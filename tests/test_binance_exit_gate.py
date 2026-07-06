from __future__ import annotations

import pytest

from tradecraft.services.binance_exit_gate import (
    binance_exit_order_side,
    binance_exit_reason,
    entry_quality_loss_tighten_plan,
    exit_reconciliation_error_plan,
    exit_fill_update_plan,
    exit_quantity_unavailable_plan,
    exit_quantity_unavailable_result_plan,
    exit_success_plan,
    favorable_r_multiple,
    partial_profit_block_update_plan,
    partial_profit_full_exit_retry_decision,
    partial_profit_full_exit_retry_state_plan,
    partial_profit_quantity_unavailable_plan,
    partial_profit_quantity_plan,
    partial_profit_success_plan,
    partial_profit_trigger_plan,
    partial_profit_unfilled_plan,
    profit_lock_stop_plan,
    profit_lock_stop_price,
    remaining_exit_qty,
    spot_exit_retry_update_plan,
)


@pytest.mark.parametrize(
    ("block", "price", "expected"),
    [
        ({"side": "long", "target_price": 105, "stop_price": 95}, 105, "target_reached"),
        ({"side": "long", "target_price": 105, "stop_price": 95}, 94.9, "stop_reached"),
        ({"side": "short", "target_price": 95, "stop_price": 105}, 94.9, "target_reached"),
        ({"side": "short", "target_price": 95, "stop_price": 105}, 105.1, "stop_reached"),
        (
            {"side": "long", "target_price": 105, "stop_price": 95, "force_exit_requested": True},
            100,
            "force_exit_requested",
        ),
        ({"side": "long", "target_price": 105, "stop_price": 95}, 100, None),
    ],
)
def test_binance_exit_reason_handles_long_short_and_force_exit(
    block: dict[str, object],
    price: float,
    expected: str | None,
) -> None:
    assert binance_exit_reason(block, price) == expected


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"market": "spot", "side": "long"}, "sell"),
        ({"market": "upbit_spot", "side": "long"}, "sell"),
        ({"market": "futures", "side": "long"}, "sell"),
        ({"market": "futures", "side": "short"}, "buy"),
    ],
)
def test_binance_exit_order_side_depends_on_market_and_position_side(
    block: dict[str, object],
    expected: str,
) -> None:
    assert binance_exit_order_side(block) == expected


def test_remaining_exit_qty_zeroes_dust_below_min_notional() -> None:
    assert remaining_exit_qty(
        requested_qty=2.0,
        filled_qty=1.6,
        price=10.0,
        min_notional=5.0,
    ) == 0.0
    assert remaining_exit_qty(
        requested_qty=2.0,
        filled_qty=1.0,
        price=10.0,
        min_notional=5.0,
    ) == 1.0


def test_favorable_r_multiple_handles_long_and_short_blocks() -> None:
    assert favorable_r_multiple(
        entry_price=100.0,
        stop_price=90.0,
        price=115.0,
        side="long",
    ) == pytest.approx(1.5)
    assert favorable_r_multiple(
        entry_price=100.0,
        stop_price=110.0,
        price=85.0,
        side="short",
    ) == pytest.approx(1.5)


def test_favorable_r_multiple_returns_zero_for_invalid_risk_inputs() -> None:
    assert favorable_r_multiple(
        entry_price=100.0,
        stop_price=100.0,
        price=115.0,
        side="long",
    ) == 0.0
    assert favorable_r_multiple(
        entry_price=0.0,
        stop_price=90.0,
        price=115.0,
        side="long",
    ) == 0.0


def test_partial_profit_quantity_plan_keeps_normal_partial_split() -> None:
    assert partial_profit_quantity_plan(
        qty_open=2.0,
        price=10.0,
        fraction=0.5,
        market="spot",
        min_notional=5.0,
    ) == {
        "status": "ok",
        "partial_qty": 1.0,
        "original_partial_qty": 1.0,
        "remaining_qty": 1.0,
        "full_exit_for_min_notional": False,
        "exit_mode": "partial",
    }


def test_partial_profit_quantity_plan_closes_full_when_dust_would_remain() -> None:
    assert partial_profit_quantity_plan(
        qty_open=0.06,
        price=108.5,
        fraction=0.5,
        market="spot",
        min_notional=5.0,
    ) == {
        "status": "ok",
        "partial_qty": pytest.approx(0.06),
        "original_partial_qty": pytest.approx(0.03),
        "remaining_qty": 0.0,
        "full_exit_for_min_notional": True,
        "exit_mode": "full_exit_min_notional",
    }


