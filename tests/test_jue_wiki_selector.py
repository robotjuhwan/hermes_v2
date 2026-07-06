from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_application import JueWikiApplicationService
from tradecraft.services.jue_wiki_selector import (
    JueWikiSelectionRequest,
    JueWikiSelector,
    build_jue_wiki_decision_adjustment_audit_contract_for_prompt,
    build_jue_wiki_decision_adjustments_for_prompt,
    build_jue_wiki_repair_contract_for_prompt,
    build_jue_wiki_trust_profile_for_prompt,
    compact_jue_wiki_application_coverage_for_prompt,
    compact_jue_wiki_repair_loop_effectiveness_for_prompt,
    resolve_jue_wiki_prompt_mode,
)


def _service(tmp_path: Path, *, page_max_chars: int = 12000) -> JueWikiService:
    return JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            page_max_chars=page_max_chars,
        )
    )


def _write_page(
    service: JueWikiService,
    *,
    scope: str,
    symbol: str,
    title: str,
    source_refs: list[dict[str, object]] | None = None,
    confidence: float = 0.8,
    freshness: str = "fresh",
    body: str = "compact selector note",
) -> str:
    result = service.write_page(
        scope=scope,
        page_type="symbol",
        key=symbol,
        title=title,
        symbols=[symbol],
        content_sections={
            "Current Stance": body,
            "Durable Facts": "durable facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": f"{title} selector summary",
        },
        source_refs=source_refs or [],
        confidence=confidence,
        freshness=freshness,
    )
    return str(result["page_id"])


def test_resolve_jue_wiki_prompt_mode_keeps_configured_mode_for_weak_recommendation() -> None:
    resolution = resolve_jue_wiki_prompt_mode(
        "assist",
        {
            "recommendation_id": "wiki-mode:weak-primary",
            "decision_scope": "kis",
            "recommended_mode": "primary",
            "sample_count": 3,
            "confidence": 0.2,
            "reasons": ["samples:3"],
        },
    )

    assert resolution["prompt_mode"] == "assist"
    assert resolution["configured_prompt_mode"] == "assist"
    assert resolution["prompt_mode_policy"]["source"] == "configured"


def test_resolve_jue_wiki_prompt_mode_applies_protective_observe_after_cleanup() -> None:
    resolution = resolve_jue_wiki_prompt_mode(
        "primary",
        {
            "recommendation_id": "wiki-mode:cleanup-observe",
            "decision_scope": "kis",
            "recommended_mode": "observe",
            "sample_count": 0,
            "confidence": 0.65,
            "reasons": [
                "stale_effectiveness_removed:2",
                "no_attributable_metrics_after_cleanup",
            ],
        },
    )

    assert resolution["prompt_mode"] == "observe"
    assert resolution["configured_prompt_mode"] == "primary"
    assert resolution["prompt_mode_policy"]["source"] == "mode_recommendation"


def test_resolve_jue_wiki_prompt_mode_omits_missing_policy_metrics_but_keeps_zero() -> None:
    missing = resolve_jue_wiki_prompt_mode(
        "assist",
        {
            "recommendation_id": "wiki-mode:missing-metrics",
            "recommended_mode": "primary",
        },
    )

    assert "sample_count" not in missing["prompt_mode_policy"]
    assert "confidence" not in missing["prompt_mode_policy"]

    explicit_zero = resolve_jue_wiki_prompt_mode(
        "primary",
        {
            "recommendation_id": "wiki-mode:explicit-zero",
            "recommended_mode": "observe",
            "sample_count": 0,
            "confidence": 0.65,
            "reasons": ["prompt_mode_effectiveness:primary:degraded"],
        },
    )

    assert explicit_zero["prompt_mode"] == "observe"
    assert explicit_zero["prompt_mode_policy"]["sample_count"] == 0
    assert explicit_zero["prompt_mode_policy"]["confidence"] == 0.65


def test_build_jue_wiki_trust_profile_marks_demoted_primary_as_observation_only() -> None:
    profile = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "observe",
            "configured_prompt_mode": "primary",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:kis-observe",
                "recommended_mode": "observe",
                "sample_count": 42,
                "confidence": 0.76,
                "reasons": [
                    "prompt_mode_effectiveness:primary:degraded",
                    "primary_avg_return_pct:-0.6200",
                ],
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_observe_recommendation",
            },
        }
    )

    assert profile["authority"] == "observation_only"
    assert profile["trust_level"] == "low"
    assert profile["posture"] == "primary_demoted_after_underperformance"
    assert profile["recommended_mode"] == "observe"
    assert profile["configured_prompt_mode"] == "primary"
    assert profile["decision_use"].startswith("inspect selected wiki")


def test_build_jue_wiki_trust_profile_embeds_matching_authority_effectiveness() -> None:
    profile = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "assist",
            "configured_prompt_mode": "assist",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:kis-assist",
                "recommended_mode": "assist",
                "sample_count": 32,
                "confidence": 0.66,
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_assist_recommendation",
            },
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profiles": [
                    {
                        "authority": "primary_compiled_knowledge",
                        "status": "degraded",
                        "sample_count": 7,
                        "avg_return_pct": -0.8,
                    },
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 12,
                        "win_rate": 0.58,
                        "avg_return_pct": 0.42,
                        "helpful_score": 5.2,
                        "confidence": 0.8,
                        "reasons": ["samples:12", "avg_return_pct:0.4200"],
                        "usage_contract_counts": {
                            "risk_posture": {"supporting_evidence": 12},
                            "allowed_uses": {"candidate_ranking": 8},
                        },
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "sample_count": 8,
                                "status": "degraded",
                                "avg_return_pct": -0.35,
                                "confidence": 1.0,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "sample_count": 6,
                                "status": "active",
                                "avg_return_pct": 0.72,
                                "confidence": 1.0,
                            },
                        ],
                        "decision_adjustment_metrics": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "repair_probe",
                                "reason": "current_risk_posture_degraded",
                                "sample_count": 6,
                                "status": "active",
                                "avg_return_pct": 0.72,
                                "confidence": 1.0,
                            }
                        ],
                        "decision_adjustment_audit_metrics": [
                            {
                                "action": (
                                    "audit_preferred_risk_posture_before_shift"
                                ),
                                "target_risk_posture": "repair_probe",
                                "sample_count": 4,
                                "status": "active",
                                "avg_return_pct": 0.5,
                                "confidence": 0.8,
                            }
                        ],
                    },
                ],
            },
        }
    )

    assert profile["authority"] == "supporting_evidence"
    assert profile["authority_effectiveness"] == {
        "status": "active",
        "sample_count": 12,
        "win_rate": 0.58,
        "avg_return_pct": 0.42,
        "helpful_score": 5.2,
        "confidence": 0.8,
        "reasons": ["samples:12", "avg_return_pct:0.4200"],
        "usage_contract_counts": {
            "risk_posture": {"supporting_evidence": 12},
            "allowed_uses": {"candidate_ranking": 8},
        },
        "risk_posture_metrics": [
            {
                "risk_posture": "supporting_evidence",
                "sample_count": 8,
                "status": "degraded",
                "avg_return_pct": -0.35,
                "confidence": 1.0,
            },
            {
                "risk_posture": "repair_probe",
                "sample_count": 6,
                "status": "active",
                "avg_return_pct": 0.72,
                "confidence": 1.0,
            },
        ],
        "decision_adjustment_metrics": [
            {
                "action": "shift_to_preferred_risk_posture",
                "target_risk_posture": "repair_probe",
                "reason": "current_risk_posture_degraded",
                "sample_count": 6,
                "status": "active",
                "avg_return_pct": 0.72,
                "confidence": 1.0,
            }
        ],
        "decision_adjustment_audit_metrics": [
            {
                "action": "audit_preferred_risk_posture_before_shift",
                "target_risk_posture": "repair_probe",
                "sample_count": 4,
                "status": "active",
                "avg_return_pct": 0.5,
                "confidence": 0.8,
            }
        ],
    }
    assert profile["usage_contract"]["risk_posture_guidance"] == {
        "current_risk_posture": "supporting_evidence",
        "current_status": "degraded",
        "preferred_risk_postures": ["repair_probe"],
        "degraded_risk_postures": ["supporting_evidence"],
        "recommended_allowed_uses": [
            "repair_candidate_design",
            "small_probe_block",
            "waiting_block",
            "candidate_level_reject",
        ],
        "deprioritized_allowed_uses": [
            "candidate_ranking",
            "target_stop_context",
            "risk_note_context",
            "follow_up_research",
        ],
        "decision_adjustment": {
            "action": "shift_to_preferred_risk_posture",
            "target_risk_posture": "repair_probe",
            "reason": "current_risk_posture_degraded",
        },
        "decision_adjustment_effectiveness": {
            "action": "shift_to_preferred_risk_posture",
            "target_risk_posture": "repair_probe",
            "reason": "current_risk_posture_degraded",
            "sample_count": 6,
            "status": "active",
            "avg_return_pct": 0.72,
            "confidence": 1.0,
        },
        "guidance": "prefer active risk postures and reduce degraded postures unless live cross-checks override",
    }


def test_build_jue_wiki_trust_profile_keeps_recommendation_reasons_unique() -> None:
    profile = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "primary",
            "configured_prompt_mode": "primary",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:kis-primary",
                "recommended_mode": "primary",
                "sample_count": 48,
                "confidence": 0.78,
                "reasons": [
                    "prompt_mode_effectiveness:primary:active",
                    "prompt_mode_effectiveness:primary:active",
                    "primary_avg_return_pct:0.8400",
                    "primary_avg_return_pct:0.8400",
                ],
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_primary_recommendation",
            },
        }
    )

    assert profile["reasons"] == [
        "prompt_mode_effectiveness:primary:active",
        "primary_avg_return_pct:0.8400",
    ]


def test_build_jue_wiki_trust_profile_omits_missing_recommendation_metrics_but_keeps_zero() -> None:
    missing = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "observe",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:kis-observe",
                "recommended_mode": "observe",
            },
        }
    )

    assert "sample_count" not in missing
    assert "confidence" not in missing

    explicit_zero = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "observe",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:kis-observe",
                "recommended_mode": "observe",
                "sample_count": 0,
                "confidence": 0.0,
            },
        }
    )

    assert explicit_zero["sample_count"] == 0
    assert explicit_zero["confidence"] == 0.0


def test_build_jue_wiki_trust_profile_audits_degraded_decision_adjustment() -> None:
    profile = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "assist",
            "configured_prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 14,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "sample_count": 8,
                                "status": "degraded",
                                "avg_return_pct": -0.45,
                                "confidence": 1.0,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "sample_count": 6,
                                "status": "active",
                                "avg_return_pct": 0.64,
                                "confidence": 1.0,
                            },
                        ],
                        "decision_adjustment_metrics": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "repair_probe",
                                "reason": "current_risk_posture_degraded",
                                "sample_count": 5,
                                "status": "degraded",
                                "avg_return_pct": -0.7,
                                "confidence": 1.0,
                                "evidence_grade_counts": {
                                    "negative": 3,
                                    "positive": 2,
                                },
                                "evidence_grade_instruction_counts": {
                                    "audit_or_repair_probe_only": 3,
                                    "usable_with_live_cross_check": 2,
                                },
                                "evidence_grade_performance": [
                                    {
                                        "status": "negative",
                                        "instruction": "audit_or_repair_probe_only",
                                        "basis": "decision_adjustment_effectiveness",
                                        "sample_count": 3,
                                        "win_rate": 0.0,
                                        "avg_return_pct": -1.2,
                                    },
                                    {
                                        "status": "positive",
                                        "instruction": "usable_with_live_cross_check",
                                        "basis": "decision_adjustment_effectiveness",
                                        "sample_count": 2,
                                        "win_rate": 1.0,
                                        "avg_return_pct": 0.8,
                                    },
                                ],
                            }
                        ],
                        "decision_adjustment_audit_metrics": [
                            {
                                "action": (
                                    "audit_preferred_risk_posture_before_shift"
                                ),
                                "target_risk_posture": "repair_probe",
                                "sample_count": 4,
                                "status": "active",
                                "avg_return_pct": 0.5,
                                "confidence": 0.8,
                            }
                        ],
                    },
                ],
            },
        }
    )

    guidance = profile["usage_contract"]["risk_posture_guidance"]
    assert guidance["decision_adjustment"] == {
        "action": "audit_preferred_risk_posture_before_shift",
        "target_risk_posture": "repair_probe",
        "reason": "prior_decision_adjustment_degraded",
    }
    assert guidance["decision_adjustment_effectiveness"] == {
        "action": "shift_to_preferred_risk_posture",
        "target_risk_posture": "repair_probe",
        "reason": "current_risk_posture_degraded",
        "sample_count": 5,
        "status": "degraded",
        "avg_return_pct": -0.7,
        "confidence": 1.0,
        "evidence_grade_counts": {
            "negative": 3,
            "positive": 2,
        },
        "evidence_grade_instruction_counts": {
            "audit_or_repair_probe_only": 3,
            "usable_with_live_cross_check": 2,
        },
        "evidence_grade_performance": [
            {
                "status": "negative",
                "instruction": "audit_or_repair_probe_only",
                "basis": "decision_adjustment_effectiveness",
                "sample_count": 3,
                "win_rate": 0.0,
                "avg_return_pct": -1.2,
            },
            {
                "status": "positive",
                "instruction": "usable_with_live_cross_check",
                "basis": "decision_adjustment_effectiveness",
                "sample_count": 2,
                "win_rate": 1.0,
                "avg_return_pct": 0.8,
            },
        ],
        "execution_hint": "cap_to_audit_or_repair_probe",
    }
    assert guidance["decision_adjustment_audit_effectiveness"] == {
        "action": "audit_preferred_risk_posture_before_shift",
        "target_risk_posture": "repair_probe",
        "sample_count": 4,
        "status": "active",
        "avg_return_pct": 0.5,
        "confidence": 0.8,
    }
    assert build_jue_wiki_decision_adjustments_for_prompt(profile) == [
        {
            "source": "usage_contract.risk_posture_guidance",
            "action": "audit_preferred_risk_posture_before_shift",
            "target_risk_posture": "repair_probe",
            "reason": "prior_decision_adjustment_degraded",
            "current_risk_posture": "supporting_evidence",
            "current_status": "degraded",
            "recommended_allowed_uses": [
                "repair_candidate_design",
                "small_probe_block",
                "waiting_block",
                "candidate_level_reject",
            ],
            "deprioritized_allowed_uses": [
                "candidate_ranking",
                "target_stop_context",
                "risk_note_context",
                "follow_up_research",
            ],
            "decision_adjustment_effectiveness": {
                "action": "shift_to_preferred_risk_posture",
                "target_risk_posture": "repair_probe",
                "reason": "current_risk_posture_degraded",
                "sample_count": 5,
                "status": "degraded",
                "avg_return_pct": -0.7,
                "confidence": 1.0,
                "evidence_grade_counts": {
                    "negative": 3,
                    "positive": 2,
                },
                "evidence_grade_instruction_counts": {
                    "audit_or_repair_probe_only": 3,
                    "usable_with_live_cross_check": 2,
                },
                "evidence_grade_performance": [
                    {
                        "status": "negative",
                        "instruction": "audit_or_repair_probe_only",
                        "basis": "decision_adjustment_effectiveness",
                        "sample_count": 3,
                        "win_rate": 0.0,
                        "avg_return_pct": -1.2,
                    },
                    {
                        "status": "positive",
                        "instruction": "usable_with_live_cross_check",
                        "basis": "decision_adjustment_effectiveness",
                        "sample_count": 2,
                        "win_rate": 1.0,
                        "avg_return_pct": 0.8,
                    },
                ],
                "execution_hint": "cap_to_audit_or_repair_probe",
            },
            "decision_adjustment_audit_effectiveness": {
                "action": "audit_preferred_risk_posture_before_shift",
                "target_risk_posture": "repair_probe",
                "sample_count": 4,
                "status": "active",
                "avg_return_pct": 0.5,
                "confidence": 0.8,
            },
            "evidence_grade": {
                "status": "positive",
                "basis": "decision_adjustment_audit_effectiveness",
                "sample_count": 4,
                "avg_return_pct": 0.5,
                "confidence": 0.8,
                "instruction": "usable_with_live_cross_check",
            },
        }
    ]


def test_build_jue_wiki_decision_adjustments_marks_degraded_evidence_as_audit_only() -> None:
    profile = {
        "usage_contract": {
            "risk_posture_guidance": {
                "current_risk_posture": "supporting_evidence",
                "current_status": "degraded",
                "recommended_allowed_uses": ["repair_candidate_design"],
                "decision_adjustment": {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                },
                "decision_adjustment_effectiveness": {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                    "sample_count": 5,
                    "status": "degraded",
                    "avg_return_pct": -0.7,
                    "confidence": 0.9,
                },
            }
        }
    }

    adjustment = build_jue_wiki_decision_adjustments_for_prompt(profile)[0]

    assert adjustment["evidence_grade"] == {
        "status": "negative",
        "basis": "decision_adjustment_effectiveness",
        "sample_count": 5,
        "avg_return_pct": -0.7,
        "confidence": 0.9,
        "instruction": "audit_or_repair_probe_only",
    }


def test_build_jue_wiki_trust_profile_flags_degraded_decision_adjustment_audit() -> None:
    profile = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "assist",
            "configured_prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 14,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "sample_count": 8,
                                "status": "degraded",
                                "avg_return_pct": -0.45,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "sample_count": 6,
                                "status": "active",
                                "avg_return_pct": 0.64,
                            },
                        ],
                        "decision_adjustment_metrics": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "repair_probe",
                                "reason": "current_risk_posture_degraded",
                                "sample_count": 5,
                                "status": "degraded",
                                "avg_return_pct": -0.7,
                            }
                        ],
                        "decision_adjustment_audit_metrics": [
                            {
                                "action": (
                                    "audit_preferred_risk_posture_before_shift"
                                ),
                                "target_risk_posture": "repair_probe",
                                "sample_count": 4,
                                "status": "degraded",
                                "avg_return_pct": -0.8,
                            }
                        ],
                    },
                ],
            },
        }
    )

    guidance = profile["usage_contract"]["risk_posture_guidance"]
    assert guidance["decision_adjustment_audit_policy"] == {
        "action": "repair_audit_contract_before_reuse",
        "reason": "prior_audit_contract_degraded",
        "target_risk_posture": "repair_probe",
        "hard_blocker": False,
    }
    assert build_jue_wiki_decision_adjustments_for_prompt(profile)[0][
        "decision_adjustment_audit_policy"
    ] == {
        "action": "repair_audit_contract_before_reuse",
        "reason": "prior_audit_contract_degraded",
        "target_risk_posture": "repair_probe",
        "hard_blocker": False,
    }


def test_build_decision_adjustment_audit_contract_promotes_audit_repair_policy() -> None:
    contract = build_jue_wiki_decision_adjustment_audit_contract_for_prompt(
        {
            "decision_adjustments": [
                {
                    "source": "usage_contract.risk_posture_guidance",
                    "action": "audit_preferred_risk_posture_before_shift",
                    "target_risk_posture": "repair_probe",
                    "reason": "prior_decision_adjustment_degraded",
                    "decision_adjustment_audit_effectiveness": {
                        "action": "audit_preferred_risk_posture_before_shift",
                        "target_risk_posture": "repair_probe",
                        "sample_count": 4,
                        "status": "degraded",
                        "avg_return_pct": -0.8,
                    },
                    "decision_adjustment_audit_policy": {
                        "action": "repair_audit_contract_before_reuse",
                        "reason": "prior_audit_contract_degraded",
                        "target_risk_posture": "repair_probe",
                        "hard_blocker": False,
                    },
                }
            ]
        }
    )

    assert contract["status"] == "repair_required"
    assert contract["audit_policies"] == [
        {
            "action": "repair_audit_contract_before_reuse",
            "reason": "prior_audit_contract_degraded",
            "target_risk_posture": "repair_probe",
            "hard_blocker": False,
        }
    ]
    assert contract["audit_effectiveness"] == [
        {
            "action": "audit_preferred_risk_posture_before_shift",
            "target_risk_posture": "repair_probe",
            "sample_count": 4,
            "status": "degraded",
            "avg_return_pct": -0.8,
        }
    ]


def test_build_jue_wiki_trust_profile_adds_usage_contract_for_degraded_authority() -> None:
    profile = build_jue_wiki_trust_profile_for_prompt(
        {
            "prompt_mode": "primary",
            "configured_prompt_mode": "primary",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:kis-primary",
                "recommended_mode": "primary",
                "sample_count": 52,
                "confidence": 0.81,
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_primary_recommendation",
            },
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profiles": [
                    {
                        "authority": "primary_compiled_knowledge",
                        "status": "degraded",
                        "sample_count": 11,
                        "avg_return_pct": -0.73,
                    }
                ],
            },
        }
    )

    assert profile["usage_contract"] == {
        "version": "jue_wiki_usage_contract_v1",
        "decision_role": "primary_compiled_knowledge",
        "effectiveness_status": "degraded",
        "risk_posture": "repair_probe",
        "standalone_trade_authority": False,
        "requires_live_cross_check": True,
        "hard_blocker": False,
        "allowed_uses": [
            "repair_candidate_design",
            "small_probe_block",
            "waiting_block",
            "candidate_level_reject",
        ],
        "required_cross_checks": [
            "live_quote",
            "account_state",
            "risk_gate",
            "fresh_research_conflicts",
            "current_price_structure",
        ],
        "conflict_resolution": "prefer_live_execution_data_and_record_wiki_repair",
    }


def test_selector_uses_fresh_mode_recommendation_after_prompt_mode_degrades(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 value-cycle",
        source_refs=[{"source_type": "policy_scorecard", "source_id": "kis"}],
        body="validated active wiki page",
    )
    for idx in range(25):
        service.upsert_page_effectiveness(
            {
                "page_id": f"kis.playbook.active.{idx}",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid_term",
                "sample_count": 8,
                "helpful_score": 7.0,
                "status": "active",
            }
        )
    service.initialize()
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:old-primary",
                "kis",
                "primary",
                "assist",
                200,
                0.75,
                json.dumps(["old primary"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    application = JueWikiApplicationService(service)
    for idx in range(20):
        return_pct = -0.8 - (idx * 0.01)
        selection_run_id = f"selection:selector-primary-loss-{idx}"
        service.record_selection_run(
            run_id=selection_run_id,
            target_scope="kis",
            request={
                "target_scope": "kis",
                "prompt_mode_application": {
                    "target_scope": "kis",
                    "mode_recommendation": {
                        "recommendation_id": "wiki-mode:old-primary",
                        "recommended_mode": "primary",
                        "sample_count": 200,
                        "confidence": 0.75,
                    },
                },
            },
            selected_pages=[{"page_id": page_id, "rank": 1, "score": 9.0}],
            rejected_pages=[],
            char_count=100,
            max_chars=1000,
            status="ok",
        )
        link = application.record_decision_link(
            selection_run_id=selection_run_id,
            manager_run_id=f"kis-manager-selector-primary-loss-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[page_id],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="primary",
        )
        application.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            evidence={"block_id": f"selector-primary-loss-{idx}"},
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    assert result.mode_recommendation["recommendation_id"] != "wiki-mode:old-primary"
    assert result.mode_recommendation["recommended_mode"] == "assist"
    assert (
        "prompt_mode_effectiveness:primary:degraded"
        in result.mode_recommendation["reasons"]
    )


def test_selector_prefers_matching_scope_symbol_fresh_source_backed_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    preferred_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk1"}],
        confidence=0.8,
        freshness="fresh",
    )
    _write_page(
        service,
        scope="binance",
        symbol="BTCUSDT",
        title="BTCUSDT",
        source_refs=[
            {"source_type": "binance_blocks", "source_id": "blk2"},
            {"source_type": "investment_memory", "source_id": "blk2"},
        ],
        confidence=1.0,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    assert result.status == "ok"
    assert [page.page_id for page in result.pages] == [preferred_id]
    assert result.pages[0].rank == 1
    assert "scope_match:kis" in result.pages[0].reasons
    assert "symbol_overlap:005930" in result.pages[0].reasons
    assert "삼성전자 selector summary" in result.content
    assert result.budget_report["selected_count"] == 1
    assert result.rejected_pages[0]["page_id"] == "binance.symbol.BTCUSDT"
    assert result.rejected_pages[0]["reason"] == "scope_or_symbol_mismatch"


def test_selector_prefers_source_backed_page_over_unsourced_confidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_backed = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930-source-backed",
        title="삼성전자 sourced thesis",
        symbols=["005930"],
        content_sections={
            "Current Stance": "source backed stance",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "source backed selector summary",
        },
        source_refs=[
            {
                "source_type": "naver_reports",
                "source_id": "005930:report",
                "quality_status": "strong",
            }
        ],
        confidence=0.65,
        freshness="fresh",
    )["page_id"]
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930-unsourced",
        title="삼성전자 unsourced high confidence",
        symbols=["005930"],
        content_sections={
            "Current Stance": "unsourced high confidence stance",
            "Durable Facts": "facts",
            "Evidence Links": "",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "unsourced selector summary",
        },
        source_refs=[],
        confidence=0.99,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [source_backed]
    unsourced_reject = next(
        row
        for row in result.rejected_pages
        if row.get("page_id") == "kis.symbol.005930-unsourced"
    )
    assert "evidence_quality:no_sources" in unsourced_reject["penalties"]


def test_selector_prefers_strong_source_over_unknown_source_confidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    strong_id = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930-strong-source",
        title="삼성전자 strong source",
        symbols=["005930"],
        content_sections={
            "Current Stance": "strong source stance",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "strong source selector summary",
        },
        source_refs=[
            {
                "source_type": "naver_reports",
                "source_id": "005930:strong",
                "quality_status": "strong",
            }
        ],
        confidence=0.65,
        freshness="fresh",
    )["page_id"]
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930-unknown-source",
        title="삼성전자 unknown source",
        symbols=["005930"],
        content_sections={
            "Current Stance": "unknown source high confidence stance",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "unknown source selector summary",
        },
        source_refs=[
            {
                "source_type": "naver_reports",
                "source_id": "005930:unknown",
            }
        ],
        confidence=0.99,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [strong_id]
    assert any(
        row.get("page_id") == "kis.symbol.005930-unknown-source"
        for row in result.rejected_pages
    )


def test_selector_surfaces_evidence_quality_and_penalizes_weak_sources(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "weak",
                "quality_warnings": ["price_missing", "valuation_metrics_sparse"],
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    assert result.status == "ok"
    assert result.pages[0].page_id == page_id
    assert result.pages[0].quality_status == "weak"
    assert set(result.pages[0].quality_warnings) == {
        "price_missing",
        "valuation_metrics_sparse",
    }
    assert result.pages[0].evidence_quality["status_counts"] == {"weak": 1}
    assert "evidence_quality:weak:1" in result.pages[0].penalties
    assert result.evidence_quality["warning_counts"] == {
        "price_missing": 1,
        "valuation_metrics_sparse": 1,
    }


def test_selector_surfaces_open_repair_queue_pressure_on_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "strong",
            }
        ],
    )
    service.record_repair_action(
        finding_id="application_repair_queue_pressure:kis:005930",
        page_id=page_id,
        action_type="repair_application_repair_queue_pressure",
        status="scheduled",
        details={
            "decision_scope": "kis",
            "quality_warnings": ["application_repair_queue_pressure"],
            "repair_action": "resolve open repair queue before trusting this page",
            "repair_targets": [
                {
                    "page_id": page_id,
                    "recommended_action": (
                        "resolve_open_repair_queue_before_reusing_page"
                    ),
                }
            ],
        },
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    selected = result.pages[0]
    assert selected.page_id == page_id
    assert selected.quality_status == "partial"
    assert "open_repair_queue" in selected.quality_warnings
    assert "application_repair_queue_pressure" in selected.quality_warnings
    assert "open_repair_queue:1" in selected.penalties
    assert selected.evidence_quality["repair_queue"]["open_count"] == 1
    assert selected.evidence_quality["repair_queue"]["actions"] == [
        {
            "action_type": "repair_application_repair_queue_pressure",
            "status": "scheduled",
            "quality_warnings": ["application_repair_queue_pressure"],
        }
    ]


def test_requested_symbol_summary_surfaces_open_repair_queue_pressure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "strong",
            }
        ],
    )
    service.record_repair_action(
        finding_id="application_repair_queue_pressure:kis:005930",
        page_id=page_id,
        action_type="repair_application_repair_queue_pressure",
        status="scheduled",
        details={
            "decision_scope": "kis",
            "quality_warnings": ["application_repair_queue_pressure"],
            "repair_action": "resolve open repair queue before trusting this page",
            "repair_targets": [
                {
                    "page_id": page_id,
                    "recommended_action": (
                        "resolve_open_repair_queue_before_reusing_page"
                    ),
                }
            ],
        },
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    summary = result.requested_symbol_summaries[0]
    assert summary["symbol"] == "005930"
    assert summary["page_id"] == page_id
    assert summary["quality_status"] == "partial"
    assert "open_repair_queue" in summary["quality_warnings"]
    assert "application_repair_queue_pressure" in summary["quality_warnings"]
    assert summary["evidence_quality"]["repair_queue"]["open_count"] == 1
    assert summary["evidence_quality"]["repair_queue"]["actions"] == [
        {
            "action_type": "repair_application_repair_queue_pressure",
            "status": "scheduled",
            "quality_warnings": ["application_repair_queue_pressure"],
        }
    ]
    assert set(
        result.budget_report["requested_symbol_degraded_summary_reasons"][0][
            "quality_warnings"
        ]
    ) == {"open_repair_queue", "application_repair_queue_pressure"}


def test_requested_symbol_summary_score_canonicalizes_evidence_quality_aliases() -> None:
    page = {
        "page_type": "symbol",
        "confidence": 0.5,
        "freshness": "fresh",
    }

    strong_score = JueWikiSelector._requested_symbol_summary_score(
        page=page,
        evidence_quality={
            "source_count": 1,
            "status_counts": {"ok": 1},
        },
        selected=False,
    )
    weak_score = JueWikiSelector._requested_symbol_summary_score(
        page=page,
        evidence_quality={
            "source_count": 1,
            "status_counts": {"degraded": 1},
        },
        selected=False,
    )

    assert weak_score == strong_score - 18.0


def test_requested_symbol_summary_score_treats_current_as_fresh() -> None:
    base_page = {
        "page_type": "symbol",
        "confidence": 0.5,
    }

    current_score = JueWikiSelector._requested_symbol_summary_score(
        page={**base_page, "freshness": "current"},
        evidence_quality={"source_count": 1, "status_counts": {"strong": 1}},
        selected=False,
    )
    unknown_score = JueWikiSelector._requested_symbol_summary_score(
        page={**base_page, "freshness": "unknown"},
        evidence_quality={"source_count": 1, "status_counts": {"strong": 1}},
        selected=False,
    )
    stale_score = JueWikiSelector._requested_symbol_summary_score(
        page={**base_page, "freshness": "stale"},
        evidence_quality={"source_count": 1, "status_counts": {"strong": 1}},
        selected=False,
    )

    assert current_score == unknown_score + 18.0
    assert current_score == stale_score + 33.0


def test_selector_marks_current_freshness_as_positive_reason(tmp_path: Path) -> None:
    service = _service(tmp_path)
    current_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 current memory",
        source_refs=[{"source_type": "symbol_fundamentals", "source_id": "current"}],
        confidence=0.7,
        freshness="current",
    )
    unknown_id = _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="SK하이닉스 unknown memory",
        source_refs=[{"source_type": "symbol_fundamentals", "source_id": "unknown"}],
        confidence=0.7,
        freshness="unknown",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930", "000660"],
            page_types=["symbol"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert selected[current_id].score > selected[unknown_id].score
    assert "freshness:current" in selected[current_id].reasons
    assert "freshness:current" not in selected[unknown_id].reasons


def test_requested_symbol_degraded_summary_does_not_treat_current_as_stale() -> None:
    reasons = JueWikiSelector._requested_symbol_degraded_summary_reasons(
        [
            {
                "symbol": "005930",
                "freshness": "current",
                "quality_status": "strong",
                "quality_warnings": [],
            }
        ]
    )

    assert reasons == []


def test_selector_canonicalizes_service_evidence_quality_summary_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 alias evidence quality",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:alias",
            }
        ],
    )

    def fake_source_refs_quality_summary(source_refs: object) -> dict[str, object]:
        return {
            "source_count": 1,
            "status_counts": {"degraded": 1},
            "source_type_counts": {"symbol_fundamentals": 1},
            "summary_line": "evidence_quality sources=1, degraded=1",
        }

    monkeypatch.setattr(
        service,
        "source_refs_quality_summary",
        fake_source_refs_quality_summary,
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    assert result.status == "ok"
    assert result.pages[0].page_id == page_id
    assert result.pages[0].evidence_quality["status_counts"] == {"weak": 1}
    assert "evidence_quality:weak:1" in result.pages[0].penalties


def test_selector_promotes_evidence_quality_warnings_to_repair_priority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-02",
                "quality_status": "weak",
                "quality_warnings": [
                    "financial_rows_rejected_credit_rating",
                    "financials_missing",
                ],
            },
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "weak",
                "quality_warnings": [
                    "financial_rows_rejected_credit_rating",
                    "financials_missing",
                ],
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    assert result.budget_report["repair_priority_count"] == 2
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
    )
    assert priority["page_id"] == page_id
    assert priority["priority_type"] == "evidence_quality"
    assert priority["source_type"] == "symbol_fundamentals"
    assert priority["source_id"] == "005930:2026-07-03"
    assert priority["quality_status"] == "weak"
    assert priority["quality_warnings"] == [
        "financial_rows_rejected_credit_rating",
        "financials_missing",
    ]
    assert priority["repair_action"] == (
        "collect or cross-check financial statements before mid/long sizing"
    )
    assert "warning:financial_rows_rejected_credit_rating" in priority["reasons"]
    requested_summary_priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "requested_symbol_degraded_summary"
    )
    assert requested_summary_priority["action_type"] == (
        "refresh_requested_symbol_summary"
    )
    assert requested_summary_priority["symbols"] == ["005930"]


def test_selector_promotes_nested_evidence_quality_to_repair_priority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 compressed evidence quality",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "evidence_quality": {
                    "source_count": 2,
                    "status_counts": {"weak": 1, "partial": 1},
                    "warning_counts": {
                        "financial_rows_rejected_credit_rating": 1,
                        "financials_missing": 1,
                    },
                    "source_type_counts": {"symbol_fundamentals": 2},
                    "top_warnings": [
                        {
                            "warning": "financial_rows_rejected_credit_rating",
                            "count": 1,
                        },
                        {"warning": "financials_missing", "count": 1},
                    ],
                },
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
    )
    assert priority["page_id"] == page_id
    assert priority["source_type"] == "symbol_fundamentals"
    assert priority["source_id"] == "kis.symbol.005930:summary"
    assert priority["quality_status"] == "weak"
    assert priority["quality_warnings"] == [
        "financial_rows_rejected_credit_rating",
        "financials_missing",
    ]
    assert priority["repair_action"] == (
        "collect or cross-check financial statements before mid/long sizing"
    )
    assert "warning:financial_rows_rejected_credit_rating" in priority["reasons"]


