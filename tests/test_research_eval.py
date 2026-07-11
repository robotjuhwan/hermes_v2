from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tradecraft import main


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def test_research_citation_eval_cases(monkeypatch, tmp_path: Path) -> None:
    async def fail_llm_with_source_fallback(**_: object) -> dict[str, object]:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "mode": "pytest_source_only",
                "error_message": "citation eval does not call external LLMs",
            },
        )

    monkeypatch.setattr(main, "_build_helper_llm_answer", fail_llm_with_source_fallback)
    monkeypatch.setattr(main, "_collect_helper_rag_rows", lambda **_: [])
    monkeypatch.setattr(
        main,
        "_collect_helper_strategy_context",
        lambda query, limit: {
            "status": "ok",
            "query": query,
            "candidates": [],
            "source_status": [],
        },
    )
    monkeypatch.setattr(
        main,
        "RESEARCH_QUERY_LOG_PATH",
        tmp_path / "research_query.log.jsonl",
    )
    monkeypatch.setattr(
        main.naver_report_repository,
        "search",
        lambda query, symbol, category, limit: [
            {
                "report_id": 101,
                "title": "테스트 리포트",
                "broker": "테스트증권",
                "symbol": symbol or "005930",
                "published_at": "2026-02-01",
                "snippet": f"{query} 관련 핵심 포인트",
            }
        ],
    )
    monkeypatch.setattr(
        main.naver_report_repository,
        "get_report_facts",
        lambda report_id: {
            "rating": "BUY",
            "target_price": {"value": 99000, "currency": "KRW", "changed": "UP"},
            "summary_bullets": ["실적 개선 기대"],
            "risks": ["수요 둔화 가능성"],
            "evidence_quotes": [
                {"page": 5, "tag": "target_price", "text": "목표주가 9.9만원"}
            ],
        }
        if int(report_id) == 101
        else None,
    )

    eval_path = Path("tests/eval/research_citation_eval.jsonl")
    lines = [
        json.loads(row)
        for row in eval_path.read_text(encoding="utf-8").splitlines()
        if row.strip()
    ]
    assert lines

    with TestClient(main.app) as client:
        for case in lines:
            response = client.get(
                "/api/research/ask",
                params={
                    "query": str(case.get("query") or ""),
                    "symbol": str(case.get("symbol") or ""),
                    "limit": 5,
                },
                headers=_admin_headers(monkeypatch),
            )
            assert response.status_code == 200
            payload = response.json()
            answer = str(payload.get("answer") or "")
            for token in list(case.get("must_include") or []):
                assert str(token) in answer
            assert payload.get("citations")
