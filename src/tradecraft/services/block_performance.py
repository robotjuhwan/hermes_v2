from __future__ import annotations

import math
from typing import Any


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, dict):
        value = value.get("price")
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else 0.0
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text.lower() in {"-", "n/a", "nan", "inf", "+inf", "-inf"}:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return number if math.isfinite(number) else 0.0


def _round_metric(value: float) -> float:
    return round(float(value), 4)


def _empty_summary() -> dict[str, float]:
    return {
        "entry_price": 0.0,
        "current_price": 0.0,
        "peak_price": 0.0,
        "trough_price": 0.0,
        "mfe_pct": 0.0,
        "mae_pct": 0.0,
        "current_pnl_pct": 0.0,
        "current_return_pct": 0.0,
        "giveback_pct": 0.0,
    }


def summarize_block_path(
    *,
    entry_price: Any,
    current_price: Any,
    prices: list[Any],
) -> dict[str, float]:
    entry = _safe_float(entry_price)
    current = _safe_float(current_price)
    if entry <= 0:
        return _empty_summary()

    clean_prices = [_safe_float(price) for price in prices]
    path_prices = [entry, *[price for price in clean_prices if price > 0]]
    if current > 0:
        path_prices.append(current)

    peak_price = max(path_prices)
    trough_price = min(path_prices)
    mfe_pct = max((peak_price - entry) / entry * 100.0, 0.0)
    mae_pct = min((trough_price - entry) / entry * 100.0, 0.0)
    current_pnl_pct = (current - entry) / entry * 100.0 if current > 0 else 0.0
    giveback_pct = max((peak_price - current) / entry * 100.0, 0.0) if current > 0 else 0.0

    return {
        "entry_price": _round_metric(entry),
        "current_price": _round_metric(current if current > 0 else 0.0),
        "peak_price": _round_metric(peak_price),
        "trough_price": _round_metric(trough_price),
        "mfe_pct": _round_metric(mfe_pct),
        "mae_pct": _round_metric(mae_pct),
        "current_pnl_pct": _round_metric(current_pnl_pct),
        "current_return_pct": _round_metric(current_pnl_pct),
        "giveback_pct": _round_metric(giveback_pct),
    }
