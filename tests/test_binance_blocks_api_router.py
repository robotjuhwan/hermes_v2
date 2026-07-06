from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.binance_blocks import (
    BinanceBlockRouteDeps,
    build_binance_blocks_router,
    compact_binance_blocks_payload,
)


def _app(deps: BinanceBlockRouteDeps) -> FastAPI:
    app = FastAPI()
    app.include_router(build_binance_blocks_router(deps))
    return app


def test_binance_blocks_router_status_adds_validation_and_readiness() -> None:
    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=lambda compact=False: {
                "status": "ok",
                "compact": compact,
                "blocks": [],
            },
            validation_repair_ops_summary=lambda target_scope, limit: {
                "scope": target_scope,
                "limit": limit,
            },
            build_readiness=lambda payload: {
                "status": payload["status"],
                "runner": {
                    "alive": True,
                    "effective_alive": True,
                    "pid": 5678,
                    "status": "running",
                    "matches": [{"command": "too-large"}],
                },
            },
            quant_signals=lambda symbols, limit: {"status": "ok", "items": symbols, "limit": limit},
            pattern_context=lambda symbols, limit: {"status": "ok", "patterns": symbols, "limit": limit},
            manager_run_once=lambda: {"status": "manager-ok"},
            spot_adoption_once=lambda: {"status": "spot-ok"},
            upbit_adoption_once=lambda: {"status": "upbit-ok"},
            executor_tick=lambda: {"status": "tick-ok"},
            set_kill_switch=lambda enabled, reason: {"enabled": enabled, "reason": reason},
        )
    )

    response = TestClient(app).get("/api/binance/blocks/status?compact=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["compact"] is True
    assert payload["validation_repair_ops"] == {"scope": "binance", "limit": 4}
    assert payload["readiness"]["status"] == "ok"
    assert payload["runner"] == {
        "alive": True,
        "effective_alive": True,
        "pid": 5678,
        "status": "running",
    }


def test_binance_blocks_router_status_uses_short_operational_history() -> None:
    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=lambda compact=False: compact_binance_blocks_payload(
                {
                    "status": "ok",
                    "active_blocks": [],
                    "block_history": [
                        {"block_id": f"h{idx}", "symbol": "BTCUSDT"}
                        for idx in range(20)
                    ],
                    "orders": [
                        {"id": idx, "block_id": f"h{idx}", "symbol": "BTCUSDT"}
                        for idx in range(20)
                    ],
                    "events": [
                        {
                            "id": idx,
                            "block_id": f"h{idx}",
                            "event_type": "tick",
                        }
                        for idx in range(20)
                    ],
                    "manager_runs": [
                        {"id": idx, "status": "ok", "run_at": f"run-{idx}"}
                        for idx in range(20)
                    ],
                }
            ),
            validation_repair_ops_summary=lambda target_scope, limit: {},
            build_readiness=lambda payload: {},
            quant_signals=lambda symbols, limit: {},
            pattern_context=lambda symbols, limit: {},
            manager_run_once=lambda: {},
            spot_adoption_once=lambda: {},
            upbit_adoption_once=None,
            executor_tick=lambda: {},
            set_kill_switch=lambda enabled, reason: {},
        )
    )

    response = TestClient(app).get("/api/binance/blocks/status?compact=true")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["block_history"]) == 4
    assert len(payload["orders"]) == 4
    assert len(payload["events"]) == 4
    assert len(payload["manager_runs"]) == 2
    assert payload["block_history_omitted_count"] == 8
    assert payload["orders_omitted_count"] == 4
    assert payload["events_omitted_count"] == 4
    assert payload["manager_runs_omitted_count"] == 18


