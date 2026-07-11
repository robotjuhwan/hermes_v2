from dataclasses import FrozenInstanceError, replace
from typing import get_args, get_type_hints

import pytest

from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    ClaimStatus,
    ClaimType,
    EvidenceRefV1,
    JueWikiPageV3,
    ReadMode,
    WikiClaimV3,
    WikiContextPacketV1,
    WikiContextRequestV1,
    WikiContractError,
    WikiDecisionGateV1,
    WikiRelationshipV1,
    WikiSnapshotV1,
)


def _evidence(
    *, evidence_id: str = "naver-report:42", content_hash: str = "a" * 64
) -> EvidenceRefV1:
    return EvidenceRefV1(
        evidence_id=evidence_id,
        source_type="naver_report",
        source_id="42",
        content_hash=content_hash,
        observed_at="2026-07-11T00:00:00+00:00",
    )


def _claim(
    *,
    claim_id: str = "claim:kis:005930:thesis",
    status: ClaimStatus = "verified",
    evidence: tuple[EvidenceRefV1, ...] | None = None,
) -> WikiClaimV3:
    return WikiClaimV3(
        claim_id=claim_id,
        claim_type="interpretation",
        text="Earnings revisions support the thesis.",
        status=status,
        scope="kis",
        evidence=(_evidence(),) if evidence is None else evidence,
    )


def _page(*, page_id: str = "page:kis:005930") -> JueWikiPageV3:
    return JueWikiPageV3(
        page_id=page_id,
        page_type="symbol_thesis",
        scope="kis",
        title="Samsung Electronics thesis",
        summary="Earnings revisions remain constructive.",
        claims=(_claim(),),
        relationships=(
            WikiRelationshipV1(
                source_claim_id="claim:kis:005930:thesis",
                relationship_type="supports",
                target_id="005930",
            ),
        ),
        status="verified",
        schema_version="jue_wiki_v3",
        compiler_version="compiler_v1",
    )


def _artifact() -> CandidateArtifactV1:
    return CandidateArtifactV1(
        artifact_id="artifact:kis:42",
        scope="kis",
        extractor_version="extractor_v1",
        input_hash="b" * 64,
        source_refs=(_evidence(),),
        claims=(_claim(),),
        created_at="2026-07-11T00:05:00+00:00",
    )


def _relationship() -> WikiRelationshipV1:
    return WikiRelationshipV1(
        source_claim_id="claim:kis:005930:thesis",
        relationship_type="depends_on",
        target_id="claim:kis:market:semiconductors",
    )


def test_candidate_artifact_relationships_default_to_empty() -> None:
    assert _artifact().relationships == ()


def test_candidate_artifact_preserves_existing_positional_field_order() -> None:
    artifact = CandidateArtifactV1(
        "artifact:kis:42",
        "kis",
        "extractor_v1",
        "b" * 64,
        (_evidence(),),
        (_claim(),),
        "2026-07-11T00:05:00+00:00",
        "gpt-5",
        "c" * 64,
        "d" * 64,
    )

    assert artifact.model == "gpt-5"
    assert artifact.prompt_hash == "c" * 64
    assert artifact.config_hash == "d" * 64
    assert artifact.relationships == ()


def test_candidate_artifact_accepts_explicit_relationships() -> None:
    relationship = _relationship()
    artifact = replace(_artifact(), relationships=(relationship,))

    assert artifact.relationships == (relationship,)


def test_candidate_artifact_relationships_are_immutable() -> None:
    artifact = replace(_artifact(), relationships=(_relationship(),))

    with pytest.raises(FrozenInstanceError):
        artifact.relationships = ()  # type: ignore[misc]


def test_candidate_artifact_to_dict_serializes_nested_relationships() -> None:
    relationship = _relationship()
    artifact = replace(_artifact(), relationships=(relationship,))

    assert artifact.to_dict()["relationships"] == (relationship.to_dict(),)


def test_verified_claim_requires_hashed_evidence() -> None:
    with pytest.raises(WikiContractError, match="verified_claim_requires_evidence"):
        WikiClaimV3(
            claim_id="claim:kis:005930:thesis",
            claim_type="interpretation",
            text="Earnings revisions support the thesis.",
            status="verified",
            scope="kis",
            evidence=(),
        )


