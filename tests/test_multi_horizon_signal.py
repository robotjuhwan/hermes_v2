from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradecraft.services.multi_horizon_signal import build_multi_horizon_signal


def _bars(
    count: int,
    *,
    direction: str,
    end: date = date(2026, 7, 10),
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        if direction == "up":
            close = 120.0 - (count - index - 1) * 2.0
        elif direction == "down":
            close = 100.0 + (count - index - 1) * 2.0
        else:
            close = 110.0
        open_time = end - timedelta(days=count - index - 1)
        rows.append(
            {
                "open_time": open_time.isoformat(),
                "open": close - 0.5,
                "high": close + 0.5,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000.0 + index * 10.0,
                "source_id": f"fixture:{direction}:{open_time.isoformat()}",
            }
        )
    return rows


def test_two_of_three_up_horizons_allow_partial_risk() -> None:
    signal = build_multi_horizon_signal(
        venue="kis",
        symbol="005930",
        evaluated_at="2026-07-11T00:00:00+00:00",
        bars_by_horizon={
            "fast": _bars(5, direction="up"),
            "medium": _bars(10, direction="up"),
            "slow": _bars(20, direction="flat"),
        },
        freshness_limits={
            "fast": 4 * 86_400,
            "medium": 4 * 86_400,
            "slow": 4 * 86_400,
        },
    )

    assert signal.agreement_count == 2
    assert signal.agreed_direction == "long"
    assert signal.entry_eligible is True
    assert signal.max_risk_fraction == pytest.approx(0.60)
    assert signal.initial_stop_reference < signal.entry_trigger
    assert len(signal.source_bar_ids) == 30


def test_cross_horizon_price_mismatch_blocks_entry() -> None:
    medium = _bars(10, direction="up")
    for row in medium:
        for key in ("open", "high", "low", "close"):
            row[key] = float(row[key]) * 1.20

    signal = build_multi_horizon_signal(
        venue="kis",
        symbol="005930",
        evaluated_at="2026-07-11T00:00:00+00:00",
        bars_by_horizon={
            "fast": _bars(5, direction="up"),
            "medium": medium,
            "slow": _bars(20, direction="flat"),
        },
        freshness_limits={
            "fast": 4 * 86_400,
            "medium": 4 * 86_400,
            "slow": 4 * 86_400,
        },
    )

    assert signal.entry_eligible is False
    assert signal.blocking_reasons == ("cross_horizon_price_mismatch",)
