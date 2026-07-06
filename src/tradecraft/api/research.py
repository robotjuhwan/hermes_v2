from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from tradecraft.api.research_payloads import (
    build_rag_search_payload,
    build_rag_status_payload,
    build_rag_sync_payload,
    build_reports_backfill_symbol_links_payload,
    build_reports_crawl_once_payload,
    build_reports_repair_metadata_payload,
    build_reports_status_payload,
)

RAG_SYNC_HEAVY_LIMIT = 5000
RAG_SYNC_HEAVY_CONFIRMATION_MESSAGE = (
    "rag sync is heavy; pass confirm_heavy_sync=true to run it"
)


@dataclass(frozen=True)
class ResearchRouteDeps:
    require_admin_auth: Callable[..., Any]
    helper_ask: Callable[[dict[str, Any]], Any]
    reports_status: Callable[..., Any]
    reports_crawl_once: Callable[[], Any]
    reports_repair_metadata: Callable[[bool, bool], Any]
    reports_backfill_symbol_links: Callable[[bool, int], Any]
    reports_search: Callable[[str, str, str, int], list[dict[str, Any]]]
    rag_status: Callable[[], Any]
    rag_sync: Callable[..., Any]
    rag_search: Callable[..., Any]


def _resolve_runtime_dependency(value: Any) -> Any:
    return value() if callable(value) else value