def test_binance_blocks_status_compact_caps_repair_ops_and_history_text() -> None:
    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=lambda compact=False: compact_binance_blocks_payload(
                {
                    "status": "ok",
                    "active_blocks": [],
                    "block_history": [
                        {
                            "block_id": f"h{idx}",
                            "symbol": "BTCUSDT",
                            "thesis": "t" * 2_000,
                            "risk_note": "r" * 2_000,
                        }
                        for idx in range(20)
                    ],
                    "orders": [
                        {
                            "id": idx,
                            "block_id": f"h{idx}",
                            "symbol": "BTCUSDT",
                            "reason": "entry",
                        }
                        for idx in range(20)
                    ],
                    "events": [
                        {
                            "id": idx,
                            "block_id": f"h{idx}",
                            "event_type": "tick",
                            "message": "m" * 2_000,
                        }
                        for idx in range(20)
                    ],
                    "manager_runs": [
                        {
                            "id": idx,
                            "status": "ok",
                            "run_at": f"run-{idx}",
                            "response": {
                                "hold_decision": {
                                    "summary": "hold " * 500,
                                    "reasons": ["reason " * 500],
                                }
                            },
                        }
                        for idx in range(20)
                    ],
                }
            ),
            validation_repair_ops_summary=lambda target_scope, limit: {
                "status": "needs_repair",
                "scope": target_scope,
                "backlog_count": 8,
                "constraint_count": 8,
                "top_backlog": [
                    {
                        "discipline_id": f"discipline-{idx}",
                        "priority": "p0",
                        "entry_bias": "wait " * 200,
                        "target_stop_review": "review " * 200,
                        "required_checks": ["check " * 200 for _ in range(8)],
                    }
                    for idx in range(8)
                ],
                "top_constraints": [
                    {
                        "policy_id": f"policy-{idx}",
                        "sizing_policy": "size " * 200,
                        "required_checks": ["constraint " * 200 for _ in range(8)],
                    }
                    for idx in range(8)
                ],
                "recovery": {
                    "items": [
                        {
                            "discipline_id": f"discipline-{idx}",
                            "current_jue_response": ["response " * 200],
                        }
                        for idx in range(8)
                    ]
                },
            },
            build_readiness=lambda payload: {},
            quant_signals=lambda symbols, limit: {},
            pattern_context=lambda symbols, limit: {},
            manager_run_once=lambda: {},
            spot_adoption_once=lambda: {},
            upbit_adoption_once=None,
            executor_tick=lambda: {},
            set_kill_switch=lambda enabled, reason: {},
        )
    )

    response = TestClient(app).get("/api/binance/blocks/status?compact=true")

    payload = response.json()
    assert response.status_code == 200
    assert len(payload["block_history"]) == 4
    assert len(payload["orders"]) == 4
    assert len(payload["events"]) == 4
    assert len(payload["manager_runs"]) == 2
    assert len(payload["validation_repair_ops"]["top_backlog"]) == 2
    assert len(payload["validation_repair_ops"]["top_constraints"]) == 2
    assert len(payload["validation_repair_ops"]["recovery"]["items"]) == 2
    assert "wait " * 80 not in response.text
    assert "review " * 80 not in response.text
    assert len(response.text) < 16_000


def test_binance_blocks_router_list_honors_compact_query() -> None:
    calls: list[bool] = []

    def blocks_snapshot(compact: bool = False) -> dict[str, Any]:
        calls.append(bool(compact))
        if not compact:
            return {
                "status": "ok",
                "blocks": [{"metadata": {"huge": "x" * 50_000}}],
            }
        return {
            "status": "ok",
            "compact": True,
            "active_blocks": [{"block_id": "b1", "symbol": "BTCUSDT"}],
        }

    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=blocks_snapshot,
            validation_repair_ops_summary=lambda target_scope, limit: {},
            build_readiness=lambda payload: {},
            quant_signals=lambda symbols, limit: {},
            pattern_context=lambda symbols, limit: {},
            manager_run_once=lambda: {},
            spot_adoption_once=lambda: {},
            upbit_adoption_once=None,
            executor_tick=lambda: {},
            set_kill_switch=lambda enabled, reason: {},
        )
    )

    response = TestClient(app).get("/api/binance/blocks?compact=true")

    assert response.status_code == 200
    assert calls == [True]
    assert response.json() == {
        "status": "ok",
        "compact": True,
        "active_blocks": [{"block_id": "b1", "symbol": "BTCUSDT"}],
    }


