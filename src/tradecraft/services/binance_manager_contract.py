from __future__ import annotations

import re
from typing import Any, Callable

from tradecraft.services.binance_lane import normalize_binance_horizon, parse_universe
from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    explicit_market_scope,
    normalize_market,
    normalize_position_side,
)


def safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clean_text(value: Any, *, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[: max(int(limit), 1)]


def clean_string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        rows: list[Any] = [value]
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    clean: list[str] = []
    for row in rows:
        text = _clean_text(row, limit=240)
        if text and text not in clean:
            clean.append(text)
        if len(clean) >= limit:
            break
    return clean


_TRIGGER_PRICE_NUMBER_RE = r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"
_NON_PRICE_TRIGGER_CONTEXT_RE = re.compile(
    r"(?:신뢰도|confidence|rr|r/r|reward\s*/?\s*risk|stop\s*risk|"
    r"손절\s*위험|위험|확률|승률)\s*(?:은|는|이|가|:|=)?\s*$",
    re.IGNORECASE,
)


def _positive_price_from_text(value: Any) -> float:
    price = safe_float(str(value or "").replace(",", ""))
    return price if price > 0 else 0.0


def _infer_trigger_price_from_condition(*, symbol: str, condition: str) -> float:
    text = _clean_text(condition, limit=300)
    if not text:
        return 0.0
    symbol_text = str(symbol or "").upper().strip()
    if symbol_text:
        symbol_pattern = re.compile(
            rf"(?<![A-Z0-9-]){re.escape(symbol_text)}(?![A-Z0-9-])"
            rf"\s*(?:[<>]=?|[≤≥]|이상|이하|above|below|near|at|@)\s*"
            rf"{_TRIGGER_PRICE_NUMBER_RE}",
            re.IGNORECASE,
        )
        match = symbol_pattern.search(text)
        if match:
            return _positive_price_from_text(match.group(1))
    korean_price_pattern = re.compile(
        rf"{_TRIGGER_PRICE_NUMBER_RE}(?!\s*%)\s*"
        r"(?:이상|이하|부근|근처|돌파|반등|눌림|재진입|재확인)"
    )
    for match in korean_price_pattern.finditer(text):
        left_context = text[max(0, match.start() - 32) : match.start()]
        if _NON_PRICE_TRIGGER_CONTEXT_RE.search(left_context):
            continue
        return _positive_price_from_text(match.group(1))
    price_word_pattern = re.compile(
        rf"(?:가격|price|entry|trigger|진입|대기 가격)\s*"
        rf"(?:[:=]|이|가|은|는|at|near|[<>]=?|[≤≥]|@)?\s*"
        rf"{_TRIGGER_PRICE_NUMBER_RE}(?!\s*%)",
        re.IGNORECASE,
    )
    match = price_word_pattern.search(text)
    if match:
        return _positive_price_from_text(match.group(1))
    return 0.0


def manager_action_count(
    actions: dict[str, list[dict[str, Any]]],
    *,
    allowed_actions: tuple[str, ...] | list[str] | set[str],
) -> int:
    return sum(
        len(actions.get(key) or [])
        for key in allowed_actions
        if isinstance(actions.get(key), list)
    )


def raise_for_manager_llm_error_payload(
    payload: dict[str, Any],
    *,
    allowed_actions: tuple[str, ...] | list[str] | set[str],
) -> None:
    has_actions = any(key in payload for key in allowed_actions)
    error_message = str(payload.get("error") or payload.get("error_message") or "")
    if payload.get("ok") is False or (error_message and not has_actions):
        raise RuntimeError(error_message or "codex_runtime_error")


def _hold_decision_from_payload(
    *,
    response: dict[str, Any],
    symbols: list[str],
    action_count: int,
) -> dict[str, Any]:
    payload = response.get("payload")
    if not isinstance(payload, dict):
        return {}
    summary = _clean_text(
        payload.get("claim")
        or payload.get("thesis")
        or payload.get("decision")
        or "",
        limit=420,
    )
    reasons = clean_string_list(payload.get("reasons"), limit=8)
    evidence = payload.get("evidence")
    if not reasons and isinstance(evidence, list):
        reasons = clean_string_list(
            [
                row.get("claim") if isinstance(row, dict) else row
                for row in evidence
            ],
            limit=6,
        )
    planned_actions = clean_string_list(payload.get("next_actions"), limit=8)
    data_gaps = clean_string_list(payload.get("data_gaps"), limit=8)
    risk_notes = clean_string_list(payload.get("risks"), limit=8)
    risk_note = _clean_text(payload.get("risk_note"), limit=360)
    if risk_note and risk_note not in risk_notes:
        risk_notes.insert(0, risk_note)
    symbol = str(payload.get("symbol") or "").upper().strip()
    market = normalize_market(
        payload.get("market")
        or payload.get("venue")
        or payload.get("market_or_account_scope")
    )
    entry_price = safe_float(
        payload.get("entry_price")
        or payload.get("entry_price_usdt")
        or payload.get("trigger_price")
        or payload.get("entry_trigger_price")
    )
    entry_style = _clean_text(
        payload.get("entry_style")
        or payload.get("entry_condition")
        or payload.get("trigger_condition"),
        limit=180,
    )
    if entry_price <= 0:
        entry_price = _infer_trigger_price_from_condition(
            symbol=symbol,
            condition=entry_style,
        )
    triggers: list[dict[str, Any]] = []
    if symbol and (entry_price > 0 or entry_style):
        triggers.append(
            {
                "symbol": symbol,
                "market": market,
                "condition": entry_style or "entry_price 감시",
                "price": entry_price,
                "reason": _clean_text(
                    payload.get("thesis") or payload.get("claim"),
                    limit=240,
                ),
            }
        )
    watch_symbols = parse_universe(
        ",".join(
            [
                symbol,
                *clean_string_list(payload.get("watch_symbols"), limit=12),
                *symbols[:6],
            ]
        )
    )[:12]
    if not any([summary, reasons, planned_actions, data_gaps, risk_notes, triggers]):
        return {}
    if not summary:
        summary = (
            "액션 실행: payload 근거를 반영한 블록 판단입니다."
            if action_count
            else "관망: payload 근거를 반영해 다음 조건을 감시합니다."
        )
    return {
        "summary": summary,
        "reasons": reasons,
        "watch_symbols": watch_symbols,
        "next_triggers": triggers,
        "planned_actions": planned_actions,
        "data_gaps": data_gaps,
        "risk_notes": risk_notes,
    }


def _hold_decision_is_sparse(raw: dict[str, Any], *, action_count: int) -> bool:
    if not raw:
        return True
    default_summaries = {
        "관망: 이번 사이클에서는 실행할 블록 변화가 없습니다.",
        "액션 실행: 매니저가 블록 변경을 선택했습니다.",
        "최근 판단은 관망입니다. 다음 매니저 실행에서 조건을 다시 봅니다.",
    }
    summary = str(raw.get("summary") or "").strip()
    reasons = raw.get("reasons")
    sparse_reasons = (
        not reasons
        or reasons == ["이번 사이클에서는 실행할 블록 변화가 없습니다."]
    )
    no_detail = not any(
        raw.get(key)
        for key in (
            "next_triggers",
            "triggers",
            "planned_actions",
            "data_gaps",
            "risk_notes",
        )
    )
    if summary in default_summaries and sparse_reasons and no_detail:
        return True
    return action_count == 0 and sparse_reasons and no_detail


def _hold_decision_from_validation_repair_resolution(
    *,
    response: dict[str, Any],
    action_count: int,
) -> dict[str, Any]:
    resolution = (
        response.get("validation_repair_resolution")
        if isinstance(response.get("validation_repair_resolution"), dict)
        else {}
    )
    triggers: list[dict[str, Any]] = []
    watch_symbols: list[str] = []
    data_gaps: list[str] = []
    reasons: list[str] = []
    for row in resolution.get("resolved_candidates") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("code") or "").upper().strip()
        condition = _clean_text(
            row.get("next_trigger")
            or row.get("condition")
            or row.get("trigger"),
            limit=180,
        )
        evidence_gap = _clean_text(row.get("evidence_gap"), limit=240)
        repair_resolution = _clean_text(row.get("resolution"), limit=120)
        if symbol and symbol not in watch_symbols:
            watch_symbols.append(symbol)
        if evidence_gap and evidence_gap not in data_gaps:
            data_gaps.append(evidence_gap)
        reason = evidence_gap or repair_resolution
        if reason and reason not in reasons:
            reasons.append(reason)
        if symbol and condition:
            price = safe_float(
                row.get("price")
                or row.get("trigger_price")
                or row.get("entry_trigger_price")
                or row.get("entry_price")
            )
            if price <= 0:
                price = _infer_trigger_price_from_condition(
                    symbol=symbol,
                    condition=condition,
                )
            triggers.append(
                {
                    "symbol": symbol,
                    "market": normalize_market(row.get("market")),
                    "condition": condition,
                    "price": price,
                    "reason": reason,
                }
            )
        if len(triggers) >= 8:
            break
    if not any([watch_symbols, triggers, data_gaps, reasons]):
        return {}
    return {
        "summary": (
            "액션 실행: validation repair 근거를 반영한 블록 판단입니다."
            if action_count
            else "관망: validation repair 근거를 반영해 다음 조건을 감시합니다."
        ),
        "reasons": reasons[:8],
        "watch_symbols": watch_symbols[:12],
        "next_triggers": triggers,
        "planned_actions": [],
        "data_gaps": data_gaps[:8],
        "risk_notes": [],
    }


