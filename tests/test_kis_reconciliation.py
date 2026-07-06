from __future__ import annotations

from tradecraft.services.kis_reconciliation import (
    build_reconciliation_plan,
    positions_by_symbol,
    unallocated_qty_by_symbol,
)


def test_positions_by_symbol_keeps_only_krx_position_rows() -> None:
    account = {
        "positions": [
            {"symbol": "005930", "qty": 3, "name": "삼성전자"},
            {"symbol": "BTCUSDT", "qty": 1},
            {"symbol": "", "qty": 9},
            "bad-row",
        ]
    }

    assert positions_by_symbol(account) == {
        "005930": {"symbol": "005930", "qty": 3, "name": "삼성전자"}
    }


def test_unallocated_qty_subtracts_active_block_quantity() -> None:
    account = {
        "positions": [
            {"symbol": "005930", "available_qty": 5},
            {"symbol": "000660", "qty": 4},
        ]
    }
    blocks = [
        {"symbol": "005930", "status": "open", "qty_open": 2},
        {"symbol": "005930", "status": "entry_pending", "qty_initial": 1},
        {"symbol": "000660", "status": "closed", "qty_open": 4},
    ]

    assert unallocated_qty_by_symbol(account=account, blocks=blocks) == {
        "005930": 2,
        "000660": 4,
    }


def test_build_reconciliation_plan_opens_filled_entry_pending_block() -> None:
    account = {"positions": [{"symbol": "005930", "available_qty": 3}]}
    blocks = [
        {
            "block_id": "open-1",
            "symbol": "005930",
            "status": "open",
            "qty_open": 1,
        },
        {
            "block_id": "entry-1",
            "symbol": "005930",
            "status": "entry_pending",
            "qty_initial": 2,
        },
    ]

    plan = build_reconciliation_plan(
        account=account,
        blocks=blocks,
        now_iso="2026-06-21T00:00:00+00:00",
    )

    assert plan["symbols"]["005930"] == {
        "account_qty": 3,
        "allocated_qty": 3,
        "overallocated_qty": 0,
    }
    assert plan["change_count"] == 1
    assert plan["updates"] == [
        {
            "type": "entry_filled",
            "block_id": "entry-1",
            "fields": {
                "status": "open",
                "qty_open": 2,
                "opened_at": "2026-06-21T00:00:00+00:00",
                "llm_reason": "filled_reconciled",
            },
        }
    ]


def test_build_reconciliation_plan_closes_exit_pending_when_quantity_left_account() -> None:
    account = {"positions": [{"symbol": "005930", "available_qty": 0}]}
    blocks = [
        {
            "block_id": "exit-1",
            "symbol": "005930",
            "status": "exit_pending",
            "qty_open": 2,
        }
    ]

    plan = build_reconciliation_plan(
        account=account,
        blocks=blocks,
        now_iso="2026-06-21T00:00:00+00:00",
    )

    assert plan["symbols"]["005930"] == {
        "account_qty": 0,
        "allocated_qty": 0,
        "overallocated_qty": 0,
    }
    assert plan["change_count"] == 1
    assert plan["updates"] == [
        {
            "type": "exit_filled",
            "block_id": "exit-1",
            "fields": {
                "status": "closed",
                "qty_open": 0,
                "closed_at": "2026-06-21T00:00:00+00:00",
                "force_exit_requested": 0,
                "llm_reason": "exit_reconciled",
            },
        }
    ]


def test_reconciliation_skips_destructive_updates_when_account_snapshot_failed() -> None:
    account = {
        "status": "error",
        "error_message": "kis balance request failed: rate limit",
        "positions": [],
    }
    blocks = [
        {
            "block_id": "open-1",
            "symbol": "005930",
            "status": "open",
            "qty_open": 3,
        }
    ]

    plan = build_reconciliation_plan(
        account=account,
        blocks=blocks,
        now_iso="2026-06-21T00:00:00+00:00",
    )

    assert plan == {
        "status": "skipped",
        "reason": "account_snapshot_unavailable",
        "error_message": "kis balance request failed: rate limit",
        "symbols": {},
        "updates": [],
        "change_count": 0,
    }
