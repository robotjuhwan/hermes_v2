from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradecraft.services import kis_block_trader as kis_block_trader_module
from tradecraft.services import kis_manager_prompt as kis_manager_prompt_module
from tradecraft.services.jue_decision_packet import DECISION_PACKET_REQUIRED_SECTIONS
from tradecraft.services.kis_manager_prompt import (
    ETF_CANDIDATE_PROMPT_KEYS,
    ETF_SCORE_PROMPT_KEYS,
    ETF_SNAPSHOT_PROMPT_KEYS,
    PROMPT_BUDGET_COMPACTION_ORDER,
    PROMPT_BUDGET_OMITTABLE_SECTIONS,
    compact_daily_discovery_prompt,
    compact_etf_prompt_fields,
    compact_etf_prompt_value,
    compact_etf_universe_rows,
    compact_jue_wiki_prompt,
    compact_jue_wiki_memory_card_quality_prompt,
    compact_jue_wiki_repair_contract_prompt,
    compact_manager_prompt_context,
    compact_manager_storage_payload,
    compact_market_judgment_prompt,
    compact_manager_prompt_blocks,
    compact_prompt_block,
    compact_prompt_event,
    compact_prompt_events,
    compact_prompt_quote,
    compact_prompt_section,
    compact_validation_repair_prompt,
    build_kis_manager_prompt_payload,
    build_prompt_strategy_payload,
    compact_investment_memory_prompt,
    enforce_prompt_budget,
    finalize_prompt_budget,
    kis_trading_playbook,
    kis_manager_response_contract_error,
    manager_run_workflow_provenance,
    manager_storage_compaction_meta,
    prompt_budget_error,
    prompt_section_size_rows,
    public_prompt_payload,
    sanitize_creative_hypotheses,
    sanitize_kis_hold_decision,
    validation_evidence_plan_from_repair,
    validation_repair_action_metadata,
    validation_repair_discipline_tokens,
    validation_repair_note,
)


def _wiki_gate_storage_contracts() -> dict[str, object]:
    return {
        "jue_wiki_decision_gate": {
            "allow_new_risk": False,
            "allow_exit_actions": True,
            "reason": "wiki_required_coverage_missing",
            "read_mode": "required",
            "snapshot_id": "snapshot:kis:storage",
            "version": "wiki_decision_gate_v1",
            "untrusted_noise": "x" * 20_000,
        },
        "jue_wiki_decision_gate_policy": {"instruction": "preserve exits"},
        "jue_wiki_raw_rag_strip_audit": {
            "read_mode": "required",
            "snapshot_id": "snapshot:kis:storage",
            "removed_path_count": 200,
            "removed_paths": [f"raw_rag.items[{index}]" for index in range(200)],
        },
        "jue_wiki_suppression_audit": {
            "venue": "kis",
            "snapshot_id": "snapshot:kis:storage",
            "read_mode": "required",
            "reason": "wiki_required_coverage_missing",
            "original_action_count": 200,
            "filtered_action_count": 1,
            "suppressed_new_risk_count": 199,
            "suppressed_actions": [
                {
                    "venue": "kis",
                    "action_kind": "create_blocks",
                    "symbol": f"{index:06d}",
                    "block_id": "",
                    "snapshot_id": "snapshot:kis:storage",
                    "read_mode": "required",
                    "reason": "wiki_required_coverage_missing",
                }
                for index in range(200)
            ],
        },
    }


def test_kis_storage_compaction_preserves_wiki_gate_contracts_at_every_label() -> None:
    source = {
        **_wiki_gate_storage_contracts(),
        "decision_inputs": ["jue_wiki_decision_gate", "jue_wiki_raw_rag_strip_audit"],
        "create_blocks": [{"symbol": f"{index:06d}", "thesis": "x" * 500} for index in range(100)],
        "noise": "x" * 80_000,
    }

    for label in (
        "kis_manager_prompt",
        "kis_manager_response",
        "kis_manager_actions",
    ):
        compact = compact_manager_storage_payload(source, limit=1_500, label=label)

        assert compact["jue_wiki_decision_gate"]["snapshot_id"] == (
            "snapshot:kis:storage"
        )
        assert compact["jue_wiki_raw_rag_strip_audit"]["removed_path_count"] == 200
        assert compact["jue_wiki_suppression_audit"][
            "suppressed_new_risk_count"
        ] == 199
        assert compact["jue_wiki_decision_gate_policy"]["instruction"] == (
            "preserve exits"
        )
        assert "untrusted_noise" not in compact["jue_wiki_decision_gate"]


def test_kis_storage_compaction_bounds_adversarial_wiki_audit_strings() -> None:
    huge = "x" * 100_000
    source = _wiki_gate_storage_contracts()
    source["jue_wiki_decision_gate"]["reason"] = huge  # type: ignore[index]
    source["jue_wiki_decision_gate"]["snapshot_id"] = huge  # type: ignore[index]
    source["jue_wiki_raw_rag_strip_audit"]["removed_paths"] = [huge] * 100  # type: ignore[index]
    source["jue_wiki_suppression_audit"]["reason"] = huge  # type: ignore[index]

    for label in ("kis_manager_prompt", "kis_manager_response"):
        compact = compact_manager_storage_payload(source, limit=1_500, label=label)

        assert len(json.dumps(compact, ensure_ascii=False)) <= 1_500
        assert len(compact["jue_wiki_decision_gate"]["reason"]) <= 120
        assert len(compact["jue_wiki_decision_gate"]["snapshot_id"]) <= 120
        assert len(compact["jue_wiki_suppression_audit"]["reason"]) <= 120


