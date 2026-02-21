from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradecraft.main import app, freqtrade_bridge, fx_rates, settings, telegram, upbit
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

    async def fake_fetch_sessions(usdt_krw_rate: float) -> dict:
        _ = usdt_krw_rate
        return {"bots": [], "sessions": []}

    monkeypatch.setattr(freqtrade_bridge, "fetch_sessions", fake_fetch_sessions)


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
