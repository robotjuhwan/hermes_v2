from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiRelationshipV1,
    WikiSnapshotV1,
)
from tradecraft.services.jue_wiki_repository import (
    JueWikiRepository,
    JueWikiRepositoryIntegrityError,
)


def _evidence(evidence_id: str = "evidence:test:1") -> EvidenceRefV1:
    return EvidenceRefV1(
        evidence_id=evidence_id,
        source_type="test",
        source_id="source:1",
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
        source_path="/tmp/source.json",
        hash_origin="source",
    )


def _claim(evidence: EvidenceRefV1) -> WikiClaimV3:
    return WikiClaimV3(
        claim_id="claim:kis:005930:direction",
        claim_type="interpretation",
        text="Revision direction is positive.",
        status="verified",
        scope="kis",
        evidence=(evidence,),
        symbols=("005930",),
        confidence=0.8,
        provenance_id="candidate:test:1",
    )


def _page(evidence: EvidenceRefV1, page_id: str = "kis.symbol.005930") -> JueWikiPageV3:
    claim = _claim(evidence)
    return JueWikiPageV3(
        page_id=page_id,
        page_type="symbol",
        scope="kis",
        title="Samsung Electronics",
        summary="Positive revision direction.",
        claims=(claim,),
        relationships=(
            WikiRelationshipV1(
                source_claim_id=claim.claim_id,
                relationship_type="applies_to",
                target_id="005930",
            ),
        ),
        status="verified",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )


def _snapshot(
    snapshot_id: str,
    *,
    pages: tuple[JueWikiPageV3, ...] = (),
) -> WikiSnapshotV1:
    return WikiSnapshotV1(
        snapshot_id=snapshot_id,
        scope="kis",
        candidate_artifact_ids=("candidate:test:1",),
        pages=pages,
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-11T00:00:00+00:00",
    )


