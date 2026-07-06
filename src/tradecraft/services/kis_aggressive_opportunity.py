from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Any

from tradecraft.services.daily_discovery import enrich_discovery_result


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan", "None"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _symbol(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{6}", text) else ""


def _clean_text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[: max(int(limit), 1)]


def _clean_name(value: Any, *, symbol: str = "") -> str:
    text = _clean_text(value, limit=80)
    if not text or text == symbol or _symbol(text):
        return ""
    if text in {"정보", "투자", "리포트", "리서치"}:
        return ""
    return text


def _normalize_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _raw(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("raw")
    return value if isinstance(value, dict) else {}


def _quote_change_pct(row: dict[str, Any]) -> float:
    raw = _raw(row)
    return (
        _safe_float(row.get("change_pct"))
        or _safe_float(raw.get("prdy_ctrt"))
        or _safe_float(raw.get("prdy_vrss_rate"))
    )


def _quote_trading_value(row: dict[str, Any]) -> float:
    raw = _raw(row)
    return (
        _safe_float(row.get("trading_value"))
        or _safe_float(raw.get("acml_tr_pbmn"))
        or _safe_float(raw.get("tr_pbmn"))
    )


def _quote_upper_limit_price(row: dict[str, Any]) -> float:
    raw = _raw(row)
    return _safe_float(raw.get("stck_mxpr") or raw.get("upper_limit_price"))


def _quote_price(row: dict[str, Any]) -> float:
    raw = _raw(row)
    return _safe_float(row.get("price") or raw.get("stck_prpr"))


def _add_unique(rows: list[str], value: str, *, limit: int = 8) -> None:
    text = _clean_text(value, limit=180)
    if text and text not in rows and len(rows) < limit:
        rows.append(text)


def _candidate(rows: dict[str, dict[str, Any]], symbol: str, name: str = "") -> dict[str, Any]:
    row = rows.setdefault(
        symbol,
        {
            "symbol": symbol,
            "name": name or symbol,
            "score": 0.0,
            "signals": [],
            "sources": [],
            "reasons": [],
            "risks": [],
            "metrics": {},
        },
    )
    clean_name = _clean_name(name, symbol=symbol)
    if clean_name and (not row.get("name") or row.get("name") == symbol):
        row["name"] = clean_name
    return row


def _add_source(row: dict[str, Any], source: str) -> None:
    _add_unique(row["sources"], source, limit=12)


def _add_signal(row: dict[str, Any], signal: str) -> None:
    _add_unique(row["signals"], signal, limit=12)


def _score_quote(row: dict[str, Any]) -> tuple[float, list[str], dict[str, Any]]:
    price = _quote_price(row)
    upper = _quote_upper_limit_price(row)
    change_pct = _quote_change_pct(row)
    trading_value = _quote_trading_value(row)
    score = 0.0
    signals: list[str] = []
    metrics: dict[str, Any] = {
        "price": price or None,
        "upper_limit_price": upper or None,
        "change_pct": round(change_pct, 2),
        "trading_value_krw": trading_value or None,
    }
    if upper > 0 and price > 0:
        distance_pct = max(((upper - price) / upper) * 100.0, 0.0)
        metrics["limit_up_distance_pct"] = round(distance_pct, 2)
        if distance_pct <= 3.0:
            score += 36.0
            signals.append("limit_up_proximity")
        elif distance_pct <= 8.0:
            score += 20.0
            signals.append("near_limit_up_watch")
    if change_pct >= 15:
        score += 24.0
        signals.append("strong_intraday_momentum")
    elif change_pct >= 7:
        score += 14.0
        signals.append("positive_momentum")
    if trading_value >= 20_000_000_000:
        score += 18.0
        signals.append("large_trading_value")
    elif trading_value >= 3_000_000_000:
        score += 10.0
        signals.append("active_trading_value")
    return score, signals, metrics


def _iter_daily_discovery_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("pre_surge_candidates", "block_candidates", "items"):
        for row in _normalize_list(value.get(key)):
            if isinstance(row, dict):
                rows.append({**row, "_daily_bucket": key})
    return rows


def _iter_research_spine_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("packets", "symbols", "candidates"):
        for row in _normalize_list(value.get(key)):
            if isinstance(row, dict):
                rows.append({**row, "_research_bucket": key})
    buckets = value.get("buckets")
    if isinstance(buckets, dict):
        for bucket_name, bucket_rows in buckets.items():
            for row in _normalize_list(bucket_rows):
                if isinstance(row, dict):
                    rows.append({**row, "_research_bucket": str(bucket_name)})
    return rows


def _iter_strategy_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _normalize_list(value.get("candidates")) if isinstance(row, dict)]


