from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.kis_blocks import KISBlockRouteDeps, build_kis_blocks_router


def _base_deps(**overrides: Any) -> KISBlockRouteDeps:
    values: dict[str, Any] = {
        "require_admin_auth": lambda: None,
        "primary_ready": lambda: True,
        "status": lambda: {"status": "ok"},
        "snapshot": lambda: {"status": "ok", "blocks": []},
        "attach_block_memory": lambda payload: {**payload, "memory_attached": True},
        "validation_repair_ops_summary": lambda target_scope, limit: {
            "scope": target_scope,
            "limit": limit,
        },
        "ops_readiness": lambda: {
            "kis_block_trader": {
                "next_manager_run_at": "next-manager",
                "runner": {
                    "alive": True,
                    "effective_alive": True,
                    "pid": 1234,
                    "status": "running",
                    "raw_matches": ["too-large"],
                },
            },
            "market_judge": {"schedule": {"next_llm_due_at": "next-judge"}},
            "stale_processes": ["x"],
        },
        "manager_run_once": lambda: {"status": "manager-ok"},
        "adoption_run_once": lambda: {"status": "adopt-ok"},
        "executor_tick": lambda manual=False: {"status": "tick-ok", "manual": manual},
        "set_kill_switch": lambda enabled, reason: {"enabled": enabled, "reason": reason},
        "cancel_order": lambda order_id, reason: {
            "status": "ok",
            "order_id": order_id,
            "reason": reason,
        },
        "block_detail": lambda block_id: {"status": "ok", "block_id": block_id},
        "block_memory": lambda block_id: {"block_id": block_id, "lessons": []},
        "add_user_directive": lambda block_id, message, preferred_horizon, scope, source: {
            "status": "ok",
            "block_id": block_id,
            "message": message,
            "preferred_horizon": preferred_horizon,
            "scope": scope,
            "source": source,
        },
        "pause_block": lambda block_id, reason: {
            "status": "ok",
            "block_id": block_id,
            "reason": reason,
        },
        "resume_block": lambda block_id, reason: {
            "status": "ok",
            "block_id": block_id,
            "reason": reason,
        },
        "close_block": lambda block_id, reason: {
            "status": "ok",
            "block_id": block_id,
            "reason": reason,
        },
    }
    values.update(overrides)
    return KISBlockRouteDeps(**values)


def _app(deps: KISBlockRouteDeps) -> FastAPI:
    app = FastAPI()
    app.include_router(build_kis_blocks_router(deps))
    return app


