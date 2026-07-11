from __future__ import annotations

import asyncio
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from tradecraft.runtime.state_store import RuntimeStateStore


OPS_READINESS_SNAPSHOT_VERSION = "ops_readiness_snapshot_v1"

ReadinessBuilder = Callable[[], dict[str, Any]]
CompactReadinessBuilder = Callable[[dict[str, Any]], dict[str, Any]]
NowFn = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


@dataclass(frozen=True)
class OpsReadinessSnapshotConfig:
    path: Path | str
    refresh_interval_sec: float = 15.0
    max_age_sec: float = 60.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(
            self,
            "refresh_interval_sec",
            max(float(self.refresh_interval_sec), 0.1),
        )
        object.__setattr__(self, "max_age_sec", max(float(self.max_age_sec), 1.0))


@dataclass(frozen=True)
class PublishedOpsReadinessV1:
    generated_at: str
    source_at: str
    fresh_until: str
    full: dict[str, Any]
    compact: dict[str, Any]
    version: str = OPS_READINESS_SNAPSHOT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PublishedOpsReadinessV1 | None:
        if str(value.get("version") or "") != OPS_READINESS_SNAPSHOT_VERSION:
            return None
        full = value.get("full")
        compact = value.get("compact")
        if not isinstance(full, dict) or not isinstance(compact, dict):
            return None
        generated_at = str(value.get("generated_at") or "")
        fresh_until = str(value.get("fresh_until") or "")
        if _parse_datetime(generated_at) is None or _parse_datetime(fresh_until) is None:
            return None
        return cls(
            generated_at=generated_at,
            source_at=str(value.get("source_at") or generated_at),
            fresh_until=fresh_until,
            full=dict(full),
            compact=dict(compact),
        )


def _unavailable_payload(reason: str) -> dict[str, Any]:
    return {
        "status": "yellow",
        "blockers": [],
        "warnings": [f"ops_readiness_snapshot_{reason}"],
        "advisories": [],
        "snapshot": {"status": reason},
    }


class OpsReadinessSnapshotCoordinator:
    def __init__(
        self,
        *,
        builder: ReadinessBuilder,
        compact_builder: CompactReadinessBuilder,
        config: OpsReadinessSnapshotConfig,
        now: NowFn = _utc_now,
    ) -> None:
        self._builder = builder
        self._compact_builder = compact_builder
        self.config = config
        self._now = now
        self._store = RuntimeStateStore(config.path)
        self._snapshot: PublishedOpsReadinessV1 | None = None
        self._load_attempted = False
        self._last_refresh_error = ""
        self._last_refresh_error_at = ""
        self._lock = threading.RLock()

    def refresh(self) -> dict[str, Any]:
        try:
            full = self._builder()
            if not isinstance(full, dict):
                raise TypeError("readiness builder returned non-dict payload")
            full_payload = deepcopy(full)
            compact = self._compact_builder(deepcopy(full_payload))
            if not isinstance(compact, dict):
                raise TypeError("compact readiness builder returned non-dict payload")
            compact_payload = deepcopy(compact)
            compact_payload["compact"] = True
            now = _as_utc(self._now())
            generated_at = now.isoformat()
            snapshot = PublishedOpsReadinessV1(
                generated_at=generated_at,
                source_at=str(full_payload.get("checked_at") or generated_at),
                fresh_until=(
                    now + timedelta(seconds=self.config.max_age_sec)
                ).isoformat(),
                full=full_payload,
                compact=compact_payload,
            )
            self._store.write_snapshot(snapshot.to_dict())
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            with self._lock:
                self._last_refresh_error = error_message
                self._last_refresh_error_at = _as_utc(self._now()).isoformat()
            return {"status": "error", "error_message": error_message}

        with self._lock:
            self._snapshot = snapshot
            self._load_attempted = True
            self._last_refresh_error = ""
            self._last_refresh_error_at = ""
        return {
            "status": "ok",
            "version": snapshot.version,
            "generated_at": snapshot.generated_at,
        }

    def _published_snapshot(self) -> tuple[PublishedOpsReadinessV1 | None, str]:
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot, ""
            if self._load_attempted:
                reason = "corrupt" if Path(self.config.path).exists() else "missing"
                return None, reason
            self._load_attempted = True

        stored = self._store.read_snapshot()
        parsed = PublishedOpsReadinessV1.from_dict(stored) if stored else None
        with self._lock:
            if parsed is not None:
                self._snapshot = parsed
                return parsed, ""
        reason = "corrupt" if Path(self.config.path).exists() else "missing"
        return None, reason

    def _materialize(self, *, compact: bool) -> dict[str, Any]:
        snapshot, unavailable_reason = self._published_snapshot()
        if snapshot is None:
            payload = _unavailable_payload(unavailable_reason or "missing")
            if compact:
                payload["compact"] = True
            return payload

        payload = deepcopy(snapshot.compact if compact else snapshot.full)
        fresh_until = _parse_datetime(snapshot.fresh_until)
        stale = fresh_until is None or _as_utc(self._now()) > fresh_until
        snapshot_status = "stale" if stale else "fresh"
        payload["snapshot"] = {
            "status": snapshot_status,
            "version": snapshot.version,
            "generated_at": snapshot.generated_at,
            "source_at": snapshot.source_at,
            "fresh_until": snapshot.fresh_until,
        }
        if stale:
            warnings = list(payload.get("warnings") or [])
            if "ops_readiness_snapshot_stale" not in warnings:
                warnings.append("ops_readiness_snapshot_stale")
            payload["warnings"] = warnings
            if not list(payload.get("blockers") or []):
                payload["status"] = "yellow"
        if compact:
            payload["compact"] = True
        return payload

    def current_full(self) -> dict[str, Any]:
        return self._materialize(compact=False)

    def current_compact(self) -> dict[str, Any]:
        return self._materialize(compact=True)

    def ensure_current(self) -> dict[str, Any]:
        snapshot, _ = self._published_snapshot()
        fresh_until = _parse_datetime(snapshot.fresh_until) if snapshot else None
        if snapshot is not None and fresh_until is not None:
            if _as_utc(self._now()) <= fresh_until:
                return {
                    "status": "fresh",
                    "version": snapshot.version,
                    "generated_at": snapshot.generated_at,
                }
        return self.refresh()

    def status(self) -> dict[str, Any]:
        snapshot, unavailable_reason = self._published_snapshot()
        with self._lock:
            last_error = self._last_refresh_error
            last_error_at = self._last_refresh_error_at
        return {
            "status": "ok" if snapshot is not None else unavailable_reason or "missing",
            "version": OPS_READINESS_SNAPSHOT_VERSION,
            "path": str(self.config.path),
            "generated_at": snapshot.generated_at if snapshot else "",
            "fresh_until": snapshot.fresh_until if snapshot else "",
            "last_refresh_error": last_error,
            "last_refresh_error_at": last_error_at,
        }

    async def run(self, stop_event: asyncio.Event) -> None:
        interval = max(float(self.config.refresh_interval_sec), 0.1)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                await asyncio.to_thread(self.refresh)