@pytest.mark.parametrize("label", ["kis_manager_prompt", "kis_manager_response"])
@pytest.mark.parametrize("with_emergency_noise", [False, True])
def test_kis_valid_gate_identity_round_trips_through_storage(
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


def test_kis_block_trader_does_not_reown_manager_prompt_sanitizers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def _clean_symbol_list(" not in source
    assert "def _sanitize_hold_trigger(" not in source
    assert "def _sanitize_horizon_notes(" not in source
    assert "def _sanitize_kis_hold_decision(" not in source
    assert "def _normalize_creative_hypothesis_type(" not in source
    assert "def _normalize_creative_hypothesis_decision(" not in source
    assert "def _sanitize_creative_hypothesis_block(" not in source
    assert "def _sanitize_creative_hypothesis(" not in source
    assert "def _sanitize_creative_hypotheses(" not in source
    assert "def _compact_prompt_event(" not in source
    assert "def _compact_prompt_events(" not in source
    assert "def _compact_daily_discovery_prompt(" not in source


def test_kis_block_trader_does_not_reown_manager_prompt_compactors() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def _compact_prompt_quote(" not in source
    assert "def _compact_prompt_block(" not in source
    assert "def _prompt_block_needs_detail(" not in source
    assert "def _compact_prompt_block_backlog_item(" not in source
    assert "def _compact_manager_prompt_blocks(" not in source
    assert "def _compact_market_judgment_strategy(" not in source
    assert "def _compact_market_judgment_prompt(" not in source
    assert "def _manager_run_workflow_provenance(" not in source
    assert "def _public_payload(" not in source
    assert "def _compact_etf_prompt_value(" not in source
    assert "def _compact_etf_prompt_fields(" not in source
    assert "def _kis_trading_playbook(" not in source
    assert "def _prompt_section_size_rows(" not in source
    assert "def _prompt_chars(" not in source
    assert "def _attach_prompt_budget(" not in source
    assert "def _prompt_budget_error(" not in source
    assert "def _format_prompt_budget_alert_message(" not in source
    assert "def _manager_storage_compaction_meta(" not in source
    assert "def _compact_manager_storage_payload(" not in source
    assert "def _validation_repair_action_metadata(" not in source
    assert "def _validation_evidence_plan_from_repair(" not in source
    assert "def _validation_repair_note(" not in source
    assert "def _validation_repair_discipline_tokens(" not in source
    assert "def _compact_prompt_section(" not in source
    assert "def _compact_validation_repair_prompt(" not in source
    assert "def _enforce_prompt_budget(" not in source
    assert "def _finalize_prompt_budget(" not in source
    assert "def _compact_etf_universe_rows(" not in source


def test_compact_jue_wiki_prompt_preserves_freshness_metadata() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:kis-freshness",
            "freshness_summary": {
                "page_count": 1,
                "status_counts": {"stale": 1},
                "warning_counts": {"updated_at_stale_gt_14d": 1},
                "stale_page_ids": ["kis:playbook"],
                "unknown_page_ids": [],
            },
            "pages": [
                {
                    "page_id": "kis:playbook",
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
        "stale_page_ids": ["kis:playbook"],
        "unknown_page_ids": [],
    }
    assert compact["pages"][0]["freshness_status"] == "stale"
    assert compact["pages"][0]["freshness_warnings"] == ["updated_at_stale_gt_14d"]


def test_compact_jue_wiki_prompt_preserves_repair_action_batches() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "target_scope": "kis",
            "repair_action_batches": [
                {
                    "scope": "kis",
                    "action_type": "refresh_symbol_financials",
                    "count": 2,
                    "symbols": ["005930", "000660"],
                    "warnings": ["valuation_missing"],
                    "recommended_actions": ["refresh_symbol_financials"],
                    "raw_notes": "x" * 500,
                }
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["repair_action_batches"] == [
        {
            "scope": "kis",
            "action_type": "refresh_symbol_financials",
            "count": 2,
            "symbols": ["005930", "000660"],
            "warnings": ["valuation_missing"],
            "recommended_actions": ["refresh_symbol_financials"],
        }
    ]


def test_compact_jue_wiki_prompt_preserves_repair_queue_summary() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "target_scope": "kis",
            "repair_queue": {
                "open_count": 2,
                "resolved_count": 1,
                "open_symbols": ["005930", "000660", "402340"],
                "open_action_batches": [
                    {
                        "scope": "kis",
                        "action_type": "refresh_symbol_financials",
                        "count": 2,
                        "symbols": ["005930", "000660"],
                        "warnings": ["valuation_missing"],
                        "recommended_actions": ["refresh_symbol_financials"],
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
        "open_symbols": ["005930", "000660", "402340"],
        "open_action_batches": [
            {
                "scope": "kis",
                "action_type": "refresh_symbol_financials",
                "count": 2,
                "symbols": ["005930", "000660"],
                "warnings": ["valuation_missing"],
                "recommended_actions": ["refresh_symbol_financials"],
            }
        ],
    }
    assert "DROP_ME" not in json.dumps(compact, ensure_ascii=False)


def test_compact_jue_wiki_prompt_preserves_nested_repair_queue_evidence() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "target_scope": "kis",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
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
                        "top_warnings": [
                            {"warning": "open_repair_queue", "count": 1},
                            {
                                "warning": "application_repair_queue_pressure",
                                "count": 1,
                            },
                        ],
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
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
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


def test_compact_jue_wiki_prompt_preserves_page_effectiveness_repair_pressure() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "target_scope": "kis",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "effectiveness": {
                        "status": "degraded",
                        "venue": "kis",
                        "horizon": "mid",
                        "sample_count": 3,
                        "win_rate": 0.0,
                        "expectancy": -0.8,
                        "helpful_score": -10.0,
                        "confidence": 0.8,
                        "reasons": [
                            "application_repair_queue_pressure",
                            "repair_queue_open_count:1",
                            "repair_queue_action:repair_application_repair_queue_pressure",
                            "DROP_ME_LONG_REASON" * 40,
                        ],
                    },
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "effectiveness": {
                        "status": "degraded",
                        "sample_count": 3,
                        "helpful_score": -10.0,
                        "reasons": [
                            "application_repair_queue_pressure",
                            "repair_queue_open_count:1",
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
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 3,
        "win_rate": 0.0,
        "expectancy": -0.8,
        "helpful_score": -10.0,
        "confidence": 0.8,
        "reasons": [
            "application_repair_queue_pressure",
            "repair_queue_open_count:1",
            "repair_queue_action:repair_application_repair_queue_pressure",
        ],
    }
    page = compact["pages"][0]
    summary = compact["requested_symbol_summaries"][0]
    assert page["effectiveness"] == expected
    assert summary["effectiveness"] == {
        "status": "degraded",
        "sample_count": 3,
        "helpful_score": -10.0,
        "reasons": [
            "application_repair_queue_pressure",
            "repair_queue_open_count:1",
        ],
    }
    assert "DROP_ME_LONG_REASON" not in json.dumps(compact, ensure_ascii=False)


def test_compact_jue_wiki_prompt_promotes_requested_symbol_coverage_from_budget_report() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "target_scope": "kis",
            "budget_report": {
                "char_count": 1200,
                "requested_symbol_count": 4,
                "requested_symbol_summary_symbols": ["005930", "000660"],
                "requested_symbol_unsummarized_count": 2,
                "requested_symbol_unsummarized_symbols": ["277810", "456040"],
                "requested_symbol_missing_summary_count": 1,
                "requested_symbol_missing_summary_symbols": ["456040"],
                "requested_symbol_prompt_omitted_count": 1,
                "requested_symbol_prompt_omitted_symbols": ["277810"],
                "requested_symbol_degraded_summary_count": 1,
                "requested_symbol_degraded_summary_symbols": ["000660"],
                "requested_symbol_summary_coverage_status": "partial",
                "verbose_debug": "DROP_ME" * 100,
            },
        },
        list_limit=2,
        string_limit=120,
    )

    assert compact["requested_symbol_coverage"] == {
        "status": "partial",
        "requested_symbol_count": 4,
        "summarized_symbol_count": 2,
        "unsummarized_symbol_count": 2,
        "unsummarized_symbols": ["277810", "456040"],
        "missing_summary_count": 1,
        "missing_summary_symbols": ["456040"],
        "prompt_omitted_count": 1,
        "prompt_omitted_symbols": ["277810"],
        "degraded_summary_count": 1,
        "degraded_summary_symbols": ["000660"],
    }
    assert "DROP_ME" not in json.dumps(compact["requested_symbol_coverage"])


def test_compact_jue_wiki_prompt_preserves_quality_warning_effectiveness() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:kis-quality-warning-effectiveness",
            "pages": [
                {
                    "page_id": "kis.symbol.277810",
                    "quality_warning_effectiveness": [
                        {
                            "warning": "price_missing",
                            "page_id": "kis.symbol.277810",
                            "status": "degraded",
                            "sample_count": 3,
                            "win_rate": 0.0,
                            "expectancy": -1.4,
                            "helpful_score": -9.5,
                            "confidence": 0.82,
                            "reasons": [
                                "quality_warning:price_missing",
                                "ignored_live_quote_gap",
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
            "warning": "price_missing",
            "page_id": "kis.symbol.277810",
            "status": "degraded",
            "sample_count": 3,
            "win_rate": 0.0,
            "expectancy": -1.4,
            "helpful_score": -9.5,
            "confidence": 0.82,
            "reasons": [
                "quality_warning:price_missing",
                "ignored_live_quote_gap",
            ],
        }
    ]
    assert compact["pages"][0]["quality_warning_effectiveness_statuses"] == [
        "degraded"
    ]


def test_compact_jue_wiki_prompt_preserves_selected_page_guidance_metadata() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:kis-guidance-metadata",
            "pages": [
                {
                    "page_id": "kis.symbol.277810",
                    "usage_guidance": {
                        "trust_level": "high",
                        "risk_posture": "standard_block_design",
                        "decision_use": (
                            "eligible_for_standard_block_design_after_live_checks"
                        ),
                        "allowed_uses": ["standard_block", "target_stop_context"],
                        "required_cross_checks": ["live_quote", "sector_flow"],
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
                                    "memory_card_quality.missing_field."
                                    "durable_facts"
                                ),
                                "status": "active",
                                "sample_count": 4,
                                "expectancy": 0.7,
                                "helpful_score": 6.0,
                                "confidence": 0.72,
                                "reasons": [
                                    "memory_card_quality:missing_field:durable_facts"
                                ],
                            }
                        ],
                    },
                    "quality_warning_source_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": "kis.symbol.277810",
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
        "required_cross_checks": ["live_quote", "sector_flow"],
        "hard_blocker": False,
    }
    assert page["usage_guidance_effectiveness"]["status"] == "active"
    assert page["usage_guidance_effectiveness"]["metrics"][0]["page_id"] == (
        "usage_guidance.risk_posture.standard_block_design"
    )
    assert page["memory_card_quality_effectiveness"]["metrics"][0]["page_id"] == (
        "memory_card_quality.missing_field.durable_facts"
    )
    assert page["quality_warning_source_effectiveness"]["metrics"][0][
        "page_id"
    ] == "kis.symbol.277810"


def test_compact_jue_wiki_prompt_compacts_effectiveness_attention_items() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:kis-attention-items",
            "effectiveness_attention_items": [
                {
                    "page_id": "kis.symbol.277810",
                    "kind": "usage_guidance",
                    "status": "active",
                    "evidence_id": (
                        "usage_guidance.risk_posture.standard_block_design"
                    ),
                    "verbose_note": "DROP_ME" * 100,
                },
                {
                    "page_id": "kis.symbol.277810",
                    "kind": "quality_warning",
                    "status": "degraded",
                    "warning": "price_missing",
                    "raw_payload": {"drop": True},
                },
            ],
        },
        list_limit=4,
        string_limit=120,
    )

    assert compact["effectiveness_attention_items"] == [
        {
            "page_id": "kis.symbol.277810",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.standard_block_design",
        },
        {
            "page_id": "kis.symbol.277810",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "price_missing",
        },
    ]


def test_compact_jue_wiki_prompt_derives_effectiveness_attention_items_from_rows() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "selection_run_id": "selection:kis-row-attention-items",
            "pages": [
                {
                    "page_id": "kis.symbol.277810",
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
                            }
                        ],
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "price_missing",
                            "page_id": "kis.symbol.277810",
                            "status": "degraded",
                            "sample_count": 3,
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
            "page_id": "kis.symbol.277810",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.standard_block_design",
        },
        {
            "page_id": "kis.symbol.277810",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "price_missing",
        },
    ]


def test_manager_run_workflow_provenance_extracts_skill_and_contract_ids() -> None:
    provenance = manager_run_workflow_provenance(
        {
            "jue_workflow": {
                "workflow_id": "kis_cycle",
                "workflow_version": "4",
                "skills": [
                    {"skill_id": "jue-kis-trading"},
                    {"skill_id": " "},
                    {"ignored": "value"},
                ],
                "contracts": [
                    {"contract_id": "block_action"},
                    {"contract_id": "risk_gate"},
                ],
            }
        }
    )

    assert provenance["workflow_id"] == "kis_cycle"
    assert provenance["workflow_version"] == 4
    assert json.loads(provenance["skill_ids_json"]) == ["jue-kis-trading"]
    assert json.loads(provenance["contract_ids_json"]) == ["block_action", "risk_gate"]


def test_manager_run_workflow_provenance_handles_missing_or_invalid_workflow() -> None:
    provenance = manager_run_workflow_provenance(
        {"jue_workflow": {"workflow_version": "not-a-number"}}
    )

    assert provenance["workflow_id"] == ""
    assert provenance["workflow_version"] == 0
    assert provenance["skill_ids_json"] == "[]"
    assert provenance["contract_ids_json"] == "[]"


def test_compact_prompt_section_jue_wiki_canonicalizes_quality_aliases() -> None:
    compact = compact_prompt_section(
        "jue_wiki",
        {
            "status": "ok",
            "selection_run_id": "selection:kis-manager-prompt-aliases",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "quality_status": "degraded",
                    "source_refs": [
                        {
                            "source_type": "naver_fundamentals",
                            "source_id": "005930:manager-prompt",
                            "quality_status": "degraded",
                            "quality_warnings": ["valuation_stale_gt_30d"],
                            "evidence_quality": {
                                "status_counts": {"ok": 1, "degraded": 1},
                                "top_warnings": [
                                    {"warning": "valuation_stale_gt_30d", "count": 1}
                                ],
                            },
                            "raw_blob": "drop-me",
                        }
                    ],
                    "evidence_quality": {
                        "status_counts": {"ok": 1, "degraded": 1},
                        "top_warnings": [
                            {"warning": "valuation_stale_gt_30d", "count": 1}
                        ],
                    },
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "title": "삼성전자",
                    "freshness": "current",
                    "freshness_status": "stale",
                    "freshness_warnings": ["updated_at_stale_gt_14d"],
                    "quality_status": "ok",
                    "summary": "요청 종목 위키 카드 보존. " + ("DROP_ME" * 60),
                    "memory_card": {
                        "stance": "mid value-cycle wait",
                        "durable_facts": "밸류 근거는 12시간 캐시 안에서만 신뢰한다.",
                        "open_questions": "외국인 수급 전환 여부를 확인한다.",
                        "lessons": "DROP_ME" * 80,
                    },
                    "evidence_quality": {
                        "status_counts": {"ok": 1, "degraded": 1},
                        "top_warnings": [
                            {"warning": "valuation_stale_gt_30d", "count": 1}
                        ],
                    },
                    "raw_blob": "drop-me",
                },
                {
                    "symbol": "000660",
                    "page_id": "kis.symbol.000660",
                    "quality_status": "ok",
                    "summary": "얇은 요청 종목 위키 카드.",
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
    assert summary["symbol"] == "005930"
    assert summary["freshness_status"] == "stale"
    assert summary["freshness_warnings"] == ["updated_at_stale_gt_14d"]
    assert summary["quality_status"] == "strong"
    assert summary["memory_card"] == {
        "stance": "mid value-cycle wait",
        "durable_facts": "밸류 근거는 12시간 캐시 안에서만 신뢰한다.",
        "open_questions": "외국인 수급 전환 여부를 확인한다.",
    }
    assert summary["memory_card_quality"] == {
        "status": "strong",
        "present_keys": ["stance", "durable_facts", "open_questions"],
        "missing_keys": ["lessons"],
    }
    assert summary["evidence_quality"]["status_counts"] == {"strong": 1, "weak": 1}
    assert len(summary["summary"]) <= 160
    assert "raw_blob" not in summary
    assert "DROP_ME" not in json.dumps(summary["memory_card"], ensure_ascii=False)
    thin_summary = compact["requested_symbol_summaries"][1]
    assert thin_summary["memory_card_quality"] == {
        "status": "weak",
        "present_keys": ["stance"],
        "missing_keys": ["durable_facts", "lessons", "open_questions"],
        "required_action": "cross_check_live_research_before_high_confidence",
    }


def test_compact_prompt_section_jue_wiki_preserves_memory_card_quality_details() -> None:
    compact = compact_prompt_section(
        "jue_wiki",
        {
            "status": "ok",
            "selection_run_id": "selection:kis-memory-card-quality-details",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "symbols": ["005930"],
                    "memory_card_quality": {
                        "status": "active",
                        "resolution": "unresolved",
                        "symbols": ["005930"],
                        "required_action": (
                            "cross_check_live_research_before_high_confidence"
                        ),
                        "missing_fields": ["durable_facts", "lessons"],
                        "required_checks": [
                            "refresh_durable_facts",
                            "inspect_block_lessons",
                        ],
                        "items": [
                            {
                                "status": "active",
                                "resolution": "unresolved",
                                "symbols": ["005930"],
                                "required_action": (
                                    "cross_check_live_research_before_high_confidence"
                                ),
                                "missing_fields": ["durable_facts", "lessons"],
                                "required_checks": [
                                    "refresh_durable_facts",
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
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "memory_card": {"stance": "thin watch only"},
                    "memory_card_quality": {
                        "status": "active",
                        "resolution": "unresolved",
                        "symbols": ["005930"],
                        "required_action": (
                            "cross_check_live_research_before_high_confidence"
                        ),
                        "missing_fields": ["durable_facts", "lessons"],
                        "required_checks": [
                            "refresh_durable_facts",
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
        "symbols": ["005930"],
        "required_action": "cross_check_live_research_before_high_confidence",
        "missing_fields": ["durable_facts", "lessons"],
        "required_checks": ["refresh_durable_facts", "inspect_block_lessons"],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }
    assert compact["pages"][0]["memory_card_quality"] == {
        **expected_quality,
        "items": [
            {
                "status": "active",
                "resolution": "unresolved",
                "symbols": ["005930"],
                "required_action": (
                    "cross_check_live_research_before_high_confidence"
                ),
                "missing_fields": ["durable_facts", "lessons"],
                "required_checks": [
                    "refresh_durable_facts",
                    "inspect_block_lessons",
                ],
            }
        ],
    }
    assert compact["requested_symbol_summaries"][0]["memory_card_quality"] == (
        expected_quality
    )


def test_kis_trading_playbook_keeps_value_cycle_and_exit_guardrails() -> None:
    playbook = kis_trading_playbook()

    assert playbook["version"] == "kis_trading_playbook_v1"
    assert playbook["style"] == "aggressive_value_cycle"
    assert "undervaluation" in playbook["entry_evidence_stack"]
    assert playbook["lanes"]["value_cycle"]["default_new_entry_style"] == (
        "wait_for_price"
    )
    assert playbook["lanes"]["core_etf"]["target_stop_semantics"].startswith(
        "For core_etf blocks"
    )
    force_exit_policy = playbook["behavior_separation"]["force_exit_policy"]
    assert force_exit_policy["requires_invalidation"] is True
    assert "target_reached" in force_exit_policy["allowed_triggers"]
    assert "operator_confirmed" in force_exit_policy["allowed_triggers"]
    assert playbook["horizon_review"]["patience_guard"][
        "minimum_live_age_before_discretionary_close"
    ] == {"mid": "72h", "long": "14d", "core_etf": "7d"}


def test_build_kis_manager_prompt_payload_preserves_core_policy_contract() -> None:
    language_policy = {
        "internal_reasoning_language": "en-US",
        "operator_display_language": "ko-KR",
    }
    metadata_schema = {
        "target_block_value_krw": "optional number",
        "decision_class": "optional string",
    }

    prompt = build_kis_manager_prompt_payload(
        clock={"session": "regular"},
        account={"cash_krw": 1_000_000},
        blocks=[{"block_id": "kis-1", "symbol": "005930"}],
        block_backlog_summary={"omitted_count": 0},
        quotes=[{"symbol": "005930", "price": 80_000}],
        pre_adoption_symbol_analysis={"005930": {"stance": "mid"}},
        allocation={"cash_weight": 0.3},
        portfolio_balance={"targets": {"cash": 0.3}},
        etf_universe=[{"symbol": "069500", "name": "KODEX 200"}],
        etf_research={"status": "active"},
        recent_events=[{"event_type": "manager_note"}],
        decision_packet_v2={
            "version": "decision_packet_v2",
            "schema": {"target_scope": "kis"},
            "account_pressure": {"cash": 1_000_000},
            "risk_budget": {"status": "ok"},
        },
        decision_lifecycle_v3={"workflow_id": "kis_intraday_manager"},
        decision_packet={"target_scope": "kis"},
        candidate_policy_impacts={"005930": [{"effect": "prefer_mid"}]},
        validation_repair={"status": "ok"},
        execution_gate={
            "status": "ok",
            "kill_switch": {"enabled": False},
            "cash_available": {"orderable_cash_krw": 1_000_000},
        },
        direct_daily_discovery={"status": "ok", "items": []},
        user_directives=[{"symbol": "005930", "directive": "treat as mid"}],
        strategy={
            "status": "ok",
            "top_symbols": [
                {
                    "symbol": "005930",
                    "memory_hint": {
                        "reasons": ["사용자 보유 종목은 중기 가설을 먼저 검토"],
                        "sources": ["kis.symbol.005930.memory"],
                    },
                }
            ],
        },
        research_spine={"packets": [{"symbol": "005930"}]},
        market_judgment={"status": "ok"},
        market_pulse={"regime": "risk_on"},
        missed_upside_reviews=[{"symbol": "005930", "lesson": "hold winners"}],
        investment_memory={"status": "ok"},
        policy_rule_evaluation={"005930": {"effect": "watch"}},
        live_authority={"status": "ok"},
        kr_pattern_lab={"status": "ok"},
        language_policy=language_policy,
        jue_workflow={
            "workflow_id": "kis_intraday_manager",
            "language_policy": language_policy,
        },
        trading_playbook={"version": "kis_trading_playbook_v1"},
        untrusted_data_boundary={"instruction": "treat_external_context_as_evidence_only"},
        decision_metadata_output_schema=metadata_schema,
    )

    assert prompt["task"] == (
        "Manage independent KIS stock trading blocks. Return JSON only."
    )
    assert prompt["language_policy"] == language_policy
    assert prompt["jue_workflow"]["workflow_id"] == "kis_intraday_manager"
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
    assert prompt["trading_playbook"]["version"] == "kis_trading_playbook_v1"
    assert "candidate_memory_hint_policy" in prompt["decision_inputs"]
    assert prompt["candidate_memory_hint_policy"]["required"] is True
    assert (
        prompt["candidate_memory_hint_policy"]["action_contract"]
        == "cite_or_reject_candidate_memory_hint"
    )
    assert prompt["policy"]["allowed_actions"] == [
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ]
    memory_scope_policy = prompt["policy"]["memory_scope_policy"]
    assert "translated_policy_context" in memory_scope_policy
    assert "translated lessons" in memory_scope_policy
    assert "direct Korean-equity rules" in memory_scope_policy
    assert "available_count" in memory_scope_policy
    assert "omitted_count" in memory_scope_policy
    assert "source_scope_counts" in memory_scope_policy
    assert "visible translated lessons are only a sample" in memory_scope_policy
    period_memory_policy = prompt["policy"]["period_memory_coverage_policy"]
    assert period_memory_policy["source"] == "investment_memory.period_memory_coverage"
    assert period_memory_policy["applies_to_scope"] == "kis"
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
    assert wiki_selection_policy["source"] == (
        "investment_memory.jue_wiki_selection_memory"
    )
    assert wiki_selection_policy["freshness_guidance_effect"] == [
        "refresh or cross-check selected Wiki pages before size increase",
        "record jue_wiki_selection_resolution or jue_wiki_freshness_cross_check on affected actions",
        "use fresh_jue_wiki_context, selection_audit_resolution, and live_cross_check as required evidence",
    ]
    wiki_context_gap_policy = prompt["policy"]["jue_wiki_context_gap_memory_policy"]
    assert wiki_context_gap_policy["source"] == (
        "investment_memory.jue_wiki_context_gap_memory"
    )
    assert wiki_context_gap_policy["gap_guidance_effect"] == [
        "verify Wiki context availability before high-confidence action",
        "record jue_wiki_context_gap on affected actions when Wiki remains unavailable",
        "use fresh_jue_wiki_context or live_cross_check as required evidence",
    ]
    wiki_reference_policy = prompt["policy"][
        "jue_wiki_action_reference_memory_policy"
    ]
    assert wiki_reference_policy["source"] == (
        "investment_memory.jue_wiki_action_reference_memory"
    )
    assert wiki_reference_policy["reference_guidance_effect"] == [
        "attach jue_wiki_freshness_cross_check or jue_wiki_selection_resolution when selected Wiki memory influences an action",
        "if an action does not use Wiki memory, record the live/research basis that overrode Wiki memory",
        "use live_cross_check before allowing high confidence without an action-level Wiki reference",
    ]
    wiki_usage_policy = prompt["policy"]["jue_wiki_usage_contract_policy"]
    assert wiki_usage_policy["source"] == "jue_wiki_application.trust_profile"
    assert wiki_usage_policy["memory_source"] == (
        "investment_memory.jue_wiki_usage_contract_memory"
    )
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
    assert "live_quote" in wiki_usage_policy["required_cross_checks"]
    assert "account_state" in wiki_usage_policy["required_cross_checks"]
    for action_name in (
        "adopt_existing_blocks",
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
        assert "investment_memory.jue_wiki_usage_contract_memory" in (
            usage_contract_schema
        )
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
    wiki_effectiveness_policy = prompt["policy"]["jue_wiki_effectiveness_policy"]
    assert "active Wiki pages" in wiki_effectiveness_policy
    assert "probe Wiki pages" in wiki_effectiveness_policy
    assert "degraded Wiki pages" in wiki_effectiveness_policy
    assert "repair/probe evidence" in wiki_effectiveness_policy
    assert "pre_adoption_symbol_analysis" in prompt["policy"][
        "existing_position_adoption"
    ]
    assert prompt["decision_packet_v2"]["version"] == "decision_packet_v2"
    assert prompt["decision_packet"]["target_scope"] == "kis"
    assert prompt["execution_gate"]["status"] == "ok"
    assert prompt["execution_gate"]["kill_switch"]["enabled"] is False
    assert prompt["canonical_decision_packet"]["target_scope"] == "kis"
    assert prompt["canonical_decision_packet"]["packet_identity"] == {
        "version": "canonical_decision_packet_prompt_v1",
        "schema_version": "decision_packet_v2",
        "target_scope": "kis",
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
    assert "decision_packet only for legacy policy metadata" in prompt[
        "decision_packet_policy"
    ]
    assert prompt["canonical_decision_packet"]["present_sections"] == [
        "account_pressure",
        "risk_budget",
    ]
    assert prompt["canonical_decision_packet"]["decision_packet_status"] == "invalid"
    assert "decision_packet_missing_sections" in prompt["canonical_decision_packet"][
        "decision_warnings"
    ]
    assert "canonical_decision_packet" in prompt["decision_inputs"]
    assert prompt["decision_inputs"].index("decision_packet_v2") < prompt[
        "decision_inputs"
    ].index("decision_packet")
    assert "research_spine" in prompt["decision_inputs"]
    assert "execution_gate" in prompt["decision_inputs"]
    assert "trading_playbook" in prompt["decision_inputs"]
    assert "missed_upside_reviews" in prompt["decision_inputs"]
    assert "daily_discovery" in prompt["decision_inputs"]
    assert "validation_repair_response_contract" in prompt["decision_inputs"]
    repair_contract = prompt["validation_repair_response_contract"]
    assert repair_contract["blanket_hold_allowed"] is False
    assert "jue_wiki_contract_feedback_gap" in repair_contract["required_when"]
    assert "jue_wiki_memory_card_quality" in repair_contract["required_when"]
    assert "not blanket no-action reasons" in repair_contract["core_rule"]
    assert "jue_wiki_repair_pressure" in repair_contract["core_rule"]
    assert "thin Wiki memory cards" in repair_contract["core_rule"]
    assert "validation_repair_resolution" in prompt["output_schema"]
    assert "jue_wiki_contract_feedback_gap" in prompt["output_schema"][
        "validation_repair_resolution"
    ]["required"]
    assert "jue_wiki_memory_card_quality" in prompt["output_schema"][
        "validation_repair_resolution"
    ]["required"]
    assert prompt["output_schema"]["validation_repair_resolution"][
        "blanket_hold_allowed"
    ] is False
    assert prompt["daily_discovery"]["status"] == "ok"
    create_schema = prompt["output_schema"]["create_blocks"][0]
    assert create_schema["horizon"] == "short|mid|long|core_etf"
    assert create_schema["entry_style"] == "aggressive_limit|wait_for_price"
    assert create_schema["target_block_value_krw"] == "optional number"
    assert "jue_wiki_repair_pressure" in create_schema
    assert "degraded Wiki effectiveness" in create_schema[
        "jue_wiki_repair_resolution"
    ]
    assert "action metadata" in create_schema["jue_wiki_repair_resolution"]
    for action_key in (
        "adopt_existing_blocks",
        "create_blocks",
        "update_blocks",
        "close_blocks",
        "pause_blocks",
    ):
        schema = prompt["output_schema"][action_key][0]
        assert schema["decision_class"] == "optional string"
        assert "jue_wiki_repair_resolution" in schema
        assert "degraded Wiki effectiveness" in schema[
            "jue_wiki_repair_resolution"
        ]


def test_kis_manager_response_contract_rejects_unresolved_repair_hold() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_ignores_binance_scoped_validation_repair() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {
                "target_scope": "binance",
                "repair_item_count": 1,
                "constraint_count": 1,
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "KIS judgment proceeds"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )

    assert error == ""


def test_kis_manager_response_contract_accepts_concrete_repair_resolution() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "장중 체결강도 회복 확인 전까지 보류",
                    }
                ]
            }
        },
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "005930 rejected until evidence improves"},
    )

    assert error == ""


def test_kis_manager_response_contract_rejects_negative_repair_resolution() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
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
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "005930 repair still unresolved"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_rejects_memory_contract_repair_on_wrong_symbol_action() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {
                "scope": "kis",
                "status": "active",
                "repair_item_count": 1,
                "block_design_constraints": [
                    {
                        "symbol": "005930",
                        "memory_contract": "period_memory_override_reason",
                        "memory_contract_error": "missing override reason",
                        "required_checks": ["require_memory_contract_resolution"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "thesis": "period_memory_override_reason 수리를 반영한 진입",
                    "metadata": {
                        "validation_repair": (
                            "resolution: period_memory_override_reason 수리 note를 기록"
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


def test_kis_manager_accepts_memory_contract_repair_on_matching_symbol_action() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {
                "scope": "kis",
                "status": "active",
                "repair_item_count": 1,
                "block_design_constraints": [
                    {
                        "symbol": "005930",
                        "memory_contract": "period_memory_override_reason",
                        "memory_contract_error": "missing override reason",
                        "required_checks": ["require_memory_contract_resolution"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "thesis": "period_memory_override_reason 수리를 반영한 진입",
                    "metadata": {
                        "validation_repair": (
                            "resolution: period_memory_override_reason 수리 note를 기록"
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


def test_kis_manager_rejects_negative_memory_contract_repair_resolution() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {
                "scope": "kis",
                "status": "active",
                "repair_item_count": 1,
                "block_design_constraints": [
                    {
                        "symbol": "005930",
                        "memory_contract": "period_memory_override_reason",
                        "memory_contract_error": "missing override reason",
                        "required_checks": ["require_memory_contract_resolution"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "thesis": "period_memory_override_reason 수리를 반영하지 못함",
                    "metadata": {
                        "validation_repair": (
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


def test_kis_manager_run_diagnostics_tracks_memory_contract_resolution() -> None:
    prompt = {
        "validation_repair": {
            "scope": "kis",
            "status": "active",
            "repair_item_count": 2,
            "block_design_constraints": [
                {
                    "symbol": "005930",
                    "memory_contract": "period_memory_override_reason",
                    "memory_contract_error": "missing override reason",
                    "required_checks": ["require_memory_contract_resolution"],
                },
                {
                    "symbol": "000660",
                    "memory_contract": "fresh_period_review_or_replay",
                    "memory_contract_error": "missing fresh period review",
                    "required_checks": ["require_memory_contract_resolution"],
                },
            ],
        }
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "thesis": "period_memory_override_reason 수리를 반영",
                    "metadata": {
                        "validation_repair": (
                            "resolution: period_memory_override_reason 수리 note"
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
    compact = kis_manager_prompt_module.compact_kis_manager_diagnostics_for_storage(
        diagnostics
    )

    assert diagnostics["memory_contract_status"] == "partial"
    assert diagnostics["memory_contract_count"] == 2
    assert diagnostics["memory_contract_resolved_count"] == 1
    assert diagnostics["memory_contract_unresolved_count"] == 1
    assert diagnostics["memory_contract_missing_symbols"] == ["000660"]
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
            "symbol": "000660",
            "status": "unresolved",
            "contracts": ["fresh_period_review_or_replay"],
            "errors": ["missing fresh period review"],
            "resolution_modes": [],
        },
        {
            "symbol": "005930",
            "status": "resolved",
            "contracts": ["period_memory_override_reason"],
            "errors": ["missing override reason"],
            "resolution_modes": ["action_metadata"],
        },
    ]
    assert diagnostics["blocker_tags"]["unresolved_memory_contract"] == 1
    assert compact["memory_contract_status"] == "partial"
    assert compact["memory_contract_missing_symbols"] == ["000660"]
    assert compact["memory_contract_missing_contracts"] == [
        "fresh_period_review_or_replay"
    ]
    assert compact["memory_contract_missing_errors"] == [
        "missing fresh period review"
    ]
    assert compact["memory_contract_resolution_modes"] == ["action_metadata"]
    assert compact["memory_contract_action_resolved_count"] == 1
    assert compact["memory_contract_rows"] == diagnostics["memory_contract_rows"]


def test_kis_manager_run_diagnostics_rejects_negative_memory_contract_response_resolution() -> None:
    prompt = {
        "validation_repair": {
            "scope": "kis",
            "status": "active",
            "repair_item_count": 1,
            "block_design_constraints": [
                {
                    "symbol": "005930",
                    "memory_contract": "period_memory_override_reason",
                    "memory_contract_error": "missing override reason",
                    "required_checks": ["require_memory_contract_resolution"],
                }
            ],
        }
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
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
            "adopt_existing_blocks": [],
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


def test_kis_manager_contract_rejects_candidate_memory_hint_on_wrong_symbol_action() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "candidate_memory_hint_policy": {"required": True},
            "candidates": [
                {
                    "symbol": "005930",
                    "memory_hint": {
                        "reasons": [
                            "삼성전자 메모리는 밸류 매력이 생겨도 외국인 수급 회복 확인을 요구"
                        ],
                        "risks": ["수급 미확인 추격은 반복 손실"],
                        "sources": ["kis.symbol.005930.memory"],
                    },
                }
            ],
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "thesis": (
                        "삼성전자 메모리는 밸류 매력이 생겨도 외국인 수급 회복 확인을 요구"
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


def test_kis_manager_contract_accepts_candidate_memory_hint_on_matching_action() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "candidate_memory_hint_policy": {"required": True},
            "candidates": [
                {
                    "symbol": "005930",
                    "memory_hint": {
                        "reasons": [
                            "삼성전자 메모리는 밸류 매력이 생겨도 외국인 수급 회복 확인을 요구"
                        ],
                        "risks": ["수급 미확인 추격은 반복 손실"],
                        "sources": ["kis.symbol.005930.memory"],
                    },
                }
            ],
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "thesis": (
                        "삼성전자 메모리는 밸류 매력이 생겨도 외국인 수급 회복 확인을 요구"
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


def test_kis_manager_contract_rejects_negative_candidate_memory_hint_resolution() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "candidate_memory_hint_policy": {"required": True},
            "candidates": [
                {
                    "symbol": "005930",
                    "memory_hint": {
                        "reasons": [
                            "삼성전자 메모리는 밸류 매력이 생겨도 외국인 수급 회복 확인을 요구"
                        ],
                        "risks": ["수급 미확인 추격은 반복 손실"],
                        "sources": ["kis.symbol.005930.memory"],
                    },
                }
            ],
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "thesis": (
                        "삼성전자 메모리는 밸류 매력이 생겨도 외국인 수급 회복 확인을 요구"
                    ),
                    "metadata": {
                        "memory_hint_resolution": (
                            "candidate memory hint not applied; no fresh memory "
                            "context available for 005930"
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


def test_kis_manager_rejects_research_spine_memory_on_wrong_symbol_action() -> None:
    prompt = {
        "research_spine_policy": {
            "memory_application": {"required": True},
        },
        "research_spine": {
            "packets": [
                {
                    "symbol": "005930",
                    "symbol_memory": {
                        "reasons": [
                            "삼성전자 기억은 외국인 수급 회복 전 추격을 금지"
                        ],
                    },
                },
                {
                    "symbol": "000660",
                    "symbol_memory": {
                        "reasons": [
                            "하이닉스 기억은 과열 돌파보다 눌림 대기를 선호"
                        ],
                    },
                },
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "thesis": "삼성전자 기억은 외국인 수급 회복 전 추격을 금지",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "research_spine_memory_resolution_missing_from_model"


def test_kis_manager_accepts_research_spine_memory_on_matching_action() -> None:
    prompt = {
        "research_spine_policy": {
            "memory_application": {"required": True},
        },
        "research_spine": {
            "packets": [
                {
                    "symbol": "000660",
                    "symbol_memory": {
                        "reasons": [
                            "하이닉스 기억은 과열 돌파보다 눌림 대기를 선호"
                        ],
                    },
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "thesis": "하이닉스 기억은 과열 돌파보다 눌림 대기를 선호",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == ""


def test_kis_manager_rejects_negative_research_spine_memory_resolution() -> None:
    prompt = {
        "research_spine_policy": {
            "memory_application": {"required": True},
        },
        "research_spine": {
            "packets": [
                {
                    "symbol": "000660",
                    "symbol_memory": {
                        "reasons": [
                            "하이닉스 기억은 과열 돌파보다 눌림 대기를 선호"
                        ],
                    },
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "thesis": "하이닉스 기억은 과열 돌파보다 눌림 대기를 선호",
                    "metadata": {
                        "research_spine_memory_resolution": (
                            "research_spine_memory unavailable; fresh context absent"
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

    assert error == "research_spine_memory_resolution_missing_from_model"


def test_kis_manager_run_diagnostics_tracks_research_spine_memory_resolution() -> None:
    prompt = {
        "research_spine_policy": {
            "memory_application": {"required": True},
        },
        "research_spine": {
            "packets": [
                {
                    "symbol": "005930",
                    "symbol_memory": {
                        "reasons": [
                            "삼성전자 기억은 외국인 수급 회복 전 추격을 금지"
                        ],
                    },
                },
                {
                    "symbol": "000660",
                    "symbol_memory": {
                        "reasons": [
                            "하이닉스 기억은 과열 돌파보다 눌림 대기를 선호"
                        ],
                    },
                },
            ],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "thesis": "삼성전자 기억은 외국인 수급 회복 전 추격을 금지",
                },
                {
                    "symbol": "000660",
                    "qty": 1,
                    "thesis": "반도체 업황 기대",
                },
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    compact = kis_manager_prompt_module.compact_kis_manager_diagnostics_for_storage(
        diagnostics
    )

    assert diagnostics["research_spine_memory_status"] == "partial"
    assert diagnostics["research_spine_memory_count"] == 2
    assert diagnostics["research_spine_memory_resolved_count"] == 1
    assert diagnostics["research_spine_memory_unresolved_count"] == 1
    assert diagnostics["research_spine_memory_missing_symbols"] == ["000660"]
    assert diagnostics["blocker_tags"]["unresolved_research_spine_memory"] == 1
    assert compact["research_spine_memory_status"] == "partial"
    assert compact["research_spine_memory_missing_symbols"] == ["000660"]


def test_kis_manager_run_diagnostics_tracks_candidate_memory_hint_resolution() -> None:
    prompt = {
        "candidate_memory_hint_policy": {"required": True},
        "candidates": [
            {
                "symbol": "005930",
                "memory_hint": {
                    "reasons": ["삼성전자 메모리는 수급 회복 확인 후 진입을 선호"],
                    "risks": ["수급 미확인 추격은 반복 손실"],
                    "sources": ["kis.symbol.005930.memory"],
                },
            },
            {
                "symbol": "000660",
                "memory_hint": {
                    "reasons": ["하이닉스 메모리는 과열 돌파보다 눌림 대기를 선호"],
                    "risks": ["고점 추격 후 빠른 손절 반복"],
                    "sources": ["kis.symbol.000660.memory"],
                },
            },
        ],
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "thesis": "삼성전자 메모리는 수급 회복 확인 후 진입을 선호",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    compact = kis_manager_prompt_module.compact_kis_manager_diagnostics_for_storage(
        diagnostics
    )

    assert diagnostics["candidate_memory_hint_status"] == "partial"
    assert diagnostics["candidate_memory_hint_count"] == 2
    assert diagnostics["candidate_memory_hint_resolved_count"] == 1
    assert diagnostics["candidate_memory_hint_unresolved_count"] == 1
    assert diagnostics["candidate_memory_hint_missing_symbols"] == ["000660"]
    assert diagnostics["blocker_tags"]["unresolved_candidate_memory_hint"] == 1
    assert compact["candidate_memory_hint_status"] == "partial"
    assert compact["candidate_memory_hint_missing_symbols"] == ["000660"]


def test_kis_manager_response_contract_rejects_unaddressed_wiki_decision_adjustment() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "knowledge_spine",
                    "reason": "current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "관망"}},
        actions=actions,
        hold_decision={"summary": "관망"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_ignores_binance_scoped_wiki_decision_adjustment() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "binance",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "binance_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "국장에는 적용할 조정 없음"}},
        actions=actions,
        hold_decision={"summary": "국장에는 적용할 조정 없음"},
    )

    assert error == ""


def test_kis_manager_response_contract_requires_translation_evidence_for_translated_binance_wiki_decision_adjustment() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "binance",
            "transferability": "translated",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "binance_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "repair_probe 전환 대기"}},
        actions=actions,
        hold_decision={
            "summary": "repair_probe 전환은 live quote 확인 후 대기",
            "watch_symbols": ["005930"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": (
                        "repair_probe shift applies only after research_spine "
                        "and quote agree"
                    ),
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_accepts_translated_binance_wiki_decision_adjustment_with_translation_evidence() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "binance",
            "transferability": "translated",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "binance_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "translated risk posture trigger staged"}},
        actions=actions,
        hold_decision={
            "summary": (
                "translated_kr_equity_mapping: Binance repair_probe posture mapped "
                "to KIS research_spine, valuation, and quote checks"
            ),
            "watch_symbols": ["005930"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": (
                        "repair_probe shift applies only after translated_kr_equity_mapping "
                        "and live quote agree"
                    ),
                }
            ],
        },
    )

    assert error == ""


def test_kis_manager_response_contract_rejects_translated_wiki_decision_adjustment_response_without_translation_evidence() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "target_scope": "binance",
            "transferability": "translated",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "binance_current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
                        "resolution": "small_waiting_block",
                        "next_trigger": (
                            "repair_probe posture mapped to KIS quote and valuation checks"
                        ),
                    }
                ]
            }
        },
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_accepts_wiki_decision_adjustment_hold_trigger() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "knowledge_spine",
                    "reason": "current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "위키 자세 조정 대기"}},
        actions=actions,
        hold_decision={
            "summary": "knowledge_spine 전환은 live quote 확인 후 대기",
            "watch_symbols": ["005930"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": (
                        "knowledge_spine shift applies only after live_quote "
                        "and price location agree"
                    ),
                }
            ],
        },
    )

    assert error == ""


def test_kis_manager_response_contract_rejects_negative_wiki_decision_adjustment_hold_note() -> None:
    prompt = {
        "jue_wiki_decision_adjustments": {
            "version": "jue_wiki_decision_adjustments_v1",
            "status": "active",
            "adjustments": [
                {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "knowledge_spine",
                    "reason": "current_risk_posture_degraded",
                }
            ],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "knowledge_spine 보정 미적용"}},
        actions=actions,
        hold_decision={
            "summary": "knowledge_spine adjustment not applied",
            "watch_symbols": ["005930"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": (
                        "knowledge_spine shift not performed despite live_quote watch"
                    ),
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_requires_wiki_decision_adjustment_evidence_grade() -> None:
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
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "repair_probe 전환 대기"}},
        actions=actions,
        hold_decision={
            "summary": "repair_probe 전환은 live quote 확인 후 대기",
            "watch_symbols": ["005930"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": (
                        "repair_probe shift applies only after live_quote agrees"
                    ),
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_requires_wiki_execution_hint_on_actions() -> None:
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
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [
            {
                "symbol": "005930",
                "qty": 1,
                "entry_style": "waiting",
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

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_negative_wiki_decision_adjustment_action_note() -> None:
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
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [
            {
                "symbol": "005930",
                "qty": 1,
                "entry_style": "waiting",
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

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_negative_wiki_decision_adjustment_response_resolution() -> None:
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
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
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
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_unresolved_jue_wiki_validation_contract() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_validation_repair_contract": {
                "status": "repair_required",
                "requires_validation_repair_resolution": True,
                "contract_feedback_gap": {
                    "status": "missing_contract_outcomes",
                    "legacy_sample_count": 8,
                    "contract_sample_count": 0,
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_ignores_binance_jue_wiki_validation_contract() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_validation_repair_contract": {
                "target_scope": "binance",
                "status": "repair_required",
                "requires_validation_repair_resolution": True,
                "contract_feedback_gap": {
                    "status": "missing_contract_outcomes",
                    "legacy_sample_count": 8,
                    "contract_sample_count": 0,
                },
            },
            "jue_wiki_contract_feedback_gap": {
                "target_scope": "binance",
                "status": "missing_contract_outcomes",
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "KIS judgment proceeds"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )

    assert error == ""


def test_kis_manager_response_contract_rejects_unaddressed_wiki_attention_plan() -> None:
    error = kis_manager_response_contract_error(
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
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_unrelated_action_for_wiki_attention_plan() -> None:
    error = kis_manager_response_contract_error(
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
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "unrelated action"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "short"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated action"},
    )

    assert error == "validation_repair_resolution_missing_from_model"

    resolved_error = kis_manager_response_contract_error(
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
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "attention metadata recorded"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
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


def test_kis_manager_response_contract_accepts_wiki_attention_hold_trigger() -> None:
    error = kis_manager_response_contract_error(
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
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "repair attention staged"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "repair attention staged",
            "watch_symbols": ["245450"],
            "data_gaps": ["financials_missing"],
            "next_triggers": [
                {
                    "symbol": "245450",
                    "condition": "재무/수급 근거 갱신 뒤 눌림목 재검토",
                }
            ],
        },
    )

    assert error == ""


def test_kis_manager_response_contract_rejects_action_ignoring_wiki_repair_pressure() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 5,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "unrelated create"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "short"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated create"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_action_ignoring_wiki_repair_action_batches() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "status": "active",
                "action_batches": [
                    {
                        "scope": "kis",
                        "action_type": "refresh_symbol_fundamentals",
                        "count": 13,
                        "symbols": ["005930", "000660"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "unrelated create"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "short"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated create"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_ignores_binance_scoped_wiki_repair_contract() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "target_scope": "binance",
            "repair_priority_count": 8,
            "top_priorities": [
                {
                    "scope": "binance",
                    "source_id": "repair:financials:binance:ETHUSDT",
                    "symbols": ["ETHUSDT"],
                }
            ],
            "action_batches": [
                {
                    "scope": "binance",
                    "action_type": "refresh_crypto_research",
                    "count": 6,
                    "symbols": ["ETHUSDT", "NEARUSDT"],
                }
            ],
            "repair_pressure_action_plan": {
                "status": "compressed",
                "omitted_priority_count": 5,
                "required_response": "mention omitted crypto repair pressure",
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [
            {
                "symbol": "005930",
                "qty": 1,
                "metadata": {"horizon": "short"},
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "KIS judgment proceeds"}},
        actions=actions,
        hold_decision={"summary": "KIS judgment proceeds"},
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={"summary": "KIS judgment proceeds"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_repair_priority_count"] == 0
    assert diagnostics["jue_wiki_repair_action_batch_count"] == 0
    assert "unresolved_jue_wiki_repair_priorities" not in diagnostics["blocker_tags"]
    assert "unresolved_jue_wiki_repair_action_batches" not in diagnostics[
        "blocker_tags"
    ]


def test_kis_manager_response_contract_rejects_action_ignoring_wiki_selection_guidance() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "investment_memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_selection.kis."
                                "operational_memory_manager_contract_recovery"
                            ),
                            "selected_page_ids": ["kis.ops.manager_runs"],
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
                                "cross_check_page_ids": ["kis.ops.manager_runs"],
                            },
                        }
                    ],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "freshness guidance ignored"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 2,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "freshness guidance ignored"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_action_ignoring_unavailable_wiki_context() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "investment_memory": {
                "jue_wiki": {
                    "status": "error",
                    "available": False,
                    "reason": "wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "wiki context ignored"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wiki context ignored"},
    )

    assert error == "wiki_context_gap_resolution_missing_from_model"


def test_kis_manager_response_contract_accepts_action_explaining_unavailable_wiki_context() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "investment_memory": {
                "jue_wiki": {
                    "status": "error",
                    "available": False,
                    "reason": "wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "wiki context handled"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_context_gap": (
                            "wiki_context_provider_timeout 때문에 위키 컨텍스트는 "
                            "사용하지 않고 live_cross_check, research_spine, "
                            "valuation으로 보수 확인"
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


def test_kis_manager_response_contract_rejects_negative_unavailable_wiki_context_resolution() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "investment_memory": {
                "jue_wiki": {
                    "status": "error",
                    "available": False,
                    "reason": "wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "wiki context still unresolved"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_context_gap": (
                            "wiki_context_provider_timeout unresolved; "
                            "no live_cross_check or research_spine cross-check yet"
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


def test_kis_manager_ignores_binance_unavailable_wiki_context() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "investment_memory": {
                "jue_wiki": {
                    "target_scope": "binance",
                    "status": "error",
                    "available": False,
                    "reason": "crypto_wiki_context_provider_timeout",
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "KIS judgment proceeds"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )

    assert error == ""


def test_kis_manager_response_contract_accepts_wiki_selection_guidance_resolution() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "investment_memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "kis",
                    "items": [
                        {
                            "policy_id": (
                                "jue_wiki_selection.kis."
                                "operational_memory_manager_contract_recovery"
                            ),
                            "selected_page_ids": ["kis.ops.manager_runs"],
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
                                "cross_check_page_ids": ["kis.ops.manager_runs"],
                            },
                        }
                    ],
                }
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "freshness guidance resolved"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context refreshed for "
                            "kis.ops.manager_runs; live_cross_check keeps this "
                            "as a small probe before size increase"
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


def test_kis_manager_response_contract_rejects_action_ignoring_wiki_reference_memory() -> None:
    prompt = {
        "investment_memory": {
            "jue_wiki_action_reference_memory": {
                "status": "available",
                "target_scope": "kis",
                "items": [
                    {
                        "policy_id": "jue_wiki_action_reference_gap.kis.missing",
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
            "new_entry_allowed_by_session": True,
        },
    }
    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "wiki reference memory ignored"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "wiki reference memory ignored"},
    )
    resolved_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "wiki reference memory resolved"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap.kis.missing 보정: "
                            "fresh_jue_wiki_context 대신 live_cross_check, "
                            "research_spine, valuation 근거를 명시"
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


def test_kis_manager_response_contract_rejects_action_using_degraded_wiki_without_repair_metadata() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbols": ["005930"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": [
                                "application_repair_queue_pressure",
                                "repair_queue_open_count:1",
                            ],
                        },
                    }
                ]
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "degraded wiki still used"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "degraded wiki still used"},
    )

    assert error == "validation_repair_resolution_missing_from_model"

    resolved_error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbols": ["005930"],
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
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "degraded wiki cross-checked"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_repair_resolution": {
                            "page_id": "kis.symbol.005930",
                            "symbol": "005930",
                            "resolution": (
                                "degraded wiki used only after live quote and "
                                "sizing reduction cross-check"
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


def test_kis_manager_response_contract_requires_action_metadata_for_degraded_wiki_action() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbols": ["005930"],
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
                "new_entry_allowed_by_session": True,
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
                        "resolution": "small_waiting_block",
                        "next_trigger": (
                            "kis.symbol.005930 degraded wiki는 live quote와 "
                            "sizing reduction으로 교차확인"
                        ),
                    }
                ]
            }
        },
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response-only repair resolution"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_action_ignoring_omitted_wiki_repair_batches() -> None:
    error = kis_manager_response_contract_error(
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
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "unrelated create"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "short"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "unrelated create"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_accepts_action_with_wiki_repair_pressure_metadata() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 5,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "repair pressure handled"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_repair_pressure": {
                            "source_id": "repair:financials:kis:005930",
                            "resolution": "reduced_size_until_financials_refreshed",
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


def test_kis_manager_response_contract_rejects_vague_wiki_repair_metadata() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 5,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "repair pressure handled"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
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


def test_kis_manager_response_contract_rejects_repair_metadata_without_active_reference() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
                "repair_pressure_action_plan": {
                    "status": "compressed",
                    "omitted_priority_count": 5,
                    "required_response": "mention omitted repair pressure before sizing",
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "repair pressure handled"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
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


def test_kis_manager_response_contract_rejects_attention_metadata_bypassing_repair_reference() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                },
                "action_batches": [
                    {
                        "scope": "kis",
                        "action_type": "refresh_symbol_financials",
                        "count": 3,
                        "symbols": ["005930"],
                    }
                ],
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "attention metadata recorded"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
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


def test_kis_manager_response_contract_rejects_response_resolution_without_active_reference() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "000660",
                        "resolution": "small_waiting_block",
                        "next_trigger": "재무 갱신 후 눌림 재검토",
                    }
                ]
            }
        },
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "repair response recorded"},
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_hold_trigger_without_wiki_reference() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                },
                "action_batches": [
                    {
                        "scope": "kis",
                        "action_type": "refresh_symbol_financials",
                        "count": 3,
                        "symbols": ["005930"],
                    }
                ],
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "repair trigger staged"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "repair trigger staged",
            "watch_symbols": ["000660"],
            "data_gaps": ["financials_missing"],
            "next_triggers": [
                {
                    "symbol": "000660",
                    "condition": "재무 갱신 후 눌림 재검토",
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_run_diagnostics_reports_unresolved_wiki_attention_and_coverage() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                },
                "action_batches": [
                    {
                        "scope": "kis",
                        "action_type": "refresh_symbol_financials",
                        "count": 3,
                        "symbols": ["005930"],
                    }
                ],
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                    }
                ],
            },
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
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
        hold_decision={
            "summary": "coverage follow-up staged",
            "watch_symbols": ["277810"],
            "next_triggers": [
                {
                    "symbol": "277810",
                    "condition": "위키 요약 수집 후 재판단",
                }
            ],
        },
    )

    assert diagnostics["jue_wiki_attention_resolution_status"] == "unresolved"
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_attention_plan"] >= 3
    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["000660"]


def test_kis_manager_run_diagnostics_reads_nested_jue_wiki_requested_symbol_coverage() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "target_scope": "kis",
                "requested_symbol_coverage": {
                    "status": "partial",
                    "missing_summary_symbols": ["000660"],
                    "unsummarized_symbols": ["000660"],
                },
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "coverage gap not addressed"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["000660"]


def test_kis_manager_run_diagnostics_uses_nested_coverage_when_top_level_is_binance_scoped() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "target_scope": "binance",
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "jue_wiki": {
                "target_scope": "kis",
                "requested_symbol_coverage": {
                    "status": "partial",
                    "missing_summary_symbols": ["000660"],
                    "unsummarized_symbols": ["000660"],
                },
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "coverage gap not addressed"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["000660"]


def test_kis_manager_run_diagnostics_uses_nested_coverage_when_top_level_has_unscoped_crypto_symbols() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "jue_wiki": {
                "target_scope": "kis",
                "requested_symbol_coverage": {
                    "status": "partial",
                    "missing_summary_symbols": ["000660"],
                    "unsummarized_symbols": ["000660"],
                },
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "coverage gap not addressed"},
    )

    assert diagnostics["blocker_tags"]["unresolved_jue_wiki_requested_symbol_coverage"] >= 2
    assert diagnostics["jue_wiki_missing_summary_symbols"] == ["000660"]


def test_kis_manager_run_diagnostics_clears_requested_symbol_coverage_when_trigger_matches_symbol() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "coverage follow-up staged",
            "watch_symbols": ["000660"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "000660",
                    "condition": "위키 요약 수집 또는 장중 실시간 교차확인 후 재판단",
                }
            ],
        },
    )

    assert "unresolved_jue_wiki_requested_symbol_coverage" not in diagnostics[
        "blocker_tags"
    ]
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"


def test_kis_manager_run_diagnostics_clears_requested_symbol_coverage_when_action_metadata_matches_symbol() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_requested_symbol_coverage_resolution": (
                            "000660 requested_symbol_summary gap handled by "
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


def test_kis_manager_run_diagnostics_rejects_negative_requested_symbol_coverage_resolution() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_requested_symbol_coverage_resolution": (
                            "000660 requested_symbol_summary still missing; "
                            "no fresh wiki summary or live_cross_check yet"
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


def test_kis_manager_run_diagnostics_rejects_negative_requested_symbol_coverage_hold_resolution() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": (
                "000660 requested_symbol_summary still missing; "
                "no fresh wiki summary or live_cross_check yet"
            ),
            "watch_symbols": ["000660"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "000660",
                    "condition": "no live_cross_check yet; rebuild wiki first",
                }
            ],
        },
    )

    assert diagnostics["blocker_tags"][
        "unresolved_jue_wiki_requested_symbol_coverage"
    ] >= 2
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] == "partial"


def test_kis_manager_run_diagnostics_flags_unresolved_degraded_wiki_effectiveness() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbols": ["005930"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
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
        "kis.symbol.005930"
    ]
    assert diagnostics["degraded_jue_wiki_effectiveness_resolution_status"] == (
        "unresolved"
    )
    assert diagnostics["blocker_tags"]["unresolved_degraded_jue_wiki_effectiveness"] >= 3

    resolved = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbols": ["005930"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_repair_resolution": {
                            "page_id": "kis.symbol.005930",
                            "symbol": "005930",
                            "resolution": (
                                "degraded wiki used only after live quote and "
                                "sizing reduction cross-check"
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


def test_kis_manager_run_diagnostics_rejects_negative_degraded_wiki_resolution() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbols": ["005930"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_repair_resolution": {
                            "page_id": "kis.symbol.005930",
                            "symbol": "005930",
                            "resolution": (
                                "degraded wiki still unresolved for "
                                "kis.symbol.005930; no live quote or sizing "
                                "reduction cross-check yet"
                            ),
                        },
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


def test_kis_manager_run_diagnostics_ignores_binance_degraded_wiki_effectiveness() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "binance.symbol.NEARUSDT",
                        "scope": "binance",
                        "symbols": ["NEARUSDT"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["crypto_only_repair_queue_pressure"],
                        },
                    }
                ]
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS action should ignore Binance-only wiki pressure"},
    )

    assert diagnostics["degraded_jue_wiki_effectiveness_count"] == 0
    assert "unresolved_degraded_jue_wiki_effectiveness" not in diagnostics[
        "blocker_tags"
    ]


def test_kis_manager_run_diagnostics_flags_unresolved_wiki_selection_guidance() -> None:
    prompt = {
        "investment_memory": {
            "jue_wiki_selection_memory": {
                "status": "available",
                "target_scope": "kis",
                "items": [
                    {
                        "policy_id": (
                            "jue_wiki_selection.kis."
                            "operational_memory_manager_contract_recovery"
                        ),
                        "selected_page_ids": ["kis.ops.manager_runs"],
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
                            "cross_check_page_ids": ["kis.ops.manager_runs"],
                        },
                    }
                ],
            }
        }
    }
    unresolved = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context refreshed for "
                            "kis.ops.manager_runs; live_cross_check completed"
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


def test_kis_manager_run_diagnostics_rejects_negative_wiki_selection_guidance_resolution() -> None:
    prompt = {
        "investment_memory": {
            "jue_wiki_selection_memory": {
                "status": "available",
                "target_scope": "kis",
                "items": [
                    {
                        "policy_id": (
                            "jue_wiki_selection.kis."
                            "operational_memory_manager_contract_recovery"
                        ),
                        "selected_page_ids": ["kis.ops.manager_runs"],
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
                            "cross_check_page_ids": ["kis.ops.manager_runs"],
                        },
                    }
                ],
            }
        }
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context missing for "
                            "kis.ops.manager_runs; no live_cross_check yet"
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


def test_kis_manager_run_diagnostics_ignores_binance_wiki_selection_guidance() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "items": [
                        {
                            "policy_id": "jue_wiki_selection.binance.repair",
                            "selected_page_ids": ["binance.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "refresh Binance wiki before futures sizing"
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
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
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


def test_kis_manager_run_diagnostics_does_not_resolve_translated_binance_selection_guidance_without_translation_evidence() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_selection.binance.translated",
                            "selected_page_ids": ["binance.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "translate Binance selection repair into a KIS "
                                    "research freshness check"
                                ),
                                "required_evidence": [
                                    "fresh_jue_wiki_context",
                                    "translated_kr_equity_mapping",
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
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_selection_resolution": (
                            "fresh_jue_wiki_context refreshed for "
                            "binance.ops.manager_runs; live_cross_check uses "
                            "research_spine, valuation, and quote"
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


def test_kis_manager_run_diagnostics_resolves_translated_binance_selection_guidance_with_translation_evidence() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_selection_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_selection.binance.translated",
                            "selected_page_ids": ["binance.ops.manager_runs"],
                            "application_guidance": {
                                "status": "freshness_repair_required",
                                "manager_instruction": (
                                    "translate Binance selection repair into a KIS "
                                    "research freshness check"
                                ),
                                "required_evidence": [
                                    "fresh_jue_wiki_context",
                                    "translated_kr_equity_mapping",
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
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_selection_resolution": (
                            "translated_kr_equity_mapping: Binance selection repair "
                            "mapped to KIS research_spine, valuation, and quote "
                            "freshness; fresh_jue_wiki_context checked"
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


def test_kis_manager_run_diagnostics_tracks_wiki_application_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "selection_run_id": "wiki-selection-kis-1",
        },
    }
    missing = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    referenced = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.005930 checked "
                            "against live quote and valuation"
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
            "symbol": "005930",
            "qty": 1,
            "horizon": "mid",
        }
    ]
    assert referenced["jue_wiki_action_reference_status"] == "referenced"
    assert referenced["jue_wiki_action_reference_count"] == 1
    assert referenced["jue_wiki_action_reference_ratio"] == 1.0
    assert referenced["jue_wiki_action_reference_unscoped_page_ids"] == []
    assert referenced["jue_wiki_action_reference_missing_actions"] == []
    assert "missing_jue_wiki_action_reference" not in referenced["blocker_tags"]


def test_kis_manager_run_diagnostics_blocks_unscoped_wiki_action_reference() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={},
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.005930 checked"
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
        "kis.symbol.005930"
    ]
    assert diagnostics["jue_wiki_action_reference_unscoped_page_omitted_count"] == 0
    assert diagnostics["blocker_tags"]["unscoped_jue_wiki_action_reference"] == 1


def test_kis_manager_run_diagnostics_caps_unscoped_wiki_page_ids() -> None:
    actions = [
        {
            "symbol": f"10{index:04d}",
            "qty": 1,
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    f"fresh_jue_wiki_context kis.symbol.10{index:04d} checked"
                )
            },
        }
        for index in range(15)
    ]

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
        f"kis.symbol.10{index:04d}" for index in range(12)
    ]
    assert diagnostics["jue_wiki_action_reference_unscoped_page_omitted_count"] == 3


def test_kis_manager_run_diagnostics_rejects_generic_wiki_action_reference_without_page_id() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "selection_run_id": "wiki-selection-kis-generic-reference",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "wiki checked for 005930 against live quote"
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
        "kis.symbol.",
        "kis.ops.",
        "jue_wiki_action_reference_gap.",
    ]
    assert diagnostics["jue_wiki_action_reference_allowed_page_ids"] == [
        "kis.symbol.005930"
    ]
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_rejects_wiki_action_reference_when_block_symbol_unresolved() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "kis-block-missing-symbol",
                    "reason": "위키 근거 청산",
                    "metadata": {
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.005930 checked "
                            "against live quote"
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
            "block_id": "kis-block-missing-symbol",
            "reason": "위키 근거 청산",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_rejects_gap_marker_as_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.ops.manager_runs",
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.ops.manager_runs"],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "jue_wiki_action_reference_gap.kis.missing "
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


def test_kis_manager_run_diagnostics_rejects_unselected_wiki_action_reference_page_id() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.000660"],
            "selection_run_id": "wiki-selection-kis-unselected-reference",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.005930 checked "
                            "against live quote"
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
        "kis.symbol.000660"
    ]
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_rejects_page_present_but_not_selected() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.000660"],
            "selection_run_id": "wiki-selection-kis-present-but-unselected",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.005930 checked "
                            "against live quote"
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
        "kis.symbol.000660"
    ]
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_requires_selected_symbol_page_when_available() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.ops.risk_gate"],
            "selection_run_id": "wiki-selection-kis-symbol-page-required",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.ops.risk_gate checked "
                            "against live quote and risk gate"
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
            "symbol": "005930",
            "qty": 1,
            "horizon": "mid",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_requires_block_symbol_page_for_block_id_action() -> None:
    prompt = {
        "blocks": [
            {
                "block_id": "kis-block-005930-1",
                "symbol": "005930",
                "status": "open",
            }
        ],
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.ops.risk_gate"],
            "selection_run_id": "wiki-selection-kis-block-id-symbol-page-required",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "kis-block-005930-1",
                    "reason": "risk check",
                    "metadata": {
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.ops.risk_gate checked "
                            "against live quote and risk gate"
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
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {
            "section": "close_blocks",
            "block_id": "kis-block-005930-1",
            "symbol": "005930",
            "reason": "risk check",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_rejects_ops_only_reference_for_symbol_action() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.ops.risk_gate"],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.ops.risk_gate checked "
                            "against live quote and risk gate"
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
            "symbol": "005930",
            "qty": 1,
            "horizon": "mid",
        }
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_rejects_ops_only_usage_contract_for_symbol_action() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.ops.risk_gate"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "kis.ops.risk_gate 위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate 교차확인 후 실행"
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


def test_kis_manager_run_diagnostics_tracks_wiki_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "selection_run_id": "wiki-selection-kis-usage-1",
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                        "current_price_structure",
                    ],
                }
            },
        },
    }
    missing = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate/"
                            "current_price_structure 교차확인 후 축소 실행"
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
        "live_quote",
        "account_state",
        "risk_gate",
        "current_price_structure",
    ]
    assert missing["jue_wiki_usage_contract_resolution_count"] == 0
    assert missing["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert missing["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1
    assert resolved["jue_wiki_usage_contract_status"] == "resolved"
    assert resolved["jue_wiki_usage_contract_resolution_count"] == 1
    assert resolved["jue_wiki_usage_contract_resolution_ratio"] == 1.0
    assert "missing_jue_wiki_usage_contract_resolution" not in resolved["blocker_tags"]


def test_kis_manager_run_diagnostics_rejects_usage_contract_resolution_for_wrong_action_symbol() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "kis.symbol.005930 위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate 교차확인 후 실행"
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


def test_kis_manager_run_diagnostics_rejects_mixed_wrong_symbol_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "kis.symbol.000660 및 kis.symbol.005930 위키는 "
                            "단독 매매권한이 아니며 live_quote/account_state/"
                            "risk_gate 교차확인 후 실행"
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


def test_kis_manager_run_diagnostics_rejects_generic_usage_contract_resolution_in_multi_symbol_context() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate 교차확인 후 실행"
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


def test_kis_manager_run_diagnostics_rejects_generic_usage_contract_resolution_for_unselected_action_symbol() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate 교차확인 후 실행"
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


def test_kis_manager_run_diagnostics_rejects_usage_contract_resolution_when_block_symbol_unresolved() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "kis-block-missing-symbol",
                    "reason": "위키 근거 청산",
                    "metadata": {
                        "jue_wiki_usage_contract_resolution": (
                            "kis.symbol.005930 위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate 교차확인 후 청산"
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


def test_kis_manager_run_diagnostics_rejects_generic_usage_contract_resolution_for_block_id_action() -> None:
    prompt = {
        "blocks": [
            {
                "block_id": "kis-block-005930",
                "symbol": "005930",
                "qty_open": 1,
            }
        ],
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [
                {
                    "block_id": "kis-block-005930",
                    "reason": "위키 근거 청산",
                    "metadata": {
                        "jue_wiki_usage_contract_resolution": (
                            "위키는 단독 매매권한이 아니며 "
                            "live_quote/account_state/risk_gate 교차확인 후 청산"
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


def test_kis_manager_run_diagnostics_rejects_negative_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "selection_run_id": "wiki-selection-kis-usage-negative",
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                        "current_price_structure",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "usage contract resolution missing; "
                            "no live_quote/account_state/risk_gate cross checks"
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
        "live_quote",
        "account_state",
        "risk_gate",
        "current_price_structure",
    ]
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_kis_manager_run_diagnostics_rejects_generic_usage_contract_resolution_without_required_cross_checks() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "selection_run_id": "wiki-selection-kis-usage-generic",
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                        "current_price_structure",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
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


def test_kis_manager_run_diagnostics_tracks_usage_contract_memory_resolution() -> None:
    prompt = {
        "investment_memory": {
            "status": "ok",
            "jue_wiki_usage_contract_memory": {
                "status": "available",
                "items": [
                    {
                        "page_id": "kis.symbol.005930",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                        },
                        "summary_md": (
                            "최근 블록이 위키 메모리를 썼지만 "
                            "실시간 가격/계좌/리스크 교차검증을 남기지 않았다."
                        ),
                    }
                ],
            },
        }
    }

    missing = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "위키 메모리는 선행가설이며 live_quote/account_state/"
                            "risk_gate/current_price_structure 확인 후만 실행"
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


def test_kis_manager_run_diagnostics_rejects_generic_usage_contract_memory_resolution_without_required_cross_checks() -> None:
    prompt = {
        "investment_memory": {
            "status": "ok",
            "jue_wiki_usage_contract_memory": {
                "status": "available",
                "items": [
                    {
                        "page_id": "kis.symbol.005930",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                            "required_cross_checks": [
                                "live_quote",
                                "account_state",
                                "risk_gate",
                            ],
                        },
                    }
                ],
            },
        }
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
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
        "live_quote",
        "account_state",
        "risk_gate",
    ]
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_kis_manager_run_diagnostics_tracks_workflow_usage_contract_resolution() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
        },
        "jue_workflow": {
            "workflow_id": "kis_cycle",
            "contracts": [
                {
                    "contract_id": "jue_wiki_usage_contract_resolution",
                    "required_metadata": "jue_wiki_usage_contract_resolution",
                }
            ],
        },
    }

    missing = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={},
    )
    resolved = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_usage_contract_resolution": (
                            "workflow contract 기준으로 위키는 선행가설이며 "
                            "live_quote/account_state/risk_gate 확인 후 실행"
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


def test_kis_manager_run_diagnostics_requires_usage_contract_resolution_for_hold_decision() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    missing = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
    resolved = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
                    "관망 판단에도 위키는 선행가설이며 live_quote/"
                    "account_state/risk_gate 교차확인 후 신규 블록 보류"
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


def test_kis_manager_run_diagnostics_partially_covers_multi_symbol_hold_usage_contract() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
                    "kis.symbol.005930 위키는 선행가설로만 사용했고 "
                    "live_quote/account_state/risk_gate 교차확인 후 관망"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "partial"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 1
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.5
    assert diagnostics["blocker_tags"]["partial_jue_wiki_usage_contract_resolution"] == 1


def test_kis_manager_run_diagnostics_rejects_unselected_page_in_hold_usage_contract() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.035420",
                    "symbols": ["035420"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "trust_profile": {
                "usage_contract": {
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                }
            },
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
                    "kis.symbol.005930/kis.symbol.000660/kis.symbol.035420 "
                    "위키는 선행가설로만 사용했고 live_quote/"
                    "account_state/risk_gate 교차확인 후 관망"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_kis_manager_run_diagnostics_requires_wiki_action_reference_for_hold_decision() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.ops.risk_gate",
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.ops.risk_gate"],
        },
    }

    missing = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
                    "fresh_jue_wiki_context kis.ops.risk_gate checked "
                    "against live quote"
                )
            },
        },
    )
    referenced = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
                    "fresh_jue_wiki_context kis.symbol.005930 checked "
                    "against live quote and account state"
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


def test_kis_manager_run_diagnostics_partially_covers_multi_symbol_hold_decision() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
                    "fresh_jue_wiki_context kis.symbol.005930 checked "
                    "against live quote"
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
            "page_id": "kis.symbol.000660",
            "symbol": "000660",
        }
    ]
    assert diagnostics["blocker_tags"]["partial_jue_wiki_action_reference"] == 1


def test_kis_manager_run_diagnostics_counts_symbolic_hold_reference_against_target_symbol() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
            "watch_symbols": ["005930"],
            "data_gaps": ["진입 가격 구조 부족"],
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context kis.symbol.005930 checked "
                    "against live quote"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "referenced"
    assert diagnostics["jue_wiki_action_reference_count"] == 1
    assert diagnostics["jue_wiki_action_reference_ratio"] == 1.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == []
    assert "partial_jue_wiki_action_reference" not in diagnostics["blocker_tags"]


def test_kis_manager_run_diagnostics_rejects_wrong_symbol_page_in_symbolic_hold_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
            "watch_symbols": ["005930"],
            "data_gaps": ["진입 가격 구조 부족"],
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context kis.symbol.000660 checked "
                    "against live quote"
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


def test_kis_manager_run_diagnostics_rejects_uncovered_target_symbol_hold_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
            "watch_symbols": ["035420"],
            "data_gaps": ["target symbol wiki not selected"],
            "metadata": {
                "jue_wiki_freshness_cross_check": (
                    "fresh_jue_wiki_context kis.symbol.005930 checked "
                    "against live quote"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {"section": "hold_decision", "symbol": "035420"}
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_rejects_unselected_page_in_hold_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.035420",
                    "symbols": ["035420"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "selection_run_id": "wiki-selection-kis-hold-unselected-page",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
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
                    "fresh_jue_wiki_context kis.symbol.005930, "
                    "kis.symbol.000660, and kis.symbol.035420 checked "
                    "against live quote"
                )
            },
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_blocks_partial_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "selection_run_id": "wiki-selection-kis-partial",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.005930 checked "
                            "against live quote and valuation"
                        ),
                    },
                },
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
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
            "symbol": "000660",
            "qty": 1,
            "horizon": "mid",
        }
    ]
    assert "missing_jue_wiki_action_reference" not in diagnostics["blocker_tags"]


def test_kis_manager_run_diagnostics_rejects_wrong_symbol_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "selection_run_id": "wiki-selection-kis-wrong-symbol",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.005930 checked "
                            "against live quote and valuation"
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


def test_kis_manager_run_diagnostics_rejects_mixed_wrong_symbol_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                },
                {
                    "page_id": "kis.symbol.000660",
                    "symbols": ["000660"],
                    "quality_status": "active",
                },
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            "selection_run_id": "wiki-selection-kis-mixed-wrong-symbol",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_freshness_cross_check": (
                            "fresh_jue_wiki_context kis.symbol.000660 and "
                            "kis.symbol.005930 checked against live quote"
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


def test_kis_manager_run_diagnostics_rejects_negative_wiki_action_reference() -> None:
    prompt = {
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbols": ["005930"],
                    "quality_status": "active",
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "selection_run_id": "wiki-selection-kis-negative-reference",
        },
    }

    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "horizon": "mid",
                        "jue_wiki_reference_basis": (
                            "wiki missing for 005930; no fresh context available"
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
            "symbol": "005930",
            "qty": 1,
            "horizon": "mid",
        }
    ]


def test_kis_manager_run_diagnostics_exposes_wiki_action_reference_recovery() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
                    "open_gap_count": 2,
                    "resolved_count": 3,
                    "total_count": 5,
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
    assert diagnostics["jue_wiki_action_reference_recovery_memory_scope"] == "kis"
    assert diagnostics["jue_wiki_action_reference_recovery_open_gap_count"] == 2
    assert diagnostics["jue_wiki_action_reference_recovery_resolved_count"] == 3
    assert diagnostics["jue_wiki_action_reference_recovery_total_count"] == 5
    assert diagnostics["jue_wiki_action_reference_recovery_ratio"] == 0.6
    assert (
        diagnostics["jue_wiki_action_reference_recovery_latest_resolution_status"]
        == "unresolved"
    )
    assert diagnostics["jue_wiki_action_reference_recovery_latest_status"] == "missing"
    assert (
        diagnostics["blocker_tags"]["unresolved_jue_wiki_action_reference_recovery"]
        == 2
    )
    assert diagnostics["top_blockers"][0]["tag"] == (
        "unresolved_jue_wiki_action_reference_recovery"
    )


def test_kis_manager_run_diagnostics_blocks_compacted_unresolved_wiki_recovery() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "unresolved",
                    "memory_scope": "kis",
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


def test_kis_manager_run_diagnostics_ignores_binance_wiki_recovery_scope() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "binance",
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


def test_kis_manager_run_diagnostics_ignores_binance_wiki_recovery_target_scope() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "target_scope": "binance",
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


def test_kis_manager_run_diagnostics_blocks_item_only_unresolved_wiki_recovery() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
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

    assert diagnostics["jue_wiki_action_reference_recovery_status"] == "unresolved"
    assert diagnostics["jue_wiki_action_reference_recovery_memory_scope"] == "kis"
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


def test_kis_manager_run_diagnostics_ignores_binance_item_only_wiki_recovery_scope() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
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

    assert "jue_wiki_action_reference_recovery_status" not in diagnostics
    assert (
        "unresolved_jue_wiki_action_reference_recovery"
        not in diagnostics["blocker_tags"]
    )


def test_kis_manager_run_diagnostics_ignores_binance_action_reference_memory_items() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
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


def test_kis_manager_run_diagnostics_ignores_binance_action_reference_memory_container_scope() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "binance",
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


def test_kis_manager_run_diagnostics_accepts_translated_binance_action_reference_container_scope() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.binance.translated",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate Binance waiting-entry lesson into a "
                                    "Korean equity pullback block check"
                                ),
                                "required_evidence": ["translated_kr_equity_mapping"],
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


def test_kis_manager_run_diagnostics_does_not_resolve_translated_binance_memory_without_translation_evidence() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.binance.translated",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate Binance waiting-entry lesson into a "
                                    "Korean equity pullback block check"
                                ),
                                "required_evidence": ["translated_kr_equity_mapping"],
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
                    "symbol": "005930",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap.binance.translated reused "
                            "from Binance waiting-entry memory after valuation and "
                            "quote check"
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


def test_kis_manager_run_diagnostics_resolves_translated_binance_memory_with_translation_evidence() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "binance",
                    "transferability": "translated",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.binance.translated",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "translate Binance waiting-entry lesson into a "
                                    "Korean equity pullback block check"
                                ),
                                "required_evidence": ["translated_kr_equity_mapping"],
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
                    "symbol": "005930",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "translated_kr_equity_mapping: Binance waiting-entry "
                            "lesson mapped to Samsung Electronics valuation, quote, "
                            "and research_spine evidence before a pullback block"
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


