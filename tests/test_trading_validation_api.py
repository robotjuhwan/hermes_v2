from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tradecraft.main as main_module
from tradecraft.main import app, settings
from tradecraft.api.trading_validation_payloads import (
    summarize_trading_validation_bottlenecks,
)
from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    LivePerformanceRepository,
)
from tradecraft.services.trading_validation import TradingValidationRepository


@pytest.fixture(autouse=True)
def _isolate_ops_readiness_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module.jue_wiki_service,
        "status",
        lambda: {"status": "ok", "page_count": 0},
    )
    monkeypatch.setattr(main_module, "_ops_readiness_cache_payload", None)
    monkeypatch.setattr(main_module, "_ops_readiness_cache_expires_at", 0.0)
    monkeypatch.setattr(main_module, "_ops_readiness_cache_key", None)


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def _seed_live_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for idx, exit_price in enumerate([104.0, 97.0, 106.0, 99.0, 108.0, 103.0], start=1):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"api-b-{idx}",
                symbol="BTCUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.2,
                filled=True,
            )
        )


def _seed_kis_live_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="api-kis-1",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000.0,
            exit_price=72_000.0,
            qty=1.0,
            fees=100.0,
            taxes=140.0,
            slippage=70.0,
            filled=True,
            metadata={
                "horizon": "mid",
                "strategy_family": "value_pullback",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 100.0, "taxes": 140.0, "slippage": 70.0},
            },
        ),
        source={
            "metadata": {
                "horizon": "mid",
                "strategy_family": "value_pullback",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 100.0, "taxes": 140.0, "slippage": 70.0},
            }
        },
    )


def test_trading_validation_status_requires_admin() -> None:
    with TestClient(app) as client:
        response = client.get("/api/trading/validation/status")

    assert response.status_code == 401


def test_trading_validation_run_once_api(monkeypatch, tmp_path: Path) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_live_performance(live_path)
    monkeypatch.setattr(settings, "live_performance_db_path", str(live_path))
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )

    with TestClient(app) as client:
        run = client.post(
            "/api/trading/validation/run-once?venue=binance",
            headers=_admin_headers(monkeypatch),
        )
        status = client.get(
            "/api/trading/validation/status?venue=binance",
            headers=_admin_headers(monkeypatch),
        )

    assert run.status_code == 200
    payload = run.json()
    assert payload["status"] == "ok"
    assert payload["discipline_count"] == 19
    assert payload["summary"]["readiness"] in {
        "research_only",
        "probe",
        "normal",
        "scale_ready",
        "blocked_by_validation",
    }
    assert status.status_code == 200
    latest = status.json()
    assert latest["status"] == "ok"
    assert latest["run_id"] == payload["run_id"]
    assert latest["payload"]["discipline_count"] == 19
    assert latest["readiness"] == latest["payload"]["summary"]["readiness"]
    assert latest["score"] == latest["payload"]["summary"]["total_score"]
    assert latest["pass_count"] == latest["payload"]["summary"]["pass_count"]
    assert latest["warn_count"] == latest["payload"]["summary"]["warn_count"]
    assert latest["fail_count"] == latest["payload"]["summary"]["fail_count"]
    assert latest["missing_count"] == latest["payload"]["summary"]["missing_count"]
    assert latest["sample_count"] == latest["metrics"]["sample_count"]
    assert latest["failed_discipline_ids"] == [
        row["id"] for row in latest["disciplines"] if row["status"] == "fail"
    ]
    assert latest["discipline_count"] == 19
    assert len(latest["disciplines"]) == 19
    assert latest["operator_guidance"]
    assert latest["remediation_plan"]["status"] in {
        "clear",
        "needs_work",
        "probe_rebuild",
        "blocked",
    }


