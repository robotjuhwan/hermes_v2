from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from tradecraft.services.jue_wiki_contract import (
    WikiRelationshipV1,
    WikiSnapshotV1,
)


@dataclass(frozen=True, slots=True)
class WikiLintFindingV1:
    severity: Literal["warning", "error"]
    finding_type: str
    page_id: str
    claim_id: str = ""
    message: str = ""


def _finding_key(row: WikiLintFindingV1) -> tuple[str, str, str, str, str]:
    severity_order = "0" if row.severity == "error" else "1"
    return (
        severity_order,
        row.finding_type,
        row.page_id,
        row.claim_id,
        row.message,
    )


def _supersedes_cycle_nodes(
    relationships: tuple[WikiRelationshipV1, ...],
    claim_ids: set[str],
) -> set[str]:
    adjacency: dict[str, set[str]] = {claim_id: set() for claim_id in claim_ids}
    for relationship in relationships:
        if (
            relationship.relationship_type == "supersedes"
            and relationship.source_claim_id in claim_ids
            and relationship.target_id in claim_ids
        ):
            adjacency[relationship.source_claim_id].add(relationship.target_id)
    cycle_nodes: set[str] = set()
    for start in sorted(claim_ids):
        pending = list(sorted(adjacency[start], reverse=True))
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == start:
                cycle_nodes.add(start)
                break
            if current in visited:
                continue
            visited.add(current)
            pending.extend(sorted(adjacency[current], reverse=True))
    return cycle_nodes


