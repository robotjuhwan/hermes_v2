from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RuntimeStoragePolicy:
    runtime_dir: str = ".runtime"
    reports_db_path: str = ".runtime/naver_reports.db"
    pdf_archive_dir: str = ".runtime/naver_reports/pdfs"
    large_file_threshold_mb: int = 10
    prune_unreferenced_pdfs: bool = True


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _dir_size(path: Path) -> int:
    if path.is_file():
        return _file_size(path)
    total = 0
    if not path.exists():
        return total
    for item in path.rglob("*"):
        if item.is_file():
            total += _file_size(item)
    return total


def _top_level_sizes(runtime_dir: Path) -> list[dict[str, Any]]:
    if not runtime_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for item in runtime_dir.iterdir():
        rows.append(
            {
                "path": str(item),
                "bytes": _dir_size(item),
                "kind": "dir" if item.is_dir() else "file",
            }
        )
    return sorted(rows, key=lambda row: int(row["bytes"]), reverse=True)


def _large_files(runtime_dir: Path, threshold_bytes: int) -> list[dict[str, Any]]:
    if not runtime_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for item in runtime_dir.rglob("*"):
        if not item.is_file():
            continue
        size = _file_size(item)
        if size >= threshold_bytes:
            rows.append({"path": str(item), "bytes": size})
    return sorted(rows, key=lambda row: int(row["bytes"]), reverse=True)


def _report_pdf_refs(db_path: Path) -> set[Path]:
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                """
                SELECT pdf_archived_path
                FROM reports
                WHERE TRIM(COALESCE(pdf_archived_path, '')) <> ''
                """
            ).fetchall()
    except sqlite3.Error:
        return set()
    return {Path(str(row[0])) for row in rows if str(row[0] or "").strip()}


def _unreferenced_report_pdfs(policy: RuntimeStoragePolicy) -> list[Path]:
    archive_dir = Path(policy.pdf_archive_dir)
    if not archive_dir.exists():
        return []
    refs = _report_pdf_refs(Path(policy.reports_db_path))
    out: list[Path] = []
    for item in archive_dir.rglob("*.pdf"):
        if item not in refs:
            out.append(item)
    return sorted(out)


def build_runtime_storage_report(policy: RuntimeStoragePolicy) -> dict[str, Any]:
    runtime_dir = Path(policy.runtime_dir)
    threshold_bytes = max(int(policy.large_file_threshold_mb), 1) * 1024 * 1024
    unreferenced = _unreferenced_report_pdfs(policy)
    unreferenced_bytes = sum(_file_size(path) for path in unreferenced)
    top_level = _top_level_sizes(runtime_dir)
    return {
        "status": "ok",
        "runtime_dir": str(runtime_dir),
        "total_bytes": _dir_size(runtime_dir),
        "top_level": top_level[:40],
        "large_files": _large_files(runtime_dir, threshold_bytes)[:40],
        "policy": {
            "large_file_threshold_mb": int(policy.large_file_threshold_mb),
            "prune_unreferenced_pdfs": bool(policy.prune_unreferenced_pdfs),
        },
        "cleanup_candidates": {
            "unreferenced_report_pdfs": {
                "count": len(unreferenced),
                "bytes": unreferenced_bytes,
                "sample": [str(path) for path in unreferenced[:12]],
            }
        },
    }


def cleanup_runtime_storage(
    policy: RuntimeStoragePolicy,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    deleted: list[dict[str, Any]] = []
    candidates = (
        _unreferenced_report_pdfs(policy)
        if bool(policy.prune_unreferenced_pdfs)
        else []
    )
    for path in candidates:
        size = _file_size(path)
        if not dry_run:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
        deleted.append({"path": str(path), "bytes": size})

    return {
        "status": "ok",
        "dry_run": bool(dry_run),
        "deleted_count": len(deleted),
        "deleted_bytes": sum(int(row["bytes"]) for row in deleted),
        "deleted": deleted[:80],
    }
