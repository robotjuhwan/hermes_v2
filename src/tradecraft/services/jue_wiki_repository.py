from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, get_args, get_type_hints
from urllib.parse import quote

from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiRelationshipV1,
    WikiSnapshotV1,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_evidence_v1 (
    evidence_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_path TEXT NOT NULL DEFAULT '',
    hash_origin TEXT NOT NULL DEFAULT 'source',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_candidate_artifacts_v1 (
    artifact_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_snapshots_v1 (
    snapshot_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    compiler_version TEXT NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wiki_snapshot_scope_published
ON wiki_snapshots_v1(scope) WHERE published = 1;
CREATE TABLE IF NOT EXISTS wiki_pages_v3 (
    snapshot_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    page_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, page_id)
);
"""


class JueWikiRepositoryIntegrityError(RuntimeError):
    pass


_RELATIONSHIP_TYPES = frozenset(
    get_args(get_type_hints(WikiRelationshipV1)["relationship_type"])
)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence_from_dict(payload: dict[str, Any]) -> EvidenceRefV1:
    return EvidenceRefV1(
        evidence_id=str(payload.get("evidence_id") or ""),
        source_type=str(payload.get("source_type") or ""),
        source_id=str(payload.get("source_id") or ""),
        content_hash=str(payload.get("content_hash") or ""),
        observed_at=str(payload.get("observed_at") or ""),
        source_path=str(payload.get("source_path") or ""),
        hash_origin=str(payload.get("hash_origin", "source")),
    )


def _claim_from_dict(payload: dict[str, Any]) -> WikiClaimV3:
    return WikiClaimV3(
        claim_id=str(payload.get("claim_id") or ""),
        claim_type=payload["claim_type"],
        text=str(payload.get("text") or ""),
        status=payload["status"],
        scope=str(payload.get("scope") or ""),
        evidence=tuple(
            _evidence_from_dict(row) for row in payload.get("evidence", ())
        ),
        symbols=tuple(payload.get("symbols", ())),
        venues=tuple(payload.get("venues", ())),
        strategies=tuple(payload.get("strategies", ())),
        regimes=tuple(payload.get("regimes", ())),
        valid_from=str(payload.get("valid_from") or ""),
        valid_to=str(payload.get("valid_to") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        provenance_id=str(payload.get("provenance_id") or ""),
    )


def _candidate_relationships_from_dict(
    payload: dict[str, Any],
    artifact_id: str,
) -> tuple[WikiRelationshipV1, ...]:
    error_message = f"candidate_artifact_relationship_malformed:{artifact_id}"
    rows = payload.get("relationships", ())
    if not isinstance(rows, (list, tuple)):
        raise JueWikiRepositoryIntegrityError(error_message)
    relationships: list[WikiRelationshipV1] = []
    for row in rows:
        if not isinstance(row, dict):
            raise JueWikiRepositoryIntegrityError(error_message)
        source_claim_id = row.get("source_claim_id")
        relationship_type = row.get("relationship_type")
        target_id = row.get("target_id")
        if (
            not isinstance(source_claim_id, str)
            or not source_claim_id.strip()
            or not isinstance(relationship_type, str)
            or relationship_type not in _RELATIONSHIP_TYPES
            or not isinstance(target_id, str)
            or not target_id.strip()
        ):
            raise JueWikiRepositoryIntegrityError(error_message)
        relationships.append(
            WikiRelationshipV1(
                source_claim_id=source_claim_id,
                relationship_type=relationship_type,
                target_id=target_id,
            )
        )
    return tuple(relationships)


def _candidate_from_json(payload_json: str) -> CandidateArtifactV1:
    payload = json.loads(payload_json)
    artifact_id = str(payload.get("artifact_id") or "")
    return CandidateArtifactV1(
        artifact_id=artifact_id,
        scope=str(payload.get("scope") or ""),
        extractor_version=str(payload.get("extractor_version") or ""),
        input_hash=str(payload.get("input_hash") or ""),
        source_refs=tuple(
            _evidence_from_dict(row) for row in payload.get("source_refs", ())
        ),
        claims=tuple(_claim_from_dict(row) for row in payload.get("claims", ())),
        created_at=str(payload.get("created_at") or ""),
        relationships=_candidate_relationships_from_dict(payload, artifact_id),
        model=str(payload.get("model") or ""),
        prompt_hash=str(payload.get("prompt_hash") or ""),
        config_hash=str(payload.get("config_hash") or ""),
    )


def _page_from_json(page_json: str) -> JueWikiPageV3:
    payload = json.loads(page_json)
    return JueWikiPageV3(
        page_id=str(payload.get("page_id") or ""),
        page_type=str(payload.get("page_type") or ""),
        scope=str(payload.get("scope") or ""),
        title=str(payload.get("title") or ""),
        summary=str(payload.get("summary") or ""),
        claims=tuple(_claim_from_dict(row) for row in payload.get("claims", ())),
        relationships=tuple(
            WikiRelationshipV1(
                source_claim_id=str(row.get("source_claim_id") or ""),
                relationship_type=row["relationship_type"],
                target_id=str(row.get("target_id") or ""),
            )
            for row in payload.get("relationships", ())
        ),
        status=payload["status"],
        schema_version=str(payload.get("schema_version") or ""),
        compiler_version=str(payload.get("compiler_version") or ""),
    )


class JueWikiRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
            evidence_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(wiki_evidence_v1)")
            }
            if "hash_origin" not in evidence_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_evidence_v1
                    ADD COLUMN hash_origin TEXT NOT NULL DEFAULT 'source'
                    """
                )

    def register_evidence(self, ref: EvidenceRefV1) -> None:
        payload_json = _json_dumps(ref.to_dict())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT evidence_id, source_type, source_id, content_hash,
                       observed_at, source_path, hash_origin
                FROM wiki_evidence_v1
                WHERE evidence_id = ?
                """,
                (ref.evidence_id,),
            ).fetchone()
            if row is not None:
                stored_ref = EvidenceRefV1(
                    evidence_id=str(row[0]),
                    source_type=str(row[1]),
                    source_id=str(row[2]),
                    content_hash=str(row[3]),
                    observed_at=str(row[4]),
                    source_path=str(row[5]),
                    hash_origin=str(row[6]),
                )
                if _json_dumps(stored_ref.to_dict()) != payload_json:
                    raise JueWikiRepositoryIntegrityError(
                        f"evidence_payload_conflict:{ref.evidence_id}"
                    )
                return
            created_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO wiki_evidence_v1 (
                    evidence_id, source_type, source_id, content_hash,
                    observed_at, source_path, hash_origin, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref.evidence_id,
                    ref.source_type,
                    ref.source_id,
                    ref.content_hash,
                    ref.observed_at,
                    ref.source_path,
                    ref.hash_origin,
                    created_at,
                ),
            )

    def evidence_refs(self) -> dict[str, EvidenceRefV1]:
        with self.open_read_only() as conn:
            evidence_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(wiki_evidence_v1)")
            }
            hash_origin_select = (
                "hash_origin"
                if "hash_origin" in evidence_columns
                else "'source' AS hash_origin"
            )
            rows = conn.execute(
                f"""
                SELECT evidence_id, source_type, source_id, content_hash,
                       observed_at, source_path, {hash_origin_select}
                FROM wiki_evidence_v1
                ORDER BY evidence_id
                """
            ).fetchall()
        return {
            str(row["evidence_id"]): EvidenceRefV1(
                evidence_id=str(row["evidence_id"]),
                source_type=str(row["source_type"]),
                source_id=str(row["source_id"]),
                content_hash=str(row["content_hash"]),
                observed_at=str(row["observed_at"]),
                source_path=str(row["source_path"]),
                hash_origin=str(row["hash_origin"]),
            )
            for row in rows
        }

    def evidence_ids(self) -> set[str]:
        return set(self.evidence_refs())

    def store_candidate(self, artifact: CandidateArtifactV1) -> None:
        payload_json = _json_dumps(artifact.to_dict())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT payload_json
                FROM wiki_candidate_artifacts_v1
                WHERE artifact_id = ?
                """,
                (artifact.artifact_id,),
            ).fetchone()
            if row is not None:
                stored_payload = json.loads(str(row[0]))
                if "relationships" not in stored_payload:
                    stored_payload["relationships"] = []
                stored_payload_json = _json_dumps(stored_payload)
                if stored_payload_json != payload_json:
                    raise JueWikiRepositoryIntegrityError(
                        "candidate_artifact_payload_conflict:"
                        f"{artifact.artifact_id}"
                    )
                return
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
                    payload_json,
                    artifact.created_at,
                ),
            )

    def candidate_artifacts(
        self,
        artifact_ids: tuple[str, ...],
    ) -> tuple[CandidateArtifactV1, ...]:
        if not artifact_ids:
            return ()
        placeholders = ",".join("?" for _ in artifact_ids)
        with self.open_read_only() as conn:
            rows = conn.execute(
                f"""
                SELECT artifact_id, payload_json
                FROM wiki_candidate_artifacts_v1
                WHERE artifact_id IN ({placeholders})
                """,
                artifact_ids,
            ).fetchall()
        artifacts_by_id = {
            str(row["artifact_id"]): _candidate_from_json(str(row["payload_json"]))
            for row in rows
        }
        missing_ids = sorted(set(artifact_ids) - artifacts_by_id.keys())
        if missing_ids:
            raise JueWikiRepositoryIntegrityError(
                f"candidate_artifact_missing:{','.join(missing_ids)}"
            )
        return tuple(
            artifacts_by_id[artifact_id]
            for artifact_id in artifact_ids
        )

    def publish_snapshot(self, snapshot: WikiSnapshotV1) -> None:
        page_ids = tuple(page.page_id for page in snapshot.pages)
        if page_ids != tuple(sorted(set(page_ids))):
            raise JueWikiRepositoryIntegrityError("snapshot_pages_not_canonical")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO wiki_snapshots_v1 (
                    snapshot_id, scope, schema_version, compiler_version,
                    candidate_ids_json, created_at, published
                ) VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.scope,
                    snapshot.schema_version,
                    snapshot.compiler_version,
                    _json_dumps(snapshot.candidate_artifact_ids),
                    snapshot.created_at,
                ),
            )
            for page in snapshot.pages:
                page_json = _json_dumps(page.to_dict())
                conn.execute(
                    """
                    INSERT INTO wiki_pages_v3 (
                        snapshot_id, page_id, page_json, content_hash
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        snapshot.snapshot_id,
                        page.page_id,
                        page_json,
                        hashlib.sha256(page_json.encode("utf-8")).hexdigest(),
                    ),
                )
            conn.execute(
                """
                UPDATE wiki_snapshots_v1
                SET published = 0
                WHERE scope = ? AND published = 1
                """,
                (snapshot.scope,),
            )
            conn.execute(
                """
                UPDATE wiki_snapshots_v1
                SET published = 1
                WHERE snapshot_id = ?
                """,
                (snapshot.snapshot_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def current_snapshot(self, scope: str) -> WikiSnapshotV1 | None:
        with self.open_read_only() as conn:
            row = conn.execute(
                """
                SELECT snapshot_id, scope, schema_version, compiler_version,
                       candidate_ids_json, created_at
                FROM wiki_snapshots_v1
                WHERE scope = ? AND published = 1
                """,
                (scope,),
            ).fetchone()
        if row is None:
            return None
        snapshot_id = str(row["snapshot_id"])
        return WikiSnapshotV1(
            snapshot_id=snapshot_id,
            scope=str(row["scope"]),
            candidate_artifact_ids=tuple(json.loads(row["candidate_ids_json"])),
            pages=self.pages_for_snapshot(snapshot_id),
            schema_version=str(row["schema_version"]),
            compiler_version=str(row["compiler_version"]),
            created_at=str(row["created_at"]),
        )

    def pages_for_snapshot(self, snapshot_id: str) -> tuple[JueWikiPageV3, ...]:
        with self.open_read_only() as conn:
            rows = conn.execute(
                """
                SELECT page_json
                FROM wiki_pages_v3
                WHERE snapshot_id = ?
                ORDER BY page_id
                """,
                (snapshot_id,),
            ).fetchall()
        return tuple(_page_from_json(str(row["page_json"])) for row in rows)

    @contextmanager
    def open_read_only(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
