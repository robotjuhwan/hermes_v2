from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class SymbolRouteDeps:
    require_admin_auth: Callable[..., Any]
    fundamentals_service: Callable[[], Any]
    analysis_service: Callable[[], Any]
    symbols_from_csv: Callable[[Any], list[str]]
    strategy_fundamental_targets: Callable[[], list[str]]
    is_krx_symbol: Callable[[Any], bool]
    max_symbols_per_collect: Callable[[], int]


def build_symbols_router(deps: SymbolRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.post("/api/symbols/fundamentals/collect")
    async def symbol_fundamentals_collect(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        raw_symbols = body.get("symbols") if isinstance(body, dict) else None
        if isinstance(raw_symbols, list):
            symbols = [str(item or "").strip() for item in raw_symbols]
            target_source = "explicit"
        elif isinstance(raw_symbols, str) and raw_symbols.strip():
            symbols = deps.symbols_from_csv(raw_symbols)
            target_source = "explicit"
        else:
            symbols = deps.strategy_fundamental_targets()
            target_source = "strategy_targets"
        force = bool(body.get("force")) if isinstance(body, dict) else False
        result = await _maybe_await(
            deps.fundamentals_service().collect_symbols(symbols, force=force)
        )
        result["target_source"] = target_source
        result["target_symbols"] = symbols[: deps.max_symbols_per_collect()]
        return result

    @router.get("/api/symbols/special-watch")
    async def symbol_analysis_special_watch(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.analysis_service().special_watch()

    @router.get("/api/symbols/fundamentals/status")
    async def symbol_fundamentals_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.fundamentals_service().status()

    @router.post("/api/symbols/{symbol}/analysis/run")
    async def symbol_analysis_run(
        symbol: str,
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        trigger = str(body.get("trigger") or "user_request").strip() or "user_request"
        force_collect = bool(body.get("force_collect", True))
        return await _maybe_await(
            deps.analysis_service().run(
                symbol,
                trigger=trigger,
                force_collect=force_collect,
            )
        )

    @router.get("/api/symbols/{symbol}/analysis/history")
    async def symbol_analysis_history(
        symbol: str,
        limit: int = 10,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        clamped_limit = min(max(int(limit), 1), 50)
        return deps.analysis_service().history(symbol, limit=clamped_limit)

    @router.get("/api/symbols/{symbol}/fundamentals")
    async def symbol_fundamentals(symbol: str) -> dict[str, Any]:
        code = str(symbol or "").strip()
        if not deps.is_krx_symbol(code):
            raise HTTPException(
                status_code=400,
                detail="symbol must be a 6-digit KRX code",
            )
        latest = deps.fundamentals_service().latest(code)
        if latest is None:
            return {"status": "missing", "symbol": code}
        return latest

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