def test_binance_blocks_router_active_only_returns_light_snapshot() -> None:
    calls: list[bool] = []

    def blocks_snapshot(compact: bool = False) -> dict[str, Any]:
        calls.append(bool(compact))
        return compact_binance_blocks_payload(
            {
                "status": "ok",
                "enabled": True,
                "execution_mode": "live",
                "execute_spot_orders": True,
                "execute_futures_orders": True,
                "active_blocks": [{"block_id": "b1", "symbol": "BTCUSDT"}],
                "block_history": [
                    {"block_id": f"h{idx}", "symbol": "ETHUSDT"}
                    for idx in range(12)
                ],
                "orders": [{"id": idx, "symbol": "ETHUSDT"} for idx in range(8)],
                "events": [{"id": idx, "message": "m"} for idx in range(8)],
                "manager_runs": [{"id": idx, "status": "ok"} for idx in range(4)],
                "live_authority": {"status": "ok", "raw": "x" * 20_000},
                "account": {"status": "ok", "total_equity_usdt": 123.4},
            }
        )

    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=blocks_snapshot,
            validation_repair_ops_summary=lambda target_scope, limit: {},
            build_readiness=lambda payload: {},
            quant_signals=lambda symbols, limit: {},
            pattern_context=lambda symbols, limit: {},
            manager_run_once=lambda: {},
            spot_adoption_once=lambda: {},
            upbit_adoption_once=None,
            executor_tick=lambda: {},
            set_kill_switch=lambda enabled, reason: {},
        )
    )

    response = TestClient(app).get(
        "/api/binance/blocks?compact=true&active_only=true"
    )

    payload = response.json()
    assert response.status_code == 200
    assert calls == [True]
    assert payload["compact"] is True
    assert payload["active_only"] is True
    assert payload["active_blocks"] == [{"block_id": "b1", "symbol": "BTCUSDT"}]
    assert payload["account"] == {"status": "ok", "total_equity_usdt": 123.4}
    assert "block_history" not in payload
    assert "orders" not in payload
    assert "events" not in payload
    assert "manager_runs" not in payload
    assert "live_authority" not in payload
    assert "raw" not in response.text
    assert len(response.text) < 5_000