def test_selector_canonicalizes_nested_evidence_quality_alias_for_repair_priority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 degraded alias evidence quality",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "evidence_quality": {
                    "source_count": 1,
                    "status_counts": {"degraded": 1},
                    "source_type_counts": {"symbol_fundamentals": 1},
                },
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
    )
    assert priority["page_id"] == page_id
    assert priority["source_type"] == "symbol_fundamentals"
    assert priority["quality_status"] == "weak"
    assert priority["repair_action"] == (
        "treat weak evidence as repair-only unless live structure is independently strong"
    )
    assert "evidence_quality:weak" in priority["reasons"]


def test_selector_preserves_direct_weak_status_when_nested_quality_has_warnings_only(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 direct weak nested warning evidence quality",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "quality_status": "degraded",
                "evidence_quality": {
                    "source_count": 1,
                    "source_type_counts": {"symbol_fundamentals": 1},
                    "top_warnings": [
                        {"warning": "financials_missing", "count": 1},
                    ],
                },
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
    )
    assert priority["page_id"] == page_id
    assert priority["source_type"] == "symbol_fundamentals"
    assert priority["quality_status"] == "weak"
    assert priority["quality_warnings"] == ["financials_missing"]
    assert priority["confidence"] == 1.0
    assert priority["repair_action"] == (
        "collect or cross-check financial statements before mid/long sizing"
    )
    assert "evidence_quality:weak" in priority["reasons"]
    assert "warning:financials_missing" in priority["reasons"]


def test_selector_avoids_duplicate_repair_priority_for_direct_and_nested_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 duplicated evidence quality",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "quality_status": "weak",
                "quality_warnings": ["financials_missing"],
                "evidence_quality": {
                    "source_count": 1,
                    "status_counts": {"weak": 1},
                    "warning_counts": {"financials_missing": 1},
                    "source_type_counts": {"symbol_fundamentals": 1},
                    "top_warnings": [
                        {"warning": "financials_missing", "count": 1},
                    ],
                },
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
    ]

    assert len(priorities) == 1
    assert priorities[0]["page_id"] == page_id
    assert priorities[0]["source_type"] == "symbol_fundamentals"
    assert priorities[0]["quality_status"] == "weak"
    assert priorities[0]["quality_warnings"] == ["financials_missing"]


def test_selector_preserves_direct_only_warning_next_to_nested_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 nested plus direct warning",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "quality_status": "weak",
                "quality_warnings": [
                    "financials_missing",
                    "identity_name_missing",
                ],
                "evidence_quality": {
                    "source_count": 1,
                    "status_counts": {"weak": 1},
                    "warning_counts": {"financials_missing": 1},
                    "source_type_counts": {"symbol_fundamentals": 1},
                    "top_warnings": [
                        {"warning": "financials_missing", "count": 1},
                    ],
                },
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
    ]

    assert len(priorities) == 2
    by_warning = {
        tuple(row["quality_warnings"]): row
        for row in priorities
    }
    assert by_warning[("financials_missing",)]["source_type"] == (
        "symbol_fundamentals"
    )
    assert by_warning[("identity_name_missing",)]["source_type"] == (
        "wiki_symbol_summary"
    )


def test_selector_exposes_repair_priority_effectiveness_to_repair_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:financials",
                "quality_status": "weak",
                "quality_warnings": ["financials_missing"],
            }
        ],
    )
    application = JueWikiApplicationService(service)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = application.record_decision_link(
            selection_run_id=f"selection:selector-repair-loop-{idx}",
            manager_run_id=f"kis-manager-selector-repair-loop-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[page_id],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        application.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_jue_wiki_repair_contract",
                "repair_priority_types": ["evidence_quality"],
                "repair_action_types": ["refresh_symbol_financials"],
                "repair_source_ids": ["005930:financials"],
                "repair_decision_uses": ["evidence_quality_cross_check"],
                "repair_loop_statuses": ["repair_required"],
                "repair_loop_action_types": ["refresh_symbol_financials"],
                "repair_loop_sample_counts": [3],
                "repair_loop_missed_counts": [2],
                "repair_loop_resolved_counts": [0],
                "repair_loop_resolution_rates": [0.0],
            },
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    assert result.repair_priority_effectiveness["status"] == "repair_required"
    assert result.repair_priority_effectiveness["resolution_rate"] == 1 / 3
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
        and row.get("source_id") == "005930:financials"
    )
    assert priority["repair_loop_status"] == "repair_required"
    assert priority["repair_loop_sample_count"] == 3
    assert priority["repair_loop_missed_count"] == 2
    assert priority["repair_loop_resolved_count"] == 1
    assert priority["repair_loop_resolution_rate"] == 1 / 3
    assert priority["repair_loop_action_type"] == "refresh_symbol_financials"
    assert priority["decision_use"] == "evidence_quality_cross_check"
    assert "repair_loop:repair_required" in priority["reasons"]
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": result.repair_priorities,
            "repair_priority_effectiveness": result.repair_priority_effectiveness,
        }
    )

    assert contract["repair_loop_effectiveness"]["status"] == "repair_required"
    assert contract["repair_loop_effectiveness"]["resolution_rate"] == 1 / 3
    assert contract["repair_loop_effectiveness"]["top_degraded"][0] == {
        "decision_scope": "kis",
        "priority_type": "evidence_quality",
        "action_type": "refresh_symbol_financials",
        "decision_use": "evidence_quality_cross_check",
        "source_id": "005930:financials",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
    }
    assert contract["repair_loop_effectiveness"]["repair_loop_status_metrics"][0] == {
        "decision_scope": "kis",
        "repair_loop_status": "repair_required",
        "action_type": "refresh_symbol_financials",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
        "loop_sample_count": 3,
        "loop_missed_count": 2,
        "loop_resolved_count": 0,
        "loop_resolution_rate": 0.0,
    }
    contract_priority = next(
        row
        for row in contract["top_priorities"]
        if row.get("priority_type") == "evidence_quality"
        and row.get("source_id") == "005930:financials"
    )
    assert contract_priority["repair_loop_status"] == "repair_required"
    assert contract_priority["repair_loop_resolution_rate"] == 1 / 3
    assert contract_priority["repair_loop_missed_count"] == 2
    assert contract_priority["decision_use"] == "evidence_quality_cross_check"


def test_selector_exposes_validation_repair_effectiveness(
    tmp_path: Path,
) -> None:
    from tradecraft.services.jue_wiki_selector import (
        build_jue_wiki_validation_repair_contract_for_prompt,
        compact_jue_wiki_validation_repair_effectiveness_for_prompt,
    )

    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
    )
    application = JueWikiApplicationService(service)
    for idx, outcome_kind in enumerate(
        [
            "missed_validation_probe",
            "missed_validation_probe",
            "resolved_validation_probe",
        ]
    ):
        link = application.record_decision_link(
            selection_run_id=f"selection:selector-validation-{idx}",
            manager_run_id=f"kis-manager-selector-validation-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.risk.trading_validation"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="manager_run",
            prompt_mode="assist",
        )
        application.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.06,
            evidence={
                "source": "kis_validation_repair",
                "discipline_ids": ["cost_simulation"],
                "repair_action_ids": ["collect_cost_edge"],
                "entry_biases": ["waiting_probe_until_cost_edge_clean"],
                "allowed_entry_postures": ["shadow_or_waiting_entry_only"],
                "blocks_new_entries": [
                    "scale_up_and_unvalidated_immediate_entries"
                ],
                "risk_budget_multiplier": 0.25,
            },
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    validation = result.validation_repair_effectiveness
    assert validation["status"] == "repair_required"
    assert validation["sample_count"] == 3
    assert validation["resolution_rate"] == 1 / 3
    assert validation["top_degraded"][0] == {
        "decision_scope": "kis",
        "discipline_id": "cost_simulation",
        "repair_action_id": "collect_cost_edge",
        "entry_bias": "waiting_probe_until_cost_edge_clean",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
        "allowed_entry_postures": ["shadow_or_waiting_entry_only"],
        "blocks_new_entries": ["scale_up_and_unvalidated_immediate_entries"],
        "risk_budget_multiplier": 0.25,
        "sources": ["kis_validation_repair"],
        "source_counts": {"kis_validation_repair": 3},
    }
    compact = compact_jue_wiki_validation_repair_effectiveness_for_prompt(validation)
    assert compact["top_degraded"][0]["sources"] == ["kis_validation_repair"]
    assert compact["top_degraded"][0]["source_counts"] == {
        "kis_validation_repair": 3
    }
    plan = compact["validation_repair_action_plan"]
    assert plan["source_counts"] == {"kis_validation_repair": 3}
    assert plan["legacy_source_counts"] == {"kis_validation_repair": 3}
    assert plan["contract_source_counts"] == {}
    assert plan["legacy_sample_count"] == 3
    assert plan["contract_sample_count"] == 0
    assert plan["source_mix_status"] == "legacy_only"
    assert plan["source_mix_count_basis"] == "top_degraded_metric_signal_count"
    assert plan["contract_feedback_gap"] == {
        "status": "missing_contract_outcomes",
        "legacy_sample_count": 3,
        "contract_sample_count": 0,
        "required_response": (
            "record validation_repair_resolution and resolved_candidates so "
            "future wiki updates can measure contract effectiveness"
        ),
    }
    assert plan["degraded_metric_evidence"] == [
        {
            "discipline_id": "cost_simulation",
            "repair_action_id": "collect_cost_edge",
            "entry_bias": "waiting_probe_until_cost_edge_clean",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
            "source_counts": {"kis_validation_repair": 3},
        }
    ]
    contract = build_jue_wiki_validation_repair_contract_for_prompt(compact)
    assert contract["source_counts"] == {"kis_validation_repair": 3}
    assert contract["legacy_source_counts"] == {"kis_validation_repair": 3}
    assert contract["contract_source_counts"] == {}
    assert contract["source_mix_status"] == "legacy_only"
    assert contract["source_mix_count_basis"] == "top_degraded_metric_signal_count"
    assert "regime_confirmed_wait" in contract["accepted_resolutions"]
    assert "risk_check_defer" in contract["accepted_resolutions"]
    assert "new_watch_with_trigger" in contract["accepted_resolutions"]
    assert contract["contract_feedback_gap"] == {
        "status": "missing_contract_outcomes",
        "legacy_sample_count": 3,
        "contract_sample_count": 0,
        "required_response": (
            "record validation_repair_resolution and resolved_candidates so "
            "future wiki updates can measure contract effectiveness"
        ),
    }
    assert contract["degraded_metric_evidence"] == [
        {
            "discipline_id": "cost_simulation",
            "repair_action_id": "collect_cost_edge",
            "entry_bias": "waiting_probe_until_cost_edge_clean",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
            "source_counts": {"kis_validation_repair": 3},
        }
    ]


def test_validation_repair_effectiveness_omits_missing_summary_metric_zeros() -> None:
    from tradecraft.services.jue_wiki_selector import (
        compact_jue_wiki_validation_repair_effectiveness_for_prompt,
    )

    compact = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
        {
            "status": "unavailable",
        }
    )

    assert compact == {
        "status": "unavailable",
    }


def test_validation_repair_effectiveness_omits_missing_metric_row_zeros() -> None:
    from tradecraft.services.jue_wiki_selector import (
        compact_jue_wiki_validation_repair_effectiveness_for_prompt,
    )

    compact = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
        {
            "status": "repair_required",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "discipline_id": "cost_simulation",
                    "repair_action_id": "collect_cost_edge",
                    "status": "repair_required",
                }
            ],
        }
    )

    row = compact["top_degraded"][0]
    assert row == {
        "decision_scope": "kis",
        "discipline_id": "cost_simulation",
        "repair_action_id": "collect_cost_edge",
        "status": "repair_required",
    }
    plan_row = compact["validation_repair_action_plan"]["degraded_metric_evidence"][0]
    assert plan_row == {
        "discipline_id": "cost_simulation",
        "repair_action_id": "collect_cost_edge",
        "status": "repair_required",
    }


def test_validation_repair_action_plan_keeps_explicit_zero_risk_budget() -> None:
    from tradecraft.services.jue_wiki_selector import (
        compact_jue_wiki_validation_repair_effectiveness_for_prompt,
    )

    missing_budget = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
        {
            "status": "repair_required",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "discipline_id": "cost_simulation",
                    "repair_action_id": "collect_cost_edge",
                    "status": "repair_required",
                }
            ],
        }
    )
    explicit_zero_budget = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
        {
            "status": "repair_required",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "discipline_id": "cost_simulation",
                    "repair_action_id": "collect_cost_edge",
                    "risk_budget_multiplier": 0.0,
                    "status": "repair_required",
                }
            ],
        }
    )

    assert "risk_budget_multiplier" not in missing_budget[
        "validation_repair_action_plan"
    ]
    assert explicit_zero_budget["top_degraded"][0]["risk_budget_multiplier"] == 0.0
    assert (
        explicit_zero_budget["validation_repair_action_plan"][
            "risk_budget_multiplier"
        ]
        == 0.0
    )


def test_validation_repair_contract_preserves_contract_basis_evidence() -> None:
    from tradecraft.services.jue_wiki_selector import (
        build_jue_wiki_validation_repair_contract_for_prompt,
        compact_jue_wiki_validation_repair_effectiveness_for_prompt,
    )

    compact = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
        {
            "status": "repair_required",
            "sample_count": 1,
            "missed_count": 1,
            "resolved_count": 0,
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "discipline_id": "cost_simulation",
                    "repair_action_id": "collect_cost_edge",
                    "entry_bias": "waiting_probe_until_cost_edge_clean",
                    "sample_count": 1,
                    "missed_count": 1,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "repair_required",
                    "source_counts": {"kis_validation_repair_contract": 1},
                    "sources": ["kis_validation_repair_contract"],
                    "contract_basis_evidence": [
                        {
                            "discipline_id": "cost_simulation",
                            "repair_action_id": "collect_cost_edge",
                            "entry_bias": "waiting_probe_until_cost_edge_clean",
                            "sample_count": 9,
                            "missed_count": 6,
                            "resolved_count": 3,
                            "resolution_rate": 1 / 3,
                            "status": "repair_required",
                            "source_counts": {
                                "kis_validation_repair_contract": 9
                            },
                        }
                    ],
                }
            ],
        }
    )

    plan = compact["validation_repair_action_plan"]
    assert plan["contract_basis_pressure_summary"] == {
        "sample_count": 9,
        "missed_count": 6,
        "resolved_count": 3,
        "resolution_rate": 1 / 3,
        "miss_rate": 2 / 3,
        "repair_pressure_score": 4.0,
        "status": "repair_required",
    }
    contract = build_jue_wiki_validation_repair_contract_for_prompt(compact)

    assert contract["contract_basis_pressure_summary"] == {
        "sample_count": 9,
        "missed_count": 6,
        "resolved_count": 3,
        "resolution_rate": 1 / 3,
        "miss_rate": 2 / 3,
        "repair_pressure_score": 4.0,
        "status": "repair_required",
    }
    assert contract["degraded_metric_evidence"][0][
        "contract_basis_sample_count"
    ] == 9
    assert contract["degraded_metric_evidence"][0][
        "contract_basis_missed_count"
    ] == 6
    assert contract["degraded_metric_evidence"][0][
        "contract_basis_resolved_count"
    ] == 3
    assert contract["degraded_metric_evidence"][0][
        "contract_basis_resolution_rate"
    ] == 1 / 3
    assert contract["degraded_metric_evidence"][0][
        "contract_basis_repair_pressure_score"
    ] == 4.0
    assert contract["degraded_metric_evidence"][0][
        "contract_basis_evidence"
    ] == [
        {
            "discipline_id": "cost_simulation",
            "repair_action_id": "collect_cost_edge",
            "entry_bias": "waiting_probe_until_cost_edge_clean",
            "sample_count": 9,
            "missed_count": 6,
            "resolved_count": 3,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
            "source_counts": {"kis_validation_repair_contract": 9},
        }
    ]


def test_validation_repair_contract_preserves_explicit_zero_contract_basis_metrics() -> None:
    from tradecraft.services.jue_wiki_selector import (
        build_jue_wiki_validation_repair_contract_for_prompt,
        compact_jue_wiki_validation_repair_effectiveness_for_prompt,
    )

    compact = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
        {
            "status": "repair_required",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "discipline_id": "cost_simulation",
                    "repair_action_id": "collect_cost_edge",
                    "entry_bias": "waiting_probe_until_cost_edge_clean",
                    "sample_count": 1,
                    "missed_count": 1,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "repair_required",
                    "contract_basis_resolution_rate": 0.0,
                    "contract_basis_miss_rate": 1.0,
                    "contract_basis_repair_pressure_score": 0.0,
                    "contract_basis_evidence": [
                        {
                            "discipline_id": "cost_simulation",
                            "repair_action_id": "collect_cost_edge",
                            "entry_bias": "waiting_probe_until_cost_edge_clean",
                            "sample_count": 9,
                            "missed_count": 6,
                            "resolved_count": 3,
                            "resolution_rate": 1 / 3,
                            "repair_pressure_score": 4.0,
                            "status": "repair_required",
                        }
                    ],
                }
            ],
        }
    )

    contract = build_jue_wiki_validation_repair_contract_for_prompt(compact)
    evidence = contract["degraded_metric_evidence"][0]
    assert evidence["contract_basis_resolution_rate"] == 0.0
    assert evidence["contract_basis_miss_rate"] == 1.0
    assert evidence["contract_basis_repair_pressure_score"] == 0.0


def test_validation_repair_contract_omits_missing_nested_contract_basis_metrics() -> None:
    from tradecraft.services.jue_wiki_selector import (
        compact_jue_wiki_validation_repair_effectiveness_for_prompt,
    )

    compact = compact_jue_wiki_validation_repair_effectiveness_for_prompt(
        {
            "status": "repair_required",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "discipline_id": "cost_simulation",
                    "repair_action_id": "collect_cost_edge",
                    "status": "repair_required",
                    "contract_basis_evidence": [
                        {
                            "discipline_id": "cost_simulation",
                            "repair_action_id": "collect_cost_edge",
                            "entry_bias": "waiting_probe_until_cost_edge_clean",
                            "status": "unproven",
                        }
                    ],
                }
            ],
        }
    )

    nested = compact["top_degraded"][0]["contract_basis_evidence"][0]
    assert nested == {
        "discipline_id": "cost_simulation",
        "repair_action_id": "collect_cost_edge",
        "entry_bias": "waiting_probe_until_cost_edge_clean",
        "status": "unproven",
    }
    plan_nested = compact["validation_repair_action_plan"][
        "degraded_metric_evidence"
    ][0]["contract_basis_evidence"][0]
    assert plan_nested == nested


def test_validation_repair_contract_omits_missing_plan_counts_but_keeps_zero() -> None:
    from tradecraft.services.jue_wiki_selector import (
        build_jue_wiki_validation_repair_contract_for_prompt,
    )

    missing = build_jue_wiki_validation_repair_contract_for_prompt(
        {
            "validation_repair_action_plan": {
                "status": "probe",
                "top_disciplines": ["cost_simulation"],
                "repair_action_ids": ["collect_cost_edge"],
            }
        }
    )
    explicit_zero = build_jue_wiki_validation_repair_contract_for_prompt(
        {
            "validation_repair_action_plan": {
                "status": "probe",
                "risk_budget_multiplier": 0.0,
                "legacy_sample_count": 0,
                "contract_sample_count": 0,
                "top_disciplines": ["cost_simulation"],
                "repair_action_ids": ["collect_cost_edge"],
                "contract_basis_pressure_summary": {
                    "status": "probe",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "miss_rate": 0,
                    "repair_pressure_score": 0.0,
                },
                "contract_feedback_gap": {
                    "status": "closed",
                    "legacy_sample_count": 0,
                    "contract_sample_count": 0,
                },
            }
        }
    )

    assert missing == {
        "version": "jue_wiki_validation_repair_contract_v1",
        "status": "probe",
        "hard_blocker": False,
        "requires_validation_repair_resolution": True,
        "top_disciplines": ["cost_simulation"],
        "repair_action_ids": ["collect_cost_edge"],
        "accepted_resolutions": [
            "smaller_probe_block",
            "waiting_entry_with_validation_repair_resolution",
            "candidate_reject_with_missing_validation_named",
            "regime_confirmed_wait",
            "risk_check_defer",
            "new_watch_with_trigger",
            "no_new_entry_until_required_validation_repair_is_resolved",
        ],
        "safety_gates_still_override": True,
    }
    assert explicit_zero["risk_budget_multiplier"] == 0.0
    assert explicit_zero["legacy_sample_count"] == 0
    assert explicit_zero["contract_sample_count"] == 0
    assert explicit_zero["contract_basis_pressure_summary"] == {
        "sample_count": 0,
        "missed_count": 0,
        "resolved_count": 0,
        "resolution_rate": 0.0,
        "miss_rate": 0.0,
        "repair_pressure_score": 0.0,
        "status": "probe",
    }
    assert explicit_zero["contract_feedback_gap"] == {
        "status": "closed",
        "legacy_sample_count": 0,
        "contract_sample_count": 0,
    }


def test_selector_exposes_wiki_application_coverage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
    )
    application = JueWikiApplicationService(service)
    application.record_decision_link(
        selection_run_id="selection:kis-covered",
        manager_run_id="kis-manager-covered",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    }
                ]
            }
        },
    )
    application.record_decision_link(
        selection_run_id="selection:kis-untraced",
        manager_run_id="kis-manager-untraced",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    coverage = result.wiki_application_coverage
    assert coverage["status"] == "warning"
    assert coverage["decision_scope"] == "kis"
    assert coverage["coverage"] == {
        "decision_scope": "kis",
        "decision_link_count": 2,
        "decision_links_with_selected_wiki_pages": 1,
        "decision_links_with_selected_wiki_pages_pct": 50.0,
        "selection_outcome_count": 0,
        "selection_outcomes_with_selected_wiki_page": 0,
        "selection_outcomes_with_selected_wiki_page_pct": 0.0,
        "selection_outcomes_with_quality_warnings": 0,
        "selection_outcome_attribution_filtered_count": 0,
        "closed_block_outcomes_without_horizon": 0,
        "closed_block_outcomes_without_horizon_pct": 0.0,
    }
    assert [alert["code"] for alert in coverage["alerts"]] == [
        "wiki_selected_pages_missing",
        "wiki_outcome_feedback_missing",
    ]


def test_compact_wiki_application_coverage_preserves_horizon_gap_alert(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    application = JueWikiApplicationService(service)
    link = application.record_decision_link(
        selection_run_id="selection:kis-horizon-gap",
        manager_run_id="kis-manager-horizon-gap",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    }
                ]
            }
        },
    )
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
            """,
            (
                "selector-horizon-gap-outcome",
                link["link_id"],
                "selection:kis-horizon-gap",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "blk-horizon-gap",
                "closed_block",
                "win",
                1.1,
                json.dumps(
                    {
                        "symbol": "005930",
                        "block_id": "blk-horizon-gap",
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    compact = compact_jue_wiki_application_coverage_for_prompt(
        application.project_wiki_application_coverage(decision_scope="kis")
    )

    assert compact["coverage"]["closed_block_outcomes_without_horizon"] == 1
    assert compact["coverage"]["closed_block_outcomes_without_horizon_pct"] == 100.0
    assert compact["alerts"] == [
        {
            "severity": "warning",
            "code": "wiki_outcome_horizon_missing",
            "decision_scope": "kis",
            "message": (
                "kis wiki closed block outcomes without horizon are 100.0%; "
                "closed block feedback must be attributed to a horizon/lane."
            ),
            "action": "project_selection_outcomes_and_page_effectiveness",
        }
    ]


def test_compact_wiki_application_coverage_omits_missing_metric_zeros() -> None:
    compact = compact_jue_wiki_application_coverage_for_prompt(
        {
            "status": "unavailable",
            "decision_scope": "kis",
        }
    )

    assert compact == {
        "status": "unavailable",
        "decision_scope": "kis",
    }


def test_selector_promotes_closed_block_horizon_gap_to_repair_priority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
    )
    application = JueWikiApplicationService(service)
    link = application.record_decision_link(
        selection_run_id="selection:kis-horizon-gap-priority",
        manager_run_id="kis-manager-horizon-gap-priority",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    }
                ]
            }
        },
    )
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
            """,
            (
                "selector-horizon-gap-priority-outcome",
                link["link_id"],
                "selection:kis-horizon-gap-priority",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "blk-horizon-gap-priority",
                "closed_block",
                "win",
                1.4,
                json.dumps(
                    {
                        "symbol": "005930",
                        "block_id": "blk-horizon-gap-priority",
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("action_type") == "reproject_closed_block_outcome_horizons"
    )
    assert priority["priority_type"] == "wiki_application_coverage"
    assert priority["source_type"] == "wiki_application_coverage"
    assert priority["source_id"] == "repair:outcome_horizon:kis"
    assert priority["decision_use"] == "horizon_lane_attribution_repair"
    assert priority["quality_warnings"] == [
        "closed_block_outcome_horizon_missing"
    ]
    assert priority["repair_targets"] == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
    ]
    assert "closed_block_outcomes_without_horizon:1" in priority["reasons"]

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    top_priority = next(
        row
        for row in contract["top_priorities"]
        if row.get("action_type") == "reproject_closed_block_outcome_horizons"
    )
    assert top_priority["decision_use"] == "horizon_lane_attribution_repair"
    assert top_priority["candidate_resolution_required"] is True


def test_repair_contract_omits_zero_horizon_gap_from_non_horizon_priorities() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "repair_queue",
                    "symbols": ["005930"],
                    "source_type": "selected_wiki_pages_repair_queue",
                    "source_id": "repair:usage-guidance",
                    "action_type": "repair_usage_guidance_contract",
                    "decision_use": "usage_guidance_effectiveness_repair",
                    "quality_warnings": ["usage_guidance_degraded"],
                }
            ]
        }
    )

    top_priority = contract["top_priorities"][0]
    assert "closed_block_outcomes_without_horizon" not in top_priority
    assert "closed_block_outcomes_without_horizon_pct" not in top_priority
    assert "sample_count" not in top_priority
    assert "win_rate" not in top_priority
    assert "expectancy" not in top_priority
    assert "helpful_score" not in top_priority
    assert "drawdown_pressure" not in top_priority
    assert "repair_loop_sample_count" not in top_priority
    assert "repair_loop_missed_count" not in top_priority
    assert "repair_loop_resolved_count" not in top_priority
    assert "repair_loop_resolution_rate" not in top_priority


def test_selector_repair_priorities_omit_missing_metrics_but_keep_explicit_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selector = JueWikiSelector(service)
    pages = [
        {
            "page_id": "kis.symbol.005930",
            "scope": "kis",
            "page_type": "symbol",
            "symbols": ["005930"],
        }
    ]

    missing = selector._repair_priorities(
        pages=pages,
        effectiveness_by_page={
            "kis.symbol.005930": {
                "page_id": "kis.symbol.005930",
                "decision_scope": "kis",
                "status": "degraded",
                "reasons": ["needs repair without measured outcome yet"],
            }
        },
        trust_profile_effectiveness={},
        repair_priority_effectiveness={},
        target_scope="kis",
        requested_symbols={"005930"},
        limit=4,
    )

    assert missing[0] == {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "symbol_overlap": ["005930"],
        "status": "degraded",
        "reasons": ["needs repair without measured outcome yet"],
        "repair_action": "treat as repair evidence, not as a no-action blocker",
    }
    for key in (
        "sample_count",
        "win_rate",
        "expectancy",
        "avg_return_pct",
        "median_mae_pct",
        "drawdown_pressure",
        "helpful_score",
        "confidence",
    ):
        assert key not in missing[0]

    explicit_zero = selector._repair_priorities(
        pages=pages,
        effectiveness_by_page={
            "kis.symbol.005930": {
                "page_id": "kis.symbol.005930",
                "decision_scope": "kis",
                "status": "degraded",
                "sample_count": 0,
                "win_rate": 0.0,
                "expectancy": 0,
                "avg_return_pct": 0.0,
                "median_mae_pct": 0,
                "drawdown_pressure": 0.0,
                "helpful_score": 0,
                "confidence": 0.0,
            }
        },
        trust_profile_effectiveness={},
        repair_priority_effectiveness={},
        target_scope="kis",
        requested_symbols={"005930"},
        limit=4,
    )

    assert explicit_zero[0]["sample_count"] == 0
    assert explicit_zero[0]["win_rate"] == 0.0
    assert explicit_zero[0]["expectancy"] == 0.0
    assert explicit_zero[0]["avg_return_pct"] == 0.0
    assert explicit_zero[0]["median_mae_pct"] == 0.0
    assert explicit_zero[0]["drawdown_pressure"] == 0.0
    assert explicit_zero[0]["helpful_score"] == 0.0
    assert explicit_zero[0]["confidence"] == 0.0


def test_selector_preserves_missing_effectiveness_metrics_after_db_upsert(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 결측 효과성",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "status": "degraded",
            "reasons": ["degraded before outcome projection has metrics"],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=10_000,
        )
    )
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("page_id") == page_id
        and row.get("status") == "degraded"
    )

    assert priority["status"] == "degraded"
    assert priority["reasons"] == ["degraded before outcome projection has metrics"]
    for key in (
        "sample_count",
        "win_rate",
        "expectancy",
        "avg_return_pct",
        "median_mae_pct",
        "drawdown_pressure",
        "helpful_score",
        "confidence",
    ):
        assert key not in priority


def test_repair_contract_omits_missing_repair_target_effectiveness_metrics() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "repair_queue",
                    "symbols": ["005930"],
                    "source_type": "wiki_repair_queue",
                    "source_id": "repair:fundamentals",
                    "action_type": "refresh_symbol_fundamentals",
                    "decision_use": "evidence_quality_repair",
                    "repair_target_effectiveness": {
                        "page_id": "repair_target.refresh_symbol_fundamentals",
                        "status": "unproven",
                    },
                }
            ]
        }
    )

    effectiveness = contract["top_priorities"][0]["repair_target_effectiveness"]
    assert effectiveness == {
        "page_id": "repair_target.refresh_symbol_fundamentals",
        "status": "unproven",
    }
    assert "sample_count" not in effectiveness
    assert "win_rate" not in effectiveness
    assert "expectancy" not in effectiveness
    assert "helpful_score" not in effectiveness
    assert "confidence" not in effectiveness


def test_selector_uses_action_level_repair_loop_metrics_when_source_is_missing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:financials",
                "quality_status": "weak",
                "quality_warnings": ["financials_missing"],
            }
        ],
    )
    application = JueWikiApplicationService(service)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = application.record_decision_link(
            selection_run_id=f"selection:selector-action-loop-{idx}",
            manager_run_id=f"kis-manager-selector-action-loop-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[page_id],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        application.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "legacy_repair_loop_outcome",
                "repair_action_types": ["refresh_symbol_financials"],
                "repair_loop_statuses": ["repair_required"],
                "repair_loop_action_types": ["refresh_symbol_financials"],
                "repair_loop_sample_counts": [4],
                "repair_loop_missed_counts": [3],
                "repair_loop_resolved_counts": [1],
                "repair_loop_resolution_rates": [0.25],
            },
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
        and row.get("source_id") == "005930:financials"
    )

    assert priority["repair_loop_status"] == "repair_required"
    assert priority["repair_loop_action_type"] == "refresh_symbol_financials"
    assert priority["repair_loop_sample_count"] == 4
    assert priority["repair_loop_missed_count"] == 3
    assert priority["repair_loop_resolved_count"] == 1
    assert priority["repair_loop_resolution_rate"] == 0.25
    assert "repair_loop_status_metric:repair_required" in priority["reasons"]


def test_selector_keeps_repair_loop_reason_when_quality_reasons_are_crowded(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:financials",
                "quality_status": "weak",
                "quality_warnings": [
                    "financials_missing",
                    "financial_rows_rejected_empty",
                    "valuation_metrics_sparse",
                    "valuation_stale_gt_30d",
                ],
            }
        ],
    )
    application = JueWikiApplicationService(service)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = application.record_decision_link(
            selection_run_id=f"selection:selector-crowded-loop-{idx}",
            manager_run_id=f"kis-manager-selector-crowded-loop-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[page_id],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        application.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "legacy_repair_loop_outcome",
                "repair_action_types": ["refresh_symbol_financials"],
                "repair_loop_statuses": ["repair_required"],
                "repair_loop_action_types": ["refresh_symbol_financials"],
                "repair_loop_sample_counts": [4],
                "repair_loop_missed_counts": [3],
                "repair_loop_resolved_counts": [1],
                "repair_loop_resolution_rates": [0.25],
            },
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "evidence_quality"
        and row.get("source_id") == "005930:financials"
    )

    assert "repair_loop_status_metric:repair_required" in priority["reasons"]
    assert "repair_loop:repair_required" in priority["reasons"]
    assert "repair_resolution_rate:0.2500" in priority["reasons"]


def test_repair_contract_preserves_evidence_quality_fields() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "evidence_quality",
                    "symbols": ["005930"],
                    "source_type": "symbol_fundamentals",
                    "source_id": "005930:2026-07-03",
                    "quality_status": "weak",
                    "quality_warnings": ["price_missing"],
                    "repair_action": "refresh price evidence",
                    "reasons": ["warning:price_missing"],
                }
            ]
        }
    )

    assert contract["repair_priority_count"] == 1
    assert contract["top_priorities"][0]["priority_type"] == "evidence_quality"
    assert contract["top_priorities"][0]["source_type"] == "symbol_fundamentals"
    assert contract["top_priorities"][0]["quality_status"] == "weak"
    assert contract["top_priorities"][0]["quality_warnings"] == ["price_missing"]


