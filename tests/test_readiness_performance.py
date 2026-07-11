from __future__ import annotations

import time

from tradecraft import main


def _reset_readiness_caches() -> None:
    main._ops_readiness_cache_payload = None
    main._ops_readiness_cache_expires_at = 0.0
    main._ops_readiness_cache_key = None
    main._ops_compact_readiness_cache_payload = None
    main._ops_compact_readiness_cache_expires_at = 0.0
    main._ops_compact_readiness_cache_key = None
    main._ops_status_provider_pool.clear()


def test_compact_and_full_readiness_meet_latency_budgets() -> None:
    _reset_readiness_caches()
    started = time.perf_counter()
    compact = main._build_ops_readiness_compact_cached()
    compact_cold_sec = time.perf_counter() - started

    warm_samples: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        main._build_ops_readiness_compact_cached()
        warm_samples.append(time.perf_counter() - started)
    warm_p95_sec = sorted(warm_samples)[18]

    _reset_readiness_caches()
    started = time.perf_counter()
    full = main._build_ops_readiness()
    full_cold_sec = time.perf_counter() - started

    assert compact["compact"] is True
    assert compact_cold_sec <= 2.0
    assert warm_p95_sec <= 0.5
    assert full["status"] in {"green", "yellow", "red"}
    assert full_cold_sec <= 2.0


def test_published_readiness_helpers_only_read_snapshot(monkeypatch) -> None:
    class Snapshot:
        def current_full(self) -> dict[str, object]:
            return {"status": "green", "warnings": [], "blockers": []}

        def current_compact(self) -> dict[str, object]:
            return {
                "compact": True,
                "status": "green",
                "warnings": [],
                "blockers": [],
            }

    monkeypatch.setattr(
        main,
        "_ops_readiness_snapshot_coordinator_instance",
        Snapshot(),
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "_build_ops_readiness",
        lambda: (_ for _ in ()).throw(AssertionError("fresh projection called")),
    )

    assert main._published_ops_readiness()["status"] == "green"
    assert main._published_ops_readiness_compact()["compact"] is True
