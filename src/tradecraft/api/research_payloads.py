from __future__ import annotations

import inspect
from typing import Any, Callable


def _rag_unavailable_descriptor(
    *,
    reason: str,
    persist_path: str,
    collection_name: str,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "persist_path": persist_path,
        "collection_name": collection_name,
    }


def build_reports_status_payload(
    *,
    naver_reports_enabled: bool,
    repository_status: dict[str, Any],
    intelligence_status: dict[str, Any],
    rag_status: dict[str, Any],
    fundamentals_status: dict[str, Any],
) -> dict[str, Any]:
    repository_status = repository_status if isinstance(repository_status, dict) else {}
    rag_status = rag_status if isinstance(rag_status, dict) else {}
    fundamentals_status = (
        fundamentals_status if isinstance(fundamentals_status, dict) else {}
    )
    return {
        "status": "ok",
        "enabled": naver_reports_enabled,
        "report_count": int(repository_status.get("total_reports") or 0),
        "latest_report_at": str(repository_status.get("last_updated_at") or ""),
        "latest_published_at": str(repository_status.get("last_published_at") or ""),
        "db_path": str(repository_status.get("db_path") or ""),
        "symbol_count": int(repository_status.get("total_symbols") or 0),
        "symbol_link_count": int(repository_status.get("symbol_link_count") or 0),
        "rag_available": bool(rag_status.get("available")),
        "rag_count": int(rag_status.get("count") or 0),
        "fundamentals_symbol_count": int(
            fundamentals_status.get("total_symbols") or 0
        ),
        "fundamentals_stale_ratio": float(
            fundamentals_status.get("stale_ratio") or 0.0
        ),
        "fundamentals_latest_symbols_stale_ratio": float(
            fundamentals_status.get("latest_symbols_stale_ratio") or 0.0
        ),
        "repository": repository_status,
        "intelligence": intelligence_status,
        "rag": rag_status,
        "fundamentals": fundamentals_status,
    }


async def build_reports_crawl_once_payload(
    *,
    crawler: Any,
    repository: Any,
    rag_store: Any | None,
    rag_enabled: bool,
    rag_sync_chunk_limit: int,
    run_report_collection_cycle: Callable[..., Any],
) -> dict[str, Any]:
    result = run_report_collection_cycle(
        crawler=crawler,
        repository=repository,
        rag_store=rag_store,
        rag_enabled=rag_enabled,
        rag_sync_chunk_limit=rag_sync_chunk_limit,
        refresh_symbol_directory=False,
    )
    if inspect.isawaitable(result):
        result = await result
    result = result if isinstance(result, dict) else {}
    return {
        "status": "ok",
        "snapshot": result.get("snapshot"),
        "metadata_repair": result.get("metadata_repair"),
        "rag_sync": result.get("rag_sync"),
        "rag_metadata_sync": result.get("rag_metadata_sync"),
    }


def _sync_report_rag_metadata(
    *,
    repository: Any,
    rag_store: Any | None,
    rag_enabled: bool,
    rag_sync_chunk_limit: int,
    sync_report_rag: Callable[..., dict[str, Any] | None],
    prune_missing: bool,
) -> dict[str, Any] | None:
    return sync_report_rag(
        repository=repository,
        rag_store=rag_store,
        enabled=rag_enabled,
        limit=rag_sync_chunk_limit,
        metadata_only=True,
        prune_missing=prune_missing,
    )


def build_reports_repair_metadata_payload(
    *,
    repository: Any,
    rag_store: Any | None,
    rag_enabled: bool,
    rag_sync_chunk_limit: int,
    sync_report_rag: Callable[..., dict[str, Any] | None],
    sync_rag_after: bool,
    prune_orphans: bool,
) -> dict[str, Any]:
    repair = repository.repair_metadata_quality()
    rag_sync_result: dict[str, Any] | None = None
    if sync_rag_after:
        rag_sync_result = _sync_report_rag_metadata(
            repository=repository,
            rag_store=rag_store,
            rag_enabled=rag_enabled,
            rag_sync_chunk_limit=rag_sync_chunk_limit,
            sync_report_rag=sync_report_rag,
            prune_missing=prune_orphans,
        )
    return {
        "status": "ok",
        "repair": repair,
        "rag_sync": rag_sync_result,
    }


