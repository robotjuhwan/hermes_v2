from __future__ import annotations

import json
from time import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.runtime import RuntimeRouteDeps, build_runtime_router


def test_runtime_storage_status_uses_current_policy() -> None:
    calls: list[Any] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": ".runtime"}

    def _report(policy: Any) -> dict[str, Any]:
        calls.append(policy)
        return {"status": "ok", "policy": policy}

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_report,
                cleanup_runtime_storage=lambda policy, dry_run: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/runtime/storage")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "policy": {"runtime_dir": ".runtime"}}
    assert calls == [{"runtime_dir": ".runtime"}]


def test_runtime_storage_legacy_status_alias_matches_status_report() -> None:
    calls: list[Any] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": ".runtime"}

    def _report(policy: Any) -> dict[str, Any]:
        calls.append(policy)
        return {"status": "ok", "policy": policy}

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_report,
                cleanup_runtime_storage=lambda policy, dry_run: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/runtime/storage/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "policy": {"runtime_dir": ".runtime"}}
    assert calls == [{"runtime_dir": ".runtime"}]


def test_runtime_legacy_status_alias_matches_storage_report() -> None:
    calls: list[Any] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": ".runtime"}

    def _report(policy: Any) -> dict[str, Any]:
        calls.append(policy)
        return {"status": "ok", "policy": policy}

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_report,
                cleanup_runtime_storage=lambda policy, dry_run: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/runtime/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "policy": {"runtime_dir": ".runtime"}}
    assert calls == [{"runtime_dir": ".runtime"}]


def test_runtime_storage_status_uses_persisted_cache_after_router_restart(
    tmp_path,
) -> None:
    calls: list[Any] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": str(tmp_path)}

    def _report(policy: Any) -> dict[str, Any]:
        calls.append(policy)
        return {
            "status": "ok",
            "runtime_dir": policy["runtime_dir"],
            "marker": "fresh-report",
        }

    first_app = FastAPI()
    first_app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_report,
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=3600,
                storage_report_file_cache_enabled=True,
            )
        )
    )
    with TestClient(first_app) as client:
        first_response = client.get("/api/runtime/storage")

    assert first_response.status_code == 200
    assert first_response.json()["marker"] == "fresh-report"
    assert calls == [{"runtime_dir": str(tmp_path)}]

    def _unexpected_rebuild(policy: Any) -> dict[str, Any]:
        raise AssertionError(f"storage report should use file cache, got {policy}")

    second_app = FastAPI()
    second_app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_unexpected_rebuild,
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=3600,
                storage_report_file_cache_enabled=True,
            )
        )
    )
    with TestClient(second_app) as client:
        second_response = client.get("/api/runtime/storage")

    assert second_response.status_code == 200
    assert second_response.json()["marker"] == "fresh-report"
    assert second_response.json()["cache_status"] == "fresh_file_cache"
    assert second_response.json()["cache_expired"] is False


def test_runtime_storage_file_cache_can_outlive_memory_cache_ttl(tmp_path) -> None:
    def _policy() -> dict[str, Any]:
        return {"runtime_dir": str(tmp_path)}

    first_app = FastAPI()
    first_app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=lambda policy: {
                    "status": "ok",
                    "runtime_dir": policy["runtime_dir"],
                    "marker": "file-cache-report",
                },
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=1,
                storage_report_file_cache_enabled=True,
                storage_report_file_cache_ttl_sec=3600,
            )
        )
    )
    with TestClient(first_app) as client:
        first_response = client.get("/api/runtime/storage")

    assert first_response.status_code == 200
    cache_path = tmp_path / "runtime_storage_report_cache.json"
    payload = json.loads(cache_path.read_text())
    payload["cached_at_epoch"] = time() - 120
    cache_path.write_text(json.dumps(payload))

    def _unexpected_rebuild(policy: Any) -> dict[str, Any]:
        raise AssertionError(f"storage report should use long file cache, got {policy}")

    second_app = FastAPI()
    second_app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_unexpected_rebuild,
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=1,
                storage_report_file_cache_enabled=True,
                storage_report_file_cache_ttl_sec=3600,
            )
        )
    )
    with TestClient(second_app) as client:
        second_response = client.get("/api/runtime/storage")

    assert second_response.status_code == 200
    assert second_response.json()["marker"] == "file-cache-report"


