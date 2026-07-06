from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
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
    _parse_signal_date,
    _sesiban_leading_payload_to_signals,
    _whale_major_rows_to_signals,
    classify_strategy_intent,
)


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


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


class _CountingReportRepository(_FakeReportRepository):
    def __init__(self) -> None:
        self.search_calls = 0

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
        self.search_calls += 1
        return super().search(
            query=query,
            symbol=symbol,
            category=category,
            limit=limit,
            broker=broker,
            analyst=analyst,
            date_from=date_from,
            date_to=date_to,
        )


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


def test_parse_signal_date_keeps_date_only_values_on_same_day() -> None:
    assert _parse_signal_date("2026-06-30") == date(2026, 6, 30)


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


class _LinkedETFReportRepository(_ExternalOnlyReportRepository):
    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        _ = limit
        if symbol != "069500":
            return []
        return [
            {
                "report_id": 501,
                "category": "invest_info",
                "title": "ETF 전략: KODEX 200 코어 비중 점검",
                "company_name": "",
                "broker": "테스트증권",
                "symbol": "069500",
                "published_at": "2026-05-18",
                "snippet": "KODEX 200(069500)을 코어 ETF로 활용한다.",
                "linked_name": "KODEX",
                "asset_class": "etf",
                "link_evidence": "KODEX 200(069500)을 코어 ETF로 활용한다.",
            }
        ]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) != 501:
            return None
        return {
            "content": (
                "KODEX 200(069500)은 시장 대표지수 분산 노출에 적합한 코어 ETF이며 "
                "단기 변동성은 분할매수로 관리한다."
            )
        }

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) != 501:
            return None
        return {
            "summary_bullets": ["KOSPI 200 대표지수에 분산 노출하는 코어 ETF"],
            "risks": ["지수 변동성 확인 필요"],
        }

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        if "069500" in symbols:
            return {"069500": "KODEX 200"}
        return super().resolve_symbol_names(symbols)


class _NoisyLinkedETFReportRepository(_LinkedETFReportRepository):
    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        _ = limit
        if symbol != "069500":
            return []
        return [
            {
                "report_id": 700 + index,
                "category": "market_info",
                "title": "u Korea Market u Global Indices u FICC 주요지수 Close 지수등락률 D-20",
                "company_name": "",
                "broker": "테스트증권",
                "symbol": "069500",
                "published_at": "2026-05-18",
                "snippet": "u Korea Market u Global Indices u FICC 주요지수 Close 지수등락률 D-20",
                "linked_name": "KODEX",
                "asset_class": "etf",
                "link_evidence": "u Korea Market u Global Indices u FICC 주요지수 Close 지수등락률 D-20",
            }
            for index in range(8)
        ]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        return {
            "content": "u Korea Market u Global Indices u FICC 주요지수 Close 지수등락률 D-20"
        }

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        return {"summary_bullets": [], "risks": []}


class _MacroTitleLinkedETFReportRepository(_LinkedETFReportRepository):
    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        _ = limit
        if symbol != "069500":
            return []
        return [
            {
                "report_id": 801,
                "category": "market_info",
                "title": "Ride With Us or Collide With Us 2026년 하반기 Credit 전략",
                "company_name": "",
                "broker": "테스트증권",
                "symbol": "069500",
                "published_at": "2026-05-18",
                "snippet": "크레딧 스프레드와 글로벌 금리 환경 점검",
                "linked_name": "KODEX",
                "asset_class": "etf",
                "link_evidence": "크레딧 스프레드와 글로벌 금리 환경 점검",
            },
            {
                "report_id": 802,
                "category": "market_info",
                "title": "ETF 전략: KODEX 200 코어 비중 점검",
                "company_name": "",
                "broker": "테스트증권",
                "symbol": "069500",
                "published_at": "2026-05-18",
                "snippet": "KODEX 200(069500)을 코어 ETF로 활용한다.",
                "linked_name": "KODEX",
                "asset_class": "etf",
                "link_evidence": "KODEX 200(069500)을 코어 ETF로 활용한다.",
            },
        ]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) == 802:
            return {"content": "KODEX 200(069500)을 코어 ETF로 활용한다."}
        return {"content": "크레딧 스프레드와 글로벌 금리 환경 점검"}

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) == 802:
            return {"summary_bullets": ["KODEX 200 코어 ETF 활용"], "risks": []}
        return {"summary_bullets": ["크레딧 스프레드 점검"], "risks": []}


