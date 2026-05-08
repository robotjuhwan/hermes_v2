from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

from tradecraft.services.naver_reports import (
    NaverReportCrawlerConfig,
    NaverReportRepository,
    NaverSecuritiesCrawler,
    _clean_company_name,
    _extract_analyst_from_text,
    _extract_broker_from_text,
    _extract_company_symbol_from_text,
    _extract_department_author_from_text,
    _infer_default_department_author_from_context,
    _is_research_detail_url,
    _looks_like_garbled_pdf_text,
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


def test_refresh_symbol_directory_uses_existing_symbol_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.upsert_report(
        category="company_analysis",
        source_url="https://finance.naver.com/research/company_list.naver",
        detail_url="https://finance.naver.com/research/company_read.naver?nid=1",
        pdf_url="https://stock.pstatic.net/stock-research/company/1.pdf",
        pdf_sha256="abc123",
        pdf_archived_path=".runtime/naver_reports/pdfs/ab/c1/abc123.pdf",
        title="(100120,KQ) 뷰웍스 1Q26P Review",
        company_name="",
        broker="테스트증권",
        analyst="홍길동",
        symbol="100120",
        published_at="2026-05-06",
        crawled_at="2026-05-06T00:00:00+00:00",
        content_source="pdf_extract",
        content="뷰웍스 실적 개선 전망",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    class _FakeStock:
        @staticmethod
        def get_market_ticker_list(as_of: str, market: str = "") -> list[str]:
            _ = (as_of, market)
            return []

        @staticmethod
        def get_etf_ticker_list(as_of: str) -> list[str]:
            _ = as_of
            return []

        @staticmethod
        def get_etn_ticker_list(as_of: str) -> list[str]:
            _ = as_of
            return []

        @staticmethod
        def get_market_ticker_name(code: str) -> str:
            return {"100120": "뷰웍스"}.get(code, "")

    monkeypatch.setitem(sys.modules, "pykrx", types.SimpleNamespace(stock=_FakeStock))

    result = repo.refresh_symbol_directory_from_krx(as_of="2026-05-06")

    assert result["ok"] is True
    assert result["updated"] == 1
    assert repo.resolve_symbol_names(["100120"]) == {"100120": "뷰웍스"}


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
            llm_bridge_url="https://example.com/v1/chat",
            llm_facts_enabled=False,
        ),
        repository=repo,
    )
    called = False

    async def fake_complete(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"ok": True, "content": "{}"}

    crawler._llm_bridge.complete = fake_complete
    facts = {"rating": "UNKNOWN", "target_price": {"value": 0}}

    result = asyncio.run(
        crawler._refine_structured_facts_via_bridge(facts=facts, text="본문")
    )

    assert result == facts
    assert called is False


def test_llm_fact_refine_updates_when_enabled(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    crawler = NaverSecuritiesCrawler(
        config=NaverReportCrawlerConfig(
            db_path=str(tmp_path / "reports.db"),
            llm_bridge_url="https://example.com/v1/chat",
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

    crawler._llm_bridge.complete = fake_complete
    facts = {"rating": "UNKNOWN", "target_price": {"value": 0}}

    result = asyncio.run(
        crawler._refine_structured_facts_via_bridge(facts=facts, text="본문")
    )

    assert result["rating"] == "BUY"
    assert result["target_price"]["value"] == 88000


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