def _merge_hold_decision_contexts(
    *holds: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def extend_unique(key: str, values: Any) -> None:
        if not isinstance(values, list):
            return
        bucket = result.setdefault(key, [])
        for value in values:
            if value in (None, "", [], {}):
                continue
            if value not in bucket:
                bucket.append(value)

    seen_triggers: set[tuple[str, str, str, str, str]] = set()
    triggers: list[dict[str, Any]] = []
    for hold in holds:
        if not isinstance(hold, dict) or not hold:
            continue
        if not result.get("summary") and hold.get("summary"):
            result["summary"] = hold.get("summary")
        for key in ("reasons", "planned_actions", "data_gaps", "risk_notes"):
            extend_unique(key, hold.get(key))
        extend_unique("watch_symbols", hold.get("watch_symbols"))
        for row in hold.get("next_triggers") or hold.get("triggers") or []:
            if not isinstance(row, dict):
                continue
            trigger = {
                "symbol": str(row.get("symbol") or "").upper().strip(),
                "market": normalize_market(row.get("market")),
                "condition": _clean_text(
                    row.get("condition") or row.get("trigger"),
                    limit=180,
                ),
                "price": safe_float(
                    row.get("price")
                    or row.get("trigger_price")
                    or row.get("entry_trigger_price")
                    or row.get("entry_price")
                ),
                "reason": _clean_text(row.get("reason"), limit=240),
            }
            if trigger["price"] <= 0:
                trigger["price"] = _infer_trigger_price_from_condition(
                    symbol=trigger["symbol"],
                    condition=trigger["condition"],
                )
            identity = (
                str(trigger.get("symbol") or ""),
                str(trigger.get("market") or ""),
                str(trigger.get("condition") or ""),
                str(trigger.get("price") or ""),
                str(trigger.get("reason") or ""),
            )
            if identity not in seen_triggers and any(
                trigger.get(key) not in (None, "", [], {})
                for key in ("symbol", "market", "condition", "reason")
            ):
                seen_triggers.add(identity)
                triggers.append(trigger)
    if triggers:
        result["next_triggers"] = triggers[:8]
    if result.get("watch_symbols"):
        result["watch_symbols"] = parse_universe(
            ",".join(str(symbol) for symbol in result["watch_symbols"])
        )[:12]
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [], {})
    }


