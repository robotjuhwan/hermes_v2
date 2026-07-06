from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.trading import TradingRouteDeps, build_trading_router


class _FakeValidationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run_once(self, *, venue: str = "") -> dict[str, Any]:
        self.calls.append({"method": "run_once", "venue": venue})
        return {"status": "ok", "venue": venue, "discipline_count": 19}


def _client(service: _FakeValidationService) -> TestClient:
    calls: list[dict[str, Any]] = []

    def _live_authority() -> dict[str, Any]:
        calls.append({"method": "live_authority"})
        return {"status": "ok", "authority": {"enabled": True}}

    def _validation_status(venue: str = "") -> dict[str, Any]:
        calls.append({"method": "validation_status", "venue": venue})
        return {"status": "ok", "venue": venue, "readiness": "normal"}

    def _validation_service(venue: str = "") -> _FakeValidationService:
        calls.append({"method": "validation_service", "venue": venue})
        return service

    def _sync_live_performance() -> dict[str, Any]:
        calls.append({"method": "sync_live_performance"})
        return {"status": "ok", "synced_blocks": {"binance": 1}}

    app = FastAPI()
    app.state.calls = calls
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=_live_authority,
                trading_validation_status_payload=_validation_status,
                trading_validation_service=_validation_service,
                sync_live_performance_and_edges=_sync_live_performance,
            )
        )
    )
    return TestClient(app)


def test_live_authority_delegates_to_payload_builder() -> None:
    service = _FakeValidationService()

    with _client(service) as client:
        response = client.get("/api/live/authority")
        full_response = client.get("/api/live/authority?compact=false")
        calls = client.app.state.calls

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "compact": True,
        "edge": {},
        "performance": {},
        "venues": {},
    }
    assert full_response.json() == {"status": "ok", "authority": {"enabled": True}}
    assert calls == [{"method": "live_authority"}, {"method": "live_authority"}]


def test_live_authority_compact_query_strips_lane_bloat() -> None:
    service = _FakeValidationService()

    def _live_authority() -> dict[str, Any]:
        return {
            "status": "ok",
            "edge": {"status": "ok", "raw": "x" * 50_000},
            "venues": {
                "binance": {
                    "status": "ok",
                    "live_grade": "probe",
                    "allow_scale_up": False,
                    "lane_authority": {
                        "weak_lanes": ["spot"],
                        "lane_actions": {
                            f"lane-{idx}": {"raw": "y" * 50_000}
                            for idx in range(50)
                        },
                    },
                    "validation_gate": {
                        "status": "warn",
                        "reason": "z" * 1000,
                    },
                }
            },
        }

    app = FastAPI()
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=_live_authority,
                trading_validation_status_payload=lambda venue="": {},
                trading_validation_service=lambda venue="": service,
                sync_live_performance_and_edges=lambda: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/live/authority?compact=1")

    payload = response.json()
    encoded = response.text
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["compact"] is True
    assert payload["venues"]["binance"]["live_grade"] == "probe"
    assert payload["venues"]["binance"]["lane_authority"]["lane_action_count"] == 50
    assert "raw" not in encoded
    assert len(encoded) < 20_000


def test_trading_validation_status_passes_venue() -> None:
    service = _FakeValidationService()

    with _client(service) as client:
        response = client.get("/api/trading/validation/status?venue=binance")
        full_response = client.get(
            "/api/trading/validation/status?venue=binance&compact=false"
        )
        calls = client.app.state.calls

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "compact": True,
        "venue": "binance",
        "readiness": "normal",
        "discipline_count": 0,
    }
    assert full_response.json() == {
        "status": "ok",
        "venue": "binance",
        "readiness": "normal",
    }
    assert calls == [
        {"method": "validation_status", "venue": "binance"},
        {"method": "validation_status", "venue": "binance"},
    ]


