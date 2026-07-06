from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tradecraft.config import AppSettings
from tradecraft.reports_api import __version__
from tradecraft.reports_api.auth import is_valid_bearer_token_any
from tradecraft.reports_api.ops import (
    build_data_quality_payload,
    build_deployment_checks,
    build_worker_health_payload,
)
from tradecraft.reports_api.saved_views import ReportsSavedViewStore
from tradecraft.reports_api.schemas import (
    CrawlOnceRequest,
    ReportFiltersPayload,
    RAGSyncRequest,
    SavedViewAlertTestRequest,
    SavedViewUpsertRequest,
    SymbolRefreshRequest,
)
from tradecraft.reports_api.ui_guard import enforce_ui_access
from tradecraft.runtime.state_store import utc_now_iso
from tradecraft.services.intelligence import (
    build_report_intelligence_status,
    build_report_intelligence_stack,
    run_report_collection_cycle,
    sync_report_rag,
)
from tradecraft.services.telegram import TelegramBridge, TelegramConfig

settings = AppSettings()
logger = logging.getLogger(__name__)

report_stack = build_report_intelligence_stack(settings)
repository = report_stack.repository
crawler = report_stack.crawler
rag_store = report_stack.rag_store
saved_view_store = ReportsSavedViewStore(".runtime/reports_saved_views.json")

app = FastAPI(title="TradeCraft Reports API", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIST_DIR = Path(__file__).resolve().parent / "web_dist"
WEB_DIST_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=WEB_DIST_DIR), name="static")


def _require_configured_api_tokens() -> list[str]:
    tokens = settings.reports_api_token_list
    if not tokens:
        raise HTTPException(status_code=500, detail="reports api token missing")
    return tokens


def require_api_auth(authorization: str | None = Header(default=None)) -> None:
    expected_tokens = _require_configured_api_tokens()
    if not is_valid_bearer_token_any(authorization, expected_tokens):
        raise HTTPException(status_code=401, detail="unauthorized")


def require_ui_access(request: Request) -> str:
    return enforce_ui_access(
        request,
        allowed_cidrs=settings.reports_ui_allowed_cidr_list,
        trust_proxy=bool(settings.reports_ui_trust_proxy),
    )


