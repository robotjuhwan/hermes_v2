from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tradecraft import main
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
    StrategyInsightRepository,
    StrategyInsightCollector,
    _extract_report_subject,
    _parse_js_object_array,
    _sesiban_leading_payload_to_signals,
    _whale_major_rows_to_signals,
    classify_strategy_intent,
)


class _FakeReportRepository:
    def search(
        self,
        query: str,
        symbol: str = "",
        category: str = "",
        limit: int = 10,
        broker: str = "",
        analyst: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        _ = (query, symbol, limit, broker, analyst, date_from, date_to)
        if category != "company_analysis":
            return []
        return [
            {
                "report_id": 101,
                "category": "company_analysis",
                "title": "삼성전자(005930) HBM 실적 점검",
                "company_name": "삼성전자",
                "broker": "테스트증권",
                "symbol": "005930",
                "published_at": "2026-04-30",
                "snippet": "HBM과 AI 서버 수요로 실적 개선",
            }
        ]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) != 101:
            return None
        return {
            "content": (
                "삼성전자(005930) BUY. HBM, AI 서버, 메모리 가격 상승으로 "
                "실적 개선과 목표주가 상향 근거가 있다."
            )
        }

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) != 101:
            return None
        return {
            "rating": "BUY",
            "target_price": {"value": 105000, "currency": "KRW", "changed": "UP"},
            "summary_bullets": ["HBM과 AI 서버 수요가 실적 개선을 이끈다"],
            "risks": ["단기 주가 변동성"],
            "evidence_quotes": [{"page": 3, "text": "목표주가 상향"}],
        }

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        return {"005930": "삼성전자"} if "005930" in symbols else {}


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
    ) -> list[dict[str, Any]]:
        _ = (query, symbol, limit, broker, doc_id, date_from, date_to)
        return [
            {
                "report_id": 101,
                "symbol": "005930",
                "broker": "테스트증권",
                "published_at": "2026-04-30",
                "page_start": 3,
                "content": "HBM 수요와 메모리 가격 상승이 이익 상향 근거다.",
            }
        ]