def test_kis_manager_run_diagnostics_accepts_kr_equity_action_reference_container_scope() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "target_scope": "kr_equity",
                    "items": [
                        {
                            "policy_id": "jue_wiki_action_reference_gap.missing",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference Korean equity wiki evidence before "
                                    "creating a block"
                                ),
                                "required_evidence": ["kr_equity_wiki_page_id"],
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


def test_kis_manager_run_diagnostics_accepts_kr_equity_action_reference_item_scope() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_memory": {
                    "status": "available",
                    "items": [
                        {
                            "scope": "kr_equity_pattern_lab",
                            "policy_id": "jue_wiki_action_reference_gap.missing",
                            "latest_status": "missing_action_reference",
                            "application_guidance": {
                                "manager_instruction": (
                                    "reference Korean equity pattern lab evidence "
                                    "before creating a block"
                                ),
                                "required_evidence": ["kr_equity_pattern_page_id"],
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


def test_kis_manager_run_diagnostics_does_not_resolve_kis_memory_with_binance_item_reference() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
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
                                    "reference KIS wiki evidence before creating "
                                    "a Korean equity block"
                                ),
                                "required_evidence": ["kr_equity_wiki_page_id"],
                            },
                        },
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
                    ],
                }
            }
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap."
                            "binance.action_reference_required "
                            "crypto_wiki_page_id checked"
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


