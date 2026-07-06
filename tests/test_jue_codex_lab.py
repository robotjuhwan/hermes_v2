from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from tradecraft.services.codex_native import CodexNativeConfig, CodexNativeRuntime
from tradecraft.services.jue_codex_lab import JueCodexLabService
from tradecraft.services.jue_codex_lab_models import RepairTask
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore
from tradecraft.services.trading_validation import TradingValidationRepository


def _save_validation_run(
    db_path: Path,
    *,
    run_id: str,
    venue: str = "binance",
    work_queue: list[dict[str, Any]],
    computed_at: str = "2026-07-02T00:00:00+09:00",
) -> None:
    TradingValidationRepository(db_path).save_run(
        {
            "run_id": run_id,
            "venue": venue,
            "status": "ok",
            "computed_at": computed_at,
            "summary": {},
            "remediation_plan": {"work_queue": work_queue},
        }
    )


def _queue_repair_task(
    store: JueCodexLabStore,
    *,
    task_id: str = "binance:validation:cost_simulation",
    allowed_paths: list[str] | None = None,
    blocked_paths: list[str] | None = None,
) -> None:
    store.initialize()
    store.upsert_task(
        RepairTask(
            task_id=task_id,
            venue="binance",
            discipline_id="cost_simulation",
            source_validation_run_id="validation-binance-1",
            status="queued",
            priority=100,
            owner="cost_model",
            automation_hook="sync_live_performance_and_edges",
            failure_status="fail",
            failure_evidence="2x cost stress is net-negative",
            green_condition={"target_statuses": ["pass", "warn"]},
            allowed_paths=allowed_paths or [],
            blocked_paths=blocked_paths or [],
        ),
        now_iso="2026-07-02T00:00:00+09:00",
    )


class _FakeRuntime:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.prompts: list[dict[str, Any]] = []

    def complete_json(self, prompt: dict[str, Any]) -> Any:
        self.prompts.append(prompt)
        return self.response


class _AsyncRuntime:
    def __init__(self, response: Any) -> None:
        self.response = response

    async def complete_json(self, prompt: dict[str, Any]) -> Any:
        return self.response


class _ExplodingVerifier:
    def run_commands(self, commands: list[str]) -> dict[str, Any]:
        raise RuntimeError("verifier transport failed")


def _valid_patch_response(
    *,
    files: list[dict[str, str]],
    touched_paths: list[str] | None = None,
    verification_commands: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "root_cause": "generated repair",
        "patch_strategy": "rewrite files",
        "patch": {
            "touched_paths": touched_paths
            if touched_paths is not None
            else [file_item["path"] for file_item in files],
            "files": files,
            "diff_summary": "test repair",
        },
        "verification_commands": verification_commands
        or ["python3 -m py_compile src/tradecraft/services/live_performance.py"],
        "rollback_notes": "restore previous content",
        "wiki_memory": {"should_update": False, "summary": "", "tags": []},
    }


def test_status_on_missing_db_does_not_create_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "codex_lab.db"
    service = JueCodexLabService(
        store=JueCodexLabStore(db_path),
        validation_db_path=tmp_path / "trading_validation.db",
    )

    status = service.status()

    assert status == {
        "status": "ok",
        "db_path": str(db_path),
        "initialized": False,
        "queued_count": 0,
        "failed_count": 0,
    }
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_status_counts_tasks_without_loading_task_rows(tmp_path: Path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, task_id="queued-1")
    _queue_repair_task(store, task_id="queued-2")
    store.mark_task_status(
        "queued-2",
        "failed",
        now_iso="2026-07-02T00:05:00+09:00",
    )
    service = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "trading_validation.db",
    )

    status = service.status()

    assert status == {
        "status": "ok",
        "db_path": str(tmp_path / "codex_lab.db"),
        "initialized": True,
        "queued_count": 1,
        "failed_count": 1,
    }