def test_compact_binance_blocks_payload_removes_raw_manager_prompt_response() -> None:
    payload = compact_binance_blocks_payload(
        {
            "status": "ok",
            "blocks": [{"block_id": "open-1"}],
            "active_blocks": [
                {
                    "block_id": "b1",
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "status": "open",
                    "horizon": "short",
                    "qty_open": 0.01,
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                    "current_price": 101.0,
                    "thesis": "x" * 500,
                    "risk_note": "risk",
                    "metadata": {"huge": "y" * 10_000, "lane": "futures:long"},
                    "quote": {"price": 101.0, "source": "ticker", "raw": "z" * 5000},
                }
            ],
            "block_history": [
                {
                    "block_id": "h1",
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "status": "closed",
                    "horizon": "mid",
                    "qty_open": 0,
                    "entry_price": 200.0,
                    "target_price": 220.0,
                    "stop_price": 190.0,
                    "realized_pnl_usdt": 3.2,
                    "r_multiple": 0.7,
                    "closed_at": "2026-06-28T00:00:00+00:00",
                    "metadata": {"huge": "h" * 10_000},
                }
            ],
            "manager_runs": [
            {
                "id": 3,
                "run_id": 3,
                "run_at": "2026-06-20T00:00:00+00:00",
                "started_at": "2026-06-20T00:00:00+00:00",
                "status": "ok",
                "mode": "llm",
                "model": "gpt-5.5",
                    "diagnostics": {
                        "version": "binance_manager_diagnostics_v1",
                        "jue_wiki_action_reference_memory_status": "active",
                        "jue_wiki_action_reference_memory_resolution_status": "unresolved",
                        "jue_wiki_action_reference_status": "missing",
                        "jue_wiki_action_reference_count": 0,
                        "jue_wiki_action_reference_ratio": 0.0,
                        "blocker_tags": {
                            "unresolved_jue_wiki_action_reference_memory": 2,
                        },
                        "raw_notes": "hidden diagnostics " * 500,
                    },
                    "prompt": {"huge": "raw prompt"},
                    "response": {
                        "raw": "raw response",
                        "hold_decision": {"summary": "관망"},
                    },
                    "actions": {
                        "create_blocks": [{"symbol": "BTCUSDT"}],
                    },
                }
            ],
            "live_authority": {
                "status": "ok",
                "live_grade": "probe",
                "scorecard_count": 20,
                "raw_payload": "x" * 50_000,
                "lane_authority": {
                    "version": "lane_authority_v1",
                    "weak_lanes": ["spot", "futures"],
                    "shadow_blocked_lanes": ["volatile_attack"],
                    "lane_actions": {
                        f"lane-{idx}": {
                            "grade": "weak_review",
                            "action": "waiting entry only " + ("w" * 1_000),
                            "sample_count": idx,
                            "max_budget_multiplier": 0.25,
                            "scale_blockers": ["blocker " + ("b" * 1_000)] * 5,
                            "scale_repair_targets": ["repair " + ("r" * 1_000)] * 5,
                            "raw": "w" * 10_000,
                        }
                        for idx in range(40)
                    },
                    "block_design_requirements": {"huge": "q" * 80_000},
                },
                "validation_gate": {
                    "status": "reduced",
                    "cost_attribution": {
                        "status": "cost_drag",
                        "worst_cost_groups": [
                            {
                                "group_type": "lane",
                                "group": f"group-{idx}",
                                "sample_count": idx,
                                "total_cost": 12.3,
                                "cost_drag_pct_of_abs_gross_pnl": 45.6,
                                "net_negative_after_cost": True,
                                "symbols": [f"SYM{n}USDT" for n in range(20)],
                                "block_ids": [f"block-{n}" for n in range(20)],
                            }
                            for idx in range(8)
                        ],
                        "worst_cost_rows": [
                            {
                                "block_id": f"block-{idx}",
                                "symbol": "BTCUSDT",
                                "horizon": "short",
                                "strategy_family": "volatile_attack",
                                "gross_pnl": 1.2,
                                "net_pnl": -0.2,
                                "cost_total": 1.4,
                                "cost_drag_pct_of_abs_gross_pnl": 60.0,
                                "net_negative_after_cost": True,
                            }
                            for idx in range(8)
                        ],
                    },
                    "operator_guidance": ["z" * 10_000] * 20,
                    "failed_disciplines": [
                        {"name": f"discipline-{idx}", "evidence": "y" * 10_000}
                        for idx in range(20)
                    ],
                },
                "repair_execution": {
                    "version": "validation_repair_execution_v1",
                    "status": "queued",
                    "actions": [
                        {
                            "discipline_id": f"discipline-{idx}",
                            "priority": "p0",
                            "status": "queued",
                            "validation_mode": "backtest_wfa_oos_rebuild",
                            "scale_up_blocked": True,
                            "live_shadow_required": True,
                            "artifact": "crypto_pattern_lab_runner",
                            "reason": "repair reason " + ("x" * 1_000),
                            "evidence_reasons": ["evidence " + ("e" * 1_000)] * 5,
                            "profit_factor": 0.7,
                        }
                        for idx in range(10)
                    ],
                },
            },
        }
    )

    assert payload["compact"] is True
    assert "blocks" not in payload
    assert payload["active_blocks"] == [
        {
            "block_id": "b1",
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "long",
            "status": "open",
            "horizon": "short",
            "lane": "futures:long",
            "qty_open": 0.01,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "current_price": 101.0,
            "thesis": "x" * 220,
            "risk_note": "risk",
            "quote": {"price": 101.0, "source": "ticker"},
        }
    ]
    assert payload["block_history"] == [
        {
            "block_id": "h1",
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "status": "closed",
            "horizon": "mid",
            "qty_open": 0,
            "entry_price": 200.0,
            "target_price": 220.0,
            "stop_price": 190.0,
            "closed_at": "2026-06-28T00:00:00+00:00",
            "realized_pnl_usdt": 3.2,
            "r_multiple": 0.7,
        }
    ]
    assert payload["manager_runs"] == [
        {
            "id": 3,
            "run_id": 3,
            "run_at": "2026-06-20T00:00:00+00:00",
            "started_at": "2026-06-20T00:00:00+00:00",
            "status": "ok",
            "mode": "llm",
            "model": "gpt-5.5",
            "error_message": None,
            "action_count": 1,
            "actions": {"create_blocks": [{"symbol": "BTCUSDT"}]},
            "hold_decision": {"summary": "관망"},
            "diagnostics": {
                "version": "binance_manager_diagnostics_v1",
                "jue_wiki_action_reference_memory_status": "active",
                "jue_wiki_action_reference_memory_resolution_status": "unresolved",
                "jue_wiki_action_reference_status": "missing",
                "jue_wiki_action_reference_count": 0,
                "jue_wiki_action_reference_ratio": 0.0,
                "blocker_tags": {
                    "unresolved_jue_wiki_action_reference_memory": 2,
                },
            },
        }
    ]
    assert payload["live_authority"]["status"] == "ok"
    assert payload["live_authority"]["live_grade"] == "probe"
    assert payload["live_authority"]["lane_authority"]["lane_action_count"] == 40
    assert len(payload["live_authority"]["lane_authority"]["lane_actions"]) == 4
    assert len(payload["live_authority"]["repair_execution"]["actions"]) == 3
    assert len(payload["live_authority"]["validation_gate"]["cost_attribution"]["groups"]) == 2
    assert len(payload["live_authority"]["validation_gate"]["cost_attribution"]["rows"]) == 2
    assert "raw_payload" not in payload["live_authority"]
    assert "block_design_requirements" not in payload["live_authority"]["lane_authority"]
    assert "instruction" not in payload["live_authority"]["validation_gate"]["cost_attribution"]
    assert len(json.dumps(payload["live_authority"])) < 5_000


