from __future__ import annotations

from dataclasses import replace

from tradecraft.services.jue_wiki_compiler import JueWikiCompilerV1
from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    WikiClaimV3,
    WikiRelationshipV1,
)


def _artifact(
    *,
    artifact_id: str,
    claim_id: str,
    text: str,
    symbols: tuple[str, ...] = ("005930",),
    created_at: str = "2026-07-11T00:00:00+00:00",
    relationships: tuple[WikiRelationshipV1, ...] = (),
) -> CandidateArtifactV1:
    evidence = EvidenceRefV1(
        evidence_id=f"evidence:{artifact_id}",
        source_type="test",
        source_id=artifact_id,
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )
    claim = WikiClaimV3(
        claim_id=claim_id,
        claim_type="interpretation",
        text=text,
        status="verified",
        scope="kis",
        evidence=(evidence,),
        symbols=symbols,
        confidence=0.8,
    )
    return CandidateArtifactV1(
        artifact_id=artifact_id,
        scope="kis",
        extractor_version="test_v1",
        input_hash="b" * 64,
        source_refs=(evidence,),
        claims=(claim,),
        created_at=created_at,
        relationships=relationships,
    )


def test_same_candidate_set_compiles_to_same_page_payload() -> None:
    candidate_artifact = _artifact(
        artifact_id="one",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    compiler = JueWikiCompilerV1()

    first = compiler.compile(
        scope="kis", artifacts=(candidate_artifact,), base_snapshot=None
    )
    second = compiler.compile(
        scope="kis", artifacts=(candidate_artifact,), base_snapshot=None
    )

    assert first == second


def test_artifact_order_does_not_change_compiled_snapshot() -> None:
    first_artifact = _artifact(
        artifact_id="one",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    second_artifact = _artifact(
        artifact_id="two",
        claim_id="claim:kis:005930:valuation",
        text="Valuation is below its recent median.",
    )
    compiler = JueWikiCompilerV1()

    first = compiler.compile(
        scope="kis",
        artifacts=(first_artifact, second_artifact),
        base_snapshot=None,
    )
    second = compiler.compile(
        scope="kis",
        artifacts=(second_artifact, first_artifact),
        base_snapshot=None,
    )

    assert first == second
    assert first.candidate_artifact_ids == ("one", "two")


def test_contradicting_verified_claims_are_preserved_and_flagged() -> None:
    bullish_artifact = _artifact(
        artifact_id="bullish",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    bearish_artifact = _artifact(
        artifact_id="bearish",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is negative.",
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis",
        artifacts=(bullish_artifact, bearish_artifact),
        base_snapshot=None,
    )

    page = snapshot.pages[0]
    claim_ids = {claim.claim_id for claim in page.claims}
    assert len(claim_ids) == 2
    assert all(row.startswith("claim:kis:005930:direction:conflict:") for row in claim_ids)
    assert {claim.status for claim in page.claims} == {"conflicted"}
    assert all(
        claim.provenance_id.startswith("wiki_compiler_v1:derived:conflict:")
        for claim in page.claims
    )
    contradicts = {
        (relation.source_claim_id, relation.target_id)
        for relation in page.relationships
        if relation.relationship_type == "contradicts"
    }
    assert contradicts == {
        (source_id, target_id)
        for source_id in claim_ids
        for target_id in claim_ids
        if source_id != target_id
    }


def test_recompiling_conflicted_base_is_idempotent() -> None:
    bullish = _artifact(
        artifact_id="bullish",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    bearish = _artifact(
        artifact_id="bearish",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is negative.",
    )
    compiler = JueWikiCompilerV1()
    first = compiler.compile(
        scope="kis", artifacts=(bullish, bearish), base_snapshot=None
    )

    second = compiler.compile(
        scope="kis", artifacts=(bearish, bullish), base_snapshot=first
    )

    assert second == first


def test_core_conflicts_stay_on_original_topic_page() -> None:
    first = _artifact(
        artifact_id="first",
        claim_id="claim:kis:risk:limit",
        text="The limit should increase.",
        symbols=(),
    )
    second = _artifact(
        artifact_id="second",
        claim_id="claim:kis:risk:limit",
        text="The limit should decrease.",
        symbols=(),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(first, second), base_snapshot=None
    )

    assert snapshot.pages[0].title == "claim:kis:risk"


def test_normalized_duplicate_claims_merge_evidence() -> None:
    first = _artifact(
        artifact_id="one",
        claim_id="claim:kis:005930:direction",
        text="  Revision   direction is positive. ",
    )
    second = _artifact(
        artifact_id="two",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(second, first), base_snapshot=None
    )

    claim = snapshot.pages[0].claims[0]
    assert claim.claim_id == "claim:kis:005930:direction"
    assert claim.text == "Revision direction is positive."
    assert tuple(row.evidence_id for row in claim.evidence) == (
        "evidence:one",
        "evidence:two",
    )


def test_core_page_key_and_created_at_are_deterministic() -> None:
    older = _artifact(
        artifact_id="older",
        claim_id="claim:kis:risk:position-limit",
        text="Position limits must be enforced.",
        symbols=(),
        created_at="2026-07-10T23:00:00+00:00",
    )
    newer = _artifact(
        artifact_id="newer",
        claim_id="claim:kis:risk:kill-switch",
        text="The kill switch remains enabled.",
        symbols=(),
        created_at="2026-07-11T01:00:00+00:00",
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(newer, older), base_snapshot=None
    )

    assert len(snapshot.pages) == 1
    assert snapshot.pages[0].page_type == "core"
    assert snapshot.pages[0].title == "claim:kis:risk"
    assert snapshot.created_at == newer.created_at


def test_base_claims_and_explicit_relationships_are_preserved() -> None:
    old = _artifact(
        artifact_id="old",
        claim_id="claim:kis:005930:old-direction",
        text="Revision direction was neutral.",
    )
    compiler = JueWikiCompilerV1()
    base = compiler.compile(scope="kis", artifacts=(old,), base_snapshot=None)
    relation_rows = (
        WikiRelationshipV1(
            source_claim_id="claim:kis:005930:new-direction",
            relationship_type="supersedes",
            target_id="claim:kis:005930:old-direction",
        ),
        WikiRelationshipV1(
            source_claim_id="claim:kis:005930:new-direction",
            relationship_type="depends_on",
            target_id="claim:kis:005930:old-direction",
        ),
    )
    new = _artifact(
        artifact_id="new",
        claim_id="claim:kis:005930:new-direction",
        text="Revision direction is positive.",
        relationships=relation_rows,
        created_at="2026-07-12T00:00:00+00:00",
    )

    snapshot = compiler.compile(
        scope="kis", artifacts=(new,), base_snapshot=base
    )

    claims = {claim.claim_id: claim for claim in snapshot.pages[0].claims}
    assert set(claims) == {
        "claim:kis:005930:old-direction",
        "claim:kis:005930:new-direction",
    }
    assert claims["claim:kis:005930:old-direction"].status == "superseded"
    assert set(snapshot.pages[0].relationships) >= set(relation_rows)


def test_explicit_self_supersedes_versions_same_id_without_conflict() -> None:
    old = _artifact(
        artifact_id="old",
        claim_id="claim:kis:005930:direction",
        text="Revision direction was neutral.",
        created_at="2026-07-10T00:00:00+00:00",
    )
    compiler = JueWikiCompilerV1()
    base = compiler.compile(scope="kis", artifacts=(old,), base_snapshot=None)
    self_supersedes = WikiRelationshipV1(
        source_claim_id="claim:kis:005930:direction",
        relationship_type="supersedes",
        target_id="claim:kis:005930:direction",
    )
    new = _artifact(
        artifact_id="new",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
        created_at="2026-07-11T00:00:00+00:00",
        relationships=(self_supersedes,),
    )

    snapshot = compiler.compile(scope="kis", artifacts=(new,), base_snapshot=base)

    page = snapshot.pages[0]
    assert len({claim.claim_id for claim in page.claims}) == 2
    assert {claim.status for claim in page.claims} == {"superseded", "verified"}
    supersedes = [
        relation
        for relation in page.relationships
        if relation.relationship_type == "supersedes"
    ]
    assert len(supersedes) == 1
    assert supersedes[0].source_claim_id != supersedes[0].target_id


def test_self_supersession_base_recompile_preserves_derived_versions() -> None:
    old = _artifact(
        artifact_id="old",
        claim_id="claim:kis:005930:direction",
        text="Revision direction was neutral.",
        created_at="2026-07-10T00:00:00+00:00",
    )
    compiler = JueWikiCompilerV1()
    base = compiler.compile(scope="kis", artifacts=(old,), base_snapshot=None)
    relation = WikiRelationshipV1(
        source_claim_id="claim:kis:005930:direction",
        relationship_type="supersedes",
        target_id="claim:kis:005930:direction",
    )
    new = _artifact(
        artifact_id="new",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
        created_at="2026-07-11T00:00:00+00:00",
        relationships=(relation,),
    )
    versioned = compiler.compile(scope="kis", artifacts=(new,), base_snapshot=base)

    recompiled = compiler.compile(
        scope="kis", artifacts=(), base_snapshot=versioned
    )

    assert recompiled == versioned
    assert all(
        claim.provenance_id.startswith("wiki_compiler_v1:derived:version:")
        for claim in versioned.pages[0].claims
    )


def test_self_supersession_recency_compares_timestamp_instants() -> None:
    earlier = _artifact(
        artifact_id="earlier",
        claim_id="claim:kis:005930:direction",
        text="Earlier instant.",
        created_at="2026-07-11T01:00:00+09:00",
    )
    relation = WikiRelationshipV1(
        source_claim_id="claim:kis:005930:direction",
        relationship_type="supersedes",
        target_id="claim:kis:005930:direction",
    )
    later = _artifact(
        artifact_id="later",
        claim_id="claim:kis:005930:direction",
        text="Later instant.",
        created_at="2026-07-10T18:00:00+00:00",
        relationships=(relation,),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(earlier, later), base_snapshot=None
    )

    claims_by_text = {claim.text: claim for claim in snapshot.pages[0].claims}
    assert claims_by_text["Later instant."].status == "verified"
    assert claims_by_text["Earlier instant."].status == "superseded"
    assert snapshot.created_at == later.created_at


def test_evidence_id_collision_is_preserved_for_lint() -> None:
    first = _artifact(
        artifact_id="first",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    first_evidence = first.claims[0].evidence[0]
    colliding_evidence = replace(
        first_evidence,
        source_id="different-source",
        content_hash="c" * 64,
    )
    second_claim = replace(first.claims[0], evidence=(colliding_evidence,))
    second = replace(
        first,
        artifact_id="second",
        source_refs=(colliding_evidence,),
        claims=(second_claim,),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(first, second), base_snapshot=None
    )

    evidence = snapshot.pages[0].claims[0].evidence
    assert len(evidence) == 2
    assert {row.content_hash for row in evidence} == {"a" * 64, "c" * 64}


def test_byte_identical_evidence_rows_are_deduplicated() -> None:
    artifact = _artifact(
        artifact_id="duplicate-evidence",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    evidence = artifact.claims[0].evidence[0]
    artifact = replace(
        artifact,
        claims=(replace(artifact.claims[0], evidence=(evidence, evidence)),),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(artifact,), base_snapshot=None
    )

    assert snapshot.pages[0].claims[0].evidence == (evidence,)


def test_user_ids_with_derived_looking_suffixes_are_not_rewritten() -> None:
    for kind in ("conflict", "version"):
        user_claim_id = f"claim:kis:risk:{kind}:0123456789abcdef"
        artifact = _artifact(
            artifact_id=f"user-id-{kind}",
            claim_id=user_claim_id,
            text="This is a user-authored identifier.",
            symbols=(),
        )

        snapshot = JueWikiCompilerV1().compile(
            scope="kis", artifacts=(artifact,), base_snapshot=None
        )

        assert snapshot.pages[0].claims[0].claim_id == user_claim_id
        assert snapshot.pages[0].title == f"claim:kis:risk:{kind}"


def test_mismatched_compiler_version_id_suffix_is_not_authenticated() -> None:
    old = _artifact(
        artifact_id="old-version",
        claim_id="claim:kis:005930:direction",
        text="Revision direction was neutral.",
    )
    relation = WikiRelationshipV1(
        source_claim_id="claim:kis:005930:direction",
        relationship_type="supersedes",
        target_id="claim:kis:005930:direction",
    )
    new = _artifact(
        artifact_id="new-version",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
        created_at="2026-07-12T00:00:00+00:00",
        relationships=(relation,),
    )
    generated = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(old, new), base_snapshot=None
    )
    generated_claim = next(
        claim for claim in generated.pages[0].claims if claim.status == "verified"
    )
    original, _, _ = generated_claim.claim_id.rpartition(":")
    mismatched = replace(
        generated_claim,
        claim_id=f"{original}:{'0' * 16}",
        symbols=(),
    )
    artifact = replace(
        new,
        artifact_id="mismatched-version",
        claims=(mismatched,),
        relationships=(),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(artifact,), base_snapshot=None
    )

    assert snapshot.pages[0].claims[0].claim_id == mismatched.claim_id
    assert snapshot.pages[0].title == mismatched.claim_id.rsplit(":", 1)[0]


def test_mismatched_compiler_conflict_id_suffix_is_not_authenticated() -> None:
    bullish = _artifact(
        artifact_id="bullish-mismatch",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    bearish = _artifact(
        artifact_id="bearish-mismatch",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is negative.",
    )
    generated = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(bullish, bearish), base_snapshot=None
    )
    generated_claim = generated.pages[0].claims[0]
    original, _, _ = generated_claim.claim_id.rpartition(":")
    mismatched = replace(
        generated_claim,
        claim_id=f"{original}:{'f' * 16}",
        symbols=(),
    )
    artifact = replace(
        bullish,
        artifact_id="mismatched-conflict",
        claims=(mismatched,),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(artifact,), base_snapshot=None
    )

    assert snapshot.pages[0].claims[0].claim_id == mismatched.claim_id
    assert snapshot.pages[0].title == mismatched.claim_id.rsplit(":", 1)[0]


def test_dangling_explicit_source_relationship_is_preserved_for_lint() -> None:
    relation = WikiRelationshipV1(
        source_claim_id="claim:kis:missing",
        relationship_type="depends_on",
        target_id="claim:kis:005930:direction",
    )
    artifact = _artifact(
        artifact_id="one",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
        relationships=(relation,),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(artifact,), base_snapshot=None
    )

    assert relation in snapshot.pages[0].relationships


def test_empty_artifact_set_keeps_base_timestamp_and_pages() -> None:
    artifact = _artifact(
        artifact_id="one",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    compiler = JueWikiCompilerV1()
    base = compiler.compile(scope="kis", artifacts=(artifact,), base_snapshot=None)

    snapshot = compiler.compile(scope="kis", artifacts=(), base_snapshot=base)

    assert snapshot.pages == base.pages
    assert snapshot.created_at == base.created_at
    assert snapshot.candidate_artifact_ids == base.candidate_artifact_ids
    assert snapshot == compiler.compile(scope="kis", artifacts=(), base_snapshot=base)


def test_first_sorted_symbol_selects_symbol_page() -> None:
    artifact = _artifact(
        artifact_id="one",
        claim_id="claim:kis:portfolio:pair",
        text="The pair should be monitored together.",
        symbols=("005930", "000660"),
    )

    snapshot = JueWikiCompilerV1().compile(
        scope="kis", artifacts=(artifact,), base_snapshot=None
    )

    assert snapshot.pages[0].page_type == "symbol"
    assert snapshot.pages[0].title == "000660"


def test_base_snapshot_scope_mismatch_is_rejected() -> None:
    artifact = _artifact(
        artifact_id="one",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    compiler = JueWikiCompilerV1()
    base = compiler.compile(scope="kis", artifacts=(artifact,), base_snapshot=None)

    try:
        compiler.compile(
            scope="binance",
            artifacts=(),
            base_snapshot=replace(base, scope="kis"),
        )
    except ValueError as exc:
        assert str(exc) == "base_snapshot_scope_mismatch"
    else:
        raise AssertionError("expected scope mismatch")
