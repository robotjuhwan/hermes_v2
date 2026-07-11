from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradecraft.services.jue_wiki_context import (
    JueWikiContextService,
    evaluate_wiki_promotion_gate,
    evaluate_wiki_decision_gate,
    strip_direct_raw_rag_context,
)
from tradecraft.services.jue_wiki_contract import (
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiContextPacketV1,
    WikiContextRequestV1,
    WikiContractError,
    WikiRelationshipV1,
    WikiSnapshotV1,
)
from tradecraft.services.jue_wiki_repository import JueWikiRepository
from tradecraft.services.jue_wiki_shadow import (
    JueWikiShadowStore,
    WikiCompletionSigner,
)


def _evidence(evidence_id: str = "naver-report:42") -> EvidenceRefV1:
    return EvidenceRefV1(
        evidence_id=evidence_id,
        source_type="naver_report",
        source_id=evidence_id.rsplit(":", 1)[-1],
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )


def _claim(
    *,
    claim_id: str = "claim:kis:005930:direction",
    symbol: str = "005930",
    symbols: tuple[str, ...] | None = None,
    status: str = "verified",
    claim_type: str = "interpretation",
    evidence: EvidenceRefV1 | None = None,
    regimes: tuple[str, ...] = (),
    strategies: tuple[str, ...] = (),
    valid_from: str = "",
    valid_to: str = "",
    confidence: float = 0.8,
) -> WikiClaimV3:
    return WikiClaimV3(
        claim_id=claim_id,
        claim_type=claim_type,  # type: ignore[arg-type]
        text=f"{symbol} has current supported direction.",
        status=status,  # type: ignore[arg-type]
        scope="kis",
        evidence=(evidence or _evidence(),),
        symbols=symbols if symbols is not None else (symbol,),
        regimes=regimes,
        strategies=strategies,
        valid_from=valid_from,
        valid_to=valid_to,
        confidence=confidence,
    )


def _page(
    *,
    page_id: str = "kis.symbol.005930",
    page_type: str = "symbol",
    claims: tuple[WikiClaimV3, ...] | None = None,
    relationships: tuple[WikiRelationshipV1, ...] = (),
    summary: str = "Current supported research.",
) -> JueWikiPageV3:
    page_claims = (_claim(),) if claims is None else claims
    return JueWikiPageV3(
        page_id=page_id,
        page_type=page_type,
        scope="kis",
        title=page_id,
        summary=summary,
        claims=page_claims,
        relationships=relationships,
        status=page_claims[0].status if page_claims else "draft",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )


def _snapshot(
    pages: tuple[JueWikiPageV3, ...],
    *,
    snapshot_id: str = "snapshot:kis:1",
    created_at: str = "2026-07-11T00:00:00+00:00",
) -> WikiSnapshotV1:
    return WikiSnapshotV1(
        snapshot_id=snapshot_id,
        scope="kis",
        candidate_artifact_ids=(),
        pages=tuple(sorted(pages, key=lambda page: page.page_id)),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at=created_at,
    )


def _repository(
    tmp_path: Path,
    snapshot: WikiSnapshotV1,
    evidence: tuple[EvidenceRefV1, ...],
) -> JueWikiRepository:
    repository = JueWikiRepository(tmp_path / "wiki.db")
    repository.initialize()
    for ref in evidence:
        repository.register_evidence(ref)
    repository.publish_snapshot(snapshot)
    return repository


class _ReadOnlyRepository:
    def __init__(
        self,
        snapshot: WikiSnapshotV1 | None,
        evidence_refs: dict[str, EvidenceRefV1],
    ) -> None:
        self.snapshot = snapshot
        self.refs = evidence_refs
        self.calls: list[str] = []

    def current_snapshot(self, scope: str) -> WikiSnapshotV1 | None:
        self.calls.append(f"current_snapshot:{scope}")
        return self.snapshot

    def evidence_refs(self) -> dict[str, EvidenceRefV1]:
        self.calls.append("evidence_refs")
        return dict(self.refs)

    def initialize(self) -> None:
        raise AssertionError("context selection must not initialize")

    def publish_snapshot(self, _snapshot: WikiSnapshotV1) -> None:
        raise AssertionError("context selection must not publish")


class _EligibilityReader:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def eligibility(self, venue: str) -> dict[str, object]:
        self.calls.append(venue)
        return {
            "version": "wiki_shadow_eligibility_v1",
            "blockers": [],
            **self.payload,
        }


def _missing_packet(read_mode: str) -> WikiContextPacketV1:
    return WikiContextPacketV1(
        status="missing",
        read_mode=read_mode,  # type: ignore[arg-type]
        snapshot_id="",
        selected_pages=(),
        rejected_page_ids=(),
        coverage_status="insufficient",
        quality_warnings=("wiki_snapshot_missing",),
        repair_required=True,
        char_count=0,
    )


