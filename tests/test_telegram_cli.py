from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradecraft import main as tradecraft_main
from tradecraft.main import app, fx_rates, settings, telegram, upbit
from tradecraft.services.telegram_cli import TelegramCLI


@pytest.fixture(autouse=True)
def disable_upbit_network_for_telegram_tests(monkeypatch) -> None:
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")

    async def fake_get_snapshot() -> dict:
        return {
            "usdt_krw": 1480.0,
            "usd_krw": 1443.0,
            "usdt_source": "upbit",
            "usd_source": "manana",
            "status": "ok",
            "fetched_at": "2026-02-15T09:00:00+00:00",
        }

    monkeypatch.setattr(fx_rates, "get_snapshot", fake_get_snapshot)


def test_webhook_help_command_dispatches_reply(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)

    with TestClient(app) as client:
        payload = {"message": {"text": "/help", "chat": {"id": telegram.config.chat_id or "test"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "HERMES BOT Telegram CLI" in sent_messages[0][0]
    assert "/status" in sent_messages[0][0]
    assert "/ask" in sent_messages[0][0]
    assert sent_messages[0][1] is None
    assert sent_messages[0][2] == str(telegram.config.chat_id or "test")


def test_webhook_ignores_non_command_text(monkeypatch) -> None:
    async def fake_send_message(
        _: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        raise AssertionError("send_message should not be called for plain text")

    monkeypatch.setattr(telegram, "send_message", fake_send_message)

    with TestClient(app) as client:
        payload = {"message": {"text": "hello", "chat": {"id": telegram.config.chat_id or "test"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is False


def test_webhook_replies_to_request_chat_id(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(telegram.config, "chat_id", "1000000000")

    with TestClient(app) as client:
        payload = {"message": {"text": "/status", "chat": {"id": "999999"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert sent_messages[0][2] == "999999"


def test_webhook_status_contains_fx_summary(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)

    with TestClient(app) as client:
        payload = {"message": {"text": "/status", "chat": {"id": telegram.config.chat_id or "test"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert sent_messages
    assert "USDT/KRW:" in sent_messages[0][0]
    assert "USD/KRW:" in sent_messages[0][0]
    assert "FX 상태:" in sent_messages[0][0]
    assert sent_messages[0][1] == "HTML"


def test_webhook_balance_command_renders_table(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)

    with TestClient(app) as client:
        payload = {"message": {"text": "/balance upbit", "chat": {"id": telegram.config.chat_id or "test"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "업비트" in sent_messages[0][0]
    assert "ASSET" not in sent_messages[0][0]
    assert "NAME" in sent_messages[0][0]
    assert "QTY" in sent_messages[0][0]
    assert "VAL" in sent_messages[0][0]
    assert "PNL" in sent_messages[0][0]
    assert "Bitcoin" in sent_messages[0][0] or "BTC" in sent_messages[0][0]
    assert sent_messages[0][1] == "HTML"
    assert sent_messages[0][2] == str(telegram.config.chat_id or "test")


def test_webhook_binancef_shortcut_renders_futures_venue(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)

    with TestClient(app) as client:
        payload = {"message": {"text": "/binancef", "chat": {"id": telegram.config.chat_id or "test"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "바이낸스 선물" in sent_messages[0][0]
    assert sent_messages[0][1] == "HTML"


def test_webhook_balance_command_uses_runtime_upbit_assets(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 1000.0,
                "available": 1000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 1000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "TEST",
                "kind": "position",
                "qty": 2.0,
                "available": 2.0,
                "locked": 0.0,
                "avg_price": 5000.0,
                "mark_price": 6000.0,
                "value_krw": 12000.0,
                "pnl_krw": 2000.0,
            },
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "dummy")
    monkeypatch.setattr(settings, "upbit_secret_key", "dummy")
    monkeypatch.setattr(upbit, "fetch_balance_assets", fake_fetch_balance_assets)
    monkeypatch.setattr(telegram, "send_message", fake_send_message)

    with TestClient(app) as client:
        payload = {"message": {"text": "/balance upbit", "chat": {"id": telegram.config.chat_id or "test"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "TEST" in sent_messages[0][0]


def test_webhook_uses_fixed_chat_id_when_message_chat_missing(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(telegram.config, "chat_id", "1000000000")

    with TestClient(app) as client:
        payload = {"message": {"text": "/help"}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert sent_messages[0][2] is None


def test_webhook_strategy_ask_uses_strategy_intelligence(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    class _FakeStrategyIntelligence:
        async def build_brief(
            self,
            *,
            query: str,
            research_feed: dict | None,
            use_llm: bool = False,
            limit: int | None = None,
        ) -> dict:
            assert query == "다음 거래일 관심 후보"
            assert research_feed is None
            assert use_llm is True
            assert limit == 5
            return {
                "status": "ok",
                "query": query,
                "model": "gpt-5.5",
                "brief_mode": "llm",
                "brief_md": "전략 브리핑 본문",
                "regime": {"label": "mixed", "stance": "확인 후 진입"},
                "candidates": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "score": 90,
                        "score_components": {"fit": 81},
                        "suitability": {
                            "balanced": {"score": 82, "grade": "A"},
                            "short_term": {"score": 84, "grade": "A"},
                            "mid_term": {"score": 78, "grade": "B"},
                            "long_term": {"score": 70, "grade": "B"},
                        },
                    }
                ],
                "sources": [
                    {"source_id": "whale_insight", "label": "Whale Insight", "status": "ok", "count": 1}
                ],
            }

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(tradecraft_main, "strategy_intelligence", _FakeStrategyIntelligence())
    monkeypatch.setattr(tradecraft_main, "_read_strategy_research_feed", lambda: None)

    with TestClient(app) as client:
        payload = {"message": {"text": "/ask 다음 거래일 관심 후보", "chat": {"id": "999999"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "HERMES 전략 인텔리전스" in sent_messages[0][0]
    assert "gpt-5.5" in sent_messages[0][0]
    assert "삼성전자(005930)" in sent_messages[0][0]
    assert "균형 A 82" in sent_messages[0][0]
    assert sent_messages[0][1] == "HTML"
    assert sent_messages[0][2] == "999999"


def test_webhook_watchlist_uses_strategy_intelligence(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    class _FakeStrategyIntelligence:
        async def build_brief(
            self,
            *,
            query: str,
            research_feed: dict | None,
            use_llm: bool = False,
            limit: int | None = None,
        ) -> dict:
            assert query == "다음 거래일 관심 후보를 전략적으로 정리해줘"
            assert research_feed is None
            assert use_llm is False
            assert limit == 8
            return {
                "status": "ok",
                "query": query,
                "model": "gpt-5.5",
                "brief_mode": "deterministic",
                "regime": {"label": "mixed", "stance": "확인 후 진입"},
                "candidates": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "score": 90,
                        "checks": ["거래대금 확인"],
                        "reasons": ["고래/수급 중첩"],
                        "suitability": {
                            "balanced": {"score": 82, "grade": "A"},
                            "short_term": {"score": 88, "grade": "A"},
                            "mid_term": {"score": 76, "grade": "B"},
                            "long_term": {"score": 62, "grade": "C"},
                        },
                        "score_components": {
                            "fit": 81,
                            "whale": 88,
                            "after_close": 76,
                        },
                        "data_warnings": ["밸류 미수집"],
                    }
                ],
            }

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(tradecraft_main, "strategy_intelligence", _FakeStrategyIntelligence())
    monkeypatch.setattr(tradecraft_main, "_read_strategy_research_feed", lambda: None)

    with TestClient(app) as client:
        payload = {"message": {"text": "/watchlist", "chat": {"id": "999999"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["sent"] is True
    assert "HERMES 전략 Watchlist" in sent_messages[0][0]
    assert "삼성전자(005930)" in sent_messages[0][0]
    assert "균형 A 82" in sent_messages[0][0]
    assert "단기 A / 중기 B / 장기 C" in sent_messages[0][0]
    assert "자료: 밸류 미수집" in sent_messages[0][0]
    assert sent_messages[0][1] == "HTML"


def test_webhook_why_uses_strategy_intelligence(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    class _FakeStrategyIntelligence:
        async def build_brief(
            self,
            *,
            query: str,
            research_feed: dict | None,
            use_llm: bool = False,
            limit: int | None = None,
        ) -> dict:
            assert query == "005930 왜 후보인지 근거와 반론을 설명해줘"
            assert research_feed is None
            assert use_llm is True
            assert limit == 8
            return {
                "status": "ok",
                "query": query,
                "model": "gpt-5.5",
                "brief_mode": "llm",
                "candidates": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "score": 90,
                        "confidence": 75,
                        "stance": "watch",
                        "reasons": ["HBM 실적 개선"],
                        "checks": ["시초 갭 확인"],
                        "risks": ["단기 변동성"],
                        "facts": ["목표주가 상향 근거 존재"],
                        "citations": ["[테스트증권, 2026-04-30, p.3]"],
                        "report_ids": [101],
                        "suitability": {
                            "balanced": {"score": 82, "grade": "A"},
                            "short_term": {
                                "score": 88,
                                "grade": "A",
                                "drivers": ["수급 88", "리포트 80"],
                            },
                            "mid_term": {
                                "score": 76,
                                "grade": "B",
                                "drivers": ["밸류 70", "성장 64"],
                            },
                            "long_term": {
                                "score": 62,
                                "grade": "C",
                                "drivers": ["퀄리티 62"],
                            },
                        },
                        "score_components": {
                            "report": 80,
                            "research": 60,
                            "whale": 88,
                            "after_close": 76,
                            "risk_penalty": 20,
                        },
                        "data_warnings": ["밸류 미수집", "고래 없음"],
                    }
                ],
            }

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(tradecraft_main, "strategy_intelligence", _FakeStrategyIntelligence())
    monkeypatch.setattr(tradecraft_main, "_read_strategy_research_feed", lambda: None)

    with TestClient(app) as client:
        payload = {"message": {"text": "/why 005930", "chat": {"id": "999999"}}}
        res = client.post("/api/telegram/webhook", json=payload)

    assert res.status_code == 200
    assert res.json()["sent"] is True
    assert "HERMES 후보 상세" in sent_messages[0][0]
    assert "기간별: 단기 A 88 / 중기 B 76 / 장기 C 62" in sent_messages[0][0]
    assert "자료 상태: 밸류 미수집 · 고래 없음" in sent_messages[0][0]
    assert "단기: 수급 88; 리포트 80" in sent_messages[0][0]
    assert "HBM 실적 개선" in sent_messages[0][0]
    assert "단기 변동성" in sent_messages[0][0]
    assert "리포트: 101" in sent_messages[0][0]
    assert sent_messages[0][1] == "HTML"


def test_render_table_aligns_pipes_with_korean_text() -> None:
    cli = TelegramCLI(lambda: {})
    table = cli._render_table(
        ["ASSET", "QTY", "VAL"],
        [
            ["한화오션", "12", "1200000"],
            ["SOL", "1.23", "345000"],
            ["삼성전자", "2", "150000"],
        ],
    )

    def pipe_positions(line: str) -> list[int]:
        return [idx for idx, ch in enumerate(line) if ch == "|"]

    lines = [line for line in table.splitlines() if "|" in line]
    assert lines
    baseline = pipe_positions(lines[0])
    assert baseline
    for line in lines[1:]:
        assert pipe_positions(line) == baseline


def test_market_judgment_text_shows_account_action_and_position() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.market_judgment_text(
        {
            "status": "ok",
            "run": {"mode": "llm", "model": "gpt-5.5", "market_session": "regular"},
            "account": {
                "cash_krw": 1_000_000,
                "position_value_krw": 760_000,
                "position_count": 1,
            },
            "judgments": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "account_action": "risk_check",
                    "stance": "risk_check",
                    "confidence": 0.73,
                    "quote": {"price": 76000, "change_pct": -1.55},
                    "reasons": ["국장1 보유 손익과 장중 약세를 함께 확인"],
                    "risks": ["추가 하락 시나리오"],
                }
            ],
        }
    )

    assert "HERMES 장중 판단" in text
    assert "리스크 관리" in text
    assert "국장1: 현금 1,000,000 원" in text
    assert "삼성전자(005930)" in text


def test_market_why_now_text_shows_quote_and_position() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.market_why_now_text(
        {
            "judgments": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "account_action": "hold",
                    "stance": "hold",
                    "horizon": "short_term",
                    "confidence": 0.55,
                    "quote": {
                        "price": 76000,
                        "change_pct": -1.55,
                        "source": "kis",
                        "status": "ok",
                    },
                    "position": {
                        "symbol": "005930",
                        "value_krw": 760000,
                        "unrealized_pnl_krw": -40000,
                        "unrealized_pnl_pct": -5.0,
                        "position_weight": 0.43,
                    },
                    "strategy": {"score": 72, "confidence": 66},
                    "reasons": ["국장1 보유 종목"],
                    "risks": ["단기 약세"],
                }
            ]
        },
        "005930",
    )

    assert "HERMES 왜 지금?" in text
    assert "현재 시세" in text
    assert "국장1 보유" in text
    assert "전략 레이어" in text
