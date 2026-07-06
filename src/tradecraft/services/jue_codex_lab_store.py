from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from tradecraft.services.jue_codex_lab_models import RepairTask

logger = logging.getLogger(__name__)
TERMINAL_TASK_STATUSES = {"failed", "deployed"}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _json_loads(
    value: str | None,
    fallback: Any,
    *,
    field: str,
    expected_type: type,
) -> Any:
    if not value:
        return fallback
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        logger.warning("malformed codex lab %s JSON: %s", field, exc)
        return fallback
    if not isinstance(decoded, expected_type):
        logger.warning(
            "malformed codex lab %s JSON: expected %s, got %s",
            field,
            expected_type.__name__,
            type(decoded).__name__,
        )
        return fallback
    return decoded


def _json_loads_with_error(
    value: str | None,
    fallback: Any,
    *,
    field: str,
    expected_type: type,
) -> tuple[Any, dict[str, str] | None]:
    if not value:
        return fallback, None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        logger.warning("malformed codex lab %s JSON: %s", field, exc)
        return fallback, {"field": field, "reason": "invalid_json"}
    if not isinstance(decoded, expected_type):
        logger.warning(
            "malformed codex lab %s JSON: expected %s, got %s",
            field,
            expected_type.__name__,
            type(decoded).__name__,
        )
        return fallback, {"field": field, "reason": "wrong_type"}
    return decoded, None


class JueCodexLabStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        if str(self.db_path) != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_tasks (
                    task_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    discipline_id TEXT NOT NULL,
                    source_validation_run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    owner TEXT NOT NULL,
                    automation_hook TEXT NOT NULL,
                    failure_status TEXT NOT NULL,
                    failure_evidence TEXT NOT NULL DEFAULT '',
                    green_condition_json TEXT NOT NULL DEFAULT '{}',
                    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
                    blocked_paths_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS patch_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    patch_summary TEXT NOT NULL DEFAULT '',
                    files_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS verification_results (
                    result_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deployment_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS green_path_progress (
                    progress_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._migrate_green_path_progress_schema(conn)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_repair_tasks_status_priority_updated_task
                ON repair_tasks (
                    status,
                    priority DESC,
                    updated_at DESC,
                    task_id ASC
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_repair_tasks_priority_updated_task
                ON repair_tasks (
                    priority DESC,
                    updated_at DESC,
                    task_id ASC
                )
                """
            )

    @staticmethod
    def _migrate_green_path_progress_schema(conn: sqlite3.Connection) -> None:
        columns = JueCodexLabStore._table_columns(conn, "green_path_progress")
        required_columns = {
            "progress_id": "TEXT",
            "task_id": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT ''",
            "progress_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in required_columns.items():
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE green_path_progress ADD COLUMN {column} {definition}"
                )

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row[1]) for row in rows}

    def upsert_task(self, task: RepairTask, now_iso: str) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO repair_tasks (
                    task_id, venue, discipline_id, source_validation_run_id,
                    status, priority, owner, automation_hook, failure_status,
                    failure_evidence, green_condition_json, allowed_paths_json,
                    blocked_paths_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.venue,
                    task.discipline_id,
                    task.source_validation_run_id,
                    task.status,
                    int(task.priority),
                    task.owner,
                    task.automation_hook,
                    task.failure_status,
                    task.failure_evidence,
                    _json_dumps(task.green_condition),
                    _json_dumps(task.allowed_paths),
                    _json_dumps(task.blocked_paths),
                    now_iso,
                    now_iso,
                ),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                existing = conn.execute(
                    """
                    SELECT status, source_validation_run_id
                    FROM repair_tasks
                    WHERE task_id = ?
                    """,
                    (task.task_id,),
                ).fetchone()
                existing_status = (
                    _clean_text(existing["status"]).lower()
                    if existing is not None
                    else ""
                )
                existing_source_run_id = (
                    _clean_text(existing["source_validation_run_id"])
                    if existing is not None
                    else ""
                )
                preserve_terminal = (
                    existing_status in TERMINAL_TASK_STATUSES
                    and existing_source_run_id == task.source_validation_run_id
                )
                if preserve_terminal:
                    conn.execute(
                        """
                        UPDATE repair_tasks
                        SET venue = ?,
                            discipline_id = ?,
                            priority = ?,
                            owner = ?,
                            automation_hook = ?,
                            failure_status = ?,
                            failure_evidence = ?,
                            green_condition_json = ?,
                            allowed_paths_json = ?,
                            blocked_paths_json = ?,
                            updated_at = ?
                        WHERE task_id = ?
                        """,
                        (
                            task.venue,
                            task.discipline_id,
                            int(task.priority),
                            task.owner,
                            task.automation_hook,
                            task.failure_status,
                            task.failure_evidence,
                            _json_dumps(task.green_condition),
                            _json_dumps(task.allowed_paths),
                            _json_dumps(task.blocked_paths),
                            now_iso,
                            task.task_id,
                        ),
                    )
                    return False
                conn.execute(
                    """
                    UPDATE repair_tasks
                    SET venue = ?,
                        discipline_id = ?,
                        source_validation_run_id = ?,
                        status = ?,
                        priority = ?,
                        owner = ?,
                        automation_hook = ?,
                        failure_status = ?,
                        failure_evidence = ?,
                        green_condition_json = ?,
                        allowed_paths_json = ?,
                        blocked_paths_json = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        task.venue,
                        task.discipline_id,
                        task.source_validation_run_id,
                        task.status,
                        int(task.priority),
                        task.owner,
                        task.automation_hook,
                        task.failure_status,
                        task.failure_evidence,
                        _json_dumps(task.green_condition),
                        _json_dumps(task.allowed_paths),
                        _json_dumps(task.blocked_paths),
                        now_iso,
                        task.task_id,
                    ),
                )
        return inserted

    def record_repair_run_start(
        self,
        *,
        run_id: str,
        task_id: str,
        owner: str,
        started_at: str,
        summary: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO repair_runs (
                    run_id, task_id, status, owner, started_at,
                    finished_at, summary_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    "running",
                    owner,
                    started_at,
                    "",
                    _json_dumps(summary or {}),
                ),
            )

    def finish_repair_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: str,
        summary: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE repair_runs
                SET status = ?,
                    finished_at = ?,
                    summary_json = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    finished_at,
                    _json_dumps(summary or {}),
                    run_id,
                ),
            )

    def mark_running_repair_runs_failed(
        self,
        *,
        status: str,
        message: str,
        finished_at: str,
    ) -> list[dict[str, Any]]:
        clean_status = _clean_text(status) or "cycle_timeout"
        clean_message = _clean_text(message)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, task_id, started_at
                FROM repair_runs
                WHERE status = 'running'
                ORDER BY started_at ASC, run_id ASC
                """
            ).fetchall()
            closed = [
                {
                    "run_id": str(row["run_id"]),
                    "task_id": str(row["task_id"]),
                    "started_at": str(row["started_at"]),
                }
                for row in rows
            ]
            for row in closed:
                summary = {
                    "reason": clean_status,
                    "message": clean_message,
                    "started_at": row["started_at"],
                }
                conn.execute(
                    """
                    UPDATE repair_runs
                    SET status = ?,
                        finished_at = ?,
                        summary_json = ?
                    WHERE run_id = ?
                    """,
                    (
                        clean_status,
                        finished_at,
                        _json_dumps(summary),
                        row["run_id"],
                    ),
                )
                conn.execute(
                    """
                    UPDATE repair_tasks
                    SET status = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    ("failed", finished_at, row["task_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO deployment_events (
                        event_id, task_id, run_id, status, detail_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"deployment_event_{uuid4().hex}",
                        row["task_id"],
                        row["run_id"],
                        clean_status,
                        _json_dumps(summary),
                        finished_at,
                    ),
                )
        return closed

    def list_repair_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM repair_runs
                ORDER BY started_at ASC, run_id ASC
                """
            ).fetchall()
        runs: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["summary"] = _json_loads(
                payload.pop("summary_json"),
                {},
                field="repair_run.summary",
                expected_type=dict,
            )
            runs.append(payload)
        return runs

    def list_tasks(self, status: str = "") -> list[dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM repair_tasks
                    WHERE status = ?
                    ORDER BY priority DESC, updated_at DESC, task_id ASC
                    """,
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM repair_tasks
                    ORDER BY priority DESC, updated_at DESC, task_id ASC
                    """
                ).fetchall()
        return [self._task_row(row) for row in rows]

    def task_status_counts(self) -> dict[str, Any]:
        if str(self.db_path) != ":memory:" and not self.db_path.exists():
            return {"initialized": False, "counts": {}}
        with self._connect_readonly() as conn:
            table = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'repair_tasks'
                """
            ).fetchone()
            if table is None:
                return {"initialized": False, "counts": {}}
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM repair_tasks
                GROUP BY status
                """
            ).fetchall()
        return {
            "initialized": True,
            "counts": {str(row["status"]): int(row["count"]) for row in rows},
        }

    def _connect_readonly(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        else:
            uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, timeout=30.0, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def mark_task_status(self, task_id: str, status: str, now_iso: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE repair_tasks
                SET status = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (status, now_iso, task_id),
            )

    def record_green_path_progress(
        self,
        venue: str,
        discipline_id: str,
        before_status: str,
        after_status: str,
        before_score: Any,
        after_score: Any,
        validation_run_before: str,
        validation_run_after: str,
        repair_task_id: str,
    ) -> dict[str, Any]:
        self.initialize()
        now_iso = _utc_now_iso()
        progress_id = f"green_path_progress_{uuid4().hex}"
        payload = {
            "venue": _clean_text(venue).lower(),
            "discipline_id": _clean_text(discipline_id),
            "before_status": _clean_text(before_status).lower(),
            "after_status": _clean_text(after_status).lower(),
            "before_score": before_score,
            "after_score": after_score,
            "validation_run_before": _clean_text(validation_run_before),
            "validation_run_after": _clean_text(validation_run_after),
            "repair_task_id": _clean_text(repair_task_id),
        }
        with self._connect() as conn:
            columns = self._table_columns(conn, "green_path_progress")
            insert_payload: dict[str, Any] = {
                "progress_id": progress_id,
                "task_id": payload["repair_task_id"],
                "status": payload["after_status"],
                "progress_json": _json_dumps(payload),
                "created_at": now_iso,
                "updated_at": now_iso,
                **payload,
            }
            insert_columns = [
                column
                for column in (
                    "progress_id",
                    "task_id",
                    "status",
                    "progress_json",
                    "venue",
                    "discipline_id",
                    "before_status",
                    "after_status",
                    "before_score",
                    "after_score",
                    "validation_run_before",
                    "validation_run_after",
                    "repair_task_id",
                    "created_at",
                    "updated_at",
                )
                if column in columns
            ]
            placeholders = ", ".join(["?"] * len(insert_columns))
            conn.execute(
                f"""
                INSERT INTO green_path_progress ({", ".join(insert_columns)})
                VALUES ({placeholders})
                """,
                tuple(insert_payload[column] for column in insert_columns),
            )
        return {
            "progress_id": progress_id,
            **payload,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

    def list_green_path_progress(self, venue: str = "") -> list[dict[str, Any]]:
        self.initialize()
        clean_venue = _clean_text(venue).lower()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM green_path_progress
                ORDER BY created_at ASC, progress_id ASC
                """
            ).fetchall()
        progress_rows: list[dict[str, Any]] = []
        for row in rows:
            payload = self._green_path_progress_row(row)
            if clean_venue and payload.get("venue") != clean_venue:
                continue
            progress_rows.append(payload)
        return progress_rows

    def record_patch_attempt(
        self,
        run_id: str,
        task_id: str,
        status: str,
        patch_summary: str,
        files: list[str],
        created_at: str,
    ) -> str:
        attempt_id = f"patch_attempt_{uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO patch_attempts (
                    attempt_id, run_id, task_id, status, patch_summary,
                    files_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    run_id,
                    task_id,
                    status,
                    patch_summary,
                    _json_dumps(files),
                    created_at,
                ),
            )
        return attempt_id

    def record_deployment_event(
        self,
        task_id: str,
        run_id: str = "",
        status: str = "",
        detail: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> str:
        event_id = f"deployment_event_{uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deployment_events (
                    event_id, task_id, run_id, status, detail_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    task_id,
                    run_id,
                    status,
                    _json_dumps(detail or {}),
                    created_at,
                ),
            )
        return event_id

    def list_deployment_events(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM deployment_events
                ORDER BY created_at ASC, event_id ASC
                """
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["detail"] = _json_loads(
                payload.pop("detail_json"),
                {},
                field="deployment_event.detail",
                expected_type=dict,
            )
            events.append(payload)
        return events

    def list_patch_attempts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM patch_attempts
                ORDER BY created_at ASC, attempt_id ASC
                """
            ).fetchall()
        attempts: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["files"] = _json_loads(
                payload.pop("files_json"),
                [],
                field="patch_attempt.files",
                expected_type=list,
            )
            attempts.append(payload)
        return attempts

    @staticmethod
    def _task_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["green_condition"] = _json_loads(
            payload.pop("green_condition_json"),
            {},
            field="green_condition",
            expected_type=dict,
        )
        policy_decode_errors: list[dict[str, str]] = []
        allowed_paths, allowed_error = _json_loads_with_error(
            payload.pop("allowed_paths_json"),
            [],
            field="allowed_paths",
            expected_type=list,
        )
        blocked_paths, blocked_error = _json_loads_with_error(
            payload.pop("blocked_paths_json"),
            [],
            field="blocked_paths",
            expected_type=list,
        )
        payload["allowed_paths"] = allowed_paths
        payload["blocked_paths"] = blocked_paths
        if allowed_error is not None:
            policy_decode_errors.append(allowed_error)
        if blocked_error is not None:
            policy_decode_errors.append(blocked_error)
        payload["policy_decode_errors"] = policy_decode_errors
        return payload

    @staticmethod
    def _green_path_progress_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        progress = _json_loads(
            payload.pop("progress_json"),
            {},
            field="green_path_progress.progress",
            expected_type=dict,
        )
        def value_for(field: str, fallback: Any = "") -> Any:
            value = progress.get(field)
            if value not in (None, ""):
                return value
            return payload.get(field, fallback)

        return {
            "progress_id": str(payload.get("progress_id") or ""),
            "venue": _clean_text(value_for("venue")).lower(),
            "discipline_id": _clean_text(value_for("discipline_id")),
            "before_status": _clean_text(value_for("before_status")).lower(),
            "after_status": _clean_text(value_for("after_status")).lower()
            or _clean_text(payload.get("status")).lower(),
            "before_score": value_for("before_score", None),
            "after_score": value_for("after_score", None),
            "validation_run_before": _clean_text(value_for("validation_run_before")),
            "validation_run_after": _clean_text(value_for("validation_run_after")),
            "repair_task_id": _clean_text(
                value_for("repair_task_id") or payload.get("task_id")
            ),
            "created_at": _clean_text(payload.get("created_at")),
            "updated_at": _clean_text(payload.get("updated_at")),
        }