def _serialized_pages(pages: tuple[JueWikiPageV3, ...]) -> str:
    return json.dumps(
        [page.to_dict() for page in pages],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _single_page_budget_packet(
    pages: tuple[JueWikiPageV3, ...],
    request: WikiContextRequestV1,
) -> WikiContextPacketV1:
    evidence_refs = {
        evidence.evidence_id: evidence
        for page in pages
        for claim in page.claims
        for evidence in claim.evidence
    }
    budget = max(len(_serialized_pages((page,))) for page in pages)
    repository = _ReadOnlyRepository(_snapshot(pages), evidence_refs)
    return JueWikiContextService(repository).context_packet(
        replace(request, max_chars=budget),
        "shadow",
    )


@pytest.mark.parametrize("mode", ["shadow", "prefer"])
def test_non_required_mode_does_not_replace_existing_safety_behavior(
    mode: str,
) -> None:
    gate = evaluate_wiki_decision_gate(_missing_packet(read_mode=mode))

    assert gate.allow_new_risk is True
    assert gate.allow_exit_actions is True
    assert gate.reason == "wiki_context_advisory"


def test_required_mode_blocks_new_risk_when_coverage_is_missing() -> None:
    gate = evaluate_wiki_decision_gate(_missing_packet(read_mode="required"))

    assert gate.allow_new_risk is False
    assert gate.allow_exit_actions is True
    assert gate.reason == "wiki_required_coverage_missing"


def test_required_mode_blocks_new_risk_before_shadow_eligibility() -> None:
    packet = WikiContextPacketV1(
        status="ok",
        read_mode="required",
        snapshot_id="snapshot:kis:1",
        selected_pages=(),
        rejected_page_ids=(),
        coverage_status="sufficient",
        quality_warnings=(),
        repair_required=False,
        char_count=0,
        required_eligible=False,
    )

    gate = evaluate_wiki_decision_gate(packet)

    assert gate.allow_new_risk is False
    assert gate.allow_exit_actions is True
    assert gate.reason == "wiki_required_mode_ineligible"


def test_context_uses_only_fresh_venue_matched_stored_eligibility() -> None:
    evidence = _evidence()
    repository = _ReadOnlyRepository(_snapshot((_page(),)), {evidence.evidence_id: evidence})
    request = WikiContextRequestV1(target_scope="kis", symbols=("005930",))
    eligible = _EligibilityReader(
        {
            "venue": "kis",
            "required_eligible": True,
            "complete_sample_count": 500,
            "evaluated_at": "2026-07-11T00:00:00+00:00",
            "evaluated_through": "2026-07-10T23:59:00+00:00",
        }
    )
    service = JueWikiContextService(
        repository,
        eligibility_reader=eligible,
        health_reader=lambda: {
            "status": "ok",
            "v3": {
                "active_read_mode": "required",
                "by_scope": {
                    "kis": {
                        "snapshot_id": "snapshot:kis:1",
                        "snapshot_created_at": "2026-07-11T00:00:00+00:00",
                        "last_ingest_status": "ok",
                        "last_compile_status": "ok",
                        "last_lint_status": "ok",
                        "last_publish_status": "ok",
                        "last_projection_status": "ok",
                        "index_rebuild": {"status": "ok"},
                        "stale_count": 0,
                        "conflicted_count": 0,
                        "orphan_page_count": 0,
                        "repair_backlog_count": 0,
                    }
                },
                "mode_eligibility": {
                    "kis": {
                        "required_eligible": True,
                        "complete_sample_count": 500,
                        "blockers": [],
                    }
                },
            },
        },
        now=lambda: datetime(2026, 7, 11, 0, 5, tzinfo=timezone.utc),
    )

    packet = service.context_packet(request, "required")

    assert packet.required_eligible is True
    assert eligible.calls == ["kis"]

    for change in (
        {"venue": "binance"},
        {"evaluated_at": "2026-07-10T00:00:00+00:00"},
        {"evaluated_at": ""},
        {"evaluated_at": "not-a-timestamp"},
        {"evaluated_at": "2026-07-11T00:05:01+00:00"},
        {"evaluated_at": "2026-07-11T00:05:00"},
        {"evaluated_through": "2026-07-11T00:00:01+00:00"},
        {"complete_sample_count": True},
        {"complete_sample_count": 499},
    ):
        reader = _EligibilityReader({**eligible.payload, **change})
        rejected = JueWikiContextService(
            repository,
            eligibility_reader=reader,
            health_reader=service.health_reader,
            now=lambda: datetime(2026, 7, 11, 0, 5, tzinfo=timezone.utc),
        ).context_packet(request, "required")
        assert rejected.required_eligible is False


@pytest.mark.parametrize(
    ("health_change", "expected_warning"),
    [
        ({"last_ingest_status": "error"}, "wiki_health_ingest_error"),
        ({"last_compile_status": "error"}, "wiki_health_compile_error"),
        ({"last_lint_status": "error"}, "wiki_health_lint_error"),
        ({"last_projection_status": "error"}, "wiki_health_projection_error"),
        ({"last_publish_status": "error"}, "wiki_health_publish_error"),
        ({"snapshot_id": "snapshot:kis:other"}, "wiki_health_snapshot_mismatch"),
        (
            {"snapshot_created_at": "2026-07-10T22:00:00+00:00"},
            "wiki_health_snapshot_stale",
        ),
        ({"index_rebuild": {"status": "rebuilding"}}, "wiki_health_index_rebuilding"),
        ({"stale_count": 1}, "wiki_health_stale_knowledge"),
        ({"conflicted_count": 1}, "wiki_health_conflicted_knowledge"),
        ({"orphan_page_count": 1}, "wiki_health_orphan_pages"),
        ({"repair_backlog_count": 1}, "wiki_health_repair_backlog"),
        ({"stale_count": None}, "wiki_health_stale_knowledge_invalid"),
        ({"conflicted_count": -1}, "wiki_health_conflicted_knowledge_invalid"),
        ({"orphan_page_count": True}, "wiki_health_orphan_pages_invalid"),
    ],
)
def test_required_context_fails_closed_on_stored_v3_health_degradation(
    health_change: dict[str, object],
    expected_warning: str,
) -> None:
    evidence = _evidence()
    prior_snapshot = _snapshot(
        (_page(),),
        snapshot_id="snapshot:kis:prior-valid",
    )
    repository = _ReadOnlyRepository(
        prior_snapshot,
        {evidence.evidence_id: evidence},
    )
    now = datetime(2026, 7, 11, 0, 5, tzinfo=timezone.utc)
    eligible = _EligibilityReader(
        {
            "venue": "kis",
            "required_eligible": True,
            "complete_sample_count": 500,
            "evaluated_at": "2026-07-11T00:00:00+00:00",
            "evaluated_through": "2026-07-10T23:59:00+00:00",
        }
    )
    base_v3 = {
        "active_read_mode": "required",
        "by_scope": {
            "kis": {
                "snapshot_id": "snapshot:kis:prior-valid",
                "snapshot_created_at": "2026-07-11T00:00:00+00:00",
                "snapshot_age_sec": 300,
                "last_ingest_status": "ok",
                "last_compile_status": "ok",
                "last_lint_status": "ok",
                "last_publish_status": "ok",
                "last_projection_status": "ok",
                "index_rebuild": {"status": "ok"},
                "stale_count": 0,
                "conflicted_count": 0,
                "orphan_page_count": 0,
                "repair_backlog_count": 0,
            }
        },
        "mode_eligibility": {
            "kis": {
                "required_eligible": True,
                "complete_sample_count": 500,
                "blockers": [],
            }
        },
    }
    service = JueWikiContextService(
        repository,
        eligibility_reader=eligible,
        health_reader=lambda: {
            "status": "ok",
            "v3": {
                **base_v3,
                "by_scope": {
                    "kis": {
                        **base_v3["by_scope"]["kis"],
                        **health_change,
                    }
                },
            },
        },
        now=lambda: now,
    )

    packet = service.context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )
    gate = evaluate_wiki_decision_gate(packet)

    assert packet.snapshot_id == "snapshot:kis:prior-valid"
    assert packet.coverage_status == "sufficient"
    assert packet.required_eligible is False
    assert expected_warning in packet.quality_warnings
    assert gate.allow_new_risk is False
    assert gate.allow_exit_actions is True


