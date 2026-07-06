from __future__ import annotations

import inspect
from typing import Any, Callable


def build_crypto_quant_status(repository: Any, *, enabled: bool) -> dict[str, Any]:
    try:
        latest = repository.latest_signals(limit=1)
        return {
            "status": "ok",
            "enabled": bool(enabled),
            "db_path": str(repository.path),
            "latest_signal_count": len(latest),
        }
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}


def build_unavailable_service_status(
    *,
    enabled: bool,
    default_message: str,
    import_error: BaseException | None = None,
) -> dict[str, Any]:
    detail = default_message
    if import_error is not None:
        detail = str(import_error)
    return {
        "status": "unavailable",
        "enabled": bool(enabled),
        "error_message": detail,
    }


async def build_service_status(
    service: Any,
    *,
    unavailable_message: str = "status method unavailable",
) -> dict[str, Any]:
    try:
        status = getattr(service, "status", None)
        if not callable(status):
            return {"status": "unavailable", "error_message": unavailable_message}
        return await _await_if_needed(status())
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}


def build_kr_equity_pattern_lab_status(
    repository_factory: Callable[[Any], Any],
    *,
    db_path: Any,
) -> dict[str, Any]:
    try:
        return repository_factory(db_path).status()
    except Exception as exc:
        return {
            "status": "error",
            "source_scope": "kr_equity_pattern_lab",
            "db_path": str(db_path),
            "error_message": str(exc),
        }


def build_memory_read_only_status(repository: Any) -> dict[str, Any]:
    try:
        return {**repository.status(), "read_only": True}
    except Exception as exc:
        return {"status": "error", "read_only": True, "error_message": str(exc)}


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
