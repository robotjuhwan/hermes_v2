from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tradecraft.services.daily_discovery import enrich_discovery_result


GENERIC_NAMES = {
    "",
    "정보",
    "투자",
    "리포트",
    "리포트 보기",
    "코스콤 국내 시세 정보",
}


def _is_symbol(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 6 and text.isdigit()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _clean_name(value: Any, *, symbol: str = "") -> str:
    text = str(value or "").strip()
    if not text or text == symbol or text in GENERIC_NAMES:
        return ""
    if _is_symbol(text):
        return ""
    return text[:80]


def _normalize_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _compact_strings(value: Any, *, limit: int = 4, chars: int = 180) -> list[str]:
    out: list[str] = []
    for item in _normalize_list(value):
        text = str(item or "").strip()
        if not text:
            continue
        text = " ".join(text.split())
        if text and text not in out:
            out.append(text[:chars])
        if len(out) >= limit:
            break
    return out


def _position_names(account: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in _normalize_list(account.get("positions")):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("asset") or "").strip()
        if not _is_symbol(symbol):
            continue
        name = _clean_name(row.get("name") or row.get("asset_name"), symbol=symbol)
        if name:
            names[symbol] = name
    return names


def _position_symbols(account: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()
    for row in _normalize_list(account.get("positions")):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("asset") or "").strip()
        if _is_symbol(symbol):
            symbols.add(symbol)
    return symbols


def _block_names(blocks: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in blocks:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            continue
        name = _clean_name(row.get("name"), symbol=symbol)
        if name:
            names[symbol] = name
    return names


def _block_symbols(blocks: list[dict[str, Any]]) -> set[str]:
    symbols: set[str] = set()
    for row in blocks:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if _is_symbol(symbol):
            symbols.add(symbol)
    return symbols


def _quote_names(quotes: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in quotes:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            continue
        name = _clean_name(row.get("name"), symbol=symbol)
        if name:
            names[symbol] = name
    return names


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _compact_number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 4)


def _compact_text(value: Any, *, chars: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:chars] if text else ""


def _compact_block_context(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    thesis = _compact_text(
        row.get("thesis")
        or row.get("llm_reason")
        or metadata.get("thesis")
        or metadata.get("llm_reason")
    )
    fields: dict[str, Any] = {
        "block_id": _compact_text(row.get("block_id"), chars=80),
        "status": _compact_text(row.get("status"), chars=40),
        "horizon": _compact_text(row.get("horizon"), chars=40),
        "qty_open": _compact_number(
            _first_present(row, ("qty_open", "remaining_qty", "qty", "qty_initial"))
        ),
        "entry_price": _compact_number(row.get("entry_price")),
        "target_price": _compact_number(row.get("target_price")),
        "stop_price": _compact_number(row.get("stop_price")),
        "current_price": _compact_number(
            _first_present(row, ("current_price", "price", "last_price"))
        ),
        "unrealized_pnl_pct": _compact_number(row.get("unrealized_pnl_pct")),
        "thesis": thesis,
    }
    return {
        key: value
        for key, value in fields.items()
        if value is not None and value != ""
    }


def _blocks_by_symbol(blocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in blocks:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            continue
        compact = _compact_block_context(row)
        if compact:
            grouped.setdefault(symbol, []).append(compact)
    return {symbol: rows[:4] for symbol, rows in grouped.items()}


def _compact_quote_context(row: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "price": _compact_number(
            _first_present(row, ("price", "current_price", "last_price", "close"))
        ),
        "change_pct": _compact_number(
            _first_present(row, ("change_pct", "pct_change", "change_rate"))
        ),
        "volume": _compact_number(row.get("volume")),
        "turnover_krw": _compact_number(row.get("turnover_krw")),
        "source": _compact_text(row.get("source"), chars=40),
        "fetched_at": _compact_text(
            _first_present(row, ("fetched_at", "as_of", "timestamp")),
            chars=60,
        ),
        "stale": row.get("stale") if isinstance(row.get("stale"), bool) else None,
        "error_message": _compact_text(row.get("error_message"), chars=120),
    }
    return {
        key: value
        for key, value in fields.items()
        if value is not None and value != ""
    }


def _quotes_by_symbol(quotes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in quotes:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            continue
        compact = _compact_quote_context(row)
        if compact:
            grouped[symbol] = compact
    return grouped


def _extend_packet_evidence(
    packet: dict[str, Any],
    key: str,
    values: list[str],
    *,
    limit: int,
    chars: int = 180,
) -> None:
    evidence = packet.get("evidence") if isinstance(packet.get("evidence"), dict) else {}
    existing = _compact_strings(evidence.get(key), limit=limit, chars=chars)
    additions = _compact_strings(values, limit=limit, chars=chars)
    evidence[key] = list(dict.fromkeys(existing + additions))[:limit]
    packet["evidence"] = evidence


def _format_price_context(value: Any, label: str) -> str:
    number = _compact_number(value)
    return f"{label}={number}" if number is not None else ""


def _attach_live_context(
    packet: dict[str, Any],
    *,
    owned: bool,
    blocks: list[dict[str, Any]],
    quote: dict[str, Any] | None,
) -> None:
    live_context: dict[str, Any] = (
        dict(packet.get("live_context"))
        if isinstance(packet.get("live_context"), dict)
        else {}
    )
    sources: list[str] = []
    checks: list[str] = []
    reasons: list[str] = []
    risks: list[str] = []

    if owned:
        sources.append("account_or_block")
    if blocks:
        live_context["blocks"] = blocks[:4]
        sources.append("block_state")
        first = blocks[0]
        price_bits = [
            _format_price_context(first.get("entry_price"), "진입가"),
            _format_price_context(first.get("current_price"), "블록현재가"),
            _format_price_context(first.get("target_price"), "목표가"),
            _format_price_context(first.get("stop_price"), "손절가"),
        ]
        checks.append(
            "블록상태 "
            + " ".join(
                bit
                for bit in [
                    f"id={first.get('block_id')}",
                    f"status={first.get('status')}",
                    f"horizon={first.get('horizon')}",
                    *[item for item in price_bits if item],
                ]
                if bit and not bit.endswith("=None")
            )
        )
        if first.get("thesis"):
            reasons.append("활성 블록 가설: " + str(first.get("thesis")))
    if quote:
        live_context["quote"] = quote
        sources.append("quote")
        price_bits = [
            _format_price_context(quote.get("price"), "현재가"),
            _format_price_context(quote.get("change_pct"), "등락률"),
        ]
        source = str(quote.get("source") or "").strip()
        checks.append(
            "현재시세 "
            + " ".join(
                bit
                for bit in [
                    *[item for item in price_bits if item],
                    f"source={source}" if source else "",
                ]
                if bit
            )
        )
        if quote.get("stale") or quote.get("error_message"):
            risks.append(
                "시세 최신성 점검: "
                + str(quote.get("error_message") or "stale quote")
            )

    if not live_context:
        return

    packet["live_context"] = live_context
    _extend_packet_evidence(packet, "sources", sources, limit=10, chars=80)
    _extend_packet_evidence(packet, "checks", checks, limit=5, chars=180)
    _extend_packet_evidence(packet, "reasons", reasons, limit=5, chars=180)
    _extend_packet_evidence(packet, "risks", risks, limit=4, chars=180)


def _candidate_asset_class(row: dict[str, Any]) -> str:
    asset_class = str(row.get("asset_class") or "").strip().lower()
    horizon = str(row.get("horizon_bias") or row.get("horizon") or "").strip().lower()
    name = str(row.get("name") or "")
    if asset_class == "etf" or horizon == "core_etf" or "ETF" in name.upper():
        return "etf"
    return "equity"


def _candidate_bucket(row: dict[str, Any]) -> str:
    asset_class = _candidate_asset_class(row)
    horizon = str(row.get("horizon_bias") or row.get("horizon") or "").strip().lower()
    if asset_class == "etf":
        return "core_etf" if horizon == "core_etf" else "sector_etf"
    score = _safe_int(row.get("score"))
    return "large_cap_equity" if score >= 65 else "mid_small_equity"


def _candidate_quality(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").strip()
    identity = row.get("identity_status") if isinstance(row.get("identity_status"), dict) else {}
    identity_status = str(identity.get("status") or "ok").strip().lower()
    warnings = _compact_strings(row.get("data_warnings"), limit=5, chars=120)
    label = str(identity.get("label") or "").strip()
    if identity_status not in {"ok", "clean"} and label and label not in warnings:
        warnings.insert(0, label[:120])
    if not _clean_name(row.get("name"), symbol=symbol):
        if "종목명 검증 필요" not in warnings:
            warnings.insert(0, "종목명 검증 필요")
        identity_status = "warning"

    valuation = row.get("valuation") if isinstance(row.get("valuation"), dict) else {}
    valuation_status = str(valuation.get("status") or "").strip().lower()
    if valuation_status in {"missing", "error", "stale", "unknown"}:
        warning = "밸류 미수집" if valuation_status in {"missing", "unknown"} else "밸류 최신성/수집 오류"
        if warning not in warnings:
            warnings.append(warning)

    source_count = len({str(item) for item in _normalize_list(row.get("sources")) if str(item)})
    score = _safe_int(row.get("score"))
    confidence = _safe_int(row.get("confidence"))
    identity_confidence = 0.95 if identity_status in {"ok", "clean"} else 0.45
    if source_count <= 1 and _candidate_asset_class(row) != "etf":
        warnings.append("소스 1개")
    evidence_strength = max(score, min(100, confidence + source_count * 6))
    decision_use = "core"
    if identity_confidence < 0.7:
        decision_use = "caution"
    elif source_count <= 1 and score < 80:
        decision_use = "supporting"
    return {
        "identity_status": identity_status or "ok",
        "identity_confidence": round(identity_confidence, 2),
        "source_count": source_count,
        "evidence_strength": max(0, min(100, evidence_strength)),
        "valuation_status": valuation_status or "not_provided",
        "decision_use": decision_use,
        "warnings": list(dict.fromkeys(warnings))[:6],
    }


def _packet_from_candidate(
    row: dict[str, Any],
    *,
    names: dict[str, str],
    extra_buckets: list[str] | None = None,
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").strip()
    name = (
        names.get(symbol)
        or _clean_name(row.get("name"), symbol=symbol)
        or symbol
    )
    sources = _compact_strings(row.get("sources"), limit=8, chars=80)
    buckets = [_candidate_bucket(row)]
    if "after_close_330" in sources:
        buckets.append("after_close_330")
    if "whale_insight" in sources:
        buckets.append("whale_insight")
    if "pre_surge_discovery" in sources:
        buckets.append("pre_surge")
    for bucket in extra_buckets or []:
        if bucket and bucket not in buckets:
            buckets.append(bucket)
    quality = _candidate_quality({**row, "name": name})
    return {
        "symbol": symbol,
        "name": name,
        "asset_class": _candidate_asset_class(row),
        "buckets": buckets,
        "horizon_bias": str(row.get("horizon_bias") or row.get("horizon") or ""),
        "score": _safe_int(row.get("score")),
        "confidence": _safe_int(row.get("confidence")),
        "suitability": row.get("suitability") if isinstance(row.get("suitability"), dict) else {},
        "quality": quality,
        "evidence": {
            "sources": sources,
            "reasons": _compact_strings(row.get("reasons"), limit=4),
            "risks": _compact_strings(row.get("risks"), limit=3),
            "checks": _compact_strings(row.get("checks"), limit=3),
        },
        "pre_surge": row.get("pre_surge") if isinstance(row.get("pre_surge"), dict) else {},
        "valuation": row.get("valuation") if isinstance(row.get("valuation"), dict) else {},
    }


def _candidate_rows(strategy_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _normalize_list(strategy_payload.get("candidates"))
        if isinstance(row, dict) and _is_symbol(row.get("symbol"))
    ]
    return sorted(
        rows,
        key=lambda row: (_safe_int(row.get("score")), _safe_int(row.get("confidence"))),
        reverse=True,
    )


def select_balanced_research_symbols(
    strategy_payload: dict[str, Any],
    *,
    existing_symbols: list[str],
    limit: int,
) -> list[str]:
    max_items = max(int(limit), 1)
    selected: list[str] = []

    def add(symbol: Any) -> None:
        text = str(symbol or "").strip()
        if _is_symbol(text) and text not in selected and len(selected) < max_items:
            selected.append(text)

    for symbol in existing_symbols:
        add(symbol)

    rows = _candidate_rows(strategy_payload)
    if rows:
        add(rows[0].get("symbol"))

    etfs = [row for row in rows if _candidate_asset_class(row) == "etf"]
    equities = [row for row in rows if _candidate_asset_class(row) != "etf"]
    equity_quota = 0
    if equities and etfs and max_items >= 3:
        equity_quota = min(len(equities), max(1, max_items // 2))
    elif equities:
        equity_quota = len(equities)

    for row in equities[:equity_quota]:
        add(row.get("symbol"))
    for row in etfs:
        add(row.get("symbol"))
    for row in equities[equity_quota:]:
        add(row.get("symbol"))
    return selected[:max_items]


def _packet_sources(packet: dict[str, Any]) -> set[str]:
    evidence = packet.get("evidence") if isinstance(packet.get("evidence"), dict) else {}
    return {str(row) for row in _normalize_list(evidence.get("sources")) if str(row)}


def _balanced_packets(
    packets: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    max_items = max(int(limit), 1)
    ordered = sorted(
        packets,
        key=lambda row: (
            1 if "owned_symbols" in row.get("buckets", []) else 0,
            _safe_int(row.get("score")),
            _safe_int(row.get("confidence")),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    selected_symbols: set[str] = set()
    etf_cap = max(3, max_items // 2)

    def add(packet: dict[str, Any]) -> bool:
        symbol = str(packet.get("symbol") or "")
        if not _is_symbol(symbol) or symbol in selected_symbols:
            return False
        if len(selected) >= max_items:
            return False
        if (
            _candidate_asset_class(packet) == "etf"
            and sum(1 for row in selected if _candidate_asset_class(row) == "etf") >= etf_cap
        ):
            return False
        selected.append(packet)
        selected_symbols.add(symbol)
        return True

    for packet in ordered:
        if "owned_symbols" in packet.get("buckets", []):
            add(packet)

    groups = [
        [row for row in ordered if "pre_surge" in row.get("buckets", [])],
        [row for row in ordered if "after_close_330" in _packet_sources(row)],
        [row for row in ordered if "whale_insight" in _packet_sources(row)],
        [row for row in ordered if "daily_discovery" in row.get("buckets", [])],
        [row for row in ordered if _candidate_asset_class(row) != "etf"],
        [row for row in ordered if _candidate_asset_class(row) == "etf"],
    ]
    cursors = [0 for _ in groups]
    while len(selected) < max_items:
        added = False
        for index, group in enumerate(groups):
            while cursors[index] < len(group):
                packet = group[cursors[index]]
                cursors[index] += 1
                if add(packet):
                    added = True
                    break
            if len(selected) >= max_items:
                break
        if not added:
            break

    for packet in ordered:
        if len(selected) >= max_items:
            break
        add(packet)
    return selected[:max_items]


def _bucket_item(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": packet.get("symbol"),
        "name": packet.get("name"),
        "score": packet.get("score"),
        "confidence": packet.get("confidence"),
        "quality": packet.get("quality"),
    }


def _daily_discovery_packets(
    daily_discovery: dict[str, Any] | None,
    *,
    names: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(daily_discovery, dict):
        return []
    packets: list[dict[str, Any]] = []
    for row in _normalize_list(daily_discovery.get("items")):
        if not isinstance(row, dict) or not _is_symbol(row.get("symbol")):
            continue
        row = enrich_discovery_result(row)
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        pre_surge = row.get("pre_surge") if isinstance(row.get("pre_surge"), dict) else {}
        sources = ["daily_discovery"]
        extra_buckets = ["daily_discovery"]
        reasons = analysis.get("reasons") or [analysis.get("summary")]
        checks = []
        if pre_surge.get("is_candidate"):
            sources.append("pre_surge_discovery")
            extra_buckets.append("pre_surge")
            reasons = list(pre_surge.get("reasons") or []) + list(_normalize_list(reasons))
            checks = [
                "pre_surge entry bias: "
                + str(pre_surge.get("entry_bias") or "scout_or_waiting_block"),
                "preferred horizon: " + str(pre_surge.get("preferred_horizon") or "mid"),
            ]
        candidate = {
            "symbol": row.get("symbol"),
            "name": row.get("name") or analysis.get("name"),
            "asset_class": "equity",
            "score": pre_surge.get("score") if pre_surge.get("is_candidate") else row.get("score"),
            "confidence": analysis.get("confidence") or row.get("confidence") or 50,
            "sources": sources,
            "reasons": reasons,
            "risks": analysis.get("risks"),
            "checks": checks,
            "pre_surge": pre_surge,
            "identity_status": {"status": "ok"},
        }
        packets.append(
            _packet_from_candidate(
                candidate,
                names=names,
                extra_buckets=extra_buckets,
            )
        )
    return packets


def _etf_research_packets(
    etf_research: dict[str, Any] | None,
    *,
    names: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(etf_research, dict):
        return []
    packets: list[dict[str, Any]] = []
    for row in _normalize_list(etf_research.get("items")):
        if not isinstance(row, dict) or not _is_symbol(row.get("symbol")):
            continue
        snapshot = (
            row.get("snapshot")
            if isinstance(row.get("snapshot"), dict)
            else row.get("latest_snapshot")
            if isinstance(row.get("latest_snapshot"), dict)
            else {}
        )
        score = (
            row.get("score")
            if isinstance(row.get("score"), dict)
            else row.get("latest_score")
            if isinstance(row.get("latest_score"), dict)
            else {}
        )
        status = str(snapshot.get("status") or score.get("status") or "ok").strip().lower()
        if status in {"error", "missing"}:
            continue
        label = str(score.get("label") or "").strip().lower()
        liquidity = _safe_int(score.get("liquidity_score"))
        momentum = _safe_int(score.get("momentum_score"))
        core_fit = _safe_int(score.get("core_fit_score"))
        risk = _safe_int(score.get("risk_score"))
        candidate_score = max(liquidity, momentum, core_fit, _safe_int(row.get("score")))
        confidence = max(45, min(100, (liquidity + core_fit + max(momentum, 0)) // 3))
        horizon_bias = "core_etf" if label == "core_fit" or core_fit >= 70 else "sector_etf"
        checks = [
            f"ETF label: {label or 'unknown'}",
            f"liquidity={liquidity} momentum={momentum} core_fit={core_fit} risk={risk}",
        ]
        if snapshot:
            checks.append(
                "snapshot status="
                + status
                + " change_pct="
                + str(snapshot.get("change_pct") or "")
                + " turnover_krw="
                + str(snapshot.get("turnover_krw") or "")
            )
        candidate = {
            "symbol": row.get("symbol"),
            "name": row.get("name") or snapshot.get("name") or score.get("name"),
            "asset_class": "etf",
            "horizon_bias": horizon_bias,
            "score": candidate_score,
            "confidence": confidence,
            "sources": ["etf_research"],
            "reasons": score.get("reasons"),
            "risks": score.get("risks"),
            "checks": checks,
            "identity_status": {"status": "ok"},
        }
        packets.append(_packet_from_candidate(candidate, names=names))
    return packets


def _symbol_analysis_memory_packets(
    investment_memory: dict[str, Any] | None,
    *,
    names: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(investment_memory, dict):
        return []
    analyses = (
        investment_memory.get("symbol_analyses")
        if isinstance(investment_memory.get("symbol_analyses"), dict)
        else {}
    )
    packets: list[dict[str, Any]] = []
    for symbol, rows in analyses.items():
        if not _is_symbol(symbol):
            continue
        first = next((row for row in _normalize_list(rows) if isinstance(row, dict)), None)
        if not first:
            continue
        confidence = _safe_float(first.get("confidence"))
        if 0.0 < confidence <= 1.0:
            confidence_score = int(confidence * 100)
        else:
            confidence_score = _safe_int(first.get("confidence"), default=55)
        candidate = {
            "symbol": symbol,
            "name": names.get(symbol) or first.get("name") or symbol,
            "asset_class": "equity",
            "horizon_bias": first.get("horizon") or first.get("stance") or "",
            "score": max(55, min(85, confidence_score)),
            "confidence": max(45, min(100, confidence_score)),
            "sources": ["symbol_analysis_memory"],
            "reasons": [first.get("summary")],
            "risks": first.get("risks"),
            "checks": [
                "data gap: " + str(item)
                for item in _compact_strings(first.get("data_gaps"), limit=3, chars=140)
            ],
            "identity_status": {"status": "ok"},
        }
        packets.append(
            _packet_from_candidate(
                candidate,
                names=names,
                extra_buckets=["symbol_memory"],
            )
        )
    return packets


def _market_judgment_packets(
    market_judgment: dict[str, Any] | None,
    *,
    names: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(market_judgment, dict):
        return []
    packets: list[dict[str, Any]] = []
    for row in _normalize_list(market_judgment.get("judgments")):
        if not isinstance(row, dict) or not _is_symbol(row.get("symbol")):
            continue
        symbol = str(row.get("symbol") or "").strip()
        confidence = _safe_float(row.get("confidence"))
        confidence_score = int(confidence * 100) if 0.0 < confidence <= 1.0 else _safe_int(row.get("confidence"), default=55)
        checks = [
            *[
                "trigger: " + item
                for item in _compact_strings(row.get("triggers"), limit=3, chars=140)
            ],
            *[
                "data gap: " + item
                for item in _compact_strings(row.get("data_gaps"), limit=3, chars=140)
            ],
        ]
        stance = str(row.get("stance") or "").strip()
        action = str(row.get("account_action") or "").strip()
        if stance or action:
            checks.append(f"stance={stance or '-'} account_action={action or '-'}")
        candidate = {
            "symbol": symbol,
            "name": names.get(symbol) or row.get("name") or symbol,
            "asset_class": "equity",
            "horizon_bias": row.get("horizon") or "",
            "score": max(50, min(90, confidence_score)),
            "confidence": max(45, min(100, confidence_score)),
            "sources": ["market_judgment"],
            "reasons": row.get("reasons"),
            "risks": row.get("risks"),
            "checks": checks,
            "identity_status": {"status": "ok"},
        }
        packets.append(
            _packet_from_candidate(
                candidate,
                names=names,
                extra_buckets=["market_judgment"],
            )
        )
    return packets


def _merge_packet(existing: dict[str, Any], packet: dict[str, Any]) -> None:
    for bucket in packet.get("buckets") or []:
        if bucket not in existing["buckets"]:
            existing["buckets"].append(bucket)
    packet_evidence = packet.get("evidence") if isinstance(packet.get("evidence"), dict) else {}
    existing_evidence = (
        existing.get("evidence") if isinstance(existing.get("evidence"), dict) else {}
    )
    existing["evidence"]["sources"] = list(
        dict.fromkeys(
            existing_evidence.get("sources", [])
            + _compact_strings(packet_evidence.get("sources"), limit=8, chars=80)
        )
    )
    existing["evidence"]["reasons"] = list(
        dict.fromkeys(
            _compact_strings(existing_evidence.get("reasons"), limit=4)
            + _compact_strings(packet_evidence.get("reasons"), limit=4)
        )
    )[:4]
    existing["evidence"]["risks"] = list(
        dict.fromkeys(
            _compact_strings(existing_evidence.get("risks"), limit=3)
            + _compact_strings(packet_evidence.get("risks"), limit=3)
        )
    )[:3]
    existing["evidence"]["checks"] = list(
        dict.fromkeys(
            _compact_strings(existing_evidence.get("checks"), limit=3)
            + _compact_strings(packet_evidence.get("checks"), limit=3)
        )
    )[:3]
    if packet.get("pre_surge"):
        existing["pre_surge"] = packet.get("pre_surge")


def build_research_spine(
    *,
    strategy_payload: dict[str, Any],
    daily_discovery: dict[str, Any] | None,
    market_judgment: dict[str, Any] | None,
    etf_research: dict[str, Any] | None,
    investment_memory: dict[str, Any] | None,
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    max_packets: int = 12,
) -> dict[str, Any]:
    account_payload = account if isinstance(account, dict) else {}
    position_names = _position_names(account_payload)
    block_names = _block_names(blocks)
    quote_names = _quote_names(quotes)
    owned_symbols = _position_symbols(account_payload) | _block_symbols(blocks)
    block_context_by_symbol = _blocks_by_symbol(blocks)
    quote_context_by_symbol = _quotes_by_symbol(quotes)
    names = {}
    names.update(position_names)
    names.update(block_names)
    names.update(quote_names)

    packets_by_symbol: dict[str, dict[str, Any]] = {}
    for row in _candidate_rows(strategy_payload if isinstance(strategy_payload, dict) else {}):
        symbol = str(row.get("symbol") or "").strip()
        packet = _packet_from_candidate(
            row,
            names=names,
            extra_buckets=["owned_symbols"] if symbol in owned_symbols else [],
        )
        packets_by_symbol[str(packet["symbol"])] = packet
    for packet in _market_judgment_packets(market_judgment, names=names):
        existing = packets_by_symbol.get(str(packet["symbol"]))
        if existing:
            _merge_packet(existing, packet)
        else:
            packets_by_symbol[str(packet["symbol"])] = packet
    for packet in _etf_research_packets(etf_research, names=names):
        existing = packets_by_symbol.get(str(packet["symbol"]))
        if existing:
            _merge_packet(existing, packet)
        else:
            packets_by_symbol[str(packet["symbol"])] = packet
    for packet in _symbol_analysis_memory_packets(investment_memory, names=names):
        existing = packets_by_symbol.get(str(packet["symbol"]))
        if existing:
            _merge_packet(existing, packet)
        else:
            packets_by_symbol[str(packet["symbol"])] = packet
    for packet in _daily_discovery_packets(daily_discovery, names=names):
        existing = packets_by_symbol.get(str(packet["symbol"]))
        if existing:
            _merge_packet(existing, packet)
        else:
            packets_by_symbol[str(packet["symbol"])] = packet

    for symbol, name in names.items():
        if symbol not in packets_by_symbol:
            is_owned = symbol in owned_symbols
            packets_by_symbol[symbol] = _packet_from_candidate(
                {
                    "symbol": symbol,
                    "name": name,
                    "asset_class": "equity",
                    "score": 50,
                    "confidence": 45,
                    "sources": ["account_or_block"] if is_owned else ["quote"],
                    "reasons": [
                        "현재 계좌/블록에 존재하므로 우선 점검 대상"
                        if is_owned
                        else "현재 시세 수집 대상이므로 보조 점검 대상"
                    ],
                    "identity_status": {"status": "ok"},
                },
                names=names,
                extra_buckets=["owned_symbols"] if is_owned else [],
            )
        elif (
            symbol in owned_symbols
            and "owned_symbols" not in packets_by_symbol[symbol]["buckets"]
        ):
            packets_by_symbol[symbol]["buckets"].append("owned_symbols")

    for symbol, packet in packets_by_symbol.items():
        _attach_live_context(
            packet,
            owned=symbol in owned_symbols,
            blocks=block_context_by_symbol.get(symbol, []),
            quote=quote_context_by_symbol.get(symbol),
        )

    packets = _balanced_packets(
        list(packets_by_symbol.values()),
        limit=max(int(max_packets), 1),
    )
    buckets: dict[str, list[dict[str, Any]]] = {
        "owned_symbols": [],
        "core_etf": [],
        "sector_etf": [],
        "large_cap_equity": [],
        "mid_small_equity": [],
        "daily_discovery": [],
        "pre_surge": [],
        "symbol_memory": [],
        "market_judgment": [],
        "after_close_330": [],
        "whale_insight": [],
    }
    for packet in packets:
        for bucket in packet.get("buckets") or []:
            if bucket in buckets:
                buckets[bucket].append(_bucket_item(packet))
    identity_warning_count = sum(
        1 for row in packets if _safe_float((row.get("quality") or {}).get("identity_confidence")) < 0.7
    )
    valuation_missing_count = sum(
        1
        for row in packets
        if str((row.get("quality") or {}).get("valuation_status") or "")
        in {"missing", "unknown", "not_provided", "error", "stale"}
        and str(row.get("asset_class") or "") != "etf"
    )
    low_evidence_count = sum(
        1 for row in packets if _safe_int((row.get("quality") or {}).get("evidence_strength")) < 55
    )
    pre_surge_count = sum(1 for row in packets if "pre_surge" in row.get("buckets", []))
    market_status = (
        str((market_judgment or {}).get("status") or "")
        if isinstance(market_judgment, dict)
        else "missing"
    ) or "missing"
    return {
        "status": "ok",
        "version": "research_spine_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "quality_summary": {
            "packet_count": len(packets),
            "identity_warning_count": identity_warning_count,
            "valuation_missing_count": valuation_missing_count,
            "low_evidence_count": low_evidence_count,
            "pre_surge_count": pre_surge_count,
            "market_judgment_status": market_status,
            "etf_research_status": str((etf_research or {}).get("status") or "missing")
            if isinstance(etf_research, dict)
            else "missing",
            "memory_status": str((investment_memory or {}).get("status") or "missing")
            if isinstance(investment_memory, dict)
            else "missing",
        },
        "buckets": {key: rows[:6] for key, rows in buckets.items()},
        "packets": packets,
        "contract": {
            "primary_use": "Use packets before raw research. Treat caution packets as hypothesis inputs requiring validation.",
            "block_requirements": [
                "symbol",
                "horizon",
                "thesis",
                "entry_logic",
                "entry_price_structure",
                "target_price_structure",
                "stop_price_structure",
                "invalidating_conditions",
                "data_support",
                "data_gaps",
            ],
        },
    }