def test_partial_profit_quantity_plan_skips_spot_when_whole_position_is_too_small() -> None:
    result = partial_profit_quantity_plan(
        qty_open=0.02,
        price=100.0,
        fraction=0.5,
        market="spot",
        min_notional=5.0,
    )

    assert result["status"] == "skip"
    assert result["reason"] == "spot_position_below_min_notional"


def test_partial_profit_quantity_plan_allows_futures_full_exit_for_min_notional() -> None:
    result = partial_profit_quantity_plan(
        qty_open=2.53,
        price=7.866,
        fraction=0.5,
        market="futures",
        min_notional=20.0,
    )

    assert result["status"] == "ok"
    assert result["partial_qty"] == pytest.approx(2.53)
    assert result["remaining_qty"] == 0.0
    assert result["full_exit_for_min_notional"] is True


def test_partial_profit_full_exit_retry_decision_allows_futures_min_notional_retry() -> None:
    decision = partial_profit_full_exit_retry_decision(
        market="futures",
        full_exit_for_min_notional=False,
        requested_qty=1.2,
        qty_open=2.5,
        error_message="Order notional below minimum",
    )

    assert decision == {
        "status": "retry",
        "retry_qty": 2.5,
        "exit_mode": "full_exit_min_notional_retry",
        "retry_reason": "Order notional below minimum",
    }


def test_partial_profit_full_exit_retry_decision_uses_response_min_notional_flag() -> None:
    decision = partial_profit_full_exit_retry_decision(
        market="futures",
        full_exit_for_min_notional=False,
        requested_qty=0.7,
        qty_open=1.4,
        response_is_min_notional_error=True,
        response_error_message="MIN_NOTIONAL",
        fallback_error_message="partial exit failed",
    )

    assert decision["status"] == "retry"
    assert decision["retry_qty"] == 1.4
    assert decision["retry_reason"] == "MIN_NOTIONAL"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"market": "spot"},
        {"market": "futures", "full_exit_for_min_notional": True},
        {"market": "futures", "requested_qty": 2.5, "qty_open": 2.5},
        {"market": "futures", "error_message": "temporary exchange outage"},
    ],
)
def test_partial_profit_full_exit_retry_decision_skips_non_retryable_cases(
    kwargs: dict[str, object],
) -> None:
    payload = {
        "market": "futures",
        "full_exit_for_min_notional": False,
        "requested_qty": 1.2,
        "qty_open": 2.5,
        "error_message": "Order notional below minimum",
    }
    payload.update(kwargs)

    decision = partial_profit_full_exit_retry_decision(**payload)

    assert decision["status"] == "skip"
    assert "reason" in decision


def test_partial_profit_full_exit_retry_state_plan_updates_retry_metadata() -> None:
    plan = partial_profit_full_exit_retry_state_plan(
        metadata={"thesis": "min notional retry"},
        retry_decision={
            "status": "retry",
            "retry_qty": 0.8,
            "exit_mode": "full_exit_min_notional_retry",
            "retry_reason": "order notional below minimum",
        },
        fallback_error_message="below min notional",
    )

    assert plan == {
        "status": "retry",
        "retry_qty": 0.8,
        "remaining_qty": 0.0,
        "full_exit_for_min_notional": True,
        "exit_mode": "full_exit_min_notional_retry",
        "metadata": {
            "thesis": "min notional retry",
            "partial_profit_retry_reason": "order notional below minimum",
        },
    }


def test_partial_profit_trigger_plan_uses_weak_lane_context() -> None:
    plan = partial_profit_trigger_plan(
        block={
            "block_id": "block-1",
            "symbol": "btcusdt",
            "market": "spot",
            "side": "long",
            "status": "open",
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "qty_open": 0.5,
            "metadata": {},
        },
        price=108.5,
        weak_lane_context={"matched": True, "source": "runtime_scorecard"},
        weak_trigger_r=0.8,
        weak_trigger_source="weak_performance_lane",
        entry_quality_repair_context={"enabled": True, "trigger_r": 0.6},
        global_repair_context={"enabled": True, "trigger_r": 0.7},
        base_fraction=0.5,
        distressed_fraction_context={"enabled": False},
        distressed_fraction=0.75,
    )

    assert plan["status"] == "ok"
    assert plan["block_id"] == "block-1"
    assert plan["symbol"] == "BTCUSDT"
    assert plan["market"] == "spot"
    assert plan["side"] == "long"
    assert plan["entry_price"] == 100.0
    assert plan["stop_price"] == 90.0
    assert plan["qty_open"] == 0.5
    assert plan["favorable_r"] == pytest.approx(0.85)
    assert plan["trigger_r"] == 0.8
    assert plan["trigger_source"] == "weak_performance_lane"
    assert plan["repair_context"] == {}
    assert plan["fraction"] == 0.5
    assert plan["fraction_source"] == "weak_lane_partial_profit_fraction"