def build_reports_backfill_symbol_links_payload(
    *,
    repository: Any,
    rag_store: Any | None,
    rag_enabled: bool,
    rag_sync_chunk_limit: int,
    sync_report_rag: Callable[..., dict[str, Any] | None],
    seed_symbol_directory: Callable[[], Any],
    sync_rag_after: bool,
    limit: int,
) -> dict[str, Any]:
    seed_symbol_directory()
    backfill = repository.backfill_report_symbol_links(
        limit=max(int(limit), 0),
        asset_class="etf",
    )
    rag_sync_result: dict[str, Any] | None = None
    if sync_rag_after:
        rag_sync_result = _sync_report_rag_metadata(
            repository=repository,
            rag_store=rag_store,
            rag_enabled=rag_enabled,
            rag_sync_chunk_limit=rag_sync_chunk_limit,
            sync_report_rag=sync_report_rag,
            prune_missing=False,
        )
    return {
        "status": "ok",
        "backfill": backfill,
        "rag_sync": rag_sync_result,
    }


def build_rag_status_payload(
    *,
    rag_enabled: bool,
    rag_store: Any | None,
    persist_path: str,
    collection_name: str,
) -> dict[str, Any]:
    if not rag_enabled:
        return {
            "status": "ok",
            "enabled": False,
            "rag": _rag_unavailable_descriptor(
                reason="rag_disabled",
                persist_path=persist_path,
                collection_name=collection_name,
            ),
        }
    if rag_store is None:
        return {
            "status": "ok",
            "enabled": True,
            "rag": _rag_unavailable_descriptor(
                reason="rag_store_missing",
                persist_path=persist_path,
                collection_name=collection_name,
            ),
        }
    rag_status = rag_store.status()
    top_level_status = (
        "degraded"
        if isinstance(rag_status, dict)
        and str(rag_status.get("status") or "").lower() == "degraded"
        else "ok"
    )
    return {
        "status": top_level_status,
        "enabled": True,
        "rag": rag_status,
    }


def build_rag_sync_payload(
    *,
    rag_enabled: bool,
    rag_store: Any | None,
    repository: Any,
    limit: int,
    sync_report_rag: Callable[..., dict[str, Any] | None],
    force: bool = False,
    metadata_only: bool = False,
    prune_orphans: bool = False,
    rebuild: bool = False,
) -> dict[str, Any]:
    if not rag_enabled:
        return {
            "status": "ok",
            "enabled": False,
            "result": {
                "status": "skipped",
                "reason": "rag_disabled",
            },
        }
    if rag_store is None:
        return {
            "status": "ok",
            "enabled": True,
            "result": {
                "status": "skipped",
                "reason": "rag_store_missing",
            },
        }
    result = sync_report_rag(
        repository=repository,
        rag_store=rag_store,
        enabled=rag_enabled,
        limit=limit,
        force_update=force,
        metadata_only=metadata_only,
        prune_missing=prune_orphans,
        rebuild=rebuild,
    )
    return {
        "status": "ok",
        "enabled": True,
        "result": result or {"status": "skipped", "reason": "rag_store_missing"},
    }


def build_rag_search_payload(
    *,
    rag_enabled: bool,
    rag_store: Any | None,
    repository: Any,
    default_limit: int,
    query: str,
    symbol: str = "",
    broker: str = "",
    doc_id: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int | None = None,
    on_resolve_error: Callable[[Exception], None] | None = None,
) -> dict[str, Any]:
    if not rag_enabled or rag_store is None:
        return {
            "status": "ok",
            "enabled": False,
            "count": 0,
            "items": [],
        }
    resolved_limit = default_limit if limit is None else int(limit)
    auto_symbol: dict[str, Any] | None = None
    resolved_symbol = str(symbol or "").strip()
    if not resolved_symbol:
        resolver = getattr(repository, "resolve_symbol_from_text", None)
        if callable(resolver):
            try:
                candidate = resolver(query)
            except Exception as exc:
                if on_resolve_error is not None:
                    on_resolve_error(exc)
                candidate = None
            if isinstance(candidate, dict) and str(candidate.get("symbol") or ""):
                auto_symbol = {
                    "symbol": str(candidate.get("symbol") or ""),
                    "company_name": str(candidate.get("company_name") or ""),
                    "match_type": str(candidate.get("match_type") or ""),
                    "confidence": float(candidate.get("confidence") or 0.0),
                }
                resolved_symbol = auto_symbol["symbol"]
    rows = rag_store.query(
        query=query,
        symbol=resolved_symbol,
        broker=broker,
        doc_id=doc_id,
        date_from=date_from,
        date_to=date_to,
        limit=resolved_limit,
    )
    rag_status = _safe_rag_store_status(rag_store)
    fallback_reason = _rag_query_fallback_reason(rag_status)
    retrieval_source = "rag"
    if not rows and fallback_reason:
        rows = _fallback_report_search_rows(
            repository=repository,
            query=query,
            symbol=resolved_symbol,
            limit=resolved_limit,
            auto_symbol=auto_symbol,
        )
        if rows:
            retrieval_source = "reports_fallback"
    return {
        "status": "degraded" if retrieval_source == "reports_fallback" else "ok",
        "enabled": True,
        "count": len(rows),
        "items": rows,
        "auto_symbol": auto_symbol,
        "retrieval_source": retrieval_source,
        **(
            {"fallback_reason": fallback_reason}
            if retrieval_source == "reports_fallback"
            else {}
        ),
    }


