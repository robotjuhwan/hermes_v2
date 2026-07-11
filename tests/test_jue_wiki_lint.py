from __future__ import annotations

from dataclasses import replace

import pytest

from tradecraft.services.jue_wiki_compiler import (
    JueWikiCompilerV1,
    JueWikiPublisherV1,
    WikiPublicationError,
)
from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    WikiClaimV3,
    WikiRelationshipV1,
    WikiSnapshotV1,
)
from tradecraft.services.jue_wiki_lint import lint_snapshot


def _artifact() -> CandidateArtifactV1:
    evidence = EvidenceRefV1(
        evidence_id="evidence:one",
        source_type="test",
        source_id="one",
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )
    claim = WikiClaimV3(
        claim_id="claim:kis:005930:direction",
        claim_type="interpretation",
        text="Revision direction is positive.",
        status="verified",
        scope="kis",
        evidence=(evidence,),
        symbols=("005930",),
        confidence=0.8,
    )
    return CandidateArtifactV1(
        artifact_id="one",
        scope="kis",
        extractor_version="test_v1",
        input_hash="b" * 64,
        source_refs=(evidence,),
        claims=(claim,),
        created_at="2026-07-11T00:00:00+00:00",
    )


def _snapshot() -> WikiSnapshotV1:
    artifact = _artifact()
    return JueWikiCompilerV1().compile(
        scope="kis", artifacts=(artifact,), base_snapshot=None
    )


def test_lint_rejects_verified_claim_with_unknown_evidence() -> None:
    findings = lint_snapshot(_snapshot(), known_evidence_ids=set())

    assert [(row.severity, row.finding_type) for row in findings] == [
        ("error", "unresolved_evidence")
    ]


def test_lint_accepts_valid_snapshot() -> None:
    findings = lint_snapshot(_snapshot(), known_evidence_ids={"evidence:one"})

    assert findings == ()


def test_lint_without_evidence_registry_skips_resolution_check() -> None:
    assert lint_snapshot(_snapshot()) == ()


def test_lint_finds_empty_hash_scope_leak_and_duplicate_ids() -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    evidence = replace(page.claims[0].evidence[0], content_hash="")
    leaking_claim = replace(
        page.claims[0],
        claim_id="claim:kis:005930:leak",
        scope="binance",
        status="draft",
        evidence=(evidence, evidence),
    )
    duplicate_page = replace(
        page,
        claims=(leaking_claim,),
        relationships=(),
    )
    invalid = replace(snapshot, pages=(duplicate_page, duplicate_page))

    findings = lint_snapshot(invalid, known_evidence_ids={"evidence:one"})

    assert {row.finding_type for row in findings if row.severity == "error"} == {
        "cross_scope_claim",
        "duplicate_claim_id",
        "duplicate_evidence_id",
        "duplicate_page_id",
        "empty_hash",
    }


def test_lint_finds_dangling_relationships_and_invalid_lifecycle() -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    claim = replace(page.claims[0], status="superseded")
    relations = (
        WikiRelationshipV1(
            source_claim_id="claim:missing",
            relationship_type="supports",
            target_id=claim.claim_id,
        ),
        WikiRelationshipV1(
            source_claim_id=claim.claim_id,
            relationship_type="depends_on",
            target_id="claim:missing",
        ),
    )
    invalid = replace(snapshot, pages=(replace(page, claims=(claim,), relationships=relations),))

    findings = lint_snapshot(invalid, known_evidence_ids={"evidence:one"})

    finding_types = {row.finding_type for row in findings if row.severity == "error"}
    assert finding_types == {"dangling_relationship", "invalid_lifecycle_transition"}


def test_lint_rejects_supersedes_target_that_is_still_active() -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    old_claim = replace(
        page.claims[0], claim_id="claim:kis:005930:old-direction"
    )
    relation = WikiRelationshipV1(
        source_claim_id=page.claims[0].claim_id,
        relationship_type="supersedes",
        target_id=old_claim.claim_id,
    )
    invalid = replace(
        snapshot,
        pages=(
            replace(
                page,
                claims=(page.claims[0], old_claim),
                relationships=(relation,),
            ),
        ),
    )

    findings = lint_snapshot(invalid, known_evidence_ids={"evidence:one"})

    assert "invalid_lifecycle_transition" in {
        row.finding_type for row in findings if row.severity == "error"
    }


def test_lint_rejects_duplicate_candidate_artifact_ids() -> None:
    snapshot = replace(
        _snapshot(), candidate_artifact_ids=("one", "one")
    )

    findings = lint_snapshot(snapshot, known_evidence_ids={"evidence:one"})

    assert [row.finding_type for row in findings] == [
        "duplicate_candidate_artifact_id"
    ]


