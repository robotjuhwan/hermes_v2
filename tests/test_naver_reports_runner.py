from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradecraft.runtime.naver_reports_runner import _is_symbol_directory_stale


def test_is_symbol_directory_stale_when_missing_timestamp() -> None:
    assert _is_symbol_directory_stale("", min_age_sec=3600)


def test_is_symbol_directory_stale_when_recent_timestamp() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    assert not _is_symbol_directory_stale(recent, min_age_sec=3600)


def test_is_symbol_directory_stale_when_old_timestamp() -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    assert _is_symbol_directory_stale(old, min_age_sec=12 * 3600)
