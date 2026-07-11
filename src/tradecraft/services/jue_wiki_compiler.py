from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Protocol

from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiRelationshipV1,
    WikiSnapshotV1,
)
from tradecraft.services.jue_wiki_lint import lint_snapshot


SCHEMA_VERSION = "jue_wiki_page_v3"
COMPILER_VERSION = "wiki_compiler_v1"
_DERIVED_ID_PATTERN = re.compile(
    r"^(?P<original>.+):(?:conflict|version):[0-9a-f]{16}$"
)


class WikiPublicationError(RuntimeError):
    def __init__(self, message: str, *, stage: str = "") -> None:
        super().__init__(message)
        self.stage = stage


class _WikiRepository(Protocol):
    def candidate_artifacts(
        self, artifact_ids: tuple[str, ...]
    ) -> tuple[CandidateArtifactV1, ...]: ...

    def current_snapshot(self, scope: str) -> WikiSnapshotV1 | None: ...

    def evidence_ids(self) -> set[str]: ...

    def publish_snapshot(self, snapshot: WikiSnapshotV1) -> None: ...


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Any) -> str:
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split())


def _stable_key(value: str) -> str:
    return _normalized_text(value).casefold()


def _relationship_key(row: WikiRelationshipV1) -> tuple[str, str, str]:
    return (row.source_claim_id, row.relationship_type, row.target_id)


def _evidence_key(row: EvidenceRefV1) -> tuple[str, str]:
    return (row.evidence_id, _json_dumps(row.to_dict()))


def _claim_sort_key(row: WikiClaimV3) -> tuple[str, str]:
    return (row.claim_id, _json_dumps(row.to_dict()))


def _derived_claim_identity(claim: WikiClaimV3) -> tuple[str, str] | None:
    match = _DERIVED_ID_PATTERN.fullmatch(claim.claim_id)
    if match is None:
        return None
    suffix = claim.claim_id[len(match.group("original")) + 1 :]
    kind = suffix.split(":", 1)[0]
    identity_hash = _digest(
        {
            "claim_id": match.group("original"),
            "text": _normalized_text(claim.text),
        }
    )[:16]
    expected_claim_id = (
        f"{match.group('original')}:{kind}:{identity_hash}"
    )
    if claim.claim_id != expected_claim_id:
        return None
    expected_provenance = _derived_provenance(
        kind,
        match.group("original"),
        claim.text,
    )
    if claim.provenance_id != expected_provenance:
        return None
    return (match.group("original"), kind)


def _semantic_claim_id(claim: WikiClaimV3) -> str:
    identity = _derived_claim_identity(claim)
    return identity[0] if identity is not None else claim.claim_id


def _derived_provenance(kind: str, claim_id: str, text: str) -> str:
    identity_hash = _digest(
        {"claim_id": claim_id, "text": _normalized_text(text)}
    )[:16]
    return f"{COMPILER_VERSION}:derived:{kind}:{identity_hash}"


def _page_key(scope: str, claim: WikiClaimV3) -> tuple[str, str, str]:
    symbols = tuple(sorted(set(claim.symbols)))
    if symbols:
        return (scope, "symbol", symbols[0])
    claim_id = _semantic_claim_id(claim)
    topic = claim_id.rsplit(":", 1)[0] if ":" in claim_id else claim_id
    return (scope, "core", _stable_key(topic))


def _timestamp_in_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_created_at(artifacts: tuple[CandidateArtifactV1, ...]) -> str:
    def sort_key(artifact: CandidateArtifactV1) -> tuple[datetime, str, str]:
        return (
            _timestamp_in_utc(artifact.created_at),
            artifact.created_at,
            artifact.artifact_id,
        )

    return max(artifacts, key=sort_key).created_at


def _merge_claims(claims: list[WikiClaimV3]) -> WikiClaimV3:
    ordered = sorted(claims, key=_claim_sort_key)
    canonical = ordered[0]
    evidence_by_payload: dict[str, EvidenceRefV1] = {}
    for claim in ordered:
        for evidence in sorted(claim.evidence, key=_evidence_key):
            payload = _json_dumps(evidence.to_dict())
            evidence_by_payload.setdefault(payload, evidence)
    status_priority = {
        "rejected": 0,
        "draft": 1,
        "stale": 2,
        "superseded": 3,
        "verified": 4,
        "conflicted": 5,
    }
    status = max(
        (claim.status for claim in ordered),
        key=lambda value: (status_priority[value], value),
    )
    return replace(
        canonical,
        text=_normalized_text(canonical.text),
        status=status,
        evidence=tuple(sorted(evidence_by_payload.values(), key=_evidence_key)),
        symbols=tuple(sorted({row for claim in ordered for row in claim.symbols})),
        venues=tuple(sorted({row for claim in ordered for row in claim.venues})),
        strategies=tuple(sorted({row for claim in ordered for row in claim.strategies})),
        regimes=tuple(sorted({row for claim in ordered for row in claim.regimes})),
        confidence=max(claim.confidence for claim in ordered),
    )