def test_kis_manager_run_diagnostics_resolves_recovery_guidance_with_recovery_metadata() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
            "create_blocks": [
                {
                    "symbol": "005930",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                            "recovered with fresh wiki, live quote, and basis check"
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


def test_kis_manager_run_diagnostics_rejects_unselected_page_in_recovery_metadata() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.symbol.005930"],
            },
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
                },
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "005930",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                            "recovered with fresh wiki kis.symbol.005930 and "
                            "kis.symbol.035420, live quote, and basis check"
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


def test_kis_manager_run_diagnostics_rejects_wrong_selected_page_in_recovery_metadata() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            },
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
                },
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                            "recovered with fresh wiki kis.symbol.005930, "
                            "live quote, and basis check"
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


def test_kis_manager_run_diagnostics_rejects_page_idless_recovery_in_multi_symbol_context() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            },
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
                },
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "000660",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                            "recovered with fresh wiki, live quote, and basis check"
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


def test_kis_manager_run_diagnostics_rejects_unresolved_block_recovery_metadata() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.symbol.005930"],
            },
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
                },
            },
        },
        response={},
        actions={
            "create_blocks": [],
            "update_blocks": [
                {
                    "block_id": "unknown-kis-block",
                    "metadata": {
                        "jue_wiki_action_reference_recovery": (
                            "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                            "recovered with fresh wiki kis.symbol.005930, "
                            "live quote, and basis check"
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


def test_kis_manager_run_diagnostics_keeps_recovery_gap_for_generic_wiki_metadata() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
                                "kis.unresolved_memory"
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
                    "symbol": "005930",
                    "metadata": {
                        "jue_wiki_reference_basis": (
                            "jue_wiki_action_reference_gap.kis.unresolved_memory "
                            "fresh wiki basis checked"
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


def test_kis_manager_run_diagnostics_resolves_recovery_guidance_with_hold_metadata() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
        hold_decision={
            "summary": "신규 블록 없이 회복 근거만 점검",
            "metadata": {
                "jue_wiki_action_reference_recovery": (
                    "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                    "recovered with fresh wiki, live quote, and basis check"
                )
            },
            "reasons": [
                "위키 회복 근거와 현재가 교차검증은 완료했지만 진입 가격 구조는 아직 부족하다"
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


def test_kis_manager_run_diagnostics_rejects_page_idless_hold_recovery_with_selected_pages() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            },
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
                    "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                    "recovered with fresh wiki, live quote, and basis check"
                )
            },
            "reasons": [
                "위키 회복 근거와 현재가 교차검증은 완료했지만 진입 가격 구조는 아직 부족하다"
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


def test_kis_manager_run_diagnostics_rejects_wrong_selected_page_in_symbolic_hold_recovery() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.symbol.005930", "kis.symbol.000660"],
            },
            "investment_memory": {
                "jue_wiki_action_reference_recovery": {
                    "status": "open_gaps",
                    "memory_scope": "kis",
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
            "summary": "005930 회복 근거만 점검",
            "watch_symbols": ["005930"],
            "data_gaps": ["진입 가격 구조 부족"],
            "metadata": {
                "jue_wiki_action_reference_recovery": (
                    "jue_wiki_action_reference_gap.kis.unresolved_recovery "
                    "kis.symbol.000660 recovered with fresh wiki and live quote"
                )
            },
            "reasons": [
                "005930은 관망하지만 000660 위키 근거로 회복했다고 기록했다"
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


def test_kis_manager_run_diagnostics_accepts_hold_ops_wiki_evidence_refs() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.ops.regime.pullback"],
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
            "summary": "눌림목 원칙 위키에 따라 신규 블록 보류",
            "evidence_refs": ["kis.ops.regime.pullback"],
            "reasons": ["kis.ops.regime.pullback 기준상 추격 진입보다 대기"],
        },
    )

    assert diagnostics["jue_wiki_action_reference_count"] == 1
    assert diagnostics["jue_wiki_action_reference_ratio"] == 1.0
    assert "missing_jue_wiki_action_reference" not in diagnostics["blocker_tags"]


def test_kis_manager_run_diagnostics_rejects_ops_only_reference_for_symbol_hold() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.ops.regime.pullback"],
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
            "watch_symbols": ["005930"],
            "data_gaps": ["target symbol wiki not selected"],
            "evidence_refs": ["kis.ops.regime.pullback"],
            "reasons": ["005930은 kis.ops.regime.pullback 기준상 추격보다 대기"],
        },
    )

    assert diagnostics["jue_wiki_action_reference_status"] == "missing"
    assert diagnostics["jue_wiki_action_reference_count"] == 0
    assert diagnostics["jue_wiki_action_reference_ratio"] == 0.0
    assert diagnostics["jue_wiki_action_reference_missing_actions"] == [
        {"section": "hold_decision", "symbol": "005930"}
    ]
    assert diagnostics["blocker_tags"]["missing_jue_wiki_action_reference"] >= 1


def test_kis_manager_run_diagnostics_rejects_ops_only_usage_contract_for_symbol_hold() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki_application": {
                "status": "ok",
                "selected_page_ids": ["kis.ops.regime.pullback"],
                "trust_profile": {
                    "usage_contract": {
                        "standalone_trade_authority": False,
                        "required_cross_checks": [
                            "live_quote",
                            "account_state",
                            "risk_gate",
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
            "watch_symbols": ["005930"],
            "data_gaps": ["target symbol wiki not selected"],
            "metadata": {
                "jue_wiki_usage_contract_resolution": (
                    "kis.ops.regime.pullback 위키는 단독 매매권한이 아니며 "
                    "live_quote/account_state/risk_gate 교차확인 후 관망"
                ),
            },
            "reasons": ["005930 관망"],
        },
    )

    assert diagnostics["jue_wiki_usage_contract_status"] == "missing"
    assert diagnostics["jue_wiki_usage_contract_resolution_count"] == 0
    assert diagnostics["jue_wiki_usage_contract_resolution_ratio"] == 0.0
    assert diagnostics["blocker_tags"]["missing_jue_wiki_usage_contract_resolution"] >= 1


def test_kis_manager_run_diagnostics_keeps_degraded_wiki_action_response_only_unresolved() -> None:
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt={
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "symbols": ["005930"],
                        "effectiveness": {
                            "status": "degraded",
                            "reasons": ["application_repair_queue_pressure"],
                        },
                    }
                ]
            },
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
                        "resolution": "small_waiting_block",
                        "next_trigger": (
                            "kis.symbol.005930 degraded wiki 교차확인 후 대기"
                        ),
                    }
                ]
            }
        },
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {"horizon": "mid"},
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


def test_compact_manager_prompt_context_includes_kis_diagnostics_and_core_wiki_sections() -> None:
    marker = "KIS_MANAGER_CONTEXT_MODULE_BLOAT"
    context = compact_manager_prompt_context(
        {
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
                "raw_notes": marker * 20,
            },
            "jue_wiki_repair_contract": {
                "attention_plan_response_contract": {
                    "status": "active",
                    "must_address": ["repair_now"],
                },
                "action_batches": [
                    {
                        "scope": "kis",
                        "action_type": "refresh_symbol_financials",
                        "count": 3,
                        "symbols": ["005930"],
                        "raw_notes": marker * 20,
                    }
                ],
                "repair_priority_count": 8,
                "top_priorities": [
                    {
                        "source_id": "repair:financials:kis:005930",
                        "symbols": ["005930"],
                        "raw_notes": marker * 20,
                    }
                ],
            },
            "jue_wiki_selection_observation": {
                "selection_run_id": "selection:kis-context",
                "repair_action_batches": [
                    {
                        "scope": "kis",
                        "action_type": "refresh_symbol_financials",
                        "count": 3,
                        "symbols": ["005930"],
                    }
                ],
                "evidence_quality": {
                    "summary_line": "evidence_quality sources=2 weak=1",
                    "status_counts": {"weak": 1, "strong": 2},
                    "raw_notes": marker * 20,
                },
            },
            "research_spine": {"summary": marker * 20},
            "market_pulse": {"summary": marker * 20},
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    encoded = json.dumps(context, ensure_ascii=False)
    assert context["diagnostics"]["blocker_tags"][
        "unresolved_jue_wiki_requested_symbol_coverage"
    ] >= 2
    assert context["diagnostics"]["blocker_tags"][
        "unresolved_jue_wiki_attention_plan"
    ] >= 3
    assert context["jue_wiki_requested_symbol_coverage"]["missing_summary_symbols"] == [
        "000660"
    ]
    assert "jue_wiki_repair_contract" in context
    assert context["jue_wiki_repair_contract"]["action_batches"][0][
        "action_type"
    ] == "refresh_symbol_financials"
    assert context["jue_wiki_selection_observation"]["evidence_quality"][
        "summary_line"
    ] == "evidence_quality sources=2 weak=1"
    assert marker not in encoded


def test_kis_manager_response_contract_rejects_unresolved_requested_symbol_coverage() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
                "prompt_omitted_symbols": ["277810"],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "validation_repair_resolution_missing_from_model"

    resolved_error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "coverage follow-up staged",
            "watch_symbols": ["000660"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "000660",
                    "condition": "위키 요약 수집 또는 장중 실시간 교차확인 후 재판단",
                }
            ],
        },
    )

    assert resolved_error == ""


def test_kis_manager_response_contract_rejects_requested_symbol_coverage_trigger_for_other_symbol() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "coverage follow-up staged",
            "watch_symbols": ["005930"],
            "data_gaps": ["requested_symbol_summary_missing"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": "위키 요약 수집 또는 장중 실시간 교차확인 후 재판단",
                }
            ],
        },
    )

    assert error == "validation_repair_resolution_missing_from_model"


def test_kis_manager_response_contract_rejects_action_with_unresolved_requested_symbol_coverage() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "effectiveness": {"status": "degraded"},
                    }
                ]
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_repair_pressure": (
                            "kis.symbol.005930 degraded wiki pressure handled"
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


def test_kis_manager_response_contract_rejects_requested_symbol_coverage_resolution_on_wrong_action_symbol() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "missing_summary_symbols": ["000660"],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 1,
                    "metadata": {
                        "jue_wiki_requested_symbol_coverage_resolution": (
                            "000660 requested_symbol_summary gap handled by "
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


def test_kis_manager_ignores_binance_requested_symbol_coverage() -> None:
    prompt = {
        "jue_wiki_requested_symbol_coverage": {
            "target_scope": "binance",
            "status": "partial",
            "missing_summary_symbols": ["ETHUSDT"],
            "prompt_omitted_symbols": ["SOLUSDT"],
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "KIS judgment proceeds"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_requested_symbol_coverage_status"] is None
    assert diagnostics["jue_wiki_missing_summary_symbols"] == []
    assert "unresolved_jue_wiki_requested_symbol_coverage" not in diagnostics[
        "blocker_tags"
    ]


def test_kis_manager_response_contract_rejects_unresolved_memory_card_quality() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930"],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    unresolved_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert unresolved_error == "validation_repair_resolution_missing_from_model"

    resolved_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "005930 위키 기억은 얇아서 실시간 리서치 교차확인 후 재판단",
            "watch_symbols": ["005930"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": "네이버/리포트/현재 수급 교차확인 후 확신도 재평가",
                }
            ],
        },
    )

    assert resolved_error == ""


def test_kis_manager_response_contract_ignores_binance_memory_card_quality() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "target_scope": "binance",
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["NEARUSDT"],
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_crypto_research_before_high_confidence",
                "symbols": ["NEARUSDT"],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "KIS judgment proceeds"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_memory_card_quality_status"] == "inactive"
    assert "unresolved_jue_wiki_memory_card_quality" not in diagnostics["blocker_tags"]


def test_kis_memory_card_quality_requires_specific_field_or_check_resolution() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
                "required_checks": [
                    "refresh_durable_facts_from_reports_fundamentals_and_market_context"
                ],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    generic_hold_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "005930 위키 기억이 얇어서 관찰",
            "watch_symbols": ["005930"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": "리서치 교차확인 후 재평가",
                }
            ],
        },
    )

    assert generic_hold_error == "validation_repair_resolution_missing_from_model"

    specific_hold_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={
            "summary": "005930 durable_facts 결손을 현재 리포트/펀더멘털로 보강 대기",
            "watch_symbols": ["005930"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": (
                        "refresh_durable_facts_from_reports_fundamentals_"
                        "and_market_context 완료 후 재평가"
                    ),
                }
            ],
        },
    )

    assert specific_hold_error == ""


def test_kis_memory_card_quality_rejects_action_resolution_on_wrong_symbol() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "metadata": {
                        "jue_wiki_memory_card_quality": {
                            "resolution": "005930 durable_facts gap cross_checked",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "action only"},
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [
                {
                    "symbol": "000660",
                    "metadata": {
                        "jue_wiki_memory_card_quality": {
                            "resolution": "005930 durable_facts gap cross_checked",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "action only"},
    )

    assert error == "validation_repair_resolution_missing_from_model"
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_kis_memory_card_quality_rejects_hold_resolution_on_wrong_symbol() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    hold_decision = {
        "summary": "005930 durable_facts gap은 언급했지만 실제 대기 트리거는 000660",
        "watch_symbols": ["000660"],
        "data_gaps": ["durable_facts"],
        "next_triggers": [
            {
                "symbol": "000660",
                "condition": "005930 durable_facts 재확인 문구가 섞인 하이닉스 트리거",
            }
        ],
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision=hold_decision,
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision=hold_decision,
    )

    assert error == "validation_repair_resolution_missing_from_model"
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_kis_memory_card_quality_rejects_response_resolution_on_wrong_symbol() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "000660",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "005930 durable_facts gap cross_checked elsewhere",
                }
            ]
        }
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response only"},
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response only"},
    )

    assert error == "validation_repair_resolution_missing_from_model"
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_kis_memory_card_quality_accepts_response_resolution_on_target_symbol() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "005930",
                    "resolution": "candidate_rejected",
                    "evidence_gap": "005930 durable_facts gap requires refresh",
                }
            ]
        }
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response only"},
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response only"},
    )

    assert error == ""
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "response_resolution"
    )


def test_kis_memory_card_quality_rejects_negative_response_resolution() -> None:
    prompt = {
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
                "missing_field_counts": {"durable_facts": 1},
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts"],
                    }
                ],
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "005930",
                    "resolution": "candidate_rejected",
                    "evidence_gap": (
                        "005930 durable_facts not refreshed; "
                        "no fresh memory card quality evidence available"
                    ),
                }
            ]
        }
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response=response,
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response only"},
    )
    diagnostics = kis_manager_prompt_module.kis_manager_run_diagnostics(
        prompt=prompt,
        response=response,
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "response only"},
    )

    assert error == "validation_repair_resolution_missing_from_model"
    assert diagnostics["jue_wiki_memory_card_quality_resolution_status"] == (
        "unresolved"
    )


def test_kis_repair_contract_memory_card_gap_requires_specific_resolution() -> None:
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
                                "refresh_durable_facts_from_reports_"
                                "fundamentals_and_market_context"
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
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    generic_hold_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 위키 수리 필요, 관망",
            "watch_symbols": ["005930"],
            "data_gaps": ["thin_memory_card_requires_live_cross_check"],
            "next_triggers": [
                {"symbol": "005930", "condition": "리서치 교차확인 후 재평가"}
            ],
        },
    )
    specific_hold_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 durable_facts 반복 결손을 먼저 보강",
            "watch_symbols": ["005930"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": (
                        "refresh_durable_facts_from_reports_fundamentals_"
                        "and_market_context 완료 후 재평가"
                    ),
                }
            ],
        },
    )

    assert generic_hold_error == "validation_repair_resolution_missing_from_model"
    assert specific_hold_error == ""


def test_kis_memory_card_gap_rejects_generic_validation_repair_resolution() -> None:
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
                                "refresh_durable_facts_from_reports_"
                                "fundamentals_and_market_context"
                            ),
                            "sample_count": 3,
                        }
                    ],
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    generic_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "시장 컨텍스트 추가 확인 필요",
                    }
                ]
            }
        },
        actions=actions,
        hold_decision={"summary": "관망"},
    )
    specific_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "005930",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "durable_facts와 "
                            "refresh_durable_facts_from_reports_fundamentals_"
                            "and_market_context 보강 전까지 보류"
                        ),
                    }
                ]
            }
        },
        actions=actions,
        hold_decision={"summary": "관망"},
    )

    assert generic_error == "validation_repair_resolution_missing_from_model"
    assert specific_error == ""


def test_kis_memory_card_gap_prioritizes_missed_fields_over_sampled_fields() -> None:
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
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    sampled_only_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 risk_notes 항목만 재점검",
            "watch_symbols": ["005930"],
            "data_gaps": ["risk_notes"],
            "next_triggers": [
                {"symbol": "005930", "condition": "risk_notes 확인 후 재평가"}
            ],
        },
    )
    missed_field_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 durable_facts 반복 누락을 우선 보강",
            "watch_symbols": ["005930"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "005930", "condition": "durable_facts 확인 후 재평가"}
            ],
        },
    )

    assert sampled_only_error == "validation_repair_resolution_missing_from_model"
    assert missed_field_error == ""


def test_kis_memory_card_gap_prioritizes_explicit_priority_terms() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "priority_missing_fields": ["durable_facts"],
                    "priority_required_checks": ["refresh_durable_facts"],
                    "missing_field_missed_counts": {
                        "durable_facts": 2,
                        "risk_notes": 2,
                    },
                    "required_check_missed_counts": {
                        "refresh_durable_facts": 2,
                        "inspect_risk_notes": 2,
                    },
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    non_priority_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 risk_notes와 inspect_risk_notes만 재점검",
            "watch_symbols": ["005930"],
            "data_gaps": ["risk_notes"],
            "next_triggers": [
                {"symbol": "005930", "condition": "inspect_risk_notes 이후 재평가"}
            ],
        },
    )
    priority_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 durable_facts와 refresh_durable_facts 우선 보강",
            "watch_symbols": ["005930"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "005930", "condition": "refresh_durable_facts 이후 재평가"}
            ],
        },
    )

    assert non_priority_error == "validation_repair_resolution_missing_from_model"
    assert priority_error == ""


def test_kis_memory_card_gap_requires_top_priority_term_first() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "priority_missing_fields": ["durable_facts", "lessons"],
                    "priority_required_checks": [
                        "refresh_durable_facts",
                        "inspect_block_lessons",
                    ],
                },
            },
        },
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    second_priority_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 lessons와 inspect_block_lessons만 보강",
            "watch_symbols": ["005930"],
            "data_gaps": ["lessons"],
            "next_triggers": [
                {"symbol": "005930", "condition": "inspect_block_lessons 이후 재평가"}
            ],
        },
    )
    top_priority_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 durable_facts와 refresh_durable_facts 우선 보강",
            "watch_symbols": ["005930"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "005930", "condition": "refresh_durable_facts 이후 재평가"}
            ],
        },
    )

    assert second_priority_error == "validation_repair_resolution_missing_from_model"
    assert top_priority_error == ""


def test_kis_memory_card_gap_rejects_stale_focus_when_priority_lists_exist() -> None:
    prompt = {
        "jue_wiki_repair_contract": {
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
                "memory_card_quality_gap_summary": {
                    "status": "repair_required",
                    "priority_missing_fields": ["durable_facts"],
                    "priority_required_checks": ["refresh_durable_facts"],
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
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    stale_focus_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 risk_notes와 inspect_risk_notes를 점검",
            "watch_symbols": ["005930"],
            "data_gaps": ["risk_notes"],
            "next_triggers": [
                {"symbol": "005930", "condition": "inspect_risk_notes 이후 재평가"}
            ],
        },
    )
    explicit_priority_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 durable_facts와 refresh_durable_facts 우선 보강",
            "watch_symbols": ["005930"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "005930", "condition": "refresh_durable_facts 이후 재평가"}
            ],
        },
    )

    assert stale_focus_error == "validation_repair_resolution_missing_from_model"
    assert explicit_priority_error == ""


def test_kis_memory_card_gap_uses_priority_focus_when_lists_are_absent() -> None:
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
                        "required_check": "refresh_durable_facts",
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
            "kill_switch": {"enabled": False},
            "new_entry_allowed_by_session": True,
        },
    }
    actions = {
        "adopt_existing_blocks": [],
        "create_blocks": [],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    generic_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 위키 점검 후 재평가",
            "watch_symbols": ["005930"],
            "data_gaps": ["memory_card_quality"],
            "next_triggers": [
                {"symbol": "005930", "condition": "위키 점검 이후 재평가"}
            ],
        },
    )
    focus_error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions=actions,
        hold_decision={
            "summary": "005930 durable_facts와 refresh_durable_facts 우선 보강",
            "watch_symbols": ["005930"],
            "data_gaps": ["durable_facts"],
            "next_triggers": [
                {"symbol": "005930", "condition": "refresh_durable_facts 이후 재평가"}
            ],
        },
    )

    assert generic_error == "validation_repair_resolution_missing_from_model"
    assert focus_error == ""


def test_kis_memory_card_gap_ignores_resolved_active_gap_summary() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "jue_wiki_repair_contract": {
                "status": "active",
                "repair_loop_effectiveness": {
                    "status": "active",
                    "memory_card_quality_gap_summary": {
                        "status": "active",
                        "missing_field_counts": {"durable_facts": 3},
                        "missing_field_missed_counts": {"durable_facts": 0},
                        "required_check_counts": {"refresh_durable_facts": 3},
                        "required_check_missed_counts": {
                            "refresh_durable_facts": 0
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
                                "check": "refresh_durable_facts",
                                "sample_count": 3,
                                "missed_count": 0,
                            }
                        ],
                    },
                },
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == ""


