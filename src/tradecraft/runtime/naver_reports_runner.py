from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.intelligence import (
    build_report_intelligence_stack,
    is_symbol_directory_stale,
    run_report_collection_cycle,
    run_report_collection_cycle_with_timeout,
)

logger = logging.getLogger(__name__)
_is_symbol_directory_stale = is_symbol_directory_stale
CollectOnceFn = Callable[[], dict[str, Any]]
SleepFn = Callable[[float], None]


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_from_iso(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _next_run_at(started_at: str, interval: int) -> str:
    started_ts = _timestamp_from_iso(started_at)
    if started_ts <= 0:
        return ""
    return datetime.fromtimestamp(started_ts + interval, tz=timezone.utc).isoformat()


def _rag_sync_error_detail(rag_result: dict[str, Any]) -> str:
    for key in ("error_message", "error", "reason", "detail"):
        detail = str(rag_result.get(key) or "").strip()
        if detail:
            return detail[:500]
    return ""


def _build_default_collect_once(settings: AppSettings) -> CollectOnceFn:
    stack = build_report_intelligence_stack(settings)

    def collect_once() -> dict[str, Any]:
        return asyncio.run(
            run_report_collection_cycle_with_timeout(
                run_report_collection_cycle(
                    crawler=stack.crawler,
                    repository=stack.repository,
                    rag_store=stack.rag_store,
                    rag_enabled=settings.rag_enabled,
                    rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
                ),
                timeout_sec=max(int(settings.naver_reports_cycle_timeout_sec), 0),
            )
        )

    logger.info(
        "naver reports crawler started: db_path=%s interval=%ss",
        settings.naver_reports_db_path,
        max(int(settings.naver_reports_interval_sec), 300),
    )
    return collect_once


def run_naver_reports_loop(
    *,
    settings: Any,
    collect_once: CollectOnceFn | None = None,
    sleep: SleepFn = time.sleep,
    once: bool = False,
) -> None:
    interval = max(int(_setting(settings, "naver_reports_interval_sec", 21_600)), 300)
    cycle_timeout_sec = max(int(_setting(settings, "naver_reports_cycle_timeout_sec", 0)), 0)
    state_store = RuntimeStateStore(
        str(
            _setting(
                settings,
                "naver_reports_state_path",
                ".runtime/naver_reports_runner.json",
            )
        )
    )

    if not bool(_setting(settings, "naver_reports_enabled", False)):
        logger.info("naver reports disabled: TRADECRAFT_NAVER_REPORTS_ENABLED=false")
        state_store.write_snapshot(
            {
                "service": "tradecraft-naver-reports",
                "status": "disabled",
                "updated_at": _utc_now_iso(),
                "interval_sec": interval,
                "reason": "naver_reports_disabled",
            }
        )
        return

    cycle = 0
    previous_snapshot = state_store.read_snapshot() or {}
    last_collection_started_at = str(
        previous_snapshot.get("last_collection_started_at") or ""
    )

    while True:
        cycle += 1
        now_ts = time.time()
        last_started_ts = _timestamp_from_iso(last_collection_started_at)
        remaining_sec = (
            max(int(interval - (now_ts - last_started_ts)), 0)
            if last_started_ts > 0
            else 0
        )
        if remaining_sec > 0:
            state_store.write_snapshot(
                {
                    "service": "tradecraft-naver-reports",
                    "status": "skipped",
                    "reason": "cadence",
                    "cycle": cycle,
                    "updated_at": _utc_now_iso(),
                    "interval_sec": interval,
                    "remaining_sec": remaining_sec,
                    "last_collection_started_at": last_collection_started_at,
                    "next_run_at": _next_run_at(last_collection_started_at, interval),
                }
            )
            logger.info(
                "naver reports cycle=%s skipped cadence remaining=%ss",
                cycle,
                remaining_sec,
            )
            if once:
                return
            sleep(float(remaining_sec))
            continue

        last_collection_started_at = _utc_now_iso()
        state_store.write_snapshot(
            {
                "service": "tradecraft-naver-reports",
                "status": "collecting",
                "cycle": cycle,
                "updated_at": _utc_now_iso(),
                "interval_sec": interval,
                "cycle_timeout_sec": cycle_timeout_sec,
                "last_collection_started_at": last_collection_started_at,
                "next_run_at": _next_run_at(last_collection_started_at, interval),
            }
        )
        try:
            resolved_collect_once = collect_once or _build_default_collect_once(settings)
            logger.info(
                "naver reports cycle=%s starting timeout=%ss",
                cycle,
                cycle_timeout_sec,
            )
            result = resolved_collect_once()
            if result.get("status") == "timeout":
                logger.warning(
                    "naver reports cycle=%s timeout after %ss",
                    cycle,
                    cycle_timeout_sec,
                )
                state_store.write_snapshot(
                    {
                        "service": "tradecraft-naver-reports",
                        "status": "timeout",
                        "cycle": cycle,
                        "updated_at": _utc_now_iso(),
                        "interval_sec": interval,
                        "cycle_timeout_sec": cycle_timeout_sec,
                        "last_collection_started_at": last_collection_started_at,
                        "next_run_at": _next_run_at(last_collection_started_at, interval),
                        "result": result,
                    }
                )
                if once:
                    return
                sleep(float(interval))
                continue
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
                rag_status = str(rag_result.get("status") or "unknown")
                rag_synced = int(rag_result.get("synced") or 0)
                rag_error = _rag_sync_error_detail(rag_result)
                if rag_status == "error" or rag_error:
                    logger.warning(
                        "rag sync status=%s synced=%s error=%s",
                        rag_status,
                        rag_synced,
                        rag_error or "unknown",
                    )
                else:
                    logger.info(
                        "rag sync status=%s synced=%s",
                        rag_status,
                        rag_synced,
                    )
            logger.info(
                "naver reports cycle=%s inserted=%s total=%s",
                cycle,
                int(snapshot.get("inserted") or 0),
                int((snapshot.get("repository") or {}).get("total_reports") or 0),
            )
            state_store.write_snapshot(
                {
                    "service": "tradecraft-naver-reports",
                    "status": str(result.get("status") or "ok"),
                    "cycle": cycle,
                    "updated_at": _utc_now_iso(),
                    "interval_sec": interval,
                    "cycle_timeout_sec": cycle_timeout_sec,
                    "last_collection_started_at": last_collection_started_at,
                    "last_collection_completed_at": _utc_now_iso(),
                    "next_run_at": _next_run_at(last_collection_started_at, interval),
                    "snapshot": snapshot,
                    "symbol_refresh": symbol_refresh,
                    "rag_sync": rag_result,
                }
            )
        except Exception as exc:
            logger.warning("naver reports cycle failed: %s", exc, exc_info=True)
            state_store.write_snapshot(
                {
                    "service": "tradecraft-naver-reports",
                    "status": "error",
                    "cycle": cycle,
                    "updated_at": _utc_now_iso(),
                    "interval_sec": interval,
                    "cycle_timeout_sec": cycle_timeout_sec,
                    "last_collection_started_at": last_collection_started_at,
                    "next_run_at": _next_run_at(last_collection_started_at, interval),
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                }
            )
        if once:
            return
        sleep(float(interval))


def run() -> None:
    write_current_runner_pid("naver_reports")
    settings = AppSettings()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)

    try:
        run_naver_reports_loop(settings=settings)
    except KeyboardInterrupt:
        logger.info("naver reports runner interrupted; stopping")
    finally:
        clear_current_runner_pid("naver_reports")


if __name__ == "__main__":
    run()
