from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
import gzip
from pathlib import Path

from tradecraft.services import runtime_maintenance
from tradecraft.services.runtime_maintenance import (
    RuntimeStoragePolicy,
    build_runtime_storage_report,
    cleanup_runtime_storage,
)
from tradecraft.services.runtime_storage_policy import (
    runtime_storage_policy_from_settings,
)
from tradecraft.config import AppSettings
from tradecraft.services.runtime_cold_archive import RuntimeColdArchiveV1


def test_runtime_storage_report_and_cleanup_prunes_unreferenced_pdfs(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    pdf_dir = runtime_dir / "naver_reports" / "pdfs"
    pdf_dir.mkdir(parents=True)
    db_path = runtime_dir / "naver_reports.db"
    keep_pdf = pdf_dir / "keep.pdf"
    stale_pdf = pdf_dir / "stale.pdf"
    keep_pdf.write_bytes(b"a" * 10)
    stale_pdf.write_bytes(b"b" * 20)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE reports (pdf_archived_path TEXT)")
        conn.execute(
            "INSERT INTO reports (pdf_archived_path) VALUES (?)",
            (str(keep_pdf),),
        )

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        cold_archive_root=str(tmp_path / ".runtime-cold-archive"),
        reports_db_path=str(db_path),
        pdf_archive_dir=str(pdf_dir),
        large_file_threshold_mb=1,
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["unreferenced_report_pdfs"]
    assert candidates["count"] == 1
    assert candidates["bytes"] == 20
    assert candidates["size_mb"] == 0.0
    assert report["cleanup_candidate_count"] == 1
    assert report["cleanup_candidate_bytes"] == 20

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_count"] == 1
    assert dry_run["would_delete_count"] == 1
    assert dry_run["actual_deleted_count"] == 0
    assert dry_run["deleted_size_mb"] == 0.0
    assert stale_pdf.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_count"] == 1
    assert result["would_delete_count"] == 0
    assert result["actual_deleted_count"] == 1
    assert result["deleted_size_mb"] == 0.0
    assert keep_pdf.exists()
    assert not stale_pdf.exists()


def test_runtime_storage_policy_is_built_outside_main_from_read_only_settings() -> None:
    settings = AppSettings()
    policy = runtime_storage_policy_from_settings(settings)
    main_source = (Path(__file__).resolve().parents[1] / "src/tradecraft/main.py").read_text(
        encoding="utf-8"
    )

    assert policy.runtime_log_retention_days == 7
    assert policy.dryrun_artifact_retention_days == 14
    assert policy.dryrun_recent_per_scenario == 3
    assert policy.cold_archive_root == settings.runtime_cold_archive_root
    assert policy.archive_dryrun_artifacts is True
    assert policy.dryrun_hot_hours == 24
    assert '"quote_snapshots_archive": (' not in main_source


def test_runtime_storage_report_exposes_operational_summary_counts(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    small_file = runtime_dir / "small.log"
    large_file = runtime_dir / "large.db"
    small_file.write_bytes(b"a" * 128)
    large_file.write_bytes(b"b" * (2 * 1024 * 1024))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        reports_db_path=str(runtime_dir / "missing_reports.db"),
        pdf_archive_dir=str(runtime_dir / "missing_pdfs"),
        rag_persist_path=str(runtime_dir / "missing_rag"),
        large_file_threshold_mb=1,
        prune_unreferenced_pdfs=False,
        prune_scratch_artifacts=False,
    )

    report = build_runtime_storage_report(policy)

    assert report["total_bytes"] == small_file.stat().st_size + large_file.stat().st_size
    assert report["total_size_mb"] == 2.0
    assert report["total_human"] == "2.0 MB"
    assert report["top_level"][0]["size_mb"] == 2.0
    assert report["large_files"][0]["size_mb"] == 2.0
    assert report["large_file_count"] == 1
    assert report["cleanup_candidate_count"] == 0
    assert report["cleanup_candidate_size_mb"] == 0.0
    assert report["cleanup_candidate_human"] == "0 B"


def test_runtime_storage_report_includes_report_and_rag_database_summaries(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    reports_db = runtime_dir / "naver_reports.db"
    rag_dir = runtime_dir / "rag_chroma"
    rag_db = rag_dir / "chroma.sqlite3"
    runtime_dir.mkdir(parents=True)
    rag_dir.mkdir(parents=True)

    with sqlite3.connect(str(reports_db)) as conn:
        conn.execute("CREATE TABLE reports (report_id INTEGER, content TEXT)")
        conn.execute("CREATE TABLE report_chunks (chunk_id INTEGER, content TEXT)")
        conn.execute("CREATE TABLE report_facts (fact_id INTEGER)")
        conn.execute("CREATE TABLE report_symbol_links (id INTEGER)")
        conn.execute("CREATE TABLE symbol_directory (symbol TEXT)")
        conn.execute("INSERT INTO reports VALUES (1, ?)", ("report text",))
        conn.executemany(
            "INSERT INTO report_chunks VALUES (?, ?)",
            [(1, "chunk one"), (2, "chunk two")],
        )
        conn.execute("INSERT INTO report_facts VALUES (1)")
        conn.execute("INSERT INTO report_symbol_links VALUES (1)")
        conn.execute("INSERT INTO symbol_directory VALUES ('005930')")

    with sqlite3.connect(str(rag_db)) as conn:
        conn.execute("CREATE TABLE collections (id TEXT)")
        conn.execute("CREATE TABLE embeddings (id TEXT, embedding_id TEXT)")
        conn.execute(
            """
            CREATE TABLE embeddings_queue (
                seq_id INTEGER PRIMARY KEY,
                created_at TIMESTAMP,
                operation INTEGER,
                topic TEXT,
                id TEXT,
                vector BLOB,
                encoding TEXT,
                metadata TEXT
            )
            """
        )
        conn.execute("CREATE TABLE embedding_metadata (id TEXT, key TEXT, string_value TEXT)")
        conn.execute("CREATE TABLE embedding_fulltext_search_content (id TEXT, c0 TEXT)")
        conn.executemany(
            "INSERT INTO embeddings VALUES (?, ?)",
            [("a", "doc-a"), ("b", "doc-b")],
        )
        conn.execute(
            "INSERT INTO embeddings_queue VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                10,
                "2026-06-30 02:58:28",
                1,
                "persistent://default/default/test-topic",
                "q",
                b"vec",
                "float32",
                '{"k":"v"}',
            ),
        )
        conn.executemany(
            "INSERT INTO embedding_metadata VALUES (?, ?, ?)",
            [
                ("m1", "chroma:document", "document one"),
                ("m2", "title", "Title A"),
                ("m3", "chroma:document", "document two"),
            ],
        )
        conn.executemany(
            "INSERT INTO embedding_fulltext_search_content VALUES (?, ?)",
            [("f1", "document one"), ("f2", "document two")],
        )

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        reports_db_path=str(reports_db),
        rag_persist_path=str(rag_dir),
        prune_unreferenced_pdfs=False,
    )

    report = build_runtime_storage_report(policy)
    summaries = report["database_summaries"]

    assert summaries["naver_reports"]["tables"]["reports"] == 1
    assert summaries["naver_reports"]["tables"]["report_chunks"] == 2
    assert summaries["naver_reports"]["tables"]["report_facts"] == 1
    assert summaries["naver_reports"]["tables"]["report_symbol_links"] == 1
    assert summaries["naver_reports"]["tables"]["symbol_directory"] == 1
    assert summaries["naver_reports"]["content_bytes"]["reports.content"] == len("report text")
    assert summaries["naver_reports"]["content_bytes"]["report_chunks.content"] == (
        len("chunk one") + len("chunk two")
    )
    assert summaries["rag_chroma"]["tables"]["embeddings"] == 2
    assert summaries["rag_chroma"]["tables"]["embeddings_queue"] == 1
    assert summaries["rag_chroma"]["tables"]["embedding_metadata"] == 3
    assert summaries["rag_chroma"]["content_bytes"]["embedding_metadata.string_value"] == (
        len("document one") + len("Title A") + len("document two")
    )
    assert summaries["rag_chroma"]["content_bytes"][
        "embedding_fulltext_search_content.c0"
    ] == (len("document one") + len("document two"))
    assert summaries["rag_chroma"]["diagnostics"]["embedding_count"] == 2
    assert summaries["rag_chroma"]["diagnostics"]["duplicate_embedding_ids"] == 0
    assert summaries["rag_chroma"]["diagnostics"]["document_metadata_bytes"] == (
        len("document one") + len("document two")
    )
    assert summaries["rag_chroma"]["diagnostics"]["metadata_key_bytes"][0] == {
        "key": "chroma:document",
        "rows": 2,
        "bytes": len("document one") + len("document two"),
    }
    assert summaries["rag_chroma"]["diagnostics"]["queue"]["metadata_bytes"] == len(
        '{"k":"v"}'
    )
    assert summaries["rag_chroma"]["diagnostics"]["queue"]["min_seq_id"] == 10
    assert summaries["rag_chroma"]["diagnostics"]["queue"]["max_seq_id"] == 10
    assert summaries["rag_chroma"]["diagnostics"]["queue"]["oldest_created_at"] == (
        "2026-06-30 02:58:28"
    )
    assert summaries["rag_chroma"]["diagnostics"]["queue"]["newest_created_at"] == (
        "2026-06-30 02:58:28"
    )
    assert summaries["rag_chroma"]["diagnostics"]["queue"]["topic_counts"] == [
        {"topic": "persistent://default/default/test-topic", "rows": 1}
    ]
    assert summaries["rag_chroma"]["diagnostics"]["queue"]["operation_counts"] == [
        {"operation": 1, "rows": 1}
    ]
    assert summaries["rag_chroma"]["free_bytes"] >= 0


