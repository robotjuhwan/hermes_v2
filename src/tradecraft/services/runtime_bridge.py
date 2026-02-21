from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Iterable
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


def _text_or_empty(value: Any) -> str:
    return str(value or "").strip()


def _score_100(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        score = int(round(float(value)))
        return max(0, min(score, 100))
    text = str(value).strip()
    if not text:
        return None
    try:
        score = int(round(float(text)))
    except ValueError:
        return None
    return max(0, min(score, 100))


def _non_negative_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        if not value == value:
            return 0
        return max(int(round(value)), 0)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return max(int(round(float(text))), 0)
    except ValueError:
        return 0


def _research_item_id(row: dict[str, Any]) -> str:
    query = _text_or_empty(row.get("query"))
    source = _text_or_empty(row.get("source") or row.get("provider"))
    title = _text_or_empty(row.get("title") or row.get("headline"))
    url = _text_or_empty(row.get("url") or row.get("link"))
    summary = _text_or_empty(row.get("summary") or row.get("excerpt"))

    key = "|".join([source, query, title, url, summary])
    if not key.strip("|"):
        return hashlib.sha256(repr(row).encode("utf-8")).hexdigest()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ResearchSnapshotReader:
    def __init__(self, path: str, max_age_sec: int = 3600, limit: int = 20) -> None:
        self.store = RuntimeStateStore(path)
        self.max_age = timedelta(seconds=max(max_age_sec, 1))
        self.limit = max(limit, 1)

    def _iter_items(self, items: Iterable[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for item in items:
            if not isinstance(item, dict):
                continue

            payload = {
                str(key): value for key, value in item.items() if isinstance(key, str)
            }
            item_id = _text_or_empty(payload.get("id") or payload.get("fingerprint"))
            if not item_id:
                item_id = _research_item_id(payload)
                payload["id"] = item_id
                payload["fingerprint"] = item_id

            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            payload.setdefault("status", "ok")
            normalized.append(payload)

            if len(normalized) >= self.limit:
                break

        return normalized

    def read_feed(self) -> tuple[dict[str, Any] | None, str]:
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

        items = snapshot.get("items")
        if items is None:
            items = []
        elif not isinstance(items, list):
            return None, "invalid_items"

        normalized = self._iter_items(items)
        agent_self_score_100 = _score_100(snapshot.get("agent_self_score_100"))
        agent_self_score_note = _text_or_empty(snapshot.get("agent_self_score_note"))
        learning_total_count = _non_negative_int(snapshot.get("learning_total_count"))
        return {
            "updated_at": updated_raw,
            "source": _text_or_empty(
                snapshot.get("source") or snapshot.get("provider") or "scheduled"
            )
            or "scheduled",
            "query": _text_or_empty(
                snapshot.get("query") or snapshot.get("topic") or "general"
            ),
            "status": "ok",
            "count": len(normalized),
            "learning_total_count": learning_total_count,
            "items": normalized,
            "agent_self_score_100": agent_self_score_100,
            "agent_self_score_note": agent_self_score_note,
        }, "ok"
