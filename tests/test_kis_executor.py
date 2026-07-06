from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradecraft.services.kis_executor import (
    is_order_stale,
    match_inquired_order,
    order_query_start_date,
    status_from_order_fill,
)


def test_kis_block_trader_does_not_reown_executor_helpers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    for marker in (
        "def _match_inquired_order(",
        "def _status_from_order_fill(",
        "def _is_order_stale(",
        "def _order_query_start_date(",
    ):
        assert marker not in source


def test_match_inquired_order_prefers_order_no_then_symbol() -> None:
    inquiry = {
        "orders": [
            {"order_no": "OTHER", "symbol": "005930", "filled_qty": 1},
            {"order_no": "O1", "symbol": "000660", "filled_qty": 2},
        ]
    }

    assert match_inquired_order({"order_no": "O1", "symbol": "005930"}, inquiry) == {
        "order_no": "O1",
        "symbol": "000660",
        "filled_qty": 2,
    }
    assert match_inquired_order({"order_no": "MISSING", "symbol": "005930"}, inquiry) == {
        "order_no": "OTHER",
        "symbol": "005930",
        "filled_qty": 1,
    }
    assert match_inquired_order({"order_no": "MISSING", "symbol": "035420"}, inquiry) is None


def test_status_from_order_fill_distinguishes_filled_partial_canceled_and_sent() -> None:
    assert status_from_order_fill({"qty": 2}, {"filled_qty": 2, "remaining_qty": 0}) == "filled"
    assert (
        status_from_order_fill({"qty": 2}, {"filled_qty": 1, "remaining_qty": 1})
        == "partially_filled"
    )
    assert status_from_order_fill({"qty": 2}, {"filled_qty": 0, "remaining_qty": 0}) == "sent"
    assert (
        status_from_order_fill({"qty": 2}, {"filled_qty": 0, "remaining_qty": 0, "canceled": True})
        == "canceled"
    )
    assert status_from_order_fill({"status": "cancel_requested"}, {"filled_qty": 0}) == (
        "cancel_requested"
    )


def test_is_order_stale_uses_created_at_age_and_minimum_timeout() -> None:
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(seconds=45)).isoformat()
    recent = (now - timedelta(seconds=20)).isoformat()

    assert is_order_stale({"created_at": old}, now=now, timeout_sec=10)
    assert not is_order_stale({"created_at": recent}, now=now, timeout_sec=30)
    assert not is_order_stale({"created_at": ""}, now=now, timeout_sec=30)


def test_order_query_start_date_uses_kst_date_or_today_fallback() -> None:
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    assert order_query_start_date({"created_at": "2026-06-19T23:30:00+00:00"}, now=now) == (
        "20260620"
    )
    assert order_query_start_date({"created_at": ""}, now=now) == "20260620"
