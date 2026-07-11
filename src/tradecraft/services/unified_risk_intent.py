from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


AUTHORITY_RISK_CAP_PCT = {
    "observe_only": 0.0,
    "restricted": 0.1875,
    "proving": 0.375,
    "validated": 0.75,
}


@dataclass(frozen=True, slots=True)
class UnifiedRiskIntentV1:
    venue: str
    symbol: str
    authority_grade: str
    drawdown_pct: float
    allowed: bool
    action: str
    max_risk_pct: float
    max_loss_amount: float
    entry_price: float
    stop_price: float
    risk_per_unit: float
    quantity: float
    leverage: float
    margin_mode: str
    applied_caps: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    version: str = "unified_risk_intent_v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def drawdown_risk_cap_pct(drawdown_pct: float) -> tuple[float, str]:
    if drawdown_pct >= 12.0:
        return 0.0, "kill_switch"
    if drawdown_pct >= 10.0:
        return 0.0, "halt_new_entries"
    if drawdown_pct >= 7.0:
        return 0.375, "de_risk_50"
    if drawdown_pct >= 4.0:
        return 0.56, "de_risk_25"
    return 0.75, "normal"


def _floor_to_step(value: float, step: float) -> float:
    if value <= 0 or step <= 0:
        return 0.0
    floored = math.floor((value + step * 1e-9) / step) * step
    decimals = max(-int(math.floor(math.log10(step))), 0) + 2 if step < 1 else 6
    return round(floored, decimals)


def build_unified_risk_intent(
    *,
    venue: str,
    symbol: str,
    equity: float,
    high_water_equity: float,
    entry_price: float,
    stop_price: float,
    round_trip_cost_per_unit: float,
    authority_grade: str,
    signal_risk_fraction: float,
    leverage: float,
    margin_mode: str,
    open_positions: list[dict[str, Any]],
    cluster: str,
    quantity_step: float,
) -> UnifiedRiskIntentV1:
    venue_key = str(venue or "").strip().lower()
    symbol_key = str(symbol or "").strip().upper()
    cluster_key = str(cluster or "").strip().lower()
    grade = str(authority_grade or "observe_only").strip().lower()
    clean_equity = max(float(equity or 0.0), 0.0)
    high_water = max(float(high_water_equity or 0.0), clean_equity)
    drawdown_pct = (
        (high_water - clean_equity) / high_water * 100.0 if high_water > 0 else 0.0
    )
    drawdown_cap, drawdown_action = drawdown_risk_cap_pct(drawdown_pct)
    authority_cap = AUTHORITY_RISK_CAP_PCT.get(grade, 0.0)
    signal_fraction = min(max(float(signal_risk_fraction or 0.0), 0.0), 1.0)
    base_cap = min(authority_cap, drawdown_cap, 0.75)
    desired_risk_pct = base_cap * signal_fraction

    venue_rows = [
        row
        for row in open_positions
        if isinstance(row, dict)
        and str(row.get("venue") or venue_key).strip().lower() == venue_key
        and float(row.get("risk_pct") or 0.0) > 0
    ]
    venue_open_risk = sum(float(row.get("risk_pct") or 0.0) for row in venue_rows)
    symbol_open_risk = sum(
        float(row.get("risk_pct") or 0.0)
        for row in venue_rows
        if str(row.get("symbol") or "").strip().upper() == symbol_key
    )
    cluster_open_risk = sum(
        float(row.get("risk_pct") or 0.0)
        for row in venue_rows
        if cluster_key
        and str(row.get("cluster") or "").strip().lower() == cluster_key
    )
    active_symbols = {
        str(row.get("symbol") or "").strip().upper()
        for row in venue_rows
        if str(row.get("symbol") or "").strip()
    }
    symbol_remaining = max(0.75 - symbol_open_risk, 0.0)
    cluster_remaining = max(1.5 - cluster_open_risk, 0.0)
    venue_remaining = max(4.5 - venue_open_risk, 0.0)
    symbol_slot_remaining = (
        0.0
        if symbol_key not in active_symbols and len(active_symbols) >= 6
        else desired_risk_pct
    )
    max_risk_pct = min(
        desired_risk_pct,
        symbol_remaining,
        cluster_remaining,
        venue_remaining,
        symbol_slot_remaining,
    )

    clean_entry = max(float(entry_price or 0.0), 0.0)
    clean_stop = max(float(stop_price or 0.0), 0.0)
    costs = max(float(round_trip_cost_per_unit or 0.0), 0.0)
    risk_per_unit = abs(clean_entry - clean_stop) + costs
    clean_leverage = max(float(leverage or 0.0), 0.0)
    margin_key = str(margin_mode or "").strip().lower()
    rejection_reasons: list[str] = []
    applied_caps = [f"authority:{grade}", f"drawdown:{drawdown_action}"]
    if max_risk_pct == symbol_remaining and symbol_remaining < desired_risk_pct:
        applied_caps.append("symbol_risk_remaining")
    if max_risk_pct == cluster_remaining and cluster_remaining < desired_risk_pct:
        applied_caps.append("cluster_risk_remaining")
    if max_risk_pct == venue_remaining and venue_remaining < desired_risk_pct:
        applied_caps.append("venue_risk_remaining")
    if symbol_slot_remaining <= 0:
        applied_caps.append("max_active_symbols")

    if clean_equity <= 0:
        rejection_reasons.append("equity_not_positive")
    if clean_entry <= 0 or clean_stop <= 0 or clean_entry == clean_stop:
        rejection_reasons.append("invalid_entry_stop")
    if venue_key == "binance" and clean_leverage > 3.0:
        rejection_reasons.append("leverage_above_3x")
    if venue_key == "binance" and clean_leverage > 1.0 and margin_key != "isolated":
        rejection_reasons.append("futures_margin_not_isolated")
    if drawdown_action in {"kill_switch", "halt_new_entries"}:
        rejection_reasons.append(drawdown_action)
    if max_risk_pct <= 0 and drawdown_action == "normal":
        if desired_risk_pct <= 0:
            rejection_reasons.append("authority_or_signal_risk_zero")
        else:
            rejection_reasons.append("portfolio_risk_capacity_zero")

    max_loss_amount = clean_equity * max_risk_pct / 100.0
    quantity = (
        _floor_to_step(max_loss_amount / risk_per_unit, float(quantity_step or 0.0))
        if risk_per_unit > 0 and not rejection_reasons
        else 0.0
    )
    if quantity <= 0 and not rejection_reasons:
        rejection_reasons.append("quantity_below_step")

    allowed = not rejection_reasons and quantity > 0
    action = "enter" if allowed else drawdown_action if drawdown_action != "normal" else "reject"
    return UnifiedRiskIntentV1(
        venue=venue_key,
        symbol=symbol_key,
        authority_grade=grade,
        drawdown_pct=round(drawdown_pct, 6),
        allowed=allowed,
        action=action,
        max_risk_pct=round(max_risk_pct, 6),
        max_loss_amount=round(max_loss_amount, 8),
        entry_price=clean_entry,
        stop_price=clean_stop,
        risk_per_unit=round(risk_per_unit, 8),
        quantity=quantity,
        leverage=clean_leverage,
        margin_mode=margin_key,
        applied_caps=tuple(applied_caps),
        rejection_reasons=tuple(rejection_reasons),
    )
