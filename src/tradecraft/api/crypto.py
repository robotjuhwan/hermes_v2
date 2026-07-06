from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends


@dataclass(frozen=True)
class CryptoRouteDeps:
    require_admin_auth: Callable[..., Any]
    crypto_research_service: Any
    crypto_alpha_service: Any
    crypto_research_symbols: Callable[[Any], list[str]]
    default_crypto_research_symbols: Callable[[], list[str]]


def build_crypto_router(deps: CryptoRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/crypto/research/status")
    async def crypto_research_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = _resolve_service(deps.crypto_research_service)
        return await _maybe_await(service.status())

    @router.get("/api/crypto/research/context")
    async def crypto_research_context(
        symbols: str | None = None,
        limit: int = 20,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        safe_limit = max(min(int(limit), 100), 1)
        symbol_list = deps.crypto_research_symbols(symbols)
        service = _resolve_service(deps.crypto_research_service)
        return await _maybe_await(
            service.latest_context(
                symbols=symbol_list or None,
                limit=safe_limit,
            )
        )

    @router.post("/api/crypto/research/collect")
    async def crypto_research_collect(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        symbols = deps.crypto_research_symbols((payload or {}).get("symbols"))
        if not symbols:
            symbols = deps.default_crypto_research_symbols()
        service = _resolve_service(deps.crypto_research_service)
        return await _maybe_await(
            service.collect_market_structure(symbols)
        )

    @router.post("/api/crypto/research/run-once")
    async def crypto_research_run_once(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        symbols = deps.crypto_research_symbols((payload or {}).get("symbols"))
        if not symbols:
            symbols = deps.default_crypto_research_symbols()
        service = _resolve_service(deps.crypto_research_service)
        return await _maybe_await(
            service.run_research_once(symbols=symbols or None)
        )

    @router.get("/api/crypto/alpha/status")
    async def crypto_alpha_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = _resolve_service(deps.crypto_alpha_service)
        return await _maybe_await(service.status())

    @router.get("/api/crypto/alpha/context")
    async def crypto_alpha_context(
        symbols: str | None = None,
        limit: int = 12,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        safe_limit = max(min(int(limit), 50), 1)
        symbol_list = deps.crypto_research_symbols(symbols)
        service = _resolve_service(deps.crypto_alpha_service)
        return await _maybe_await(
            service.context_pack(
                symbols=symbol_list or None,
                limit=safe_limit,
            )
        )

    @router.post("/api/crypto/alpha/collect")
    async def crypto_alpha_collect(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = _resolve_service(deps.crypto_alpha_service)
        return await _maybe_await(service.collect_once())

    @router.post("/api/crypto/alpha/outcomes/run-once")
    async def crypto_alpha_outcomes_run_once(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = _resolve_service(deps.crypto_alpha_service)
        return await _maybe_await(service.label_due_outcomes())

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _resolve_service(value: Any) -> Any:
    return value() if callable(value) and not hasattr(value, "status") else value
