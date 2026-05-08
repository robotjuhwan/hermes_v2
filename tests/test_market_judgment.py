from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradecraft.services.market_judgment import (
    MarketJudgmentConfig,
    MarketJudgmentEngine,
    build_market_clock,
    normalize_account_assets,
    normalize_kis_quote,
)


class _OpenCalendar:
    def is_open_day(self, value) -> bool:
        _ = value
        return True


class _FakeKIS:
    async def fetch_balance_assets(self) -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "value_krw": 1_000_000.0,
                "qty": 1_000_000.0,
            },
            {
                "asset": "005930",
                "asset_name": "삼성전자",
                "kind": "position",
                "qty": 10.0,
                "available": 8.0,
                "avg_price": 80000.0,
                "mark_price": 76000.0,
                "value_krw": 760000.0,
                "pnl_krw": -40000.0,
            },
        ]

    async def fetch_domestic_quote(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "name": "삼성전자" if symbol == "005930" else symbol,
            "price": 76000.0,
            "raw": {
                "stck_prpr": "76000",
                "prdy_vrss": "-1200",
                "prdy_ctrt": "-1.55",
                "stck_oprc": "77000",
                "stck_hgpr": "78000",
                "stck_lwpr": "75000",
                "acml_vol": "1234567",
                "acml_tr_pbmn": "90000000000",
                "hts_kor_isnm": "삼성전자" if symbol == "005930" else symbol,
            },
        }


class _FailingKIS(_FakeKIS):
    async def fetch_balance_assets(self) -> list[dict]:
        raise RuntimeError("token rate limited")


class _FakeStrategy:
    def build_candidates(self, *, query, research_feed, limit=None) -> dict:
        _ = (query, research_feed, limit)
        return {
            "status": "ok",
            "score_method_version": "v2",
            "candidates": [
                {
                    "symbol": "000660",
                    "name": "SK하이닉스",
                    "score": 76,
                    "confidence": 68,
                    "reasons": ["HBM 수요"],
                    "risks": ["변동성"],
                }
            ],
            "exclusions": [],
            "sources": [],
        }

    def list_external_signals(self, **kwargs) -> dict:
        _ = kwargs
        return {"status": "ok", "items": [{"symbol": "402340"}]}


class _FakeLLM:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self) -> None:
        self.last_payload: dict | None = None

    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.last_payload = payload
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "judgments": [
                        {
                            "symbol": "005930",
                            "stance": "risk_check",
                            "account_action": "risk_check",
                            "horizon": "short_term",
                            "confidence": 0.73,
                            "reasons": ["국장1 보유 손익과 장중 약세를 함께 확인"],
                            "risks": ["추가 하락 시나리오"],
                            "triggers": ["거래대금 확인"],
                            "data_gaps": ["고래"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        }


def test_market_clock_sessions_respect_kst_open_window() -> None:
    clock = build_market_clock(
        now=datetime(2026, 5, 7, 9, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    assert clock["session"] == "regular"
    assert clock["is_market_open"] is True


def test_normalize_account_assets_computes_position_weight_and_pnl_pct() -> None:
    payload = normalize_account_assets(
        [
            {"asset": "KRW", "kind": "cash", "value_krw": 1_000_000},
            {
                "asset": "005930",
                "asset_name": "삼성전자",
                "kind": "position",
                "qty": 10,
                "avg_price": 80000,
                "value_krw": 760000,
                "pnl_krw": -40000,
            },
        ]
    )

    position = payload["positions"][0]
    assert payload["cash_krw"] == pytest.approx(1_000_000)
    assert position["unrealized_pnl_pct"] == pytest.approx(-5.0)
    assert position["position_weight"] == pytest.approx(760000 / 1760000)


def test_normalize_kis_quote_extracts_intraday_fields() -> None:
    quote = normalize_kis_quote(
        {
            "symbol": "005930",
            "name": "삼성전자",
            "price": 76000,
            "raw": {
                "stck_prpr": "76000",
                "prdy_vrss": "-1200",
                "prdy_ctrt": "-1.55",
                "acml_vol": "123",
            },
        },
        fetched_at="2026-05-07T00:00:00+00:00",
    )

    assert quote["price"] == pytest.approx(76000)
    assert quote["change_pct"] == pytest.approx(-1.55)
    assert quote["volume"] == pytest.approx(123)


def test_market_judgment_run_includes_account_and_position_first(tmp_path: Path) -> None:
    llm = _FakeLLM()
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        llm_bridge=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        watchlist=["178920"],
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["status"] == "ok"
    assert result["focus_symbols"][0] == "005930"
    assert result["account"]["positions"][0]["symbol"] == "005930"
    assert result["judgments"][0]["account_action"] == "risk_check"
    assert llm.last_payload is not None
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert prompt["account"]["cash_krw"] == pytest.approx(1_000_000)
    assert prompt["symbols"][0]["position"]["symbol"] == "005930"
    latest = engine.latest_judgment()
    assert latest["judgments"][0]["symbol"] == "005930"
    assert latest["account"]["position_count"] == 1

    quote_only = asyncio.run(engine.run_once(use_llm=False))
    assert quote_only["status"] == "quotes_only"
    assert quote_only["run_id"] == 0
    assert engine.latest_judgment()["run"]["mode"] == "llm"


def test_collect_account_uses_stale_snapshot_on_fetch_error(tmp_path: Path) -> None:
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        llm_bridge=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    ok_account = asyncio.run(engine.collect_account())
    engine.kis = _FailingKIS()  # type: ignore[assignment]
    stale_account = asyncio.run(engine.collect_account())

    assert ok_account["status"] == "ok"
    assert stale_account["status"] == "stale"
    assert stale_account["positions"][0]["symbol"] == "005930"
    assert "token rate limited" in stale_account["error_message"]
