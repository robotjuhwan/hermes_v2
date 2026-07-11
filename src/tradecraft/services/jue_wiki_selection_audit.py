from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from tradecraft.services.runtime_cold_archive import (
    ArchiveCandidateV1,
    RuntimeColdArchiveV1,
)


_COLUMNS = (
    "run_id",
    "page_id",
    "rank",
    "score",
    "reasons_json",
    "penalties_json",
    "char_count",
    "included",
    "created_at",
)


@dataclass(frozen=True)
class SelectionCompactionV1:
    exported_keys: tuple[tuple[str, str], ...]
    deleted_keys: tuple[tuple[str, str], ...]
    exported_count: int
    deleted_count: int
    entry_ids: tuple[str, ...]
    verified: bool
    dry_run: bool


@dataclass(frozen=True)
class SelectionRestoreV1:
    restored: bool
    reason: str | None
    path: Path
    row_count: int


def _utc_text(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row_bytes(row: dict[str, Any]) -> bytes:
    return (
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


class JueWikiSelectionAuditStore:
    def __init__(self, database_path: Path | str, cold_root: Path | str) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.cold_root = Path(cold_root).expanduser().resolve()
        self.archive_root = self.cold_root / "jue-selection"
        self.manifest_path = self.archive_root / "manifest-v1.json"
        self._lock_path = self.archive_root / ".manifest-v1.lock"

    def record_run(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        target_scope: str,
        request: dict[str, Any],
        budget_report: dict[str, Any],
        selected_pages: list[dict[str, Any]],
        rejected_pages: list[dict[str, Any]],
        char_count: int,
        max_chars: int,
        status: str,
        error_message: str,
        created_at: str,
    ) -> None:
        def dumps(value: Any) -> str:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        conn.execute(
            """
            INSERT OR REPLACE INTO wiki_selection_runs (
                run_id, target_scope, request_json, budget_report_json,
                selected_count, rejected_count, char_count, max_chars, status,
                error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                target_scope,
                dumps(request),
                dumps(budget_report),
                len(selected_pages),
                len(rejected_pages),
                int(char_count),
                int(max_chars),
                status,
                error_message,
                created_at,
            ),
        )
        conn.execute("DELETE FROM wiki_selection_pages WHERE run_id = ?", (run_id,))
        for included, pages in ((1, selected_pages), (0, rejected_pages)):
            for page in pages:
                penalties = page.get("penalties") or []
                if not included and not penalties:
                    penalties = [page.get("reason")]
                conn.execute(
                    """
                    INSERT INTO wiki_selection_pages (
                        run_id, page_id, rank, score, reasons_json,
                        penalties_json, char_count, included, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(page.get("page_id") or ""),
                        int(page.get("rank") or 0),
                        float(page.get("score") or 0.0),
                        dumps(page.get("reasons") or []),
                        dumps(penalties),
                        int(page.get("char_count") or 0),
                        included,
                        created_at,
                    ),
                )

    def compact_rejected(
        self,
        *,
        cutoff: datetime,
        apply: bool,
        before_delete: Callable[[], Any] | None = None,
    ) -> SelectionCompactionV1:
        cutoff_text = _utc_text(cutoff)
        count, key_sample = self._rejected_summary(cutoff_text)
        if not apply or count == 0:
            return SelectionCompactionV1(
                exported_keys=key_sample,
                deleted_keys=(),
                exported_count=count,
                deleted_count=0,
                entry_ids=(),
                verified=False,
                dry_run=not apply,
            )

        entries = [
            self._publish_partition(day, cutoff_text)
            for day in self._rejected_days(cutoff_text)
        ]

        if before_delete is not None:
            before_delete()
        deleted_count = 0
        deleted_sample: list[tuple[str, str]] = []
        for entry in entries:
            partition_count, partition_sample = self._delete_partition(
                entry,
                cutoff_text=cutoff_text,
            )
            deleted_count += partition_count
            deleted_sample.extend(partition_sample[: max(100 - len(deleted_sample), 0)])

        self._advance_lifecycle(tuple(entry["entry_id"] for entry in entries))
        return SelectionCompactionV1(
            exported_keys=key_sample,
            deleted_keys=tuple(deleted_sample),
            exported_count=count,
            deleted_count=deleted_count,
            entry_ids=tuple(entry["entry_id"] for entry in entries),
            verified=True,
            dry_run=False,
        )

    def historical_pages(self, run_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self._connect() as conn:
            hot_rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM wiki_selection_pages "
                "WHERE run_id = ? ORDER BY included DESC, rank ASC, page_id ASC",
                (run_id,),
            ).fetchall()
            rows.extend(dict(row) for row in hot_rows)

        seen = {(str(row["run_id"]), str(row["page_id"])) for row in rows}
        for entry in self._manifest().get("entries", []):
            if entry.get("lifecycle") not in {
                "verified_hot_retained",
                "hot_removed",
            }:
                continue
            for row in self._iter_partition_rows(entry):
                key = (str(row["run_id"]), str(row["page_id"]))
                if key in seen or key[0] != run_id:
                    continue
                rows.append(row)
                seen.add(key)
        return rows

    def included_pages(
        self,
        conn: sqlite3.Connection,
        run_id: str,
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT page_id, rank, score, reasons_json, penalties_json, char_count
            FROM wiki_selection_pages
            WHERE run_id = ? AND included = 1
            ORDER BY rank ASC, page_id ASC
            """,
            (run_id,),
        ).fetchall()

    def restore_partition(
        self,
        entry_id: str,
        destination: Path,
    ) -> SelectionRestoreV1:
        entry = next(
            (
                row
                for row in self._manifest().get("entries", [])
                if str(row.get("entry_id")) == entry_id
            ),
            None,
        )
        destination = destination.expanduser().resolve()
        output = destination / f"{entry_id}.jsonl"
        if entry is None:
            return SelectionRestoreV1(False, "entry_not_found", output, 0)
        if destination.exists() and (
            not destination.is_dir() or any(destination.iterdir())
        ):
            return SelectionRestoreV1(False, "destination_collision", output, 0)
        self._verify_partition_entry(entry)
        destination.mkdir(parents=True, exist_ok=True)
        row_count = 0
        with (
            gzip.open(self._entry_path(entry, "archive_path"), "rb") as source,
            output.open("wb") as handle,
        ):
            for block in iter(lambda: source.read(1024 * 1024), b""):
                handle.write(block)
                row_count += block.count(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        if row_count != int(entry["row_count"]):
            output.unlink(missing_ok=True)
            return SelectionRestoreV1(False, "restored_row_count_mismatch", output, 0)
        return SelectionRestoreV1(True, None, output, row_count)

    def status(self) -> dict[str, Any]:
        entries = list(self._manifest().get("entries", []))
        corrupt: list[str] = []
        archive_bytes = 0
        for entry in entries:
            archive_bytes += int(entry.get("archive_bytes") or 0) + int(
                entry.get("keyset_archive_bytes") or 0
            )
            try:
                self._verify_partition_entry(entry)
            except (OSError, ValueError, sqlite3.Error, gzip.BadGzipFile):
                corrupt.append(str(entry.get("entry_id") or ""))
        return {
            "status": "warning" if corrupt else "ok",
            "entry_count": len(entries),
            "archive_bytes": archive_bytes,
            "corrupt_entry_ids": corrupt,
        }

    def vacuum_hot_database(
        self,
        *,
        min_free_bytes: int = 4 * 1024 * 1024,
    ) -> dict[str, Any]:
        before_bytes = self.database_path.stat().st_size
        with self._connect() as conn:
            conn.execute("PRAGMA busy_timeout = 30000")
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0] or 0)
            freelist_count = int(
                conn.execute("PRAGMA freelist_count").fetchone()[0] or 0
            )
            pre_integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not pre_integrity or str(pre_integrity[0]).lower() != "ok":
            return {
                "status": "error",
                "reason": "pre_compaction_integrity_failed",
                "before_bytes": before_bytes,
                "after_bytes": before_bytes,
                "backup_verified": False,
            }
        free_bytes = page_size * freelist_count
        if free_bytes < max(int(min_free_bytes), 0):
            return {
                "status": "skipped",
                "reason": "insufficient_reclaimable_space",
                "before_bytes": before_bytes,
                "after_bytes": before_bytes,
                "reclaimable_bytes": free_bytes,
                "backup_verified": False,
            }
        archive = RuntimeColdArchiveV1(self.cold_root)
        backup = archive.archive(
            ArchiveCandidateV1(
                category="jue-wiki-backup",
                logical_scenario="pre-compaction",
                source_paths=(self.database_path,),
                restore_contract={
                    "kind": "sqlite-backup-v1",
                    "sqlite_backup": True,
                    "purpose": "pre-selection-audit-vacuum",
                },
            )
        )
        if not backup.verified:
            return {
                "status": "error",
                "reason": backup.reason or "backup_verification_failed",
                "before_bytes": before_bytes,
                "after_bytes": before_bytes,
                "backup_verified": False,
            }
        try:
            with self._connect() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                conn.execute("VACUUM")
                post_integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not post_integrity or str(post_integrity[0]).lower() != "ok":
                return {
                    "status": "error",
                    "reason": "post_compaction_integrity_failed",
                    "before_bytes": before_bytes,
                    "after_bytes": self.database_path.stat().st_size,
                    "backup_verified": True,
                    "backup_entry_id": backup.entry_id,
                }
        except sqlite3.OperationalError as exc:
            return {
                "status": "skipped",
                "reason": f"sqlite_lock_or_vacuum_error:{exc}",
                "before_bytes": before_bytes,
                "after_bytes": self.database_path.stat().st_size,
                "backup_verified": True,
                "backup_entry_id": backup.entry_id,
            }
        return {
            "status": "ok",
            "before_bytes": before_bytes,
            "after_bytes": self.database_path.stat().st_size,
            "reclaimed_bytes": max(
                before_bytes - self.database_path.stat().st_size,
                0,
            ),
            "backup_verified": True,
            "backup_entry_id": backup.entry_id,
        }

    def _rejected_summary(
        self,
        cutoff_text: str,
    ) -> tuple[int, tuple[tuple[str, str], ...]]:
        with self._connect() as conn:
            count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM wiki_selection_pages
                    WHERE included = 0 AND created_at < ?
                    """,
                    (cutoff_text,),
                ).fetchone()[0]
                or 0
            )
            sample = conn.execute(
                """
                SELECT run_id, page_id FROM wiki_selection_pages
                WHERE included = 0 AND created_at < ?
                ORDER BY created_at ASC, run_id ASC, page_id ASC
                LIMIT 100
                """,
                (cutoff_text,),
            ).fetchall()
        return count, tuple((str(row[0]), str(row[1])) for row in sample)

    def _rejected_days(self, cutoff_text: str) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT substr(created_at, 1, 10) AS archive_day
                FROM wiki_selection_pages
                WHERE included = 0 AND created_at < ?
                ORDER BY archive_day ASC
                """,
                (cutoff_text,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows if str(row[0] or ""))

    def _publish_partition(
        self,
        day: str,
        cutoff_text: str,
    ) -> dict[str, Any]:
        entry_id = f"{day}-{uuid.uuid4().hex[:16]}"
        directory = self.archive_root / day
        directory.mkdir(parents=True, exist_ok=True)
        archive_path = directory / f"{entry_id}.jsonl.gz"
        keyset_path = directory / f"{entry_id}.keys.jsonl.gz"
        temporary = directory / f".{entry_id}.{uuid.uuid4().hex}.rows.tmp"
        keyset_temporary = directory / f".{entry_id}.{uuid.uuid4().hex}.keys.tmp"
        try:
            stream_digest = hashlib.sha256()
            keyset_digest = hashlib.sha256()
            row_count = 0
            first_key: list[str] = []
            last_key: list[str] = []
            with (
                self._connect() as conn,
                temporary.open("wb") as raw_handle,
                keyset_temporary.open("wb") as key_raw_handle,
                gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as zipped,
                gzip.GzipFile(fileobj=key_raw_handle, mode="wb", mtime=0) as key_zipped,
            ):
                cursor = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM wiki_selection_pages "
                    "WHERE included = 0 AND created_at < ? "
                    "AND substr(created_at, 1, 10) = ? "
                    "ORDER BY created_at ASC, run_id ASC, page_id ASC",
                    (cutoff_text, day),
                )
                while True:
                    batch = cursor.fetchmany(1000)
                    if not batch:
                        break
                    for sqlite_row in batch:
                        row = dict(sqlite_row)
                        encoded = _row_bytes(row)
                        key = [str(row["run_id"]), str(row["page_id"])]
                        key_row = {
                            "run_id": key[0],
                            "page_id": key[1],
                            "row_sha256": _sha256(encoded),
                        }
                        key_encoded = _row_bytes(key_row)
                        zipped.write(encoded)
                        key_zipped.write(key_encoded)
                        stream_digest.update(encoded)
                        keyset_digest.update(key_encoded)
                        row_count += 1
                        if not first_key:
                            first_key = key
                        last_key = key
                zipped.close()
                key_zipped.close()
                raw_handle.flush()
                key_raw_handle.flush()
                os.fsync(raw_handle.fileno())
                os.fsync(key_raw_handle.fileno())
            if row_count <= 0:
                raise sqlite3.IntegrityError("selection archive partition is empty")
            archive_sha256 = self._sha256_file(temporary)
            keyset_archive_sha256 = self._sha256_file(keyset_temporary)
            expected = {
                "row_count": row_count,
                "stream_sha256": stream_digest.hexdigest(),
                "keyset_sha256": keyset_digest.hexdigest(),
                "archive_sha256": archive_sha256,
                "keyset_archive_sha256": keyset_archive_sha256,
            }
            self._verify_partition_paths(
                archive_path=temporary,
                keyset_path=keyset_temporary,
                expected=expected,
            )
            os.replace(temporary, archive_path)
            os.replace(keyset_temporary, keyset_path)
            entry = {
                "version": 1,
                "entry_id": entry_id,
                "day": day,
                "archive_path": archive_path.relative_to(self.cold_root).as_posix(),
                "keyset_path": keyset_path.relative_to(self.cold_root).as_posix(),
                "row_count": row_count,
                "first_key": first_key,
                "last_key": last_key,
                "stream_sha256": stream_digest.hexdigest(),
                "keyset_sha256": keyset_digest.hexdigest(),
                "archive_sha256": archive_sha256,
                "keyset_archive_sha256": keyset_archive_sha256,
                "archive_bytes": archive_path.stat().st_size,
                "keyset_archive_bytes": keyset_path.stat().st_size,
                "created_at": _utc_text(datetime.now(timezone.utc)),
                "lifecycle": "verified_hot_retained",
            }
            with self._manifest_lock():
                manifest = self._manifest()
                manifest["entries"].append(entry)
                _atomic_json_write(self.manifest_path, manifest)
            return entry
        finally:
            temporary.unlink(missing_ok=True)
            keyset_temporary.unlink(missing_ok=True)

    def _delete_partition(
        self,
        entry: dict[str, Any],
        *,
        cutoff_text: str,
    ) -> tuple[int, tuple[tuple[str, str], ...]]:
        keyset_path = self._entry_path(entry, "keyset_path")
        sample: list[tuple[str, str]] = []
        inserted = 0
        with self._connect() as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    CREATE TEMP TABLE selection_archive_keys (
                        run_id TEXT NOT NULL,
                        page_id TEXT NOT NULL,
                        row_sha256 TEXT NOT NULL,
                        PRIMARY KEY (run_id, page_id)
                    ) WITHOUT ROWID
                    """
                )
                batch: list[tuple[str, str, str]] = []
                with gzip.open(keyset_path, "rb") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        key = (str(row["run_id"]), str(row["page_id"]))
                        batch.append((*key, str(row["row_sha256"])))
                        if len(sample) < 100:
                            sample.append(key)
                        if len(batch) >= 1000:
                            conn.executemany(
                                "INSERT INTO selection_archive_keys VALUES (?, ?, ?)",
                                batch,
                            )
                            inserted += len(batch)
                            batch.clear()
                    if batch:
                        conn.executemany(
                            "INSERT INTO selection_archive_keys VALUES (?, ?, ?)",
                            batch,
                        )
                        inserted += len(batch)
                if inserted != int(entry["row_count"]):
                    raise sqlite3.IntegrityError(
                        "selection audit keyset row count changed"
                    )
                current_count = 0
                cursor = conn.execute(
                    f"SELECT {', '.join(f'p.{column}' for column in _COLUMNS)}, "
                    "k.row_sha256 FROM wiki_selection_pages p "
                    "JOIN selection_archive_keys k "
                    "ON k.run_id = p.run_id AND k.page_id = p.page_id "
                    "ORDER BY p.created_at, p.run_id, p.page_id"
                )
                for current in cursor:
                    row = {column: current[column] for column in _COLUMNS}
                    if _sha256(_row_bytes(row)) != str(current["row_sha256"]):
                        raise sqlite3.IntegrityError(
                            "selection audit deletion row hash changed"
                        )
                    current_count += 1
                if current_count != inserted:
                    raise sqlite3.IntegrityError(
                        "selection audit deletion keyset changed"
                    )
                delete_cursor = conn.execute(
                    """
                    DELETE FROM wiki_selection_pages
                    WHERE included = 0 AND created_at < ? AND EXISTS (
                        SELECT 1 FROM selection_archive_keys k
                        WHERE k.run_id = wiki_selection_pages.run_id
                          AND k.page_id = wiki_selection_pages.page_id
                    )
                    """,
                    (cutoff_text,),
                )
                if int(delete_cursor.rowcount) != inserted:
                    raise sqlite3.IntegrityError(
                        "selection audit deleted row count changed"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return inserted, tuple(sample)

    def _entry_path(self, entry: dict[str, Any], field: str) -> Path:
        relative = Path(str(entry[field]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe selection archive path")
        return self.cold_root / relative

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _verify_partition_paths(
        self,
        *,
        archive_path: Path,
        keyset_path: Path,
        expected: dict[str, Any],
    ) -> None:
        if self._sha256_file(archive_path) != str(expected["archive_sha256"]):
            raise sqlite3.IntegrityError("selection archive checksum mismatch")
        if self._sha256_file(keyset_path) != str(
            expected["keyset_archive_sha256"]
        ):
            raise sqlite3.IntegrityError("selection keyset checksum mismatch")
        stream_digest = hashlib.sha256()
        keyset_digest = hashlib.sha256()
        row_count = 0
        with (
            gzip.open(archive_path, "rb") as rows_handle,
            gzip.open(keyset_path, "rb") as keys_handle,
        ):
            while True:
                row_line = rows_handle.readline()
                key_line = keys_handle.readline()
                if not row_line and not key_line:
                    break
                if not row_line or not key_line:
                    raise sqlite3.IntegrityError(
                        "selection archive and keyset lengths differ"
                    )
                row = json.loads(row_line)
                key = json.loads(key_line)
                if (
                    str(row["run_id"]) != str(key["run_id"])
                    or str(row["page_id"]) != str(key["page_id"])
                    or _sha256(row_line) != str(key["row_sha256"])
                ):
                    raise sqlite3.IntegrityError(
                        "selection archive keyset verification failed"
                    )
                stream_digest.update(row_line)
                keyset_digest.update(key_line)
                row_count += 1
        if row_count != int(expected["row_count"]):
            raise sqlite3.IntegrityError("selection archive row count mismatch")
        if stream_digest.hexdigest() != str(expected["stream_sha256"]):
            raise sqlite3.IntegrityError("selection archive stream checksum mismatch")
        if keyset_digest.hexdigest() != str(expected["keyset_sha256"]):
            raise sqlite3.IntegrityError("selection keyset stream checksum mismatch")

    def _verify_partition_entry(self, entry: dict[str, Any]) -> None:
        self._verify_partition_paths(
            archive_path=self._entry_path(entry, "archive_path"),
            keyset_path=self._entry_path(entry, "keyset_path"),
            expected=entry,
        )

    def _iter_partition_rows(self, entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self._verify_partition_entry(entry)
        with gzip.open(self._entry_path(entry, "archive_path"), "rb") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    def _advance_lifecycle(self, entry_ids: tuple[str, ...]) -> None:
        with self._manifest_lock():
            manifest = self._manifest()
            for entry in manifest["entries"]:
                if str(entry.get("entry_id")) in entry_ids:
                    entry["lifecycle"] = "hot_removed"
                    entry["hot_removed_at"] = _utc_text(datetime.now(timezone.utc))
            _atomic_json_write(self.manifest_path, manifest)

    def _manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"version": 1, "entries": []}
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("entries"), list):
            raise ValueError("invalid selection archive manifest")
        return payload

    @contextmanager
    def _manifest_lock(self) -> Iterator[None]:
        self.archive_root.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