def test_kis_memory_card_gap_ignores_zero_count_priority_focus() -> None:
    error = kis_manager_response_contract_error(
        prompt={
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
                            "required_check": "refresh_durable_facts",
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
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == ""


def test_kis_manager_response_contract_allows_server_safety_gate_hold() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "validation_repair": {"repair_item_count": 1},
            "execution_gate": {
                "status": "blocked",
                "kill_switch": {"enabled": True},
                "new_entry_allowed_by_session": False,
            },
        },
        response={"hold_decision": {"summary": "kill switch"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "kill switch"},
    )

    assert error == ""


def test_kis_manager_response_contract_rejects_action_pressure_without_trigger() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "proactive_decision_pressure": {"status": "action_required"},
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "hold_decision_missing_concrete_trigger"


def test_kis_manager_ignores_binance_scoped_action_pressure() -> None:
    error = kis_manager_response_contract_error(
        prompt={
            "proactive_decision_pressure": {
                "target_scope": "binance",
                "status": "action_required",
            },
            "jue_wiki_action_pressure_contract": {
                "target_scope": "binance",
                "status": "active",
                "page_ids": ["binance.ops.action_pressure"],
            },
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "KIS judgment proceeds"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "KIS judgment proceeds"},
    )

    assert error == ""


def test_kis_jue_wiki_action_pressure_contract_attaches_and_requires_resolution() -> None:
    prompt: dict[str, object] = {"decision_inputs": []}

    kis_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:kis-action-pressure",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.ops.action_pressure",
                    "summary": "no_action=70/80 with 489 candidates",
                    "source_refs": [
                        {
                            "source_type": "action_pressure",
                            "source_id": "kis.manager_runs",
                        }
                    ],
                }
            ],
            "budget_report": {"char_count": 10},
        },
        max_chars=1000,
    )

    contract = prompt["jue_wiki_action_pressure_contract"]
    assert contract["status"] == "active"
    assert contract["page_ids"] == ["kis.ops.action_pressure"]
    assert "jue_wiki_action_pressure_contract" in prompt["decision_inputs"]

    error = kis_manager_response_contract_error(
        prompt={
            **prompt,
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "new_entry_allowed_by_session": True,
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "adopt_existing_blocks": [],
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "hold_decision_missing_concrete_trigger"


def test_kis_jue_wiki_decision_adjustments_attach_as_actionable_contract() -> None:
    prompt: dict[str, object] = {"decision_inputs": []}

    kis_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:kis-decision-adjustment",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "trust_profiles": [
                    {
                        "decision_scope": "kis",
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 12,
                        "confidence": 0.76,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "status": "degraded",
                                "avg_return_pct": -0.9,
                                "sample_count": 6,
                            },
                            {
                                "risk_posture": "knowledge_spine",
                                "status": "active",
                                "avg_return_pct": 1.4,
                                "sample_count": 8,
                            },
                        ],
                    }
                ]
            },
            "pages": [
                {
                    "page_id": "kis.playbook.value_cycle",
                    "summary": "Use durable wiki evidence for value-cycle design.",
                }
            ],
            "budget_report": {"char_count": 100},
        },
        max_chars=2000,
    )

    contract = prompt["jue_wiki_decision_adjustments"]
    assert contract["status"] == "active"
    assert contract["hard_filters"] is False
    assert contract["safety_gates_still_override"] is True
    assert contract["adjustments"][0]["action"] == "shift_to_preferred_risk_posture"
    assert contract["adjustments"][0]["target_risk_posture"] == "knowledge_spine"
    assert "jue_wiki_decision_adjustments" in prompt["decision_inputs"]


def test_prompt_section_size_rows_orders_by_json_size_and_ignores_budget() -> None:
    rows = prompt_section_size_rows(
        {
            "small": "x",
            "large": {"items": list(range(20))},
            "prompt_budget": {"total_chars": 999_999},
        }
    )

    assert [row["section"] for row in rows] == ["large", "small"]
    assert rows[0]["chars"] > rows[1]["chars"]


def test_prompt_budget_policy_constants_cover_large_optional_sections() -> None:
    ordered_sections = [row[0] for row in PROMPT_BUDGET_COMPACTION_ORDER]

    assert ordered_sections[:3] == [
        "blocks",
        "pre_adoption_symbol_analysis",
        "investment_memory",
    ]
    assert "research_spine" in ordered_sections
    assert "candidate_policy_impacts" in PROMPT_BUDGET_OMITTABLE_SECTIONS
    assert "live_authority" in ordered_sections
    assert "live_authority" not in PROMPT_BUDGET_OMITTABLE_SECTIONS


def test_finalize_prompt_budget_keeps_compact_live_authority_context() -> None:
    marker = "KIS_LIVE_AUTHORITY_BUDGET"
    prompt = {
        "task": "Manage KIS blocks",
        "clock": {"session": "regular"},
        "account": {"cash_krw": 1_000_000},
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": "005930",
                "status": "open",
                "thesis": marker * 30,
            }
            for idx in range(8)
        ],
        "candidate_policy_impacts": {
            f"{idx:06d}": [{"reason": marker * 60}]
            for idx in range(60)
        },
        "investment_memory": {"notes": [{"summary": marker * 90} for _ in range(24)]},
        "live_authority": {
            "status": "ok",
            "live_grade": "restricted",
            "validation_gate": {
                "status": "validation_probe",
                "readiness": "probe",
                "reason": marker * 200,
            },
            "lane_authority": {
                "version": "lane_authority_v1",
                "weak_lanes": ["mid", "long"],
                "lane_actions": {
                    "mid": {
                        "action": "observe_or_waiting_entry",
                        "reason": marker * 200,
                    }
                },
            },
        },
        "strategy": {"items": [{"summary": marker * 50} for _ in range(12)]},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=28_000,
        warn_chars=32_000,
        max_chars=90_000,
    )

    live_authority = prompt["live_authority"]
    assert live_authority["status"] != "omitted_for_prompt_budget"
    assert live_authority["status"] == "ok"
    assert live_authority["live_grade"] == "restricted"
    assert live_authority["validation_gate"]["status"] == "validation_probe"
    assert prompt["prompt_budget"]["over_max"] is False


def test_prompt_budget_compacts_legacy_decision_packet_before_canonical() -> None:
    ordered_sections = [row[0] for row in PROMPT_BUDGET_COMPACTION_ORDER]

    assert ordered_sections.index("decision_packet") < ordered_sections.index(
        "decision_packet_v2"
    )


def test_compact_prompt_section_routes_special_sections() -> None:
    long_text = "긴 설명 " * 80

    blocks = compact_prompt_section(
        "blocks",
        [
            {
                "block_id": "b1",
                "symbol": "005930",
                "name": "삼성전자",
                "qty_open": 1,
                "status": "open",
                "thesis": long_text,
                "metadata": {"horizon": "mid", "raw": "DROP"},
            },
            {
                "block_id": "b2",
                "symbol": "000660",
                "status": "open",
            },
        ],
        list_limit=1,
        string_limit=90,
    )
    quotes = compact_prompt_section(
        "quotes",
        [
            {
                "symbol": "005930",
                "price": 80000,
                "raw": "DROP",
                "unrelated": "DROP",
            }
        ],
        list_limit=3,
        string_limit=90,
    )
    events = compact_prompt_section(
        "recent_events",
        [
            {
                "id": 1,
                "event_type": "note",
                "message": long_text,
                "payload": {"symbol": "005930", "raw": "DROP"},
            }
        ],
        list_limit=3,
        string_limit=90,
    )
    generic = compact_prompt_section(
        "strategy",
        {
            "summary": long_text,
            "raw": "DROP",
            "items": [{"reason": long_text}, {"reason": "second"}],
        },
        list_limit=1,
        string_limit=90,
    )

    assert len(blocks) == 1
    assert blocks[0]["block_id"] == "b1"
    assert len(blocks[0]["thesis"]) <= 260
    assert "raw" not in blocks[0].get("metadata", {})
    assert quotes == [{"symbol": "005930", "price": 80000}]
    assert events[0]["payload"] == {"symbol": "005930"}
    assert len(events[0]["message"]) <= 90
    assert "raw" not in generic
    assert len(generic["summary"]) <= 90
    assert len(generic["items"]) == 1


def test_compact_prompt_section_preserves_jue_workflow_usage_contract() -> None:
    compact = compact_prompt_section(
        "jue_workflow",
        {
            "workflow_id": "kis_cycle",
            "instructions": "KIS workflow " * 80,
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


def test_public_prompt_payload_previews_content_and_drops_raw_fields() -> None:
    payload = public_prompt_payload(
        {
            "source": "naver",
            "content_md": "긴 원문 " * 80,
            "raw": "DROP",
            "raw_payload": {"secret": "DROP"},
            "nested": [
                {
                    "content_md": "하위 원문 " * 80,
                    "html": "<div>DROP</div>",
                    "title": "유지",
                }
            ],
        }
    )

    assert payload["preview"].startswith("긴 원문")
    assert len(payload["preview"]) <= 220
    assert "content_md" not in payload
    assert "raw" not in payload
    assert "raw_payload" not in payload
    assert payload["nested"][0]["preview"].startswith("하위 원문")
    assert "html" not in payload["nested"][0]
    assert payload["nested"][0]["title"] == "유지"


def test_build_prompt_strategy_payload_compacts_candidates_with_research_spine() -> None:
    payload = build_prompt_strategy_payload(
        {
            "status": "ok",
            "score_method_version": "v2",
            "candidate_count": 99,
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "asset_class": "equity",
                    "score": 84.9,
                    "confidence": 76.2,
                    "memory_hint": {
                        "reasons": ["외국인 수급 회복 전 추격 금지"],
                        "risks": ["수급 미확인 추격은 반복 손실"],
                        "sources": ["kis.symbol.005930.memory"],
                    },
                    "raw": "DROP",
                },
                {
                    "symbol": "069500",
                    "name": "KODEX 200",
                    "asset_class": "etf",
                    "score": 72,
                    "confidence": 68,
                },
                {
                    "symbol": "000660",
                    "name": "SK하이닉스",
                    "score": 71,
                    "confidence": 66,
                },
            ],
            "exclusions": [{"symbol": "123456"}, {"symbol": "234567"}],
            "sources": [
                {
                    "source": "naver",
                    "content_md": "원문 본문 " * 80,
                    "raw_payload": "DROP",
                }
            ],
            "methodology": ["리포트/RAG/수급/밸류를 함께 본다 " * 20],
        },
        research_spine={
            "packets": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "asset_class": "equity",
                    "buckets": ["report", "valuation", "market", "memory", "extra"],
                    "quality": {
                        "decision_use": "primary",
                        "warnings": ["실적 확인 필요", "외국인 수급 약함", "ETF 대체 가능", "extra"],
                    },
                },
                {
                    "symbol": "069500",
                    "buckets": ["etf", "market_pulse"],
                    "quality": {"decision_use": "hedge"},
                },
            ]
        },
        max_symbols=2,
    )

    assert payload["status"] == "ok"
    assert payload["mode"] == "reference_compact"
    assert payload["candidate_count"] == 99
    assert payload["exclusion_count"] == 2
    assert [row["symbol"] for row in payload["top_symbols"]] == ["005930", "069500"]
    assert payload["top_symbols"][0]["buckets"] == [
        "report",
        "valuation",
        "market",
        "memory",
    ]
    assert payload["top_symbols"][0]["decision_use"] == "primary"
    assert payload["top_symbols"][0]["warnings"] == [
        "실적 확인 필요",
        "외국인 수급 약함",
        "ETF 대체 가능",
    ]
    assert payload["top_symbols"][0]["memory_hint"] == {
        "reasons": ["외국인 수급 회복 전 추격 금지"],
        "risks": ["수급 미확인 추격은 반복 손실"],
        "sources": ["kis.symbol.005930.memory"],
    }
    assert payload["sources"][0]["preview"].startswith("원문 본문")
    assert "content_md" not in payload["sources"][0]
    assert "raw_payload" not in payload["sources"][0]
    assert len(payload["methodology"][0]) <= 160


def test_compact_etf_universe_rows_keeps_scan_fields_only() -> None:
    rows = compact_etf_universe_rows(
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "category": "core",
                "content_md": "DROP " * 100,
                "score": 99,
            },
            {
                "symbol": "091160",
                "name": "KODEX 반도체",
                "category": "sector",
                "raw": "DROP",
            },
            {
                "symbol": "102110",
                "name": "TIGER 200",
                "category": "core",
            },
        ],
        limit=2,
    )

    assert rows == [
        {"symbol": "069500", "name": "KODEX 200", "category": "core"},
        {"symbol": "091160", "name": "KODEX 반도체", "category": "sector"},
    ]


def test_enforce_prompt_budget_compacts_large_optional_sections() -> None:
    prompt = {
        "blocks": [
            {
                "block_id": "b1",
                "symbol": "005930",
                "status": "open",
                "thesis": "블록 논리 " * 200,
            }
        ],
        "strategy": {
            "summary": "전략 " * 8_000,
            "raw": "DROP",
            "items": [{"reason": "근거 " * 400} for _ in range(20)],
        },
        "candidate_policy_impacts": {
            "_global": {"summary": "정책 " * 2_000, "raw": "DROP"},
            **{
                f"{index:06d}": [{"reason": "영향 " * 300, "raw": "DROP"}]
                for index in range(40)
            },
        },
        "recent_events": [
            {
                "id": index,
                "event_type": "note",
                "message": "이벤트 " * 300,
                "payload": {"symbol": "005930", "raw": "DROP"},
            }
            for index in range(40)
        ],
    }

    before_chars = len(json.dumps(prompt, ensure_ascii=False, sort_keys=True))

    enforce_prompt_budget(prompt, max_chars=40_000)

    after_chars = len(json.dumps(prompt, ensure_ascii=False, sort_keys=True))
    assert after_chars < before_chars
    assert prompt["prompt_compaction"]["version"] == "prompt_compaction_v1"
    assert prompt["prompt_compaction"]["effective_max_chars"] == 37_500
    assert "raw" not in prompt["strategy"]
    assert len(prompt["strategy"]["summary"]) <= 180
    assert len(prompt["recent_events"]) <= 30
    assert "raw" not in prompt["recent_events"][0]["payload"]


def test_finalize_prompt_budget_attaches_budget_and_prefers_warn_limit() -> None:
    marker = "KIS_MANAGER_FINALIZE_WARN"
    prompt = {
        "task": "Manage KIS blocks",
        "clock": {"session": "regular"},
        "blocks": [
            {
                "block_id": "blk_1",
                "symbol": "005930",
                "status": "open",
                "thesis": "중기 보유",
            }
        ],
        "investment_memory": {
            "notes": [{"summary": marker * 90} for _ in range(18)]
        },
        "research_spine": [{"evidence": marker * 60} for _ in range(14)],
        "candidate_policy_impacts": {
            f"{idx:06d}": [{"reason": marker * 24}]
            for idx in range(18)
        },
        "strategy": {"items": [{"summary": marker * 40} for _ in range(8)]},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=50_000,
        warn_chars=70_000,
        max_chars=120_000,
    )

    text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(text) <= 70_000
    assert prompt["prompt_budget"]["version"] == "prompt_budget_v1"
    assert prompt["prompt_budget"]["over_warn"] is False


def test_kis_warn_budget_preserves_core_types_under_large_optional_context() -> None:
    prompt = {
        "decision_inputs": ["account", "quotes", "risk"],
        "candidates": [
            {"symbol": f"{index:06d}", "evidence": "x" * 2_000}
            for index in range(80)
        ],
        "blocks": [
            {"block_id": f"block-{index}", "notes": "y" * 1_000}
            for index in range(40)
        ],
        "decision_packet": {
            "candidates": [{"detail": "d" * 2_000} for _ in range(80)]
        },
        "pre_adoption_symbol_analysis": {
            "items": [{"detail": "a" * 2_000} for _ in range(80)]
        },
        "recent_events": [{"detail": "z" * 1_000} for _ in range(300)],
        "research_spine": {
            "items": [{"content": "r" * 2_000} for _ in range(100)]
        },
        "opportunity_research_brief": {
            "items": [{"content": "o" * 2_000} for _ in range(100)]
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=120_000,
        warn_chars=150_000,
        max_chars=190_000,
    )

    assert isinstance(prompt["decision_inputs"], list)
    assert isinstance(prompt["candidates"], list)
    assert isinstance(prompt["blocks"], list)
    assert prompt["prompt_budget"]["over_warn"] is False
    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_compaction"]["original_counts"]["recent_events"] == 300
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_compaction"]["sections"]


def test_finalize_prompt_budget_guarantees_attached_budget_under_max() -> None:
    marker = "KIS_MANAGER_FINALIZE_MAX"
    prompt = {
        "task": "Manage KIS blocks",
        "clock": {"session": "regular"},
        "account": {"cash_krw": 1_000_000},
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": "005930",
                "status": "open",
                "thesis": marker * 35,
                "risk_note": marker * 35,
            }
            for idx in range(18)
        ],
        "investment_memory": {"notes": [{"summary": marker * 80} for _ in range(24)]},
        "decision_packet_v2": [{"evidence": marker * 80} for _ in range(18)],
        "policy_rules": [{"body": marker * 80} for _ in range(18)],
        "candidate_policy_impacts": {
            f"{idx:06d}": [{"reason": marker * 50}]
            for idx in range(50)
        },
        "research_spine": [{"evidence": marker * 70} for _ in range(18)],
        "market_pulse": {"items": [{"summary": marker * 70} for _ in range(18)]},
        "quotes": [{"symbol": "005930", "raw": marker * 50} for _ in range(24)],
        "live_authority": {"validation": marker * 3000},
        "output_schema": {"schema": marker * 2000},
        "recent_events": [{"message": marker * 80} for _ in range(24)],
        "jue_workflow": {"instructions": marker * 2000},
        "strategy": {"items": [{"summary": marker * 70} for _ in range(16)]},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=60_000,
        warn_chars=80_000,
        max_chars=90_000,
    )

    text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(text) <= 90_000
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 90_000
    assert prompt_budget_error(prompt) == ""


def test_finalize_prompt_budget_recompacts_when_budget_metadata_pushes_over_max() -> None:
    marker = "KIS_MANAGER_FINALIZE_EDGE"
    prompt = {
        "task": "Manage KIS blocks",
        "clock": {"session": "regular"},
        "account": {"cash_krw": 1_000_000},
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": "005930",
                "status": "open",
                "thesis": marker * 205,
                "risk_note": marker * 205,
            }
            for idx in range(30)
        ],
        "candidate_policy_impacts": {
            f"{idx:06d}": [{"reason": marker * 205}]
            for idx in range(100)
        },
        "research_spine": [{"evidence": marker * 205} for _ in range(100)],
        "market_pulse": {"items": [{"summary": marker * 205} for _ in range(100)]},
        "jue_wiki": {
            "pages": [
                {
                    "page_id": f"kis.symbol.{idx:06d}",
                    "selection_reasons": [marker * 205],
                }
                for idx in range(100)
            ],
            "content": marker * 2050,
        },
        "investment_memory": {
            "notes": [{"summary": marker * 205} for _ in range(100)]
        },
        "opportunity_research_brief": {
            "pre_surge_candidates": [
                {"symbol": "123450", "reason": marker * 205}
                for _ in range(50)
            ]
        },
        "output_schema": {"schema": marker * 20_500},
        "jue_workflow": {
            "instructions": marker * 20_500,
            "contracts": [
                {"contract_id": "legacy_contract", "detail": marker * 20},
                {
                    "contract_id": "jue_wiki_usage_contract_resolution",
                    "required_metadata": "jue_wiki_usage_contract_resolution",
                },
            ],
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=100_000,
        warn_chars=150_000,
        max_chars=190_000,
    )

    text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(text) <= 190_000
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 190_000
    assert prompt_budget_error(prompt) == ""
    contract_ids = [
        row.get("contract_id")
        for row in prompt["jue_workflow"].get("contracts", [])
        if isinstance(row, dict)
    ]
    assert "jue_wiki_usage_contract_resolution" in contract_ids


def test_compact_investment_memory_prompt_filters_binance_scoped_rows_for_kis() -> None:
    compact = compact_investment_memory_prompt(
        {
            "status": "ok",
            "memory_scope": "kis",
            "items": [
                {
                    "memory_scope": "binance",
                    "summary": "ETHUSDT funding squeeze only",
                },
                {
                    "page_id": "binance.symbol.SOLUSDT",
                    "summary": "SOLUSDT unscoped wiki row must not reach KIS",
                },
                {
                    "page_id": "binance:symbol:ATOMUSDT",
                    "summary": "colon Binance page id must not reach KIS",
                },
                {
                    "page_id": "crypto/symbol/AVAXUSDT",
                    "summary": "slash crypto page id must not reach KIS",
                },
                {
                    "scope": "domestic_crypto_equity",
                    "summary": "ambiguous domestic crypto equity must not reach KIS",
                },
                {
                    "policy_id": "binance.short_churn",
                    "summary": "bare Binance policy id must not reach KIS",
                },
                {
                    "memory_scope": "kis",
                    "summary": "삼성전자 눌림목 대기",
                },
                {
                    "memory_scope": "global",
                    "summary": "과도한 추격매수는 피한다",
                },
                {
                    "memory_scope": "binance",
                    "transferability": "translated",
                    "summary": "크립토 변동성 교훈을 KIS 대기진입으로 번역",
                },
                {
                    "page_id": "binance.symbol.XRPUSDT",
                    "transferability": "translated",
                    "summary": "translated unscoped Binance page lesson for KIS",
                },
                {
                    "policy_id": "binance.value_transfer",
                    "transferability": "translated",
                    "summary": "translated bare Binance policy id lesson for KIS",
                },
                {
                    "page_id": "binance:symbol:LINKUSDT",
                    "transferability": "translated",
                    "summary": "translated colon Binance page id lesson for KIS",
                },
            ],
            "notes": [
                {
                    "scope": "domestic_crypto_equity",
                    "transferability": "translated",
                    "summary": "translated ambiguous crypto equity lesson for KIS",
                },
            ],
            "active_policies": [
                {
                    "scope": "crypto",
                    "policy_id": "policy.binance.short_churn",
                    "rule": "short squeeze watch",
                },
                {
                    "scope": "kr_equity",
                    "policy_id": "policy.kis.value_pullback",
                    "rule": "저평가 눌림목 선호",
                },
            ],
            "symbol_notes": {
                "ETHUSDT": "ETH scalar crypto note",
                "BTCUSDT": {
                    "market": "binance",
                    "summary": "BTC orderbook imbalance",
                },
                "000660": "SK하이닉스 scalar KIS note",
                "005930": {
                    "market": "kis",
                    "summary": "삼성전자 밸류 점검",
                },
            },
            "block_notes": {
                "generic_note": {
                    "page_id": "binance.symbol.AAVEUSDT",
                    "summary": "generic keyed Binance mapping must not reach KIS",
                },
                "translated_note": {
                    "page_id": "binance.symbol.DOGEUSDT",
                    "transferability": "translated",
                    "summary": "translated generic keyed Binance mapping lesson for KIS",
                },
            },
            "period_reviews": [
                {
                    "target_scope": "binance",
                    "summary": "BTCUSDT futures overtrading review",
                },
                {
                    "target_scope": "kis",
                    "summary": "KIS 장중 눌림 대기 리뷰",
                },
            ],
            "jue_wiki_action_reference_memory": {
                "target_scope": "binance",
                "status": "available",
                "items": [
                    {
                        "page_id": "binance.symbol.BTCUSDT",
                        "summary": "BTCUSDT nested reference",
                    },
                    {
                        "page_id": "binance.symbol.ETHUSDT",
                        "transferability": "translated",
                        "summary": "translated nested Binance container lesson for KIS",
                    }
                ],
            },
            "jue_wiki_usage_contract_memory": {
                "target_scope": "binance",
                "status": "available",
                "items": [
                    {
                        "page_id": "binance.symbol.BNBUSDT",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                        },
                        "summary_md": "raw Binance usage contract gap must not reach KIS",
                    },
                    {
                        "page_id": "binance.symbol.ADAUSDT",
                        "transferability": "translated",
                        "application_guidance": {
                            "required_evidence": [
                                "jue_wiki_usage_contract_resolution"
                            ],
                        },
                        "summary_md": (
                            "translated Binance usage contract gap lesson for KIS"
                        ),
                    },
                ],
            },
            "jue_wiki_context_gap_memory": {
                "target_scope": "kis",
                "status": "available",
                "items": [
                    {
                        "page_id": "kis.symbol.005930",
                        "summary": "005930 nested reference",
                    }
                ],
            },
            "decision_skills": {
                "jue-binance-trading": {
                    "skill_id": "jue-binance-trading",
                    "preview": "Binance futures skill preview",
                },
                "jue-kis-trading": {
                    "skill_id": "jue-kis-trading",
                    "preview": "KIS equity skill preview",
                },
            },
            "decision_skill_status": {
                "jue-binance-trading": {
                    "status": "ready",
                    "preview": "Binance skill status nested",
                },
                "jue-kis-trading": {
                    "status": "ready",
                    "preview": "KIS skill status nested",
                },
            },
            "translated_policy_context": {
                "status": "available",
                "target_scope": "kis",
                "binance_nested_list": [
                    {
                        "page_id": "binance.symbol.RAWUSDT",
                        "summary": "raw Binance list child must not reach KIS",
                    },
                    {
                        "page_id": "binance.symbol.APTUSDT",
                        "transferability": "translated",
                        "summary": "translated list child under Binance key for KIS",
                    },
                ],
                "items_by_policy": {
                    "binance.breakout.long": {
                        "transferability": "translated",
                        "summary": "translated keyed Binance breakout lesson",
                    },
                    "binance.raw.short": {
                        "summary": "untranslated keyed Binance raw lesson",
                    },
                },
            },
        },
        list_limit=6,
        string_limit=120,
    )

    encoded = json.dumps(compact, ensure_ascii=False)
    assert "ETHUSDT funding squeeze only" not in encoded
    assert "SOLUSDT unscoped wiki row must not reach KIS" not in encoded
    assert "colon Binance page id must not reach KIS" not in encoded
    assert "slash crypto page id must not reach KIS" not in encoded
    assert "ambiguous domestic crypto equity must not reach KIS" not in encoded
    assert "bare Binance policy id must not reach KIS" not in encoded
    assert "policy.binance.short_churn" not in encoded
    assert "ETH scalar crypto note" not in encoded
    assert "BTC orderbook imbalance" not in encoded
    assert "generic keyed Binance mapping must not reach KIS" not in encoded
    assert "BTCUSDT futures overtrading review" not in encoded
    assert "BTCUSDT nested reference" not in encoded
    assert "raw Binance usage contract gap must not reach KIS" not in encoded
    assert "Binance futures skill preview" not in encoded
    assert "Binance skill status nested" not in encoded
    assert "untranslated keyed Binance raw lesson" not in encoded
    assert "raw Binance list child must not reach KIS" not in encoded
    assert "삼성전자 눌림목 대기" in encoded
    assert "과도한 추격매수는 피한다" in encoded
    assert "크립토 변동성 교훈을 KIS 대기진입으로 번역" in encoded
    assert "translated unscoped Binance page lesson for KIS" in encoded
    assert "translated generic keyed Binance mapping lesson for KIS" in encoded
    assert "translated nested Binance container lesson for KIS" in encoded
    assert "translated Binance usage contract gap lesson for KIS" in encoded
    assert "jue_wiki_usage_contract_resolution" in encoded
    assert "translated list child under Binance key for KIS" in encoded
    assert "translated bare Binance policy id lesson for KIS" in encoded
    assert "translated colon Binance page id lesson for KIS" in encoded
    assert "translated ambiguous crypto equity lesson for KIS" in encoded
    assert "저평가 눌림목 선호" in encoded
    assert "SK하이닉스 scalar KIS note" in encoded
    assert "삼성전자 밸류 점검" in encoded
    assert "KIS 장중 눌림 대기 리뷰" in encoded
    assert "005930 nested reference" in encoded
    assert "KIS equity skill preview" in encoded
    assert "KIS skill status nested" in encoded
    assert "translated keyed Binance breakout lesson" in encoded