def test_ingestion_creates_queued_repair_task_with_decoded_green_condition(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _save_validation_run(
        validation_db,
        run_id="validation-binance-1",
        work_queue=[
            {
                "task_id": "validation:cost_simulation:fail",
                "discipline_id": "cost_simulation",
                "status": "fail",
                "priority": "p0",
                "automation_hook": "sync_live_performance_and_edges",
                "evidence": "2x cost stress is net-negative",
            }
        ],
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=validation_db,
    ).ingest_validation_work_queue("binance")

    tasks = store.list_tasks(status="queued")
    assert result == {
        "status": "ok",
        "venue": "binance",
        "source_validation_run_id": "validation-binance-1",
        "created_count": 1,
        "queued_count": 1,
    }
    assert len(tasks) == 1
    task = tasks[0]
    assert task["task_id"] == "binance:validation:cost_simulation"
    assert task["discipline_id"] == "cost_simulation"
    assert task["owner"] == "cost_model"
    assert task["failure_status"] == "fail"
    assert task["failure_evidence"] == "2x cost stress is net-negative"
    assert task["green_condition"] == {
        "discipline_id": "cost_simulation",
        "target_statuses": ["pass", "warn"],
    }


def test_record_green_path_progress_when_status_improves(tmp_path: Path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    service = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "trading_validation.db",
    )

    progress = service.record_green_path_progress(
        venue="binance",
        discipline_id="cost_simulation",
        before={"status": "fail", "score": 0.24, "run_id": "validation-before"},
        after={"status": "warn", "score": 0.71, "run_id": "validation-after"},
        repair_task_id="binance:validation:cost_simulation",
    )

    rows = store.list_green_path_progress("binance")
    assert progress["venue"] == "binance"
    assert progress["discipline_id"] == "cost_simulation"
    assert progress["before_status"] == "fail"
    assert progress["after_status"] == "warn"
    assert progress["before_score"] == 0.24
    assert progress["after_score"] == 0.71
    assert progress["validation_run_before"] == "validation-before"
    assert progress["validation_run_after"] == "validation-after"
    assert progress["repair_task_id"] == "binance:validation:cost_simulation"
    assert rows == [progress]


def test_record_green_path_progress_migrates_legacy_table(tmp_path: Path) -> None:
    db_path = tmp_path / "codex_lab.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE green_path_progress (
                venue TEXT NOT NULL,
                discipline_id TEXT NOT NULL,
                before_status TEXT NOT NULL,
                after_status TEXT NOT NULL,
                before_score REAL,
                after_score REAL,
                validation_run_before TEXT NOT NULL,
                validation_run_after TEXT NOT NULL,
                repair_task_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO green_path_progress (
                venue, discipline_id, before_status, after_status,
                before_score, after_score, validation_run_before,
                validation_run_after, repair_task_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "binance",
                "cost_simulation",
                "fail",
                "warn",
                0.2,
                0.7,
                "legacy-before",
                "legacy-after",
                "binance:legacy:cost",
                "2026-07-02T00:00:00+00:00",
            ),
        )
    store = JueCodexLabStore(db_path)

    progress = store.record_green_path_progress(
        venue="binance",
        discipline_id="walk_forward_analysis",
        before_status="missing",
        after_status="warn",
        before_score=0.1,
        after_score=0.5,
        validation_run_before="new-before",
        validation_run_after="new-after",
        repair_task_id="binance:validation:walk_forward_analysis",
    )

    rows = store.list_green_path_progress("binance")
    assert progress["discipline_id"] == "walk_forward_analysis"
    assert {row["discipline_id"] for row in rows} == {
        "cost_simulation",
        "walk_forward_analysis",
    }
    assert any(row["repair_task_id"] == "binance:legacy:cost" for row in rows)


def test_ingestion_ignores_pass_and_unknown_items(tmp_path: Path) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _save_validation_run(
        validation_db,
        run_id="validation-binance-1",
        work_queue=[
            {
                "task_id": "validation:cost_simulation:pass",
                "discipline_id": "cost_simulation",
                "status": "pass",
            },
            {
                "task_id": "validation:cost_simulation:unknown",
                "discipline_id": "cost_simulation",
                "status": "unknown",
            },
        ],
    )

    result = JueCodexLabService(store, validation_db).ingest_validation_work_queue(
        "binance"
    )

    assert result["status"] == "ok"
    assert result["created_count"] == 0
    assert result["queued_count"] == 0
    assert store.list_tasks() == []