def test_compact_binance_blocks_payload_exposes_runtime_flags_at_top_level() -> None:
    payload = compact_binance_blocks_payload(
        {
            "status": "ok",
            "enabled": True,
            "execution_mode": "live",
            "execute_spot_orders": True,
            "execute_futures_orders": False,
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": False,
                "upbit_orders_enabled": True,
            },
            "config": {
                "quote_interval_sec": 15,
                "rule_interval_sec": 15,
                "manager_interval_sec": 1800,
                "llm_timeout_ms": 600000,
            },
            "blocks": [],
            "manager_runs": [],
        }
    )

    assert payload["execute_orders"] is True
    assert payload["manager_interval_sec"] == 1800
    assert payload["rule_interval_sec"] == 15
    assert payload["quote_interval_sec"] == 15
    assert payload["llm_timeout_ms"] == 600000


def test_compact_binance_blocks_payload_limits_operational_history_bloat() -> None:
    payload = compact_binance_blocks_payload(
        {
            "status": "ok",
            "blocks": [],
            "active_blocks": [],
            "block_history": [
                {
                    "block_id": f"h{i}",
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "status": "closed",
                    "thesis": "t" * 1_000,
                    "risk_note": "r" * 1_000,
                }
                for i in range(30)
            ],
            "orders": [
                {
                    "id": i,
                    "block_id": f"h{i}",
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "buy",
                    "qty": 0.1,
                    "order_type": "LIMIT_IOC",
                    "status": "sent",
                    "reason": "entry",
                    "response": {
                        "order_id": f"order-{i}",
                        "status": "NEW",
                        "price": 100,
                        "executed_qty": 0,
                        "remaining_qty": 0.1,
                        "raw": "o" * 20_000,
                    },
                    "created_at": "2026-06-28T00:00:00+00:00",
                    "updated_at": "2026-06-28T00:00:00+00:00",
                }
                for i in range(20)
            ],
            "events": [
                {
                    "id": i,
                    "block_id": f"h{i}",
                    "event_type": "error",
                    "message": "m" * 1_000,
                    "payload": {
                        "error_message": "boom",
                        "run_id": i,
                        "venue": "Binance",
                        "raw": "e" * 20_000,
                    },
                    "created_at": "2026-06-28T00:00:00+00:00",
                }
                for i in range(20)
            ],
            "performance": {
                "sample_count": 19,
                "win_rate_pct": 31.5,
                "avg_r_multiple": -0.27,
                "realized_pnl_usdt": -1.13,
                "symbol_scorecards": [{"raw": "s" * 20_000}],
            },
            "growth_unlock": {
                "phase": "rebuilding",
                "can_leave_edge_rebuild": False,
                "criteria": [{"id": "win_rate", "passed": False, "raw": "c" * 20_000}],
                "next_missions": [{"raw": "m" * 20_000}],
            },
        }
    )

    assert len(payload["block_history"]) == 12
    assert len(payload["orders"]) == 8
    assert len(payload["events"]) == 8
    assert payload["orders"][0]["response"] == {
        "order_id": "order-0",
        "status": "NEW",
        "price": 100,
        "executed_qty": 0,
        "remaining_qty": 0.1,
    }
    assert payload["events"][0]["payload"] == {
        "error_message": "boom",
        "run_id": 0,
        "venue": "Binance",
    }
    assert payload["performance"] == {
        "sample_count": 19,
        "win_rate_pct": 31.5,
        "avg_r_multiple": -0.27,
        "realized_pnl_usdt": -1.13,
    }
    assert payload["growth_unlock"] == {
        "phase": "rebuilding",
        "can_leave_edge_rebuild": False,
        "criteria": [{"id": "win_rate", "passed": False}],
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "symbol_scorecards" not in serialized
    assert "next_missions" not in serialized
    assert "raw" not in serialized
    assert len(serialized) < 16_000


def test_binance_blocks_router_awaits_manager_actions() -> None:
    calls: list[str] = []

    async def manager() -> dict[str, Any]:
        calls.append("manager")
        return {"status": "manager-ok"}

    async def tick() -> dict[str, Any]:
        calls.append("tick")
        return {"status": "tick-ok"}

    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=lambda compact=False: {},
            validation_repair_ops_summary=lambda target_scope, limit: {},
            build_readiness=lambda payload: {},
            quant_signals=lambda symbols, limit: {},
            pattern_context=lambda symbols, limit: {},
            manager_run_once=manager,
            spot_adoption_once=lambda: {"status": "spot-ok"},
            upbit_adoption_once=None,
            executor_tick=tick,
            set_kill_switch=lambda enabled, reason: {},
        )
    )
    client = TestClient(app)

    manager_response = client.post("/api/binance/blocks/manager/run-once")
    tick_response = client.post("/api/binance/blocks/executor/tick")

    assert manager_response.json() == {"status": "manager-ok"}
    assert tick_response.json() == {"status": "tick-ok"}
    assert calls == ["manager", "tick"]