@app.middleware("http")
async def ui_static_guard(request: Request, call_next):
    path = str(request.url.path or "")
    if path == "/" or path == "/static" or path.startswith("/static/"):
        try:
            require_ui_access(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


def _rag_disabled_payload(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "persist_path": settings.rag_persist_path,
        "collection_name": settings.rag_collection_name,
    }


def _rag_status_payload() -> dict[str, Any]:
    if not settings.rag_enabled:
        return _rag_disabled_payload("rag_disabled")
    if rag_store is None:
        return _rag_disabled_payload("rag_store_missing")
    return rag_store.status()


def _search_rows(filters: ReportFiltersPayload | dict[str, Any]) -> list[dict[str, Any]]:
    payload = (
        filters.model_dump()
        if isinstance(filters, ReportFiltersPayload)
        else dict(filters or {})
    )
    return repository.search(
        query=str(payload.get("query") or ""),
        symbol=str(payload.get("symbol") or ""),
        category=str(payload.get("category") or ""),
        broker=str(payload.get("broker") or ""),
        analyst=str(payload.get("analyst") or ""),
        date_from=str(payload.get("date_from") or ""),
        date_to=str(payload.get("date_to") or ""),
        limit=int(payload.get("limit") or 20),
    )


def _report_detail_payload(
    report_id: int,
    include_chunks: bool = False,
    chunk_limit: int = 200,
) -> dict[str, Any]:
    report = repository.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    chunks: list[dict[str, Any]] = []
    if include_chunks:
        chunks = repository.list_report_chunks(report_id=report_id, limit=chunk_limit)
    return {
        "status": "ok",
        "report": report,
        "facts": repository.get_report_facts(report_id),
        "chunks": chunks,
    }


def _saved_view_or_404(view_id: str) -> dict[str, Any]:
    row = saved_view_store.get_view(view_id)
    if row is None:
        raise HTTPException(status_code=404, detail="saved view not found")
    return row


def _build_saved_view_alert_message(
    view: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    filters = dict(view.get("filters") or {})
    filter_bits: list[str] = []
    for label, key in (
        ("query", "query"),
        ("symbol", "symbol"),
        ("category", "category"),
        ("broker", "broker"),
        ("analyst", "analyst"),
        ("from", "date_from"),
        ("to", "date_to"),
    ):
        value = str(filters.get(key) or "").strip()
        if value:
            filter_bits.append(f"{label}={value}")

    lines = [f"[Hermes Reports] {str(view.get('name') or 'Saved View').strip()}"]
    if filter_bits:
        lines.append(f"Filters: {', '.join(filter_bits)}")
    lines.append(f"Matched reports: {len(rows)}")
    if not rows:
        lines.append("- matching reports 없음")
        return "\n".join(lines)

    for row in rows[:5]:
        company = str(row.get("company_name") or row.get("symbol") or "-").strip() or "-"
        title = str(row.get("title") or "제목 없음").strip() or "제목 없음"
        broker = str(row.get("broker") or "-").strip() or "-"
        published_at = str(row.get("published_at") or "-").strip() or "-"
        lines.append(f"- {published_at} | {company} | {title} | {broker}")
    return "\n".join(lines)


def _telegram_bridge() -> TelegramBridge:
    return TelegramBridge(
        TelegramConfig(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    )


async def _crawl_once(sync_rag: bool) -> dict[str, Any]:
    result = await run_report_collection_cycle(
        crawler=crawler,
        repository=repository,
        rag_store=rag_store,
        rag_enabled=settings.rag_enabled,
        rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
        refresh_symbol_directory=False,
        sync_rag=sync_rag,
    )
    return {
        "status": "ok",
        "snapshot": result.get("snapshot"),
        "rag_sync": result.get("rag_sync"),
    }


@app.get("/", response_class=HTMLResponse)
async def index(_: str = Depends(require_ui_access)):
    index_path = WEB_DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        content=(
            "<html><body><h1>Reports Console build missing</h1>"
            "<p>Run <code>npm run build</code> in <code>web/reports-console</code>.</p>"
            "</body></html>"
        ),
        status_code=503,
    )


@app.get("/v1/health")
async def v1_health() -> dict[str, Any]:
    readiness = build_deployment_checks(settings, require_worker=False)
    repository_status = repository.status()
    return {
        "status": "ok",
        "service": "reports_api",
        "version": __version__,
        "updated_at": utc_now_iso(),
        "readiness": readiness,
        "quality": build_data_quality_payload(settings, repository_status),
        "intelligence": build_report_intelligence_status(settings),
        "worker": build_worker_health_payload(settings),
    }


v1_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_auth)])


@v1_router.get("/reports/status")
async def v1_reports_status() -> dict[str, Any]:
    repository_status = repository.status()
    return {
        "status": "ok",
        "enabled": settings.naver_reports_enabled,
        "repository": repository_status,
        "quality": build_data_quality_payload(settings, repository_status),
        "intelligence": build_report_intelligence_status(settings),
        "rag": _rag_status_payload(),
        "readiness": build_deployment_checks(settings, require_worker=False),
        "worker": build_worker_health_payload(settings),
    }


@v1_router.post("/reports/crawl-once")
async def v1_reports_crawl_once(
    payload: CrawlOnceRequest | None = Body(default=None),
) -> dict[str, Any]:
    sync_rag = True if payload is None else bool(payload.sync_rag)
    return await _crawl_once(sync_rag=sync_rag)


