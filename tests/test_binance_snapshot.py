from __future__ import annotations

from pathlib import Path

from tradecraft.services.binance_snapshot import (
    attach_performance_reflections,
    block_history_rows,
    compact_snapshot_manager_run,
    enrich_blocks_with_latest_quotes,
    manager_run_with_decision_context,
    visible_block_rows,
)
from tradecraft.services import binance_snapshot as binance_snapshot_module


def test_binance_block_trader_does_not_reown_snapshot_history_helper() -> None:
    source = Path("src/tradecraft/services/binance_block_trader.py").read_text()

    assert "def _block_history_rows(" not in source
    assert "self._block_history_rows(" not in source


def test_binance_snapshot_owns_account_normalization() -> None:
    source = Path("src/tradecraft/services/binance_block_trader.py").read_text()
    normalizer = getattr(binance_snapshot_module, "normalize_account_snapshot", None)

    assert callable(normalizer)
    assert "def _normalize_account_snapshot(" not in source
    assert "self._normalize_account_snapshot(" not in source


def test_normalize_account_snapshot_preserves_upbit_fx_and_cash_conversion() -> None:
    normalizer = getattr(binance_snapshot_module, "normalize_account_snapshot", None)

    assert callable(normalizer)
    account = normalizer(
        {
            "status": "ok",
            "spot_available_usdt": "12.5",
            "available_balance_usdt": "7.5",
            "upbit_cash_krw": 280_000.0,
            "upbit_spot_assets": [
                {"kind": "position", "symbol": "KRW-BTC", "value_krw": 1_400_000.0}
            ],
        },
        default_upbit_usdt_krw_rate=1_400.0,
    )

    assert account["spot_cash_usdt"] == 12.5
    assert account["futures_cash_usdt"] == 7.5
    assert account["upbit_cash_krw"] == 280_000.0
    assert account["upbit_cash_usdt"] == 200.0
    assert account["upbit_usdt_krw_rate"] == 1_400.0
    assert account["upbit_spot_assets"][0]["symbol"] == "KRW-BTC"


def test_visible_block_rows_keeps_only_visible_statuses() -> None:
    blocks = [
        {"block_id": "proposed", "status": "proposed"},
        {"block_id": "open", "status": "open"},
        {"block_id": "closed", "status": "closed"},
        {"block_id": "error", "status": "error", "qty_open": 1},
    ]

    assert [row["block_id"] for row in visible_block_rows(blocks)] == [
        "proposed",
        "open",
        "error",
    ]


def test_visible_block_rows_hides_zero_qty_frozen_error_blocks() -> None:
    blocks = [
        {
            "block_id": "frozen-upbit-scale-error",
            "status": "error",
            "qty_open": 0,
            "risk_note": "frozen: upbit KRW/USDT price-scale mismatch repaired in code",
        },
        {
            "block_id": "dangling-error",
            "status": "error",
            "qty_open": 0.5,
        },
        {"block_id": "proposed", "status": "proposed", "qty_open": 0},
    ]

    assert [row["block_id"] for row in visible_block_rows(blocks)] == [
        "dangling-error",
        "proposed",
    ]


def test_block_history_rows_keeps_closed_and_error_sorted_by_latest_activity() -> None:
    blocks = [
        {
            "block_id": "old-closed",
            "status": "closed",
            "closed_at": "2026-06-19T00:00:00+00:00",
        },
        {
            "block_id": "latest-error",
            "status": "error",
            "updated_at": "2026-06-21T00:00:00+00:00",
        },
        {
            "block_id": "active",
            "status": "open",
            "updated_at": "2026-06-22T00:00:00+00:00",
        },
        {
            "block_id": "middle-closed",
            "status": "closed",
            "created_at": "2026-06-20T00:00:00+00:00",
        },
    ]

    rows = block_history_rows(blocks, limit=2)

    assert [row["block_id"] for row in rows] == ["latest-error", "middle-closed"]


def test_enrich_blocks_with_latest_quotes_attaches_quote_and_long_short_pnl() -> None:
    blocks = [
        {
            "block_id": "long",
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": "100",
            "qty_open": "2",
        },
        {
            "block_id": "short",
            "symbol": "ETHUSDT",
            "market": "binance_futures",
            "side": "short",
            "entry_price": "200",
            "qty_open": "3",
        },
        {
            "block_id": "missing",
            "symbol": "SOLUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": "10",
            "qty_open": "4",
        },
    ]
    quotes = {
        ("spot", "BTCUSDT"): {"price": "110"},
        ("futures", "ETHUSDT"): {"price": "180"},
    }

    enriched = enrich_blocks_with_latest_quotes(blocks, quotes)

    assert enriched[0]["quote"] == {"price": "110"}
    assert enriched[0]["current_price"] == 110.0
    assert enriched[0]["current_price_usdt"] == 110.0
    assert enriched[0]["unrealized_pnl_usdt"] == 20.0
    assert enriched[1]["current_price"] == 180.0
    assert enriched[1]["unrealized_pnl_usdt"] == 60.0
    assert "quote" not in enriched[2]
    assert "unrealized_pnl_usdt" not in enriched[2]
    assert "quote" not in blocks[0]


