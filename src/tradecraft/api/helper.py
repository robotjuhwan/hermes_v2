from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends


@dataclass(frozen=True)
class HelperRouteDeps:
    require_admin_auth: Callable[..., Any]
    ask: Callable[[dict[str, Any]], Any]


def build_helper_router(deps: HelperRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.post("/api/helper/ask")
    async def helper_ask(
        payload: dict[str, Any],
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _maybe_await(deps.ask(payload))

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
