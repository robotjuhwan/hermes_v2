from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_performance_signal import (
    block_performance_summary,
    has_exit_signal,
    profit_lock_signal_plan,
)

ROOT = Path(__file__).resolve().parents[1]


def test_block_performance_summary_uses_entry_current_and_price_path() -> None:
    summary = block_performance_summary(
        {"entry_price": 100.0},
        current_price=108.0,
        prices=[98.0, 112.0, 104.0],
    )

    assert summary["entry_price"] == 100.0
    assert summary["current_price"] == 108.0
    assert summary["peak_price"] == 112.0
    assert summary["trough_price"] == 98.0
    assert summary["mfe_pct"] == 12.0
    assert summary["mae_pct"] == -2.0
    assert summary["giveback_pct"] == 4.0


def test_has_exit_signal_matches_event_type_and_reason() -> None:
    events = [
        {
            "event_type": "profit_lock_signal",
            "payload": {"reason": "profit_giveback"},
        },
        {
            "event_type": "exit_signal",
            "payload": {"reason": "target_reached"},
        },
    ]

    assert has_exit_signal(
        events,
        "profit_giveback",
        event_type="profit_lock_signal",
    )
    assert has_exit_signal(events, "target_reached")
    assert not has_exit_signal(events, "stop_reached")


def test_profit_lock_signal_plan_returns_event_payload_once_thresholds_are_met() -> None:
    plan = profit_lock_signal_plan(
        {
            "block_id": "blk-1",
            "metadata": {"horizon": "mid"},
        },
        price=108.0,
        performance={
            "mfe_pct": 12.0,
            "giveback_pct": 4.0,
            "current_pnl_pct": 8.0,
        },
        already_signaled=False,
    )

    assert plan == {
        "status": "profit_lock_signal",
        "reason": "profit_giveback",
        "horizon": "mid",
        "block_id": "blk-1",
        "event": {
            "block_id": "blk-1",
            "event_type": "profit_lock_signal",
            "message": "mid block gave back profit from path peak; manager review required",
            "payload": {
                "horizon": "mid",
                "reason": "profit_giveback",
                "price": 108.0,
                "policy_action": "manager_review",
                "performance": {
                    "mfe_pct": 12.0,
                    "giveback_pct": 4.0,
                    "current_pnl_pct": 8.0,
                },
                "manager_review": "regular_market_30m_full_portfolio",
            },
        },
    }


def test_profit_lock_signal_plan_skips_missing_block_existing_signal_or_weak_performance() -> None:
    assert (
        profit_lock_signal_plan(
            {"block_id": ""},
            price=108.0,
            performance={"mfe_pct": 12.0, "giveback_pct": 4.0, "current_pnl_pct": 8.0},
            already_signaled=False,
        )
        is None
    )
    assert (
        profit_lock_signal_plan(
            {"block_id": "blk-1"},
            price=108.0,
            performance={"mfe_pct": 12.0, "giveback_pct": 4.0, "current_pnl_pct": 8.0},
            already_signaled=True,
        )
        is None
    )
    assert (
        profit_lock_signal_plan(
            {"block_id": "blk-1"},
            price=108.0,
            performance={"mfe_pct": 1.0, "giveback_pct": 0.1, "current_pnl_pct": 0.5},
            already_signaled=False,
        )
        is None
    )


def test_kis_performance_signal_helpers_live_outside_block_trader() -> None:
    trader_source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()
    helper_source = (
        ROOT / "src/tradecraft/services/kis_performance_signal.py"
    ).read_text()

    assert "def block_performance_summary(" in helper_source
    assert "def has_exit_signal(" in helper_source
    assert "def profit_lock_signal_plan(" in helper_source
    assert "def _should_emit_profit_lock_signal(" not in trader_source
    assert "def _has_exit_signal(" not in trader_source
