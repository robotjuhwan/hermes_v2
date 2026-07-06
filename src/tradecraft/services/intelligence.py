from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable

from tradecraft.config import AppSettings
from tradecraft.services.codex_native import codex_native_service_config_kwargs
from tradecraft.services.naver_reports import (
    NaverReportCrawlerConfig,
    NaverReportRepository,
    NaverSecuritiesCrawler,
)
from tradecraft.services.rag_store import RAGStore, RAGStoreConfig


@dataclass(slots=True)
class ReportIntelligenceStack:
    repository: NaverReportRepository
    crawler: NaverSecuritiesCrawler
    rag_store: RAGStore | None


def build_report_crawler(settings: AppSettings) -> NaverSecuritiesCrawler:
    repository = NaverReportRepository(settings.naver_reports_db_path)
    config = NaverReportCrawlerConfig(
        db_path=settings.naver_reports_db_path,
        pdf_archive_dir=settings.naver_reports_pdf_archive_dir,
        seed_url=settings.naver_reports_seed_url,
        seed_urls=settings.naver_reports_seed_url_list,
        max_pages=settings.naver_reports_max_pages,
        since_date=settings.naver_reports_since_date,
        request_delay_sec=settings.naver_reports_request_delay_sec,
        min_pdf_text_chars=settings.naver_reports_min_pdf_text_chars,
        codex_runtime_mode=settings.codex_runtime_mode,
        codex_runtime_sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
        codex_runtime_timeout_ms=settings.codex_runtime_timeout_ms,
        llm_model=settings.llm_model,
        llm_reasoning_effort=settings.llm_reasoning_effort,
        llm_usage_enabled=settings.llm_usage_enabled,
        llm_usage_db_path=settings.llm_usage_db_path,
        llm_usage_component="research_reports",
        **codex_native_service_config_kwargs(settings),
        llm_facts_enabled=settings.naver_reports_llm_facts_enabled,
    )
    return NaverSecuritiesCrawler(config=config, repository=repository)


def build_report_rag_store(settings: AppSettings) -> RAGStore | None:
    if not settings.rag_enabled:
        return None
    return RAGStore(
        RAGStoreConfig(
            persist_path=settings.rag_persist_path,
            collection_name=settings.rag_collection_name,
            sync_batch_size=settings.rag_sync_batch_size,
            skip_existing=settings.rag_skip_existing,
            query_oversample_factor=settings.rag_query_oversample_factor,
            allow_legacy_pickle_migration=settings.rag_allow_legacy_pickle_migration,
        )
    )


def build_report_intelligence_stack(settings: AppSettings) -> ReportIntelligenceStack:
    repository = NaverReportRepository(settings.naver_reports_db_path)
    crawler = NaverSecuritiesCrawler(
        config=NaverReportCrawlerConfig(
            db_path=settings.naver_reports_db_path,
            pdf_archive_dir=settings.naver_reports_pdf_archive_dir,
            seed_url=settings.naver_reports_seed_url,
            seed_urls=settings.naver_reports_seed_url_list,
            max_pages=settings.naver_reports_max_pages,
            since_date=settings.naver_reports_since_date,
            request_delay_sec=settings.naver_reports_request_delay_sec,
            min_pdf_text_chars=settings.naver_reports_min_pdf_text_chars,
            codex_runtime_mode=settings.codex_runtime_mode,
            codex_runtime_sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
            codex_runtime_timeout_ms=settings.codex_runtime_timeout_ms,
            llm_model=settings.llm_model,
            llm_reasoning_effort=settings.llm_reasoning_effort,
            llm_usage_enabled=settings.llm_usage_enabled,
            llm_usage_db_path=settings.llm_usage_db_path,
            llm_usage_component="research_reports",
            **codex_native_service_config_kwargs(settings),
            llm_facts_enabled=settings.naver_reports_llm_facts_enabled,
        ),
        repository=repository,
    )
    return ReportIntelligenceStack(
        repository=repository,
        crawler=crawler,
        rag_store=build_report_rag_store(settings),
    )


def build_report_intelligence_status(settings: AppSettings) -> dict[str, Any]:
    return {
        "codex_runtime": {
            "mode": settings.codex_runtime_mode,
            "ready": settings.codex_runtime_ready,
            "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
        },
        "llm_facts": {
            "enabled": bool(settings.naver_reports_llm_facts_enabled),
            "active": settings.naver_reports_llm_facts_active,
        },
        "rag": {
            "enabled": bool(settings.rag_enabled),
            "collection_name": settings.rag_collection_name,
            "query_top_k": int(settings.rag_query_top_k),
            "sync_chunk_limit": int(settings.rag_sync_chunk_limit),
            "sync_batch_size": int(settings.rag_sync_batch_size),
            "skip_existing": bool(settings.rag_skip_existing),
            "query_oversample_factor": int(settings.rag_query_oversample_factor),
        },
    }


def is_symbol_directory_stale(last_updated_at: str, *, min_age_sec: int) -> bool:
    raw = str(last_updated_at or "").strip()
    if not raw:
        return True
    try:
        updated_dt = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    age_sec = (
        datetime.now(timezone.utc) - updated_dt.astimezone(timezone.utc)
    ).total_seconds()
    return age_sec >= max(int(min_age_sec), 1)


