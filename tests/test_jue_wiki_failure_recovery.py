from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradecraft.services.binance_block_trader import apply_binance_wiki_decision_gate
from tradecraft.services.jue_wiki_context import (
    JueWikiContextService,
    evaluate_wiki_decision_gate,
)
from tradecraft.services.jue_wiki_contract import (
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiContextRequestV1,
    WikiSnapshotV1,
)
from tradecraft.services.kis_block_trader import apply_kis_wiki_decision_gate


NOW = datetime(2026, 7, 12, 0, 5, tzinfo=timezone.utc)


def _evidence(venue: str = "kis") -> EvidenceRefV1:
    return EvidenceRefV1(
        evidence_id=f"evidence:{venue}:primary",
        source_type="naver_report" if venue == "kis" else "crypto_research",
        source_id="report-1",
        content_hash="a" * 64,
        observed_at="2026-07-12T00:00:00+00:00",
    )


def _snapshot(venue: str = "kis") -> WikiSnapshotV1:
    evidence = _evidence(venue)
    symbol = "005930" if venue == "kis" else "BTCUSDT"
    claim = WikiClaimV3(
        claim_id=f"claim:{venue}:{symbol}",
        claim_type="fact",
        text="005930 has current verified support.",
        status="verified",
        scope=venue,
        evidence=(evidence,),
        symbols=(symbol,),
        confidence=0.9,
    )
    page = JueWikiPageV3(
        page_id=f"{venue}.symbol.{symbol}",
        page_type="symbol",
        scope=venue,
        title="005930",
        summary="Current verified support.",
        claims=(claim,),
        relationships=(),
        status="verified",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )
    return WikiSnapshotV1(
        snapshot_id=f"snapshot:{venue}:prior-valid",
        scope=venue,
        candidate_artifact_ids=(),
        pages=(page,),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-12T00:00:00+00:00",
    )


class _Repository:
    def __init__(self, venue: str = "kis") -> None:
        self.venue = venue
        self.snapshot = _snapshot(venue)
        evidence = _evidence(venue)
        self._evidence = {evidence.evidence_id: evidence}

    def current_snapshot(self, scope: str) -> WikiSnapshotV1 | None:
        assert scope == self.venue
        return self.snapshot

    def evidence_refs(self) -> dict[str, EvidenceRefV1]:
        return dict(self._evidence)


class _Eligibility:
    def eligibility(self, venue: str) -> dict[str, object]:
        return {
            "version": "wiki_shadow_eligibility_v1",
            "venue": venue,
            "required_eligible": True,
            "complete_sample_count": 500,
            "blockers": [],
            "evaluated_at": "2026-07-12T00:00:00+00:00",
            "evaluated_through": "2026-07-11T23:59:00+00:00",
        }


