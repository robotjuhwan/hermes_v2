from __future__ import annotations

from tradecraft.services.opportunity_scanner import rank_opportunities


def test_rank_opportunities_uses_broad_pool_but_returns_compact_top_slice() -> None:
    symbols = [
        {"symbol": f"{index:06d}", "name": f"종목{index}", "market": "KOSPI"}
        for index in range(1, 301)
    ]
    reports = [{"symbol": "000010", "score": 30}, {"symbol": "000020", "score": 10}]
    insights = [{"symbol": "000020", "strength": 90}, {"symbol": "000030", "strength": 70}]
    positions = [{"symbol": "000040", "value_krw": 100_000}]

    result = rank_opportunities(
        symbols=symbols,
        reports=reports,
        insights=insights,
        fundamentals=[],
        etfs=[],
        positions=positions,
        limit=12,
        generated_at="2026-05-22T00:00:00+00:00",
    )

    assert result["status"] == "ok"
    assert result["pool_count"] == 300
    assert len(result["candidates"]) == 12
    assert result["coverage"]["position_count"] == 1
    assert result["generated_at"] == "2026-05-22T00:00:00+00:00"
    assert {row["symbol"] for row in result["candidates"][:4]} >= {
        "000010",
        "000020",
        "000040",
    }


def test_rank_opportunities_merges_symbols_only_seen_in_secondary_sources() -> None:
    result = rank_opportunities(
        symbols=[],
        reports=[{"symbol": "111111", "name": "리포트종목", "score": 12}],
        insights=[],
        fundamentals=[
            {
                "symbol": "222222",
                "name": "펀더멘털종목",
                "score": {"undervalued_score": 70},
            }
        ],
        etfs=[{"symbol": "333333", "name": "ETF", "momentum_score": 80}],
        positions=[],
        limit=5,
    )

    assert result["pool_count"] == 3
    assert [row["symbol"] for row in result["candidates"]] == [
        "333333",
        "222222",
        "111111",
    ]
    assert result["coverage"]["etf_count"] == 1
