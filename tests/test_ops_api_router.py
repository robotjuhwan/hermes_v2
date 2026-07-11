from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import tradecraft.api.ops as ops_api
from tradecraft.api.ops import OpsRouteDeps, build_ops_router


def test_ops_router_blocks_kis_restart_during_open_blocks_without_confirmation() -> None:
    calls: list[list[str] | None] = []

    def readiness() -> dict[str, Any]:
        return {
            "kis_block_trader": {
                "status": {
                    "clock": {"is_market_open": True},
                    "open_block_count": 2,
                    "waiting_entry_block_count": 1,
                    "pending_order_count": 0,
                }
            },
        }

    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = delay_sec
        calls.append(keys)
        return {"keys": keys}

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=readiness,
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post(
        "/api/ops/restart",
        json={"keys": ["kis_block_trader"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "kis restart requires confirmation during active market blocks"
    )
    assert calls == []


def test_ops_router_allows_confirmed_kis_restart_during_open_blocks() -> None:
    calls: list[list[str] | None] = []

    def readiness() -> dict[str, Any]:
        return {
            "kis_block_trader": {
                "status": {
                    "clock": {"is_market_open": True},
                    "open_block_count": 2,
                    "waiting_entry_block_count": 1,
                    "pending_order_count": 0,
                }
            },
        }

    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = delay_sec
        calls.append(keys)
        return {"keys": keys}

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=readiness,
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post(
        "/api/ops/restart",
        json={
            "keys": ["kis_block_trader"],
            "confirm_active_trading_restart": True,
        },
    )

    assert response.status_code == 200
    assert calls == [["kis_block_trader"]]


def test_ops_router_blocks_binance_restart_when_live_execution_without_confirmation() -> None:
    calls: list[list[str] | None] = []

    def readiness() -> dict[str, Any]:
        return {
            "binance_block_trader": {
                "execution": {
                    "spot_mode": "live",
                    "futures_mode": "live",
                    "upbit_spot_mode": "paper",
                },
                "activity_pressure": {
                    "status": "action_required",
                    "source": "binance_activity_gap",
                },
            },
        }

    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = delay_sec
        calls.append(keys)
        return {"keys": keys}

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=readiness,
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post(
        "/api/ops/restart",
        json={"keys": ["binance_block_trader", "watchdog"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "binance restart requires confirmation while live crypto execution is enabled"
    )
    assert calls == []


def test_ops_router_allows_confirmed_binance_restart_when_live_execution() -> None:
    calls: list[list[str] | None] = []

    def readiness() -> dict[str, Any]:
        return {
            "binance_block_trader": {
                "execution": {
                    "spot_mode": "live",
                    "futures_mode": "paper",
                    "upbit_spot_mode": "paper",
                }
            }
        }

    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = delay_sec
        calls.append(keys)
        return {"keys": keys}

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=readiness,
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post(
        "/api/ops/restart",
        json={
            "keys": ["binance_block_trader"],
            "confirm_active_trading_restart": True,
        },
    )

    assert response.status_code == 200
    assert calls == [["binance_block_trader"]]


def test_ops_router_accepts_targets_alias_without_restarting_everything() -> None:
    calls: list[list[str] | None] = []

    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = delay_sec
        calls.append(keys)
        return {"keys": keys}

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {"status": "green"},
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post(
        "/api/ops/restart",
        json={"targets": ["runtime"]},
    )

    assert response.status_code == 200
    assert response.json()["keys"] == ["runtime"]
    assert calls == [["runtime"]]


def test_ops_router_rejects_malformed_restart_keys_instead_of_defaulting_all() -> None:
    calls: list[list[str] | None] = []

    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = delay_sec
        calls.append(keys)
        return {"keys": keys}

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {"status": "green"},
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post(
        "/api/ops/restart",
        json={"keys": "runtime"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "keys must be a list of runner keys"
    assert calls == []


def test_ops_router_uses_fresh_restart_readiness_instead_of_cached_status() -> None:
    calls: list[list[str] | None] = []

    def cached_readiness() -> dict[str, Any]:
        return {"status": "green"}

    def fresh_restart_readiness() -> dict[str, Any]:
        return {
            "kis_block_trader": {
                "status": {
                    "clock": {"is_market_open": True},
                    "open_block_count": 1,
                    "waiting_entry_block_count": 0,
                    "pending_order_count": 0,
                }
            }
        }

    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = delay_sec
        calls.append(keys)
        return {"keys": keys}

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=cached_readiness,
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
                build_ops_restart_readiness=fresh_restart_readiness,
            )
        )
    )

    response = TestClient(app).post(
        "/api/ops/restart",
        json={"keys": ["kis_block_trader"]},
    )

    assert response.status_code == 409
    assert calls == []


def test_ops_router_serves_health_without_admin_dependency() -> None:
    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {"status": "ok"},
                build_codex_native_status=lambda: {"status": "ok"},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {"cpu": 0},
                watchdog_status=lambda: {"status": "ok"},
                restart_runner_processes=lambda keys, delay_sec=0.5: {"keys": keys},
                build_settings_catalog=lambda: {"settings": []},
                update_settings_env=lambda updates, confirm_high_risk=False: {
                    "updated": updates,
                    "confirmed": confirm_high_risk,
                },
            )
        )
    )

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["service"] == "tradecraft-control"


