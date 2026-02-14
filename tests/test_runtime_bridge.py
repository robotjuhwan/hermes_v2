from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.runtime_bridge import RuntimeSnapshotReader


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
            "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
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
