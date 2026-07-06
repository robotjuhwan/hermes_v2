from __future__ import annotations

import asyncio

from tradecraft.services.binance_executor import (
    entry_not_filled_reason,
    entry_order_side,
    exit_order_execution,
    filled_order_price,
    is_min_notional_error_response,
    partial_profit_exit_order_execution,
    response_error_message,
    response_filled_qty,
    response_order_id,
)


def test_response_filled_qty_reads_exchange_quantity_variants() -> None:
    assert response_filled_qty({"executedQty": "0.12"}, requested_qty=1.0) == 0.12
    assert response_filled_qty({"cum_qty": "0.34"}, requested_qty=1.0) == 0.34
    assert response_filled_qty({"filledQty": "0.56"}, requested_qty=1.0) == 0.56


def test_response_filled_qty_does_not_use_requested_qty_as_fill_evidence() -> None:
    assert response_filled_qty({"status": "FILLED"}, requested_qty=2.5) == 0.0
    assert response_filled_qty({"status": "NEW"}, requested_qty=2.5) == 0.0
    assert response_filled_qty({"status": "FILLED"}, requested_qty=-1.0) == 0.0


def test_partial_profit_exit_order_execution_retries_min_notional_exception() -> None:
    calls: list[dict[str, object]] = []

    async def submit_exit_order(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            raise RuntimeError("Order notional below minimum")
        return {"status": "FILLED", "executedQty": "2.5"}

    result = asyncio.run(
        partial_profit_exit_order_execution(
            execution_enabled=True,
            submit_exit_order=submit_exit_order,
            market="futures",
            symbol="ETHUSDT",
            order_side="buy",
            qty=1.2,
            requested_qty=1.2,
            qty_open=2.5,
            remaining_qty=1.3,
            price=2800.0,
            full_exit_for_min_notional=False,
            exit_mode="partial",
            metadata={"thesis": "weak lane"},
        )
    )

    assert calls[0]["qty"] == 1.2
    assert calls[0]["allow_reduce_only_below_min_notional"] is False
    assert calls[1]["qty"] == 2.5
    assert calls[1]["allow_reduce_only_below_min_notional"] is True
    assert result["status"] == "sent"
    assert result["response"] == {"status": "FILLED", "executedQty": "2.5"}
    assert result["qty"] == 2.5
    assert result["remaining_qty"] == 0.0
    assert result["full_exit_for_min_notional"] is True
    assert result["exit_mode"] == "full_exit_min_notional_retry"
    assert result["metadata"]["partial_profit_retry_reason"] == (
        "Order notional below minimum"
    )


def test_partial_profit_exit_order_execution_retries_min_notional_response() -> None:
    calls: list[dict[str, object]] = []

    async def submit_exit_order(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            return {"status": "error", "error_message": "below minimum notional"}
        return {"status": "FILLED", "executedQty": "2.5"}

    result = asyncio.run(
        partial_profit_exit_order_execution(
            execution_enabled=True,
            submit_exit_order=submit_exit_order,
            market="futures",
            symbol="ETHUSDT",
            order_side="buy",
            qty=1.2,
            requested_qty=1.2,
            qty_open=2.5,
            remaining_qty=1.3,
            price=2800.0,
            full_exit_for_min_notional=False,
            exit_mode="partial",
            metadata={},
        )
    )

    assert len(calls) == 2
    assert calls[1]["qty"] == 2.5
    assert result["status"] == "sent"
    assert result["qty"] == 2.5
    assert result["remaining_qty"] == 0.0
    assert result["full_exit_for_min_notional"] is True
    assert result["metadata"]["partial_profit_retry_reason"] == "below minimum notional"


def test_exit_order_execution_returns_paper_when_execution_disabled() -> None:
    calls: list[dict[str, object]] = []

    async def submit_exit_order(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"status": "FILLED"}

    result = asyncio.run(
        exit_order_execution(
            execution_enabled=False,
            submit_exit_order=submit_exit_order,
            enrich_order_response_for_costs=None,
            block={"block_id": "block-1"},
            market="spot",
            symbol="BTCUSDT",
            order_side="sell",
            qty=0.2,
            price=110.0,
            allow_reduce_only_below_min_notional=False,
        )
    )

    assert calls == []
    assert result == {"status": "paper", "response": {}}


def test_exit_order_execution_enriches_sent_response() -> None:
    calls: list[dict[str, object]] = []

    async def submit_exit_order(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"status": "FILLED", "executedQty": "0.2"}

    async def enrich_order_response_for_costs(**kwargs: object) -> dict[str, object]:
        assert kwargs["include_funding"] is True
        response = dict(kwargs["response"])  # type: ignore[arg-type]
        response["funding_usdt"] = "0.01"
        return response

    result = asyncio.run(
        exit_order_execution(
            execution_enabled=True,
            submit_exit_order=submit_exit_order,
            enrich_order_response_for_costs=enrich_order_response_for_costs,
            block={"block_id": "block-1"},
            market="futures",
            symbol="ETHUSDT",
            order_side="buy",
            qty=1.5,
            price=2800.0,
            allow_reduce_only_below_min_notional=True,
        )
    )

    assert calls == [
        {
            "market": "futures",
            "symbol": "ETHUSDT",
            "side": "buy",
            "qty": 1.5,
            "price": 2800.0,
            "allow_reduce_only_below_min_notional": True,
        }
    ]
    assert result == {
        "status": "sent",
        "response": {
            "status": "FILLED",
            "executedQty": "0.2",
            "funding_usdt": "0.01",
        },
    }


def test_exit_order_execution_returns_error_response_on_submit_exception() -> None:
    async def submit_exit_order(**_: object) -> dict[str, object]:
        raise RuntimeError("exchange timeout")

    result = asyncio.run(
        exit_order_execution(
            execution_enabled=True,
            submit_exit_order=submit_exit_order,
            enrich_order_response_for_costs=None,
            block={"block_id": "block-1"},
            market="spot",
            symbol="BTCUSDT",
            order_side="sell",
            qty=0.2,
            price=110.0,
            allow_reduce_only_below_min_notional=False,
        )
    )

    assert result == {
        "status": "error",
        "response": {"status": "error", "error_message": "exchange timeout"},
    }


def test_response_error_message_prefers_top_level_then_raw_message() -> None:
    assert response_error_message({"error_message": "top level"}) == "top level"
    assert response_error_message({"raw": {"msg": "raw msg"}}) == "raw msg"
    assert response_error_message({"raw": {"message": "raw message"}}) == "raw message"
    assert response_error_message({}) == ""


def test_response_order_id_reads_top_level_and_raw_variants() -> None:
    assert response_order_id({"order_id": "abc"}) == "abc"
    assert response_order_id({"raw": {"orderId": 12345}}) == "12345"
    assert response_order_id({"raw": {"orderID": "X-1"}}) == "X-1"
    assert response_order_id({}) == ""


def test_is_min_notional_error_response_detects_minimum_notional_failures() -> None:
    assert is_min_notional_error_response(
        {"status": "rejected", "raw": {"msg": "Order below minimum notional"}}
    )
    assert is_min_notional_error_response(
        {"status": "error", "error_message": "below min order value"}
    )
    assert not is_min_notional_error_response({"status": "filled"})
    assert not is_min_notional_error_response({"status": "error", "msg": "rate limited"})


def test_entry_not_filled_reason_prefers_exchange_expiry_reason() -> None:
    assert entry_not_filled_reason(
        {"raw": {"expiryReason": "IOC_NO_FILL"}, "error_message": "no fill"},
        fallback_status="EXPIRED",
    ) == "entry order not filled: EXPIRED; IOC_NO_FILL"
    assert entry_not_filled_reason(
        {"expiry_reason": "post only rejected"},
        fallback_status="EXPIRED",
    ) == "entry order not filled: EXPIRED; post only rejected"


def test_entry_order_side_uses_buy_for_spot_and_direction_for_futures() -> None:
    assert entry_order_side({"market": "spot", "side": "short"}) == "buy"
    assert entry_order_side({"market": "upbit_spot", "side": "short"}) == "buy"
    assert entry_order_side({"market": "futures", "side": "short"}) == "sell"
    assert entry_order_side({"market": "futures", "side": "long"}) == "buy"


def test_filled_order_price_uses_weighted_trade_fills() -> None:
    assert filled_order_price(
        {
            "response": {
                "status": "FILLED",
                "trade_fills": [
                    {"price": "100.0", "qty": "0.2"},
                    {"price": "110.0", "qty": "0.8"},
                    {"price": "999.0", "qty": "0"},
                    "ignored",
                ],
            }
        }
    ) == 108.0


def test_filled_order_price_reads_response_and_raw_fallbacks() -> None:
    assert filled_order_price(
        {"response": {"status": "FILLED", "avg_fill_price": "102.5"}}
    ) == 102.5
    assert filled_order_price(
        {
            "status": "paper",
            "response": {"raw": {"avgPrice": "97.25"}},
        }
    ) == 97.25
    assert filled_order_price(
        {
            "status": "filled",
            "response": {"status": "FILLED"},
            "avg_price": "91.75",
        }
    ) == 91.75
    assert filled_order_price(
        {"status": "open", "response": {"status": "NEW", "avg_fill_price": "102.5"}}
    ) == 0.0