def sync_report_rag(
    *,
    repository: NaverReportRepository,
    rag_store: RAGStore | None,
    enabled: bool,
    limit: int,
    force_update: bool = False,
    metadata_only: bool = False,
    prune_missing: bool = False,
    updated_since: str | None = None,
    rebuild: bool = False,
) -> dict[str, Any] | None:
    if not enabled or rag_store is None:
        return None
    rebuild_result: dict[str, Any] | None = None
    if rebuild:
        if metadata_only:
            return {
                "status": "error",
                "reason": "rag_rebuild_requires_document_sync",
                "detail": "rebuild=true cannot be combined with metadata_only=true",
                "rebuild": True,
            }
        rebuild_func = getattr(rag_store, "rebuild_persistent_store", None)
        if not callable(rebuild_func):
            return {
                "status": "error",
                "reason": "rag_rebuild_unsupported",
                "rebuild": True,
            }
        rebuild_result = rebuild_func()
        if str(rebuild_result.get("status") or "").lower() != "ok":
            return {
                "status": "error",
                "reason": "rag_rebuild_failed",
                "rebuild": rebuild_result,
            }
    clean_updated_since = str(updated_since or "").strip()
    if clean_updated_since and not rebuild:
        try:
            status = rag_store.status()
            if int(status.get("count") or 0) <= 0:
                clean_updated_since = ""
        except Exception:
            clean_updated_since = ""
    if clean_updated_since:
        docs = repository.list_chunks_for_rag(
            limit=max(int(limit), 1),
            updated_since=clean_updated_since,
        )
    else:
        docs = repository.list_chunks_for_rag(limit=max(int(limit), 1))
    should_prune_missing = bool(prune_missing) and len(docs) < max(int(limit), 1)
    if metadata_only:
        result = rag_store.sync_metadata(
            docs,
            prune_missing=should_prune_missing,
        )
        if prune_missing and len(docs) >= max(int(limit), 1):
            result["prune_skipped_reason"] = "input_limit_reached"
        return result
    result = rag_store.sync_documents(
        docs,
        force_update=bool(force_update or rebuild),
        prune_missing=False if rebuild else should_prune_missing,
    )
    if prune_missing and len(docs) >= max(int(limit), 1):
        result["prune_skipped_reason"] = "input_limit_reached"
    if rebuild_result is not None:
        result["rebuild"] = rebuild_result
    return result


async def run_report_collection_cycle(
    *,
    crawler: NaverSecuritiesCrawler,
    repository: NaverReportRepository,
    rag_store: RAGStore | None,
    rag_enabled: bool,
    rag_sync_chunk_limit: int,
    refresh_symbol_directory: bool = True,
    symbol_refresh_min_age_sec: int = 60 * 60 * 12,
    sync_rag: bool = True,
) -> dict[str, Any]:
    cycle_started_at = datetime.now(timezone.utc).isoformat()
    snapshot = await crawler.crawl_once()
    symbol_refresh: dict[str, Any] | None = None
    rag_sync: dict[str, Any] | None = None
    rag_metadata_sync: dict[str, Any] | None = None

    if refresh_symbol_directory:
        try:
            status = repository.status()
            symbol_last_updated_at = str(status.get("symbol_last_updated_at") or "")
            if is_symbol_directory_stale(
                symbol_last_updated_at,
                min_age_sec=symbol_refresh_min_age_sec,
            ):
                symbol_refresh = repository.refresh_symbol_directory_from_krx()
        except Exception as exc:
            symbol_refresh = {
                "ok": False,
                "reason": "symbol_directory_refresh_failed",
                "detail": str(exc)[:300],
            }

    metadata_repair = repository.repair_metadata_quality()

    if sync_rag:
        rag_sync = sync_report_rag(
            repository=repository,
            rag_store=rag_store,
            enabled=rag_enabled,
            limit=rag_sync_chunk_limit,
            updated_since=cycle_started_at,
        )
        if metadata_repair.get("updated_reports"):
            rag_metadata_sync = sync_report_rag(
                repository=repository,
                rag_store=rag_store,
                enabled=rag_enabled,
                limit=rag_sync_chunk_limit,
                metadata_only=True,
            )

    return {
        "snapshot": snapshot,
        "symbol_refresh": symbol_refresh,
        "metadata_repair": metadata_repair,
        "rag_sync": rag_sync,
        "rag_metadata_sync": rag_metadata_sync,
    }


async def run_report_collection_cycle_with_timeout(
    cycle: Awaitable[dict[str, Any]],
    *,
    timeout_sec: float,
) -> dict[str, Any]:
    resolved_timeout = float(timeout_sec or 0)
    if resolved_timeout <= 0:
        return await cycle
    started_at = datetime.now(timezone.utc)
    try:
        return await asyncio.wait_for(cycle, timeout=resolved_timeout)
    except asyncio.TimeoutError:
        return {
            "status": "timeout",
            "timeout_sec": resolved_timeout,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error_message": (
                f"naver report collection exceeded {resolved_timeout:.1f}s"
            ),
        }
