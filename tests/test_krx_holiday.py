from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import tradecraft.services.krx_holiday as krx_holiday
from tradecraft.services.krx_holiday import (
    KRXHolidayCalendar,
    KRXHolidayCalendarConfig,
)


@pytest.fixture(autouse=True)
def clear_krx_holiday_global_cache() -> None:
    krx_holiday._GLOBAL_CACHE.clear()
    krx_holiday._GLOBAL_CACHE_UPDATED_AT.clear()
    krx_holiday._GLOBAL_FAILURE_UPDATED_AT.clear()


def test_krx_holiday_calendar_backs_off_after_fetch_failure(tmp_path: Path) -> None:
    calls = 0

    class FailingCalendar(KRXHolidayCalendar):
        def _fetch_year_holidays(self, year: int) -> set[date] | None:
            nonlocal calls
            calls += 1
            return None

    calendar = FailingCalendar(
        KRXHolidayCalendarConfig(
            cache_path=str(tmp_path / "krx_holidays.json"),
            failure_cache_ttl_sec=1800,
        )
    )

    assert calendar.is_open_day(date(2026, 6, 15)) is True
    assert calendar.is_open_day(date(2026, 6, 16)) is True
    assert calls == 1


def test_krx_holiday_calendar_shares_failure_backoff_between_instances(tmp_path: Path) -> None:
    calls = 0

    class FailingCalendar(KRXHolidayCalendar):
        def _fetch_year_holidays(self, year: int) -> set[date] | None:
            nonlocal calls
            calls += 1
            return None

    config = KRXHolidayCalendarConfig(
        cache_path=str(tmp_path / "krx_holidays.json"),
        failure_cache_ttl_sec=1800,
    )
    first = FailingCalendar(config)
    second = FailingCalendar(config)

    assert first.is_open_day(date(2099, 6, 15)) is True
    assert second.is_open_day(date(2099, 6, 16)) is True
    assert calls == 1


def test_krx_holiday_calendar_default_timeout_is_short_for_ui_paths() -> None:
    assert KRXHolidayCalendarConfig().timeout_sec <= 2.0


def test_krx_holiday_calendar_persists_holidays_across_memory_cache_reset(
    tmp_path: Path,
) -> None:
    calls = 0
    cache_path = tmp_path / "krx_holidays.json"

    class FetchingCalendar(KRXHolidayCalendar):
        def _fetch_year_holidays(self, year: int) -> set[date] | None:
            nonlocal calls
            calls += 1
            return {date(year, 5, 5)}

    config = KRXHolidayCalendarConfig(cache_path=str(cache_path))
    first = FetchingCalendar(config)
    assert first.is_open_day(date(2026, 5, 5)) is False
    assert calls == 1

    krx_holiday._GLOBAL_CACHE.clear()
    krx_holiday._GLOBAL_CACHE_UPDATED_AT.clear()
    krx_holiday._GLOBAL_FAILURE_UPDATED_AT.clear()

    second = FetchingCalendar(config)
    assert second.is_open_day(date(2026, 5, 5)) is False
    assert calls == 1


def test_krx_holiday_calendar_persists_failure_backoff_across_memory_cache_reset(
    tmp_path: Path,
) -> None:
    calls = 0
    cache_path = tmp_path / "krx_holidays.json"

    class FailingCalendar(KRXHolidayCalendar):
        def _fetch_year_holidays(self, year: int) -> set[date] | None:
            nonlocal calls
            calls += 1
            return None

    config = KRXHolidayCalendarConfig(
        cache_path=str(cache_path),
        failure_cache_ttl_sec=1800,
    )
    first = FailingCalendar(config)
    assert first.is_open_day(date(2026, 6, 15)) is True
    assert calls == 1

    krx_holiday._GLOBAL_CACHE.clear()
    krx_holiday._GLOBAL_CACHE_UPDATED_AT.clear()
    krx_holiday._GLOBAL_FAILURE_UPDATED_AT.clear()

    second = FailingCalendar(config)
    assert second.is_open_day(date(2026, 6, 16)) is True
    assert calls == 1
