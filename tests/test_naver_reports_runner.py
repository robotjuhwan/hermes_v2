from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from tradecraft.services.intelligence import (
    run_report_collection_cycle,
    run_report_collection_cycle_with_timeout,
)
from tradecraft.runtime.naver_reports_runner import (
    _worker_state_paths,
    _is_symbol_directory_stale,
    run_naver_reports_loop,
    supervise_report_worker,
)
from tradecraft.runtime.runner_manifest import RUNNER_SPECS
from tradecraft.runtime.state_store import RuntimeStateStore


def test_report_worker_state_paths_are_isolated_by_parent_process(tmp_path) -> None:
    state_path = tmp_path / "naver_reports_runner.json"

    first = _worker_state_paths(state_path, parent_pid=101)
    second = _worker_state_paths(state_path, parent_pid=202)

    assert first != second
    assert first[0].name == "naver_reports_runner.worker-101-result.json"
    assert first[1].name == "naver_reports_runner.worker-101-progress.json"


def test_naver_reports_restart_pattern_includes_supervised_worker() -> None:
    pattern = RUNNER_SPECS["naver_reports"].pattern

    assert re.search(
        pattern,
        ".venv/bin/python -m tradecraft.runtime.naver_reports_worker",
    )


def test_report_supervisor_kills_worker_after_deadline(tmp_path) -> None:
    class FakeProcess:
        pid = 321

        def __init__(self) -> None:
            self.terminate_calls = 0
            self.kill_calls = 0

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1

    ticks = iter([0.0, 1.0, 3.0])
    process = FakeProcess()
    parent_state = RuntimeStateStore(tmp_path / "parent.json")
    parent_state.write_snapshot(
        {
            "service": "tradecraft-naver-reports",
            "cycle": 7,
            "last_collection_started_at": "2026-07-10T00:00:00+00:00",
            "next_run_at": "2026-07-10T03:00:00+00:00",
        }
    )
    result = supervise_report_worker(
        process=process,
        result_store=RuntimeStateStore(tmp_path / "result.json"),
        progress_store=RuntimeStateStore(tmp_path / "progress.json"),
        parent_state=parent_state,
        timeout_sec=2,
        heartbeat_interval_sec=1,
        monotonic=lambda: next(ticks),
        sleep=lambda _: None,
    )

    assert result["status"] == "timeout"
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    parent = json.loads((tmp_path / "parent.json").read_text(encoding="utf-8"))
    assert parent["worker_pid"] == 321
    assert parent["heartbeat_at"]
    assert parent["cycle"] == 7
    assert parent["last_collection_started_at"] == "2026-07-10T00:00:00+00:00"
    assert parent["next_run_at"] == "2026-07-10T03:00:00+00:00"


def test_is_symbol_directory_stale_when_missing_timestamp() -> None:
    assert _is_symbol_directory_stale("", min_age_sec=3600)


def test_is_symbol_directory_stale_when_recent_timestamp() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    assert not _is_symbol_directory_stale(recent, min_age_sec=3600)


def test_is_symbol_directory_stale_when_old_timestamp() -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    assert _is_symbol_directory_stale(old, min_age_sec=12 * 3600)


def test_report_collection_cycle_timeout_returns_operational_status() -> None:
    async def slow_cycle() -> dict:
        await asyncio.sleep(0.05)
        return {"status": "ok"}

    result = asyncio.run(
        run_report_collection_cycle_with_timeout(
            slow_cycle(),
            timeout_sec=0.001,
        )
    )

    assert result["status"] == "timeout"
    assert result["timeout_sec"] == 0.001


def test_report_collection_cycle_emits_stage_progress() -> None:
    class Crawler:
        async def crawl_once(self) -> dict[str, object]:
            return {"inserted": 2}

    class Repository:
        def repair_metadata_quality(self) -> dict[str, object]:
            return {"updated_reports": 0}

    stages: list[str] = []

    result = asyncio.run(
        run_report_collection_cycle(
            crawler=Crawler(),  # type: ignore[arg-type]
            repository=Repository(),  # type: ignore[arg-type]
            rag_store=None,
            rag_enabled=False,
            rag_sync_chunk_limit=100,
            refresh_symbol_directory=False,
            sync_rag=False,
            progress=lambda stage, _detail: stages.append(stage),
        )
    )

    assert result["snapshot"] == {"inserted": 2}
    assert stages == [
        "crawl_started",
        "crawl_completed",
        "metadata_repair_started",
        "metadata_repair_completed",
        "cycle_completed",
    ]