def test_trading_validation_status_compact_query_strips_heavy_payload() -> None:
    service = _FakeValidationService()

    def _validation_status(venue: str = "") -> dict[str, Any]:
        return {
            "status": "ok",
            "venue": venue,
            "summary": {
                "total_score": 50,
                "readiness": "probe",
                "diagnostic_status": "watch",
                "warn_count": 2,
            },
            "payload": {
                "disciplines": [
                    {
                        "id": "cost_simulation",
                        "status": "warn",
                        "evidence": "x" * 50_000,
                        "metric": {"raw": "y" * 50_000},
                    }
                ],
                "metrics": {"raw": "z" * 100_000},
            },
        }

    app = FastAPI()
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=lambda: {},
                trading_validation_status_payload=_validation_status,
                trading_validation_service=lambda venue="": service,
                sync_live_performance_and_edges=lambda: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/trading/validation/status?venue=kis&compact=true")

    payload = response.json()
    assert response.status_code == 200
    assert payload["compact"] is True
    assert payload["venue"] == "kis"
    assert payload["readiness"] == "probe"
    assert payload["warned_discipline_ids"] == ["cost_simulation"]
    assert "raw" not in response.text
    assert len(response.text) < 10_000


def test_trading_validation_short_status_alias_accepts_compact_query() -> None:
    service = _FakeValidationService()

    with _client(service) as client:
        response = client.get("/api/trading/validation?venue=kis")
        full_response = client.get("/api/trading/validation?venue=kis&compact=false")
        calls = client.app.state.calls

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "compact": True,
        "venue": "kis",
        "readiness": "normal",
        "discipline_count": 0,
    }
    assert full_response.json() == {"status": "ok", "venue": "kis", "readiness": "normal"}
    assert calls == [
        {"method": "validation_status", "venue": "kis"},
        {"method": "validation_status", "venue": "kis"},
    ]


def test_trading_validation_compact_query_strips_heavy_payload() -> None:
    service = _FakeValidationService()

    def _validation_status(venue: str = "") -> dict[str, Any]:
        return {
            "status": "ok",
            "venue": venue,
            "run_id": "validation-binance",
            "computed_at": "2026-06-30T12:00:00+00:00",
            "summary": {
                "total_score": 34.2,
                "readiness": "probe",
                "diagnostic_status": "risk_repair",
                "fail_count": 10,
                "warn_count": 6,
            },
            "payload": {
                "disciplines": [
                    {
                        "id": f"discipline-{idx}",
                        "status": "fail" if idx % 2 else "warn",
                        "evidence": "x" * 20_000,
                        "metric": {"raw": "y" * 20_000},
                    }
                    for idx in range(20)
                ],
                "metrics": {"raw": "z" * 100_000},
                "top_bottlenecks": [
                    {
                        "id": "cost_simulation",
                        "status": "fail",
                        "evidence": "비용 드래그 큼",
                    }
                ],
                "primary_next_actions": ["비용 검증 후 대기진입 위주로 축소"],
            },
            "disciplines": [{"id": "raw-top-level", "metric": {"raw": "w" * 100_000}}],
        }

    app = FastAPI()
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=lambda: {},
                trading_validation_status_payload=_validation_status,
                trading_validation_service=lambda venue="": service,
                sync_live_performance_and_edges=lambda: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/trading/validation?venue=binance&compact=true")

    payload = response.json()
    encoded = response.text
    assert response.status_code == 200
    assert payload["compact"] is True
    assert payload["venue"] == "binance"
    assert payload["summary"]["readiness"] == "probe"
    assert payload["score"] == 34.2
    assert payload["fail_count"] == 10
    assert payload["warn_count"] == 6
    assert payload["failed_discipline_ids"][:3] == [
        "discipline-1",
        "discipline-3",
        "discipline-5",
    ]
    assert payload["top_bottlenecks"][0]["id"] == "cost_simulation"
    assert "raw" not in encoded
    assert len(encoded) < 20_000


