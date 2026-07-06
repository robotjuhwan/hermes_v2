from __future__ import annotations

from pathlib import Path


def test_trading_validation_bottleneck_sorting_lives_in_payload_module() -> None:
    from tradecraft.api.trading_validation_payloads import (
        summarize_trading_validation_bottlenecks,
    )

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


def test_trading_validation_lane_authority_summary_lives_in_payload_module() -> None:
    from tradecraft.api.trading_validation_payloads import (
        summarize_trading_validation_lane_authority,
    )

    summary = summarize_trading_validation_lane_authority(
        {
            "lane_scorecards": {
                "scale_candidate_lanes": ["futures_long"],
                "weak_lanes": ["spot"],
                "lane_actions": {
                    "spot": {
                        "grade": "qualified",
                        "action": "validation_repair_enforced_before_scale",
                        "authority_multiplier": 0.25,
                        "requires_waiting_entry": True,
                        "scale_blocked_by_validation_repair": True,
                        "scale_blocked_by_cost_evidence": True,
                    }
                },
            }
        },
        venue="binance",
    )

    assert summary["status"] == "warn"
    assert summary["reduced_lane_count"] == 1
    assert summary["scale_blocked_lane_count"] == 1
    assert summary["probe_lane_count"] == 1
    assert summary["probe_lane_names"] == ["spot"]
    assert summary["scale_blocked_lanes"] == ["spot"]
    assert summary["execution_posture"] == "probe_allowed_scale_blocked"
    assert summary["reduced_lanes"][0]["lane"] == "spot"
    assert summary["reduced_lanes"][0]["authority_multiplier"] == 0.25
    assert "validation_repair" in summary["reduced_lanes"][0]["reasons"]
    assert "cost_evidence" in summary["reduced_lanes"][0]["reasons"]


def test_trading_validation_payload_promotes_diagnostic_status() -> None:
    from tradecraft.api.trading_validation_payloads import (
        aggregate_trading_validation_venue_payloads,
        promote_trading_validation_payload_fields,
    )

    venue_payload = promote_trading_validation_payload_fields(
        {
            "status": "ok",
            "computed_at": "2026-06-29T09:00:00+00:00",
            "summary": {
                "readiness": "normal",
                "diagnostic_status": "risk_repair",
                "pass_count": 6,
                "warn_count": 0,
                "fail_count": 13,
                "missing_count": 0,
                "hard_fail_count": 0,
                "hard_missing_count": 0,
                "core_fail_count": 0,
                "core_missing_count": 0,
            },
            "payload": {
                "summary": {
                    "readiness": "normal",
                    "diagnostic_status": "risk_repair",
                    "pass_count": 6,
                    "warn_count": 0,
                    "fail_count": 13,
                    "missing_count": 0,
                    "hard_fail_count": 0,
                    "hard_missing_count": 0,
                    "core_fail_count": 0,
                    "core_missing_count": 0,
                },
                "disciplines": [],
            },
        },
        expected_discipline_count=19,
    )

    aggregate = aggregate_trading_validation_venue_payloads(
        {"binance": venue_payload},
        db_path=".runtime/trading_validation.db",
        live_performance_db_path=".runtime/live_performance.db",
        max_age_sec=1800,
        expected_discipline_count=19,
    )

    assert venue_payload["readiness"] == "probe"
    assert venue_payload["diagnostic_status"] == "risk_repair"
    assert aggregate["summary"]["readiness"] == "probe"
    assert aggregate["summary"]["diagnostic_status"] == "risk_repair"
    assert aggregate["diagnostic_status"] == "risk_repair"


def test_trading_validation_payload_promotes_single_venue_bottlenecks_and_actions() -> None:
    from tradecraft.api.trading_validation_payloads import (
        promote_trading_validation_payload_fields,
    )

    payload = promote_trading_validation_payload_fields(
        {
            "status": "ok",
            "venue": "binance",
            "computed_at": "2026-06-29T09:00:00+00:00",
            "summary": {
                "readiness": "normal",
                "pass_count": 12,
                "warn_count": 1,
                "fail_count": 1,
                "missing_count": 0,
            },
            "payload": {
                "venue": "binance",
                "summary": {
                    "readiness": "normal",
                    "pass_count": 12,
                    "warn_count": 1,
                    "fail_count": 1,
                    "missing_count": 0,
                },
                "disciplines": [
                    {
                        "id": "profit_factor",
                        "label": "수익팩터",
                        "status": "warn",
                        "evidence": "profit factor weak",
                        "action": "lane별 기대값 재검증",
                    },
                    {
                        "id": "cost_simulation",
                        "label": "거래비용 시뮬레이션",
                        "status": "fail",
                        "evidence": "cost drag too high",
                        "action": "스프레드와 슬리피지를 포함해 재계산",
                    },
                ],
                "remediation_plan": {
                    "status": "risk_repair",
                    "primary_next_action": "비용 드래그가 낮은 lane만 남기기",
                },
            },
        },
        expected_discipline_count=19,
    )

    assert payload["top_bottlenecks"][0] == {
        "venue": "binance",
        "id": "cost_simulation",
        "label": "거래비용 시뮬레이션",
        "status": "fail",
        "evidence": "cost drag too high",
        "action": "스프레드와 슬리피지를 포함해 재계산",
    }
    assert payload["primary_next_actions"] == [
        {
            "venue": "binance",
            "status": "risk_repair",
            "action": "비용 드래그가 낮은 lane만 남기기",
        }
    ]


def test_main_no_longer_owns_trading_validation_payload_helpers() -> None:
    source = Path("src/tradecraft/main.py").read_text(encoding="utf-8")

    assert "def _promote_trading_validation_payload_fields" not in source
    assert "def _summarize_trading_validation_lane_authority" not in source
    assert "def _aggregate_trading_validation_lane_authority" not in source
    assert "def _summarize_trading_validation_bottlenecks" not in source
    assert "def _summarize_trading_validation_next_actions" not in source
