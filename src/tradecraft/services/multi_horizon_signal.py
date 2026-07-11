from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any


@dataclass(frozen=True, slots=True)
class MultiHorizonSignalV1:
    venue: str
    symbol: str
    evaluated_at: str
    horizons: dict[str, dict[str, Any]]
    agreement_count: int
    agreed_direction: str
    entry_eligible: bool
    max_risk_fraction: float
    entry_trigger: float
    initial_stop_reference: float
    expires_at: str
    source_bar_ids: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    version: str = "multi_horizon_signal_v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _unavailable(reason: str, *, bar_count: int = 0) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "direction": "unknown",
        "reason": reason,
        "bar_count": bar_count,
    }


def _horizon_signal(
    rows: list[dict[str, Any]],
    *,
    evaluated_at: datetime,
    freshness_limit_sec: int,
) -> dict[str, Any]:
    if len(rows) < 3:
        return _unavailable("insufficient_bars", bar_count=len(rows))
    try:
        times = [_timestamp(row.get("open_time")) for row in rows]
        if times != sorted(times) or len(set(times)) != len(times):
            return _unavailable("non_monotonic_bars", bar_count=len(rows))
        if times[-1] > evaluated_at:
            return _unavailable("future_bar", bar_count=len(rows))
        age_sec = (evaluated_at - times[-1]).total_seconds()
        if age_sec > max(int(freshness_limit_sec), 0):
            return _unavailable("stale_bars", bar_count=len(rows))

        opens = [float(row.get("open") or 0.0) for row in rows]
        highs = [float(row.get("high") or 0.0) for row in rows]
        lows = [float(row.get("low") or 0.0) for row in rows]
        closes = [float(row.get("close") or 0.0) for row in rows]
        if min(opens + highs + lows + closes) <= 0:
            return _unavailable("invalid_ohlc", bar_count=len(rows))
        if any(
            high < max(open_price, close_price)
            or low > min(open_price, close_price)
            for open_price, high, low, close_price in zip(
                opens, highs, lows, closes
            )
        ):
            return _unavailable("invalid_ohlc", bar_count=len(rows))
    except (TypeError, ValueError):
        return _unavailable("invalid_bar_payload", bar_count=len(rows))

    short_window = max(min(len(closes) // 3, 5), 2)
    short_mean = fmean(closes[-short_window:])
    long_mean = fmean(closes)
    last_close = closes[-1]
    previous_high = max(highs[:-1])
    previous_low = min(lows[:-1])
    momentum_pct = (last_close - closes[0]) / closes[0] * 100.0
    true_ranges = [high - low for high, low in zip(highs, lows)]
    atr = fmean(true_ranges)

    if last_close > closes[0] and short_mean > long_mean and last_close >= previous_high:
        direction = "long"
        structure_stop = min(lows[-3:])
        stop_reference = max(structure_stop, last_close - 2.0 * atr)
    elif last_close < closes[0] and short_mean < long_mean and last_close <= previous_low:
        direction = "short"
        structure_stop = max(highs[-3:])
        stop_reference = min(structure_stop, last_close + 2.0 * atr)
    else:
        direction = "flat"
        stop_reference = 0.0

    return {
        "status": "ok",
        "direction": direction,
        "bar_count": len(rows),
        "momentum_pct": round(momentum_pct, 6),
        "atr": round(atr, 8),
        "entry_trigger": last_close,
        "stop_reference": round(stop_reference, 8),
        "latest_bar_at": times[-1].isoformat(),
    }


def build_multi_horizon_signal(
    *,
    venue: str,
    symbol: str,
    evaluated_at: str,
    bars_by_horizon: dict[str, list[dict[str, Any]]],
    freshness_limits: dict[str, int],
) -> MultiHorizonSignalV1:
    evaluated = _timestamp(evaluated_at)
    horizons = {
        name: _horizon_signal(
            list(bars_by_horizon.get(name) or []),
            evaluated_at=evaluated,
            freshness_limit_sec=int(freshness_limits.get(name) or 0),
        )
        for name in ("fast", "medium", "slow")
    }
    directions = [
        str(row.get("direction") or "")
        for row in horizons.values()
        if row.get("status") == "ok"
        and row.get("direction") in {"long", "short"}
    ]
    long_count = directions.count("long")
    short_count = directions.count("short")
    if long_count >= 2:
        agreed_direction = "long"
        agreement_count = long_count
    elif short_count >= 2:
        agreed_direction = "short"
        agreement_count = short_count
    else:
        agreed_direction = "none"
        agreement_count = max(long_count, short_count)

    agreed_rows = [
        row
        for row in horizons.values()
        if row.get("status") == "ok"
        and row.get("direction") == agreed_direction
    ]
    entry_trigger = (
        float(agreed_rows[0].get("entry_trigger") or 0.0) if agreed_rows else 0.0
    )
    stop_values = [
        float(row.get("stop_reference") or 0.0)
        for row in agreed_rows
        if float(row.get("stop_reference") or 0.0) > 0
    ]
    if agreed_direction == "long" and stop_values:
        initial_stop = max(stop_values)
    elif agreed_direction == "short" and stop_values:
        initial_stop = min(stop_values)
    else:
        initial_stop = 0.0

    source_bar_ids = tuple(
        sorted(
            {
                str(row.get("source_id") or "")
                for rows in bars_by_horizon.values()
                for row in rows
                if str(row.get("source_id") or "").strip()
            }
        )
    )
    valid_entry_prices = [
        float(row.get("entry_trigger") or 0.0)
        for row in agreed_rows
        if float(row.get("entry_trigger") or 0.0) > 0
    ]
    blocking_reasons: list[str] = []
    if (
        len(valid_entry_prices) >= 2
        and min(valid_entry_prices) > 0
        and (max(valid_entry_prices) - min(valid_entry_prices))
        / min(valid_entry_prices)
        > 0.02
    ):
        blocking_reasons.append("cross_horizon_price_mismatch")
    entry_eligible = agreement_count >= 2 and not blocking_reasons
    max_risk_fraction = (
        0.0 if not entry_eligible else 1.0 if agreement_count == 3 else 0.6
    )
    expires = evaluated + timedelta(
        hours=12 if str(venue).strip().lower() == "binance" else 96
    )
    return MultiHorizonSignalV1(
        venue=str(venue).strip().lower(),
        symbol=str(symbol).strip().upper(),
        evaluated_at=evaluated.isoformat(),
        horizons=horizons,
        agreement_count=agreement_count,
        agreed_direction=agreed_direction,
        entry_eligible=entry_eligible,
        max_risk_fraction=max_risk_fraction,
        entry_trigger=entry_trigger,
        initial_stop_reference=initial_stop,
        expires_at=expires.isoformat(),
        source_bar_ids=source_bar_ids,
        blocking_reasons=tuple(blocking_reasons),
    )
