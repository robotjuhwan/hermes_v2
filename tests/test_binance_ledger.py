from __future__ import annotations

from pathlib import Path

from tradecraft.services.binance_ledger import (
    ledger_json_dumps,
    ledger_json_loads,
    parse_iso_datetime,
    row_order_payload,
    row_to_block,
    row_to_event,
    row_to_manager_run,
    row_to_order,
    row_to_performance_reflection,
    safe_float,
    safe_int,
    utc_now_iso,
)

ROOT = Path(__file__).resolve().parents[1]


def test_binance_ledger_owns_lane_allocation_summary() -> None:
    ledger_source = (ROOT / "src/tradecraft/services/binance_ledger.py").read_text()
    lane_source = (ROOT / "src/tradecraft/services/binance_lane.py").read_text()
    trader_source = (ROOT / "src/tradecraft/services/binance_block_trader.py").read_text()

    assert "def build_lane_allocation_summary(" in ledger_source
    assert "def build_lane_allocation_summary(" not in lane_source
    assert "def _lane_allocation_summary(" not in trader_source
    assert "self._lane_allocation_summary(" not in trader_source


def test_binance_repository_does_not_reown_ledger_row_converters() -> None:
    source = (ROOT / "src/tradecraft/services/binance_block_trader.py").read_text()
    repository_start = source.index("class BinanceBlockRepository")
    repository_end = source.index("class BinanceBlockTrader:", repository_start)
    repository_source = source[repository_start:repository_end]

    for marker in (
        "def _row_to_block",
        "def _row_order_payload",
        "def _row_to_order",
        "def _row_to_event",
        "def _row_to_performance_reflection",
        "def _row_to_manager_run",
    ):
        assert marker not in repository_source
    for marker in (
        "build_row_to_block(",
        "build_row_order_payload(",
        "build_row_to_order(",
        "build_row_to_event(",
        "build_row_to_performance_reflection(",
        "build_row_to_manager_run(",
    ):
        assert marker in repository_source


def test_binance_block_trader_uses_ledger_primitive_helpers() -> None:
    source = (ROOT / "src/tradecraft/services/binance_block_trader.py").read_text()

    for marker in (
        "def utc_now_iso",
        "def _parse_iso_datetime",
        "def _json_dumps",
        "def _json_loads",
        "def _safe_float",
        "def _safe_int",
    ):
        assert marker not in source


def test_ledger_primitive_helpers_normalize_time_json_and_numbers() -> None:
    now = utc_now_iso()
    parsed = parse_iso_datetime(now)

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert ledger_json_dumps({"b": 1, "a": "한글"}) == '{"a":"한글","b":1}'
    assert ledger_json_loads('{"ok": true}', {}) == {"ok": True}
    assert ledger_json_loads("{bad", {"fallback": True}) == {"fallback": True}
    assert safe_float(None) == 0.0
    assert safe_float("nan") == 0.0
    assert safe_float("12.5") == 12.5
    assert safe_int("12.9") == 12
    assert safe_int("bad") == 0


def test_row_order_payload_decodes_response_json_and_numeric_quantity() -> None:
    payload = row_order_payload(
        {
            "block_id": "blk_1",
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "buy",
            "qty": "0.12",
            "order_type": "LIMIT",
            "status": "FILLED",
            "reason": "entry",
            "response_json": '{"orderId": 123}',
            "created_at": "2026-06-20T00:00:00+00:00",
            "updated_at": "2026-06-20T00:00:01+00:00",
        }
    )

    assert payload["qty"] == 0.12
    assert payload["response"] == {"orderId": 123}
    assert payload["market"] == "spot"


def test_row_order_payload_exposes_effective_fill_from_binance_response() -> None:
    payload = row_order_payload(
        {
            "block_id": "blk_1",
            "symbol": "MEGAUSDT",
            "market": "spot",
            "side": "buy",
            "qty": "371.609067261241",
            "order_type": "LIMIT_IOC",
            "status": "sent",
            "reason": "entry_order",
            "response_json": (
                '{"status":"FILLED","executed_qty":"371.60000000",'
                '"cum_quote":"19.91776000","raw":{"avgPrice":"0.0536"}}'
            ),
            "created_at": "2026-07-05T07:25:25+00:00",
            "updated_at": "2026-07-05T07:25:25+00:00",
        }
    )

    assert payload["status"] == "sent"
    assert payload["execution_status"] == "filled"
    assert payload["filled_qty"] == 371.6
    assert payload["filled_quote"] == 19.91776
    assert payload["effective_fill"] is True


def test_row_to_order_and_event_add_database_ids() -> None:
    order = row_to_order(
        {
            "id": 7,
            "block_id": "blk_1",
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 2,
            "order_type": "LIMIT",
            "status": "NEW",
            "reason": "exit",
            "response_json": "{}",
            "created_at": "created",
            "updated_at": "updated",
        }
    )
    event = row_to_event(
        {
            "id": 9,
            "block_id": "blk_1",
            "event_type": "closed",
            "message": "target hit",
            "payload_json": '{"price": 123.45}',
            "created_at": "created",
        }
    )

    assert order["id"] == 7
    assert order["qty"] == 2.0
    assert event["id"] == 9
    assert event["payload"] == {"price": 123.45}


