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