def test_trading_validation_compact_query_preserves_trade_blocking_scope() -> None:
    service = _FakeValidationService()

    def _validation_status(venue: str = "") -> dict[str, Any]:
        return {
            "status": "ok",
            "venue": venue,
            "summary": {
                "total_score": 34.2,
                "readiness": "probe",
                "diagnostic_status": "risk_repair",
                "fail_count": 10,
            },
            "payload": {
                "remediation_plan": {
                    "status": "blocked",
                    "trade_blocking": False,
                    "blocking_scope": "scale_up_only",
                    "primary_next_action": "risk repair before scale-up",
                    "lane_policy_hints": {
                        "version": "validation_lane_policy_hints_v2",
                        "trade_blocking": False,
                        "blocking_scope": "scale_up_only",
                        "entry_mode": "risk_off_recovery",
                        "risk_budget_mode": "fractional_kelly_probe",
                        "requires_shadow_or_waiting_entry": True,
                        "scale_up_allowed": False,
                        "raw": "x" * 10_000,
                    },
                    "raw": "y" * 10_000,
                }
            },
        }

    app = FastAPI()
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=lambda: {},
                trading_validation_status_payload=_validation_status,
                trading_validation_service=lambda venue="": service,
                sync_live_performance_and_edges=lambda: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/trading/validation/status?venue=binance")

    payload = response.json()
    plan = payload["remediation_plan"]
    hints = plan["lane_policy_hints"]
    assert response.status_code == 200
    assert plan["status"] == "blocked"
    assert plan["trade_blocking"] is False
    assert plan["blocking_scope"] == "scale_up_only"
    assert hints["trade_blocking"] is False
    assert hints["blocking_scope"] == "scale_up_only"
    assert hints["entry_mode"] == "risk_off_recovery"
    assert hints["scale_up_allowed"] is False
    assert "raw" not in response.text


def test_trading_validation_compact_aggregate_removes_redundant_rows() -> None:
    service = _FakeValidationService()

    def _venue_payload(venue: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "venue": venue,
            "summary": {
                "total_score": 44,
                "readiness": "probe",
                "diagnostic_status": "watch",
                "warn_count": 1,
            },
            "payload": {
                "disciplines": [
                    {
                        "id": f"discipline-{idx}",
                        "status": "pass" if idx < 4 else "warn",
                        "evidence": "compact venue detail " + ("x" * 1_000),
                        "action": "repair action " + ("y" * 1_000),
                    }
                    for idx in range(12)
                ],
                "top_bottlenecks": [
                    {
                        "venue": venue,
                        "id": "cost_simulation",
                        "status": "warn",
                        "evidence": "spread/cost still needs repair",
                    }
                ],
            },
        }

    def _validation_status(venue: str = "") -> dict[str, Any]:
        return {
            "status": "ok",
            "venue": venue,
            "summary": {
                "total_score": 40,
                "readiness": "probe",
                "diagnostic_status": "watch",
                "warn_count": 2,
            },
            "disciplines": [
                {
                    "id": f"aggregate-{idx}",
                    "status": "warn",
                    "evidence": "x" * 5_000,
                }
                for idx in range(38)
            ],
            "bottlenecks": [
                {
                    "venue": "kis",
                    "id": "cost_simulation",
                    "status": "warn",
                    "evidence": "aggregate bottleneck",
                }
            ],
            "venues": {
                "kis": _venue_payload("kis"),
                "binance": _venue_payload("binance"),
            },
        }

    app = FastAPI()
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=lambda: {},
                trading_validation_status_payload=_validation_status,
                trading_validation_service=lambda venue="": service,
                sync_live_performance_and_edges=lambda: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/trading/validation/status?compact=true")

    payload = response.json()
    assert response.status_code == 200
    assert "disciplines" not in payload
    assert payload["bottlenecks"][0]["id"] == "cost_simulation"
    assert len(payload["venues"]["kis"]["disciplines"]) == 6
    assert payload["venues"]["kis"]["disciplines"][0]["id"] == "discipline-4"
    assert all(row["status"] == "warn" for row in payload["venues"]["kis"]["disciplines"])
    assert len(payload["venues"]["kis"]["disciplines"][0]["evidence"]) <= 160
    assert len(payload["venues"]["kis"]["disciplines"][0]["action"]) <= 160
    assert payload["venues"]["kis"]["top_bottlenecks"][0]["id"] == "cost_simulation"
    assert "bottlenecks" not in payload["venues"]["kis"]
    assert len(response.text) < 9_000