def test_lint_warns_for_stale_orphan_conflict_and_low_confidence() -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    stale = replace(page.claims[0], status="stale", confidence=0.2)
    conflicted = replace(
        page.claims[0],
        claim_id="claim:kis:005930:conflict",
        status="conflicted",
    )
    warning_page = replace(page, claims=(stale, conflicted), relationships=())
    orphan_page = replace(
        page,
        page_id="page:orphan",
        title="orphan",
        claims=(),
        relationships=(),
        status="draft",
    )
    warning_snapshot = replace(snapshot, pages=(warning_page, orphan_page))

    findings = lint_snapshot(
        warning_snapshot,
        known_evidence_ids={"evidence:one"},
    )

    assert {row.finding_type for row in findings if row.severity == "warning"} == {
        "low_confidence",
        "missing_counter_thesis",
        "orphan_page",
        "stale_claim",
    }


def test_lint_warns_for_zero_confidence() -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    zero_confidence = replace(page.claims[0], confidence=0.0)
    snapshot = replace(snapshot, pages=(replace(page, claims=(zero_confidence,)),))

    findings = lint_snapshot(snapshot, known_evidence_ids={"evidence:one"})

    assert "low_confidence" in {row.finding_type for row in findings}


def test_self_contradiction_does_not_supply_counter_thesis() -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    conflicted = replace(page.claims[0], status="conflicted")
    self_edge = WikiRelationshipV1(
        source_claim_id=conflicted.claim_id,
        relationship_type="contradicts",
        target_id=conflicted.claim_id,
    )
    snapshot = replace(
        snapshot,
        pages=(replace(page, claims=(conflicted,), relationships=(self_edge,)),),
    )

    findings = lint_snapshot(snapshot, known_evidence_ids={"evidence:one"})

    assert "missing_counter_thesis" in {row.finding_type for row in findings}


@pytest.mark.parametrize("cycle_length", [2, 3])
def test_lint_rejects_directed_supersedes_cycle_deterministically(
    cycle_length: int,
) -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    claims = tuple(
        replace(
            page.claims[0],
            claim_id=f"claim:kis:005930:{suffix}",
            status="superseded",
        )
        for suffix in ("a", "b", "c")[:cycle_length]
    )
    relations = tuple(
        WikiRelationshipV1(
            source_claim_id=claims[index].claim_id,
            relationship_type="supersedes",
            target_id=claims[(index + 1) % len(claims)].claim_id,
        )
        for index in range(len(claims))
    )
    cyclic = replace(
        snapshot,
        pages=(replace(page, claims=claims, relationships=relations),),
    )

    first = lint_snapshot(cyclic, known_evidence_ids={"evidence:one"})
    second = lint_snapshot(cyclic, known_evidence_ids={"evidence:one"})

    cycle_findings = [
        row
        for row in first
        if row.finding_type == "invalid_lifecycle_transition"
        and "cycle" in row.message
    ]
    assert first == second
    assert {row.claim_id for row in cycle_findings} == {
        claim.claim_id for claim in claims
    }


def test_lint_rejects_rejected_supersedes_source() -> None:
    snapshot = _snapshot()
    page = snapshot.pages[0]
    source = replace(page.claims[0], status="rejected")
    target = replace(
        page.claims[0],
        claim_id="claim:kis:005930:old-direction",
        status="superseded",
    )
    relation = WikiRelationshipV1(
        source_claim_id=source.claim_id,
        relationship_type="supersedes",
        target_id=target.claim_id,
    )
    invalid = replace(
        snapshot,
        pages=(replace(page, claims=(source, target), relationships=(relation,)),),
    )

    findings = lint_snapshot(invalid, known_evidence_ids={"evidence:one"})

    assert any(
        row.finding_type == "invalid_lifecycle_transition"
        and row.claim_id == source.claim_id
        and "rejected" in row.message
        for row in findings
    )


class _RecordingRepository:
    def __init__(self, artifact: CandidateArtifactV1) -> None:
        self.artifact = artifact
        self.published: list[WikiSnapshotV1] = []

    def candidate_artifacts(
        self, artifact_ids: tuple[str, ...]
    ) -> tuple[CandidateArtifactV1, ...]:
        assert artifact_ids == (self.artifact.artifact_id,)
        return (self.artifact,)

    def current_snapshot(self, scope: str) -> WikiSnapshotV1 | None:
        assert scope == "kis"
        return self.published[-1] if self.published else None

    def evidence_ids(self) -> set[str]:
        return set()

    def publish_snapshot(self, snapshot: WikiSnapshotV1) -> None:
        self.published.append(snapshot)


def test_publisher_does_not_publish_when_lint_fails() -> None:
    repository = _RecordingRepository(_artifact())
    publisher = JueWikiPublisherV1(repository)

    with pytest.raises(WikiPublicationError, match="wiki_snapshot_lint_failed"):
        publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert repository.published == []


