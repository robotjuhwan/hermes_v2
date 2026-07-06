from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, pow
from typing import Any

MAX_REQUIRED_DAILY_RETURN_PCT = 1_000_000.0
MAX_EQUITY_OUTPUT_USDT = 1_000_000_000_000_000.0


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pct_change(start: float, current: float) -> float:
    if start <= 0:
        return 0.0
    return (current / start - 1.0) * 100.0


def _finite_pct(value: float, *, floor: float | None = None) -> float:
    numeric = _to_float(value)
    if numeric != numeric:
        return 0.0
    if not isfinite(numeric):
        return MAX_REQUIRED_DAILY_RETURN_PCT if numeric >= 0 else -MAX_REQUIRED_DAILY_RETURN_PCT
    if floor is not None:
        numeric = max(numeric, floor)
    return min(max(numeric, -MAX_REQUIRED_DAILY_RETURN_PCT), MAX_REQUIRED_DAILY_RETURN_PCT)


def _finite_amount(value: float) -> float:
    numeric = _to_float(value)
    if numeric != numeric:
        return 0.0
    if not isfinite(numeric):
        return MAX_EQUITY_OUTPUT_USDT if numeric >= 0 else 0.0
    return min(max(numeric, 0.0), MAX_EQUITY_OUTPUT_USDT)


@dataclass(frozen=True, slots=True)
class CryptoGrowthTargetLedger:
    monthly_target_pct: float = 50.0

    def snapshot(
        self,
        *,
        start_equity_usdt: float,
        current_equity_usdt: float,
        elapsed_days: float,
        month_days: float = 30.0,
    ) -> dict[str, float | str | bool]:
        start = max(_to_float(start_equity_usdt), 0.0)
        current = max(_to_float(current_equity_usdt), 0.0)
        total_days = max(_to_float(month_days), 1.0)
        elapsed = min(max(_to_float(elapsed_days), 0.0), total_days)
        remaining_days = max(total_days - elapsed, 0.0)
        monthly_target = _finite_pct(_to_float(self.monthly_target_pct))
        target_equity = _finite_amount(start * (1.0 + monthly_target / 100.0))
        current_return = _finite_pct(_pct_change(start, current))
        remaining_return = _finite_pct(_pct_change(current, target_equity), floor=0.0)
        required_daily = 0.0
        required_daily_capped = False
        if current > 0 and target_equity > current and remaining_days > 0:
            try:
                required_daily = (
                    pow(target_equity / current, 1.0 / remaining_days) - 1.0
                ) * 100.0
            except OverflowError:
                required_daily = MAX_REQUIRED_DAILY_RETURN_PCT
                required_daily_capped = True
            if not isfinite(required_daily) or required_daily > MAX_REQUIRED_DAILY_RETURN_PCT:
                required_daily = MAX_REQUIRED_DAILY_RETURN_PCT
                required_daily_capped = True
        expected_to_date = monthly_target * (elapsed / total_days)
        if current >= target_equity:
            status = "ahead_target"
        elif current_return >= expected_to_date:
            status = "on_track"
        else:
            status = "behind_target"
        return {
            "monthly_target_pct": round(monthly_target, 4),
            "basis": "account_equity_monthly_run_rate",
            "basis_label": "account equity monthly run-rate",
            "start_equity_usdt": round(start, 4),
            "current_equity_usdt": round(current, 4),
            "target_equity_usdt": round(target_equity, 4),
            "current_return_pct": round(current_return, 4),
            "remaining_return_pct": round(remaining_return, 4),
            "elapsed_days": round(elapsed, 4),
            "remaining_days": round(remaining_days, 4),
            "required_daily_return_pct": round(required_daily, 4),
            "required_daily_return_capped": required_daily_capped,
            "status": status,
        }
