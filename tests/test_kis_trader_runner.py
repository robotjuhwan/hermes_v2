from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tradecraft.runtime.kis_trader_runner import _compute_sleep_seconds


KST = ZoneInfo("Asia/Seoul")


def test_compute_sleep_seconds_caps_to_market_open_boundary() -> None:
    now = datetime(2026, 2, 20, 8, 59, 40, tzinfo=KST)
    sleep_sec = _compute_sleep_seconds(300, now=now)
    assert sleep_sec == 20


def test_compute_sleep_seconds_keeps_interval_during_open_session() -> None:
    now = datetime(2026, 2, 20, 9, 5, 0, tzinfo=KST)
    sleep_sec = _compute_sleep_seconds(300, now=now)
    assert sleep_sec == 300