def test_required_context_allows_healthy_matching_stored_v3_snapshot() -> None:
    evidence = _evidence()
    snapshot = _snapshot((_page(),), snapshot_id="snapshot:kis:healthy")
    repository = _ReadOnlyRepository(snapshot, {evidence.evidence_id: evidence})
    now = datetime(2026, 7, 11, 0, 5, tzinfo=timezone.utc)
    eligibility = {
        "venue": "kis",
        "required_eligible": True,
        "complete_sample_count": 500,
        "evaluated_at": "2026-07-11T00:00:00+00:00",
        "evaluated_through": "2026-07-10T23:59:00+00:00",
    }
    health = {
        "status": "ok",
        "v3": {
            "active_read_mode": "required",
            "by_scope": {
                "kis": {
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_created_at": "2026-07-11T00:00:00+00:00",
                    "snapshot_age_sec": 300,
                    "last_ingest_status": "ok",
                    "last_compile_status": "ok",
                    "last_lint_status": "ok",
                    "last_publish_status": "ok",
                    "last_projection_status": "warning",
                    "projection_warning_reason": "cleanup_only",
                    "index_rebuild": {"status": "ok"},
                    "stale_count": 0,
                    "conflicted_count": 0,
                    "orphan_page_count": 0,
                    "repair_backlog_count": 0,
                }
            },
            "mode_eligibility": {
                "kis": {
                    "required_eligible": True,
                    "complete_sample_count": 500,
                    "blockers": [],
                }
            },
        },
    }

    packet = JueWikiContextService(
        repository,
        eligibility_reader=_EligibilityReader(eligibility),
        health_reader=lambda: health,
        now=lambda: now,
    ).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    gate = evaluate_wiki_decision_gate(packet)
    assert packet.required_eligible is True
    assert gate.allow_new_risk is True
    assert gate.allow_exit_actions is True


def test_required_context_fails_closed_when_stored_health_is_unavailable() -> None:
    evidence = _evidence()
    repository = _ReadOnlyRepository(
        _snapshot((_page(),), snapshot_id="snapshot:kis:prior-valid"),
        {evidence.evidence_id: evidence},
    )
    eligible = _EligibilityReader(
        {
            "venue": "kis",
            "required_eligible": True,
            "complete_sample_count": 500,
            "evaluated_at": "2026-07-11T00:00:00+00:00",
            "evaluated_through": "2026-07-10T23:59:00+00:00",
        }
    )

    def unavailable_health() -> dict[str, object]:
        raise sqlite3.OperationalError("wiki health database unavailable")

    packet = JueWikiContextService(
        repository,
        eligibility_reader=eligible,
        health_reader=unavailable_health,
        now=lambda: datetime(2026, 7, 11, 0, 5, tzinfo=timezone.utc),
    ).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    gate = evaluate_wiki_decision_gate(packet)
    assert packet.required_eligible is False
    assert "wiki_health_unavailable" in packet.quality_warnings
    assert gate.allow_new_risk is False
    assert gate.allow_exit_actions is True


def test_context_reads_eligibility_from_explicit_shadow_db(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    repository = _repository(tmp_path, _snapshot((_page(),)), (evidence,))
    shadow_db_path = tmp_path / "shadow" / "jue_wiki_shadow.db"
    store = JueWikiShadowStore(
        shadow_db_path,
        completion_verifier=WikiCompletionSigner(tmp_path / "provenance.key"),
    )
    store.initialize()
    with pytest.raises(Exception):
        with store._connect() as conn:
            conn.execute(
                "UPDATE wiki_shadow_eligibility_v1 "
                "SET complete_sample_count = 500 WHERE venue = 'kis'"
            )

    packet = JueWikiContextService(
        repository,
        eligibility_db_path=shadow_db_path,
    ).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    assert packet.required_eligible is False


def test_promotion_fails_closed_without_configured_threshold() -> None:
    result = evaluate_wiki_promotion_gate(
        venue="kis",
        playbook_type="swing",
        promotion_thresholds={},
        fill_proven_closed_sample_count=100,
        cost_attribution_complete=True,
        policy_review_approved=True,
    )

    assert result["automatic_promotion_allowed"] is False
    assert result["reason"] == "promotion_threshold_unconfigured"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("fill_proven_closed_sample_count", True, "fill_proven_closed_sample_invalid"),
        ("fill_proven_closed_sample_count", -1, "fill_proven_closed_sample_invalid"),
        ("cost_attribution_complete", 1, "cost_attribution_invalid"),
        ("policy_review_approved", "yes", "policy_review_invalid"),
    ],
)
def test_promotion_rejects_coerced_or_malformed_gate_values(
    field: str,
    value: object,
    reason: str,
) -> None:
    kwargs: dict[str, object] = {
        "venue": "kis",
        "playbook_type": "swing",
        "promotion_thresholds": {"kis": {"swing": 1}},
        "fill_proven_closed_sample_count": 1,
        "cost_attribution_complete": True,
        "policy_review_approved": True,
    }
    kwargs[field] = value

    result = evaluate_wiki_promotion_gate(**kwargs)  # type: ignore[arg-type]

    assert result["automatic_promotion_allowed"] is False
    assert result["reason"] == reason


