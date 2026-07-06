from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradecraft import main as tradecraft_main
from tradecraft.main import app, fx_rates, settings, telegram, upbit
from tradecraft.services.telegram_cli import TelegramCLI


def _webhook_headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}


@pytest.fixture(autouse=True)
def disable_upbit_network_for_telegram_tests(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_webhook_secret", "webhook-secret")
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(telegram.config, "bot_token", "")
    monkeypatch.setattr(telegram.config, "chat_id", "999999")
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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

    assert res.status_code == 200
    assert res.json()["handled"] is False


def test_webhook_requires_secret_header(monkeypatch) -> None:
    with TestClient(app) as client:
        payload = {"message": {"text": "/help", "chat": {"id": telegram.config.chat_id}}}
        missing = client.post("/api/telegram/webhook", json=payload)
        invalid = client.post(
            "/api/telegram/webhook",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )

    assert missing.status_code == 401
    assert missing.json()["detail"] == "unauthorized"
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "unauthorized"


def test_webhook_ignores_unapproved_chat_id(monkeypatch) -> None:
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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

    assert res.status_code == 200
    assert res.json()["handled"] is False
    assert res.json()["sent"] is False
    assert res.json()["reason"] == "chat_not_allowed"
    assert not sent_messages


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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "업비트" in sent_messages[0][0]
    assert "ASSET" not in sent_messages[0][0]
    assert "(empty)" in sent_messages[0][0]
    assert "Bitcoin" not in sent_messages[0][0]
    assert "BTC" not in sent_messages[0][0]
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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "TEST" in sent_messages[0][0]


def test_webhook_ignores_missing_chat_id(monkeypatch) -> None:
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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

    assert res.status_code == 200
    assert res.json()["handled"] is False
    assert res.json()["sent"] is False
    assert res.json()["reason"] == "chat_not_allowed"
    assert not sent_messages


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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "HERMES 전략 인텔리전스" in sent_messages[0][0]
    assert "gpt-5.5" in sent_messages[0][0]
    assert "삼성전자 (005930)" in sent_messages[0][0]
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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

    assert res.status_code == 200
    assert res.json()["sent"] is True
    assert "HERMES 전략 Watchlist" in sent_messages[0][0]
    assert "삼성전자 (005930)" in sent_messages[0][0]
    assert "균형 A 82" in sent_messages[0][0]
    assert "단기 A / 중기 B / 장기 C" in sent_messages[0][0]
    assert "자료: 밸류 미수집" in sent_messages[0][0]
    assert sent_messages[0][1] == "HTML"


def test_strategy_watchlist_text_avoids_duplicate_code_only_name() -> None:
    cli = TelegramCLI(lambda: {})

    text = cli.strategy_watchlist_text(
        {
            "query": "테스트",
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "005930",
                    "score": 70,
                    "suitability": {"balanced": {"score": 70, "grade": "B"}},
                }
            ],
        }
    )

    assert "005930(005930)" not in text
    assert "005930 (005930)" not in text


def test_codex_lab_command_is_retired() -> None:
    cli = TelegramCLI(lambda: {})

    handled, text = cli.handle_text("/codexlab")

    assert handled is True
    assert "알 수 없는 명령어" in text
    assert "Codex Lab" not in text


def test_production_telegram_cli_retires_codex_lab_command() -> None:
    handled, text = tradecraft_main.telegram_cli.handle_text("/codexlab")

    assert handled is True
    assert "알 수 없는 명령어" in text
    assert "Codex Lab" not in text


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
        res = client.post("/api/telegram/webhook", json=payload, headers=_webhook_headers())

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