def test_partial_profit_trigger_plan_falls_back_to_global_repair_context() -> None:
    plan = partial_profit_trigger_plan(
        block={
            "block_id": "block-2",
            "symbol": "ethusdt",
            "market": "futures",
            "side": "short",
            "status": "open",
            "entry_price": 100.0,
            "target_price": 80.0,
            "stop_price": 110.0,
            "qty_open": 1.0,
            "metadata": {"profit_lock_original_stop_price": 110.0},
        },
        price=91.0,
        weak_lane_context={"matched": False},
        weak_trigger_r=0.0,
        weak_trigger_source="weak_performance_lane",
        entry_quality_repair_context={"enabled": False, "trigger_r": 0.6},
        global_repair_context={"enabled": True, "trigger_r": 0.8},
        base_fraction=0.5,
        distressed_fraction_context={"enabled": True, "reason": "distressed"},
        distressed_fraction=0.75,
    )

    assert plan["status"] == "ok"
    assert plan["market"] == "futures"
    assert plan["side"] == "short"
    assert plan["favorable_r"] == pytest.approx(0.9)
    assert plan["trigger_r"] == 0.8
    assert plan["trigger_source"] == "mfe_surrender_repair"
    assert plan["repair_context"] == {"enabled": True, "trigger_r": 0.8}
    assert plan["fraction"] == 0.75
    assert plan["fraction_source"] == "distressed_entry_quality"
    assert plan["fraction_context"] == {"enabled": True, "reason": "distressed"}


def test_partial_profit_trigger_plan_skips_ineligible_blocks() -> None:
    plan = partial_profit_trigger_plan(
        block={
            "status": "open",
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "qty_open": 0.5,
            "metadata": {"partial_profit_taken_at": "2026-06-20T00:00:00+00:00"},
        },
        price=108.5,
        weak_lane_context={"matched": True},
        weak_trigger_r=0.8,
        weak_trigger_source="weak_performance_lane",
        entry_quality_repair_context={},
        global_repair_context={},
        base_fraction=0.5,
        distressed_fraction_context={},
        distressed_fraction=0.75,
    )

    assert plan == {"status": "skip", "reason": "partial_profit_already_taken"}


def test_partial_profit_quantity_unavailable_plan_builds_update_event_and_result() -> None:
    plan = partial_profit_quantity_unavailable_plan(
        metadata={"thesis": "protect gains"},
        message="free balance too small",
        block_id="block-1",
        symbol="BTCUSDT",
        market="spot",
        order_side="sell",
        requested_qty=0.25,
        quantity_context={"order_qty": 0.0, "balance_checked": True},
        price=108.5,
        now_iso="2026-06-21T01:02:03+00:00",
    )

    assert plan["update_fields"] == {
        "risk_note": "free balance too small",
        "metadata": {
            "thesis": "protect gains",
            "partial_profit_error_at": "2026-06-21T01:02:03+00:00",
            "partial_profit_error_message": "free balance too small",
        },
    }
    assert plan["event"] == {
        "type": "partial_profit_skipped",
        "message": "free balance too small",
        "payload": {
            "price": 108.5,
            "side": "sell",
            "requested_qty": 0.25,
            "quantity_context": {"order_qty": 0.0, "balance_checked": True},
        },
    }
    assert plan["result"] == {
        "status": "skipped",
        "block_id": "block-1",
        "symbol": "BTCUSDT",
        "market": "spot",
        "side": "sell",
        "qty": 0.0,
        "requested_qty": 0.25,
        "quantity_context": {"order_qty": 0.0, "balance_checked": True},
        "price": 108.5,
        "reason": "partial_profit_reached",
    }


def test_partial_profit_unfilled_plan_maps_sent_to_unfilled_result() -> None:
    plan = partial_profit_unfilled_plan(
        metadata={"thesis": "protect gains"},
        error_message="EXPIRED",
        status="sent",
        block_id="block-2",
        symbol="ETHUSDT",
        market="futures",
        order_side="buy",
        qty=1.2,
        requested_qty=0.6,
        quantity_context={"order_qty": 1.2},
        price=91.0,
        order={"id": 7, "status": "sent"},
        now_iso="2026-06-21T01:02:03+00:00",
    )

    assert plan["update_fields"]["risk_note"] == "partial profit not filled: EXPIRED"
    assert plan["update_fields"]["metadata"]["partial_profit_error_message"] == "EXPIRED"
    assert plan["event"]["type"] == "partial_profit_not_filled"
    assert plan["event"]["payload"]["order"] == {"id": 7, "status": "sent"}
    assert plan["result"]["status"] == "unfilled"
    assert plan["result"]["order"] == {"id": 7, "status": "sent"}


