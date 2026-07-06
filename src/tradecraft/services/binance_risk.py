from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradecraft.services.binance_symbol import (
    is_upbit_market,
    upbit_market_symbol,
    upbit_market_to_usdt_symbol,
)

ACTIVE_EXPOSURE_STATUSES = {"entry_pending", "open", "exit_pending"}
LANE_RISK_MULTIPLIERS = {
    "core_trend": 1.0,
    "short": 1.0,
    "mid": 1.0,
    "long": 1.0,
    "futures": 0.9,
    "spot:long": 0.8,
    "spot_accumulation": 0.8,
    "intraday_reversal": 0.7,
    "futures:long": 0.7,
    "futures:short": 0.7,
    "funding_squeeze": 0.7,
    "volatile_attack": 0.35,
}


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def block_notional_usdt(
    block: dict[str, Any],
    *,
    upbit_usdt_krw_rate: float = 1.0,
) -> float:
    entry = _to_float(block.get("entry_price"))
    qty = _to_float(block.get("qty_open") or block.get("qty_initial"))
    notional = max(entry * qty, 0.0)
    if is_upbit_market(block.get("market")):
        return notional / max(_to_float(upbit_usdt_krw_rate), 1.0)
    return notional


def current_symbol_exposure_usdt(
    blocks: list[dict[str, Any]],
    symbol: str,
    *,
    upbit_usdt_krw_rate: float = 1.0,
    active_statuses: set[str] | None = None,
) -> float:
    statuses = active_statuses or ACTIVE_EXPOSURE_STATUSES
    target = str(symbol or "").upper().strip()
    target_aliases = {
        target,
        upbit_market_to_usdt_symbol(target),
        upbit_market_symbol(target),
    }
    total = 0.0
    for block in blocks:
        if str(block.get("symbol") or "").upper().strip() not in target_aliases:
            continue
        if str(block.get("status") or "") not in statuses:
            continue
        total += block_notional_usdt(
            block,
            upbit_usdt_krw_rate=upbit_usdt_krw_rate,
        )
    return total


def current_total_exposure_usdt(
    blocks: list[dict[str, Any]],
    *,
    upbit_usdt_krw_rate: float = 1.0,
    active_statuses: set[str] | None = None,
) -> float:
    statuses = active_statuses or ACTIVE_EXPOSURE_STATUSES
    total = 0.0
    for block in blocks:
        if str(block.get("status") or "") not in statuses:
            continue
        total += block_notional_usdt(
            block,
            upbit_usdt_krw_rate=upbit_usdt_krw_rate,
        )
    return total


def cash_reference_usdt(
    *,
    market: str,
    account: dict[str, Any],
    upbit_usdt_krw_rate: float = 1.0,
) -> float:
    if is_upbit_market(market):
        cash_usdt = _to_float(account.get("upbit_cash_usdt"))
        if cash_usdt > 0:
            return cash_usdt
        cash_krw = _to_float(account.get("upbit_cash_krw"))
        if cash_krw > 0:
            return cash_krw / max(_to_float(upbit_usdt_krw_rate), 1.0)
        return 0.0
    if str(market or "").strip().lower() == "futures":
        cash = _to_float(account.get("futures_cash_usdt"))
        if cash > 0:
            return cash
    cash = _to_float(account.get("spot_cash_usdt"))
    if cash > 0:
        return cash
    return _to_float(account.get("cash_usdt") or account.get("total_value_usdt"))


@dataclass(slots=True)
class BinanceRiskConfig:
    account_risk_pct: float = 0.25
    max_total_exposure_usdt: float = 0.0
    max_symbol_exposure_pct: float = 25.0
    min_reward_risk: float = 1.3


