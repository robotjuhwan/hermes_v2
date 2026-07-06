from __future__ import annotations

import asyncio
import os
from pathlib import Path

import tradecraft.services.naver_reports as naver_reports
from tradecraft.services.naver_reports import (
    NaverReportCrawlerConfig,
    NaverReportRepository,
    NaverSecuritiesCrawler,
    _clean_company_name,
    _extract_analyst_from_text,
    _extract_broker_from_text,
    _extract_company_symbol_from_text,
    _extract_department_author_from_text,
    _extract_report_symbol_links,
    _infer_default_department_author_from_context,
    _is_research_detail_url,
    _looks_like_garbled_pdf_text,
    _parse_date,
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

    detail = repo.get_report(report_id)
    assert detail is not None
    assert detail["title"] == "삼성전자 리포트"
    assert detail["symbol"] == "005930"

    chunk_rows = repo.list_report_chunks(report_id, limit=5)
    assert chunk_rows
    assert chunk_rows[0]["report_id"] == report_id


def test_naver_report_empty_query_search_does_not_require_chunk_table(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=chunkless",
        pdf_url="https://stock.pstatic.net/stock-research/market/chunkless.pdf",
        pdf_sha256="chunkless",
        pdf_archived_path=".runtime/naver_reports/pdfs/chunkless.pdf",
        title="청크 없는 빈 검색",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2025-01-12",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="빈 검색은 청크 테이블을 스캔하지 않고 원문 앞부분으로 스니펫을 만든다.",
        chunk_size=20,
        max_chunks_per_report=10,
    )

    with repo._connect() as conn:  # noqa: SLF001 - verifies query-plan dependency
        conn.execute("DROP TABLE report_chunks")

    rows = repo.search(query="", category="market_info", limit=5)

    assert [row["report_id"] for row in rows] == [report_id]
    assert "원문 앞부분" in rows[0]["snippet"]


def test_search_keyword_snippet_prefers_matching_chunk_over_noisy_chunk(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="industry_analysis",
        source_url="https://finance.naver.com/research/industry_list.naver",
        detail_url="https://finance.naver.com/research/industry_read.naver?nid=snippet",
        pdf_url="https://stock.pstatic.net/stock-research/industry/snippet.pdf",
        pdf_sha256="snippet",
        pdf_archived_path=".runtime/naver_reports/pdfs/snippet.pdf",
        title="반도체 산업 점검",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "힣힣 본 조사분석자료는 참고 문구입니다. "
            "반도체 사이클은 HBM 수요와 장비 투자 재개를 중심으로 개선된다."
        ),
        chunk_size=200,
        max_chunks_per_report=10,
        chunks=[
            {
                "content": "힣힣 본 조사분석자료는 참고 문구입니다.",
                "page_start": 1,
                "page_end": 1,
                "section_title": "disclaimer",
            },
            {
                "content": "반도체 사이클은 HBM 수요와 장비 투자 재개를 중심으로 개선된다.",
                "page_start": 2,
                "page_end": 2,
                "section_title": "thesis",
            },
        ],
    )

    rows = repo.search(query="반도체", limit=5)

    row = next(item for item in rows if item["report_id"] == report_id)
    assert "반도체 사이클" in row["snippet"]
    assert "본 조사분석자료" not in row["snippet"]


def test_search_keyword_snippet_strips_leading_report_disclaimer(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=snippet-disclaimer",
        pdf_url="https://stock.pstatic.net/stock-research/company/snippet-disclaimer.pdf",
        pdf_sha256="snippet-disclaimer",
        pdf_archived_path=".runtime/naver_reports/pdfs/snippet-disclaimer.pdf",
        title="삼성전기 (009150) MLCC 대규모 수주 공시",
        company_name="삼성전기",
        broker="IBK투자증권",
        analyst="김운호",
        symbol="009150",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "www.ibks.com 본 조사분석자료는 참고 문구입니다. "
            "고객께서는 자신의 판단과 책임 하에 결정하시기 바랍니다. "
            "IBKS Spot Comment 2026. 7.1 IT/반도체 [삼성전기] MLCC 대규모 수주 공시"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
        chunks=[
            {
                "content": (
                    "www.ibks.com 본 조사분석자료는 참고 문구입니다. "
                    "고객께서는 자신의 판단과 책임 하에 결정하시기 바랍니다. "
                    "IBKS Spot Comment 2026. 7.1 IT/반도체 [삼성전기] MLCC 대규모 수주 공시"
                ),
                "page_start": 1,
                "page_end": 1,
                "section_title": "unknown",
            }
        ],
    )

    rows = repo.search(query="반도체", limit=5)

    row = next(item for item in rows if item["report_id"] == report_id)
    assert row["snippet"].startswith("IBKS Spot Comment")
    assert "본 조사분석자료" not in row["snippet"]


def test_search_keyword_snippet_normalizes_company_market_suffix_prefix(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=snippet-market-suffix",
        pdf_url="https://stock.pstatic.net/stock-research/company/snippet-market-suffix.pdf",
        pdf_sha256="snippet-market-suffix",
        pdf_archived_path=".runtime/naver_reports/pdfs/snippet-market-suffix.pdf",
        title="한국금융지주 (071050) ETF 타고 브로커리지 왕좌를 노린다",
        company_name="한국금융지주",
        broker="테스트증권",
        analyst="홍길동",
        symbol="071050",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "00 한국금융지주 (071050/KS) ETF 타고 브로커리지 왕좌를 노린다 "
            "ETF 까지 합산 시 브로커리지 점유율 1 등에 근접"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
        chunks=[
            {
                "content": (
                    "00 한국금융지주 (071050/KS) ETF 타고 브로커리지 왕좌를 노린다 "
                    "ETF 까지 합산 시 브로커리지 점유율 1 등에 근접"
                ),
                "page_start": 1,
                "page_end": 1,
                "section_title": "summary",
            }
        ],
    )

    rows = repo.search(query="ETF", limit=5)

    row = next(item for item in rows if item["report_id"] == report_id)
    assert row["snippet"].startswith("한국금융지주 (071050) ETF 타고")
    assert not row["snippet"].startswith("00 ")
    assert "(071050/KS)" not in row["snippet"]


def test_search_keyword_snippet_strips_leading_compliance_notice(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=compliance-snippet",
        pdf_url="https://stock.pstatic.net/stock-research/company/compliance-snippet.pdf",
        pdf_sha256="compliance-snippet",
        pdf_archived_path=".runtime/naver_reports/pdfs/compliance-snippet.pdf",
        title="아이엠티 (451220) 2026년 예상 매출 성장률 51%",
        company_name="아이엠티",
        broker="테스트증권",
        analyst="홍길동",
        symbol="451220",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "금융투자분석사의 확인 및 중요 공시는 Appendix 참조 NOT RATED "
            "현재주가 (6/29) 12,620원 2026년 장비와 소재, 동반 성장 가시화 "
            "반도체 HBM 고단화로 세정장비 수요 증가를 전망한다."
        ),
        chunk_size=220,
        max_chunks_per_report=10,
        chunks=[
            {
                "content": (
                    "금융투자분석사의 확인 및 중요 공시는 Appendix 참조 NOT RATED "
                    "현재주가 (6/29) 12,620원 2026년 장비와 소재, 동반 성장 가시화 "
                    "반도체 HBM 고단화로 세정장비 수요 증가를 전망한다."
                ),
                "page_start": 1,
                "page_end": 1,
                "section_title": "unknown",
            }
        ],
    )

    rows = repo.search(query="반도체", limit=5)

    row = next(item for item in rows if item["report_id"] == report_id)
    assert row["snippet"].startswith("NOT RATED")
    assert "금융투자분석사의 확인" not in row["snippet"]
    assert "Appendix 참조" not in row["snippet"]