def test_partial_profit_block_update_plan_records_metadata_and_closes_empty_block() -> None:
    plan = partial_profit_block_update_plan(
        metadata={"cost_edge_gate": {"estimated_round_trip_cost_pct": 0.24}},
        qty_open=2.0,
        filled_qty=2.0,
        remaining_qty=0.0,
        price=112.5,
        favorable_r=1.25,
        trigger_r=1.0,
        trigger_source="runtime_scorecard",
        fraction=0.5,
        fraction_source="weak_lane_partial_profit_fraction",
        exit_mode="full_exit_min_notional_retry",
        min_notional=10.0,
        original_requested_qty=1.0,
        requested_qty=2.0,
        order_status="sent",
        new_stop_price=103.0,
        original_stop_price=95.0,
        weak_lane_context={"source": "runtime_scorecard", "lane": "futures_short"},
        repair_context={"enabled": True, "rule": "mfe_surrender"},
        fraction_context={"enabled": True, "reason": "distressed"},
        taken_at="2026-06-20T01:02:03+00:00",
    )

    metadata = plan["metadata"]
    update_fields = plan["update_fields"]
    assert metadata["partial_profit_taken_at"] == "2026-06-20T01:02:03+00:00"
    assert metadata["partial_profit_trigger_source"] == "runtime_scorecard"
    assert metadata["partial_profit_filled_qty"] == 2.0
    assert metadata["partial_profit_remaining_qty"] == 0.0
    assert metadata["runtime_weak_performance_lane"] == {
        "source": "runtime_scorecard",
        "lane": "futures_short",
    }
    assert metadata["partial_profit_repair_context"] == {
        "enabled": True,
        "rule": "mfe_surrender",
    }
    assert metadata["partial_profit_fraction_context"] == {
        "enabled": True,
        "reason": "distressed",
    }
    assert metadata["profit_lock_original_stop_price"] == 95.0
    assert update_fields["stop_price"] == 103.0
    assert update_fields["status"] == "closed"
    assert update_fields["qty_open"] == 0.0
    assert update_fields["closed_at"] == "2026-06-20T01:02:03+00:00"


def test_partial_profit_success_plan_builds_event_and_result_payload() -> None:
    plan = partial_profit_success_plan(
        block_id="bnb_futures_ETHUSDT_1",
        symbol="ETHUSDT",
        market="futures",
        side="short",
        order_side="buy",
        qty=0.4,
        requested_qty=0.5,
        filled_qty=0.4,
        remaining_qty=0.6,
        quantity_context={"order_qty": 0.4, "free_qty": 1.0},
        price=2800.0,
        favorable_r=1.4,
        trigger_r=1.0,
        trigger_source="mfe_surrender_repair",
        new_stop_price=2860.0,
        order={"id": 11, "status": "sent"},
        block={"block_id": "bnb_futures_ETHUSDT_1", "qty_open": 0.6},
    )

    assert plan["event"] == {
        "type": "partial_profit",
        "message": "weak lane partial profit taken",
        "payload": {
            "price": 2800.0,
            "side": "short",
            "order_side": "buy",
            "qty": 0.4,
            "requested_qty": 0.5,
            "filled_qty": 0.4,
            "remaining_qty": 0.6,
            "favorable_r": 1.4,
            "trigger_r": 1.0,
            "new_stop_price": 2860.0,
            "order": {"id": 11, "status": "sent"},
            "quantity_context": {"order_qty": 0.4, "free_qty": 1.0},
        },
    }
    assert plan["result"] == {
        "status": "partial_profit_taken",
        "block_id": "bnb_futures_ETHUSDT_1",
        "symbol": "ETHUSDT",
        "market": "futures",
        "side": "short",
        "order_side": "buy",
        "qty": 0.4,
        "requested_qty": 0.5,
        "filled_qty": 0.4,
        "remaining_qty": 0.6,
        "quantity_context": {"order_qty": 0.4, "free_qty": 1.0},
        "price": 2800.0,
        "favorable_r": 1.4,
        "trigger_r": 1.0,
        "trigger_source": "mfe_surrender_repair",
        "reason": "partial_profit_reached",
        "order": {"id": 11, "status": "sent"},
        "block": {"block_id": "bnb_futures_ETHUSDT_1", "qty_open": 0.6},
    }