def test_publisher_rejects_colliding_evidence_payloads() -> None:
    artifact = _artifact()
    evidence = artifact.claims[0].evidence[0]
    collision = replace(
        evidence,
        source_id="different-source",
        content_hash="f" * 64,
    )
    artifact = replace(
        artifact,
        claims=(replace(artifact.claims[0], evidence=(evidence, collision)),),
    )
    repository = _RecordingRepository(artifact)
    repository.evidence_ids = lambda: {"evidence:one"}  # type: ignore[method-assign]
    publisher = JueWikiPublisherV1(repository)

    with pytest.raises(WikiPublicationError, match="wiki_snapshot_lint_failed"):
        publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert repository.published == []


def test_publisher_publishes_linted_snapshot() -> None:
    repository = _RecordingRepository(_artifact())
    repository.evidence_ids = lambda: {"evidence:one"}  # type: ignore[method-assign]
    publisher = JueWikiPublisherV1(repository)

    snapshot = publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert repository.published == [snapshot]


def test_publisher_skips_identical_snapshot_but_publishes_changed_snapshot() -> None:
    artifact = _artifact()
    repository = _RecordingRepository(artifact)
    repository.evidence_ids = lambda: {"evidence:one"}  # type: ignore[method-assign]
    publisher = JueWikiPublisherV1(repository)

    first = publisher.compile_and_publish(scope="kis", artifact_ids=("one",))
    repeated = publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert repeated == first
    assert repository.published == [first]

    added_claim = replace(
        artifact.claims[0],
        claim_id="claim:kis:005930:valuation",
        text="Valuation is below its recent median.",
    )
    repository.artifact = replace(
        artifact,
        input_hash="c" * 64,
        claims=(*artifact.claims, added_claim),
        created_at="2026-07-12T00:00:00+00:00",
    )

    changed = publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert changed.snapshot_id != first.snapshot_id
    assert repository.published == [first, changed]


def test_publisher_lints_identical_snapshot_before_idempotent_return() -> None:
    repository = _RecordingRepository(_artifact())
    repository.evidence_ids = lambda: {"evidence:one"}  # type: ignore[method-assign]
    publisher = JueWikiPublisherV1(repository)
    first = publisher.compile_and_publish(scope="kis", artifact_ids=("one",))
    repository.evidence_ids = lambda: set()  # type: ignore[method-assign]

    with pytest.raises(WikiPublicationError, match="wiki_snapshot_lint_failed"):
        publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert repository.published == [first]


def test_publisher_exposes_compile_and_publish_stages() -> None:
    repository = _RecordingRepository(_artifact())
    publisher = JueWikiPublisherV1(repository)

    snapshot = publisher.compile_snapshot(scope="kis", artifact_ids=("one",))

    assert repository.published == []
    assert publisher.publish_snapshot(snapshot) == snapshot
    assert repository.published == [snapshot]
    assert publisher.publish_snapshot(snapshot) == snapshot
    assert repository.published == [snapshot]


def test_publisher_compile_failure_is_distinct_from_publication_errors() -> None:
    class FailingCompiler:
        def compile(self, **kwargs: object) -> WikiSnapshotV1:
            raise RuntimeError(f"compile failed:{kwargs['scope']}")

    repository = _RecordingRepository(_artifact())
    publisher = JueWikiPublisherV1(repository, compiler=FailingCompiler())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="compile failed:kis") as exc_info:
        publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert not isinstance(exc_info.value, WikiPublicationError)
    assert repository.published == []


def test_publisher_lint_failure_exposes_lint_stage() -> None:
    repository = _RecordingRepository(_artifact())
    publisher = JueWikiPublisherV1(repository)

    with pytest.raises(WikiPublicationError) as exc_info:
        publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert str(exc_info.value) == "wiki_snapshot_lint_failed"
    assert exc_info.value.stage == "lint"
    assert repository.published == []


def test_publisher_storage_failure_exposes_publish_stage() -> None:
    class FailingStorageRepository(_RecordingRepository):
        def publish_snapshot(self, snapshot: WikiSnapshotV1) -> None:
            raise OSError(f"storage unavailable:{snapshot.snapshot_id}")

    repository = FailingStorageRepository(_artifact())
    repository.evidence_ids = lambda: {"evidence:one"}  # type: ignore[method-assign]
    publisher = JueWikiPublisherV1(repository)

    with pytest.raises(WikiPublicationError) as exc_info:
        publisher.compile_and_publish(scope="kis", artifact_ids=("one",))

    assert str(exc_info.value) == "wiki_snapshot_publish_failed"
    assert exc_info.value.stage == "publish"
    assert repository.published == []
