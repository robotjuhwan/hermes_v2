from __future__ import annotations

import threading
import time

from tradecraft.services.status_provider import StatusProviderPool


def test_status_provider_pool_queries_independent_providers_in_parallel() -> None:
    barrier = threading.Barrier(2)

    def provider(name: str) -> dict[str, str]:
        barrier.wait(timeout=0.5)
        return {"status": "ok", "name": name}

    pool = StatusProviderPool()
    result = pool.collect(
        {
            "first": lambda: provider("first"),
            "second": lambda: provider("second"),
        },
        timeout_sec=0.5,
    )

    assert result["first"] == {"status": "ok", "name": "first"}
    assert result["second"] == {"status": "ok", "name": "second"}


def test_status_provider_pool_uses_last_good_cache_after_timeout() -> None:
    pool = StatusProviderPool()
    first = pool.collect(
        {"wiki": lambda: {"status": "ok", "page_count": 42}},
        timeout_sec=0.5,
    )
    assert first["wiki"]["page_count"] == 42

    started = time.monotonic()
    result = pool.collect(
        {"wiki": lambda: time.sleep(0.2) or {"status": "ok"}},
        timeout_sec=0.02,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert result["wiki"]["status"] == "ok"
    assert result["wiki"]["page_count"] == 42
    assert result["wiki"]["_status_provider"]["status"] == "stale_cache"
    assert result["wiki"]["_status_provider"]["reason"] == "timeout"


def test_status_provider_pool_isolates_failure_without_cache() -> None:
    def fail() -> dict[str, str]:
        raise RuntimeError("provider unavailable")

    result = StatusProviderPool().collect(
        {"reports": fail},
        timeout_sec=0.5,
    )

    assert result["reports"] == {
        "status": "error",
        "error_message": "provider unavailable",
        "_status_provider": {
            "status": "error",
            "reason": "provider_error",
        },
    }
