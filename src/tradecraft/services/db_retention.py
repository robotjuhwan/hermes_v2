from __future__ import annotations

import base64
import gzip
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

TimestampKind = Literal["iso8601", "unix_seconds", "unix_ms"]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ARCHIVE_ARCHIVED_AT_COLUMN = "__archive_archived_at"
_ARCHIVE_COMPRESSED_COLUMNS_COLUMN = "__archive_compressed_columns_json"


@dataclass(frozen=True)
class RetentionRule:
    table: str
    timestamp_column: str
    retention_days: int
    timestamp_kind: TimestampKind = "iso8601"
    archive_table: str | None = None
    archive_compress_columns: tuple[str, ...] = ()
    archive_batch_size: int = 1000
    minimum_timestamp_value: str | int | None = None
    vacuum_after_delete: bool = False


class SQLiteRetentionPruner:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def compact_archive_columns(
        self,
        *,
        table: str,
        columns: tuple[str, ...],
        batch_size: int = 1000,
        vacuum: bool = False,
    ) -> dict[str, Any]:
        table_name = _validate_identifier(table)
        target_columns = tuple(_validate_identifier(column) for column in columns)
        if not target_columns:
            return {"status": "skipped", "reason": "columns_missing"}
        compacted = 0
        skipped_already_compressed = 0
        skipped_empty = 0
        with sqlite3.connect(self.db_path) as conn:
            if not _table_exists(conn, table_name):
                return {"status": "skipped", "reason": "table_missing"}
            table_columns = set(_table_columns(conn, table_name))
            missing = [column for column in target_columns if column not in table_columns]
            if missing:
                return {
                    "status": "skipped",
                    "reason": "column_missing",
                    "missing_columns": missing,
                }
            _ensure_archive_metadata_columns(conn, table_name)
            select_columns = ", ".join(f'"{column}"' for column in target_columns)
            cursor = conn.execute(
                f'SELECT rowid, {select_columns}, '
                f'"{_ARCHIVE_COMPRESSED_COLUMNS_COLUMN}" '
                f'FROM "{table_name}" '
                f"WHERE "
                + " OR ".join(
                    f"""("{column}" != "" AND "{column}" NOT LIKE 'gzip+base64:%')"""
                    for column in target_columns
                )
                + " LIMIT ?",
                (max(int(batch_size or 1000), 1),),
            )
            rows = cursor.fetchall()
            for row in rows:
                rowid = int(row[0])
                values = list(row[1 : 1 + len(target_columns)])
                metadata_raw = str(row[1 + len(target_columns)] or "[]")
                try:
                    compressed_columns = set(json.loads(metadata_raw))
                except json.JSONDecodeError:
                    compressed_columns = set()
                updates: dict[str, str] = {}
                for column, value in zip(target_columns, values):
                    text = str(value or "")
                    if not text:
                        skipped_empty += 1
                        continue
                    if text.startswith("gzip+base64:"):
                        skipped_already_compressed += 1
                        compressed_columns.add(column)
                        continue
                    updates[column] = _gzip_base64_text(text)
                    compressed_columns.add(column)
                if not updates:
                    conn.execute(
                        f'UPDATE "{table_name}" '
                        f'SET "{_ARCHIVE_COMPRESSED_COLUMNS_COLUMN}" = ? '
                        f"WHERE rowid = ?",
                        (
                            json.dumps(
                                sorted(compressed_columns),
                                ensure_ascii=False,
                            ),
                            rowid,
                        ),
                    )
                    continue
                set_sql = ", ".join(f'"{column}" = ?' for column in updates)
                params = [
                    *updates.values(),
                    json.dumps(sorted(compressed_columns), ensure_ascii=False),
                    rowid,
                ]
                conn.execute(
                    f'UPDATE "{table_name}" '
                    f'SET {set_sql}, "{_ARCHIVE_COMPRESSED_COLUMNS_COLUMN}" = ? '
                    f"WHERE rowid = ?",
                    tuple(params),
                )
                compacted += 1
            conn.commit()
        if vacuum and compacted:
            with sqlite3.connect(self.db_path, isolation_level=None) as conn:
                conn.execute("VACUUM")
        return {
            "status": "ok",
            "table": table_name,
            "columns": list(target_columns),
            "compacted": compacted,
            "skipped_already_compressed": skipped_already_compressed,
            "skipped_empty": skipped_empty,
            "batch_size": max(int(batch_size or 1000), 1),
            "vacuumed": bool(vacuum and compacted),
        }

    def prune(self, rules: list[RetentionRule]) -> dict[str, Any]:
        results: dict[str, Any] = {"status": "ok", "tables": {}, "vacuumed": False}
        should_vacuum = False
        with sqlite3.connect(self.db_path) as conn:
            for rule in rules:
                table_result = self._apply_rule(conn, rule)
                results["tables"][rule.table] = table_result
                if rule.vacuum_after_delete and int(table_result.get("deleted") or 0) > 0:
                    should_vacuum = True
            conn.commit()
        if should_vacuum:
            with sqlite3.connect(self.db_path, isolation_level=None) as conn:
                conn.execute("VACUUM")
            results["vacuumed"] = True
        return results

    def _apply_rule(
        self,
        conn: sqlite3.Connection,
        rule: RetentionRule,
    ) -> dict[str, Any]:
        table = _validate_identifier(rule.table)
        timestamp_column = _validate_identifier(rule.timestamp_column)
        if int(rule.retention_days) <= 0:
            return {"status": "skipped", "reason": "retention_disabled"}
        if not _table_exists(conn, table):
            return {"status": "skipped", "reason": "table_missing"}
        columns = _table_columns(conn, table)
        if timestamp_column not in set(columns):
            return {
                "status": "skipped",
                "reason": "timestamp_column_missing",
                "timestamp_column": timestamp_column,
            }
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=int(rule.retention_days))
        cutoff = _cutoff_value(cutoff_dt, rule.timestamp_kind)
        where_sql, where_params = _retention_where(
            timestamp_column=timestamp_column,
            cutoff=cutoff,
            minimum_timestamp_value=rule.minimum_timestamp_value,
        )
        archived = 0
        archive_key_columns: list[str] = []
        archive_deduplicated = 0
        if rule.archive_table:
            archive_table = _validate_identifier(rule.archive_table)
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{archive_table}" AS '
                f'SELECT * FROM "{table}" WHERE 0'
            )
            _ensure_archive_metadata_columns(conn, archive_table)
            archive_key_columns = _primary_key_columns(conn, table)
            if archive_key_columns:
                archive_deduplicated = _dedupe_archive_by_key(
                    conn,
                    archive_table=archive_table,
                    key_columns=archive_key_columns,
                )
                _ensure_archive_unique_key(
                    conn,
                    archive_table=archive_table,
                    key_columns=archive_key_columns,
                )
            compress_columns = tuple(
                _validate_identifier(column)
                for column in rule.archive_compress_columns
            )
            archived_at = datetime.now(timezone.utc).isoformat()
            compressed_columns_json = json.dumps(
                list(compress_columns),
                ensure_ascii=False,
                sort_keys=True,
            )
            if compress_columns:
                archived = self._archive_with_compression(
                    conn,
                    table=table,
                    timestamp_column=timestamp_column,
                    where_sql=where_sql,
                    where_params=where_params,
                    archive_table=archive_table,
                    compress_columns=compress_columns,
                    batch_size=max(int(rule.archive_batch_size or 1000), 1),
                    archived_at=archived_at,
                    compressed_columns_json=compressed_columns_json,
                    replace_existing=bool(archive_key_columns),
                )
            else:
                columns = _table_columns(conn, table)
                column_sql = ", ".join(f'"{column}"' for column in columns)
                select_sql = ", ".join(f'"{column}"' for column in columns)
                insert_verb = "INSERT OR REPLACE" if archive_key_columns else "INSERT"
                archived = int(
                    conn.execute(
                        f'{insert_verb} INTO "{archive_table}" '
                        f'({column_sql}, "{_ARCHIVE_ARCHIVED_AT_COLUMN}", '
                        f'"{_ARCHIVE_COMPRESSED_COLUMNS_COLUMN}") '
                        f'SELECT {select_sql}, ?, ? '
                        f'FROM "{table}" WHERE {where_sql}',
                        (archived_at, compressed_columns_json, *where_params),
                    ).rowcount
                    or 0
                )
        deleted = int(
            conn.execute(
                f'DELETE FROM "{table}" WHERE {where_sql}',
                where_params,
            ).rowcount
            or 0
        )
        return {
            "status": "ok",
            "cutoff": cutoff_dt.isoformat(),
            "cutoff_value": cutoff,
            "archived": archived,
            "deleted": deleted,
            "archive_table": rule.archive_table or "",
            "archive_metadata_columns": (
                [
                    _ARCHIVE_ARCHIVED_AT_COLUMN,
                    _ARCHIVE_COMPRESSED_COLUMNS_COLUMN,
                ]
                if rule.archive_table
                else []
            ),
            "compressed": archived if rule.archive_compress_columns else 0,
            "compressed_columns": list(rule.archive_compress_columns),
            "archive_batch_size": (
                max(int(rule.archive_batch_size or 1000), 1)
                if rule.archive_compress_columns
                else 0
            ),
            "archive_key_columns": archive_key_columns,
            "archive_deduplicated": archive_deduplicated,
        }

    def _archive_with_compression(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        timestamp_column: str,
        where_sql: str,
        where_params: tuple[str | int, ...],
        archive_table: str,
        compress_columns: tuple[str, ...],
        batch_size: int,
        archived_at: str,
        compressed_columns_json: str,
        replace_existing: bool = False,
    ) -> int:
        _ = timestamp_column
        cursor = conn.execute(
            f'SELECT * FROM "{table}" WHERE {where_sql}',
            where_params,
        )
        columns = _table_columns(conn, table)
        column_set = set(columns)
        missing = [column for column in compress_columns if column not in column_set]
        if missing:
            raise ValueError(f"missing sqlite columns for compression: {missing!r}")
        insert_columns = [
            *columns,
            _ARCHIVE_ARCHIVED_AT_COLUMN,
            _ARCHIVE_COMPRESSED_COLUMNS_COLUMN,
        ]
        placeholders = ", ".join("?" for _ in insert_columns)
        column_sql = ", ".join(f'"{column}"' for column in insert_columns)
        compress_indexes = {columns.index(column) for column in compress_columns}
        archived = 0
        insert_verb = "INSERT OR REPLACE" if replace_existing else "INSERT"
        while True:
            rows = cursor.fetchmany(max(int(batch_size or 1000), 1))
            if not rows:
                break
            for row in rows:
                values = list(row)
                for index in compress_indexes:
                    values[index] = _gzip_base64_text(values[index])
                values.extend([archived_at, compressed_columns_json])
                conn.execute(
                    f'{insert_verb} INTO "{archive_table}" ({column_sql}) VALUES ({placeholders})',
                    values,
                )
                archived += 1
        return archived


