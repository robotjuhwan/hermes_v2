from __future__ import annotations

import pytest

from tradecraft.services.kis_research_packet import (
    KisResearchEvidenceV1,
    KisResearchPacketV2,
    build_kis_research_packet,
    build_kis_research_packets_for_symbols,
    kis_packet_candidate_claims,
)
from tradecraft.services.jue_research_spine import build_research_spine


def _report(
    report_id: int,
    *,
    broker: str,
    published_at: str,
    link_confidence: float = 0.95,
) -> dict[str, object]:
    return {
        "report_id": report_id,
        "symbol": "005930",
        "broker": broker,
        "published_at": published_at,
        "pdf_sha256": f"sha-{report_id}",
        "pdf_url": f"https://example.test/{report_id}.pdf",
        "link_confidence": link_confidence,
        "asset_class": "stock",
    }


def _facts(
    *,
    rating: str,
    target_price: int,
    catalysts: list[str] | None = None,
    risks: list[str] | None = None,
) -> dict[str, object]:
    return {
        "rating": rating,
        "target_price": {
            "value": target_price,
            "currency": "KRW",
            "changed": "UNKNOWN",
        },
        "catalysts": catalysts or [],
        "risks": risks or [],
        "evidence_quotes": ["원문 근거"],
    }


def test_packet_computes_target_revision_from_traceable_reports() -> None:
    packet = build_kis_research_packet(
        symbol="005930",
        asset_class="stock",
        reports=[
            _report(2, broker="A", published_at="2026-07-10"),
            _report(1, broker="A", published_at="2026-06-20"),
        ],
        facts_by_report={
            2: _facts(rating="BUY", target_price=110_000, catalysts=["HBM 수요"]),
            1: _facts(rating="BUY", target_price=100_000),
        },
        now="2026-07-11T00:00:00+00:00",
    )

    assert packet.status == "eligible"
    assert packet.entry_support == "supported"
    assert packet.revisions["target_price_pct"] == pytest.approx(10.0)
    assert packet.evidence[0].report_id == 2
    assert packet.evidence[0].source_ref["pdf_sha256"] == "sha-2"
    assert packet.addition_allowed is True


def test_conflicting_broker_directions_require_waiting_entry() -> None:
    packet = build_kis_research_packet(
        symbol="005930",
        asset_class="stock",
        reports=[
            _report(2, broker="A", published_at="2026-07-10"),
            _report(1, broker="B", published_at="2026-07-09"),
        ],
        facts_by_report={
            2: _facts(rating="BUY", target_price=90_000),
            1: _facts(rating="SELL", target_price=50_000),
        },
        now="2026-07-11T00:00:00+00:00",
    )

    assert packet.conflict_status == "material"
    assert packet.entry_support == "waiting_entry"
    assert packet.addition_allowed is False


def test_research_spine_attaches_kis_packet_to_matching_symbol() -> None:
    packet = build_kis_research_packet(
        symbol="005930",
        asset_class="stock",
        reports=[_report(2, broker="A", published_at="2026-07-10")],
        facts_by_report={2: _facts(rating="BUY", target_price=110_000)},
        now="2026-07-11T00:00:00+00:00",
    )

    spine = build_research_spine(
        strategy_payload={
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "score": 80,
                    "confidence": 75,
                    "sources": ["naver_reports"],
                }
            ]
        },
        daily_discovery=None,
        market_judgment=None,
        etf_research=None,
        investment_memory=None,
        account={"positions": []},
        blocks=[],
        quotes=[],
        kis_research_packets={"005930": packet.to_dict()},
    )

    row = next(item for item in spine["packets"] if item["symbol"] == "005930")
    assert row["kis_research"]["version"] == "kis_research_packet_v2"
    assert row["kis_research"]["evidence"][0]["report_id"] == 2
    assert spine["quality_summary"]["kis_research_eligible_count"] == 1


def test_packet_map_reads_each_symbol_from_repository_once() -> None:
    class Repository:
        def __init__(self) -> None:
            self.report_calls: list[str] = []
            self.fact_calls: list[int] = []

        def latest_symbol_linked_reports(
            self,
            symbol: str,
            *,
            limit: int,
        ) -> list[dict[str, object]]:
            assert limit == 12
            self.report_calls.append(symbol)
            return [_report(2, broker="A", published_at="2026-07-10")]

        def get_report_facts(self, report_id: int) -> dict[str, object]:
            self.fact_calls.append(report_id)
            return _facts(rating="BUY", target_price=110_000)

    repository = Repository()

    packets = build_kis_research_packets_for_symbols(
        repository=repository,
        symbols=["005930", "005930"],
        asset_classes={"005930": "stock"},
        now="2026-07-11T00:00:00+00:00",
    )

    assert repository.report_calls == ["005930"]
    assert repository.fact_calls == [2]
    assert packets["005930"]["status"] == "eligible"


def test_kis_packet_candidate_claims_verify_only_confirmed_hashed_facts() -> None:
    packet = KisResearchPacketV2(
        symbol="005930",
        asset_class="stock",
        status="eligible",
        entry_support="supported",
        addition_allowed=True,
        revisions={},
        conflict_status="none",
        confirmed_facts=("confirmed rating",),
        interpretation=("target may rise",),
        missing_data=("forward estimate missing",),
        evidence=(
            KisResearchEvidenceV1(
                report_id=42,
                symbol="005930",
                published_at="2026-07-10T00:00:00+00:00",
                broker="example",
                rating="BUY",
                target_price=100_000,
                catalysts=(),
                risks=(),
                evidence_quotes=("forecast revised",),
                source_ref={
                    "pdf_sha256": "f" * 64,
                    "pdf_archived_path": "/evidence/report-42.pdf",
                },
                link_confidence=0.99,
                freshness="fresh",
            ),
        ),
    )

    claims = kis_packet_candidate_claims(packet, artifact_id="artifact:42")

    assert [claim.claim_type for claim in claims] == [
        "fact",
        "interpretation",
        "hypothesis",
    ]
    assert [claim.status for claim in claims] == ["verified", "draft", "draft"]
    assert claims[0].evidence[0].evidence_id == "naver-report:42"
    assert claims[0].evidence[0].content_hash == "f" * 64
    assert claims[2].text == "forward estimate missing"


def test_kis_packet_candidate_fact_without_source_hash_remains_draft() -> None:
    packet = build_kis_research_packet(
        symbol="005930",
        asset_class="stock",
        reports=[
            {
                **_report(42, broker="A", published_at="2026-07-10"),
                "pdf_sha256": "",
            }
        ],
        facts_by_report={42: _facts(rating="BUY", target_price=100_000)},
        now="2026-07-11T00:00:00+00:00",
    )

    claims = kis_packet_candidate_claims(packet, artifact_id="artifact:42")

    assert claims[0].status == "draft"
    assert claims[0].evidence == ()


@pytest.mark.parametrize("invalid_hash", ["not-hex", "a" * 63, "g" * 64])
def test_kis_packet_malformed_source_hash_cannot_verify_fact(
    invalid_hash: str,
) -> None:
    packet = build_kis_research_packet(
        symbol="005930",
        asset_class="stock",
        reports=[
            {
                **_report(42, broker="A", published_at="2026-07-10"),
                "pdf_sha256": invalid_hash,
            }
        ],
        facts_by_report={42: _facts(rating="BUY", target_price=100_000)},
        now="2026-07-11T00:00:00+00:00",
    )

    claims = kis_packet_candidate_claims(packet, artifact_id="artifact:42")

    assert claims[0].status == "draft"
    assert claims[0].evidence == ()