def normalize_manager_hold_decision(
    *,
    response: dict[str, Any],
    actions: dict[str, list[dict[str, Any]]],
    symbols: list[str],
    allowed_actions: tuple[str, ...] | list[str] | set[str],
) -> dict[str, Any]:
    raw = response.get("hold_decision")
    if not isinstance(raw, dict):
        raw = {}
    action_count = manager_action_count(actions, allowed_actions=allowed_actions)
    payload_hold = _hold_decision_from_payload(
        response=response,
        symbols=symbols,
        action_count=action_count,
    )
    repair_hold = _hold_decision_from_validation_repair_resolution(
        response=response,
        action_count=action_count,
    )
    enriched_hold = _merge_hold_decision_contexts(payload_hold, repair_hold)
    if enriched_hold:
        if _hold_decision_is_sparse(raw, action_count=action_count):
            raw = {**enriched_hold, "action_count": action_count}
        else:
            merged_raw = _merge_hold_decision_contexts(raw, enriched_hold)
            raw = {
                **raw,
                **merged_raw,
                "summary": raw.get("summary") or merged_raw.get("summary"),
            }
    summary = _clean_text(
        raw.get("summary")
        or raw.get("reason")
        or raw.get("rationale")
        or (
            "관망: 이번 사이클에서는 실행할 블록 변화가 없습니다."
            if action_count == 0
            else "액션 실행: 매니저가 블록 변경을 선택했습니다."
        ),
        limit=400,
    )
    reasons = clean_string_list(raw.get("reasons") or raw.get("why"), limit=8)
    if action_count == 0 and not reasons:
        reasons = ["이번 사이클에서는 실행할 블록 변화가 없습니다."]
    planned_actions = clean_string_list(
        raw.get("planned_actions") or raw.get("next_actions"),
        limit=8,
    )
    watch_symbols = parse_universe(
        ",".join(
            [
                *clean_string_list(
                    raw.get("watch_symbols")
                    or raw.get("symbols")
                    or raw.get("watchlist"),
                    limit=12,
                ),
                *symbols[:6],
            ]
        )
    )[:12]
    triggers: list[dict[str, Any]] = []
    raw_triggers = raw.get("next_triggers") or raw.get("triggers") or []
    if isinstance(raw_triggers, list):
        for item in raw_triggers:
            if not isinstance(item, dict):
                continue
            trigger = {
                "symbol": str(item.get("symbol") or "").upper().strip(),
                "market": normalize_market(item.get("market")),
                "condition": _clean_text(
                    item.get("condition") or item.get("operator") or item.get("trigger"),
                    limit=180,
                ),
                "price": safe_float(
                    item.get("price")
                    or item.get("trigger_price")
                    or item.get("entry_trigger_price")
                    or item.get("entry_price")
                ),
                "reason": _clean_text(item.get("reason"), limit=240),
            }
            if trigger["price"] <= 0:
                trigger["price"] = _infer_trigger_price_from_condition(
                    symbol=trigger["symbol"],
                    condition=trigger["condition"],
                )
            if trigger["symbol"] or trigger["condition"] or trigger["price"] > 0:
                triggers.append(trigger)
            if len(triggers) >= 8:
                break
    return {
        "summary": summary,
        "reasons": reasons,
        "watch_symbols": watch_symbols,
        "next_triggers": triggers,
        "planned_actions": planned_actions,
        "data_gaps": clean_string_list(
            raw.get("data_gaps") or raw.get("missing_data"),
            limit=8,
        ),
        "risk_notes": clean_string_list(
            raw.get("risk_notes") or raw.get("risks"),
            limit=8,
        ),
        "action_count": action_count,
    }


