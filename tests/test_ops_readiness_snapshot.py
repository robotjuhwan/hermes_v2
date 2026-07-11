from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.ops_readiness_snapshot import (
    OpsReadinessSnapshotConfig,
    OpsReadinessSnapshotCoordinator,
)


def _now(hour: int = 0) -> datetime:
    return datetime(2026, 7, 10, hour, 0, tzinfo=timezone.utc)


def test_current_reads_published_payload_without_calling_builder(
    tmp_path: Path,
) -> None:
    calls = 0

    def builder() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "status": "green",
            "checked_at": _now().isoformat(),
            "warnings": [],
            "blockers": [],
            "advisories": ["trading_validation_probe_kis"],
        }

    coordinator = OpsReadinessSnapshotCoordinator(
        builder=builder,
        compact_builder=lambda payload: {"compact": True, **payload},
        config=OpsReadinessSnapshotConfig(path=tmp_path / "ops.json"),
        now=_now,
    )
    coordinator.refresh()
    calls_after_refresh = calls

    assert coordinator.current_full()["status"] == "green"
    assert coordinator.current_compact()["compact"] is True
    assert calls == calls_after_refresh


def test_missing_snapshot_returns_bounded_warning_without_refresh(
    tmp_path: Path,
) -> None:
    coordinator = OpsReadinessSnapshotCoordinator(
        builder=lambda: (_ for _ in ()).throw(AssertionError("must not refresh")),
        compact_builder=lambda payload: payload,
        config=OpsReadinessSnapshotConfig(path=tmp_path / "missing.json"),
        now=_now,
    )

    assert coordinator.current_full() == {
        "status": "yellow",
        "blockers": [],
        "warnings": ["ops_readiness_snapshot_missing"],
        "advisories": [],
        "snapshot": {"status": "missing"},
    }


def test_failed_refresh_keeps_last_known_good_snapshot(tmp_path: Path) -> None:
    responses: list[dict[str, Any] | Exception] = [
        {
            "status": "green",
            "checked_at": _now().isoformat(),
            "warnings": [],
            "blockers": [],
        },
        RuntimeError("provider failed"),
    ]

    def builder() -> dict[str, Any]:
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    path = tmp_path / "ops.json"
    coordinator = OpsReadinessSnapshotCoordinator(
        builder=builder,
        compact_builder=lambda payload: {"compact": True, **payload},
        config=OpsReadinessSnapshotConfig(path=path),
        now=_now,
    )
    assert coordinator.refresh()["status"] == "ok"
    before = path.read_bytes()

    failed = coordinator.refresh()

    assert failed["status"] == "error"
    assert path.read_bytes() == before
    assert coordinator.current_full()["status"] == "green"
    assert coordinator.status()["last_refresh_error"] == "provider failed"


def test_stale_snapshot_keeps_last_good_payload_with_operational_warning(
    tmp_path: Path,
) -> None:
    clock = [_now()]
    coordinator = OpsReadinessSnapshotCoordinator(
        builder=lambda: {
            "status": "green",
            "checked_at": _now().isoformat(),
            "warnings": [],
            "blockers": [],
            "advisories": ["trading_validation_probe_kis"],
        },
        compact_builder=lambda payload: {"compact": True, **payload},
        config=OpsReadinessSnapshotConfig(
            path=tmp_path / "ops.json",
            max_age_sec=60,
        ),
        now=lambda: clock[0],
    )
    coordinator.refresh()
    clock[0] = _now() + timedelta(seconds=61)

    payload = coordinator.current_full()

    assert payload["status"] == "yellow"
    assert payload["warnings"] == ["ops_readiness_snapshot_stale"]
    assert payload["advisories"] == ["trading_validation_probe_kis"]
    assert payload["snapshot"]["status"] == "stale"


def test_new_coordinator_loads_persisted_snapshot_without_projection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ops.json"
    writer = OpsReadinessSnapshotCoordinator(
        builder=lambda: {
            "status": "green",
            "checked_at": _now().isoformat(),
            "warnings": [],
            "blockers": [],
        },
        compact_builder=lambda payload: {"compact": True, **payload},
        config=OpsReadinessSnapshotConfig(path=path),
        now=_now,
    )
    writer.refresh()

    reader = OpsReadinessSnapshotCoordinator(
        builder=lambda: (_ for _ in ()).throw(AssertionError("must not project")),
        compact_builder=lambda payload: payload,
        config=OpsReadinessSnapshotConfig(path=path),
        now=_now,
    )

    assert reader.current_full()["status"] == "green"
    assert reader.current_compact()["compact"] is True


def test_ensure_current_skips_projection_while_snapshot_is_fresh(
    tmp_path: Path,
) -> None:
    calls = 0

    def builder() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "status": "green",
            "checked_at": _now().isoformat(),
            "warnings": [],
            "blockers": [],
        }

    coordinator = OpsReadinessSnapshotCoordinator(
        builder=builder,
        compact_builder=lambda payload: {"compact": True, **payload},
        config=OpsReadinessSnapshotConfig(path=tmp_path / "ops.json"),
        now=_now,
    )
    coordinator.refresh()

    result = coordinator.ensure_current()

    assert result["status"] == "fresh"
    assert calls == 1