class _NoisyRAGStore:
    def query(
        self,
        query: str,
        symbol: str = "",
        limit: int = 8,
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        _ = (query, symbol, limit, broker, doc_id, date_from, date_to)
        return [
            {
                "report_id": 201,
                "symbol": "635805",
                "broker": "노이즈증권",
                "published_at": "2026-04-30",
                "page_start": 1,
                "content": '<img src="https://ssl.pstatic.net/static/nfinance/btn_report.gif" alt="리포트 보기">',
            },
            {
                "report_id": 202,
                "symbol": "635805",
                "broker": "노이즈증권",
                "published_at": "2026-04-30",
                "page_start": 2,
                "content": "가처분 소득 대비 비율 (우)",
            },
            {
                "report_id": 203,
                "symbol": "005930",
                "broker": "테스트증권",
                "published_at": "2026-04-30",
                "page_start": 3,
                "content": "삼성전자 HBM 수요와 메모리 가격 상승이 이익 상향과 목표주가 상향의 핵심 근거다.",
            },
        ]


class _LowTargetReportRepository(_FakeReportRepository):
    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        facts = super().get_report_facts(report_id)
        if not isinstance(facts, dict):
            return facts
        return {
            **facts,
            "target_price": {"value": 995, "currency": "KRW", "changed": "UP"},
        }


class _DirectoryNameReportRepository(_FakeReportRepository):
    def search(
        self,
        query: str,
        symbol: str = "",
        category: str = "",
        limit: int = 10,
        broker: str = "",
        analyst: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        _ = (query, symbol, limit, broker, analyst, date_from, date_to)
        if category != "company_analysis":
            return []
        return [
            {
                "report_id": 101,
                "category": "company_analysis",
                "title": "허민호 Analyst LS(006260)",
                "company_name": "리포트 보기",
                "broker": "테스트증권",
                "symbol": "072080",
                "published_at": "2026-04-30",
                "snippet": "LS(006260) 지주회사 재평가",
            }
        ]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) != 101:
            return None
        return {
            "content": (
                "Earnings Preview LS (006260) 이제 지주회사 보다는 사업 회사로 재평가해야 할 때 "
                "허민호 minho.hur@broker.com 투자의견 BUY 목표주가 460,000원 상향"
            )
        }

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        return {"006260": "LS"} if "006260" in symbols else {}


class _BoilerplateDirectoryReportRepository(_FakeReportRepository):
    def search(
        self,
        query: str,
        symbol: str = "",
        category: str = "",
        limit: int = 10,
        broker: str = "",
        analyst: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        _ = (query, symbol, limit, broker, analyst, date_from, date_to)
        if category != "company_analysis":
            return []
        return [
            {
                "report_id": 101,
                "category": "company_analysis",
                "title": "2026.05.06 PI첨단소재 (178920) 수익성 개선 가속화",
                "company_name": "코스콤 국내 시세 정보",
                "broker": "테스트증권",
                "symbol": "178920",
                "published_at": "2026-05-06",
                "snippet": "PI첨단소재(178920) 판가 상승과 믹스 개선",
            }
        ]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) != 101:
            return None
        return {
            "content": (
                "PI첨단소재(178920) BUY. 판가 상승과 고부가가치 제품 비중 상승으로 "
                "영업이익 개선과 목표주가 상향 근거가 있다."
            )
        }

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        return {"178920": "코스콤 국내 시세 정보"} if "178920" in symbols else {}


class _ExternalOnlyReportRepository(_FakeReportRepository):
    def search(
        self,
        query: str,
        symbol: str = "",
        category: str = "",
        limit: int = 10,
        broker: str = "",
        analyst: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        _ = (query, symbol, category, limit, broker, analyst, date_from, date_to)
        return []

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        _ = report_id
        return None

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        _ = report_id
        return None

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        names = {
            "000660": "SK하이닉스",
            "402340": "SK스퀘어",
        }
        return {symbol: names[symbol] for symbol in symbols if symbol in names}


class _FakeBridge:
    ready = False
    resolved_model = "gpt-5.5"

    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        _ = (payload, timeout_ms)
        return {"ok": False}


class _FakeFundamentalsRepository:
    def latest(self, symbol: str) -> dict[str, Any] | None:
        if symbol != "005930":
            return None
        return {
            "status": "ok",
            "symbol": "005930",
            "name": "삼성전자",
            "valuation": {
                "price": 80_000,
                "market_cap_krw": 470_000_000_000_000,
                "per": 7.0,
                "eps": 40_286,
                "pbr": 0.9,
                "bps": 63_997,
                "dividend_yield_pct": 1.4,
                "industry_per": 28.9,
                "as_of": "2026-05-06",
                "crawled_at": "2026-05-06T00:00:00+00:00",
            },
            "score": {
                "undervalued_score": 82,
                "overvalued_risk": 18,
                "quality_score": 72,
                "growth_score": 16,
                "relative_per_discount_pct": 75.78,
                "label": "undervalued",
                "reasons": ["업종 PER 대비 75.8% 낮음", "PBR 0.90배"],
                "risks": [],
                "scored_at": "2026-05-06T00:01:00+00:00",
            },
            "financials": [],
        }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def test_strategy_intent_routes_candidate_questions() -> None:
    payload = classify_strategy_intent("다음주 월요일 담으면 좋을 후보 정리해줘")

    assert payload["intent"] == "candidate_scout"
    assert payload["route"] == "strategy_candidates"


def test_extract_report_subject_filters_date_like_symbols_and_noise_names() -> None:
    symbol, name, exact = _extract_report_subject(
        {
            "symbol": "260403",
            "company_name": "리포트 보기",
            "title": "2026.04.03 시장 전략",
        },
        "거시 리포트 2026.04.03",
    )

    assert symbol == ""
    assert name == ""
    assert exact is False

    symbol, name, exact = _extract_report_subject(
        {
            "symbol": "005930",
            "company_name": "리포트 보기",
            "title": "삼성전자(005930) HBM 실적 점검",
        },
        "삼성전자(005930) BUY",
    )

    assert symbol == "005930"
    assert name == "삼성전자"
    assert exact is True

    symbol, name, exact = _extract_report_subject(
        {
            "symbol": "010280",
            "company_name": "absmiddle",
            "title": "리포트 보기",
        },
        "아이티센엔텍(010280) 클라우드 수주 확대 BUY",
    )

    assert symbol == "010280"
    assert name == "아이티센엔텍"
    assert exact is True

    symbol, name, exact = _extract_report_subject(
        {
            "symbol": "281740",
            "company_name": "레이크머티리얼즈",
            "title": "소재(281740) 실적 점검",
        },
        "소재(281740) 전구체 성장",
    )

    assert symbol == "281740"
    assert name == "레이크머티리얼즈"
    assert exact is True

    symbol, name, exact = _extract_report_subject(
        {
            "symbol": "006260",
            "company_name": "LS",
            "title": "minho.hur Analyst LS(006260)",
        },
        "minho.hur@broker.com LS(006260) 구리 가격과 전력망 수혜",
    )

    assert symbol == "006260"
    assert name == "LS"
    assert exact is True

    symbol, name, exact = _extract_report_subject(
        {
            "symbol": "178920",
            "company_name": "코스콤 국내 시세 정보",
            "title": "2026.05.06 PI첨단소재 (178920) 수익성 개선 가속화",
        },
        "PI첨단소재(178920) BUY",
    )

    assert symbol == "178920"
    assert name == "PI첨단소재"
    assert exact is True


def test_strategy_engine_builds_candidates_from_reports_and_external_sources(
    tmp_path: Path,
) -> None:
    whale_path = tmp_path / "whale.jsonl"
    sesiban_path = tmp_path / "sesiban.jsonl"
    decision_path = tmp_path / "decisions.jsonl"
    _write_jsonl(
        whale_path,
        [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "type": "large_holder_change",
                "direction": "positive",
                "strength": 82,
                "summary": "큰손 포지션 변화가 우호적으로 관측됨",
                "tags": ["whale", "position"],
            }
        ],
    )
    _write_jsonl(
        sesiban_path,
        [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "type": "after_close_flow",
                "direction": "positive",
                "strength": 76,
                "summary": "장마감 수급 후보군에 포함",
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        fundamentals_repository=_FakeFundamentalsRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(sesiban_path),
            decision_log_path=str(decision_path),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed={"count": 1, "items": [{"picks": ["005930"], "title": "AI 반도체"}]},
        limit=5,
    )

    assert payload["status"] == "ok"
    assert payload["model"] == "gpt-5.5"
    assert payload["score_method_version"] == "v2"
    assert payload["candidates"][0]["symbol"] == "005930"
    assert payload["candidates"][0]["stance"] == "watch"
    assert payload["candidates"][0]["score_components"]["report"] > 0
    assert payload["candidates"][0]["score_components"]["research"] > 0
    assert payload["candidates"][0]["score_components"]["whale"] == 82
    assert payload["candidates"][0]["score_components"]["after_close"] == 76
    assert payload["candidates"][0]["score_components"]["valuation"] == 82
    assert payload["candidates"][0]["valuation"]["label"] == "undervalued"
    assert payload["candidates"][0]["score"] == payload["candidates"][0]["suitability"]["balanced"]["score"]
    assert payload["candidates"][0]["suitability"]["short_term"]["grade"] == "A"
    assert payload["candidates"][0]["suitability"]["mid_term"]["score"] > 65
    assert payload["candidates"][0]["suitability"]["long_term"]["score"] > 65
    assert payload["candidates"][0]["data_coverage"]["has_valuation"] is True
    assert payload["candidates"][0]["identity_status"]["status"] == "ok"
    assert payload["candidates"][0]["data_warnings"] == []
    assert payload["candidates"][0]["risk_score"] == payload["candidates"][0]["score_components"]["risk_score"]
    assert any("밸류에이션" in row for row in payload["candidates"][0]["reasons"])
    assert "whale_insight" in payload["candidates"][0]["sources"]
    assert "after_close_330" in payload["candidates"][0]["sources"]
    assert any(row["source_id"] == "whale_insight" and row["status"] == "ok" for row in payload["sources"])


def test_strategy_engine_weights_report_only_candidates_below_raw_cap(tmp_path: Path) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )

    assert payload["candidates"][0]["symbol"] == "005930"
    assert 30 <= payload["candidates"][0]["score"] < 65
    assert payload["candidates"][0]["score"] == payload["candidates"][0]["suitability"]["balanced"]["score"]
    assert payload["candidates"][0]["suitability"]["short_term"]["score"] > payload["candidates"][0]["suitability"]["long_term"]["score"]
    assert payload["candidates"][0]["data_coverage"]["has_valuation"] is False
    assert "밸류 미수집" in payload["candidates"][0]["data_warnings"]
    assert "소스 1개" in payload["candidates"][0]["data_warnings"]
    assert payload["candidates"][0]["risk_score"] < 50
    assert payload["candidates"][0]["confidence"] <= 62
    assert payload["candidates"][0]["score_components"]["recency"] > 0
    assert payload["candidates"][0]["score_components"]["evidence"] > 0


def test_strategy_engine_does_not_overvalue_flow_only_candidates(tmp_path: Path) -> None:
    sesiban_path = tmp_path / "sesiban.jsonl"
    _write_jsonl(
        sesiban_path,
        [
            {
                "schema_version": 1,
                "source_id": "after_close_330",
                "symbol": "402340",
                "name": "SK스퀘어",
                "signal_type": "sector_treemap",
                "direction": "positive",
                "strength": 92,
                "summary": "세시반 선도 섹터 상위",
                "as_of": "2026-05-06T15:30:00+09:00",
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(sesiban_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "402340")

    assert row["score"] < 30


def test_strategy_engine_filters_noisy_rag_context(tmp_path: Path) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_NoisyRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 HBM 관심 후보",
        research_feed=None,
        limit=5,
    )

    rag_text = "\n".join(row["content"] for row in payload["rag_context"])
    assert "HBM 수요" in rag_text
    assert "btn_report" not in rag_text
    assert "가처분 소득 대비 비율" not in rag_text
    assert payload["rag_context"][0]["quality_score"] > 0


def test_strategy_engine_does_not_score_suspicious_low_target_price(tmp_path: Path) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_LowTargetReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )

    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "005930")
    reasons = "\n".join(row["reasons"])
    checks = "\n".join(row["checks"])
    assert "995 KRW" not in reasons
    assert "목표주가 단위/OCR 확인 필요" in checks


def test_strategy_engine_prefers_symbol_directory_names(tmp_path: Path) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_DirectoryNameReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )

    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "006260")
    assert row["name"] == "LS"