def test_attach_performance_reflections_adds_realized_fields_without_mutating_input() -> None:
    blocks = [
        {"block_id": "closed-1", "status": "closed"},
        {"block_id": "closed-2", "status": "closed"},
    ]
    reflections = {
        "closed-1": {
            "pnl_usdt": "4.5",
            "r_multiple": "1.2",
            "exit_price": "120.0",
        }
    }

    enriched = attach_performance_reflections(blocks, reflections)

    assert enriched[0]["performance"] == reflections["closed-1"]
    assert enriched[0]["performance_reflection"] == reflections["closed-1"]
    assert enriched[0]["realized_pnl_usdt"] == 4.5
    assert enriched[0]["r_multiple"] == 1.2
    assert enriched[0]["exit_price"] == 120.0
    assert enriched[1] == {"block_id": "closed-2", "status": "closed"}
    assert "performance" not in blocks[0]


def test_manager_run_with_decision_context_uses_prompt_context_builder() -> None:
    row = {
        "id": 3,
        "prompt": {"context": {"candidate_count": 2}},
        "response": {"hold_decision": {"summary": "관망"}},
        "actions": {"create_blocks": [{"symbol": "BTCUSDT"}]},
    }

    def compact_context(
        prompt: dict,
        *,
        response: dict,
        actions: dict,
        hold_decision: dict,
    ) -> dict:
        return {
            "prompt": prompt,
            "response": response,
            "actions": actions,
            "hold_decision": hold_decision,
        }

    enriched = manager_run_with_decision_context(
        row,
        compact_prompt_context=compact_context,
    )

    assert enriched["id"] == 3
    assert enriched["decision_context"] == {
        "prompt": {"context": {"candidate_count": 2}},
        "response": {"hold_decision": {"summary": "관망"}},
        "actions": {"create_blocks": [{"symbol": "BTCUSDT"}]},
        "hold_decision": {"summary": "관망"},
    }


def test_compact_snapshot_manager_run_counts_actions_and_normalizes_hold_decision() -> None:
    row = {
        "id": 7,
        "run_at": "2026-06-21T00:00:00+00:00",
        "status": "ok",
        "mode": "regular",
        "model": "gpt-5.5",
        "error_message": "",
        "prompt": {"context": {"candidate_count": 5}},
        "response": {"raw": "payload"},
        "actions": {
            "adopt_existing_blocks": [{"symbol": "ETHUSDT"}],
            "create_blocks": [{"symbol": "BTCUSDT"}],
            "update_blocks": [{"block_id": "b1"}, {"block_id": "b2"}],
            "close_blocks": [],
            "pause_blocks": [{"block_id": "b3"}],
        },
        "hold_decision": {"summary": "원본"},
    }

    compacted = compact_snapshot_manager_run(
        row,
        normalize_hold_decision=lambda response, actions: {
            "summary": response["hold_decision"]["summary"],
            "action_keys": sorted(actions),
        },
        compact_response_payload=lambda response: {"keys": sorted(response)},
        compact_prompt_context=lambda prompt, *, response, actions, hold_decision: {
            "candidate_count": prompt["context"]["candidate_count"],
            "hold": hold_decision["summary"],
            "action_count": sum(len(v) for v in actions.values() if isinstance(v, list)),
        },
    )

    assert compacted == {
        "id": 7,
        "run_at": "2026-06-21T00:00:00+00:00",
        "status": "ok",
        "mode": "regular",
        "model": "gpt-5.5",
        "error_message": "",
        "workflow_id": None,
        "workflow_version": None,
        "skill_ids": [],
        "contract_ids": [],
        "action_count": 5,
        "actions": row["actions"],
        "hold_decision": {
            "summary": "원본",
            "action_keys": [
                "adopt_existing_blocks",
                "close_blocks",
                "create_blocks",
                "pause_blocks",
                "update_blocks",
            ],
        },
        "decision_payload": {"keys": ["raw"]},
        "decision_context": {
            "candidate_count": 5,
            "hold": "원본",
            "action_count": 5,
        },
    }