@v1_router.get("/reports/search")
async def v1_reports_search(
    query: str = "",
    symbol: str = "",
    category: str = "",
    broker: str = "",
    analyst: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    rows = _search_rows(
        ReportFiltersPayload(
            query=query,
            symbol=symbol,
            category=category,
            broker=broker,
            analyst=analyst,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    )
    return {
        "status": "ok",
        "count": len(rows),
        "items": rows,
    }


@v1_router.get("/reports/{report_id}")
async def v1_reports_detail(
    report_id: int,
    include_chunks: bool = False,
    chunk_limit: int = Query(default=200, ge=1, le=5000),
) -> dict[str, Any]:
    return _report_detail_payload(
        report_id=report_id,
        include_chunks=include_chunks,
        chunk_limit=chunk_limit,
    )


@v1_router.post("/reports/symbol-directory/refresh")
async def v1_symbol_directory_refresh(
    payload: SymbolRefreshRequest | None = Body(default=None),
) -> dict[str, Any]:
    as_of = "" if payload is None else str(payload.as_of or "").strip()
    result = repository.refresh_symbol_directory_from_krx(as_of=as_of)
    return {
        "status": "ok" if bool(result.get("ok")) else "warn",
        "result": result,
    }


@v1_router.get("/rag/status")
async def v1_rag_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "enabled": bool(settings.rag_enabled),
        "rag": _rag_status_payload(),
    }


@v1_router.post("/rag/sync")
async def v1_rag_sync(
    payload: RAGSyncRequest | None = Body(default=None),
) -> dict[str, Any]:
    if not settings.rag_enabled:
        return {
            "status": "ok",
            "enabled": False,
            "result": {"status": "skipped", "reason": "rag_disabled"},
        }
    if rag_store is None:
        return {
            "status": "ok",
            "enabled": True,
            "result": {"status": "skipped", "reason": "rag_store_missing"},
        }
    limit = settings.rag_sync_chunk_limit
    if payload is not None and payload.limit is not None:
        limit = int(payload.limit)
    result = sync_report_rag(
        repository=repository,
        rag_store=rag_store,
        enabled=settings.rag_enabled,
        limit=limit,
        force_update=bool(payload.force) if payload is not None else False,
        metadata_only=bool(payload.metadata_only) if payload is not None else False,
        prune_missing=bool(payload.prune_orphans) if payload is not None else False,
    )
    return {
        "status": "ok",
        "enabled": True,
        "result": result or {"status": "skipped", "reason": "rag_store_missing"},
    }


@v1_router.get("/rag/search")
async def v1_rag_search(
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
    if not settings.rag_enabled or rag_store is None:
        return {"status": "ok", "enabled": False, "count": 0, "items": []}
    resolved_limit = settings.rag_query_top_k if limit is None else int(limit)
    rows = rag_store.query(
        query=text,
        symbol=symbol,
        broker=broker,
        doc_id=doc_id,
        date_from=date_from,
        date_to=date_to,
        limit=resolved_limit,
    )
    return {
        "status": "ok",
        "enabled": True,
        "count": len(rows),
        "items": rows,
    }


ui_router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(require_ui_access), Depends(require_api_auth)],
)


@ui_router.get("/overview")
async def ui_overview() -> dict[str, Any]:
    repository_status = repository.status()
    return {
        "status": "ok",
        "updated_at": utc_now_iso(),
        "service": {
            "name": "reports_api",
            "version": __version__,
            "ui_refresh_sec": 10,
        },
        "crawler": {
            "enabled": bool(settings.naver_reports_enabled),
            "interval_sec": int(settings.naver_reports_interval_sec),
            "since_date": str(settings.naver_reports_since_date or ""),
            "seed_urls": settings.naver_reports_seed_url_list,
        },
        "reports": repository_status,
        "quality": build_data_quality_payload(settings, repository_status),
        "intelligence": build_report_intelligence_status(settings),
        "rag": _rag_status_payload(),
        "readiness": build_deployment_checks(settings, require_worker=False),
        "worker": build_worker_health_payload(settings),
    }


