from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.runtime.state_store import RuntimeStateStore


class _Settings:
    def __init__(self, path: Path, *, enabled: bool = True, max_age_sec: int = 60) -> None:
        self.research_enabled = enabled
        self.research_state_path = str(path)
        self.research_max_age_sec = max_age_sec
        self.research_max_items = 20


def test_active_research_feed_returns_none_when_disabled(tmp_path: Path) -> None:
    path = tmp_path / "research.json"
    RuntimeStateStore(path).write_snapshot(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "items": [{"title": "Fresh but disabled", "picks": ["005930"]}],
        }
    )

    feed, status = read_active_research_feed(_Settings(path, enabled=False))

    assert feed is None
    assert status == "disabled"


def test_active_research_feed_returns_none_when_stale(tmp_path: Path) -> None:
    path = tmp_path / "research.json"
    RuntimeStateStore(path).write_snapshot(
        {
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "status": "ok",
            "items": [{"title": "Old brief", "picks": ["005930"]}],
        }
    )

    feed, status = read_active_research_feed(_Settings(path, max_age_sec=60))

    assert feed is None
    assert status == "stale"


def test_active_research_feed_returns_fresh_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "research.json"
    RuntimeStateStore(path).write_snapshot(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "research_runner",
            "query": "KRX",
            "items": [{"title": "Fresh brief", "picks": ["005930"]}],
        }
    )

    feed, status = read_active_research_feed(_Settings(path))

    assert status == "ok"
    assert feed is not None
    assert feed["count"] == 1
    assert feed["items"][0]["title"] == "Fresh brief"