def test_ops_router_readiness_compact_strips_heavy_nested_payloads() -> None:
    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {
                    "status": "yellow",
                    "checked_at": "2026-06-30T04:30:00+00:00",
                    "live_trading_enabled": True,
                    "warnings": ["restart_required"],
                    "blockers": [],
                    "advisories": ["trading_validation_probe_binance"],
                    "trading_validation_advisories": [
                        "trading_validation_probe_binance"
                    ],
                    "processes": {
                        "control": {
                            "key": "control",
                            "label": "control API",
                            "status": "running",
                            "pid": 42110,
                            "started_at": "2026-06-30T04:00:00+00:00",
                            "stale_process": False,
                            "raw_matches": ["python " + "x" * 50_000],
                        }
                    },
                    "memory": {
                        "enabled": True,
                        "seeded": True,
                        "reflection_count": 10,
                        "raw_journals": "m" * 50_000,
                    },
                    "market_judge": {
                        "enabled": True,
                        "schedule": {
                            "next_llm_due_at": "2026-07-01T08:30:00+09:00",
                            "recent_runs": [{"prompt": "p" * 50_000}],
                        },
                        "status": {
                            "latest_run_status": "ok",
                            "config": {"judge_interval_sec": 1800},
                            "raw_payload": "j" * 50_000,
                        },
                    },
                    "binance_block_trader": {
                        "enabled": True,
                        "warnings": ["binance_activity_pressure_open"],
                            "activity_pressure": {
                                "status": "action_required",
                                "level": "high",
                                "source": "binance_activity_gap",
                            "entry_stale_hours": 73.8,
                            "candidate_symbols": [
                                "ESPUSDT",
                                "SOLUSDT",
                                "XLMUSDT",
                                ],
                                "raw_candidates": ["x" * 50_000],
                            },
                            "activity_repair_actions": [
                                {
                                    "id": "refresh_binance_crypto_research_context",
                                    "label": "Binance 후보 리서치 갱신",
                                    "detail": "r" * 50_000,
                                    "severity": "warn",
                                    "endpoint": "/api/crypto/research/run-once",
                                    "method": "POST",
                                    "signals": ["binance_activity_pressure_open"],
                                    "request_payload": {
                                        "symbols": ["ESPUSDT", "SOLUSDT"],
                                        "raw": "p" * 50_000,
                                    },
                                    "raw": "a" * 50_000,
                                }
                            ],
                            "entry_activity": {
                                "version": "binance_entry_activity_v1",
                                "status": "stale_binance_entries",
                                "latest_binance_entry_at": (
                                    "2026-07-05T00:00:00+00:00"
                                ),
                                "latest_binance_entry_market": "futures",
                                "latest_upbit_entry_at": "2026-07-07T23:00:00+00:00",
                                "binance_entry_stale_hours": 73.5,
                                "binance_entry_count": 2,
                                "upbit_entry_count": 4,
                                "raw": "e" * 50_000,
                            },
                            "manager_contract_replay": {
                                "contract_replay_status": (
                                    "stored_error_resolved_by_current_contract"
                                ),
                                "stored_error_message": "e" * 50_000,
                                "current_contract_error": "",
                                "action_count": 0,
                                "current_replay_action_count": 1,
                                "current_replay_auto_action_count": 1,
                                "current_replay_action_sections": {
                                    "create_blocks": 1,
                                    "raw": "s" * 50_000,
                                },
                                "current_replay_hold_summary": "h" * 50_000,
                                "current_replay_watch_symbols": [
                                    "KRW-SOL",
                                    "SOLUSDT",
                                    "x" * 50_000,
                                ],
                                "current_replay_next_triggers": [
                                    {
                                        "symbol": "SOLUSDT",
                                        "market": "futures",
                                        "condition": "pattern prior recovers",
                                        "price": 0.0,
                                        "reason": "pattern prior missing",
                                    }
                                ],
                                "current_replay_data_gaps": [
                                    "pattern prior missing",
                                    "d" * 50_000,
                                ],
                                "current_replay_auto_create_preview": [
                                    {
                                        "symbol": "SOLUSDT",
                                        "market": "futures",
                                        "side": "long",
                                        "entry_style": "wait_for_price",
                                        "entry_trigger_price": "150.0",
                                        "entry_trigger_operator": "<=",
                                        "entry_price": "150.0",
                                        "target_price": "180.0",
                                        "stop_price": "140.0",
                                        "qty": "0.2",
                                        "quote_budget_usdt": "30.0",
                                        "min_executable_notional_usdt": "20.0",
                                        "min_executable_qty": "0.133333",
                                        "notional_estimate_usdt": "30.0",
                                        "auto_materialized_reason": (
                                            "manager_rejected_probe_design_without_current_execution_gate"
                                        ),
                                        "raw": "drop me",
                                    }
                                ],
                                "raw": "r" * 50_000,
                            },
                            "status": {
                                "latest_manager_status": "error",
                                "latest_unresolved_manager_error": {
                                    "error_message": "e" * 50_000,
                                },
                        },
                    },
                    "disk_space": {
                        "status": "ok",
                        "free_bytes": 123456,
                        "total_bytes": 999999,
                    },
                    "advisory_details": [
                        {
                            "signal": "trading_validation_probe_binance",
                            "venue": "binance",
                            "readiness": "probe",
                            "diagnostic_status": "watch",
                            "score": 34.2,
                            "failed_discipline_ids": ["cost_simulation"],
                            "weak_lanes": ["futures_short", "spot_long"],
                            "top_bottlenecks": [
                                {
                                    "id": "cost_simulation",
                                    "label": "거래비용 시뮬레이션",
                                    "status": "fail",
                                    "evidence": "e" * 20_000,
                                    "metric": {"raw": "z" * 20_000},
                                }
                            ],
                            "raw_payload": "a" * 50_000,
                        }
                    ],
                    "trading_validation": {
                        "status": "ok",
                        "readiness": "probe",
                        "diagnostic_status": "risk_repair",
                        "score": 50.0,
                        "summary": {"fail_count": 1, "warn_count": 2},
                        "bottlenecks": [
                            {
                                "id": "cost_simulation",
                                "label": "거래비용 시뮬레이션",
                                "status": "fail",
                                "metric": {"raw": "b" * 50_000},
                            }
                        ],
                        "venues": {"binance": {"payload": {"raw": "v" * 50_000}}},
                    },
                    "remediation_actions": [
                        {
                            "id": "review",
                            "label": "점검",
                            "detail": "d" * 20_000,
                            "endpoint": "/api/trading/validation/status",
                            "method": "GET",
                            "severity": "warn",
                            "request_payload": {
                                "keys": ["binance_block_trader", "watchdog"],
                                "raw": "p" * 50_000,
                            },
                            "requires_confirmation": True,
                            "follow_up_actions": [
                                {
                                    "id": "check_binance_status_after_restart",
                                    "label": "Binance 상태 재확인",
                                    "endpoint": "/api/binance/blocks/status",
                                    "method": "GET",
                                    "raw": "f" * 50_000,
                                },
                                {
                                    "id": "run_binance_manager_after_restart",
                                    "label": "Binance 매니저 즉시 실행",
                                    "endpoint": "/api/binance/blocks/manager/run-once",
                                    "method": "POST",
                                    "request_payload": {
                                        "confirm_live_manager_run": True,
                                        "raw": "p" * 50_000,
                                    },
                                    "requires_confirmation": True,
                                    "raw": "f" * 50_000,
                                },
                                {
                                    "id": "run_binance_executor_after_manager",
                                    "label": "Binance 실행 틱 확인 실행",
                                    "endpoint": "/api/binance/blocks/executor/tick",
                                    "method": "POST",
                                    "request_payload": {
                                        "confirm_live_executor_tick": True,
                                        "raw": "p" * 50_000,
                                    },
                                    "requires_confirmation": True,
                                    "raw": "f" * 50_000,
                                },
                            ],
                            "raw": "r" * 20_000,
                        }
                    ],
                },
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=lambda keys, delay_sec=0.5: {"keys": keys},
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).get("/api/ops/readiness?compact=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["compact"] is True
    assert payload["status"] == "yellow"
    assert payload["warnings"] == ["restart_required"]
    assert payload["processes"]["control"] == {
        "key": "control",
        "label": "control API",
        "status": "running",
        "pid": 42110,
        "started_at": "2026-06-30T04:00:00+00:00",
        "stale_process": False,
    }
    assert payload["memory"] == {
        "enabled": True,
        "seeded": True,
        "reflection_count": 10,
    }
    assert "jue_codex_lab" not in payload
    assert payload["market_judge"]["schedule"] == {
        "next_llm_due_at": "2026-07-01T08:30:00+09:00"
    }
    assert payload["binance_block_trader"]["warnings"] == [
        "binance_activity_pressure_open"
    ]
    assert payload["binance_block_trader"]["activity_pressure"] == {
        "status": "action_required",
        "level": "high",
        "source": "binance_activity_gap",
        "entry_stale_hours": 73.8,
        "candidate_symbols": ["ESPUSDT", "SOLUSDT", "XLMUSDT"],
    }
    assert payload["binance_block_trader"]["activity_repair_actions"] == [
        {
            "id": "refresh_binance_crypto_research_context",
            "label": "Binance 후보 리서치 갱신",
            "detail": "r" * 220,
            "severity": "warn",
            "endpoint": "/api/crypto/research/run-once",
            "method": "POST",
            "signals": ["binance_activity_pressure_open"],
            "request_payload": {"symbols": ["ESPUSDT", "SOLUSDT"]},
        }
    ]
    assert payload["binance_block_trader"]["entry_activity"] == {
        "version": "binance_entry_activity_v1",
        "status": "stale_binance_entries",
        "latest_binance_entry_at": "2026-07-05T00:00:00+00:00",
        "latest_binance_entry_market": "futures",
        "latest_upbit_entry_at": "2026-07-07T23:00:00+00:00",
        "binance_entry_stale_hours": 73.5,
        "binance_entry_count": 2,
        "upbit_entry_count": 4,
    }
    assert payload["binance_block_trader"]["manager_contract_replay"] == {
        "contract_replay_status": "stored_error_resolved_by_current_contract",
        "stored_error_message": "e" * 220,
        "action_count": 0,
        "current_replay_action_count": 1,
        "current_replay_auto_action_count": 1,
        "current_replay_action_sections": {"create_blocks": 1},
        "current_replay_hold_summary": "h" * 220,
        "current_replay_watch_symbols": ["KRW-SOL", "SOLUSDT", "x" * 120],
        "current_replay_next_triggers": [
            {
                "symbol": "SOLUSDT",
                "market": "futures",
                "condition": "pattern prior recovers",
                "price": 0.0,
                "reason": "pattern prior missing",
            }
        ],
        "current_replay_data_gaps": ["pattern prior missing", "d" * 120],
        "current_replay_auto_create_preview": [
            {
                "symbol": "SOLUSDT",
                "market": "futures",
                "side": "long",
                "entry_style": "wait_for_price",
                "entry_trigger_price": 150.0,
                "entry_trigger_operator": "<=",
                "entry_price": 150.0,
                "target_price": 180.0,
                "stop_price": 140.0,
                "qty": 0.2,
                "quote_budget_usdt": 30.0,
                "min_executable_notional_usdt": 20.0,
                "min_executable_qty": 0.133333,
                "notional_estimate_usdt": 30.0,
                "auto_materialized_reason": (
                    "manager_rejected_probe_design_without_current_execution_gate"
                ),
            }
        ],
    }
    assert payload["advisory_details"][0]["weak_lanes"] == [
        "futures_short",
        "spot_long",
    ]
    assert payload["advisory_details"][0]["top_bottlenecks"] == [
        {
            "id": "cost_simulation",
            "label": "거래비용 시뮬레이션",
            "status": "fail",
            "evidence": "e" * 220,
        }
    ]
    assert payload["trading_validation"]["bottlenecks"] == [
        {
            "id": "cost_simulation",
            "label": "거래비용 시뮬레이션",
            "status": "fail",
        }
    ]
    assert payload["remediation_actions"][0]["detail"] == "d" * 220
    assert payload["remediation_actions"][0]["request_payload"] == {
        "keys": ["binance_block_trader", "watchdog"]
    }
    assert payload["remediation_actions"][0]["requires_confirmation"] is True
    assert payload["remediation_actions"][0]["follow_up_actions"] == [
        {
            "id": "check_binance_status_after_restart",
            "label": "Binance 상태 재확인",
            "endpoint": "/api/binance/blocks/status",
            "method": "GET",
        },
        {
            "id": "run_binance_manager_after_restart",
            "label": "Binance 매니저 즉시 실행",
            "endpoint": "/api/binance/blocks/manager/run-once",
            "method": "POST",
            "request_payload": {"confirm_live_manager_run": True},
            "requires_confirmation": True,
        },
        {
            "id": "run_binance_executor_after_manager",
            "label": "Binance 실행 틱 확인 실행",
            "endpoint": "/api/binance/blocks/executor/tick",
            "method": "POST",
            "request_payload": {"confirm_live_executor_tick": True},
            "requires_confirmation": True,
        },
    ]
    serialized = response.text
    assert "raw_matches" not in serialized
    assert "raw_payload" not in serialized
    assert "venues" not in serialized
    assert len(serialized) < 10_000


def test_ops_compact_readiness_compacts_each_section_once(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def compact_section(section: dict[str, Any]) -> dict[str, Any]:
        calls.append(section)
        return {"status": section["status"]}

    monkeypatch.setattr(ops_api, "_compact_readiness_section", compact_section)

    payload = ops_api._compact_ops_readiness(
        {
            "status": "green",
            "memory": {"status": "ok"},
            "market_judge": {"status": "ok"},
        }
    )

    assert payload["memory"] == {"status": "ok"}
    assert payload["market_judge"] == {"status": "ok"}
    assert calls == [{"status": "ok"}, {"status": "ok"}]


def test_ops_compact_readiness_keeps_stored_wiki_v3_health() -> None:
    payload = ops_api._compact_ops_readiness(
        {
            "status": "red",
            "jue_wiki": {
                "enabled": True,
                "active_read_mode": "required",
                "publication_age_sec": 120,
                "comparison_count_by_venue": {"kis": 500, "binance": 420},
                "eligibility_by_venue": {
                    "kis": {
                        "version": "wiki_shadow_eligibility_v1",
                        "required_eligible": True,
                        "blockers": [],
                        "evaluated_through": "2026-07-12T00:00:00+00:00",
                    },
                    "binance": {
                        "required_eligible": False,
                        "blockers": ["insufficient_complete_comparisons"],
                    },
                },
                "v3": {
                    "active_read_mode": "required",
                    "by_scope": {
                        "kis": {
                            "snapshot_id": "snapshot:kis:1",
                            "snapshot_age_sec": 120,
                            "last_compile_status": "ok",
                            "last_lint_status": "ok",
                            "index_rebuild": {"status": "ok"},
                        }
                    },
                },
                "status": {
                    "status": "ok",
                },
            },
        }
    )

    wiki = payload["jue_wiki"]
    assert wiki["active_read_mode"] == "required"
    assert wiki["publication_age_sec"] == 120
    assert wiki["comparison_count_by_venue"] == {"kis": 500, "binance": 420}
    assert wiki["eligibility_by_venue"]["binance"]["required_eligible"] is False
    assert wiki["eligibility_by_venue"]["kis"]["version"] == (
        "wiki_shadow_eligibility_v1"
    )
    assert wiki["eligibility_by_venue"]["kis"]["evaluated_through"] == (
        "2026-07-12T00:00:00+00:00"
    )
    assert wiki["v3"]["by_scope"]["kis"]["snapshot_id"] == "snapshot:kis:1"


def test_ops_compact_readiness_uses_direct_builder_without_full_payload() -> None:
    app = FastAPI()

    def full_readiness() -> dict[str, Any]:
        raise AssertionError("compact readiness must not build the full payload")

    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=full_readiness,
                build_compact_ops_readiness=lambda: {
                    "compact": True,
                    "status": "green",
                    "warnings": [],
                },
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=lambda keys, delay_sec=0.5: {"keys": keys},
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).get("/api/ops/readiness?compact=true")

    assert response.status_code == 200
    assert response.json() == {
        "compact": True,
        "status": "green",
        "warnings": [],
    }


def test_ops_router_serves_legacy_processes_from_readiness_payload() -> None:
    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {
                    "status": "yellow",
                    "checked_at": "2026-06-30T04:30:00+00:00",
                    "processes": {
                        "control": {
                            "status": "running",
                            "pid": 42110,
                            "label": "control app",
                        },
                        "watchdog": {
                            "status": "stopped",
                            "pid": None,
                            "label": "watchdog",
                        },
                    },
                    "stale_processes": ["naver_reports"],
                    "missing_processes": ["watchdog"],
                    "duplicate_processes": [],
                },
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=lambda keys, delay_sec=0.5: {"keys": keys},
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).get("/api/ops/processes")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checked_at": "2026-06-30T04:30:00+00:00",
        "processes": {
            "control": {
                "status": "running",
                "pid": 42110,
                "label": "control app",
            },
            "watchdog": {
                "status": "stopped",
                "pid": None,
                "label": "watchdog",
            },
        },
        "stale_processes": ["naver_reports"],
        "missing_processes": ["watchdog"],
        "duplicate_processes": [],
    }