@pytest.mark.parametrize(
    ("sample_count", "cost_complete", "reviewed", "reason"),
    [
        (29, True, True, "fill_proven_closed_sample_insufficient"),
        (30, False, True, "cost_attribution_incomplete"),
        (30, True, False, "policy_review_required"),
        (30, True, True, "promotion_acceptance_gates_passed"),
    ],
)
def test_promotion_requires_fill_cost_and_policy_gates(
    sample_count: int,
    cost_complete: bool,
    reviewed: bool,
    reason: str,
) -> None:
    result = evaluate_wiki_promotion_gate(
        venue="binance",
        playbook_type="breakout",
        promotion_thresholds={"binance": {"breakout": 30}},
        fill_proven_closed_sample_count=sample_count,
        cost_attribution_complete=cost_complete,
        policy_review_approved=reviewed,
    )

    assert result["reason"] == reason
    assert result["automatic_promotion_allowed"] is (reason.endswith("passed"))


def test_required_mode_allows_new_risk_only_after_both_proofs() -> None:
    packet = WikiContextPacketV1(
        status="ok",
        read_mode="required",
        snapshot_id="snapshot:kis:1",
        selected_pages=(),
        rejected_page_ids=(),
        coverage_status="sufficient",
        quality_warnings=(),
        repair_required=False,
        char_count=0,
        required_eligible=True,
    )

    gate = evaluate_wiki_decision_gate(packet)

    assert gate.allow_new_risk is True
    assert gate.allow_exit_actions is True
    assert gate.reason == "wiki_context_eligible"


def test_stale_claim_is_warning_not_positive_entry_support(tmp_path: Path) -> None:
    evidence = _evidence()
    stale_claim = _claim(status="stale", evidence=evidence)
    stale_page = _page(claims=(stale_claim,), summary="Stale research only.")
    repository = _repository(
        tmp_path,
        _snapshot((stale_page,), snapshot_id="snapshot:kis:stale"),
        (evidence,),
    )

    packet = JueWikiContextService(repository).context_packet(
        request=WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        read_mode="required",
    )

    assert packet.coverage_status == "insufficient"
    assert packet.selected_pages == (stale_page,)
    assert "stale_only_support" in packet.quality_warnings


