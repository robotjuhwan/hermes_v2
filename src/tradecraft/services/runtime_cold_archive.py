from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


logger = logging.getLogger(__name__)

_ARCHIVE_VERSION = 1
_MANIFEST_NAME = "manifest-v1.json"
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ArchiveCandidateV1:
    category: str
    logical_scenario: str
    source_paths: tuple[Path, ...]
    restore_contract: dict[str, Any]
    row_count: int | None = None
    started_at: str | None = None
    ended_at: str | None = None


@dataclass(frozen=True)
class ArchiveSourceV1:
    original_path: Path
    member_name: str
    size_bytes: int
    sha256: str
    hot_size_bytes: int
    hot_sha256: str


@dataclass(frozen=True)
class ArchiveManifestEntryV1:
    version: int
    entry_id: str
    category: str
    logical_scenario: str
    sources: tuple[ArchiveSourceV1, ...]
    restore_contract: dict[str, Any]
    row_count: int | None
    started_at: str | None
    ended_at: str | None
    archive_path: Path
    archive_sha256: str
    archive_bytes: int
    created_at: str
    verified_at: str
    lifecycle: str


@dataclass(frozen=True)
class ArchiveResultV1:
    entry_id: str
    archive_path: Path
    verified: bool
    lifecycle: str
    reason: str | None = None