def test_upsert_report_trims_noisy_company_report_title_prefix(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=noisy-title",
        pdf_url="https://stock.pstatic.net/stock-research/company/noisy-title.pdf",
        pdf_sha256="noisy-title",
        pdf_archived_path=".runtime/naver_reports/pdfs/noisy-title.pdf",
        title=(
            "주가 및 주요이벤트 재무지표 밸류에이션 지표 체크포인트 "
            "기업분석ㅣ2026.07.01 KOSPIㅣ식품,음료,담배 현대그린푸드 (453340)"
        ),
        company_name="현대그린푸드",
        broker="테스트증권",
        analyst="홍길동",
        symbol="453340",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "주가 및 주요이벤트 재무지표 밸류에이션 지표 체크포인트 "
            "기업분석ㅣ2026.07.01 KOSPIㅣ식품,음료,담배 현대그린푸드 (453340) "
            "영업실적도, 주주환원도 레벨업 ■ 기업가치 제고계획"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "현대그린푸드 (453340) 영업실적도, 주주환원도 레벨업"


def test_upsert_report_normalizes_company_title_with_market_suffix_code(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=market-suffix",
        pdf_url="https://stock.pstatic.net/stock-research/company/market-suffix.pdf",
        pdf_sha256="market-suffix",
        pdf_archived_path=".runtime/naver_reports/pdfs/market-suffix.pdf",
        title=(
            "00 한국금융지주 (071050/KS) ETF 타고 브로커리지 왕좌를 노린다 "
            "ETF 까지 합산 시 브로커리지 점유율 1 등에 근접"
        ),
        company_name="한국금융지주",
        broker="테스트증권",
        analyst="홍길동",
        symbol="071050",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "00 한국금융지주 (071050/KS) ETF 타고 브로커리지 왕좌를 노린다 "
            "ETF 까지 합산 시 브로커리지 점유율 1 등에 근접 ETF 일평균 거래대금이 급성장함에 따라"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "한국금융지주 (071050) ETF 타고 브로커리지 왕좌를 노린다"


def test_upsert_report_derives_company_spot_comment_title_without_code_in_text(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=company-spot",
        pdf_url="https://stock.pstatic.net/stock-research/company/company-spot.pdf",
        pdf_sha256="company-spot",
        pdf_archived_path=".runtime/naver_reports/pdfs/company-spot.pdf",
        title="www.ibks.com 본 조사분석자료는 당사 리서치본부에서 신뢰할 만한 자료",
        company_name="삼성전기",
        broker="IBK투자증권",
        analyst="김운호",
        symbol="009150",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "www.ibks.com 본 조사분석자료입니다. "
            "IBKS Spot Comment 2026. 7.1 IT/반도체 김운호 "
            "unokim88@ibks.com [삼성전기] MLCC 대규모 수주 공시 "
            "What’s New: 단일판매공급 계약 공시"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "삼성전기 (009150) MLCC 대규모 수주 공시"


def test_upsert_report_company_spot_comment_title_stops_before_body_context(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=company-spot-long",
        pdf_url="https://stock.pstatic.net/stock-research/company/company-spot-long.pdf",
        pdf_sha256="company-spot-long",
        pdf_archived_path=".runtime/naver_reports/pdfs/company-spot-long.pdf",
        title="www.ibks.com 본 조사분석자료는 당사 리서치본부에서 신뢰할 만한 자료",
        company_name="한미약품",
        broker="IBK투자증권",
        analyst="정이수",
        symbol="128940",
        published_at="2026-06-02",
        crawled_at="2026-06-02T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "www.ibks.com 본 조사분석자료입니다. "
            "IBKS Spot Comment 2026. 6. 2 제약/바이오 정이수 "
            "[한미약품] 일라이 릴리 기술이전으로 실적과 신약가치 ‘일석이조’ "
            "약 6년 만에 글로벌 빅파마와 기술이전 계약 체결 한미약품은 6월 1일 "
            "일라이 릴리와 라이선스 계약 체결을 발표했다."
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "한미약품 (128940) 일라이 릴리 기술이전으로 실적과 신약가치 ‘일석이조’"


def test_upsert_report_company_spot_comment_title_stops_before_repeated_company(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=company-repeat",
        pdf_url="https://stock.pstatic.net/stock-research/company/company-repeat.pdf",
        pdf_sha256="company-repeat",
        pdf_archived_path=".runtime/naver_reports/pdfs/company-repeat.pdf",
        title="www.ibks.com 본 조사분석자료는 당사 리서치본부에서 신뢰할 만한 자료",
        company_name="녹십자",
        broker="IBK투자증권",
        analyst="정이수",
        symbol="006280",
        published_at="2026-03-11",
        crawled_at="2026-03-11T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "www.ibks.com 본 조사분석자료입니다. "
            "IBKS Spot Comment 2026. 3. 11 제약/바이오 정이수 "
            "[녹십자] Investor Day 후기 녹십자는 2026년 3월 10일 "
            "핵심 품목인 알리글로의 미국 사업 전략을 공유했다."
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "녹십자 (006280) Investor Day 후기"


def test_upsert_report_derives_non_company_weekly_comment_title(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="industry_analysis",
        source_url="https://finance.naver.com/research/industry_list.naver",
        detail_url="https://finance.naver.com/research/industry_read.naver?nid=weekly",
        pdf_url="https://stock.pstatic.net/stock-research/industry/weekly.pdf",
        pdf_sha256="weekly-comment",
        pdf_archived_path=".runtime/naver_reports/pdfs/weekly-comment.pdf",
        title="1 제목입니다 2026.07.01 Yuanta Research ■ Valuation ■ 2026E 실적",
        company_name="",
        broker="유안타증권",
        analyst="",
        symbol="",
        published_at="2026-07-01",
        crawled_at="2026-07-01T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "1 제목입니다 2026.07.01 Yuanta Research ■ Valuation ■ 2026E 실적 "
            "■ 수급 현황 자료: 에프앤가이드 Quantiwise, 유안타증권 리서치센터 "
            "■ 주간 Comment(06/24~06/30) ■ 주가 현황 PER(x) PBR(x)"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "주간 Comment(06/24~06/30)"


def test_upsert_report_derives_non_company_spot_comment_title(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="industry_analysis",
        source_url="https://finance.naver.com/research/industry_list.naver",
        detail_url="https://finance.naver.com/research/industry_read.naver?nid=spot",
        pdf_url="https://stock.pstatic.net/stock-research/industry/spot.pdf",
        pdf_sha256="spot-comment",
        pdf_archived_path=".runtime/naver_reports/pdfs/spot-comment.pdf",
        title=(
            "www.ibks.com 본 조사분석자료는 당사 리서치본부에서 신뢰할 만한 자료 및 "
            "정보를 바탕으로 작성한 것이나"
        ),
        company_name="",
        broker="IBK투자증권",
        analyst="",
        symbol="",
        published_at="2026-06-30",
        crawled_at="2026-06-30T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "www.ibks.com 본 조사분석자료는 당사 리서치본부에서 신뢰할 만한 자료 및 "
            "정보를 바탕으로 작성한 것이나 과거의 자료입니다. "
            "IBKS Spot Comment 2026. 6.30 IT/반도체 김운호 02) 6915-5656 "
            "unokim88@ibks.com [반도체] 한국도 국가와 기업이 AI 기치 아래 공조 "
            "What’s New"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "IBKS Spot Comment [반도체] 한국도 국가와 기업이 AI 기치 아래 공조"


def test_upsert_report_spot_comment_title_stops_before_body_sentence(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="industry_analysis",
        source_url="https://finance.naver.com/research/industry_list.naver",
        detail_url="https://finance.naver.com/research/industry_read.naver?nid=spot-long",
        pdf_url="https://stock.pstatic.net/stock-research/industry/spot-long.pdf",
        pdf_sha256="spot-comment-long",
        pdf_archived_path=".runtime/naver_reports/pdfs/spot-comment-long.pdf",
        title="www.ibks.com 본 조사분석자료는 당사 리서치본부에서 신뢰할 만한 자료",
        company_name="",
        broker="IBK투자증권",
        analyst="",
        symbol="",
        published_at="2026-04-29",
        crawled_at="2026-04-29T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "www.ibks.com 본 조사분석자료입니다. "
            "IBKS Spot Comment 2026. 4.29 에너지/소재 연구원 "
            "energy@ibks.com [에너지/소재] 한화솔루션 유증, 얼마가 아니라 언제의 문제 "
            "이번 유상증자의 본질은 조달 선택권 방어에 있다"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["title"] == "IBKS Spot Comment [에너지/소재] 한화솔루션 유증, 얼마가 아니라 언제의 문제"


def test_naver_report_status_uses_short_cache_until_repository_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = NaverReportRepository(
        str(tmp_path / "reports.db"),
        status_cache_ttl_sec=60,
    )

    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=cache1",
        pdf_url="https://stock.pstatic.net/stock-research/company/cache1.pdf",
        pdf_sha256="cache1",
        pdf_archived_path=".runtime/naver_reports/pdfs/cache1.pdf",
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

    calls = 0
    original_compute_status = repo._compute_status

    def counted_compute_status() -> dict:
        nonlocal calls
        calls += 1
        return original_compute_status()

    monkeypatch.setattr(repo, "_compute_status", counted_compute_status)

    first = repo.status()
    first["total_reports"] = 999
    second = repo.status()

    assert calls == 1
    assert second["total_reports"] == 1

    repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=cache2",
        pdf_url="https://stock.pstatic.net/stock-research/market/cache2.pdf",
        pdf_sha256="cache2",
        pdf_archived_path=".runtime/naver_reports/pdfs/cache2.pdf",
        title="코스피 ETF 시황",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2025-01-11",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="ETF 시장 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    third = repo.status()

    assert calls == 2
    assert third["total_reports"] == 2


def test_naver_report_status_cache_tracks_sqlite_wal_timestamp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = NaverReportRepository(
        str(tmp_path / "reports.db"),
        status_cache_ttl_sec=60,
    )

    calls = 0
    original_compute_status = repo._compute_status

    def counted_compute_status() -> dict:
        nonlocal calls
        calls += 1
        return original_compute_status()

    monkeypatch.setattr(repo, "_compute_status", counted_compute_status)

    repo.status()
    wal_path = Path(f"{repo.path}-wal")
    wal_path.write_bytes(b"external wal activity")
    future = repo.path.stat().st_mtime_ns + 1_000_000_000
    os.utime(wal_path, ns=(future, future))
    repo.status()

    assert calls == 2


def test_naver_report_ops_status_uses_lightweight_summary_without_deep_quality_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=ops1",
        pdf_url="https://stock.pstatic.net/stock-research/company/ops1.pdf",
        pdf_sha256="ops1",
        pdf_archived_path=".runtime/naver_reports/pdfs/ops1.pdf",
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

    def fail_deep_status() -> dict:
        raise AssertionError("ops status must not run deep report quality scan")

    monkeypatch.setattr(repo, "_compute_status", fail_deep_status)

    payload = repo.ops_status()

    assert payload["status"] == "ok"
    assert payload["total_reports"] == 1
    assert payload["category_counts"] == {"company_analysis": 1}
    assert payload["last_updated_at"]
    assert payload["quality_mode"] == "lightweight"
    assert "identity_drift_samples" not in str(payload)


def test_naver_report_ops_status_uses_read_only_connection_without_wal_reset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=ops-ro",
        pdf_url="https://stock.pstatic.net/stock-research/market/ops-ro.pdf",
        pdf_sha256="ops-ro",
        pdf_archived_path=".runtime/naver_reports/pdfs/ops-ro.pdf",
        title="코스피 시황",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2025-01-11",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="코스피 수급 흐름",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    repo._ops_status_cache = None

    def fail_write_connection():
        raise AssertionError("ops status should not reset WAL through _connect")

    monkeypatch.setattr(repo, "_connect", fail_write_connection)

    payload = repo.ops_status()

    assert payload["status"] == "ok"
    assert payload["total_reports"] == 1


def test_naver_report_ops_status_hydrates_disk_cache_after_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "reports.db"
    repo = NaverReportRepository(str(db_path))
    repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=ops-cache",
        pdf_url="https://stock.pstatic.net/stock-research/market/ops-cache.pdf",
        pdf_sha256="ops-cache",
        pdf_archived_path=".runtime/naver_reports/pdfs/ops-cache.pdf",
        title="코스피 시황",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2025-01-12",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="코스피 수급 흐름",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    first = repo.ops_status()

    restarted = NaverReportRepository(str(db_path))

    def fail_compute_ops_status() -> dict:
        raise AssertionError("restart should hydrate lightweight ops status cache")

    monkeypatch.setattr(restarted, "_compute_ops_status", fail_compute_ops_status)

    payload = restarted.ops_status()

    assert payload["total_reports"] == first["total_reports"] == 1
    assert payload["quality_mode"] == "lightweight"


def test_report_upsert_rejects_semantically_invalid_published_date(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    assert _parse_date("발간일 2026.50.20") == ""

    report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=11",
        pdf_url="https://stock.pstatic.net/stock-research/market/bad-date.pdf",
        pdf_sha256="bad-date",
        pdf_archived_path="",
        title="날짜 오류 리포트",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-50-20",
        crawled_at="2026-06-04T00:00:00+00:00",
        content_source="pdf_extract",
        content="날짜 오류를 포함한 리포트",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)
    assert detail is not None
    assert detail["published_at"] == ""
    assert repo.status()["quality"]["invalid_published_at_count"] == 0


def test_clean_company_name_rejects_naver_quote_boilerplate() -> None:
    assert _clean_company_name("코스콤 국내 시세 정보") == ""
    assert _clean_company_name("테마 정보 네이버에 콘텐츠 제공 코스콤 국내 시세 정보") == ""
    assert _clean_company_name("Review") == ""
    assert _clean_company_name("Preview") == ""
    assert _clean_company_name("eugenefn.com") == ""
    assert _clean_company_name("PI첨단소재") == "PI첨단소재"


def test_resolve_symbol_names_ignores_boilerplate_directory_values(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    repo.upsert_symbol_directory(
        symbol="178920",
        company_name="코스콤 국내 시세 정보",
        source="test",
    )
    repo.upsert_symbol_directory(
        symbol="009150",
        company_name="삼성전기",
        source="test",
    )

    assert repo.resolve_symbol_names(["178920", "009150"]) == {"009150": "삼성전기"}


def test_refresh_symbol_directory_uses_krx_direct_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.calls: list[dict[str, str]] = []

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], data: dict[str, str]):
            _ = (url, headers)
            self.calls.append(data)
            if data["bld"] == "dbms/comm/finder/finder_stkisu":
                return _FakeResponse(
                    {
                        "block1": [
                            {
                                "short_code": "100120",
                                "codeName": "뷰웍스",
                                "marketCode": "KSQ",
                            },
                            {
                                "short_code": "005930",
                                "codeName": "삼성전자",
                                "marketCode": "STK",
                            },
                        ]
                    }
                )
            return _FakeResponse(
                {
                    "block1": [
                        {
                            "short_code": "069500",
                            "codeName": "KODEX 200",
                        }
                    ]
                }
            )

    monkeypatch.setattr(naver_reports.httpx, "Client", _FakeClient)

    result = repo.refresh_symbol_directory_from_krx(as_of="2026-05-06")

    assert result["ok"] is True
    assert result["updated"] == 3
    assert repo.resolve_symbol_names(["100120", "005930", "069500"]) == {
        "100120": "뷰웍스",
        "005930": "삼성전자",
        "069500": "KODEX 200",
    }


def test_is_research_detail_url_accepts_read_pages_only() -> None:
    assert _is_research_detail_url(
        "https://finance.naver.com/research/company_read.naver?nid=10&page=1"
    )
    assert _is_research_detail_url("https://stock.naver.com/research/company/12345")
    assert not _is_research_detail_url(
        "https://finance.naver.com/research/company_list.naver?page=1"
    )
    assert not _is_research_detail_url("https://finance.naver.com/research/")


def test_extract_company_symbol_from_pdf_text_patterns() -> None:
    symbol_names = {
        "017960": "한국카본",
        "247540": "에코프로비엠",
        "005930": "삼성전자",
    }

    assert _extract_company_symbol_from_text(
        "017960 · 조선 한국카본 돋보이는 방산",
        symbol_names=symbol_names,
    ) == ("017960", "한국카본")
    assert _extract_company_symbol_from_text(
        "(247540. KQ) 에코프로비엠 2026.04.29 투자의견 BUY",
        symbol_names=symbol_names,
    ) == ("247540", "에코프로비엠")
    assert _extract_company_symbol_from_text(
        "삼성전자 (005930) 목표주가 상향",
        symbol_names=symbol_names,
    ) == ("005930", "삼성전자")
    assert _extract_company_symbol_from_text(
        "오르비텍 046120 Apr 30, 2026 N/R 새로운 시작",
        symbol_names=symbol_names,
    ) == ("046120", "오르비텍")
    assert _extract_company_symbol_from_text(
        "(010280,KQ) 아이티센엔텍 2026.04.02 IT S/W & H/W",
        symbol_names=symbol_names,
    ) == ("010280", "아이티센엔텍")
    assert _extract_company_symbol_from_text(
        "IR 삼성전자 COMMENT 1Q26 Conference call",
        symbol_names=symbol_names,
    ) == ("005930", "삼성전자")
    assert _extract_company_symbol_from_text(
        "2026년 5월 4일 I 기업분석_Earnings Preview GS건설 (006360)",
        symbol_names=symbol_names,
    ) == ("006360", "GS건설")
    assert _extract_company_symbol_from_text(
        "키움증권리서치센터 Price Trend LG에너지솔루션 (373220)",
        symbol_names=symbol_names,
    ) == ("373220", "LG에너지솔루션")
    assert _extract_company_symbol_from_text(
        "2026년 5월 4일 I 기업분석_Review NAVER (035420)",
        symbol_names={"035420": "일기업분석"},
    ) == ("035420", "NAVER")
    assert _extract_company_symbol_from_text(
        "(307950) 현대오토에버 2026.03.23 이재일 CFA",
        symbol_names={"307950": "SDV가수익모델의구"},
    ) == ("307950", "현대오토에버")
    assert _extract_company_symbol_from_text(
        "Kyobo Company Analysis 엔터 JYP Ent. 035900 4Q25 Review",
        symbol_names={"035900": "가적인상승여력도충분하다고판단한다. JYP Ent."},
    ) == ("035900", "JYP Ent.")
    assert _extract_company_symbol_from_text(
        "(078890) 가온그룹 2026.03.04 통신 이찬영 현재주가 7,600원",
        symbol_names={"078890": "대차의로봇산업진출확대시수혜가예상된다. 가온그룹"},
    ) == ("078890", "가온그룹")


def test_extract_analyst_from_pdf_text_patterns() -> None:
    assert (
        _extract_analyst_from_text("철강금속 Analyst 이종형 leejh@kiwoom.com")
        == "이종형"
    )
    assert (
        _extract_analyst_from_text("그린산업/ESG 한병화 bhh1026@eugenefn.com")
        == "한병화"
    )
    assert (
        _extract_analyst_from_text(
            "기술분석보고서율촌(146060) 작 성 기 관서울평가정보(주) 박진희 책임작 성 자"
        )
        == "박진희"
    )
    assert (
        _extract_analyst_from_text(
            "펄어비스 출시 후 점검 인터넷/게임. 남효지 / hjnam@sks.co.kr"
        )
        == "남효지"
    )
    assert (
        _extract_analyst_from_text(
            "Monetary Policy [채권] 김명실 2122-9206 msbond@imfnsec.com"
        )
        == "김명실"
    )
    assert (
        _extract_analyst_from_text(
            "(035420) NAVER 인터넷/게임/우주 정의훈 6170 / uihoon0607@eugenefn.com"
        )
        == "정의훈"
    )
    assert (
        _extract_analyst_from_text(
            "본문 앞부분 " + ("채권 시장 점검 " * 1200) + "작성자: 이재형"
        )
        == "이재형"
    )
    assert _extract_analyst_from_text("작성되었음을 확인함. 본인의 의견") == ""
    assert _extract_analyst_from_text("Weekly Research Center 리서치센터") == ""
    assert (
        _extract_analyst_from_text(
            "키움증권 리서치센터 투자전략팀 채권전략 안예하 4월 FOMC Review"
        )
        == "안예하"
    )
    assert (
        _extract_analyst_from_text(
            "2026. 4.10 채권 김지 나 6148 / jnkim0526@eugenefn.com"
        )
        == "김지나"
    )
    assert (
        _extract_analyst_from_text(
            "방산 5사 12M Fwd PBR-ROE Matrix 연구원강태호02-709-2666 kth@ds-sec.co.kr"
        )
        == "강태호"
    )
    assert (
        _extract_analyst_from_text(
            "FICC 리서치부 [Key Point] 정해창 / Strategist Jr. haechang.chung@daishin.com"
        )
        == "정해창"
    )
    assert (
        _extract_analyst_from_text(
            "Fixed Income 국채 투자의견 채권 Strategist 박 준우, CFA junoopark@hanafn.com"
        )
        == "박준우"
    )
    assert (
        _extract_analyst_from_text(
            "Macro 한국 GDP : 하방만큼 상방도 Economist 이정훈, CFA jhoon.lee@daishin.com"
        )
        == "이정훈"
    )
    assert (
        _extract_analyst_from_text(
            "금융/소비재팀 이혜인 Analyst hyeining.lee@samsung.com COMPANY UPDATE"
        )
        == "이혜인"
    )
    assert (
        _extract_analyst_from_text("미드/스몰캡 이채은 chaeun.lee@eugenefn.com")
        == "이채은"
    )
    assert (
        _extract_analyst_from_text(
            "유통 Analyst 이승은 seungeun.lee@example.com"
        )
        == "이승은"
    )
    assert (
        _extract_analyst_from_text(
            "[Compliance Notice] 외부 압력 없이 반영하였습니다. (담당자:문건우, 문남중)"
        )
        == "문건우"
    )
    assert (
        _extract_analyst_from_text(
            "[전략/자산배분] 김준우 책임연구원 발간일자 2026.04.27 jwkim07@iprovest.com"
        )
        == "김준우"
    )
    assert (
        _extract_analyst_from_text(
            "Compliance Notice 작성자(조준기)는 본 조사분석자료에 게재된 내용들이 본인의 의견을 정확하게 반영합니다."
        )
        == "조준기"
    )
    assert (
        _extract_analyst_from_text(
            "본 자료를 작성한 애널리스트(송선재)는 외부의 압력이나 부당한 간섭을 받지 않았습니다."
        )
        == "송선재"
    )
    assert (
        _extract_analyst_from_text(
            "글로벌 전략 Great Rebalancing 글로벌매크로팀 허재환 02)368-6176_ jaehwan.huh@eugenefn.com"
        )
        == "허재환"
    )
    assert _extract_analyst_from_text("작성자: 중동") == ""
    assert _extract_analyst_from_text("작성자: 원자재") == ""
    assert _extract_analyst_from_text("작성자: 애널리스트") == ""
    assert _extract_analyst_from_text("Macro 세계경제는 2026 test@example.com") == ""
    assert _extract_analyst_from_text("Macro 성장률은 2.3 test@example.com") == ""
    assert _extract_analyst_from_text("Macro 성장률 jhoon.lee@example.com") == ""
    assert (
        _extract_analyst_from_text(
            "미 3월 S&P 글로벌 서비스/종합 PMI 투자전략정보팀 Analyst 임정은 jungeun.lim@kbfg.com"
        )
        == "임정은"
    )
    assert (
        _extract_analyst_from_text(
            "MIRAE ASSET Equity Research younggun.kim.a@miraeasset.com"
        )
        == "김영건"
    )
    assert (
        _extract_analyst_from_text(
            "MIRAE ASSET Equity Research un.kim.a@miraeasset.com"
        )
        == "김영건"
    )


def test_extract_department_author_from_pdf_text_patterns() -> None:
    assert (
        _extract_department_author_from_text(
            "※ 작성자(SK증권 리서치센터)는 본 조사분석자료에 게재된 내용들을 작성했습니다.",
            broker="SK증권",
        )
        == "부서: SK증권 리서치센터"
    )
    assert (
        _extract_department_author_from_text(
            "본 자료에 수록된 내용은 당사 리서치센터가 신뢰할 만한 자료 및 정보를 바탕으로 작성한 것입니다.",
            broker="DS투자증권",
        )
        == "부서: DS투자증권 리서치센터"
    )
    assert (
        _extract_department_author_from_text(
            "코스피 코스닥 │ 2025. 2. 24│ 투자분석부",
            broker="IBK투자증권",
        )
        == "부서: IBK투자증권 투자분석부"
    )
    assert (
        _extract_department_author_from_text(
            "출처: Quantiwise, 언론기사 종합, IBK투자증권 │ 기간산업분석부 ・ 혁신기업분석부 │",
            broker="IBK투자증권",
        )
        == "부서: IBK투자증권 기간산업분석부·혁신기업분석부"
    )
    assert (
        _extract_department_author_from_text(
            "KB 리서치 장마감코멘트 [리서치본부 투자전략정보팀] 자료: Bloomberg",
            broker="KB증권",
        )
        == "부서: KB증권 리서치본부 투자전략정보팀"
    )
    assert (
        _extract_department_author_from_text(
            "Daishin Research Center 구분 투자의견 선호 업종",
            broker="대신증권",
        )
        == "부서: 대신증권 리서치센터"
    )
    assert _extract_department_author_from_text("Analyst 이승은 seungeun@example.com") == ""


def test_infer_default_department_author_from_context() -> None:
    assert (
        _infer_default_department_author_from_context(
            broker="유안타증권",
            category="market_info",
            text="글로벌 증시 추이 Yuanta Morning Snapshot",
        )
        == "부서: 유안타증권 리서치센터"
    )
    assert (
        _infer_default_department_author_from_context(
            broker="다올투자증권",
            category="bond_analysis",
            text="KR Market KR Bond(%,bp) YTM 1D 1W 1M",
        )
        == "부서: 다올투자증권 리서치센터"
    )
    assert (
        _infer_default_department_author_from_context(
            broker="유안타증권",
            category="bond_analysis",
            text="금융투자분석사의 확인 및 중요 공시는 Appendix 참조",
        )
        == "부서: 유안타증권 리서치센터"
    )
    assert (
        _infer_default_department_author_from_context(
            broker="유안타증권",
            category="bond_analysis",
            text="외국인이 주도하는 채권시장 흐름 Appendix",
        )
        == "부서: 유안타증권 리서치센터"
    )
    assert (
        _infer_default_department_author_from_context(
            broker="하나증권",
            category="invest_info",
            text="ETF 투자유망종목 해외주식분석실 국내 ETF 종목",
        )
        == "부서: 하나증권 해외주식분석실"
    )
    assert (
        _infer_default_department_author_from_context(
            broker="하나증권",
            category="company_analysis",
            text="본 조사자료는 고객의 투자에 정보를 제공할 목적으로 작성되었습니다.",
        )
        == ""
    )


def test_extract_broker_from_text_patterns() -> None:
    assert _extract_broker_from_text("Analyst 김선호 shkim@kirs.or .kr") == "한국IR협의회"
    assert _extract_broker_from_text("Yuanta Morning Snapshot") == "유안타증권"
    assert (
        _extract_broker_from_text(
            "기술분석보고서오에스피(368970) 작 성 기 관나이스평가정보(주) 류치선 연구원"
        )
        == "나이스평가정보"
    )


def test_detects_garbled_pdf_text() -> None:
    assert _looks_like_garbled_pdf_text("\x01 \u0b53 \u0a78 \u09d4 " * 40)
    assert not _looks_like_garbled_pdf_text("삼성전자 실적 개선과 메모리 업황 회복 전망")


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


def test_report_status_includes_facts_counts(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=3",
        pdf_url="https://stock.pstatic.net/stock-research/company/3.pdf",
        pdf_sha256="facts123",
        pdf_archived_path=".runtime/naver_reports/pdfs/fa/ct/facts123.pdf",
        title="테스트 리포트",
        company_name="테스트",
        broker="테스트증권",
        analyst="홍길동",
        symbol="123456",
        published_at="2025-01-12",
        crawled_at="2026-01-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="테스트 리포트 본문",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    repo.upsert_report_facts(
        report_id,
        {
            "rating": "BUY",
            "target_price": {"value": 50000, "currency": "KRW", "changed": "UP"},
            "valuation": {},
        },
    )

    status = repo.status()

    assert status["facts"]["total_facts"] == 1
    assert status["facts"]["target_price_count"] == 1
    assert status["facts"]["rating_count"] == 1


def test_llm_fact_refine_is_skipped_when_disabled(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    crawler = NaverSecuritiesCrawler(
        config=NaverReportCrawlerConfig(
            db_path=str(tmp_path / "reports.db"),
            llm_facts_enabled=False,
        ),
        repository=repo,
    )
    called = False

    async def fake_complete(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"ok": True, "content": "{}"}

    crawler._codex_runtime.complete = fake_complete
    facts = {"rating": "UNKNOWN", "target_price": {"value": 0}}

    result = asyncio.run(
        crawler._refine_structured_facts_via_native(facts=facts, text="본문")
    )

    assert result == facts
    assert called is False


def test_crawler_prioritizes_company_reports_before_macro_seeds(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    crawler = NaverSecuritiesCrawler(
        config=NaverReportCrawlerConfig(
            db_path=str(tmp_path / "reports.db"),
            seed_urls=[
                "https://finance.naver.com/research/market_info_list.naver",
                "https://finance.naver.com/research/invest_list.naver",
                "https://finance.naver.com/research/company_list.naver",
                "https://finance.naver.com/research/industry_list.naver",
            ],
        ),
        repository=repo,
    )

    seeds = crawler._seed_urls()

    assert seeds[0] == "https://finance.naver.com/research/company_list.naver"
    assert seeds[1] == "https://finance.naver.com/research/industry_list.naver"
    assert seeds[-1] == "https://finance.naver.com/research/market_info_list.naver"


def test_crawler_skips_pdf_already_present_in_repository(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    pdf_url = "https://stock.pstatic.net/stock-research/company/existing.pdf"
    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=1",
        pdf_url=pdf_url,
        pdf_sha256="sha-existing",
        pdf_archived_path=str(tmp_path / "existing.pdf"),
        title="삼성전자",
        company_name="삼성전자",
        broker="테스트증권",
        analyst="테스터",
        symbol="005930",
        published_at="2026-06-29",
        crawled_at="2026-06-29T00:00:00+00:00",
        content_source="pdf_extract",
        content="기존 리포트 본문",
        chunk_size=1200,
        max_chunks_per_report=2,
    )
    crawler = NaverSecuritiesCrawler(
        config=NaverReportCrawlerConfig(
            db_path=str(tmp_path / "reports.db"),
            seed_urls=["https://finance.naver.com/research/company_list.naver"],
            max_pages=1,
            request_delay_sec=0,
            max_pdfs_per_cycle=10,
        ),
        repository=repo,
    )
    fetch_bytes_calls = 0
    detail_fetch_calls = 0

    async def fake_fetch_text(_client, url: str) -> str:
        nonlocal detail_fetch_calls
        if "company_list" in url:
            return (
                '<a href="/research/company_read.naver?nid=1">'
                "삼성전자 리포트"
                "</a>"
            )
        if "company_read" in url:
            detail_fetch_calls += 1
            return (
                "<html><title>삼성전자 리포트</title>"
                f'<a href="{pdf_url}">PDF</a>'
                "</html>"
            )
        return ""

    async def fake_fetch_bytes(_client, _url: str) -> bytes:
        nonlocal fetch_bytes_calls
        fetch_bytes_calls += 1
        return b"%PDF-new"

    crawler._fetch_text = fake_fetch_text  # type: ignore[method-assign]
    crawler._fetch_bytes = fake_fetch_bytes  # type: ignore[method-assign]

    result = asyncio.run(crawler.crawl_once())

    assert result["discovered"] == 0
    assert result["skipped"] == 1
    assert result["inserted"] == 0
    assert result["errors"] == 0
    assert detail_fetch_calls == 0
    assert fetch_bytes_calls == 0
    assert repo.status()["total_reports"] == 1


def test_llm_fact_refine_updates_when_enabled(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    crawler = NaverSecuritiesCrawler(
        config=NaverReportCrawlerConfig(
            db_path=str(tmp_path / "reports.db"),
            llm_facts_enabled=True,
        ),
        repository=repo,
    )

    async def fake_complete(*_args, **_kwargs):
        return {
            "ok": True,
            "content": (
                '{"rating":"BUY","target_price":{"value":88000,'
                '"currency":"KRW","changed":"UP"},"summary_bullets":["보강"]}'
            ),
        }

    crawler._codex_runtime.complete = fake_complete
    facts = {"rating": "UNKNOWN", "target_price": {"value": 0}}

    result = asyncio.run(
        crawler._refine_structured_facts_via_native(facts=facts, text="본문")
    )

    assert result["rating"] == "BUY"
    assert result["target_price"]["value"] == 88000


def test_llm_fact_refine_uses_ephemeral_native_thread(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    crawler = NaverSecuritiesCrawler(
        config=NaverReportCrawlerConfig(
            db_path=str(tmp_path / "reports.db"),
            llm_facts_enabled=True,
        ),
        repository=repo,
    )
    seen_payload: dict = {}

    async def fake_complete(payload, **_kwargs):
        seen_payload.update(payload)
        return {
            "ok": True,
            "content": (
                '{"rating":"HOLD","target_price":{"value":0,'
                '"currency":"KRW","changed":"UNKNOWN"}}'
            ),
        }

    crawler._codex_runtime.complete = fake_complete

    asyncio.run(
        crawler._refine_structured_facts_via_native(
            facts={"rating": "UNKNOWN", "target_price": {"value": 0}},
            text="본문",
        )
    )

    assert seen_payload["native_thread_mode"] == "ephemeral"
    assert seen_payload["telemetry"] == {
        "component": "research_reports",
        "operation": "report_fact_extraction",
    }
    assert seen_payload["jue_workflow"] == {
        "workflow_id": "report_fact_extraction",
    }


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


def test_resolve_symbol_from_query_text_uses_name_or_code(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="000660",
        company_name="SK하이닉스",
        market="KOSPI",
        source="test",
        confidence=0.95,
    )

    by_name = repo.resolve_symbol_from_text("SK 하이닉스 HBM 리포트")
    by_code = repo.resolve_symbol_from_text("000660 HBM 리포트")

    assert by_name is not None
    assert by_name["symbol"] == "000660"
    assert by_name["company_name"] == "SK하이닉스"
    assert by_name["match_type"] == "company_name"
    assert by_code is not None
    assert by_code["symbol"] == "000660"
    assert by_code["match_type"] == "symbol"


def test_report_symbol_links_store_etf_mentions(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=1",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=1",
        pdf_url="https://example.com/etf.pdf",
        pdf_sha256="etfhash",
        pdf_archived_path="",
        title="ETF 전략: KODEX 200과 TIGER 200 점검",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500), TIGER 200(102110)을 중심으로 코어 ETF 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    repo.upsert_report_symbol_links(
        report_id,
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "asset_class": "etf",
                "link_type": "mention",
                "source": "text_extract",
                "confidence": 0.95,
                "evidence": "KODEX 200(069500)",
            },
            {
                "symbol": "102110",
                "name": "TIGER 200",
                "asset_class": "etf",
                "link_type": "mention",
                "source": "text_extract",
                "confidence": 0.95,
                "evidence": "TIGER 200(102110)",
            },
        ],
    )

    links = repo.list_report_symbol_links(report_id)
    assert [item["symbol"] for item in links] == ["069500", "102110"]
    assert repo.search(query="", symbol="069500", limit=5)[0]["report_id"] == report_id


def test_report_symbol_links_reject_stock_name_code_mismatch(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="삼성전자",
        market="KOSPI",
        source="pykrx",
        confidence=1.0,
    )
    report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=12",
        pdf_url="https://stock.pstatic.net/stock-research/market/mismatch.pdf",
        pdf_sha256="mismatch",
        pdf_archived_path="",
        title="오염 링크 점검",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="오염 링크 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    written = repo.upsert_report_symbol_links(
        report_id,
        [
            {
                "symbol": "005930",
                "name": "솔루엠",
                "asset_class": "stock",
                "link_type": "mention",
                "source": "text_extract",
                "confidence": 0.85,
                "evidence": "솔루엠 (005930)",
            }
        ],
    )

    assert written == 0
    assert repo.list_report_symbol_links(report_id) == []


def test_extract_report_symbol_links_skips_conflicting_name_near_code() -> None:
    links = _extract_report_symbol_links(
        "이번 자료는 솔루엠 (005930) 실적을 점검한다.",
        symbol_names={"005930": "삼성전자"},
        asset_class_by_symbol={"005930": "stock"},
        published_at="2026-05-18",
    )

    assert links == []


def test_extract_report_symbol_links_accepts_short_alias_before_code() -> None:
    links = _extract_report_symbol_links(
        "Top picks KB금융(105560)ⅠBUYⅠTP 200,000원 하나금융(086790)ⅠBUYⅠTP 157,000원",
        symbol_names={
            "105560": "KB금융",
            "086790": "하나금융지주",
        },
        asset_class_by_symbol={
            "105560": "stock",
            "086790": "stock",
        },
        published_at="2026-05-19",
    )

    by_symbol = {row["symbol"]: row for row in links}
    assert by_symbol["105560"]["name"] == "KB금융"
    assert by_symbol["086790"]["name"] == "하나금융지주"


def test_seed_etf_universe_into_symbol_directory(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    updated = repo.seed_symbol_directory(
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "market": "ETF",
                "source": "configured_etf",
            },
            {
                "symbol": "102110",
                "name": "TIGER 200",
                "market": "ETF",
                "source": "configured_etf",
            },
        ]
    )

    assert updated == 2
    assert repo.resolve_symbol_names(["069500", "102110"]) == {
        "069500": "KODEX 200",
        "102110": "TIGER 200",
    }


def test_extract_report_symbol_links_for_etfs() -> None:
    symbol_names = {
        "069500": "KODEX 200",
        "102110": "TIGER 200",
        "091160": "KODEX 반도체",
    }

    links = _extract_report_symbol_links(
        "ETF 전략: KODEX 200(069500), TIGER 200 ETF. 단순 ETF 단어는 코드가 아니다.",
        symbol_names=symbol_names,
        asset_class_by_symbol={code: "etf" for code in symbol_names},
        published_at="2026-05-18",
    )

    assert [item["symbol"] for item in links] == ["069500", "102110"]
    assert links[0]["confidence"] >= 0.9
    assert all(item["asset_class"] == "etf" for item in links)

    compact_links = _extract_report_symbol_links(
        "KODEX200 비중 확대를 검토한다.",
        symbol_names=symbol_names,
        asset_class_by_symbol={code: "etf" for code in symbol_names},
    )
    assert [item["symbol"] for item in compact_links] == ["069500"]


def test_extract_report_symbol_links_prefers_longer_etf_names() -> None:
    symbol_names = {
        "069500": "KODEX 200",
        "252670": "KODEX 200선물인버스2X",
    }

    links = _extract_report_symbol_links(
        "KODEX 200선물인버스2X 변동성을 점검한다.",
        symbol_names=symbol_names,
        asset_class_by_symbol={code: "etf" for code in symbol_names},
    )

    assert [item["symbol"] for item in links] == ["252670"]


def test_extract_report_symbol_links_avoids_short_stock_name_noise() -> None:
    symbol_names = {
        "000660": "SK하이닉스",
        "034730": "SK",
        "452400": "이닉스",
        "037370": "EG",
        "001680": "대상",
        "003550": "LG",
    }

    links = _extract_report_symbol_links(
        "SK하이닉스 (000660)는 HBM 중심으로 실적 회복이 진행 중이다.",
        symbol_names=symbol_names,
        published_at="2026-06-02",
    )

    assert [item["symbol"] for item in links] == ["000660"]

    coded_short_name_links = _extract_report_symbol_links(
        "LG(003550)는 지주회사 할인율을 점검한다.",
        symbol_names=symbol_names,
        published_at="2026-06-02",
    )

    assert [item["symbol"] for item in coded_short_name_links] == ["003550"]


def test_upsert_report_auto_links_etf_mentions(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "market": "ETF",
                "source": "configured_etf",
            }
        ]
    )

    report_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=10",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=10",
        pdf_url="https://example.com/kodex200.pdf",
        pdf_sha256="kodexhash",
        pdf_archived_path="",
        title="ETF 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500) 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    links = repo.list_report_symbol_links(report_id)
    assert links[0]["symbol"] == "069500"
    assert repo.search(query="", symbol="069500", limit=5)[0]["report_id"] == report_id


def test_list_chunks_for_rag_includes_linked_etf_metadata(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "market": "ETF",
                "source": "configured_etf",
            }
        ]
    )

    report_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=13",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=13",
        pdf_url="https://example.com/kodex200-rag.pdf",
        pdf_sha256="kodexraghash",
        pdf_archived_path="",
        title="ETF 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500) 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    chunks = repo.list_chunks_for_rag(limit=10)
    chunk = next(row for row in chunks if row["report_id"] == report_id)

    assert chunk["symbol"] == ""
    assert chunk["linked_symbols"] == "069500"
    assert chunk["linked_names"] == "KODEX 200"
    assert chunk["linked_asset_classes"] == "etf"


def test_list_chunks_for_rag_filters_by_report_updated_since(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    old_report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=old",
        pdf_url="https://example.com/old.pdf",
        pdf_sha256="oldhash",
        pdf_archived_path="",
        title="오래된 시황",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-06-01",
        crawled_at="2026-06-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="오래된 청크",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    new_report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=new",
        pdf_url="https://example.com/new.pdf",
        pdf_sha256="newhash",
        pdf_archived_path="",
        title="새로운 시황",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-06-30",
        crawled_at="2026-06-30T00:00:00+00:00",
        content_source="pdf_extract",
        content="새로운 청크",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    with repo._connect() as conn:  # noqa: SLF001 - repository timestamp fixture
        conn.execute(
            "UPDATE reports SET updated_at = ? WHERE report_id = ?",
            ("2026-06-01T00:00:00+00:00", old_report_id),
        )
        conn.execute(
            "UPDATE reports SET updated_at = ? WHERE report_id = ?",
            ("2026-06-30T10:00:00+00:00", new_report_id),
        )

    chunks = repo.list_chunks_for_rag(
        limit=10,
        updated_since="2026-06-30T09:00:00+00:00",
    )

    assert [row["report_id"] for row in chunks] == [new_report_id]


def test_search_symbol_matches_primary_and_linked_reports(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "market": "ETF",
                "source": "configured_etf",
            }
        ]
    )
    primary_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_read.naver?nid=20",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=20",
        pdf_url="https://example.com/kodex-primary.pdf",
        pdf_sha256="kodexprimaryhash",
        pdf_archived_path="",
        title="KODEX 200 리포트",
        company_name="KODEX 200",
        broker="테스트증권",
        analyst="",
        symbol="069500",
        published_at="2026-05-19",
        crawled_at="2026-05-19T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200 직접 분석",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    linked_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=21",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=21",
        pdf_url="https://example.com/kodex-linked.pdf",
        pdf_sha256="kodexlinkedhash",
        pdf_archived_path="",
        title="ETF 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500) 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    rows = repo.search(query="", symbol="069500", limit=10)

    assert {row["report_id"] for row in rows} == {primary_id, linked_id}


def test_upsert_report_replaces_stale_generated_symbol_links(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "market": "ETF",
                "source": "configured_etf",
            },
            {
                "symbol": "102110",
                "name": "TIGER 200",
                "market": "ETF",
                "source": "configured_etf",
            },
            {
                "symbol": "091160",
                "name": "KODEX 반도체",
                "market": "ETF",
                "source": "configured_etf",
            },
        ]
    )

    report_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=12",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=12",
        pdf_url="https://example.com/relinked-etf.pdf",
        pdf_sha256="relinkedhash",
        pdf_archived_path="",
        title="ETF 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500) 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    repo.upsert_report_symbol_links(
        report_id,
        [
            {
                "symbol": "091160",
                "name": "KODEX 반도체",
                "asset_class": "etf",
                "link_type": "manual",
                "source": "manual",
                "confidence": 1.0,
                "evidence": "사용자 지정",
            }
        ],
    )

    repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=12",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=12",
        pdf_url="https://example.com/relinked-etf.pdf",
        pdf_sha256="relinkedhash",
        pdf_archived_path="",
        title="ETF 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="TIGER 200(102110) 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    links = repo.list_report_symbol_links(report_id)

    assert [item["symbol"] for item in links] == ["091160", "102110"]
    assert {item["symbol"]: item["source"] for item in links} == {
        "091160": "manual",
        "102110": "text_extract",
    }
    assert repo.search(query="", symbol="069500", limit=5) == []
    assert repo.search(query="", symbol="102110", limit=5)[0]["report_id"] == report_id


