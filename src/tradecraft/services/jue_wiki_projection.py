from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from tradecraft.services.jue_wiki_contract import JueWikiPageV3, WikiSnapshotV1


INDEX_FILENAME = "wiki-search.sqlite3"
GENERATION_MARKER = ".jue-wiki-generation.json"
GENERATION_SCHEMA_VERSION = "jue_wiki_projection_v1"
_UNSUPPORTED_ERRNOS = frozenset(
    {
        errno.ENOSYS,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.EINVAL,
    }
)


class WikiProjectionError(RuntimeError):
    pass


class WikiProjectionRecoveryError(WikiProjectionError):
    pass


@dataclass(frozen=True, slots=True)
class WikiProjectionResultV1:
    snapshot_id: str
    projection_root: Path
    row_hashes: tuple[str, ...]
    cleanup_warnings: tuple[str, ...] = ()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_hash(payload: dict[str, str]) -> str:
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _page_filename(page_id: str) -> str:
    digest = hashlib.sha256(page_id.encode("utf-8")).hexdigest()
    return f"{digest}.md"


class JueWikiProjectionWriter:
    def __init__(
        self,
        projection_root: Path,
        containment_root: Path | None = None,
    ) -> None:
        root = Path(projection_root)
        if root == root.parent:
            raise ValueError("projection_root_must_not_be_filesystem_root")
        self.projection_root = root
        self.containment_root = (
            Path(containment_root) if containment_root is not None else None
        )
        self._containment_path: Path | None = None
        self._containment_identity: tuple[int, int] | None = None
        self._projection_parent_parts: tuple[str, ...] = ()
        if self.containment_root is not None:
            self._initialize_containment()

    @property
    def index_path(self) -> Path:
        return self.projection_root / INDEX_FILENAME

    def project(self, snapshot: WikiSnapshotV1) -> WikiProjectionResultV1:
        self._require_dir_fd_support()
        try:
            with self._open_projection_parent() as parent_fd:
                self._probe_dir_fd_support(parent_fd)
                return self._project_at(parent_fd, snapshot)
        except (AttributeError, NotImplementedError, TypeError) as exc:
            raise WikiProjectionError("projection_dir_fd_unsupported") from exc
        except OSError as exc:
            self._raise_if_unsupported(exc)
            raise

    def rebuild_index(self, snapshot: WikiSnapshotV1) -> WikiProjectionResultV1:
        return self.project(snapshot)

    def _project_at(
        self,
        parent_fd: int,
        snapshot: WikiSnapshotV1,
    ) -> WikiProjectionResultV1:
        self._validate_projection_pointer_at(parent_fd)
        old_target = self._read_pointer_at(parent_fd)
        generation_name, generation_fd = self._create_generation_at(parent_fd)
        pointer_name: str | None = None
        rollback_name: str | None = None
        durable = False
        preserve_generation = False
        cleanup_warnings: list[str] = []
        try:
            row_hashes = self._build_projection(
                parent_fd,
                generation_fd,
                snapshot,
            )
            self._write_text_at(
                generation_fd,
                GENERATION_MARKER,
                _json_dumps(
                    {
                        "schema_version": GENERATION_SCHEMA_VERSION,
                        "projection_name": self.projection_root.name,
                        "snapshot_id": snapshot.snapshot_id,
                    }
                )
                + "\n",
            )
            os.fsync(generation_fd)
            pointer_name = self._create_pointer_at(
                parent_fd,
                target=generation_name,
                prefix=f".{self.projection_root.name}.pointer-",
            )
            self._replace_at(
                parent_fd,
                pointer_name,
                self.projection_root.name,
            )
            pointer_name = None
            try:
                os.fsync(parent_fd)
            except Exception as publish_error:
                try:
                    if old_target is None:
                        os.unlink(self.projection_root.name, dir_fd=parent_fd)
                    else:
                        rollback_name = self._create_pointer_at(
                            parent_fd,
                            target=old_target,
                            prefix=f".{self.projection_root.name}.rollback-",
                        )
                        self._replace_at(
                            parent_fd,
                            rollback_name,
                            self.projection_root.name,
                        )
                        rollback_name = None
                    os.fsync(parent_fd)
                except Exception as rollback_error:
                    preserve_generation = True
                    raise WikiProjectionRecoveryError(
                        "projection_pointer_rollback_failed"
                    ) from rollback_error
                os.close(generation_fd)
                generation_fd = -1
                self._remove_owned_generation_at(
                    parent_fd,
                    generation_name,
                    require_marker=False,
                )
                raise publish_error
            durable = True
            if (
                old_target is not None
                and old_target != generation_name
                and self._is_owned_generation_name(old_target)
            ):
                try:
                    self._remove_owned_generation_at(
                        parent_fd,
                        old_target,
                        require_marker=True,
                    )
                except Exception:
                    cleanup_warnings.append("old_generation_cleanup_failed")
            return WikiProjectionResultV1(
                snapshot_id=snapshot.snapshot_id,
                projection_root=self.projection_root,
                row_hashes=row_hashes,
                cleanup_warnings=tuple(cleanup_warnings),
            )
        finally:
            if generation_fd >= 0:
                os.close(generation_fd)
            self._unlink_pointer_at(parent_fd, pointer_name)
            self._unlink_pointer_at(parent_fd, rollback_name)
            if not durable and not preserve_generation:
                try:
                    self._remove_owned_generation_at(
                        parent_fd,
                        generation_name,
                        require_marker=False,
                    )
                except OSError:
                    pass

    def _build_projection(
        self,
        parent_fd: int,
        generation_fd: int,
        snapshot: WikiSnapshotV1,
    ) -> tuple[str, ...]:
        os.mkdir("pages", 0o755, dir_fd=generation_fd)
        pages_fd = self._open_directory_at(generation_fd, "pages")
        try:
            page_files: list[tuple[JueWikiPageV3, str]] = []
            for page in sorted(snapshot.pages, key=lambda row: row.page_id):
                filename = _page_filename(page.page_id)
                self._write_text_at(pages_fd, filename, self._render_page(page))
                page_files.append((page, filename))
            os.fsync(pages_fd)
        finally:
            os.close(pages_fd)
        self._write_text_at(
            generation_fd,
            "index.md",
            self._render_index(snapshot, page_files),
        )
        self._write_text_at(
            generation_fd,
            "contradictions.md",
            self._render_contradictions(snapshot),
        )
        return self._build_and_install_index(
            parent_fd,
            generation_fd,
            snapshot,
        )

    def _build_and_install_index(
        self,
        parent_fd: int,
        generation_fd: int,
        snapshot: WikiSnapshotV1,
    ) -> tuple[str, ...]:
        staging_name, staging_fd = self._create_staging_at(parent_fd)
        try:
            index_fd = os.open(
                INDEX_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=staging_fd,
            )
            try:
                staging_index_path = Path(
                    f"/dev/fd/{staging_fd}/{INDEX_FILENAME}"
                )
                try:
                    expected_hashes = self._build_index(
                        staging_index_path,
                        snapshot,
                    )
                except sqlite3.OperationalError as exc:
                    if "unable to open database file" not in str(exc):
                        raise
                    os.ftruncate(index_fd, 0)
                    expected_hashes = self._build_index_via_fd(
                        index_fd,
                        snapshot,
                    )
                os.fsync(index_fd)
            finally:
                os.close(index_fd)
            staged_index_fd = os.open(
                INDEX_FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=staging_fd,
            )
            try:
                os.fsync(staged_index_fd)
                with sqlite3.connect(
                    f"file:/dev/fd/{staged_index_fd}?mode=ro",
                    uri=True,
                ) as conn:
                    staged_hashes = self._verified_index_hashes(conn)
            except sqlite3.Error as exc:
                raise WikiProjectionError(
                    "projection_dir_fd_unsupported"
                ) from exc
            finally:
                os.close(staged_index_fd)
            if staged_hashes != expected_hashes:
                raise WikiProjectionError("wiki_search_index_verification_failed")
            self._replace_between_directories(
                staging_fd,
                INDEX_FILENAME,
                generation_fd,
                INDEX_FILENAME,
            )
            installed_index_fd = os.open(
                INDEX_FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=generation_fd,
            )
            try:
                os.fsync(installed_index_fd)
                with sqlite3.connect(
                    f"file:/dev/fd/{installed_index_fd}?mode=ro",
                    uri=True,
                ) as conn:
                    installed_hashes = self._verified_index_hashes(conn)
            except sqlite3.Error as exc:
                raise WikiProjectionError(
                    "projection_dir_fd_unsupported"
                ) from exc
            finally:
                os.close(installed_index_fd)
            if installed_hashes != expected_hashes:
                raise WikiProjectionError("wiki_search_index_verification_failed")
            return installed_hashes
        finally:
            os.close(staging_fd)
            self._remove_staging_at(parent_fd, staging_name)

    def _build_index(
        self,
        index_path: Path,
        snapshot: WikiSnapshotV1,
    ) -> tuple[str, ...]:
        with sqlite3.connect(index_path) as conn:
            return self._populate_index(conn, snapshot)

    def _build_index_via_fd(
        self,
        index_fd: int,
        snapshot: WikiSnapshotV1,
    ) -> tuple[str, ...]:
        with sqlite3.connect(
            f"file:/dev/fd/{index_fd}?mode=rw",
            uri=True,
        ) as conn:
            conn.execute("PRAGMA journal_mode=MEMORY")
            return self._populate_index(conn, snapshot)

    def _populate_index(
        self,
        conn: sqlite3.Connection,
        snapshot: WikiSnapshotV1,
    ) -> tuple[str, ...]:
        rows = self._search_rows(snapshot)
        conn.execute(
            """
            CREATE VIRTUAL TABLE wiki_search USING fts5(
                page_id UNINDEXED,
                claim_id UNINDEXED,
                title,
                body,
                status UNINDEXED,
                scope UNINDEXED
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE wiki_search_rows (
                row_hash TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                claim_id TEXT NOT NULL
            )
            """
        )
        for row in rows:
            digest = _row_hash(row)
            conn.execute(
                """
                INSERT INTO wiki_search (
                    page_id, claim_id, title, body, status, scope
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["page_id"],
                    row["claim_id"],
                    row["title"],
                    row["body"],
                    row["status"],
                    row["scope"],
                ),
            )
            conn.execute(
                "INSERT INTO wiki_search_rows VALUES (?, ?, ?)",
                (digest, row["page_id"], row["claim_id"]),
            )
        conn.commit()
        return self._verified_index_hashes(conn)

    @staticmethod
    def _search_rows(snapshot: WikiSnapshotV1) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "page_id": page.page_id,
                "claim_id": claim.claim_id,
                "title": page.title,
                "body": claim.text,
                "status": claim.status,
                "scope": claim.scope,
            }
            for page in sorted(snapshot.pages, key=lambda row: row.page_id)
            for claim in sorted(page.claims, key=lambda row: row.claim_id)
        )

    def _verified_index_hashes(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[str, ...]:
        indexed_rows = tuple(
            {
                "page_id": str(row[0]),
                "claim_id": str(row[1]),
                "title": str(row[2]),
                "body": str(row[3]),
                "status": str(row[4]),
                "scope": str(row[5]),
            }
            for row in conn.execute(
                """
                SELECT page_id, claim_id, title, body, status, scope
                FROM wiki_search
                ORDER BY rowid
                """
            ).fetchall()
        )
        manifest_rows = tuple(
            (str(row[0]), str(row[1]), str(row[2]))
            for row in conn.execute(
                """
                SELECT row_hash, page_id, claim_id
                FROM wiki_search_rows
                ORDER BY rowid
                """
            ).fetchall()
        )
        actual_rows = tuple(
            (_row_hash(row), row["page_id"], row["claim_id"])
            for row in indexed_rows
        )
        if actual_rows != manifest_rows:
            raise WikiProjectionError("wiki_search_index_verification_failed")
        return tuple(row[0] for row in manifest_rows)

    def _render_page(self, page: JueWikiPageV3) -> str:
        lines = [
            f"# {page.title}",
            "",
            page.summary,
            "",
            f"- Page ID: `{page.page_id}`",
            f"- Scope: `{page.scope}`",
            f"- Type: `{page.page_type}`",
            f"- Status: `{page.status}`",
            "",
            "## Claims",
            "",
        ]
        if not page.claims:
            lines.append("No claims.")
        for claim in sorted(page.claims, key=lambda row: row.claim_id):
            lines.extend(
                [
                    f"### {claim.claim_id}",
                    "",
                    claim.text,
                    "",
                    f"- Type: `{claim.claim_type}`",
                    f"- Status: `{claim.status}`",
                    f"- Confidence: `{claim.confidence:.4f}`",
                    "- Evidence: "
                    + (
                        ", ".join(
                            f"`{row.evidence_id}`"
                            for row in sorted(
                                claim.evidence,
                                key=lambda item: item.evidence_id,
                            )
                        )
                        or "none"
                    ),
                    "",
                ]
            )
        lines.extend(["## Relationships", ""])
        if not page.relationships:
            lines.append("No relationships.")
        for relationship in sorted(
            page.relationships,
            key=lambda row: (
                row.source_claim_id,
                row.relationship_type,
                row.target_id,
            ),
        ):
            lines.append(
                f"- `{relationship.source_claim_id}` "
                f"**{relationship.relationship_type}** `{relationship.target_id}`"
            )
        return "\n".join(lines).rstrip() + "\n"

    def _render_index(
        self,
        snapshot: WikiSnapshotV1,
        page_files: list[tuple[JueWikiPageV3, str]],
    ) -> str:
        lines = [
            f"# Jue Wiki snapshot {snapshot.snapshot_id}",
            "",
            f"- Scope: `{snapshot.scope}`",
            f"- Created: `{snapshot.created_at}`",
            "",
            "## Pages",
            "",
        ]
        if not page_files:
            lines.append("No pages.")
        for page, filename in page_files:
            lines.append(f"- [{page.title}](pages/{filename}) — `{page.status}`")
        return "\n".join(lines).rstrip() + "\n"

    def _render_contradictions(self, snapshot: WikiSnapshotV1) -> str:
        rows = sorted(
            (
                (page.page_id, relationship)
                for page in snapshot.pages
                for relationship in page.relationships
                if relationship.relationship_type == "contradicts"
            ),
            key=lambda item: (
                item[0],
                item[1].source_claim_id,
                item[1].target_id,
            ),
        )
        lines = [f"# Contradictions for {snapshot.snapshot_id}", ""]
        if not rows:
            lines.append("No contradictions.")
        for page_id, relationship in rows:
            lines.append(
                f"- `{relationship.source_claim_id}` contradicts "
                f"`{relationship.target_id}` on `{page_id}`"
            )
        return "\n".join(lines).rstrip() + "\n"

    @property
    def _generation_prefix(self) -> str:
        return f".{self.projection_root.name}.generation-"

    @property
    def _staging_prefix(self) -> str:
        return f".{self.projection_root.name}.staging-"

    @staticmethod
    def _raise_if_unsupported(exc: OSError) -> None:
        if exc.errno in _UNSUPPORTED_ERRNOS:
            raise WikiProjectionError("projection_dir_fd_unsupported") from exc

    def _require_dir_fd_support(self) -> None:
        required_attributes = ("O_DIRECTORY", "O_NOFOLLOW")
        if any(not hasattr(os, name) for name in required_attributes):
            raise WikiProjectionError("projection_dir_fd_unsupported")

    def _probe_dir_fd_support(self, parent_fd: int) -> None:
        token = secrets.token_hex(8)
        directory_name = f".{self.projection_root.name}.capability-{token}"
        source_name = f"{directory_name}-source"
        destination_name = f"{directory_name}-destination"
        directory_fd = -1
        try:
            os.mkdir(directory_name, 0o700, dir_fd=parent_fd)
            directory_fd = self._open_directory_at(parent_fd, directory_name)
            os.listdir(directory_fd)
            os.stat(".", dir_fd=directory_fd, follow_symlinks=False)
            os.symlink("probe", source_name, dir_fd=parent_fd)
            if os.readlink(source_name, dir_fd=parent_fd) != "probe":
                raise WikiProjectionError("projection_dir_fd_unsupported")
            self._replace_at(parent_fd, source_name, destination_name)
            os.unlink(destination_name, dir_fd=parent_fd)
            os.close(directory_fd)
            directory_fd = -1
            os.rmdir(directory_name, dir_fd=parent_fd)
        except WikiProjectionError:
            raise
        except (AttributeError, NotImplementedError, TypeError) as exc:
            raise WikiProjectionError("projection_dir_fd_unsupported") from exc
        except OSError as exc:
            self._raise_if_unsupported(exc)
            raise
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)
            for name in (source_name, destination_name):
                try:
                    os.unlink(name, dir_fd=parent_fd)
                except OSError:
                    pass
            try:
                os.rmdir(directory_name, dir_fd=parent_fd)
            except OSError:
                pass

    def _initialize_containment(self) -> None:
        assert self.containment_root is not None
        containment_path = Path(
            os.path.realpath(os.path.abspath(self.containment_root))
        )
        projection_parent = Path(
            os.path.realpath(os.path.abspath(self.projection_root.parent))
        )
        try:
            relative_parent = projection_parent.relative_to(containment_path)
            self._require_dir_fd_support()
            containment_fd = self._open_absolute_directory(
                containment_path,
                create=False,
            )
        except WikiProjectionError:
            raise
        except (AttributeError, NotImplementedError, TypeError) as exc:
            raise WikiProjectionError("projection_dir_fd_unsupported") from exc
        except OSError as exc:
            self._raise_if_unsupported(exc)
            raise WikiProjectionError("projection_containment_invalid") from exc
        except (RuntimeError, ValueError) as exc:
            raise WikiProjectionError("projection_containment_invalid") from exc
        try:
            containment_stat = os.fstat(containment_fd)
            self._containment_identity = (
                containment_stat.st_dev,
                containment_stat.st_ino,
            )
        finally:
            os.close(containment_fd)
        self._containment_path = containment_path
        self._projection_parent_parts = relative_parent.parts

    @contextmanager
    def _open_projection_parent(self) -> Iterator[int]:
        try:
            if self._containment_path is None:
                parent_fd = self._open_absolute_directory(
                    Path(
                        os.path.realpath(
                            os.path.abspath(self.projection_root.parent)
                        )
                    ),
                    create=True,
                )
            else:
                parent_fd = self._open_contained_parent()
        except WikiProjectionError:
            raise
        except OSError as exc:
            self._raise_if_unsupported(exc)
            raise WikiProjectionError("projection_containment_invalid") from exc
        except (RuntimeError, ValueError) as exc:
            raise WikiProjectionError("projection_containment_invalid") from exc
        try:
            yield parent_fd
        finally:
            os.close(parent_fd)

    def _open_contained_parent(self) -> int:
        assert self._containment_path is not None
        assert self._containment_identity is not None
        current_fd = self._open_absolute_directory(
            self._containment_path,
            create=False,
        )
        try:
            containment_stat = os.fstat(current_fd)
            if (
                containment_stat.st_dev,
                containment_stat.st_ino,
            ) != self._containment_identity:
                raise WikiProjectionError("projection_containment_invalid")
            for component in self._projection_parent_parts:
                next_fd = self._open_or_create_directory_at(current_fd, component)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    def _open_absolute_directory(self, path: Path, *, create: bool) -> int:
        absolute = Path(os.path.abspath(path))
        current_fd = os.open(
            os.path.sep,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            for component in absolute.parts[1:]:
                if create:
                    next_fd = self._open_or_create_directory_at(
                        current_fd,
                        component,
                    )
                else:
                    next_fd = self._open_directory_at(current_fd, component)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    def _open_or_create_directory_at(self, parent_fd: int, name: str) -> int:
        try:
            return self._open_directory_at(parent_fd, name)
        except FileNotFoundError:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
            return self._open_directory_at(parent_fd, name)

    @staticmethod
    def _open_directory_at(parent_fd: int, name: str) -> int:
        return os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )

    def _validate_projection_pointer_at(self, parent_fd: int) -> None:
        try:
            pointer_stat = os.stat(
                self.projection_root.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        if not stat.S_ISLNK(pointer_stat.st_mode):
            raise WikiProjectionError("projection_root_unmanaged")

    def _read_pointer_at(self, parent_fd: int) -> str | None:
        try:
            return os.readlink(self.projection_root.name, dir_fd=parent_fd)
        except FileNotFoundError:
            return None

    def _create_generation_at(self, parent_fd: int) -> tuple[str, int]:
        for _ in range(32):
            name = f"{self._generation_prefix}{secrets.token_hex(8)}"
            try:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
            except FileExistsError:
                continue
            try:
                generation_fd = self._open_directory_at(parent_fd, name)
            except Exception:
                try:
                    os.rmdir(name, dir_fd=parent_fd)
                except OSError:
                    pass
                raise
            return (name, generation_fd)
        raise WikiProjectionError("projection_generation_name_exhausted")

    def _create_staging_at(self, parent_fd: int) -> tuple[str, int]:
        for _ in range(32):
            name = f"{self._staging_prefix}{secrets.token_hex(8)}"
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            try:
                staging_fd = self._open_directory_at(parent_fd, name)
            except Exception:
                try:
                    os.rmdir(name, dir_fd=parent_fd)
                except OSError:
                    pass
                raise
            return (name, staging_fd)
        raise WikiProjectionError("projection_staging_name_exhausted")

    def _create_pointer_at(
        self,
        parent_fd: int,
        *,
        target: str,
        prefix: str,
    ) -> str:
        for _ in range(32):
            name = f"{prefix}{secrets.token_hex(8)}"
            try:
                os.symlink(target, name, dir_fd=parent_fd)
            except FileExistsError:
                continue
            return name
        raise WikiProjectionError("projection_pointer_name_exhausted")

    def _replace_at(self, parent_fd: int, source: str, destination: str) -> None:
        try:
            os.replace(
                source,
                destination,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except TypeError as exc:
            raise WikiProjectionError("projection_dir_fd_unsupported") from exc
        except OSError as exc:
            self._raise_if_unsupported(exc)
            raise

    def _replace_between_directories(
        self,
        source_fd: int,
        source: str,
        destination_fd: int,
        destination: str,
    ) -> None:
        try:
            os.replace(
                source,
                destination,
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
            )
        except TypeError as exc:
            raise WikiProjectionError("projection_dir_fd_unsupported") from exc
        except OSError as exc:
            self._raise_if_unsupported(exc)
            raise

    @staticmethod
    def _write_text_at(parent_fd: int, name: str, content: str) -> None:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent_fd,
        )
        try:
            payload = content.encode("utf-8")
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _unlink_pointer_at(self, parent_fd: int, name: str | None) -> None:
        if name is None:
            return
        try:
            link_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(link_stat.st_mode):
                os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return

    def _is_owned_generation_name(self, name: str) -> bool:
        return Path(name).name == name and name.startswith(self._generation_prefix)

    def _remove_owned_generation_at(
        self,
        parent_fd: int,
        name: str,
        *,
        require_marker: bool,
    ) -> bool:
        if not self._is_owned_generation_name(name):
            return False
        try:
            generation_fd = self._open_directory_at(parent_fd, name)
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                return False
            raise
        try:
            if require_marker and not self._valid_generation_marker_at(generation_fd):
                return False
            self._remove_directory_contents(generation_fd)
        finally:
            os.close(generation_fd)
        os.rmdir(name, dir_fd=parent_fd)
        return True

    def _remove_staging_at(self, parent_fd: int, name: str) -> bool:
        if Path(name).name != name or not name.startswith(self._staging_prefix):
            return False
        try:
            staging_fd = self._open_directory_at(parent_fd, name)
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                return False
            raise
        try:
            self._remove_directory_contents(staging_fd)
        finally:
            os.close(staging_fd)
        os.rmdir(name, dir_fd=parent_fd)
        return True

    def _valid_generation_marker_at(self, generation_fd: int) -> bool:
        try:
            marker_fd = os.open(
                GENERATION_MARKER,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=generation_fd,
            )
        except (FileNotFoundError, OSError):
            return False
        try:
            marker_stat = os.fstat(marker_fd)
            if not stat.S_ISREG(marker_stat.st_mode):
                return False
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(marker_fd, 4096)
                if not chunk:
                    break
                total += len(chunk)
                if total > 64 * 1024:
                    return False
                chunks.append(chunk)
            marker_payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        finally:
            os.close(marker_fd)
        return (
            isinstance(marker_payload, dict)
            and set(marker_payload)
            == {"schema_version", "projection_name", "snapshot_id"}
            and marker_payload.get("schema_version") == GENERATION_SCHEMA_VERSION
            and marker_payload.get("projection_name") == self.projection_root.name
            and isinstance(marker_payload.get("snapshot_id"), str)
            and bool(marker_payload["snapshot_id"].strip())
        )

    def _remove_directory_contents(self, directory_fd: int) -> None:
        for name in os.listdir(directory_fd):
            entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(entry_stat.st_mode):
                child_fd = self._open_directory_at(directory_fd, name)
                try:
                    self._remove_directory_contents(child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(name, dir_fd=directory_fd)
            else:
                os.unlink(name, dir_fd=directory_fd)

    def _remove_owned_generation(
        self,
        path: Path,
        *,
        require_marker: bool,
    ) -> None:
        if path.parent != self.projection_root.parent:
            return
        self._require_dir_fd_support()
        with self._open_projection_parent() as parent_fd:
            self._remove_owned_generation_at(
                parent_fd,
                path.name,
                require_marker=require_marker,
            )
