from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tradecraft.services.jue_wiki import JueWikiService
from tradecraft.services.jue_wiki_application import JueWikiApplicationService


class WikiServiceProtocol(Protocol):
    def status(self) -> dict[str, Any]: ...

    def context_pack(
        self,
        *,
        target_scope: str = "",
        symbols: list[str] | None = None,
        page_types: list[str] | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]: ...

    def read_page(self, page_id: str) -> dict[str, Any]: ...

    def search(
        self,
        query: str = "",
        scope: str | None = None,
        page_type: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]: ...

    def rebuild(self, *, scope: str = "", force: bool = False) -> dict[str, Any]: ...

    def lint(self, *, scope: str = "") -> dict[str, Any]: ...

    def list_lint_findings(
        self,
        *,
        scope: str | None = None,
        status: str = "open",
    ) -> list[dict[str, Any]]: ...

    def repair_once(self, *, scope: str | None = None) -> dict[str, Any]: ...

    def page_sources(self, page_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class WikiRouteDeps:
    require_admin_auth: Callable[..., Any]
    service: WikiServiceProtocol | None = None


class UnavailableWikiService:
    def status(self) -> dict[str, Any]:
        return {"status": "disabled", "enabled": False, "page_count": 0}

    def context_pack(self, **_: Any) -> dict[str, Any]:
        return {"status": "disabled", "pages": [], "target_scope": ""}

    def read_page(self, page_id: str) -> dict[str, Any]:
        return {"status": "not_found", "page_id": page_id, "content": ""}

    def search(
        self,
        query: str = "",
        scope: str | None = None,
        page_type: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = query, scope, page_type
        return []

    def rebuild(self, **_: Any) -> dict[str, Any]:
        return {"status": "disabled", "updated_count": 0}

    def lint(self, **_: Any) -> dict[str, Any]:
        return {"status": "disabled", "open_findings": []}

    def list_lint_findings(self, **_: Any) -> list[dict[str, Any]]:
        return []

    def repair_once(self, **_: Any) -> dict[str, Any]:
        return {"status": "disabled", "actions": []}

    def page_sources(self, page_id: str) -> dict[str, Any]:
        return {"status": "disabled", "page_id": page_id, "source_refs": []}


class WikiRebuildRequest(BaseModel):
    scope: str = ""
    force: bool = False


class WikiLintRequest(BaseModel):
    scope: str = ""


class WikiRepairRequest(BaseModel):
    scope: str = ""


def _search_envelope(
    raw_result: list[dict[str, Any]] | dict[str, Any],
    *,
    query: str,
    scope: str | None,
    page_type: str | None,
) -> dict[str, Any]:
    status = "ok"
    pages: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    if isinstance(raw_result, dict):
        if "status" in raw_result:
            status = str(raw_result.get("status") or "ok")
        elif "error" in raw_result or "error_message" in raw_result:
            status = "error"
        raw_pages = raw_result.get("pages")
        pages = raw_pages if isinstance(raw_pages, list) else []
        for key in ("reason", "error", "error_message", "warnings"):
            if key in raw_result:
                extra[key] = raw_result[key]
    elif isinstance(raw_result, list):
        pages = raw_result
    return {
        "status": status,
        "query": query,
        "scope": scope,
        "page_type": page_type,
        "pages": pages,
        **extra,
    }


def build_wiki_router(deps: WikiRouteDeps) -> APIRouter:
    router = APIRouter()
    service = deps.service or UnavailableWikiService()

    def application_service() -> JueWikiApplicationService | None:
        if isinstance(service, JueWikiService):
            return JueWikiApplicationService(service)
        return None

    @router.get("/api/wiki/status")
    async def wiki_status() -> dict[str, Any]:
        return service.status()

    @router.get("/api/wiki/context")
    async def wiki_context(
        scope: str = "",
        symbol: str = "",
        page_type: str = "",
        max_chars: int | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        symbols = [symbol.strip()] if symbol.strip() else []
        page_types = [page_type.strip()] if page_type.strip() else []
        return service.context_pack(
            target_scope=scope,
            symbols=symbols,
            page_types=page_types,
            max_chars=max_chars,
        )

    @router.get("/api/wiki/search")
    async def wiki_search(
        query: str = "",
        scope: str | None = None,
        page_type: str | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        raw_result = service.search(
            query=query,
            scope=scope,
            page_type=page_type,
        )
        return _search_envelope(
            raw_result,
            query=query,
            scope=scope,
            page_type=page_type,
        )

    @router.get("/api/wiki/lint/findings")
    async def wiki_lint_findings(
        scope: str = "",
        status: str = "open",
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "findings": service.list_lint_findings(
                scope=scope,
                status=status,
            ),
        }

    @router.get("/api/wiki/pages/{page_id}/sources")
    async def wiki_page_sources(
        page_id: str,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return service.page_sources(page_id)

    @router.post("/api/wiki/rebuild")
    async def wiki_rebuild(
        payload: WikiRebuildRequest,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return service.rebuild(scope=payload.scope, force=payload.force)

    @router.post("/api/wiki/lint")
    async def wiki_lint(
        payload: WikiLintRequest,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return service.lint(scope=payload.scope)

    @router.post("/api/wiki/repair/run-once")
    async def wiki_repair_run_once(
        payload: WikiRepairRequest,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return service.repair_once(scope=payload.scope)

    @router.get("/api/wiki/application/status")
    async def wiki_application_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        application = application_service()
        if application is None:
            return {
                "status": "unavailable",
                "reason": "service_does_not_support_application",
                "recent_links": [],
            }
        return {
            **application.status(),
            "recent_links": application.list_decision_links(limit=20),
            "mode_recommendations": application.list_mode_recommendations(limit=10),
        }

    @router.get("/api/wiki/application/effectiveness")
    async def wiki_application_effectiveness(
        scope: str = "",
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        application = application_service()
        if application is None:
            return {"status": "unavailable", "scope": scope, "pages": []}
        return {
            "status": "ok",
            "scope": scope,
            "pages": application.list_page_effectiveness(
                decision_scope=scope,
                limit=100,
            ),
        }

    @router.get("/api/wiki/pages/{page_id}")
    async def wiki_page(
        page_id: str,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return service.read_page(page_id)

    return router
