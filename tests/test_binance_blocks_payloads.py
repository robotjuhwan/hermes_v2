from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from tradecraft.api.binance_blocks import (
    _compact_runner_status,
    compact_binance_blocks_payload,
)
from tradecraft.api.binance_blocks_payloads import (
    build_binance_block_readiness_payload,
    build_binance_block_route_deps,
    build_binance_blocks_snapshot,
    build_binance_pattern_context_payload,
    build_binance_quant_signals_payload,
)


ROOT = Path(__file__).resolve().parents[1]


def test_build_binance_blocks_snapshot_prefers_compact_snapshot_when_requested() -> None:
    class _Trader:
        def snapshot(self) -> dict[str, Any]:
            raise AssertionError("full snapshot must not be called for compact status")

        def snapshot_compact(self) -> dict[str, Any]:
            return {
                "status": "ok",
                "compact": True,
                "manager_runs": [
                    {
                        "id": 1,
                        "status": "ok",
                        "actions": {"create_blocks": [{"symbol": "BTCUSDT"}]},
                        "response": {"hold_decision": {"summary": "관망"}},
                    }
                ],
            }

    payload = asyncio.run(build_binance_blocks_snapshot(_Trader(), compact=True))

    assert payload["compact"] is True
    assert "response" not in payload["manager_runs"][0]
    assert payload["manager_runs"][0]["action_count"] == 1


def test_compact_binance_blocks_payload_compacts_manager_run_actions() -> None:
    heavy_text = "x" * 20_000
    payload = {
        "status": "ok",
        "manager_runs": [
            {
                "id": 7,
                "run_at": "2026-06-29T00:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "model": "gpt-5.5",
                "actions": {
                    "create_blocks": [
                        {
                            "block_id": "b1",
                            "symbol": "BTCUSDT",
                            "market": "futures",
                            "side": "short",
                            "status": "waiting_entry",
                            "entry_price": 100.0,
                            "target_price": 90.0,
                            "stop_price": 105.0,
                            "thesis": heavy_text,
                            "candidate": {"raw": heavy_text},
                            "metadata": {"raw": heavy_text},
                        }
                    ],
                    "update_blocks": [],
                    "close_blocks": [],
                    "pause_blocks": [],
                },
                "response": {
                    "hold_decision": {
                        "summary": "관망",
                        "reasons": [heavy_text],
                    }
                },
            }
        ],
    }

    compact = compact_binance_blocks_payload(payload)
    run = compact["manager_runs"][0]
    create = run["actions"]["create_blocks"][0]

    assert run["run_id"] == 7
    assert run["started_at"] == "2026-06-29T00:00:00+00:00"
    assert run["action_count"] == 1
    assert create == {
        "block_id": "b1",
        "symbol": "BTCUSDT",
        "market": "futures",
        "side": "short",
        "status": "waiting_entry",
        "entry_price": 100.0,
        "target_price": 90.0,
        "stop_price": 105.0,
    }
    assert len(run["hold_decision"]["reasons"][0]) <= 160
    assert "candidate" not in create
    assert "metadata" not in create
    assert len(str(compact)) < 5_000


def test_compact_binance_blocks_payload_preserves_order_fill_summary() -> None:
    payload = {
        "status": "ok",
        "orders": [
            {
                "id": 4217,
                "block_id": "bnb_spot_MEGAUSDT",
                "symbol": "MEGAUSDT",
                "market": "spot",
                "side": "buy",
                "qty": 371.609067261241,
                "order_type": "LIMIT_IOC",
                "status": "sent",
                "execution_status": "filled",
                "filled_qty": 371.6,
                "filled_quote": 19.91776,
                "effective_fill": True,
                "reason": "entry_order",
                "response": {"status": "FILLED", "executed_qty": "371.60000000"},
                "created_at": "2026-07-05T07:25:25+00:00",
                "updated_at": "2026-07-05T07:25:25+00:00",
            }
        ],
    }

    compact = compact_binance_blocks_payload(payload)
    order = compact["orders"][0]

    assert order["status"] == "sent"
    assert order["execution_status"] == "filled"
    assert order["filled_qty"] == 371.6
    assert order["filled_quote"] == 19.91776
    assert order["effective_fill"] is True
    assert order["response"]["status"] == "FILLED"