def test_repair_contract_prioritizes_repair_required_status_metrics() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "evidence_quality",
                    "symbols": ["005930"],
                    "source_type": "symbol_fundamentals",
                    "source_id": "005930:financials",
                    "action_type": "refresh_symbol_financials",
                    "quality_status": "weak",
                    "quality_warnings": ["financials_missing"],
                    "repair_action": "refresh financial evidence",
                    "reasons": ["warning:financials_missing"],
                }
            ],
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 24,
                "missed_count": 10,
                "resolved_count": 14,
                "resolution_rate": 14 / 24,
                "repair_required_count": 1,
                    "repair_loop_status_metrics": [
                        {
                            "decision_scope": "kis",
                            "repair_loop_status": "active",
                            "action_type": f"aaa_active_{idx}",
                        "sample_count": 6,
                        "missed_count": 1,
                        "resolved_count": 5,
                        "resolution_rate": 5 / 6,
                        "status": "active",
                    }
                    for idx in range(5)
                ]
                + [
                    {
                        "decision_scope": "kis",
                        "repair_loop_status": "repair_required",
                        "priority_type": "evidence_quality",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "sample_count": 9,
                        "missed_count": 8,
                        "resolved_count": 1,
                        "resolution_rate": 1 / 9,
                        "status": "repair_required",
                    },
                    {
                        "decision_scope": "kis",
                        "repair_loop_status": "repair_required",
                        "priority_type": "evidence_quality",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "sample_count": 4,
                        "missed_count": 2,
                        "resolved_count": 2,
                        "resolution_rate": 0.5,
                        "status": "repair_required",
                        }
                    ],
                    "repair_success_criteria_metrics": [
                        {
                            "decision_scope": "kis",
                            "criterion": "audit_contract_repaired_or_demoted",
                            "sample_count": 3,
                            "missed_count": 2,
                            "resolved_count": 1,
                            "resolution_rate": 1 / 3,
                            "status": "repair_required",
                        }
                    ],
                    "repair_learning_directive_metrics": [
                        {
                            "decision_scope": "kis",
                            "recommended_action": (
                                "repair_or_demote_success_criterion_before_reuse"
                            ),
                            "sample_count": 3,
                            "missed_count": 2,
                            "resolved_count": 1,
                            "resolution_rate": 1 / 3,
                            "status": "repair_required",
                        }
                    ],
                    "repair_learning_step_metrics": [
                        {
                            "decision_scope": "kis",
                            "resolution_step": (
                                "inspect_failed_repair_directive_outcomes"
                            ),
                            "sample_count": 3,
                            "missed_count": 2,
                            "resolved_count": 1,
                            "resolution_rate": 1 / 3,
                            "status": "repair_required",
                        }
                    ],
                    "repair_learning_resolution_metrics": [
                        {
                            "decision_scope": "kis",
                            "recommended_resolution": (
                                "revise_repair_step_contract_then_probe"
                            ),
                            "sample_count": 3,
                            "missed_count": 2,
                            "resolved_count": 1,
                            "resolution_rate": 1 / 3,
                            "status": "repair_required",
                        }
                    ],
                },
            }
        )

    metrics = contract["repair_loop_effectiveness"]["repair_loop_status_metrics"]
    assert any(
        row.get("action_type") == "refresh_symbol_financials"
        and row.get("status") == "repair_required"
        for row in metrics
    )
    assert contract["repair_loop_effectiveness"]["repair_loop_status_summary"] == {
        "metric_count": 7,
        "repair_required_count": 2,
        "probe_count": 0,
        "active_count": 5,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_repair_action_type": "refresh_symbol_financials",
        "repair_required_action_types": ["refresh_symbol_financials"],
        "top_missed_action_types": ["refresh_symbol_financials"],
        "repair_action_targets": [
            {
                "decision_scope": "kis",
                "status": "repair_required",
                "action_type": "refresh_symbol_financials",
                "primary_decision_use": "evidence_quality_cross_check",
                "decision_uses": ["evidence_quality_cross_check"],
                "priority_types": ["evidence_quality"],
                "sample_count": 13,
                "missed_count": 10,
                "resolved_count": 3,
                "resolution_rate": 3 / 13,
                "miss_rate": round(10 / 13, 6),
                "repair_pressure_score": round(10 * (10 / 13), 6),
                "recommended_resolution": "refresh_evidence_then_reprice_entry_exit",
                "resolution_steps": [
                    "refresh_required_evidence",
                    "reprice_entry_target_stop",
                    "downgrade_or_reject_if_still_missing",
                ],
                "resolution_success_criteria": [
                    "required_evidence_present",
                    "entry_target_stop_repriced",
                    "unresolved_gap_downgraded_or_rejected",
                ],
                "metric_count": 2,
            }
        ],
        "max_missed_count": 8,
        "max_sample_count": 9,
        "min_resolution_rate": 1 / 9,
    }
    assert contract["repair_loop_effectiveness"]["repair_success_criteria_metrics"] == [
        {
            "decision_scope": "kis",
            "criterion": "audit_contract_repaired_or_demoted",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
        }
    ]
    assert contract["repair_loop_effectiveness"]["repair_success_criteria_summary"] == {
        "metric_count": 1,
        "repair_required_count": 1,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_failed_criterion": "audit_contract_repaired_or_demoted",
        "top_failed_criteria": ["audit_contract_repaired_or_demoted"],
        "repair_learning_directives": [
            {
                "criterion": "audit_contract_repaired_or_demoted",
                "status": "repair_required",
                "recommended_action": (
                    "repair_or_demote_success_criterion_before_reuse"
                ),
                "missed_count": 2,
                "resolution_rate": 1 / 3,
            }
        ],
        "max_missed_count": 2,
        "max_sample_count": 3,
        "min_resolution_rate": 1 / 3,
    }
    assert contract["repair_loop_effectiveness"]["repair_learning_directive_metrics"] == [
        {
            "decision_scope": "kis",
            "recommended_action": "repair_or_demote_success_criterion_before_reuse",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
        }
    ]
    assert contract["repair_loop_effectiveness"]["repair_learning_directive_summary"] == {
        "metric_count": 1,
        "repair_required_count": 1,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_recommended_action": (
            "repair_or_demote_success_criterion_before_reuse"
        ),
        "repair_required_actions": [
            "repair_or_demote_success_criterion_before_reuse"
        ],
        "top_missed_actions": [
            "repair_or_demote_success_criterion_before_reuse"
        ],
        "action_targets": [
            {
                "decision_scope": "kis",
                "status": "repair_required",
                "recommended_action": (
                    "repair_or_demote_success_criterion_before_reuse"
                ),
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "miss_rate": 2 / 3,
                "repair_pressure_score": round(2 * (2 / 3), 6),
                "recommended_resolution": (
                    "revise_learning_directive_then_probe"
                ),
                "resolution_steps": [
                    "inspect_failed_repair_directive_outcomes",
                    "revise_or_demote_learning_directive",
                    "record_next_outcome_before_reuse",
                ],
                "metric_count": 1,
            }
        ],
        "max_missed_count": 2,
        "max_sample_count": 3,
        "min_resolution_rate": 1 / 3,
    }
    assert contract["repair_loop_effectiveness"]["repair_learning_step_metrics"] == [
        {
            "decision_scope": "kis",
            "resolution_step": "inspect_failed_repair_directive_outcomes",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
        }
    ]
    assert contract["repair_loop_effectiveness"]["repair_learning_step_summary"] == {
        "metric_count": 1,
        "repair_required_count": 1,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_resolution_step": "inspect_failed_repair_directive_outcomes",
        "repair_required_steps": ["inspect_failed_repair_directive_outcomes"],
        "top_missed_steps": ["inspect_failed_repair_directive_outcomes"],
        "step_targets": [
            {
                "decision_scope": "kis",
                "status": "repair_required",
                "resolution_step": "inspect_failed_repair_directive_outcomes",
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "miss_rate": 2 / 3,
                "repair_pressure_score": round(2 * (2 / 3), 6),
                "recommended_resolution": "revise_repair_step_contract_then_probe",
                "resolution_steps": [
                    "inspect_failed_resolution_step_outcomes",
                    "revise_repair_step_contract",
                    "record_next_outcome_before_reuse",
                ],
                "metric_count": 1,
            }
        ],
        "max_missed_count": 2,
        "max_sample_count": 3,
        "min_resolution_rate": 1 / 3,
    }
    assert contract["repair_loop_effectiveness"]["repair_learning_resolution_metrics"] == [
        {
            "decision_scope": "kis",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
        }
    ]
    assert contract["repair_loop_effectiveness"]["repair_learning_resolution_summary"] == {
        "metric_count": 1,
        "repair_required_count": 1,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_recommended_resolution": "revise_repair_step_contract_then_probe",
        "repair_required_resolutions": ["revise_repair_step_contract_then_probe"],
        "top_missed_resolutions": ["revise_repair_step_contract_then_probe"],
        "resolution_targets": [
            {
                "decision_scope": "kis",
                "status": "repair_required",
                "recommended_resolution": "revise_repair_step_contract_then_probe",
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "miss_rate": 2 / 3,
                "repair_pressure_score": round(2 * (2 / 3), 6),
                "next_review_steps": [
                    "inspect_failed_resolution_strategy_outcomes",
                    "revise_resolution_strategy_contract",
                    "record_next_outcome_before_reuse",
                ],
                "metric_count": 1,
            }
        ],
        "max_missed_count": 2,
        "max_sample_count": 3,
        "min_resolution_rate": 1 / 3,
    }


def test_repair_contract_guides_closed_block_horizon_reprojection() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_required_count": 1,
                "repair_loop_status_metrics": [
                    {
                        "decision_scope": "kis",
                        "repair_loop_status": "repair_required",
                        "priority_type": "wiki_application_coverage",
                        "action_type": "reproject_closed_block_outcome_horizons",
                        "decision_use": "horizon_lane_attribution_repair",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "quality_warnings": [
                            "closed_block_outcome_horizon_missing"
                        ],
                        "impacted_page_ids": [
                            "kis.application.closed_block_outcomes"
                        ],
                    }
                ],
            },
        }
    )

    target = contract["repair_loop_effectiveness"]["repair_loop_status_summary"][
        "repair_action_targets"
    ][0]
    assert target["action_type"] == "reproject_closed_block_outcome_horizons"
    assert target["recommended_resolution"] == (
        "reproject_closed_block_outcomes_then_refresh_effectiveness"
    )
    assert target["resolution_steps"] == [
        "derive_block_horizon_or_crypto_lane",
        "reproject_selection_outcomes_with_horizon",
        "refresh_page_effectiveness_by_horizon",
    ]
    assert target["resolution_success_criteria"] == [
        "closed_block_outcomes_have_horizon_or_lane",
        "page_effectiveness_separated_by_horizon",
        "future_manager_prompt_uses_horizon_specific_effectiveness",
    ]


def test_repair_loop_status_summary_omits_missing_metrics_but_keeps_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_loop_status_metrics": [
                {
                    "decision_scope": "kis",
                    "repair_loop_status": "repair_required",
                    "priority_type": "wiki_application_coverage",
                    "action_type": "reproject_closed_block_outcome_horizons",
                    "decision_use": "horizon_lane_attribution_repair",
                    "status": "repair_required",
                }
            ],
        }
    )

    status_summary = missing["repair_loop_status_summary"]
    for key in ("max_missed_count", "max_sample_count", "min_resolution_rate"):
        assert key not in status_summary
    target = status_summary["repair_action_targets"][0]
    for key in (
        "sample_count",
        "missed_count",
        "resolved_count",
        "resolution_rate",
        "miss_rate",
        "repair_pressure_score",
    ):
        assert key not in target

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_loop_status_metrics": [
                {
                    "decision_scope": "kis",
                    "repair_loop_status": "repair_required",
                    "priority_type": "wiki_application_coverage",
                    "action_type": "reproject_closed_block_outcome_horizons",
                    "decision_use": "horizon_lane_attribution_repair",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "repair_required",
                }
            ],
        }
    )

    explicit_summary = explicit_zero["repair_loop_status_summary"]
    assert explicit_summary["max_missed_count"] == 0
    assert explicit_summary["max_sample_count"] == 0
    assert explicit_summary["min_resolution_rate"] == 0.0
    explicit_target = explicit_summary["repair_action_targets"][0]
    assert explicit_target["sample_count"] == 0
    assert explicit_target["missed_count"] == 0
    assert explicit_target["resolved_count"] == 0
    assert explicit_target["resolution_rate"] == 0.0
    assert explicit_target["miss_rate"] == 0.0
    assert explicit_target["repair_pressure_score"] == 0.0


def test_repair_loop_probe_action_targets_require_present_missed_count_but_keep_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_loop_status_metrics": [
                {
                    "decision_scope": "kis",
                    "repair_loop_status": "probe",
                    "action_type": "refresh_symbol_financials",
                    "status": "probe",
                }
            ],
        }
    )

    assert "repair_action_targets" not in missing["repair_loop_status_summary"]

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_loop_status_metrics": [
                {
                    "decision_scope": "kis",
                    "repair_loop_status": "probe",
                    "action_type": "refresh_symbol_financials",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                }
            ],
        }
    )

    target = explicit_zero["repair_loop_status_summary"]["repair_action_targets"][0]
    assert target["action_type"] == "refresh_symbol_financials"
    assert target["sample_count"] == 0
    assert target["missed_count"] == 0
    assert target["resolved_count"] == 0
    assert target["resolution_rate"] == 0.0


def test_repair_loop_compact_reconstructs_steps_from_action_targets() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_learning_directive_summary": {
                "action_targets": [
                    {
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "recommended_action": (
                            "repair_or_demote_success_criterion_before_reuse"
                        ),
                        "sample_count": 4,
                        "missed_count": 3,
                        "resolved_count": 1,
                        "resolution_rate": 0.25,
                        "miss_rate": 0.75,
                        "repair_pressure_score": 2.25,
                        "recommended_resolution": (
                            "revise_learning_directive_then_probe"
                        ),
                        "resolution_steps": [
                            "inspect_failed_repair_directive_outcomes",
                            "revise_or_demote_learning_directive",
                        ],
                        "metric_count": 1,
                    }
                ]
            }
        }
    )

    assert summary["repair_learning_step_metrics"] == [
        {
            "decision_scope": "kis",
            "resolution_step": "inspect_failed_repair_directive_outcomes",
            "sample_count": 4,
            "missed_count": 3,
            "resolved_count": 1,
            "resolution_rate": 0.25,
            "status": "repair_required",
        },
        {
            "decision_scope": "kis",
            "resolution_step": "revise_or_demote_learning_directive",
            "sample_count": 4,
            "missed_count": 3,
            "resolved_count": 1,
            "resolution_rate": 0.25,
            "status": "repair_required",
        },
    ]
    assert summary["repair_learning_step_summary"]["primary_resolution_step"] == (
        "inspect_failed_repair_directive_outcomes"
    )


def test_repair_loop_effectiveness_omits_missing_metrics_but_keeps_explicit_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "status": "active",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "priority_type": "evidence_quality",
                    "status": "active",
                    "source_id": "005930:financials",
                }
            ],
        }
    )

    for key in (
        "sample_count",
        "missed_count",
        "resolved_count",
        "resolution_rate",
        "metric_count",
        "repair_required_count",
    ):
        assert key not in missing
    degraded = missing["top_degraded"][0]
    for key in ("sample_count", "missed_count", "resolved_count", "resolution_rate"):
        assert key not in degraded

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "status": "active",
            "sample_count": 0,
            "missed_count": 0,
            "resolved_count": 0,
            "resolution_rate": 0.0,
            "metric_count": 0,
            "repair_required_count": 0,
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "priority_type": "evidence_quality",
                    "status": "active",
                    "source_id": "005930:financials",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                }
            ],
        }
    )

    assert explicit_zero["sample_count"] == 0
    assert explicit_zero["missed_count"] == 0
    assert explicit_zero["resolved_count"] == 0
    assert explicit_zero["resolution_rate"] == 0.0
    assert explicit_zero["metric_count"] == 0
    assert explicit_zero["repair_required_count"] == 0
    explicit_degraded = explicit_zero["top_degraded"][0]
    assert explicit_degraded["sample_count"] == 0
    assert explicit_degraded["missed_count"] == 0
    assert explicit_degraded["resolved_count"] == 0
    assert explicit_degraded["resolution_rate"] == 0.0


def test_repair_loop_compact_reconstructs_resolution_from_step_targets() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_learning_step_summary": {
                "step_targets": [
                    {
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "resolution_step": "inspect_failed_repair_directive_outcomes",
                        "sample_count": 4,
                        "missed_count": 3,
                        "resolved_count": 1,
                        "resolution_rate": 0.25,
                        "miss_rate": 0.75,
                        "repair_pressure_score": 2.25,
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "metric_count": 1,
                    }
                ]
            }
        }
    )

    assert summary["repair_learning_resolution_metrics"] == [
        {
            "decision_scope": "kis",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "sample_count": 4,
            "missed_count": 3,
            "resolved_count": 1,
            "resolution_rate": 0.25,
            "status": "repair_required",
        }
    ]
    assert summary["repair_learning_resolution_summary"]["resolution_targets"] == [
        {
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "sample_count": 4,
            "missed_count": 3,
            "resolved_count": 1,
            "resolution_rate": 0.25,
            "miss_rate": 0.75,
            "repair_pressure_score": 2.25,
            "next_review_steps": [
                "inspect_failed_resolution_strategy_outcomes",
                "revise_resolution_strategy_contract",
                "record_next_outcome_before_reuse",
            ],
            "metric_count": 1,
        }
    ]


def test_repair_learning_summaries_omit_missing_metrics_but_keep_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_learning_directive_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_action": (
                        "repair_or_demote_success_criterion_before_reuse"
                    ),
                    "status": "probe",
                }
            ],
            "repair_learning_step_metrics": [
                {
                    "decision_scope": "kis",
                    "resolution_step": "inspect_failed_repair_directive_outcomes",
                    "status": "probe",
                }
            ],
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "status": "probe",
                }
            ],
        }
    )

    for section, target_key in (
        ("repair_learning_directive_summary", "action_targets"),
        ("repair_learning_step_summary", "step_targets"),
        ("repair_learning_resolution_summary", "resolution_targets"),
    ):
        section_summary = missing[section]
        for key in ("max_missed_count", "max_sample_count", "min_resolution_rate"):
            assert key not in section_summary
        target = section_summary[target_key][0]
        for key in (
            "sample_count",
            "missed_count",
            "resolved_count",
            "resolution_rate",
            "miss_rate",
            "repair_pressure_score",
        ):
            assert key not in target

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_learning_directive_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_action": (
                        "repair_or_demote_success_criterion_before_reuse"
                    ),
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                }
            ],
            "repair_learning_step_metrics": [
                {
                    "decision_scope": "kis",
                    "resolution_step": "inspect_failed_repair_directive_outcomes",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                }
            ],
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                }
            ],
        }
    )

    for section, target_key in (
        ("repair_learning_directive_summary", "action_targets"),
        ("repair_learning_step_summary", "step_targets"),
        ("repair_learning_resolution_summary", "resolution_targets"),
    ):
        section_summary = explicit_zero[section]
        assert section_summary["max_missed_count"] == 0
        assert section_summary["max_sample_count"] == 0
        assert section_summary["min_resolution_rate"] == 0.0
        target = section_summary[target_key][0]
        assert target["sample_count"] == 0
        assert target["missed_count"] == 0
        assert target["resolved_count"] == 0
        assert target["resolution_rate"] == 0.0
        assert target["miss_rate"] == 0.0
        assert target["repair_pressure_score"] == 0.0


def test_top_missed_summaries_require_present_missed_count_but_keep_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_loop_status_metrics": [
                {
                    "decision_scope": "kis",
                    "action_type": "refresh_symbol_financials",
                    "status": "probe",
                }
            ],
            "repair_learning_directive_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_action": (
                        "repair_or_demote_success_criterion_before_reuse"
                    ),
                    "status": "probe",
                }
            ],
            "repair_learning_step_metrics": [
                {
                    "decision_scope": "kis",
                    "resolution_step": "inspect_failed_repair_directive_outcomes",
                    "status": "probe",
                }
            ],
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "status": "probe",
                }
            ],
        }
    )

    assert "top_missed_action_types" not in missing["repair_loop_status_summary"]
    assert "top_missed_actions" not in missing["repair_learning_directive_summary"]
    assert "top_missed_steps" not in missing["repair_learning_step_summary"]
    assert (
        "top_missed_resolutions"
        not in missing["repair_learning_resolution_summary"]
    )

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_loop_status_metrics": [
                {
                    "decision_scope": "kis",
                    "action_type": "refresh_symbol_financials",
                    "missed_count": 0,
                    "status": "probe",
                }
            ],
            "repair_learning_directive_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_action": (
                        "repair_or_demote_success_criterion_before_reuse"
                    ),
                    "missed_count": 0,
                    "status": "probe",
                }
            ],
            "repair_learning_step_metrics": [
                {
                    "decision_scope": "kis",
                    "resolution_step": "inspect_failed_repair_directive_outcomes",
                    "missed_count": 0,
                    "status": "probe",
                }
            ],
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "missed_count": 0,
                    "status": "probe",
                }
            ],
        }
    )

    assert explicit_zero["repair_loop_status_summary"]["top_missed_action_types"] == [
        "refresh_symbol_financials"
    ]
    assert explicit_zero["repair_learning_directive_summary"][
        "top_missed_actions"
    ] == ["repair_or_demote_success_criterion_before_reuse"]
    assert explicit_zero["repair_learning_step_summary"]["top_missed_steps"] == [
        "inspect_failed_repair_directive_outcomes"
    ]
    assert explicit_zero["repair_learning_resolution_summary"][
        "top_missed_resolutions"
    ] == ["revise_repair_step_contract_then_probe"]


def test_repair_success_criteria_summary_omits_missing_metrics_but_keeps_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_success_criteria_metrics": [
                {
                    "decision_scope": "kis",
                    "criterion": "audit_contract_repaired_or_demoted",
                    "status": "probe",
                }
            ],
        }
    )

    criteria = missing["repair_success_criteria_metrics"][0]
    for key in ("sample_count", "missed_count", "resolved_count", "resolution_rate"):
        assert key not in criteria
    summary = missing["repair_success_criteria_summary"]
    for key in ("max_missed_count", "max_sample_count", "min_resolution_rate"):
        assert key not in summary
    directive = summary["repair_learning_directives"][0]
    assert directive["criterion"] == "audit_contract_repaired_or_demoted"
    for key in ("missed_count", "resolution_rate"):
        assert key not in directive

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_success_criteria_metrics": [
                {
                    "decision_scope": "kis",
                    "criterion": "audit_contract_repaired_or_demoted",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                }
            ],
        }
    )

    explicit_criteria = explicit_zero["repair_success_criteria_metrics"][0]
    assert explicit_criteria["sample_count"] == 0
    assert explicit_criteria["missed_count"] == 0
    assert explicit_criteria["resolved_count"] == 0
    assert explicit_criteria["resolution_rate"] == 0.0
    explicit_summary = explicit_zero["repair_success_criteria_summary"]
    assert explicit_summary["max_missed_count"] == 0
    assert explicit_summary["max_sample_count"] == 0
    assert explicit_summary["min_resolution_rate"] == 0.0
    explicit_directive = explicit_summary["repair_learning_directives"][0]
    assert explicit_directive["missed_count"] == 0
    assert explicit_directive["resolution_rate"] == 0.0


def test_repair_loop_compact_reconstructs_learning_chain_with_target_details() -> None:
    target_context = {
        "priority_types": ["repair_queue"],
        "primary_decision_use": "evidence_quality_cross_check",
        "decision_uses": ["evidence_quality_cross_check"],
        "source_id": "repair:financials",
        "quality_warnings": ["financials_missing"],
        "impacted_page_ids": ["kis.symbol.245450"],
        "impacted_symbols": ["245450"],
        "repair_targets": [
            {
                "page_id": "kis.symbol.245450",
                "symbol": "245450",
                "recommended_action": (
                    "refresh_symbol_financials_and_rewrite_page_evidence"
                ),
            }
        ],
        "repair_target_effectiveness": [
            {
                "page_id": "kis.symbol.245450",
                "status": "degraded",
                "sample_count": 5,
                "win_rate": 0.2,
                "expectancy": -0.04,
                "helpful_score": -0.1,
                "confidence": 0.7,
                "reasons": ["fallback_chain_failed_after_financial_refresh"],
            }
        ],
        "repair_target_effectiveness_statuses": ["degraded"],
    }
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_success_criteria_metrics": [
                {
                    "decision_scope": "kis",
                    "criterion": "audit_contract_repaired_or_demoted",
                    "priority_type": "repair_queue",
                    "action_type": "refresh_symbol_financials",
                    "decision_use": "evidence_quality_cross_check",
                    "source_id": "repair:financials",
                    "sample_count": 5,
                    "missed_count": 4,
                    "resolved_count": 1,
                    "resolution_rate": 0.2,
                    "status": "repair_required",
                    "quality_warnings": ["financials_missing"],
                    "impacted_page_ids": ["kis.symbol.245450"],
                    "impacted_symbols": ["245450"],
                    "repair_targets": target_context["repair_targets"],
                    "repair_target_effectiveness": target_context[
                        "repair_target_effectiveness"
                    ][0],
                }
            ],
            "repair_learning_directive_summary": {
                "action_targets": [
                    {
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "recommended_action": (
                            "repair_or_demote_success_criterion_before_reuse"
                        ),
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "resolution_steps": [
                            "inspect_failed_repair_directive_outcomes"
                        ],
                        **target_context,
                    }
                ]
            },
            "repair_learning_step_summary": {
                "step_targets": [
                    {
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "resolution_step": (
                            "inspect_failed_repair_directive_outcomes"
                        ),
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        **target_context,
                    }
                ]
            },
        }
    )

    directive_metric = summary["repair_learning_directive_metrics"][0]
    step_metric = summary["repair_learning_step_metrics"][0]
    resolution_metric = summary["repair_learning_resolution_metrics"][0]
    for row in (directive_metric, step_metric, resolution_metric):
        assert row["priority_type"] == "repair_queue"
        assert row["decision_use"] == "evidence_quality_cross_check"
        assert row["source_id"] == "repair:financials"
        assert row["quality_warnings"] == ["financials_missing"]
        assert row["impacted_page_ids"] == ["kis.symbol.245450"]
        assert row["impacted_symbols"] == ["245450"]
        assert row["repair_targets"] == target_context["repair_targets"]
        assert row["repair_target_effectiveness"] == (
            target_context["repair_target_effectiveness"][0]
        )


def test_repair_loop_compact_preserves_component_status_summary() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "component_status_summary": {
                "component_count": 1,
                "metric_count": 1,
                "repair_required_count": 1,
                "probe_count": 0,
                "active_count": 0,
                "unknown_count": 0,
                "worst_status": "repair_required",
                "repair_required_components": [
                    "repair_learning_resolution_metrics"
                ],
                "components": [
                    {
                        "component": "repair_learning_resolution_metrics",
                        "metric_count": 1,
                        "repair_required_count": 1,
                        "probe_count": 0,
                        "active_count": 0,
                        "unknown_count": 0,
                        "worst_status": "repair_required",
                    }
                ],
            }
        }
    )

    assert summary["component_status_summary"] == {
        "component_count": 1,
        "metric_count": 1,
        "repair_required_count": 1,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "repair_required_components": ["repair_learning_resolution_metrics"],
        "components": [
            {
                "component": "repair_learning_resolution_metrics",
                "metric_count": 1,
                "repair_required_count": 1,
                "probe_count": 0,
                "active_count": 0,
                "unknown_count": 0,
                "worst_status": "repair_required",
            }
        ],
    }


def test_repair_component_status_summary_omits_missing_metrics_but_keeps_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "component_status_summary": {
                "worst_status": "probe",
                "components": [
                    {
                        "component": "repair_learning_resolution_metrics",
                        "worst_status": "probe",
                    }
                ],
            }
        }
    )

    status_summary = missing["component_status_summary"]
    for key in (
        "component_count",
        "metric_count",
        "repair_required_count",
        "probe_count",
        "active_count",
        "unknown_count",
    ):
        assert key not in status_summary
    component = status_summary["components"][0]
    for key in (
        "metric_count",
        "repair_required_count",
        "probe_count",
        "active_count",
        "unknown_count",
    ):
        assert key not in component

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "component_status_summary": {
                "component_count": 0,
                "metric_count": 0,
                "repair_required_count": 0,
                "probe_count": 0,
                "active_count": 0,
                "unknown_count": 0,
                "worst_status": "probe",
                "components": [
                    {
                        "component": "repair_learning_resolution_metrics",
                        "metric_count": 0,
                        "repair_required_count": 0,
                        "probe_count": 0,
                        "active_count": 0,
                        "unknown_count": 0,
                        "worst_status": "probe",
                    }
                ],
            }
        }
    )

    explicit_summary = explicit_zero["component_status_summary"]
    assert explicit_summary["component_count"] == 0
    assert explicit_summary["metric_count"] == 0
    assert explicit_summary["repair_required_count"] == 0
    assert explicit_summary["probe_count"] == 0
    assert explicit_summary["active_count"] == 0
    assert explicit_summary["unknown_count"] == 0
    explicit_component = explicit_summary["components"][0]
    assert explicit_component["metric_count"] == 0
    assert explicit_component["repair_required_count"] == 0
    assert explicit_component["probe_count"] == 0
    assert explicit_component["active_count"] == 0
    assert explicit_component["unknown_count"] == 0


def test_repair_loop_compact_reconstructs_component_status_summary() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "status": "active",
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "sample_count": 3,
                    "missed_count": 2,
                    "resolved_count": 1,
                    "resolution_rate": 1 / 3,
                    "status": "repair_required",
                }
            ],
        }
    )

    assert summary["status"] == "repair_required"
    assert summary["component_status_summary"] == {
        "component_count": 1,
        "metric_count": 1,
        "repair_required_count": 1,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "repair_required_components": ["repair_learning_resolution_metrics"],
        "components": [
            {
                "component": "repair_learning_resolution_metrics",
                "metric_count": 1,
                "repair_required_count": 1,
                "probe_count": 0,
                "active_count": 0,
                "unknown_count": 0,
                "worst_status": "repair_required",
                "component_targets": [
                    {
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 3,
                        "missed_count": 2,
                        "resolved_count": 1,
                        "resolution_rate": 1 / 3,
                    }
                ],
            }
        ],
        "top_component_targets": [
            {
                "component": "repair_learning_resolution_metrics",
                "decision_scope": "kis",
                "status": "repair_required",
                "recommended_resolution": (
                    "revise_repair_step_contract_then_probe"
                ),
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
            }
        ],
        "repair_required_component_targets": [
            {
                "component": "repair_learning_resolution_metrics",
                "decision_scope": "kis",
                "status": "repair_required",
                "recommended_resolution": (
                    "revise_repair_step_contract_then_probe"
                ),
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
            }
        ],
    }


def test_repair_loop_compact_preserves_component_target_metrics() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_component_target_metrics": [
                {
                    "decision_scope": "kis",
                    "component": "repair_learning_resolution_metrics",
                    "target_status": "repair_required",
                    "priority_type": "repair_queue",
                    "action_type": "refresh_symbol_financials",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "sample_count": 3,
                    "missed_count": 2,
                    "resolved_count": 1,
                    "resolution_rate": 1 / 3,
                    "status": "repair_required",
                    "impacted_symbols": ["245450"],
                }
            ],
            "repair_component_target_summary": {
                "metric_count": 1,
                "repair_required_count": 1,
                "probe_count": 0,
                "active_count": 0,
                "unknown_count": 0,
                "worst_status": "repair_required",
                "primary_component_target": (
                    "repair_learning_resolution_metrics"
                ),
                "top_component_targets": [
                    "repair_learning_resolution_metrics"
                ],
                "max_missed_count": 2,
                "max_sample_count": 3,
                "min_resolution_rate": 1 / 3,
            },
        }
    )

    assert summary["status"] == "repair_required"
    assert summary["repair_component_target_metrics"] == [
        {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "target_status": "repair_required",
            "priority_type": "repair_queue",
            "priority_types": ["repair_queue"],
            "action_type": "refresh_symbol_financials",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "impacted_symbols": ["245450"],
        }
    ]
    assert summary["repair_component_target_summary"] == {
        "metric_count": 1,
        "repair_required_count": 1,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_component_target": "repair_learning_resolution_metrics",
        "top_component_targets": ["repair_learning_resolution_metrics"],
        "top_component_target_details": [
            {
                "component": "repair_learning_resolution_metrics",
                "decision_scope": "kis",
                "status": "repair_required",
                "target_status": "repair_required",
                "priority_type": "repair_queue",
                "priority_types": ["repair_queue"],
                "action_type": "refresh_symbol_financials",
                "recommended_resolution": (
                    "revise_repair_step_contract_then_probe"
                ),
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "impacted_symbols": ["245450"],
            }
        ],
        "repair_required_component_target_details": [
            {
                "component": "repair_learning_resolution_metrics",
                "decision_scope": "kis",
                "status": "repair_required",
                "target_status": "repair_required",
                "priority_type": "repair_queue",
                "priority_types": ["repair_queue"],
                "action_type": "refresh_symbol_financials",
                "recommended_resolution": (
                    "revise_repair_step_contract_then_probe"
                ),
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "impacted_symbols": ["245450"],
            }
        ],
        "primary_repair_required_component_target_detail": {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "target_status": "repair_required",
            "priority_type": "repair_queue",
            "priority_types": ["repair_queue"],
            "action_type": "refresh_symbol_financials",
            "recommended_resolution": (
                "revise_repair_step_contract_then_probe"
            ),
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "impacted_symbols": ["245450"],
        },
        "component_target_attention_plan": {
            "status": "repair_required",
            "repair_now": {
                "component": "repair_learning_resolution_metrics",
                "decision_scope": "kis",
                "status": "repair_required",
                "target_status": "repair_required",
                "priority_type": "repair_queue",
                "action_type": "refresh_symbol_financials",
                "recommended_resolution": (
                    "revise_repair_step_contract_then_probe"
                ),
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "impacted_symbols": ["245450"],
            },
        },
        "max_missed_count": 2,
        "max_sample_count": 3,
        "min_resolution_rate": 1 / 3,
    }
    assert summary["component_status_summary"]["repair_required_components"] == [
        "repair_component_target_metrics"
    ]


def test_repair_component_target_summary_omits_missing_metrics_but_keeps_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_component_target_summary": {
                "worst_status": "probe",
                "primary_component_target": "repair_learning_resolution_metrics",
                "top_component_target_details": [
                    {
                        "component": "repair_learning_resolution_metrics",
                        "decision_scope": "kis",
                        "status": "probe",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                    }
                ],
            },
        }
    )

    target_summary = missing["repair_component_target_summary"]
    for key in (
        "metric_count",
        "repair_required_count",
        "probe_count",
        "active_count",
        "unknown_count",
        "max_missed_count",
        "max_sample_count",
        "min_resolution_rate",
    ):
        assert key not in target_summary
    target_detail = target_summary["top_component_target_details"][0]
    for key in ("sample_count", "missed_count", "resolved_count", "resolution_rate"):
        assert key not in target_detail

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_component_target_summary": {
                "metric_count": 0,
                "repair_required_count": 0,
                "probe_count": 0,
                "active_count": 0,
                "unknown_count": 0,
                "max_missed_count": 0,
                "max_sample_count": 0,
                "min_resolution_rate": 0.0,
                "worst_status": "probe",
                "primary_component_target": "repair_learning_resolution_metrics",
                "top_component_target_details": [
                    {
                        "component": "repair_learning_resolution_metrics",
                        "decision_scope": "kis",
                        "status": "probe",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 0,
                        "missed_count": 0,
                        "resolved_count": 0,
                        "resolution_rate": 0.0,
                    }
                ],
            },
        }
    )

    explicit_summary = explicit_zero["repair_component_target_summary"]
    assert explicit_summary["metric_count"] == 0
    assert explicit_summary["repair_required_count"] == 0
    assert explicit_summary["probe_count"] == 0
    assert explicit_summary["active_count"] == 0
    assert explicit_summary["unknown_count"] == 0
    assert explicit_summary["max_missed_count"] == 0
    assert explicit_summary["max_sample_count"] == 0
    assert explicit_summary["min_resolution_rate"] == 0.0
    explicit_detail = explicit_summary["top_component_target_details"][0]
    assert explicit_detail["sample_count"] == 0
    assert explicit_detail["missed_count"] == 0
    assert explicit_detail["resolved_count"] == 0
    assert explicit_detail["resolution_rate"] == 0.0