def test_runtime_storage_report_includes_operational_database_summaries(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    kis_db = runtime_dir / "kis_blocks.db"
    binance_db = runtime_dir / "binance_blocks.db"

    with sqlite3.connect(str(kis_db)) as conn:
        conn.execute("CREATE TABLE quote_snapshots (id INTEGER)")
        conn.execute("CREATE TABLE blocks (block_id TEXT)")
        conn.execute("CREATE TABLE autoincrement_probe (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        conn.executemany("INSERT INTO quote_snapshots VALUES (?)", [(1,), (2,), (3,)])
        conn.execute("INSERT INTO blocks VALUES ('b1')")
        conn.execute("INSERT INTO autoincrement_probe DEFAULT VALUES")

    with sqlite3.connect(str(binance_db)) as conn:
        conn.execute("CREATE TABLE quote_snapshots_archive (id INTEGER)")
        conn.execute("CREATE TABLE manager_runs (id INTEGER)")
        conn.executemany("INSERT INTO quote_snapshots_archive VALUES (?)", [(1,), (2,)])
        conn.execute("INSERT INTO manager_runs VALUES (1)")

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        reports_db_path=str(runtime_dir / "missing_reports.db"),
        rag_persist_path=str(runtime_dir / "missing_rag"),
        operational_db_paths=(str(kis_db), str(binance_db)),
        prune_unreferenced_pdfs=False,
    )

    report = build_runtime_storage_report(policy)
    operational = report["database_summaries"]["operational"]

    assert operational["kis_blocks"]["status"] == "ok"
    assert operational["kis_blocks"]["tables"]["quote_snapshots"] == 3
    assert operational["kis_blocks"]["tables"]["blocks"] == 1
    assert "sqlite_sequence" not in operational["kis_blocks"]["tables"]
    assert operational["kis_blocks"]["free_bytes"] >= 0
    assert operational["binance_blocks"]["tables"]["quote_snapshots_archive"] == 2
    assert operational["binance_blocks"]["tables"]["manager_runs"] == 1


def test_runtime_storage_report_surfaces_database_growth_pressure(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    operational_db = runtime_dir / "crypto_market_research.db"
    reports_db = runtime_dir / "naver_reports.db"

    with sqlite3.connect(str(operational_db)) as conn:
        conn.execute("CREATE TABLE crypto_klines (id INTEGER)")
        conn.execute("CREATE TABLE crypto_klines_archive (id INTEGER)")
        conn.executemany("INSERT INTO crypto_klines VALUES (?)", [(idx,) for idx in range(3)])
        conn.executemany(
            "INSERT INTO crypto_klines_archive VALUES (?)",
            [(idx,) for idx in range(7)],
        )

    with sqlite3.connect(str(reports_db)) as conn:
        conn.execute("CREATE TABLE reports (report_id INTEGER, content TEXT)")
        conn.execute("CREATE TABLE report_chunks (chunk_id INTEGER, content TEXT)")
        conn.execute("CREATE TABLE report_facts (fact_id INTEGER)")
        conn.execute("CREATE TABLE report_symbol_links (id INTEGER)")
        conn.execute("CREATE TABLE symbol_directory (symbol TEXT)")
        conn.executemany(
            "INSERT INTO reports VALUES (?, ?)",
            [(idx, "x" * 4096) for idx in range(4)],
        )

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        reports_db_path=str(reports_db),
        rag_persist_path=str(runtime_dir / "missing_rag"),
        operational_db_paths=(str(operational_db),),
        prune_unreferenced_pdfs=False,
    )

    report = build_runtime_storage_report(policy)
    pressure = report["database_growth_pressure"]

    assert report["database_growth_pressure_count"] >= 1
    assert report["database_growth_pressure_bytes"] >= os.path.getsize(operational_db)
    assert report["database_growth_pressure_size_mb"] >= 0.0
    assert report["database_growth_pressure_archive_rows"] == 7
    assert "database_growth_pressure_text_payload_bytes" in report
    assert report["database_growth_pressure_text_payload_bytes"] >= 0
    by_key = {row["key"]: row for row in pressure}
    assert by_key["crypto_market_research"]["archive_rows"] == 7
    assert by_key["crypto_market_research"]["archive_ratio_pct"] == 70.0
    assert by_key["crypto_market_research"]["largest_tables"][0] == {
        "table": "crypto_klines_archive",
        "rows": 7,
    }
    assert "archive_tables" in by_key["crypto_market_research"]["reasons"]
    assert by_key["crypto_market_research"]["action"] == "review_archive_retention"
    assert by_key["crypto_market_research"]["reclaimability"] == "policy"


def test_database_growth_pressure_actions_distinguish_corpus_and_rag_queue() -> None:
    summaries = {
        "naver_reports": {
            "status": "ok",
            "path": "naver_reports.db",
            "bytes": 160 * 1024 * 1024,
            "free_bytes": 0,
            "tables": {"reports": 10},
            "content_bytes": {"reports.content": 120 * 1024 * 1024},
        },
        "rag_chroma": {
            "status": "ok",
            "path": "chroma.sqlite3",
            "bytes": 160 * 1024 * 1024,
            "free_bytes": 0,
            "tables": {"embeddings": 10},
            "content_bytes": {},
            "diagnostics": {"queue": {"rows": 1}},
        },
    }

    by_key = {
        row["key"]: row
        for row in runtime_maintenance._database_growth_pressure(summaries)
    }

    assert by_key["naver_reports"]["action"] == (
        "preserve_corpus_prune_external_artifacts"
    )
    assert by_key["naver_reports"]["reclaimability"] == "low"
    assert by_key["rag_chroma"]["action"] == "finish_rag_sync"
    assert by_key["rag_chroma"]["action_label"] == "RAG sync/queue 점검"


def test_database_growth_pressure_marks_archive_retention_status() -> None:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    recent = (now - timedelta(days=2)).isoformat()
    old = (now - timedelta(days=10)).isoformat()
    older_manager = (now - timedelta(days=18)).isoformat()
    summaries = {
        "operational": {
            "kis_blocks": {
                "status": "ok",
                "path": "kis_blocks.db",
                "bytes": 160 * 1024 * 1024,
                "free_bytes": 0,
                "tables": {
                    "quote_snapshots_archive": 2,
                    "manager_runs_archive": 2,
                },
                "content_bytes": {},
                "diagnostics": {
                    "table_ranges": {
                        "quote_snapshots_archive": {"fetched_at": [recent, recent]},
                        "manager_runs_archive": {
                            "run_at": [older_manager, older_manager]
                        },
                    }
                },
            },
            "binance_blocks": {
                "status": "ok",
                "path": "binance_blocks.db",
                "bytes": 160 * 1024 * 1024,
                "free_bytes": 0,
                "tables": {"quote_snapshots_archive": 2},
                "content_bytes": {},
                "diagnostics": {
                    "table_ranges": {
                        "quote_snapshots_archive": {"fetched_at": [old, recent]}
                    }
                },
            },
        }
    }

    by_key = {
        row["key"]: row
        for row in runtime_maintenance._database_growth_pressure(
            summaries,
            archive_retention_days_by_key={
                "kis_blocks": {
                    "quote_snapshots_archive": 10,
                    "manager_runs_archive": 21,
                },
                "binance_blocks": {"quote_snapshots_archive": 7},
            },
            now=now,
        )
    }

    assert by_key["kis_blocks"]["archive_retention_status"] == "within_retention"
    assert by_key["kis_blocks"]["archive_retention_days"] == 10
    kis_tables = {
        row["table"]: row for row in by_key["kis_blocks"]["archive_retention_tables"]
    }
    assert kis_tables["manager_runs_archive"]["retention_days"] == 21
    assert kis_tables["manager_runs_archive"]["status"] == "within_retention"
    assert by_key["binance_blocks"]["archive_retention_status"] == "overdue"
    assert by_key["binance_blocks"]["archive_overdue_days"] == 3.0


def test_database_growth_pressure_uses_judgment_archive_effective_retention() -> None:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    judgment_old_but_retained = (now - timedelta(days=45)).isoformat()
    summaries = {
        "operational": {
            "market_judgment": {
                "status": "ok",
                "path": "market_judgment.db",
                "bytes": 160 * 1024 * 1024,
                "free_bytes": 0,
                "tables": {
                    "judgment_runs_archive": 2,
                    "symbol_judgments_archive": 8,
                },
                "content_bytes": {},
                "diagnostics": {
                    "table_ranges": {
                        "judgment_runs_archive": {
                            "run_at": [
                                judgment_old_but_retained,
                                judgment_old_but_retained,
                            ]
                        }
                    }
                },
            }
        }
    }

    rows = runtime_maintenance._database_growth_pressure(
        summaries,
        archive_retention_days_by_key={
            "market_judgment": {
                "quote_snapshots_archive": 7,
                "judgment_runs_archive": 60,
                "symbol_judgments_archive": 60,
            }
        },
        now=now,
    )

    row = rows[0]
    assert row["key"] == "market_judgment"
    assert row["archive_retention_status"] == "within_retention"
    tables = {item["table"]: item for item in row["archive_retention_tables"]}
    assert tables["judgment_runs_archive"]["retention_days"] == 60
    assert tables["symbol_judgments_archive"]["retention_days"] == 60
    assert tables["symbol_judgments_archive"]["oldest_at"] == judgment_old_but_retained


def test_database_growth_pressure_marks_small_archive_drift_as_cleanup_due() -> None:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    just_expired = (now - timedelta(days=7, hours=2)).isoformat()
    summaries = {
        "operational": {
            "market_pulse": {
                "status": "ok",
                "path": "market_pulse.db",
                "bytes": 160 * 1024 * 1024,
                "free_bytes": 0,
                "tables": {"market_pulse_snapshots_archive": 10},
                "content_bytes": {},
                "diagnostics": {
                    "table_ranges": {
                        "market_pulse_snapshots_archive": {
                            "captured_at": [just_expired, just_expired]
                        }
                    }
                },
            }
        }
    }

    row = runtime_maintenance._database_growth_pressure(
        summaries,
        archive_retention_days_by_key={"market_pulse": 7},
        now=now,
    )[0]

    assert row["archive_retention_status"] == "cleanup_due"
    assert row["archive_overdue_days"] == 0.08
    assert row["archive_retention_tables"][0]["status"] == "cleanup_due"


def test_runtime_storage_report_flags_database_compact_candidates(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    bloated_db = runtime_dir / "live_performance.db"
    clean_db = runtime_dir / "kis_blocks.db"

    with sqlite3.connect(str(bloated_db)) as conn:
        conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.executemany(
            "INSERT INTO samples (payload) VALUES (?)",
            [("x" * 2048,) for _ in range(100)],
        )
        conn.execute("DELETE FROM samples")

    with sqlite3.connect(str(clean_db)) as conn:
        conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.executemany(
            "INSERT INTO samples (payload) VALUES (?)",
            [("x" * 128,) for _ in range(10)],
        )

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        reports_db_path=str(runtime_dir / "missing_reports.db"),
        rag_persist_path=str(runtime_dir / "missing_rag"),
        operational_db_paths=(str(bloated_db), str(clean_db)),
        database_compact_min_free_mb=0,
        database_compact_min_free_ratio_pct=10.0,
        prune_unreferenced_pdfs=False,
    )

    report = build_runtime_storage_report(policy)
    candidates = report["database_compact_candidates"]

    assert candidates
    assert candidates[0]["key"] == "live_performance"
    assert candidates[0]["group"] == "operational"
    assert candidates[0]["free_bytes"] > 0
    assert candidates[0]["free_ratio_pct"] >= 10.0
    assert "kis_blocks" not in {row["key"] for row in candidates}
    assert report["policy"]["database_compact_min_free_mb"] == 0
    assert report["policy"]["database_compact_min_free_ratio_pct"] == 10.0


def test_runtime_storage_cleanup_can_compact_database_candidates(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    bloated_db = runtime_dir / "live_performance.db"

    with sqlite3.connect(str(bloated_db)) as conn:
        conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.executemany(
            "INSERT INTO samples (payload) VALUES (?)",
            [("x" * 4096,) for _ in range(200)],
        )
        conn.execute("DELETE FROM samples")

    before_size = bloated_db.stat().st_size
    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        reports_db_path=str(runtime_dir / "missing_reports.db"),
        rag_persist_path=str(runtime_dir / "missing_rag"),
        operational_db_paths=(str(bloated_db),),
        database_compact_min_free_mb=0,
        database_compact_min_free_ratio_pct=10.0,
        prune_unreferenced_pdfs=False,
    )

    dry_run = cleanup_runtime_storage(
        policy,
        dry_run=True,
        compact_databases=True,
    )

    assert dry_run["database_compaction"]["enabled"] is True
    assert dry_run["database_compaction"]["candidate_count"] == 1
    assert dry_run["database_compaction"]["compacted_count"] == 0
    assert bloated_db.stat().st_size == before_size

    result = cleanup_runtime_storage(
        policy,
        dry_run=False,
        compact_databases=True,
    )

    compaction = result["database_compaction"]
    assert compaction["enabled"] is True
    assert compaction["candidate_count"] == 1
    assert compaction["compacted_count"] == 1
    assert compaction["error_count"] == 0
    assert compaction["results"][0]["status"] == "ok"
    assert compaction["results"][0]["before_free_bytes"] > 0
    assert compaction["results"][0]["after_bytes"] < compaction["results"][0]["before_bytes"]
    assert bloated_db.stat().st_size < before_size


def test_runtime_tree_snapshot_reuses_single_walk_for_size_report(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    nested_dir = runtime_dir / "nested"
    empty_dir = runtime_dir / "empty"
    nested_dir.mkdir(parents=True)
    empty_dir.mkdir()
    root_file = runtime_dir / "root.log"
    nested_file = nested_dir / "large.bin"
    root_file.write_bytes(b"a" * 5)
    nested_file.write_bytes(b"b" * 20)

    original_rglob = Path.rglob
    calls: list[tuple[str, str]] = []

    def counted_rglob(self: Path, pattern: str):  # type: ignore[no-untyped-def]
        if self == runtime_dir:
            calls.append((str(self), pattern))
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", counted_rglob)

    snapshot = runtime_maintenance._runtime_tree_snapshot(
        runtime_dir,
        threshold_bytes=10,
    )

    assert snapshot["total_bytes"] == 25
    assert snapshot["top_level"] == [
        {"path": str(nested_dir), "bytes": 20, "kind": "dir"},
        {"path": str(root_file), "bytes": 5, "kind": "file"},
        {"path": str(empty_dir), "bytes": 0, "kind": "dir"},
    ]
    assert snapshot["large_files"] == [{"path": str(nested_file), "bytes": 20}]
    assert calls == [(str(runtime_dir), "*")]


def test_runtime_storage_cleanup_can_prune_old_extracted_report_pdfs(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    pdf_dir = runtime_dir / "naver_reports" / "pdfs"
    pdf_dir.mkdir(parents=True)
    db_path = runtime_dir / "naver_reports.db"
    old_pdf = pdf_dir / "old-extracted.pdf"
    recent_pdf = pdf_dir / "recent-extracted.pdf"
    no_content_pdf = pdf_dir / "old-no-content.pdf"
    old_pdf.write_bytes(b"a" * 100)
    recent_pdf.write_bytes(b"b" * 200)
    no_content_pdf.write_bytes(b"c" * 300)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE reports (
                pdf_archived_path TEXT,
                content TEXT,
                content_source TEXT,
                crawled_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO reports (
                pdf_archived_path, content, content_source, crawled_at
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (str(old_pdf), "already extracted", "pdf_extract", "2026-01-01T00:00:00+00:00"),
                (str(recent_pdf), "recent text", "pdf_extract", "2026-06-10T00:00:00+00:00"),
                (str(no_content_pdf), "", "pdf_extract", "2026-01-01T00:00:00+00:00"),
            ],
        )

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        cold_archive_root=str(tmp_path / ".runtime-cold-archive"),
        reports_db_path=str(db_path),
        pdf_archive_dir=str(pdf_dir),
        large_file_threshold_mb=1,
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=True,
        extracted_report_pdf_retention_days=30,
        now_iso="2026-06-14T00:00:00+00:00",
    )
    assert Path(policy.cold_archive_root) == tmp_path / ".runtime-cold-archive"

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["extracted_report_pdfs"]
    assert candidates["count"] == 1
    assert candidates["bytes"] == 100
    assert candidates["sample"] == [str(old_pdf)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_count"] == 1
    assert dry_run["deleted_by_category"]["extracted_report_pdfs"]["count"] == 1
    assert old_pdf.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_count"] == 1
    archives = [
        row
        for row in result["archived"]
        if row["category"] == "extracted_report_pdfs"
    ]
    assert len(archives) == 1
    assert archives[0]["verified"] is True
    assert not old_pdf.exists()
    assert recent_pdf.exists()
    assert no_content_pdf.exists()
    restored = RuntimeColdArchiveV1(policy.cold_archive_root).restore(
        archives[0]["entry_id"],
        tmp_path / "restored-pdfs",
    )
    assert restored.restored is True
    assert restored.paths[0].read_bytes() == b"a" * 100


def test_runtime_storage_cleanup_can_prune_old_rag_repair_artifacts(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    active_rag = runtime_dir / "rag_chroma"
    corrupt_dir = runtime_dir / "rag_chroma_corrupt_20260621_161059"
    active_rag.mkdir(parents=True)
    corrupt_dir.mkdir(parents=True)
    active_db = active_rag / "chroma.sqlite3"
    legacy_backup = active_rag / "chroma.sqlite3.legacy-config.bak"
    corrupt_db = corrupt_dir / "chroma.sqlite3"
    active_db.write_bytes(b"active" * 10)
    legacy_backup.write_bytes(b"backup" * 20)
    corrupt_db.write_bytes(b"corrupt" * 30)

    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    os.utime(legacy_backup, (old_ts, old_ts))
    os.utime(corrupt_db, (old_ts, old_ts))
    os.utime(corrupt_dir, (old_ts, old_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        rag_persist_path=str(active_rag),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=True,
        rag_repair_artifact_retention_days=7,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["rag_repair_artifacts"]
    assert candidates["count"] == 2
    assert candidates["bytes"] == legacy_backup.stat().st_size + corrupt_db.stat().st_size
    assert str(active_db) not in candidates["sample"]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["rag_repair_artifacts"]["count"] == 2
    assert legacy_backup.exists()
    assert corrupt_dir.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["rag_repair_artifacts"]["count"] == 2
    assert active_db.exists()
    assert not legacy_backup.exists()
    assert not corrupt_dir.exists()


def test_runtime_storage_cleanup_can_prune_old_rag_rebuild_backups(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    active_rag = runtime_dir / "rag_chroma"
    old_backup = runtime_dir / "rag_chroma.rebuild-backup-20260601T000000Z"
    recent_backup = runtime_dir / "rag_chroma.rebuild-backup-20260613T000000Z"
    active_rag.mkdir(parents=True)
    old_backup.mkdir(parents=True)
    recent_backup.mkdir(parents=True)
    active_db = active_rag / "chroma.sqlite3"
    old_backup_db = old_backup / "chroma.sqlite3"
    recent_backup_db = recent_backup / "chroma.sqlite3"
    for database, value in (
        (active_db, "active"),
        (old_backup_db, "old backup"),
        (recent_backup_db, "recent backup"),
    ):
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE documents (value TEXT)")
            conn.execute("INSERT INTO documents (value) VALUES (?)", (value,))

    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 13, tzinfo=timezone.utc).timestamp()
    os.utime(old_backup_db, (old_ts, old_ts))
    os.utime(old_backup, (old_ts, old_ts))
    os.utime(recent_backup_db, (recent_ts, recent_ts))
    os.utime(recent_backup, (recent_ts, recent_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        cold_archive_root=str(tmp_path / ".runtime-cold-archive"),
        rag_persist_path=str(active_rag),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_rag_rebuild_backups=True,
        rag_rebuild_backup_retention_days=7,
        prune_old_runtime_logs=False,
        prune_repair_backup_artifacts=False,
        prune_scratch_artifacts=False,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["rag_rebuild_backups"]
    retained = report["retained_artifacts"]["rag_rebuild_backups"]
    assert candidates["count"] == 1
    assert candidates["bytes"] == old_backup_db.stat().st_size
    assert candidates["sample"] == [str(old_backup)]
    assert retained["count"] == 1
    assert retained["sample"] == [str(recent_backup)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["rag_rebuild_backups"]["count"] == 1
    assert old_backup.exists()
    assert recent_backup.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["rag_rebuild_backups"]["count"] == 1
    rag_archives = [
        row for row in result["archived"] if row["category"] == "rag_rebuild_backups"
    ]
    assert len(rag_archives) == 1
    assert rag_archives[0]["verified"] is True
    assert active_db.exists()
    assert not old_backup.exists()
    assert recent_backup.exists()
    restored = RuntimeColdArchiveV1(policy.cold_archive_root).restore(
        rag_archives[0]["entry_id"],
        tmp_path / "restored-rag",
    )
    assert restored.restored is True
    with sqlite3.connect(restored.paths[0]) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT value FROM documents").fetchone()[0] == "old backup"


def test_runtime_storage_cleanup_can_prune_old_runtime_logs(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    nested_log_dir = runtime_dir / "logs"
    nested_log_dir.mkdir(parents=True)
    old_root_log = runtime_dir / "old-runner.log"
    old_nested_log = nested_log_dir / "old-runtime.log"
    old_rotated_log = nested_log_dir / "runtime.log.1"
    recent_log = runtime_dir / "recent.log"
    old_root_log.write_bytes(b"a" * 100)
    old_nested_log.write_bytes(b"b" * 200)
    old_rotated_log.write_bytes(b"d" * 400)
    recent_log.write_bytes(b"c" * 300)

    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 14, tzinfo=timezone.utc).timestamp()
    os.utime(old_root_log, (old_ts, old_ts))
    os.utime(old_nested_log, (old_ts, old_ts))
    os.utime(old_rotated_log, (old_ts, old_ts))
    os.utime(recent_log, (recent_ts, recent_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=True,
        runtime_log_retention_days=7,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["old_runtime_logs"]
    assert candidates["count"] == 3
    assert candidates["bytes"] == 700
    assert candidates["sample"] == [
        str(old_nested_log),
        str(old_rotated_log),
        str(old_root_log),
    ]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["old_runtime_logs"]["count"] == 3
    assert old_root_log.exists()
    assert old_nested_log.exists()
    assert old_rotated_log.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["old_runtime_logs"]["bytes"] == 700
    assert not old_root_log.exists()
    assert not old_nested_log.exists()
    assert not old_rotated_log.exists()
    assert recent_log.exists()


def test_runtime_storage_cleanup_rotates_large_active_runtime_logs(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    log_dir = runtime_dir / "logs"
    log_dir.mkdir(parents=True)
    active_log = log_dir / "runtime.log"
    old_log = log_dir / "old.log"
    active_log.write_bytes(b"a" * 900 + b"TAIL")
    old_log.write_bytes(b"old" * 100)

    recent_ts = datetime(2026, 6, 14, tzinfo=timezone.utc).timestamp()
    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    os.utime(active_log, (recent_ts, recent_ts))
    os.utime(old_log, (old_ts, old_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        runtime_log_retention_days=7,
        active_log_max_mb=1,
        active_log_tail_kb=1,
        now_iso="2026-06-14T00:00:00+00:00",
    )
    active_log.write_bytes(b"b" * ((1024 * 1024) + 128) + b"TAIL")
    os.utime(active_log, (recent_ts, recent_ts))

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["large_active_runtime_logs"]
    assert candidates["count"] == 1
    assert candidates["sample"] == [str(active_log)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["rotated_by_category"]["large_active_runtime_logs"]["count"] == 1
    assert active_log.stat().st_size > 1024 * 1024

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["rotated_by_category"]["large_active_runtime_logs"]["count"] == 1
    assert active_log.stat().st_size <= 1024
    assert active_log.read_bytes().endswith(b"TAIL")
    archives = sorted((runtime_dir / "log_archives").glob("runtime.log.*.gz"))
    assert len(archives) == 1
    with gzip.open(archives[0], "rb") as fh:
        archived = fh.read()
    assert archived.endswith(b"TAIL")
    assert old_log.exists()


def test_runtime_storage_protects_manifest_log_and_flags_old_duplicate(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    duplicate_dir = runtime_dir / "logs"
    duplicate_dir.mkdir(parents=True)
    canonical = runtime_dir / "control.log"
    duplicate = duplicate_dir / "tradecraft-control.log"
    canonical.write_text("canonical", encoding="utf-8")
    duplicate.write_text("duplicate", encoding="utf-8")
    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    os.utime(canonical, (old_ts, old_ts))
    os.utime(duplicate, (old_ts, old_ts))
    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        runtime_log_retention_days=7,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)

    assert str(canonical) in report["canonical_runner_log_paths"]
    assert str(canonical) not in report["cleanup_candidates"]["old_runtime_logs"][
        "sample"
    ]
    assert report["cleanup_candidates"]["duplicate_runtime_logs"]["sample"] == [
        str(duplicate)
    ]
    cleanup = cleanup_runtime_storage(policy, dry_run=True)
    assert cleanup["deleted_by_category"]["duplicate_runtime_logs"]["count"] == 1
    assert canonical.exists()
    assert duplicate.exists()


def test_runtime_storage_dryrun_retention_archives_all_over_24h_except_manifest(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    scenario_dir = runtime_dir / "dryrun" / "rehearsal-a"
    scenario_dir.mkdir(parents=True)
    files = [scenario_dir / f"run-{index}.json" for index in range(5)]
    for index, path in enumerate(files):
        path.write_text(str(index), encoding="utf-8")
        timestamp = datetime(2026, 5, 1 + index, tzinfo=timezone.utc).timestamp()
        os.utime(path, (timestamp, timestamp))
    manifest = runtime_dir / "dryrun" / "protected_manifest.json"
    manifest.write_text(
        '{"protected_paths":["rehearsal-a/run-0.json"]}',
        encoding="utf-8",
    )
    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["old_dryrun_artifacts"]

    assert candidates["count"] == 4
    assert candidates["sample"] == [str(path) for path in files[1:]]
    assert report["retained_artifacts"]["protected_dryrun_artifacts"]["sample"] == [
        str(files[0])
    ]
    cleanup = cleanup_runtime_storage(policy, dry_run=True)
    assert cleanup["deleted_by_category"]["old_dryrun_artifacts"]["count"] == 4
    assert all(path.exists() for path in files)


def test_runtime_storage_groups_rehearsal_revisions_as_one_scenario(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".runtime" / "dryrun"

    keys = {
        runtime_maintenance._dryrun_scenario_key(
            root / f"binance_blocks_rehearsal{suffix}.db",
            root,
        )
        for suffix in ("", "2", "7")
    }
    json_key = runtime_maintenance._dryrun_scenario_key(
        root / "binance_block_trader_rehearsal7.json",
        root,
    )

    assert keys == {"rehearsal"}
    assert json_key == "rehearsal"


def test_runtime_storage_archives_old_dryrun_bundle_before_removal(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    dryrun_dir = runtime_dir / "dryrun"
    dryrun_dir.mkdir(parents=True)
    database = dryrun_dir / "binance_blocks_rehearsal4.db"
    state = dryrun_dir / "binance_block_trader_rehearsal4.json"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO orders (value) VALUES ('preserved')")
    state.write_text('{"scenario":"rehearsal4"}', encoding="utf-8")
    old_timestamp = datetime(2026, 7, 9, tzinfo=timezone.utc).timestamp()
    os.utime(database, (old_timestamp, old_timestamp))
    os.utime(state, (old_timestamp, old_timestamp))
    cold_root = tmp_path / ".runtime-cold-archive"
    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        cold_archive_root=str(cold_root),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_rag_rebuild_backups=False,
        prune_old_runtime_logs=False,
        rotate_large_active_logs=False,
        prune_repair_backup_artifacts=False,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=False,
        prune_old_ui_check_artifacts=False,
        prune_zero_byte_runtime_markers=False,
        prune_retired_state_artifacts=False,
        prune_retired_log_artifacts=False,
        prune_retired_db_artifacts=False,
        now_iso="2026-07-11T00:00:00+00:00",
    )

    result = cleanup_runtime_storage(policy, dry_run=False)

    assert result["archive_failures"] == []
    assert len(result["archived"]) == 1
    assert result["archived"][0]["verified"] is True
    assert not database.exists()
    assert not state.exists()
    restored = RuntimeColdArchiveV1(cold_root).restore(
        result["archived"][0]["entry_id"],
        tmp_path / "restored",
    )
    assert restored.restored is True
    restored_database = next(path for path in restored.paths if path.suffix == ".db")
    with sqlite3.connect(restored_database) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT value FROM orders").fetchone()[0] == "preserved"


def test_runtime_storage_cleanup_can_prune_old_repair_backups_and_show_retained(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    old_backup = runtime_dir / "kis_blocks.before_failed_reconciliation_repair_20260601.db.gz"
    recent_backup = runtime_dir / "kis_blocks.before_failed_reconciliation_repair_20260613.db.gz"
    generic_scratch = runtime_dir / "tmp_validation_status.json"

    old_backup.write_bytes(b"old repair backup")
    recent_backup.write_bytes(b"recent repair backup")
    generic_scratch.write_bytes(b"scratch")

    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 13, tzinfo=timezone.utc).timestamp()
    os.utime(old_backup, (old_ts, old_ts))
    os.utime(recent_backup, (recent_ts, recent_ts))
    os.utime(generic_scratch, (old_ts, old_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_repair_backup_artifacts=True,
        repair_backup_artifact_retention_days=7,
        prune_scratch_artifacts=True,
        scratch_artifact_retention_days=7,
        now_iso="2026-06-14T00:00:00+00:00",
    )
    assert Path(policy.cold_archive_root) == tmp_path / ".runtime-cold-archive"

    report = build_runtime_storage_report(policy)
    repair_candidates = report["cleanup_candidates"]["repair_backup_artifacts"]
    scratch_candidates = report["cleanup_candidates"]["scratch_artifacts"]
    retained = report["retained_artifacts"]["repair_backup_artifacts"]

    assert repair_candidates["count"] == 1
    assert repair_candidates["sample"] == [str(old_backup)]
    assert scratch_candidates["count"] == 1
    assert scratch_candidates["sample"] == [str(generic_scratch)]
    assert retained["count"] == 1
    assert retained["sample"] == [str(recent_backup)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["repair_backup_artifacts"]["count"] == 1
    assert old_backup.exists()
    assert recent_backup.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["repair_backup_artifacts"]["count"] == 1
    assert not old_backup.exists()
    assert recent_backup.exists()
    assert not generic_scratch.exists()


def test_runtime_storage_cleanup_can_prune_old_scratch_artifacts(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    old_tmp_db = runtime_dir / "_tmp_krx_refresh_check.db"
    old_tmp_shm = runtime_dir / "_tmp_krx_refresh_check.db-shm"
    old_validation = runtime_dir / "tmp_validation_status.json"
    old_backup = runtime_dir / "llm_usage.db.before_test_pollution_cleanup"
    recent_tmp = runtime_dir / "tmp_recent.json"
    real_db = runtime_dir / "kis_blocks.db"
    nested_backup = runtime_dir / "config_backups" / ".env.20260528T114551Z.bak"
    nested_backup.parent.mkdir()

    for path, size in [
        (old_tmp_db, 100),
        (old_tmp_shm, 200),
        (old_validation, 300),
        (old_backup, 400),
        (recent_tmp, 500),
        (real_db, 600),
        (nested_backup, 700),
    ]:
        path.write_bytes(b"x" * size)

    old_ts = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 14, tzinfo=timezone.utc).timestamp()
    for path in [old_tmp_db, old_tmp_shm, old_validation, old_backup, real_db, nested_backup]:
        os.utime(path, (old_ts, old_ts))
    os.utime(recent_tmp, (recent_ts, recent_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=True,
        scratch_artifact_retention_days=7,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["scratch_artifacts"]
    assert candidates["count"] == 4
    assert candidates["bytes"] == 1000
    assert str(real_db) not in candidates["sample"]
    assert str(nested_backup) not in candidates["sample"]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["scratch_artifacts"]["count"] == 4
    assert old_tmp_db.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["scratch_artifacts"]["bytes"] == 1000
    assert not old_tmp_db.exists()
    assert not old_tmp_shm.exists()
    assert not old_validation.exists()
    assert not old_backup.exists()
    assert recent_tmp.exists()
    assert real_db.exists()
    assert nested_backup.exists()


def test_runtime_storage_cleanup_can_prune_old_backtest_artifacts(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    old_grid = runtime_dir / "backtest_grid_sma_30d.json"
    old_result = runtime_dir / "backtest_result_btc_sma_30d.json"
    recent_live = runtime_dir / "backtest_live.json"
    unrelated = runtime_dir / "dashboard_snapshot.json"

    old_grid.write_bytes(b"grid" * 10)
    old_result.write_bytes(b"result" * 20)
    recent_live.write_bytes(b"live" * 30)
    unrelated.write_bytes(b"keep" * 40)

    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp()
    os.utime(old_grid, (old_ts, old_ts))
    os.utime(old_result, (old_ts, old_ts))
    os.utime(recent_live, (recent_ts, recent_ts))
    os.utime(unrelated, (old_ts, old_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=True,
        backtest_artifact_retention_days=30,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["old_backtest_artifacts"]
    assert candidates["count"] == 2
    assert candidates["bytes"] == old_grid.stat().st_size + old_result.stat().st_size
    assert candidates["sample"] == [str(old_grid), str(old_result)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["old_backtest_artifacts"]["count"] == 2
    assert old_grid.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["old_backtest_artifacts"]["bytes"] == (
        40 + 120
    )
    assert not old_grid.exists()
    assert not old_result.exists()
    assert recent_live.exists()
    assert unrelated.exists()


def test_runtime_storage_cleanup_can_prune_old_ui_check_artifacts(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    ui_check_dir = runtime_dir / "ui-check"
    nested_dir = ui_check_dir / "nested"
    nested_dir.mkdir(parents=True)
    old_desktop = ui_check_dir / "desktop-dashboard.png"
    old_mobile = nested_dir / "mobile-blocks.png"
    recent_wide = ui_check_dir / "wide-dashboard.png"
    unrelated = ui_check_dir / "notes.txt"

    old_desktop.write_bytes(b"desktop" * 10)
    old_mobile.write_bytes(b"mobile" * 20)
    recent_wide.write_bytes(b"wide" * 30)
    unrelated.write_bytes(b"keep" * 40)

    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp()
    os.utime(old_desktop, (old_ts, old_ts))
    os.utime(old_mobile, (old_ts, old_ts))
    os.utime(recent_wide, (recent_ts, recent_ts))
    os.utime(unrelated, (old_ts, old_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=False,
        prune_old_ui_check_artifacts=True,
        ui_check_artifact_retention_days=30,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["old_ui_check_artifacts"]
    assert candidates["count"] == 2
    assert candidates["bytes"] == old_desktop.stat().st_size + old_mobile.stat().st_size
    assert candidates["sample"] == [str(old_desktop), str(old_mobile)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["old_ui_check_artifacts"]["count"] == 2
    assert old_desktop.exists()
    assert old_mobile.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["old_ui_check_artifacts"]["bytes"] == (
        70 + 120
    )
    assert not old_desktop.exists()
    assert not old_mobile.exists()
    assert recent_wide.exists()
    assert unrelated.exists()


def test_runtime_storage_cleanup_can_prune_zero_byte_db_marker_files(tmp_path) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    db_path = runtime_dir / "naver_reports.db"
    old_marker = runtime_dir / "naver_reports.db report_chunks"
    recent_marker = runtime_dir / "naver_reports.db reports"
    nonzero_marker = runtime_dir / "naver_reports.db symbol_directory"
    orphan_marker = runtime_dir / "missing.db report_chunks"
    wal_file = runtime_dir / "naver_reports.db-wal"

    db_path.write_bytes(b"sqlite db")
    old_marker.write_bytes(b"")
    recent_marker.write_bytes(b"")
    nonzero_marker.write_bytes(b"not empty")
    orphan_marker.write_bytes(b"")
    wal_file.write_bytes(b"")

    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp()
    os.utime(old_marker, (old_ts, old_ts))
    os.utime(recent_marker, (recent_ts, recent_ts))
    os.utime(nonzero_marker, (old_ts, old_ts))
    os.utime(orphan_marker, (old_ts, old_ts))
    os.utime(wal_file, (old_ts, old_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=False,
        prune_zero_byte_runtime_markers=True,
        zero_byte_marker_retention_days=7,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["zero_byte_runtime_markers"]
    assert candidates["count"] == 1
    assert candidates["sample"] == [str(old_marker)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["zero_byte_runtime_markers"]["count"] == 1
    assert old_marker.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["zero_byte_runtime_markers"]["count"] == 1
    assert not old_marker.exists()
    assert recent_marker.exists()
    assert nonzero_marker.exists()
    assert orphan_marker.exists()
    assert wal_file.exists()


def test_runtime_storage_cleanup_can_prune_old_zero_byte_sqlite_placeholders(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    nested_dir = runtime_dir / "naver_reports"
    nested_dir.mkdir(parents=True)
    old_sqlite = runtime_dir / "naver_reports.sqlite"
    old_nested_db = nested_dir / "reports.db"
    active_db = runtime_dir / "naver_reports.db"
    recent_sqlite = runtime_dir / "recent.sqlite"
    wal_file = runtime_dir / "naver_reports.db-wal"
    nonzero_sqlite = runtime_dir / "nonzero.sqlite"

    old_sqlite.write_bytes(b"")
    old_nested_db.write_bytes(b"")
    active_db.write_bytes(b"sqlite db")
    recent_sqlite.write_bytes(b"")
    wal_file.write_bytes(b"")
    nonzero_sqlite.write_bytes(b"not empty")

    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp()
    for path in (old_sqlite, old_nested_db, wal_file, nonzero_sqlite):
        os.utime(path, (old_ts, old_ts))
    os.utime(recent_sqlite, (recent_ts, recent_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=False,
        prune_zero_byte_runtime_markers=True,
        zero_byte_marker_retention_days=7,
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["zero_byte_sqlite_placeholders"]
    assert candidates["count"] == 2
    assert candidates["bytes"] == 0
    assert candidates["sample"] == [str(old_sqlite), str(old_nested_db)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert (
        dry_run["deleted_by_category"]["zero_byte_sqlite_placeholders"]["count"]
        == 2
    )
    assert old_sqlite.exists()
    assert old_nested_db.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["zero_byte_sqlite_placeholders"]["count"] == 2
    assert not old_sqlite.exists()
    assert not old_nested_db.exists()
    assert active_db.exists()
    assert recent_sqlite.exists()
    assert wal_file.exists()
    assert nonzero_sqlite.exists()


def test_runtime_storage_cleanup_can_prune_zero_byte_retired_db_artifacts(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    active_wiki_dir = runtime_dir / "jue_wiki"
    active_wiki_dir.mkdir(parents=True)
    retired_placeholder = runtime_dir / "jue_wiki.db"
    active_wiki_db = active_wiki_dir / "wiki.db"
    nonzero_retired_name = runtime_dir / "legacy_live_performance.db"

    retired_placeholder.write_bytes(b"")
    active_wiki_db.write_bytes(b"sqlite db")
    nonzero_retired_name.write_bytes(b"not empty")

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=False,
        prune_zero_byte_runtime_markers=False,
        prune_retired_db_artifacts=True,
        retired_db_artifact_names=("jue_wiki.db", "legacy_live_performance.db"),
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["retired_db_artifacts"]
    assert candidates["count"] == 1
    assert candidates["sample"] == [str(retired_placeholder)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["retired_db_artifacts"]["count"] == 1
    assert retired_placeholder.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["retired_db_artifacts"]["count"] == 1
    assert not retired_placeholder.exists()
    assert active_wiki_db.exists()
    assert nonzero_retired_name.exists()


def test_runtime_storage_cleanup_can_prune_retired_kis_trader_state_artifacts(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    retired_state = runtime_dir / "kis_trader.json"
    active_state = runtime_dir / "kis_block_trader.json"
    recent_retired_state = runtime_dir / "recent_kis_trader.json"
    retired_state.write_text('{"runner":"legacy"}')
    active_state.write_text('{"runner":"active"}')
    recent_retired_state.write_text('{"runner":"legacy-recent"}')

    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp()
    os.utime(retired_state, (old_ts, old_ts))
    os.utime(active_state, (old_ts, old_ts))
    os.utime(recent_retired_state, (recent_ts, recent_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=False,
        prune_zero_byte_runtime_markers=False,
        prune_retired_state_artifacts=True,
        retired_state_artifact_retention_days=7,
        retired_state_artifact_names=("kis_trader.json", "recent_kis_trader.json"),
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["retired_state_artifacts"]
    assert candidates["count"] == 1
    assert candidates["sample"] == [str(retired_state)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["retired_state_artifacts"]["count"] == 1
    assert retired_state.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["retired_state_artifacts"]["count"] == 1
    assert not retired_state.exists()
    assert active_state.exists()
    assert recent_retired_state.exists()


def test_runtime_storage_cleanup_can_prune_named_retired_log_artifacts(
    tmp_path,
) -> None:
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    retired_log = runtime_dir / "investment-memory.log"
    active_log = runtime_dir / "investment_memory.log"
    nested_retired_log = runtime_dir / "nested" / "crypto-market-research.log"
    nested_retired_log.parent.mkdir()
    recent_retired_log = runtime_dir / "crypto-market-research.log"
    retired_log.write_text("legacy investment memory")
    active_log.write_text("active investment memory")
    nested_retired_log.write_text("nested should not be matched by basename")
    recent_retired_log.write_text("recent legacy crypto research")

    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    recent_ts = datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp()
    os.utime(retired_log, (old_ts, old_ts))
    os.utime(active_log, (old_ts, old_ts))
    os.utime(nested_retired_log, (old_ts, old_ts))
    os.utime(recent_retired_log, (recent_ts, recent_ts))

    policy = RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=False,
        prune_rag_repair_artifacts=False,
        prune_old_runtime_logs=False,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=False,
        prune_zero_byte_runtime_markers=False,
        prune_retired_state_artifacts=False,
        prune_retired_log_artifacts=True,
        retired_log_artifact_retention_days=7,
        retired_log_artifact_names=(
            "investment-memory.log",
            "crypto-market-research.log",
        ),
        now_iso="2026-06-14T00:00:00+00:00",
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["retired_log_artifacts"]
    assert candidates["count"] == 1
    assert candidates["sample"] == [str(retired_log)]

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_by_category"]["retired_log_artifacts"]["count"] == 1
    assert retired_log.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_by_category"]["retired_log_artifacts"]["count"] == 1
    assert not retired_log.exists()
    assert active_log.exists()
    assert nested_retired_log.exists()
    assert recent_retired_log.exists()
