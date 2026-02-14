from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tradecraft.runtime.state_store import RuntimeStateStore


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RuntimeSnapshotReader:
    def __init__(self, path: str, max_age_sec: int = 90) -> None:
        self.store = RuntimeStateStore(path)
        self.max_age = timedelta(seconds=max(max_age_sec, 1))

    def read_sessions(self) -> tuple[list[dict[str, Any]] | None, str]:
        snapshot = self.store.read_snapshot()
        if not snapshot:
            return None, "missing"

        updated_raw = str(snapshot.get("updated_at") or "").strip()
        updated_at = _parse_iso(updated_raw) if updated_raw else None
        if not updated_at:
            return None, "invalid_timestamp"

        age = datetime.now(timezone.utc) - updated_at
        if age > self.max_age:
            return None, "stale"

        sessions = snapshot.get("sessions")
        if not isinstance(sessions, list):
            return None, "invalid_sessions"

        normalized = [row for row in sessions if isinstance(row, dict)]
        return normalized, "ok"