@pytest.mark.parametrize(
    ("snapshot_created_at", "valid_from"),
    [
        ("2026-07-11T09:00:00+09:00", "2026-07-11T00:00:00Z"),
        ("2026-07-11T00:00:00", "2026-07-11T00:00:00+00:00"),
    ],
)
def test_claim_valid_from_is_inclusive_at_normalized_snapshot_instant(
    snapshot_created_at: str,
    valid_from: str,
) -> None:
    evidence = _evidence()
    page = _page(claims=(_claim(evidence=evidence, valid_from=valid_from),))
    repository = _ReadOnlyRepository(
        _snapshot((page,), created_at=snapshot_created_at),
        {evidence.evidence_id: evidence},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    assert packet.coverage_status == "sufficient"


def test_claim_valid_to_is_exclusive_at_normalized_snapshot_instant() -> None:
    evidence = _evidence()
    claim = _claim(
        evidence=evidence,
        valid_from="2026-07-10T23:59:59Z",
        valid_to="2026-07-11T09:00:00+09:00",
    )
    page = _page(claims=(claim,))
    repository = _ReadOnlyRepository(
        _snapshot((page,), created_at="2026-07-11T00:00:00Z"),
        {evidence.evidence_id: evidence},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    assert packet.coverage_status == "insufficient"
    assert f"claim_expired:{claim.claim_id}" in packet.quality_warnings


@pytest.mark.parametrize(
    ("valid_from", "valid_to", "warning"),
    [
        ("invalid", "", "claim_valid_from_invalid"),
        ("", "invalid", "claim_valid_to_invalid"),
        ("2026-07-11T00:00:01Z", "", "claim_not_yet_valid"),
        ("", "2026-07-10T23:59:59Z", "claim_expired"),
    ],
)
def test_invalid_or_out_of_window_claim_cannot_support_coverage(
    valid_from: str,
    valid_to: str,
    warning: str,
) -> None:
    evidence = _evidence()
    claim = _claim(
        evidence=evidence,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    page = _page(claims=(claim,))
    repository = _ReadOnlyRepository(
        _snapshot((page,), created_at="2026-07-11T00:00:00Z"),
        {evidence.evidence_id: evidence},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "shadow",
    )

    assert packet.coverage_status == "insufficient"
    assert f"{warning}:{claim.claim_id}" in packet.quality_warnings


@pytest.mark.parametrize("created_at", ["", "not-a-timestamp", "2026-07-11"])
def test_missing_or_invalid_snapshot_time_disables_positive_support(
    created_at: str,
) -> None:
    evidence = _evidence()
    page = _page(claims=(_claim(evidence=evidence),))
    repository = _ReadOnlyRepository(
        _snapshot((page,), created_at=created_at),
        {evidence.evidence_id: evidence},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "shadow",
    )

    assert packet.coverage_status == "insufficient"
    assert "snapshot_created_at_invalid" in packet.quality_warnings


def test_evidence_resolution_canonicalizes_hash_case_and_observed_instant() -> None:
    registry = replace(
        _evidence(),
        content_hash="A" * 64,
        observed_at="2026-07-11T09:00:00+09:00",
        source_path="reports/42.pdf",
        hash_origin="source",
    )
    claim_evidence = replace(
        registry,
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00Z",
    )
    page = _page(claims=(_claim(evidence=claim_evidence),))
    repository = _ReadOnlyRepository(
        _snapshot((page,)),
        {registry.evidence_id: registry},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "shadow",
    )

    assert packet.coverage_status == "sufficient"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_type", "different_source"),
        ("source_id", "different-id"),
        ("content_hash", "b" * 64),
        ("observed_at", "2026-07-11T00:00:01Z"),
        ("source_path", "different/path.pdf"),
        ("hash_origin", "normalized_payload"),
    ],
)
def test_verified_evidence_payload_mismatch_rejects_affected_page(
    field: str,
    value: str,
) -> None:
    registry = replace(_evidence(), source_path="reports/42.pdf")
    claim_evidence = replace(registry, **{field: value})
    page = _page(claims=(_claim(evidence=claim_evidence),))
    repository = _ReadOnlyRepository(
        _snapshot((page,)),
        {registry.evidence_id: registry},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    assert packet.selected_pages == ()
    assert packet.rejected_page_ids == (page.page_id,)
    assert f"evidence_mismatch:{registry.evidence_id}:{field}" in (
        packet.quality_warnings
    )


def test_same_evidence_id_with_different_claim_payload_rejects_all_owners() -> None:
    registry = _evidence("evidence:shared")
    first = _page(
        page_id="kis.symbol.005930.first",
        claims=(
            _claim(
                claim_id="claim:first",
                evidence=registry,
            ),
        ),
    )
    conflicting_evidence = replace(registry, content_hash="b" * 64)
    second = _page(
        page_id="kis.symbol.005930.second",
        claims=(
            _claim(
                claim_id="claim:second",
                evidence=conflicting_evidence,
            ),
        ),
    )
    repository = _ReadOnlyRepository(
        _snapshot((first, second)),
        {registry.evidence_id: registry},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    assert packet.selected_pages == ()
    assert packet.rejected_page_ids == tuple(sorted((first.page_id, second.page_id)))
    assert f"evidence_payload_collision:{registry.evidence_id}" in (
        packet.quality_warnings
    )


def test_evidence_payload_collision_rejects_non_verified_owner_too() -> None:
    registry = _evidence("evidence:shared-status")
    verified_page = _page(
        page_id="kis.symbol.005930.verified-owner",
        claims=(
            _claim(
                claim_id="claim:verified-owner",
                evidence=registry,
            ),
        ),
    )
    draft_page = _page(
        page_id="kis.symbol.005930.draft-owner",
        claims=(
            _claim(
                claim_id="claim:draft-owner",
                evidence=replace(registry, source_path="different.pdf"),
                status="draft",
            ),
        ),
    )
    repository = _ReadOnlyRepository(
        _snapshot((verified_page, draft_page)),
        {registry.evidence_id: registry},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "shadow",
    )

    assert packet.selected_pages == ()
    assert packet.rejected_page_ids == tuple(
        sorted((verified_page.page_id, draft_page.page_id))
    )


def test_registry_key_cannot_mask_evidence_id_payload_mismatch() -> None:
    registry = _evidence("evidence:canonical")
    claim_evidence = replace(registry, evidence_id="evidence:claim")
    page = _page(claims=(_claim(evidence=claim_evidence),))
    repository = _ReadOnlyRepository(
        _snapshot((page,)),
        {claim_evidence.evidence_id: registry},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "shadow",
    )

    assert packet.selected_pages == ()
    assert f"evidence_mismatch:{claim_evidence.evidence_id}:evidence_id" in (
        packet.quality_warnings
    )


def test_cross_page_supersedes_error_rejects_relationship_and_claim_owners() -> None:
    source_evidence = _evidence("evidence:source")
    target_evidence = _evidence("evidence:target")
    source_claim = _claim(
        claim_id="claim:source",
        evidence=source_evidence,
    )
    target_claim = _claim(
        claim_id="claim:target",
        evidence=target_evidence,
    )
    source_page = _page(
        page_id="kis.symbol.005930.source",
        claims=(source_claim,),
        relationships=(
            WikiRelationshipV1(
                source_claim_id=source_claim.claim_id,
                relationship_type="supersedes",
                target_id=target_claim.claim_id,
            ),
        ),
    )
    target_page = _page(
        page_id="kis.symbol.005930.target",
        claims=(target_claim,),
    )
    repository = _ReadOnlyRepository(
        _snapshot((source_page, target_page)),
        {
            source_evidence.evidence_id: source_evidence,
            target_evidence.evidence_id: target_evidence,
        },
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "shadow",
    )

    assert packet.selected_pages == ()
    assert packet.rejected_page_ids == tuple(
        sorted((source_page.page_id, target_page.page_id))
    )


def test_cross_page_dangling_error_rejects_message_claim_owner() -> None:
    target_evidence = _evidence("evidence:target")
    target_claim = _claim(
        claim_id="claim:target",
        evidence=target_evidence,
    )
    relationship_page = _page(
        page_id="kis.core.relationship",
        page_type="core",
        claims=(),
        relationships=(
            WikiRelationshipV1(
                source_claim_id="claim:missing",
                relationship_type="supports",
                target_id=target_claim.claim_id,
            ),
        ),
    )
    target_page = _page(
        page_id="kis.symbol.005930.target",
        claims=(target_claim,),
    )
    repository = _ReadOnlyRepository(
        _snapshot((relationship_page, target_page)),
        {target_evidence.evidence_id: target_evidence},
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "shadow",
    )

    assert packet.selected_pages == ()
    assert packet.rejected_page_ids == tuple(
        sorted((relationship_page.page_id, target_page.page_id))
    )


def test_coverage_requires_verified_fact_or_interpretation_for_every_symbol(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    page = _page(claims=(_claim(evidence=evidence),))
    repository = _repository(tmp_path, _snapshot((page,)), (evidence,))

    packet = JueWikiContextService(repository).context_packet(
        request=WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930", "000660"),
        ),
        read_mode="shadow",
    )

    assert packet.coverage_status == "insufficient"
    assert "symbol_coverage_missing:000660" in packet.quality_warnings
    assert packet.repair_required is True


def test_unresolved_registered_evidence_rejects_page_and_coverage(
    tmp_path: Path,
) -> None:
    evidence = _evidence("naver-report:unregistered")
    page = _page(claims=(_claim(evidence=evidence),))
    repository = _repository(tmp_path, _snapshot((page,)), ())

    packet = JueWikiContextService(repository).context_packet(
        request=WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        read_mode="required",
    )

    assert packet.selected_pages == ()
    assert packet.rejected_page_ids == (page.page_id,)
    assert packet.coverage_status == "insufficient"
    assert any("lint_error:unresolved_evidence" in row for row in packet.quality_warnings)


def test_ranking_is_deterministic_across_page_regime_lane_and_relationship(
    tmp_path: Path,
) -> None:
    evidence_rows = tuple(_evidence(f"evidence:{index}") for index in range(4))
    generic = _page(
        page_id="kis.symbol.005930.generic",
        claims=(_claim(claim_id="claim:generic", evidence=evidence_rows[0]),),
    )
    relevant = _page(
        page_id="kis.symbol.005930.relevant",
        claims=(
            _claim(
                claim_id="claim:relevant",
                evidence=evidence_rows[1],
                regimes=("risk_on",),
                strategies=("value_cycle", "mid"),
            ),
        ),
        relationships=(
            WikiRelationshipV1(
                source_claim_id="claim:relevant",
                relationship_type="applies_to",
                target_id="block:005930:open",
            ),
        ),
    )
    wrong_type = _page(
        page_id="kis.research.005930",
        page_type="research",
        claims=(
            _claim(
                claim_id="claim:wrong-type",
                evidence=evidence_rows[2],
                regimes=("risk_on",),
                strategies=("value_cycle", "mid"),
                confidence=0.99,
            ),
        ),
    )
    unrelated = _page(
        page_id="kis.symbol.000660",
        claims=(
            _claim(
                claim_id="claim:unrelated",
                symbol="000660",
                evidence=evidence_rows[3],
            ),
        ),
    )
    repository = _repository(
        tmp_path,
        _snapshot((unrelated, wrong_type, generic, relevant)),
        evidence_rows,
    )
    request = WikiContextRequestV1(
        target_scope="kis",
        symbols=("005930",),
        page_types=("symbol",),
        lanes=("value_cycle",),
        regimes=("risk_on",),
        block_ids=("block:005930:open",),
        horizons=("mid",),
    )
    service = JueWikiContextService(repository)

    first = service.context_packet(request=request, read_mode="prefer")
    second = service.context_packet(request=request, read_mode="prefer")

    assert first == second
    assert tuple(page.page_id for page in first.selected_pages[:3]) == (
        relevant.page_id,
        generic.page_id,
        wrong_type.page_id,
    )
    assert first.coverage_status == "sufficient"


def test_ranking_prefers_exact_requested_symbol_set_over_superset() -> None:
    exact = _page(
        page_id="kis.symbol.z-exact",
        claims=(_claim(claim_id="claim:exact", symbols=("005930",)),),
    )
    superset = _page(
        page_id="kis.symbol.a-superset",
        claims=(
            _claim(
                claim_id="claim:superset",
                symbols=("005930", "000660"),
            ),
        ),
    )

    packet = _single_page_budget_packet(
        (superset, exact),
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
    )

    assert packet.selected_pages == (exact,)


def test_ranking_prefers_symbol_overlap_over_no_relation() -> None:
    overlap = _page(
        page_id="kis.symbol.z-overlap",
        claims=(_claim(claim_id="claim:overlap", symbols=("005930",)),),
    )
    unrelated = _page(
        page_id="kis.symbol.a-unrelated",
        claims=(_claim(claim_id="claim:none", symbols=("035420",)),),
    )

    packet = _single_page_budget_packet(
        (unrelated, overlap),
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930", "000660"),
        ),
    )

    assert packet.selected_pages == (overlap,)


def test_ranking_prefers_requested_page_type_before_regime() -> None:
    preferred_type = _page(
        page_id="kis.symbol.z-type",
        page_type="symbol",
        claims=(_claim(claim_id="claim:type"),),
    )
    regime_match = _page(
        page_id="kis.research.a-regime",
        page_type="research",
        claims=(
            _claim(
                claim_id="claim:regime-later",
                regimes=("risk_on",),
            ),
        ),
    )

    packet = _single_page_budget_packet(
        (regime_match, preferred_type),
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            page_types=("symbol",),
            regimes=("risk_on",),
        ),
    )

    assert packet.selected_pages == (preferred_type,)


def test_ranking_regime_uses_only_claim_regimes() -> None:
    regime_match = _page(
        page_id="kis.symbol.z-regime",
        claims=(
            _claim(claim_id="claim:regime", regimes=("risk_on",)),
        ),
    )
    strategy_only = _page(
        page_id="kis.symbol.a-strategy",
        claims=(
            _claim(claim_id="claim:strategy", strategies=("risk_on",)),
        ),
    )

    packet = _single_page_budget_packet(
        (strategy_only, regime_match),
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            regimes=("risk_on",),
        ),
    )

    assert packet.selected_pages == (regime_match,)