def manager_contract_action_key(
    payload: dict[str, Any],
    *,
    response: dict[str, Any],
) -> str:
    raw = str(
        payload.get("decision")
        or payload.get("action")
        or payload.get("action_type")
        or response.get("decision")
        or response.get("action")
        or ""
    ).strip().lower()
    compact = re.sub(r"[\s/_-]+", "_", raw)
    has_symbol = bool(str(payload.get("symbol") or "").strip())
    has_block_id = bool(str(payload.get("block_id") or "").strip())
    has_qty = safe_float(payload.get("qty") or payload.get("quantity")) > 0
    if (
        compact
        in {
            "adopt",
            "adopt_block",
            "adopt_existing",
            "adopt_existing_block",
            "adopt_existing_blocks",
            "existing_position",
            "wallet_adoption",
        }
        or (
            has_symbol
            and has_qty
            and any(token in compact for token in ("adopt", "existing_position"))
        )
    ):
        return "adopt_existing_blocks"
    if (
        compact in {"create", "create_block", "create_blocks", "new_block"}
        or (
            has_symbol
            and any(
                token in compact
                for token in ("create", "create_block", "create_blocks", "new_block")
            )
        )
    ):
        return "create_blocks"
    if (
        compact in {"update", "update_block", "update_blocks"}
        or (has_block_id and "update" in compact)
    ):
        return "update_blocks"
    if (
        compact in {"close", "close_block", "close_blocks", "exit", "exit_block"}
        or (
            has_block_id
            and any(token in compact for token in ("close", "exit", "stale"))
        )
    ):
        return "close_blocks"
    if (
        compact in {"pause", "pause_block", "pause_blocks"}
        or (has_block_id and "pause" in compact)
    ):
        return "pause_blocks"
    return ""


