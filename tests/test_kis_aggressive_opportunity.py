from __future__ import annotations

from tradecraft.services.kis_aggressive_opportunity import (
    build_aggressive_opportunity_packet,
)


def test_aggressive_packet_prioritizes_limit_up_proximity_and_pre_surge() -> None:
    packet = build_aggressive_opportunity_packet(
        quotes=[
            {
                "symbol": "123450",
                "name": "테스트",
                "price": 12900,
                "raw": {
                    "stck_mxpr": "13000",
                    "acml_tr_pbmn": "5000000000",
                    "prdy_ctrt": "18.2",
                },
            },
            {
                "symbol": "222220",
                "name": "느린후보",
                "price": 10000,
                "raw": {
                    "stck_mxpr": "13000",
                    "acml_tr_pbmn": "100000000",
                    "prdy_ctrt": "1.0",
                },
            },
        ],
        daily_discovery={
            "pre_surge_candidates": [
                {
                    "symbol": "123450",
                    "name": "테스트",
                    "pre_surge": {
                        "score": 82,
                        "reasons": ["저점권", "거래대금"],
                    },
                }
            ]
        },
        research_spine={},
        strategy={},
        fundamentals_status={},
        market_pulse={},
        limit=10,
    )

    assert packet["status"] == "ok"
    assert packet["candidates"][0]["symbol"] == "123450"
    assert packet["candidates"][0]["name"] == "테스트"
    assert "limit_up_proximity" in packet["candidates"][0]["signals"]
    assert "pre_surge" in packet["candidates"][0]["signals"]
    assert packet["candidates"][0]["source_count"] >= 2


def test_aggressive_packet_reconstructs_pre_surge_from_raw_daily_discovery() -> None:
    packet = build_aggressive_opportunity_packet(
        quotes=[],
        daily_discovery={
            "items": [
                {
                    "symbol": "001390",
                    "name": "KG케미칼",
                    "score": 91.2,
                    "analysis": {
                        "stance": "block_candidate",
                        "confidence": 0.82,
                        "summary": "저평가 눌림목에서 거래대금과 순환매 가능성이 붙었다.",
                        "reasons": ["저평가", "눌림목", "거래대금 증가"],
                        "risks": ["추격 매수 주의"],
                    },
                }
            ],
        },
        research_spine={},
        strategy={},
        fundamentals_status={},
        market_pulse={},
        limit=10,
    )

    candidate = next(row for row in packet["candidates"] if row["symbol"] == "001390")
    assert "pre_surge" in candidate["signals"]
    assert candidate["metrics"]["pre_surge_score"] > 0


def test_aggressive_packet_keeps_research_and_strategy_sources() -> None:
    packet = build_aggressive_opportunity_packet(
        quotes=[],
        daily_discovery={},
        research_spine={
            "packets": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "score": 71,
                    "buckets": ["large_cap_equity"],
                    "evidence": {"reasons": ["반도체 순환매 후보"]},
                }
            ]
        },
        strategy={
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "score": 80,
                    "confidence": 70,
                    "reasons": ["전략 후보"],
                }
            ]
        },
        fundamentals_status={},
        market_pulse={"status": "ok", "regime": "risk_on"},
        limit=10,
    )

    candidate = packet["candidates"][0]
    assert candidate["symbol"] == "005930"
    assert "research_spine" in candidate["sources"]
    assert "strategy" in candidate["sources"]
    assert "market_pulse_risk_on" in candidate["signals"]


def test_aggressive_packet_reports_empty_when_no_candidates() -> None:
    packet = build_aggressive_opportunity_packet(
        quotes=[],
        daily_discovery={},
        research_spine={},
        strategy={},
        fundamentals_status={},
        market_pulse={},
    )

    assert packet["status"] == "empty"
    assert packet["candidates"] == []
    assert packet["candidate_count"] == 0