def test_ranking_lane_uses_only_exact_claim_strategy_tokens() -> None:
    lane_match = _page(
        page_id="kis.symbol.z-lane",
        claims=(
            _claim(claim_id="claim:lane", strategies=("value_cycle",)),
        ),
    )
    regime_only = _page(
        page_id="kis.symbol.a-regime",
        claims=(
            _claim(claim_id="claim:regime-only", regimes=("value_cycle",)),
        ),
    )

    packet = _single_page_budget_packet(
        (regime_only, lane_match),
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            lanes=("value_cycle",),
        ),
    )

    assert packet.selected_pages == (lane_match,)


def test_ranking_horizon_uses_exact_strategy_horizon_tokens() -> None:
    horizon_match = _page(
        page_id="kis.symbol.z-horizon",
        claims=(
            _claim(claim_id="claim:horizon", strategies=("mid",)),
        ),
    )
    regime_only = _page(
        page_id="kis.symbol.a-regime-mid",
        claims=(
            _claim(claim_id="claim:regime-mid", regimes=("mid",)),
        ),
    )

    packet = _single_page_budget_packet(
        (regime_only, horizon_match),
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            horizons=("mid",),
        ),
    )

    assert packet.selected_pages == (horizon_match,)


def test_ranking_prefers_one_fresh_claim_over_two_older_claims() -> None:
    older_first = replace(
        _evidence("evidence:older-first"),
        observed_at="2026-07-09T00:00:00Z",
    )
    older_second = replace(
        _evidence("evidence:older-second"),
        observed_at="2026-07-09T01:00:00Z",
    )
    fresh_evidence = replace(
        _evidence("evidence:fresh-single"),
        observed_at="2026-07-10T00:00:00Z",
    )
    older_two_claims = _page(
        page_id="kis.symbol.a-older-two",
        claims=(
            _claim(claim_id="claim:older:1", evidence=older_first),
            _claim(claim_id="claim:older:2", evidence=older_second),
        ),
    )
    fresh_one_claim = _page(
        page_id="kis.symbol.z-fresh-one",
        claims=(
            _claim(claim_id="claim:fresh:1", evidence=fresh_evidence),
        ),
    )

    packet = _single_page_budget_packet(
        (older_two_claims, fresh_one_claim),
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
    )

    assert packet.selected_pages == (fresh_one_claim,)


