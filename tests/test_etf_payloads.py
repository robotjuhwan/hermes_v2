from __future__ import annotations

from typing import Any

from tradecraft.api.etf_payloads import build_etf_research_candidates_payload
from tradecraft.api.etf_payloads import merge_etf_items_payload


def test_build_etf_research_candidates_payload_prefers_configured_order() -> None:
    class _Repository:
        def list_universe(self) -> list[dict[str, Any]]:
            return [
                {"symbol": "091160", "name": "KODEX 반도체", "updated_at": "old"},
                {"symbol": "069500", "name": "KODEX 200", "updated_at": "hot"},
            ]

        def latest_snapshot(self, symbol: str) -> dict[str, Any]:
            return {"symbol": symbol, "price": 1000}

        def latest_score(self, symbol: str) -> dict[str, Any]:
            return {"symbol": symbol, "label": "watch"}

    configured = [
        type(
            "ETFItem",
            (),
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "category": "core",
                "tags": ["configured"],
            },
        )(),
        type(
            "ETFItem",
            (),
            {
                "symbol": "102110",
                "name": "TIGER 200",
                "category": "core",
                "tags": ["configured"],
            },
        )(),
    ]

    rows = build_etf_research_candidates_payload(
        repository=_Repository(),
        configured=configured,
    )

    assert [row["symbol"] for row in rows] == ["069500", "102110"]
    assert rows[0]["updated_at"] == "hot"
    assert rows[0]["latest_snapshot"] == {"symbol": "069500", "price": 1000}
    assert rows[0]["latest_score"] == {"symbol": "069500", "label": "watch"}
    assert rows[1]["name"] == "TIGER 200"
    assert rows[1]["updated_at"] == ""
    assert rows[1]["latest_snapshot"]["symbol"] == "102110"


def test_build_etf_research_candidates_payload_returns_repository_rows_without_configured_filter() -> None:
    class _Repository:
        def list_universe(self) -> list[dict[str, Any]]:
            return [{"symbol": "091160", "name": "KODEX 반도체"}]

        def latest_snapshot(self, symbol: str) -> dict[str, Any]:
            return {"symbol": symbol}

        def latest_score(self, symbol: str) -> dict[str, Any]:
            return {"label": "unknown"}

    rows = build_etf_research_candidates_payload(
        repository=_Repository(),
        configured=[],
    )

    assert rows == [
        {
            "symbol": "091160",
            "name": "KODEX 반도체",
            "latest_snapshot": {"symbol": "091160"},
            "latest_score": {"label": "unknown"},
        }
    ]


def test_merge_etf_items_payload_keeps_order_dedupes_and_limits() -> None:
    def item(symbol: str) -> Any:
        return type(
            "ETFItem",
            (),
            {
                "symbol": symbol,
                "name": symbol,
                "category": "test",
                "tags": [],
            },
        )()

    merged = merge_etf_items_payload(
        [item("069500"), item("BAD")],
        [item("069500"), item("102110"), item("091160")],
        limit=2,
    )

    assert [row.symbol for row in merged] == ["069500", "102110"]