def build_research_route_deps(
    *,
    require_admin_auth: Callable[..., Any],
    helper_ask: Callable[[dict[str, Any]], Any],
    settings: Any,
    naver_report_repository: Any,
    naver_report_crawler: Any,
    rag_store: Any | None,
    symbol_fundamentals_service: Any,
    build_report_intelligence_status: Callable[[Any], dict[str, Any]],
    run_report_collection_cycle: Callable[..., Any],
    sync_report_rag: Callable[..., dict[str, Any] | None],
    seed_symbol_directory: Callable[[], Any],
    on_rag_resolve_error: Callable[[Exception], None] | None = None,
) -> ResearchRouteDeps:
    def reports_status(*, compact: bool = False) -> dict[str, Any]:
        repository = _resolve_runtime_dependency(naver_report_repository)
        store = _resolve_runtime_dependency(rag_store)
        fundamentals = _resolve_runtime_dependency(symbol_fundamentals_service)
        repository_status_fn = (
            getattr(repository, "ops_status", None)
            if compact
            else getattr(repository, "status", None)
        )
        if not callable(repository_status_fn):
            repository_status_fn = repository.status
        rag_status = (
            store.status()
            if (settings.rag_enabled and store is not None)
            else {
                "available": False,
                "reason": "rag_disabled",
                "persist_path": settings.rag_persist_path,
                "collection_name": settings.rag_collection_name,
            }
        )
        payload = build_reports_status_payload(
            naver_reports_enabled=settings.naver_reports_enabled,
            repository_status=repository_status_fn(),
            intelligence_status=build_report_intelligence_status(settings),
            rag_status=rag_status,
            fundamentals_status=fundamentals.status(),
        )
        payload["compact"] = bool(compact)
        return payload

    async def reports_crawl_once() -> dict[str, Any]:
        repository = _resolve_runtime_dependency(naver_report_repository)
        crawler = _resolve_runtime_dependency(naver_report_crawler)
        store = _resolve_runtime_dependency(rag_store)
        return await build_reports_crawl_once_payload(
            crawler=crawler,
            repository=repository,
            rag_store=store,
            rag_enabled=settings.rag_enabled,
            rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
            run_report_collection_cycle=run_report_collection_cycle,
        )

    def reports_repair_metadata(
        sync_rag_after: bool,
        prune_orphans: bool,
    ) -> dict[str, Any]:
        repository = _resolve_runtime_dependency(naver_report_repository)
        store = _resolve_runtime_dependency(rag_store)
        return build_reports_repair_metadata_payload(
            repository=repository,
            rag_store=store,
            rag_enabled=settings.rag_enabled,
            rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
            sync_report_rag=sync_report_rag,
            sync_rag_after=sync_rag_after,
            prune_orphans=prune_orphans,
        )

    def reports_backfill_symbol_links(
        sync_rag_after: bool,
        limit: int,
    ) -> dict[str, Any]:
        repository = _resolve_runtime_dependency(naver_report_repository)
        store = _resolve_runtime_dependency(rag_store)
        return build_reports_backfill_symbol_links_payload(
            repository=repository,
            rag_store=store,
            rag_enabled=settings.rag_enabled,
            rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
            sync_report_rag=sync_report_rag,
            seed_symbol_directory=seed_symbol_directory,
            sync_rag_after=sync_rag_after,
            limit=limit,
        )

    def reports_search(
        query: str = "",
        symbol: str = "",
        category: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        repository = _resolve_runtime_dependency(naver_report_repository)
        return repository.search(
            query=query,
            symbol=symbol,
            category=category,
            limit=limit,
        )

    def rag_status() -> dict[str, Any]:
        store = _resolve_runtime_dependency(rag_store)
        return build_rag_status_payload(
            rag_enabled=settings.rag_enabled,
            rag_store=store,
            persist_path=settings.rag_persist_path,
            collection_name=settings.rag_collection_name,
        )

    def rag_sync(
        force: bool = False,
        metadata_only: bool = False,
        prune_orphans: bool = False,
        limit: int | None = None,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        repository = _resolve_runtime_dependency(naver_report_repository)
        store = _resolve_runtime_dependency(rag_store)
        resolved_limit = settings.rag_sync_chunk_limit if limit is None else max(int(limit), 1)
        return build_rag_sync_payload(
            rag_enabled=settings.rag_enabled,
            rag_store=store,
            repository=repository,
            limit=resolved_limit,
            sync_report_rag=sync_report_rag,
            force=force,
            metadata_only=metadata_only,
            prune_orphans=prune_orphans,
            rebuild=rebuild,
        )

    def rag_search(
        query: str,
        symbol: str = "",
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int | None = None,
    ) -> dict[str, Any]:
        repository = _resolve_runtime_dependency(naver_report_repository)
        store = _resolve_runtime_dependency(rag_store)
        return build_rag_search_payload(
            rag_enabled=settings.rag_enabled,
            rag_store=store,
            repository=repository,
            default_limit=settings.rag_query_top_k,
            query=query,
            symbol=symbol,
            broker=broker,
            doc_id=doc_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            on_resolve_error=on_rag_resolve_error,
        )

    return ResearchRouteDeps(
        require_admin_auth=require_admin_auth,
        helper_ask=helper_ask,
        reports_status=reports_status,
        reports_crawl_once=reports_crawl_once,
        reports_repair_metadata=reports_repair_metadata,
        reports_backfill_symbol_links=reports_backfill_symbol_links,
        reports_search=reports_search,
        rag_status=rag_status,
        rag_sync=rag_sync,
        rag_search=rag_search,
    )


def build_research_router(deps: ResearchRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/research/ask")
    async def research_ask(
        query: str,
        symbol: str = "",
        broker: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 8,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        public_payload = {
            "query": query,
            "symbol": symbol,
            "broker": broker,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        }
        result = await _maybe_await(
            deps.helper_ask(
                {
                    **public_payload,
                    "allow_source_only_on_llm_error": True,
                }
            )
        )
        if isinstance(result.get("payload"), dict):
            result["payload"] = {
                key: value
                for key, value in result["payload"].items()
                if key != "allow_source_only_on_llm_error"
            }
        else:
            result["payload"] = public_payload
        result["source"] = "research_ask"
        return result

    @router.get("/api/reports/status")
    async def reports_status(
        compact: bool = Query(False),
    ) -> dict[str, Any]:
        return await _maybe_await(deps.reports_status(compact=compact))

    @router.get("/api/research/status")
    async def research_status(
        compact: bool = Query(False),
    ) -> dict[str, Any]:
        payload = await _maybe_await(deps.reports_status(compact=compact))
        if isinstance(payload, dict):
            return {
                **payload,
                "source": "research_status",
                "reports_status_endpoint": "/api/reports/status",
            }
        return {
            "status": "error",
            "source": "research_status",
            "reports_status_endpoint": "/api/reports/status",
            "error_message": "reports status payload is not a mapping",
        }

    @router.post("/api/reports/crawl-once")
    async def reports_crawl_once(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _maybe_await(deps.reports_crawl_once())

    @router.post("/api/reports/repair-metadata")
    async def reports_repair_metadata(
        sync_rag_after: bool = True,
        prune_orphans: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _maybe_await(
            deps.reports_repair_metadata(sync_rag_after, prune_orphans)
        )

    @router.post("/api/reports/backfill-symbol-links")
    async def reports_backfill_symbol_links(
        sync_rag_after: bool = True,
        limit: int = 0,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _maybe_await(
            deps.reports_backfill_symbol_links(sync_rag_after, max(int(limit), 0))
        )

    @router.get("/api/reports/search")
    async def reports_search(
        query: str = "",
        symbol: str = "",
        category: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        rows = deps.reports_search(query, symbol, category, limit)
        return {
            "status": "ok",
            "count": len(rows),
            "items": rows,
        }

    @router.get("/api/rag/status")
    async def rag_status() -> dict[str, Any]:
        return await _maybe_await(deps.rag_status())

    @router.post("/api/rag/sync")
    async def rag_sync(
        force: bool = False,
        metadata_only: bool = False,
        prune_orphans: bool = False,
        limit: int | None = None,
        rebuild: bool = False,
        confirm_heavy_sync: bool = False,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        if not confirm_heavy_sync and (
            bool(rebuild)
            or (limit is not None and int(limit) > RAG_SYNC_HEAVY_LIMIT)
        ):
            raise HTTPException(
                status_code=409,
                detail=RAG_SYNC_HEAVY_CONFIRMATION_MESSAGE,
            )
        return await _maybe_await(
            deps.rag_sync(
                force,
                metadata_only,
                prune_orphans,
                limit=limit,
                rebuild=rebuild,
            )
        )

    @router.get("/api/rag/search")
    async def rag_search(
        query: str,
        symbol: str = "",
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int | None = None,
    ) -> dict[str, Any]:
        text = str(query or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="query is required")
        return await _maybe_await(
            deps.rag_search(
                text,
                symbol,
                broker,
                doc_id,
                date_from,
                date_to,
                limit,
            )
        )

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
