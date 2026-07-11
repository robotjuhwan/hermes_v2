from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, time
from typing import Any, Callable

from fastapi import APIRouter, Depends


STORAGE_REPORT_FILE_CACHE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class RuntimeRouteDeps:
    require_admin_auth: Callable[..., Any]
    runtime_storage_policy: Callable[[], Any]
    build_runtime_storage_report: Callable[[Any], dict[str, Any]]
    cleanup_runtime_storage: Callable[..., dict[str, Any]]
    refresh_cold_archive_status: Callable[[], dict[str, Any]] | None = None
    storage_report_cache_ttl_sec: float = 30.0
    storage_report_file_cache_enabled: bool = False
    storage_report_file_cache_ttl_sec: float | None = None


def _policy_runtime_dir(policy: Any) -> Path:
    if isinstance(policy, dict):
        raw = policy.get("runtime_dir")
    else:
        raw = getattr(policy, "runtime_dir", None)
    return Path(str(raw or ".runtime"))


def _storage_report_cache_path(policy: Any) -> Path:
    return _policy_runtime_dir(policy) / "runtime_storage_report_cache.json"


def _read_storage_report_file_cache(
    policy: Any,
    *,
    ttl_sec: float,
    allow_expired: bool = False,
) -> dict[str, Any] | None:
    if ttl_sec <= 0:
        return None
    path = _storage_report_cache_path(policy)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if (
        int(payload.get("schema_version") or 0)
        != STORAGE_REPORT_FILE_CACHE_SCHEMA_VERSION
    ):
        return None
    cached_at = payload.get("cached_at_epoch")
    try:
        age_sec = time() - float(cached_at)
    except (TypeError, ValueError):
        return None
    if age_sec < 0:
        return None
    cache_expired = age_sec > ttl_sec
    if cache_expired and not allow_expired:
        return None
    report = payload.get("report")
    if not isinstance(report, dict):
        return None
    cached_report = deepcopy(report)
    cached_report["cache_status"] = "fresh_file_cache"
    cached_report["cache_expired"] = False
    cached_report["cache_age_sec"] = round(age_sec, 3)
    cached_report["cache_ttl_sec"] = ttl_sec
    cached_report["refresh_available"] = True
    if cache_expired:
        cached_report["cache_status"] = "stale_file_cache"
        cached_report["cache_expired"] = True
    return cached_report


def _write_storage_report_file_cache(policy: Any, report: dict[str, Any]) -> None:
    path = _storage_report_cache_path(policy)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(
                {
                    "schema_version": STORAGE_REPORT_FILE_CACHE_SCHEMA_VERSION,
                    "cached_at_epoch": time(),
                    "report": report,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        tmp_path.replace(path)
    except (OSError, TypeError, ValueError):
        return


def build_runtime_router(deps: RuntimeRouteDeps) -> APIRouter:
    router = APIRouter()
    cached_storage_report: dict[str, Any] | None = None
    cached_storage_report_at = 0.0

    def fresh_storage_report(policy: Any | None = None) -> dict[str, Any]:
        nonlocal cached_storage_report, cached_storage_report_at
        active_policy = policy if policy is not None else deps.runtime_storage_policy()
        report = deps.build_runtime_storage_report(active_policy)
        cached_storage_report = deepcopy(report)
        cached_storage_report_at = monotonic()
        if deps.storage_report_file_cache_enabled:
            _write_storage_report_file_cache(active_policy, report)
        return report

    def cached_or_fresh_storage_report(*, refresh: bool = False) -> dict[str, Any]:
        nonlocal cached_storage_report, cached_storage_report_at
        policy = deps.runtime_storage_policy()
        if refresh:
            return fresh_storage_report(policy)
        ttl_sec = max(float(deps.storage_report_cache_ttl_sec), 0.0)
        if (
            ttl_sec > 0
            and cached_storage_report is not None
            and monotonic() - cached_storage_report_at <= ttl_sec
        ):
            return deepcopy(cached_storage_report)
        if deps.storage_report_file_cache_enabled:
            file_ttl_sec = (
                ttl_sec
                if deps.storage_report_file_cache_ttl_sec is None
                else max(float(deps.storage_report_file_cache_ttl_sec), 0.0)
            )
            report = _read_storage_report_file_cache(policy, ttl_sec=file_ttl_sec)
            if report is not None:
                cached_storage_report = deepcopy(report)
                cached_storage_report_at = monotonic()
                return report
            stale_report = _read_storage_report_file_cache(
                policy,
                ttl_sec=file_ttl_sec,
                allow_expired=True,
            )
            if stale_report is not None:
                cached_storage_report = deepcopy(stale_report)
                cached_storage_report_at = monotonic()
                return stale_report
        return fresh_storage_report(policy)

    @router.get("/api/runtime/storage/status")
    @router.get("/api/runtime/status")
    @router.get("/api/runtime/storage")
    async def runtime_storage_status(
        refresh: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return cached_or_fresh_storage_report(refresh=refresh)

    @router.get("/api/runtime/storage-cleanup")
    async def runtime_storage_cleanup_legacy_dry_run(
        compact_databases: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.cleanup_runtime_storage(
            deps.runtime_storage_policy(),
            dry_run=True,
            compact_databases=compact_databases,
        )
        result["after"] = fresh_storage_report()
        return result

    @router.post("/api/runtime/storage/cleanup")
    async def runtime_storage_cleanup(
        dry_run: bool = True,
        compact_databases: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.cleanup_runtime_storage(
            deps.runtime_storage_policy(),
            dry_run=dry_run,
            compact_databases=compact_databases,
        )
        if (
            not dry_run
            and list(result.get("archived") or [])
            and deps.refresh_cold_archive_status is not None
        ):
            result["cold_archive_verification"] = (
                deps.refresh_cold_archive_status()
            )
        result["after"] = fresh_storage_report()
        return result

    return router
