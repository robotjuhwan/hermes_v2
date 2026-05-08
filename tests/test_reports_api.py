from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main


def test_reports_status_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main.naver_report_repository,
        "status",
        lambda: {
            "total_reports": 3,
            "last_updated_at": "2026-01-01T00:00:00+00:00",
            "last_published_at": "2025-12-31",
            "db_path": ".runtime/naver_reports.db",
        },
    )

    with TestClient(main.app) as client:
        response = client.get("/api/reports/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["repository"]["total_reports"] == 3


def test_reports_search_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main.naver_report_repository,
        "search",
        lambda query, symbol, category, limit: [
            {
                "report_id": 1,
                "category": category or "company_analysis",
                "title": "테스트 리포트",
                "broker": "테스트증권",
                "symbol": symbol or "005930",
                "published_at": "2025-01-01",
                "pdf_url": "https://example.com/a.pdf",
                "detail_url": "https://example.com/a",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "snippet": query or "요약",
            }
        ],
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/reports/search",
            params={"query": "반도체", "symbol": "005930", "limit": 5},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["items"][0]["symbol"] == "005930"


def test_reports_crawl_once_endpoint(monkeypatch) -> None:
    async def fake_crawl_once() -> dict:
        return {
            "status": "ok",
            "inserted": 1,
            "repository": {"total_reports": 10},
        }

    monkeypatch.setattr(main.naver_report_crawler, "crawl_once", fake_crawl_once)
    monkeypatch.setattr(
        main.naver_report_repository,
        "repair_metadata_quality",
        lambda: {"status": "ok", "updated_reports": 0},
    )
    monkeypatch.setattr(main.settings, "rag_enabled", False)

    with TestClient(main.app) as client:
        response = client.post("/api/reports/crawl-once")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["snapshot"]["inserted"] == 1
    assert payload["metadata_repair"]["status"] == "ok"


def test_reports_repair_metadata_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main.naver_report_repository,
        "repair_metadata_quality",
        lambda: {"status": "ok", "updated_reports": 2},
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/reports/repair-metadata",
            params={"sync_rag_after": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["repair"]["updated_reports"] == 2
    assert payload["rag_sync"] is None


def test_rag_status_endpoint_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "rag_enabled", False)

    with TestClient(main.app) as client:
        response = client.get("/api/rag/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["enabled"] is False
    assert payload["rag"]["reason"] == "rag_disabled"


def test_rag_sync_endpoint(monkeypatch) -> None:
    class _FakeRAGStore:
        def sync_documents(
            self,
            docs: list[dict],
            *,
            force_update: bool = False,
        ) -> dict:
            return {
                "status": "ok",
                "synced": len(docs),
                "force_update": force_update,
            }

    monkeypatch.setattr(main.settings, "rag_enabled", True)
    monkeypatch.setattr(main.settings, "rag_sync_chunk_limit", 2)
    monkeypatch.setattr(main, "rag_store", _FakeRAGStore())
    monkeypatch.setattr(
        main.naver_report_repository,
        "list_chunks_for_rag",
        lambda limit: [
            {"report_id": 1, "chunk_index": 0, "content": "a"},
            {"report_id": 1, "chunk_index": 1, "content": "b"},
        ],
    )

    with TestClient(main.app) as client:
        response = client.post("/api/rag/sync")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["enabled"] is True
    assert payload["result"]["status"] == "ok"
    assert payload["result"]["synced"] == 2
    assert payload["result"]["force_update"] is False


def test_rag_search_uses_default_top_k(monkeypatch) -> None:
    called: dict[str, int] = {"limit": 0}

    class _FakeRAGStore:
        def query(
            self,
            query: str,
            symbol: str = "",
            limit: int = 8,
            broker: str = "",
            doc_id: str = "",
            date_from: str = "",
            date_to: str = "",
        ) -> list[dict]:
            _ = (broker, doc_id, date_from, date_to)
            called["limit"] = int(limit)
            return [
                {"content": query, "symbol": symbol, "report_id": 1, "chunk_index": 0}
            ]

    monkeypatch.setattr(main.settings, "rag_enabled", True)
    monkeypatch.setattr(main.settings, "rag_query_top_k", 13)
    monkeypatch.setattr(main, "rag_store", _FakeRAGStore())

    with TestClient(main.app) as client:
        response = client.get("/api/rag/search", params={"query": "반도체"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["enabled"] is True
    assert payload["count"] == 1
    assert called["limit"] == 13


def test_research_ask_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main.naver_report_repository,
        "search",
        lambda query, symbol, category, limit: [
            {
                "report_id": 11,
                "title": "반도체 사이클",
                "broker": "테스트증권",
                "symbol": symbol or "005930",
                "published_at": "2026-02-01",
                "snippet": "메모리 업사이클 신호",
            }
        ],
    )
    monkeypatch.setattr(
        main.naver_report_repository,
        "get_report_facts",
        lambda report_id: {
            "rating": "BUY",
            "target_price": {"value": 98000, "currency": "KRW", "changed": "UP"},
            "summary_bullets": ["실적 개선과 수요 회복"],
            "risks": ["단기 변동성 확대"],
            "evidence_quotes": [
                {"page": 7, "tag": "target_price", "text": "목표주가 9.8만원"}
            ],
        }
        if int(report_id) == 11
        else None,
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/research/ask",
            params={
                "query": "삼성전자 목표주가 상향 근거",
                "symbol": "005930",
                "limit": 5,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert "요약(3줄)" in payload["answer"]
    assert "핵심 근거(인용 포함)" in payload["answer"]
    assert payload["citations"]


def test_helper_ask_endpoint_uses_llm_and_rag(monkeypatch) -> None:
    monkeypatch.setattr(
        main.naver_report_repository,
        "search",
        lambda query, symbol, category, limit: [
            {
                "report_id": 21,
                "title": "삼성전자 업황 점검",
                "broker": "테스트증권",
                "symbol": symbol or "005930",
                "published_at": "2026-04-30",
                "snippet": "메모리 가격 회복과 AI 서버 수요가 핵심 근거",
            }
        ],
    )
    monkeypatch.setattr(
        main.naver_report_repository,
        "get_report_facts",
        lambda report_id: {
            "rating": "BUY",
            "target_price": {"value": 105000, "currency": "KRW", "changed": "UP"},
            "summary_bullets": ["AI 서버 수요와 메모리 가격 개선"],
            "risks": ["환율과 재고 조정"],
            "evidence_quotes": [
                {"page": 3, "tag": "summary", "text": "AI 서버 수요"}
            ],
        }
        if int(report_id) == 21
        else None,
    )

    class _FakeRAG:
        def query(
            self,
            query: str,
            symbol: str = "",
            limit: int = 8,
            broker: str = "",
            doc_id: str = "",
            date_from: str = "",
            date_to: str = "",
        ) -> list[dict]:
            _ = (query, limit, broker, doc_id, date_from, date_to)
            return [
                {
                    "report_id": 21,
                    "chunk_index": 1,
                    "content": "AI 서버향 고부가 메모리 수요가 이어진다",
                    "symbol": symbol or "005930",
                    "broker": "테스트증권",
                    "published_at": "2026-04-30",
                    "page_start": 4,
                    "title": "삼성전자 업황 점검",
                }
            ]

    class _FakeBridge:
        ready = True
        resolved_model = "gpt-5.5"

        async def complete(
            self,
            payload: dict,
            timeout_ms: int | None = None,
        ) -> dict:
            assert payload["model"] == "gpt-5.5"
            assert timeout_ms is not None
            return {
                "ok": True,
                "content": (
                    '{"answer_md":"근거 기반 답변입니다.","confidence":"high",'
                    '"followups":["리스크만 다시 보기"],"limitations":["정보 제공용"]}'
                ),
                "usage": {"total_tokens": 42},
            }

    monkeypatch.setattr(main.settings, "rag_enabled", True)
    monkeypatch.setattr(main, "rag_store", _FakeRAG())
    monkeypatch.setattr(main, "helper_llm_bridge", _FakeBridge())

    with TestClient(main.app) as client:
        response = client.post(
            "/api/helper/ask",
            json={"query": "삼성전자 긍정 근거", "symbol": "005930", "limit": 5},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "llm"
    assert payload["model"] == "gpt-5.5"
    assert payload["answer"] == "근거 기반 답변입니다."
    assert payload["count"] == 1
    assert payload["rag_count"] == 1
    assert payload["followups"] == ["리스크만 다시 보기"]


def test_portfolio_coach_review_queue_endpoints(monkeypatch) -> None:
    state = {
        "row": {
            "message_id": 101,
            "message_md": "[Portfolio Coach] test",
            "status": "pending_review",
        }
    }

    class _Store:
        def list_advice_messages(self, *, status: str, limit: int) -> list[dict]:
            _ = (status, limit)
            return [dict(state["row"])]

        def get_advice_message(self, message_id: int) -> dict | None:
            if int(message_id) != int(state["row"]["message_id"]):
                return None
            return dict(state["row"])

        def update_message_status(
            self,
            *,
            message_id: int,
            status: str,
            review_note: str = "",
        ) -> bool:
            if int(message_id) != int(state["row"]["message_id"]):
                return False
            state["row"]["status"] = status
            state["row"]["review_note"] = review_note
            return True

    async def _send_message(message: str) -> dict:
        _ = message
        return {"ok": True}

    monkeypatch.setattr(main, "portfolio_coach_store", _Store())
    monkeypatch.setattr(main.telegram, "send_message", _send_message)

    with TestClient(main.app) as client:
        queue_res = client.get("/api/portfolio-coach/review-queue")
        assert queue_res.status_code == 200
        assert queue_res.json()["count"] == 1

        approve_res = client.post(
            "/api/portfolio-coach/review-queue/101/approve",
            json={"review_note": "ok"},
        )
        assert approve_res.status_code == 200
        assert approve_res.json()["sent"] is True

        reject_res = client.post(
            "/api/portfolio-coach/review-queue/101/reject",
            json={"review_note": "hold"},
        )
        assert reject_res.status_code == 200
        assert reject_res.json()["updated"] is True
