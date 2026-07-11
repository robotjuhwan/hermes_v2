from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


OPS_SECTION_SNAPSHOT_VERSION = "ops_section_snapshot_v1"


@dataclass(frozen=True)
class OpsSectionSnapshotV1:
    section: str
    generated_at: str
    payload: dict[str, Any]
    version: str = OPS_SECTION_SNAPSHOT_VERSION


def persist_ops_section_snapshot(
    conn: sqlite3.Connection,
    snapshot: OpsSectionSnapshotV1,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO wiki_ops_section_snapshots (
            section, version, payload_json, generated_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            snapshot.section,
            snapshot.version,
            json.dumps(snapshot.payload, ensure_ascii=False, sort_keys=True),
            snapshot.generated_at,
        ),
    )


def read_ops_section_snapshot(
    db_path: str | Path,
    *,
    section: str,
) -> OpsSectionSnapshotV1 | None:
    path = Path(db_path)
    if not path.is_file():
        return None
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'wiki_ops_section_snapshots'
                """
            ).fetchone()
            if table is None:
                return None
            row = conn.execute(
                """
                SELECT section, version, payload_json, generated_at
                FROM wiki_ops_section_snapshots
                WHERE section = ?
                LIMIT 1
                """,
                (section,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return OpsSectionSnapshotV1(
        section=str(row["section"] or section),
        version=str(row["version"] or OPS_SECTION_SNAPSHOT_VERSION),
        generated_at=str(row["generated_at"] or ""),
        payload=payload,
    )