def _page_status(claims: tuple[WikiClaimV3, ...]) -> str:
    for status in (
        "conflicted",
        "verified",
        "stale",
        "superseded",
        "draft",
        "rejected",
    ):
        if any(claim.status == status for claim in claims):
            return status
    return "draft"


class JueWikiCompilerV1:
    def compile(
        self,
        *,
        scope: str,
        artifacts: tuple[CandidateArtifactV1, ...],
        base_snapshot: WikiSnapshotV1 | None,
    ) -> WikiSnapshotV1:
        if base_snapshot is not None and base_snapshot.scope != scope:
            raise ValueError("base_snapshot_scope_mismatch")

        ordered_artifacts = tuple(sorted(artifacts, key=lambda row: row.artifact_id))
        all_claims = [
            claim
            for page in (base_snapshot.pages if base_snapshot else ())
            for claim in page.claims
        ]
        all_claims.extend(
            claim for artifact in ordered_artifacts for claim in artifact.claims
        )
        variant_recency: dict[
            tuple[str, str], tuple[datetime, int, str]
        ] = {}
        for page in base_snapshot.pages if base_snapshot else ():
            for claim in page.claims:
                identity = (
                    _semantic_claim_id(claim),
                    _normalized_text(claim.text).casefold(),
                )
                variant_recency[identity] = (
                    _timestamp_in_utc(
                        base_snapshot.created_at if base_snapshot else ""
                    ),
                    0,
                    claim.claim_id,
                )
        for artifact in ordered_artifacts:
            for claim in artifact.claims:
                identity = (
                    _semantic_claim_id(claim),
                    _normalized_text(claim.text).casefold(),
                )
                variant_recency[identity] = max(
                    variant_recency.get(
                        identity,
                        (datetime.min.replace(tzinfo=timezone.utc), -1, ""),
                    ),
                    (
                        _timestamp_in_utc(artifact.created_at),
                        1,
                        artifact.artifact_id,
                    ),
                )
        explicit_relationships = [
            relation
            for page in (base_snapshot.pages if base_snapshot else ())
            for relation in page.relationships
        ]
        explicit_relationships.extend(
            relation
            for artifact in ordered_artifacts
            for relation in artifact.relationships
        )

        variants: dict[tuple[str, str], list[WikiClaimV3]] = {}
        for claim in sorted(all_claims, key=_claim_sort_key):
            key = (
                _semantic_claim_id(claim),
                _normalized_text(claim.text).casefold(),
            )
            variants.setdefault(key, []).append(claim)
        merged_variants: dict[tuple[str, str], WikiClaimV3] = {}
        for key, rows in sorted(variants.items()):
            merged = _merge_claims(rows)
            derived_rows = sorted(
                (
                    row
                    for row in rows
                    if _derived_claim_identity(row) is not None
                ),
                key=_claim_sort_key,
            )
            if derived_rows:
                derived = derived_rows[0]
                merged = replace(
                    merged,
                    claim_id=derived.claim_id,
                    status=derived.status,
                    provenance_id=derived.provenance_id,
                )
            else:
                merged = replace(merged, claim_id=key[0])
            merged_variants[key] = merged

        variants_by_id: dict[str, list[WikiClaimV3]] = {}
        for (claim_id, _), claim in merged_variants.items():
            variants_by_id.setdefault(claim_id, []).append(claim)

        resolved_claims: list[WikiClaimV3] = []
        id_map: dict[str, tuple[str, ...]] = {}
        conflict_relationships: list[WikiRelationshipV1] = []
        version_relationships: list[WikiRelationshipV1] = []
        versioned_self_supersedes: set[str] = set()
        for claim_id, rows in sorted(variants_by_id.items()):
            ordered_rows = sorted(rows, key=_claim_sort_key)
            derived_kinds = {
                identity[1]
                for row in ordered_rows
                if (identity := _derived_claim_identity(row)) is not None
            }
            all_are_one_derived_kind = (
                len(derived_kinds) == 1
                and all(
                    _derived_claim_identity(row) is not None
                    for row in ordered_rows
                )
            )
            if all_are_one_derived_kind:
                resolved_claims.extend(ordered_rows)
                if derived_kinds == {"version"}:
                    active_ids = tuple(
                        row.claim_id
                        for row in ordered_rows
                        if row.status != "superseded"
                    )
                    id_map[claim_id] = active_ids or tuple(
                        row.claim_id for row in ordered_rows
                    )
                else:
                    id_map[claim_id] = tuple(
                        row.claim_id for row in ordered_rows
                    )
                continue
            has_self_supersedes = any(
                relation.relationship_type == "supersedes"
                and relation.source_claim_id == claim_id
                and relation.target_id == claim_id
                for relation in explicit_relationships
            )
            if len(ordered_rows) == 1:
                resolved_claims.extend(ordered_rows)
                id_map[claim_id] = tuple(row.claim_id for row in ordered_rows)
                continue
            if has_self_supersedes:
                newest = max(
                    ordered_rows,
                    key=lambda row: (
                        variant_recency.get(
                            (
                                claim_id,
                                _normalized_text(row.text).casefold(),
                            ),
                            (
                                datetime.min.replace(tzinfo=timezone.utc),
                                -1,
                                "",
                            ),
                        ),
                        _normalized_text(row.text),
                    ),
                )
                versioned = tuple(
                    replace(
                        row,
                        claim_id=(
                            f"{claim_id}:version:"
                            f"{_digest({'claim_id': claim_id, 'text': _normalized_text(row.text)})[:16]}"
                        ),
                        status=(
                            row.status if row is newest else "superseded"
                        ),
                        provenance_id=_derived_provenance(
                            "version", claim_id, row.text
                        ),
                    )
                    for row in ordered_rows
                )
                newest_id = next(
                    row.claim_id
                    for row, original in zip(versioned, ordered_rows)
                    if original is newest
                )
                resolved_claims.extend(versioned)
                id_map[claim_id] = (newest_id,)
                versioned_self_supersedes.add(claim_id)
                version_relationships.extend(
                    WikiRelationshipV1(
                        source_claim_id=newest_id,
                        relationship_type="supersedes",
                        target_id=row.claim_id,
                    )
                    for row in versioned
                    if row.claim_id != newest_id
                )
                continue
            disambiguated = tuple(
                replace(
                    row,
                    claim_id=(
                        f"{claim_id}:conflict:"
                        f"{_digest({'claim_id': claim_id, 'text': _normalized_text(row.text)})[:16]}"
                    ),
                    status="conflicted",
                    provenance_id=_derived_provenance(
                        "conflict", claim_id, row.text
                    ),
                )
                for row in ordered_rows
            )
            resolved_claims.extend(disambiguated)
            id_map[claim_id] = tuple(row.claim_id for row in disambiguated)
            for source in disambiguated:
                for target in disambiguated:
                    if source.claim_id != target.claim_id:
                        conflict_relationships.append(
                            WikiRelationshipV1(
                                source_claim_id=source.claim_id,
                                relationship_type="contradicts",
                                target_id=target.claim_id,
                            )
                        )

        resolved_by_identity = {
            (
                _semantic_claim_id(claim),
                _normalized_text(claim.text).casefold(),
            ): claim.claim_id
            for claim in resolved_claims
        }
        for claim in all_claims:
            original_id = _semantic_claim_id(claim)
            if claim.claim_id == original_id:
                continue
            resolved_id = resolved_by_identity.get(
                (original_id, _normalized_text(claim.text).casefold())
            )
            if resolved_id is not None:
                id_map[claim.claim_id] = (resolved_id,)

        remapped_relationships: list[WikiRelationshipV1] = []
        for relation in explicit_relationships:
            if (
                relation.relationship_type == "supersedes"
                and relation.source_claim_id == relation.target_id
                and relation.source_claim_id in versioned_self_supersedes
            ):
                continue
            source_ids = id_map.get(relation.source_claim_id, (relation.source_claim_id,))
            if relation.relationship_type == "applies_to":
                target_ids = (relation.target_id,)
            else:
                target_ids = id_map.get(relation.target_id, (relation.target_id,))
            for source_id in source_ids:
                for target_id in target_ids:
                    remapped_relationships.append(
                        replace(
                            relation,
                            source_claim_id=source_id,
                            target_id=target_id,
                        )
                    )
        remapped_relationships.extend(conflict_relationships)
        remapped_relationships.extend(version_relationships)
        relationships = tuple(
            sorted(set(remapped_relationships), key=_relationship_key)
        )

        superseded_ids = {
            relation.target_id
            for relation in relationships
            if relation.relationship_type == "supersedes"
        }
        resolved_claims = [
            replace(claim, status="superseded")
            if claim.claim_id in superseded_ids
            else claim
            for claim in resolved_claims
        ]

        grouped: dict[tuple[str, str, str], list[WikiClaimV3]] = {}
        for claim in sorted(resolved_claims, key=_claim_sort_key):
            grouped.setdefault(_page_key(scope, claim), []).append(claim)

        pages: list[JueWikiPageV3] = []
        for page_key, claim_rows in sorted(grouped.items()):
            _, page_type, key = page_key
            page_claims = tuple(sorted(claim_rows, key=_claim_sort_key))
            claim_ids = {claim.claim_id for claim in page_claims}
            page_relationships = tuple(
                relation
                for relation in relationships
                if relation.source_claim_id in claim_ids
            )
            page_identity = {"scope": scope, "page_type": page_type, "key": key}
            pages.append(
                JueWikiPageV3(
                    page_id=f"page:{_digest(page_identity)}",
                    page_type=page_type,
                    scope=scope,
                    title=key,
                    summary=page_claims[0].text if page_claims else "",
                    claims=page_claims,
                    relationships=page_relationships,
                    status=_page_status(page_claims),
                    schema_version=SCHEMA_VERSION,
                    compiler_version=COMPILER_VERSION,
                )
            )
        assigned_relationships = {
            relationship
            for page in pages
            for relationship in page.relationships
        }
        unassigned_relationships = tuple(
            relationship
            for relationship in relationships
            if relationship not in assigned_relationships
        )
        if unassigned_relationships and pages:
            first_page = pages[0]
            pages[0] = replace(
                first_page,
                relationships=tuple(
                    sorted(
                        {*first_page.relationships, *unassigned_relationships},
                        key=_relationship_key,
                    )
                ),
            )
        elif unassigned_relationships:
            orphan_identity = {
                "scope": scope,
                "page_type": "core",
                "key": "relationship-orphans",
            }
            pages.append(
                JueWikiPageV3(
                    page_id=f"page:{_digest(orphan_identity)}",
                    page_type="core",
                    scope=scope,
                    title="relationship-orphans",
                    summary="",
                    claims=(),
                    relationships=unassigned_relationships,
                    status="draft",
                    schema_version=SCHEMA_VERSION,
                    compiler_version=COMPILER_VERSION,
                )
            )
        canonical_pages = tuple(sorted(pages, key=lambda page: page.page_id))
        artifact_ids = tuple(
            sorted(
                {
                    *(base_snapshot.candidate_artifact_ids if base_snapshot else ()),
                    *(artifact.artifact_id for artifact in ordered_artifacts),
                }
            )
        )
        if ordered_artifacts:
            created_at = _latest_created_at(ordered_artifacts)
        elif base_snapshot is not None:
            created_at = base_snapshot.created_at
        else:
            created_at = ""
        snapshot_payload = {
            "scope": scope,
            "candidate_artifact_ids": artifact_ids,
            "pages": [page.to_dict() for page in canonical_pages],
            "schema_version": SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "created_at": created_at,
        }
        return WikiSnapshotV1(
            snapshot_id=f"snapshot:{_digest(snapshot_payload)}",
            scope=scope,
            candidate_artifact_ids=artifact_ids,
            pages=canonical_pages,
            schema_version=SCHEMA_VERSION,
            compiler_version=COMPILER_VERSION,
            created_at=created_at,
        )