def _add_daily_discovery(rows: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    for item in _iter_daily_discovery_rows(payload):
        item = enrich_discovery_result(item)
        symbol = _symbol(item.get("symbol"))
        if not symbol:
            continue
        row = _candidate(rows, symbol, str(item.get("name") or ""))
        _add_source(row, "daily_discovery")
        bucket = str(item.get("_daily_bucket") or "")
        if bucket == "pre_surge_candidates":
            _add_signal(row, "pre_surge")
            row["score"] += 28.0
        elif bucket == "block_candidates":
            _add_signal(row, "daily_block_candidate")
            row["score"] += 18.0
        pre_surge = item.get("pre_surge") if isinstance(item.get("pre_surge"), dict) else {}
        pre_surge_score = _safe_float(pre_surge.get("score"))
        if pre_surge.get("is_candidate"):
            _add_signal(row, "pre_surge")
        if pre_surge_score:
            row["score"] += min(pre_surge_score * 0.25, 25.0)
            row["metrics"]["pre_surge_score"] = round(pre_surge_score, 2)
        for reason in _normalize_list(pre_surge.get("reasons")):
            _add_unique(row["reasons"], f"pre-surge: {reason}")
        analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
        for reason in _normalize_list(analysis.get("reasons"))[:3]:
            _add_unique(row["reasons"], reason)
        for risk in _normalize_list(analysis.get("risks"))[:2]:
            _add_unique(row["risks"], risk)


def _add_research_spine(rows: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    for item in _iter_research_spine_rows(payload):
        symbol = _symbol(item.get("symbol"))
        if not symbol:
            continue
        row = _candidate(rows, symbol, str(item.get("name") or ""))
        _add_source(row, "research_spine")
        score = _safe_float(item.get("score") or item.get("confidence"))
        row["score"] += min(score * 0.28, 24.0) if score else 8.0
        bucket = str(item.get("_research_bucket") or "")
        if bucket:
            _add_signal(row, f"research_{bucket}")
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        for reason in [
            *_normalize_list(item.get("reasons"))[:3],
            *_normalize_list(evidence.get("reasons"))[:3],
        ]:
            _add_unique(row["reasons"], reason)


def _add_strategy(rows: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    for item in _iter_strategy_rows(payload):
        symbol = _symbol(item.get("symbol"))
        if not symbol:
            continue
        row = _candidate(rows, symbol, str(item.get("name") or ""))
        _add_source(row, "strategy")
        score = _safe_float(item.get("score"))
        confidence = _safe_float(item.get("confidence"))
        row["score"] += min(score * 0.22 + confidence * 0.08, 30.0)
        _add_signal(row, "strategy_candidate")
        for reason in _normalize_list(item.get("reasons"))[:4]:
            _add_unique(row["reasons"], reason)
        for risk in _normalize_list(item.get("risks"))[:2]:
            _add_unique(row["risks"], risk)


def _add_quotes(rows: dict[str, dict[str, Any]], quotes: list[dict[str, Any]]) -> None:
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        symbol = _symbol(quote.get("symbol"))
        if not symbol:
            continue
        row = _candidate(rows, symbol, str(quote.get("name") or ""))
        _add_source(row, "quote")
        score, signals, metrics = _score_quote(quote)
        row["score"] += score
        row["metrics"].update({k: v for k, v in metrics.items() if v not in (None, 0, 0.0)})
        for signal in signals:
            _add_signal(row, signal)
        if score:
            _add_unique(
                row["reasons"],
                (
                    f"시세 신호: 등락률 {metrics.get('change_pct', 0)}%, "
                    f"상한가 거리 {metrics.get('limit_up_distance_pct', '-') }%"
                ),
            )


def _apply_market_pulse(rows: dict[str, dict[str, Any]], market_pulse: dict[str, Any]) -> None:
    text = " ".join(
        str(market_pulse.get(key) or "")
        for key in ("regime", "status", "summary", "stance")
    ).lower()
    if not rows:
        return
    if "risk_on" in text or "risk-on" in text or "강세" in text:
        for row in rows.values():
            row["score"] += 4.0
            _add_signal(row, "market_pulse_risk_on")
    elif "risk_off" in text or "risk-off" in text or "약세" in text:
        for row in rows.values():
            row["score"] -= 4.0
            _add_signal(row, "market_pulse_risk_off")


def _finalize(row: dict[str, Any]) -> dict[str, Any]:
    score = max(float(row.get("score") or 0.0), 0.0)
    signals = list(row.get("signals") or [])
    sources = list(row.get("sources") or [])
    if len(sources) >= 3:
        score += 6.0
        _add_signal(row, "multi_source_confirmation")
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "aggressive_score": round(min(score, 100.0), 2),
        "source_count": len(set(sources)),
        "sources": list(dict.fromkeys(sources))[:12],
        "signals": list(dict.fromkeys(signals))[:12],
        "reasons": list(dict.fromkeys(row.get("reasons") or []))[:8],
        "risks": list(dict.fromkeys(row.get("risks") or []))[:5],
        "metrics": dict(row.get("metrics") or {}),
        "preferred_action": (
            "scout_or_waiting_block"
            if score >= 50
            else "watch_and_collect_more"
        ),
    }


def build_aggressive_opportunity_packet(
    *,
    quotes: list[dict[str, Any]],
    daily_discovery: dict[str, Any] | None,
    research_spine: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
    fundamentals_status: dict[str, Any] | None,
    market_pulse: dict[str, Any] | None,
    limit: int = 24,
    generated_at: str = "",
) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    _add_quotes(rows, quotes)
    _add_daily_discovery(rows, daily_discovery if isinstance(daily_discovery, dict) else {})
    _add_research_spine(rows, research_spine if isinstance(research_spine, dict) else {})
    _add_strategy(rows, strategy if isinstance(strategy, dict) else {})
    _apply_market_pulse(rows, market_pulse if isinstance(market_pulse, dict) else {})

    candidates = [_finalize(row) for row in rows.values()]
    candidates.sort(
        key=lambda row: (
            -float(row.get("aggressive_score") or 0.0),
            -int(row.get("source_count") or 0),
            str(row.get("symbol") or ""),
        )
    )
    max_rows = max(int(limit), 1)
    selected = candidates[:max_rows]
    return {
        "version": "kis_aggressive_opportunity_v1",
        "status": "ok" if selected else "empty",
        "generated_at": generated_at or _utc_now_iso(),
        "candidate_count": len(candidates),
        "returned_count": len(selected),
        "candidates": selected,
        "coverage": {
            "quote_count": len([row for row in quotes if isinstance(row, dict)]),
            "daily_discovery_status": str(
                (daily_discovery or {}).get("status")
                if isinstance(daily_discovery, dict)
                else "missing"
            ),
            "research_spine_status": str(
                (research_spine or {}).get("status")
                if isinstance(research_spine, dict)
                else "missing"
            ),
            "strategy_status": str(
                (strategy or {}).get("status")
                if isinstance(strategy, dict)
                else "missing"
            ),
            "fundamentals_status": str(
                (fundamentals_status or {}).get("status")
                if isinstance(fundamentals_status, dict)
                else "missing"
            ),
            "market_pulse_status": str(
                (market_pulse or {}).get("status")
                if isinstance(market_pulse, dict)
                else "missing"
            ),
        },
        "operator_note": (
            "쥬가 상한가 근접, 급등 전 후보, 리서치 근거, 전략 후보를 한 번에 "
            "보도록 압축한 공격 기회 패킷입니다."
        ),
    }
