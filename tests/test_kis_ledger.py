from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_ledger import (
    ledger_json_dumps,
    ledger_json_loads,
    parse_iso_datetime,
    row_to_block,
    row_to_event,
    row_to_manager_run,
    row_to_order,
    safe_float,
    safe_int,
    utc_now_iso,
)

ROOT = Path(__file__).resolve().parents[1]


def test_kis_ledger_owns_allocation_summary_helpers() -> None:
    ledger_source = (ROOT / "src/tradecraft/services/kis_ledger.py").read_text()
    horizon_source = (ROOT / "src/tradecraft/services/kis_horizon.py").read_text()
    trader_source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def build_allocation_summary(" in ledger_source
    assert "def build_horizon_allocation_summary(" in ledger_source
    assert "def build_allocation_summary(" not in horizon_source
    assert "def build_horizon_allocation_summary(" not in horizon_source
    assert "def _allocation_summary(" not in trader_source
    assert "def _horizon_allocation_summary(" not in trader_source
    assert "self._allocation_summary(" not in trader_source
    assert "self._horizon_allocation_summary(" not in trader_source


def test_kis_ledger_owns_position_allocation_helpers() -> None:
    ledger_source = (ROOT / "src/tradecraft/services/kis_ledger.py").read_text()
    reconciliation_source = (
        ROOT / "src/tradecraft/services/kis_reconciliation.py"
    ).read_text()
    trader_source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def positions_by_symbol(" in ledger_source
    assert "def unallocated_qty_by_symbol(" in ledger_source
    assert "def positions_by_symbol(" not in reconciliation_source
    assert "def unallocated_qty_by_symbol(" not in reconciliation_source
    assert "def _positions_by_symbol(" not in trader_source
    assert "def _unallocated_qty_by_symbol(" not in trader_source
    assert "self._positions_by_symbol(" not in trader_source
    assert "self._unallocated_qty_by_symbol(" not in trader_source


def test_kis_repository_does_not_reown_ledger_row_converters() -> None:
    source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()
    repository_start = source.index("class KISBlockRepository")
    repository_end = source.index("class KISBlockTrader:", repository_start)
    repository_source = source[repository_start:repository_end]

    for marker in (
        "def _row_to_block(",
        "def _row_to_order(",
        "def _row_to_event(",
        "def _row_to_manager_run(",
    ):
        assert marker not in repository_source
    for marker in (
        "build_row_to_block(",
        "build_row_to_order(",
        "build_row_to_event(",
        "build_row_to_manager_run(",
    ):
        assert marker in repository_source


def test_kis_block_trader_uses_ledger_primitive_helpers() -> None:
    source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()

    for marker in (
        "def utc_now_iso",
        "def _safe_float",
        "def _safe_int",
        "def _json_dumps",
        "def _json_loads",
        "def _parse_iso_datetime",
    ):
        assert marker not in source


def test_kis_ledger_primitive_helpers_normalize_time_json_and_numbers() -> None:
    now = utc_now_iso()
    parsed = parse_iso_datetime(now)

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert ledger_json_dumps({"b": 1, "a": "한글"}) == '{"a":"한글","b":1}'
    assert ledger_json_loads('{"ok": true}', {}) == {"ok": True}
    assert ledger_json_loads("{bad", {"fallback": True}) == {"fallback": True}
    assert safe_float(None) == 0.0
    assert safe_float("N/A") == 0.0
    assert safe_float("nan") == 0.0
    assert safe_float("12.5%") == 12.5
    assert safe_int("12.9") == 12
    assert safe_int("bad") == 0


def test_row_to_block_decodes_metadata_and_numeric_fields() -> None:
    block = row_to_block(
        {
            "block_id": "blk_005930",
            "symbol": "005930",
            "name": "삼성전자",
            "qty_initial": "3",
            "qty_open": "2",
            "entry_price": 70000.0,
            "target_price": 76000.0,
            "stop_price": 67000.0,
            "thesis": "mid thesis",
            "llm_reason": "manager",
            "risk_note": "risk",
            "created_by": "jue",
            "manager_run_id": 11,
            "status": "open",
            "force_exit_requested": 1,
            "metadata_json": '{"horizon": "mid", "tags": ["value"]}',
            "created_at": "created",
            "updated_at": "updated",
            "opened_at": "opened",
            "closed_at": "",
        }
    )

    assert block["symbol"] == "005930"
    assert block["name"] == "삼성전자"
    assert block["qty_initial"] == 3
    assert block["qty_open"] == 2
    assert block["force_exit_requested"] is True
    assert block["metadata"] == {"horizon": "mid", "tags": ["value"]}


def test_row_to_order_decodes_cancel_and_response_json() -> None:
    order = row_to_order(
        {
            "id": "7",
            "block_id": "blk_005930",
            "symbol": "005930",
            "side": "buy",
            "qty": "3",
            "limit_price": "70000",
            "order_type": "00",
            "status": "filled",
            "order_no": "123",
            "order_orgno": "",
            "reason": "entry",
            "filled_qty": "2",
            "remaining_qty": "1",
            "avg_fill_price": 69900.5,
            "last_checked_at": "checked",
            "cancel_requested": 0,
            "cancel_order_no": "",
            "cancel_response_json": '{"rt_cd": "0"}',
            "response_json": '{"odno": "123"}',
            "created_at": "created",
            "updated_at": "updated",
        }
    )

    assert order["id"] == 7
    assert order["qty"] == 3
    assert order["limit_price"] == 70000
    assert order["filled_qty"] == 2
    assert order["remaining_qty"] == 1
    assert order["cancel_requested"] is False
    assert order["cancel_response"] == {"rt_cd": "0"}
    assert order["response"] == {"odno": "123"}


def test_row_to_event_decodes_payload_json() -> None:
    event = row_to_event(
        {
            "id": "9",
            "block_id": "blk_005930",
            "event_type": "target_hit",
            "message": "target",
            "payload_json": '{"price": 76000}',
            "created_at": "created",
        }
    )

    assert event["id"] == 9
    assert event["payload"] == {"price": 76000}


def test_row_to_manager_run_splits_applied_actions_and_sanitizes_response() -> None:
    hold_calls = []
    creative_calls = []

    def safe_int(value):
        return int(value or 0)

    def sanitize_hold(value, *, action_count):
        hold_calls.append((value, action_count))
        return {"summary": value.get("summary"), "action_count": action_count}

    def sanitize_creative(value):
        creative_calls.append(value)
        return [{"hypothesis": item.get("title")} for item in value]

    run = row_to_manager_run(
        {
            "id": "3",
            "run_at": "2026-06-20T00:00:00+09:00",
            "market_session": "regular",
            "status": "ok",
            "mode": "manager",
            "model": "gpt-5.5",
            "error_message": "",
            "workflow_id": "kis_cycle",
            "workflow_version": "2",
            "skill_ids_json": '["jue-kis-trading"]',
            "contract_ids_json": '["block_action"]',
            "prompt_json": '{"task": "manage"}',
            "response_json": (
                '{"hold_decision": {"summary": "wait"}, '
                '"creative_hypotheses": [{"title": "눌림목"}]}'
            ),
            "actions_json": (
                '{"create_blocks": [{"symbol": "005930"}], '
                '"update_blocks": [{"block_id": "blk"}], '
                '"_applied": {"created": 1}}'
            ),
        },
        safe_int=safe_int,
        sanitize_hold_decision=sanitize_hold,
        sanitize_creative_hypotheses=sanitize_creative,
    )

    assert run["id"] == 3
    assert run["workflow_version"] == 2
    assert run["skill_ids"] == ["jue-kis-trading"]
    assert run["contract_ids"] == ["block_action"]
    assert run["prompt"] == {"task": "manage"}
    assert run["response"]["hold_decision"] == {"summary": "wait"}
    assert run["actions"] == {
        "create_blocks": [{"symbol": "005930"}],
        "update_blocks": [{"block_id": "blk"}],
    }
    assert run["applied"] == {"created": 1}
    assert run["hold_decision"] == {"summary": "wait", "action_count": 2}
    assert run["creative_hypotheses"] == [{"hypothesis": "눌림목"}]
    assert hold_calls == [({"summary": "wait"}, 2)]
    assert creative_calls == [[{"title": "눌림목"}]]