def test_ranking_prefers_fresher_resolved_evidence_deterministically() -> None:
    older_evidence = replace(
        _evidence("evidence:older"),
        observed_at="2026-07-09T00:00:00Z",
    )
    newer_evidence = replace(
        _evidence("evidence:newer"),
        observed_at="2026-07-10T09:00:00+09:00",
    )
    newer = _page(
        page_id="kis.symbol.z-newer",
        claims=(
            _claim(claim_id="claim:newer", evidence=newer_evidence),
        ),
    )
    older = _page(
        page_id="kis.symbol.a-older",
        claims=(
            _claim(claim_id="claim:older", evidence=older_evidence),
        ),
    )

    packet = _single_page_budget_packet(
        (older, newer),
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
    )

    assert packet.selected_pages == (newer,)


def test_ranking_prefers_confidence_after_freshness() -> None:
    high_confidence = _page(
        page_id="kis.symbol.z-confidence",
        claims=(
            _claim(claim_id="claim:high", confidence=0.9),
        ),
    )
    low_confidence = _page(
        page_id="kis.symbol.a-low",
        claims=(
            _claim(claim_id="claim:low", confidence=0.6),
        ),
    )

    packet = _single_page_budget_packet(
        (low_confidence, high_confidence),
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
    )

    assert packet.selected_pages == (high_confidence,)


def test_ranking_prefers_relationship_relevance_after_confidence() -> None:
    relevant = _page(
        page_id="kis.symbol.z-relationship",
        claims=(_claim(claim_id="claim:relationship"),),
        relationships=(
            WikiRelationshipV1(
                source_claim_id="claim:relationship",
                relationship_type="applies_to",
                target_id="block:open",
            ),
        ),
    )
    generic = _page(
        page_id="kis.symbol.a-generic",
        claims=(_claim(claim_id="claim:generic-ranking"),),
    )

    packet = _single_page_budget_packet(
        (generic, relevant),
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            block_ids=("block:open",),
        ),
    )

    assert packet.selected_pages == (relevant,)


def test_budget_rejects_whole_page_without_mutating_claim_shapes(
    tmp_path: Path,
) -> None:
    evidence = _evidence()
    page = _page(
        claims=(_claim(evidence=evidence),),
        summary="large " * 80,
    )
    repository = _repository(tmp_path, _snapshot((page,)), (evidence,))
    full = JueWikiContextService(repository).context_packet(
        request=WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            max_chars=10_000,
        ),
        read_mode="shadow",
    )

    packet = JueWikiContextService(repository).context_packet(
        request=WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            max_chars=full.char_count - 1,
        ),
        read_mode="shadow",
    )

    assert full.selected_pages == (page,)
    assert full.selected_pages[0].claims[0].evidence == (evidence,)
    assert packet.selected_pages == ()
    assert packet.rejected_page_ids == (page.page_id,)
    assert packet.char_count == len("[]")
    assert f"page_budget_exceeded:{page.page_id}" in packet.quality_warnings


def test_char_count_is_exact_canonical_selected_pages_array_length() -> None:
    first_evidence = _evidence("evidence:first")
    second_evidence = _evidence("evidence:second")
    first = _page(
        page_id="kis.symbol.005930.first",
        claims=(_claim(claim_id="claim:first-char", evidence=first_evidence),),
    )
    second = _page(
        page_id="kis.symbol.005930.second",
        claims=(_claim(claim_id="claim:second-char", evidence=second_evidence),),
    )
    repository = _ReadOnlyRepository(
        _snapshot((first, second)),
        {
            first_evidence.evidence_id: first_evidence,
            second_evidence.evidence_id: second_evidence,
        },
    )

    packet = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            max_chars=24_000,
        ),
        "shadow",
    )

    expected = _serialized_pages(packet.selected_pages)
    assert packet.char_count == len(expected)
    assert packet.selected_pages == (first, second)
    assert isinstance(packet.selected_pages[0].claims, tuple)
    assert isinstance(packet.selected_pages[0].claims[0].evidence, tuple)