def test_naver_reports_runner_preserves_collection_cadence_after_restart(
    tmp_path,
) -> None:
    state_path = tmp_path / "naver_reports_runner.json"
    started_at = datetime.now(timezone.utc).isoformat()
    state_path.write_text(
        (
            '{"service":"tradecraft-naver-reports",'
            f'"status":"ok","last_collection_started_at":"{started_at}"'
            "}"
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    class Settings:
        naver_reports_enabled = True
        naver_reports_state_path = str(state_path)
        naver_reports_interval_sec = 10_800
        naver_reports_cycle_timeout_sec = 3600
        naver_reports_db_path = str(tmp_path / "naver_reports.db")
        rag_enabled = False
        rag_sync_chunk_limit = 100

    def collect_once() -> dict:
        calls.append("collect")
        return {"status": "ok", "snapshot": {"inserted": 1}}

    run_naver_reports_loop(
        settings=Settings(),
        collect_once=collect_once,
        sleep=lambda _: None,
        once=True,
    )

    payload = state_path.read_text(encoding="utf-8")

    assert calls == []
    assert '"status": "skipped"' in payload
    assert '"reason": "cadence"' in payload
    assert f'"last_collection_started_at": "{started_at}"' in payload


def test_naver_reports_runner_recovers_cadence_from_latest_successful_worker(
    tmp_path,
) -> None:
    state_path = tmp_path / "naver_reports_runner.json"
    state_path.write_text(
        json.dumps(
            {
                "service": "tradecraft-naver-reports",
                "status": "collecting",
                "worker_pid": 999,
            }
        ),
        encoding="utf-8",
    )
    started_at = datetime.now(timezone.utc).isoformat()
    state_path.with_name("naver_reports_runner.worker-result.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    class Settings:
        naver_reports_enabled = True
        naver_reports_state_path = str(state_path)
        naver_reports_interval_sec = 10_800
        naver_reports_cycle_timeout_sec = 3600
        naver_reports_db_path = str(tmp_path / "naver_reports.db")
        rag_enabled = False
        rag_sync_chunk_limit = 100

    def collect_once() -> dict:
        calls.append("collect")
        return {"status": "ok", "snapshot": {"inserted": 1}}

    run_naver_reports_loop(
        settings=Settings(),
        collect_once=collect_once,
        sleep=lambda _: None,
        once=True,
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert calls == []
    assert payload["status"] == "skipped"
    assert payload["reason"] == "cadence"
    assert payload["last_collection_started_at"] == started_at


def test_naver_reports_runner_does_not_build_collection_stack_when_cadence_skips(
    tmp_path,
    monkeypatch,
) -> None:
    state_path = tmp_path / "naver_reports_runner.json"
    started_at = datetime.now(timezone.utc).isoformat()
    state_path.write_text(
        json.dumps(
            {
                "service": "tradecraft-naver-reports",
                "status": "ok",
                "last_collection_started_at": started_at,
            }
        ),
        encoding="utf-8",
    )

    class Settings:
        naver_reports_enabled = True
        naver_reports_state_path = str(state_path)
        naver_reports_interval_sec = 10_800
        naver_reports_cycle_timeout_sec = 3600
        naver_reports_db_path = str(tmp_path / "naver_reports.db")
        rag_enabled = False
        rag_sync_chunk_limit = 100

    def fail_build_stack(_settings) -> None:
        raise AssertionError("collection stack should not be built before cadence check")

    monkeypatch.setattr(
        "tradecraft.runtime.naver_reports_runner._build_default_collect_once",
        fail_build_stack,
    )

    run_naver_reports_loop(
        settings=Settings(),
        sleep=lambda _: None,
        once=True,
    )

    payload = state_path.read_text(encoding="utf-8")
    assert '"status": "skipped"' in payload
    assert '"reason": "cadence"' in payload


def test_naver_reports_runner_logs_rag_sync_error_detail(
    tmp_path,
    caplog,
) -> None:
    state_path = tmp_path / "naver_reports_runner.json"

    class Settings:
        naver_reports_enabled = True
        naver_reports_state_path = str(state_path)
        naver_reports_interval_sec = 10_800
        naver_reports_cycle_timeout_sec = 3600
        naver_reports_db_path = str(tmp_path / "naver_reports.db")
        rag_enabled = True
        rag_sync_chunk_limit = 100

    def collect_once() -> dict:
        return {
            "status": "ok",
            "snapshot": {"inserted": 0},
            "rag_sync": {
                "status": "error",
                "synced": 0,
                "error_message": "chroma fts corruption",
            },
        }

    caplog.set_level(logging.INFO, logger="tradecraft.runtime.naver_reports_runner")

    run_naver_reports_loop(
        settings=Settings(),
        collect_once=collect_once,
        sleep=lambda _: None,
        once=True,
    )

    assert "rag sync status=error synced=0" in caplog.text
    assert "error=chroma fts corruption" in caplog.text


def test_naver_reports_runner_logs_cycle_failure_traceback(
    tmp_path,
    caplog,
) -> None:
    state_path = tmp_path / "naver_reports_runner.json"

    class Settings:
        naver_reports_enabled = True
        naver_reports_state_path = str(state_path)
        naver_reports_interval_sec = 10_800
        naver_reports_cycle_timeout_sec = 3600
        naver_reports_db_path = str(tmp_path / "naver_reports.db")
        rag_enabled = True
        rag_sync_chunk_limit = 100

    def collect_once() -> dict:
        raise RuntimeError("crawler db write failed")

    caplog.set_level(logging.WARNING, logger="tradecraft.runtime.naver_reports_runner")

    run_naver_reports_loop(
        settings=Settings(),
        collect_once=collect_once,
        sleep=lambda _: None,
        once=True,
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    matching_records = [
        record
        for record in caplog.records
        if "naver reports cycle failed" in record.getMessage()
    ]

    assert payload["status"] == "error"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_message"] == "crawler db write failed"
    assert matching_records
    assert matching_records[0].exc_info is not None