def test_compact_binance_blocks_payload_exposes_latest_run_and_recent_errors() -> None:
    payload = {
        "status": "ok",
        "manager_runs": [
            {
                "id": 11,
                "run_at": "2026-06-30T09:55:13+00:00",
                "status": "ok",
                "mode": "llm",
                "model": "gpt-5.5",
                "actions": {},
                "response": {"hold_decision": {"summary": "관망"}},
            },
            {
                "id": 10,
                "run_at": "2026-06-30T09:19:06+00:00",
                "status": "error",
                "mode": "llm",
                "model": "gpt-5.5",
                "error_message": "codex native sdk timed out after 600.0s",
                "actions": {},
                "response": {},
            },
        ],
    }

    compact = compact_binance_blocks_payload(payload)

    assert compact["latest_manager_run"] == {
        "id": 11,
        "run_id": 11,
        "run_at": "2026-06-30T09:55:13+00:00",
        "started_at": "2026-06-30T09:55:13+00:00",
        "status": "ok",
        "mode": "llm",
        "model": "gpt-5.5",
        "error_message": None,
        "action_count": 0,
    }
    assert compact["recent_manager_errors"] == [
        {
            "id": 10,
            "run_id": 10,
            "run_at": "2026-06-30T09:19:06+00:00",
            "started_at": "2026-06-30T09:19:06+00:00",
            "status": "error",
            "mode": "llm",
            "model": "gpt-5.5",
            "error_message": "codex native sdk timed out after 600.0s",
            "action_count": 0,
        }
    ]
    assert "response" not in compact["latest_manager_run"]
    assert "actions" not in compact["latest_manager_run"]


def test_build_binance_blocks_snapshot_builds_status_fallback_from_list_blocks() -> None:
    class _Trader:
        def status(self) -> dict[str, Any]:
            return {"status": "ok", "source": "fallback"}

        def list_blocks(self) -> list[dict[str, Any]]:
            return [{"block_id": "b1", "symbol": "ETHUSDT"}]

    payload = asyncio.run(build_binance_blocks_snapshot(_Trader(), compact=False))

    assert payload == {
        "status": "ok",
        "source": "fallback",
        "blocks": [{"block_id": "b1", "symbol": "ETHUSDT"}],
    }


def test_build_binance_block_readiness_payload_summarizes_status_without_payload_bloat() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "compact": True,
            "enabled": True,
            "updated_at": "2026-06-28T00:00:00+00:00",
            "active_blocks": [{"block_id": "b1"}],
            "block_history": [{"block_id": "h1"} for _ in range(30)],
            "live_authority": {"huge": "x" * 10_000},
            "readiness": {"old": "nested"},
        },
        runner={"running": True},
        enabled=True,
        spot_live=True,
        futures_live=False,
        upbit_live=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=100,
        max_symbol_exposure_pct=10,
        min_reward_risk=1.5,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: "next",
    )

    assert payload["status"] == {
        "status": "ok",
        "enabled": True,
        "compact": True,
        "updated_at": "2026-06-28T00:00:00+00:00",
        "active_block_count": 1,
        "block_history_count": 30,
    }
    assert "live_authority" not in payload["status"]
    assert "readiness" not in payload["status"]


def test_build_binance_block_readiness_payload_exposes_activity_pressure() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "latest_decision_input": {
                "current_replay_pressure_status": "action_required",
                "current_replay_pressure_level": "high",
                "current_replay_pressure_source": "binance_activity_gap",
                "current_replay_zero_action_streak": 5,
                "current_replay_binance_zero_action_streak": 5,
                "current_replay_binance_activity_gap_status": "stale_binance_entries",
                "current_replay_binance_entry_stale_hours": 73.5,
                "current_replay_binance_candidate_symbols": ["ESPUSDT", "CHIPUSDT"],
            },
        },
        runner={},
        enabled=True,
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=0,
        max_symbol_exposure_pct=25,
        min_reward_risk=1.3,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: "next",
    )

    assert payload["activity_pressure"] == {
        "status": "action_required",
        "level": "high",
        "source": "binance_activity_gap",
        "zero_action_streak": 5,
        "binance_zero_action_streak": 5,
        "activity_gap_status": "stale_binance_entries",
        "entry_stale_hours": 73.5,
        "candidate_symbols": ["ESPUSDT", "CHIPUSDT"],
    }
    assert payload["warnings"] == ["binance_activity_pressure_open"]