def test_ops_router_codex_check_awaits_refresh_with_force() -> None:
    calls: list[dict[str, Any]] = []

    async def refresh(*, force: bool = False) -> None:
        calls.append({"force": force})

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {"status": "ok"},
                build_codex_native_status=lambda: {"status": "native-ok"},
                refresh_codex_native_checks=refresh,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=lambda keys, delay_sec=0.5: {},
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post("/api/codex/native/check")

    assert response.status_code == 200
    assert response.json() == {"status": "native-ok"}
    assert calls == [{"force": True}]


def test_ops_router_serves_legacy_codex_native_status_path() -> None:
    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {"status": "ok"},
                build_codex_native_status=lambda: {"status": "native-ok"},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=lambda keys, delay_sec=0.5: {},
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).get("/api/ops/codex-native/status")

    assert response.status_code == 200
    assert response.json() == {"status": "native-ok"}


def test_ops_router_restart_maps_invalid_key_to_400() -> None:
    def restart(keys: list[str] | None, delay_sec: float = 0.5) -> dict[str, Any]:
        _ = (keys, delay_sec)
        raise ValueError("unknown runner")

    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {},
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=restart,
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).post("/api/ops/restart", json={"keys": ["bad"]})

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown runner"


def test_ops_router_settings_update_requires_updates_object() -> None:
    app = FastAPI()
    app.include_router(
        build_ops_router(
            OpsRouteDeps(
                require_admin_auth=lambda: None,
                build_ops_readiness=lambda: {},
                build_codex_native_status=lambda: {},
                refresh_codex_native_checks=lambda force=False: None,
                system_metrics_snapshot=lambda: {},
                watchdog_status=lambda: {},
                restart_runner_processes=lambda keys, delay_sec=0.5: {},
                build_settings_catalog=lambda: {},
                update_settings_env=lambda updates, confirm_high_risk=False: {},
            )
        )
    )

    response = TestClient(app).patch("/api/settings/values", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "updates required"