def test_trading_validation_status_defaults_to_venue_aggregate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "trading_validation_max_age_sec",
        864000000,
        raising=False,
    )
    repo = TradingValidationRepository(validation_path)
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-binance-weak",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {"id": "profit_factor", "label": "수익팩터", "status": "fail"}
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 4,
                "warn_count": 5,
                "fail_count": 10,
                "missing_count": 0,
                "core_fail_count": 0,
                "hard_fail_count": 0,
            },
        }
    )
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-kis-healthy",
            "venue": "kis",
            "scope": "live",
            "computed_at": "2026-06-01T00:05:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {"id": "data_validation", "label": "데이터 검증", "status": "pass"}
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 6,
                "warn_count": 13,
                "fail_count": 0,
                "missing_count": 0,
                "core_fail_count": 0,
                "hard_fail_count": 0,
            },
        }
    )

    with TestClient(app) as client:
        aggregate = client.get(
            "/api/trading/validation/status",
            headers=_admin_headers(monkeypatch),
        )
        kis_only = client.get(
            "/api/trading/validation/status?venue=kis",
            headers=_admin_headers(monkeypatch),
        )

    assert aggregate.status_code == 200
    payload = aggregate.json()
    assert payload["venue"] == "aggregate"
    assert set(payload["venues"]) == {"kis", "binance"}
    assert payload["summary"]["pass_count"] == 10
    assert payload["summary"]["warn_count"] == 18
    assert payload["summary"]["fail_count"] == 10
    assert payload["summary"]["readiness"] == "probe"
    assert payload["venues"]["kis"]["summary"]["fail_count"] == 0
    assert payload["venues"]["binance"]["summary"]["fail_count"] == 10
    assert payload["venues"]["binance"]["summary"]["readiness"] == "probe"
    assert payload["bottlenecks"][0]["venue"] == "binance"
    assert kis_only.json()["venue"] == "kis"