class _CrowdedLinkedETFReportRepository(_LinkedETFReportRepository):
    equity_symbols = [f"10{index:04d}" for index in range(1, 13)]

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
            return super().search(
                query=query,
                symbol=symbol,
                category=category,
                limit=limit,
                broker=broker,
                analyst=analyst,
                date_from=date_from,
                date_to=date_to,
            )
        return [
            {
                "report_id": 600 + index,
                "category": "company_analysis",
                "title": f"테스트고득점{index}({symbol}) AI 실적 점검",
                "company_name": f"테스트고득점{index}",
                "broker": "테스트증권",
                "symbol": symbol,
                "published_at": "2026-05-18",
                "snippet": "AI 수혜와 실적 개선, 목표주가 상향",
            }
            for index, symbol in enumerate(self.equity_symbols, start=1)
        ]

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) == 501:
            return super().get_report(report_id)
        if 601 <= int(report_id) <= 612:
            index = int(report_id) - 600
            symbol = self.equity_symbols[index - 1]
            return {
                "content": (
                    f"테스트고득점{index}({symbol}) BUY. AI 수혜, 실적 개선, "
                    "목표주가 상향과 수급 개선 근거가 있다."
                )
            }
        return None

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        if int(report_id) == 501:
            return super().get_report_facts(report_id)
        if 601 <= int(report_id) <= 612:
            return {
                "rating": "BUY",
                "target_price": {"value": 100000, "currency": "KRW", "changed": "UP"},
                "summary_bullets": ["AI 수혜와 실적 개선이 목표주가 상향을 뒷받침한다"],
                "risks": [],
                "evidence_quotes": [{"page": 2, "text": "목표주가 상향"}],
            }
        return None

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        names = super().resolve_symbol_names(symbols)
        names.update(
            {
                symbol: f"테스트고득점{index}"
                for index, symbol in enumerate(self.equity_symbols, start=1)
                if symbol in symbols
            }
        )
        return names


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


class _ReadyFailingBridge:
    ready = True
    resolved_model = "gpt-5.5"

    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        _ = (payload, timeout_ms)
        return {"ok": False, "error": "native timeout"}


class _EmptyReportRepository:
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
        return {symbol: f"테스트종목{symbol}" for symbol in symbols}


class _EmptyRAGStore:
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
        return []


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


class _FakeETFResearchRepository:
    def list_universe(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "category": "core",
                "tags": ["core", "kospi200"],
            }
        ]

    def latest_snapshot(self, symbol: str) -> dict[str, Any]:
        if symbol != "069500":
            return {"status": "missing", "symbol": symbol}
        return {
            "symbol": "069500",
            "name": "KODEX 200",
            "price": 42_500.0,
            "change_pct": 0.8,
            "volume": 1_250_000,
            "turnover_krw": 53_000_000_000.0,
            "source": "test",
            "raw": {},
            "captured_at": "2026-05-06T06:30:00+00:00",
            "status": "ok",
            "error_message": "",
        }

    def latest_score(self, symbol: str) -> dict[str, Any]:
        if symbol != "069500":
            return {"label": "unknown", "symbol": symbol}
        return {
            "symbol": "069500",
            "label": "core_fit",
            "liquidity_score": 90.0,
            "momentum_score": 62.0,
            "core_fit_score": 86.0,
            "risk_score": 8.0,
            "reasons": ["KOSPI 200 대표 ETF이며 거래대금이 충분하다."],
            "risks": ["지수 갭 변동성은 확인 필요."],
            "scored_at": "2026-05-06T06:31:00+00:00",
        }

    def status(self) -> dict[str, Any]:
        return {
            "db_path": ":memory:",
            "universe_count": 1,
            "snapshot_count": 1,
            "score_count": 1,
            "latest_snapshot_at": "2026-05-06T06:30:00+00:00",
            "latest_score_at": "2026-05-06T06:31:00+00:00",
        }


class _CrowdedETFResearchRepository(_FakeETFResearchRepository):
    symbols = [
        ("069500", "KODEX 200"),
        ("102110", "TIGER 200"),
        ("091160", "KODEX 반도체"),
        ("229200", "KODEX 코스닥150"),
        ("133690", "TIGER 미국나스닥100"),
        ("379810", "KODEX 미국나스닥100"),
    ]

    def list_universe(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": symbol,
                "name": name,
                "category": "core" if index < 2 else "theme",
                "tags": ["etf", "core" if index < 2 else "theme"],
            }
            for index, (symbol, name) in enumerate(self.symbols)
        ]

    def latest_snapshot(self, symbol: str) -> dict[str, Any]:
        if not any(row[0] == symbol for row in self.symbols):
            return {"status": "missing", "symbol": symbol}
        return {
            "symbol": symbol,
            "name": next(name for code, name in self.symbols if code == symbol),
            "price": 42_500.0,
            "change_pct": 1.2,
            "volume": 1_250_000,
            "turnover_krw": 53_000_000_000.0,
            "source": "test",
            "raw": {},
            "captured_at": "2026-05-26T06:30:00+00:00",
            "status": "ok",
            "error_message": "",
        }

    def latest_score(self, symbol: str) -> dict[str, Any]:
        if not any(row[0] == symbol for row in self.symbols):
            return {"label": "unknown", "symbol": symbol}
        return {
            "symbol": symbol,
            "label": "core_fit",
            "liquidity_score": 100.0,
            "momentum_score": 96.0,
            "core_fit_score": 94.0,
            "risk_score": 0.0,
            "reasons": ["거래대금과 추세가 모두 우수한 ETF 후보."],
            "risks": [],
            "scored_at": "2026-05-26T06:31:00+00:00",
        }

    def status(self) -> dict[str, Any]:
        return {
            "db_path": ":memory:",
            "universe_count": len(self.symbols),
            "snapshot_count": len(self.symbols),
            "score_count": len(self.symbols),
            "latest_snapshot_at": "2026-05-26T06:30:00+00:00",
            "latest_score_at": "2026-05-26T06:31:00+00:00",
        }


