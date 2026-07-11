from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

from tradecraft.services.runtime_cold_archive import (
    ArchiveCandidateV1,
    RuntimeColdArchiveV1,
)
from tradecraft.services.runtime_cold_archive_status import (
    build_runtime_cold_archive_status,
    persist_runtime_cold_archive_status,
    read_runtime_cold_archive_status,
)


def _archived_text_fixture(
    tmp_path: Path,
) -> tuple[RuntimeColdArchiveV1, Path, object]:
    source = tmp_path / "hot" / "state.json"
    source.parent.mkdir()
    source.write_text('{"ok": true}', encoding="utf-8")
    archive = RuntimeColdArchiveV1(tmp_path / "cold")
    result = archive.archive(
        ArchiveCandidateV1(
            category="dryrun",
            logical_scenario="rehearsal",
            source_paths=(source,),
            restore_contract={"kind": "files-v1"},
        )
    )
    return archive, source, archive.entry(result.entry_id)


def test_archive_publishes_verified_manifest_before_hot_removal(
    tmp_path: Path,
) -> None:
    archive, source, entry = _archived_text_fixture(tmp_path)

    assert source.exists()
    assert entry.lifecycle == "verified_hot_retained"
    assert archive.verify(entry).ok is True


def test_corrupt_archive_never_authorizes_hot_removal(tmp_path: Path) -> None:
    archive, source, entry = _archived_text_fixture(tmp_path)
    entry.archive_path.write_bytes(b"corrupt")

    verification = archive.verify(entry)
    removal = archive.mark_hot_removed(entry.entry_id, (source,))

    assert verification.ok is False
    assert source.exists()
    assert removal.removed is False
    assert removal.reason == "archive_verification_failed"


def test_restore_refuses_destination_collision(tmp_path: Path) -> None:
    archive, _, entry = _archived_text_fixture(tmp_path)
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "state.json").write_text("existing", encoding="utf-8")

    result = archive.restore(entry.entry_id, destination)

    assert result.restored is False
    assert result.reason == "destination_collision"
    assert (destination / "state.json").read_text(encoding="utf-8") == "existing"


def test_verified_archive_can_remove_and_restore_hot_source(tmp_path: Path) -> None:
    archive, source, entry = _archived_text_fixture(tmp_path)

    removal = archive.mark_hot_removed(entry.entry_id, (source,))
    restored = archive.restore(entry.entry_id, tmp_path / "restored")

    assert removal.removed is True
    assert not source.exists()
    assert archive.entry(entry.entry_id).lifecycle == "hot_removed"
    assert restored.restored is True
    assert restored.paths == (tmp_path / "restored" / "state.json",)
    assert restored.paths[0].read_text(encoding="utf-8") == '{"ok": true}'


def test_verify_rejects_manifest_source_hash_tampering(tmp_path: Path) -> None:
    archive, _, entry = _archived_text_fixture(tmp_path)
    altered_source = replace(entry.sources[0], sha256="0" * 64)

    verification = archive.verify(replace(entry, sources=(altered_source,)))

    assert verification.ok is False
    assert "member_sha256_mismatch:state.json" in verification.errors


def test_status_reports_corrupt_entries_without_mutating_manifest(tmp_path: Path) -> None:
    archive, _, entry = _archived_text_fixture(tmp_path)
    manifest_before = archive.manifest_path.read_bytes()
    entry.archive_path.write_bytes(b"corrupt")

    status = archive.status()

    assert status["status"] == "warning"
    assert status["entry_count"] == 1
    assert status["corrupt_entry_ids"] == [entry.entry_id]
    assert archive.manifest_path.read_bytes() == manifest_before


def test_sqlite_backup_archive_includes_wal_without_mutating_hot_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "hot" / "rehearsal.db"
    database.parent.mkdir()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, value TEXT)")
    connection.commit()
    database_before = database.read_bytes()
    connection.execute("INSERT INTO orders (value) VALUES ('from-wal')")
    connection.commit()
    wal_path = Path(f"{database}-wal")
    assert wal_path.exists()
    archive = RuntimeColdArchiveV1(tmp_path / "cold")

    result = archive.archive(
        ArchiveCandidateV1(
            category="dryrun",
            logical_scenario="rehearsal",
            source_paths=(database,),
            restore_contract={"kind": "sqlite-bundle-v1", "sqlite_backup": True},
        )
    )
    assert database.read_bytes() == database_before
    connection.close()
    restored = archive.restore(result.entry_id, tmp_path / "restored")

    assert result.verified is True
    with sqlite3.connect(restored.paths[0]) as restored_connection:
        assert restored_connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored_connection.execute("SELECT value FROM orders").fetchone()[0] == "from-wal"


def test_combined_status_includes_core_and_selection_sections(tmp_path: Path) -> None:
    core, _, _ = _archived_text_fixture(tmp_path)

    status = build_runtime_cold_archive_status(
        root=core.root,
        jue_wiki_db_path=tmp_path / "wiki.db",
    )

    assert status["status"] == "ok"
    assert status["entry_count"] == 1
    assert status["sections"]["core"]["entry_count"] == 1
    assert status["sections"]["jue_selection"]["entry_count"] == 0


def test_readiness_status_reads_persisted_verification_without_rehashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    core, _, _ = _archived_text_fixture(tmp_path)
    persisted = persist_runtime_cold_archive_status(
        root=core.root,
        jue_wiki_db_path=tmp_path / "wiki.db",
    )
    monkeypatch.setattr(
        RuntimeColdArchiveV1,
        "verify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("read path must not rehash archives")
        ),
    )

    current = read_runtime_cold_archive_status(root=core.root)

    assert current["status"] == "ok"
    assert current["entry_count"] == persisted["entry_count"]
    assert current["verification_snapshot"]["status"] == "current"


def test_readiness_status_rejects_snapshot_after_manifest_changes(tmp_path: Path) -> None:
    core, source, _ = _archived_text_fixture(tmp_path)
    persist_runtime_cold_archive_status(
        root=core.root,
        jue_wiki_db_path=tmp_path / "wiki.db",
    )
    source.write_text("changed", encoding="utf-8")
    core.archive(
        ArchiveCandidateV1(
            category="dryrun",
            logical_scenario="new",
            source_paths=(source,),
            restore_contract={"kind": "files-v1"},
        )
    )

    current = read_runtime_cold_archive_status(root=core.root)

    assert current["status"] == "warning"
    assert current["verification_snapshot"]["status"] == "stale"
