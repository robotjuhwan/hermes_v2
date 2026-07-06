from __future__ import annotations

from tradecraft.api.helper_payloads import (
    build_helper_source_draft_answer,
    clean_helper_text,
    clean_report_title,
    format_citation,
    helper_query_keywords,
    helper_report_sort_key,
    normalize_helper_answer_contract,
    parse_helper_llm_content,
    safe_helper_limit,
)


def test_helper_payloads_own_text_limit_and_title_contracts() -> None:
    assert clean_helper_text("<b> 삼성전자 </b>\n  AI  수요", limit=20) == "삼성전자 AI 수요"
    assert clean_report_title({"title": "리포트 보기", "symbol": "005930", "category": "company_analysis"}) == (
        "005930 company_analysis"
    )
    assert format_citation("테스트증권", "2026-06-20", "3") == "[테스트증권, 2026-06-20, p.3]"
    assert safe_helper_limit("99") == 12
    assert safe_helper_limit("bad") == 8


def test_helper_payloads_own_query_sorting_and_answer_normalization() -> None:
    keywords = helper_query_keywords("삼성전자 005930 최근 긍정 근거와 AI 서버 후보 알려줘")
    assert keywords == ["삼성전자", "근거와", "AI", "서버"]

    exact = {
        "report_id": 8,
        "category": "company_analysis",
        "symbol": "005930",
        "title": "삼성전자(005930) AI 서버 수혜",
        "company_name": "삼성전자",
        "snippet": "메모리 회복",
        "broker": "테스트",
        "published_at": "2026-06-20",
    }
    broad = {
        "report_id": 9,
        "category": "market_info",
        "symbol": "",
        "title": "시장 점검",
        "published_at": "2026-06-21",
    }
    assert helper_report_sort_key(exact, query="삼성전자 AI", symbol="005930") < helper_report_sort_key(
        broad,
        query="삼성전자 AI",
        symbol="005930",
    )

    normalized = normalize_helper_answer_contract("요약\n핵심 근거\n리스크/반론")
    assert normalized.splitlines() == [
        "요약(3줄)",
        "핵심 근거(인용 포함)",
        "리스크/반론 체크리스트",
    ]
    assert parse_helper_llm_content('{"answer_md":"좋다"}') == {"answer_md": "좋다"}
    assert parse_helper_llm_content("plain answer") == {"answer_md": "plain answer"}


def test_helper_source_draft_answer_contains_strategy_and_report_sections() -> None:
    answer = build_helper_source_draft_answer(
        query="다음 거래일 후보",
        strategy_context={
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "score": 82,
                    "stance": "watch_add",
                    "reasons": ["메모리 가격 회복"],
                    "risks": ["환율 변동"],
                }
            ]
        },
        facts_rows=[
            {
                "report_id": 1,
                "broker": "테스트증권",
                "published_at": "2026-06-20",
                "snippet": "AI 서버 수요가 개선된다",
                "facts": {
                    "rating": "BUY",
                    "target_price": {"value": 100000},
                    "summary_bullets": ["실적 추정치 상향"],
                    "risks": ["재고 조정"],
                    "evidence_quotes": [{"page": 2}],
                },
            }
        ],
        rag_rows=[],
    )

    assert "전략 후보/감시 리스트" in answer
    assert "삼성전자(005930) score 82" in answer
    assert "투자의견 다수: BUY, 목표주가 평균: 100,000 KRW" in answer
