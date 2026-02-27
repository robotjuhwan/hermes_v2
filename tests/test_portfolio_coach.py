from __future__ import annotations

import asyncio
from pathlib import Path

from tradecraft.services.portfolio_coach import (
    KISHoldingsProvider,
    PortfolioCoachConfig,
    PortfolioCoachService,
)


class _HoldingsProvider:
    async def get_snapshot(self, user_id: str) -> dict:
        return {
            "user_id": user_id,
            "as_of": "2026-02-19T08:00:00+09:00",
            "positions": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "quantity": 10,
                    "avg_price": 70000,
                    "cost_basis": 700000,
                    "market_value": 740000,
                    "weight": 0.42,
                },
                {
                    "ticker": "000660",
                    "name": "SK하이닉스",
                    "quantity": 5,
                    "avg_price": 120000,
                    "cost_basis": 600000,
                    "market_value": 650000,
                    "weight": 0.30,
                },
                {
                    "ticker": "005380",
                    "name": "현대차",
                    "quantity": 3,
                    "avg_price": 210000,
                    "cost_basis": 630000,
                    "market_value": 640000,
                    "weight": 0.28,
                },
            ],
            "cash": 300000,
        }


class _ReportRepo:
    def search(
        self, query: str, symbol: str = "", category: str = "", limit: int = 10
    ) -> list[dict]:
        _ = (query, category, limit)
        if not symbol:
            return [
                {
                    "report_id": 1,
                    "title": "삼성전자 리포트",
                    "company_name": "삼성전자",
                    "broker": "테스트증권",
                    "symbol": "005930",
                    "published_at": "2026-02-18",
                },
                {
                    "report_id": 20,
                    "title": "피노 리포트",
                    "company_name": "피노",
                    "broker": "테스트증권",
                    "symbol": "033790",
                    "published_at": "2026-02-18",
                },
            ]
        if symbol == "005930":
            return [
                {
                    "report_id": 1,
                    "title": "삼성전자 리포트",
                    "broker": "테스트증권",
                    "published_at": "2026-02-18",
                },
                {
                    "report_id": 2,
                    "title": "삼성전자 이전 리포트",
                    "broker": "테스트증권",
                    "published_at": "2026-02-10",
                },
            ]
        return [
            {
                "report_id": 10,
                "title": f"{symbol} 리포트",
                "broker": "테스트증권",
                "published_at": "2026-02-18",
            }
        ]

    def get_report_facts(self, report_id: int) -> dict | None:
        if int(report_id) == 1:
            return {
                "rating": "BUY",
                "target_price": {"value": 98000, "currency": "KRW", "changed": "UP"},
                "risks": ["메모리 수요 둔화 가능성"],
                "catalysts": ["HBM 수요"],
                "evidence_quotes": [
                    {"page": 5, "tag": "target_price", "text": "목표주가 9.8만원"}
                ],
            }
        if int(report_id) == 2:
            return {
                "rating": "BUY",
                "target_price": {
                    "value": 90000,
                    "currency": "KRW",
                    "changed": "UNCHANGED",
                },
                "risks": ["원가 변동"],
                "catalysts": [],
                "evidence_quotes": [
                    {"page": 4, "tag": "target_price", "text": "목표주가 9.0만원"}
                ],
            }
        return {
            "rating": "HOLD",
            "target_price": {"value": 0, "currency": "KRW", "changed": "UNKNOWN"},
            "risks": ["근거 제한"],
            "catalysts": [],
            "evidence_quotes": [{"page": 3, "tag": "risk", "text": "리스크 구간"}],
        }


class _RagStore:
    available = True

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
        _ = (query, symbol, limit, broker, doc_id, date_from, date_to)
        return []


class _Kis:
    async def fetch_domestic_quote(self, symbol: str) -> dict:
        mapping = {
            "005930": {
                "name": "삼성전자",
                "price": 74500,
                "raw": {"stck_prdy_ctrt": "2.1"},
            },
            "000660": {
                "name": "SK하이닉스",
                "price": 130000,
                "raw": {"stck_prdy_ctrt": "-1.5"},
            },
            "005380": {
                "name": "현대차",
                "price": 213000,
                "raw": {"stck_prdy_ctrt": "0.8"},
            },
        }
        return mapping.get(symbol, {"name": symbol, "price": 0, "raw": {}})


def _service(tmp_path: Path) -> PortfolioCoachService:
    return PortfolioCoachService(
        PortfolioCoachConfig(
            state_db_path=str(tmp_path / "portfolio_coach.db"),
            user_id="u1",
            llm_bridge_command="",
            top_n=5,
            option_count=3,
            trigger_count=3,
            review_queue_enabled=True,
        ),
        holdings_provider=_HoldingsProvider(),
        report_repo=_ReportRepo(),
        rag_store=_RagStore(),
        kis=_Kis(),
    )