class BinanceRiskSizer:
    def __init__(self, config: BinanceRiskConfig) -> None:
        self.config = config

    def size_block(
        self,
        *,
        symbol: str,
        account_equity_usdt: float,
        current_symbol_exposure_usdt: float,
        entry_price: float,
        stop_price: float,
        target_price: float,
        side: str,
        proposed_qty: float | None,
        leverage: int = 1,
        current_total_exposure_usdt: float = 0.0,
        lane: str = "core_trend",
        performance_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        equity = _to_float(account_equity_usdt)
        entry = _to_float(entry_price)
        stop = _to_float(stop_price)
        target = _to_float(target_price)
        if equity <= 0 or entry <= 0 or stop <= 0 or target <= 0:
            return {
                "status": "rejected",
                "reason": "missing_price_or_equity",
                "symbol": symbol,
            }
        stop_distance = abs(entry - stop)
        reward_distance = abs(target - entry)
        if stop_distance <= 0:
            return {
                "status": "rejected",
                "reason": "invalid_stop_distance",
                "symbol": symbol,
            }
        normalized_side = str(side or "long").strip().lower()
        if normalized_side == "short":
            if not (target < entry < stop):
                return {
                    "status": "rejected",
                    "reason": "invalid_price_direction",
                    "symbol": symbol,
                    "side": normalized_side,
                }
        elif not (stop < entry < target):
            return {
                "status": "rejected",
                "reason": "invalid_price_direction",
                "symbol": symbol,
                "side": "long",
            }
        reward_risk = reward_distance / stop_distance
        if reward_risk < float(self.config.min_reward_risk):
            return {
                "status": "rejected",
                "reason": "reward_risk_too_low",
                "symbol": symbol,
                "reward_risk": reward_risk,
                "min_reward_risk": float(self.config.min_reward_risk),
            }
        normalized_lane = str(lane or "core_trend").strip().lower() or "core_trend"
        lane_multiplier = LANE_RISK_MULTIPLIERS.get(normalized_lane, 1.0)
        lane_multiplier = min(max(_to_float(lane_multiplier), 0.05), 3.0)
        live_multiplier = min(max(_to_float(performance_multiplier) or 1.0, 0.1), 3.0)
        effective_multiplier = lane_multiplier * live_multiplier
        risk_budget = equity * (float(self.config.account_risk_pct) / 100.0)
        risk_budget *= effective_multiplier
        max_qty_by_risk = risk_budget / stop_distance
        max_qty_by_symbol = self._max_qty_by_symbol_exposure(
            equity=equity,
            entry=entry,
            current_symbol_exposure_usdt=current_symbol_exposure_usdt,
        )
        max_qty_by_total = self._max_qty_by_total_exposure(
            entry=entry,
            current_total_exposure_usdt=current_total_exposure_usdt,
        )
        raw_qty = min(max_qty_by_risk, max_qty_by_symbol, max_qty_by_total)
        proposed = _to_float(proposed_qty)
        if proposed > 0:
            raw_qty = min(raw_qty, proposed)
        if raw_qty <= 0:
            return {
                "status": "rejected",
                "reason": "exposure_budget_exhausted",
                "symbol": symbol,
            }
        return {
            "status": "ok",
            "symbol": symbol,
            "side": side,
            "qty": raw_qty,
            "risk_budget_usdt": risk_budget,
            "lane": normalized_lane,
            "lane_risk_multiplier": lane_multiplier,
            "stop_distance_usdt": stop_distance,
            "reward_distance_usdt": reward_distance,
            "reward_risk": reward_risk,
            "notional_usdt": raw_qty * entry,
            "leverage": max(int(leverage or 1), 1),
            "performance_multiplier": live_multiplier,
            "effective_risk_multiplier": effective_multiplier,
        }

    def _max_qty_by_symbol_exposure(
        self,
        *,
        equity: float,
        entry: float,
        current_symbol_exposure_usdt: float,
    ) -> float:
        symbol_pct = float(self.config.max_symbol_exposure_pct)
        if symbol_pct <= 0:
            return float("inf")
        max_symbol_exposure = equity * (symbol_pct / 100.0)
        remaining = max(max_symbol_exposure - _to_float(current_symbol_exposure_usdt), 0.0)
        return remaining / entry if entry > 0 else 0.0

    def _max_qty_by_total_exposure(
        self,
        *,
        entry: float,
        current_total_exposure_usdt: float,
    ) -> float:
        max_total = float(self.config.max_total_exposure_usdt)
        if max_total <= 0:
            return float("inf")
        remaining = max(max_total - _to_float(current_total_exposure_usdt), 0.0)
        return remaining / entry if entry > 0 else 0.0