def test_exit_fill_update_plan_rearms_retry_after_partial_live_fill() -> None:
    plan = exit_fill_update_plan(
        metadata={"thesis": "breakout"},
        status="sent",
        response_status="FILLED",
        reason="target_reached",
        requested_qty=2.0,
        order_qty=1.5,
        filled_qty=1.5,
        remaining_qty=0.5,
        retry_reason="partial_fill",
        retry_cooldown_sec=60,
        retry_after_ts=10060.0,
        now_iso="2026-06-20T01:02:03+00:00",
    )

    update_fields = plan["update_fields"]
    metadata = update_fields["metadata"]
    assert update_fields["status"] == "open"
    assert update_fields["qty_open"] == 0.5
    assert update_fields["force_exit_requested"] == 1
    assert update_fields["risk_note"] == "partial exit fill after balance clamp: 1.5/2.0"
    assert metadata["thesis"] == "breakout"
    assert metadata["last_exit_retry_reason"] == "partial_fill"
    assert metadata["last_exit_failure_at"] == "2026-06-20T01:02:03+00:00"
    assert metadata["exit_retry_cooldown_sec"] == 60
    assert metadata["exit_retry_after_ts"] == 10060.0


def test_exit_fill_update_plan_closes_filled_or_dust_exit() -> None:
    plan = exit_fill_update_plan(
        metadata={},
        status="sent",
        response_status="PARTIALLY_FILLED",
        reason="stop_reached",
        requested_qty=2.0,
        order_qty=2.0,
        filled_qty=1.999999999,
        remaining_qty=0.0,
        retry_reason="partial_fill",
        retry_cooldown_sec=60,
        retry_after_ts=10060.0,
        now_iso="2026-06-20T01:02:03+00:00",
    )

    assert plan["update_fields"] == {
        "llm_reason": "stop_reached",
        "status": "closed",
        "qty_open": 0.0,
        "force_exit_requested": 0,
        "closed_at": "2026-06-20T01:02:03+00:00",
    }


def test_exit_fill_update_plan_rearms_unfilled_exit_with_cooldown() -> None:
    plan = exit_fill_update_plan(
        metadata={},
        status="sent",
        response_status="EXPIRED",
        reason="force_exit_requested",
        requested_qty=2.0,
        order_qty=2.0,
        filled_qty=0.0,
        remaining_qty=2.0,
        retry_reason="EXPIRED",
        retry_cooldown_sec=0,
        retry_after_ts=None,
        now_iso="2026-06-20T01:02:03+00:00",
    )

    update_fields = plan["update_fields"]
    metadata = update_fields["metadata"]
    assert update_fields["status"] == "open"
    assert update_fields["force_exit_requested"] == 1
    assert update_fields["risk_note"] == "exit order not filled; retry armed: EXPIRED"
    assert metadata["last_exit_retry_reason"] == "EXPIRED"
    assert metadata["exit_retry_cooldown_sec"] == 0
    assert "exit_retry_after_ts" not in metadata


def test_exit_success_plan_builds_canonical_executor_result() -> None:
    order = {"id": 7, "status": "sent"}
    block = {"block_id": "bnb_futures_ETHUSDT_1", "status": "closed"}
    quantity_context = {"order_qty": 1.5, "requested_qty": 2.0}

    plan = exit_success_plan(
        status="sent",
        block_id="bnb_futures_ETHUSDT_1",
        symbol="ETHUSDT",
        market="futures",
        order_side="sell",
        qty=1.5,
        requested_qty=2.0,
        quantity_context=quantity_context,
        price=2800.0,
        reason="target_reached",
        order=order,
        block=block,
    )

    assert plan == {
        "status": "sent",
        "block_id": "bnb_futures_ETHUSDT_1",
        "symbol": "ETHUSDT",
        "market": "futures",
        "side": "sell",
        "qty": 1.5,
        "requested_qty": 2.0,
        "quantity_context": quantity_context,
        "price": 2800.0,
        "reason": "target_reached",
        "order": order,
        "block": block,
    }


def test_exit_success_plan_preserves_optional_extra_fields() -> None:
    initial_order = {"id": 1, "status": "error"}

    plan = exit_success_plan(
        status="sent",
        block_id="bnb_spot_SOLUSDT_1",
        symbol="solusdt",
        market="spot",
        order_side="sell",
        qty=3.0,
        requested_qty=5.0,
        quantity_context={"insufficient_balance_retry": True},
        price=140.0,
        reason="target_reached",
        order={"id": 2, "status": "sent"},
        block={"block_id": "bnb_spot_SOLUSDT_1", "status": "closed"},
        extra_fields={"initial_order": initial_order},
    )

    assert plan["symbol"] == "SOLUSDT"
    assert plan["initial_order"] == initial_order


