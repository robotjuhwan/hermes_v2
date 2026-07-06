from __future__ import annotations

import sqlite3

from tradecraft.services.jue_codex_lab_models import RepairTask
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore


def test_store_initializes_repair_loop_tables(tmp_path) -> None:
    db_path = tmp_path / "nested" / "codex_lab.db"
    store = JueCodexLabStore(db_path)

    assert not db_path.parent.exists()

    store.initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }

    assert {
        "repair_tasks",
        "repair_runs",
        "patch_attempts",
        "verification_results",
        "deployment_events",
        "green_path_progress",
    } <= tables


def test_store_initializes_indexes_and_wal_settings(tmp_path) -> None:
    db_path = tmp_path / "codex_lab.db"
    store = JueCodexLabStore(db_path)

    store.initialize()

    with store._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        synchronous = int(conn.execute("PRAGMA synchronous").fetchone()[0])
        indexes = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                """
            )
        }

    assert journal_mode == "wal"
    assert synchronous == 1
    assert {
        "idx_repair_tasks_status_priority_updated_task",
        "idx_repair_tasks_priority_updated_task",
    } <= indexes


def test_upsert_task_roundtrips_decoded_json_fields(tmp_path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    store.initialize()
    task = RepairTask(
        task_id="task_1",
        venue="binance",
        discipline_id="validation",
        source_validation_run_id="validation_run_1",
        status="queued",
        priority=7,
        owner="worker-2",
        automation_hook="pytest",
        failure_status="red",
        failure_evidence="assertion mismatch",
        green_condition={"command": "pytest tests/test_target.py", "expected": "pass"},
        allowed_paths=["src/tradecraft/services/foo.py"],
        blocked_paths=["src/tradecraft/services/bar.py"],
    )

    inserted = store.upsert_task(task, "2026-07-02T00:00:00+09:00")

    rows = store.list_tasks(status="queued")
    assert inserted is True
    assert len(rows) == 1
    assert rows[0]["task_id"] == "task_1"
    assert rows[0]["green_condition"] == {
        "command": "pytest tests/test_target.py",
        "expected": "pass",
    }
    assert rows[0]["allowed_paths"] == ["src/tradecraft/services/foo.py"]
    assert rows[0]["blocked_paths"] == ["src/tradecraft/services/bar.py"]


def test_list_tasks_falls_back_only_green_condition_and_flags_policy_errors(
    tmp_path,
) -> None:
    db_path = tmp_path / "codex_lab.db"
    store = JueCodexLabStore(db_path)
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repair_tasks (
                task_id, venue, discipline_id, source_validation_run_id,
                status, priority, owner, automation_hook, failure_status,
                green_condition_json, allowed_paths_json, blocked_paths_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "malformed",
                "binance",
                "validation",
                "run_1",
                "queued",
                2,
                "worker-2",
                "pytest",
                "red",
                "{",
                "[",
                "{",
                "2026-07-02T00:00:00+09:00",
                "2026-07-02T00:00:00+09:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO repair_tasks (
                task_id, venue, discipline_id, source_validation_run_id,
                status, priority, owner, automation_hook, failure_status,
                green_condition_json, allowed_paths_json, blocked_paths_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wrong_type",
                "binance",
                "validation",
                "run_2",
                "queued",
                1,
                "worker-2",
                "pytest",
                "red",
                '["not", "a", "dict"]',
                '{"not": "a list"}',
                '"not a list"',
                "2026-07-02T00:00:00+09:00",
                "2026-07-02T00:00:00+09:00",
            ),
        )

    rows = {row["task_id"]: row for row in store.list_tasks(status="queued")}

    assert rows["malformed"]["green_condition"] == {}
    assert rows["malformed"]["allowed_paths"] == []
    assert rows["malformed"]["blocked_paths"] == []
    assert rows["malformed"]["policy_decode_errors"] == [
        {"field": "allowed_paths", "reason": "invalid_json"},
        {"field": "blocked_paths", "reason": "invalid_json"},
    ]
    assert rows["wrong_type"]["green_condition"] == {}
    assert rows["wrong_type"]["allowed_paths"] == []
    assert rows["wrong_type"]["blocked_paths"] == []
    assert rows["wrong_type"]["policy_decode_errors"] == [
        {"field": "allowed_paths", "reason": "wrong_type"},
        {"field": "blocked_paths", "reason": "wrong_type"},
    ]


def test_upsert_task_updates_existing_task_without_duplicate(tmp_path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    store.initialize()
    initial = RepairTask(
        task_id="task_1",
        venue="kis",
        discipline_id="api",
        source_validation_run_id="validation_run_1",
        status="queued",
        priority=3,
        owner="worker-2",
        automation_hook="pytest",
        failure_status="red",
        failure_evidence="first failure",
    )
    updated = RepairTask(
        task_id="task_1",
        venue="kis",
        discipline_id="api",
        source_validation_run_id="validation_run_1",
        status="queued",
        priority=9,
        owner="worker-2",
        automation_hook="pytest",
        failure_status="red",
        failure_evidence="newer failure",
    )

    first_result = store.upsert_task(initial, now_iso="2026-07-02T00:00:00+09:00")
    second_result = store.upsert_task(updated, now_iso="2026-07-02T00:05:00+09:00")

    rows = store.list_tasks(status="queued")
    assert first_result is True
    assert second_result is False
    assert len(rows) == 1
    assert rows[0]["priority"] == 9
    assert rows[0]["failure_evidence"] == "newer failure"
    assert rows[0]["created_at"] == "2026-07-02T00:00:00+09:00"
    assert rows[0]["updated_at"] == "2026-07-02T00:05:00+09:00"


def test_list_patch_attempts_decodes_files_json(tmp_path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    store.initialize()

    attempt_id = store.record_patch_attempt(
        run_id="run_1",
        task_id="task_1",
        status="failed",
        patch_summary="verification_failed",
        files=["src/tradecraft/services/foo.py"],
        created_at="2026-07-02T00:00:00+09:00",
    )

    attempts = store.list_patch_attempts()
    assert len(attempts) == 1
    assert attempts[0]["attempt_id"] == attempt_id
    assert attempts[0]["files"] == ["src/tradecraft/services/foo.py"]


def test_mark_running_repair_runs_failed_closes_runs_and_tasks(tmp_path) -> None:
    store = JueCodexLabStore(tmp_path / "codex_lab.db")
    store.initialize()
    task = RepairTask(
        task_id="task_1",
        venue="binance",
        discipline_id="validation",
        source_validation_run_id="validation_run_1",
        status="queued",
        priority=7,
        owner="worker-2",
        automation_hook="pytest",
        failure_status="red",
    )
    store.upsert_task(task, "2026-07-02T00:00:00+09:00")
    store.record_repair_run_start(
        run_id="repair_run_1",
        task_id="task_1",
        owner="worker-2",
        started_at="2026-07-02T00:01:00+09:00",
    )

    closed = store.mark_running_repair_runs_failed(
        status="cycle_timeout",
        message="isolated worker timed out",
        finished_at="2026-07-02T00:11:00+09:00",
    )

    assert closed == [
        {
            "run_id": "repair_run_1",
            "task_id": "task_1",
            "started_at": "2026-07-02T00:01:00+09:00",
        }
    ]
    repair_run = store.list_repair_runs()[0]
    assert repair_run["status"] == "cycle_timeout"
    assert repair_run["finished_at"] == "2026-07-02T00:11:00+09:00"
    assert repair_run["summary"]["message"] == "isolated worker timed out"
    assert store.list_tasks(status="failed")[0]["task_id"] == "task_1"
    event = store.list_deployment_events()[0]
    assert event["status"] == "cycle_timeout"
    assert event["detail"]["reason"] == "cycle_timeout"