def _validate_identifier(value: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER_RE.match(text):
        raise ValueError(f"invalid sqlite identifier: {value!r}")
    return text


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name = ?
        LIMIT 1
        """,
        (table,),
    ).fetchone()
    return row is not None


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    keyed = [
        (int(row[5]), str(row[1]))
        for row in rows
        if len(row) >= 6 and int(row[5] or 0) > 0
    ]
    keyed.sort(key=lambda item: item[0])
    return [column for _, column in keyed]


def _archive_unique_index_name(archive_table: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", archive_table)
    return _validate_identifier(f"idx_{cleaned}_archive_key")


def _dedupe_archive_by_key(
    conn: sqlite3.Connection,
    *,
    archive_table: str,
    key_columns: list[str],
) -> int:
    if not key_columns:
        return 0
    archive_columns = set(_table_columns(conn, archive_table))
    missing = [column for column in key_columns if column not in archive_columns]
    if missing:
        raise ValueError(f"missing archive key columns: {missing!r}")
    group_sql = ", ".join(f'"{column}"' for column in key_columns)
    return int(
        conn.execute(
            f'DELETE FROM "{archive_table}" '
            f"WHERE rowid NOT IN ("
            f'SELECT MAX(rowid) FROM "{archive_table}" GROUP BY {group_sql}'
            f")"
        ).rowcount
        or 0
    )


def _ensure_archive_unique_key(
    conn: sqlite3.Connection,
    *,
    archive_table: str,
    key_columns: list[str],
) -> None:
    if not key_columns:
        return
    index_name = _archive_unique_index_name(archive_table)
    column_sql = ", ".join(f'"{column}"' for column in key_columns)
    conn.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS "{index_name}" '
        f'ON "{archive_table}" ({column_sql})'
    )


def _ensure_archive_metadata_columns(
    conn: sqlite3.Connection,
    archive_table: str,
) -> None:
    columns = set(_table_columns(conn, archive_table))
    if _ARCHIVE_ARCHIVED_AT_COLUMN not in columns:
        conn.execute(
            f'ALTER TABLE "{archive_table}" '
            f'ADD COLUMN "{_ARCHIVE_ARCHIVED_AT_COLUMN}" TEXT NOT NULL DEFAULT ""'
        )
    if _ARCHIVE_COMPRESSED_COLUMNS_COLUMN not in columns:
        conn.execute(
            f'ALTER TABLE "{archive_table}" '
            f'ADD COLUMN "{_ARCHIVE_COMPRESSED_COLUMNS_COLUMN}" TEXT NOT NULL DEFAULT "[]"'
        )


def _cutoff_value(cutoff_dt: datetime, timestamp_kind: TimestampKind) -> str | int:
    if timestamp_kind == "unix_seconds":
        return int(cutoff_dt.timestamp())
    if timestamp_kind == "unix_ms":
        return int(cutoff_dt.timestamp() * 1000)
    return cutoff_dt.isoformat()


def _retention_where(
    *,
    timestamp_column: str,
    cutoff: str | int,
    minimum_timestamp_value: str | int | None,
) -> tuple[str, tuple[str | int, ...]]:
    column_sql = f'"{timestamp_column}"'
    if minimum_timestamp_value is None:
        return f"{column_sql} < ?", (cutoff,)
    return f"{column_sql} > ? AND {column_sql} < ?", (
        minimum_timestamp_value,
        cutoff,
    )


def _gzip_base64_text(value: Any) -> str:
    return gzip_base64_archive_text(value)


def gzip_base64_archive_text(value: Any) -> str:
    raw = str(value or "").encode("utf-8")
    return "gzip+base64:" + base64.b64encode(gzip.compress(raw)).decode("ascii")


def summarize_sqlite_retention_result(retention: dict[str, Any]) -> dict[str, Any]:
    deleted: dict[str, int] = {}
    archived: dict[str, int] = {}
    compressed: dict[str, int] = {}
    archive_tables: dict[str, str] = {}
    for table, row in dict(retention.get("tables") or {}).items():
        if not isinstance(row, dict) or row.get("status") != "ok":
            continue
        table_key = str(table)
        deleted[table_key] = int(row.get("deleted") or 0)
        archived[table_key] = int(row.get("archived") or 0)
        compressed_count = int(row.get("compressed") or 0)
        if compressed_count:
            compressed[table_key] = compressed_count
        archive_table = str(row.get("archive_table") or "").strip()
        if archive_table:
            archive_tables[table_key] = archive_table
    return {
        "status": str(retention.get("status") or "ok"),
        "deleted": deleted,
        "archived": archived,
        "compressed": compressed,
        "archive_tables": archive_tables,
        "retention": retention,
    }