def test_runtime_storage_file_cache_ignores_legacy_schema_payload(tmp_path) -> None:
    calls: list[Any] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": str(tmp_path)}

    legacy_cache = {
        "cached_at_epoch": time(),
        "report": {
            "status": "ok",
            "marker": "legacy-cache-report",
        },
    }
    (tmp_path / "runtime_storage_report_cache.json").write_text(
        json.dumps(legacy_cache)
    )

    def _report(policy: Any) -> dict[str, Any]:
        calls.append(policy)
        return {
            "status": "ok",
            "runtime_dir": policy["runtime_dir"],
            "marker": "fresh-schema-report",
        }

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_report,
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=3600,
                storage_report_file_cache_enabled=True,
            )
        )
    )
    with TestClient(app) as client:
        response = client.get("/api/runtime/storage")

    assert response.status_code == 200
    assert response.json()["marker"] == "fresh-schema-report"
    assert calls == [{"runtime_dir": str(tmp_path)}]


def test_runtime_storage_status_serves_expired_file_cache_without_rebuilding(
    tmp_path,
) -> None:
    def _policy() -> dict[str, Any]:
        return {"runtime_dir": str(tmp_path)}

    first_app = FastAPI()
    first_app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=lambda policy: {
                    "status": "ok",
                    "runtime_dir": policy["runtime_dir"],
                    "marker": "stale-report",
                },
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=1,
                storage_report_file_cache_enabled=True,
                storage_report_file_cache_ttl_sec=1,
            )
        )
    )
    with TestClient(first_app) as client:
        first_response = client.get("/api/runtime/storage")

    assert first_response.status_code == 200
    cache_path = tmp_path / "runtime_storage_report_cache.json"
    payload = json.loads(cache_path.read_text())
    payload["cached_at_epoch"] = time() - 120
    cache_path.write_text(json.dumps(payload))

    def _unexpected_rebuild(policy: Any) -> dict[str, Any]:
        raise AssertionError(f"storage report should use stale file cache, got {policy}")

    second_app = FastAPI()
    second_app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_unexpected_rebuild,
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=1,
                storage_report_file_cache_enabled=True,
                storage_report_file_cache_ttl_sec=1,
            )
        )
    )
    with TestClient(second_app) as client:
        second_response = client.get("/api/runtime/storage")

    assert second_response.status_code == 200
    assert second_response.json()["marker"] == "stale-report"
    assert second_response.json()["cache_status"] == "stale_file_cache"
    assert second_response.json()["cache_expired"] is True

    refresh_app = FastAPI()
    refresh_app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=lambda policy: {
                    "status": "ok",
                    "runtime_dir": policy["runtime_dir"],
                    "marker": "fresh-after-refresh",
                },
                cleanup_runtime_storage=lambda policy, dry_run: {},
                storage_report_cache_ttl_sec=1,
                storage_report_file_cache_enabled=True,
                storage_report_file_cache_ttl_sec=1,
            )
        )
    )
    with TestClient(refresh_app) as client:
        refresh_response = client.get("/api/runtime/storage?refresh=true")

    assert refresh_response.status_code == 200
    assert refresh_response.json()["marker"] == "fresh-after-refresh"


def test_runtime_storage_legacy_cleanup_get_is_dry_run_only() -> None:
    cleanup_calls: list[tuple[Any, bool, bool]] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": ".runtime"}

    def _cleanup(
        policy: Any,
        *,
        dry_run: bool,
        compact_databases: bool = False,
    ) -> dict[str, Any]:
        cleanup_calls.append((policy, dry_run, compact_databases))
        return {"status": "ok", "dry_run": dry_run, "deleted": []}

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=lambda policy: {"status": "ok"},
                cleanup_runtime_storage=_cleanup,
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/runtime/storage-cleanup?dry_run=false")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dry_run": True,
        "deleted": [],
        "after": {"status": "ok"},
    }
    assert cleanup_calls == [({"runtime_dir": ".runtime"}, True, False)]


