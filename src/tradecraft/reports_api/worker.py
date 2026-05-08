from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.intelligence import (
    build_report_crawler,
    build_report_rag_store,
    is_symbol_directory_stale,
    run_report_collection_cycle,
)
from tradecraft.services.naver_reports import (
    NaverReportRepository,
    NaverSecuritiesCrawler,
)
from tradecraft.services.rag_store import RAGStore

logger = logging.getLogger(__name__)


def _write_worker_state(
    state_store: RuntimeStateStore,
    *,
    status: str,
    cycle: int,
    interval_sec: int,
    last_success_at: str = "",
    last_error_at: str = "",
    last_error: str = "",
    snapshot: dict[str, Any] | None = None,
    symbol_refresh: dict[str, Any] | None = None,
    rag_sync: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "service": "reports_worker",
        "pid": os.getpid(),
        "status": status,
        "cycle": cycle,
        "interval_sec": interval_sec,
        "last_success_at": last_success_at,
        "last_error_at": last_error_at,
        "last_error": last_error,
    }
    if snapshot is not None:
        payload["snapshot"] = snapshot
    if symbol_refresh:
        payload["symbol_refresh"] = symbol_refresh
    if rag_sync:
        payload["rag_sync"] = rag_sync
    state_store.write_snapshot(payload)


_is_symbol_directory_stale = is_symbol_directory_stale


def build_crawler(settings: AppSettings) -> NaverSecuritiesCrawler:
    return build_report_crawler(settings)


def build_rag_store(settings: AppSettings) -> RAGStore | None:
    return build_report_rag_store(settings)


async def run_cycle(
    *,
    crawler: NaverSecuritiesCrawler,
    repository: NaverReportRepository,
    rag_store: RAGStore | None,
    settings: AppSettings,
) -> dict[str, Any]:
    return await run_report_collection_cycle(
        crawler=crawler,
        repository=repository,
        rag_store=rag_store,
        rag_enabled=settings.rag_enabled,
        rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
    )


def run() -> None:
    settings = AppSettings()
    interval = max(int(settings.naver_reports_interval_sec), 300)
    state_store = RuntimeStateStore(settings.reports_worker_state_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)

    _write_worker_state(
        state_store,
        status="starting",
        cycle=0,
        interval_sec=interval,
    )

    if not settings.naver_reports_enabled:
        logger.info("naver reports disabled: TRADECRAFT_NAVER_REPORTS_ENABLED=false")
        _write_worker_state(
            state_store,
            status="disabled",
            cycle=0,
            interval_sec=interval,
        )
        return

    crawler = build_crawler(settings)
    repository = NaverReportRepository(settings.naver_reports_db_path)
    rag = build_rag_store(settings)

    logger.info(
        "reports worker started: db_path=%s interval=%ss",
        settings.naver_reports_db_path,
        interval,
    )

    cycle = 0
    while True:
        cycle += 1
        try:
            result = asyncio.run(
                run_cycle(
                    crawler=crawler,
                    repository=repository,
                    rag_store=rag,
                    settings=settings,
                )
            )
            snapshot = result.get("snapshot") or {}
            symbol_refresh = result.get("symbol_refresh") or {}
            rag_sync = result.get("rag_sync") or {}

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
            if rag_sync:
                logger.info(
                    "rag sync status=%s synced=%s",
                    str(rag_sync.get("status") or "unknown"),
                    int(rag_sync.get("synced") or 0),
                )
            logger.info(
                "reports worker cycle=%s inserted=%s total=%s",
                cycle,
                int(snapshot.get("inserted") or 0),
                int((snapshot.get("repository") or {}).get("total_reports") or 0),
            )
            _write_worker_state(
                state_store,
                status="ok",
                cycle=cycle,
                interval_sec=interval,
                last_success_at=utc_now_iso(),
                snapshot=snapshot,
                symbol_refresh=symbol_refresh,
                rag_sync=rag_sync,
            )
        except Exception as exc:
            logger.warning("reports worker cycle failed: %s", exc)
            previous = state_store.read_snapshot() or {}
            _write_worker_state(
                state_store,
                status="error",
                cycle=cycle,
                interval_sec=interval,
                last_success_at=str(previous.get("last_success_at") or ""),
                last_error_at=utc_now_iso(),
                last_error=str(exc)[:300],
            )
        time.sleep(interval)


if __name__ == "__main__":
    run()