def test_backfill_report_symbol_links_is_idempotent_for_existing_etf_report(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "market": "ETF",
                "source": "configured_etf",
            }
        ]
    )
    report_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=11",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=11",
        pdf_url="https://example.com/backfill-kodex200.pdf",
        pdf_sha256="backfillkodexhash",
        pdf_archived_path="",
        title="ETF 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500) ETF 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    first = repo.backfill_report_symbol_links()
    second = repo.backfill_report_symbol_links()
    links = repo.list_report_symbol_links(report_id)

    assert first["updated_reports"] == 1
    assert second["updated_reports"] == 1
    assert [item["symbol"] for item in links] == ["069500"]
    assert repo.status()["etf_link_count"] == 1


def test_backfill_report_symbol_links_supports_stock_industry_reports(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {
                "symbol": "105560",
                "name": "KB금융",
                "market": "KOSPI",
                "source": "krx",
            },
            {
                "symbol": "086790",
                "name": "하나금융지주",
                "market": "KOSPI",
                "source": "krx",
            },
        ]
    )
    report_id = repo.upsert_report(
        category="industry_analysis",
        source_url="https://finance.naver.com/research/industry_read.naver?nid=22",
        detail_url="https://finance.naver.com/research/industry_read.naver?nid=22",
        pdf_url="https://example.com/backfill-bank-top-picks.pdf",
        pdf_sha256="backfillbanktoppickshash",
        pdf_archived_path="",
        title="Top picks KB금융(105560) 하나금융(086790) 은행 업종 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-19",
        crawled_at="2026-05-19T00:00:00+00:00",
        content_source="pdf_extract",
        content="은행 업종 Top picks KB금융(105560)과 하나금융(086790)을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    with repo._connect() as conn:
        conn.execute("DELETE FROM report_symbol_links WHERE report_id = ?", (report_id,))

    backfill = repo.backfill_report_symbol_links(asset_class="stock")
    links = repo.list_report_symbol_links(report_id)

    assert backfill["updated_reports"] == 1
    assert [item["symbol"] for item in links] == ["105560", "086790"]


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


def test_upsert_report_prefers_exact_title_identity_over_stale_directory(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="POSCO홀딩스",
        source="naver_reports",
        confidence=0.8,
    )

    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=12",
        pdf_url="https://stock.pstatic.net/stock-research/company/12.pdf",
        pdf_sha256="abc012",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc012.pdf",
        title="삼성전자(005930) HBM 실적 점검",
        company_name="POSCO홀딩스",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2026-05-06",
        crawled_at="2026-05-06T00:00:00+00:00",
        content_source="pdf_extract",
        content="삼성전자(005930) 목표주가 상향과 메모리 가격 상승",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["symbol"] == "005930"
    assert detail["company_name"] == "삼성전자"
    assert repo.get_symbol_name("005930") == "삼성전자"


def test_upsert_report_corrects_wrong_supplied_symbol_from_title_identity(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="삼성전자",
        market="KOSPI",
        source="pykrx",
        confidence=1.0,
    )
    repo.upsert_symbol_directory(
        symbol="020000",
        company_name="한섬",
        market="KOSPI",
        source="pykrx",
        confidence=1.0,
    )

    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=121",
        pdf_url="https://stock.pstatic.net/stock-research/company/121.pdf",
        pdf_sha256="abc121",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc121.pdf",
        title="한섬 (020000) 소비 회복을 기다리는 구간",
        company_name="한섬",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2026-05-06",
        crawled_at="2026-05-06T00:00:00+00:00",
        content_source="pdf_extract",
        content="한섬 (020000) 의류 업황 회복과 재고 정상화를 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    detail = repo.get_report(report_id)

    assert detail is not None
    assert detail["symbol"] == "020000"
    assert detail["company_name"] == "한섬"
    assert repo.get_symbol_name("005930") == "삼성전자"
    assert repo.get_symbol_name("020000") == "한섬"


def test_symbol_directory_authoritative_source_repairs_polluted_verified_name(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="비나텍",
        source="metadata_repair",
        confidence=1.0,
    )

    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="삼성전자",
        market="KOSPI",
        source="pykrx",
        confidence=1.0,
    )

    assert repo.get_symbol_name("005930") == "삼성전자"