def test_portfolio_coach_builds_actionable_pack_with_review_queue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    payload = asyncio.run(service.build_advice())

    assert payload["status"] == "pending_review"
    assert int(payload.get("message_id") or 0) > 0
    message = str(payload["message"])
    assert message.startswith("[Portfolio Coach]")
    assert "## 시장 분위기" in message
    assert "## 내 포트폴리오 스냅샷" in message
    assert "## 오늘의 실행안" in message
    assert "## 장투 모델 포트폴리오 제안" in message
    assert "## 리서치 근거/커버리지 + 다음 액션" in message
    assert message.count(") [") == 3
    assert "[REDUCE]" in message or "[SELL]" in message
    assert "사라" not in message
    assert "팔아라" not in message
    assert "근거 부족 체크" not in message
    used = list(payload.get("used_candidates") or [])
    assert len(used) == 3
    pack = dict(payload.get("pack") or {})
    strategy_spec = dict(pack.get("strategy_spec") or {})
    seed = dict(pack.get("advice_seed_json") or {})
    model_portfolio = dict(seed.get("model_portfolio") or {})
    target_cash_weight = float(model_portfolio.get("target_cash_weight") or -1)
    assert 0.03 <= target_cash_weight <= 0.35
    assert strategy_spec.get("target_cash_weight") == target_cash_weight
    history_rows = service.store.list_recent_rebalance_history(user_id="u1", limit=5)
    assert len(history_rows) == 1
    assert len(list(history_rows[0].get("targets") or [])) >= 1


