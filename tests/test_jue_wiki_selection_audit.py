from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tradecraft.services.jue_wiki_selection_audit import JueWikiSelectionAuditStore


def _create_selection_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE wiki_selection_runs (
                run_id TEXT PRIMARY KEY,
                target_scope TEXT NOT NULL,
                request_json TEXT NOT NULL DEFAULT '{}',
                budget_report_json TEXT NOT NULL DEFAULT '{}',
                selected_count INTEGER NOT NULL DEFAULT 0,
                rejected_count INTEGER NOT NULL DEFAULT 0,
                char_count INTEGER NOT NULL DEFAULT 0,
                max_chars INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE wiki_selection_pages (
                run_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                score REAL NOT NULL,
                reasons_json TEXT NOT NULL DEFAULT '[]',
                penalties_json TEXT NOT NULL DEFAULT '[]',
                char_count INTEGER NOT NULL DEFAULT 0,
                included INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, page_id)
            )
            """
        )


def test_record_run_replaces_summary_and_page_rows(tmp_path: Path) -> None:
    database = tmp_path / "wiki.db"
    _create_selection_database(database)
    store = JueWikiSelectionAuditStore(database, tmp_path / "cold")
    with sqlite3.connect(database) as conn:
        store.record_run(
            conn,
            run_id="run-1",
            target_scope="kis",
            request={"symbols": ["005930"]},
            budget_report={"status": "ok"},
            selected_pages=[{"page_id": "included", "rank": 1, "score": 2.0}],
            rejected_pages=[{"page_id": "rejected", "reason": "budget"}],
            char_count=120,
            max_chars=200,
            status="ok",
            error_message="",
            created_at="2026-07-11T00:00:00Z",
        )

    with sqlite3.connect(database) as conn:
        summary = conn.execute(
            "SELECT selected_count, rejected_count FROM wiki_selection_runs"
        ).fetchone()
        pages = conn.execute(
            "SELECT page_id, included FROM wiki_selection_pages ORDER BY page_id"
        ).fetchall()
    assert summary == (1, 1)
    assert pages == [("included", 1), ("rejected", 0)]


def _insert_page(
    path: Path,
    *,
    run_id: str,
    page_id: str,
    included: int,
    created_at: str,
) -> dict[str, object]:
    row = {
        "run_id": run_id,
        "page_id": page_id,
        "rank": 1,
        "score": 0.25,
        "reasons_json": '["reason"]',
        "penalties_json": '["penalty"]',
        "char_count": 123,
        "included": included,
        "created_at": created_at,
    }
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_pages (
                run_id, page_id, rank, score, reasons_json, penalties_json,
                char_count, included, created_at
            ) VALUES (:run_id, :page_id, :rank, :score, :reasons_json,
                      :penalties_json, :char_count, :included, :created_at)
            """,
            row,
        )
    return row


def test_compaction_exports_only_old_rejected_rows(tmp_path: Path) -> None:
    database = tmp_path / "wiki.db"
    _create_selection_database(database)
    old_rejected = _insert_page(
        database,
        run_id="old-run",
        page_id="old-rejected",
        included=0,
        created_at="2026-07-09T00:00:00Z",
    )
    included = _insert_page(
        database,
        run_id="old-run",
        page_id="included",
        included=1,
        created_at="2026-07-09T00:00:00Z",
    )
    recent_rejected = _insert_page(
        database,
        run_id="recent-run",
        page_id="recent-rejected",
        included=0,
        created_at="2026-07-10T12:00:00Z",
    )
    store = JueWikiSelectionAuditStore(
        database,
        tmp_path / ".runtime-cold-archive",
    )

    result = store.compact_rejected(
        cutoff=datetime(2026, 7, 10, tzinfo=timezone.utc),
        apply=True,
    )

    assert result.exported_keys == (("old-run", "old-rejected"),)
    assert result.deleted_keys == result.exported_keys
    assert result.verified is True
    with sqlite3.connect(database) as conn:
        hot_keys = conn.execute(
            "SELECT run_id, page_id FROM wiki_selection_pages ORDER BY page_id"
        ).fetchall()
    assert hot_keys == [("old-run", "included"), ("recent-run", "recent-rejected")]
    assert store.historical_pages("old-run") == [included, old_rejected]
    assert store.historical_pages("recent-run") == [recent_rejected]
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        assert [
            row["page_id"] for row in store.included_pages(conn, "old-run")
        ] == ["included"]
    restored = store.restore_partition(result.entry_ids[0], tmp_path / "restored")
    assert restored.restored is True
    assert restored.row_count == 1
    assert restored.path.read_text(encoding="utf-8").count("old-rejected") == 1


