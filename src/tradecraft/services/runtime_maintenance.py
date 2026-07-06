from __future__ import annotations

import gzip
import sqlite3
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ArchiveRetentionPolicy = int | dict[str, int]
ARCHIVE_RETENTION_DIAGNOSTIC_GRACE_DAYS = 1.0


@dataclass(slots=True)
class RuntimeStoragePolicy:
    runtime_dir: str = ".runtime"
    reports_db_path: str = ".runtime/naver_reports.db"
    pdf_archive_dir: str = ".runtime/naver_reports/pdfs"
    rag_persist_path: str = ".runtime/rag_chroma"
    large_file_threshold_mb: int = 10
    prune_unreferenced_pdfs: bool = True
    prune_extracted_report_pdfs: bool = False
    extracted_report_pdf_retention_days: int = 14
    prune_rag_repair_artifacts: bool = True
    rag_repair_artifact_retention_days: int = 7
    prune_rag_rebuild_backups: bool = True
    rag_rebuild_backup_retention_days: int = 7
    prune_old_runtime_logs: bool = True
    runtime_log_retention_days: int = 7
    rotate_large_active_logs: bool = True
    active_log_max_mb: int = 16
    active_log_tail_kb: int = 2048
    prune_repair_backup_artifacts: bool = True
    repair_backup_artifact_retention_days: int = 7
    prune_scratch_artifacts: bool = True
    scratch_artifact_retention_days: int = 7
    prune_old_backtest_artifacts: bool = True
    backtest_artifact_retention_days: int = 30
    prune_old_ui_check_artifacts: bool = True
    ui_check_artifact_retention_days: int = 30
    prune_zero_byte_runtime_markers: bool = True
    zero_byte_marker_retention_days: int = 7
    prune_retired_state_artifacts: bool = True
    retired_state_artifact_retention_days: int = 7
    retired_state_artifact_names: tuple[str, ...] = ("kis_trader.json",)
    prune_retired_log_artifacts: bool = True
    retired_log_artifact_retention_days: int = 1
    retired_log_artifact_names: tuple[str, ...] = (
        "investment-memory.log",
        "crypto-market-research.log",
    )
    prune_retired_db_artifacts: bool = True
    retired_db_artifact_names: tuple[str, ...] = ("jue_wiki.db",)
    database_compact_min_free_mb: int = 4
    database_compact_min_free_ratio_pct: float = 10.0
    archive_retention_days_by_key: dict[str, ArchiveRetentionPolicy] | None = None
    operational_db_paths: tuple[str, ...] = (
        ".runtime/crypto_market_research.db",
        ".runtime/crypto_quant.db",
        ".runtime/crypto_pattern_lab.db",
        ".runtime/binance_blocks.db",
        ".runtime/kis_blocks.db",
        ".runtime/market_judgment.db",
        ".runtime/market_pulse.db",
        ".runtime/investment_memory.db",
        ".runtime/etf_research.db",
        ".runtime/strategy_insights.db",
        ".runtime/trading_validation.db",
        ".runtime/live_performance.db",
    )
    now_iso: str = ""


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _size_mb(bytes_value: Any) -> float:
    return round(max(int(bytes_value or 0), 0) / (1024 * 1024), 2)


def _human_bytes(bytes_value: Any) -> str:
    size = max(int(bytes_value or 0), 0)
    if size < 1024:
        return f"{size} B"
    units = ("KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            return f"{round(value, 1)} {unit}"
    return f"{size} B"


def _with_size_mb(row: dict[str, Any], *, byte_key: str = "bytes") -> dict[str, Any]:
    payload = dict(row)
    bytes_value = payload.get(byte_key)
    payload["size_mb"] = _size_mb(bytes_value)
    payload["size_human"] = _human_bytes(bytes_value)
    return payload


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


def _runtime_tree_snapshot(
    runtime_dir: Path,
    threshold_bytes: int,
) -> dict[str, Any]:
    if not runtime_dir.exists():
        return {"total_bytes": 0, "top_level": [], "large_files": []}
    if runtime_dir.is_file():
        size = _file_size(runtime_dir)
        large_files = (
            [{"path": str(runtime_dir), "bytes": size}]
            if size >= threshold_bytes
            else []
        )
        return {"total_bytes": size, "top_level": [], "large_files": large_files}

    top_rows: dict[Path, dict[str, Any]] = {}
    for item in runtime_dir.iterdir():
        top_rows[item] = {
            "path": str(item),
            "bytes": 0,
            "kind": "dir" if item.is_dir() else "file",
        }

    total_bytes = 0
    large_files: list[dict[str, Any]] = []
    for item in runtime_dir.rglob("*"):
        if not item.is_file():
            continue
        size = _file_size(item)
        total_bytes += size
        if size >= threshold_bytes:
            large_files.append({"path": str(item), "bytes": size})
        try:
            top_name = item.relative_to(runtime_dir).parts[0]
        except (OSError, ValueError, IndexError):
            continue
        top_path = runtime_dir / top_name
        row = top_rows.setdefault(
            top_path,
            {
                "path": str(top_path),
                "bytes": 0,
                "kind": "dir" if top_path.is_dir() else "file",
            },
        )
        row["bytes"] = int(row["bytes"]) + size

    return {
        "total_bytes": total_bytes,
        "top_level": sorted(
            top_rows.values(),
            key=lambda row: int(row["bytes"]),
            reverse=True,
        ),
        "large_files": sorted(
            large_files,
            key=lambda row: int(row["bytes"]),
            reverse=True,
        ),
    }


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


def _parse_iso_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _policy_now(policy: RuntimeStoragePolicy) -> datetime:
    parsed = _parse_iso_datetime(policy.now_iso)
    return parsed or datetime.now(timezone.utc)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


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


def _extracted_report_pdfs(policy: RuntimeStoragePolicy) -> list[Path]:
    db_path = Path(policy.reports_db_path)
    archive_dir = Path(policy.pdf_archive_dir)
    if not db_path.exists() or not archive_dir.exists():
        return []
    retention_days = max(int(policy.extracted_report_pdf_retention_days), 1)
    cutoff = _policy_now(policy) - timedelta(days=retention_days)
    try:
        with sqlite3.connect(str(db_path)) as conn:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(reports)").fetchall()
            }
            required = {
                "pdf_archived_path",
                "content",
                "content_source",
                "crawled_at",
            }
            if not required.issubset(columns):
                return []
            rows = conn.execute(
                """
                SELECT pdf_archived_path, crawled_at
                FROM reports
                WHERE TRIM(COALESCE(pdf_archived_path, '')) <> ''
                  AND TRIM(COALESCE(content, '')) <> ''
                  AND LOWER(TRIM(COALESCE(content_source, ''))) LIKE 'pdf%'
                """
            ).fetchall()
    except sqlite3.Error:
        return []

    out: list[Path] = []
    seen: set[Path] = set()
    for raw_path, crawled_at in rows:
        path = Path(str(raw_path or "").strip())
        if not path.suffix.lower() == ".pdf":
            continue
        if not _is_relative_to(path, archive_dir):
            continue
        crawled_dt = _parse_iso_datetime(str(crawled_at or ""))
        if crawled_dt is None or crawled_dt > cutoff:
            continue
        if not path.exists() or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return sorted(out)


def _path_age_cutoff(policy: RuntimeStoragePolicy, retention_days: int) -> datetime:
    return _policy_now(policy) - timedelta(days=max(int(retention_days), 1))


def _is_path_older_than(path: Path, cutoff: datetime) -> bool:
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    return modified_at <= cutoff


