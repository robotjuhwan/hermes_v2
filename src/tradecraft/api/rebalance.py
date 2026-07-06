from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends


@dataclass(frozen=True)
class RebalanceRouteDeps:
    require_admin_auth: Callable[..., Any]
    kis_rebalance_status: Callable[[], Any]


def build_rebalance_router(deps: RebalanceRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/rebalance/kis-status")
    async def rebalance_kis_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _maybe_await(deps.kis_rebalance_status())

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
