from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from tradecraft.services.intelligence import run_report_collection_cycle_with_timeout
from tradecraft.runtime.naver_reports_runner import (
    _is_symbol_directory_stale,
    run_naver_reports_loop,
)


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
