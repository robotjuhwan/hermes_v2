from __future__ import annotations

from pathlib import Path

from tradecraft.services.naver_reports import (
    NaverReportRepository,
    _is_research_detail_url,
)


def test_naver_report_repository_upsert_and_search(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=1",
        pdf_url="https://stock.pstatic.net/stock-research/company/1.pdf",
        pdf_sha256="abc123",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc123.pdf",
        title="삼성전자 리포트",
        company_name="삼성전자",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2025-01-10",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="삼성전자 실적 개선 전망과 밸류에이션 재평가 가능성",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    assert report_id > 0
    status = repo.status()
    assert status["total_reports"] == 1

    rows = repo.search(query="밸류에이션", symbol="005930", limit=5)
    assert len(rows) == 1
    assert rows[0]["category"] == "company_analysis"
    assert rows[0]["symbol"] == "005930"
    assert "삼성전자" in rows[0]["title"]

    market_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=2",
        pdf_url="https://stock.pstatic.net/stock-research/market/2.pdf",
        pdf_sha256="def456",
        pdf_archived_path=".runtime/naver_reports/pdfs/de/f4/def456.pdf",
        title="코스피 시황",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2025-01-11",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="코스피 수급 흐름과 매크로 이벤트 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    assert market_id > report_id

    cat_rows = repo.search(query="", symbol="", category="market_info", limit=5)
    assert len(cat_rows) == 1
    assert cat_rows[0]["category"] == "market_info"

    chunks = repo.list_chunks_for_rag(limit=10)
    assert len(chunks) >= 2
    by_id = {
        int(row["report_id"]): row for row in chunks if int(row["chunk_index"]) == 0
    }
    assert report_id in by_id
    assert market_id in by_id
    assert by_id[report_id]["category"] == "company_analysis"
    assert "실적 개선" in by_id[report_id]["content"]


def test_is_research_detail_url_accepts_read_pages_only() -> None:
    assert _is_research_detail_url(
        "https://finance.naver.com/research/company_read.naver?nid=10&page=1"
    )
    assert _is_research_detail_url("https://stock.naver.com/research/company/12345")
    assert not _is_research_detail_url(
        "https://finance.naver.com/research/company_list.naver?page=1"
    )
    assert not _is_research_detail_url("https://finance.naver.com/research/")


def test_naver_report_repository_upsert_with_structured_facts(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=2",
        pdf_url="https://stock.pstatic.net/stock-research/market/2.pdf",
        pdf_sha256="def456",
        pdf_archived_path=".runtime/naver_reports/pdfs/de/f4/def456.pdf",
        title="코스피 시황",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2025-01-11",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="코스피 수급 흐름과 매크로 이벤트 점검",
        chunk_size=200,
        max_chunks_per_report=10,
        structured_facts={
            "rating": "BUY",
            "target_price": {"value": 120000, "currency": "KRW", "changed": "UP"},
            "summary_bullets": ["핵심 포인트"],
            "investment_thesis": ["매크로 개선"],
            "risks": ["변동성"],
            "earnings_outlook": [],
            "valuation": {
                "method": "PER",
                "value": 10.0,
                "basis": "2026E",
                "notes": "",
            },
            "catalysts": ["정책 모멘텀"],
            "evidence_quotes": [{"page": 1, "tag": "summary", "text": "핵심"}],
        },
    )

    facts = repo.get_report_facts(report_id)
    assert facts is not None
    assert facts.get("rating") == "BUY"
    assert int((facts.get("target_price") or {}).get("value") or 0) == 120000


def test_symbol_directory_upsert_and_resolve(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="삼성전자",
        market="KOSPI",
        source="test",
        confidence=0.9,
    )

    assert repo.get_symbol_name("005930") == "삼성전자"
    out = repo.resolve_symbol_names(["005930", "000660"])
    assert out == {"005930": "삼성전자"}


def test_upsert_report_backfills_symbol_directory(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=1",
        pdf_url="https://stock.pstatic.net/stock-research/company/1.pdf",
        pdf_sha256="abc123",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc123.pdf",
        title="삼성전자 리포트",
        company_name="삼성전자",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2025-01-10",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="삼성전자 실적 개선 전망",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    assert report_id > 0
    assert repo.get_symbol_name("005930") == "삼성전자"