def test_repair_component_target_metrics_summary_omits_missing_metrics_but_keeps_zero() -> None:
    missing = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_component_target_metrics": [
                {
                    "decision_scope": "kis",
                    "component": "repair_learning_resolution_metrics",
                    "status": "probe",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                }
            ],
        }
    )

    target_summary = missing["repair_component_target_summary"]
    for key in ("max_missed_count", "max_sample_count", "min_resolution_rate"):
        assert key not in target_summary
    target_detail = target_summary["top_component_target_details"][0]
    for key in (
        "sample_count",
        "missed_count",
        "resolved_count",
        "resolution_rate",
    ):
        assert key not in target_detail

    explicit_zero = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_component_target_metrics": [
                {
                    "decision_scope": "kis",
                    "component": "repair_learning_resolution_metrics",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                }
            ],
        }
    )

    explicit_summary = explicit_zero["repair_component_target_summary"]
    assert explicit_summary["max_missed_count"] == 0
    assert explicit_summary["max_sample_count"] == 0
    assert explicit_summary["min_resolution_rate"] == 0.0
    explicit_detail = explicit_summary["top_component_target_details"][0]
    assert explicit_detail["sample_count"] == 0
    assert explicit_detail["missed_count"] == 0
    assert explicit_detail["resolved_count"] == 0
    assert explicit_detail["resolution_rate"] == 0.0


def test_repair_loop_compact_splits_component_target_details_by_status() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "repair_component_target_metrics": [
                {
                    "decision_scope": "kis",
                    "component": "repair_learning_resolution_metrics",
                    "target_status": "repair_required",
                    "priority_type": "repair_queue",
                    "action_type": "refresh_symbol_financials",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "sample_count": 3,
                    "missed_count": 2,
                    "resolved_count": 1,
                    "resolution_rate": 1 / 3,
                    "status": "repair_required",
                    "quality_warnings": ["financials_missing"],
                    "impacted_page_ids": ["kis.symbol.245450"],
                    "impacted_symbols": ["245450"],
                    "repair_targets": [
                        {
                            "page_id": "kis.symbol.245450",
                            "symbol": "245450",
                            "recommended_action": "refresh_symbol_financials",
                        }
                    ],
                },
                {
                    "decision_scope": "kis",
                    "component": "repair_success_criteria_metrics",
                    "target_status": "probe",
                    "priority_type": "success_criteria",
                    "action_type": "record_outcome_basis",
                    "recommended_resolution": "collect_two_more_outcomes",
                    "sample_count": 1,
                    "missed_count": 1,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                    "quality_warnings": ["insufficient_outcome_samples"],
                    "impacted_page_ids": ["kis.symbol.005930"],
                    "impacted_symbols": ["005930"],
                    "repair_targets": [
                        {
                            "page_id": "kis.symbol.005930",
                            "symbol": "005930",
                            "recommended_action": "record_outcome_basis",
                        }
                    ],
                },
            ],
        }
    )

    target_summary = summary["repair_component_target_summary"]
    assert [
        row["component"]
        for row in target_summary["repair_required_component_target_details"]
    ] == ["repair_learning_resolution_metrics"]
    assert (
        target_summary["primary_repair_required_component_target_detail"]["component"]
        == "repair_learning_resolution_metrics"
    )
    assert [
        row["component"]
        for row in target_summary["probe_component_target_details"]
    ] == ["repair_success_criteria_metrics"]
    assert (
        target_summary["primary_probe_component_target_detail"]["component"]
        == "repair_success_criteria_metrics"
    )
    assert target_summary["component_target_attention_plan"] == {
        "status": "repair_required",
        "repair_now": {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "target_status": "repair_required",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "quality_warnings": ["financials_missing"],
            "impacted_page_ids": ["kis.symbol.245450"],
            "impacted_symbols": ["245450"],
            "repair_targets": [
                {
                    "page_id": "kis.symbol.245450",
                    "symbol": "245450",
                    "recommended_action": "refresh_symbol_financials",
                }
            ],
        },
        "probe_next": {
            "component": "repair_success_criteria_metrics",
            "decision_scope": "kis",
            "status": "probe",
            "target_status": "probe",
            "priority_type": "success_criteria",
            "action_type": "record_outcome_basis",
            "recommended_resolution": "collect_two_more_outcomes",
            "sample_count": 1,
            "missed_count": 1,
            "resolved_count": 0,
            "resolution_rate": 0.0,
            "quality_warnings": ["insufficient_outcome_samples"],
            "impacted_page_ids": ["kis.symbol.005930"],
            "impacted_symbols": ["005930"],
            "repair_targets": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbol": "005930",
                    "recommended_action": "record_outcome_basis",
                }
            ],
        },
    }


def test_repair_loop_component_summary_preserves_component_targets() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "status": "active",
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "priority_type": "repair_queue",
                    "action_type": "refresh_symbol_financials",
                    "decision_use": "evidence_quality_cross_check",
                    "source_id": "repair:financials",
                    "sample_count": 5,
                    "missed_count": 4,
                    "resolved_count": 1,
                    "resolution_rate": 0.2,
                    "status": "repair_required",
                    "quality_warnings": ["financials_missing"],
                    "impacted_page_ids": ["kis.symbol.245450"],
                    "impacted_symbols": ["245450"],
                    "repair_targets": [
                        {
                            "page_id": "kis.symbol.245450",
                            "symbol": "245450",
                            "recommended_action": (
                                "refresh_symbol_financials_and_rewrite_page_evidence"
                            ),
                        }
                    ],
                    "repair_target_effectiveness": {
                        "page_id": "kis.symbol.245450",
                        "status": "degraded",
                        "sample_count": 5,
                        "win_rate": 0.2,
                        "expectancy": -0.04,
                        "helpful_score": -0.1,
                        "confidence": 0.7,
                        "reasons": [
                            "component_target_failed_after_financial_refresh"
                        ],
                    },
                }
            ],
        }
    )

    component = summary["component_status_summary"]["components"][0]
    assert component["component"] == "repair_learning_resolution_metrics"
    assert component["component_targets"] == [
        {
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
            "priority_types": ["repair_queue"],
            "action_type": "refresh_symbol_financials",
            "primary_decision_use": "evidence_quality_cross_check",
            "decision_uses": ["evidence_quality_cross_check"],
            "source_id": "repair:financials",
            "sample_count": 5,
            "missed_count": 4,
            "resolved_count": 1,
            "resolution_rate": 0.2,
            "quality_warnings": ["financials_missing"],
            "impacted_page_ids": ["kis.symbol.245450"],
            "impacted_symbols": ["245450"],
            "repair_targets": [
                {
                    "page_id": "kis.symbol.245450",
                    "symbol": "245450",
                    "recommended_action": (
                        "refresh_symbol_financials_and_rewrite_page_evidence"
                    ),
                }
            ],
            "repair_target_effectiveness": [
                {
                    "page_id": "kis.symbol.245450",
                    "status": "degraded",
                    "sample_count": 5,
                    "win_rate": 0.2,
                    "expectancy": -0.04,
                    "helpful_score": -0.1,
                    "confidence": 0.7,
                    "reasons": [
                        "component_target_failed_after_financial_refresh"
                    ],
                }
            ],
            "repair_target_effectiveness_statuses": ["degraded"],
        }
    ]


def test_repair_loop_component_summary_promotes_top_component_targets() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "status": "active",
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "priority_type": "repair_queue",
                    "action_type": "refresh_symbol_financials",
                    "decision_use": "evidence_quality_cross_check",
                    "source_id": "repair:financials",
                    "sample_count": 5,
                    "missed_count": 4,
                    "resolved_count": 1,
                    "resolution_rate": 0.2,
                    "status": "repair_required",
                    "quality_warnings": ["financials_missing"],
                    "impacted_page_ids": ["kis.symbol.245450"],
                    "impacted_symbols": ["245450"],
                    "repair_targets": [
                        {
                            "page_id": "kis.symbol.245450",
                            "symbol": "245450",
                            "recommended_action": (
                                "refresh_symbol_financials_and_rewrite_page_evidence"
                            ),
                        }
                    ],
                }
            ],
        }
    )

    assert summary["component_status_summary"]["top_component_targets"] == [
        {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
            "priority_types": ["repair_queue"],
            "action_type": "refresh_symbol_financials",
            "primary_decision_use": "evidence_quality_cross_check",
            "decision_uses": ["evidence_quality_cross_check"],
            "source_id": "repair:financials",
            "sample_count": 5,
            "missed_count": 4,
            "resolved_count": 1,
            "resolution_rate": 0.2,
            "quality_warnings": ["financials_missing"],
            "impacted_page_ids": ["kis.symbol.245450"],
            "impacted_symbols": ["245450"],
            "repair_targets": [
                {
                    "page_id": "kis.symbol.245450",
                    "symbol": "245450",
                    "recommended_action": (
                        "refresh_symbol_financials_and_rewrite_page_evidence"
                    ),
                }
            ],
        }
    ]


def test_repair_loop_component_summary_splits_repair_and_probe_targets() -> None:
    summary = compact_jue_wiki_repair_loop_effectiveness_for_prompt(
        {
            "status": "active",
            "repair_learning_resolution_metrics": [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "priority_type": "repair_queue",
                    "action_type": "refresh_symbol_financials",
                    "sample_count": 5,
                    "missed_count": 4,
                    "resolved_count": 1,
                    "resolution_rate": 0.2,
                    "status": "repair_required",
                    "impacted_symbols": ["245450"],
                }
            ],
            "repair_success_criteria_metrics": [
                {
                    "decision_scope": "binance",
                    "criterion": "probe_live_authority_alignment",
                    "priority_type": "probe_queue",
                    "action_type": "probe_live_authority_gate",
                    "sample_count": 6,
                    "missed_count": 2,
                    "resolved_count": 4,
                    "resolution_rate": 2 / 3,
                    "status": "probe",
                    "impacted_symbols": ["NEARUSDT"],
                }
            ],
        }
    )

    component_summary = summary["component_status_summary"]
    assert component_summary["repair_required_component_targets"] == [
        {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
            "priority_types": ["repair_queue"],
            "action_type": "refresh_symbol_financials",
            "sample_count": 5,
            "missed_count": 4,
            "resolved_count": 1,
            "resolution_rate": 0.2,
            "impacted_symbols": ["245450"],
        }
    ]
    assert component_summary["probe_component_targets"] == [
        {
            "component": "repair_success_criteria_metrics",
            "decision_scope": "binance",
            "status": "probe",
            "priority_type": "probe_queue",
            "priority_types": ["probe_queue"],
            "action_type": "probe_live_authority_gate",
            "criterion": "probe_live_authority_alignment",
            "sample_count": 6,
            "missed_count": 2,
            "resolved_count": 4,
            "resolution_rate": 2 / 3,
            "impacted_symbols": ["NEARUSDT"],
        }
    ]
    assert all(
        row["status"] == "repair_required"
        for row in component_summary["repair_required_component_targets"]
    )
    assert all(
        row["status"] == "probe"
        for row in component_summary["probe_component_targets"]
    )


def test_repair_contract_prioritizes_repair_required_top_degraded() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "evidence_quality",
                    "symbols": ["005930"],
                    "source_type": "symbol_fundamentals",
                    "source_id": "005930:financials",
                    "action_type": "refresh_symbol_financials",
                    "quality_status": "weak",
                    "quality_warnings": ["financials_missing"],
                    "repair_action": "refresh financial evidence",
                    "reasons": ["warning:financials_missing"],
                }
            ],
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 24,
                "missed_count": 10,
                "resolved_count": 14,
                "resolution_rate": 14 / 24,
                "repair_required_count": 1,
                "top_degraded": [
                    {
                        "decision_scope": "kis",
                        "priority_type": "evidence_quality",
                        "action_type": f"aaa_active_{idx}",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": f"active:{idx}",
                        "sample_count": 6,
                        "missed_count": 1,
                        "resolved_count": 5,
                        "resolution_rate": 5 / 6,
                        "status": "active",
                    }
                    for idx in range(5)
                ]
                + [
                    {
                        "decision_scope": "kis",
                        "priority_type": "evidence_quality",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "005930:financials",
                        "sample_count": 9,
                        "missed_count": 8,
                        "resolved_count": 1,
                        "resolution_rate": 1 / 9,
                        "status": "repair_required",
                    }
                ],
            },
        }
    )

    degraded = contract["repair_loop_effectiveness"]["top_degraded"]
    assert degraded[0]["action_type"] == "refresh_symbol_financials"
    assert degraded[0]["status"] == "repair_required"


def test_repair_contract_preserves_top_degraded_quality_warnings() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "top_degraded": [
                    {
                        "decision_scope": "kis",
                        "priority_type": "repair_queue",
                        "action_type": "repair_usage_guidance_contract",
                        "decision_use": "usage_guidance_effectiveness_repair",
                        "source_id": "repair:usage-guidance",
                        "sample_count": 3,
                        "missed_count": 2,
                        "resolved_count": 1,
                        "resolution_rate": 1 / 3,
                        "status": "repair_required",
                        "quality_warnings": ["usage_guidance_degraded"],
                    }
                ],
            },
        }
    )

    degraded = contract["repair_loop_effectiveness"]["top_degraded"]
    assert degraded[0]["quality_warnings"] == ["usage_guidance_degraded"]


def test_repair_contract_exposes_component_target_attention_plan() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 4,
                "missed_count": 3,
                "resolved_count": 1,
                "resolution_rate": 0.25,
                "repair_component_target_metrics": [
                    {
                        "decision_scope": "kis",
                        "component": "repair_learning_resolution_metrics",
                        "target_status": "repair_required",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 4,
                        "missed_count": 3,
                        "resolved_count": 1,
                        "resolution_rate": 0.25,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                    },
                    {
                        "decision_scope": "kis",
                        "component": "repair_success_criteria_metrics",
                        "target_status": "probe",
                        "priority_type": "success_criteria",
                        "action_type": "record_outcome_basis",
                        "recommended_resolution": "collect_two_more_outcomes",
                        "sample_count": 1,
                        "missed_count": 1,
                        "resolved_count": 0,
                        "resolution_rate": 0.0,
                        "status": "probe",
                        "quality_warnings": ["insufficient_outcome_samples"],
                        "impacted_page_ids": ["kis.symbol.005930"],
                        "impacted_symbols": ["005930"],
                    },
                ],
            },
        }
    )

    assert contract["component_target_attention_plan"] == {
        "status": "repair_required",
        "repair_now": {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "target_status": "repair_required",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "sample_count": 4,
            "missed_count": 3,
            "resolved_count": 1,
            "resolution_rate": 0.25,
            "quality_warnings": ["financials_missing"],
            "impacted_page_ids": ["kis.symbol.245450"],
            "impacted_symbols": ["245450"],
        },
        "probe_next": {
            "component": "repair_success_criteria_metrics",
            "decision_scope": "kis",
            "status": "probe",
            "target_status": "probe",
            "priority_type": "success_criteria",
            "action_type": "record_outcome_basis",
            "recommended_resolution": "collect_two_more_outcomes",
            "sample_count": 1,
            "missed_count": 1,
            "resolved_count": 0,
            "resolution_rate": 0.0,
            "quality_warnings": ["insufficient_outcome_samples"],
            "impacted_page_ids": ["kis.symbol.005930"],
            "impacted_symbols": ["005930"],
        },
    }
    assert contract["attention_plan_response_contract"] == {
        "status": "active",
        "must_address": ["repair_now", "probe_next"],
        "accepted_response_locations": [
            "create_blocks[].metadata.jue_wiki_repair_attention",
            "update_blocks[].metadata.jue_wiki_repair_attention",
            "close_blocks[].metadata.jue_wiki_repair_attention",
            "hold_decision.reasons",
            "hold_decision.data_gaps",
            "hold_decision.next_triggers",
        ],
        "repair_now": {
            "component": "repair_learning_resolution_metrics",
            "action_type": "refresh_symbol_financials",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "quality_warnings": ["financials_missing"],
            "impacted_page_ids": ["kis.symbol.245450"],
            "impacted_symbols": ["245450"],
        },
        "probe_next": {
            "component": "repair_success_criteria_metrics",
            "action_type": "record_outcome_basis",
            "recommended_resolution": "collect_two_more_outcomes",
            "quality_warnings": ["insufficient_outcome_samples"],
            "impacted_page_ids": ["kis.symbol.005930"],
            "impacted_symbols": ["005930"],
        },
        "accepted_resolutions": [
            "action_metadata_records_repair_attention",
            "hold_decision_names_missing_evidence_and_next_trigger",
            "explicit_candidate_reject_with_repair_reason",
            "defer_due_to_safety_gate_with_repair_context",
        ],
        "hard_blocker": False,
    }


def test_repair_contract_merges_memory_card_quality_with_existing_attention_plan() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "memory_card_quality",
                    "source_type": "jue_wiki_memory_card_quality",
                    "source_id": "kis.symbol.005930:manager_run:11",
                    "symbols": ["005930"],
                    "action_type": "cross_check_memory_card_quality",
                    "repair_status": "unresolved",
                    "quality_warnings": ["memory_card_quality_unresolved"],
                    "repair_action": (
                        "cross_check_live_research_before_high_confidence"
                    ),
                    "impacted_page_ids": ["kis.symbol.005930"],
                    "impacted_symbols": ["005930"],
                }
            ],
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 4,
                "missed_count": 3,
                "resolved_count": 1,
                "resolution_rate": 0.25,
                "repair_component_target_metrics": [
                    {
                        "decision_scope": "kis",
                        "component": "repair_learning_resolution_metrics",
                        "target_status": "repair_required",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 4,
                        "missed_count": 3,
                        "resolved_count": 1,
                        "resolution_rate": 0.25,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                    }
                ],
            },
        }
    )

    assert contract["component_target_attention_plan"]["repair_now"][
        "component"
    ] == "repair_learning_resolution_metrics"
    assert contract["component_target_attention_plan"]["probe_next"] == {
        "component": "memory_card_quality",
        "decision_scope": "kis",
        "status": "repair_required",
        "target_status": "unresolved",
        "priority_type": "memory_card_quality",
        "action_type": "cross_check_memory_card_quality",
        "recommended_resolution": (
            "cross_check_live_research_before_high_confidence"
        ),
        "sample_count": 1,
        "missed_count": 1,
        "resolved_count": 0,
        "resolution_rate": 0.0,
        "quality_warnings": ["memory_card_quality_unresolved"],
        "impacted_page_ids": ["kis.symbol.005930"],
        "impacted_symbols": ["005930"],
    }
    assert contract["attention_plan_response_contract"]["must_address"] == [
        "repair_now",
        "probe_next",
    ]
    assert contract["attention_plan_response_contract"]["probe_next"] == {
        "component": "memory_card_quality",
        "action_type": "cross_check_memory_card_quality",
        "recommended_resolution": (
            "cross_check_live_research_before_high_confidence"
        ),
        "quality_warnings": ["memory_card_quality_unresolved"],
        "impacted_page_ids": ["kis.symbol.005930"],
        "impacted_symbols": ["005930"],
    }


def test_repair_contract_keeps_memory_card_quality_as_additional_attention() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "memory_card_quality",
                    "source_type": "jue_wiki_memory_card_quality",
                    "source_id": "kis.symbol.005930:manager_run:11",
                    "symbols": ["005930"],
                    "action_type": "cross_check_memory_card_quality",
                    "repair_status": "unresolved",
                    "quality_warnings": ["memory_card_quality_unresolved"],
                    "repair_action": (
                        "cross_check_live_research_before_high_confidence"
                    ),
                    "impacted_page_ids": ["kis.symbol.005930"],
                    "impacted_symbols": ["005930"],
                }
            ],
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_component_target_metrics": [
                    {
                        "decision_scope": "kis",
                        "component": "repair_learning_resolution_metrics",
                        "target_status": "repair_required",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                    },
                    {
                        "decision_scope": "kis",
                        "component": "repair_success_criteria_metrics",
                        "target_status": "probe",
                        "priority_type": "success_criteria",
                        "action_type": "record_outcome_basis",
                        "recommended_resolution": "collect_two_more_outcomes",
                        "sample_count": 1,
                        "missed_count": 1,
                        "resolved_count": 0,
                        "resolution_rate": 0.0,
                        "status": "probe",
                        "quality_warnings": ["insufficient_outcome_samples"],
                        "impacted_page_ids": ["kis.symbol.000660"],
                        "impacted_symbols": ["000660"],
                    },
                ],
            },
        }
    )

    assert contract["component_target_attention_plan"]["additional_attention"] == [
        {
            "component": "memory_card_quality",
            "decision_scope": "kis",
            "status": "repair_required",
            "target_status": "unresolved",
            "priority_type": "memory_card_quality",
            "action_type": "cross_check_memory_card_quality",
            "recommended_resolution": (
                "cross_check_live_research_before_high_confidence"
            ),
            "sample_count": 1,
            "missed_count": 1,
            "resolved_count": 0,
            "resolution_rate": 0.0,
            "quality_warnings": ["memory_card_quality_unresolved"],
            "impacted_page_ids": ["kis.symbol.005930"],
            "impacted_symbols": ["005930"],
        }
    ]
    assert contract["attention_plan_response_contract"]["must_address"] == [
        "repair_now",
        "probe_next",
        "additional_attention",
    ]
    assert contract["attention_plan_response_contract"]["additional_attention"] == [
        {
            "component": "memory_card_quality",
            "action_type": "cross_check_memory_card_quality",
            "recommended_resolution": (
                "cross_check_live_research_before_high_confidence"
            ),
            "quality_warnings": ["memory_card_quality_unresolved"],
            "impacted_page_ids": ["kis.symbol.005930"],
            "impacted_symbols": ["005930"],
        }
    ]


def test_repair_contract_derives_attention_plan_from_wiki_attention_priority() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "page_type": "symbol",
                    "priority_type": "wiki_attention",
                    "source_type": "jue_wiki_attention",
                    "source_id": "binance.symbol.ETHUSDT:manager_run:9",
                    "symbols": ["ETHUSDT"],
                    "action_type": "live_probe",
                    "repair_status": "unresolved",
                    "quality_warnings": ["wiki_attention_unresolved"],
                    "repair_action": (
                        "spot_underuse_live_probe: 현물 미사용 문제를 "
                        "실행 가능한 spot 대기블록으로 검증한다."
                    ),
                    "impacted_page_ids": ["binance.symbol.ETHUSDT"],
                    "impacted_symbols": ["ETHUSDT"],
                    "repair_targets": [
                        {
                            "page_id": "binance.symbol.ETHUSDT",
                            "symbol": "ETHUSDT",
                            "recommended_action": "live_probe",
                        }
                    ],
                }
            ]
        }
    )

    assert contract["attention_plan_response_contract"] == {
        "status": "active",
        "must_address": ["repair_now"],
        "accepted_response_locations": [
            "create_blocks[].metadata.jue_wiki_repair_attention",
            "update_blocks[].metadata.jue_wiki_repair_attention",
            "close_blocks[].metadata.jue_wiki_repair_attention",
            "hold_decision.reasons",
            "hold_decision.data_gaps",
            "hold_decision.next_triggers",
        ],
        "repair_now": {
            "component": "wiki_attention",
            "action_type": "live_probe",
            "recommended_resolution": (
                "spot_underuse_live_probe: 현물 미사용 문제를 "
                "실행 가능한 spot 대기블록으로 검증한다."
            ),
            "quality_warnings": ["wiki_attention_unresolved"],
            "impacted_page_ids": ["binance.symbol.ETHUSDT"],
            "impacted_symbols": ["ETHUSDT"],
        },
        "accepted_resolutions": [
            "action_metadata_records_repair_attention",
            "hold_decision_names_missing_evidence_and_next_trigger",
            "explicit_candidate_reject_with_repair_reason",
            "defer_due_to_safety_gate_with_repair_context",
        ],
        "hard_blocker": False,
    }


def test_repair_contract_derives_attention_plan_from_memory_card_quality_priority() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "memory_card_quality",
                    "source_type": "jue_wiki_memory_card_quality",
                    "source_id": "kis.symbol.005930:manager_run:11",
                    "symbols": ["005930"],
                    "symbol_overlap": ["005930"],
                    "action_type": "cross_check_memory_card_quality",
                    "repair_status": "unresolved",
                    "quality_warnings": ["memory_card_quality_unresolved"],
                    "repair_action": (
                        "cross_check_live_research_before_high_confidence"
                    ),
                    "impacted_page_ids": ["kis.symbol.005930"],
                    "impacted_symbols": ["005930"],
                    "candidate_resolution_required": True,
                }
            ]
        }
    )

    assert contract["attention_plan_response_contract"] == {
        "status": "active",
        "must_address": ["repair_now"],
        "accepted_response_locations": [
            "create_blocks[].metadata.jue_wiki_repair_attention",
            "update_blocks[].metadata.jue_wiki_repair_attention",
            "close_blocks[].metadata.jue_wiki_repair_attention",
            "hold_decision.reasons",
            "hold_decision.data_gaps",
            "hold_decision.next_triggers",
        ],
        "repair_now": {
            "component": "memory_card_quality",
            "action_type": "cross_check_memory_card_quality",
            "recommended_resolution": (
                "cross_check_live_research_before_high_confidence"
            ),
            "quality_warnings": ["memory_card_quality_unresolved"],
            "impacted_page_ids": ["kis.symbol.005930"],
            "impacted_symbols": ["005930"],
        },
        "accepted_resolutions": [
            "action_metadata_records_repair_attention",
            "hold_decision_names_missing_evidence_and_next_trigger",
            "explicit_candidate_reject_with_repair_reason",
            "defer_due_to_safety_gate_with_repair_context",
        ],
        "hard_blocker": False,
    }


def test_repair_contract_top_degraded_preserves_target_details() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "top_degraded": [
                    {
                        "decision_scope": "kis",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                        "repair_target_effectiveness": {
                            "page_id": "kis.symbol.245450",
                            "status": "degraded",
                            "sample_count": 5,
                            "win_rate": 0.2,
                            "expectancy": -0.04,
                            "helpful_score": -0.1,
                            "confidence": 0.7,
                            "reasons": [
                                "financial_refresh_repeatedly_failed_to_improve_entry_quality"
                            ],
                        },
                    }
                ],
            },
        }
    )

    degraded = contract["repair_loop_effectiveness"]["top_degraded"][0]
    assert degraded["impacted_page_ids"] == ["kis.symbol.245450"]
    assert degraded["impacted_symbols"] == ["245450"]
    assert degraded["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert degraded["repair_target_effectiveness"] == {
        "page_id": "kis.symbol.245450",
        "status": "degraded",
        "sample_count": 5,
        "win_rate": 0.2,
        "expectancy": -0.04,
        "helpful_score": -0.1,
        "confidence": 0.7,
        "reasons": [
            "financial_refresh_repeatedly_failed_to_improve_entry_quality"
        ],
    }


def test_repair_contract_status_metrics_preserve_target_details() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_loop_status_metrics": [
                    {
                        "decision_scope": "kis",
                        "repair_loop_status": "repair_required",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                        "repair_target_effectiveness": {
                            "page_id": "kis.symbol.245450",
                            "status": "degraded",
                            "sample_count": 5,
                            "win_rate": 0.2,
                            "expectancy": -0.04,
                            "helpful_score": -0.1,
                            "confidence": 0.7,
                            "reasons": [
                                "financial_refresh_repeatedly_failed_to_improve_entry_quality"
                            ],
                        },
                    }
                ],
            },
        }
    )

    metric = contract["repair_loop_effectiveness"][
        "repair_loop_status_metrics"
    ][0]
    assert metric["priority_type"] == "repair_queue"
    assert metric["decision_use"] == "evidence_quality_cross_check"
    assert metric["source_id"] == "repair:financials"
    assert metric["quality_warnings"] == ["financials_missing"]
    assert metric["impacted_page_ids"] == ["kis.symbol.245450"]
    assert metric["impacted_symbols"] == ["245450"]
    assert metric["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert metric["repair_target_effectiveness"] == {
        "page_id": "kis.symbol.245450",
        "status": "degraded",
        "sample_count": 5,
        "win_rate": 0.2,
        "expectancy": -0.04,
        "helpful_score": -0.1,
        "confidence": 0.7,
        "reasons": [
            "financial_refresh_repeatedly_failed_to_improve_entry_quality"
        ],
    }


def test_repair_contract_success_criteria_preserve_target_details() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_success_criteria_metrics": [
                    {
                        "decision_scope": "kis",
                        "criterion": "audit_contract_repaired_or_demoted",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                        "repair_target_effectiveness": {
                            "page_id": "kis.symbol.245450",
                            "status": "degraded",
                            "sample_count": 5,
                            "win_rate": 0.2,
                            "expectancy": -0.04,
                            "helpful_score": -0.1,
                            "confidence": 0.7,
                            "reasons": [
                                "success_criterion_failed_after_financial_refresh"
                            ],
                        },
                    }
                ],
            },
        }
    )

    metric = contract["repair_loop_effectiveness"][
        "repair_success_criteria_metrics"
    ][0]
    assert metric["priority_type"] == "repair_queue"
    assert metric["action_type"] == "refresh_symbol_financials"
    assert metric["decision_use"] == "evidence_quality_cross_check"
    assert metric["source_id"] == "repair:financials"
    assert metric["quality_warnings"] == ["financials_missing"]
    assert metric["impacted_page_ids"] == ["kis.symbol.245450"]
    assert metric["impacted_symbols"] == ["245450"]
    assert metric["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert metric["repair_target_effectiveness"] == {
        "page_id": "kis.symbol.245450",
        "status": "degraded",
        "sample_count": 5,
        "win_rate": 0.2,
        "expectancy": -0.04,
        "helpful_score": -0.1,
        "confidence": 0.7,
        "reasons": ["success_criterion_failed_after_financial_refresh"],
    }


def test_repair_contract_learning_directive_preserve_target_details() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_learning_directive_metrics": [
                    {
                        "decision_scope": "kis",
                        "recommended_action": (
                            "repair_or_demote_success_criterion_before_reuse"
                        ),
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                        "repair_target_effectiveness": {
                            "page_id": "kis.symbol.245450",
                            "status": "degraded",
                            "sample_count": 5,
                            "win_rate": 0.2,
                            "expectancy": -0.04,
                            "helpful_score": -0.1,
                            "confidence": 0.7,
                            "reasons": [
                                "directive_failed_after_financial_refresh"
                            ],
                        },
                    }
                ],
            },
        }
    )

    metric = contract["repair_loop_effectiveness"][
        "repair_learning_directive_metrics"
    ][0]
    assert metric["priority_type"] == "repair_queue"
    assert metric["action_type"] == "refresh_symbol_financials"
    assert metric["decision_use"] == "evidence_quality_cross_check"
    assert metric["source_id"] == "repair:financials"
    assert metric["quality_warnings"] == ["financials_missing"]
    assert metric["impacted_page_ids"] == ["kis.symbol.245450"]
    assert metric["impacted_symbols"] == ["245450"]
    assert metric["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert metric["repair_target_effectiveness"] == {
        "page_id": "kis.symbol.245450",
        "status": "degraded",
        "sample_count": 5,
        "win_rate": 0.2,
        "expectancy": -0.04,
        "helpful_score": -0.1,
        "confidence": 0.7,
        "reasons": ["directive_failed_after_financial_refresh"],
    }


def test_repair_contract_learning_step_preserve_target_details() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_learning_step_metrics": [
                    {
                        "decision_scope": "kis",
                        "resolution_step": (
                            "inspect_failed_repair_directive_outcomes"
                        ),
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                        "repair_target_effectiveness": {
                            "page_id": "kis.symbol.245450",
                            "status": "degraded",
                            "sample_count": 5,
                            "win_rate": 0.2,
                            "expectancy": -0.04,
                            "helpful_score": -0.1,
                            "confidence": 0.7,
                            "reasons": ["step_failed_after_financial_refresh"],
                        },
                    }
                ],
            },
        }
    )

    metric = contract["repair_loop_effectiveness"][
        "repair_learning_step_metrics"
    ][0]
    assert metric["priority_type"] == "repair_queue"
    assert metric["action_type"] == "refresh_symbol_financials"
    assert metric["decision_use"] == "evidence_quality_cross_check"
    assert metric["source_id"] == "repair:financials"
    assert metric["quality_warnings"] == ["financials_missing"]
    assert metric["impacted_page_ids"] == ["kis.symbol.245450"]
    assert metric["impacted_symbols"] == ["245450"]
    assert metric["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert metric["repair_target_effectiveness"] == {
        "page_id": "kis.symbol.245450",
        "status": "degraded",
        "sample_count": 5,
        "win_rate": 0.2,
        "expectancy": -0.04,
        "helpful_score": -0.1,
        "confidence": 0.7,
        "reasons": ["step_failed_after_financial_refresh"],
    }


def test_repair_contract_learning_resolution_preserve_target_details() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_learning_resolution_metrics": [
                    {
                        "decision_scope": "kis",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "quality_warnings": ["financials_missing"],
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                        "repair_target_effectiveness": {
                            "page_id": "kis.symbol.245450",
                            "status": "degraded",
                            "sample_count": 5,
                            "win_rate": 0.2,
                            "expectancy": -0.04,
                            "helpful_score": -0.1,
                            "confidence": 0.7,
                            "reasons": [
                                "resolution_failed_after_financial_refresh"
                            ],
                        },
                    }
                ],
            },
        }
    )

    metric = contract["repair_loop_effectiveness"][
        "repair_learning_resolution_metrics"
    ][0]
    assert metric["priority_type"] == "repair_queue"
    assert metric["action_type"] == "refresh_symbol_financials"
    assert metric["decision_use"] == "evidence_quality_cross_check"
    assert metric["source_id"] == "repair:financials"
    assert metric["quality_warnings"] == ["financials_missing"]
    assert metric["impacted_page_ids"] == ["kis.symbol.245450"]
    assert metric["impacted_symbols"] == ["245450"]
    assert metric["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert metric["repair_target_effectiveness"] == {
        "page_id": "kis.symbol.245450",
        "status": "degraded",
        "sample_count": 5,
        "win_rate": 0.2,
        "expectancy": -0.04,
        "helpful_score": -0.1,
        "confidence": 0.7,
        "reasons": ["resolution_failed_after_financial_refresh"],
    }


def test_repair_contract_learning_summary_targets_preserve_target_details() -> None:
    target_context = {
        "priority_type": "repair_queue",
        "action_type": "refresh_symbol_financials",
        "decision_use": "evidence_quality_cross_check",
        "source_id": "repair:financials",
        "quality_warnings": ["financials_missing"],
        "impacted_page_ids": ["kis.symbol.245450"],
        "impacted_symbols": ["245450"],
        "repair_targets": [
            {
                "page_id": "kis.symbol.245450",
                "symbol": "245450",
                "recommended_action": (
                    "refresh_symbol_financials_and_rewrite_page_evidence"
                ),
            }
        ],
        "repair_target_effectiveness": {
            "page_id": "kis.symbol.245450",
            "status": "degraded",
            "sample_count": 5,
            "win_rate": 0.2,
            "expectancy": -0.04,
            "helpful_score": -0.1,
            "confidence": 0.7,
            "reasons": ["summary_target_failed_after_financial_refresh"],
        },
    }
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_learning_directive_metrics": [
                    {
                        "decision_scope": "kis",
                        "recommended_action": (
                            "repair_or_demote_success_criterion_before_reuse"
                        ),
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        **target_context,
                    }
                ],
                "repair_learning_step_metrics": [
                    {
                        "decision_scope": "kis",
                        "resolution_step": (
                            "inspect_failed_repair_directive_outcomes"
                        ),
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        **target_context,
                    }
                ],
                "repair_learning_resolution_metrics": [
                    {
                        "decision_scope": "kis",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        **target_context,
                    }
                ],
            },
        }
    )

    summary = contract["repair_loop_effectiveness"]
    target = summary["repair_learning_directive_summary"]["action_targets"][0]
    step_target = summary["repair_learning_step_summary"]["step_targets"][0]
    resolution_target = summary["repair_learning_resolution_summary"][
        "resolution_targets"
    ][0]
    for row in (target, step_target, resolution_target):
        assert row["priority_types"] == ["repair_queue"]
        assert row["primary_decision_use"] == "evidence_quality_cross_check"
        assert row["decision_uses"] == ["evidence_quality_cross_check"]
        assert row["source_id"] == "repair:financials"
        assert row["quality_warnings"] == ["financials_missing"]
        assert row["impacted_page_ids"] == ["kis.symbol.245450"]
        assert row["impacted_symbols"] == ["245450"]
        assert row["repair_targets"] == [
            {
                "page_id": "kis.symbol.245450",
                "symbol": "245450",
                "recommended_action": (
                    "refresh_symbol_financials_and_rewrite_page_evidence"
                ),
            }
        ]
        assert row["repair_target_effectiveness"] == [
            {
                "page_id": "kis.symbol.245450",
                "status": "degraded",
                "sample_count": 5,
                "win_rate": 0.2,
                "expectancy": -0.04,
                "helpful_score": -0.1,
                "confidence": 0.7,
                "reasons": ["summary_target_failed_after_financial_refresh"],
            }
        ]
        assert row["repair_target_effectiveness_statuses"] == ["degraded"]