def lint_snapshot(
    snapshot: WikiSnapshotV1,
    known_evidence_ids: set[str] | None = None,
) -> tuple[WikiLintFindingV1, ...]:
    known_ids = known_evidence_ids
    findings: list[WikiLintFindingV1] = []
    page_counts = Counter(page.page_id for page in snapshot.pages)
    artifact_counts = Counter(snapshot.candidate_artifact_ids)
    claim_counts = Counter(
        claim.claim_id for page in snapshot.pages for claim in page.claims
    )
    claim_ids = set(claim_counts)
    claims_by_id = {
        claim.claim_id: claim
        for page in snapshot.pages
        for claim in page.claims
    }
    page_id_by_claim_id = {
        claim.claim_id: page.page_id
        for page in snapshot.pages
        for claim in page.claims
    }
    relationships = tuple(
        relationship
        for page in snapshot.pages
        for relationship in page.relationships
    )
    incoming_supersedes = {
        relationship.target_id
        for relationship in relationships
        if relationship.relationship_type == "supersedes"
    }
    contradiction_ids = {
        relationship.source_claim_id
        for relationship in relationships
        if relationship.relationship_type == "contradicts"
        and relationship.source_claim_id != relationship.target_id
        and relationship.source_claim_id in claim_ids
        and relationship.target_id in claim_ids
    } | {
        relationship.target_id
        for relationship in relationships
        if relationship.relationship_type == "contradicts"
        and relationship.source_claim_id != relationship.target_id
        and relationship.source_claim_id in claim_ids
        and relationship.target_id in claim_ids
    }
    relationship_counts = Counter(relationships)
    cycle_nodes = _supersedes_cycle_nodes(relationships, claim_ids)

    for artifact_id, count in artifact_counts.items():
        if count > 1:
            findings.append(
                WikiLintFindingV1(
                    severity="error",
                    finding_type="duplicate_candidate_artifact_id",
                    page_id="",
                    claim_id=artifact_id,
                    message="candidate artifact ID occurs more than once",
                )
            )

    for claim_id in sorted(cycle_nodes):
        findings.append(
            WikiLintFindingV1(
                severity="error",
                finding_type="invalid_lifecycle_transition",
                page_id=page_id_by_claim_id.get(claim_id, ""),
                claim_id=claim_id,
                message="supersedes cycle includes claim",
            )
        )

    for page in snapshot.pages:
        if page_counts[page.page_id] > 1:
            findings.append(
                WikiLintFindingV1(
                    severity="error",
                    finding_type="duplicate_page_id",
                    page_id=page.page_id,
                    message="page_id occurs more than once",
                )
            )
        if page.scope != snapshot.scope:
            findings.append(
                WikiLintFindingV1(
                    severity="error",
                    finding_type="cross_scope_claim",
                    page_id=page.page_id,
                    message="page scope does not match snapshot scope",
                )
            )
        if not page.claims:
            findings.append(
                WikiLintFindingV1(
                    severity="warning",
                    finding_type="orphan_page",
                    page_id=page.page_id,
                    message="page has no claims",
                )
            )
        for claim in page.claims:
            if claim_counts[claim.claim_id] > 1:
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="duplicate_claim_id",
                        page_id=page.page_id,
                        claim_id=claim.claim_id,
                        message="claim_id occurs more than once",
                    )
                )
            if claim.scope != snapshot.scope or claim.scope != page.scope:
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="cross_scope_claim",
                        page_id=page.page_id,
                        claim_id=claim.claim_id,
                        message="claim scope does not match page and snapshot scope",
                    )
                )
            evidence_counts = Counter(row.evidence_id for row in claim.evidence)
            for evidence in claim.evidence:
                if evidence_counts[evidence.evidence_id] > 1:
                    findings.append(
                        WikiLintFindingV1(
                            severity="error",
                            finding_type="duplicate_evidence_id",
                            page_id=page.page_id,
                            claim_id=claim.claim_id,
                            message=evidence.evidence_id,
                        )
                    )
                if not evidence.content_hash.strip():
                    findings.append(
                        WikiLintFindingV1(
                            severity="error",
                            finding_type="empty_hash",
                            page_id=page.page_id,
                            claim_id=claim.claim_id,
                            message=evidence.evidence_id,
                        )
                    )
                if (
                    claim.status == "verified"
                    and known_ids is not None
                    and evidence.evidence_id not in known_ids
                ):
                    findings.append(
                        WikiLintFindingV1(
                            severity="error",
                            finding_type="unresolved_evidence",
                            page_id=page.page_id,
                            claim_id=claim.claim_id,
                            message=evidence.evidence_id,
                        )
                    )
            if claim.status == "superseded" and claim.claim_id not in incoming_supersedes:
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="invalid_lifecycle_transition",
                        page_id=page.page_id,
                        claim_id=claim.claim_id,
                        message="superseded claim has no supersedes relationship",
                    )
                )
            if claim.status == "stale":
                findings.append(
                    WikiLintFindingV1(
                        severity="warning",
                        finding_type="stale_claim",
                        page_id=page.page_id,
                        claim_id=claim.claim_id,
                        message="claim is stale",
                    )
                )
            if claim.status == "conflicted" and claim.claim_id not in contradiction_ids:
                findings.append(
                    WikiLintFindingV1(
                        severity="warning",
                        finding_type="missing_counter_thesis",
                        page_id=page.page_id,
                        claim_id=claim.claim_id,
                        message="conflicted claim has no contradiction relationship",
                    )
                )
            if claim.confidence < 0.5:
                findings.append(
                    WikiLintFindingV1(
                        severity="warning",
                        finding_type="low_confidence",
                        page_id=page.page_id,
                        claim_id=claim.claim_id,
                        message=f"confidence={claim.confidence:.4f}",
                    )
                )
        for relationship in page.relationships:
            if relationship_counts[relationship] > 1:
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="duplicate_relationship",
                        page_id=page.page_id,
                        claim_id=relationship.source_claim_id,
                        message=relationship.target_id,
                    )
                )
            dangling_source = relationship.source_claim_id not in claim_ids
            dangling_target = (
                relationship.relationship_type != "applies_to"
                and relationship.target_id not in claim_ids
            )
            if dangling_source or dangling_target:
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="dangling_relationship",
                        page_id=page.page_id,
                        claim_id=relationship.source_claim_id,
                        message=relationship.target_id,
                    )
                )
            if (
                relationship.relationship_type == "supersedes"
                and relationship.source_claim_id == relationship.target_id
            ):
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="invalid_lifecycle_transition",
                        page_id=page.page_id,
                        claim_id=relationship.source_claim_id,
                        message="claim cannot supersede itself",
                    )
                )
            target_claim = claims_by_id.get(relationship.target_id)
            source_claim = claims_by_id.get(relationship.source_claim_id)
            if (
                relationship.relationship_type == "supersedes"
                and source_claim is not None
                and source_claim.status == "rejected"
            ):
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="invalid_lifecycle_transition",
                        page_id=page.page_id,
                        claim_id=relationship.source_claim_id,
                        message="rejected claim cannot supersede another claim",
                    )
                )
            if (
                relationship.relationship_type == "supersedes"
                and target_claim is not None
                and target_claim.status != "superseded"
            ):
                findings.append(
                    WikiLintFindingV1(
                        severity="error",
                        finding_type="invalid_lifecycle_transition",
                        page_id=page.page_id,
                        claim_id=relationship.target_id,
                        message="supersedes target is still active",
                    )
                )

    return tuple(sorted(set(findings), key=_finding_key))