def manager_contract_close_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(block_id: Any, *, reason: str) -> None:
        clean = str(block_id or "").strip()
        if not clean or clean in seen:
            return
        seen.add(clean)
        rows.append({"block_id": clean, "reason": reason})

    close_blocks = payload.get("close_blocks")
    if isinstance(close_blocks, list):
        for row in close_blocks:
            if isinstance(row, dict):
                add(
                    row.get("block_id") or row.get("id"),
                    reason=str(row.get("reason") or "manager_close_requested"),
                )
            else:
                add(row, reason="manager_close_requested")

    for key, reason in (
        ("close_block_ids", "manager_close_requested"),
        ("stale_block_ids", "manager_stale_waiting_block"),
        ("block_ids_to_close", "manager_close_requested"),
    ):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for block_id in values:
            add(block_id, reason=reason)

    text_values: list[str] = []
    for key in ("next_actions", "reasons", "risks"):
        value = payload.get(key)
        if isinstance(value, list):
            text_values.extend(str(row) for row in value)
        elif isinstance(value, str):
            text_values.append(value)
    block_pattern = re.compile(r"\bbnb_(?:spot|futures)_[A-Z0-9]+_\d{14,}\b")
    for text in text_values:
        lowered = text.lower()
        if not any(token in lowered for token in ("close", "exit", "stale", "정리")):
            continue
        for block_id in block_pattern.findall(text):
            add(block_id, reason="manager_stale_waiting_block")
    return rows