@ui_router.get("/reports/recent")
async def ui_reports_recent(
    query: str = "",
    symbol: str = "",
    category: str = "",
    broker: str = "",
    analyst: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    rows = _search_rows(
        ReportFiltersPayload(
            query=query,
            symbol=symbol,
            category=category,
            broker=broker,
            analyst=analyst,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    )
    return {
        "status": "ok",
        "count": len(rows),
        "items": rows,
    }


@ui_router.get("/reports/{report_id}")
async def ui_report_detail(
    report_id: int,
    include_chunks: bool = True,
    chunk_limit: int = Query(default=40, ge=1, le=5000),
) -> dict[str, Any]:
    return _report_detail_payload(
        report_id=report_id,
        include_chunks=include_chunks,
        chunk_limit=chunk_limit,
    )


@ui_router.get("/saved-views")
async def ui_saved_views_list() -> dict[str, Any]:
    rows = saved_view_store.list_views()
    return {
        "status": "ok",
        "count": len(rows),
        "items": rows,
    }


@ui_router.post("/saved-views")
async def ui_saved_views_save(payload: SavedViewUpsertRequest) -> dict[str, Any]:
    try:
        row = saved_view_store.save_view(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "view": row,
    }


@ui_router.delete("/saved-views/{view_id}")
async def ui_saved_views_delete(view_id: str) -> dict[str, Any]:
    if not saved_view_store.delete_view(view_id):
        raise HTTPException(status_code=404, detail="saved view not found")
    return {"status": "ok", "deleted": True}


@ui_router.post("/saved-views/{view_id}/alert-preview")
async def ui_saved_view_alert_preview(
    view_id: str,
    payload: SavedViewAlertTestRequest | None = Body(default=None),
) -> dict[str, Any]:
    row = _saved_view_or_404(view_id)
    filters = dict(row.get("filters") or {})
    filters["limit"] = payload.limit if payload is not None else 5
    items = _search_rows(filters)
    message = _build_saved_view_alert_message(row, items)
    return {
        "status": "ok",
        "count": len(items),
        "message": message,
        "items": items,
    }


@ui_router.post("/saved-views/{view_id}/alert-test")
async def ui_saved_view_alert_test(
    view_id: str,
    payload: SavedViewAlertTestRequest | None = Body(default=None),
) -> dict[str, Any]:
    row = _saved_view_or_404(view_id)
    alert = dict(row.get("alert") or {})
    channel = str(alert.get("channel") or "telegram").strip().lower()
    if channel != "telegram":
        raise HTTPException(status_code=400, detail=f"unsupported alert channel: {channel}")

    filters = dict(row.get("filters") or {})
    filters["limit"] = payload.limit if payload is not None else 5
    items = _search_rows(filters)
    message = _build_saved_view_alert_message(row, items)
    target = str(alert.get("target") or "").strip() or None
    result = await _telegram_bridge().send_message(text=message, chat_id=target)
    if not bool(result.get("ok")):
        raise HTTPException(
            status_code=400,
            detail=str(result.get("detail") or "telegram alert failed"),
        )
    return {
        "status": "ok",
        "count": len(items),
        "message": message,
        "result": result,
    }


@ui_router.post("/actions/crawl-once")
async def ui_action_crawl_once(
    payload: CrawlOnceRequest | None = Body(default=None),
) -> dict[str, Any]:
    sync_rag = True if payload is None else bool(payload.sync_rag)
    return await _crawl_once(sync_rag=sync_rag)


@ui_router.post("/actions/rag-sync")
async def ui_action_rag_sync(
    payload: RAGSyncRequest | None = Body(default=None),
) -> dict[str, Any]:
    return await v1_rag_sync(payload=payload)


@ui_router.post("/actions/symbol-refresh")
async def ui_action_symbol_refresh(
    payload: SymbolRefreshRequest | None = Body(default=None),
) -> dict[str, Any]:
    return await v1_symbol_directory_refresh(payload=payload)


app.include_router(v1_router)
app.include_router(ui_router)


def run() -> None:
    import uvicorn

    if not settings.reports_api_token_list:
        raise RuntimeError(
            "TRADECRAFT_REPORTS_API_TOKEN or TRADECRAFT_REPORTS_API_TOKENS is required"
        )

    uvicorn.run(
        "tradecraft.reports_api.main:app",
        host=settings.reports_api_host,
        port=settings.reports_api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
