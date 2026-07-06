from __future__ import annotations

import json
from pathlib import Path

from tradecraft.services.kis_notifications import (
    format_reconciled_order_message,
    has_order_notification,
)

ROOT = Path(__file__).resolve().parents[1]


def test_has_order_notification_matches_order_status_and_filled_qty() -> None:
    events = [
        {
            "event_type": "telegram_notified",
            "payload": {
                "order_id": 10,
                "status": "filled",
                "filled_qty": 2,
            },
        },
        {
            "event_type": "order_reconciled",
            "payload": {
                "order_id": 10,
                "status": "filled",
                "filled_qty": 2,
            },
        },
    ]

    assert has_order_notification(
        events,
        order_id=10,
        order_status="filled",
        filled_qty=2,
    )
    assert not has_order_notification(
        events,
        order_id=10,
        order_status="partially_filled",
        filled_qty=2,
    )


def test_format_reconciled_order_message_prefers_kis_product_name_when_block_name_is_code() -> None:
    message = format_reconciled_order_message(
        order={
            "symbol": "005930",
            "side": "buy",
            "limit_price": 76_000,
            "response_json": json.dumps({"prdt_name": "삼성전자"}, ensure_ascii=False),
        },
        match={
            "filled_qty": 1,
            "avg_fill_price": 76_000,
            "raw": {"prdt_name": "삼성전자"},
        },
        block={
            "block_id": "blk_005930_1",
            "symbol": "005930",
            "name": "005930",
            "target_price": 80_000,
            "stop_price": 74_000,
            "thesis": "코드명 블록 알림 테스트",
        },
        filled_qty=1,
    )

    assert "삼성전자 (005930)" in message
    assert "목표 80,000원 · 손절 74,000원" in message
    assert "가설: 코드명 블록 알림 테스트" in message


def test_format_reconciled_order_message_summarizes_sell_pnl() -> None:
    message = format_reconciled_order_message(
        order={
            "symbol": "005930",
            "side": "sell",
            "reason": "target_reached",
            "limit_price": 81_000,
        },
        match={"filled_qty": 2, "avg_fill_price": 81_000},
        block={
            "block_id": "blk_005930_1",
            "symbol": "005930",
            "name": "삼성전자",
            "entry_price": 76_000,
        },
        filled_qty=2,
    )

    assert "쥬 블록 청산 체결" in message
    assert "삼성전자 (005930)" in message
    assert "사유 목표가 도달 (target_reached)" in message
    assert "블록 손익 +10,000원" in message


def test_kis_notification_helpers_live_outside_block_trader() -> None:
    trader_source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()
    helper_source = (ROOT / "src/tradecraft/services/kis_notifications.py").read_text()

    assert "def has_order_notification(" in helper_source
    assert "def format_reconciled_order_message(" in helper_source
    assert "def _has_order_notification(" not in trader_source
    assert "def _format_reconciled_order_message(" not in trader_source
