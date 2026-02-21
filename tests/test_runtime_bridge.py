from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.runtime_bridge import (
    ResearchSnapshotReader,
    RuntimeSnapshotReader,
)


def test_runtime_reader_missing_snapshot(tmp_path) -> None:
    path = tmp_path / "state.json"
    reader = RuntimeSnapshotReader(str(path), max_age_sec=10)
    sessions, status = reader.read_sessions()
    assert sessions is None
    assert status == "missing"


def test_runtime_reader_stale_snapshot(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = RuntimeStateStore(path)
    store.write_snapshot(
        {
            "updated_at": (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).isoformat(),
            "sessions": [{"session_id": "s1"}],
        }
    )

    reader = RuntimeSnapshotReader(str(path), max_age_sec=30)
    sessions, status = reader.read_sessions()
    assert sessions is None
    assert status == "stale"


def test_runtime_reader_ok_snapshot(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = RuntimeStateStore(path)
    store.write_snapshot(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sessions": [{"session_id": "s1", "mode": "short_term"}],
        }
    )

    reader = RuntimeSnapshotReader(str(path), max_age_sec=30)
    sessions, status = reader.read_sessions()
    assert status == "ok"
    assert sessions is not None
    assert sessions[0]["session_id"] == "s1"


def test_research_reader_missing_snapshot(tmp_path) -> None:
    path = tmp_path / "research.json"
    reader = ResearchSnapshotReader(str(path), max_age_sec=10)
    payload, status = reader.read_feed()
    assert payload is None
    assert status == "missing"


def test_research_reader_dedupes_entries(tmp_path) -> None:
    path = tmp_path / "research.json"
    store = RuntimeStateStore(path)
    store.write_snapshot(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "codex",
            "query": "crypto-policy",
            "items": [
                {
                    "title": "Bitcoin macro update",
                    "summary": "ETF flows remain strong",
                    "source": "codex",
                    "url": "https://example.com/bitcoin",
                },
                {
                    "title": "Bitcoin macro update",
                    "summary": "ETF flows remain strong",
                    "source": "codex",
                    "url": "https://example.com/bitcoin",
                },
                {
                    "title": "Altcoin report",
                    "summary": "Stablecoin reserve grows",
                    "fingerprint": "altcoin-2026",
                    "url": "https://example.com/altcoin",
                },
                "not-a-dict",
            ],
        }
    )

    reader = ResearchSnapshotReader(str(path), max_age_sec=10)
    payload, status = reader.read_feed()
    assert status == "ok"
    assert payload is not None
    assert payload["source"] == "codex"
    assert payload["query"] == "crypto-policy"
    assert payload["count"] == 2
    assert len(payload["items"]) == 2
    ids = [item.get("id") for item in payload["items"]]
    assert len(ids) == len(set(ids))


def test_research_reader_stale_snapshot(tmp_path) -> None:
    path = tmp_path / "research.json"
    store = RuntimeStateStore(path)
    store.write_snapshot(
        {
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "items": [
                {
                    "title": "Old research",
                    "summary": "outdated",
                }
            ],
        }
    )

    reader = ResearchSnapshotReader(str(path), max_age_sec=60)
    payload, status = reader.read_feed()
    assert payload is None
    assert status == "stale"


def test_research_reader_exposes_agent_self_score(tmp_path) -> None:
    path = tmp_path / "research.json"
    store = RuntimeStateStore(path)
    store.write_snapshot(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "research_runner",
            "query": "KRX research",
            "learning_total_count": 14,
            "agent_self_score_100": 87,
            "agent_self_score_note": "최근 리포트 DB와 거시 흐름 일치도 높음",
            "items": [
                {
                    "title": "sample",
                    "summary": "sample summary",
                }
            ],
        }
    )

    reader = ResearchSnapshotReader(str(path), max_age_sec=60)
    payload, status = reader.read_feed()
    assert status == "ok"
    assert payload is not None
    assert payload["agent_self_score_100"] == 87
    assert "일치도" in payload["agent_self_score_note"]
    assert payload["learning_total_count"] == 14