def test_telegram_analyze_symbol_command(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    class _FakeSymbolAnalysisService:
        async def run(
            self,
            symbol_or_name: str,
            *,
            trigger: str = "user_request",
            force_collect: bool = True,
        ) -> dict:
            assert symbol_or_name == "033790"
            assert trigger == "telegram"
            assert force_collect is True
            return {
                "status": "ok",
                "symbol": "033790",
                "name": "스피어",
                "analysis": {
                    "symbol": "033790",
                    "name": "스피어",
                    "stance": "watch",
                    "confidence": 0.81,
                    "summary": "단기 변동성은 있지만 추적 가치가 있습니다.",
                },
            }

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(
        tradecraft_main,
        "symbol_analysis_service",
        _FakeSymbolAnalysisService(),
    )

    with TestClient(app) as client:
        payload = {"message": {"text": "/analyze 033790", "chat": {"id": "999999"}}}
        res = client.post(
            "/api/telegram/webhook",
            json=payload,
            headers=_webhook_headers(),
        )

    assert res.status_code == 200
    assert res.json()["handled"] is True
    assert res.json()["sent"] is True
    assert sent_messages
    assert "스피어(033790)" in sent_messages[0][0]
    assert "stance: watch" in sent_messages[0][0]
    assert "confidence: 0.81" in sent_messages[0][0]
    assert "단기 변동성은 있지만 추적 가치가 있습니다." in sent_messages[0][0]
    assert sent_messages[0][1] is None
    assert sent_messages[0][2] == "999999"


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
    assert "삼성전자 (005930)" in text


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


def test_memory_status_text_shows_horizon_allocation() -> None:
    cli = TelegramCLI(lambda: {})

    text = cli.memory_status_text(
        {
            "status": "ok",
            "model": "gpt-5.5",
            "horizon_allocation": {
                "items": [
                    {"horizon": "cash", "current_weight": 0.30, "target_weight": 0.30},
                    {"horizon": "short", "current_weight": 0.10, "target_weight": 0.15},
                    {"horizon": "core_etf", "current_weight": 0.20, "target_weight": 0.10},
                ]
            },
            "active_policies": [],
            "today_journals": [],
        }
    )

    assert "현금 30%" in text
    assert "단기 10%" in text
    assert "ETF/Core 20%" in text


def test_memory_period_review_text_formats_review() -> None:
    cli = TelegramCLI(lambda: {})

    text = cli.memory_period_review_text(
        {
            "period_type": "weekly",
            "review": {
                "period_key": "2026-W21",
                "review_md": "중기 블록 손절 판단은 일중 노이즈와 분리한다.",
            },
            "revision_count": 2,
        }
    )

    assert "weekly 반성" in text
    assert "2026-W21" in text
    assert "정책 개정안 2개" in text


def test_memory_policy_revisions_text_formats_rows() -> None:
    cli = TelegramCLI(lambda: {})

    text = cli.memory_policy_revisions_text(
        {
            "items": [
                {
                    "policy_id": "prefer_mid_user_positions",
                    "status": "active_caution",
                    "scope": "user_position",
                }
            ]
        }
    )

    assert "정책 개정안" in text
    assert "prefer_mid_user_positions" in text
    assert "active_caution" in text


def test_webhook_memory_review_and_policy_commands(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []

    class _FakeMemoryService:
        async def run_period_review(
            self,
            *,
            period_type: str,
            context: dict,
            force: bool = False,
        ) -> dict:
            assert period_type == "weekly"
            assert force is True
            assert context == {"account": {"cash_krw": 1_000_000}}
            return {
                "status": "ok",
                "period_type": period_type,
                "review": {
                    "period_key": "2026-W21",
                    "review_md": "중기 블록 손절 판단은 일중 노이즈와 분리한다.",
                },
                "revision_count": 1,
            }

        def policy_revisions(self, *, status: str = "", limit: int = 30) -> dict:
            assert status == ""
            assert limit == 8
            return {
                "status": "ok",
                "items": [
                    {
                        "policy_id": "prefer_mid_user_positions",
                        "status": "active_caution",
                        "scope": "user_position",
                    }
                ],
            }

    async def fake_context() -> dict:
        return {"account": {"cash_krw": 1_000_000}}

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(tradecraft_main, "investment_memory_service", _FakeMemoryService())
    monkeypatch.setattr(tradecraft_main, "_build_investment_memory_context", fake_context)

    with TestClient(app) as client:
        review = client.post(
            "/api/telegram/webhook",
            json={"message": {"text": "/weekly-review", "chat": {"id": "999999"}}},
            headers=_webhook_headers(),
        )
        policy = client.post(
            "/api/telegram/webhook",
            json={"message": {"text": "/policy", "chat": {"id": "999999"}}},
            headers=_webhook_headers(),
        )

    assert review.status_code == 200
    assert policy.status_code == 200
    assert review.json()["sent"] is True
    assert policy.json()["sent"] is True
    assert "weekly 반성" in sent_messages[0][0]
    assert "정책 개정안" in sent_messages[1][0]
    assert "prefer_mid_user_positions" in sent_messages[1][0]
    assert sent_messages[0][1] == "HTML"
    assert sent_messages[1][1] == "HTML"


def test_webhook_reflect_runs_due_reflections(monkeypatch) -> None:
    sent_messages: list[tuple[str, str | None, str | None]] = []
    calls: list[dict] = []

    class _FakeMemoryService:
        def run_due_reflections(self, *, context: dict, force: bool = False) -> dict:
            calls.append({"context": context, "force": force})
            return {
                "status": "ok",
                "created_count": 1,
                "journal": {
                    "title": "블록 거래 반성",
                    "trading_day": "2026-05-21",
                    "slot": "block_reflection",
                    "message_md": "닫힌 블록 1건을 반성했습니다.",
                },
            }

    async def fake_context() -> dict:
        return {"blocks": {"blocks": [{"block_id": "blk_1", "status": "closed"}]}}

    async def fake_send_message(
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict:
        sent_messages.append((text, parse_mode, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(tradecraft_main, "investment_memory_service", _FakeMemoryService())
    monkeypatch.setattr(tradecraft_main, "_build_investment_memory_context", fake_context)

    with TestClient(app) as client:
        response = client.post(
            "/api/telegram/webhook",
            json={"message": {"text": "/reflect", "chat": {"id": "999999"}}},
            headers=_webhook_headers(),
        )

    assert response.status_code == 200
    assert response.json()["sent"] is True
    assert calls == [{"context": {"blocks": {"blocks": [{"block_id": "blk_1", "status": "closed"}]}}, "force": True}]
    assert "닫힌 블록 1건" in sent_messages[0][0]


def test_llm_usage_text_groups_components() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.llm_usage_text(
        {
            "trading_day": "2026-05-13",
            "total": {
                "call_count": 3,
                "total_tokens": 1200,
                "prompt_tokens": 900,
                "completion_tokens": 300,
                "estimated_token_count": 1,
                "error_count": 0,
            },
            "by_component": [
                {"component": "kis_block_manager", "call_count": 2, "total_tokens": 900},
                {"component": "investment_memory", "call_count": 1, "total_tokens": 300},
            ],
        }
    )

    assert "LLM 사용량" in text
    assert "1,200" in text
    assert "kis_block_manager" in text
    assert "investment_memory" in text


def test_live_authority_text_shows_venues_and_grade() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.live_authority_text(
        {
            "venues": {
                "kis": {
                    "live_grade": "qualified",
                    "max_budget_multiplier": 1.0,
                    "allow_scale_up": False,
                    "scorecard_count": 1,
                    "scorecards": [
                        {
                            "strategy_family": "mid",
                            "evidence_key": "all",
                            "grade": "qualified",
                            "sample_count": 8,
                            "win_rate": 50.0,
                        }
                    ],
                },
                "binance": {
                    "live_grade": "observe_only",
                    "max_budget_multiplier": 0.5,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "scorecards": [],
                },
            }
        }
    )

    assert "KIS: qualified" in text
    assert "BINANCE: observe_only" in text
    assert "mid / all" in text


def test_live_authority_text_shows_validation_gate_reason() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.live_authority_text(
        {
            "venues": {
                "kis": {
                    "live_grade": "scale_candidate",
                    "max_budget_multiplier": 1.0,
                    "allow_scale_up": False,
                    "scorecard_count": 2,
                    "validation_gate": {
                        "status": "validation_probe",
                        "readiness": "probe",
                        "reason": "validation_readiness_probe_not_scale_ready",
                        "fail_count": 0,
                    },
                    "trading_validation": {
                        "summary": {
                            "readiness": "probe",
                            "pass_count": 7,
                            "warn_count": 5,
                            "fail_count": 0,
                            "missing_count": 7,
                        }
                    },
                    "scorecards": [],
                },
                "binance": {
                    "live_grade": "qualified",
                    "max_budget_multiplier": 0.5,
                    "allow_scale_up": False,
                    "scorecard_count": 3,
                    "validation_gate": {
                        "status": "blocked_by_validation",
                        "readiness": "blocked_by_validation",
                        "reason": "readiness=blocked_by_validation, fail_count=1",
                        "fail_count": 1,
                        "risk_governor_action": "halt_new_risk",
                        "risk_governor_source": "ruin_profile",
                        "failed_disciplines": [
                            {
                                "id": "monte_carlo",
                                "label": "몬테카를로 시뮬레이션",
                                "status": "fail",
                            },
                            {
                                "id": "stress_test",
                                "label": "스트레스 테스트",
                                "status": "fail",
                            },
                        ],
                        "capacity_bottleneck": {
                            "status": "fail",
                            "capacity_method": "metadata_capacity_ratio",
                            "min_capacity_ratio": 0.79563,
                            "tightest_symbol": "023810",
                            "tightest_block_id": "blk_023810",
                        },
                        "failure_attribution": {
                            "recovery_focus": [
                                "symbol=ZECUSDT net -1.39, PF 0.00, expectancy -2.77%"
                            ]
                        },
                        "loss_cooldown": {
                            "symbols": [
                                {
                                    "symbol": "ZECUSDT",
                                    "total_net_pnl": -1.39,
                                    "profit_factor": 0.0,
                                    "expectancy_pct": -2.77,
                                    "action": "do_not_scale_or_create_live_entry_without_new_evidence",
                                }
                            ],
                            "groups": [
                                {
                                    "group_type": "strategy_family",
                                    "group": "volatile_attack",
                                    "profit_factor": 0.2,
                                    "action": "deprioritize_until_revalidated",
                                }
                            ],
                        },
                        "operator_guidance": [
                            "몬테카를로: sequence risk를 낮추기 전까지 size-up 금지",
                        ],
                    },
                    "trading_validation": {
                        "summary": {
                            "readiness": "blocked_by_validation",
                            "pass_count": 10,
                            "warn_count": 4,
                            "fail_count": 1,
                            "missing_count": 4,
                        }
                    },
                    "scorecards": [],
                },
            }
        }
    )

    assert "검증 Probe 단계" in text
    assert "readiness Probe 단계" in text
    assert "Probe 단계라 대기 진입 중심" in text
    assert "검증 검증 차단" in text
    assert "실패 항목 1개" in text
    assert "governor 신규 리스크 중단" in text
    assert "validation_probe" not in text
    assert "validation_readiness_probe_not_scale_ready" not in text
    assert "blocked_by_validation" not in text
    assert "fail 1" in text
    assert "실패 몬테카를로 시뮬레이션, 스트레스 테스트" in text
    assert "용량 병목 023810" in text
    assert "0.8x" in text
    assert "실패 귀속 symbol=ZECUSDT net -1.39" in text
    assert "손실 쿨다운 ZECUSDT" in text
    assert "신규 확대 금지" in text
    assert "volatile_attack" in text
    assert "재검증 전 우선순위 하향" in text
    assert "do_not_scale_or_create_live_entry_without_new_evidence" not in text
    assert "deprioritize_until_revalidated" not in text
    assert "조치 몬테카를로: sequence risk를 낮추기 전까지 size-up 금지" in text


def test_live_authority_text_shows_validation_passport() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.live_authority_text(
        {
            "venues": {
                "kis": {
                    "live_grade": "qualified",
                    "max_budget_multiplier": 0.5,
                    "allow_scale_up": False,
                    "scorecard_count": 1,
                    "validation_gate": {
                        "status": "blocked_by_validation",
                        "readiness": "blocked_by_validation",
                        "fail_count": 1,
                        "validation_passport": {
                            "version": "trading_validation_passport_v1",
                            "status": "blocked_by_validation",
                            "readiness": "blocked_by_validation",
                            "score": 42.5,
                            "expected_count": 19,
                            "actual_count": 18,
                            "row_detail_count": 2,
                            "row_detail_complete": False,
                            "is_complete": False,
                            "failed_ids": ["monte_carlo"],
                            "weak_ids": ["monte_carlo", "kelly_sizing"],
                            "requires_revalidation": True,
                            "risk_governor_action": "halt_new_risk",
                        },
                    },
                    "trading_validation": {
                        "summary": {
                            "readiness": "blocked_by_validation",
                            "pass_count": 16,
                            "warn_count": 2,
                            "fail_count": 1,
                            "missing_count": 0,
                        },
                    },
                    "scorecards": [],
                },
                "binance": {
                    "live_grade": "qualified",
                    "max_budget_multiplier": 1.0,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "scorecards": [],
                },
            }
        }
    )

    assert "검증 여권 재검증" in text
    assert "18/19" in text
    assert "row 2/19" in text
    assert "42.5점" in text
    assert "실패 monte_carlo" in text
    assert "취약 monte_carlo, kelly_sizing" in text
    assert "halt_new_risk" not in text


def test_live_authority_text_shows_repair_execution() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.live_authority_text(
        {
            "venues": {
                "kis": {
                    "live_grade": "qualified",
                    "max_budget_multiplier": 1.0,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "scorecards": [],
                },
                "binance": {
                    "live_grade": "restricted",
                    "max_budget_multiplier": 0.5,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "repair_execution": {
                        "status": "queued",
                        "executed_count": 0,
                        "queued_count": 1,
                        "m1_execution_posture": "sequential_priority_queue",
                        "actions": [
                            {
                                "discipline_id": "walk_forward_analysis",
                                "status": "queued_external_runner",
                                "validation_mode": "backtest_wfa_oos_rebuild",
                                "scale_up_blocked": True,
                                "live_shadow_required": True,
                            }
                        ],
                    },
                    "scorecards": [],
                },
            }
        }
    )

    assert "복구 실행 대기" in text
    assert "대기 1" in text
    assert "sequential_priority_queue" in text
    assert "복구 walk_forward_analysis" in text
    assert "외부 러너 대기" in text
    assert "backtest_wfa_oos_rebuild" in text
    assert "scale-up 차단" in text
    assert "live shadow 필요" in text
    assert "queued_external_runner" not in text


def test_live_authority_text_preserves_zero_budget_multiplier() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.live_authority_text(
        {
            "venues": {
                "kis": {
                    "live_grade": "restricted",
                    "max_budget_multiplier": 0.0,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "scorecards": [],
                },
                "binance": {
                    "live_grade": "observe_only",
                    "max_budget_multiplier": 0.5,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "scorecards": [],
                },
            }
        }
    )

    assert "KIS: restricted · 배수 0.0x" in text
    assert "KIS: restricted · 배수 -x" not in text


def test_live_authority_text_uses_user_facing_budget_and_error_reasons() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.live_authority_text(
        {
            "venues": {
                "kis": {
                    "live_grade": "restricted",
                    "max_budget_multiplier": 0.0,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "validation_gate": {
                        "status": "clear",
                        "readiness": "normal",
                        "reason": "live_authority_budget_zero",
                        "risk_governor_action": "halt_new_risk",
                        "risk_governor_source": "mdd_limit",
                    },
                    "trading_validation": {
                        "summary": {
                            "readiness": "normal",
                            "pass_count": 19,
                            "warn_count": 0,
                            "fail_count": 0,
                            "missing_count": 0,
                        }
                    },
                    "scorecards": [],
                },
                "binance": {
                    "live_grade": "error",
                    "max_budget_multiplier": 0.0,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "validation_gate": {
                        "status": "validation_error",
                        "readiness": "blocked_by_validation",
                        "reason": "live_authority_error",
                    },
                    "trading_validation": {
                        "summary": {
                            "readiness": "blocked_by_validation",
                            "pass_count": 0,
                            "warn_count": 0,
                            "fail_count": 1,
                            "missing_count": 18,
                        }
                    },
                    "scorecards": [],
                },
            }
        }
    )

    assert "신규 리스크 예산 0" in text
    assert "Live Authority 오류로 신규 리스크 중단" in text
    assert "governor 신규 리스크 중단 · MDD" in text
    assert "live_authority_budget_zero" not in text
    assert "live_authority_error" not in text
    assert "halt_new_risk" not in text


def test_live_authority_text_shows_incomplete_validation_count() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.live_authority_text(
        {
            "venues": {
                "kis": {
                    "live_grade": "scale_candidate",
                    "max_budget_multiplier": 0.5,
                    "allow_scale_up": False,
                    "scorecard_count": 2,
                    "validation_gate": {
                        "status": "validation_incomplete",
                        "readiness": "scale_ready",
                        "reason": "discipline_count=3,expected=19",
                        "discipline_count": 3,
                        "expected_discipline_count": 19,
                        "fail_count": 0,
                    },
                    "trading_validation": {
                        "payload": {"discipline_count": 3},
                        "summary": {
                            "readiness": "scale_ready",
                            "pass_count": 3,
                            "warn_count": 0,
                            "fail_count": 0,
                            "missing_count": 0,
                        },
                    },
                    "scorecards": [],
                },
                "binance": {
                    "live_grade": "qualified",
                    "max_budget_multiplier": 1.0,
                    "allow_scale_up": False,
                    "scorecard_count": 0,
                    "scorecards": [],
                },
            }
        }
    )

    assert "검증 19개 검증 미완성" in text
    assert "readiness 스케일 준비" in text
    assert "검증수 3/19" in text
    assert "검증 항목 수 부족: 3/19" in text
    assert "validation_incomplete" not in text
    assert "scale_ready" not in text
    assert "discipline_count=3,expected=19" not in text