def test_rows_newer_than_fixed_cutoff_are_never_deleted(tmp_path: Path) -> None:
    database = tmp_path / "wiki.db"
    _create_selection_database(database)
    _insert_page(
        database,
        run_id="old-run",
        page_id="old",
        included=0,
        created_at="2026-07-09T00:00:00Z",
    )
    store = JueWikiSelectionAuditStore(database, tmp_path / "cold")

    result = store.compact_rejected(
        cutoff=datetime(2026, 7, 10, tzinfo=timezone.utc),
        before_delete=lambda: _insert_page(
            database,
            run_id="new-run",
            page_id="new",
            included=0,
            created_at="2026-07-10T00:00:01Z",
        ),
        apply=True,
    )

    assert result.deleted_keys == (("old-run", "old"),)
    with sqlite3.connect(database) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_selection_pages WHERE page_id = 'new'"
        ).fetchone()[0] == 1


def test_compaction_dry_run_writes_neither_archive_nor_database(tmp_path: Path) -> None:
    database = tmp_path / "wiki.db"
    _create_selection_database(database)
    _insert_page(
        database,
        run_id="old-run",
        page_id="old",
        included=0,
        created_at="2026-07-09T00:00:00Z",
    )
    cold_root = tmp_path / "cold"
    store = JueWikiSelectionAuditStore(database, cold_root)

    result = store.compact_rejected(
        cutoff=datetime(2026, 7, 10, tzinfo=timezone.utc),
        apply=False,
    )

    assert result.exported_keys == (("old-run", "old"),)
    assert result.deleted_keys == ()
    assert not cold_root.exists()
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wiki_selection_pages").fetchone()[0] == 1


def test_status_reports_corrupt_selection_partition(tmp_path: Path) -> None:
    database = tmp_path / "wiki.db"
    _create_selection_database(database)
    _insert_page(
        database,
        run_id="old-run",
        page_id="old",
        included=0,
        created_at="2026-07-09T00:00:00Z",
    )
    store = JueWikiSelectionAuditStore(database, tmp_path / "cold")
    result = store.compact_rejected(
        cutoff=datetime(2026, 7, 10, tzinfo=timezone.utc),
        apply=True,
    )
    entry = next(
        row
        for row in store._manifest()["entries"]
        if row["entry_id"] == result.entry_ids[0]
    )
    (store.cold_root / entry["archive_path"]).write_bytes(b"corrupt")

    status = store.status()

    assert status["status"] == "warning"
    assert status["corrupt_entry_ids"] == [result.entry_ids[0]]


def test_vacuum_keeps_verified_pre_compaction_backup(tmp_path: Path) -> None:
    database = tmp_path / "wiki.db"
    _create_selection_database(database)
    for index in range(100):
        _insert_page(
            database,
            run_id="old-run",
            page_id=f"old-{index}",
            included=0,
            created_at="2026-07-09T00:00:00Z",
        )
    cold_root = tmp_path / "cold"
    store = JueWikiSelectionAuditStore(database, cold_root)
    store.compact_rejected(
        cutoff=datetime(2026, 7, 10, tzinfo=timezone.utc),
        apply=True,
    )

    result = store.vacuum_hot_database(min_free_bytes=0)

    assert result["status"] == "ok"
    assert result["backup_verified"] is True
    assert result["after_bytes"] <= result["before_bytes"]
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_large_dry_run_reports_count_without_materializing_every_key(
    tmp_path: Path,
) -> None:
    database = tmp_path / "wiki.db"
    _create_selection_database(database)
    rows = [
        (
            "old-run",
            f"page-{index:05d}",
            0,
            0.0,
            "[]",
            "[]",
            0,
            0,
            "2026-07-09T00:00:00Z",
        )
        for index in range(2_000)
    ]
    with sqlite3.connect(database) as conn:
        conn.executemany(
            """
            INSERT INTO wiki_selection_pages (
                run_id, page_id, rank, score, reasons_json, penalties_json,
                char_count, included, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    store = JueWikiSelectionAuditStore(database, tmp_path / "cold")

    result = store.compact_rejected(
        cutoff=datetime(2026, 7, 10, tzinfo=timezone.utc),
        apply=False,
    )

    assert result.exported_count == 2_000
    assert len(result.exported_keys) <= 100
    assert not store.cold_root.exists()