def test_runtime_storage_cleanup_attaches_after_report() -> None:
    cleanup_calls: list[tuple[Any, bool, bool]] = []
    verification_calls = 0

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": ".runtime"}

    def _report(policy: Any) -> dict[str, Any]:
        return {"status": "ok", "runtime_dir": policy["runtime_dir"]}

    def _cleanup(
        policy: Any,
        *,
        dry_run: bool,
        compact_databases: bool = False,
    ) -> dict[str, Any]:
        cleanup_calls.append((policy, dry_run, compact_databases))
        return {
            "status": "ok",
            "deleted": ["old.pdf"],
            "archived": [{"entry_id": "archive-1", "verified": True}],
        }

    def _refresh_cold_archive_status() -> dict[str, Any]:
        nonlocal verification_calls
        verification_calls += 1
        return {
            "status": "ok",
            "entry_count": 1,
            "verification_snapshot": {"status": "current"},
        }

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_report,
                cleanup_runtime_storage=_cleanup,
                refresh_cold_archive_status=_refresh_cold_archive_status,
            )
        )
    )

    with TestClient(app) as client:
        response = client.post("/api/runtime/storage/cleanup?dry_run=false")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "deleted": ["old.pdf"],
        "archived": [{"entry_id": "archive-1", "verified": True}],
        "cold_archive_verification": {
            "status": "ok",
            "entry_count": 1,
            "verification_snapshot": {"status": "current"},
        },
        "after": {"status": "ok", "runtime_dir": ".runtime"},
    }
    assert cleanup_calls == [({"runtime_dir": ".runtime"}, False, False)]
    assert verification_calls == 1


def test_runtime_storage_cleanup_dry_run_refreshes_after_report() -> None:
    report_calls: list[Any] = []
    cleanup_calls: list[tuple[Any, bool, bool]] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": ".runtime"}

    def _report(policy: Any) -> dict[str, Any]:
        report_calls.append(policy)
        return {
            "status": "ok",
            "runtime_dir": policy["runtime_dir"],
            "marker": f"report-{len(report_calls)}",
        }

    def _cleanup(
        policy: Any,
        *,
        dry_run: bool,
        compact_databases: bool = False,
    ) -> dict[str, Any]:
        cleanup_calls.append((policy, dry_run, compact_databases))
        return {"status": "ok", "dry_run": dry_run, "deleted": []}

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=_report,
                cleanup_runtime_storage=_cleanup,
                storage_report_cache_ttl_sec=3600,
            )
        )
    )

    with TestClient(app) as client:
        first = client.get("/api/runtime/storage")
        dry_run = client.post("/api/runtime/storage/cleanup?dry_run=true")

    assert first.status_code == 200
    assert dry_run.status_code == 200
    assert dry_run.json()["after"]["marker"] == "report-2"
    assert report_calls == [{"runtime_dir": ".runtime"}, {"runtime_dir": ".runtime"}]
    assert cleanup_calls == [({"runtime_dir": ".runtime"}, True, False)]


def test_runtime_storage_cleanup_forwards_database_compaction_flag() -> None:
    cleanup_calls: list[tuple[Any, bool, bool]] = []

    def _policy() -> dict[str, Any]:
        return {"runtime_dir": ".runtime"}

    def _cleanup(
        policy: Any,
        *,
        dry_run: bool,
        compact_databases: bool = False,
    ) -> dict[str, Any]:
        cleanup_calls.append((policy, dry_run, compact_databases))
        return {"status": "ok"}

    app = FastAPI()
    app.include_router(
        build_runtime_router(
            RuntimeRouteDeps(
                require_admin_auth=lambda: None,
                runtime_storage_policy=_policy,
                build_runtime_storage_report=lambda policy: {"status": "ok"},
                cleanup_runtime_storage=_cleanup,
            )
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/runtime/storage/cleanup?dry_run=false&compact_databases=true"
        )

    assert response.status_code == 200
    assert cleanup_calls == [({"runtime_dir": ".runtime"}, False, True)]
