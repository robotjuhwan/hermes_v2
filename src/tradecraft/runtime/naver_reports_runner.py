from __future__ import annotations

import asyncio
import logging
import time

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.services.intelligence import (
    build_report_intelligence_stack,
    is_symbol_directory_stale,
    run_report_collection_cycle,
)

logger = logging.getLogger(__name__)
_is_symbol_directory_stale = is_symbol_directory_stale


def run() -> None:
    write_current_runner_pid("naver_reports")
    settings = AppSettings()
    interval = max(int(settings.naver_reports_interval_sec), 300)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)

    if not settings.naver_reports_enabled:
        logger.info("naver reports disabled: TRADECRAFT_NAVER_REPORTS_ENABLED=false")
        return

    stack = build_report_intelligence_stack(settings)
    logger.info(
        "naver reports crawler started: db_path=%s interval=%ss",
        settings.naver_reports_db_path,
        interval,
    )

    cycle = 0
    while True:
        cycle += 1
        try:
            result = asyncio.run(
                run_report_collection_cycle(
                    crawler=stack.crawler,
                    repository=stack.repository,
                    rag_store=stack.rag_store,
                    rag_enabled=settings.rag_enabled,
                    rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
                )
            )
            snapshot = result.get("snapshot") or {}
            symbol_refresh = result.get("symbol_refresh") or {}
            rag_result = result.get("rag_sync") or {}
            if symbol_refresh:
                if bool(symbol_refresh.get("ok")):
                    logger.info(
                        "symbol directory refreshed: updated=%s as_of=%s",
                        int(symbol_refresh.get("updated") or 0),
                        str(symbol_refresh.get("as_of") or ""),
                    )
                else:
                    logger.warning(
                        "symbol directory refresh skipped/failed: reason=%s detail=%s",
                        str(symbol_refresh.get("reason") or "unknown"),
                        str(symbol_refresh.get("detail") or "")[:200],
                    )
            if rag_result:
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