def test_kis_blocks_router_status_adds_readiness_and_validation() -> None:
    response = TestClient(
        _app(
            _base_deps(
                status=lambda: {
                    "status": "ok",
                    "model": "gpt-5.5",
                    "reasoning_effort": "xhigh",
                    "config": {
                        "manager_interval_sec": 1800,
                        "rule_interval_sec": 10,
                    },
                    "latest_decision_input": {
                        "status": "ok",
                        "aggressive_candidate_count": 7,
                        "no_action_watch": {"status": "watch", "streak": 2},
                    },
                },
            )
        )
    ).get("/api/kis/blocks/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["validation_repair_ops"] == {"scope": "kis", "limit": 4}
    assert payload["model"] == "gpt-5.5"
    assert payload["reasoning_effort"] == "xhigh"
    assert payload["manager_interval_sec"] == 1800
    assert payload["rule_interval_sec"] == 10
    assert payload["latest_decision_input"]["aggressive_candidate_count"] == 7
    assert payload["latest_decision_input"]["no_action_watch"]["streak"] == 2
    assert payload["next_manager_run_at"] == "next-manager"
    assert payload["next_market_judge"] == "next-judge"
    assert payload["stale_processes"] == ["x"]
    assert payload["runner"] == {
        "alive": True,
        "effective_alive": True,
        "pid": 1234,
        "status": "running",
    }
    assert payload["readiness"]["kis_block_trader"]["next_manager_run_at"] == "next-manager"


def test_kis_blocks_router_status_caps_validation_repair_ops() -> None:
    def validation_repair_ops_summary(target_scope: str, limit: int) -> dict[str, Any]:
        return {
            "version": "2026-06-30T00:00:00+00:00",
            "scope": target_scope,
            "status": "needs_repair",
            "backlog_count": 3,
            "constraint_count": 3,
            "top_backlog": [
                {
                    "policy_id": f"policy-{idx}",
                    "discipline_id": f"discipline-{idx}",
                    "priority": idx,
                    "status": "pending",
                    "automation_hook": "raw-hook" * 100,
                    "execution_weight": 999,
                    "entry_bias": "entry-bias-" + ("x" * 500),
                    "sizing_policy": "size-" + ("y" * 500),
                    "target_stop_review": "target-" + ("z" * 500),
                    "required_checks": ["check-a" * 100, "check-b" * 100, "check-c"],
                }
                for idx in range(3)
            ],
            "top_constraints": [
                {
                    "policy_id": f"constraint-{idx}",
                    "discipline_id": f"constraint-discipline-{idx}",
                    "risk_budget_multiplier": 0.25,
                    "required_checks": ["constraint-check-a" * 100, "constraint-check-b"],
                }
                for idx in range(3)
            ],
            "recovery": {
                "status": "needs_recovery",
                "item_count": 3,
                "items": [
                    {
                        "policy_id": f"recovery-{idx}",
                        "discipline_id": f"recovery-discipline-{idx}",
                        "status": "pending",
                        "current_jue_response": ["response-" + ("r" * 500)],
                    }
                    for idx in range(3)
                ],
            },
        }

    response = TestClient(
        _app(
            _base_deps(
                validation_repair_ops_summary=validation_repair_ops_summary,
            )
        )
    ).get("/api/kis/blocks/status")

    assert response.status_code == 200
    ops = response.json()["validation_repair_ops"]
    assert ops["status"] == "needs_repair"
    assert ops["backlog_count"] == 3
    assert len(ops["top_backlog"]) == 2
    assert len(ops["top_constraints"]) == 2
    assert len(ops["recovery"]["items"]) == 2
    assert len(ops["top_backlog"][0]["required_checks"]) == 2
    assert "automation_hook" not in ops["top_backlog"][0]
    assert "execution_weight" not in ops["top_backlog"][0]
    serialized = json.dumps(ops, ensure_ascii=False)
    assert "raw-hook" not in serialized
    assert len(serialized) < 2_000


def test_kis_blocks_router_status_uses_light_readiness_without_full_ops() -> None:
    def full_ops_readiness() -> dict[str, Any]:
        raise AssertionError("status endpoint must not build full ops readiness")

    response = TestClient(
        _app(
            _base_deps(
                ops_readiness=full_ops_readiness,
                status_readiness=lambda: {
                    "status": "yellow",
                    "warnings": ["restart_required"],
                    "stale_processes": ["kis_block_trader"],
                    "kis_block_trader": {"next_manager_run_at": "next-manager"},
                    "market_judge": {"schedule": {"next_llm_due_at": "next-judge"}},
                },
            )
        )
    ).get("/api/kis/blocks/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_manager_run_at"] == "next-manager"
    assert payload["next_market_judge"] == "next-judge"
    assert payload["stale_processes"] == ["kis_block_trader"]
    assert payload["readiness"]["warnings"] == ["restart_required"]


def test_kis_blocks_router_status_compacts_large_runtime_payloads() -> None:
    large_status = {
        "status": "ok",
        "runner": {"status": "running", "raw_log": "x" * 50_000},
        "account": {
            "total_asset_krw": 4_400_000,
            "raw_balance_response": "y" * 50_000,
        },
        "open_blocks": [
            {
                "block_id": "kb1",
                "symbol": "005930",
                "name": "삼성전자",
                "status": "open",
                "horizon": "mid",
                "thesis": "z" * 1_000,
                "metadata": {"huge": "m" * 50_000},
            }
        ],
        "manager_runs": [
            {
                "id": 1,
                "status": "ok",
                "prompt": {"huge": "p" * 50_000},
                "response": {"raw": "r" * 50_000},
                "diagnostics": {
                    "version": "kis_manager_diagnostics_v1",
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
                "actions": {"create_blocks": [{"symbol": "005930"}]},
            }
        ],
    }
    large_readiness = {
        "status": "green",
        "blockers": [],
        "warnings": [],
        "advisories": ["trading_validation_probe"],
        "processes": {
            "kis_block_trader": {
                "status": "running",
                "pid": 123,
                "matches": [{"command": "x" * 50_000}],
            }
        },
        "kis_block_trader": {
            "status": {"raw": "s" * 50_000},
            "next_manager_run_at": "next-manager",
        },
        "market_judge": {"schedule": {"next_llm_due_at": "next-judge"}},
        "raw_payload": "q" * 50_000,
    }
    response = TestClient(
        _app(
            _base_deps(
                status=lambda: large_status,
                ops_readiness=lambda: large_readiness,
            )
        )
    ).get("/api/kis/blocks/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["open_blocks"][0]["name"] == "삼성전자"
    assert payload["readiness"]["status"] == "green"
    assert payload["readiness"]["blockers"] == []
    assert payload["readiness"]["warnings"] == []
    assert payload["readiness"]["advisories"] == ["trading_validation_probe"]
    assert payload["readiness"]["kis_block_trader"] == {
        "next_manager_run_at": "next-manager"
    }
    assert "raw_balance_response" not in payload["account"]
    assert "prompt" not in payload["manager_runs"][0]
    assert payload["manager_runs"][0]["diagnostics"] == {
        "version": "kis_manager_diagnostics_v1",
        "jue_wiki_action_reference_memory_status": "active",
        "jue_wiki_action_reference_memory_resolution_status": "unresolved",
        "jue_wiki_action_reference_status": "missing",
        "jue_wiki_action_reference_count": 0,
        "jue_wiki_action_reference_ratio": 0.0,
        "blocker_tags": {
            "unresolved_jue_wiki_action_reference_memory": 2,
        },
    }
    assert "raw_payload" not in payload["readiness"]
    assert "processes" not in payload["readiness"]
    assert len(json.dumps(payload, ensure_ascii=False)) < 12_000


def test_kis_blocks_router_status_unwraps_snapshot_summary_payload() -> None:
    snapshot_status = {
        "status": "ok",
        "summary": {
            "status": "ok",
            "block_count": 12,
            "open_block_count": 3,
            "latest_manager_status": "ok",
            "execution_mode": "live",
            "clock": {"session": "regular", "is_market_open": True},
            "kis_ready": True,
            "llm_ready": True,
            "model": "gpt-5.5",
            "kill_switch": {"enabled": False},
        },
        "account": {
            "status": "ok",
            "account_label": "국장1",
            "cash_krw": 4_010_886,
            "total_value_krw": 4_423_801,
            "position_count": 4,
            "raw_balance_response": "ignored",
        },
        "blocks": [
            {
                "block_id": "kb1",
                "symbol": "005930",
                "name": "삼성전자",
                "status": "open",
                "horizon": "mid",
                "metadata": {"huge": "ignored"},
            }
        ],
    }

    response = TestClient(
        _app(_base_deps(status=lambda: snapshot_status))
    ).get("/api/kis/blocks/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["block_count"] == 12
    assert payload["open_block_count"] == 3
    assert payload["execution_mode"] == "live"
    assert payload["clock"] == {"session": "regular", "is_market_open": True}
    assert payload["account"] == {
        "status": "ok",
        "account_label": "국장1",
        "cash_krw": 4_010_886,
        "total_value_krw": 4_423_801,
        "position_count": 4,
    }
    assert payload["blocks"] == [
        {
            "block_id": "kb1",
            "symbol": "005930",
            "name": "삼성전자",
            "status": "open",
            "horizon": "mid",
        }
    ]


def test_kis_blocks_router_compact_list_strips_large_block_metadata() -> None:
    snapshot = {
        "status": "ok",
        "summary": {
            "status": "ok",
            "block_count": 2,
            "open_block_count": 1,
            "kill_switch": {"enabled": False},
        },
        "account": {"cash_krw": 4_000_000, "raw": "ignored"},
        "blocks": [
            {
                "block_id": "kb1",
                "symbol": "005930",
                "name": "삼성전자",
                "status": "open",
                "horizon": "mid",
                "thesis": "a" * 1_000,
                "quote": {
                    "symbol": "005930",
                    "price": 70000,
                    "change_pct": 1.2,
                    "source": "kis",
                    "fetched_at": "2026-06-29T01:23:45+00:00",
                    "raw": {"output": "raw_quote_huge" * 10_000},
                },
                "metadata": {
                    "decision_class": "hold_update",
                    "entry_trigger_price": 70000,
                    "huge_raw": "x" * 100_000,
                    "cost_feasibility": {
                        "status": "pass",
                        "net_target_profit_after_cost_krw": 1500,
                        "target_cost_multiple": 2.5,
                        "design_note": "cost ok",
                        "gross_target_profit_krw": 1800,
                        "target_round_trip_cost_krw": 300,
                        "net_stop_loss_after_cost_krw": -900,
                        "raw_breakdown": "cost_raw" * 10_000,
                    },
                    "live_authority": {
                        "discipline_matrix": {
                            "summary": {"pass_count": 6, "warn_count": 13},
                            "statuses": [
                                {"id": str(index), "raw": "y" * 1000}
                                for index in range(19)
                            ],
                        },
                        "validation_passport": {
                            "status": "probe",
                            "score": 65.7,
                            "raw": "z" * 100_000,
                        },
                    },
                },
                "policy_impacts": [
                    {"rule_id": "r1", "reason": "keep"},
                    {"rule_id": "r2", "raw": "ignored"},
                    {"rule_id": "r3"},
                    {"rule_id": "r4"},
                ],
            },
            {
                "block_id": "kb2",
                "symbol": "000660",
                "name": "SK하이닉스",
                "status": "closed",
                "horizon": "mid",
                "thesis": "closed block",
                "risk_note": "r" * 1_000,
                "current_price": 171000,
                "entry_price": 170000,
                "target_price": 180000,
                "stop_price": 165000,
                "closed_at": "2026-06-28T06:00:00+00:00",
                "metadata": {
                    "allocation_reason": "a" * 1_000,
                    "decision_class": "closed_review",
                    "stop_policy": "manual_review",
                    "entry_trigger_price": 169500,
                    "cost_feasibility": {
                        "status": "pass",
                        "raw_breakdown": "history_cost_raw" * 10_000,
                    },
                    "live_authority": {
                        "validation_passport": {
                            "status": "probe",
                            "raw": "history_passport_raw" * 10_000,
                        },
                    },
                    "policy_effect_audit": {
                        "rules": [{"rule_id": "p1", "raw": "history_policy_raw" * 10_000}],
                    },
                    "huge_raw": "h" * 100_000,
                },
                "quote": {
                    "price": 171000,
                    "source": "kis",
                    "raw": {"output": "history_quote_raw" * 10_000},
                },
                "performance": {
                    "mfe_pct": 3.2,
                    "mae_pct": -0.7,
                    "current_pnl_pct": 0.6,
                    "raw_path": "history_performance_raw" * 10_000,
                },
                "policy_impacts": [{"rule_id": "history_rule", "raw": "ignored"}],
            }
        ],
        "orders": [{"id": 1, "block_id": "kb1", "raw": "ignored"}],
        "events": [
            {
                "id": 2,
                "block_id": "kb1",
                "event_type": "opened",
                "message": "m" * 1_000,
                "raw": "ignored",
            }
        ],
        "latest_manager_run": {
            "id": 7,
            "status": "ok",
            "prompt": {"raw": "p" * 100_000},
            "hold_decision": {"summary": "관망"},
            "actions": {"create_blocks": [{"symbol": "005930"}]},
        },
    }

    response = TestClient(
        _app(_base_deps(snapshot=lambda: snapshot))
    ).get("/api/kis/blocks?compact=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["compact"] is True
    assert payload["summary"]["block_count"] == 2
    assert "blocks" not in payload
    assert payload["active_blocks"][0]["thesis"] == "a" * 220
    assert payload["active_blocks"][0]["metadata"]["decision_class"] == "hold_update"
    assert payload["active_blocks"][0]["metadata"]["entry_trigger_price"] == 70000
    assert payload["active_blocks"][0]["metadata"]["live_authority"]["discipline_matrix"][
        "summary"
    ] == {"pass_count": 6, "warn_count": 13}
    assert payload["active_blocks"][0]["quote"] == {
        "symbol": "005930",
        "price": 70000,
        "change_pct": 1.2,
        "source": "kis",
        "fetched_at": "2026-06-29T01:23:45+00:00",
    }
    assert payload["active_blocks"][0]["metadata"]["cost_feasibility"] == {
        "status": "pass",
        "net_target_profit_after_cost_krw": 1500,
        "target_cost_multiple": 2.5,
        "design_note": "cost ok",
        "gross_target_profit_krw": 1800,
        "target_round_trip_cost_krw": 300,
        "net_stop_loss_after_cost_krw": -900,
    }
    assert payload["active_blocks"][0]["policy_impacts"] == [
        {"rule_id": "r1", "reason": "keep"},
        {"rule_id": "r2"},
        {"rule_id": "r3"},
    ]
    assert payload["block_history"] == [
        {
            "block_id": "kb2",
            "symbol": "000660",
            "name": "SK하이닉스",
            "status": "closed",
            "horizon": "mid",
            "entry_price": 170000,
            "target_price": 180000,
            "stop_price": 165000,
            "current_price": 171000,
            "closed_at": "2026-06-28T06:00:00+00:00",
            "thesis": "closed block",
            "risk_note": "r" * 140,
            "metadata": {
                "allocation_reason": "a" * 140,
                "decision_class": "closed_review",
                "entry_trigger_price": 169500,
                "stop_policy": "manual_review",
            },
        }
    ]
    assert payload["events"][0]["message"] == "m" * 240
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "huge_raw" not in serialized
    assert "raw_quote_huge" not in serialized
    assert "cost_raw" not in serialized
    assert "history_quote_raw" not in serialized
    assert "history_cost_raw" not in serialized
    assert "history_passport_raw" not in serialized
    assert "history_policy_raw" not in serialized
    assert "history_performance_raw" not in serialized
    assert "policy_impacts" not in payload["block_history"][0]
    assert "quote" not in payload["block_history"][0]
    assert "performance" not in payload["block_history"][0]
    assert "statuses" not in serialized
    assert "prompt" not in serialized
    assert len(serialized) < 15_000


def test_kis_blocks_router_compact_list_exposes_summary_status_at_top_level() -> None:
    snapshot = {
        "status": "ok",
        "summary": {
            "status": "ok",
            "block_count": 12,
            "open_block_count": 5,
            "waiting_entry_block_count": 1,
            "pending_order_count": 0,
            "latest_manager_run_at": "2026-06-29T03:20:36+00:00",
            "latest_manager_status": "ok",
            "execution_mode": "live",
            "execute_orders": True,
            "kis_ready": True,
            "llm_ready": True,
            "model": "gpt-5.5",
            "kill_switch": {"enabled": False},
        },
        "account": {"cash_krw": 4_000_000},
        "blocks": [],
    }

    response = TestClient(
        _app(_base_deps(snapshot=lambda: snapshot))
    ).get("/api/kis/blocks?compact=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["block_count"] == 12
    assert payload["open_block_count"] == 5
    assert payload["waiting_entry_block_count"] == 1
    assert payload["pending_order_count"] == 0
    assert payload["execution_mode"] == "live"
    assert payload["execute_orders"] is True
    assert payload["kis_ready"] is True
    assert payload["llm_ready"] is True
    assert payload["model"] == "gpt-5.5"
    assert payload["summary"]["open_block_count"] == 5


def test_kis_blocks_router_compact_list_keeps_light_account_positions() -> None:
    snapshot = {
        "status": "ok",
        "summary": {"status": "ok", "block_count": 0},
        "account": {
            "status": "ok",
            "cash_krw": 4_000_000,
            "position_count": 2,
            "positions": [
                {
                    "symbol": "360750",
                    "name": "TIGER 미국S&P500",
                    "qty": 4,
                    "available_qty": 4,
                    "avg_price": 28213.5,
                    "mark_price": 28640,
                    "value_krw": 114560,
                    "unrealized_pnl_krw": 1706,
                    "unrealized_pnl_pct": 1.51,
                    "position_weight": 0.025,
                    "raw_balance_response": "x" * 100_000,
                },
                {
                    "symbol": "423160",
                    "name": "KODEX KOFR금리액티브(합성)",
                    "qty": 1,
                    "value_krw": 110655,
                },
            ],
            "raw_balance_response": "ignored",
        },
        "blocks": [],
    }

    response = TestClient(
        _app(_base_deps(snapshot=lambda: snapshot))
    ).get("/api/kis/blocks?compact=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["account"]["cash_krw"] == 4_000_000
    assert payload["account"]["position_count"] == 2
    assert payload["account"]["positions"] == [
        {
            "symbol": "360750",
            "name": "TIGER 미국S&P500",
            "qty": 4,
            "available_qty": 4,
            "avg_price": 28213.5,
            "mark_price": 28640,
            "value_krw": 114560,
            "unrealized_pnl_krw": 1706,
            "unrealized_pnl_pct": 1.51,
            "position_weight": 0.025,
        },
        {
            "symbol": "423160",
            "name": "KODEX KOFR금리액티브(합성)",
            "qty": 1,
            "value_krw": 110655,
        },
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "raw_balance_response" not in serialized
    assert len(serialized) < 5_000


def test_kis_blocks_router_active_only_uses_compact_snapshot_and_omits_history_feed() -> None:
    calls: list[Any] = []

    def full_snapshot() -> dict[str, Any]:
        calls.append("full")
        return {
            "status": "ok",
            "blocks": [{"block_id": "closed", "status": "closed"}],
            "events": [{"id": 99, "message": "full"}],
        }

    def compact_snapshot(*, refresh_live: bool = True) -> dict[str, Any]:
        calls.append(("compact", refresh_live))
        return {
            "status": "ok",
            "compact": True,
            "summary": {
                "status": "ok",
                "block_count": 10,
                "open_block_count": 1,
            },
            "account": {"cash_krw": 4_000_000},
            "active_blocks": [
                {
                    "block_id": "open1",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "open",
                    "thesis": "active",
                }
            ],
            "recent_closed_blocks": [
                {"block_id": "closed1", "status": "closed", "thesis": "old"}
            ],
            "recent_orders": [{"id": 1, "block_id": "open1"}],
            "recent_events": [{"id": 2, "block_id": "open1", "message": "event"}],
            "latest_manager_run": {"id": 7, "status": "ok", "prompt": {"raw": "x"}},
        }

    response = TestClient(
        _app(
            _base_deps(
                snapshot=full_snapshot,
                snapshot_compact=compact_snapshot,
            )
        )
    ).get("/api/kis/blocks?compact=true&active_only=true")

    assert response.status_code == 200
    payload = response.json()
    assert calls == [("compact", False)]
    assert payload["compact"] is True
    assert payload["active_only"] is True
    assert payload["summary"]["block_count"] == 10
    assert payload["active_blocks"][0]["block_id"] == "open1"
    assert payload["active_blocks"][0]["thesis"] == "active"
    assert "block_history" not in payload
    assert "orders" not in payload
    assert "events" not in payload
    assert "recent_closed_blocks" not in payload
    assert "prompt" not in json.dumps(payload, ensure_ascii=False)


def test_kis_blocks_router_active_only_strips_heavy_manager_and_block_details() -> None:
    def compact_snapshot() -> dict[str, Any]:
        return {
            "status": "ok",
            "compact": True,
            "summary": {"status": "ok", "block_count": 1, "open_block_count": 1},
            "active_blocks": [
                {
                    "block_id": "open1",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "status": "open",
                    "thesis": "a" * 1_000,
                    "risk_note": "r" * 1_000,
                    "metadata": {
                        "decision_class": "hold_update",
                        "entry_trigger_price": 70_000,
                        "live_authority": {
                            "discipline_matrix": {
                                "summary": {"pass_count": 6, "warn_count": 3},
                                "rows": [{"raw": "x" * 50_000}],
                            },
                            "validation_passport": {
                                "status": "pass",
                                "raw": "y" * 50_000,
                            },
                        },
                        "cost_feasibility": {
                            "status": "pass",
                            "gross_target_profit_krw": 1_800,
                            "raw": "z" * 50_000,
                        },
                        "user_directives": [{"message": "m" * 1_000}],
                    },
                    "quote": {"symbol": "005930", "price": 70_000, "raw": "q" * 50_000},
                    "performance": {"current_pnl_pct": 1.2, "raw": "p" * 50_000},
                    "policy_impacts": [{"rule_id": "r1", "reason": "keep", "raw": "i" * 50_000}],
                }
            ],
            "latest_manager_run": {
                "id": 7,
                "status": "ok",
                "run_at": "2026-06-29T03:20:36+00:00",
                "model": "gpt-5.5",
                "mode": "live",
                "actions": {
                    "create_blocks": [{"symbol": "005930"}],
                    "update_blocks": [{"block_id": "open1"}],
                },
                "hold_decision": {"summary": "h" * 50_000},
                "creative_hypotheses": [{"thesis": "c" * 50_000}],
                "prompt": {"raw": "prompt" * 50_000},
            },
        }

    response = TestClient(
        _app(
            _base_deps(
                snapshot=lambda: {"status": "full"},
                snapshot_compact=compact_snapshot,
            )
        )
    ).get("/api/kis/blocks?compact=true&active_only=true")

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["latest_manager_run"] == {
        "id": 7,
        "run_at": "2026-06-29T03:20:36+00:00",
        "status": "ok",
        "mode": "live",
        "model": "gpt-5.5",
        "action_counts": {"create_blocks": 1, "update_blocks": 1},
    }
    assert payload["active_blocks"][0]["thesis"] == "a" * 120
    assert payload["active_blocks"][0]["risk_note"] == "r" * 120
    assert payload["active_blocks"][0]["metadata"] == {
        "decision_class": "hold_update",
        "entry_trigger_price": 70_000,
    }
    assert payload["active_blocks"][0]["quote"] == {
        "symbol": "005930",
        "price": 70_000,
    }
    assert payload["active_blocks"][0]["performance"] == {"current_pnl_pct": 1.2}
    assert "hold_decision" not in serialized
    assert "creative_hypotheses" not in serialized
    assert "discipline_matrix" not in serialized
    assert "cost_feasibility" not in serialized
    assert "user_directives" not in serialized
    assert "policy_impacts" not in serialized
    assert "prompt" not in serialized
    assert len(serialized) < 3_000


def test_kis_blocks_router_requires_primary_ready_for_manager() -> None:
    response = TestClient(_app(_base_deps(primary_ready=lambda: False))).post(
        "/api/kis/blocks/manager/run-once"
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "kis primary account not configured"


def test_kis_blocks_router_maps_missing_order_to_404() -> None:
    response = TestClient(
        _app(_base_deps(cancel_order=lambda order_id, reason: {"status": "missing"}))
    ).post("/api/kis/blocks/orders/7/cancel")

    assert response.status_code == 404
    assert response.json()["detail"] == "order not found"


def test_kis_blocks_router_directive_uses_payload_and_source_ui() -> None:
    seen: dict[str, Any] = {}

    def directive(
        block_id: str,
        message: str,
        preferred_horizon: str,
        scope: str,
        source: str,
    ) -> dict[str, Any]:
        seen.update(
            {
                "block_id": block_id,
                "message": message,
                "preferred_horizon": preferred_horizon,
                "scope": scope,
                "source": source,
            }
        )
        return {"status": "ok"}

    response = TestClient(_app(_base_deps(add_user_directive=directive))).post(
        "/api/kis/blocks/blk1/directive",
        json={"message": "중기로 다뤄줘", "preferred_horizon": "mid"},
    )

    assert response.status_code == 200
    assert seen == {
        "block_id": "blk1",
        "message": "중기로 다뤄줘",
        "preferred_horizon": "mid",
        "scope": "block",
        "source": "ui",
    }


def test_kis_blocks_router_close_requires_primary_ready() -> None:
    response = TestClient(_app(_base_deps(primary_ready=lambda: False))).post(
        "/api/kis/blocks/blk1/close"
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "kis primary account not configured"