def test_repair_contract_summary_action_targets_preserve_quality_warnings() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "repair_loop_status_metrics": [
                    {
                        "decision_scope": "kis",
                        "priority_type": "repair_queue",
                        "action_type": "repair_usage_guidance_contract",
                        "decision_use": "usage_guidance_effectiveness_repair",
                        "source_id": "repair:usage-guidance",
                        "sample_count": 3,
                        "missed_count": 2,
                        "resolved_count": 1,
                        "resolution_rate": 1 / 3,
                        "status": "repair_required",
                        "quality_warnings": ["usage_guidance_degraded"],
                    }
                ],
            },
        }
    )

    target = contract["repair_loop_effectiveness"]["repair_loop_status_summary"][
        "repair_action_targets"
    ][0]
    assert target["quality_warnings"] == ["usage_guidance_degraded"]


def test_repair_contract_summary_action_targets_preserve_impacted_identifiers() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 4,
                "missed_count": 3,
                "resolved_count": 1,
                "resolution_rate": 0.25,
                "repair_loop_status_metrics": [
                    {
                        "decision_scope": "kis",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 4,
                        "missed_count": 3,
                        "resolved_count": 1,
                        "resolution_rate": 0.25,
                        "status": "repair_required",
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                    }
                ],
            },
        }
    )

    target = contract["repair_loop_effectiveness"]["repair_loop_status_summary"][
        "repair_action_targets"
    ][0]
    assert target["impacted_page_ids"] == ["kis.symbol.245450"]
    assert target["impacted_symbols"] == ["245450"]
    assert target["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]


def test_repair_contract_summary_action_targets_preserve_target_effectiveness() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "repair_loop_status_metrics": [
                    {
                        "decision_scope": "kis",
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "repair:financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "repair_target_effectiveness": {
                            "page_id": "kis.symbol.245450",
                            "status": "degraded",
                            "sample_count": 5,
                            "win_rate": 0.2,
                            "expectancy": -0.04,
                            "helpful_score": -0.1,
                            "confidence": 0.7,
                            "reasons": [
                                "financial_refresh_repeatedly_failed_to_improve_entry_quality"
                            ],
                        },
                    }
                ],
            },
        }
    )

    target = contract["repair_loop_effectiveness"]["repair_loop_status_summary"][
        "repair_action_targets"
    ][0]
    assert target["repair_target_effectiveness"] == [
        {
            "page_id": "kis.symbol.245450",
            "status": "degraded",
            "sample_count": 5,
            "win_rate": 0.2,
            "expectancy": -0.04,
            "helpful_score": -0.1,
            "confidence": 0.7,
            "reasons": [
                "financial_refresh_repeatedly_failed_to_improve_entry_quality"
            ],
        }
    ]
    assert target["repair_target_effectiveness_statuses"] == ["degraded"]


def test_repair_contract_preserves_repair_queue_fields() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.research.repair_queue",
                    "page_type": "research",
                    "priority_type": "repair_queue",
                    "symbols": ["245450"],
                    "source_type": "wiki_repair_queue",
                    "source_id": "repair:financials",
                    "action_type": "refresh_symbol_financials",
                    "repair_status": "scheduled",
                    "quality_warnings": ["financials_missing"],
                    "repair_action": "collect financial statements",
                    "impacted_page_ids": ["kis.symbol.245450"],
                    "impacted_symbols": ["245450"],
                    "repair_targets": [
                        {
                            "page_id": "kis.symbol.245450",
                            "symbol": "245450",
                            "recommended_action": (
                                "refresh_symbol_financials_and_rewrite_page_evidence"
                            ),
                        }
                    ],
                    "repair_target_effectiveness": {
                        "page_id": (
                            "repair_target."
                            "refresh_symbol_financials_and_rewrite_page_evidence"
                        ),
                        "status": "degraded",
                        "sample_count": 3,
                        "win_rate": 0.0,
                        "expectancy": -0.6,
                        "helpful_score": -8.0,
                        "confidence": 0.75,
                        "reasons": [
                            "repair_target:"
                            "refresh_symbol_financials_and_rewrite_page_evidence"
                        ],
                    },
                    "reasons": ["repair_queue:open"],
                }
            ]
        }
    )

    row = contract["top_priorities"][0]
    assert row["priority_type"] == "repair_queue"
    assert row["source_type"] == "wiki_repair_queue"
    assert row["action_type"] == "refresh_symbol_financials"
    assert row["repair_status"] == "scheduled"
    assert row["quality_warnings"] == ["financials_missing"]
    assert row["impacted_page_ids"] == ["kis.symbol.245450"]
    assert row["impacted_symbols"] == ["245450"]
    assert row["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": "refresh_symbol_financials_and_rewrite_page_evidence",
        }
    ]
    assert row["repair_target_effectiveness"] == {
        "page_id": "repair_target.refresh_symbol_financials_and_rewrite_page_evidence",
        "status": "degraded",
        "sample_count": 3,
        "win_rate": 0.0,
        "expectancy": -0.6,
        "helpful_score": -8.0,
        "confidence": 0.75,
        "reasons": [
            "repair_target:refresh_symbol_financials_and_rewrite_page_evidence"
        ],
    }


def test_repair_contract_keeps_repair_queue_visible_after_many_coverage_repairs() -> None:
    repair_priorities = [
        {
            "page_id": f"kis.symbol.00000{idx}",
            "page_type": "symbol",
            "priority_type": "requested_symbol_coverage",
            "symbols": [f"00000{idx}"],
            "source_type": "selection_budget_report",
            "source_id": f"repair:coverage:kis:00000{idx}",
            "action_type": "refresh_requested_symbol_summary",
            "quality_warnings": ["requested_symbol_summary_missing"],
            "repair_action": "collect_or_rebuild_requested_symbol_wiki_summary",
            "severity_score": 88.0,
        }
        for idx in range(1, 10)
    ]
    repair_priorities.append(
        {
            "page_id": "kis.research.repair_queue",
            "page_type": "research",
            "priority_type": "repair_queue",
            "symbols": ["000009"],
            "source_type": "wiki_repair_queue",
            "source_id": "repair:financials:kis:000009",
            "action_type": "refresh_symbol_financials",
            "repair_status": "scheduled",
            "quality_warnings": ["financials_missing"],
            "repair_action": "collect or cross-check financial statements",
            "impacted_page_ids": ["kis.symbol.000009"],
            "impacted_symbols": ["000009"],
            "severity_score": 34.0,
        }
    )

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": repair_priorities},
        limit=6,
    )

    assert contract["repair_priority_count"] == 10
    assert contract["top_priority_count"] == 6
    assert contract["omitted_priority_count"] == 4
    assert contract["priority_type_counts"] == {
        "repair_queue": 1,
        "requested_symbol_coverage": 9,
    }
    assert contract["top_priority_type_counts"] == {
        "repair_queue": 1,
        "requested_symbol_coverage": 5,
    }
    assert contract["omitted_priority_type_counts"] == {
        "requested_symbol_coverage": 4,
    }
    assert contract["repair_pressure_action_plan"] == {
        "status": "compressed",
        "total_priority_count": 10,
        "top_priority_count": 6,
        "omitted_priority_count": 4,
        "omitted_priority_type_counts": {"requested_symbol_coverage": 4},
        "action_batch_count": 2,
        "action_batch_total_count": 10,
        "action_batch_type_counts": {
            "refresh_requested_symbol_summary": 9,
            "refresh_symbol_financials": 1,
        },
        "action_batch_scopes": ["kis"],
        "action_batch_warning_counts": {
            "requested_symbol_summary_missing": 9,
            "financials_missing": 1,
        },
        "action_batch_max_severity_score": 88.0,
        "action_batch_visible_pressure_count": 10,
        "action_batch_pressure_visibility_ratio": 1.0,
        "required_response": (
            "treat top_priorities as representative, not exhaustive; mention "
            "omitted repair pressure when confidence or sizing depends on wiki "
            "freshness; treat action_batches as grouped repair work that must be "
            "reflected in candidate resolution, hold triggers, or repair metadata "
            "before confidence/sizing"
        ),
    }
    assert len(contract["top_priorities"]) == 6
    assert any(
        row.get("source_id") == "repair:coverage:kis:000001"
        for row in contract["top_priorities"]
    )
    assert any(
        row.get("source_id") == "repair:financials:kis:000009"
        for row in contract["top_priorities"]
    )
    coverage_batch = next(
        row
        for row in contract["action_batches"]
        if row.get("action_type") == "refresh_requested_symbol_summary"
    )
    assert coverage_batch["warning_counts"] == {
        "requested_symbol_summary_missing": 9
    }
    assert coverage_batch["max_severity_score"] == 88.0


def test_repair_contract_preserves_selector_budget_repair_pressure() -> None:
    top_repair_priorities = [
        {
            "page_id": f"kis.symbol.00000{idx}",
            "page_type": "symbol",
            "priority_type": "requested_symbol_coverage",
            "symbols": [f"00000{idx}"],
            "source_type": "selection_budget_report",
            "source_id": f"repair:coverage:kis:00000{idx}",
            "action_type": "refresh_requested_symbol_summary",
            "quality_warnings": ["requested_symbol_summary_missing"],
            "repair_action": "collect_or_rebuild_requested_symbol_wiki_summary",
            "severity_score": 88.0,
        }
        for idx in range(1, 6)
    ]
    top_repair_priorities.append(
        {
            "page_id": "kis.research.repair_queue",
            "page_type": "research",
            "priority_type": "repair_queue",
            "symbols": ["000009"],
            "source_type": "wiki_repair_queue",
            "source_id": "repair:financials:kis:000009",
            "action_type": "refresh_symbol_financials",
            "repair_status": "scheduled",
            "quality_warnings": ["financials_missing"],
            "repair_action": "collect or cross-check financial statements",
            "severity_score": 34.0,
        }
    )

    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": top_repair_priorities,
            "budget_report": {
                "repair_priority_total_count": 10,
                "repair_priority_selected_count": 6,
                "repair_priority_omitted_count": 4,
                "repair_priority_type_counts": {
                    "repair_queue": 1,
                    "requested_symbol_coverage": 9,
                },
                "repair_priority_selected_type_counts": {
                    "repair_queue": 1,
                    "requested_symbol_coverage": 5,
                },
                "repair_priority_omitted_type_counts": {
                    "requested_symbol_coverage": 4,
                },
            },
        },
        limit=6,
    )

    assert contract["repair_priority_count"] == 10
    assert contract["top_priority_count"] == 6
    assert contract["omitted_priority_count"] == 4
    assert contract["priority_type_counts"] == {
        "repair_queue": 1,
        "requested_symbol_coverage": 9,
    }
    assert contract["top_priority_type_counts"] == {
        "repair_queue": 1,
        "requested_symbol_coverage": 5,
    }
    assert contract["omitted_priority_type_counts"] == {
        "requested_symbol_coverage": 4,
    }


def test_repair_contract_does_not_synthesize_priorities_when_budget_selected_count_zero() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "priority_type": "requested_symbol_coverage",
                    "symbols": ["005930"],
                    "source_type": "selection_budget_report",
                    "source_id": "repair:coverage:kis:005930",
                    "action_type": "refresh_requested_symbol_summary",
                }
            ],
            "budget_report": {
                "repair_priority_total_count": 0,
                "repair_priority_selected_count": 0,
                "repair_priority_omitted_count": 0,
            },
        },
        limit=6,
    )

    assert contract == {}


def test_repair_contract_preserves_repair_target_effectiveness_reasons() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "kis.research.repair_queue",
                    "priority_type": "repair_queue",
                    "source_type": "wiki_repair_queue",
                    "source_id": "repair:financials",
                    "action_type": "refresh_symbol_financials",
                    "repair_status": "scheduled",
                    "reasons": [
                        "repair_queue:open",
                        "repair_status:scheduled",
                        "action_type:refresh_symbol_financials",
                        "repair_target_effectiveness:degraded",
                        (
                            "repair_target_effectiveness:"
                            "refresh_symbol_financials_and_rewrite_page_evidence:"
                            "degraded"
                        ),
                        "warning:financials_missing",
                    ],
                }
            ]
        }
    )

    reasons = contract["top_priorities"][0]["reasons"]

    assert "repair_target_effectiveness:degraded" in reasons
    assert (
        "repair_target_effectiveness:"
        "refresh_symbol_financials_and_rewrite_page_evidence:degraded"
    ) in reasons
    assert "warning:financials_missing" in reasons


def test_selector_promotes_degraded_decision_adjustment_audit_to_repair_priority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
    )
    application = JueWikiApplicationService(service)
    for idx in range(5):
        selection_run_id = f"selection:audit-contract-loss-{idx}"
        service.record_selection_run(
            run_id=selection_run_id,
            target_scope="kis",
            request={
                "target_scope": "kis",
                "jue_wiki_decision_adjustment_audit_contract": {
                    "version": "jue_wiki_decision_adjustment_audit_contract_v1",
                    "status": "repair_required",
                    "adjustment_count": 1,
                    "actions": ["audit_preferred_risk_posture_before_shift"],
                    "target_risk_postures": ["repair_probe"],
                    "required_review": [
                        "verify why prior shift_to_preferred_risk_posture underperformed",
                    ],
                    "accepted_resolutions": [
                        "create a smaller repair probe or waiting block",
                    ],
                    "hard_blocker": False,
                    "safety_gates_still_override": True,
                },
            },
            selected_pages=[{"page_id": page_id, "rank": 1, "score": 9.0}],
            rejected_pages=[],
            char_count=100,
            max_chars=1000,
            status="ok",
        )
        link = application.record_decision_link(
            selection_run_id=selection_run_id,
            manager_run_id=f"kis-manager-audit-contract-loss-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[page_id],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
            metadata={
                "jue_wiki_trust_profile": {
                    "authority": "supporting_evidence",
                    "trust_level": "medium",
                    "posture": "validated_mode_recommendation",
                    "prompt_mode": "assist",
                    "usage_contract": {
                        "risk_posture": "supporting_evidence",
                        "allowed_uses": ["candidate_ranking"],
                        "required_cross_checks": ["live_quote", "risk_gate"],
                    },
                },
                "jue_wiki_decision_adjustment_audit_contract": {
                    "version": "jue_wiki_decision_adjustment_audit_contract_v1",
                    "status": "repair_required",
                    "adjustment_count": 1,
                    "actions": ["audit_preferred_risk_posture_before_shift"],
                    "target_risk_postures": ["repair_probe"],
                    "required_review": [
                        "verify why prior shift_to_preferred_risk_posture underperformed",
                    ],
                    "accepted_resolutions": [
                        "create a smaller repair probe or waiting block",
                    ],
                    "hard_blocker": False,
                    "safety_gates_still_override": True,
                },
            },
        )
        application.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=-0.9 - (idx * 0.05),
            evidence={"block_id": f"audit-contract-loss-{idx}"},
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "decision_adjustment_audit"
    )
    assert priority["source_type"] == "jue_wiki_decision_adjustment_audit_metric"
    assert priority["action_type"] == "repair_decision_adjustment_audit_contract"
    assert priority["decision_use"] == "decision_adjustment_audit_repair"
    assert priority["quality_status"] == "weak"
    assert priority["repair_status"] == "repair_required"
    assert priority["symbols"] == ["005930"]
    assert "audit_preferred_risk_posture_before_shift" in priority["source_id"]
    assert "target_risk_posture:repair_probe" in priority["reasons"]
    assert priority["repair_action"] == (
        "repair degraded decision adjustment audit contract before reusing "
        "repair_probe escalation"
    )

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    contract_priority = next(
        row
        for row in contract["top_priorities"]
        if row.get("priority_type") == "decision_adjustment_audit"
    )
    assert contract_priority["decision_use"] == "decision_adjustment_audit_repair"
    assert contract_priority["candidate_resolution_required"] is True


def test_repair_contract_canonicalizes_quality_status_aliases() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "decision_adjustment_audit.kis.sample",
                    "page_type": "policy",
                    "priority_type": "decision_adjustment_audit",
                    "symbols": ["005930"],
                    "source_type": "jue_wiki_decision_adjustment_audit_metric",
                    "source_id": "kis:supporting_evidence:sample",
                    "action_type": "repair_decision_adjustment_audit_contract",
                    "repair_status": "repair_required",
                    "quality_status": "degraded",
                    "quality_warnings": ["decision_adjustment_audit_degraded"],
                    "decision_use": "decision_adjustment_audit_repair",
                    "repair_action": "repair degraded decision adjustment audit contract",
                }
            ]
        }
    )

    assert contract["top_priorities"][0]["quality_status"] == "weak"


def test_repair_contract_marks_quality_warning_effectiveness_as_non_blocking() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [
                {
                    "page_id": "quality_warning.financials_missing",
                    "page_type": "",
                    "priority_type": "repair_queue",
                    "symbols": [],
                    "source_type": "wiki_repair_queue",
                    "source_id": "repair:quality-warning:financials",
                    "action_type": "repair_quality_warning_effectiveness",
                    "repair_status": "scheduled",
                    "quality_warnings": ["financials_missing"],
                    "repair_action": (
                        "repair or downgrade evidence carrying financials_missing"
                    ),
                    "reasons": ["samples:24", "expectancy:-2.0013"],
                }
            ]
        }
    )

    row = contract["top_priorities"][0]
    assert row["decision_use"] == "quality_warning_effectiveness_repair"
    assert row["hard_blocker"] is False
    assert row["candidate_resolution_required"] is True
    assert contract["hard_blockers_allowed"] is False
    assert (
        contract["quality_warning_effectiveness_policy"]
        == "repair_or_downgrade_warning_bearing_evidence_without_blanket_holds"
    )


def test_selector_prioritizes_evidence_quality_research_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "weak",
                "quality_warnings": ["price_missing"],
            }
        ],
    )
    service._rebuild_evidence_quality_page(scope="kis")

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert result.status == "ok"
    assert result.pages[0].page_id == "kis.research.evidence_quality"
    assert "operational_memory:evidence_quality" in result.pages[0].reasons
    assert "price_missing" in result.pages[0].content


def test_selector_prioritizes_repair_queue_research_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "test", "source_id": "symbol"}],
        confidence=0.9,
        freshness="fresh",
    )
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["245450"],
        content_sections={
            "Current Stance": "open_action_count=1",
            "Durable Facts": "- open_action_count=1",
            "Evidence Links": "- wiki_repair_queue:repair:financials",
            "Trading History": "- source repair queue",
            "Lessons": "- repair before size increase",
            "Contradictions": "- attractive but incomplete",
            "Open Questions": "- which repair blocks sizing?",
            "Next Context Pack Summary": "repair queue open=1",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:financials",
            }
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert result.pages[0].page_id == "kis.research.repair_queue"
    assert "operational_memory:repair_queue" in result.pages[0].reasons


def test_selector_promotes_repair_queue_actions_to_repair_priorities(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.upsert_page_effectiveness(
        {
            "page_id": (
                "repair_target.refresh_symbol_financials_and_rewrite_page_evidence"
            ),
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "",
            "sample_count": 3,
            "win_rate": 0.0,
            "expectancy": -0.6,
            "avg_return_pct": -0.6,
            "helpful_score": -8.0,
            "confidence": 0.75,
            "status": "degraded",
            "reasons": [
                "samples:3",
                "win_rate:0.0000",
                "expectancy:-0.6000",
                "median_mae:0.0000",
                "repair_target:refresh_symbol_financials_and_rewrite_page_evidence",
                "repair_target_prior_status:degraded",
            ],
        }
    )
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["245450"],
        content_sections={
            "Current Stance": "open_action_count=1",
            "Durable Facts": "- open_action_count=1",
            "Evidence Links": "- wiki_repair_queue:repair:financials",
            "Trading History": "- source repair queue",
            "Lessons": "- repair before size increase",
            "Contradictions": "- attractive but incomplete",
            "Open Questions": "- which repair blocks sizing?",
            "Next Context Pack Summary": "repair queue open=1",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:financials",
                "action_type": "refresh_symbol_financials",
                "status": "scheduled",
                "symbols": ["245450"],
                "quality_warnings": ["financials_missing"],
                "repair_action": "collect or cross-check financial statements",
                "impacted_page_ids": ["kis.symbol.245450"],
                "impacted_symbols": ["245450"],
                "repair_targets": [
                    {
                        "page_id": "kis.symbol.245450",
                        "symbol": "245450",
                        "recommended_action": (
                            "refresh_symbol_financials_and_rewrite_page_evidence"
                        ),
                    }
                ],
            }
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["245450"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:financials"
    )
    assert priority["priority_type"] == "repair_queue"
    assert priority["source_type"] == "wiki_repair_queue"
    assert priority["symbols"] == ["245450"]
    assert priority["quality_warnings"] == ["financials_missing"]
    assert priority["repair_action"] == (
        "collect or cross-check financial statements"
    )
    assert priority["impacted_page_ids"] == ["kis.symbol.245450"]
    assert priority["impacted_symbols"] == ["245450"]
    assert priority["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": "refresh_symbol_financials_and_rewrite_page_evidence",
        }
    ]
    repair_batch = next(
        row
        for row in result.repair_action_batches
        if row.get("action_type") == "refresh_symbol_financials"
    )
    assert repair_batch == {
        "scope": "kis",
        "action_type": "refresh_symbol_financials",
        "count": 1,
        "symbols": ["245450"],
        "warnings": ["financials_missing"],
        "warning_counts": {"financials_missing": 1},
        "max_severity_score": 58.5,
        "recommended_actions": [
            "refresh_symbol_financials_and_rewrite_page_evidence"
        ],
        "priority_types": ["repair_queue"],
    }
    batch_contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": result.repair_priorities,
            "repair_action_batches": result.repair_action_batches,
        }
    )
    assert repair_batch in batch_contract["action_batches"]
    assert priority["repair_target_effectiveness"] == {
        "page_id": "repair_target.refresh_symbol_financials_and_rewrite_page_evidence",
        "status": "degraded",
        "sample_count": 3,
        "win_rate": 0.0,
        "expectancy": -0.6,
        "helpful_score": -8.0,
        "confidence": 0.75,
        "reasons": [
            "samples:3",
            "win_rate:0.0000",
            "expectancy:-0.6000",
            "median_mae:0.0000",
            "repair_target:refresh_symbol_financials_and_rewrite_page_evidence",
            "repair_target_prior_status:degraded",
        ],
    }
    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    contract_priority = next(
        row
        for row in contract["top_priorities"]
        if row.get("source_id") == "repair:financials"
    )
    assert contract_priority["repair_target_effectiveness"] == {
        "page_id": "repair_target.refresh_symbol_financials_and_rewrite_page_evidence",
        "status": "degraded",
        "sample_count": 3,
        "win_rate": 0.0,
        "expectancy": -0.6,
        "helpful_score": -8.0,
        "confidence": 0.75,
        "reasons": [
            "samples:3",
            "win_rate:0.0000",
            "expectancy:-0.6000",
            "median_mae:0.0000",
            "repair_target:refresh_symbol_financials_and_rewrite_page_evidence",
            "repair_target_prior_status:degraded",
        ],
    }


def test_repair_contract_keeps_action_batches_when_priorities_are_omitted() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [],
            "repair_action_batches": [
                {
                    "scope": "kis",
                    "action_type": "refresh_symbol_fundamentals",
                    "count": 13,
                    "symbols": ["033100", "033270"],
                    "warnings": ["financial_metrics_sparse"],
                    "warning_counts": {"financial_metrics_sparse": 13},
                    "max_severity_score": 74.5,
                    "recommended_actions": [],
                    "priority_types": ["repair_queue"],
                }
            ],
            "budget_report": {
                "repair_priority_total_count": 13,
                "repair_priority_selected_count": 0,
                "repair_priority_omitted_count": 13,
                "repair_priority_type_counts": {"repair_queue": 13},
                "repair_priority_selected_type_counts": {},
                "repair_priority_omitted_type_counts": {"repair_queue": 13},
            },
        }
    )

    assert contract["status"] == "active"
    assert contract["action_batches"][0]["action_type"] == "refresh_symbol_fundamentals"
    assert contract["action_batches"][0]["count"] == 13
    assert contract["repair_pressure_action_plan"]["action_batch_count"] == 1
    assert contract["repair_pressure_action_plan"]["action_batch_total_count"] == 13
    assert contract["repair_pressure_action_plan"]["action_batch_type_counts"] == {
        "refresh_symbol_fundamentals": 13
    }
    assert contract["repair_pressure_action_plan"]["action_batch_scopes"] == ["kis"]
    assert contract["repair_pressure_action_plan"]["action_batch_warning_counts"] == {
        "financial_metrics_sparse": 13
    }
    assert contract["repair_pressure_action_plan"]["action_batch_max_severity_score"] == 74.5
    assert "action_batches" in contract["repair_pressure_action_plan"]["required_response"]


def test_repair_contract_keeps_high_severity_action_batch_inside_prompt_limit() -> None:
    low_priority_rows = [
        {
            "page_id": f"kis.symbol.000{i:03d}",
            "page_type": "symbol",
            "priority_type": "repair_queue",
            "symbols": [f"000{i:03d}"],
            "source_type": "wiki_repair_queue",
            "source_id": f"repair:low:{i}",
            "action_type": f"a_low_repair_{i:02d}",
            "quality_warnings": ["minor_gap"],
            "severity_score": 10.0 + i,
        }
        for i in range(13)
    ]
    high_priority_row = {
        "page_id": "kis.symbol.999999",
        "page_type": "symbol",
        "priority_type": "repair_queue",
        "symbols": ["999999"],
        "source_type": "wiki_repair_queue",
        "source_id": "repair:critical:999999",
        "action_type": "zz_refresh_critical_financials",
        "quality_warnings": ["financials_missing", "valuation_stale"],
        "severity_score": 99.0,
    }

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": [*low_priority_rows, high_priority_row]},
        limit=4,
    )

    action_types = [row.get("action_type") for row in contract["action_batches"]]
    assert "zz_refresh_critical_financials" in action_types
    assert contract["action_batches"][0]["action_type"] == "zz_refresh_critical_financials"
    assert contract["action_batches"][0]["max_severity_score"] == 99.0
    assert contract["action_batch_total_count"] == 14
    assert contract["action_batch_omitted_count"] == 2
    assert contract["action_batch_visible_pressure_count"] == 12
    assert contract["action_batch_pressure_visibility_ratio"] == 0.8571
    assert contract["repair_pressure_action_plan"][
        "action_batch_type_counts"
    ]["zz_refresh_critical_financials"] == 1
    assert (
        contract["repair_pressure_action_plan"]["action_batch_max_severity_score"]
        == 99.0
    )
    assert contract["repair_pressure_action_plan"]["action_batch_total_count"] == 14
    assert contract["repair_pressure_action_plan"]["action_batch_omitted_count"] == 2
    assert (
        contract["repair_pressure_action_plan"][
            "action_batch_pressure_visibility_ratio"
        ]
        == 0.8571
    )


def test_repair_contract_reports_omitted_action_batch_count() -> None:
    raw_action_batches = [
        {
            "scope": "kis",
            "action_type": f"refresh_symbol_slice_{idx:02d}",
            "count": 1,
            "symbols": [f"{idx:06d}"],
            "warnings": ["slice_gap"],
            "max_severity_score": 20.0 + idx,
        }
        for idx in range(15)
    ]

    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [],
            "repair_action_batches": raw_action_batches,
            "budget_report": {
                "repair_priority_total_count": 15,
                "repair_priority_selected_count": 0,
                "repair_priority_omitted_count": 15,
            },
        }
    )

    assert len(contract["action_batches"]) == 12
    assert contract["action_batches"][0]["action_type"] == "refresh_symbol_slice_14"
    assert contract["action_batch_total_count"] == 15
    assert contract["action_batch_omitted_count"] == 3
    assert contract["action_batch_visible_pressure_count"] == 12
    assert contract["action_batch_pressure_visibility_ratio"] == 0.8
    assert contract["repair_pressure_action_plan"]["action_batch_count"] == 12
    assert contract["repair_pressure_action_plan"]["action_batch_total_count"] == 15
    assert contract["repair_pressure_action_plan"]["action_batch_omitted_count"] == 3
    assert (
        contract["repair_pressure_action_plan"]["action_batch_visible_pressure_count"]
        == 12
    )
    assert (
        contract["repair_pressure_action_plan"]["action_batch_pressure_visibility_ratio"]
        == 0.8
    )


def test_repair_contract_compacts_explicit_action_batches_before_prompt() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [],
            "repair_action_batches": [
                {
                    "scope": " kis ",
                    "action_type": " refresh_symbol_fundamentals ",
                    "count": "13",
                    "symbols": [" 033100 ", "", "033270", "051370"],
                    "warnings": ["financial_metrics_sparse", "W" * 500],
                    "warning_counts": {"financial_metrics_sparse": "13", "": 99},
                    "max_severity_score": "88.5",
                    "recommended_actions": ["refresh evidence", "A" * 500],
                    "priority_types": ["repair_queue", " evidence_quality "],
                    "raw_blob": "RAW" * 5000,
                    "nested": {"large": "NESTED" * 500},
                }
            ],
            "budget_report": {
                "repair_priority_total_count": 13,
                "repair_priority_selected_count": 0,
                "repair_priority_omitted_count": 13,
            },
        }
    )

    batch = contract["action_batches"][0]
    assert batch == {
        "scope": "kis",
        "action_type": "refresh_symbol_fundamentals",
        "count": 13,
        "symbols": ["033100", "033270", "051370"],
        "warnings": ["financial_metrics_sparse", "W" * 120],
        "warning_counts": {"financial_metrics_sparse": 13},
        "max_severity_score": 88.5,
        "recommended_actions": ["refresh evidence", "A" * 180],
        "priority_types": ["repair_queue", "evidence_quality"],
    }


def test_repair_contract_compacts_scalar_action_batch_fields_as_single_values() -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(
        {
            "repair_priorities": [],
            "repair_action_batches": [
                {
                    "scope": "kis",
                    "action_type": "refresh_symbol_fundamentals",
                    "count": 1,
                    "symbols": "005930",
                    "warnings": "financial_metrics_sparse",
                    "recommended_actions": "refresh evidence",
                    "priority_types": "repair_queue",
                }
            ],
            "budget_report": {
                "repair_priority_total_count": 1,
                "repair_priority_selected_count": 0,
                "repair_priority_omitted_count": 1,
            },
        }
    )

    batch = contract["action_batches"][0]
    assert batch["symbols"] == ["005930"]
    assert batch["warnings"] == ["financial_metrics_sparse"]
    assert batch["recommended_actions"] == ["refresh evidence"]
    assert batch["priority_types"] == ["repair_queue"]


def test_selector_preserves_repair_queue_horizon_gap_diagnostics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=[],
        content_sections={
            "Current Stance": "open_action_count=1",
            "Durable Facts": "- closed_block_outcomes_without_horizon=3",
            "Evidence Links": "- wiki_repair_queue:repair:outcome-horizon:kis",
            "Trading History": "- source repair queue",
            "Lessons": "- outcome attribution must be separated by horizon",
            "Contradictions": "- block results exist but horizon is unknown",
            "Open Questions": "- which blocks need horizon/lane reprojection?",
            "Next Context Pack Summary": "horizon repair queue open=1",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:outcome-horizon:kis",
                "action_type": "reproject_closed_block_outcome_horizons",
                "status": "scheduled",
                "symbols": [],
                "quality_warnings": ["closed_block_outcome_horizon_missing"],
                "repair_action": (
                    "reproject closed block outcomes so effectiveness is credited "
                    "to the block horizon or crypto lane"
                ),
                "impacted_page_ids": ["kis.application.closed_block_outcomes"],
                "impacted_symbols": [],
                "closed_block_outcomes_without_horizon": 3,
                "closed_block_outcomes_without_horizon_pct": 75.0,
                "diagnostic_reasons": [
                    "closed_block_outcomes_without_horizon:3",
                    "selector_lost_repair_queue_diagnostic_reason",
                ],
                "repair_targets": [
                    {
                        "page_id": "kis.application.closed_block_outcomes",
                        "recommended_action": (
                            "reproject_closed_block_outcomes_with_block_horizon_or_lane"
                        ),
                    }
                ],
            }
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:outcome-horizon:kis"
    )
    assert priority["priority_type"] == "repair_queue"
    assert priority["action_type"] == "reproject_closed_block_outcome_horizons"
    assert priority["decision_use"] == "horizon_lane_attribution_repair"
    assert priority["symbols"] == []
    assert priority["symbol_overlap"] == []
    assert priority["sample_count"] == 3
    assert priority["quality_warnings"] == ["closed_block_outcome_horizon_missing"]
    assert priority["closed_block_outcomes_without_horizon"] == 3
    assert priority["closed_block_outcomes_without_horizon_pct"] == 75.0
    assert priority["diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:3",
        "selector_lost_repair_queue_diagnostic_reason",
    ]
    assert priority["repair_targets"] == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
    ]
    assert "closed_block_outcomes_without_horizon:3" in priority["reasons"]
    assert "closed_block_outcomes_without_horizon_pct:75.0" in priority["reasons"]
    assert (
        "diagnostic:selector_lost_repair_queue_diagnostic_reason"
        in priority["reasons"]
    )
    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    contract_priority = next(
        row
        for row in contract["top_priorities"]
        if row.get("source_id") == "repair:outcome-horizon:kis"
    )
    assert contract_priority["closed_block_outcomes_without_horizon"] == 3
    assert contract_priority["closed_block_outcomes_without_horizon_pct"] == 75.0
    assert contract_priority["diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:3",
        "selector_lost_repair_queue_diagnostic_reason",
    ]
    assert (
        "diagnostic:selector_lost_repair_queue_diagnostic_reason"
        in contract_priority["reasons"]
    )


