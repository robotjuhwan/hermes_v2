from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_SCHEMA_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        default=str,
        ensure_ascii=False,
        sort_keys=True,
    )


def _json_loads(value: str | None, fallback: Any, *, field: str = "json") -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        logger.warning("malformed codex native %s: %s", field, exc)
        return fallback


def _lease_owner_pid(owner: str) -> int | None:
    parts = str(owner or "").split(":", 2)
    if len(parts) < 3 or parts[0] != "pid":
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class CodexNativeStore:
    def __init__(self, path: str) -> None:
        self.path = str(path or ".runtime/codex_native_threads.db")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        key = str(Path(self.path).expanduser().resolve())
        with _SCHEMA_LOCK:
            if key not in _INITIALIZED_PATHS:
                self._init()
                _INITIALIZED_PATHS.add(key)

    def _connect(self, *, initialize: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        if initialize:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._connect(initialize=True) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_threads (
                    thread_key TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    component TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    resumed_at TEXT NOT NULL,
                    compacted_at TEXT NOT NULL DEFAULT '',
                    archived_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_turns (
                    run_id TEXT PRIMARY KEY,
                    thread_key TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    input_hash TEXT NOT NULL DEFAULT '',
                    output_schema_hash TEXT NOT NULL DEFAULT '',
                    skill_refs_json TEXT NOT NULL DEFAULT '[]',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    thread_read_json TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_account_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    account_label TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    checked_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_model_checks (
                    model TEXT PRIMARY KEY,
                    available INTEGER NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    checked_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_thread_leases (
                    thread_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_runtime_events (
                    event_id TEXT PRIMARY KEY,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL DEFAULT '',
                    workflow_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    reasoning_effort TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )

    def upsert_thread(
        self,
        *,
        thread_key: str,
        thread_id: str,
        component: str,
        workflow_id: str,
        model: str,
        reasoning_effort: str,
        status: str,
        metadata: dict[str, Any],
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_threads (
                    thread_key, thread_id, component, workflow_id, model,
                    reasoning_effort, status, metadata_json, created_at, resumed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_key) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    component = excluded.component,
                    workflow_id = excluded.workflow_id,
                    model = excluded.model,
                    reasoning_effort = excluded.reasoning_effort,
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    resumed_at = excluded.resumed_at
                """,
                (
                    thread_key,
                    thread_id,
                    component,
                    workflow_id,
                    model,
                    reasoning_effort,
                    status,
                    _json_dumps(metadata),
                    now,
                    now,
                ),
            )

    def get_active_thread(self, thread_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM codex_threads
                WHERE thread_key = ? AND status = 'active'
                """,
                (thread_key,),
            ).fetchone()
        if row is None:
            return None
        return self._thread_row(row)

    def mark_thread_compacted(self, thread_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE codex_threads SET compacted_at = ? WHERE thread_key = ?",
                (_utc_now(), thread_key),
            )

    def archive_thread(self, thread_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE codex_threads
                SET status = 'archived', archived_at = ?
                WHERE thread_key = ?
                """,
                (_utc_now(), thread_key),
            )

    def record_turn(
        self,
        *,
        thread_key: str,
        thread_id: str,
        component: str,
        operation: str,
        workflow_id: str,
        model: str,
        reasoning_effort: str,
        status: str,
        latency_ms: int,
        input_hash: str,
        output_schema_hash: str,
        skill_refs: list[dict[str, Any]],
        usage: dict[str, Any] | None,
        error_message: str,
        result: dict[str, Any] | None,
        thread_read: dict[str, Any] | None,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_turns (
                    run_id, thread_key, thread_id, component, operation, workflow_id,
                    model, reasoning_effort, status, latency_ms, input_hash,
                    output_schema_hash, skill_refs_json, usage_json, error_message,
                    result_json, thread_read_json, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    thread_key,
                    thread_id,
                    component,
                    operation,
                    workflow_id,
                    model,
                    reasoning_effort,
                    status,
                    max(int(latency_ms), 0),
                    input_hash,
                    output_schema_hash,
                    _json_dumps(skill_refs),
                    _json_dumps(usage or {}),
                    str(error_message or "")[:2000],
                    _json_dumps(result or {}),
                    _json_dumps(thread_read) if thread_read else "",
                    now,
                    now,
                ),
            )
        return run_id

    def list_recent_turns(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 200), 1)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_turns ORDER BY finished_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._turn_row(row) for row in rows]

    def record_runtime_event(
        self,
        *,
        component: str,
        operation: str,
        workflow_id: str,
        model: str,
        reasoning_effort: str,
        status: str,
        error_message: str,
        detail: dict[str, Any] | None = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_runtime_events (
                    event_id, component, operation, workflow_id, model,
                    reasoning_effort, status, error_message, detail_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(component or "unknown"),
                    str(operation or ""),
                    str(workflow_id or ""),
                    str(model or ""),
                    str(reasoning_effort or ""),
                    str(status or "error"),
                    str(error_message or "")[:2000],
                    _json_dumps(detail or {}),
                    _utc_now(),
                ),
            )
        return event_id

    def list_recent_runtime_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 200), 1)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_runtime_events ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._runtime_event_row(row) for row in rows]

    def count_turns_for_thread(self, thread_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM codex_turns WHERE thread_key = ?",
                (thread_key,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def acquire_thread_lease(
        self,
        *,
        thread_key: str,
        owner: str,
        ttl_sec: int,
    ) -> bool:
        key = str(thread_key or "").strip()
        if not key:
            return True
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=max(int(ttl_sec), 1))).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, expires_at FROM codex_thread_leases WHERE thread_key = ?",
                (key,),
            ).fetchone()
            if row is not None and str(row["expires_at"] or "") > now:
                owner_pid = _lease_owner_pid(str(row["owner"] or ""))
                if owner_pid is None or _process_is_alive(owner_pid):
                    return False
            conn.execute(
                """
                INSERT INTO codex_thread_leases (
                    thread_key, owner, acquired_at, expires_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(thread_key) DO UPDATE SET
                    owner = excluded.owner,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at
                """,
                (key, owner, now, expires_at),
            )
        return True

    def release_thread_lease(self, *, thread_key: str, owner: str) -> None:
        key = str(thread_key or "").strip()
        if not key:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM codex_thread_leases WHERE thread_key = ? AND owner = ?",
                (key, owner),
            )

    def record_account_check(
        self,
        *,
        status: str,
        account_label: str,
        detail: dict[str, Any],
        error_message: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_account_checks (
                    status, account_label, detail_json, error_message, checked_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    status,
                    account_label,
                    _json_dumps(detail),
                    str(error_message or "")[:2000],
                    _utc_now(),
                ),
            )

    def record_model_check(
        self,
        *,
        model: str,
        available: bool,
        detail: dict[str, Any],
        error_message: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_model_checks (
                    model, available, detail_json, error_message, checked_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model) DO UPDATE SET
                    available = excluded.available,
                    detail_json = excluded.detail_json,
                    error_message = excluded.error_message,
                    checked_at = excluded.checked_at
                """,
                (
                    model,
                    1 if available else 0,
                    _json_dumps(detail),
                    str(error_message or "")[:2000],
                    _utc_now(),
                ),
            )

    def latest_account_check(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM codex_account_checks ORDER BY checked_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "status": row["status"],
            "account_label": row["account_label"],
            "detail": _json_loads(
                row["detail_json"],
                {},
                field="account.detail_json",
            ),
            "error_message": row["error_message"],
            "checked_at": row["checked_at"],
        }

    def list_model_checks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_model_checks ORDER BY model"
            ).fetchall()
        return [
            {
                "model": row["model"],
                "available": bool(row["available"]),
                "detail": _json_loads(
                    row["detail_json"],
                    {},
                    field="model.detail_json",
                ),
                "error_message": row["error_message"],
                "checked_at": row["checked_at"],
            }
            for row in rows
        ]

    def _thread_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "thread_key": row["thread_key"],
            "thread_id": row["thread_id"],
            "component": row["component"],
            "workflow_id": row["workflow_id"],
            "model": row["model"],
            "reasoning_effort": row["reasoning_effort"],
            "status": row["status"],
            "metadata": _json_loads(row["metadata_json"], {}, field="thread.metadata_json"),
            "created_at": row["created_at"],
            "resumed_at": row["resumed_at"],
            "compacted_at": row["compacted_at"],
            "archived_at": row["archived_at"],
        }

    def _turn_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "thread_key": row["thread_key"],
            "thread_id": row["thread_id"],
            "component": row["component"],
            "operation": row["operation"],
            "workflow_id": row["workflow_id"],
            "model": row["model"],
            "reasoning_effort": row["reasoning_effort"],
            "status": row["status"],
            "latency_ms": row["latency_ms"],
            "input_hash": row["input_hash"],
            "output_schema_hash": row["output_schema_hash"],
            "skill_refs": _json_loads(
                row["skill_refs_json"],
                [],
                field="turn.skill_refs_json",
            ),
            "usage": _json_loads(row["usage_json"], {}, field="turn.usage_json"),
            "error_message": row["error_message"],
            "result": _json_loads(row["result_json"], {}, field="turn.result_json"),
            "thread_read": _json_loads(
                row["thread_read_json"],
                None,
                field="turn.thread_read_json",
            ),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def _runtime_event_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "component": row["component"],
            "operation": row["operation"],
            "workflow_id": row["workflow_id"],
            "model": row["model"],
            "reasoning_effort": row["reasoning_effort"],
            "status": row["status"],
            "error_message": row["error_message"],
            "detail": _json_loads(
                row["detail_json"],
                {},
                field="runtime_event.detail_json",
            ),
            "created_at": row["created_at"],
            "finished_at": row["created_at"],
        }
