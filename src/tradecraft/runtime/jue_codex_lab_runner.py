from __future__ import annotations

import logging
import multiprocessing as mp
import queue
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.services.codex_native import CodexNativeConfig, CodexNativeRuntime
from tradecraft.services.jue_codex_lab import JueCodexLabService
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore

logger = logging.getLogger(__name__)
KILL_JOIN_TIMEOUT_SEC = 5.0


def run_jue_codex_lab_cycle(*, lab: Any, max_tasks: int) -> dict[str, Any]:
    kis_ingest = lab.ingest_validation_work_queue(venue="kis")
    binance_ingest = lab.ingest_validation_work_queue(venue="binance")
    repair_result = lab.run_once(max_tasks=max_tasks)
    return {
        "status": str(repair_result.get("status") or "ok"),
        "ingest": {
            "kis": kis_ingest,
            "binance": binance_ingest,
        },
        "repair": repair_result,
    }


def _run_cycle_worker(lab: Any, max_tasks: int, result_queue: Any) -> None:
    try:
        result_queue.put(run_jue_codex_lab_cycle(lab=lab, max_tasks=max_tasks))
    except BaseException as exc:
        result_queue.put(
            {
                "status": "error",
                "ingest": {},
                "repair": {
                    "status": "error",
                    "processed_count": 0,
                    "deployed_count": 0,
                    "failed_count": 1,
                    "errors": [
                        {
                            "reason": "cycle_worker_error",
                            "message": str(exc),
                            "exception_type": type(exc).__name__,
                        }
                    ],
                },
            }
        )


def _mark_cycle_timeout(lab: Any, *, timeout_sec: float) -> list[dict[str, Any]]:
    store = getattr(lab, "store", None)
    if store is None:
        return []
    mark_failed = getattr(store, "mark_running_repair_runs_failed", None)
    if not callable(mark_failed):
        return []
    try:
        from datetime import datetime, timezone

        return list(
            mark_failed(
                status="cycle_timeout",
                message=(
                    "jue codex lab isolated worker exceeded "
                    f"{max(float(timeout_sec), 0.1):.1f}s"
                ),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        )
    except Exception:
        logger.exception("failed to mark timed out jue codex lab repair runs")
        return []


def run_jue_codex_lab_cycle_isolated(
    *,
    lab: Any,
    max_tasks: int,
    timeout_sec: float,
) -> dict[str, Any]:
    method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    ctx = mp.get_context(method)
    result_queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_run_cycle_worker,
        args=(lab, max_tasks, result_queue),
    )
    process.start()
    process.join(max(float(timeout_sec), 0.1))
    if process.is_alive():
        process.terminate()
        process.join(KILL_JOIN_TIMEOUT_SEC)
        if process.is_alive():
            process.kill()
            process.join(KILL_JOIN_TIMEOUT_SEC)
        closed_runs = _mark_cycle_timeout(lab, timeout_sec=timeout_sec)
        return {
            "status": "error",
            "ingest": {},
            "repair": {
                "status": "error",
                "processed_count": 0,
                "deployed_count": 0,
                "failed_count": 1,
                "errors": [
                    {
                        "reason": "cycle_timeout",
                        "message": (
                            "jue codex lab cycle exceeded isolated process timeout"
                        ),
                        "timeout_sec": max(float(timeout_sec), 0.1),
                        "closed_running_runs": closed_runs,
                    }
                ],
            },
        }
    if process.exitcode not in (0, None):
        return {
            "status": "error",
            "ingest": {},
            "repair": {
                "status": "error",
                "processed_count": 0,
                "deployed_count": 0,
                "failed_count": 1,
                "errors": [
                    {
                        "reason": "cycle_process_exit",
                        "message": "jue codex lab cycle process exited non-zero",
                        "exitcode": process.exitcode,
                    }
                ],
            },
        }
    try:
        result = result_queue.get_nowait()
    except queue.Empty:
        return {
            "status": "error",
            "ingest": {},
            "repair": {
                "status": "error",
                "processed_count": 0,
                "deployed_count": 0,
                "failed_count": 1,
                "errors": [
                    {
                        "reason": "cycle_result_missing",
                        "message": "jue codex lab cycle did not return a result",
                    }
                ],
            },
        }
    return result if isinstance(result, dict) else {"status": "error", "repair": result}


def _build_lab(settings: AppSettings) -> JueCodexLabService:
    return JueCodexLabService(
        JueCodexLabStore(settings.jue_codex_lab_db_path),
        validation_db_path=settings.trading_validation_db_path,
        codex_runtime=_build_codex_runtime(settings),
        autonomy_mode=settings.jue_codex_lab_autonomy_mode,
        max_patch_bytes=int(settings.jue_codex_lab_max_patch_bytes),
        allowed_paths=settings.jue_codex_lab_allowed_paths,
        blocked_paths=settings.jue_codex_lab_blocked_paths,
        market_hours_hot_deploy=bool(settings.jue_codex_lab_market_hours_hot_deploy),
    )


def _build_codex_runtime(settings: AppSettings) -> CodexNativeRuntime:
    return CodexNativeRuntime(
        CodexNativeConfig(
            mode=settings.codex_runtime_mode,
            sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
            timeout_ms=settings.codex_runtime_timeout_ms,
            model=settings.llm_model,
            reasoning_effort=settings.llm_reasoning_effort,
            usage_enabled=settings.llm_usage_enabled,
            usage_db_path=settings.llm_usage_db_path,
            usage_component="jue_codex_lab",
            thread_mode=settings.codex_native_thread_mode,
            thread_db_path=settings.codex_native_thread_db_path,
            compact_after_turns=settings.codex_native_compact_after_turns,
            read_turns=settings.codex_native_read_turns,
            developer_instructions_enabled=(
                settings.codex_native_developer_instructions_enabled
            ),
        )
    )


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logger.info("jue codex lab runner is retired; exiting without work")


if __name__ == "__main__":
    run()