def test_multi_page_budget_uses_prospective_array_at_exact_boundary() -> None:
    first_evidence = _evidence("evidence:first-boundary")
    second_evidence = _evidence("evidence:second-boundary")
    first = _page(
        page_id="kis.symbol.005930.first-boundary",
        claims=(
            _claim(claim_id="claim:first-boundary", evidence=first_evidence),
        ),
    )
    second = _page(
        page_id="kis.symbol.005930.second-boundary",
        claims=(
            _claim(claim_id="claim:second-boundary", evidence=second_evidence),
        ),
    )
    evidence_refs = {
        first_evidence.evidence_id: first_evidence,
        second_evidence.evidence_id: second_evidence,
    }
    repository = _ReadOnlyRepository(_snapshot((first, second)), evidence_refs)
    full_size = len(_serialized_pages((first, second)))

    exact = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            max_chars=full_size,
        ),
        "shadow",
    )
    below = JueWikiContextService(repository).context_packet(
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            max_chars=full_size - 1,
        ),
        "shadow",
    )

    assert exact.selected_pages == (first, second)
    assert exact.char_count == full_size
    assert below.selected_pages == (first,)
    assert below.char_count == len(_serialized_pages((first,)))
    assert below.rejected_page_ids == (second.page_id,)


def test_context_budget_minimum_matches_canonical_empty_array() -> None:
    with pytest.raises(
        WikiContractError,
        match="wiki_context_max_chars",
    ):
        WikiContextRequestV1(
            target_scope="kis",
            symbols=("005930",),
            max_chars=1,
        )

    evidence = _evidence("evidence:min-budget")
    page = _page(claims=(_claim(evidence=evidence),))
    repository = _ReadOnlyRepository(
        _snapshot((page,)),
        {evidence.evidence_id: evidence},
    )
    request = WikiContextRequestV1(
        target_scope="kis",
        symbols=("005930",),
        max_chars=2,
    )
    packet = JueWikiContextService(repository).context_packet(request, "shadow")

    assert packet.selected_pages == ()
    assert packet.char_count == len("[]") == 2
    assert packet.char_count <= request.max_chars


def test_context_selection_uses_only_repository_read_methods() -> None:
    page = _page()
    snapshot = _snapshot((page,))

    class ReadOnlyRepository:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def current_snapshot(self, scope: str) -> WikiSnapshotV1 | None:
            self.calls.append(f"current_snapshot:{scope}")
            return snapshot

        def evidence_refs(self) -> dict[str, EvidenceRefV1]:
            self.calls.append("evidence_refs")
            evidence = _evidence()
            return {evidence.evidence_id: evidence}

        def initialize(self) -> None:
            raise AssertionError("context selection must not initialize")

        def publish_snapshot(self, _snapshot: WikiSnapshotV1) -> None:
            raise AssertionError("context selection must not publish")

    repository = ReadOnlyRepository()

    packet = JueWikiContextService(repository).context_packet(  # type: ignore[arg-type]
        request=WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        read_mode="shadow",
    )

    assert packet.coverage_status == "sufficient"
    assert repository.calls == ["current_snapshot:kis", "evidence_refs"]


def test_missing_published_snapshot_is_read_only_and_explicit() -> None:
    class EmptyRepository:
        def current_snapshot(self, _scope: str) -> WikiSnapshotV1 | None:
            return None

        def evidence_refs(self) -> dict[str, EvidenceRefV1]:
            raise AssertionError("missing snapshot does not need registry read")

    packet = JueWikiContextService(EmptyRepository()).context_packet(  # type: ignore[arg-type]
        request=WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        read_mode="shadow",
    )

    assert packet.status == "missing"
    assert packet.snapshot_id == ""
    assert packet.coverage_status == "insufficient"
    assert packet.quality_warnings == ("wiki_snapshot_missing",)


def test_required_prompt_strips_only_direct_raw_rag_payloads() -> None:
    original = {
        "live_account": {"cash": 1000},
        "market": {"last": 72_000},
        "order": {"side": "buy"},
        "safety": {"kill_switch": False},
        "jue_wiki": {"snapshot_id": "snapshot:kis:1"},
        "raw_reports": [{"content": "bulk report"}],
        "nested": {
            "keep": {"source_contract": "wiki", "value": 1},
            "remove": {"source_contract": "raw_rag", "value": 2},
        },
    }

    prompt, removed = strip_direct_raw_rag_context(original)

    assert removed == ("nested.remove", "raw_reports")
    assert "raw_reports" not in prompt
    assert prompt["live_account"] == {"cash": 1000}
    assert prompt["market"] == {"last": 72_000}
    assert prompt["order"] == {"side": "buy"}
    assert prompt["safety"] == {"kill_switch": False}
    assert prompt["jue_wiki"] == {"snapshot_id": "snapshot:kis:1"}
    assert prompt["nested"] == {
        "keep": {"source_contract": "wiki", "value": 1},
    }
    assert original["raw_reports"] == [{"content": "bulk report"}]
    assert original["nested"]["remove"] == {
        "source_contract": "raw_rag",
        "value": 2,
    }


def test_raw_rag_stripping_matches_only_exact_top_level_keys() -> None:
    prompt, removed = strip_direct_raw_rag_context(
        {
            "rag": {"bulk": True},
            "raw_reports_summary": {"keep": True},
            "nested": {"raw_reports": {"keep": True}},
        }
    )

    assert removed == ("rag",)
    assert prompt == {
        "raw_reports_summary": {"keep": True},
        "nested": {"raw_reports": {"keep": True}},
    }


def test_raw_rag_stripping_removes_marked_dicts_from_nested_sequences() -> None:
    prompt, removed = strip_direct_raw_rag_context(
        {
            "items": [
                {"source_contract": "raw_rag", "content": "remove"},
                {"source_contract": "wiki", "content": "keep"},
            ],
            "tuple_items": (
                {"source_contract": "raw_rag", "content": "remove"},
                {"source_contract": "wiki", "content": "keep"},
            ),
        }
    )

    assert removed == ("items[0]", "tuple_items[0]")
    assert prompt == {
        "items": [{"source_contract": "wiki", "content": "keep"}],
        "tuple_items": ({"source_contract": "wiki", "content": "keep"},),
    }