class _ConfiguredOnlyETFResearchRepository:
    def list_universe(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "category": "core",
                "tags": ["core", "kospi200"],
            }
        ]

    def latest_snapshot(self, symbol: str) -> dict[str, Any]:
        return {"status": "missing", "symbol": symbol}

    def latest_score(self, symbol: str) -> dict[str, Any]:
        return {"label": "unknown", "symbol": symbol}

    def status(self) -> dict[str, Any]:
        return {
            "db_path": ":memory:",
            "universe_count": 1,
            "snapshot_count": 0,
            "score_count": 0,
            "latest_snapshot_at": "",
            "latest_score_at": "",
        }


class _StaleCountETFResearchRepository(_ConfiguredOnlyETFResearchRepository):
    def status(self) -> dict[str, Any]:
        return {
            "db_path": ":memory:",
            "universe_count": 1,
            "snapshot_count": 3,
            "score_count": 2,
            "usable_research_count": 0,
            "latest_snapshot_at": "2026-05-01T00:00:00+00:00",
            "latest_score_at": "2026-05-01T00:01:00+00:00",
        }


class _ErrorOnlyETFResearchRepository(_ConfiguredOnlyETFResearchRepository):
    def latest_snapshot(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "name": "KODEX 200",
            "status": "error",
            "error_message": "quote timeout",
        }

    def latest_score(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "label": "unknown",
            "liquidity_score": 0,
            "momentum_score": 0,
            "core_fit_score": 0,
            "risk_score": 90,
            "risks": ["quote timeout"],
        }

    def status(self) -> dict[str, Any]:
        return {
            "db_path": ":memory:",
            "universe_count": 1,
            "snapshot_count": 1,
            "score_count": 1,
            "latest_snapshot_at": "2026-05-01T00:00:00+00:00",
            "latest_score_at": "2026-05-01T00:01:00+00:00",
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
                "as_of": date.today().isoformat(),
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
                "as_of": date.today().isoformat(),
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
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


def test_strategy_brief_reuses_short_deterministic_cache(tmp_path: Path) -> None:
    decision_path = tmp_path / "decisions.jsonl"
    repository = _CountingReportRepository()
    engine = StrategyIntelligenceEngine(
        repository=repository,
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        fundamentals_repository=_FakeFundamentalsRepository(),
        config=StrategyIntelligenceConfig(
            decision_log_path=str(decision_path),
            max_report_scan=10,
            brief_cache_ttl_sec=60,
        ),
    )

    first = asyncio.run(
        engine.build_brief(
            query="다음 거래일 관심 후보",
            research_feed={"count": 1, "items": [{"picks": ["005930"]}]},
            use_llm=False,
            limit=5,
        )
    )
    first["candidates"][0]["name"] = "mutated"
    first_search_calls = repository.search_calls
    second = asyncio.run(
        engine.build_brief(
            query="다음 거래일 관심 후보",
            research_feed={"count": 1, "items": [{"picks": ["005930"]}]},
            use_llm=False,
            limit=5,
        )
    )

    assert first_search_calls > 0
    assert repository.search_calls == first_search_calls
    assert first["cache_status"] == "miss"
    assert second["cache_status"] == "hit"
    assert second["candidates"][0]["name"] == "삼성전자"


def test_strategy_brief_llm_mode_surfaces_native_runtime_unavailable(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        fundamentals_repository=_FakeFundamentalsRepository(),
        config=StrategyIntelligenceConfig(
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = asyncio.run(
        engine.build_brief(
            query="다음 거래일 관심 후보",
            research_feed={"count": 1, "items": [{"picks": ["005930"]}]},
            use_llm=True,
            limit=5,
        )
    )

    assert payload["status"] == "error"
    assert payload["brief_mode"] == "llm_error"
    assert payload["brief_md"] == ""
    assert payload["error_message"] == "codex_runtime_unavailable"
    assert payload["candidates"]


def test_strategy_brief_llm_mode_surfaces_native_runtime_error(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_ReadyFailingBridge(),
        fundamentals_repository=_FakeFundamentalsRepository(),
        config=StrategyIntelligenceConfig(
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = asyncio.run(
        engine.build_brief(
            query="다음 거래일 관심 후보",
            research_feed={"count": 1, "items": [{"picks": ["005930"]}]},
            use_llm=True,
            limit=5,
        )
    )

    assert payload["status"] == "error"
    assert payload["brief_mode"] == "llm_error"
    assert payload["brief_md"] == ""
    assert "native timeout" in payload["error_message"]
    assert payload["candidates"]


def test_strategy_engine_honors_ai_scale_limit_for_external_signal_candidates(
    tmp_path: Path,
) -> None:
    whale_path = tmp_path / "whale.jsonl"
    sesiban_path = tmp_path / "sesiban.jsonl"
    _write_jsonl(
        sesiban_path,
        [
            {
                "symbol": f"{1000 + index:06d}",
                "name": f"세시반후보{index}",
                "type": "after_close_flow",
                "direction": "positive",
                "strength": 95 - (index % 5),
                "summary": f"세시반 독립 수급 후보 {index}",
                "as_of": f"{date.today().isoformat()}T15:30:00+09:00",
            }
            for index in range(16)
        ],
    )
    _write_jsonl(
        whale_path,
        [
            {
                "symbol": f"{3000 + index:06d}",
                "name": f"고래후보{index}",
                "type": "large_holder_change",
                "direction": "positive",
                "strength": 90 - index,
                "summary": f"고래 독립 포지션 후보 {index}",
                "as_of": date.today().isoformat(),
            }
            for index in range(8)
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_EmptyReportRepository(),
        rag_store=_EmptyRAGStore(),
        codex_runtime=_FakeBridge(),
        fundamentals_repository=None,
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(sesiban_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    payload = engine.build_candidates(
        query="국장1 계좌와 전략 지식을 바탕으로 블록 매매 계획을 관리해줘",
        research_feed=None,
        limit=20,
    )

    assert payload["candidate_count"] == 20
    source_sets = [set(row["sources"]) for row in payload["candidates"]]
    assert any("after_close_330" in sources for sources in source_sets)
    assert any("whale_insight" in sources for sources in source_sets)


def test_strategy_engine_marks_disabled_research_runner_as_optional(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        fundamentals_repository=_FakeFundamentalsRepository(),
        config=StrategyIntelligenceConfig(
            insight_db_path=str(tmp_path / "insights.db"),
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 후보",
        research_feed=None,
        limit=3,
    )

    research_source = next(
        row for row in payload["sources"] if row["source_id"] == "research_runner"
    )
    assert research_source["status"] == "optional_disabled"
    assert "legacy" in research_source["role"].lower()

    payload_with_discovery_only = engine.build_candidates(
        query="다음 거래일 후보",
        research_feed={
            "daily_discovery": {
                "status": "ok",
                "items": [{"symbol": "005930", "name": "삼성전자"}],
            }
        },
        limit=3,
    )
    research_source = next(
        row
        for row in payload_with_discovery_only["sources"]
        if row["source_id"] == "research_runner"
    )
    assert research_source["status"] == "optional_disabled"


def test_strategy_engine_weights_report_only_candidates_below_raw_cap(tmp_path: Path) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
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


def test_strategy_engine_keeps_flow_only_candidates_in_confirm_lane(tmp_path: Path) -> None:
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
                "as_of": f"{date.today().isoformat()}T15:30:00+09:00",
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
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

    assert row["score"] < 50
    assert row["stance"] == "confirm"


def test_strategy_engine_includes_etf_research_without_company_reports(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_FakeETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="코어 ETF 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "069500")

    assert row["asset_class"] == "etf"
    assert row["name"] == "KODEX 200"
    assert row["horizon_bias"] == "core_etf"
    assert row["valuation"] == {"status": "not_applicable", "label": "etf"}
    assert row["data_coverage"]["has_etf_research"] is True
    assert row["data_coverage"]["has_valuation"] is False
    assert row["score_components"]["liquidity"] == 90
    assert row["score_components"]["core_fit"] == 86
    assert row["score_components"]["valuation"] == 0
    assert "etf_research" in row["sources"]
    assert "밸류 미수집" not in row["data_warnings"]
    assert row["report_ids"] == []
    assert next(
        source for source in payload["sources"] if source["source_id"] == "etf_research"
    )["status"] == "active"


def test_strategy_engine_uses_daily_discovery_equity_research(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed={
            "daily_discovery": {
                "status": "ok",
                "trading_day": "2026-05-26",
                "items": [
                    {
                        "symbol": "375500",
                        "name": "DL이앤씨",
                        "score": 83.2,
                        "analysis": {
                            "stance": "confirm",
                            "confidence": 0.68,
                            "summary": "건설 업황 대비 재무 안정성과 저평가 매력이 확인됨",
                            "reasons": ["PER/PBR 부담이 낮고 수주잔고가 견조함"],
                            "risks": ["주택 업황 둔화 확인 필요"],
                        },
                    }
                ],
            }
        },
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "375500")

    assert row["name"] == "DL이앤씨"
    assert row["asset_class"] == "equity"
    assert row["score"] >= 30
    assert row["data_coverage"]["has_research"] is True
    assert "daily_discovery" in row["sources"]
    assert any("데일리 디스커버리" in reason for reason in row["reasons"])
    assert "주택 업황 둔화 확인 필요" in row["risks"]


def test_strategy_engine_keeps_daily_discovery_equities_visible_when_etfs_are_crowded(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_CrowdedETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed={
            "daily_discovery": {
                "status": "ok",
                "trading_day": "2026-05-26",
                "items": [
                    {
                        "symbol": "375500",
                        "name": "DL이앤씨",
                        "score": 83.2,
                        "analysis": {
                            "stance": "confirm",
                            "confidence": 0.68,
                            "summary": "일반 종목 디스커버리 상위 후보",
                            "reasons": ["저평가와 실적 회복 가능성"],
                            "risks": [],
                        },
                    }
                ],
            }
        },
        limit=5,
    )

    assert len(payload["candidates"]) == 5
    assert any(row["symbol"] == "375500" for row in payload["candidates"])
    assert any(row["asset_class"] != "etf" for row in payload["candidates"])
    assert any(row["asset_class"] == "etf" for row in payload["candidates"])
    assert any(row["asset_class"] != "etf" for row in payload["candidates"][:3])


def test_strategy_engine_creates_etf_candidate_from_linked_naver_report_without_snapshot(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_LinkedETFReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_ConfiguredOnlyETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="코어 ETF 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "069500")

    assert row["asset_class"] == "etf"
    assert row["name"] == "KODEX 200"
    assert row["horizon_bias"] == "core_etf"
    assert row["valuation"] == {"status": "not_applicable", "label": "etf"}
    assert row["data_coverage"]["has_report"] is True
    assert row["data_coverage"]["has_etf_research"] is False
    assert row["report_ids"] == [501]
    assert "naver_reports" in row["sources"]
    assert "etf_research" not in row["sources"]
    assert "밸류 미수집" not in row["data_warnings"]
    assert row["score_components"]["report"] > 0
    assert row["score_components"]["evidence"] > 0
    assert row["score_components"]["recency"] > 0
    assert row["score_components"]["valuation"] == 0
    assert row["score_components"]["quality"] == 0
    assert row["score_components"]["growth"] == 0


def test_strategy_engine_caps_noisy_linked_etf_report_only_candidate(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_NoisyLinkedETFReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_ConfiguredOnlyETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=20,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "069500")

    assert row["asset_class"] == "etf"
    assert row["data_coverage"]["has_etf_research"] is False
    assert row["score_components"]["report"] <= 45
    assert row["score_components"]["evidence"] <= 45
    assert row["score"] < 65
    assert not any("Global Indices" in reason for reason in row["reasons"])
    assert not any("Global Indices" in fact for fact in row["facts"])


def test_strategy_engine_hides_macro_titles_from_linked_etf_reasons(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_MacroTitleLinkedETFReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_ConfiguredOnlyETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=20,
        ),
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "069500")

    assert any("KODEX 200" in reason for reason in row["reasons"])
    assert not any("Credit 전략" in reason for reason in row["reasons"])
    assert any("KODEX 200" in fact for fact in row["facts"])
    assert not any("크레딧 스프레드" in fact for fact in row["facts"])


def test_strategy_engine_keeps_linked_etf_visible_for_etf_query_when_equities_score_higher(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_CrowdedLinkedETFReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_ConfiguredOnlyETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=20,
        ),
    )

    payload = engine.build_candidates(
        query="ETF 코어 후보 KODEX",
        research_feed=None,
        limit=12,
    )

    candidate_symbols = [row["symbol"] for row in payload["candidates"]]
    row = next(item for item in payload["candidates"] if item["symbol"] == "069500")
    equity_scores = [
        item["score"]
        for item in payload["candidates"]
        if item["asset_class"] != "etf"
    ]

    assert len(payload["candidates"]) == 12
    assert "069500" in candidate_symbols
    assert row["asset_class"] == "etf"
    assert row["report_ids"] == [501]
    assert "naver_reports" in row["sources"]
    assert equity_scores
    assert row["score"] < min(equity_scores)


def test_strategy_engine_merges_linked_naver_report_with_etf_research(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_LinkedETFReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_FakeETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="코어 ETF 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "069500")

    assert row["data_coverage"]["has_report"] is True
    assert row["name"] == "KODEX 200"
    assert row["data_coverage"]["has_etf_research"] is True
    assert row["report_ids"] == [501]
    assert "naver_reports" in row["sources"]
    assert "etf_research" in row["sources"]
    assert row["score_components"]["report"] > 0
    assert row["score_components"]["liquidity"] == 90
    assert row["score_components"]["core_fit"] == 86
    assert "밸류 미수집" not in row["data_warnings"]


def test_strategy_engine_marks_etf_source_waiting_without_usable_data(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_ConfiguredOnlyETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="코어 ETF 후보",
        research_feed=None,
        limit=5,
    )
    etf_source = next(
        source for source in payload["sources"] if source["source_id"] == "etf_research"
    )

    assert etf_source["status"] == "waiting"
    assert etf_source["count"] == 0
    assert not any(
        row["symbol"] == "069500"
        for row in list(payload["candidates"]) + list(payload["exclusions"])
    )


def test_strategy_engine_prefers_explicit_zero_etf_usable_count(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_StaleCountETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="코어 ETF 후보",
        research_feed=None,
        limit=5,
    )
    etf_source = next(
        source for source in payload["sources"] if source["source_id"] == "etf_research"
    )

    assert etf_source["status"] == "waiting"
    assert etf_source["count"] == 0


def test_strategy_engine_does_not_use_error_only_etf_research(
    tmp_path: Path,
) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        etf_research_repository=_ErrorOnlyETFResearchRepository(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )

    payload = engine.build_candidates(
        query="코어 ETF 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    etf_source = next(
        source for source in payload["sources"] if source["source_id"] == "etf_research"
    )

    assert etf_source["status"] == "waiting"
    assert not any(row["symbol"] == "069500" for row in rows)


def test_configured_etf_provider_lists_universe_before_api_seed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "configured_etf.db"
    monkeypatch.setattr(main.settings, "etf_research_db_path", str(db_path))
    monkeypatch.setattr(main.settings, "etf_research_universe", "069500:KODEX 200")
    provider = main.ConfiguredETFResearchProvider(
        repository_factory=main._etf_research_repository,
        universe_provider=main._configured_etf_universe,
    )

    rows = provider.list_universe()

    symbols = [row["symbol"] for row in rows]
    assert symbols[0] == "069500"
    assert "102110" in symbols
    assert rows[0]["name"] == "KODEX 200"
    assert provider.latest_snapshot("069500")["status"] == "missing"
    assert provider.status()["status"] == "waiting"
    assert provider.status()["usable_research_count"] == 0


def test_strategy_engine_filters_noisy_rag_context(tmp_path: Path) -> None:
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_NoisyRAGStore(),
        codex_runtime=_FakeBridge(),
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
        codex_runtime=_FakeBridge(),
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
        codex_runtime=_FakeBridge(),
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
        codex_runtime=_FakeBridge(),
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
                "as_of": f"{date.today().isoformat()}T15:30:00+09:00",
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_ExternalOnlyReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
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
        codex_runtime=_FakeBridge(),
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
        codex_runtime=_FakeBridge(),
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


def test_strategy_engine_lists_sqlite_external_signals_with_row_freshness(
    tmp_path: Path,
) -> None:
    whale_path = tmp_path / "whale.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            insight_db_path=str(db_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )
    engine.append_external_signals(
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

    listed = engine.list_external_signals(source_id="whale", limit=10)

    assert listed["storage"] == "sqlite"
    assert listed["count"] == 1
    row = listed["items"][0]
    assert row["stale"] is True
    assert row["stale_reason"] == "as_of_too_old"
    assert row["stale_days"] >= 30
    assert row["stale_after_days"] == 5


def test_strategy_engine_lists_jsonl_external_signals_with_row_freshness(
    tmp_path: Path,
) -> None:
    whale_path = tmp_path / "whale.jsonl"
    fresh_as_of = datetime.now(timezone.utc).date().isoformat()
    _write_jsonl(
        whale_path,
        [
            {
                "schema_version": 1,
                "source_id": "whale_insight",
                "symbol": "005930",
                "name": "삼성전자",
                "signal_type": "large_holder_change",
                "direction": "positive",
                "strength": 88,
                "summary": "국민연금 보유 변화 확인",
                "as_of": fresh_as_of,
            }
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    listed = engine.list_external_signals(source_id="whale", limit=10)

    assert listed["storage"] == "jsonl"
    assert listed["count"] == 1
    row = listed["items"][0]
    assert row["stale"] is False
    assert row["stale_days"] == 0
    assert row["stale_after_days"] == 5


def test_strategy_engine_marks_stale_external_insight_sources(tmp_path: Path) -> None:
    whale_path = tmp_path / "whale.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            insight_db_path=str(db_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    engine.append_external_signals(
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

    status = engine.source_status()[0]

    assert status["storage"] == "sqlite"
    assert status["count"] == 1
    assert status["status"] == "stale"
    assert status["stale"] is True
    assert status["stale_days"] >= 30
    assert "latest_as_of" in status["warnings"][0]
    assert status["signals"][0]["stale"] is True
    assert status["signals"][0]["stale_reason"] == "as_of_too_old"


def test_strategy_engine_excludes_stale_external_signals_from_candidate_scores(
    tmp_path: Path,
) -> None:
    whale_path = tmp_path / "whale.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            insight_db_path=str(db_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            max_report_scan=10,
        ),
    )
    engine.append_external_signals(
        source_id="whale",
        payload={
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "large_holder_change",
            "direction": "positive",
            "strength": 88,
            "summary": "오래된 큰손 포지션 변화",
            "as_of": "2026-05-06",
        },
    )

    payload = engine.build_candidates(
        query="다음 거래일 관심 후보",
        research_feed=None,
        limit=5,
    )
    rows = list(payload["candidates"]) + list(payload["exclusions"])
    row = next(item for item in rows if item["symbol"] == "005930")

    assert engine.source_status()[0]["status"] == "stale"
    assert row["score_components"]["whale"] == 0
    assert "whale_insight" not in row["sources"]
    assert "고래 없음" in row["data_warnings"]


def test_strategy_engine_excludes_stale_rows_inside_fresh_external_source(
    tmp_path: Path,
) -> None:
    whale_path = tmp_path / "whale.jsonl"
    _write_jsonl(
        whale_path,
        [
            {
                "schema_version": 1,
                "source_id": "whale_insight",
                "symbol": "000660",
                "name": "SK하이닉스",
                "signal_type": "large_holder_change",
                "direction": "positive",
                "strength": 92,
                "summary": "오래된 큰손 포지션 변화",
                "as_of": "2026-05-06",
            },
            {
                "schema_version": 1,
                "source_id": "whale_insight",
                "symbol": "005930",
                "name": "삼성전자",
                "signal_type": "large_holder_change",
                "direction": "positive",
                "strength": 82,
                "summary": "최신 큰손 포지션 변화",
                "as_of": date.today().isoformat(),
            },
        ],
    )
    engine = StrategyIntelligenceEngine(
        repository=_EmptyReportRepository(),
        rag_store=_EmptyRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
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
    by_symbol = {row["symbol"]: row for row in rows}

    assert engine.source_status()[0]["status"] == "ok"
    assert by_symbol["005930"]["score_components"]["whale"] == 82
    assert "000660" not in by_symbol


def test_strategy_engine_db_mode_does_not_append_jsonl_sidecar(
    tmp_path: Path,
) -> None:
    whale_path = tmp_path / "whale.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(whale_path),
            sesiban_path=str(tmp_path / "sesiban.jsonl"),
            insight_db_path=str(db_path),
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
            "as_of": "2026-05-06",
        },
    )

    assert result["inserted"] == 1
    assert result["db_path"] == str(db_path)
    assert not whale_path.exists()
    assert engine.source_status()[0]["storage"] == "sqlite"
    assert engine.source_status()[0]["count"] == 1


def test_strategy_engine_does_not_auto_migrate_legacy_jsonl_when_db_empty_by_default(
    tmp_path: Path,
) -> None:
    sesiban_path = tmp_path / "sesiban.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    _write_jsonl(
        sesiban_path,
        [
            {
                "schema_version": 1,
                "source_id": "after_close_330",
                "symbol": "000660",
                "name": "SK하이닉스",
                "signal_type": "sector_treemap",
                "direction": "positive",
                "strength": 80,
                "summary": "legacy row should stay in sidecar unless migration is explicit",
                "as_of": "2026-05-06T15:30:00+09:00",
            }
        ],
    )

    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(sesiban_path),
            insight_db_path=str(db_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    listed = engine.list_external_signals(source_id="after_close_330", limit=10)

    assert listed["storage"] == "sqlite"
    assert listed["count"] == 0
    assert listed["items"] == []


def test_strategy_engine_does_not_rehydrate_pruned_db_rows_from_legacy_jsonl(
    tmp_path: Path,
) -> None:
    sesiban_path = tmp_path / "sesiban.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    _write_jsonl(
        sesiban_path,
        [
            {
                "schema_version": 1,
                "source_id": "after_close_330",
                "symbol": "000660",
                "name": "SK하이닉스",
                "signal_type": "sector_treemap",
                "direction": "positive",
                "strength": 80,
                "summary": "legacy row should not be rehydrated",
                "as_of": "2026-05-06T15:30:00+09:00",
            }
        ],
    )
    repo = StrategyInsightRepository(str(db_path))
    repo.upsert_signals(
        [
            {
                "source_id": "after_close_330",
                "symbol": "005930",
                "name": "삼성전자",
                "signal_type": "sector_treemap",
                "direction": "positive",
                "strength": 88,
                "summary": "already migrated source row",
                "as_of": "2026-06-29T08:00:00+09:00",
            }
        ]
    )

    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(sesiban_path),
            insight_db_path=str(db_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    listed = engine.list_external_signals(source_id="after_close_330", limit=10)

    assert listed["count"] == 1
    assert [row["symbol"] for row in listed["items"]] == ["005930"]


def test_strategy_engine_compacts_legacy_jsonl_sidecars_from_sqlite(
    tmp_path: Path,
) -> None:
    sesiban_path = tmp_path / "sesiban.jsonl"
    db_path = tmp_path / "strategy_insights.db"
    repo = StrategyInsightRepository(str(db_path))
    repo.upsert_signals(
        [
            {
                "source_id": "after_close_330",
                "symbol": f"00000{idx}",
                "name": f"테스트{idx}",
                "signal_type": "sector_treemap",
                "direction": "positive",
                "strength": 70 + idx,
                "summary": f"sqlite row {idx}",
                "as_of": f"2026-05-0{idx}T15:30:00+09:00",
            }
            for idx in range(1, 4)
        ]
    )
    _write_jsonl(
        sesiban_path,
        [
            {
                "source_id": "after_close_330",
                "symbol": f"99999{idx}",
                "summary": "old sidecar bloat",
                "as_of": f"2026-04-0{idx}T15:30:00+09:00",
            }
            for idx in range(1, 6)
        ],
    )
    old_bytes = sesiban_path.stat().st_size
    engine = StrategyIntelligenceEngine(
        repository=_FakeReportRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(
            whale_insight_path=str(tmp_path / "whale.jsonl"),
            sesiban_path=str(sesiban_path),
            insight_db_path=str(db_path),
            decision_log_path=str(tmp_path / "decisions.jsonl"),
        ),
    )

    result = engine.compact_legacy_jsonl_sidecars(max_lines_per_source=2)
    rows = [
        json.loads(line)
        for line in sesiban_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result["status"] == "ok"
    sesiban_result = next(
        row for row in result["sources"] if row["source_id"] == "after_close_330"
    )
    assert sesiban_result["old_bytes"] == old_bytes
    assert sesiban_result["rows"] == 2
    assert len(rows) == 2
    assert [row["symbol"] for row in rows] == ["000002", "000003"]


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


def test_strategy_insight_repository_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "strategy_insights.db"
    repo = StrategyInsightRepository(str(db_path))

    with repo._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout_ms >= 30000


def test_strategy_insight_repository_prunes_old_signals(tmp_path: Path) -> None:
    repo = StrategyInsightRepository(str(tmp_path / "strategy_insights.db"))
    repo.upsert_signals(
        [
            {
                "source_id": "after_close_330",
                "symbol": "005930",
                "name": "삼성전자",
                "signal_type": "sector_treemap",
                "summary": "old",
                "as_of": "2026-05-01T15:30:00+09:00",
            },
            {
                "source_id": "after_close_330",
                "symbol": "000660",
                "name": "SK하이닉스",
                "signal_type": "sector_treemap",
                "summary": "hot",
                "as_of": "2026-06-20T15:30:00+09:00",
            },
        ]
    )

    result = repo.prune_history(
        retention_days=30,
        now_iso="2026-06-29T00:00:00+00:00",
        vacuum=False,
    )
    rows = repo.list_signals(source_id="after_close_330", limit=10)

    assert result["status"] == "ok"
    assert result["deleted"]["strategy_signals"] == 1
    assert result["retention_days"] == 30
    assert [row["symbol"] for row in rows] == ["000660"]


def test_strategy_insight_repository_caps_repeated_signal_rows_per_symbol(
    tmp_path: Path,
) -> None:
    repo = StrategyInsightRepository(str(tmp_path / "strategy_insights.db"))
    rows = [
        {
            "source_id": "after_close_330",
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "sector_treemap",
            "direction": "positive",
            "strength": 70 + idx,
            "summary": f"반복 세시반 신호 {idx}",
            "as_of": f"2026-06-20T09:{idx:02d}:00+09:00",
        }
        for idx in range(5)
    ]
    rows.append(
        {
            "source_id": "whale_insight",
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "large_holder_change",
            "direction": "positive",
            "strength": 88,
            "summary": "고래 신호는 별도 source/type으로 보존",
            "as_of": "2026-06-20",
        }
    )
    repo.upsert_signals(rows)

    result = repo.prune_history(
        retention_days=45,
        now_iso="2026-06-29T00:00:00+00:00",
        signal_row_cap_per_symbol=2,
        vacuum=False,
    )

    assert result["deleted"]["strategy_signals_capped"] == 3
    remaining = repo.list_signals(source_id="after_close_330", symbol="005930", limit=10)
    assert [row["summary"] for row in remaining] == [
        "반복 세시반 신호 4",
        "반복 세시반 신호 3",
    ]
    whale = repo.list_signals(source_id="whale_insight", symbol="005930", limit=10)
    assert len(whale) == 1


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
        codex_runtime=_FakeBridge(),
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


def test_whale_symbol_resolver_matches_common_corporate_suffix() -> None:
    class _KepcoRepository:
        def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
            _ = (query, limit)
            return [{"symbol": "015760", "company_name": "한국전력"}]

    engine = StrategyIntelligenceEngine(
        repository=_KepcoRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(),
    )
    collector = StrategyInsightCollector(engine=engine, sources=[])

    assert collector._resolve_symbol_from_repository("한국전력공사") == "015760"


def test_whale_symbol_resolver_prefers_symbol_directory_text_resolution() -> None:
    class _DirectoryAwareRepository:
        def resolve_symbol_from_text(self, text: str) -> dict[str, Any] | None:
            _ = text
            return {"symbol": "015760", "company_name": "한국전력"}

        def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
            _ = (query, limit)
            return [{"symbol": "495810", "company_name": "유비씨"}]

    engine = StrategyIntelligenceEngine(
        repository=_DirectoryAwareRepository(),
        rag_store=_FakeRAGStore(),
        codex_runtime=_FakeBridge(),
        config=StrategyIntelligenceConfig(),
    )
    collector = StrategyInsightCollector(engine=engine, sources=[])

    assert collector._resolve_symbol_from_repository("한국전력공사") == "015760"


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
            headers=_admin_headers(monkeypatch),
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
    monkeypatch.setattr(
        main,
        "_safe_strategy_collect_sources",
        lambda source_ids=None: [
            {"source_id": source_id, "kind": "test"} for source_id in (source_ids or [])
        ],
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/strategy/insights/collect",
            json={"source_ids": ["whale_insight"]},
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    assert response.json()["inserted"] == 2
    assert seen["sources"] == [{"source_id": "whale_insight", "kind": "test"}]


def test_strategy_insights_collect_rejects_arbitrary_sources(monkeypatch) -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/strategy/insights/collect",
            json={
                "sources": [
                    {
                        "source_id": "whale_insight",
                        "url": "http://169.254.169.254/latest/meta-data",
                    }
                ]
            },
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 400
    assert "source_ids only" in response.json()["detail"]


def test_strategy_research_feed_merges_daily_discovery(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeDiscoveryService:
        def latest_context(self, limit: int = 10) -> dict[str, Any]:
            seen["limit"] = limit
            return {
                "status": "ok",
                "trading_day": "2026-05-26",
                "items": [{"symbol": "375500", "name": "DL이앤씨", "score": 83.2}],
            }

    monkeypatch.setattr(
        main,
        "read_active_research_feed",
        lambda settings: ({"count": 1, "items": [{"picks": ["005930"]}]}, None),
    )
    monkeypatch.setattr(main, "daily_discovery_service", _FakeDiscoveryService())

    feed = main._read_strategy_research_feed()

    assert seen["limit"] == 10
    assert feed is not None
    assert feed["count"] == 1
    assert feed["daily_discovery"]["items"][0]["symbol"] == "375500"


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
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["model"] == "gpt-5.5"
    assert payload["brief_mode"] == "llm"
