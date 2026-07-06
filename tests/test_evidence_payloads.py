from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tradecraft.api.evidence_payloads import (
    build_crypto_quant_status,
    build_kr_equity_pattern_lab_status,
    build_memory_read_only_status,
    build_service_status,
    build_unavailable_service_status,
)


class _FakeQuantRepository:
    path = Path(".runtime/crypto_quant.db")

    def __init__(self, latest: list[dict[str, Any]] | None = None) -> None:
        self.latest = latest or []
        self.calls: list[dict[str, Any]] = []

    def latest_signals(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append({"method": "latest_signals", "limit": limit})
        return self.latest


class _FailingQuantRepository:
    path = Path(".runtime/crypto_quant.db")

    def latest_signals(self, *, limit: int) -> list[dict[str, Any]]:
        raise RuntimeError("quant db is locked")


class _FakeMemoryRepository:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"status": "ok", "policy_rule_count": 3}

    def status(self) -> dict[str, Any]:
        return dict(self.payload)


class _SyncService:
    def status(self) -> dict[str, Any]:
        return {"status": "ok", "service": "sync"}


class _AsyncService:
    async def status(self) -> dict[str, Any]:
        return {"status": "ok", "service": "async"}


def test_build_crypto_quant_status_reports_latest_signal_count() -> None:
    repository = _FakeQuantRepository(latest=[{"symbol": "BTCUSDT"}])

    payload = build_crypto_quant_status(repository, enabled=True)

    assert payload == {
        "status": "ok",
        "enabled": True,
        "db_path": ".runtime/crypto_quant.db",
        "latest_signal_count": 1,
    }
    assert repository.calls == [{"method": "latest_signals", "limit": 1}]


def test_build_crypto_quant_status_returns_error_payload_on_repository_failure() -> None:
    payload = build_crypto_quant_status(_FailingQuantRepository(), enabled=False)

    assert payload == {"status": "error", "error_message": "quant db is locked"}


def test_build_service_status_handles_sync_and_async_status_methods() -> None:
    assert asyncio.run(build_service_status(_SyncService())) == {
        "status": "ok",
        "service": "sync",
    }
    assert asyncio.run(build_service_status(_AsyncService())) == {
        "status": "ok",
        "service": "async",
    }


def test_build_service_status_reports_unavailable_or_error() -> None:
    assert asyncio.run(build_service_status(object())) == {
        "status": "unavailable",
        "error_message": "status method unavailable",
    }

    class _BrokenService:
        def status(self) -> dict[str, Any]:
            raise RuntimeError("status exploded")

    assert asyncio.run(build_service_status(_BrokenService())) == {
        "status": "error",
        "error_message": "status exploded",
    }


def test_build_unavailable_service_status_uses_import_error_detail() -> None:
    payload = build_unavailable_service_status(
        enabled=True,
        default_message="crypto pattern lab service is unavailable",
        import_error=ImportError("missing dependency"),
    )

    assert payload == {
        "status": "unavailable",
        "enabled": True,
        "error_message": "missing dependency",
    }


def test_build_kr_equity_pattern_lab_status_uses_factory_and_includes_db_path_on_error() -> None:
    def ok_factory(db_path: str) -> _FakeMemoryRepository:
        assert db_path == ".runtime/kr_equity_pattern_lab.db"
        return _FakeMemoryRepository({"status": "ok", "db_path": db_path})

    assert build_kr_equity_pattern_lab_status(
        ok_factory,
        db_path=".runtime/kr_equity_pattern_lab.db",
    ) == {
        "status": "ok",
        "db_path": ".runtime/kr_equity_pattern_lab.db",
    }

    def broken_factory(db_path: str) -> _FakeMemoryRepository:
        raise RuntimeError("kr pattern db missing")

    assert build_kr_equity_pattern_lab_status(
        broken_factory,
        db_path=".runtime/kr_equity_pattern_lab.db",
    ) == {
        "status": "error",
        "source_scope": "kr_equity_pattern_lab",
        "db_path": ".runtime/kr_equity_pattern_lab.db",
        "error_message": "kr pattern db missing",
    }


def test_build_memory_read_only_status_adds_read_only_marker() -> None:
    assert build_memory_read_only_status(_FakeMemoryRepository()) == {
        "status": "ok",
        "policy_rule_count": 3,
        "read_only": True,
    }
