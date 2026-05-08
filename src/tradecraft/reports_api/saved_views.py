from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradecraft.runtime.state_store import utc_now_iso

_FILTER_KEYS = (
    "query",
    "symbol",
    "category",
    "broker",
    "analyst",
    "date_from",
    "date_to",
)


def _normalize_filters(payload: dict[str, Any]) -> dict[str, Any]:
    out = {key: str(payload.get(key) or "").strip() for key in _FILTER_KEYS}
    raw_limit = payload.get("limit", 20)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 20
    out["limit"] = max(1, min(limit, 100))
    return out


def _normalize_alert(payload: dict[str, Any]) -> dict[str, Any]:
    channel = str(payload.get("channel") or "telegram").strip().lower() or "telegram"
    return {
        "enabled": bool(payload.get("enabled")),
        "channel": channel,
        "target": str(payload.get("target") or "").strip(),
    }


class ReportsSavedViewStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def list_views(self) -> list[dict[str, Any]]:
        rows = self._load()
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return rows

    def get_view(self, view_id: str) -> dict[str, Any] | None:
        needle = str(view_id or "").strip()
        if not needle:
            return None
        for row in self._load():
            if str(row.get("view_id") or "") == needle:
                return row
        return None

    def save_view(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("saved view name is required")

        now = utc_now_iso()
        target_id = str(payload.get("view_id") or "").strip() or uuid4().hex[:12]
        filters = _normalize_filters(dict(payload.get("filters") or {}))
        alert = _normalize_alert(dict(payload.get("alert") or {}))

        rows = self._load()
        existing = self.get_view(target_id)
        created_at = str((existing or {}).get("created_at") or now)
        row = {
            "view_id": target_id,
            "name": name[:80],
            "filters": filters,
            "alert": alert,
            "created_at": created_at,
            "updated_at": now,
        }

        replaced = False
        next_rows: list[dict[str, Any]] = []
        for item in rows:
            if str(item.get("view_id") or "") == target_id:
                next_rows.append(row)
                replaced = True
            else:
                next_rows.append(item)
        if not replaced:
            next_rows.append(row)
        self._write(next_rows)
        return row

    def delete_view(self, view_id: str) -> bool:
        needle = str(view_id or "").strip()
        if not needle:
            return False
        rows = self._load()
        next_rows = [
            row for row in rows if str(row.get("view_id") or "").strip() != needle
        ]
        removed = len(next_rows) != len(rows)
        if removed:
            self._write(next_rows)
        return removed

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        out: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            view_id = str(item.get("view_id") or "").strip()
            if not view_id:
                continue
            out.append(
                {
                    "view_id": view_id,
                    "name": str(item.get("name") or "").strip(),
                    "filters": _normalize_filters(dict(item.get("filters") or {})),
                    "alert": _normalize_alert(dict(item.get("alert") or {})),
                    "created_at": str(item.get("created_at") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                }
            )
        return out

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
