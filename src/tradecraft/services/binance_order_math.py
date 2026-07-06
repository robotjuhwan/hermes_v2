from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any


def safe_float(value: Any) -> float:
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


def safe_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal("0")


def quantize_to_step(value: Decimal, step: Decimal, *, rounding: str) -> Decimal:
    if value <= 0 or step <= 0:
        return max(value, Decimal("0"))
    units = (value / step).to_integral_value(rounding=rounding)
    return max(units * step, Decimal("0"))


def round_candidate_price(value: float) -> float:
    if value <= 0:
        return 0.0
    if value >= 100:
        digits = 2
    elif value >= 10:
        digits = 3
    elif value >= 1:
        digits = 4
    elif value >= 0.1:
        digits = 5
    else:
        digits = 6
    return round(value, digits)


def reward_risk(
    *,
    side: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
) -> float:
    if side == "long":
        risk = entry_price - stop_price
        reward = target_price - entry_price
    else:
        risk = stop_price - entry_price
        reward = entry_price - target_price
    if risk <= 0 or reward <= 0:
        return 0.0
    return round(reward / risk, 4)


def candidate_last_price(*, candidate: dict[str, Any], features: dict[str, Any]) -> float:
    for source in (candidate, features):
        for key in ("price", "last_price", "current_price", "close"):
            value = safe_float(source.get(key))
            if value > 0:
                return value
    return 0.0


def min_notional_from_filters(filters: dict[str, dict[str, Any]]) -> Decimal:
    for key in ("MIN_NOTIONAL", "NOTIONAL"):
        row = filters.get(key) or {}
        for field in ("minNotional", "notional", "min_notional"):
            value = safe_decimal(row.get(field))
            if value > 0:
                return value
    return Decimal("0")


def normalize_order_for_filters(
    filters: dict[str, dict[str, Any]],
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    allow_min_notional_qty_bump: bool = False,
    allow_reduce_only_below_min_notional: bool = False,
    max_notional_bump_shortfall_pct: float = 5.0,
) -> dict[str, float]:
    if not filters:
        return {"quantity": qty, "limit_price": limit_price}

    qty_dec = safe_decimal(qty)
    price_dec = safe_decimal(limit_price)
    lot = filters.get("LOT_SIZE") or {}
    price_filter = filters.get("PRICE_FILTER") or {}
    step = safe_decimal(lot.get("stepSize") or lot.get("step_size") or 0)
    tick = safe_decimal(price_filter.get("tickSize") or price_filter.get("tick_size") or 0)
    min_qty = safe_decimal(lot.get("minQty") or lot.get("min_qty") or 0)
    min_price = safe_decimal(price_filter.get("minPrice") or price_filter.get("min_price") or 0)

    if step > 0:
        qty_dec = quantize_to_step(qty_dec, step, rounding=ROUND_FLOOR)
    if tick > 0:
        price_rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
        price_dec = quantize_to_step(price_dec, tick, rounding=price_rounding)

    if qty_dec <= 0 or (min_qty > 0 and qty_dec < min_qty):
        raise ValueError(
            f"order quantity below min quantity: {symbol} {float(qty_dec):g} < "
            f"{float(min_qty):g}"
        )
    if price_dec <= 0 or (min_price > 0 and price_dec < min_price):
        raise ValueError(f"order price below min price: {symbol} {float(price_dec):g}")

    min_notional = min_notional_from_filters(filters)
    if (
        min_notional > 0
        and qty_dec * price_dec < min_notional
        and not allow_reduce_only_below_min_notional
    ):
        notional_before_bump = qty_dec * price_dec
        max_shortfall_pct = safe_decimal(max_notional_bump_shortfall_pct)
        min_bumpable_notional = min_notional * (
            Decimal("1") - max_shortfall_pct / Decimal("100")
        )
        if (
            allow_min_notional_qty_bump
            and step > 0
            and price_dec > 0
            and notional_before_bump >= min_bumpable_notional
        ):
            min_qty_for_notional = quantize_to_step(
                min_notional / price_dec,
                step,
                rounding=ROUND_CEILING,
            )
            if min_qty_for_notional > qty_dec:
                qty_dec = min_qty_for_notional
        if min_qty > 0 and qty_dec < min_qty:
            qty_dec = min_qty
    if (
        min_notional > 0
        and qty_dec * price_dec < min_notional
        and not allow_reduce_only_below_min_notional
    ):
        raise ValueError(
            f"order notional below minimum: {symbol} "
            f"{float(qty_dec * price_dec):g} < {float(min_notional):g}"
        )
    return {"quantity": float(qty_dec), "limit_price": float(price_dec)}


def candidate_volatility_pct(
    *,
    change_pct_24h: float,
    spread_bps: float,
    horizon: str,
    market: str,
) -> float:
    horizon_floor = {"short": 0.75, "mid": 1.1, "long": 1.4, "futures": 0.85}.get(horizon, 0.9)
    spread_pct = max(spread_bps / 100.0, 0.0)
    change_component = abs(change_pct_24h) * 0.28
    market_floor = 1.0 if market == "futures" else horizon_floor
    return min(max(change_component, spread_pct, market_floor), 7.5)


def candidate_stop_pct(
    *,
    volatility_pct: float,
    horizon: str,
    market: str,
    min_candidate_stop_pct: float,
) -> float:
    multiplier = {"short": 0.9, "mid": 1.25, "long": 1.7, "futures": 0.85}.get(horizon, 0.95)
    if market == "futures":
        multiplier = min(multiplier, 0.9)
    floor = max(safe_float(min_candidate_stop_pct), 0.1)
    return min(max(volatility_pct * multiplier, floor), 9.0)
