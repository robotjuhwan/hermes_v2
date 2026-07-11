from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

from tradecraft.services.kis_signal_context import collect_kis_signal_context
from tradecraft.services.market_bars import MarketBarRepository


def test_kis_signal_context_fetches_once_and_builds_three_horizons(
    tmp_path: Path,
) -> None:
    class PriceSource:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        async def fetch_domestic_daily_prices(
            self,
            symbol: str,
            *,
            start_date: str,
            end_date: str,
            adjusted: bool = True,
        ) -> list[dict[str, object]]:
            assert adjusted is True
            self.calls.append((symbol, start_date, end_date))
            rows: list[dict[str, object]] = []
            for index in range(20):
                open_date = date(2026, 7, 10) - timedelta(days=index)
                close = 120.0 - index * 2.0
                rows.append(
                    {
                        "open_time": open_date.isoformat(),
                        "open": close - 0.5,
                        "high": close + 0.5,
                        "low": close - 1.0,
                        "close": close,
                        "volume": 1_000.0,
                    }
                )
            return rows

    source = PriceSource()
    repository = MarketBarRepository(tmp_path / "signals.db")

    context = asyncio.run(
        collect_kis_signal_context(
            price_source=source,
            repository=repository,
            symbols=["005930", "005930"],
            evaluated_at="2026-07-11T00:00:00+00:00",
            concurrency=2,
        )
    )

    assert len(source.calls) == 1
    assert source.calls[0][0] == "005930"
    assert context["status"] == "ok"
    signal = context["signals"]["005930"]
    assert signal["agreement_count"] == 3
    assert signal["entry_eligible"] is True
    assert signal["max_risk_fraction"] == 1.0
    assert set(signal["horizons"]) == {"fast", "medium", "slow"}