def test_build_binance_block_readiness_payload_exposes_entry_activity() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "entry_activity": {
                "version": "binance_entry_activity_v1",
                "status": "stale_binance_entries",
                "latest_binance_entry_at": "2026-07-05T00:00:00+00:00",
                "latest_binance_entry_market": "futures",
                "latest_upbit_entry_at": "2026-07-07T23:00:00+00:00",
                "binance_entry_stale_hours": 73.5,
                "binance_entry_count": 2,
                "upbit_entry_count": 4,
                "raw": "x" * 20_000,
            },
        },
        runner={},
        enabled=True,
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=0,
        max_symbol_exposure_pct=25,
        min_reward_risk=1.3,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: "next",
    )

    assert payload["entry_activity"] == {
        "version": "binance_entry_activity_v1",
        "status": "stale_binance_entries",
        "latest_binance_entry_at": "2026-07-05T00:00:00+00:00",
        "latest_binance_entry_market": "futures",
        "latest_upbit_entry_at": "2026-07-07T23:00:00+00:00",
        "binance_entry_stale_hours": 73.5,
        "binance_entry_count": 2,
        "upbit_entry_count": 4,
    }
    assert "warnings" not in payload


def test_build_binance_block_readiness_payload_exposes_contract_replay_recovery() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "latest_decision_input": {
                "contract_replay_status": "stored_error_resolved_by_current_contract",
                "stored_error_message": "missing required field action_type",
                "current_contract_error": "",
                "action_count": 1,
                "current_replay_action_count": 3,
                "current_replay_auto_action_count": 2,
                "current_replay_action_sections": [
                    "create_blocks",
                    "update_blocks",
                ],
                "current_replay_hold_summary": "stored failure now replays",
                "current_replay_watch_symbols": ["BTCUSDT", "ETHUSDT"],
                "current_replay_next_triggers": [
                    {
                        "symbol": "ETHUSDT",
                        "market": "futures",
                        "condition": "pattern prior recovers",
                        "price": 0.0,
                        "reason": "pattern prior missing",
                    }
                ],
                "current_replay_data_gaps": ["pattern prior missing"],
                "current_replay_auto_create_preview": [
                    {
                        "symbol": "ETHUSDT",
                        "market": "futures",
                        "side": "short",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 2475.0,
                        "entry_trigger_operator": ">=",
                        "entry_price": 2475.0,
                        "target_price": 2300.0,
                        "stop_price": 2525.0,
                        "qty": 0.01,
                        "quote_budget_usdt": 24.75,
                        "min_executable_notional_usdt": 20.0,
                        "min_executable_qty": 0.008081,
                        "notional_estimate_usdt": 24.75,
                        "auto_materialized_reason": (
                            "manager_selected_probe_waiting_block_without_create_action"
                        ),
                        "raw": "drop me",
                    }
                ],
                "raw_replay_payload": {"heavy": "drop me"},
            },
        },
        runner={},
        enabled=True,
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=0,
        max_symbol_exposure_pct=25,
        min_reward_risk=1.3,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: "next",
    )

    assert payload["manager_contract_replay"] == {
        "contract_replay_status": "stored_error_resolved_by_current_contract",
        "stored_error_message": "missing required field action_type",
        "action_count": 1,
        "current_replay_action_count": 3,
        "current_replay_auto_action_count": 2,
        "current_replay_action_sections": ["create_blocks", "update_blocks"],
        "current_replay_hold_summary": "stored failure now replays",
        "current_replay_watch_symbols": ["BTCUSDT", "ETHUSDT"],
        "current_replay_next_triggers": [
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "condition": "pattern prior recovers",
                "price": 0.0,
                "reason": "pattern prior missing",
            }
        ],
        "current_replay_data_gaps": ["pattern prior missing"],
        "current_replay_auto_create_preview": [
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "short",
                "entry_style": "wait_for_price",
                "entry_trigger_price": 2475.0,
                "entry_trigger_operator": ">=",
                "entry_price": 2475.0,
                "target_price": 2300.0,
                "stop_price": 2525.0,
                "qty": 0.01,
                "quote_budget_usdt": 24.75,
                "min_executable_notional_usdt": 20.0,
                "min_executable_qty": 0.008081,
                "notional_estimate_usdt": 24.75,
                "auto_materialized_reason": (
                    "manager_selected_probe_waiting_block_without_create_action"
                ),
            }
        ],
    }
    assert payload["warnings"] == ["binance_manager_contract_replay_recovered"]


