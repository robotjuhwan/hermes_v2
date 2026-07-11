from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from tradecraft.services.market_bars import MarketBarRepository
from tradecraft.services.multi_horizon_signal import build_multi_horizon_signal


KST = ZoneInfo("Asia/Seoul")


class KISDailyPriceSource(Protocol):
    async def fetch_domestic_daily_prices(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> list[dict[str, Any]]: ...


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def collect_kis_signal_context(
    *,
    price_source: KISDailyPriceSource,
    repository: MarketBarRepository,
    symbols: list[str],
    evaluated_at: str,
    concurrency: int = 2,
) -> dict[str, Any]:
    evaluated = _timestamp(evaluated_at)
    end_date = evaluated.astimezone(KST).date()
    start_date = end_date - timedelta(days=120)
    clean_symbols = [
        symbol
        for symbol in dict.fromkeys(
            str(value or "").strip().upper() for value in symbols
        )
        if len(symbol) == 6 and symbol.isdigit()
    ]
    semaphore = asyncio.Semaphore(max(int(concurrency), 1))

    async def collect_one(symbol: str) -> tuple[str, dict[str, Any] | None, str]:
        try:
            async with semaphore:
                rows = await price_source.fetch_domestic_daily_prices(
                    symbol,
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjusted=True,
                )
            repository.save_bars(
                venue="kis",
                symbol=symbol,
                interval="1d",
                rows=rows,
                source="kis:FHKST03010100",
            )
            stored = repository.list_bars(
                venue="kis",
                symbol=symbol,
                interval="1d",
                limit=60,
            )
            signal = build_multi_horizon_signal(
                venue="kis",
                symbol=symbol,
                evaluated_at=evaluated.isoformat(),
                bars_by_horizon={
                    "fast": stored[-5:],
                    "medium": stored[-10:],
                    "slow": stored[-20:],
                },
                freshness_limits={
                    "fast": 4 * 86_400,
                    "medium": 4 * 86_400,
                    "slow": 4 * 86_400,
                },
            )
            return symbol, signal.to_dict(), ""
        except Exception as exc:
            return symbol, None, str(exc)

    results = await asyncio.gather(*(collect_one(symbol) for symbol in clean_symbols))
    signals = {
        symbol: signal
        for symbol, signal, _ in results
        if isinstance(signal, dict)
    }
    errors = [
        {"symbol": symbol, "error_message": error}
        for symbol, _, error in results
        if error
    ]
    status = "partial" if signals and errors else "error" if errors else "ok"
    return {
        "status": status,
        "generated_at": evaluated.isoformat(),
        "requested_count": len(clean_symbols),
        "signal_count": len(signals),
        "error_count": len(errors),
        "signals": signals,
        "errors": errors,
        "version": "kis_signal_context_v1",
    }