class JueWikiPublisherV1:
    def __init__(
        self,
        repository: _WikiRepository,
        compiler: JueWikiCompilerV1 | None = None,
    ) -> None:
        self.repository = repository
        self.compiler = compiler or JueWikiCompilerV1()

    def compile_snapshot(
        self,
        *,
        scope: str,
        artifact_ids: tuple[str, ...],
    ) -> WikiSnapshotV1:
        artifacts = self.repository.candidate_artifacts(artifact_ids)
        current_snapshot = self.repository.current_snapshot(scope)
        return self.compiler.compile(
            scope=scope,
            artifacts=artifacts,
            base_snapshot=current_snapshot,
        )

    def publish_snapshot(self, snapshot: WikiSnapshotV1) -> WikiSnapshotV1:
        try:
            current_snapshot = self.repository.current_snapshot(snapshot.scope)
            if (
                current_snapshot is not None
                and snapshot.snapshot_id == current_snapshot.snapshot_id
            ):
                return current_snapshot
            self.repository.publish_snapshot(snapshot)
        except Exception as exc:
            raise WikiPublicationError(
                "wiki_snapshot_publish_failed",
                stage="publish",
            ) from exc
        return snapshot

    def compile_and_publish(
        self,
        *,
        scope: str,
        artifact_ids: tuple[str, ...],
    ) -> WikiSnapshotV1:
        snapshot = self.compile_snapshot(
            scope=scope,
            artifact_ids=artifact_ids,
        )
        findings = lint_snapshot(
            snapshot,
            known_evidence_ids=self.repository.evidence_ids(),
        )
        if any(row.severity == "error" for row in findings):
            raise WikiPublicationError(
                "wiki_snapshot_lint_failed",
                stage="lint",
            )
        return self.publish_snapshot(snapshot)