def test_trading_validation_status_uses_venue_specific_service(monkeypatch) -> None:
    created_for: list[str] = []
    latest_called_with: list[str] = []

    class FakeConfig:
        strategy_revision_id = "test-revision"

    class FakeService:
        config = FakeConfig()

        def latest(self, venue: str = "") -> dict[str, object]:
            latest_called_with.append(venue)
            return {
                "status": "ok",
                "run_id": "validation-kis-latest",
                "venue": venue,
                "computed_at": "2026-06-01T00:00:00+00:00",
                "discipline_count": 19,
                "disciplines": [
                    {
                        "id": "data_validation",
                        "label": "데이터 검증",
                        "status": "pass",
                    }
                ],
                "summary": {
                    "readiness": "normal",
                    "total_score": 100,
                    "pass_count": 19,
                    "warn_count": 0,
                    "fail_count": 0,
                    "missing_count": 0,
                    "core_fail_count": 0,
                    "hard_fail_count": 0,
                },
                "metrics": {
                    "sample_count": 8,
                    "failure_attribution": {
                        "recovery_focus": [
                            "symbol=BNBUSDT net -7.30, PF 0.00, expectancy -1.67%"
                        ],
                        "worst_groups": [
                            {
                                "group_type": "symbol",
                                "group": "BNBUSDT",
                                "sample_count": 5,
                                "total_net_pnl": -7.3,
                                "expectancy_pct": -1.67,
                                "win_rate_pct": 0.0,
                                "profit_factor": 0.0,
                                "cost_drag_pct_of_gross_pnl": 25.6,
                                "risk_score": 47.4,
                            }
                        ],
                    },
                    "cost_simulation": {
                        "worst_cost_groups": [
                            {
                                "group_type": "horizon",
                                "group": "short",
                                "sample_count": 21,
                                "total_net_pnl": -8.3,
                                "total_cost": 2.66,
                                "cost_drag_pct_of_abs_gross_pnl": 47.3,
                                "net_negative_after_cost": False,
                            }
                        ],
                    },
                },
            }

    def fake_service(venue: str = "") -> FakeService:
        created_for.append(venue)
        return FakeService()

    monkeypatch.setattr(
        main_module,
        "_trading_validation_service",
        fake_service,
        raising=False,
    )

    with TestClient(app) as client:
        created_for.clear()
        latest_called_with.clear()
        response = client.get(
            "/api/trading/validation/status?venue=kis",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["failure_drivers"] == [
        {
            "group_type": "symbol",
            "group": "BNBUSDT",
            "sample_count": 5,
            "total_net_pnl": -7.3,
            "expectancy_pct": -1.67,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "cost_drag_pct_of_gross_pnl": 25.6,
            "risk_score": 47.4,
        }
    ]
    assert payload["cost_drivers"] == [
        {
            "group_type": "horizon",
            "group": "short",
            "sample_count": 21,
            "total_net_pnl": -8.3,
            "total_cost": 2.66,
            "cost_drag_pct_of_abs_gross_pnl": 47.3,
            "net_negative_after_cost": False,
        }
    ]
    assert payload["recovery_focus"] == [
        "symbol=BNBUSDT net -7.30, PF 0.00, expectancy -1.67%"
    ]
    assert created_for == ["kis"]
    assert latest_called_with == ["kis"]


def test_trading_validation_run_once_syncs_live_performance_first(
    monkeypatch,
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(settings, "live_performance_db_path", str(live_path))
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    calls: list[str] = []

    def fake_sync(current_settings) -> dict[str, object]:
        calls.append(str(current_settings.live_performance_db_path))
        LivePerformanceRepository(live_path).upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id="api-sync-b-1",
                symbol="BTCUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=104.0,
                qty=1.0,
                fees=0.2,
                slippage=0.1,
                filled=True,
                metadata={
                    "cost_model_status": "estimated_from_notional",
                    "cost_components": {"fees": 0.2, "slippage": 0.1},
                },
            ),
            source={
                "metadata": {
                    "cost_model_status": "estimated_from_notional",
                    "cost_components": {"fees": 0.2, "slippage": 0.1},
                }
            },
        )
        return {"status": "ok", "synced_blocks": {"binance": 1}}

    monkeypatch.setattr(
        main_module,
        "sync_live_performance_and_edges",
        fake_sync,
        raising=False,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/trading/validation/run-once?venue=binance",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert calls == [str(live_path)]
    assert payload["metrics"]["sample_count"] == 1
    assert payload["metrics"]["data_quality"]["missing_cost_count"] == 0
    active_revision = payload["metrics"]["active_revision_evidence"]
    assert active_revision["proxy_sample_used_for_metrics"] is True
    assert active_revision["validation_sample_role"] == "legacy_proxy_metrics_no_scale"


def test_trading_validation_run_once_api_uses_venue_specific_initial_equity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_kis_live_performance(live_path)
    monkeypatch.setattr(settings, "live_performance_db_path", str(live_path))
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    monkeypatch.setattr(settings, "kis_validation_initial_equity_krw", 4_000_000.0)

    with TestClient(app) as client:
        response = client.post(
            "/api/trading/validation/run-once?venue=kis",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metrics"]["drawdown_budget"]["initial_equity"] == 4_000_000.0


def test_ops_readiness_surfaces_trading_validation(monkeypatch, tmp_path: Path) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert "trading_validation" in payload
    assert payload["trading_validation"]["db_path"] == str(validation_path)


def test_ops_readiness_blocks_when_trading_validation_is_blocked(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    TradingValidationRepository(validation_path).save_run(
        {
            "status": "ok",
            "run_id": "validation-blocked",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 10,
                "warn_count": 4,
                "fail_count": 1,
                "missing_count": 4,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "red"
    assert "trading_validation_blocked" in payload["blockers"]


def test_ops_readiness_does_not_block_on_diagnostic_validation_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    TradingValidationRepository(validation_path).save_run(
        {
            "status": "ok",
            "run_id": "validation-diagnostic-failures",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {"id": "data_validation", "status": "pass"},
                {"id": "capacity_analysis", "status": "pass"},
                {"id": "mdd_limit", "status": "pass"},
                {
                    "id": "monte_carlo",
                    "label": "몬테카를로",
                    "status": "fail",
                    "evidence": "tail drawdown breached",
                    "action": "volatile lane budget 축소",
                },
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 3,
                "warn_count": 0,
                "fail_count": 16,
                "missing_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
                "hard_fail_count": 0,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert "trading_validation_blocked" not in payload["blockers"]
    assert "trading_validation_diagnostic_failures" not in payload["advisories"]
    assert "trading_validation_diagnostic_failures_binance" in payload["advisories"]
    assert "trading_validation_probe_binance" in payload["advisories"]
    assert any(
        row["id"] == "review_trading_validation_diagnostics"
        for row in payload["remediation_actions"]
    )
    diagnostic_detail = next(
        row
        for row in payload["advisory_details"]
        if row["signal"] == "trading_validation_diagnostic_failures_binance"
    )
    assert diagnostic_detail["top_bottlenecks"] == [
        {
            "venue": "binance",
            "id": "monte_carlo",
            "label": "몬테카를로",
            "status": "fail",
            "evidence": "tail drawdown breached",
            "action": "volatile lane budget 축소",
        }
    ]


def test_ops_readiness_surfaces_lane_authority_reductions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "trading_validation_max_age_sec",
        864000000,
        raising=False,
    )
    TradingValidationRepository(validation_path).save_run(
        {
            "status": "ok",
            "run_id": "validation-lane-authority",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "readiness": "normal",
                "pass_count": 11,
                "warn_count": 4,
                "fail_count": 0,
                "missing_count": 4,
                "core_fail_count": 0,
                "core_missing_count": 0,
                "hard_fail_count": 0,
            },
            "metrics": {
                "lane_scorecards": {
                    "version": "lane_scorecards_v1",
                    "status": "warn",
                    "scale_candidate_lanes": ["futures_short"],
                    "weak_lanes": [],
                    "validation_repair_weak_lanes": ["spot"],
                    "lane_actions": {
                        "spot": {
                            "grade": "qualified",
                            "action": "validation_repair_enforced_before_scale",
                            "authority_multiplier": 0.25,
                            "requires_waiting_entry": True,
                            "scale_blocked_by_validation_repair": True,
                            "scale_blocked_by_cost_evidence": True,
                            "scale_blocked_by_verified_edge_samples": True,
                            "cost_verified_alpha_count": 4,
                            "cost_unverified_alpha_count": 8,
                            "validation_repair_enforced_count": 4,
                            "validation_repair_scale_up_blocked_count": 4,
                            "validation_repair_requirements": [
                                "respect_validation_repair_enforcement_until_repair_passes",
                                "keep_probe_or_waiting_entry_when_repair_blocks_scale_up",
                            ],
                        }
                    },
                }
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert "trading_validation_lane_authority_reduced" not in payload["advisories"]
    assert "trading_validation_lane_authority_reduced_binance" in payload["advisories"]
    assert (
        "trading_validation_lane_authority_reduced_binance"
        in payload["advisories"]
    )
    aggregate = payload["trading_validation"]["lane_authority_summary"]
    venue_summary = payload["trading_validation"]["venues"]["binance"][
        "lane_authority_summary"
    ]
    assert aggregate["reduced_lane_count"] == 1
    assert venue_summary["reduced_lanes"][0]["lane"] == "spot"
    assert venue_summary["reduced_lanes"][0]["authority_multiplier"] == 0.25
    assert "validation_repair" in venue_summary["reduced_lanes"][0]["reasons"]
    assert "cost_evidence" in venue_summary["reduced_lanes"][0]["reasons"]
    assert "verified_edge_samples" in venue_summary["reduced_lanes"][0]["reasons"]
    assert venue_summary["reduced_lanes"][0]["cost_verified_alpha_count"] == 4
    assert venue_summary["reduced_lanes"][0]["cost_unverified_alpha_count"] == 8
    assert any(
        row["id"] == "review_lane_authority_reductions"
        for row in payload["remediation_actions"]
    )


def test_ops_readiness_warns_when_trading_validation_is_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    TradingValidationRepository(validation_path).save_run(
        {
            "status": "ok",
            "run_id": "validation-probe",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "readiness": "probe",
                "pass_count": 4,
                "warn_count": 5,
                "fail_count": 0,
                "missing_count": 10,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert "trading_validation_probe" not in payload["advisories"]
    assert "trading_validation_probe_binance" in payload["advisories"]


def test_ops_readiness_warns_when_trading_validation_is_stale(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    monkeypatch.setattr(settings, "trading_validation_max_age_sec", 60, raising=False)
    TradingValidationRepository(validation_path).save_run(
        {
            "status": "ok",
            "run_id": "validation-stale",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 15,
                "warn_count": 2,
                "fail_count": 0,
                "missing_count": 2,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    binance = payload["trading_validation"]["venues"]["binance"]
    assert binance["stale"] is True
    assert binance["max_age_sec"] == 60
    assert binance["age_sec"] > 60
    assert "trading_validation_stale_binance" in payload["warnings"]
    actions = payload["remediation_actions"]
    assert actions[0]["id"] == "refresh_trading_validation"
    assert actions[0]["label"] == "19개 검증 즉시 재실행"
    assert actions[0]["endpoint"] == "/api/trading/validation/run-once"
    assert "trading_validation_stale_binance" in actions[0]["signals"]


def test_ops_readiness_blocks_when_scale_ready_validation_is_incomplete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "trading_validation_max_age_sec",
        864000000,
        raising=False,
    )
    TradingValidationRepository(validation_path).save_run(
        {
            "status": "ok",
            "run_id": "validation-incomplete-scale-ready",
            "venue": "kis",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 3,
            "disciplines": [
                {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                {
                    "id": "walk_forward_analysis",
                    "label": "Walk Forward Analysis",
                    "status": "pass",
                },
                {
                    "id": "monte_carlo",
                    "label": "몬테카를로 시뮬레이션",
                    "status": "pass",
                },
            ],
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 3,
                "warn_count": 0,
                "fail_count": 0,
                "missing_count": 0,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    kis = payload["trading_validation"]["venues"]["kis"]
    assert payload["status"] == "red"
    assert kis["payload"]["discipline_count"] == 3
    assert "trading_validation_incomplete" in payload["blockers"]
    assert "trading_validation_incomplete_kis" in payload["blockers"]


def test_ops_readiness_aggregates_trading_validation_by_venue(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    repo = TradingValidationRepository(validation_path)
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-binance-blocked",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 9,
                "warn_count": 5,
                "fail_count": 1,
                "missing_count": 4,
            },
        }
    )
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-kis-scale-ready",
            "venue": "kis",
            "scope": "live",
            "computed_at": "2026-06-01T00:05:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 15,
                "warn_count": 2,
                "fail_count": 0,
                "missing_count": 2,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "red"
    assert "trading_validation_blocked_binance" in payload["blockers"]
    actions = payload["remediation_actions"]
    assert any(row["id"] == "review_trading_validation_failures" for row in actions)
    assert any("trading_validation_blocked_binance" in row["signals"] for row in actions)
    assert payload["trading_validation"]["venues"]["binance"]["summary"]["readiness"] == (
        "blocked_by_validation"
    )
    assert payload["trading_validation"]["venues"]["kis"]["summary"]["readiness"] == (
        "scale_ready"
    )


def test_ops_readiness_top_level_validation_summary_is_venue_aggregate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "trading_validation_max_age_sec",
        864000000,
        raising=False,
    )
    repo = TradingValidationRepository(validation_path)
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-binance-blocked",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {
                    "id": "risk_of_ruin",
                    "label": "파산확률",
                    "status": "fail",
                    "evidence": "ruin risk too high",
                    "action": "risk off",
                }
            ],
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 8,
                "warn_count": 4,
                "fail_count": 1,
                "missing_count": 6,
            },
        }
    )
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-kis-scale-ready",
            "venue": "kis",
            "scope": "live",
            "computed_at": "2026-06-01T00:05:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "readiness": "scale_ready",
                "pass_count": 16,
                "warn_count": 2,
                "fail_count": 0,
                "missing_count": 1,
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    validation = response.json()["trading_validation"]
    assert validation["summary"]["readiness"] == "blocked_by_validation"
    assert validation["summary"]["fail_count"] == 1
    assert validation["summary"]["pass_count"] == 24
    assert validation["summary"]["warn_count"] == 6
    assert validation["summary"]["missing_count"] == 7
    assert validation["discipline_count"] == 38


def test_ops_readiness_summarizes_trading_validation_bottlenecks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    monkeypatch.setattr(
        settings,
        "trading_validation_db_path",
        str(validation_path),
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "trading_validation_max_age_sec",
        864000000,
        raising=False,
    )
    repo = TradingValidationRepository(validation_path)
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-kis-bottleneck",
            "venue": "kis",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {
                    "id": "monte_carlo",
                    "label": "몬테카를로 시뮬레이션",
                    "status": "fail",
                    "evidence": "tail drawdown breached",
                    "action": "volatile lane budget 축소",
                },
                {
                    "id": "profit_factor",
                    "label": "수익팩터",
                    "status": "warn",
                    "evidence": "PF 1.20",
                    "action": "익절/손절 구조 재검증",
                },
            ],
            "summary": {
                "readiness": "blocked_by_validation",
                "pass_count": 8,
                "warn_count": 1,
                "fail_count": 1,
                "missing_count": 9,
            },
            "remediation_plan": {
                "status": "blocked",
                "primary_next_action": "Monte Carlo tail risk부터 낮추기",
            },
        }
    )
    repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-binance-bottleneck",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:05:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {
                    "id": "cost_simulation",
                    "label": "거래비용 시뮬레이션",
                    "status": "warn",
                    "evidence": "cost drag high",
                    "action": "스프레드/수수료 포함 재계산",
                }
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 10,
                "warn_count": 1,
                "fail_count": 0,
                "missing_count": 8,
            },
            "remediation_plan": {
                "status": "watch",
                "primary_next_action": "비용 모델 보정",
            },
        }
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ops/readiness",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    validation = response.json()["trading_validation"]
    assert validation["primary_next_actions"] == [
        {
            "venue": "kis",
            "status": "blocked",
            "action": "Monte Carlo tail risk부터 낮추기",
        },
        {
            "venue": "binance",
            "status": "watch",
            "action": "비용 모델 보정",
        },
    ]
    assert validation["bottlenecks"][0] == {
        "venue": "kis",
        "id": "monte_carlo",
        "label": "몬테카를로 시뮬레이션",
        "status": "fail",
        "evidence": "tail drawdown breached",
        "action": "volatile lane budget 축소",
    }
    assert validation["bottlenecks"][1]["venue"] == "binance"
    assert validation["bottlenecks"][1]["id"] == "cost_simulation"
    assert validation["bottlenecks"][2]["venue"] == "kis"
    assert validation["bottlenecks"][2]["id"] == "profit_factor"


def test_trading_validation_bottlenecks_prioritize_survival_and_execution() -> None:
    bottlenecks = summarize_trading_validation_bottlenecks(
        {
            "kis": {
                "disciplines": [
                    {"id": "calmar_ratio", "label": "Calmar", "status": "fail"},
                    {
                        "id": "cost_simulation",
                        "label": "거래비용",
                        "status": "fail",
                    },
                    {"id": "monte_carlo", "label": "몬테카를로", "status": "fail"},
                    {"id": "kelly_sizing", "label": "켈리", "status": "fail"},
                    {"id": "profit_factor", "label": "수익팩터", "status": "fail"},
                    {"id": "risk_of_ruin", "label": "파산확률", "status": "fail"},
                ]
            }
        },
        limit=6,
    )

    assert [row["id"] for row in bottlenecks] == [
        "cost_simulation",
        "monte_carlo",
        "kelly_sizing",
        "risk_of_ruin",
        "profit_factor",
        "calmar_ratio",
    ]


def test_trading_validation_bottlenecks_sort_globally_across_venues() -> None:
    bottlenecks = summarize_trading_validation_bottlenecks(
        {
            "kis": {
                "disciplines": [
                    {"id": "profit_factor", "label": "수익팩터", "status": "warn"},
                    {"id": "cost_simulation", "label": "비용", "status": "warn"},
                ]
            },
            "binance": {
                "disciplines": [
                    {"id": "risk_of_ruin", "label": "파산확률", "status": "fail"},
                    {"id": "monte_carlo", "label": "몬테카를로", "status": "fail"},
                ]
            },
        },
        limit=4,
    )

    assert [(row["venue"], row["id"], row["status"]) for row in bottlenecks] == [
        ("binance", "monte_carlo", "fail"),
        ("binance", "risk_of_ruin", "fail"),
        ("kis", "cost_simulation", "warn"),
        ("kis", "profit_factor", "warn"),
    ]