def test_repair_metadata_quality_replaces_polluted_naver_directory_name_at_same_confidence(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="064350",
        company_name="것들",
        source="naver_reports",
        confidence=1.0,
    )

    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=64350",
        pdf_url="https://stock.pstatic.net/stock-research/company/64350.pdf",
        pdf_sha256="abc64350",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc64350.pdf",
        title="(064350) 현대로템 2026.06.01 창원 공장 투어에서 보고 온 것들",
        company_name="것들",
        broker="유진투자증권",
        analyst="홍길동",
        symbol="064350",
        published_at="2026-06-01",
        crawled_at="2026-06-03T00:05:05+00:00",
        content_source="pdf_extract",
        content="(064350) 현대로템 2026.06.01 창원 공장 투어에서 보고 온 것들 투자의견 BUY",
        chunk_size=2000,
        max_chunks_per_report=2,
    )

    repo.repair_metadata_quality()

    assert repo.get_symbol_name("064350") == "현대로템"


def test_upsert_report_keeps_authoritative_krx_directory_name(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="삼성전자",
        market="KOSPI",
        source="pykrx",
        confidence=1.0,
    )

    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=13",
        pdf_url="https://stock.pstatic.net/stock-research/company/13.pdf",
        pdf_sha256="abc013",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc013.pdf",
        title="삼성전자(005930) HBM 실적 점검",
        company_name="POSCO홀딩스",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2026-05-06",
        crawled_at="2026-05-06T00:00:00+00:00",
        content_source="pdf_extract",
        content="삼성전자(005930) 목표주가 상향과 메모리 가격 상승",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    assert repo.get_symbol_name("005930") == "삼성전자"


