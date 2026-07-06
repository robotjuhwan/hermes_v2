from __future__ import annotations

import math
from typing import Any


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


def krx_tick_size(price: float) -> int:
    value = max(float(price), 0.0)
    if value < 1_000:
        return 1
    if value < 5_000:
        return 5
    if value < 10_000:
        return 10
    if value < 50_000:
        return 50
    if value < 100_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def aggressive_limit_price(price: float, *, side: str, bps: float = 30.0) -> int:
    base = max(float(price), 0.0)
    if base <= 0:
        return 0
    ratio = max(float(bps), 0.0) / 10_000.0
    raw = base * (1 + ratio) if str(side).lower() == "buy" else base * (1 - ratio)
    tick = krx_tick_size(raw)
    if str(side).lower() == "buy":
        return int(math.ceil(raw / tick) * tick)
    return int(max(math.floor(raw / tick) * tick, tick))


def round_policy_krx_price(value: Any, *, field: str) -> float:
    price = max(_safe_float(value), 0.0)
    if price <= 0:
        return 0.0
    tick = krx_tick_size(price)
    if field == "target_price":
        return float(math.ceil(price / tick) * tick)
    return float(max(math.floor(price / tick) * tick, tick))