def test_row_to_manager_run_decodes_payloads_and_workflow_ids() -> None:
    run = row_to_manager_run(
        {
            "id": 3,
            "run_at": "2026-06-20T00:00:00+00:00",
            "status": "ok",
            "mode": "manager",
            "model": "gpt-5.5",
            "error_message": "",
            "workflow_id": "binance_cycle",
            "workflow_version": "2",
            "skill_ids_json": '["jue-binance-trading"]',
            "contract_ids_json": '["block_action"]',
            "prompt_json": '{"task": "manage"}',
            "response_json": '{"hold_decision": {"summary": "wait"}}',
            "actions_json": '{"create_blocks": []}',
        }
    )

    assert run["id"] == 3
    assert run["workflow_version"] == 2
    assert run["skill_ids"] == ["jue-binance-trading"]
    assert run["contract_ids"] == ["block_action"]
    assert run["prompt"] == {"task": "manage"}
    assert run["actions"] == {"create_blocks": []}


def test_row_to_block_normalizes_market_horizon_lane_and_metadata() -> None:
    block = row_to_block(
        {
            "block_id": "blk_1",
            "symbol": "ETHUSDT",
            "market": "binance_futures",
            "side": "short",
            "qty_initial": "2",
            "qty_open": "1.5",
            "entry_price": 100.0,
            "target_price": 80.0,
            "stop_price": 110.0,
            "leverage": "2",
            "margin_type": "isolated",
            "liquidation_price": 150.0,
            "thesis": "short thesis",
            "llm_reason": "manager",
            "risk_note": "risk",
            "created_by": "llm",
            "manager_run_id": 4,
            "status": "open",
            "force_exit_requested": 1,
            "metadata_json": '{"horizon": "mid", "validation_repair": {"keep": true}}',
            "created_at": "created",
            "updated_at": "updated",
            "opened_at": "opened",
            "closed_at": "",
        },
        compact_validation_repair=lambda value: {"compacted": value["keep"]},
    )

    assert block["market"] == "futures"
    assert block["horizon"] == "futures"
    assert block["lane"] == "futures"
    assert block["block_color"] == "futures"
    assert block["qty_initial"] == 2.0
    assert block["qty_open"] == 1.5
    assert block["force_exit_requested"] is True
    assert block["metadata"]["validation_repair"] == {"compacted": True}


def test_row_to_block_uses_upbit_lane_and_preserves_custom_block_color() -> None:
    block = row_to_block(
        {
            "block_id": "blk_2",
            "symbol": "KRW-BTC",
            "market": "upbit",
            "side": "long",
            "qty_initial": 1,
            "qty_open": 1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "leverage": 1,
            "margin_type": "",
            "liquidation_price": None,
            "thesis": "",
            "llm_reason": "",
            "risk_note": "",
            "created_by": "llm",
            "manager_run_id": None,
            "status": "proposed",
            "force_exit_requested": 0,
            "metadata_json": '{"horizon": "long", "block_color": "custom"}',
            "created_at": "created",
            "updated_at": "updated",
            "opened_at": "",
            "closed_at": "",
        }
    )

    assert block["market"] == "upbit_spot"
    assert block["horizon"] == "long"
    assert block["lane"] == "upbit_spot:long"
    assert block["block_color"] == "custom"
    assert block["metadata"]["block_color"] == "custom"


def test_row_to_performance_reflection_normalizes_costs_and_lesson_payload() -> None:
    canonical_calls = []
    entry_quality_calls = []

    def canonical_lane(*, raw_lane, market, side):
        canonical_calls.append((raw_lane, market, side))
        return f"{market}:{side}:canonical"

    def entry_quality_label(*sources):
        entry_quality_calls.append(sources)
        return "strong"

    reflection = row_to_performance_reflection(
        {
            "block_id": "blk_perf",
            "symbol": "NEARUSDT",
            "market": "binance_futures",
            "side": "sell",
            "lane": "intraday",
            "entry_price": "1.20",
            "exit_price": "1.10",
            "stop_price": "1.30",
            "target_price": "1.00",
            "pnl_usdt": "3.5",
            "gross_pnl_usdt": "4.0",
            "net_pnl_usdt": "3.1",
            "fee_usdt": "0.2",
            "funding_usdt": "0.1",
            "slippage_usdt": "0.05",
            "spread_usdt": "0.03",
            "cost_source": "estimated",
            "r_multiple": "1.4",
            "mfe_r_multiple": "2.1",
            "mae_r_multiple": "-0.3",
            "pattern_key": "pullback",
            "lesson_json": '{"present_cost_components": ["fee", "funding"], "note": "ok"}',
            "block_metadata_json": '{"entry_quality": "strong"}',
            "created_at": "2026-06-20T00:00:00+00:00",
        },
        canonical_performance_lane=canonical_lane,
        entry_quality_label=entry_quality_label,
    )

    assert reflection["market"] == "futures"
    assert reflection["side"] == "short"
    assert reflection["lane"] == "futures:short:canonical"
    assert reflection["entry_price"] == 1.2
    assert reflection["gross_pnl_usdt"] == 4.0
    assert reflection["net_pnl_usdt"] == 3.1
    assert reflection["total_cost_usdt"] == 0.38
    assert reflection["present_cost_components"] == ["fee", "funding"]
    assert reflection["lesson"] == {
        "present_cost_components": ["fee", "funding"],
        "note": "ok",
    }
    assert reflection["entry_quality_label"] == "strong"
    assert canonical_calls == [("intraday", "futures", "short")]
    assert entry_quality_calls == [
        (
            {"present_cost_components": ["fee", "funding"], "note": "ok"},
            {"entry_quality": "strong"},
        )
    ]