def test_verified_claim_accepts_resolvable_hashed_evidence() -> None:
    evidence = _evidence()
    claim = _claim(evidence=(evidence,))

    assert claim.evidence == (evidence,)
    assert replace(claim, status="stale").status == "stale"


def test_verified_claim_rejects_any_evidence_without_a_hash() -> None:
    with pytest.raises(
        WikiContractError, match="verified_claim_requires_hashed_evidence"
    ):
        _claim(evidence=(_evidence(), _evidence(evidence_id="report:43", content_hash="")))


def test_verified_claim_rejects_whitespace_only_evidence_hash() -> None:
    with pytest.raises(
        WikiContractError, match="verified_claim_requires_hashed_evidence"
    ):
        _claim(evidence=(_evidence(content_hash=" \t "),))


@pytest.mark.parametrize(
    ("factory", "error_code"),
    [
        (lambda: _evidence(evidence_id=""), "evidence_id_must_be_non_empty"),
        (
            lambda: CandidateArtifactV1(
                artifact_id="",
                scope="kis",
                extractor_version="extractor_v1",
                input_hash="b" * 64,
                source_refs=(_evidence(),),
                claims=(_claim(),),
                created_at="2026-07-11T00:05:00+00:00",
            ),
            "artifact_id_must_be_non_empty",
        ),
        (lambda: _claim(claim_id=""), "claim_id_must_be_non_empty"),
        (lambda: _page(page_id=""), "page_id_must_be_non_empty"),
        (
            lambda: WikiSnapshotV1(
                snapshot_id="",
                scope="kis",
                candidate_artifact_ids=("artifact:kis:42",),
                pages=(_page(),),
                schema_version="jue_wiki_v3",
                compiler_version="compiler_v1",
                created_at="2026-07-11T00:10:00+00:00",
            ),
            "snapshot_id_must_be_non_empty",
        ),
    ],
)
def test_contract_identifiers_must_be_non_empty(factory: object, error_code: str) -> None:
    with pytest.raises(WikiContractError, match=error_code):
        factory()  # type: ignore[operator]


def test_claim_literal_values_are_frozen() -> None:
    assert get_args(ClaimType) == ("fact", "interpretation", "hypothesis", "policy")
    assert get_args(ClaimStatus) == (
        "draft",
        "verified",
        "stale",
        "conflicted",
        "superseded",
        "rejected",
    )


def test_read_mode_and_relationship_literal_values_are_frozen() -> None:
    assert get_args(ReadMode) == ("shadow", "prefer", "required")
    assert get_type_hints(WikiContextPacketV1)["read_mode"] == ReadMode
    assert get_type_hints(WikiDecisionGateV1)["read_mode"] == ReadMode
    relationship_type = get_type_hints(WikiRelationshipV1)["relationship_type"]
    assert get_args(relationship_type) == (
        "supports",
        "contradicts",
        "supersedes",
        "depends_on",
        "applies_to",
    )


@pytest.mark.parametrize(
    ("raw_confidence", "expected"),
    [(-0.1, 0.0), (0.35, 0.35), (1.1, 1.0), ("0.75", 0.75)],
)
def test_claim_confidence_is_numeric_and_clamped(
    raw_confidence: float | str, expected: float
) -> None:
    claim = replace(_claim(), confidence=raw_confidence)  # type: ignore[arg-type]

    assert claim.confidence == expected
    assert isinstance(claim.confidence, float)


def test_claim_symbols_are_trimmed_uppercased_and_empty_values_removed() -> None:
    claim = replace(_claim(), symbols=(" 005930 ", "", " btcusdt", "   "))

    assert claim.symbols == ("005930", "BTCUSDT")


def test_context_request_normalizes_symbols_and_uses_default_max_chars() -> None:
    request = WikiContextRequestV1(
        target_scope="binance",
        symbols=(" btcusdt ", "", " ethusdt"),
    )

    assert request.symbols == ("BTCUSDT", "ETHUSDT")
    assert request.max_chars == 24_000
    assert request.to_dict() == {
        "target_scope": "binance",
        "symbols": ("BTCUSDT", "ETHUSDT"),
        "page_types": (),
        "lanes": (),
        "regimes": (),
        "block_ids": (),
        "horizons": (),
        "max_chars": 24_000,
    }