def test_duplicate_ingestion_updates_existing_task_without_incrementing_created_count(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    work_item = {
        "task_id": "validation:walk_forward_analysis:warn",
        "discipline_id": "walk_forward_analysis",
        "status": "warn",
        "priority": "p1",
        "automation_hook": "pattern_lab_rebuild_wfa_oos",
        "runner_hint": "first hint",
    }
    _save_validation_run(
        validation_db,
        run_id="validation-binance-1",
        work_queue=[work_item],
    )
    service = JueCodexLabService(store, validation_db)

    first = service.ingest_validation_work_queue("binance")
    work_item["runner_hint"] = "updated hint"
    _save_validation_run(
        validation_db,
        run_id="validation-binance-2",
        work_queue=[work_item],
        computed_at="2026-07-02T00:05:00+09:00",
    )
    second = service.ingest_validation_work_queue("binance")

    tasks = store.list_tasks(status="queued")
    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert second["queued_count"] == 1
    assert len(tasks) == 1
    assert tasks[0]["source_validation_run_id"] == "validation-binance-2"
    assert tasks[0]["owner"] == "pattern_lab"
    assert tasks[0]["failure_evidence"] == "updated hint"


def test_duplicate_ingestion_does_not_requeue_failed_same_validation_run(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    work_item = {
        "task_id": "validation:cost_simulation:fail",
        "repair_action_id": "validation_repair.cost_evidence_repair.cost_simulation",
        "discipline_id": "cost_simulation",
        "status": "fail",
        "priority": "p0",
        "automation_hook": "sync_live_performance_and_edges",
        "evidence": "same failing evidence",
    }
    _save_validation_run(
        validation_db,
        run_id="validation-binance-1",
        work_queue=[work_item],
    )
    service = JueCodexLabService(
        store,
        validation_db,
        codex_runtime=_FakeRuntime({"root_cause": "no patch"}),
        repo_root=tmp_path,
    )

    first_ingest = service.ingest_validation_work_queue("binance")
    first_repair = service.run_once()
    second_ingest = service.ingest_validation_work_queue("binance")

    assert first_ingest["created_count"] == 1
    assert first_repair["errors"][0]["reason"] == "patch_missing"
    assert second_ingest["created_count"] == 0
    assert store.list_tasks(status="queued") == []
    failed = store.list_tasks(status="failed")
    assert len(failed) == 1
    assert failed[0]["task_id"] == (
        "binance:validation_repair.cost_evidence_repair.cost_simulation"
    )


def test_repair_action_id_keeps_task_identity_stable_across_status_changes(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    work_item = {
        "task_id": "validation:cost_simulation:fail",
        "repair_action_id": "validation_repair.cost_evidence_repair.cost_simulation",
        "discipline_id": "cost_simulation",
        "status": "fail",
        "priority": "p0",
        "automation_hook": "sync_live_performance_and_edges",
        "evidence": "cost stress failed",
    }
    _save_validation_run(
        validation_db,
        run_id="validation-binance-1",
        work_queue=[work_item],
    )
    service = JueCodexLabService(store, validation_db)

    first = service.ingest_validation_work_queue("binance")
    work_item.update(
        {
            "task_id": "validation:cost_simulation:warn",
            "status": "warn",
            "evidence": "cost stress is thin but no longer failing",
        }
    )
    _save_validation_run(
        validation_db,
        run_id="validation-binance-2",
        work_queue=[work_item],
        computed_at="2026-07-02T00:05:00+09:00",
    )
    second = service.ingest_validation_work_queue("binance")

    tasks = store.list_tasks(status="queued")
    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == (
        "binance:validation_repair.cost_evidence_repair.cost_simulation"
    )
    assert tasks[0]["failure_status"] == "warn"
    assert tasks[0]["failure_evidence"] == "cost stress is thin but no longer failing"


def test_invalid_validation_payload_json_returns_error_status(tmp_path: Path) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _save_validation_run(
        validation_db,
        run_id="validation-binance-1",
        work_queue=[],
    )
    with sqlite3.connect(validation_db) as conn:
        conn.execute(
            "UPDATE validation_runs SET payload_json = ? WHERE run_id = ?",
            ("{", "validation-binance-1"),
        )

    result = JueCodexLabService(store, validation_db).ingest_validation_work_queue(
        "binance"
    )

    assert result["status"] == "error"
    assert result["venue"] == "binance"
    assert result["source_validation_run_id"] == "validation-binance-1"
    assert "invalid validation payload JSON" in result["message"]
    assert store.list_tasks() == []


def test_corrupt_validation_db_returns_error_status(tmp_path: Path) -> None:
    validation_db = tmp_path / "trading_validation.db"
    validation_db.write_text("not a sqlite database", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")

    result = JueCodexLabService(store, validation_db).ingest_validation_work_queue(
        "binance"
    )

    assert result["status"] == "error"
    assert result["venue"] == "binance"
    assert result["source_validation_run_id"] == ""
    assert result["created_count"] == 0
    assert result["queued_count"] == 0
    assert "could not read validation DB" in result["message"]
    assert store.list_tasks() == []


def test_latest_row_tie_uses_highest_run_id(tmp_path: Path) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    computed_at = "2026-07-02T00:00:00+09:00"
    _save_validation_run(
        validation_db,
        run_id="validation-binance-1",
        work_queue=[
            {
                "discipline_id": "cost_simulation",
                "status": "fail",
                "evidence": "old evidence",
            }
        ],
        computed_at=computed_at,
    )
    _save_validation_run(
        validation_db,
        run_id="validation-binance-2",
        work_queue=[
            {
                "discipline_id": "cost_simulation",
                "status": "fail",
                "evidence": "new evidence",
            }
        ],
        computed_at=computed_at,
    )

    result = JueCodexLabService(store, validation_db).ingest_validation_work_queue(
        "binance"
    )

    tasks = store.list_tasks(status="queued")
    assert result["source_validation_run_id"] == "validation-binance-2"
    assert tasks[0]["source_validation_run_id"] == "validation-binance-2"
    assert tasks[0]["failure_evidence"] == "new evidence"


def test_missing_task_id_uses_validation_discipline_fallback(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _save_validation_run(
        validation_db,
        run_id="validation-kis-1",
        venue="kis",
        work_queue=[
            {
                "discipline_id": "kelly_sizing",
                "failure_status": "missing",
                "priority": "p2",
                "exit_criteria": "kelly sizing evidence restored",
            }
        ],
    )

    result = JueCodexLabService(store, validation_db).ingest_validation_work_queue("kis")

    tasks = store.list_tasks(status="queued")
    assert result["created_count"] == 1
    assert tasks[0]["task_id"] == "kis:validation:kelly_sizing"
    assert tasks[0]["failure_evidence"] == "kelly sizing evidence restored"


def test_build_repair_prompt_requires_patch_and_verification_plan() -> None:
    task = {
        "task_id": "binance:validation:cost_simulation",
        "discipline_id": "cost_simulation",
        "failure_status": "fail",
        "failure_evidence": "2x cost stress is net-negative",
        "allowed_paths": [
            "src/tradecraft/services/live_performance.py",
            "tests/test_live_performance.py",
        ],
        "blocked_paths": [".env", ".runtime"],
    }

    prompt = JueCodexLabService.build_codex_repair_prompt(task)

    assert prompt["role"] == "jue_codex_autonomous_repair"
    assert prompt["approval_policy"] == "no_human_approval_required"
    assert prompt["failure_policy"] == "fail_loudly_no_fallback"
    assert "validation warning/failure" in prompt["objective"]
    assert "verified code/data/research/test improvement" in prompt["objective"]
    assert "Do not hide the metric" in prompt["objective"]
    assert prompt["task"] == task
    assert prompt["allowed_paths"] == task["allowed_paths"]
    assert prompt["blocked_paths"] == task["blocked_paths"]
    assert prompt["operational_constraints"] == {
        "do_not_edit_secrets": True,
        "do_not_disable_safety_gates": True,
        "do_not_convert_failures_to_fallbacks": True,
        "must_add_or_update_tests": True,
        "must_keep_runtime_restart_scope_minimal": True,
    }
    expected_output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "root_cause",
            "patch_strategy",
            "patch",
            "verification_commands",
            "rollback_notes",
            "wiki_memory",
        ],
        "properties": {
            "root_cause": {"type": "string"},
            "patch_strategy": {"type": "string"},
            "patch": {
                "type": "object",
                "additionalProperties": False,
                "required": ["touched_paths", "files"],
                "properties": {
                    "touched_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["path", "content"],
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                        },
                    },
                    "diff_summary": {"type": "string"},
                },
            },
            "verification_commands": {
                "type": "array",
                "items": {"type": "string"},
            },
            "rollback_notes": {"type": "string"},
            "wiki_memory": {
                "type": "object",
                "additionalProperties": False,
                "required": ["should_update", "summary", "tags"],
                "properties": {
                    "should_update": {"type": "boolean"},
                    "summary": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    }
    assert prompt["output_schema"] == expected_output_schema
    assert (
        CodexNativeRuntime(CodexNativeConfig())._native_output_schema(prompt)
        == expected_output_schema
    )


def test_build_repair_prompt_copies_allowed_and_blocked_paths() -> None:
    allowed_paths = ["src/tradecraft/services/live_performance.py"]
    blocked_paths = [".env"]
    task = {
        "task_id": "binance:validation:cost_simulation",
        "allowed_paths": allowed_paths,
        "blocked_paths": blocked_paths,
    }

    prompt = JueCodexLabService.build_codex_repair_prompt(task)
    allowed_paths.append("src/tradecraft/services/unexpected.py")
    blocked_paths.append("secrets")

    assert prompt["allowed_paths"] == ["src/tradecraft/services/live_performance.py"]
    assert prompt["blocked_paths"] == [".env"]
    assert prompt["allowed_paths"] is not allowed_paths
    assert prompt["blocked_paths"] is not blocked_paths


def test_run_once_records_failed_codex_response_without_fallback(
    tmp_path: Path,
) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store)
    runtime = _FakeRuntime({"root_cause": "known issue"})

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=runtime,
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["processed_count"] == 1
    assert result["failed_count"] == 1
    assert result["errors"][0]["reason"] == "patch_missing"
    assert runtime.prompts[0]["task"]["task_id"] == "binance:validation:cost_simulation"
    failed_tasks = store.list_tasks(status="failed")
    assert len(failed_tasks) == 1
    assert failed_tasks[0]["task_id"] == "binance:validation:cost_simulation"
    deployment_events = store.list_deployment_events()
    assert len(deployment_events) == 1
    assert deployment_events[0]["status"] == "patch_missing"
    assert deployment_events[0]["detail"]["reason"] == "patch_missing"
    assert deployment_events[0]["detail"]["response_summary"]["keys"] == [
        "root_cause"
    ]
    repair_runs = store.list_repair_runs()
    assert len(repair_runs) == 1
    assert repair_runs[0]["status"] == "patch_missing"
    assert repair_runs[0]["summary"]["response_summary"]["keys"] == ["root_cause"]


def test_run_once_records_codex_json_error_separately_from_patch_missing(
    tmp_path: Path,
) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store)
    runtime = _FakeRuntime(
        {
            "ok": False,
            "mode": "sdk",
            "error": "llm_json_error: expected object",
            "content": "I cannot safely produce a patch yet.",
        }
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=runtime,
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "codex_response_error"
    assert "llm_json_error" in result["errors"][0]["message"]
    deployment_event = store.list_deployment_events()[0]
    assert deployment_event["status"] == "codex_response_error"
    assert deployment_event["detail"]["response_summary"]["ok"] is False
    assert deployment_event["detail"]["response_summary"]["content_preview"] == (
        "I cannot safely produce a patch yet."
    )
    repair_run = store.list_repair_runs()[0]
    assert repair_run["status"] == "codex_response_error"


def test_run_once_records_missing_runtime_without_fallback(tmp_path: Path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store)

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["processed_count"] == 1
    assert result["failed_count"] == 1
    assert result["errors"][0]["reason"] == "no_codex_runtime"
    failed_tasks = store.list_tasks(status="failed")
    assert len(failed_tasks) == 1
    assert failed_tasks[0]["task_id"] == "binance:validation:cost_simulation"
    deployment_events = store.list_deployment_events()
    assert len(deployment_events) == 1
    assert deployment_events[0]["status"] == "no_codex_runtime"
    assert deployment_events[0]["detail"]["reason"] == "no_codex_runtime"


def test_run_once_records_codex_runtime_error_event(tmp_path: Path) -> None:
    class BrokenRuntime:
        def complete_json(self, prompt: dict[str, Any]) -> Any:
            raise RuntimeError("codex unavailable")

    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store)

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=BrokenRuntime(),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "codex_runtime_error"
    deployment_events = store.list_deployment_events()
    assert len(deployment_events) == 1
    assert deployment_events[0]["status"] == "codex_runtime_error"
    assert deployment_events[0]["detail"]["reason"] == "codex_runtime_error"
    assert "codex unavailable" in deployment_events[0]["detail"]["message"]


def test_run_once_accepts_async_complete_json_coroutine(tmp_path: Path) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_AsyncRuntime(
            _valid_patch_response(
                files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "ok"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'new'\n"


def test_run_once_rejects_future_from_complete_json(tmp_path: Path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store)
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    future.set_result({"patch": {}})
    try:
        result = JueCodexLabService(
            store=store,
            validation_db_path=tmp_path / "validation.db",
            codex_runtime=_FakeRuntime(future),
            repo_root=tmp_path,
        ).run_once()
    finally:
        loop.close()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "codex_runtime_error"
    assert "coroutine object" in result["errors"][0]["message"]
    deployment_events = store.list_deployment_events()
    assert deployment_events[0]["status"] == "codex_runtime_error"


def test_run_once_auto_applies_verified_patch_and_records_deployment(
    tmp_path: Path,
) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path], blocked_paths=[".env"])
    runtime = _FakeRuntime(
        {
            "root_cause": "stale live performance constant",
            "patch_strategy": "replace the stale constant",
            "patch": {
                "touched_paths": [target_path],
                "files": [{"path": target_path, "content": "VALUE = 'new'\n"}],
                "diff_summary": "updated live performance constant",
            },
            "verification_commands": [
                "python3 -m py_compile src/tradecraft/services/live_performance.py"
            ],
            "rollback_notes": "restore previous file content",
            "wiki_memory": {"should_update": False, "summary": "", "tags": []},
        }
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=runtime,
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "ok"
    assert result["processed_count"] == 1
    assert result["deployed_count"] == 1
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'new'\n"
    assert store.list_tasks(status="deployed")[0]["task_id"] == (
        "binance:validation:cost_simulation"
    )
    deployment_events = store.list_deployment_events()
    assert deployment_events[-1]["task_id"] == "binance:validation:cost_simulation"
    assert deployment_events[-1]["status"] == "deployed"
    assert deployment_events[-1]["detail"]["files"] == [target_path]
    patch_attempts = store.list_patch_attempts()
    assert len(patch_attempts) == 1
    assert patch_attempts[0]["status"] == "applied"


def test_run_once_respects_proposal_only_autonomy_before_write(
    tmp_path: Path,
) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
        autonomy_mode="proposal_only",
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "autonomy_mode_not_auto_apply"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"


def test_run_once_rejects_patch_larger_than_configured_limit(
    tmp_path: Path,
) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
        max_patch_bytes=8,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "patch_too_large"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"


def test_run_once_enforces_global_allowed_and_blocked_paths(
    tmp_path: Path,
) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
        allowed_paths=["tests"],
        blocked_paths=["src/tradecraft/services/live_performance.py"],
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] in {
        "outside_allowed_paths",
        "blocked_path_touched",
    }
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"


def test_run_once_blocks_kis_sensitive_hot_deploy_during_market_hours(
    tmp_path: Path,
) -> None:
    target_path = "src/tradecraft/services/kis_executor.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(
        store,
        task_id="kis:validation:cost_simulation",
        allowed_paths=[target_path],
    )
    with sqlite3.connect(tmp_path / "codex_lab.db") as conn:
        conn.execute("UPDATE repair_tasks SET venue = 'kis'")

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
        market_hours_hot_deploy=False,
        now=lambda: datetime.fromisoformat("2026-07-02T10:00:00+09:00"),
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "market_hours_hot_deploy_blocked"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"


def test_run_once_rolls_back_patch_when_verification_fails(tmp_path: Path) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])
    runtime = _FakeRuntime(
        {
            "root_cause": "bad generated syntax",
            "patch_strategy": "replace file content",
            "patch": {
                "touched_paths": [target_path],
                "files": [{"path": target_path, "content": "VALUE =\n"}],
            },
            "verification_commands": [
                "python3 -m py_compile src/tradecraft/services/live_performance.py"
            ],
            "rollback_notes": "restore previous file content",
            "wiki_memory": {"should_update": False, "summary": "", "tags": []},
        }
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=runtime,
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["processed_count"] == 1
    assert result["failed_count"] == 1
    assert result["errors"][0]["reason"] == "verification_failed"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    assert store.list_tasks(status="failed")[0]["task_id"] == (
        "binance:validation:cost_simulation"
    )
    deployment_events = store.list_deployment_events()
    assert deployment_events[-1]["status"] == "verification_failed"


def test_run_once_rolls_back_all_files_when_multi_file_verification_fails(
    tmp_path: Path,
) -> None:
    first_path = "src/tradecraft/services/live_performance.py"
    second_path = "tests/test_live_performance.py"
    first_file = tmp_path / first_path
    second_file = tmp_path / second_path
    first_file.parent.mkdir(parents=True)
    second_file.parent.mkdir(parents=True)
    first_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    second_file.write_text("def test_old():\n    assert True\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[first_path, second_path])
    runtime = _FakeRuntime(
        _valid_patch_response(
            files=[
                {"path": first_path, "content": "VALUE = 'new'\n"},
                {"path": second_path, "content": "def test_new():\n    assert True\n"},
            ],
            verification_commands=[
                "python3 -m py_compile src/tradecraft/services/live_performance.py",
                "python3 -m py_compile tests/test_live_performance.py",
                "python3 -m py_compile tests/missing_file.py",
            ],
        )
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=runtime,
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "verification_failed"
    assert first_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    assert second_file.read_text(encoding="utf-8") == "def test_old():\n    assert True\n"


def test_run_once_removes_new_file_on_rollback(tmp_path: Path) -> None:
    existing_path = "src/tradecraft/services/live_performance.py"
    new_path = "tests/test_live_performance.py"
    existing_file = tmp_path / existing_path
    new_file = tmp_path / new_path
    existing_file.parent.mkdir(parents=True)
    existing_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[existing_path, new_path])
    runtime = _FakeRuntime(
        _valid_patch_response(
            files=[
                {"path": existing_path, "content": "VALUE = 'new'\n"},
                {"path": new_path, "content": "def test_new():\n    assert True\n"},
            ],
            verification_commands=[
                "python3 -m py_compile src/tradecraft/services/live_performance.py",
                "python3 -m py_compile tests/missing_file.py",
            ],
        )
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=runtime,
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert existing_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    assert not new_file.exists()


def test_run_once_verifier_exception_is_caught_and_rolls_back(tmp_path: Path) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
        verifier=_ExplodingVerifier(),
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "patch_apply_exception"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    assert store.list_tasks(status="failed")[0]["task_id"] == (
        "binance:validation:cost_simulation"
    )
    event = store.list_deployment_events()[-1]
    assert event["status"] == "patch_apply_exception"
    assert event["detail"]["rollback_status"] == "restored"
    assert "verifier transport failed" in event["detail"]["message"]


def test_run_once_write_exception_restores_partial_file_change(
    tmp_path: Path,
) -> None:
    class PartialWriteService(JueCodexLabService):
        @staticmethod
        def _write_patch_files(files: list[dict[str, Any]]) -> None:
            first = files[0]
            first["target"].write_text(first["content"], encoding="utf-8")
            raise OSError("disk full after first write")

    first_path = "src/tradecraft/services/live_performance.py"
    second_path = "tests/test_live_performance.py"
    first_file = tmp_path / first_path
    second_file = tmp_path / second_path
    first_file.parent.mkdir(parents=True)
    second_file.parent.mkdir(parents=True)
    first_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    second_file.write_text("def test_old():\n    assert True\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[first_path, second_path])

    result = PartialWriteService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[
                    {"path": first_path, "content": "VALUE = 'new'\n"},
                    {"path": second_path, "content": "def test_new():\n    assert True\n"},
                ]
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "patch_apply_exception"
    assert first_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    assert second_file.read_text(encoding="utf-8") == "def test_old():\n    assert True\n"
    patch_attempts = store.list_patch_attempts()
    assert [attempt["status"] for attempt in patch_attempts] == ["failed"]


def test_run_once_continues_rollback_when_first_restore_fails(
    tmp_path: Path,
) -> None:
    class RestoreFailureService(JueCodexLabService):
        @staticmethod
        def _write_patch_files(files: list[dict[str, Any]]) -> None:
            for file_item in files:
                file_item["target"].write_text(file_item["content"], encoding="utf-8")
            first_target = files[0]["target"]
            first_target.unlink()
            first_target.mkdir()

    first_path = "src/tradecraft/services/live_performance.py"
    second_path = "tests/test_live_performance.py"
    first_file = tmp_path / first_path
    second_file = tmp_path / second_path
    first_file.parent.mkdir(parents=True)
    second_file.parent.mkdir(parents=True)
    first_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    second_file.write_text("def test_old():\n    assert True\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[first_path, second_path])

    result = RestoreFailureService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[
                    {"path": first_path, "content": "VALUE = 'new'\n"},
                    {"path": second_path, "content": "def test_new():\n    assert True\n"},
                ],
                verification_commands=["python3 -m py_compile tests/missing_file.py"],
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "verification_failed"
    assert first_file.is_dir()
    assert second_file.read_text(encoding="utf-8") == "def test_old():\n    assert True\n"
    event = store.list_deployment_events()[-1]
    assert event["detail"]["rollback_status"] == "partial_restore_failed"
    assert event["detail"]["rollback_errors"][0]["path"] == first_path


def test_run_once_validates_verification_commands_before_writing(tmp_path: Path) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])
    response = _valid_patch_response(
        files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
    )
    response["verification_commands"] = "python3 -m py_compile bad"

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(response),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "verification_commands_invalid"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"


def test_run_once_rejects_patch_outside_allowed_paths(tmp_path: Path) -> None:
    allowed_path = "src/tradecraft/services/live_performance.py"
    outside_path = "src/tradecraft/services/unowned.py"
    allowed_file = tmp_path / allowed_path
    allowed_file.parent.mkdir(parents=True)
    allowed_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    outside_file = tmp_path / outside_path
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[allowed_path])
    runtime = _FakeRuntime(
        {
            "root_cause": "wrong target",
            "patch_strategy": "write unowned file",
            "patch": {
                "touched_paths": [outside_path],
                "files": [{"path": outside_path, "content": "VALUE = 'new'\n"}],
            },
            "verification_commands": [
                "python3 -m py_compile src/tradecraft/services/unowned.py"
            ],
            "rollback_notes": "no write expected",
            "wiki_memory": {"should_update": False, "summary": "", "tags": []},
        }
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=runtime,
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["processed_count"] == 1
    assert result["failed_count"] == 1
    assert result["errors"][0]["reason"] == "outside_allowed_paths"
    assert allowed_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    assert not outside_file.exists()
    assert store.list_tasks(status="failed")[0]["task_id"] == (
        "binance:validation:cost_simulation"
    )


def test_run_once_fails_closed_when_policy_json_is_malformed(tmp_path: Path) -> None:
    target_path = "src/tradecraft/services/live_performance.py"
    target_file = tmp_path / target_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[target_path])
    with sqlite3.connect(tmp_path / "codex_lab.db") as conn:
        conn.execute(
            "UPDATE repair_tasks SET allowed_paths_json = ?",
            ("{",),
        )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": target_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "policy_decode_error"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    event = store.list_deployment_events()[0]
    assert event["status"] == "policy_decode_error"
    assert event["detail"]["policy_decode_errors"][0]["field"] == "allowed_paths"


def test_run_once_rejects_traversal_path_before_write(tmp_path: Path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=["src/tradecraft/services"])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": "../escape.py", "content": "VALUE = 'new'\n"}],
                touched_paths=["../escape.py"],
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "invalid_path"
    assert not (tmp_path.parent / "escape.py").exists()


def test_run_once_rejects_absolute_path_before_write(tmp_path: Path) -> None:
    absolute_path = str(tmp_path / "escape.py")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=["src/tradecraft/services"])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": absolute_path, "content": "VALUE = 'new'\n"}],
                touched_paths=[absolute_path],
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "invalid_path"
    assert not Path(absolute_path).exists()


def test_run_once_rejects_symlink_to_outside_repo(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}_outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "target.py"
    outside_file.write_text("VALUE = 'outside'\n", encoding="utf-8")
    link_path = "src/tradecraft/services/live_performance.py"
    link_file = tmp_path / link_path
    link_file.parent.mkdir(parents=True)
    link_file.symlink_to(outside_file)
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[link_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": link_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "path_outside_repo"
    assert outside_file.read_text(encoding="utf-8") == "VALUE = 'outside'\n"


def test_run_once_rejects_allowed_file_symlink_to_blocked_in_repo_path(
    tmp_path: Path,
) -> None:
    allowed_path = "src/tradecraft/services/live_performance.py"
    blocked_path = "src/tradecraft/services/blocked_target.py"
    blocked_file = tmp_path / blocked_path
    link_file = tmp_path / allowed_path
    blocked_file.parent.mkdir(parents=True)
    blocked_file.write_text("VALUE = 'blocked'\n", encoding="utf-8")
    link_file.symlink_to(blocked_file.name)
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(
        store,
        allowed_paths=[allowed_path],
        blocked_paths=[blocked_path],
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": allowed_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "symlink_indirected_path"
    assert blocked_file.read_text(encoding="utf-8") == "VALUE = 'blocked'\n"
    event = store.list_deployment_events()[-1]
    assert event["status"] == "symlink_indirected_path"
    assert event["detail"]["resolved_paths"] == [
        {"path": allowed_path, "resolved_path": blocked_path}
    ]


def test_run_once_rejects_allowed_path_under_symlinked_parent_to_blocked_dir(
    tmp_path: Path,
) -> None:
    allowed_path = "allowed_dir/link_parent/file.py"
    blocked_path = "blocked/file.py"
    blocked_file = tmp_path / blocked_path
    link_parent = tmp_path / "allowed_dir" / "link_parent"
    blocked_file.parent.mkdir(parents=True)
    link_parent.parent.mkdir(parents=True)
    blocked_file.write_text("VALUE = 'blocked'\n", encoding="utf-8")
    link_parent.symlink_to(tmp_path / "blocked")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(
        store,
        allowed_paths=[allowed_path],
        blocked_paths=["blocked"],
    )

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": allowed_path, "content": "VALUE = 'new'\n"}]
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "symlink_indirected_path"
    assert blocked_file.read_text(encoding="utf-8") == "VALUE = 'blocked'\n"
    event = store.list_deployment_events()[-1]
    assert event["detail"]["reason"] == "symlink_indirected_path"
    assert event["detail"]["resolved_paths"] == [
        {"path": allowed_path, "resolved_path": blocked_path}
    ]


def test_run_once_rejects_backslash_patch_file_path_before_write(
    tmp_path: Path,
) -> None:
    touched_path = "src/tradecraft/services/live_performance.py"
    raw_file_path = "src\\tradecraft\\services\\live_performance.py"
    target_file = tmp_path / touched_path
    backslash_file = tmp_path / raw_file_path
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[touched_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": raw_file_path, "content": "VALUE = 'new'\n"}],
                touched_paths=[touched_path],
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "invalid_patch_file_path"
    assert target_file.read_text(encoding="utf-8") == "VALUE = 'old'\n"
    assert not backslash_file.exists()
    event = store.list_deployment_events()[-1]
    assert event["status"] == "invalid_patch_file_path"
    assert event["detail"]["invalid_paths"] == [
        {
            "path": raw_file_path,
            "reason": "backslash_separator",
            "normalized_path": touched_path,
        }
    ]


def test_run_once_rejects_file_missing_from_touched_paths(tmp_path: Path) -> None:
    touched_path = "src/tradecraft/services/live_performance.py"
    file_path = "tests/test_live_performance.py"
    touched_file = tmp_path / touched_path
    file_target = tmp_path / file_path
    touched_file.parent.mkdir(parents=True)
    file_target.parent.mkdir(parents=True)
    touched_file.write_text("VALUE = 'old'\n", encoding="utf-8")
    file_target.write_text("def test_old():\n    assert True\n", encoding="utf-8")
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    _queue_repair_task(store, allowed_paths=[touched_path, file_path])

    result = JueCodexLabService(
        store=store,
        validation_db_path=tmp_path / "validation.db",
        codex_runtime=_FakeRuntime(
            _valid_patch_response(
                files=[{"path": file_path, "content": "def test_new():\n    assert True\n"}],
                touched_paths=[touched_path],
            )
        ),
        repo_root=tmp_path,
    ).run_once()

    assert result["status"] == "error"
    assert result["errors"][0]["reason"] == "file_not_declared_touched"
    assert file_target.read_text(encoding="utf-8") == "def test_old():\n    assert True\n"
