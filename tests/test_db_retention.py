from __future__ import annotations

import base64
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradecraft.services.db_retention import RetentionRule, SQLiteRetentionPruner


def test_sqlite_retention_archives_old_rows_and_keeps_hot_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "retention.db"
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    hot = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "INSERT INTO quote_snapshots (symbol, fetched_at, raw_json) VALUES (?, ?, ?)",
            ("OLDUSDT", old, '{"old": true}'),
        )
        conn.execute(
            "INSERT INTO quote_snapshots (symbol, fetched_at, raw_json) VALUES (?, ?, ?)",
            ("HOTUSDT", hot, '{"hot": true}'),
        )

    result = SQLiteRetentionPruner(db_path).prune(
        [
            RetentionRule(
                table="quote_snapshots",
                timestamp_column="fetched_at",
                retention_days=7,
                archive_table="quote_snapshots_archive",
            )
        ]
    )

    assert result["status"] == "ok"
    assert result["tables"]["quote_snapshots"]["archived"] == 1
    assert result["tables"]["quote_snapshots"]["deleted"] == 1
    with sqlite3.connect(db_path) as conn:
        hot_rows = conn.execute(
            "SELECT symbol FROM quote_snapshots ORDER BY id"
        ).fetchall()
        archived_rows = conn.execute(
            "SELECT symbol, raw_json FROM quote_snapshots_archive ORDER BY id"
        ).fetchall()
    assert hot_rows == [("HOTUSDT",)]
    assert archived_rows == [("OLDUSDT", '{"old": true}')]


def test_sqlite_retention_skips_disabled_rules(tmp_path: Path) -> None:
    db_path = tmp_path / "retention.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE manager_runs (id INTEGER PRIMARY KEY, run_at TEXT)")
        conn.execute("INSERT INTO manager_runs (id, run_at) VALUES (1, '2026-01-01')")

    result = SQLiteRetentionPruner(db_path).prune(
        [RetentionRule(table="manager_runs", timestamp_column="run_at", retention_days=0)]
    )

    assert result["status"] == "ok"
    assert result["tables"]["manager_runs"]["status"] == "skipped"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM manager_runs").fetchone()[0] == 1


def test_sqlite_retention_skips_missing_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "retention.db"

    result = SQLiteRetentionPruner(db_path).prune(
        [
            RetentionRule(
                table="quote_snapshots_archive",
                timestamp_column="__archive_archived_at",
                retention_days=30,
            )
        ]
    )

    assert result["status"] == "ok"
    assert result["tables"]["quote_snapshots_archive"] == {
        "status": "skipped",
        "reason": "table_missing",
    }


def test_sqlite_retention_vacuums_after_delete_when_requested(tmp_path: Path) -> None:
    db_path = tmp_path / "retention.db"
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    hot = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE raw_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO raw_snapshots (captured_at, raw_json) VALUES (?, ?)",
            (old, "x" * 1000),
        )
        conn.execute(
            "INSERT INTO raw_snapshots (captured_at, raw_json) VALUES (?, ?)",
            (hot, "y"),
        )

    result = SQLiteRetentionPruner(db_path).prune(
        [
            RetentionRule(
                table="raw_snapshots",
                timestamp_column="captured_at",
                retention_days=7,
                archive_table="raw_snapshots_archive",
                vacuum_after_delete=True,
            )
        ]
    )

    assert result["status"] == "ok"
    assert result["vacuumed"] is True
    assert result["tables"]["raw_snapshots"]["archived"] == 1
    assert result["tables"]["raw_snapshots"]["deleted"] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM raw_snapshots_archive").fetchone()[0]
            == 1
        )


