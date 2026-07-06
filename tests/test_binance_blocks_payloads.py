from __future__ import annotations

import asyncio
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
