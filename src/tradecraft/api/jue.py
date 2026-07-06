from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class JueRouteDeps:
    require_admin_auth: Callable[..., Any]
    registry_factory: Callable[[], Any]
    available_workflow_ids: Callable[[Any], list[str]]
    validation_error_type: type[Exception]
    lifecycle_repository_factory: Callable[[str], Any]
    investment_memory_db_path: Callable[[], str]


def build_jue_router(deps: JueRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jue/workflows/status")
    async def jue_workflows_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        registry = deps.registry_factory()
        workflows: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for workflow_id in deps.available_workflow_ids(registry):
            try:
                workflows[workflow_id] = registry.compile_prompt_pack(workflow_id)
            except deps.validation_error_type as exc:
                errors[workflow_id] = str(exc)
        return {
            "status": "ok" if not errors else "error",
            "workflow_count": len(workflows),
            "error_count": len(errors),
            "workflows": workflows,
            "errors": errors,
        }

    @router.get("/api/jue/source-manifest")
    async def jue_source_manifest(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        registry = deps.registry_factory()
        try:
            manifest = registry.load_source_manifest("financial_services")
        except deps.validation_error_type as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "status": "ok",
            "source_id": manifest.get("source_id"),
            "repository_url": manifest.get("repository_url"),
            "mapping_count": len(manifest.get("mappings") or []),
            "manifest": manifest,
        }

    @router.get("/api/jue/lifecycle/latest")
    async def jue_lifecycle_latest(
        symbol: str | None = None,
        workflow_id: str | None = None,
        limit: int = 20,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        symbols = [symbol.strip()] if symbol and symbol.strip() else None
        safe_limit = max(min(int(limit), 100), 1)
        repository = deps.lifecycle_repository_factory(deps.investment_memory_db_path())
        items = repository.list_artifacts(
            symbols=symbols,
            workflow_id=workflow_id.strip() if workflow_id else None,
            limit=safe_limit,
        )
        return {
            "status": "ok",
            "count": len(items),
            "items": items,
            "filters": {
                "symbol": symbols[0] if symbols else "",
                "workflow_id": workflow_id or "",
                "limit": safe_limit,
            },
        }

    return router
