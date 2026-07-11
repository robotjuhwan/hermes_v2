from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.intelligence import is_symbol_directory_stale

logger = logging.getLogger(__name__)
_is_symbol_directory_stale = is_symbol_directory_stale
CollectOnceFn = Callable[[], dict[str, Any]]
SleepFn = Callable[[float], None]
MonotonicFn = Callable[[], float]


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


def _worker_state_paths(
    state_path: Path,
    *,
    parent_pid: int | None = None,
) -> tuple[Path, Path]:
    resolved_parent_pid = int(parent_pid if parent_pid is not None else os.getpid())
    worker_stem = f"{state_path.stem}.worker-{resolved_parent_pid}"
    return (
        state_path.with_name(f"{worker_stem}-result.json"),
        state_path.with_name(f"{worker_stem}-progress.json"),
    )


def _latest_successful_worker_started_at(state_path: Path) -> str:
    latest_text = ""
    latest_timestamp = 0.0
    pattern = f"{state_path.stem}.worker*-result.json"
    for result_path in state_path.parent.glob(pattern):
        payload = RuntimeStateStore(result_path).read_snapshot() or {}
        if str(payload.get("status") or "").lower() != "ok":
            continue
        started_at = str(payload.get("started_at") or "").strip()
        started_timestamp = _timestamp_from_iso(started_at)
        if started_timestamp <= latest_timestamp:
            continue
        latest_text = started_at
        latest_timestamp = started_timestamp
    return latest_text


def _rag_sync_error_detail(rag_result: dict[str, Any]) -> str:
    for key in ("error_message", "error", "reason", "detail"):
        detail = str(rag_result.get(key) or "").strip()
        if detail:
            return detail[:500]
    return ""


def wait_for_exit(
    process: Any,
    *,
    grace_sec: float,
    monotonic: MonotonicFn = time.monotonic,
    sleep: SleepFn = time.sleep,
) -> bool:
    grace = max(float(grace_sec), 0.0)
    if process.poll() is not None:
        return True
    if grace <= 0:
        return False
    deadline = monotonic() + grace
    while monotonic() < deadline:
        sleep(min(0.1, max(deadline - monotonic(), 0.0)))
        if process.poll() is not None:
            return True
    return process.poll() is not None


def supervise_report_worker(
    *,
    process: Any,
    result_store: RuntimeStateStore,
    progress_store: RuntimeStateStore,
    parent_state: RuntimeStateStore,
    timeout_sec: float,
    heartbeat_interval_sec: float,
    terminate_grace_sec: float = 0.0,
    monotonic: MonotonicFn = time.monotonic,
    sleep: SleepFn = time.sleep,
) -> dict[str, Any]:
    started_at = _utc_now_iso()
    timeout = max(float(timeout_sec), 0.1)
    deadline = monotonic() + timeout
    deadline_at = (
        datetime.now(timezone.utc) + timedelta(seconds=timeout)
    ).isoformat()
    heartbeat_interval = max(float(heartbeat_interval_sec), 0.1)
    parent_context = dict(parent_state.read_snapshot() or {})

    while process.poll() is None:
        progress = progress_store.read_snapshot() or {}
        parent_state.write_snapshot(
            {
                **parent_context,
                "service": "tradecraft-naver-reports",
                "status": "collecting",
                "stage": str(progress.get("stage") or "starting"),
                "stage_started_at": str(
                    progress.get("stage_started_at") or started_at
                ),
                "heartbeat_at": _utc_now_iso(),
                "deadline_at": deadline_at,
                "worker_pid": int(process.pid),
            }
        )
        if monotonic() >= deadline:
            process.terminate()
            if not wait_for_exit(
                process,
                grace_sec=terminate_grace_sec,
                monotonic=monotonic,
                sleep=sleep,
            ):
                process.kill()
            return {
                "status": "timeout",
                "timeout_sec": timeout,
                "started_at": started_at,
                "finished_at": _utc_now_iso(),
                "worker_pid": int(process.pid),
                "stage": str(progress.get("stage") or "starting"),
            }
        sleep(heartbeat_interval)

    result = result_store.read_snapshot() or {}
    if str(result.get("status") or "") in {"", "pending"}:
        return {
            "status": "error",
            "error_message": "naver reports worker exited without a result",
            "worker_exit_code": process.poll(),
            "worker_pid": int(process.pid),
        }
    return dict(result)


def _build_default_collect_once(settings: AppSettings) -> CollectOnceFn:
    state_path = Path(
        str(
            _setting(
                settings,
                "naver_reports_state_path",
                ".runtime/naver_reports_runner.json",
            )
        )
    )
    result_path, progress_path = _worker_state_paths(state_path)
    result_store = RuntimeStateStore(result_path)
    progress_store = RuntimeStateStore(progress_path)
    parent_state = RuntimeStateStore(state_path)

    def collect_once() -> dict[str, Any]:
        result_store.write_snapshot({"status": "pending"})
        progress_store.write_snapshot(
            {
                "status": "pending",
                "stage": "starting",
                "stage_started_at": _utc_now_iso(),
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tradecraft.runtime.naver_reports_worker",
                "--result-path",
                str(result_path),
                "--progress-path",
                str(progress_path),
            ],
            start_new_session=True,
        )
        return supervise_report_worker(
            process=process,
            result_store=result_store,
            progress_store=progress_store,
            parent_state=parent_state,
            timeout_sec=max(int(settings.naver_reports_cycle_timeout_sec), 1),
            heartbeat_interval_sec=max(
                float(settings.naver_reports_heartbeat_interval_sec),
                0.1,
            ),
            terminate_grace_sec=max(
                float(settings.naver_reports_worker_terminate_grace_sec),
                0.0,
            ),
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
    if not last_collection_started_at:
        last_collection_started_at = _latest_successful_worker_started_at(
            Path(
                str(
                    _setting(
                        settings,
                        "naver_reports_state_path",
                        ".runtime/naver_reports_runner.json",
                    )
                )
            )
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