def _rag_repair_artifacts(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    active_rag = Path(policy.rag_persist_path)
    cutoff = _path_age_cutoff(policy, int(policy.rag_repair_artifact_retention_days))
    out: list[Path] = []
    seen: set[Path] = set()

    for item in runtime_dir.iterdir():
        name = item.name
        if not item.is_dir():
            continue
        if not (
            name.startswith("rag_chroma_corrupt_")
            or name.startswith("rag_chroma_backup_")
            or name.startswith("rag_chroma_legacy_")
        ):
            continue
        if item.resolve() == active_rag.resolve():
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
        seen.add(item)

    for item in runtime_dir.rglob("*.legacy-config.bak"):
        if item in seen:
            continue
        if not item.is_file():
            continue
        if not _is_relative_to(item, runtime_dir):
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
        seen.add(item)

    return sorted(out)


def _rag_rebuild_backups(
    policy: RuntimeStoragePolicy,
    *,
    expired: bool,
) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    active_rag = Path(policy.rag_persist_path)
    cutoff = _path_age_cutoff(policy, int(policy.rag_rebuild_backup_retention_days))
    out: list[Path] = []
    for item in runtime_dir.iterdir():
        if not item.is_dir():
            continue
        if not item.name.startswith("rag_chroma.rebuild-backup-"):
            continue
        if item.resolve() == active_rag.resolve():
            continue
        is_expired = _is_path_older_than(item, cutoff)
        if expired != is_expired:
            continue
        out.append(item)
    return sorted(out)


def _old_runtime_logs(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(policy, int(policy.runtime_log_retention_days))
    out: list[Path] = []
    for item in runtime_dir.rglob("*"):
        if not item.is_file():
            continue
        name = item.name
        if not (name.endswith(".log") or ".log." in name):
            continue
        if not _is_relative_to(item, runtime_dir):
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
    return sorted(out)


def _large_active_runtime_logs(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(policy, int(policy.runtime_log_retention_days))
    threshold_bytes = max(int(policy.active_log_max_mb), 1) * 1024 * 1024
    out: list[Path] = []
    for item in runtime_dir.rglob("*.log"):
        if not item.is_file():
            continue
        if not _is_relative_to(item, runtime_dir):
            continue
        if _is_path_older_than(item, cutoff):
            continue
        if _file_size(item) <= threshold_bytes:
            continue
        out.append(item)
    return sorted(out)


def _is_repair_backup_artifact(path: Path) -> bool:
    name = path.name
    return (
        path.is_file()
        and ".before_" in name
        and "repair" in name.lower()
        and (name.endswith(".db") or name.endswith(".db.gz"))
    )


def _repair_backup_artifacts(
    policy: RuntimeStoragePolicy,
    *,
    expired: bool,
) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(
        policy,
        int(policy.repair_backup_artifact_retention_days),
    )
    out: list[Path] = []
    for item in runtime_dir.iterdir():
        if not _is_repair_backup_artifact(item):
            continue
        is_expired = _is_path_older_than(item, cutoff)
        if expired != is_expired:
            continue
        out.append(item)
    return sorted(out)


def _scratch_artifacts(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(policy, int(policy.scratch_artifact_retention_days))
    out: list[Path] = []
    for item in runtime_dir.iterdir():
        if not item.is_file():
            continue
        if _is_repair_backup_artifact(item):
            continue
        name = item.name
        is_scratch = (
            name.startswith("_tmp_")
            or name.startswith("tmp_")
            or ".before_" in name
            or name.endswith(".before_test_pollution_cleanup")
        )
        if not is_scratch:
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
    return sorted(out)


def _old_backtest_artifacts(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(policy, int(policy.backtest_artifact_retention_days))
    out: list[Path] = []
    for item in runtime_dir.iterdir():
        if not item.is_file():
            continue
        name = item.name
        if not (name.startswith("backtest_") and name.endswith(".json")):
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
    return sorted(out)


def _old_ui_check_artifacts(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    ui_check_dir = runtime_dir / "ui-check"
    if not ui_check_dir.exists() or not ui_check_dir.is_dir():
        return []
    cutoff = _path_age_cutoff(policy, int(policy.ui_check_artifact_retention_days))
    allowed_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    out: list[Path] = []
    for item in ui_check_dir.rglob("*"):
        if not item.is_file():
            continue
        if not _is_relative_to(item, ui_check_dir):
            continue
        if item.suffix.lower() not in allowed_suffixes:
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
    return sorted(out)


def _zero_byte_runtime_markers(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(policy, int(policy.zero_byte_marker_retention_days))
    out: list[Path] = []
    for item in runtime_dir.iterdir():
        if not item.is_file():
            continue
        if _file_size(item) != 0:
            continue
        name = item.name
        if ".db " not in name:
            continue
        db_name = name.split(".db ", 1)[0] + ".db"
        if not (runtime_dir / db_name).is_file():
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
    return sorted(out)


def _zero_byte_sqlite_placeholders(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(policy, int(policy.zero_byte_marker_retention_days))
    sqlite_suffixes = {".db", ".sqlite", ".sqlite3"}
    out: list[Path] = []
    for item in runtime_dir.rglob("*"):
        if not item.is_file():
            continue
        if not _is_relative_to(item, runtime_dir):
            continue
        if item.suffix.lower() not in sqlite_suffixes:
            continue
        if _file_size(item) != 0:
            continue
        if not _is_path_older_than(item, cutoff):
            continue
        out.append(item)
    return sorted(out, key=lambda path: str(path))


def _retired_state_artifacts(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(
        policy,
        int(policy.retired_state_artifact_retention_days),
    )
    out: list[Path] = []
    seen: set[Path] = set()
    for raw_name in policy.retired_state_artifact_names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        raw_path = Path(name)
        path = raw_path if raw_path.is_absolute() else runtime_dir / raw_path.name
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            continue
        if not _is_relative_to(path, runtime_dir):
            continue
        if not _is_path_older_than(path, cutoff):
            continue
        out.append(path)
    return sorted(out, key=lambda path: str(path))


def _retired_log_artifacts(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    cutoff = _path_age_cutoff(
        policy,
        int(policy.retired_log_artifact_retention_days),
    )
    out: list[Path] = []
    seen: set[Path] = set()
    for raw_name in policy.retired_log_artifact_names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        raw_path = Path(name)
        path = raw_path if raw_path.is_absolute() else runtime_dir / raw_path.name
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            continue
        if not _is_relative_to(path, runtime_dir):
            continue
        if not _is_path_older_than(path, cutoff):
            continue
        out.append(path)
    return sorted(out, key=lambda path: str(path))


def _retired_db_artifacts(policy: RuntimeStoragePolicy) -> list[Path]:
    runtime_dir = Path(policy.runtime_dir)
    if not runtime_dir.exists():
        return []
    out: list[Path] = []
    seen: set[Path] = set()
    for raw_name in policy.retired_db_artifact_names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        raw_path = Path(name)
        path = raw_path if raw_path.is_absolute() else runtime_dir / raw_path.name
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            continue
        if _file_size(path) != 0:
            continue
        out.append(path)
    return sorted(out, key=lambda path: str(path))


def _path_cleanup_size(path: Path) -> int:
    return _dir_size(path) if path.is_dir() else _file_size(path)


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _sqlite_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(
        f"PRAGMA table_info({_quote_sqlite_identifier(table)})"
    ).fetchall()
    return {str(row[1]) for row in rows}


def _sqlite_count_rows(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {_quote_sqlite_identifier(table)}"
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _sqlite_text_blob_bytes(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> int:
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(length(CAST({_quote_sqlite_identifier(column)} AS BLOB))), 0)
        FROM {_quote_sqlite_identifier(table)}
        """
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _sqlite_table_time_ranges(
    conn: sqlite3.Connection,
    table: str,
) -> dict[str, list[str]]:
    columns = _sqlite_table_columns(conn, table)
    time_columns = [
        column
        for column in sorted(columns)
        if column.endswith("_at")
        or column
        in {
            "as_of",
            "bucket_at",
            "captured_at",
            "created_at",
            "date",
            "fetched_at",
            "run_at",
            "scored_at",
            "timestamp",
            "trading_day",
            "ts",
            "updated_at",
        }
    ][:6]
    ranges: dict[str, list[str]] = {}
    for column in time_columns:
        try:
            row = conn.execute(
                f"""
                SELECT MIN({_quote_sqlite_identifier(column)}),
                       MAX({_quote_sqlite_identifier(column)})
                FROM {_quote_sqlite_identifier(table)}
                WHERE {_quote_sqlite_identifier(column)} IS NOT NULL
                  AND {_quote_sqlite_identifier(column)} != ''
                """
            ).fetchone()
        except sqlite3.Error:
            continue
        if not row or (row[0] is None and row[1] is None):
            continue
        ranges[column] = [str(row[0] or ""), str(row[1] or "")]
    return ranges


def _sqlite_storage_stats(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0] or 0)
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0] or 0)
    free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": _file_size(path),
        "page_size": page_size,
        "page_count": page_count,
        "free_pages": free_pages,
        "free_bytes": free_pages * page_size,
    }


def _sqlite_database_summary(
    path: Path,
    *,
    table_names: list[str] | None = None,
    content_columns: list[tuple[str, str]] | None = None,
    table_limit: int = 20,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "exists": False,
            "bytes": 0,
            "page_size": 0,
            "page_count": 0,
            "free_pages": 0,
            "free_bytes": 0,
            "tables": {},
            "content_bytes": {},
        }
    try:
        with sqlite3.connect(str(path)) as conn:
            tables = _sqlite_table_names(conn)
            summary = _sqlite_storage_stats(conn, path)
            table_counts = {
                table: _sqlite_count_rows(conn, table)
                for table in (table_names or sorted(tables))
                if table in tables
            }
            if table_names is None:
                table_counts = dict(
                    sorted(
                        table_counts.items(),
                        key=lambda row: int(row[1]),
                        reverse=True,
                    )[: max(int(table_limit), 1)]
                )
            summary.update(
                {
                    "status": "ok",
                    "table_count": len(tables),
                    "tables": table_counts,
                    "content_bytes": {},
                    "diagnostics": {"table_ranges": {}},
                }
            )
            for table in table_counts:
                if "archive" not in table:
                    continue
                ranges = _sqlite_table_time_ranges(conn, table)
                if ranges:
                    summary["diagnostics"]["table_ranges"][table] = ranges
            for table, column in content_columns or []:
                if table not in tables:
                    continue
                if column not in _sqlite_table_columns(conn, table):
                    continue
                summary["content_bytes"][f"{table}.{column}"] = _sqlite_text_blob_bytes(
                    conn,
                    table,
                    column,
                )
            return summary
    except sqlite3.Error as exc:
        return {
            "status": "error",
            "path": str(path),
            "exists": path.exists(),
            "bytes": _file_size(path),
            "page_size": 0,
            "page_count": 0,
            "free_pages": 0,
            "free_bytes": 0,
            "table_count": 0,
            "tables": {},
            "content_bytes": {},
            "error_message": str(exc),
        }


def _safe_sqlite_int(conn: sqlite3.Connection, sql: str, default: int = 0) -> int:
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error:
        return default
    if not row:
        return default
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError):
        return default


def _rag_chroma_diagnostics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(str(path)) as conn:
            tables = _sqlite_table_names(conn)
            diagnostics: dict[str, Any] = {}
            if "embeddings" in tables:
                columns = _sqlite_table_columns(conn, "embeddings")
                diagnostics["embedding_count"] = _sqlite_count_rows(conn, "embeddings")
                if "embedding_id" in columns:
                    total = _safe_sqlite_int(conn, "SELECT COUNT(*) FROM embeddings")
                    distinct = _safe_sqlite_int(
                        conn,
                        "SELECT COUNT(DISTINCT embedding_id) FROM embeddings",
                    )
                    diagnostics["distinct_embedding_ids"] = distinct
                    diagnostics["duplicate_embedding_ids"] = max(total - distinct, 0)

            if "embedding_metadata" in tables:
                columns = _sqlite_table_columns(conn, "embedding_metadata")
                if {"key", "string_value"}.issubset(columns):
                    rows = conn.execute(
                        """
                        SELECT
                            key,
                            COUNT(*) AS row_count,
                            SUM(LENGTH(COALESCE(string_value, ''))) AS bytes
                        FROM embedding_metadata
                        GROUP BY key
                        ORDER BY bytes DESC
                        LIMIT 12
                        """
                    ).fetchall()
                    diagnostics["metadata_key_bytes"] = [
                        {
                            "key": str(key),
                            "rows": int(row_count or 0),
                            "bytes": int(byte_count or 0),
                        }
                        for key, row_count, byte_count in rows
                    ]
                    diagnostics["document_metadata_bytes"] = _safe_sqlite_int(
                        conn,
                        """
                        SELECT SUM(LENGTH(COALESCE(string_value, '')))
                        FROM embedding_metadata
                        WHERE key = 'chroma:document'
                        """,
                    )

            if "embedding_fulltext_search_content" in tables:
                columns = _sqlite_table_columns(conn, "embedding_fulltext_search_content")
                if "c0" in columns:
                    diagnostics["fulltext_document_bytes"] = _sqlite_text_blob_bytes(
                        conn,
                        "embedding_fulltext_search_content",
                        "c0",
                    )

            if "embeddings_queue" in tables:
                columns = _sqlite_table_columns(conn, "embeddings_queue")
                queue: dict[str, Any] = {
                    "rows": _sqlite_count_rows(conn, "embeddings_queue"),
                }
                for column in ("vector", "metadata", "encoding"):
                    if column in columns:
                        queue[f"{column}_bytes"] = _sqlite_text_blob_bytes(
                            conn,
                            "embeddings_queue",
                            column,
                        )
                if "seq_id" in columns:
                    row = conn.execute(
                        "SELECT MIN(seq_id), MAX(seq_id) FROM embeddings_queue"
                    ).fetchone()
                    if row:
                        queue["min_seq_id"] = int(row[0] or 0)
                        queue["max_seq_id"] = int(row[1] or 0)
                if "created_at" in columns:
                    row = conn.execute(
                        "SELECT MIN(created_at), MAX(created_at) FROM embeddings_queue"
                    ).fetchone()
                    if row:
                        queue["oldest_created_at"] = str(row[0] or "")
                        queue["newest_created_at"] = str(row[1] or "")
                if "topic" in columns:
                    rows = conn.execute(
                        """
                        SELECT COALESCE(topic, '') AS topic, COUNT(*) AS row_count
                        FROM embeddings_queue
                        GROUP BY COALESCE(topic, '')
                        ORDER BY row_count DESC, topic ASC
                        LIMIT 8
                        """
                    ).fetchall()
                    queue["topic_counts"] = [
                        {"topic": str(topic), "rows": int(row_count or 0)}
                        for topic, row_count in rows
                    ]
                if "operation" in columns:
                    rows = conn.execute(
                        """
                        SELECT COALESCE(operation, '') AS operation, COUNT(*) AS row_count
                        FROM embeddings_queue
                        GROUP BY COALESCE(operation, '')
                        ORDER BY row_count DESC, operation ASC
                        LIMIT 8
                        """
                    ).fetchall()
                    queue["operation_counts"] = [
                        {"operation": operation, "rows": int(row_count or 0)}
                        for operation, row_count in rows
                    ]
                diagnostics["queue"] = queue
            return diagnostics
    except sqlite3.Error as exc:
        return {"status": "error", "error_message": str(exc)}


def _operational_db_key(path: Path) -> str:
    name = path.name
    if name.endswith(".db"):
        return name[:-3]
    return path.stem


def _operational_database_summaries(policy: RuntimeStoragePolicy) -> dict[str, Any]:
    out: dict[str, Any] = {}
    seen: set[Path] = set()
    for raw_path in policy.operational_db_paths:
        path = Path(str(raw_path))
        if path in seen:
            continue
        seen.add(path)
        out[_operational_db_key(path)] = _sqlite_database_summary(path, table_limit=20)
    return out


def _database_summaries(policy: RuntimeStoragePolicy) -> dict[str, Any]:
    rag_db = Path(policy.rag_persist_path) / "chroma.sqlite3"
    rag_summary = _sqlite_database_summary(
        rag_db,
        table_names=[
            "collections",
            "embeddings",
            "embedding_metadata",
            "embeddings_queue",
            "embedding_fulltext_search",
            "embedding_fulltext_search_content",
        ],
        content_columns=[
            ("embedding_metadata", "string_value"),
            ("embedding_fulltext_search_content", "c0"),
            ("embeddings_queue", "vector"),
            ("embeddings_queue", "metadata"),
            ("embeddings_queue", "encoding"),
        ],
    )
    rag_diagnostics = _rag_chroma_diagnostics(rag_db)
    if rag_diagnostics:
        rag_summary["diagnostics"] = rag_diagnostics
    return {
        "naver_reports": _sqlite_database_summary(
            Path(policy.reports_db_path),
            table_names=[
                "reports",
                "report_chunks",
                "report_facts",
                "report_symbol_links",
                "symbol_directory",
            ],
            content_columns=[
                ("reports", "content"),
                ("report_chunks", "content"),
            ],
        ),
        "rag_chroma": rag_summary,
        "operational": _operational_database_summaries(policy),
    }


def _iter_database_summary_rows(
    summaries: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group, value in summaries.items():
        if group == "operational" and isinstance(value, dict):
            for key, row in value.items():
                if isinstance(row, dict):
                    rows.append({"group": group, "key": str(key), **row})
            continue
        if isinstance(value, dict):
            rows.append({"group": str(group), "key": str(group), **value})
    return rows


def _database_compact_candidates(
    policy: RuntimeStoragePolicy,
    summaries: dict[str, Any],
) -> list[dict[str, Any]]:
    threshold_bytes = max(int(policy.database_compact_min_free_mb), 0) * 1024 * 1024
    threshold_ratio_pct = max(float(policy.database_compact_min_free_ratio_pct), 0.0)
    out: list[dict[str, Any]] = []
    for row in _iter_database_summary_rows(summaries):
        if str(row.get("status") or "") != "ok":
            continue
        total_bytes = int(row.get("bytes") or 0)
        free_bytes = int(row.get("free_bytes") or 0)
        if total_bytes <= 0 or free_bytes <= 0:
            continue
        free_ratio_pct = (free_bytes / total_bytes) * 100.0
        if free_bytes < threshold_bytes or free_ratio_pct < threshold_ratio_pct:
            continue
        out.append(
            {
                "group": str(row.get("group") or ""),
                "key": str(row.get("key") or ""),
                "path": str(row.get("path") or ""),
                "bytes": total_bytes,
                "free_bytes": free_bytes,
                "free_ratio_pct": round(free_ratio_pct, 4),
                "reason": "sqlite_freelist_free_space",
            }
        )
    return sorted(out, key=lambda row: int(row["free_bytes"]), reverse=True)


def _database_growth_action(
    *,
    key: str,
    reasons: list[str],
    archive_rows: int,
    text_payload_bytes: int,
    free_bytes: int,
    total_bytes: int,
) -> dict[str, str]:
    free_ratio = (free_bytes / total_bytes) * 100.0 if total_bytes > 0 else 0.0
    if free_bytes >= 4 * 1024 * 1024 and free_ratio >= 10.0:
        return {
            "action": "compact_database",
            "action_label": "DB compact 후보",
            "reclaimability": "high",
        }
    if "rag_queue_pending" in reasons:
        return {
            "action": "finish_rag_sync",
            "action_label": "RAG sync/queue 점검",
            "reclaimability": "low",
        }
    if key == "rag_chroma":
        return {
            "action": "review_rag_dedup_retention",
            "action_label": "RAG 중복/보존 정책 점검",
            "reclaimability": "policy",
        }
    if key == "naver_reports" or text_payload_bytes >= 100 * 1024 * 1024:
        return {
            "action": "preserve_corpus_prune_external_artifacts",
            "action_label": "원문 코퍼스 보존, 외부 산출물만 정리",
            "reclaimability": "low",
        }
    if archive_rows > 0:
        return {
            "action": "review_archive_retention",
            "action_label": "archive retention 점검",
            "reclaimability": "policy",
        }
    return {
        "action": "monitor_retention",
        "action_label": "성장률 모니터링",
        "reclaimability": "low",
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _archive_time_ranges(diagnostics: dict[str, Any]) -> dict[str, Any]:
    table_ranges = (
        diagnostics.get("table_ranges")
        if isinstance(diagnostics.get("table_ranges"), dict)
        else {}
    )
    return table_ranges


def _archive_table_range(
    table_name: str,
    diagnostics: dict[str, Any],
) -> tuple[datetime | None, datetime | None]:
    table_ranges = _archive_time_ranges(diagnostics)
    raw_ranges = (
        table_ranges.get(table_name)
        if isinstance(table_ranges.get(table_name), dict)
        else {}
    )
    if not raw_ranges and table_name == "symbol_judgments_archive":
        raw_ranges = (
            table_ranges.get("judgment_runs_archive")
            if isinstance(table_ranges.get("judgment_runs_archive"), dict)
            else {}
        )
    oldest: datetime | None = None
    newest: datetime | None = None
    for column_range in raw_ranges.values():
        if not isinstance(column_range, (list, tuple)) or len(column_range) < 2:
            continue
        for raw_value in (column_range[0], column_range[1]):
            parsed = _parse_iso_datetime(raw_value)
            if parsed is None:
                continue
            oldest = parsed if oldest is None or parsed < oldest else oldest
            newest = parsed if newest is None or parsed > newest else newest
    return oldest, newest


def _archive_range_from_tables(
    tables: dict[str, int],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    oldest: datetime | None = None
    newest: datetime | None = None
    archive_table_count = 0
    for table_name, row_count in tables.items():
        if not (
            table_name.endswith("_archive")
            or "_archive_" in table_name
            or "archive" in table_name
        ):
            continue
        archive_table_count += 1
        table_oldest, table_newest = _archive_table_range(table_name, diagnostics)
        if table_oldest is not None:
            oldest = table_oldest if oldest is None or table_oldest < oldest else oldest
        if table_newest is not None:
            newest = table_newest if newest is None or table_newest > newest else newest
    return {
        "archive_table_count": archive_table_count,
        "archive_oldest_at": oldest.isoformat() if oldest is not None else "",
        "archive_newest_at": newest.isoformat() if newest is not None else "",
    }


def _archive_retention_policy_for_table(
    *,
    key: str,
    table_name: str,
    retention_days_by_key: dict[str, ArchiveRetentionPolicy],
) -> int:
    raw_policy = retention_days_by_key.get(key, 0)
    if isinstance(raw_policy, dict):
        return int(
            raw_policy.get(table_name)
            or raw_policy.get("*")
            or raw_policy.get("default")
            or 0
        )
    return int(raw_policy or 0)


def _archive_table_retention_diagnostics(
    *,
    key: str,
    tables: dict[str, int],
    diagnostics: dict[str, Any],
    retention_days_by_key: dict[str, ArchiveRetentionPolicy],
    now: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table_name, row_count in sorted(tables.items()):
        if not (
            table_name.endswith("_archive")
            or "_archive_" in table_name
            or "archive" in table_name
        ):
            continue
        count = int(row_count or 0)
        if count <= 0:
            continue
        retention_days = _archive_retention_policy_for_table(
            key=key,
            table_name=table_name,
            retention_days_by_key=retention_days_by_key,
        )
        oldest, newest = _archive_table_range(table_name, diagnostics)
        if retention_days <= 0:
            status = "unconfigured"
            overdue_days = 0.0
        elif oldest is None:
            status = "unknown_age"
            overdue_days = 0.0
        else:
            cutoff = now - timedelta(days=retention_days)
            overdue_seconds = max((cutoff - oldest).total_seconds(), 0.0)
            overdue_days = round(overdue_seconds / 86400.0, 2)
            grace_seconds = ARCHIVE_RETENTION_DIAGNOSTIC_GRACE_DAYS * 86400.0
            if overdue_seconds > grace_seconds:
                status = "overdue"
            elif overdue_seconds > 0:
                status = "cleanup_due"
            else:
                status = "within_retention"
        rows.append(
            {
                "table": table_name,
                "rows": count,
                "status": status,
                "retention_days": retention_days,
                "overdue_days": overdue_days,
                "oldest_at": oldest.isoformat() if oldest is not None else "",
                "newest_at": newest.isoformat() if newest is not None else "",
            }
        )
    return rows


def _archive_retention_diagnostic(
    *,
    key: str,
    tables: dict[str, int],
    diagnostics: dict[str, Any],
    retention_days_by_key: dict[str, ArchiveRetentionPolicy],
    now: datetime,
) -> dict[str, Any]:
    archive_rows = sum(
        count
        for name, count in tables.items()
        if name.endswith("_archive") or "_archive_" in name or "archive" in name
    )
    table_diagnostics = _archive_table_retention_diagnostics(
        key=key,
        tables=tables,
        diagnostics=diagnostics,
        retention_days_by_key=retention_days_by_key,
        now=now,
    )
    configured_days = [
        int(row.get("retention_days") or 0)
        for row in table_diagnostics
        if int(row.get("retention_days") or 0) > 0
    ]
    retention_days = min(configured_days) if configured_days else 0
    archive_range = _archive_range_from_tables(tables, diagnostics)
    oldest = _parse_iso_datetime(archive_range.get("archive_oldest_at"))
    if archive_rows <= 0:
        status = "none"
        overdue_days = 0.0
    elif table_diagnostics and any(
        row.get("status") == "overdue" for row in table_diagnostics
    ):
        status = "overdue"
        overdue_days = max(
            float(row.get("overdue_days") or 0.0)
            for row in table_diagnostics
            if row.get("status") == "overdue"
        )
    elif table_diagnostics and any(
        row.get("status") == "cleanup_due" for row in table_diagnostics
    ):
        status = "cleanup_due"
        overdue_days = max(
            float(row.get("overdue_days") or 0.0)
            for row in table_diagnostics
            if row.get("status") == "cleanup_due"
        )
    elif table_diagnostics and all(
        row.get("status") == "within_retention" for row in table_diagnostics
    ):
        status = "within_retention"
        overdue_days = 0.0
    elif table_diagnostics and any(
        row.get("status") == "unknown_age" for row in table_diagnostics
    ):
        status = "unknown_age"
        overdue_days = 0.0
    elif retention_days <= 0:
        status = "unconfigured"
        overdue_days = 0.0
    elif oldest is None:
        status = "unknown_age"
        overdue_days = 0.0
    else:
        cutoff = now - timedelta(days=retention_days)
        overdue_seconds = max((cutoff - oldest).total_seconds(), 0.0)
        overdue_days = round(overdue_seconds / 86400.0, 2)
        status = "overdue" if overdue_seconds > 0 else "within_retention"
    return {
        "archive_retention_status": status,
        "archive_retention_days": retention_days,
        "archive_overdue_days": overdue_days,
        "archive_retention_tables": table_diagnostics,
        **archive_range,
    }


def _database_growth_pressure(
    summaries: dict[str, Any],
    *,
    archive_retention_days_by_key: dict[str, ArchiveRetentionPolicy] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    retention_days_by_key = archive_retention_days_by_key or {}
    active_now = now or datetime.now(timezone.utc)
    for row in _iter_database_summary_rows(summaries):
        if str(row.get("status") or "") != "ok":
            continue
        total_bytes = int(row.get("bytes") or 0)
        tables_raw = row.get("tables") if isinstance(row.get("tables"), dict) else {}
        content_raw = (
            row.get("content_bytes") if isinstance(row.get("content_bytes"), dict) else {}
        )
        diagnostics = (
            row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
        )
        tables = {
            str(key): int(value or 0)
            for key, value in tables_raw.items()
            if int(value or 0) >= 0
        }
        content_bytes = {
            str(key): int(value or 0)
            for key, value in content_raw.items()
            if int(value or 0) >= 0
        }
        total_rows = sum(tables.values())
        archive_rows = sum(
            count
            for name, count in tables.items()
            if name.endswith("_archive") or "_archive_" in name or "archive" in name
        )
        text_payload_bytes = sum(content_bytes.values())
        largest_tables = [
            {"table": name, "rows": count}
            for name, count in sorted(
                tables.items(),
                key=lambda item: int(item[1]),
                reverse=True,
            )[:5]
        ]
        largest_payloads = [
            {"column": name, "bytes": byte_count}
            for name, byte_count in sorted(
                content_bytes.items(),
                key=lambda item: int(item[1]),
                reverse=True,
            )[:5]
        ]
        reasons: list[str] = []
        if total_bytes >= 100 * 1024 * 1024:
            reasons.append("large_database")
        if total_rows >= 50_000:
            reasons.append("high_row_count")
        if archive_rows > 0:
            reasons.append("archive_tables")
        if text_payload_bytes >= 25 * 1024 * 1024:
            reasons.append("large_text_payloads")
        queue = diagnostics.get("queue") if isinstance(diagnostics.get("queue"), dict) else {}
        if int(queue.get("rows") or 0) > 0:
            reasons.append("rag_queue_pending")
        if not reasons:
            continue
        key = str(row.get("key") or "")
        action = _database_growth_action(
            key=key,
            reasons=reasons,
            archive_rows=archive_rows,
            text_payload_bytes=text_payload_bytes,
            free_bytes=int(row.get("free_bytes") or 0),
            total_bytes=total_bytes,
        )
        archive_diagnostic = _archive_retention_diagnostic(
            key=key,
            tables=tables,
            diagnostics=diagnostics,
            retention_days_by_key=retention_days_by_key,
            now=active_now,
        )
        rows.append(
            {
                "group": str(row.get("group") or ""),
                "key": key,
                "path": str(row.get("path") or ""),
                "bytes": total_bytes,
                "size_mb": round(total_bytes / (1024 * 1024), 2),
                "total_rows": total_rows,
                "archive_rows": archive_rows,
                "archive_ratio_pct": round(
                    (archive_rows / total_rows) * 100.0,
                    2,
                )
                if total_rows > 0
                else 0.0,
                "text_payload_bytes": text_payload_bytes,
                "text_payload_mb": round(text_payload_bytes / (1024 * 1024), 2),
                "largest_tables": largest_tables,
                "largest_payloads": largest_payloads,
                "reasons": reasons,
                **action,
                **archive_diagnostic,
            }
        )
    return sorted(rows, key=lambda item: int(item["bytes"]), reverse=True)


def _compact_sqlite_database(path: Path) -> dict[str, Any]:
    before_size = _file_size(path)
    with sqlite3.connect(str(path), timeout=1.0, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout = 1000")
        before = _sqlite_storage_stats(conn, path)
        conn.execute("VACUUM")
        conn.execute("PRAGMA optimize")
        after = _sqlite_storage_stats(conn, path)
    return {
        "status": "ok",
        "path": str(path),
        "before_bytes": before_size,
        "after_bytes": _file_size(path),
        "before_free_bytes": int(before.get("free_bytes") or 0),
        "after_free_bytes": int(after.get("free_bytes") or 0),
        "reclaimed_bytes": max(before_size - _file_size(path), 0),
    }


def _compact_database_candidates(
    policy: RuntimeStoragePolicy,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    report = build_runtime_storage_report(policy)
    candidates = list(report.get("database_compact_candidates") or [])
    results: list[dict[str, Any]] = []
    for row in candidates:
        path = Path(str(row.get("path") or ""))
        if dry_run:
            results.append(
                {
                    "status": "dry_run",
                    "path": str(path),
                    "before_bytes": int(row.get("bytes") or 0),
                    "before_free_bytes": int(row.get("free_bytes") or 0),
                    "estimated_reclaim_bytes": int(row.get("free_bytes") or 0),
                    "group": row.get("group"),
                    "key": row.get("key"),
                }
            )
            continue
        try:
            compacted = _compact_sqlite_database(path)
            compacted["group"] = row.get("group")
            compacted["key"] = row.get("key")
            results.append(compacted)
        except sqlite3.Error as exc:
            results.append(
                {
                    "status": "error",
                    "path": str(path),
                    "error_message": str(exc),
                    "group": row.get("group"),
                    "key": row.get("key"),
                    "before_bytes": int(row.get("bytes") or 0),
                    "before_free_bytes": int(row.get("free_bytes") or 0),
                }
            )
    return {
        "enabled": True,
        "dry_run": bool(dry_run),
        "candidate_count": len(candidates),
        "estimated_reclaim_bytes": sum(
            int(row.get("free_bytes") or 0) for row in candidates
        ),
        "compacted_count": sum(1 for row in results if row.get("status") == "ok"),
        "error_count": sum(1 for row in results if row.get("status") == "error"),
        "results": results[:40],
    }


def build_runtime_storage_report(policy: RuntimeStoragePolicy) -> dict[str, Any]:
    runtime_dir = Path(policy.runtime_dir)
    threshold_bytes = max(int(policy.large_file_threshold_mb), 1) * 1024 * 1024
    tree_snapshot = _runtime_tree_snapshot(runtime_dir, threshold_bytes)
    total_bytes = int(tree_snapshot["total_bytes"])
    large_files = tree_snapshot["large_files"][:40]
    database_summaries = _database_summaries(policy)
    unreferenced = _unreferenced_report_pdfs(policy)
    unreferenced_bytes = sum(_file_size(path) for path in unreferenced)
    extracted = _extracted_report_pdfs(policy)
    extracted_bytes = sum(_file_size(path) for path in extracted)
    rag_artifacts = _rag_repair_artifacts(policy)
    rag_artifact_bytes = sum(_path_cleanup_size(path) for path in rag_artifacts)
    rag_rebuild_backups = (
        _rag_rebuild_backups(policy, expired=True)
        if bool(policy.prune_rag_rebuild_backups)
        else []
    )
    rag_rebuild_backup_bytes = sum(
        _path_cleanup_size(path) for path in rag_rebuild_backups
    )
    retained_rag_rebuild_backups = _rag_rebuild_backups(policy, expired=False)
    retained_rag_rebuild_backup_bytes = sum(
        _path_cleanup_size(path) for path in retained_rag_rebuild_backups
    )
    old_logs = _old_runtime_logs(policy)
    old_log_bytes = sum(_file_size(path) for path in old_logs)
    large_active_logs = (
        _large_active_runtime_logs(policy)
        if bool(policy.rotate_large_active_logs)
        else []
    )
    large_active_log_bytes = sum(_file_size(path) for path in large_active_logs)
    repair_backups = (
        _repair_backup_artifacts(policy, expired=True)
        if bool(policy.prune_repair_backup_artifacts)
        else []
    )
    repair_backup_bytes = sum(_file_size(path) for path in repair_backups)
    retained_repair_backups = _repair_backup_artifacts(policy, expired=False)
    retained_repair_backup_bytes = sum(_file_size(path) for path in retained_repair_backups)
    scratch = _scratch_artifacts(policy)
    scratch_bytes = sum(_file_size(path) for path in scratch)
    old_backtests = (
        _old_backtest_artifacts(policy)
        if bool(policy.prune_old_backtest_artifacts)
        else []
    )
    old_backtest_bytes = sum(_file_size(path) for path in old_backtests)
    old_ui_checks = (
        _old_ui_check_artifacts(policy)
        if bool(policy.prune_old_ui_check_artifacts)
        else []
    )
    old_ui_check_bytes = sum(_file_size(path) for path in old_ui_checks)
    zero_byte_markers = (
        _zero_byte_runtime_markers(policy)
        if bool(policy.prune_zero_byte_runtime_markers)
        else []
    )
    zero_byte_marker_bytes = sum(_file_size(path) for path in zero_byte_markers)
    zero_byte_sqlite_placeholders = (
        _zero_byte_sqlite_placeholders(policy)
        if bool(policy.prune_zero_byte_runtime_markers)
        else []
    )
    zero_byte_sqlite_placeholder_bytes = sum(
        _file_size(path) for path in zero_byte_sqlite_placeholders
    )
    retired_state_artifacts = (
        _retired_state_artifacts(policy)
        if bool(policy.prune_retired_state_artifacts)
        else []
    )
    retired_state_artifact_bytes = sum(
        _file_size(path) for path in retired_state_artifacts
    )
    retired_log_artifacts = (
        _retired_log_artifacts(policy)
        if bool(policy.prune_retired_log_artifacts)
        else []
    )
    retired_log_artifact_bytes = sum(
        _file_size(path) for path in retired_log_artifacts
    )
    retired_db_artifacts = (
        _retired_db_artifacts(policy)
        if bool(policy.prune_retired_db_artifacts)
        else []
    )
    retired_db_artifact_bytes = sum(_file_size(path) for path in retired_db_artifacts)
    cleanup_candidate_rows = {
        "unreferenced_report_pdfs": {
            "count": len(unreferenced),
            "bytes": unreferenced_bytes,
            "sample": [str(path) for path in unreferenced[:12]],
        },
        "extracted_report_pdfs": {
            "count": len(extracted),
            "bytes": extracted_bytes,
            "sample": [str(path) for path in extracted[:12]],
        },
        "rag_repair_artifacts": {
            "count": len(rag_artifacts),
            "bytes": rag_artifact_bytes,
            "sample": [str(path) for path in rag_artifacts[:12]],
        },
        "rag_rebuild_backups": {
            "count": len(rag_rebuild_backups),
            "bytes": rag_rebuild_backup_bytes,
            "sample": [str(path) for path in rag_rebuild_backups[:12]],
        },
        "old_runtime_logs": {
            "count": len(old_logs),
            "bytes": old_log_bytes,
            "sample": [str(path) for path in old_logs[:12]],
        },
        "large_active_runtime_logs": {
            "count": len(large_active_logs),
            "bytes": large_active_log_bytes,
            "sample": [str(path) for path in large_active_logs[:12]],
        },
        "repair_backup_artifacts": {
            "count": len(repair_backups),
            "bytes": repair_backup_bytes,
            "sample": [str(path) for path in repair_backups[:12]],
        },
        "scratch_artifacts": {
            "count": len(scratch),
            "bytes": scratch_bytes,
            "sample": [str(path) for path in scratch[:12]],
        },
        "old_backtest_artifacts": {
            "count": len(old_backtests),
            "bytes": old_backtest_bytes,
            "sample": [str(path) for path in old_backtests[:12]],
        },
        "old_ui_check_artifacts": {
            "count": len(old_ui_checks),
            "bytes": old_ui_check_bytes,
            "sample": [str(path) for path in old_ui_checks[:12]],
        },
        "zero_byte_runtime_markers": {
            "count": len(zero_byte_markers),
            "bytes": zero_byte_marker_bytes,
            "sample": [str(path) for path in zero_byte_markers[:12]],
        },
        "zero_byte_sqlite_placeholders": {
            "count": len(zero_byte_sqlite_placeholders),
            "bytes": zero_byte_sqlite_placeholder_bytes,
            "sample": [str(path) for path in zero_byte_sqlite_placeholders[:12]],
        },
        "retired_state_artifacts": {
            "count": len(retired_state_artifacts),
            "bytes": retired_state_artifact_bytes,
            "sample": [str(path) for path in retired_state_artifacts[:12]],
        },
        "retired_log_artifacts": {
            "count": len(retired_log_artifacts),
            "bytes": retired_log_artifact_bytes,
            "sample": [str(path) for path in retired_log_artifacts[:12]],
        },
        "retired_db_artifacts": {
            "count": len(retired_db_artifacts),
            "bytes": retired_db_artifact_bytes,
            "sample": [str(path) for path in retired_db_artifacts[:12]],
        },
    }
    cleanup_candidate_count = sum(
        int(row.get("count") or 0) for row in cleanup_candidate_rows.values()
    )
    cleanup_candidate_bytes = sum(
        int(row.get("bytes") or 0) for row in cleanup_candidate_rows.values()
    )
    for row in cleanup_candidate_rows.values():
        row["size_mb"] = _size_mb(row.get("bytes"))
        row["size_human"] = _human_bytes(row.get("bytes"))
    archive_retention_days_by_key = dict(policy.archive_retention_days_by_key or {})
    database_growth_pressure = _database_growth_pressure(
        database_summaries,
        archive_retention_days_by_key=archive_retention_days_by_key,
        now=_policy_now(policy),
    )
    database_growth_pressure_bytes = sum(
        int(row.get("bytes") or 0) for row in database_growth_pressure
    )
    database_growth_pressure_archive_rows = sum(
        int(row.get("archive_rows") or 0) for row in database_growth_pressure
    )
    database_growth_pressure_text_payload_bytes = sum(
        int(row.get("text_payload_bytes") or 0)
        for row in database_growth_pressure
    )
    return {
        "status": "ok",
        "runtime_dir": str(runtime_dir),
        "total_bytes": total_bytes,
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "total_human": _human_bytes(total_bytes),
        "top_level": [_with_size_mb(row) for row in tree_snapshot["top_level"][:40]],
        "top_level_count": len(tree_snapshot["top_level"]),
        "large_files": [_with_size_mb(row) for row in large_files],
        "large_file_count": len(tree_snapshot["large_files"]),
        "database_summaries": database_summaries,
        "database_compact_candidates": _database_compact_candidates(
            policy,
            database_summaries,
        ),
        "database_growth_pressure": database_growth_pressure,
        "database_growth_pressure_count": len(database_growth_pressure),
        "database_growth_pressure_bytes": database_growth_pressure_bytes,
        "database_growth_pressure_size_mb": round(
            database_growth_pressure_bytes / (1024 * 1024),
            2,
        ),
        "database_growth_pressure_archive_rows": database_growth_pressure_archive_rows,
        "database_growth_pressure_text_payload_bytes": (
            database_growth_pressure_text_payload_bytes
        ),
        "database_growth_pressure_text_payload_size_mb": round(
            database_growth_pressure_text_payload_bytes / (1024 * 1024),
            2,
        ),
        "cleanup_candidate_count": cleanup_candidate_count,
        "cleanup_candidate_bytes": cleanup_candidate_bytes,
        "cleanup_candidate_size_mb": round(
            cleanup_candidate_bytes / (1024 * 1024),
            2,
        ),
        "cleanup_candidate_human": _human_bytes(cleanup_candidate_bytes),
        "policy": {
            "large_file_threshold_mb": int(policy.large_file_threshold_mb),
            "prune_unreferenced_pdfs": bool(policy.prune_unreferenced_pdfs),
            "prune_extracted_report_pdfs": bool(policy.prune_extracted_report_pdfs),
            "extracted_report_pdf_retention_days": int(
                policy.extracted_report_pdf_retention_days
            ),
            "rag_persist_path": str(policy.rag_persist_path),
            "prune_rag_repair_artifacts": bool(policy.prune_rag_repair_artifacts),
            "rag_repair_artifact_retention_days": int(
                policy.rag_repair_artifact_retention_days
            ),
            "prune_rag_rebuild_backups": bool(policy.prune_rag_rebuild_backups),
            "rag_rebuild_backup_retention_days": int(
                policy.rag_rebuild_backup_retention_days
            ),
            "prune_old_runtime_logs": bool(policy.prune_old_runtime_logs),
            "runtime_log_retention_days": int(policy.runtime_log_retention_days),
            "rotate_large_active_logs": bool(policy.rotate_large_active_logs),
            "active_log_max_mb": int(policy.active_log_max_mb),
            "active_log_tail_kb": int(policy.active_log_tail_kb),
            "prune_scratch_artifacts": bool(policy.prune_scratch_artifacts),
            "scratch_artifact_retention_days": int(
                policy.scratch_artifact_retention_days
            ),
            "prune_old_backtest_artifacts": bool(
                policy.prune_old_backtest_artifacts
            ),
            "backtest_artifact_retention_days": int(
                policy.backtest_artifact_retention_days
            ),
            "prune_old_ui_check_artifacts": bool(
                policy.prune_old_ui_check_artifacts
            ),
            "ui_check_artifact_retention_days": int(
                policy.ui_check_artifact_retention_days
            ),
            "prune_zero_byte_runtime_markers": bool(
                policy.prune_zero_byte_runtime_markers
            ),
            "zero_byte_marker_retention_days": int(
                policy.zero_byte_marker_retention_days
            ),
            "prune_retired_state_artifacts": bool(
                policy.prune_retired_state_artifacts
            ),
            "retired_state_artifact_retention_days": int(
                policy.retired_state_artifact_retention_days
            ),
            "retired_state_artifact_names": list(
                policy.retired_state_artifact_names
            ),
            "prune_retired_log_artifacts": bool(
                policy.prune_retired_log_artifacts
            ),
            "retired_log_artifact_retention_days": int(
                policy.retired_log_artifact_retention_days
            ),
            "retired_log_artifact_names": list(policy.retired_log_artifact_names),
            "prune_retired_db_artifacts": bool(policy.prune_retired_db_artifacts),
            "retired_db_artifact_names": list(policy.retired_db_artifact_names),
            "database_compact_min_free_mb": int(
                policy.database_compact_min_free_mb
            ),
            "database_compact_min_free_ratio_pct": float(
                policy.database_compact_min_free_ratio_pct
            ),
            "archive_retention_days_by_key": archive_retention_days_by_key,
            "prune_repair_backup_artifacts": bool(
                policy.prune_repair_backup_artifacts
            ),
            "repair_backup_artifact_retention_days": int(
                policy.repair_backup_artifact_retention_days
            ),
        },
        "retained_artifacts": {
            "repair_backup_artifacts": {
                "count": len(retained_repair_backups),
                "bytes": retained_repair_backup_bytes,
                "retention_days": int(policy.repair_backup_artifact_retention_days),
                "sample": [str(path) for path in retained_repair_backups[:12]],
            },
            "rag_rebuild_backups": {
                "count": len(retained_rag_rebuild_backups),
                "bytes": retained_rag_rebuild_backup_bytes,
                "retention_days": int(policy.rag_rebuild_backup_retention_days),
                "sample": [str(path) for path in retained_rag_rebuild_backups[:12]],
            },
        },
        "cleanup_candidates": cleanup_candidate_rows,
    }


def _archive_and_tail_active_log(
    path: Path,
    policy: RuntimeStoragePolicy,
) -> dict[str, Any]:
    size = _file_size(path)
    archive_dir = Path(policy.runtime_dir) / "log_archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _policy_now(policy).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"{path.name}.{timestamp}.gz"
    suffix = 1
    while archive_path.exists():
        archive_path = archive_dir / f"{path.name}.{timestamp}.{suffix}.gz"
        suffix += 1

    tail_bytes = max(int(policy.active_log_tail_kb), 1) * 1024
    data = path.read_bytes()
    tail = data[-tail_bytes:]
    with gzip.open(archive_path, "wb") as fh:
        fh.write(data)
    path.write_bytes(tail)
    return {
        "path": str(path),
        "archive_path": str(archive_path),
        "bytes": size,
        "kept_bytes": len(tail),
    }


def cleanup_runtime_storage(
    policy: RuntimeStoragePolicy,
    *,
    dry_run: bool = True,
    compact_databases: bool = False,
) -> dict[str, Any]:
    deleted: list[dict[str, Any]] = []
    rotated: list[dict[str, Any]] = []
    candidates_by_category = {
        "unreferenced_report_pdfs": (
            _unreferenced_report_pdfs(policy)
            if bool(policy.prune_unreferenced_pdfs)
            else []
        ),
        "extracted_report_pdfs": (
            _extracted_report_pdfs(policy)
            if bool(policy.prune_extracted_report_pdfs)
            else []
        ),
        "rag_repair_artifacts": (
            _rag_repair_artifacts(policy)
            if bool(policy.prune_rag_repair_artifacts)
            else []
        ),
        "rag_rebuild_backups": (
            _rag_rebuild_backups(policy, expired=True)
            if bool(policy.prune_rag_rebuild_backups)
            else []
        ),
        "old_runtime_logs": (
            _old_runtime_logs(policy)
            if bool(policy.prune_old_runtime_logs)
            else []
        ),
        "repair_backup_artifacts": (
            _repair_backup_artifacts(policy, expired=True)
            if bool(policy.prune_repair_backup_artifacts)
            else []
        ),
        "scratch_artifacts": (
            _scratch_artifacts(policy)
            if bool(policy.prune_scratch_artifacts)
            else []
        ),
        "old_backtest_artifacts": (
            _old_backtest_artifacts(policy)
            if bool(policy.prune_old_backtest_artifacts)
            else []
        ),
        "old_ui_check_artifacts": (
            _old_ui_check_artifacts(policy)
            if bool(policy.prune_old_ui_check_artifacts)
            else []
        ),
        "zero_byte_runtime_markers": (
            _zero_byte_runtime_markers(policy)
            if bool(policy.prune_zero_byte_runtime_markers)
            else []
        ),
        "zero_byte_sqlite_placeholders": (
            _zero_byte_sqlite_placeholders(policy)
            if bool(policy.prune_zero_byte_runtime_markers)
            else []
        ),
        "retired_state_artifacts": (
            _retired_state_artifacts(policy)
            if bool(policy.prune_retired_state_artifacts)
            else []
        ),
        "retired_log_artifacts": (
            _retired_log_artifacts(policy)
            if bool(policy.prune_retired_log_artifacts)
            else []
        ),
        "retired_db_artifacts": (
            _retired_db_artifacts(policy)
            if bool(policy.prune_retired_db_artifacts)
            else []
        ),
    }
    rotate_candidates_by_category = {
        "large_active_runtime_logs": (
            _large_active_runtime_logs(policy)
            if bool(policy.rotate_large_active_logs)
            else []
        ),
    }
    deleted_by_category: dict[str, dict[str, Any]] = {
        key: {"count": 0, "bytes": 0}
        for key in candidates_by_category
    }
    rotated_by_category: dict[str, dict[str, Any]] = {
        key: {"count": 0, "bytes": 0, "kept_bytes": 0}
        for key in rotate_candidates_by_category
    }
    seen: set[Path] = set()
    rotate_seen: set[Path] = set()
    for category, candidates in rotate_candidates_by_category.items():
        for path in candidates:
            if path in rotate_seen:
                continue
            rotate_seen.add(path)
            size = _file_size(path)
            if dry_run:
                row = {
                    "path": str(path),
                    "bytes": size,
                    "kept_bytes": min(
                        size, max(int(policy.active_log_tail_kb), 1) * 1024
                    ),
                    "category": category,
                }
            else:
                try:
                    row = _archive_and_tail_active_log(path, policy)
                except FileNotFoundError:
                    continue
                row["category"] = category
            rotated.append(row)
            rotated_by_category[category]["count"] += 1
            rotated_by_category[category]["bytes"] += int(row["bytes"])
            rotated_by_category[category]["kept_bytes"] += int(row["kept_bytes"])

    for category, candidates in candidates_by_category.items():
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            size = _path_cleanup_size(path)
            if not dry_run:
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except FileNotFoundError:
                    continue
            deleted.append({"path": str(path), "bytes": size, "category": category})
            deleted_by_category[category]["count"] += 1
            deleted_by_category[category]["bytes"] += size
    database_compaction = (
        _compact_database_candidates(policy, dry_run=dry_run)
        if bool(compact_databases)
        else {
            "enabled": False,
            "dry_run": bool(dry_run),
            "candidate_count": 0,
            "estimated_reclaim_bytes": 0,
            "compacted_count": 0,
            "error_count": 0,
            "results": [],
        }
    )
    for row in deleted_by_category.values():
        row["size_mb"] = _size_mb(row.get("bytes"))
    for row in rotated_by_category.values():
        row["size_mb"] = _size_mb(row.get("bytes"))
        row["kept_size_mb"] = _size_mb(row.get("kept_bytes"))

    deleted_bytes = sum(int(row["bytes"]) for row in deleted)
    rotated_bytes = sum(int(row["bytes"]) for row in rotated)
    would_delete_count = len(deleted) if dry_run else 0
    would_delete_bytes = deleted_bytes if dry_run else 0
    actual_deleted_count = 0 if dry_run else len(deleted)
    actual_deleted_bytes = 0 if dry_run else deleted_bytes
    would_rotate_count = len(rotated) if dry_run else 0
    would_rotate_bytes = rotated_bytes if dry_run else 0
    actual_rotated_count = 0 if dry_run else len(rotated)
    actual_rotated_bytes = 0 if dry_run else rotated_bytes

    return {
        "status": "ok",
        "dry_run": bool(dry_run),
        "would_delete_count": would_delete_count,
        "would_delete_bytes": would_delete_bytes,
        "would_delete_size_mb": _size_mb(would_delete_bytes),
        "actual_deleted_count": actual_deleted_count,
        "actual_deleted_bytes": actual_deleted_bytes,
        "actual_deleted_size_mb": _size_mb(actual_deleted_bytes),
        "deleted_count": len(deleted),
        "deleted_bytes": deleted_bytes,
        "deleted_size_mb": _size_mb(deleted_bytes),
        "deleted_by_category": deleted_by_category,
        "deleted": [_with_size_mb(row) for row in deleted[:80]],
        "would_rotate_count": would_rotate_count,
        "would_rotate_bytes": would_rotate_bytes,
        "would_rotate_size_mb": _size_mb(would_rotate_bytes),
        "actual_rotated_count": actual_rotated_count,
        "actual_rotated_bytes": actual_rotated_bytes,
        "actual_rotated_size_mb": _size_mb(actual_rotated_bytes),
        "rotated_count": len(rotated),
        "rotated_bytes": rotated_bytes,
        "rotated_size_mb": _size_mb(rotated_bytes),
        "rotated_by_category": rotated_by_category,
        "rotated": [_with_size_mb(row) for row in rotated[:80]],
        "database_compaction": database_compaction,
    }