def test_build_binance_block_readiness_payload_exposes_current_contract_replay_error() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "latest_decision_input": {
                "contract_replay_status": "current_contract_error",
                "stored_error_message": "validation_repair_resolution_missing_from_model",
                "current_contract_error": (
                    "binance_activity_gap_resolution_missing_from_model"
                ),
                "action_count": 0,
                "current_replay_action_count": 0,
                "current_replay_pressure_status": "action_required",
                "current_replay_pressure_source": "binance_activity_gap",
                "raw_replay_payload": {"heavy": "drop me"},
            },
        },
        runner={},
        enabled=True,
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=0,
        max_symbol_exposure_pct=25,
        min_reward_risk=1.3,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: "next",
    )

    assert payload["manager_contract_replay"] == {
        "contract_replay_status": "current_contract_error",
        "stored_error_message": "validation_repair_resolution_missing_from_model",
        "current_contract_error": (
            "binance_activity_gap_resolution_missing_from_model"
        ),
        "action_count": 0,
        "current_replay_action_count": 0,
    }
    assert payload["warnings"] == [
        "binance_activity_pressure_open",
        "binance_manager_contract_replay_current_error",
    ]


def test_build_binance_block_readiness_payload_separates_recovered_manager_error() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "latest_manager_run_at": "2026-06-30T09:55:13+00:00",
            "latest_manager_status": "ok",
            "manager_operational_status": "ok",
            "latest_manager_mode": "llm",
            "latest_manager_error": {
                "run_at": "2026-06-29T13:09:59+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "codex native sdk timed out after 600.0s",
            },
            "latest_manager_error_recovered": True,
            "latest_unresolved_manager_error": {},
        },
        runner={},
        enabled=True,
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=0,
        max_symbol_exposure_pct=25,
        min_reward_risk=1.3,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: f"{latest}|{interval}",
    )

    assert payload["status"]["latest_manager_status"] == "ok"
    assert payload["status"]["manager_operational_status"] == "ok"
    assert payload["status"]["latest_manager_mode"] == "llm"
    assert "latest_manager_error" not in payload["status"]
    assert payload["status"]["latest_recovered_manager_error"] == {
        "run_at": "2026-06-29T13:09:59+00:00",
        "status": "error",
        "mode": "llm",
        "error_message": "codex native sdk timed out after 600.0s",
    }
    assert payload["status"]["latest_manager_error_recovered"] is True
    assert "latest_unresolved_manager_error" not in payload["status"]


def test_build_binance_block_readiness_payload_suppresses_replay_recovered_error() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "latest_manager_run_at": "2026-07-08T00:00:00+00:00",
            "latest_manager_status": "error",
            "manager_operational_status": "manager_error_pending_next_run",
            "latest_manager_mode": "llm",
            "latest_unresolved_manager_error": {
                "run_at": "2026-07-08T00:00:00+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "validation_repair_resolution_missing_from_model",
            },
            "latest_manager_error_recovered": False,
            "latest_decision_input": {
                "contract_replay_status": "stored_error_resolved_by_current_contract",
                "stored_error_message": "validation_repair_resolution_missing_from_model",
                "current_contract_error": "",
                "action_count": 0,
                "current_replay_action_count": 0,
            },
        },
        runner={},
        enabled=True,
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=0,
        max_symbol_exposure_pct=25,
        min_reward_risk=1.3,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: "next",
    )

    assert "latest_unresolved_manager_error" not in payload["status"]
    assert payload["status"]["latest_manager_error_recovered"] is True
    assert payload["status"]["manager_operational_status"] == (
        "manager_contract_replay_recovered"
    )
    assert payload["manager_contract_replay"]["contract_replay_status"] == (
        "stored_error_resolved_by_current_contract"
    )


def test_build_binance_block_readiness_payload_marks_manager_error_stale_after_restart() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "latest_manager_run_at": "2026-07-08T00:00:00+00:00",
            "latest_manager_status": "error",
            "manager_operational_status": "manager_error_pending_next_run",
            "latest_manager_mode": "llm",
            "latest_unresolved_manager_error": {
                "run_at": "2026-07-08T00:00:00+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "validation_repair_resolution_missing_from_model",
            },
            "latest_manager_error_recovered": False,
        },
        runner={
            "started_at_epoch": datetime(
                2026,
                7,
                8,
                1,
                0,
                tzinfo=timezone.utc,
            ).timestamp()
        },
        enabled=True,
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=0.25,
        max_total_exposure_usdt=0,
        max_symbol_exposure_pct=25,
        min_reward_risk=1.3,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: f"{latest}|{interval}",
    )

    assert payload["status"]["latest_manager_error_stale_after_restart"] is True
    assert payload["status"]["latest_stale_manager_error"] == {
        "run_at": "2026-07-08T00:00:00+00:00",
        "status": "error",
        "mode": "llm",
        "error_message": "validation_repair_resolution_missing_from_model",
    }
    assert "latest_unresolved_manager_error" not in payload["status"]
    assert "latest_manager_error" not in payload["status"]