def test_strategy_engine_ignores_boilerplate_symbol_directory_names(tmp_path: Path) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_BoilerplateDirectoryReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )

    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "178920")
    assert row["name"] == "PI첨단소재"


def test_strategy_engine_resolves_external_only_candidate_names(tmp_path: Path) -> None:
    sesiban_path = tmp_path / "sesiban.jsonl"
    _write_jsonl(
        sesiban_path,
        [
            {
                "schema_version": 1,
                "source_id": "after_close_330",
                "symbol": "402340",
                "name": "",
                "signal_type": "sector_treemap",
                "direction": "positive",
                "strength": 92,
                "summary": "세시반 선도 섹터: SK스퀘어 거래대금 상위",
                "as_of": "2026-05-06T15:30:00+09:00",
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(sesiban_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed={
            "items": [
                {
                    "title": "리서치 후보",
                    "source": "research",
                    "picks": ["000660"],
                }
            ]
        },
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    by_symbol = {row["symbol"]: row for row in rows}

    assert by_symbol["402340"]["name"] == "SK스퀘어"
    assert by_symbol["000660"]["name"] == "SK하이닉스"


def test_strategy_engine_appends_external_insight_signals(tmp_path: Path) -> None:
    whale_path = tmp_path / "whale.jsonl"
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    result = engine.append_external_signals(
        source_id="whale",
        payload={
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "large_holder_change",
            "direction": "positive",
            "strength": 88,
            "summary": "국민연금 보유 변화 확인",
        },
    )

    assert result["status"] == "ok"
    assert result["source_id"] == "whale_insight"
    assert result["inserted"] == 1
    assert whale_path.exists()
    status = engine.source_status()[0]
    assert status["status"] == "ok"
    assert status["signals"][0]["strength"] == 88


def test_strategy_engine_persists_external_insights_to_db(tmp_path: Path) -> None:
    whale_path = tmp_path / "whale.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            insight_db_path=str(db_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    first = engine.append_external_signals(
        source_id="whale",
        payload={
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "large_holder_change",
            "direction": "positive",
            "strength": 88,
            "summary": "국민연금 보유 변화 확인",
            "as_of": "2026-05-06",
        },
    )
    second = engine.append_external_signals(
        source_id="whale",
        payload={
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "large_holder_change",
            "direction": "positive",
            "strength": 88,
            "summary": "국민연금 보유 변화 확인",
            "as_of": "2026-05-06",
        },
    )

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert first["db_path"] == str(db_path)
    status = engine.source_status()[0]
    assert status["storage"] == "sqlite"
    assert status["count"] == 1
    assert status["signals"][0]["symbol"] == "005930"
    listed = engine.list_external_signals(source_id="whale", date_from="2026-05-06")
    assert listed["storage"] == "sqlite"
    assert listed["count"] == 1


def test_strategy_insight_repository_migrates_jsonl(tmp_path: Path) -> None:
    source_path = tmp_path / "sesiban.jsonl"
    _write_jsonl(
        source_path,
        [
            {
                "schema_version": 1,
                "source_id": "after_close_330",
                "symbol": "005930",
                "name": "삼성전자",
                "signal_type": "sector_treemap",
                "direction": "positive",
                "strength": 88,
                "summary": "세시반 선도 섹터 반도체",
                "as_of": "2026-05-06T15:30:00+09:00",
                "tags": ["sesiban"],
            }
        ],
    )
    repo = StrategyInsightRepository(str(tmp_path / "strategy_insights.db"))

    first = repo.migrate_jsonl(source_id="after_close_330", path=str(source_path))
    second = repo.migrate_jsonl(source_id="after_close_330", path=str(source_path))
    rows = repo.list_signals(source_id="after_close_330")

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["skipped"] == 1
    assert len(rows) == 1
    assert rows[0]["symbol"] == "005930"


def test_strategy_insight_collector_imports_local_jsonl_once(tmp_path: Path) -> None:
    import_path = tmp_path / "incoming_whale.jsonl"
    whale_path = tmp_path / "whale.jsonl"
    _write_jsonl(
        import_path,
        [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "signal_type": "large_holder_change",
                "direction": "positive",
                "strength": 88,
                "summary": "국민연금 보유 변화 확인",
                "as_of": "2026-04-30T15:30:00+09:00",
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        llm_bridge=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )
    collector = StrategyInsightCollector(
        engine=engine,
        sources=[{"source_id": "whale_insight", "path": str(import_path)}],
    )

    first = asyncio.run(collector.collect_once())
    second = asyncio.run(collector.collect_once())

    assert first["status"] == "ok"
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["sources"][0]["skipped"] == 1
    assert len(whale_path.read_text(encoding="utf-8").splitlines()) == 1


def test_whale_static_rows_convert_to_strategy_signals() -> None:
    rows = _parse_js_object_array(
        """
        const MAJOR_DATA = [
          {
            company: '삼성전자',
            stkqy: '1,000,000',
            stkqy_irds: '250,000',
            stkrt: '7.20',
            stkrt_irds: '1.10',
            report_resn: '단순추가취득/처분',
            date: '2026-04-30'
          }
        ];
        """,
        "MAJOR_DATA",
    )

    signals = _whale_major_rows_to_signals(
        rows,
        symbol_by_name={"삼성전자": "005930"},
        limit=10,
    )

    assert signals == [
        {
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "large_holder_change",
            "direction": "positive",
            "strength": 63,
            "summary": (
                "Whale Insight 5% 지분 변동: 삼성전자 지분율 7.20%, "
                "변동 1.10%p, 주식수 변동 250,000주, 사유 단순추가취득/처분"
            ),
            "as_of": "2026-04-30",
            "tags": ["whale", "dart", "major_holder"],
        }
    ]


def test_sesiban_leading_payload_converts_to_strategy_signals() -> None:
    signals = _sesiban_leading_payload_to_signals(
        {
            "generated_at": "2026-05-04T21:39:17+09:00",
            "sectors": [
                {
                    "name": "전기장비",
                    "rank": 1,
                    "intensity": 1.5,
                    "weighted_return_pct": 10.2,
                    "leading_stocks": [
                        {
                            "symbol": "062040",
                            "name": "산일전기",
                            "change_rate": 23.68,
                            "contribution_pct": 17.16,
                        }
                    ],
                }
            ],
        },
        limit=5,
    )

    assert signals[0]["symbol"] == "062040"
    assert signals[0]["name"] == "산일전기"
    assert signals[0]["signal_type"] == "sector_treemap"
    assert signals[0]["direction"] == "positive"
    assert signals[0]["strength"] == 95
    assert "전기장비" in signals[0]["summary"]


def test_strategy_insight_append_endpoint(monkeypatch) -> None:
    class _FakeStrategyEngine:
        def append_external_signals(
            self,
            *,
            source_id: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            assert source_id == "after_close_330"
            assert payload["symbol"] == "005930"
            return {"status": "ok", "source_id": source_id, "inserted": 1}

    monkeypatch.setattr(main, "strategy_intelligence", _FakeStrategyEngine())

    with TestClient(main.app) as client:
        response = client.post(
            "/api/strategy/insights/after_close_330",
            json={
                "symbol": "005930",
                "direction": "positive",
                "strength": 77,
                "summary": "종가 수급 후보",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["inserted"] == 1


def test_strategy_insights_collect_endpoint_uses_collector(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeCollector:
        async def collect_once(self) -> dict[str, Any]:
            return {"status": "ok", "inserted": 2, "sources": []}

    def fake_build_collector(sources: list[dict[str, Any]] | None = None) -> _FakeCollector:
        seen["sources"] = sources
        return _FakeCollector()

    monkeypatch.setattr(main, "_build_strategy_insight_collector", fake_build_collector)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/strategy/insights/collect",
            json={"sources": [{"source_id": "whale_insight", "signals": []}]},
        )

    assert response.status_code == 200
    assert response.json()["inserted"] == 2
    assert seen["sources"] == [{"source_id": "whale_insight", "signals": []}]


def test_strategy_brief_endpoint_uses_strategy_engine(monkeypatch) -> None:
    class _FakeStrategyEngine:
        async def build_brief(
            self,
            *,
            query: str,
            research_feed: dict[str, Any] | None,
            use_llm: bool = False,
            limit: int | None = None,
        ) -> dict[str, Any]:
            assert query == "다음 거래일 관심 후보"
            assert research_feed is None
            assert use_llm is True
            assert limit == 5
            return {
                "status": "ok",
                "query": query,
                "model": "gpt-5.5",
                "brief_mode": "llm",
                "brief_md": "후보 브리핑",
                "candidates": [],
            }

    monkeypatch.setattr(main, "strategy_intelligence", _FakeStrategyEngine())
    monkeypatch.setattr(main, "_read_strategy_research_feed", lambda: None)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/strategy/brief",
            json={"query": "다음 거래일 관심 후보", "limit": 5, "use_llm": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["model"] == "gpt-5.5"
    assert payload["brief_mode"] == "llm"