def manager_contract_payloads(response: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    def append_payload(value: Any) -> None:
        if not isinstance(value, dict):
            return
        nested = value.get("payload")
        if isinstance(nested, dict):
            payloads.append(dict(nested))
            return
        payloads.append(dict(value))

    append_payload(response.get("payload"))
    append_payload(response.get("selected_contract"))
    append_payload(response.get("contract_payload"))
    for key in ("payloads", "selected_contracts", "contracts"):
        rows = response.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            append_payload(row)
    return payloads


def manager_actions_from_contract_payload(
    response: dict[str, Any],
    *,
    normalize_create_payload: Callable[[dict[str, Any]], dict[str, Any]],
    allowed_actions: tuple[str, ...] | list[str] | set[str],
) -> dict[str, list[dict[str, Any]]]:
    actions: dict[str, list[dict[str, Any]]] = {
        key: [] for key in sorted(allowed_actions)
    }
    for payload in manager_contract_payloads(response):
        action_key = manager_contract_action_key(payload, response=response)
        close_rows = manager_contract_close_payloads(payload)
        if close_rows:
            actions["close_blocks"].extend(close_rows)
        if action_key not in actions:
            continue
        if action_key == "create_blocks":
            actions[action_key].append(normalize_create_payload(payload))
            continue
        if action_key == "close_blocks" and close_rows:
            continue
        actions[action_key].append(dict(payload))
    return actions


def validate_manager_actions(
    response: dict[str, Any],
    *,
    normalize_create_payload: Callable[[dict[str, Any]], dict[str, Any]],
    allowed_actions: tuple[str, ...] | list[str] | set[str],
) -> dict[str, list[dict[str, Any]]]:
    actions: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(allowed_actions):
        rows = response.get(key)
        if rows is None:
            actions[key] = []
            continue
        if not isinstance(rows, list):
            raise ValueError(f"{key} must be a list")
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                if key == "create_blocks" and (
                    row.get("market_or_account_scope")
                    or row.get("contract_id")
                    or row.get("selected_contract_id")
                ):
                    normalized_rows.append(normalize_create_payload(row))
                else:
                    normalized_rows.append(dict(row))
        actions[key] = normalized_rows
    if not manager_action_count(actions, allowed_actions=allowed_actions):
        inferred = manager_actions_from_contract_payload(
            response,
            normalize_create_payload=normalize_create_payload,
            allowed_actions=allowed_actions,
        )
        for key, rows in inferred.items():
            actions[key].extend(rows)
    return actions


def infer_manager_contract_side(
    payload: dict[str, Any],
    *,
    candidate: dict[str, Any],
    row: dict[str, Any],
) -> str:
    def direct_side(value: Any) -> str:
        raw = str(value or "").strip().lower()
        compact = re.sub(r"[\s/_-]+", "_", raw)
        if compact in {"short", "sell", "short_watch", "short_bias", "bearish"}:
            return "short"
        if compact in {"long", "buy", "long_watch", "long_bias", "bullish"}:
            return "long"
        return ""

    for source in (payload, row, candidate):
        for key in ("side", "position_side", "direction", "stance"):
            side = direct_side(source.get(key))
            if side:
                return side
    entry = safe_float(
        row.get("entry_price")
        or row.get("entry_price_usdt")
        or row.get("entry_trigger_price")
        or row.get("trigger_price")
        or payload.get("entry_price")
        or payload.get("entry_price_usdt")
        or payload.get("entry_trigger_price")
        or payload.get("trigger_price")
        or candidate.get("entry_price")
        or candidate.get("entry_price_usdt")
        or candidate.get("entry_trigger_price")
        or candidate.get("trigger_price")
    )
    target = safe_float(
        row.get("target_price")
        or row.get("target_price_usdt")
        or payload.get("target_price")
        or payload.get("target_price_usdt")
        or candidate.get("target_price")
        or candidate.get("target_price_usdt")
    )
    stop = safe_float(
        row.get("stop_price")
        or row.get("stop_price_usdt")
        or payload.get("stop_price")
        or payload.get("stop_price_usdt")
        or candidate.get("stop_price")
        or candidate.get("stop_price_usdt")
    )
    if target > 0 and entry > 0 and stop > 0:
        if target < entry < stop:
            return "short"
        if stop < entry < target:
            return "long"
    explicit_scope = explicit_market_scope(payload.get("market_or_account_scope"))
    market_scope = str(
        payload.get("market")
        or payload.get("venue")
        or row.get("market")
        or row.get("venue")
        or candidate.get("market")
        or candidate.get("venue")
        or ""
    ).strip().lower()
    market_scope = explicit_scope or market_scope
    if market_scope == "spot":
        return "long"
    text_parts: list[str] = []
    for key in ("claim", "thesis", "risk_note", "reason"):
        text_parts.append(str(payload.get(key) or ""))
    for key in ("reasons", "risks", "next_actions"):
        value = payload.get(key)
        if isinstance(value, list):
            text_parts.extend(str(row) for row in value)
    haystack = " ".join(text_parts).lower()
    short_hit = any(token in haystack for token in ("short", " 숏", "숏 ", "sell"))
    long_hit = any(token in haystack for token in ("long", " 롱", "롱 ", "buy"))
    if short_hit and not long_hit:
        return "short"
    if long_hit and not short_hit:
        return "long"
    return ""


def apply_manager_contract_aliases(row: dict[str, Any]) -> None:
    calculated = (
        row.get("calculated_price_plan")
        if isinstance(row.get("calculated_price_plan"), dict)
        else row.get("calculated")
        if isinstance(row.get("calculated"), dict)
        else {}
    )
    explicit_scope = explicit_market_scope(row.get("market_or_account_scope"))
    if explicit_scope:
        row["market"] = explicit_scope
        row["venue"] = explicit_scope
    elif "market" not in row and row.get("market_or_account_scope"):
        row["market"] = normalize_market(row.get("market_or_account_scope"))
    if "entry_price" not in row:
        row["entry_price"] = (
            row.get("entry_price_usdt")
            or row.get("entry_trigger_price")
            or calculated.get("entry_price")
        )
    if "target_price" not in row:
        row["target_price"] = row.get("target_price_usdt") or calculated.get("target_price")
    if "stop_price" not in row:
        row["stop_price"] = row.get("stop_price_usdt") or calculated.get("stop_price")
    if row.get("quantity_or_quote_budget") is not None:
        if normalize_market(row.get("market")) == UPBIT_SPOT_MARKET:
            row.setdefault("quote_budget_krw", row.get("quantity_or_quote_budget"))
            row.setdefault("quote_budget", row.get("quantity_or_quote_budget"))
            row.setdefault("quote_currency", "KRW")
        else:
            row.setdefault("quote_budget_usdt", row.get("quantity_or_quote_budget"))
    if "entry_trigger_price" not in row and calculated.get("entry_trigger_price") is not None:
        row["entry_trigger_price"] = calculated.get("entry_trigger_price")
    if "entry_trigger_operator" not in row and calculated.get("entry_trigger_operator"):
        row["entry_trigger_operator"] = calculated.get("entry_trigger_operator")
    if "margin_type" not in row and calculated.get("margin_type"):
        row["margin_type"] = calculated.get("margin_type")
    if "leverage" not in row and calculated.get("leverage"):
        row["leverage"] = calculated.get("leverage")
    if "liquidation_price" not in row and calculated.get("liquidation_price"):
        row["liquidation_price"] = calculated.get("liquidation_price")


def manager_contract_candidate_matches_source_id(
    candidate: dict[str, Any],
    source_id: str,
) -> bool:
    if str(candidate.get("source_id") or "") == source_id:
        return True
    evidence_refs = candidate.get("evidence_refs")
    if isinstance(evidence_refs, list) and source_id in {str(row) for row in evidence_refs}:
        return True
    return source_id == f"candidates:{str(candidate.get('symbol') or '').upper().strip()}"


def manager_contract_candidate_price_match_score(
    candidate: dict[str, Any],
    payload: dict[str, Any],
) -> int:
    score = 0
    for key in ("target_price", "stop_price", "entry_price"):
        expected = safe_float(payload.get(key) or payload.get(f"{key}_usdt"))
        actual = safe_float(candidate.get(key) or candidate.get(f"{key}_usdt"))
        if expected <= 0 or actual <= 0:
            continue
        if abs(expected - actual) <= max(actual * 0.001, 0.000001):
            score += 3
    return score


def normalized_manager_contract_horizon(value: Any, *, market: Any = "spot") -> str:
    return normalize_binance_horizon(value, market=normalize_market(market))


def normalized_manager_contract_market(value: Any) -> str:
    return normalize_market(value)


def normalized_manager_contract_position_side(value: Any) -> str:
    return normalize_position_side(value)