def test_compact_investment_memory_prompt_preserves_policy_rule_provenance() -> None:
    compact = compact_investment_memory_prompt(
        {
            "status": "ok",
            "memory_scope": "kis",
            "policy_rule_evaluation": {
                "global": [
                    {
                        "policy_id": "jue_wiki_action_reference_gap.kis.missing",
                        "rule_id": "jue_wiki_action_reference_gap.kis.missing@v1",
                        "effect": {"entry_bias": "require_wiki_action_reference"},
                        "evidence": {
                            "workflow_ids": ["kis_intraday_manager"],
                            "skill_ids": ["jue-kis-trading"],
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
    assert impact["evidence"]["workflow_ids"] == ["kis_intraday_manager"]
    assert impact["evidence"]["skill_ids"] == ["jue-kis-trading"]
    assert impact["evidence"]["contract_ids"] == ["jue_wiki_action_reference_contract"]


def test_finalize_prompt_budget_preserves_compact_investment_memory() -> None:
    marker = "KIS_MEMORY_WIKI_CONTEXT_PRESSURE"
    prompt = {
        "task": "Manage KIS blocks",
        "clock": {"session": "regular"},
        "account": {"cash_krw": 1_000_000},
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": "005930",
                "name": "삼성전자",
                "qty_open": 1,
                "thesis": marker * 20,
                "risk_note": marker * 20,
            }
            for idx in range(12)
        ],
        "investment_memory": {
            "status": "ok",
            "memory_scope": "kis",
            "persona": "쥬는 적극적으로 수익 기회를 찾는다.",
            "trading_policy": "probe와 대기진입으로 표본을 축적한다.",
            "scoped_memory": {"core": [{"summary_md": marker * 50} for _ in range(20)]},
            "active_policies": [
                {"policy_id": f"p{idx}", "reason": marker * 50}
                for idx in range(80)
            ],
            "policy_scorecards": [
                {"policy_id": str(idx), "evidence": marker * 40}
                for idx in range(40)
            ],
            "translated_policy_context": {
                "status": "available",
                "target_scope": "kis",
                "available_count": 9,
                "selected_count": 4,
                "omitted_count": 5,
                "source_scope_counts": {"binance": 9},
                "selection_policy": {
                    "order": "active status, then prompt order",
                    "limit": 4,
                },
                "items": [
                    {
                        "policy_id": "binance.breakout.long",
                        "source_scope": "binance",
                        "transferability": "translated",
                        "status": "active_preference",
                        "action": "prefer",
                        "sample_count": 8,
                        "confidence": 0.74,
                        "expectancy_pct": 0.42,
                        "reason": "바이낸스 돌파 교훈은 KIS에서 번역 참고만 한다.",
                    }
                ],
                "instruction": (
                    "Use these only as translated lessons, never as direct venue rules."
                ),
            },
            "policy_rules": [
                {"rule_id": str(idx), "body": marker * 40}
                for idx in range(40)
            ],
            "validation_repair_backlog": {
                f"case_{idx}": {"summary": marker * 40}
                for idx in range(80)
            },
            "period_memory_coverage": {
                "status": "needs_attention",
                "scopes": ["kis"],
                "missing": ["kis:weekly_replay", "kis:monthly_review"],
                "weekly_reviews": {
                    "kis": {"status": "ok", "period_key": "2026-W21"}
                },
                "weekly_replays": {"kis": {"status": "missing"}},
                "monthly_reviews": {"kis": {"status": "missing"}},
            },
            "period_reviews": {
                "weekly": {"summary": marker * 300},
                "monthly": {"summary": marker * 300},
            },
            "policy_rule_evaluation": {
                "global": [
                    {
                        "policy_id": "jue_wiki_action_reference_gap.kis.missing",
                        "rule_id": "jue_wiki_action_reference_gap.kis.missing@v1",
                        "effect": {"entry_bias": "require_wiki_action_reference"},
                        "evidence": {
                            "workflow_ids": ["kis_intraday_manager"],
                            "skill_ids": ["jue-kis-trading"],
                            "contract_ids": ["jue_wiki_action_reference_contract"],
                        },
                    }
                ],
                **{
                    f"rule_{idx}": {"summary": marker * 40}
                    for idx in range(80)
                },
            },
            "symbol_notes": {f"{idx:06d}": marker * 40 for idx in range(350)},
        },
        "research_spine": {
            "status": "ok",
            "packets": [
                {
                    "symbol": f"{idx:06d}",
                    "name": "후보",
                    "evidence": marker * 80,
                    "quality": {"decision_use": "candidate"},
                }
                for idx in range(80)
            ],
        },
        "daily_discovery": {
            "status": "ok",
            "items": [
                {
                    "symbol": f"12{idx:04d}",
                    "name": "선행후보",
                    "analysis": {"summary": marker * 70},
                    "pre_surge": {"is_candidate": True, "reasons": [marker * 20]},
                }
                for idx in range(50)
            ],
        },
        "candidate_policy_impacts": {
            f"{idx:06d}": [{"reason": marker * 50}]
            for idx in range(120)
        },
        "policy_rules": [{"body": marker * 80} for _ in range(80)],
        "recent_events": [{"message": marker * 80} for _ in range(50)],
        "market_pulse": {"items": [{"summary": marker * 80} for _ in range(30)]},
        "decision_packet_v2": [{"evidence": marker * 80} for _ in range(30)],
        "quotes": [{"symbol": f"{idx:06d}", "raw": marker * 50} for idx in range(30)],
        "live_authority": {"validation": marker * 3000},
        "output_schema": {"schema": marker * 2500},
        "jue_workflow": {"instructions": marker * 2500},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=60_000,
        warn_chars=80_000,
        max_chars=90_000,
    )

    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(prompt_text) <= 90_000
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt_budget_error(prompt) == ""
    assert prompt["investment_memory"]["status"] == "ok"
    assert prompt["investment_memory"]["memory_scope"] == "kis"
    assert prompt["investment_memory"]["persona"]
    assert prompt["investment_memory"]["scoped_memory"]["core"]
    assert prompt["investment_memory"]["active_policies"]
    assert prompt["investment_memory"]["policy_rules"]
    translated_policy_context = prompt["investment_memory"]["translated_policy_context"]
    assert translated_policy_context["status"] == "available"
    assert translated_policy_context["available_count"] == 9
    assert translated_policy_context["selected_count"] == 4
    assert translated_policy_context["omitted_count"] == 5
    assert translated_policy_context["source_scope_counts"] == {"binance": 9}
    assert translated_policy_context["selection_policy"]["limit"] == 4
    assert translated_policy_context["items"][0]["policy_id"] == "binance.breakout.long"
    assert translated_policy_context["items"][0]["source_scope"] == "binance"
    assert translated_policy_context["items"][0]["transferability"] == "translated"
    assert "direct venue rules" in translated_policy_context["instruction"]
    assert prompt["investment_memory"]["period_memory_coverage"]["status"] == (
        "needs_attention"
    )
    assert prompt["investment_memory"]["period_memory_coverage"]["missing"] == [
        "kis:weekly_replay",
        "kis:monthly_review",
    ]
    policy_impact = prompt["investment_memory"]["policy_rule_evaluation"]["global"][0]
    assert policy_impact["evidence"]["workflow_ids"] == ["kis_intraday_manager"]
    assert policy_impact["evidence"]["skill_ids"] == ["jue-kis-trading"]
    assert policy_impact["evidence"]["contract_ids"] == [
        "jue_wiki_action_reference_contract"
    ]
    assert (
        len(json.dumps(prompt["investment_memory"], ensure_ascii=False, sort_keys=True))
        <= 18_000
    )
    assert not any(
        row["section"] == "investment_memory:final_omitted"
        for row in prompt.get("prompt_compaction", {}).get("sections", [])
    )


def test_compact_investment_memory_preserves_jue_wiki_selection_guidance() -> None:
    compact = compact_investment_memory_prompt(
        {
            "status": "ok",
            "memory_scope": "kis",
            "jue_wiki_selection_memory": {
                "status": "available",
                "target_scope": "kis",
                "items": [
                    {
                        "policy_id": (
                            "jue_wiki_selection.kis."
                            "operational_memory_manager_contract_recovery"
                        ),
                        "primary_reason": (
                            "operational_memory:manager_contract_recovery"
                        ),
                        "selected_page_ids": ["kis.ops.manager_runs"],
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
                            "cross_check_page_ids": ["kis.ops.manager_runs"],
                        },
                    }
                ],
            },
        },
        list_limit=3,
        string_limit=120,
    )

    selection_memory = compact["jue_wiki_selection_memory"]
    guidance = selection_memory["items"][0]["application_guidance"]
    assert selection_memory["status"] == "available"
    assert guidance["status"] == "freshness_repair_required"
    assert guidance["manager_instruction"] == (
        "refresh_or_cross_check_selected_wiki_before_size_increase"
    )
    assert guidance["required_evidence"] == [
        "fresh_jue_wiki_context",
        "selection_audit_resolution",
        "live_cross_check",
    ]


def test_manager_storage_compaction_meta_caps_retained_keys() -> None:
    meta = manager_storage_compaction_meta(
        label="prompt",
        original_chars=1200,
        storage_limit_chars=800,
        retained_keys=[f"k{i}" for i in range(60)],
        emergency=True,
    )

    assert meta["status"] == "compacted"
    assert meta["label"] == "prompt"
    assert meta["original_chars"] == 1200
    assert meta["storage_limit_chars"] == 800
    assert len(meta["retained_keys"]) == 50
    assert meta["emergency"] is True


def test_compact_manager_storage_payload_uses_callback_and_preserves_budget() -> None:
    calls: list[tuple[int, int]] = []

    def compact_value(value: object, *, list_limit: int, string_limit: int) -> object:
        calls.append((list_limit, string_limit))
        assert isinstance(value, dict)
        return {
            "summary": "x" * 50,
            "items": list(range(min(list_limit, 3))),
        }

    payload = {
        "large": "y" * 5_000,
        "items": list(range(100)),
        "prompt_budget": {"total_chars": 5_100},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_200,
        label="prompt",
        compact_value=compact_value,
        clean_text=lambda value, limit: str(value or "")[:limit],
    )

    assert calls
    assert compact["prompt_budget"] == {"total_chars": 5_100}
    assert compact["_storage_compaction"]["label"] == "prompt"
    assert compact["_storage_compaction"]["original_chars"] > 1_200


def test_compact_manager_storage_payload_preserves_kis_diagnostics() -> None:
    payload = {
        "diagnostics": {
            "version": "kis_manager_diagnostics_v1",
            "blocker_tags": {
                "unresolved_jue_wiki_requested_symbol_coverage": 2,
                "unresolved_jue_wiki_selection_guidance": 2,
                "unresolved_jue_wiki_action_reference_recovery": 7,
            },
            "top_blockers": [
                {
                    "tag": "unresolved_jue_wiki_action_reference_recovery",
                    "score": 7,
                    "details": "oversized recovery diagnostic detail " * 80,
                }
            ],
            "jue_wiki_missing_summary_symbols": ["000660"],
            "jue_wiki_selection_guidance_status": "active",
            "jue_wiki_selection_guidance_resolution_status": "unresolved",
            "jue_wiki_action_reference_memory_status": "active",
            "jue_wiki_action_reference_memory_resolution_status": "unresolved",
            "jue_wiki_action_reference_status": "missing",
            "jue_wiki_action_reference_count": 0,
            "jue_wiki_action_reference_ratio": 0.0,
            "jue_wiki_action_reference_unscoped_page_ids": [
                "kis.symbol.005930"
            ],
            "jue_wiki_action_reference_unscoped_page_omitted_count": 2,
            "jue_wiki_action_reference_missing_actions": [
                {
                    "section": "create_blocks",
                    "symbol": "000660",
                    "qty": 1,
                    "horizon": "mid",
                    "reason": "missing wiki action reference",
                }
            ],
            "jue_wiki_action_reference_recovery_status": "unresolved",
            "jue_wiki_action_reference_recovery_memory_scope": "kis",
            "jue_wiki_action_reference_recovery_open_gap_count": 7,
            "jue_wiki_action_reference_recovery_resolved_count": 0,
            "jue_wiki_action_reference_recovery_total_count": 7,
            "jue_wiki_action_reference_recovery_ratio": 0.0,
            "jue_wiki_action_reference_recovery_latest_resolution_status": (
                "unresolved"
            ),
            "jue_wiki_action_reference_recovery_latest_status": "missing",
            "degraded_jue_wiki_effectiveness_count": 1,
            "degraded_jue_wiki_effectiveness_page_ids": ["kis.symbol.000660"],
            "degraded_jue_wiki_effectiveness_resolution_status": "unresolved",
            "raw_notes": "diagnostics detail " * 500,
        },
        "jue_wiki_requested_symbol_coverage": {
            "status": "partial",
            "missing_summary_symbols": ["000660"],
        },
        "large": "x" * 8_000,
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_200,
        label="kis_manager_prompt",
        compact_value=lambda value, **_: {"summary": "compacted"},
    )

    assert compact["diagnostics"]["blocker_tags"] == {
        "unresolved_jue_wiki_requested_symbol_coverage": 2,
        "unresolved_jue_wiki_selection_guidance": 2,
        "unresolved_jue_wiki_action_reference_recovery": 7,
    }
    assert compact["diagnostics"]["top_blockers"] == [
        {"tag": "unresolved_jue_wiki_action_reference_recovery", "weight": 7}
    ]
    assert compact["diagnostics"]["jue_wiki_missing_summary_symbols"] == ["000660"]
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
        "kis.symbol.005930"
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
            "symbol": "000660",
            "qty": 1,
            "horizon": "mid",
            "reason": "missing wiki action reference",
        }
    ]
    assert compact["diagnostics"]["jue_wiki_action_reference_recovery_status"] == (
        "unresolved"
    )
    assert compact["diagnostics"]["jue_wiki_action_reference_recovery_memory_scope"] == (
        "kis"
    )
    assert (
        compact["diagnostics"]["jue_wiki_action_reference_recovery_open_gap_count"]
        == 7
    )
    assert (
        compact["diagnostics"]["jue_wiki_action_reference_recovery_resolved_count"]
        == 0
    )
    assert (
        compact["diagnostics"]["jue_wiki_action_reference_recovery_total_count"]
        == 7
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
        "kis.symbol.000660"
    ]
    assert compact["diagnostics"][
        "degraded_jue_wiki_effectiveness_resolution_status"
    ] == "unresolved"
    assert "raw_notes" not in compact["diagnostics"]


def test_compact_manager_storage_payload_preserves_kis_rule_signal_review_pressure() -> None:
    prompt = {
        "decision_packet_v2": {
            "version": "decision_packet_v2",
            "blocks": [
                {
                    "block_id": "blk_010130_20260706055414397262",
                    "symbol": "010130",
                    "name": "고려아연",
                    "status": "open",
                    "horizon": "mid",
                    "qty_open": 1,
                    "entry_price": 1_080_000,
                    "stop_price": 1_045_000,
                    "technical": {"price": 1_003_000},
                    "stop_policy": {
                        "horizon": "mid",
                        "stop_touched_now": True,
                        "touch_action": "manager_review",
                        "latest_signal": {
                            "reason": "stop_reached",
                            "price": 1_045_000,
                            "created_at": "2026-07-08T04:04:08+00:00",
                        },
                    },
                }
            ],
        },
        "research_spine": {"packets": [{"symbol": "010130", "raw": "R" * 8_000}]},
        "daily_discovery": {"block_candidates": [{"symbol": "010130", "raw": "D" * 8_000}]},
        "jue_wiki": {"pages": [{"page_id": "kis.symbol.010130", "summary": "W" * 8_000}]},
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 80_000},
    }

    compact = compact_manager_storage_payload(
        prompt,
        limit=1_500,
        label="kis_manager_prompt",
    )

    pressure = compact["manager_action_required"]
    assert pressure["status"] == "action_required"
    assert pressure["resolution_contract"] == "close_or_explicit_hold_review"
    assert pressure["item_count"] == 1
    assert pressure["items"] == [
        {
            "block_id": "blk_010130_20260706055414397262",
            "symbol": "010130",
            "name": "고려아연",
            "horizon": "mid",
            "reason": "stop_reached",
            "signal_type": "stop_signal",
            "policy_action": "manager_review",
            "current_price": 1_003_000,
            "signal_price": 1_045_000,
            "signal_at": "2026-07-08T04:04:08+00:00",
        }
    ]


def test_kis_manager_contract_requires_mid_stop_signal_resolution() -> None:
    prompt = {
        "manager_action_required": {
            "status": "action_required",
            "resolution_contract": "close_or_explicit_hold_review",
            "items": [
                {
                    "block_id": "blk_010130_20260706055414397262",
                    "symbol": "010130",
                    "horizon": "mid",
                    "reason": "stop_reached",
                    "signal_type": "stop_signal",
                    "policy_action": "manager_review",
                }
            ],
        }
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={"hold_decision": {"summary": "관망"}},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert error == "rule_signal_review_resolution_missing_from_model"


def test_kis_manager_contract_accepts_explicit_mid_stop_hold_review() -> None:
    prompt = {
        "manager_action_required": {
            "status": "action_required",
            "resolution_contract": "close_or_explicit_hold_review",
            "items": [
                {
                    "block_id": "blk_010130_20260706055414397262",
                    "symbol": "010130",
                    "horizon": "mid",
                    "reason": "stop_reached",
                    "signal_type": "stop_signal",
                    "policy_action": "manager_review",
                }
            ],
        }
    }

    error = kis_manager_response_contract_error(
        prompt=prompt,
        response={},
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={
            "summary": "고려아연 중기 손절 신호는 리서치 재확인 후 보류",
            "watch_symbols": ["010130"],
            "next_triggers": [
                {
                    "symbol": "010130",
                    "condition": "1,000,000원 회복 실패 또는 추가 저점 이탈 시 청산",
                    "horizon": "mid",
                    "reason": "stop_reached manager_review explicit hold",
                }
            ],
        },
    )

    assert error == ""


def test_compact_manager_storage_payload_has_default_compaction_adapters() -> None:
    payload = {
        "status": "ok",
        "hold_decision": {
            "summary": "중기 블록 유지",
            "notes": "긴 판단 근거 " * 700,
        },
        "large_model_trace": {"trace": "raw reasoning " * 2_000},
        "prompt_budget": {"total_chars": 40_000},
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=1_000,
        label="kis_manager_response",
    )

    assert compact["_storage_compaction"]["status"] == "compacted"
    assert compact["prompt_budget"] == {"total_chars": 40_000}
    assert compact["hold_decision"]["summary"] == "중기 블록 유지"
    assert len(str(compact["hold_decision"])) < len(str(payload["hold_decision"]))


def test_kis_manager_actions_storage_compacts_applied_full_block_metadata() -> None:
    huge_metadata = {
        "jue_wiki": {"pages": [{"body": "wiki-context " * 1_000}]},
        "research_spine": {"packets": [{"body": "research-context " * 1_000}]},
        "validation_repair": {"raw": "repair-context " * 1_000},
        "selected_candidate": {"raw": "candidate-context " * 1_000},
    }
    payload = {
        "create_blocks": [
            {
                "symbol": "003070",
                "name": "코오롱글로벌",
                "qty": 3,
                "entry_style": "wait_for_price",
                "entry_trigger_price": 9850,
                "target_price": 10850,
                "stop_price": 9450,
                "thesis": "눌림 대기",
            }
        ],
        "_applied": {
            "created": [
                {
                    "status": "waiting_entry",
                    "entry_trigger_price": 9850,
                    "block": {
                        "block_id": "blk_003070_202607020001",
                        "symbol": "003070",
                        "name": "코오롱글로벌",
                        "qty_initial": 3,
                        "qty_open": 0,
                        "entry_price": 9850,
                        "target_price": 10850,
                        "stop_price": 9450,
                        "status": "proposed",
                        "metadata": huge_metadata,
                    },
                }
            ],
            "updated": [
                {
                    "block_id": "blk_002290_202607020001",
                    "symbol": "002290",
                    "metadata": huge_metadata,
                    "target_price": 3900,
                    "stop_price": 3220,
                }
            ],
            "policy_rule_impacts": {
                f"{idx:06d}": [
                    {
                        "rule_id": f"rule-{idx}",
                        "reason": "policy-impact-context " * 200,
                    }
                ]
                for idx in range(40)
            },
        },
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=12_000,
        label="kis_manager_actions",
    )
    compact_text = json.dumps(compact, ensure_ascii=False, sort_keys=True)

    assert len(compact_text) <= 12_000
    assert compact["_storage_compaction"]["label"] == "kis_manager_actions"
    assert compact["_applied"]["created"]["item_count"] == 1
    created = compact["_applied"]["created"]["items"][0]
    assert created["block_id"] == "blk_003070_202607020001"
    assert created["symbol"] == "003070"
    assert created["name"] == "코오롱글로벌"
    assert created["qty_initial"] == 3
    assert "metadata" not in json.dumps(created, ensure_ascii=False)
    assert "wiki-context" not in compact_text
    assert "research-context" not in compact_text
    assert "policy-impact-context" not in compact_text
    assert compact["_applied"]["policy_rule_impacts"]["symbol_count"] == 40


def test_kis_manager_actions_storage_preserves_wiki_repair_action_metadata() -> None:
    wiki_action_metadata = {
        "jue_wiki_repair_pressure": {
            "status": "required",
            "page_id": "kis.symbol.003070",
            "reason": "종목 메모리 카드가 오래되어 신규 블록 근거와 교차 점검 필요",
        },
        "jue_wiki_repair_resolution": {
            "status": "resolved_by_action",
            "evidence_ids": ["report:003070:2026-07-02", "naver:003070"],
        },
        "jue_wiki_memory_card_quality": {
            "status": "degraded",
            "missing": ["recent_thesis", "exit_lesson"],
        },
        "jue_wiki_memory_card_cross_check": {
            "status": "cross_checked",
            "sources": ["research_spine", "valuation_snapshot"],
        },
        "jue_wiki_reference_basis": (
            "jue_wiki_action_reference_gap.kis.missing 보정: "
            "live_cross_check와 research_spine 근거를 액션에 남김"
        ),
        "jue_wiki_usage_contract_resolution": (
            "위키는 단독 매매권한이 아니며 live_quote/account_state/risk_gate "
            "교차확인 후 1주 대기 블록으로 축소"
        ),
        "raw_wiki_page": "raw wiki body " * 600,
    }
    payload = {
        "create_blocks": [
            {
                "symbol": "003070",
                "name": "코오롱글로벌",
                "qty": 3,
                "entry_style": "wait_for_price",
                "entry_trigger_price": 9850,
                "target_price": 10850,
                "stop_price": 9450,
                "metadata": wiki_action_metadata,
                "thesis": "눌림 대기 " * 300,
            }
        ],
        "_applied": {
            "created": [
                {
                    "status": "waiting_entry",
                    "block": {
                        "block_id": "blk_003070_202607020001",
                        "symbol": "003070",
                        "name": "코오롱글로벌",
                        "qty_initial": 3,
                        "entry_price": 9850,
                        "target_price": 10850,
                        "stop_price": 9450,
                        "metadata": wiki_action_metadata,
                    },
                }
            ],
        },
    }

    compact = compact_manager_storage_payload(
        payload,
        limit=4_000,
        label="kis_manager_actions",
    )
    compact_text = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    create_metadata = compact["create_blocks"][0]["metadata_summary"]
    created_context = compact["_applied"]["created"]["items"][0]["context_summary"]

    assert "raw wiki body" not in compact_text
    for key in (
        "jue_wiki_repair_pressure",
        "jue_wiki_repair_resolution",
        "jue_wiki_memory_card_quality",
        "jue_wiki_memory_card_cross_check",
        "jue_wiki_reference_basis",
        "jue_wiki_usage_contract_resolution",
    ):
        assert key in create_metadata
        assert key in created_context
    assert create_metadata["jue_wiki_repair_pressure"]["status"] == "required"
    assert created_context["jue_wiki_repair_resolution"]["status"] == (
        "resolved_by_action"
    )


def test_finalize_prompt_budget_keeps_final_payload_under_max_after_budget_report() -> None:
    marker = "FINAL_BUDGET_RESERVE_MARKER "
    prompt = {
        "task": "KIS manager final budget reserve test",
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": f"{idx:06d}",
                "metadata": {"raw": marker * 220},
                "thesis": marker * 40,
            }
            for idx in range(40)
        ],
        "investment_memory": {
            "status": "ok",
            "policy_rules": [{"reason": marker * 80} for _ in range(40)],
            "symbol_analyses": {
                f"{idx:06d}": [{"summary": marker * 50}] for idx in range(40)
            },
        },
        "decision_packet_v2": [{"evidence": marker * 90} for _ in range(35)],
        "live_authority": {
            "status": "ok",
            "validation_gate": {"details": marker * 400},
            "lane_authority": [{"reason": marker * 120} for _ in range(20)],
        },
        "validation_repair": {
            "status": "risk_repair",
            "repair_backlog": [{"reason": marker * 100} for _ in range(20)],
        },
        "output_schema": {"schema": marker * 260},
        "opportunity_research_brief": {
            "pre_surge_candidates": [{"why": marker * 80} for _ in range(10)]
        },
        "research_spine": {"packets": [{"summary": marker * 80} for _ in range(15)]},
        "strategy": {"candidates": [{"reason": marker * 80} for _ in range(20)]},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=100_000,
        warn_chars=150_000,
        max_chars=190_000,
    )

    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(prompt_text) <= 190_000
    assert prompt_budget_error(prompt) == ""


def test_finalize_prompt_budget_compacts_requested_symbol_coverage_contract() -> None:
    marker = "KIS_REQUESTED_SYMBOL_COVERAGE_BLOAT "
    prompt = {
        "task": "KIS manager coverage contract budget test",
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "partial",
            "unsummarized_symbols": [
                f"{idx:06d}:{marker * 30}" for idx in range(180)
            ],
            "required_action": "live cross-check required before confidence",
            "required_adjustments": [
                {"symbol": f"{idx:06d}", "reason": marker * 30}
                for idx in range(60)
            ],
        },
        "jue_wiki": {
            "status": "ok",
            "pages": [{"page_id": "kis.symbol.005930", "content": marker * 80}],
        },
        "research_spine": {"packets": [{"summary": marker * 80} for _ in range(15)]},
        "daily_discovery": {
            "block_candidates": [{"symbol": f"{idx:06d}", "why": marker * 40} for idx in range(30)]
        },
        "output_schema": {"schema": marker * 200},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=20_000,
        warn_chars=25_000,
        max_chars=30_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_max"] is False
    coverage = prompt["jue_wiki_requested_symbol_coverage"]
    assert coverage["status"] == "partial"
    assert "000000" in json.dumps(coverage, ensure_ascii=False)
    assert marker not in json.dumps(coverage, ensure_ascii=False)


def test_finalize_prompt_budget_keeps_requested_symbol_coverage_missing_and_omitted() -> None:
    marker = "KIS_REQUESTED_SYMBOL_COVERAGE_BLOAT "
    prompt = {
        "task": "KIS manager coverage split budget test",
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "partial",
            "unsummarized_symbols": ["000660", "277810", marker * 20],
            "missing_summary_symbols": ["000660", marker * 20],
            "prompt_omitted_symbols": ["277810", marker * 20],
            "required_adjustments": [
                {
                    "adjustment_type": "coverage_gap_follow_up",
                    "reason": marker * 20,
                    "symbols": ["000660"],
                    "resolution": "collect_or_rebuild_summary_before_confident_decision",
                },
                {
                    "adjustment_type": "prompt_omission_follow_up",
                    "reason": marker * 20,
                    "symbols": ["277810"],
                    "resolution": "treat_as_reviewed_but_lower_confidence_until_direct_summary_check",
                },
            ],
        },
        "jue_wiki": {
            "status": "ok",
            "pages": [{"page_id": "kis.symbol.005930", "content": marker * 200}],
        },
        "output_schema": {"schema": marker * 200},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=12_000,
    )

    assert prompt_budget_error(prompt) == ""
    coverage = prompt["jue_wiki_requested_symbol_coverage"]
    assert coverage["missing_summary_symbols"] == ["000660"]
    assert coverage["prompt_omitted_symbols"] == ["277810"]
    assert marker not in json.dumps(coverage, ensure_ascii=False)