def test_sqlite_retention_skips_vacuum_when_no_rows_deleted(tmp_path: Path) -> None:
    db_path = tmp_path / "retention.db"
    hot = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE raw_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO raw_snapshots (captured_at, raw_json) VALUES (?, ?)",
            (hot, "y"),
        )

    result = SQLiteRetentionPruner(db_path).prune(
        [
            RetentionRule(
                table="raw_snapshots",
                timestamp_column="captured_at",
                retention_days=7,
                vacuum_after_delete=True,
            )
        ]
    )

    assert result["status"] == "ok"
    assert result["vacuumed"] is False
    assert result["tables"]["raw_snapshots"]["deleted"] == 0


def test_sqlite_retention_compresses_archived_raw_snapshot_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "retention.db"
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    hot = datetime.now(timezone.utc).isoformat()
    raw_payload = '{"payload":"' + ("x" * 512) + '"}'
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE raw_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO raw_snapshots (captured_at, symbol, raw_json) VALUES (?, ?, ?)",
            (old, "OLDUSDT", raw_payload),
        )
        conn.execute(
            "INSERT INTO raw_snapshots (captured_at, symbol, raw_json) VALUES (?, ?, ?)",
            (hot, "HOTUSDT", '{"payload":"hot"}'),
        )

    result = SQLiteRetentionPruner(db_path).prune(
        [
            RetentionRule(
                table="raw_snapshots",
                timestamp_column="captured_at",
                retention_days=7,
                archive_table="raw_snapshots_archive",
                archive_compress_columns=("raw_json",),
            )
        ]
    )

    assert result["tables"]["raw_snapshots"]["archived"] == 1
    assert result["tables"]["raw_snapshots"]["compressed"] == 1
    assert result["tables"]["raw_snapshots"]["compressed_columns"] == ["raw_json"]
    with sqlite3.connect(db_path) as conn:
        hot_rows = conn.execute(
            "SELECT symbol, raw_json FROM raw_snapshots ORDER BY id"
        ).fetchall()
        archived = conn.execute(
            "SELECT symbol, raw_json FROM raw_snapshots_archive ORDER BY id"
        ).fetchone()
    assert hot_rows == [("HOTUSDT", '{"payload":"hot"}')]
    assert archived[0] == "OLDUSDT"
    assert archived[1].startswith("gzip+base64:")
    restored = gzip.decompress(
        base64.b64decode(archived[1].removeprefix("gzip+base64:"))
    ).decode("utf-8")
    assert restored == raw_payload


def test_sqlite_retention_compressed_archive_batches_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "retention.db"
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE raw_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO raw_snapshots (captured_at, symbol, raw_json) VALUES (?, ?, ?)",
            [(old, f"OLD{i}USDT", '{"payload":"' + ("x" * 128) + '"}') for i in range(5)],
        )

    result = SQLiteRetentionPruner(db_path).prune(
        [
            RetentionRule(
                table="raw_snapshots",
                timestamp_column="captured_at",
                retention_days=7,
                archive_table="raw_snapshots_archive",
                archive_compress_columns=("raw_json",),
                archive_batch_size=2,
            )
        ]
    )

    assert result["tables"]["raw_snapshots"]["archived"] == 5
    assert result["tables"]["raw_snapshots"]["archive_batch_size"] == 2
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM raw_snapshots_archive").fetchone()[0]
            == 5
        )


def test_sqlite_retention_records_archive_metadata_for_compressed_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "retention.db"
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE raw_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO raw_snapshots (captured_at, raw_json) VALUES (?, ?)",
            (old, '{"old": true}'),
        )

    result = SQLiteRetentionPruner(db_path).prune(
        [
            RetentionRule(
                table="raw_snapshots",
                timestamp_column="captured_at",
                retention_days=7,
                archive_table="raw_snapshots_archive",
                archive_compress_columns=("raw_json",),
            )
        ]
    )

    assert result["tables"]["raw_snapshots"]["archive_metadata_columns"] == [
        "__archive_archived_at",
        "__archive_compressed_columns_json",
    ]
    with sqlite3.connect(db_path) as conn:
        columns = [
            str(row[1])
            for row in conn.execute(
                'PRAGMA table_info("raw_snapshots_archive")'
            ).fetchall()
        ]
        archived_at, compressed_columns_json = conn.execute(
            """
            SELECT __archive_archived_at, __archive_compressed_columns_json
            FROM raw_snapshots_archive
            """
        ).fetchone()

    assert "__archive_archived_at" in columns
    assert "__archive_compressed_columns_json" in columns
    assert datetime.fromisoformat(archived_at)
    assert json.loads(compressed_columns_json) == ["raw_json"]