def _safe_rag_store_status(rag_store: Any) -> dict[str, Any]:
    status_func = getattr(rag_store, "status", None)
    if not callable(status_func):
        return {}
    try:
        status = status_func()
    except Exception:
        return {}
    return status if isinstance(status, dict) else {}


def _rag_query_fallback_reason(rag_status: dict[str, Any]) -> str:
    if str(rag_status.get("last_query_error") or "").strip():
        return "rag_query_degraded"
    if str(rag_status.get("status") or "").strip().lower() == "degraded":
        return "rag_query_degraded"
    return ""


def _fallback_report_search_rows(
    *,
    repository: Any,
    query: str,
    symbol: str,
    limit: int,
    auto_symbol: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    search = getattr(repository, "search", None)
    if not callable(search):
        return []
    clean_query = str(query or "").strip()
    clean_symbol = str(symbol or "").strip()
    max_rows = max(min(int(limit), 50), 1)
    for candidate_query in _fallback_report_queries(clean_query, auto_symbol=auto_symbol):
        try:
            rows = search(query=candidate_query, symbol=clean_symbol, limit=max_rows)
        except TypeError:
            rows = search(query=candidate_query, symbol=clean_symbol)
        except Exception:
            rows = []
        normalized = [
            _report_search_row_to_rag_item(row, fallback_query=candidate_query)
            for row in list(rows or [])
            if isinstance(row, dict)
        ]
        if normalized:
            return normalized[:max_rows]
    return []


def _fallback_report_queries(
    query: str,
    *,
    auto_symbol: dict[str, Any] | None,
) -> list[str]:
    queries: list[str] = []

    def add(value: str) -> None:
        clean = " ".join(str(value or "").split())
        if clean not in queries:
            queries.append(clean)

    add(query)
    company_name = ""
    if isinstance(auto_symbol, dict):
        company_name = str(
            auto_symbol.get("company_name") or auto_symbol.get("name") or ""
        ).strip()
    if company_name:
        without_company = " ".join(
            token for token in query.split() if token.strip() != company_name
        )
        add(without_company)
        leading_removed = query.replace(company_name, " ", 1)
        add(leading_removed)
    tokens = [token.strip() for token in query.split() if token.strip()]
    if len(tokens) > 1:
        add(" ".join(tokens[:2]))
        add(" ".join(tokens[-2:]))
    add("")
    return queries


def _report_search_row_to_rag_item(
    row: dict[str, Any],
    *,
    fallback_query: str,
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "")
    company_name = str(row.get("company_name") or "")
    return {
        "content": str(row.get("snippet") or row.get("content") or ""),
        "distance": None,
        "doc_id": str(row.get("doc_id") or ""),
        "report_id": int(row.get("report_id") or 0),
        "chunk_index": 0,
        "symbol": symbol,
        "category": str(row.get("category") or "unknown"),
        "title": str(row.get("title") or ""),
        "broker": str(row.get("broker") or ""),
        "published_at": str(row.get("published_at") or ""),
        "page_start": 0,
        "page_end": 0,
        "section_title": "reports_search_fallback",
        "pdf_url": str(row.get("pdf_url") or ""),
        "detail_url": str(row.get("detail_url") or ""),
        "linked_symbols": symbol,
        "linked_names": company_name,
        "linked_asset_classes": "equity" if symbol else "",
        "retrieval_source": "reports_fallback",
        "fallback_query": str(fallback_query or ""),
    }
