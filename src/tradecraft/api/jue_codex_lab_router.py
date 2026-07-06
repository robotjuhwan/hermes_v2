from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends


@dataclass(frozen=True)
class JueCodexLabRouteDeps:
    require_admin_auth: Callable[..., Any]
    lab_provider: Callable[[], Any]


def build_jue_codex_lab_router(deps: JueCodexLabRouteDeps) -> APIRouter:
    router = APIRouter(prefix="/api/jue/codex-lab")

    @router.get("/status")
    async def jue_codex_lab_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.lab_provider().status()

    @router.post("/run-once")
    async def jue_codex_lab_run_once(
        max_tasks: int = 1,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.lab_provider().run_once(max_tasks=max_tasks)

    return router
