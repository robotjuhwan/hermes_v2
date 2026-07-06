from __future__ import annotations

import re
from typing import Any


_TIMESTAMP_KEYS = (
    "generated_at",
    "last_scan_at",
    "updated_at",
    "captured_at",
    "scored_at",
    "published_at",
    "crawled_at",
)


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


def _name(row: dict[str, Any], symbol: str) -> str:
    return str(
        row.get("name")
        or row.get("company_name")
        or row.get("asset_name")
        or row.get("stock_name")
        or symbol
    ).strip()


def _market(row: dict[str, Any]) -> str:
    return str(row.get("market") or row.get("category") or "").strip()


def _append_unique(rows: list[str], value: str) -> None:
    if value and value not in rows:
        rows.append(value)


def _metric(row: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            nested = _metric(value, keys)
            if nested:
                return nested
            continue
        score = _safe_float(value)
        if score:
            return score
    return 0.0


def _latest_timestamp(rows: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in _TIMESTAMP_KEYS:
            value = str(row.get(key) or "").strip()
            if value:
                values.append(value)
    return max(values) if values else ""


def _base_row(row: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": _name(row, symbol),
        "market": _market(row),
        "score": 0.0,
        "opportunity_score": 0.0,
        "sources": [],
        "source_scores": {},
        "reasons": [],
    }


def _ensure_candidate(
    rows: dict[str, dict[str, Any]],
    row: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any] | None:
    symbol = _symbol(row.get("symbol") or row.get("asset"))
    if not symbol:
        return None
    target = rows.setdefault(symbol, _base_row(row, symbol))
    if not target.get("name") or str(target.get("name")) == symbol:
        target["name"] = _name(row, symbol)
    if not target.get("market"):
        target["market"] = _market(row)
    _append_unique(target["sources"], source)
    return target


def _add_score(
    rows: dict[str, dict[str, Any]],
    source_rows: list[dict[str, Any]],
    *,
    source: str,
    metric_keys: tuple[str, ...],
    weight: float,
    reason: str,
) -> None:
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        target = _ensure_candidate(rows, row, source=source)
        if target is None:
            continue
        raw_score = _metric(row, metric_keys)
        contribution = raw_score * weight
        target["score"] = float(target.get("score") or 0.0) + contribution
        target["source_scores"][source] = round(
            float(target["source_scores"].get(source) or 0.0) + contribution,
            4,
        )
        if raw_score:
            target["reasons"].append(reason.format(score=round(raw_score, 2)))


def _compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    score = round(float(row.get("score") or 0.0), 4)
    sources = list(row.get("sources") or [])
    reasons = list(dict.fromkeys(str(item) for item in row.get("reasons") or [] if item))
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "market": str(row.get("market") or ""),
        "score": score,
        "opportunity_score": score,
        "sources": sources,
        "source_scores": dict(row.get("source_scores") or {}),
        "reasons": reasons[:5],
    }


def rank_opportunities(
    *,
    symbols: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    insights: list[dict[str, Any]],
    fundamentals: list[dict[str, Any]],
    etfs: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    recent_blocks: list[dict[str, Any]] | None = None,
    limit: int = 60,
    generated_at: str = "",
) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for row in symbols:
        if not isinstance(row, dict):
            continue
        target = _ensure_candidate(rows, row, source="symbol_directory")
        if target is not None:
            target["reasons"].append("listed in symbol directory")

    _add_score(
        rows,
        reports,
        source="reports",
        metric_keys=("score", "confidence", "target_upside_pct"),
        weight=1.0,
        reason="report score {score}",
    )
    _add_score(
        rows,
        insights,
        source="strategy_insights",
        metric_keys=("strength", "score", "confidence"),
        weight=0.5,
        reason="strategy strength {score}",
    )
    _add_score(
        rows,
        fundamentals,
        source="fundamentals",
        metric_keys=(
            "valuation_score",
            "undervalued_score",
            "quality_score",
            "growth_score",
            "score",
        ),
        weight=0.4,
        reason="fundamental score {score}",
    )
    _add_score(
        rows,
        etfs,
        source="etf_research",
        metric_keys=(
            "score",
            "momentum_score",
            "core_fit_score",
            "liquidity_score",
        ),
        weight=0.6,
        reason="ETF research score {score}",
    )
    _add_score(
        rows,
        positions,
        source="account_position",
        metric_keys=("value_krw", "market_value_krw", "eval_amount_krw"),
        weight=0.0001,
        reason="account position value {score}",
    )
    _add_score(
        rows,
        list(recent_blocks or []),
        source="recent_blocks",
        metric_keys=("pnl_pct", "score", "confidence"),
        weight=0.25,
        reason="recent block signal {score}",
    )

    candidates = [_compact_candidate(row) for row in rows.values()]
    candidates.sort(
        key=lambda row: (
            -float(row.get("score") or 0.0),
            -len(row.get("sources") or []),
            str(row.get("symbol") or ""),
        )
    )
    max_rows = max(int(limit), 1)
    all_inputs = [
        *symbols,
        *reports,
        *insights,
        *fundamentals,
        *etfs,
        *positions,
        *list(recent_blocks or []),
    ]
    last_scan_at = _latest_timestamp([row for row in all_inputs if isinstance(row, dict)])
    return {
        "status": "ok" if rows else "empty",
        "pool_count": len(rows),
        "candidates": candidates[:max_rows],
        "coverage": {
            "symbol_count": len(symbols),
            "report_count": len(reports),
            "insight_count": len(insights),
            "fundamental_count": len(fundamentals),
            "etf_count": len(etfs),
            "position_count": len(positions),
            "recent_block_count": len(list(recent_blocks or [])),
        },
        "generated_at": str(generated_at or last_scan_at or ""),
        "last_scan_at": last_scan_at,
    }