def test_compact_jue_wiki_prompt_keeps_requested_symbol_guidance_metadata() -> None:
    compact = compact_jue_wiki_prompt(
        {
            "status": "ok",
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "summary": "실적 회복 전에는 눌림 대기",
                    "usage_guidance": {
                        "risk_posture": "patient_waiting_entry",
                        "required_cross_checks": [
                            "live_price_location",
                            "valuation_discount",
                        ],
                        "max_confidence_without_cross_check": 0.55,
                    },
                    "usage_guidance_effectiveness": {
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "patient_waiting_entry"
                                ),
                                "status": "active",
                                "sample_count": 7,
                            }
                        ]
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "valuation_stale_gt_30d",
                            "status": "degraded",
                            "sample_count": 4,
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
    assert summary["usage_guidance"]["risk_posture"] == "patient_waiting_entry"
    assert summary["usage_guidance"]["max_confidence_without_cross_check"] == 0.55
    assert summary["usage_guidance_effectiveness"]["metrics"][0]["status"] == "active"
    assert summary["quality_warning_effectiveness"][0]["warning"] == (
        "valuation_stale_gt_30d"
    )
    assert summary["quality_warning_effectiveness_statuses"] == ["degraded"]


def test_finalize_prompt_budget_keeps_requested_symbol_degraded_summaries() -> None:
    marker = "KIS_REQUESTED_SYMBOL_DEGRADED_BLOAT "
    prompt = {
        "task": "KIS manager degraded requested summary budget test",
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "full",
            "degraded_summary_count": 1,
            "degraded_summary_symbols": ["005930", marker * 20],
            "degraded_summary_reasons": [
                {
                    "symbol": "005930",
                    "freshness": "stale",
                    "quality_status": "degraded",
                    "quality_warnings": ["valuation_stale_gt_30d", marker * 20],
                }
            ],
            "required_adjustments": [
                {
                    "adjustment_type": "degraded_summary_cross_check",
                    "reason": marker * 20,
                    "symbols": ["005930"],
                    "resolution": "cross_check_live_research_and_lower_confidence_until_refreshed",
                }
            ],
        },
        "jue_wiki": {
            "status": "ok",
            "pages": [{"page_id": "kis.symbol.005930", "content": marker * 200}],
        },
        "output_schema": {"schema": marker * 200},
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
    assert coverage["degraded_summary_symbols"] == ["005930"]
    assert coverage["degraded_summary_reasons"] == [
        {
            "symbol": "005930",
            "freshness": "stale",
            "quality_status": "weak",
            "quality_warnings": ["valuation_stale_gt_30d"],
        }
    ]
    assert marker not in json.dumps(coverage, ensure_ascii=False)


def test_finalize_prompt_budget_preserves_memory_card_quality_contract() -> None:
    marker = "KIS_MEMORY_CARD_QUALITY_BLOAT "
    prompt = {
        "task": "KIS manager memory card quality budget test",
        "jue_wiki_memory_card_quality": {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "summary": {
                "version": "jue_wiki_memory_card_quality_v1",
                "status_counts": {"weak": 1, "strong": 2},
                "weak_symbols": ["005930", marker * 20],
                "rows": [
                    {
                        "symbol": "005930",
                        "quality": "weak",
                        "reason": marker * 25,
                    }
                ],
            },
            "action_plan": {
                "status": "active",
                "required_action": "cross_check_live_research_before_high_confidence",
                "symbols": ["005930", marker * 20],
                "reason": marker * 25,
            },
        },
        "jue_wiki": {
            "status": "ok",
            "pages": [{"page_id": "kis.symbol.005930", "content": marker * 200}],
        },
        "research_spine": {"packets": [{"summary": marker * 80} for _ in range(12)]},
        "output_schema": {"schema": marker * 200},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=12_000,
    )

    assert prompt_budget_error(prompt) == ""
    quality = prompt["jue_wiki_memory_card_quality"]
    assert quality["summary"]["weak_symbols"] == ["005930"]
    assert quality["action_plan"]["status"] == "active"
    assert quality["action_plan"]["symbols"] == ["005930"]
    assert marker not in json.dumps(quality, ensure_ascii=False)


def test_kis_memory_card_quality_compaction_preserves_missing_field_plan() -> None:
    compact = compact_jue_wiki_memory_card_quality_prompt(
        {
            "version": "jue_wiki_memory_card_quality_input_v1",
            "status": "active",
            "summary": {
                "version": "jue_wiki_memory_card_quality_v1",
                "status": "active",
                "status_counts": {"weak": 1},
                "weak_symbols": ["005930"],
                "missing_field_counts": {"durable_facts": 1, "lessons": 1},
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts", "lessons"],
                    }
                ],
                "rows": [
                    {
                        "symbol": "005930",
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
                "symbols": ["005930"],
                "missing_fields_by_symbol": [
                    {
                        "symbol": "005930",
                        "status": "weak",
                        "missing_fields": ["durable_facts", "lessons"],
                    }
                ],
                "required_checks": [
                    "refresh_durable_facts_from_reports_fundamentals_and_market_context",
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
            "symbol": "005930",
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
            "symbol": "005930",
            "status": "weak",
            "missing_fields": ["durable_facts", "lessons"],
        }
    ]
    assert compact["action_plan"]["required_checks"] == [
        "refresh_durable_facts_from_reports_fundamentals_and_market_context",
        "review_block_history_and_reflections_for_lessons",
    ]


def test_finalize_prompt_budget_preserves_jue_wiki_repair_pressure_plan() -> None:
    marker = "KIS_REPAIR_PRESSURE_BLOAT "
    prompt = {
        "task": "KIS manager repair pressure budget test",
        "jue_wiki_repair_contract": {
            "version": "jue_wiki_repair_contract_v1",
            "status": "active",
            "repair_priority_count": 12,
            "top_priority_count": 6,
            "omitted_priority_count": 6,
            "priority_type_counts": {
                "repair_queue": 2,
                "requested_symbol_coverage": 10,
            },
            "top_priority_type_counts": {
                "repair_queue": 2,
                "requested_symbol_coverage": 4,
            },
            "omitted_priority_type_counts": {"requested_symbol_coverage": 6},
            "repair_pressure_action_plan": {
                "status": "compressed",
                "total_priority_count": 12,
                "top_priority_count": 6,
                "omitted_priority_count": 6,
                "omitted_priority_type_counts": {"requested_symbol_coverage": 6},
                "required_response": (
                    "treat top_priorities as representative, not exhaustive; mention "
                    "omitted repair pressure when confidence, horizon, or sizing depends "
                    "on stale wiki coverage"
                ),
            },
            "top_priorities": [
                {
                    "page_id": "kis.research.repair_queue",
                    "priority_type": "repair_queue",
                    "source_id": "repair:financials:kis:005930",
                    "symbols": ["005930"],
                    "repair_action": marker * 20,
                }
            ],
            "action_batches": [
                {
                    "scope": "kis",
                    "action_type": "refresh_symbol_fundamentals",
                    "count": 13,
                    "symbols": ["033100", "033270", "051370", "081180"],
                    "warnings": ["financial_metrics_sparse"],
                    "recommended_actions": [
                        "refresh_stale_valuation_and_rewrite_page_evidence"
                    ],
                    "priority_types": ["repair_queue"],
                }
            ],
        },
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {"page_id": f"kis.symbol.{idx:06d}", "content": marker * 80}
                for idx in range(40)
            ],
        },
        "research_spine": {
            "packets": [
                {"symbol": f"{idx:06d}", "summary": marker * 80}
                for idx in range(30)
            ]
        },
        "daily_discovery": {
            "block_candidates": [
                {"symbol": f"{idx:06d}", "why": marker * 50}
                for idx in range(30)
            ]
        },
        "output_schema": {"schema": marker * 200},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=20_000,
        warn_chars=25_000,
        max_chars=30_000,
    )

    assert prompt_budget_error(prompt) == ""
    contract = prompt["jue_wiki_repair_contract"]
    assert contract["repair_priority_count"] == 12
    assert contract["omitted_priority_count"] == 6
    assert contract["repair_pressure_action_plan"]["status"] == "compressed"
    assert contract["repair_pressure_action_plan"]["omitted_priority_count"] == 6
    assert contract["top_priorities"][0]["source_id"] == "repair:financials:kis:005930"
    assert contract["action_batches"][0]["action_type"] == "refresh_symbol_fundamentals"
    assert contract["action_batches"][0]["count"] == 13
    assert marker not in json.dumps(contract, ensure_ascii=False)


def test_compact_jue_wiki_repair_contract_preserves_memory_card_gap_summary() -> None:
    compact = compact_jue_wiki_repair_contract_prompt(
        {
            "version": "jue_wiki_repair_contract_v1",
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
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
                            "instruction": (
                                "resolve_priority_memory_card_quality_gap_first"
                            ),
                        },
                        "missing_field_counts": {
                            "durable_facts": 3,
                            "lessons": 1,
                    },
                    "missing_field_missed_counts": {
                        "durable_facts": 2,
                        "lessons": 1,
                    },
                    "required_check_counts": {
                        "refresh_durable_facts": 3,
                        "inspect_block_lessons": 1,
                    },
                    "required_check_missed_counts": {
                        "refresh_durable_facts": 2,
                        "inspect_block_lessons": 1,
                    },
                    "top_missing_fields": [
                        {
                            "field": "durable_facts",
                            "sample_count": 3,
                            "missed_count": 2,
                        },
                        {"field": "lessons", "sample_count": 1, "missed_count": 1},
                    ],
                    "top_required_checks": [
                        {
                            "check": "refresh_durable_facts",
                            "sample_count": 3,
                            "missed_count": 2,
                        },
                        {
                            "check": "inspect_block_lessons",
                            "sample_count": 1,
                            "missed_count": 1,
                        },
                    ],
                },
            },
        },
        list_limit=2,
        string_limit=100,
    )

    gap_summary = compact["repair_loop_effectiveness"][
        "memory_card_quality_gap_summary"
    ]
    assert gap_summary["priority_missing_fields"] == ["durable_facts"]
    assert gap_summary["priority_required_checks"] == ["refresh_durable_facts"]
    assert gap_summary["priority_focus"] == {
        "missing_field": "durable_facts",
        "missing_field_sample_count": 3,
        "missing_field_missed_count": 2,
        "required_check": "refresh_durable_facts",
        "required_check_sample_count": 3,
        "required_check_missed_count": 2,
        "instruction": "resolve_priority_memory_card_quality_gap_first",
    }
    assert gap_summary["top_missing_fields"] == [
        {"field": "durable_facts", "sample_count": 3, "missed_count": 2},
        {"field": "lessons", "sample_count": 1, "missed_count": 1},
    ]
    assert gap_summary["top_required_checks"] == [
        {
            "check": "refresh_durable_facts",
            "sample_count": 3,
            "missed_count": 2,
        },
        {"check": "inspect_block_lessons", "sample_count": 1, "missed_count": 1},
    ]


def test_compact_jue_wiki_repair_contract_preserves_action_batches() -> None:
    compact = compact_jue_wiki_repair_contract_prompt(
        {
            "version": "jue_wiki_repair_contract_v1",
            "status": "active",
            "repair_priority_count": 21,
            "top_priority_count": 6,
            "omitted_priority_count": 15,
            "repair_pressure_action_plan": {
                "status": "compressed",
                "total_priority_count": 21,
                "top_priority_count": 6,
                "omitted_priority_count": 15,
                "action_batch_count": 2,
                "action_batch_total_count": 18,
                "required_response": (
                    "treat action_batches as grouped repair work before sizing"
                ),
            },
            "action_batches": [
                {
                    "scope": "kis",
                    "action_type": "refresh_symbol_fundamentals",
                    "count": 13,
                    "symbols": ["033100", "033270", "051370", "081180"],
                    "warnings": [
                        "financial_metrics_sparse",
                        "financial_rows_rejected_empty",
                    ],
                    "warning_counts": {
                        "financial_metrics_sparse": 8,
                        "financial_rows_rejected_empty": 5,
                    },
                    "max_severity_score": 91.5,
                    "recommended_actions": [
                        "refresh_stale_valuation_and_rewrite_page_evidence"
                    ],
                    "priority_types": ["repair_queue"],
                },
                {
                    "scope": "kis",
                    "action_type": "repair_quality_warning_effectiveness",
                    "count": 5,
                    "symbols": ["245450", "249420"],
                    "warnings": ["financials_missing"],
                    "warning_counts": {"financials_missing": 5},
                    "max_severity_score": 72.0,
                    "recommended_actions": [
                        "cross_check_warning_bearing_evidence_and_rewrite_page_evidence"
                    ],
                    "priority_types": ["repair_queue"],
                },
            ],
        },
        list_limit=2,
        string_limit=80,
    )

    assert compact["repair_pressure_action_plan"]["action_batch_count"] == 2
    assert compact["repair_pressure_action_plan"]["action_batch_total_count"] == 18
    assert compact["repair_pressure_action_plan"]["action_batch_type_counts"] == {
        "refresh_symbol_fundamentals": 13,
        "repair_quality_warning_effectiveness": 5,
    }
    assert compact["repair_pressure_action_plan"]["action_batch_scopes"] == ["kis"]
    assert compact["repair_pressure_action_plan"]["action_batch_warning_counts"] == {
        "financial_metrics_sparse": 8,
        "financial_rows_rejected_empty": 5,
        "financials_missing": 5,
    }
    assert (
        compact["repair_pressure_action_plan"]["action_batch_max_severity_score"]
        == 91.5
    )
    assert compact["action_batches"] == [
        {
            "scope": "kis",
            "action_type": "refresh_symbol_fundamentals",
            "count": 13,
            "symbols": ["033100", "033270", "051370", "081180"],
            "warnings": [
                "financial_metrics_sparse",
                "financial_rows_rejected_empty",
            ],
            "warning_counts": {
                "financial_metrics_sparse": 8,
                "financial_rows_rejected_empty": 5,
            },
            "max_severity_score": 91.5,
            "recommended_actions": [
                "refresh_stale_valuation_and_rewrite_page_evidence"
            ],
            "priority_types": ["repair_queue"],
        },
        {
            "scope": "kis",
            "action_type": "repair_quality_warning_effectiveness",
            "count": 5,
            "symbols": ["245450", "249420"],
            "warnings": ["financials_missing"],
            "warning_counts": {"financials_missing": 5},
            "max_severity_score": 72.0,
            "recommended_actions": [
                "cross_check_warning_bearing_evidence_and_rewrite_page_evidence"
            ],
            "priority_types": ["repair_queue"],
        },
    ]


def test_compact_jue_wiki_repair_contract_preserves_upstream_action_batch_omissions() -> None:
    action_batches = [
        {
            "scope": "kis",
            "action_type": f"refresh_symbol_slice_{idx:02d}",
            "count": 1,
            "symbols": [f"{idx:06d}"],
            "max_severity_score": 80.0 - idx,
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


def test_compact_jue_wiki_repair_contract_rebuilds_legacy_gap_priorities() -> None:
    compact = compact_jue_wiki_repair_contract_prompt(
        {
            "version": "jue_wiki_repair_contract_v1",
            "status": "repair_required",
            "repair_loop_effectiveness": {
                "status": "repair_required",
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
                },
            },
        },
        list_limit=4,
        string_limit=100,
    )

    gap_summary = compact["repair_loop_effectiveness"][
        "memory_card_quality_gap_summary"
    ]
    assert gap_summary["priority_missing_fields"] == ["durable_facts"]
    assert gap_summary["priority_required_checks"] == ["refresh_durable_facts"]


def test_kis_manager_prompt_storage_emergency_keeps_research_and_authority_snapshot() -> None:
    marker = "KIS_STORAGE_CRITICAL_CONTEXT"
    prompt = {
        "task": "Manage KIS blocks" + marker * 500,
        "status": "ok",
        "research_spine": {
            "version": "research_spine_v1",
            "packets": [
                {
                    "symbol": "005930",
                    "thesis": "저점 대기 블록 후보",
                    "evidence": marker * 300,
                }
            ],
            "buckets": {"pre_surge": [{"symbol": "005930", "why": marker * 200}]},
        },
        "daily_discovery": {
            "status": "ok",
            "block_candidates": [{"symbol": "005930", "why": marker * 200}],
        },
        "quotes": [{"symbol": "005930", "price": 72000, "raw": marker * 200}],
        "live_authority": {
            "status": "ok",
            "live_grade": "restricted",
            "validation_gate": {"status": "validation_probe", "reason": marker * 300},
        },
        "validation_repair": {
            "version": "validation_repair_prompt_v1",
            "scope": "kis",
            "status": "risk_repair",
            "repair_item_count": 1,
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
        },
        "validation_repair_response_contract": {
            "version": "kis_validation_repair_response_contract_v1",
            "core_rule": "Validation repair is not blanket hold.",
            "blanket_hold_allowed": False,
        },
        "aggressive_opportunities": {
            "status": "ok",
            "candidate_count": 3,
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "aggressive_score": 82,
                    "preferred_action": "scout_or_waiting_block",
                    "reasons": [marker * 20],
                }
            ],
        },
        "jue_wiki_application": {
            "status": "ok",
            "selected_page_ids": ["kis.symbol.005930"],
            "selection_audit": {
                "selected_page_count": 2,
                "reason_counts": {
                    "scope_match:kis": 2,
                    "operational_memory:manager_contract_recovery": 1,
                },
                "penalty_counts": {"freshness:stale": 1},
                "top_pages": [
                    {
                        "page_id": "kis.ops.manager_runs",
                        "rank": 1,
                        "score": 142.5,
                        "selection_reasons": [
                            "scope_match:kis",
                            "operational_memory:manager_contract_recovery",
                        ],
                    }
                ],
            },
        },
        "jue_wiki_requested_symbol_coverage": {
            "version": "jue_wiki_requested_symbol_coverage_v1",
            "status": "partial",
            "unsummarized_symbols": ["000660", "277810"],
            "required_action": "live cross-check required before confidence",
        },
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "summary": "삼성전자 장기 메모리 " + marker * 40,
                }
            ],
        },
        "investment_memory": {
            "status": "ok",
            "scoped_memory": {
                "core": [
                    {
                        "summary": "눌림 대기 블록은 수량 의도를 보존한다 " + marker * 40
                    }
                ],
            },
            "active_policies": [
                {"policy_id": "prefer_waiting_probe", "summary": marker * 20}
            ],
        },
        "hold_decision": {"summary": "대기 블록 중심으로 검토"},
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 80_000},
    }

    compact = compact_manager_storage_payload(
        prompt,
        limit=1_500,
        label="kis_manager_prompt",
    )

    assert compact["_storage_compaction"]["emergency"] is True
    assert compact["research_spine"]["packets"][0]["symbol"] == "005930"
    assert compact["daily_discovery"]["block_candidates"][0]["symbol"] == "005930"
    assert compact["quotes"][0]["symbol"] == "005930"
    assert compact["live_authority"]["validation_gate"]["status"] == "validation_probe"
    assert compact["validation_repair"]["scope"] == "kis"
    assert compact["validation_repair"]["discipline_ids"] == ["cost_simulation"]
    assert compact["validation_repair"]["hard_filter"] is False
    assert compact["validation_repair_response_contract"][
        "blanket_hold_allowed"
    ] is False
    assert compact["aggressive_opportunities"]["candidates"][0]["symbol"] == "005930"
    assert compact["jue_wiki_application"]["selected_page_ids"] == ["kis.symbol.005930"]
    assert compact["jue_wiki_application"]["selection_audit"]["reason_counts"][
        "operational_memory:manager_contract_recovery"
    ] == 1
    assert compact["jue_wiki_application"]["selection_audit"]["top_pages"][0][
        "page_id"
    ] == "kis.ops.manager_runs"
    assert compact["jue_wiki_requested_symbol_coverage"]["status"] == "partial"
    assert compact["jue_wiki_requested_symbol_coverage"]["unsummarized_symbols"] == [
        "000660",
        "277810",
    ]
    assert compact["jue_wiki"]["pages"][0]["page_id"] == "kis.symbol.005930"
    assert compact["investment_memory"]["status"] == "ok"


def test_compact_jue_wiki_application_prompt_keeps_trust_profile_in_emergency() -> None:
    compact = kis_manager_prompt_module.compact_jue_wiki_application_prompt(
        {
            "status": "ok",
            "prompt_mode": "assist",
            "selection_run_id": "selection:kis-trust",
            "selected_page_ids": ["kis.symbol.005930"],
            "trust_profile": {
                "trust_level": "medium",
                "authority": "supporting_evidence",
                "decision_use": (
                    "use selected wiki pages as supporting evidence alongside "
                    "live quotes and account state"
                ),
                "usage_contract": {
                    "decision_role": "supporting_evidence",
                    "requires_live_cross_check": True,
                    "standalone_trade_authority": False,
                    "required_cross_checks": [
                        "live_quote",
                        "account_state",
                        "risk_gate",
                    ],
                    "raw_blob": "DROP_ME",
                },
                "raw_debug": "DROP_ME",
            },
        },
        list_limit=2,
        string_limit=120,
        emergency=True,
    )

    assert compact["status"] == "ok"
    assert compact["prompt_mode"] == "assist"
    assert compact["selection_run_id"] == "selection:kis-trust"
    assert compact["selected_page_ids"] == ["kis.symbol.005930"]
    assert compact["trust_profile"] == {
        "trust_level": "medium",
        "authority": "supporting_evidence",
        "decision_use": (
            "use selected wiki pages as supporting evidence alongside live "
            "quotes and account state"
        ),
        "usage_contract": {
            "decision_role": "supporting_evidence",
            "requires_live_cross_check": True,
            "standalone_trade_authority": False,
            "required_cross_checks": [
                "live_quote",
                "account_state",
            ],
        },
    }
    assert "DROP_ME" not in json.dumps(compact, ensure_ascii=False)


def test_kis_manager_prompt_storage_preserves_full_decision_inputs_and_opportunities() -> None:
    marker = "KIS_STORAGE_CRITICAL_CONTEXT"
    prompt = {
        "decision_inputs": [
            "account",
            "blocks",
            "quotes",
            "execution_gate",
            "strategy",
            "research_spine",
            "investment_memory",
            "daily_discovery",
            "aggressive_opportunities",
            "jue_wiki",
            "market_pulse",
            "opportunity_research_brief",
        ],
        "blocks": [
            {
                "block_id": f"blk-{idx}",
                "symbol": f"{idx:06d}",
                "thesis": marker * 30,
                "risk_note": marker * 30,
            }
            for idx in range(35)
        ],
        "investment_memory": {
            "status": "ok",
            "items": [{"lesson": marker * 30} for _ in range(24)],
        },
        "research_spine": {
            "status": "ok",
            "packets": [
                {"symbol": "005930", "name": "삼성전자", "summary": marker * 10},
                {"symbol": "000660", "name": "SK하이닉스", "summary": marker * 10},
            ],
        },
        "daily_discovery": {
            "status": "ok",
            "block_candidates": [
                {"symbol": "005930", "name": "삼성전자", "why": marker * 10}
            ],
            "pre_surge_candidates": [
                {"symbol": "000660", "name": "SK하이닉스", "why": marker * 10}
            ],
        },
        "aggressive_opportunities": {
            "status": "ok",
            "candidates": [
                {"symbol": "005930", "name": "삼성전자", "reason": marker * 10}
            ],
        },
        "market_pulse": {
            "status": "ok",
            "items": [{"symbol": "005930", "summary": marker * 10}],
        },
        "opportunity_research_brief": {
            "status": "ok",
            "block_candidates": [{"symbol": "005930"}],
        },
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 140_000},
    }

    compact = compact_manager_storage_payload(
        prompt,
        limit=6_000,
        label="kis_manager_prompt",
    )

    assert compact["_storage_compaction"]["emergency"] is False
    assert compact["decision_inputs"] == prompt["decision_inputs"]
    for section in (
        "investment_memory",
        "research_spine",
        "daily_discovery",
        "aggressive_opportunities",
        "market_pulse",
        "opportunity_research_brief",
    ):
        assert compact[section]["status"] != "omitted_for_prompt_budget"
    assert compact["investment_memory"]["items"][0]["lesson"]
    assert compact["research_spine"]["packets"][0]["symbol"] == "005930"
    assert compact["daily_discovery"]["block_candidates"][0]["symbol"] == "005930"
    assert compact["aggressive_opportunities"]["candidates"][0]["symbol"] == "005930"


def test_finalize_kis_prompt_budget_keeps_critical_opportunity_summaries() -> None:
    marker = "KIS_CRITICAL_OPPORTUNITY_CONTEXT"
    prompt = {
        "blocks": [
            {
                "block_id": f"blk-{idx}",
                "symbol": f"{idx:06d}",
                "status": "open",
                "thesis": marker * 120,
                "risk_note": marker * 120,
            }
            for idx in range(60)
        ],
        "jue_wiki": {
            "status": "ok",
            "pages": [
                {
                    "page_id": f"kis.symbol.{idx:06d}",
                    "content": marker * 120,
                }
                for idx in range(30)
            ],
        },
        "research_spine": {
            "status": "ok",
            "packets": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "quality": {"decision_use": "candidate"},
                    "summary": "저점 대기 블록 후보",
                    "evidence": marker * 60,
                }
            ],
        },
        "daily_discovery": {
            "status": "ok",
            "block_candidates": [
                {"symbol": "005930", "name": "삼성전자", "why": marker * 60}
            ],
            "pre_surge_candidates": [
                {"symbol": "000660", "name": "SK하이닉스", "why": marker * 60}
            ],
        },
        "aggressive_opportunities": {
            "status": "ok",
            "candidates": [
                {
                    "symbol": "005930",
                    "preferred_action": "wait_for_price",
                    "reasons": [marker * 20],
                }
            ],
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=35_000,
        warn_chars=45_000,
        max_chars=55_000,
    )

    for section in (
        "research_spine",
        "daily_discovery",
        "aggressive_opportunities",
    ):
        assert prompt[section]["status"] != "omitted_for_prompt_budget"
    assert prompt["research_spine"]["packets"][0]["symbol"] == "005930"
    assert prompt["daily_discovery"]["block_candidates"][0]["symbol"] == "005930"
    assert prompt["aggressive_opportunities"]["candidates"][0]["symbol"] == "005930"
    assert prompt_budget_error(prompt) == ""


def test_compact_validation_repair_prompt_extracts_kis_backlog_and_constraints() -> None:
    compact = compact_validation_repair_prompt(
        {
            "validation_repair_backlog": {
                "status": "active_caution",
                "total_item_count": 2,
                "items": [
                    {
                        "policy_id": "validation.kis.cost",
                        "repair_action_id": "cost_evidence_repair",
                        "discipline_id": "cost_simulation",
                        "automation_hook": "refresh_trading_validation",
                        "pass_required_evidence": ["positive_net_edge"],
                        "ignored_raw": "drop",
                    }
                ],
            },
            "block_design_constraints": {
                "count": 1,
                "items": [
                    {
                        "policy_id": "validation.kis.cost",
                        "discipline_id": "cost_simulation",
                        "entry_bias": "waiting_probe",
                        "risk_budget_multiplier": 0.25,
                        "risk_note": "증액은 보류 " * 100,
                    }
                ],
            },
        },
        scope="kis",
        compact_value=lambda value, **_: value,
    )

    assert compact["version"] == "validation_repair_prompt_v1"
    assert compact["scope"] == "kis"
    assert compact["status"] == "active_caution"
    assert compact["repair_item_count"] == 2
    assert compact["constraint_count"] == 1
    assert compact["repair_backlog"][0]["discipline_id"] == "cost_simulation"
    assert compact["repair_backlog"][0]["pass_required_evidence"] == [
        "positive_net_edge"
    ]
    assert "ignored_raw" not in compact["repair_backlog"][0]
    assert compact["block_design_constraints"][0]["entry_bias"] == "waiting_probe"
    assert "not strategy hard filters" in compact["instruction"]


