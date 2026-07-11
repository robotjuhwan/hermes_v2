from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

from tradecraft.services.binance_lane import (
    BINANCE_MANAGER_LANES,
    normalize_binance_display_lane,
    normalize_binance_horizon,
)
from tradecraft.services.binance_symbol import (
    explicit_market_scope,
    normalize_market,
    normalize_position_side,
)
from tradecraft.services.jue_decision_packet import (
    build_canonical_decision_prompt_bundle,
)
from tradecraft.services.jue_wiki import normalize_jue_wiki_quality_status
from tradecraft.services.jue_wiki_prompt_quality import (
    canonical_jue_wiki_evidence_quality,
    jue_wiki_quality_status_from_evidence,
)
from tradecraft.services.jue_wiki_contract import WIKI_GATE_IDENTITY_MAX_CHARS
from tradecraft.services.manager_prompt_budget import (
    attach_prompt_budget as build_attach_prompt_budget,
    format_prompt_budget_alert_message as build_format_prompt_budget_alert_message,
    prompt_budget_error as build_prompt_budget_error,
)


BINANCE_MANAGER_ACTION_SECTIONS = (
    "adopt_existing_blocks",
    "create_blocks",
    "update_blocks",
    "close_blocks",
    "pause_blocks",
)
FUTURES_POSITION_MIN_MANAGEABLE_NOTIONAL_USDT = 5.0


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _compact_storage_compaction_meta_for_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("status", "label", "priority_reason"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=120)
    if value.get("emergency") not in (None, ""):
        out["emergency"] = bool(value.get("emergency"))
    for key in ("original_chars", "storage_limit_chars", "dropped_key_count"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _safe_int(value.get(key))
    dropped = [
        _clean_text(item, limit=80)
        for item in _as_list(value.get("dropped_keys"))[:8]
        if _clean_text(item, limit=80)
    ]
    if dropped:
        out["dropped_keys"] = dropped
    return {key: item for key, item in out.items() if item not in (None, "", [], {})}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return 0.0
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_int(value: Any) -> int:
    return int(math.floor(_safe_float(value)))


def _truthy_gate_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _compact_requested_crypto_symbol_token(value: Any) -> str:
    text = _clean_text(value, limit=80).upper()
    match = re.search(r"\b[A-Z0-9]{2,24}(?:USDT|USDC|BTC|ETH|BNB|KRW)\b", text)
    if match:
        return match.group(0)
    return ""


def _identity_compact_value(value: Any, **_: Any) -> Any:
    return value


def _clean_text(value: Any, *, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def _metadata_contract_repair_note(row: dict[str, Any]) -> str:
    repairs = [
        _clean_text(item, limit=160)
        for item in _as_list(row.get("period_memory_repair_actions"))[:2]
        if _clean_text(item, limit=160)
    ]
    resolutions = [
        _clean_text(item, limit=180)
        for item in _as_list(row.get("metadata_contract_audit_resolutions"))[:2]
        if _clean_text(item, limit=180)
    ]
    parts: list[str] = []
    if repairs:
        parts.append(f"metadata contract repair: {', '.join(repairs)}")
    if resolutions:
        parts.append(f"resolution: {', '.join(resolutions)}")
    return "; ".join(parts)


def _memory_contract_resolution_contract_from_repair_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    memory_contracts: list[str] = []
    memory_contract_errors: list[str] = []
    impacted_symbols: list[str] = []
    required_checks: list[str] = []

    def add_text(target: list[str], value: Any, *, limit: int = 160) -> None:
        text = _clean_text(value, limit=limit)
        if text and text not in target:
            target.append(text)

    for row in rows:
        if not isinstance(row, dict):
            continue
        add_text(memory_contracts, row.get("memory_contract"))
        add_text(memory_contract_errors, row.get("memory_contract_error"))
        for symbol in _as_list(row.get("impacted_symbols")):
            add_text(impacted_symbols, symbol, limit=80)
        for check in _as_list(row.get("required_checks")):
            add_text(required_checks, check, limit=120)

    required = bool(
        memory_contracts
        or memory_contract_errors
        or "require_memory_contract_resolution" in set(required_checks)
    )
    if not required:
        return {}
    return {
        "memory_contract_resolution_required": True,
        "memory_contract_resolution_contract": {
            "version": "memory_contract_resolution_contract_v1",
            "response_field": (
                "validation_repair_resolution.resolved_candidates[]."
                "memory_contract_resolution"
            ),
            "memory_contracts": memory_contracts[:6],
            "memory_contract_errors": memory_contract_errors[:6],
            "impacted_symbols": impacted_symbols[:12],
            "required_checks": sorted(set(required_checks))[:10],
            "accepted_resolutions": [
                "cite_memory_and_apply",
                "reject_memory_with_reason",
                "wait_until_memory_refresh",
                "safety_gate_defer_with_contract_note",
            ],
            "instruction": (
                "For each impacted symbol, explicitly cite or reject the memory "
                "contract in validation_repair_resolution.resolved_candidates[]."
                "memory_contract_resolution. Generic candidate rejection does not "
                "repair a memory contract error."
            ),
        },
    }


def prompt_chars(value: dict[str, Any]) -> int:
    return len(_json_dumps(value))


def prompt_chars_capped(value: Any, *, cap: int) -> int:
    limit = max(int(cap), 1)

    def walk(raw: Any, used: int) -> int:
        if used > limit:
            return used
        if isinstance(raw, dict):
            used += 2
            for key, child in raw.items():
                used += len(str(key)) + 4
                if used > limit:
                    return used
                used = walk(child, used)
                if used > limit:
                    return used
            return used
        if isinstance(raw, (list, tuple)):
            used += 2
            for child in raw:
                used = walk(child, used + 1)
                if used > limit:
                    return used
            return used
        if isinstance(raw, str):
            return used + min(len(raw), max(limit - used + 1, 1))
        return used + len(str(raw))

    return walk(value, 0)


def manager_prompt_original_chars_hint(value: dict[str, Any], *, cap: int) -> int:
    prompt_budget = value.get("prompt_budget")
    if isinstance(prompt_budget, dict):
        for key in ("total_chars", "original_chars"):
            try:
                hinted = int(float(prompt_budget.get(key) or 0))
            except (TypeError, ValueError):
                hinted = 0
            if hinted > 0:
                return max(hinted, cap + 1)
    return prompt_chars_capped(value, cap=cap)


def compact_manager_output_schema_for_prompt(value: Any) -> dict[str, Any]:
    if (
        isinstance(value, dict)
        and "lane_review" in value
        and prompt_chars({"output_schema": value}) <= 4_000
    ):
        return value
    return {
        "adopt_existing_blocks": [
            {
                "symbol": "BTCUSDT",
                "market": "spot|futures|upbit_spot",
                "side": "long|short",
                "horizon": "short|mid|long|futures",
                "lane": "spot|futures|volatile_attack|upbit_spot",
                "qty": "existing wallet/position quantity to assign",
                "entry_price": "average/current basis price if known",
                "target_price": "required number",
                "stop_price": "required number",
                "adoption_note": "ledger adoption without sending a new entry order",
            }
        ],
        "create_blocks": [
            {
                "symbol": "BTCUSDT",
                "market": "spot|futures|upbit_spot",
                "side": "long|short",
                "horizon": (
                    "short|mid|long for spot/upbit_spot; futures only when "
                    "market=futures"
                ),
                "lane": "spot|futures|volatile_attack|upbit_spot",
                "qty": "number",
                "entry_price": "required number",
                "target_price": "required number",
                "stop_price": "required number",
                "thesis": "concise execution thesis",
                "risk_note": "concise risk note",
            }
        ],
        "update_blocks": [{"block_id": "string"}],
        "close_blocks": [{"block_id": "string"}],
        "pause_blocks": [{"block_id": "string"}],
        "lane_review": {
            "required": "mandatory top-level object on every response",
            "dominant_lane": "spot:long|futures:long|futures:short|upbit_spot:long|none",
            "lanes_reviewed": list(BINANCE_MANAGER_LANES),
            "selected_lanes": ["market:side lanes used by actions"],
            "non_selected_lane_reasons": {
                "spot:long": "why not chosen or only watched",
                "futures:long": "why not chosen or only watched",
                "futures:short": "why not chosen or chosen",
                "upbit_spot:long": "why not chosen or only watched",
            },
            "concentration_note": "short note about lane balance",
            "exploration_watch": ["symbols or lanes to keep watching"],
        },
        "validation_repair_resolution": {
            "required": "mandatory whenever validation_repair is present",
            "resolved_candidates": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot|futures|upbit_spot",
                    "resolution": (
                        "probe_waiting_block|updated_price_geometry|"
                        "candidate_rejected|safety_gate_defer"
                    ),
                    "next_trigger": "price/depth/funding/regime condition",
                    "evidence_gap": "precise missing evidence, if rejected",
                    "memory_contract": (
                        "required when validation_repair."
                        "memory_contract_resolution_required=true"
                    ),
                    "memory_contract_error": (
                        "required when validation_repair."
                        "memory_contract_resolution_required=true"
                    ),
                    "memory_contract_resolution": (
                        "required when validation_repair."
                        "memory_contract_resolution_required=true; cite_memory_and_apply|"
                        "reject_memory_with_reason|wait_until_memory_refresh|"
                        "safety_gate_defer_with_contract_note plus concise evidence"
                    ),
                }
            ],
            "blanket_hold_allowed": False,
        },
    }


def manager_storage_compaction_meta(
    *,
    label: str,
    original_chars: int,
    storage_limit_chars: int,
    retained_keys: list[str],
    emergency: bool = False,
) -> dict[str, Any]:
    return {
        "status": "compacted",
        "label": label,
        "original_chars": int(original_chars),
        "storage_limit_chars": int(storage_limit_chars),
        "retained_keys": retained_keys[:50],
        "emergency": bool(emergency),
    }


def prompt_section_size_rows(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"section": key, "chars": prompt_chars({key: value})}
        for key, value in prompt.items()
        if key not in {"prompt_budget", "prompt_compaction"}
    ]
    rows.sort(key=lambda row: int(row["chars"]), reverse=True)
    return rows


def compact_account_asset_row(row: dict[str, Any]) -> dict[str, Any]:
    symbol = _clean_text(row.get("symbol"), limit=32).upper()
    asset = _clean_text(row.get("asset"), limit=32).upper()
    kind = _clean_text(row.get("kind") or "position", limit=32)
    qty = _safe_float(row.get("qty") or row.get("quantity") or row.get("balance"))
    available = _safe_float(row.get("available") or row.get("free"))
    locked = _safe_float(row.get("locked"))
    value_usdt = _safe_float(row.get("value_usdt"))
    value_krw = _safe_float(row.get("value_krw"))
    if not (symbol or asset):
        return {}
    out: dict[str, Any] = {
        "asset": asset,
        "symbol": symbol or (f"{asset}USDT" if asset and kind != "cash" else asset),
        "kind": kind,
        "qty": qty,
        "available": available,
        "locked": locked,
    }
    optional_fields = {
        "value_usdt": value_usdt,
        "value_krw": value_krw,
        "mark_price": _safe_float(row.get("mark_price")),
        "avg_price": _safe_float(row.get("avg_price")),
        "pnl_usdt": _safe_float(row.get("pnl_usdt")),
        "pnl_pct": _safe_float(row.get("pnl_pct")),
    }
    for key, value in optional_fields.items():
        if value:
            out[key] = value
    return out


def compact_account_asset_rows(rows: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(rows, (list, tuple)):
        return []
    compacted: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        compact = compact_account_asset_row(row)
        if not compact:
            continue
        compacted.append(compact)
        if len(compacted) >= limit:
            break
    return compacted


def is_meaningful_futures_risk_row(row: dict[str, Any]) -> bool:
    return (
        abs(_safe_float(row.get("position_amt") or row.get("positionAmt"))) > 0
        or abs(
            _safe_float(
                row.get("unrealized_profit")
                or row.get("unRealizedProfit")
                or row.get("unrealizedProfit")
            )
        )
        > 0
    )


def compact_futures_risk_row(row: dict[str, Any]) -> dict[str, Any]:
    position_amt = _safe_float(row.get("position_amt") or row.get("positionAmt"))
    entry_price = _safe_float(row.get("entry_price") or row.get("entryPrice"))
    mark_price = _safe_float(row.get("mark_price") or row.get("markPrice"))
    reference_price = mark_price if mark_price > 0 else entry_price
    position_notional = abs(position_amt) * reference_price if reference_price > 0 else 0.0
    compact = {
        "symbol": _clean_text(row.get("symbol"), limit=32).upper(),
        "position_amt": position_amt,
        "entry_price": entry_price,
        "mark_price": mark_price,
        "liquidation_price": _safe_float(
            row.get("liquidation_price") or row.get("liquidationPrice")
        ),
        "leverage": _safe_int(row.get("leverage")),
        "margin_type": _clean_text(row.get("margin_type") or row.get("marginType"), limit=24),
        "unrealized_profit": _safe_float(
            row.get("unrealized_profit")
            or row.get("unRealizedProfit")
            or row.get("unrealizedProfit")
        ),
        "position_notional_usdt": round(position_notional, 8),
        "management_status": (
            "dust_below_min_notional"
            if 0 < position_notional < FUTURES_POSITION_MIN_MANAGEABLE_NOTIONAL_USDT
            else "open_position"
        ),
    }
    if compact["management_status"] == "dust_below_min_notional":
        compact["min_manageable_notional_usdt"] = (
            FUTURES_POSITION_MIN_MANAGEABLE_NOTIONAL_USDT
        )
    return compact


def compact_manager_account_for_prompt(account: dict[str, Any]) -> dict[str, Any]:
    futures_risk = [
        row for row in account.get("futures_position_risk") or [] if isinstance(row, dict)
    ]
    visible_risk = [
        compact_futures_risk_row(row)
        for row in futures_risk
        if is_meaningful_futures_risk_row(row)
    ][:20]
    dust_count = sum(
        1
        for row in visible_risk
        if row.get("management_status") == "dust_below_min_notional"
    )
    errors = [
        {
            "source": _clean_text(row.get("source"), limit=80),
            "error_message": _clean_text(row.get("error_message"), limit=240),
        }
        for row in account.get("errors") or []
        if isinstance(row, dict)
    ][:8]

    out: dict[str, Any] = {
        "status": _clean_text(account.get("status") or "ok", limit=40),
        "spot_cash_usdt": _safe_float(account.get("spot_cash_usdt")),
        "futures_cash_usdt": _safe_float(account.get("futures_cash_usdt")),
        "upbit_cash_krw": _safe_float(account.get("upbit_cash_krw")),
        "upbit_cash_usdt": _safe_float(account.get("upbit_cash_usdt")),
        "spot_assets": compact_account_asset_rows(
            account.get("spot_assets") or account.get("positions") or [],
            limit=24,
        ),
        "upbit_spot_assets": compact_account_asset_rows(
            account.get("upbit_spot_assets") or [],
            limit=24,
        ),
        "futures_assets": compact_account_asset_rows(
            account.get("futures_assets") or [],
            limit=12,
        ),
        "futures_position_risk": visible_risk,
        "futures_position_risk_summary": {
            "total_count": len(futures_risk),
            "nonzero_count": sum(
                1 for row in futures_risk if is_meaningful_futures_risk_row(row)
            ),
            "visible_count": len(visible_risk),
            "omitted_zero_count": sum(
                1 for row in futures_risk if not is_meaningful_futures_risk_row(row)
            ),
            "dust_count": dust_count,
        },
    }
    if errors:
        out["errors"] = errors
    if "cash_usdt" in account:
        out["cash_usdt"] = _safe_float(account.get("cash_usdt"))
    if "total_value_usdt" in account:
        out["total_value_usdt"] = _safe_float(account.get("total_value_usdt"))
    return out


def compact_prompt_value(
    value: Any,
    *,
    string_limit: int = 160,
    list_limit: int = 8,
) -> Any:
    if isinstance(value, dict):
        return {
            str(key): compact_prompt_value(
                child,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            compact_prompt_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            for item in list(value)[: max(int(list_limit), 0)]
        ]
    if isinstance(value, str):
        text = _clean_text(value, limit=max(len(value), 1))
        if len(text) > max(int(string_limit), 1):
            return f"[truncated:{len(text)} chars]"
        return text
    return value


def _manager_memory_rows_for_prompt_scope(value: Any) -> list[Any]:
    rows: list[Any] = []
    for row in _as_list(value):
        if not isinstance(row, dict) or _manager_memory_node_matches_prompt_scope(row):
            rows.append(row)
    return rows


def _manager_memory_mapping_key_matches_prompt_scope(key: Any) -> bool:
    text = str(key or "").strip().lower()
    compact = (
        text.replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("|", "_")
        .replace(":", "_")
    )
    if not compact:
        return True
    if any(
        marker in compact
        for marker in ("kis", "krx", "kr_equity", "korean_equity", "domestic_equity")
    ):
        return False
    if re.fullmatch(r"\d{6}", compact):
        return False
    return True


def _manager_memory_child_is_translated(child: Any) -> bool:
    if isinstance(child, dict):
        return _manager_wiki_memory_transferability_is_translated(child)
    if isinstance(child, (list, tuple)):
        return any(_manager_memory_child_is_translated(item) for item in child)
    return False


def _manager_memory_mapping_for_prompt_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scoped: dict[str, Any] = {}
    for key, child in value.items():
        if (
            not _manager_memory_mapping_key_matches_prompt_scope(key)
            and not _manager_memory_child_is_translated(child)
        ):
            continue
        if isinstance(child, dict):
            row = {**child, "id": child.get("id") or key}
            if not _manager_memory_node_matches_prompt_scope(row):
                continue
        scoped[key] = child
    return scoped


def _manager_memory_node_matches_prompt_scope(row: dict[str, Any]) -> bool:
    if not _manager_wiki_memory_item_matches_scope(row):
        return False
    if not _manager_jue_wiki_prompt_item_matches_scope(row):
        return False
    source_scope = str(row.get("source_scope") or "").strip()
    if (
        source_scope
        and not _manager_wiki_memory_transferability_is_translated(row)
        and not _manager_wiki_memory_scope_value_matches(source_scope)
    ):
        return False
    return True


_MANAGER_MEMORY_SCOPE_COUNT_MAPPING_KEYS = {
    "source_scope_counts",
    "target_scope_counts",
    "scope_counts",
}


def _manager_memory_payload_for_prompt_scope(
    value: Any,
    *,
    filter_mapping_keys: bool = True,
) -> Any:
    if isinstance(value, dict):
        node_matches_scope = _manager_memory_node_matches_prompt_scope(value)
        scoped: dict[str, Any] = {}
        for key, child in value.items():
            if not node_matches_scope and not isinstance(
                child,
                (dict, list, tuple),
            ):
                continue
            if (
                filter_mapping_keys
                and not _manager_memory_mapping_key_matches_prompt_scope(key)
                and not _manager_memory_child_is_translated(child)
            ):
                continue
            scoped_child = _manager_memory_payload_for_prompt_scope(
                child,
                filter_mapping_keys=filter_mapping_keys
                and str(key) not in _MANAGER_MEMORY_SCOPE_COUNT_MAPPING_KEYS,
            )
            if scoped_child not in (None, "", [], {}):
                scoped[key] = scoped_child
        return scoped
    if isinstance(value, (list, tuple)):
        rows: list[Any] = []
        for item in value:
            scoped_item = _manager_memory_payload_for_prompt_scope(
                item,
                filter_mapping_keys=filter_mapping_keys,
            )
            if scoped_item not in (None, "", [], {}):
                rows.append(scoped_item)
        return rows
    return value


def _manager_decision_skill_matches_prompt_scope(
    key: Any,
    child: dict[str, Any],
) -> bool:
    if _manager_wiki_memory_transferability_is_translated(child):
        return True
    identity = " ".join(
        str(value or "")
        for value in (
            key,
            child.get("skill_id"),
            child.get("id"),
            child.get("scope"),
            child.get("target_scope"),
            child.get("market"),
        )
    )
    compact = (
        identity.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("|", "_")
        .replace(":", "_")
    )
    if not compact:
        return True
    return not any(
        marker in compact
        for marker in ("kis", "krx", "kr_equity", "korean_equity", "domestic_equity")
    )


def _manager_scoped_memory_for_prompt(value: dict[str, Any]) -> dict[str, Any]:
    scoped = dict(value)
    memory_scope = (
        scoped.get("scoped_memory")
        if isinstance(scoped.get("scoped_memory"), dict)
        else {}
    )
    if memory_scope:
        preserved_scope_keys = {
            "status",
            "target_scope",
            "source_scope",
            "market_scope",
            "scope",
            "venue",
            "blocked_count",
            "local_count",
            "core_count",
            "translated_count",
        }
        scoped["scoped_memory"] = {
            key: _manager_memory_rows_for_prompt_scope(child)
            for key, child in memory_scope.items()
            if _manager_memory_rows_for_prompt_scope(child)
            or key in preserved_scope_keys
        }
        for key in preserved_scope_keys:
            if key in memory_scope:
                scoped["scoped_memory"][key] = memory_scope[key]
    for key in (
        "items",
        "notes",
        "lessons",
        "memories",
        "active_policies",
        "policy_scorecards",
        "policy_rules",
        "recent_reflections",
        "latest_journals",
        "seed_memory",
        "active_insights",
        "policy_revisions",
        "policy_outcomes",
    ):
        if key in scoped:
            scoped[key] = _manager_memory_rows_for_prompt_scope(scoped.get(key))
    for key in (
        "symbol_notes",
        "block_notes",
        "validation_repair_backlog",
        "policy_rule_evaluation",
    ):
        if key in scoped:
            scoped[key] = _manager_memory_mapping_for_prompt_scope(scoped.get(key))
    decision_skills = (
        scoped.get("decision_skills")
        if isinstance(scoped.get("decision_skills"), dict)
        else {}
    )
    if decision_skills:
        scoped["decision_skills"] = {
            key: child
            for key, child in decision_skills.items()
            if isinstance(child, dict)
            and _manager_decision_skill_matches_prompt_scope(key, child)
        }
    for key in (
        "period_memory_coverage",
        "period_reviews",
        "historical_replays",
        "block_design_constraints",
        "validation_recovery_summary",
        "next_block_design_playbook",
        "translated_policy_context",
        "jue_wiki_selection_memory",
        "jue_wiki_context_gap_memory",
        "jue_wiki_action_reference_memory",
        "jue_wiki_usage_contract_memory",
        "market_pulse",
        "crypto_market_pulse",
        "jue_wiki",
        "decision_skill_status",
    ):
        if key in scoped:
            scoped[key] = _manager_memory_payload_for_prompt_scope(scoped.get(key))
    recovery = (
        scoped.get("validation_recovery_summary")
        if isinstance(scoped.get("validation_recovery_summary"), dict)
        else {}
    )
    if recovery and "manager_contract_recovered" in recovery:
        scoped_recovery = dict(recovery)
        scoped_recovery["manager_contract_recovered"] = (
            _manager_memory_rows_for_prompt_scope(
                recovery.get("manager_contract_recovered")
            )
        )
        scoped["validation_recovery_summary"] = scoped_recovery
    return scoped


def compact_binance_memory_for_prompt(
    value: Any,
    *,
    string_limit: int = 160,
    list_limit: int = 8,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scoped_value = _manager_scoped_memory_for_prompt(value)
    compact = compact_prompt_value(
        scoped_value,
        string_limit=string_limit,
        list_limit=list_limit,
    )
    if not isinstance(compact, dict):
        compact = {}

    bounded_string_limit = max(min(int(string_limit), 120), 48)
    bounded_list_limit = max(min(int(list_limit), 4), 1)
    bounded_dict_limit = max(min(int(list_limit) * 2, 12), 4)
    for key in (
        "policy_rule_evaluation",
        "validation_repair_backlog",
        "block_design_constraints",
        "next_block_design_playbook",
    ):
        if key not in scoped_value:
            continue
        compact[key] = compact_prompt_value_bounded(
            scoped_value.get(key),
            string_limit=bounded_string_limit,
            list_limit=bounded_list_limit,
            dict_limit=bounded_dict_limit,
        )
    for key in (
        "policy_rules",
        "policy_scorecards",
        "active_policies",
        "policy_revisions",
        "policy_outcomes",
        "active_insights",
    ):
        if key not in scoped_value:
            continue
        compact[key] = compact_prompt_value_bounded(
            scoped_value.get(key),
            string_limit=bounded_string_limit,
            list_limit=bounded_list_limit,
            dict_limit=bounded_dict_limit,
        )

    recovery = (
        scoped_value.get("validation_recovery_summary")
        if isinstance(scoped_value.get("validation_recovery_summary"), dict)
        else {}
    )
    if recovery:
        compact_recovery: dict[str, Any] = {}
        for key in (
            "status",
            "recovery_status",
            "total_item_count",
            "manager_contract_recovered_count",
        ):
            if recovery.get(key) not in (None, "", [], {}):
                if isinstance(recovery.get(key), str):
                    compact_recovery[key] = _clean_text(
                        recovery.get(key),
                        limit=max(int(string_limit), 80),
                    )
                else:
                    compact_recovery[key] = recovery.get(key)
        rows: list[dict[str, Any]] = []
        row_limit = max(min(int(list_limit), 4), 1)
        text_limit = max(int(string_limit) * 2, 160)
        for row in _as_list(recovery.get("manager_contract_recovered"))[:row_limit]:
            if not isinstance(row, dict):
                continue
            compact_row: dict[str, Any] = {}
            for key in (
                "policy_id",
                "resolution_policy_id",
                "contract",
                "error",
                "resolution_status",
            ):
                if row.get(key) not in (None, "", [], {}):
                    compact_row[key] = _clean_text(row.get(key), limit=160)
            symbols = [
                _clean_text(symbol, limit=32)
                for symbol in _as_list(row.get("impacted_symbols"))[:4]
                if _clean_text(symbol, limit=32)
            ]
            if symbols:
                compact_row["impacted_symbols"] = symbols
            if row.get("latest_resolution") not in (None, "", [], {}):
                compact_row["latest_resolution"] = _clean_text(
                    row.get("latest_resolution"),
                    limit=text_limit,
                )
            if compact_row:
                rows.append(compact_row)
        if rows:
            compact_recovery["manager_contract_recovered"] = rows
        if compact_recovery:
            compact["validation_recovery_summary"] = compact_recovery
    return compact


_BOOK_FIELD_KEYS = {
    "bid_price",
    "bid",
    "ask_price",
    "ask",
    "spread_bps",
    "book_source",
    "book_fetched_at",
    "book_market",
    "book_fresh",
}


def merge_manager_candidate_price_plan(
    *,
    candidate: dict[str, Any],
    symbol: str,
    market: str,
    side: str,
    horizon: str,
    price_plan: dict[str, Any],
    score_candidate: Callable[[dict[str, Any]], float],
) -> dict[str, Any]:
    compact_candidate = compact_prompt_value(candidate, string_limit=220, list_limit=8)
    if isinstance(compact_candidate, dict):
        for field in _BOOK_FIELD_KEYS:
            compact_candidate.pop(field, None)
    else:
        compact_candidate = {}
    merged = {
        **compact_candidate,
        "symbol": symbol,
        "market": market,
        "side": side,
        "horizon": horizon,
    }
    if not price_plan:
        gaps = merged.get("data_gaps") if isinstance(merged.get("data_gaps"), list) else []
        gaps.append("missing executable price inputs")
        merged["data_gaps"] = gaps
        return merged

    price_plan = dict(price_plan)
    entry_style = str(price_plan.get("entry_style") or "").strip()
    metadata = dict(merged.get("metadata") if isinstance(merged.get("metadata"), dict) else {})
    metadata["calculated_price_plan"] = price_plan
    metadata["jue_price_override_allowed"] = True
    metadata["pattern_live_crosscheck"] = price_plan.get("pattern_live_crosscheck", {})
    metadata["lane"] = normalize_binance_display_lane(
        lane=price_plan.get("lane"),
        market=market,
        horizon=horizon,
        side=side,
    )
    metadata["volatile_attack"] = bool(price_plan.get("volatile_attack"))
    metadata["pattern_prior"] = (
        price_plan.get("pattern_inputs", {}).get("prior", {})
        if isinstance(price_plan.get("pattern_inputs"), dict)
        else {}
    )
    execution_fields = {
        "entry_price": price_plan["entry_price"],
        "target_price": price_plan["target_price"],
        "stop_price": price_plan["stop_price"],
        "quote_budget": price_plan.get("quote_budget"),
        "quote_currency": price_plan.get("quote_currency"),
        "quote_budget_usdt": price_plan["quote_budget_usdt"],
        "quote_budget_krw": price_plan.get("quote_budget_krw", 0.0),
        "entry_style": entry_style,
        "lane": metadata["lane"],
        "calculated": price_plan,
        "calculated_price_plan": price_plan,
        "metadata": metadata,
    }
    if market == "upbit_spot":
        execution_fields.update(
            {
                "entry_price_krw": price_plan["entry_price"],
                "target_price_krw": price_plan["target_price"],
                "stop_price_krw": price_plan["stop_price"],
            }
        )
    else:
        execution_fields.update(
            {
                "entry_price_usdt": price_plan["entry_price"],
                "target_price_usdt": price_plan["target_price"],
                "stop_price_usdt": price_plan["stop_price"],
            }
        )
    merged.update(execution_fields)
    edge_score = float(score_candidate(merged))
    merged["empirical_edge_score"] = edge_score
    price_plan["empirical_edge_score"] = edge_score
    merged["calculated"] = price_plan
    merged["calculated_price_plan"] = price_plan
    metadata["calculated_price_plan"] = price_plan
    metadata["empirical_edge_score"] = edge_score
    merged["metadata"] = metadata
    market_inputs = (
        price_plan.get("market_inputs")
        if isinstance(price_plan.get("market_inputs"), dict)
        else {}
    )
    if bool(market_inputs.get("book_fresh")):
        merged["bid_price"] = market_inputs.get("bid_price", 0)
        merged["ask_price"] = market_inputs.get("ask_price", 0)
        merged["spread_bps"] = market_inputs.get("spread_bps", 0)
        merged["book_source"] = market_inputs.get("book_source", "")
        merged["book_fetched_at"] = market_inputs.get("book_fetched_at", "")
        merged["book_market"] = market_inputs.get("book_market", market)
        merged["book_fresh"] = True
    if entry_style == "wait_for_price":
        merged["entry_trigger_price"] = price_plan["entry_trigger_price"]
        merged["entry_trigger_operator"] = price_plan["entry_trigger_operator"]
    if market == "futures":
        merged["leverage"] = price_plan["leverage"]
        merged["margin_type"] = price_plan["margin_type"]
        merged["liquidation_price"] = price_plan["liquidation_price"]
    return merged


_PROMPT_BOUNDED_PRIORITY_KEYS = (
    "version",
    "status",
    "action_batches",
    "memory_card_quality_gap_summary",
    "id",
    "label",
    "symbol",
    "market",
    "side",
    "horizon",
    "lane",
    "venue",
    "action",
    "action_hint",
    "reason",
    "instruction",
    "policy_id",
    "discipline_id",
    "pattern_key",
    "quality_hint",
    "readiness",
    "is_complete",
    "requires_revalidation",
    "total_score",
    "pass_count",
    "warn_count",
    "fail_count",
    "missing_count",
    "discipline_count",
    "expected_discipline_count",
    "block_count",
    "alpha_count",
    "sample_count",
    "win_rate_pct",
    "profit_factor",
    "expectancy_pct",
    "expectancy_r",
    "avg_r_multiple",
    "max_drawdown_pct",
    "recovery_factor",
    "cost_drag_pct_of_abs_gross_pnl",
    "pnl_usdt",
    "net_pnl",
    "risk_budget_usdt",
    "quote_budget_usdt",
    "entry_style",
    "entry_price",
    "entry_trigger_price",
    "entry_trigger_operator",
    "target_price",
    "stop_price",
    "reward_risk",
    "spread_bps",
    "funding_rate",
    "live_grade",
    "max_budget_multiplier",
    "budget_multiplier",
    "risk_budget_multiplier",
    "min_reward_risk",
    "max_stop_risk_pct",
    "requires_waiting_entry",
    "scale_up_allowed",
    "pattern_live_crosscheck",
    "risk_sizing",
)


def _compact_memory_card_quality_gap_summary_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def compact_terms(key: str) -> list[str]:
        terms: list[str] = []
        for item in _as_list(value.get(key))[:6]:
            text = _clean_text(item, limit=140)
            if text and text not in terms:
                terms.append(text)
        return terms

    def fallback_priority_terms(
        *,
        priority_key: str,
        missed_counts_key: str,
        top_key: str,
        label: str,
    ) -> list[str]:
        terms = compact_terms(priority_key)
        if terms:
            return terms
        missed_counts = (
            value.get(missed_counts_key)
            if isinstance(value.get(missed_counts_key), dict)
            else {}
        )
        for item, count in sorted(
            missed_counts.items(),
            key=lambda row: (-_safe_int(row[1]), str(row[0])),
        ):
            if _safe_int(count) <= 0:
                continue
            text = _clean_text(item, limit=140)
            if text and text not in terms:
                terms.append(text)
            if len(terms) >= 6:
                return terms
        for row in _as_list(value.get(top_key)):
            if not isinstance(row, dict) or _safe_int(row.get("missed_count")) <= 0:
                continue
            text = _clean_text(row.get(label), limit=140)
            if text and text not in terms:
                terms.append(text)
            if len(terms) >= 6:
                return terms
        return terms

    def priority_metric(
        *,
        top_key: str,
        label: str,
        sample_counts_key: str,
        missed_counts_key: str,
    ) -> tuple[str, int, int] | None:
        for row in _as_list(value.get(top_key))[:6]:
            if not isinstance(row, dict):
                continue
            item = _clean_text(row.get(label), limit=140)
            missed_count = _safe_int(row.get("missed_count"))
            if item and missed_count > 0:
                return item, _safe_int(row.get("sample_count")), missed_count
        missed_counts = (
            value.get(missed_counts_key)
            if isinstance(value.get(missed_counts_key), dict)
            else {}
        )
        sample_counts = (
            value.get(sample_counts_key)
            if isinstance(value.get(sample_counts_key), dict)
            else {}
        )
        for item, missed_count in sorted(
            missed_counts.items(),
            key=lambda row: (-_safe_int(row[1]), str(row[0])),
        ):
            clean = _clean_text(item, limit=140)
            if clean and _safe_int(missed_count) > 0:
                return clean, _safe_int(sample_counts.get(item)), _safe_int(missed_count)
        return None

    def compact_priority_focus() -> dict[str, Any]:
        source_focus = (
            value.get("priority_focus")
            if isinstance(value.get("priority_focus"), dict)
            else {}
        )
        if source_focus:
            compact_focus: dict[str, Any] = {}
            for key in ("missing_field", "required_check", "instruction"):
                text = _clean_text(source_focus.get(key), limit=180)
                if text:
                    compact_focus[key] = text
            for key in (
                "missing_field_sample_count",
                "missing_field_missed_count",
                "required_check_sample_count",
                "required_check_missed_count",
            ):
                if source_focus.get(key) not in (None, "", [], {}):
                    compact_focus[key] = _safe_int(source_focus.get(key))
            return compact_focus
        focus: dict[str, Any] = {}
        missing = priority_metric(
            top_key="top_missing_fields",
            label="field",
            sample_counts_key="missing_field_counts",
            missed_counts_key="missing_field_missed_counts",
        )
        check = priority_metric(
            top_key="top_required_checks",
            label="check",
            sample_counts_key="required_check_counts",
            missed_counts_key="required_check_missed_counts",
        )
        if missing:
            field, sample_count, missed_count = missing
            focus.update(
                {
                    "missing_field": field,
                    "missing_field_sample_count": sample_count,
                    "missing_field_missed_count": missed_count,
                }
            )
        if check:
            check_name, sample_count, missed_count = check
            focus.update(
                {
                    "required_check": check_name,
                    "required_check_sample_count": sample_count,
                    "required_check_missed_count": missed_count,
                }
            )
        if focus:
            focus["instruction"] = "resolve_priority_memory_card_quality_gap_first"
        return focus

    def compact_top(key: str, *, label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in _as_list(value.get(key))[:6]:
            if not isinstance(item, dict):
                continue
            label_value = _clean_text(item.get(label), limit=140)
            if not label_value:
                continue
            row: dict[str, Any] = {label: label_value}
            for metric_key in ("sample_count", "missed_count"):
                if item.get(metric_key) not in (None, "", [], {}):
                    row[metric_key] = _safe_int(item.get(metric_key))
            rows.append(row)
        return rows

    compact = {
        "status": _clean_text(value.get("status"), limit=80),
        "priority_missing_fields": fallback_priority_terms(
            priority_key="priority_missing_fields",
            missed_counts_key="missing_field_missed_counts",
            top_key="top_missing_fields",
            label="field",
        ),
        "priority_required_checks": fallback_priority_terms(
            priority_key="priority_required_checks",
            missed_counts_key="required_check_missed_counts",
            top_key="top_required_checks",
            label="check",
        ),
        "priority_focus": compact_priority_focus(),
        "top_missing_fields": compact_top("top_missing_fields", label="field"),
        "top_required_checks": compact_top("top_required_checks", label="check"),
    }
    return {
        key: child
        for key, child in compact.items()
        if child not in (None, "", [], {})
    }


def compact_prompt_value_bounded(
    value: Any,
    *,
    string_limit: int = 120,
    list_limit: int = 4,
    dict_limit: int = 24,
    drop_keys: set[str] | None = None,
    placeholder_strings: bool = True,
) -> Any:
    if isinstance(value, dict):
        if (
            "memory_card_quality_gap_summary" not in value
            and (
                "priority_missing_fields" in value
                or "priority_required_checks" in value
                or "top_missing_fields" in value
                or "top_required_checks" in value
            )
        ):
            gap_summary = _compact_memory_card_quality_gap_summary_for_prompt(value)
            if gap_summary:
                return gap_summary
        drops = {str(key) for key in (drop_keys or set())}
        selected: list[str] = []

        def add(raw_key: Any) -> None:
            key = str(raw_key)
            if key in drops or key in selected or key not in value:
                return
            if value.get(key) in ({}, [], "", None):
                return
            selected.append(key)

        for key in _PROMPT_BOUNDED_PRIORITY_KEYS:
            add(key)
            if len(selected) >= max(int(dict_limit), 1):
                break
        if len(selected) < max(int(dict_limit), 1):
            for key in value:
                add(key)
                if len(selected) >= max(int(dict_limit), 1):
                    break

        out = {
            key: compact_prompt_value_bounded(
                value.get(key),
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=max(min(int(dict_limit), 16), 4),
                drop_keys=drops,
                placeholder_strings=placeholder_strings,
            )
            for key in selected
        }
        omitted_count = len(
            [
                key
                for key, child in value.items()
                if str(key) not in drops and child not in ({}, [], "", None)
            ]
        ) - len(selected)
        if omitted_count > 0:
            out["_omitted_count"] = omitted_count
        return out
    if isinstance(value, (list, tuple)):
        return [
            compact_prompt_value_bounded(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
                drop_keys=drop_keys,
                placeholder_strings=placeholder_strings,
            )
            for item in list(value)[: max(int(list_limit), 0)]
        ]
    if isinstance(value, str):
        text = _clean_text(value, limit=max(len(value), 1))
        if placeholder_strings and len(text) > max(int(string_limit), 1):
            return f"[truncated:{len(text)} chars]"
        return _clean_text(text, limit=max(int(string_limit), 1))
    return value


def _jue_wiki_quality_status_from_evidence(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return jue_wiki_quality_status_from_evidence(value)


def _jue_wiki_quality_warnings_from_evidence(
    value: Any,
    *,
    limit: int = 3,
) -> list[str]:
    if not isinstance(value, dict):
        return []
    warnings: list[str] = []
    for item in _as_list(value.get("top_warnings")):
        if isinstance(item, dict):
            warning = item.get("warning") or item.get("key") or item.get("name")
        else:
            warning = item
        text = _clean_text(warning, limit=120)
        if text and text not in warnings:
            warnings.append(text)
        if len(warnings) >= max(int(limit), 0):
            break
    return warnings


def _compact_jue_wiki_evidence_quality_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    canonical = canonical_jue_wiki_evidence_quality(value)
    row = {
        key: canonical.get(key)
        for key in (
            "summary_line",
            "source_count",
            "status_counts",
            "warning_counts",
            "source_type_counts",
            "top_warnings",
        )
        if canonical.get(key) not in (None, "", [], {})
    }
    repair_queue = _compact_jue_wiki_evidence_repair_queue_for_prompt(
        canonical.get("repair_queue")
    )
    if repair_queue:
        row["repair_queue"] = repair_queue
    return row


def _compact_jue_wiki_evidence_repair_queue_for_prompt(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    open_count = _safe_int(value.get("open_count"))
    if open_count > 0:
        row["open_count"] = open_count
    raw_actions = value.get("actions")
    if isinstance(raw_actions, (list, tuple, set)):
        action_items = list(raw_actions)
    elif raw_actions in (None, "", [], {}):
        action_items = []
    else:
        action_items = [raw_actions]
    actions: list[dict[str, Any]] = []
    for action in action_items[:4]:
        if not isinstance(action, dict):
            continue
        compact_action: dict[str, Any] = {}
        for key in ("action_type", "status"):
            raw = action.get(key)
            if raw not in (None, "", [], {}):
                compact_action[key] = _clean_text(raw, limit=120)
        raw_warnings = action.get("quality_warnings")
        if isinstance(raw_warnings, (list, tuple, set)):
            warning_items = list(raw_warnings)
        elif raw_warnings in (None, "", [], {}):
            warning_items = []
        else:
            warning_items = [raw_warnings]
        quality_warnings = [
            _clean_text(warning, limit=120)
            for warning in warning_items[:6]
            if str(warning or "").strip()
        ]
        if quality_warnings:
            compact_action["quality_warnings"] = quality_warnings
        if compact_action:
            actions.append(compact_action)
    if actions:
        row["actions"] = actions
    return row


def _compact_jue_wiki_source_ref_for_prompt(
    value: Any,
    *,
    string_limit: int,
) -> dict[str, Any] | str:
    if not isinstance(value, dict):
        return _clean_text(value, limit=min(max(int(string_limit), 1), 180))
    row: dict[str, Any] = {}
    for key in (
        "source_type",
        "source_id",
        "source_scope",
        "kind",
        "id",
        "status",
        "action_type",
        "repair_status",
        "decision_use",
        "observed_at",
        "as_of",
    ):
        raw = value.get(key)
        if raw not in ({}, [], "", None):
            row[key] = _clean_text(raw, limit=180)
    symbols = [
        _clean_text(symbol, limit=40)
        for symbol in _as_list(value.get("symbols"))[:6]
        if str(symbol or "").strip()
    ]
    if symbols:
        row["symbols"] = symbols
    evidence_quality = _compact_jue_wiki_evidence_quality_for_prompt(
        value.get("evidence_quality")
    )
    if evidence_quality:
        row["evidence_quality"] = evidence_quality
    quality_status = normalize_jue_wiki_quality_status(value.get("quality_status"))
    if not quality_status:
        quality_status = _jue_wiki_quality_status_from_evidence(evidence_quality)
    if quality_status:
        row["quality_status"] = quality_status
    quality_warnings = [
        _clean_text(warning, limit=120)
        for warning in _as_list(value.get("quality_warnings"))[:6]
        if str(warning or "").strip()
    ]
    if not quality_warnings:
        quality_warnings = _jue_wiki_quality_warnings_from_evidence(
            evidence_quality,
            limit=6,
        )
    if quality_warnings:
        row["quality_warnings"] = quality_warnings
    return {key: child for key, child in row.items() if child not in ({}, [], "", None)}


def _compact_jue_wiki_memory_card_quality_details_for_prompt(
    value: Any,
    *,
    item_limit: int = 4,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def string_list(raw: Any, *, limit: int, max_len: int = 96) -> list[str]:
        values: list[str] = []
        for item in _as_list(raw)[: max(int(limit), 0)]:
            text = _clean_text(item, limit=max_len)
            if text and text not in values:
                values.append(text)
        return values

    row: dict[str, Any] = {}
    for key in ("status", "resolution", "required_action", "decision_use"):
        raw = value.get(key)
        if raw not in ({}, [], "", None):
            row[key] = _clean_text(raw, limit=120)
    symbols = [
        symbol
        for symbol in (
            _compact_requested_crypto_symbol_token(raw_symbol)
            for raw_symbol in _as_list(value.get("symbols"))[:8]
        )
        if symbol
    ]
    if symbols:
        row["symbols"] = symbols
    missing_fields = string_list(value.get("missing_fields"), limit=8)
    if missing_fields:
        row["missing_fields"] = missing_fields
    required_checks = string_list(value.get("required_checks"), limit=8, max_len=140)
    if required_checks:
        row["required_checks"] = required_checks
    if value.get("candidate_resolution_required") not in ({}, [], "", None):
        row["candidate_resolution_required"] = bool(
            value.get("candidate_resolution_required")
        )

    items: list[dict[str, Any]] = []
    for item in _as_list(value.get("items"))[: max(int(item_limit), 0)]:
        child = _compact_jue_wiki_memory_card_quality_details_for_prompt(
            item,
            item_limit=0,
        )
        if child:
            items.append(child)
    if items:
        row["items"] = items
    return {key: child for key, child in row.items() if child not in ({}, [], "", None)}


def _compact_jue_wiki_memory_card_for_prompt(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    card: dict[str, str] = {}
    for key, limit, raw_limit in (
        ("stance", 160, 320),
        ("durable_facts", 220, 440),
        ("trading_history", 220, 440),
        ("lessons", 220, 440),
        ("contradictions", 180, 360),
        ("open_questions", 220, 440),
    ):
        raw_text = _clean_text(
            value.get(key),
            limit=max(len(str(value.get(key) or "")), 1),
        )
        if not raw_text or len(raw_text) > raw_limit:
            continue
        card[key] = _clean_text(raw_text, limit=limit)
    return card


def _jue_wiki_memory_card_quality_for_prompt(card: dict[str, str]) -> dict[str, Any]:
    if not isinstance(card, dict) or not card:
        return {}
    core_keys = ("stance", "durable_facts", "lessons", "open_questions")
    present_keys = [key for key in core_keys if str(card.get(key) or "").strip()]
    missing_keys = [key for key in core_keys if key not in present_keys]
    evidence_keys = [key for key in present_keys if key != "stance"]
    if "stance" in present_keys and len(evidence_keys) >= 2:
        status = "strong"
    elif len(present_keys) >= 2:
        status = "partial"
    else:
        status = "weak"
    quality: dict[str, Any] = {
        "status": status,
        "present_keys": present_keys,
        "missing_keys": missing_keys,
    }
    if status != "strong":
        quality["required_action"] = "cross_check_live_research_before_high_confidence"
    return quality


def _compact_jue_wiki_requested_symbol_summary_for_prompt(
    value: Any,
    *,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key in (
        "symbol",
        "page_id",
        "title",
        "selected_as_page",
        "confidence",
        "freshness",
        "freshness_status",
        "quality_status",
        "updated_at",
        "as_of",
    ):
        raw = value.get(key)
        if raw not in ({}, [], "", None):
            row[key] = _clean_text(raw, limit=96) if isinstance(raw, str) else raw
    if "quality_status" in row:
        row["quality_status"] = normalize_jue_wiki_quality_status(
            row.get("quality_status")
        )
    quality_warnings = [
        _clean_text(warning, limit=120)
        for warning in _as_list(value.get("quality_warnings"))[:3]
        if str(warning or "").strip()
    ]
    if quality_warnings:
        row["quality_warnings"] = quality_warnings
    freshness_warnings = [
        _clean_text(warning, limit=120)
        for warning in _as_list(value.get("freshness_warnings"))[:3]
        if str(warning or "").strip()
    ]
    if freshness_warnings:
        row["freshness_warnings"] = freshness_warnings
    evidence_quality = _compact_jue_wiki_evidence_quality_for_prompt(
        value.get("evidence_quality")
    )
    if evidence_quality:
        row["evidence_quality"] = evidence_quality
        if not row.get("quality_status"):
            quality_status = _jue_wiki_quality_status_from_evidence(evidence_quality)
            if quality_status:
                row["quality_status"] = quality_status
        if not row.get("quality_warnings"):
            warnings = _jue_wiki_quality_warnings_from_evidence(evidence_quality)
            if warnings:
                row["quality_warnings"] = warnings
    summary = _clean_text(value.get("summary"), limit=max(int(string_limit), 1))
    if summary:
        row["summary"] = summary
    effectiveness = _compact_jue_wiki_page_effectiveness_for_prompt(
        value.get("effectiveness")
    )
    if effectiveness:
        row["effectiveness"] = effectiveness
    usage_guidance = _compact_jue_wiki_usage_guidance_for_prompt(
        value.get("usage_guidance")
    )
    if usage_guidance:
        row["usage_guidance"] = usage_guidance
    for key in (
        "usage_guidance_effectiveness",
        "memory_card_quality_effectiveness",
        "quality_warning_source_effectiveness",
    ):
        effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle_for_prompt(
            value.get(key)
        )
        if effectiveness_bundle:
            row[key] = effectiveness_bundle
    quality_warning_effectiveness = (
        _compact_jue_wiki_quality_warning_effectiveness_for_prompt(
            value.get("quality_warning_effectiveness")
        )
    )
    if quality_warning_effectiveness:
        row["quality_warning_effectiveness"] = quality_warning_effectiveness
        statuses = _compact_jue_wiki_status_list(
            value.get("quality_warning_effectiveness_statuses")
        )
        if not statuses:
            statuses = _compact_jue_wiki_status_list(
                [item.get("status") for item in quality_warning_effectiveness]
            )
        if statuses:
            row["quality_warning_effectiveness_statuses"] = statuses
    memory_card = value.get("memory_card")
    if isinstance(memory_card, dict):
        card = _compact_jue_wiki_memory_card_for_prompt(memory_card)
        if card:
            row["memory_card"] = card
            explicit_quality = (
                _compact_jue_wiki_memory_card_quality_details_for_prompt(
                    value.get("memory_card_quality")
                )
            )
            row["memory_card_quality"] = explicit_quality or (
                _jue_wiki_memory_card_quality_for_prompt(card)
            )
    return {key: child for key, child in row.items() if child not in ({}, [], "", None)}


def _compact_jue_wiki_freshness_summary_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {
        key: value.get(key)
        for key in ("page_count", "status_counts", "warning_counts")
        if value.get(key) not in ({}, [], "", None)
    }
    for key in ("stale_page_ids", "unknown_page_ids"):
        row[key] = [
            _clean_text(item, limit=120)
            for item in list(value.get(key) or [])[:12]
            if str(item or "").strip()
        ]
    return row


def _compact_jue_wiki_quality_warning_effectiveness_for_prompt(
    value: Any,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(value)[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        warning = _clean_text(item.get("warning"), limit=120)
        if not warning:
            continue
        row: dict[str, Any] = {"warning": warning}
        for key, max_len in (("page_id", 160), ("status", 80)):
            raw = item.get(key)
            if raw not in ({}, [], "", None):
                row[key] = _clean_text(raw, limit=max_len)
        if item.get("sample_count") not in ({}, [], "", None):
            row["sample_count"] = _safe_int(item.get("sample_count"))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if item.get(key) not in ({}, [], "", None):
                row[key] = _safe_float(item.get(key))
        reasons = [
            _clean_text(reason, limit=120)
            for reason in _as_list(item.get("reasons"))[:4]
            if str(reason or "").strip()
        ]
        if reasons:
            row["reasons"] = reasons
        rows.append(row)
    return rows


def _compact_jue_wiki_effectiveness_bundle_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (("status", 80), ("decision_use", 180)):
        raw = value.get(key)
        if raw not in ({}, [], "", None):
            row[key] = _clean_text(raw, limit=max_len)
    metrics: list[dict[str, Any]] = []
    for item in _as_list(value.get("metrics"))[:4]:
        if not isinstance(item, dict):
            continue
        metric: dict[str, Any] = {}
        for key, max_len in (
            ("warning", 120),
            ("page_id", 160),
            ("source_type", 80),
            ("source_id", 160),
            ("status", 80),
        ):
            raw = item.get(key)
            if raw not in ({}, [], "", None):
                metric[key] = _clean_text(raw, limit=max_len)
        if item.get("sample_count") not in ({}, [], "", None):
            metric["sample_count"] = _safe_int(item.get("sample_count"))
        for key in ("win_rate", "expectancy", "helpful_score", "confidence"):
            if item.get(key) not in ({}, [], "", None):
                metric[key] = _safe_float(item.get(key))
        reasons = [
            _clean_text(reason, limit=120)
            for reason in _as_list(item.get("reasons"))[:4]
            if str(reason or "").strip()
        ]
        if reasons:
            metric["reasons"] = reasons
        if metric:
            metrics.append(metric)
    if metrics:
        row["metrics"] = metrics
    return {key: child for key, child in row.items() if child not in ({}, [], "", None)}


def _compact_jue_wiki_effectiveness_reasons_for_prompt(
    value: Any,
    *,
    limit: int = 8,
    regular_limit: int = 3,
) -> list[str]:
    priority_prefixes = (
        "metric_source=",
        "page_id=",
        "raw_scope=",
        "raw_venue=",
        "base_playbook_id=",
        "raw_playbook_id=",
    )
    priority: list[str] = []
    regular: list[str] = []
    for item in _as_list(value):
        text = _clean_text(item, limit=160)
        if not text:
            continue
        target = (
            priority
            if any(text.startswith(prefix) for prefix in priority_prefixes)
            else regular
        )
        if text not in priority and text not in regular:
            target.append(text)

    compacted: list[str] = []
    max_items = max(int(limit), 0)
    for text in priority:
        if text not in compacted:
            compacted.append(text)
        if len(compacted) >= max_items:
            return compacted
    max_regular = min(max(int(regular_limit), 0), max(max_items - len(compacted), 0))
    for text in regular[:max_regular]:
        if text not in compacted:
            compacted.append(text)
        if len(compacted) >= max_items:
            return compacted
    return compacted


def _compact_jue_wiki_page_effectiveness_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (
        ("status", 80),
        ("venue", 80),
        ("horizon", 80),
    ):
        raw = value.get(key)
        if raw not in ({}, [], "", None):
            row[key] = _clean_text(raw, limit=max_len)
    if value.get("sample_count") not in ({}, [], "", None):
        row["sample_count"] = _safe_int(value.get("sample_count"))
    for key in (
        "win_rate",
        "expectancy",
        "avg_return_pct",
        "median_mae_pct",
        "drawdown_pressure",
        "helpful_score",
        "confidence",
    ):
        if value.get(key) not in ({}, [], "", None):
            row[key] = _safe_float(value.get(key))
    reasons = _compact_jue_wiki_effectiveness_reasons_for_prompt(value.get("reasons"))
    if reasons:
        row["reasons"] = reasons
    return {key: child for key, child in row.items() if child not in ({}, [], "", None)}


def _compact_jue_wiki_usage_guidance_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (
        ("trust_level", 40),
        ("risk_posture", 80),
        ("decision_use", 180),
    ):
        raw = value.get(key)
        if raw not in ({}, [], "", None):
            row[key] = _clean_text(raw, limit=max_len)
    for key in ("allowed_uses", "required_cross_checks"):
        items = [
            _clean_text(item, limit=100)
            for item in _as_list(value.get(key))[:8]
            if str(item or "").strip()
        ]
        if items:
            row[key] = items
    if value.get("hard_blocker") not in ({}, [], "", None):
        row["hard_blocker"] = _truthy_gate_value(value.get("hard_blocker"))
    if value.get("max_confidence_without_cross_check") not in ({}, [], "", None):
        row["max_confidence_without_cross_check"] = _safe_float(
            value.get("max_confidence_without_cross_check")
        )
    return {key: child for key, child in row.items() if child not in ({}, [], "", None)}


def _compact_jue_wiki_status_list(value: Any, *, limit: int = 6) -> list[str]:
    statuses: list[str] = []
    for item in _as_list(value)[: max(int(limit), 0)]:
        status = _clean_text(item, limit=80).lower()
        if status and status not in statuses:
            statuses.append(status)
    return statuses


def _compact_jue_wiki_effectiveness_attention_items_for_prompt(
    value: Any,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _as_list(value)[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key, max_len in (
            ("page_id", 160),
            ("kind", 80),
            ("status", 80),
            ("evidence_id", 180),
            ("warning", 160),
        ):
            raw = item.get(key)
            if raw not in ({}, [], "", None):
                row[key] = _clean_text(raw, limit=max_len)
        if row and row not in items:
            items.append(row)
    return items


def _jue_wiki_effectiveness_attention_items_from_rows_for_prompt(
    rows: list[Any],
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        page_id = _clean_text(row.get("page_id"), limit=160)
        if not page_id:
            continue
        for kind, key in (
            ("usage_guidance", "usage_guidance_effectiveness"),
            ("memory_card_quality", "memory_card_quality_effectiveness"),
            ("quality_warning_source", "quality_warning_source_effectiveness"),
            ("quality_warning", "quality_warning_effectiveness"),
        ):
            for item in _jue_wiki_effectiveness_attention_items_for_value_for_prompt(
                page_id=page_id,
                kind=kind,
                value=row.get(key),
            ):
                if item not in items:
                    items.append(item)
                if len(items) >= limit:
                    return _compact_jue_wiki_effectiveness_attention_items_for_prompt(
                        items,
                        limit=limit,
                    )
    return _compact_jue_wiki_effectiveness_attention_items_for_prompt(
        items,
        limit=limit,
    )


def _jue_wiki_effectiveness_attention_items_for_value_for_prompt(
    *,
    page_id: str,
    kind: str,
    value: Any,
) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else [value]
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metrics = [
            metric for metric in _as_list(row.get("metrics")) if isinstance(metric, dict)
        ]
        source_rows = metrics or [row]
        for source in source_rows:
            if not isinstance(source, dict):
                continue
            status = _clean_text(
                source.get("status") or row.get("status"),
                limit=80,
            ).lower()
            evidence_id = (
                ""
                if kind == "quality_warning"
                else _clean_text(
                    source.get("page_id")
                    or source.get("source_id")
                    or source.get("rule_id"),
                    limit=180,
                )
            )
            warning = _clean_text(
                source.get("warning") or row.get("warning"),
                limit=160,
            )
            if not status and not evidence_id and not warning:
                continue
            item: dict[str, Any] = {"page_id": page_id, "kind": kind}
            if status:
                item["status"] = status
            if evidence_id:
                item["evidence_id"] = evidence_id
            if warning:
                item["warning"] = warning
            if item not in items:
                items.append(item)
    return items


def compact_jue_wiki_for_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> Any:
    if not isinstance(value, dict):
        return compact_prompt_value_bounded(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
            dict_limit=12,
            placeholder_strings=False,
        )

    pages = value.get("pages") if isinstance(value.get("pages"), list) else []
    compact_pages: list[dict[str, Any]] = []
    for page in pages[: max(int(list_limit), 0)]:
        if not isinstance(page, dict):
            continue
        compact_page: dict[str, Any] = {}
        for key in (
            "page_id",
            "title",
            "page_type",
            "scope",
            "symbol",
            "lane",
            "regime",
            "quality_status",
            "freshness",
            "freshness_status",
            "updated_at",
            "as_of",
            "confidence",
            "score",
        ):
            raw = page.get(key)
            if raw not in ({}, [], "", None):
                compact_page[key] = (
                    _clean_text(raw, limit=96) if isinstance(raw, str) else raw
                )
        if "quality_status" in compact_page:
            compact_page["quality_status"] = normalize_jue_wiki_quality_status(
                compact_page.get("quality_status")
            )
        for key in ("summary", "content", "body", "notes"):
            raw = page.get(key)
            if raw in ({}, [], "", None):
                continue
            out_key = key if key == "summary" else "excerpt"
            excerpt_limit = (
                max(int(string_limit), 1)
                if out_key == "summary"
                else max(min(int(string_limit), 120), 1)
            )
            compact_page[out_key] = _clean_text(
                raw,
                limit=excerpt_limit,
            )
            break
        refs = page.get("evidence_refs")
        if isinstance(refs, list):
            compact_page["evidence_refs"] = [
                _clean_text(ref, limit=64) for ref in refs[:2]
            ]
        selection_reasons = page.get("selection_reasons")
        if isinstance(selection_reasons, list):
            compact_page["selection_reasons"] = [
                _clean_text(reason, limit=96)
                for reason in selection_reasons[:3]
                if str(reason or "").strip()
            ]
        quality_warnings = page.get("quality_warnings")
        if isinstance(quality_warnings, list):
            compact_page["quality_warnings"] = [
                _clean_text(warning, limit=120)
                for warning in quality_warnings[:3]
                if str(warning or "").strip()
            ]
        freshness_warnings = page.get("freshness_warnings")
        if isinstance(freshness_warnings, list):
            compact_page["freshness_warnings"] = [
                _clean_text(warning, limit=120)
                for warning in freshness_warnings[:3]
                if str(warning or "").strip()
            ]
        selection_penalties = page.get("selection_penalties")
        if isinstance(selection_penalties, list):
            compact_page["selection_penalties"] = [
                _clean_text(penalty, limit=96)
                for penalty in selection_penalties[:3]
                if str(penalty or "").strip()
            ]
        source_refs = page.get("source_refs")
        if isinstance(source_refs, list):
            refs = [
                ref
                for ref in (
                    _compact_jue_wiki_source_ref_for_prompt(
                        source_ref,
                        string_limit=96,
                    )
                    for source_ref in source_refs[:2]
                )
                if ref not in ({}, [], "", None)
            ]
            if refs:
                compact_page["source_refs"] = refs
        evidence_quality = page.get("evidence_quality")
        if isinstance(evidence_quality, dict):
            compact_page["evidence_quality"] = (
                _compact_jue_wiki_evidence_quality_for_prompt(evidence_quality)
            )
            if not compact_page.get("quality_status"):
                quality_status = _jue_wiki_quality_status_from_evidence(
                    compact_page["evidence_quality"]
                )
                if quality_status:
                    compact_page["quality_status"] = quality_status
            if not compact_page.get("quality_warnings"):
                quality_warnings = _jue_wiki_quality_warnings_from_evidence(
                    compact_page["evidence_quality"]
                )
                if quality_warnings:
                    compact_page["quality_warnings"] = quality_warnings
        memory_card_quality = (
            _compact_jue_wiki_memory_card_quality_details_for_prompt(
                page.get("memory_card_quality")
            )
        )
        if memory_card_quality:
            compact_page["memory_card_quality"] = memory_card_quality
        effectiveness = _compact_jue_wiki_page_effectiveness_for_prompt(
            page.get("effectiveness")
        )
        if effectiveness:
            compact_page["effectiveness"] = effectiveness
        usage_guidance = _compact_jue_wiki_usage_guidance_for_prompt(
            page.get("usage_guidance")
        )
        if usage_guidance:
            compact_page["usage_guidance"] = usage_guidance
        for source_key in (
            "usage_guidance_effectiveness",
            "memory_card_quality_effectiveness",
            "quality_warning_source_effectiveness",
        ):
            effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle_for_prompt(
                page.get(source_key)
            )
            if effectiveness_bundle:
                compact_page[source_key] = effectiveness_bundle
        quality_warning_effectiveness = (
            _compact_jue_wiki_quality_warning_effectiveness_for_prompt(
                page.get("quality_warning_effectiveness")
            )
        )
        if quality_warning_effectiveness:
            compact_page["quality_warning_effectiveness"] = (
                quality_warning_effectiveness
            )
            statuses = _compact_jue_wiki_status_list(
                page.get("quality_warning_effectiveness_statuses")
            )
            if not statuses:
                statuses = _compact_jue_wiki_status_list(
                    [item.get("status") for item in quality_warning_effectiveness]
                )
            if statuses:
                compact_page["quality_warning_effectiveness_statuses"] = statuses
        if compact_page:
            compact_pages.append(compact_page)

    compact: dict[str, Any] = {}
    for key in (
        "status",
        "prompt_mode",
        "selection_run_id",
        "target_scope",
        "primary_context",
        "raw_context_policy",
        "evidence_quality_summary",
    ):
        raw = value.get(key)
        if raw not in ({}, [], "", None):
            compact[key] = _clean_text(raw, limit=120) if isinstance(raw, str) else raw
    evidence_quality = value.get("evidence_quality")
    if isinstance(evidence_quality, dict):
        compact["evidence_quality"] = _compact_jue_wiki_evidence_quality_for_prompt(
            evidence_quality
        )
    freshness_summary = _compact_jue_wiki_freshness_summary_for_prompt(
        value.get("freshness_summary")
    )
    if freshness_summary:
        compact["freshness_summary"] = freshness_summary
    effectiveness_attention_items = (
        _compact_jue_wiki_effectiveness_attention_items_for_prompt(
            value.get("effectiveness_attention_items"),
            limit=max(int(list_limit), 1) * 4,
        )
    )
    if effectiveness_attention_items:
        compact["effectiveness_attention_items"] = effectiveness_attention_items
    repair_contract = compact_jue_wiki_repair_contract_prompt(
        {"action_batches": value.get("repair_action_batches")},
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if repair_contract.get("action_batches"):
        compact["repair_action_batches"] = repair_contract["action_batches"]
    repair_queue = _compact_jue_wiki_repair_queue_for_prompt(
        value.get("repair_queue"),
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if repair_queue:
        compact["repair_queue"] = repair_queue
    raw_symbols = value.get("symbols")
    if isinstance(raw_symbols, list) and raw_symbols:
        compact["symbols"] = [
            _clean_text(symbol, limit=32)
            for symbol in raw_symbols[: max(int(list_limit), 0)]
            if str(symbol or "").strip()
        ]
    raw_content = value.get("content")
    if raw_content not in ({}, [], "", None):
        compact["content"] = _clean_text(
            raw_content,
            limit=max(int(string_limit), 1),
        )
    requested_symbol_summaries = value.get("requested_symbol_summaries")
    if isinstance(requested_symbol_summaries, list):
        summaries = [
            row
            for row in (
                _compact_jue_wiki_requested_symbol_summary_for_prompt(
                    summary,
                    string_limit=string_limit,
                )
                for summary in requested_symbol_summaries[: max(int(list_limit), 0)]
            )
            if row
        ]
        if summaries:
            compact["requested_symbol_summaries"] = summaries
        omitted = max(len(requested_symbol_summaries) - len(summaries), 0)
        if omitted:
            compact["requested_symbol_summaries_omitted_count"] = omitted
    budget_report = value.get("budget_report")
    if isinstance(budget_report, dict):
        compact["budget_report"] = compact_prompt_value_bounded(
            budget_report,
            list_limit=4,
            string_limit=120,
            dict_limit=12,
            placeholder_strings=False,
        )
        requested_symbol_coverage = _requested_symbol_coverage_from_budget_report(
            budget_report,
            list_limit=list_limit,
            string_limit=string_limit,
        )
        if requested_symbol_coverage:
            compact["requested_symbol_coverage"] = requested_symbol_coverage
    compact["page_count"] = len(pages)
    compact["pages"] = compact_pages
    omitted = max(len(pages) - len(compact_pages), 0)
    if omitted:
        compact["omitted_page_count"] = omitted
    if "effectiveness_attention_items" not in compact:
        derived_attention_items = (
            _jue_wiki_effectiveness_attention_items_from_rows_for_prompt(
                [
                    *list(compact.get("pages") or []),
                    *list(compact.get("requested_symbol_summaries") or []),
                ],
                limit=max(int(list_limit), 1) * 4,
            )
        )
        if derived_attention_items:
            compact["effectiveness_attention_items"] = derived_attention_items
    compact["compacted_for_prompt_budget"] = True
    return compact


def _compact_jue_wiki_repair_queue_for_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("open_count", "resolved_count"):
        if value.get(key) not in ({}, [], "", None):
            out[key] = _safe_int(value.get(key))
    symbol_limit = max(int(list_limit), 0) * 4
    open_symbols = [
        _clean_text(symbol, limit=32)
        for symbol in _as_list(value.get("open_symbols"))[:symbol_limit]
        if str(symbol or "").strip()
    ]
    if open_symbols:
        out["open_symbols"] = open_symbols
    repair_contract = compact_jue_wiki_repair_contract_prompt(
        {"action_batches": value.get("open_action_batches")},
        list_limit=list_limit,
        string_limit=string_limit,
    )
    if repair_contract.get("action_batches"):
        out["open_action_batches"] = repair_contract["action_batches"]
    return {
        key: child
        for key, child in out.items()
        if child not in ({}, [], "", None)
    }


def manager_run_workflow_provenance(prompt: dict[str, Any]) -> dict[str, Any]:
    workflow = prompt.get("jue_workflow") if isinstance(prompt, dict) else {}
    if not isinstance(workflow, dict):
        workflow = {}
    try:
        workflow_version = int(workflow.get("workflow_version") or 0)
    except (TypeError, ValueError):
        workflow_version = 0

    skills = workflow.get("skills")
    contracts = workflow.get("contracts")
    skill_ids = [
        str(row.get("skill_id") or "").strip()
        for row in skills
        if isinstance(row, dict) and str(row.get("skill_id") or "").strip()
    ] if isinstance(skills, list) else []
    contract_ids = [
        str(row.get("contract_id") or "").strip()
        for row in contracts
        if isinstance(row, dict) and str(row.get("contract_id") or "").strip()
    ] if isinstance(contracts, list) else []
    return {
        "workflow_id": str(workflow.get("workflow_id") or ""),
        "workflow_version": workflow_version,
        "skill_ids_json": _json_dumps(skill_ids),
        "contract_ids_json": _json_dumps(contract_ids),
    }


def _manager_workflow_with_core_contracts(value: Any | None = None) -> dict[str, Any]:
    workflow = dict(value) if isinstance(value, dict) else {}
    if not str(workflow.get("workflow_id") or "").strip():
        workflow["workflow_id"] = "binance_block_manager"
    contracts = [
        dict(row)
        for row in _as_list(workflow.get("contracts"))
        if isinstance(row, dict)
    ]
    contract_ids = {
        str(row.get("contract_id") or "").strip()
        for row in contracts
        if str(row.get("contract_id") or "").strip()
    }
    if "jue_wiki_usage_contract_resolution" not in contract_ids:
        contracts.append(
            {
                "contract_id": "jue_wiki_usage_contract_resolution",
                "source": "policy.jue_wiki_usage_contract_policy",
                "required_metadata": "jue_wiki_usage_contract_resolution",
                "purpose": (
                    "preserve audit provenance for Wiki usage-contract "
                    "resolution on every affected block action"
                ),
            }
        )
    workflow["contracts"] = contracts
    return workflow


def _compact_manager_jue_workflow_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    workflow = _manager_workflow_with_core_contracts(value)
    compact = compact_prompt_value(
        workflow,
        list_limit=list_limit,
        string_limit=string_limit,
    )
    compact_workflow = compact if isinstance(compact, dict) else {}
    contracts = [
        row
        for row in _as_list(compact_workflow.get("contracts"))
        if isinstance(row, dict)
    ]
    if not any(
        row.get("contract_id") == "jue_wiki_usage_contract_resolution"
        for row in contracts
    ):
        source_contract = next(
            (
                row
                for row in _as_list(workflow.get("contracts"))
                if isinstance(row, dict)
                and row.get("contract_id") == "jue_wiki_usage_contract_resolution"
            ),
            None,
        )
        if source_contract:
            preserved = compact_prompt_value(
                source_contract,
                list_limit=max(int(list_limit), 1),
                string_limit=string_limit,
            )
            if isinstance(preserved, dict):
                contracts.append(preserved)
    if contracts:
        compact_workflow["contracts"] = contracts
    return compact_workflow


def manager_candidate_identity_from_payload(
    payload: Any,
) -> tuple[str, str, str, str] | None:
    if not isinstance(payload, dict):
        return None
    template = (
        payload.get("block_template")
        if isinstance(payload.get("block_template"), dict)
        else {}
    )
    symbol = str(payload.get("symbol") or template.get("symbol") or "").upper().strip()
    if not symbol:
        return None
    market = normalize_market(
        payload.get("market") or payload.get("venue") or template.get("market")
    )
    side = normalize_position_side(
        payload.get("side")
        or payload.get("direction")
        or payload.get("stance")
        or template.get("side")
    )
    horizon = normalize_binance_horizon(
        payload.get("horizon") or template.get("horizon"),
        market=market,
    )
    return (symbol, market, side, horizon)


def manager_action_candidate_keys(actions: Any) -> set[tuple[str, str, str, str]]:
    if not isinstance(actions, dict):
        return set()
    keys: set[tuple[str, str, str, str]] = set()
    for section in ("create_blocks", "update_blocks", "pause_blocks", "close_blocks"):
        rows = actions.get(section)
        for row in rows if isinstance(rows, list) else []:
            key = manager_candidate_identity_from_payload(row)
            if key:
                keys.add(key)
    return keys


def _manager_prompt_priority_candidate_keys(
    prompt: dict[str, Any],
    existing: set[tuple[str, str, str, str]] | None = None,
) -> set[tuple[str, str, str, str]]:
    keys = set(existing or set())
    if not isinstance(prompt, dict):
        return keys
    pressure = prompt.get("proactive_decision_pressure")
    pressure = pressure if isinstance(pressure, dict) else {}
    top_candidates = pressure.get("top_candidates")
    if isinstance(top_candidates, dict) and isinstance(
        top_candidates.get("items"),
        list,
    ):
        top_candidates = top_candidates.get("items")
    for row in _as_list(top_candidates):
        key = manager_candidate_identity_from_payload(row)
        if key:
            keys.add(key)
    return keys


def prioritize_manager_candidate_rows(
    rows: Any,
    priority_candidate_keys: set[tuple[str, str, str, str]],
) -> list[Any]:
    if not isinstance(rows, (list, tuple)) or not priority_candidate_keys:
        return list(rows) if isinstance(rows, (list, tuple)) else []
    priority_symbols = {key[0] for key in priority_candidate_keys}
    priority: list[Any] = []
    rest: list[Any] = []
    seen_ids: set[int] = set()
    for row in rows:
        key = manager_candidate_identity_from_payload(row)
        symbol = key[0] if key else str(
            row.get("symbol") if isinstance(row, dict) else ""
        ).upper().strip()
        if key in priority_candidate_keys or symbol in priority_symbols:
            priority.append(row)
            seen_ids.add(id(row))
        else:
            rest.append(row)
    return [*priority, *[row for row in rest if id(row) not in seen_ids]]


def _candidate_compaction_lane(row: Any) -> str:
    if isinstance(row, dict):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        calculated = row.get("calculated") if isinstance(row.get("calculated"), dict) else {}
        lane = str(
            row.get("lane")
            or metadata.get("lane")
            or calculated.get("lane")
            or ""
        ).strip().lower()
        if lane == "volatile_attack":
            return "volatile_attack"
    key = manager_candidate_identity_from_payload(row)
    if key:
        _symbol, market, side, _horizon = key
        return f"{market}:{side}"
    if isinstance(row, dict):
        lane = str(row.get("lane") or "").strip().lower()
        if lane:
            return lane
        raw_market = row.get("market") or row.get("venue")
        market = normalize_market(raw_market) if raw_market not in (None, "") else ""
        side = normalize_position_side(row.get("side") or row.get("stance"))
        return f"{market}:{side}"
    return "unknown"


def select_manager_candidate_rows_for_compaction(
    rows: Any,
    *,
    limit: int,
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
    lane_diverse: bool = False,
) -> list[Any]:
    if not isinstance(rows, (list, tuple)):
        return []
    ordered = prioritize_manager_candidate_rows(rows, priority_candidate_keys or set())
    bounded_limit = max(int(limit), 0)
    if not lane_diverse or bounded_limit <= 1:
        return ordered[:bounded_limit]
    selected: list[Any] = []
    seen_ids: set[int] = set()

    def add(row: Any) -> None:
        if len(selected) >= bounded_limit or id(row) in seen_ids:
            return
        selected.append(row)
        seen_ids.add(id(row))

    for row in ordered:
        key = manager_candidate_identity_from_payload(row)
        if key and priority_candidate_keys and (
            key in priority_candidate_keys
            or key[0] in {priority_key[0] for priority_key in priority_candidate_keys}
        ):
            add(row)
    target_lanes = (
        "volatile_attack",
        "spot:long",
        "futures:long",
        "futures:short",
        "upbit_spot:long",
    )
    for lane in target_lanes:
        for row in ordered:
            if id(row) in seen_ids:
                continue
            if _candidate_compaction_lane(row) == lane:
                add(row)
                break
    for row in ordered:
        add(row)
    return selected


def _binance_period_memory_action_metadata_output_schema(
    action_label: str,
) -> dict[str, str]:
    return {
        "period_memory_coverage_gap": (
            "optional string; required when memory.period_memory_coverage shows "
            f"missing weekly/monthly review or replay for this {action_label}; "
            "describe the gap and confidence/risk effect"
        ),
        "period_memory_override_reason": (
            "optional string; required when this action proceeds despite a period "
            "memory coverage gap; explain why current live evidence overrides the gap"
        ),
        "metadata_contract_audit_resolution": (
            "optional string; required when validation repair or policy_rules mention "
            "metadata_contract_audit_resolution; explain how this action resolves, "
            "defers, or compensates for the period memory metadata contract audit gap"
        ),
        "metadata_contract_repair_note": (
            "optional string; required when validation_repair.block_design_constraints "
            "provide metadata_contract_repair_note; copy the compact repair note so "
            "reflection can verify that this action followed the memory contract repair"
        ),
        "jue_wiki_selection_resolution": (
            "optional string; required when "
            "memory.jue_wiki_selection_memory.application_guidance requires freshness "
            "repair and this action uses or sizes from the selected Wiki context; "
            "cite fresh_jue_wiki_context, selection_audit_resolution, or "
            "live_cross_check"
        ),
        "jue_wiki_freshness_cross_check": (
            "optional string; live book/spread/funding/quant/research evidence used "
            "to cross-check stale selected Wiki pages before size increase"
        ),
        "jue_wiki_context_gap": (
            "optional string; required when memory.jue_wiki or jue_wiki is "
            "disabled/error/unavailable and this action proceeds; explain the "
            "missing Wiki context and the live_cross_check, crypto_quant, book, "
            "spread, funding, live_authority, or research evidence used instead"
        ),
        "jue_wiki_reference_basis": (
            "optional string; required when "
            "memory.jue_wiki_action_reference_memory.application_guidance requires "
            "wiki-reference repair and this action proceeds; either cite the "
            "selected Wiki cross-check or explicitly name the book/quant/research "
            "basis that overrode Wiki memory"
        ),
        "jue_wiki_usage_contract_resolution": (
            "optional string; required when "
            "jue_wiki_application.trust_profile.usage_contract or "
            "memory.jue_wiki_usage_contract_memory.application_guidance requires "
            f"usage-contract resolution for this {action_label}; state that Wiki "
            "memory has no standalone trade authority and name the live_spread, "
            "funding, liquidation_distance, account_state, risk_gate, book, quant, "
            "or research cross-check that confirmed, reduced, or overrode the Wiki "
            "usage contract"
        ),
    }


def build_binance_manager_prompt_payload(
    *,
    allowed_actions: list[str],
    language_policy: dict[str, Any],
    manager_lanes: list[str],
    account: dict[str, Any],
    growth_target: dict[str, Any],
    growth_governor: dict[str, Any],
    growth_unlock: dict[str, Any],
    risk_guard: dict[str, Any],
    execution_gate: dict[str, Any],
    memory_context: dict[str, Any],
    decision_packet_v2: dict[str, Any],
    decision_packet: dict[str, Any],
    candidate_policy_impacts: dict[str, Any],
    validation_repair: dict[str, Any],
    crypto_market_pulse: dict[str, Any],
    raw_context_refs: dict[str, Any],
    recent_performance: dict[str, Any],
    performance: dict[str, Any],
    entry_gate_policy: dict[str, Any],
    live_authority: dict[str, Any],
    lane_balance: dict[str, Any],
    candidate_generation: dict[str, Any],
    candidates: list[dict[str, Any]],
    universe: list[str],
    market_universe: dict[str, list[str]],
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    decision_bundle = build_canonical_decision_prompt_bundle(
        target_scope="binance",
        decision_packet_v2=decision_packet_v2,
        legacy_decision_packet=decision_packet,
        base_inputs=[
            "account",
            "growth_target",
            "growth_governor",
            "growth_unlock",
            "risk_guard",
            "execution_gate",
            "memory",
        ],
        extra_inputs=[
            "candidate_policy_impacts",
            "validation_repair",
            "crypto_market_pulse",
            "recent_performance",
            "performance",
            "live_authority",
            "lane_balance",
            "candidate_generation",
            "candidates",
            "market_universe",
            "blocks",
        ],
    )
    decision_inputs = list(decision_bundle["decision_inputs"])
    has_candidate_memory_hints = any(
        isinstance(row, dict) and isinstance(row.get("memory_hint"), dict)
        for row in candidates
    )
    if has_candidate_memory_hints and "candidate_memory_hint_policy" not in decision_inputs:
        decision_inputs.append("candidate_memory_hint_policy")
    payload = {
        "task": (
            "Manage independent crypto trading blocks across Binance spot, "
            "Binance futures, and Upbit KRW spot. Return JSON only."
        ),
        "jue_workflow": _manager_workflow_with_core_contracts(),
        "critical_response_contract": {
            "version": "binance_manager_response_contract_v2",
            "required_top_level_keys": [
                "adopt_existing_blocks",
                "create_blocks",
                "update_blocks",
                "close_blocks",
                "pause_blocks",
                "hold_decision",
                "lane_review",
                "validation_repair_resolution",
            ],
            "strict_json_only": True,
            "lane_review": {
                "required": True,
                "invalid_if_missing": True,
                "required_even_when_no_actions": True,
                "lanes_reviewed": list(manager_lanes),
                "minimum_fields": [
                    "dominant_lane",
                    "lanes_reviewed",
                    "selected_lanes",
                    "non_selected_lane_reasons",
                    "concentration_note",
                    "exploration_watch",
                ],
            },
            "on_hold_only": {
                "lane_review_required": True,
                "dominant_lane": "none when no lane is selected",
                "selected_lanes": [],
                "instruction": (
                    "If no adopt_existing_blocks/create_blocks action is selected and no "
                    "adopt/update/close/pause management action is selected, still return "
                    "lane_review and explain each non-selected lane. Missing lane_review "
                    "causes the run to fail and no trade action will be applied."
                ),
            },
            "create_blocks_candidate_visibility": {
                "required": True,
                "error": "manager_create_candidate_not_visible",
                "instruction": (
                    "New create_blocks must use symbols and markets visible in "
                    "candidates/candidates.items. market_universe is a research and "
                    "quote scope, not permission to invent a new block. If a "
                    "market_universe symbol looks attractive but is absent from visible "
                    "candidates, put it in hold_decision.watch_symbols/data_gaps or "
                    "wait for candidate generation to surface it."
                ),
            },
            "waiting_entry_gate_hint": {
                "instruction": (
                    "When candidates[].waiting_entry_gate_hint.status is "
                    "waiting_entry_base_gate_available, the candidate is below the "
                    "immediate entry gate but still meets the small waiting-entry "
                    "base gate. Do not reject solely on immediate min_confidence; "
                    "either create/update the small wait_for_price block if live "
                    "spread, depth, funding, pattern, venue, and risk gates pass, "
                    "or reject with the current gate that blocks execution."
                )
            },
            "validation_repair_resolution": {
                "required_when_validation_repair_present": True,
                "accepted_resolutions": [
                    "create or update a smaller waiting/probe block",
                    "stage a concrete next trigger in hold_decision.next_triggers",
                    "reject a candidate with exact missing price/depth/funding/risk evidence",
                    "defer only because an explicit server safety gate blocks execution",
                ],
                "hold_only_requires": [
                    "candidate symbols reviewed",
                    "repair discipline or degraded wiki page addressed",
                    "next executable trigger or precise data gap recorded",
                ],
                "action_required_for_resolutions": {
                    "resolutions": [
                        "probe_waiting_block",
                        "small_waiting_block",
                        "one_share_probe",
                        "create or update a smaller waiting/probe block",
                    ],
                    "required_action_sections": ["create_blocks", "update_blocks"],
                    "error": (
                        "validation_repair_action_missing_from_model: executable "
                        "validation_repair_resolution rows such as probe_waiting_block, "
                        "small_waiting_block, one_share_probe, or create/update "
                        "waiting probe must include a matching "
                        "create_blocks or update_blocks action for the same symbol and "
                        "market. Use candidate_rejected or safety_gate_defer when no "
                        "action is selected."
                    ),
                },
                "repairable_probe_design": {
                    "required_when_present": (
                        "proactive_decision_pressure.top_candidates[]."
                        "validation_repair_probe_design"
                    ),
                    "allowed_rejection_evidence": [
                        "spread/depth/orderbook/liquidity gate",
                        "funding or derivatives venue gate",
                        "confidence or safety gate",
                    ],
                    "error": (
                        "validation_repair_probe_design_ignored_from_model: when a "
                        "top candidate contains validation_repair_probe_design, do "
                        "not reject it solely because the original stop was wider "
                        "than validation_repair. Either create/update the matching "
                        "waiting probe from the provided design, or reject with a "
                        "current live execution gate such as spread, depth, funding, "
                        "liquidity, confidence, venue availability, or safety gate "
                        "inside evidence_gap/reason. next_trigger alone does not "
                        "satisfy this rejection evidence; it is only the future "
                        "unlock condition. pattern prior, research_only, "
                        "immediate_block_allowed=false, and original stop/RR "
                        "defects are not sufficient rejection evidence when the "
                        "provided validation_repair_probe_design already repairs "
                        "the executable waiting-entry geometry."
                    ),
                    "min_executable_qty_error": (
                        "validation_repair_min_executable_qty_missing_from_model: "
                        "when validation_repair_probe_design includes "
                        "min_executable_qty, matching create_blocks/update_blocks "
                        "actions must use qty at or above that value, or reject the "
                        "candidate with a current live execution gate instead of "
                        "creating an unexecutable dust probe."
                    ),
                },
                "instruction": (
                    "validation_repair, jue_wiki_repair_contract, "
                    "jue_wiki_validation_repair_contract, and "
                    "jue_wiki_contract_feedback_gap, and "
                    "jue_wiki_memory_card_quality are repair work, not blanket "
                    "no-trade reasons. Resolve them into candidate-level execution "
                    "checks, smaller waiting/probe designs, or precise reject "
                    "conditions. If all actions are empty, hold_decision must still "
                    "name which candidates were rejected and what evidence would "
                    "unlock them. When an action proceeds under active "
                    "jue_wiki_repair_contract pressure, include "
                    "jue_wiki_repair_pressure or jue_wiki_repair_resolution on the "
                    "action so the block record shows how stale/omitted Wiki repair "
                    "work affected sizing, horizon, or evidence requirements. When "
                    "an action uses degraded Wiki effectiveness, the action metadata "
                    "itself must include jue_wiki_repair_resolution or "
                    "jue_wiki_repair_pressure; a response-only repair note is not "
                    "enough. When "
                    "jue_wiki_memory_card_quality flags thin Wiki memory cards, include "
                    "jue_wiki_memory_card_quality or jue_wiki_memory_card_cross_check on "
                    "the action, or record a concrete hold_decision trigger/data gap for "
                    "live research cross-check before high-confidence judgment."
                ),
                "blanket_hold_allowed": False,
            },
            "failure_policy": (
                "Do not omit required keys. Do not replace missing evidence with "
                "fabricated values; describe missing evidence in hold_decision.data_gaps."
            ),
        },
        "policy": {
            "allowed_actions": sorted(
                set(allowed_actions) | {"adopt_existing_blocks"}
            ),
            "execution_guard": (
                "No live order is sent unless market-specific execution flags are enabled "
                "and kill switch is released."
            ),
            "risk_guard_policy": (
                "risk_guard is a server-enforced account drawdown gate. If "
                "risk_guard.allow_new_entries is false, do not create new blocks. "
                "You may still update, pause, or request exits for existing blocks."
            ),
            "growth_governor_policy": (
                "growth_governor is a server-enforced growth cadence controller. "
                "If mode=edge_rebuild, Jue may create up to two small, executable "
                "waiting-entry blocks per cycle when the candidates are distinct "
                "enough and every price, cost, depth, and risk gate is satisfied; "
                "avoid immediate entries for rows that match weak_lanes until live "
                "block edge improves. If growth_governor.positive_lanes is present, "
                "treat those lanes as recovered lanes rather than blanket rebuild "
                "lanes: Jue may selectively press them when live_authority, book, "
                "funding, spread, quant, and cost gates agree, while still keeping "
                "weak_lanes in probe/waiting-entry mode. If mode=press_verified_edges, "
                "Jue may press verified confluence with more "
                "active entries, but still obey every risk, spread, depth, and "
                "execution gate. If mode=halt_new_entries, update or exit existing "
                "blocks only."
            ),
            "growth_unlock_policy": (
                "growth_unlock is the explicit recovery ladder from observation or "
                "edge_rebuild into probe, immediate, and scale phases. Use "
                "growth_unlock.criteria and growth_unlock.next_missions to decide "
                "what evidence Jue must collect next. Do not claim the system is "
                "ready to scale unless growth_unlock.action_permissions.scale_up "
                "is true."
            ),
            "futures_guard": (
                "Futures v1 supports isolated margin only. Leverage and liquidation "
                "distance gates are enforced after the model response."
            ),
            "memory_scope_policy": (
                "Treat memory.scoped_memory.core and local Binance memories as primary. "
                "Treat translated KIS memories only as translated lessons and process "
                "checks, not as direct crypto asset evidence or direct crypto rules. "
                "memory.translated_policy_context contains translated lessons only; "
                "use it to ask better checks, not to override crypto research, "
                "quant, book, funding, account, or safety-gate evidence. Read "
                "available_count, omitted_count, and source_scope_counts as "
                "coverage/selection metadata: visible translated lessons are only "
                "a sample when omitted_count is positive, so do not overfit the "
                "visible items."
            ),
            "candidate_memory_hint_policy": (
                "Each candidate may include memory_hint derived from Binance-scoped "
                "symbol analyses, local crypto memory, or explicitly translated memory. "
                "Use it as candidate-level prior evidence: cite it when it supports a "
                "block, record it as a risk when it conflicts, and reject or downsize "
                "the candidate when memory warns about repeated failure modes that live "
                "book, funding, quant, and authority evidence do not repair."
            ),
            "period_memory_coverage_policy": {
                "source": "memory.period_memory_coverage",
                "applies_to_scope": "binance",
                "missing_coverage_decision_effect": [
                    (
                        "record missing weekly/monthly review or replay in "
                        "hold_decision.data_gaps"
                    ),
                    (
                        "reduce action confidence or explain why current live "
                        "evidence overrides the gap"
                    ),
                    (
                        "include the gap in risk_note or action metadata for "
                        "affected block actions"
                    ),
                    (
                        "when policy_rules or validation_repair require "
                        "metadata_contract_audit_resolution, populate "
                        "metadata_contract_audit_resolution on the affected action"
                    ),
                    (
                        "when validation_repair provides metadata_contract_repair_note, "
                        "copy that note into metadata_contract_repair_note on the "
                        "affected action"
                    ),
                ],
                "confidence_rule": (
                    "Do not use absent period memory as proof that a setup is clean."
                ),
                "override_rule": (
                    "A trade may still proceed when live book, funding, spread, "
                    "quant, account, research, and risk evidence are strong; in "
                    "that case name the coverage gap and the evidence that overrode it."
                ),
            },
            "jue_wiki_selection_memory_policy": {
                "source": "memory.jue_wiki_selection_memory",
                "applies_to_scope": "binance",
                "freshness_guidance_effect": [
                    "refresh or cross-check selected Wiki pages before size increase",
                    (
                        "record jue_wiki_selection_resolution or "
                        "jue_wiki_freshness_cross_check on affected actions"
                    ),
                    (
                        "use fresh_jue_wiki_context, selection_audit_resolution, "
                        "and live_cross_check as required evidence"
                    ),
                ],
                "confidence_rule": (
                    "Do not treat stale selected Wiki pages as conviction evidence "
                    "until live book, spread, funding, quant, account, and research "
                    "evidence cross-check the memory."
                ),
            },
            "jue_wiki_context_gap_memory_policy": {
                "source": "memory.jue_wiki_context_gap_memory",
                "applies_to_scope": "binance",
                "gap_guidance_effect": [
                    "verify Wiki context availability before high-confidence action",
                    (
                        "record jue_wiki_context_gap on affected actions when "
                        "Wiki remains unavailable"
                    ),
                    "use fresh_jue_wiki_context or live_cross_check as required evidence",
                ],
                "confidence_rule": (
                    "Repeated Wiki context gaps are operational caution signals. "
                    "Do not raise confidence from memory unless current Wiki context "
                    "is fresh or the action records concrete book, spread, funding, "
                    "quant, and live_authority cross-checks."
                ),
            },
            "jue_wiki_action_reference_memory_policy": {
                "source": "memory.jue_wiki_action_reference_memory",
                "applies_to_scope": "binance",
                "reference_guidance_effect": [
                    (
                        "attach jue_wiki_freshness_cross_check or "
                        "jue_wiki_selection_resolution when selected Wiki memory "
                        "influences an action"
                    ),
                    (
                        "if an action does not use Wiki memory, record the "
                        "book/quant/research basis that overrode Wiki memory"
                    ),
                    (
                        "use live_cross_check before allowing high confidence "
                        "without an action-level Wiki reference"
                    ),
                ],
                "confidence_rule": (
                    "Repeated missing Wiki action references reduce audit "
                    "confidence. Do not claim memory-backed conviction unless "
                    "the affected action names the Wiki cross-check, selection "
                    "resolution, or explicit book/quant/research evidence basis."
                ),
            },
            "jue_wiki_usage_contract_policy": {
                "source": "jue_wiki_application.trust_profile",
                "memory_source": "memory.jue_wiki_usage_contract_memory",
                "applies_to_scope": "binance",
                "standalone_trade_authority": False,
                "required_action_metadata": "jue_wiki_usage_contract_resolution",
                "required_cross_checks": [
                    "live_spread",
                    "funding",
                    "liquidation_distance",
                    "account_state",
                    "risk_gate",
                    "book",
                    "quant",
                    "research",
                ],
                "memory_guidance_effect": [
                    (
                        "apply usage-contract reflections as action-level "
                        "evidence requirements, not as standalone trade authority"
                    ),
                    (
                        "when memory requires jue_wiki_usage_contract_resolution, "
                        "every affected action must name the live cross-check that "
                        "confirmed, reduced, or overrode the Wiki memory prior"
                    ),
                ],
                "decision_effect": (
                    "Selected Wiki pages are memory priors, not standalone trade "
                    "authority. When Wiki trust_profile or usage_contract influences "
                    "an action, record how live spread, funding, liquidation distance, "
                    "account state, risk gate, book, quant, or research evidence "
                    "confirmed or overrode that memory."
                ),
            },
            "jue_wiki_effectiveness_policy": (
                "Use Jue Wiki page effectiveness as a trading-design prior. "
                "active Wiki pages may raise confidence only after live book, "
                "funding, spread, account, and risk gates agree. probe Wiki pages "
                "support small probe or waiting-entry designs, not oversized "
                "conviction. degraded Wiki pages are repair/probe evidence: do not "
                "use them as standalone entry support, and if they still influence "
                "a block then name the cross-check, sizing reduction, or repair "
                "resolution."
            ),
            "validation_repair_response_policy": (
                "When validation_repair or jue_wiki_repair_contract is active, Jue must "
                "turn the repair into a concrete trading-design response: smaller "
                "waiting/probe block, updated target/stop geometry, explicit candidate "
                "reject condition, or safety-gate defer. Do not use degraded memory, "
                "recent validation loss, observe_only, or missing repair evidence as a "
                "blanket reason to stop all exploration."
            ),
            "crypto_alpha_policy": (
                "Use crypto_alpha as catalyst memory: recent public events, similar "
                "outcomes, scorecards, lessons, contradictions, and gaps."
            ),
            "crypto_regime_policy": (
                "Use crypto_market_pulse.regime_brief as the market narrative and "
                "regime layer before choosing a lane. It summarizes breadth, major "
                "coin pressure, derivatives notes, external context, lane_bias, and "
                "horizon_bias. A new block should either align with that narrative or "
                "explicitly explain why live quant/book evidence justifies a tactical "
                "exception."
            ),
            "crypto_quant_policy": (
                "Use crypto_quant as the compact directional packet. Compare latest "
                "long_score, short_score, no_trade_score, expected_R, and recent "
                "history trend before creating or resizing blocks."
            ),
            "crypto_pattern_quality_policy": (
                "Use crypto_patterns.qualified_scorecards as empirical entry support. "
                "A pattern must have at least 8 trades, win_rate >= 0.50, "
                "expectancy_r > 0, and profit_factor >= 1.05 before it can raise "
                "conviction or sizing. Treat non-qualified scorecards as diagnostics "
                "or cautionary context, not as positive entry evidence."
            ),
            "crypto_pattern_optimization_policy": (
                "Use crypto_patterns.optimized_strategy_sets as audited price-geometry "
                "priors for stop_pct, target_pct, and holding_bars. They are empirical "
                "optimization outputs, not order commands. A set may raise conviction "
                "only when walk_forward_quality.passed is true or when no walk-forward "
                "payload exists in a legacy external provider. Prefer optimized sets "
                "when out-of-sample trade count, expectancy, profit factor, fresh "
                "crypto_quant, market book, funding, spread, and live_authority agree; "
                "reject or wait when live context or walk-forward evidence contradicts them."
            ),
            "pattern_live_confluence_policy": (
                "Each executable candidate may include calculated.pattern_live_crosscheck. "
                "Treat pattern_live_crosscheck.status='aligned' as permission to consider "
                "the optimized price geometry, not as permission to trade blindly. If it is "
                "'wait', prefer waiting-entry or hold_decision until book freshness, spread, "
                "funding, quant direction, and live_authority improve. If it is "
                "'contradicted', do not create a new block unless override_reason explains "
                "the live contradiction and safety gates still pass."
            ),
            "pattern_performance_policy": (
                "Each executable candidate may include "
                "candidate.pattern_performance_scorecard and "
                "calculated.pattern_performance_multiplier from live block outcomes. "
                "If status='de_risk', treat the optimized pattern as recently weak: "
                "prefer reject, hold_decision, smaller waiting-entry, or clearer live "
                "confluence rather than immediate entry. If status='scale_candidate', "
                "the pattern has positive live evidence, but Jue may press it only when "
                "book freshness, spread, funding, crypto_quant, live_authority, and "
                "account risk also agree. Never use a backtest pattern alone to override "
                "a weak live pattern_performance_scorecard."
            ),
            "performance_window_policy": (
                "recent_performance is the short feedback window used for immediate "
                "gate cadence. performance is the configured feedback window used for "
                "Jue's trading judgment, lane review, pattern performance, and execution "
                "defect awareness. Do not ignore losses or execution defects that appear "
                "in performance simply because the latest few blocks look cleaner."
            ),
            "market_universe_policy": (
                "The configured spot/futures/upbit_spot universes are seeds. Crypto research expands "
                "them with ranked liquid intraday candidates. Create spot blocks only from "
                "market_universe.spot and futures blocks only from market_universe.futures; "
                "create Upbit KRW spot blocks only from market_universe.upbit_spot using "
                "KRW-* symbols such as KRW-BTC. "
                "the server rejects pairs outside the current runtime universe."
            ),
            "multi_block_policy": (
                "Multiple blocks for the same symbol are allowed when the thesis, entry "
                "price, or time horizon differs. Do not treat an existing block as a "
                "standalone ban on creating another block; evaluate whether the new block "
                "has independent rationale and passes the server-side risk gates."
            ),
            "lane_balance_policy": (
                "Review spot:long, futures:long, futures:short, and upbit_spot:long independently every "
                "manager cycle. lane_balance shows recent block concentration, candidate "
                "lane distribution, and performance by market:side. If recent blocks are "
                "concentrated in a dominant lane such as futures:short, another block in "
                "that same dominant lane requires an explicit lane_review explaining why "
                "spot:long, upbit_spot:long, and futures:long were not selected. This is not a hard filter: "
                "do not force a weak long, but do not let abundant futures:short evidence "
                "hide viable long or spot candidates."
            ),
            "spot_exploration_policy": (
                "Spot:long and upbit_spot:long are independent accumulation/exploration lanes, not merely "
                "a weaker version of futures:long. Do not let futures:short losses or "
                "futures lane concentration freeze fresh spot exploration. When venue "
                "live_authority is observe_only or restricted, spot blocks may still be "
                "proposed as small waiting-entry blocks if fresh spot book, quote budget, "
                "candidate confidence, reward/risk, and spot-specific quant evidence pass "
                "the server gates. Prefer mid/long spot accumulation when regime_brief "
                "supports selective risk-on or discount accumulation."
            ),
            "upbit_spot_policy": (
                "Upbit spot is KRW cash trading only. Use market='upbit_spot', side='long', "
                "and KRW-* symbols. It can be selected when Korean fiat liquidity, KRW cash, "
                "or venue diversification makes it better than Binance spot. Do not use "
                "futures-only short or leverage logic for Upbit."
            ),
            "volatile_attack_policy": {
                "role": "small aggressive lane for high-volatility alts",
                "instruction": (
                    "volatile_attack is a separate lane. Use it for explosive altcoin "
                    "setups only when volume expansion, acceptable spread/depth, wick "
                    "risk, funding/open interest, squeeze context, and alpha events are "
                    "audited. Prefer wait_for_price, breakout_confirmed, or "
                    "pullback_reclaim entries over immediate orders. Position size must "
                    "start smaller than normal lanes, stop distance must be wide enough "
                    "for the symbol volatility, and reward/risk must be at least the "
                    "volatile_attack minimum. Reject thin books or tight-stop designs."
                ),
                "lane": "volatile_attack",
                "hard_filters": False,
                "safety_gates_still_override": True,
            },
            "hold_decision_policy": (
                "Always include hold_decision. When no block action is selected, explain "
                "why Jue is waiting, which symbols remain on watch, the next concrete "
                "price or condition triggers, and any data gaps that prevented action. "
                "Jue must think and draft conclusions in English. All user-facing "
                "hold_decision text must then be translated into natural Korean "
                "for the operator: summary, reasons, next_triggers.condition, "
                "next_triggers.reason, data_gaps, and risk_notes. "
                "Use crypto_market_pulse for the market-wide crypto regime and major "
                "asset breadth. Do not report market_pulse missing when "
                "crypto_market_pulse is present; this Binance prompt uses the "
                "crypto-research-derived pulse."
            ),
            "price_design_policy": (
                "Use calculated values as default prices; override only with reason."
            ),
            "adaptive_entry_gate_policy": entry_gate_policy,
            "live_authority_policy": {
                "role": "live outcome authority gate",
                "instruction": (
                    "Use live_authority to calibrate aggression, not as a blanket "
                    "freeze across every lane. Restricted or observe_only means keep "
                    "researching, use smaller or waiting structures, and avoid scaling "
                    "frequency until realized edge improves. For spot:long, evaluate "
                    "the spot lane scorecard and regime separately from futures:short. "
                    "scale_candidate allows more conviction only when crypto quant, "
                    "pattern, risk, validation_gate, and safety gates agree. "
                    "validation_gate is server-enforced: blocked/research_only/error "
                    "prevents new blocks, and validation_probe/validation_normal "
                    "requires waiting-entry structure instead of immediate entry."
                ),
                "probe_mandate": (
                    "When execution_posture is probe_allowed_scale_blocked, Jue "
                    "should actively seek small waiting-entry probe blocks in lanes "
                    "with fresh evidence instead of converting the state into a "
                    "generic hold."
                ),
                "server_enforced_gates": (
                    "blocked/research_only/error validation states and exchange/risk gates"
                ),
                "hard_filters": False,
                "safety_gates_still_override": True,
            },
        },
        "horizon_policy": {
            "short": (
                "Short spot blocks are active trading blocks. They may react to intraday "
                "momentum, quant deterioration, target/stop touches, and catalyst decay."
            ),
            "mid": (
                "Mid spot blocks are swing blocks. They should not be closed only because "
                "of one short-term noisy candle; require thesis deterioration, risk breach, "
                "or better allocation opportunity."
            ),
            "long": (
                "Long spot blocks are position blocks. Do not close long horizon spot blocks "
                "because of short-term noise; prefer update_blocks or hold_decision unless "
                "the long thesis is invalidated."
            ),
            "futures": (
                "Futures blocks are high-risk directional trades. Keep them separate from "
                "spot horizons and require explicit liquidation-distance/risk reasoning. "
                "If horizon is futures, market must be futures; do not send market=spot "
                "with horizon=futures."
            ),
            "volatile_attack": (
                "Volatile attack blocks are small, high-volatility tactical blocks. "
                "Use short/futures horizon semantics for execution, but set lane to "
                "volatile_attack and keep entry conditional unless live evidence is exceptional."
            ),
        },
        "horizon_action_authority": {
            "short": "active_trade",
            "mid": "swing_trade",
            "long": "position_trade",
            "futures": "active_high_risk_trade",
        },
        "language_policy": language_policy,
        "output_language_policy": language_policy,
        "account": account,
        "growth_target": growth_target,
        "growth_governor": growth_governor,
        "growth_unlock": growth_unlock,
        "risk_guard": risk_guard,
        "execution_gate": execution_gate,
        "memory": memory_context,
        "canonical_decision_packet": decision_bundle["canonical_decision_packet"],
        "decision_packet_v2": decision_packet_v2,
        "decision_packet": decision_packet,
        "decision_packet_policy": decision_bundle["decision_packet_policy"],
        "candidate_policy_impacts": candidate_policy_impacts,
        "validation_repair": validation_repair,
        "crypto_market_pulse": crypto_market_pulse,
        "raw_context_refs": raw_context_refs,
        "recent_performance": recent_performance,
        "performance": performance,
        "entry_gate_policy": entry_gate_policy,
        "live_authority": live_authority,
        "lane_balance": lane_balance,
        "candidate_generation": candidate_generation,
        "candidates": candidates,
        "candidate_memory_hint_policy": {
            "required": has_candidate_memory_hints,
            "action_contract": "cite_or_reject_candidate_memory_hint",
            "sources": [
                "symbol_analysis_memory",
                "scoped_local_memory",
                "scoped_translated_memory",
            ],
            "instruction": (
                "For every candidate with memory_hint, either cite the hint in the "
                "created/updated block thesis, risk_note, or metadata, or explain in "
                "hold_decision why live evidence overrides or rejects the hint. "
                "Do not silently ignore candidate memory."
            ),
        },
        "universe": universe,
        "market_universe": market_universe,
        "blocks": blocks,
        "decision_inputs": decision_inputs,
        "output_schema": {
            "adopt_existing_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot|futures|upbit_spot (venue alias accepted)",
                    "side": "long|short",
                    "horizon": (
                        "short|mid|long for spot/upbit_spot; futures only when "
                        "market=futures"
                    ),
                    "lane": (
                        "spot:long|futures:long|futures:short|volatile_attack|"
                        "upbit_spot:long"
                    ),
                    "qty": "existing wallet/position quantity to assign to the block ledger",
                    "entry_price": (
                        "average/current basis price if known; do not send a new entry order"
                    ),
                    "target_price": "required number for rule executor management",
                    "stop_price": "required number for rule executor management",
                    "thesis": "concise adoption thesis",
                    "risk_note": "concise risk note",
                    "adoption_note": (
                        "required string; explain this assigns an existing "
                        "wallet/position to a block without sending a new entry order"
                    ),
                    "calculated_price_plan": "copy from candidate.calculated when available",
                    "pattern_live_crosscheck": (
                        "copy from candidate.calculated.pattern_live_crosscheck when present"
                    ),
                    "jue_price_override": (
                        "boolean, true when Jue changed calculated prices"
                    ),
                    "override_reason": (
                        "required when prices differ from calculated plan"
                    ),
                    "jue_wiki_repair_pressure": (
                        "optional string; how wiki repair pressure affected adoption"
                    ),
                    "jue_wiki_repair_resolution": (
                        "optional string; required action metadata when this adoption uses "
                        "degraded Wiki effectiveness; explain live wallet/book/spread/"
                        "funding/account/risk cross-check and target/stop adjustment"
                    ),
                    "jue_wiki_memory_card_quality": (
                        "optional string; how thin Wiki memory affected adoption"
                    ),
                    "jue_wiki_memory_card_cross_check": (
                        "optional string; live cross-check used before trusting thin Wiki memory"
                    ),
                    **_binance_period_memory_action_metadata_output_schema(
                        "adopt existing block action"
                    ),
                }
            ],
            "create_blocks": [
                {
                    "symbol": (
                        "BTCUSDT; must match one of the visible candidates for the "
                        "same market"
                    ),
                    "market": "spot|futures|upbit_spot (venue alias accepted)",
                    "side": "long|short",
                    "horizon": (
                        "short|mid|long for spot/upbit_spot; futures only when "
                        "market=futures"
                    ),
                    "lane": (
                        "optional; use volatile_attack for the high-volatility small "
                        "attack lane"
                    ),
                    "qty": (
                        "number, or quote_budget_usdt with entry_price_usdt; for "
                        "upbit_spot use quote_budget_krw/quote_budget with KRW entry_price"
                    ),
                    "entry_price": "required number; entry_price_usdt alias accepted",
                    "target_price": "required number; target_price_usdt alias accepted",
                    "stop_price": "required number; stop_price_usdt alias accepted",
                    "leverage": "futures integer <= max_futures_leverage",
                    "margin_type": "isolated for futures",
                    "liquidation_price": (
                        "required for active futures live entry/open blocks; "
                        "optional for proposed wait_for_price futures probes, but "
                        "must be supplied or refreshed before the trigger can open"
                    ),
                    "calculated_price_plan": "copy from candidate.calculated",
                    "pattern_live_crosscheck": (
                        "copy from candidate.calculated.pattern_live_crosscheck when present"
                    ),
                    "jue_price_override": (
                        "boolean, true when Jue changed calculated prices"
                    ),
                    "override_reason": (
                        "required when prices differ from calculated plan"
                    ),
                    "jue_wiki_repair_pressure": (
                        "optional string; how wiki repair pressure or omitted repair queue "
                        "affected confidence, sizing, horizon, or evidence requirements"
                    ),
                    "jue_wiki_repair_resolution": (
                        "optional string; required action metadata when this action uses "
                        "degraded Wiki effectiveness; explain the live book/spread/"
                        "funding/account/risk cross-check and sizing or trigger "
                        "adjustment, because response-only repair notes do not resolve "
                        "degraded Wiki effectiveness for actions"
                    ),
                    "jue_wiki_memory_card_quality": (
                        "optional string; how thin Wiki memory card quality affected "
                        "confidence, sizing, horizon, or evidence requirements"
                    ),
                    "jue_wiki_memory_card_cross_check": (
                        "optional string; live book/funding/quant/research cross-check used "
                        "before trusting thin Wiki memory cards"
                    ),
                    **_binance_period_memory_action_metadata_output_schema(
                        "create action"
                    ),
                }
            ],
            "update_blocks": [
                {
                    "block_id": "string",
                    "jue_wiki_repair_pressure": (
                        "optional string; how active wiki repair pressure affected update"
                    ),
                    "jue_wiki_repair_resolution": (
                        "optional string; required action metadata when this update uses "
                        "degraded Wiki effectiveness; explain live book/spread/funding/"
                        "account/risk cross-check and sizing or trigger adjustment"
                    ),
                    "jue_wiki_memory_card_quality": (
                        "optional string; how thin Wiki memory affected the update"
                    ),
                    "jue_wiki_memory_card_cross_check": (
                        "optional string; live cross-check used before trusting thin Wiki memory"
                    ),
                    **_binance_period_memory_action_metadata_output_schema(
                        "update action"
                    ),
                }
            ],
            "close_blocks": [
                {
                    "block_id": "string",
                    "jue_wiki_repair_pressure": (
                        "optional string; how active wiki repair pressure affected close"
                    ),
                    "jue_wiki_repair_resolution": (
                        "optional string; required action metadata when this close uses "
                        "degraded Wiki effectiveness; explain live book/spread/funding/"
                        "account/risk cross-check and exit/risk adjustment"
                    ),
                    "jue_wiki_memory_card_quality": (
                        "optional string; how thin Wiki memory affected the close"
                    ),
                    "jue_wiki_memory_card_cross_check": (
                        "optional string; live cross-check used before trusting thin Wiki memory"
                    ),
                    **_binance_period_memory_action_metadata_output_schema(
                        "close action"
                    ),
                }
            ],
            "pause_blocks": [
                {
                    "block_id": "string",
                    "jue_wiki_repair_pressure": (
                        "optional string; how active wiki repair pressure affected pause"
                    ),
                    "jue_wiki_repair_resolution": (
                        "optional string; required action metadata when this pause uses "
                        "degraded Wiki effectiveness; explain live book/spread/funding/"
                        "account/risk cross-check and pause trigger"
                    ),
                    "jue_wiki_memory_card_quality": (
                        "optional string; how thin Wiki memory affected the pause"
                    ),
                    "jue_wiki_memory_card_cross_check": (
                        "optional string; live cross-check used before trusting thin Wiki memory"
                    ),
                    **_binance_period_memory_action_metadata_output_schema(
                        "pause action"
                    ),
                }
            ],
            "lane_review": {
                "required": (
                    "mandatory top-level object on every response, including "
                    "no actions / hold-only cycles; missing lane_review is an "
                    "invalid manager response"
                ),
                "dominant_lane": (
                    "spot:long|futures:long|futures:short|upbit_spot:long|none"
                ),
                "lanes_reviewed": list(manager_lanes),
                "selected_lanes": ["market:side lanes used by create/update/close actions"],
                "non_selected_lane_reasons": {
                    "spot:long": "why spot long was not chosen or was only watched",
                    "futures:long": (
                        "why futures long was not chosen or was only watched"
                    ),
                    "futures:short": "why futures short was not chosen or was chosen",
                    "upbit_spot:long": (
                        "why Upbit KRW spot was not chosen or was only watched"
                    ),
                },
                "concentration_note": (
                    "required when lane_balance.recent_blocks.requires_review is true"
                ),
                "exploration_watch": [
                    "specific non-dominant symbols/triggers Jue will keep watching"
                ],
            },
            "hold_decision": {
                "summary": "short plain-language reason for action or no-action",
                "reasons": ["why no new block/update/close was selected"],
                "watch_symbols": ["symbols still being monitored"],
                "next_triggers": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot|futures|upbit_spot",
                        "condition": "price/flow/regime condition to watch",
                        "price": "optional numeric trigger",
                        "reason": "why this trigger matters",
                    }
                ],
                "data_gaps": ["missing evidence that would change the decision"],
                "risk_notes": ["risks behind waiting or acting"],
            },
            "validation_repair_resolution": {
                "required": (
                    "mandatory whenever validation_repair has repair_item_count, "
                    "constraint_count, jue_wiki_repair_contract has top_priorities "
                    "or action_batches, "
                    "jue_wiki_validation_repair_contract requires resolution, or "
                    "jue_wiki_contract_feedback_gap is present, or "
                    "jue_wiki_memory_card_quality has an active action_plan"
                ),
                "resolved_candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot|futures|upbit_spot",
                        "resolution": (
                            "probe_waiting_block|updated_price_geometry|"
                            "candidate_rejected|safety_gate_defer"
                        ),
                        "next_trigger": "price/depth/funding/regime condition",
                        "evidence_gap": "precise missing evidence, if rejected",
                        "memory_contract": (
                            "required when validation_repair."
                            "memory_contract_resolution_required=true"
                        ),
                        "memory_contract_error": (
                            "required when validation_repair."
                            "memory_contract_resolution_required=true"
                        ),
                        "memory_contract_resolution": (
                            "required when validation_repair."
                            "memory_contract_resolution_required=true; cite_memory_and_apply|"
                            "reject_memory_with_reason|wait_until_memory_refresh|"
                            "safety_gate_defer_with_contract_note plus concise evidence"
                        ),
                    }
                ],
                "blanket_hold_allowed": False,
            },
        },
        "native_thread_mode": "daily",
        "native_thread_key": "binance:block_manager:{date}",
    }
    payload["native_output_schema"] = payload["output_schema"]
    return payload


MANAGER_PROMPT_STORAGE_EMERGENCY_KEYS = (
    "task",
    "critical_response_contract",
    "policy",
    "horizon_policy",
    "horizon_action_authority",
    "language_policy",
    "output_language_policy",
    "account",
    "growth_target",
    "growth_governor",
    "growth_unlock",
    "risk_guard",
    "execution_gate",
    "memory",
    "decision_inputs",
    "decision_packet_v2",
    "decision_packet",
    "candidate_policy_impacts",
    "validation_repair",
    "jue_wiki",
    "crypto_market_pulse",
    "raw_context_refs",
    "performance",
    "entry_gate_policy",
    "live_authority",
    "lane_balance",
    "candidate_generation",
    "candidates",
    "universe",
    "market_universe",
    "blocks",
    "output_schema",
    "native_output_schema",
    "jue_workflow",
    "proactive_decision_pressure",
    "jue_wiki_application",
    "jue_wiki_decision_adjustments",
    "jue_wiki_requested_symbol_coverage",
    "jue_wiki_memory_card_quality",
    "jue_wiki_repair_contract",
    "jue_wiki_action_pressure_contract",
    "latency_guard",
    "native_thread_mode",
    "prompt_compaction",
    "prompt_budget",
    "diagnostics",
)


def _compact_prompt_storage_section(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    string_limit: int = 180,
    list_limit: int = 8,
) -> Any:
    if isinstance(value, (list, tuple)):
        return {
            "item_count": len(value),
            "items": compact_value(
                list(value),
                string_limit=string_limit,
                list_limit=list_limit,
            ),
        }
    return compact_value(
        value,
        string_limit=string_limit,
        list_limit=list_limit,
    )


def _compact_jue_wiki_trust_profile_for_storage(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    text_limit = max(min(int(string_limit), 240), 48)
    out: dict[str, Any] = {}
    for key in (
        "trust_level",
        "authority",
        "decision_use",
        "posture",
        "policy_reason",
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            out[key] = _clean_text(raw, limit=text_limit)
    usage_contract = (
        value.get("usage_contract")
        if isinstance(value.get("usage_contract"), dict)
        else {}
    )
    if usage_contract:
        contract: dict[str, Any] = {}
        for key in ("decision_role", "effectiveness_status", "risk_posture"):
            raw = usage_contract.get(key)
            if raw not in (None, "", [], {}):
                contract[key] = _clean_text(raw, limit=text_limit)
        for key in (
            "requires_live_cross_check",
            "standalone_trade_authority",
            "hard_blocker",
        ):
            raw = usage_contract.get(key)
            if raw not in (None, "", [], {}):
                contract[key] = bool(raw)
        checks = [
            _clean_text(item, limit=80)
            for item in _as_list(
                usage_contract.get("required_cross_checks")
            )[: max(int(list_limit), 1)]
            if str(item or "").strip()
        ]
        if checks:
            contract["required_cross_checks"] = checks
        if contract:
            out["usage_contract"] = contract
    return {key: child for key, child in out.items() if child not in (None, "", [], {})}


def compact_jue_wiki_application_for_storage(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    string_limit: int = 180,
    list_limit: int = 8,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = _compact_prompt_storage_section(
        value,
        compact_value=compact_value,
        string_limit=string_limit,
        list_limit=list_limit,
    )
    if not isinstance(compact, dict):
        return {}
    out = {
        key: child
        for key, child in compact.items()
        if key not in {"trust_profile", "raw_debug", "raw_blob", "debug"}
    }
    trust_profile = _compact_jue_wiki_trust_profile_for_storage(
        value.get("trust_profile"),
        string_limit=string_limit,
        list_limit=list_limit,
    )
    if trust_profile:
        out["trust_profile"] = trust_profile
    return {key: child for key, child in out.items() if child not in (None, "", [], {})}


def _compact_diagnostic_top_blockers(
    value: Any,
    *,
    list_limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _as_list(value)[: max(int(list_limit), 1)]:
        if not isinstance(row, dict):
            continue
        tag = _clean_text(row.get("tag"), limit=120)
        if not tag:
            continue
        weight = (
            _safe_int(row.get("weight"))
            or _safe_int(row.get("count"))
            or _safe_int(row.get("score"))
            or 1
        )
        rows.append({"tag": tag, "weight": max(weight, 1)})
    return rows


def compact_manager_diagnostics_for_storage(
    value: Any,
    *,
    compact_value: Callable[..., Any] | None = None,
    string_limit: int = 180,
    list_limit: int = 8,
) -> dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    if not diagnostics:
        return {}
    compact_fn = compact_value or compact_prompt_value
    out: dict[str, Any] = {}
    for key in (
        "version",
        "action_count",
        "jue_wiki_repair_priority_count",
        "jue_wiki_repair_action_batch_count",
        "jue_wiki_requested_symbol_coverage_status",
        "jue_wiki_attention_status",
        "jue_wiki_attention_resolution_status",
        "jue_wiki_memory_card_quality_status",
        "jue_wiki_selection_guidance_status",
        "jue_wiki_selection_guidance_resolution_status",
        "jue_wiki_context_gap_status",
        "jue_wiki_context_gap_resolution_status",
        "jue_wiki_action_reference_memory_status",
        "jue_wiki_action_reference_memory_resolution_status",
        "jue_wiki_action_reference_status",
        "jue_wiki_action_reference_count",
        "jue_wiki_action_reference_ratio",
        "jue_wiki_action_reference_unscoped_page_omitted_count",
        "jue_wiki_usage_contract_status",
        "jue_wiki_usage_contract_resolution_count",
        "jue_wiki_usage_contract_resolution_ratio",
        "jue_wiki_action_reference_recovery_status",
        "jue_wiki_action_reference_recovery_memory_scope",
        "jue_wiki_action_reference_recovery_open_gap_count",
        "jue_wiki_action_reference_recovery_resolved_count",
        "jue_wiki_action_reference_recovery_total_count",
        "jue_wiki_action_reference_recovery_ratio",
        "jue_wiki_action_reference_recovery_latest_resolution_status",
        "jue_wiki_action_reference_recovery_latest_status",
        "degraded_jue_wiki_effectiveness_count",
        "degraded_jue_wiki_effectiveness_resolution_status",
        "candidate_memory_hint_status",
        "candidate_memory_hint_count",
        "candidate_memory_hint_resolved_count",
        "candidate_memory_hint_unresolved_count",
        "memory_contract_status",
        "memory_contract_count",
        "memory_contract_resolved_count",
        "memory_contract_unresolved_count",
        "memory_contract_action_resolved_count",
        "memory_contract_hold_resolved_count",
        "memory_contract_response_resolved_count",
    ):
        if diagnostics.get(key) not in ({}, [], "", None):
            out[key] = diagnostics.get(key)
    for key in (
        "blocker_tags",
        "top_blockers",
        "jue_wiki_missing_summary_symbols",
        "jue_wiki_prompt_omitted_symbols",
        "jue_wiki_action_reference_unscoped_page_ids",
        "jue_wiki_action_reference_missing_actions",
        "jue_wiki_attention_must_address",
        "jue_wiki_weak_memory_card_symbols",
        "degraded_jue_wiki_effectiveness_page_ids",
        "candidate_memory_hint_missing_symbols",
        "memory_contract_missing_symbols",
        "memory_contract_missing_contracts",
        "memory_contract_missing_errors",
        "memory_contract_resolution_modes",
        "memory_contract_rows",
    ):
        if diagnostics.get(key) not in ({}, [], "", None):
            if key == "top_blockers":
                out[key] = _compact_diagnostic_top_blockers(
                    diagnostics.get(key),
                    list_limit=max(int(list_limit), 1),
                )
            else:
                out[key] = compact_fn(
                    diagnostics.get(key),
                    string_limit=max(int(string_limit), 48),
                    list_limit=max(int(list_limit), 1),
                )
    return out


def _compact_prompt_storage_sequence_section(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    string_limit: int,
    list_limit: int,
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
    lane_diverse: bool = False,
) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        rows = list(value.get("items") or [])
        item_count = _safe_int(value.get("item_count")) or len(rows)
    elif isinstance(value, (list, tuple)):
        rows = list(value)
        item_count = len(rows)
    else:
        rows = []
        item_count = 0
    bounded_limit = max(int(list_limit), 0)
    rows = select_manager_candidate_rows_for_compaction(
        rows,
        limit=bounded_limit,
        priority_candidate_keys=priority_candidate_keys or set(),
        lane_diverse=lane_diverse,
    )
    item_list_limit = bounded_limit if lane_diverse else max(min(bounded_limit, 4), 1)
    return {
        "item_count": item_count,
        "items": compact_value(
            rows,
            string_limit=string_limit,
            list_limit=item_list_limit,
        ),
    }


def _runtime_prompt_sequence_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return list(value.get("items") or [])
    return []


def _compact_prompt_storage_mapping_section(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    string_limit: int,
    list_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"item_count": 0, "items": []}
    bounded_limit = max(int(list_limit), 0)
    items: list[dict[str, Any]] = []
    for key, child in list(value.items())[:bounded_limit]:
        compact_child = compact_value(
            child,
            string_limit=string_limit,
            list_limit=max(min(bounded_limit, 4), 1),
        )
        if isinstance(compact_child, dict):
            items.append({"key": str(key), **compact_child})
        else:
            items.append({"key": str(key), "value": compact_child})
    return {"item_count": len(value), "items": items}


def _compact_manager_prompt_emergency_section(
    key: str,
    value: Any,
    *,
    compact_value: Callable[..., Any],
    string_limit: int,
    list_limit: int,
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
) -> Any:
    if key in {"candidates", "blocks", "universe"}:
        return _compact_prompt_storage_sequence_section(
            value,
            compact_value=compact_value,
            string_limit=string_limit,
            list_limit=list_limit,
            priority_candidate_keys=priority_candidate_keys
            if key == "candidates"
            else None,
            lane_diverse=key == "candidates",
        )
    if key == "market_universe":
        if isinstance(value, dict):
            return _compact_prompt_storage_mapping_section(
                value,
                compact_value=compact_value,
                string_limit=string_limit,
                list_limit=list_limit,
            )
        return _compact_prompt_storage_sequence_section(
            value,
            compact_value=compact_value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
    if key in {"output_schema", "native_output_schema"}:
        return compact_manager_output_schema_for_prompt(value)
    if key == "memory":
        return compact_binance_memory_for_prompt(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
    if key == "proactive_decision_pressure":
        return compact_proactive_decision_pressure_for_latency(value)
    if key == "validation_repair":
        return compact_validation_repair_for_storage(
            value,
            repair_metadata=validation_repair_action_metadata,
        )
    if key == "jue_wiki_requested_symbol_coverage":
        return compact_requested_symbol_coverage_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if key == "jue_wiki_repair_contract":
        return compact_jue_wiki_repair_contract_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if key == "jue_wiki_application":
        return compact_jue_wiki_application_for_storage(
            value,
            compact_value=compact_value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
    return _compact_prompt_storage_section(
        value,
        compact_value=compact_value,
        string_limit=string_limit,
        list_limit=list_limit,
    )


def _binance_memory_has_manager_contract_recovery(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    memory = value.get("memory") if isinstance(value.get("memory"), dict) else {}
    recovery = (
        memory.get("validation_recovery_summary")
        if isinstance(memory.get("validation_recovery_summary"), dict)
        else {}
    )
    return bool(_as_list(recovery.get("manager_contract_recovered")))


def _bounded_wiki_identity(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value[:WIKI_GATE_IDENTITY_MAX_CHARS]


def compact_wiki_gate_storage_contracts(value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    gate = value.get("jue_wiki_decision_gate")
    if isinstance(gate, dict):
        compact_gate: dict[str, Any] = {}
        for key in ("allow_new_risk", "allow_exit_actions"):
            if type(gate.get(key)) is bool:
                compact_gate[key] = gate[key]
        for key in ("reason", "snapshot_id"):
            if gate.get(key) not in (None, ""):
                compact_gate[key] = _bounded_wiki_identity(gate.get(key))
        for key, limit in (("read_mode", 16), ("version", 40)):
            if gate.get(key) not in (None, ""):
                compact_gate[key] = _clean_text(gate.get(key), limit=limit)
        out["jue_wiki_decision_gate"] = compact_gate
    policy = value.get("jue_wiki_decision_gate_policy")
    if isinstance(policy, dict):
        out["jue_wiki_decision_gate_policy"] = {
            "instruction": _clean_text(policy.get("instruction"), limit=120)
        }
    strip_audit = value.get("jue_wiki_raw_rag_strip_audit")
    if isinstance(strip_audit, dict):
        out["jue_wiki_raw_rag_strip_audit"] = {
            "read_mode": _clean_text(strip_audit.get("read_mode"), limit=16),
            "snapshot_id": _bounded_wiki_identity(strip_audit.get("snapshot_id")),
            "removed_path_count": _safe_int(strip_audit.get("removed_path_count")),
            "removed_paths": [
                _clean_text(path, limit=48)
                for path in _as_list(strip_audit.get("removed_paths"))[:2]
            ],
        }
    suppression = value.get("jue_wiki_suppression_audit")
    if isinstance(suppression, dict):
        rows: list[dict[str, Any]] = []
        for row in _as_list(suppression.get("suppressed_actions"))[:1]:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    key: _clean_text(row.get(key), limit=80)
                    for key in (
                        "action_kind",
                        "symbol",
                        "block_id",
                    )
                    if row.get(key) not in (None, "")
                }
            )
        out["jue_wiki_suppression_audit"] = {
            "venue": _clean_text(suppression.get("venue"), limit=16),
            "snapshot_id": _bounded_wiki_identity(suppression.get("snapshot_id")),
            "read_mode": _clean_text(suppression.get("read_mode"), limit=16),
            "reason": _bounded_wiki_identity(suppression.get("reason")),
            "original_action_count": _safe_int(
                suppression.get("original_action_count")
            ),
            "filtered_action_count": _safe_int(
                suppression.get("filtered_action_count")
            ),
            "suppressed_new_risk_count": _safe_int(
                suppression.get("suppressed_new_risk_count")
            ),
            "suppressed_actions": rows,
        }
    return out


def preserve_wiki_gate_storage_contracts(
    compact: dict[str, Any],
    original: dict[str, Any],
) -> None:
    compact.update(compact_wiki_gate_storage_contracts(original))


def _fit_binance_manager_prompt_emergency_payload(
    payload: dict[str, Any],
    *,
    storage_limit: int,
    prioritize_memory_recovery: bool,
) -> dict[str, Any]:
    dropped_keys: list[str] = []

    def drop_key(key: str) -> None:
        if key not in payload:
            return
        payload.pop(key, None)
        if key not in dropped_keys:
            dropped_keys.append(key)

    def annotate() -> None:
        meta = (
            payload.get("_storage_compaction")
            if isinstance(payload.get("_storage_compaction"), dict)
            else {}
        )
        if not isinstance(meta, dict):
            return
        if prioritize_memory_recovery:
            meta["priority_reason"] = "manager_contract_recovery"
        if dropped_keys:
            drop_sample: list[str] = []
            for key in (
                "output_schema",
                "native_output_schema",
                "candidates",
                "account",
            ):
                if key in dropped_keys and key not in drop_sample:
                    drop_sample.append(key)
            for key in dropped_keys:
                if key not in drop_sample:
                    drop_sample.append(key)
                if len(drop_sample) >= 8:
                    break
            meta["dropped_keys"] = drop_sample[:8]
            meta["dropped_key_count"] = len(dropped_keys)
        payload["_storage_compaction"] = meta

    if prompt_chars(payload) <= storage_limit:
        return payload
    for key in list(payload.keys()):
        if key == "_storage_compaction":
            continue
        if payload.get(key) in ({}, [], None):
            drop_key(key)
    annotate()
    if prompt_chars(payload) <= storage_limit:
        return payload
    if payload.get("native_output_schema") == payload.get("output_schema"):
        drop_key("native_output_schema")
    annotate()
    if prompt_chars(payload) <= storage_limit:
        return payload
    for key in (
        "output_schema",
        "native_output_schema",
        "jue_wiki",
        "market_universe",
        "crypto_market_pulse",
        "candidate_policy_impacts",
        "performance",
        "candidate_generation",
        "lane_balance",
        "live_authority",
        "validation_repair",
        "proactive_decision_pressure",
        "execution_gate",
        "growth_unlock",
        "growth_governor",
        "entry_gate_policy",
        "growth_target",
        "risk_guard",
        "critical_response_contract",
    ):
        drop_key(key)
        annotate()
        if prompt_chars(payload) <= storage_limit:
            return payload
    tail_priority = (
        ("candidates", "jue_workflow", "prompt_budget", "account")
        if prioritize_memory_recovery
        else ("memory", "jue_workflow", "prompt_budget", "account")
    )
    for key in tail_priority:
        drop_key(key)
        annotate()
        if prompt_chars(payload) <= storage_limit:
            return payload
    annotate()
    return payload


def manager_prompt_storage_emergency_payload(
    value: dict[str, Any],
    *,
    limit: int,
    label: str,
    original_chars: int,
    retained_keys: list[str],
    compact_value: Callable[..., Any],
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
) -> dict[str, Any]:
    priority_candidate_keys = _manager_prompt_priority_candidate_keys(
        value,
        priority_candidate_keys,
    )
    manager_output_schema = compact_manager_output_schema_for_prompt(
        value.get("native_output_schema") or value.get("output_schema") or {}
    )
    compact: dict[str, Any] = {
        "_storage_compaction": manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=limit,
            retained_keys=retained_keys,
            emergency=True,
        )
    }
    section_limits: dict[str, tuple[int, int]] = {
        "account": (160, 8),
        "risk_guard": (180, 8),
        "growth_target": (160, 8),
        "critical_response_contract": (180, 10),
        "candidates": (120, 8),
        "blocks": (140, 6),
        "memory": (140, 6),
        "performance": (160, 12),
        "entry_gate_policy": (180, 20),
        "crypto_market_pulse": (180, 8),
        "decision_packet_v2": (140, 10),
        "decision_packet": (140, 10),
        "jue_wiki": (180, 4),
        "output_schema": (120, 6),
        "native_output_schema": (120, 6),
        "jue_workflow": (180, 10),
        "market_universe": (120, 6),
        "universe": (100, 8),
        "prompt_budget": (180, 8),
        "growth_governor": (180, 8),
        "growth_unlock": (180, 8),
        "execution_gate": (180, 8),
        "proactive_decision_pressure": (180, 8),
        "validation_repair": (180, 8),
        "jue_wiki_application": (180, 6),
        "jue_wiki_requested_symbol_coverage": (180, 8),
        "jue_wiki_repair_contract": (180, 8),
        "jue_wiki_action_pressure_contract": (180, 8),
        "latency_guard": (160, 6),
        "live_authority": (160, 8),
        "lane_balance": (160, 8),
        "candidate_generation": (160, 8),
        "diagnostics": (180, 8),
    }
    for key in MANAGER_PROMPT_STORAGE_EMERGENCY_KEYS:
        if key not in value:
            continue
        if key == "diagnostics":
            compact[key] = compact_manager_diagnostics_for_storage(
                value.get(key) or {},
                compact_value=compact_value,
                string_limit=180,
                list_limit=8,
            )
            continue
        string_limit, list_limit = section_limits.get(key, (140, 6))
        compact[key] = _compact_manager_prompt_emergency_section(
            key,
            value[key],
            compact_value=compact_value,
            string_limit=string_limit,
            list_limit=list_limit,
            priority_candidate_keys=priority_candidate_keys,
        )
    compact["output_schema"] = manager_output_schema
    compact["native_output_schema"] = manager_output_schema
    preserve_wiki_gate_storage_contracts(compact, value)
    if prompt_chars(compact) <= limit:
        return compact
    prioritize_memory_recovery = _binance_memory_has_manager_contract_recovery(value)
    emergency_payload = {
        "_storage_compaction": manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=limit,
            retained_keys=retained_keys,
            emergency=True,
        ),
        "prompt_budget": _compact_prompt_storage_section(
            value.get("prompt_budget") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=4,
        ),
        "critical_response_contract": _compact_prompt_storage_section(
            value.get("critical_response_contract") or {},
            compact_value=compact_value,
            string_limit=160,
            list_limit=8,
        ),
        "account": _compact_prompt_storage_section(
            value.get("account") or {},
            compact_value=compact_value,
            string_limit=140,
            list_limit=6,
        ),
        "risk_guard": _compact_prompt_storage_section(
            value.get("risk_guard") or {},
            compact_value=compact_value,
            string_limit=140,
            list_limit=6,
        ),
        "growth_target": _compact_prompt_storage_section(
            value.get("growth_target") or {},
            compact_value=compact_value,
            string_limit=140,
            list_limit=6,
        ),
        "memory": compact_binance_memory_for_prompt(
            value.get("memory") or {},
            string_limit=140,
            list_limit=4,
        ),
        "output_schema": manager_output_schema,
        "native_output_schema": manager_output_schema,
        "entry_gate_policy": _compact_prompt_storage_section(
            value.get("entry_gate_policy") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=8,
        ),
        "growth_governor": _compact_prompt_storage_section(
            value.get("growth_governor") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=6,
        ),
        "growth_unlock": _compact_prompt_storage_section(
            value.get("growth_unlock") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=6,
        ),
        "execution_gate": _compact_prompt_storage_section(
            value.get("execution_gate") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=6,
        ),
        "proactive_decision_pressure": compact_proactive_decision_pressure_for_latency(
            value.get("proactive_decision_pressure")
        ),
        "validation_repair": compact_validation_repair_for_storage(
            value.get("validation_repair") or {},
            repair_metadata=validation_repair_action_metadata,
        ),
        "live_authority": _compact_prompt_storage_section(
            value.get("live_authority") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=6,
        ),
        "jue_wiki": compact_jue_wiki_for_prompt(
            value.get("jue_wiki") or {},
            list_limit=2,
            string_limit=140,
        ),
        "lane_balance": _compact_prompt_storage_section(
            value.get("lane_balance") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=6,
        ),
        "candidate_generation": _compact_prompt_storage_section(
            value.get("candidate_generation") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=6,
        ),
        "performance": _compact_prompt_storage_section(
            value.get("performance") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=6,
        ),
        "crypto_market_pulse": _compact_prompt_storage_section(
            value.get("crypto_market_pulse") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=4,
        ),
        "candidates": _compact_prompt_storage_sequence_section(
            value.get("candidates") or [],
            compact_value=compact_value,
            string_limit=80,
            list_limit=3,
            priority_candidate_keys=priority_candidate_keys,
            lane_diverse=True,
        ),
        "candidate_policy_impacts": compact_candidate_policy_impacts_for_latency(
            value.get("candidate_policy_impacts"),
            priority_symbols=_prompt_priority_symbols(value, limit=8),
            symbol_limit=8,
        ),
        "market_universe": _compact_manager_prompt_emergency_section(
            "market_universe",
            value.get("market_universe") or {},
            compact_value=compact_value,
            string_limit=80,
            list_limit=3,
        ),
        "jue_workflow": _compact_manager_jue_workflow_prompt(
            value.get("jue_workflow") or {},
            string_limit=120,
            list_limit=4,
        ),
        "diagnostics": compact_manager_diagnostics_for_storage(
            value.get("diagnostics") or {},
            compact_value=compact_value,
            string_limit=140,
            list_limit=6,
        ),
    }
    if "jue_wiki_application" in value:
        emergency_payload["jue_wiki_application"] = compact_jue_wiki_application_for_storage(
            value.get("jue_wiki_application") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=4,
        )
    if "jue_wiki_decision_adjustments" in value:
        emergency_payload["jue_wiki_decision_adjustments"] = (
            _compact_prompt_storage_section(
                value.get("jue_wiki_decision_adjustments") or {},
                compact_value=compact_value,
                string_limit=140,
                list_limit=4,
            )
        )
    if "jue_wiki_requested_symbol_coverage" in value:
        emergency_payload["jue_wiki_requested_symbol_coverage"] = (
            _compact_prompt_storage_section(
                value.get("jue_wiki_requested_symbol_coverage") or {},
                compact_value=compact_value,
                string_limit=140,
                list_limit=4,
            )
        )
    if "jue_wiki_memory_card_quality" in value:
        emergency_payload["jue_wiki_memory_card_quality"] = (
            compact_jue_wiki_memory_card_quality_prompt(
                value.get("jue_wiki_memory_card_quality") or {},
                string_limit=140,
                list_limit=4,
            )
        )
    if "jue_wiki_repair_contract" in value:
        emergency_payload["jue_wiki_repair_contract"] = (
            compact_jue_wiki_repair_contract_prompt(
                value.get("jue_wiki_repair_contract") or {},
                string_limit=140,
                list_limit=4,
            )
        )
    if "latency_guard" in value:
        emergency_payload["latency_guard"] = _compact_prompt_storage_section(
            value.get("latency_guard") or {},
            compact_value=compact_value,
            string_limit=120,
            list_limit=4,
        )
    if value.get("native_thread_mode") not in (None, ""):
        emergency_payload["native_thread_mode"] = str(value.get("native_thread_mode"))
    if value.get("native_thread_key") not in (None, ""):
        emergency_payload["native_thread_key"] = str(value.get("native_thread_key"))
    preserve_wiki_gate_storage_contracts(emergency_payload, value)
    if not prioritize_memory_recovery:
        return emergency_payload
    return _fit_binance_manager_prompt_emergency_payload(
        emergency_payload,
        storage_limit=limit,
        prioritize_memory_recovery=prioritize_memory_recovery,
    )


def compact_manager_applied_item_for_storage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"value": _clean_text(value, limit=120)}
    out: dict[str, Any] = {}
    for key in (
        "status",
        "reason",
        "block_id",
        "symbol",
        "market",
        "side",
        "lane",
        "horizon",
        "entry_style",
        "entry_trigger_operator",
        "source_id",
    ):
        if key in value:
            out[key] = _clean_text(value.get(key), limit=120)
    for key in (
        "qty",
        "qty_initial",
        "quote_budget_usdt",
        "entry_price",
        "entry_trigger_price",
        "target_price",
        "stop_price",
        "confidence",
        "score",
        "reward_risk",
    ):
        if key in value:
            out[key] = value.get(key)
    for key in ("reasons", "risks", "data_gaps", "next_actions"):
        raw = value.get(key)
        if isinstance(raw, list):
            out[key] = [_clean_text(item, limit=120) for item in raw[:3]]
            if len(raw) > 3:
                out[f"{key}_omitted_count"] = len(raw) - 3
    if isinstance(value.get("metadata"), dict):
        metadata = value["metadata"]
        out["metadata"] = {}
        for key in (
            "block_color",
            "entry_quality",
            "pattern_key",
            "jue_wiki_repair_pressure",
            "jue_wiki_repair_resolution",
            "jue_wiki_memory_card_quality",
            "jue_wiki_memory_card_cross_check",
            "jue_wiki_reference_basis",
            "jue_wiki_usage_contract_resolution",
        ):
            if key not in metadata:
                continue
            raw = metadata.get(key)
            if raw in ({}, [], "", None):
                continue
            out["metadata"][key] = (
                compact_prompt_value(raw, string_limit=100, list_limit=3)
                if isinstance(raw, (dict, list))
                else _clean_text(raw, limit=120)
            )
        if "validation_repair" in metadata:
            out["metadata"]["validation_repair_present"] = True
        if "calculated_price_plan" in metadata:
            out["metadata"]["calculated_price_plan_present"] = True
    return out


def compact_manager_applied_for_storage(
    value: Any,
    *,
    compact_value: Callable[..., Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, raw in value.items():
        key_str = str(key)
        if isinstance(raw, list):
            raw_chars = prompt_chars(raw)
            if len(raw) <= 3 and raw_chars <= 1_200:
                out[key_str] = compact_value(raw, string_limit=160, list_limit=3)
                continue
            out[key_str] = {
                "item_count": len(raw),
                "items": [
                    compact_manager_applied_item_for_storage(item)
                    for item in raw[:2]
                ],
                "omitted_item_count": max(len(raw) - 2, 0),
            }
            continue
        if isinstance(raw, dict):
            if isinstance(raw.get("items"), list):
                items = raw.get("items") or []
                out[key_str] = {
                    "item_count": int(raw.get("item_count") or len(items)),
                    "items": [
                        compact_manager_applied_item_for_storage(item)
                        for item in items[:2]
                    ],
                    "omitted_item_count": max(
                        int(raw.get("omitted_item_count") or 0),
                        max(len(items) - 2, 0),
                    ),
                }
                continue
            out[key_str] = compact_value(raw, string_limit=120, list_limit=3)
            continue
        out[key_str] = _clean_text(raw, limit=120)
    return out


def compact_manager_storage_payload(
    value: dict[str, Any],
    *,
    limit: int,
    label: str,
    compact_value: Callable[..., Any] | None = None,
    clean_text: Callable[[Any, int], str] | None = None,
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact_fn = compact_value or compact_prompt_value
    clean_text_fn = clean_text or (lambda raw, text_limit: _clean_text(raw, limit=text_limit))
    storage_limit = max(int(limit), 1000)
    if label == "binance_manager_prompt":
        estimated_chars = prompt_chars_capped(value, cap=storage_limit)
        if estimated_chars > storage_limit:
            return manager_prompt_storage_emergency_payload(
                value,
                limit=storage_limit,
                label=label,
                original_chars=manager_prompt_original_chars_hint(
                    value,
                    cap=storage_limit,
                ),
                retained_keys=[str(key) for key in value.keys()],
                compact_value=compact_fn,
                priority_candidate_keys=priority_candidate_keys,
            )
    original_chars = prompt_chars(value)
    if original_chars <= storage_limit:
        return value
    retained_keys = [str(key) for key in value.keys()]
    string_limit = 900
    list_limit = 10
    compact = compact_fn(
        value,
        string_limit=string_limit,
        list_limit=list_limit,
    )
    if not isinstance(compact, dict):
        compact = {}
    preserve_wiki_gate_storage_contracts(compact, value)
    if isinstance(value.get("prompt_budget"), dict):
        compact["prompt_budget"] = value["prompt_budget"]
    if isinstance(value.get("diagnostics"), dict):
        compact["diagnostics"] = compact_manager_diagnostics_for_storage(
            value.get("diagnostics") or {},
            compact_value=compact_fn,
            string_limit=180,
            list_limit=8,
        )
    if isinstance(value.get("applied"), dict):
        compact["applied"] = compact_fn(
            value["applied"],
            string_limit=220,
            list_limit=8,
        )
    if isinstance(value.get("hold_decision"), dict):
        compact["hold_decision"] = compact_fn(
            value["hold_decision"],
            string_limit=220,
            list_limit=4,
        )
    if isinstance(value.get("lane_review"), dict):
        compact["lane_review"] = compact_fn(
            value["lane_review"],
            string_limit=180,
            list_limit=4,
        )
    if isinstance(value.get("prompt_compaction"), dict):
        compact["prompt_compaction"] = compact_fn(
            value["prompt_compaction"],
            string_limit=180,
            list_limit=8,
        )
    compact["_storage_compaction"] = manager_storage_compaction_meta(
        label=label,
        original_chars=original_chars,
        storage_limit_chars=storage_limit,
        retained_keys=retained_keys,
    )
    while prompt_chars(compact) > storage_limit and (
        string_limit > 120 or list_limit > 2
    ):
        string_limit = max(int(string_limit * 0.55), 120)
        list_limit = max(int(list_limit // 2), 2)
        compact = compact_fn(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
        if not isinstance(compact, dict):
            compact = {}
        preserve_wiki_gate_storage_contracts(compact, value)
        if isinstance(value.get("prompt_budget"), dict):
            compact["prompt_budget"] = value["prompt_budget"]
        if isinstance(value.get("diagnostics"), dict):
            compact["diagnostics"] = compact_manager_diagnostics_for_storage(
                value.get("diagnostics") or {},
                compact_value=compact_fn,
                string_limit=160,
                list_limit=6,
            )
        if isinstance(value.get("applied"), dict):
            compact["applied"] = compact_fn(
                value["applied"],
                string_limit=220,
                list_limit=8,
            )
        if isinstance(value.get("hold_decision"), dict):
            compact["hold_decision"] = compact_fn(
                value["hold_decision"],
                string_limit=220,
                list_limit=4,
            )
        if isinstance(value.get("lane_review"), dict):
            compact["lane_review"] = compact_fn(
                value["lane_review"],
                string_limit=180,
                list_limit=4,
            )
        compact["_storage_compaction"] = manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=storage_limit,
            retained_keys=retained_keys,
        )
    if prompt_chars(compact) <= storage_limit:
        return compact
    if label == "binance_manager_prompt":
        return manager_prompt_storage_emergency_payload(
            value,
            limit=storage_limit,
            label=label,
            original_chars=original_chars,
            retained_keys=retained_keys,
            compact_value=compact_fn,
            priority_candidate_keys=priority_candidate_keys,
        )
    emergency = {
        "_storage_compaction": manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=storage_limit,
            retained_keys=retained_keys,
            emergency=True,
        ),
        "prompt_budget": value.get("prompt_budget")
        if isinstance(value.get("prompt_budget"), dict)
        else {},
        "status": clean_text_fn(value.get("status"), 80),
        "hold_decision": compact_fn(
            value.get("hold_decision") or {},
            string_limit=220,
            list_limit=4,
        ),
        "lane_review": compact_fn(
            value.get("lane_review") or {},
            string_limit=180,
            list_limit=4,
        ),
        "applied": compact_manager_applied_for_storage(
            value.get("applied") or {},
            compact_value=compact_fn,
        ),
        "selected_contract_id": clean_text_fn(
            value.get("selected_contract_id"),
            120,
        ),
    }
    preserve_wiki_gate_storage_contracts(emergency, value)
    return emergency


def compact_prompt_candidate_minimal(
    row: Any,
    *,
    compact_value: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
) -> Any:
    if not isinstance(row, dict):
        return row
    calculated = (
        row.get("calculated")
        if isinstance(row.get("calculated"), dict)
        else row.get("calculated_price_plan")
        if isinstance(row.get("calculated_price_plan"), dict)
        else {}
    )
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    compact_calculated = {
        key: calculated.get(key)
        for key in (
            "method_version",
            "lane",
            "volatile_attack",
            "entry_quality",
            "reward_risk",
            "quote_budget_usdt",
            "risk_pct",
            "stop_pct",
            "target_pct",
            "margin_type",
            "liquidation_price",
            "pattern_live_crosscheck",
        )
        if calculated.get(key) not in ({}, [], "", None)
    }
    market_inputs = calculated.get("market_inputs")
    if isinstance(market_inputs, dict):
        compact_calculated["market_inputs"] = compact_prompt_value_bounded(
            market_inputs,
            string_limit=80,
            list_limit=3,
            dict_limit=16,
        )
    compact_metadata = {
        key: metadata.get(key)
        for key in (
            "lane",
            "volatile_attack",
            "entry_style",
            "entry_trigger_price",
            "entry_trigger_operator",
            "pattern_live_crosscheck",
        )
        if metadata.get(key) not in ({}, [], "", None)
    }
    out = {
        key: row.get(key)
        for key in (
            "symbol",
            "market",
            "side",
            "horizon",
            "lane",
            "stance",
            "score",
            "confidence",
            "entry_style",
            "entry_price",
            "entry_price_usdt",
            "entry_trigger_price",
            "entry_trigger_operator",
            "target_price",
            "target_price_usdt",
            "stop_price",
            "stop_price_usdt",
            "quote_budget_usdt",
            "leverage",
            "margin_type",
            "liquidation_price",
            "spread_bps",
            "book_fresh",
            "spot_shadow",
            "futures_shadow",
            "source_market",
            "updated_at",
            "captured_at",
        )
        if row.get(key) not in ({}, [], "", None)
    }
    if compact_calculated:
        out["calculated"] = compact_value(
            compact_calculated,
            list_limit=3,
            string_limit=80,
        )
    if compact_metadata:
        out["metadata"] = compact_value(
            compact_metadata,
            list_limit=3,
            string_limit=80,
        )
    reason = clean_text(row.get("reason_md") or row.get("reason") or "", 180)
    if reason:
        out["reason_md"] = reason
    return out


def compact_prompt_candidates_minimal(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
) -> Any:
    if not isinstance(value, list):
        return value
    return [
        compact_prompt_candidate_minimal(
            row,
            compact_value=compact_value,
            clean_text=clean_text,
        )
        for row in value
    ]


def _compact_prompt_candidates_lane_diverse_section(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
    list_limit: int,
    string_limit: int,
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
) -> Any:
    input_was_list = isinstance(value, list)
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        minimal_value = {
            **value,
            "items": compact_prompt_candidates_minimal(
                value.get("items"),
                compact_value=compact_value,
                clean_text=clean_text,
            ),
        }
    else:
        minimal_value = compact_prompt_candidates_minimal(
            value,
            compact_value=compact_value,
            clean_text=clean_text,
        )
    compacted = _compact_prompt_storage_sequence_section(
        minimal_value,
        compact_value=compact_value,
        string_limit=string_limit,
        list_limit=list_limit,
        priority_candidate_keys=priority_candidate_keys or set(),
        lane_diverse=True,
    )
    return compacted.get("items", []) if input_was_list else compacted


def compact_manager_candidate_for_prompt(
    row: dict[str, Any],
    *,
    compact_value: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
    score_candidate: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    calculated = (
        row.get("calculated")
        if isinstance(row.get("calculated"), dict)
        else row.get("calculated_price_plan")
        if isinstance(row.get("calculated_price_plan"), dict)
        else {}
    )
    compact_calculated: dict[str, Any] = {
        key: calculated.get(key)
        for key in (
            "method_version",
            "lane",
            "volatile_attack",
            "entry_quality",
            "empirical_edge_score",
            "reward_risk",
            "quote_budget",
            "quote_currency",
            "quote_budget_usdt",
            "quote_budget_krw",
            "performance_budget_multiplier",
            "pattern_performance_multiplier",
            "pattern_performance_scorecard",
            "risk_pct",
            "stop_pct",
            "target_pct",
            "margin_type",
            "liquidation_price",
            "pattern_live_crosscheck",
        )
        if calculated.get(key) not in ({}, [], "", None)
    }
    for section in (
        "market_inputs",
        "technical_inputs",
        "derivatives_inputs",
        "pattern_inputs",
        "sizing_inputs",
    ):
        section_payload = calculated.get(section)
        if isinstance(section_payload, dict):
            compact_calculated[section] = compact_value(
                section_payload,
                string_limit=90,
                list_limit=4,
            )
    notes = calculated.get("decision_notes")
    if isinstance(notes, list):
        compact_calculated["decision_notes"] = [
            clean_text(note, 120)
            for note in notes
            if str(note or "").strip()
        ][:4]

    alpha_score = (
        row.get("alpha_score_v3")
        if isinstance(row.get("alpha_score_v3"), dict)
        else score_candidate(row)
        if score_candidate is not None
        else {}
    )
    sizing_inputs = (
        calculated.get("sizing_inputs")
        if isinstance(calculated.get("sizing_inputs"), dict)
        else {}
    )
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    performance_budget_multiplier = (
        _safe_float(row.get("performance_budget_multiplier"))
        or _safe_float(calculated.get("performance_budget_multiplier"))
        or _safe_float(sizing_inputs.get("performance_budget_multiplier"))
        or _safe_float(metadata.get("performance_budget_multiplier"))
    )
    pattern_performance_scorecard = (
        row.get("pattern_performance_scorecard")
        if isinstance(row.get("pattern_performance_scorecard"), dict)
        else calculated.get("pattern_performance_scorecard")
        if isinstance(calculated.get("pattern_performance_scorecard"), dict)
        else metadata.get("pattern_performance_scorecard")
        if isinstance(metadata.get("pattern_performance_scorecard"), dict)
        else {}
    )
    out = {
        key: value
        for key, value in {
            "symbol": str(row.get("symbol") or "").upper().strip(),
            "market": str(row.get("market") or row.get("venue") or "spot"),
            "side": str(row.get("side") or "long"),
            "horizon": str(row.get("horizon") or ""),
            "lane": str(row.get("lane") or ""),
            "stance": str(row.get("stance") or ""),
            "score": _safe_float(row.get("score")),
            "confidence": _safe_float(row.get("confidence")),
            "entry_style": row.get("entry_style"),
            "entry_price": _safe_float(row.get("entry_price")),
            "entry_price_usdt": _safe_float(
                row.get("entry_price_usdt") or row.get("entry_price")
            ),
            "entry_trigger_price": _safe_float(row.get("entry_trigger_price")),
            "entry_trigger_operator": row.get("entry_trigger_operator"),
            "target_price": _safe_float(row.get("target_price")),
            "target_price_usdt": _safe_float(
                row.get("target_price_usdt") or row.get("target_price")
            ),
            "stop_price": _safe_float(row.get("stop_price")),
            "stop_price_usdt": _safe_float(
                row.get("stop_price_usdt") or row.get("stop_price")
            ),
            "quote_budget_usdt": _safe_float(row.get("quote_budget_usdt")),
            "quote_budget": _safe_float(row.get("quote_budget")),
            "quote_budget_krw": _safe_float(row.get("quote_budget_krw")),
            "quote_currency": row.get("quote_currency"),
            "empirical_edge_score": _safe_float(row.get("empirical_edge_score")),
            "performance_budget_multiplier": performance_budget_multiplier,
            "pattern_performance_scorecard": compact_value(
                pattern_performance_scorecard,
                string_limit=100,
                list_limit=4,
            )
            if pattern_performance_scorecard
            else {},
            "leverage": _safe_int(row.get("leverage")),
            "margin_type": row.get("margin_type"),
            "liquidation_price": _safe_float(row.get("liquidation_price")),
            "spread_bps": _safe_float(row.get("spread_bps")),
            "book_fresh": row.get("book_fresh"),
            "spot_shadow": row.get("spot_shadow"),
            "upbit_shadow": row.get("upbit_shadow"),
            "futures_shadow": row.get("futures_shadow"),
            "source_market": row.get("source_market"),
            "updated_at": row.get("updated_at"),
            "captured_at": row.get("captured_at"),
            "reason_md": clean_text(row.get("reason_md"), 220),
            "alpha_score_v3": alpha_score,
            "calculated": compact_calculated,
            "near_duplicate_active_block": compact_value(
                row.get("near_duplicate_active_block"),
                string_limit=160,
                list_limit=4,
            )
            if isinstance(row.get("near_duplicate_active_block"), dict)
            else {},
            "lane_authority_candidate": compact_value(
                row.get("lane_authority_candidate"),
                string_limit=120,
                list_limit=4,
            )
            if isinstance(row.get("lane_authority_candidate"), dict)
            else {},
            "execution_blockers": compact_value(
                row.get("execution_blockers"),
                string_limit=140,
                list_limit=4,
            )
            if isinstance(row.get("execution_blockers"), dict)
            else {},
            "memory_hint": compact_value(
                row.get("memory_hint"),
                string_limit=140,
                list_limit=4,
            )
            if isinstance(row.get("memory_hint"), dict)
            else {},
        }.items()
        if value not in ({}, [], "", None)
    }
    if normalize_market(out.get("market")) == "upbit_spot":
        entry_price = _safe_float(row.get("entry_price_krw") or row.get("entry_price"))
        target_price = _safe_float(row.get("target_price_krw") or row.get("target_price"))
        stop_price = _safe_float(row.get("stop_price_krw") or row.get("stop_price"))
        for key in (
            "entry_price_usdt",
            "target_price_usdt",
            "stop_price_usdt",
            "quote_budget_usdt",
        ):
            out.pop(key, None)
        if isinstance(out.get("calculated"), dict):
            out["calculated"].pop("quote_budget_usdt", None)
        if _safe_float(out.get("quote_budget_krw")) <= 0:
            out.pop("quote_budget_krw", None)
        if entry_price > 0:
            out["entry_price_krw"] = entry_price
        if target_price > 0:
            out["target_price_krw"] = target_price
        if stop_price > 0:
            out["stop_price_krw"] = stop_price
    block_template = row.get("block_template")
    if isinstance(block_template, dict):
        out["block_template"] = compact_value(
            block_template,
            string_limit=90,
            list_limit=4,
        )
    if isinstance(metadata, dict):
        compact_metadata = {
            key: metadata.get(key)
            for key in (
                "lane",
                "volatile_attack",
                "entry_style",
                "entry_trigger_price",
                "entry_trigger_operator",
                "pattern_live_crosscheck",
                "pattern_performance_multiplier",
                "pattern_performance_scorecard",
                "lane_authority_candidate",
                "execution_blockers",
            )
            if metadata.get(key) not in ({}, [], "", None)
        }
        if compact_metadata:
            out["metadata"] = compact_metadata
    return out


def compact_manager_block_for_prompt(
    row: Any,
    *,
    compact_value_bounded: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
    string_limit: int = 160,
) -> Any:
    if not isinstance(row, dict):
        return row
    out: dict[str, Any] = {
        key: row.get(key)
        for key in (
            "block_id",
            "symbol",
            "market",
            "side",
            "status",
            "qty_initial",
            "qty_open",
            "entry_price",
            "target_price",
            "stop_price",
            "leverage",
            "margin_type",
            "liquidation_price",
            "force_exit_requested",
            "created_at",
            "updated_at",
            "opened_at",
            "closed_at",
        )
        if row.get(key) not in ({}, [], "", None)
    }
    for key in ("thesis", "llm_reason", "risk_note"):
        text = clean_text(row.get(key), max(len(str(row.get(key) or "")), 1))
        if text:
            out[key] = (
                f"[truncated:{len(text)} chars]"
                if len(text) > max(int(string_limit), 1)
                else text
            )
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    compact_metadata: dict[str, Any] = {}
    for key in (
        "lane",
        "horizon",
        "block_color",
        "entry_style",
        "entry_trigger_price",
        "entry_trigger_operator",
        "quote_currency",
        "partial_profit_taken_at",
        "partial_profit_trigger_source",
        "partial_profit_exit_mode",
        "profit_lock_trigger_source",
        "profit_lock_original_stop_price",
        "jue_wiki_repair_pressure",
        "jue_wiki_repair_resolution",
        "jue_wiki_memory_card_quality",
        "jue_wiki_memory_card_cross_check",
        "jue_wiki_selection_resolution",
        "jue_wiki_freshness_cross_check",
        "jue_wiki_context_gap",
        "jue_wiki_reference_basis",
        "jue_wiki_usage_contract_resolution",
    ):
        if metadata.get(key) not in ({}, [], "", None):
            compact_metadata[key] = metadata.get(key)
    for key in (
        "risk_sizing",
        "calculated_price_plan",
        "pattern_live_crosscheck",
        "runtime_weak_performance_lane",
        "adoption",
        "cost_edge_gate",
        "lane_authority_gate",
        "validation_evidence",
        "validation_repair_enforcement",
        "policy_effect_enforcement",
    ):
        value = metadata.get(key)
        if value in ({}, [], "", None):
            continue
        compact_metadata[key] = compact_value_bounded(
            value,
            string_limit=90,
            list_limit=4,
            dict_limit=18,
            drop_keys={
                "diagnostics",
                "evidence",
                "huge_inputs",
                "raw",
                "raw_json",
                "raw_payload",
                "raw_rows",
            },
        )
    omitted_keys = sorted(
        key
        for key in metadata
        if key not in compact_metadata
    )
    if omitted_keys:
        compact_metadata["_omitted_for_prompt"] = omitted_keys[:12]
        compact_metadata["_omitted_count"] = len(omitted_keys)
    if compact_metadata:
        out["metadata"] = compact_metadata
    return out


def compact_manager_blocks_for_prompt(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    compact_value_bounded: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
    list_limit: int = 12,
    string_limit: int = 160,
) -> Any:
    if not isinstance(value, list):
        return compact_value(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
    return [
        compact_manager_block_for_prompt(
            row,
            string_limit=string_limit,
            compact_value_bounded=compact_value_bounded,
            clean_text=clean_text,
        )
        for row in value[: max(int(list_limit), 0)]
    ]


def compact_manager_lane_action_for_prompt(
    key: str,
    value: Any,
    *,
    clean_text: Callable[[Any, int], str],
) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    active_revision_gate = (
        row.get("active_revision_gate")
        if isinstance(row.get("active_revision_gate"), dict)
        else {}
    )
    validation_gate = (
        row.get("validation_gate")
        if isinstance(row.get("validation_gate"), dict)
        else {}
    )
    return {
        item_key: item_value
        for item_key, item_value in {
            "lane": key,
            "action": row.get("action"),
            "budget_multiplier": row.get("budget_multiplier")
            or row.get("max_budget_multiplier"),
            "requires_waiting_entry": row.get("requires_waiting_entry")
            or row.get("waiting_entry_required"),
            "scale_up_blocked": row.get("scale_up_blocked")
            or row.get("blocks_scale_up"),
            "active_revision_status": active_revision_gate.get("status"),
            "validation_status": validation_gate.get("status")
            or row.get("validation_gate_status"),
            "reason": clean_text(row.get("reason") or row.get("summary"), 90),
        }.items()
        if item_value not in ({}, [], "", None)
    }


def compact_manager_lane_authority_for_prompt(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
    string_limit: int = 140,
    list_limit: int = 6,
) -> Any:
    if not isinstance(value, dict):
        return compact_value(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
    allowed_keys = (
        "version",
        "validation_gate_status",
        "global_scale_up_allowed",
        "execution_posture",
        "probe_policy",
        "probe_lane_count",
        "probe_lane_names",
        "scale_blocked_lane_count",
        "scale_blocked_lanes",
        "max_budget_multiplier",
        "weak_lanes",
        "cost_weak_lanes",
        "cost_evidence_weak_lanes",
        "validation_evidence_weak_lanes",
        "validation_repair_weak_lanes",
        "exposure_blocked_lanes",
        "insufficient_lanes",
        "remediation_blocked_lanes",
        "shadow_blocked_lanes",
        "validation_remediation_gate",
        "active_revision_gate",
        "validation_shadow_gate",
        "validation_exposure_gate",
        "block_design_requirements",
    )
    compact = {
        key: compact_value(
            value.get(key),
            string_limit=string_limit,
            list_limit=list_limit,
        )
        for key in allowed_keys
        if value.get(key) not in ({}, [], "", None)
    }
    lane_actions = value.get("lane_actions")
    if isinstance(lane_actions, dict):
        compact["lane_actions"] = [
            compact_manager_lane_action_for_prompt(
                str(key),
                action,
                clean_text=clean_text,
            )
            for key, action in list(lane_actions.items())[: max(int(list_limit), 1)]
        ]
        compact["lane_action_count"] = len(lane_actions)
    elif isinstance(lane_actions, list):
        compact["lane_actions"] = compact_value(
            lane_actions,
            string_limit=string_limit,
            list_limit=list_limit,
        )
        compact["lane_action_count"] = len(lane_actions)
    omitted_count = len([key for key in value if key not in compact])
    if omitted_count:
        compact["_omitted_count"] = omitted_count
    return compact


LIVE_AUTHORITY_BLOAT_KEYS = {
    "diagnostics",
    "evidence",
    "huge_inputs",
    "payload",
    "raw",
    "raw_diagnostics",
    "raw_json",
    "raw_payload",
    "raw_rows",
    "trading_validation",
}


def compact_live_authority_prompt_value(
    value: Any,
    *,
    compact_value_bounded: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
    string_limit: int = 180,
    list_limit: int = 6,
) -> Any:
    """Compact live authority without replacing useful guidance with placeholders."""
    if isinstance(value, dict):
        return compact_value_bounded(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
            dict_limit=max(min(int(list_limit) * 4, 24), 8),
            drop_keys=LIVE_AUTHORITY_BLOAT_KEYS,
            placeholder_strings=False,
        )
    if isinstance(value, (list, tuple)):
        return [
            compact_live_authority_prompt_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                compact_value_bounded=compact_value_bounded,
                clean_text=clean_text,
            )
            for item in list(value)[: max(int(list_limit), 0)]
        ]
    if isinstance(value, str):
        return clean_text(value, max(int(string_limit), 1))
    return value


def compact_manager_live_authority_for_prompt(
    value: Any,
    *,
    compact_value: Callable[..., Any],
    compact_value_bounded: Callable[..., Any],
    clean_text: Callable[[Any, int], str],
    string_limit: int = 140,
    list_limit: int = 6,
) -> Any:
    if not isinstance(value, dict):
        return compact_value(
            value,
            string_limit=string_limit,
            list_limit=list_limit,
        )
    allowed_keys = (
        "status",
        "version",
        "generated_at",
        "live_grade",
        "summary",
        "reason",
        "validation_gate",
        "active_revision_evidence",
        "lane_authority",
        "performance_lanes",
        "growth_authority",
        "scale_candidate",
        "max_budget_multiplier",
        "risk_budget_multiplier",
        "action_permissions",
        "data_gaps",
        "warnings",
        "validation_gate_status",
        "validation_readiness",
        "validation_gate_reason",
        "risk_governor_action",
        "risk_governor_source",
        "discipline_matrix",
        "validation_passport",
        "validation_pressure",
        "cost_attribution",
        "failed_disciplines",
        "weak_disciplines",
        "capacity_bottleneck",
        "failure_attribution",
        "loss_cooldown",
        "validation_recovery_focus",
        "operator_guidance",
        "risk_governor_reasons",
        "remediation_plan",
    )
    compact: dict[str, Any] = {}
    for key in allowed_keys:
        if value.get(key) in ({}, [], "", None):
            continue
        if key == "lane_authority":
            compact[key] = compact_manager_lane_authority_for_prompt(
                value.get(key),
                string_limit=string_limit,
                list_limit=list_limit,
                compact_value=compact_value,
                clean_text=clean_text,
            )
        else:
            compact[key] = compact_live_authority_prompt_value(
                value.get(key),
                string_limit=string_limit,
                list_limit=list_limit,
                compact_value_bounded=compact_value_bounded,
                clean_text=clean_text,
            )
    omitted_count = len([key for key in value if key not in compact])
    if omitted_count:
        compact["_omitted_count"] = omitted_count
    return compact or {
        "status": str(value.get("status") or "compacted"),
        "_omitted_count": len(value),
    }


def _prompt_priority_symbols(prompt: dict[str, Any], *, limit: int) -> list[str]:
    symbols: list[str] = []

    def add(raw: Any) -> None:
        symbol = str(raw or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)

    for row in prompt.get("candidates") or []:
        if isinstance(row, dict):
            add(row.get("symbol"))
        if len(symbols) >= limit:
            return symbols
    for row in prompt.get("blocks") or []:
        if isinstance(row, dict):
            add(row.get("symbol"))
        if len(symbols) >= limit:
            return symbols
    for raw in prompt.get("universe") or []:
        add(raw)
        if len(symbols) >= limit:
            return symbols
    return symbols


def compact_candidate_policy_impacts_for_latency(
    value: Any,
    *,
    priority_symbols: list[str],
    symbol_limit: int,
) -> Any:
    if not isinstance(value, dict):
        return compact_prompt_value_bounded(
            value,
            list_limit=8,
            string_limit=90,
            dict_limit=6,
        )
    compact: dict[str, Any] = {}
    if "_global" in value:
        compact["_global"] = compact_prompt_value_bounded(
            value.get("_global"),
            list_limit=2,
            string_limit=90,
            dict_limit=6,
        )
    for symbol in priority_symbols:
        if len([key for key in compact if key != "_global"]) >= max(
            int(symbol_limit),
            1,
        ):
            break
        if symbol not in value:
            continue
        compact[symbol] = compact_prompt_value_bounded(
            value.get(symbol),
            list_limit=2,
            string_limit=90,
            dict_limit=6,
        )
    if len([key for key in compact if key != "_global"]) < max(
        int(symbol_limit),
        1,
    ):
        for key, rows in value.items():
            if key == "_global" or key in compact:
                continue
            if len([item for item in compact if item != "_global"]) >= max(
                int(symbol_limit),
                1,
            ):
                break
            compact[str(key)] = compact_prompt_value_bounded(
                rows,
                list_limit=2,
                string_limit=90,
                dict_limit=6,
            )
    return compact


def compact_proactive_decision_pressure_for_latency(value: Any) -> Any:
    compact = compact_prompt_value_bounded(
        value or {},
        list_limit=1,
        string_limit=50,
        dict_limit=8,
    )
    if not isinstance(value, dict) or not isinstance(compact, dict):
        return compact
    for key in (
        "version",
        "status",
        "pressure_level",
        "zero_action_streak",
        "previous_error_streak",
        "binance_zero_action_streak",
        "binance_previous_error_streak",
        "effective_zero_action_streak",
        "pressure_source",
        "candidate_count",
        "strong_candidate_count",
        "growth_governor_mode",
        "growth_governor_allows_new_blocks",
        "live_grade",
    ):
        if key in value:
            compact[key] = value[key]
    gap = (
        value.get("binance_market_activity_gap")
        if isinstance(value.get("binance_market_activity_gap"), dict)
        else {}
    )
    if gap:
        compact["binance_market_activity_gap"] = compact_prompt_value_bounded(
            gap,
            list_limit=4,
            string_limit=90,
            dict_limit=12,
            placeholder_strings=False,
        )
    return compact


def manager_run_is_timeout_error(row: dict[str, Any]) -> bool:
    if str(row.get("status") or "").strip().lower() != "error":
        return False
    message = str(row.get("error_message") or "").strip().lower()
    return "timed out" in message or "timeout" in message


def _manager_latency_recovery_target_chars(
    *,
    configured_target_chars: int,
    recent_timeout_count: int,
) -> int:
    configured = max(int(configured_target_chars), 10_000)
    count = max(int(recent_timeout_count), 0)
    if count >= 4:
        return min(configured, 18_000)
    if count >= 3:
        return min(configured, 24_000)
    if count >= 2:
        return min(configured, 36_000)
    if count >= 1:
        return min(configured, 45_000)
    return configured


def manager_latency_guard_from_runs(
    recent_runs: list[dict[str, Any]],
    *,
    target_chars: int,
) -> dict[str, Any]:
    timeout_runs = [
        row
        for row in recent_runs[:6]
        if isinstance(row, dict) and manager_run_is_timeout_error(row)
    ]
    if not timeout_runs:
        return {
            "version": "binance_manager_latency_guard_v1",
            "active": False,
            "reason": "",
            "recent_timeout_count": 0,
            "target_chars": max(int(target_chars), 10_000),
            "configured_target_chars": max(int(target_chars), 10_000),
        }
    configured_target = max(int(target_chars), 10_000)
    recovery_target = _manager_latency_recovery_target_chars(
        configured_target_chars=configured_target,
        recent_timeout_count=len(timeout_runs),
    )
    return {
        "version": "binance_manager_latency_guard_v1",
        "active": True,
        "reason": "recent_manager_timeout",
        "recent_timeout_count": len(timeout_runs),
        "recent_timeout_run_ids": [
            int(row.get("id") or 0)
            for row in timeout_runs[:3]
            if int(row.get("id") or 0) > 0
        ],
        "target_chars": recovery_target,
        "configured_target_chars": configured_target,
        "instruction": (
            "Previous manager call timed out. Keep the next judgment payload lean: "
            "preserve executable candidates, active blocks, guard state, and top "
            "policy impacts, but avoid raw or repeated per-symbol policy dumps."
        ),
    }


def _compact_entry_gate_policy_card(value: Any, *, string_limit: int) -> Any:
    if not isinstance(value, dict):
        return compact_prompt_value_bounded(
            value,
            list_limit=2,
            string_limit=string_limit,
            dict_limit=8,
        )
    keep_keys = (
        "status",
        "source",
        "matched_lane",
        "sample_count",
        "min_samples",
        "win_rate_pct",
        "profit_factor",
        "pnl_usdt",
        "live_budget_multiplier",
        "avg_r_multiple",
        "max_drawdown_r_multiple",
        "recovery_factor",
        "instruction",
        "recovery_required",
    )
    compact: dict[str, Any] = {}
    for key in keep_keys:
        if key in value:
            keep_text = key in {"instruction", "recovery_required", "reason"}
            compact[key] = compact_prompt_value_bounded(
                value.get(key),
                list_limit=2,
                string_limit=string_limit,
                dict_limit=4,
                placeholder_strings=not keep_text,
            )
    return compact


def _compact_entry_gate_policy_mapping(
    mapping: Any,
    *,
    priority_keys: list[str],
    limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in priority_keys:
        if len(compact) >= max(int(limit), 1):
            break
        if key not in mapping:
            continue
        compact[str(key)] = _compact_entry_gate_policy_card(
            mapping.get(key),
            string_limit=string_limit,
        )
    if len(compact) < max(int(limit), 1):
        for key, value in mapping.items():
            if len(compact) >= max(int(limit), 1):
                break
            if str(key) in compact:
                continue
            compact[str(key)] = _compact_entry_gate_policy_card(
                value,
                string_limit=string_limit,
            )
    return compact


def compact_entry_gate_policy_for_prompt(
    value: Any,
    *,
    list_limit: int = 8,
    string_limit: int = 120,
) -> Any:
    if not isinstance(value, dict):
        return compact_prompt_value_bounded(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
            dict_limit=12,
        )

    compact: dict[str, Any] = {}
    scalar_keys = (
        "status",
        "version",
        "adjustment",
        "min_confidence",
        "min_expected_r",
        "min_directional_score",
        "min_direction_margin",
        "performance_sample_count",
        "performance_win_rate_pct",
        "performance_avg_r_multiple",
        "lane_policy",
        "hold_stance_requires_explicit_upgrade",
        "watch_stance_requires_min_confidence",
    )
    for key in scalar_keys:
        if key in value:
            compact[key] = compact_prompt_value_bounded(
                value.get(key),
                list_limit=2,
                string_limit=string_limit,
                dict_limit=6,
            )

    for key in (
        "cooldown_lane_keys",
        "cooldown_symbol_keys",
        "entry_quality_cooldown_keys",
    ):
        if key in value:
            compact[key] = compact_prompt_value_bounded(
                value.get(key),
                list_limit=list_limit,
                string_limit=80,
                dict_limit=4,
            )

    lane_keys = [str(item) for item in compact.get("cooldown_lane_keys") or []]
    symbol_keys = [str(item) for item in compact.get("cooldown_symbol_keys") or []]
    entry_quality_keys = [
        str(item) for item in compact.get("entry_quality_cooldown_keys") or []
    ]
    if "cooldown_lanes" in value:
        compact["cooldown_lanes"] = _compact_entry_gate_policy_mapping(
            value.get("cooldown_lanes"),
            priority_keys=lane_keys,
            limit=list_limit,
            string_limit=string_limit,
        )
    if "cooldown_symbols" in value:
        compact["cooldown_symbols"] = _compact_entry_gate_policy_mapping(
            value.get("cooldown_symbols"),
            priority_keys=symbol_keys,
            limit=list_limit,
            string_limit=string_limit,
        )
    if "entry_quality_cooldowns" in value:
        compact["entry_quality_cooldowns"] = _compact_entry_gate_policy_mapping(
            value.get("entry_quality_cooldowns"),
            priority_keys=entry_quality_keys,
            limit=list_limit,
            string_limit=string_limit,
        )
    if "shadow_only_entry_qualities" in value:
        shadow_keys = [
            str(item)
            for item in value.get("shadow_only_entry_quality_keys") or []
            if str(item).strip()
        ]
        compact["shadow_only_entry_quality_keys"] = shadow_keys[:list_limit]
        compact["shadow_only_entry_qualities"] = _compact_entry_gate_policy_mapping(
            value.get("shadow_only_entry_qualities"),
            priority_keys=shadow_keys,
            limit=list_limit,
            string_limit=string_limit,
        )

    for key in (
        "waiting_entry_policy",
        "candidate_symbol_cooldown_lookup",
        "effective_adjustment",
    ):
        if key in value:
            compact[key] = compact_prompt_value_bounded(
                value.get(key),
                list_limit=4,
                string_limit=string_limit,
                dict_limit=10,
            )

    lane_scorecards = value.get("lane_scorecards")
    if isinstance(lane_scorecards, dict):
        compact["lane_scorecards"] = _compact_entry_gate_policy_mapping(
            lane_scorecards,
            priority_keys=lane_keys,
            limit=list_limit,
            string_limit=string_limit,
        )
    elif isinstance(lane_scorecards, list):
        compact["lane_scorecards"] = compact_prompt_value_bounded(
            lane_scorecards,
            list_limit=list_limit,
            string_limit=string_limit,
            dict_limit=8,
        )

    compact["compacted_for_prompt"] = True
    return compact


BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS = (
    "performance",
    "lane_balance",
    "raw_context_refs",
    "universe",
)


def compact_policy_prompt_section(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> Any:
    if not isinstance(value, dict):
        return compact_prompt_value(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    out: dict[str, Any] = {}
    policy_string_limit = max(int(string_limit), 720)
    for key, child in value.items():
        key_str = str(key)
        if isinstance(child, str) and key_str.endswith("_policy"):
            out[key_str] = _clean_text(child, limit=policy_string_limit)
        else:
            out[key_str] = compact_prompt_value(
                child,
                list_limit=list_limit,
                string_limit=string_limit,
            )
    return out


def compact_requested_symbol_coverage_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    symbol_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 160), 48)
    out: dict[str, Any] = {}
    for key in (
        "version",
        "status",
        "decision_policy",
        "required_action",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=text_limit)
    if "hard_blocker" in value:
        out["hard_blocker"] = bool(value.get("hard_blocker"))
    for key in (
        "requested_symbol_count",
        "summarized_symbol_count",
        "unsummarized_symbol_count",
        "missing_summary_count",
        "prompt_omitted_count",
        "degraded_summary_count",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = value.get(key)

    def compact_crypto_symbols(raw: Any) -> list[str]:
        return [
            symbol
            for symbol in (
                _compact_requested_crypto_symbol_token(item) for item in _as_list(raw)
            )
            if symbol
        ]

    for source_key, output_key, omitted_key in (
        (
            "unsummarized_symbols",
            "unsummarized_symbols",
            "unsummarized_symbol_omitted_count",
        ),
        (
            "missing_summary_symbols",
            "missing_summary_symbols",
            "missing_summary_symbol_omitted_count",
        ),
        (
            "prompt_omitted_symbols",
            "prompt_omitted_symbols",
            "prompt_omitted_symbol_omitted_count",
        ),
    ):
        symbols = compact_crypto_symbols(value.get(source_key))
        if symbols:
            out[output_key] = symbols[:symbol_limit]
            out[omitted_key] = max(len(symbols) - symbol_limit, 0)

    degraded_symbols = compact_crypto_symbols(value.get("degraded_summary_symbols"))
    if degraded_symbols:
        out["degraded_summary_symbols"] = degraded_symbols[:symbol_limit]
        out["degraded_summary_symbol_omitted_count"] = max(
            len(degraded_symbols) - symbol_limit,
            0,
        )
    degraded_reasons: list[dict[str, Any]] = []
    for item in _as_list(value.get("degraded_summary_reasons"))[:symbol_limit]:
        if not isinstance(item, dict):
            continue
        symbol = _compact_requested_crypto_symbol_token(item.get("symbol"))
        if not symbol:
            continue
        row: dict[str, Any] = {"symbol": symbol}
        if item.get("freshness") not in (None, "", [], {}):
            row["freshness"] = _clean_text(item.get("freshness"), limit=text_limit)
        quality_status = normalize_jue_wiki_quality_status(item.get("quality_status"))
        if quality_status:
            row["quality_status"] = quality_status
        warnings = [
            _clean_text(raw, limit=text_limit)
            for raw in _as_list(item.get("quality_warnings"))[:symbol_limit]
            if str(raw).strip() and len(str(raw)) <= text_limit
        ]
        if warnings:
            row["quality_warnings"] = warnings
        degraded_reasons.append(row)
    if degraded_reasons:
        out["degraded_summary_reasons"] = degraded_reasons
        out["degraded_summary_reason_omitted_count"] = max(
            len(_as_list(value.get("degraded_summary_reasons")))
            - len(degraded_reasons),
            0,
        )

    adjustments: list[dict[str, Any]] = []
    for item in _as_list(value.get("required_adjustments"))[:symbol_limit]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        symbols = compact_crypto_symbols(item.get("symbols"))
        if symbols:
            row["symbols"] = symbols[:symbol_limit]
            row["symbol_omitted_count"] = max(len(symbols) - symbol_limit, 0)
        for key in (
            "adjustment_type",
            "resolution",
            "action",
            "required_action",
            "decision_policy",
        ):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        raw_reason = str(item.get("reason") or "")
        if raw_reason and len(raw_reason) <= text_limit:
            row["reason"] = _clean_text(raw_reason, limit=text_limit)
        elif raw_reason:
            row["reason_omitted_for_prompt_budget"] = True
        if row:
            adjustments.append(row)
    if adjustments:
        out["required_adjustments"] = adjustments
        out["required_adjustment_omitted_count"] = max(
            len(_as_list(value.get("required_adjustments"))) - len(adjustments),
            0,
        )
    return out


def _requested_symbol_coverage_from_budget_report(
    budget_report: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(budget_report, dict):
        return {}
    summary_symbols = [
        symbol
        for symbol in (
            _compact_requested_crypto_symbol_token(item)
            for item in _as_list(budget_report.get("requested_symbol_summary_symbols"))
        )
        if symbol
    ]
    coverage = {
        "status": budget_report.get("requested_symbol_summary_coverage_status"),
        "requested_symbol_count": budget_report.get("requested_symbol_count"),
        "summarized_symbol_count": len(summary_symbols),
        "unsummarized_symbol_count": budget_report.get(
            "requested_symbol_unsummarized_count"
        ),
        "unsummarized_symbols": budget_report.get(
            "requested_symbol_unsummarized_symbols"
        ),
        "missing_summary_count": budget_report.get(
            "requested_symbol_missing_summary_count"
        ),
        "missing_summary_symbols": budget_report.get(
            "requested_symbol_missing_summary_symbols"
        ),
        "prompt_omitted_count": budget_report.get(
            "requested_symbol_prompt_omitted_count"
        ),
        "prompt_omitted_symbols": budget_report.get(
            "requested_symbol_prompt_omitted_symbols"
        ),
        "degraded_summary_count": budget_report.get(
            "requested_symbol_degraded_summary_count"
        ),
        "degraded_summary_symbols": budget_report.get(
            "requested_symbol_degraded_summary_symbols"
        ),
        "degraded_summary_reasons": budget_report.get(
            "requested_symbol_degraded_summary_reasons"
        ),
    }
    compact = compact_requested_symbol_coverage_prompt(
        coverage,
        list_limit=list_limit,
        string_limit=string_limit,
    )
    return {
        key: value
        for key, value in compact.items()
        if not (key.endswith("_omitted_count") and _safe_int(value) == 0)
    }


def compact_jue_wiki_memory_card_quality_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    item_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 160), 48)
    out: dict[str, Any] = {}

    def compact_string_list(
        raw: Any,
        *,
        limit: int,
        max_len: int = 120,
    ) -> list[str]:
        values: list[str] = []
        for item in _as_list(raw)[: max(int(limit), 0)]:
            text = _clean_text(item, limit=max_len)
            if text and text not in values:
                values.append(text)
        return values

    def compact_missing_fields_by_symbol(raw: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in _as_list(raw)[:item_limit]:
            if not isinstance(item, dict):
                continue
            symbol = _compact_requested_crypto_symbol_token(item.get("symbol"))
            if not symbol:
                continue
            row: dict[str, Any] = {"symbol": symbol}
            for key in ("status", "quality"):
                if item.get(key) not in (None, "", [], {}):
                    row[key] = _clean_text(item.get(key), limit=80)
            missing_fields = compact_string_list(
                item.get("missing_fields"),
                limit=8,
                max_len=80,
            )
            if missing_fields:
                row["missing_fields"] = missing_fields
            required_checks = compact_string_list(
                item.get("required_checks"),
                limit=8,
                max_len=text_limit,
            )
            if required_checks:
                row["required_checks"] = required_checks
            rows.append(row)
        return rows

    for key in ("version", "status"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=text_limit)

    summary = value.get("summary") if isinstance(value.get("summary"), dict) else {}
    compact_summary: dict[str, Any] = {}
    for key in ("version", "status"):
        if summary.get(key) not in (None, "", [], {}):
            compact_summary[key] = _clean_text(summary.get(key), limit=text_limit)
    status_counts = (
        summary.get("status_counts")
        if isinstance(summary.get("status_counts"), dict)
        else {}
    )
    if status_counts:
        compact_summary["status_counts"] = {
            _clean_text(raw_key, limit=40): _safe_int(raw_value)
            for raw_key, raw_value in status_counts.items()
            if str(raw_key).strip()
        }
    missing_field_counts = (
        summary.get("missing_field_counts")
        if isinstance(summary.get("missing_field_counts"), dict)
        else {}
    )
    if missing_field_counts:
        compact_summary["missing_field_counts"] = {
            _clean_text(raw_key, limit=60): _safe_int(raw_value)
            for raw_key, raw_value in missing_field_counts.items()
            if str(raw_key).strip()
        }
    weak_symbols = [
        symbol
        for symbol in (
            _compact_requested_crypto_symbol_token(raw_symbol)
            for raw_symbol in _as_list(summary.get("weak_symbols"))[:item_limit]
        )
        if symbol
    ]
    if weak_symbols:
        compact_summary["weak_symbols"] = weak_symbols
    missing_fields_by_symbol = compact_missing_fields_by_symbol(
        summary.get("missing_fields_by_symbol")
    )
    if missing_fields_by_symbol:
        compact_summary["missing_fields_by_symbol"] = missing_fields_by_symbol
    rows: list[dict[str, Any]] = []
    for item in _as_list(summary.get("rows"))[:item_limit]:
        if not isinstance(item, dict):
            continue
        symbol = _compact_requested_crypto_symbol_token(item.get("symbol"))
        if not symbol:
            continue
        row = {"symbol": symbol}
        for key in ("quality", "required_action"):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        missing_fields = compact_string_list(
            item.get("missing_fields"),
            limit=8,
            max_len=80,
        )
        if missing_fields:
            row["missing_fields"] = missing_fields
        required_checks = compact_string_list(
            item.get("required_checks"),
            limit=8,
            max_len=text_limit,
        )
        if required_checks:
            row["required_checks"] = required_checks
        reason = str(item.get("reason") or "").strip()
        if reason and len(reason) <= text_limit:
            row["reason"] = _clean_text(reason, limit=text_limit)
        elif reason:
            row["reason_omitted_for_prompt_budget"] = True
        rows.append(row)
    if rows:
        compact_summary["rows"] = rows
    if compact_summary:
        out["summary"] = compact_summary

    action_plan = (
        value.get("action_plan") if isinstance(value.get("action_plan"), dict) else {}
    )
    compact_plan: dict[str, Any] = {}
    for key in ("status", "required_action", "decision_policy"):
        if action_plan.get(key) not in (None, "", [], {}):
            compact_plan[key] = _clean_text(action_plan.get(key), limit=text_limit)
    plan_symbols = [
        symbol
        for symbol in (
            _compact_requested_crypto_symbol_token(raw_symbol)
            for raw_symbol in _as_list(action_plan.get("symbols"))[:item_limit]
        )
        if symbol
    ]
    if plan_symbols:
        compact_plan["symbols"] = plan_symbols
    plan_missing_fields = compact_missing_fields_by_symbol(
        action_plan.get("missing_fields_by_symbol")
    )
    if plan_missing_fields:
        compact_plan["missing_fields_by_symbol"] = plan_missing_fields
    required_checks = compact_string_list(
        action_plan.get("required_checks"),
        limit=item_limit * 3,
        max_len=text_limit,
    )
    if required_checks:
        compact_plan["required_checks"] = required_checks
    reason = str(action_plan.get("reason") or "").strip()
    if reason and len(reason) <= text_limit:
        compact_plan["reason"] = _clean_text(reason, limit=text_limit)
    elif reason:
        compact_plan["reason_omitted_for_prompt_budget"] = True
    if compact_plan:
        out["action_plan"] = compact_plan
    return out


def compact_jue_wiki_repair_contract_prompt(
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    item_limit = max(int(list_limit), 1)
    text_limit = max(min(int(string_limit), 180), 48)
    out: dict[str, Any] = {}
    for key in ("version", "status"):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _clean_text(value.get(key), limit=text_limit)
    for key in (
        "repair_priority_count",
        "top_priority_count",
        "omitted_priority_count",
    ):
        if value.get(key) not in (None, "", [], {}):
            out[key] = _safe_int(value.get(key))
    for key in (
        "priority_type_counts",
        "top_priority_type_counts",
        "omitted_priority_type_counts",
    ):
        if isinstance(value.get(key), dict) and value.get(key):
            out[key] = {
                _clean_text(raw_key, limit=80): _safe_int(raw_value)
                for raw_key, raw_value in value.get(key, {}).items()
                if str(raw_key).strip()
            }

    action_plan = value.get("repair_pressure_action_plan")
    if isinstance(action_plan, dict) and action_plan:
        plan: dict[str, Any] = {}
        for key in ("status", "required_response"):
            if action_plan.get(key) not in (None, "", [], {}):
                plan[key] = _clean_text(action_plan.get(key), limit=text_limit)
        for key in (
            "total_priority_count",
            "top_priority_count",
            "omitted_priority_count",
            "action_batch_count",
            "action_batch_total_count",
            "action_batch_omitted_count",
            "action_batch_visible_pressure_count",
        ):
            if action_plan.get(key) not in (None, "", [], {}):
                plan[key] = _safe_int(action_plan.get(key))
        if action_plan.get("action_batch_pressure_visibility_ratio") not in (
            None,
            "",
            [],
            {},
        ):
            plan["action_batch_pressure_visibility_ratio"] = round(
                min(
                    max(
                        _safe_float(
                            action_plan.get("action_batch_pressure_visibility_ratio")
                        ),
                        0.0,
                    ),
                    1.0,
                ),
                4,
            )
        omitted_types = action_plan.get("omitted_priority_type_counts")
        if isinstance(omitted_types, dict) and omitted_types:
            plan["omitted_priority_type_counts"] = {
                _clean_text(raw_key, limit=80): _safe_int(raw_value)
                for raw_key, raw_value in omitted_types.items()
                if str(raw_key).strip()
            }
        batch_type_counts = action_plan.get("action_batch_type_counts")
        if isinstance(batch_type_counts, dict) and batch_type_counts:
            plan["action_batch_type_counts"] = {
                _clean_text(raw_key, limit=120): _safe_int(raw_value)
                for raw_key, raw_value in batch_type_counts.items()
                if str(raw_key).strip()
            }
        elif _as_list(value.get("action_batches")):
            inferred_batch_type_counts: dict[str, int] = {}
            for batch in _as_list(value.get("action_batches")):
                if not isinstance(batch, dict):
                    continue
                action_type = _clean_text(batch.get("action_type"), limit=120)
                if not action_type:
                    continue
                count = max(_safe_int(batch.get("count")), 0) or 1
                inferred_batch_type_counts[action_type] = (
                    inferred_batch_type_counts.get(action_type, 0) + count
                )
            if inferred_batch_type_counts:
                plan["action_batch_type_counts"] = inferred_batch_type_counts
        batch_scopes = [
            _clean_text(raw_value, limit=40)
            for raw_value in _as_list(action_plan.get("action_batch_scopes"))[:8]
            if str(raw_value).strip()
        ]
        if not batch_scopes:
            batch_scopes = []
            for batch in _as_list(value.get("action_batches")):
                if not isinstance(batch, dict):
                    continue
                scope = _clean_text(batch.get("scope"), limit=40)
                if scope and scope not in batch_scopes:
                    batch_scopes.append(scope)
                if len(batch_scopes) >= 8:
                    break
        if batch_scopes:
            plan["action_batch_scopes"] = batch_scopes
        batch_warning_counts = action_plan.get("action_batch_warning_counts")
        if isinstance(batch_warning_counts, dict) and batch_warning_counts:
            plan["action_batch_warning_counts"] = {
                _clean_text(raw_key, limit=120): _safe_int(raw_value)
                for raw_key, raw_value in batch_warning_counts.items()
                if str(raw_key).strip() and _safe_int(raw_value) > 0
            }
        elif _as_list(value.get("action_batches")):
            inferred_warning_counts: dict[str, int] = {}
            for batch in _as_list(value.get("action_batches")):
                if not isinstance(batch, dict):
                    continue
                raw_counts = batch.get("warning_counts")
                if isinstance(raw_counts, dict):
                    for raw_key, raw_value in raw_counts.items():
                        warning = _clean_text(raw_key, limit=120)
                        count = _safe_int(raw_value)
                        if warning and count > 0:
                            inferred_warning_counts[warning] = (
                                inferred_warning_counts.get(warning, 0) + count
                            )
                    continue
                for raw_value in _as_list(batch.get("warnings")):
                    warning = _clean_text(raw_value, limit=120)
                    if warning:
                        inferred_warning_counts[warning] = (
                            inferred_warning_counts.get(warning, 0) + 1
                        )
            if inferred_warning_counts:
                plan["action_batch_warning_counts"] = inferred_warning_counts
        max_severity_score = _safe_float(
            action_plan.get("action_batch_max_severity_score")
        )
        if max_severity_score <= 0 and _as_list(value.get("action_batches")):
            max_severity_score = max(
                (
                    _safe_float(batch.get("max_severity_score"))
                    for batch in _as_list(value.get("action_batches"))
                    if isinstance(batch, dict)
                ),
                default=0.0,
            )
        if max_severity_score > 0:
            plan["action_batch_max_severity_score"] = max_severity_score
        if plan:
            out["repair_pressure_action_plan"] = plan

    action_batches: list[dict[str, Any]] = []
    for item in _as_list(value.get("action_batches"))[:item_limit]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key in ("scope", "action_type"):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        if item.get("count") not in (None, "", [], {}):
            row["count"] = _safe_int(item.get("count"))
        if item.get("max_severity_score") not in (None, "", [], {}):
            max_severity_score = _safe_float(item.get("max_severity_score"))
            if max_severity_score > 0:
                row["max_severity_score"] = max_severity_score
        raw_warning_counts = item.get("warning_counts")
        if isinstance(raw_warning_counts, dict) and raw_warning_counts:
            warning_counts = {
                _clean_text(raw_key, limit=text_limit): _safe_int(raw_value)
                for raw_key, raw_value in raw_warning_counts.items()
                if str(raw_key).strip() and _safe_int(raw_value) > 0
            }
            if warning_counts:
                row["warning_counts"] = warning_counts
        for source_key, target_key, max_len, value_limit in (
            ("symbols", "symbols", 24, max(item_limit, 8)),
            ("warnings", "warnings", text_limit, max(item_limit, 4)),
            (
                "recommended_actions",
                "recommended_actions",
                text_limit,
                max(item_limit, 4),
            ),
            ("priority_types", "priority_types", 80, max(item_limit, 4)),
        ):
            values = [
                _clean_text(raw_value, limit=max_len)
                for raw_value in _as_list(item.get(source_key))[:value_limit]
                if str(raw_value).strip()
            ]
            if values:
                row[target_key] = values
        if row:
            action_batches.append(row)
    if action_batches:
        out["action_batches"] = action_batches
        raw_action_batch_count = len(_as_list(value.get("action_batches")))
        source_action_batch_total_count = _safe_int(
            value.get("action_batch_total_count")
        ) or _safe_int(
            (action_plan or {}).get("action_batch_total_count")
            if isinstance(action_plan, dict)
            else None
        )
        if source_action_batch_total_count > 0:
            out["action_batch_total_count"] = source_action_batch_total_count
        source_action_batch_omitted_count = _safe_int(
            value.get("action_batch_omitted_count")
        ) or _safe_int(
            (action_plan or {}).get("action_batch_omitted_count")
            if isinstance(action_plan, dict)
            else None
        )
        visible_action_batch_omitted_count = max(
            raw_action_batch_count - len(action_batches),
            0,
        )
        visible_action_batch_pressure_count = sum(
            max(_safe_int(row.get("count")), 0) for row in action_batches
        )
        if visible_action_batch_pressure_count <= 0 and action_batches:
            visible_action_batch_pressure_count = len(action_batches)
        if source_action_batch_total_count <= 0:
            source_action_batch_total_count = visible_action_batch_pressure_count
        total_action_batch_omitted_count = (
            source_action_batch_omitted_count + visible_action_batch_omitted_count
        )
        visibility_ratio = (
            round(
                min(
                    max(
                        visible_action_batch_pressure_count
                        / source_action_batch_total_count,
                        0.0,
                    ),
                    1.0,
                ),
                4,
            )
            if source_action_batch_total_count > 0
            else 0.0
        )
        out["action_batch_visible_pressure_count"] = (
            visible_action_batch_pressure_count
        )
        out["action_batch_pressure_visibility_ratio"] = visibility_ratio
        out["action_batch_omitted_count"] = total_action_batch_omitted_count
        if isinstance(out.get("repair_pressure_action_plan"), dict):
            if source_action_batch_total_count > 0:
                out["repair_pressure_action_plan"][
                    "action_batch_total_count"
                ] = source_action_batch_total_count
            out["repair_pressure_action_plan"][
                "action_batch_visible_pressure_count"
            ] = visible_action_batch_pressure_count
            out["repair_pressure_action_plan"][
                "action_batch_pressure_visibility_ratio"
            ] = visibility_ratio
            out["repair_pressure_action_plan"][
                "action_batch_omitted_count"
            ] = total_action_batch_omitted_count

    priorities: list[dict[str, Any]] = []
    for item in _as_list(value.get("top_priorities"))[:item_limit]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key in (
            "page_id",
            "priority_type",
            "source_id",
            "repair_status",
            "freshness",
            "quality_status",
        ):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _clean_text(item.get(key), limit=text_limit)
        symbols = [
            _compact_requested_crypto_symbol_token(raw_symbol)
            for raw_symbol in _as_list(item.get("symbols"))[:item_limit]
        ]
        symbols = [symbol for symbol in symbols if symbol]
        if symbols:
            row["symbols"] = symbols
        for key in ("repair_action", "reason", "why"):
            raw_text = str(item.get(key) or "").strip()
            if not raw_text:
                continue
            if len(raw_text) <= text_limit:
                row[key] = _clean_text(raw_text, limit=text_limit)
            else:
                row[f"{key}_omitted_for_prompt_budget"] = True
        if row:
            priorities.append(row)
    if priorities:
        out["top_priorities"] = priorities
        out["top_priority_omitted_count"] = max(
            len(_as_list(value.get("top_priorities"))) - len(priorities),
            0,
        )
    return out


def compact_prompt_section(
    section: str,
    value: Any,
    *,
    list_limit: int,
    string_limit: int,
) -> Any:
    if section == "policy":
        return compact_policy_prompt_section(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "blocks":
        return compact_manager_blocks_for_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
            compact_value=compact_prompt_value,
            compact_value_bounded=compact_prompt_value_bounded,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
        )
    if section == "live_authority":
        return compact_manager_live_authority_for_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
            compact_value=compact_prompt_value,
            compact_value_bounded=compact_prompt_value_bounded,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
        )
    if section == "candidate_policy_impacts":
        return compact_candidate_policy_impacts_for_latency(
            value,
            priority_symbols=[],
            symbol_limit=list_limit,
        )
    if section == "entry_gate_policy":
        return compact_entry_gate_policy_for_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "memory":
        return compact_binance_memory_for_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki":
        return compact_jue_wiki_for_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_requested_symbol_coverage":
        return compact_requested_symbol_coverage_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_memory_card_quality":
        return compact_jue_wiki_memory_card_quality_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_wiki_repair_contract":
        return compact_jue_wiki_repair_contract_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    if section == "jue_workflow":
        return _compact_manager_jue_workflow_prompt(
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )
    return compact_prompt_value(
        value,
        list_limit=list_limit,
        string_limit=string_limit,
    )


def enforce_prompt_budget(
    prompt: dict[str, Any],
    *,
    max_chars: int,
) -> None:
    configured_max = max(int(max_chars), 10_000)
    effective_max = max(configured_max - 2_500, 10_000)
    priority_candidate_keys = _manager_prompt_priority_candidate_keys(prompt)
    compacted: list[dict[str, Any]] = []
    for section, list_limit, string_limit in (
        ("critical_response_contract", 6, 120),
        ("candidates", 30, 180),
        ("memory", 5, 160),
        ("jue_wiki", 6, 260),
        ("jue_wiki_decision_adjustments", 6, 160),
        ("jue_wiki_requested_symbol_coverage", 8, 160),
        ("jue_wiki_memory_card_quality", 6, 140),
        ("jue_wiki_repair_contract", 6, 140),
        ("decision_packet_v2", 5, 160),
        ("decision_packet", 5, 160),
        ("candidate_policy_impacts", 30, 140),
        ("validation_repair", 5, 140),
        ("recent_performance", 5, 140),
        ("crypto_market_pulse", 5, 150),
        ("entry_gate_policy", 8, 120),
        ("performance", 5, 140),
        ("lane_balance", 5, 140),
        ("growth_unlock", 5, 140),
        ("execution_gate", 5, 140),
        ("candidate_generation", 5, 140),
        ("language_policy", 40, 120),
        ("output_language_policy", 40, 120),
        ("raw_context_refs", 4, 120),
        ("market_universe", 24, 80),
        ("blocks", 12, 170),
        ("live_authority", 6, 140),
        ("account", 8, 140),
    ):
        if section not in prompt:
            continue
        before = prompt_chars({section: prompt.get(section)})
        if section == "candidate_policy_impacts":
            prompt[section] = compact_candidate_policy_impacts_for_latency(
                prompt.get(section),
                priority_symbols=_prompt_priority_symbols(prompt, limit=24),
                symbol_limit=20,
            )
        else:
            prompt[section] = compact_prompt_section(
                section,
                prompt.get(section),
                list_limit=list_limit,
                string_limit=string_limit,
            )
        after = prompt_chars({section: prompt.get(section)})
        if after < before:
            compacted.append(
                {
                    "section": section,
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    if prompt_chars(prompt) > effective_max:
        if "candidate_policy_impacts" in prompt:
            before = prompt_chars(
                {"candidate_policy_impacts": prompt.get("candidate_policy_impacts")}
            )
            value = prompt.get("candidate_policy_impacts")
            if isinstance(value, dict):
                compact_impacts: dict[str, Any] = {}
                if "_global" in value:
                    compact_impacts["_global"] = compact_prompt_value(
                        value.get("_global"),
                        list_limit=2,
                        string_limit=100,
                    )
                for key, rows in value.items():
                    if key == "_global":
                        continue
                    if len(compact_impacts) >= 25:
                        break
                    compact_impacts[str(key)] = compact_prompt_value(
                        rows,
                        list_limit=2,
                        string_limit=100,
                    )
                prompt["candidate_policy_impacts"] = compact_impacts
            else:
                prompt["candidate_policy_impacts"] = compact_prompt_value(
                    value,
                    list_limit=12,
                    string_limit=100,
                )
            after = prompt_chars(
                {"candidate_policy_impacts": prompt.get("candidate_policy_impacts")}
            )
            if after < before:
                compacted.append(
                    {
                        "section": "candidate_policy_impacts:dedupe_hard",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
        for section in (
            "decision_packet",
            "memory",
            "jue_wiki",
            "jue_wiki_requested_symbol_coverage",
            "jue_wiki_memory_card_quality",
            "blocks",
        ):
            if section not in prompt:
                continue
            before = prompt_chars({section: prompt.get(section)})
            prompt[section] = compact_prompt_section(
                section,
                prompt.get(section),
                list_limit=4,
                string_limit=110,
            )
            after = prompt_chars({section: prompt.get(section)})
            if after < before:
                compacted.append(
                    {
                        "section": f"{section}:hard",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
    if prompt_chars(prompt) > effective_max and "candidates" in prompt:
        before = prompt_chars({"candidates": prompt.get("candidates")})
        prompt["candidates"] = _compact_prompt_candidates_lane_diverse_section(
            prompt.get("candidates"),
            compact_value=compact_prompt_value,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
            list_limit=30,
            string_limit=160,
            priority_candidate_keys=priority_candidate_keys,
        )
        after = prompt_chars({"candidates": prompt.get("candidates")})
        if after < before:
            compacted.append(
                {
                    "section": "candidates:minimal",
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    if prompt_chars(prompt) > effective_max and "candidates" in prompt:
        before = prompt_chars({"candidates": prompt.get("candidates")})
        prompt["candidates"] = _compact_prompt_candidates_lane_diverse_section(
            prompt.get("candidates"),
            compact_value=compact_prompt_value,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
            list_limit=24,
            string_limit=120,
            priority_candidate_keys=priority_candidate_keys,
        )
        after = prompt_chars({"candidates": prompt.get("candidates")})
        if after < before:
            compacted.append(
                {
                    "section": "candidates:hard",
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    if prompt_chars(prompt) > effective_max and "candidates" in prompt:
        before = prompt_chars({"candidates": prompt.get("candidates")})
        prompt["candidates"] = _compact_prompt_candidates_lane_diverse_section(
            prompt.get("candidates"),
            compact_value=compact_prompt_value,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
            list_limit=12,
            string_limit=90,
            priority_candidate_keys=priority_candidate_keys,
        )
        after = prompt_chars({"candidates": prompt.get("candidates")})
        if after < before:
            compacted.append(
                {
                    "section": "candidates:emergency",
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    if compacted:
        prompt["prompt_compaction"] = {
            "version": "binance_prompt_compaction_v1",
            "max_chars": configured_max,
            "effective_max_chars": effective_max,
            "sections": compacted,
            "final_chars_before_budget": prompt_chars(prompt),
        }


def attach_prompt_budget(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    warn_chars: int,
    max_chars: int,
) -> None:
    build_attach_prompt_budget(
        prompt,
        target_chars=target_chars,
        warn_chars=warn_chars,
        max_chars=max_chars,
        section_size_rows=prompt_section_size_rows,
        prompt_chars=prompt_chars,
        policy=(
            "Keep Binance manager context broad but bounded; use compact "
            "candidate, memory, quant, and block evidence instead of raw dumps."
        ),
    )


def prompt_budget_error(prompt: dict[str, Any]) -> str:
    return build_prompt_budget_error(prompt)


def format_prompt_budget_alert_message(
    *,
    venue: str,
    run_id: int,
    error_message: str,
    prompt: dict[str, Any],
) -> str:
    return build_format_prompt_budget_alert_message(
        venue=venue,
        run_id=run_id,
        error_message=error_message,
        prompt=prompt,
    )


def extend_prompt_compaction(
    prompt: dict[str, Any],
    *,
    max_chars: int,
    effective_max_chars: int,
    sections: list[dict[str, Any]],
) -> None:
    if not sections:
        return
    current = (
        dict(prompt.get("prompt_compaction"))
        if isinstance(prompt.get("prompt_compaction"), dict)
        else {}
    )
    existing_sections = list(current.get("sections") or [])
    current.update(
        {
            "version": str(current.get("version") or "binance_prompt_compaction_v1"),
            "max_chars": int(current.get("max_chars") or max_chars),
            "effective_max_chars": int(
                current.get("effective_max_chars") or effective_max_chars
            ),
            "sections": [*existing_sections, *sections],
            "final_chars_before_budget": prompt_chars(prompt),
        }
    )
    prompt["prompt_compaction"] = current


def compact_manager_sections_for_final_budget(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    target = max(int(target_chars), 10_000)
    priority_candidate_keys = _manager_prompt_priority_candidate_keys(
        prompt,
        priority_candidate_keys,
    )

    def final_compact_section(
        section: str,
        value: Any,
        *,
        list_limit: int,
        string_limit: int,
    ) -> Any:
        if section == "critical_response_contract":
            return compact_prompt_value_bounded(
                value,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_limit=16,
            )
        if section == "jue_wiki_application":
            out = compact_prompt_value_bounded(
                value,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_limit=16,
            )
            if not isinstance(out, dict):
                return out
            source = value if isinstance(value, dict) else {}
            for key in ("validation_repair_effectiveness", "wiki_application_coverage"):
                if key not in source:
                    continue
                out[key] = compact_prompt_value_bounded(
                    source.get(key),
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_limit=8,
                )
            return out
        if section in {
            "candidate_generation",
            "entry_gate_policy",
            "live_authority",
            "diagnostics",
            "crypto_market_pulse",
            "validation_repair",
            "jue_wiki_requested_symbol_coverage",
        }:
            return compact_prompt_value_bounded(
                value,
                list_limit=list_limit,
                string_limit=string_limit,
                dict_limit=12,
            )
        if section == "proactive_decision_pressure":
            return compact_proactive_decision_pressure_for_latency(value)
        return compact_prompt_section(
            section,
            value,
            list_limit=list_limit,
            string_limit=string_limit,
        )

    for section, list_limit, string_limit in (
        ("critical_response_contract", 4, 90),
        ("blocks", 6, 70),
        ("candidates", 18, 80),
        ("candidate_policy_impacts", 12, 80),
        ("validation_repair", 4, 80),
        ("jue_wiki_application", 2, 60),
        ("jue_wiki_repair_contract", 4, 80),
        ("jue_wiki_requested_symbol_coverage", 2, 60),
        ("proactive_decision_pressure", 2, 60),
        ("diagnostics", 2, 60),
        ("recent_performance", 4, 80),
        ("memory", 2, 70),
        ("jue_wiki", 4, 180),
        ("candidate_generation", 2, 70),
        ("entry_gate_policy", 6, 80),
        ("live_authority", 2, 70),
        ("policy", 2, 70),
        ("performance", 2, 70),
        ("growth_unlock", 2, 70),
        ("execution_gate", 2, 70),
        ("crypto_market_pulse", 2, 70),
        ("decision_packet_v2", 2, 70),
        ("decision_packet", 2, 70),
        ("jue_workflow", 2, 60),
        ("account", 4, 70),
    ):
        if prompt_chars(prompt) <= target:
            break
        if section not in prompt:
            continue
        before = prompt_chars({section: prompt.get(section)})
        if section == "candidates":
            prompt[section] = _compact_prompt_candidates_lane_diverse_section(
                compact_prompt_candidates_minimal(
                    _runtime_prompt_sequence_items(prompt.get(section)),
                    compact_value=compact_prompt_value,
                    clean_text=lambda raw, text_limit: _clean_text(
                        raw,
                        limit=text_limit,
                    ),
                ),
                compact_value=compact_prompt_value,
                clean_text=lambda raw, text_limit: _clean_text(
                    raw,
                    limit=text_limit,
                ),
                list_limit=list_limit,
                string_limit=string_limit,
                priority_candidate_keys=priority_candidate_keys,
            )
        else:
            prompt[section] = final_compact_section(
                section,
                prompt.get(section),
                list_limit=list_limit,
                string_limit=string_limit,
            )
        after = prompt_chars({section: prompt.get(section)})
        if after < before:
            compacted.append(
                {
                    "section": f"{section}:final",
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    while prompt_chars(prompt) > target:
        candidates: list[tuple[int, str]] = []
        for section in BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS:
            if section not in prompt:
                continue
            value = prompt.get(section)
            if (
                isinstance(value, dict)
                and value.get("status") == "omitted_for_prompt_budget"
            ):
                continue
            before = prompt_chars({section: value})
            if before > 0:
                candidates.append((before, section))
        if not candidates:
            break
        before, section = max(candidates, key=lambda row: row[0])
        prompt[section] = {
            "status": "omitted_for_prompt_budget",
            "original_chars": before,
            "stage": "final_budget_guarantee",
        }
        after = prompt_chars({section: prompt.get(section)})
        compacted.append(
            {
                "section": f"{section}:final_omitted",
                "before_chars": before,
                "after_chars": after,
            }
        )
    if prompt_chars(prompt) > target:
        for section, list_limit, string_limit in (
            ("blocks", 3, 48),
            ("live_authority", 1, 48),
            ("account", 2, 48),
        ):
            if prompt_chars(prompt) <= target:
                break
            if section not in prompt:
                continue
            before = prompt_chars({section: prompt.get(section)})
            if section == "account":
                prompt[section] = compact_prompt_value_bounded(
                    prompt.get(section),
                    list_limit=list_limit,
                    string_limit=string_limit,
                    dict_limit=10,
                )
            else:
                prompt[section] = compact_prompt_section(
                    section,
                    prompt.get(section),
                    list_limit=list_limit,
                    string_limit=string_limit,
                )
            after = prompt_chars({section: prompt.get(section)})
            if after < before:
                compacted.append(
                    {
                        "section": f"{section}:core_minimal",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
    return compacted


def _compact_jue_wiki_application_for_warn_budget(value: Any) -> Any:
    if not isinstance(value, dict):
        return {}
    out = compact_prompt_value_bounded(
        value,
        list_limit=2,
        string_limit=50,
        dict_limit=12,
    )
    if not isinstance(out, dict):
        return out
    trust_profile = _compact_jue_wiki_trust_profile_for_storage(
        value.get("trust_profile"),
        string_limit=50,
        list_limit=2,
    )
    if trust_profile:
        out["trust_profile"] = trust_profile
    for key in ("validation_repair_effectiveness", "wiki_application_coverage"):
        if key not in value:
            continue
        out[key] = compact_prompt_value_bounded(
            value.get(key),
            list_limit=1,
            string_limit=50,
            dict_limit=4,
        )
    return out


def _manager_warn_budget_recovery_is_needed(
    prompt: dict[str, Any],
    *,
    warn_chars: int,
) -> bool:
    if prompt_chars(prompt) <= warn_chars:
        return False
    severe_total = prompt_chars(prompt) >= max(int(warn_chars) + 35_000, int(warn_chars * 1.6))
    if not severe_total:
        return False
    memory_chars = prompt_chars({"memory": prompt.get("memory")})
    wiki_application_chars = prompt_chars(
        {"jue_wiki_application": prompt.get("jue_wiki_application")}
    )
    return memory_chars >= 25_000 or wiki_application_chars >= 18_000


def compact_manager_sections_for_warn_budget(
    prompt: dict[str, Any],
    *,
    warn_chars: int,
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    target = max(int(warn_chars) - 2_000, 10_000)
    for section, compact_fn in (
        (
            "memory",
            lambda value: compact_prompt_section(
                "memory",
                value,
                list_limit=2,
                string_limit=70,
            ),
        ),
        ("jue_wiki_application", _compact_jue_wiki_application_for_warn_budget),
        (
            "validation_repair",
            lambda value: compact_prompt_value_bounded(
                value,
                list_limit=2,
                string_limit=60,
                dict_limit=12,
            ),
        ),
        (
            "jue_wiki_requested_symbol_coverage",
            lambda value: compact_prompt_value_bounded(
                value,
                list_limit=2,
                string_limit=60,
                dict_limit=12,
            ),
        ),
        (
            "proactive_decision_pressure",
            compact_proactive_decision_pressure_for_latency,
        ),
        (
            "diagnostics",
            lambda value: compact_prompt_value_bounded(
                value,
                list_limit=2,
                string_limit=60,
                dict_limit=12,
            ),
        ),
        (
            "candidate_generation",
            lambda value: compact_prompt_value_bounded(
                value,
                list_limit=2,
                string_limit=60,
                dict_limit=12,
            ),
        ),
        (
            "jue_wiki_repair_contract",
            lambda value: compact_prompt_section(
                "jue_wiki_repair_contract",
                value,
                list_limit=4,
                string_limit=70,
            ),
        ),
        (
            "crypto_market_pulse",
            lambda value: compact_prompt_value_bounded(
                value,
                list_limit=2,
                string_limit=60,
                dict_limit=12,
            ),
        ),
        (
            "entry_gate_policy",
            lambda value: compact_prompt_value_bounded(
                value,
                list_limit=2,
                string_limit=60,
                dict_limit=12,
            ),
        ),
    ):
        if prompt_chars(prompt) <= target:
            break
        if section not in prompt:
            continue
        before = prompt_chars({section: prompt.get(section)})
        prompt[section] = compact_fn(prompt.get(section))
        after = prompt_chars({section: prompt.get(section)})
        if after < before:
            compacted.append(
                {
                    "section": f"{section}:warn_recovery",
                    "before_chars": before,
                    "after_chars": after,
                }
            )
    return compacted


def latency_recovery_core_prompt(
    prompt: dict[str, Any],
    *,
    latency_guard: dict[str, Any],
    original_chars: int,
    label: str,
    priority_candidate_keys: set[tuple[str, str, str, str]] | None = None,
) -> dict[str, Any]:
    output_schema = compact_manager_output_schema_for_prompt(
        prompt.get("native_output_schema") or prompt.get("output_schema") or {}
    )
    priority_candidate_keys = _manager_prompt_priority_candidate_keys(
        prompt,
        priority_candidate_keys,
    )
    priority_symbols = _prompt_priority_symbols(prompt, limit=6)
    return {
        "native_thread_mode": prompt.get("native_thread_mode"),
        "native_thread_key": prompt.get("native_thread_key"),
        "decision_inputs": compact_prompt_value(
            prompt.get("decision_inputs") or [],
            list_limit=32,
            string_limit=100,
        ),
        "universe": compact_prompt_value(
            prompt.get("universe") or [],
            list_limit=12,
            string_limit=80,
        ),
        "_storage_compaction": manager_storage_compaction_meta(
            label=label,
            original_chars=original_chars,
            storage_limit_chars=max(int(latency_guard.get("target_chars") or 0), 0),
            retained_keys=list(prompt.keys()),
            emergency=True,
        ),
        "critical_response_contract": compact_prompt_section(
            "critical_response_contract",
            prompt.get("critical_response_contract") or {},
            list_limit=2,
            string_limit=60,
        ),
        "native_output_schema": compact_prompt_value(
            output_schema,
            list_limit=2,
            string_limit=60,
        ),
        "execution_gate": compact_prompt_section(
            "execution_gate",
            prompt.get("execution_gate") or {},
            list_limit=2,
            string_limit=50,
        ),
        "account": compact_prompt_value_bounded(
            prompt.get("account") or {},
            list_limit=2,
            string_limit=50,
            dict_limit=8,
        ),
        "growth_governor": compact_prompt_value_bounded(
            prompt.get("growth_governor") or {},
            list_limit=1,
            string_limit=50,
            dict_limit=8,
        ),
        "growth_unlock": compact_prompt_section(
            "growth_unlock",
            prompt.get("growth_unlock") or {},
            list_limit=1,
            string_limit=50,
        ),
        "live_authority": compact_prompt_value_bounded(
            prompt.get("live_authority") or {},
            list_limit=1,
            string_limit=50,
            dict_limit=8,
        ),
        "validation_repair": compact_prompt_value_bounded(
            prompt.get("validation_repair") or {},
            list_limit=1,
            string_limit=50,
            dict_limit=8,
        ),
        "jue_wiki": compact_jue_wiki_for_prompt(
            prompt.get("jue_wiki") or {},
            list_limit=1,
            string_limit=70,
        ),
        "jue_wiki_repair_contract": compact_jue_wiki_repair_contract_prompt(
            prompt.get("jue_wiki_repair_contract") or {},
            list_limit=1,
            string_limit=60,
        ),
        "proactive_decision_pressure": compact_proactive_decision_pressure_for_latency(
            prompt.get("proactive_decision_pressure")
        ),
        "candidate_generation": compact_prompt_value_bounded(
            prompt.get("candidate_generation") or {},
            list_limit=1,
            string_limit=50,
            dict_limit=8,
        ),
        "candidate_policy_impacts": compact_candidate_policy_impacts_for_latency(
            prompt.get("candidate_policy_impacts"),
            priority_symbols=priority_symbols,
            symbol_limit=4,
        ),
        "candidates": _compact_prompt_candidates_lane_diverse_section(
            compact_prompt_candidates_minimal(
                _runtime_prompt_sequence_items(prompt.get("candidates")),
                compact_value=compact_prompt_value,
                clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
            ),
            compact_value=compact_prompt_value,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
            list_limit=5,
            string_limit=50,
            priority_candidate_keys=priority_candidate_keys,
        ),
        "blocks": compact_prompt_section(
            "blocks",
            prompt.get("blocks") or [],
            list_limit=2,
            string_limit=50,
        ),
        "crypto_market_pulse": compact_prompt_value_bounded(
            prompt.get("crypto_market_pulse") or {},
            list_limit=1,
            string_limit=50,
            dict_limit=8,
        ),
        "market_universe": compact_prompt_section(
            "market_universe",
            prompt.get("market_universe") or {},
            list_limit=3,
            string_limit=40,
        ),
        "jue_workflow": _compact_manager_jue_workflow_prompt(
            prompt.get("jue_workflow") or {},
            list_limit=1,
            string_limit=40,
        ),
        "latency_guard": compact_prompt_value_bounded(
            latency_guard,
            list_limit=2,
            string_limit=70,
            dict_limit=12,
        ),
    }


def apply_manager_latency_guard(
    prompt: dict[str, Any],
    *,
    latency_guard: dict[str, Any],
    target_chars: int,
) -> None:
    if not bool(latency_guard.get("active")):
        prompt["latency_guard"] = latency_guard
        return

    configured_target = max(
        int(latency_guard.get("target_chars") or target_chars),
        10_000,
    )
    before_total = prompt_chars(prompt)
    sections: list[dict[str, Any]] = []
    timeout_count = _safe_int(latency_guard.get("recent_timeout_count"))
    priority_limit = 12 if timeout_count >= 3 else 16 if timeout_count >= 2 else 20
    policy_symbol_limit = 10 if timeout_count >= 3 else 14 if timeout_count >= 2 else 18
    candidate_limit = 16 if timeout_count >= 3 else 22 if timeout_count >= 2 else 28
    priority_symbols = _prompt_priority_symbols(prompt, limit=priority_limit)
    priority_candidate_keys = _manager_prompt_priority_candidate_keys(prompt)

    if "candidate_policy_impacts" in prompt:
        before = prompt_chars(
            {"candidate_policy_impacts": prompt.get("candidate_policy_impacts")}
        )
        prompt["candidate_policy_impacts"] = compact_candidate_policy_impacts_for_latency(
            prompt.get("candidate_policy_impacts"),
            priority_symbols=priority_symbols,
            symbol_limit=policy_symbol_limit,
        )
        after = prompt_chars(
            {"candidate_policy_impacts": prompt.get("candidate_policy_impacts")}
        )
        if after < before:
            sections.append(
                {
                    "section": "candidate_policy_impacts:latency_guard",
                    "before_chars": before,
                    "after_chars": after,
                }
            )

    if prompt_chars(prompt) > configured_target and "candidates" in prompt:
        before = prompt_chars({"candidates": prompt.get("candidates")})
        prompt["candidates"] = _compact_prompt_candidates_lane_diverse_section(
            prompt.get("candidates"),
            compact_value=compact_prompt_value,
            clean_text=lambda raw, text_limit: _clean_text(raw, limit=text_limit),
            list_limit=candidate_limit,
            string_limit=90 if timeout_count >= 2 else 110,
            priority_candidate_keys=priority_candidate_keys,
        )
        after = prompt_chars({"candidates": prompt.get("candidates")})
        if after < before:
            sections.append(
                {
                    "section": "candidates:latency_guard",
                    "before_chars": before,
                    "after_chars": after,
                }
            )

    if prompt_chars(prompt) > configured_target:
        sections.extend(
            compact_manager_sections_for_final_budget(
                prompt,
                target_chars=configured_target,
            )
        )

    if timeout_count >= 3 and prompt_chars(prompt) > configured_target:
        before = prompt_chars(prompt)
        recovery_prompt = latency_recovery_core_prompt(
            prompt,
            latency_guard=latency_guard,
            original_chars=before,
            label="binance_manager_latency_guard_core",
        )
        recovery_prompt = {
            key: value
            for key, value in recovery_prompt.items()
            if value not in ({}, [], "", None)
        }
        prompt.clear()
        prompt.update(recovery_prompt)
        sections.append(
            {
                "section": "latency_guard:recovery_core_prompt",
                "before_chars": before,
                "after_chars": prompt_chars(prompt),
            }
        )

    prompt["latency_guard"] = {
        **latency_guard,
        "active": True,
        "target_chars": configured_target,
        "configured_target_chars": latency_guard.get(
            "configured_target_chars",
            target_chars,
        ),
        "chars_before": before_total,
        "chars_after": prompt_chars(prompt),
        "kept_policy_symbols": priority_symbols[:policy_symbol_limit],
        "sections": sections,
    }

    inputs = prompt.get("decision_inputs")
    if isinstance(inputs, list) and "latency_guard" not in inputs:
        inputs.append("latency_guard")


def compact_manager_response_payload(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    keys = (
        "decision",
        "symbol",
        "market_or_account_scope",
        "horizon",
        "claim",
        "thesis",
        "entry_style",
        "entry_price",
        "target_price",
        "stop_price",
        "confidence",
        "reasons",
        "next_actions",
        "risks",
        "risk_note",
        "data_gaps",
        "evidence_refs",
    )
    compact = {
        key: compact_prompt_value(payload.get(key), string_limit=280, list_limit=8)
        for key in keys
        if payload.get(key) not in (None, "", [])
    }
    preserve_wiki_gate_storage_contracts(compact, response)
    return compact


def _manager_action_item_count(actions: dict[str, Any]) -> int:
    if not isinstance(actions, dict):
        return 0
    return sum(
        len(actions.get(key) or [])
        for key in BINANCE_MANAGER_ACTION_SECTIONS
        if isinstance(actions.get(key), list)
    )


def _manager_entry_repair_action_item_count(actions: dict[str, Any]) -> int:
    if not isinstance(actions, dict):
        return 0
    return sum(
        len(actions.get(key) or [])
        for key in ("create_blocks", "update_blocks")
        if isinstance(actions.get(key), list)
    )


def _manager_actions_have_wiki_attention_resolution(actions: dict[str, Any]) -> bool:
    return _manager_actions_have_wiki_repair_metadata(
        actions,
        metadata_keys=("jue_wiki_repair_attention",),
    )


def _manager_validation_repair_resolution_note_applies_constraints(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    source = str(value.get("source") or "").strip().lower()
    if source != "validation_repair_resolution":
        return False
    resolution = (
        value.get("resolution") if isinstance(value.get("resolution"), dict) else {}
    )
    kind = str(resolution.get("resolution") or "").strip().lower()
    if kind not in {
        "probe_waiting_block",
        "small_waiting_block",
        "one_share_probe",
        "updated_price_geometry",
        "candidate_rejected",
        "safety_gate_defer",
    }:
        return False
    if not _as_list(value.get("degraded_wiki_page_ids")):
        return False
    applied_note = resolution.get("memory_contract_resolution")
    next_trigger = resolution.get("next_trigger")
    return _manager_repair_note_is_concrete(
        {
            "resolution": kind,
            "memory_contract_resolution": applied_note,
            "next_trigger": next_trigger,
        }
    )


def _manager_action_repair_metadata_note_is_negative(value: Any) -> bool:
    ignored_keys = {
        "evidence_gap",
        "evidence_gaps",
        "data_gap",
        "data_gaps",
        "missing_evidence",
        "unresolved_evidence",
    }

    def without_gap_fields(raw: Any) -> Any:
        if isinstance(raw, dict):
            return {
                key: without_gap_fields(child)
                for key, child in raw.items()
                if str(key or "").strip().lower() not in ignored_keys
            }
        if isinstance(raw, list):
            return [without_gap_fields(child) for child in raw]
        if isinstance(raw, tuple):
            return tuple(without_gap_fields(child) for child in raw)
        if isinstance(raw, set):
            return {without_gap_fields(child) for child in raw}
        return raw

    if isinstance(value, dict):
        if _manager_validation_repair_resolution_note_applies_constraints(value):
            return False
        return _manager_repair_note_is_negative(without_gap_fields(value))
    return _manager_repair_note_is_negative(value)


def _manager_actions_have_wiki_repair_metadata(
    actions: dict[str, Any],
    *,
    metadata_keys: tuple[str, ...] = (
        "jue_wiki_repair_attention",
        "jue_wiki_repair_pressure",
        "jue_wiki_repair_resolution",
        "jue_wiki_memory_card_quality",
        "jue_wiki_memory_card_cross_check",
    ),
) -> bool:
    if not isinstance(actions, dict):
        return False
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in metadata_keys:
                repair_note = metadata.get(metadata_key)
                if repair_note in (None, "", [], {}):
                    repair_note = row.get(metadata_key)
                if _manager_action_repair_metadata_note_is_negative(repair_note):
                    continue
                if _manager_repair_note_is_concrete(repair_note):
                    return True
    return False


def _manager_repair_note_is_concrete(value: Any) -> bool:
    generic = {
        "handled",
        "done",
        "ok",
        "resolved",
        "checked",
        "considered",
        "repair pressure handled",
        "wiki repair handled",
        "처리",
        "처리함",
        "확인",
        "확인함",
    }
    if isinstance(value, dict):
        if not value:
            return False
        return any(_manager_repair_note_is_concrete(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_manager_repair_note_is_concrete(child) for child in value)
    text = str(value or "").strip().lower()
    if not text or text in generic:
        return False
    if len(text) < 12:
        return False
    concrete_terms = (
        "repair:",
        "cite_memory_and_apply",
        "source_id",
        "resolution",
        "action_metadata",
        "metadata_records",
        "memory contract",
        "repair_attention",
        "wiki_attention",
        "stale",
        "missing",
        "omitted",
        "reduced",
        "defer",
        "refresh",
        "coverage",
        "evidence",
        "financial",
        "narrative",
        "orderbook",
        "depth",
        "funding",
        "spread",
        "quality",
        "cross",
        "sizing",
        "confidence",
        "entry",
        "target",
        "stop",
        "rr>=",
        "rr >=",
        "reward/risk",
        "trigger",
        "waiting",
        "probe",
        "reject",
        "risk",
        "위키",
        "수리",
        "누락",
        "오래",
        "갱신",
        "축소",
        "보류",
        "대기",
        "근거",
        "위험",
        "리스크",
        "진입",
        "재진입",
        "기각",
        "재검토",
        "손절",
        "목표",
        "가격 구조",
        "가격구조",
        "메모리 계약",
        "신규 블록",
        "저신뢰",
    )
    return any(term in text for term in concrete_terms)


def _manager_repair_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_manager_repair_note_is_negative(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_manager_repair_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    text = re.sub(r"\b[a-z0-9_]*missing_from_model\b", " ", text)
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "still unresolved",
        "not resolved",
        "repair unresolved",
        "resolution missing",
        "repair missing",
        "not checked",
        "not cross checked",
        "no cross check",
        "no cross checks",
        "cross check missing",
        "cross checks missing",
        "no orderbook depth",
        "orderbook depth missing",
        "no book depth",
        "book depth missing",
        "no spread",
        "spread missing",
        "no funding",
        "funding missing",
        "no sizing reduction",
        "sizing reduction missing",
        "no risk gate",
        "risk gate missing",
        "미해결",
        "미확인",
        "수리 미완료",
        "해결 누락",
        "교차확인 없음",
        "교차 확인 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_prompt_has_active_validation_repair(prompt: dict[str, Any]) -> bool:
    repair = _manager_scoped_validation_repair(prompt)
    if bool(repair.get("hard_filter")):
        return False
    return (
        _safe_int(repair.get("repair_item_count")) > 0
        or _safe_int(repair.get("constraint_count")) > 0
    )


def _manager_prompt_has_jue_wiki_validation_repair_contract(
    prompt: dict[str, Any],
) -> bool:
    contract = _manager_scoped_jue_wiki_validation_repair_contract(prompt)
    if bool(contract.get("requires_validation_repair_resolution")):
        return True
    status = str(contract.get("status") or "").strip().lower()
    if status in {"repair_required", "degraded", "warning"}:
        return True
    if isinstance(contract.get("contract_feedback_gap"), dict):
        return True
    feedback_gap = _manager_scoped_jue_wiki_contract_feedback_gap(prompt)
    return bool(feedback_gap)


def _manager_prompt_has_wiki_repair_priorities(prompt: dict[str, Any]) -> bool:
    repair_contract = _manager_scoped_jue_wiki_repair_contract(prompt)
    if _as_list(repair_contract.get("action_batches")):
        return True
    if _as_list(repair_contract.get("top_priorities")):
        return True
    action_plan = repair_contract.get("repair_pressure_action_plan")
    action_plan = action_plan if isinstance(action_plan, dict) else {}
    for key in (
        "action_batch_total_count",
        "action_batch_omitted_count",
        "action_batch_visible_pressure_count",
        "omitted_priority_count",
        "total_priority_count",
    ):
        if _safe_int(action_plan.get(key)) > 0:
            return True
    return _safe_int(repair_contract.get("repair_priority_count")) > 0


def _manager_degraded_wiki_effectiveness_items(
    prompt: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(prompt, dict):
        return []
    jue_wiki = prompt.get("jue_wiki") if isinstance(prompt.get("jue_wiki"), dict) else {}
    rows: list[dict[str, Any]] = []
    for key in ("pages", "requested_symbol_summaries"):
        for item in _as_list(jue_wiki.get(key)):
            if not isinstance(item, dict):
                continue
            if not _manager_jue_wiki_prompt_item_matches_scope(item):
                continue
            effectiveness = (
                item.get("effectiveness")
                if isinstance(item.get("effectiveness"), dict)
                else {}
            )
            status = str(effectiveness.get("status") or "").strip().lower()
            if status != "degraded":
                continue
            rows.append(
                {
                    **item,
                    "effectiveness_reasons": list(
                        effectiveness.get("reasons") or []
                    ),
                }
            )
    for item in _as_list(jue_wiki.get("effectiveness_attention_items")):
        if not isinstance(item, dict):
            continue
        if not _manager_jue_wiki_prompt_item_matches_scope(item):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "degraded":
            rows.append(item)
    return rows


def _manager_prompt_has_degraded_wiki_effectiveness(prompt: dict[str, Any]) -> bool:
    return bool(_manager_degraded_wiki_effectiveness_items(prompt))


def _manager_prompt_has_wiki_attention_response_contract(
    prompt: dict[str, Any],
) -> bool:
    repair_contract = _manager_scoped_jue_wiki_repair_contract(prompt)
    contract = repair_contract.get("attention_plan_response_contract")
    contract = contract if isinstance(contract, dict) else {}
    if str(contract.get("status") or "").strip().lower() != "active":
        return False
    return bool(_as_list(contract.get("must_address")))


def _manager_prompt_has_wiki_selection_guidance(prompt: dict[str, Any]) -> bool:
    return bool(_manager_wiki_selection_guidance_terms(prompt))


def _manager_wiki_memory_item_matches_scope(
    row: dict[str, Any],
    *,
    inherited_translated: bool = False,
) -> bool:
    item_scope = str(
        row.get("memory_scope")
        or row.get("scope")
        or row.get("venue")
        or row.get("market")
        or ""
    ).strip().lower()
    translated = inherited_translated or _manager_wiki_memory_transferability_is_translated(row)
    if item_scope and not _manager_wiki_memory_scope_value_matches(item_scope):
        if not translated:
            return False
    policy_id = str(row.get("policy_id") or row.get("id") or "").strip()
    return translated or _manager_memory_mapping_key_matches_prompt_scope(policy_id)


def _manager_wiki_memory_scope_value_matches(value: str) -> bool:
    scope = (
        value.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("|", "_")
        .replace(":", "_")
    )
    if not scope:
        return True
    tokens = {token for token in scope.split("_") if token}
    if tokens.intersection({"global", "shared", "core"}):
        return True
    if "binance" in tokens or scope.startswith("binance"):
        return True
    if "crypto" in tokens or scope.startswith("crypto"):
        return True
    if tokens.intersection(
        {
            "kis",
            "krx",
            "kr",
            "korea",
            "korean",
            "domestic",
            "equity",
            "equities",
            "stock",
            "stocks",
        }
    ):
        return False
    return bool(tokens.intersection({"spot", "futures", "future", "perp", "perpetual"}))


def _manager_wiki_memory_transferability_is_translated(value: dict[str, Any]) -> bool:
    transferability = str(
        value.get("transferability")
        or value.get("translation_status")
        or value.get("cross_scope_status")
        or value.get("cross_venue_status")
        or ""
    ).strip().lower()
    if transferability in {"translated", "translation", "cross", "cross_venue"}:
        return True
    return bool(value.get("translated") is True or value.get("is_translated") is True)


def _manager_wiki_memory_container_matches_scope(container: dict[str, Any]) -> bool:
    target_scope = str(
        container.get("target_scope")
        or container.get("memory_scope")
        or container.get("scope")
        or container.get("venue")
        or container.get("market")
        or ""
    ).strip().lower()
    if _manager_wiki_memory_transferability_is_translated(container):
        return True
    return _manager_wiki_memory_scope_value_matches(target_scope)


def _manager_jue_wiki_prompt_item_matches_scope(item: dict[str, Any]) -> bool:
    if _manager_wiki_memory_transferability_is_translated(item):
        return True
    item_scope = str(
        item.get("target_scope")
        or item.get("memory_scope")
        or item.get("scope")
        or item.get("venue")
        or item.get("market")
        or ""
    ).strip().lower()
    if item_scope and not _manager_wiki_memory_scope_value_matches(item_scope):
        return False
    page_id = str(item.get("page_id") or item.get("id") or "").strip()
    return _manager_memory_mapping_key_matches_prompt_scope(page_id)


def _manager_scoped_memory_card_quality(prompt: dict[str, Any]) -> dict[str, Any]:
    quality = (
        prompt.get("jue_wiki_memory_card_quality")
        if isinstance(prompt, dict)
        else {}
    )
    quality = quality if isinstance(quality, dict) else {}
    if quality and not _manager_wiki_memory_container_matches_scope(quality):
        return {}
    return quality


def _manager_scoped_jue_wiki_repair_contract(prompt: dict[str, Any]) -> dict[str, Any]:
    repair_contract = (
        prompt.get("jue_wiki_repair_contract") if isinstance(prompt, dict) else {}
    )
    repair_contract = repair_contract if isinstance(repair_contract, dict) else {}
    if repair_contract and not _manager_wiki_memory_container_matches_scope(
        repair_contract
    ):
        return {}
    if not repair_contract:
        return {}
    contract = dict(repair_contract)
    top_priorities = [
        row
        for row in _as_list(repair_contract.get("top_priorities"))
        if not isinstance(row, dict) or _manager_jue_wiki_prompt_item_matches_scope(row)
    ]
    action_batches = [
        row
        for row in _as_list(repair_contract.get("action_batches"))
        if not isinstance(row, dict) or _manager_jue_wiki_prompt_item_matches_scope(row)
    ]
    if "top_priorities" in repair_contract:
        contract["top_priorities"] = top_priorities
        original_priority_count = _safe_int(
            repair_contract.get("repair_priority_count")
        )
        contract["repair_priority_count"] = (
            max(original_priority_count, len(top_priorities)) if top_priorities else 0
        )
    if "action_batches" in repair_contract:
        contract["action_batches"] = action_batches
        visible_count = sum(
            max(_safe_int(row.get("count")), 0)
            for row in action_batches
            if isinstance(row, dict)
        ) or len(action_batches)
        contract["action_batch_total_count"] = (
            max(_safe_int(repair_contract.get("action_batch_total_count")), len(action_batches))
            if action_batches
            else 0
        )
        contract["action_batch_omitted_count"] = (
            _safe_int(repair_contract.get("action_batch_omitted_count"))
            if action_batches
            else 0
        )
        contract["action_batch_visible_pressure_count"] = (
            max(
                _safe_int(repair_contract.get("action_batch_visible_pressure_count")),
                visible_count,
            )
            if action_batches
            else 0
        )
        action_plan = (
            dict(repair_contract.get("repair_pressure_action_plan"))
            if isinstance(repair_contract.get("repair_pressure_action_plan"), dict)
            else {}
        )
        if action_plan:
            action_plan["action_batch_total_count"] = contract[
                "action_batch_total_count"
            ]
            action_plan["action_batch_omitted_count"] = contract[
                "action_batch_omitted_count"
            ]
            action_plan["action_batch_visible_pressure_count"] = contract[
                "action_batch_visible_pressure_count"
            ]
            contract["repair_pressure_action_plan"] = action_plan
    attention_contract = contract.get("attention_plan_response_contract")
    if (
        isinstance(attention_contract, dict)
        and not _manager_jue_wiki_prompt_item_matches_scope(attention_contract)
    ):
        contract["attention_plan_response_contract"] = {}
    return contract


def _manager_scoped_validation_repair(prompt: dict[str, Any]) -> dict[str, Any]:
    repair = prompt.get("validation_repair") if isinstance(prompt, dict) else {}
    repair = repair if isinstance(repair, dict) else {}
    if repair and not _manager_wiki_memory_container_matches_scope(repair):
        return {}
    return repair


def _manager_scoped_proactive_decision_pressure(prompt: dict[str, Any]) -> dict[str, Any]:
    pressure = (
        prompt.get("proactive_decision_pressure") if isinstance(prompt, dict) else {}
    )
    pressure = pressure if isinstance(pressure, dict) else {}
    if pressure and not _manager_wiki_memory_container_matches_scope(pressure):
        return {}
    return pressure


def _manager_scoped_jue_wiki_action_pressure_contract(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    contract = (
        prompt.get("jue_wiki_action_pressure_contract")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if contract and not _manager_wiki_memory_container_matches_scope(contract):
        return {}
    return contract


def _manager_requested_symbol_coverage_has_explicit_scope(
    coverage: dict[str, Any],
) -> bool:
    return any(
        coverage.get(key) not in ({}, [], "", None)
        for key in ("target_scope", "memory_scope", "scope", "venue", "market")
    )


def _manager_requested_symbol_coverage_symbols(
    coverage: dict[str, Any],
) -> tuple[list[str], list[str]]:
    raw_symbols: list[str] = []
    crypto_symbols: list[str] = []

    def add(value: Any) -> None:
        raw = _clean_text(value, limit=80).upper()
        if not raw:
            return
        if raw not in raw_symbols:
            raw_symbols.append(raw)
        token = _compact_requested_crypto_symbol_token(raw)
        if token and token not in crypto_symbols:
            crypto_symbols.append(token)

    for key in (
        "missing_summary_symbols",
        "unsummarized_symbols",
        "prompt_omitted_symbols",
        "degraded_summary_symbols",
    ):
        for item in _as_list(coverage.get(key)):
            add(item)
    for item in _as_list(coverage.get("degraded_summary_reasons")):
        if isinstance(item, dict):
            add(item.get("symbol"))
    return raw_symbols, crypto_symbols


def _manager_requested_symbol_coverage_matches_scope(coverage: dict[str, Any]) -> bool:
    if not _manager_wiki_memory_container_matches_scope(coverage):
        return False
    if (
        _manager_requested_symbol_coverage_has_explicit_scope(coverage)
        or _manager_wiki_memory_transferability_is_translated(coverage)
    ):
        return True
    raw_symbols, crypto_symbols = _manager_requested_symbol_coverage_symbols(coverage)
    if not raw_symbols:
        return True
    return len(crypto_symbols) == len(raw_symbols)


def _manager_scoped_requested_symbol_coverage(prompt: dict[str, Any]) -> dict[str, Any]:
    coverage = (
        prompt.get("jue_wiki_requested_symbol_coverage")
        if isinstance(prompt, dict)
        else {}
    )
    coverage = coverage if isinstance(coverage, dict) else {}
    if coverage and _manager_requested_symbol_coverage_matches_scope(coverage):
        return coverage
    jue_wiki = prompt.get("jue_wiki") if isinstance(prompt, dict) else {}
    jue_wiki = jue_wiki if isinstance(jue_wiki, dict) else {}
    nested = jue_wiki.get("requested_symbol_coverage")
    nested = nested if isinstance(nested, dict) else {}
    if not nested:
        return {}
    scoped = dict(nested)
    if not any(
        scoped.get(key) not in ({}, [], "", None)
        for key in ("target_scope", "memory_scope", "scope", "venue", "market")
    ):
        parent_scope = str(jue_wiki.get("target_scope") or "").strip()
        if parent_scope:
            scoped["target_scope"] = parent_scope
    if not _manager_requested_symbol_coverage_matches_scope(scoped):
        return {}
    return scoped


def _manager_scoped_jue_wiki_validation_repair_contract(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    contract = (
        prompt.get("jue_wiki_validation_repair_contract")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if contract and not _manager_wiki_memory_container_matches_scope(contract):
        return {}
    return contract


def _manager_scoped_jue_wiki_contract_feedback_gap(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    feedback_gap = (
        prompt.get("jue_wiki_contract_feedback_gap") if isinstance(prompt, dict) else {}
    )
    feedback_gap = feedback_gap if isinstance(feedback_gap, dict) else {}
    if feedback_gap and not _manager_wiki_memory_container_matches_scope(feedback_gap):
        return {}
    return feedback_gap


def _manager_operational_scope_is_kis_only(value: str) -> bool:
    scope = (
        value.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("|", "_")
        .replace(":", "_")
    )
    if not scope or _manager_wiki_memory_scope_value_matches(scope):
        return False
    tokens = {token for token in scope.split("_") if token}
    if tokens.intersection({"kis", "krx"}):
        return True
    if any(
        marker in scope
        for marker in (
            "kis",
            "krx",
            "kr_equity",
            "korean_equity",
            "domestic_equity",
            "kr_stock",
            "korean_stock",
            "domestic_stock",
            "국장",
            "한국주식",
        )
    ):
        return True
    return bool(
        tokens.intersection({"equity", "stock", "stocks"})
        and tokens.intersection({"kr", "korean", "domestic"})
    )


def _manager_scoped_operational_prompt_section(
    prompt: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    section = prompt.get(key) if isinstance(prompt, dict) else {}
    section = section if isinstance(section, dict) else {}
    if not section or _manager_wiki_memory_transferability_is_translated(section):
        return section
    for scope_key in ("target_scope", "memory_scope", "scope", "venue", "market"):
        scope_value = str(section.get(scope_key) or "").strip()
        if scope_value and _manager_operational_scope_is_kis_only(scope_value):
            return {}
    return section


def _manager_prompt_has_wiki_action_reference_memory(prompt: dict[str, Any]) -> bool:
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    status = str(reference_memory.get("status") or "").strip().lower()
    if not _manager_wiki_memory_container_matches_scope(reference_memory):
        return False
    translated = _manager_wiki_memory_transferability_is_translated(reference_memory)
    items = _as_list(reference_memory.get("items"))
    return status == "available" and any(
        isinstance(row, dict)
        and _manager_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        )
        for row in items
    )


def _manager_wiki_action_reference_recovery_diagnostics(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    recovery = (
        memory.get("jue_wiki_action_reference_recovery")
        if isinstance(memory.get("jue_wiki_action_reference_recovery"), dict)
        else {}
    )
    if recovery and not _manager_wiki_memory_container_matches_scope(recovery):
        recovery = {}
    if not recovery:
        reference_memory = (
            memory.get("jue_wiki_action_reference_memory")
            if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
            else {}
        )
        if not _manager_wiki_memory_container_matches_scope(reference_memory):
            return {}
        translated = _manager_wiki_memory_transferability_is_translated(reference_memory)
        has_recovery_guidance = any(
            "unresolved_recovery" in _json_dumps(row).lower()
            or "resolve_action_reference_recovery" in _json_dumps(row).lower()
            for row in _as_list(reference_memory.get("items"))
            if isinstance(row, dict)
            and _manager_wiki_memory_item_matches_scope(
                row,
                inherited_translated=translated,
            )
        )
        if not has_recovery_guidance:
            return {}
        return {
            "jue_wiki_action_reference_recovery_status": "unresolved",
            "jue_wiki_action_reference_recovery_memory_scope": "binance",
            "jue_wiki_action_reference_recovery_open_gap_count": 1,
            "jue_wiki_action_reference_recovery_resolved_count": 0,
            "jue_wiki_action_reference_recovery_total_count": 1,
            "jue_wiki_action_reference_recovery_ratio": 0.0,
            "jue_wiki_action_reference_recovery_latest_resolution_status": (
                "unresolved"
            ),
            "jue_wiki_action_reference_recovery_latest_status": "missing",
        }
    return {
        "jue_wiki_action_reference_recovery_status": str(
            recovery.get("status") or ""
        ).strip(),
        "jue_wiki_action_reference_recovery_memory_scope": str(
            recovery.get("memory_scope") or ""
        ).strip(),
        "jue_wiki_action_reference_recovery_open_gap_count": _safe_int(
            recovery.get("open_gap_count")
        ),
        "jue_wiki_action_reference_recovery_resolved_count": _safe_int(
            recovery.get("resolved_count")
        ),
        "jue_wiki_action_reference_recovery_total_count": _safe_int(
            recovery.get("total_count")
        ),
        "jue_wiki_action_reference_recovery_ratio": round(
            _safe_float(recovery.get("recovery_ratio")),
            4,
        ),
        "jue_wiki_action_reference_recovery_latest_resolution_status": str(
            recovery.get("latest_resolution_status") or ""
        ).strip(),
        "jue_wiki_action_reference_recovery_latest_status": str(
            recovery.get("latest_status") or ""
        ).strip(),
    }


def _manager_prompt_has_wiki_action_reference_recovery_guidance(
    prompt: dict[str, Any],
) -> bool:
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    recovery = (
        memory.get("jue_wiki_action_reference_recovery")
        if isinstance(memory.get("jue_wiki_action_reference_recovery"), dict)
        else {}
    )
    if recovery and _manager_wiki_memory_container_matches_scope(recovery):
        return True
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    if not _manager_wiki_memory_container_matches_scope(reference_memory):
        return False
    translated = _manager_wiki_memory_transferability_is_translated(reference_memory)
    for row in _as_list(reference_memory.get("items")):
        if not isinstance(row, dict) or not _manager_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        ):
            continue
        text = _json_dumps(row).lower()
        if (
            "unresolved_recovery" in text
            or "resolve_action_reference_recovery" in text
            or "jue_wiki_action_reference_recovery" in text
        ):
            return True
    return False


def _manager_resolved_wiki_action_reference_recovery_diagnostics(
    recovery: dict[str, Any],
    *,
    resolution_status: str,
) -> dict[str, Any]:
    result = dict(recovery)
    total_count = max(
        _safe_int(result.get("jue_wiki_action_reference_recovery_total_count")),
        1,
    )
    resolved_count = max(
        _safe_int(result.get("jue_wiki_action_reference_recovery_resolved_count")),
        total_count,
    )
    result.update(
        {
            "jue_wiki_action_reference_recovery_status": "resolved",
            "jue_wiki_action_reference_recovery_open_gap_count": 0,
            "jue_wiki_action_reference_recovery_resolved_count": resolved_count,
            "jue_wiki_action_reference_recovery_total_count": total_count,
            "jue_wiki_action_reference_recovery_ratio": 1.0,
            "jue_wiki_action_reference_recovery_latest_resolution_status": (
                resolution_status
            ),
            "jue_wiki_action_reference_recovery_latest_status": (
                "referenced" if resolution_status == "action_metadata" else "no_actions"
            ),
        }
    )
    return result


def _manager_wiki_action_reference_terms(prompt: dict[str, Any]) -> list[str]:
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _manager_wiki_memory_container_matches_scope(reference_memory):
        return terms
    translated = _manager_wiki_memory_transferability_is_translated(reference_memory)

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    for row in _as_list(reference_memory.get("items")):
        if not isinstance(row, dict):
            continue
        if not _manager_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        ):
            continue
        for key in ("policy_id", "latest_status"):
            add(row.get(key))
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        for key in ("status", "manager_instruction", "required_evidence"):
            value = guidance.get(key)
            if isinstance(value, list):
                for item in value:
                    add(item)
            else:
                add(value)
    for term in (
        "fresh_jue_wiki_context",
        "jue_wiki_freshness_cross_check",
        "jue_wiki_selection_resolution",
        "jue_wiki_reference_basis",
        "live_cross_check",
        "book",
        "spread",
        "funding",
        "crypto_quant",
        "research",
    ):
        add(term)
    return terms


def _manager_wiki_action_reference_translation_terms(prompt: dict[str, Any]) -> list[str]:
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    reference_memory = (
        memory.get("jue_wiki_action_reference_memory")
        if isinstance(memory.get("jue_wiki_action_reference_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _manager_wiki_memory_container_matches_scope(reference_memory):
        return terms
    inherited_translated = _manager_wiki_memory_transferability_is_translated(
        reference_memory
    )

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    has_translated_memory = False
    for row in _as_list(reference_memory.get("items")):
        if not isinstance(row, dict):
            continue
        row_translated = (
            inherited_translated
            or _manager_wiki_memory_transferability_is_translated(row)
        )
        if not row_translated or not _manager_wiki_memory_item_matches_scope(
            row,
            inherited_translated=inherited_translated,
        ):
            continue
        has_translated_memory = True
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        for value in _as_list(guidance.get("required_evidence")):
            add(value)
    if has_translated_memory:
        for term in (
            "translated_crypto_mapping",
            "crypto_translation_mapping",
            "cross_venue_mapping",
            "cross_scope_mapping",
            "translated_policy_context",
        ):
            add(term)
    return terms


def _manager_payload_resolves_wiki_action_reference_terms(
    *,
    payload: Any,
    terms: list[str],
    translation_terms: list[str],
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    action_symbols: set[str] | None = None,
    require_selected_symbol_page_reference: bool = False,
) -> bool:
    if not _manager_payload_uses_only_allowed_wiki_page_ids(
        payload,
        allowed_page_ids=allowed_page_ids,
    ):
        return False
    if require_selected_symbol_page_reference and not (
        _manager_payload_has_selected_symbol_wiki_page_reference(
            payload,
            required_symbol_page_ids=required_symbol_page_ids,
        )
    ):
        return False
    if not _manager_payload_wiki_page_ids_match_action_symbols(
        payload,
        action_symbols=action_symbols,
        required_symbol_page_ids=required_symbol_page_ids,
    ):
        return False
    if terms and not _manager_payload_mentions_any_term(payload, terms):
        return False
    if translation_terms and not _manager_payload_mentions_any_term(
        payload,
        translation_terms,
    ):
        return False
    return True


def _manager_wiki_selection_guidance_terms(prompt: dict[str, Any]) -> list[str]:
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    selection_memory = (
        memory.get("jue_wiki_selection_memory")
        if isinstance(memory.get("jue_wiki_selection_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _manager_wiki_memory_container_matches_scope(selection_memory):
        return terms
    translated = _manager_wiki_memory_transferability_is_translated(selection_memory)

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    for row in _as_list(selection_memory.get("items")):
        if not isinstance(row, dict):
            continue
        if not _manager_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        ):
            continue
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        if str(guidance.get("status") or "").strip().lower() != (
            "freshness_repair_required"
        ):
            continue
        for key in ("policy_id", "primary_reason", "selected_page_ids"):
            value = row.get(key)
            if isinstance(value, list):
                for item in value:
                    add(item)
            else:
                add(value)
        for key in (
            "status",
            "manager_instruction",
            "required_evidence",
            "cross_check_page_ids",
        ):
            value = guidance.get(key)
            if isinstance(value, list):
                for item in value:
                    add(item)
            else:
                add(value)
    return terms


def _manager_wiki_selection_guidance_translation_terms(prompt: dict[str, Any]) -> list[str]:
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    selection_memory = (
        memory.get("jue_wiki_selection_memory")
        if isinstance(memory.get("jue_wiki_selection_memory"), dict)
        else {}
    )
    terms: list[str] = []
    if not _manager_wiki_memory_container_matches_scope(selection_memory):
        return terms
    inherited_translated = _manager_wiki_memory_transferability_is_translated(
        selection_memory
    )

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    has_translated_memory = False
    for row in _as_list(selection_memory.get("items")):
        if not isinstance(row, dict):
            continue
        row_translated = (
            inherited_translated
            or _manager_wiki_memory_transferability_is_translated(row)
        )
        if not row_translated or not _manager_wiki_memory_item_matches_scope(
            row,
            inherited_translated=inherited_translated,
        ):
            continue
        guidance = (
            row.get("application_guidance")
            if isinstance(row.get("application_guidance"), dict)
            else {}
        )
        if str(guidance.get("status") or "").strip().lower() != (
            "freshness_repair_required"
        ):
            continue
        has_translated_memory = True
        for value in _as_list(guidance.get("required_evidence")):
            text = str(value or "").strip().lower()
            if any(marker in text for marker in ("translat", "mapping", "cross_")):
                add(value)
    if has_translated_memory:
        for term in (
            "translated_crypto_mapping",
            "crypto_translation_mapping",
            "cross_venue_mapping",
            "cross_scope_mapping",
            "translated_policy_context",
        ):
            add(term)
    return terms


def _manager_payload_resolves_wiki_selection_guidance_terms(
    *,
    payload: Any,
    terms: list[str],
    translation_terms: list[str],
) -> bool:
    if _manager_wiki_selection_guidance_note_is_negative(payload):
        return False
    if terms and not _manager_payload_mentions_any_term(payload, terms):
        return False
    if translation_terms and not _manager_payload_mentions_any_term(
        payload,
        translation_terms,
    ):
        return False
    return True


def _manager_wiki_selection_guidance_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_wiki_selection_guidance_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_manager_wiki_selection_guidance_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "selection unresolved",
        "selection guidance unresolved",
        "selection resolution missing",
        "selection audit resolution missing",
        "fresh jue wiki context missing",
        "fresh wiki context missing",
        "fresh context missing",
        "wiki freshness missing",
        "no fresh jue wiki context",
        "no fresh wiki context",
        "no fresh context",
        "no live cross check",
        "no live cross checks",
        "without live cross check",
        "without live cross checks",
        "live cross check missing",
        "live cross checks missing",
        "not cross checked",
        "not checked",
        "미해결",
        "미확인",
        "신선도 미확인",
        "위키 신선도 미확인",
        "교차확인 없음",
        "교차 확인 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_prompt_has_requested_symbol_coverage_gap(
    prompt: dict[str, Any],
) -> bool:
    coverage = _manager_scoped_requested_symbol_coverage(prompt)
    status = str(coverage.get("status") or "").strip().lower()
    if status not in {"partial", "none"}:
        return False
    if _as_list(coverage.get("missing_summary_symbols")):
        return True
    if "missing_summary_symbols" in coverage:
        return False
    return bool(_as_list(coverage.get("unsummarized_symbols")))


def _manager_prompt_has_memory_card_quality_gap(prompt: dict[str, Any]) -> bool:
    quality = _manager_scoped_memory_card_quality(prompt)
    action_plan = (
        quality.get("action_plan") if isinstance(quality.get("action_plan"), dict) else {}
    )
    if str(action_plan.get("status") or "").strip().lower() == "active":
        return bool(
            _as_list(action_plan.get("symbols"))
            or str(action_plan.get("required_action") or "").strip()
        )
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    if _as_list(summary.get("weak_symbols")):
        return True
    status_counts = (
        summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {}
    )
    if _safe_int(status_counts.get("weak")) > 0:
        return True
    return _manager_memory_card_quality_gap_summary_is_active(
        _manager_repair_contract_memory_card_quality_gap_summary(prompt)
    )


def _manager_repair_contract_memory_card_quality_gap_summary(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    repair_contract = _manager_scoped_jue_wiki_repair_contract(prompt)
    loop = repair_contract.get("repair_loop_effectiveness")
    loop = loop if isinstance(loop, dict) else {}
    if loop and not _manager_wiki_memory_container_matches_scope(loop):
        return {}
    gap_summary = loop.get("memory_card_quality_gap_summary")
    gap_summary = gap_summary if isinstance(gap_summary, dict) else {}
    if gap_summary and not _manager_wiki_memory_container_matches_scope(gap_summary):
        return {}
    return gap_summary


def _manager_memory_card_quality_gap_summary_is_active(
    gap_summary: dict[str, Any],
) -> bool:
    if not isinstance(gap_summary, dict) or not gap_summary:
        return False
    status = str(gap_summary.get("status") or "").strip().lower()
    if status in {"repair_required", "warning", "degraded"}:
        return True
    if _as_list(gap_summary.get("priority_missing_fields")):
        return True
    if _as_list(gap_summary.get("priority_required_checks")):
        return True
    if _manager_memory_card_quality_focus_is_active(
        gap_summary.get("priority_focus")
    ):
        return True
    for key in (
        "missing_field_missed_counts",
        "required_check_missed_counts",
    ):
        counts = gap_summary.get(key) if isinstance(gap_summary.get(key), dict) else {}
        if any(_safe_int(value) > 0 for value in counts.values()):
            return True
    for key in ("top_missing_fields", "top_required_checks"):
        for row in _as_list(gap_summary.get(key)):
            if isinstance(row, dict) and _safe_int(row.get("missed_count")) > 0:
                return True
    return False


def _manager_memory_card_quality_focus_is_active(value: Any) -> bool:
    focus = value if isinstance(value, dict) else {}
    if not focus:
        return False
    if not (
        str(focus.get("missing_field") or "").strip()
        or str(focus.get("required_check") or "").strip()
    ):
        return False
    count_keys = (
        "missing_field_missed_count",
        "required_check_missed_count",
    )
    present_counts = [
        _safe_int(focus.get(key))
        for key in count_keys
        if focus.get(key) not in (None, "", [], {})
    ]
    if present_counts:
        return any(count > 0 for count in present_counts)
    return True


def _manager_memory_card_quality_gap_summary_required_terms(
    gap_summary: dict[str, Any],
) -> tuple[list[str], bool]:
    terms: list[str] = []
    priority_terms: list[str] = []
    missed_terms: list[str] = []

    def add(target: list[str], value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in target:
            target.append(text)

    for field in _as_list(gap_summary.get("priority_missing_fields"))[:1]:
        add(priority_terms, field)
    for check in _as_list(gap_summary.get("priority_required_checks"))[:1]:
        add(priority_terms, check)
    if priority_terms:
        return priority_terms, True

    focus = (
        gap_summary.get("priority_focus")
        if isinstance(gap_summary.get("priority_focus"), dict)
        else {}
    )
    add(priority_terms, focus.get("missing_field"))
    add(priority_terms, focus.get("required_check"))
    if priority_terms:
        return priority_terms, True

    for counts_key in (
        "missing_field_counts",
        "missing_field_missed_counts",
        "required_check_counts",
        "required_check_missed_counts",
    ):
        counts = (
            gap_summary.get(counts_key)
            if isinstance(gap_summary.get(counts_key), dict)
            else {}
        )
        for field, count in counts.items():
            if counts_key.endswith("_missed_counts") and _safe_int(count) > 0:
                add(missed_terms, field)
            add(terms, field)
    for row in _as_list(gap_summary.get("top_missing_fields")):
        if not isinstance(row, dict):
            continue
        field = row.get("field")
        if _safe_int(row.get("missed_count")) > 0:
            add(missed_terms, field)
        add(terms, field)
    for row in _as_list(gap_summary.get("top_required_checks")):
        if not isinstance(row, dict):
            continue
        check = row.get("check")
        if _safe_int(row.get("missed_count")) > 0:
            add(missed_terms, check)
        add(terms, check)
    if missed_terms:
        return missed_terms, True
    return terms, False


def _manager_memory_card_quality_gap_summary_display_terms(
    gap_summary: dict[str, Any],
    *,
    top_key: str,
    item_key: str,
    count_key: str,
    missed_count_key: str,
    limit: int = 12,
) -> list[str]:
    terms: list[str] = []
    missed_terms: list[str] = []

    def add(target: list[str], value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in target:
            target.append(text)

    priority_source_key = (
        "priority_missing_fields"
        if item_key == "field"
        else "priority_required_checks"
    )
    priority_terms: list[str] = []
    for field in _as_list(gap_summary.get(priority_source_key)):
        add(priority_terms, field)
    if priority_terms:
        return priority_terms[: max(int(limit), 1)]
    focus = (
        gap_summary.get("priority_focus")
        if isinstance(gap_summary.get("priority_focus"), dict)
        else {}
    )
    focus_key = "missing_field" if item_key == "field" else "required_check"
    add(priority_terms, focus.get(focus_key))
    if priority_terms:
        return priority_terms[: max(int(limit), 1)]

    missed_counts = (
        gap_summary.get(missed_count_key)
        if isinstance(gap_summary.get(missed_count_key), dict)
        else {}
    )
    for field, count in missed_counts.items():
        if _safe_int(count) > 0:
            add(missed_terms, field)
    counts = (
        gap_summary.get(count_key)
        if isinstance(gap_summary.get(count_key), dict)
        else {}
    )
    for field in counts:
        add(terms, field)
    for row in _as_list(gap_summary.get(top_key)):
        if not isinstance(row, dict):
            continue
        value = row.get(item_key)
        if _safe_int(row.get("missed_count")) > 0:
            add(missed_terms, value)
        add(terms, value)
    return (missed_terms or terms)[: max(int(limit), 1)]


def _manager_prompt_has_action_pressure(prompt: dict[str, Any]) -> bool:
    pressure = _manager_scoped_proactive_decision_pressure(prompt)
    return str(pressure.get("status") or "").strip().lower() == "action_required"


def _manager_prompt_has_binance_activity_gap_pressure(prompt: dict[str, Any]) -> bool:
    pressure = _manager_scoped_proactive_decision_pressure(prompt)
    if str(pressure.get("status") or "").strip().lower() != "action_required":
        return False
    gap = (
        pressure.get("binance_market_activity_gap")
        if isinstance(pressure.get("binance_market_activity_gap"), dict)
        else {}
    )
    status = str(gap.get("status") or "").strip().lower()
    return status in {"no_binance_entries", "stale_binance_entries"}


def _manager_binance_activity_gap_target_symbols(prompt: dict[str, Any]) -> set[str]:
    pressure = _manager_scoped_proactive_decision_pressure(prompt)
    gap = (
        pressure.get("binance_market_activity_gap")
        if isinstance(pressure.get("binance_market_activity_gap"), dict)
        else {}
    )
    symbols: set[str] = {
        str(symbol or "").upper().strip()
        for symbol in _as_list(gap.get("candidate_symbols"))
        if str(symbol or "").strip()
    }
    for row in _as_list(gap.get("candidate_snapshots")):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol:
            symbols.add(symbol)
    for row in _as_list(pressure.get("top_candidates")):
        if not isinstance(row, dict):
            continue
        market = normalize_market(row.get("market") or row.get("venue"))
        symbol = str(row.get("symbol") or "").upper().strip()
        if market in {"spot", "futures"} and symbol:
            symbols.add(symbol)
    return symbols


def _manager_action_resolves_binance_activity_gap(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    target_symbols = _manager_binance_activity_gap_target_symbols(prompt)
    block_symbols_by_id, block_pairs_by_id, _block_by_id = (
        _manager_prompt_block_identity_context(prompt)
    )
    for section in ("adopt_existing_blocks", "create_blocks", "update_blocks"):
        for row in _as_list(actions.get(section)):
            if not isinstance(row, dict):
                continue
            raw_market = row.get("market") or row.get("venue")
            explicit_market = explicit_market_scope(raw_market)
            if str(raw_market or "").strip() and explicit_market not in {
                "spot",
                "futures",
            }:
                continue
            for symbol, market in _manager_action_symbol_market_identities(
                row,
                block_symbols_by_id=block_symbols_by_id,
                block_pairs_by_id=block_pairs_by_id,
            ):
                symbol = str(symbol or "").upper().strip()
                if not symbol or symbol.startswith("KRW-"):
                    continue
                if target_symbols and symbol not in target_symbols:
                    continue
                if explicit_market in {"spot", "futures"}:
                    return True
                if market in {"spot", "futures"}:
                    return True
                if not market and symbol.endswith("USDT"):
                    return True
    return False


def _manager_response_resolves_binance_activity_gap(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    target_symbols = _manager_binance_activity_gap_target_symbols(prompt)
    if not _manager_response_has_concrete_repair_resolution(
        response,
        target_symbols=target_symbols,
    ):
        return False
    return _manager_hold_has_concrete_next_step_for_symbols(
        hold_decision,
        target_symbols,
    )


def _manager_prompt_has_wiki_action_pressure(prompt: dict[str, Any]) -> bool:
    contract = _manager_scoped_jue_wiki_action_pressure_contract(prompt)
    if str(contract.get("status") or "").strip().lower() == "active":
        return True
    return bool(_as_list(contract.get("page_ids")))


def _manager_execution_gate_blocks_contract(prompt: dict[str, Any]) -> bool:
    gate = prompt.get("execution_gate") if isinstance(prompt, dict) else {}
    gate = gate if isinstance(gate, dict) else {}
    if str(gate.get("status") or "").strip().lower() in {
        "blocked",
        "disabled",
        "error",
        "halted",
    }:
        return True
    kill = gate.get("kill_switch") if isinstance(gate.get("kill_switch"), dict) else {}
    if bool(kill.get("enabled")):
        return True
    execution = gate.get("execution") if isinstance(gate.get("execution"), dict) else {}
    if execution and not any(
        bool(execution.get(key))
        for key in (
            "spot_orders_enabled",
            "futures_orders_enabled",
            "upbit_orders_enabled",
        )
    ):
        return True
    return False


def _manager_response_has_concrete_repair_resolution(
    response: dict[str, Any],
    *,
    target_symbols: set[str] | None = None,
) -> bool:
    resolution = (
        response.get("validation_repair_resolution")
        if isinstance(response, dict)
        else {}
    )
    resolution = resolution if isinstance(resolution, dict) else {}
    accepted = {
        "probe_waiting_block",
        "small_waiting_block",
        "one_share_probe",
        "updated_price_geometry",
        "candidate_rejected",
        "safety_gate_defer",
    }
    phrase_aliases = {
        "create or update a smaller waiting/probe block": "small_waiting_block",
        "stage a concrete next trigger in hold_decision.next_triggers": "safety_gate_defer",
        "reject a candidate with exact missing price/depth/funding/risk evidence": (
            "candidate_rejected"
        ),
        "explicit_reject_with_price_reason": "candidate_rejected",
        "defer only because an explicit server safety gate blocks execution": (
            "safety_gate_defer"
        ),
        "defer_due_to_safety_gate": "safety_gate_defer",
        "stage_concrete_next_trigger": "safety_gate_defer",
    }

    def negative_repair_row(value: Any) -> bool:
        if isinstance(value, dict):
            return any(negative_repair_row(child) for child in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(negative_repair_row(child) for child in value)
        text = str(value or "").strip().lower()
        if not text:
            return False
        text = re.sub(r"\b[a-z0-9_]*missing_from_model\b", " ", text)
        compact = (
            text.replace("_", " ")
            .replace("-", " ")
            .replace("/", " ")
            .replace(";", " ")
            .replace(",", " ")
        )
        negative_phrases = (
            "validation repair not resolved",
            "repair not resolved",
            "repair unresolved",
            "resolution missing",
            "repair missing",
            "no concrete next trigger",
            "no concrete trigger",
            "trigger unavailable",
            "next trigger unavailable",
            "failed to repair",
            "cannot repair",
            "수리 미해결",
            "해결 못함",
        )
        return any(phrase in compact for phrase in negative_phrases)

    for row in _as_list(resolution.get("resolved_candidates")):
        if not isinstance(row, dict):
            continue
        raw_kind = str(row.get("resolution") or "").strip().lower()
        kind = phrase_aliases.get(raw_kind, raw_kind)
        if kind not in accepted:
            continue
        if negative_repair_row(row):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        if target_symbols and symbol.upper() not in target_symbols:
            continue
        next_trigger = str(row.get("next_trigger") or "").strip()
        evidence_gap = str(row.get("evidence_gap") or "").strip()
        if kind in {"candidate_rejected", "safety_gate_defer"}:
            if evidence_gap or next_trigger:
                return True
            continue
        if next_trigger:
            return True
    return False


def _manager_executable_repair_resolution_missing_action(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    resolution = (
        response.get("validation_repair_resolution")
        if isinstance(response, dict)
        else {}
    )
    resolution = resolution if isinstance(resolution, dict) else {}
    executable_kinds = {
        "probe_waiting_block",
        "small_waiting_block",
        "one_share_probe",
        "updated_price_geometry",
        "create or update a smaller waiting/probe block",
    }
    (
        block_symbols_by_id,
        block_pairs_by_id,
        _block_by_id,
    ) = _manager_prompt_block_identity_context(prompt)
    existing_block_symbols = {
        symbol
        for symbols in block_symbols_by_id.values()
        for symbol in symbols
    }
    existing_block_pairs = {
        pair
        for pairs in block_pairs_by_id.values()
        for pair in pairs
    }
    action_pairs: set[tuple[str, str]] = set()
    action_symbols: set[str] = set()
    for section in ("create_blocks", "update_blocks"):
        for action in _as_list(actions.get(section)):
            if not isinstance(action, dict):
                continue
            for symbol, market in _manager_action_symbol_market_identities(
                action,
                block_symbols_by_id=block_symbols_by_id,
                block_pairs_by_id=block_pairs_by_id,
            ):
                action_symbols.add(symbol)
                if market:
                    action_pairs.add((symbol, market))
    for row in _as_list(resolution.get("resolved_candidates")):
        if not isinstance(row, dict):
            continue
        kind = str(row.get("resolution") or "").strip().lower()
        if kind not in executable_kinds:
            continue
        symbol = str(
            row.get("symbol")
            or row.get("code")
            or row.get("ticker")
            or ""
        ).upper().strip()
        if not symbol:
            continue
        raw_market = row.get("market") or row.get("venue")
        market = normalize_market(raw_market) if raw_market not in (None, "") else ""
        if kind == "updated_price_geometry":
            if market and (symbol, market) not in existing_block_pairs:
                continue
            if not market and symbol not in existing_block_symbols:
                continue
        if market:
            if (symbol, market) not in action_pairs:
                return True
            continue
        if symbol not in action_symbols:
            return True
    return False


def _manager_repairable_probe_design_rejection_ignores_design(
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    repairable_pairs: set[tuple[str, str]] = set()
    repairable_symbols: set[str] = set()
    repairable_pairs_by_symbol: dict[str, set[tuple[str, str]]] = {}
    for row in _manager_repairable_probe_design_rows(prompt):
        if not isinstance(row, dict):
            continue
        design = row.get("validation_repair_probe_design")
        if not isinstance(design, dict) or not design:
            continue
        symbol = str(
            row.get("symbol")
            or row.get("code")
            or row.get("ticker")
            or ""
        ).upper().strip()
        if not symbol:
            continue
        repairable_symbols.add(symbol)
        market = normalize_market(row.get("market") or row.get("venue"))
        if market:
            pair = (symbol, market)
            repairable_pairs.add(pair)
            repairable_pairs_by_symbol.setdefault(symbol, set()).add(pair)
    if not repairable_symbols:
        return False
    (
        block_symbols_by_id,
        block_pairs_by_id,
        _block_by_id,
    ) = _manager_prompt_block_identity_context(prompt)
    repair_action_pairs: set[tuple[str, str]] = set()
    repair_action_symbols: set[str] = set()

    def action_has_repair_probe_geometry(action: dict[str, Any]) -> bool:
        return any(
            _safe_float(action.get(key)) > 0
            for key in (
                "entry_price",
                "entry_trigger_price",
                "target_price",
                "stop_price",
                "qty",
                "quote_budget_usdt",
                "quote_budget_krw",
                "quantity_or_quote_budget",
            )
        )

    for section in ("create_blocks", "update_blocks", "close_blocks", "pause_blocks"):
        for action in _as_list(actions.get(section)):
            if not isinstance(action, dict):
                continue
            if section not in {"create_blocks", "update_blocks"}:
                continue
            if not action_has_repair_probe_geometry(action):
                continue
            for symbol, market in _manager_action_symbol_market_identities(
                action,
                block_symbols_by_id=block_symbols_by_id,
                block_pairs_by_id=block_pairs_by_id,
            ):
                repair_action_symbols.add(symbol)
                if market:
                    repair_action_pairs.add((symbol, market))
    if repair_action_pairs.intersection(repairable_pairs):
        return False
    if not repairable_pairs and repairable_symbols.intersection(repair_action_symbols):
        return False
    resolution = (
        response.get("validation_repair_resolution")
        if isinstance(response, dict)
        else {}
    )
    resolution = resolution if isinstance(resolution, dict) else {}
    executable_probe_resolutions = {
        "probe_waiting_block",
        "small_waiting_block",
        "one_share_probe",
        "create or update a smaller waiting/probe block",
    }
    for item in _as_list(resolution.get("resolved_candidates")):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("resolution") or "").strip().lower()
        if kind not in executable_probe_resolutions:
            continue
        symbol = str(
            item.get("symbol")
            or item.get("code")
            or item.get("ticker")
            or ""
        ).upper().strip()
        if not symbol:
            continue
        raw_market = item.get("market") or item.get("venue")
        market = normalize_market(raw_market) if raw_market not in (None, "") else ""
        if market and (symbol, market) in repair_action_pairs:
            return False
        if not market and symbol in repair_action_symbols:
            return False
    for item in _as_list(resolution.get("resolved_candidates")):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("resolution") or "").strip().lower()
        if kind not in {"candidate_rejected", "safety_gate_defer"}:
            continue
        symbol = str(
            item.get("symbol")
            or item.get("code")
            or item.get("ticker")
            or ""
        ).upper().strip()
        if not symbol or symbol not in repairable_symbols:
            continue
        raw_market = item.get("market") or item.get("venue")
        market = normalize_market(raw_market) if raw_market not in (None, "") else ""
        if market and repairable_pairs and (symbol, market) not in repairable_pairs:
            continue
        if _manager_probe_rejection_cites_current_execution_gate(item):
            continue
        if market and (symbol, market) in repair_action_pairs:
            continue
        symbol_repairable_pairs = repairable_pairs_by_symbol.get(symbol, set())
        if not market and symbol_repairable_pairs:
            if repair_action_pairs.intersection(symbol_repairable_pairs):
                continue
        elif not market and symbol in repair_action_symbols:
            continue
        return True
    return False


def _manager_repairable_probe_design_rows(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pressure = _manager_scoped_proactive_decision_pressure(prompt)
    for row in _as_list(pressure.get("top_candidates")):
        if isinstance(row, dict):
            rows.append(row)
    for row in _manager_prompt_candidate_rows(prompt):
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _manager_probe_rejection_cites_current_execution_gate(value: Any) -> bool:
    current_gate_terms = (
        "spread",
        "depth",
        "orderbook",
        "order book",
        "funding",
        "liquidity",
        "slippage",
        "confidence",
        "live gate",
        "live_authority",
        "live authority",
        "validation_probe",
        "validation probe",
        "execution gate",
        "safety gate",
        "cooldown",
        "derivatives unavailable",
        "venue unavailable",
        "호가",
        "스프레드",
        "깊이",
        "유동성",
        "펀딩",
        "신뢰도",
        "쿨다운",
        "권한",
        "실행 게이트",
        "안전 게이트",
    )
    if isinstance(value, dict):
        evidence_fields = (
            "evidence_gap",
            "evidence_gaps",
            "reason",
            "reasons",
            "risk_note",
            "risk_notes",
            "data_gap",
            "data_gaps",
            "execution_gate",
            "execution_gates",
            "gate",
            "gate_reason",
            "safety_gate",
            "live_gate",
        )
        text = json.dumps(
            {
                key: value.get(key)
                for key in evidence_fields
                if value.get(key) not in (None, "", [], {})
            },
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    return any(term in text for term in current_gate_terms)


def _manager_repair_probe_action_below_min_executable_qty(
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    min_qty_by_pair: dict[tuple[str, str], float] = {}
    min_qty_by_symbol: dict[str, float] = {}
    min_notional_by_pair: dict[tuple[str, str], float] = {}
    min_notional_by_symbol: dict[str, float] = {}
    for row in _manager_repairable_probe_design_rows(prompt):
        if not isinstance(row, dict):
            continue
        design = row.get("validation_repair_probe_design")
        if not isinstance(design, dict):
            continue
        min_qty = _safe_float(design.get("min_executable_qty"))
        min_notional = max(
            _safe_float(design.get("min_executable_notional_usdt")),
            _safe_float(design.get("min_executable_notional_krw")),
        )
        if min_qty <= 0 and min_notional <= 0:
            continue
        symbol = str(
            row.get("symbol")
            or row.get("code")
            or row.get("ticker")
            or ""
        ).upper().strip()
        if not symbol:
            continue
        market = normalize_market(row.get("market") or row.get("venue"))
        if min_qty > 0:
            min_qty_by_symbol[symbol] = max(
                min_qty_by_symbol.get(symbol, 0.0),
                min_qty,
            )
        if min_notional > 0:
            min_notional_by_symbol[symbol] = max(
                min_notional_by_symbol.get(symbol, 0.0),
                min_notional,
            )
        if market:
            if min_qty > 0:
                min_qty_by_pair[(symbol, market)] = max(
                    min_qty_by_pair.get((symbol, market), 0.0),
                    min_qty,
                )
            if min_notional > 0:
                min_notional_by_pair[(symbol, market)] = max(
                    min_notional_by_pair.get((symbol, market), 0.0),
                    min_notional,
                )
    if not min_qty_by_symbol and not min_notional_by_symbol:
        return False

    symbols_with_market_floor = {
        symbol
        for symbol, _market in {
            *min_qty_by_pair.keys(),
            *min_notional_by_pair.keys(),
        }
    }
    (
        block_symbols_by_id,
        block_pairs_by_id,
        block_by_id,
    ) = _manager_prompt_block_identity_context(prompt)

    for section in ("create_blocks", "update_blocks"):
        for action in _as_list(actions.get(section)):
            if not isinstance(action, dict):
                continue
            identities = _manager_action_symbol_market_identities(
                action,
                block_symbols_by_id=block_symbols_by_id,
                block_pairs_by_id=block_pairs_by_id,
            )
            if not identities:
                continue
            for symbol, identity_market in identities:
                if identity_market:
                    min_qty = min_qty_by_pair.get((symbol, identity_market))
                    min_notional = min_notional_by_pair.get(
                        (symbol, identity_market)
                    )
                    if (
                        min_qty is None
                        and min_notional is None
                        and symbol in symbols_with_market_floor
                    ):
                        continue
                    if min_qty is None:
                        min_qty = min_qty_by_symbol.get(symbol, 0.0)
                    if min_notional is None:
                        min_notional = min_notional_by_symbol.get(symbol, 0.0)
                else:
                    min_qty = min_qty_by_symbol.get(symbol, 0.0)
                    min_notional = min_notional_by_symbol.get(symbol, 0.0)
                if min_qty <= 0 and min_notional <= 0:
                    continue
                explicit_notional = max(
                    _safe_float(action.get("quote_budget_usdt")),
                    _safe_float(action.get("quote_budget_krw")),
                    _safe_float(action.get("quote_budget")),
                    _safe_float(action.get("notional_usdt")),
                    _safe_float(action.get("notional_krw")),
                    _safe_float(action.get("notional")),
                    _safe_float(action.get("max_notional_usdt")),
                    _safe_float(action.get("target_block_value_usdt")),
                )
                if min_notional > 0 and explicit_notional >= min_notional - 1e-12:
                    continue
                qty = _safe_float(
                    action.get("qty")
                    or action.get("quantity")
                    or action.get("qty_initial")
                )
                entry = _safe_float(
                    action.get("entry_trigger_price")
                    or action.get("entry_price")
                    or action.get("entry_price_usdt")
                    or action.get("entry_price_krw")
                )
                if section == "update_blocks" and (
                    qty <= 0 or (min_notional > 0 and entry <= 0)
                ):
                    for block_id in _manager_action_identity_block_ids(action):
                        block = block_by_id.get(block_id)
                        if not isinstance(block, dict):
                            continue
                        block_qty = max(
                            _safe_float(block.get("qty")),
                            _safe_float(block.get("quantity")),
                            _safe_float(block.get("qty_initial")),
                            _safe_float(block.get("qty_open")),
                        )
                        block_entry = _safe_float(
                            block.get("entry_trigger_price")
                            or block.get("entry_price")
                            or block.get("entry_price_usdt")
                            or block.get("entry_price_krw")
                        )
                        if qty <= 0 and block_qty > 0:
                            qty = block_qty
                        if entry <= 0 and block_entry > 0:
                            entry = block_entry
                if qty > 0 and min_qty > 0 and qty + 1e-12 >= min_qty:
                    continue
                if min_notional > 0:
                    if qty > 0 and entry > 0 and qty * entry >= min_notional - 1e-12:
                        continue
                    if 0 < explicit_notional + 1e-12 < min_notional:
                        return True
                if min_qty > 0 and 0 < qty + 1e-12 < min_qty:
                    return True
                if min_qty > 0 and qty <= 0 and explicit_notional <= 0:
                    return True
    return False


def _manager_hold_has_concrete_next_step(hold_decision: dict[str, Any]) -> bool:
    hold = hold_decision if isinstance(hold_decision, dict) else {}
    for row in _as_list(hold.get("next_triggers")):
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").strip() and str(
            row.get("condition") or row.get("reason") or ""
        ).strip():
            return True
        if _safe_float(row.get("price")) > 0 and str(
            row.get("condition") or ""
        ).strip():
            return True
    if _as_list(hold.get("data_gaps")) and _as_list(hold.get("watch_symbols")):
        return True
    return False


def _manager_hold_identity_symbols(hold_decision: dict[str, Any]) -> set[str]:
    hold = hold_decision if isinstance(hold_decision, dict) else {}

    def extract(value: Any) -> set[str]:
        values = value if isinstance(value, list) else [value]
        symbols: set[str] = set()
        for item in values:
            symbol = str(item or "").strip().upper()
            if symbol:
                symbols.add(symbol)
        return symbols

    symbols: set[str] = (
        extract(hold.get("symbol"))
        | extract(hold.get("symbols"))
        | extract(hold.get("watch_symbols"))
        | extract(hold.get("long_watch_symbols"))
    )
    for row in _as_list(hold.get("next_triggers")):
        if not isinstance(row, dict):
            continue
        symbols.update(
            extract(row.get("symbol"))
            | extract(row.get("code"))
            | extract(row.get("ticker"))
            | extract(row.get("symbols"))
        )
    return symbols


def _manager_memory_card_quality_required_terms(prompt: dict[str, Any]) -> list[str]:
    quality = _manager_scoped_memory_card_quality(prompt)
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    action_plan = (
        quality.get("action_plan") if isinstance(quality.get("action_plan"), dict) else {}
    )
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for field in _as_list(action_plan.get("required_checks")):
        add(field)
    for row in _as_list(action_plan.get("missing_fields_by_symbol")):
        if isinstance(row, dict):
            for field in _as_list(row.get("missing_fields")):
                add(field)
            for field in _as_list(row.get("required_checks")):
                add(field)
    field_counts = (
        summary.get("missing_field_counts")
        if isinstance(summary.get("missing_field_counts"), dict)
        else {}
    )
    for field in field_counts:
        add(field)
    for row in _as_list(summary.get("missing_fields_by_symbol")):
        if isinstance(row, dict):
            for field in _as_list(row.get("missing_fields")):
                add(field)
            for field in _as_list(row.get("required_checks")):
                add(field)
    for row in _as_list(summary.get("rows")):
        if isinstance(row, dict):
            for field in _as_list(row.get("missing_fields")):
                add(field)
            for field in _as_list(row.get("required_checks")):
                add(field)
    gap_summary = _manager_repair_contract_memory_card_quality_gap_summary(prompt)
    gap_terms, has_missed_terms = (
        _manager_memory_card_quality_gap_summary_required_terms(gap_summary)
    )
    if has_missed_terms:
        return gap_terms
    for field in gap_terms:
        add(field)
    return terms


def _manager_memory_card_quality_target_symbols(prompt: dict[str, Any]) -> set[str]:
    quality = _manager_scoped_memory_card_quality(prompt)
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    action_plan = (
        quality.get("action_plan") if isinstance(quality.get("action_plan"), dict) else {}
    )
    symbols: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            symbol = str(item or "").strip().upper()
            if symbol:
                symbols.add(symbol)

    add(summary.get("weak_symbols"))
    add(action_plan.get("symbols"))
    for source in (summary, action_plan):
        for row in _as_list(source.get("missing_fields_by_symbol")):
            if isinstance(row, dict):
                add(row.get("symbol"))
        for row in _as_list(source.get("rows")):
            if isinstance(row, dict):
                add(row.get("symbol"))
    return symbols


def _manager_action_identity_symbols(row: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            symbol = str(item or "").strip().upper()
            if symbol:
                symbols.add(symbol)

    add(row.get("symbol"))
    add(row.get("code"))
    add(row.get("ticker"))
    add(row.get("symbols"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    add(metadata.get("symbol"))
    add(metadata.get("code"))
    add(metadata.get("ticker"))
    add(metadata.get("symbols"))
    return symbols


def _manager_action_identity_block_ids(row: dict[str, Any]) -> set[str]:
    block_ids: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item or "").strip().lower()
            if text:
                block_ids.add(text)

    add(row.get("block_id"))
    add(row.get("id"))
    add(row.get("block_ids"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    add(metadata.get("block_id"))
    add(metadata.get("id"))
    add(metadata.get("block_ids"))
    return block_ids


def _manager_action_identity_symbols_with_block_map(
    row: dict[str, Any],
    *,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> set[str]:
    symbols = set(_manager_action_identity_symbols(row))
    if not block_symbol_map:
        return symbols
    for block_id in _manager_action_identity_block_ids(row):
        symbols.update(block_symbol_map.get(block_id, set()))
    return symbols


def _manager_hold_has_concrete_next_step_for_symbols(
    hold_decision: dict[str, Any],
    target_symbols: set[str],
) -> bool:
    if not target_symbols:
        return _manager_hold_has_concrete_next_step(hold_decision)
    hold = hold_decision if isinstance(hold_decision, dict) else {}

    def extract(value: Any) -> set[str]:
        values = value if isinstance(value, list) else [value]
        symbols: set[str] = set()
        for item in values:
            symbol = str(item or "").strip().upper()
            if symbol:
                symbols.add(symbol)
        return symbols

    for row in _as_list(hold.get("next_triggers")):
        if not isinstance(row, dict):
            continue
        row_symbols = (
            extract(row.get("symbol"))
            | extract(row.get("code"))
            | extract(row.get("ticker"))
            | extract(row.get("symbols"))
        )
        if row_symbols.isdisjoint(target_symbols):
            continue
        if str(row.get("condition") or row.get("reason") or "").strip():
            return True
        if _safe_float(row.get("price")) > 0 and str(
            row.get("condition") or ""
        ).strip():
            return True
    watch_symbols = extract(hold.get("watch_symbols"))
    return bool(
        watch_symbols.intersection(target_symbols)
        and _as_list(hold.get("data_gaps"))
    )


def _manager_memory_card_quality_action_has_specific_evidence(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _manager_memory_card_quality_required_terms(prompt)
    target_symbols = _manager_memory_card_quality_target_symbols(prompt)
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            if target_symbols and _manager_action_identity_symbols(row).isdisjoint(
                target_symbols
            ):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_memory_card_quality",
                "jue_wiki_memory_card_cross_check",
            ):
                repair_note = metadata.get(metadata_key)
                if repair_note in (None, "", [], {}):
                    repair_note = row.get(metadata_key)
                if _manager_repair_note_is_negative(repair_note):
                    continue
                if terms:
                    if _manager_payload_mentions_any_term(repair_note, terms):
                        return True
                elif _manager_repair_note_is_concrete(repair_note):
                    return True
    return False


def _manager_memory_card_quality_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_memory_card_quality_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _manager_memory_card_quality_note_is_negative(child) for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    text = re.sub(r"\b[a-z0-9_]*missing_from_model\b", " ", text)
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "memory card quality not resolved",
        "memory card quality unresolved",
        "memory card quality unavailable",
        "quality evidence unavailable",
        "no fresh memory card quality evidence",
        "no memory card quality evidence",
        "not refreshed",
        "not cross checked",
        "without quality evidence",
        "품질 미해결",
        "품질 근거 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_wiki_repair_reference_terms(prompt: dict[str, Any]) -> list[str]:
    repair_contract = _manager_scoped_jue_wiki_repair_contract(prompt)
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 2 and text not in terms:
            terms.append(text)

    def add_values(source: dict[str, Any], keys: tuple[str, ...]) -> None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, dict):
                for child_key in value:
                    add(child_key)
                continue
            for item in _as_list(value):
                add(item)
            if value not in (None, "", [], {}):
                add(value)

    for row in _as_list(repair_contract.get("top_priorities")):
        if not isinstance(row, dict):
            continue
        add_values(
            row,
            (
                "source_id",
                "page_id",
                "symbol",
                "symbols",
                "action_type",
                "priority_type",
                "type",
                "warning",
                "warnings",
                "quality_warning",
                "quality_warnings",
            ),
        )

    for row in _as_list(repair_contract.get("action_batches")):
        if not isinstance(row, dict):
            continue
        add_values(
            row,
            (
                "source_id",
                "page_id",
                "symbol",
                "symbols",
                "action_type",
                "warning",
                "warnings",
                "quality_warning",
                "quality_warnings",
                "warning_counts",
            ),
        )

    action_plan = repair_contract.get("repair_pressure_action_plan")
    action_plan = action_plan if isinstance(action_plan, dict) else {}
    for key in (
        "action_batch_type_counts",
        "action_batch_warning_counts",
        "omitted_priority_type_counts",
    ):
        counts = action_plan.get(key) if isinstance(action_plan.get(key), dict) else {}
        for term in counts:
            add(term)
    for row in _manager_degraded_wiki_effectiveness_items(prompt):
        add_values(
            row,
            (
                "page_id",
                "symbol",
                "symbols",
                "warning",
                "warnings",
                "quality_warning",
                "quality_warnings",
                "effectiveness_reasons",
            ),
        )
    return terms


def _manager_payload_mentions_any_term(value: Any, terms: list[str]) -> bool:
    if not terms:
        return False
    text = _json_dumps(value).lower()
    text_spaced = text.replace("_", " ")
    for term in terms:
        if term in text or term.replace("_", " ") in text_spaced:
            return True
    return False


def _manager_candidate_memory_hint_items(
    prompt: dict[str, Any],
) -> list[tuple[set[str], list[str]]]:
    if not isinstance(prompt, dict):
        return []
    policy = prompt.get("candidate_memory_hint_policy")
    policy = policy if isinstance(policy, dict) else {}
    candidates = _as_list(prompt.get("candidates"))
    if not bool(policy.get("required")) and not any(
        isinstance(row, dict) and isinstance(row.get("memory_hint"), dict)
        for row in candidates
    ):
        return []

    def add_term(terms: list[str], value: Any) -> None:
        text = _clean_text(value, limit=180).lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    items: list[tuple[set[str], list[str]]] = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        hint = row.get("memory_hint") if isinstance(row.get("memory_hint"), dict) else {}
        if not hint:
            continue
        terms: list[str] = []
        for key in ("reasons", "risks", "checks"):
            for item in _as_list(hint.get(key)):
                add_term(terms, item)
        for item in _as_list(hint.get("sources")):
            add_term(terms, item)
        if terms:
            items.append((_manager_action_identity_symbols(row), terms))
    return items


def _manager_candidate_memory_hint_terms(prompt: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for _, item_terms in _manager_candidate_memory_hint_items(prompt):
        for term in item_terms:
            if term not in terms:
                terms.append(term)
    return terms


def _manager_payload_mentions_any_symbol(value: Any, symbols: set[str]) -> bool:
    if not symbols:
        return True
    text = _json_dumps(value).upper()
    return any(symbol in text for symbol in symbols)


def _manager_candidate_memory_hint_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_candidate_memory_hint_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _manager_candidate_memory_hint_note_is_negative(child) for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "candidate memory hint not applied",
        "memory hint not applied",
        "memory hint unresolved",
        "memory hint unavailable",
        "memory context unavailable",
        "fresh context absent",
        "no fresh memory",
        "no fresh crypto memory",
        "no memory context",
        "without memory context",
        "memory missing",
        "not applied",
        "적용 못함",
        "메모리 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_actions_resolve_candidate_memory_hint(
    actions: dict[str, Any],
    *,
    symbols: set[str],
    terms: list[str],
) -> bool:
    if not isinstance(actions, dict):
        return False
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            row_symbols = _manager_action_identity_symbols(row)
            if symbols and row_symbols and row_symbols.isdisjoint(symbols):
                continue
            if symbols and not row_symbols and not _manager_payload_mentions_any_symbol(
                row, symbols
            ):
                continue
            if _manager_candidate_memory_hint_note_is_negative(row):
                continue
            if _manager_payload_mentions_any_term(row, terms):
                return True
    return False


def _manager_payload_resolves_candidate_memory_hint(
    value: Any,
    *,
    symbols: set[str],
    terms: list[str],
) -> bool:
    if _manager_candidate_memory_hint_note_is_negative(value):
        return False
    return _manager_payload_mentions_any_term(
        value, terms
    ) and _manager_payload_mentions_any_symbol(value, symbols)


def _manager_candidate_memory_hint_resolution_missing(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    items = _manager_candidate_memory_hint_items(prompt)
    if not items:
        return False
    for symbols, terms in items:
        if (
            _manager_actions_resolve_candidate_memory_hint(
                actions,
                symbols=symbols,
                terms=terms,
            )
            or _manager_payload_resolves_candidate_memory_hint(
                response,
                symbols=symbols,
                terms=terms,
            )
            or _manager_payload_resolves_candidate_memory_hint(
                hold_decision,
                symbols=symbols,
                terms=terms,
            )
        ):
            continue
        return True
    return False


def _manager_candidate_memory_hint_resolution_summary(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> dict[str, Any]:
    items = _manager_candidate_memory_hint_items(prompt)
    if not items:
        return {
            "candidate_memory_hint_status": "inactive",
            "candidate_memory_hint_count": 0,
            "candidate_memory_hint_resolved_count": 0,
            "candidate_memory_hint_unresolved_count": 0,
            "candidate_memory_hint_missing_symbols": [],
        }
    resolved_count = 0
    missing_symbols: list[str] = []
    for symbols, terms in items:
        resolved = (
            _manager_actions_resolve_candidate_memory_hint(
                actions,
                symbols=symbols,
                terms=terms,
            )
            or _manager_payload_resolves_candidate_memory_hint(
                response,
                symbols=symbols,
                terms=terms,
            )
            or _manager_payload_resolves_candidate_memory_hint(
                hold_decision,
                symbols=symbols,
                terms=terms,
            )
        )
        if resolved:
            resolved_count += 1
            continue
        for symbol in sorted(symbols):
            if symbol not in missing_symbols:
                missing_symbols.append(symbol)
    unresolved_count = len(items) - resolved_count
    status = (
        "resolved"
        if unresolved_count <= 0
        else "partial"
        if resolved_count > 0
        else "unresolved"
    )
    return {
        "candidate_memory_hint_status": status,
        "candidate_memory_hint_count": len(items),
        "candidate_memory_hint_resolved_count": resolved_count,
        "candidate_memory_hint_unresolved_count": unresolved_count,
        "candidate_memory_hint_missing_symbols": missing_symbols[:12],
    }


def _manager_actions_have_prompt_linked_wiki_repair_metadata(
    prompt: dict[str, Any],
    actions: dict[str, Any],
    *,
    metadata_keys: tuple[str, ...],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _manager_wiki_repair_reference_terms(prompt)
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in metadata_keys:
                repair_note = metadata.get(metadata_key)
                if repair_note in (None, "", [], {}):
                    repair_note = row.get(metadata_key)
                if _manager_action_repair_metadata_note_is_negative(repair_note):
                    continue
                if not _manager_repair_note_is_concrete(repair_note):
                    continue
                if not terms or _manager_payload_mentions_any_term(
                    {"repair_note": repair_note, "symbol": row.get("symbol")},
                    terms,
                ):
                    return True
    return False


def _manager_response_has_prompt_linked_repair_resolution(
    prompt: dict[str, Any],
    response: dict[str, Any],
) -> bool:
    if not _manager_response_has_concrete_repair_resolution(response):
        return False
    if _manager_response_rejects_all_visible_candidates_with_repair_evidence(
        prompt,
        response,
    ):
        return True
    terms = _manager_wiki_repair_reference_terms(prompt)
    if not terms:
        return True
    return _manager_payload_mentions_any_term(
        response.get("validation_repair_resolution"),
        terms,
    )


def _manager_response_rejects_all_visible_candidates_with_repair_evidence(
    prompt: dict[str, Any],
    response: dict[str, Any],
) -> bool:
    candidate_rows = _manager_prompt_candidate_rows(prompt)
    if not candidate_rows:
        return False
    expected_pairs: set[tuple[str, str]] = set()
    expected_unscoped_symbols: set[str] = set()
    for row in candidate_rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        raw_market = row.get("market") or row.get("venue")
        if raw_market in (None, ""):
            expected_unscoped_symbols.add(symbol)
        else:
            expected_pairs.add((symbol, normalize_market(raw_market)))
    if not expected_pairs and not expected_unscoped_symbols:
        return False

    resolution = response.get("validation_repair_resolution")
    resolution = resolution if isinstance(resolution, dict) else {}
    covered_pairs: set[tuple[str, str]] = set()
    covered_symbols: set[str] = set()
    for item in _as_list(resolution.get("resolved_candidates")):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        outcome = str(item.get("resolution") or "").strip().lower()
        if outcome not in {
            "candidate_rejected",
            "safety_gate_defer",
            "wait_until_memory_refresh",
            "updated_price_geometry",
        }:
            continue
        evidence_gap = item.get("evidence_gap")
        next_trigger = item.get("next_trigger")
        memory_resolution = item.get("memory_contract_resolution")
        if (
            _manager_repair_note_is_negative(item)
            or not _manager_repair_note_is_concrete(evidence_gap)
            or not _manager_repair_note_is_concrete(next_trigger)
            or not _manager_repair_note_is_concrete(memory_resolution)
        ):
            continue
        market = normalize_market(item.get("market") or item.get("venue"))
        if market:
            covered_pairs.add((symbol, market))
        covered_symbols.add(symbol)

    for symbol, market in expected_pairs:
        if (symbol, market) not in covered_pairs and symbol not in covered_symbols:
            return False
    return expected_unscoped_symbols.issubset(covered_symbols)


def _manager_memory_contract_repair_terms(prompt: dict[str, Any]) -> list[str]:
    repair = _manager_scoped_validation_repair(prompt)
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if len(text) >= 4 and text not in terms:
            terms.append(text)

    for key in ("memory_contracts", "memory_contract_errors"):
        for item in _as_list(repair.get(key)):
            add(item)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _as_list(repair.get(section)):
            if not isinstance(row, dict):
                continue
            add(row.get("memory_contract"))
            add(row.get("memory_contract_error"))
    return terms


def _manager_memory_contract_repair_target_symbols(
    prompt: dict[str, Any],
) -> set[str]:
    repair = _manager_scoped_validation_repair(prompt)
    symbols: set[str] = set()

    def add(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            token = _compact_requested_crypto_symbol_token(item)
            if token:
                symbols.add(token)

    for key in (
        "symbol",
        "symbols",
        "code",
        "codes",
        "ticker",
        "tickers",
        "target_symbol",
        "target_symbols",
        "impacted_symbol",
        "impacted_symbols",
        "missing_symbol",
        "missing_symbols",
    ):
        add(repair.get(key))
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _as_list(repair.get(section)):
            if not isinstance(row, dict):
                continue
            for key in (
                "symbol",
                "symbols",
                "code",
                "codes",
                "ticker",
                "tickers",
                "target_symbol",
                "target_symbols",
                "impacted_symbol",
                "impacted_symbols",
                "missing_symbol",
                "missing_symbols",
            ):
                add(row.get(key))
    return symbols


def _manager_memory_contract_repair_details_by_symbol(
    prompt: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    repair = _manager_scoped_validation_repair(prompt)
    details: dict[str, dict[str, list[str]]] = {}

    def add_text(rows: list[str], value: Any) -> None:
        text = _clean_text(value, limit=160)
        if text and text not in rows:
            rows.append(text)

    def symbols_from(row: dict[str, Any]) -> set[str]:
        symbols: set[str] = set()
        for key in (
            "symbol",
            "symbols",
            "code",
            "codes",
            "ticker",
            "tickers",
            "target_symbol",
            "target_symbols",
            "impacted_symbol",
            "impacted_symbols",
            "missing_symbol",
            "missing_symbols",
        ):
            values = row.get(key)
            values = values if isinstance(values, list) else [values]
            for item in values:
                token = _compact_requested_crypto_symbol_token(item)
                if token:
                    symbols.add(token)
        return symbols

    rows: list[dict[str, Any]] = []
    if isinstance(repair, dict):
        rows.append(repair)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _as_list(repair.get(section)):
            if isinstance(row, dict):
                rows.append(row)
    for row in rows:
        row_symbols = symbols_from(row)
        if not row_symbols:
            continue
        for symbol in row_symbols:
            detail = details.setdefault(symbol, {"contracts": [], "errors": []})
            add_text(detail["contracts"], row.get("memory_contract"))
            add_text(detail["errors"], row.get("memory_contract_error"))
            for value in _as_list(row.get("memory_contracts")):
                add_text(detail["contracts"], value)
            for value in _as_list(row.get("memory_contract_errors")):
                add_text(detail["errors"], value)
    return details


def _manager_prompt_requires_memory_contract_repair(prompt: dict[str, Any]) -> bool:
    repair = _manager_scoped_validation_repair(prompt)
    if _manager_memory_contract_repair_terms(prompt):
        return True
    if "require_memory_contract_resolution" in {
        str(item or "").strip()
        for item in _as_list(repair.get("required_checks"))
    }:
        return True
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _as_list(repair.get(section)):
            if not isinstance(row, dict):
                continue
            if str(row.get("discipline_id") or "").strip() == "memory_contract":
                return True
            if "require_memory_contract_resolution" in {
                str(item or "").strip()
                for item in _as_list(row.get("required_checks"))
            }:
                return True
    return False


def _manager_actions_resolve_memory_contract_repair(
    *,
    actions: dict[str, Any],
    terms: list[str],
    target_symbols: set[str],
) -> bool:
    if not isinstance(actions, dict):
        return False
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            if target_symbols and _manager_action_identity_symbols(row).isdisjoint(
                target_symbols
            ):
                continue
            if _manager_memory_contract_resolution_note_is_negative(row):
                continue
            if not terms or _manager_payload_mentions_any_term(row, terms):
                return True
    return False


def _manager_memory_contract_resolution_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_memory_contract_resolution_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _manager_memory_contract_resolution_note_is_negative(child)
            for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "memory contract not applied",
        "memory contract unresolved",
        "memory contract unavailable",
        "contract resolution missing",
        "memory contract resolution missing",
        "no fresh memory contract",
        "no memory contract",
        "without memory contract",
        "not applied",
        "반영하지 못함",
        "계약 미해결",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_memory_contract_repair_resolved(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _manager_prompt_requires_memory_contract_repair(prompt):
        return True
    terms = _manager_memory_contract_repair_terms(prompt)
    target_symbols = _manager_memory_contract_repair_target_symbols(prompt)
    if not terms:
        return (
            _manager_response_has_concrete_repair_resolution(
                response,
                target_symbols=target_symbols,
            )
            or _manager_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                target_symbols,
            )
            or _manager_actions_resolve_memory_contract_repair(
                actions=actions,
                terms=terms,
                target_symbols=target_symbols,
            )
        )
    if _manager_memory_contract_resolution_note_is_negative(
        response.get("validation_repair_resolution")
    ):
        response_mentions_contract = False
    else:
        response_mentions_contract = _manager_payload_mentions_any_term(
            response.get("validation_repair_resolution"),
            terms,
        )
    if _manager_memory_contract_resolution_note_is_negative(hold_decision):
        hold_mentions_contract = False
    else:
        hold_mentions_contract = _manager_payload_mentions_any_term(
            hold_decision,
            terms,
        )
    action_mentions_contract = _manager_actions_resolve_memory_contract_repair(
        actions=actions,
        terms=terms,
        target_symbols=target_symbols,
    )
    mentions_contract = (
        response_mentions_contract
        or hold_mentions_contract
        or action_mentions_contract
    )
    if not mentions_contract:
        return False
    return (
        (
            response_mentions_contract
            and _manager_response_has_concrete_repair_resolution(
                response,
                target_symbols=target_symbols,
            )
        )
        or (
            hold_mentions_contract
            and _manager_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                target_symbols,
            )
        )
        or action_mentions_contract
    )


def _manager_memory_contract_repair_resolution_summary(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> dict[str, Any]:
    if not _manager_prompt_requires_memory_contract_repair(prompt):
        return {
            "memory_contract_status": "inactive",
            "memory_contract_count": 0,
            "memory_contract_resolved_count": 0,
            "memory_contract_unresolved_count": 0,
            "memory_contract_missing_symbols": [],
            "memory_contract_missing_contracts": [],
            "memory_contract_missing_errors": [],
            "memory_contract_resolution_modes": [],
            "memory_contract_action_resolved_count": 0,
            "memory_contract_hold_resolved_count": 0,
            "memory_contract_response_resolved_count": 0,
            "memory_contract_rows": [],
        }
    terms = _manager_memory_contract_repair_terms(prompt)
    target_symbols = sorted(_manager_memory_contract_repair_target_symbols(prompt))
    details_by_symbol = _manager_memory_contract_repair_details_by_symbol(prompt)
    validation_repair_resolution = response.get("validation_repair_resolution")
    response_resolution_negative = _manager_memory_contract_resolution_note_is_negative(
        validation_repair_resolution
    )
    hold_resolution_negative = _manager_memory_contract_resolution_note_is_negative(
        hold_decision
    )
    if not target_symbols:
        response_resolved = (
            not response_resolution_negative
            and (
                (not terms)
                or _manager_payload_mentions_any_term(
                    validation_repair_resolution,
                    terms,
                )
            )
            and _manager_response_has_concrete_repair_resolution(response)
        )
        hold_resolved = (
            not hold_resolution_negative
            and (
                (not terms)
                or _manager_payload_mentions_any_term(hold_decision, terms)
            )
            and _manager_hold_has_concrete_next_step(hold_decision)
        )
        action_resolved = _manager_actions_resolve_memory_contract_repair(
            actions=actions,
            terms=terms,
            target_symbols=set(),
        )
        resolved = response_resolved or hold_resolved or action_resolved
        modes = []
        if action_resolved:
            modes.append("action_metadata")
        if hold_resolved:
            modes.append("hold_trigger")
        if response_resolved:
            modes.append("response_resolution")
        return {
            "memory_contract_status": "resolved" if resolved else "unresolved",
            "memory_contract_count": 1,
            "memory_contract_resolved_count": 1 if resolved else 0,
            "memory_contract_unresolved_count": 0 if resolved else 1,
            "memory_contract_missing_symbols": [],
            "memory_contract_missing_contracts": [] if resolved else terms[:12],
            "memory_contract_missing_errors": [],
            "memory_contract_resolution_modes": modes,
            "memory_contract_action_resolved_count": 1 if action_resolved else 0,
            "memory_contract_hold_resolved_count": 1 if hold_resolved else 0,
            "memory_contract_response_resolved_count": (
                1 if response_resolved else 0
            ),
            "memory_contract_rows": [],
        }

    resolved_symbols: list[str] = []
    action_resolved_count = 0
    hold_resolved_count = 0
    response_resolved_count = 0
    memory_contract_rows: list[dict[str, Any]] = []
    for symbol in target_symbols:
        symbol_set = {symbol}
        detail = details_by_symbol.get(symbol, {})
        response_resolved = (
            not response_resolution_negative
            and (
                (not terms)
                or _manager_payload_mentions_any_term(
                    validation_repair_resolution,
                    terms,
                )
            )
            and _manager_response_has_concrete_repair_resolution(
                response,
                target_symbols=symbol_set,
            )
        )
        hold_resolved = (
            not hold_resolution_negative
            and (
                (not terms)
                or _manager_payload_mentions_any_term(hold_decision, terms)
            )
            and _manager_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                symbol_set,
            )
        )
        action_resolved = _manager_actions_resolve_memory_contract_repair(
            actions=actions,
            terms=terms,
            target_symbols=symbol_set,
        )
        if response_resolved or hold_resolved or action_resolved:
            resolved_symbols.append(symbol)
        resolution_modes = [
            mode
            for mode, flag in (
                ("action_metadata", action_resolved),
                ("hold_trigger", hold_resolved),
                ("response_resolution", response_resolved),
            )
            if flag
        ]
        memory_contract_rows.append(
            {
                "symbol": symbol,
                "status": "resolved" if resolution_modes else "unresolved",
                "contracts": detail.get("contracts", [])[:4],
                "errors": detail.get("errors", [])[:4],
                "resolution_modes": resolution_modes,
            }
        )
        if action_resolved:
            action_resolved_count += 1
        if hold_resolved:
            hold_resolved_count += 1
        if response_resolved:
            response_resolved_count += 1
    missing_symbols = [
        symbol for symbol in target_symbols if symbol not in resolved_symbols
    ]
    missing_contracts: list[str] = []
    missing_errors: list[str] = []
    for symbol in missing_symbols:
        detail = details_by_symbol.get(symbol, {})
        for contract in detail.get("contracts", []):
            if contract not in missing_contracts:
                missing_contracts.append(contract)
        for error in detail.get("errors", []):
            if error not in missing_errors:
                missing_errors.append(error)
    status = (
        "resolved"
        if not missing_symbols
        else "partial"
        if resolved_symbols
        else "unresolved"
    )
    return {
        "memory_contract_status": status,
        "memory_contract_count": len(target_symbols),
        "memory_contract_resolved_count": len(resolved_symbols),
        "memory_contract_unresolved_count": len(missing_symbols),
        "memory_contract_missing_symbols": missing_symbols[:12],
        "memory_contract_missing_contracts": missing_contracts[:12],
        "memory_contract_missing_errors": missing_errors[:12],
        "memory_contract_resolution_modes": [
            mode
            for mode, count in (
                ("action_metadata", action_resolved_count),
                ("hold_trigger", hold_resolved_count),
                ("response_resolution", response_resolved_count),
            )
            if count > 0
        ],
        "memory_contract_action_resolved_count": action_resolved_count,
        "memory_contract_hold_resolved_count": hold_resolved_count,
        "memory_contract_response_resolved_count": response_resolved_count,
        "memory_contract_rows": memory_contract_rows[:12],
    }


def _manager_hold_has_prompt_linked_concrete_next_step(
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _manager_hold_has_concrete_next_step(hold_decision):
        return False
    terms = _manager_wiki_repair_reference_terms(prompt)
    if not terms:
        return True
    return _manager_payload_mentions_any_term(hold_decision, terms)


def _manager_requested_symbol_coverage_terms(prompt: dict[str, Any]) -> list[str]:
    coverage = _manager_scoped_requested_symbol_coverage(prompt)
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    missing = _as_list(coverage.get("missing_summary_symbols"))
    if missing:
        for symbol in missing:
            add(symbol)
        for symbol in _as_list(coverage.get("prompt_omitted_symbols")):
            add(symbol)
        return terms
    if "missing_summary_symbols" in coverage:
        return terms
    for symbol in _as_list(coverage.get("unsummarized_symbols")):
        add(symbol)
    for symbol in _as_list(coverage.get("prompt_omitted_symbols")):
        add(symbol)
    return terms


def _manager_requested_symbol_coverage_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_requested_symbol_coverage_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _manager_requested_symbol_coverage_note_is_negative(child)
            for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "coverage unresolved",
        "coverage still unresolved",
        "summary still missing",
        "still missing",
        "no fresh summary",
        "no fresh crypto wiki summary",
        "no fresh wiki summary",
        "no wiki summary",
        "without wiki summary",
        "no live cross check",
        "no live cross checks",
        "not resolved",
        "unresolved",
        "미해결",
        "요약 없음",
        "아직 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_requested_symbol_coverage_blocks_actions(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    coverage = _manager_scoped_requested_symbol_coverage(prompt)
    if bool(coverage.get("hard_blocker")):
        return True
    terms = _manager_requested_symbol_coverage_terms(prompt)
    if not terms:
        return True
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if _manager_payload_mentions_any_term(row, terms):
                return True
    return False


def _manager_requested_symbol_coverage_resolution_state(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any] | None = None,
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
    action_count: int | None = None,
) -> dict[str, bool]:
    gap = _manager_prompt_has_requested_symbol_coverage_gap(prompt)
    coverage = _manager_scoped_requested_symbol_coverage(prompt)
    hard_blocker = bool(coverage.get("hard_blocker"))
    blocks_actions = (
        gap
        and _manager_requested_symbol_coverage_blocks_actions(
            prompt=prompt,
            actions=actions,
        )
    )
    resolved_by_action = (
        blocks_actions
        and _manager_actions_resolve_requested_symbol_coverage(
            prompt=prompt,
            actions=actions,
        )
    )
    resolved_by_hold = (
        gap
        and _manager_hold_resolves_requested_symbol_coverage(
            prompt,
            hold_decision,
        )
    )
    if action_count is None:
        action_count = _manager_action_item_count(actions)
    resolved_by_response = (
        gap
        and not hard_blocker
        and action_count <= 0
        and _manager_prompt_has_active_validation_repair(prompt)
        and (
            _manager_response_has_prompt_linked_repair_resolution(
                prompt,
                response or {},
            )
            or _manager_response_rejects_all_visible_candidates_with_repair_evidence(
                prompt,
                response or {},
            )
        )
    )
    resolved = (
        gap
        and (
            (
                blocks_actions
                and (
                    resolved_by_action
                    or resolved_by_hold
                )
            )
            or (
                not blocks_actions
                and (
                    resolved_by_hold
                    or resolved_by_response
                    or action_count > 0
                )
            )
        )
    )
    return {
        "gap": bool(gap),
        "blocks_actions": bool(blocks_actions),
        "resolved_by_action": bool(resolved_by_action),
        "resolved_by_hold": bool(resolved_by_hold),
        "resolved_by_response": bool(resolved_by_response),
        "resolved": bool(resolved),
    }


def _manager_hold_resolves_requested_symbol_coverage(
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _manager_hold_has_concrete_next_step(hold_decision):
        return False
    if _manager_requested_symbol_coverage_note_is_negative(hold_decision):
        return False
    terms = _manager_requested_symbol_coverage_terms(prompt)
    if not terms:
        return True
    return _manager_payload_mentions_any_term(hold_decision, terms)


def _manager_actions_resolve_requested_symbol_coverage(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _manager_requested_symbol_coverage_terms(prompt)
    if not terms:
        return False
    resolution_terms = [
        "requested_symbol_coverage",
        "requested_symbol_summary",
        "summary_missing",
        "wiki_summary",
        "live_cross_check",
        "cross_check",
        "research_refresh",
        "crypto_research",
        "위키",
        "요약",
        "교차확인",
    ]
    metadata_keys = (
        "jue_wiki_requested_symbol_coverage",
        "jue_wiki_requested_symbol_coverage_resolution",
        "requested_symbol_coverage",
        "requested_symbol_coverage_resolution",
        "wiki_coverage_resolution",
    )
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            symbol_payload: dict[str, Any] = {
                "symbol": row.get("symbol"),
                "code": row.get("code"),
                "ticker": row.get("ticker"),
                "symbols": row.get("symbols"),
                "metadata_symbols": (
                    metadata.get("symbols")
                    or metadata.get("symbol")
                    or metadata.get("requested_symbols")
                ),
            }
            resolution_payload: dict[str, Any] = {
                "metadata": {
                    metadata_key: metadata.get(metadata_key)
                    for metadata_key in metadata_keys
                    if metadata.get(metadata_key) not in ({}, [], "", None)
                },
                "row": {
                    metadata_key: row.get(metadata_key)
                    for metadata_key in metadata_keys
                    if row.get(metadata_key) not in ({}, [], "", None)
                },
            }
            if _manager_requested_symbol_coverage_note_is_negative(resolution_payload):
                continue
            if _manager_payload_mentions_any_term(
                symbol_payload,
                terms,
            ) and _manager_payload_mentions_any_term(resolution_payload, resolution_terms):
                return True
    return False


def _manager_actions_resolve_wiki_selection_guidance(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _manager_wiki_selection_guidance_terms(prompt)
    if not terms:
        return False
    translation_terms = _manager_wiki_selection_guidance_translation_terms(prompt)
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_selection_resolution",
                "jue_wiki_freshness_cross_check",
            ):
                note = metadata.get(metadata_key)
                if note in (None, "", [], {}):
                    note = row.get(metadata_key)
                if not _manager_repair_note_is_concrete(note):
                    continue
                if _manager_payload_resolves_wiki_selection_guidance_terms(
                    payload={"note": note, "symbol": row.get("symbol")},
                    terms=terms,
                    translation_terms=translation_terms,
                ):
                    return True
    return False


def _manager_hold_resolves_wiki_selection_guidance(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    if not _manager_hold_has_concrete_next_step(hold_decision):
        return False
    terms = _manager_wiki_selection_guidance_terms(prompt)
    if not terms:
        return False
    translation_terms = _manager_wiki_selection_guidance_translation_terms(prompt)
    return _manager_payload_resolves_wiki_selection_guidance_terms(
        payload=hold_decision,
        terms=terms,
        translation_terms=translation_terms,
    )


def _manager_unavailable_wiki_context(prompt: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(prompt, dict):
        return {}
    candidates: list[Any] = [prompt.get("jue_wiki")]
    memory = prompt.get("memory")
    if isinstance(memory, dict):
        candidates.append(memory.get("jue_wiki"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if not _manager_wiki_memory_container_matches_scope(candidate):
            continue
        status = str(candidate.get("status") or "").strip().lower()
        available = candidate.get("available")
        if status in {"error", "disabled", "unavailable"} or available is False:
            return {
                "status": status or "unavailable",
                "reason": _clean_text(candidate.get("reason"), limit=160),
            }
    return {}


def _manager_prompt_has_unavailable_wiki_context(prompt: dict[str, Any]) -> bool:
    return bool(_manager_unavailable_wiki_context(prompt))


def _manager_wiki_context_gap_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_wiki_context_gap_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_manager_wiki_context_gap_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "unresolved",
        "not resolved",
        "gap unresolved",
        "context still unresolved",
        "no live cross check",
        "no live cross checks",
        "without live cross check",
        "without live cross checks",
        "live cross check missing",
        "live cross checks missing",
        "no crypto quant",
        "without crypto quant",
        "crypto quant missing",
        "no book",
        "without book",
        "book missing",
        "no spread",
        "spread missing",
        "no funding",
        "funding missing",
        "cross check missing",
        "cross checks missing",
        "not cross checked",
        "not checked",
        "미해결",
        "미확인",
        "교차확인 없음",
        "교차 확인 없음",
        "대체근거 없음",
        "대체 근거 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_actions_resolve_unavailable_wiki_context(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    gap = _manager_unavailable_wiki_context(prompt)
    if not gap or not isinstance(actions, dict):
        return False
    terms = [
        term
        for term in (
            "wiki",
            "위키",
            gap.get("status"),
            gap.get("reason"),
            "live_cross_check",
            "crypto_quant",
            "book",
            "spread",
            "funding",
            "live_authority",
            "research",
        )
        if str(term or "").strip()
    ]
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            note = metadata.get("jue_wiki_context_gap")
            if note in (None, "", [], {}):
                note = row.get("jue_wiki_context_gap")
            if not _manager_repair_note_is_concrete(note):
                continue
            if _manager_wiki_context_gap_note_is_negative(note):
                continue
            if _manager_payload_mentions_any_term({"note": note}, terms):
                return True
    return False


def _manager_hold_resolves_unavailable_wiki_context(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    gap = _manager_unavailable_wiki_context(prompt)
    if not gap or not _manager_hold_has_concrete_next_step(hold_decision):
        return False
    if _manager_wiki_context_gap_note_is_negative(hold_decision):
        return False
    return _manager_payload_mentions_any_term(
        hold_decision,
        [
            "wiki",
            "위키",
            gap.get("status"),
            gap.get("reason"),
            "live_cross_check",
            "crypto_quant",
            "book",
            "spread",
            "funding",
            "live_authority",
            "research",
        ],
    )


def _manager_actions_resolve_wiki_action_reference_memory(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _manager_wiki_action_reference_terms(prompt)
    translation_terms = _manager_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _manager_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _manager_prompt_wiki_reference_symbol_page_ids(prompt)
    block_symbol_map = _manager_prompt_block_symbol_map(prompt)
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_reference_basis",
                "jue_wiki_freshness_cross_check",
                "jue_wiki_selection_resolution",
                "jue_wiki_action_reference_recovery",
                "jue_wiki_context_gap",
            ):
                note = metadata.get(metadata_key)
                if note in (None, "", [], {}):
                    note = row.get(metadata_key)
                if not _manager_repair_note_is_concrete(note):
                    continue
                action_symbols = _manager_action_identity_symbols_with_block_map(
                    row,
                    block_symbol_map=block_symbol_map,
                )
                if _manager_action_identity_block_ids(row) and not action_symbols:
                    continue
                if _manager_payload_resolves_wiki_action_reference_terms(
                    payload={"note": note, "symbol": row.get("symbol")},
                    terms=terms,
                    translation_terms=translation_terms,
                    allowed_page_ids=allowed_page_ids,
                    required_symbol_page_ids=required_symbol_page_ids,
                    action_symbols=action_symbols,
                ):
                    return True
    return False


def _manager_actions_resolve_wiki_action_reference_recovery(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    if not isinstance(actions, dict):
        return False
    terms = _manager_wiki_action_reference_terms(prompt)
    translation_terms = _manager_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _manager_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _manager_prompt_wiki_reference_symbol_page_ids(prompt)
    block_symbol_map = _manager_prompt_block_symbol_map(prompt)
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            note = metadata.get("jue_wiki_action_reference_recovery")
            if note in (None, "", [], {}):
                note = row.get("jue_wiki_action_reference_recovery")
            if not _manager_repair_note_is_concrete(note):
                continue
            action_symbols = _manager_action_identity_symbols_with_block_map(
                row,
                block_symbol_map=block_symbol_map,
            )
            if _manager_action_identity_block_ids(row) and not action_symbols:
                continue
            if _manager_payload_resolves_wiki_action_reference_terms(
                payload={"note": note, "symbol": row.get("symbol")},
                terms=terms,
                translation_terms=translation_terms,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                action_symbols=action_symbols,
            ):
                return True
    return False


def _manager_hold_resolves_wiki_action_reference_memory(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    terms = _manager_wiki_action_reference_terms(prompt)
    translation_terms = _manager_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _manager_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _manager_prompt_wiki_reference_symbol_page_ids(prompt)
    hold_symbols = _manager_hold_identity_symbols(hold_decision)
    if _manager_hold_has_concrete_next_step(hold_decision):
        return _manager_payload_resolves_wiki_action_reference_terms(
            payload=hold_decision,
            terms=terms,
            translation_terms=translation_terms,
            allowed_page_ids=allowed_page_ids,
            required_symbol_page_ids=required_symbol_page_ids,
            action_symbols=hold_symbols,
            require_selected_symbol_page_reference=True,
        )
    hold = hold_decision if isinstance(hold_decision, dict) else {}
    metadata = hold.get("metadata") if isinstance(hold.get("metadata"), dict) else {}
    recovery_note = metadata.get("jue_wiki_action_reference_recovery")
    if recovery_note in (None, "", [], {}):
        recovery_note = hold.get("jue_wiki_action_reference_recovery")
    if not _manager_repair_note_is_concrete(recovery_note):
        return False
    return _manager_payload_resolves_wiki_action_reference_terms(
        payload={"note": recovery_note},
        terms=terms,
        translation_terms=translation_terms,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
        action_symbols=hold_symbols,
        require_selected_symbol_page_reference=True,
    )


def _manager_hold_resolves_wiki_action_reference_recovery(
    *,
    prompt: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    hold = hold_decision if isinstance(hold_decision, dict) else {}
    metadata = hold.get("metadata") if isinstance(hold.get("metadata"), dict) else {}
    recovery_note = metadata.get("jue_wiki_action_reference_recovery")
    if recovery_note in (None, "", [], {}):
        recovery_note = hold.get("jue_wiki_action_reference_recovery")
    if not _manager_repair_note_is_concrete(recovery_note):
        return False
    terms = _manager_wiki_action_reference_terms(prompt)
    translation_terms = _manager_wiki_action_reference_translation_terms(prompt)
    allowed_page_ids = _manager_prompt_wiki_reference_page_ids(prompt)
    required_symbol_page_ids = _manager_prompt_wiki_reference_symbol_page_ids(prompt)
    hold_symbols = _manager_hold_identity_symbols(hold_decision)
    return _manager_payload_resolves_wiki_action_reference_terms(
        payload={"note": recovery_note},
        terms=terms,
        translation_terms=translation_terms,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
        action_symbols=hold_symbols,
        require_selected_symbol_page_reference=True,
    )


def _manager_prompt_has_applicable_wiki_context(prompt: dict[str, Any]) -> bool:
    if not isinstance(prompt, dict):
        return False
    if _manager_prompt_has_unavailable_wiki_context(prompt):
        return False
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    if _as_list(application.get("selected_page_ids")):
        return True
    wiki = prompt.get("jue_wiki") if isinstance(prompt.get("jue_wiki"), dict) else {}
    if _as_list(wiki.get("pages")):
        return True
    memory = prompt.get("memory") if isinstance(prompt.get("memory"), dict) else {}
    memory_wiki = (
        memory.get("jue_wiki") if isinstance(memory.get("jue_wiki"), dict) else {}
    )
    if memory_wiki and str(memory_wiki.get("status") or "").lower() not in {
        "error",
        "disabled",
        "unavailable",
    }:
        return bool(_as_list(memory_wiki.get("pages")))
    return False


def _manager_wiki_reference_symbols_from_payload(value: Any) -> set[str]:
    text = _json_dumps(value).upper()
    return {
        match
        for match in re.findall(r"BINANCE\.SYMBOL\.([A-Z0-9]+)", text)
        if match
    }


def _manager_wiki_reference_page_ids_from_payload(value: Any) -> set[str]:
    text = _json_dumps(value).upper()
    return {
        match.lower()
        for match in re.findall(
            r"BINANCE\.(?:SYMBOL\.[A-Z0-9]+|OPS\.[A-Z0-9_.:-]+)",
            text,
        )
    }


def _manager_payload_uses_only_allowed_wiki_page_ids(
    value: Any,
    *,
    allowed_page_ids: set[str] | None,
) -> bool:
    page_ids = _manager_wiki_reference_page_ids_from_payload(value)
    if not page_ids or not allowed_page_ids:
        return True
    return not (page_ids - allowed_page_ids)


def _manager_payload_wiki_page_ids_match_action_symbols(
    value: Any,
    *,
    action_symbols: set[str] | None,
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> bool:
    page_ids = _manager_wiki_reference_page_ids_from_payload(value)
    referenced_symbol_page_ids = {
        page_id
        for page_id in page_ids
        if re.fullmatch(r"binance\.symbol\.[a-z0-9]+", page_id)
    }
    if not action_symbols:
        return True
    expected_symbol_page_ids: set[str] = set()
    for symbol in action_symbols:
        expected_symbol_page_ids.update(
            (required_symbol_page_ids or {}).get(symbol.upper(), set())
        )
    if expected_symbol_page_ids and not referenced_symbol_page_ids:
        return False
    if not referenced_symbol_page_ids:
        return True
    if not expected_symbol_page_ids:
        return not bool(required_symbol_page_ids)
    return not (referenced_symbol_page_ids - expected_symbol_page_ids)


def _manager_payload_has_selected_symbol_wiki_page_reference(
    value: Any,
    *,
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> bool:
    selected_symbol_page_ids: set[str] = set()
    for page_ids in (required_symbol_page_ids or {}).values():
        selected_symbol_page_ids.update(page_ids)
    if not selected_symbol_page_ids:
        return True
    page_ids = _manager_wiki_reference_page_ids_from_payload(value)
    referenced_symbol_page_ids = {
        page_id
        for page_id in page_ids
        if re.fullmatch(r"binance\.symbol\.[a-z0-9]+", page_id)
    }
    if not referenced_symbol_page_ids:
        return False
    return not (referenced_symbol_page_ids - selected_symbol_page_ids)


def _manager_prompt_wiki_reference_page_ids(prompt: dict[str, Any]) -> set[str]:
    selected_page_ids: set[str] = set()
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    for page_id in _as_list(application.get("selected_page_ids")):
        text = str(page_id or "").strip().lower()
        if text:
            selected_page_ids.add(text)
    if selected_page_ids:
        return selected_page_ids
    page_ids: set[str] = set()
    wiki = prompt.get("jue_wiki") if isinstance(prompt.get("jue_wiki"), dict) else {}
    for row in _as_list(wiki.get("pages")):
        if not isinstance(row, dict):
            continue
        text = str(row.get("page_id") or "").strip().lower()
        if text:
            page_ids.add(text)
    return page_ids


def _manager_prompt_wiki_reference_symbol_page_ids(
    prompt: dict[str, Any],
) -> dict[str, set[str]]:
    symbol_page_ids: dict[str, set[str]] = {}
    for page_id in _manager_prompt_wiki_reference_page_ids(prompt):
        match = re.fullmatch(r"binance\.symbol\.([a-z0-9]+)", page_id)
        if not match:
            continue
        symbol = match.group(1).upper()
        symbol_page_ids.setdefault(symbol, set()).add(page_id)
    return symbol_page_ids


def _manager_prompt_block_symbol_map(prompt: dict[str, Any]) -> dict[str, set[str]]:
    block_symbol_map, _block_pair_map, _block_by_id = (
        _manager_prompt_block_identity_context(prompt)
    )
    return block_symbol_map


def _manager_prompt_block_identity_context(
    prompt: dict[str, Any],
) -> tuple[
    dict[str, set[str]],
    dict[str, set[tuple[str, str]]],
    dict[str, dict[str, Any]],
]:
    block_symbol_map: dict[str, set[str]] = {}
    block_pair_map: dict[str, set[tuple[str, str]]] = {}
    block_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(prompt, dict):
        return block_symbol_map, block_pair_map, block_by_id
    for section in ("blocks", "open_blocks", "active_blocks"):
        for row in _as_list(prompt.get(section)):
            if not isinstance(row, dict):
                continue
            symbols = _manager_action_identity_symbols(row)
            if not symbols:
                continue
            raw_market = row.get("market") or row.get("venue")
            market = (
                normalize_market(raw_market)
                if raw_market not in (None, "")
                else ""
            )
            for block_id in _manager_action_identity_block_ids(row):
                block_symbol_map.setdefault(block_id, set()).update(symbols)
                block_by_id.setdefault(block_id, row)
                if market:
                    block_pair_map.setdefault(block_id, set()).update(
                        (symbol, market) for symbol in symbols
                    )
    return block_symbol_map, block_pair_map, block_by_id


def _manager_action_symbol_market_identities(
    row: dict[str, Any],
    *,
    block_symbols_by_id: dict[str, set[str]] | None = None,
    block_pairs_by_id: dict[str, set[tuple[str, str]]] | None = None,
) -> list[tuple[str, str]]:
    raw_market = row.get("market") or row.get("venue")
    market = normalize_market(raw_market) if raw_market not in (None, "") else ""
    identities: list[tuple[str, str]] = [
        (symbol, market)
        for symbol in sorted(_manager_action_identity_symbols(row))
    ]
    block_symbols_by_id = block_symbols_by_id or {}
    block_pairs_by_id = block_pairs_by_id or {}
    for block_id in _manager_action_identity_block_ids(row):
        block_pairs = sorted(block_pairs_by_id.get(block_id, set()))
        identities.extend(block_pairs)
        if market:
            identities.extend(
                (symbol, market)
                for symbol in sorted(block_symbols_by_id.get(block_id, set()))
            )
        elif not block_pairs:
            identities.extend(
                (symbol, "")
                for symbol in sorted(block_symbols_by_id.get(block_id, set()))
            )
    return list(dict.fromkeys(identities))


def _manager_wiki_reference_has_traceable_id(value: Any) -> bool:
    text = _json_dumps(value).upper()
    return "BINANCE.SYMBOL." in text or "BINANCE.OPS." in text


def _manager_wiki_reference_matches_action_symbols(
    row: dict[str, Any],
    value: Any,
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> bool:
    if not _manager_wiki_reference_has_traceable_id(value):
        return False
    page_ids = _manager_wiki_reference_page_ids_from_payload(value)
    if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
        return False
    action_symbols = _manager_action_identity_symbols_with_block_map(
        row,
        block_symbol_map=block_symbol_map,
    )
    expected_symbol_page_ids: set[str] = set()
    for symbol in action_symbols:
        expected_symbol_page_ids.update(
            (required_symbol_page_ids or {}).get(symbol.upper(), set())
        )
    if allowed_page_ids and action_symbols and not expected_symbol_page_ids:
        return False
    if expected_symbol_page_ids:
        referenced_symbol_page_ids = {
            page_id
            for page_id in page_ids
            if re.fullmatch(r"binance\.symbol\.[a-z0-9]+", page_id)
        }
        if referenced_symbol_page_ids - expected_symbol_page_ids:
            return False
        return bool(page_ids & expected_symbol_page_ids)
    reference_symbols = _manager_wiki_reference_symbols_from_payload(value)
    if not reference_symbols:
        return True
    if _manager_action_identity_block_ids(row):
        return False
    return not action_symbols or bool(reference_symbols & action_symbols)


def _manager_wiki_reference_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_wiki_reference_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_manager_wiki_reference_note_is_negative(child) for child in value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "wiki missing",
        "wiki unavailable",
        "wiki not available",
        "no wiki",
        "without wiki",
        "no fresh context",
        "fresh context unavailable",
        "fresh jue wiki context unavailable",
        "jue wiki missing",
        "jue wiki unavailable",
        "위키 없음",
        "위키 누락",
        "위키 미확인",
        "위키 근거 없음",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_action_has_wiki_reference(
    row: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> bool:
    if not isinstance(row, dict):
        return False
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for source in (metadata, row):
        for key, value in source.items():
            if not str(key or "").startswith("jue_wiki"):
                continue
            if _manager_wiki_reference_note_is_negative(value):
                continue
            if _manager_repair_note_is_concrete(value):
                return _manager_wiki_reference_matches_action_symbols(
                    row,
                    value,
                    allowed_page_ids=allowed_page_ids,
                    required_symbol_page_ids=required_symbol_page_ids,
                    block_symbol_map=block_symbol_map,
                )
    evidence_payload = {
        "evidence_refs": row.get("evidence_refs"),
        "evidence": row.get("evidence"),
        "metadata_evidence_refs": metadata.get("evidence_refs"),
    }
    if _manager_wiki_reference_note_is_negative(evidence_payload):
        return False
    return _manager_payload_mentions_any_term(
        evidence_payload,
        [
            "jue_wiki",
            "fresh_jue_wiki_context",
            "wiki",
            "위키",
            "binance.symbol.",
            "binance.ops.",
        ],
    ) and _manager_wiki_reference_matches_action_symbols(
        row,
        evidence_payload,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
        block_symbol_map=block_symbol_map,
    )


def _manager_hold_wiki_reference_matches_context(
    value: Any,
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> bool:
    if not _manager_wiki_reference_has_traceable_id(value):
        return False
    page_ids = _manager_wiki_reference_page_ids_from_payload(value)
    if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
        return False
    selected_symbol_page_ids: set[str] = set()
    for page_id_set in (required_symbol_page_ids or {}).values():
        selected_symbol_page_ids.update(page_id_set)
    if selected_symbol_page_ids:
        return bool(page_ids & selected_symbol_page_ids)
    return True


def _manager_hold_has_wiki_reference(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> bool:
    if not isinstance(hold_decision, dict):
        return False
    metadata = (
        hold_decision.get("metadata")
        if isinstance(hold_decision.get("metadata"), dict)
        else {}
    )
    for source in (metadata, hold_decision):
        for key, value in source.items():
            if not str(key or "").startswith("jue_wiki"):
                continue
            if _manager_wiki_reference_note_is_negative(value):
                continue
            if (
                _manager_repair_note_is_concrete(value)
                or _manager_payload_mentions_any_term(
                    value,
                    [
                        "jue_wiki",
                        "fresh_jue_wiki_context",
                        "wiki",
                        "위키",
                        "binance.symbol.",
                        "binance.ops.",
                    ],
                )
            ):
                return _manager_hold_wiki_reference_matches_context(
                    value,
                    allowed_page_ids=allowed_page_ids,
                    required_symbol_page_ids=required_symbol_page_ids,
                )
    evidence_payload = {
        "evidence_refs": hold_decision.get("evidence_refs"),
        "evidence": hold_decision.get("evidence"),
        "metadata_evidence_refs": metadata.get("evidence_refs"),
    }
    if _manager_wiki_reference_note_is_negative(evidence_payload):
        return False
    return _manager_payload_mentions_any_term(
        evidence_payload,
        [
            "jue_wiki",
            "fresh_jue_wiki_context",
            "wiki",
            "위키",
            "binance.symbol.",
            "binance.ops.",
        ],
    ) and _manager_hold_wiki_reference_matches_context(
        evidence_payload,
        allowed_page_ids=allowed_page_ids,
        required_symbol_page_ids=required_symbol_page_ids,
    )


def _manager_required_symbol_page_entries(
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for symbol, page_ids in sorted((required_symbol_page_ids or {}).items()):
        for page_id in sorted(page_ids):
            entries.append((page_id, symbol.upper()))
    return entries


def _manager_hold_required_symbol_page_ids(
    hold_decision: dict[str, Any],
    required_symbol_page_ids: dict[str, set[str]] | None,
) -> dict[str, set[str]]:
    target_symbols = _manager_hold_identity_symbols(hold_decision)
    if not target_symbols:
        return dict(required_symbol_page_ids or {})
    return {
        symbol: set(page_ids)
        for symbol, page_ids in (required_symbol_page_ids or {}).items()
        if symbol.upper() in target_symbols
    }


def _manager_hold_uncovered_target_symbols(
    hold_decision: dict[str, Any],
    required_symbol_page_ids: dict[str, set[str]] | None,
    *,
    allowed_page_ids: set[str] | None = None,
) -> set[str]:
    target_symbols = _manager_hold_identity_symbols(hold_decision)
    if not target_symbols:
        return set()
    if not required_symbol_page_ids:
        return set(target_symbols) if allowed_page_ids else set()
    covered_symbols = {
        str(symbol or "").strip().upper()
        for symbol in required_symbol_page_ids
        if str(symbol or "").strip()
    }
    return target_symbols - covered_symbols


def _manager_hold_wiki_reference_page_ids(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
) -> set[str]:
    if not isinstance(hold_decision, dict):
        return set()
    metadata = (
        hold_decision.get("metadata")
        if isinstance(hold_decision.get("metadata"), dict)
        else {}
    )
    referenced_page_ids: set[str] = set()
    for source in (metadata, hold_decision):
        for key, value in source.items():
            if not str(key or "").startswith("jue_wiki"):
                continue
            if _manager_wiki_reference_note_is_negative(value):
                continue
            if not (
                _manager_repair_note_is_concrete(value)
                or _manager_payload_mentions_any_term(
                    value,
                    [
                        "jue_wiki",
                        "fresh_jue_wiki_context",
                        "wiki",
                        "위키",
                        "binance.symbol.",
                        "binance.ops.",
                    ],
                )
            ):
                continue
            page_ids = _manager_wiki_reference_page_ids_from_payload(value)
            if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
                continue
            if allowed_page_ids:
                page_ids &= allowed_page_ids
            referenced_page_ids.update(page_ids)
    evidence_payload = {
        "evidence_refs": hold_decision.get("evidence_refs"),
        "evidence": hold_decision.get("evidence"),
        "metadata_evidence_refs": metadata.get("evidence_refs"),
    }
    if not _manager_wiki_reference_note_is_negative(
        evidence_payload
    ) and _manager_payload_mentions_any_term(
        evidence_payload,
        [
            "jue_wiki",
            "fresh_jue_wiki_context",
            "wiki",
            "위키",
            "binance.symbol.",
            "binance.ops.",
        ],
    ):
        page_ids = _manager_wiki_reference_page_ids_from_payload(evidence_payload)
        if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
            return referenced_page_ids
        if allowed_page_ids:
            page_ids &= allowed_page_ids
        referenced_page_ids.update(page_ids)
    return referenced_page_ids


def _manager_hold_wiki_reference_missing_symbol_pages(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    referenced_page_ids = _manager_hold_wiki_reference_page_ids(
        hold_decision,
        allowed_page_ids=allowed_page_ids,
    )
    return [
        {"section": "hold_decision", "page_id": page_id, "symbol": symbol}
        for page_id, symbol in _manager_required_symbol_page_entries(
            required_symbol_page_ids
        )
        if page_id not in referenced_page_ids
    ]


def _manager_hold_wiki_reference_count(
    hold_decision: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> int:
    required_entries = _manager_required_symbol_page_entries(required_symbol_page_ids)
    if required_entries:
        referenced_page_ids = _manager_hold_wiki_reference_page_ids(
            hold_decision,
            allowed_page_ids=allowed_page_ids,
        )
        return sum(1 for page_id, _symbol in required_entries if page_id in referenced_page_ids)
    return (
        1
        if _manager_hold_has_wiki_reference(
            hold_decision,
            allowed_page_ids=allowed_page_ids,
            required_symbol_page_ids=required_symbol_page_ids,
        )
        else 0
    )


def _manager_action_wiki_reference_count(
    actions: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> int:
    if not isinstance(actions, dict):
        return 0
    count = 0
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if isinstance(row, dict) and _manager_action_has_wiki_reference(
                row,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                block_symbol_map=block_symbol_map,
            ):
                count += 1
    return count


def _manager_action_wiki_reference_missing_actions(
    actions: dict[str, Any],
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not isinstance(actions, dict):
        return []
    missing: list[dict[str, Any]] = []
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict) or _manager_action_has_wiki_reference(
                row,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                block_symbol_map=block_symbol_map,
            ):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            symbols = sorted(
                _manager_action_identity_symbols_with_block_map(
                    row,
                    block_symbol_map=block_symbol_map,
                )
            )
            summary: dict[str, Any] = {"section": key}
            if row.get("block_id") not in (None, "", [], {}):
                summary["block_id"] = _clean_text(row.get("block_id"), limit=80)
            if symbols:
                summary["symbol"] = symbols[0]
            market = _clean_text(row.get("market") or metadata.get("market"), limit=40)
            if market:
                summary["market"] = market
            side = _clean_text(row.get("side") or metadata.get("side"), limit=40)
            if side:
                summary["side"] = side
            lane = _clean_text(row.get("lane") or metadata.get("lane"), limit=80)
            if lane:
                summary["lane"] = lane
            horizon = _clean_text(
                row.get("horizon") or metadata.get("horizon"),
                limit=40,
            )
            if horizon:
                summary["horizon"] = horizon
            reason = _clean_text(row.get("reason") or metadata.get("reason"), limit=120)
            if reason:
                summary["reason"] = reason
            missing.append(summary)
            if len(missing) >= max(int(limit), 1):
                return missing
    return missing


def _manager_prompt_has_wiki_usage_contract(prompt: dict[str, Any]) -> bool:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    trust_profile = (
        application.get("trust_profile")
        if isinstance(application.get("trust_profile"), dict)
        else {}
    )
    usage_contract = (
        trust_profile.get("usage_contract")
        if isinstance(trust_profile.get("usage_contract"), dict)
        else {}
    )
    if usage_contract:
        return True
    for memory_key in ("memory", "investment_memory"):
        memory = (
            prompt.get(memory_key)
            if isinstance(prompt.get(memory_key), dict)
            else {}
        )
        usage_memory = (
            memory.get("jue_wiki_usage_contract_memory")
            if isinstance(memory.get("jue_wiki_usage_contract_memory"), dict)
            else {}
        )
        if _manager_payload_mentions_any_term(
            usage_memory,
            [
                "jue_wiki_usage_contract_resolution",
                "required_evidence",
                "usage_contract",
                "usage contract",
            ],
        ):
            return True
    workflow = (
        prompt.get("jue_workflow")
        if isinstance(prompt.get("jue_workflow"), dict)
        else {}
    )
    for contract in _as_list(workflow.get("contracts")):
        if not isinstance(contract, dict):
            continue
        if (
            contract.get("contract_id") == "jue_wiki_usage_contract_resolution"
            and _manager_prompt_has_applicable_wiki_context(prompt)
        ):
            return True
    return False


def _manager_wiki_usage_contract_required_terms(prompt: dict[str, Any]) -> list[str]:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    trust_profile = (
        application.get("trust_profile")
        if isinstance(application.get("trust_profile"), dict)
        else {}
    )
    usage_contract = (
        trust_profile.get("usage_contract")
        if isinstance(trust_profile.get("usage_contract"), dict)
        else {}
    )
    terms: list[str] = []

    def add_term(value: Any) -> None:
        term = str(value or "").strip().lower()
        if (
            term
            and term != "jue_wiki_usage_contract_resolution"
            and term not in terms
        ):
            terms.append(term)

    for key in ("required_cross_checks",):
        for value in _as_list(usage_contract.get(key)):
            add_term(value)

    for memory_key in ("memory", "investment_memory"):
        memory = (
            prompt.get(memory_key)
            if isinstance(prompt.get(memory_key), dict)
            else {}
        )
        usage_memory = (
            memory.get("jue_wiki_usage_contract_memory")
            if isinstance(memory.get("jue_wiki_usage_contract_memory"), dict)
            else {}
        )
        for item in _as_list(usage_memory.get("items")):
            if not isinstance(item, dict):
                continue
            guidance = (
                item.get("application_guidance")
                if isinstance(item.get("application_guidance"), dict)
                else {}
            )
            for key in ("required_cross_checks", "cross_checks", "required_terms"):
                for value in _as_list(guidance.get(key)):
                    add_term(value)
    return terms


def _manager_usage_contract_resolution_is_concrete(
    value: Any,
    *,
    required_terms: list[str] | None = None,
) -> bool:
    text = _clean_text(value, limit=800)
    if not text:
        return False
    compact = (
        text.strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "usage contract resolution missing",
        "contract resolution missing",
        "resolution missing",
        "resolution unavailable",
        "not resolved",
        "not checked",
        "not cross checked",
        "cross check missing",
        "cross checks missing",
        "cross check unavailable",
        "cross checks unavailable",
        "no cross check",
        "no cross checks",
        "no live spread",
        "no funding",
        "no liquidation distance",
        "no orderbook depth",
        "교차확인 없음",
        "교차 확인 없음",
        "계약 해결 누락",
        "사용 계약 해결 누락",
        "미해결",
        "미확인",
    )
    if any(phrase in compact for phrase in negative_phrases):
        return False
    for term in required_terms or []:
        normalized_term = (
            str(term or "")
            .strip()
            .lower()
            .replace("_", " ")
            .replace("-", " ")
            .replace("/", " ")
        )
        if normalized_term and normalized_term not in compact:
            return False
    return True


def _manager_action_wiki_usage_contract_resolution_count(
    actions: dict[str, Any],
    *,
    required_terms: list[str] | None = None,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> int:
    if not isinstance(actions, dict):
        return 0
    count = 0
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            resolution = _clean_text(
                metadata.get("jue_wiki_usage_contract_resolution")
                or row.get("jue_wiki_usage_contract_resolution"),
                limit=800,
            )
            if _manager_usage_contract_resolution_is_concrete(
                resolution,
                required_terms=required_terms,
            ) and _manager_usage_contract_resolution_matches_action_symbols(
                row,
                resolution,
                allowed_page_ids=allowed_page_ids,
                required_symbol_page_ids=required_symbol_page_ids,
                block_symbol_map=block_symbol_map,
            ):
                count += 1
    return count


def _manager_usage_contract_resolution_matches_action_symbols(
    row: dict[str, Any],
    resolution: Any,
    *,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
    block_symbol_map: dict[str, set[str]] | None = None,
) -> bool:
    action_symbols = _manager_action_identity_symbols_with_block_map(
        row,
        block_symbol_map=block_symbol_map,
    )
    expected_symbol_page_ids: set[str] = set()
    for symbol in action_symbols:
        expected_symbol_page_ids.update(
            (required_symbol_page_ids or {}).get(symbol.upper(), set())
        )
    if allowed_page_ids and action_symbols and not expected_symbol_page_ids:
        return False
    page_ids = _manager_wiki_reference_page_ids_from_payload(resolution)
    if not page_ids:
        if _manager_action_identity_block_ids(row) and expected_symbol_page_ids:
            text = _json_dumps(resolution).upper()
            return any(symbol.upper() in text for symbol in action_symbols)
        selected_symbol_page_count = sum(
            len(page_ids) for page_ids in (required_symbol_page_ids or {}).values()
        )
        if selected_symbol_page_count > 1 and expected_symbol_page_ids:
            text = _json_dumps(resolution).upper()
            return any(symbol.upper() in text for symbol in action_symbols)
        return True
    if allowed_page_ids and page_ids - allowed_page_ids:
        return False
    if expected_symbol_page_ids:
        referenced_symbol_page_ids = {
            page_id
            for page_id in page_ids
            if re.fullmatch(r"binance\.symbol\.[a-z0-9]+", page_id)
        }
        if referenced_symbol_page_ids - expected_symbol_page_ids:
            return False
        return bool(page_ids & expected_symbol_page_ids)
    reference_symbols = _manager_wiki_reference_symbols_from_payload(resolution)
    if not reference_symbols:
        return True
    if _manager_action_identity_block_ids(row):
        return False
    return not action_symbols or bool(reference_symbols & action_symbols)


def _manager_hold_decision_has_payload(hold_decision: dict[str, Any]) -> bool:
    return isinstance(hold_decision, dict) and bool(hold_decision)


def _manager_hold_wiki_usage_contract_resolution_count(
    hold_decision: dict[str, Any],
    *,
    required_terms: list[str] | None = None,
    allowed_page_ids: set[str] | None = None,
    required_symbol_page_ids: dict[str, set[str]] | None = None,
) -> int:
    if not isinstance(hold_decision, dict):
        return 0
    metadata = (
        hold_decision.get("metadata")
        if isinstance(hold_decision.get("metadata"), dict)
        else {}
    )
    resolution = _clean_text(
        metadata.get("jue_wiki_usage_contract_resolution")
        or hold_decision.get("jue_wiki_usage_contract_resolution"),
        limit=800,
    )
    if not _manager_usage_contract_resolution_is_concrete(
        resolution,
        required_terms=required_terms,
    ):
        return 0
    required_entries = _manager_required_symbol_page_entries(
        required_symbol_page_ids
    )
    if len(required_entries) <= 1:
        page_ids = _manager_wiki_reference_page_ids_from_payload(resolution)
        if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
            return 0
        if page_ids and required_entries:
            required_page_id = required_entries[0][0]
            return 1 if required_page_id in page_ids else 0
        return 1
    page_ids = _manager_wiki_reference_page_ids_from_payload(resolution)
    if page_ids and allowed_page_ids and page_ids - allowed_page_ids:
        return 0
    if allowed_page_ids:
        page_ids &= allowed_page_ids
    return sum(1 for page_id, _symbol in required_entries if page_id in page_ids)


def _manager_prompt_has_wiki_decision_adjustments(prompt: dict[str, Any]) -> bool:
    contract = (
        prompt.get("jue_wiki_decision_adjustments")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if not _manager_wiki_memory_container_matches_scope(contract):
        return False
    status = str(contract.get("status") or "").strip().lower()
    return status == "active" and bool(
        _manager_wiki_decision_adjustment_rows(prompt)
    )


def _manager_wiki_decision_adjustment_rows(
    prompt: dict[str, Any],
) -> list[dict[str, Any]]:
    contract = (
        prompt.get("jue_wiki_decision_adjustments")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if not _manager_wiki_memory_container_matches_scope(contract):
        return []
    translated = _manager_wiki_memory_transferability_is_translated(contract)
    return [
        row
        for row in _as_list(contract.get("adjustments"))
        if isinstance(row, dict)
        and _manager_wiki_memory_item_matches_scope(
            row,
            inherited_translated=translated,
        )
    ]


def _manager_wiki_decision_adjustment_terms(prompt: dict[str, Any]) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for row in _manager_wiki_decision_adjustment_rows(prompt):
        for key in (
            "action",
            "target_risk_posture",
            "reason",
            "current_risk_posture",
            "current_status",
        ):
            add(row.get(key))
        for key in ("recommended_allowed_uses", "deprioritized_allowed_uses"):
            for item in _as_list(row.get(key)):
                add(item)
    return terms


def _manager_wiki_decision_adjustment_evidence_terms(
    prompt: dict[str, Any],
) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for row in _manager_wiki_decision_adjustment_rows(prompt):
        evidence_grade = (
            row.get("evidence_grade")
            if isinstance(row.get("evidence_grade"), dict)
            else {}
        )
        if not evidence_grade:
            continue
        for key in ("instruction", "status", "basis"):
            add(evidence_grade.get(key))
    return terms


def _manager_wiki_decision_adjustment_execution_hint_terms(
    prompt: dict[str, Any],
) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text and text not in terms:
            terms.append(text)

    for row in _manager_wiki_decision_adjustment_rows(prompt):
        for key in (
            "decision_adjustment_effectiveness",
            "decision_adjustment_audit_effectiveness",
        ):
            effectiveness = row.get(key) if isinstance(row.get(key), dict) else {}
            add(effectiveness.get("execution_hint"))
    return terms


def _manager_wiki_decision_adjustment_translation_terms(
    prompt: dict[str, Any],
) -> list[str]:
    contract = (
        prompt.get("jue_wiki_decision_adjustments")
        if isinstance(prompt, dict)
        else {}
    )
    contract = contract if isinstance(contract, dict) else {}
    if not _manager_wiki_memory_container_matches_scope(contract):
        return []
    inherited_translated = _manager_wiki_memory_transferability_is_translated(
        contract
    )
    has_translated_adjustment = any(
        isinstance(row, dict)
        and (
            inherited_translated
            or _manager_wiki_memory_transferability_is_translated(row)
        )
        for row in _manager_wiki_decision_adjustment_rows(prompt)
    )
    if not has_translated_adjustment:
        return []
    return [
        "translated_crypto_mapping",
        "crypto_translation_mapping",
        "cross_venue_mapping",
        "cross_scope_mapping",
        "translated_policy_context",
    ]


def _manager_wiki_decision_adjustment_note_is_negative(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _manager_wiki_decision_adjustment_note_is_negative(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _manager_wiki_decision_adjustment_note_is_negative(child)
            for child in value
        )
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(";", " ")
        .replace(",", " ")
    )
    negative_phrases = (
        "decision adjustment not applied",
        "wiki decision adjustment not applied",
        "adjustment not applied",
        "decision adjustment unresolved",
        "adjustment unresolved",
        "decision adjustment unavailable",
        "adjustment unavailable",
        "decision adjustment missing",
        "adjustment missing",
        "evidence unavailable",
        "execution not performed",
        "execution unavailable",
        "not performed",
        "not applied",
        "보정 미적용",
        "보정 미해결",
        "보정 불가",
        "적용 못함",
        "실행 안함",
        "실행 못함",
    )
    return any(phrase in compact for phrase in negative_phrases)


def _manager_actions_have_prompt_linked_decision_adjustment_metadata(
    prompt: dict[str, Any],
    actions: dict[str, Any],
    *,
    required_terms: list[str] | None = None,
) -> bool:
    terms = _manager_wiki_decision_adjustment_terms(prompt)
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for metadata_key in (
                "jue_wiki_decision_adjustment",
                "jue_wiki_decision_adjustments",
                "jue_wiki_decision_adjustment_resolution",
            ):
                note = metadata.get(metadata_key)
                if note in (None, "", [], {}):
                    note = row.get(metadata_key)
                if _manager_wiki_decision_adjustment_note_is_negative(note):
                    continue
                if not _manager_repair_note_is_concrete(note):
                    continue
                if not terms or _manager_payload_mentions_any_term(note, terms):
                    if required_terms and not _manager_payload_mentions_any_term(
                        note,
                        required_terms,
                    ):
                        continue
                    return True
    return False


def _manager_wiki_decision_adjustment_resolved(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    terms = _manager_wiki_decision_adjustment_terms(prompt)
    evidence_terms = _manager_wiki_decision_adjustment_evidence_terms(prompt)
    execution_hint_terms = _manager_wiki_decision_adjustment_execution_hint_terms(
        prompt
    )
    translation_terms = _manager_wiki_decision_adjustment_translation_terms(prompt)
    if _manager_actions_have_prompt_linked_decision_adjustment_metadata(
        prompt,
        actions,
    ):
        if evidence_terms:
            if not _manager_actions_have_prompt_linked_decision_adjustment_metadata(
                prompt,
                actions,
                required_terms=evidence_terms,
            ):
                return False
        if execution_hint_terms:
            if not _manager_actions_have_prompt_linked_decision_adjustment_metadata(
                prompt,
                actions,
                required_terms=execution_hint_terms,
            ):
                return False
        if translation_terms:
            if not _manager_actions_have_prompt_linked_decision_adjustment_metadata(
                prompt,
                actions,
                required_terms=translation_terms,
            ):
                return False
        return True
    if (
        not _manager_wiki_decision_adjustment_note_is_negative(hold_decision)
        and _manager_payload_mentions_any_term(hold_decision, terms)
    ):
        if evidence_terms and not _manager_payload_mentions_any_term(
            hold_decision,
            evidence_terms,
        ):
            return False
        if execution_hint_terms and not _manager_payload_mentions_any_term(
            hold_decision,
            execution_hint_terms,
        ):
            return False
        if translation_terms and not _manager_payload_mentions_any_term(
            hold_decision,
            translation_terms,
        ):
            return False
        return _manager_hold_has_concrete_next_step(hold_decision)
    validation_repair_resolution = response.get("validation_repair_resolution")
    if _manager_payload_mentions_any_term(
        validation_repair_resolution,
        terms,
    ):
        if _manager_wiki_decision_adjustment_note_is_negative(
            validation_repair_resolution,
        ):
            return False
        if evidence_terms and not _manager_payload_mentions_any_term(
            validation_repair_resolution,
            evidence_terms,
        ):
            return False
        if execution_hint_terms and not _manager_payload_mentions_any_term(
            validation_repair_resolution,
            execution_hint_terms,
        ):
            return False
        if translation_terms and not _manager_payload_mentions_any_term(
            validation_repair_resolution,
            translation_terms,
        ):
            return False
        return _manager_response_has_concrete_repair_resolution(response)
    return False


def _manager_memory_card_quality_resolution_has_specific_evidence(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> bool:
    terms = _manager_memory_card_quality_required_terms(prompt)
    target_symbols = _manager_memory_card_quality_target_symbols(prompt)
    if not terms:
        hold_resolved = (
            not _manager_memory_card_quality_note_is_negative(hold_decision)
            and _manager_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                target_symbols,
            )
        )
        return hold_resolved or _manager_memory_card_quality_action_has_specific_evidence(
            prompt=prompt,
            actions=actions,
        )
    if (
        not _manager_memory_card_quality_note_is_negative(hold_decision)
        and _manager_payload_mentions_any_term(hold_decision, terms)
    ):
        return _manager_hold_has_concrete_next_step_for_symbols(
            hold_decision,
            target_symbols,
        )
    validation_repair_resolution = response.get("validation_repair_resolution")
    if (
        not _manager_memory_card_quality_note_is_negative(
            validation_repair_resolution
        )
        and _manager_payload_mentions_any_term(
            validation_repair_resolution,
            terms,
        )
    ):
        return _manager_response_has_concrete_repair_resolution(
            response,
            target_symbols=target_symbols,
        )
    if _manager_memory_card_quality_action_has_specific_evidence(
        prompt=prompt,
        actions=actions,
    ):
        return True
    return False


def _manager_prompt_candidate_rows(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = prompt.get("candidates") if isinstance(prompt, dict) else None
    if isinstance(candidates, dict):
        rows = candidates.get("items")
    else:
        rows = candidates
    return [row for row in _as_list(rows) if isinstance(row, dict)]


def _manager_prompt_create_visible_candidate_rows(
    prompt: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = list(_manager_prompt_candidate_rows(prompt))
    if not isinstance(prompt, dict):
        return rows
    pressure = prompt.get("proactive_decision_pressure")
    pressure = pressure if isinstance(pressure, dict) else {}
    pressure_rows = pressure.get("top_candidates")
    if isinstance(pressure_rows, dict) and isinstance(
        pressure_rows.get("items"),
        list,
    ):
        pressure_rows = pressure_rows.get("items")
    seen: set[tuple[str, str]] = {
        (
            str(row.get("symbol") or "").upper().strip(),
            normalize_market(row.get("market") or row.get("venue")),
        )
        for row in rows
        if isinstance(row, dict)
    }
    for row in _as_list(pressure_rows):
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("symbol") or "").upper().strip(),
            normalize_market(row.get("market") or row.get("venue")),
        )
        if not key[0] or key in seen:
            continue
        rows.append(row)
        seen.add(key)
    return rows


def _manager_create_actions_use_visible_candidates(
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    candidate_rows = _manager_prompt_create_visible_candidate_rows(prompt)
    if not candidate_rows:
        return True
    visible_pairs: set[tuple[str, str]] = set()
    unscoped_symbols: set[str] = set()
    for row in candidate_rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        raw_market = row.get("market") or row.get("venue")
        if raw_market in (None, ""):
            unscoped_symbols.add(symbol)
        else:
            visible_pairs.add((symbol, normalize_market(raw_market)))
    if not visible_pairs and not unscoped_symbols:
        return True
    for row in _as_list(actions.get("create_blocks")):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        market = normalize_market(row.get("market") or row.get("venue"))
        if symbol in unscoped_symbols:
            continue
        if (symbol, market) in visible_pairs:
            continue
        return False
    return True


def manager_response_contract_error(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> str:
    """Return a contract error when a no-action run hides required repair work."""

    if _manager_execution_gate_blocks_contract(prompt):
        return ""
    action_count = _manager_action_item_count(actions)
    entry_repair_action_count = _manager_entry_repair_action_item_count(actions)
    if _manager_candidate_memory_hint_resolution_missing(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    ):
        return "candidate_memory_hint_resolution_missing_from_model"
    if not _manager_create_actions_use_visible_candidates(prompt, actions):
        return "manager_create_candidate_not_visible"
    binance_activity_gap_pressure = _manager_prompt_has_binance_activity_gap_pressure(
        prompt
    )
    binance_activity_gap_action_resolved = (
        binance_activity_gap_pressure
        and _manager_action_resolves_binance_activity_gap(
            prompt=prompt,
            actions=actions,
        )
    )
    binance_activity_gap_response_resolved = (
        binance_activity_gap_pressure
        and _manager_response_resolves_binance_activity_gap(
            prompt=prompt,
            response=response,
            hold_decision=hold_decision,
        )
    )
    binance_activity_gap_resolved = (
        binance_activity_gap_action_resolved
        or binance_activity_gap_response_resolved
    )
    active_validation_repair = _manager_prompt_has_active_validation_repair(prompt)
    active_repair = (
        active_validation_repair
        or _manager_prompt_has_jue_wiki_validation_repair_contract(prompt)
        or _manager_prompt_has_wiki_repair_priorities(prompt)
        or _manager_prompt_has_wiki_attention_response_contract(prompt)
        or _manager_prompt_has_requested_symbol_coverage_gap(prompt)
        or _manager_prompt_has_memory_card_quality_gap(prompt)
        or _manager_prompt_has_degraded_wiki_effectiveness(prompt)
        or _manager_prompt_has_wiki_decision_adjustments(prompt)
        or _manager_prompt_has_wiki_selection_guidance(prompt)
        or _manager_prompt_has_unavailable_wiki_context(prompt)
        or _manager_prompt_has_wiki_action_reference_memory(prompt)
    )
    validation_repair_action_missing = (
        active_validation_repair
        and _manager_executable_repair_resolution_missing_action(
            prompt=prompt,
            response=response,
            actions=actions,
        )
    )
    wiki_attention = _manager_prompt_has_wiki_attention_response_contract(prompt)
    requested_symbol_coverage_gap = _manager_prompt_has_requested_symbol_coverage_gap(
        prompt
    )
    requested_symbol_coverage_resolution = (
        _manager_requested_symbol_coverage_resolution_state(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
            action_count=action_count,
        )
    )
    requested_symbol_coverage_resolved = requested_symbol_coverage_resolution[
        "resolved"
    ]
    memory_card_quality_gap = _manager_prompt_has_memory_card_quality_gap(prompt)
    attention_action_resolved = (
        wiki_attention
        and _manager_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=("jue_wiki_repair_attention",),
        )
    )
    memory_card_quality_resolved = (
        memory_card_quality_gap
        and _manager_memory_card_quality_resolution_has_specific_evidence(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    )
    degraded_wiki_effectiveness_gap = _manager_prompt_has_degraded_wiki_effectiveness(
        prompt
    )
    wiki_decision_adjustment_gap = _manager_prompt_has_wiki_decision_adjustments(
        prompt
    )
    wiki_decision_adjustment_resolved = (
        wiki_decision_adjustment_gap
        and _manager_wiki_decision_adjustment_resolved(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    )
    wiki_selection_guidance_gap = _manager_prompt_has_wiki_selection_guidance(prompt)
    wiki_selection_guidance_action_resolved = (
        wiki_selection_guidance_gap
        and _manager_actions_resolve_wiki_selection_guidance(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_selection_guidance_hold_resolved = (
        wiki_selection_guidance_gap
        and _manager_hold_resolves_wiki_selection_guidance(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    unavailable_wiki_context_gap = _manager_prompt_has_unavailable_wiki_context(prompt)
    unavailable_wiki_context_action_resolved = (
        unavailable_wiki_context_gap
        and _manager_actions_resolve_unavailable_wiki_context(
            prompt=prompt,
            actions=actions,
        )
    )
    unavailable_wiki_context_hold_resolved = (
        unavailable_wiki_context_gap
        and _manager_hold_resolves_unavailable_wiki_context(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    wiki_action_reference_gap = _manager_prompt_has_wiki_action_reference_memory(
        prompt
    )
    wiki_action_reference_action_resolved = (
        wiki_action_reference_gap
        and _manager_actions_resolve_wiki_action_reference_memory(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_action_reference_hold_resolved = (
        wiki_action_reference_gap
        and _manager_hold_resolves_wiki_action_reference_memory(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    memory_contract_repair_required = (
        _manager_prompt_requires_memory_contract_repair(prompt)
    )
    memory_contract_repair_resolved = _manager_memory_contract_repair_resolved(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    )
    degraded_wiki_effectiveness_action_resolved = (
        degraded_wiki_effectiveness_gap
        and (
            _manager_actions_have_prompt_linked_wiki_repair_metadata(
                prompt,
                actions,
                metadata_keys=(
                    "jue_wiki_repair_pressure",
                    "jue_wiki_repair_resolution",
                ),
            )
            or (
                entry_repair_action_count <= 0
                and _manager_response_has_prompt_linked_repair_resolution(
                    prompt,
                    response,
                )
            )
        )
    )
    repair_resolved = (
        attention_action_resolved
        or _manager_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=(
                "jue_wiki_repair_attention",
                "jue_wiki_repair_pressure",
                "jue_wiki_repair_resolution",
            ),
        )
        or _manager_response_has_prompt_linked_repair_resolution(prompt, response)
        or (
            wiki_attention
            and _manager_hold_has_prompt_linked_concrete_next_step(
                prompt,
                hold_decision,
            )
        )
        or (
            requested_symbol_coverage_resolved
        )
        or memory_card_quality_resolved
        or wiki_decision_adjustment_resolved
        or wiki_selection_guidance_action_resolved
        or wiki_selection_guidance_hold_resolved
        or unavailable_wiki_context_action_resolved
        or unavailable_wiki_context_hold_resolved
        or wiki_action_reference_action_resolved
        or wiki_action_reference_hold_resolved
    )
    if memory_card_quality_gap and not memory_card_quality_resolved:
        repair_resolved = False
    if requested_symbol_coverage_gap and not requested_symbol_coverage_resolved:
        repair_resolved = False
    if wiki_decision_adjustment_gap and not wiki_decision_adjustment_resolved:
        repair_resolved = False
    if memory_contract_repair_required and not memory_contract_repair_resolved:
        repair_resolved = False
    if wiki_selection_guidance_gap and not (
        wiki_selection_guidance_action_resolved
        or (action_count <= 0 and wiki_selection_guidance_hold_resolved)
    ):
        repair_resolved = False
    if unavailable_wiki_context_gap and not (
        unavailable_wiki_context_action_resolved
        or (action_count <= 0 and unavailable_wiki_context_hold_resolved)
    ):
        repair_resolved = False
    if wiki_action_reference_gap and not (
        wiki_action_reference_action_resolved
        or (action_count <= 0 and wiki_action_reference_hold_resolved)
    ):
        repair_resolved = False
    if action_count > 0:
        if (
            wiki_action_reference_gap
            and not wiki_action_reference_action_resolved
        ):
            return "wiki_action_reference_resolution_missing_from_model"
        if (
            unavailable_wiki_context_gap
            and not unavailable_wiki_context_action_resolved
        ):
            return "wiki_context_gap_resolution_missing_from_model"
        if (
            wiki_selection_guidance_gap
            and not wiki_selection_guidance_action_resolved
        ):
            return "validation_repair_resolution_missing_from_model"
        if memory_contract_repair_required and not memory_contract_repair_resolved:
            return "memory_contract_resolution_missing_from_model"
        if requested_symbol_coverage_gap and not requested_symbol_coverage_resolved:
            return "validation_repair_resolution_missing_from_model"
        if (
            degraded_wiki_effectiveness_gap
            and not degraded_wiki_effectiveness_action_resolved
        ):
            return "validation_repair_resolution_missing_from_model"
        if validation_repair_action_missing:
            return "validation_repair_action_missing_from_model"
        if _manager_repair_probe_action_below_min_executable_qty(prompt, actions):
            return "validation_repair_min_executable_qty_missing_from_model"
        if _manager_repairable_probe_design_rejection_ignores_design(
            prompt,
            response,
            actions,
        ):
            return "validation_repair_probe_design_ignored_from_model"
        if active_repair and not repair_resolved:
            return "validation_repair_resolution_missing_from_model"
        if binance_activity_gap_pressure and not binance_activity_gap_resolved:
            return "binance_activity_gap_resolution_missing_from_model"
        return ""
    if binance_activity_gap_pressure and not binance_activity_gap_response_resolved:
        return "binance_activity_gap_resolution_missing_from_model"
    if memory_contract_repair_required and not memory_contract_repair_resolved:
        return "memory_contract_resolution_missing_from_model"
    if unavailable_wiki_context_gap and not unavailable_wiki_context_hold_resolved:
        return "wiki_context_gap_resolution_missing_from_model"
    if wiki_action_reference_gap and not wiki_action_reference_hold_resolved:
        return "wiki_action_reference_resolution_missing_from_model"
    if memory_card_quality_gap and not memory_card_quality_resolved:
        return "validation_repair_resolution_missing_from_model"
    if requested_symbol_coverage_gap and not requested_symbol_coverage_resolved:
        return "validation_repair_resolution_missing_from_model"
    if wiki_decision_adjustment_gap and not wiki_decision_adjustment_resolved:
        return "validation_repair_resolution_missing_from_model"
    if wiki_selection_guidance_gap and not wiki_selection_guidance_hold_resolved:
        return "validation_repair_resolution_missing_from_model"
    if validation_repair_action_missing:
        return "validation_repair_action_missing_from_model"
    if _manager_repairable_probe_design_rejection_ignores_design(
        prompt,
        response,
        actions,
    ):
        return "validation_repair_probe_design_ignored_from_model"
    if active_repair and not repair_resolved:
        return "validation_repair_resolution_missing_from_model"
    if (
        _manager_prompt_has_action_pressure(prompt)
        or _manager_prompt_has_wiki_action_pressure(prompt)
    ) and not _manager_hold_has_concrete_next_step(hold_decision):
        return "hold_decision_missing_concrete_trigger"
    return ""


def _compact_wiki_attention_contract_item(source: Any) -> dict[str, Any]:
    row = source if isinstance(source, dict) else {}
    if not row:
        return {}
    compact = {
        "component": _clean_text(row.get("component"), limit=120),
        "action_type": _clean_text(row.get("action_type"), limit=120),
        "recommended_resolution": _clean_text(
            row.get("recommended_resolution"),
            limit=180,
        ),
        "quality_warnings": [
            item
            for item in (
                _clean_text(value, limit=120)
                for value in _as_list(row.get("quality_warnings"))
            )
            if item
        ][:6],
        "impacted_symbols": [
            item
            for item in (
                _clean_text(value, limit=40)
                for value in _as_list(row.get("impacted_symbols"))
            )
            if item
        ][:8],
        "missing_fields": [
            item
            for item in (
                _clean_text(value, limit=80)
                for value in _as_list(row.get("missing_fields"))
            )
            if item
        ][:8],
        "required_checks": [
            item
            for item in (
                _clean_text(value, limit=160)
                for value in _as_list(row.get("required_checks"))
            )
            if item
        ][:8],
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in (None, "", [], {})
    }


def manager_run_diagnostics(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
    actions: dict[str, Any],
    hold_decision: dict[str, Any],
) -> dict[str, Any]:
    _ = response
    action_count = sum(
        len(actions.get(key) or [])
        for key in BINANCE_MANAGER_ACTION_SECTIONS
        if isinstance(actions.get(key), list)
    )
    entry_repair_action_count = _manager_entry_repair_action_item_count(actions)
    prompt_budget = prompt.get("prompt_budget")
    prompt_budget = prompt_budget if isinstance(prompt_budget, dict) else {}
    growth_governor = _manager_scoped_operational_prompt_section(
        prompt,
        "growth_governor",
    )
    growth_unlock = _manager_scoped_operational_prompt_section(
        prompt,
        "growth_unlock",
    )
    proactive_pressure = _manager_scoped_proactive_decision_pressure(prompt)
    repair_contract = _manager_scoped_jue_wiki_repair_contract(prompt)
    repair_action_batches = _as_list(repair_contract.get("action_batches"))
    repair_action_plan = repair_contract.get("repair_pressure_action_plan")
    repair_action_plan = repair_action_plan if isinstance(repair_action_plan, dict) else {}
    repair_action_batch_total_count = _safe_int(
        repair_contract.get("action_batch_total_count")
    ) or _safe_int(repair_action_plan.get("action_batch_total_count"))
    repair_action_batch_omitted_count = _safe_int(
        repair_contract.get("action_batch_omitted_count")
    ) or _safe_int(repair_action_plan.get("action_batch_omitted_count"))
    repair_action_batch_visible_pressure_count = _safe_int(
        repair_contract.get("action_batch_visible_pressure_count")
    ) or _safe_int(repair_action_plan.get("action_batch_visible_pressure_count"))
    if repair_action_batch_visible_pressure_count <= 0 and repair_action_batches:
        repair_action_batch_visible_pressure_count = sum(
            max(_safe_int(row.get("count")), 0)
            for row in repair_action_batches
            if isinstance(row, dict)
        )
        if repair_action_batch_visible_pressure_count <= 0:
            repair_action_batch_visible_pressure_count = len(repair_action_batches)
    repair_action_batch_visibility_ratio = _safe_float(
        repair_contract.get("action_batch_pressure_visibility_ratio")
    ) or _safe_float(repair_action_plan.get("action_batch_pressure_visibility_ratio"))
    requested_symbol_coverage = _manager_scoped_requested_symbol_coverage(prompt)
    memory_card_quality = _manager_scoped_memory_card_quality(prompt)
    memory_card_quality_summary = (
        memory_card_quality.get("summary")
        if isinstance(memory_card_quality.get("summary"), dict)
        else {}
    )
    memory_card_quality_action_plan = (
        memory_card_quality.get("action_plan")
        if isinstance(memory_card_quality.get("action_plan"), dict)
        else {}
    )
    memory_card_quality_gap_summary = (
        _manager_repair_contract_memory_card_quality_gap_summary(prompt)
    )
    attention_contract = repair_contract.get("attention_plan_response_contract")
    attention_contract = attention_contract if isinstance(attention_contract, dict) else {}
    attention_additional = [
        item
        for item in (
            _compact_wiki_attention_contract_item(row)
            for row in _as_list(attention_contract.get("additional_attention"))[:4]
        )
        if item
    ]
    attention_active = (
        str(attention_contract.get("status") or "").strip().lower() == "active"
        and bool(_as_list(attention_contract.get("must_address")))
    )
    degraded_effectiveness_items = _manager_degraded_wiki_effectiveness_items(prompt)
    degraded_effectiveness_page_ids: list[str] = []
    for item in degraded_effectiveness_items:
        page_id = str(item.get("page_id") or "").strip()
        if page_id and page_id not in degraded_effectiveness_page_ids:
            degraded_effectiveness_page_ids.append(page_id)
    candidate_memory_hint_summary = _manager_candidate_memory_hint_resolution_summary(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    )
    memory_contract_summary = _manager_memory_contract_repair_resolution_summary(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision=hold_decision,
    )
    validation_repair = _manager_scoped_validation_repair(prompt)
    live_authority = _manager_scoped_operational_prompt_section(
        prompt,
        "live_authority",
    )
    performance = _manager_scoped_operational_prompt_section(
        prompt,
        "performance",
    )
    candidate_generation = _manager_scoped_operational_prompt_section(
        prompt,
        "candidate_generation",
    )
    hold_rows = [
        hold_decision.get("summary"),
        *(
            hold_decision.get("reasons")
            if isinstance(hold_decision.get("reasons"), list)
            else []
        ),
        *(
            hold_decision.get("data_gaps")
            if isinstance(hold_decision.get("data_gaps"), list)
            else []
        ),
        *(
            hold_decision.get("risk_notes")
            if isinstance(hold_decision.get("risk_notes"), list)
            else []
        ),
    ]
    haystack = " ".join(str(item or "").lower() for item in hold_rows)
    blocker_tags: dict[str, int] = {}

    def add(tag: str, weight: int = 1) -> None:
        blocker_tags[tag] = blocker_tags.get(tag, 0) + max(int(weight), 1)

    if prompt_budget.get("over_max"):
        add("prompt_budget_over_max", 3)
    elif prompt_budget.get("over_warn"):
        add("prompt_budget_over_warn", 1)
    if str(growth_governor.get("mode") or "") in {
        "edge_rebuild",
        "halt_new_entries",
    }:
        add(str(growth_governor.get("mode")), 2)
    if (
        action_count <= 0
        and str(proactive_pressure.get("status") or "") == "action_required"
    ):
        add("unresolved_proactive_pressure", 4)
    if action_count <= 0 and repair_contract.get("top_priorities"):
        add("unresolved_jue_wiki_repair_priorities", 3)
    if (
        action_count <= 0
        and (
            repair_action_batches
            or repair_action_batch_total_count > 0
            or repair_action_batch_omitted_count > 0
        )
    ):
        add("unresolved_jue_wiki_repair_action_batches", 3)
    requested_symbol_coverage_gap = _manager_prompt_has_requested_symbol_coverage_gap(
        prompt
    )
    requested_symbol_coverage_resolution = (
        _manager_requested_symbol_coverage_resolution_state(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
            action_count=action_count,
        )
    )
    requested_symbol_coverage_resolved = requested_symbol_coverage_resolution[
        "resolved"
    ]
    if requested_symbol_coverage_gap and not requested_symbol_coverage_resolved:
        add("unresolved_jue_wiki_requested_symbol_coverage", 2)
    memory_card_quality_gap = _manager_prompt_has_memory_card_quality_gap(prompt)
    memory_card_quality_terms = _manager_memory_card_quality_required_terms(prompt)
    memory_card_quality_resolved = (
        memory_card_quality_gap
        and _manager_memory_card_quality_resolution_has_specific_evidence(
            prompt=prompt,
            response=response,
            actions=actions,
            hold_decision=hold_decision,
        )
    )
    if memory_card_quality_terms:
        memory_card_quality_resolved_by_action = (
            memory_card_quality_resolved
            and _manager_memory_card_quality_action_has_specific_evidence(
                prompt=prompt,
                actions=actions,
            )
        )
        memory_card_quality_resolved_by_hold = (
            memory_card_quality_resolved
            and not memory_card_quality_resolved_by_action
            and _manager_payload_mentions_any_term(
                hold_decision,
                memory_card_quality_terms,
            )
            and _manager_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                _manager_memory_card_quality_target_symbols(prompt),
            )
        )
        memory_card_quality_resolved_by_response = (
            memory_card_quality_resolved
            and not memory_card_quality_resolved_by_action
            and not memory_card_quality_resolved_by_hold
            and bool(response.get("validation_repair_resolution"))
        )
    else:
        memory_card_quality_resolved_by_action = (
            memory_card_quality_gap
            and _manager_memory_card_quality_action_has_specific_evidence(
                prompt=prompt,
                actions=actions,
            )
        )
        memory_card_quality_resolved_by_hold = (
            memory_card_quality_gap
            and not memory_card_quality_resolved_by_action
            and _manager_hold_has_concrete_next_step_for_symbols(
                hold_decision,
                _manager_memory_card_quality_target_symbols(prompt),
            )
        )
        memory_card_quality_resolved_by_response = (
            memory_card_quality_resolved
            and not memory_card_quality_resolved_by_action
            and not memory_card_quality_resolved_by_hold
            and bool(response.get("validation_repair_resolution"))
        )
    if (
        action_count <= 0
        and memory_card_quality_gap
        and not memory_card_quality_resolved
    ):
        add("unresolved_jue_wiki_memory_card_quality", 2)
    candidate_memory_hint_unresolved_count = _safe_int(
        candidate_memory_hint_summary.get("candidate_memory_hint_unresolved_count")
    )
    if candidate_memory_hint_unresolved_count > 0:
        add("unresolved_candidate_memory_hint", candidate_memory_hint_unresolved_count)
    memory_contract_unresolved_count = _safe_int(
        memory_contract_summary.get("memory_contract_unresolved_count")
    )
    if memory_contract_unresolved_count > 0:
        add("unresolved_memory_contract", memory_contract_unresolved_count)
    attention_resolved_by_action = _manager_actions_have_prompt_linked_wiki_repair_metadata(
        prompt,
        actions,
        metadata_keys=("jue_wiki_repair_attention",),
    )
    attention_resolved_by_hold = _manager_hold_has_prompt_linked_concrete_next_step(
        prompt,
        hold_decision,
    )
    if attention_resolved_by_action:
        attention_resolution_status = "action_metadata"
    elif attention_resolved_by_hold:
        attention_resolution_status = "hold_trigger"
    else:
        attention_resolution_status = "unresolved"
    if attention_active and not (
        attention_resolved_by_action or attention_resolved_by_hold
    ):
        add("unresolved_jue_wiki_attention_plan", 3)
    degraded_effectiveness_resolved_by_action = (
        _manager_actions_have_prompt_linked_wiki_repair_metadata(
            prompt,
            actions,
            metadata_keys=(
                "jue_wiki_repair_pressure",
                "jue_wiki_repair_resolution",
            ),
        )
    )
    degraded_effectiveness_resolved_by_hold = (
        _manager_hold_has_prompt_linked_concrete_next_step(prompt, hold_decision)
    )
    degraded_effectiveness_resolved_by_response = (
        _manager_response_has_prompt_linked_repair_resolution(prompt, response)
    )
    if degraded_effectiveness_resolved_by_action:
        degraded_effectiveness_resolution_status = "action_metadata"
    elif entry_repair_action_count <= 0 and degraded_effectiveness_resolved_by_hold:
        degraded_effectiveness_resolution_status = "hold_trigger"
    elif entry_repair_action_count <= 0 and degraded_effectiveness_resolved_by_response:
        degraded_effectiveness_resolution_status = "response_resolution"
    else:
        degraded_effectiveness_resolution_status = "unresolved"
    if degraded_effectiveness_items and not (
        degraded_effectiveness_resolved_by_action
        or (
            entry_repair_action_count <= 0
            and degraded_effectiveness_resolved_by_hold
        )
        or (
            entry_repair_action_count <= 0
            and degraded_effectiveness_resolved_by_response
        )
    ):
        add("unresolved_degraded_jue_wiki_effectiveness", 3)
    wiki_selection_guidance_gap = _manager_prompt_has_wiki_selection_guidance(prompt)
    wiki_selection_guidance_resolved_by_action = (
        wiki_selection_guidance_gap
        and _manager_actions_resolve_wiki_selection_guidance(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_selection_guidance_resolved_by_hold = (
        wiki_selection_guidance_gap
        and not wiki_selection_guidance_resolved_by_action
        and action_count <= 0
        and _manager_hold_resolves_wiki_selection_guidance(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    if wiki_selection_guidance_gap and not (
        wiki_selection_guidance_resolved_by_action
        or wiki_selection_guidance_resolved_by_hold
    ):
        add("unresolved_jue_wiki_selection_guidance", 2)
    unavailable_wiki_context_gap = _manager_prompt_has_unavailable_wiki_context(prompt)
    unavailable_wiki_context_resolved_by_action = (
        unavailable_wiki_context_gap
        and _manager_actions_resolve_unavailable_wiki_context(
            prompt=prompt,
            actions=actions,
        )
    )
    unavailable_wiki_context_resolved_by_hold = (
        unavailable_wiki_context_gap
        and not unavailable_wiki_context_resolved_by_action
        and action_count <= 0
        and _manager_hold_resolves_unavailable_wiki_context(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    if unavailable_wiki_context_gap and not (
        unavailable_wiki_context_resolved_by_action
        or unavailable_wiki_context_resolved_by_hold
    ):
        add("unresolved_jue_wiki_context_gap", 2)
    wiki_action_reference_memory_gap = (
        _manager_prompt_has_wiki_action_reference_memory(prompt)
    )
    wiki_action_reference_memory_resolved_by_action = (
        wiki_action_reference_memory_gap
        and _manager_actions_resolve_wiki_action_reference_memory(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_action_reference_memory_resolved_by_hold = (
        wiki_action_reference_memory_gap
        and not wiki_action_reference_memory_resolved_by_action
        and action_count <= 0
        and _manager_hold_resolves_wiki_action_reference_memory(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    if wiki_action_reference_memory_gap and not (
        wiki_action_reference_memory_resolved_by_action
        or wiki_action_reference_memory_resolved_by_hold
    ):
        add("unresolved_jue_wiki_action_reference_memory", 2)
    wiki_action_reference_recovery_action_resolved = (
        wiki_action_reference_memory_gap
        and _manager_actions_resolve_wiki_action_reference_recovery(
            prompt=prompt,
            actions=actions,
        )
    )
    wiki_action_reference_recovery_hold_resolved = (
        wiki_action_reference_memory_gap
        and not wiki_action_reference_recovery_action_resolved
        and action_count <= 0
        and _manager_hold_resolves_wiki_action_reference_recovery(
            prompt=prompt,
            hold_decision=hold_decision,
        )
    )
    applicable_wiki_context = _manager_prompt_has_applicable_wiki_context(prompt)
    wiki_action_reference_allowed_page_ids = _manager_prompt_wiki_reference_page_ids(
        prompt
    )
    wiki_action_reference_symbol_page_ids = (
        _manager_prompt_wiki_reference_symbol_page_ids(prompt)
    )
    wiki_action_reference_block_symbol_map = _manager_prompt_block_symbol_map(prompt)
    wiki_action_reference_decision_count = action_count
    if action_count > 0:
        wiki_action_reference_count = _manager_action_wiki_reference_count(
            actions,
            allowed_page_ids=wiki_action_reference_allowed_page_ids,
            required_symbol_page_ids=wiki_action_reference_symbol_page_ids,
            block_symbol_map=wiki_action_reference_block_symbol_map,
        )
        wiki_action_reference_missing_actions = (
            _manager_action_wiki_reference_missing_actions(
                actions,
                allowed_page_ids=wiki_action_reference_allowed_page_ids,
                required_symbol_page_ids=wiki_action_reference_symbol_page_ids,
                block_symbol_map=wiki_action_reference_block_symbol_map,
            )
        )
    elif _manager_hold_decision_has_payload(hold_decision):
        hold_symbol_page_ids = _manager_hold_required_symbol_page_ids(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
        )
        hold_uncovered_target_symbols = _manager_hold_uncovered_target_symbols(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
            allowed_page_ids=wiki_action_reference_allowed_page_ids,
        )
        hold_required_symbol_entries = _manager_required_symbol_page_entries(
            hold_symbol_page_ids
        )
        wiki_action_reference_decision_count = max(
            1,
            len(hold_required_symbol_entries) + len(hold_uncovered_target_symbols),
        )
        wiki_action_reference_count = (
            _manager_hold_wiki_reference_count(
                hold_decision,
                allowed_page_ids=wiki_action_reference_allowed_page_ids,
                required_symbol_page_ids=hold_symbol_page_ids,
            )
            if hold_required_symbol_entries or not hold_uncovered_target_symbols
            else 0
        )
        if hold_required_symbol_entries:
            if wiki_action_reference_count > 0:
                wiki_action_reference_missing_actions = (
                    _manager_hold_wiki_reference_missing_symbol_pages(
                        hold_decision,
                        allowed_page_ids=wiki_action_reference_allowed_page_ids,
                        required_symbol_page_ids=hold_symbol_page_ids,
                    )
                )
            else:
                wiki_action_reference_missing_actions = [{"section": "hold_decision"}]
            wiki_action_reference_missing_actions.extend(
                {
                    "section": "hold_decision",
                    "symbol": symbol,
                }
                for symbol in sorted(hold_uncovered_target_symbols)
            )
        elif hold_uncovered_target_symbols:
            wiki_action_reference_missing_actions = [
                {
                    "section": "hold_decision",
                    "symbol": symbol,
                }
                for symbol in sorted(hold_uncovered_target_symbols)
            ]
        else:
            wiki_action_reference_missing_actions = (
                []
                if wiki_action_reference_count > 0
                else [{"section": "hold_decision"}]
            )
    else:
        wiki_action_reference_count = 0
        wiki_action_reference_missing_actions = []
    wiki_action_reference_ratio = (
        round(wiki_action_reference_count / wiki_action_reference_decision_count, 3)
        if wiki_action_reference_decision_count > 0
        else 0.0
    )
    wiki_action_reference_recovery = (
        _manager_wiki_action_reference_recovery_diagnostics(prompt)
    )
    wiki_action_reference_recovery_resolution = (
        "action_metadata"
        if wiki_action_reference_recovery_action_resolved
        else "hold_trigger"
        if wiki_action_reference_recovery_hold_resolved
        else ""
    )
    if (
        wiki_action_reference_recovery_resolution
        and _manager_prompt_has_wiki_action_reference_recovery_guidance(prompt)
    ):
        wiki_action_reference_recovery = (
            _manager_resolved_wiki_action_reference_recovery_diagnostics(
                wiki_action_reference_recovery,
                resolution_status=wiki_action_reference_recovery_resolution,
            )
        )
    wiki_action_reference_open_gap_count = _safe_int(
        wiki_action_reference_recovery.get(
            "jue_wiki_action_reference_recovery_open_gap_count"
        )
    )
    wiki_action_reference_recovery_status = str(
        wiki_action_reference_recovery.get(
            "jue_wiki_action_reference_recovery_status"
        )
        or ""
    ).strip().lower()
    wiki_action_reference_recovery_resolution_status = str(
        wiki_action_reference_recovery.get(
            "jue_wiki_action_reference_recovery_latest_resolution_status"
        )
        or ""
    ).strip().lower()
    if (
        wiki_action_reference_open_gap_count > 0
        or wiki_action_reference_recovery_status in {"open_gaps", "unresolved"}
        or wiki_action_reference_recovery_resolution_status == "unresolved"
    ):
        add(
            "unresolved_jue_wiki_action_reference_recovery",
            max(wiki_action_reference_open_gap_count, 1),
        )
    partial_wiki_action_reference = (
        applicable_wiki_context
        and wiki_action_reference_decision_count > 0
        and 0 < wiki_action_reference_count < wiki_action_reference_decision_count
    )
    complete_wiki_action_reference = (
        wiki_action_reference_decision_count > 0
        and wiki_action_reference_count >= wiki_action_reference_decision_count
    )
    unscoped_wiki_action_reference = (
        not applicable_wiki_context and wiki_action_reference_count > 0
    )
    wiki_action_reference_referenced_page_ids = sorted(
        _manager_wiki_reference_page_ids_from_payload(
            {"actions": actions, "hold_decision": hold_decision}
        )
    )
    wiki_action_reference_unscoped_page_ids = (
        wiki_action_reference_referenced_page_ids[:12]
        if unscoped_wiki_action_reference
        else []
    )
    wiki_action_reference_unscoped_page_omitted_count = (
        max(
            len(wiki_action_reference_referenced_page_ids)
            - len(wiki_action_reference_unscoped_page_ids),
            0,
        )
        if unscoped_wiki_action_reference
        else 0
    )
    if (
        applicable_wiki_context
        and wiki_action_reference_decision_count > 0
        and wiki_action_reference_count <= 0
    ):
        add("missing_jue_wiki_action_reference", 1)
    elif partial_wiki_action_reference:
        add(
            "partial_jue_wiki_action_reference",
            wiki_action_reference_decision_count - wiki_action_reference_count,
        )
    elif unscoped_wiki_action_reference:
        add("unscoped_jue_wiki_action_reference", wiki_action_reference_count)
    wiki_usage_contract_gap = _manager_prompt_has_wiki_usage_contract(prompt)
    wiki_usage_contract_required_terms = _manager_wiki_usage_contract_required_terms(
        prompt
    )
    wiki_usage_contract_decision_count = action_count
    if action_count > 0:
        wiki_usage_contract_resolution_count = (
            _manager_action_wiki_usage_contract_resolution_count(
                actions,
                required_terms=wiki_usage_contract_required_terms,
                allowed_page_ids=wiki_action_reference_allowed_page_ids,
                required_symbol_page_ids=wiki_action_reference_symbol_page_ids,
                block_symbol_map=wiki_action_reference_block_symbol_map,
            )
        )
    elif _manager_hold_decision_has_payload(hold_decision):
        hold_usage_symbol_page_ids = _manager_hold_required_symbol_page_ids(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
        )
        hold_usage_uncovered_target_symbols = _manager_hold_uncovered_target_symbols(
            hold_decision,
            wiki_action_reference_symbol_page_ids,
            allowed_page_ids=wiki_action_reference_allowed_page_ids,
        )
        wiki_usage_contract_required_symbol_entries = (
            _manager_required_symbol_page_entries(
                hold_usage_symbol_page_ids
            )
        )
        wiki_usage_contract_decision_count = max(
            1,
            len(wiki_usage_contract_required_symbol_entries)
            + len(hold_usage_uncovered_target_symbols),
        )
        wiki_usage_contract_resolution_count = (
            (
                _manager_hold_wiki_usage_contract_resolution_count(
                    hold_decision,
                    required_terms=wiki_usage_contract_required_terms,
                    allowed_page_ids=wiki_action_reference_allowed_page_ids,
                    required_symbol_page_ids=hold_usage_symbol_page_ids,
                )
            )
            if wiki_usage_contract_required_symbol_entries
            or not hold_usage_uncovered_target_symbols
            else 0
        )
    else:
        wiki_usage_contract_resolution_count = 0
    wiki_usage_contract_resolution_ratio = (
        round(
            wiki_usage_contract_resolution_count
            / wiki_usage_contract_decision_count,
            3,
        )
        if wiki_usage_contract_decision_count > 0
        else 0.0
    )
    partial_wiki_usage_contract_resolution = (
        wiki_usage_contract_gap
        and wiki_usage_contract_decision_count > 0
        and 0
        < wiki_usage_contract_resolution_count
        < wiki_usage_contract_decision_count
    )
    complete_wiki_usage_contract_resolution = (
        wiki_usage_contract_decision_count > 0
        and wiki_usage_contract_resolution_count
        >= wiki_usage_contract_decision_count
    )
    if (
        wiki_usage_contract_gap
        and wiki_usage_contract_decision_count > 0
        and wiki_usage_contract_resolution_count <= 0
    ):
        add(
            "missing_jue_wiki_usage_contract_resolution",
            wiki_usage_contract_decision_count,
        )
    elif partial_wiki_usage_contract_resolution:
        add(
            "partial_jue_wiki_usage_contract_resolution",
            wiki_usage_contract_decision_count - wiki_usage_contract_resolution_count,
        )
    if (
        action_count <= 0
        and not bool(validation_repair.get("hard_filter"))
        and (
            _safe_int(validation_repair.get("repair_item_count")) > 0
            or _safe_int(validation_repair.get("constraint_count")) > 0
        )
    ):
        add("unresolved_validation_repair_probe", 3)
    if bool(growth_governor.get("require_waiting_entry")):
        add("waiting_entry_required", 1)
    live_grade = str(
        live_authority.get("live_grade")
        or live_authority.get("status")
        or ""
    ).lower()
    if "observe" in live_grade:
        add("observe_only", 2)
    if live_authority.get("allow_scale_up") is False:
        add("scale_up_disabled", 1)
    if any(token in haystack for token in ("observe_only", "observe only")):
        add("observe_only", 1)
    if _safe_float(performance.get("avg_r_multiple")) < 0:
        add("weak_recent_edge", 2)
    if _safe_float(performance.get("realized_pnl_usdt")) < 0:
        add("recent_pnl_negative", 1)
    if any(token in haystack for token in ("pattern_prior", "pattern prior", "패턴", "prior")):
        add("pattern_prior_missing", 2)
    if any(token in haystack for token in ("orderbook_depth", "book", "호가", "depth")):
        add("book_depth_gap", 1)
    if any(token in haystack for token in ("과밀", "concentration", "편중")):
        add("lane_concentration", 2)
    if any(token in haystack for token in ("중복", "duplicate")):
        add("duplicate_block", 2)
    if any(token in haystack for token in ("성과가 약", "성과 약", "weak edge", "edge weak")):
        add("weak_recent_edge", 1)
    if any(token in haystack for token in ("futures:long", "롱", "long 후보")):
        add("long_lane_gap", 1)

    stage_counts = (
        candidate_generation.get("stage_counts")
        if isinstance(candidate_generation.get("stage_counts"), dict)
        else {}
    )
    top_blockers = [
        {"tag": tag, "count": count}
        for tag, count in sorted(
            blocker_tags.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ][:8]
    return {
        "version": "binance_manager_diagnostics_v1",
        "action_count": action_count,
        "blocker_tags": blocker_tags,
        "top_blockers": top_blockers,
        "candidate_count": len(prompt.get("candidates") or [])
        if isinstance(prompt.get("candidates"), list)
        else 0,
        "stage_counts": stage_counts,
        "growth_governor_mode": growth_governor.get("mode"),
        "growth_governor_scope": growth_governor.get("scope"),
        "growth_unlock_phase": growth_unlock.get("phase"),
        "proactive_pressure_status": proactive_pressure.get("status"),
        "proactive_pressure_level": proactive_pressure.get("pressure_level"),
        "jue_wiki_repair_priority_count": int(
            repair_contract.get("repair_priority_count") or 0
        ),
        "jue_wiki_repair_action_batch_count": len(repair_action_batches),
        "jue_wiki_repair_action_batch_total_count": repair_action_batch_total_count,
        "jue_wiki_repair_action_batch_omitted_count": (
            repair_action_batch_omitted_count
        ),
        "jue_wiki_repair_action_batch_visible_pressure_count": (
            repair_action_batch_visible_pressure_count
        ),
        "jue_wiki_repair_action_batch_pressure_visibility_ratio": (
            repair_action_batch_visibility_ratio
        ),
        "jue_wiki_requested_symbol_coverage_status": requested_symbol_coverage.get(
            "status"
        ),
        "jue_wiki_requested_symbol_coverage_resolution_status": (
            "action_metadata"
            if requested_symbol_coverage_resolution["resolved_by_action"]
            else "hold_trigger"
            if requested_symbol_coverage_resolution["resolved_by_hold"]
            else "response_resolution"
            if requested_symbol_coverage_resolution["resolved_by_response"]
            else "unresolved"
            if requested_symbol_coverage_resolution["gap"]
            else "inactive"
        ),
        "jue_wiki_missing_summary_symbols": _as_list(
            requested_symbol_coverage.get("missing_summary_symbols")
        )[:12],
        "jue_wiki_prompt_omitted_symbols": _as_list(
            requested_symbol_coverage.get("prompt_omitted_symbols")
        )[:12],
        "jue_wiki_memory_card_quality_status": memory_card_quality_action_plan.get(
            "status"
        )
        or ("active" if memory_card_quality_gap else "inactive"),
        "jue_wiki_weak_memory_card_symbols": _as_list(
            memory_card_quality_summary.get("weak_symbols")
            or memory_card_quality_action_plan.get("symbols")
        )[:12],
        "jue_wiki_memory_card_quality_gap_top_missing_fields": (
            _manager_memory_card_quality_gap_summary_display_terms(
                memory_card_quality_gap_summary,
                top_key="top_missing_fields",
                item_key="field",
                count_key="missing_field_counts",
                missed_count_key="missing_field_missed_counts",
            )
        ),
        "jue_wiki_memory_card_quality_gap_top_required_checks": (
            _manager_memory_card_quality_gap_summary_display_terms(
                memory_card_quality_gap_summary,
                top_key="top_required_checks",
                item_key="check",
                count_key="required_check_counts",
                missed_count_key="required_check_missed_counts",
            )
        ),
        "jue_wiki_memory_card_quality_resolution_status": (
            "action_metadata"
            if memory_card_quality_resolved_by_action
            else "hold_trigger"
            if memory_card_quality_resolved_by_hold
            else "response_resolution"
            if memory_card_quality_resolved_by_response
            else "unresolved"
            if memory_card_quality_gap
            else "inactive"
        ),
        "jue_wiki_attention_status": attention_contract.get("status"),
        "jue_wiki_attention_must_address": _as_list(
            attention_contract.get("must_address")
        )[:4],
        **(
            {"jue_wiki_attention_additional_attention": attention_additional}
            if attention_additional
            else {}
        ),
        "jue_wiki_attention_resolution_status": (
            attention_resolution_status if attention_active else "inactive"
        ),
        "degraded_jue_wiki_effectiveness_count": len(
            degraded_effectiveness_items
        ),
        "degraded_jue_wiki_effectiveness_page_ids": (
            degraded_effectiveness_page_ids
        ),
        "degraded_jue_wiki_effectiveness_resolution_status": (
            degraded_effectiveness_resolution_status
            if degraded_effectiveness_items
            else "inactive"
        ),
        "jue_wiki_selection_guidance_status": (
            "active" if wiki_selection_guidance_gap else "inactive"
        ),
        "jue_wiki_selection_guidance_resolution_status": (
            "action_metadata"
            if wiki_selection_guidance_resolved_by_action
            else "hold_trigger"
            if wiki_selection_guidance_resolved_by_hold
            else "unresolved"
            if wiki_selection_guidance_gap
            else "inactive"
        ),
        "jue_wiki_context_gap_status": (
            "active" if unavailable_wiki_context_gap else "inactive"
        ),
        "jue_wiki_context_gap_resolution_status": (
            "action_metadata"
            if unavailable_wiki_context_resolved_by_action
            else "hold_trigger"
            if unavailable_wiki_context_resolved_by_hold
            else "unresolved"
            if unavailable_wiki_context_gap
            else "inactive"
        ),
        "jue_wiki_action_reference_memory_status": (
            "active" if wiki_action_reference_memory_gap else "inactive"
        ),
        "jue_wiki_action_reference_memory_resolution_status": (
            "action_metadata"
            if wiki_action_reference_memory_resolved_by_action
            else "hold_trigger"
            if wiki_action_reference_memory_resolved_by_hold
            else "unresolved"
            if wiki_action_reference_memory_gap
            else "inactive"
        ),
        "jue_wiki_action_reference_status": (
            "unscoped"
            if unscoped_wiki_action_reference
            else "referenced"
            if complete_wiki_action_reference
            else "partial"
            if partial_wiki_action_reference
            else "missing"
            if applicable_wiki_context and wiki_action_reference_decision_count > 0
            else "no_actions"
            if applicable_wiki_context
            else "inactive"
        ),
        "jue_wiki_action_reference_count": wiki_action_reference_count,
        "jue_wiki_action_reference_ratio": wiki_action_reference_ratio,
        "jue_wiki_action_reference_unscoped_page_ids": (
            wiki_action_reference_unscoped_page_ids
        ),
        "jue_wiki_action_reference_unscoped_page_omitted_count": (
            wiki_action_reference_unscoped_page_omitted_count
        ),
        "jue_wiki_action_reference_required_trace_markers": [
            "binance.symbol.",
            "binance.ops.",
            "jue_wiki_action_reference_gap.",
        ],
        "jue_wiki_action_reference_allowed_page_ids": sorted(
            wiki_action_reference_allowed_page_ids
        )[:12],
        "jue_wiki_action_reference_missing_actions": (
            wiki_action_reference_missing_actions
        ),
        "jue_wiki_usage_contract_status": (
            "resolved"
            if complete_wiki_usage_contract_resolution
            else "partial"
            if partial_wiki_usage_contract_resolution
            else "missing"
            if wiki_usage_contract_gap and wiki_usage_contract_decision_count > 0
            else "no_actions"
            if wiki_usage_contract_gap
            else "inactive"
        ),
        "jue_wiki_usage_contract_resolution_count": (
            wiki_usage_contract_resolution_count
        ),
        "jue_wiki_usage_contract_resolution_ratio": (
            wiki_usage_contract_resolution_ratio
        ),
        "jue_wiki_usage_contract_required_terms": (
            wiki_usage_contract_required_terms
        ),
        **wiki_action_reference_recovery,
        **candidate_memory_hint_summary,
        **memory_contract_summary,
        "validation_repair_item_count": _safe_int(
            validation_repair.get("repair_item_count")
        ),
        "validation_repair_constraint_count": _safe_int(
            validation_repair.get("constraint_count")
        ),
        "live_authority_grade": live_authority.get("live_grade"),
        "prompt_over_warn": bool(prompt_budget.get("over_warn")),
        "prompt_over_max": bool(prompt_budget.get("over_max")),
    }


def compact_manager_prompt_context(
    prompt: dict[str, Any],
    *,
    response: dict[str, Any] | None = None,
    actions: dict[str, Any] | None = None,
    hold_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_payload = response or {}
    action_payload = actions or {}
    hold_payload = hold_decision or {}
    if not prompt:
        diagnostics = manager_run_diagnostics(
            prompt={},
            response=response_payload,
            actions=action_payload,
            hold_decision=hold_payload,
        )
        return {"diagnostics": diagnostics} if diagnostics else {}
    decision_packet = (
        prompt.get("decision_packet")
        if isinstance(prompt.get("decision_packet"), dict)
        else {}
    )
    context = {
        "storage_compaction": _compact_storage_compaction_meta_for_context(
            prompt.get("_storage_compaction")
        ),
        "candidate_generation": compact_prompt_value(
            prompt.get("candidate_generation") or {},
            string_limit=180,
            list_limit=8,
        ),
        "growth_unlock": compact_prompt_value(
            prompt.get("growth_unlock") or {},
            string_limit=180,
            list_limit=8,
        ),
        "lane_balance": compact_prompt_value(
            prompt.get("lane_balance") or {},
            string_limit=180,
            list_limit=8,
        ),
        "crypto_market_pulse": compact_prompt_value(
            prompt.get("crypto_market_pulse") or {},
            string_limit=180,
            list_limit=8,
        ),
        "raw_context_refs": compact_prompt_value(
            prompt.get("raw_context_refs") or {},
            string_limit=120,
            list_limit=8,
        ),
        "canonical_decision_packet": compact_prompt_value(
            prompt.get("canonical_decision_packet") or {},
            string_limit=180,
            list_limit=12,
        ),
        "decision_packet": {
            "evidence": compact_prompt_value(
                decision_packet.get("evidence") or [],
                string_limit=180,
                list_limit=12,
            ),
            "scorecards": compact_prompt_value(
                decision_packet.get("scorecards") or [],
                string_limit=180,
                list_limit=8,
            ),
            "active_policies": compact_prompt_value(
                decision_packet.get("active_policies") or [],
                string_limit=180,
                list_limit=8,
            ),
        },
        "candidates": compact_prompt_value(
            prompt.get("candidates") or [],
            string_limit=180,
            list_limit=30,
        ),
    }
    for section, string_limit, list_limit in (
        ("jue_wiki_requested_symbol_coverage", 140, 6),
        ("jue_wiki_repair_contract", 160, 8),
        ("jue_wiki_memory_card_quality", 140, 6),
        ("jue_wiki_decision_adjustments", 140, 6),
        ("jue_wiki", 160, 4),
        ("jue_wiki_application", 160, 6),
        ("jue_wiki_selection_observation", 140, 6),
        ("jue_wiki_validation_repair_effectiveness", 140, 6),
        ("jue_wiki_application_coverage", 140, 6),
    ):
        if section not in prompt:
            continue
        context[section] = compact_prompt_value(
            prompt.get(section) or {},
            string_limit=string_limit,
            list_limit=list_limit,
        )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response=response_payload,
        actions=action_payload,
        hold_decision=hold_payload,
    )
    if diagnostics:
        context["diagnostics"] = diagnostics
    return {key: item for key, item in context.items() if item not in (None, "", [], {})}


def _finalize_prompt_budget_impl(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    warn_chars: int,
    max_chars: int,
) -> None:
    configured_max = max(int(max_chars), 10_000)
    latency_guard = (
        prompt.get("latency_guard")
        if isinstance(prompt.get("latency_guard"), dict)
        else {}
    )
    latency_active = bool(latency_guard.get("active"))
    effective_target_chars = (
        max(int(latency_guard.get("target_chars") or target_chars), 10_000)
        if latency_active
        else max(int(target_chars), 10_000)
    )
    effective_warn_chars = (
        max(int(warn_chars), effective_target_chars)
        if not latency_active
        else max(effective_target_chars + 2_500, effective_target_chars)
    )
    if "output_schema" in prompt:
        prompt["output_schema"] = compact_manager_output_schema_for_prompt(
            prompt.get("output_schema")
        )
    prompt.pop("prompt_budget", None)
    enforce_prompt_budget(prompt, max_chars=configured_max)
    attach_prompt_budget(
        prompt,
        target_chars=effective_target_chars,
        warn_chars=effective_warn_chars,
        max_chars=configured_max,
    )
    if (
        not latency_active
        and _manager_warn_budget_recovery_is_needed(
            prompt,
            warn_chars=effective_warn_chars,
        )
    ):
        prompt.pop("prompt_budget", None)
        sections = compact_manager_sections_for_warn_budget(
            prompt,
            warn_chars=effective_warn_chars,
        )
        extend_prompt_compaction(
            prompt,
            max_chars=configured_max,
            effective_max_chars=effective_warn_chars,
            sections=sections,
        )
    attach_prompt_budget(
        prompt,
        target_chars=effective_target_chars,
        warn_chars=effective_warn_chars,
        max_chars=configured_max,
    )
    target_limit = effective_target_chars
    if bool(latency_guard.get("active")) and prompt_chars(prompt) > target_limit:
        prompt.pop("prompt_budget", None)
        latency_target = max(target_limit - 2_500, 10_000)
        sections = compact_manager_sections_for_final_budget(
            prompt,
            target_chars=latency_target,
        )
        extend_prompt_compaction(
            prompt,
            max_chars=configured_max,
            effective_max_chars=latency_target,
            sections=sections,
        )
        attach_prompt_budget(
            prompt,
            target_chars=effective_target_chars,
            warn_chars=effective_warn_chars,
            max_chars=configured_max,
        )
        if prompt_chars(prompt) > target_limit:
            original_chars = prompt_chars(prompt)
            prompt.pop("prompt_budget", None)
            emergency_payload = manager_prompt_storage_emergency_payload(
                prompt,
                limit=max(target_limit - 2_500, 10_000),
                label="binance_manager_latency_guard",
                original_chars=original_chars,
                retained_keys=list(prompt.keys()),
                compact_value=compact_prompt_value,
                priority_candidate_keys=set(),
            )
            prompt.clear()
            prompt.update(emergency_payload)
            attach_prompt_budget(
                prompt,
                target_chars=effective_target_chars,
                warn_chars=effective_warn_chars,
                max_chars=configured_max,
            )
        if latency_active and prompt_chars(prompt) > effective_warn_chars:
            original_chars = prompt_chars(prompt)
            prompt.pop("prompt_budget", None)
            recovery_payload = latency_recovery_core_prompt(
                prompt,
                latency_guard=latency_guard,
                original_chars=original_chars,
                label="binance_manager_latency_guard_final_core",
            )
            prompt.clear()
            prompt.update(
                {
                    key: value
                    for key, value in recovery_payload.items()
                    if value not in ({}, [], "", None)
                }
            )
            attach_prompt_budget(
                prompt,
                target_chars=effective_target_chars,
                warn_chars=effective_warn_chars,
                max_chars=configured_max,
            )
    if not prompt_budget_error(prompt) and prompt_chars(prompt) <= effective_warn_chars:
        return

    original_chars = prompt_chars(prompt)
    prompt.pop("prompt_budget", None)
    emergency_limit = max(configured_max - 2_500, 10_000)
    emergency_payload = manager_prompt_storage_emergency_payload(
        prompt,
        limit=emergency_limit,
        label="binance_manager_prompt_runtime",
        original_chars=original_chars,
        retained_keys=list(prompt.keys()),
        compact_value=compact_prompt_value,
        priority_candidate_keys=set(),
    )
    prompt.clear()
    prompt.update(emergency_payload)
    attach_prompt_budget(
        prompt,
        target_chars=effective_target_chars,
        warn_chars=effective_warn_chars,
        max_chars=configured_max,
    )
    target_limit = effective_target_chars
    latency_guard = (
        prompt.get("latency_guard")
        if isinstance(prompt.get("latency_guard"), dict)
        else {}
    )
    if bool(latency_guard.get("active")) and prompt_chars(prompt) > target_limit:
        prompt.pop("prompt_budget", None)
        latency_target = max(target_limit - 2_500, 10_000)
        sections = compact_manager_sections_for_final_budget(
            prompt,
            target_chars=latency_target,
        )
        extend_prompt_compaction(
            prompt,
            max_chars=configured_max,
            effective_max_chars=latency_target,
            sections=sections,
        )
        attach_prompt_budget(
            prompt,
            target_chars=effective_target_chars,
            warn_chars=effective_warn_chars,
            max_chars=configured_max,
        )
        if prompt_chars(prompt) <= target_limit:
            return
        original_chars = prompt_chars(prompt)
        prompt.pop("prompt_budget", None)
        emergency_payload = manager_prompt_storage_emergency_payload(
            prompt,
            limit=max(target_limit - 2_500, 10_000),
            label="binance_manager_latency_guard",
            original_chars=original_chars,
            retained_keys=list(prompt.keys()),
            compact_value=compact_prompt_value,
            priority_candidate_keys=set(),
        )
        prompt.clear()
        prompt.update(emergency_payload)
        attach_prompt_budget(
            prompt,
            target_chars=effective_target_chars,
            warn_chars=effective_warn_chars,
            max_chars=configured_max,
        )
        if prompt_chars(prompt) > effective_warn_chars:
            original_chars = prompt_chars(prompt)
            prompt.pop("prompt_budget", None)
            recovery_payload = latency_recovery_core_prompt(
                prompt,
                latency_guard=latency_guard,
                original_chars=original_chars,
                label="binance_manager_latency_guard_final_core",
            )
            prompt.clear()
            prompt.update(
                {
                    key: value
                    for key, value in recovery_payload.items()
                    if value not in ({}, [], "", None)
                }
            )
            attach_prompt_budget(
                prompt,
                target_chars=effective_target_chars,
                warn_chars=effective_warn_chars,
                max_chars=configured_max,
            )
        if prompt_chars(prompt) <= target_limit:
            return
    warn_limit = effective_warn_chars
    if not prompt_budget_error(prompt) and prompt_chars(prompt) <= warn_limit:
        return

    if not prompt_budget_error(prompt) and prompt_chars(prompt) <= configured_max:
        prompt.pop("prompt_budget", None)
        soft_reserve = 2_500
        effective_warn = max(warn_limit - soft_reserve, 10_000)
        sections = compact_manager_sections_for_final_budget(
            prompt,
            target_chars=effective_warn,
        )
        extend_prompt_compaction(
            prompt,
            max_chars=configured_max,
            effective_max_chars=effective_warn,
            sections=sections,
        )
        attach_prompt_budget(
            prompt,
            target_chars=effective_target_chars,
            warn_chars=effective_warn_chars,
            max_chars=configured_max,
        )
        if not prompt_budget_error(prompt) and prompt_chars(prompt) <= warn_limit:
            return

    prompt.pop("prompt_budget", None)
    reserve = 12_000
    warn_reserve = 2_500
    effective_max = max(
        min(configured_max - reserve, warn_limit - warn_reserve),
        10_000,
    )
    sections = compact_manager_sections_for_final_budget(
        prompt,
        target_chars=effective_max,
    )
    extend_prompt_compaction(
        prompt,
        max_chars=configured_max,
        effective_max_chars=effective_max,
        sections=sections,
    )
    attach_prompt_budget(
        prompt,
        target_chars=effective_target_chars,
        warn_chars=effective_warn_chars,
        max_chars=configured_max,
    )


def finalize_prompt_budget(
    prompt: dict[str, Any],
    *,
    target_chars: int,
    warn_chars: int,
    max_chars: int,
) -> None:
    original_latency_guard = (
        dict(prompt.get("latency_guard"))
        if isinstance(prompt.get("latency_guard"), dict)
        else {}
    )
    latency_active = bool(original_latency_guard.get("active"))
    effective_target_chars = (
        max(
            int(original_latency_guard.get("target_chars") or target_chars),
            10_000,
        )
        if latency_active
        else max(int(target_chars), 10_000)
    )
    effective_warn_chars = (
        max(effective_target_chars + 2_500, effective_target_chars)
        if latency_active
        else max(int(warn_chars), effective_target_chars)
    )
    configured_max_chars = max(int(max_chars), 10_000)
    original_sequences = {
        key: list(value)
        for key in ("decision_inputs", "candidates", "blocks", "universe")
        if isinstance((value := prompt.get(key)), list)
    }
    original_thread = {
        key: prompt.get(key)
        for key in ("native_thread_mode", "native_thread_key")
        if prompt.get(key) not in (None, "")
    }
    original_contract_sections = {
        key: prompt.get(key)
        for key in (
            "candidate_generation",
            "candidate_memory_hint_policy",
            "canonical_decision_packet",
            "decision_packet_policy",
            "horizon_action_authority",
            "horizon_policy",
            "jue_wiki_application_coverage",
            "jue_wiki_budget_report",
            "jue_wiki_primary_context_policy",
            "jue_wiki_selection_observation",
            "jue_wiki_validation_repair_effectiveness",
            "lane_balance",
            "language_policy",
            "market_universe",
            "output_language_policy",
            "policy",
            "proactive_decision_pressure",
            "recent_performance",
            "validation_repair",
        )
        if prompt.get(key) not in (None, "", [], {})
    }

    _finalize_prompt_budget_impl(
        prompt,
        target_chars=target_chars,
        warn_chars=warn_chars,
        max_chars=max_chars,
    )

    runtime_candidate_limit = 30 if configured_max_chars >= 60_000 else 8
    compaction_sections: dict[str, dict[str, int]] = {}
    for key, original in original_sequences.items():
        if key == "candidates":
            rows = select_manager_candidate_rows_for_compaction(
                original,
                limit=runtime_candidate_limit,
                priority_candidate_keys=_manager_prompt_priority_candidate_keys(
                    {"candidates": original}
                ),
                lane_diverse=True,
            )
            compacted = compact_prompt_value(
                rows,
                list_limit=runtime_candidate_limit,
                string_limit=180,
            )
        elif key == "blocks":
            compacted = compact_prompt_section(
                "blocks",
                original,
                list_limit=6,
                string_limit=110,
            )
        else:
            compacted = compact_prompt_value(
                original,
                list_limit=32 if key == "decision_inputs" else 12,
                string_limit=100,
            )
        retained = compacted if isinstance(compacted, list) else []
        prompt[key] = retained
        compaction_sections[key] = {
            "item_count": len(original),
            "retained_item_count": len(retained),
            "omitted_item_count": max(len(original) - len(retained), 0),
        }

    for key, original in original_contract_sections.items():
        if key == "policy":
            prompt[key] = (
                compact_prompt_section(
                    "policy",
                    original,
                    list_limit=8,
                    string_limit=240,
                )
                if configured_max_chars >= 60_000
                else compact_prompt_value_bounded(
                    original,
                    list_limit=8,
                    string_limit=140,
                    dict_limit=40,
                )
            )
            continue
        if key in {
            "candidate_generation",
            "horizon_action_authority",
            "horizon_policy",
            "lane_balance",
            "language_policy",
            "market_universe",
            "output_language_policy",
            "proactive_decision_pressure",
            "validation_repair",
        }:
            prompt[key] = compact_prompt_value(
                original,
                list_limit=(
                    40
                    if key in {"language_policy", "output_language_policy"}
                    else 30
                    if key == "market_universe"
                    else 12
                ),
                string_limit=240,
            )
            continue
        if key in prompt:
            continue
        prompt[key] = compact_prompt_value(
            original,
            list_limit=8,
            string_limit=180,
        )

    prompt.update(original_thread)
    prompt["compaction_meta"] = {
        "version": "manager_prompt_compaction_meta_v1",
        "sections": compaction_sections,
    }
    prompt.pop("prompt_budget", None)
    attach_prompt_budget(
        prompt,
        target_chars=effective_target_chars,
        warn_chars=effective_warn_chars,
        max_chars=configured_max_chars,
    )
    needs_final_recovery = bool(prompt_budget_error(prompt)) or (
        latency_active and prompt_chars(prompt) > effective_target_chars
    )
    if needs_final_recovery:
        prompt.pop("prompt_budget", None)
        final_target = (
            max(effective_target_chars - 3_500, 10_000)
            if latency_active
            else max(configured_max_chars - 12_000, 10_000)
        )
        sections = compact_manager_sections_for_final_budget(
            prompt,
            target_chars=final_target,
        )
        if (
            latency_active
            and prompt_chars(prompt) > final_target
            and prompt.get("output_schema") == prompt.get("native_output_schema")
        ):
            before = prompt_chars({"output_schema": prompt.get("output_schema")})
            prompt.pop("output_schema", None)
            sections.append(
                {
                    "section": "output_schema:latency_duplicate_removed",
                    "before_chars": before,
                    "after_chars": 0,
                }
            )
        if (
            latency_active
            and prompt_chars(prompt) > final_target
            and isinstance(prompt.get("candidates"), list)
        ):
            before = prompt_chars({"candidates": prompt.get("candidates")})
            prompt["candidates"] = _compact_prompt_candidates_lane_diverse_section(
                prompt.get("candidates"),
                compact_value=compact_prompt_value,
                clean_text=lambda raw, text_limit: _clean_text(
                    raw,
                    limit=text_limit,
                ),
                list_limit=12,
                string_limit=80,
                priority_candidate_keys=_manager_prompt_priority_candidate_keys(prompt),
            )
            after = prompt_chars({"candidates": prompt.get("candidates")})
            if after < before:
                sections.append(
                    {
                        "section": "candidates:latency_final",
                        "before_chars": before,
                        "after_chars": after,
                    }
                )
        extend_prompt_compaction(
            prompt,
            max_chars=configured_max_chars,
            effective_max_chars=final_target,
            sections=sections,
        )
        for key, original in original_sequences.items():
            retained = prompt.get(key)
            retained_count = len(retained) if isinstance(retained, list) else 0
            compaction_sections[key] = {
                "item_count": len(original),
                "retained_item_count": retained_count,
                "omitted_item_count": max(len(original) - retained_count, 0),
            }
        prompt["compaction_meta"] = {
            "version": "manager_prompt_compaction_meta_v1",
            "sections": compaction_sections,
        }
        attach_prompt_budget(
            prompt,
            target_chars=effective_target_chars,
            warn_chars=effective_warn_chars,
            max_chars=configured_max_chars,
        )


def compact_validation_repair_prompt(
    memory_context: dict[str, Any],
    *,
    scope: str,
    compact_value: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    compact = compact_value or _identity_compact_value
    if not isinstance(memory_context, dict) or not memory_context:
        return {
            "version": "validation_repair_prompt_v1",
            "scope": scope,
            "status": "missing",
            "repair_backlog": [],
            "block_design_constraints": [],
        }

    backlog = (
        memory_context.get("validation_repair_backlog")
        if isinstance(memory_context.get("validation_repair_backlog"), dict)
        else {}
    )
    constraints = (
        memory_context.get("block_design_constraints")
        if isinstance(memory_context.get("block_design_constraints"), dict)
        else {}
    )
    backlog_items: list[dict[str, Any]] = []
    for row in _as_list(backlog.get("items") or backlog.get("primary_items"))[:6]:
        if not isinstance(row, dict):
            continue
        backlog_items.append(
            {
                key: compact(row.get(key), list_limit=3, string_limit=120)
                for key in (
                    "policy_id",
                    "repair_policy_id",
                    "repair_action_id",
                    "event_key",
                    "venue",
                    "discipline_id",
                    "memory_contract",
                    "memory_contract_error",
                    "impacted_symbols",
                    "priority",
                    "status",
                    "label",
                    "owner",
                    "cadence",
                    "automation_hook",
                    "execution_weight",
                    "last_repair_status",
                    "last_repair_policy_status",
                    "last_repair_action",
                    "last_repair_confidence",
                    "last_repair_automation_hook",
                    "last_repair_execution_weight",
                    "last_repair_reason",
                    "lane_policy_hint",
                    "scale_blocker",
                    "validation_effect_profile",
                    "entry_bias",
                    "sizing_policy",
                    "target_stop_review",
                    "min_reward_risk",
                    "max_stop_risk_pct",
                    "risk_budget_multiplier",
                    "max_budget_multiplier",
                    "required_evidence",
                    "required_checks",
                    "blocks_scaling",
                    "blocks_new_entries",
                    "runner_hint",
                    "verification_artifact",
                    "exit_criteria",
                    "validation_mode",
                    "allowed_entry_posture",
                    "live_shadow_required",
                    "scale_up_blocked",
                    "evidence_targets",
                    "pass_current_gap",
                    "pass_collection_hook",
                    "pass_criteria",
                    "pass_required_evidence",
                    "pass_jue_behavior_until_pass",
                    "pass_m1_runtime_profile",
                )
                if row.get(key) not in (None, "", [], {})
            }
        )

    constraint_items: list[dict[str, Any]] = []
    for row in _as_list(constraints.get("items"))[:6]:
        if not isinstance(row, dict):
            continue
        item = {
            key: compact(row.get(key), list_limit=4, string_limit=140)
            for key in (
                "policy_id",
                "venue",
                "discipline_id",
                "memory_contract",
                "memory_contract_error",
                "impacted_symbols",
                "scale_blocker",
                "period_memory_status",
                "period_memory_gap_count",
                "period_memory_override_count",
                "period_memory_contract_gap_count",
                "period_memory_missing_metadata",
                "period_memory_repair_actions",
                "metadata_contract_audit_resolutions",
                "period_memory_repair_quality",
                "priority",
                "validation_effect_profile",
                "entry_bias",
                "sizing_policy",
                "target_stop_review",
                "min_reward_risk",
                "max_stop_risk_pct",
                "risk_budget_multiplier",
                "max_budget_multiplier",
                "required_evidence",
                "required_checks",
                "blocks_scaling",
                "blocks_new_entries",
                "runner_hint",
                "verification_artifact",
                "exit_criteria",
                "risk_note",
                "pass_current_gap",
                "pass_collection_hook",
                "pass_criteria",
                "pass_required_evidence",
            )
            if row.get(key) not in (None, "", [], {})
        }
        note = _metadata_contract_repair_note(row)
        if note:
            item["metadata_contract_repair_note"] = compact(
                note,
                list_limit=1,
                string_limit=300,
            )
        constraint_items.append(item)

    memory_contract_resolution = _memory_contract_resolution_contract_from_repair_rows(
        [*backlog_items, *constraint_items]
    )
    return {
        "version": "validation_repair_prompt_v1",
        "scope": scope,
        "status": str(backlog.get("status") or constraints.get("status") or "ok"),
        "repair_item_count": _safe_int(
            backlog.get("total_item_count") or backlog.get("count") or len(backlog_items)
        ),
        "constraint_count": _safe_int(
            constraints.get("total_item_count")
            or constraints.get("count")
            or len(constraint_items)
        ),
        "instruction": (
            "Use these 19-test repair items as soft block-design constraints. "
            "They adjust entry style, evidence requirements, sizing, target/stop "
            "review, and scale-up authority; they are not strategy hard filters. "
            "Cash, position, duplicate-order, and kill-switch safety gates still override."
        ),
        "repair_backlog": backlog_items,
        "block_design_constraints": constraint_items,
        **memory_contract_resolution,
    }


def validation_repair_action_metadata(
    validation_repair: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(validation_repair, dict) or not validation_repair:
        return {}
    backlog = [
        row
        for row in _as_list(validation_repair.get("repair_backlog"))[:4]
        if isinstance(row, dict)
    ]
    constraints = [
        row
        for row in _as_list(validation_repair.get("block_design_constraints"))[:4]
        if isinstance(row, dict)
    ]
    if not backlog and not constraints:
        return {}

    policy_ids: list[str] = []
    discipline_ids: list[str] = []
    required_evidence: list[str] = []
    required_checks: list[str] = []
    entry_biases: list[str] = []
    sizing_policies: list[str] = []
    scale_blockers: list[str] = []
    blocks_scaling: list[str] = []
    blocks_new_entries: list[str] = []
    runner_hints: list[str] = []
    verification_artifacts: list[str] = []
    repair_action_ids: list[str] = []
    automation_hooks: list[str] = []
    pass_collection_hooks: list[str] = []
    pass_current_gaps: list[str] = []
    pass_criteria: list[str] = []
    execution_weights: list[str] = []
    allowed_entry_postures: list[str] = []
    last_repair_statuses: list[str] = []
    last_repair_reasons: list[str] = []
    period_memory_statuses: list[str] = []
    period_memory_gap_count = 0
    period_memory_override_count = 0
    period_memory_contract_gap_count = 0
    period_memory_missing_metadata: list[str] = []
    period_memory_repair_actions: list[str] = []
    metadata_contract_audit_resolutions: list[str] = []
    metadata_contract_repair_notes: list[str] = []
    period_memory_repair_qualities: list[str] = []
    memory_contracts: list[str] = []
    memory_contract_errors: list[str] = []
    impacted_symbols: list[str] = []
    scale_up_blocked = False
    live_shadow_required = False
    risk_budget_multipliers: list[float] = []
    max_budget_multipliers: list[float] = []
    min_reward_risks: list[float] = []
    max_stop_risk_pcts: list[float] = []

    def add_token(target: list[str], source: Any, *, max_len: int = 160) -> None:
        value = str(source or "").strip()
        if not value:
            return
        if len(value) > max_len:
            value = f"[omitted_long_text chars={len(value)}]"
        if value not in target:
            target.append(value)

    for row in [*backlog, *constraints]:
        for source, target in (
            (row.get("policy_id"), policy_ids),
            (row.get("repair_action_id"), repair_action_ids),
            (row.get("discipline_id"), discipline_ids),
            (row.get("scale_blocker"), scale_blockers),
            (row.get("entry_bias"), entry_biases),
            (row.get("sizing_policy"), sizing_policies),
            (row.get("blocks_scaling"), blocks_scaling),
            (row.get("blocks_new_entries"), blocks_new_entries),
            (row.get("runner_hint"), runner_hints),
            (row.get("verification_artifact"), verification_artifacts),
            (row.get("automation_hook"), automation_hooks),
            (row.get("pass_collection_hook"), pass_collection_hooks),
            (row.get("pass_current_gap"), pass_current_gaps),
            (row.get("pass_criteria"), pass_criteria),
            (row.get("execution_weight"), execution_weights),
            (row.get("allowed_entry_posture"), allowed_entry_postures),
            (row.get("last_repair_status"), last_repair_statuses),
            (row.get("last_repair_reason"), last_repair_reasons),
            (row.get("period_memory_status"), period_memory_statuses),
            (row.get("period_memory_repair_quality"), period_memory_repair_qualities),
            (row.get("memory_contract"), memory_contracts),
            (row.get("memory_contract_error"), memory_contract_errors),
        ):
            add_token(target, source)
        for item in _as_list(row.get("impacted_symbols")):
            add_token(impacted_symbols, item, max_len=80)
        period_memory_gap_count += _safe_int(row.get("period_memory_gap_count"))
        period_memory_override_count += _safe_int(
            row.get("period_memory_override_count")
        )
        period_memory_contract_gap_count += _safe_int(
            row.get("period_memory_contract_gap_count")
        )
        for key, target in (
            ("period_memory_missing_metadata", period_memory_missing_metadata),
            ("period_memory_repair_actions", period_memory_repair_actions),
            (
                "metadata_contract_audit_resolutions",
                metadata_contract_audit_resolutions,
            ),
        ):
            for item in _as_list(row.get(key)):
                add_token(target, item, max_len=160)
        add_token(
            metadata_contract_repair_notes,
            _metadata_contract_repair_note(row),
            max_len=300,
        )
        scale_up_blocked = scale_up_blocked or _truthy_gate_value(
            row.get("scale_up_blocked")
        )
        live_shadow_required = live_shadow_required or _truthy_gate_value(
            row.get("live_shadow_required")
        )
        for key, target in (
            ("required_evidence", required_evidence),
            ("required_checks", required_checks),
        ):
            for item in _as_list(row.get(key)):
                add_token(target, item, max_len=120)
        for key, target in (
            ("risk_budget_multiplier", risk_budget_multipliers),
            ("max_budget_multiplier", max_budget_multipliers),
            ("min_reward_risk", min_reward_risks),
            ("max_stop_risk_pct", max_stop_risk_pcts),
        ):
            value = _safe_float(row.get(key))
            if value > 0:
                target.append(value)

    effective_risk_budget_multiplier = (
        min(risk_budget_multipliers) if risk_budget_multipliers else 0.0
    )
    effective_max_budget_multiplier = (
        min(max_budget_multipliers) if max_budget_multipliers else 0.0
    )
    effective_min_reward_risk = max(min_reward_risks) if min_reward_risks else 0.0
    effective_max_stop_risk_pct = min(max_stop_risk_pcts) if max_stop_risk_pcts else 0.0
    return {
        "validation_repair": {
            "version": "validation_repair_action_v1",
            "scope": str(validation_repair.get("scope") or ""),
            "status": str(validation_repair.get("status") or "ok"),
            "repair_item_count": _safe_int(validation_repair.get("repair_item_count")),
            "constraint_count": _safe_int(validation_repair.get("constraint_count")),
            "policy_ids": policy_ids[:8],
            "repair_action_ids": repair_action_ids[:8],
            "discipline_ids": discipline_ids[:8],
            "scale_blockers": scale_blockers[:8],
            "entry_biases": entry_biases[:6],
            "sizing_policies": sizing_policies[:6],
            "blocks_scaling": blocks_scaling[:6],
            "blocks_new_entries": blocks_new_entries[:6],
            "automation_hooks": automation_hooks[:6],
            "pass_collection_hooks": pass_collection_hooks[:6],
            "pass_current_gaps": pass_current_gaps[:6],
            "pass_criteria": pass_criteria[:6],
            "execution_weights": execution_weights[:6],
            "allowed_entry_postures": allowed_entry_postures[:6],
            "last_repair_statuses": last_repair_statuses[:6],
            "last_repair_reasons": last_repair_reasons[:4],
            "period_memory_statuses": period_memory_statuses[:4],
            "period_memory_gap_count": period_memory_gap_count,
            "period_memory_override_count": period_memory_override_count,
            "period_memory_contract_gap_count": period_memory_contract_gap_count,
            "memory_contracts": memory_contracts[:6],
            "memory_contract_errors": memory_contract_errors[:6],
            "impacted_symbols": impacted_symbols[:12],
            "period_memory_missing_metadata": period_memory_missing_metadata[:6],
            "period_memory_repair_actions": period_memory_repair_actions[:6],
            "metadata_contract_audit_resolutions": (
                metadata_contract_audit_resolutions[:6]
            ),
            "metadata_contract_repair_notes": metadata_contract_repair_notes[:4],
            "period_memory_repair_qualities": period_memory_repair_qualities[:4],
            "runner_hints": runner_hints[:6],
            "verification_artifacts": verification_artifacts[:6],
            "required_evidence": required_evidence[:10],
            "required_checks": required_checks[:10],
            "scale_up_blocked": scale_up_blocked,
            "live_shadow_required": live_shadow_required,
            "risk_budget_multiplier": round(effective_risk_budget_multiplier, 6)
            if effective_risk_budget_multiplier > 0
            else None,
            "max_budget_multiplier": round(effective_max_budget_multiplier, 6)
            if effective_max_budget_multiplier > 0
            else None,
            "min_reward_risk": round(effective_min_reward_risk, 6)
            if effective_min_reward_risk > 0
            else None,
            "max_stop_risk_pct": round(effective_max_stop_risk_pct, 6)
            if effective_max_stop_risk_pct > 0
            else None,
            "raw_sections_omitted": True,
            "hard_filter": False,
        }
    }


def validation_repair_note(validation_repair: dict[str, Any]) -> str:
    if not isinstance(validation_repair, dict):
        return ""
    discipline_ids = [
        str(row or "").strip()
        for row in _as_list(validation_repair.get("discipline_ids"))[:3]
        if str(row or "").strip()
    ]
    entry_biases = [
        str(row or "").strip()
        for row in _as_list(validation_repair.get("entry_biases"))[:2]
        if str(row or "").strip()
    ]
    period_memory_qualities = validation_repair_period_memory_quality_tokens(
        validation_repair
    )[:3]
    if not discipline_ids and not entry_biases and not period_memory_qualities:
        return ""
    parts = []
    if discipline_ids:
        parts.append("검증항목=" + ",".join(discipline_ids))
    if entry_biases:
        parts.append("진입성향=" + ",".join(entry_biases))
    if period_memory_qualities:
        parts.append("메모리수리=" + ",".join(period_memory_qualities))
    return "19검증 반영 - " + " / ".join(parts)


def validation_repair_discipline_tokens(value: Any) -> list[str]:
    repair = value if isinstance(value, dict) else {}
    tokens: list[str] = []

    def add(raw: Any) -> None:
        token = re.sub(r"[\s/]+", "_", str(raw or "").strip().lower())
        if token and token not in tokens:
            tokens.append(token)

    for raw in _as_list(repair.get("discipline_ids")):
        add(raw)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _as_list(repair.get(section)):
            if isinstance(row, dict):
                add(row.get("discipline_id"))
    return tokens[:8]


def validation_repair_period_memory_quality_tokens(value: Any) -> list[str]:
    repair = value if isinstance(value, dict) else {}
    tokens: list[str] = []

    def add(raw: Any) -> None:
        token = re.sub(r"[\s/]+", "_", str(raw or "").strip().lower())
        if token and token not in tokens:
            tokens.append(token)

    add(repair.get("period_memory_repair_quality"))
    for raw in _as_list(repair.get("period_memory_repair_qualities")):
        add(raw)
    for section in ("repair_backlog", "block_design_constraints"):
        for row in _as_list(repair.get(section)):
            if isinstance(row, dict):
                add(row.get("period_memory_repair_quality"))
                for raw in _as_list(row.get("period_memory_repair_qualities")):
                    add(raw)
    return tokens[:8]


def validation_evidence_plan_from_repair(repair: Any) -> dict[str, Any]:
    if not isinstance(repair, dict) or not repair:
        return {}

    dimension_by_discipline = {
        "backtest": "backtest",
        "backtesting": "backtest",
        "backtest_quality": "backtest",
        "walk_forward": "walk_forward",
        "walk_forward_analysis": "walk_forward",
        "wfa": "walk_forward",
        "out_of_sample": "out_of_sample",
        "out_of_sample_test": "out_of_sample",
        "oos": "out_of_sample",
        "live_shadow": "live_shadow",
        "live_shadow_test": "live_shadow",
        "shadow": "live_shadow",
    }
    required_dimensions: list[str] = []
    for discipline_id in _as_list(repair.get("discipline_ids")):
        key = str(discipline_id or "").strip().lower()
        dimension = dimension_by_discipline.get(key)
        if dimension and dimension not in required_dimensions:
            required_dimensions.append(dimension)

    status_tokens = {
        str(value or "").strip().lower()
        for value in _as_list(repair.get("last_repair_statuses"))
        if str(value or "").strip()
    }
    repair_status = str(repair.get("status") or "").strip().lower()
    scale_blocked = _truthy_gate_value(repair.get("scale_up_blocked"))
    pending = (
        repair_status
        in {"pending", "running", "active", "active_caution", "error", "failed", "blocked"}
        or any(
            token.startswith("queued")
            or token in {
                "pending",
                "running",
                "active_caution",
                "error",
                "failed",
                "blocked",
            }
            for token in status_tokens
        )
    )
    plan = {
        "version": "validation_evidence_plan_v1",
        "source": "validation_repair",
        "status": "repair_required" if pending or scale_blocked else "requirements_attached",
        "required_dimensions": required_dimensions[:4],
        "missing_dimensions": required_dimensions[:4],
        "required_evidence": _as_list(repair.get("required_evidence"))[:10],
        "required_checks": _as_list(repair.get("required_checks"))[:10],
        "pass_collection_hooks": _as_list(repair.get("pass_collection_hooks"))[:6],
        "pass_current_gaps": _as_list(repair.get("pass_current_gaps"))[:6],
        "pass_criteria": _as_list(repair.get("pass_criteria"))[:6],
        "verification_artifacts": _as_list(repair.get("verification_artifacts"))[:6],
        "period_memory_repair_qualities": (
            validation_repair_period_memory_quality_tokens(repair)[:6]
        ),
        "scale_up_blocked": scale_blocked,
        "live_shadow_required": _truthy_gate_value(repair.get("live_shadow_required")),
    }
    return {
        key: value
        for key, value in plan.items()
        if value not in (None, "", [], {})
    }


def compact_validation_repair_for_storage(
    value: Any,
    *,
    repair_metadata: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    metadata = repair_metadata(value) if repair_metadata is not None else {}
    repair = metadata.get("validation_repair") if isinstance(metadata, dict) else None
    if isinstance(repair, dict) and repair:
        return repair

    allowed_keys = {
        "version",
        "scope",
        "status",
        "repair_item_count",
        "constraint_count",
        "policy_ids",
        "repair_action_ids",
        "discipline_ids",
        "scale_blockers",
        "entry_biases",
        "sizing_policies",
        "blocks_scaling",
        "blocks_new_entries",
        "automation_hooks",
        "pass_collection_hooks",
        "pass_current_gaps",
        "pass_criteria",
        "execution_weights",
        "allowed_entry_postures",
        "last_repair_statuses",
        "last_repair_reasons",
        "period_memory_statuses",
        "period_memory_gap_count",
        "period_memory_override_count",
        "runner_hints",
        "verification_artifacts",
        "required_evidence",
        "required_checks",
        "scale_up_blocked",
        "live_shadow_required",
        "risk_budget_multiplier",
        "max_budget_multiplier",
        "min_reward_risk",
        "max_stop_risk_pct",
        "raw_sections_omitted",
        "hard_filter",
    }
    compact: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in value:
            continue
        item = value.get(key)
        if isinstance(item, str) and len(item) > 160:
            compact[key] = f"[omitted_long_text chars={len(item)}]"
        elif isinstance(item, list):
            compact_items: list[Any] = []
            for row in item[:10]:
                if isinstance(row, str) and len(row) > 160:
                    compact_items.append(f"[omitted_long_text chars={len(row)}]")
                else:
                    compact_items.append(row)
            compact[key] = compact_items
        elif isinstance(item, (str, int, float, bool)) or item is None:
            compact[key] = item
    if not compact:
        return {}
    compact["raw_sections_omitted"] = True
    compact["hard_filter"] = False
    return compact


def normalize_lane_review(
    *,
    response: dict[str, Any],
    lane_balance: dict[str, Any],
) -> dict[str, Any]:
    raw = response.get("lane_review")
    review = dict(raw) if isinstance(raw, dict) else {}
    recent = lane_balance.get("recent_blocks") if isinstance(lane_balance, dict) else {}
    candidate_lanes = (
        lane_balance.get("candidate_lanes")
        if isinstance(lane_balance, dict)
        else {}
    )
    return {
        "status": "provided" if review else "missing_from_model",
        "dominant_lane": review.get("dominant_lane")
        or (recent.get("dominant_lane") if isinstance(recent, dict) else ""),
        "review_required": bool(
            recent.get("requires_review") if isinstance(recent, dict) else False
        ),
        "selected_lanes": review.get("selected_lanes") or [],
        "lanes_reviewed": review.get("lanes_reviewed") or list(BINANCE_MANAGER_LANES),
        "non_selected_lane_reasons": review.get("non_selected_lane_reasons") or {},
        "candidate_lane_summary": (
            candidate_lanes.get("items")
            if isinstance(candidate_lanes, dict)
            else {}
        ),
        "concentration_note": review.get("concentration_note") or "",
        "exploration_watch": review.get("exploration_watch") or [],
    }