def test_binance_blocks_router_parses_quant_symbols_and_limit() -> None:
    seen: dict[str, Any] = {}

    def quant(symbols: list[str], limit: int) -> dict[str, Any]:
        seen["symbols"] = symbols
        seen["limit"] = limit
        return {"status": "ok", "items": symbols}

    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=lambda compact=False: {},
            validation_repair_ops_summary=lambda target_scope, limit: {},
            build_readiness=lambda payload: {},
            quant_signals=quant,
            pattern_context=lambda symbols, limit: {},
            manager_run_once=lambda: {},
            spot_adoption_once=lambda: {},
            upbit_adoption_once=None,
            executor_tick=lambda: {},
            set_kill_switch=lambda enabled, reason: {},
        )
    )

    response = TestClient(app).get(
        "/api/binance/quant/signals?symbols=btcUSDT, ethusdt&limit=999"
    )

    assert response.status_code == 200
    assert seen == {"symbols": ["BTCUSDT", "ETHUSDT"], "limit": 100}
    assert response.json()["items"] == ["BTCUSDT", "ETHUSDT"]


def test_binance_blocks_router_adoption_combines_spot_and_upbit() -> None:
    app = _app(
        BinanceBlockRouteDeps(
            require_admin_auth=lambda: None,
            blocks_snapshot=lambda compact=False: {},
            validation_repair_ops_summary=lambda target_scope, limit: {},
            build_readiness=lambda payload: {},
            quant_signals=lambda symbols, limit: {},
            pattern_context=lambda symbols, limit: {},
            manager_run_once=lambda: {},
            spot_adoption_once=lambda: {"status": "spot-ok"},
            upbit_adoption_once=lambda: {"status": "upbit-ok"},
            executor_tick=lambda: {},
            set_kill_switch=lambda enabled, reason: {},
        )
    )

    response = TestClient(app).post("/api/binance/blocks/adopt-existing/run-once")

    assert response.status_code == 200
    assert response.json() == {
        "binance_spot": {"status": "spot-ok"},
        "upbit_spot": {"status": "upbit-ok"},
    }
