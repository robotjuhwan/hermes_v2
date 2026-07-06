from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response


@dataclass(frozen=True)
class StaticRouteDeps:
    static_dir: Callable[[], Path]


def build_static_router(deps: StaticRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/")
    async def index() -> FileResponse:
        return FileResponse(deps.static_dir() / "index.html")

    @router.get("/favicon.ico", status_code=204)
    @router.get("/apple-touch-icon.png", status_code=204)
    @router.get("/apple-touch-icon-precomposed.png", status_code=204)
    async def icon_fallback() -> Response:
        return Response(status_code=204)

    return router
