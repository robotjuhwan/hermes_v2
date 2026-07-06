from __future__ import annotations

import pytest

from tradecraft.services.binance_live_authority_gate import (
    active_revision_budget_multiplier,
    active_revision_waiting_entry_reason,
    lane_authority_evidence_fields,
    live_authority_create_gate,
    live_authority_validation_gate,
)


def test_live_authority_validation_gate_normalizes_core_fields() -> None:
    gate = live_authority_validation_gate(
        {
            "validation_gate": {
                "status": " VALIDATION_NORMAL ",
                "readiness": " Normal ",
                "reason": "needs probe",
                "risk_governor_action": " RISK_OFF ",
            }
        }
    )

    assert gate == {
        "status": "validation_normal",
        "readiness": "normal",
        "reason": "needs probe",
        "risk_governor_action": "risk_off",
    }


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (
            "no_active_revision_samples_with_proxy",
            "active_revision_evidence:no_active_revision_samples_with_proxy",
        ),
        (
            "active_revision_samples_pending_close",
            "active_revision_evidence:active_revision_samples_pending_close",
        ),
    ],
)
def test_active_revision_waiting_entry_reason_blocks_immediate_entries(
    status: str,
    expected_reason: str,
) -> None:
    reason = active_revision_waiting_entry_reason(
        {
            "active_revision_evidence": {
                "status": status,
                "effective_sample_count": 0,
                "min_samples_to_scale": 20,
                "scale_up_allowed": False,
            }
        }
    )

    assert reason == expected_reason
    assert active_revision_budget_multiplier(
        {"active_revision_evidence": {"status": status}}
    ) == pytest.approx(0.25)


def test_active_revision_budget_multiplier_allows_half_budget_for_sample_building() -> None:
    assert active_revision_budget_multiplier(
        {
            "active_revision_evidence": {
                "status": "active_revision_sample_building",
                "active_sample_count": 5,
                "effective_sample_count": 5,
                "min_samples_to_scale": 10,
                "scale_up_allowed": False,
            }
        }
    ) == pytest.approx(0.5)


def test_live_authority_create_gate_requires_waiting_entry_for_validation_pressure() -> None:
    result = live_authority_create_gate(
        {
            "status": "ok",
            "max_budget_multiplier": 1.0,
            "validation_gate": {
                "status": "clear",
                "validation_pressure": {
                    "entry_posture": "patient_waiting_entry",
                    "severity": "remediation_waiting_probe",
                    "sizing_posture": "reduced_probe_only",
                },
            },
        },
        waiting_entry=False,
    )

    assert result["ok"] is False
    assert result["reason"] == (
        "live_authority_requires_waiting_entry:"
        "validation_pressure:patient_waiting_entry"
    )
    assert result["gate"]["validation_pressure_entry_posture"] == (
        "patient_waiting_entry"
    )
    assert result["gate"]["validation_pressure_sizing_posture"] == "reduced_probe_only"


def test_live_authority_create_gate_blocks_hard_validation_status_and_zero_budget() -> None:
    hard_gate = live_authority_create_gate(
        {
            "status": "ok",
            "max_budget_multiplier": 1.0,
            "validation_gate": {"status": "validation_stale"},
        },
        waiting_entry=True,
    )
    zero_budget = live_authority_create_gate(
        {
            "status": "ok",
            "max_budget_multiplier": 0.0,
            "validation_gate": {"status": "clear"},
        },
        waiting_entry=True,
    )

    assert hard_gate["ok"] is False
    assert hard_gate["reason"] == "live_authority_validation_gate:validation_stale"
    assert zero_budget["ok"] is False
    assert zero_budget["reason"] == "live_authority_budget_zero"


def test_lane_authority_evidence_fields_compacts_detail_and_passport() -> None:
    evidence = lane_authority_evidence_fields(
        {
            "cost_evidence_status": " weak ",
            "entry_repair_targets": [
                "spread too wide",
                "late chase",
                "",
                "oversized wick risk",
            ],
            "cost_precision_counts": {
                "fee": 3,
                "spread": 2.1256789,
                "ignored": ["too big"],
            },
            "cost_hybrid_alpha_net_pnl": "12.3456789",
            "scale_blocked_by_cost_precision": True,
        },
        {
            "budget_status": " capped ",
            "risk_of_ruin_pct": "7.25%",
            "reasons": ["sample short", "drawdown guard"],
        },
    )

    assert evidence["cost_evidence_status"] == "weak"
    assert evidence["risk_budget_passport_status"] == "capped"
    assert evidence["entry_repair_targets"] == [
        "spread too wide",
        "late chase",
        "oversized wick risk",
    ]
    assert evidence["cost_precision_counts"] == {
        "fee": 3,
        "spread": pytest.approx(2.125679),
    }
    assert evidence["cost_hybrid_alpha_net_pnl"] == pytest.approx(12.345679)
    assert evidence["risk_of_ruin_pct"] == pytest.approx(7.25)
    assert evidence["scale_blocked_by_cost_precision"] is True
