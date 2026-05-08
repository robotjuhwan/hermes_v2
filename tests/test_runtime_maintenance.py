from __future__ import annotations

import sqlite3

from tradecraft.services.runtime_maintenance import (
    RuntimeStoragePolicy,
    build_runtime_storage_report,
    cleanup_runtime_storage,
)


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
        reports_db_path=str(db_path),
        pdf_archive_dir=str(pdf_dir),
        large_file_threshold_mb=1,
    )

    report = build_runtime_storage_report(policy)
    candidates = report["cleanup_candidates"]["unreferenced_report_pdfs"]
    assert candidates["count"] == 1
    assert candidates["bytes"] == 20

    dry_run = cleanup_runtime_storage(policy, dry_run=True)
    assert dry_run["deleted_count"] == 1
    assert stale_pdf.exists()

    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["deleted_count"] == 1
    assert keep_pdf.exists()
    assert not stale_pdf.exists()