def test_portfolio_coach_includes_rebalance_history_context(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.store.write_advice_message(
        as_of="2026-02-18T00:00:00+09:00",
        user_id="u1",
        message_md="seed history",
        used_candidates=[],
        holdings_hash="h1",
        candidate_hash="c1",
        status="sent",
        rebalance_targets=[
            {"ticker": "005930", "target_weight": 0.28},
            {"ticker": "000660", "target_weight": 0.24},
        ],
    )

    payload = asyncio.run(service.build_advice())
    pack = dict(payload.get("pack") or {})
    seed = dict(pack.get("advice_seed_json") or {})
    coverage = dict(seed.get("evidence_coverage") or {})
    history = list(coverage.get("rebalance_history") or [])
    notes = list((dict(seed.get("action_plan") or {})).get("notes") or [])

    assert len(history) >= 1
    assert any("급변" in str(note) for note in notes)


def test_portfolio_coach_dedupe_by_date_holdings_candidates(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = asyncio.run(service.build_advice())
    assert first["status"] == "pending_review"

    second = asyncio.run(service.build_advice())
    assert second["status"] == "skipped"
    assert second["reason"] == "duplicate_daily_message"


def test_portfolio_coach_fills_citation_gap_with_geungeo_bujok(tmp_path: Path) -> None:
    class _NoFactRepo(_ReportRepo):
        def get_report_facts(self, report_id: int) -> dict | None:
            _ = report_id
            return None

    service = PortfolioCoachService(
        PortfolioCoachConfig(
            state_db_path=str(tmp_path / "portfolio_coach.db"),
            user_id="u1",
            llm_bridge_command="",
            review_queue_enabled=True,
        ),
        holdings_provider=_HoldingsProvider(),
        report_repo=_NoFactRepo(),
        rag_store=_RagStore(),
        kis=_Kis(),
    )
    payload = asyncio.run(service.build_advice())
    message = str(payload["message"])
    assert "보류(근거 부족" in message


def test_portfolio_coach_single_holding_uses_scan_friendly_markdown(
    tmp_path: Path,
) -> None:
    class _OneHoldingProvider:
        async def get_snapshot(self, user_id: str) -> dict:
            return {
                "user_id": user_id,
                "as_of": "2026-02-19T01:41:00+09:00",
                "positions": [
                    {
                        "ticker": "033790",
                        "name": "피노",
                        "quantity": 360,
                        "avg_price": 5881,
                        "cost_basis": 2117160,
                        "market_value": 2012400,
                        "weight": 1.0,
                    }
                ],
                "cash": 0,
            }

    class _NoReportRepo:
        def search(
            self, query: str, symbol: str = "", category: str = "", limit: int = 10
        ) -> list[dict]:
            _ = (query, symbol, category, limit)
            return []

        def get_report_facts(self, report_id: int) -> dict | None:
            _ = report_id
            return None

    class _OneKis:
        async def fetch_domestic_quote(self, symbol: str) -> dict:
            return {"name": "피노", "price": 5590, "raw": {"stck_prdy_ctrt": "-0.7"}}

    service = PortfolioCoachService(
        PortfolioCoachConfig(
            state_db_path=str(tmp_path / "portfolio_coach.db"),
            user_id="u1",
            llm_bridge_command="",
            review_queue_enabled=True,
        ),
        holdings_provider=_OneHoldingProvider(),
        report_repo=_NoReportRepo(),
        rag_store=_RagStore(),
        kis=_OneKis(),
    )

    payload = asyncio.run(service.build_advice())
    message = str(payload["message"])
    assert "피노(033790)" in message
    assert "## 오늘의 실행안" in message
    assert message.count(") [") == 3
    assert "제안 보류" in message
    assert "리포트 인덱싱" in message or "lookback" in message
    assert "근거 부족 체크" not in message


def test_portfolio_coach_idea_section_keeps_minimum_rows_with_single_citation(
    tmp_path: Path,
) -> None:
    class _WideReportRepo:
        def list_recent_report_facts(
            self, lookback_days: int, limit: int
        ) -> list[dict]:
            _ = (lookback_days, limit)
            rows: list[dict] = []
            tickers = ["005930", "000660", "005380", "035420", "051910", "068270"]
            for idx, ticker in enumerate(tickers, start=1):
                rows.append(
                    {
                        "report_id": idx,
                        "symbol": ticker,
                        "company_name": f"종목{idx}",
                        "title": f"종목{idx} 리포트",
                        "broker": "테스트증권",
                        "category": "tech",
                        "published_at": "2026-02-18",
                        "rating": "BUY",
                        "target_price_value": 120000 + (idx * 1000),
                        "catalysts": ["실적 모멘텀"],
                        "risks": ["수요 둔화"],
                        "investment_thesis": ["밸류에이션 매력"],
                        "evidence_quotes": [
                            {"page": idx, "text": "핵심 근거"},
                            {"page": idx + 10, "text": "추가 근거"},
                        ],
                    }
                )
            return rows

        def search(
            self, query: str, symbol: str = "", category: str = "", limit: int = 10
        ) -> list[dict]:
            _ = (query, symbol, category, limit)
            return []

        def get_report_facts(self, report_id: int) -> dict | None:
            _ = report_id
            return None

    class _WideKis:
        async def fetch_domestic_quote(self, symbol: str) -> dict:
            prices = {
                "005930": 74000,
                "000660": 130000,
                "005380": 212000,
                "035420": 180000,
                "051910": 320000,
                "068270": 160000,
            }
            return {
                "name": f"종목{symbol[-2:]}",
                "price": prices.get(symbol, 100000),
                "raw": {"stck_prdy_ctrt": "0.3"},
            }

    service = PortfolioCoachService(
        PortfolioCoachConfig(
            state_db_path=str(tmp_path / "portfolio_coach.db"),
            user_id="u1",
            llm_bridge_command="",
            review_queue_enabled=True,
        ),
        holdings_provider=_HoldingsProvider(),
        report_repo=_WideReportRepo(),
        rag_store=_RagStore(),
        kis=_WideKis(),
    )

    payload = asyncio.run(service.build_advice())
    message = str(payload["message"])
    assert message.count(") **") >= 5


def test_portfolio_coach_name_format_uses_company_name_first_label(
    tmp_path: Path,
) -> None:
    class _NoNameKis:
        async def fetch_domestic_quote(self, symbol: str) -> dict:
            return {"name": symbol, "price": 5590, "raw": {"stck_prdy_ctrt": "-0.7"}}

    class _EmptyRepo:
        def search(
            self, query: str, symbol: str = "", category: str = "", limit: int = 10
        ) -> list[dict]:
            _ = (query, symbol, category, limit)
            return []

        def get_report_facts(self, report_id: int) -> dict | None:
            _ = report_id
            return None

    class _OneHoldingProviderNoName:
        async def get_snapshot(self, user_id: str) -> dict:
            return {
                "user_id": user_id,
                "as_of": "2026-02-19T01:41:00+09:00",
                "positions": [
                    {
                        "ticker": "033790",
                        "name": "033790",
                        "quantity": 360,
                        "avg_price": 5881,
                        "cost_basis": 2117160,
                        "market_value": 2012400,
                        "weight": 1.0,
                    }
                ],
                "cash": 0,
            }

    service = PortfolioCoachService(
        PortfolioCoachConfig(
            state_db_path=str(tmp_path / "portfolio_coach.db"),
            user_id="u1",
            llm_bridge_command="",
            review_queue_enabled=True,
        ),
        holdings_provider=_OneHoldingProviderNoName(),
        report_repo=_EmptyRepo(),
        rag_store=_RagStore(),
        kis=_NoNameKis(),
    )

    payload = asyncio.run(service.build_advice())
    message = str(payload["message"])
    assert "033790" in message


def test_kis_holdings_provider_weight_uses_total_assets_with_cash() -> None:
    class _KisBalance:
        async def fetch_balance_assets(self) -> list[dict]:
            return [
                {"asset": "KRW", "kind": "cash", "value_krw": 100000},
                {
                    "asset": "033790",
                    "asset_name": "피노",
                    "kind": "position",
                    "qty": 10,
                    "avg_price": 10000,
                    "value_krw": 100000,
                },
            ]

    provider = KISHoldingsProvider(_KisBalance())
    snapshot = asyncio.run(provider.get_snapshot("u1"))
    positions = list(snapshot.get("positions") or [])
    assert len(positions) == 1
    assert positions[0].get("weight") == 0.5
