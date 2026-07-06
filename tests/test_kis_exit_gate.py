from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_exit_gate import (
    exit_policy_for_block,
    kis_sell_fill_update_plan,
    manager_close_guard,
    manager_close_row_signal,
    rule_exit_trigger_for_block,
    should_emit_profit_lock_signal,
)


def test_kis_block_trader_does_not_reown_exit_policy_helpers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def _exit_policy_for_block(" not in source


def test_manager_close_row_signal_accepts_explicit_operator_confirmation() -> None:
    assert manager_close_row_signal({"operator_confirmed": True}) == {
        "reason": "operator_confirmed",
        "source": "explicit_flag",
    }


def test_manager_close_row_signal_accepts_close_trigger() -> None:
    assert manager_close_row_signal({"close_trigger": "thesis_invalidated"}) == {
        "reason": "thesis_invalidated",
        "source": "close_trigger",
    }


def test_manager_close_row_signal_detects_text_invalidation() -> None:
    result = manager_close_row_signal(
        {
            "reason": "실적 가설이 무효가 되었고 thesis_broken 상태",
            "metadata": {"invalidation": "수급 논리 훼손"},
        }
    )

    assert result["reason"] == "text_invalidation_signal"
    assert result["source"] in {"thesis_broken", "무효", "논리 훼손"}


def test_manager_close_row_signal_ignores_plain_review_commentary() -> None:
    assert manager_close_row_signal({"reason": "30분 리뷰에서 약간 피곤해 보임"}) == {}


def test_exit_policy_for_block_sells_short_horizon_on_target_touch() -> None:
    assert exit_policy_for_block(
        {"metadata": {"horizon": "short"}},
        "target_reached",
    ) == {"action": "sell_all", "horizon": "short"}


def test_exit_policy_for_block_defers_mid_and_long_horizons_to_manager() -> None:
    assert exit_policy_for_block(
        {"metadata": {"horizon": "mid_term"}},
        "target_reached",
    ) == {"action": "manager_review", "horizon": "mid"}


def test_exit_policy_for_block_uses_trim_review_for_core_etf_touch() -> None:
    assert exit_policy_for_block(
        {"metadata": {"horizon": "core"}},
        "target_reached",
    ) == {"action": "manager_trim_review", "horizon": "core_etf"}


def test_exit_policy_for_block_manual_close_overrides_horizon_patience() -> None:
    assert exit_policy_for_block(
        {"metadata": {"horizon": "core_etf"}},
        "manual_close",
    ) == {"action": "sell_all", "horizon": "core_etf"}


def test_manager_close_guard_blocks_early_mid_close_without_rule_signal() -> None:
    result = manager_close_guard(
        block={
            "block_id": "blk_mid",
            "symbol": "277810",
            "qty_open": 1,
            "status": "open",
            "target_price": 120_000,
            "stop_price": 90_000,
            "metadata": {"horizon": "mid"},
        },
        row={"block_id": "blk_mid", "reason": "30분 리뷰에서 약해서 청산"},
        quote={"price": 100_000},
        is_waiting_entry=False,
        latest_signal={},
        age_sec=60.0,
        min_age_by_horizon={"mid": 72 * 60 * 60},
    )

    assert result["allowed"] is False
    assert result["reason"] == "horizon_patience_guard"
    assert result["horizon"] == "mid"
    assert result["age_sec"] == 60.0
    assert result["min_age_sec"] == 259_200


def test_manager_close_guard_allows_rule_signal_before_patience_age() -> None:
    result = manager_close_guard(
        block={
            "block_id": "blk_mid",
            "symbol": "277810",
            "qty_open": 1,
            "status": "open",
            "target_price": 120_000,
            "stop_price": 90_000,
            "metadata": {"horizon": "mid_term"},
        },
        row={"block_id": "blk_mid", "reason": "리뷰선 터치 후 약화"},
        quote={"price": 95_000},
        is_waiting_entry=False,
        latest_signal={"reason": "stop_reached", "price": 95_000},
        age_sec=60.0,
        min_age_by_horizon={"mid": 72 * 60 * 60},
    )

    assert result["allowed"] is True
    assert result["reason"] == "review_signal_present"
    assert result["horizon"] == "mid"
    assert result["signal"] == {"reason": "stop_reached", "price": 95_000}


def test_manager_close_guard_requires_invalidation_after_patience_age() -> None:
    result = manager_close_guard(
        block={
            "block_id": "blk_mid",
            "symbol": "277810",
            "qty_open": 1,
            "status": "open",
            "target_price": 120_000,
            "stop_price": 90_000,
            "metadata": {"horizon": "mid"},
        },
        row={"block_id": "blk_mid", "reason": "그냥 느낌상 청산"},
        quote={"price": 100_000},
        is_waiting_entry=False,
        latest_signal={},
        age_sec=300_000.0,
        min_age_by_horizon={"mid": 72 * 60 * 60},
    )

    assert result["allowed"] is False
    assert result["reason"] == "manager_close_requires_invalidation"
    assert "target_reached" in result["required"]


def test_manager_close_guard_accepts_invalidation_after_patience_age() -> None:
    result = manager_close_guard(
        block={
            "block_id": "blk_mid",
            "symbol": "277810",
            "qty_open": 1,
            "status": "open",
            "target_price": 120_000,
            "stop_price": 90_000,
            "metadata": {"horizon": "mid"},
        },
        row={"block_id": "blk_mid", "reason": "thesis_broken"},
        quote={"price": 100_000},
        is_waiting_entry=False,
        latest_signal={},
        age_sec=300_000.0,
        min_age_by_horizon={"mid": 72 * 60 * 60},
    )

    assert result["allowed"] is True
    assert result["reason"] == "text_invalidation_signal"
    assert result["row_signal"]["source"] == "thesis_broken"


def test_rule_exit_trigger_for_block_rejects_invalid_target_stop_structure() -> None:
    result = rule_exit_trigger_for_block(
        {
            "block_id": "blk_bad",
            "symbol": "277810",
            "target_price": 90_000,
            "stop_price": 95_000,
        },
        {"price": 100_000},
    )

    assert result == {
        "status": "invalid_price_structure",
        "detail": "target_not_above_stop",
        "payload": {
            "reason": "target_not_above_stop",
            "current_price": 100_000.0,
            "target_price": 90_000.0,
            "stop_price": 95_000.0,
        },
    }


def test_rule_exit_trigger_for_block_prioritizes_force_exit_over_price() -> None:
    result = rule_exit_trigger_for_block(
        {
            "force_exit_requested": True,
            "target_price": 90_000,
            "stop_price": 80_000,
        },
        {"price": 95_000},
    )

    assert result["status"] == "triggered"
    assert result["reason"] == "force_exit_requested"


def test_rule_exit_trigger_for_block_detects_target_and_stop_touches() -> None:
    assert rule_exit_trigger_for_block(
        {"target_price": 110_000, "stop_price": 95_000},
        {"price": 111_000},
    )["reason"] == "target_reached"
    assert rule_exit_trigger_for_block(
        {"target_price": 110_000, "stop_price": 95_000},
        {"price": 94_000},
    )["reason"] == "stop_reached"


def test_rule_exit_trigger_for_block_returns_no_trigger_for_inside_band() -> None:
    assert rule_exit_trigger_for_block(
        {"target_price": 110_000, "stop_price": 95_000},
        {"price": 100_000},
    ) == {
        "status": "no_trigger",
        "price": 100_000.0,
        "target_price": 110_000.0,
        "stop_price": 95_000.0,
    }


def test_should_emit_profit_lock_signal_requires_mfe_giveback_and_positive_pnl() -> None:
    assert should_emit_profit_lock_signal(
        {
            "mfe_pct": 8.0,
            "giveback_pct": 4.0,
            "current_pnl_pct": 2.0,
        }
    )
    assert not should_emit_profit_lock_signal(
        {
            "mfe_pct": 7.9,
            "giveback_pct": 4.0,
            "current_pnl_pct": 2.0,
        }
    )
    assert not should_emit_profit_lock_signal(
        {
            "mfe_pct": 8.0,
            "giveback_pct": 3.9,
            "current_pnl_pct": 2.0,
        }
    )
    assert not should_emit_profit_lock_signal(
        {
            "mfe_pct": 8.0,
            "giveback_pct": 4.0,
            "current_pnl_pct": 1.9,
        }
    )


def test_kis_sell_fill_update_plan_closes_filled_exit() -> None:
    plan = kis_sell_fill_update_plan(
        block={"qty_open": 3},
        filled_qty=3,
        order_status="filled",
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan == {
        "action": "closed",
        "remaining_open": 0,
        "update_fields": {
            "status": "closed",
            "qty_open": 0,
            "closed_at": "2026-06-20T01:02:03+00:00",
            "force_exit_requested": 0,
            "llm_reason": "exit_filled_reconciled_by_order",
        },
    }


def test_kis_sell_fill_update_plan_keeps_remaining_partial_exit() -> None:
    plan = kis_sell_fill_update_plan(
        block={"qty_open": 5},
        filled_qty=2,
        order_status="partially_filled",
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan == {
        "action": "partial",
        "remaining_open": 3,
        "update_fields": {
            "qty_open": 3,
            "llm_reason": "partial_exit_reconciled",
        },
    }


def test_kis_sell_fill_update_plan_reopens_canceled_exit() -> None:
    plan = kis_sell_fill_update_plan(
        block={"qty_open": 5},
        filled_qty=0,
        order_status="canceled",
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan == {
        "action": "canceled",
        "remaining_open": 5,
        "update_fields": {
            "status": "open",
            "force_exit_requested": 0,
            "llm_reason": "exit_order_canceled",
        },
    }
