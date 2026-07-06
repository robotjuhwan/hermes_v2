from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


class JueLifecycleRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jue_lifecycle_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_type TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary_md TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jue_lifecycle_symbol
                ON jue_lifecycle_artifacts(symbol, updated_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jue_lifecycle_workflow
                ON jue_lifecycle_artifacts(workflow_id, updated_at)
                """
            )

    def upsert_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("artifact_id required")
        row = {
            "artifact_id": artifact_id,
            "artifact_type": str(artifact.get("artifact_type") or "note"),
            "workflow_id": str(artifact.get("workflow_id") or ""),
            "symbol": str(artifact.get("symbol") or ""),
            "title": str(artifact.get("title") or ""),
            "summary_md": str(artifact.get("summary_md") or ""),
            "payload_json": _json_dumps(artifact.get("payload") or {}),
            "evidence_json": _json_dumps(artifact.get("evidence") or []),
            "status": str(artifact.get("status") or "active"),
            "created_at": str(artifact.get("created_at") or now),
            "updated_at": str(artifact.get("updated_at") or now),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jue_lifecycle_artifacts (
                    artifact_id, artifact_type, workflow_id, symbol, title,
                    summary_md, payload_json, evidence_json, status, created_at,
                    updated_at
                ) VALUES (
                    :artifact_id, :artifact_type, :workflow_id, :symbol, :title,
                    :summary_md, :payload_json, :evidence_json, :status,
                    :created_at, :updated_at
                )
                ON CONFLICT(artifact_id) DO UPDATE SET
                    artifact_type=excluded.artifact_type,
                    workflow_id=excluded.workflow_id,
                    symbol=excluded.symbol,
                    title=excluded.title,
                    summary_md=excluded.summary_md,
                    payload_json=excluded.payload_json,
                    evidence_json=excluded.evidence_json,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                row,
            )
        return self.get_artifact(artifact_id) or row

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jue_lifecycle_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        return _decode_row(row) if row else None

    def list_artifacts(
        self,
        *,
        symbols: list[str] | None = None,
        workflow_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = ["status='active'"]
        params: list[Any] = []
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if workflow_id:
            clauses.append("workflow_id=?")
            params.append(workflow_id)
        params.append(max(int(limit), 1))
        sql = (
            "SELECT * FROM jue_lifecycle_artifacts WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode_row(row) for row in rows]


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = _json_loads(data.pop("payload_json", "{}"), {})
    data["evidence"] = _json_loads(data.pop("evidence_json", "[]"), [])
    return data