def test_selector_prioritizes_usage_guidance_repair_queue_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["005930"],
        content_sections={
            "Current Stance": "open_action_count=2",
            "Durable Facts": "- open_action_count=2",
            "Evidence Links": "- wiki_repair_queue:repair:usage-guidance",
            "Trading History": "- source repair queue",
            "Lessons": "- repair before strong reuse",
            "Contradictions": "- none",
            "Open Questions": "- which usage guidance failed?",
            "Next Context Pack Summary": "repair queue open=2",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:fundamentals",
                "action_type": "refresh_symbol_fundamentals",
                "status": "scheduled",
                "symbols": ["005930"],
                "quality_warnings": ["valuation_metrics_sparse"],
                "repair_action": "refresh valuation metrics",
            },
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:usage-guidance",
                "action_type": "repair_usage_guidance_contract",
                "status": "scheduled",
                "symbols": ["005930"],
                "quality_warnings": ["usage_guidance_degraded"],
                "repair_action": (
                    "repair degraded wiki usage guidance before reusing this "
                    "page usage pattern"
                ),
            },
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    assert result.repair_priorities[0]["source_id"] == "repair:usage-guidance"
    assert result.repair_priorities[0]["action_type"] == (
        "repair_usage_guidance_contract"
    )
    assert result.repair_priorities[0]["decision_use"] == (
        "usage_guidance_effectiveness_repair"
    )
    assert "usage_guidance_repair_contract" in result.repair_priorities[0][
        "reasons"
    ]
    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    assert contract["top_priorities"][0]["source_id"] == "repair:usage-guidance"
    assert contract["top_priorities"][0]["decision_use"] == (
        "usage_guidance_effectiveness_repair"
    )


def test_selector_promotes_nested_repair_queue_ref_from_symbol_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.upsert_page_effectiveness(
        {
            "page_id": (
                "repair_target.refresh_symbol_financials_and_rewrite_page_evidence"
            ),
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "",
            "sample_count": 4,
            "win_rate": 0.0,
            "expectancy": -0.7,
            "avg_return_pct": -0.7,
            "helpful_score": -7.0,
            "confidence": 0.8,
            "status": "degraded",
            "reasons": [
                "repair_target:refresh_symbol_financials_and_rewrite_page_evidence",
                "repair_target_prior_status:degraded",
            ],
        }
    )
    page_id = _write_page(
        service,
        scope="kis",
        symbol="245450",
        title="씨앤에스링크 nested repair memory",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.245450:summary",
                "source_refs": [
                    {
                        "source_type": "wiki_repair_queue",
                        "source_id": "repair:nested-financials",
                        "action_type": "refresh_symbol_financials",
                        "status": "scheduled",
                        "symbols": ["245450"],
                        "quality_warnings": ["financials_missing"],
                        "repair_action": "refresh nested symbol financials",
                        "impacted_page_ids": ["kis.symbol.245450"],
                        "impacted_symbols": ["245450"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.245450",
                                "symbol": "245450",
                                "recommended_action": (
                                    "refresh_symbol_financials_and_rewrite_page_evidence"
                                ),
                            }
                        ],
                    }
                ],
            }
        ],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["245450"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "repair_queue"
    )
    assert priority["page_id"] == page_id
    assert priority["source_id"] == "repair:nested-financials"
    assert priority["symbols"] == ["245450"]
    assert priority["symbol_overlap"] == ["245450"]
    assert priority["quality_warnings"] == ["financials_missing"]
    assert priority["repair_action"] == "refresh nested symbol financials"
    assert priority["repair_target_effectiveness"]["status"] == "degraded"
    assert "repair_target_effectiveness:degraded" in priority["reasons"]


def test_selector_keeps_multiple_nested_repair_queue_refs_without_source_ids(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="research",
        key="compressed_nested_repairs",
        title="Compressed nested repairs",
        symbols=["111111", "222222"],
        content_sections={
            "Current Stance": "two symbols need separate repairs",
            "Durable Facts": "- two missing financial repairs",
            "Evidence Links": "- compressed nested repair queue",
            "Trading History": "- no trades yet",
            "Lessons": "- keep symbol-specific repairs separate",
            "Contradictions": "- source ids are missing after compression",
            "Open Questions": "- which repair resolves first?",
            "Next Context Pack Summary": "two nested repairs",
        },
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "compressed:nested-repairs",
                "source_refs": [
                    {
                        "source_type": "wiki_repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "status": "scheduled",
                        "symbols": ["111111"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.111111",
                                "symbol": "111111",
                                "recommended_action": "refresh_symbol_financials",
                            }
                        ],
                    },
                    {
                        "source_type": "wiki_repair_queue",
                        "action_type": "refresh_symbol_financials",
                        "status": "scheduled",
                        "symbols": ["222222"],
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.222222",
                                "symbol": "222222",
                                "recommended_action": "refresh_symbol_financials",
                            }
                        ],
                    },
                ],
            }
        ],
        confidence=0.86,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["111111", "222222"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    repair_priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "repair_queue"
    ]

    assert len(repair_priorities) == 2
    assert {tuple(row["symbols"]) for row in repair_priorities} == {
        ("111111",),
        ("222222",),
    }
    assert {row["source_id"] for row in repair_priorities} == {
        "kis.research.compressed_nested_repairs:repair_queue:111111:refresh_symbol_financials",
        "kis.research.compressed_nested_repairs:repair_queue:222222:refresh_symbol_financials",
    }


def test_selector_prioritizes_degraded_repair_target_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.upsert_page_effectiveness(
        {
            "page_id": "repair_target.fix_second_evidence",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "",
            "sample_count": 6,
            "win_rate": 0.0,
            "expectancy": -1.2,
            "avg_return_pct": -1.2,
            "helpful_score": -9.0,
            "confidence": 1.0,
            "status": "degraded",
            "reasons": ["repair_target:fix_second_evidence"],
        }
    )
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["111111", "222222"],
        content_sections={
            "Current Stance": "open_action_count=2",
            "Durable Facts": "- open_action_count=2",
            "Evidence Links": "- wiki_repair_queue:repair:first\n- wiki_repair_queue:repair:second",
            "Trading History": "- source repair queue",
            "Lessons": "- repair before size increase",
            "Contradictions": "- attractive but incomplete",
            "Open Questions": "- which repair blocks sizing?",
            "Next Context Pack Summary": "repair queue open=2",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:first",
                "action_type": "refresh_symbol_financials",
                "status": "scheduled",
                "symbols": ["111111"],
                "quality_warnings": ["financials_missing"],
                "repair_targets": [
                    {
                        "page_id": "kis.symbol.111111",
                        "symbol": "111111",
                        "recommended_action": "fix_first_evidence",
                    }
                ],
            },
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:second",
                "action_type": "refresh_symbol_financials",
                "status": "scheduled",
                "symbols": ["222222"],
                "quality_warnings": ["financials_missing"],
                "repair_targets": [
                    {
                        "page_id": "kis.symbol.222222",
                        "symbol": "222222",
                        "recommended_action": "fix_second_evidence",
                    }
                ],
            },
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=[],
            max_pages=3,
            max_chars=20_000,
        )
    )

    assert result.repair_priorities[0]["source_id"] == "repair:second"
    assert result.repair_priorities[0]["severity_score"] > (
        result.repair_priorities[1]["severity_score"]
    )
    assert (
        "repair_target_effectiveness:degraded"
        in result.repair_priorities[0]["reasons"]
    )
    assert (
        "repair_target_effectiveness:fix_second_evidence:degraded"
        in result.repair_priorities[0]["reasons"]
    )


def test_selector_keeps_global_quality_warning_effectiveness_in_repair_priorities(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_refs = [
        {
            "source_type": "wiki_repair_queue",
            "source_id": f"repair:symbol:{idx}",
            "action_type": "cross_check_evidence_quality",
            "status": "scheduled",
            "symbols": ["005930"],
            "quality_warnings": ["financials_missing", "valuation_stale_gt_30d"],
            "repair_action": "cross-check symbol evidence before sizing",
        }
        for idx in range(10)
    ]
    source_refs.append(
        {
            "source_type": "wiki_repair_queue",
            "source_id": "repair:quality-warning:financials",
            "action_type": "repair_quality_warning_effectiveness",
            "status": "scheduled",
            "symbols": [],
            "quality_warnings": ["financials_missing"],
            "repair_action": (
                "repair or downgrade evidence carrying financials_missing"
            ),
        }
    )
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["005930"],
        content_sections={
            "Current Stance": "open_action_count=11",
            "Durable Facts": "- open_action_count=11",
            "Evidence Links": "- wiki_repair_queue:repair",
            "Trading History": "- source repair queue",
            "Lessons": "- quality warning effectiveness must survive",
            "Contradictions": "- crowded repair queue",
            "Open Questions": "- which warnings are degraded?",
            "Next Context Pack Summary": "repair queue includes global warning quality",
        },
        source_refs=source_refs,
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    action_types = [row.get("action_type") for row in result.repair_priorities]
    assert "repair_quality_warning_effectiveness" in action_types
    warning_priority = next(
        row
        for row in result.repair_priorities
        if row.get("action_type") == "repair_quality_warning_effectiveness"
    )
    assert warning_priority["decision_use"] == "quality_warning_effectiveness_repair"
    assert warning_priority["hard_blocker"] is False


def test_selector_records_trace_rows_and_rejects_pages_over_budget(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk1"}],
        body="short note",
    )
    rejected_id = _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="SK하이닉스",
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk2"}],
        body="large note " * 600,
    )

    selected_len = len(service.read_page(selected_id)["content"])
    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930", "000660"],
            page_types=["symbol"],
            max_chars=selected_len + 20,
        )
    )

    assert [page.page_id for page in result.pages] == [selected_id]
    assert result.rejected_pages == [
        {
            "page_id": rejected_id,
            "reason": "max_chars_exceeded",
            "char_count": len(service.read_page(rejected_id)["content"]),
        }
    ]
    assert result.budget_report["char_count"] <= selected_len + 20

    with sqlite3.connect(service.config.db_path) as conn:
        run_row = conn.execute(
            """
            SELECT selected_count, rejected_count, char_count, max_chars, status,
                   request_json
            FROM wiki_selection_runs
            WHERE run_id = ?
            """,
            (result.selection_run_id,),
        ).fetchone()
        page_rows = conn.execute(
            """
            SELECT page_id, included, rank
            FROM wiki_selection_pages
            WHERE run_id = ?
            ORDER BY included DESC, rank ASC
            """,
            (result.selection_run_id,),
        ).fetchall()

    request_json = json.loads(run_row[5])
    assert run_row[:5] == (
        1,
        1,
        result.budget_report["char_count"],
        selected_len + 20,
        "ok",
    )
    assert request_json["prompt_mode_application"]["mode_recommendation"] == {}
    assert request_json["prompt_mode_application"]["target_scope"] == "kis"
    assert page_rows == [(selected_id, 1, 1), (rejected_id, 0, 0)]


def test_selector_records_mode_recommendation_in_selection_request_trace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "policy_scorecard", "source_id": "kis"}],
        body="validated wiki page",
    )
    service.initialize()
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:kis-primary-trace",
                "kis",
                "primary",
                "assist",
                50,
                0.75,
                '["samples:50"]',
                "2026-06-28T00:00:00+00:00",
            ),
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    with sqlite3.connect(service.config.db_path) as conn:
        row = conn.execute(
            """
            SELECT request_json
            FROM wiki_selection_runs
            WHERE run_id = ?
            """,
            (result.selection_run_id,),
        ).fetchone()

    request_json = json.loads(row[0])
    application = request_json["prompt_mode_application"]
    assert application["target_scope"] == "kis"
    assert application["mode_recommendation"]["recommendation_id"] == (
        "wiki-mode:kis-primary-trace"
    )
    assert application["mode_recommendation"]["recommended_mode"] == "primary"
    assert application["mode_recommendation"]["sample_count"] == 50


def test_selector_includes_scope_trust_profile_effectiveness_in_trace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "policy_scorecard", "source_id": "kis"}],
        body="validated wiki page",
    )
    application = JueWikiApplicationService(service)
    link = application.record_decision_link(
        selection_run_id="selection:trust-profile-trace",
        manager_run_id="kis-manager-trust-profile-trace",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="primary",
        metadata={
            "jue_wiki_trust_profile": {
                "authority": "primary_compiled_knowledge",
                "trust_level": "high",
                "posture": "validated_mode_recommendation",
                "prompt_mode": "primary",
                "usage_contract": {
                    "risk_posture": "knowledge_spine",
                    "allowed_uses": ["block_thesis_design"],
                    "required_cross_checks": ["live_quote", "risk_gate"],
                },
            },
            "jue_wiki_decision_adjustments": [
                {
                    "source": "usage_contract.risk_posture_guidance",
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                    "current_risk_posture": "knowledge_spine",
                    "current_status": "degraded",
                    "recommended_allowed_uses": ["small_probe_block"],
                    "deprioritized_allowed_uses": ["sizing_context"],
                }
            ],
            "jue_wiki_decision_adjustment_audit_contract": {
                "version": "jue_wiki_decision_adjustment_audit_contract_v1",
                "status": "active",
                "adjustment_count": 1,
                "actions": ["audit_preferred_risk_posture_before_shift"],
                "target_risk_postures": ["repair_probe"],
                "required_review": [
                    "verify why prior shift_to_preferred_risk_posture underperformed",
                ],
                "accepted_resolutions": [
                    "create a smaller repair probe or waiting block",
                ],
                "hard_blocker": False,
                "safety_gates_still_override": True,
            },
        },
    )
    application.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        return_pct=-1.0,
        evidence={"block_id": "trust-profile-trace"},
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
        )
    )

    with sqlite3.connect(service.config.db_path) as conn:
        row = conn.execute(
            """
            SELECT request_json
            FROM wiki_selection_runs
            WHERE run_id = ?
            """,
            (result.selection_run_id,),
        ).fetchone()

    assert result.trust_profile_effectiveness["trust_profiles"][0]["authority"] == (
        "primary_compiled_knowledge"
    )
    assert result.trust_profile_effectiveness["trust_profiles"][0][
        "usage_contract_counts"
    ] == {
        "risk_posture": {"knowledge_spine": 1},
        "allowed_uses": {"block_thesis_design": 1},
        "required_cross_checks": {
            "live_quote": 1,
            "risk_gate": 1,
        },
    }
    assert result.trust_profile_effectiveness["trust_profiles"][0][
        "risk_posture_metrics"
    ][0]["risk_posture"] == "knowledge_spine"
    assert result.trust_profile_effectiveness["trust_profiles"][0][
        "decision_adjustment_metrics"
    ][0]["action"] == "shift_to_preferred_risk_posture"
    assert result.trust_profile_effectiveness["trust_profiles"][0][
        "decision_adjustment_audit_metrics"
    ][0]["action"] == "audit_preferred_risk_posture_before_shift"
    request_json = json.loads(row[0])
    application_trace = request_json["prompt_mode_application"]
    assert application_trace["trust_profile_effectiveness"]["trust_profiles"][0][
        "authority"
    ] == "primary_compiled_knowledge"
    assert application_trace["trust_profile_effectiveness"]["trust_profiles"][0][
        "usage_contract_counts"
    ]["risk_posture"] == {"knowledge_spine": 1}
    assert application_trace["trust_profile_effectiveness"]["trust_profiles"][0][
        "risk_posture_metrics"
    ][0]["status"] == "probe"
    assert application_trace["trust_profile_effectiveness"]["trust_profiles"][0][
        "risk_posture_metrics"
    ][0]["avg_return_pct"] == -1.0
    assert application_trace["trust_profile_effectiveness"]["trust_profiles"][0][
        "decision_adjustment_metrics"
    ][0]["target_risk_posture"] == "repair_probe"
    assert application_trace["trust_profile_effectiveness"]["trust_profiles"][0][
        "decision_adjustment_audit_metrics"
    ][0]["target_risk_posture"] == "repair_probe"


def test_selector_keeps_large_prompt_below_soft_fill_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=20000)
    for idx in range(30):
        _write_page(
            service,
            scope="binance",
            symbol=f"T{idx:02d}USDT",
            title=f"T{idx:02d}USDT",
            source_refs=[{"source_type": "binance_manager_runs", "source_id": str(idx)}],
            body="large wiki page " * 650,
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            max_chars=190_000,
            max_pages=30,
        )
    )

    assert result.status == "ok"
    assert result.budget_report["max_chars"] == 190_000
    assert result.budget_report["selection_budget"] == 161_500
    assert result.budget_report["soft_fill_ratio"] == 0.85
    assert result.budget_report["char_count"] <= 161_500
    assert any(
        page["reason"] == "soft_fill_limit_exceeded"
        for page in result.rejected_pages
    )


