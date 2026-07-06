from __future__ import annotations

from typing import Any

from tradecraft.services.kis_horizon import normalize_horizon

DEFAULT_HORIZON_TARGETS = {
    "cash": 0.30,
    "short": 0.15,
    "mid": 0.30,
    "long": 0.15,
    "core_etf": 0.10,
}
DEFAULT_ETF_UNIVERSE = [
    {"symbol": "069500", "name": "KODEX 200"},
    {"symbol": "102110", "name": "TIGER 200"},
    {"symbol": "091160", "name": "KODEX 반도체"},
]


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _is_symbol(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 6 and text.isdigit()


def parse_horizon_targets(value: Any) -> dict[str, float]:
    if not value:
        return dict(DEFAULT_HORIZON_TARGETS)
    rows: dict[str, Any] = {}
    if isinstance(value, dict):
        rows = value
    else:
        for item in str(value or "").split(","):
            key, sep, raw_weight = item.partition(":")
            if not sep:
                continue
            rows[key.strip()] = raw_weight.strip()
    targets = dict(DEFAULT_HORIZON_TARGETS)
    for key, raw_weight in rows.items():
        horizon = str(key or "").strip().lower()
        if horizon != "cash":
            horizon = normalize_horizon(horizon)
        weight = _safe_float(raw_weight)
        if weight > 0:
            targets[horizon] = weight
    return targets


def parse_etf_universe(value: Any) -> list[dict[str, str]]:
    if not value:
        return [dict(row) for row in DEFAULT_ETF_UNIVERSE]
    rows = value if isinstance(value, list) else str(value or "").split(",")
    out: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            symbol = str(row.get("symbol") or "").strip()
            name = str(row.get("name") or symbol).strip()
        else:
            raw = str(row or "").strip()
            symbol, sep, name = raw.partition(":")
            if not sep:
                continue
            symbol = symbol.strip()
            name = name.strip()
        if _is_symbol(symbol) and name:
            out.append({"symbol": symbol, "name": name})
    return out or [dict(row) for row in DEFAULT_ETF_UNIVERSE]
