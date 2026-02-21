from __future__ import annotations

import asyncio
import logging
import time

from tradecraft.config import AppSettings
from tradecraft.services.naver_reports import (
    NaverReportCrawlerConfig,
    NaverReportRepository,
    NaverSecuritiesCrawler,
)
from tradecraft.services.rag_store import RAGStore, RAGStoreConfig

logger = logging.getLogger(__name__)


def _build_crawler(settings: AppSettings) -> NaverSecuritiesCrawler:
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
    )
    return NaverSecuritiesCrawler(config=config, repository=repository)


def run() -> None:
    settings = AppSettings()
    interval = max(int(settings.naver_reports_interval_sec), 300)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if not settings.naver_reports_enabled:
        logger.info("naver reports disabled: TRADECRAFT_NAVER_REPORTS_ENABLED=false")
        return

    crawler = _build_crawler(settings)
    repository = NaverReportRepository(settings.naver_reports_db_path)
    rag = (
        RAGStore(
            RAGStoreConfig(
                persist_path=settings.rag_persist_path,
                collection_name=settings.rag_collection_name,
            )
        )
        if settings.rag_enabled
        else None
    )
    logger.info(
        "naver reports crawler started: db_path=%s interval=%ss",
        settings.naver_reports_db_path,
        interval,
    )

    cycle = 0
    while True:
        cycle += 1
        try:
            snapshot = asyncio.run(crawler.crawl_once())
            if settings.rag_enabled and rag is not None:
                docs = repository.list_chunks_for_rag(
                    limit=settings.rag_sync_chunk_limit
                )
                rag_result = rag.sync_documents(docs)
                logger.info(
                    "rag sync status=%s synced=%s",
                    str(rag_result.get("status") or "unknown"),
                    int(rag_result.get("synced") or 0),
                )
            logger.info(
                "naver reports cycle=%s inserted=%s total=%s",
                cycle,
                int(snapshot.get("inserted") or 0),
                int((snapshot.get("repository") or {}).get("total_reports") or 0),
            )
        except Exception as exc:
            logger.warning("naver reports cycle failed: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    run()