def test_build_binance_quant_signals_payload_attaches_recent_history() -> None:
    class _Repository:
        def latest_signals(self, *, symbols: list[str] | None, limit: int) -> list[dict[str, Any]]:
            assert symbols == ["BTCUSDT"]
            assert limit == 3
            return [{"symbol": "BTCUSDT", "bias": "long"}]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str,
            points_per_symbol: int,
        ) -> dict[str, Any]:
            return {
                "symbols": symbols,
                "horizon": horizon,
                "points_per_symbol": points_per_symbol,
            }

    payload = build_binance_quant_signals_payload(
        repository=_Repository(),
        symbols=["BTCUSDT"],
        limit=3,
    )

    assert payload == {
        "status": "ok",
        "items": [{"symbol": "BTCUSDT", "bias": "long"}],
        "history": {
            "symbols": ["BTCUSDT"],
            "horizon": "intraday",
            "points_per_symbol": 12,
        },
        "count": 1,
    }


def test_build_binance_pattern_context_payload_raises_503_when_lab_unavailable() -> None:
    with pytest.raises(HTTPException) as exc_info:
        build_binance_pattern_context_payload(
            repository_cls=None,
            db_path=".runtime/missing.db",
            symbols=["BTCUSDT"],
            limit=5,
            import_error=RuntimeError("not importable"),
        )

    assert exc_info.value.status_code == 503
    assert "not importable" in str(exc_info.value.detail)


def test_build_binance_pattern_context_payload_uses_repository_context() -> None:
    class _Repository:
        def __init__(self, path: str) -> None:
            self.path = path

        def pattern_context(self, *, symbols: list[str] | None, limit: int) -> dict[str, Any]:
            return {
                "status": "ok",
                "db_path": self.path,
                "symbols": symbols,
                "limit": limit,
            }

    payload = build_binance_pattern_context_payload(
        repository_cls=_Repository,
        db_path=".runtime/patterns.db",
        symbols=["ETHUSDT"],
        limit=9,
    )

    assert payload == {
        "status": "ok",
        "db_path": ".runtime/patterns.db",
        "symbols": ["ETHUSDT"],
        "limit": 9,
    }


def test_binance_block_readiness_payload_lives_outside_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    payloads_source = (
        ROOT / "src/tradecraft/api/binance_blocks_payloads.py"
    ).read_text()

    assert "def build_binance_block_readiness_payload(" in payloads_source
    assert "def _build_binance_block_trader_readiness(" not in main_source
    assert '"futures_mode": "live"' not in main_source


def test_build_binance_block_readiness_payload_strips_nested_readiness() -> None:
    payload = build_binance_block_readiness_payload(
        status_payload={
            "status": "ok",
            "latest_manager_run_at": "2026-06-21T00:00:00+00:00",
            "readiness": {"old": True},
        },
        runner={"alive": True},
        enabled=True,
        spot_live=True,
        futures_live=False,
        upbit_live=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        account_risk_pct=1.25,
        max_total_exposure_usdt=1000,
        max_symbol_exposure_pct=20,
        min_reward_risk=1.8,
        manager_interval_sec=1800,
        next_from_latest=lambda latest, interval: f"{latest}|{interval}",
    )

    assert payload["enabled"] is True
    assert payload["status"] == {
        "status": "ok",
        "latest_manager_run_at": "2026-06-21T00:00:00+00:00",
    }
    assert payload["execution"] == {
        "spot_mode": "live",
        "futures_mode": "paper",
        "upbit_spot_mode": "live",
    }
    assert payload["runner"] == {"alive": True}
    assert payload["model"] == "gpt-5.5"
    assert payload["reasoning_effort"] == "xhigh"
    assert payload["risk"] == {
        "account_risk_pct": 1.25,
        "max_total_exposure_usdt": 1000.0,
        "max_symbol_exposure_pct": 20.0,
        "min_reward_risk": 1.8,
    }
    assert payload["next_manager_run_at"] == "2026-06-21T00:00:00+00:00|1800"