def test_sqlite_retention_compacts_existing_plain_archive_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "retention.db"
    already_compressed = "gzip+base64:" + base64.b64encode(
        gzip.compress(b'{"already": true}')
    ).decode("ascii")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE quote_snapshots_archive (
                id INTEGER PRIMARY KEY,
                fetched_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                __archive_compressed_columns_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO quote_snapshots_archive (
                id, fetched_at, raw_json, __archive_compressed_columns_json
            )
            VALUES (1, '2026-06-01T00:00:00+00:00', ?, '[]')
            """,
            ('{"plain":"' + ("x" * 512) + '"}',),
        )
        conn.execute(
            """
            INSERT INTO quote_snapshots_archive (
                id, fetched_at, raw_json, __archive_compressed_columns_json
            )
            VALUES (2, '2026-06-01T00:00:01+00:00', ?, '["raw_json"]')
            """,
            (already_compressed,),
        )

    result = SQLiteRetentionPruner(db_path).compact_archive_columns(
        table="quote_snapshots_archive",
        columns=("raw_json",),
        batch_size=100,
        vacuum=False,
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, raw_json, __archive_compressed_columns_json
            FROM quote_snapshots_archive
            ORDER BY id
            """
        ).fetchall()

    assert result["status"] == "ok"
    assert result["compacted"] == 1
    assert rows[0][1].startswith("gzip+base64:")
    assert json.loads(rows[0][2]) == ["raw_json"]
    restored = gzip.decompress(
        base64.b64decode(rows[0][1].removeprefix("gzip+base64:"))
    ).decode("utf-8")
    assert json.loads(restored)["plain"].startswith("xxx")
    assert rows[1][1] == already_compressed


def test_sqlite_retention_replaces_composite_pk_archive_duplicates(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "retention.db"
    old = int((datetime.now(timezone.utc) - timedelta(days=14)).timestamp() * 1000)
    raw_payload = {"source": "first"}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE klines (
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                close_time INTEGER NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (symbol, market, interval, open_time)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO klines (
                symbol, market, interval, open_time, close_time, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("BTCUSDT", "spot", "4h", old, old, json.dumps(raw_payload)),
        )

    pruner = SQLiteRetentionPruner(db_path)
    rule = RetentionRule(
        table="klines",
        timestamp_column="close_time",
        retention_days=7,
        timestamp_kind="unix_ms",
        archive_table="klines_archive",
        archive_compress_columns=("raw_json",),
    )
    first = pruner.prune([rule])

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO klines (
                symbol, market, interval, open_time, close_time, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "BTCUSDT",
                "spot",
                "4h",
                old,
                old,
                json.dumps({"source": "second"}),
            ),
        )
    second = pruner.prune([rule])

    assert first["tables"]["klines"]["archive_key_columns"] == [
        "symbol",
        "market",
        "interval",
        "open_time",
    ]
    assert second["tables"]["klines"]["archive_key_columns"] == [
        "symbol",
        "market",
        "interval",
        "open_time",
    ]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, market, interval, open_time, raw_json FROM klines_archive"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][:4] == ("BTCUSDT", "spot", "4h", old)
    restored = gzip.decompress(
        base64.b64decode(rows[0][4].removeprefix("gzip+base64:"))
    ).decode("utf-8")
    assert json.loads(restored) == {"source": "second"}