def test_exit_quantity_unavailable_plan_marks_reconciliation_error() -> None:
    quantity_context = {
        "reconciliation_error": True,
        "error_message": "wallet mismatch",
    }

    plan = exit_quantity_unavailable_plan(
        metadata={"thesis": "mean_reversion"},
        message="wallet mismatch",
        price=12.5,
        side="sell",
        requested_qty=3.0,
        quantity_context=quantity_context,
        reason="stop_reached",
        now_iso="2026-06-20T01:02:03+00:00",
        retry_cooldown_sec=60,
        retry_after_ts=10060.0,
    )

    update_fields = plan["update_fields"]
    event = plan["event"]
    assert plan["status"] == "reconciliation_error"
    assert update_fields["status"] == "error"
    assert update_fields["force_exit_requested"] == 0
    assert update_fields["risk_note"] == "wallet mismatch"
    assert update_fields["metadata"]["thesis"] == "mean_reversion"
    assert update_fields["metadata"]["exit_reconciliation_error"] == {
        "message": "wallet mismatch",
        "price": 12.5,
        "side": "sell",
        "requested_qty": 3.0,
        "quantity_context": quantity_context,
        "detected_at": "2026-06-20T01:02:03+00:00",
    }
    assert event == {
        "type": "exit_reconciliation_error",
        "message": "wallet mismatch",
        "payload": {
            "price": 12.5,
            "side": "sell",
            "requested_qty": 3.0,
            "quantity_context": quantity_context,
        },
    }


def test_exit_reconciliation_error_plan_builds_update_and_event_payload() -> None:
    quantity_context = {"order_qty": 0.5, "balance_checked": True}
    order = {"id": 7, "status": "error"}

    plan = exit_reconciliation_error_plan(
        metadata={"thesis": "force exit"},
        error_message="insufficient balance",
        price=111.0,
        side="sell",
        requested_qty=0.6,
        order_qty=0.5,
        quantity_context=quantity_context,
        order=order,
        detected_at="2026-06-20T01:02:03+00:00",
    )

    assert plan["update_fields"] == {
        "status": "error",
        "force_exit_requested": 0,
        "risk_note": "insufficient balance",
        "metadata": {
            "thesis": "force exit",
            "exit_reconciliation_error": {
                "message": "insufficient balance",
                "price": 111.0,
                "side": "sell",
                "requested_qty": 0.6,
                "order_qty": 0.5,
                "quantity_context": quantity_context,
                "detected_at": "2026-06-20T01:02:03+00:00",
            },
        },
    }
    assert plan["event"] == {
        "type": "exit_reconciliation_error",
        "message": "insufficient balance",
        "payload": {
            "price": 111.0,
            "side": "sell",
            "requested_qty": 0.6,
            "order_qty": 0.5,
            "quantity_context": quantity_context,
            "order": order,
        },
    }


def test_exit_quantity_unavailable_plan_closes_dust_without_retry_metadata() -> None:
    plan = exit_quantity_unavailable_plan(
        metadata={},
        message="dust below minimum notional",
        price=0.25,
        side="sell",
        requested_qty=4.0,
        quantity_context={"close_as_dust": True},
        reason="target_reached",
        now_iso="2026-06-20T01:02:03+00:00",
        retry_cooldown_sec=60,
        retry_after_ts=10060.0,
    )

    assert plan["status"] == "closed_as_dust"
    assert plan["update_fields"] == {
        "status": "closed",
        "qty_open": 0.0,
        "force_exit_requested": 0,
        "closed_at": "2026-06-20T01:02:03+00:00",
        "risk_note": "dust below minimum notional",
    }
    assert plan["event"]["type"] == "exit_closed_as_dust"


def test_exit_quantity_unavailable_plan_rearms_retry_for_plain_skip() -> None:
    plan = exit_quantity_unavailable_plan(
        metadata={},
        message="exit quantity unavailable",
        price=12.5,
        side="sell",
        requested_qty=3.0,
        quantity_context={"balance_checked": True},
        reason="force_exit_requested",
        now_iso="2026-06-20T01:02:03+00:00",
        retry_cooldown_sec=60,
        retry_after_ts=10060.0,
    )

    metadata = plan["update_fields"]["metadata"]
    assert plan["status"] == "skipped"
    assert plan["update_fields"]["status"] == "open"
    assert plan["update_fields"]["force_exit_requested"] == 1
    assert plan["update_fields"]["risk_note"] == "exit quantity unavailable"
    assert metadata["last_exit_retry_reason"] == "exit quantity unavailable"
    assert metadata["last_exit_failure_at"] == "2026-06-20T01:02:03+00:00"
    assert metadata["exit_retry_cooldown_sec"] == 60
    assert metadata["exit_retry_after_ts"] == 10060.0
    assert plan["event"]["type"] == "exit_skipped"