def test_trading_validation_compact_query_promotes_summary_counts() -> None:
    service = _FakeValidationService()

    def _validation_status(venue: str = "") -> dict[str, Any]:
        return {
            "status": "ok",
            "venue": venue,
            "payload": {
                "summary": {
                    "total_score": 18.42,
                    "readiness": "blocked_by_validation",
                    "diagnostic_status": "blocked",
                    "pass_count": 2,
                    "warn_count": 3,
                    "fail_count": 10,
                    "missing_count": 4,
                    "diagnostic_fail_count": 10,
                    "core_missing_count": 1,
                    "hard_missing_count": 1,
                },
                "disciplines": [],
            },
        }

    app = FastAPI()
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=lambda: {},
                trading_validation_status_payload=_validation_status,
                trading_validation_service=lambda venue="": service,
                sync_live_performance_and_edges=lambda: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/trading/validation/status?venue=binance")

    payload = response.json()
    assert response.status_code == 200
    assert payload["summary"]["fail_count"] == 10
    assert payload["score"] == 18.42
    assert payload["fail_count"] == 10
    assert payload["missing_count"] == 4
    assert payload["diagnostic_fail_count"] == 10
    assert payload["core_missing_count"] == 1
    assert payload["hard_missing_count"] == 1


def test_trading_validation_compact_query_preserves_structured_next_actions() -> None:
    service = _FakeValidationService()

    def _validation_status(venue: str = "") -> dict[str, Any]:
        return {
            "status": "ok",
            "venue": venue,
            "payload": {
                "summary": {"readiness": "probe"},
                "primary_next_actions": [
                    {
                        "venue": "binance",
                        "status": "risk_repair",
                        "action": "비용 드래그가 낮은 lane만 남기기",
                        "reason": "cost drag 28%",
                        "raw": "x" * 10_000,
                    },
                    "레거시 문자열 액션도 구조화",
                ],
            },
        }

    app = FastAPI()
    app.include_router(
        build_trading_router(
            TradingRouteDeps(
                require_admin_auth=lambda: None,
                live_authority_payload=lambda: {},
                trading_validation_status_payload=_validation_status,
                trading_validation_service=lambda venue="": service,
                sync_live_performance_and_edges=lambda: {},
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/trading/validation/status?venue=binance&compact=true")

    payload = response.json()
    assert response.status_code == 200
    assert payload["primary_next_actions"] == [
        {
            "venue": "binance",
            "status": "risk_repair",
            "action": "비용 드래그가 낮은 lane만 남기기",
            "reason": "cost drag 28%",
        },
        {"action": "레거시 문자열 액션도 구조화"},
    ]
    assert "raw" not in response.text


def test_trading_validation_run_once_normalizes_venue_syncs_first_and_attaches_sync() -> None:
    service = _FakeValidationService()

    with _client(service) as client:
        response = client.post("/api/trading/validation/run-once?venue= Binance ")
        calls = client.app.state.calls

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "venue": "binance",
        "discipline_count": 19,
        "sync": {"status": "ok", "synced_blocks": {"binance": 1}},
    }
    assert calls == [
        {"method": "validation_service", "venue": "binance"},
        {"method": "sync_live_performance"},
    ]
    assert service.calls == [{"method": "run_once", "venue": "binance"}]
