from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from tradecraft.services.binance_lane import BINANCE_MANAGER_LANES
from tradecraft.services.jue_decision_packet import DECISION_PACKET_REQUIRED_SECTIONS
from tradecraft.services.binance_manager_prompt import (
    BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS,
    apply_manager_latency_guard,
    build_binance_manager_prompt_payload,
    compact_account_asset_rows,
    compact_futures_risk_row,
    compact_manager_candidate_for_prompt,
    compact_manager_account_for_prompt,
    compact_validation_repair_prompt,
    compact_manager_block_for_prompt,
    compact_manager_blocks_for_prompt,
    compact_manager_diagnostics_for_storage,
    compact_binance_memory_for_prompt,
    compact_manager_lane_action_for_prompt,
    compact_manager_lane_authority_for_prompt,
    compact_manager_live_authority_for_prompt,
    compact_manager_prompt_context,
    compact_manager_response_payload,
    compact_manager_sections_for_final_budget,
    compact_manager_sections_for_warn_budget,
    compact_manager_storage_payload,
    compact_prompt_section,
    compact_jue_wiki_for_prompt,
    compact_jue_wiki_memory_card_quality_prompt,
    compact_jue_wiki_repair_contract_prompt,
    compact_prompt_value_bounded,
    compact_validation_repair_for_storage,
    compact_live_authority_prompt_value,
    compact_prompt_candidate_minimal,
    compact_prompt_candidates_minimal,
    enforce_prompt_budget,
    latency_recovery_core_prompt,
    manager_latency_guard_from_runs,
    manager_response_contract_error,
    manager_run_diagnostics,
    manager_run_workflow_provenance,
    manager_run_is_timeout_error,
    merge_manager_candidate_price_plan,
    is_meaningful_futures_risk_row,
    manager_action_candidate_keys,
    manager_candidate_identity_from_payload,
    manager_prompt_original_chars_hint,
    manager_prompt_storage_emergency_payload,
    manager_storage_compaction_meta,
    normalize_lane_review,
    finalize_prompt_budget,
    prompt_budget_error,
    prompt_chars,
    prompt_chars_capped,
    prompt_section_size_rows,
    prioritize_manager_candidate_rows,
    select_manager_candidate_rows_for_compaction,
    validation_repair_action_metadata,
    validation_repair_discipline_tokens,
    validation_evidence_plan_from_repair,
    validation_repair_note,
)

ROOT = Path(__file__).resolve().parents[1]


def _wiki_gate_storage_contracts() -> dict[str, object]:
    return {
        "jue_wiki_decision_gate": {
            "allow_new_risk": False,
            "allow_exit_actions": True,
            "reason": "wiki_required_coverage_missing",
            "read_mode": "required",
            "snapshot_id": "snapshot:binance:storage",
            "version": "wiki_decision_gate_v1",
            "untrusted_noise": "x" * 20_000,
        },
        "jue_wiki_decision_gate_policy": {"instruction": "preserve reduce-only exits"},
        "jue_wiki_raw_rag_strip_audit": {
            "read_mode": "required",
            "snapshot_id": "snapshot:binance:storage",
            "removed_path_count": 200,
            "removed_paths": [f"raw_rag.items[{index}]" for index in range(200)],
        },
        "jue_wiki_suppression_audit": {
            "venue": "binance",
            "snapshot_id": "snapshot:binance:storage",
            "read_mode": "required",
            "reason": "wiki_required_coverage_missing",
            "original_action_count": 200,
            "filtered_action_count": 1,
            "suppressed_new_risk_count": 199,
            "suppressed_actions": [
                {
                    "venue": "binance",
                    "action_kind": "create_blocks",
                    "symbol": f"COIN{index}USDT",
                    "block_id": "",
                    "snapshot_id": "snapshot:binance:storage",
                    "read_mode": "required",
                    "reason": "wiki_required_coverage_missing",
                }
                for index in range(200)
            ],
        },
    }


def test_binance_storage_compaction_preserves_wiki_gate_contracts_at_every_label() -> None:
    source = {
        **_wiki_gate_storage_contracts(),
        "decision_inputs": ["jue_wiki_decision_gate", "jue_wiki_raw_rag_strip_audit"],
        "create_blocks": [
            {"symbol": f"COIN{index}USDT", "thesis": "x" * 500}
            for index in range(100)
        ],
        "noise": "x" * 80_000,
    }

    for label in (
        "binance_manager_prompt",
        "binance_manager_response",
        "binance_manager_actions",
    ):
        compact = compact_manager_storage_payload(source, limit=1_500, label=label)

        assert compact["jue_wiki_decision_gate"]["snapshot_id"] == (
            "snapshot:binance:storage"
        )
        assert compact["jue_wiki_raw_rag_strip_audit"]["removed_path_count"] == 200
        assert compact["jue_wiki_suppression_audit"][
            "suppressed_new_risk_count"
        ] == 199
        assert compact["jue_wiki_decision_gate_policy"]["instruction"] == (
            "preserve reduce-only exits"
        )
        assert "untrusted_noise" not in compact["jue_wiki_decision_gate"]


def test_binance_storage_compaction_bounds_adversarial_wiki_audit_strings() -> None:
    huge = "x" * 100_000
    source = _wiki_gate_storage_contracts()
    source["jue_wiki_decision_gate"]["reason"] = huge  # type: ignore[index]
    source["jue_wiki_decision_gate"]["snapshot_id"] = huge  # type: ignore[index]
    source["jue_wiki_raw_rag_strip_audit"]["removed_paths"] = [huge] * 100  # type: ignore[index]
    source["jue_wiki_suppression_audit"]["reason"] = huge  # type: ignore[index]

    for label in ("binance_manager_prompt", "binance_manager_response"):
        compact = compact_manager_storage_payload(source, limit=1_500, label=label)

        documented_minimum = 10_000 if label == "binance_manager_prompt" else 1_500
        assert len(json.dumps(compact, ensure_ascii=False)) <= documented_minimum
        assert len(compact["jue_wiki_decision_gate"]["reason"]) <= 120
        assert len(compact["jue_wiki_decision_gate"]["snapshot_id"]) <= 120
        assert len(compact["jue_wiki_suppression_audit"]["reason"]) <= 120


@pytest.mark.parametrize(
    "label",
    ["binance_manager_prompt", "binance_manager_response"],
)
@pytest.mark.parametrize("with_emergency_noise", [False, True])
def test_binance_valid_gate_identity_round_trips_through_storage(
    label: str,
    with_emergency_noise: bool,
) -> None:
    reason_prefix = "wiki_required_  coverage  "
    reason = reason_prefix + ("r" * (118 - len(reason_prefix))) + "  "
    snapshot_prefix = "  snapshot  with  spaces  "
    snapshot_id = snapshot_prefix + ("s" * (118 - len(snapshot_prefix))) + "  "
    source = _wiki_gate_storage_contracts()
    source["jue_wiki_decision_gate"]["reason"] = reason  # type: ignore[index]
    source["jue_wiki_decision_gate"]["snapshot_id"] = snapshot_id  # type: ignore[index]
    source["jue_wiki_raw_rag_strip_audit"]["snapshot_id"] = snapshot_id  # type: ignore[index]
    source["jue_wiki_suppression_audit"]["reason"] = reason  # type: ignore[index]
    source["jue_wiki_suppression_audit"]["snapshot_id"] = snapshot_id  # type: ignore[index]
    if with_emergency_noise:
        source["noise"] = "x" * 100_000
    else:
        source["jue_wiki_decision_gate"].pop("untrusted_noise")  # type: ignore[union-attr]
        source["jue_wiki_raw_rag_strip_audit"]["removed_paths"] = []  # type: ignore[index]
        source["jue_wiki_suppression_audit"]["suppressed_actions"] = []  # type: ignore[index]

    compact = compact_manager_storage_payload(
        source,
        limit=1_500 if with_emergency_noise else 10_000,
        label=label,
    )

    assert compact["jue_wiki_decision_gate"]["reason"] == reason
    assert compact["jue_wiki_decision_gate"]["snapshot_id"] == snapshot_id
    assert compact["jue_wiki_raw_rag_strip_audit"]["snapshot_id"] == snapshot_id
    assert compact["jue_wiki_suppression_audit"]["reason"] == reason
    assert compact["jue_wiki_suppression_audit"]["snapshot_id"] == snapshot_id
    if with_emergency_noise:
        assert compact["_storage_compaction"]["emergency"] is True
    else:
        assert "_storage_compaction" not in compact


def test_binance_compact_manager_response_preserves_wiki_suppression_audit() -> None:
    response = {
        "payload": {"decision": "hold"},
        **_wiki_gate_storage_contracts(),
    }

    compact = compact_manager_response_payload(response)

    assert compact["jue_wiki_suppression_audit"]["suppressed_new_risk_count"] == 199


def test_compact_jue_wiki_for_prompt_canonicalizes_quality_aliases() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-manager-prompt-aliases",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "quality_status": "degraded",
                    "source_refs": [
                        {
                            "source_type": "crypto_quant",
                            "source_id": "ETHUSDT:manager-prompt",
                            "quality_status": "degraded",
                            "quality_warnings": ["funding_missing"],
                            "evidence_quality": {
                                "status_counts": {"ok": 1, "degraded": 1},
                                "top_warnings": [
                                    {"warning": "funding_missing", "count": 1}
                                ],
                            },
                            "raw_blob": "drop-me",
                        }
                    ],
                    "evidence_quality": {
                        "status_counts": {"ok": 1, "degraded": 1},
                        "top_warnings": [{"warning": "funding_missing", "count": 1}],
                    },
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "ETHUSDT",
                    "page_id": "binance.symbol.ETHUSDT",
                    "title": "ETHUSDT memory",
                    "freshness": "stale",
                    "freshness_status": "stale",
                    "freshness_warnings": ["freshness_label_stale"],
                    "quality_status": "ok",
                    "quality_warnings": ["funding_missing"],
                    "summary": "Keep this requested symbol summary. " + ("DROP_ME" * 60),
                    "memory_card": {
                        "stance": "wait for pullback",
                        "durable_facts": "ETHUSDT rewards pullback confirmation.",
                        "open_questions": "Check funding before futures entry.",
                        "lessons": "DROP_ME" * 80,
                    },
                    "evidence_quality": {
                        "status_counts": {"ok": 1, "degraded": 1},
                        "top_warnings": [
                            {"warning": "funding_missing", "count": 1}
                        ],
                    },
                    "raw_blob": "drop-me",
                },
                {
                    "symbol": "DOGEUSDT",
                    "page_id": "binance.symbol.DOGEUSDT",
                    "quality_status": "ok",
                    "summary": "Thin requested symbol memory.",
                    "memory_card": {"stance": "thin watch only"},
                }
            ],
        },
        list_limit=4,
        string_limit=160,
    )

    page = compact["pages"][0]
    assert page["quality_status"] == "weak"
    assert page["evidence_quality"]["status_counts"] == {"strong": 1, "weak": 1}
    source_ref = page["source_refs"][0]
    assert source_ref["quality_status"] == "weak"
    assert source_ref["evidence_quality"]["status_counts"] == {
        "strong": 1,
        "weak": 1,
    }
    assert "raw_blob" not in source_ref
    summary = compact["requested_symbol_summaries"][0]
    assert summary["symbol"] == "ETHUSDT"
    assert summary["freshness_status"] == "stale"
    assert summary["freshness_warnings"] == ["freshness_label_stale"]
    assert summary["quality_status"] == "strong"
    assert summary["evidence_quality"]["status_counts"] == {"strong": 1, "weak": 1}
    assert summary["memory_card"] == {
        "stance": "wait for pullback",
        "durable_facts": "ETHUSDT rewards pullback confirmation.",
        "open_questions": "Check funding before futures entry.",
    }
    assert summary["memory_card_quality"] == {
        "status": "strong",
        "present_keys": ["stance", "durable_facts", "open_questions"],
        "missing_keys": ["lessons"],
    }
    assert "raw_blob" not in summary
    assert summary["summary"].startswith("Keep this requested symbol summary.")
    assert len(summary["summary"]) <= 160
    assert "DROP_ME" not in json.dumps(summary["memory_card"], ensure_ascii=False)
    thin_summary = compact["requested_symbol_summaries"][1]
    assert thin_summary["memory_card_quality"] == {
        "status": "weak",
        "present_keys": ["stance"],
        "missing_keys": ["durable_facts", "lessons", "open_questions"],
        "required_action": "cross_check_live_research_before_high_confidence",
    }


def test_compact_jue_wiki_for_prompt_prioritizes_live_outcome_effectiveness_reasons() -> None:
    noisy_reasons = [f"low_priority_reason_{idx}" for idx in range(20)]

    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-manager-live-outcomes",
            "pages": [
                {
                    "page_id": "binance.performance.live_outcomes",
                    "effectiveness": {
                        "status": "probe",
                        "sample_count": 1,
                        "win_rate": 1.0,
                        "expectancy": 1.4,
                        "avg_return_pct": 0.9,
                        "helpful_score": 2.9,
                        "confidence": 0.2,
                        "reasons": [
                            *noisy_reasons,
                            "metric_source=live_block_performance",
                            "page_id=binance.performance.live_outcomes",
                            "raw_venue=binance_futures",
                        ],
                    },
                }
            ],
        },
        list_limit=4,
        string_limit=200,
    )

    reasons = compact["pages"][0]["effectiveness"]["reasons"]
    assert len(reasons) <= 8
    assert "metric_source=live_block_performance" in reasons
    assert "page_id=binance.performance.live_outcomes" in reasons
    assert "raw_venue=binance_futures" in reasons


def test_compact_jue_wiki_for_prompt_preserves_freshness_metadata() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-freshness",
            "freshness_summary": {
                "page_count": 1,
                "status_counts": {"stale": 1},
                "warning_counts": {"updated_at_stale_gt_14d": 1},
                "stale_page_ids": ["binance:playbook"],
                "unknown_page_ids": [],
            },
            "pages": [
                {
                    "page_id": "binance:playbook",
                    "freshness": "current",
                    "freshness_status": "stale",
                    "freshness_warnings": ["updated_at_stale_gt_14d"],
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["freshness_summary"] == {
        "page_count": 1,
        "status_counts": {"stale": 1},
        "warning_counts": {"updated_at_stale_gt_14d": 1},
        "stale_page_ids": ["binance:playbook"],
        "unknown_page_ids": [],
    }
    assert compact["pages"][0]["freshness_status"] == "stale"
    assert compact["pages"][0]["freshness_warnings"] == ["updated_at_stale_gt_14d"]


def test_compact_jue_wiki_for_prompt_promotes_requested_symbol_coverage_from_budget_report() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "target_scope": "binance",
            "budget_report": {
                "char_count": 1600,
                "requested_symbol_count": 5,
                "requested_symbol_summary_symbols": ["BTCUSDT", "ETHUSDT"],
                "requested_symbol_unsummarized_count": 3,
                "requested_symbol_unsummarized_symbols": [
                    "NEARUSDT",
                    "LTCUSDT",
                    "DOGEUSDT",
                ],
                "requested_symbol_missing_summary_count": 1,
                "requested_symbol_missing_summary_symbols": ["DOGEUSDT"],
                "requested_symbol_prompt_omitted_count": 2,
                "requested_symbol_prompt_omitted_symbols": ["NEARUSDT", "LTCUSDT"],
                "requested_symbol_degraded_summary_count": 1,
                "requested_symbol_degraded_summary_symbols": ["ETHUSDT"],
                "requested_symbol_summary_coverage_status": "partial",
                "verbose_debug": "DROP_ME" * 100,
            },
        },
        list_limit=2,
        string_limit=120,
    )

    assert compact["requested_symbol_coverage"] == {
        "status": "partial",
        "requested_symbol_count": 5,
        "summarized_symbol_count": 2,
        "unsummarized_symbol_count": 3,
        "unsummarized_symbols": ["NEARUSDT", "LTCUSDT"],
        "unsummarized_symbol_omitted_count": 1,
        "missing_summary_count": 1,
        "missing_summary_symbols": ["DOGEUSDT"],
        "prompt_omitted_count": 2,
        "prompt_omitted_symbols": ["NEARUSDT", "LTCUSDT"],
        "degraded_summary_count": 1,
        "degraded_summary_symbols": ["ETHUSDT"],
    }
    assert "DROP_ME" not in json.dumps(compact["requested_symbol_coverage"])


def test_compact_jue_wiki_for_prompt_preserves_repair_action_batches() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "target_scope": "binance",
            "repair_action_batches": [
                {
                    "scope": "binance",
                    "action_type": "refresh_crypto_microstructure",
                    "count": 4,
                    "symbols": ["NEARUSDT", "LTCUSDT"],
                    "warnings": ["funding_missing"],
                    "recommended_actions": ["refresh_crypto_microstructure"],
                    "raw_notes": "x" * 500,
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["repair_action_batches"] == [
        {
            "scope": "binance",
            "action_type": "refresh_crypto_microstructure",
            "count": 4,
            "symbols": ["NEARUSDT", "LTCUSDT"],
            "warnings": ["funding_missing"],
            "recommended_actions": ["refresh_crypto_microstructure"],
        }
    ]


def test_compact_jue_wiki_for_prompt_preserves_repair_queue_summary() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "target_scope": "binance",
            "repair_queue": {
                "open_count": 2,
                "resolved_count": 1,
                "open_symbols": ["NEARUSDT", "LTCUSDT", "BTCUSDT"],
                "open_action_batches": [
                    {
                        "scope": "binance",
                        "action_type": "refresh_crypto_microstructure",
                        "count": 4,
                        "symbols": ["NEARUSDT", "LTCUSDT"],
                        "warnings": ["funding_missing"],
                        "recommended_actions": ["refresh_crypto_microstructure"],
                        "raw_notes": "x" * 500,
                    }
                ],
                "raw_debug": "DROP_ME",
            },
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["repair_queue"] == {
        "open_count": 2,
        "resolved_count": 1,
        "open_symbols": ["NEARUSDT", "LTCUSDT", "BTCUSDT"],
        "open_action_batches": [
            {
                "scope": "binance",
                "action_type": "refresh_crypto_microstructure",
                "count": 4,
                "symbols": ["NEARUSDT", "LTCUSDT"],
                "warnings": ["funding_missing"],
                "recommended_actions": ["refresh_crypto_microstructure"],
            }
        ],
    }
    assert "DROP_ME" not in json.dumps(compact, ensure_ascii=False)


def test_compact_jue_wiki_for_prompt_preserves_nested_repair_queue_evidence() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "quality_status": "partial",
                    "quality_warnings": [
                        "open_repair_queue",
                        "application_repair_queue_pressure",
                    ],
                    "evidence_quality": {
                        "source_count": 2,
                        "status_counts": {"strong": 1, "partial": 1},
                        "warning_counts": {
                            "open_repair_queue": 1,
                            "application_repair_queue_pressure": 1,
                        },
                        "repair_queue": {
                            "open_count": 1,
                            "raw_debug": "DROP_ME",
                            "actions": [
                                {
                                    "action_type": (
                                        "repair_application_repair_queue_pressure"
                                    ),
                                    "status": "scheduled",
                                    "quality_warnings": [
                                        "application_repair_queue_pressure"
                                    ],
                                    "raw_blob": "DROP_ME",
                                }
                            ],
                        },
                    },
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "NEARUSDT",
                    "page_id": "binance.symbol.NEARUSDT",
                    "quality_status": "partial",
                    "quality_warnings": ["open_repair_queue"],
                    "evidence_quality": {
                        "source_count": 2,
                        "status_counts": {"strong": 1, "partial": 1},
                        "warning_counts": {"open_repair_queue": 1},
                        "repair_queue": {
                            "open_count": 1,
                            "actions": [
                                {
                                    "action_type": (
                                        "repair_application_repair_queue_pressure"
                                    ),
                                    "status": "scheduled",
                                    "quality_warnings": [
                                        "application_repair_queue_pressure"
                                    ],
                                    "raw_blob": "DROP_ME",
                                }
                            ],
                        },
                    },
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    expected_repair_queue = {
        "open_count": 1,
        "actions": [
            {
                "action_type": "repair_application_repair_queue_pressure",
                "status": "scheduled",
                "quality_warnings": ["application_repair_queue_pressure"],
            }
        ],
    }
    page_quality = compact["pages"][0]["evidence_quality"]
    assert page_quality["repair_queue"] == expected_repair_queue
    summary_quality = compact["requested_symbol_summaries"][0]["evidence_quality"]
    assert summary_quality["repair_queue"] == expected_repair_queue
    assert "DROP_ME" not in json.dumps(compact, ensure_ascii=False)


def test_compact_jue_wiki_for_prompt_preserves_page_effectiveness_repair_pressure() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "effectiveness": {
                        "status": "degraded",
                        "venue": "binance",
                        "horizon": "intraday",
                        "sample_count": 4,
                        "win_rate": 0.25,
                        "expectancy": -0.65,
                        "helpful_score": -8.5,
                        "confidence": 0.9,
                        "reasons": [
                            "application_repair_queue_pressure",
                            "repair_queue_open_count:2",
                            "repair_queue_action:repair_crypto_microstructure",
                            "DROP_ME_LONG_REASON" * 40,
                        ],
                    },
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "NEARUSDT",
                    "page_id": "binance.symbol.NEARUSDT",
                    "effectiveness": {
                        "status": "degraded",
                        "sample_count": 4,
                        "helpful_score": -8.5,
                        "reasons": [
                            "application_repair_queue_pressure",
                            "repair_queue_open_count:2",
                        ],
                    },
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    expected = {
        "status": "degraded",
        "venue": "binance",
        "horizon": "intraday",
        "sample_count": 4,
        "win_rate": 0.25,
        "expectancy": -0.65,
        "helpful_score": -8.5,
        "confidence": 0.9,
        "reasons": [
            "application_repair_queue_pressure",
            "repair_queue_open_count:2",
            "repair_queue_action:repair_crypto_microstructure",
        ],
    }
    page = compact["pages"][0]
    summary = compact["requested_symbol_summaries"][0]
    assert page["effectiveness"] == expected
    assert summary["effectiveness"] == {
        "status": "degraded",
        "sample_count": 4,
        "helpful_score": -8.5,
        "reasons": [
            "application_repair_queue_pressure",
            "repair_queue_open_count:2",
        ],
    }
    assert "DROP_ME_LONG_REASON" not in str(compact)


def test_compact_jue_wiki_for_prompt_preserves_quality_warning_effectiveness() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-quality-warning-effectiveness",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "quality_warning_effectiveness": [
                        {
                            "warning": "funding_missing",
                            "page_id": "binance.symbol.ETHUSDT",
                            "status": "degraded",
                            "sample_count": 4,
                            "win_rate": 0.25,
                            "expectancy": -0.8,
                            "helpful_score": -6.5,
                            "confidence": 0.76,
                            "reasons": [
                                "quality_warning:funding_missing",
                                "ignored_funding_gap",
                            ],
                        }
                    ],
                    "quality_warning_effectiveness_statuses": ["degraded"],
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["pages"][0]["quality_warning_effectiveness"] == [
        {
            "warning": "funding_missing",
            "page_id": "binance.symbol.ETHUSDT",
            "status": "degraded",
            "sample_count": 4,
            "win_rate": 0.25,
            "expectancy": -0.8,
            "helpful_score": -6.5,
            "confidence": 0.76,
            "reasons": [
                "quality_warning:funding_missing",
                "ignored_funding_gap",
            ],
        }
    ]
    assert compact["pages"][0]["quality_warning_effectiveness_statuses"] == [
        "degraded"
    ]


def test_compact_jue_wiki_for_prompt_preserves_selected_page_guidance_metadata() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-guidance-metadata",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "usage_guidance": {
                        "trust_level": "high",
                        "risk_posture": "standard_block_design",
                        "decision_use": (
                            "eligible_for_standard_block_design_after_live_checks"
                        ),
                        "allowed_uses": ["standard_block", "target_stop_context"],
                        "required_cross_checks": ["live_quote", "funding_rate"],
                        "hard_blocker": False,
                    },
                    "usage_guidance_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "standard_block_design"
                                ),
                                "status": "active",
                                "sample_count": 7,
                                "win_rate": 0.64,
                                "expectancy": 0.55,
                                "helpful_score": 6.5,
                                "confidence": 0.74,
                                "reasons": [
                                    "usage_guidance:risk_posture:standard_block_design"
                                ],
                            }
                        ],
                        "decision_use": (
                            "prior usage guidance has positive evidence; still "
                            "cross-check live execution data"
                        ),
                    },
                    "memory_card_quality_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": (
                                    "memory_card_quality.required_check."
                                    "refresh_crypto_alpha_facts"
                                ),
                                "status": "active",
                                "sample_count": 4,
                                "expectancy": 0.7,
                                "helpful_score": 6.0,
                                "confidence": 0.72,
                                "reasons": [
                                    "memory_card_quality:required_check:"
                                    "refresh_crypto_alpha_facts"
                                ],
                            }
                        ],
                    },
                    "quality_warning_source_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": "binance.symbol.ETHUSDT",
                                "status": "active",
                                "sample_count": 4,
                                "expectancy": 0.62,
                                "helpful_score": 6.2,
                                "confidence": 0.78,
                                "reasons": ["quality_warning_source_page"],
                            }
                        ],
                    },
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    page = compact["pages"][0]
    assert page["usage_guidance"] == {
        "trust_level": "high",
        "risk_posture": "standard_block_design",
        "decision_use": "eligible_for_standard_block_design_after_live_checks",
        "allowed_uses": ["standard_block", "target_stop_context"],
        "required_cross_checks": ["live_quote", "funding_rate"],
        "hard_blocker": False,
    }
    assert page["usage_guidance_effectiveness"]["status"] == "active"
    assert page["usage_guidance_effectiveness"]["metrics"][0]["page_id"] == (
        "usage_guidance.risk_posture.standard_block_design"
    )
    assert page["memory_card_quality_effectiveness"]["metrics"][0]["page_id"] == (
        "memory_card_quality.required_check.refresh_crypto_alpha_facts"
    )
    assert page["quality_warning_source_effectiveness"]["metrics"][0][
        "page_id"
    ] == "binance.symbol.ETHUSDT"


def test_compact_jue_wiki_for_prompt_compacts_effectiveness_attention_items() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-attention",
            "effectiveness_attention_items": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "kind": "usage_guidance",
                    "status": "active",
                    "evidence_id": (
                        "usage_guidance.risk_posture.wait_for_pullback"
                    ),
                    "details": "x" * 500,
                },
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "kind": "quality_warning",
                    "status": "degraded",
                    "warning": "funding_missing",
                    "details": "y" * 500,
                },
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["effectiveness_attention_items"] == [
        {
            "page_id": "binance.symbol.ETHUSDT",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.wait_for_pullback",
        },
        {
            "page_id": "binance.symbol.ETHUSDT",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "funding_missing",
        },
    ]


def test_compact_jue_wiki_for_prompt_derives_effectiveness_attention_items_from_rows() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-attention-derived",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "usage_guidance_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "wait_for_pullback"
                                ),
                                "status": "active",
                            }
                        ],
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "funding_missing",
                            "status": "degraded",
                        }
                    ],
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["effectiveness_attention_items"] == [
        {
            "page_id": "binance.symbol.ETHUSDT",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.wait_for_pullback",
        },
        {
            "page_id": "binance.symbol.ETHUSDT",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "funding_missing",
        },
    ]


def test_compact_jue_wiki_for_prompt_preserves_memory_card_quality_details() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:binance-memory-card-quality-details",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "page_type": "symbol",
                    "symbols": ["ETHUSDT"],
                    "memory_card_quality": {
                        "status": "active",
                        "resolution": "unresolved",
                        "symbols": ["ETHUSDT"],
                        "required_action": (
                            "cross_check_live_research_before_high_confidence"
                        ),
                        "missing_fields": ["durable_facts", "lessons"],
                        "required_checks": [
                            "refresh_crypto_alpha_facts",
                            "inspect_block_lessons",
                        ],
                        "items": [
                            {
                                "status": "active",
                                "resolution": "unresolved",
                                "symbols": ["ETHUSDT"],
                                "required_action": (
                                    "cross_check_live_research_before_high_confidence"
                                ),
                                "missing_fields": ["durable_facts", "lessons"],
                                "required_checks": [
                                    "refresh_crypto_alpha_facts",
                                    "inspect_block_lessons",
                                ],
                            }
                        ],
                        "decision_use": "memory_card_quality_resolution_check",
                        "candidate_resolution_required": True,
                    },
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "ETHUSDT",
                    "page_id": "binance.symbol.ETHUSDT",
                    "memory_card": {"stance": "thin watch only"},
                    "memory_card_quality": {
                        "status": "active",
                        "resolution": "unresolved",
                        "symbols": ["ETHUSDT"],
                        "required_action": (
                            "cross_check_live_research_before_high_confidence"
                        ),
                        "missing_fields": ["durable_facts", "lessons"],
                        "required_checks": [
                            "refresh_crypto_alpha_facts",
                            "inspect_block_lessons",
                        ],
                        "decision_use": "memory_card_quality_resolution_check",
                        "candidate_resolution_required": True,
                    },
                }
            ],
        },
        list_limit=4,
        string_limit=160,
    )

    expected_quality = {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["ETHUSDT"],
        "required_action": "cross_check_live_research_before_high_confidence",
        "missing_fields": ["durable_facts", "lessons"],
        "required_checks": ["refresh_crypto_alpha_facts", "inspect_block_lessons"],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }
    assert compact["pages"][0]["memory_card_quality"] == {
        **expected_quality,
        "items": [
            {
                "status": "active",
                "resolution": "unresolved",
                "symbols": ["ETHUSDT"],
                "required_action": (
                    "cross_check_live_research_before_high_confidence"
                ),
                "missing_fields": ["durable_facts", "lessons"],
                "required_checks": [
                    "refresh_crypto_alpha_facts",
                    "inspect_block_lessons",
                ],
            }
        ],
    }
    assert compact["requested_symbol_summaries"][0]["memory_card_quality"] == (
        expected_quality
    )


def test_binance_block_trader_does_not_reown_manager_prompt_candidate_wrappers() -> None:
    source = (ROOT / "src/tradecraft/services/binance_block_trader.py").read_text()

    for marker in (
        "def _manager_candidate_identity_from_payload(",
        "def _manager_action_candidate_keys(",
        "def _prioritize_manager_candidate_rows(",
        "def _compact_prompt_candidate_minimal(",
        "def _compact_prompt_candidates_minimal(",
        "def _compact_manager_lane_action_for_prompt(",
        "def _compact_manager_lane_authority_for_prompt(",
        "def _compact_candidate_policy_impacts_for_latency(",
        "def _compact_entry_gate_policy_for_prompt(",
    ):
        assert marker not in source
    assert "build_manager_action_candidate_keys(" in source


def test_binance_block_trader_does_not_reown_manager_prompt_budget_wrappers() -> None:
    source = (ROOT / "src/tradecraft/services/binance_block_trader.py").read_text()

    for marker in (
        "def _compact_prompt_value(",
        "def _compact_prompt_value_bounded(",
        "def _compact_manager_block_for_prompt(",
        "def _compact_manager_blocks_for_prompt(",
        "def _compact_live_authority_prompt_value(",
        "def _compact_manager_live_authority_for_prompt(",
        "def _compact_validation_repair_prompt(",
        "def _manager_run_workflow_provenance(",
        "def _prompt_chars(",
        "def _prompt_chars_capped(",
        "def _manager_prompt_original_chars_hint(",
        "def _prompt_section_size_rows(",
        "def _attach_prompt_budget(",
        "def _prompt_budget_error(",
        "def _format_prompt_budget_alert_message(",
        "def _manager_storage_compaction_meta(",
        "def _validation_repair_action_metadata(",
        "def _validation_evidence_plan_from_repair(",
        "def _validation_repair_note(",
        "def _validation_repair_discipline_tokens(",
        "def _compact_validation_repair_for_storage(",
        "def _manager_run_is_timeout_error(",
        "def _manager_latency_guard_from_runs(",
        "def _apply_manager_latency_guard(",
        "def _enforce_manager_prompt_budget(",
        "def _extend_manager_prompt_compaction(",
        "def _compact_manager_sections_for_final_budget(",
        "def _compact_manager_storage_payload(",
        "def _finalize_manager_prompt_budget(",
    ):
        assert marker not in source


def test_binance_block_trader_does_not_reown_manager_prompt_account_wrappers() -> None:
    source = (ROOT / "src/tradecraft/services/binance_block_trader.py").read_text()

    for marker in (
        "def _compact_account_asset_rows(",
        "def _compact_account_asset_row(",
        "def _is_meaningful_futures_risk_row(",
        "def _compact_futures_risk_row(",
    ):
        assert marker not in source


def test_binance_final_prompt_budget_never_omits_output_schema() -> None:
    output_schema = {
        "create_blocks": [
            {
                "symbol": "BTCUSDT",
                "market": "spot|futures",
                "entry_price": "required number",
                "target_price": "required number",
                "stop_price": "required number",
            }
        ],
        "lane_review": {
            "required": "mandatory on every response",
            "selected_lanes": ["spot:long"],
        },
    }
    prompt = {
        "task": "Build executable Binance blocks." + ("X" * 16_000),
        "output_schema": output_schema,
        "raw_context_refs": {"blob": "Y" * 4_000},
    }

    sections = compact_manager_sections_for_final_budget(prompt, target_chars=10_000)

    assert prompt["output_schema"] == output_schema
    assert prompt["output_schema"]["lane_review"]["required"].startswith("mandatory")
    assert not any(
        row.get("section") == "output_schema:final_omitted" for row in sections
    )


def test_binance_manager_prompt_exposes_native_output_schema_for_manager_contract() -> None:
    prompt = build_binance_manager_prompt_payload(
        allowed_actions=["create_blocks", "update_blocks", "close_blocks", "pause_blocks"],
        language_policy={},
        manager_lanes=["spot:long", "futures:long", "futures:short", "upbit_spot:long"],
        account={},
        growth_target={},
        growth_governor={},
        growth_unlock={},
        risk_guard={},
        execution_gate={},
        memory_context={},
        decision_packet_v2={},
        decision_packet={},
        candidate_policy_impacts={},
        validation_repair={},
        crypto_market_pulse={},
        raw_context_refs={},
        recent_performance={},
        performance={},
        entry_gate_policy={},
        live_authority={},
        lane_balance={},
        candidate_generation={},
        candidates=[],
        universe=[],
        market_universe={},
        blocks=[],
    )

    assert prompt["native_output_schema"] == prompt["output_schema"]
    assert "adopt_existing_blocks" in prompt["policy"]["allowed_actions"]
    assert "adopt_existing_blocks" in prompt["native_output_schema"]
    adopt_schema = prompt["native_output_schema"]["adopt_existing_blocks"][0]
    assert adopt_schema["symbol"] == "BTCUSDT"
    assert "without sending a new entry order" in adopt_schema["adoption_note"]
    assert "jue_wiki_usage_contract_resolution" in adopt_schema
    assert "lane_review" in prompt["native_output_schema"]
    critical_contract = prompt["critical_response_contract"]
    assert "adopt_existing_blocks" in critical_contract["required_top_level_keys"]
    assert "adopt_existing_blocks" in critical_contract["on_hold_only"]["instruction"]
    assert "adopt/update/close/pause" in critical_contract["on_hold_only"]["instruction"]
    assert prompt["critical_response_contract"]["lane_review"]["required"] is True
    wiki_effectiveness_policy = prompt["policy"]["jue_wiki_effectiveness_policy"]
    assert "active Wiki pages" in wiki_effectiveness_policy
    assert "probe Wiki pages" in wiki_effectiveness_policy
    assert "degraded Wiki pages" in wiki_effectiveness_policy
    assert "repair/probe evidence" in wiki_effectiveness_policy
    assert "validation_repair_resolution" in prompt["native_output_schema"]
    repair_contract = prompt["critical_response_contract"][
        "validation_repair_resolution"
    ]
    assert repair_contract["blanket_hold_allowed"] is False
    assert "jue_wiki_contract_feedback_gap" in repair_contract["instruction"]
    assert "jue_wiki_memory_card_quality" in repair_contract["instruction"]
    assert "not blanket no-trade reasons" in repair_contract["instruction"]
    assert "jue_wiki_contract_feedback_gap" in prompt["native_output_schema"][
        "validation_repair_resolution"
    ]["required"]
    assert "jue_wiki_memory_card_quality" in prompt["native_output_schema"][
        "validation_repair_resolution"
    ]["required"]
    candidate_visibility = critical_contract["create_blocks_candidate_visibility"]
    assert candidate_visibility["error"] == "manager_create_candidate_not_visible"
    assert "visible candidates" in candidate_visibility["instruction"]
    assert "market_universe" in candidate_visibility["instruction"]
    create_schema = prompt["native_output_schema"]["create_blocks"][0]
    assert "visible candidates" in create_schema["symbol"]
    assert "jue_wiki_repair_pressure" in create_schema
    assert "repair pressure" in create_schema["jue_wiki_repair_pressure"]
    assert "jue_wiki_repair_resolution" in create_schema
    assert "degraded Wiki effectiveness" in create_schema[
        "jue_wiki_repair_resolution"
    ]
    assert "action metadata" in create_schema["jue_wiki_repair_resolution"]
    assert "jue_wiki_memory_card_quality" in create_schema
    assert "thin Wiki memory card" in create_schema["jue_wiki_memory_card_quality"]
    for action_key in (
        "adopt_existing_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        schema = prompt["native_output_schema"][action_key][0]
        assert "degraded Wiki effectiveness" in schema[
            "jue_wiki_repair_resolution"
        ]


def test_binance_manager_response_contract_rejects_unresolved_repair_hold() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "wait"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wait"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_ignores_kis_scoped_validation_repair() -> None:
    prompt = {
        "validation_repair": {
            "target_scope": "kis",
            "repair_item_count": 1,
            "constraint_count": 1,
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "Binance judgment proceeds"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "Binance judgment proceeds"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "Binance judgment proceeds"},
    )

    assert error == ""
    assert "unresolved_validation_repair_probe" not in diagnostics["blocker_tags"]
    assert diagnostics["validation_repair_item_count"] == 0
    assert diagnostics["validation_repair_constraint_count"] == 0


def test_binance_manager_response_contract_accepts_concrete_repair_resolution() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "NEARUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "orderbook depth is not strong enough yet",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "NEARUSDT rejected until depth improves"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_negative_repair_resolution() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "NEARUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "validation repair not resolved; "
                            "no concrete next trigger available"
                        ),
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "NEARUSDT repair still unresolved"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_rejects_memory_contract_repair_on_wrong_symbol_action() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {
                "scope": "binance",
                "status": "active",
                "repair_item_count": 1,
                "block_design_constraints": [
                    {
                        "symbol": "ETHUSDT",
                        "memory_contract": "period_memory_override_reason",
                        "memory_contract_error": "missing override reason",
                        "required_checks": ["require_memory_contract_resolution"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "thesis": "period_memory_override_reason repair is reflected",
                    "metadata": {
                        "jue_wiki_repair_resolution": (
                            "resolution: period_memory_override_reason repair note recorded"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "memory_contract_resolution_missing_from_model"


def test_binance_manager_accepts_memory_contract_repair_on_matching_symbol_action() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {
                "scope": "binance",
                "status": "active",
                "repair_item_count": 1,
                "block_design_constraints": [
                    {
                        "symbol": "ETHUSDT",
                        "memory_contract": "period_memory_override_reason",
                        "memory_contract_error": "missing override reason",
                        "required_checks": ["require_memory_contract_resolution"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "thesis": "period_memory_override_reason repair is reflected",
                    "metadata": {
                        "jue_wiki_repair_resolution": (
                            "resolution: period_memory_override_reason repair note recorded"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_rejects_negative_memory_contract_repair_resolution() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {
                "scope": "binance",
                "status": "active",
                "repair_item_count": 1,
                "block_design_constraints": [
                    {
                        "symbol": "ETHUSDT",
                        "memory_contract": "period_memory_override_reason",
                        "memory_contract_error": "missing override reason",
                        "required_checks": ["require_memory_contract_resolution"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "thesis": "period_memory_override_reason repair was not applied",
                    "metadata": {
                        "jue_wiki_repair_resolution": (
                            "period_memory_override_reason memory contract not applied; "
                            "no fresh memory contract resolution available"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "memory_contract_resolution_missing_from_model"


def test_binance_manager_run_diagnostics_tracks_memory_contract_resolution() -> None:
    prompt = {
        "validation_repair": {
            "scope": "binance",
            "status": "active",
            "repair_item_count": 2,
            "block_design_constraints": [
                {
                    "symbol": "ETHUSDT",
                    "memory_contract": "period_memory_override_reason",
                    "memory_contract_error": "missing override reason",
                    "required_checks": ["require_memory_contract_resolution"],
                },
                {
                    "symbol": "SOLUSDT",
                    "memory_contract": "fresh_period_review_or_replay",
                    "memory_contract_error": "missing fresh period review",
                    "required_checks": ["require_memory_contract_resolution"],
                },
            ],
        }
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "thesis": "period_memory_override_reason repair is reflected",
                    "metadata": {
                        "jue_wiki_repair_resolution": (
                            "resolution: period_memory_override_reason repair note"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    compact = compact_manager_diagnostics_for_storage(diagnostics)

    assert diagnostics["memory_contract_status"] == "partial"
    assert diagnostics["memory_contract_count"] == 2
    assert diagnostics["memory_contract_resolved_count"] == 1
    assert diagnostics["memory_contract_unresolved_count"] == 1
    assert diagnostics["memory_contract_missing_symbols"] == ["SOLUSDT"]
    assert diagnostics["memory_contract_missing_contracts"] == [
        "fresh_period_review_or_replay"
    ]
    assert diagnostics["memory_contract_missing_errors"] == [
        "missing fresh period review"
    ]
    assert diagnostics["memory_contract_resolution_modes"] == ["action_metadata"]
    assert diagnostics["memory_contract_action_resolved_count"] == 1
    assert diagnostics["memory_contract_hold_resolved_count"] == 0
    assert diagnostics["memory_contract_response_resolved_count"] == 0
    assert diagnostics["memory_contract_rows"] == [
        {
            "symbol": "ETHUSDT",
            "status": "resolved",
            "contracts": ["period_memory_override_reason"],
            "errors": ["missing override reason"],
            "resolution_modes": ["action_metadata"],
        },
        {
            "symbol": "SOLUSDT",
            "status": "unresolved",
            "contracts": ["fresh_period_review_or_replay"],
            "errors": ["missing fresh period review"],
            "resolution_modes": [],
        },
    ]
    assert diagnostics["blocker_tags"]["unresolved_memory_contract"] == 1
    assert compact["memory_contract_status"] == "partial"
    assert compact["memory_contract_missing_symbols"] == ["SOLUSDT"]
    assert compact["memory_contract_missing_contracts"] == [
        "fresh_period_review_or_replay"
    ]
    assert compact["memory_contract_missing_errors"] == [
        "missing fresh period review"
    ]
    assert compact["memory_contract_resolution_modes"] == ["action_metadata"]
    assert compact["memory_contract_action_resolved_count"] == 1
    assert compact["memory_contract_rows"] == diagnostics["memory_contract_rows"]


def test_binance_manager_run_diagnostics_rejects_negative_memory_contract_response_resolution() -> None:
    prompt = {
        "validation_repair": {
            "scope": "binance",
            "status": "active",
            "repair_item_count": 1,
            "block_design_constraints": [
                {
                    "symbol": "ETHUSDT",
                    "memory_contract": "period_memory_override_reason",
                    "memory_contract_error": "missing override reason",
                    "required_checks": ["require_memory_contract_resolution"],
                }
            ],
        }
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ETHUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "period_memory_override_reason memory contract not "
                            "applied; next trigger staged without fresh memory"
                        ),
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["memory_contract_status"] == "unresolved"
    assert diagnostics["memory_contract_resolved_count"] == 0
    assert diagnostics["memory_contract_unresolved_count"] == 1
    assert diagnostics["memory_contract_response_resolved_count"] == 0
    assert diagnostics["blocker_tags"]["unresolved_memory_contract"] == 1


def test_binance_manager_response_contract_rejects_unaddressed_wiki_decision_adjustment() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "wait"}},
        actions=actions,
        hold_decision={"summary": "wait"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_ignores_kis_scoped_wiki_decision_adjustment() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "kis",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "knowledge_spine",
                    "reason": "kis_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "crypto has no scoped adjustment"}},
        actions=actions,
        hold_decision={"summary": "crypto has no scoped adjustment"},
    )

    assert error == ""


def test_binance_manager_response_contract_requires_translation_evidence_for_translated_kis_wiki_decision_adjustment() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "kis",
            "transferability": "translated",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "knowledge_spine",
                    "reason": "kis_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "knowledge_spine trigger staged"}},
        actions=actions,
        hold_decision={
            "summary": "knowledge_spine 전환은 spread와 orderbook 확인 후 대기",
            "watch_symbols": ["NEARUSDT"],
            "next_triggers": [
                {
                    "symbol": "NEARUSDT",
                    "condition": (
                        "knowledge_spine shift applies only after spread, funding, "
                        "and orderbook depth agree"
                    ),
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_accepts_translated_kis_wiki_decision_adjustment_with_translation_evidence() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "kis",
            "transferability": "translated",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "knowledge_spine",
                    "reason": "kis_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "translated posture trigger staged"}},
        actions=actions,
        hold_decision={
            "summary": (
                "translated_crypto_mapping: KIS knowledge_spine posture mapped "
                "to Binance spread, funding, orderbook depth, and quant checks"
            ),
            "watch_symbols": ["NEARUSDT"],
            "next_triggers": [
                {
                    "symbol": "NEARUSDT",
                    "condition": (
                        "knowledge_spine shift applies only after translated_crypto_mapping "
                        "and funding agree"
                    ),
                }
            ],
        },
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_translated_wiki_decision_adjustment_response_without_translation_evidence() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "kis",
            "transferability": "translated",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "knowledge_spine",
                    "reason": "kis_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "NEARUSDT",
                        "resolution": "small_waiting_block",
                        "next_trigger": (
                            "knowledge_spine posture mapped to spread and funding checks"
                        ),
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_accepts_wiki_decision_adjustment_hold_trigger() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "repair_probe trigger staged"}},
        actions=actions,
        hold_decision={
            "summary": "repair_probe 전환은 orderbook depth 확인 후 대기",
            "watch_symbols": ["NEARUSDT"],
            "next_triggers": [
                {
                    "symbol": "NEARUSDT",
                    "condition": (
                        "repair_probe shift applies only after spread, funding, "
                        "and orderbook depth agree"
                    ),
                }
            ],
        },
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_negative_wiki_decision_adjustment_hold_note() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "repair_probe 보정 미적용"}},
        actions=actions,
        hold_decision={
            "summary": "repair_probe adjustment not applied",
            "watch_symbols": ["NEARUSDT"],
            "next_triggers": [
                {
                    "symbol": "NEARUSDT",
                    "condition": (
                        "repair_probe shift not performed despite spread watch"
                    ),
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_requires_wiki_decision_adjustment_evidence_grade() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                    "evidence_grade": {
                        "status": "negative",
                        "basis": "decision_adjustment_effectiveness",
                        "sample_count": 5,
                        "avg_return_pct": -0.7,
                        "confidence": 0.9,
                        "instruction": "audit_or_repair_probe_only",
                    },
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "repair_probe trigger staged"}},
        actions=actions,
        hold_decision={
            "summary": "repair_probe 전환은 spread와 orderbook 확인 후 대기",
            "watch_symbols": ["NEARUSDT"],
            "next_triggers": [
                {
                    "symbol": "NEARUSDT",
                    "condition": (
                        "repair_probe shift applies only after spread and "
                        "orderbook depth agree"
                    ),
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_requires_wiki_execution_hint_on_actions() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                    "evidence_grade": {
                        "status": "negative",
                        "basis": "decision_adjustment_effectiveness",
                        "instruction": "audit_or_repair_probe_only",
                    },
                    "decision_adjustment_effectiveness": {
                        "execution_hint": "cap_to_audit_or_repair_probe",
                    },
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [
            {
                "symbol": "NEARUSDT",
                "market": "spot",
                "side": "long",
                "entry_style": "waiting_entry",
                "metadata": {
                    "jue_wiki_decision_adjustment_resolution": (
                        "repair_probe with audit_or_repair_probe_only evidence"
                    )
                },
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_negative_wiki_decision_adjustment_action_note() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                    "evidence_grade": {
                        "status": "negative",
                        "basis": "decision_adjustment_effectiveness",
                        "instruction": "audit_or_repair_probe_only",
                    },
                    "decision_adjustment_effectiveness": {
                        "execution_hint": "cap_to_audit_or_repair_probe",
                    },
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [
            {
                "symbol": "NEARUSDT",
                "market": "spot",
                "side": "long",
                "entry_style": "waiting_entry",
                "metadata": {
                    "jue_wiki_decision_adjustment_resolution": (
                        "repair_probe adjustment not applied; "
                        "audit_or_repair_probe_only evidence unavailable; "
                        "cap_to_audit_or_repair_probe execution not performed"
                    )
                },
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_negative_wiki_decision_adjustment_response_resolution() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                    "evidence_grade": {
                        "status": "negative",
                        "basis": "decision_adjustment_effectiveness",
                        "instruction": "audit_or_repair_probe_only",
                    },
                    "decision_adjustment_effectiveness": {
                        "execution_hint": "cap_to_audit_or_repair_probe",
                    },
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"spot_orders_enabled": True},
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "NEARUSDT",
                        "resolution": "small_waiting_block",
                        "next_trigger": (
                            "repair_probe adjustment not applied; "
                            "audit_or_repair_probe_only evidence unavailable; "
                            "cap_to_audit_or_repair_probe execution not performed"
                        ),
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_unresolved_jue_wiki_validation_contract() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_validation_repair_contract": {
                "status": "repair_required",
                "requires_validation_repair_resolution": True,
                "contract_feedback_gap": {
                    "status": "missing_contract_outcomes",
                    "legacy_sample_count": 12,
                    "contract_sample_count": 0,
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "wait"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wait"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_ignores_kis_jue_wiki_validation_contract() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_validation_repair_contract": {
                "target_scope": "kis",
                "status": "repair_required",
                "requires_validation_repair_resolution": True,
                "contract_feedback_gap": {
                    "status": "missing_contract_outcomes",
                    "legacy_sample_count": 12,
                    "contract_sample_count": 0,
                },
            },
            "jue_wiki_contract_feedback_gap": {
                "target_scope": "kis",
                "status": "missing_contract_outcomes",
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "Binance judgment proceeds"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "Binance judgment proceeds"},
    )

    assert error == ""


def test_binance_manager_response_contract_accepts_prompt_resolution_phrase() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "REUSDT",
                        "resolution": (
                            "stage a concrete next trigger in "
                            "hold_decision.next_triggers"
                        ),
                        "next_trigger": (
                            "5m close reclaims resistance while spread is under 3bp"
                        ),
                        "evidence_gap": "candidate price geometry is missing",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "stage trigger for REUSDT"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_unaddressed_wiki_attention_plan() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "wait"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wait"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_unrelated_action_for_wiki_attention_plan() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "unrelated action"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {"lane": "spot:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated action"},
    )

    assert error == "validation_repair_resolution_missing_from_model"

    resolved_error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "attention metadata recorded"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_attention": {
                            "resolution": "action_metadata_records_repair_attention",
                            "component": "wiki_attention",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "attention metadata recorded"},
    )

    assert resolved_error == ""


def test_binance_manager_response_contract_accepts_wiki_attention_hold_trigger() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "repair attention staged"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "repair attention staged",
            "watch_symbols": ["NEARUSDT"],
            "data_gaps": ["orderbook_depth_missing"],
            "next_triggers": [
                {
                    "symbol": "NEARUSDT",
                    "condition": "depth improves while spread stays under 3bp",
                }
            ],
        },
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_action_ignoring_wiki_repair_pressure() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 4,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "unrelated create"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated create"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_action_ignoring_wiki_repair_action_batches() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "status": "active",
                "action_batches": [
                    {
                        "scope": "binance",
                        "action_type": "refresh_symbol_research",
                        "count": 7,
                        "symbols": ["NEARUSDT", "LTCUSDT"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "unrelated create"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated create"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_ignores_kis_scoped_wiki_repair_contract() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "target_scope": "kis",
            "repair_priority_count": 9,
            "top_priorities": [
                {
                    "scope": "kis",
                    "source_id": "repair:financials:kis:005930",
                    "symbols": ["005930"],
                }
            ],
            "action_batches": [
                {
                    "scope": "kis",
                    "action_type": "refresh_symbol_fundamentals",
                    "count": 5,
                    "symbols": ["005930", "000660"],
                }
            ],
            "repair_pressure_action_plan": {
                "status": "compressed",
                "omitted_priority_count": 4,
                "required_response": "mention omitted KIS repair pressure",
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"futures_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "long",
                "metadata": {"lane": "futures:long"},
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "Binance judgment proceeds"}},
        actions=actions,
        hold_decision={"summary": "Binance judgment proceeds"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "Binance judgment proceeds"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_repair_priority_count"] == 0
    assert diagnostics["jue_wiki_repair_action_batch_count"] == 0
    assert "unresolved_jue_wiki_repair_priorities" not in diagnostics["blocker_tags"]
    assert "unresolved_jue_wiki_repair_action_batches" not in diagnostics[
        "blocker_tags"
    ]


def test_binance_manager_response_contract_rejects_action_ignoring_wiki_selection_guidance() -> None:
    error = manager_response_contract_error(
        prompt={
            "memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_selection.binance."
                                "operational_memory_manager_contract_recovery"
                            ),
                            "selected_page_ids": ["binance.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "refresh_or_cross_check_selected_wiki_before_size_increase"
                                ),
                                "required_evidence": [
                                    "fresh_jue_wiki_context",
                                    "selection_audit_resolution",
                                    "live_cross_check",
                                ],
                                "cross_check_page_ids": ["binance.ops.manager_runs"],
                            },
                        }
                    ],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "freshness guidance ignored"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "freshness guidance ignored"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_action_ignoring_unavailable_wiki_context() -> None:
    error = manager_response_contract_error(
        prompt={
            "memory": {
                "jue_wiki": {
                    "status": "error",
                    "available": False,
                    "reason": "wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "wiki context ignored"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wiki context ignored"},
    )

    assert error == "wiki_context_gap_resolution_missing_from_model"


def test_binance_manager_response_contract_accepts_action_explaining_unavailable_wiki_context() -> None:
    error = manager_response_contract_error(
        prompt={
            "memory": {
                "jue_wiki": {
                    "status": "error",
                    "available": False,
                    "reason": "wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "wiki context handled"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "evidence_refs": ["binance.symbol.NEARUSDT"],
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_context_gap": (
                            "wiki_context_provider_timeout 때문에 위키 컨텍스트는 "
                            "사용하지 않고 live_cross_check, crypto_quant, "
                            "book, spread, funding으로 보완 확인"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wiki context handled"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_negative_unavailable_wiki_context_resolution() -> None:
    error = manager_response_contract_error(
        prompt={
            "memory": {
                "jue_wiki": {
                    "status": "error",
                    "available": False,
                    "reason": "wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "wiki context still unresolved"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_context_gap": (
                            "wiki_context_provider_timeout unresolved; "
                            "no live_cross_check, crypto_quant, book, spread, "
                            "or funding cross-check yet"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wiki context still unresolved"},
    )

    assert error == "wiki_context_gap_resolution_missing_from_model"


def test_binance_manager_ignores_kis_unavailable_wiki_context() -> None:
    error = manager_response_contract_error(
        prompt={
            "memory": {
                "jue_wiki": {
                    "target_scope": "kis",
                    "status": "error",
                    "available": False,
                    "reason": "kis_wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "Binance judgment proceeds"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "Binance judgment proceeds"},
    )

    assert error == ""


def test_binance_manager_response_contract_accepts_wiki_selection_guidance_resolution() -> None:
    error = manager_response_contract_error(
        prompt={
            "memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_selection.binance."
                                "operational_memory_manager_contract_recovery"
                            ),
                            "selected_page_ids": ["binance.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "refresh_or_cross_check_selected_wiki_before_size_increase"
                                ),
                                "required_evidence": [
                                    "fresh_jue_wiki_context",
                                    "selection_audit_resolution",
                                    "live_cross_check",
                                ],
                                "cross_check_page_ids": ["binance.ops.manager_runs"],
                            },
                        }
                    ],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "freshness guidance resolved"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context refreshed for "
                            "binance.ops.manager_runs; live_cross_check uses "
                            "book, spread, funding, and quant before size increase"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "freshness guidance resolved"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_action_ignoring_wiki_reference_memory() -> None:
    prompt = {
        "memory": {
            "jue_wiki_action_reference_memory": {
                "status": "available",
                "target_scope": "binance",
                "items": [
                    {
                        "policy_id": "jue_wiki_action_reference_gap.binance.missing",
                        "application_guidance": {
                            "status": "wiki_reference_repair_required",
                            "manager_instruction": (
                                "attach_jue_wiki_reference_or_explicitly_record_non_wiki_basis"
                            ),
                            "required_evidence": [
                                "jue_wiki_freshness_cross_check",
                                "jue_wiki_selection_resolution",
                                "live_cross_check",
                            ],
                        },
                    }
                ],
            }
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"futures_orders_enabled": True},
        },
    }
    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "wiki reference memory ignored"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wiki reference memory ignored"},
    )
    resolved_error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "wiki reference memory resolved"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap.binance.missing 보정: "
                            "fresh_jue_wiki_context 대신 live_cross_check, "
                            "book, spread, funding, crypto_quant 근거를 명시"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wiki reference memory resolved"},
    )

    assert error == "wiki_action_reference_resolution_missing_from_model"
    assert resolved_error == ""


def test_binance_manager_response_contract_prioritizes_wiki_reference_over_probe_action_gap() -> None:
    prompt = {
        "validation_repair": {
            "scope": "binance",
            "status": "needs_repair",
            "repair_item_count": 1,
        },
        "memory": {
            "jue_wiki_action_reference_memory": {
                "status": "available",
                "target_scope": "binance",
                "items": [
                    {
                        "policy_id": "jue_wiki_action_reference_gap.binance.missing",
                        "application_guidance": {
                            "status": "wiki_reference_repair_required",
                            "manager_instruction": (
                                "attach_jue_wiki_reference_or_explicitly_record_non_wiki_basis"
                            ),
                            "required_evidence": [
                                "jue_wiki_freshness_cross_check",
                                "live_cross_check",
                            ],
                        },
                    }
                ],
            }
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"futures_orders_enabled": True},
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "NEARUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "funding and depth refresh before probe",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "wiki_action_reference_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_action_using_degraded_wiki_without_repair_metadata() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": [
                                "application_repair_queue_pressure",
                                "repair_queue_open_count:2",
                            ],
                        },
                    }
                ]
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "degraded wiki still used"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "degraded wiki still used"},
    )

    assert error == "validation_repair_resolution_missing_from_model"

    resolved_error = manager_response_contract_error(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "degraded wiki cross-checked"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_resolution": {
                            "page_id": "binance.symbol.NEARUSDT",
                            "symbol": "NEARUSDT",
                            "resolution": (
                                "degraded wiki used only after orderbook depth, "
                                "spread, and sizing reduction cross-check"
                            ),
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "degraded wiki cross-checked"},
    )

    assert resolved_error == ""


def test_binance_manager_response_contract_requires_action_metadata_for_degraded_wiki_action() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "NEARUSDT",
                        "resolution": "small_waiting_block",
                        "next_trigger": (
                            "binance.symbol.NEARUSDT degraded wiki는 orderbook "
                            "depth, spread, funding으로 교차확인"
                        ),
                    }
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response-only repair resolution"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_action_ignoring_omitted_wiki_repair_batches() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "status": "active",
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "action_batch_total_count": 15,
                    "action_batch_visible_pressure_count": 0,
                    "action_batch_omitted_count": 15,
                    "action_batch_pressure_visibility_ratio": 0.0,
                    "required_response": (
                        "visible repair batches were compressed away; address omitted "
                        "repair pressure before sizing"
                    ),
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "unrelated create"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated create"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_accepts_action_with_wiki_repair_pressure_metadata() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 4,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "repair pressure handled"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_pressure": {
                            "source_id": "repair:financials:binance:ETHUSDT",
                            "resolution": "reduced_size_until_market_narrative_refreshed",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "repair pressure handled"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_vague_wiki_repair_metadata() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 4,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "repair pressure handled"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"jue_wiki_repair_pressure": "handled"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "repair pressure handled"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_repair_metadata_without_active_reference() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 4,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "repair pressure handled"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_pressure": (
                            "financials missing, reduced size until refresh"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "repair pressure handled"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_attention_metadata_bypassing_repair_reference() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                },
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "attention metadata recorded"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_attention": {
                            "resolution": "action_metadata_records_repair_attention",
                            "component": "wiki_attention",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "attention metadata recorded"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_response_resolution_without_active_reference() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "resolution": "small_waiting_block",
                        "next_trigger": "refresh funding/depth before sizing",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "repair response recorded"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_hold_trigger_without_wiki_reference() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                },
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "repair trigger staged"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "repair trigger staged",
            "watch_symbols": ["BTCUSDT"],
            "data_gaps": ["funding_missing"],
            "next_triggers": [
                {
                    "symbol": "BTCUSDT",
                    "condition": "refresh funding/depth before sizing",
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_allows_server_safety_gate_hold() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "blocked",
                "kill_switch": {"enabled": True},
                "execution": {"spot_orders_enabled": False},
            },
        },
        response={"hold_decision": {"summary": "kill switch"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "kill switch"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_action_pressure_without_trigger() -> None:
    error = manager_response_contract_error(
        prompt={
            "proactive_decision_pressure": {"status": "action_required"},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "wait"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wait"},
    )

    assert error == "hold_decision_missing_concrete_trigger"


def test_binance_manager_response_contract_rejects_upbit_action_masking_binance_gap() -> None:
    error = manager_response_contract_error(
        prompt={
            "candidates": [
                {"symbol": "ETHUSDT", "market": "futures", "side": "long"},
                {"symbol": "KRW-SOL", "market": "upbit_spot", "side": "long"},
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "pressure_source": "binance_activity_gap",
                "binance_market_activity_gap": {
                    "status": "stale_binance_entries",
                    "candidate_symbols": ["ETHUSDT"],
                    "candidate_markets": ["futures"],
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {
                    "futures_orders_enabled": True,
                    "upbit_orders_enabled": True,
                },
            },
        },
        response={"hold_decision": {"summary": "KRW-SOL만 신규 대기"}},
        actions={
            "create_blocks": [
                {"symbol": "KRW-SOL", "market": "upbit_spot", "side": "long"}
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KRW-SOL만 신규 대기"},
    )

    assert error == "binance_activity_gap_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_generic_hold_masking_binance_gap() -> None:
    error = manager_response_contract_error(
        prompt={
            "proactive_decision_pressure": {
                "status": "action_required",
                "pressure_source": "binance_activity_gap",
                "binance_market_activity_gap": {
                    "status": "stale_binance_entries",
                    "candidate_symbols": ["ETHUSDT"],
                    "candidate_markets": ["futures"],
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "시장 대기"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "시장 대기",
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "돌파 후 재검토",
                    "reason": "generic market caution",
                }
            ],
        },
    )

    assert error == "binance_activity_gap_resolution_missing_from_model"


def test_binance_manager_response_contract_allows_binance_gap_rejection_with_trigger() -> None:
    error = manager_response_contract_error(
        prompt={
            "proactive_decision_pressure": {
                "status": "action_required",
                "pressure_source": "binance_activity_gap",
                "binance_market_activity_gap": {
                    "status": "stale_binance_entries",
                    "candidate_symbols": ["ETHUSDT"],
                    "candidate_markets": ["futures"],
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "hold_decision": {"summary": "ETHUSDT는 펀딩/호가 확인 후 대기"},
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ETHUSDT",
                        "market": "futures",
                        "resolution": "safety_gate_defer",
                        "evidence_gap": "funding and order book depth not fresh",
                        "next_trigger": "funding <= 0.01%, spread <= 12bps",
                    }
                ]
            },
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "ETHUSDT는 펀딩/호가 확인 후 대기",
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "funding <= 0.01%, spread <= 12bps",
                    "reason": "funding and order book depth not fresh",
                }
            ],
        },
    )

    assert error == ""


def test_binance_manager_response_contract_allows_advertised_binance_gap_resolution_aliases() -> None:
    base_prompt = {
        "proactive_decision_pressure": {
            "status": "action_required",
            "pressure_source": "binance_activity_gap",
            "binance_market_activity_gap": {
                "status": "stale_binance_entries",
                "candidate_symbols": ["ETHUSDT"],
                "candidate_markets": ["futures"],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"futures_orders_enabled": True},
        },
    }

    for resolution in ("explicit_reject_with_price_reason", "defer_due_to_safety_gate"):
        error = manager_response_contract_error(
            prompt=base_prompt,
            response={
                "hold_decision": {"summary": "ETHUSDT는 호가/펀딩 확인 후 대기"},
                "validation_repair_resolution": {
                    "resolved_candidates": [
                        {
                            "symbol": "ETHUSDT",
                            "market": "futures",
                            "resolution": resolution,
                            "evidence_gap": "funding and order book depth not fresh",
                            "next_trigger": "funding <= 0.01%, spread <= 12bps",
                        }
                    ]
                },
            },
            actions={
                "create_blocks": [],
                "update_blocks": [],
                "close_blocks": [],
                "pause_blocks": [],
            },
            hold_decision={
                "summary": "ETHUSDT는 호가/펀딩 확인 후 대기",
                "next_triggers": [
                    {
                        "symbol": "ETHUSDT",
                        "condition": "funding <= 0.01%, spread <= 12bps",
                        "reason": "funding and order book depth not fresh",
                    }
                ],
            },
        )

        assert error == ""


def test_binance_manager_response_contract_allows_binance_gap_probe_action() -> None:
    error = manager_response_contract_error(
        prompt={
            "candidates": [
                {"symbol": "ETHUSDT", "market": "futures", "side": "long"},
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "pressure_source": "binance_activity_gap",
                "binance_market_activity_gap": {
                    "status": "stale_binance_entries",
                    "candidate_symbols": ["ETHUSDT"],
                    "candidate_markets": ["futures"],
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={"hold_decision": {"summary": "ETHUSDT small probe"}},
        actions={
            "create_blocks": [
                {"symbol": "ETHUSDT", "market": "futures", "side": "long"}
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "ETHUSDT small probe"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_ignoring_repairable_probe_design() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "btc-waiting",
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "original stop risk exceeded validation repair max "
                            "stop cap"
                        ),
                        "next_trigger": "tightened stop later",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "ESPUSDT rejected because original stop was wide",
            "watch_symbols": ["ESPUSDT"],
            "next_triggers": [
                {
                    "symbol": "ESPUSDT",
                    "condition": "tightened stop later",
                    "reason": "original stop was wide",
                }
            ],
        },
    )

    assert error == "validation_repair_probe_design_ignored_from_model"


def test_binance_manager_response_contract_rejects_unrelated_action_masking_repairable_probe_rejection() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "original stop risk exceeded repair cap",
                        "next_trigger": "tightened stop later",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "btc-waiting",
                    "entry_price": 60_000,
                    "target_price": 58_000,
                    "stop_price": 61_000,
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_probe_design_ignored_from_model"


def test_binance_manager_response_contract_rejects_close_action_masking_repairable_probe_rejection() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "esp-live",
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "side": "short",
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "original stop risk exceeded repair cap",
                        "next_trigger": "tightened stop later",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [{"block_id": "esp-live", "reason": "risk off"}],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_probe_design_ignored_from_model"


def test_binance_manager_response_contract_rejects_metadata_only_update_masking_repairable_probe_rejection() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "esp-live",
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "side": "short",
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "original stop risk exceeded repair cap",
                        "next_trigger": "tightened stop later",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [{"block_id": "esp-live", "reason": "metadata only"}],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_probe_design_ignored_from_model"


def test_binance_manager_response_contract_allows_one_repair_probe_action_with_other_repairable_watch() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 2},
            "candidates": [
                {"symbol": "SENTUSDT", "market": "futures", "side": "short"}
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "SENTUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.013612,
                            "target_price": 0.012807,
                            "stop_price": 0.014005,
                            "reward_risk": 2.05,
                        },
                    },
                    {
                        "symbol": "HMSTRUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.000204,
                            "target_price": 0.00019,
                            "stop_price": 0.000210,
                            "reward_risk": 2.33,
                        },
                    },
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "SENTUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "SENTUSDT >= 0.013612 waiting short probe",
                    },
                    {
                        "symbol": "HMSTRUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "original stop risk exceeded validation repair max stop cap"
                        ),
                        "next_trigger": "tightened stop later",
                    },
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "SENTUSDT",
                    "market": "futures",
                    "side": "short",
                    "entry_style": "wait_for_price",
                    "entry_price": 0.013612,
                    "target_price": 0.012807,
                    "stop_price": 0.014005,
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "SENTUSDT repair probe action; HMSTRUSDT watched"},
    )

    assert error == ""


def test_binance_manager_response_contract_allows_response_probe_action_even_if_not_current_repairable_pressure() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 2},
            "candidates": [
                {"symbol": "SENTUSDT", "market": "futures", "side": "short"}
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "HMSTRUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.000204,
                            "target_price": 0.00019,
                            "stop_price": 0.000210,
                            "reward_risk": 2.33,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "SENTUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "SENTUSDT >= 0.013612 waiting short probe",
                    },
                    {
                        "symbol": "HMSTRUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "original stop risk exceeded validation repair max stop cap"
                        ),
                        "next_trigger": "tightened stop later",
                    },
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "SENTUSDT",
                    "market": "futures",
                    "side": "short",
                    "entry_style": "wait_for_price",
                    "entry_price": 0.013612,
                    "target_price": 0.012807,
                    "stop_price": 0.014005,
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "SENTUSDT repair probe action; HMSTRUSDT watched"},
    )

    assert error == ""


def test_binance_manager_response_contract_allows_repairable_probe_rejection_with_live_gate() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "live spread is 18bps and orderbook depth is below "
                            "the minimum probe-liquidity gate"
                        ),
                        "next_trigger": (
                            "reconsider the provided probe design when spread <= 10bps, "
                            "depth recovers, and funding is neutral"
                        ),
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "ESPUSDT repairable probe deferred by live liquidity gate",
            "watch_symbols": ["ESPUSDT"],
            "next_triggers": [
                {
                    "symbol": "ESPUSDT",
                    "condition": "spread <= 10bps and depth recovers",
                }
            ],
        },
    )

    assert error == ""


def test_binance_manager_response_contract_allows_repairable_probe_rejection_with_pattern_or_authority_gate() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    },
                    {
                        "symbol": "SOLUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 76.553,
                            "target_price": 77.777,
                            "stop_price": 74.25641,
                            "reward_risk": 0.532953,
                        },
                    },
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "pattern prior missing and live_authority "
                            "validation_probe blocks immediate execution"
                        ),
                        "next_trigger": (
                            "pattern prior recovers and live_authority becomes "
                            "qualified"
                        ),
                    },
                    {
                        "symbol": "SOLUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "lane cooldown active until validation recovers",
                        "next_trigger": "cooldown clears and RR >= 2.0",
                    },
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "Binance probes deferred by current pattern and authority gates",
            "watch_symbols": ["ESPUSDT", "SOLUSDT"],
            "next_triggers": [
                {
                    "symbol": "ESPUSDT",
                    "condition": "pattern prior recovers and live_authority qualifies",
                },
                {
                    "symbol": "SOLUSDT",
                    "condition": "cooldown clears and RR >= 2.0",
                },
            ],
        },
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_pattern_not_qualified_as_live_gate() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "pattern prior is not qualified yet",
                        "next_trigger": "pattern prior qualifies later",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "ESPUSDT pattern not qualified",
            "watch_symbols": ["ESPUSDT"],
            "next_triggers": [
                {
                    "symbol": "ESPUSDT",
                    "condition": "pattern prior qualifies later",
                }
            ],
        },
    )

    assert error == "validation_repair_probe_design_ignored_from_model"


def test_binance_manager_response_contract_rejects_safety_gate_defer_that_ignores_repairable_probe_design() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "safety_gate_defer",
                        "evidence_gap": "original stop risk exceeded repair cap",
                        "next_trigger": "tightened stop later",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "ESPUSDT defer because original stop was wide",
            "watch_symbols": ["ESPUSDT"],
            "next_triggers": [
                {"symbol": "ESPUSDT", "condition": "tightened stop later"}
            ],
        },
    )

    assert error == "validation_repair_probe_design_ignored_from_model"


def test_binance_manager_response_contract_rejects_wrong_market_action_masking_repairable_probe_rejection() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "esp-spot-waiting",
                    "symbol": "ESPUSDT",
                    "market": "spot",
                    "side": "long",
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "side": "short",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.07156,
                            "target_price": 0.06397,
                            "stop_price": 0.0737068,
                            "reward_risk": 3.535495,
                        },
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {"spot_orders_enabled": True, "futures_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "original stop risk exceeded repair cap",
                        "next_trigger": "tightened stop later",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "esp-spot-waiting",
                    "entry_price": 0.071,
                    "target_price": 0.073,
                    "stop_price": 0.07,
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_probe_design_ignored_from_model"


def test_binance_manager_response_contract_rejects_repair_probe_below_min_executable_qty() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "CHIPUSDT <= 0.033983 waiting long probe",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                    "qty": 63.75,
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_min_executable_qty_missing_from_model"


def test_binance_manager_response_contract_rejects_repair_probe_below_min_executable_notional() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "CHIPUSDT <= 0.033983 waiting long probe",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                    "quote_budget_usdt": 5.0,
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_min_executable_qty_missing_from_model"


def test_binance_manager_response_contract_rejects_block_id_update_below_min_executable_qty() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "chip-waiting",
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "updated_price_geometry",
                        "next_trigger": "tightened active waiting probe geometry",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "chip-waiting",
                    "qty": 63.75,
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_min_executable_qty_missing_from_model"


def test_binance_manager_response_contract_allows_block_id_update_at_min_executable_notional() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "chip-waiting",
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "updated_price_geometry",
                        "next_trigger": "tightened active waiting probe geometry",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "chip-waiting",
                    "quote_budget_usdt": 20.0,
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_response_contract_allows_block_id_update_with_existing_executable_qty() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "chip-waiting",
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                    "qty_initial": 600.0,
                    "entry_price": 0.033983,
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "updated_price_geometry",
                        "next_trigger": "tightened active waiting probe geometry",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "chip-waiting",
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_response_contract_allows_block_id_update_to_resolve_executable_repair() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "chip-waiting",
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                    "qty_initial": 600.0,
                    "entry_price": 0.033983,
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "tightened active waiting probe geometry",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "chip-waiting",
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_response_contract_ignores_wrong_market_action_for_min_executable_floor() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "chip-spot-waiting",
                    "symbol": "CHIPUSDT",
                    "market": "spot",
                    "side": "long",
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "live spread is 18bps and orderbook depth is thin",
                        "next_trigger": "spread <= 10bps and depth recovers",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "chip-spot-waiting",
                    "qty": 1.0,
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_updated_price_geometry_without_update_action() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "blocks": [
                {
                    "block_id": "chip-waiting",
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                    "qty_initial": 600.0,
                    "entry_price": 0.033983,
                }
            ],
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "updated_price_geometry",
                        "next_trigger": "tightened active waiting probe geometry",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "CHIPUSDT geometry updated",
            "watch_symbols": ["CHIPUSDT"],
            "next_triggers": [
                {"symbol": "CHIPUSDT", "condition": "tightened geometry is ready"}
            ],
        },
    )

    assert error == "validation_repair_action_missing_from_model"


def test_binance_manager_response_contract_allows_repair_probe_at_min_executable_qty() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "CHIPUSDT <= 0.033983 waiting long probe",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                    "qty": 588.52955949,
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_response_contract_allows_repair_probe_at_min_executable_notional() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "proactive_decision_pressure": {
                "status": "action_required",
                "top_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "side": "long",
                        "validation_repair_probe_design": {
                            "resolution": "probe_waiting_block",
                            "entry_price": 0.033983,
                            "target_price": 0.035219,
                            "stop_price": 0.03296351,
                            "reward_risk": 2.0,
                            "min_executable_notional_usdt": 20.0,
                            "min_executable_qty": 588.52955949,
                        },
                    }
                ],
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "CHIPUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "next_trigger": "CHIPUSDT <= 0.033983 waiting long probe",
                    }
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "CHIPUSDT",
                    "market": "futures",
                    "side": "long",
                    "quote_budget_usdt": 20.0,
                    "entry_price": 0.033983,
                    "target_price": 0.035219,
                    "stop_price": 0.03296351,
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_ignores_kis_scoped_action_pressure() -> None:
    prompt = {
        "proactive_decision_pressure": {
            "target_scope": "kis",
            "status": "action_required",
        },
        "jue_wiki_action_pressure_contract": {
            "target_scope": "kis",
            "status": "active",
            "page_ids": ["kis.ops.action_pressure"],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"futures_orders_enabled": True},
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "Binance judgment proceeds"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "Binance judgment proceeds"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "Binance judgment proceeds"},
    )

    assert error == ""
    assert "unresolved_proactive_pressure" not in diagnostics["blocker_tags"]
    assert diagnostics["proactive_pressure_status"] is None


def test_binance_prompt_budget_emergency_keeps_native_manager_schema() -> None:
    prompt = build_binance_manager_prompt_payload(
        allowed_actions=["create_blocks", "update_blocks", "close_blocks", "pause_blocks"],
        language_policy={},
        manager_lanes=["spot:long", "futures:long", "futures:short", "upbit_spot:long"],
        account={"raw": "A" * 80_000},
        growth_target={},
        growth_governor={},
        growth_unlock={},
        risk_guard={},
        execution_gate={},
        memory_context={"raw": "M" * 80_000},
        decision_packet_v2={"raw": "D" * 80_000},
        decision_packet={},
        candidate_policy_impacts={},
        validation_repair={},
        crypto_market_pulse={},
        raw_context_refs={"raw": "R" * 80_000},
        recent_performance={},
        performance={},
        entry_gate_policy={},
        live_authority={"raw": "L" * 80_000},
        lane_balance={},
        candidate_generation={},
        candidates=[
            {"symbol": f"T{idx:02d}USDT", "reason_md": "C" * 20_000}
            for idx in range(20)
        ],
        universe=[],
        market_universe={},
        blocks=[],
    )
    prompt["unbounded_extra_context"] = "X" * 240_000

    finalize_prompt_budget(
        prompt,
        target_chars=20_000,
        warn_chars=25_000,
        max_chars=30_000,
    )

    assert prompt["prompt_budget"]["over_max"] is False
    assert "native_output_schema" in prompt
    assert prompt["native_output_schema"] == prompt["output_schema"]
    assert "lane_review" in prompt["native_output_schema"]


def test_binance_manager_prompt_storage_emergency_keeps_jue_wiki_snapshot() -> None:
    prompt = {
        "task": "Build executable Binance blocks." + ("X" * 8_000),
        "account": {"futures_cash_usdt": 123.45},
        "candidates": [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "long",
                "reason_md": "candidate evidence " + ("C" * 2_000),
            }
        ],
        "jue_wiki": {
            "status": "ok",
            "selection_run_id": "selection:binance-test",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "title": "BTCUSDT trading memory",
                    "summary": "Respect the BTCUSDT liquidity playbook.",
                    "content": "historical lesson " + ("W" * 8_000),
                    "evidence_refs": ["block:btc-1", "research:btc-2"],
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selection_run_id": "selection:binance-test",
            "prompt_mode": "assist",
            "selected_page_ids": ["binance.symbol.BTCUSDT"],
            "selection_audit": {
                "selected_page_count": 2,
                "reason_counts": {
                    "scope_match:binance": 2,
                    "operational_memory:manager_contract_recovery": 1,
                },
                "penalty_counts": {"freshness:stale": 1},
                "top_pages": [
                    {
                        "page_id": "binance.ops.manager_runs",
                        "rank": 1,
                        "score": 139.0,
                        "selection_reasons": [
                            "scope_match:binance",
                            "operational_memory:manager_contract_recovery",
                        ],
                    }
                ],
            },
        },
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "partial",
            "unsummarized_symbols": ["ETHUSDT", "SOLUSDT"],
            "required_action": "live cross-check required before confidence",
        },
        "jue_wiki_repair_contract": {
            "status": "active",
            "top_priorities": [
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "repair_action": "widen stop and require clean spread",
                }
            ],
        },
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 25_000},
    }

    stored = compact_manager_storage_payload(
        prompt,
        limit=4_000,
        label="binance_manager_prompt",
    )

    assert stored["_storage_compaction"]["emergency"] is True
    assert stored["jue_wiki"]["pages"][0]["page_id"] == "binance.symbol.BTCUSDT"
    assert "Respect the BTCUSDT liquidity playbook." in json.dumps(
        stored["jue_wiki"],
        ensure_ascii=False,
    )
    assert stored["jue_wiki_application"]["selected_page_ids"] == [
        "binance.symbol.BTCUSDT"
    ]
    assert stored["jue_wiki_application"]["selection_audit"]["reason_counts"][
        "operational_memory:manager_contract_recovery"
    ] == 1
    assert stored["jue_wiki_application"]["selection_audit"]["top_pages"][0][
        "page_id"
    ] == "binance.ops.manager_runs"
    assert stored["jue_wiki_requested_symbol_coverage"]["status"] == "partial"
    assert stored["jue_wiki_requested_symbol_coverage"]["unsummarized_symbols"] == [
        "ETHUSDT",
        "SOLUSDT",
    ]
    assert stored["jue_wiki_repair_contract"]["top_priorities"][0]["page_id"] == (
        "binance.symbol.BTCUSDT"
    )


def test_binance_manager_prompt_storage_keeps_wiki_trust_profile_without_raw_noise() -> None:
    prompt = {
        "task": "Build executable Binance blocks." + ("X" * 16_000),
        "account": {"futures_cash_usdt": 123.45},
        "candidates": [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "long",
                "reason_md": "candidate evidence " + ("C" * 3_000),
            }
        ],
        "jue_wiki_application": {
            "status": "ok",
            "selection_run_id": "selection:binance-trust",
            "prompt_mode": "assist",
            "selected_page_ids": ["binance.symbol.BTCUSDT"],
            "trust_profile": {
                "trust_level": "medium",
                "authority": "supporting_evidence",
                "decision_use": (
                    "use selected wiki pages as supporting evidence alongside "
                    "live spread, funding, liquidation distance, and account risk"
                ),
                "usage_contract": {
                    "decision_role": "supporting_evidence",
                    "requires_live_cross_check": True,
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "liquidation_distance",
                        "account_state",
                        "risk_gate",
                    ],
                    "raw_blob": "DROP_ME",
                },
                "raw_debug": "DROP_ME",
            },
        },
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 80_000},
    }

    stored = compact_manager_storage_payload(
        prompt,
        limit=2_500,
        label="binance_manager_prompt",
    )

    assert stored["_storage_compaction"]["emergency"] is True
    assert stored["jue_wiki_application"]["status"] == "ok"
    assert stored["jue_wiki_application"]["selection_run_id"] == (
        "selection:binance-trust"
    )
    assert stored["jue_wiki_application"]["selected_page_ids"] == [
        "binance.symbol.BTCUSDT"
    ]
    assert stored["jue_wiki_application"]["trust_profile"] == {
        "trust_level": "medium",
        "authority": "supporting_evidence",
        "decision_use": (
            "use selected wiki pages as supporting evidence alongside live "
            "spread, funding, liquidation distance, and account risk"
        ),
        "usage_contract": {
            "decision_role": "supporting_evidence",
            "requires_live_cross_check": True,
            "standalone_trade_authority": False,
            "required_cross_checks": [
                "live_spread",
                "funding",
                "liquidation_distance",
                "account_state",
            ],
        },
    }
    assert "DROP_ME" not in json.dumps(stored, ensure_ascii=False)


def test_binance_manager_prompt_storage_emergency_keeps_validation_repair_snapshot() -> None:
    prompt = {
        "task": "Build executable Binance blocks." + ("X" * 8_000),
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 30_000},
        "validation_repair": {
            "version": "validation_repair_prompt_v1",
            "scope": "binance",
            "status": "risk_repair",
            "repair_item_count": 2,
            "constraint_count": 1,
            "repair_backlog": [
                {
                    "discipline_id": "cost_simulation",
                    "repair_action_id": "validation_repair.cost_evidence_repair",
                    "entry_bias": "waiting_entry_until_cost_edge_clean",
                    "blocks_new_entries": "scale_up_and_unvalidated_immediate_entries",
                    "risk_budget_multiplier": 0.25,
                }
            ],
            "block_design_constraints": [
                {
                    "discipline_id": "kelly_sizing",
                    "sizing_policy": "fractional_kelly_probe_only",
                    "min_reward_risk": 2.0,
                }
            ],
        },
        "candidates": [{"symbol": "BTCUSDT", "notes": "C" * 4_000}],
    }

    stored = compact_manager_storage_payload(
        prompt,
        limit=4_000,
        label="binance_manager_prompt",
    )

    assert stored["_storage_compaction"]["emergency"] is True
    assert stored["validation_repair"]["scope"] == "binance"
    assert stored["validation_repair"]["repair_item_count"] == 2
    assert "cost_simulation" in stored["validation_repair"]["discipline_ids"]
    assert stored["validation_repair"]["hard_filter"] is False


def test_compact_manager_account_for_prompt_summarizes_assets_risk_and_errors() -> None:
    account = {
        "status": " degraded ",
        "spot_cash_usdt": "1,000.50",
        "futures_cash_usdt": 250.25,
        "upbit_cash_krw": "123,000",
        "upbit_cash_usdt": "90.2",
        "cash_usdt": "1250.75",
        "total_value_usdt": "1500.25",
        "spot_assets": [
            {"asset": "USDT", "kind": "cash", "qty": 1000, "raw": "DROP_ME" * 100},
            {"asset": "INJ", "kind": "position", "qty": "1.25", "value_usdt": "8.5"},
        ],
        "upbit_spot_assets": [
            {"asset": "BTC", "symbol": "KRW-BTC", "qty": "0.01", "value_krw": 950000}
        ],
        "futures_assets": [{"asset": "USDT-FUT", "kind": "cash", "balance": 250.25}],
        "futures_position_risk": [
            {"symbol": f"ZERO{i}USDT", "positionAmt": 0, "unRealizedProfit": 0}
            for i in range(3)
        ]
        + [
            {
                "symbol": "SUIUSDT",
                "positionAmt": "-3.5",
                "entryPrice": "3.2",
                "markPrice": "3.1",
                "liquidationPrice": "4.2",
                "leverage": "2",
                "marginType": "isolated",
                "unRealizedProfit": "0.35",
                "raw": {"large": "DROP_ME" * 100},
            }
        ]
        + [
            {
                "symbol": "SUIDUSTUSDT",
                "positionAmt": "-0.1",
                "entryPrice": "0.7171",
                "markPrice": "0.7144",
                "liquidationPrice": "1.4209",
                "leverage": "1",
                "marginType": "isolated",
                "unRealizedProfit": "0.0002",
                "raw": {"large": "DROP_ME" * 100},
            }
        ],
        "errors": [
            {"source": "binance futures", "error_message": " timeout " * 80},
            {"source": "", "error_message": ""},
        ],
    }

    compact = compact_manager_account_for_prompt(account)
    rendered = json.dumps(compact, ensure_ascii=False)

    assert compact["status"] == "degraded"
    assert compact["spot_cash_usdt"] == 1000.5
    assert compact["futures_cash_usdt"] == 250.25
    assert compact["upbit_cash_krw"] == 123000.0
    assert compact["upbit_cash_usdt"] == 90.2
    assert compact["cash_usdt"] == 1250.75
    assert compact["total_value_usdt"] == 1500.25
    assert compact["spot_assets"][1]["symbol"] == "INJUSDT"
    assert compact["upbit_spot_assets"][0]["symbol"] == "KRW-BTC"
    assert compact["futures_position_risk"] == [
        {
            "symbol": "SUIUSDT",
            "position_amt": -3.5,
            "entry_price": 3.2,
            "mark_price": 3.1,
            "liquidation_price": 4.2,
            "leverage": 2,
            "margin_type": "isolated",
            "unrealized_profit": 0.35,
            "position_notional_usdt": 10.85,
            "management_status": "open_position",
        },
        {
            "symbol": "SUIDUSTUSDT",
            "position_amt": -0.1,
            "entry_price": 0.7171,
            "mark_price": 0.7144,
            "liquidation_price": 1.4209,
            "leverage": 1,
            "margin_type": "isolated",
            "unrealized_profit": 0.0002,
            "position_notional_usdt": 0.07144,
            "management_status": "dust_below_min_notional",
            "min_manageable_notional_usdt": 5.0,
        }
    ]
    assert compact["futures_position_risk_summary"] == {
        "total_count": 5,
        "nonzero_count": 2,
        "visible_count": 2,
        "omitted_zero_count": 3,
        "dust_count": 1,
    }
    assert len(compact["errors"]) == 2
    assert len(compact["errors"][0]["error_message"]) <= 240
    assert "raw" not in rendered
    assert "DROP_ME" not in rendered


def test_build_binance_manager_prompt_payload_preserves_core_policy_contract() -> None:
    language_policy = {
        "internal_reasoning_language": "en-US",
        "operator_display_language": "ko-KR",
        "applies_to": ["hold_decision", "create_blocks.thesis"],
    }

    prompt = build_binance_manager_prompt_payload(
        allowed_actions=["create_blocks", "update_blocks", "close_blocks", "pause_blocks"],
        language_policy=language_policy,
        manager_lanes=list(BINANCE_MANAGER_LANES),
        account={"status": "ok"},
        growth_target={"status": "ok"},
        growth_governor={"mode": "steady"},
        growth_unlock={"phase": "observe"},
        risk_guard={"allow_new_entries": True},
        execution_gate={
            "status": "ok",
            "kill_switch": {"enabled": False},
            "cash_available": {"spot_cash_usdt": 100.0},
        },
        memory_context={"status": "ok"},
        decision_packet_v2={
            "version": "decision_packet_v2",
            "schema": {"target_scope": "binance"},
            "account_pressure": {"cash": 100},
            "risk_budget": {"status": "ok"},
        },
        decision_packet={"target_scope": "binance"},
        candidate_policy_impacts={"_global": []},
        validation_repair={"status": "ok"},
        crypto_market_pulse={"regime_brief": "risk-on"},
        raw_context_refs={"crypto_research": {"status": "ok"}},
        recent_performance={"sample_count": 1},
        performance={"sample_count": 5},
        entry_gate_policy={"status": "ok"},
        live_authority={"status": "ok"},
        lane_balance={"review_required": False},
        candidate_generation={"candidate_count": 1},
        candidates=[{"symbol": "BTCUSDT", "market": "spot", "side": "long"}],
        universe=["BTCUSDT"],
        market_universe={"spot": ["BTCUSDT"], "futures": [], "upbit_spot": []},
        blocks=[],
    )

    assert prompt["task"].startswith("Manage independent crypto trading blocks")
    contract_ids = [
        row.get("contract_id")
        for row in prompt["jue_workflow"].get("contracts", [])
        if isinstance(row, dict)
    ]
    assert "jue_wiki_usage_contract_resolution" in contract_ids
    provenance = manager_run_workflow_provenance(prompt)
    assert "jue_wiki_usage_contract_resolution" in json.loads(
        provenance["contract_ids_json"]
    )
    assert prompt["policy"]["allowed_actions"] == [
        "adopt_existing_blocks",
        "close_blocks",
        "create_blocks",
        "pause_blocks",
        "update_blocks",
    ]
    memory_scope_policy = prompt["policy"]["memory_scope_policy"]
    assert "translated_policy_context" in memory_scope_policy
    assert "translated lessons" in memory_scope_policy
    assert "direct crypto rules" in memory_scope_policy
    assert "available_count" in memory_scope_policy
    assert "omitted_count" in memory_scope_policy
    assert "source_scope_counts" in memory_scope_policy
    assert "visible translated lessons are only a sample" in memory_scope_policy
    period_memory_policy = prompt["policy"]["period_memory_coverage_policy"]
    assert period_memory_policy["source"] == "memory.period_memory_coverage"
    assert period_memory_policy["applies_to_scope"] == "binance"
    assert period_memory_policy["missing_coverage_decision_effect"] == [
        "record missing weekly/monthly review or replay in hold_decision.data_gaps",
        "reduce action confidence or explain why current live evidence overrides the gap",
        "include the gap in risk_note or action metadata for affected block actions",
        (
            "when policy_rules or validation_repair require "
            "metadata_contract_audit_resolution, populate "
            "metadata_contract_audit_resolution on the affected action"
        ),
        (
            "when validation_repair provides metadata_contract_repair_note, copy "
            "that note into metadata_contract_repair_note on the affected action"
        ),
    ]
    assert period_memory_policy["confidence_rule"] == (
        "Do not use absent period memory as proof that a setup is clean."
    )
    wiki_selection_policy = prompt["policy"]["jue_wiki_selection_memory_policy"]
    assert wiki_selection_policy["source"] == "memory.jue_wiki_selection_memory"
    assert wiki_selection_policy["freshness_guidance_effect"] == [
        "refresh or cross-check selected Wiki pages before size increase",
        "record jue_wiki_selection_resolution or jue_wiki_freshness_cross_check on affected actions",
        "use fresh_jue_wiki_context, selection_audit_resolution, and live_cross_check as required evidence",
    ]
    wiki_context_gap_policy = prompt["policy"]["jue_wiki_context_gap_memory_policy"]
    assert wiki_context_gap_policy["source"] == "memory.jue_wiki_context_gap_memory"
    assert wiki_context_gap_policy["gap_guidance_effect"] == [
        "verify Wiki context availability before high-confidence action",
        "record jue_wiki_context_gap on affected actions when Wiki remains unavailable",
        "use fresh_jue_wiki_context or live_cross_check as required evidence",
    ]
    wiki_reference_policy = prompt["policy"][
        "jue_wiki_action_reference_memory_policy"
    ]
    assert wiki_reference_policy["source"] == (
        "memory.jue_wiki_action_reference_memory"
    )
    assert wiki_reference_policy["reference_guidance_effect"] == [
        "attach jue_wiki_freshness_cross_check or jue_wiki_selection_resolution when selected Wiki memory influences an action",
        "if an action does not use Wiki memory, record the book/quant/research basis that overrode Wiki memory",
        "use live_cross_check before allowing high confidence without an action-level Wiki reference",
    ]
    wiki_usage_policy = prompt["policy"]["jue_wiki_usage_contract_policy"]
    assert wiki_usage_policy["source"] == "jue_wiki_application.trust_profile"
    assert wiki_usage_policy["memory_source"] == "memory.jue_wiki_usage_contract_memory"
    assert wiki_usage_policy["memory_guidance_effect"] == [
        (
            "apply usage-contract reflections as action-level evidence requirements, "
            "not as standalone trade authority"
        ),
        (
            "when memory requires jue_wiki_usage_contract_resolution, every affected "
            "action must name the live cross-check that confirmed, reduced, or "
            "overrode the Wiki memory prior"
        ),
    ]
    assert wiki_usage_policy["standalone_trade_authority"] is False
    assert wiki_usage_policy["required_action_metadata"] == (
        "jue_wiki_usage_contract_resolution"
    )
    assert "live_spread" in wiki_usage_policy["required_cross_checks"]
    assert "funding" in wiki_usage_policy["required_cross_checks"]
    assert "liquidation_distance" in wiki_usage_policy["required_cross_checks"]
    for action_name in (
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        action_schema = prompt["output_schema"][action_name][0]
        assert "period_memory_coverage_gap" in action_schema
        assert "period_memory_override_reason" in action_schema
        assert "jue_wiki_selection_resolution" in action_schema
        assert "jue_wiki_freshness_cross_check" in action_schema
        assert "jue_wiki_context_gap" in action_schema
        assert "jue_wiki_reference_basis" in action_schema
        assert "jue_wiki_usage_contract_resolution" in action_schema
        usage_contract_schema = action_schema[
            "jue_wiki_usage_contract_resolution"
        ]
        assert "required when" in usage_contract_schema
        assert "memory.jue_wiki_usage_contract_memory" in usage_contract_schema
        assert "jue_wiki_application.trust_profile.usage_contract" in (
            usage_contract_schema
        )
        assert "metadata_contract_audit_resolution" in action_schema
        assert "metadata_contract_repair_note" in action_schema
        assert "weekly/monthly review or replay" in action_schema[
            "period_memory_coverage_gap"
        ]
        assert "current live evidence overrides" in action_schema[
            "period_memory_override_reason"
        ]
        assert "metadata contract audit" in action_schema[
            "metadata_contract_audit_resolution"
        ]
        assert "copy the compact repair note" in action_schema[
            "metadata_contract_repair_note"
        ]
        if action_name == "create_blocks":
            assert "active futures" in action_schema["liquidation_price"]
            assert "proposed wait_for_price" in action_schema["liquidation_price"]
    assert "same symbol" in prompt["policy"]["multi_block_policy"]
    assert "futures:short" in prompt["policy"]["lane_balance_policy"]
    assert "edge_rebuild" in prompt["policy"]["growth_governor_policy"]
    assert prompt["language_policy"] == language_policy
    assert prompt["output_language_policy"] == language_policy
    assert prompt["execution_gate"]["status"] == "ok"
    assert prompt["execution_gate"]["kill_switch"]["enabled"] is False
    assert "execution_gate" in prompt["decision_inputs"]
    assert prompt["canonical_decision_packet"]["target_scope"] == "binance"
    assert prompt["canonical_decision_packet"]["packet_identity"] == {
        "version": "canonical_decision_packet_prompt_v1",
        "schema_version": "decision_packet_v2",
        "target_scope": "binance",
        "producer": "tradecraft.services.jue_decision_packet.build_decision_packet",
        "primary_prompt_key": "decision_packet_v2",
        "legacy_prompt_key": "decision_packet",
        "required_section_count": len(DECISION_PACKET_REQUIRED_SECTIONS),
        "present_section_count": 2,
        "legacy_context_mode": "policy_metadata_only",
    }
    assert prompt["canonical_decision_packet"]["primary_prompt_key"] == (
        "decision_packet_v2"
    )
    assert "decision_packet_v2 is the canonical" in prompt["decision_packet_policy"]
    assert "legacy policy metadata" in prompt["decision_packet_policy"]
    assert prompt["canonical_decision_packet"]["present_sections"] == [
        "account_pressure",
        "risk_budget",
    ]
    assert prompt["canonical_decision_packet"]["decision_packet_status"] == "invalid"
    assert "decision_packet_missing_sections" in prompt["canonical_decision_packet"][
        "decision_warnings"
    ]
    assert "canonical_decision_packet" in prompt["decision_inputs"]
    assert "decision_packet_v2" in prompt["decision_inputs"]
    assert "crypto_market_pulse" in prompt["decision_inputs"]
    assert prompt["output_schema"]["create_blocks"][0]["market"].startswith(
        "spot|futures|upbit_spot"
    )
    assert prompt["output_schema"]["lane_review"]["lanes_reviewed"] == list(
        BINANCE_MANAGER_LANES
    )
    assert "mandatory" in prompt["output_schema"]["lane_review"]["required"]
    assert "no actions" in prompt["output_schema"]["lane_review"]["required"]
    assert prompt["critical_response_contract"]["lane_review"]["required"] is True
    assert "lane_review" in prompt["critical_response_contract"]["required_top_level_keys"]
    assert "validation_repair_resolution" in prompt[
        "critical_response_contract"
    ]["required_top_level_keys"]
    assert prompt["critical_response_contract"]["on_hold_only"]["lane_review_required"] is True
    assert prompt["critical_response_contract"]["validation_repair_resolution"][
        "blanket_hold_allowed"
    ] is False
    assert "validation_repair_action_missing_from_model" in prompt[
        "critical_response_contract"
    ]["validation_repair_resolution"]["action_required_for_resolutions"]["error"]
    assert "validation_repair_probe_design_ignored_from_model" in prompt[
        "critical_response_contract"
    ]["validation_repair_resolution"]["repairable_probe_design"]["error"]
    assert "next_trigger alone does not satisfy this rejection evidence" in prompt[
        "critical_response_contract"
    ]["validation_repair_resolution"]["repairable_probe_design"]["error"]
    assert "pattern prior, research_only, immediate_block_allowed" in prompt[
        "critical_response_contract"
    ]["validation_repair_resolution"]["repairable_probe_design"]["error"]
    assert "validation_repair_min_executable_qty_missing_from_model" in prompt[
        "critical_response_contract"
    ]["validation_repair_resolution"]["repairable_probe_design"][
        "min_executable_qty_error"
    ]
    assert "blanket reason to stop all exploration" in prompt["policy"][
        "validation_repair_response_policy"
    ]
    assert prompt["native_thread_mode"] == "daily"
    assert prompt["native_thread_key"] == "binance:block_manager:{date}"


def test_compact_account_asset_rows_limits_and_omits_raw_fields() -> None:
    rows = [
        {"asset": "BTC", "qty": "0.1", "value_usdt": "10000", "raw": "DROP_ME"},
        {"symbol": "ETHUSDT", "quantity": "2", "free": "1.5", "locked": "0.5"},
        {"not": "an asset"},
    ]

    compact = compact_account_asset_rows(rows, limit=2)

    assert compact == [
        {
            "asset": "BTC",
            "symbol": "BTCUSDT",
            "kind": "position",
            "qty": 0.1,
            "available": 0.0,
            "locked": 0.0,
            "value_usdt": 10000.0,
        },
        {
            "asset": "",
            "symbol": "ETHUSDT",
            "kind": "position",
            "qty": 2.0,
            "available": 1.5,
            "locked": 0.5,
        },
    ]


def test_futures_risk_helpers_detect_and_compact_live_exposure() -> None:
    assert is_meaningful_futures_risk_row({"positionAmt": 0, "unRealizedProfit": 0}) is False
    assert is_meaningful_futures_risk_row({"positionAmt": "-0.25"}) is True
    assert is_meaningful_futures_risk_row({"unrealizedProfit": "0.01"}) is True

    assert compact_futures_risk_row(
        {
            "symbol": "ethusdt",
            "positionAmt": "-0.25",
            "entryPrice": "3100",
            "markPrice": "3090",
            "liquidationPrice": "4200",
            "leverage": "3",
            "marginType": "cross",
            "unrealizedProfit": "-2.5",
            "raw": "DROP_ME",
        }
    ) == {
        "symbol": "ETHUSDT",
        "position_amt": -0.25,
        "entry_price": 3100.0,
        "mark_price": 3090.0,
        "liquidation_price": 4200.0,
        "leverage": 3,
        "margin_type": "cross",
        "unrealized_profit": -2.5,
        "position_notional_usdt": 772.5,
        "management_status": "open_position",
    }


def test_merge_manager_candidate_price_plan_builds_canonical_execution_packet() -> None:
    candidate = {
        "symbol": "raw",
        "market": "spot",
        "side": "long",
        "score": 81,
        "bid_price": 1,
        "ask_price": 2,
        "book_fresh": True,
        "metadata": {"existing": "keep"},
        "notes": "x" * 500,
    }
    price_plan = {
        "entry_price": 100.0,
        "target_price": 94.0,
        "stop_price": 103.0,
        "quote_budget": 20.0,
        "quote_currency": "USDT",
        "quote_budget_usdt": 20.0,
        "quote_budget_krw": 0.0,
        "entry_style": "wait_for_price",
        "entry_trigger_price": 100.0,
        "entry_trigger_operator": ">=",
        "lane": "futures:short",
        "leverage": 3,
        "margin_type": "isolated",
        "liquidation_price": 140.0,
        "volatile_attack": True,
        "pattern_live_crosscheck": {"status": "aligned"},
        "pattern_inputs": {"prior": {"pattern_key": "squeeze:short"}},
        "market_inputs": {
            "book_fresh": True,
            "bid_price": 99.9,
            "ask_price": 100.1,
            "spread_bps": 20,
            "book_source": "book_ticker",
            "book_fetched_at": "2026-06-20T00:00:00Z",
            "book_market": "futures",
        },
    }

    merged = merge_manager_candidate_price_plan(
        candidate=candidate,
        symbol="BTCUSDT",
        market="futures",
        side="short",
        horizon="futures",
        price_plan=price_plan,
        score_candidate=lambda row: 51.25 if row["symbol"] == "BTCUSDT" else 0.0,
    )

    assert merged["symbol"] == "BTCUSDT"
    assert merged["market"] == "futures"
    assert merged["side"] == "short"
    assert merged["horizon"] == "futures"
    assert merged["entry_price"] == 100.0
    assert merged["target_price_usdt"] == 94.0
    assert merged["stop_price_usdt"] == 103.0
    assert merged["quote_budget_usdt"] == 20.0
    assert merged["entry_style"] == "wait_for_price"
    assert merged["entry_trigger_price"] == 100.0
    assert merged["entry_trigger_operator"] == ">="
    assert merged["lane"] == "futures:short"
    assert merged["leverage"] == 3
    assert merged["margin_type"] == "isolated"
    assert merged["liquidation_price"] == 140.0
    assert merged["bid_price"] == 99.9
    assert merged["ask_price"] == 100.1
    assert merged["book_fresh"] is True
    assert merged["empirical_edge_score"] == 51.25
    assert merged["calculated"]["empirical_edge_score"] == 51.25
    assert merged["metadata"]["existing"] == "keep"
    assert merged["metadata"]["lane"] == "futures:short"
    assert merged["metadata"]["volatile_attack"] is True


def test_merge_manager_candidate_price_plan_keeps_upbit_prices_in_krw_not_usdt_aliases() -> None:
    price_plan = {
        "entry_price": 1_230.0,
        "target_price": 1_260.0,
        "stop_price": 1_210.0,
        "quote_budget": 50_000.0,
        "quote_currency": "KRW",
        "quote_budget_usdt": 35.0,
        "quote_budget_krw": 50_000.0,
        "entry_style": "wait_for_price",
        "entry_trigger_price": 1_230.0,
        "entry_trigger_operator": "<=",
        "lane": "upbit_spot:long",
        "leverage": 1,
        "margin_type": "",
        "liquidation_price": 0.0,
        "market_inputs": {
            "book_fresh": True,
            "bid_price": 1_232.0,
            "ask_price": 1_234.0,
            "spread_bps": 16,
            "book_source": "upbit.orderbook",
            "book_fetched_at": "2026-06-20T00:00:00Z",
            "book_market": "upbit_spot",
        },
        "pattern_inputs": {"prior": {"pattern_key": "squeeze:short"}},
    }

    merged = merge_manager_candidate_price_plan(
        candidate={"metadata": {}, "notes": "KRW unit test"},
        symbol="KRW-JTO",
        market="upbit_spot",
        side="long",
        horizon="short",
        price_plan=price_plan,
        score_candidate=lambda row: 1.0,
    )

    assert merged["entry_price"] == pytest.approx(1_230.0)
    assert merged["quote_currency"] == "KRW"
    assert merged["quote_budget_krw"] == pytest.approx(50_000.0)
    assert "entry_price_usdt" not in merged
    assert "target_price_usdt" not in merged
    assert "stop_price_usdt" not in merged
    assert merged["metadata"]["pattern_prior"] == {"pattern_key": "squeeze:short"}
    assert merged["metadata"]["jue_price_override_allowed"] is True
    assert "notes" in merged


def test_merge_manager_candidate_price_plan_records_gap_when_prices_missing() -> None:
    merged = merge_manager_candidate_price_plan(
        candidate={
            "symbol": "ETHUSDT",
            "data_gaps": ["missing research freshness"],
            "bid_price": 100.0,
        },
        symbol="ETHUSDT",
        market="spot",
        side="long",
        horizon="short",
        price_plan={},
        score_candidate=lambda row: 0.0,
    )

    assert merged["symbol"] == "ETHUSDT"
    assert merged["market"] == "spot"
    assert merged["data_gaps"] == [
        "missing research freshness",
        "missing executable price inputs",
    ]
    assert "bid_price" not in merged
    assert "calculated_price_plan" not in merged


def test_normalize_lane_review_fills_defaults_from_lane_balance() -> None:
    review = normalize_lane_review(
        response={},
        lane_balance={
            "recent_blocks": {
                "dominant_lane": "futures:short",
                "requires_review": True,
            },
            "candidate_lanes": {
                "items": {
                    "spot:long": 2,
                    "futures:short": 5,
                }
            },
        },
    )

    assert review["status"] == "missing_from_model"
    assert review["dominant_lane"] == "futures:short"
    assert review["review_required"] is True
    assert review["lanes_reviewed"] == list(BINANCE_MANAGER_LANES)
    assert review["candidate_lane_summary"] == {"spot:long": 2, "futures:short": 5}
    assert review["selected_lanes"] == []


def test_normalize_lane_review_preserves_model_review_fields() -> None:
    review = normalize_lane_review(
        response={
            "lane_review": {
                "dominant_lane": "spot:long",
                "selected_lanes": ["spot:long"],
                "lanes_reviewed": ["spot:long", "futures:short"],
                "non_selected_lane_reasons": {"futures:short": "funding crowded"},
                "concentration_note": "rotating out of short concentration",
                "exploration_watch": ["ETHUSDT pullback"],
            }
        },
        lane_balance={
            "recent_blocks": {
                "dominant_lane": "futures:short",
                "requires_review": True,
            },
            "candidate_lanes": {"items": {"spot:long": 1}},
        },
    )

    assert review["status"] == "provided"
    assert review["dominant_lane"] == "spot:long"
    assert review["selected_lanes"] == ["spot:long"]
    assert review["lanes_reviewed"] == ["spot:long", "futures:short"]
    assert review["non_selected_lane_reasons"] == {"futures:short": "funding crowded"}
    assert review["concentration_note"] == "rotating out of short concentration"
    assert review["exploration_watch"] == ["ETHUSDT pullback"]


def test_manager_run_workflow_provenance_extracts_workflow_skill_contract_ids() -> None:
    provenance = manager_run_workflow_provenance(
        {
            "jue_workflow": {
                "workflow_id": "binance_cycle",
                "workflow_version": "3",
                "skills": [
                    {"skill_id": "crypto_research"},
                    {"skill_id": " "},
                    {"not_skill": "ignored"},
                ],
                "contracts": [
                    {"contract_id": "block_action"},
                    {"contract_id": "risk_review"},
                ],
            }
        }
    )

    assert provenance["workflow_id"] == "binance_cycle"
    assert provenance["workflow_version"] == 3
    assert json.loads(provenance["skill_ids_json"]) == ["crypto_research"]
    assert json.loads(provenance["contract_ids_json"]) == ["block_action", "risk_review"]


def test_manager_run_workflow_provenance_handles_missing_or_invalid_workflow() -> None:
    provenance = manager_run_workflow_provenance({"jue_workflow": {"workflow_version": "bad"}})

    assert provenance["workflow_id"] == ""
    assert provenance["workflow_version"] == 0
    assert provenance["skill_ids_json"] == "[]"
    assert provenance["contract_ids_json"] == "[]"


def test_manager_candidate_identity_from_payload_normalizes_template_fields() -> None:
    identity = manager_candidate_identity_from_payload(
        {
            "block_template": {
                "symbol": "nearusdt",
                "market": "binance_futures",
                "side": "sell",
                "horizon": "mid",
            }
        }
    )

    assert identity == ("NEARUSDT", "futures", "short", "futures")


def test_manager_action_candidate_keys_collects_create_update_pause_close_rows() -> None:
    keys = manager_action_candidate_keys(
        {
            "create_blocks": [{"symbol": "BTCUSDT", "market": "spot", "side": "long"}],
            "update_blocks": [{"symbol": "ETHUSDT", "venue": "futures", "direction": "short"}],
            "pause_blocks": [{"symbol": ""}],
            "close_blocks": [
                {
                    "block_template": {
                        "symbol": "SOLUSDT",
                        "market": "spot",
                        "stance": "long",
                        "horizon": "long",
                    }
                }
            ],
        }
    )

    assert keys == {
        ("BTCUSDT", "spot", "long", "short"),
        ("ETHUSDT", "futures", "short", "futures"),
        ("SOLUSDT", "spot", "long", "long"),
    }


def test_prioritize_manager_candidate_rows_moves_action_symbols_to_front() -> None:
    rows = [
        {"symbol": "XRPUSDT", "market": "spot", "side": "long"},
        {"symbol": "BTCUSDT", "market": "spot", "side": "long"},
        {"symbol": "ETHUSDT", "market": "futures", "side": "short"},
        {"symbol": "SOLUSDT", "market": "spot", "side": "long"},
    ]
    priority_keys = {
        ("BTCUSDT", "spot", "long", "short"),
        ("ETHUSDT", "futures", "short", "futures"),
    }

    prioritized = prioritize_manager_candidate_rows(rows, priority_keys)

    assert [row["symbol"] for row in prioritized] == [
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "SOLUSDT",
    ]
    assert prioritized[0] is rows[1]


def test_prompt_chars_matches_sorted_json_with_default_string_conversion() -> None:
    payload = {"b": datetime(2026, 1, 2, 3, 4, 5), "a": "alpha"}

    chars = prompt_chars(payload)

    assert chars == len(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def test_prompt_chars_capped_stops_after_limit_without_full_serialization() -> None:
    payload = {"large": "x" * 10_000}

    chars = prompt_chars_capped(payload, cap=80)

    assert chars > 80
    assert chars < prompt_chars(payload)


def test_manager_prompt_original_chars_hint_prefers_prompt_budget_values() -> None:
    assert manager_prompt_original_chars_hint(
        {"prompt_budget": {"total_chars": "1234"}},
        cap=80,
    ) == 1234
    assert manager_prompt_original_chars_hint(
        {"prompt_budget": {"original_chars": 4321}},
        cap=80,
    ) == 4321


def test_manager_prompt_original_chars_hint_uses_capped_estimate_without_budget() -> None:
    hint = manager_prompt_original_chars_hint({"large": "x" * 10_000}, cap=80)

    assert hint > 80
    assert hint < prompt_chars({"large": "x" * 10_000})


def test_manager_storage_compaction_meta_caps_retained_keys() -> None:
    meta = manager_storage_compaction_meta(
        label="binance_manager_prompt",
        original_chars=1200,
        storage_limit_chars=800,
        retained_keys=[f"k{i}" for i in range(60)],
        emergency=True,
    )

    assert meta["status"] == "compacted"
    assert meta["label"] == "binance_manager_prompt"
    assert meta["original_chars"] == 1200
    assert meta["storage_limit_chars"] == 800
    assert len(meta["retained_keys"]) == 50
    assert meta["emergency"] is True


def test_prompt_section_size_rows_orders_sections_and_ignores_budget() -> None:
    rows = prompt_section_size_rows(
        {
            "small": "x",
            "large": {"items": list(range(20))},
            "prompt_budget": {"total_chars": 999_999},
            "prompt_compaction": {"large_internal_notes": "x" * 1000},
        }
    )

    assert [row["section"] for row in rows] == ["large", "small"]
    assert rows[0]["chars"] > rows[1]["chars"]


def test_binance_prompt_budget_finalize_guarantees_attached_budget_under_max() -> None:
    marker = "BINANCE_MANAGER_PROMPT_BUDGET_MODULE_BLOAT"
    prompt = {
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "thesis": marker * 90,
                "risk_note": marker * 90,
                "metadata": {"diagnostics": marker * 50},
            }
            for idx in range(40)
        ],
        "candidates": [
            {
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "reason_md": marker * 80,
                "calculated": {"notes": [marker * 40 for _ in range(10)]},
                "metadata": {"pattern_live_crosscheck": {"raw": marker * 50}},
            }
            for idx in range(60)
        ],
        "jue_workflow": {"instructions": marker * 3000},
        "memory": {"notes": [{"summary": marker * 80} for _ in range(30)]},
        "candidate_generation": {"items": [{"summary": marker * 80} for _ in range(30)]},
        "live_authority": {"validation": marker * 3000},
        "policy": {"items": [{"body": marker * 70} for _ in range(30)]},
        "performance": {"items": [{"summary": marker * 80} for _ in range(20)]},
        "crypto_market_pulse": {"items": [{"summary": marker * 80} for _ in range(20)]},
        "decision_packet": {"evidence": [{"summary": marker * 80} for _ in range(20)]},
        "account": {"raw": marker * 1200},
        "output_schema": {"schema": marker * 1200},
        "language_policy": {"notes": marker * 800},
        "output_language_policy": {"notes": marker * 800},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=60_000,
        warn_chars=80_000,
        max_chars=90_000,
    )

    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(prompt_text) <= 90_000
    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 90_000
    assert marker not in prompt_text


def test_binance_prompt_budget_finalize_prefers_warn_budget_for_latency_sections() -> None:
    marker = "BLOAT"
    prompt = {
        "account": {
            "spot_cash_usdt": 250.0,
            "futures_cash_usdt": 500.0,
            "positions": [{"symbol": "BTCUSDT", "note": marker * 80}],
        },
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": f"B{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "thesis": marker * 120,
                "risk_note": marker * 120,
            }
            for idx in range(20)
        ],
        "candidates": [
            {
                "symbol": f"C{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "reason_md": marker * 120,
                "calculated": {"notes": [marker * 48 for _ in range(4)]},
            }
            for idx in range(60)
        ],
        "candidate_policy_impacts": {
            "_global": [{"policy_id": "global", "decision_guidance": marker * 32}],
            **{
                f"C{idx:02d}USDT": [
                    {
                        "policy_id": f"C{idx:02d}-policy",
                        "decision_guidance": marker * 32,
                        "effect": {"entry_bias": "wait"},
                    }
                ]
                for idx in range(60)
            },
        },
        "recent_performance": {
            "window": "latest",
            "rows": [
                {
                    "symbol": f"C{idx:02d}USDT",
                    "outcome": "loss",
                    "lesson": marker * 140,
                    "path": [marker * 24 for _ in range(4)],
                }
                for idx in range(40)
            ],
        },
        "validation_repair": {
            "status": "ok",
            "repair_backlog": [
                {
                    "id": f"repair-{idx}",
                    "problem": marker * 120,
                    "required_fix": marker * 120,
                }
                for idx in range(30)
            ],
            "block_design_constraints": [
                {"constraint": marker * 100, "reason": marker * 100}
                for _ in range(20)
            ],
        },
        "entry_gate_policy": {"items": [{"body": marker * 120} for _ in range(20)]},
        "policy": {"items": [{"body": marker * 120} for _ in range(20)]},
        "memory": {"notes": [{"summary": marker * 120} for _ in range(20)]},
        "proactive_decision_pressure": {
            "version": "binance_proactive_decision_pressure_v1",
            "status": "action_required",
            "pressure_level": "high",
            "zero_action_streak": 5,
            "previous_error_streak": 5,
            "binance_zero_action_streak": 7,
            "binance_previous_error_streak": 2,
            "effective_zero_action_streak": 7,
            "pressure_source": "binance_activity_gap",
            "candidate_count": 8,
            "strong_candidate_count": 8,
            "growth_governor_mode": "edge_rebuild",
            "growth_governor_allows_new_blocks": True,
            "live_grade": "insufficient",
            "previous_hold_summary": "관망",
            "top_candidates": [
                {"symbol": "KRW-SOL", "market": "upbit_spot", "reason": marker * 50}
            ],
            "binance_market_activity_gap": {
                "version": "binance_market_activity_gap_v1",
                "status": "stale_binance_entries",
                "latest_binance_entry_at": "2026-07-03T05:08:52+00:00",
                "latest_binance_entry_market": "futures",
                "latest_upbit_entry_at": "2026-07-06T08:35:20+00:00",
                "binance_entry_stale_hours": 72.0,
                "binance_candidate_count": 3,
                "candidate_markets": ["futures", "spot"],
                "candidate_symbols": ["ESPUSDT", "BTCUSDT"],
            },
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=70_000,
        warn_chars=90_000,
        max_chars=190_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt["prompt_budget"]["over_warn"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 90_000
    assert prompt_chars(prompt) <= 90_000
    assert "recent_performance" in prompt
    assert "validation_repair" in prompt
    pressure = prompt["proactive_decision_pressure"]
    assert pressure["binance_zero_action_streak"] == 7
    assert pressure["effective_zero_action_streak"] == 7
    assert pressure["pressure_source"] == "binance_activity_gap"
    gap = prompt["proactive_decision_pressure"]["binance_market_activity_gap"]
    assert gap["candidate_symbols"] == ["ESPUSDT", "BTCUSDT"]


def test_binance_prompt_budget_compacts_large_jue_wiki_context() -> None:
    marker = "BINANCE_MANAGER_JUE_WIKI_CONTEXT_BLOAT"
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "prompt_mode": "primary",
            "selection_run_id": "sel_binance_001",
            "budget_report": {"total_chars": 180_000},
            "pages": [
                {
                    "page_id": f"binance.symbol.T{idx:02d}USDT",
                    "title": f"T{idx:02d}USDT research memory",
                    "summary": marker * 60,
                    "content": marker * 600,
                    "evidence_refs": [marker * 30 for _ in range(8)],
                    "metadata": {f"raw_{n}": marker * 20 for n in range(20)},
                }
                for idx in range(16)
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selection_run_id": "sel_binance_001",
            "prompt_mode": "primary",
            "selected_page_ids": ["binance.symbol.T00USDT"],
        },
        "candidates": [
            {"symbol": f"T{idx:02d}USDT", "reason_md": marker * 80}
            for idx in range(40)
        ],
        "blocks": [{"block_id": f"blk_{idx}", "thesis": marker * 80} for idx in range(20)],
        "memory": {"notes": [{"summary": marker * 80} for _ in range(20)]},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=60_000,
        warn_chars=80_000,
        max_chars=90_000,
    )

    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(prompt_text) <= 90_000
    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt["jue_wiki"]["status"] == "ok"
    assert prompt["jue_wiki"]["selection_run_id"] == "sel_binance_001"
    assert prompt["jue_wiki_application"]["selected_page_ids"] == [
        "binance.symbol.T00USDT"
    ]
    assert len(prompt["jue_wiki"]["pages"]) <= 6
    assert prompt_text.count(marker) < 60


def test_binance_prompt_budget_compacts_requested_symbol_coverage_contract() -> None:
    marker = "BINANCE_REQUESTED_SYMBOL_COVERAGE_BLOAT"
    prompt = {
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "partial",
            "unsummarized_symbols": [
                f"T{idx:02d}USDT:{marker * 30}" for idx in range(120)
            ],
            "required_action": "live cross-check required before confidence",
            "required_adjustments": [
                {"symbol": f"T{idx:02d}USDT", "reason": marker * 30}
                for idx in range(60)
            ],
        },
        "jue_wiki": {
            "status": "ok",
            "selection_run_id": "sel_binance_coverage",
            "pages": [
                {
                    "page_id": f"binance.symbol.T{idx:02d}USDT",
                    "content": marker * 500,
                }
                for idx in range(12)
            ],
        },
        "candidates": [
            {"symbol": f"T{idx:02d}USDT", "reason_md": marker * 80}
            for idx in range(40)
        ],
        "output_schema": {"schema": {"create_blocks": [], "lane_review": {}}},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=20_000,
        warn_chars=25_000,
        max_chars=30_000,
    )

    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    coverage = prompt["jue_wiki_requested_symbol_coverage"]
    assert coverage["status"] == "partial"
    assert "T00USDT" in json.dumps(coverage, ensure_ascii=False)
    assert marker not in json.dumps(coverage, ensure_ascii=False)
    assert prompt_text.count(marker) < 20


def test_binance_prompt_budget_keeps_requested_symbol_coverage_missing_and_omitted() -> None:
    marker = "BINANCE_REQUESTED_SYMBOL_COVERAGE_BLOAT"
    prompt = {
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "partial",
            "unsummarized_symbols": ["ETHUSDT", "SOLUSDT", marker * 20],
            "missing_summary_symbols": ["ETHUSDT", marker * 20],
            "prompt_omitted_symbols": ["SOLUSDT", marker * 20],
            "required_adjustments": [
                {
                    "adjustment_type": "coverage_gap_follow_up",
                    "reason": marker * 20,
                    "symbols": ["ETHUSDT"],
                    "resolution": "collect_or_rebuild_summary_before_confident_decision",
                },
                {
                    "adjustment_type": "prompt_omission_follow_up",
                    "reason": marker * 20,
                    "symbols": ["SOLUSDT"],
                    "resolution": "treat_as_reviewed_but_lower_confidence_until_direct_summary_check",
                },
            ],
        },
        "jue_wiki": {
            "status": "ok",
            "selection_run_id": "sel_binance_coverage_split",
            "pages": [{"page_id": "binance.symbol.BTCUSDT", "content": marker * 500}],
        },
        "output_schema": {"schema": {"create_blocks": [], "lane_review": {}}},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=12_000,
    )

    assert prompt_budget_error(prompt) == ""
    coverage = prompt["jue_wiki_requested_symbol_coverage"]
    assert coverage["missing_summary_symbols"] == ["ETHUSDT"]
    assert coverage["prompt_omitted_symbols"] == ["SOLUSDT"]
    assert marker not in json.dumps(coverage, ensure_ascii=False)


def test_compact_jue_wiki_for_prompt_keeps_requested_symbol_guidance_metadata() -> None:
    compact = compact_jue_wiki_for_prompt(
        {
            "status": "ok",
            "requested_symbol_summaries": [
                {
                    "symbol": "ETHUSDT",
                    "page_id": "binance.symbol.ETHUSDT",
                    "summary": "breakout 전에는 orderbook 재확인을 요구",
                    "usage_guidance": {
                        "risk_posture": "breakout_retest",
                        "required_cross_checks": [
                            "funding_rate",
                            "orderbook_depth",
                        ],
                        "max_confidence_without_cross_check": 0.5,
                    },
                    "usage_guidance_effectiveness": {
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture.breakout_retest"
                                ),
                                "status": "active",
                                "sample_count": 9,
                            }
                        ]
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "funding_missing",
                            "status": "degraded",
                            "sample_count": 5,
                        }
                    ],
                    "quality_warning_effectiveness_statuses": ["degraded"],
                }
            ],
        },
        list_limit=4,
        string_limit=240,
    )

    summary = compact["requested_symbol_summaries"][0]
    assert summary["usage_guidance"]["risk_posture"] == "breakout_retest"
    assert summary["usage_guidance"]["max_confidence_without_cross_check"] == 0.5
    assert summary["usage_guidance_effectiveness"]["metrics"][0]["status"] == "active"
    assert summary["quality_warning_effectiveness"][0]["warning"] == "funding_missing"
    assert summary["quality_warning_effectiveness_statuses"] == ["degraded"]


def test_binance_prompt_budget_keeps_requested_symbol_degraded_summaries() -> None:
    marker = "BINANCE_REQUESTED_SYMBOL_DEGRADED_BLOAT"
    prompt = {
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "full",
            "degraded_summary_count": 1,
            "degraded_summary_symbols": ["BTCUSDT", marker * 20],
            "degraded_summary_reasons": [
                {
                    "symbol": "BTCUSDT",
                    "freshness": "stale",
                    "quality_status": "degraded",
                    "quality_warnings": ["funding_data_stale", marker * 20],
                }
            ],
            "required_adjustments": [
                {
                    "adjustment_type": "degraded_summary_cross_check",
                    "reason": marker * 20,
                    "symbols": ["BTCUSDT"],
                    "resolution": "cross_check_live_research_and_lower_confidence_until_refreshed",
                }
            ],
        },
        "jue_wiki": {
            "status": "ok",
            "selection_run_id": "sel_binance_degraded_summary",
            "pages": [{"page_id": "binance.symbol.BTCUSDT", "content": marker * 500}],
        },
        "output_schema": {"schema": {"create_blocks": [], "lane_review": {}}},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=12_000,
    )

    assert prompt_budget_error(prompt) == ""
    coverage = prompt["jue_wiki_requested_symbol_coverage"]
    assert coverage["degraded_summary_count"] == 1
    assert coverage["degraded_summary_symbols"] == ["BTCUSDT"]
    assert coverage["degraded_summary_reasons"] == [
        {
            "symbol": "BTCUSDT",
            "freshness": "stale",
            "quality_status": "weak",
            "quality_warnings": ["funding_data_stale"],
        }
    ]
    assert marker not in json.dumps(coverage, ensure_ascii=False)


def test_binance_prompt_budget_preserves_memory_card_quality_contract() -> None:
    marker = "BINANCE_MEMORY_CARD_QUALITY_BLOAT"
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "version": "jue_wiki_memory_card_quality_v1",
                "status_counts": {"weak": 1, "strong": 2},
                "weak_symbols": ["ETHUSDT", marker * 20],
                "rows": [
                    {
                        "symbol": "ETHUSDT",
                        "quality": "weak",
                        "reason": marker * 25,
                    }
                ],
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT", marker * 20],
                "reason": marker * 25,
            },
        },
        "jue_wiki": {
            "status": "ok",
            "selection_run_id": "sel_binance_memory_quality",
            "pages": [{"page_id": "binance.symbol.ETHUSDT", "content": marker * 500}],
        },
        "candidates": [
            {"symbol": f"T{idx:02d}USDT", "reason_md": marker * 80}
            for idx in range(40)
        ],
        "output_schema": {"schema": {"create_blocks": [], "lane_review": {}}},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=12_000,
    )

    assert prompt_budget_error(prompt) == ""
    quality = prompt["jue_wiki_memory_card_quality"]
    assert quality["summary"]["weak_symbols"] == ["ETHUSDT"]
    assert quality["action_plan"]["status"] == "active"
    assert quality["action_plan"]["symbols"] == ["ETHUSDT"]
    assert marker not in json.dumps(quality, ensure_ascii=False)


def test_binance_memory_card_quality_compaction_preserves_missing_field_plan() -> None:
    compact = compact_jue_wiki_memory_card_quality_prompt(
        {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "status": "active",
            "summary": {
                "version": "jue_wiki_memory_card_quality_v1",
                "status": "active",
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
                "missing_field_counts": {"durable_facts": 1, "lessons": 1},
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts", "lessons"],
                    }
                ],
                "rows": [
                    {
                        "symbol": "ETHUSDT",
                        "quality": "weak",
                        "required_action": (
                            "cross_check_live_research_before_high_confidence"
                        ),
                        "missing_fields": ["durable_facts", "lessons"],
                    }
                ],
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "decision_policy": "resolve_memory_card_quality_before_entry",
                "symbols": ["ETHUSDT"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts", "lessons"],
                    }
                ],
                "required_checks": [
                    "refresh_durable_facts_from_crypto_research_and_quant_context",
                    "review_block_history_and_reflections_for_lessons",
                ],
            },
        },
        list_limit=4,
        string_limit=160,
    )

    assert compact["summary"]["missing_field_counts"] == {
        "durable_facts": 1,
        "lessons": 1,
    }
    assert compact["summary"]["missing_fields_by_symbol"] == [
        {
            "symbol": "ETHUSDT",
            "status": "weak",
            "missing_fields": ["durable_facts", "lessons"],
        }
    ]
    assert compact["summary"]["rows"][0]["missing_fields"] == [
        "durable_facts",
        "lessons",
    ]
    assert compact["action_plan"]["missing_fields_by_symbol"] == [
        {
            "symbol": "ETHUSDT",
            "status": "weak",
            "missing_fields": ["durable_facts", "lessons"],
        }
    ]
    assert compact["action_plan"]["required_checks"] == [
        "refresh_durable_facts_from_crypto_research_and_quant_context",
        "review_block_history_and_reflections_for_lessons",
    ]


def test_binance_prompt_budget_finalize_preserves_core_execution_context() -> None:
    marker = "BINANCE_MANAGER_PROMPT_CORE_CONTEXT_MARKER"
    prompt = {
        "account": {
            "spot_cash_usdt": 1200.0,
            "futures_cash_usdt": 800.0,
            "positions": [{"symbol": "BTCUSDT", "qty": 0.02, "note": marker * 120}],
        },
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "entry_price": 100 + idx,
                "target_price": 95 + idx,
                "stop_price": 103 + idx,
                "thesis": marker * 120,
            }
            for idx in range(36)
        ],
        "live_authority": {
            "live_grade": "restricted",
            "validation_gate": {"status": "validation_probe", "reason": marker * 300},
            "lane_authority": {"raw": marker * 600},
        },
        "candidates": [
            {
                "symbol": f"C{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "reason_md": marker * 120,
                "calculated": {"notes": [marker * 20 for _ in range(8)]},
            }
            for idx in range(80)
        ],
        "policy": {"items": [{"body": marker * 120} for _ in range(30)]},
        "memory": {"notes": [{"summary": marker * 120} for _ in range(30)]},
        "decision_packet": {"evidence": [{"summary": marker * 120} for _ in range(30)]},
        "jue_workflow": {"instructions": marker * 2000},
        "output_schema": {"schema": marker * 2000},
        "raw_context_refs": {"payload": marker * 2000},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=18_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    for section in ("account", "blocks", "live_authority"):
        value = prompt[section]
        assert not (
            isinstance(value, dict)
            and value.get("status") == "omitted_for_prompt_budget"
        ), section
        assert section not in BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS


def test_compact_binance_memory_for_prompt_filters_kis_scoped_rows() -> None:
    compact = compact_binance_memory_for_prompt(
        {
            "status": "ok",
            "memory_scope": "binance",
            "items": [
                {
                    "memory_scope": "kis",
                    "summary": "삼성전자 눌림목 대기 only",
                },
                {
                    "page_id": "kis.symbol.005930",
                    "summary": "unscoped KIS wiki row must not reach Binance",
                },
                {
                    "page_id": "kis:symbol:005930",
                    "summary": "colon KIS page id must not reach Binance",
                },
                {
                    "page_id": "krx/symbol/000660",
                    "summary": "slash KRX page id must not reach Binance",
                },
                {
                    "scope": "domestic_futures",
                    "summary": "domestic futures scope must not reach Binance",
                },
                {
                    "scope": "stock_futures",
                    "summary": "stock futures scope must not reach Binance",
                },
                {
                    "policy_id": "kis.value_pullback",
                    "summary": "bare KIS policy id must not reach Binance",
                },
                {
                    "memory_scope": "binance",
                    "summary": "ETHUSDT funding squeeze watch",
                },
                {
                    "memory_scope": "global",
                    "summary": "avoid chasing extended moves",
                },
                {
                    "memory_scope": "kis",
                    "transferability": "translated",
                    "summary": "KIS value pullback translated to crypto pullback",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "transferability": "translated",
                    "summary": "translated unscoped KIS page lesson for Binance",
                },
                {
                    "policy_id": "kis.value_transfer",
                    "transferability": "translated",
                    "summary": "translated bare KIS policy id lesson for Binance",
                },
                {
                    "page_id": "kis:symbol:003070",
                    "transferability": "translated",
                    "summary": "translated colon KIS page id lesson for Binance",
                },
            ],
            "notes": [
                {
                    "scope": "stock_futures",
                    "transferability": "translated",
                    "summary": "translated stock futures scope lesson for Binance",
                },
            ],
            "active_policies": [
                {
                    "scope": "kr_equity",
                    "policy_id": "policy.kis.value_pullback",
                    "rule": "저평가 눌림목 선호",
                },
                {
                    "scope": "futures",
                    "policy_id": "policy.binance.funding_squeeze",
                    "rule": "funding and book confirmation",
                },
            ],
            "symbol_notes": {
                "000660": "SK hynix scalar KIS note",
                "005930": {
                    "market": "kis",
                    "summary": "삼성전자 밸류 점검",
                },
                "BTCUSDT": "BTC scalar Binance note",
                "ETHUSDT": {
                    "market": "binance",
                    "summary": "ETH orderbook imbalance",
                },
            },
            "block_notes": {
                "generic_note": {
                    "page_id": "kis.symbol.005930",
                    "summary": "generic keyed KIS mapping must not reach Binance",
                },
                "translated_note": {
                    "page_id": "kis.symbol.000660",
                    "transferability": "translated",
                    "summary": "translated generic keyed KIS mapping lesson for Binance",
                },
            },
            "period_reviews": [
                {
                    "target_scope": "kis",
                    "summary": "KIS 장중 눌림 대기 리뷰",
                },
                {
                    "target_scope": "binance",
                    "summary": "BTCUSDT futures overtrading review",
                },
            ],
            "jue_wiki_action_reference_memory": {
                "target_scope": "kis",
                "status": "available",
                "items": [
                    {
                        "page_id": "kis.symbol.005930",
                        "summary": "005930 nested reference",
                    },
                    {
                        "page_id": "kis.symbol.000660",
                        "transferability": "translated",
                        "summary": "translated nested KIS container lesson for Binance",
                    }
                ],
            },
            "jue_wiki_usage_contract_memory": {
                "target_scope": "kis",
                "status": "available",
                "items": [
                    {
                        "page_id": "kis.symbol.123456",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                        },
                        "summary_md": "raw KIS usage contract gap must not reach Binance",
                    },
                    {
                        "page_id": "kis.symbol.654321",
                        "transferability": "translated",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                        },
                        "summary_md": (
                            "translated KIS usage contract gap lesson for Binance"
                        ),
                    },
                ],
            },
            "jue_wiki_context_gap_memory": {
                "target_scope": "binance",
                "status": "available",
                "items": [
                    {
                        "page_id": "binance.symbol.BTCUSDT",
                        "summary": "BTCUSDT nested reference",
                    }
                ],
            },
            "decision_skills": {
                "jue-kis-trading": {
                    "skill_id": "jue-kis-trading",
                    "preview": "KIS equity skill preview",
                },
                "jue-binance-trading": {
                    "skill_id": "jue-binance-trading",
                    "preview": "Binance futures skill preview",
                },
            },
            "decision_skill_status": {
                "jue-kis-trading": {
                    "status": "ready",
                    "preview": "KIS skill status nested",
                },
                "jue-binance-trading": {
                    "status": "ready",
                    "preview": "Binance skill status nested",
                },
            },
            "translated_policy_context": {
                "status": "available",
                "target_scope": "binance",
                "kis_nested_list": [
                    {
                        "page_id": "kis.symbol.111111",
                        "summary": "raw KIS list child must not reach Binance",
                    },
                    {
                        "page_id": "kis.symbol.222222",
                        "transferability": "translated",
                        "summary": "translated list child under KIS key for Binance",
                    },
                ],
                "items_by_policy": {
                    "kis.value.pullback": {
                        "transferability": "translated",
                        "summary": "translated keyed KIS pullback lesson",
                    },
                    "kis.raw.breakout": {
                        "summary": "untranslated keyed KIS raw lesson",
                    },
                },
            },
        },
        list_limit=6,
        string_limit=140,
    )

    encoded = json.dumps(compact, ensure_ascii=False)
    assert "삼성전자 눌림목 대기 only" not in encoded
    assert "unscoped KIS wiki row must not reach Binance" not in encoded
    assert "colon KIS page id must not reach Binance" not in encoded
    assert "slash KRX page id must not reach Binance" not in encoded
    assert "domestic futures scope must not reach Binance" not in encoded
    assert "stock futures scope must not reach Binance" not in encoded
    assert "bare KIS policy id must not reach Binance" not in encoded
    assert "policy.kis.value_pullback" not in encoded
    assert "SK hynix scalar KIS note" not in encoded
    assert "삼성전자 밸류 점검" not in encoded
    assert "generic keyed KIS mapping must not reach Binance" not in encoded
    assert "KIS 장중 눌림 대기 리뷰" not in encoded
    assert "005930 nested reference" not in encoded
    assert "raw KIS usage contract gap must not reach Binance" not in encoded
    assert "KIS equity skill preview" not in encoded
    assert "KIS skill status nested" not in encoded
    assert "untranslated keyed KIS raw lesson" not in encoded
    assert "raw KIS list child must not reach Binance" not in encoded
    assert "ETHUSDT funding squeeze watch" in encoded
    assert "avoid chasing extended moves" in encoded
    assert "KIS value pullback translated to crypto pullback" in encoded
    assert "translated unscoped KIS page lesson for Binance" in encoded
    assert "translated generic keyed KIS mapping lesson for Binance" in encoded
    assert "translated nested KIS container lesson for Binance" in encoded
    assert "translated KIS usage contract gap lesson for Binance" in encoded
    assert "jue_wiki_usage_contract_resolution" in encoded
    assert "translated list child under KIS key for Binance" in encoded
    assert "translated bare KIS policy id lesson for Binance" in encoded
    assert "translated colon KIS page id lesson for Binance" in encoded
    assert "translated stock futures scope lesson for Binance" in encoded
    assert "policy.binance.funding_squeeze" in encoded
    assert "BTC scalar Binance note" in encoded
    assert "ETH orderbook imbalance" in encoded
    assert "BTCUSDT futures overtrading review" in encoded
    assert "BTCUSDT nested reference" in encoded
    assert "Binance futures skill preview" in encoded
    assert "Binance skill status nested" in encoded
    assert "translated keyed KIS pullback lesson" in encoded


def test_compact_binance_memory_for_prompt_preserves_policy_rule_provenance() -> None:
    compact = compact_binance_memory_for_prompt(
        {
            "status": "ok",
            "memory_scope": "binance",
            "policy_rule_evaluation": {
                "global": [
                    {
                        "policy_id": "jue_wiki_action_reference_gap.binance.missing",
                        "rule_id": "jue_wiki_action_reference_gap.binance.missing@v1",
                        "effect": {"entry_bias": "require_wiki_action_reference"},
                        "evidence": {
                            "workflow_ids": ["binance_cycle"],
                            "skill_ids": ["jue-binance-trading"],
                            "contract_ids": ["jue_wiki_action_reference_contract"],
                        },
                    }
                ],
            },
        },
        list_limit=4,
        string_limit=120,
    )

    impact = compact["policy_rule_evaluation"]["global"][0]
    assert impact["evidence"]["workflow_ids"] == ["binance_cycle"]
    assert impact["evidence"]["skill_ids"] == ["jue-binance-trading"]
    assert impact["evidence"]["contract_ids"] == ["jue_wiki_action_reference_contract"]


def test_binance_prompt_budget_preserves_growth_memory_and_workflow_context() -> None:
    marker = "BINANCE_GROWTH_MEMORY_CONTEXT_PRESSURE"
    prompt = {
        "account": {"spot_cash_usdt": 1_000.0, "futures_cash_usdt": 1_000.0},
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "thesis": marker * 60,
                "risk_note": marker * 60,
            }
            for idx in range(24)
        ],
        "candidates": {
            "spot": [
                {"symbol": f"S{idx:02d}USDT", "reason_md": marker * 60}
                for idx in range(60)
            ],
            "futures": [
                {"symbol": f"F{idx:02d}USDT", "reason_md": marker * 60}
                for idx in range(60)
            ],
        },
        "memory": {
            "status": "ok",
            "memory_scope": "binance",
            "persona": "쥬는 변동성 기회를 적극 탐색한다.",
            "scoped_memory": {
                "core": [{"summary_md": marker * 50} for _ in range(20)]
            },
            "active_policies": [
                {"policy_id": f"p{idx}", "reason": marker * 50}
                for idx in range(80)
            ],
            "translated_policy_context": {
                "status": "available",
                "target_scope": "binance",
                "available_count": 7,
                "selected_count": 4,
                "omitted_count": 3,
                "source_scope_counts": {"kis": 7},
                "selection_policy": {
                    "order": "active status, then prompt order",
                    "limit": 4,
                },
                "items": [
                    {
                        "policy_id": "kis.value.pullback",
                        "source_scope": "kis",
                        "transferability": "translated",
                        "status": "active_preference",
                        "action": "prefer",
                        "sample_count": 8,
                        "confidence": 0.72,
                        "expectancy_pct": 0.31,
                        "reason": "KIS 눌림목 교훈은 Binance에서 번역 참고만 한다.",
                    }
                ],
                "instruction": (
                    "Use these only as translated lessons, never as direct venue rules."
                ),
            },
            "symbol_notes": {f"SYM{idx}USDT": marker * 40 for idx in range(250)},
            "validation_repair_backlog": {
                f"case_{idx}": {"summary": marker * 40}
                for idx in range(80)
            },
            "period_memory_coverage": {
                "status": "needs_attention",
                "scopes": ["binance"],
                "missing": ["binance:weekly_replay", "binance:monthly_review"],
                "weekly_reviews": {
                    "binance": {"status": "ok", "period_key": "2026-W21"}
                },
                "weekly_replays": {"binance": {"status": "missing"}},
                "monthly_reviews": {"binance": {"status": "missing"}},
            },
            "policy_rule_evaluation": {
                "global": [
                    {
                        "policy_id": "jue_wiki_action_reference_gap.binance.missing",
                        "rule_id": "jue_wiki_action_reference_gap.binance.missing@v1",
                        "effect": {"entry_bias": "require_wiki_action_reference"},
                        "evidence": {
                            "workflow_ids": ["binance_cycle"],
                            "skill_ids": ["jue-binance-trading"],
                            "contract_ids": ["jue_wiki_action_reference_contract"],
                        },
                    }
                ],
                **{
                    f"rule_{idx}": {"summary": marker * 40}
                    for idx in range(80)
                },
            },
        },
        "crypto_market_pulse": {
            "status": "ok",
            "regime": marker * 80,
            "items": [{"summary": marker * 80} for _ in range(50)],
        },
        "decision_packet_v2": {
            "status": "ok",
            "evidence": [{"summary": marker * 80} for _ in range(50)],
        },
        "decision_packet": {
            "status": "ok",
            "evidence": [{"summary": marker * 80} for _ in range(50)],
        },
        "growth_unlock": {
            "status": "edge_rebuild",
            "reason": marker * 100,
            "actions": [{"summary": marker * 80} for _ in range(30)],
        },
        "jue_workflow": {"instructions": marker * 2500},
        "live_authority": {"status": "ok", "validation": marker * 2000},
        "candidate_policy_impacts": {
            f"SYM{idx}USDT": [{"reason": marker * 50}]
            for idx in range(100)
        },
        "entry_gate_policy": {"policy": marker * 2000},
        "policy": {"items": [{"body": marker * 80} for _ in range(40)]},
        "output_schema": {"schema": marker * 2500},
        "raw_context_refs": {"payload": marker * 3000},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=35_000,
        warn_chars=70_000,
        max_chars=90_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    for section in (
        "memory",
        "crypto_market_pulse",
        "decision_packet_v2",
        "decision_packet",
        "growth_unlock",
        "jue_workflow",
    ):
        value = prompt[section]
        assert not (
            isinstance(value, dict)
            and value.get("status") == "omitted_for_prompt_budget"
        ), section
        assert section not in BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS
    translated_policy_context = prompt["memory"]["translated_policy_context"]
    assert translated_policy_context["status"] == "available"
    assert translated_policy_context["available_count"] == 7
    assert translated_policy_context["selected_count"] == 4
    assert translated_policy_context["omitted_count"] == 3
    assert translated_policy_context["source_scope_counts"] == {"kis": 7}
    assert translated_policy_context["selection_policy"]["limit"] == 4
    assert translated_policy_context["items"][0]["policy_id"] == "kis.value.pullback"
    assert translated_policy_context["items"][0]["source_scope"] == "kis"
    assert translated_policy_context["items"][0]["transferability"] == "translated"
    assert prompt["memory"]["period_memory_coverage"]["status"] == "needs_attention"
    assert prompt["memory"]["period_memory_coverage"]["missing"] == [
        "binance:weekly_replay",
        "binance:monthly_review",
    ]
    policy_impact = prompt["memory"]["policy_rule_evaluation"]["global"][0]
    assert policy_impact["evidence"]["workflow_ids"] == ["binance_cycle"]
    assert policy_impact["evidence"]["skill_ids"] == ["jue-binance-trading"]
    assert policy_impact["evidence"]["contract_ids"] == [
        "jue_wiki_action_reference_contract"
    ]


def test_binance_memory_compaction_preserves_period_memory_coverage() -> None:
    compact = compact_prompt_section(
        "memory",
        {
            "status": "ok",
            "memory_scope": "binance",
            "persona": "쥬는 크립토 기회를 찾는다.",
            "period_memory_coverage": {
                "status": "needs_attention",
                "scopes": ["binance"],
                "missing": ["binance:weekly_replay"],
                "weekly_reviews": {
                    "binance": {"status": "ok", "period_key": "2026-W21"}
                },
                "weekly_replays": {"binance": {"status": "missing"}},
                "monthly_reviews": {"binance": {"status": "missing"}},
            },
            "active_policies": [
                {"policy_id": f"p{idx}", "reason": "x" * 100}
                for idx in range(20)
            ],
        },
        list_limit=1,
        string_limit=60,
    )

    assert compact["period_memory_coverage"]["status"] == "needs_attention"
    assert compact["period_memory_coverage"]["missing"] == ["binance:weekly_replay"]


def test_compact_prompt_section_preserves_jue_workflow_usage_contract() -> None:
    compact = compact_prompt_section(
        "jue_workflow",
        {
            "workflow_id": "binance_cycle",
            "instructions": "Binance workflow " * 80,
            "contracts": [
                {"contract_id": "legacy_contract_1"},
                {"contract_id": "legacy_contract_2"},
                {
                    "contract_id": "jue_wiki_usage_contract_resolution",
                    "required_metadata": "jue_wiki_usage_contract_resolution",
                },
            ],
        },
        list_limit=1,
        string_limit=40,
    )

    contract_ids = [
        row.get("contract_id")
        for row in compact.get("contracts", [])
        if isinstance(row, dict)
    ]
    assert "jue_wiki_usage_contract_resolution" in contract_ids


def test_binance_prompt_budget_handles_operational_203k_pressure() -> None:
    marker = "BINANCE_OPERATIONAL_203K_PRESSURE"
    prompt = {
        "account": {"spot_cash_usdt": 500.0, "futures_cash_usdt": 500.0},
        "memory": {
            "persona": "Jue aggressively searches for asymmetric crypto blocks.",
            "active_policies": [
                {
                    "policy_id": f"policy-{idx}",
                    "summary": marker * 35,
                    "evidence": [marker * 20 for _ in range(4)],
                }
                for idx in range(120)
            ],
            "recent_reflections": [
                {"block_id": f"b{idx}", "lesson": marker * 40}
                for idx in range(80)
            ],
        },
        "candidates": [
            {
                "symbol": f"C{idx:03d}USDT",
                "market": "futures" if idx % 2 else "spot",
                "side": "short" if idx % 3 else "long",
                "entry_price": 1.0 + idx,
                "target_price": 1.2 + idx,
                "stop_price": 0.9 + idx,
                "reason_md": marker * 35,
                "metadata": {"raw_quant": marker * 40},
            }
            for idx in range(120)
        ],
        "policy": {
            "rules": [
                {"id": f"rule-{idx}", "body": marker * 45}
                for idx in range(70)
            ]
        },
        "recent_performance": {
            "rows": [
                {"lane": "futures", "summary": marker * 45}
                for _ in range(70)
            ]
        },
        "entry_gate_policy": {
            "status": "active",
            "cooldown_lanes": {
                f"lane-{idx}": {"raw": marker * 50, "profit_factor": 0.4}
                for idx in range(80)
            },
            "waiting_entry_policy": {"requires_price_trigger": True},
        },
        "candidate_generation": {
            "observe_universe": [
                {"symbol": f"U{idx:03d}USDT", "reason": marker * 20}
                for idx in range(120)
            ]
        },
        "crypto_market_pulse": {
            "narratives": [{"summary": marker * 40} for _ in range(50)]
        },
        "live_authority": {"raw": marker * 1200},
        "jue_workflow": {"instructions": marker * 1200},
        "growth_unlock": {"raw": marker * 600},
        "decision_packet_v2": {
            "evidence": [{"summary": marker * 40} for _ in range(50)]
        },
        "output_schema": {"schema": {"create_blocks": [], "lane_review": {}}},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=70_000,
        warn_chars=90_000,
        max_chars=190_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt_chars(prompt) <= 190_000
    assert prompt["prompt_budget"]["over_max"] is False
    for section in ("memory", "candidates", "entry_gate_policy", "jue_workflow"):
        assert section in prompt


def test_binance_prompt_budget_compacts_policy_rule_memory_bloat_under_warn() -> None:
    marker = "BINANCE_POLICY_RULE_MEMORY_BLOAT"
    prompt = {
        "account": {"spot_cash_usdt": 500.0, "futures_cash_usdt": 500.0},
        "memory": {
            "status": "ok",
            "memory_scope": "binance",
            "persona": "Jue searches for executable crypto asymmetry.",
            "policy_rule_evaluation": {
                f"rule_{idx}": {
                    "policy_id": f"binance.policy.{idx}",
                    "rule_id": f"binance.policy.{idx}@v1",
                    "effect": {"entry_bias": "allow_probe"},
                    "evidence": {
                        "workflow_ids": ["binance_cycle"],
                        "skill_ids": ["jue-binance-trading"],
                        "contract_ids": ["jue_wiki_action_reference_contract"],
                        "raw": marker * 45,
                    },
                    "raw_context": marker * 45,
                }
                for idx in range(90)
            },
            "validation_repair_backlog": {
                f"case_{idx}": {
                    "symbol": "ICPUSDT",
                    "market": "futures",
                    "side": "long",
                    "summary": marker * 30,
                }
                for idx in range(30)
            },
            "translated_policy_context": {
                "status": "available",
                "target_scope": "binance",
                "available_count": 2,
                "selected_count": 1,
                "omitted_count": 1,
                "items": [
                    {
                        "policy_id": "kis.value.pullback",
                        "source_scope": "kis",
                        "transferability": "translated",
                        "reason": "Translated KIS pullback lesson for Binance.",
                    }
                ],
            },
        },
        "candidates": {
            "futures": [
                {
                    "symbol": "ICPUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "intraday",
                    "entry_price": 2.2266,
                    "target_price": 2.34,
                    "stop_price": 2.18,
                    "liquidation_price": 1.71,
                    "quote_budget_usdt": 8.0,
                    "min_executable_notional_usdt": 5.0,
                    "min_executable_qty": 2.25,
                    "validation_repair_probe_design": {
                        "status": "repairable",
                        "min_executable_notional_usdt": 5.0,
                        "min_executable_qty": 2.25,
                        "liquidation_price": 1.71,
                    },
                    "reason_md": marker * 10,
                }
            ]
        },
        "entry_gate_policy": {"status": "active", "policy": marker * 400},
        "candidate_generation": {"status": "ok", "source": "research"},
        "jue_wiki_repair_contract": {
            "status": "required",
            "top_priorities": [
                {
                    "page_id": "binance.symbol.ICPUSDT",
                    "priority_type": "validation_repair",
                    "repair_action": "Create executable ICPUSDT probe.",
                }
            ],
        },
        "critical_response_contract": {
            "create_blocks": {
                "must_use_min_executable_notional_usdt": True,
                "must_preserve_liquidation_price_for_futures": True,
            }
        },
        "native_output_schema": {
            "create_blocks": [
                {
                    "symbol": "ICPUSDT",
                    "market": "futures",
                    "liquidation_price": "required before live trigger",
                }
            ]
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=45_000,
        warn_chars=65_000,
        max_chars=190_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_warn"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 65_000
    assert prompt_chars({"memory": prompt["memory"]}) < 12_000
    policy_impact = prompt["memory"]["policy_rule_evaluation"]["rule_0"]
    assert policy_impact["policy_id"] == "binance.policy.0"
    assert policy_impact["evidence"]["workflow_ids"] == ["binance_cycle"]
    candidate = prompt["candidates"]["futures"][0]
    assert candidate["min_executable_notional_usdt"] == 5.0
    assert candidate["min_executable_qty"] == 2.25
    assert candidate["liquidation_price"] == 1.71
    assert (
        candidate["validation_repair_probe_design"]["min_executable_notional_usdt"]
        == 5.0
    )


def test_binance_prompt_budget_preserves_validation_repair_geometry_constraints() -> None:
    marker = "BINANCE_REPAIR_CONSTRAINT_BLOAT"
    prompt = {
        "memory": {
            "policy_rule_evaluation": {
                f"rule_{idx}": {"raw": marker * 80}
                for idx in range(90)
            }
        },
        "jue_wiki_application": {"raw": marker * 1_000},
        "validation_repair": {
            "version": "validation_repair_action_v1",
            "status": "needs_repair",
            "repair_item_count": 2,
            "constraint_count": 2,
            "max_budget_multiplier": 0.25,
            "risk_budget_multiplier": 0.25,
            "min_reward_risk": 2.0,
            "max_stop_risk_pct": 3.0,
            "policy_ids": [f"policy_{idx}" for idx in range(40)],
            "runner_hints": [marker * 20 for _ in range(20)],
        },
        "candidates": [
            {
                "symbol": "ESPUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 0.07156,
                "target_price": 0.06397,
                "stop_price": 0.075262,
            }
        ],
    }

    finalize_prompt_budget(
        prompt,
        target_chars=45_000,
        warn_chars=65_000,
        max_chars=190_000,
    )

    assert prompt["prompt_budget"]["over_warn"] is False
    repair = prompt["validation_repair"]
    assert repair["max_budget_multiplier"] == pytest.approx(0.25)
    assert repair["risk_budget_multiplier"] == pytest.approx(0.25)
    assert repair["min_reward_risk"] == pytest.approx(2.0)
    assert repair["max_stop_risk_pct"] == pytest.approx(3.0)


def test_binance_prompt_budget_emergency_compacts_large_instruction_sections() -> None:
    marker = "BINANCE_LARGE_INSTRUCTION_SECTIONS"
    prompt = {
        "task": marker * 6_000,
        "horizon_policy": marker * 5_000,
        "horizon_action_authority": marker * 5_000,
        "risk_guard": {"policy": marker * 4_000},
        "growth_target": {"plan": marker * 4_000},
        "decision_inputs": {"raw": marker * 4_000},
        "language_policy": {"raw": marker * 2_000},
        "output_language_policy": {"raw": marker * 2_000},
        "account": {"spot_cash_usdt": 500.0, "futures_cash_usdt": 500.0},
        "memory": {"notes": [{"summary": marker * 80} for _ in range(40)]},
        "candidates": [
            {"symbol": f"C{idx:03d}USDT", "reason_md": marker * 40}
            for idx in range(60)
        ],
        "entry_gate_policy": {"status": "active", "raw": marker * 2_000},
        "output_schema": {"schema": {"create_blocks": [], "lane_review": {}}},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=70_000,
        warn_chars=90_000,
        max_chars=190_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt_chars(prompt) <= 190_000
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt.get("_storage_compaction", {}).get("emergency") is True
    assert "output_schema" in prompt


def test_binance_prompt_budget_preserves_critical_response_contract_under_pressure() -> None:
    contract = {
        "required_top_level_keys": [
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
            "hold_decision",
            "lane_review",
        ],
        "lane_review": {
            "required": True,
            "invalid_if_missing": True,
            "lanes_reviewed": list(BINANCE_MANAGER_LANES),
        },
        "on_hold_only": {"lane_review_required": True},
    }
    prompt = {
        "task": "Manage crypto blocks." + ("T" * 180_000),
        "critical_response_contract": contract,
        "output_schema": {"lane_review": {"required": "mandatory"}},
        "candidates": [{"symbol": "BTCUSDT", "notes": "x" * 30_000} for _ in range(20)],
        "memory": {"notes": "m" * 80_000},
        "live_authority": {"notes": "l" * 80_000},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=70_000,
        warn_chars=90_000,
        max_chars=190_000,
    )

    rendered = json.dumps(prompt, ensure_ascii=False)
    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    assert "critical_response_contract" in prompt
    assert "lane_review" in rendered
    assert "invalid_if_missing" in rendered


def test_binance_prompt_budget_preserves_jue_wiki_repair_pressure_plan() -> None:
    marker = "BINANCE_REPAIR_PRESSURE_BLOAT "
    prompt = {
        "task": "Manage crypto blocks." + (marker * 5_000),
        "jue_wiki_repair_contract": {
            "version": "jue_wiki_repair_contract_v1",
            "status": "active",
            "repair_priority_count": 10,
            "top_priority_count": 6,
            "omitted_priority_count": 4,
            "priority_type_counts": {
                "repair_queue": 1,
                "requested_symbol_coverage": 9,
            },
            "top_priority_type_counts": {
                "repair_queue": 1,
                "requested_symbol_coverage": 5,
            },
            "omitted_priority_type_counts": {"requested_symbol_coverage": 4},
            "repair_pressure_action_plan": {
                "status": "compressed",
                "total_priority_count": 10,
                "top_priority_count": 6,
                "omitted_priority_count": 4,
                "omitted_priority_type_counts": {
                    "requested_symbol_coverage": 4
                },
                "required_response": (
                    "treat top_priorities as representative, not exhaustive; mention "
                    "omitted repair pressure when confidence or sizing depends on wiki "
                    "freshness"
                ),
            },
            "top_priorities": [
                {
                    "page_id": "binance.research.repair_queue",
                    "priority_type": "repair_queue",
                    "source_id": "repair:financials:binance:ETHUSDT",
                    "symbols": ["ETHUSDT"],
                    "repair_action": marker * 20,
                }
            ],
        },
        "candidates": [{"symbol": "BTCUSDT", "notes": marker * 80} for _ in range(40)],
        "memory": {"notes": marker * 2_000},
        "live_authority": {"notes": marker * 2_000},
        "output_schema": {"schema": {"create_blocks": [], "lane_review": {}}},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=70_000,
        warn_chars=90_000,
        max_chars=190_000,
    )

    contract = prompt["jue_wiki_repair_contract"]
    assert prompt_budget_error(prompt) == ""
    assert contract["repair_priority_count"] == 10
    assert contract["omitted_priority_count"] == 4
    assert contract["repair_pressure_action_plan"]["status"] == "compressed"
    assert contract["repair_pressure_action_plan"]["omitted_priority_count"] == 4
    assert contract["top_priorities"][0]["source_id"] == (
        "repair:financials:binance:ETHUSDT"
    )


def test_compact_binance_jue_wiki_repair_contract_preserves_action_batch_plan_counts() -> None:
    compact = compact_jue_wiki_repair_contract_prompt(
        {
            "version": "jue_wiki_repair_contract_v1",
            "status": "active",
            "repair_priority_count": 20,
            "top_priority_count": 4,
            "omitted_priority_count": 16,
            "repair_pressure_action_plan": {
                "status": "compressed",
                "total_priority_count": 20,
                "top_priority_count": 4,
                "omitted_priority_count": 16,
                "action_batch_count": 2,
                "action_batch_total_count": 9,
                "required_response": (
                    "treat action_batches as grouped repair work before sizing"
                ),
            },
            "action_batches": [
                {
                    "scope": "binance",
                    "action_type": "refresh_symbol_research",
                    "count": 7,
                    "symbols": ["NEARUSDT", "LTCUSDT", "SOLUSDT"],
                    "warning_counts": {"research_summary_missing": 7},
                    "max_severity_score": 86.0,
                },
                {
                    "scope": "binance",
                    "action_type": "refresh_orderbook_depth",
                    "count": 2,
                    "symbols": ["BTCUSDT", "ETHUSDT"],
                    "warning_counts": {"orderbook_depth_stale": 2},
                    "max_severity_score": 64.0,
                },
            ],
        },
        list_limit=1,
        string_limit=80,
    )

    plan = compact["repair_pressure_action_plan"]
    assert plan["action_batch_count"] == 2
    assert plan["action_batch_total_count"] == 9
    assert plan["action_batch_type_counts"] == {
        "refresh_symbol_research": 7,
        "refresh_orderbook_depth": 2,
    }
    assert plan["action_batch_scopes"] == ["binance"]
    assert plan["action_batch_warning_counts"] == {
        "research_summary_missing": 7,
        "orderbook_depth_stale": 2,
    }
    assert plan["action_batch_max_severity_score"] == 86.0
    assert compact["action_batches"][0]["symbols"] == [
        "NEARUSDT",
        "LTCUSDT",
        "SOLUSDT",
    ]
    assert compact["action_batches"][0]["warning_counts"] == {
        "research_summary_missing": 7
    }
    assert compact["action_batches"][0]["max_severity_score"] == 86.0


def test_compact_binance_jue_wiki_repair_contract_preserves_upstream_action_batch_omissions() -> None:
    action_batches = [
        {
            "scope": "binance",
            "action_type": f"refresh_crypto_slice_{idx:02d}",
            "count": 1,
            "symbols": [f"ALT{idx}USDT"],
            "max_severity_score": 90.0 - idx,
        }
        for idx in range(12)
    ]

    compact = compact_jue_wiki_repair_contract_prompt(
        {
            "version": "jue_wiki_repair_contract_v1",
            "status": "active",
            "repair_priority_count": 15,
            "top_priority_count": 4,
            "omitted_priority_count": 11,
            "action_batch_total_count": 15,
            "action_batch_omitted_count": 3,
            "repair_pressure_action_plan": {
                "status": "compressed",
                "total_priority_count": 15,
                "top_priority_count": 4,
                "omitted_priority_count": 11,
                "action_batch_count": 12,
                "action_batch_total_count": 15,
                "action_batch_omitted_count": 3,
                "required_response": "treat action_batches as grouped repair work",
            },
            "action_batches": action_batches,
        },
        list_limit=2,
        string_limit=80,
    )

    assert len(compact["action_batches"]) == 2
    assert compact["action_batch_total_count"] == 15
    assert compact["action_batch_omitted_count"] == 13
    assert compact["action_batch_visible_pressure_count"] == 2
    assert compact["action_batch_pressure_visibility_ratio"] == 0.1333
    assert compact["repair_pressure_action_plan"]["action_batch_count"] == 12
    assert compact["repair_pressure_action_plan"]["action_batch_total_count"] == 15
    assert compact["repair_pressure_action_plan"]["action_batch_omitted_count"] == 13
    assert (
        compact["repair_pressure_action_plan"][
            "action_batch_visible_pressure_count"
        ]
        == 2
    )
    assert (
        compact["repair_pressure_action_plan"][
            "action_batch_pressure_visibility_ratio"
        ]
        == 0.1333
    )


def test_binance_bounded_compaction_preserves_memory_card_gap_summary() -> None:
    compact = compact_prompt_value_bounded(
        {
            "version": "jue_wiki_repair_contract_v1",
            "status": "repair_required",
            "sample_count": 3,
            "missed_count": 2,
            "repair_priority_count": 1,
            "top_degraded": [{"source_id": "binance.symbol.ETHUSDT"}],
            "memory_card_quality_gap_summary": {
                "status": "repair_required",
                "priority_missing_fields": ["durable_facts"],
                "priority_required_checks": ["refresh_durable_facts"],
                "priority_focus": {
                    "missing_field": "durable_facts",
                    "missing_field_sample_count": 3,
                    "missing_field_missed_count": 2,
                    "required_check": "refresh_durable_facts",
                    "required_check_sample_count": 3,
                    "required_check_missed_count": 2,
                    "instruction": "resolve_priority_memory_card_quality_gap_first",
                },
                "top_missing_fields": [
                    {
                        "field": "durable_facts",
                        "sample_count": 3,
                        "missed_count": 2,
                    }
                ],
                "top_required_checks": [
                    {
                        "check": "refresh_durable_facts",
                        "sample_count": 3,
                        "missed_count": 2,
                    }
                ],
            },
        },
        list_limit=1,
        string_limit=80,
        dict_limit=4,
    )

    assert compact["memory_card_quality_gap_summary"] == {
        "status": "repair_required",
        "priority_missing_fields": ["durable_facts"],
        "priority_required_checks": ["refresh_durable_facts"],
        "priority_focus": {
            "missing_field": "durable_facts",
            "missing_field_sample_count": 3,
            "missing_field_missed_count": 2,
            "required_check": "refresh_durable_facts",
            "required_check_sample_count": 3,
            "required_check_missed_count": 2,
            "instruction": "resolve_priority_memory_card_quality_gap_first",
        },
        "top_missing_fields": [
            {"field": "durable_facts", "sample_count": 3, "missed_count": 2}
        ],
        "top_required_checks": [
            {
                "check": "refresh_durable_facts",
                "sample_count": 3,
                "missed_count": 2,
            }
        ],
    }


def test_binance_bounded_compaction_rebuilds_legacy_memory_card_gap_priorities() -> None:
    compact = compact_prompt_value_bounded(
        {
            "memory_card_quality_gap_summary": {
                "status": "repair_required",
                "missing_field_missed_counts": {
                    "durable_facts": 2,
                    "risk_notes": 0,
                },
                "required_check_missed_counts": {
                    "refresh_durable_facts": 2,
                    "inspect_risk_notes": 0,
                },
                "top_missing_fields": [
                    {
                        "field": "risk_notes",
                        "sample_count": 3,
                        "missed_count": 0,
                    },
                    {
                        "field": "durable_facts",
                        "sample_count": 2,
                        "missed_count": 2,
                    },
                ],
                "top_required_checks": [
                    {
                        "check": "inspect_risk_notes",
                        "sample_count": 3,
                        "missed_count": 0,
                    },
                    {
                        "check": "refresh_durable_facts",
                        "sample_count": 2,
                        "missed_count": 2,
                    },
                ],
            }
        },
        list_limit=4,
        string_limit=80,
        dict_limit=4,
    )

    gap_summary = compact["memory_card_quality_gap_summary"]
    assert gap_summary["priority_missing_fields"] == ["durable_facts"]
    assert gap_summary["priority_required_checks"] == ["refresh_durable_facts"]


def test_binance_manager_latency_guard_detects_recent_timeout_runs() -> None:
    guard = manager_latency_guard_from_runs(
        [
            {
                "id": 42,
                "status": "error",
                "error_message": "codex native sdk timed out after 600.0s",
            },
            {"id": 43, "status": "ok", "error_message": ""},
            {"id": 44, "status": "error", "error_message": "timeout while waiting"},
        ],
        target_chars=7_000,
    )

    assert manager_run_is_timeout_error(
        {"status": "error", "error_message": "request timed out"}
    )
    assert not manager_run_is_timeout_error(
        {"status": "ok", "error_message": "request timed out"}
    )
    assert guard["active"] is True
    assert guard["reason"] == "recent_manager_timeout"
    assert guard["recent_timeout_count"] == 2
    assert guard["recent_timeout_run_ids"] == [42, 44]
    assert guard["target_chars"] == 10_000


def test_binance_manager_latency_guard_caps_recovery_target_after_repeated_timeouts() -> None:
    guard = manager_latency_guard_from_runs(
        [
            {
                "id": 45,
                "status": "error",
                "error_message": "manager_task_timeout_after_630s",
            },
            {
                "id": 44,
                "status": "error",
                "error_message": "codex native sdk timed out after 600s",
            },
            {
                "id": 43,
                "status": "error",
                "error_message": "request timeout",
            },
        ],
        target_chars=70_000,
    )

    assert guard["active"] is True
    assert guard["recent_timeout_count"] == 3
    assert guard["target_chars"] == 24_000
    assert guard["configured_target_chars"] == 70_000

    deeper_guard = manager_latency_guard_from_runs(
        [
            {
                "id": 46,
                "status": "error",
                "error_message": "codex native sdk timed out after 900s",
            },
            {
                "id": 45,
                "status": "error",
                "error_message": "manager_task_timeout_after_630s",
            },
            {
                "id": 44,
                "status": "error",
                "error_message": "codex native sdk timed out after 600s",
            },
            {
                "id": 43,
                "status": "error",
                "error_message": "request timeout",
            },
        ],
        target_chars=70_000,
    )

    assert deeper_guard["recent_timeout_count"] == 4
    assert deeper_guard["target_chars"] == 18_000
    assert deeper_guard["configured_target_chars"] == 70_000


def test_finalize_binance_manager_prompt_budget_uses_latency_guard_target() -> None:
    prompt = {
        "native_thread_mode": "ephemeral",
        "decision_inputs": ["account", "candidates"],
        "critical_response_contract": {"lane_review": {"required": True}},
        "native_output_schema": {
            "create_blocks": [{"symbol": "BTCUSDT", "entry_price": "required"}],
            "lane_review": {"required": "mandatory"},
        },
        "entry_gate_policy": {"blob": "ENTRY_POLICY_BLOAT" * 900},
        "candidate_generation": {"blob": "CANDIDATE_GENERATION_BLOAT" * 800},
        "candidate_policy_impacts": {
            f"T{idx:02d}USDT": [
                {
                    "policy_id": f"p{idx}",
                    "decision_guidance": "POLICY_IMPACT_BLOAT" * 80,
                }
            ]
            for idx in range(40)
        },
        "candidates": [
            {
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "reason_md": "CANDIDATE_BLOAT" * 120,
            }
            for idx in range(40)
        ],
        "blocks": [{"symbol": "BTCUSDT", "thesis": "BLOCK_BLOAT" * 300}],
        "latency_guard": {
            "version": "binance_manager_latency_guard_v1",
            "active": True,
            "reason": "recent_manager_timeout",
            "recent_timeout_count": 4,
            "target_chars": 18_000,
            "configured_target_chars": 45_000,
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=45_000,
        warn_chars=65_000,
        max_chars=190_000,
    )

    assert prompt["prompt_budget"]["target_chars"] == 18_000
    assert prompt["prompt_budget"]["warn_chars"] == 20_500
    assert prompt["prompt_budget"]["total_chars"] <= 20_500
    assert prompt["latency_guard"]["target_chars"] == 18_000
    assert prompt["native_thread_mode"] == "ephemeral"


def test_binance_prompt_budget_finalizes_under_warn_after_soft_recovery_case() -> None:
    marker = "BINANCE_SOFT_WARN_RECOVERY_BLOAT "
    multiplier = 2
    prompt = {
        "critical_response_contract": {"lane_review": {"required": True}},
        "memory": {
            "memory_scope": "binance",
            "persona": "Jue searches for executable crypto asymmetry.",
            "policy_rule_evaluation": {
                f"rule_{idx}": {
                    "policy_id": f"binance.policy.{idx}",
                    "evidence": {"workflow_ids": ["binance_cycle"], "raw": marker * (18 * multiplier)},
                    "raw_context": marker * (18 * multiplier),
                }
                for idx in range(120)
            },
            "active_insights": [
                {"summary": marker * (24 * multiplier), "symbols": ["ESPUSDT", "HMSTRUSDT"]}
                for _ in range(70)
            ],
        },
        "jue_wiki_application": {
            "pages": [
                {"page_id": f"binance.symbol.{idx}", "summary": marker * (22 * multiplier)}
                for idx in range(45)
            ],
            "trust_profile": {"notes": [marker * (18 * multiplier) for _ in range(20)]},
        },
        "validation_repair": {
            "status": "needs_repair",
            "min_reward_risk": 2.0,
            "max_stop_risk_pct": 3.0,
            "runner_hints": [marker * (18 * multiplier) for _ in range(20)],
        },
        "candidates": [
            {
                "symbol": "ESPUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 0.07156,
                "target_price": 0.06397,
                "stop_price": 0.075262,
                "reason_md": marker * (18 * multiplier),
            }
        ],
        "candidate_generation": {
            "observe_universe": [
                {"symbol": f"C{idx:03d}USDT", "reason": marker * (14 * multiplier)}
                for idx in range(60)
            ]
        },
        "entry_gate_policy": {"status": "active", "raw": marker * (160 * multiplier)},
        "jue_wiki_repair_contract": {"status": "active", "raw": marker * (140 * multiplier)},
        "live_authority": {"raw": marker * (120 * multiplier)},
        "diagnostics": {"raw": marker * (100 * multiplier)},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=70_000,
        warn_chars=90_000,
        max_chars=190_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_warn"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 90_000
    assert prompt_chars({"memory": prompt["memory"]}) < 12_000
    assert prompt["validation_repair"]["min_reward_risk"] == pytest.approx(2.0)
    assert prompt["validation_repair"]["max_stop_risk_pct"] == pytest.approx(3.0)


def test_finalize_binance_manager_prompt_budget_preserves_ephemeral_after_latency_emergency() -> None:
    prompt = {
        "native_thread_mode": "ephemeral",
        "task": "manage crypto blocks " + ("T" * 220_000),
        "decision_inputs": ["account", "candidates", "jue_wiki"],
        "account": {"status": "ok", "spot_cash_usdt": 1_000.0},
        "risk_guard": {
            "status": "ok",
            "current_equity_usdt": 1_000.0,
            "allow_new_entries": True,
        },
        "growth_target": {"current_equity_usdt": 1_000.0},
        "growth_governor": {"metrics": {"risk_guard_status": "ok"}},
        "critical_response_contract": {"lane_review": {"required": True}},
        "native_output_schema": {
            "create_blocks": [{"symbol": "BTCUSDT", "entry_price": "required"}],
            "lane_review": {"required": "mandatory"},
        },
        "candidate_policy_impacts": {
            f"T{idx:02d}USDT": [
                {
                    "policy_id": f"p{idx}",
                    "decision_guidance": "POLICY_IMPACT_BLOAT" * 120,
                }
            ]
            for idx in range(80)
        },
        "candidates": [
            {
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "reason_md": "CANDIDATE_BLOAT" * 200,
            }
            for idx in range(80)
        ],
        "jue_wiki": {"pages": [{"body": "WIKI_BLOAT" * 2000}]},
        "latency_guard": {
            "version": "binance_manager_latency_guard_v1",
            "active": True,
            "reason": "recent_manager_timeout",
            "recent_timeout_count": 6,
            "target_chars": 18_000,
            "configured_target_chars": 45_000,
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=45_000,
        warn_chars=65_000,
        max_chars=190_000,
    )

    assert prompt["native_thread_mode"] == "ephemeral"
    assert prompt["prompt_budget"]["target_chars"] == 18_000
    assert prompt_budget_error(prompt) == ""


def test_binance_latency_recovery_keeps_jue_wiki_repair_action_batches() -> None:
    prompt = {
        "native_thread_mode": "ephemeral",
        "native_thread_key": "binance:manager:test",
        "critical_response_contract": {"lane_review": {"required": True}},
        "native_output_schema": {
            "create_blocks": [{"symbol": "required"}],
            "lane_review": {"required": True},
        },
        "execution_gate": {"status": "open"},
        "account": {"status": "ok", "cash_usdt": 1000.0},
        "jue_wiki_repair_contract": {
            "version": "jue_wiki_repair_contract_v1",
            "status": "repair_required",
            "repair_priority_count": 20,
            "top_priority_count": 4,
            "omitted_priority_count": 16,
            "priority_type_counts": {"repair_queue": 20},
            "top_priority_type_counts": {"repair_queue": 4},
            "omitted_priority_type_counts": {"repair_queue": 16},
            "repair_pressure_action_plan": {
                "status": "compressed",
                "required_response": "batch repair pressure must remain visible",
            },
            "top_priorities": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "priority_type": "repair_queue",
                    "source_id": "repair:binance:NEARUSDT",
                }
            ],
            "action_batches": [
                {
                    "scope": "binance",
                    "action_type": "refresh_symbol_research",
                    "count": 7,
                    "symbols": ["NEARUSDT", "LTCUSDT"],
                    "recommended_actions": ["refresh crypto market research"],
                }
            ],
        },
    }

    recovery = latency_recovery_core_prompt(
        prompt,
        latency_guard={"target_chars": 18_000},
        original_chars=250_000,
        label="binance_latency_test",
    )

    contract = recovery["jue_wiki_repair_contract"]
    assert contract["status"] == "repair_required"
    assert contract["action_batches"][0]["action_type"] == "refresh_symbol_research"
    assert contract["action_batches"][0]["symbols"] == ["NEARUSDT", "LTCUSDT"]


def test_finalize_binance_manager_latency_core_keeps_executable_candidate_items() -> None:
    prompt = {
        "native_thread_mode": "ephemeral",
        "task": "manage crypto blocks " + ("T" * 220_000),
        "decision_inputs": ["account", "candidates", "candidate_generation"],
        "account": {"status": "ok", "spot_cash_usdt": 1_000.0},
        "risk_guard": {
            "status": "ok",
            "current_equity_usdt": 1_000.0,
            "allow_new_entries": True,
        },
        "growth_target": {"current_equity_usdt": 1_000.0},
        "growth_governor": {"require_waiting_entry": True},
        "critical_response_contract": {"lane_review": {"required": True}},
        "native_output_schema": {
            "create_blocks": [{"symbol": "BTCUSDT", "entry_price": "required"}],
            "lane_review": {"required": "mandatory"},
        },
        "candidate_generation": {
            "candidate_count": 12,
            "candidate_packets": {
                "top_movers": [{"symbol": "RIFUSDT", "score": 80}],
            },
        },
        "candidate_policy_impacts": {
            "RIFUSDT": [{"policy_id": "validation.binance.calmar_ratio"}],
        },
        "candidates": [
            {
                "symbol": "RIFUSDT",
                "market": "spot",
                "side": "long",
                "horizon": "short",
                "score": 80,
                "entry_price": 1.2,
                "target_price": 1.38,
                "stop_price": 1.12,
                "quote_budget_usdt": 25.0,
                "calculated": {
                    "method_version": "price_design_v1",
                    "entry_quality": "pullback_reclaim",
                    "reward_risk": 2.25,
                    "stop_pct": 6.67,
                    "target_pct": 15.0,
                },
                "reason_md": "executable high-volatility spot probe",
            },
            *[
                {
                    "symbol": f"T{idx:02d}USDT",
                    "market": "futures",
                    "side": "short",
                    "entry_price": 10.0 + idx,
                    "target_price": 9.0 + idx,
                    "stop_price": 10.5 + idx,
                    "reason_md": "CANDIDATE_BLOAT" * 200,
                }
                for idx in range(60)
            ],
        ],
        "jue_wiki": {"pages": [{"body": "WIKI_BLOAT" * 2000}]},
        "latency_guard": {
            "version": "binance_manager_latency_guard_v1",
            "active": True,
            "reason": "recent_manager_timeout",
            "recent_timeout_count": 6,
            "target_chars": 18_000,
            "configured_target_chars": 45_000,
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=45_000,
        warn_chars=65_000,
        max_chars=190_000,
    )

    candidates = prompt["candidates"]
    assert isinstance(candidates, list)
    assert candidates
    first = candidates[0]
    assert first["symbol"] == "RIFUSDT"
    assert first["entry_price"] == 1.2
    assert first["target_price"] == 1.38
    assert first["stop_price"] == 1.12
    assert "calculated" in first
    assert prompt["compaction_meta"]["sections"]["candidates"] == {
        "item_count": 61,
        "retained_item_count": len(candidates),
        "omitted_item_count": 61 - len(candidates),
    }


def test_compact_manager_storage_payload_preserves_precompacted_candidate_items() -> None:
    payload = {
        "native_thread_mode": "ephemeral",
        "task": "manage crypto blocks " + ("T" * 200_000),
        "prompt_budget": {"total_chars": 220_000},
        "account": {"status": "ok", "spot_cash_usdt": 1_000.0},
        "risk_guard": {
            "status": "ok",
            "current_equity_usdt": 1_000.0,
            "allow_new_entries": True,
        },
        "candidates": {
            "item_count": 2,
            "items": [
                {
                    "symbol": "RIFUSDT",
                    "market": "spot",
                    "side": "long",
                    "entry_price": 1.2,
                    "target_price": 1.38,
                    "stop_price": 1.12,
                    "calculated": {"reward_risk": 2.25},
                }
            ],
        },
        "output_schema": {"create_blocks": [], "lane_review": {"required": True}},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_000,
        label="binance_manager_prompt",
    )

    assert compact["candidates"]["item_count"] == 2
    assert compact["candidates"]["items"][0]["symbol"] == "RIFUSDT"
    assert compact["candidates"]["items"][0]["entry_price"] == 1.2


def test_compact_manager_storage_payload_keeps_lane_diverse_candidate_items() -> None:
    payload = {
        "native_thread_mode": "ephemeral",
        "task": "manage crypto blocks " + ("T" * 200_000),
        "prompt_budget": {"total_chars": 220_000},
        "account": {"status": "ok", "spot_cash_usdt": 1_000.0},
        "risk_guard": {
            "status": "ok",
            "current_equity_usdt": 1_000.0,
            "allow_new_entries": True,
        },
        "candidates": {
            "item_count": 5,
            "items": [
                {
                    "symbol": "KRW-A",
                    "market": "upbit_spot",
                    "side": "long",
                    "entry_price": 1.0,
                    "target_price": 1.1,
                    "stop_price": 0.95,
                },
                {
                    "symbol": "KRW-B",
                    "market": "upbit_spot",
                    "side": "long",
                    "entry_price": 2.0,
                    "target_price": 2.1,
                    "stop_price": 1.95,
                },
                {
                    "symbol": "RIFUSDT",
                    "market": "spot",
                    "side": "long",
                    "entry_price": 1.2,
                    "target_price": 1.38,
                    "stop_price": 1.12,
                },
                {
                    "symbol": "DOGEUSDT",
                    "market": "futures",
                    "side": "short",
                    "entry_price": 0.2,
                    "target_price": 0.18,
                    "stop_price": 0.21,
                    "liquidation_price": 0.25,
                },
            ],
        },
        "output_schema": {"create_blocks": [], "lane_review": {"required": True}},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_000,
        label="binance_manager_prompt",
    )

    selected_symbols = {
        str(row.get("symbol") or "")
        for row in compact["candidates"]["items"]
        if isinstance(row, dict)
    }
    assert {"KRW-A", "RIFUSDT", "DOGEUSDT"}.issubset(selected_symbols)


def test_candidate_compaction_preserves_volatile_attack_lane_under_tight_limit() -> None:
    rows = [
        {"symbol": "KRW-A", "market": "upbit_spot", "side": "long"},
        {"symbol": "RIFUSDT", "market": "spot", "side": "long"},
        {"symbol": "SOLUSDT", "market": "futures", "side": "long"},
        {"symbol": "DOGEUSDT", "market": "futures", "side": "short"},
        {
            "symbol": "TLMUSDT",
            "market": "spot",
            "side": "long",
            "lane": "volatile_attack",
            "calculated": {"lane": "volatile_attack"},
        },
    ]

    selected = select_manager_candidate_rows_for_compaction(
        rows,
        limit=4,
        lane_diverse=True,
    )

    assert any(row.get("lane") == "volatile_attack" for row in selected)


def test_final_budget_candidate_compaction_preserves_deep_volatile_attack() -> None:
    prompt = {
        "critical_response_contract": {"text": "keep"},
        "candidates": [
            {
                "symbol": f"KRW-{idx:03d}",
                "market": "upbit_spot",
                "side": "long",
                "entry_price": 1000 + idx,
                "target_price": 1100 + idx,
                "stop_price": 950 + idx,
                "reason_md": "ordinary candidate " * 120,
            }
            for idx in range(22)
        ]
        + [
            {
                "symbol": "TLMUSDT",
                "market": "spot",
                "side": "long",
                "lane": "volatile_attack",
                "entry_price": 0.01,
                "target_price": 0.013,
                "stop_price": 0.009,
                "reason_md": "explosive volatile candidate " * 120,
                "calculated": {"lane": "volatile_attack", "volatile_attack": True},
            }
        ],
        "performance": {"blob": "x" * 20_000},
    }

    compact_manager_sections_for_final_budget(prompt, target_chars=10_000)
    rows = prompt["candidates"]

    assert isinstance(rows, list)
    assert any(row.get("lane") == "volatile_attack" for row in rows)


def test_final_budget_candidate_compaction_keeps_more_than_four_lane_diverse_rows() -> None:
    candidates = [
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "target_price": 106.0,
            "stop_price": 97.0,
            "reason_md": "spot candidate " * 20,
        },
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "long",
            "entry_price": 200.0,
            "target_price": 212.0,
            "stop_price": 194.0,
            "reason_md": "futures long candidate " * 20,
        },
        {
            "symbol": "SOLUSDT",
            "market": "futures",
            "side": "short",
            "entry_price": 50.0,
            "target_price": 47.0,
            "stop_price": 51.5,
            "reason_md": "futures short candidate " * 20,
        },
        {
            "symbol": "KRW-XRP",
            "market": "upbit_spot",
            "side": "long",
            "entry_price": 700.0,
            "target_price": 735.0,
            "stop_price": 682.0,
            "reason_md": "upbit candidate " * 20,
        },
        {
            "symbol": "HEIUSDT",
            "market": "futures",
            "side": "short",
            "lane": "volatile_attack",
            "entry_price": 0.12,
            "target_price": 0.108,
            "stop_price": 0.123,
            "reason_md": "volatile candidate " * 20,
            "calculated": {"lane": "volatile_attack", "volatile_attack": True},
        },
        {
            "symbol": "BNBUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 600.0,
            "target_price": 630.0,
            "stop_price": 585.0,
            "reason_md": "extra binance candidate " * 20,
        },
    ]
    prompt = {
        "critical_response_contract": {"text": "keep"},
        "candidates": candidates,
        "memory": {"blob": "M" * 80_000},
        "jue_wiki_application": {"blob": "W" * 20_000},
    }

    compact_manager_sections_for_final_budget(prompt, target_chars=70_000)

    rows = prompt["candidates"]
    assert isinstance(rows, list)
    selected_symbols = {row["symbol"] for row in rows}
    assert len(rows) >= 5
    assert {"BTCUSDT", "ETHUSDT", "SOLUSDT", "KRW-XRP", "HEIUSDT"}.issubset(
        selected_symbols
    )


def test_final_budget_preserves_binance_activity_gap_pressure() -> None:
    prompt = {
        "critical_response_contract": {"lane_review": {"required": True}},
        "proactive_decision_pressure": {
            "version": "binance_proactive_decision_pressure_v1",
            "status": "action_required",
            "pressure_level": "high",
            "zero_action_streak": 5,
            "previous_error_streak": 5,
            "binance_zero_action_streak": 7,
            "binance_previous_error_streak": 2,
            "effective_zero_action_streak": 7,
            "pressure_source": "binance_activity_gap",
            "candidate_count": 8,
            "strong_candidate_count": 8,
            "growth_governor_mode": "edge_rebuild",
            "growth_governor_allows_new_blocks": True,
            "live_grade": "insufficient",
            "previous_hold_summary": "관망",
            "top_candidates": [
                {"symbol": "KRW-SOL", "market": "upbit_spot", "reason": "x" * 20_000}
            ],
            "required_resolution": "resolve at least one candidate",
            "response_contract": {"action_required": "cite exact blocker"},
            "allowed_resolutions": ["small_probe"],
            "binance_market_activity_gap": {
                "version": "binance_market_activity_gap_v1",
                "status": "stale_binance_entries",
                "latest_binance_entry_at": "2026-07-03T05:08:52+00:00",
                "latest_binance_entry_market": "futures",
                "latest_upbit_entry_at": "2026-07-06T08:35:20+00:00",
                "binance_entry_stale_hours": 72.0,
                "binance_candidate_count": 3,
                "candidate_markets": ["futures", "spot"],
                "candidate_symbols": ["ESPUSDT", "BTCUSDT"],
                "manager_instruction": (
                    "prefer at least one Binance spot/futures waiting probe"
                ),
            },
        },
        "memory": {"blob": "M" * 80_000},
    }

    compact_manager_sections_for_final_budget(prompt, target_chars=10_000)

    pressure = prompt["proactive_decision_pressure"]
    assert pressure["binance_zero_action_streak"] == 7
    assert pressure["effective_zero_action_streak"] == 7
    assert pressure["pressure_source"] == "binance_activity_gap"
    gap = prompt["proactive_decision_pressure"]["binance_market_activity_gap"]
    assert gap["status"] == "stale_binance_entries"
    assert gap["candidate_symbols"] == ["ESPUSDT", "BTCUSDT"]
    assert gap["candidate_markets"] == ["futures", "spot"]


def test_warn_budget_preserves_binance_activity_gap_pressure() -> None:
    prompt = {
        "proactive_decision_pressure": {
            "version": "binance_proactive_decision_pressure_v1",
            "status": "action_required",
            "pressure_level": "high",
            "zero_action_streak": 5,
            "previous_error_streak": 5,
            "binance_zero_action_streak": 7,
            "binance_previous_error_streak": 2,
            "effective_zero_action_streak": 7,
            "pressure_source": "binance_activity_gap",
            "candidate_count": 8,
            "strong_candidate_count": 8,
            "growth_governor_mode": "edge_rebuild",
            "growth_governor_allows_new_blocks": True,
            "live_grade": "insufficient",
            "previous_hold_summary": "관망",
            "top_candidates": [
                {"symbol": "KRW-SOL", "market": "upbit_spot", "reason": "x" * 20_000}
            ],
            "required_resolution": "resolve at least one candidate",
            "response_contract": {"action_required": "cite exact blocker"},
            "allowed_resolutions": ["small_probe"],
            "binance_market_activity_gap": {
                "version": "binance_market_activity_gap_v1",
                "status": "stale_binance_entries",
                "latest_binance_entry_at": "2026-07-03T05:08:52+00:00",
                "latest_binance_entry_market": "futures",
                "latest_upbit_entry_at": "2026-07-06T08:35:20+00:00",
                "binance_entry_stale_hours": 72.0,
                "binance_candidate_count": 3,
                "candidate_markets": ["futures", "spot"],
                "candidate_symbols": ["ESPUSDT", "BTCUSDT"],
            },
        }
    }

    compact_manager_sections_for_warn_budget(prompt, warn_chars=10_000)

    pressure = prompt["proactive_decision_pressure"]
    assert pressure["binance_zero_action_streak"] == 7
    assert pressure["effective_zero_action_streak"] == 7
    assert pressure["pressure_source"] == "binance_activity_gap"
    gap = prompt["proactive_decision_pressure"]["binance_market_activity_gap"]
    assert gap["status"] == "stale_binance_entries"
    assert gap["candidate_symbols"] == ["ESPUSDT", "BTCUSDT"]
    assert gap["candidate_markets"] == ["futures", "spot"]


def test_prompt_budget_candidate_emergency_compaction_keeps_binance_lanes() -> None:
    prompt = {
        "critical_response_contract": {"lane_review": {"required": True}},
        "task": "manage Binance blocks " + ("T" * 60_000),
        "candidates": [
            {
                "symbol": f"KRW-{idx:03d}",
                "market": "upbit_spot",
                "side": "long",
                "entry_price": 1000 + idx,
                "target_price": 1040 + idx,
                "stop_price": 980 + idx,
                "reason_md": "upbit candidate " * 80,
            }
            for idx in range(24)
        ]
        + [
            {
                "symbol": "BTCUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "target_price": 104.0,
                "stop_price": 98.0,
                "reason_md": "binance spot candidate " * 80,
            },
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 200.0,
                "target_price": 208.0,
                "stop_price": 196.0,
                "reason_md": "binance futures long candidate " * 80,
            },
            {
                "symbol": "SOLUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 50.0,
                "target_price": 48.0,
                "stop_price": 51.0,
                "reason_md": "binance futures short candidate " * 80,
            },
        ],
        "memory": {"blob": "M" * 80_000},
    }

    enforce_prompt_budget(prompt, max_chars=10_000)
    rows = (
        prompt["candidates"]["items"]
        if isinstance(prompt.get("candidates"), dict)
        else prompt["candidates"]
    )
    lanes = {
        f"{row.get('market')}:{row.get('side')}"
        for row in rows
        if isinstance(row, dict)
    }
    selected_symbols = {
        row.get("symbol") for row in rows if isinstance(row, dict)
    }

    assert {"spot:long", "futures:long", "futures:short"}.issubset(lanes)
    assert "KRW-000" in selected_symbols


def test_binance_manager_latency_recovery_preserves_ephemeral_thread_mode() -> None:
    prompt = {
        "native_thread_mode": "ephemeral",
        "critical_response_contract": {"lane_review": {"required": True}},
        "native_output_schema": {
            "create_blocks": [{"symbol": "BTCUSDT", "entry_price": "required"}],
            "lane_review": {"required": "mandatory"},
        },
        "jue_workflow": {"workflow_id": "binance_cycle"},
        "candidates": [{"symbol": "BTCUSDT", "market": "futures"}],
    }
    guard = {
        "version": "binance_manager_latency_guard_v1",
        "active": True,
        "reason": "recent_manager_timeout",
        "recent_timeout_count": 6,
        "target_chars": 18_000,
    }

    recovered = latency_recovery_core_prompt(
        prompt,
        latency_guard=guard,
        original_chars=300_000,
        label="binance_manager_latency_guard_core",
    )

    assert recovered["native_thread_mode"] == "ephemeral"


def test_binance_manager_latency_recovery_preserves_activity_gap() -> None:
    prompt = {
        "native_thread_mode": "ephemeral",
        "critical_response_contract": {"lane_review": {"required": True}},
        "native_output_schema": {
            "create_blocks": [{"symbol": "BTCUSDT", "entry_price": "required"}],
            "lane_review": {"required": "mandatory"},
        },
        "execution_gate": {"status": "ok"},
        "account": {"status": "ok", "cash_usdt": 1000.0},
        "proactive_decision_pressure": {
            "version": "binance_proactive_decision_pressure_v1",
            "status": "action_required",
            "pressure_level": "high",
            "zero_action_streak": 5,
            "previous_error_streak": 5,
            "binance_zero_action_streak": 7,
            "binance_previous_error_streak": 2,
            "effective_zero_action_streak": 7,
            "pressure_source": "binance_activity_gap",
            "candidate_count": 8,
            "strong_candidate_count": 8,
            "growth_governor_mode": "edge_rebuild",
            "growth_governor_allows_new_blocks": True,
            "live_grade": "insufficient",
            "previous_hold_summary": "관망",
            "binance_market_activity_gap": {
                "version": "binance_market_activity_gap_v1",
                "status": "stale_binance_entries",
                "latest_binance_entry_at": "2026-07-03T05:08:52+00:00",
                "latest_binance_entry_market": "futures",
                "latest_upbit_entry_at": "2026-07-06T08:35:20+00:00",
                "binance_entry_stale_hours": 72.0,
                "binance_candidate_count": 3,
                "candidate_markets": ["futures", "spot"],
                "candidate_symbols": ["ESPUSDT", "BTCUSDT"],
                "manager_instruction": (
                    "prefer at least one Binance spot/futures waiting probe"
                ),
            },
            "top_candidates": [
                {"symbol": "KRW-SOL", "market": "upbit_spot", "reason": "x" * 200}
            ],
        },
    }
    guard = {
        "version": "binance_manager_latency_guard_v1",
        "active": True,
        "reason": "recent_manager_timeout",
        "recent_timeout_count": 6,
        "target_chars": 18_000,
    }

    recovered = latency_recovery_core_prompt(
        prompt,
        latency_guard=guard,
        original_chars=300_000,
        label="binance_manager_latency_guard_core",
    )

    gap = recovered["proactive_decision_pressure"]["binance_market_activity_gap"]
    pressure = recovered["proactive_decision_pressure"]
    assert pressure["binance_zero_action_streak"] == 7
    assert pressure["effective_zero_action_streak"] == 7
    assert pressure["pressure_source"] == "binance_activity_gap"
    assert gap["status"] == "stale_binance_entries"
    assert gap["candidate_symbols"] == ["ESPUSDT", "BTCUSDT"]
    assert gap["candidate_markets"] == ["futures", "spot"]


def test_apply_binance_manager_latency_guard_compacts_priority_policy_context() -> None:
    marker = "BINANCE_MANAGER_LATENCY_GUARD_MODULE_BLOAT"
    prompt = {
        "decision_inputs": ["account"],
        "candidates": [
            {
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "reason_md": marker * 20,
            }
            for idx in range(30)
        ],
        "blocks": [{"symbol": "T40USDT", "thesis": marker * 20}],
        "candidate_policy_impacts": {
            "_global": [{"policy_id": "global", "decision_guidance": marker * 12}],
            **{
                f"T{idx:02d}USDT": [
                    {
                        "policy_id": f"T{idx:02d}-policy",
                        "decision_guidance": marker * 12,
                        "effect": {"entry_bias": "wait_for_cleaner_book"},
                    }
                ]
                for idx in range(60)
            },
        },
    }
    guard = {
        "version": "binance_manager_latency_guard_v1",
        "active": True,
        "reason": "recent_manager_timeout",
        "recent_timeout_count": 1,
        "target_chars": 10_000,
    }

    apply_manager_latency_guard(
        prompt,
        latency_guard=guard,
        target_chars=10_000,
    )

    policy_payload = json.dumps(prompt["candidate_policy_impacts"], ensure_ascii=False)
    assert prompt["latency_guard"]["active"] is True
    assert prompt["latency_guard"]["reason"] == "recent_manager_timeout"
    assert "latency_guard" in prompt["decision_inputs"]
    assert "T00USDT" in prompt["candidate_policy_impacts"]
    assert "T59USDT" not in prompt["candidate_policy_impacts"]
    assert marker not in policy_payload
    assert prompt["latency_guard"]["chars_after"] <= prompt["latency_guard"]["chars_before"]


def test_compact_manager_response_payload_keeps_decision_fields_only() -> None:
    marker = "BINANCE_MANAGER_RESPONSE_PAYLOAD_BLOAT"
    compact = compact_manager_response_payload(
        {
            "payload": {
                "decision": "hold_watch",
                "symbol": "BTCUSDT",
                "market_or_account_scope": "futures",
                "claim": "BTC 숏은 반등 확인 전까지 대기",
                "reasons": [marker * 20, "book depth gap"],
                "next_actions": ["wait for trigger"],
                "risks": ["short squeeze"],
                "data_gaps": ["funding unavailable"],
                "ignored_raw_trace": marker * 200,
            }
        }
    )

    encoded = json.dumps(compact, ensure_ascii=False)
    assert compact["decision"] == "hold_watch"
    assert compact["symbol"] == "BTCUSDT"
    assert compact["claim"] == "BTC 숏은 반등 확인 전까지 대기"
    assert "ignored_raw_trace" not in compact
    assert marker not in encoded


def test_manager_run_diagnostics_tags_no_action_blockers() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "prompt_budget": {"over_warn": True},
            "growth_governor": {
                "mode": "edge_rebuild",
                "scope": "lane_aware",
                "require_waiting_entry": True,
            },
            "growth_unlock": {"phase": "rebuilding"},
            "live_authority": {
                "live_grade": "observe_only",
                "allow_scale_up": False,
            },
            "performance": {
                "avg_r_multiple": -0.4,
                "realized_pnl_usdt": -1.2,
            },
            "candidate_generation": {
                "stage_counts": {"research": 12, "executable": 2},
            },
            "proactive_decision_pressure": {
                "status": "action_required",
                "pressure_level": "high",
                "zero_action_streak": 2,
            },
            "jue_wiki_repair_contract": {
                "repair_priority_count": 2,
                "top_priorities": [
                    {
                        "page_id": "binance.symbol.ETHUSDT",
                        "repair_action": "revise entry/exit design",
                    }
                ],
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                    "repair_now": {
                        "component": "repair_learning_resolution_metrics",
                        "action_type": "repair_price_geometry",
                    },
                },
            },
            "validation_repair": {
                "status": "active_caution",
                "repair_item_count": 1,
                "constraint_count": 1,
                "hard_filter": False,
            },
            "candidates": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "observe_only 대기",
            "reasons": [
                "BNBUSDT spot long 후보는 pattern_prior가 부족합니다.",
                "futures:short 레인이 과밀하고 최근 성과가 약합니다.",
            ],
            "data_gaps": ["orderbook_depth_usdt가 0입니다."],
        },
    )

    assert diagnostics["action_count"] == 0
    assert diagnostics["candidate_count"] == 2
    assert diagnostics["prompt_over_warn"] is True
    assert diagnostics["blocker_tags"]["edge_rebuild"] >= 2
    assert diagnostics["blocker_tags"]["unresolved_proactive_pressure"] >= 4
    assert diagnostics["blocker_tags"]["waiting_entry_required"] >= 1
    assert diagnostics["blocker_tags"]["observe_only"] >= 2
    assert diagnostics["blocker_tags"]["pattern_prior_missing"] >= 2
    assert diagnostics["blocker_tags"]["lane_concentration"] >= 2
    assert diagnostics["blocker_tags"]["weak_recent_edge"] >= 2
    assert diagnostics["blocker_tags"]["book_depth_gap"] >= 1
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_repair_priorities"] >= 3
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_attention_plan"] >= 3
    assert diagnostics["blocker_tags"]["unresolved_validation_repair_probe"] >= 3
    assert diagnostics["proactive_pressure_status"] == "action_required"
    assert diagnostics["proactive_pressure_level"] == "high"
    assert diagnostics["jue_wiki_repair_priority_count"] == 2
    assert diagnostics["jue_wiki_attention_status"] == "active"
    assert diagnostics["jue_wiki_attention_must_address"] == ["repair_now"]
    assert diagnostics["jue_wiki_attention_resolution_status"] == "unresolved"
    assert diagnostics["validation_repair_item_count"] == 1
    assert diagnostics["validation_repair_constraint_count"] == 1
    assert diagnostics["top_blockers"][0]["tag"] in diagnostics["blocker_tags"]


def test_binance_manager_run_diagnostics_ignores_kis_scoped_operational_pressure() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "growth_governor": {
                "target_scope": "kis",
                "mode": "edge_rebuild",
                "require_waiting_entry": True,
            },
            "growth_unlock": {
                "target_scope": "kis",
                "phase": "rebuilding",
            },
            "live_authority": {
                "target_scope": "kis",
                "live_grade": "observe_only",
                "allow_scale_up": False,
            },
            "performance": {
                "target_scope": "kis",
                "avg_r_multiple": -0.4,
                "realized_pnl_usdt": -1.2,
            },
            "candidate_generation": {
                "target_scope": "kis",
                "stage_counts": {"research": 99, "executable": 0},
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "ETHUSDT는 Binance 전용 트리거 재확인",
            "reasons": ["book and funding cross-check pending"],
        },
    )

    blocker_tags = diagnostics["blocker_tags"]
    assert "edge_rebuild" not in blocker_tags
    assert "waiting_entry_required" not in blocker_tags
    assert "observe_only" not in blocker_tags
    assert "scale_up_disabled" not in blocker_tags
    assert "weak_recent_edge" not in blocker_tags
    assert "recent_pnl_negative" not in blocker_tags
    assert diagnostics["stage_counts"] == {}
    assert diagnostics["growth_governor_mode"] is None
    assert diagnostics["growth_unlock_phase"] is None
    assert diagnostics["live_authority_grade"] is None


def test_manager_run_diagnostics_tags_unresolved_wiki_repair_action_batches() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_repair_contract": {
                "status": "active",
                "action_batches": [
                    {
                        "scope": "binance",
                        "action_type": "refresh_symbol_research",
                        "count": 7,
                        "symbols": ["NEARUSDT", "LTCUSDT"],
                    }
                ],
            },
            "candidates": [{"symbol": "NEARUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_repair_action_batches"] >= 3
    assert diagnostics["jue_wiki_repair_action_batch_count"] == 1


def test_manager_run_diagnostics_tags_hidden_wiki_repair_action_batch_pressure() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_repair_contract": {
                "status": "active",
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "action_batch_total_count": 15,
                    "action_batch_visible_pressure_count": 0,
                    "action_batch_omitted_count": 15,
                    "action_batch_pressure_visibility_ratio": 0.0,
                },
            },
            "candidates": [{"symbol": "NEARUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_repair_action_batches"] >= 3
    assert diagnostics["jue_wiki_repair_action_batch_count"] == 0
    assert diagnostics["jue_wiki_repair_action_batch_total_count"] == 15
    assert diagnostics["jue_wiki_repair_action_batch_omitted_count"] == 15
    assert diagnostics["jue_wiki_repair_action_batch_visible_pressure_count"] == 0
    assert diagnostics["jue_wiki_repair_action_batch_pressure_visibility_ratio"] == 0.0


def test_manager_run_diagnostics_preserves_additional_wiki_attention() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": [
                        "repair_now",
                        "probe_next",
                        "additional_attention",
                    ],
                    "repair_now": {
                        "component": "repair_learning_resolution_metrics",
                        "action_type": "repair_price_geometry",
                        "impacted_symbols": ["ETHUSDT"],
                    },
                    "probe_next": {
                        "component": "repair_success_criteria_metrics",
                        "action_type": "record_outcome_basis",
                        "impacted_symbols": ["BTCUSDT"],
                    },
                    "additional_attention": [
                        {
                            "component": "memory_card_quality",
                            "action_type": "cross_check_memory_card_quality",
                            "impacted_symbols": ["SOLUSDT"],
                            "missing_fields": ["durable_facts", "open_questions"],
                            "required_checks": [
                                (
                                    "refresh_durable_facts_from_reports_"
                                    "fundamentals_and_market_context"
                                ),
                                (
                                    "record_open_questions_and_data_gaps_"
                                    "before_confident_action"
                                ),
                            ],
                        }
                    ],
                }
            },
            "candidates": [{"symbol": "SOLUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={},
    )

    assert diagnostics["jue_wiki_attention_must_address"] == [
        "repair_now",
        "probe_next",
        "additional_attention",
    ]
    assert diagnostics["jue_wiki_attention_additional_attention"] == [
        {
            "component": "memory_card_quality",
            "action_type": "cross_check_memory_card_quality",
            "impacted_symbols": ["SOLUSDT"],
            "missing_fields": ["durable_facts", "open_questions"],
            "required_checks": [
                "refresh_durable_facts_from_reports_fundamentals_and_market_context",
                "record_open_questions_and_data_gaps_before_confident_action",
            ],
        }
    ]
    assert diagnostics["jue_wiki_attention_resolution_status"] == "unresolved"


def test_manager_run_diagnostics_distinguishes_wiki_attention_action_metadata() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "attention_plan_response_contract": {
                "status": "active",
                "must_address": ["repair_now"],
                "repair_now": {
                    "component": "wiki_attention",
                    "action_type": "live_probe",
                },
            }
        },
        "candidates": [{"symbol": "ETHUSDT"}],
    }
    unrelated = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "metadata": {"lane": "spot:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated action"},
    )
    resolved = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "metadata": {
                        "jue_wiki_repair_attention": {
                            "resolution": (
                                "action_metadata_records_repair_attention"
                            ),
                            "component": "wiki_attention",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "attention metadata recorded"},
    )

    assert unrelated["jue_wiki_attention_resolution_status"] == "unresolved"
    assert unrelated["blocker_tags"]["unresolved_jue_wiki_attention_plan"] >= 3
    assert resolved["jue_wiki_attention_resolution_status"] == "action_metadata"
    assert "unresolved_jue_wiki_attention_plan" not in resolved["blocker_tags"]


def test_manager_run_diagnostics_rejects_attention_metadata_without_active_repair_reference() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                },
                "repair_priority_count": 9,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:binance:ETHUSDT",
                        "symbols": ["ETHUSDT"],
                    }
                ],
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "metadata": {
                        "jue_wiki_repair_attention": {
                            "resolution": "action_metadata_records_repair_attention",
                            "component": "wiki_attention",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "attention metadata recorded"},
    )

    assert diagnostics["jue_wiki_attention_resolution_status"] == "unresolved"
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_attention_plan"] >= 3


def test_binance_manager_run_diagnostics_flags_unresolved_degraded_wiki_effectiveness() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
            "candidates": [{"symbol": "NEARUSDT"}],
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "degraded wiki still used"},
    )

    assert diagnostics["degraded_jue_wiki_effectiveness_count"] == 1
    assert diagnostics["degraded_jue_wiki_effectiveness_page_ids"] == [
        "binance.symbol.NEARUSDT"
    ]
    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "unresolved"
    )
    assert diagnostics["blocker_tags"]["unresolved_degraded_jue_wiki_effectiveness"] >= 3

    resolved = manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
            "candidates": [{"symbol": "NEARUSDT"}],
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_resolution": {
                            "page_id": "binance.symbol.NEARUSDT",
                            "symbol": "NEARUSDT",
                            "resolution": (
                                "degraded wiki used only after orderbook depth, "
                                "spread, and sizing reduction cross-check"
                            ),
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "degraded wiki cross-checked"},
    )

    assert resolved["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "action_metadata"
    )
    assert "unresolved_degraded_jue_wiki_effectiveness" not in resolved[
        "blocker_tags"
    ]


def test_binance_manager_contract_accepts_resolved_missing_from_model_contract_id() -> None:
    prompt = {
        "jue_wiki": {
            "pages": [
                {
                    "page_id": "binance.risk.trading_validation",
                    "effectiveness": {
                        "status": "degraded",
                        "reasons": ["aggregated_effectiveness_rows:3"],
                    },
                }
            ]
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {"upbit_orders_enabled": True},
        },
    }
    actions = {
        "create_blocks": [
            {
                "symbol": "KRW-SOL",
                "market": "upbit_spot",
                "side": "long",
                "metadata": {
                    "jue_wiki_repair_resolution": {
                        "source": "validation_repair_resolution",
                        "degraded_wiki_page_ids": [
                            "binance.risk.trading_validation"
                        ],
                        "resolution": {
                            "symbol": "KRW-SOL",
                            "market": "upbit_spot",
                            "resolution": "probe_waiting_block",
                            "memory_contract": (
                                "manager_contract_error.binance."
                                "validation_repair_resolution_missing_from_model"
                            ),
                            "memory_contract_resolution": (
                                "cite_memory_and_apply: validation_repair 요구에 "
                                "따라 waiting/probe, 소액, no scale-up으로 처리했다."
                            ),
                            "next_trigger": (
                                "121516.01 이하 대기 진입, 목표 123460.27, "
                                "손절 120543.89"
                            ),
                        },
                    }
                },
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "validation repair action metadata recorded"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "validation repair action metadata recorded"},
    )

    assert error == ""
    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "action_metadata"
    )


def test_binance_manager_diagnostics_accepts_degraded_wiki_action_resolution_with_negative_evidence_gap() -> None:
    prompt = {
        "jue_wiki": {
            "pages": [
                {
                    "page_id": "binance.risk.trading_validation",
                    "effectiveness": {
                        "status": "degraded",
                        "reasons": ["aggregated_effectiveness_rows:3"],
                    },
                }
            ]
        },
    }
    actions = {
        "create_blocks": [
            {
                "symbol": "ESPUSDT",
                "market": "futures",
                "side": "short",
                "metadata": {
                    "jue_wiki_repair_resolution": {
                        "source": "validation_repair_resolution",
                        "degraded_wiki_page_ids": [
                            "binance.risk.trading_validation"
                        ],
                        "resolution": {
                            "symbol": "ESPUSDT",
                            "market": "futures",
                            "resolution": "probe_waiting_block",
                            "evidence_gap": (
                                "cost_simulation/monte_carlo repair 미해결이라 "
                                "즉시·확대 진입은 불가"
                            ),
                            "memory_contract_resolution": (
                                "cite_memory_and_apply: 소액 대기 프로브로만 "
                                "검증수리 요구를 적용한다."
                            ),
                            "next_trigger": (
                                "가격 0.07156 이상, 스프레드 10bps 이내, "
                                "펀딩 급변 없음이면 대기 숏 프로브 유효"
                            ),
                        },
                    }
                },
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "validation repair action metadata recorded"},
    )

    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "action_metadata"
    )
    assert "unresolved_degraded_jue_wiki_effectiveness" not in diagnostics[
        "blocker_tags"
    ]


def test_binance_manager_run_diagnostics_rejects_negative_degraded_wiki_resolution() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
            "candidates": [{"symbol": "NEARUSDT"}],
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_resolution": {
                            "page_id": "binance.symbol.NEARUSDT",
                            "symbol": "NEARUSDT",
                            "resolution": (
                                "degraded wiki still unresolved for "
                                "binance.symbol.NEARUSDT; no orderbook depth, "
                                "spread, funding, or sizing reduction cross-check yet"
                            ),
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "degraded wiki still unresolved"},
    )

    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "unresolved"
    )
    assert diagnostics["blocker_tags"]["unresolved_degraded_jue_wiki_effectiveness"] >= 3


def test_binance_manager_run_diagnostics_ignores_kis_degraded_wiki_effectiveness() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "scope": "kis",
                        "symbols": ["005930"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["kr_equity_only_repair_queue_pressure"],
                        },
                    }
                ]
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "Binance action should ignore KIS-only wiki pressure"},
    )

    assert diagnostics["degraded_jue_wiki_effectiveness_count"] == 0
    assert "unresolved_degraded_jue_wiki_effectiveness" not in diagnostics[
        "blocker_tags"
    ]


def test_binance_manager_run_diagnostics_flags_unresolved_wiki_selection_guidance() -> None:
    prompt = {
        "memory": {
            "jue_wiki_selection_memory": {
                "status": "available",
                "target_scope": "binance",
                "items": [
                    {
                        "policy_id": (
                            "jue_wiki_selection.binance."
                            "operational_memory_manager_contract_recovery"
                        ),
                        "selected_page_ids": ["binance.ops.manager_runs"],
                        "application_guidance": {
                            "status": "freshness_repair_required",
                            "manager_instruction": (
                                "refresh_or_cross_check_selected_wiki_before_size_increase"
                            ),
                            "required_evidence": [
                                "fresh_jue_wiki_context",
                                "selection_audit_resolution",
                                "live_cross_check",
                            ],
                            "cross_check_page_ids": ["binance.ops.manager_runs"],
                        },
                    }
                ],
            }
        }
    }
    unresolved = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context refreshed for "
                            "binance.ops.manager_runs; live_cross_check uses "
                            "book, spread, funding, and quant"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert unresolved["jue_wiki_selection_guidance_status"] == "active"
    assert unresolved["jue_wiki_selection_guidance_resolution_status"] == "unresolved"
    assert unresolved["blocker_tags"]["unresolved_jue_wiki_selection_guidance"] >= 2
    assert resolved["jue_wiki_selection_guidance_resolution_status"] == (
        "action_metadata"
    )
    assert "unresolved_jue_wiki_selection_guidance" not in resolved["blocker_tags"]


def test_binance_manager_run_diagnostics_rejects_negative_wiki_selection_guidance_resolution() -> None:
    prompt = {
        "memory": {
            "jue_wiki_selection_memory": {
                "status": "available",
                "target_scope": "binance",
                "items": [
                    {
                        "policy_id": (
                            "jue_wiki_selection.binance."
                            "operational_memory_manager_contract_recovery"
                        ),
                        "selected_page_ids": ["binance.ops.manager_runs"],
                        "application_guidance": {
                            "status": "freshness_repair_required",
                            "manager_instruction": (
                                "refresh_or_cross_check_selected_wiki_before_size_increase"
                            ),
                            "required_evidence": [
                                "fresh_jue_wiki_context",
                                "selection_audit_resolution",
                                "live_cross_check",
                            ],
                            "cross_check_page_ids": ["binance.ops.manager_runs"],
                        },
                    }
                ],
            }
        }
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context missing for "
                            "binance.ops.manager_runs; no live_cross_check yet"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_selection_guidance_status"] == "active"
    assert (
        diagnostics["jue_wiki_selection_guidance_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_selection_guidance"] >= 2


def test_binance_manager_run_diagnostics_ignores_kis_wiki_selection_guidance() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "items": [
                        {
                            "policy_id": "jue_wiki_selection.kis.repair",
                            "selected_page_ids": ["kis.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "refresh KIS wiki before Korean equity sizing"
                                ),
                                "required_evidence": ["fresh_jue_wiki_context"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_selection_guidance_status"] == "inactive"
    assert (
        diagnostics["jue_wiki_selection_guidance_resolution_status"]
        == "inactive"
    )
    assert (
        "unresolved_jue_wiki_selection_guidance"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_does_not_resolve_translated_kis_selection_guidance_without_translation_evidence() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_selection.kis.translated",
                            "selected_page_ids": ["kis.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "translate KIS selection repair into a Binance "
                                    "candidate freshness check"
                                ),
                                "required_evidence": [
                                    "fresh_jue_wiki_context",
                                    "translated_crypto_mapping",
                                ],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context refreshed for kis.ops.manager_runs; "
                            "live_cross_check uses book, spread, funding, and quant"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_selection_guidance_status"] == "active"
    assert (
        diagnostics["jue_wiki_selection_guidance_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_selection_guidance"] >= 2


def test_binance_manager_run_diagnostics_resolves_translated_kis_selection_guidance_with_translation_evidence() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_selection.kis.translated",
                            "selected_page_ids": ["kis.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "translate KIS selection repair into a Binance "
                                    "candidate freshness check"
                                ),
                                "required_evidence": [
                                    "fresh_jue_wiki_context",
                                    "translated_crypto_mapping",
                                ],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_selection_resolution": (
                            "translated_crypto_mapping: KIS selection repair mapped "
                            "to Binance book, spread, funding, and quant freshness; "
                            "fresh_jue_wiki_context checked"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_selection_guidance_status"] == "active"
    assert (
        diagnostics["jue_wiki_selection_guidance_resolution_status"]
        == "action_metadata"
    )
    assert "unresolved_jue_wiki_selection_guidance" not in diagnostics["blocker_tags"]


def test_binance_manager_run_diagnostics_tracks_wiki_application_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "selection_run_id": "wiki-selection-binance-1",
        },
    }
    missing = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    referenced = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "evidence_refs": ["binance.symbol.NEARUSDT"],
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                            "checked against book, spread, funding, and quant"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert missing["jue_wiki_action_reference_status"] == "missing"
    assert missing["jue_wiki_action_reference_count"] == 0
    assert missing["jue_wiki_action_reference_ratio"] == 0.0
    assert missing["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1
    assert missing["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "NEARUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
        }
    ]
    assert referenced["jue_wiki_action_reference_status"] == "referenced"
    assert referenced["jue_wiki_action_reference_count"] == 1
    assert referenced["jue_wiki_action_reference_ratio"] == 1.0
    assert referenced["jue_wiki_action_reference_unscoped_page_ids"] == []
    assert referenced["jue_wiki_action_reference_missing_actions"] == []
    assert "missing_jue_wiki_action_reference" not in referenced["blocker_tags"]


def test_binance_manager_run_diagnostics_blocks_unscoped_wiki_action_reference() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={},
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "evidence_refs": ["binance.symbol.NEARUSDT"],
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context "
                            "binance.symbol.NEARUSDT checked"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "unscoped"
    assert diagnostics["jue_wiki_action_reference_count"] == 1
    assert diagnostics["jue_wiki_action_reference_ratio"] == 1.0
    assert diagnostics["jue_wiki_action_reference_unscoped_page_ids"] == [
        "binance.symbol.NEARUSDT".lower()
    ]
    assert diagnostics["jue_wiki_action_reference_unscoped_page_omitted_count"] == 0
    assert diagnostics["blocker_tags"]["unscoped_jue_wiki_action_reference"] == 1


def test_binance_manager_run_diagnostics_caps_unscoped_wiki_page_ids() -> None:
    actions = [
        {
            "symbol": f"ALT{index:03d}USDT",
            "market": "futures",
            "side": "long",
            "evidence_refs": [f"binance.symbol.ALT{index:03d}USDT"],
            "metadata": {"lane": "futures:long"},
        }
        for index in range(15)
    ]

    diagnostics = manager_run_diagnostics(
        prompt={},
        response={},
        actions={
            "create_blocks": actions,
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "unscoped"
    assert diagnostics["jue_wiki_action_reference_count"] == 15
    assert diagnostics["jue_wiki_action_reference_unscoped_page_ids"] == [
        f"binance.symbol.alt{index:03d}usdt" for index in range(12)
    ]
    assert diagnostics["jue_wiki_action_reference_unscoped_page_omitted_count"] == 3


def test_binance_manager_run_diagnostics_rejects_generic_wiki_action_reference_without_page_id() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "selection_run_id": "wiki-selection-binance-generic-reference",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "wiki checked for NEARUSDT against spread"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_required_trace_markers"] == [
        "binance.symbol.",
        "binance.ops.",
        "jue_wiki_action_reference_gap.",
    ]
    assert diagnostics["jue_wiki_action_reference_allowed_page_ids"] == [
        "binance.symbol.nearusdt"
    ]
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_wiki_action_reference_when_block_symbol_unresolved() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "binance-block-missing-symbol",
                    "reason": "위키 근거 청산",
                    "metadata": {
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                            "checked against spread"
                        )
                    },
                }
            ],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "close_blocks",
            "block_id": "binance-block-missing-symbol",
            "reason": "위키 근거 청산",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_gap_marker_as_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.ops.manager_runs",
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.ops.manager_runs"],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "jue_wiki_action_reference_gap.binance.missing "
                            "보정 필요를 확인"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_unselected_wiki_action_reference_page_id() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "symbols": ["BTCUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.BTCUSDT"],
            "selection_run_id": "wiki-selection-binance-unselected-reference",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                            "checked against spread"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_allowed_page_ids"] == [
        "binance.symbol.btcusdt"
    ]
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_page_present_but_not_selected() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "symbols": ["BTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.BTCUSDT"],
            "selection_run_id": "wiki-selection-binance-present-but-unselected",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                            "checked against spread"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_allowed_page_ids"] == [
        "binance.symbol.btcusdt"
    ]
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_requires_selected_symbol_page_when_available() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.ops.risk_gate",
            ],
            "selection_run_id": "wiki-selection-binance-symbol-page-required",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.ops.risk_gate "
                            "checked against spread and funding"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "NEARUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_requires_block_symbol_page_for_block_id_action() -> None:
    prompt = {
        "blocks": [
            {
                "block_id": "binance-block-near-1",
                "symbol": "NEARUSDT",
                "market": "futures",
                "side": "long",
                "status": "open",
            }
        ],
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.ops.risk_gate",
            ],
            "selection_run_id": "wiki-selection-binance-block-id-symbol-page-required",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "binance-block-near-1",
                    "reason": "risk check",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.ops.risk_gate "
                            "checked against spread and funding"
                        ),
                    },
                }
            ],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "close_blocks",
            "block_id": "binance-block-near-1",
            "symbol": "NEARUSDT",
            "lane": "futures:long",
            "reason": "risk check",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_ops_only_reference_for_symbol_action() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.ops.risk_gate"],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.ops.risk_gate "
                            "checked against spread and funding"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "NEARUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_checks_adopt_existing_block_wiki_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {
                        "lane": "spot:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                            "checked against spot wallet"
                        ),
                    },
                }
            ],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["action_count"] == 1
    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "adopt_existing_blocks",
            "symbol": "LTCUSDT",
            "market": "spot",
            "side": "long",
            "lane": "spot:long",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_checks_adopt_existing_block_usage_contract() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {
                        "lane": "spot:long",
                        "jue_wiki_usage_contract_resolution": (
                            "binance.symbol.NEARUSDT 위키는 단독 매매권한이 아니며 "
                            "live_spread/funding/orderbook_depth 교차확인 후 편입"
                        ),
                    },
                }
            ],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["action_count"] == 1
    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_ops_only_usage_contract_for_symbol_action() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.ops.risk_gate"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "binance.ops.risk_gate 위키는 단독 매매권한이 아니며 "
                            "live_spread/funding/orderbook_depth 교차확인 후 실행"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_tracks_wiki_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "selection_run_id": "wiki-selection-binance-usage-1",
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "liquidation_distance",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }
    missing = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 live_spread/funding/"
                            "liquidation_distance/orderbook_depth 교차확인 완료"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert missing["jue_wiki_usage_contract_status"] == "missing"
    assert missing["jue_wiki_usage_contract_required_terms"] == [
        "live_spread",
        "funding",
        "liquidation_distance",
        "orderbook_depth",
    ]
    assert missing["jue_wiki_usage_contract_resolution_count"] == 0
    assert missing["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert missing["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1
    assert resolved["jue_wiki_usage_contract_status"] == "resolved"
    assert resolved["jue_wiki_usage_contract_resolution_count"] == 1
    assert resolved["jue_wiki_usage_contract_resolution_ratio"] == 1.0
    assert "missing_jue_wiki_usage_contract_resolution" not in resolved["blocker_tags"]


def test_binance_manager_run_diagnostics_rejects_usage_contract_resolution_for_wrong_action_symbol() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "binance.symbol.NEARUSDT 위키는 단독 매매권한이 아니며 "
                            "live_spread/funding/orderbook_depth 교차확인 후 실행"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_mixed_wrong_symbol_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "binance.symbol.LTCUSDT and binance.symbol.NEARUSDT "
                            "are not standalone trade authority; live_spread/"
                            "funding/orderbook_depth cross-check completed"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_generic_usage_contract_resolution_in_multi_symbol_context() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_spread/funding/orderbook_depth 교차확인 후 실행"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_generic_usage_contract_resolution_for_unselected_action_symbol() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_spread/funding/orderbook_depth 교차확인 후 실행"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_usage_contract_resolution_when_block_symbol_unresolved() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "binance-block-missing-symbol",
                    "reason": "위키 근거 청산",
                    "metadata": {
                        "jue_wiki_usage_contract_resolution": (
                            "binance.symbol.NEARUSDT 위키는 단독 매매권한이 아니며 "
                            "live_spread/funding/orderbook_depth 교차확인 후 청산"
                        ),
                    },
                }
            ],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_generic_usage_contract_resolution_for_block_id_action() -> None:
    prompt = {
        "blocks": [
            {
                "block_id": "binance-block-nearusdt",
                "symbol": "NEARUSDT",
                "qty_open": 1,
            }
        ],
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "binance-block-nearusdt",
                    "reason": "위키 근거 청산",
                    "metadata": {
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_spread/funding/orderbook_depth 교차확인 후 청산"
                        ),
                    },
                }
            ],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_negative_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "selection_run_id": "wiki-selection-binance-usage-negative",
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "liquidation_distance",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "usage contract resolution missing; "
                            "no live_spread/funding/orderbook_depth cross checks"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_required_terms"] == [
        "live_spread",
        "funding",
        "liquidation_distance",
        "orderbook_depth",
    ]
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_rejects_generic_usage_contract_resolution_without_required_cross_checks() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "selection_run_id": "wiki-selection-binance-usage-generic",
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "liquidation_distance",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "usage contract reviewed before creating the block"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_tracks_usage_contract_memory_resolution() -> None:
    prompt = {
        "memory": {
            "status": "ok",
            "jue_wiki_usage_contract_memory": {
                "status": "available",
                "items": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                        },
                        "summary_md": (
                            "최근 블록이 위키 메모리를 썼지만 live spread, "
                            "funding, book, risk gate 확인을 남기지 않았다."
                        ),
                    }
                ],
            },
        }
    }

    missing = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "위키 메모리는 선행가설이며 live_spread/funding/"
                            "book/risk_gate 확인 후만 실행"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert missing["jue_wiki_usage_contract_status"] == "missing"
    assert missing["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1
    assert resolved["jue_wiki_usage_contract_status"] == "resolved"
    assert "missing_jue_wiki_usage_contract_resolution" not in resolved["blocker_tags"]


def test_binance_manager_run_diagnostics_rejects_generic_usage_contract_memory_resolution_without_required_cross_checks() -> None:
    prompt = {
        "memory": {
            "status": "ok",
            "jue_wiki_usage_contract_memory": {
                "status": "available",
                "items": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                            "required_cross_checks": [
                                "live_spread",
                                "funding",
                                "orderbook_depth",
                            ],
                        },
                    }
                ],
            },
        }
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "usage contract reviewed before creating the block"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_required_terms"] == [
        "live_spread",
        "funding",
        "orderbook_depth",
    ]
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_tracks_workflow_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
        },
        "jue_workflow": {
            "workflow_id": "binance_cycle",
            "contracts": [
                {
                    "contract_id": "jue_wiki_usage_contract_resolution",
                    "required_metadata": "jue_wiki_usage_contract_resolution",
                }
            ],
        },
    }

    missing = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_usage_contract_resolution": (
                            "workflow contract 기준으로 위키는 선행가설이며 "
                            "live_spread/funding/book/risk_gate 확인 후 실행"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert missing["jue_wiki_usage_contract_status"] == "missing"
    assert missing["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1
    assert resolved["jue_wiki_usage_contract_status"] == "resolved"
    assert "missing_jue_wiki_usage_contract_resolution" not in resolved["blocker_tags"]


def test_binance_manager_run_diagnostics_requires_usage_contract_resolution_for_hold_decision() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    missing = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "위키 기반으로 관망",
        },
    )
    resolved = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "위키 기반으로 관망",
            "metadata": {
                "jue_wiki_usage_contract_resolution": (
                    "관망 판단에도 위키는 선행가설이며 live_spread/"
                    "funding/orderbook_depth 교차확인 후 신규 블록 보류"
                )
            },
        },
    )

    assert missing["jue_wiki_usage_contract_status"] == "missing"
    assert missing["jue_wiki_usage_contract_resolution_count"] == 0
    assert missing["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert missing["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1
    assert resolved["jue_wiki_usage_contract_status"] == "resolved"
    assert resolved["jue_wiki_usage_contract_resolution_count"] == 1
    assert resolved["jue_wiki_usage_contract_resolution_ratio"] == 1.0
    assert "missing_jue_wiki_usage_contract_resolution" not in resolved["blocker_tags"]


def test_binance_manager_run_diagnostics_partially_covers_multi_symbol_hold_usage_contract() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "선택 종목 위키를 보고 관망",
            "metadata": {
                "jue_wiki_usage_contract_resolution": (
                    "binance.symbol.NEARUSDT 위키는 선행가설로만 사용했고 "
                    "live_spread/funding/orderbook_depth 교차확인 후 관망"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "partial"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 1
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.5
    assert diagnostics["blocker_tags"]["partial_jue_wiki_usage_contract_resolution"] == 1


def test_binance_manager_run_diagnostics_rejects_unselected_page_in_hold_usage_contract() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "symbols": ["BTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_spread",
                        "funding",
                        "orderbook_depth",
                    ],
                }
            },
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "선택 종목 위키를 보고 관망",
            "metadata": {
                "jue_wiki_usage_contract_resolution": (
                    "binance.symbol.NEARUSDT/binance.symbol.LTCUSDT/"
                    "binance.symbol.BTCUSDT wiki is not standalone authority; "
                    "live_spread/funding/orderbook_depth cross-check completed"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_requires_wiki_action_reference_for_hold_decision() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.ops.risk_gate",
            ],
        },
    }

    missing = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "위키 기반 관망",
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context binance.ops.risk_gate "
                    "checked against spread"
                )
            },
        },
    )
    referenced = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "위키 기반 관망",
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                    "checked against spread and funding"
                )
            },
        },
    )

    assert missing["jue_wiki_action_reference_status"] == "missing"
    assert missing["jue_wiki_action_reference_count"] == 0
    assert missing["jue_wiki_action_reference_ratio"] == 0.0
    assert missing["jue_wiki_action_reference_missing_actions"] == [
        {"section": "hold_decision"}
    ]
    assert missing["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1
    assert referenced["jue_wiki_action_reference_status"] == "referenced"
    assert referenced["jue_wiki_action_reference_count"] == 1
    assert referenced["jue_wiki_action_reference_ratio"] == 1.0
    assert referenced["jue_wiki_action_reference_missing_actions"] == []


def test_binance_manager_run_diagnostics_partially_covers_multi_symbol_hold_decision() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "선택 종목 위키를 보고 관망",
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                    "checked against spread"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "partial"
    assert diagnostics["jue_wiki_action_reference_count"] == 1
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.5
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "hold_decision",
            "page_id": "binance.symbol.ltcusdt",
            "symbol": "LTCUSDT",
        }
    ]
    assert diagnostics["blocker_tags"]["partial_jue_wiki_action_reference"] == 1


def test_binance_manager_run_diagnostics_counts_symbolic_hold_reference_against_target_symbol() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "watch_symbols": ["NEARUSDT"],
            "data_gaps": ["진입 가격 구조 부족"],
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                    "checked against spread"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "referenced"
    assert diagnostics["jue_wiki_action_reference_count"] == 1
    assert diagnostics["jue_wiki_action_reference_ratio"] == 1.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == []
    assert "partial_jue_wiki_action_reference" not in diagnostics["blocker_tags"]


def test_binance_manager_run_diagnostics_rejects_wrong_symbol_page_in_symbolic_hold_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "watch_symbols": ["NEARUSDT"],
            "data_gaps": ["진입 가격 구조 부족"],
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context binance.symbol.LTCUSDT "
                    "checked against spread"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {"section": "hold_decision"}
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_uncovered_target_symbol_hold_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "watch_symbols": ["BTCUSDT"],
            "data_gaps": ["target symbol wiki not selected"],
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                    "checked against spread"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {"section": "hold_decision", "symbol": "BTCUSDT"}
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_unselected_page_in_hold_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "symbols": ["BTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "selection_run_id": "wiki-selection-binance-hold-unselected-page",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "reason": "선택 종목 위키를 보고 관망",
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context binance.symbol.NEARUSDT, "
                    "binance.symbol.LTCUSDT, and binance.symbol.BTCUSDT "
                    "checked against spread"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_blocks_partial_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "selection_run_id": "wiki-selection-binance-partial",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                            "checked against book, spread, funding, and quant"
                        ),
                    },
                },
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "metadata": {"lane": "futures:short"},
                },
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "partial"
    assert diagnostics["jue_wiki_action_reference_count"] == 1
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.5
    assert diagnostics["blocker_tags"]["partial_jue_wiki_action_reference"] == 1
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "LTCUSDT",
            "market": "futures",
            "side": "short",
            "lane": "futures:short",
        }
    ]
    assert "missing_jue_wiki_action_reference" not in diagnostics["blocker_tags"]


def test_binance_manager_run_diagnostics_rejects_wrong_symbol_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "selection_run_id": "wiki-selection-binance-wrong-symbol",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "metadata": {
                        "lane": "futures:short",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.NEARUSDT "
                            "checked against book, spread, funding, and quant"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_mixed_wrong_symbol_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                },
                {
                    "page_id": "binance.symbol.LTCUSDT",
                    "symbols": ["LTCUSDT"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": [
                "binance.symbol.NEARUSDT",
                "binance.symbol.LTCUSDT",
            ],
            "selection_run_id": "wiki-selection-binance-mixed-wrong-symbol",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context binance.symbol.LTCUSDT "
                            "and binance.symbol.NEARUSDT checked against spread"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_negative_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "symbols": ["NEARUSDT"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
            "selection_run_id": "wiki-selection-binance-negative-reference",
        },
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "lane": "futures:long",
                        "jue_wiki_reference_basis": (
                            "wiki missing for NEARUSDT; no fresh context available"
                        ),
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "NEARUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
        }
    ]


def test_binance_manager_contract_rejects_candidate_memory_hint_on_wrong_symbol_action() -> None:
    prompt = {
        "candidate_memory_hint_policy": {"required": True},
        "candidates": [
            {
                "symbol": "NEARUSDT",
                "memory_hint": {
                    "reasons": [
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ],
                    "risks": ["late chase failed repeatedly"],
                    "sources": ["binance.symbol.NEARUSDT.memory"],
                },
            }
        ],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "thesis": (
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ),
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "candidate_memory_hint_resolution_missing_from_model"


def test_binance_manager_contract_accepts_candidate_memory_hint_on_matching_action() -> None:
    prompt = {
        "candidate_memory_hint_policy": {"required": True},
        "candidates": [
            {
                "symbol": "NEARUSDT",
                "memory_hint": {
                    "reasons": [
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ],
                    "risks": ["late chase failed repeatedly"],
                    "sources": ["binance.symbol.NEARUSDT.memory"],
                },
            }
        ],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "thesis": (
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ),
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_binance_manager_contract_rejects_negative_candidate_memory_hint_resolution() -> None:
    prompt = {
        "candidate_memory_hint_policy": {"required": True},
        "candidates": [
            {
                "symbol": "NEARUSDT",
                "memory_hint": {
                    "reasons": [
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ],
                    "risks": ["late chase failed repeatedly"],
                    "sources": ["binance.symbol.NEARUSDT.memory"],
                },
            }
        ],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "thesis": (
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ),
                    "metadata": {
                        "memory_hint_resolution": (
                            "candidate memory hint not applied; no fresh crypto "
                            "memory context available for NEARUSDT"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "candidate_memory_hint_resolution_missing_from_model"


def test_binance_manager_run_diagnostics_tracks_candidate_memory_hint_resolution() -> None:
    prompt = {
        "candidate_memory_hint_policy": {"required": True},
        "candidates": [
            {
                "symbol": "NEARUSDT",
                "memory_hint": {
                    "reasons": [
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ],
                    "risks": ["late chase failed repeatedly"],
                    "sources": ["binance.symbol.NEARUSDT.memory"],
                },
            },
            {
                "symbol": "LTCUSDT",
                "memory_hint": {
                    "reasons": ["LTC memory prefers pullback reclaim over chase"],
                    "risks": ["spread widened during failed breakouts"],
                    "sources": ["binance.symbol.LTCUSDT.memory"],
                },
            },
        ],
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "thesis": (
                        "NEAR memory says prior squeeze setups only worked after reclaim"
                    ),
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    compact = compact_manager_diagnostics_for_storage(diagnostics)

    assert diagnostics["candidate_memory_hint_status"] == "partial"
    assert diagnostics["candidate_memory_hint_count"] == 2
    assert diagnostics["candidate_memory_hint_resolved_count"] == 1
    assert diagnostics["candidate_memory_hint_unresolved_count"] == 1
    assert diagnostics["candidate_memory_hint_missing_symbols"] == ["LTCUSDT"]
    assert diagnostics["blocker_tags"]["unresolved_candidate_memory_hint"] == 1
    assert compact["candidate_memory_hint_status"] == "partial"
    assert compact["candidate_memory_hint_missing_symbols"] == ["LTCUSDT"]


def test_binance_manager_run_diagnostics_exposes_wiki_action_reference_recovery() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 4,
                    "resolved_count": 6,
                    "total_count": 10,
                    "recovery_ratio": 0.6,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_memory_scope"] == "binance"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 4
    assert diagnostics["jue_wiki_action_reference_recovery_resolved_count"] == 6
    assert diagnostics["jue_wiki_action_reference_recovery_total_count"] == 10
    assert diagnostics["jue_wiki_action_reference_recovery_ratio"] == 0.6
    assert (
        diagnostics["jue_wiki_action_reference_recovery_latest_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_latest_status"] == "missing"
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 4
    )
    assert diagnostics["top_blockers"][0]["tag"] == (
        "unresolved_jue_wiki_action_reference_recovery"
    )


def test_binance_manager_run_diagnostics_blocks_compacted_unresolved_wiki_recovery() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "unresolved",
                    "memory_scope": "binance",
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_ignores_kis_wiki_recovery_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
                    "open_gap_count": 3,
                    "resolved_count": 0,
                    "total_count": 3,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert "jue_wiki_action_reference_recovery_status" not in diagnostics
    assert (
        "unresolved_jue_wiki_action_reference_recovery"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_ignores_kis_wiki_recovery_target_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "target_scope": "kis",
                    "open_gap_count": 3,
                    "resolved_count": 0,
                    "total_count": 3,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert "jue_wiki_action_reference_recovery_status" not in diagnostics
    assert (
        "unresolved_jue_wiki_action_reference_recovery"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_blocks_item_only_unresolved_wiki_recovery() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "unresolved"
    assert diagnostics["jue_wiki_action_reference_recovery_memory_scope"] == "binance"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert diagnostics["jue_wiki_action_reference_recovery_total_count"] == 1
    assert (
        diagnostics["jue_wiki_action_reference_recovery_latest_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_ignores_kis_item_only_wiki_recovery_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "kis.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert "jue_wiki_action_reference_recovery_status" not in diagnostics
    assert (
        "unresolved_jue_wiki_action_reference_recovery"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_ignores_kis_action_reference_memory_items() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "kis.action_reference_required"
                            ),
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference KIS wiki evidence before "
                                    "creating a Korean equity block"
                                )
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "inactive"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "inactive"
    )
    assert (
        "unresolved_jue_wiki_action_reference_memory"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_ignores_kis_action_reference_memory_container_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.missing",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference scoped wiki evidence before creating "
                                    "a block"
                                ),
                                "required_evidence": ["scoped_wiki_page_id"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "inactive"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "inactive"
    )
    assert (
        "unresolved_jue_wiki_action_reference_memory"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_accepts_translated_kis_action_reference_container_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.kis.translated",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate KIS pullback lesson into a Binance "
                                    "waiting-entry check"
                                ),
                                "required_evidence": ["translated_crypto_mapping"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )


def test_binance_manager_run_diagnostics_accepts_translated_kis_action_reference_item_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "scope": "kis",
                            "transferability": "translated",
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "kis.translated_pullback"
                            ),
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate KIS chase-avoid lesson into Binance "
                                    "spread/funding confirmation"
                                ),
                                "required_evidence": ["translated_crypto_mapping"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )


def test_binance_manager_run_diagnostics_does_not_resolve_translated_kis_memory_without_translation_evidence() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.kis.translated",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate KIS pullback lesson into a Binance "
                                    "waiting-entry check"
                                ),
                                "required_evidence": ["translated_crypto_mapping"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap.kis.translated reused "
                            "from KIS pullback memory after spread and funding check"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )


def test_binance_manager_run_diagnostics_requires_default_translation_evidence_when_translated_memory_omits_required_evidence() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.kis.translated",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate KIS pullback lesson into a Binance "
                                    "waiting-entry check"
                                )
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap.kis.translated reused "
                            "from KIS pullback memory after spread and funding check"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )


def test_binance_manager_run_diagnostics_resolves_translated_kis_memory_with_translation_evidence() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.kis.translated",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate KIS pullback lesson into a Binance "
                                    "waiting-entry check"
                                ),
                                "required_evidence": ["translated_crypto_mapping"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "translated_crypto_mapping: KIS pullback lesson mapped "
                            "to NEARUSDT spread, funding, and orderbook depth "
                            "before a waiting-entry block"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "action_metadata"
    )
    assert (
        "unresolved_jue_wiki_action_reference_memory"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_accepts_crypto_action_reference_container_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "crypto",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.missing",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference crypto wiki evidence before creating "
                                    "a Binance block"
                                ),
                                "required_evidence": ["crypto_wiki_page_id"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )


def test_binance_manager_run_diagnostics_accepts_futures_action_reference_item_scope() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "market": "futures",
                            "policy_id": "jue_wiki_action_reference_gap.missing",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference futures wiki evidence before creating "
                                    "a futures block"
                                ),
                                "required_evidence": ["futures_wiki_page_id"],
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )


def test_binance_manager_run_diagnostics_does_not_resolve_binance_memory_with_kis_item_reference() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.action_reference_required"
                            ),
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference crypto wiki evidence before "
                                    "creating a Binance block"
                                ),
                                "required_evidence": ["crypto_wiki_page_id"],
                            },
                        },
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "kis.action_reference_required"
                            ),
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference KIS wiki evidence before creating "
                                    "a Korean equity block"
                                ),
                                "required_evidence": ["kr_equity_wiki_page_id"],
                            },
                        },
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap."
                            "kis.action_reference_required "
                            "kr_equity_wiki_page_id checked"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )


def test_binance_manager_run_diagnostics_resolves_recovery_guidance_with_recovery_metadata() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                            "recovered with fresh wiki, orderbook, funding, and quant"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "action_metadata"
    )
    assert diagnostics["jue_wiki_action_reference_status"] == "inactive"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert (
        "unresolved_jue_wiki_action_reference_memory"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_rejects_unselected_page_in_recovery_metadata() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["binance.symbol.NEARUSDT"],
            },
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                },
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                            "recovered with fresh wiki binance.symbol.NEARUSDT and "
                            "binance.symbol.BTCUSDT, orderbook, funding, and quant"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_rejects_wrong_selected_page_in_recovery_metadata() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": [
                    "binance.symbol.NEARUSDT",
                    "binance.symbol.LTCUSDT",
                ],
            },
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                },
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                            "recovered with fresh wiki binance.symbol.NEARUSDT, "
                            "orderbook, funding, and quant"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_rejects_page_idless_recovery_in_multi_symbol_context() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": [
                    "binance.symbol.NEARUSDT",
                    "binance.symbol.LTCUSDT",
                ],
            },
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                },
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                            "recovered with fresh wiki, orderbook, funding, and quant"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_rejects_unresolved_block_recovery_metadata() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["binance.symbol.NEARUSDT"],
            },
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                },
            },
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "unknown-binance-block",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                            "recovered with fresh wiki binance.symbol.NEARUSDT, "
                            "orderbook, funding, and quant"
                        )
                    },
                }
            ],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_keeps_recovery_gap_for_generic_wiki_metadata() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_memory"
                            ),
                            "latest_status": "missing",
                            "application_guidance": {
                                "manager_instruction": (
                                    "cite_fresh_wiki_or_record_non_wiki_basis"
                                )
                            },
                        }
                    ],
                },
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap.binance.unresolved_memory "
                            "evidence orderbook cross-check recorded"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "action_metadata"
    )
    assert diagnostics["jue_wiki_action_reference_status"] == "inactive"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_resolves_recovery_guidance_with_hold_metadata() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "신규 블록 없이 회복 근거만 점검",
            "metadata": {
                "jue_wiki_action_reference_recovery": (
                    "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                    "recovered with fresh wiki, orderbook, funding, and quant"
                )
            },
            "reasons": [
                "위키 회복 근거와 호가/펀딩/퀀트 교차검증은 완료했지만 진입 가격 구조는 아직 부족하다"
            ],
        },
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "hold_trigger"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "resolved"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 0
    assert (
        diagnostics["jue_wiki_action_reference_recovery_latest_resolution_status"]
        == "hold_trigger"
    )
    assert (
        "unresolved_jue_wiki_action_reference_memory"
        not in diagnostics["blocker_tags"]
    )
    assert (
        "unresolved_jue_wiki_action_reference_recovery"
        not in diagnostics["blocker_tags"]
    )


def test_binance_manager_run_diagnostics_rejects_page_idless_hold_recovery_with_selected_pages() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": [
                    "binance.symbol.NEARUSDT",
                    "binance.symbol.LTCUSDT",
                ],
            },
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                },
            },
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "신규 블록 없이 회복 근거만 점검",
            "metadata": {
                "jue_wiki_action_reference_recovery": (
                    "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                    "recovered with fresh wiki, orderbook, funding, and quant"
                )
            },
            "reasons": [
                "위키 회복 근거와 호가/펀딩/퀀트 교차검증은 완료했지만 진입 가격 구조는 아직 부족하다"
            ],
        },
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_rejects_wrong_selected_page_in_symbolic_hold_recovery() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": [
                    "binance.symbol.NEARUSDT",
                    "binance.symbol.LTCUSDT",
                ],
            },
            "memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
                    "open_gap_count": 1,
                    "resolved_count": 0,
                    "total_count": 1,
                    "recovery_ratio": 0.0,
                    "latest_resolution_status": "unresolved",
                    "latest_status": "missing",
                },
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_action_reference_gap."
                                "binance.unresolved_recovery"
                            ),
                            "latest_status": "no_actions",
                            "application_guidance": {
                                "manager_instruction": (
                                    "resolve_action_reference_recovery_"
                                    "before_next_decision"
                                )
                            },
                        }
                    ],
                },
            },
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "NEARUSDT 회복 근거만 점검",
            "watch_symbols": ["NEARUSDT"],
            "data_gaps": ["진입 가격 구조 부족"],
            "metadata": {
                "jue_wiki_action_reference_recovery": (
                    "jue_wiki_action_reference_gap.binance.unresolved_recovery "
                    "binance.symbol.LTCUSDT recovered with fresh wiki, "
                    "orderbook, funding, and quant"
                )
            },
            "reasons": [
                "NEARUSDT는 관망하지만 LTCUSDT 위키 근거로 회복했다고 기록했다"
            ],
        },
    )

    assert diagnostics["jue_wiki_action_reference_memory_status"] == "active"
    assert (
        diagnostics["jue_wiki_action_reference_memory_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "open_gaps"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 1
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_memory"]
        == 2
    )
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 1
    )


def test_binance_manager_run_diagnostics_accepts_hold_ops_wiki_evidence_refs() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["binance.ops.regime.squeeze"],
            },
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "summary": "스퀴즈 원칙 위키에 따라 신규 블록 보류",
            "evidence_refs": ["binance.ops.regime.squeeze"],
            "reasons": ["binance.ops.regime.squeeze 기준상 추격 진입보다 대기"],
        },
    )

    assert diagnostics["jue_wiki_action_reference_count"] == 1
    assert diagnostics["jue_wiki_action_reference_ratio"] == 1.0
    assert "missing_jue_wiki_action_reference" not in diagnostics["blocker_tags"]


def test_binance_manager_run_diagnostics_rejects_ops_only_reference_for_symbol_hold() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["binance.ops.regime.squeeze"],
            },
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "watch_symbols": ["NEARUSDT"],
            "data_gaps": ["target symbol wiki not selected"],
            "evidence_refs": ["binance.ops.regime.squeeze"],
            "reasons": [
                "NEARUSDT는 binance.ops.regime.squeeze 기준상 추격보다 대기"
            ],
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {"section": "hold_decision", "symbol": "NEARUSDT"}
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_binance_manager_run_diagnostics_rejects_ops_only_usage_contract_for_symbol_hold() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["binance.ops.regime.squeeze"],
                "trust_profile": {
                    "usage_contract": {
                        "standalone_trade_authority": False,
                        "required_cross_checks": [
                            "live_spread",
                            "funding",
                            "orderbook_depth",
                        ],
                    }
                },
            },
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "stance": "hold",
            "watch_symbols": ["NEARUSDT"],
            "data_gaps": ["target symbol wiki not selected"],
            "metadata": {
                "jue_wiki_usage_contract_resolution": (
                    "binance.ops.regime.squeeze 위키는 단독 매매권한이 아니며 "
                    "live_spread/funding/orderbook_depth 교차확인 후 관망"
                ),
            },
            "reasons": ["NEARUSDT 관망"],
        },
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_binance_manager_run_diagnostics_keeps_degraded_wiki_action_response_only_unresolved() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
            "candidates": [{"symbol": "NEARUSDT"}],
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "NEARUSDT",
                        "resolution": "small_waiting_block",
                        "next_trigger": (
                            "binance.symbol.NEARUSDT degraded wiki 교차확인 후 대기"
                        ),
                    }
                ]
            }
        },
        actions={
            "create_blocks": [
                {
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "metadata": {"lane": "futures:long"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response-only degraded wiki resolution"},
    )

    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "unresolved"
    )
    assert diagnostics["blocker_tags"]["unresolved_degraded_jue_wiki_effectiveness"] >= 3


def test_binance_manager_response_contract_rejects_create_for_non_visible_candidate_symbol() -> None:
    response = {
        "create_blocks": [
            {
                "symbol": "KRW-SOL",
                "market": "upbit_spot",
                "side": "long",
                "metadata": {
                    "jue_wiki_repair_resolution": {
                        "resolution": "probe_waiting_block"
                    }
                },
            }
        ],
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "KRW-SOL",
                    "market": "upbit_spot",
                    "resolution": "probe_waiting_block",
                    "next_trigger": "KRW-SOL pullback",
                }
            ]
        },
    }
    error = manager_response_contract_error(
        prompt={
            "candidates": [
                {"symbol": "ESPUSDT", "market": "futures", "side": "short"},
                {"symbol": "ZROUSDT", "market": "futures", "side": "long"},
                {"symbol": "KRW-ZRO", "market": "upbit_spot", "side": "long"},
            ],
            "validation_repair": {
                "status": "needs_repair",
                "repair_item_count": 1,
            },
        },
        response=response,
        actions={"create_blocks": response["create_blocks"]},
        hold_decision={},
    )

    assert error == "manager_create_candidate_not_visible"


def test_binance_manager_response_contract_allows_create_from_proactive_pressure_candidate() -> None:
    error = manager_response_contract_error(
        prompt={
            "candidates": [
                {"symbol": "ESPUSDT", "market": "futures", "side": "short"},
                {"symbol": "KRW-ZRO", "market": "upbit_spot", "side": "long"},
            ],
            "proactive_decision_pressure": {
                "status": "candidate_present",
                "top_candidates": [
                    {
                        "symbol": "KRW-SOL",
                        "market": "upbit_spot",
                        "side": "long",
                        "horizon": "short",
                        "entry_price": 120_000,
                        "target_price": 123_000,
                        "stop_price": 119_000,
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"upbit_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "KRW-SOL",
                    "market": "upbit_spot",
                    "side": "long",
                    "horizon": "short",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KRW-SOL pressure candidate selected"},
    )

    assert error == ""


def test_binance_manager_response_contract_allows_no_action_when_all_visible_candidates_rejected_with_repair_evidence() -> None:
    prompt = {
        "jue_wiki": {
            "pages": [
                {
                    "page_id": "binance.risk.trading_validation",
                    "effectiveness": {
                        "status": "degraded",
                        "reasons": ["samples:120", "status_mix:futures:degraded"],
                    },
                }
            ]
        },
        "jue_wiki_requested_symbol_coverage": {
            "status": "partial",
            "missing_summary_symbols": ["AVAXUSDT"],
            "prompt_omitted_symbols": ["AAVEUSDT", "AVAXUSDT"],
        },
        "validation_repair": {
            "status": "needs_repair",
            "repair_item_count": 1,
        },
        "candidates": [
            {"symbol": "ESPUSDT", "market": "futures", "side": "short"},
            {"symbol": "KRW-AVAX", "market": "upbit_spot", "side": "long"},
        ],
    }
    response = {
        "validation_repair_resolution": {
            "blanket_hold_allowed": False,
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "resolution": "candidate_rejected",
                    "evidence_gap": (
                        "stop risk exceeds validation max and pattern prior missing"
                    ),
                    "memory_contract_resolution": (
                        "reject_memory_with_reason: validation repair requires "
                        "cost/depth/funding evidence before a futures probe"
                    ),
                    "next_trigger": (
                        "entry >= 0.07156 with stop risk <= 3%, RR >= 2, "
                        "fresh depth and funding evidence"
                    ),
                },
                {
                    "symbol": "KRW-AVAX",
                    "market": "upbit_spot",
                    "resolution": "candidate_rejected",
                    "evidence_gap": (
                        "confidence below waiting threshold and RR below validation min"
                    ),
                    "memory_contract_resolution": (
                        "reject_memory_with_reason: degraded AVAX summary and weak "
                        "reward/risk keep the candidate in watch-only mode"
                    ),
                    "next_trigger": (
                        "waiting entry recalculated with confidence >= 0.58, "
                        "RR >= 2, and refreshed AVAX summary"
                    ),
                },
            ],
        }
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={},
    )

    assert error == ""
    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "response_resolution"
    )
    assert diagnostics["jue_wiki_requested_symbol_coverage_resolution_status"] == (
        "response_resolution"
    )


def test_binance_manager_response_contract_allows_candidate_rejection_with_existing_position_actions() -> None:
    prompt = {
        "jue_wiki": {
            "pages": [
                {
                    "page_id": "binance.risk.trading_validation",
                    "effectiveness": {
                        "status": "degraded",
                        "reasons": ["samples:120", "status_mix:futures:degraded"],
                    },
                }
            ]
        },
        "validation_repair": {
            "status": "needs_repair",
            "repair_item_count": 1,
        },
        "candidates": [
            {"symbol": "ESPUSDT", "market": "futures", "side": "short"},
            {"symbol": "HMSTRUSDT", "market": "futures", "side": "short"},
        ],
    }
    response = {
        "validation_repair_resolution": {
            "blanket_hold_allowed": False,
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "resolution": "candidate_rejected",
                    "evidence_gap": (
                        "pattern prior missing and stop risk exceeds validation cap"
                    ),
                    "memory_contract_resolution": (
                        "reject_memory_with_reason: keep the candidate watch-only "
                        "until validation repair evidence is refreshed"
                    ),
                    "next_trigger": (
                        "entry >= 0.07156 with stop risk <= 3%, RR >= 2, "
                        "fresh depth and funding evidence"
                    ),
                },
                {
                    "symbol": "HMSTRUSDT",
                    "market": "futures",
                    "resolution": "candidate_rejected",
                    "evidence_gap": (
                        "stop risk exceeds validation cap and pattern prior is absent"
                    ),
                    "memory_contract_resolution": (
                        "reject_memory_with_reason: do not create a volatile probe "
                        "until the risk geometry is repaired"
                    ),
                    "next_trigger": (
                        "entry >= 0.000209 with stop risk <= 3%, RR >= 2, "
                        "and pattern prior confirmed"
                    ),
                },
            ],
        }
    }
    actions = {
        "adopt_existing_blocks": [
            {
                "symbol": "JTOUSDT",
                "market": "spot",
                "side": "long",
                "reason": "adopt unmanaged wallet dust into the ledger",
            }
        ],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [
            {
                "block_id": "xrp-stale",
                "reason": "close stale block below stop",
            }
        ],
        "pause_blocks": [],
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision={},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions=actions,
        hold_decision={},
    )

    assert error == ""
    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "response_resolution"
    )
    assert "unresolved_degraded_jue_wiki_effectiveness" not in diagnostics[
        "blocker_tags"
    ]


def test_binance_manager_response_contract_allows_no_action_with_price_geometry_update_repair_evidence() -> None:
    prompt = {
        "jue_wiki": {
            "pages": [
                {
                    "page_id": "binance.risk.trading_validation",
                    "effectiveness": {
                        "status": "degraded",
                        "reasons": ["samples:120"],
                    },
                }
            ]
        },
        "jue_wiki_requested_symbol_coverage": {
            "status": "partial",
            "missing_summary_symbols": ["AAVEUSDT"],
            "unsummarized_symbols": ["AAVEUSDT"],
        },
        "validation_repair": {
            "status": "needs_repair",
            "repair_item_count": 1,
        },
        "candidates": [
            {"symbol": "ESPUSDT", "market": "futures", "side": "short"},
            {"symbol": "KRW-ZRO", "market": "upbit_spot", "side": "long"},
        ],
    }
    response = {
        "validation_repair_resolution": {
            "blanket_hold_allowed": False,
            "resolved_candidates": [
                {
                    "symbol": "ESPUSDT",
                    "market": "futures",
                    "resolution": "updated_price_geometry",
                    "evidence_gap": (
                        "pattern_live_crosscheck=no_pattern_prior and stop risk "
                        "5.17% exceeds validation max_stop_risk_pct 3.0"
                    ),
                    "memory_contract_resolution": (
                        "wait_until_memory_refresh: validation repair 기준으로 "
                        "가격 구조만 재계산하고 신규 블록은 보류합니다."
                    ),
                    "next_trigger": (
                        "가격이 0.07307 이상이고 stop 0.075262 대비 손절폭이 "
                        "3% 이내, target 0.06397 기준 RR>=2이면 재검토"
                    ),
                },
                {
                    "symbol": "KRW-ZRO",
                    "market": "upbit_spot",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "confidence 0.47 below waiting threshold",
                    "memory_contract_resolution": (
                        "cite_memory_and_apply: memory contract를 반영해 저신뢰 "
                        "후보를 신규 블록으로 승격하지 않음."
                    ),
                    "next_trigger": (
                        "entry<=1499.51, confidence>=0.58, spread<=25bps이면 "
                        "소액 waiting probe 재검토"
                    ),
                },
            ],
        }
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={},
    )

    assert error == ""
    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "response_resolution"
    )
    assert diagnostics["jue_wiki_requested_symbol_coverage_resolution_status"] == (
        "response_resolution"
    )


def test_binance_manager_response_contract_rejects_unresolved_requested_symbol_coverage() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
                "prompt_omitted_symbols": ["SOLUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {
                    "spot_orders_enabled": True,
                    "futures_orders_enabled": True,
                },
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert error == "validation_repair_resolution_missing_from_model"

    resolved_error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "coverage follow-up staged",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "rebuild wiki summary or run live cross-check",
                }
            ],
        },
    )

    assert resolved_error == ""


def test_binance_manager_response_contract_rejects_requested_symbol_coverage_trigger_for_other_symbol() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "coverage follow-up staged",
            "watch_symbols": ["BTCUSDT"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "BTCUSDT",
                    "condition": "rebuild wiki summary or run live cross-check",
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_allows_validation_repair_hold_with_unrelated_requested_symbol_coverage() -> None:
    error = manager_response_contract_error(
        prompt={
            "validation_repair": {
                "scope": "binance",
                "status": "needs_repair",
                "repair_item_count": 1,
                "constraint_count": 1,
            },
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "hard_blocker": False,
                "prompt_omitted_symbols": ["AAVEUSDT", "ASTERUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ESPUSDT",
                        "market": "futures",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "pattern prior is missing and stop risk exceeds "
                            "the validation repair limit"
                        ),
                        "next_trigger": (
                            "wait for fresh pattern prior, spread <= 10bps, "
                            "and stop risk <= 3% before reconsidering"
                        ),
                    }
                ]
            },
            "hold_decision": {"summary": "관망"},
        },
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert error == ""


def test_binance_manager_response_contract_rejects_action_with_unresolved_requested_symbol_coverage() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.BTCUSDT",
                        "effectiveness": {"status": "degraded"},
                    }
                ]
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_repair_pressure": (
                            "binance.symbol.BTCUSDT degraded wiki pressure handled"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "created unrelated wiki repair action"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_allows_unrelated_action_for_non_hard_requested_symbol_coverage() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "hard_blocker": False,
                "missing_summary_symbols": ["ETHUSDT"],
                "prompt_omitted_symbols": ["SOLUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "created unrelated action"},
    )

    assert error == ""


def test_binance_manager_response_contract_keeps_hard_requested_symbol_coverage_blocking_unrelated_action() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "hard_blocker": True,
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "created unrelated action"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_response_contract_rejects_requested_symbol_coverage_resolution_on_wrong_action_symbol() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_requested_symbol_coverage_resolution": (
                            "ETHUSDT requested_symbol_summary gap handled by "
                            "live_cross_check before block sizing"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wrong symbol action metadata"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_binance_manager_ignores_kis_requested_symbol_coverage() -> None:
    prompt = {
        "jue_wiki_requested_symbol_coverage": {
            "target_scope": "kis",
            "status": "partial",
            "missing_summary_symbols": ["005930"],
            "prompt_omitted_symbols": ["000660"],
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "Binance judgment proceeds"}},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "Binance judgment proceeds"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "Binance judgment proceeds"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] is None
    assert diagnostics["jue_wiki_missing_summary_symbols"] == []
    assert "unresolved_jue_wiki_requested_symbol_coverage" not in diagnostics[
        "blocker_tags"
    ]


def test_binance_manager_response_contract_rejects_unresolved_memory_card_quality() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT"],
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    unresolved_error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "관망"}},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert unresolved_error == "validation_repair_resolution_missing_from_model"

    resolved_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "ETHUSDT thin memory card, require live cross-check",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "cross-check book, funding, quant, and crypto research",
                }
            ],
        },
    )

    assert resolved_error == ""


def test_binance_manager_response_contract_ignores_kis_memory_card_quality() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "target_scope": "kis",
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_kis_research_before_high_confidence",
                "symbols": ["005930"],
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "Binance judgment proceeds"}},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "Binance judgment proceeds"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "Binance judgment proceeds"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_memory_card_quality_status"] == "inactive"
    assert "unresolved_jue_wiki_memory_card_quality" not in diagnostics["blocker_tags"]


def test_binance_memory_card_quality_requires_specific_field_or_check_resolution() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
                "required_checks": [
                    "refresh_durable_facts_from_crypto_research_and_quant_context"
                ],
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }

    generic_hold_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "ETHUSDT thin memory card, observe for now",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "cross-check market context before entry",
                }
            ],
        },
    )

    assert generic_hold_error == "validation_repair_resolution_missing_from_model"

    specific_hold_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "ETHUSDT durable_facts gap requires crypto research refresh",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": (
                        "refresh_durable_facts_from_crypto_research_and_"
                        "quant_context 완료 후 재평가"
                    ),
                }
            ],
        },
    )

    assert specific_hold_error == ""


def test_binance_manager_run_diagnostics_tags_unresolved_requested_symbol_coverage() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["ETHUSDT"]


def test_binance_manager_run_diagnostics_clears_unrelated_non_hard_requested_symbol_coverage_action() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "hard_blocker": False,
                "missing_summary_symbols": ["ETHUSDT"],
                "prompt_omitted_symbols": ["SOLUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "created unrelated action"},
    )

    assert "unresolved_jue_wiki_requested_symbol_coverage" not in diagnostics[
        "blocker_tags"
    ]
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"


def test_binance_manager_run_diagnostics_keeps_hard_requested_symbol_coverage_blocking_unrelated_action() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "hard_blocker": True,
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "created unrelated action"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"


def test_binance_manager_run_diagnostics_reads_nested_jue_wiki_requested_symbol_coverage() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "target_scope": "binance",
                "requested_symbol_coverage": {
                    "status": "partial",
                    "missing_summary_symbols": ["ETHUSDT"],
                    "unsummarized_symbols": ["ETHUSDT"],
                },
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "coverage gap not addressed"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["ETHUSDT"]


def test_binance_manager_run_diagnostics_uses_nested_coverage_when_top_level_is_kis_scoped() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "target_scope": "kis",
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
            "jue_wiki": {
                "target_scope": "binance",
                "requested_symbol_coverage": {
                    "status": "partial",
                    "missing_summary_symbols": ["ETHUSDT"],
                    "unsummarized_symbols": ["ETHUSDT"],
                },
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "coverage gap not addressed"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["ETHUSDT"]


def test_binance_manager_run_diagnostics_uses_nested_coverage_when_top_level_has_unscoped_kis_symbols() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
            "jue_wiki": {
                "target_scope": "binance",
                "requested_symbol_coverage": {
                    "status": "partial",
                    "missing_summary_symbols": ["ETHUSDT"],
                    "unsummarized_symbols": ["ETHUSDT"],
                },
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "coverage gap not addressed"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["ETHUSDT"]


def test_binance_manager_run_diagnostics_clears_requested_symbol_coverage_when_trigger_matches_symbol() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "coverage follow-up staged",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "rebuild wiki summary or run live cross-check",
                }
            ],
        },
    )

    assert "unresolved_jue_wiki_requested_symbol_coverage" not in diagnostics[
        "blocker_tags"
    ]


def test_binance_manager_run_diagnostics_clears_requested_symbol_coverage_when_action_metadata_matches_symbol() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_requested_symbol_coverage_resolution": (
                            "ETHUSDT requested_symbol_summary gap handled by "
                            "live_cross_check before block sizing"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "action metadata records coverage resolution"},
    )

    assert "unresolved_jue_wiki_requested_symbol_coverage" not in diagnostics[
        "blocker_tags"
    ]
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"


def test_binance_manager_run_diagnostics_rejects_negative_requested_symbol_coverage_resolution() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {
                        "jue_wiki_requested_symbol_coverage_resolution": (
                            "ETHUSDT requested_symbol_summary still missing; "
                            "no fresh crypto wiki summary or live_cross_check yet"
                        )
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "negative coverage note should not resolve"},
    )

    assert diagnostics["blocker_tags"][
        "unresolved_jue_wiki_requested_symbol_coverage"
    ] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"


def test_binance_manager_run_diagnostics_rejects_negative_requested_symbol_coverage_hold_resolution() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": (
                "ETHUSDT requested_symbol_summary still missing; "
                "no fresh crypto wiki summary or live_cross_check yet"
            ),
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "no live_cross_check yet; rebuild crypto wiki first",
                }
            ],
        },
    )

    assert diagnostics["blocker_tags"][
        "unresolved_jue_wiki_requested_symbol_coverage"
    ] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"


def test_binance_manager_run_diagnostics_tags_unresolved_memory_card_quality() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_memory_card_quality": {
                "summary": {
                    "status_counts": {"weak": 1},
                    "weak_symbols": ["ETHUSDT"],
                },
                "action_plan": {
                    "status": "active",
                    "required_action": "cross_check_live_research_before_high_confidence",
                    "symbols": ["ETHUSDT"],
                },
            },
            "candidates": [{"symbol": "ETHUSDT"}],
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_memory_card_quality"] >= 2
    assert diagnostics["jue_wiki_memory_card_quality_status"] == "active"
    assert diagnostics["jue_wiki_weak_memory_card_symbols"] == ["ETHUSDT"]


def test_binance_manager_run_diagnostics_requires_specific_memory_card_quality_resolution() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
                "required_checks": [
                    "refresh_durable_facts_from_crypto_research_and_quant_context"
                ],
            },
        },
        "candidates": [{"symbol": "ETHUSDT"}],
    }
    generic = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "ETHUSDT thin memory card, observe for now",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": "cross-check market context before entry",
                }
            ],
        },
    )
    specific = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "ETHUSDT durable_facts gap requires crypto research refresh",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": (
                        "refresh_durable_facts_from_crypto_research_and_"
                        "quant_context 완료 후 재평가"
                    ),
                }
            ],
        },
    )

    assert generic["jue_wiki_memory_card_quality_resolution_status"] == "unresolved"
    assert generic["blocker_tags"]["unresolved_jue_wiki_memory_card_quality"] >= 2
    assert specific["jue_wiki_memory_card_quality_resolution_status"] == (
        "hold_trigger"
    )
    assert "unresolved_jue_wiki_memory_card_quality" not in specific["blocker_tags"]


def test_binance_manager_run_diagnostics_rejects_memory_card_hold_on_wrong_symbol() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "candidates": [{"symbol": "ETHUSDT"}],
    }
    hold_decision = {
        "summary": "ETHUSDT durable_facts gap mentioned, but BTC trigger only",
        "watch_symbols": ["BTCUSDT"],
        "data_gaps": ["durable_facts"],
        "next_triggers": [
            {
                "symbol": "BTCUSDT",
                "condition": "ETHUSDT durable_facts 문구가 섞인 BTC 트리거",
            }
        ],
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision=hold_decision,
    )
    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision=hold_decision,
    )

    assert error == "validation_repair_resolution_missing_from_model"
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_binance_manager_run_diagnostics_rejects_memory_card_response_on_wrong_symbol() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "candidates": [{"symbol": "ETHUSDT"}],
    }
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "BTCUSDT",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "ETHUSDT durable_facts gap cross_checked elsewhere",
                }
            ]
        }
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "response only"},
    )
    error = manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "response only"},
    )

    assert error == "validation_repair_resolution_missing_from_model"
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_binance_manager_run_diagnostics_accepts_memory_card_response_on_target_symbol() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "candidates": [{"symbol": "ETHUSDT"}],
    }
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "ETHUSDT",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "ETHUSDT durable_facts gap requires refresh",
                }
            ]
        }
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "response only"},
    )
    error = manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "response only"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "response_resolution"
    )


def test_binance_memory_card_quality_rejects_negative_response_resolution() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["ETHUSDT"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["ETHUSDT"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "ETHUSDT",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "candidates": [{"symbol": "ETHUSDT"}],
    }
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "ETHUSDT",
                    "resolution": "candidate_rejected",
                    "evidence_gap": (
                        "ETHUSDT durable_facts not refreshed; "
                        "no fresh memory card quality evidence available"
                    ),
                }
            ]
        }
    }

    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "response only"},
    )
    error = manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "response only"},
    )

    assert error == "validation_repair_resolution_missing_from_model"
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_binance_repair_contract_memory_card_gap_requires_specific_resolution() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "top_missing_fields": [
                        {
                            "field": "durable_facts",
                            "sample_count": 3,
                            "missed_count": 2,
                        }
                    ],
                    "top_required_checks": [
                        {
                            "check": (
                                "refresh_durable_facts_from_crypto_research_"
                                "and_quant_context"
                            ),
                            "sample_count": 3,
                            "missed_count": 2,
                        }
                    ],
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    generic_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT wiki repair needed, observe",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "cross-check before entry"}
            ],
        },
    )
    specific_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT durable_facts gap requires crypto research refresh",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {
                    "symbol": "ETHUSDT",
                    "condition": (
                        "refresh_durable_facts_from_crypto_research_and_"
                        "quant_context 완료 후 재평가"
                    ),
                }
            ],
        },
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT wiki repair needed, observe",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "cross-check before entry"}
            ],
        },
    )

    assert generic_error == "validation_repair_resolution_missing_from_model"
    assert specific_error == ""
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )
    assert diagnostics["jue_wiki_memory_card_quality_gap_top_missing_fields"] == [
        "durable_facts"
    ]
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_memory_card_quality"] >= 2


def test_binance_manager_run_diagnostics_prioritizes_missed_memory_card_gaps() -> None:
    diagnostics = manager_run_diagnostics(
        prompt={
            "jue_wiki_repair_contract": {
                "status": "repair_required",
                "repair_loop_effectiveness": {
                    "status": "repair_required",
                    "memory_card_quality_gap_summary": {
                        "status": "repair_required",
                        "missing_field_counts": {
                            "durable_facts": 3,
                            "risk_notes": 2,
                        },
                        "missing_field_missed_counts": {
                            "durable_facts": 2,
                            "risk_notes": 0,
                        },
                        "required_check_counts": {
                            "refresh_crypto_alpha_facts": 3,
                            "refresh_risk_notes": 2,
                        },
                        "required_check_missed_counts": {
                            "refresh_crypto_alpha_facts": 2,
                            "refresh_risk_notes": 0,
                        },
                        "top_missing_fields": [
                            {
                                "field": "durable_facts",
                                "sample_count": 3,
                                "missed_count": 2,
                            },
                            {
                                "field": "risk_notes",
                                "sample_count": 2,
                                "missed_count": 0,
                            },
                        ],
                        "top_required_checks": [
                            {
                                "check": "refresh_crypto_alpha_facts",
                                "sample_count": 3,
                                "missed_count": 2,
                            },
                            {
                                "check": "refresh_risk_notes",
                                "sample_count": 2,
                                "missed_count": 0,
                            },
                        ],
                    },
                },
            },
            "execution_gate": {
                "status": "ok",
                "execution": {
                    "spot_orders_enabled": True,
                    "futures_orders_enabled": True,
                },
            },
        },
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "observe"},
    )

    assert diagnostics["jue_wiki_memory_card_quality_gap_top_missing_fields"] == [
        "durable_facts"
    ]
    assert diagnostics["jue_wiki_memory_card_quality_gap_top_required_checks"] == [
        "refresh_crypto_alpha_facts"
    ]


def test_binance_memory_card_gap_rejects_generic_validation_repair_resolution() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "top_missing_fields": [
                        {"field": "durable_facts", "sample_count": 3}
                    ],
                    "top_required_checks": [
                        {
                            "check": (
                                "refresh_durable_facts_from_crypto_research_"
                                "and_quant_context"
                            ),
                            "sample_count": 3,
                        }
                    ],
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    generic_error = manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ETHUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "market context cross-check required",
                    }
                ]
            }
        },
        actions=actions,
        hold_decision={"summary": "observe"},
    )
    specific_error = manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "ETHUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "durable_facts and "
                            "refresh_durable_facts_from_crypto_research_"
                            "and_quant_context must be repaired first"
                        ),
                    }
                ]
            }
        },
        actions=actions,
        hold_decision={"summary": "observe"},
    )

    assert generic_error == "validation_repair_resolution_missing_from_model"
    assert specific_error == ""


def test_binance_memory_card_gap_prioritizes_missed_fields_over_sampled_fields() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "missing_field_counts": {"durable_facts": 3, "risk_notes": 2},
                    "missing_field_missed_counts": {
                        "durable_facts": 2,
                        "risk_notes": 0,
                    },
                    "top_missing_fields": [
                        {
                            "field": "durable_facts",
                            "sample_count": 3,
                            "missed_count": 2,
                        },
                        {
                            "field": "risk_notes",
                            "sample_count": 2,
                            "missed_count": 0,
                        },
                    ],
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    sampled_only_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT risk_notes only recheck",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["risk_notes"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "recheck risk_notes"}
            ],
        },
    )
    missed_field_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT durable_facts repeated miss must be repaired",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "recheck durable_facts"}
            ],
        },
    )

    assert sampled_only_error == "validation_repair_resolution_missing_from_model"
    assert missed_field_error == ""


def test_binance_memory_card_gap_prioritizes_explicit_priority_terms() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "priority_missing_fields": ["durable_facts"],
                    "priority_required_checks": ["refresh_crypto_alpha_facts"],
                    "missing_field_missed_counts": {
                        "durable_facts": 2,
                        "risk_notes": 2,
                    },
                    "required_check_missed_counts": {
                        "refresh_crypto_alpha_facts": 2,
                        "inspect_risk_notes": 2,
                    },
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    non_priority_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT risk_notes and inspect_risk_notes only",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["risk_notes"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "inspect_risk_notes"}
            ],
        },
    )
    priority_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT durable_facts and refresh_crypto_alpha_facts first",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "refresh_crypto_alpha_facts"}
            ],
        },
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "observe"},
    )

    assert non_priority_error == "validation_repair_resolution_missing_from_model"
    assert priority_error == ""
    assert diagnostics["jue_wiki_memory_card_quality_gap_top_missing_fields"] == [
        "durable_facts"
    ]
    assert diagnostics["jue_wiki_memory_card_quality_gap_top_required_checks"] == [
        "refresh_crypto_alpha_facts"
    ]


def test_binance_memory_card_gap_requires_top_priority_term_first() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "priority_missing_fields": ["durable_facts", "lessons"],
                    "priority_required_checks": [
                        "refresh_crypto_alpha_facts",
                        "inspect_block_lessons",
                    ],
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    second_priority_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT lessons and inspect_block_lessons only",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["lessons"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "inspect_block_lessons"}
            ],
        },
    )
    top_priority_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT durable_facts and refresh_crypto_alpha_facts first",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "refresh_crypto_alpha_facts"}
            ],
        },
    )

    assert second_priority_error == "validation_repair_resolution_missing_from_model"
    assert top_priority_error == ""


def test_binance_memory_card_gap_rejects_stale_focus_when_priority_lists_exist() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "priority_missing_fields": ["durable_facts"],
                    "priority_required_checks": ["refresh_crypto_alpha_facts"],
                    "priority_focus": {
                        "missing_field": "risk_notes",
                        "missing_field_sample_count": 4,
                        "missing_field_missed_count": 3,
                        "required_check": "inspect_risk_notes",
                        "required_check_sample_count": 4,
                        "required_check_missed_count": 3,
                        "instruction": (
                            "resolve_priority_memory_card_quality_gap_first"
                        ),
                    },
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    stale_focus_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT risk_notes and inspect_risk_notes only",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["risk_notes"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "inspect_risk_notes"}
            ],
        },
    )
    explicit_priority_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT durable_facts and refresh_crypto_alpha_facts first",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "refresh_crypto_alpha_facts"}
            ],
        },
    )

    assert stale_focus_error == "validation_repair_resolution_missing_from_model"
    assert explicit_priority_error == ""


def test_binance_memory_card_gap_uses_priority_focus_when_lists_are_absent() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "active",
            "repair_loop_effectiveness": {
                "status": "active",
                "memory_card_quality_gap_summary": {
                    "status": "active",
                    "priority_focus": {
                        "missing_field": "durable_facts",
                        "missing_field_sample_count": 3,
                        "missing_field_missed_count": 2,
                        "required_check": "refresh_crypto_alpha_facts",
                        "required_check_sample_count": 3,
                        "required_check_missed_count": 2,
                        "instruction": (
                            "resolve_priority_memory_card_quality_gap_first"
                        ),
                    },
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    generic_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT memory card quality check later",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["memory_card_quality"],
            "next_triggers": [{"symbol": "ETHUSDT", "condition": "recheck wiki"}],
        },
    )
    focus_error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "ETHUSDT durable_facts and refresh_crypto_alpha_facts first",
            "watch_symbols": ["ETHUSDT"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "ETHUSDT", "condition": "refresh_crypto_alpha_facts"}
            ],
        },
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "observe"},
    )

    assert generic_error == "validation_repair_resolution_missing_from_model"
    assert focus_error == ""
    assert diagnostics["jue_wiki_memory_card_quality_gap_top_missing_fields"] == [
        "durable_facts"
    ]
    assert diagnostics["jue_wiki_memory_card_quality_gap_top_required_checks"] == [
        "refresh_crypto_alpha_facts"
    ]
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_binance_memory_card_gap_ignores_resolved_active_gap_summary() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "active",
            "repair_loop_effectiveness": {
                "status": "active",
                "memory_card_quality_gap_summary": {
                    "status": "active",
                    "missing_field_counts": {"durable_facts": 3},
                    "missing_field_missed_counts": {"durable_facts": 0},
                    "required_check_counts": {"refresh_crypto_alpha_facts": 3},
                    "required_check_missed_counts": {
                        "refresh_crypto_alpha_facts": 0
                    },
                    "top_missing_fields": [
                        {
                            "field": "durable_facts",
                            "sample_count": 3,
                            "missed_count": 0,
                        }
                    ],
                    "top_required_checks": [
                        {
                            "check": "refresh_crypto_alpha_facts",
                            "sample_count": 3,
                            "missed_count": 0,
                        }
                    ],
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "observe"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "observe"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "inactive"
    )
    assert "unresolved_jue_wiki_memory_card_quality" not in diagnostics[
        "blocker_tags"
    ]


def test_binance_memory_card_gap_ignores_zero_count_priority_focus() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "active",
            "repair_loop_effectiveness": {
                "status": "active",
                "memory_card_quality_gap_summary": {
                    "status": "active",
                    "priority_focus": {
                        "missing_field": "durable_facts",
                        "missing_field_sample_count": 3,
                        "missing_field_missed_count": 0,
                        "required_check": "refresh_crypto_alpha_facts",
                        "required_check_sample_count": 3,
                        "required_check_missed_count": 0,
                        "instruction": (
                            "resolve_priority_memory_card_quality_gap_first"
                        ),
                    },
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
            },
        },
    }
    actions = {"create_blocks": [], "update_blocks": [], "close_blocks": []}

    error = manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "observe"},
    )
    diagnostics = manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "observe"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "inactive"
    )
    assert "unresolved_jue_wiki_memory_card_quality" not in diagnostics[
        "blocker_tags"
    ]


def test_compact_manager_prompt_context_includes_diagnostics_and_core_sections() -> None:
    marker = "BINANCE_MANAGER_CONTEXT_MODULE_BLOAT"
    context = compact_manager_prompt_context(
        {
            "candidate_generation": {
                "stage_counts": {"research": 5},
                "raw_notes": marker * 20,
            },
            "growth_unlock": {"phase": "rebuilding", "notes": marker * 20},
            "lane_balance": {"recent_blocks": {"dominant_lane": "futures:short"}},
            "crypto_market_pulse": {"summary": marker * 20},
            "raw_context_refs": {"large": marker * 20},
            "canonical_decision_packet": {
                "target_scope": "binance",
                "primary_prompt_key": "decision_packet_v2",
                "missing_required_sections": ["blocks"],
            },
            "decision_packet": {
                "evidence": [{"summary": marker * 20}],
                "scorecards": [{"lane": "futures:short", "notes": marker * 20}],
                "active_policies": [{"policy_id": "probe", "notes": marker * 20}],
            },
            "candidates": [
                {"symbol": "BTCUSDT", "reason_md": marker * 20},
                {"symbol": "ETHUSDT", "reason_md": marker * 20},
            ],
            "jue_wiki_application": {
                "status": "ok",
                "selection_run_id": "selection:binance-context",
                "selected_page_ids": ["binance.symbol.BTCUSDT"],
                "raw_notes": marker * 20,
            },
            "jue_wiki_repair_contract": {
                "status": "active",
                "action_batches": [
                    {
                        "scope": "binance",
                        "action_type": "refresh_crypto_microstructure",
                        "count": 4,
                        "symbols": ["BTCUSDT"],
                        "raw_notes": marker * 20,
                    }
                ],
            },
            "jue_wiki_decision_adjustments": {
                "version": "jue_wiki_decision_adjustments_v1",
                "status": "active",
                "adjustments": [
                    {
                        "action": "shift_to_preferred_risk_posture",
                        "target_risk_posture": "repair_probe",
                        "reason": marker * 20,
                    }
                ],
            },
            "live_authority": {"live_grade": "observe_only", "allow_scale_up": False},
        },
        response={},
        actions={"create_blocks": []},
        hold_decision={"summary": "observe_only 대기"},
    )

    encoded = json.dumps(context, ensure_ascii=False)
    assert context["canonical_decision_packet"]["target_scope"] == "binance"
    assert context["canonical_decision_packet"]["primary_prompt_key"] == (
        "decision_packet_v2"
    )
    assert context["decision_packet"]["evidence"]
    assert len(context["candidates"]) == 2
    assert context["jue_wiki_application"]["selection_run_id"] == (
        "selection:binance-context"
    )
    assert context["jue_wiki_repair_contract"]["action_batches"][0][
        "action_type"
    ] == "refresh_crypto_microstructure"
    assert context["jue_wiki_decision_adjustments"]["adjustments"][0][
        "target_risk_posture"
    ] == "repair_probe"
    assert context["diagnostics"]["blocker_tags"]["observe_only"] >= 1
    assert context["diagnostics"]["blocker_tags"][
        "unresolved_jue_wiki_repair_action_batches"
    ] >= 3
    assert marker not in encoded


def test_compact_manager_prompt_context_exposes_storage_compaction_trace() -> None:
    context = compact_manager_prompt_context(
        {
            "_storage_compaction": {
                "status": "compacted",
                "label": "binance_manager_prompt",
                "emergency": True,
                "priority_reason": "manager_contract_recovery",
                "dropped_keys": ["output_schema", "candidates"],
                "dropped_key_count": 2,
            },
            "memory": {
                "status": "ok",
                "validation_recovery_summary": {
                    "manager_contract_recovered": [
                        {
                            "contract": "cite_or_reject_candidate_memory_hint",
                            "error": (
                                "candidate_memory_hint_resolution_missing_from_model"
                            ),
                        }
                    ]
                },
            },
        },
        response={},
        actions={"create_blocks": []},
        hold_decision={},
    )

    assert context["storage_compaction"] == {
        "status": "compacted",
        "label": "binance_manager_prompt",
        "emergency": True,
        "priority_reason": "manager_contract_recovery",
        "dropped_keys": ["output_schema", "candidates"],
        "dropped_key_count": 2,
    }


def test_manager_prompt_storage_emergency_payload_prioritizes_action_candidates() -> None:
    payload = {
        "prompt_budget": {"total_chars": 20_000},
        "candidates": [
            {"symbol": "XRPUSDT", "market": "spot", "side": "long"},
            {"symbol": "BTCUSDT", "market": "spot", "side": "long"},
            {"symbol": "ETHUSDT", "market": "futures", "side": "short"},
        ],
        "market_universe": {
            "spot": [{"symbol": "BTCUSDT"}],
            "futures": [{"symbol": "ETHUSDT"}],
        },
        "jue_workflow": {
            "workflow_id": "binance_cycle",
            "contracts": [
                {"contract_id": f"legacy_contract_{idx}"}
                for idx in range(5)
            ]
            + [
                {
                    "contract_id": "jue_wiki_usage_contract_resolution",
                    "required_metadata": "jue_wiki_usage_contract_resolution",
                }
            ],
        },
        "unused_blob": "x" * 5000,
    }

    compact = manager_prompt_storage_emergency_payload(
        payload,
        limit=8_000,
        label="binance_manager_prompt",
        original_chars=20_000,
        retained_keys=list(payload.keys()),
        priority_candidate_keys={
            ("BTCUSDT", "spot", "long", "short"),
            ("ETHUSDT", "futures", "short", "futures"),
        },
        compact_value=lambda value, **_: value,
    )

    assert compact["_storage_compaction"]["emergency"] is True
    assert "unused_blob" not in compact
    assert compact["candidates"]["item_count"] == 3
    assert [row["symbol"] for row in compact["candidates"]["items"][:2]] == [
        "BTCUSDT",
        "ETHUSDT",
    ]
    assert compact["market_universe"]["item_count"] == 2
    assert compact["jue_workflow"]["workflow_id"] == "binance_cycle"
    contract_ids = [
        row.get("contract_id")
        for row in compact["jue_workflow"].get("contracts", [])
        if isinstance(row, dict)
    ]
    assert "jue_wiki_usage_contract_resolution" in contract_ids


def test_manager_prompt_storage_emergency_payload_prioritizes_proactive_pressure_candidates() -> None:
    payload = {
        "prompt_budget": {"total_chars": 120_000},
        "critical_response_contract": {
            "create_blocks_candidate_visibility": {
                "required": True,
                "instruction": "use visible candidates only",
            }
        },
        "proactive_decision_pressure": {
            "status": "candidate_present",
            "top_candidates": [
                {
                    "symbol": "KRW-SOL",
                    "market": "upbit_spot",
                    "side": "long",
                    "horizon": "short",
                    "score": 78,
                    "confidence": 0.58,
                }
            ],
        },
        "candidates": [
            {"symbol": "BTCUSDT", "market": "spot", "side": "long"},
            {"symbol": "ETHUSDT", "market": "futures", "side": "long"},
            {"symbol": "ESPUSDT", "market": "futures", "side": "short"},
            {"symbol": "KRW-ZRO", "market": "upbit_spot", "side": "long"},
            {
                "symbol": "KRW-SOL",
                "market": "upbit_spot",
                "side": "long",
                "horizon": "short",
                "reason_md": "proactive pressure candidate " * 20,
            },
        ],
        "memory": {"blob": "M" * 60_000},
        "jue_wiki_application": {"blob": "W" * 60_000},
    }

    compact = manager_prompt_storage_emergency_payload(
        payload,
        limit=1_500,
        label="binance_manager_prompt_runtime",
        original_chars=120_000,
        retained_keys=list(payload.keys()),
        compact_value=compact_prompt_value_bounded,
    )

    rows = compact["candidates"]["items"]
    assert any(row.get("symbol") == "KRW-SOL" for row in rows)


def test_manager_prompt_storage_emergency_payload_falls_back_when_still_too_large() -> None:
    payload = {
        "prompt_budget": {"total_chars": 80_000},
        "candidates": [
            {"symbol": f"ALT{i}USDT", "notes": "x" * 1000}
            for i in range(30)
        ],
        "entry_gate_policy": {"notes": "y" * 5000},
        "market_universe": {"spot": [{"symbol": "BTCUSDT", "notes": "z" * 5000}]},
        "jue_workflow": {"workflow_id": "binance_cycle", "notes": "w" * 5000},
    }

    compact = manager_prompt_storage_emergency_payload(
        payload,
        limit=1_000,
        label="binance_manager_prompt",
        original_chars=80_000,
        retained_keys=list(payload.keys()),
        priority_candidate_keys=set(),
        compact_value=lambda value, **_: value,
    )

    assert compact["_storage_compaction"]["emergency"] is True
    assert set(compact).issuperset(
        {"_storage_compaction", "prompt_budget", "entry_gate_policy", "candidates"}
    )
    assert "market_universe" in compact


def test_compact_manager_storage_payload_returns_small_payload_unchanged() -> None:
    payload = {"status": "ok", "selected_contract_id": "binance_cycle_v1"}

    compact = compact_manager_storage_payload(
        payload,
        limit=10_000,
        label="binance_manager_response",
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact is payload


def test_compact_manager_storage_payload_has_default_compaction_adapters() -> None:
    payload = {
        "status": "ok",
        "selected_contract_id": "binance_cycle_v1",
        "hold_decision": {"summary": "대기", "notes": "x" * 5_000},
        "large_model_trace": {"trace": "y" * 20_000},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_000,
        label="binance_manager_response",
    )

    assert compact["_storage_compaction"]["status"] == "compacted"
    assert compact["selected_contract_id"] == "binance_cycle_v1"
    assert compact["hold_decision"]["summary"] == "대기"
    assert compact["hold_decision"]["notes"].startswith("[truncated:")
    assert compact["large_model_trace"]["trace"].startswith("[truncated:")


def test_compact_manager_storage_payload_preserves_wiki_repair_action_metadata() -> None:
    wiki_action_metadata = {
        "block_color": "futures",
        "jue_wiki_repair_pressure": {
            "status": "required",
            "page_id": "binance.symbol.NEARUSDT",
            "reason": "최근 손절 이후 가격 구조와 내러티브를 교차 점검해야 함",
        },
        "jue_wiki_repair_resolution": {
            "status": "resolved_by_action",
            "evidence_ids": ["crypto_quant:NEARUSDT:live", "alpha:NEARUSDT"],
        },
        "jue_wiki_memory_card_quality": {
            "status": "degraded",
            "missing": ["recent_microstructure", "spot_futures_split"],
        },
        "jue_wiki_memory_card_cross_check": {
            "status": "cross_checked",
            "sources": ["crypto_quant", "orderbook", "funding"],
        },
        "jue_wiki_reference_basis": (
            "jue_wiki_action_reference_gap.binance.missing 보정: "
            "book, spread, funding, crypto_quant 근거를 액션에 남김"
        ),
        "jue_wiki_usage_contract_resolution": (
            "위키는 단독 매매권한이 아니며 live_spread/funding/"
            "liquidation_distance/account_state 교차확인 후 소액 선물 블록"
        ),
        "raw_wiki_page": "raw crypto wiki body " * 600,
    }
    payload = {
        "status": "ok",
        "selected_contract_id": "binance_cycle_v1",
        "applied": {
            "created": {
                "item_count": 1,
                "items": [
                    {
                        "symbol": "NEARUSDT",
                        "market": "futures",
                        "side": "short",
                        "lane": "futures",
                        "status": "created",
                        "entry_price": 1.93,
                        "target_price": 1.82,
                        "stop_price": 1.98,
                        "metadata": wiki_action_metadata,
                    }
                ],
                "omitted_item_count": 0,
            },
            "create_blocks": [
                {
                    "symbol": f"ALT{index}USDT",
                    "status": "rejected",
                    "reason": "large rejected action",
                    "thesis": "oversized action thesis " * 260,
                    "risk_note": "oversized action risk " * 260,
                }
                for index in range(80)
            ],
        },
        "large_model_trace": {"trace": "raw reasoning " * 3_000},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_000,
        label="binance_manager_response",
    )
    compact_text = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    metadata = compact["applied"]["created"]["items"][0]["metadata"]

    assert compact["_storage_compaction"]["emergency"] is True
    assert "raw crypto wiki body" not in compact_text
    for key in (
        "jue_wiki_repair_pressure",
        "jue_wiki_repair_resolution",
        "jue_wiki_memory_card_quality",
        "jue_wiki_memory_card_cross_check",
        "jue_wiki_reference_basis",
        "jue_wiki_usage_contract_resolution",
    ):
        assert key in metadata
    assert metadata["jue_wiki_repair_pressure"]["status"] == "required"
    assert metadata["jue_wiki_repair_resolution"]["status"] == "resolved_by_action"


def test_compact_manager_storage_payload_preserves_degraded_wiki_diagnostics() -> None:
    payload = {
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 220_000},
        "diagnostics": {
            "version": "binance_manager_diagnostics_v1",
            "action_count": 0,
            "blocker_tags": {
                "unresolved_degraded_jue_wiki_effectiveness": 3,
                "unresolved_jue_wiki_selection_guidance": 2,
                "unresolved_jue_wiki_action_reference_recovery": 4,
            },
            "top_blockers": [
                {
                    "tag": "unresolved_jue_wiki_action_reference_recovery",
                    "count": 4,
                    "diagnostic_context": "oversized recovery detail " * 90,
                }
            ],
            "jue_wiki_selection_guidance_status": "active",
            "jue_wiki_selection_guidance_resolution_status": "unresolved",
            "jue_wiki_action_reference_memory_status": "active",
            "jue_wiki_action_reference_memory_resolution_status": "unresolved",
            "jue_wiki_action_reference_status": "missing",
            "jue_wiki_action_reference_count": 0,
            "jue_wiki_action_reference_ratio": 0.0,
            "jue_wiki_action_reference_unscoped_page_ids": [
                "binance.symbol.NEARUSDT".lower()
            ],
            "jue_wiki_action_reference_unscoped_page_omitted_count": 2,
            "jue_wiki_action_reference_missing_actions": [
                {
                    "section": "create_blocks",
                    "symbol": "NEARUSDT",
                    "market": "futures",
                    "side": "long",
                    "lane": "futures:long",
                    "reason": "missing wiki action reference",
                }
            ],
            "jue_wiki_action_reference_recovery_status": "unresolved",
            "jue_wiki_action_reference_recovery_memory_scope": "binance",
            "jue_wiki_action_reference_recovery_open_gap_count": 4,
            "jue_wiki_action_reference_recovery_resolved_count": 0,
            "jue_wiki_action_reference_recovery_total_count": 4,
            "jue_wiki_action_reference_recovery_ratio": 0.0,
            "jue_wiki_action_reference_recovery_latest_resolution_status": (
                "unresolved"
            ),
            "jue_wiki_action_reference_recovery_latest_status": "missing",
            "degraded_jue_wiki_effectiveness_count": 1,
            "degraded_jue_wiki_effectiveness_page_ids": [
                "binance.symbol.NEARUSDT"
            ],
            "degraded_jue_wiki_effectiveness_resolution_status": "unresolved",
            "raw_notes": "diagnostic context " * 600,
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["binance.symbol.NEARUSDT"],
        },
        "jue_wiki_repair_contract": {
            "status": "active",
            "top_priorities": [
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "repair_action": "refresh recent microstructure",
                }
            ],
        },
        "candidates": [
            {
                "symbol": f"ALT{index}USDT",
                "market": "futures",
                "side": "short",
                "reason_md": "candidate context " * 240,
            }
            for index in range(120)
        ],
        "overflow": "x" * 220_000,
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=6_000,
        label="binance_manager_prompt",
    )
    compact_text = json.dumps(compact, ensure_ascii=False, sort_keys=True)

    assert compact["_storage_compaction"]["emergency"] is True
    assert compact["diagnostics"]["blocker_tags"] == {
        "unresolved_degraded_jue_wiki_effectiveness": 3,
        "unresolved_jue_wiki_selection_guidance": 2,
        "unresolved_jue_wiki_action_reference_recovery": 4,
    }
    assert compact["diagnostics"]["top_blockers"] == [
        {"tag": "unresolved_jue_wiki_action_reference_recovery", "weight": 4}
    ]
    assert compact["diagnostics"]["jue_wiki_selection_guidance_status"] == "active"
    assert compact["diagnostics"][
        "jue_wiki_selection_guidance_resolution_status"
    ] == "unresolved"
    assert compact["diagnostics"]["jue_wiki_action_reference_memory_status"] == (
        "active"
    )
    assert compact["diagnostics"][
        "jue_wiki_action_reference_memory_resolution_status"
    ] == "unresolved"
    assert compact["diagnostics"]["jue_wiki_action_reference_status"] == "missing"
    assert compact["diagnostics"]["jue_wiki_action_reference_count"] == 0
    assert compact["diagnostics"]["jue_wiki_action_reference_ratio"] == 0.0
    assert compact["diagnostics"]["jue_wiki_action_reference_unscoped_page_ids"] == [
        "binance.symbol.NEARUSDT".lower()
    ]
    assert (
        compact["diagnostics"][
            "jue_wiki_action_reference_unscoped_page_omitted_count"
        ]
        == 2
    )
    assert compact["diagnostics"]["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "create_blocks",
            "symbol": "NEARUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures:long",
            "reason": "missing wiki action reference",
        }
    ]
    assert compact["diagnostics"]["jue_wiki_action_reference_recovery_status"] == (
        "unresolved"
    )
    assert compact["diagnostics"]["jue_wiki_action_reference_recovery_memory_scope"] == (
        "binance"
    )
    assert (
        compact["diagnostics"]["jue_wiki_action_reference_recovery_open_gap_count"]
        == 4
    )
    assert (
        compact["diagnostics"]["jue_wiki_action_reference_recovery_resolved_count"]
        == 0
    )
    assert (
        compact["diagnostics"]["jue_wiki_action_reference_recovery_total_count"]
        == 4
    )
    assert compact["diagnostics"]["jue_wiki_action_reference_recovery_ratio"] == 0.0
    assert compact["diagnostics"][
        "jue_wiki_action_reference_recovery_latest_resolution_status"
    ] == "unresolved"
    assert compact["diagnostics"]["jue_wiki_action_reference_recovery_latest_status"] == (
        "missing"
    )
    assert compact["diagnostics"]["degraded_jue_wiki_effectiveness_count"] == 1
    assert compact["diagnostics"]["degraded_jue_wiki_effectiveness_page_ids"] == [
        "binance.symbol.NEARUSDT"
    ]
    assert compact["diagnostics"][
        "degraded_jue_wiki_effectiveness_resolution_status"
    ] == "unresolved"
    assert "diagnostic context diagnostic context" not in compact_text


def test_compact_manager_storage_payload_preserves_response_diagnostics_on_fallback() -> None:
    payload = {
        "status": "ok",
        "selected_contract_id": "binance_cycle_v1",
        "hold_decision": {"summary": "대기", "reasons": ["probe gate"]},
        "lane_review": {"selected_lanes": ["futures:short"]},
        "applied": {"created": [{"status": "rejected", "reason": "restricted"}]},
        "large_model_trace": {"trace": "x" * 20_000},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_000,
        label="binance_manager_response",
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact["_storage_compaction"]["emergency"] is True
    assert compact["selected_contract_id"] == "binance_cycle_v1"
    assert compact["hold_decision"]["summary"] == "대기"
    assert compact["lane_review"]["selected_lanes"] == ["futures:short"]
    assert compact["applied"]["created"][0]["reason"] == "restricted"


def test_compact_manager_storage_payload_uses_binance_prompt_emergency_path() -> None:
    payload = {
        "prompt_budget": {"total_chars": 60_000},
        "candidates": [
            {"symbol": "XRPUSDT", "market": "spot", "side": "long", "notes": "x" * 1000},
            {"symbol": "BTCUSDT", "market": "spot", "side": "long", "notes": "x" * 1000},
        ],
        "entry_gate_policy": {"status": "active"},
        "large_context": "y" * 50_000,
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=5_000,
        label="binance_manager_prompt",
        priority_candidate_keys={("BTCUSDT", "spot", "long", "short")},
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact["_storage_compaction"]["emergency"] is True
    assert "large_context" not in compact
    assert compact["candidates"]["item_count"] == 2
    assert compact["candidates"]["items"][0]["symbol"] == "BTCUSDT"


def test_compact_manager_storage_payload_preserves_account_gate_in_final_emergency() -> None:
    payload = {
        "native_thread_mode": "ephemeral",
        "task": "manage Binance blocks " + ("T" * 200_000),
        "prompt_budget": {"total_chars": 240_000},
        "account": {
            "status": "ok",
            "spot_cash_usdt": 1_000.0,
            "futures_cash_usdt": 500.0,
        },
        "risk_guard": {
            "status": "ok",
            "current_equity_usdt": 1_500.0,
            "allow_new_entries": True,
        },
        "growth_target": {
            "status": "on_track",
            "current_equity_usdt": 1_500.0,
            "target_equity_usdt": 2_250.0,
        },
        "growth_governor": {
            "mode": "edge_rebuild",
            "metrics": {"risk_guard_status": "ok"},
        },
        "execution_gate": {
            "cash_available": {"total_equity_usdt": 1_500.0},
        },
        "candidates": [
            {"symbol": f"C{idx:03d}USDT", "notes": "C" * 1_000}
            for idx in range(80)
        ],
        "output_schema": {"create_blocks": [], "lane_review": {"required": True}},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_000,
        label="binance_manager_prompt",
    )

    assert compact["_storage_compaction"]["emergency"] is True
    assert compact["native_thread_mode"] == "ephemeral"
    assert compact["account"]["spot_cash_usdt"] == 1_000.0
    assert compact["risk_guard"]["current_equity_usdt"] == 1_500.0
    assert compact["risk_guard"]["allow_new_entries"] is True
    assert compact["growth_target"]["current_equity_usdt"] == 1_500.0
    assert compact["growth_governor"]["metrics"]["risk_guard_status"] == "ok"


def test_compact_prompt_candidate_minimal_keeps_execution_fields_and_summarizes_nested_data() -> None:
    candidate = {
        "symbol": "BTCUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "futures",
        "score": 84,
        "confidence": 0.72,
        "target_price_usdt": 62000,
        "stop_price_usdt": 67500,
        "quote_budget_usdt": 50,
        "reason_md": "x" * 300,
        "large_unused_context": "drop me",
        "calculated_price_plan": {
            "method_version": "v3",
            "lane": "futures:short",
            "reward_risk": 2.4,
            "quote_budget_usdt": 50,
            "diagnostics": "drop",
        },
        "metadata": {
            "lane": "futures:short",
            "entry_style": "wait_for_price",
            "entry_trigger_price": 65000,
            "raw_context": "drop",
        },
    }

    compact = compact_prompt_candidate_minimal(
        candidate,
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact["symbol"] == "BTCUSDT"
    assert compact["market"] == "futures"
    assert compact["target_price_usdt"] == 62000
    assert "large_unused_context" not in compact
    assert compact["calculated"] == {
        "method_version": "v3",
        "lane": "futures:short",
        "reward_risk": 2.4,
        "quote_budget_usdt": 50,
    }
    assert compact["metadata"] == {
        "lane": "futures:short",
        "entry_style": "wait_for_price",
        "entry_trigger_price": 65000,
    }
    assert len(compact["reason_md"]) == 180


def test_compact_prompt_candidates_minimal_only_transforms_lists() -> None:
    rows = [
        {"symbol": "BTCUSDT", "reason": "first"},
        {"symbol": "ETHUSDT", "reason_md": "second"},
    ]
    passthrough = {"not": "a-list"}

    compact_rows = compact_prompt_candidates_minimal(
        rows,
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )
    compact_passthrough = compact_prompt_candidates_minimal(
        passthrough,
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert [row["symbol"] for row in compact_rows] == ["BTCUSDT", "ETHUSDT"]
    assert compact_rows[0]["reason_md"] == "first"
    assert compact_rows[1]["reason_md"] == "second"
    assert compact_passthrough is passthrough


def test_compact_manager_candidate_for_prompt_keeps_execution_review_hints() -> None:
    compact = compact_manager_candidate_for_prompt(
        {
            "symbol": "PAXGUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "score": 83.0,
            "confidence": 0.64,
            "entry_price": 4172.94,
            "target_price": 4106.18,
            "stop_price": 4206.33,
            "near_duplicate_active_block": {
                "status": "review_required",
                "action_hint": "manage_existing_block",
                "existing_block_id": "bnb_futures_PAXGUSDT_open",
                "instruction": "Do not create another near-identical block.",
            },
            "lane_authority_candidate": {
                "lane": "futures:short",
                "action": "allow_probe",
            },
            "execution_blockers": {
                "items": ["spread_too_wide", "funding_crowded"],
            },
        },
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
        score_candidate=lambda row: {"score": 77.0, "grade": "B"},
    )

    assert compact["symbol"] == "PAXGUSDT"
    assert compact["entry_price_usdt"] == 4172.94
    assert compact["near_duplicate_active_block"]["status"] == "review_required"
    assert (
        compact["near_duplicate_active_block"]["action_hint"]
        == "manage_existing_block"
    )
    assert compact["lane_authority_candidate"]["action"] == "allow_probe"
    assert compact["execution_blockers"]["items"] == ["spread_too_wide", "funding_crowded"]
    assert compact["alpha_score_v3"] == {"score": 77.0, "grade": "B"}


def test_compact_manager_candidate_for_prompt_labels_upbit_prices_as_krw() -> None:
    compact = compact_manager_candidate_for_prompt(
        {
            "symbol": "KRW-AAVE",
            "market": "upbit_spot",
            "side": "long",
            "horizon": "short",
            "score": 82.0,
            "confidence": 0.62,
            "entry_price": 129_241.6,
            "target_price": 131_645.5,
            "stop_price": 127_690.7,
            "quote_budget_krw": 50_000.0,
            "calculated": {
                "quote_budget_krw": 50_000.0,
                "reward_risk": 1.55,
            },
        },
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
        score_candidate=lambda row: {"score": 77.0, "grade": "B"},
    )

    assert compact["entry_price"] == pytest.approx(129_241.6)
    assert compact["entry_price_krw"] == pytest.approx(129_241.6)
    assert compact["target_price_krw"] == pytest.approx(131_645.5)
    assert compact["stop_price_krw"] == pytest.approx(127_690.7)
    assert compact["quote_budget_krw"] == pytest.approx(50_000.0)
    assert "entry_price_usdt" not in compact
    assert "target_price_usdt" not in compact
    assert "stop_price_usdt" not in compact


def test_compact_manager_candidate_for_prompt_hides_upbit_usdt_budget_alias() -> None:
    compact = compact_manager_candidate_for_prompt(
        {
            "symbol": "KRW-AAVE",
            "market": "upbit_spot",
            "side": "long",
            "entry_price": 129_241.6,
            "target_price": 131_645.5,
            "stop_price": 127_690.7,
            "quote_budget_usdt": 47.25,
            "calculated": {
                "quote_budget_usdt": 47.25,
                "reward_risk": 1.55,
            },
        },
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
        score_candidate=lambda row: {"score": 77.0, "grade": "B"},
    )

    assert "quote_budget_usdt" not in compact
    assert "quote_budget_usdt" not in compact["calculated"]
    assert "quote_budget_krw" not in compact


def test_compact_manager_candidate_for_prompt_exposes_budget_and_pattern_scorecard() -> None:
    compact = compact_manager_candidate_for_prompt(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "score": 91.0,
            "confidence": 0.81,
            "pattern_performance_scorecard": {
                "pattern_key": "bollinger_squeeze:short:5m",
                "status": "de_risk",
                "sample_count": 5,
                "pnl_usdt": -1.2,
            },
            "calculated": {
                "reward_risk": 2.4,
                "performance_budget_multiplier": 0.5,
                "pattern_performance_multiplier": 0.5,
                "pattern_performance_scorecard": {
                    "pattern_key": "bollinger_squeeze:short:5m",
                    "status": "de_risk",
                    "sample_count": 5,
                    "pnl_usdt": -1.2,
                },
                "sizing_inputs": {
                    "performance_budget_multiplier": 0.5,
                    "pattern_performance_multiplier": 0.5,
                },
                "decision_notes": ["respect weak live edge", ""],
            },
        },
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
        score_candidate=lambda row: {"score": 64.0},
    )

    assert compact["performance_budget_multiplier"] == 0.5
    assert compact["pattern_performance_scorecard"]["pattern_key"] == (
        "bollinger_squeeze:short:5m"
    )
    assert compact["pattern_performance_scorecard"]["status"] == "de_risk"
    assert compact["calculated"]["performance_budget_multiplier"] == 0.5
    assert compact["calculated"]["pattern_performance_multiplier"] == 0.5
    assert compact["calculated"]["sizing_inputs"]["pattern_performance_multiplier"] == 0.5
    assert compact["calculated"]["decision_notes"] == ["respect weak live edge"]


def test_compact_manager_candidate_for_prompt_omits_duplicate_calculated_price_plan() -> None:
    compact = compact_manager_candidate_for_prompt(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "entry_price": 100.0,
            "target_price": 96.0,
            "stop_price": 102.0,
            "calculated": {
                "method_version": "test",
                "reward_risk": 2.5,
                "pattern_live_crosscheck": {"status": "aligned"},
            },
        },
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact["calculated"]["reward_risk"] == 2.5
    assert "calculated_price_plan" not in compact


def test_compact_manager_block_for_prompt_keeps_block_fields_and_compacts_metadata() -> None:
    calls: list[dict[str, object]] = []

    def bounded(value: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        if isinstance(value, dict):
            drop_keys = set(kwargs.get("drop_keys") or set())
            return {key: item for key, item in value.items() if key not in drop_keys}
        return value

    block = {
        "block_id": "B1",
        "symbol": "BTCUSDT",
        "market": "futures",
        "side": "short",
        "status": "open",
        "qty_open": 0.02,
        "entry_price": 65000,
        "target_price": 62000,
        "stop_price": 67000,
        "thesis": "x" * 15,
        "risk_note": "tight invalidation",
        "ignored_top_level": "drop",
        "metadata": {
            "lane": "futures:short",
            "horizon": "futures",
            "entry_style": "wait_for_price",
            "jue_wiki_repair_pressure": "degraded crypto page used as repair evidence",
            "jue_wiki_repair_resolution": (
                "live book spread funding cross-check reduced size"
            ),
            "jue_wiki_memory_card_quality": "thin crypto card required live check",
            "jue_wiki_memory_card_cross_check": "checked book spread funding",
            "jue_wiki_selection_resolution": (
                "fresh_jue_wiki_context refreshed before crypto size increase"
            ),
            "jue_wiki_freshness_cross_check": (
                "selection_audit_resolution and live_cross_check completed"
            ),
            "jue_wiki_reference_basis": (
                "jue_wiki_action_reference_gap.binance.missing 보정 근거"
            ),
            "jue_wiki_usage_contract_resolution": (
                "위키는 단독 매매권한이 아니며 live_spread/funding/liquidation_distance 교차확인 완료"
            ),
            "risk_sizing": {"budget": 30, "raw": "drop"},
            "calculated_price_plan": {"reward_risk": 2.1, "diagnostics": "drop"},
            "raw_payload": "drop",
            "custom_extra": "drop",
        },
    }

    compact = compact_manager_block_for_prompt(
        block,
        string_limit=10,
        compact_value_bounded=bounded,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact["block_id"] == "B1"
    assert compact["symbol"] == "BTCUSDT"
    assert compact["qty_open"] == 0.02
    assert "ignored_top_level" not in compact
    assert compact["thesis"] == "[truncated:15 chars]"
    assert compact["risk_note"] == "[truncated:18 chars]"
    assert compact["metadata"]["lane"] == "futures:short"
    assert compact["metadata"]["jue_wiki_repair_pressure"] == (
        "degraded crypto page used as repair evidence"
    )
    assert compact["metadata"]["jue_wiki_repair_resolution"] == (
        "live book spread funding cross-check reduced size"
    )
    assert compact["metadata"]["jue_wiki_memory_card_quality"] == (
        "thin crypto card required live check"
    )
    assert compact["metadata"]["jue_wiki_memory_card_cross_check"] == (
        "checked book spread funding"
    )
    assert compact["metadata"]["jue_wiki_selection_resolution"] == (
        "fresh_jue_wiki_context refreshed before crypto size increase"
    )
    assert compact["metadata"]["jue_wiki_freshness_cross_check"] == (
        "selection_audit_resolution and live_cross_check completed"
    )
    assert compact["metadata"]["jue_wiki_reference_basis"] == (
        "jue_wiki_action_reference_gap.binance.missing 보정 근거"
    )
    assert compact["metadata"]["jue_wiki_usage_contract_resolution"] == (
        "위키는 단독 매매권한이 아니며 live_spread/funding/liquidation_distance 교차확인 완료"
    )
    assert compact["metadata"]["risk_sizing"] == {"budget": 30}
    assert compact["metadata"]["calculated_price_plan"] == {"reward_risk": 2.1}
    assert compact["metadata"]["_omitted_count"] == 2
    assert set(compact["metadata"]["_omitted_for_prompt"]) == {"custom_extra", "raw_payload"}
    assert calls
    assert "raw" in calls[0]["drop_keys"]
    assert "diagnostics" in calls[0]["drop_keys"]


def test_compact_manager_blocks_for_prompt_limits_lists_and_compacts_non_lists() -> None:
    rows = [
        {"block_id": "B1", "symbol": "BTCUSDT", "thesis": "short"},
        {"block_id": "B2", "symbol": "ETHUSDT", "thesis": "mid"},
    ]
    non_list = {"not": "blocks"}

    compact_rows = compact_manager_blocks_for_prompt(
        rows,
        list_limit=1,
        string_limit=20,
        compact_value=lambda value, **_: {"wrapped": value},
        compact_value_bounded=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )
    compact_non_list = compact_manager_blocks_for_prompt(
        non_list,
        list_limit=1,
        string_limit=20,
        compact_value=lambda value, **_: {"wrapped": value},
        compact_value_bounded=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert len(compact_rows) == 1
    assert compact_rows[0]["block_id"] == "B1"
    assert compact_non_list == {"wrapped": non_list}


def test_compact_manager_lane_action_for_prompt_extracts_gate_statuses() -> None:
    compact = compact_manager_lane_action_for_prompt(
        "futures:short",
        {
            "action": "allow_probe",
            "max_budget_multiplier": 0.5,
            "waiting_entry_required": True,
            "blocks_scale_up": True,
            "active_revision_gate": {"status": "active"},
            "validation_gate": {"status": "probe"},
            "summary": "x" * 120,
            "raw": "drop",
        },
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact == {
        "lane": "futures:short",
        "action": "allow_probe",
        "budget_multiplier": 0.5,
        "requires_waiting_entry": True,
        "scale_up_blocked": True,
        "active_revision_status": "active",
        "validation_status": "probe",
        "reason": "x" * 90,
    }


def test_compact_manager_lane_authority_for_prompt_keeps_allowed_fields_and_action_count() -> None:
    payload = {
        "version": "lane-authority-v1",
        "validation_gate_status": "probe",
        "global_scale_up_allowed": False,
        "execution_posture": "probe_allowed_scale_blocked",
        "probe_lane_count": 2,
        "probe_lane_names": ["futures:short", "spot:long"],
        "scale_blocked_lane_count": 1,
        "scale_blocked_lanes": ["spot:long"],
        "probe_policy": "scale-up is blocked, probes are allowed",
        "weak_lanes": ["spot:long"],
        "active_revision_gate": {"status": "active", "raw": "compact me"},
        "lane_actions": {
            "futures:short": {
                "action": "allow",
                "budget_multiplier": 0.8,
                "reason": "short lane recovered",
            },
            "spot:long": {
                "action": "restrict",
                "validation_gate_status": "weak",
                "reason": "spot lane weak",
            },
        },
        "huge_raw_payload": "drop",
    }

    compact = compact_manager_lane_authority_for_prompt(
        payload,
        string_limit=40,
        list_limit=1,
        compact_value=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact["version"] == "lane-authority-v1"
    assert compact["validation_gate_status"] == "probe"
    assert compact["global_scale_up_allowed"] is False
    assert compact["execution_posture"] == "probe_allowed_scale_blocked"
    assert compact["probe_lane_count"] == 2
    assert compact["probe_lane_names"] == ["futures:short", "spot:long"]
    assert compact["scale_blocked_lane_count"] == 1
    assert compact["scale_blocked_lanes"] == ["spot:long"]
    assert compact["probe_policy"] == "scale-up is blocked, probes are allowed"
    assert compact["weak_lanes"] == ["spot:long"]
    assert compact["active_revision_gate"] == {"status": "active", "raw": "compact me"}
    assert compact["lane_action_count"] == 2
    assert compact["lane_actions"] == [
        {
            "lane": "futures:short",
            "action": "allow",
            "budget_multiplier": 0.8,
            "reason": "short lane recovered",
        }
    ]
    assert compact["_omitted_count"] == 1


def test_compact_manager_lane_authority_for_prompt_compacts_non_mapping_values() -> None:
    compact = compact_manager_lane_authority_for_prompt(
        ["raw", "lane", "authority"],
        compact_value=lambda value, **_: {"wrapped": value},
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact == {"wrapped": ["raw", "lane", "authority"]}


def test_compact_live_authority_prompt_value_drops_bloat_and_preserves_strings() -> None:
    calls: list[dict[str, object]] = []

    def bounded(value: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        if isinstance(value, dict):
            drop_keys = set(kwargs.get("drop_keys") or set())
            return {key: item for key, item in value.items() if key not in drop_keys}
        return value

    compact_dict = compact_live_authority_prompt_value(
        {
            "status": "restricted",
            "summary": "keep",
            "raw_payload": "drop",
            "trading_validation": {"huge": True},
        },
        string_limit=12,
        list_limit=2,
        compact_value_bounded=bounded,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )
    compact_list = compact_live_authority_prompt_value(
        ["x" * 20, {"status": "ok", "raw": "drop"}, "third"],
        string_limit=5,
        list_limit=2,
        compact_value_bounded=bounded,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact_dict == {"status": "restricted", "summary": "keep"}
    assert compact_list == ["x" * 5, {"status": "ok"}]
    assert calls
    assert "raw_payload" in calls[0]["drop_keys"]
    assert "trading_validation" in calls[0]["drop_keys"]
    assert calls[0]["placeholder_strings"] is False


def test_compact_manager_live_authority_for_prompt_keeps_lane_authority_and_allowed_fields() -> None:
    payload = {
        "status": "ok",
        "live_grade": "restricted",
        "summary": "x" * 100,
        "lane_authority": {
            "version": "lane-v1",
            "lane_actions": {
                "futures:short": {"action": "allow", "reason": "ok"},
                "spot:long": {"action": "restrict", "reason": "weak"},
            },
        },
        "validation_gate": {"status": "probe", "raw": "drop"},
        "raw_payload": "drop",
    }

    compact = compact_manager_live_authority_for_prompt(
        payload,
        string_limit=20,
        list_limit=1,
        compact_value=lambda value, **_: value,
        compact_value_bounded=lambda value, **kwargs: {
            key: item
            for key, item in value.items()
            if key not in set(kwargs.get("drop_keys") or set())
        }
        if isinstance(value, dict)
        else value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact["status"] == "ok"
    assert compact["live_grade"] == "restricted"
    assert compact["summary"] == "x" * 20
    assert compact["validation_gate"] == {"status": "probe"}
    assert compact["lane_authority"]["version"] == "lane-v1"
    assert compact["lane_authority"]["lane_action_count"] == 2
    assert compact["lane_authority"]["lane_actions"] == [
        {"lane": "futures:short", "action": "allow", "reason": "ok"}
    ]
    assert compact["_omitted_count"] == 1


def test_compact_manager_live_authority_for_prompt_handles_non_mapping_and_empty_mapping() -> None:
    compact_non_mapping = compact_manager_live_authority_for_prompt(
        ["raw", "authority"],
        compact_value=lambda value, **_: {"wrapped": value},
        compact_value_bounded=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )
    compact_empty = compact_manager_live_authority_for_prompt(
        {"raw_payload": "drop"},
        compact_value=lambda value, **_: value,
        compact_value_bounded=lambda value, **_: value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert compact_non_mapping == {"wrapped": ["raw", "authority"]}
    assert compact_empty == {"_omitted_count": 1}


def test_compact_validation_repair_for_storage_prefers_extracted_metadata() -> None:
    compact = compact_validation_repair_for_storage(
        {"repair_backlog": [{"raw": "large"}]},
        repair_metadata=lambda value: {
            "validation_repair": {
                "scope": "binance",
                "discipline_ids": ["walk_forward_analysis"],
                "entry_biases": ["waiting_probe"],
            }
        },
    )

    assert compact == {
        "scope": "binance",
        "discipline_ids": ["walk_forward_analysis"],
        "entry_biases": ["waiting_probe"],
    }


def test_compact_validation_repair_for_storage_keeps_allowed_scalar_and_short_list_fields() -> None:
    long_text = "x" * 200
    compact = compact_validation_repair_for_storage(
        {
            "version": "validation-repair-v1",
            "scope": "binance",
            "status": "active",
            "repair_item_count": 3,
            "discipline_ids": ["walk_forward_analysis", long_text],
            "scale_blockers": ["sample_cap"],
            "risk_budget_multiplier": 0.25,
            "min_reward_risk": 2.0,
            "required_checks": [f"check-{i}" for i in range(12)],
            "runner_hints": long_text,
            "repair_backlog": [{"raw": "drop"}],
            "hard_filter": True,
        },
        repair_metadata=lambda value: {},
    )

    assert compact["version"] == "validation-repair-v1"
    assert compact["scope"] == "binance"
    assert compact["repair_item_count"] == 3
    assert compact["discipline_ids"][0] == "walk_forward_analysis"
    assert compact["discipline_ids"][1] == "[omitted_long_text chars=200]"
    assert compact["risk_budget_multiplier"] == 0.25
    assert compact["min_reward_risk"] == 2.0
    assert len(compact["required_checks"]) == 10
    assert compact["runner_hints"] == "[omitted_long_text chars=200]"
    assert "repair_backlog" not in compact
    assert compact["raw_sections_omitted"] is True
    assert compact["hard_filter"] is False


def test_compact_validation_repair_for_storage_returns_empty_for_unusable_payloads() -> None:
    assert compact_validation_repair_for_storage(None) == {}
    assert compact_validation_repair_for_storage({}) == {}
    assert compact_validation_repair_for_storage(
        {"repair_backlog": [{"raw": "drop"}]},
        repair_metadata=lambda value: {},
    ) == {}


def test_compact_validation_repair_prompt_extracts_backlog_and_constraints() -> None:
    compact = compact_validation_repair_prompt(
        {
            "validation_repair_backlog": {
                "status": "active",
                "total_item_count": 3,
                "items": [
                    {
                        "policy_id": "validation.walk_forward",
                        "repair_action_id": "collect_wfa",
                        "discipline_id": "walk_forward_analysis",
                        "required_evidence": ["wfa_result", "oos_result"],
                        "pass_required_evidence": ["shadow_trade_log"],
                        "ignored_raw": "drop",
                    }
                ],
            },
            "block_design_constraints": {
                "count": 2,
                "items": [
                    {
                        "policy_id": "validation.walk_forward",
                        "discipline_id": "walk_forward_analysis",
                        "entry_bias": "waiting_probe",
                        "min_reward_risk": 2.0,
                        "risk_note": "x" * 300,
                    }
                ],
            },
        },
        scope="binance",
        compact_value=lambda value, **_: value,
    )

    assert compact["version"] == "validation_repair_prompt_v1"
    assert compact["scope"] == "binance"
    assert compact["status"] == "active"
    assert compact["repair_item_count"] == 3
    assert compact["constraint_count"] == 2
    assert compact["repair_backlog"][0]["policy_id"] == "validation.walk_forward"
    assert compact["repair_backlog"][0]["pass_required_evidence"] == ["shadow_trade_log"]
    assert "ignored_raw" not in compact["repair_backlog"][0]
    assert compact["block_design_constraints"][0]["entry_bias"] == "waiting_probe"
    assert "hard filters" in compact["instruction"]


def test_compact_validation_repair_prompt_preserves_period_memory_constraints() -> None:
    compact = compact_validation_repair_prompt(
        {
            "block_design_constraints": {
                "count": 1,
                "items": [
                    {
                        "policy_id": "period_memory_coverage.gap_unresolved",
                        "venue": "binance",
                        "period_memory_status": "gap_unresolved",
                        "period_memory_gap_count": 3,
                        "period_memory_override_count": 0,
                        "period_memory_contract_gap_count": 3,
                        "period_memory_missing_metadata": [
                            "period_memory_override_reason"
                        ],
                        "period_memory_repair_actions": [
                            "add_period_memory_override_reason_before_scaling"
                        ],
                        "metadata_contract_audit_resolutions": [
                            "kept micro probe until override reason is restored"
                        ],
                        "period_memory_repair_quality": "repair_required",
                        "entry_bias": (
                            "wait_or_micro_probe_until_period_memory_gap_resolved"
                        ),
                        "sizing_policy": "micro_probe_until_period_memory_gap_repaired",
                        "required_evidence": [
                            "period_memory_coverage_gap",
                            "fresh_period_review_or_replay",
                        ],
                        "required_checks": [
                            "require_period_memory_fresh_review_or_replay"
                        ],
                    }
                ],
            }
        },
        scope="binance",
        compact_value=lambda value, **_: value,
    )

    constraint = compact["block_design_constraints"][0]
    assert constraint["policy_id"] == "period_memory_coverage.gap_unresolved"
    assert constraint["venue"] == "binance"
    assert constraint["period_memory_status"] == "gap_unresolved"
    assert constraint["period_memory_gap_count"] == 3
    assert constraint["period_memory_override_count"] == 0
    assert constraint["period_memory_contract_gap_count"] == 3
    assert constraint["period_memory_missing_metadata"] == [
        "period_memory_override_reason"
    ]
    assert constraint["period_memory_repair_actions"] == [
        "add_period_memory_override_reason_before_scaling"
    ]
    assert constraint["metadata_contract_audit_resolutions"] == [
        "kept micro probe until override reason is restored"
    ]
    assert constraint["metadata_contract_repair_note"] == (
        "metadata contract repair: add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    )
    assert constraint["period_memory_repair_quality"] == "repair_required"
    assert constraint["entry_bias"] == (
        "wait_or_micro_probe_until_period_memory_gap_resolved"
    )
    assert constraint["sizing_policy"] == (
        "micro_probe_until_period_memory_gap_repaired"
    )
    assert "period_memory_coverage_gap" in constraint["required_evidence"]
    assert "require_period_memory_fresh_review_or_replay" in constraint[
        "required_checks"
    ]


def test_validation_repair_action_metadata_summarizes_repair_without_raw_sections() -> None:
    long_reason = "validation narrative " * 100

    metadata = validation_repair_action_metadata(
        {
            "scope": "binance",
            "status": "active",
            "repair_item_count": 1,
            "constraint_count": 1,
            "repair_backlog": [
                {
                    "policy_id": "validation.walk_forward",
                    "repair_action_id": "collect_wfa",
                    "discipline_id": "walk_forward_analysis",
                    "last_repair_reason": long_reason,
                    "required_evidence": ["wfa_result"],
                    "required_checks": ["oos_pass"],
                }
            ],
            "block_design_constraints": [
                {
                    "policy_id": "validation.walk_forward",
                    "discipline_id": "walk_forward_analysis",
                    "entry_bias": "waiting_probe",
                    "scale_blocker": "sample_cap",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.5,
                    "min_reward_risk": 2.0,
                    "max_stop_risk_pct": 0.02,
                    "scale_up_blocked": True,
                    "live_shadow_required": True,
                }
            ],
        }
    )

    repair = metadata["validation_repair"]
    assert repair["scope"] == "binance"
    assert repair["discipline_ids"] == ["walk_forward_analysis"]
    assert repair["entry_biases"] == ["waiting_probe"]
    assert repair["scale_blockers"] == ["sample_cap"]
    assert repair["risk_budget_multiplier"] == 0.25
    assert repair["max_budget_multiplier"] == 0.5
    assert repair["min_reward_risk"] == 2.0
    assert repair["max_stop_risk_pct"] == 0.02
    assert repair["scale_up_blocked"] is True
    assert repair["live_shadow_required"] is True
    assert repair["raw_sections_omitted"] is True
    assert repair["hard_filter"] is False
    assert repair["last_repair_reasons"] == [
        f"[omitted_long_text chars={len(long_reason.strip())}]"
    ]


def test_validation_repair_action_metadata_preserves_period_memory_summary() -> None:
    metadata = validation_repair_action_metadata(
        {
            "scope": "binance",
            "status": "active",
            "constraint_count": 1,
            "block_design_constraints": [
                {
                    "policy_id": "period_memory_coverage.gap_unresolved",
                    "period_memory_status": "gap_unresolved",
                    "period_memory_gap_count": 3,
                    "period_memory_override_count": 0,
                    "period_memory_contract_gap_count": 3,
                    "period_memory_missing_metadata": [
                        "period_memory_override_reason"
                    ],
                    "period_memory_repair_actions": [
                        "add_period_memory_override_reason_before_scaling"
                    ],
                    "metadata_contract_audit_resolutions": [
                        "kept micro probe until override reason is restored"
                    ],
                    "period_memory_repair_quality": "repair_required",
                    "entry_bias": (
                        "wait_or_micro_probe_until_period_memory_gap_resolved"
                    ),
                    "required_checks": [
                        "require_period_memory_fresh_review_or_replay"
                    ],
                }
            ],
        }
    )

    repair = metadata["validation_repair"]
    assert repair["period_memory_statuses"] == ["gap_unresolved"]
    assert repair["period_memory_gap_count"] == 3
    assert repair["period_memory_override_count"] == 0
    assert repair["period_memory_contract_gap_count"] == 3
    assert repair["period_memory_missing_metadata"] == [
        "period_memory_override_reason"
    ]
    assert repair["period_memory_repair_actions"] == [
        "add_period_memory_override_reason_before_scaling"
    ]
    assert repair["metadata_contract_audit_resolutions"] == [
        "kept micro probe until override reason is restored"
    ]
    assert repair["metadata_contract_repair_notes"] == [
        "metadata contract repair: add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    ]
    assert repair["period_memory_repair_qualities"] == ["repair_required"]
    assert "period_memory_coverage.gap_unresolved" in repair["policy_ids"]
    assert "require_period_memory_fresh_review_or_replay" in repair[
        "required_checks"
    ]
    assert repair["raw_sections_omitted"] is True


def test_validation_repair_note_and_discipline_tokens_are_compact() -> None:
    repair = {
        "discipline_ids": ["Walk Forward Analysis", "out/of sample"],
        "entry_biases": ["waiting_probe"],
        "period_memory_repair_qualities": ["successful_repair"],
        "repair_backlog": [{"discipline_id": "live shadow"}],
        "block_design_constraints": [{"discipline_id": "walk_forward_analysis"}],
    }

    assert validation_repair_note(repair) == (
        "19검증 반영 - 검증항목=Walk Forward Analysis,out/of sample / "
        "진입성향=waiting_probe / 메모리수리=successful_repair"
    )
    assert validation_repair_discipline_tokens(repair) == [
        "walk_forward_analysis",
        "out_of_sample",
        "live_shadow",
    ]


def test_validation_evidence_plan_from_repair_maps_disciplines_to_required_dimensions() -> None:
    plan = validation_evidence_plan_from_repair(
        {
            "status": "active",
            "discipline_ids": [
                "walk_forward_analysis",
                "out_of_sample",
                "live_shadow",
                "unmapped",
            ],
            "required_evidence": ["wfa_result", "oos_result"],
            "required_checks": ["positive_expectancy"],
            "pass_collection_hooks": ["run_walk_forward"],
            "pass_current_gaps": ["not_enough_samples"],
            "pass_criteria": ["profit_factor_above_1_2"],
            "verification_artifacts": ["walk_forward_report"],
            "scale_up_blocked": True,
            "live_shadow_required": True,
        }
    )

    assert plan == {
        "version": "validation_evidence_plan_v1",
        "source": "validation_repair",
        "status": "repair_required",
        "required_dimensions": ["walk_forward", "out_of_sample", "live_shadow"],
        "missing_dimensions": ["walk_forward", "out_of_sample", "live_shadow"],
        "required_evidence": ["wfa_result", "oos_result"],
        "required_checks": ["positive_expectancy"],
        "pass_collection_hooks": ["run_walk_forward"],
        "pass_current_gaps": ["not_enough_samples"],
        "pass_criteria": ["profit_factor_above_1_2"],
        "verification_artifacts": ["walk_forward_report"],
        "scale_up_blocked": True,
        "live_shadow_required": True,
    }


def test_validation_evidence_plan_from_repair_returns_empty_for_missing_repair() -> None:
    assert validation_evidence_plan_from_repair({}) == {}
    assert validation_evidence_plan_from_repair(None) == {}
