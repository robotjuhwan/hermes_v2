from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tradecraft.config import AppSettings
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
        llm_bridge_command=settings.llm_bridge_command,
        llm_bridge_args=settings.llm_bridge_args,
        llm_bridge_url=settings.llm_bridge_url,
        llm_bridge_token=settings.llm_bridge_token,
        llm_bridge_timeout_ms=settings.llm_bridge_timeout_ms,
        llm_model=settings.llm_model,
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
            llm_bridge_command=settings.llm_bridge_command,
            llm_bridge_args=settings.llm_bridge_args,
            llm_bridge_url=settings.llm_bridge_url,
            llm_bridge_token=settings.llm_bridge_token,
            llm_bridge_timeout_ms=settings.llm_bridge_timeout_ms,
            llm_model=settings.llm_model,
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
        "llm_bridge": {
            "mode": settings.llm_bridge_mode,
            "ready": settings.llm_bridge_ready,
            "model": settings.llm_model,
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
) -> dict[str, Any] | None:
    if not enabled or rag_store is None:
        return None
    docs = repository.list_chunks_for_rag(limit=max(int(limit), 1))
    if metadata_only:
        result = rag_store.sync_metadata(
            docs,
            prune_missing=bool(prune_missing) and len(docs) < max(int(limit), 1),
        )
        if prune_missing and len(docs) >= max(int(limit), 1):
            result["prune_skipped_reason"] = "input_limit_reached"
        return result
    return rag_store.sync_documents(docs, force_update=force_update)


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
