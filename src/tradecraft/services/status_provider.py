from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable


StatusProvider = Callable[[], dict[str, Any]]


class StatusProviderPool:
    def __init__(self, *, max_workers: int = 16) -> None:
        self.max_workers = max(int(max_workers), 1)
        self._cache: dict[str, tuple[dict[str, Any], str]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def collect(
        self,
        providers: dict[str, StatusProvider],
        *,
        timeout_sec: float,
    ) -> dict[str, dict[str, Any]]:
        if not providers:
            return {}
        executor = ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(providers)),
            thread_name_prefix="tradecraft-status",
        )
        futures: dict[Future[dict[str, Any]], str] = {
            executor.submit(provider): name
            for name, provider in providers.items()
        }
        done, pending = wait(
            futures,
            timeout=max(float(timeout_sec), 0.001),
        )
        results: dict[str, dict[str, Any]] = {}
        for future in done:
            name = futures[future]
            try:
                payload = future.result()
                if not isinstance(payload, dict):
                    raise TypeError("provider returned non-dict payload")
            except Exception as exc:
                results[name] = self._fallback(
                    name,
                    reason="provider_error",
                    error_message=str(exc),
                )
                continue
            result = dict(payload)
            results[name] = result
            if str(result.get("status") or "").strip().lower() != "error":
                cached_at = datetime.now(timezone.utc).isoformat()
                with self._lock:
                    self._cache[name] = (dict(result), cached_at)
        for future in pending:
            name = futures[future]
            future.cancel()
            results[name] = self._fallback(
                name,
                reason="timeout",
                error_message=f"status provider timed out after {timeout_sec:.3f}s",
            )
        executor.shutdown(wait=False, cancel_futures=True)
        return {name: results[name] for name in providers}

    def _fallback(
        self,
        name: str,
        *,
        reason: str,
        error_message: str,
    ) -> dict[str, Any]:
        with self._lock:
            cached = self._cache.get(name)
        if cached is not None:
            payload, cached_at = cached
            return {
                **payload,
                "_status_provider": {
                    "status": "stale_cache",
                    "reason": reason,
                    "cached_at": cached_at,
                    "error_message": error_message,
                },
            }
        return {
            "status": "error",
            "error_message": error_message,
            "_status_provider": {
                "status": "error",
                "reason": reason,
            },
        }
