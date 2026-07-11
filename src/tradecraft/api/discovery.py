from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class DiscoveryRouteDeps:
    require_admin_auth: Callable[..., Any]
    service: Callable[[], Any]
    today: Callable[[], date]
    config_payload: Callable[[], dict[str, Any]]


def build_discovery_router(deps: DiscoveryRouteDeps) -> APIRouter:
    router = APIRouter()

    def latest_limit(config: dict[str, Any]) -> int:
        kospi_count = _safe_int(config.get("kospi_count"))
        kosdaq_count = _safe_int(config.get("kosdaq_count"))
        etf_count = _safe_int(config.get("etf_count"))
        return min(max(kospi_count + kosdaq_count + etf_count, 10), 120)

    @router.get("/api/discovery/status")
    async def daily_discovery_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        service = deps.service()
        config = deps.config_payload()
        today = deps.today()
        should_run = getattr(service, "should_run_for_day", None)
        due_today = should_run(today) if callable(should_run) else None
        return {
            "status": "ok",
            "config": config,
            "latest": service.latest_context(limit=latest_limit(config)),
            "due_today": due_today,
            "coverage": {
                "kospi_count": int(config.get("kospi_count", 0)),
                "kosdaq_count": int(config.get("kosdaq_count", 0)),
                "etf_count": int(config.get("etf_count", 0)),
                "candidate_limit_per_market": int(
                    config.get("candidate_limit_per_market", 0)
                ),
            },
        }

    @router.get("/api/discovery/latest")
    async def daily_discovery_latest(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        config = deps.config_payload()
        return deps.service().latest_context(limit=latest_limit(config))

    @router.post("/api/discovery/run-once")
    async def daily_discovery_run_once(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        raw_day = str(body.get("trading_day") or "").strip()
        try:
            trading_day = date.fromisoformat(raw_day) if raw_day else deps.today()
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="trading_day must be YYYY-MM-DD",
            ) from exc
        return await _maybe_await(
            deps.service().run_once(
                trading_day=trading_day,
                force=bool(body.get("force", False)),
            )
        )

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
