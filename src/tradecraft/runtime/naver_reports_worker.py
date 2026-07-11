from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tradecraft.config import AppSettings
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.intelligence import (
    build_report_intelligence_stack,
    run_report_collection_cycle,
)


logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_worker(
    *,
    result_path: str | Path,
    progress_path: str | Path,
    settings: Any | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or AppSettings()
    result_store = RuntimeStateStore(result_path)
    progress_store = RuntimeStateStore(progress_path)
    started_at = _utc_now_iso()
    progress_store.write_snapshot(
        {
            "status": "running",
            "stage": "starting",
            "stage_started_at": started_at,
        }
    )

    def publish_progress(stage: str, detail: dict[str, Any]) -> None:
        progress_store.write_snapshot(
            {
                "status": "running" if stage != "cycle_completed" else "ok",
                "stage": stage,
                "stage_started_at": _utc_now_iso(),
                "detail": detail,
            }
        )

    try:
        stack = build_report_intelligence_stack(resolved_settings)
        payload = asyncio.run(
            run_report_collection_cycle(
                crawler=stack.crawler,
                repository=stack.repository,
                rag_store=stack.rag_store,
                rag_enabled=bool(resolved_settings.rag_enabled),
                rag_sync_chunk_limit=int(resolved_settings.rag_sync_chunk_limit),
                progress=publish_progress,
            )
        )
        result = {
            "status": "ok",
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
            **payload,
        }
    except Exception as exc:
        logger.exception("naver reports worker cycle failed: %s", exc)
        result = {
            "status": "error",
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }
        progress_store.write_snapshot(
            {
                "status": "error",
                "stage": "cycle_error",
                "stage_started_at": _utc_now_iso(),
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
        )
    result_store.write_snapshot(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Naver report cycle")
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--progress-path", required=True)
    args = parser.parse_args(argv)
    result = run_worker(
        result_path=args.result_path,
        progress_path=args.progress_path,
    )
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