def test_exit_quantity_unavailable_result_plan_uses_zero_qty_without_order() -> None:
    quantity_context = {"balance_checked": True, "error_message": "free balance too small"}
    block = {"block_id": "bnb_spot_SOLUSDT_1", "status": "open"}

    result = exit_quantity_unavailable_result_plan(
        status="skipped",
        block_id="bnb_spot_SOLUSDT_1",
        symbol="solusdt",
        market="spot",
        order_side="sell",
        requested_qty=3.5,
        quantity_context=quantity_context,
        price=138.0,
        reason="target_reached",
        block=block,
    )

    assert result == {
        "status": "skipped",
        "block_id": "bnb_spot_SOLUSDT_1",
        "symbol": "SOLUSDT",
        "market": "spot",
        "side": "sell",
        "qty": 0.0,
        "requested_qty": 3.5,
        "quantity_context": quantity_context,
        "price": 138.0,
        "reason": "target_reached",
        "block": block,
    }
    assert "order" not in result


def test_spot_exit_retry_update_plan_closes_after_fresh_balance_fill() -> None:
    retry_context = {"insufficient_balance_retry": True, "order_qty": 0.0629}
    plan = spot_exit_retry_update_plan(
        metadata={"thesis": "failed first balance lookup"},
        initial_error_message="insufficient balance",
        price=204.0,
        side="sell",
        requested_qty=0.063,
        failed_qty=0.063,
        retry_qty=0.0629,
        filled_qty=0.0629,
        remaining_qty=0.0,
        status="sent",
        response_status="FILLED",
        response={"status": "FILLED"},
        retry_context=retry_context,
        initial_order={"id": 1, "status": "error"},
        retry_order={"id": 2, "status": "sent"},
        reason="force_exit_requested",
        now_iso="2026-06-20T01:02:03+00:00",
        retry_cooldown_sec=60,
        retry_after_ts=10060.0,
    )

    update_fields = plan["update_fields"]
    assert update_fields["status"] == "closed"
    assert update_fields["qty_open"] == 0.0
    assert update_fields["force_exit_requested"] == 0
    assert update_fields["closed_at"] == "2026-06-20T01:02:03+00:00"
    assert update_fields["risk_note"] == (
        "spot exit retried with fresh sellable balance: 0.0629/0.063"
    )
    assert update_fields["metadata"]["thesis"] == "failed first balance lookup"
    assert update_fields["metadata"]["insufficient_balance_exit_retry"] == {
        "retried_at": "2026-06-20T01:02:03+00:00",
        "initial_error_message": "insufficient balance",
        "initial_order_qty": 0.063,
        "retry_order_qty": 0.0629,
        "filled_qty": 0.0629,
        "remaining_qty": 0.0,
        "status": "sent",
        "response_status": "FILLED",
    }
    assert plan["event"]["type"] == "insufficient_balance_exit_retry"
    assert plan["event"]["payload"]["quantity_context"] == retry_context


def test_spot_exit_retry_update_plan_rearms_after_partial_retry_fill() -> None:
    plan = spot_exit_retry_update_plan(
        metadata={},
        initial_error_message="insufficient balance",
        price=100.0,
        side="sell",
        requested_qty=2.0,
        failed_qty=2.0,
        retry_qty=1.5,
        filled_qty=1.0,
        remaining_qty=1.0,
        status="sent",
        response_status="PARTIALLY_FILLED",
        response={"status": "PARTIALLY_FILLED"},
        retry_context={"insufficient_balance_retry": True},
        initial_order={"id": 1},
        retry_order={"id": 2},
        reason="target_reached",
        now_iso="2026-06-20T01:02:03+00:00",
        retry_cooldown_sec=60,
        retry_after_ts=10060.0,
    )

    update_fields = plan["update_fields"]
    metadata = update_fields["metadata"]
    assert update_fields["status"] == "open"
    assert update_fields["qty_open"] == 1.0
    assert update_fields["force_exit_requested"] == 1
    assert update_fields["risk_note"] == "spot exit retry partially filled: 1/2"
    assert metadata["last_exit_retry_reason"] == (
        "partial_fill_after_insufficient_balance_retry"
    )
    assert metadata["exit_retry_after_ts"] == 10060.0