@dataclass(frozen=True)
class ArchiveVerificationV1:
    ok: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveRemovalResultV1:
    removed: bool
    reason: str | None
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class RestoreResultV1:
    restored: bool
    reason: str | None
    paths: tuple[Path, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_stream(handle: Any) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_component(value: str, *, fallback: str) -> str:
    normalized = _SAFE_COMPONENT.sub("_", value.strip()).strip("._")
    return normalized or fallback


def _safe_member_name(value: str) -> bool:
    member = PurePosixPath(value)
    return bool(value) and not member.is_absolute() and ".." not in member.parts


class RuntimeColdArchiveV1:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.manifest_path = self.root / _MANIFEST_NAME
        self._lock_path = self.root / ".manifest-v1.lock"

    @contextmanager
    def _manifest_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def archive(self, candidate: ArchiveCandidateV1) -> ArchiveResultV1:
        entry_id = self._entry_id()
        category = _safe_component(candidate.category, fallback="other")
        scenario = _safe_component(candidate.logical_scenario, fallback="default")
        archive_dir = self.root / category / scenario
        archive_dir.mkdir(parents=True, exist_ok=True)
        final_path = archive_dir / f"{entry_id}.tar.gz"
        temporary_path = archive_dir / f".{entry_id}.{uuid.uuid4().hex}.tmp"

        try:
            source_specs = self._candidate_source_specs(candidate.source_paths)
            with tempfile.TemporaryDirectory(
                prefix=f".{entry_id}.sources-",
                dir=archive_dir,
            ) as prepared_dir:
                sources, archive_sources = self._materialize_sources(
                    source_specs,
                    prepared_root=Path(prepared_dir),
                    sqlite_backup=bool(
                        candidate.restore_contract.get("sqlite_backup", False)
                    ),
                )
                with temporary_path.open("wb") as raw_handle:
                    with tarfile.open(fileobj=raw_handle, mode="w:gz") as tar:
                        for source, archive_source in zip(sources, archive_sources):
                            tar.add(
                                archive_source,
                                arcname=source.member_name,
                                recursive=False,
                            )
                    raw_handle.flush()
                    os.fsync(raw_handle.fileno())

                archive_sha256 = _sha256_file(temporary_path)
                verification = self._verify_archive_file(
                    temporary_path,
                    sources,
                    archive_sha256,
                )
                if not verification.ok:
                    return ArchiveResultV1(
                        entry_id=entry_id,
                        archive_path=temporary_path,
                        verified=False,
                        lifecycle="failed",
                        reason=";".join(verification.errors),
                    )

                os.replace(temporary_path, final_path)
            _fsync_directory(archive_dir)
            now = _utc_now()
            entry = ArchiveManifestEntryV1(
                version=_ARCHIVE_VERSION,
                entry_id=entry_id,
                category=candidate.category,
                logical_scenario=candidate.logical_scenario,
                sources=sources,
                restore_contract=dict(candidate.restore_contract),
                row_count=candidate.row_count,
                started_at=candidate.started_at,
                ended_at=candidate.ended_at,
                archive_path=final_path,
                archive_sha256=archive_sha256,
                archive_bytes=final_path.stat().st_size,
                created_at=now,
                verified_at=now,
                lifecycle="verified_hot_retained",
            )
            final_verification = self.verify(entry)
            if not final_verification.ok:
                return ArchiveResultV1(
                    entry_id=entry_id,
                    archive_path=final_path,
                    verified=False,
                    lifecycle="failed",
                    reason=";".join(final_verification.errors),
                )
            with self._manifest_lock():
                manifest = self._load_manifest()
                manifest["entries"].append(self._entry_to_payload(entry))
                _atomic_json_write(self.manifest_path, manifest)
            return ArchiveResultV1(
                entry_id=entry_id,
                archive_path=final_path,
                verified=True,
                lifecycle=entry.lifecycle,
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    def entry(self, entry_id: str) -> ArchiveManifestEntryV1:
        manifest = self._load_manifest()
        for payload in manifest["entries"]:
            if str(payload.get("entry_id")) == entry_id:
                return self._entry_from_payload(payload)
        raise KeyError(entry_id)

    def entries(self) -> tuple[ArchiveManifestEntryV1, ...]:
        manifest = self._load_manifest()
        return tuple(self._entry_from_payload(row) for row in manifest["entries"])

    def verify(
        self,
        entry: ArchiveManifestEntryV1,
    ) -> ArchiveVerificationV1:
        return self._verify_archive_file(
            entry.archive_path,
            entry.sources,
            entry.archive_sha256,
        )

    def mark_hot_removed(
        self,
        entry_id: str,
        source_paths: tuple[Path, ...],
    ) -> ArchiveRemovalResultV1:
        with self._manifest_lock():
            manifest = self._load_manifest()
            index = next(
                (
                    position
                    for position, row in enumerate(manifest["entries"])
                    if str(row.get("entry_id")) == entry_id
                ),
                None,
            )
            if index is None:
                return ArchiveRemovalResultV1(False, "entry_not_found", ())
            entry = self._entry_from_payload(manifest["entries"][index])
            if entry.lifecycle == "hot_removed":
                return ArchiveRemovalResultV1(True, None, ())
            if not self.verify(entry).ok:
                return ArchiveRemovalResultV1(
                    False,
                    "archive_verification_failed",
                    (),
                )

            requested = tuple(Path(path).expanduser().resolve() for path in source_paths)
            recorded = tuple(source.original_path for source in entry.sources)
            if requested != recorded:
                return ArchiveRemovalResultV1(False, "source_path_mismatch", ())
            if not self._hot_sources_match(entry.sources):
                return ArchiveRemovalResultV1(False, "hot_source_mismatch", ())

            removed: list[Path] = []
            try:
                for path in requested:
                    path.unlink()
                    removed.append(path)
            except OSError as exc:
                logger.error("cold archive hot removal failed for %s: %s", entry_id, exc)
                return ArchiveRemovalResultV1(
                    False,
                    f"hot_removal_failed:{type(exc).__name__}",
                    tuple(removed),
                )

            updated = replace(entry, lifecycle="hot_removed")
            manifest["entries"][index] = self._entry_to_payload(updated)
            _atomic_json_write(self.manifest_path, manifest)
            return ArchiveRemovalResultV1(True, None, tuple(removed))

    def restore(self, entry_id: str, destination: Path) -> RestoreResultV1:
        try:
            entry = self.entry(entry_id)
        except KeyError:
            return RestoreResultV1(False, "entry_not_found", ())
        if not self.verify(entry).ok:
            return RestoreResultV1(False, "archive_verification_failed", ())

        destination = destination.expanduser().resolve()
        if destination.exists() and (
            not destination.is_dir() or any(destination.iterdir())
        ):
            return RestoreResultV1(False, "destination_collision", ())
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.restore-",
                dir=destination.parent,
            )
        )
        try:
            with tarfile.open(entry.archive_path, mode="r:gz") as tar:
                by_name = {source.member_name: source for source in entry.sources}
                for member in tar.getmembers():
                    if (
                        member.name not in by_name
                        or not member.isfile()
                        or not _safe_member_name(member.name)
                    ):
                        return RestoreResultV1(False, "unsafe_archive_member", ())
                    source_handle = tar.extractfile(member)
                    if source_handle is None:
                        return RestoreResultV1(False, "archive_member_unreadable", ())
                    target = temporary / Path(*PurePosixPath(member.name).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source_handle, target.open("wb") as target_handle:
                        shutil.copyfileobj(source_handle, target_handle)
                        target_handle.flush()
                        os.fsync(target_handle.fileno())

            for source in entry.sources:
                restored_path = temporary / Path(
                    *PurePosixPath(source.member_name).parts
                )
                if (
                    not restored_path.is_file()
                    or restored_path.stat().st_size != source.size_bytes
                    or _sha256_file(restored_path) != source.sha256
                ):
                    return RestoreResultV1(False, "restored_hash_mismatch", ())
            if destination.exists():
                destination.rmdir()
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
            restored_paths = tuple(
                destination / Path(*PurePosixPath(source.member_name).parts)
                for source in entry.sources
            )
            return RestoreResultV1(True, None, restored_paths)
        except (OSError, tarfile.TarError) as exc:
            logger.error("cold archive restore failed for %s: %s", entry_id, exc)
            return RestoreResultV1(
                False,
                f"restore_failed:{type(exc).__name__}",
                (),
            )
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def status(self) -> dict[str, Any]:
        entries = self.entries()
        corrupt = [entry.entry_id for entry in entries if not self.verify(entry).ok]
        unverified = [
            entry.entry_id
            for entry in entries
            if entry.lifecycle not in {"verified_hot_retained", "hot_removed"}
        ]
        return {
            "status": "warning" if corrupt or unverified else "ok",
            "version": _ARCHIVE_VERSION,
            "root": str(self.root),
            "entry_count": len(entries),
            "archive_bytes": sum(entry.archive_bytes for entry in entries),
            "corrupt_entry_ids": corrupt,
            "unverified_entry_ids": unverified,
        }

    def _candidate_source_specs(
        self,
        source_paths: tuple[Path, ...],
    ) -> tuple[tuple[Path, str], ...]:
        if not source_paths:
            raise ValueError("archive candidate requires at least one source")
        resolved = tuple(path.expanduser().resolve() for path in source_paths)
        if any(not path.is_file() or path.is_symlink() for path in resolved):
            raise ValueError("archive sources must be existing regular files")
        common_parent = Path(os.path.commonpath([str(path.parent) for path in resolved]))
        member_names = tuple(path.relative_to(common_parent).as_posix() for path in resolved)
        if len(set(member_names)) != len(member_names):
            raise ValueError("archive member names must be unique")
        if any(not _safe_member_name(name) for name in member_names):
            raise ValueError("archive member name is unsafe")
        return tuple(zip(resolved, member_names))

    def _materialize_sources(
        self,
        source_specs: tuple[tuple[Path, str], ...],
        *,
        prepared_root: Path,
        sqlite_backup: bool,
    ) -> tuple[tuple[ArchiveSourceV1, ...], tuple[Path, ...]]:
        sources: list[ArchiveSourceV1] = []
        archive_paths: list[Path] = []
        for original_path, member_name in source_specs:
            archive_path = original_path
            if sqlite_backup and original_path.suffix in {".db", ".sqlite", ".sqlite3"}:
                archive_path = prepared_root / Path(
                    *PurePosixPath(member_name).parts
                )
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                with (
                    sqlite3.connect(str(original_path), timeout=5.0) as source_conn,
                    sqlite3.connect(str(archive_path), timeout=5.0) as target_conn,
                ):
                    source_conn.backup(target_conn)
                    integrity = target_conn.execute("PRAGMA integrity_check").fetchone()
                    if not integrity or str(integrity[0]).lower() != "ok":
                        raise sqlite3.DatabaseError(
                            f"SQLite archive snapshot failed integrity: {original_path}"
                        )
            sources.append(
                ArchiveSourceV1(
                    original_path=original_path,
                    member_name=member_name,
                    size_bytes=archive_path.stat().st_size,
                    sha256=_sha256_file(archive_path),
                    hot_size_bytes=original_path.stat().st_size,
                    hot_sha256=_sha256_file(original_path),
                )
            )
            archive_paths.append(archive_path)
        return tuple(sources), tuple(archive_paths)

    def _verify_archive_file(
        self,
        archive_path: Path,
        sources: tuple[ArchiveSourceV1, ...],
        expected_archive_sha256: str,
    ) -> ArchiveVerificationV1:
        errors: list[str] = []
        if not archive_path.is_file():
            return ArchiveVerificationV1(False, ("archive_missing",))
        if _sha256_file(archive_path) != expected_archive_sha256:
            errors.append("archive_sha256_mismatch")
        expected = {source.member_name: source for source in sources}
        try:
            with tarfile.open(archive_path, mode="r:gz") as tar:
                members = tar.getmembers()
                actual_names = [member.name for member in members]
                if len(actual_names) != len(set(actual_names)):
                    errors.append("duplicate_archive_member")
                if set(actual_names) != set(expected):
                    errors.append("archive_member_set_mismatch")
                for member in members:
                    if not member.isfile() or not _safe_member_name(member.name):
                        errors.append(f"unsafe_archive_member:{member.name}")
                        continue
                    source = expected.get(member.name)
                    if source is None:
                        continue
                    handle = tar.extractfile(member)
                    if handle is None:
                        errors.append(f"archive_member_unreadable:{member.name}")
                        continue
                    with handle:
                        digest, size = _sha256_stream(handle)
                    if size != source.size_bytes:
                        errors.append(f"member_size_mismatch:{member.name}")
                    if digest != source.sha256:
                        errors.append(f"member_sha256_mismatch:{member.name}")
        except (OSError, tarfile.TarError):
            errors.append("archive_unreadable")
        return ArchiveVerificationV1(not errors, tuple(dict.fromkeys(errors)))

    def _hot_sources_match(self, sources: tuple[ArchiveSourceV1, ...]) -> bool:
        return all(
            source.original_path.is_file()
            and source.original_path.stat().st_size == source.hot_size_bytes
            and _sha256_file(source.original_path) == source.hot_sha256
            for source in sources
        )

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"version": _ARCHIVE_VERSION, "entries": []}
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if payload.get("version") != _ARCHIVE_VERSION:
            raise ValueError("unsupported cold archive manifest version")
        if not isinstance(payload.get("entries"), list):
            raise ValueError("invalid cold archive manifest entries")
        return payload

    def _entry_to_payload(self, entry: ArchiveManifestEntryV1) -> dict[str, Any]:
        payload = asdict(entry)
        payload["archive_path"] = entry.archive_path.relative_to(self.root).as_posix()
        payload["sources"] = [
            {
                **asdict(source),
                "original_path": str(source.original_path),
            }
            for source in entry.sources
        ]
        return payload

    def _entry_from_payload(self, payload: dict[str, Any]) -> ArchiveManifestEntryV1:
        archive_path = Path(str(payload["archive_path"]))
        if archive_path.is_absolute() or ".." in archive_path.parts:
            raise ValueError("invalid archive path in manifest")
        return ArchiveManifestEntryV1(
            version=int(payload["version"]),
            entry_id=str(payload["entry_id"]),
            category=str(payload["category"]),
            logical_scenario=str(payload["logical_scenario"]),
            sources=tuple(
                ArchiveSourceV1(
                    original_path=Path(str(source["original_path"])).resolve(),
                    member_name=str(source["member_name"]),
                    size_bytes=int(source["size_bytes"]),
                    sha256=str(source["sha256"]),
                    hot_size_bytes=int(
                        source.get("hot_size_bytes", source["size_bytes"])
                    ),
                    hot_sha256=str(source.get("hot_sha256", source["sha256"])),
                )
                for source in payload["sources"]
            ),
            restore_contract=dict(payload["restore_contract"]),
            row_count=(
                int(payload["row_count"])
                if payload.get("row_count") is not None
                else None
            ),
            started_at=payload.get("started_at"),
            ended_at=payload.get("ended_at"),
            archive_path=self.root / archive_path,
            archive_sha256=str(payload["archive_sha256"]),
            archive_bytes=int(payload["archive_bytes"]),
            created_at=str(payload["created_at"]),
            verified_at=str(payload["verified_at"]),
            lifecycle=str(payload["lifecycle"]),
        )

    def _entry_id(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{timestamp}-{uuid.uuid4().hex[:12]}"
