from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tradecraft.api.strategy import StrategyRouteDeps, build_strategy_router


class _FakeStrategyEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def source_status(self) -> list[dict[str, Any]]:
        self.calls.append({"method": "source_status"})
        return [
            {
                "source_id": "whale_insight",
                "status": "ok",
                "count": 1,
                "returned_count": 1,
                "signals": [{"symbol": "005930", "summary": "large sample row"}],
            }
        ]

    def list_external_signals(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": "list_external_signals", **kwargs})
        return {"status": "ok", "items": [kwargs]}

    def append_external_signals(
        self,
        *,
        source_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "append_external_signals",
                "source_id": source_id,
                "payload": payload,
            }
        )
        if source_id == "bad":
            raise ValueError("bad source")
        return {"status": "ok", "source_id": source_id, "inserted": 1}

    def build_candidates(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": "build_candidates", **kwargs})
        return {"status": "ok", "candidates": [], "query": kwargs["query"]}

    async def build_brief(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": "build_brief", **kwargs})
        return {
            "status": "ok",
            "brief_mode": "llm" if kwargs.get("use_llm") else "deterministic",
            "query": kwargs["query"],
        }


class _FakeCollector:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result

    async def collect_once(self) -> dict[str, Any]:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _client(
    engine: _FakeStrategyEngine,
    *,
    collector_result: dict[str, Any] | Exception | None = None,
    auth_required_error: bool = False,
) -> TestClient:
    auth_calls: list[dict[str, Any]] = []

    def _auth(
        authorization: str | None = None,
        admin_token: str | None = None,
    ) -> None:
        auth_calls.append(
            {"authorization": authorization, "admin_token": admin_token}
        )
        if auth_required_error:
            raise HTTPException(status_code=401, detail="unauthorized")

    app = FastAPI()
    app.state.auth_calls = auth_calls
    app.include_router(
        build_strategy_router(
            StrategyRouteDeps(
                require_admin_auth=_auth,
                strategy_engine=lambda: engine,
                classify_intent=lambda query: "buy_idea" if "매수" in query else "general",
                default_query=lambda query="": str(query or "기본 전략").strip(),
                safe_limit=lambda value: min(max(int(value or 8), 1), 30),
                read_research_feed=lambda: {"status": "ok", "items": []},
                collect_source_ids=lambda payload: list((payload or {}).get("source_ids") or []),
                safe_collect_sources=lambda source_ids=None: [
                    {"source_id": source_id} for source_id in (source_ids or [])
                ],
                build_insight_collector=lambda sources=None: _FakeCollector(
                    collector_result or {"status": "ok", "sources": sources or []}
                ),
                now=lambda: datetime(2026, 6, 20, tzinfo=timezone.utc),
            )
        )
    )
    return TestClient(app)


def test_strategy_intent_and_candidates_delegate_to_engine() -> None:
    engine = _FakeStrategyEngine()

    with _client(engine) as client:
        intent = client.post("/api/strategy/intent", json={"query": "매수 후보"})
        candidates = client.get("/api/strategy/candidates?query=테스트&limit=5")

    assert intent.status_code == 200
    assert intent.json() == {
        "status": "ok",
        "query": "매수 후보",
        "intent": "buy_idea",
    }
    assert candidates.status_code == 200
    assert candidates.json()["query"] == "테스트"
    assert engine.calls == [
        {
            "method": "build_candidates",
            "query": "테스트",
            "research_feed": {"status": "ok", "items": []},
            "limit": 5,
        }
    ]


def test_strategy_insights_collect_and_append_convert_value_errors() -> None:
    engine = _FakeStrategyEngine()

    with _client(engine, collector_result=ValueError("source_ids only")) as client:
        collect = client.post(
            "/api/strategy/insights/collect",
            json={"source_ids": ["whale_insight"]},
        )
        append_ok = client.post(
            "/api/strategy/insights/after_close_330",
            json={"symbol": "005930"},
        )
        append_bad = client.post(
            "/api/strategy/insights/bad",
            json={"symbol": "005930"},
        )

    assert collect.status_code == 400
    assert collect.json()["detail"] == "source_ids only"
    assert append_ok.status_code == 200
    assert append_ok.json()["inserted"] == 1
    assert append_bad.status_code == 400
    assert append_bad.json()["detail"] == "bad source"


def test_strategy_brief_requires_admin_only_for_llm_mode() -> None:
    engine = _FakeStrategyEngine()

    with _client(engine, auth_required_error=True) as client:
        deterministic = client.post(
            "/api/strategy/brief",
            json={"query": "테스트", "use_llm": False},
        )
        llm = client.post(
            "/api/strategy/brief",
            json={"query": "테스트", "use_llm": True},
        )

    assert deterministic.status_code == 200
    assert deterministic.json()["brief_mode"] == "deterministic"
    assert llm.status_code == 401


def test_strategy_brief_compact_trims_candidate_and_exclusion_payloads() -> None:
    class _HeavyStrategyEngine(_FakeStrategyEngine):
        async def build_brief(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append({"method": "build_brief", **kwargs})
            return {
                "status": "ok",
                "brief_mode": "deterministic",
                "query": kwargs["query"],
                "brief_md": "brief",
                "candidates": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "asset_class": "equity",
                        "score": 82,
                        "score_method_version": "v2",
                        "score_components": {"report": 80, "raw": "x" * 10_000},
                        "suitability": {
                            "balanced": {"score": 82, "grade": "A", "drivers": ["d" * 1_000]},
                            "short_term": {"score": 80, "grade": "A", "risks": ["r" * 1_000]},
                        },
                        "valuation": {
                            "label": "undervalued",
                            "metrics": {"per": 8.1, "raw": "v" * 10_000},
                            "score": {"undervalued_score": 78},
                            "reasons": ["value " + ("a" * 1_000)],
                            "risks": ["risk " + ("b" * 1_000)],
                            "raw_json": "ignored",
                        },
                        "confidence": 74,
                        "risk_score": 18,
                        "data_coverage": {
                            "source_count": 3,
                            "coverage_score": 80,
                            "missing": ["valuation", "whale", "after_close"],
                            "raw": "coverage" * 1_000,
                        },
                        "identity_status": {"status": "ok", "label": "검증됨", "raw": "i" * 10_000},
                        "data_warnings": ["warning " + ("w" * 1_000)],
                        "stance": "watch",
                        "reasons": ["reason " + ("x" * 1_000) for _ in range(6)],
                        "risks": ["risk " + ("y" * 1_000) for _ in range(5)],
                        "checks": ["check " + ("z" * 1_000) for _ in range(5)],
                        "sources": [f"source-{idx}" for idx in range(10)],
                        "report_ids": [str(idx) for idx in range(20)],
                        "citations": ["citation " + ("c" * 1_000) for _ in range(10)],
                        "facts": ["fact " + ("f" * 1_000) for _ in range(10)],
                    }
                ],
                "exclusions": [
                    {
                        "symbol": f"00000{idx}",
                        "name": "제외",
                        "reason": "exclude " + ("e" * 1_000),
                        "score": 20,
                        "score_components": {"raw": "x" * 10_000},
                        "facts": ["f" * 10_000],
                    }
                    for idx in range(3)
                ],
                "sources": [{"source_id": "naver_reports", "signals": [{"raw": "s" * 10_000}]}],
                "rag_context": [{"text": "rag " + ("r" * 10_000)}],
            }

    engine = _HeavyStrategyEngine()

    with _client(engine) as client:
        response = client.post(
            "/api/strategy/brief",
            json={"query": "테스트", "limit": 8, "compact": True},
        )

    assert response.status_code == 200
    payload = response.json()
    candidate = payload["candidates"][0]
    assert candidate["symbol"] == "005930"
    assert candidate["suitability"]["balanced"]["grade"] == "A"
    assert candidate["suitability"]["balanced"]["drivers"] == ["d" * 80]
    assert candidate["data_coverage"] == {
        "coverage_score": 80,
        "source_count": 3,
        "missing": ["valuation", "whale", "after_close"],
    }
    assert candidate["valuation"]["label"] == "undervalued"
    assert len(candidate["reasons"]) == 3
    assert len(candidate["sources"]) == 5
    assert len(candidate["report_ids"]) == 8
    assert len(candidate["facts"]) == 3
    assert all(len(item) <= 140 for item in candidate["facts"])
    assert len(candidate["citations"]) == 3
    assert all(len(item) <= 100 for item in candidate["citations"])
    assert len(payload["exclusions"]) == 3
    assert set(payload["exclusions"][0]) == {"symbol", "name", "reason", "score"}
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "raw_json" not in serialized
    assert "raw" not in serialized
    assert "rag " not in serialized
    assert len(serialized) < 12_000


def test_strategy_insights_status_has_schema_and_timestamp() -> None:
    engine = _FakeStrategyEngine()

    with _client(engine) as client:
        response = client.get("/api/strategy/insights")
        status_alias = client.get("/api/strategy/insights/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated_at"] == "2026-06-20T00:00:00+00:00"
    assert payload["sources"][0]["source_id"] == "whale_insight"
    assert payload["sources"][0]["signals"][0]["symbol"] == "005930"
    assert payload["schema"]["symbol"] == "005930"
    assert status_alias.status_code == 200
    status_payload = status_alias.json()
    assert status_payload["updated_at"] == payload["updated_at"]
    assert status_payload["schema"] == payload["schema"]
    assert status_payload["sources"][0]["source_id"] == "whale_insight"
    assert status_payload["sources"][0]["returned_count"] == 1
    assert "signals" not in status_payload["sources"][0]
