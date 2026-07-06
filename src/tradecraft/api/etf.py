from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class ETFRouteDeps:
    require_admin_auth: Callable[..., Any]
    repository_factory: Callable[[], Any]
    configured_universe: Callable[[], list[Any]]
    expanded_universe: Callable[[list[Any]], list[Any]]
    universe_item_payload: Callable[[Any], dict[str, Any]]
    settings_payload: Callable[[], dict[str, Any]]
    list_candidates: Callable[[Any, list[Any]], list[dict[str, Any]]]
    read_only_auto_collect: Callable[[], dict[str, Any]]
    seed_universe: Callable[[Any], list[Any]]
    symbols_from_payload: Callable[[dict[str, Any] | None, list[Any]], list[str]]
    collect_snapshots: Callable[..., Any]
    fetch_quote: Callable[..., Any]


def etf_universe_item_payload(item: Any) -> dict[str, Any]:
    return {
        "symbol": item.symbol,
        "name": item.name,
        "category": item.category,
        "tags": list(item.tags),
    }


def etf_symbols_from_payload(
    payload: dict[str, Any] | None,
    configured: list[Any],
    *,
    max_symbols: int,
) -> list[str]:
    configured_symbols = [
        str(item.get("symbol") if isinstance(item, dict) else item.symbol)
        for item in configured
    ]
    limit = max(int(max_symbols), 0)
    if payload is None or "symbols" not in payload:
        return configured_symbols[:limit]
    requested = payload.get("symbols")
    if not isinstance(requested, list):
        raise HTTPException(status_code=400, detail="symbols must be a list")
    if not requested:
        raise HTTPException(status_code=400, detail="symbols must not be empty")
    invalid = [
        item
        for item in requested
        if (
            not isinstance(item, str)
            or not item.strip().isdigit()
            or len(item.strip()) != 6
        )
    ]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail="symbols must contain 6 digit codes",
        )
    symbols = [
        symbol
        for symbol in dict.fromkeys(str(item or "").strip() for item in requested)
    ]
    return symbols[:limit]


def etf_auto_collect_skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason, "auto": True}


def etf_read_only_auto_collect() -> dict[str, Any]:
    return {"status": "skipped", "reason": "read_only_endpoint", "auto": False}


def build_etf_router(deps: ETFRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/etf/research/status")
    async def etf_research_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        repository = deps.repository_factory()
        configured = deps.configured_universe()
        universe = deps.expanded_universe(configured)
        repository_status = repository.status()
        settings_payload = deps.settings_payload()
        return {
            "status": "ok",
            **repository_status,
            "db_path": settings_payload["db_path"],
            "max_symbols": settings_payload["max_symbols"],
            "configured_universe": [
                deps.universe_item_payload(item) for item in configured
            ],
            "expanded_universe": [
                deps.universe_item_payload(item) for item in universe
            ],
            "expanded_universe_count": len(universe),
            "universe": repository.list_universe(),
            "auto_collect": deps.read_only_auto_collect(),
        }

    @router.get("/api/etf/research/candidates")
    async def etf_research_candidates(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        repository = deps.repository_factory()
        configured = deps.configured_universe()
        universe = deps.expanded_universe(configured)
        settings_payload = deps.settings_payload()
        return {
            "status": "ok",
            "db_path": settings_payload["db_path"],
            "items": deps.list_candidates(repository, universe),
            "expanded_universe_count": len(universe),
            "auto_collect": deps.read_only_auto_collect(),
        }

    @router.post("/api/etf/research/collect")
    async def etf_research_collect(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        repository = deps.repository_factory()
        universe = deps.seed_universe(repository)
        symbols = deps.symbols_from_payload(payload, universe)
        return await _maybe_await(
            deps.collect_snapshots(
                repository=repository,
                configured=universe,
                fetch_quote=deps.fetch_quote,
                symbols=symbols,
                force=bool((payload or {}).get("force")),
            )
        )

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