def test_initialize_adds_v3_schema_without_changing_legacy_pages(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE wiki_pages (page_id TEXT PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO wiki_pages VALUES ('legacy:1', 'compiled markdown')")

    JueWikiRepository(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        legacy_rows = conn.execute("SELECT page_id, body FROM wiki_pages").fetchall()
        v3_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert legacy_rows == [("legacy:1", "compiled markdown")]
    assert {
        "wiki_evidence_v1",
        "wiki_candidate_artifacts_v1",
        "wiki_snapshots_v1",
        "wiki_pages_v3",
    } <= v3_tables


def test_register_evidence_exposes_known_evidence_ids(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    first = _evidence("evidence:test:1")
    second = _evidence("evidence:test:2")

    repo.register_evidence(second)
    repo.register_evidence(first)

    assert repo.evidence_ids() == {first.evidence_id, second.evidence_id}


def test_evidence_refs_round_trip_full_fields_in_stable_key_order(
    tmp_path: Path,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    later = replace(
        _evidence("evidence:test:z"),
        source_type="normalized_test",
        source_id="source:z",
        content_hash="z" * 64,
        observed_at="2026-07-11T00:02:00+00:00",
        source_path="/tmp/normalized-z.json",
        hash_origin="normalized_payload",
    )
    earlier = replace(
        _evidence("evidence:test:a"),
        source_id="source:a",
        source_path="/tmp/source-a.json",
    )
    repo.register_evidence(later)
    repo.register_evidence(earlier)

    refs = repo.evidence_refs()

    assert list(refs) == [earlier.evidence_id, later.evidence_id]
    assert refs == {
        earlier.evidence_id: earlier,
        later.evidence_id: later,
    }


def test_initialize_migrates_existing_evidence_schema_with_source_default(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    legacy = _evidence("evidence:test:legacy")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE wiki_evidence_v1 (
                evidence_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_evidence_v1 (
                evidence_id, source_type, source_id, content_hash,
                observed_at, source_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy.evidence_id,
                legacy.source_type,
                legacy.source_id,
                legacy.content_hash,
                legacy.observed_at,
                legacy.source_path,
                "2026-07-11T00:01:00+00:00",
            ),
        )

    repo = JueWikiRepository(db_path)
    repo.initialize()

    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1]): str(row[4])
            for row in conn.execute("PRAGMA table_info(wiki_evidence_v1)")
        }
    assert columns["hash_origin"] == "'source'"
    assert repo.evidence_refs() == {legacy.evidence_id: legacy}


def test_evidence_refs_reads_unmigrated_schema_without_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    legacy = _evidence("evidence:test:unmigrated")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE wiki_evidence_v1 (
                evidence_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_evidence_v1 (
                evidence_id, source_type, source_id, content_hash,
                observed_at, source_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy.evidence_id,
                legacy.source_type,
                legacy.source_id,
                legacy.content_hash,
                legacy.observed_at,
                legacy.source_path,
                "2026-07-11T00:01:00+00:00",
            ),
        )
    database_paths = tuple(
        Path(f"{db_path}{suffix}") for suffix in ("", "-wal", "-shm", "-journal")
    )

    def database_state() -> dict[str, tuple[bytes, int]]:
        return {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in database_paths
            if path.exists()
        }

    before = database_state()

    refs = JueWikiRepository(db_path).evidence_refs()

    assert refs == {legacy.evidence_id: legacy}
    assert database_state() == before


def test_register_evidence_exact_replay_is_idempotent(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    evidence = replace(_evidence(), hash_origin="normalized_payload")

    repo.register_evidence(evidence)
    repo.register_evidence(evidence)

    assert repo.evidence_refs() == {evidence.evidence_id: evidence}


@pytest.mark.parametrize(
    "changes",
    [
        {"source_type": "other"},
        {"source_id": "source:other"},
        {"content_hash": "b" * 64},
        {"observed_at": "2026-07-11T00:03:00+00:00"},
        {"source_path": "/tmp/other-source.json"},
        {"hash_origin": "normalized_payload"},
    ],
)
def test_register_evidence_rejects_divergent_payload_and_retains_original(
    tmp_path: Path,
    changes: dict[str, str],
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    original = _evidence("evidence:test:immutable")
    divergent = replace(original, **changes)
    repo.register_evidence(original)

    with pytest.raises(JueWikiRepositoryIntegrityError) as exc_info:
        repo.register_evidence(divergent)

    assert str(exc_info.value) == (
        "evidence_payload_conflict:evidence:test:immutable"
    )
    assert repo.evidence_refs() == {original.evidence_id: original}


def test_evidence_read_paths_do_not_initialize_migrate_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "wiki.db"
    repo = JueWikiRepository(db_path)
    repo.initialize()
    evidence = _evidence("evidence:test:read-only")
    repo.register_evidence(evidence)
    before_bytes = db_path.read_bytes()
    before_mtime_ns = db_path.stat().st_mtime_ns
    monkeypatch.setattr(
        repo,
        "initialize",
        lambda: pytest.fail("read_path_initialized_repository"),
    )

    assert repo.evidence_refs() == {evidence.evidence_id: evidence}
    assert repo.evidence_ids() == {evidence.evidence_id}

    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime_ns


def test_store_and_load_candidate_artifacts_in_requested_order(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    evidence = _evidence()
    first = CandidateArtifactV1(
        artifact_id="candidate:test:1",
        scope="kis",
        extractor_version="test_v1",
        input_hash="b" * 64,
        source_refs=(evidence,),
        claims=(_claim(evidence),),
        created_at="2026-07-11T00:00:00+00:00",
        model="test-model",
        prompt_hash="c" * 64,
        config_hash="d" * 64,
    )
    second = CandidateArtifactV1(
        artifact_id="candidate:test:2",
        scope="kis",
        extractor_version="test_v1",
        input_hash="e" * 64,
        source_refs=(evidence,),
        claims=(),
        created_at="2026-07-11T00:01:00+00:00",
    )
    repo.store_candidate(first)
    repo.store_candidate(second)

    loaded = repo.candidate_artifacts((second.artifact_id, first.artifact_id))

    assert loaded == (second, first)


def test_store_candidate_is_idempotent_for_replayed_artifact(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    evidence = _evidence()
    artifact = CandidateArtifactV1(
        artifact_id="candidate:test:replayed",
        scope="kis",
        extractor_version="test_v1",
        input_hash="f" * 64,
        source_refs=(evidence,),
        claims=(_claim(evidence),),
        created_at="2026-07-11T00:00:00+00:00",
    )

    repo.store_candidate(artifact)
    repo.store_candidate(artifact)

    assert repo.candidate_artifacts((artifact.artifact_id,)) == (artifact,)


def test_candidate_relationships_round_trip_in_order_and_replay_exactly(
    tmp_path: Path,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    evidence = _evidence()
    relationships = (
        WikiRelationshipV1(
            source_claim_id="claim:kis:005930:direction",
            relationship_type="supports",
            target_id="claim:kis:005930:thesis",
        ),
        WikiRelationshipV1(
            source_claim_id="claim:kis:005930:direction",
            relationship_type="supersedes",
            target_id="claim:kis:005930:prior-direction",
        ),
    )
    artifact = CandidateArtifactV1(
        artifact_id="candidate:test:relationships",
        scope="kis",
        extractor_version="test_v1",
        input_hash="2" * 64,
        source_refs=(evidence,),
        claims=(_claim(evidence),),
        created_at="2026-07-11T00:00:00+00:00",
        relationships=relationships,
    )

    repo.store_candidate(artifact)
    repo.store_candidate(artifact)

    loaded = repo.candidate_artifacts((artifact.artifact_id,))
    assert loaded == (artifact,)
    assert loaded[0].relationships == relationships


def test_candidate_replay_normalizes_legacy_missing_relationships(
    tmp_path: Path,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    artifact = CandidateArtifactV1(
        artifact_id="candidate:test:legacy-no-relationships",
        scope="kis",
        extractor_version="test_v1",
        input_hash="5" * 64,
        source_refs=(),
        claims=(),
        created_at="2026-07-11T00:00:00+00:00",
    )
    legacy_payload = artifact.to_dict()
    del legacy_payload["relationships"]
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute(
            """
            INSERT INTO wiki_candidate_artifacts_v1 (
                artifact_id, scope, extractor_version, input_hash,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.scope,
                artifact.extractor_version,
                artifact.input_hash,
                json.dumps(legacy_payload),
                artifact.created_at,
            ),
        )

    repo.store_candidate(artifact)

    assert repo.candidate_artifacts((artifact.artifact_id,)) == (artifact,)


def test_candidate_relationship_change_is_a_canonical_payload_conflict(
    tmp_path: Path,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    original = CandidateArtifactV1(
        artifact_id="candidate:test:relationship-conflict",
        scope="kis",
        extractor_version="test_v1",
        input_hash="3" * 64,
        source_refs=(),
        claims=(),
        created_at="2026-07-11T00:00:00+00:00",
        relationships=(
            WikiRelationshipV1(
                source_claim_id="claim:new",
                relationship_type="supersedes",
                target_id="claim:old",
            ),
        ),
    )
    divergent = replace(
        original,
        relationships=(
            WikiRelationshipV1(
                source_claim_id="claim:new",
                relationship_type="supports",
                target_id="claim:old",
            ),
        ),
    )
    repo.store_candidate(original)

    with pytest.raises(JueWikiRepositoryIntegrityError) as exc_info:
        repo.store_candidate(divergent)

    assert str(exc_info.value) == (
        "candidate_artifact_payload_conflict:candidate:test:relationship-conflict"
    )
    assert repo.candidate_artifacts((original.artifact_id,)) == (original,)


@pytest.mark.parametrize(
    "relationship_payload",
    [
        {
            "source_claim_id": "claim:new",
            "relationship_type": "invalid",
            "target_id": "claim:old",
        },
        {
            "source_claim_id": "claim:new",
            "relationship_type": "supports",
        },
        {
            "source_claim_id": "claim:new",
            "relationship_type": [],
            "target_id": "claim:old",
        },
        "not-a-relationship-object",
    ],
)
def test_candidate_hydration_rejects_malformed_stored_relationships(
    tmp_path: Path,
    relationship_payload: object,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    artifact = CandidateArtifactV1(
        artifact_id="candidate:test:malformed-relationship",
        scope="kis",
        extractor_version="test_v1",
        input_hash="4" * 64,
        source_refs=(),
        claims=(),
        created_at="2026-07-11T00:00:00+00:00",
    )
    repo.store_candidate(artifact)
    malformed_payload = artifact.to_dict()
    malformed_payload["relationships"] = [relationship_payload]
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute(
            """
            UPDATE wiki_candidate_artifacts_v1
            SET payload_json = ?
            WHERE artifact_id = ?
            """,
            (json.dumps(malformed_payload), artifact.artifact_id),
        )

    with pytest.raises(JueWikiRepositoryIntegrityError) as exc_info:
        repo.candidate_artifacts((artifact.artifact_id,))

    assert str(exc_info.value) == (
        "candidate_artifact_relationship_malformed:"
        "candidate:test:malformed-relationship"
    )


def test_store_candidate_rejects_divergent_payload_and_preserves_original(
    tmp_path: Path,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    evidence = _evidence()
    original = CandidateArtifactV1(
        artifact_id="candidate:test:conflict",
        scope="kis",
        extractor_version="test_v1",
        input_hash="f" * 64,
        source_refs=(evidence,),
        claims=(_claim(evidence),),
        created_at="2026-07-11T00:00:00+00:00",
    )
    divergent = replace(original, input_hash="0" * 64)
    repo.store_candidate(original)

    with pytest.raises(JueWikiRepositoryIntegrityError) as exc_info:
        repo.store_candidate(divergent)

    assert str(exc_info.value) == (
        "candidate_artifact_payload_conflict:candidate:test:conflict"
    )
    assert repo.candidate_artifacts((original.artifact_id,)) == (original,)


def test_candidate_artifacts_rejects_missing_requested_ids(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()

    with pytest.raises(JueWikiRepositoryIntegrityError) as exc_info:
        repo.candidate_artifacts(("candidate:missing:z", "candidate:missing:a"))

    assert str(exc_info.value) == (
        "candidate_artifact_missing:candidate:missing:a,candidate:missing:z"
    )


def test_candidate_round_trip_preserves_explicit_empty_hash_origin(
    tmp_path: Path,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    evidence = replace(_evidence(), hash_origin="")
    artifact = CandidateArtifactV1(
        artifact_id="candidate:test:empty-hash-origin",
        scope="kis",
        extractor_version="test_v1",
        input_hash="1" * 64,
        source_refs=(evidence,),
        claims=(),
        created_at="2026-07-11T00:00:00+00:00",
    )

    repo.store_candidate(artifact)

    assert repo.candidate_artifacts((artifact.artifact_id,)) == (artifact,)


def test_publish_and_read_current_snapshot_with_pages(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    first_page = _page(_evidence(), "kis.symbol.000001")
    second_page = _page(_evidence(), "kis.symbol.000002")
    first = _snapshot("snapshot:kis:1", pages=(first_page, second_page))
    second = _snapshot("snapshot:kis:2")

    repo.publish_snapshot(first)
    assert repo.current_snapshot("kis") == first
    repo.publish_snapshot(second)

    assert repo.current_snapshot("kis") == second
    assert repo.pages_for_snapshot(first.snapshot_id) == first.pages
    assert repo.current_snapshot("binance") is None


@pytest.mark.parametrize(
    "page_ids",
    [
        ("kis.symbol.000002", "kis.symbol.000001"),
        ("kis.symbol.000001", "kis.symbol.000001"),
    ],
)
def test_publish_snapshot_rejects_noncanonical_pages_before_writes(
    tmp_path: Path,
    page_ids: tuple[str, str],
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    invalid = _snapshot(
        "snapshot:kis:noncanonical",
        pages=tuple(_page(_evidence(), page_id) for page_id in page_ids),
    )

    with pytest.raises(JueWikiRepositoryIntegrityError) as exc_info:
        repo.publish_snapshot(invalid)

    assert str(exc_info.value) == "snapshot_pages_not_canonical"
    with repo.open_read_only() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wiki_snapshots_v1 WHERE snapshot_id = ?",
            (invalid.snapshot_id,),
        ).fetchone()[0] == 0


def test_failed_snapshot_publish_keeps_previous_snapshot(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    first = _snapshot("snapshot:kis:1")
    repo.publish_snapshot(first)

    with pytest.raises(sqlite3.IntegrityError):
        repo.publish_snapshot(first)

    assert repo.current_snapshot("kis") == first


def test_promotion_failure_rolls_back_snapshot_pages_and_publication_pointer(
    tmp_path: Path,
) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    evidence = _evidence()
    first = _snapshot(
        "snapshot:kis:1",
        pages=(_page(evidence, "kis.symbol.000001"),),
    )
    failed = _snapshot(
        "snapshot:kis:2",
        pages=(_page(evidence, "kis.symbol.000002"),),
    )
    repo.publish_snapshot(first)
    with sqlite3.connect(repo.db_path) as conn:
        conn.executescript(
            """
            CREATE TRIGGER abort_snapshot_promotion
            BEFORE UPDATE OF published ON wiki_snapshots_v1
            WHEN NEW.snapshot_id = 'snapshot:kis:2' AND NEW.published = 1
            BEGIN
                SELECT RAISE(ABORT, 'blocked_snapshot_promotion');
            END;
            """
        )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="blocked_snapshot_promotion"):
            repo.publish_snapshot(failed)
    finally:
        with sqlite3.connect(repo.db_path) as conn:
            conn.execute("DROP TRIGGER abort_snapshot_promotion")

    assert repo.current_snapshot("kis") == first
    with repo.open_read_only() as conn:
        snapshot_count = conn.execute(
            "SELECT COUNT(*) FROM wiki_snapshots_v1 WHERE snapshot_id = ?",
            (failed.snapshot_id,),
        ).fetchone()[0]
        page_count = conn.execute(
            "SELECT COUNT(*) FROM wiki_pages_v3 WHERE snapshot_id = ?",
            (failed.snapshot_id,),
        ).fetchone()[0]
    assert snapshot_count == 0
    assert page_count == 0


def test_read_only_connection_rejects_writes(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    with repo.open_read_only() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE forbidden_write (id INTEGER)")


def test_legacy_service_exposes_v3_repository_without_replacing_writes(
    tmp_path: Path,
) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "wiki",
            db_path=tmp_path / "wiki.db",
        )
    )

    repository = service.repository()

    assert isinstance(repository, JueWikiRepository)
    assert repository.db_path == service.config.db_path
    assert service.write_page.__func__ is JueWikiService.write_page