def test_compact_runner_status_keeps_manager_retry_diagnostics() -> None:
    compact = _compact_runner_status(
        {
            "alive": True,
            "status": "ok",
            "manager_due_reason": "retry_after_manager_error",
            "last_manager_due_reason": "retry_after_manager_error",
            "manager_error_retry_sec": 300,
            "manager_result": {
                "status": "running",
                "started_at": "2026-07-01T16:52:17+00:00",
            },
            "last_manager_result": {
                "status": "error",
                "error_message": "codex native thread lease unavailable",
            },
            "raw_heavy": "drop me",
        }
    )

    assert compact == {
        "alive": True,
        "status": "ok",
        "manager_due_reason": "retry_after_manager_error",
        "last_manager_due_reason": "retry_after_manager_error",
        "manager_error_retry_sec": 300,
        "manager_result": {
            "status": "running",
            "started_at": "2026-07-01T16:52:17+00:00",
        },
        "last_manager_result": {
            "status": "error",
            "error_message": "codex native thread lease unavailable",
        },
    }


def test_build_binance_block_route_deps_wires_trader_and_payload_helpers() -> None:
    class _Trader:
        def __init__(self) -> None:
            self.kill_switch: dict[str, Any] = {}

        def snapshot(self) -> dict[str, Any]:
            return {"status": "ok", "blocks": [{"symbol": "BTCUSDT"}]}

        async def run_manager_once(self) -> dict[str, Any]:
            return {"status": "manager_ok"}

        async def run_spot_adoption_once(self) -> dict[str, Any]:
            return {"status": "spot_ok"}

        async def run_upbit_adoption_once(self) -> dict[str, Any]:
            return {"status": "upbit_ok"}

        async def executor_tick(self) -> dict[str, Any]:
            return {"status": "tick_ok"}

        def set_kill_switch(self, enabled: bool, reason: str) -> dict[str, Any]:
            self.kill_switch = {"enabled": enabled, "reason": reason}
            return self.kill_switch

    class _Memory:
        def validation_repair_ops_summary(
            self,
            *,
            target_scope: str,
            limit: int,
        ) -> dict[str, Any]:
            return {"target_scope": target_scope, "limit": limit}

    class _QuantRepository:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None,
            limit: int,
        ) -> list[dict[str, Any]]:
            return [{"symbol": (symbols or [""])[0], "limit": limit}]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str,
            points_per_symbol: int,
        ) -> dict[str, Any]:
            return {"symbols": symbols, "horizon": horizon, "points": points_per_symbol}

    class _PatternRepository:
        def __init__(self, path: str) -> None:
            self.path = path

        def pattern_context(
            self,
            *,
            symbols: list[str] | None,
            limit: int,
        ) -> dict[str, Any]:
            return {"path": self.path, "symbols": symbols, "limit": limit}

    trader = _Trader()
    deps = build_binance_block_route_deps(
        require_admin_auth=lambda: {"ok": True},
        trader=trader,
        memory_service=_Memory(),
        build_readiness=lambda payload: {"block_count": len(payload.get("blocks") or [])},
        quant_repository_factory=_QuantRepository,
        pattern_repository_cls=_PatternRepository,
        pattern_db_path=lambda: ".runtime/patterns.db",
        pattern_import_error=None,
    )

    assert asyncio.run(deps.blocks_snapshot(compact=False))["blocks"] == [
        {"symbol": "BTCUSDT"}
    ]
    assert deps.validation_repair_ops_summary("binance", 4) == {
        "target_scope": "binance",
        "limit": 4,
    }
    assert deps.build_readiness({"blocks": [{}, {}]}) == {"block_count": 2}
    assert deps.quant_signals(["ETHUSDT"], 2)["items"] == [
        {"symbol": "ETHUSDT", "limit": 2}
    ]
    assert deps.pattern_context(["BNBUSDT"], 3) == {
        "path": ".runtime/patterns.db",
        "symbols": ["BNBUSDT"],
        "limit": 3,
    }
    assert asyncio.run(deps.manager_run_once()) == {"status": "manager_ok"}
    assert asyncio.run(deps.spot_adoption_once()) == {"status": "spot_ok"}
    assert deps.upbit_adoption_once is not None
    assert asyncio.run(deps.upbit_adoption_once()) == {"status": "upbit_ok"}
    assert asyncio.run(deps.executor_tick()) == {"status": "tick_ok"}
    assert deps.set_kill_switch(True, "test") == {"enabled": True, "reason": "test"}