def test_search_supports_broker_analyst_and_date_filters(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=10",
        pdf_url="https://stock.pstatic.net/stock-research/company/10.pdf",
        pdf_sha256="abc010",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c0/abc010.pdf",
        title="반도체 리포트",
        company_name="삼성전자",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2026-01-10",
        crawled_at="2026-01-10T01:00:00+00:00",
        content_source="pdf_extract",
        content="반도체 사이클 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=11",
        pdf_url="https://stock.pstatic.net/stock-research/company/11.pdf",
        pdf_sha256="abc011",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc011.pdf",
        title="조선 리포트",
        company_name="현대중공업",
        broker="다른증권",
        analyst="임꺽정",
        symbol="329180",
        published_at="2026-01-11",
        crawled_at="2026-01-11T01:00:00+00:00",
        content_source="pdf_extract",
        content="조선 업황 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    rows = repo.search(
        query="",
        symbol="005930",
        category="company_analysis",
        broker="테스트",
        analyst="홍길",
        date_from="2026-01-01",
        date_to="2026-01-31",
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "005930"
    assert rows[0]["broker"] == "테스트증권"


def test_status_reports_metadata_quality_guardrails(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    dirty_report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=21",
        pdf_url="https://stock.pstatic.net/stock-research/company/21.pdf",
        pdf_sha256="abc021",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c2/abc021.pdf",
        title="삼성전자 HTML 이름",
        company_name="<b>삼성전자</b>",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2026-04-01",
        crawled_at="2026-04-01T00:00:00+00:00",
        content_source="pdf_extract",
        content="이름 정합성 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    with repo._connect() as conn:
        conn.execute(
            """
            UPDATE reports
            SET title = ?, company_name = ?
            WHERE report_id = ?
            """,
            (
                '<img src="https://ssl.pstatic.net/static/nfinance/btn_report.gif" alt="리포트 보기">',
                "<b>삼성전자</b>",
                dirty_report_id,
            ),
        )
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="삼성전자",
        market="KOSPI",
        source="test",
        confidence=1.0,
    )
    repo.upsert_report(
        category="unknown",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=22",
        pdf_url="https://stock.pstatic.net/stock-research/company/22.pdf",
        pdf_sha256="abc022",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c2/abc022.pdf",
        title="메타데이터 누락",
        company_name="",
        broker="",
        analyst="",
        symbol="",
        published_at="2026-04-02",
        crawled_at="2026-04-02T00:00:00+00:00",
        content_source="pdf_extract",
        content="메타데이터 누락 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    status = repo.status()
    quality = status["quality"]

    assert quality["html_title_count"] == 1
    assert quality["html_company_name_count"] == 1
    assert quality["missing_company_name_count"] == 0
    assert quality["missing_symbol_count"] == 0
    assert quality["missing_broker_count"] == 1
    assert quality["missing_analyst_count"] == 1
    assert quality["unknown_category_count"] == 1
    assert quality["symbol_directory_drift_count"] == 1
    assert quality["identity_suspect_count"] == 1
    assert quality["identity_drift_samples"][0]["symbol"] == "005930"


def test_repair_metadata_quality_cleans_html_and_date_symbols(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=30",
        pdf_url="https://stock.pstatic.net/stock-research/market/30.pdf",
        pdf_sha256="abc030",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c3/abc030.pdf",
        title='<img src="https://ssl.pstatic.net/static/nfinance/btn_report.gif" alt="리포트 보기">',
        company_name='<img src="https://ssl.pstatic.net/static/nfinance/btn_report.gif" alt="리포트 보기">',
        broker="테스트증권",
        analyst="",
        symbol="260504",
        published_at="2026-05-04",
        crawled_at="2026-05-04T00:00:00+00:00",
        content_source="pdf_extract",
        content="국내 증시 Comment 반도체와 전력기기 중심으로 수급이 개선되었습니다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    with repo._connect() as conn:
        conn.execute(
            """
            UPDATE reports
            SET title = ?, company_name = ?, symbol = ?
            WHERE report_id = ?
            """,
            (
                '<img src="https://ssl.pstatic.net/static/nfinance/btn_report.gif" alt="리포트 보기">',
                '<img src="https://ssl.pstatic.net/static/nfinance/btn_report.gif" alt="리포트 보기">',
                "260504",
                report_id,
            ),
        )

    repair = repo.repair_metadata_quality()
    detail = repo.get_report(report_id)

    assert repair["updated_reports"] == 1
    assert detail is not None
    assert detail["symbol"] == ""
    assert detail["company_name"] == ""
    assert "국내 증시 Comment" in detail["title"]
    assert repo.status()["quality"]["html_title_count"] == 0


def test_repair_metadata_quality_backfills_company_identity_from_content(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="247540",
        company_name="에코프로비엠",
        market="KOSDAQ",
        source="test",
        confidence=1.0,
    )
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=40",
        pdf_url="https://stock.pstatic.net/stock-research/company/40.pdf",
        pdf_sha256="abc040",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c4/abc040.pdf",
        title="Trading Buy 실적 리뷰",
        company_name="",
        broker="유진투자증권",
        analyst="",
        symbol="",
        published_at="2026-04-30",
        crawled_at="2026-04-30T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "(247540. KQ) 에코프로비엠 2026.04.29 그린산업/ESG "
            "한병화 bhh1026@eugenefn.com 유럽 전기차향 물량 증가 시그널 긍정적"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )
    with repo._connect() as conn:
        conn.execute(
            """
            UPDATE reports
            SET symbol = '', company_name = '', analyst = ''
            WHERE report_id = ?
            """,
            (report_id,),
        )

    repair = repo.repair_metadata_quality()
    detail = repo.get_report(report_id)

    assert repair["backfilled_symbols"] == 1
    assert repair["backfilled_company_names"] == 1
    assert repair["backfilled_analysts"] == 1
    assert detail is not None
    assert detail["symbol"] == "247540"
    assert detail["company_name"] == "에코프로비엠"
    assert detail["analyst"] == "한병화"


def test_repair_metadata_quality_corrects_stale_company_identity(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_symbol_directory(
        symbol="005930",
        company_name="삼성전자",
        market="KOSPI",
        source="test",
        confidence=1.0,
    )
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=41",
        pdf_url="https://stock.pstatic.net/stock-research/company/41.pdf",
        pdf_sha256="abc041",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c4/abc041.pdf",
        title="포스코인터내셔널 (047050) 1Q26 Review: 순조로운 출발",
        company_name="삼성전자",
        broker="키움증권",
        analyst="조재원",
        symbol="005930",
        published_at="2026-05-04",
        crawled_at="2026-05-04T00:00:00+00:00",
        content_source="pdf_extract",
        content="포스코인터내셔널 (047050) 에너지와 무역 부문 이익 개선",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    with repo._connect() as conn:
        conn.execute(
            """
            UPDATE reports
            SET symbol = ?, company_name = ?
            WHERE report_id = ?
            """,
            ("005930", "삼성전자", report_id),
        )

    repair = repo.repair_metadata_quality()
    detail = repo.get_report(report_id)

    assert repair["corrected_symbols"] == 1
    assert repair["corrected_company_names"] == 1
    assert detail is not None
    assert detail["symbol"] == "047050"
    assert detail["company_name"] == "포스코인터내셔널"
    assert repo.get_symbol_name("047050") == "포스코인터내셔널"


def test_repair_metadata_quality_corrects_stale_same_symbol_company_name(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=411",
        pdf_url="https://stock.pstatic.net/stock-research/company/411.pdf",
        pdf_sha256="abc411",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c4/abc411.pdf",
        title="삼성전자(005930) HBM 실적 점검",
        company_name="삼성전자",
        broker="테스트증권",
        analyst="홍길동",
        symbol="005930",
        published_at="2026-05-06",
        crawled_at="2026-05-06T00:00:00+00:00",
        content_source="pdf_extract",
        content="삼성전자(005930) HBM 수요와 메모리 가격 상승",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    with repo._connect() as conn:
        conn.execute(
            "UPDATE reports SET company_name = ? WHERE report_id = ?",
            ("POSCO홀딩스", report_id),
        )
        conn.execute(
            """
            INSERT INTO symbol_directory (
                symbol, company_name, market, status, source, confidence,
                first_seen_at, updated_at, last_verified_at
            )
            VALUES (?, ?, '', 'active', 'naver_reports', 0.8, '', '', '')
            ON CONFLICT(symbol) DO UPDATE SET
                company_name = excluded.company_name,
                source = excluded.source,
                confidence = excluded.confidence
            """,
            ("005930", "POSCO홀딩스"),
        )

    repair = repo.repair_metadata_quality()
    detail = repo.get_report(report_id)

    assert repair["corrected_company_names"] >= 1
    assert detail is not None
    assert detail["symbol"] == "005930"
    assert detail["company_name"] == "삼성전자"
    assert repo.get_symbol_name("005930") == "삼성전자"


def test_repair_metadata_quality_backfills_broker_and_technical_report_analyst(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=42",
        pdf_url="https://stock.pstatic.net/stock-research/company/42.pdf",
        pdf_sha256="abc042",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c4/abc042.pdf",
        title="기술분석보고서율촌(146060)경기관련소비재",
        company_name="",
        broker="",
        analyst="",
        symbol="",
        published_at="2026-05-04",
        crawled_at="2026-05-04T00:00:00+00:00",
        content_source="pdf_extract",
        content=(
            "기술분석보고서율촌(146060)경기관련소비재 "
            "작 성 기 관서울평가정보(주) 박진희 책임작 성 자"
        ),
        chunk_size=200,
        max_chunks_per_report=10,
    )
    with repo._connect() as conn:
        conn.execute(
            """
            UPDATE reports
            SET symbol = '', company_name = '', broker = '', analyst = ''
            WHERE report_id = ?
            """,
            (report_id,),
        )

    repair = repo.repair_metadata_quality()
    detail = repo.get_report(report_id)

    assert repair["backfilled_symbols"] == 1
    assert repair["backfilled_company_names"] == 1
    assert repair["backfilled_brokers"] == 1
    assert repair["backfilled_analysts"] == 1
    assert detail is not None
    assert detail["symbol"] == "146060"
    assert detail["company_name"] == "율촌"
    assert detail["broker"] == "서울평가정보"
    assert detail["analyst"] == "박진희"


def test_repair_metadata_quality_corrects_strong_broker_mismatch(
    tmp_path: Path,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="market_info",
        source_url="https://finance.naver.com/research/market_info_list.naver",
        detail_url="https://finance.naver.com/research/market_info_read.naver?nid=42",
        pdf_url="https://stock.pstatic.net/stock-research/market/42.pdf",
        pdf_sha256="abc043",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c4/abc043.pdf",
        title="Yuanta Morning Snapshot (2026.03.03)",
        company_name="",
        broker="유진투자증권",
        analyst="",
        symbol="",
        published_at="2026-03-03",
        crawled_at="2026-03-03T00:00:00+00:00",
        content_source="pdf_extract",
        content="Yuanta Morning Snapshot 글로벌 증시 추이와 시장 점검",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    repair = repo.repair_metadata_quality()
    detail = repo.get_report(report_id)

    assert repair["corrected_brokers"] == 1
    assert repair["backfilled_analysts"] == 1
    assert detail is not None
    assert detail["broker"] == "유안타증권"
    assert detail["analyst"] == "부서: 유안타증권 리서치센터"