def test_selector_prioritizes_trading_validation_risk_memory_with_many_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbols = ["005930", "000660", "402340", "178920"]
    for symbol in symbols:
        _write_page(
            service,
            scope="kis",
            symbol=symbol,
            title=f"{symbol} symbol memory",
            source_refs=[{"source_type": "naver_reports", "source_id": symbol}],
            confidence=0.9,
            freshness="fresh",
        )
    risk_page = service.write_page(
        scope="kis",
        page_type="risk",
        key="trading_validation",
        title="KIS Trading Validation Risk",
        symbols=[],
        content_sections={
            "Current Stance": "쥬는 validation risk를 항상 먼저 본다.",
            "Durable Facts": "readiness=probe",
            "Evidence Links": "trading_validation:kis-latest",
            "Trading History": "sample_count=5",
            "Lessons": "작은 대기진입 probe로 표본을 확장한다.",
            "Contradictions": "none",
            "Open Questions": "which lane improves next?",
            "Next Context Pack Summary": "KIS validation says probe, not stop.",
        },
        source_refs=[
            {"source_type": "trading_validation", "source_id": "kis-latest"},
            {
                "source_type": "trading_validation_discipline",
                "source_id": "kis-latest:sortino_ratio",
            },
        ],
        confidence=0.86,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=symbols,
            page_types=["symbol"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    assert risk_page in selected_ids
    assert selected_ids[0] == risk_page
    assert "operational_memory:trading_validation" in result.pages[0].reasons


def test_selector_keeps_trading_validation_before_block_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_block_id = "bnb_futures_MEGAUSDT_live_block"
    symbol_page = service.write_page(
        scope="binance",
        page_type="symbol",
        key="MEGAUSDT",
        title="MEGAUSDT",
        symbols=["MEGAUSDT"],
        content_sections={
            "Current Stance": "MEGAUSDT live block memory " * 200,
            "Durable Facts": "futures long futures short spot long volatile_attack",
            "Evidence Links": "binance block",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "MEGAUSDT selector summary",
        },
        source_refs=[
            {"source_type": "binance_blocks", "source_id": active_block_id},
            {"source_type": "binance_manager_runs", "source_id": "run-1"},
        ],
        confidence=0.95,
        freshness="fresh",
    )["page_id"]
    risk_page = service.write_page(
        scope="binance",
        page_type="risk",
        key="trading_validation",
        title="BINANCE Trading Validation Risk",
        symbols=[],
        content_sections={
            "Current Stance": "validation risk first",
            "Durable Facts": "readiness=probe; fail_count=10",
            "Evidence Links": "trading_validation:latest",
            "Trading History": "negative expectancy",
            "Lessons": "repair queue before scale",
            "Contradictions": "none",
            "Open Questions": "which lane repairs?",
            "Next Context Pack Summary": "Risk page must survive before symbols.",
        },
        source_refs=[
            {"source_type": "trading_validation", "source_id": "binance-latest"},
            {
                "source_type": "trading_validation_discipline",
                "source_id": "binance-latest:profit_factor",
            },
        ],
        confidence=0.86,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["MEGAUSDT"],
            page_types=["risk", "symbol"],
            lanes=["futures:long", "futures:short", "spot:long", "volatile_attack"],
            block_ids=[active_block_id],
            max_pages=2,
            max_chars=10_000,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    assert selected_ids[0] == risk_page
    assert symbol_page in selected_ids
    assert "operational_memory:trading_validation" in result.pages[0].reasons


def test_selector_prioritizes_live_performance_memory_before_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_block_id = "bnb_futures_FASTUSDT_live"
    symbol_page = service.write_page(
        scope="binance",
        page_type="symbol",
        key="FASTUSDT",
        title="FASTUSDT",
        symbols=["FASTUSDT"],
        content_sections={
            "Current Stance": "FASTUSDT active symbol memory",
            "Durable Facts": "volatile_attack futures_short futures_long",
            "Evidence Links": "binance block",
            "Trading History": "recent block still active",
            "Lessons": "symbol-specific note",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "FASTUSDT summary",
        },
        source_refs=[
            {"source_type": "binance_blocks", "source_id": active_block_id},
            {"source_type": "crypto_quant", "source_id": "FASTUSDT"},
            {"source_type": "crypto_alpha", "source_id": "FASTUSDT"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]
    performance_page = service.write_page(
        scope="binance",
        page_type="performance",
        key="live_outcomes",
        title="BINANCE Live Performance Outcomes",
        symbols=["BTCUSDT", "ETHUSDT"],
        content_sections={
            "Current Stance": "Live outcomes are negative and require repair.",
            "Durable Facts": "sample_count=200; total_net_pnl=-65.5383",
            "Evidence Links": "live_block_performance:latest",
            "Trading History": "recent block outcomes",
            "Lessons": "cost and exit quality need repair before scale.",
            "Contradictions": "gross edge is not net edge.",
            "Open Questions": "which lane repairs first?",
            "Next Context Pack Summary": "live performance must guide aggression.",
            "Performance Evidence": "profit_factor=0.3178",
            "Cost Friction": "cost_to_abs_gross_ratio=0.1368",
            "Latest Blocks": "latest outcomes",
            "Repair Actions": "repair losses before scale",
        },
        source_refs=[
            {"source_type": "live_block_performance", "source_id": "perf-1"},
            {"source_type": "live_block_performance", "source_id": "perf-2"},
        ],
        confidence=0.82,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["FASTUSDT"],
            lanes=["volatile_attack", "futures:short", "futures:long"],
            block_ids=[active_block_id],
            max_pages=1,
            max_chars=20_000,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    assert selected_ids == [performance_page]
    assert symbol_page not in selected_ids
    assert "operational_memory:live_performance" in result.pages[0].reasons


def test_selector_compacts_operational_pages_to_keep_live_performance_in_budget(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    marker = "LIVE_PERFORMANCE_MUST_SURVIVE"
    risk_page = service.write_page(
        scope="binance",
        page_type="risk",
        key="trading_validation",
        title="BINANCE Trading Validation",
        symbols=[],
        content_sections={
            "Current Stance": "Risk validation must remain first.",
            "Durable Facts": "risk " * 1200,
            "Evidence Links": "trading_validation:latest",
            "Trading History": "risk history",
            "Lessons": "risk lesson",
            "Contradictions": "risk contradiction",
            "Open Questions": "risk questions",
            "Next Context Pack Summary": "risk summary",
        },
        source_refs=[{"source_type": "trading_validation", "source_id": "latest"}],
        confidence=0.86,
        freshness="fresh",
    )["page_id"]
    action_page = service.write_page(
        scope="binance",
        page_type="ops",
        key="action_pressure",
        title="BINANCE Action Pressure",
        symbols=[],
        content_sections={
            "Current Stance": "Candidate backlog must be resolved.",
            "Durable Facts": "action pressure " * 1200,
            "Evidence Links": "binance_manager_runs:latest",
            "Trading History": "action history",
            "Lessons": "action lesson",
            "Contradictions": "action contradiction",
            "Open Questions": "action questions",
            "Next Context Pack Summary": "action summary",
        },
        source_refs=[{"source_type": "binance_manager_runs", "source_id": "latest"}],
        confidence=0.84,
        freshness="fresh",
    )["page_id"]
    performance_page = service.write_page(
        scope="binance",
        page_type="performance",
        key="live_outcomes",
        title="BINANCE Live Performance Outcomes",
        symbols=["BTCUSDT"],
        content_sections={
            "Current Stance": f"{marker}: live losses require better entries.",
            "Durable Facts": "performance " * 1200,
            "Evidence Links": "live_block_performance:latest",
            "Trading History": "performance history",
            "Lessons": "performance lesson",
            "Contradictions": "performance contradiction",
            "Open Questions": "performance questions",
            "Next Context Pack Summary": "performance summary",
            "Performance Evidence": "profit_factor=0.42",
            "Repair Actions": "tighten price geometry",
        },
        source_refs=[
            {"source_type": "live_block_performance", "source_id": "latest"}
        ],
        confidence=0.82,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            page_types=["risk", "ops", "performance"],
            max_pages=4,
            max_chars=14_000,
            min_confidence=0.0,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    assert risk_page in selected_ids
    assert action_page in selected_ids
    assert performance_page in selected_ids
    performance = next(page for page in result.pages if page.page_id == performance_page)
    assert marker in performance.content
    assert "compacted_for_selection_budget" in performance.penalties
    assert result.budget_report["char_count"] <= 14_000


def test_selector_prioritizes_action_pressure_before_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbol_page = _write_page(
        service,
        scope="binance",
        symbol="BTCUSDT",
        title="BTCUSDT",
        source_refs=[
            {"source_type": "binance_blocks", "source_id": "blk-btc"},
            {"source_type": "crypto_quant", "source_id": "BTCUSDT"},
        ],
        confidence=1.0,
        freshness="fresh",
        body="BTCUSDT symbol memory with strong evidence",
    )
    action_page = service.write_page(
        scope="binance",
        page_type="ops",
        key="action_pressure",
        title="BINANCE Action Pressure",
        symbols=[],
        content_sections={
            "Current Stance": (
                "candidate pressure exists and must become probe/waiting blocks "
                "or candidate-level rejection."
            ),
            "Durable Facts": (
                "no_action_run_count=4\nzero_action_streak_max=3\n"
                "candidate_count_total=28"
            ),
            "Evidence Links": "binance_manager_runs:2397",
            "Trading History": "hold_decision cycles with candidates",
            "Lessons": "no-action pressure is unresolved trading work.",
            "Contradictions": "research coverage broad but no block created",
            "Open Questions": "which candidate gets a waiting trigger?",
            "Next Context Pack Summary": (
                "Binance action pressure requires next Jue cycle to resolve "
                "candidate backlog into a probe/waiting block or explicit reject."
            ),
        },
        source_refs=[
            {"source_type": "binance_manager_runs", "source_id": "2397"},
        ],
        confidence=0.83,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["BTCUSDT"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    assert selected_ids == [action_page]
    assert symbol_page not in selected_ids
    assert "operational_memory:action_pressure" in result.pages[0].reasons


def test_selector_prioritizes_requested_symbol_with_unresolved_wiki_attention(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    attention_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="245450",
        title="씨앤에스링크",
        symbols=["245450"],
        content_sections={
            "Current Stance": "씨앤에스링크는 위키 수리 attention이 남은 후보.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- kis_manager_runs:7",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=7, status=active, resolution=unresolved, "
                "component=repair_learning_resolution_metrics, "
                "targets=kis.symbol.245450"
            ),
            "Lessons": (
                "Jue Wiki attention is unresolved repair memory: next KIS "
                "manager run must explicitly resolve it."
            ),
            "Contradictions": "- none",
            "Open Questions": "- repair target을 대기블록/기각/탐색 중 무엇으로 해소할까?",
            "Next Context Pack Summary": "위키 attention 해소가 최우선.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "7"}],
        confidence=0.3,
        freshness="fresh",
    )["page_id"]
    ordinary_page = service.write_page(
        scope="kis",
        page_type="research",
        key="ordinary_245450",
        title="씨앤에스링크 ordinary research",
        symbols=["245450"],
        content_sections={
            "Current Stance": "일반 리서치 메모리.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- ordinary research only",
            "Lessons": "- ordinary lesson",
            "Contradictions": "- none",
            "Open Questions": "- ordinary question",
            "Next Context Pack Summary": "일반 리서치 근거.",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["245450"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [attention_page]
    assert ordinary_page not in [page.page_id for page in result.pages]
    assert "operational_memory:wiki_attention" in result.pages[0].reasons


@pytest.mark.parametrize("resolution_status", ["action_metadata", "hold_trigger"])
def test_selector_does_not_prioritize_handled_wiki_attention(
    tmp_path: Path,
    resolution_status: str,
) -> None:
    service = _service(tmp_path)
    handled_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="245450",
        title="씨앤에스링크 handled attention",
        symbols=["245450"],
        content_sections={
            "Current Stance": "씨앤에스링크는 위키 attention을 이미 처리한 후보.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- kis_manager_runs:8",
            "Trading History": (
                "### Jue Wiki Attention\n"
                f"- manager_run=8, status=active, resolution={resolution_status}, "
                "component=wiki_attention, action=live_probe, "
                "targets=kis.symbol.245450"
            ),
            "Lessons": "attention handled by action metadata or hold trigger.",
            "Contradictions": "- none",
            "Open Questions": "- 일반 리서치와 가격 구조를 다시 비교한다.",
            "Next Context Pack Summary": "처리 완료 attention은 긴급 수리 과제가 아니다.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "8"}],
        confidence=0.3,
        freshness="fresh",
    )["page_id"]
    ordinary_page = service.write_page(
        scope="kis",
        page_type="research",
        key="ordinary_245450",
        title="씨앤에스링크 ordinary research",
        symbols=["245450"],
        content_sections={
            "Current Stance": "처리 완료 후 다시 볼 일반 리서치 메모리.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- ordinary research only",
            "Lessons": "- ordinary lesson",
            "Contradictions": "- none",
            "Open Questions": "- ordinary question",
            "Next Context Pack Summary": "일반 리서치 근거.",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["245450"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [ordinary_page]
    assert handled_page not in [page.page_id for page in result.pages]
    assert "operational_memory:wiki_attention" not in result.pages[0].reasons
    assert result.repair_priorities == []


def test_selector_latest_handled_attention_suppresses_older_unresolved_attention(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    attention_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="245450",
        title="씨앤에스링크 mixed attention",
        symbols=["245450"],
        content_sections={
            "Current Stance": "씨앤에스링크는 같은 attention이 최신 런에서 처리됨.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- kis_manager_runs:9\n- kis_manager_runs:8",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=9, status=active, resolution=action_metadata, "
                "component=wiki_attention, action=live_probe, "
                "targets=kis.symbol.245450\n"
                "- manager_run=8, status=active, resolution=unresolved, "
                "component=wiki_attention, action=live_probe, "
                "targets=kis.symbol.245450"
            ),
            "Lessons": "latest manager run handled the older attention.",
            "Contradictions": "- none",
            "Open Questions": "- 일반 리서치와 가격 구조를 다시 비교한다.",
            "Next Context Pack Summary": "최신 handled attention이 과거 unresolved를 덮는다.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "9"}],
        confidence=0.3,
        freshness="fresh",
    )["page_id"]
    ordinary_page = service.write_page(
        scope="kis",
        page_type="research",
        key="ordinary_245450",
        title="씨앤에스링크 ordinary research",
        symbols=["245450"],
        content_sections={
            "Current Stance": "최신 처리 이후 일반 리서치로 다시 보는 후보.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- ordinary research only",
            "Lessons": "- ordinary lesson",
            "Contradictions": "- none",
            "Open Questions": "- ordinary question",
            "Next Context Pack Summary": "일반 리서치 근거.",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["245450"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [ordinary_page]
    assert attention_page not in [page.page_id for page in result.pages]
    assert result.repair_priorities == []


def test_selector_global_handled_attention_suppresses_older_page_unresolved_attention(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    stale_attention_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="245450",
        title="씨앤에스링크 stale attention",
        symbols=["245450"],
        content_sections={
            "Current Stance": "씨앤에스링크는 예전 위키 attention이 남은 페이지.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- kis_manager_runs:8",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=8, status=active, resolution=unresolved, "
                "component=wiki_attention, action=live_probe, "
                "targets=kis.symbol.245450"
            ),
            "Lessons": "older unresolved attention before latest manager run.",
            "Contradictions": "- none",
            "Open Questions": "- 최신 처리 상태와 비교해야 한다.",
            "Next Context Pack Summary": "오래된 unresolved attention.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "8"}],
        confidence=0.3,
        freshness="fresh",
    )["page_id"]
    handled_marker_page = service.write_page(
        scope="kis",
        page_type="research",
        key="handled_attention_marker_245450",
        title="씨앤에스링크 handled attention marker",
        symbols=[],
        content_sections={
            "Current Stance": "최신 manager run에서 같은 attention을 처리했다.",
            "Durable Facts": "- scope=kis",
            "Evidence Links": "- kis_manager_runs:9",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=9, status=active, resolution=action_metadata, "
                "component=wiki_attention, action=live_probe, "
                "targets=kis.symbol.245450"
            ),
            "Lessons": "latest run resolved the symbol attention.",
            "Contradictions": "- none",
            "Open Questions": "- 일반 리서치와 가격 구조를 다시 비교한다.",
            "Next Context Pack Summary": "최신 handled attention은 전역적으로 유효하다.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "9"}],
        confidence=0.1,
        freshness="fresh",
    )["page_id"]
    ordinary_page = service.write_page(
        scope="kis",
        page_type="research",
        key="ordinary_245450",
        title="씨앤에스링크 ordinary research",
        symbols=["245450"],
        content_sections={
            "Current Stance": "최신 처리 이후 일반 리서치로 다시 보는 후보.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- ordinary research only",
            "Lessons": "- ordinary lesson",
            "Contradictions": "- none",
            "Open Questions": "- ordinary question",
            "Next Context Pack Summary": "일반 리서치 근거.",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["245450"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [ordinary_page]
    assert stale_attention_page not in [page.page_id for page in result.pages]
    assert handled_marker_page not in [page.page_id for page in result.pages]
    assert result.repair_priorities == []
    assert result.requested_symbol_summaries[0]["page_id"] == ordinary_page
    trading_history = result.requested_symbol_summaries[0]["memory_card"].get(
        "trading_history",
        "",
    )
    assert "resolution=unresolved" not in trading_history
    assert "resolution=unresolved" not in str(result.requested_symbol_summaries)


def test_selector_newer_unresolved_attention_overrides_older_handled_string_run_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    handled_marker_page = service.write_page(
        scope="kis",
        page_type="research",
        key="handled_attention_marker_245450",
        title="씨앤에스링크 old handled attention marker",
        symbols=[],
        content_sections={
            "Current Stance": "이전 manager run에서는 attention을 처리했다.",
            "Durable Facts": "- scope=kis",
            "Evidence Links": "- kis_manager_runs:run-old",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=run-old, observed_at=2026-07-04T08:30:00+09:00, "
                "status=active, resolution=action_metadata, "
                "component=wiki_attention, action=live_probe, "
                "targets=kis.symbol.245450"
            ),
            "Lessons": "old run handled a previous attention.",
            "Contradictions": "- none",
            "Open Questions": "- 최신 unresolved가 다시 생겼는지 확인한다.",
            "Next Context Pack Summary": "오래된 handled marker.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "run-old"}],
        confidence=0.1,
        freshness="fresh",
    )["page_id"]
    attention_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="245450",
        title="씨앤에스링크 newer unresolved attention",
        symbols=["245450"],
        content_sections={
            "Current Stance": "최신 manager run에서 같은 attention이 다시 unresolved.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- kis_manager_runs:run-new",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=run-new, observed_at=2026-07-04T09:00:00+09:00, "
                "status=active, resolution=unresolved, "
                "component=wiki_attention, action=live_probe, "
                "targets=kis.symbol.245450"
            ),
            "Lessons": "new unresolved attention should override old handled marker.",
            "Contradictions": "- none",
            "Open Questions": "- 새 unresolved를 대기블록/기각/탐색 중 무엇으로 해소할까?",
            "Next Context Pack Summary": "최신 unresolved attention이 우선.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "run-new"}],
        confidence=0.3,
        freshness="fresh",
    )["page_id"]
    ordinary_page = service.write_page(
        scope="kis",
        page_type="research",
        key="ordinary_245450",
        title="씨앤에스링크 ordinary research",
        symbols=["245450"],
        content_sections={
            "Current Stance": "일반 리서치 메모리.",
            "Durable Facts": "- scope=kis\n- symbol=245450",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- ordinary research only",
            "Lessons": "- ordinary lesson",
            "Contradictions": "- none",
            "Open Questions": "- ordinary question",
            "Next Context Pack Summary": "일반 리서치 근거.",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["245450"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [attention_page]
    assert handled_marker_page not in [page.page_id for page in result.pages]
    assert ordinary_page not in [page.page_id for page in result.pages]
    assert result.repair_priorities[0]["source_id"] == (
        "kis.symbol.245450:manager_run:run-new:attention:"
        "wiki_attention:live_probe:kis.symbol.245450"
    )


def test_selector_promotes_unresolved_wiki_attention_to_repair_priorities(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="binance",
        page_type="symbol",
        key="ETHUSDT",
        title="ETHUSDT",
        symbols=["ETHUSDT"],
        content_sections={
            "Current Stance": "ETHUSDT has unresolved Jue Wiki attention.",
            "Durable Facts": "- scope=binance\n- symbol=ETHUSDT",
            "Evidence Links": "- binance_manager_runs:9",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=9, status=active, resolution=unresolved, "
                "component=spot_underuse_live_probe, action=live_probe, "
                "must=probe_next, targets=binance.symbol.ETHUSDT, "
                "recommended=현물 미사용 문제를 실행 가능한 spot 대기블록으로 검증한다."
            ),
            "Lessons": (
                "Jue Wiki attention is unresolved repair memory: the next "
                "Binance manager run must explicitly resolve it."
            ),
            "Contradictions": "- none",
            "Open Questions": "- spot 대기블록 조건을 만들 수 있는가?",
            "Next Context Pack Summary": "resolve ETHUSDT wiki attention.",
        },
        source_refs=[{"source_type": "binance_manager_runs", "source_id": "9"}],
        confidence=0.8,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["ETHUSDT"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    priority = result.repair_priorities[0]
    assert priority["priority_type"] == "wiki_attention"
    assert priority["source_type"] == "jue_wiki_attention"
    assert priority["source_id"] == (
        "binance.symbol.ETHUSDT:manager_run:9:attention:"
        "spot_underuse_live_probe:live_probe:binance.symbol.ETHUSDT"
    )
    assert priority["symbols"] == ["ETHUSDT"]
    assert priority["impacted_page_ids"] == ["binance.symbol.ETHUSDT"]
    assert priority["action_type"] == "live_probe"
    assert priority["repair_status"] == "unresolved"
    assert "spot_underuse_live_probe" in priority["repair_action"]

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    assert contract["top_priorities"][0]["priority_type"] == "wiki_attention"
    assert contract["top_priorities"][0]["action_type"] == "live_probe"
    assert contract["top_priorities"][0]["candidate_resolution_required"] is True


def test_selector_preserves_multiple_wiki_attention_refs_from_same_manager_run(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="binance",
        page_type="symbol",
        key="SOLUSDT",
        title="SOLUSDT",
        symbols=["SOLUSDT"],
        content_sections={
            "Current Stance": "SOLUSDT has multiple unresolved wiki attention items.",
            "Durable Facts": "- scope=binance\n- symbol=SOLUSDT",
            "Evidence Links": "- binance_manager_runs:11",
            "Trading History": (
                "### Jue Wiki Attention\n"
                "- manager_run=11, status=active, resolution=unresolved, "
                "component=memory_card_quality, "
                "action=cross_check_memory_card_quality, "
                "must=additional_attention, targets=binance.symbol.SOLUSDT, "
                "recommended=SOLUSDT 위키 기억을 최근 체결과 교차검증한다.\n"
                "- manager_run=11, status=active, resolution=unresolved, "
                "component=spot_lane_probe, action=live_probe, "
                "must=repair_now, targets=binance.symbol.SOLUSDT, "
                "recommended=SOLUSDT spot lane 대기블록을 검증한다."
            ),
            "Lessons": "Both attention rows must survive source identity compaction.",
            "Contradictions": "- none",
            "Open Questions": "- 두 attention을 각각 어떻게 해소할까?",
            "Next Context Pack Summary": "resolve both SOLUSDT wiki attention rows.",
        },
        source_refs=[{"source_type": "binance_manager_runs", "source_id": "11"}],
        confidence=0.8,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["SOLUSDT"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    wiki_priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "wiki_attention"
    ]
    assert len(wiki_priorities) == 2
    assert {row["action_type"] for row in wiki_priorities} == {
        "cross_check_memory_card_quality",
        "live_probe",
    }
    assert len({row["source_id"] for row in wiki_priorities}) == 2

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    assert {
        row["action_type"]
        for row in contract["top_priorities"]
        if row.get("priority_type") == "wiki_attention"
    } == {"cross_check_memory_card_quality", "live_probe"}


def test_selector_promotes_unresolved_memory_card_quality_to_repair_priorities(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "005930 memory card is weak.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- kis_manager_runs:11",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence, "
                "missing_fields=durable_facts|lessons, "
                "required_checks=refresh_durable_facts_from_reports_fundamentals_and_market_context|"
                "review_block_history_and_reflections_for_lessons"
            ),
            "Lessons": (
                "Jue Wiki memory card quality is unresolved repair memory: "
                "weak symbol memory must be cross-checked."
            ),
            "Contradictions": "- none",
            "Open Questions": "- 최신 리서치와 밸류로 보강해야 한다.",
            "Next Context Pack Summary": (
                "memory card quality repair active: "
                "cross_check_live_research_before_high_confidence"
            ),
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "11"}],
        confidence=0.8,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    priority = result.repair_priorities[0]
    assert priority["priority_type"] == "memory_card_quality"
    assert priority["source_type"] == "jue_wiki_memory_card_quality"
    assert priority["source_id"] == (
        "kis.symbol.005930:manager_run:11:memory_card_quality:"
        "cross_check_live_research_before_high_confidence:005930:"
        "fields:durable_facts:lessons"
    )
    assert priority["symbols"] == ["005930"]
    assert priority["action_type"] == "cross_check_memory_card_quality"
    assert priority["decision_use"] == "memory_card_quality_resolution_check"
    assert priority["repair_status"] == "unresolved"
    assert "cross_check_live_research_before_high_confidence" in priority[
        "repair_action"
    ]
    assert priority["missing_fields"] == ["durable_facts", "lessons"]
    assert priority["required_checks"] == [
        "refresh_durable_facts_from_reports_fundamentals_and_market_context",
        "review_block_history_and_reflections_for_lessons",
    ]

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    assert contract["top_priorities"][0]["priority_type"] == "memory_card_quality"
    assert contract["top_priorities"][0]["candidate_resolution_required"] is True
    assert contract["top_priorities"][0]["missing_fields"] == [
        "durable_facts",
        "lessons",
    ]
    assert contract["top_priorities"][0]["required_checks"] == [
        "refresh_durable_facts_from_reports_fundamentals_and_market_context",
        "review_block_history_and_reflections_for_lessons",
    ]
    assert contract["attention_plan_response_contract"]["repair_now"][
        "missing_fields"
    ] == [
        "durable_facts",
        "lessons",
    ]
    assert contract["attention_plan_response_contract"]["repair_now"][
        "required_checks"
    ] == [
        "refresh_durable_facts_from_reports_fundamentals_and_market_context",
        "review_block_history_and_reflections_for_lessons",
    ]


def test_selector_preserves_multiple_memory_card_quality_refs_from_same_manager_run(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "005930 memory card has two quality gaps.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- kis_manager_runs:11",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=refresh_recent_report_before_scale"
            ),
            "Lessons": (
                "Both memory-card-quality rows must remain individually repairable."
            ),
            "Contradictions": "- none",
            "Open Questions": "- 최신 리서치와 리포트 근거를 각각 보강해야 한다.",
            "Next Context Pack Summary": "resolve both memory-card quality rows.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "11"}],
        confidence=0.8,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "memory_card_quality"
    ]
    assert len(priorities) == 2
    assert {row["repair_action"] for row in priorities} == {
        "cross_check_live_research_before_high_confidence",
        "refresh_recent_report_before_scale",
    }
    assert len({row["source_id"] for row in priorities}) == 2

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    assert {
        row["repair_action"]
        for row in contract["top_priorities"]
        if row.get("priority_type") == "memory_card_quality"
    } == {
        "cross_check_live_research_before_high_confidence",
        "refresh_recent_report_before_scale",
    }


def test_selector_prioritizes_core_memory_card_quality_gaps(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "005930 memory card has core gaps.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- kis_manager_runs:11",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence, "
                "missing_fields=durable_facts|lessons"
            ),
            "Lessons": "Core memory gaps should rank first.",
            "Contradictions": "- none",
            "Open Questions": "- none",
            "Next Context Pack Summary": "resolve core memory gaps.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "11"}],
        confidence=0.8,
        freshness="fresh",
    )
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="000660",
        title="SK하이닉스",
        symbols=["000660"],
        content_sections={
            "Current Stance": "000660 memory card only lacks open questions.",
            "Durable Facts": "- scope=kis\n- symbol=000660",
            "Evidence Links": "- kis_manager_runs:12",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=12, observed_at=2026-07-01T09:31:00+09:00, "
                "status=active, resolution=unresolved, symbols=000660, "
                "required=record_open_questions_before_confident_action, "
                "missing_fields=open_questions"
            ),
            "Lessons": "Open question gaps are still useful but less severe.",
            "Contradictions": "- none",
            "Open Questions": "- none",
            "Next Context Pack Summary": "resolve open question gap.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "12"}],
        confidence=0.8,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=[],
            max_pages=4,
            max_chars=20_000,
        )
    )

    priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "memory_card_quality"
    ]
    assert [row["symbols"][0] for row in priorities[:2]] == ["005930", "000660"]
    assert priorities[0]["severity_score"] > priorities[1]["severity_score"]
    assert "core_missing_fields:durable_facts|lessons" in priorities[0]["reasons"]


def test_selector_latest_handled_memory_card_quality_suppresses_older_unresolved(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    stale_quality_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 memory quality handled",
        symbols=["005930"],
        content_sections={
            "Current Stance": "005930 memory-card quality was handled later.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- kis_manager_runs:12\n- kis_manager_runs:11",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=12, observed_at=2026-07-01T09:40:00+09:00, "
                "status=active, resolution=action_metadata, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence"
            ),
            "Lessons": "latest run handled weak memory-card quality.",
            "Contradictions": "- none",
            "Open Questions": "- 일반 리서치와 가격 구조를 다시 비교한다.",
            "Next Context Pack Summary": "처리 완료 memory-card quality.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "12"}],
        confidence=0.3,
        freshness="fresh",
    )["page_id"]
    ordinary_page = service.write_page(
        scope="kis",
        page_type="research",
        key="ordinary_005930",
        title="삼성전자 ordinary research",
        symbols=["005930"],
        content_sections={
            "Current Stance": "처리 완료 후 다시 볼 일반 리서치 메모리.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- ordinary research only",
            "Lessons": "- ordinary lesson",
            "Contradictions": "- none",
            "Open Questions": "- ordinary question",
            "Next Context Pack Summary": "일반 리서치 근거.",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [ordinary_page]
    assert stale_quality_page not in [page.page_id for page in result.pages]
    assert result.repair_priorities == []


def test_selector_handled_memory_card_quality_with_different_missing_fields_keeps_core_gap(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 mixed memory quality",
        symbols=["005930"],
        content_sections={
            "Current Stance": "005930 has one handled soft gap and one core gap.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- kis_manager_runs:12\n- kis_manager_runs:11",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=12, observed_at=2026-07-01T09:40:00+09:00, "
                "status=active, resolution=action_metadata, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence, "
                "missing_fields=open_questions\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence, "
                "missing_fields=durable_facts|lessons"
            ),
            "Lessons": "Core memory-card gaps remain unresolved.",
            "Contradictions": "- none",
            "Open Questions": "- 최신 처리 상태와 누락 필드를 구분해야 한다.",
            "Next Context Pack Summary": "core memory-card quality gap.",
        },
        source_refs=[
            {"source_type": "kis_manager_runs", "source_id": "12"},
            {"source_type": "kis_manager_runs", "source_id": "11"},
        ],
        confidence=0.4,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "memory_card_quality"
    ]
    assert len(priorities) == 1
    assert priorities[0]["repair_status"] == "unresolved"
    assert priorities[0]["missing_fields"] == ["durable_facts", "lessons"]
    assert "core_missing_fields:durable_facts|lessons" in priorities[0]["reasons"]


def test_selector_global_handled_memory_card_quality_suppresses_older_unresolved(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    stale_quality_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 stale memory quality",
        symbols=["005930"],
        content_sections={
            "Current Stance": "005930 memory-card quality has older unresolved.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- kis_manager_runs:11",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence"
            ),
            "Lessons": "older unresolved before latest manager run.",
            "Contradictions": "- none",
            "Open Questions": "- 최신 처리 상태와 비교해야 한다.",
            "Next Context Pack Summary": "오래된 unresolved memory-card quality.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "11"}],
        confidence=0.3,
        freshness="fresh",
    )["page_id"]
    handled_marker_page = service.write_page(
        scope="kis",
        page_type="research",
        key="handled_memory_quality_005930",
        title="삼성전자 handled memory quality marker",
        symbols=[],
        content_sections={
            "Current Stance": "최신 manager run에서 memory-card quality를 처리했다.",
            "Durable Facts": "- scope=kis",
            "Evidence Links": "- kis_manager_runs:12",
            "Trading History": (
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=12, observed_at=2026-07-01T09:40:00+09:00, "
                "status=active, resolution=action_metadata, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence"
            ),
            "Lessons": "latest run resolved the symbol memory-card quality.",
            "Contradictions": "- none",
            "Open Questions": "- 일반 리서치와 가격 구조를 다시 비교한다.",
            "Next Context Pack Summary": "최신 handled memory-card quality.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "12"}],
        confidence=0.1,
        freshness="fresh",
    )["page_id"]
    ordinary_page = service.write_page(
        scope="kis",
        page_type="research",
        key="ordinary_005930",
        title="삼성전자 ordinary research",
        symbols=["005930"],
        content_sections={
            "Current Stance": "최신 처리 이후 일반 리서치로 다시 보는 후보.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- ordinary research only",
            "Lessons": "- ordinary lesson",
            "Contradictions": "- none",
            "Open Questions": "- ordinary question",
            "Next Context Pack Summary": "일반 리서치 근거.",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [ordinary_page]
    assert stale_quality_page not in [page.page_id for page in result.pages]
    assert handled_marker_page not in [page.page_id for page in result.pages]
    assert result.repair_priorities == []


def test_requested_symbol_memory_card_filters_superseded_memory_card_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbol_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 memory quality summary",
        symbols=["005930"],
        content_sections={
            "Current Stance": "005930 memory-card quality is now handled.",
            "Durable Facts": "- scope=kis\n- symbol=005930",
            "Evidence Links": "- kis_manager_runs:12\n- kis_manager_runs:11",
            "Trading History": (
                "- ordinary block history\n"
                "### Jue Wiki Memory Card Quality\n"
                "- manager_run=12, observed_at=2026-07-01T09:40:00+09:00, "
                "status=active, resolution=action_metadata, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence\n"
                "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                "status=active, resolution=unresolved, symbols=005930, "
                "required=cross_check_live_research_before_high_confidence"
            ),
            "Lessons": "latest run handled weak memory-card quality.",
            "Contradictions": "- none",
            "Open Questions": "- 일반 리서치와 가격 구조를 다시 비교한다.",
            "Next Context Pack Summary": "처리 완료 memory-card quality.",
        },
        source_refs=[
            {"source_type": "kis_manager_runs", "source_id": "12"},
            {"source_type": "naver_reports", "source_id": "005930:report"},
            {"source_type": "symbol_fundamentals", "source_id": "005930:fund"},
        ],
        confidence=0.9,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    assert result.requested_symbol_summaries[0]["page_id"] == symbol_page
    trading_history = result.requested_symbol_summaries[0]["memory_card"][
        "trading_history"
    ]
    assert "resolution=action_metadata" in trading_history
    assert "resolution=unresolved" not in trading_history


def test_selector_preserves_requested_symbol_summary_when_ops_page_wins(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbol_page = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {"source_type": "naver_reports", "source_id": "005930:report"},
            {"source_type": "symbol_fundamentals", "source_id": "005930:fund"},
        ],
        confidence=0.92,
        freshness="fresh",
        body="삼성전자 symbol memory with valuation, report, and block lessons",
    )
    action_page = service.write_page(
        scope="kis",
        page_type="ops",
        key="action_pressure",
        title="KIS Action Pressure",
        symbols=[],
        content_sections={
            "Current Stance": "candidate pressure must be resolved.",
            "Durable Facts": "zero_action_streak=3",
            "Evidence Links": "kis_manager_runs:77",
            "Trading History": "hold cycles",
            "Lessons": "resolve backlog.",
            "Contradictions": "none",
            "Open Questions": "which candidate gets trigger?",
            "Next Context Pack Summary": "KIS action pressure summary.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "77"}],
        confidence=0.86,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [action_page]
    assert result.requested_symbol_summaries[0]["page_id"] == symbol_page
    assert result.requested_symbol_summaries[0]["symbol"] == "005930"
    assert "삼성전자 selector summary" in result.requested_symbol_summaries[0][
        "summary"
    ]
    card = result.requested_symbol_summaries[0]["memory_card"]
    assert "삼성전자 symbol memory" in card["stance"]
    assert "durable facts" in card["durable_facts"]
    assert "history" in card["trading_history"]
    assert "lessons" in card["lessons"]
    assert "questions" in card["open_questions"]
    assert result.budget_report["requested_symbol_summary_count"] == 1


def test_selector_requested_symbol_summary_prefers_stronger_research_memory(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    weak_symbol_page = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 weak symbol memory",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:old-fund",
                "quality_status": "weak",
                "quality_warnings": ["valuation_stale_gt_30d"],
            }
        ],
        confidence=0.2,
        freshness="stale",
        body="삼성전자 오래된 symbol memory",
    )
    strong_research_page = service.write_page(
        scope="kis",
        page_type="research",
        key="samsung_deep_research",
        title="삼성전자 fresh research memory",
        symbols=["005930"],
        content_sections={
            "Current Stance": "삼성전자 최신 리서치/밸류/수급 근거가 결합된 메모리.",
            "Durable Facts": "- scope=kis\n- symbol=005930\n- evidence=strong",
            "Evidence Links": "- naver_reports:r1\n- rag:r2\n- symbol_fundamentals:f3",
            "Trading History": "- fresh research history",
            "Lessons": "- fresh research lesson",
            "Contradictions": "- none",
            "Open Questions": "- 다음 가격 구조를 확인한다.",
            "Next Context Pack Summary": "삼성전자 fresh research selector summary",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "r1"},
            {"source_type": "rag", "source_id": "r2"},
            {"source_type": "symbol_fundamentals", "source_id": "f3"},
        ],
        confidence=0.95,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert result.requested_symbol_summaries[0]["page_id"] == strong_research_page
    assert result.requested_symbol_summaries[0]["symbol"] == "005930"
    assert weak_symbol_page != strong_research_page
    assert "fresh research selector summary" in result.requested_symbol_summaries[0][
        "summary"
    ]


def test_selector_requested_symbol_summary_prefers_active_effectiveness_over_degraded_confidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930-active-summary",
        title="삼성전자 active summary",
        symbols=["005930"],
        content_sections={
            "Current Stance": "active page",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "active effectiveness selector summary",
        },
        source_refs=[{"source_type": "test", "source_id": "active-summary"}],
        confidence=0.72,
        freshness="fresh",
    )["page_id"]
    degraded_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930-degraded-summary",
        title="삼성전자 degraded high confidence summary",
        symbols=["005930"],
        content_sections={
            "Current Stance": "degraded page",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "degraded confidence selector summary",
        },
        source_refs=[{"source_type": "test", "source_id": "degraded-summary"}],
        confidence=0.99,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": active_page,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 6,
            "win_rate": 0.66,
            "expectancy": 0.45,
            "helpful_score": 7.0,
            "confidence": 0.9,
            "status": "active",
            "reasons": ["active summary worked"],
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": degraded_page,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 6,
            "win_rate": 0.0,
            "expectancy": -0.8,
            "helpful_score": -9.0,
            "confidence": 0.9,
            "status": "degraded",
            "reasons": [
                "application_repair_queue_pressure",
                "repair_queue_open_count:1",
            ],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            horizons=["mid"],
            max_pages=1,
            max_chars=20_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    summary = result.requested_symbol_summaries[0]
    assert summary["page_id"] == active_page
    assert summary["effectiveness"]["status"] == "active"
    assert "active effectiveness selector summary" in summary["summary"]
    assert degraded_page in [
        row.get("page_id") for row in result.repair_priorities
    ]


def test_selector_requested_symbol_summary_exposes_quality_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbol_page = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:fund",
                "quality_status": "weak",
                "quality_warnings": [
                    "valuation_stale_gt_30d",
                    "price_missing",
                ],
            }
        ],
        confidence=0.92,
        freshness="stale",
        body="삼성전자 stale symbol memory",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    summary = result.requested_symbol_summaries[0]
    assert summary["page_id"] == symbol_page
    assert summary["freshness"] == "stale"
    assert summary["freshness_status"] == "stale"
    assert summary["freshness_warnings"] == ["freshness_label_stale"]
    assert summary["quality_status"] == "weak"
    assert set(summary["quality_warnings"]) == {
        "valuation_stale_gt_30d",
        "price_missing",
    }
    assert summary["updated_at"]


def test_selector_budget_report_marks_degraded_requested_symbol_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:old-fund",
                "quality_status": "weak",
                "quality_warnings": ["valuation_stale_gt_30d"],
            }
        ],
        confidence=0.92,
        freshness="stale",
        body="삼성전자 오래된 requested summary",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert result.budget_report["requested_symbol_summary_coverage_status"] == "full"
    assert result.budget_report["requested_symbol_degraded_summary_count"] == 1
    assert result.budget_report["requested_symbol_degraded_summary_symbols"] == [
        "005930"
    ]
    assert result.budget_report["requested_symbol_degraded_summary_reasons"] == [
        {
            "symbol": "005930",
            "freshness": "stale",
            "freshness_status": "stale",
            "freshness_warnings": ["freshness_label_stale"],
            "quality_status": "weak",
            "quality_warnings": ["valuation_stale_gt_30d"],
        }
    ]
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:degraded_summary:kis:005930"
    )
    assert priority["action_type"] == "refresh_requested_symbol_summary"
    assert priority["symbols"] == ["005930"]
    assert priority["quality_warnings"] == [
        "requested_symbol_summary_degraded",
        "valuation_stale_gt_30d",
        "freshness_label_stale",
    ]
    assert priority["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": (
                "refresh_stale_or_weak_requested_symbol_wiki_summary"
            ),
        }
    ]


def test_selector_budget_report_marks_age_stale_current_summary_degraded(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:fresh-looking-fund",
                "quality_status": "ok",
            }
        ],
        confidence=0.92,
        freshness="current",
        body="삼성전자 current 라벨이지만 오래된 requested summary",
    )
    old_updated_at = (
        datetime.now(timezone.utc) - timedelta(days=21)
    ).isoformat()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            "UPDATE wiki_pages SET updated_at = ? WHERE page_id = ?",
            (old_updated_at, page_id),
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    summary = result.requested_symbol_summaries[0]
    assert summary["freshness"] == "current"
    assert summary["freshness_status"] == "stale"
    assert summary["freshness_warnings"] == ["updated_at_stale_gt_14d"]
    assert result.budget_report["requested_symbol_degraded_summary_symbols"] == [
        "005930"
    ]
    assert result.budget_report["requested_symbol_degraded_summary_reasons"] == [
        {
            "symbol": "005930",
            "freshness": "current",
            "freshness_status": "stale",
            "freshness_warnings": ["updated_at_stale_gt_14d"],
            "quality_status": "strong",
        }
    ]
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:degraded_summary:kis:005930"
    )
    assert priority["quality_warnings"] == [
        "requested_symbol_summary_degraded",
        "updated_at_stale_gt_14d",
    ]


def test_requested_symbol_coverage_repair_priorities_canonicalize_degraded_alias(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    priorities = JueWikiSelector(service)._requested_symbol_coverage_repair_priorities(
        pages=[],
        requested_symbol_summary_coverage={
            "requested_symbol_summary_coverage_status": "full",
            "requested_symbol_degraded_summary_symbols": ["005930"],
            "requested_symbol_degraded_summary_reasons": [
                {
                    "symbol": "005930",
                    "freshness": "stale",
                    "quality_status": "degraded",
                    "quality_warnings": ["valuation_stale_gt_30d"],
                }
            ],
        },
        target_scope="kis",
        requested_symbols={"005930"},
    )

    priority = priorities[0]
    assert priority["quality_status"] == "weak"
    assert "quality_status:weak" in priority["reasons"]
    assert "quality_status:degraded" not in priority["reasons"]


def test_selector_keeps_degraded_summary_repair_when_other_symbol_repair_is_open(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:old-fund",
                "quality_status": "weak",
                "quality_warnings": ["valuation_stale_gt_30d"],
            }
        ],
        confidence=0.92,
        freshness="stale",
        body="삼성전자 오래된 requested summary",
    )
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["005930"],
        content_sections={
            "Current Stance": "open_action_count=1",
            "Durable Facts": "- open_action_count=1",
            "Evidence Links": "- wiki_repair_queue:repair:financials:kis:005930",
            "Trading History": "- source repair queue",
            "Lessons": "- repair financials before size increase",
            "Contradictions": "- stale summary still needs its own refresh",
            "Open Questions": "- which repair blocks sizing?",
            "Next Context Pack Summary": "repair queue open=1",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:financials:kis:005930",
                "action_type": "refresh_symbol_financials",
                "status": "scheduled",
                "symbols": ["005930"],
                "quality_warnings": ["financials_missing"],
                "repair_action": "collect or cross-check financial statements",
                "impacted_page_ids": ["kis.symbol.005930"],
                "impacted_symbols": ["005930"],
                "repair_targets": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbol": "005930",
                        "recommended_action": (
                            "refresh_symbol_financials_and_rewrite_page_evidence"
                        ),
                    }
                ],
            }
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    assert any(
        row.get("source_id") == "repair:financials:kis:005930"
        for row in result.repair_priorities
    )
    degraded_priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:degraded_summary:kis:005930"
    )
    assert degraded_priority["priority_type"] == "requested_symbol_degraded_summary"
    assert degraded_priority["quality_warnings"] == [
        "requested_symbol_summary_degraded",
        "valuation_stale_gt_30d",
        "freshness_label_stale",
    ]


def test_selector_records_degraded_requested_symbol_summary_as_repair_action(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:old-fund",
                "quality_status": "weak",
                "quality_warnings": ["valuation_stale_gt_30d"],
            }
        ],
        confidence=0.92,
        freshness="stale",
        body="삼성전자 오래된 requested summary",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT action_id, finding_id, page_id, action_type, status, details_json
            FROM wiki_repair_actions
            WHERE action_id = 'repair:degraded_summary:kis:005930'
            """
        ).fetchone()

    assert row is not None
    action_id, finding_id, page_id, action_type, status, details_json = row
    details = json.loads(details_json)
    assert action_id == "repair:degraded_summary:kis:005930"
    assert finding_id == "requested_symbol_degraded_summary:kis:005930"
    assert page_id == "kis.symbol.005930"
    assert action_type == "refresh_requested_symbol_summary"
    assert status == "scheduled"
    assert details["decision_scope"] == "kis"
    assert details["selection_run_id"] == result.selection_run_id
    assert details["symbols"] == ["005930"]
    assert details["quality_warnings"] == [
        "requested_symbol_summary_degraded",
        "valuation_stale_gt_30d",
        "freshness_label_stale",
    ]
    assert details["repair_action"] == (
        "refresh_stale_or_weak_requested_symbol_wiki_summary"
    )


def test_selector_budget_report_records_requested_symbol_summary_gaps(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbols = [f"00000{idx}" for idx in range(1, 10)]
    for symbol in symbols:
        _write_page(
            service,
            scope="kis",
            symbol=symbol,
            title=f"종목{symbol}",
            source_refs=[
                {"source_type": "naver_reports", "source_id": f"{symbol}:report"}
            ],
            confidence=0.8,
            freshness="fresh",
            body=f"{symbol} summary memory",
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=symbols,
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert len(result.requested_symbol_summaries) == 8
    assert result.budget_report["requested_symbol_count"] == 9
    assert result.budget_report["requested_symbol_summary_count"] == 8
    assert result.budget_report["requested_symbol_unsummarized_count"] == 1
    assert result.budget_report["requested_symbol_summary_coverage_status"] == "partial"
    assert result.budget_report["requested_symbol_unsummarized_symbols"] == ["000009"]


def test_selector_does_not_repair_symbols_omitted_only_by_summary_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbols = [f"00000{idx}" for idx in range(1, 10)]
    for symbol in symbols:
        _write_page(
            service,
            scope="kis",
            symbol=symbol,
            title=f"종목{symbol}",
            source_refs=[
                {"source_type": "naver_reports", "source_id": f"{symbol}:report"}
            ],
            confidence=0.8,
            freshness="fresh",
            body=f"{symbol} summary memory",
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=symbols,
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert result.budget_report["requested_symbol_prompt_omitted_symbols"] == [
        "000009"
    ]
    assert result.budget_report["requested_symbol_missing_summary_symbols"] == []
    assert not [
        row
        for row in result.repair_priorities
        if row.get("action_type") == "refresh_requested_symbol_summary"
    ]
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        repair_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM wiki_repair_actions
            WHERE action_type = 'refresh_requested_symbol_summary'
            """
        ).fetchone()[0]
    assert repair_count == 0


def test_selector_treats_requested_symbol_placeholder_without_sources_as_missing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="000001",
        title="근거없는 종목000001",
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
        body="source refs 없이 만들어진 placeholder memory",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["000001"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert result.budget_report["requested_symbol_missing_summary_symbols"] == [
        "000001"
    ]
    assert result.budget_report["requested_symbol_summary_coverage_status"] == "none"
    priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:coverage:kis:000001"
    )
    assert priority["priority_type"] == "requested_symbol_coverage"
    assert priority["quality_warnings"] == ["requested_symbol_summary_missing"]


def test_selector_keeps_missing_summary_repair_when_other_symbol_repair_is_open(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["000002"],
        content_sections={
            "Current Stance": "open_action_count=1",
            "Durable Facts": "- open_action_count=1",
            "Evidence Links": "- wiki_repair_queue:repair:financials:kis:000002",
            "Trading History": "- source repair queue",
            "Lessons": "- repair financials before size increase",
            "Contradictions": "- requested symbol summary is still missing",
            "Open Questions": "- which repair blocks sizing?",
            "Next Context Pack Summary": "repair queue open=1",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:financials:kis:000002",
                "action_type": "refresh_symbol_financials",
                "status": "scheduled",
                "symbols": ["000002"],
                "quality_warnings": ["financials_missing"],
                "repair_action": "collect or cross-check financial statements",
                "impacted_page_ids": ["kis.symbol.000002"],
                "impacted_symbols": ["000002"],
                "repair_targets": [
                    {
                        "page_id": "kis.symbol.000002",
                        "symbol": "000002",
                        "recommended_action": (
                            "refresh_symbol_financials_and_rewrite_page_evidence"
                        ),
                    }
                ],
            }
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["000002"],
            max_pages=3,
            max_chars=20_000,
        )
    )

    assert result.budget_report["requested_symbol_missing_summary_symbols"] == [
        "000002"
    ]
    assert any(
        row.get("source_id") == "repair:financials:kis:000002"
        for row in result.repair_priorities
    )
    coverage_priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:coverage:kis:000002"
    )
    assert coverage_priority["priority_type"] == "requested_symbol_coverage"
    assert coverage_priority["quality_warnings"] == ["requested_symbol_summary_missing"]
    assert coverage_priority["repair_action"] == (
        "collect_or_rebuild_requested_symbol_wiki_summary"
    )


def test_selector_keeps_repair_queue_visible_when_many_coverage_repairs_exist(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbols = [f"00000{idx}" for idx in range(1, 10)]
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["000009"],
        content_sections={
            "Current Stance": "open_action_count=1",
            "Durable Facts": "- open_action_count=1",
            "Evidence Links": "- wiki_repair_queue:repair:financials:kis:000009",
            "Trading History": "- source repair queue",
            "Lessons": "- repair financials before size increase",
            "Contradictions": "- coverage repairs must not hide repair queue",
            "Open Questions": "- which repair blocks sizing?",
            "Next Context Pack Summary": "repair queue open=1",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:financials:kis:000009",
                "action_type": "refresh_symbol_financials",
                "status": "scheduled",
                "symbols": ["000009"],
                "quality_warnings": ["financials_missing"],
                "repair_action": "collect or cross-check financial statements",
                "impacted_page_ids": ["kis.symbol.000009"],
                "impacted_symbols": ["000009"],
                "repair_targets": [
                    {
                        "page_id": "kis.symbol.000009",
                        "symbol": "000009",
                        "recommended_action": (
                            "refresh_symbol_financials_and_rewrite_page_evidence"
                        ),
                    }
                ],
            }
        ],
        confidence=0.84,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=symbols,
            max_pages=3,
            max_chars=20_000,
        )
    )
    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )

    assert result.budget_report["requested_symbol_missing_summary_count"] == 9
    assert len(result.repair_priorities) == 8
    assert result.budget_report["repair_priority_count"] == 8
    assert result.budget_report["repair_priority_total_count"] == 10
    assert result.budget_report["repair_priority_selected_count"] == 8
    assert result.budget_report["repair_priority_omitted_count"] == 2
    assert result.budget_report["repair_priority_type_counts"] == {
        "repair_queue": 1,
        "requested_symbol_coverage": 9,
    }
    assert result.budget_report["repair_priority_selected_type_counts"] == {
        "repair_queue": 1,
        "requested_symbol_coverage": 7,
    }
    assert result.budget_report["repair_priority_omitted_type_counts"] == {
        "requested_symbol_coverage": 2,
    }
    assert any(
        row.get("source_id") == "repair:coverage:kis:000001"
        for row in result.repair_priorities
    )
    assert any(
        row.get("source_id") == "repair:financials:kis:000009"
        for row in result.repair_priorities
    )
    assert any(
        row.get("source_id") == "repair:financials:kis:000009"
        for row in contract["top_priorities"]
    )


def test_selector_excludes_requested_symbol_placeholder_from_summaries(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="000001",
        title="근거없는 종목000001",
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
        body="source refs 없이 만들어진 placeholder memory",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["000001"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    assert result.requested_symbol_summaries == []
    assert result.budget_report["requested_symbol_summary_count"] == 0
    assert result.budget_report["requested_symbol_missing_summary_symbols"] == [
        "000001"
    ]


def test_selector_records_requested_symbol_coverage_gap_as_repair_action(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbols = [f"00000{idx}" for idx in range(1, 10)]
    for symbol in symbols[:-1]:
        _write_page(
            service,
            scope="kis",
            symbol=symbol,
            title=f"종목{symbol}",
            source_refs=[
                {"source_type": "naver_reports", "source_id": f"{symbol}:report"}
            ],
            confidence=0.8,
            freshness="fresh",
            body=f"{symbol} summary memory",
        )

    request = JueWikiSelectionRequest(
        target_scope="kis",
        symbols=symbols,
        max_pages=1,
        max_chars=20_000,
    )
    JueWikiSelector(service).select(request)
    latest_result = JueWikiSelector(service).select(request)

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_id, finding_id, page_id, action_type, status, details_json
            FROM wiki_repair_actions
            WHERE action_type = 'refresh_requested_symbol_summary'
            """
        ).fetchall()

    assert len(rows) == 1
    action_id, finding_id, page_id, action_type, status, details_json = rows[0]
    details = json.loads(details_json)
    assert action_id == "repair:coverage:kis:000009"
    assert finding_id == "requested_symbol_coverage:kis:000009"
    assert page_id == "kis.symbol.000009"
    assert action_type == "refresh_requested_symbol_summary"
    assert status == "scheduled"
    assert details["decision_scope"] == "kis"
    assert details["symbols"] == ["000009"]
    assert details["selection_run_id"] == latest_result.selection_run_id
    assert details["coverage_status"] == "partial"
    assert details["repair_targets"] == [
        {
            "page_id": "kis.symbol.000009",
            "symbol": "000009",
            "recommended_action": (
                "collect_or_rebuild_requested_symbol_wiki_summary"
            ),
        }
    ]

    service.rebuild(scope="kis", force=True)
    queue = service.read_page("kis.research.repair_queue")
    sources = service.page_sources("kis.research.repair_queue")
    assert queue["status"] == "ok"
    assert "000009" in queue["content"]
    assert "refresh_requested_symbol_summary" in queue["content"]
    assert any(
        row["source_id"] == "repair:coverage:kis:000009"
        and row["symbols"] == ["000009"]
        for row in sources["source_refs"]
    )


def test_requested_symbol_coverage_repair_requires_evidence_backed_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbols = ["000001", "000002"]
    _write_page(
        service,
        scope="kis",
        symbol="000001",
        title="종목000001",
        source_refs=[{"source_type": "naver_reports", "source_id": "000001:report"}],
        confidence=0.8,
        freshness="fresh",
        body="000001 summary memory",
    )
    request = JueWikiSelectionRequest(
        target_scope="kis",
        symbols=symbols,
        max_pages=1,
        max_chars=20_000,
    )
    JueWikiSelector(service).select(request)

    _write_page(
        service,
        scope="kis",
        symbol="000002",
        title="종목000002",
        source_refs=[],
        confidence=0.4,
        freshness="fresh",
        body="placeholder without evidence",
    )
    service.rebuild(scope="kis", force=True)
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        status_after_placeholder = conn.execute(
            """
            SELECT status
            FROM wiki_repair_actions
            WHERE action_id = 'repair:coverage:kis:000002'
            """
        ).fetchone()[0]
    assert status_after_placeholder == "scheduled"

    _write_page(
        service,
        scope="kis",
        symbol="000002",
        title="종목000002",
        source_refs=[{"source_type": "naver_reports", "source_id": "000002:report"}],
        confidence=0.8,
        freshness="fresh",
        body="evidence-backed symbol summary",
    )
    service.rebuild(scope="kis", force=True)
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        status, details_json = conn.execute(
            """
            SELECT status, details_json
            FROM wiki_repair_actions
            WHERE action_id = 'repair:coverage:kis:000002'
            """
        ).fetchone()
    details = json.loads(details_json)
    assert status == "resolved"
    assert details["resolved_by"] == "repair_targets_cleaned"
    assert details["resolved_target_page_ids"] == ["kis.symbol.000002"]


def test_selector_promotes_requested_symbol_coverage_gap_to_specific_repair_use(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="000001",
        title="종목000001",
        source_refs=[{"source_type": "naver_reports", "source_id": "000001:report"}],
        confidence=0.8,
        freshness="fresh",
        body="000001 summary memory",
    )
    request = JueWikiSelectionRequest(
        target_scope="kis",
        symbols=["000001", "000002"],
        max_pages=3,
        max_chars=20_000,
    )
    JueWikiSelector(service).select(request)
    service.rebuild(scope="kis", force=True)

    result = JueWikiSelector(service).select(request)

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:coverage:kis:000002"
    )
    assert priority["priority_type"] == "repair_queue"
    assert priority["action_type"] == "refresh_requested_symbol_summary"
    assert priority["symbols"] == ["000002"]
    assert priority["quality_warnings"] == ["requested_symbol_summary_missing"]
    assert priority["decision_use"] == "requested_symbol_summary_repair"
    assert priority["repair_action"] == (
        "collect_or_rebuild_requested_symbol_wiki_summary"
    )
    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )
    top = next(
        row
        for row in contract["top_priorities"]
        if row.get("source_id") == "repair:coverage:kis:000002"
    )
    assert top["decision_use"] == "requested_symbol_summary_repair"


def test_selector_surfaces_requested_symbol_coverage_gap_without_rebuild(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="000001",
        title="종목000001",
        source_refs=[{"source_type": "naver_reports", "source_id": "000001:report"}],
        confidence=0.8,
        freshness="fresh",
        body="000001 summary memory",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["000001", "000002"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("source_id") == "repair:coverage:kis:000002"
    )
    assert priority["priority_type"] == "requested_symbol_coverage"
    assert priority["source_type"] == "selection_budget_report"
    assert priority["page_id"] == "kis.symbol.000002"
    assert priority["symbols"] == ["000002"]
    assert priority["quality_warnings"] == ["requested_symbol_summary_missing"]
    assert priority["decision_use"] == "requested_symbol_summary_repair"
    assert priority["candidate_resolution_required"] is True
    assert priority["repair_targets"] == [
        {
            "page_id": "kis.symbol.000002",
            "symbol": "000002",
            "recommended_action": (
                "collect_or_rebuild_requested_symbol_wiki_summary"
            ),
        }
    ]


def test_selector_prioritizes_opportunity_pipeline_before_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbol_page = _write_page(
        service,
        scope="kis",
        symbol="123450",
        title="숨은후보",
        source_refs=[
            {"source_type": "naver_reports", "source_id": "123450"},
            {"source_type": "daily_discovery", "source_id": "123450"},
        ],
        confidence=1.0,
        freshness="fresh",
        body="single symbol opportunity memory",
    )
    pipeline_page = service.write_page(
        scope="kis",
        page_type="ops",
        key="opportunity_pipeline",
        title="KIS Opportunity Pipeline",
        symbols=[],
        content_sections={
            "Current Stance": "pre-surge backlog must become blocks or rejects.",
            "Durable Facts": (
                "candidate_backlog_count=12\nmissed_upside_count=3\n"
                "creative_hypothesis_count=4"
            ),
            "Evidence Links": "kis_manager_runs:88",
            "Trading History": "daily discovery backlog and missed upside reviews",
            "Lessons": "turn evidence into waiting/probe block designs.",
            "Contradictions": "research finds candidates but blocks are not created",
            "Open Questions": "which candidate deserves a waiting trigger?",
            "Next Context Pack Summary": "opportunity backlog requires resolution.",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "88"}],
        confidence=0.76,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["123450"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    assert selected_ids == [pipeline_page]
    assert symbol_page not in selected_ids
    assert "operational_memory:opportunity_pipeline" in result.pages[0].reasons


def test_selector_prioritizes_research_coverage_before_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    symbol_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "삼성전자 symbol research memory",
            "Durable Facts": "HBM valuation reports whale sesiban",
            "Evidence Links": "naver report",
            "Trading History": "symbol outcomes",
            "Lessons": "symbol-specific note",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "삼성전자 summary",
        },
        source_refs=[
            {"source_type": "naver_reports", "source_id": "005930"},
            {"source_type": "kis_blocks", "source_id": "blk_005930"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]
    research_page = service.write_page(
        scope="kis",
        page_type="research",
        key="coverage",
        title="KIS Research Coverage",
        symbols=[],
        content_sections={
            "Current Stance": "KIS research coverage is broad but has gaps.",
            "Durable Facts": "ok_sources=4/5; total_primary_rows=40000",
            "Evidence Links": "research_coverage:naver_reports",
            "Trading History": "pre-trade evidence memory",
            "Lessons": "missing research becomes follow-up checks.",
            "Contradictions": "research breadth is not execution quality.",
            "Open Questions": "which source finds moves early?",
            "Next Context Pack Summary": "KIS research coverage guides breadth.",
            "Coverage Matrix": "naver_reports rows=5000",
            "Freshness": "latest_at=2026-07-02T00:00:00+00:00",
            "Data Gaps": "daily_discovery missing",
            "Actionability": "expand candidates from reports and ETFs",
        },
        source_refs=[
            {"source_type": "research_coverage", "source_id": "naver_reports"},
            {"source_type": "research_coverage", "source_id": "etf_research"},
        ],
        confidence=0.8,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_pages=1,
            max_chars=20_000,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    assert selected_ids == [research_page]
    assert symbol_page not in selected_ids
    assert "operational_memory:research_coverage" in result.pages[0].reasons


def test_selector_gives_trading_validation_structural_priority_even_when_score_is_lower(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_block_id = "blk_hot_symbol"
    symbol_page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "hot symbol memory",
            "Durable Facts": "short mid long core_etf waiting_entry risk_on kospi",
            "Evidence Links": "block evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "삼성전자 selector summary",
        },
        source_refs=[
            {"source_type": "kis_blocks", "source_id": active_block_id},
            {"source_type": "naver_reports", "source_id": "005930"},
            {"source_type": "daily_discovery", "source_id": "005930"},
            {"source_type": "market_pulse", "source_id": "latest"},
            {"source_type": "investment_memory", "source_id": "005930"},
            {"source_type": "kis_manager_runs", "source_id": "1"},
        ],
        confidence=1.0,
        freshness="fresh",
    )["page_id"]
    risk_page = service.write_page(
        scope="kis",
        page_type="risk",
        key="trading_validation",
        title="KIS Trading Validation Risk",
        symbols=[],
        content_sections={
            "Current Stance": "minimal validation memory",
            "Durable Facts": "readiness=probe",
            "Evidence Links": "trading_validation:kis-latest",
            "Trading History": "sample_count=5",
            "Lessons": "probe continues",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "Risk page survives structurally.",
        },
        source_refs=[],
        confidence=0.15,
        freshness="fresh",
    )["page_id"]

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            lanes=["short", "mid", "long", "core_etf", "waiting_entry"],
            regimes=["risk_on", "kospi"],
            block_ids=[active_block_id],
            max_pages=2,
            max_chars=20_000,
        )
    )

    selected_ids = [page.page_id for page in result.pages]
    selected_scores = {page.page_id: page.score for page in result.pages}
    assert selected_scores[symbol_page] > selected_scores[risk_page]
    assert selected_ids[0] == risk_page


def test_selector_keeps_manager_ops_page_before_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="binance",
        page_type="ops",
        key="manager_runs",
        title="Binance Manager Run Operations",
        symbols=[],
        content_sections={
            "Current Stance": "manager errors are repair memory",
            "Durable Facts": "recent_error_count=4",
            "Evidence Links": "- binance_manager_runs:2370",
            "Trading History": "prompt_budget_exceeded and native sdk timeout",
            "Lessons": "compact context and keep action pressure alive",
            "Contradictions": "action pressure high but zero actions",
            "Open Questions": "which prompt section grew fastest?",
            "Next Context Pack Summary": "ops repair memory",
        },
        source_refs=[{"source_type": "binance_manager_runs", "source_id": "2370"}],
        confidence=0.4,
        freshness="fresh",
    )
    for idx in range(3):
        _write_page(
            service,
            scope="binance",
            symbol=f"T{idx}USDT",
            title=f"T{idx}USDT",
            source_refs=[{"source_type": "crypto_research", "source_id": f"r{idx}"}],
            confidence=0.95,
            freshness="fresh",
        )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["T0USDT", "T1USDT", "T2USDT"],
            page_types=["symbol"],
            max_pages=2,
            max_chars=12000,
        )
    )

    assert result.pages[0].page_id == "binance.ops.manager_runs"
    assert [page.page_id for page in result.pages].count(
        "binance.ops.manager_runs"
    ) == 1


def test_selector_prioritizes_manager_contract_recovery_ops_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="risk",
        key="trading_validation",
        title="KIS Trading Validation Risk",
        symbols=[],
        content_sections={
            "Current Stance": "risk memory stays first",
            "Durable Facts": "risk check",
            "Evidence Links": "validation:kis",
            "Trading History": "recent validation events",
            "Lessons": "respect safety gates",
            "Contradictions": "none",
            "Open Questions": "none",
            "Next Context Pack Summary": "validation summary",
        },
        source_refs=[],
        confidence=0.6,
        freshness="fresh",
    )
    service.write_page(
        scope="kis",
        page_type="ops",
        key="action_pressure",
        title="KIS Action Pressure",
        symbols=[],
        content_sections={
            "Current Stance": "ordinary action pressure",
            "Durable Facts": "pressure=normal",
            "Evidence Links": "action:kis",
            "Trading History": "no storage recovery marker",
            "Lessons": "normal ops memory",
            "Contradictions": "none",
            "Open Questions": "none",
            "Next Context Pack Summary": "action pressure summary",
        },
        source_refs=[{"source_type": "kis_action_pressure", "source_id": "latest"}],
        confidence=0.8,
        freshness="fresh",
    )
    service.write_page(
        scope="kis",
        page_type="ops",
        key="manager_runs",
        title="KIS Manager Run Operations",
        symbols=[],
        content_sections={
            "Current Stance": "manager contract recovery must shape the next run",
            "Durable Facts": "storage_emergency=True",
            "Evidence Links": "- kis_manager_runs:120",
            "Trading History": (
                "run=120 storage_emergency=True "
                "priority=manager_contract_recovery "
                "dropped=research_spine,output_schema"
            ),
            "Lessons": "preserve recovered contract memory before action pressure",
            "Contradictions": "none",
            "Open Questions": "which dropped section should be restored next?",
            "Next Context Pack Summary": "manager contract recovery memory",
        },
        source_refs=[{"source_type": "kis_manager_runs", "source_id": "120"}],
        confidence=0.35,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            page_types=["symbol"],
            max_pages=2,
            max_chars=20_000,
        )
    )

    assert [page.page_id for page in result.pages] == [
        "kis.risk.trading_validation",
        "kis.ops.manager_runs",
    ]
    assert "operational_memory:manager_contract_recovery" in result.pages[1].reasons


def test_selector_prioritizes_requested_block_source_refs(tmp_path: Path) -> None:
    service = _service(tmp_path)
    stale_block_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 old block memory",
        source_refs=[{"source_type": "kis_blocks", "source_id": "old-blk"}],
        confidence=0.9,
        freshness="fresh",
    )
    active_block_id = _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="SK하이닉스 active block memory",
        source_refs=[
            {"source_type": "kis_blocks", "source_id": "active-blk"},
            {"source_type": "investment_memory", "source_id": "active-blk"},
        ],
        confidence=0.55,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930", "000660"],
            block_ids=["active-blk"],
            max_chars=20_000,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert result.pages[0].page_id == active_block_id
    assert selected[active_block_id].score > selected[stale_block_id].score
    assert "block_ref_overlap:active-blk" in selected[active_block_id].reasons


def test_selector_prioritizes_requested_nested_block_source_refs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    stale_block_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 old block memory",
        source_refs=[{"source_type": "kis_blocks", "source_id": "old-blk"}],
        confidence=0.9,
        freshness="fresh",
    )
    active_block_id = _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="SK하이닉스 compressed active block memory",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.000660:summary",
                "source_refs": [
                    {"source_type": "kis_blocks", "source_id": "active-blk"}
                ],
            }
        ],
        confidence=0.55,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930", "000660"],
            block_ids=["active-blk"],
            max_chars=20_000,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert result.pages[0].page_id == active_block_id
    assert selected[active_block_id].score > selected[stale_block_id].score
    assert "block_ref_overlap:active-blk" in selected[active_block_id].reasons


def test_selector_selected_page_source_refs_are_flattened(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="SK하이닉스 compressed evidence memory",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.000660:summary",
                "source_refs": [
                    {
                        "source_type": "kis_blocks",
                        "source_id": "active-blk",
                    },
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "000660:fundamentals",
                    },
                ],
            }
        ],
        confidence=0.9,
        freshness="fresh",
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["000660"],
            block_ids=["active-blk"],
            max_chars=20_000,
        )
    )

    assert [
        (ref["source_type"], ref["source_id"])
        for ref in result.pages[0].source_refs
    ] == [
        ("wiki_symbol_summary", "kis.symbol.000660:summary"),
        ("kis_blocks", "active-blk"),
        ("symbol_fundamentals", "000660:fundamentals"),
    ]


def test_selector_can_exclude_pages_with_open_lint_findings(tmp_path: Path) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[],
    )
    lint_result = service.lint(scope="kis")
    assert lint_result["status"] == "warn"

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            exclude_lint_warnings=True,
        )
    )

    assert result.pages == []
    assert result.rejected_pages == [
        {
            "page_id": page_id,
            "reason": "open_lint_warning",
            "char_count": len(service.read_page(page_id)["content"]),
        }
    ]
    assert result.budget_report["status"] == "empty"


def test_selector_promotes_open_lint_findings_to_repair_priorities(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 weak source identity",
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "quality_status": "weak",
                        "quality_warnings": ["financials_missing"],
                    }
                ],
            }
        ],
    )
    lint_result = service.lint(scope="kis")
    assert lint_result["status"] == "warn"

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=20_000,
        )
    )

    priority = next(
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "lint"
    )
    assert priority["page_id"] == page_id
    assert priority["source_type"] == "wiki_lint_findings"
    assert priority["finding_type"] == "source_ref_identity_gap"
    assert priority["symbols"] == ["005930"]
    assert priority["symbol_overlap"] == ["005930"]
    assert priority["decision_use"] == "wiki_lint_repair"
    assert priority["action_type"] == "repair_source_ref_identity_gap"
    assert priority["repair_action"] == (
        "repair wiki source reference identity gaps before reusing this memory"
    )
    assert "lint_finding:source_ref_identity_gap" in priority["reasons"]


def test_selector_rejects_all_eligible_pages_when_max_pages_is_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk1"}],
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_pages=0,
        )
    )

    assert result.pages == []
    assert result.rejected_pages == [
        {
            "page_id": page_id,
            "reason": "max_pages_exceeded",
            "char_count": len(service.read_page(page_id)["content"]),
        }
    ]
    assert result.budget_report["status"] == "empty"

    with sqlite3.connect(service.config.db_path) as conn:
        page_row = conn.execute(
            """
            SELECT page_id, included, rank
            FROM wiki_selection_pages
            WHERE run_id = ?
            """,
            (result.selection_run_id,),
        ).fetchone()

    assert page_row == (page_id, 0, 0)


def test_selector_applies_bounded_effectiveness_adjustment(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base_id = _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="SK하이닉스",
        source_refs=[{"source_type": "test", "source_id": "base"}],
        confidence=0.5,
        freshness="fresh",
    )
    effective_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 효과성",
        source_refs=[{"source_type": "test", "source_id": "effect"}],
        confidence=0.5,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": effective_id,
            "decision_scope": "kis",
            "sample_count": 12,
            "win_rate": 0.75,
            "expectancy": 1.2,
            "avg_return_pct": 1.2,
            "median_mae_pct": -0.2,
            "drawdown_pressure": 0.2,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["effective"],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930", "000660"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert result.budget_report["effectiveness_status_counts"]["active"] == 1
    assert "degraded pages as repair evidence" in result.effectiveness_policy[
        "degraded"
    ]
    assert "effectiveness:active" in selected[effective_id].reasons
    assert "effectiveness_status:active" in selected[effective_id].reasons
    assert selected[effective_id].effectiveness["status"] == "active"
    assert selected[effective_id].effectiveness["sample_count"] == 12
    assert selected[effective_id].effectiveness["reasons"] == ["effective"]
    assert selected[effective_id].score > selected[base_id].score


def test_selector_uses_requested_horizon_effectiveness_before_aggregate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = service.write_page(
        scope="kis",
        page_type="market",
        key="regime-session",
        title="KIS regular regime memory",
        symbols=[],
        content_sections={
            "Current Stance": "regular session tested regime memory",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "regular session summary",
        },
        source_refs=[{"source_type": "market_judgment", "source_id": "regular"}],
        confidence=0.7,
        freshness="fresh",
    )["page_id"]
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "market_session:regular",
            "sample_count": 4,
            "win_rate": 1.0,
            "expectancy": 0.0,
            "avg_return_pct": 0.0,
            "helpful_score": 7.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["regular-session-worked"],
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "market_session:closing_watch",
            "sample_count": 4,
            "win_rate": 0.0,
            "expectancy": -0.12,
            "avg_return_pct": -0.12,
            "helpful_score": -7.0,
            "confidence": 1.0,
            "status": "degraded",
            "reasons": ["closing-watch-failed"],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            page_types=["market"],
            horizons=["market_session:regular"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    assert result.pages[0].page_id == page_id
    assert result.pages[0].effectiveness["horizon"] == "market_session:regular"
    assert result.pages[0].effectiveness["status"] == "active"
    assert result.pages[0].effectiveness["reasons"] == ["regular-session-worked"]
    assert "effectiveness:active" in result.pages[0].reasons
    assert "degraded_repair_only" not in result.pages[0].penalties


def test_selector_prunes_stale_misattributed_effectiveness_before_scoring(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "test", "source_id": "symbol-page"}],
        confidence=0.8,
        freshness="fresh",
    )
    application = JueWikiApplicationService(service)
    link = application.record_decision_link(
        selection_run_id="selection:selector-stale",
        manager_run_id="kis-manager-selector-stale",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=[page_id],
        symbol="000660",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": page_id,
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    }
                ]
            }
        },
    )
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "selector-stale-misattributed-outcome",
                link["link_id"],
                "selection:selector-stale",
                page_id,
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                (
                    '{"symbol":"000660","selected_wiki_page":'
                    f'{{"page_id":"{page_id}","page_type":"symbol",'
                    '"symbols":["005930"]}}}'
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "win_rate": 1.0,
            "expectancy": 4.0,
            "avg_return_pct": 4.0,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["legacy polluted"],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    assert result.pages[0].page_id == page_id
    assert result.pages[0].effectiveness == {}
    assert "effectiveness:active" not in result.pages[0].reasons
    assert "active" not in result.budget_report["effectiveness_status_counts"]
    assert service.page_effectiveness_map(decision_scope="kis") == {}


def test_selector_penalizes_pages_with_degraded_quality_warning_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    degraded_warning_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 sparse fundamentals",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:weak",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )
    clean_id = _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="SK하이닉스 clean fundamentals",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "000660:clean",
                "quality_status": "ok",
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 9,
            "win_rate": 0.11,
            "expectancy": -1.4,
            "avg_return_pct": -1.4,
            "median_mae_pct": -1.8,
            "drawdown_pressure": 2.6,
            "helpful_score": -8.0,
            "confidence": 0.9,
            "status": "degraded",
            "reasons": [
                "samples:9",
                "win_rate:0.1100",
                "expectancy:-1.4000",
                "median_mae:-1.8000",
                "quality_warning:financials_missing",
                "quality_warning_prior_status:degraded",
            ],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930", "000660"],
            page_types=["symbol"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=6.0,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert "quality_warning_effectiveness:financials_missing:degraded" in selected[
        degraded_warning_id
    ].penalties
    assert selected[degraded_warning_id].score < selected[clean_id].score


def test_selector_exposes_quality_warning_effectiveness_in_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 sparse fundamentals",
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:weak",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 9,
            "win_rate": 0.11,
            "expectancy": -1.4,
            "avg_return_pct": -1.4,
            "median_mae_pct": -1.8,
            "drawdown_pressure": 2.6,
            "helpful_score": -8.0,
            "confidence": 0.9,
            "status": "degraded",
            "reasons": [
                "samples:9",
                "win_rate:0.1100",
                "expectancy:-1.4000",
                "median_mae:-1.8000",
                "quality_warning:financials_missing",
                "quality_warning_prior_status:degraded",
            ],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=6.0,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert selected[page_id].evidence_quality["warning_effectiveness"] == [
        {
            "warning": "financials_missing",
            "page_id": "quality_warning.financials_missing",
            "status": "degraded",
            "sample_count": 9,
            "win_rate": 0.11,
            "expectancy": -1.4,
            "helpful_score": -8.0,
            "confidence": 0.9,
            "reasons": [
                "samples:9",
                "win_rate:0.1100",
                "expectancy:-1.4000",
                "median_mae:-1.8000",
                "quality_warning:financials_missing",
                "quality_warning_prior_status:degraded",
            ],
        }
    ]


def test_quality_warning_metrics_omit_missing_values_but_keep_explicit_zero() -> None:
    missing = JueWikiSelector._compact_quality_warning_metrics(
        [
            {
                "warning": "financials_missing",
                "page_id": "quality_warning.financials_missing",
                "status": "probe",
            }
        ]
    )

    assert missing == [
        {
            "warning": "financials_missing",
            "page_id": "quality_warning.financials_missing",
            "status": "probe",
        }
    ]

    explicit_zero = JueWikiSelector._compact_quality_warning_metrics(
        [
            {
                "warning": "financials_missing",
                "page_id": "quality_warning.financials_missing",
                "status": "probe",
                "sample_count": 0,
                "win_rate": 0.0,
                "expectancy": 0,
                "helpful_score": 0.0,
                "confidence": 0,
            }
        ]
    )

    assert explicit_zero == [
        {
            "warning": "financials_missing",
            "page_id": "quality_warning.financials_missing",
            "status": "probe",
            "sample_count": 0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "helpful_score": 0.0,
            "confidence": 0.0,
        }
    ]


def test_selector_promotes_degraded_usage_guidance_effectiveness_to_repair_priority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 weak guidance memory",
        source_refs=[{"source_type": "test", "source_id": "guidance-memory"}],
        confidence=0.8,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "sample_count": 6,
            "win_rate": 0.5,
            "expectancy": 0.2,
            "helpful_score": 2.0,
            "status": "active",
            "confidence": 0.7,
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "usage_guidance.risk_posture.repair_cross_check",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 8,
            "win_rate": 0.125,
            "expectancy": -1.1,
            "avg_return_pct": -1.1,
            "median_mae_pct": -1.7,
            "drawdown_pressure": 2.4,
            "helpful_score": -7.5,
            "confidence": 0.85,
            "status": "degraded",
            "reasons": [
                "samples:8",
                "win_rate:0.1250",
                "expectancy:-1.1000",
                "usage_guidance:risk_posture:repair_cross_check",
            ],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    priorities = [
        row
        for row in result.repair_priorities
        if row.get("priority_type") == "usage_guidance_effectiveness"
    ]
    assert len(priorities) == 1
    priority = priorities[0]
    assert priority["page_id"] == "usage_guidance.risk_posture.repair_cross_check"
    assert priority["page_type"] == "policy"
    assert priority["source_type"] == "jue_wiki_usage_guidance_metric"
    assert priority["action_type"] == "repair_usage_guidance_contract"
    assert priority["decision_use"] == "usage_guidance_effectiveness_repair"
    assert priority["candidate_resolution_required"] is True
    assert priority["hard_blocker"] is False
    assert priority["quality_status"] == "weak"
    assert priority["quality_warnings"] == ["usage_guidance_degraded"]
    assert "usage_guidance_effectiveness:degraded" in priority["reasons"]

    contract = build_jue_wiki_repair_contract_for_prompt(
        {"repair_priorities": result.repair_priorities}
    )

    contract_priority = next(
        row
        for row in contract["top_priorities"]
        if row["priority_type"] == "usage_guidance_effectiveness"
    )
    assert contract_priority["decision_use"] == "usage_guidance_effectiveness_repair"
    assert contract_priority["quality_status"] == "weak"
    assert contract_priority["candidate_resolution_required"] is True
    assert contract_priority["hard_blocker"] is False


def test_selector_reads_page_level_quality_warnings_once() -> None:
    warnings = JueWikiSelector._page_quality_warnings(
        {
            "quality_warnings": [
                "financials_missing",
                "valuation_metrics_sparse",
            ],
            "source_refs": [
                {
                    "quality_warnings": [
                        "financials_missing",
                        "price_missing",
                    ]
                }
            ],
        }
    )

    assert warnings == [
        "financials_missing",
        "valuation_metrics_sparse",
        "price_missing",
    ]


def test_selector_reads_compacted_evidence_quality_warnings_once() -> None:
    warnings = JueWikiSelector._page_quality_warnings(
        {
            "evidence_quality": {
                "top_warnings": [
                    {"warning": "financials_missing", "count": 3},
                    {"warning": "valuation_metrics_sparse", "count": 2},
                ]
            },
            "source_refs": [
                {
                    "evidence_quality": {
                        "top_warnings": [
                            {"warning": "financials_missing", "count": 2},
                            {"warning": "price_missing", "count": 1},
                        ]
                    }
                }
            ],
        }
    )

    assert warnings == [
        "financials_missing",
        "valuation_metrics_sparse",
        "price_missing",
    ]


def test_selector_scores_requested_lane_and_regime_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    generic_id = _write_page(
        service,
        scope="kis",
        symbol="000660",
        title="Generic KIS note",
        source_refs=[{"source_type": "test", "source_id": "generic"}],
        confidence=0.8,
        freshness="fresh",
        body="plain symbol memory",
    )
    lane_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="Value cycle playbook",
        source_refs=[{"source_type": "test", "source_id": "lane"}],
        confidence=0.8,
        freshness="fresh",
        body=(
            "value_cycle lane waits for pullback accumulation under "
            "semiconductor regime pressure."
        ),
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930", "000660"],
            lanes=["value_cycle"],
            regimes=["semiconductor"],
            max_chars=10_000,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert "lane_overlap:value_cycle" in selected[lane_id].reasons
    assert "regime_overlap:semiconductor" in selected[lane_id].reasons
    assert selected[lane_id].score > selected[generic_id].score


def test_selector_caps_negative_effectiveness_adjustment(tmp_path: Path) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자",
        source_refs=[{"source_type": "test", "source_id": "base"}],
        confidence=0.8,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "helpful_score": -100.0,
            "confidence": 1.0,
            "status": "degraded",
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    assert result.pages
    assert "effectiveness_adjustment:-8.0000" in result.pages[0].reasons
    assert "effectiveness_status:degraded" in result.pages[0].penalties
    assert result.pages[0].effectiveness["status"] == "degraded"
    assert result.budget_report["effectiveness_status_counts"]["degraded"] == 1
    assert result.budget_report["repair_priority_count"] == 1
    assert result.repair_priorities[0]["page_id"] == page_id
    assert result.repair_priorities[0]["repair_action"] in {
        "treat as repair evidence, not as a no-action blocker",
        "revise entry/exit design before reusing this memory",
    }


def test_selector_surfaces_degraded_repair_priority_even_when_not_selected(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_id = _write_page(
        service,
        scope="binance",
        symbol="BTCUSDT",
        title="BTC active",
        source_refs=[{"source_type": "test", "source_id": "active"}],
        confidence=0.9,
        freshness="fresh",
    )
    degraded_id = _write_page(
        service,
        scope="binance",
        symbol="ETHUSDT",
        title="ETH degraded",
        source_refs=[{"source_type": "test", "source_id": "degraded"}],
        confidence=0.9,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": active_id,
            "decision_scope": "binance",
            "sample_count": 9,
            "helpful_score": 7.0,
            "status": "active",
            "confidence": 1.0,
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": degraded_id,
            "decision_scope": "binance",
            "sample_count": 8,
            "win_rate": 0.25,
            "expectancy": -0.7,
            "drawdown_pressure": 1.4,
            "helpful_score": -8.0,
            "status": "degraded",
            "confidence": 1.0,
            "reasons": [
                "samples:8",
                "win_rate:0.2500",
                "expectancy:-0.7000",
                "median_mae:0.0000",
                "failed breakout reuse",
                "repair_target_prior_status:degraded",
            ],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["BTCUSDT"],
            max_chars=4_000,
            max_pages=1,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    assert [page.page_id for page in result.pages] == [active_id]
    assert result.repair_priorities[0]["page_id"] == degraded_id
    assert result.repair_priorities[0]["reasons"] == [
        "samples:8",
        "win_rate:0.2500",
        "expectancy:-0.7000",
        "median_mae:0.0000",
        "failed breakout reuse",
        "repair_target_prior_status:degraded",
    ]
    assert result.repair_priorities[0]["repair_action"] == (
        "revise entry/exit design before reusing this memory"
    )


def test_selector_keeps_generic_degraded_playbook_out_of_prompt_body(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_id = _write_page(
        service,
        scope="binance",
        symbol="BTCUSDT",
        title="BTC active",
        source_refs=[{"source_type": "test", "source_id": "active"}],
        confidence=0.9,
        freshness="fresh",
    )
    degraded = service.write_page(
        scope="binance",
        page_type="playbook",
        key="reflection_lessons",
        title="Binance degraded reflection lessons",
        symbols=["BTCUSDT"],
        content_sections={
            "Current Stance": "old generic lessons",
            "Durable Facts": "generic",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "loss-heavy generic lesson",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "degraded generic playbook",
        },
        source_refs=[{"source_type": "test", "source_id": "degraded-playbook"}],
        confidence=0.9,
        freshness="fresh",
    )
    degraded_id = str(degraded["page_id"])
    service.upsert_page_effectiveness(
        {
            "page_id": active_id,
            "decision_scope": "binance",
            "sample_count": 9,
            "helpful_score": 7.0,
            "status": "active",
            "confidence": 1.0,
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": degraded_id,
            "decision_scope": "binance",
            "sample_count": 40,
            "win_rate": 0.2,
            "expectancy": -0.8,
            "drawdown_pressure": 1.2,
            "helpful_score": -9.0,
            "status": "degraded",
            "confidence": 1.0,
            "reasons": ["generic lessons degraded"],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="binance",
            symbols=["BTCUSDT"],
            max_chars=20_000,
            max_pages=5,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    assert active_id in [page.page_id for page in result.pages]
    assert degraded_id not in [page.page_id for page in result.pages]
    assert any(
        row["page_id"] == degraded_id and row["reason"] == "degraded_repair_only"
        for row in result.rejected_pages
    )
    assert result.repair_priorities[0]["page_id"] == degraded_id


def test_selector_aggregates_horizon_degraded_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = _write_page(
        service,
        scope="kis",
        symbol="005930",
        title="삼성전자 mixed horizon",
        source_refs=[{"source_type": "test", "source_id": "mixed"}],
        confidence=0.8,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "long",
            "sample_count": 6,
            "win_rate": 0.66,
            "expectancy": 0.8,
            "helpful_score": 8.0,
            "status": "active",
            "confidence": 1.0,
            "reasons": ["long horizon worked"],
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 7,
            "win_rate": 0.2,
            "expectancy": -0.9,
            "drawdown_pressure": 1.8,
            "helpful_score": -9.0,
            "status": "degraded",
            "confidence": 1.0,
            "reasons": ["mid horizon failed"],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    assert result.pages[0].effectiveness["status"] == "degraded"
    assert result.pages[0].effectiveness["sample_count"] == 13
    assert result.repair_priorities[0]["page_id"] == page_id
    assert "aggregated_effectiveness_rows:2" in result.repair_priorities[0][
        "reasons"
    ]
