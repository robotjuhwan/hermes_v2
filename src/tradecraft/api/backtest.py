from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from tradecraft.backtest.engine import BacktestConfig


@dataclass(frozen=True)
class BacktestRouteDeps:
    require_admin_auth: Callable[..., Any]
    manager: Callable[[], Any]
    data_registry: Callable[[], Any]
    list_scenarios: Callable[[], list[dict[str, Any]]]
    load_sessions: Callable[[], tuple[list[dict[str, Any]], str]]
    build_config: Callable[[dict[str, Any]], BacktestConfig]
    emit_interval: Callable[[], int]


def build_backtest_router(deps: BacktestRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/backtest/status")
    async def backtest_status(_: Any = Depends(deps.require_admin_auth)) -> dict[str, Any]:
        payload = deps.manager().status()
        return payload if isinstance(payload, dict) else {"status": "unknown"}

    @router.get("/api/backtest/scenarios")
    async def backtest_scenarios(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return {"scenarios": deps.list_scenarios()}

    @router.get("/api/backtest/data-status")
    async def backtest_data_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        payload = deps.data_registry().status()
        return payload if isinstance(payload, dict) else {"symbol_count": 0}

    @router.post("/api/backtest/start")
    async def backtest_start(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        request_payload = payload or {}
        rows, source = deps.load_sessions()
        selected = _normalize_session_filter(request_payload.get("session_ids"))
        filtered_rows = _filter_session_rows(rows, selected)
        if not filtered_rows:
            raise HTTPException(
                status_code=400,
                detail="no backtest sessions selected",
            )

        registry = deps.data_registry()
        observe = getattr(registry, "observe_sessions", None)
        if callable(observe):
            observe(filtered_rows, source)

        result = deps.manager().start(
            session_rows=filtered_rows,
            config=deps.build_config(request_payload),
            scenario=str(request_payload.get("scenario") or "baseline"),
            session_source=source,
            emit_interval=max(int(deps.emit_interval()), 1),
        )
        return result if isinstance(result, dict) else {"status": "running"}

    @router.post("/api/backtest/stop")
    async def backtest_stop(_: Any = Depends(deps.require_admin_auth)) -> dict[str, Any]:
        result = deps.manager().stop()
        return result if isinstance(result, dict) else {"ok": True}

    return router


def _normalize_session_filter(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _filter_session_rows(
    rows: list[dict[str, Any]],
    selected: set[str],
) -> list[dict[str, Any]]:
    if not selected:
        return list(rows)
    return [
        row
        for row in rows
        if str(row.get("session_id") or "").strip().lower() in selected
    ]


__all__ = [
    "BacktestRouteDeps",
    "build_backtest_router",
]