def test_entry_quality_loss_tighten_plan_builds_update_event_and_result_fields() -> None:
    plan = entry_quality_loss_tighten_plan(
        metadata={"existing": "note"},
        block_id="bnb_futures_ETHUSDT_1",
        symbol="ethusdt",
        market="futures",
        side="short",
        entry_quality="distressed",
        entry_quality_lane="futures:short:distressed",
        trigger_r=0.45,
        unfavorable_r=0.61234567,
        price=2200.0,
        old_stop_price=2250.0,
        new_stop_price=2200.0,
        original_stop_price=2250.0,
        quality_card={
            "sample_count": 4,
            "pnl_usdt": -12.5,
            "win_rate_pct": 25.0,
            "avg_r_multiple": -0.4,
            "profit_factor": 0.6,
        },
        min_samples=3,
        now_iso="2026-06-20T01:02:03+00:00",
    )

    context = plan["context"]
    update_fields = plan["update_fields"]
    assert context["version"] == "binance_entry_quality_loss_tighten_v1"
    assert context["entry_quality_lane"] == "futures:short:distressed"
    assert context["unfavorable_r"] == 0.612346
    assert context["tightened_at"] == "2026-06-20T01:02:03+00:00"
    assert update_fields["stop_price"] == 2200.0
    assert update_fields["llm_reason"] == "entry_quality_loss_tighten"
    assert update_fields["metadata"]["existing"] == "note"
    assert update_fields["metadata"]["entry_quality_loss_tighten"] == context
    assert update_fields["metadata"]["entry_quality_loss_tighten_original_stop_price"] == 2250.0
    assert plan["event"] == {
        "type": "entry_quality_loss_tighten",
        "message": "entry-quality loss stop tightened",
        "payload": context,
    }
    assert plan["result_fields"] == {
        "status": "entry_quality_loss_tightened",
        "block_id": "bnb_futures_ETHUSDT_1",
        "symbol": "ETHUSDT",
        "market": "futures",
        "side": "short",
        "price": 2200.0,
        "old_stop_price": 2250.0,
        "new_stop_price": 2200.0,
        "unfavorable_r": 0.61234567,
        "trigger_r": 0.45,
        "entry_quality_lane": "futures:short:distressed",
        "reason": "entry_quality_loss_tighten",
    }


def test_profit_lock_stop_price_moves_long_stop_above_entry_and_cost_floor() -> None:
    assert profit_lock_stop_price(
        side="long",
        entry_price=100.0,
        stop_price=90.0,
        risk=10.0,
        price=108.0,
        lock_r=0.25,
        estimated_cost_pct=0.25,
        min_net_buffer_pct=0.12,
        default_cost_pct=0.20,
    ) == pytest.approx(102.5)


def test_profit_lock_stop_price_moves_short_stop_below_entry_and_cost_floor() -> None:
    assert profit_lock_stop_price(
        side="short",
        entry_price=100.0,
        stop_price=110.0,
        risk=10.0,
        price=92.0,
        lock_r=0.25,
        estimated_cost_pct=0.25,
        min_net_buffer_pct=0.12,
        default_cost_pct=0.20,
    ) == pytest.approx(97.5)


def test_profit_lock_stop_price_uses_default_cost_floor_when_metadata_cost_missing() -> None:
    assert profit_lock_stop_price(
        side="long",
        entry_price=100.0,
        stop_price=90.0,
        risk=10.0,
        price=108.0,
        lock_r=0.1,
        estimated_cost_pct=0.0,
        min_net_buffer_pct=0.12,
        default_cost_pct=2.0,
    ) == pytest.approx(102.12)


def test_profit_lock_stop_price_rejects_unexecutable_stop() -> None:
    assert profit_lock_stop_price(
        side="long",
        entry_price=100.0,
        stop_price=90.0,
        risk=10.0,
        price=102.0,
        lock_r=0.25,
        estimated_cost_pct=0.25,
        min_net_buffer_pct=0.12,
        default_cost_pct=0.20,
    ) is None


def test_profit_lock_stop_plan_returns_stop_and_cost_metadata() -> None:
    plan = profit_lock_stop_plan(
        side="long",
        entry_price=100.0,
        stop_price=90.0,
        risk=10.0,
        price=108.0,
        lock_r=0.1,
        estimated_cost_pct=0.0,
        min_net_buffer_pct=0.12,
        default_cost_pct=2.0,
    )

    assert plan["status"] == "ok"
    assert plan["stop_price"] == pytest.approx(102.12)
    assert plan["cost_floor_pct"] == pytest.approx(2.12)
    assert plan["estimated_round_trip_cost_pct"] == pytest.approx(2.0)
    assert plan["min_net_buffer_pct"] == pytest.approx(0.12)


def test_profit_lock_stop_plan_records_unexecutable_skip_reason() -> None:
    plan = profit_lock_stop_plan(
        side="long",
        entry_price=100.0,
        stop_price=90.0,
        risk=10.0,
        price=102.0,
        lock_r=0.25,
        estimated_cost_pct=0.25,
        min_net_buffer_pct=0.12,
        default_cost_pct=0.20,
    )

    assert plan["status"] == "skip"
    assert plan["reason"] == "unexecutable_profit_lock_stop"
    assert plan["cost_floor_pct"] == pytest.approx(0.37)
    assert plan["estimated_round_trip_cost_pct"] == pytest.approx(0.25)