def test_compact_validation_repair_prompt_preserves_period_memory_constraints() -> None:
    compact = compact_validation_repair_prompt(
        {
            "block_design_constraints": {
                "count": 1,
                "items": [
                    {
                        "policy_id": "period_memory_coverage.gap_overridden",
                        "venue": "kis",
                        "period_memory_status": "gap_overridden",
                        "period_memory_gap_count": 4,
                        "period_memory_override_count": 4,
                        "period_memory_contract_gap_count": 2,
                        "period_memory_missing_metadata": [
                            "period_memory_override_reason"
                        ],
                        "period_memory_repair_actions": [
                            "add_period_memory_override_reason_before_scaling"
                        ],
                        "metadata_contract_audit_resolutions": [
                            "kept micro probe until override reason is restored"
                        ],
                        "period_memory_repair_quality": "successful_repair",
                        "entry_bias": "cross_checked_probe_or_wait_on_memory_gap",
                        "sizing_policy": "reduce_without_fresh_period_review_or_replay",
                        "required_evidence": [
                            "period_memory_coverage_gap",
                            "period_memory_override_reason",
                        ],
                        "required_checks": ["require_period_memory_override_audit"],
                    }
                ],
            }
        },
        scope="kis",
        compact_value=lambda value, **_: value,
    )

    constraint = compact["block_design_constraints"][0]
    assert constraint["policy_id"] == "period_memory_coverage.gap_overridden"
    assert constraint["venue"] == "kis"
    assert constraint["period_memory_status"] == "gap_overridden"
    assert constraint["period_memory_gap_count"] == 4
    assert constraint["period_memory_override_count"] == 4
    assert constraint["period_memory_contract_gap_count"] == 2
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
    assert constraint["period_memory_repair_quality"] == "successful_repair"
    assert constraint["entry_bias"] == "cross_checked_probe_or_wait_on_memory_gap"
    assert constraint["sizing_policy"] == "reduce_without_fresh_period_review_or_replay"
    assert "period_memory_coverage_gap" in constraint["required_evidence"]
    assert "require_period_memory_override_audit" in constraint["required_checks"]


def test_validation_repair_action_metadata_preserves_kis_raw_sections() -> None:
    metadata = validation_repair_action_metadata(
        {
            "scope": "kis",
            "status": "active",
            "repair_item_count": 1,
            "constraint_count": 1,
            "repair_backlog": [
                {
                    "policy_id": "validation.kis.cost",
                    "repair_action_id": "cost_evidence_repair",
                    "discipline_id": "cost_simulation",
                    "required_evidence": ["fee_slippage_report"],
                    "required_checks": ["positive_net_edge"],
                    "last_repair_status": "queued",
                }
            ],
            "block_design_constraints": [
                {
                    "policy_id": "validation.kis.cost",
                    "discipline_id": "cost_simulation",
                    "entry_bias": "waiting_probe",
                    "scale_blocker": "verified_edge_sample_cap",
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.5,
                    "min_reward_risk": 2.0,
                    "max_stop_risk_pct": 0.03,
                    "scale_up_blocked": "required",
                    "live_shadow_required": True,
                }
            ],
        }
    )

    repair = metadata["validation_repair"]
    assert repair["scope"] == "kis"
    assert repair["discipline_ids"] == ["cost_simulation"]
    assert repair["entry_biases"] == ["waiting_probe"]
    assert repair["scale_blockers"] == ["verified_edge_sample_cap"]
    assert repair["risk_budget_multiplier"] == 0.25
    assert repair["max_budget_multiplier"] == 0.5
    assert repair["min_reward_risk"] == 2.0
    assert repair["max_stop_risk_pct"] == 0.03
    assert repair["scale_up_blocked"] is True
    assert repair["live_shadow_required"] is True
    assert repair["repair_backlog"][0]["policy_id"] == "validation.kis.cost"
    assert repair["block_design_constraints"][0]["entry_bias"] == "waiting_probe"
    assert repair["hard_filter"] is False


def test_validation_repair_action_metadata_preserves_kis_period_memory_summary() -> None:
    metadata = validation_repair_action_metadata(
        {
            "scope": "kis",
            "status": "active",
            "constraint_count": 1,
            "block_design_constraints": [
                {
                    "policy_id": "period_memory_coverage.gap_overridden",
                    "period_memory_status": "gap_overridden",
                    "period_memory_gap_count": 4,
                    "period_memory_override_count": 4,
                    "period_memory_contract_gap_count": 2,
                    "period_memory_missing_metadata": [
                        "period_memory_override_reason"
                    ],
                    "period_memory_repair_actions": [
                        "add_period_memory_override_reason_before_scaling"
                    ],
                    "metadata_contract_audit_resolutions": [
                        "kept micro probe until override reason is restored"
                    ],
                    "period_memory_repair_quality": "successful_repair",
                    "entry_bias": "cross_checked_probe_or_wait_on_memory_gap",
                    "required_checks": ["require_period_memory_override_audit"],
                }
            ],
        }
    )

    repair = metadata["validation_repair"]
    assert repair["period_memory_statuses"] == ["gap_overridden"]
    assert repair["period_memory_gap_count"] == 4
    assert repair["period_memory_override_count"] == 4
    assert repair["period_memory_contract_gap_count"] == 2
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
    assert repair["period_memory_repair_qualities"] == ["successful_repair"]
    assert "period_memory_coverage.gap_overridden" in repair["policy_ids"]
    assert "require_period_memory_override_audit" in repair["required_checks"]
    assert repair["block_design_constraints"][0]["period_memory_status"] == (
        "gap_overridden"
    )


def test_validation_evidence_plan_and_korean_note_from_repair() -> None:
    repair = {
        "status": "active_caution",
        "discipline_ids": ["walk_forward_analysis", "out_of_sample"],
        "entry_biases": ["waiting_probe"],
        "required_evidence": ["wfa_result"],
        "required_checks": ["oos_pass"],
        "pass_collection_hooks": ["run_walk_forward"],
        "scale_up_blocked": True,
        "live_shadow_required": True,
        "period_memory_repair_qualities": ["successful_repair"],
    }

    plan = validation_evidence_plan_from_repair(repair)

    assert plan["status"] == "repair_required"
    assert plan["required_dimensions"] == ["walk_forward", "out_of_sample"]
    assert plan["missing_dimensions"] == ["walk_forward", "out_of_sample"]
    assert plan["required_evidence"] == ["wfa_result"]
    assert plan["required_checks"] == ["oos_pass"]
    assert plan["pass_collection_hooks"] == ["run_walk_forward"]
    assert plan["scale_up_blocked"] is True
    assert plan["live_shadow_required"] is True
    assert plan["period_memory_repair_qualities"] == ["successful_repair"]
    assert validation_repair_note(repair) == (
        "19검증 반영 - 검증항목=walk_forward_analysis,out_of_sample / "
        "진입성향=waiting_probe / 메모리수리=successful_repair"
    )


def test_validation_repair_discipline_tokens_collects_nested_rows() -> None:
    assert validation_repair_discipline_tokens(
        {
            "discipline_ids": ["Walk Forward Analysis"],
            "repair_backlog": [{"discipline_id": "out/of sample"}],
            "block_design_constraints": [{"discipline_id": "live shadow"}],
        }
    ) == ["walk_forward_analysis", "out_of_sample", "live_shadow"]


def test_sanitize_kis_hold_decision_filters_symbols_and_adds_idle_summary() -> None:
    decision = sanitize_kis_hold_decision(
        {
            "reasons": ["관망 " * 120],
            "watch_symbols": ["005930", "bad", "005930", "000660"],
            "long_watch_symbols": ["035420", "not-symbol"],
            "next_triggers": [
                {
                    "symbol": "005930",
                    "condition": "72000 아래 눌림 확인 " * 30,
                    "price": "72,000",
                    "horizon": "중기",
                    "reason": "밸류 부담 완화 " * 30,
                },
                {"symbol": "bad", "condition": "drop"},
            ],
            "horizon_notes": {
                "mid": ["중기 유지 " * 120],
                "long": ["장기 후보"],
            },
            "missed_upside_reviews": [{"symbol": "078600"}, "drop"],
        },
        action_count=0,
    )

    assert decision["summary"] == "이번 KIS 매니저 실행은 새 블록 없이 관망했습니다."
    assert len(decision["reasons"][0]) <= 500
    assert decision["watch_symbols"] == ["005930", "000660"]
    assert decision["long_watch_symbols"] == ["035420"]
    trigger = decision["next_triggers"][0]
    assert trigger["symbol"] == "005930"
    assert trigger["price"] == 72000.0
    assert trigger["horizon"] == "mid"
    assert len(trigger["condition"]) <= 500
    assert decision["horizon_notes"]["mid"]
    assert decision["missed_upside_reviews"] == [{"symbol": "078600"}]
    assert decision["action_count"] == 0


def test_sanitize_creative_hypotheses_normalizes_blocks_and_caps_items() -> None:
    rows = [
        {
            "hypothesis_id": "rot-1",
            "hypothesis_type": "second rank",
            "title": "전자장비 2등주 눌림 대기",
            "summary": "순환매 후보 " * 120,
            "symbols": ["078600", "bad"],
            "sector": "전자장비",
            "horizon": "중기",
            "decision": "create-wait-block",
            "confidence": "1.5",
            "evidence": ["거래대금 증가 " * 80],
            "risks": ["실적 확인 필요"],
            "invalidation": "섹터 수급 이탈 " * 80,
            "proposed_block": {
                "symbol": "078600",
                "qty": "3",
                "entry_style": "pullback",
                "entry_trigger_price": "12,300",
                "entry_trigger_operator": "below",
                "target_price": "14,500",
                "stop_price": "11,700",
                "horizon": "mid-term",
                "reason": "눌림 대기 " * 80,
            },
            "next_check": "다음 장중 점검",
        },
        {"summary": ""},
        {
            "title": "fallback",
            "hypothesis_type": "unknown-kind",
            "decision": "unknown-decision",
            "confidence": "-2",
        },
    ]

    compact = sanitize_creative_hypotheses(rows, max_items=2)

    assert len(compact) == 2
    first = compact[0]
    assert first["hypothesis_type"] == "second_rank"
    assert first["decision"] == "create_wait_block"
    assert first["confidence"] == 1.0
    assert first["symbols"] == ["078600"]
    assert len(first["summary"]) <= 900
    assert len(first["evidence"][0]) <= 500
    assert len(first["invalidation"]) <= 700
    block = first["proposed_block"]
    assert block["symbol"] == "078600"
    assert block["qty"] == 3
    assert block["entry_style"] == "wait_for_price"
    assert block["entry_trigger_price"] == 12300.0
    assert block["entry_trigger_operator"] == "lte"
    assert block["horizon"] == "mid"
    assert len(block["reason"]) <= 700
    assert compact[1]["hypothesis_type"] == "contrarian"
    assert compact[1]["decision"] == "watch"
    assert compact[1]["confidence"] == 0.0


def test_compact_prompt_block_keeps_live_block_fields_and_compacts_metadata() -> None:
    marker = "RAW-LIVE-AUTHORITY-MARKER"
    compact = compact_prompt_block(
        {
            "block_id": "kis_005930_mid_1",
            "symbol": "005930",
            "name": "삼성전자",
            "qty_initial": 3,
            "qty_open": 2,
            "entry_price": 72000,
            "target_price": 78000,
            "stop_price": 69000,
            "status": "open",
            "thesis": "중기 반등 가설 " * 80,
            "risk_note": "손절 기준 " * 80,
            "metadata": {
                "horizon": "mid",
                "entry_style": "wait_for_price",
                "entry_trigger_price": 71000,
                "jue_wiki_repair_pressure": "degraded page used as repair evidence",
                "jue_wiki_repair_resolution": (
                    "live quote and sizing cross-check reduced confidence"
                ),
                "jue_wiki_memory_card_quality": "thin card required live check",
                "jue_wiki_memory_card_cross_check": "checked quote and valuation",
                "jue_wiki_selection_resolution": (
                    "fresh_jue_wiki_context refreshed before size increase"
                ),
                "jue_wiki_freshness_cross_check": (
                    "selection_audit_resolution and live_cross_check completed"
                ),
                "jue_wiki_reference_basis": (
                    "jue_wiki_action_reference_gap.kis.missing 보정 근거"
                ),
                "jue_wiki_usage_contract_resolution": (
                    "위키는 단독 매매권한이 아니며 live_quote/account_state/risk_gate 교차확인 완료"
                ),
                "cost_feasibility": {"status": "pass", "raw": marker * 100},
                "policy_rule_impacts": [{"policy": "hold_mid", "raw": marker * 30}],
                "live_authority": {
                    "status": "ok",
                    "live_grade": "restricted",
                    "allow_scale_up": False,
                    "validation_gate_reason": marker * 20,
                    "lane_authority": {
                        "version": "lane_authority_v1",
                        "global_scale_up_allowed": False,
                        "execution_posture": "probe_allowed_scale_blocked",
                        "probe_lane_count": 1,
                        "probe_lane_names": ["mid"],
                        "scale_blocked_lane_count": 1,
                        "scale_blocked_lanes": ["mid"],
                        "probe_policy": "scale-up is blocked, probes are allowed",
                        "weak_lanes": ["mid", "long"],
                        "lane_actions": {
                            "mid": {
                                "action": "observe_or_waiting_entry",
                                "reason": marker * 20,
                                "risk_budget_passport": {
                                    "sample_confidence": "low",
                                    "raw": marker * 20,
                                },
                                "entry_quality_requirements": [
                                    "low_risk_pullback",
                                    "volume_confirmation",
                                ],
                            }
                        },
                    },
                    "remediation_plan": {"items": [{"raw": marker * 20}]},
                },
            },
        }
    )

    assert compact["symbol"] == "005930"
    assert compact["name"] == "삼성전자"
    assert compact["qty_open"] == 2
    assert len(compact["thesis"]) <= 260
    assert len(compact["risk_note"]) <= 260
    assert compact["metadata"]["horizon"] == "mid"
    assert compact["metadata"]["entry_style"] == "wait_for_price"
    assert compact["metadata"]["jue_wiki_repair_pressure"] == (
        "degraded page used as repair evidence"
    )
    assert compact["metadata"]["jue_wiki_repair_resolution"] == (
        "live quote and sizing cross-check reduced confidence"
    )
    assert compact["metadata"]["jue_wiki_memory_card_quality"] == (
        "thin card required live check"
    )
    assert compact["metadata"]["jue_wiki_memory_card_cross_check"] == (
        "checked quote and valuation"
    )
    assert compact["metadata"]["jue_wiki_selection_resolution"] == (
        "fresh_jue_wiki_context refreshed before size increase"
    )
    assert compact["metadata"]["jue_wiki_freshness_cross_check"] == (
        "selection_audit_resolution and live_cross_check completed"
    )
    assert compact["metadata"]["jue_wiki_reference_basis"] == (
        "jue_wiki_action_reference_gap.kis.missing 보정 근거"
    )
    assert compact["metadata"]["jue_wiki_usage_contract_resolution"] == (
        "위키는 단독 매매권한이 아니며 live_quote/account_state/risk_gate 교차확인 완료"
    )
    assert compact["metadata"]["cost_feasibility"]["status"] == "pass"
    assert "raw" not in compact["metadata"]["cost_feasibility"]
    live_authority = compact["metadata"]["live_authority"]
    assert live_authority["status"] == "ok"
    assert live_authority["live_grade"] == "restricted"
    assert live_authority["lane_authority"]["execution_posture"] == (
        "probe_allowed_scale_blocked"
    )
    assert live_authority["lane_authority"]["probe_lane_count"] == 1
    assert live_authority["lane_authority"]["probe_lane_names"] == ["mid"]
    assert live_authority["lane_authority"]["scale_blocked_lane_count"] == 1
    assert live_authority["lane_authority"]["scale_blocked_lanes"] == ["mid"]
    assert live_authority["lane_authority"]["weak_lanes"] == ["mid", "long"]
    assert live_authority["lane_authority"]["lane_actions"]["mid"]["action"] == (
        "observe_or_waiting_entry"
    )
    assert "remediation_plan" not in live_authority


def test_compact_manager_prompt_blocks_summarizes_inactive_zero_quantity_blocks() -> None:
    detailed, summary = compact_manager_prompt_blocks(
        [
            {
                "block_id": "live",
                "symbol": "005930",
                "name": "삼성전자",
                "qty_open": 1,
                "status": "open",
                "metadata": {"horizon": "mid"},
            },
            {
                "block_id": "closed-1",
                "symbol": "000660",
                "name": "SK하이닉스",
                "qty_open": 0,
                "status": "closed",
                "metadata": {"horizon": "short"},
                "updated_at": "2026-06-19T08:00:00+09:00",
            },
            {
                "block_id": "paused-1",
                "symbol": "035420",
                "name": "NAVER",
                "qty_open": 0,
                "status": "paused",
                "metadata": {"horizon": "long"},
            },
        ]
    )

    assert [row["block_id"] for row in detailed] == ["live"]
    assert summary["version"] == "block_backlog_summary_v1"
    assert summary["input_count"] == 3
    assert summary["detailed_count"] == 1
    assert summary["omitted_count"] == 2
    assert summary["omitted_by_status"] == {"closed": 1, "paused": 1}
    assert summary["samples"][0]["block_id"] == "closed-1"
    assert summary["samples"][0]["horizon"] == "short"


def test_compact_prompt_event_keeps_safe_payload_fields_only() -> None:
    compact = compact_prompt_event(
        {
            "id": 7,
            "event_type": "target_reached",
            "message": "목표 도달 " * 80,
            "payload": {
                "symbol": "005930",
                "side": "sell",
                "price": 81000,
                "qty": 2,
                "status": "filled",
                "reason": "익절 " * 80,
                "raw": "DROP-ME",
                "html": "<b>DROP</b>",
            },
            "created_at": "2026-06-20T09:10:00+09:00",
        }
    )

    assert compact["id"] == 7
    assert compact["event_type"] == "target_reached"
    assert len(compact["message"]) <= 180
    assert compact["payload"]["symbol"] == "005930"
    assert compact["payload"]["price"] == 81000
    assert compact["payload"]["qty"] == 2
    assert len(compact["payload"]["reason"]) <= 120
    assert "raw" not in compact["payload"]
    assert "html" not in compact["payload"]


def test_compact_prompt_events_limits_rows_and_ignores_invalid_items() -> None:
    rows = [{"id": index, "event_type": "note"} for index in range(4)]

    compact = compact_prompt_events([rows[0], "invalid", *rows[1:]], limit=2)

    assert [row["id"] for row in compact] == [0]


def test_compact_prompt_quote_keeps_allowed_quote_fields_only() -> None:
    compact = compact_prompt_quote(
        {
            "symbol": "005930",
            "name": "삼성전자",
            "price": 81200,
            "change_pct": 1.25,
            "source": "kis",
            "fetched_at": "2026-06-20T09:10:00+09:00",
            "raw": {"large": "payload"},
            "content": "DROP",
        }
    )

    assert compact == {
        "symbol": "005930",
        "name": "삼성전자",
        "price": 81200,
        "change_pct": 1.25,
        "source": "kis",
        "fetched_at": "2026-06-20T09:10:00+09:00",
    }


def test_compact_daily_discovery_prompt_limits_candidates_and_drops_raw_payloads() -> None:
    payload = {
        "status": "ok",
        "trading_day": "2026-06-20",
        "summary": "장전 랜덤 리서치 " * 80,
        "items": [
            {
                "symbol": f"{index:06d}",
                "name": f"종목{index}",
                "market": "KOSPI",
                "score": index,
                "analysis": {
                    "stance": "study",
                    "confidence": 0.6,
                    "summary": "분석 요약 " * 80,
                },
                "pre_surge": {
                    "is_candidate": index % 2 == 0,
                    "reasons": ["거래량 증가 " * 40, "수급"],
                    "raw": "DROP",
                },
                "raw": "DROP",
            }
            for index in range(12)
        ],
        "block_candidates": [
            {"symbol": f"1{index:05d}", "analysis": {"name": f"후보{index}"}}
            for index in range(7)
        ],
        "pre_surge_candidates": [],
    }

    compact = compact_daily_discovery_prompt(payload)

    assert compact is not None
    assert compact["status"] == "ok"
    assert compact["trading_day"] == "2026-06-20"
    assert len(compact["summary"]) <= 500
    assert len(compact["items"]) == 12
    assert len(compact["block_candidates"]) == 7
    assert len(compact["pre_surge_candidates"]) == 6
    first = compact["items"][0]
    assert first["symbol"] == "000000"
    assert first["name"] == "종목0"
    assert first["stance"] == "study"
    assert len(first["summary"]) <= 260
    assert len(first["pre_surge"]["reasons"]) == 2
    assert len(first["pre_surge"]["reasons"][0]) <= 140
    assert "raw" not in first
    assert "raw" not in first["pre_surge"]


def test_compact_daily_discovery_prompt_reconstructs_missing_pre_surge() -> None:
    payload = {
        "status": "ok",
        "trading_day": "2026-07-02",
        "items": [
            {
                "symbol": "001390",
                "name": "KG케미칼",
                "market": "KOSPI",
                "status": "ok",
                "score": 91.2,
                "analysis": {
                    "stance": "block_candidate",
                    "confidence": 0.82,
                    "summary": "저평가 눌림목에서 거래대금과 순환매 가능성이 붙었다.",
                    "reasons": ["저평가", "눌림목", "거래대금 증가"],
                    "risks": ["추격 매수 주의"],
                },
            }
        ],
        "block_candidates": [],
        "pre_surge_candidates": [],
    }

    compact = compact_daily_discovery_prompt(payload)

    assert compact is not None
    assert compact["pre_surge_candidates"][0]["symbol"] == "001390"
    assert compact["pre_surge_candidates"][0]["pre_surge"]["is_candidate"] is True
    assert (
        compact["pre_surge_candidates"][0]["pre_surge"]["entry_bias"]
        == "scout_or_waiting_block"
    )


def test_compact_daily_discovery_prompt_handles_invalid_payload() -> None:
    assert compact_daily_discovery_prompt(None) is None
    assert compact_daily_discovery_prompt("invalid") is None


def test_compact_etf_prompt_value_drops_raw_payloads_recursively() -> None:
    compact = compact_etf_prompt_value(
        {
            "symbol": "069500",
            "name": "KODEX 200",
            "nested": {
                "label": "core",
                "raw": "DROP",
                "raw_json": {"huge": "DROP"},
            },
            "items": [
                {"reason": "유동성 " * 80, "html": "DROP"},
                {"reason": "지수 대표성"},
            ],
            "response": "DROP",
        },
        list_limit=1,
        string_limit=80,
    )

    assert compact["symbol"] == "069500"
    assert compact["nested"] == {"label": "core"}
    assert len(compact["items"]) == 1
    assert len(compact["items"][0]["reason"]) <= 80
    assert "html" not in compact["items"][0]
    assert "response" not in compact


def test_compact_etf_prompt_fields_use_specific_allowed_key_sets() -> None:
    snapshot = compact_etf_prompt_fields(
        {
            "symbol": "069500",
            "name": "KODEX 200",
            "price": 41000,
            "change_pct": 0.7,
            "raw": "DROP",
            "unrelated": "DROP",
        },
        ETF_SNAPSHOT_PROMPT_KEYS,
    )
    score = compact_etf_prompt_fields(
        {
            "symbol": "069500",
            "label": "core",
            "liquidity_score": 88,
            "reasons": ["거래대금 안정"],
            "raw": "DROP",
            "unrelated": "DROP",
        },
        ETF_SCORE_PROMPT_KEYS,
    )
    candidate = compact_etf_prompt_fields(
        {
            "symbol": "069500",
            "asset_class": "etf",
            "horizon_bias": "core_etf",
            "score": 78,
            "sources": ["etf_research"],
            "raw": "DROP",
            "unrelated": "DROP",
        },
        ETF_CANDIDATE_PROMPT_KEYS,
    )

    assert snapshot == {
        "symbol": "069500",
        "name": "KODEX 200",
        "price": 41000,
        "change_pct": 0.7,
    }
    assert score == {
        "symbol": "069500",
        "label": "core",
        "liquidity_score": 88,
        "reasons": ["거래대금 안정"],
    }
    assert candidate == {
        "symbol": "069500",
        "asset_class": "etf",
        "horizon_bias": "core_etf",
        "score": 78,
        "sources": ["etf_research"],
    }


def test_compact_market_judgment_prompt_limits_rows_and_nested_payloads() -> None:
    long_text = "중기 보유 근거 " * 80
    payload = {
        "status": "ok",
        "run": {
            "id": 3,
            "run_at": "2026-06-20T11:40:00+09:00",
            "market_session": "regular",
            "status": "completed",
            "mode": "llm",
            "model": "gpt-5.5",
            "raw": "DROP",
        },
        "candidate_coverage": {
            "selected": [f"{index:06d}" for index in range(8)],
            "raw": "DROP",
        },
        "judgments": [
            {
                "symbol": f"{index:06d}",
                "name": f"종목{index}",
                "stance": "hold",
                "account_action": "watch_add",
                "horizon": "mid",
                "confidence": 0.7,
                "reasons": [long_text, "second", "third", "drop"],
                "risks": [long_text, "risk2", "risk3", "drop"],
                "triggers": [long_text, "trigger2", "trigger3", "drop"],
                "data_gaps": [long_text, "gap2", "gap3", "drop"],
                "quote": {
                    "symbol": f"{index:06d}",
                    "price": 1000 + index,
                    "raw": "DROP",
                },
                "position": {"qty": 1, "raw": "DROP"},
                "strategy": {
                    "symbol": f"{index:06d}",
                    "score": 72,
                    "valuation": {"label": "fair", "raw": "DROP"},
                    "raw": "DROP",
                },
            }
            for index in range(13)
        ],
    }

    compact = compact_market_judgment_prompt(payload)

    assert compact["status"] == "ok"
    assert compact["run"] == {
        "id": 3,
        "run_at": "2026-06-20T11:40:00+09:00",
        "market_session": "regular",
        "status": "completed",
        "mode": "llm",
        "model": "gpt-5.5",
    }
    assert len(compact["candidate_coverage"]["selected"]) == 4
    assert "raw" not in compact["candidate_coverage"]
    assert len(compact["judgments"]) == 12
    first = compact["judgments"][0]
    assert len(first["reasons"]) == 3
    assert len(first["reasons"][0]) <= 180
    assert len(first["triggers"]) == 3
    assert len(first["data_gaps"][0]) <= 120
    assert first["quote"] == {"symbol": "000000", "price": 1000}
    assert first["position"] == {"qty": 1}
    assert first["strategy"]["symbol"] == "000000"
    assert first["strategy"]["valuation"] == {"label": "fair"}
    assert "raw" not in first["strategy"]