def _healthy_scope(snapshot_id: str) -> dict[str, object]:
    return {
        "snapshot_id": snapshot_id,
        "snapshot_created_at": "2026-07-12T00:00:00+00:00",
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


def _health(
    scope_change: dict[str, object] | None = None,
    *,
    venue: str = "kis",
) -> dict[str, object]:
    snapshot_id = f"snapshot:{venue}:prior-valid"
    return {
        "status": "ok",
        "v3": {
            "active_read_mode": "required",
            "by_scope": {
                venue: {**_healthy_scope(snapshot_id), **(scope_change or {})}
            },
            "mode_eligibility": {
                venue: {
                    "version": "wiki_shadow_eligibility_v1",
                    "venue": venue,
                    "required_eligible": True,
                    "complete_sample_count": 500,
                    "blockers": [],
                }
            },
        },
    }


def _packet(
    scope_change: dict[str, object] | None = None,
    *,
    venue: str = "kis",
):
    symbol = "005930" if venue == "kis" else "BTCUSDT"
    return JueWikiContextService(
        _Repository(venue),
        eligibility_reader=_Eligibility(),
        health_reader=lambda: _health(scope_change, venue=venue),
        now=lambda: NOW,
    ).context_packet(
        WikiContextRequestV1(target_scope=venue, symbols=(symbol,)),
        "required",
    )


def _assert_gate_preserves_operational_actions(packet) -> None:
    gate = evaluate_wiki_decision_gate(packet)
    actions = {
        "create_blocks": [{"symbol": "005930"}],
        "update_blocks": [
            {"block_id": "increase", "quantity": 2},
            {"block_id": "reduce", "quantity": 0.5},
        ],
        "close_blocks": [{"block_id": "existing-1"}],
        "reconciliation_actions": [{"block_id": "existing-1"}],
        "kill_switch_checks": [{"enabled": False}],
    }
    current = {
        "increase": {"block_id": "increase", "quantity": 1},
        "reduce": {"block_id": "reduce", "quantity": 1},
    }
    for apply_gate in (
        apply_kis_wiki_decision_gate,
        apply_binance_wiki_decision_gate,
    ):
        filtered, _audit = apply_gate(
            actions,
            gate,
            trusted_read_mode="required",
            current_blocks=current,
        )
        assert filtered["create_blocks"] == []
        assert filtered["update_blocks"] == [
            {"block_id": "reduce", "quantity": 0.5}
        ]
        assert filtered["close_blocks"] == [{"block_id": "existing-1"}]
        assert filtered["reconciliation_actions"] == [{"block_id": "existing-1"}]
        assert filtered["kill_switch_checks"] == [{"enabled": False}]
    assert gate.allow_new_risk is False
    assert gate.allow_exit_actions is True


@pytest.mark.parametrize("venue", ["kis", "binance"])
def test_healthy_required_context_control_allows_new_risk(venue: str) -> None:
    packet = _packet(venue=venue)
    gate = evaluate_wiki_decision_gate(packet)

    assert packet.coverage_status == "sufficient"
    assert packet.required_eligible is True
    assert gate.allow_new_risk is True
    assert gate.allow_exit_actions is True


@pytest.mark.parametrize(
    "scope_change",
    [
        {"last_ingest_status": "error"},
        {"last_compile_status": "error"},
        {"last_lint_status": "error"},
        {"last_publish_status": "error"},
        {"last_projection_status": "error"},
        {"index_rebuild": {"status": "missing"}},
        {"snapshot_id": "snapshot:kis:other"},
        {"snapshot_created_at": "2026-07-11T20:00:00+00:00"},
        {"stale_count": 1},
        {"conflicted_count": 1},
        {"orphan_page_count": 1},
        {"repair_backlog_count": 1},
    ],
)
def test_actual_required_context_failure_blocks_both_venue_risk_gates(
    scope_change: dict[str, object],
) -> None:
    packet = _packet(scope_change)

    assert packet.coverage_status == "sufficient"
    assert packet.snapshot_id == "snapshot:kis:prior-valid"
    assert packet.required_eligible is False
    _assert_gate_preserves_operational_actions(packet)


def test_actual_binance_context_compile_failure_uses_binance_scoped_health() -> None:
    packet = _packet({"last_compile_status": "error"}, venue="binance")

    assert packet.snapshot_id == "snapshot:binance:prior-valid"
    assert "wiki_health_compile_error" in packet.quality_warnings
    _assert_gate_preserves_operational_actions(packet)


def test_actual_required_context_db_status_outage_blocks_both_venue_risk_gates() -> None:
    def unavailable() -> dict[str, object]:
        raise OSError("wiki status database unavailable")

    packet = JueWikiContextService(
        _Repository(),
        eligibility_reader=_Eligibility(),
        health_reader=unavailable,
        now=lambda: NOW,
    ).context_packet(
        WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        "required",
    )

    assert "wiki_health_unavailable" in packet.quality_warnings
    _assert_gate_preserves_operational_actions(packet)