@pytest.mark.parametrize("max_chars", [0, 1])
def test_context_request_requires_space_for_an_empty_array(max_chars: int) -> None:
    with pytest.raises(
        WikiContractError, match="wiki_context_max_chars_must_encode_empty_array"
    ):
        WikiContextRequestV1(target_scope="kis", symbols=("005930",), max_chars=max_chars)


def test_context_request_accepts_two_max_chars() -> None:
    request = WikiContextRequestV1(
        target_scope="kis", symbols=("005930",), max_chars=2
    )

    assert request.max_chars == 2


def test_to_dict_recursively_serializes_the_full_snapshot_contract() -> None:
    evidence = _evidence()
    claim = _claim(evidence=(evidence,))
    page = _page()
    snapshot = WikiSnapshotV1(
        snapshot_id="snapshot:kis:20260711",
        scope="kis",
        candidate_artifact_ids=("artifact:kis:42",),
        pages=(page,),
        schema_version="jue_wiki_v3",
        compiler_version="compiler_v1",
        created_at="2026-07-11T00:10:00+00:00",
    )
    artifact = CandidateArtifactV1(
        artifact_id="artifact:kis:42",
        scope="kis",
        extractor_version="extractor_v1",
        input_hash="b" * 64,
        source_refs=(evidence,),
        claims=(claim,),
        created_at="2026-07-11T00:05:00+00:00",
        model="gpt-5",
        prompt_hash="c" * 64,
        config_hash="d" * 64,
    )

    assert evidence.to_dict() == {
        "evidence_id": "naver-report:42",
        "source_type": "naver_report",
        "source_id": "42",
        "content_hash": "a" * 64,
        "observed_at": "2026-07-11T00:00:00+00:00",
        "source_path": "",
        "hash_origin": "source",
    }
    artifact_dict = artifact.to_dict()
    assert artifact_dict["input_hash"] == "b" * 64
    assert artifact_dict["prompt_hash"] == "c" * 64
    assert artifact_dict["config_hash"] == "d" * 64
    assert artifact_dict["source_refs"] == (evidence.to_dict(),)
    assert artifact_dict["claims"] == (claim.to_dict(),)
    assert snapshot.to_dict()["pages"] == (page.to_dict(),)
    assert snapshot.to_dict()["candidate_artifact_ids"] == ("artifact:kis:42",)
    assert page.relationships[0].to_dict() == {
        "source_claim_id": "claim:kis:005930:thesis",
        "relationship_type": "supports",
        "target_id": "005930",
    }


def test_context_outputs_preserve_read_modes_statuses_and_version_defaults() -> None:
    packet = WikiContextPacketV1(
        status="ready",
        read_mode="required",
        snapshot_id="snapshot:kis:20260711",
        selected_pages=(_page(),),
        rejected_page_ids=("page:kis:rejected",),
        coverage_status="complete",
        quality_warnings=(),
        repair_required=False,
        char_count=1_024,
    )
    gate = WikiDecisionGateV1(
        allow_new_risk=True,
        allow_exit_actions=True,
        reason="wiki_context_eligible",
        read_mode="prefer",
        snapshot_id="snapshot:kis:20260711",
    )

    assert packet.to_dict()["selected_pages"] == (_page().to_dict(),)
    assert packet.version == "wiki_context_packet_v1"
    assert packet.required_eligible is False
    assert gate.to_dict() == {
        "allow_new_risk": True,
        "allow_exit_actions": True,
        "reason": "wiki_context_eligible",
        "read_mode": "prefer",
        "snapshot_id": "snapshot:kis:20260711",
        "version": "wiki_decision_gate_v1",
    }


def test_contracts_are_immutable() -> None:
    evidence = _evidence()

    with pytest.raises(FrozenInstanceError):
        evidence.evidence_id = "changed"  # type: ignore[misc]
