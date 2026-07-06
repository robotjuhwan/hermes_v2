from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class MarketRouteDeps:
    require_admin_auth: Callable[..., Any]
    build_dashboard_payload: Callable[..., Any]
    market_judgment_engine: Any
    market_pulse_service: Any
    kis_primary_ready: Callable[[], bool]


def build_market_router(deps: MarketRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/dashboard")
    @router.get("/api/portfolio")
    async def dashboard(
        refresh: bool = False,
        force_refresh: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        should_force_refresh = bool(refresh or force_refresh)
        return await _maybe_await(
            deps.build_dashboard_payload(
                include_telegram=True,
                force_refresh=should_force_refresh,
            )
        )

    @router.get("/api/market/clock")
    async def market_clock() -> dict[str, Any]:
        engine = _resolve(deps.market_judgment_engine)
        return engine.clock()

    @router.get("/api/market/quotes")
    async def market_quotes(
        limit: int = 100,
        symbols: str = "",
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        requested_symbols = [
            item.strip()
            for item in re.split(r"[\s,]+", str(symbols or ""))
            if re.fullmatch(r"\d{6}", item.strip())
        ]
        engine = _resolve(deps.market_judgment_engine)
        return engine.latest_quotes(
            limit=max(min(int(limit), 300), 1),
            symbols=list(dict.fromkeys(requested_symbols)),
        )

    @router.get("/api/market/pulse/latest")
    async def market_pulse_latest(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = _resolve(deps.market_pulse_service)
        return service.latest()

    @router.get("/api/market/pulse/status")
    async def market_pulse_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = _resolve(deps.market_pulse_service)
        return service.latest()

    @router.get("/api/market/pulse/history")
    async def market_pulse_history(
        limit: int = 20,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = _resolve(deps.market_pulse_service)
        return service.history(limit=max(min(int(limit), 200), 1))

    @router.post("/api/market/pulse/run-once")
    async def market_pulse_run_once(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        engine = _resolve(deps.market_judgment_engine)
        service = _resolve(deps.market_pulse_service)
        return await _maybe_await(service.collect(clock=engine.clock()))

    @router.get("/api/market/account")
    async def market_account(_: Any = Depends(deps.require_admin_auth)) -> dict[str, Any]:
        engine = _resolve(deps.market_judgment_engine)
        return engine.latest_account()

    @router.get("/api/market/judgments/latest")
    async def market_judgment_latest(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        engine = _resolve(deps.market_judgment_engine)
        return engine.latest_judgment()

    @router.get("/api/market/judgments/schedule")
    async def market_judgment_schedule(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        engine = _resolve(deps.market_judgment_engine)
        return engine.schedule()

    @router.post("/api/market/judgments/run-once")
    async def market_judgment_run_once(
        use_llm: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        if not deps.kis_primary_ready():
            raise HTTPException(
                status_code=400,
                detail="kis primary account not configured",
            )
        engine = _resolve(deps.market_judgment_engine)
        return await _maybe_await(engine.run_once(use_llm=bool(use_llm)))

    return router


def _resolve(value: Any) -> Any:
    return value() if callable(value) and not _looks_like_service(value) else value


def _looks_like_service(value: Any) -> bool:
    return any(
        hasattr(value, attr)
        for attr in (
            "clock",
            "latest",
            "latest_quotes",
            "latest_account",
            "latest_judgment",
        )
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
