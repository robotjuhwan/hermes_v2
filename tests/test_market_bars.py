from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.services.market_bars import MarketBarRepository


def test_market_bar_repository_round_trips_in_source_order(tmp_path: Path) -> None:
    repository = MarketBarRepository(tmp_path / "signals.db")

    affected = repository.save_bars(
        venue="kis",
        symbol="005930",
        interval="1d",
        rows=[
            {
                "open_time": "2026-07-10",
                "open": 61_500,
                "high": 63_000,
                "low": 61_000,
                "close": 62_800,
                "volume": 1_200,
            },
            {
                "open_time": "2026-07-09",
                "open": 60_000,
                "high": 62_000,
                "low": 59_500,
                "close": 61_500,
                "volume": 1_000,
            },
        ],
        source="kis:FHKST03010100",
    )

    rows = repository.list_bars(
        venue="kis",
        symbol="005930",
        interval="1d",
        limit=20,
    )

    assert affected == 2
    assert [row["close"] for row in rows] == [61_500.0, 62_800.0]
    assert rows[-1]["source_id"] == "kis:FHKST03010100:005930:1d:2026-07-10"


def test_market_bar_repository_upserts_same_source_bar(tmp_path: Path) -> None:
    repository = MarketBarRepository(tmp_path / "signals.db")
    base = {
        "open_time": "2026-07-10",
        "open": 61_500,
        "high": 63_000,
        "low": 61_000,
        "close": 62_800,
        "volume": 1_200,
    }

    repository.save_bars(
        venue="kis",
        symbol="005930",
        interval="1d",
        rows=[base],
        source="kis:FHKST03010100",
    )
    repository.save_bars(
        venue="kis",
        symbol="005930",
        interval="1d",
        rows=[{**base, "close": 62_900}],
        source="kis:FHKST03010100",
    )

    rows = repository.list_bars(
        venue="kis",
        symbol="005930",
        interval="1d",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["close"] == 62_900.0


@pytest.mark.parametrize(
    "row",
    [
        {
            "open_time": "2026-07-10",
            "open": 0,
            "high": 63_000,
            "low": 61_000,
            "close": 62_800,
            "volume": 1_200,
        },
        {
            "open_time": "2026-07-10",
            "open": 61_500,
            "high": 60_000,
            "low": 61_000,
            "close": 62_800,
            "volume": 1_200,
        },
    ],
)
def test_market_bar_repository_rejects_invalid_ohlc(
    tmp_path: Path,
    row: dict[str, float | str],
) -> None:
    repository = MarketBarRepository(tmp_path / "signals.db")

    with pytest.raises(ValueError, match="invalid market bar"):
        repository.save_bars(
            venue="kis",
            symbol="005930",
            interval="1d",
            rows=[row],
            source="kis:FHKST03010100",
        )
