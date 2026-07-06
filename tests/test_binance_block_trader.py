from __future__ import annotations

import asyncio
import base64
import gzip
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tradecraft.services import binance_block_trader as binance_block_trader_module
from tradecraft.services.binance_block_trader import (
    BinanceBlockTrader,
    BinanceBlockTraderConfig,
    BinanceBlockRepository,
)
from tradecraft.services.binance_exit_gate import remaining_exit_qty
from tradecraft.services.binance_manager_prompt import (
    compact_manager_storage_payload,
    compact_prompt_section,
    compact_validation_repair_prompt as compact_binance_validation_repair_prompt,
    enforce_prompt_budget,
    finalize_prompt_budget,
    manager_response_contract_error,
    prompt_chars,
    validation_repair_action_metadata as binance_validation_repair_action_metadata,
)
from tradecraft.services.binance_snapshot import (
    compact_snapshot_manager_run,
    normalize_account_snapshot,
)
from tradecraft.services.binance_symbol import normalize_market
from tradecraft.services.binance_risk import BinanceRiskConfig, BinanceRiskSizer
from tradecraft.services.jue_skill_registry import JueSkillValidationError
from tradecraft.services.live_authority import LiveAuthorityConfig, build_authority_packet
from tradecraft.services.live_performance import LivePerformanceRepository

ROOT = Path(__file__).resolve().parents[1]


def _decode_gzip_base64(value: str) -> str:
    assert value.startswith("gzip+base64:")
    return gzip.decompress(base64.b64decode(value.removeprefix("gzip+base64:"))).decode(
        "utf-8"
    )


def _binance_memory_hint_contract_prompt() -> dict[str, Any]:
    return {
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
                "upbit_orders_enabled": True,
            },
        },
        "candidate_memory_hint_policy": {
            "required": True,
            "action_contract": "cite_or_reject_candidate_memory_hint",
        },
        "candidates": [
            {
                "symbol": "BTCUSDT",
                "memory_hint": {
                    "status": "available",
                    "sources": ["symbol_analysis_memory"],
                    "reasons": ["BTC는 급등 추격보다 눌림 대기 우선"],
                    "risks": ["급등 추격 손실"],
                    "checks": ["펀딩과 오더북 두께 재확인"],
                },
            }
        ],
    }


def test_binance_compact_snapshot_manager_run_preserves_workflow_provenance() -> None:
    compact = compact_snapshot_manager_run(
        {
            "id": 9,
            "run_at": "2026-07-06T01:00:00+00:00",
            "status": "ok",
            "mode": "llm",
            "model": "gpt-5.5",
            "workflow_id": "binance_cycle",
            "workflow_version": 1,
            "skill_ids": ["jue-binance-trading"],
            "contract_ids": ["jue_wiki_usage_contract_resolution"],
            "prompt": {
                "jue_workflow": {"workflow_id": "binance_cycle"},
                "compact_manager_context": {
                    "diagnostics": {
                        "version": "binance_manager_diagnostics_v1",
                    }
                },
            },
            "response": {"hold_decision": {"summary": "관망"}},
            "actions": {"create_blocks": []},
        },
        normalize_hold_decision=lambda response, actions: response["hold_decision"],
        compact_response_payload=lambda response: {"hold_decision": response["hold_decision"]},
        compact_prompt_context=lambda prompt, **kwargs: prompt["compact_manager_context"],
    )

    assert compact["workflow_id"] == "binance_cycle"
    assert compact["workflow_version"] == 1
    assert compact["skill_ids"] == ["jue-binance-trading"]
    assert compact["contract_ids"] == ["jue_wiki_usage_contract_resolution"]
    assert compact["decision_context"]["diagnostics"]["version"] == (
        "binance_manager_diagnostics_v1"
    )


def test_binance_validation_repair_prompt_preserves_memory_contract_fields() -> None:
    memory_context = {
        "validation_repair_backlog": {
            "status": "needs_repair",
            "items": [
                {
                    "policy_id": (
                        "manager_contract_error.binance."
                        "candidate_memory_hint_resolution_missing_from_model"
                    ),
                    "repair_action_id": (
                        "memory_contract_repair.binance."
                        "candidate_memory_hint_resolution_missing_from_model"
                    ),
                    "venue": "binance",
                    "discipline_id": "memory_contract",
                    "memory_contract": "cite_or_reject_candidate_memory_hint",
                    "memory_contract_error": (
                        "candidate_memory_hint_resolution_missing_from_model"
                    ),
                    "impacted_symbols": ["BTCUSDT"],
                    "required_checks": ["require_memory_contract_resolution"],
                    "required_evidence": ["memory_contract_resolution"],
                }
            ],
        },
        "block_design_constraints": {
            "status": "active_constraints",
            "items": [
                {
                    "policy_id": (
                        "manager_contract_error.binance."
                        "candidate_memory_hint_resolution_missing_from_model"
                    ),
                    "venue": "binance",
                    "discipline_id": "memory_contract",
                    "memory_contract": "cite_or_reject_candidate_memory_hint",
                    "memory_contract_error": (
                        "candidate_memory_hint_resolution_missing_from_model"
                    ),
                    "impacted_symbols": ["BTCUSDT"],
                    "entry_bias": "memory_contract_resolved_probe_or_wait",
                    "sizing_policy": (
                        "no_size_increase_until_memory_contract_repaired"
                    ),
                    "required_checks": ["require_memory_contract_resolution"],
                }
            ],
        },
    }

    repair = compact_binance_validation_repair_prompt(
        memory_context,
        scope="binance",
    )
    metadata = binance_validation_repair_action_metadata(repair)[
        "validation_repair"
    ]

    assert repair["repair_backlog"][0]["memory_contract"] == (
        "cite_or_reject_candidate_memory_hint"
    )
    assert repair["repair_backlog"][0]["memory_contract_error"] == (
        "candidate_memory_hint_resolution_missing_from_model"
    )
    assert repair["repair_backlog"][0]["impacted_symbols"] == ["BTCUSDT"]
    assert repair["block_design_constraints"][0]["memory_contract"] == (
        "cite_or_reject_candidate_memory_hint"
    )
    assert metadata["memory_contracts"] == [
        "cite_or_reject_candidate_memory_hint"
    ]
    assert metadata["memory_contract_errors"] == [
        "candidate_memory_hint_resolution_missing_from_model"
    ]
    assert metadata["impacted_symbols"] == ["BTCUSDT"]
    assert repair["memory_contract_resolution_required"] is True
    contract = repair["memory_contract_resolution_contract"]
    assert contract["response_field"] == (
        "validation_repair_resolution.resolved_candidates[]."
        "memory_contract_resolution"
    )
    assert contract["memory_contracts"] == [
        "cite_or_reject_candidate_memory_hint"
    ]
    assert contract["memory_contract_errors"] == [
        "candidate_memory_hint_resolution_missing_from_model"
    ]
    assert contract["impacted_symbols"] == ["BTCUSDT"]


def test_binance_manager_storage_preserves_manager_contract_recovery_summary() -> None:
    compact = compact_manager_storage_payload(
        {
            "account": {"equity_usdt": 1000.0},
            "memory": {
                "status": "ok",
                "validation_recovery_summary": {
                    "status": "clear",
                    "manager_contract_recovered": [
                        {
                            "policy_id": (
                                "manager_contract_error.binance."
                                "candidate_memory_hint_resolution_missing_from_model"
                            ),
                            "resolution_policy_id": (
                                "manager_contract_resolution.binance."
                                "candidate_memory_hint_resolution_missing_from_model"
                            ),
                            "contract": "cite_or_reject_candidate_memory_hint",
                            "error": (
                                "candidate_memory_hint_resolution_missing_from_model"
                            ),
                            "impacted_symbols": ["BTCUSDT"],
                            "latest_resolution": (
                                "cite_memory_and_apply: BTCUSDT 급등 추격 손실 "
                                "메모리를 반영해 즉시 진입 대신 눌림 대기 "
                                "선물 블록으로 낮춘다."
                            ),
                        }
                    ],
                },
            },
            "candidates": [
                {
                    "symbol": f"TEST{i}USDT",
                    "reason": "x" * 400,
                    "context": {"nested": ["y" * 300 for _ in range(4)]},
                }
                for i in range(60)
            ],
        },
        limit=1200,
        label="binance_manager_prompt",
    )

    assert len(json.dumps(compact, ensure_ascii=False)) <= 1200
    assert compact["_storage_compaction"]["priority_reason"] == (
        "manager_contract_recovery"
    )
    assert "output_schema" in compact["_storage_compaction"]["dropped_keys"]
    recovered = compact["memory"]["validation_recovery_summary"][
        "manager_contract_recovered"
    ]
    assert recovered[0]["contract"] == "cite_or_reject_candidate_memory_hint"
    assert recovered[0]["error"] == (
        "candidate_memory_hint_resolution_missing_from_model"
    )
    assert recovered[0]["latest_resolution"].startswith("cite_memory_and_apply")


def test_binance_memory_prompt_compaction_preserves_manager_contract_recovery() -> None:
    compact = compact_prompt_section(
        "memory",
        {
            "status": "ok",
            "validation_recovery_summary": {
                "status": "clear",
                "manager_contract_recovered": [
                    {
                        "policy_id": (
                            "manager_contract_error.binance."
                            "candidate_memory_hint_resolution_missing_from_model"
                        ),
                        "resolution_policy_id": (
                            "manager_contract_resolution.binance."
                            "candidate_memory_hint_resolution_missing_from_model"
                        ),
                        "contract": "cite_or_reject_candidate_memory_hint",
                        "error": "candidate_memory_hint_resolution_missing_from_model",
                        "impacted_symbols": ["BTCUSDT"],
                        "latest_resolution": (
                            "cite_memory_and_apply: BTCUSDT 급등 추격 손실 "
                            "메모리를 반영해 즉시 진입 대신 눌림 대기 "
                            "선물 블록으로 낮춘다."
                        ),
                    }
                ],
            },
        },
        list_limit=2,
        string_limit=70,
    )

    recovered = compact["validation_recovery_summary"]["manager_contract_recovered"]
    assert recovered[0]["contract"] == "cite_or_reject_candidate_memory_hint"
    assert recovered[0]["latest_resolution"].startswith("cite_memory_and_apply")


def _normalize_test_account(
    trader: BinanceBlockTrader,
    account: dict[str, Any],
) -> dict[str, Any]:
    return normalize_account_snapshot(
        account,
        default_upbit_usdt_krw_rate=trader.config.upbit_usdt_krw_rate,
    )


def test_binance_performance_lane_normalization_lives_in_lane_module() -> None:
    trader_source = (
        ROOT / "src/tradecraft/services/binance_block_trader.py"
    ).read_text()
    lane_source = (ROOT / "src/tradecraft/services/binance_lane.py").read_text()

    assert "def canonical_binance_performance_lane(" in lane_source
    assert "def binance_performance_lane_from_payload(" in lane_source
    assert "def _canonical_performance_lane(" not in trader_source
    assert "def _performance_lane_from_payload(" not in trader_source


def test_binance_order_fill_price_lives_in_executor_module() -> None:
    trader_source = (
        ROOT / "src/tradecraft/services/binance_block_trader.py"
    ).read_text()
    executor_source = (ROOT / "src/tradecraft/services/binance_executor.py").read_text()

    assert "def filled_order_price(" in executor_source
    assert "def _filled_order_price(" not in trader_source
    assert "filled_order_price as build_filled_order_price" in trader_source


def test_binance_block_repository_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")

    with repo._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout_ms >= 30000


def test_binance_manager_prompt_defaults_are_latency_oriented() -> None:
    config = BinanceBlockTraderConfig()

    assert config.max_manager_symbols == 36
    assert config.quant_context_limit == 18
    assert config.prompt_target_chars == 45_000
    assert config.prompt_warn_chars == 65_000
    assert config.prompt_max_chars == 190_000
    assert config.jue_wiki_context_max_chars == 18_000
    assert config.prompt_target_chars < config.prompt_warn_chars < config.prompt_max_chars


def test_binance_jue_wiki_prompt_context_is_compacted_before_storage() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}
    marker = "BINANCE_JUE_WIKI_OVERSIZED_CONTEXT"
    payload = {
        "status": "ok",
        "selection_run_id": "selection:binance-compact-test",
        "prompt_mode": "assist",
        "target_scope": "binance",
        "pages": [
            {
                "page_id": "binance.risk.trading_validation",
                "rank": 1,
                "score": 180,
                "content": marker * 400,
                "selection_reasons": [marker * 8],
            },
            {
                "page_id": "binance.symbol.BTCUSDT",
                "rank": 2,
                "score": 120,
                "content": marker * 400,
            },
        ],
        "content": marker * 1_000,
        "rejected_pages": [
            {
                "page_id": f"binance.symbol.T{idx:02d}USDT",
                "reason": marker * 20,
                "content": marker * 200,
            }
            for idx in range(30)
        ],
        "requested_symbol_summaries": [
            {
                "page_id": "binance.symbol.ETHUSDT",
                "symbol": "ETHUSDT",
                "summary": marker * 100,
                "memory_card": {
                    "stance": "spot patience",
                    "lessons": marker * 80,
                },
            }
        ],
    }

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        payload,
        max_chars=4_000,
    )

    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(json.dumps(prompt["jue_wiki"], ensure_ascii=False)) <= 4_400
    assert "binance.risk.trading_validation" in prompt_text
    assert prompt["jue_wiki"]["pages"][0]["page_id"] == (
        "binance.risk.trading_validation"
    )
    assert "content" not in prompt["jue_wiki"]["pages"][0]
    assert prompt["jue_wiki"]["requested_symbol_summaries"][0]["page_id"] == (
        "binance.symbol.ETHUSDT"
    )
    assert prompt["jue_wiki"]["requested_symbol_summaries"][0]["memory_card"] == {
        "stance": "spot patience"
    }
    assert prompt_text.count(marker) < 50
    assert prompt["jue_wiki"]["budget_report"]["prompt_payload_status"] == "compacted"


def test_binance_jue_wiki_prompt_context_compacts_live_outcome_effectiveness_reasons() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}
    noisy_reasons = [f"low_priority_reason_{idx}" for idx in range(20)]

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-live-outcomes",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.performance.live_outcomes",
                    "rank": 1,
                    "score": 91.0,
                    "selection_reasons": ["operational_memory:live_performance"],
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
            "budget_report": {"char_count": 10},
        },
        max_chars=4_000,
    )

    reasons = prompt["jue_wiki"]["pages"][0]["effectiveness"]["reasons"]
    assert len(reasons) <= 8
    assert "metric_source=live_block_performance" in reasons
    assert "page_id=binance.performance.live_outcomes" in reasons
    assert "raw_venue=binance_futures" in reasons


def test_binance_jue_wiki_prompt_context_keeps_page_freshness_quality_metadata() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-page-quality",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "rank": 1,
                    "score": 87.5,
                    "freshness": "stale",
                    "quality_status": "weak",
                    "quality_warnings": [
                        "market_structure_stale",
                        "funding_missing",
                        "ignore-extra-warning",
                        "ignore-extra-warning-2",
                    ],
                    "updated_at": "2026-06-01T00:00:00+00:00",
                    "as_of": "2026-06-01",
                    "selection_penalties": ["freshness:stale"],
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    assert page["freshness"] == "stale"
    assert page["quality_status"] == "weak"
    assert page["quality_warnings"] == [
        "market_structure_stale",
        "funding_missing",
        "ignore-extra-warning",
    ]
    assert page["updated_at"] == "2026-06-01T00:00:00+00:00"
    assert page["as_of"] == "2026-06-01"


def test_binance_jue_wiki_prompt_context_keeps_requested_symbol_quality_metadata() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-symbol-quality",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "requested_symbol_summaries": [
                {
                    "symbol": "ETHUSDT",
                    "page_id": "binance.symbol.ETHUSDT",
                    "title": "ETHUSDT",
                    "freshness": "stale",
                    "quality_status": "weak",
                    "quality_warnings": [
                        "market_structure_stale",
                        "funding_missing",
                        "ignore-extra-warning",
                        "ignore-extra-warning-2",
                    ],
                    "updated_at": "2026-06-01T00:00:00+00:00",
                    "as_of": "2026-06-01",
                    "summary": "KEEP_SUMMARY. " + ("DROP_ME" * 100),
                }
            ],
        },
        max_chars=4_000,
    )

    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert summary["freshness"] == "stale"
    assert summary["quality_status"] == "weak"
    assert summary["quality_warnings"] == [
        "market_structure_stale",
        "funding_missing",
        "ignore-extra-warning",
    ]
    assert summary["updated_at"] == "2026-06-01T00:00:00+00:00"
    assert summary["as_of"] == "2026-06-01"


def test_binance_jue_wiki_requested_symbol_summary_preserves_guidance_metadata() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-requested-guidance",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "requested_symbol_summaries": [
                {
                    "symbol": "ETHUSDT",
                    "page_id": "binance.symbol.ETHUSDT",
                    "title": "ETHUSDT",
                    "summary": "funding 과열 전에는 breakout 재확인을 요구",
                    "usage_guidance": {
                        "risk_posture": "breakout_retest",
                        "required_cross_checks": [
                            "funding_rate",
                            "orderbook_depth",
                            "spread_bps",
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
                                "avg_return_pct": 0.8,
                            }
                        ],
                        "summary": "재확인 지침은 false breakout 회피에 유효",
                    },
                    "memory_card_quality_effectiveness": {
                        "metrics": [
                            {
                                "page_id": "memory_card_quality.missing_field.lessons",
                                "status": "degraded",
                                "sample_count": 3,
                            }
                        ]
                    },
                    "quality_warning_source_effectiveness": {
                        "metrics": [
                            {
                                "page_id": "binance.symbol.ETHUSDT",
                                "source_type": "crypto_quant",
                                "warning": "funding_missing",
                                "status": "degraded",
                                "sample_count": 5,
                            }
                        ]
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "funding_missing",
                            "status": "degraded",
                            "sample_count": 5,
                            "avg_return_pct": -0.4,
                        }
                    ],
                    "quality_warning_effectiveness_statuses": ["degraded"],
                }
            ],
        },
        max_chars=4_000,
    )

    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert summary["usage_guidance"]["risk_posture"] == "breakout_retest"
    assert summary["usage_guidance"]["required_cross_checks"] == [
        "funding_rate",
        "orderbook_depth",
        "spread_bps",
    ]
    assert summary["usage_guidance"]["max_confidence_without_cross_check"] == 0.5
    assert summary["usage_guidance_effectiveness"]["metrics"][0]["status"] == "active"
    assert (
        summary["memory_card_quality_effectiveness"]["metrics"][0]["page_id"]
        == "memory_card_quality.missing_field.lessons"
    )
    assert (
        summary["quality_warning_source_effectiveness"]["metrics"][0]["source_type"]
        == "crypto_quant"
    )
    assert summary["quality_warning_effectiveness"][0]["warning"] == "funding_missing"
    assert summary["quality_warning_effectiveness_statuses"] == ["degraded"]


def test_binance_jue_wiki_prompt_context_derives_quality_metadata_from_evidence_quality() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}

    evidence_quality = {
        "status_counts": {"weak": 1},
        "top_warnings": [
            {"warning": "market_structure_stale", "count": 2},
            {"warning": "funding_missing", "count": 1},
            {"warning": "ignore-extra-warning", "count": 1},
            {"warning": "ignore-extra-warning-2", "count": 1},
        ],
    }
    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-evidence-quality",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "rank": 1,
                    "score": 87.5,
                    "evidence_quality": evidence_quality,
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "ETHUSDT",
                    "page_id": "binance.symbol.ETHUSDT",
                    "freshness": "stale",
                    "summary": "KEEP_SUMMARY.",
                    "evidence_quality": evidence_quality,
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert page["quality_status"] == "weak"
    assert summary["quality_status"] == "weak"
    assert page["quality_warnings"] == [
        "market_structure_stale",
        "funding_missing",
        "ignore-extra-warning",
    ]
    assert summary["quality_warnings"] == [
        "market_structure_stale",
        "funding_missing",
        "ignore-extra-warning",
    ]


def test_binance_jue_wiki_prompt_context_canonicalizes_direct_quality_aliases() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-direct-quality-aliases",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "rank": 1,
                    "quality_status": "degraded",
                    "source_refs": [
                        {
                            "source_type": "crypto_quant",
                            "source_id": "ETHUSDT:alias-source",
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
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "ETHUSDT",
                    "page_id": "binance.symbol.ETHUSDT",
                    "summary": "KEEP_SUMMARY.",
                    "quality_status": "ok",
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert page["quality_status"] == "weak"
    assert summary["quality_status"] == "strong"
    source_ref = page["source_refs"][0]
    assert source_ref["quality_status"] == "weak"
    assert source_ref["evidence_quality"]["status_counts"] == {
        "strong": 1,
        "weak": 1,
    }
    assert "raw_blob" not in source_ref


def test_binance_jue_wiki_application_metadata_summarizes_quality_pressure() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-quality-summary",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "quality_status": "weak",
                    "freshness_status": "stale",
                    "freshness_warnings": ["updated_at_stale_gt_14d"],
                    "quality_warnings": [
                        "market_structure_stale",
                        "funding_missing",
                    ],
                }
            ],
            "requested_symbol_summaries": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "symbol": "ETHUSDT",
                    "quality_status": "partial",
                    "quality_warnings": ["funding_missing"],
                    "summary": "ETHUSDT 압축 기억",
                }
            ],
        },
        max_chars=4_000,
    )

    assert prompt["jue_wiki_application"]["quality_summary"] == {
        "row_count": 2,
        "status_counts": {"partial": 1, "weak": 1},
        "warning_counts": {
            "funding_missing": 2,
            "market_structure_stale": 1,
            "updated_at_stale_gt_14d": 1,
        },
        "top_warnings": [
            {"warning": "funding_missing", "count": 2},
            {"warning": "market_structure_stale", "count": 1},
            {"warning": "updated_at_stale_gt_14d", "count": 1},
        ],
        "warning_page_ids": {
            "funding_missing": [
                "binance.symbol.BTCUSDT",
                "binance.symbol.ETHUSDT",
            ],
            "market_structure_stale": ["binance.symbol.BTCUSDT"],
            "updated_at_stale_gt_14d": ["binance.symbol.BTCUSDT"],
        },
        "weak_page_ids": ["binance.symbol.BTCUSDT"],
        "caution_page_ids": ["binance.symbol.BTCUSDT", "binance.symbol.ETHUSDT"],
    }
    assert prompt["jue_wiki_application"]["quality_pressure_action_plan"] == {
        "status": "repair_required",
        "hard_blocker": False,
        "decision_policy": (
            "use_quality_warnings_as_candidate_level_cross_checks_not_blanket_holds"
        ),
        "required_adjustments": [
            {
                "adjustment_type": "candidate_level_cross_check",
                "reason": "weak_wiki_pages",
                "page_ids": ["binance.symbol.BTCUSDT"],
                "resolution": "refresh_or_cross_check_before_sizing",
            },
            {
                "adjustment_type": "quality_warning_resolution",
                "warning": "funding_missing",
                "count": 2,
                "resolution": "refresh_or_cross_check_before_sizing",
                "page_ids": [
                    "binance.symbol.BTCUSDT",
                    "binance.symbol.ETHUSDT",
                ],
            },
            {
                "adjustment_type": "quality_warning_resolution",
                "warning": "market_structure_stale",
                "count": 1,
                "resolution": "refresh_or_cross_check_before_sizing",
                "page_ids": ["binance.symbol.BTCUSDT"],
            },
            {
                "adjustment_type": "quality_warning_resolution",
                "warning": "updated_at_stale_gt_14d",
                "count": 1,
                "resolution": "refresh_or_cross_check_before_sizing",
                "page_ids": ["binance.symbol.BTCUSDT"],
            },
        ],
        "repair_focus": [
            {
                "priority_type": "evidence_quality",
                "warning": "funding_missing",
                "count": 2,
                "decision_use": "evidence_quality_cross_check",
                "page_ids": [
                    "binance.symbol.BTCUSDT",
                    "binance.symbol.ETHUSDT",
                ],
            },
            {
                "priority_type": "evidence_quality",
                "warning": "market_structure_stale",
                "count": 1,
                "decision_use": "evidence_quality_cross_check",
                "page_ids": ["binance.symbol.BTCUSDT"],
            },
            {
                "priority_type": "evidence_quality",
                "warning": "updated_at_stale_gt_14d",
                "count": 1,
                "decision_use": "evidence_quality_cross_check",
                "page_ids": ["binance.symbol.BTCUSDT"],
            },
        ],
        "caution_page_ids": ["binance.symbol.BTCUSDT", "binance.symbol.ETHUSDT"],
    }


def test_binance_jue_wiki_action_pressure_contract_attaches_and_requires_resolution() -> None:
    prompt: dict[str, Any] = {"decision_inputs": []}

    binance_block_trader_module._attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-action-pressure",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "binance.ops.action_pressure",
                    "summary": "no_action=57/80 with 586 candidates",
                    "source_refs": [
                        {
                            "source_type": "action_pressure",
                            "source_id": "binance.manager_runs",
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
    assert contract["page_ids"] == ["binance.ops.action_pressure"]
    assert "jue_wiki_action_pressure_contract" in prompt["decision_inputs"]

    error = manager_response_contract_error(
        prompt={
            **prompt,
            "execution_gate": {
                "status": "ok",
                "kill_switch": {"enabled": False},
                "execution": {
                    "spot_orders_enabled": True,
                    "futures_orders_enabled": True,
                    "upbit_orders_enabled": True,
                },
            },
        },
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "hold_decision_missing_concrete_trigger"


def test_manager_response_contract_requires_candidate_memory_hint_resolution() -> None:
    error = manager_response_contract_error(
        prompt=_binance_memory_hint_contract_prompt(),
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "thesis": "breakout continuation",
                    "risk_note": "standard risk",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == "candidate_memory_hint_resolution_missing_from_model"


def test_manager_response_contract_accepts_candidate_memory_hint_resolution() -> None:
    error = manager_response_contract_error(
        prompt=_binance_memory_hint_contract_prompt(),
        response={"hold_decision": {"summary": "관망"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "thesis": "BTC는 급등 추격보다 눌림 대기 우선",
                    "risk_note": "급등 추격 손실 때문에 펀딩과 오더북 두께 재확인",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "관망"},
    )

    assert error == ""


def test_manager_response_contract_requires_memory_contract_repair_resolution() -> None:
    prompt = {
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
                "upbit_orders_enabled": True,
            },
        },
        "validation_repair": {
            "scope": "binance",
            "status": "needs_repair",
            "repair_item_count": 1,
            "constraint_count": 1,
            "memory_contracts": ["cite_or_reject_candidate_memory_hint"],
            "memory_contract_errors": [
                "candidate_memory_hint_resolution_missing_from_model"
            ],
            "impacted_symbols": ["BTCUSDT"],
            "required_checks": ["require_memory_contract_resolution"],
            "repair_backlog": [
                {
                    "discipline_id": "memory_contract",
                    "memory_contract": "cite_or_reject_candidate_memory_hint",
                    "memory_contract_error": (
                        "candidate_memory_hint_resolution_missing_from_model"
                    ),
                    "impacted_symbols": ["BTCUSDT"],
                    "required_checks": ["require_memory_contract_resolution"],
                }
            ],
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": "funding and spread evidence missing",
                    }
                ]
            }
        },
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert error == "memory_contract_resolution_missing_from_model"


def test_manager_response_contract_accepts_memory_contract_repair_resolution() -> None:
    prompt = {
        "execution_gate": {
            "status": "ok",
            "kill_switch": {"enabled": False},
            "execution": {
                "spot_orders_enabled": True,
                "futures_orders_enabled": True,
                "upbit_orders_enabled": True,
            },
        },
        "validation_repair": {
            "scope": "binance",
            "status": "needs_repair",
            "repair_item_count": 1,
            "constraint_count": 1,
            "memory_contracts": ["cite_or_reject_candidate_memory_hint"],
            "memory_contract_errors": [
                "candidate_memory_hint_resolution_missing_from_model"
            ],
            "impacted_symbols": ["BTCUSDT"],
            "required_checks": ["require_memory_contract_resolution"],
        },
    }

    error = manager_response_contract_error(
        prompt=prompt,
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "resolution": "candidate_rejected",
                        "evidence_gap": (
                            "cite_or_reject_candidate_memory_hint 계약을 확인했고 "
                            "candidate_memory_hint_resolution_missing_from_model "
                            "수리를 위해 후보 메모리 근거를 명시적으로 거절한다."
                        ),
                    }
                ]
            }
        },
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        hold_decision={"summary": "관망"},
    )

    assert error == ""


def test_binance_prune_operational_history_archives_old_quotes(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    hot = datetime.now(timezone.utc).isoformat()
    repo.save_quotes(
        [
            {
                "symbol": "OLDUSDT",
                "market": "futures",
                "price": 1.0,
                "source": "test",
                "fetched_at": old,
                "raw": {"old": True},
            },
            {
                "symbol": "HOTUSDT",
                "market": "futures",
                "price": 2.0,
                "source": "test",
                "fetched_at": hot,
                "raw": {"hot": True},
            },
        ]
    )

    result = repo.prune_operational_history(
        quote_retention_days=7,
        manager_run_retention_days=0,
    )

    assert result["deleted"]["quote_snapshots"] == 1
    assert result["archived"]["quote_snapshots"] == 1
    with repo._connect() as conn:
        hot_symbols = conn.execute(
            "SELECT symbol FROM quote_snapshots ORDER BY id"
        ).fetchall()
        archived_symbols = conn.execute(
            "SELECT symbol, raw_json FROM quote_snapshots_archive ORDER BY id"
        ).fetchall()
    assert [row[0] for row in hot_symbols] == ["HOTUSDT"]
    assert [row[0] for row in archived_symbols] == ["OLDUSDT"]
    assert json.loads(_decode_gzip_base64(archived_symbols[0][1])) == {"old": True}


def test_binance_quote_raw_compaction_keeps_small_payloads_and_compacts_exchange_raw(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")

    repo.save_quotes(
        [
            {
                "symbol": "BTCUSDT",
                "market": "spot",
                "price": 60000.0,
                "source": "test",
                "raw": {"small": True},
            },
            {
                "symbol": "ETHUSDT",
                "market": "spot",
                "price": 3000.0,
                "source": "binance",
                "raw": {
                    "last_price": 3000.0,
                    "raw": {
                        "symbol": "ETHUSDT",
                        "lastPrice": "3000",
                        "quoteVolume": "123456",
                        "ignored_blob": "x" * 2000,
                    },
                },
            },
        ]
    )

    with repo._connect() as conn:
        rows = conn.execute(
            "SELECT symbol, raw_json FROM quote_snapshots ORDER BY id"
        ).fetchall()

    small = json.loads(rows[0][1])
    compact = json.loads(rows[1][1])
    assert small == {"small": True}
    assert compact["_raw_compacted"] is True
    assert compact["raw"]["_raw_compacted"] is True
    assert compact["raw"]["lastPrice"] == "3000"
    assert "ignored_blob" not in compact["raw"]


def test_binance_compacts_existing_verbose_quote_raw_payloads(tmp_path: Path) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    with repo._connect() as conn:
        conn.execute(
            """
            INSERT INTO quote_snapshots (
                symbol, market, price, source, fetched_at, status, error_message, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "BTCUSDT",
                "spot",
                60000,
                "binance",
                datetime.now(timezone.utc).isoformat(),
                "ok",
                "",
                json.dumps(
                    {
                        "last_price": 60000,
                        "raw": {
                            "symbol": "BTCUSDT",
                            "lastPrice": "60000",
                            "ignored_blob": "x" * 2000,
                        },
                    }
                ),
            ),
        )

    result = repo.compact_verbose_quote_raw_payloads(batch_size=10)

    assert result["updated"] == 1
    assert result["remaining"] == 0
    with repo._connect() as conn:
        raw = conn.execute("SELECT raw_json FROM quote_snapshots").fetchone()[0]
    payload = json.loads(raw)
    assert payload["_raw_compacted"] is True
    assert payload["raw"]["_raw_compacted"] is True
    assert "ignored_blob" not in payload["raw"]


def test_binance_prune_operational_history_archives_old_manager_runs(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    hot = datetime.now(timezone.utc).isoformat()
    with repo._connect() as conn:
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, error_message,
                prompt_json, response_json, actions_json
            )
            VALUES (?, 'ok', 'llm', 'gpt-5.5', '', ?, '{}', '{}')
            """,
            (old, json.dumps({"kind": "old-manager"})),
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, error_message,
                prompt_json, response_json, actions_json
            )
            VALUES (?, 'ok', 'llm', 'gpt-5.5', '', ?, '{}', '{}')
            """,
            (hot, json.dumps({"kind": "hot-manager"})),
        )

    result = repo.prune_operational_history(
        quote_retention_days=0,
        manager_run_retention_days=30,
        archive_retention_days=30,
    )

    assert result["deleted"]["manager_runs"] == 1
    assert result["archived"]["manager_runs"] == 1
    with repo._connect() as conn:
        active_prompts = conn.execute(
            "SELECT prompt_json FROM manager_runs ORDER BY id"
        ).fetchall()
        archived_prompts = conn.execute(
            "SELECT prompt_json FROM manager_runs_archive ORDER BY id"
        ).fetchall()
    assert json.loads(active_prompts[0][0])["kind"] == "hot-manager"
    assert json.loads(_decode_gzip_base64(archived_prompts[0][0]))["kind"] == "old-manager"


def test_binance_compacts_old_active_manager_run_payloads(tmp_path: Path) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    old_id = repo.save_manager_run(
        prompt={"kind": "old", "blob": "P" * 30_000},
        response={"kind": "old-response", "blob": "R" * 10_000},
        actions={"create_blocks": [{"symbol": "OLDUSDT", "thesis": "A" * 10_000}]},
        model="gpt-5.5",
    )
    recent_id = repo.save_manager_run(
        prompt={"kind": "recent", "blob": "N" * 30_000},
        response={"kind": "recent-response", "blob": "S" * 10_000},
        actions={"create_blocks": [{"symbol": "NEWUSDT", "thesis": "B" * 10_000}]},
        model="gpt-5.5",
    )
    with repo._connect() as conn:
        conn.execute(
            "UPDATE manager_runs SET run_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", old_id),
        )
        conn.execute(
            "UPDATE manager_runs SET run_at = ? WHERE id = ?",
            ("2026-01-02T00:00:00+00:00", recent_id),
        )

    result = repo.compact_active_manager_runs(recent_count=1, min_chars=1_000)

    assert result["manager_runs"] == 1
    with repo._connect() as conn:
        old_prompt, old_response, old_actions = conn.execute(
            """
            SELECT prompt_json, response_json, actions_json
            FROM manager_runs
            WHERE id = ?
            """,
            (old_id,),
        ).fetchone()
        recent_prompt, recent_response, recent_actions = conn.execute(
            """
            SELECT prompt_json, response_json, actions_json
            FROM manager_runs
            WHERE id = ?
            """,
            (recent_id,),
        ).fetchone()

    old_prompt_payload = json.loads(old_prompt)
    old_response_payload = json.loads(old_response)
    old_actions_payload = json.loads(old_actions)
    assert old_prompt_payload["compacted"] is True
    assert old_prompt_payload["reason"] == "binance_manager_run_payload_retention"
    assert old_prompt_payload["field"] == "prompt_json"
    assert old_prompt_payload["original_chars"] > 1_000
    assert old_response_payload["compacted"] is True
    assert old_response_payload["field"] == "response_json"
    assert old_actions_payload["compacted"] is True
    assert old_actions_payload["field"] == "actions_json"
    assert json.loads(recent_prompt)["kind"] == "recent"
    assert json.loads(recent_response)["kind"] == "recent-response"
    assert json.loads(recent_actions)["create_blocks"][0]["symbol"] == "NEWUSDT"

    second = repo.compact_active_manager_runs(recent_count=1, min_chars=1_000)
    assert second["manager_runs"] == 0


def test_binance_prune_operational_history_compacts_active_manager_payloads(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    old_id = repo.save_manager_run(
        prompt={"kind": "old", "blob": "P" * 30_000},
        response={"kind": "old-response", "blob": "R" * 10_000},
        actions={"create_blocks": [{"symbol": "OLDUSDT", "thesis": "A" * 10_000}]},
        model="gpt-5.5",
    )
    recent_id = repo.save_manager_run(
        prompt={"kind": "recent", "blob": "N" * 30_000},
        response={"kind": "recent-response", "blob": "S" * 10_000},
        actions={"create_blocks": [{"symbol": "NEWUSDT", "thesis": "B" * 10_000}]},
        model="gpt-5.5",
    )
    with repo._connect() as conn:
        conn.execute(
            "UPDATE manager_runs SET run_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", old_id),
        )
        conn.execute(
            "UPDATE manager_runs SET run_at = ? WHERE id = ?",
            ("2026-01-02T00:00:00+00:00", recent_id),
        )

    result = repo.prune_operational_history(
        quote_retention_days=0,
        manager_run_retention_days=3650,
        archive_retention_days=0,
        manager_run_recent_count=1,
        manager_run_payload_min_chars=1_000,
    )

    assert result["active_manager_compaction"]["manager_runs"] == 1
    with repo._connect() as conn:
        old_prompt = conn.execute(
            "SELECT prompt_json FROM manager_runs WHERE id = ?",
            (old_id,),
        ).fetchone()[0]
        recent_prompt = conn.execute(
            "SELECT prompt_json FROM manager_runs WHERE id = ?",
            (recent_id,),
        ).fetchone()[0]
    assert json.loads(old_prompt)["reason"] == "binance_manager_run_payload_retention"
    assert json.loads(recent_prompt)["kind"] == "recent"


def test_binance_prune_operational_history_prunes_growth_target_snapshots(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    for equity, created_at in (
        (100.0, "2026-01-01T00:00:00+00:00"),
        (200.0, "2026-01-02T00:00:00+00:00"),
        (300.0, datetime.now(timezone.utc).isoformat()),
    ):
        repo.save_growth_target_snapshot(
            month_key="2026-01",
            payload={
                "start_equity_usdt": 100.0,
                "current_equity_usdt": equity,
                "target_equity_usdt": 150.0,
                "current_return_pct": 0.0,
                "remaining_return_pct": 0.0,
                "required_daily_return_pct": 0.0,
                "status": "tracking",
            },
        )
        with repo._connect() as conn:
            conn.execute(
                """
                UPDATE growth_target_snapshots
                SET created_at = ?
                WHERE id = (SELECT MAX(id) FROM growth_target_snapshots)
                """,
                (created_at,),
            )

    result = repo.prune_operational_history(
        quote_retention_days=0,
        manager_run_retention_days=0,
        archive_retention_days=0,
        growth_snapshot_retention_days=30,
    )

    assert result["growth_target_snapshots_deleted"] == 2
    with repo._connect() as conn:
        rows = conn.execute(
            """
            SELECT current_equity_usdt
            FROM growth_target_snapshots
            ORDER BY id
            """
        ).fetchall()
    assert [row[0] for row in rows] == [pytest.approx(300.0)]


def test_binance_prune_operational_history_deletes_cold_archives(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    repo.save_quotes(
        [
            {
                "symbol": "OLDUSDT",
                "market": "futures",
                "price": 1.0,
                "source": "test",
                "fetched_at": old,
                "raw": {"old": True},
            }
        ]
    )
    repo.prune_operational_history(
        quote_retention_days=7,
        manager_run_retention_days=0,
        archive_retention_days=30,
    )
    cold_source_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with repo._connect() as conn:
        conn.execute(
            "UPDATE quote_snapshots_archive SET fetched_at = ?",
            (cold_source_time,),
        )

    result = repo.prune_operational_history(
        quote_retention_days=0,
        manager_run_retention_days=0,
        archive_retention_days=30,
    )

    assert result["deleted"]["quote_snapshots_archive"] == 1
    with repo._connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM quote_snapshots_archive").fetchone()[0]
            == 0
        )


def test_binance_compact_legacy_archives_compresses_plain_archive_payloads(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with repo._connect() as conn:
        conn.execute(
            """
            CREATE TABLE quote_snapshots_archive (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_runs_archive (
                id INTEGER PRIMARY KEY,
                run_at TEXT NOT NULL,
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO quote_snapshots_archive (
                id, symbol, market, fetched_at, raw_json
            )
            VALUES (1, 'BTCUSDT', 'futures', ?, ?)
            """,
            (old, '{"raw":"' + ("q" * 512) + '"}'),
        )
        conn.execute(
            """
            INSERT INTO manager_runs_archive (
                id, run_at, prompt_json, response_json, actions_json
            )
            VALUES (1, ?, ?, ?, ?)
            """,
            (
                old,
                '{"prompt":"' + ("p" * 512) + '"}',
                '{"response":"' + ("r" * 512) + '"}',
                '{"actions":"' + ("a" * 512) + '"}',
            ),
        )

    result = repo.compact_legacy_archives(batch_size=100, vacuum=False)

    with repo._connect() as conn:
        quote_raw = conn.execute(
            "SELECT raw_json FROM quote_snapshots_archive"
        ).fetchone()[0]
        manager_prompt = conn.execute(
            "SELECT prompt_json FROM manager_runs_archive"
        ).fetchone()[0]

    assert result["status"] == "ok"
    assert result["tables"]["quote_snapshots_archive"]["compacted"] == 1
    assert result["tables"]["manager_runs_archive"]["compacted"] == 1
    assert json.loads(_decode_gzip_base64(quote_raw))["raw"].startswith("qqq")
    assert json.loads(_decode_gzip_base64(manager_prompt))["prompt"].startswith("ppp")


def test_binance_block_repository_compacts_validation_repair_metadata(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    long_text = "raw validation section " * 500

    block = repo.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.05,
            "entry_price": 100.0,
            "target_price": 108.0,
            "stop_price": 96.0,
            "status": "proposed",
            "metadata": {
                "validation_repair": {
                    "scope": "binance",
                    "repair_item_count": 1,
                    "constraint_count": 1,
                    "repair_backlog": [
                        {
                            "policy_id": "validation.storage.compact",
                            "discipline_id": "walk_forward_analysis",
                            "last_repair_reason": long_text,
                        }
                    ],
                    "block_design_constraints": [
                        {
                            "policy_id": "validation.storage.compact",
                            "discipline_id": "walk_forward_analysis",
                            "entry_bias": "waiting_probe",
                            "risk_budget_multiplier": 0.25,
                            "risk_note": long_text,
                        }
                    ],
                }
            },
        }
    )

    repair = block["metadata"]["validation_repair"]
    encoded_metadata = json.dumps(block["metadata"], ensure_ascii=False)

    assert "repair_backlog" not in repair
    assert "block_design_constraints" not in repair
    assert "raw validation section" not in encoded_metadata
    assert repair["raw_sections_omitted"] is True
    assert repair["discipline_ids"] == ["walk_forward_analysis"]
    assert repair["entry_biases"] == ["waiting_probe"]
    assert repair["risk_budget_multiplier"] == pytest.approx(0.25)
    assert len(encoded_metadata) < 2600


def test_binance_block_events_store_compact_payloads(tmp_path: Path) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    large_metadata = {
        "applied_policy_versions": [f"policy_{index}" for index in range(200)],
        "raw_candidate_packet": "candidate evidence " * 5000,
        "nested": {"raw": "nested evidence " * 5000},
    }

    block = repo.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "short",
            "qty": 0.05,
            "entry_price": 100.0,
            "target_price": 92.0,
            "stop_price": 104.0,
            "status": "proposed",
            "metadata": large_metadata,
        }
    )
    repo.update_block(block["block_id"], {"metadata": large_metadata})
    repo.claim_entry_pending(
        block["block_id"],
        reason="test entry",
        payload={"block": block, "raw_candidate_packet": "entry evidence " * 5000},
    )

    with sqlite3.connect(repo.path) as conn:
        rows = conn.execute(
            """
            SELECT event_type, length(payload_json), payload_json
            FROM block_events
            ORDER BY id
            """
        ).fetchall()

    assert [row[0] for row in rows] == ["created", "updated", "entry_claimed"]
    assert max(row[1] for row in rows) < 5000
    encoded = "\n".join(row[2] for row in rows)
    assert "candidate evidence" not in encoded
    assert "entry evidence" not in encoded
    assert "nested evidence" not in encoded
    created = json.loads(rows[0][2])
    assert created["block_id"] == block["block_id"]
    assert created["symbol"] == "BTCUSDT"
    assert created["metadata"]["_metadata_compacted"] is True


def test_binance_block_repository_lists_summaries_without_heavy_metadata(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    block = repo.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.05,
            "entry_price": 100.0,
            "target_price": 108.0,
            "stop_price": 96.0,
            "status": "closed",
            "metadata": {
                "horizon": "short",
                "block_color": "short",
                "lane": "volatile_attack",
                "raw_candidate_packet": "candidate evidence " * 10_000,
            },
        }
    )

    rows = repo.list_block_summaries(include_closed=True, limit=10)
    encoded = json.dumps(rows, ensure_ascii=False)

    assert rows[0]["block_id"] == block["block_id"]
    assert rows[0]["horizon"] == "short"
    assert rows[0]["block_color"] == "short"
    assert rows[0]["lane"] == "volatile_attack"
    assert "raw_candidate_packet" not in rows[0]["metadata"]
    assert "candidate evidence" not in encoded
    assert len(encoded) < 4000


class _FakeBinance:
    def __init__(self) -> None:
        self.prices: dict[str, float] = {"BTCUSDT": 100.0, "ETHUSDT": 200.0}
        self.spot_orders: list[dict[str, Any]] = []
        self.futures_orders: list[dict[str, Any]] = []
        self.account = {
            "status": "ok",
            "cash_usdt": 10_000.0,
            "positions": [],
        }

    async def fetch_quote(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "market": market,
            "price": self.prices[symbol],
            "source": "fake",
        }

    async def fetch_account_snapshot(self) -> dict[str, Any]:
        return self.account

    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        return {"order_id": f"S{len(self.spot_orders)}", **kwargs}

    async def submit_futures_order(self, **kwargs: Any) -> dict[str, Any]:
        self.futures_orders.append(kwargs)
        return {"order_id": f"F{len(self.futures_orders)}", **kwargs}


class _NoSubmitBinance:
    def __init__(self) -> None:
        self.prices: dict[str, float] = {"BTCUSDT": 100.0}
        self.account = {
            "status": "ok",
            "cash_usdt": 10_000.0,
            "positions": [],
        }

    async def fetch_quote(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "market": market,
            "price": self.prices[symbol],
            "source": "fake",
        }

    async def fetch_account_snapshot(self) -> dict[str, Any]:
        return self.account


class _ExpiredExitBinance(_FakeBinance):
    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        return {"status": "EXPIRED", "order_id": f"S{len(self.spot_orders)}", **kwargs}


class _ExpiredFuturesExitBinance(_FakeBinance):
    async def submit_futures_order(self, **kwargs: Any) -> dict[str, Any]:
        self.futures_orders.append(kwargs)
        return {
            "status": "EXPIRED",
            "executedQty": "0",
            "expiryReason": "UNFILLED_IOC_QUANTITY_EXPIRED",
            "order_id": f"F{len(self.futures_orders)}",
            **kwargs,
        }


class _FailingExitBinance(_FakeBinance):
    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        raise RuntimeError("temporary binance outage")


class _InsufficientBalanceExitBinance(_FakeBinance):
    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        raise RuntimeError(
            "binance spot post failed: {'code': -2010, "
            "'msg': 'Account has insufficient balance for requested action.'}"
        )


class _ShrinkingBalanceExitBinance(_FakeBinance):
    def __init__(self) -> None:
        super().__init__()
        self.accounts: list[dict[str, Any]] = []

    async def fetch_account_snapshot(self) -> dict[str, Any]:
        if self.accounts:
            return self.accounts.pop(0)
        return self.account

    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        if len(self.spot_orders) == 1:
            raise RuntimeError(
                "binance spot post failed: {'code': -2010, "
                "'msg': 'Account has insufficient balance for requested action.'}"
            )
        return {
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "order_id": f"S{len(self.spot_orders)}",
            **kwargs,
        }


class _FailingAccountSnapshotBinance(_FakeBinance):
    async def fetch_account_snapshot(self) -> dict[str, Any]:
        raise AssertionError("compact status must not fetch a live account snapshot")


class _FillingEntryBinance(_FakeBinance):
    def __init__(self) -> None:
        super().__init__()
        self.margin_calls: list[dict[str, Any]] = []
        self.leverage_calls: list[dict[str, Any]] = []

    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        return {
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "order_id": f"S{len(self.spot_orders)}",
            **kwargs,
        }

    async def submit_futures_order(self, **kwargs: Any) -> dict[str, Any]:
        self.futures_orders.append(kwargs)
        return {
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "order_id": f"F{len(self.futures_orders)}",
            **kwargs,
        }

    async def set_futures_margin_type(self, **kwargs: Any) -> dict[str, Any]:
        self.margin_calls.append(kwargs)
        return {"status": "ok", **kwargs}

    async def set_futures_leverage(self, **kwargs: Any) -> dict[str, Any]:
        self.leverage_calls.append(kwargs)
        return {"status": "ok", **kwargs}


class _AvgPriceFillingEntryBinance(_FillingEntryBinance):
    def __init__(self, avg_price: float) -> None:
        super().__init__()
        self.avg_price = avg_price

    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        return {
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "order_id": f"S{len(self.spot_orders)}",
            "raw": {
                "avgPrice": str(self.avg_price),
                "executedQty": str(kwargs["quantity"]),
            },
            **kwargs,
        }

    async def submit_futures_order(self, **kwargs: Any) -> dict[str, Any]:
        self.futures_orders.append(kwargs)
        return {
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "order_id": f"F{len(self.futures_orders)}",
            "raw": {
                "avgPrice": str(self.avg_price),
                "executedQty": str(kwargs["quantity"]),
            },
            **kwargs,
        }


class _CostHistoryBinance(_FakeBinance):
    def __init__(self) -> None:
        super().__init__()
        self.trade_calls: list[dict[str, Any]] = []
        self.income_calls: list[dict[str, Any]] = []

    async def fetch_futures_user_trades(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.trade_calls.append({"symbol": symbol, **kwargs})
        return [
            {
                "id": 10,
                "orderId": kwargs.get("order_id"),
                "symbol": symbol,
                "price": "90.0",
                "qty": "2.0",
                "commission": "0.07",
                "commissionAsset": "USDT",
            }
        ]

    async def fetch_futures_income_history(
        self,
        symbol: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.income_calls.append({"symbol": symbol, **kwargs})
        return [
            {
                "symbol": symbol,
                "incomeType": "FUNDING_FEE",
                "income": "-0.03",
                "asset": "USDT",
            }
        ]


class _BookTickerFillingEntryBinance(_FillingEntryBinance):
    def __init__(self) -> None:
        super().__init__()
        self.book_tickers: dict[tuple[str, str], dict[str, float]] = {}
        self.book_calls: list[tuple[str, str]] = []

    async def fetch_book_ticker(
        self,
        symbol: str,
        *,
        market: str = "spot",
    ) -> dict[str, Any]:
        self.book_calls.append((market, symbol))
        book = self.book_tickers[(market, symbol)]
        return {
            "symbol": symbol,
            "market": market,
            "bid": book["bid"],
            "ask": book["ask"],
            "source": "fake_book",
        }


class _SequencedBookTickerFillingEntryBinance(_BookTickerFillingEntryBinance):
    def __init__(self) -> None:
        super().__init__()
        self.book_sequences: dict[tuple[str, str], list[dict[str, float]]] = {}

    async def fetch_book_ticker(
        self,
        symbol: str,
        *,
        market: str = "spot",
    ) -> dict[str, Any]:
        key = (market, symbol)
        if key not in self.book_sequences:
            return await super().fetch_book_ticker(symbol, market=market)
        self.book_calls.append(key)
        books = self.book_sequences[key]
        book = books.pop(0) if len(books) > 1 else books[0]
        return {
            "symbol": symbol,
            "market": market,
            "bid": book["bid"],
            "ask": book["ask"],
            "source": "fake_book_sequence",
        }


class _ExpiringEntryBookTickerBinance(_BookTickerFillingEntryBinance):
    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.spot_orders.append(kwargs)
        return {
            "status": "EXPIRED",
            "executedQty": "0",
            "expiryReason": "UNFILLED_IOC_QUANTITY_EXPIRED",
            "order_id": f"S{len(self.spot_orders)}",
            **kwargs,
        }


class _FilteredFillingEntryBinance(_FillingEntryBinance):
    async def fetch_futures_exchange_filters(self, symbol: str) -> dict[str, dict[str, Any]]:
        assert symbol in {"BNBUSDT", "LTCUSDT", "LINKUSDT"}
        if symbol in {"LTCUSDT", "LINKUSDT"}:
            return {
                "PRICE_FILTER": {"tickSize": "0.01"},
                "LOT_SIZE": {"minQty": "0.01", "stepSize": "0.01"},
                "MIN_NOTIONAL": {"notional": "20"},
            }
        return {
            "PRICE_FILTER": {"tickSize": "0.01"},
            "LOT_SIZE": {"minQty": "0.01", "stepSize": "0.01"},
            "MIN_NOTIONAL": {"notional": "5"},
        }


class _FuturesMinNotionalResponseErrorBinance(_FillingEntryBinance):
    async def fetch_futures_exchange_filters(self, symbol: str) -> dict[str, dict[str, Any]]:
        assert symbol == "LINKUSDT"
        return {}

    async def submit_futures_order(self, **kwargs: Any) -> dict[str, Any]:
        self.futures_orders.append(kwargs)
        if kwargs["quantity"] < 2.0:
            return {
                "status": "error",
                "error_message": "order notional below minimum: LINKUSDT 9.93636 < 20",
                **kwargs,
            }
        return {
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "order_id": f"F{len(self.futures_orders)}",
            **kwargs,
        }


class _SpotFilteredFillingEntryBinance(_FillingEntryBinance):
    async def fetch_spot_exchange_filters(self, symbol: str) -> dict[str, dict[str, Any]]:
        assert symbol == "BTCUSDT"
        return {
            "PRICE_FILTER": {"tickSize": "0.0001"},
            "LOT_SIZE": {"minQty": "0.1", "stepSize": "0.1"},
            "MIN_NOTIONAL": {"notional": "5"},
        }


class _FakeUpbit:
    def __init__(self) -> None:
        self.prices: dict[str, float] = {"KRW-BTC": 100_000_000.0}
        self.orders: list[dict[str, Any]] = []
        self.assets: list[dict[str, Any]] = [
            {
                "asset": "KRW",
                "symbol": "KRW",
                "market": "upbit_spot",
                "kind": "cash",
                "qty": 1_000_000.0,
                "available": 1_000_000.0,
                "locked": 0.0,
                "value_krw": 1_000_000.0,
            }
        ]

    async def fetch_balance_assets(self) -> list[dict[str, Any]]:
        return list(self.assets)

    async def fetch_spot_quote(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "market": "upbit_spot",
            "price": self.prices[symbol],
            "source": "fake_upbit",
        }

    async def fetch_book_ticker(self, symbol: str, *, market: str = "upbit_spot") -> dict[str, Any]:
        price = self.prices[symbol]
        return {
            "symbol": symbol,
            "market": market,
            "bid": price - 1_000,
            "ask": price,
            "price": price - 500,
            "spread_bps": 0.1,
            "source": "fake_upbit_book",
        }

    async def fetch_exchange_filters(self, symbol: str, *, market: str = "upbit_spot") -> dict[str, dict[str, Any]]:
        _ = (symbol, market)
        return {
            "PRICE_FILTER": {"tickSize": "1000"},
            "LOT_SIZE": {"minQty": "0.00000001", "stepSize": "0.00000001"},
            "MIN_NOTIONAL": {"minNotional": "5000"},
        }

    async def submit_spot_order(self, **kwargs: Any) -> dict[str, Any]:
        self.orders.append(kwargs)
        return {
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "order_id": f"U{len(self.orders)}",
            **kwargs,
        }


class _CountingQuoteBinance(_FakeBinance):
    def __init__(self) -> None:
        super().__init__()
        self.quote_calls: list[tuple[str, str]] = []

    async def fetch_quote(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        self.quote_calls.append((market, symbol))
        return await super().fetch_quote(symbol, market=market)


class _FailingQuoteBinance(_FakeBinance):
    def __init__(self) -> None:
        super().__init__()
        self.prices = {}

    async def fetch_quote(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        _ = (symbol, market)
        raise RuntimeError("temporary quote timeout")


class _FakeLLM:
    ready = True
    resolved_model = "fake-model"

    def __init__(self, payload: dict[str, Any], *, include_lane_review: bool = True) -> None:
        self.payload = payload
        self.include_lane_review = include_lane_review
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"payload": payload, "kwargs": kwargs})
        response = dict(self.payload)
        if self.include_lane_review and "lane_review" not in response:
            response["lane_review"] = {
                "dominant_lane": "spot:long",
                "selected_lanes": [],
                "lanes_reviewed": [
                    "spot:long",
                    "futures:long",
                    "futures:short",
                    "upbit_spot:long",
                    "volatile_attack",
                ],
                "non_selected_lane_reasons": {},
                "concentration_note": "test fixture reviewed all lanes",
                "exploration_watch": [],
            }
        return response


class _FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(
        self,
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        _ = (parse_mode, chat_id)
        self.messages.append(text)
        return {"ok": True, "message_id": len(self.messages)}


class _FakeCompleteOnlyLLM:
    ready = True
    resolved_model = "fake-complete-model"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"payload": payload, "timeout_ms": timeout_ms})
        response = dict(self.payload)
        if "lane_review" not in response:
            response["lane_review"] = {
                "dominant_lane": "spot:long",
                "selected_lanes": [],
                "lanes_reviewed": [
                    "spot:long",
                    "futures:long",
                    "futures:short",
                    "upbit_spot:long",
                    "volatile_attack",
                ],
                "non_selected_lane_reasons": {},
                "concentration_note": "test fixture reviewed all lanes",
                "exploration_watch": [],
            }
        return response


class _NoCompleteLLM:
    ready = True
    resolved_model = "no-complete-model"


def _trader(
    tmp_path: Path,
    *,
    adapter: _FakeBinance | None = None,
    llm: _FakeLLM | None = None,
    memory_provider: Any | None = None,
    wiki_context_provider: Any | None = None,
    quant_provider: Any | None = None,
    quant_context_limit: int = 16,
    execute_spot: bool = False,
    execute_futures: bool = False,
    execute_upbit: bool = False,
    enabled: bool = False,
    telegram: Any | None = None,
    upbit: Any | None = None,
) -> BinanceBlockTrader:
    return BinanceBlockTrader(
        config=BinanceBlockTraderConfig(
            db_path=str(tmp_path / "binance_blocks.db"),
            live_performance_db_path=str(tmp_path / "live_performance.db"),
            enabled=enabled,
            execute_spot_orders=execute_spot,
            execute_futures_orders=execute_futures,
            execute_upbit_orders=execute_upbit,
            spot_universe="BTCUSDT,ETHUSDT",
            futures_universe="BTCUSDT,ETHUSDT",
            upbit_universe="KRW-BTC,KRW-ETH",
            quant_context_limit=quant_context_limit,
            llm_timeout_ms=123000,
            max_futures_leverage=2,
            min_liquidation_distance_pct=12.0,
            upbit_usdt_krw_rate=1_000.0,
        ),
        adapter=adapter or _FakeBinance(),
        upbit=upbit,
        codex_runtime=llm or _FakeLLM({"create_blocks": []}),
        memory_provider=memory_provider
        or (lambda **kwargs: {"status": "ok", "symbols": kwargs.get("symbols", [])}),
        wiki_context_provider=wiki_context_provider,
        quant_provider=quant_provider,
        telegram=telegram,
    )


def test_binance_status_exposes_latest_proactive_decision_pressure(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
            "decision_inputs": ["account", "proactive_decision_pressure"],
            "execution_gate": {
                "status": "ok",
                "live_venues": ["spot", "futures", "upbit_spot"],
                "kill_switch": {"enabled": False},
                "cash_available": {
                    "spot_cash_usdt": 125.0,
                    "futures_cash_usdt": 250.0,
                    "upbit_cash_krw": 100_000.0,
                    "total_equity_usdt": 447.0,
                },
                "active_block_count": 3,
                "waiting_entry_block_count": 2,
                "pending_order_block_count": 1,
            },
            "proactive_decision_pressure": {
                "status": "action_required",
                "pressure_level": "high",
                "zero_action_streak": 2,
                "candidate_count": 12,
                "strong_candidate_count": 5,
            },
            "prompt_budget": {
                "status": "ok",
                "total_chars": 12_345,
                "target_chars": 70_000,
                "warn_chars": 90_000,
                "max_chars": 190_000,
            },
        },
        response={
            "hold_decision": {
                "summary": "강한 후보를 더 미루면 기회비용이 커진다.",
                "watch_symbols": ["BTCUSDT", "SOLUSDT"],
            }
        },
        actions={"create_blocks": [{"symbol": "BTCUSDT", "market": "futures"}]},
        model="gpt-5.5",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["proactive_pressure_status"] == "action_required"
    assert latest["proactive_pressure_level"] == "high"
    assert latest["proactive_zero_action_streak"] == 2
    assert latest["proactive_candidate_count"] == 12
    assert latest["proactive_strong_candidate_count"] == 5
    assert latest["action_count"] == 1
    assert latest["hold_watch_symbols"] == ["BTCUSDT", "SOLUSDT"]
    assert latest["prompt_budget"]["total_chars"] == 12_345
    assert latest["execution_gate_status"] == "ok"
    assert latest["live_venues"] == ["spot", "futures", "upbit_spot"]
    assert latest["kill_switch_enabled"] is False
    assert latest["spot_cash_usdt"] == pytest.approx(125.0)
    assert latest["futures_cash_usdt"] == pytest.approx(250.0)
    assert latest["upbit_cash_krw"] == pytest.approx(100_000.0)
    assert latest["total_equity_usdt"] == pytest.approx(447.0)
    assert latest["active_block_count"] == 3
    assert latest["waiting_entry_block_count"] == 2
    assert latest["pending_order_block_count"] == 1


def test_binance_status_preserves_latest_wiki_attention_summary(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
            "decision_inputs": ["jue_wiki_repair_contract"],
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
        },
        response={
            "hold_decision": {
                "summary": "SOL 위키 메모리 카드 품질을 다음 틱에서 재확인",
                "watch_symbols": ["SOLUSDT"],
                "data_gaps": ["memory_card_quality_unresolved"],
            }
        },
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        model="gpt-5.5",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["jue_wiki_attention"] == {
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
                        "refresh_durable_facts_from_reports_fundamentals_"
                        "and_market_context"
                    ),
                    "record_open_questions_and_data_gaps_before_confident_action",
                ],
            }
        ],
        "resolution_status": "hold_trigger",
    }


def test_binance_status_preserves_latest_memory_card_quality_summary(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
            "decision_inputs": ["jue_wiki_memory_card_quality"],
            "jue_wiki_memory_card_quality": {
                "summary": {
                    "status": "weak",
                    "weak_symbols": ["SOLUSDT"],
                    "weak_count": 1,
                },
                "action_plan": {
                    "status": "active",
                    "decision_policy": (
                        "do_not_overtrust_thin_requested_symbol_memory_cards"
                    ),
                    "required_action": (
                        "cross_check_live_research_before_high_confidence"
                    ),
                    "reason": "requested_symbol_memory_cards_are_thin",
                    "symbols": ["SOLUSDT"],
                    "missing_fields_by_symbol": [
                        {
                            "symbol": "SOLUSDT",
                            "status": "weak",
                            "missing_fields": [
                                "durable_facts",
                                "open_questions",
                            ],
                        }
                    ],
                    "required_checks": [
                        (
                            "refresh_durable_facts_from_reports_fundamentals_"
                            "and_market_context"
                        ),
                        "record_open_questions_and_data_gaps_before_confident_action",
                    ],
                },
            },
        },
        response={
            "hold_decision": {
                "summary": "SOL 위키 메모리 카드가 얇아 라이브 근거를 재확인",
                "watch_symbols": ["SOLUSDT"],
                "data_gaps": ["memory_card_quality_unresolved"],
            }
        },
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        model="gpt-5.5",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["jue_wiki_memory_card_quality"] == {
        "status": "active",
        "weak_symbols": ["SOLUSDT"],
        "required_action": "cross_check_live_research_before_high_confidence",
        "decision_policy": "do_not_overtrust_thin_requested_symbol_memory_cards",
        "reason": "requested_symbol_memory_cards_are_thin",
        "missing_fields_by_symbol": [
            {
                "symbol": "SOLUSDT",
                "status": "weak",
                "missing_fields": ["durable_facts", "open_questions"],
            }
        ],
        "required_checks": [
            "refresh_durable_facts_from_reports_fundamentals_and_market_context",
            "record_open_questions_and_data_gaps_before_confident_action",
        ],
        "resolution_status": "hold_trigger",
    }


def test_binance_status_rejects_memory_card_quality_resolution_on_wrong_symbol(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
            "decision_inputs": ["jue_wiki_memory_card_quality"],
            "jue_wiki_memory_card_quality": {
                "summary": {
                    "status": "weak",
                    "weak_symbols": ["SOLUSDT"],
                    "weak_count": 1,
                },
                "action_plan": {
                    "status": "active",
                    "symbols": ["SOLUSDT"],
                    "required_action": (
                        "cross_check_live_research_before_high_confidence"
                    ),
                },
            },
        },
        response={"hold_decision": {"summary": "action only"}},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "metadata": {
                        "jue_wiki_memory_card_quality": {
                            "resolution": "cross_checked_SOLUSDT_memory_card",
                        }
                    },
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        model="gpt-5.5",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["jue_wiki_memory_card_quality"]["resolution_status"] == (
        "unresolved"
    )


def test_binance_status_rejects_memory_card_quality_hold_on_wrong_symbol(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
            "decision_inputs": ["jue_wiki_memory_card_quality"],
            "jue_wiki_memory_card_quality": {
                "summary": {
                    "status": "weak",
                    "weak_symbols": ["SOLUSDT"],
                    "weak_count": 1,
                },
                "action_plan": {
                    "status": "active",
                    "symbols": ["SOLUSDT"],
                    "required_action": (
                        "cross_check_live_research_before_high_confidence"
                    ),
                },
            },
        },
        response={
            "hold_decision": {
                "summary": "SOLUSDT gap mentioned, but BTC trigger only",
                "watch_symbols": ["BTCUSDT"],
                "data_gaps": ["SOLUSDT durable_facts"],
                "next_triggers": [
                    {
                        "symbol": "BTCUSDT",
                        "condition": "SOLUSDT durable_facts 문구가 섞인 BTC 트리거",
                    }
                ],
            }
        },
        actions={"create_blocks": [], "update_blocks": [], "close_blocks": []},
        model="gpt-5.5",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["jue_wiki_memory_card_quality"]["resolution_status"] == (
        "unresolved"
    )


def test_binance_status_surfaces_candidate_memory_hint_contract_error(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt=_binance_memory_hint_contract_prompt(),
        response={
            "contract_error": (
                "candidate_memory_hint_resolution_missing_from_model"
            )
        },
        actions={
            "create_blocks": [{"symbol": "BTCUSDT", "thesis": "breakout"}],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        status="error",
        mode="contract_error",
        model="gpt-5.5",
        error_message="candidate_memory_hint_resolution_missing_from_model",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["memory_contract"] == {
        "status": "error",
        "contract": "cite_or_reject_candidate_memory_hint",
        "error": "candidate_memory_hint_resolution_missing_from_model",
        "memory_packet_count": 1,
        "impacted_symbols": ["BTCUSDT"],
        "resolution_status": "missing",
    }


def test_binance_status_surfaces_validation_repair_memory_contract_error(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
            "validation_repair": {
                "scope": "binance",
                "status": "needs_repair",
                "repair_item_count": 1,
                "constraint_count": 1,
                "memory_contracts": ["cite_or_reject_candidate_memory_hint"],
                "memory_contract_errors": [
                    "candidate_memory_hint_resolution_missing_from_model"
                ],
                "impacted_symbols": ["BTCUSDT"],
                "required_checks": ["require_memory_contract_resolution"],
                "repair_backlog": [
                    {
                        "discipline_id": "memory_contract",
                        "memory_contract": "cite_or_reject_candidate_memory_hint",
                        "memory_contract_error": (
                            "candidate_memory_hint_resolution_missing_from_model"
                        ),
                        "impacted_symbols": ["BTCUSDT"],
                    }
                ],
            }
        },
        response={
            "contract_error": "memory_contract_resolution_missing_from_model"
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        status="error",
        mode="contract_error",
        model="gpt-5.5",
        error_message="memory_contract_resolution_missing_from_model",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["memory_contract"] == {
        "status": "error",
        "contract": "cite_or_reject_candidate_memory_hint",
        "error": "memory_contract_resolution_missing_from_model",
        "memory_contract_errors": [
            "candidate_memory_hint_resolution_missing_from_model"
        ],
        "memory_packet_count": 1,
        "impacted_symbols": ["BTCUSDT"],
        "resolution_status": "missing",
        "source": "validation_repair",
    }


def test_binance_status_surfaces_validation_repair_memory_contract_resolution(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
            "validation_repair": {
                "scope": "binance",
                "status": "needs_repair",
                "memory_contracts": ["cite_or_reject_candidate_memory_hint"],
                "memory_contract_errors": [
                    "candidate_memory_hint_resolution_missing_from_model"
                ],
                "impacted_symbols": ["BTCUSDT"],
                "required_checks": ["require_memory_contract_resolution"],
            }
        },
        response={
            "validation_repair_resolution": {
                "resolved_candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "resolution": "probe_waiting_block",
                        "memory_contract": "cite_or_reject_candidate_memory_hint",
                        "memory_contract_error": (
                            "candidate_memory_hint_resolution_missing_from_model"
                        ),
                        "memory_contract_resolution": (
                            "cite_memory_and_apply: 급등 추격 손실 메모리를 반영해 "
                            "즉시 진입 대신 눌림 대기 선물 블록으로 낮춘다."
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
        status="ok",
        mode="llm",
        model="gpt-5.5",
        error_message="",
    )

    latest = trader.status()["latest_decision_input"]

    assert latest["memory_contract"]["status"] == "resolved"
    assert latest["memory_contract"]["resolution_status"] == "resolved"
    assert latest["memory_contract"]["resolved_candidates"] == [
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "resolution": "probe_waiting_block",
            "memory_contract": "cite_or_reject_candidate_memory_hint",
            "memory_contract_error": (
                "candidate_memory_hint_resolution_missing_from_model"
            ),
            "memory_contract_resolution": (
                "cite_memory_and_apply: 급등 추격 손실 메모리를 반영해 즉시 진입 대신 "
                "눌림 대기 선물 블록으로 낮춘다."
            ),
        }
    ]


def test_binance_manager_prompt_contains_jue_workflow_pack(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    wiki_calls: list[dict] = []

    def wiki_context_provider(**kwargs) -> dict:
        wiki_calls.append(kwargs)
        return {
            "status": "ok",
            "selection_run_id": "selection:binance-test",
            "target_scope": kwargs.get("target_scope"),
            "prompt_mode": "assist",
            "symbols": kwargs.get("symbols", []),
            "content": "Binance wiki context",
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
                    "rank": 1,
                    "score": 77.0,
                    "freshness": "current",
                    "freshness_status": "stale",
                    "freshness_warnings": ["updated_at_stale_gt_14d"],
                    "selection_reasons": ["scope_match:binance"],
                    "selection_penalties": [],
                    "char_count": 21,
                    "source_refs": [],
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "BTCUSDT",
                    "page_id": "binance.symbol.BTCUSDT",
                    "freshness": "current",
                    "freshness_status": "stale",
                    "freshness_warnings": ["updated_at_stale_gt_14d"],
                    "summary": "BTCUSDT stale memory",
                }
            ],
            "rejected_pages": [],
            "budget_report": {"status": "ok", "max_chars": 24000},
        }

    trader = _trader(tmp_path, llm=llm, wiki_context_provider=wiki_context_provider)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    assert result["status"] == "ok"
    prompt = llm.calls[0]["payload"]
    assert prompt["jue_workflow"]["workflow_id"] == "binance_cycle"
    wiki = prompt["jue_wiki"]
    assert wiki["status"] == "ok"
    assert wiki["selection_run_id"]
    assert wiki["target_scope"] == "binance"
    assert "BTCUSDT" in wiki["symbols"]
    assert wiki["pages"]
    assert wiki["budget_report"]["status"] == "ok"
    assert "selection_reasons" in wiki["pages"][0]
    assert wiki["freshness_summary"] == {
        "page_count": 1,
        "status_counts": {"stale": 1},
        "warning_counts": {"updated_at_stale_gt_14d": 1},
        "stale_page_ids": ["binance:playbook"],
        "unknown_page_ids": [],
    }
    assert wiki["pages"][0]["freshness_status"] == "stale"
    assert wiki["pages"][0]["freshness_warnings"] == ["updated_at_stale_gt_14d"]
    assert wiki["requested_symbol_summaries"][0]["freshness_status"] == "stale"
    assert wiki["requested_symbol_summaries"][0]["freshness_warnings"] == [
        "updated_at_stale_gt_14d"
    ]
    assert prompt["jue_wiki_budget_report"]["status"] == "ok"
    assert prompt["jue_wiki_budget_report"]["max_chars"] == trader.config.prompt_max_chars
    assert (
        prompt["jue_wiki_budget_report"]["total_chars"]
        <= prompt["jue_wiki_budget_report"]["max_chars"]
    )
    assert wiki_calls[0]["target_scope"] == "binance"
    assert "BTCUSDT" in wiki_calls[0]["symbols"]
    assert wiki_calls[0]["max_chars"] == trader.config.jue_wiki_context_max_chars
    assert "risk" in wiki_calls[0]["page_types"]
    assert "playbook" in wiki_calls[0]["page_types"]
    assert "ops" in wiki_calls[0]["page_types"]
    assert "research" in wiki_calls[0]["page_types"]
    assert "performance" in wiki_calls[0]["page_types"]
    assert "futures:short" in wiki_calls[0]["lanes"]
    assert "spot:long" in wiki_calls[0]["lanes"]
    assert prompt["policy"]["live_authority_policy"]["hard_filters"] is False
    assert (
        prompt["policy"]["live_authority_policy"]["server_enforced_gates"]
        == "blocked/research_only/error validation states and exchange/risk gates"
    )
    assert (
        "small waiting-entry probe"
        in prompt["policy"]["live_authority_policy"]["probe_mandate"]
    )
    assert (
        prompt["jue_workflow"]["model_policy"]["expected_runtime_model"]
        == "gpt-5.5"
    )
    assert prompt["jue_workflow"]["cadence"]["interval_sec"] == trader.config.manager_interval_sec
    assert (
        prompt["jue_workflow"]["cadence"]["runtime_interval_sec"]
        == trader.config.manager_interval_sec
    )
    skill_ids = {row["skill_id"] for row in prompt["jue_workflow"]["skills"]}
    assert {"crypto_market_sweep", "block_design", "risk_sizing"}.issubset(skill_ids)


def test_binance_manager_observe_mode_attaches_wiki_observation_only(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})

    def wiki_context_provider(**kwargs) -> dict:
        return {
            "status": "ok",
            "selection_run_id": "selection:binance-observe",
            "target_scope": kwargs.get("target_scope"),
            "prompt_mode": "observe",
            "symbols": kwargs.get("symbols", []),
            "content": "Binance wiki context",
            "pages": [
                {
                    "page_id": "binance:playbook",
                    "rank": 1,
                    "score": 77.0,
                    "selection_reasons": ["scope_match:binance"],
                    "selection_penalties": [],
                    "char_count": 21,
                    "source_refs": [],
                }
            ],
            "rejected_pages": [],
            "budget_report": {"status": "ok", "max_chars": 24000},
        }

    trader = _trader(tmp_path, llm=llm, wiki_context_provider=wiki_context_provider)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert "jue_wiki" not in prompt
    observation = prompt["jue_wiki_selection_observation"]
    assert observation["status"] == "ok"
    assert observation["selection_run_id"]
    assert observation["prompt_mode"] == "observe"
    assert observation["budget_report"]["status"] == "ok"
    assert observation.get("content", "") == ""
    assert "content" not in observation["pages"][0]
    assert "jue_wiki_budget_report" not in prompt


def test_binance_manager_accepts_no_arg_legacy_wiki_provider(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    calls = 0

    def wiki_context_provider() -> dict:
        nonlocal calls
        calls += 1
        return {
            "status": "ok",
            "target_scope": "legacy",
            "symbols": ["legacy"],
            "content": "legacy binance wiki context",
        }

    trader = _trader(tmp_path, llm=llm, wiki_context_provider=wiki_context_provider)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert calls == 1
    assert prompt["jue_wiki"]["status"] == "ok"
    assert prompt["jue_wiki"]["content"] == "legacy binance wiki context"


def test_binance_manager_primary_mode_marks_wiki_as_evidence_policy(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})

    def wiki_context_provider(**kwargs) -> dict:
        return {
            "status": "ok",
            "selection_run_id": "selection:binance-primary",
            "target_scope": kwargs.get("target_scope"),
            "prompt_mode": "primary",
            "symbols": kwargs.get("symbols", []),
            "content": "Binance primary wiki context",
            "pages": [
                {
                    "page_id": "binance:playbook",
                    "rank": 1,
                    "score": 77.0,
                    "selection_reasons": ["scope_match:binance"],
                    "selection_penalties": [],
                    "char_count": 31,
                    "source_refs": [],
                }
            ],
            "rejected_pages": [],
            "budget_report": {"status": "ok", "max_chars": 24000},
        }

    trader = _trader(tmp_path, llm=llm, wiki_context_provider=wiki_context_provider)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["jue_wiki"]["primary_context"] is True
    assert prompt["jue_wiki"]["raw_context_policy"] == "evidence_only"
    assert prompt["jue_wiki_primary_context_policy"]["raw_context_policy"] == (
        "evidence_only"
    )


def test_binance_market_scope_aliases_preserve_futures_lane() -> None:
    assert normalize_market("binance_futures") == "futures"
    assert normalize_market("binance-futures") == "futures"
    assert normalize_market("binance:futures") == "futures"
    assert normalize_market("binance_futures_account") == "futures"
    assert normalize_market("binance:futures:account") == "futures"
    assert normalize_market("um_futures") == "futures"
    assert normalize_market("binance_spot") == "spot"


def test_binance_block_trader_does_not_reown_symbol_and_lane_helpers() -> None:
    source = Path(binance_block_trader_module.__file__).read_text()

    for marker in (
        "def _normalize_market(",
        "def _explicit_market_scope(",
        "def _is_upbit_market(",
        "def _upbit_market_symbol(",
        "def _upbit_market_to_usdt_symbol(",
        "def _normalize_position_side(",
        "def _normalize_binance_horizon(",
        "def _raw_binance_horizon_requests_futures(",
        "def _binance_block_lane(",
        "def _normalize_binance_display_lane(",
        "def _binance_market_side_lane(",
        "def _row_price_value(",
        "def _row_pattern_live_crosscheck_status(",
        "def _prices_within_bps(",
        "def _parse_universe(",
    ):
        assert marker not in source


def test_binance_block_trader_does_not_reown_order_math_helpers() -> None:
    source = Path(binance_block_trader_module.__file__).read_text()

    for marker in (
        "def _safe_decimal(",
        "def _quantize_to_step(",
        "def _min_notional_from_filters(",
        "def _candidate_last_price(",
        "def _round_candidate_price(",
        "def _candidate_volatility_pct(",
        "def _candidate_stop_pct(",
        "def _reward_risk(",
    ):
        assert marker not in source


def test_binance_block_trader_does_not_reown_gate_and_executor_wrappers() -> None:
    source = Path(binance_block_trader_module.__file__).read_text()

    for marker in (
        "def _remaining_exit_qty(",
        "def _exit_reason(",
        "def _exit_order_side(",
        "def _response_filled_qty(",
        "def _entry_fill_price_update_fields(",
        "def _response_error_message(",
        "def _is_min_notional_error_response(",
        "def _response_order_id(",
        "def _entry_not_filled_reason(",
        "def _entry_order_side(",
        "def _is_waiting_entry_block(",
        "def _entry_trigger_fired(",
        "def _normalize_entry_trigger_operator(",
        "_normalize_entry_quality_label =",
        "_entry_quality_label_from_payload =",
        "def _entry_gate_shadow_only_entry_qualities(",
        "def _wait_pullback_confirmation_rejection(",
        "def _entry_quality_gate_check(",
    ):
        assert marker not in source


def test_contract_payload_uses_futures_candidate_when_scope_is_binance_futures(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader._last_manager_candidate_index = {
        "WLDUSDT:spot": {
            "symbol": "BTCUSDT",
            "market": "spot",
            "venue": "spot",
            "side": "long",
            "horizon": "short",
            "source_id": "candidate:WLDUSDT:spot_shadow",
            "block_template": {"market": "spot", "side": "long"},
            "entry_price": 0.62,
            "target_price": 0.64,
            "stop_price": 0.61,
        },
        "WLDUSDT:futures": {
            "symbol": "BTCUSDT",
            "market": "futures",
            "venue": "futures",
            "side": "long",
            "horizon": "futures",
            "source_id": "candidate:WLDUSDT:futures_shadow",
            "block_template": {"market": "futures", "side": "long"},
            "entry_price": 0.61583,
            "target_price": 0.63061,
            "stop_price": 0.60844,
        },
    }

    row = trader._normalize_manager_contract_create_payload(
        {
            "decision": "create_waiting_probe",
            "symbol": "BTCUSDT",
            "market_or_account_scope": "binance_futures",
            "source_id": "candidate:WLDUSDT:futures_shadow",
            "side": "long",
            "horizon": "futures",
            "entry_price": 0.61583,
            "target_price": 0.63061,
            "stop_price": 0.60844,
            "quantity_or_quote_budget": 49.63,
        }
    )

    assert row["market"] == "futures"
    assert row["venue"] == "futures"
    assert row["block_template"]["market"] == "futures"
    assert row["horizon"] == "futures"
    assert row["metadata"]["manager_contract_defaults_used"] is True


def test_contract_scope_overrides_conflicting_spot_market_for_futures_short(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader._last_manager_candidate_index = {
        "ETHUSDT:futures:short": {
            "symbol": "ETHUSDT",
            "market": "futures",
            "venue": "futures",
            "side": "short",
            "horizon": "futures",
            "source_id": "candidate:ETHUSDT:futures_short",
            "block_template": {"market": "futures", "side": "short"},
            "entry_price": 1800.0,
            "target_price": 1764.0,
            "stop_price": 1818.0,
        }
    }

    row = trader._normalize_manager_contract_create_payload(
        {
            "decision": "create_waiting_probe",
            "symbol": "ETHUSDT",
            "market": "spot",
            "market_or_account_scope": "binance:futures",
            "source_id": "candidate:ETHUSDT:futures_short",
            "horizon": "short",
            "entry_price": 1800.0,
            "target_price": 1764.0,
            "stop_price": 1818.0,
            "quantity_or_quote_budget": 49.63,
            "thesis": "Use a 1x isolated futures short waiting-entry probe.",
        }
    )

    assert row["market"] == "futures"
    assert row["venue"] == "futures"
    assert row["side"] == "short"
    assert row["horizon"] == "futures"
    assert row["block_template"]["market"] == "futures"


def test_spot_block_normalization_pins_market_side_and_cleans_horizon(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)

    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.01,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "status": "proposed",
            "horizon": "futures",
            "metadata": {
                "horizon": "futures",
                "lane": "short",
            },
        }
    )

    assert block["market"] == "spot"
    assert block["side"] == "long"
    assert block["metadata"]["market"] == "spot"
    assert block["metadata"]["side"] == "long"
    assert block["metadata"]["horizon"] == "short"
    assert block["metadata"]["lane"] == "short"


def test_block_storage_compacts_live_authority_metadata_bloat(
    tmp_path: Path,
) -> None:
    marker = "BINANCE_STORAGE_LIVE_AUTHORITY_RAW_BLOAT"
    trader = _trader(tmp_path)

    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.01,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "metadata": {
                "live_authority": {
                    "status": "ok",
                    "live_grade": "restricted",
                    "lane_authority": {
                        "max_budget_multiplier": 0.25,
                        "weak_lanes": ["futures:short"],
                        "lane_actions": {
                            f"futures:short:{idx}": {
                                "action": "validation_waiting_probe",
                                "budget_multiplier": 0.25,
                                "requires_waiting_entry": True,
                                "active_revision_gate": {
                                    "status": "blocked",
                                    "raw": marker * 20,
                                },
                                "raw": marker * 20,
                            }
                            for idx in range(30)
                        },
                    },
                    "trading_validation": {
                        "payload": {"raw_diagnostics": marker * 80}
                    },
                }
            },
        }
    )

    metadata_text = json.dumps(block["metadata"], ensure_ascii=False)
    live_authority = block["metadata"]["live_authority"]
    assert marker not in metadata_text
    assert len(metadata_text) < 12_000
    assert live_authority["status"] == "ok"
    assert live_authority["lane_authority"]["lane_action_count"] == 30
    assert len(live_authority["lane_authority"]["lane_actions"]) <= 6


def test_manager_rejects_spot_action_with_futures_horizon_conflict(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.01,
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                    "thesis": "Use a 1x isolated futures long, but this payload says spot.",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "market_horizon_conflict:spot:futures"
    assert trader.list_blocks() == []


def test_binance_manager_prompt_and_snapshot_include_growth_target(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 1_000.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
    }
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    snapshot = asyncio.run(trader.snapshot())

    assert result["status"] == "ok"
    prompt = llm.calls[0]["payload"]
    assert "growth_target" in prompt["decision_inputs"]
    assert prompt["growth_target"]["monthly_target_pct"] == pytest.approx(50.0)
    assert prompt["growth_target"]["basis"] == "account_equity_monthly_run_rate"
    assert prompt["growth_target"]["current_equity_usdt"] == pytest.approx(1_000.0)
    assert prompt["growth_target"]["target_equity_usdt"] == pytest.approx(1_500.0)
    assert snapshot["growth_target"]["current_equity_usdt"] == pytest.approx(1_000.0)
    assert snapshot["growth_target"]["basis"] == "account_equity_monthly_run_rate"


def test_binance_status_uses_latest_manager_account_when_memory_snapshot_missing(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
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
        },
        response={},
        actions={},
        status="ok",
    )
    fresh_control_instance = _trader(tmp_path)

    status = fresh_control_instance.status()

    assert status["risk_guard"]["status"] == "ok"
    assert status["risk_guard"]["current_equity_usdt"] == pytest.approx(1_500.0)
    assert status["growth_governor"]["metrics"]["risk_guard_status"] == "ok"


def test_binance_status_recovers_recent_manager_account_when_latest_run_lacks_account(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
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
        },
        response={},
        actions={},
        status="ok",
    )
    trader.repository.save_manager_run(
        prompt={
            "prompt_budget": {"total_chars": 18_000},
            "latency_guard": {"active": True},
        },
        response={},
        actions={},
        status="error",
        error_message="codex native sdk timed out after 420.0s",
    )
    fresh_control_instance = _trader(tmp_path)

    status = fresh_control_instance.status()

    assert status["risk_guard"]["status"] == "ok"
    assert status["risk_guard"]["current_equity_usdt"] == pytest.approx(1_500.0)
    assert status["growth_governor"]["metrics"]["risk_guard_status"] == "ok"


def test_binance_compact_snapshot_uses_latest_manager_account_when_memory_snapshot_missing(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={
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
        },
        response={},
        actions={},
        status="ok",
    )
    fresh_control_instance = _trader(tmp_path)

    snapshot = asyncio.run(fresh_control_instance.snapshot_compact())

    assert snapshot["account"]["spot_cash_usdt"] == pytest.approx(1_000.0)
    assert snapshot["growth_target"]["current_equity_usdt"] == pytest.approx(1_500.0)
    assert snapshot["risk_guard"]["status"] == "ok"
    assert snapshot["growth_governor"]["metrics"]["risk_guard_status"] == "ok"


def test_binance_compact_snapshot_collects_account_when_cache_is_empty(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 250.0,
        "futures_cash_usdt": 750.0,
        "positions": [],
    }
    trader = _trader(tmp_path, adapter=adapter)

    snapshot = asyncio.run(trader.snapshot_compact())

    assert snapshot["account"]["spot_cash_usdt"] == pytest.approx(250.0)
    assert snapshot["account"]["futures_cash_usdt"] == pytest.approx(750.0)
    assert snapshot["risk_guard"]["status"] == "ok"
    assert snapshot["risk_guard"]["current_equity_usdt"] == pytest.approx(1_000.0)
    assert snapshot["growth_governor"]["metrics"]["risk_guard_status"] == "ok"


def test_binance_snapshot_collects_account_before_growth_baseline_without_persisting_snapshots(
    tmp_path: Path,
) -> None:
    stale_writer = _trader(tmp_path)
    stale_writer.repository.save_manager_run(
        prompt={
            "account": {
                "status": "ok",
                "spot_cash_usdt": 100.0,
                "futures_cash_usdt": 0.0,
            },
            "risk_guard": {
                "status": "ok",
                "current_equity_usdt": 100.0,
                "allow_new_entries": True,
            },
        },
        response={},
        actions={},
        status="ok",
    )
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 1_000.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
    }
    fresh_control_instance = _trader(tmp_path, adapter=adapter)

    snapshot = asyncio.run(fresh_control_instance.snapshot())
    with sqlite3.connect(tmp_path / "binance_blocks.db") as conn:
        month_starts = [
            row[0]
            for row in conn.execute(
                "SELECT start_equity_usdt FROM growth_target_months"
            ).fetchall()
        ]
        snapshot_equities = [
            row[0]
            for row in conn.execute(
                "SELECT current_equity_usdt FROM growth_target_snapshots"
            ).fetchall()
        ]

    assert snapshot["risk_guard"]["current_equity_usdt"] == pytest.approx(1_000.0)
    assert snapshot["growth_target"]["start_equity_usdt"] == pytest.approx(1_000.0)
    assert month_starts == [pytest.approx(1_000.0)]
    assert snapshot_equities == []


def test_binance_status_does_not_persist_growth_target_snapshots(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader._last_account_snapshot = {
        "status": "ok",
        "spot_cash_usdt": 1_000.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
    }

    first = trader.status()
    second = trader.status()

    assert first["growth_target"]["current_equity_usdt"] == pytest.approx(1_000.0)
    assert second["growth_target"]["current_equity_usdt"] == pytest.approx(1_000.0)
    with sqlite3.connect(tmp_path / "binance_blocks.db") as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM growth_target_snapshots"
        ).fetchone()[0]
    assert count == 0


def test_binance_snapshot_does_not_persist_growth_target_snapshots(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 1_000.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
    }
    trader = _trader(tmp_path, adapter=adapter)

    first = asyncio.run(trader.snapshot())
    second = asyncio.run(trader.snapshot())

    assert first["growth_target"]["current_equity_usdt"] == pytest.approx(1_000.0)
    assert second["growth_target"]["current_equity_usdt"] == pytest.approx(1_000.0)
    with sqlite3.connect(tmp_path / "binance_blocks.db") as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM growth_target_snapshots"
        ).fetchone()[0]
    assert count == 0


def test_binance_account_equity_ignores_krw_scaled_total_value_usdt(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    account = {
        "status": "ok",
        "total_value_usdt": 1_405_600.0,
        "spot_cash_usdt": 1_000.0,
        "upbit_usdt_krw_rate": 1_400.0,
        "upbit_spot_assets": [
            {
                "kind": "position",
                "symbol": "KRW-BTC",
                "value_krw": 1_400_000.0,
            }
        ],
    }

    assert trader._account_equity_usdt(account) == pytest.approx(2_000.0)


def test_binance_normalized_account_preserves_upbit_fx_rate(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    account = _normalize_test_account(
        trader,
        {
            "status": "ok",
            "spot_cash_usdt": 1_000.0,
            "upbit_spot_assets": [
                {
                    "kind": "position",
                    "symbol": "KRW-BTC",
                    "value_krw": 1_400_000.0,
                }
            ],
        }
    )

    assert account["upbit_usdt_krw_rate"] == pytest.approx(
        trader.config.upbit_usdt_krw_rate
    )
    assert trader._account_equity_usdt(account) == pytest.approx(
        1_000.0 + 1_400_000.0 / trader.config.upbit_usdt_krw_rate
    )


def test_binance_manager_prompt_enforces_payload_budget(tmp_path: Path) -> None:
    marker = "BINANCE_PROMPT_BLOAT_MARKER"

    def memory_provider(**kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return {
            "status": "ok",
            "notes": [{"summary": marker * 200} for _ in range(40)],
            "policy_rules": [{"body": marker * 120} for _ in range(30)],
        }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm, memory_provider=memory_provider)
    heavy_candidate = {
        "symbol": "BTCUSDT",
        "market": "futures",
        "side": "short",
        "summary": marker * 300,
        "calculated_price_plan": {"basis": marker * 200},
        "evidence": [marker * 80 for _ in range(80)],
    }

    result = asyncio.run(
        trader.run_manager_once(candidates=[heavy_candidate for _ in range(40)])
    )

    assert result["status"] == "ok"
    prompt = llm.calls[0]["payload"]
    prompt_text = json.dumps(prompt, ensure_ascii=False)
    assert prompt["prompt_budget"]["version"] == "prompt_budget_v1"
    assert prompt["prompt_budget"]["target_chars"] == trader.config.prompt_target_chars
    assert prompt["prompt_budget"]["warn_chars"] == trader.config.prompt_warn_chars
    assert prompt["prompt_budget"]["max_chars"] == trader.config.prompt_max_chars
    assert "over_warn" in prompt["prompt_budget"]
    assert prompt["prompt_budget"]["over_max"] is False
    assert len(prompt_text) <= prompt["prompt_budget"]["max_chars"]
    assert marker not in prompt_text


def test_binance_status_exposes_manager_prompt_budget(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    status = trader.status()

    assert status["config"]["prompt_target_chars"] == trader.config.prompt_target_chars
    assert status["config"]["prompt_warn_chars"] == trader.config.prompt_warn_chars
    assert status["config"]["prompt_max_chars"] == trader.config.prompt_max_chars


def test_binance_manager_prompt_budget_never_marks_candidates_omittable() -> None:
    assert (
        "candidates"
        not in binance_block_trader_module.BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS
    )
    assert (
        "candidate_generation"
        not in binance_block_trader_module.BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS
    )
    assert (
        "market_universe"
        not in binance_block_trader_module.BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS
    )


def test_binance_manager_prompt_uses_compact_live_authority_validation_focus(
    tmp_path: Path,
) -> None:
    raw_marker = "BINANCE_LIVE_AUTHORITY_RAW_VALIDATION_MARKER"
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.5,
        "active_revision_evidence": {
            "version": "active_revision_evidence_v1",
            "venue": "binance",
            "strategy_revision_id": "jue_edge_repair_v1",
            "status": "no_active_revision_samples",
            "authority_posture": "observe_only_until_new_revision_trades_close",
            "effective_sample_count": 0,
            "validation_sample_count": 0,
            "lane_alpha_count": 0,
            "min_samples_to_scale": 20,
            "scorecard_count": 0,
            "performance_lane_count": 0,
            "validation_fail_count": 0,
            "validation_missing_count": 16,
            "hard_blocking_count": 3,
            "scale_up_allowed": False,
            "block_design_requirement": (
                "새 revision Binance 표본이 쌓일 때까지 대기진입/소액 검증만 허용"
            ),
            "raw_rows": ["BINANCE_ACTIVE_REVISION_RAW_ROWS"] * 100,
        },
        "trading_validation": {
            "payload": {
                "raw_diagnostics": raw_marker * 40,
            }
        },
        "validation_gate": {
            "status": "blocked_by_validation",
            "readiness": "blocked_by_validation",
            "reason": "readiness=blocked_by_validation, fail_count=6",
            "risk_governor_action": "halt_new_risk",
            "failed_disciplines": [
                {
                    "id": "monte_carlo",
                    "label": "몬테카를로 시뮬레이션",
                    "status": "fail",
                    "action": "sequence risk 축소",
                }
            ],
            "loss_cooldown": {
                "symbols": [
                    {
                        "symbol": "ZECUSDT",
                        "risk_score": 55.14,
                        "action": "do_not_scale_or_create_live_entry_without_new_evidence",
                    }
                ]
            },
            "operator_guidance": ["몬테카를로: sequence risk 축소"],
            "remediation_plan": {
                "status": "blocked",
                "primary_next_action": "Binance rolling WFA 재생성 후 OOS 재검증",
                "lane_policy_hints": {
                    "scale_up_allowed": False,
                    "entry_mode": "verified_waiting_probe",
                },
                "work_queue": [
                    {
                        "task_id": "validation:walk_forward_analysis:fail",
                        "discipline_id": "walk_forward_analysis",
                        "owner": "pattern_lab",
                        "lane_policy_hint": "shadow_or_waiting_only_until_wfa_rebuilt",
                        "blocks_scaling": "no_scale_up_until_wfa_oos_clean",
                        "exit_criteria": "active rolling WFA rebuilt",
                    }
                ],
                "categories": [
                    {
                        "id": "research_validation_work",
                        "items": [
                            {
                                "discipline_id": "walk_forward_analysis",
                                "status": "fail",
                                "action": "Binance rolling WFA 재생성",
                            }
                        ],
                        "decision_adjustment_metrics": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "repair_probe",
                                "reason": "current_risk_posture_degraded",
                                "status": "active",
                                "sample_count": 5,
                                "avg_return_pct": 0.24,
                                "confidence": 0.75,
                            }
                        ],
                    }
                ],
            },
        },
        "performance_lanes": [
            {
                "venue": "binance",
                "lane": "futures:short",
                "block_count": 7,
                "alpha_count": 7,
                "expectancy_pct": -0.25,
                "win_rate_pct": 28.57,
                "profit_factor": 0.72,
                "max_drawdown_pct": -3.1,
                "recovery_factor": 0.0,
                "cost_drag_pct_of_abs_gross_pnl": 67.5,
                "quality_hint": "weak_review",
                "action_hint": "reduce_or_wait_for_better_entry_quality",
            }
        ],
    }

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    prompt = llm.calls[0]["payload"]
    prompt_text = json.dumps(prompt, ensure_ascii=False)
    gate = prompt["live_authority"]["validation_gate"]
    active_evidence = prompt["live_authority"]["active_revision_evidence"]
    assert result["status"] == "ok"
    assert raw_marker not in prompt_text
    assert "BINANCE_ACTIVE_REVISION_RAW_ROWS" not in prompt_text
    assert "trading_validation" not in prompt["live_authority"]
    assert active_evidence["strategy_revision_id"] == "jue_edge_repair_v1"
    assert active_evidence["status"] == "no_active_revision_samples"
    assert active_evidence["min_samples_to_scale"] == 20
    assert active_evidence["scale_up_allowed"] is False
    assert gate["status"] == "blocked_by_validation"
    assert gate["failed_disciplines"][0]["id"] == "monte_carlo"
    assert gate["loss_cooldown"]["symbols"][0]["symbol"] == "ZECUSDT"
    assert gate["remediation_plan"]["primary_next_action"] == (
        "Binance rolling WFA 재생성 후 OOS 재검증"
    )
    assert gate["remediation_plan"]["lane_policy_hints"]["entry_mode"] == (
        "verified_waiting_probe"
    )
    assert gate["remediation_plan"]["work_queue"][0]["owner"] == "pattern_lab"
    assert gate["remediation_plan"]["work_queue"][0]["blocks_scaling"] == (
        "no_scale_up_until_wfa_oos_clean"
    )
    assert prompt["live_authority"]["performance_lanes"][0]["lane"] == "futures:short"
    assert (
        prompt["live_authority"]["performance_lanes"][0]["quality_hint"]
        == "weak_review"
    )
    assert "better_entry_quality" in (
        prompt["live_authority"]["performance_lanes"][0]["action_hint"]
    )
    assert "[truncated:" not in json.dumps(prompt["live_authority"], ensure_ascii=False)


def test_manager_prompt_budget_preserves_broad_candidates_when_trimming_context() -> None:
    marker = "BINANCE_CONTEXT_BLOAT"
    prompt = {
        "candidates": [
            {
                "symbol": f"T{index:02d}USDT",
                "market": "spot",
                "side": "long",
                "horizon": "short",
                "summary": f"candidate {index}",
                "calculated_price_plan": {
                    "basis": "compact calculated entry/target/stop",
                    "evidence": ["fresh_book", "quant", "regime"],
                },
            }
            for index in range(30)
        ],
        "memory": {"notes": [{"summary": marker * 200} for _ in range(30)]},
        "blocks": [{"thesis": marker * 180} for _ in range(30)],
        "decision_packet": {"evidence": [{"summary": marker * 120} for _ in range(30)]},
    }

    enforce_prompt_budget(
        prompt,
        max_chars=190_000,
    )

    assert len(prompt["candidates"]) == 30
    assert marker not in json.dumps(prompt, ensure_ascii=False)


def test_binance_candidate_policy_impacts_deduplicates_global_impacts_for_large_universe() -> None:
    global_policy = {
        "policy_id": "global-loss-cooldown",
        "matched_metric": {
            "group_type": "strategy_family",
            "group": "late_chase",
        },
        "decision_guidance": "Do not scale unless new live evidence repairs the setup.",
    }
    memory_context = {
        "policy_rule_evaluation": {
            "global": [global_policy],
            "by_symbol": {
                "BTCUSDT": [
                    {
                        "policy_id": "btc-specific",
                        "decision_guidance": "BTC-specific condition.",
                    }
                ],
            },
        }
    }
    symbols = [f"T{i:02d}USDT" for i in range(40)] + ["BTCUSDT"]

    impacts = binance_block_trader_module._binance_candidate_policy_impacts(
        memory_context,
        symbols,
    )

    assert impacts["_global"][0]["policy_id"] == "global-loss-cooldown"
    assert "T00USDT" not in impacts
    assert impacts["BTCUSDT"][0]["policy_id"] == "btc-specific"
    assert json.dumps(impacts, ensure_ascii=False).count("global-loss-cooldown") == 1


def test_manager_prompt_budget_caps_policy_impacts_before_hard_limit() -> None:
    marker = "BINANCE_POLICY_IMPACT_NORMAL_BLOAT"
    prompt = {
        "candidates": [
            {
                "symbol": f"T{index:02d}USDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0 + index,
                "target_price": 96.0 + index,
                "stop_price": 102.0 + index,
            }
            for index in range(12)
        ],
        "blocks": [
            {
                "block_id": "blk-active",
                "symbol": "T20USDT",
                "market": "spot",
                "side": "long",
                "status": "open",
                "entry_price": 2.0,
                "target_price": 2.2,
                "stop_price": 1.9,
            }
        ],
        "candidate_policy_impacts": {
            "_global": [
                {
                    "policy_id": "global-crypto-risk",
                    "decision_guidance": marker * 8,
                }
            ],
            **{
                f"T{index:02d}USDT": [
                    {
                        "policy_id": f"T{index:02d}-policy-{row_index}",
                        "decision_guidance": marker * 8,
                        "effect": {
                            "entry_bias": "wait_for_cleaner_book",
                            "target_stop_review": marker * 4,
                        },
                    }
                    for row_index in range(4)
                ]
                for index in range(80)
            },
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=120_000,
        warn_chars=160_000,
        max_chars=190_000,
    )

    impacts = prompt["candidate_policy_impacts"]
    symbol_keys = [key for key in impacts if key != "_global"]
    assert prompt["prompt_budget"]["over_max"] is False
    assert len(symbol_keys) <= 24
    assert "T00USDT" in impacts
    assert "T11USDT" in impacts
    assert "T20USDT" in impacts
    assert "T79USDT" not in impacts
    assert (
        prompt_chars(
            {"candidate_policy_impacts": impacts}
        )
        <= 24_000
    )


def test_manager_prompt_budget_bounds_many_small_policy_impact_fields() -> None:
    policy_row = {
        "policy_id": "dense-policy",
        "decision_guidance": "prefer cleaner entries",
        "effect": {
            f"metric_{index:02d}": round(index * 0.1234, 4)
            for index in range(40)
        },
        "matched_metric": {
            f"sample_{index:02d}": f"value-{index:02d}"
            for index in range(40)
        },
        **{
            f"diagnostic_{index:02d}": f"diagnostic-value-{index:02d}"
            for index in range(40)
        },
    }
    prompt = {
        "candidates": [
            {
                "symbol": f"T{index:02d}USDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0 + index,
                "target_price": 96.0 + index,
                "stop_price": 102.0 + index,
            }
            for index in range(30)
        ],
        "candidate_policy_impacts": {
            f"T{index:02d}USDT": [
                {**policy_row, "policy_id": f"T{index:02d}-policy-{row_index}"}
                for row_index in range(4)
            ]
            for index in range(80)
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=120_000,
        warn_chars=160_000,
        max_chars=190_000,
    )

    impacts = prompt["candidate_policy_impacts"]
    encoded = json.dumps({"candidate_policy_impacts": impacts}, ensure_ascii=False)
    assert prompt["prompt_budget"]["over_max"] is False
    assert len([key for key in impacts if key != "_global"]) <= 24
    assert len(encoded) <= 24_000
    assert "diagnostic_39" not in encoded
    assert "_omitted_count" in encoded


def test_manager_prompt_latency_guard_compacts_policy_impacts_after_timeout(
    tmp_path: Path,
) -> None:
    symbols = [f"T{index:02d}USDT" for index in range(60)]
    marker = "BINANCE_LATENCY_POLICY_IMPACT_BLOAT"

    def memory_provider(**kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return {
            "status": "ok",
            "policy_rule_evaluation": {
                "global": [
                    {
                        "policy_id": "global-latency-test",
                        "decision_guidance": marker * 12,
                    }
                ],
                "by_symbol": {
                    symbol: [
                        {
                            "policy_id": f"{symbol}-policy-{row_index}",
                            "decision_guidance": marker * 12,
                            "effect": {
                                "entry_bias": "wait_for_cleaner_book",
                                "target_stop_review": "review bracket",
                            },
                        }
                        for row_index in range(4)
                    ]
                    for symbol in symbols
                },
            },
        }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm, memory_provider=memory_provider)
    trader.repository.save_manager_run(
        prompt={"prompt_budget": {"total_chars": 137_000}},
        response={},
        actions={},
        status="error",
        error_message="codex native sdk timed out after 600.0s",
        model="gpt-5.5",
    )
    trader.repository.save_manager_run(
        prompt={"prompt_budget": {"total_chars": 141_000}},
        response={},
        actions={},
        status="error",
        error_message="manager_task_timeout_after_630s",
        model="gpt-5.5",
    )
    trader.repository.save_manager_run(
        prompt={"prompt_budget": {"total_chars": 139_000}},
        response={},
        actions={},
        status="error",
        error_message="manager_task_timeout_after_630s",
        model="gpt-5.5",
    )
    candidates = [
        {
            "symbol": symbol,
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "score": 80 - index * 0.1,
            "confidence": 0.72,
            "entry_price": 100.0 + index,
            "target_price": 96.0 + index,
            "stop_price": 102.0 + index,
            "liquidation_price": 140.0 + index,
        }
        for index, symbol in enumerate(symbols)
    ]

    result = asyncio.run(trader.run_manager_once(candidates=candidates))

    assert result["status"] == "ok"
    prompt = llm.calls[0]["payload"]
    assert prompt["latency_guard"]["active"] is True
    assert prompt["latency_guard"]["reason"] == "recent_manager_timeout"
    assert prompt["latency_guard"]["target_chars"] == 24_000
    assert prompt["prompt_budget"]["total_chars"] <= 24_000
    assert len(prompt["candidate_policy_impacts"]) <= 25
    assert "T00USDT" in prompt["candidate_policy_impacts"]
    assert "T59USDT" not in prompt["candidate_policy_impacts"]
    assert marker not in json.dumps(prompt, ensure_ascii=False)


def test_manager_created_block_persists_policy_effect_audit(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 106.0,
                    "stop_price": 97.0,
                    "confidence": 0.78,
                    "thesis": "memory policy audit test",
                }
            ]
        }
    )
    memory_context = {
        "status": "ok",
        "policy_rule_evaluation": {
            "status": "ok",
            "global": [
                {
                    "policy_id": "avoid_tight_stops_after_losses",
                    "rule_id": "avoid_tight_stops_after_losses@v2",
                    "status": "active_caution",
                    "effect": {
                        "stop_bias": "wider_after_loss_cluster",
                        "entry_bias": "wait_for_pullback",
                        "risk_note": "최근 손실 군집 뒤에는 손절 폭과 진입 품질을 더 엄격히 본다.",
                    },
                }
            ],
            "by_symbol": {},
            "by_block": {},
        },
    }
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: memory_context,
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks()[0]
    assert block["status"] == "proposed"
    assert block["metadata"]["entry_trigger_price"] == pytest.approx(100.0)
    assert block["metadata"]["entry_trigger_operator"] == "<="
    assert block["metadata"]["policy_effect_enforcement"]["adjustments"][0] == {
        "rule_id": "avoid_tight_stops_after_losses@v2",
        "field": "entry_style",
        "from": "immediate",
        "to": "wait_for_price",
        "entry_trigger_price": 100.0,
        "entry_trigger_price_from": 0.0,
        "method": "explicit_price",
        "effect_key": "reference_entry_price",
    }
    assert block["metadata"]["applied_policy_versions"] == [
        "avoid_tight_stops_after_losses@v2"
    ]
    assert block["metadata"]["policy_effect_audit"] == {
        "version": "policy_effect_audit_v1",
        "mode": "advisory",
        "affected_fields": ["entry_style", "risk_note", "stop_price"],
        "rules": [
            {
                "rule_id": "avoid_tight_stops_after_losses@v2",
                "policy_id": "avoid_tight_stops_after_losses",
                "status": "active_caution",
                "affected_fields": ["entry_style", "risk_note", "stop_price"],
                "effect_keys": ["entry_bias", "risk_note", "stop_bias"],
            }
        ],
    }


def test_manager_created_block_persists_growth_governor_for_tick_profit_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "entry_style": "wait_for_price",
                    "qty": 0.5,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                    "confidence": 0.82,
                    "thesis": "weak growth governor profit protection test",
                }
            ]
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm)

    def fake_growth_governor(**_: Any) -> dict[str, Any]:
        return {
            "version": "test_growth_governor_v1",
            "status": "edge_rebuild",
            "mode": "edge_rebuild",
            "allow_new_blocks": True,
            "max_new_blocks": 1,
            "require_waiting_entry": True,
            "weak_lanes": ["spot:long:short"],
        }

    monkeypatch.setattr(trader, "_growth_governor_context", fake_growth_governor)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )
    created = result["applied"]["created"][0]
    block = trader.get_block(created["block_id"])

    assert created["status"] == "waiting_entry"
    assert block is not None
    assert block["metadata"]["growth_governor"]["weak_lanes"] == ["spot:long:short"]

    trader.repository.update_block(
        block["block_id"],
        {
            "status": "open",
            "qty_open": 0.5,
            "opened_at": "2026-06-01T00:00:00+00:00",
        },
    )
    adapter.prices["BTCUSDT"] = 108.5

    tick = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert tick["status"] == "ok"
    assert tick["actions"][0]["status"] == "partial_profit_taken"
    assert tick["actions"][0]["trigger_source"] == "weak_performance_lane"
    assert updated is not None
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["stop_price"] > 100.0


def test_explicit_policy_qty_cap_reduces_binance_block_size(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.2,
                    "entry_price": 100.0,
                    "target_price": 106.0,
                    "stop_price": 97.0,
                    "confidence": 0.78,
                    "thesis": "memory policy qty cap test",
                }
            ]
        }
    )
    memory_context = {
        "status": "ok",
        "policy_rule_evaluation": {
            "status": "ok",
            "global": [
                {
                    "policy_id": "reduce_crypto_probe_after_cost_drag",
                    "rule_id": "reduce_crypto_probe_after_cost_drag@v1",
                    "status": "active_caution",
                    "effect": {
                        "qty_cap": 0.1,
                        "risk_note": "비용 drag가 큰 lane은 다음 블록을 절반 probe로 낮춘다.",
                    },
                }
            ],
            "by_symbol": {},
            "by_block": {},
        },
    }
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: memory_context,
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "created"
    block = trader.list_blocks()[0]
    assert block["qty_initial"] == pytest.approx(0.1)
    assert block["metadata"]["policy_effect_enforcement"]["adjustments"][0] == {
        "rule_id": "reduce_crypto_probe_after_cost_drag@v1",
        "field": "qty",
        "from": 0.2,
        "to": 0.1,
    }


def test_explicit_policy_budget_multiplier_reduces_binance_block_size(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.2,
                    "entry_price": 100.0,
                    "target_price": 106.0,
                    "stop_price": 97.0,
                    "confidence": 0.78,
                    "thesis": "validation budget multiplier policy test",
                }
            ]
        }
    )
    memory_context = {
        "status": "ok",
        "policy_rule_evaluation": {
            "status": "ok",
            "global": [
                {
                    "policy_id": "validation.binance.cost_simulation",
                    "rule_id": "validation.binance.cost_simulation@v1",
                    "status": "active_caution",
                    "effect": {
                        "max_budget_multiplier": 0.5,
                        "risk_note": "비용 검증이 약한 lane은 다음 블록을 절반 probe로 낮춘다.",
                    },
                }
            ],
            "by_symbol": {},
            "by_block": {},
        },
    }
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: memory_context,
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "created"
    block = trader.list_blocks()[0]
    assert block["qty_initial"] == pytest.approx(0.1)
    assert block["metadata"]["policy_effect_enforcement"]["adjustments"][0] == {
        "rule_id": "validation.binance.cost_simulation@v1",
        "field": "qty",
        "from": 0.2,
        "to": 0.1,
    }


def test_relative_policy_effect_reprices_binance_short_waiting_entry_target_stop(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 0.2,
                    "entry_price": 100.0,
                    "target_price": 94.0,
                    "stop_price": 103.0,
                    "confidence": 0.78,
                    "thesis": "memory policy reprices short block after failed entries",
                }
            ]
        }
    )
    memory_context = {
        "status": "ok",
        "policy_rule_evaluation": {
            "status": "ok",
            "global": [
                {
                    "policy_id": "repair_failed_short_chase_structure",
                    "rule_id": "repair_failed_short_chase_structure@v2",
                    "status": "active_caution",
                    "effect": {
                        "entry_pullback_pct": 2.0,
                        "stop_risk_pct": 3.0,
                        "target_reward_risk": 2.0,
                        "risk_note": "실패한 숏 추격 뒤에는 반등 대기와 2R 구조만 쓴다.",
                    },
                }
            ],
            "by_symbol": {},
            "by_block": {},
        },
    }
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: memory_context,
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks()[0]
    assert block["status"] == "proposed"
    assert block["entry_price"] == pytest.approx(102.0)
    assert block["stop_price"] == pytest.approx(105.06)
    assert block["target_price"] == pytest.approx(95.88)
    metadata = block["metadata"]
    assert metadata["entry_trigger_price"] == pytest.approx(102.0)
    assert metadata["entry_trigger_operator"] == ">="
    enforcement = metadata["policy_effect_enforcement"]
    assert [row["field"] for row in enforcement["adjustments"]] == [
        "entry_style",
        "stop_price",
        "target_price",
    ]
    assert enforcement["adjustments"][0]["effect_key"] == "entry_pullback_pct"
    assert enforcement["adjustments"][1]["effect_key"] == "stop_risk_pct"
    assert enforcement["adjustments"][2]["effect_key"] == "target_reward_risk"


def test_explicit_policy_waiting_entry_uses_reference_price_for_binance_trigger(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 106.0,
                    "stop_price": 97.0,
                    "confidence": 0.78,
                    "thesis": "memory policy waiting-entry rejection test",
                }
            ]
        }
    )
    memory_context = {
        "status": "ok",
        "policy_rule_evaluation": {
            "status": "ok",
            "global": [
                {
                    "policy_id": "wait_after_failed_breakouts",
                    "rule_id": "wait_after_failed_breakouts@v2",
                    "status": "active_caution",
                    "effect": {
                        "requires_waiting_entry": True,
                        "risk_note": "실패한 돌파 뒤에는 trigger 없는 즉시진입을 막는다.",
                    },
                }
            ],
            "by_symbol": {},
            "by_block": {},
        },
    }
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: memory_context,
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks()[0]
    assert block["status"] == "proposed"
    assert block["entry_price"] == pytest.approx(100.0)
    assert block["metadata"]["entry_trigger_price"] == pytest.approx(100.0)
    assert block["metadata"]["entry_trigger_operator"] == "<="
    enforcement = block["metadata"]["policy_effect_enforcement"]
    assert enforcement["adjustments"][0]["field"] == "entry_style"
    assert enforcement["adjustments"][0]["effect_key"] == "reference_entry_price"


def test_target_stop_review_policy_forces_binance_waiting_entry_reprice(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 106.0,
                    "stop_price": 97.0,
                    "confidence": 0.78,
                    "thesis": "target/stop 재검토 정책이 있는데 즉시 진입을 시도한다.",
                }
            ]
        }
    )
    memory_context = {
        "status": "ok",
        "policy_rule_evaluation": {
            "status": "ok",
            "global": [
                {
                    "policy_id": "reprice_after_weak_crypto_target_stop",
                    "rule_id": "reprice_after_weak_crypto_target_stop@v1",
                    "status": "active_caution",
                    "effect": {
                        "target_stop_review": (
                            "widen_expected_move_or_wait_for_price_improvement"
                        ),
                        "risk_note": (
                            "반성상 target/stop이 약하면 즉시진입 대신 "
                            "가격개선 대기블록으로 재가격한다."
                        ),
                    },
                }
            ],
            "by_symbol": {},
            "by_block": {},
        },
    }
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: memory_context,
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "waiting_entry"
    block = trader.list_blocks()[0]
    assert block["status"] == "proposed"
    assert block["entry_price"] == pytest.approx(100.0)
    assert block["metadata"]["entry_trigger_price"] == pytest.approx(100.0)
    assert block["metadata"]["entry_trigger_operator"] == "<="
    enforcement = block["metadata"]["policy_effect_enforcement"]
    assert enforcement["adjustments"][0]["field"] == "entry_style"
    assert enforcement["adjustments"][0]["effect_key"] == "reference_entry_price"


def test_explicit_policy_min_reward_risk_rejects_binance_weak_target_stop(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 103.0,
                    "stop_price": 97.0,
                    "confidence": 0.78,
                    "thesis": "memory policy reward/risk rejection test",
                }
            ]
        }
    )
    memory_context = {
        "status": "ok",
        "policy_rule_evaluation": {
            "status": "ok",
            "global": [
                {
                    "policy_id": "demand_asymmetric_crypto_entries",
                    "rule_id": "demand_asymmetric_crypto_entries@v1",
                    "status": "active_caution",
                    "effect": {
                        "min_reward_risk": 2.0,
                        "risk_note": "비용 drag 이후에는 최소 2R 이상 구조만 블록화한다.",
                    },
                }
            ],
            "by_symbol": {},
            "by_block": {},
        },
    }
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: memory_context,
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert trader.list_blocks() == []
    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == "policy_min_reward_risk_not_met"
    check = created["policy_effect_enforcement"]["checks"][0]
    assert check["reward_risk"] == pytest.approx(1.0)
    assert check["min_reward_risk"] == pytest.approx(2.0)


def test_manager_prompt_budget_removes_duplicate_candidate_price_plan_bloat() -> None:
    marker = "BINANCE_DUPLICATED_PRICE_PLAN_BLOAT"
    prompt = {
        "candidates": [
            {
                "symbol": f"T{index:02d}USDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "lane": "futures",
                "entry_price": 100.0 + index,
                "target_price": 95.0 + index,
                "stop_price": 103.0 + index,
                "entry_style": "wait_for_price",
                "entry_trigger_price": 100.0 + index,
                "entry_trigger_operator": ">=",
                "margin_type": "isolated",
                "leverage": 2,
                "liquidation_price": 150.0 + index,
                "calculated": {
                    "method_version": "test",
                    "lane": "futures",
                    "pattern_live_crosscheck": {"status": "aligned"},
                    "decision_notes": [marker * 80 for _ in range(30)],
                    "market_inputs": {f"k{x}": marker * 20 for x in range(140)},
                },
                "calculated_price_plan": {
                    "method_version": "test",
                    "lane": "futures",
                    "pattern_live_crosscheck": {"status": "aligned"},
                    "decision_notes": [marker * 80 for _ in range(30)],
                    "market_inputs": {f"k{x}": marker * 20 for x in range(140)},
                },
                "metadata": {
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 100.0 + index,
                    "entry_gate": {"diagnostics": marker * 400},
                    "pattern_live_crosscheck": {"status": "aligned", "raw": marker * 400},
                },
            }
            for index in range(30)
        ],
        "memory": {"notes": [{"summary": marker * 120} for _ in range(30)]},
        "blocks": [{"thesis": marker * 120} for _ in range(30)],
        "decision_packet": {"evidence": [{"summary": marker * 80} for _ in range(30)]},
    }

    enforce_prompt_budget(
        prompt,
        max_chars=190_000,
    )

    payload = json.dumps(prompt, ensure_ascii=False)
    assert len(payload) <= 187_500
    assert len(prompt["candidates"]) == 30
    assert prompt["candidates"][0]["symbol"] == "T00USDT"
    assert "calculated_price_plan" not in prompt["candidates"][0]
    assert prompt["candidates"][0]["metadata"]["entry_style"] == "wait_for_price"
    assert marker not in payload


def test_binance_prompt_budget_finalize_guarantees_attached_budget_under_max() -> None:
    marker = "BINANCE_FINALIZE_BUDGET_NEAR_LIMIT"
    prompt = {
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
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
    assert prompt["prompt_budget"]["over_max"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 90_000


def test_binance_prompt_budget_finalize_preserves_core_execution_context() -> None:
    marker = "BINANCE_FINALIZE_CORE_CONTEXT_MARKER"
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

    assert prompt["prompt_budget"]["over_max"] is False
    for section in ("account", "blocks", "live_authority"):
        value = prompt[section]
        assert not (
            isinstance(value, dict)
            and value.get("status") == "omitted_for_prompt_budget"
        ), section
        assert (
        section
        not in binance_block_trader_module.BINANCE_PROMPT_BUDGET_OMITTABLE_SECTIONS
    )


def test_binance_prompt_budget_keeps_compact_entry_gate_policy_under_pressure() -> None:
    marker = "BINANCE_ENTRY_GATE_POLICY_BLOAT"
    prompt = {
        "account": {"spot_cash_usdt": 300.0, "futures_cash_usdt": 500.0},
        "blocks": [
            {
                "block_id": "open-short",
                "symbol": "SUIUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "entry_price": 0.7163,
                "target_price": 0.6991,
                "stop_price": 0.71415,
            }
        ],
        "candidates": [
            {
                "symbol": f"C{idx:02d}USDT",
                "market": "spot" if idx % 2 else "futures",
                "side": "long" if idx % 3 else "short",
                "entry_price": 1.0 + idx,
                "target_price": 1.1 + idx,
                "stop_price": 0.96 + idx,
                "reason_md": marker * 40,
            }
            for idx in range(30)
        ],
        "entry_gate_policy": {
            "status": "active",
            "version": "binance_entry_gate_v1",
            "min_confidence": 0.58,
            "min_expected_r": 0.55,
            "cooldown_lane_keys": ["spot:long"],
            "cooldown_lanes": {
                "spot:long": {
                    "status": "cooldown",
                    "sample_count": 40,
                    "profit_factor": 0.0273,
                    "instruction": "Avoid fresh spot long entries until clean evidence repairs the lane.",
                    "raw_detail": marker * 60,
                }
            },
            "cooldown_symbol_keys": ["BIOUSDT", "BNBUSDT", "SOLUSDT"],
            "cooldown_symbols": {
                symbol: {
                    "status": "cooldown",
                    "sample_count": 6 + idx,
                    "profit_factor": 0.3,
                    "pnl_usdt": -1.5,
                    "instruction": "Avoid this symbol until a stronger edge appears.",
                    "raw_detail": marker * 60,
                }
                for idx, symbol in enumerate(["BIOUSDT", "BNBUSDT", "SOLUSDT"])
            },
            "entry_quality_cooldown_keys": ["spot:long:wait_pullback"],
            "entry_quality_cooldowns": {
                "spot:long:wait_pullback": {
                    "status": "cooldown",
                    "sample_count": 12,
                    "profit_factor": 0.25,
                    "recovery_required": "Do not repeat until fresh evidence offsets realized losses.",
                    "raw_detail": marker * 60,
                }
            },
            "waiting_entry_policy": {
                "enabled": True,
                "max_new_waiting_blocks_per_run": 1,
                "requires_price_trigger": True,
            },
            "raw_scorecards": [{"payload": marker * 120} for _ in range(80)],
        },
        "candidate_generation": {"raw": marker * 1000},
        "performance": {"raw": marker * 1000},
        "memory": {"raw": marker * 1000},
        "jue_workflow": {"instructions": marker * 1000},
        "output_schema": {"schema": marker * 1000},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=8_000,
        warn_chars=10_000,
        max_chars=18_000,
    )

    policy = prompt["entry_gate_policy"]
    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert prompt["prompt_budget"]["over_max"] is False
    assert not (
        isinstance(policy, dict)
        and policy.get("status") == "omitted_for_prompt_budget"
    )
    assert policy["status"] == "active"
    assert policy["cooldown_lane_keys"] == ["spot:long"]
    assert policy["cooldown_lanes"]["spot:long"]["profit_factor"] == pytest.approx(
        0.0273
    )
    assert policy["cooldown_symbol_keys"] == ["BIOUSDT", "BNBUSDT", "SOLUSDT"]
    assert policy["cooldown_symbols"]["BIOUSDT"]["status"] == "cooldown"
    assert policy["entry_quality_cooldown_keys"] == ["spot:long:wait_pullback"]
    assert (
        policy["entry_quality_cooldowns"]["spot:long:wait_pullback"]["sample_count"]
        == 12
    )
    assert policy["waiting_entry_policy"]["requires_price_trigger"] is True
    assert marker not in prompt_text


def test_binance_prompt_budget_finalize_removes_active_block_metadata_bloat() -> None:
    marker = "BINANCE_ACTIVE_BLOCK_METADATA_BLOAT"
    prompt = {
        "account": {"spot_cash_usdt": 1000.0, "futures_cash_usdt": 1000.0},
        "blocks": [
            {
                "block_id": f"block-{idx}",
                "symbol": "PAXGUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "qty_initial": 0.004,
                "qty_open": 0.004,
                "entry_price": 4150.0,
                "target_price": 4080.0,
                "stop_price": 4185.0,
                "thesis": "PAXG short thesis",
                "risk_note": "PAXG active short risk note",
                "metadata": {
                    "lane": "futures",
                    "risk_sizing": {
                        "status": "ok",
                        "lane": "futures",
                        "performance_multiplier": 0.5,
                        "risk_budget_usdt": 0.25,
                    },
                    "live_authority": {
                        f"raw_{inner}": {
                            "diagnostics": marker * 80,
                            "rows": [marker * 20 for _ in range(8)],
                        }
                        for inner in range(600)
                    },
                    "policy_rule_impacts": [
                        {"summary": marker * 80}
                        for _ in range(200)
                    ],
                },
            }
            for idx in range(2)
        ],
        "live_authority": {
            "status": "ok",
            "live_grade": "restricted",
            "lane_authority": {
                "max_budget_multiplier": 0.25,
                "weak_lanes": ["futures:short"],
                "lane_actions": {
                    f"futures:short:validation:{idx}": {
                        "action": "validation_evidence_repair_waiting_probe",
                        "budget_multiplier": 0.25,
                        "requires_waiting_entry": True,
                        "active_revision_gate": {
                            "status": "blocked",
                            "raw": marker * 120,
                        },
                        "raw": marker * 120,
                    }
                    for idx in range(400)
                },
            },
        },
        "candidates": [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "target_price": 90.0,
                "stop_price": 105.0,
            }
        ],
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
    assert marker not in prompt_text
    assert prompt["blocks"][0]["block_id"] == "block-0"
    assert prompt["blocks"][0]["symbol"] == "PAXGUSDT"
    assert prompt["blocks"][0]["metadata"]["risk_sizing"]["performance_multiplier"] == 0.5
    assert "live_authority" not in prompt["blocks"][0]["metadata"]
    assert "lane_actions" in prompt["live_authority"]["lane_authority"]
    assert len(prompt["live_authority"]["lane_authority"]["lane_actions"]) <= 8


def test_binance_prompt_budget_finalize_hard_caps_core_nested_bloat() -> None:
    marker = "BINANCE_CORE_NESTED_BLOAT"
    prompt = {
        "account": {"spot_cash_usdt": 1000.0, "futures_cash_usdt": 1000.0},
        "blocks": [
            {
                "block_id": f"block-{idx}",
                "symbol": "PAXGUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "qty_initial": 0.004,
                "qty_open": 0.004,
                "entry_price": 4150.0,
                "target_price": 4080.0,
                "stop_price": 4185.0,
                "metadata": {
                    "calculated_price_plan": {
                        "method_version": "test",
                        "pattern_live_crosscheck": {"status": "wait"},
                        "huge_inputs": {
                            f"raw_{inner}": {
                                "diagnostics": marker * 90,
                                "rows": [marker * 30 for _ in range(12)],
                            }
                            for inner in range(250)
                        },
                    },
                    "validation_evidence": {
                        f"raw_validation_{inner}": marker * 100
                        for inner in range(250)
                    },
                    "risk_sizing": {"status": "ok", "risk_budget_usdt": 0.25},
                },
            }
            for idx in range(8)
        ],
        "live_authority": {
            "status": "ok",
            "live_grade": "restricted",
            "failed_disciplines": [
                {
                    "id": f"discipline-{idx}",
                    "label": "stress",
                    "action": "stress evidence repair",
                    "evidence": {
                        f"raw_{inner}": marker * 80
                        for inner in range(200)
                    },
                }
                for idx in range(30)
            ],
            "lane_authority": {
                "weak_lanes": ["spot:long"],
                "lane_actions": {
                    f"spot:long:validation:{idx}": {
                        "action": "observe_or_waiting_entry",
                        "raw": marker * 150,
                    }
                    for idx in range(300)
                },
            },
        },
        "candidates": [
            {
                "symbol": f"T{idx:02d}USDT",
                "market": "spot",
                "side": "long",
                "entry_price": 1.0,
                "target_price": 1.1,
                "stop_price": 0.95,
                "reason_md": marker * 120,
            }
            for idx in range(60)
        ],
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
    assert prompt["prompt_budget"]["total_chars"] <= 90_000
    assert marker not in prompt_text
    assert prompt["blocks"][0]["block_id"] == "block-0"
    assert prompt["blocks"][0]["symbol"] == "PAXGUSDT"
    assert prompt["live_authority"]["status"] == "ok"


def test_binance_prompt_budget_finalize_prefers_warn_budget_when_possible() -> None:
    marker = "BINANCE_FINALIZE_WARN_BUDGET_MARKER"
    prompt = {
        "blocks": [
            {
                "block_id": f"blk_{idx}",
                "symbol": f"T{idx:02d}USDT",
                "market": "spot",
                "side": "long",
                "thesis": marker * 80,
                "risk_note": marker * 60,
            }
            for idx in range(18)
        ],
        "candidates": [
            {
                "symbol": f"T{idx:02d}USDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "reason_md": marker * 60,
                "calculated": {"notes": [marker * 30 for _ in range(6)]},
            }
            for idx in range(36)
        ],
        "memory": {"notes": [{"summary": marker * 60} for _ in range(14)]},
        "live_authority": {"rows": [{"summary": marker * 60} for _ in range(14)]},
        "performance": {"rows": [{"summary": marker * 60} for _ in range(10)]},
        "raw_context_refs": {
            "source": "large but omittable evidence reference",
            "payload": marker * 2_500,
        },
    }

    finalize_prompt_budget(
        prompt,
        target_chars=35_000,
        warn_chars=70_000,
        max_chars=120_000,
    )

    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    assert len(prompt_text) <= 70_000
    assert prompt["prompt_budget"]["over_warn"] is False
    assert prompt["prompt_budget"]["over_max"] is False


def test_binance_manager_prompt_over_max_fails_before_llm_call(tmp_path: Path) -> None:
    marker = "BINANCE_PROMPT_HARD_LIMIT_MARKER"

    def memory_provider(**kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return {
            "status": "ok",
            "notes": [{"summary": marker * 200} for _ in range(20)],
        }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm, memory_provider=memory_provider)
    trader.config.prompt_target_chars = 1_000
    trader.config.prompt_warn_chars = 1_000
    trader.config.prompt_max_chars = 1_000

    result = asyncio.run(trader.run_manager_once())
    latest = trader.repository.latest_manager_run()

    assert result["status"] == "error"
    assert "prompt_budget_exceeded" in result["error_message"]
    assert llm.calls == []
    assert latest["prompt"]["prompt_budget"]["over_max"] is True


def test_binance_manager_prompt_over_max_sends_telegram_alert(tmp_path: Path) -> None:
    marker = "BINANCE_PROMPT_TELEGRAM_LIMIT_MARKER"

    def memory_provider(**kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return {
            "status": "ok",
            "notes": [{"summary": marker * 200} for _ in range(20)],
        }

    llm = _FakeLLM({"create_blocks": []})
    telegram = _FakeTelegram()
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=memory_provider,
        telegram=telegram,
    )
    trader.config.prompt_target_chars = 1_000
    trader.config.prompt_warn_chars = 1_000
    trader.config.prompt_max_chars = 1_000

    result = asyncio.run(trader.run_manager_once())

    assert result["status"] == "error"
    assert llm.calls == []
    assert len(telegram.messages) == 1
    assert "Binance 쥬 판단 입력 상한 초과" in telegram.messages[0]
    events = trader.repository.list_events(block_id="__system__", limit=10)
    assert any(
        str(event.get("event_type") or "") == "telegram_manager_error_notified"
        for event in events
    )


def test_manager_contract_composite_create_decision_becomes_action(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "payload": {
                "decision": "create_blocks_and_close_stale",
                "symbol": "BTCUSDT",
                "market_or_account_scope": "futures",
                "horizon": "futures",
                "quantity_or_quote_budget": "0.001 BTC",
                "entry_price": 101.0,
                "target_price": 95.0,
                "stop_price": 104.0,
                "liquidation_price": 150.0,
                "claim": "BTCUSDT 선물 숏 대기 블록 생성",
                "reasons": ["BTCUSDT futures short candidate"],
                "next_actions": [
                    "close stale proposed block: bnb_futures_ETHUSDT_20260604120000000000"
                ],
                "evidence_refs": ["ev-btc"],
            }
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 2,
        "trading_validation": {
            "payload": {
                "summary": {
                    "total_score": 88.0,
                    "readiness": "scale_ready",
                    "pass_count": 19,
                    "warn_count": 0,
                    "fail_count": 0,
                    "missing_count": 0,
                },
                "disciplines": [
                    {"id": "data_validation", "label": "데이터 검증", "status": "pass"},
                    {"id": "monte_carlo", "label": "몬테카를로", "status": "pass"},
                ],
            }
        },
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
            "discipline_count": 19,
            "expected_discipline_count": 19,
        },
    }
    trader.repository.create_block(
        {
            "block_id": "bnb_futures_ETHUSDT_20260604120000000000",
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "qty_initial": 1.0,
            "qty_open": 0.0,
            "entry_price": 200.0,
            "target_price": 190.0,
            "stop_price": 205.0,
            "thesis": "stale waiting block",
            "llm_reason": "",
            "risk_note": "",
            "status": "proposed",
            "metadata": {
                "entry_style": "wait_for_price",
                "entry_trigger_price": 200.0,
                "entry_trigger_operator": ">=",
            },
        }
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "confidence": 0.78,
                    "stance": "short_watch",
                    "entry_price": 101.0,
                    "target_price": 95.0,
                    "stop_price": 104.0,
                    "liquidation_price": 150.0,
                    "quote_budget_usdt": 50.0,
                    "metadata": {
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 101.0,
                        "entry_trigger_operator": ">=",
                    },
                }
            ]
        )
    )

    assert result["status"] == "ok"
    assert len(result["actions"]["create_blocks"]) == 1
    assert result["actions"]["create_blocks"][0]["symbol"] == "BTCUSDT"
    assert result["actions"]["create_blocks"][0]["side"] == "short"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert result["actions"]["close_blocks"] == [
        {
            "block_id": "bnb_futures_ETHUSDT_20260604120000000000",
            "reason": "manager_stale_waiting_block",
        }
    ]
    assert result["applied"]["closed"] == [
        {
            "status": "closed",
            "block_id": "bnb_futures_ETHUSDT_20260604120000000000",
        }
    ]
    block = trader.list_blocks(include_closed=False)[0]
    assert block["symbol"] == "BTCUSDT"
    assert block["market"] == "futures"
    assert block["side"] == "short"
    assert block["metadata"]["manager_contract_decision"] == "create_blocks"
    passport = block["metadata"]["live_authority"]["validation_passport"]
    assert passport["version"] == "trading_validation_passport_v1"
    assert passport["readiness"] == "scale_ready"
    assert passport["is_complete"] is True
    assert passport["requires_revalidation"] is False


def test_manager_contract_spot_long_side_uses_price_geometry_before_mixed_text(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "payload": {
                "contract_id": "block_action_contract",
                "decision": "create_blocks_and_close_stale_block",
                "symbol": "WLDUSDT",
                "market": "spot",
                "horizon": "short",
                "entry_style": "wait_for_price",
                "entry_price": None,
                "entry_trigger_price": 0.52848,
                "entry_trigger_operator": "<=",
                "target_price": 0.55051,
                "stop_price": 0.51427,
                "quote_budget_usdt": 207.01,
                "confidence": 0.68,
                "claim": (
                    "close stale futures:short block; create WLDUSDT spot long "
                    "wait block at 0.52848"
                ),
                "reasons": [
                    "futures:short lane is concentrated",
                    "WLDUSDT spot long has fresh quant long_score 90",
                ],
                "risk_note": "현물 롱 대기 블록이며 추격 진입은 금지한다.",
            }
        }
    )
    adapter = _FakeBinance()
    adapter.prices["WLDUSDT"] = 0.53
    trader = _trader(tmp_path, adapter=adapter, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "WLDUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.68,
                    "stance": "long_watch",
                    "entry_price": 0.52848,
                    "target_price": 0.55051,
                    "stop_price": 0.51427,
                    "quote_budget_usdt": 207.01,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 0.52848,
                    "entry_trigger_operator": "<=",
                    "evidence_refs": ["quant:WLDUSDT"],
                }
            ]
        )
    )

    action = result["actions"]["create_blocks"][0]
    block = trader.list_blocks(include_closed=False)[0]
    assert action["side"] == "long"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert block["market"] == "spot"
    assert block["side"] == "long"
    assert block["entry_price"] == pytest.approx(0.52848)


def test_binance_created_block_records_validation_repair_constraints(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 99.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 99.0,
                    "target_price": 108.0,
                    "stop_price": 95.0,
                    "thesis": "상관 노출 검증 전 소액 대기진입",
                    "risk_note": "검증 repair 블록",
                    "confidence": 0.82,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        llm=llm,
        memory_provider=lambda **_: {
            "status": "ok",
            "validation_repair_backlog": {
                "status": "pending",
                "total_item_count": 1,
                "items": [
                    {
                        "policy_id": "validation.correlation.binance",
                        "venue": "binance",
                        "discipline_id": "correlation",
                        "priority": "high",
                        "blocks_scaling": "cap_correlated_exposure",
                        "blocks_new_entries": (
                            "correlated_or_factor_concentrated_entries"
                        ),
                        "runner_hint": (
                            "refresh portfolio exposure snapshots, then "
                            "refresh_trading_validation"
                        ),
                        "verification_artifact": (
                            "correlation metric includes active exposure buckets"
                        ),
                        "pass_current_gap": "portfolio correlation evidence missing",
                        "pass_collection_hook": (
                            "refresh portfolio exposure snapshots, then "
                            "refresh_trading_validation"
                        ),
                        "pass_criteria": "correlation exposure stays inside active cap",
                        "exit_criteria": ["correlation_exposure_under_cap"],
                    }
                ],
            },
            "block_design_constraints": {
                "status": "active",
                "total_item_count": 1,
                "items": [
                    {
                        "policy_id": "validation.correlation.binance",
                        "venue": "binance",
                        "discipline_id": "correlation",
                        "priority": "high",
                        "entry_bias": "concentration_checked_waiting_entry",
                        "sizing_policy": "cap_correlated_exposure",
                        "required_evidence": [
                            "correlation_matrix",
                            "active_block_exposure",
                        ],
                        "required_checks": ["concentration_review"],
                        "blocks_scaling": "cap_correlated_exposure",
                        "blocks_new_entries": (
                            "correlated_or_factor_concentrated_entries"
                        ),
                        "runner_hint": (
                            "refresh portfolio exposure snapshots, then "
                            "refresh_trading_validation"
                        ),
                        "verification_artifact": (
                            "factor/correlation metrics survive current blocks"
                        ),
                    }
                ],
            },
        },
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long",
                    "confidence": 0.84,
                    "price": 100.0,
                }
            ]
        )
    )
    block = trader.list_blocks(include_closed=False)[0]
    events = trader.repository.list_events(str(block["block_id"]))
    repair = block["metadata"]["validation_repair"]
    validation_evidence = block["metadata"]["validation_evidence"]

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert repair["scope"] == "binance"
    assert repair["hard_filter"] is False
    assert repair["discipline_ids"] == ["correlation"]
    assert repair["entry_biases"] == ["concentration_checked_waiting_entry"]
    assert repair["required_evidence"] == [
        "correlation_matrix",
        "active_block_exposure",
    ]
    assert repair["blocks_new_entries"] == [
        "correlated_or_factor_concentrated_entries"
    ]
    assert repair["runner_hints"] == [
        "refresh portfolio exposure snapshots, then refresh_trading_validation"
    ]
    assert repair["verification_artifacts"] == [
        "correlation metric includes active exposure buckets",
        "factor/correlation metrics survive current blocks",
    ]
    assert validation_evidence["source"] == "validation_repair"
    assert validation_evidence["status"] == "repair_required"
    assert validation_evidence["required_evidence"] == [
        "correlation_matrix",
        "active_block_exposure",
    ]
    assert validation_evidence["pass_current_gaps"] == [
        "portfolio correlation evidence missing"
    ]
    assert validation_evidence["pass_collection_hooks"] == [
        "refresh portfolio exposure snapshots, then refresh_trading_validation"
    ]
    assert validation_evidence["pass_criteria"] == [
        "correlation exposure stays inside active cap"
    ]
    assert any(row["event_type"] == "validation_repair_applied" for row in events)
    assert "19검증 반영" in block["risk_note"]


def test_validation_repair_forces_binance_waiting_probe_create_action() -> None:
    actions = {
        "create_blocks": [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "long",
                "entry_style": "immediate",
                "entry_price": 50000,
                "target_price": 52000,
                "stop_price": 49000,
                "qty": 0.08,
                "quote_budget_usdt": 4000,
                "risk_budget_usdt": 120,
                "thesis": "LLM tried to scale before repair completed.",
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        actions,
        validation_repair={
            "scope": "binance",
            "repair_item_count": 1,
            "repair_backlog": [
                {
                    "discipline_id": "walk_forward_analysis",
                    "repair_action_id": (
                        "validation_repair.backtest_wfa_oos_rebuild."
                        "walk_forward_analysis"
                    ),
                    "allowed_entry_posture": "shadow_or_waiting_entry_only",
                    "scale_up_blocked": True,
                    "live_shadow_required": True,
                    "last_repair_status": "queued_external_runner",
                }
            ],
        },
    )

    row = adjusted["create_blocks"][0]
    enforcement = row["validation_repair_enforcement"]
    assert row["entry_style"] == "wait_for_price"
    assert row["entry_trigger_price"] == 50000
    assert row["entry_trigger_operator"] == "<="
    assert row["quote_budget_usdt"] == pytest.approx(1000)
    assert row["risk_budget_usdt"] == pytest.approx(30)
    assert row["qty"] == pytest.approx(0.02)
    assert enforcement["scale_up_blocked"] is True
    assert enforcement["waiting_entry_required"] is True
    assert enforcement["budget_multiplier"] == pytest.approx(0.25)
    assert enforcement["last_repair_statuses"] == ["queued_external_runner"]
    assert row["metadata"]["validation_repair_enforcement"] == enforcement


def test_lane_scale_validation_repair_caps_binance_budget_and_checks_reward_risk() -> None:
    actions = {
        "create_blocks": [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "long",
                "entry_style": "immediate",
                "entry_price": 100.0,
                "target_price": 110.0,
                "stop_price": 95.0,
                "qty": 0.08,
                "quote_budget_usdt": 4000,
                "risk_budget_usdt": 120,
                "thesis": "verified alpha sample gap should stay probe-sized.",
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        actions,
        validation_repair={
            "scope": "binance",
            "block_design_constraints": [
                {
                    "policy_id": "lane_scale.verified_edge_sample_cap",
                    "scale_blocker": "verified_edge_sample_cap",
                    "entry_bias": "recorded_cost_alpha_waiting_probe",
                    "sizing_policy": "recorded_alpha_probe_until_min_samples",
                    "risk_budget_multiplier": 0.25,
                    "min_reward_risk": 2.0,
                    "required_evidence": [
                        "recorded_entry_fill",
                        "recorded_exit_fill",
                        "positive_net_edge",
                    ],
                }
            ],
        },
    )

    row = adjusted["create_blocks"][0]
    enforcement = row["validation_repair_enforcement"]

    assert row["entry_style"] == "wait_for_price"
    assert row["entry_trigger_price"] == pytest.approx(100.0)
    assert row["entry_trigger_operator"] == "<="
    assert row["quote_budget_usdt"] == pytest.approx(1000)
    assert row["risk_budget_usdt"] == pytest.approx(30)
    assert row["qty"] == pytest.approx(0.02)
    assert enforcement["scale_up_blocked"] is True
    assert enforcement["budget_multiplier"] == pytest.approx(0.25)
    assert enforcement["checks"][0]["reward_risk"] == pytest.approx(2.0)
    assert enforcement["checks"][0]["status"] == "ok"
    assert row["validation_repair"]["scale_blockers"] == ["verified_edge_sample_cap"]
    assert row["validation_repair"]["risk_budget_multiplier"] == pytest.approx(0.25)
    assert row["validation_repair"]["min_reward_risk"] == pytest.approx(2.0)


def test_validation_repair_action_metadata_drops_full_prompt_sections() -> None:
    long_reason = "long validation narrative " * 400
    actions = {
        "create_blocks": [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "long",
                "entry_style": "wait_for_price",
                "entry_price": 100.0,
                "entry_trigger_price": 100.0,
                "target_price": 108.0,
                "stop_price": 96.0,
                "qty": 0.05,
                "thesis": "validation repair should stay compact in actions.",
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        actions,
        validation_repair={
            "scope": "binance",
            "repair_item_count": 1,
            "constraint_count": 1,
            "repair_backlog": [
                {
                    "policy_id": "validation.large.prompt",
                    "discipline_id": "walk_forward_analysis",
                    "repair_action_id": "validation.large.prompt.walk_forward",
                    "allowed_entry_posture": "shadow_or_waiting_entry_only",
                    "last_repair_reason": long_reason,
                    "pass_required_evidence": [long_reason, long_reason],
                    "risk_note": long_reason,
                }
            ],
            "block_design_constraints": [
                {
                    "policy_id": "validation.large.prompt",
                    "discipline_id": "walk_forward_analysis",
                    "entry_bias": "waiting_probe",
                    "risk_budget_multiplier": 0.25,
                    "min_reward_risk": 2.0,
                    "required_evidence": ["walk_forward_result", "out_of_sample_result"],
                    "pass_criteria": long_reason,
                    "risk_note": long_reason,
                }
            ],
        },
    )

    row = adjusted["create_blocks"][0]
    repair = row["validation_repair"]
    metadata_repair = row["metadata"]["validation_repair"]
    encoded_repair = json.dumps(repair, ensure_ascii=False)

    assert repair is metadata_repair
    assert "repair_backlog" not in repair
    assert "block_design_constraints" not in repair
    assert "pass_required_evidence" not in encoded_repair
    assert "long validation narrative" not in encoded_repair
    assert len(encoded_repair) < 2200
    assert repair["discipline_ids"] == ["walk_forward_analysis"]
    assert repair["entry_biases"] == ["waiting_probe"]
    assert repair["risk_budget_multiplier"] == pytest.approx(0.25)


def test_validation_repair_budget_cap_keeps_binance_probe_executable() -> None:
    actions = {
        "create_blocks": [
            {
                "symbol": "LINKUSDT",
                "market": "futures",
                "side": "short",
                "entry_style": "wait_for_price",
                "entry_price": 8.0236,
                "entry_trigger_price": 8.0236,
                "target_price": 7.8952,
                "stop_price": 8.0877,
                "quote_budget_usdt": 49.63,
                "thesis": "small waiting-entry futures probe should remain executable.",
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        actions,
        validation_repair={
            "scope": "binance",
            "block_design_constraints": [
                {
                    "scale_blocker": "walk_forward_probe",
                    "entry_bias": "waiting_probe",
                    "risk_budget_multiplier": 0.25,
                    "min_reward_risk": 2.0,
                }
            ],
        },
    )

    row = adjusted["create_blocks"][0]
    enforcement = row["validation_repair_enforcement"]

    assert row["quote_budget_usdt"] == pytest.approx(20.0)
    adjustment = next(
        item for item in enforcement["adjustments"] if item["field"] == "quote_budget_usdt"
    )
    assert adjustment["raw_scaled_to"] == pytest.approx(12.4075)
    assert adjustment["floor_reason"] == "minimum_executable_notional_floor"


def test_lane_authority_budget_adjustment_keeps_probe_executable_floor() -> None:
    entry_price = 8.0236
    row = {
        "symbol": "LINKUSDT",
        "market": "futures",
        "side": "short",
        "entry_price": entry_price,
        "entry_trigger_price": entry_price,
        "qty": 20.0 / entry_price,
        "quote_budget_usdt": 20.0,
        "metadata": {},
    }

    adjusted = BinanceBlockTrader._apply_lane_authority_budget_to_row(
        row,
        {
            "budget_multiplier": 0.25,
            "matched_lanes": ["futures:short"],
            "reason": "probe_budget",
        },
    )
    adjustment = adjusted["metadata"]["lane_authority_budget_adjustment"]

    assert adjusted["quote_budget_usdt"] == pytest.approx(20.0)
    assert adjusted["qty"] * entry_price == pytest.approx(20.0)
    assert adjustment["raw_scaled_to_quote_budget_usdt"] == pytest.approx(5.0)
    assert adjustment["floor_reason_quote_budget_usdt"] == "minimum_executable_notional_floor"
    assert adjustment["floor_reason_qty"] == "minimum_executable_notional_floor"


def test_validation_repair_reward_risk_allows_tiny_rounding_drift() -> None:
    actions = {
        "create_blocks": [
            {
                "symbol": "WLDUSDT",
                "market": "spot",
                "side": "long",
                "entry_style": "wait_for_price",
                "entry_price": 0.63629,
                "entry_trigger_price": 0.63629,
                "target_price": 0.65156,
                "stop_price": 0.62865,
                "quote_budget_usdt": 51.3175,
                "thesis": "rounded 2R geometry should not be rejected at 1.998R.",
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        actions,
        validation_repair={
            "scope": "binance",
            "block_design_constraints": [
                {
                    "scale_blocker": "cost_probe",
                    "entry_bias": "waiting_probe",
                    "risk_budget_multiplier": 0.25,
                    "min_reward_risk": 2.0,
                }
            ],
        },
    )

    enforcement = adjusted["create_blocks"][0]["validation_repair_enforcement"]

    assert enforcement.get("rejected") is not True
    assert enforcement["checks"][0]["reward_risk"] == pytest.approx(1.998691)
    assert enforcement["checks"][0]["status"] == "ok"


def test_validation_repair_allows_volatile_attack_wide_probe_stop() -> None:
    actions = {
        "create_blocks": [
            {
                "symbol": "ALCXUSDT",
                "market": "spot",
                "side": "long",
                "lane": "volatile_attack",
                "entry_style": "wait_for_price",
                "entry_price": 2.7505,
                "entry_trigger_price": 2.7505,
                "target_price": 3.2644,
                "stop_price": 2.4999,
                "quote_budget_usdt": 71.86,
                "thesis": "small volatile attack probe needs wider stop than ordinary lanes.",
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        actions,
        validation_repair={
            "scope": "binance",
            "block_design_constraints": [
                {
                    "scale_blocker": "cost_probe",
                    "entry_bias": "waiting_probe",
                    "risk_budget_multiplier": 0.25,
                    "min_reward_risk": 2.0,
                    "max_stop_risk_pct": 3.0,
                }
            ],
        },
    )

    row = adjusted["create_blocks"][0]
    enforcement = row["validation_repair_enforcement"]

    assert enforcement.get("rejected") is not True
    assert enforcement["checks"][0]["status"] == "ok"
    assert enforcement["checks"][0]["stop_risk_pct"] == pytest.approx(9.111071)
    assert enforcement["checks"][0]["max_stop_risk_pct"] == pytest.approx(12.0)
    assert (
        enforcement["checks"][0]["max_stop_risk_override_reason"]
        == "volatile_attack_wide_stop"
    )


def test_verified_edge_net_loss_policy_caps_binance_budget_and_requires_positive_rr() -> None:
    actions = {
        "create_blocks": [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "long",
                "entry_style": "immediate",
                "entry_price": 100.0,
                "target_price": 112.0,
                "stop_price": 95.0,
                "qty": 0.08,
                "quote_budget_usdt": 4000,
                "risk_budget_usdt": 120,
                "thesis": "recorded-cost alpha net pnl is negative but size is being pressed.",
            }
        ],
        "update_blocks": [],
        "close_blocks": [],
        "pause_blocks": [],
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        actions,
        validation_repair={
            "scope": "binance",
            "block_design_constraints": [
                {
                    "policy_id": "lane_scale.verified_edge_net_pnl_cap",
                    "scale_blocker": "verified_edge_net_pnl_cap",
                    "entry_bias": "positive_recorded_edge_waiting_probe",
                    "sizing_policy": "micro_probe_until_recorded_alpha_positive",
                    "target_stop_review": (
                        "reprice_or_wait_until_recorded_cost_alpha_positive"
                    ),
                    "risk_budget_multiplier": 0.25,
                    "max_budget_multiplier": 0.25,
                    "min_reward_risk": 2.2,
                    "required_evidence": [
                        "recorded_entry_fill",
                        "recorded_exit_fill",
                        "fee",
                        "spread",
                        "slippage",
                        "funding_or_tax",
                        "positive_recorded_cost_alpha_net_pnl",
                    ],
                    "required_checks": ["require_positive_net_edge"],
                }
            ],
        },
    )

    row = adjusted["create_blocks"][0]
    enforcement = row["validation_repair_enforcement"]

    assert row["entry_style"] == "wait_for_price"
    assert row["entry_trigger_price"] == pytest.approx(100.0)
    assert row["entry_trigger_operator"] == "<="
    assert row["quote_budget_usdt"] == pytest.approx(1000)
    assert row["risk_budget_usdt"] == pytest.approx(30)
    assert row["qty"] == pytest.approx(0.02)
    assert enforcement["scale_up_blocked"] is True
    assert enforcement["budget_multiplier"] == pytest.approx(0.25)
    assert enforcement["checks"][0]["reward_risk"] == pytest.approx(2.4)
    assert enforcement["checks"][0]["min_reward_risk"] == pytest.approx(2.2)
    assert enforcement["checks"][0]["status"] == "ok"
    repair = row["validation_repair"]
    assert repair["scale_blockers"] == ["verified_edge_net_pnl_cap"]
    assert repair["entry_biases"] == ["positive_recorded_edge_waiting_probe"]
    assert repair["risk_budget_multiplier"] == pytest.approx(0.25)
    assert repair["max_budget_multiplier"] == pytest.approx(0.25)
    assert repair["min_reward_risk"] == pytest.approx(2.2)
    assert "positive_recorded_cost_alpha_net_pnl" in repair["required_evidence"]
    assert "require_positive_net_edge" in repair["required_checks"]


def test_verified_edge_net_loss_policy_marks_binance_weak_reward_risk_rejected() -> None:
    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "entry_style": "immediate",
                    "entry_price": 100.0,
                    "target_price": 107.0,
                    "stop_price": 95.0,
                    "qty": 0.08,
                    "quote_budget_usdt": 4000,
                    "risk_budget_usdt": 120,
                    "thesis": "net edge repair should reject weak target/stop geometry.",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        validation_repair={
            "scope": "binance",
            "block_design_constraints": [
                {
                    "policy_id": "lane_scale.verified_edge_net_pnl_cap",
                    "scale_blocker": "verified_edge_net_pnl_cap",
                    "entry_bias": "positive_recorded_edge_waiting_probe",
                    "sizing_policy": "micro_probe_until_recorded_alpha_positive",
                    "risk_budget_multiplier": 0.25,
                    "min_reward_risk": 2.2,
                    "required_checks": ["require_positive_net_edge"],
                }
            ],
        },
    )

    row = adjusted["create_blocks"][0]
    enforcement = row["validation_repair_enforcement"]
    assert enforcement["rejected"] is True
    assert enforcement["reason"] == "validation_repair_min_reward_risk_not_met"
    assert enforcement["checks"][0]["reward_risk"] == pytest.approx(1.4)
    assert enforcement["checks"][0]["min_reward_risk"] == pytest.approx(2.2)


def test_validation_repair_marks_binance_waiting_entry_without_price_rejected() -> None:
    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "entry_style": "immediate",
                    "target_price": 52000,
                    "stop_price": 49000,
                    "thesis": "missing executable price structure",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        validation_repair={
            "scope": "binance",
            "repair_backlog": [
                {
                    "discipline_id": "walk_forward_analysis",
                    "allowed_entry_posture": "shadow_or_waiting_entry_only",
                    "scale_up_blocked": True,
                    "last_repair_status": "queued_external_runner",
                }
            ],
        },
    )

    enforcement = adjusted["create_blocks"][0]["validation_repair_enforcement"]
    assert enforcement["rejected"] is True
    assert enforcement["reason"] == (
        "validation_repair_waiting_entry_requires_trigger_price"
    )


def test_manager_contract_close_and_hold_extracts_stale_block_ids(
    tmp_path: Path,
) -> None:
    block_id = "bnb_futures_SUIUSDT_20260609062107686640"
    llm = _FakeLLM(
        {
            "payload": {
                "contract_id": "block_action_contract",
                "decision": "close_blocks_and_hold",
                "symbol": "SUIUSDT,TONUSDT",
                "claim": "Close stale proposed futures short blocks and hold.",
                "next_actions": [f"{block_id} close"],
                "reasons": ["stale proposed block should be closed"],
            }
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.repository.create_block(
        {
            "block_id": block_id,
            "symbol": "SUIUSDT",
            "market": "futures",
            "side": "short",
            "qty_initial": 1.0,
            "qty_open": 0.0,
            "entry_price": 0.76159,
            "target_price": 0.74331,
            "stop_price": 0.77072,
            "thesis": "stale waiting block",
            "llm_reason": "",
            "risk_note": "",
            "status": "proposed",
            "metadata": {
                "entry_style": "wait_for_price",
                "entry_trigger_price": 0.76159,
                "entry_trigger_operator": ">=",
            },
        }
    )

    result = asyncio.run(trader.run_manager_once(candidates=[]))

    assert result["actions"]["create_blocks"] == []
    assert result["actions"]["close_blocks"] == [
        {"block_id": block_id, "reason": "manager_stale_waiting_block"}
    ]
    assert result["applied"]["closed"] == [{"status": "closed", "block_id": block_id}]


def test_binance_manager_prompt_records_jue_workflow_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BrokenRegistry:
        def compile_prompt_pack(self, workflow_id: str) -> dict[str, Any]:
            assert workflow_id == "binance_cycle"
            raise JueSkillValidationError("workflow assets unavailable")

    monkeypatch.setattr(
        binance_block_trader_module,
        "JueSkillRegistry",
        BrokenRegistry,
        raising=False,
    )
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    assert result["status"] == "ok"
    prompt = llm.calls[0]["payload"]
    assert prompt["jue_workflow"] == {
        "workflow_id": "binance_cycle",
        "status": "error",
        "error_message": "workflow assets unavailable",
    }


def test_binance_manager_run_storage_compacts_large_prompt(tmp_path: Path) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    prompt = {
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 900_000},
        "candidates": [
            {
                "symbol": f"ALT{i}USDT",
                "reason_md": "X" * 20_000,
                "metadata": {"raw": "Y" * 20_000},
            }
            for i in range(40)
        ],
        "memory": {"raw": "Z" * 400_000},
    }

    run_id = repo.save_manager_run(
        prompt=prompt,
        response={"hold_decision": {"summary": "H" * 200_000}},
        actions={"create_blocks": [{"symbol": "BTCUSDT", "thesis": "A" * 120_000}]},
    )

    with sqlite3.connect(repo.path) as conn:
        row = conn.execute(
            """
            SELECT length(prompt_json), length(response_json), length(actions_json),
                   prompt_json
            FROM manager_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()

    assert row is not None
    assert row[0] <= 90_000
    assert row[1] <= 90_000
    assert row[2] <= 70_000
    stored_prompt = json.loads(row[3])
    assert stored_prompt["_storage_compaction"]["status"] == "compacted"
    assert stored_prompt["prompt_budget"]["version"] == "prompt_budget_v1"


def test_binance_manager_run_storage_records_wiki_diagnostics(tmp_path: Path) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")

    manager_run_id = repo.save_manager_run(
        prompt={
            "decision_inputs": ["jue_wiki", "jue_wiki_repair_contract"],
            "jue_wiki_application": {
                "status": "ok",
                "selection_run_id": "selection:binance-storage",
                "selected_page_ids": ["binance.symbol.NEARUSDT"],
            },
            "jue_wiki_repair_contract": {
                "status": "active",
                "action_batches": [
                    {
                        "scope": "binance",
                        "action_type": "refresh_crypto_microstructure",
                        "count": 4,
                        "symbols": ["NEARUSDT", "LTCUSDT"],
                    }
                ],
            },
            "candidates": [{"symbol": "NEARUSDT", "market": "futures"}],
        },
        response={
            "hold_decision": {
                "summary": "관망",
                "watch_symbols": ["BTCUSDT"],
                "data_gaps": ["별도 후보만 추적"],
            }
        },
        actions={
            "create_blocks": [],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
    )

    run = repo.list_manager_runs(limit=1)[0]

    assert run["id"] == manager_run_id
    assert run["prompt"]["compact_manager_context"]["jue_wiki_application"][
        "selection_run_id"
    ] == "selection:binance-storage"
    assert run["prompt"]["compact_manager_context"]["jue_wiki_repair_contract"][
        "action_batches"
    ][0]["action_type"] == "refresh_crypto_microstructure"
    assert run["prompt"]["diagnostics"]["version"] == "binance_manager_diagnostics_v1"
    assert run["prompt"]["diagnostics"]["blocker_tags"][
        "unresolved_jue_wiki_repair_action_batches"
    ] >= 3


def test_binance_manager_prompt_emergency_compaction_keeps_prompt_evidence_keys(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    prompt = {
        "task": "Build executable Binance blocks from research and live gates.",
        "entry_gate_policy": {
            "cooldown_symbol_keys": ["BIOUSDT"],
            "cooldown_symbols": {
                "BIOUSDT": {
                    "status": "cooldown",
                    "reason": "negative symbol cost-drag",
                }
            },
        },
        "performance": {
            "sample_count": 42,
            "symbol_scorecards": [
                {
                    "symbol": "BIOUSDT",
                    "market": "futures",
                    "side": "long",
                    "pnl_usdt": -0.30,
                    "profit_factor": 0.55,
                }
            ],
        },
        "crypto_market_pulse": {
            "status": "ok",
            "regime_brief": {"label": "choppy_risk"},
        },
        "decision_packet": {
            "items": [
                {"symbol": "BIOUSDT", "market": "futures", "side": "long"}
            ],
        },
        "candidates": [
            {
                "symbol": f"ALT{i}USDT",
                "market": "futures",
                "side": "long",
                "thesis": "large prompt evidence " * 200,
            }
            for i in range(120)
        ],
        "output_schema": {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                }
            ]
        },
        "jue_workflow": {
            "workflow_id": "binance_cycle",
            "workflow_version": 1,
        },
        "jue_wiki_application": {
            "status": "ok",
            "selection_run_id": "selection:binance-cycle",
            "prompt_mode": "assist",
            "selected_page_ids": ["binance.symbol.BIOUSDT"],
        },
        "jue_wiki_repair_contract": {
            "status": "active",
            "top_priorities": [
                {
                    "page_id": "binance.symbol.BIOUSDT",
                    "repair_action": "do not repeat low-edge futures churn",
                }
            ],
        },
        "proactive_decision_pressure": {
            "status": "action_required",
            "pressure_level": "high",
            "zero_action_streak": 2,
            "top_candidates": [{"symbol": "BTCUSDT", "score": 88}],
        },
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 220_000},
    }
    prompt.update({f"overflow_{i}": "X" * 1_000 for i in range(4_200)})

    run_id = repo.save_manager_run(prompt=prompt, response={}, actions={})

    with sqlite3.connect(repo.path) as conn:
        row = conn.execute(
            "SELECT prompt_json FROM manager_runs WHERE id = ?",
            (run_id,),
        ).fetchone()

    assert row is not None
    stored_prompt = json.loads(row[0])
    assert stored_prompt["_storage_compaction"]["emergency"] is True
    assert stored_prompt["entry_gate_policy"]["cooldown_symbol_keys"] == ["BIOUSDT"]
    assert stored_prompt["performance"]["sample_count"] == 42
    assert stored_prompt["crypto_market_pulse"]["regime_brief"]["label"] == "choppy_risk"
    assert stored_prompt["candidates"]["item_count"] == 120
    assert stored_prompt["jue_workflow"]["workflow_id"] == "binance_cycle"
    assert stored_prompt["jue_wiki_application"]["selected_page_ids"] == [
        "binance.symbol.BIOUSDT"
    ]
    assert stored_prompt["jue_wiki_repair_contract"]["top_priorities"][0]["page_id"] == (
        "binance.symbol.BIOUSDT"
    )
    assert stored_prompt["proactive_decision_pressure"]["status"] == (
        "action_required"
    )
    assert stored_prompt["proactive_decision_pressure"]["zero_action_streak"] == 2
    assert "hold_decision" not in stored_prompt
    assert "applied" not in stored_prompt


def test_binance_manager_run_storage_keeps_candidate_performance_multiplier(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    prompt = {
        "task": "Build executable Binance blocks from research and live gates.",
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 320_000},
        "candidates": [
            {
                "symbol": f"ALT{i}USDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "performance_budget_multiplier": 0.35 if i == 77 else 1.0,
                "quote_budget_usdt": 5.0 + i,
                "reason_md": "large candidate evidence " * 240,
                "calculated": {
                    "reward_risk": 2.2,
                    "performance_budget_multiplier": 0.35 if i == 77 else 1.0,
                    "sizing_inputs": {
                        "performance_budget_multiplier": 0.35 if i == 77 else 1.0,
                        "lane_performance_loss_multiplier": 0.35 if i == 77 else 1.0,
                    },
                },
            }
            for i in range(90)
        ],
        "performance": {"sample_count": 42, "raw": "P" * 220_000},
        "crypto_market_pulse": {"status": "ok", "raw": "M" * 220_000},
        "jue_workflow": {"workflow_id": "binance_cycle", "workflow_version": 1},
    }

    run_id = repo.save_manager_run(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ALT77USDT",
                    "market": "futures",
                    "side": "short",
                    "entry_price": 1.02,
                    "target_price": 0.94,
                    "stop_price": 1.05,
                }
            ]
        },
    )

    with sqlite3.connect(repo.path) as conn:
        row = conn.execute(
            "SELECT prompt_json FROM manager_runs WHERE id = ?",
            (run_id,),
        ).fetchone()

    assert row is not None
    stored_prompt = json.loads(row[0])
    candidate_items = stored_prompt["candidates"]["items"]
    selected = next(item for item in candidate_items if item.get("symbol") == "ALT77USDT")
    assert selected["performance_budget_multiplier"] == pytest.approx(0.35)
    assert selected["calculated"]["performance_budget_multiplier"] == pytest.approx(0.35)


def test_binance_manager_run_storage_keeps_action_selected_candidate(
    tmp_path: Path,
) -> None:
    repo = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    prompt = {
        "task": "Build executable Binance blocks from research and live gates.",
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 300_000},
        "candidates": [
            {
                "symbol": f"ALT{i}USDT",
                "market": "futures",
                "side": "short",
                "horizon": "intraday",
                "score": 70 + i,
                "reason_md": "large candidate evidence " * 260,
            }
            for i in range(80)
        ],
        "performance": {"sample_count": 42, "raw": "P" * 200_000},
        "crypto_market_pulse": {"status": "ok", "raw": "M" * 200_000},
        "jue_workflow": {"workflow_id": "binance_cycle", "workflow_version": 1},
    }

    run_id = repo.save_manager_run(
        prompt=prompt,
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "ALT79USDT",
                    "market": "futures",
                    "side": "short",
                    "entry_price": 1.02,
                    "target_price": 0.94,
                    "stop_price": 1.05,
                }
            ]
        },
    )

    with sqlite3.connect(repo.path) as conn:
        row = conn.execute(
            "SELECT prompt_json FROM manager_runs WHERE id = ?",
            (run_id,),
        ).fetchone()

    assert row is not None
    stored_prompt = json.loads(row[0])
    candidate_items = stored_prompt["candidates"]["items"]
    assert stored_prompt["candidates"]["item_count"] == 80
    assert any(item.get("symbol") == "ALT79USDT" for item in candidate_items)


def test_binance_manager_prompt_emergency_compaction_is_bounded_for_large_nested_sections() -> None:
    heavy_market_universe = {
        f"SYM{i:03d}USDT": {
            "symbol": f"SYM{i:03d}USDT",
            "lane": "volatile_attack" if i % 3 == 0 else "futures",
            "observations": [
                {
                    "event": f"event-{j}",
                    "evidence": "nested market evidence " * 180,
                    "orderbook": {"bids": [["1.0", "100"]] * 20, "asks": [["1.1", "100"]] * 20},
                }
                for j in range(6)
            ],
        }
        for i in range(450)
    }
    prompt = {
        "task": "Build executable Binance blocks from research and live gates.",
        "entry_gate_policy": {"status": "active", "cooldown_symbol_keys": ["BTCUSDT"]},
        "performance": {"sample_count": 20, "win_rate": 0.4},
        "crypto_market_pulse": {"status": "ok", "regime_brief": {"label": "choppy_risk"}},
        "market_universe": heavy_market_universe,
        "universe": [
            {"symbol": f"ALT{i}USDT", "notes": "universe evidence " * 150}
            for i in range(600)
        ],
        "candidates": [
            {"symbol": f"ALT{i}USDT", "reason_md": "candidate evidence " * 220}
            for i in range(160)
        ],
        "blocks": [
            {"block_id": f"B{i}", "symbol": "BTCUSDT", "metadata": {"raw": "block evidence " * 180}}
            for i in range(80)
        ],
        "output_schema": {"create_blocks": [{"symbol": "BTCUSDT", "market": "spot"}]},
        "jue_workflow": {"workflow_id": "binance_cycle", "contracts": [{"id": "block_action"}]},
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 500_000},
    }

    started = time.monotonic()
    compacted = compact_manager_storage_payload(
        prompt,
        limit=80_000,
        label="binance_manager_prompt",
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert prompt_chars(compacted) <= 80_000
    assert compacted["_storage_compaction"]["emergency"] is True
    assert compacted["entry_gate_policy"]["cooldown_symbol_keys"] == ["BTCUSDT"]
    assert compacted["performance"]["sample_count"] == 20
    assert compacted["crypto_market_pulse"]["regime_brief"]["label"] == "choppy_risk"
    assert compacted["market_universe"]["item_count"] == 450
    assert len(compacted["market_universe"]["items"]) <= 6
    assert compacted["candidates"]["item_count"] == 160
    assert compacted["jue_workflow"]["workflow_id"] == "binance_cycle"
    assert "hold_decision" not in compacted
    assert "applied" not in compacted


def test_binance_manager_prompt_emergency_fallback_keeps_decision_diagnostics() -> None:
    prompt = {
        "task": "Build executable Binance blocks from research and live gates.",
        "prompt_budget": {"version": "prompt_budget_v1", "total_chars": 220_000},
        "entry_gate_policy": {"status": "active", "cooldown_symbol_keys": ["BIOUSDT"]},
        "growth_governor": {
            "mode": "edge_rebuild",
            "weak_lanes": ["futures:long", "spot:long:short"],
            "positive_lanes": ["futures:short"],
            "positive_lane_count": 1,
        },
        "growth_unlock": {
            "phase": "rebuilding",
            "next_missions": [
                {"lane": "futures:short", "mission": "press recovered lane carefully"}
            ],
        },
        "live_authority": {
            "live_grade": "restricted",
            "validation_gate": {"status": "validation_probe"},
        },
        "lane_balance": {"dominant_lane": "futures:short"},
        "candidate_generation": {
            "stage_counts": {"observe": 300, "research": 80, "manager": 30}
        },
        "performance": {"sample_count": 19, "realized_pnl_usdt": -0.4662},
        "crypto_market_pulse": {"status": "ok", "regime_brief": {"label": "risk_on"}},
        "market_universe": {
            f"SYM{i:03d}USDT": {
                "symbol": f"SYM{i:03d}USDT",
                "evidence": "market universe evidence " * 180,
            }
            for i in range(450)
        },
        "candidates": [
            {"symbol": f"ALT{i}USDT", "reason_md": "candidate evidence " * 220}
            for i in range(160)
        ],
        "blocks": [
            {"block_id": f"B{i}", "symbol": "BTCUSDT", "metadata": {"raw": "block evidence " * 180}}
            for i in range(80)
        ],
        "jue_workflow": {"workflow_id": "binance_cycle"},
    }

    compacted = compact_manager_storage_payload(
        prompt,
        limit=10_000,
        label="binance_manager_prompt",
    )

    assert prompt_chars(compacted) <= 10_000
    assert compacted["_storage_compaction"]["emergency"] is True
    assert compacted["growth_governor"]["positive_lanes"] == ["futures:short"]
    assert compacted["growth_unlock"]["phase"] == "rebuilding"
    assert compacted["live_authority"]["live_grade"] == "restricted"
    assert compacted["lane_balance"]["dominant_lane"] == "futures:short"
    assert compacted["candidate_generation"]["stage_counts"]["manager"] == 30
    assert compacted["candidates"]["item_count"] == 160


def test_binance_manager_run_records_jue_workflow_metadata(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    workflow = {
        "workflow_id": "binance_cycle",
        "workflow_version": 1,
        "skills": [
            {"skill_id": "crypto_market_sweep"},
            {"skill_id": "block_design"},
            {"skill_id": "risk_sizing"},
        ],
        "contracts": [
            {"contract_id": "block_action_contract"},
            {"contract_id": "evidence_claim_contract"},
        ],
    }

    manager_run_id = trader.repository.save_manager_run(
        prompt={"jue_workflow": workflow},
        response={"hold_decision": {"summary": "hold"}},
        actions={},
        status="ok",
        mode="llm",
        model="fake-model",
    )
    run = trader.repository.list_manager_runs(limit=1)[0]

    assert manager_run_id == run["id"]
    assert run["workflow_id"] == "binance_cycle"
    assert run["workflow_version"] == 1
    assert run["skill_ids"] == [
        "crypto_market_sweep",
        "block_design",
        "risk_sizing",
    ]
    assert run["contract_ids"] == [
        "block_action_contract",
        "evidence_claim_contract",
    ]


def test_same_symbol_multiple_blocks_remain_independent(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    first = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
            "thesis": "first block",
        }
    )
    second = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.2,
            "entry_price": 101.0,
            "target_price": 130.0,
            "stop_price": 95.0,
            "status": "open",
            "thesis": "second block",
        }
    )

    blocks = trader.list_blocks()

    assert len(blocks) == 2
    assert {row["block_id"] for row in blocks} == {
        first["block_id"],
        second["block_id"],
    }
    assert {row["qty_initial"] for row in blocks} == {0.1, 0.2}
    assert trader.get_block(first["block_id"])["target_price"] == 120.0  # type: ignore[index]
    assert trader.get_block(second["block_id"])["target_price"] == 130.0  # type: ignore[index]


def test_binance_block_horizon_is_stored_and_exposed(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.01,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 92.0,
            "status": "proposed",
            "horizon": "mid_term",
            "thesis": "mid swing block",
        }
    )

    assert block["horizon"] == "mid"
    assert block["lane"] == "mid"
    assert block["metadata"]["horizon"] == "mid"
    assert block["metadata"]["block_color"] == "mid"


def test_binance_manager_prompt_uses_decision_packet(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})

    class QuantProvider:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            return [
                {
                    "symbol": "BNBUSDT",
                    "horizon": "intraday",
                    "long_score": 22.0,
                    "short_score": 68.0,
                    "no_trade_score": 48.0,
                    "expected_r_long": -0.26,
                    "expected_r_short": 0.2,
                    "signal": {
                        "bias": "short",
                        "drivers": ["fast EMA is below slow EMA"],
                        "risks": ["funding is crowded"],
                        "metrics": {"atr_pct": 1.8, "rsi": 44.0},
                    },
                    "updated_at": "2026-05-24T09:00:00+00:00",
                }
            ]

        def latest_evidence(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            _ = (symbols, limit)
            return [
                {
                    "evidence_id": "ev_quant_btcusdt_fresh",
                    "source": "crypto_quant",
                    "signal_type": "directional_score",
                    "symbol": "BTCUSDT",
                    "scope": "binance",
                    "confidence": 0.87,
                    "captured_at": "2026-05-25T00:00:00+00:00",
                    "expires_at": "2999-01-01T00:00:00+00:00",
                    "payload": {
                        "bias": "long",
                        "long_score": 82.0,
                        "giant_note": "RAW_CONTEXT_GIANT_TEXT" * 200,
                    },
                }
            ]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str = "intraday",
            points_per_symbol: int = 12,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "horizon": horizon,
                "items": [
                    {
                        "symbol": "BNBUSDT",
                        "history_points": points_per_symbol,
                        "trend": {"short_score_delta": 12.0},
                    }
                ],
            }

    trader = _trader(
        tmp_path,
        llm=llm,
        quant_provider=QuantProvider(),
        memory_provider=lambda **_: {
            "status": "ok",
            "policy_rules": [
                {"policy_id": "policy_global@v1", "scope": "global"},
                {"policy_id": "policy_binance@v1", "scope": "binance"},
                {"policy_id": "policy_crypto@v1", "scope": "crypto"},
            ],
            "validation_repair_backlog": {
                "status": "pending",
                "total_item_count": 2,
                "items": [
                    {
                        "policy_id": "validation.correlation.binance",
                        "repair_policy_id": "validation_repair.binance.correlation",
                        "repair_action_id": (
                            "validation_repair.portfolio_exposure_check.correlation"
                        ),
                        "event_key": "binance:correlation",
                        "venue": "binance",
                        "discipline_id": "correlation",
                        "priority": "high",
                        "status": "active_caution",
                        "label": "상관 노출 확인 필요",
                        "owner": "jue",
                        "cadence": "per_manager_run",
                        "automation_hook": "refresh_portfolio_exposure_snapshot",
                        "execution_weight": "lightweight",
                        "last_repair_status": "queued_external_runner",
                        "last_repair_policy_status": "active_caution",
                        "last_repair_action": "caution",
                        "last_repair_confidence": 0.5,
                        "last_repair_automation_hook": (
                            "refresh_portfolio_exposure_snapshot"
                        ),
                        "last_repair_execution_weight": "lightweight",
                        "last_repair_reason": (
                            "복구 대기 중이라 해당 lane의 증액은 보류한다."
                        ),
                        "lane_policy_hint": "concentration_checked_waiting_entry",
                        "scale_blocker": "validation_correlation_repair",
                        "validation_effect_profile": "portfolio_concentration",
                        "entry_bias": "concentration_checked_waiting_entry",
                        "sizing_policy": "cap_correlated_exposure",
                        "target_stop_review": (
                            "review_regime_correlation_factor_exposure"
                        ),
                        "risk_budget_multiplier": 0.5,
                        "max_budget_multiplier": 0.5,
                        "required_evidence": [
                            "correlation_cluster",
                            "factor_exposure",
                            "sector_or_beta_bucket",
                        ],
                        "required_checks": ["require_exposure_review"],
                        "blocks_scaling": "cap_correlated_exposure",
                        "exit_criteria": [
                            "active_block_correlation_measured",
                            "factor_overlap_reviewed",
                        ],
                        "pass_current_gap": "evidence_thin_or_not_scalable",
                        "pass_collection_hook": "refresh_portfolio_exposure_snapshot",
                        "pass_criteria": "correlation returns to pass or warn",
                        "pass_required_evidence": {
                            "max_top_cluster_share_pct": 60.0,
                            "requires_active_block_exposure_snapshot": True,
                        },
                        "pass_jue_behavior_until_pass": {
                            "allowed_entry_posture": "exposure_capped_probe",
                            "scale_up_blocked": True,
                        },
                    }
                ],
            },
            "block_design_constraints": {
                "status": "active",
                "total_item_count": 1,
                "items": [
                    {
                        "policy_id": "validation.correlation.binance",
                        "venue": "binance",
                        "discipline_id": "correlation",
                        "priority": "high",
                        "validation_effect_profile": "concentration_checked_waiting_entry",
                        "entry_bias": "concentration_checked_waiting_entry",
                        "sizing_policy": "cap_correlated_exposure",
                        "target_stop_review": "correlation_aware",
                        "required_evidence": [
                            "correlation_matrix",
                            "active_block_exposure",
                        ],
                        "required_checks": ["concentration_review"],
                        "blocks_scaling": "cap_correlated_exposure",
                        "exit_criteria": ["correlation_exposure_under_cap"],
                        "risk_note": "상관 노출 검증 전에는 사이즈 확대 금지",
                        "pass_current_gap": "evidence_thin_or_not_scalable",
                        "pass_collection_hook": "refresh_portfolio_exposure_snapshot",
                        "pass_criteria": "correlation returns to pass or warn",
                        "pass_required_evidence": {
                            "max_top_cluster_share_pct": 60.0,
                        },
                    }
                ],
            },
            "policy_rule_evaluation": {
                "status": "ok",
                "by_symbol": {
                    "BTCUSDT": [
                        {
                            "policy_id": "validation_attribution.strategy_family.late_chase",
                            "rule_id": "validation_attribution.strategy_family.late_chase@v1",
                            "status": "active_caution",
                            "effect": {
                                "entry_bias": "reduce_or_wait_on_repeated_attribution",
                            },
                            "matched_metric": {
                                "group_type": "strategy_family",
                                "group": "late_chase",
                            },
                        }
                    ]
                },
            },
        },
    )
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **kwargs: {
                "status": "ok",
                "candidates": [{"symbol": "BNBUSDT", "market": "futures"}],
                "requested_symbols": ["BTCUSDT"],
                "evidence": 1,
                "raw_note": "RAW_CONTEXT_GIANT_TEXT" * 200,
            }
        },
    )()

    asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "calculated": {
                        "entry_style": "wait_for_price",
                        "entry_price": 100.0,
                        "target_price": 103.0,
                        "stop_price": 98.0,
                    },
                }
            ]
        )
    )
    prompt = llm.calls[0]["payload"]

    evidence_ids = [
        row["evidence_id"]
        for row in prompt["decision_packet"]["evidence"]
    ]
    policy_ids = [
        row["policy_id"]
        for row in prompt["decision_packet"]["active_policies"]
    ]
    rendered_prompt = json.dumps(prompt, ensure_ascii=False)
    payload_note = prompt["decision_packet"]["evidence"][0]["payload"]["giant_note"]
    assert "crypto_quant" not in prompt
    assert "crypto_research" not in prompt
    assert "crypto_alpha" not in prompt
    assert "crypto_patterns" not in prompt
    assert "decision_packet" in prompt
    assert "decision_packet_v2" in prompt
    assert prompt["decision_packet_v2"]["version"] == "decision_packet_v2"
    assert prompt["decision_packet_v2"]["schema"]["target_scope"] == "binance"
    assert (
        prompt["decision_packet_v2"]["schema"]["producer"]
        == "tradecraft.services.jue_decision_packet.build_decision_packet"
    )
    assert prompt["decision_packet"]["target_scope"] == "binance"
    assert "ev_quant_btcusdt_fresh" in evidence_ids
    assert len(payload_note) < len("RAW_CONTEXT_GIANT_TEXT" * 200)
    assert "RAW_CONTEXT_GIANT_TEXT" not in rendered_prompt
    assert prompt["raw_context_refs"]["crypto_research"]["requested_symbols"] == ["BTCUSDT"]
    assert "policy_global@v1" in policy_ids
    assert "policy_binance@v1" in policy_ids
    assert "policy_crypto@v1" not in policy_ids
    assert prompt["candidate_policy_impacts"]["BTCUSDT"][0]["matched_metric"] == {
        "group_type": "strategy_family",
        "group": "late_chase",
    }
    assert "decision_packet_v2" in prompt["decision_inputs"]
    assert "validation_repair" in prompt["decision_inputs"]
    assert prompt["validation_repair"]["scope"] == "binance"
    assert (
        prompt["validation_repair"]["repair_backlog"][0]["discipline_id"]
        == "correlation"
    )
    assert prompt["validation_repair"]["repair_backlog"][0]["repair_action_id"] == (
        "validation_repair.portfolio_exposure_check.correlation"
    )
    assert prompt["validation_repair"]["repair_backlog"][0]["automation_hook"] == (
        "refresh_portfolio_exposure_snapshot"
    )
    assert prompt["validation_repair"]["repair_backlog"][0]["last_repair_status"] == (
        "queued_external_runner"
    )
    assert prompt["validation_repair"]["repair_backlog"][0][
        "last_repair_policy_status"
    ] == "active_caution"
    assert "증액은 보류" in prompt["validation_repair"]["repair_backlog"][0][
        "last_repair_reason"
    ]
    assert prompt["validation_repair"]["repair_backlog"][0]["entry_bias"] == (
        "concentration_checked_waiting_entry"
    )
    assert prompt["validation_repair"]["repair_backlog"][0]["pass_current_gap"] == (
        "evidence_thin_or_not_scalable"
    )
    assert prompt["validation_repair"]["repair_backlog"][0][
        "pass_collection_hook"
    ] == "refresh_portfolio_exposure_snapshot"
    assert prompt["validation_repair"]["repair_backlog"][0][
        "pass_required_evidence"
    ]["max_top_cluster_share_pct"] == pytest.approx(60.0)
    assert prompt["validation_repair"]["repair_backlog"][0][
        "risk_budget_multiplier"
    ] == pytest.approx(0.5)
    assert "require_exposure_review" in prompt["validation_repair"][
        "repair_backlog"
    ][0]["required_checks"]
    assert (
        prompt["validation_repair"]["block_design_constraints"][0]["entry_bias"]
        == "concentration_checked_waiting_entry"
    )
    assert prompt["validation_repair"]["block_design_constraints"][0][
        "required_evidence"
    ] == ["correlation_matrix", "active_block_exposure"]
    assert prompt["validation_repair"]["block_design_constraints"][0][
        "pass_collection_hook"
    ] == "refresh_portfolio_exposure_snapshot"
    assert "decision_packet" in prompt["decision_inputs"]
    assert "crypto_quant" not in prompt["decision_inputs"]
    assert prompt["raw_context_refs"]["crypto_quant"]["item_count"] == 1
    assert prompt["raw_context_refs"]["crypto_quant"]["history_item_count"] == 1


def test_manager_prompt_allows_same_symbol_blocks_when_thesis_differs(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 92.0,
            "status": "proposed",
            "thesis": "pullback block",
        }
    )

    asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "calculated": {
                        "entry_style": "wait_for_price",
                        "entry_price": 100.0,
                        "target_price": 103.0,
                        "stop_price": 98.0,
                    },
                }
            ]
        )
    )
    prompt = llm.calls[0]["payload"]

    policy = prompt["policy"]["multi_block_policy"]
    assert "same symbol" in policy
    assert "thesis" in policy
    assert "Do not treat an existing block as a standalone ban" in policy


def test_binance_manager_prompt_escalates_after_repeated_no_action_with_candidates(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.repository.save_manager_run(
        prompt={"decision_inputs": ["older_action"]},
        response={"hold_decision": {"summary": "older action"}},
        actions={
            "create_blocks": [{"symbol": "ETHUSDT", "market": "spot"}],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
    )
    for _ in range(2):
        trader.repository.save_manager_run(
            prompt={"decision_inputs": ["candidates"]},
            response={
                "hold_decision": {
                    "summary": "실행 후보를 봤지만 조건 대기",
                    "watch_symbols": ["BTCUSDT"],
                }
            },
            actions={
                "create_blocks": [],
                "update_blocks": [],
                "close_blocks": [],
                "pause_blocks": [],
            },
        )
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **_kwargs: {
                "status": "ok",
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "horizon": "intraday",
                        "score": 80,
                        "confidence": 0.62,
                    }
                ],
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "price": 100.0,
                            "bid_price": 99.9,
                            "ask_price": 100.1,
                            "derivatives_status": "available",
                        },
                    }
                ],
            }
        },
    )()

    asyncio.run(
        trader.run_manager_once(
            candidates=[]
        )
    )
    prompt = llm.calls[0]["payload"]
    pressure = prompt["proactive_decision_pressure"]

    assert pressure["status"] == "action_required"
    assert pressure["pressure_level"] == "high"
    assert pressure["zero_action_streak"] == 2
    assert pressure["candidate_count"] >= 1
    assert any(row["symbol"] == "BTCUSDT" for row in pressure["top_candidates"])
    assert "Generic market caution is not a valid resolution" in (
        pressure["response_contract"]["action_required"]
    )
    assert "next trigger price or condition" in pressure["response_contract"][
        "hold_only_requires"
    ]
    assert "proactive_decision_pressure" in prompt["decision_inputs"]


def test_binance_pressure_candidates_drop_upbit_rows_without_krw_price() -> None:
    candidates = [
        {
            "symbol": "KRW-AAVE",
            "market": "upbit_spot",
            "side": "long",
            "score": 82,
            "confidence": 0.62,
            "calculated": {
                "entry_style": "wait_for_price",
                "entry_price_usdt": 85.221,
                "target_price_usdt": 86.806,
                "stop_price_usdt": 84.198,
            },
        },
        {
            "symbol": "KRW-ETH",
            "market": "upbit_spot",
            "side": "long",
            "score": 77,
            "confidence": 0.6,
            "calculated": {
                "entry_style": "wait_for_price",
                "entry_price_krw": 4_200_000,
                "target_price_krw": 4_300_000,
                "stop_price_krw": 4_100_000,
                "quote_budget_krw": 50_000,
            },
        },
    ]

    rows = BinanceBlockTrader._compact_pressure_candidates(candidates, limit=8)

    assert [row["symbol"] for row in rows] == ["KRW-ETH"]
    assert rows[0]["entry_price"] == 4_200_000
    assert rows[0]["target_price"] == 4_300_000
    assert rows[0]["stop_price"] == 4_100_000


def test_manager_errors_when_lane_review_is_missing_from_model(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []}, include_lane_review=False)
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    assert result["status"] == "error"
    assert "lane_review_missing_from_model" in result["error_message"]


def test_manager_error_preserves_invalid_model_response_for_diagnostics(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [],
            "hold_decision": {
                "summary": "대기",
                "reasons": ["lane review omitted by model"],
            },
        },
        include_lane_review=False,
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    latest = trader.repository.latest_manager_run()

    assert result["status"] == "error"
    assert latest["status"] == "error"
    assert latest["response"]["create_blocks"] == []
    assert latest["response"]["hold_decision"]["summary"] == "대기"
    assert "lane_review" not in latest["response"]


def test_manager_rejects_near_duplicate_same_symbol_price_structure(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 100.4,
                    "entry_trigger_operator": ">=",
                    "qty": 0.02,
                    "entry_price": 100.4,
                    "target_price": 98.2,
                    "stop_price": 101.7,
                    "confidence": 0.86,
                    "margin_type": "isolated",
                    "leverage": 1,
                    "thesis": "Duplicate pullback short around the same BTC range",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "qty": 0.02,
            "qty_open": 0.02,
            "entry_price": 100.0,
            "target_price": 98.0,
            "stop_price": 101.5,
            "liquidation_price": 150.0,
            "margin_type": "isolated",
            "leverage": 1,
            "status": "open",
            "thesis": "Existing pullback short around the same BTC range",
            "metadata": {"horizon": "futures"},
        }
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short_watch",
                    "confidence": 0.86,
                    "price": 99.8,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith("near_duplicate_block")
    assert created["near_duplicate_block"]["existing_block_id"].startswith(
        "bnb_futures_BTCUSDT_"
    )
    assert len(trader.list_blocks(include_closed=True)) == 1


def test_manager_rejects_near_duplicate_calculated_price_plan_action(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "entry_style": "wait_for_price",
                    "entry_trigger_operator": ">=",
                    "qty": 0.004,
                    "confidence": 0.84,
                    "margin_type": "isolated",
                    "leverage": 1,
                    "liquidation_price": 5600.0,
                    "thesis": "Near-identical BTC short using calculated price plan only",
                    "calculated_price_plan": {
                        "entry_price": 4154.65,
                        "entry_trigger_price": 4154.65,
                        "entry_trigger_operator": ">=",
                        "target_price": 4088.18,
                        "stop_price": 4187.89,
                        "lane": "futures",
                    },
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "qty": 0.004,
            "qty_open": 0.004,
            "entry_price": 4150.64,
            "target_price": 4084.23,
            "stop_price": 4183.84,
            "liquidation_price": 5603.36,
            "margin_type": "isolated",
            "leverage": 1,
            "status": "open",
            "thesis": "Existing BTC futures short in the same price pocket",
            "metadata": {"horizon": "futures", "lane": "futures"},
        }
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short_watch",
                    "confidence": 0.84,
                    "price": 4135.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith("near_duplicate_block")
    assert created["near_duplicate_block"]["candidate"]["entry_price"] == pytest.approx(
        4154.65
    )
    assert len(trader.list_blocks(include_closed=True)) == 1


def test_binance_manager_prompt_requires_horizon_lanes(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert prompt["output_schema"]["create_blocks"][0]["horizon"] == (
        "short|mid|long for spot/upbit_spot; futures only when market=futures"
    )
    repair_candidate_schema = prompt["output_schema"]["validation_repair_resolution"][
        "resolved_candidates"
    ][0]
    assert "memory_contract_resolution" in repair_candidate_schema
    assert "memory_contract" in repair_candidate_schema
    assert "memory_contract_error" in repair_candidate_schema
    assert prompt["horizon_policy"]["short"].startswith("Short")
    assert "Do not close long horizon spot blocks" in prompt["horizon_policy"]["long"]
    assert prompt["horizon_action_authority"]["futures"] == "active_high_risk_trade"


def test_binance_manager_prompt_audits_short_concentration_and_preserves_lane_diversity(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm, quant_context_limit=8)
    trader.config.max_manager_symbols = 8
    for index in range(9):
        trader.create_block(
            {
                "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "market": "futures",
                "side": "short",
                "qty": 0.1,
                "entry_price": 100.0 + index,
                "target_price": 96.0 + index,
                "stop_price": 102.0 + index,
                "status": "closed",
                "thesis": "recent futures short block",
            }
        )
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 10.0,
            "target_price": 12.0,
            "stop_price": 9.0,
            "status": "closed",
            "thesis": "older spot long block",
        }
    )
    candidates = [
        {
            "symbol": f"SHRT{index}USDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "stance": "short_watch",
            "confidence": 0.76,
            "price": 100.0 + index,
        }
        for index in range(10)
    ] + [
        {
            "symbol": "NEARUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "stance": "long_watch",
            "confidence": 0.72,
            "price": 2.0,
        },
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "long",
            "horizon": "futures",
            "stance": "long_watch",
            "confidence": 0.74,
            "price": 150.0,
        },
    ]

    asyncio.run(trader.run_manager_once(candidates=candidates))
    prompt = llm.calls[0]["payload"]

    lanes = {f"{row['market']}:{row['side']}" for row in prompt["candidates"]}
    assert {"futures:short", "spot:long", "futures:long"}.issubset(lanes)
    assert prompt["candidate_generation"]["candidate_lane_diversified"] is True
    assert prompt["lane_balance"]["recent_blocks"]["dominant_lane"] == "futures:short"
    assert prompt["lane_balance"]["recent_blocks"]["requires_review"] is True
    assert prompt["lane_balance"]["recent_blocks"]["dominant_share_pct"] == pytest.approx(90.0)
    assert prompt["lane_balance"]["candidate_lanes"]["items"]["spot:long"]["count"] >= 1
    assert prompt["lane_balance"]["candidate_lanes"]["items"]["futures:long"]["count"] >= 1
    assert "lane_balance" in prompt["decision_inputs"]
    assert "futures:short" in prompt["policy"]["lane_balance_policy"]
    assert "not a hard filter" in prompt["policy"]["lane_balance_policy"]
    assert "lane_review" in prompt["output_schema"]


def test_binance_manager_prompt_surfaces_near_duplicate_active_blocks(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    for entry_price in (100.0, 100.42):
        trader.create_block(
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "qty": 0.02,
                "qty_open": 0.02,
                "entry_price": entry_price,
                "target_price": 98.0 + (entry_price - 100.0),
                "stop_price": 101.5 + (entry_price - 100.0),
                "liquidation_price": 150.0,
                "margin_type": "isolated",
                "leverage": 1,
                "status": "open",
                "thesis": "near duplicate active exposure",
                "metadata": {"horizon": "futures"},
            }
        )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "ETHUSDT"}]))
    prompt = llm.calls[0]["payload"]

    duplicate_exposure = prompt["lane_balance"]["near_duplicate_active_blocks"]
    assert duplicate_exposure["status"] == "review_required"
    assert duplicate_exposure["group_count"] == 1
    group = duplicate_exposure["groups"][0]
    assert group["symbol"] == "BTCUSDT"
    assert group["market"] == "futures"
    assert group["side"] == "short"
    assert group["horizon"] == "futures"
    assert group["block_count"] == 2
    assert "avoid adding" in duplicate_exposure["instruction"]


def test_binance_manager_prompt_requires_korean_user_facing_notes(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert prompt["language_policy"]["internal_reasoning_language"] == "en-US"
    assert prompt["language_policy"]["operator_display_language"] == "ko-KR"
    assert prompt["language_policy"]["user_visible_generation_order"] == (
        "draft_conclusion_in_english_then_translate_to_korean_for_display"
    )
    assert prompt["output_language_policy"] == prompt["language_policy"]
    assert "Korean" in prompt["policy"]["hold_decision_policy"]
    assert "hold_decision" in prompt["output_language_policy"]["applies_to"]


def test_binance_manager_prompt_includes_execution_gate_context(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 125.0,
        "futures_cash_usdt": 250.0,
        "upbit_cash_krw": 100_000.0,
        "upbit_cash_usdt": 72.0,
        "total_equity_usdt": 447.0,
    }
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        execute_spot=True,
        execute_futures=True,
        execute_upbit=True,
    )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]
    gate = prompt["execution_gate"]

    assert "execution_gate" in prompt["decision_inputs"]
    assert gate["status"] == "ok"
    assert gate["kill_switch"]["enabled"] is False
    assert gate["execution"]["spot_orders_enabled"] is True
    assert gate["execution"]["futures_orders_enabled"] is True
    assert gate["execution"]["upbit_orders_enabled"] is True
    assert set(gate["live_venues"]) == {"spot", "futures", "upbit_spot"}
    assert gate["cash_available"]["spot_cash_usdt"] == pytest.approx(125.0)
    assert gate["cash_available"]["futures_cash_usdt"] == pytest.approx(250.0)
    assert gate["cash_available"]["upbit_cash_krw"] == pytest.approx(100_000.0)
    assert gate["duplicate_order_guard"]["status"] == "ok"


def test_binance_manager_prompt_compacts_large_futures_position_risk(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 1000.0,
        "futures_cash_usdt": 500.0,
        "spot_assets": [
            {"asset": "USDT", "kind": "cash", "qty": 1000.0, "raw": "DROP_ME" * 200},
            {"asset": "INJ", "kind": "position", "qty": 1.25, "value_usdt": 8.0},
        ],
        "futures_assets": [
            {"asset": "USDT-FUT", "kind": "cash", "qty": 500.0, "raw": "DROP_ME" * 200},
        ],
        "futures_position_risk": [
            {
                "symbol": f"ZERO{i}USDT",
                "position_amt": 0.0,
                "entry_price": 0.0,
                "mark_price": 0.0,
                "liquidation_price": 0.0,
                "leverage": 20,
                "margin_type": "cross",
                "unrealized_profit": 0.0,
                "raw": {"large": "X" * 1000},
            }
            for i in range(120)
        ]
        + [
            {
                "symbol": "SUIUSDT",
                "position_amt": -3.5,
                "entry_price": 3.2,
                "mark_price": 3.1,
                "liquidation_price": 4.2,
                "leverage": 2,
                "margin_type": "isolated",
                "unrealized_profit": 0.35,
                "raw": {"large": "Y" * 1000},
            }
        ],
    }
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    account = llm.calls[0]["payload"]["account"]
    rendered = json.dumps(account, ensure_ascii=False)

    assert account["spot_cash_usdt"] == pytest.approx(1000.0)
    assert account["futures_cash_usdt"] == pytest.approx(500.0)
    assert account["futures_position_risk_summary"] == {
        "total_count": 121,
        "nonzero_count": 1,
        "visible_count": 1,
        "omitted_zero_count": 120,
    }
    assert account["futures_position_risk"][0]["symbol"] == "SUIUSDT"
    assert "raw" not in rendered
    assert "DROP_ME" not in rendered
    assert len(rendered) < 6000


def test_manager_rejects_low_conviction_hold_candidate_create(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                    "thesis": "LLM tried to trade a hold candidate",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "hold",
                    "confidence": 0.45,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"].startswith("entry_gate_rejected")
    assert trader.list_blocks() == []


def test_manager_rejects_create_when_quant_edge_is_weak(
    tmp_path: Path,
) -> None:
    class QuantProvider:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            _ = (symbols, limit)
            return [
                {
                    "symbol": "BTCUSDT",
                    "horizon": "short",
                    "long_score": 56.0,
                    "short_score": 50.0,
                    "no_trade_score": 30.0,
                    "expected_r_long": 0.26,
                    "expected_r_short": 0.2,
                    "signal": {"bias": "long"},
                    "updated_at": "2026-05-27T00:00:00+00:00",
                }
            ]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str = "intraday",
            points_per_symbol: int = 12,
        ) -> dict[str, Any]:
            _ = (symbols, horizon, points_per_symbol)
            return {"status": "ok", "items": []}

    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm, quant_provider=QuantProvider())

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.72,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert "quant_expected_r_too_low" in result["applied"]["created"][0]["reason"]
    assert trader.list_blocks() == []


def test_manager_rejects_high_chase_immediate_binance_entry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 106.0,
                    "stop_price": 97.0,
                    "entry_quality": "extended_momentum",
                    "chase_risk": "high",
                    "price_location": "near_24h_high",
                    "confidence": 0.82,
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long",
                    "confidence": 0.84,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith("entry_gate_rejected")
    assert "entry_quality_requires_waiting_entry" in created["reason"]
    assert created["gate"]["entry_quality"]["requires_waiting_entry"] is True
    assert trader.list_blocks() == []


def test_manager_rejects_wait_pullback_quality_when_binance_entry_is_immediate(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "mid",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 108.0,
                    "stop_price": 96.0,
                    "entry_quality": "wait_pullback",
                    "price_location": "near_24h_high",
                    "regime_alignment": "positive aligned",
                    "funding_context": "neutral",
                    "confidence": 0.78,
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "mid",
                    "stance": "long",
                    "confidence": 0.78,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    gate = created["gate"]["entry_quality"]
    assert created["status"] == "rejected"
    assert "entry_quality_requires_waiting_entry" in created["reason"]
    assert gate["hard_pressure"] is True
    assert gate["price_relief_present"] is False
    assert "regime_aligned" in gate["confluence"]
    assert "funding_not_hostile" in gate["confluence"]
    assert trader.list_blocks() == []


def test_manager_allows_high_chase_binance_waiting_entry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 98.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 98.0,
                    "target_price": 106.0,
                    "stop_price": 95.0,
                    "entry_quality": "extended_momentum",
                    "chase_risk": "high",
                    "price_location": "near_24h_high",
                    "confidence": 0.82,
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long",
                    "confidence": 0.84,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "waiting_entry"
    blocks = trader.list_blocks()
    assert len(blocks) == 1
    assert blocks[0]["metadata"]["entry_gate"]["entry_quality"][
        "requires_waiting_entry"
    ] is False


def test_manager_allows_waiting_entry_with_base_edge_when_recent_performance_is_weak(
    tmp_path: Path,
) -> None:
    class QuantProvider:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            _ = (symbols, limit)
            return [
                {
                    "symbol": "BTCUSDT",
                    "horizon": "short",
                    "long_score": 63.0,
                    "short_score": 50.0,
                    "no_trade_score": 30.0,
                    "expected_r_long": 0.56,
                    "expected_r_short": 0.2,
                    "signal": {"bias": "long"},
                    "updated_at": "2026-05-27T00:00:00+00:00",
                }
            ]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str = "intraday",
            points_per_symbol: int = 12,
        ) -> dict[str, Any]:
            _ = (symbols, horizon, points_per_symbol)
            return {"status": "ok", "items": []}

    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 98.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 98.0,
                    "target_price": 104.0,
                    "stop_price": 96.0,
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm, quant_provider=QuantProvider())
    for index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"loss-{index}",
                "symbol": "BTCUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100,
                "exit_price": 98,
                "stop_price": 98,
                "target_price": 104,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.59,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks()[0]
    assert block["status"] == "proposed"
    assert block["metadata"]["entry_style"] == "wait_for_price"
    assert block["metadata"]["entry_gate"]["ok"] is True
    assert block["metadata"]["entry_gate"]["policy"]["adjustment"] == "tightened_by_recent_reflections"
    assert block["metadata"]["entry_gate"]["waiting_entry_relaxed"] is True


def test_distressed_spot_wait_crosscheck_rejects_new_waiting_entry(
    tmp_path: Path,
) -> None:
    class QuantProvider:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            _ = (symbols, limit)
            return [
                {
                    "symbol": "BTCUSDT",
                    "horizon": "short",
                    "long_score": 91.0,
                    "short_score": 20.0,
                    "no_trade_score": 10.0,
                    "expected_r_long": 1.25,
                    "expected_r_short": 0.05,
                    "signal": {"bias": "long"},
                    "updated_at": "2026-06-19T00:00:00+00:00",
                }
            ]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str = "intraday",
            points_per_symbol: int = 12,
        ) -> dict[str, Any]:
            _ = (symbols, horizon, points_per_symbol)
            return {"status": "ok", "items": []}

    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.92,
                    "qty": 0.1,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 98.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 98.0,
                    "target_price": 106.0,
                    "stop_price": 96.0,
                    "evidence_refs": ["crypto_quant:BTCUSDT"],
                    "calculated_price_plan": {
                        "lane": "short",
                        "entry_style": "wait_for_price",
                        "entry_price": 98.0,
                        "target_price": 106.0,
                        "stop_price": 96.0,
                        "pattern_live_crosscheck": {"status": "wait"},
                    },
                    "pattern_live_crosscheck": {"status": "wait"},
                    "thesis": "Strong-looking spot pullback, but the live pattern gate is still waiting.",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm, quant_provider=QuantProvider())
    for index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"recent-spot-wait-loss-{index}",
                "symbol": f"LOSS{index}USDT",
                "market": "spot",
                "side": "long",
                "lane": "short",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.5,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.92,
                    "price": 100.0,
                    "calculated": {
                        "pattern_live_crosscheck": {"status": "wait"},
                    },
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith("lane_performance_cooldown:spot:long")
    assert created["lane_performance_cooldown"]["distressed"] is True
    assert trader.list_blocks(include_closed=True) == []


def test_deep_negative_spot_long_lane_rejects_new_waiting_entry(
    tmp_path: Path,
) -> None:
    class QuantProvider:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            _ = (symbols, limit)
            return [
                {
                    "symbol": "BTCUSDT",
                    "horizon": "short",
                    "long_score": 91.0,
                    "short_score": 20.0,
                    "no_trade_score": 10.0,
                    "expected_r_long": 1.25,
                    "expected_r_short": 0.05,
                    "signal": {"bias": "long"},
                    "updated_at": "2026-06-19T00:00:00+00:00",
                }
            ]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str = "intraday",
            points_per_symbol: int = 12,
        ) -> dict[str, Any]:
            _ = (symbols, horizon, points_per_symbol)
            return {"status": "ok", "items": []}

    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.92,
                    "qty": 0.1,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 98.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 98.0,
                    "target_price": 106.0,
                    "stop_price": 96.0,
                    "evidence_refs": ["crypto_quant:BTCUSDT"],
                    "thesis": "strong-looking pullback, but lane is losing deeply",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm, quant_provider=QuantProvider())
    for index in range(12):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"spot-long-loss-{index}",
                "symbol": f"LOSS{index}USDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.5,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.92,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith("lane_performance_cooldown:spot:long")
    assert trader.list_blocks(include_closed=True) == []


def test_validation_repair_qty_scale_keeps_minimum_executable_probe_notional() -> None:
    row = {
        "symbol": "XPLUSDT",
        "market": "spot",
        "side": "long",
        "qty": 1125.3769,
        "entry_price": 0.091205,
        "target_price": 0.093394,
        "stop_price": 0.090110,
    }
    repair = {
        "status": "needs_repair",
        "scale_up_blocked": True,
        "risk_budget_multiplier": 0.01,
        "allowed_entry_postures": ["fractional_kelly_probe"],
        "sizing_policies": ["fractional_kelly_probe_only"],
    }

    enforcement = BinanceBlockTrader._validation_repair_create_enforcement(row, repair)

    assert enforcement["adjustments"][0]["field"] == "qty"
    assert enforcement["adjustments"][0]["floor_reason"] == (
        "minimum_executable_notional_floor"
    )
    assert row["qty"] * row["entry_price"] >= 20.0 - 1e-9


def test_lane_authority_scale_keeps_validation_probe_above_minimum_notional() -> None:
    row = {
        "symbol": "XPLUSDT",
        "market": "spot",
        "side": "long",
        "qty": 8.79200703125,
        "entry_price": 0.091205,
        "entry_trigger_price": 0.091205,
        "metadata": {
            "validation_repair_enforcement": {
                "scale_up_blocked": True,
                "waiting_entry_required": True,
                "adjustments": [
                    {
                        "field": "qty",
                        "from": 1125.3769,
                        "to": 281.344225,
                        "reason": "validation_repair_scale_up_blocked_probe_qty",
                    }
                ],
            }
        },
    }

    adjusted = BinanceBlockTrader._apply_lane_authority_budget_to_row(
        row,
        {
            "budget_multiplier": 0.25,
            "matched_lanes": ["spot"],
            "reason": "de_risk_or_waiting_entry",
        },
    )

    assert adjusted["qty"] * adjusted["entry_price"] >= 20.0 - 1e-9
    assert adjusted["qty"] <= 281.344225 + 1e-9
    assert adjusted["metadata"]["lane_authority_budget_adjustment"][
        "floor_reason_qty"
    ] == "minimum_executable_notional_floor"


def test_validation_probe_waiting_entry_can_recover_through_lane_cooldown(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.lane_performance_min_samples = 3
    trader.config.budget_performance_scale_min_samples = 3
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"spot-lane-loss-{index}",
                "symbol": f"LOSS{index}USDT",
                "market": "spot",
                "side": "long",
                "lane": "short",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
            }
        )
    row = {
        "symbol": "XPLUSDT",
        "market": "spot",
        "side": "long",
        "horizon": "short",
        "lane": "spot:long",
        "entry_style": "wait_for_price",
        "entry_trigger_price": 0.091205,
        "entry_trigger_operator": "<=",
        "entry_price": 0.091205,
        "target_price": 0.093394,
        "stop_price": 0.090110,
        "qty": 219.28622334,
        "metadata": {
            "validation_repair": {
                "status": "needs_repair",
                "hard_filter": False,
                "allowed_entry_postures": [
                    "fractional_kelly_probe",
                    "cost_verified_waiting_entry",
                ],
                "scale_up_blocked": True,
            },
            "validation_repair_enforcement": {
                "scale_up_blocked": True,
                "waiting_entry_required": True,
            },
        },
    }

    rejection = trader._lane_performance_cooldown_rejection(row)  # noqa: SLF001

    assert rejection is None
    assert row["metadata"]["performance_cooldown_recovery"]["kind"] == (
        "validation_probe"
    )


def test_risk_sizing_updates_existing_qty_initial_after_probe_floor(
    tmp_path: Path,
) -> None:
    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "status": "ok",
                "qty": 130.0,
                "risk_budget_usdt": 0.14,
                "reward_risk": 2.0,
            }

    trader = _trader(tmp_path)
    trader.risk_sizer = FixedRiskSizer()
    trader._last_account_snapshot = {  # noqa: SLF001
        "spot_cash_usdt": 4_000.0,
        "futures_cash_usdt": 500.0,
    }
    payload = trader._apply_risk_sizing(  # noqa: SLF001
        {
            "symbol": "XPLUSDT",
            "market": "spot",
            "side": "long",
            "lane": "spot:long",
            "entry_price": 0.091603,
            "target_price": 0.093801,
            "stop_price": 0.090504,
            "qty": 218.33346069451872,
            "qty_initial": 218.33346069451872,
            "created_by": "llm",
            "metadata": {
                "validation_repair_enforcement": {
                    "scale_up_blocked": True,
                    "waiting_entry_required": True,
                    "adjustments": [
                        {
                            "field": "qty",
                            "from": 1120.48,
                            "to": 280.12,
                        }
                    ],
                }
            },
        }
    )

    assert payload["qty"] == pytest.approx(payload["qty_initial"])
    assert payload["qty"] * payload["entry_price"] >= 20.0 - 1e-9
    sizing = payload["metadata"]["risk_sizing"]
    assert sizing["raw_risk_sizer_qty"] == pytest.approx(130.0)
    assert sizing["minimum_executable_notional_floor"]["notional_floor"] == pytest.approx(
        20.0
    )


def test_cost_drag_negative_spot_lane_rejects_even_with_mixed_wins(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.9,
                    "qty": 0.05,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 1980.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 1980.0,
                    "target_price": 2100.0,
                    "stop_price": 1940.0,
                    "evidence_refs": ["crypto_quant:ETHUSDT"],
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"small-win-{index}",
                "symbol": f"WIN{index}USDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 100.2,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": 0.1,
                "r_multiple": 0.05,
                "lesson": {"result": "positive"},
            }
        )
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"large-loss-{index}",
                "symbol": f"LOSS{index}USDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.7,
                "r_multiple": -0.35,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.9,
                    "price": 2000.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith("lane_performance_cooldown:spot:long")
    assert created["lane_performance_cooldown"]["win_rate_pct"] == pytest.approx(50.0)
    assert created["lane_performance_cooldown"]["profit_factor"] < 1.0
    assert trader.list_blocks(include_closed=True) == []


def test_negative_wait_pullback_entry_quality_rejects_matching_spot_long(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.91,
                    "qty": 0.05,
                    "entry_style": "wait_for_price",
                    "entry_quality": "wait_pullback",
                    "entry_trigger_price": 1980.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 1980.0,
                    "target_price": 2100.0,
                    "stop_price": 1940.0,
                    "calculated_price_plan": {
                        "raw_entry_quality": "wait_pullback",
                        "entry_style": "wait_for_price",
                        "pattern_live_crosscheck": {
                            "status": "aligned",
                            "checks": ["book_spread_ok", "funding_acceptable"],
                        },
                    },
                    "evidence_refs": ["crypto_quant:ETHUSDT"],
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader._runtime_market_universe = {
        "spot": ["ETHUSDT", *[f"PULL{index}USDT" for index in range(12)]],
        "futures": [],
        "upbit_spot": [],
    }
    for index in range(12):
        block = trader.create_block(
            {
                "symbol": f"PULL{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 106.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-19T00:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "wait_pullback",
                    "entry_style": "wait_for_price",
                    "calculated_price_plan": {
                        "raw_entry_quality": "wait_pullback",
                        "entry_style": "wait_for_price",
                    },
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.7,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.91,
                    "price": 2000.0,
                    "entry_quality": "wait_pullback",
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith(
        "entry_quality_performance_cooldown:spot:long:wait_pullback"
    )
    assert created["entry_quality_performance_cooldown"]["sample_count"] == 12
    assert trader.list_blocks(include_closed=False) == []


def test_negative_entry_quality_rejects_after_lane_min_samples_even_when_lane_is_mixed(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.91,
                    "qty": 0.05,
                    "entry_style": "wait_for_price",
                    "entry_quality": "wait_pullback",
                    "entry_trigger_price": 1980.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 1980.0,
                    "target_price": 2100.0,
                    "stop_price": 1940.0,
                    "calculated_price_plan": {
                        "raw_entry_quality": "wait_pullback",
                        "entry_style": "wait_for_price",
                        "pattern_live_crosscheck": {
                            "status": "aligned",
                            "checks": ["book_spread_ok", "funding_acceptable"],
                        },
                    },
                    "evidence_refs": ["crypto_quant:ETHUSDT"],
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader._runtime_market_universe = {
        "spot": [
            "ETHUSDT",
            *[f"PULL{index}USDT" for index in range(3)],
            *[f"EDGE{index}USDT" for index in range(8)],
        ],
        "futures": [],
        "upbit_spot": [],
    }
    for index in range(3):
        block = trader.create_block(
            {
                "symbol": f"PULL{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 106.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-19T00:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "wait_pullback",
                    "entry_style": "wait_for_price",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.7,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )
    for index in range(8):
        block = trader.create_block(
            {
                "symbol": f"EDGE{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 106.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-19T01:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "actionable_now",
                    "entry_style": "immediate",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 106.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": 0.4,
                "r_multiple": 1.0,
                "lesson": {"result": "positive"},
            }
        )

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "ETHUSDT"}]))

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith(
        "entry_quality_performance_cooldown:spot:long:wait_pullback"
    )
    assert created["entry_quality_performance_cooldown"]["sample_count"] == 3
    assert created["entry_quality_performance_cooldown"]["min_samples"] == 3
    assert trader.list_blocks(include_closed=False) == []


def test_recovered_entry_quality_recent_window_can_create_waiting_block(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "intraday",
                    "confidence": 0.91,
                    "qty": 0.2,
                    "entry_style": "wait_for_price",
                    "entry_quality": "wait_pullback",
                    "entry_trigger_price": 205.0,
                    "entry_trigger_operator": ">=",
                    "entry_price": 205.0,
                    "target_price": 195.0,
                    "stop_price": 210.0,
                    "calculated_price_plan": {
                        "raw_entry_quality": "wait_pullback",
                        "entry_style": "wait_for_price",
                        "pattern_live_crosscheck": {
                            "status": "aligned",
                            "checks": ["book_spread_ok", "funding_acceptable"],
                        },
                    },
                    "evidence_refs": ["crypto_quant:ETHUSDT"],
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader._runtime_market_universe = {
        "spot": [],
        "futures": [
            "ETHUSDT",
            *[f"OLD{index}USDT" for index in range(30)],
            *[f"REC{index}USDT" for index in range(20)],
        ],
        "upbit_spot": [],
    }
    for index in range(30):
        block = trader.create_block(
            {
                "symbol": f"OLD{index}USDT",
                "market": "futures",
                "side": "short",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 94.0,
                "stop_price": 103.0,
                "status": "closed",
                "closed_at": f"2026-06-18T00:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "wait_pullback",
                    "entry_style": "wait_for_price",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 103.0,
                "stop_price": 103.0,
                "target_price": 94.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
                "created_at": block["closed_at"],
            }
        )
    for index in range(20):
        block = trader.create_block(
            {
                "symbol": f"REC{index}USDT",
                "market": "futures",
                "side": "short",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 94.0,
                "stop_price": 103.0,
                "status": "closed",
                "closed_at": f"2026-06-19T00:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "wait_pullback",
                    "entry_style": "wait_for_price",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 96.0,
                "stop_price": 103.0,
                "target_price": 94.0,
                "pnl_usdt": 0.3,
                "r_multiple": 0.6,
                "lesson": {"result": "positive"},
                "created_at": block["closed_at"],
            }
        )

    long_window = trader.repository.latest_performance_scorecard(limit=120)
    recent_window = trader.repository.latest_performance_scorecard(limit=20)

    long_quality = next(
        row
        for row in long_window["entry_quality_scorecards"]
        if row["entry_quality_lane"] == "futures:short:wait_pullback"
    )
    recent_quality = next(
        row
        for row in recent_window["entry_quality_scorecards"]
        if row["entry_quality_lane"] == "futures:short:wait_pullback"
    )
    assert long_quality["profit_factor"] < 1.0
    assert recent_quality["profit_factor"] > 1.0

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "intraday",
                    "stance": "short_watch",
                    "confidence": 0.91,
                    "price": 200.0,
                    "entry_quality": "wait_pullback",
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "waiting_entry"
    assert not str(created.get("reason") or "").startswith(
        "entry_quality_performance_cooldown"
    )


def test_futures_wait_pullback_requires_live_microstructure_confirmation(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "intraday",
                    "confidence": 0.91,
                    "qty": 0.2,
                    "entry_style": "wait_for_price",
                    "entry_quality": "wait_pullback",
                    "entry_trigger_price": 205.0,
                    "entry_trigger_operator": ">=",
                    "entry_price": 205.0,
                    "target_price": 195.0,
                    "stop_price": 210.0,
                    "calculated_price_plan": {
                        "raw_entry_quality": "wait_pullback",
                        "entry_style": "wait_for_price",
                    },
                    "evidence_refs": ["crypto_quant:ETHUSDT"],
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader._runtime_market_universe = {
        "spot": [],
        "futures": ["ETHUSDT"],
        "upbit_spot": [],
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "intraday",
                    "stance": "short_watch",
                    "confidence": 0.91,
                    "price": 200.0,
                    "entry_quality": "wait_pullback",
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == "wait_pullback_live_confirmation_required"
    assert created["wait_pullback_confirmation"]["market"] == "futures"
    assert created["wait_pullback_confirmation"]["entry_quality"] == "wait_pullback"
    assert trader.list_blocks(include_closed=False) == []


def test_recent_lane_recovery_uses_lane_min_samples_not_budget_scale_minimum(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader._runtime_market_universe = {
        "spot": [*[f"FILL{index}USDT" for index in range(14)]],
        "futures": [
            "ETHUSDT",
            *[f"OLD{index}USDT" for index in range(30)],
            *[f"REC{index}USDT" for index in range(6)],
        ],
        "upbit_spot": [],
    }
    for index in range(30):
        block = trader.create_block(
            {
                "symbol": f"OLD{index}USDT",
                "market": "futures",
                "side": "short",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 94.0,
                "stop_price": 103.0,
                "status": "closed",
                "closed_at": f"2026-06-17T00:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "wait_pullback",
                    "entry_style": "wait_for_price",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 103.0,
                "stop_price": 103.0,
                "target_price": 94.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
                "created_at": block["closed_at"],
            }
        )
    for index in range(14):
        block = trader.create_block(
            {
                "symbol": f"FILL{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 106.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-18T00:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "actionable_now",
                    "entry_style": "immediate",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.1,
                "r_multiple": -0.2,
                "lesson": {"result": "filler"},
                "created_at": block["closed_at"],
            }
        )
    for index in range(6):
        block = trader.create_block(
            {
                "symbol": f"REC{index}USDT",
                "market": "futures",
                "side": "short",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 94.0,
                "stop_price": 103.0,
                "status": "closed",
                "closed_at": f"2026-06-19T00:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "wait_pullback",
                    "entry_style": "wait_for_price",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 96.0,
                "stop_price": 103.0,
                "target_price": 94.0,
                "pnl_usdt": 0.3,
                "r_multiple": 0.6,
                "lesson": {"result": "positive"},
                "created_at": block["closed_at"],
            }
        )

    row = {
        "symbol": "ETHUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "intraday",
        "confidence": 0.91,
        "entry_style": "wait_for_price",
        "entry_quality": "wait_pullback",
        "metadata": {"entry_quality": "wait_pullback"},
        "calculated": {"lane": "futures"},
    }

    assert trader._entry_quality_performance_cooldown_rejection(row) is None
    assert row["metadata"]["performance_cooldown_recovery"]["kind"] == "entry_quality"
    assert trader._lane_performance_cooldown_rejection(row) is None


def test_symbol_cost_drag_rejects_repeated_candidate_before_lane_freezes(
    tmp_path: Path,
) -> None:
    class QuantProvider:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            _ = (symbols, limit)
            return [
                {
                    "symbol": "BIOUSDT",
                    "horizon": "futures",
                    "long_score": 88.0,
                    "short_score": 30.0,
                    "no_trade_score": 12.0,
                    "expected_r_long": 1.4,
                    "expected_r_short": 0.2,
                    "signal": {"bias": "long"},
                    "updated_at": "2026-06-19T00:00:00+00:00",
                }
            ]

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str = "intraday",
            points_per_symbol: int = 12,
        ) -> dict[str, Any]:
            _ = (symbols, horizon, points_per_symbol)
            return {"status": "ok", "items": []}

    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BIOUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "confidence": 0.9,
                    "qty": 550.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 0.0353,
                    "entry_trigger_operator": "<=",
                    "entry_price": 0.0353,
                    "target_price": 0.0372,
                    "stop_price": 0.0346,
                    "margin_type": "isolated",
                    "leverage": 1,
                    "liquidation_price": 0.0,
                    "evidence_refs": ["crypto_quant:BIOUSDT"],
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm, quant_provider=QuantProvider())
    for index in range(2):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"bio-win-{index}",
                "symbol": "BIOUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 0.035,
                "exit_price": 0.03535,
                "stop_price": 0.0343,
                "target_price": 0.0364,
                "pnl_usdt": 0.12,
                "r_multiple": 0.2,
                "lesson": {"result": "positive"},
            }
        )
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"bio-loss-{index}",
                "symbol": "BIOUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 0.035,
                "exit_price": 0.03445,
                "stop_price": 0.03445,
                "target_price": 0.0364,
                "pnl_usdt": -0.22,
                "r_multiple": -0.35,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BIOUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long_watch",
                    "confidence": 0.9,
                    "price": 0.0355,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"].startswith("symbol_performance_cooldown:BIOUSDT")
    assert created["symbol_performance_cooldown"]["sample_count"] == 5
    assert created["symbol_performance_cooldown"]["profit_factor"] < 1.0
    assert trader.list_blocks(include_closed=True) == []


def test_futures_losses_do_not_freeze_spot_normal_entries(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.59,
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                    "thesis": "spot setup should use its own lane evidence",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    for index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"futures-short-loss-{index}",
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100,
                "exit_price": 102,
                "stop_price": 102,
                "target_price": 96,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.59,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "created"
    block = trader.list_blocks()[0]
    entry_gate = block["metadata"]["entry_gate"]
    assert entry_gate["effective_policy"]["effective_adjustment"] == "base_gate_for_fresh_lane"
    assert entry_gate["effective_policy"]["min_confidence"] == pytest.approx(0.58)


def test_repeated_symbol_lane_losses_cool_down_same_futures_short_entry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "confidence": 0.82,
                    "qty": 0.02,
                    "entry_price": 600.0,
                    "target_price": 570.0,
                    "stop_price": 615.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 800.0,
                    "thesis": "do not repeat a losing same-lane setup without cooldown",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **kwargs: {
                "status": "ok",
                "candidates": [{"symbol": "BNBUSDT", "market": "futures"}],
            }
        },
    )()
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"bnb-short-loss-{index}",
                "symbol": "BNBUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 600.0,
                "exit_price": 615.0,
                "stop_price": 615.0,
                "target_price": 570.0,
                "pnl_usdt": -0.3,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    result = asyncio.run(trader.run_manager_once(candidates=[]))

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert "symbol_lane_cooldown" in result["applied"]["created"][0]["reason"]
    assert trader.list_blocks(include_closed=True) == []


def test_manager_prompt_exposes_exploratory_waiting_entry_policy_after_losses(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    for index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"loss-{index}",
                "symbol": "BTCUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100,
                "exit_price": 98,
                "stop_price": 98,
                "target_price": 104,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    policy = llm.calls[0]["payload"]["entry_gate_policy"]

    assert policy["adjustment"] == "tightened_by_recent_reflections"
    assert policy["waiting_entry_policy"]["enabled"] is True
    assert policy["waiting_entry_policy"]["min_confidence"] == pytest.approx(0.58)
    assert policy["waiting_entry_policy"]["max_new_waiting_blocks_per_run"] == 1


def test_manager_prompt_marks_negative_cost_drag_lane_as_cooldown(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"prompt-small-win-{index}",
                "symbol": f"WIN{index}USDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 100.2,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": 0.1,
                "r_multiple": 0.05,
                "lesson": {"result": "positive"},
            }
        )
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"prompt-large-loss-{index}",
                "symbol": f"LOSS{index}USDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.7,
                "r_multiple": -0.35,
                "lesson": {"result": "negative"},
            }
        )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "ETHUSDT"}]))
    policy = llm.calls[0]["payload"]["entry_gate_policy"]

    assert policy["cooldown_lanes"]["spot:long"]["status"] == "cooldown"
    assert policy["cooldown_lanes"]["spot:long"]["profit_factor"] < 1.0
    assert "spot:long" in policy["cooldown_lane_keys"]


def test_manager_prompt_marks_negative_entry_quality_as_cooldown(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader._runtime_market_universe = {
        "spot": ["ETHUSDT", *[f"PULL{index}USDT" for index in range(12)]],
        "futures": [],
        "upbit_spot": [],
    }
    for index in range(12):
        block = trader.create_block(
            {
                "symbol": f"PULL{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 106.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-19T01:{index:02d}:00+00:00",
                "metadata": {
                    "entry_quality": "wait_pullback",
                    "entry_style": "wait_for_price",
                    "calculated_price_plan": {
                        "raw_entry_quality": "wait_pullback",
                        "entry_style": "wait_for_price",
                    },
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.7,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "ETHUSDT"}]))
    policy = llm.calls[0]["payload"]["entry_gate_policy"]

    cooldown = policy["entry_quality_cooldowns"]["spot:long:wait_pullback"]
    assert cooldown["status"] == "cooldown"
    assert cooldown["sample_count"] == 12
    assert cooldown["profit_factor"] < 1.0
    assert "spot:long:wait_pullback" in policy["entry_quality_cooldown_keys"]
    assert "fresh evidence" in cooldown["recovery_required"]


def test_manager_prompt_marks_bad_spot_long_pullback_as_shadow_only(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader._runtime_market_universe = {
        "spot": ["ETHUSDT", *[f"PULL{index}USDT" for index in range(12)]],
        "futures": [],
        "upbit_spot": [],
    }
    for index in range(12):
        block = trader.create_block(
            {
                "symbol": f"PULL{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 106.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-19T01:{index:02d}:00+00:00",
                "metadata": {"entry_quality": "wait_pullback"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": -0.7,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "ETHUSDT"}]))
    policy = llm.calls[0]["payload"]["entry_gate_policy"]

    shadow = policy["shadow_only_entry_qualities"]["spot:long:wait_pullback"]
    assert shadow["status"] == "shadow_only"
    assert shadow["live_budget_multiplier"] == 0.0
    assert shadow["sample_count"] == 12
    assert "spot:long:wait_pullback" in policy["shadow_only_entry_quality_keys"]


def test_manager_prompt_marks_symbol_cost_drag_as_cooldown(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    for index in range(2):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"prompt-bio-win-{index}",
                "symbol": "BIOUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 0.035,
                "exit_price": 0.0352,
                "stop_price": 0.0344,
                "target_price": 0.0364,
                "pnl_usdt": 0.08,
                "r_multiple": 0.15,
                "lesson": {"result": "positive"},
            }
        )
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"prompt-bio-loss-{index}",
                "symbol": "BIOUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 0.035,
                "exit_price": 0.03445,
                "stop_price": 0.03445,
                "target_price": 0.0364,
                "pnl_usdt": -0.22,
                "r_multiple": -0.35,
                "lesson": {"result": "negative"},
            }
        )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BIOUSDT"}]))
    policy = llm.calls[0]["payload"]["entry_gate_policy"]

    assert policy["cooldown_symbols"]["BIOUSDT"]["status"] == "cooldown"
    assert policy["cooldown_symbols"]["BIOUSDT"]["profit_factor"] < 1.0
    assert "BIOUSDT" in policy["cooldown_symbol_keys"]


def test_manager_prompt_adds_candidate_symbol_cooldown_outside_top_scorecards(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.config.symbol_lane_cooldown_min_samples = 5

    for sample_index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"prompt-target-symbol-loss-{sample_index}",
                "symbol": "TARGETUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "stop_price": 98.0,
                "target_price": 104.0,
                "pnl_usdt": -0.2,
                "r_multiple": -0.5,
                "created_at": f"2026-06-18T00:{sample_index:02d}:00+00:00",
            }
        )
    for symbol_index in range(9):
        for sample_index in range(6):
            trader.repository.save_performance_reflection(
                {
                    "block_id": f"prompt-worse-symbol-{symbol_index}-{sample_index}",
                    "symbol": f"WORSE{symbol_index}USDT",
                    "market": "spot",
                    "side": "long",
                    "entry_price": 100.0,
                    "exit_price": 98.0,
                    "stop_price": 98.0,
                    "target_price": 104.0,
                    "pnl_usdt": -2.0,
                    "r_multiple": -1.0,
                    "created_at": f"2026-06-19T00:{symbol_index:02d}:{sample_index:02d}+00:00",
                }
            )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "TARGETUSDT"}]))
    policy = llm.calls[0]["payload"]["entry_gate_policy"]

    assert "TARGETUSDT" in policy["cooldown_symbol_keys"]
    assert policy["cooldown_symbols"]["TARGETUSDT"]["sample_count"] == 5
    assert policy["cooldown_symbols"]["TARGETUSDT"]["pnl_usdt"] == pytest.approx(-1.0)


def test_spot_horizon_lane_cooldown_uses_horizon_scorecard(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader._runtime_market_universe = {
        "spot": ["ETHUSDT", *[f"LOSS{index}USDT" for index in range(10)], *[f"WIN{index}USDT" for index in range(10)]],
        "futures": [],
        "upbit_spot": [],
    }
    for index in range(10):
        block = trader.create_block(
            {
                "symbol": f"LOSS{index}USDT",
                "market": "spot",
                "side": "long",
                "horizon": "short",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 104.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-19T01:{index:02d}:00+00:00",
                "metadata": {"lane": "short", "horizon": "short"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "lane": "short",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 104.0,
                "pnl_usdt": -0.5,
                "r_multiple": -1.0,
            }
        )
    for index in range(10):
        block = trader.create_block(
            {
                "symbol": f"WIN{index}USDT",
                "market": "spot",
                "side": "long",
                "horizon": "mid",
                "qty": 0.1,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 104.0,
                "stop_price": 98.0,
                "status": "closed",
                "closed_at": f"2026-06-19T02:{index:02d}:00+00:00",
                "metadata": {"lane": "mid", "horizon": "mid"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "lane": "mid",
                "entry_price": 100.0,
                "exit_price": 104.0,
                "stop_price": 98.0,
                "target_price": 104.0,
                "pnl_usdt": 1.0,
                "r_multiple": 1.0,
            }
        )

    rejection = trader._lane_performance_cooldown_rejection(  # noqa: SLF001
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "entry_style": "wait_for_price",
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
        }
    )

    assert rejection is not None
    cooldown = rejection["lane_performance_cooldown"]
    assert cooldown["matched_lane"] == "spot:long:short"
    assert cooldown["sample_count"] == 10
    assert cooldown["pnl_usdt"] == pytest.approx(-5.0)


def test_spot_horizon_performance_lane_is_market_side_qualified(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)

    saved = trader.repository.save_performance_reflection(
        {
            "block_id": "spot-short-horizon-loss",
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "lane": "short",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "stop_price": 98.0,
            "target_price": 104.0,
            "pnl_usdt": -0.5,
            "r_multiple": -1.0,
        }
    )

    assert saved["lane"] == "spot:long:short"
    performance = trader.repository.latest_performance_scorecard(limit=20)
    lane_keys = {row["lane"] for row in performance["lane_scorecards"]}
    assert "spot:long:short" in lane_keys
    assert "short" not in lane_keys


def test_repository_startup_repairs_legacy_performance_lanes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "binance_blocks.db"
    repo = BinanceBlockRepository(db_path)
    saved = repo.save_performance_reflection(
        {
            "block_id": "legacy-spot-short",
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "lane": "short",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "stop_price": 98.0,
            "target_price": 104.0,
            "pnl_usdt": -0.5,
            "r_multiple": -1.0,
        }
    )
    assert saved["lane"] == "spot:long:short"
    with repo._connect() as conn:
        conn.execute(
            "UPDATE block_performance_reflections SET lane = 'short' WHERE block_id = ?",
            ("legacy-spot-short",),
        )
        raw_lane = conn.execute(
            "SELECT lane FROM block_performance_reflections WHERE block_id = ?",
            ("legacy-spot-short",),
        ).fetchone()[0]
    assert raw_lane == "short"

    BinanceBlockRepository(db_path)

    with repo._connect() as conn:
        repaired_lane = conn.execute(
            "SELECT lane FROM block_performance_reflections WHERE block_id = ?",
            ("legacy-spot-short",),
        ).fetchone()[0]
    assert repaired_lane == "spot:long:short"


def test_repository_startup_repairs_legacy_performance_pattern_keys(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "binance_blocks.db"
    repo = BinanceBlockRepository(db_path)
    repo.create_block(
        {
            "block_id": "legacy-pattern-block",
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 96.0,
            "stop_price": 102.0,
            "liquidation_price": 135.0,
            "status": "closed",
            "metadata": {
                "calculated_price_plan": {
                    "pattern_inputs": {
                        "prior": {
                            "pattern_key": "ema_trend:short:15m",
                            "symbol": "ETHUSDT",
                        }
                    }
                }
            },
        }
    )
    repo.save_performance_reflection(
        {
            "block_id": "legacy-pattern-block",
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "lane": "futures",
            "pattern_key": "futures:short:rr_high",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "stop_price": 102.0,
            "target_price": 96.0,
            "pnl_usdt": 1.86,
            "r_multiple": 0.93,
        }
    )

    BinanceBlockRepository(db_path)

    with repo._connect() as conn:
        repaired_pattern = conn.execute(
            "SELECT pattern_key FROM block_performance_reflections WHERE block_id = ?",
            ("legacy-pattern-block",),
        ).fetchone()[0]
    assert repaired_pattern == "ema_trend:short:15m"


def test_manager_derives_qty_from_quote_budget_for_waiting_entry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "quote_budget_usdt": 49.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 98.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 98.0,
                    "target_price": 104.0,
                    "stop_price": 96.0,
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.7,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks()[0]
    assert block["qty_initial"] == pytest.approx(0.5)
    assert block["metadata"]["quote_budget_usdt"] == pytest.approx(49.0)


def test_manager_caps_create_budget_to_selected_candidate_budget(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(tmp_path, adapter=adapter, enabled=True)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 20,
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }
    trader._last_account_snapshot = _normalize_test_account(trader, adapter.account)
    trader._last_live_authority_context = trader.live_authority_provider()
    trader._last_manager_entry_gate_policy = trader._entry_gate_policy(
        performance={},
        memory_context={},
    )
    trader._runtime_market_universe = {"spot": [], "futures": ["BTCUSDT"], "upbit_spot": []}
    trader._last_manager_candidate_index = trader._manager_candidate_index(
        [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "stance": "short",
                "confidence": 0.88,
                "entry_price": 50_000.0,
                "target_price": 48_000.0,
                "stop_price": 51_000.0,
                "quote_budget_usdt": 35.0,
                "performance_budget_multiplier": 0.35,
                "calculated": {
                    "quote_budget_usdt": 35.0,
                    "performance_budget_multiplier": 0.35,
                    "sizing_inputs": {"performance_budget_multiplier": 0.35},
                },
            }
        ]
    )

    applied = asyncio.run(
        trader._apply_manager_actions(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "quote_budget_usdt": 200.0,
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000.0,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000.0,
                        "target_price": 48_000.0,
                        "stop_price": 51_000.0,
                        "margin_type": "isolated",
                        "leverage": 1,
                        "liquidation_price": 67_500.0,
                        "confidence": 0.88,
                        "thesis": "LLM asked too much size but candidate budget is smaller.",
                    }
                ],
                "update_blocks": [],
                "close_blocks": [],
                "pause_blocks": [],
            },
            manager_run_id=1,
        )
    )

    assert applied["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks(include_closed=False)[0]
    assert block["qty_initial"] == pytest.approx(35.0 / 50_000.0)
    assert block["metadata"]["quote_budget_usdt"] == pytest.approx(35.0)
    assert block["metadata"]["candidate_budget_cap"]["from_quote_budget_usdt"] == pytest.approx(200.0)
    assert block["metadata"]["candidate_budget_cap"]["to_quote_budget_usdt"] == pytest.approx(35.0)
    assert block["metadata"]["candidate_budget_cap"]["performance_budget_multiplier"] == pytest.approx(0.35)


def test_risk_sizer_cannot_exceed_candidate_budget_cap(
    tmp_path: Path,
) -> None:
    class OversizedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 20.0,
                "reward_risk": 2.0,
                "proposed_qty_seen": kwargs.get("proposed_qty"),
            }

    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(tmp_path, adapter=adapter, enabled=True)
    trader.risk_sizer = OversizedRiskSizer()
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 20,
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }
    trader._last_account_snapshot = _normalize_test_account(trader, adapter.account)
    trader._last_live_authority_context = trader.live_authority_provider()
    trader._last_manager_entry_gate_policy = trader._entry_gate_policy(
        performance={},
        memory_context={},
    )
    trader._runtime_market_universe = {"spot": [], "futures": ["BTCUSDT"], "upbit_spot": []}
    trader._last_manager_candidate_index = trader._manager_candidate_index(
        [
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "stance": "short",
                "confidence": 0.88,
                "entry_price": 50_000.0,
                "target_price": 48_000.0,
                "stop_price": 51_000.0,
                "quote_budget_usdt": 35.0,
                "performance_budget_multiplier": 0.35,
                "calculated": {
                    "quote_budget_usdt": 35.0,
                    "performance_budget_multiplier": 0.35,
                    "sizing_inputs": {"performance_budget_multiplier": 0.35},
                },
            }
        ]
    )

    applied = asyncio.run(
        trader._apply_manager_actions(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "quote_budget_usdt": 200.0,
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000.0,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000.0,
                        "target_price": 48_000.0,
                        "stop_price": 51_000.0,
                        "margin_type": "isolated",
                        "leverage": 1,
                        "liquidation_price": 67_500.0,
                        "confidence": 0.88,
                        "thesis": "Risk sizer must not expand above candidate budget.",
                    }
                ],
                "update_blocks": [],
                "close_blocks": [],
                "pause_blocks": [],
            },
            manager_run_id=1,
        )
    )

    assert applied["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks(include_closed=False)[0]
    risk_sizing = block["metadata"]["risk_sizing"]
    assert block["qty_initial"] == pytest.approx(35.0 / 50_000.0)
    assert risk_sizing["qty"] == pytest.approx(35.0 / 50_000.0)
    assert risk_sizing["candidate_budget_qty_cap"]["from_qty"] == pytest.approx(0.01)
    assert risk_sizing["candidate_budget_qty_cap"]["to_qty"] == pytest.approx(35.0 / 50_000.0)


def test_risk_sizer_reduces_budget_for_weak_pattern_performance(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(tmp_path, adapter=adapter, enabled=True)
    trader.config.lane_performance_min_samples = 3
    trader.config.lane_performance_loss_multiplier = 0.5
    trader.risk_sizer = FixedRiskSizer()
    trader._last_account_snapshot = _normalize_test_account(trader, adapter.account)
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"weak-pattern-{index}",
                "symbol": "SPOTLOSSUSDT",
                "market": "spot",
                "side": "long",
                "lane": "spot:long:short",
                "pattern_key": "bollinger_squeeze:short:5m",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 104.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
            }
        )

    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 9.0,
            "entry_price": 50_000.0,
            "target_price": 48_000.0,
            "stop_price": 51_000.0,
            "liquidation_price": 67_500.0,
            "margin_type": "isolated",
            "leverage": 1,
            "created_by": "llm",
            "status": "proposed",
            "metadata": {
                "calculated_price_plan": {
                    "pattern_inputs": {
                        "prior": {
                            "pattern_key": "bollinger_squeeze:short:5m",
                            "symbol": "BTCUSDT",
                        }
                    }
                }
            },
        }
    )

    risk_sizing = block["metadata"]["risk_sizing"]
    assert calls[0]["account_equity_usdt"] == pytest.approx(5_000.0)
    assert risk_sizing["pattern_performance_multiplier"] == pytest.approx(0.5)
    assert risk_sizing["pattern_performance_scorecard"]["pattern_key"] == (
        "bollinger_squeeze:short:5m"
    )


def test_pattern_performance_context_uses_pattern_specific_lookup_when_top_cards_truncated(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.lane_performance_min_samples = 3
    trader.config.lane_performance_loss_multiplier = 0.5

    for pattern_index in range(9):
        for sample_index in range(6):
            trader.repository.save_performance_reflection(
                {
                    "block_id": f"dominant-pattern-{pattern_index}-{sample_index}",
                    "symbol": "NOISEUSDT",
                    "market": "futures",
                    "side": "long",
                    "lane": "futures:long:futures",
                    "pattern_key": f"dominant_pattern_{pattern_index}:long:5m",
                    "entry_price": 100.0,
                    "exit_price": 101.0,
                    "stop_price": 98.0,
                    "target_price": 104.0,
                    "pnl_usdt": 1.0,
                    "r_multiple": 0.5,
                }
            )

    for sample_index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"rare-bad-pattern-{sample_index}",
                "symbol": "RAREUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures:short:futures",
                "pattern_key": "rare_bad:short:5m",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "stop_price": 104.0,
                "target_price": 96.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
            }
        )

    latest = trader.repository.latest_performance_scorecard(limit=120)
    assert all(
        row["pattern_key"] != "rare_bad:short:5m"
        for row in latest["pattern_scorecards"]
    )

    multiplier, scorecard = trader._pattern_performance_risk_context(
        "rare_bad:short:5m"
    )

    assert multiplier == pytest.approx(0.5)
    assert scorecard["pattern_key"] == "rare_bad:short:5m"
    assert scorecard["sample_count"] == 3
    assert scorecard["status"] == "de_risk"


def test_manager_rejects_invalid_long_waiting_entry_price_geometry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.82,
                    "quote_budget_usdt": 49.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 98.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 98.0,
                    "target_price": 104.0,
                    "stop_price": 99.0,
                    "thesis": "invalid long geometry must not reach the block ledger",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "invalid price direction for long block"
    assert trader.list_blocks(include_closed=True) == []


def test_manager_rejects_invalid_short_waiting_entry_price_geometry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "confidence": 0.82,
                    "quote_budget_usdt": 49.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 101.0,
                    "entry_trigger_operator": ">=",
                    "entry_price": 101.0,
                    "target_price": 96.0,
                    "stop_price": 100.0,
                    "margin_type": "isolated",
                    "leverage": 1,
                    "liquidation_price": 140.0,
                    "thesis": "invalid short geometry must not reach the block ledger",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "invalid price direction for short block"
    assert trader.list_blocks(include_closed=True) == []


def test_performance_scorecard_excludes_invalid_price_geometry(
    tmp_path: Path,
) -> None:
    repository = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    repository.save_performance_reflection(
        {
            "block_id": "valid-long-loss",
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "target_price": 104.0,
            "stop_price": 97.0,
            "pnl_usdt": -0.2,
            "net_pnl_usdt": -0.2,
            "r_multiple": -0.67,
        }
    )
    repository.save_performance_reflection(
        {
            "block_id": "invalid-long-win",
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "target_price": 104.0,
            "stop_price": 101.0,
            "pnl_usdt": 10.0,
            "net_pnl_usdt": 10.0,
            "r_multiple": 2.0,
        }
    )

    scorecard = repository.latest_performance_scorecard(limit=10)

    assert scorecard["sample_count"] == 1
    assert scorecard["realized_pnl_usdt"] == pytest.approx(-0.2)
    assert scorecard["window"]["excluded_execution_defect_count"] == 1
    assert scorecard["window"]["excluded_execution_defect_pnl_usdt"] == pytest.approx(10.0)


def test_performance_scorecard_keeps_profit_locked_trade_with_moved_stop(
    tmp_path: Path,
) -> None:
    repository = BinanceBlockRepository(tmp_path / "binance_blocks.db")
    repository.save_performance_reflection(
        {
            "block_id": "profit-locked-long-win",
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "target_price": 104.0,
            "stop_price": 101.0,
            "pnl_usdt": 3.0,
            "net_pnl_usdt": 3.0,
            "r_multiple": 1.0,
            "lesson": {
                "risk_stop_price": 97.0,
                "final_stop_price": 101.0,
                "result": "positive",
            },
        }
    )

    scorecard = repository.latest_performance_scorecard(limit=10)

    assert scorecard["sample_count"] == 1
    assert scorecard["realized_pnl_usdt"] == pytest.approx(3.0)
    assert scorecard["window"]["excluded_execution_defect_count"] == 0
    assert scorecard["window"]["excluded_execution_defect_pnl_usdt"] == pytest.approx(0.0)


def test_binance_candidate_quote_budget_uses_lane_specific_percentages(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.futures_quote_budget_pct = 10.0
    trader.config.futures_min_quote_budget_usdt = 25.0
    trader.config.futures_max_quote_budget_usdt = 150.0
    trader.config.spot_quote_budget_pct = 5.0
    trader.config.spot_min_quote_budget_usdt = 50.0
    trader.config.spot_max_quote_budget_usdt = 300.0

    account = {
        "futures_cash_usdt": 502.0,
        "spot_cash_usdt": 4140.0,
    }

    assert trader._candidate_quote_budget_usdt(market="futures", account=account) == pytest.approx(50.2)
    assert trader._candidate_quote_budget_usdt(market="spot", account=account) == pytest.approx(207.0)


def test_binance_candidate_quote_budget_scales_after_positive_performance(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.futures_quote_budget_pct = 10.0
    trader.config.futures_min_quote_budget_usdt = 25.0
    trader.config.futures_max_quote_budget_usdt = 150.0
    trader.config.budget_performance_scale_enabled = True
    trader.config.budget_performance_scale_min_samples = 3
    trader.config.budget_performance_scale_win_rate_pct = 60.0
    trader.config.budget_performance_scale_multiplier = 1.5

    for index, pnl in enumerate([0.2, 0.1, -0.05], start=1):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"perf-{index}",
                "symbol": "BNBUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "stop_price": 101.0,
                "target_price": 98.0,
                "pnl_usdt": pnl,
                "r_multiple": 1.0 if pnl > 0 else -1.0,
            }
        )

    budget = trader._candidate_quote_budget_usdt(
        market="futures",
        account={"futures_cash_usdt": 500.0, "spot_cash_usdt": 0.0},
    )
    spot_budget = trader._candidate_quote_budget_usdt(
        market="spot",
        account={"futures_cash_usdt": 500.0, "spot_cash_usdt": 1000.0},
    )

    assert budget == pytest.approx(75.0)
    assert spot_budget == pytest.approx(50.0)


def test_binance_candidate_quote_budget_scales_only_matching_futures_side(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.futures_quote_budget_pct = 10.0
    trader.config.futures_min_quote_budget_usdt = 25.0
    trader.config.futures_max_quote_budget_usdt = 150.0
    trader.config.budget_performance_scale_enabled = True
    trader.config.budget_performance_scale_min_samples = 3
    trader.config.budget_performance_scale_win_rate_pct = 60.0
    trader.config.budget_performance_scale_multiplier = 1.5

    for index in range(1, 4):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"long-win-{index}",
                "symbol": "BNBUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 104.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": 0.5,
                "r_multiple": 1.0,
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": f"short-loss-{index}",
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "stop_price": 102.0,
                "target_price": 96.0,
                "pnl_usdt": -0.2,
                "r_multiple": -1.0,
            }
        )

    account = {"futures_cash_usdt": 500.0, "spot_cash_usdt": 0.0}

    assert trader._candidate_budget_performance_multiplier(
        market="futures",
        side="long",
    ) == pytest.approx(1.5)
    assert trader._candidate_budget_performance_multiplier(
        market="futures",
        side="short",
    ) == pytest.approx(0.5)
    assert trader._candidate_quote_budget_usdt(
        market="futures",
        side="long",
        account=account,
    ) == pytest.approx(75.0)
    assert trader._candidate_quote_budget_usdt(
        market="futures",
        side="short",
        account=account,
    ) == pytest.approx(25.0)


def test_binance_candidate_quote_budget_shrinks_after_negative_lane_performance(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.futures_quote_budget_pct = 10.0
    trader.config.futures_min_quote_budget_usdt = 25.0
    trader.config.futures_max_quote_budget_usdt = 150.0
    trader.config.budget_performance_scale_enabled = True
    trader.config.budget_performance_scale_min_samples = 10
    trader.config.lane_performance_min_samples = 3
    trader.config.lane_performance_loss_multiplier = 0.4
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"short-costed-loss-{index}",
                "symbol": f"LOSS{index}USDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "stop_price": 102.0,
                "target_price": 96.0,
                "pnl_usdt": -0.4,
                "gross_pnl_usdt": -0.2,
                "net_pnl_usdt": -0.4,
                "slippage_usdt": 0.2,
                "cost_source": "estimated_from_notional",
                "r_multiple": -1.0,
            }
        )

    assert trader._candidate_budget_performance_multiplier(
        market="futures",
        side="short",
    ) == pytest.approx(0.4)
    assert trader._candidate_quote_budget_usdt(
        market="futures",
        side="short",
        account={"futures_cash_usdt": 500.0, "spot_cash_usdt": 0.0},
    ) == pytest.approx(20.0)


def test_binance_candidate_quote_budget_requires_drawdown_recovery_before_scale(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.futures_quote_budget_pct = 10.0
    trader.config.futures_min_quote_budget_usdt = 25.0
    trader.config.futures_max_quote_budget_usdt = 150.0
    trader.config.budget_performance_scale_enabled = True
    trader.config.budget_performance_scale_min_samples = 3
    trader.config.budget_performance_scale_win_rate_pct = 60.0
    trader.config.budget_performance_scale_multiplier = 1.5
    trader.repository.save_performance_reflection(
        {
            "block_id": "long-deep-loss",
            "symbol": "BNBUSDT",
            "market": "futures",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 90.0,
            "stop_price": 98.0,
            "target_price": 106.0,
            "pnl_usdt": -10.0,
            "r_multiple": -5.0,
        }
    )
    for index in range(1, 7):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"long-small-recovery-{index}",
                "symbol": "BNBUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": 2.0,
                "r_multiple": 1.0,
            }
        )

    scorecard = trader.repository.latest_performance_scorecard(limit=20)
    side_card = next(
        row for row in scorecard["side_scorecards"] if row["side"] == "futures:long"
    )

    assert side_card["win_rate_pct"] > 60.0
    assert side_card["pnl_usdt"] > 0.0
    assert side_card["profit_factor"] == pytest.approx(1.2)
    assert side_card["max_drawdown_r_multiple"] <= -2.0
    assert side_card["recovery_factor"] < 1.0
    assert trader._candidate_budget_performance_multiplier(
        market="futures",
        side="long",
    ) == pytest.approx(0.5)
    assert trader._candidate_quote_budget_usdt(
        market="futures",
        side="long",
        account={"futures_cash_usdt": 500.0},
    ) == pytest.approx(25.0)


def test_binance_candidate_budget_does_not_scale_on_recent_only_recovery(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.futures_quote_budget_pct = 10.0
    trader.config.futures_min_quote_budget_usdt = 25.0
    trader.config.futures_max_quote_budget_usdt = 150.0
    trader.config.budget_performance_scale_enabled = True
    trader.config.budget_performance_scale_min_samples = 3
    trader.config.budget_performance_scale_win_rate_pct = 55.0
    trader.config.budget_performance_scale_multiplier = 1.5
    trader.config.performance_scorecard_feedback_limit = 120
    for index in range(8):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"old-short-damage-{index}",
                "symbol": "OLDUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "stop_price": 105.0,
                "target_price": 90.0,
                "pnl_usdt": -10.0,
                "r_multiple": -2.0,
                "created_at": f"2026-01-01T00:{index:02d}:00+00:00",
            }
        )
    for index in range(20):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"recent-short-recovery-{index}",
                "symbol": "RECENTUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures",
                "entry_price": 100.0,
                "exit_price": 95.0,
                "stop_price": 105.0,
                "target_price": 90.0,
                "pnl_usdt": 1.0,
                "r_multiple": 1.0,
                "created_at": f"2026-02-01T00:{index:02d}:00+00:00",
            }
        )

    recent = trader.repository.latest_performance_scorecard(limit=20)
    long_window = trader.repository.latest_performance_scorecard(limit=120)
    recent_short = next(
        row for row in recent["side_scorecards"] if row["side"] == "futures:short"
    )
    long_short = next(
        row for row in long_window["side_scorecards"] if row["side"] == "futures:short"
    )

    assert recent_short["pnl_usdt"] > 0.0
    assert recent_short["profit_factor"] > 1.2
    assert long_short["pnl_usdt"] < 0.0
    assert trader._candidate_budget_performance_multiplier(
        market="futures",
        side="short",
    ) == pytest.approx(1.0)
    assert trader._candidate_quote_budget_usdt(
        market="futures",
        side="short",
        account={"futures_cash_usdt": 500.0},
    ) == pytest.approx(50.0)


def test_binance_candidate_quote_budget_prefers_spot_horizon_lane_over_broad_side(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.spot_quote_budget_pct = 10.0
    trader.config.spot_min_quote_budget_usdt = 25.0
    trader.config.spot_max_quote_budget_usdt = 150.0
    trader.config.budget_performance_scale_enabled = True
    trader.config.budget_performance_scale_min_samples = 3
    trader.config.budget_performance_scale_win_rate_pct = 60.0
    trader.config.budget_performance_scale_multiplier = 1.5
    for index in range(1, 4):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"spot-mid-win-{index}",
                "symbol": "ETHUSDT",
                "market": "spot",
                "side": "long",
                "lane": "mid",
                "entry_price": 100.0,
                "exit_price": 104.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": 1.0,
                "r_multiple": 1.0,
            }
        )

    account = {"spot_cash_usdt": 500.0}

    assert trader._candidate_budget_performance_multiplier(
        market="spot",
        side="long",
        lane="mid",
    ) == pytest.approx(1.5)
    assert trader._candidate_budget_performance_multiplier(
        market="spot",
        side="long",
        lane="short",
    ) == pytest.approx(1.0)
    assert trader._candidate_quote_budget_usdt(
        market="spot",
        side="long",
        lane="mid",
        account=account,
    ) == pytest.approx(75.0)
    assert trader._candidate_quote_budget_usdt(
        market="spot",
        side="long",
        lane="short",
        account=account,
    ) == pytest.approx(50.0)


def test_manager_waiting_entry_uses_output_confidence_when_candidate_is_conservative(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "confidence": 0.64,
                    "quote_budget_usdt": 49.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 98.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 98.0,
                    "target_price": 104.0,
                    "stop_price": 96.0,
                    "evidence_refs": ["ev_directional_quant"],
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.45,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    block = trader.list_blocks()[0]
    candidate_gate = block["metadata"]["entry_gate"]["candidate"]
    assert candidate_gate["confidence"] == pytest.approx(0.64)
    assert candidate_gate["confidence_source"] == "manager_output"


def test_manager_prompt_includes_crypto_pattern_context(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})

    class PatternProvider:
        def context_pack(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 12,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "scorecards": [
                    {
                        "symbol": "BTCUSDT",
                        "family": "ema_trend",
                        "direction": "long",
                        "expectancy_r": 0.22,
                        "trade_count": 18,
                        "score": 72.0,
                    }
                ],
                "optimized_strategy_sets": [
                    {
                        "symbol": "BTCUSDT",
                        "pattern_key": "ema_trend:long:5m",
                        "objective_score": 68.5,
                        "parameter_set": {
                            "stop_pct": 0.008,
                            "target_pct": 0.012,
                            "holding_bars": 4,
                        },
                    }
                ],
                "symbols": symbols or [],
                "limit": limit,
            }

    trader = _trader(tmp_path, llm=llm)
    trader.crypto_pattern_provider = PatternProvider()

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert "crypto_patterns" not in prompt
    assert "decision_packet" in prompt["decision_inputs"]
    assert "crypto_patterns" not in prompt["decision_inputs"]
    assert prompt["raw_context_refs"]["crypto_patterns"]["scorecard_count"] == 1
    assert (
        prompt["raw_context_refs"]["crypto_patterns"]["optimized_strategy_set_count"]
        == 1
    )


def test_spot_wallet_adoption_creates_block_for_unassigned_holding(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.prices["BTCUSDT"] = 100_000.0
    adapter.prices["FIOUSDT"] = 0.00094
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {"asset": "USDT", "kind": "cash", "qty": 100.0, "available": 100.0},
            {
                "asset": "BTC",
                "kind": "position",
                "qty": 0.25,
                "available": 0.25,
                "locked": 0.0,
                "avg_price": 126_000_000.0,
                "mark_price": 140_000_000.0,
            },
            {
                "asset": "FIO",
                "kind": "position",
                "qty": 0.882,
                "available": 0.882,
                "locked": 0.0,
            },
        ],
    }
    trader = _trader(tmp_path, adapter=adapter)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 90_000.0,
            "target_price": 99_000.0,
            "stop_price": 85_000.0,
            "status": "open",
            "created_by": "llm",
        }
    )

    result = asyncio.run(trader.run_spot_adoption_once())
    second = asyncio.run(trader.run_spot_adoption_once())
    blocks = trader.list_blocks(include_closed=False)
    adopted = [row for row in blocks if row["created_by"] == "wallet_adoption"]

    assert result["status"] == "ok"
    assert result["adopted_count"] == 1
    assert second["adopted_count"] == 0
    assert len(adopted) == 1
    assert adopted[0]["symbol"] == "BTCUSDT"
    assert adopted[0]["status"] == "open"
    assert adopted[0]["qty_open"] == pytest.approx(0.15)
    assert adopted[0]["qty_initial"] == pytest.approx(0.15)
    assert adopted[0]["entry_price"] == pytest.approx(90_000.0)
    assert adopted[0]["target_price"] == pytest.approx(108_000.0)
    assert adopted[0]["stop_price"] == pytest.approx(95_000.0)
    assert any(
        row["symbol"] == "FIOUSDT" and row["reason"] == "dust_below_min_notional"
        for row in result["skipped"]
    )


def test_manager_run_adopts_existing_spot_wallet_position_without_entry_order(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.prices["BTCUSDT"] = 100_000.0
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {"asset": "USDT", "kind": "cash", "qty": 100.0, "available": 100.0},
            {
                "asset": "BTC",
                "kind": "position",
                "qty": 0.25,
                "available": 0.25,
                "locked": 0.0,
                "avg_price": 92_000.0,
                "mark_price": 100_000.0,
            },
        ],
    }
    llm = _FakeLLM(
        {
            "adopt_existing_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "mid",
                    "qty": 0.15,
                    "entry_price": 92_000.0,
                    "target_price": 112_000.0,
                    "stop_price": 88_000.0,
                    "thesis": "기존 BTC 현물 보유분을 중기 블록으로 쥬가 관리한다.",
                    "risk_note": "신규 진입 주문 없이 기존 지갑 수량만 배정한다.",
                    "adoption_note": "기존 현물 지갑 보유분을 신규 주문 없이 블록 원장에 흡수",
                    "confidence": 0.71,
                }
            ],
            "create_blocks": [],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_spot=True)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 90_000.0,
            "target_price": 99_000.0,
            "stop_price": 85_000.0,
            "status": "open",
            "created_by": "llm",
        }
    )

    result = asyncio.run(trader.run_manager_once())
    blocks = trader.list_blocks(include_closed=False)
    adopted = [row for row in blocks if row["created_by"] == "existing_position"]

    assert result["status"] == "ok"
    assert result["actions"]["adopt_existing_blocks"][0]["qty"] == pytest.approx(0.15)
    assert result["applied"]["adopted"][0]["status"] == "adopted"
    assert adapter.spot_orders == []
    assert len(adopted) == 1
    assert adopted[0]["symbol"] == "BTCUSDT"
    assert adopted[0]["status"] == "open"
    assert adopted[0]["qty_open"] == pytest.approx(0.15)
    assert adopted[0]["target_price"] == pytest.approx(112_000.0)
    assert adopted[0]["stop_price"] == pytest.approx(88_000.0)
    assert adopted[0]["metadata"]["adoption"]["source"] == "manager_existing_position"


def test_manager_run_adopts_existing_futures_short_position_without_entry_order(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.prices["BTCUSDT"] = 88.0
    adapter.account = {
        "status": "ok",
        "futures_cash_usdt": 1_000.0,
        "futures_position_risk": [
            {
                "symbol": "BTCUSDT",
                "position_amt": -2.0,
                "entry_price": 90.0,
                "mark_price": 88.0,
                "liquidation_price": 126.0,
                "leverage": 2,
                "margin_type": "isolated",
                "unrealized_profit": 4.0,
            }
        ],
    }
    llm = _FakeLLM(
        {
            "adopt_existing_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 1.5,
                    "entry_price": 90.0,
                    "target_price": 84.0,
                    "stop_price": 93.0,
                    "liquidation_price": 126.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "thesis": "기존 BTCUSDT 숏 포지션을 선물 블록으로 쥬가 관리한다.",
                    "risk_note": "신규 선물 진입 주문 없이 기존 포지션 수량만 배정한다.",
                    "adoption_note": "기존 선물 숏 포지션을 신규 주문 없이 블록 원장에 흡수",
                    "confidence": 0.68,
                }
            ],
            "create_blocks": [],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_futures=True)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 90.0,
            "target_price": 84.0,
            "stop_price": 93.0,
            "liquidation_price": 126.0,
            "margin_type": "isolated",
            "leverage": 2,
            "status": "open",
            "created_by": "llm",
        }
    )

    result = asyncio.run(trader.run_manager_once())
    blocks = trader.list_blocks(include_closed=False)
    adopted = [row for row in blocks if row["created_by"] == "existing_position"]

    assert result["status"] == "ok"
    assert result["actions"]["adopt_existing_blocks"][0]["qty"] == pytest.approx(1.5)
    assert result["applied"]["adopted"][0]["status"] == "adopted"
    assert adapter.futures_orders == []
    assert len(adopted) == 1
    assert adopted[0]["symbol"] == "BTCUSDT"
    assert adopted[0]["market"] == "futures"
    assert adopted[0]["side"] == "short"
    assert adopted[0]["status"] == "open"
    assert adopted[0]["qty_open"] == pytest.approx(1.5)
    assert adopted[0]["target_price"] == pytest.approx(84.0)
    assert adopted[0]["stop_price"] == pytest.approx(93.0)
    assert adopted[0]["liquidation_price"] == pytest.approx(126.0)
    assert adopted[0]["metadata"]["adoption"]["source"] == "manager_existing_position"
    assert adopted[0]["metadata"]["adoption"]["position_amt"] == pytest.approx(-2.0)


def test_manager_prompt_uses_crypto_quant_context_limit(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    calls: list[dict[str, Any]] = []

    class QuantProvider:
        def latest_signals(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 16,
        ) -> list[dict[str, Any]]:
            calls.append({"symbols": symbols, "limit": limit})
            return []

        def retrieval_context(
            self,
            *,
            symbols: list[str],
            horizon: str = "intraday",
            points_per_symbol: int = 12,
        ) -> dict[str, Any]:
            return {"status": "ok", "items": []}

    trader = _trader(
        tmp_path,
        llm=llm,
        quant_provider=QuantProvider(),
        quant_context_limit=5,
    )

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BNBUSDT"}]))

    assert calls[0]["limit"] == 5


def test_target_executor_creates_paper_exit_without_llm_or_live_order(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["action_count"] == 1
    assert result["actions"][0]["reason"] == "target_reached"
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == 0.0
    assert adapter.spot_orders == []
    assert llm.calls == []
    orders = trader.repository.list_orders(block_id=block["block_id"])
    assert orders[0]["status"] == "paper"
    assert orders[0]["side"] == "sell"


def test_executor_locks_profit_after_favorable_r_without_llm(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 112.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "profit_locked"
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["stop_price"] == pytest.approx(102.5)
    assert adapter.spot_orders == []
    assert llm.calls == []


def test_executor_profit_lock_respects_estimated_round_trip_cost_floor(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 101.0,
            "stop_price": 99.6,
            "status": "open",
            "metadata": {
                "cost_edge_gate": {
                    "estimated_round_trip_cost_pct": 0.25,
                },
            },
        }
    )
    adapter.prices["BTCUSDT"] = 100.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "profit_locked"
    assert updated is not None
    assert updated["stop_price"] == pytest.approx(100.37)
    assert updated["metadata"]["profit_lock_cost_floor_pct"] == pytest.approx(0.37)


def test_executor_skips_profit_lock_when_cost_floor_is_not_executable(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 101.0,
            "stop_price": 99.8,
            "status": "open",
            "metadata": {
                "cost_edge_gate": {
                    "estimated_round_trip_cost_pct": 0.25,
                },
            },
        }
    )
    adapter.prices["BTCUSDT"] = 100.25

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["action_count"] == 0
    assert updated is not None
    assert updated["stop_price"] == pytest.approx(99.8)


def test_executor_does_not_lock_profit_before_default_trigger_r(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["action_count"] == 0
    assert updated is not None
    assert updated["stop_price"] == pytest.approx(90.0)
    assert adapter.spot_orders == []
    assert llm.calls == []


def test_executor_uses_partial_profit_when_recent_mfe_is_surrendered(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    for index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"loss-{index}",
                "symbol": "LOSSUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": -0.10,
                "r_multiple": -0.20,
                "mfe_r_multiple": 1.10,
                "mae_r_multiple": -0.50,
            }
        )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["trigger_r"] == pytest.approx(0.8)
    assert result["actions"][0]["trigger_source"] == "mfe_surrender_repair"
    assert updated is not None
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["stop_price"] > 100.0
    assert updated["metadata"]["partial_profit_trigger_source"] == "mfe_surrender_repair"
    assert updated["metadata"]["partial_profit_repair_context"]["enabled"] is True


def test_executor_uses_partial_profit_for_entry_quality_mfe_surrender(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    trader._runtime_market_universe = {
        "spot": [
            "BTCUSDT",
            *[f"PULL{index}USDT" for index in range(3)],
            *[f"EDGE{index}USDT" for index in range(5)],
        ],
        "futures": [],
        "upbit_spot": [],
    }
    for index in range(3):
        closed = trader.create_block(
            {
                "symbol": f"PULL{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.5,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "status": "closed",
                "closed_at": f"2026-06-19T02:{index:02d}:00+00:00",
                "metadata": {"entry_quality": "wait_pullback"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": closed["block_id"],
                "symbol": closed["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": -0.10,
                "r_multiple": -0.20,
                "mfe_r_multiple": 1.10,
                "mae_r_multiple": -0.30,
            }
        )
    for index in range(5):
        closed = trader.create_block(
            {
                "symbol": f"EDGE{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 0.5,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "status": "closed",
                "closed_at": f"2026-06-19T03:{index:02d}:00+00:00",
                "metadata": {"entry_quality": "actionable_now"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": closed["block_id"],
                "symbol": closed["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": 0.50,
                "r_multiple": 1.0,
                "mfe_r_multiple": 1.10,
                "mae_r_multiple": -0.10,
            }
        )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "liquidation_price": 40.0,
            "status": "open",
            "metadata": {"entry_quality": "wait_pullback"},
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["trigger_r"] == pytest.approx(0.8)
    assert result["actions"][0]["trigger_source"] == "entry_quality_mfe_surrender_repair"
    assert updated is not None
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["stop_price"] > 100.0
    assert (
        updated["metadata"]["partial_profit_trigger_source"]
        == "entry_quality_mfe_surrender_repair"
    )
    assert (
        updated["metadata"]["partial_profit_repair_context"]["entry_quality_lane"]
        == "spot:long:wait_pullback"
    )


def test_executor_takes_larger_partial_profit_for_distressed_entry_quality(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    trader._runtime_market_universe = {
        "spot": [
            "BTCUSDT",
            *[f"PULL{index}USDT" for index in range(5)],
            *[f"EDGE{index}USDT" for index in range(8)],
        ],
        "futures": [],
        "upbit_spot": [],
    }
    for index in range(5):
        closed = trader.create_block(
            {
                "symbol": f"PULL{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 1.0,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "status": "closed",
                "closed_at": f"2026-06-19T02:{index:02d}:00+00:00",
                "metadata": {"entry_quality": "wait_pullback"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": closed["block_id"],
                "symbol": closed["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": -0.20,
                "r_multiple": -0.20,
                "mfe_r_multiple": 1.10,
                "mae_r_multiple": -0.30,
            }
        )
    for index in range(8):
        closed = trader.create_block(
            {
                "symbol": f"EDGE{index}USDT",
                "market": "spot",
                "side": "long",
                "qty": 1.0,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "status": "closed",
                "closed_at": f"2026-06-19T03:{index:02d}:00+00:00",
                "metadata": {"entry_quality": "actionable_now"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": closed["block_id"],
                "symbol": closed["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": 0.50,
                "r_multiple": 1.0,
                "mfe_r_multiple": 1.10,
                "mae_r_multiple": -0.10,
            }
        )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 1.0,
            "qty_open": 1.0,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
            "metadata": {"entry_quality": "wait_pullback"},
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["qty"] == pytest.approx(0.75)
    assert updated is not None
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["metadata"]["partial_profit_fraction"] == pytest.approx(0.75)
    assert updated["metadata"]["partial_profit_fraction_source"] == "distressed_entry_quality"
    assert (
        updated["metadata"]["partial_profit_fraction_context"]["entry_quality_lane"]
        == "spot:long:wait_pullback"
    )


def test_executor_tightens_loss_stop_for_underperforming_entry_quality(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    trader._runtime_market_universe = {
        "spot": [],
        "futures": [
            "BIOUSDT",
            *[f"PULL{index}USDT" for index in range(3)],
        ],
        "upbit_spot": [],
    }
    for index in range(3):
        closed = trader.create_block(
            {
                "symbol": f"PULL{index}USDT",
                "market": "futures",
                "side": "long",
                "qty": 1.0,
                "qty_open": 0.0,
                "entry_price": 100.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "status": "closed",
                "closed_at": f"2026-06-19T02:{index:02d}:00+00:00",
                "metadata": {"entry_quality": "wait_pullback"},
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": closed["block_id"],
                "symbol": closed["symbol"],
                "market": "futures",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 89.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.1,
                "mfe_r_multiple": 0.05,
                "mae_r_multiple": -1.1,
            }
        )
    block = trader.create_block(
        {
            "symbol": "BIOUSDT",
            "market": "futures",
            "side": "long",
            "qty": 10.0,
            "qty_open": 10.0,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "liquidation_price": 40.0,
            "status": "open",
            "metadata": {"entry_quality": "wait_pullback"},
        }
    )
    adapter.prices["BIOUSDT"] = 95.0

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "entry_quality_loss_tightened"
    assert result["actions"][0]["unfavorable_r"] == pytest.approx(0.5)
    assert result["actions"][0]["entry_quality_lane"] == "futures:long:wait_pullback"
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["stop_price"] == pytest.approx(95.0)
    assert updated["metadata"]["entry_quality_loss_tighten"]["trigger_r"] == pytest.approx(
        0.5
    )
    assert updated["metadata"]["entry_quality_loss_tighten"]["sample_count"] == 3


def test_executor_locks_profit_earlier_for_weak_performance_lane(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
            "metadata": {
                "lane": "short",
                "partial_profit_taken_at": "2026-06-01T00:00:00+00:00",
                "lane_authority": {
                    "weak_lanes": ["spot:long"],
                },
            },
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "profit_locked"
    assert result["actions"][0]["trigger_r"] == pytest.approx(0.8)
    assert updated is not None
    assert updated["stop_price"] > 100.0
    assert updated["metadata"]["profit_lock_trigger_source"] == "weak_performance_lane"


def test_executor_partially_takes_profit_for_weak_performance_lane_without_llm(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
            "metadata": {
                "lane": "short",
                "lane_authority": {
                    "weak_lanes": ["spot:long"],
                },
            },
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    orders = trader.repository.list_orders(block_id=block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["reason"] == "partial_profit_reached"
    assert result["actions"][0]["qty"] == pytest.approx(0.25)
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["stop_price"] > 100.0
    assert updated["metadata"]["partial_profit_trigger_source"] == "weak_performance_lane"
    assert orders[0]["status"] == "paper"
    assert orders[0]["reason"] == "partial_profit_reached"
    assert adapter.spot_orders == []
    assert llm.calls == []


def test_live_spot_partial_profit_closes_full_when_partial_is_below_min_notional(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.06,
                "available": 0.06,
                "locked": 0.0,
            }
        ],
        "spot_cash_usdt": 0.0,
        "futures_cash_usdt": 0.0,
    }
    trader = _trader(
        tmp_path,
        adapter=adapter,
        enabled=True,
        execute_spot=True,
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.06,
            "qty_open": 0.06,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
            "metadata": {
                "lane": "short",
                "lane_authority": {
                    "weak_lanes": ["spot:long"],
                },
            },
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    orders = trader.repository.list_orders(block_id=block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["qty"] == pytest.approx(0.06)
    assert result["actions"][0]["remaining_qty"] == pytest.approx(0.0)
    assert adapter.spot_orders[0]["quantity"] == pytest.approx(0.06)
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == pytest.approx(0.0)
    assert updated["metadata"]["partial_profit_exit_mode"] == "full_exit_min_notional"
    assert orders[0]["status"] == "sent"


def test_live_futures_partial_profit_closes_full_when_partial_is_below_min_notional(
    tmp_path: Path,
) -> None:
    adapter = _FilteredFillingEntryBinance()
    adapter.prices["LINKUSDT"] = 7.866
    trader = _trader(
        tmp_path,
        adapter=adapter,
        enabled=True,
        execute_futures=True,
    )
    trader._runtime_market_universe = {"spot": [], "futures": ["LINKUSDT"], "upbit_spot": []}
    block = trader.create_block(
        {
            "symbol": "LINKUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.53,
            "qty_open": 2.53,
            "entry_price": 7.9241,
            "target_price": 7.7974,
            "stop_price": 7.9875,
            "status": "open",
            "leverage": 1,
            "margin_type": "isolated",
            "liquidation_price": 10.698,
            "metadata": {
                "lane": "futures",
                "lane_authority": {
                    "weak_lanes": ["futures:short"],
                },
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    orders = trader.repository.list_orders(block_id=block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["qty"] == pytest.approx(2.53)
    assert result["actions"][0]["remaining_qty"] == pytest.approx(0.0)
    assert adapter.futures_orders[0]["quantity"] == pytest.approx(2.53)
    assert adapter.futures_orders[0]["reduce_only"] is True
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == pytest.approx(0.0)
    assert updated["metadata"]["partial_profit_exit_mode"] == "full_exit_min_notional"
    assert orders[0]["status"] == "sent"


def test_live_futures_partial_profit_retries_full_when_prelookup_misses_min_notional(
    tmp_path: Path,
) -> None:
    adapter = _FilteredFillingEntryBinance()
    adapter.prices["LINKUSDT"] = 7.866
    trader = _trader(
        tmp_path,
        adapter=adapter,
        enabled=True,
        execute_futures=True,
    )
    trader._runtime_market_universe = {"spot": [], "futures": ["LINKUSDT"], "upbit_spot": []}

    async def missing_exit_min_notional(*, market: str, symbol: str) -> float:
        _ = (market, symbol)
        return 0.0

    trader._exit_min_notional = missing_exit_min_notional  # type: ignore[method-assign]
    block = trader.create_block(
        {
            "symbol": "LINKUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.53,
            "qty_open": 2.53,
            "entry_price": 7.9241,
            "target_price": 7.7974,
            "stop_price": 7.9875,
            "status": "open",
            "leverage": 1,
            "margin_type": "isolated",
            "liquidation_price": 10.698,
            "metadata": {
                "lane": "futures",
                "lane_authority": {
                    "weak_lanes": ["futures:short"],
                },
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    orders = trader.repository.list_orders(block_id=block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["qty"] == pytest.approx(2.53)
    assert adapter.futures_orders[0]["quantity"] == pytest.approx(2.53)
    assert adapter.futures_orders[0]["reduce_only"] is True
    assert len(orders) == 1
    assert orders[0]["status"] == "sent"
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["metadata"]["partial_profit_exit_mode"] == "full_exit_min_notional_retry"


def test_live_futures_partial_profit_retries_full_when_exchange_returns_min_notional_error(
    tmp_path: Path,
) -> None:
    adapter = _FuturesMinNotionalResponseErrorBinance()
    adapter.prices["LINKUSDT"] = 7.866
    trader = _trader(
        tmp_path,
        adapter=adapter,
        enabled=True,
        execute_futures=True,
    )
    trader._runtime_market_universe = {"spot": [], "futures": ["LINKUSDT"], "upbit_spot": []}
    block = trader.create_block(
        {
            "symbol": "LINKUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.53,
            "qty_open": 2.53,
            "entry_price": 7.9241,
            "target_price": 7.7974,
            "stop_price": 7.9875,
            "status": "open",
            "leverage": 1,
            "margin_type": "isolated",
            "liquidation_price": 10.698,
            "metadata": {
                "lane": "futures",
                "lane_authority": {
                    "weak_lanes": ["futures:short"],
                },
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    orders = trader.repository.list_orders(block_id=block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["qty"] == pytest.approx(2.53)
    assert len(adapter.futures_orders) == 2
    assert adapter.futures_orders[0]["quantity"] == pytest.approx(1.265)
    assert adapter.futures_orders[1]["quantity"] == pytest.approx(2.53)
    assert adapter.futures_orders[1]["reduce_only"] is True
    assert len(orders) == 1
    assert orders[0]["status"] == "sent"
    assert orders[0]["qty"] == pytest.approx(2.53)
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["metadata"]["partial_profit_exit_mode"] == "full_exit_min_notional_retry"
    assert "order notional below minimum" in updated["metadata"]["partial_profit_retry_reason"]


def test_executor_uses_runtime_scorecard_for_weak_lane_profit_protection(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"spot-long-loss-{index}",
                "symbol": "LOSSUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": -0.25,
                "r_multiple": -0.4,
                "mfe_r_multiple": 0.65,
                "mae_r_multiple": -0.8,
            }
        )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["trigger_source"] == "weak_performance_lane"
    assert updated is not None
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["stop_price"] > 100.0
    matched_card = updated["metadata"]["runtime_weak_performance_lane"]["matched_card"]
    assert (matched_card.get("side") or matched_card.get("lane")) == "spot:long"


def test_executor_partially_takes_profit_when_recent_mfe_is_surrendered_globally(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    for index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"futures-short-mfe-surrender-{index}",
                "symbol": "LOSSUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "target_price": 96.0,
                "stop_price": 102.0,
                "pnl_usdt": -0.25,
                "r_multiple": -0.5,
                "mfe_r_multiple": 1.1,
                "mae_r_multiple": -0.7,
            }
        )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["trigger_source"] == "mfe_surrender_repair"
    assert result["actions"][0]["trigger_r"] == pytest.approx(0.8)
    assert updated is not None
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["stop_price"] > 100.0
    assert updated["metadata"]["partial_profit_trigger_source"] == "mfe_surrender_repair"
    assert updated["metadata"]["partial_profit_repair_context"]["enabled"] is True


def test_runtime_weak_lane_uses_broader_window_when_recent_rows_crowd_lane(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"futures-short-loss-{index}",
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 4200.0,
                "exit_price": 4230.0,
                "target_price": 4100.0,
                "stop_price": 4230.0,
                "pnl_usdt": -0.4,
                "r_multiple": -1.0,
                "mfe_r_multiple": 0.9,
                "mae_r_multiple": -1.0,
                "created_at": f"2026-06-19T00:00:{index:02d}+00:00",
            }
        )
    for index in range(45):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"spot-long-win-{index}",
                "symbol": "BTCUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "target_price": 104.0,
                "stop_price": 98.0,
                "pnl_usdt": 0.1,
                "r_multiple": 0.5,
                "mfe_r_multiple": 0.7,
                "mae_r_multiple": -0.2,
                "created_at": f"2026-06-19T01:00:{index:02d}+00:00",
            }
        )
    block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "short",
            "qty": 0.01,
            "qty_open": 0.01,
            "entry_price": 4200.0,
            "target_price": 4100.0,
            "stop_price": 4230.0,
            "liquidation_price": 5200.0,
            "status": "open",
        }
    )

    context = trader._runtime_weak_performance_lane_context(block)

    assert context["matched"] is True
    assert context["source"] == "runtime_scorecard"
    assert context["feedback_limit"] == 120
    assert context["matched_card"]["lane"] == "futures:short"
    assert context["matched_card"]["sample_count"] == 3


def test_executor_takes_profit_earlier_for_distressed_runtime_lane(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"spot-long-distressed-loss-{index}",
                "symbol": "LOSSUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 90.0,
                "target_price": 120.0,
                "stop_price": 90.0,
                "pnl_usdt": -0.25,
                "r_multiple": -1.0,
                "mfe_r_multiple": 0.62,
                "mae_r_multiple": -1.0,
            }
        )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 106.0

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "partial_profit_taken"
    assert result["actions"][0]["trigger_source"] == "distressed_performance_lane"
    assert result["actions"][0]["trigger_r"] == pytest.approx(0.55)
    assert updated is not None
    assert updated["qty_open"] == pytest.approx(0.25)
    assert updated["stop_price"] > 100.0
    assert updated["metadata"]["partial_profit_trigger_source"] == "distressed_performance_lane"
    assert updated["metadata"]["runtime_weak_performance_lane"]["distressed"] is True


def test_executor_locks_profit_earlier_for_insufficient_validation_lane_suffix(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
            "metadata": {
                "lane": "short",
                "partial_profit_taken_at": "2026-06-01T00:00:00+00:00",
                "lane_authority_gate": {
                    "insufficient_lanes": [
                        "spot:long:short:validation:cost_simulation",
                    ],
                    "lane_action": "validation_evidence_repair_waiting_probe",
                },
            },
        }
    )
    adapter.prices["BTCUSDT"] = 108.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "ok"
    assert result["actions"][0]["status"] == "profit_locked"
    assert result["actions"][0]["trigger_source"] == "weak_performance_lane"
    assert updated is not None
    assert updated["metadata"]["profit_lock_trigger_source"] == "weak_performance_lane"


def test_executor_tick_dedupes_same_symbol_quote_requests(tmp_path: Path) -> None:
    adapter = _CountingQuoteBinance()
    trader = _trader(tmp_path, adapter=adapter)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.2,
            "qty_open": 0.2,
            "entry_price": 101.0,
            "target_price": 121.0,
            "stop_price": 91.0,
            "status": "open",
        }
    )

    result = asyncio.run(trader.executor_tick())

    assert result["status"] == "ok"
    assert result["quote_count"] == 1
    assert result["aux_quote_count"] == 1
    assert adapter.quote_calls == [("spot", "BTCUSDT"), ("spot", "BNBUSDT")]


def test_executor_tick_keeps_running_when_quote_fetch_fails(tmp_path: Path) -> None:
    trader = _trader(tmp_path, adapter=_FailingQuoteBinance())
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )

    result = asyncio.run(trader.executor_tick())
    quotes = trader.repository.quote_prices(
        symbol="BTCUSDT",
        market="spot",
        start_at="",
        end_at="9999-12-31T00:00:00+00:00",
    )

    assert result["status"] == "ok"
    assert result["quote_count"] == 1
    assert result["action_count"] == 0
    assert quotes == []


def test_live_futures_stop_uses_futures_execute_flag(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter, execute_futures=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "long",
            "qty": 1.25,
            "qty_open": 1.25,
            "entry_price": 200.0,
            "target_price": 240.0,
            "stop_price": 180.0,
            "status": "open",
            "leverage": 2,
            "margin_type": "isolated",
            "liquidation_price": 150.0,
        }
    )
    adapter.prices["ETHUSDT"] = 179.0

    result = asyncio.run(trader.executor_tick())

    assert result["actions"][0]["reason"] == "stop_reached"
    assert adapter.futures_orders == [
        {
            "symbol": "ETHUSDT",
            "side": "sell",
            "quantity": 1.25,
            "reduce_only": True,
            "limit_price": pytest.approx(178.642),
            "client_order_id": adapter.futures_orders[0]["client_order_id"],
        }
    ]
    orders = trader.repository.list_orders(block_id=block["block_id"])
    assert orders[0]["status"] == "sent"
    assert orders[0]["response"]["order_id"] == "F1"


def test_missing_binance_submit_method_raises_instead_of_paper_fallback(
    tmp_path: Path,
) -> None:
    adapter = _NoSubmitBinance()
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.0,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "proposed",
        }
    )

    with pytest.raises(RuntimeError, match="missing Binance adapter method: submit_spot_order"):
        asyncio.run(
            trader._submit_entry_order(
                block=block,
                market="spot",
                symbol="BTCUSDT",
                side="buy",
                qty=0.5,
                price=100.0,
            )
        )
    with pytest.raises(RuntimeError, match="missing Binance adapter method: submit_spot_order"):
        asyncio.run(
            trader._submit_exit_order(
                market="spot",
                symbol="BTCUSDT",
                side="sell",
                qty=0.5,
                price=111.0,
            )
        )


def test_unfilled_live_exit_rearms_open_block_for_retry(tmp_path: Path) -> None:
    adapter = _ExpiredExitBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.5,
                "available": 0.5,
                "locked": 0.0,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    first = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    second = asyncio.run(trader.executor_tick())

    assert first["action_count"] == 1
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["force_exit_requested"] is True
    assert len(adapter.spot_orders) == 1
    assert second["action_count"] == 0


def test_unfilled_futures_exit_retries_on_next_tick_without_cooldown(
    tmp_path: Path,
) -> None:
    adapter = _ExpiredFuturesExitBinance()
    trader = _trader(tmp_path, adapter=adapter, execute_futures=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "long",
            "qty": 1.25,
            "qty_open": 1.25,
            "entry_price": 200.0,
            "target_price": 240.0,
            "stop_price": 180.0,
            "status": "open",
            "leverage": 2,
            "margin_type": "isolated",
            "liquidation_price": 150.0,
        }
    )
    adapter.prices["ETHUSDT"] = 179.0

    first = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    second = asyncio.run(trader.executor_tick())

    assert first["action_count"] == 1
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["force_exit_requested"] is True
    assert updated["metadata"]["exit_retry_cooldown_sec"] == 0
    assert "exit_retry_after_ts" not in updated["metadata"]
    assert second["action_count"] == 1
    assert len(adapter.futures_orders) == 2


def test_transient_live_exit_error_rearms_open_block_for_retry(tmp_path: Path) -> None:
    adapter = _FailingExitBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.5,
                "available": 0.5,
                "locked": 0.0,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    first = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    second = asyncio.run(trader.executor_tick())

    assert first["action_count"] == 1
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["force_exit_requested"] is True
    assert "temporary binance outage" in updated["risk_note"]
    assert len(adapter.spot_orders) == 1
    assert second["action_count"] == 0


def test_live_spot_exit_clamps_sell_to_available_spot_balance(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 555.9435,
                "available": 555.9435,
                "locked": 0.0,
            }
        ],
        "spot_cash_usdt": 0.0,
        "futures_cash_usdt": 0.0,
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 556.5,
            "qty_open": 556.5,
            "entry_price": 0.2156,
            "target_price": 0.2295,
            "stop_price": 0.2092,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 0.2090

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    orders = trader.repository.list_orders(block_id=block["block_id"])

    assert result["action_count"] == 1
    assert adapter.spot_orders[0]["quantity"] == pytest.approx(555.9435)
    assert result["actions"][0]["qty"] == pytest.approx(555.9435)
    assert orders[0]["qty"] == pytest.approx(555.9435)
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == 0.0
    assert updated["force_exit_requested"] is False


def test_live_spot_exit_matches_asset_only_binance_spot_balance(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "kind": "position",
                "qty": 0.249,
                "available": 0.249,
                "locked": 0.0,
            }
        ],
        "spot_cash_usdt": 0.0,
        "futures_cash_usdt": 0.0,
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.25,
            "qty_open": 0.25,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["action_count"] == 1
    assert adapter.spot_orders[0]["quantity"] == pytest.approx(0.249)
    assert result["actions"][0]["quantity_context"]["balance_source"] == "spot_account"
    assert result["actions"][0]["quantity_context"]["clamped"] is True
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == 0.0


def test_live_spot_exit_with_no_available_balance_skips_order_and_cools_down(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.5,
                "available": 0.0,
                "locked": 0.5,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    first = asyncio.run(trader.executor_tick())
    second = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert first["action_count"] == 1
    assert first["actions"][0]["status"] == "skipped"
    assert second["action_count"] == 0
    assert adapter.spot_orders == []
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["force_exit_requested"] is True
    assert "available quantity is zero" in updated["risk_note"]


def test_live_spot_exit_insufficient_balance_error_freezes_block(
    tmp_path: Path,
) -> None:
    adapter = _InsufficientBalanceExitBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.5,
                "available": 0.5,
                "locked": 0.0,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    first = asyncio.run(trader.executor_tick())
    second = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert first["action_count"] == 1
    assert first["actions"][0]["status"] == "reconciliation_error"
    assert second["action_count"] == 0
    assert len(adapter.spot_orders) == 1
    assert updated is not None
    assert updated["status"] == "error"
    assert updated["force_exit_requested"] is False
    assert "insufficient balance" in updated["risk_note"].lower()
    assert events[0]["event_type"] == "exit_reconciliation_error"


def test_live_spot_exit_retries_with_fresh_lower_balance_after_insufficient_balance(
    tmp_path: Path,
) -> None:
    adapter = _ShrinkingBalanceExitBinance()
    adapter.accounts = [
        {
            "status": "ok",
            "spot_assets": [
                {
                    "asset": "BTC",
                    "symbol": "BTCUSDT",
                    "qty": 0.063,
                    "available": 0.063,
                    "locked": 0.0,
                }
            ],
        },
        {
            "status": "ok",
            "spot_assets": [
                {
                    "asset": "BTC",
                    "symbol": "BTCUSDT",
                    "qty": 0.0629,
                    "available": 0.0629,
                    "locked": 0.0,
                }
            ],
        },
    ]
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.063,
            "qty_open": 0.063,
            "entry_price": 206.88,
            "target_price": 210.19,
            "stop_price": 205.22,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 204.0

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    orders = trader.repository.list_orders(block_id=block["block_id"], limit=10)
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "sent"
    assert result["actions"][0]["qty"] == pytest.approx(0.0629)
    assert result["actions"][0]["quantity_context"]["insufficient_balance_retry"] is True
    assert len(adapter.spot_orders) == 2
    assert adapter.spot_orders[0]["quantity"] == pytest.approx(0.063)
    assert adapter.spot_orders[1]["quantity"] == pytest.approx(0.0629)
    assert len(orders) == 2
    assert orders[0]["status"] == "sent"
    assert orders[1]["status"] == "error"
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == 0.0
    assert updated["force_exit_requested"] is False
    assert events[0]["event_type"] == "insufficient_balance_exit_retry"


def test_live_spot_exit_records_retry_balance_lookup_failure_after_insufficient_balance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter = _InsufficientBalanceExitBinance()
    snapshot_calls = 0

    async def fake_account_snapshot() -> dict[str, Any]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 1:
            return {
                "status": "ok",
                "spot_assets": [
                    {
                        "asset": "BTC",
                        "symbol": "BTCUSDT",
                        "qty": 0.063,
                        "available": 0.063,
                        "locked": 0.0,
                    }
                ],
            }
        raise RuntimeError("fresh balance snapshot failed")

    monkeypatch.setattr(adapter, "fetch_account_snapshot", fake_account_snapshot)
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.063,
            "qty_open": 0.063,
            "entry_price": 206.88,
            "target_price": 210.19,
            "stop_price": 205.22,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 204.0

    result = asyncio.run(trader.executor_tick())
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "reconciliation_error"
    assert len(adapter.spot_orders) == 1
    assert snapshot_calls == 2
    payload = events[0]["payload"]
    assert payload["quantity_context"]["insufficient_balance_retry"] is False
    assert (
        payload["quantity_context"]["insufficient_balance_retry_error"]
        == "fresh balance snapshot failed"
    )


def test_live_spot_exit_missing_account_asset_marks_reconciliation_error(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [],
        "spot_cash_usdt": 100.0,
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "reconciliation_error"
    assert adapter.spot_orders == []
    assert updated is not None
    assert updated["status"] == "error"
    assert updated["force_exit_requested"] is False
    assert "spot asset missing" in updated["risk_note"]
    assert events[0]["event_type"] == "exit_reconciliation_error"


def test_live_spot_exit_reconciles_error_block_when_asset_still_missing(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [],
        "spot_cash_usdt": 100.0,
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 111.0

    first = asyncio.run(trader.executor_tick())
    second = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert first["actions"][0]["status"] == "reconciliation_error"
    assert second["action_count"] == 1
    assert second["actions"][0]["status"] == "reconciled_missing_asset"
    assert adapter.spot_orders == []
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == 0.0
    assert updated["force_exit_requested"] is False
    assert events[0]["event_type"] == "exit_reconciled_missing_asset"


def test_live_spot_exit_closes_block_when_remaining_balance_is_dust(
    tmp_path: Path,
) -> None:
    adapter = _SpotFilteredFillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 555.9435,
                "available": 555.9435,
                "locked": 0.0,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 556.5,
            "qty_open": 556.5,
            "entry_price": 0.2156,
            "target_price": 0.2295,
            "stop_price": 0.2092,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 0.2090

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["action_count"] == 1
    assert adapter.spot_orders[0]["quantity"] == pytest.approx(555.9)
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == 0.0
    assert updated["force_exit_requested"] is False


def test_live_spot_exit_closes_existing_dust_balance_without_order(
    tmp_path: Path,
) -> None:
    adapter = _SpotFilteredFillingEntryBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.0435,
                "available": 0.0435,
                "locked": 0.0,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.6,
            "qty_open": 0.6,
            "entry_price": 0.2156,
            "target_price": 0.2295,
            "stop_price": 0.2092,
            "status": "open",
            "force_exit_requested": True,
        }
    )
    adapter.prices["BTCUSDT"] = 0.2090

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "closed_as_dust"
    assert adapter.spot_orders == []
    assert updated is not None
    assert updated["status"] == "closed"
    assert updated["qty_open"] == 0.0
    assert updated["force_exit_requested"] is False


def test_remaining_exit_qty_never_expands_to_symbol_wallet_balance() -> None:
    full = remaining_exit_qty(
        requested_qty=1.6,
        filled_qty=1.6,
        price=72.0,
    )
    partial = remaining_exit_qty(
        requested_qty=1.6,
        filled_qty=1.0,
        price=72.0,
    )

    assert full == 0.0
    assert partial == pytest.approx(0.6)


def test_kill_switch_blocks_executor_and_manager(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "qty": 0.1,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                }
            ]
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_spot=True)
    trader.set_kill_switch(True, reason="test")
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "target_price": 101.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 102.0

    tick = asyncio.run(trader.executor_tick())
    manager = asyncio.run(trader.run_manager_once())

    assert tick["status"] == "blocked"
    assert tick["reason"] == "kill_switch_enabled"
    assert manager["status"] == "blocked"
    assert adapter.spot_orders == []
    assert llm.calls == []
    assert trader.status()["kill_switch"]["enabled"] is True


def test_risk_guard_blocks_new_manager_entries_but_allows_llm_review(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "cash_usdt": 9_200.0,
        "spot_cash_usdt": 9_200.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.5,
                "available": 0.5,
                "locked": 0.0,
            }
        ],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                    "thesis": "risk guard test",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_spot=True, enabled=True)
    keys = trader._risk_guard_period_keys()
    trader.repository.set_equity_baseline(keys["day"], start_equity_usdt=10_000.0)
    trader.repository.set_equity_baseline(keys["month"], start_equity_usdt=10_000.0)

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))

    assert result["status"] == "ok"
    assert llm.calls != []
    assert llm.calls[0]["payload"]["risk_guard"]["status"] == "halt_new_entries"
    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "risk_guard_halt_new_entries"
    assert trader.list_blocks(include_closed=False) == []


def test_risk_guard_does_not_block_existing_exit_orders(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "cash_usdt": 9_200.0,
        "spot_cash_usdt": 9_200.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.5,
                "available": 0.5,
                "locked": 0.0,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    keys = trader._risk_guard_period_keys()
    trader.repository.set_equity_baseline(keys["day"], start_equity_usdt=10_000.0)
    trader.repository.set_equity_baseline(keys["month"], start_equity_usdt=10_000.0)
    trader._last_account_snapshot = _normalize_test_account(trader, adapter.account)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 101.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 102.0

    result = asyncio.run(trader.executor_tick())

    assert result["action_count"] == 1
    assert result["actions"][0]["reason"] == "target_reached"
    assert adapter.spot_orders != []
    updated = trader.get_block(block["block_id"])
    assert updated is not None
    assert updated["status"] in {"open", "exit_pending", "closed"}


def test_growth_governor_rebuild_mode_rejects_immediate_entries(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 80.0,
                    "stop_price": 110.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 150.0,
                    "thesis": "immediate futures short under weak edge",
                }
            ],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_spot=True, enabled=True)
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"weak-edge-{index}",
                "symbol": "ALTUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 1.0,
                "exit_price": 1.05,
                "stop_price": 1.05,
                "target_price": 0.85,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
            }
        )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    prompt = llm.calls[0]["payload"]

    assert prompt["growth_governor"]["mode"] == "edge_rebuild"
    assert prompt["growth_governor"]["require_waiting_entry"] is True
    assert "positive_lanes" in prompt["policy"]["growth_governor_policy"]
    assert prompt["growth_unlock"]["phase"] == "rebuilding"
    assert prompt["growth_unlock"]["can_leave_edge_rebuild"] is False
    assert prompt["growth_unlock"]["action_permissions"]["new_waiting_entry_probe"] is True
    assert prompt["growth_unlock"]["action_permissions"]["immediate_entry"] is False
    assert any(
        row["mission"] == "rebuild_edge_quality"
        for row in prompt["growth_unlock"]["next_missions"]
    )
    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "growth_governor_requires_waiting_entry"
    assert trader.list_blocks(include_closed=False) == []


def test_growth_governor_positive_lane_is_not_global_rebuild_limited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 10_000.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 96.0,
                    "stop_price": 102.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 150.0,
                    "confidence": 0.82,
                    "thesis": "positive futures short lane can be pressed selectively",
                    "regime_alignment": "aligned",
                    "funding_context": "neutral",
                    "alpha_event": "narrative continuation",
                },
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 100.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 50.0,
                    "confidence": 0.75,
                    "thesis": "weak futures long lane waiting probe",
                    "regime_alignment": "aligned",
                    "funding_context": "neutral",
                },
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 99.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 99.0,
                    "target_price": 103.0,
                    "stop_price": 97.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 50.0,
                    "confidence": 0.75,
                    "thesis": "second weak futures long lane waiting probe",
                    "regime_alignment": "aligned",
                    "funding_context": "neutral",
                },
            ],
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        execute_futures=False,
        enabled=True,
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "validation_gate": {"status": "clear"},
    }

    def fake_growth_governor(**_: Any) -> dict[str, Any]:
        return {
            "version": "test_growth_governor_v1",
            "status": "edge_rebuild",
            "mode": "edge_rebuild",
            "allow_new_blocks": True,
            "max_new_blocks": 1,
            "require_waiting_entry": True,
            "weak_lanes": ["futures:long"],
            "positive_lanes": ["futures:short"],
            "positive_lane_count": 1,
        }

    monkeypatch.setattr(trader, "_growth_governor_context", fake_growth_governor)

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT", "ETHUSDT"]))

    created = result["applied"]["created"]
    assert created[0]["status"] == "created"
    assert created[1]["status"] == "waiting_entry"
    assert created[2]["status"] == "rejected"
    assert created[2]["reason"] == "growth_governor_new_block_limit"
    blocks = {
        block["symbol"]: block for block in trader.list_blocks(include_closed=False)
    }
    assert set(blocks) == {"BTCUSDT", "ETHUSDT"}
    assert blocks["BTCUSDT"]["metadata"].get("growth_governor") is None
    assert blocks["ETHUSDT"]["metadata"]["growth_governor"]["weak_lanes"] == [
        "futures:long"
    ]


def test_lane_authority_rejects_weak_binance_immediate_entry(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 10_000.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 80.0,
                    "stop_price": 110.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 150.0,
                    "thesis": "futures short lane 성과가 약한데 즉시 진입을 시도한다.",
                    "confidence": 0.8,
                }
            ],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_futures=False, enabled=True)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.5,
        "scorecard_count": 10,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["futures:short"],
            "insufficient_lanes": [],
            "lane_actions": {
                "futures:short": {
                    "grade": "restricted",
                    "action": "de_risk_or_waiting_entry",
                }
            },
        },
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == (
        "lane_authority_requires_waiting_entry"
    )
    assert result["applied"]["created"][0]["lane_authority_gate"]["matched_lanes"] == [
        "futures:short"
    ]
    assert trader.list_blocks(include_closed=False) == []


def test_performance_lane_weakness_rejects_binance_immediate_entry(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 10_000.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 80.0,
                    "stop_price": 110.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 150.0,
                    "thesis": "성과가 약한 futures short lane에서 즉시진입을 시도한다.",
                    "confidence": 0.8,
                }
            ],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_futures=False, enabled=True)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 0,
        "performance_lanes": [
            {
                "venue": "binance",
                "lane": "futures_short",
                "alpha_count": 7,
                "quality_hint": "weak_review",
                "action_hint": "observe_small_probe_or_waiting_entry",
            }
        ],
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == "performance_lane_requires_waiting_entry"
    assert created["lane_authority_gate"]["budget_multiplier_source"] == (
        "performance_lanes"
    )
    assert created["lane_authority_gate"]["performance_quality_hint"] == "weak_review"
    assert trader.list_blocks(include_closed=False) == []


def test_scale_candidate_performance_lane_expands_binance_sizing_equity(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.02,
                "risk_budget_usdt": 10.0,
                "reward_risk": 2.4,
            }

    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 0.01,
                        "margin_type": "isolated",
                        "leverage": 2,
                        "liquidation_price": 60_000,
                        "thesis": "검증된 futures short lane에서만 보수적으로 키운다.",
                    }
                ]
            }
        ),
        execute_futures=False,
        enabled=True,
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "scorecard_count": 12,
        "performance_lanes": [
            {
                "venue": "binance",
                "lane": "futures_short",
                "alpha_count": 12,
                "expectancy_pct": 0.72,
                "win_rate_pct": 57.0,
                "profit_factor": 1.9,
                "max_drawdown_pct": -3.5,
                "recovery_factor": 1.8,
                "quality_hint": "scale_candidate",
                "action_hint": "eligible_to_review_for_sizing_increase",
            }
        ],
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short",
                    "confidence": 0.88,
                    "price": 50_000,
                }
            ]
        )
    )
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert calls[0]["account_equity_usdt"] == pytest.approx(12_500.0)
    assert block["metadata"]["lane_authority_gate"]["budget_multiplier"] == pytest.approx(
        1.25
    )
    assert block["metadata"]["lane_authority_gate"]["scale_up_allowed"] is True
    assert block["metadata"]["risk_sizing"]["live_authority_budget_multiplier"] == (
        pytest.approx(1.0)
    )
    assert block["metadata"]["risk_sizing"]["lane_authority_budget_multiplier"] == (
        pytest.approx(1.25)
    )
    assert block["metadata"]["risk_sizing"]["sizing_equity_usdt"] == (
        pytest.approx(12_500.0)
    )


def test_validation_shadow_gate_blocks_binance_performance_lane_scale_up(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.02,
                "risk_budget_usdt": 10.0,
                "reward_risk": 2.4,
            }

    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 0.01,
                        "margin_type": "isolated",
                        "leverage": 2,
                        "liquidation_price": 60_000,
                        "thesis": "성과는 좋지만 WFA/OOS/live shadow 재검증 전에는 키우지 않는다.",
                    }
                ]
            }
        ),
        execute_futures=False,
        enabled=True,
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "scorecard_count": 12,
        "performance_lanes": [
            {
                "venue": "binance",
                "lane": "futures_short",
                "alpha_count": 12,
                "expectancy_pct": 0.72,
                "win_rate_pct": 57.0,
                "profit_factor": 1.9,
                "max_drawdown_pct": -3.5,
                "recovery_factor": 1.8,
                "quality_hint": "scale_candidate",
                "action_hint": "eligible_to_review_for_sizing_increase",
            }
        ],
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
        "lane_authority": {
            "version": "lane_authority_v1",
            "validation_shadow_gate": {
                "status": "revalidation_required_before_scale_up",
                "blocks_scale_up": True,
                "requires_waiting_entry": True,
            },
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short",
                    "confidence": 0.88,
                    "price": 50_000,
                }
            ]
        )
    )
    block = trader.repository.list_blocks(include_closed=False)[0]
    gate = block["metadata"]["lane_authority_gate"]

    assert result["status"] == "ok"
    assert calls[0]["account_equity_usdt"] == pytest.approx(10_000.0)
    assert gate["budget_multiplier"] == pytest.approx(1.0)
    assert gate["scale_up_allowed"] is False
    assert gate["validation_scale_blocked"] is True
    assert gate["validation_shadow_gate_status"] == (
        "revalidation_required_before_scale_up"
    )
    assert gate["requires_waiting_entry"] is True
    assert block["metadata"]["risk_sizing"]["lane_authority_budget_multiplier"] == (
        pytest.approx(1.0)
    )


def test_scale_candidate_binance_lane_does_not_expand_poor_entry_quality(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.02,
                "risk_budget_usdt": 10.0,
                "reward_risk": 2.4,
            }

    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 0.01,
                        "margin_type": "isolated",
                        "leverage": 2,
                        "liquidation_price": 60_000,
                        "price_location": "near_24h_high",
                        "chase_risk": "high",
                        "thesis": "성과 lane은 좋지만 고점권 대기라 키우지 않는다.",
                    }
                ]
            }
        ),
        execute_futures=False,
        enabled=True,
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "scorecard_count": 12,
        "performance_lanes": [
            {
                "venue": "binance",
                "lane": "futures_short",
                "alpha_count": 12,
                "quality_hint": "scale_candidate",
                "action_hint": "eligible_to_review_for_sizing_increase",
            }
        ],
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short",
                    "confidence": 0.88,
                    "price": 50_000,
                }
            ]
        )
    )
    block = trader.repository.list_blocks(include_closed=False)[0]
    gate = block["metadata"]["lane_authority_gate"]

    assert result["status"] == "ok"
    assert calls[0]["account_equity_usdt"] == pytest.approx(10_000.0)
    assert gate["budget_multiplier"] == pytest.approx(1.0)
    assert gate["scale_up_allowed"] is False
    assert "price_location_near_24h_high" in gate["scale_entry_quality"]["hard_pressure"]
    assert "chase_risk_high" in gate["scale_entry_quality"]["hard_pressure"]
    assert block["metadata"]["risk_sizing"]["lane_authority_budget_multiplier"] == (
        pytest.approx(1.0)
    )


def test_binance_performance_lane_risk_profile_caps_scale_candidate_sizing(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.02,
                "risk_budget_usdt": 10.0,
                "reward_risk": 2.4,
            }

    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 0.01,
                        "margin_type": "isolated",
                        "leverage": 2,
                        "liquidation_price": 60_000,
                        "thesis": "성과 lane은 좋지만 risk budget은 아직 절반만 허용한다.",
                    }
                ]
            }
        ),
        execute_futures=False,
        enabled=True,
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "scorecard_count": 12,
        "performance_lanes": [
            {
                "venue": "binance",
                "lane": "futures_short",
                "alpha_count": 12,
                "quality_hint": "scale_candidate",
                "action_hint": "eligible_to_review_for_sizing_increase",
                "risk_budget_multiplier": 0.5,
                "risk_of_ruin_pct": 13.5,
                "lane_confidence_score": 0.55,
                "recommended_risk_fraction": 0.01,
            }
        ],
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short",
                    "confidence": 0.88,
                    "price": 50_000,
                }
            ]
        )
    )
    block = trader.repository.list_blocks(include_closed=False)[0]
    gate = block["metadata"]["lane_authority_gate"]

    assert result["status"] == "ok"
    assert calls[0]["account_equity_usdt"] == pytest.approx(5_000.0)
    assert gate["budget_multiplier"] == pytest.approx(0.5)
    assert gate["scale_up_allowed"] is False
    assert gate["risk_budget_multiplier"] == pytest.approx(0.5)
    assert gate["risk_profile_allows_scale"] is False
    assert gate["risk_of_ruin_pct"] == pytest.approx(13.5)
    assert block["metadata"]["risk_sizing"]["lane_authority_budget_multiplier"] == (
        pytest.approx(0.5)
    )


def test_lane_authority_scales_weak_binance_waiting_entry_budget(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 10_000.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "quote_budget_usdt": 200.0,
                    "entry_price": 100.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 99.0,
                    "entry_trigger_operator": "lte",
                    "target_price": 120.0,
                    "stop_price": 90.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 50.0,
                    "thesis": "futures long lane은 약하므로 대기진입만 허용한다.",
                    "confidence": 0.8,
                }
            ],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_futures=False, enabled=True)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 10,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["futures:long"],
            "insufficient_lanes": [],
            "lane_actions": {
                "futures:long": {
                    "grade": "restricted",
                    "action": "de_risk_or_waiting_entry",
                    "max_budget_multiplier": 0.5,
                    "applied_max_budget_multiplier": 0.25,
                    "requires_waiting_entry": True,
                }
            },
        },
        "validation_gate": {"status": "clear", "readiness": "scale_ready"},
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    block = trader.list_blocks(include_closed=False)[0]
    metadata = block["metadata"]
    assert created["status"] == "waiting_entry"
    assert block["qty_initial"] == pytest.approx(37.5 / 99.0)
    assert metadata["quote_budget_usdt"] == pytest.approx(37.5)
    assert metadata["candidate_budget_cap"]["from_quote_budget_usdt"] == pytest.approx(200.0)
    assert metadata["candidate_budget_cap"]["to_quote_budget_usdt"] == pytest.approx(150.0)
    assert metadata["lane_authority_budget_adjustment"]["from_quote_budget_usdt"] == 150.0
    assert metadata["lane_authority_budget_adjustment"]["to_quote_budget_usdt"] == 37.5
    assert metadata["lane_authority_budget_adjustment"]["budget_multiplier"] == pytest.approx(0.25)


def test_validation_pressure_requires_binance_waiting_entry_for_immediate_block(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 10_000.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 94.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 55.0,
                    "thesis": "진단 fail이 있지만 즉시 진입을 시도한다.",
                    "confidence": 0.8,
                }
            ],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_futures=False, enabled=True)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "validation_gate": {
            "status": "validation_normal",
            "readiness": "normal",
            "reason": "diagnostic failures only",
            "validation_pressure": {
                "version": "validation_pressure_v1",
                "severity": "diagnostic_de_risk",
                "entry_posture": "patient_waiting_entry",
                "sizing_posture": "fractional_small_only",
                "block_design_requirements": [
                    "prefer_waiting_entry_or_price_improvement"
                ],
            },
        },
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == (
        "live_authority_requires_waiting_entry:"
        "validation_pressure:patient_waiting_entry"
    )
    assert trader.list_blocks(include_closed=False) == []


def test_validation_pressure_budget_caps_binance_waiting_entry_risk_sizing(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.02,
                "risk_budget_usdt": 10.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": "<=",
                        "entry_price": 50_000,
                        "target_price": 52_000,
                        "stop_price": 49_000,
                        "qty": 9.0,
                        "thesis": "validation pressure allows only a reduced waiting probe",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.5,
        "scorecard_count": 1,
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
            "validation_pressure": {
                "version": "validation_pressure_v1",
                "severity": "remediation_waiting_probe",
                "entry_posture": "patient_waiting_entry",
                "sizing_posture": "reduced_probe_only",
            },
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert calls[0]["account_equity_usdt"] == pytest.approx(5_000.0)
    assert block["qty_initial"] == pytest.approx(0.02)
    assert block["metadata"]["risk_sizing"]["live_authority_budget_multiplier"] == (
        pytest.approx(0.5)
    )
    assert block["metadata"]["risk_sizing"]["sizing_equity_usdt"] == (
        pytest.approx(5_000.0)
    )
    assert block["metadata"]["live_authority_gate"]["gate"][
        "validation_pressure_entry_posture"
    ] == "patient_waiting_entry"


def test_active_revision_evidence_rejects_immediate_binance_entry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 94.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 55.0,
                    "thesis": "새 revision 표본이 없지만 즉시 진입을 시도한다.",
                    "confidence": 0.8,
                }
            ],
        }
    )
    trader = _trader(tmp_path, llm=llm, execute_futures=False, enabled=True)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.0,
        "active_revision_evidence": {
            "version": "active_revision_evidence_v1",
            "venue": "binance",
            "strategy_revision_id": "jue_edge_repair_v1",
            "status": "no_active_revision_samples_with_proxy",
            "validation_sample_role": "legacy_proxy_metrics_no_scale",
            "effective_sample_count": 0,
            "min_samples_to_scale": 20,
            "scale_up_allowed": False,
        },
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
            "reason": "legacy gate clear but active revision has no samples",
        },
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == (
        "live_authority_requires_waiting_entry:"
        "active_revision_evidence:no_active_revision_samples_with_proxy"
    )
    assert trader.list_blocks(include_closed=False) == []


def test_pending_active_revision_evidence_rejects_immediate_binance_entry(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 94.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 55.0,
                    "thesis": "현재 revision의 닫힌 표본 없이 pending 블록만 있다.",
                    "confidence": 0.8,
                }
            ],
        }
    )
    trader = _trader(tmp_path, llm=llm, execute_futures=False, enabled=True)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.0,
        "active_revision_evidence": {
            "version": "active_revision_evidence_v1",
            "venue": "binance",
            "strategy_revision_id": "jue_edge_repair_v2",
            "status": "active_revision_samples_pending_close_with_proxy",
            "validation_sample_role": "legacy_proxy_metrics_no_scale",
            "active_sample_count": 0,
            "effective_sample_count": 0,
            "legacy_proxy_sample_count": 149,
            "pending_block_count": 14,
            "min_samples_to_scale": 20,
            "scale_up_allowed": False,
        },
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
        },
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "stance": "long",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == (
        "live_authority_requires_waiting_entry:"
        "active_revision_evidence:active_revision_samples_pending_close_with_proxy"
    )
    assert trader.list_blocks(include_closed=False) == []


def test_active_revision_evidence_caps_binance_waiting_entry_sizing(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": "<=",
                        "entry_price": 50_000,
                        "target_price": 52_000,
                        "stop_price": 49_000,
                        "qty": 9.0,
                        "thesis": "새 revision 표본을 소액 대기진입으로 검증한다.",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.0,
        "active_revision_evidence": {
            "version": "active_revision_evidence_v1",
            "venue": "binance",
            "strategy_revision_id": "jue_edge_repair_v1",
            "status": "insufficient_active_revision_samples",
            "effective_sample_count": 4,
            "min_samples_to_scale": 20,
            "scale_up_allowed": False,
        },
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
            "reason": "legacy gate clear but active revision is still probing",
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert calls[0]["account_equity_usdt"] == pytest.approx(5_000.0)
    assert block["qty_initial"] == pytest.approx(0.01)
    assert block["metadata"]["risk_sizing"]["live_authority_budget_multiplier"] == (
        pytest.approx(0.5)
    )
    assert block["metadata"]["risk_sizing"]["active_revision_budget_multiplier"] == (
        pytest.approx(0.5)
    )
    assert block["metadata"]["risk_sizing"]["active_revision_gate_reason"] == (
        "active_revision_evidence:insufficient_active_revision_samples"
    )
    assert block["metadata"]["live_authority_gate"]["gate"][
        "active_revision_evidence"
    ]["budget_multiplier_cap"] == pytest.approx(0.5)


def test_lane_authority_matches_setup_specific_binance_lane() -> None:
    gate = BinanceBlockTrader._live_authority_lane_gate(
        {
            "lane_authority": {
                "version": "lane_authority_v1",
                "weak_lanes": ["futures:short:late_chase"],
                "insufficient_lanes": [],
                "lane_actions": {
                    "futures:short:late_chase": {
                        "grade": "restricted",
                        "action": "de_risk_or_waiting_entry",
                        "max_budget_multiplier": 0.5,
                        "applied_max_budget_multiplier": 0.25,
                        "requires_waiting_entry": True,
                    }
                },
            }
        },
        {
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "strategy_family": "late_chase",
        },
        waiting_entry=False,
    )

    assert gate["ok"] is False
    assert gate["matched_lanes"] == ["futures:short:late_chase"]
    assert gate["reason"] == "lane_authority_requires_waiting_entry"


def test_lane_authority_matches_validation_repair_binance_lane() -> None:
    gate = BinanceBlockTrader._live_authority_lane_gate(
        {
            "lane_authority": {
                "version": "lane_authority_v1",
                "weak_lanes": ["futures:long:validation:correlation"],
                "insufficient_lanes": [],
                "lane_actions": {
                    "futures:long:validation:correlation": {
                        "grade": "restricted",
                        "action": "concentration_checked_waiting_entry",
                        "max_budget_multiplier": 0.5,
                        "applied_max_budget_multiplier": 0.25,
                        "requires_waiting_entry": True,
                    }
                },
            }
        },
        {
            "market": "futures",
            "side": "long",
            "horizon": "futures",
            "metadata": {
                "validation_repair": {
                    "discipline_ids": ["correlation"],
                }
            },
        },
        waiting_entry=False,
    )

    assert gate["ok"] is False
    assert gate["matched_lanes"] == ["futures:long:validation:correlation"]
    assert gate["reason"] == "lane_authority_requires_waiting_entry"
    assert gate["budget_multiplier"] == pytest.approx(0.25)


def test_lane_authority_matches_validation_evidence_binance_lane() -> None:
    gate = BinanceBlockTrader._live_authority_lane_gate(
        {
            "lane_authority": {
                "version": "lane_authority_v1",
                "validation_evidence_weak_lanes": ["futures:long:ema_trend"],
                "lane_actions": {
                    "futures:long:ema_trend": {
                        "grade": "qualified",
                        "action": "validation_evidence_repair_waiting_probe",
                        "applied_max_budget_multiplier": 0.5,
                        "requires_waiting_entry": True,
                        "validation_evidence_status": "partial",
                        "validation_missing_dimensions": ["live_shadow"],
                        "validation_failed_dimensions": ["walk_forward"],
                        "scale_blocked_by_validation_evidence": True,
                        "validation_evidence_repair_hint": (
                            "run_walk_forward_out_of_sample_and_live_shadow"
                        ),
                        "core_validation_evidence_gaps": [
                            "walk_forward",
                            "out_of_sample",
                            "live_shadow",
                        ],
                        "validation_evidence_repair_targets": [
                            "rebuild_walk_forward_windows_before_scale_up",
                            "pass_out_of_sample_validation_before_scale_up",
                            "collect_live_shadow_samples_before_scale_up",
                        ],
                        "validation_evidence_required_evidence": [
                            "funding",
                            "spread",
                            "slippage",
                        ],
                        "validation_evidence_required_checks": [
                            "positive_net_edge",
                        ],
                        "validation_evidence_pass_collection_hooks": [
                            "sync futures fills/funding -> refresh_trading_validation",
                        ],
                        "validation_evidence_pass_current_gaps": [
                            "live shadow and funding adjusted edge missing",
                        ],
                        "validation_evidence_pass_criteria": [
                            "net edge remains positive after funding and spread stress",
                        ],
                        "validation_evidence_verification_artifacts": [
                            "futures funding/spread/slippage proof packet",
                        ],
                        "validation_evidence_cap_multiplier": 0.5,
                        "cost_evidence_status": "partial",
                        "cost_precision_counts": {
                            "recorded": 1,
                            "hybrid": 1,
                            "estimated": 4,
                        },
                        "missing_cost_component_counts": {
                            "funding": 2,
                            "spread": 1,
                            "slippage": 1,
                        },
                        "cost_evidence_repair_hint": (
                            "record_fee_spread_slippage_and_funding"
                        ),
                        "cost_verified_alpha_count": 3,
                        "cost_unverified_alpha_count": 5,
                        "cost_verified_alpha_net_pnl": 8.75,
                        "cost_unverified_alpha_net_pnl": -4.25,
                        "verified_edge_sample_cap_multiplier": 0.5,
                        "scale_blocked_by_verified_edge_samples": True,
                        "cost_repair_targets": [
                            "record_missing_cost_component:funding",
                            "record_missing_cost_component:spread",
                            "record_missing_cost_component:slippage",
                        ],
                        "bad_entry_quality_label_counts": {
                            "late_chase": 2,
                            "wick_risk_high": 3,
                        },
                        "dominant_bad_entry_quality_label": "wick_risk_high",
                        "entry_quality_repair_hint": (
                            "use_waiting_entry_after_wick_risk_compresses"
                        ),
                        "entry_repair_targets": [
                            "wait_for_wick_risk_compression_before_entry",
                            "use_waiting_entry_or_price_improvement_before_live_block",
                        ],
                        "scale_decision": "capped_until_repairs",
                        "scale_blockers": [
                            "cost_evidence_repair",
                            "entry_quality_repair",
                            "validation_backtest_wfa_oos_shadow_cap",
                            "verified_edge_sample_cap",
                        ],
                        "scale_repair_targets": [
                            "record_missing_cost_component:funding",
                            "wait_for_wick_risk_compression_before_entry",
                            "rebuild_walk_forward_windows_before_scale_up",
                            "close_more_recorded_cost_alpha_samples_before_scale_up",
                        ],
                    }
                },
            }
        },
        {
            "market": "futures",
            "side": "long",
            "horizon": "futures",
            "strategy_family": "ema_trend",
        },
        waiting_entry=False,
    )

    assert gate["ok"] is False
    assert gate["matched_lanes"] == ["futures:long:ema_trend"]
    assert gate["reason"] == "lane_authority_requires_waiting_entry"
    assert gate["budget_multiplier"] == pytest.approx(0.5)
    assert gate["validation_evidence_status"] == "partial"
    assert gate["validation_missing_dimensions"] == ["live_shadow"]
    assert gate["validation_failed_dimensions"] == ["walk_forward"]
    assert gate["scale_blocked_by_validation_evidence"] is True
    assert gate["validation_evidence_repair_hint"] == (
        "run_walk_forward_out_of_sample_and_live_shadow"
    )
    assert gate["core_validation_evidence_gaps"] == [
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert gate["validation_evidence_repair_targets"] == [
        "rebuild_walk_forward_windows_before_scale_up",
        "pass_out_of_sample_validation_before_scale_up",
        "collect_live_shadow_samples_before_scale_up",
    ]
    assert gate["validation_evidence_required_evidence"] == [
        "funding",
        "spread",
        "slippage",
    ]
    assert gate["validation_evidence_required_checks"] == ["positive_net_edge"]
    assert gate["validation_evidence_pass_collection_hooks"] == [
        "sync futures fills/funding -> refresh_trading_validation"
    ]
    assert gate["validation_evidence_pass_current_gaps"] == [
        "live shadow and funding adjusted edge missing"
    ]
    assert gate["validation_evidence_pass_criteria"] == [
        "net edge remains positive after funding and spread stress"
    ]
    assert gate["validation_evidence_verification_artifacts"] == [
        "futures funding/spread/slippage proof packet"
    ]
    assert gate["validation_evidence_cap_multiplier"] == pytest.approx(0.5)
    assert gate["cost_evidence_status"] == "partial"
    assert gate["cost_precision_counts"] == {
        "recorded": 1,
        "hybrid": 1,
        "estimated": 4,
    }
    assert gate["missing_cost_component_counts"] == {
        "funding": 2,
        "spread": 1,
        "slippage": 1,
    }
    assert gate["cost_evidence_repair_hint"] == (
        "record_fee_spread_slippage_and_funding"
    )
    assert gate["cost_verified_alpha_count"] == pytest.approx(3)
    assert gate["cost_unverified_alpha_count"] == pytest.approx(5)
    assert gate["cost_verified_alpha_net_pnl"] == pytest.approx(8.75)
    assert gate["cost_unverified_alpha_net_pnl"] == pytest.approx(-4.25)
    assert gate["verified_edge_sample_cap_multiplier"] == pytest.approx(0.5)
    assert gate["scale_blocked_by_cost_precision"] is True
    assert gate["scale_blocked_by_cost_evidence"] is True
    assert gate["scale_blocked_by_verified_edge_samples"] is True
    assert gate["cost_repair_targets"] == [
        "record_missing_cost_component:funding",
        "record_missing_cost_component:spread",
        "record_missing_cost_component:slippage",
    ]
    assert gate["bad_entry_quality_label_counts"] == {
        "late_chase": 2,
        "wick_risk_high": 3,
    }
    assert gate["dominant_bad_entry_quality_label"] == "wick_risk_high"
    assert gate["entry_quality_repair_hint"] == (
        "use_waiting_entry_after_wick_risk_compresses"
    )
    assert gate["entry_repair_targets"] == [
        "wait_for_wick_risk_compression_before_entry",
        "use_waiting_entry_or_price_improvement_before_live_block",
    ]
    assert gate["scale_decision"] == "capped_until_repairs"
    assert gate["scale_blockers"] == [
        "cost_evidence_repair",
        "entry_quality_repair",
        "validation_backtest_wfa_oos_shadow_cap",
        "verified_edge_sample_cap",
    ]
    assert gate["scale_repair_targets"] == [
        "record_missing_cost_component:funding",
        "wait_for_wick_risk_compression_before_entry",
        "rebuild_walk_forward_windows_before_scale_up",
        "close_more_recorded_cost_alpha_samples_before_scale_up",
    ]


@pytest.mark.parametrize(
    "weak_key,action_name",
    [
        ("cost_weak_lanes", "cost_repair_waiting_probe"),
        ("cost_evidence_weak_lanes", "cost_evidence_repair_waiting_probe"),
        ("entry_quality_weak_lanes", "entry_quality_repair_waiting_entry"),
        ("validation_repair_weak_lanes", "validation_repair_enforced_waiting_probe"),
    ],
)
def test_lane_authority_treats_auxiliary_binance_weak_lane_lists_as_weak(
    weak_key: str,
    action_name: str,
) -> None:
    gate = BinanceBlockTrader._live_authority_lane_gate(
        {
            "lane_authority": {
                "version": "lane_authority_v1",
                weak_key: ["futures:long:ema_trend"],
                "lane_actions": {
                    "futures:long:ema_trend": {
                        "grade": "qualified",
                        "action": action_name,
                        "applied_max_budget_multiplier": 0.5,
                        "requires_waiting_entry": True,
                    }
                },
            }
        },
        {
            "market": "futures",
            "side": "long",
            "horizon": "futures",
            "strategy_family": "ema_trend",
        },
        waiting_entry=False,
    )

    assert gate["ok"] is False
    assert gate["matched_lanes"] == ["futures:long:ema_trend"]
    assert gate["reason"] == "lane_authority_requires_waiting_entry"
    assert gate["budget_multiplier"] == pytest.approx(0.5)
    assert gate["weak_lane_sources"] == [weak_key]


def test_lane_authority_matches_scale_candidate_binance_lane() -> None:
    gate = BinanceBlockTrader._live_authority_lane_gate(
        {
            "lane_authority": {
                "version": "lane_authority_v1",
                "global_scale_up_allowed": True,
                "max_budget_multiplier": 1.25,
                "weak_lanes": [],
                "insufficient_lanes": [],
                "scale_candidate_lanes": ["spot:long"],
                "lane_actions": {
                    "spot:long": {
                        "grade": "scale_candidate",
                        "action": "eligible_to_press_when_validation_clear",
                        "max_budget_multiplier": 1.25,
                        "applied_max_budget_multiplier": 1.25,
                        "scale_up_allowed": True,
                    }
                },
            }
        },
        {
            "market": "spot",
            "side": "long",
            "horizon": "short",
        },
        waiting_entry=False,
    )

    assert gate["ok"] is True
    assert gate["matched_lanes"] == ["spot:long"]
    assert gate["lane_action"] == "eligible_to_press_when_validation_clear"
    assert gate["budget_multiplier"] == pytest.approx(1.25)
    assert gate["scale_up_allowed"] is True
    assert gate["requires_waiting_entry"] is False


def test_lane_authority_gate_prefers_specific_positive_lane_over_broad_performance_weak() -> None:
    gate = BinanceBlockTrader._live_authority_lane_gate(
        {
            "status": "ok",
            "max_budget_multiplier": 0.25,
            "lane_authority": {
                "version": "lane_authority_v1",
                "weak_lanes": ["futures:short:validation:capacity_analysis"],
                "insufficient_lanes": ["futures:short:futures"],
                "lane_actions": {
                    "futures:short:validation:capacity_analysis": {
                        "grade": "insufficient",
                        "action": "validation_evidence_repair_waiting_probe",
                        "sample_count": 1,
                        "expectancy_pct": -0.04,
                        "profit_factor": 0.0,
                        "requires_waiting_entry": True,
                        "applied_max_budget_multiplier": 0.1,
                    },
                    "futures:short:futures": {
                        "grade": "insufficient",
                        "action": "validation_evidence_repair_waiting_probe",
                        "sample_count": 7,
                        "expectancy_pct": 0.25,
                        "profit_factor": 2.13,
                        "requires_waiting_entry": True,
                        "applied_max_budget_multiplier": 0.25,
                    },
                },
            },
            "performance_lanes": [
                {
                    "venue": "binance",
                    "lane": "futures",
                    "quality_hint": "weak_review",
                    "action_hint": "cost_evidence_repair_waiting_entry",
                    "risk_budget_multiplier": 0.25,
                }
            ],
        },
        {
            "symbol": "PAXGUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "entry_style": "wait_for_price",
        },
        waiting_entry=True,
    )

    assert gate["ok"] is True
    assert gate["matched_lanes"] == ["futures:short:futures"]
    assert gate["lane_authority"]["version"] == "lane_authority_v1"
    assert gate["lane_action"] == "validation_evidence_repair_waiting_probe"
    assert gate["budget_multiplier"] == pytest.approx(0.25)
    assert gate["selection_bias"] == "positive_sample_building"
    assert "performance_quality_hint" not in gate


def test_high_cost_insufficient_binance_lane_rejects_immediate_entry(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 10_000.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                    "thesis": "spot long lane은 샘플 부족이지만 비용 드래그가 높다.",
                    "confidence": 0.8,
                }
            ],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_spot=False, enabled=True)
    trader.live_authority_provider = lambda: build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "spot:long",
                "grade": "insufficient",
                "authority_multiplier": 0.75,
                "sample_count": 5,
                "expectancy_pct": 0.1,
                "win_rate": 50.0,
                "profit_factor": 1.05,
                "recovery_factor": 0.3,
                "cost_drag_pct_of_gross_pnl": 85.0,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == "lane_authority_requires_waiting_entry"
    assert created["lane_authority_gate"]["matched_lanes"] == ["spot:long"]
    assert created["lane_authority_gate"]["requires_waiting_entry"] is True
    assert created["lane_authority_gate"]["lane_action"] == "cost_repair_waiting_probe"
    assert trader.list_blocks(include_closed=False) == []


def test_manager_rejects_binance_block_when_target_move_cannot_clear_cost_floor(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 100.0,
                    "entry_trigger_operator": "<=",
                    "entry_price": 100.0,
                    "target_price": 100.25,
                    "stop_price": 99.5,
                    "qty": 0.5,
                    "spread_bps": 12.0,
                    "thesis": "target move is too thin after spread and fees",
                    "confidence": 0.8,
                    "evidence_refs": ["candidates:BTCUSDT"],
                }
            ],
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "horizon": "short",
                    "stance": "long_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "cost_edge_too_thin"
    gate = result["applied"]["created"][0]["cost_edge_gate"]
    assert gate["target_move_pct"] == pytest.approx(0.25)
    assert gate["estimated_round_trip_cost_pct"] == pytest.approx(0.32)
    assert gate["required_target_move_pct"] == pytest.approx(0.96)
    assert trader.list_blocks(include_closed=False) == []


def test_growth_governor_rebuild_mode_allows_two_waiting_entries(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 10_000.0,
        "futures_cash_usdt": 0.0,
        "positions": [],
    }
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 80.0,
                    "stop_price": 110.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 150.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 100.0,
                    "entry_trigger_operator": ">=",
                    "thesis": "first waiting rebuild entry",
                },
                {
                    "symbol": "ETHUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "qty": 0.1,
                    "entry_price": 200.0,
                    "target_price": 160.0,
                    "stop_price": 220.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 300.0,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 200.0,
                    "entry_trigger_operator": ">=",
                    "thesis": "second waiting rebuild entry",
                },
            ],
        }
    )
    trader = _trader(tmp_path, adapter=adapter, llm=llm, execute_spot=True, enabled=True)
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"weak-edge-wait-{index}",
                "symbol": "ALTUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 1.0,
                "exit_price": 1.05,
                "stop_price": 1.05,
                "target_price": 0.85,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
            }
        )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT", "ETHUSDT"]))
    snapshot = asyncio.run(trader.snapshot_compact())

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert result["applied"]["created"][1]["status"] == "waiting_entry"
    assert snapshot["growth_unlock"]["phase"] == "rebuilding"
    blocks = trader.list_blocks(include_closed=False)
    assert len(blocks) == 2
    assert {block["metadata"]["entry_style"] for block in blocks} == {"wait_for_price"}


def test_growth_unlock_context_marks_scale_ready_when_live_edge_is_verified() -> None:
    unlock = BinanceBlockTrader._growth_unlock_context(
        growth_governor={
            "mode": "press_verified_edges",
            "require_waiting_entry": False,
            "metrics": {"risk_guard_status": "ok"},
        },
        performance={
            "sample_count": 12,
            "win_rate_pct": 58.0,
            "avg_r_multiple": 0.42,
            "realized_pnl_usdt": 17.5,
        },
        live_authority={
            "status": "ok",
            "live_grade": "scale_candidate",
            "allow_scale_up": True,
        },
    )

    assert unlock["phase"] == "scale_ready"
    assert unlock["can_leave_edge_rebuild"] is True
    assert unlock["action_permissions"]["immediate_entry"] is True
    assert unlock["action_permissions"]["scale_up"] is True
    assert all(row["passed"] for row in unlock["criteria"])


@pytest.mark.parametrize(
    "payload,error",
    [
        (
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "qty": 1.0,
                "margin_type": "cross",
                "leverage": 1,
            },
            "isolated",
        ),
        (
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "qty": 1.0,
                "margin_type": "isolated",
                "leverage": 3,
            },
            "leverage",
        ),
        (
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "qty": 1.0,
                "margin_type": "isolated",
                "leverage": 2,
                "entry_price": 100.0,
                "liquidation_price": 95.0,
            },
            "liquidation",
        ),
    ],
)
def test_invalid_futures_risk_parameters_are_rejected(
    tmp_path: Path,
    payload: dict[str, Any],
    error: str,
) -> None:
    trader = _trader(tmp_path)

    with pytest.raises(ValueError, match=error):
        trader.create_block(payload)


@pytest.mark.parametrize(
    "payload,error",
    [
        (
            {
                "symbol": "BTCUSDT",
                "market": "spot",
                "qty": 0.1,
                "side": "long",
                "entry_price": 100.0,
                "target_price": 95.0,
                "stop_price": 90.0,
            },
            "long",
        ),
        (
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "qty": 0.1,
                "side": "short",
                "entry_price": 100.0,
                "target_price": 90.0,
                "stop_price": 95.0,
                "margin_type": "isolated",
                "leverage": 1,
                "liquidation_price": 130.0,
            },
            "short",
        ),
    ],
)
def test_invalid_price_direction_is_rejected(
    tmp_path: Path,
    payload: dict[str, Any],
    error: str,
) -> None:
    trader = _trader(tmp_path)

    with pytest.raises(ValueError, match=error):
        trader.create_block(payload)


def test_status_and_list_shape_are_stable(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "status": "proposed",
        }
    )
    trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "qty": 0.2,
            "qty_open": 0.2,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 90.0,
            "status": "error",
            "risk_note": "spot asset missing in account snapshot for ETHUSDT",
            "metadata": {
                "exit_reconciliation_error": {
                    "quantity_context": {"balance_source": "missing_asset"}
                }
            },
        }
    )
    frozen = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "risk_note": "frozen: upbit KRW/USDT price-scale mismatch repaired in code",
        }
    )
    trader.repository.update_block(
        frozen["block_id"],
        {
            "status": "error",
            "qty_open": 0,
            "risk_note": "frozen: upbit KRW/USDT price-scale mismatch repaired in code",
        },
    )

    status = trader.status()
    block = trader.list_blocks()[0]

    assert status["status"] == "ok"
    assert status["block_count"] == 3
    assert status["open_block_count"] == 0
    assert status["proposed_block_count"] == 1
    assert status["error_block_count"] == 1
    assert status["inactive_error_block_count"] == 1
    assert status["dangling_error_qty_block_count"] == 1
    assert status["dangling_error_qty_notional_usdt"] == pytest.approx(20.0)
    assert status["execution_mode"] == "paper"
    assert status["execute_spot_orders"] is False
    assert status["execute_futures_orders"] is False
    assert "latest_manager_status" in status
    assert {
        "block_id",
        "symbol",
        "market",
        "status",
        "qty_initial",
        "qty_open",
        "target_price",
        "stop_price",
        "metadata",
        "created_at",
        "updated_at",
    }.issubset(block)


def test_snapshot_compact_does_not_fetch_live_account_when_cache_is_empty(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path, adapter=_FailingAccountSnapshotBinance())
    trader.repository.save_manager_run(
        prompt={
            "risk_guard": {
                "status": "ok",
                "current_equity_usdt": 1234.5,
            }
        },
        response={},
        actions={},
        status="ok",
        mode="llm",
        model="gpt-5.5",
    )

    payload = asyncio.run(trader.snapshot_compact())

    assert payload["compact"] is True
    assert payload["account"]["total_equity_usdt"] == pytest.approx(1234.5)
    assert payload.get("account_fetch_error", "") == ""


def test_status_exposes_latest_manager_error_summary(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={},
        response={},
        actions={},
        status="error",
        mode="llm",
        model="gpt-5.5",
        error_message="codex native sdk timed out after 600.0s",
    )
    trader.repository.save_manager_run(
        prompt={},
        response={},
        actions={},
        status="ok",
        mode="llm",
        model="gpt-5.5",
    )

    status = trader.status()

    assert status["latest_manager_status"] == "ok"
    assert status["manager_operational_status"] == "ok"
    assert status["latest_manager_error"] == {
        "status": "error",
        "run_at": status["latest_manager_error"]["run_at"],
        "mode": "llm",
        "error_message": "codex native sdk timed out after 600.0s",
    }
    assert status["latest_manager_error_recovered"] is True
    assert status["latest_unresolved_manager_error"] == {}


def test_status_marks_current_manager_error_as_unresolved(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_manager_run(
        prompt={},
        response={},
        actions={},
        status="ok",
        mode="llm",
        model="gpt-5.5",
    )
    trader.repository.save_manager_run(
        prompt={},
        response={},
        actions={},
        status="error",
        mode="llm",
        model="gpt-5.5",
        error_message="manager_task_timeout_after_610s",
    )

    status = trader.status()

    assert status["latest_manager_status"] == "error"
    assert status["manager_operational_status"] == "manager_error_pending_next_run"
    assert status["latest_manager_error_recovered"] is False
    assert status["latest_unresolved_manager_error"] == status["latest_manager_error"]


def test_snapshot_compact_recovers_display_equity_from_daily_baseline_when_prompt_is_compacted(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path, adapter=_FailingAccountSnapshotBinance())
    trader.repository.set_equity_baseline(
        trader._risk_guard_period_keys()["day"],
        start_equity_usdt=1234.5,
    )
    trader.repository.save_manager_run(
        prompt={
            "growth_governor": {
                "status": "edge_rebuild",
                "metrics": {"risk_guard_status": "ok"},
            }
        },
        response={},
        actions={},
        status="ok",
        mode="llm",
        model="gpt-5.5",
    )

    payload = asyncio.run(trader.snapshot_compact())

    assert payload["compact"] is True
    assert payload["account"]["total_equity_usdt"] == pytest.approx(1234.5)
    assert payload["account"]["account_snapshot_source"] == "daily_equity_baseline"
    assert payload["account"]["stale"] is True
    assert payload["risk_guard"]["status"] == "ok"
    assert payload["growth_governor"]["metrics"]["risk_guard_status"] == "ok"


def test_snapshot_compact_never_fetches_live_account_for_status_polling(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path, adapter=_FailingAccountSnapshotBinance())

    payload = asyncio.run(trader.snapshot_compact())

    assert payload["compact"] is True
    assert payload["account"].get("total_equity_usdt", 0.0) == pytest.approx(0.0)
    assert payload.get("account_fetch_error", "") == ""


def test_snapshot_compact_limits_closed_block_history_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trader = _trader(tmp_path)
    calls: list[dict[str, Any]] = []
    original_list_block_summaries = trader.repository.list_block_summaries

    def list_block_summaries_spy(
        *,
        include_closed: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        calls.append({"include_closed": include_closed, "limit": limit})
        return original_list_block_summaries(
            include_closed=include_closed,
            limit=limit,
        )

    monkeypatch.setattr(
        trader.repository,
        "list_block_summaries",
        list_block_summaries_spy,
    )

    payload = asyncio.run(trader.snapshot_compact())

    assert payload["compact"] is True
    assert any(
        call["include_closed"] is True and call["limit"] == 30 for call in calls
    )


def test_snapshot_compact_omits_heavy_block_history_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trader = _trader(tmp_path)
    heavy_block = {
        "block_id": "B-heavy",
        "symbol": "BTCUSDT",
        "market": "futures",
        "side": "long",
        "qty_initial": 0.1,
        "qty_open": 0.0,
        "entry_price": 100.0,
        "target_price": 108.0,
        "stop_price": 96.0,
        "status": "closed",
        "thesis": "history thesis",
        "risk_note": "history risk",
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T01:00:00+00:00",
        "closed_at": "2026-06-01T01:00:00+00:00",
        "metadata": {
            "horizon": "short",
            "lane": "volatile_attack",
            "raw_candidate_packet": "raw candidate evidence " * 20_000,
        },
    }

    def list_block_summaries_spy(
        *,
        include_closed: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return [heavy_block] if include_closed else []

    monkeypatch.setattr(
        trader.repository,
        "list_block_summaries",
        list_block_summaries_spy,
    )

    payload = asyncio.run(trader.snapshot_compact())
    history_row = payload["block_history"][0]

    assert history_row["block_id"] == "B-heavy"
    assert history_row["horizon"] == "short"
    assert history_row["lane"] == "volatile_attack"
    assert "metadata" not in history_row
    assert "raw candidate evidence" not in json.dumps(payload, ensure_ascii=False)


def test_snapshot_compact_compacts_live_authority_status_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trader = _trader(tmp_path)
    heavy_authority = {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["futures:short"],
            "lane_actions": {
                f"lane-{index}": {"raw": "lane evidence " * 1_000}
                for index in range(20)
            },
        },
        "validation_gate": {
            "status": "validation_probe",
            "reason": "probe mode",
            "operator_guidance": ["raw guidance " * 1_000],
        },
    }

    monkeypatch.setattr(
        trader,
        "status",
        lambda: {"status": "ok", "live_authority": heavy_authority},
    )

    payload = asyncio.run(trader.snapshot_compact())
    live_authority = payload["live_authority"]

    assert live_authority["live_grade"] == "restricted"
    assert live_authority["lane_authority"]["lane_action_count"] == 20
    assert "raw guidance" not in json.dumps(payload, ensure_ascii=False)
    assert "lane evidence" not in json.dumps(payload, ensure_ascii=False)


def test_snapshot_compact_error_names_failed_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trader = _trader(tmp_path)

    def broken_status() -> dict[str, Any]:
        raise OverflowError("math range error")

    monkeypatch.setattr(trader, "status", broken_status)

    with pytest.raises(RuntimeError, match="snapshot_compact:status: math range error"):
        asyncio.run(trader.snapshot_compact())


def test_risk_status_reuses_one_performance_scorecard_for_lane_multipliers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trader = _trader(tmp_path)
    calls = 0

    def fake_scorecard(*, limit: int = 20) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"sample_count": 0, "lane_scorecards": []}

    monkeypatch.setattr(
        trader.repository,
        "latest_performance_scorecard",
        fake_scorecard,
    )

    status = trader._risk_status()

    assert status["lane_performance_multipliers"]["futures:short"] == 1.0
    assert calls == 1


def test_binance_status_reuses_feedback_performance_scorecard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trader = _trader(tmp_path)
    calls: list[int] = []

    def fake_scorecard(*, limit: int = 20) -> dict[str, Any]:
        calls.append(int(limit))
        return {
            "sample_count": 0,
            "win_rate_pct": 0.0,
            "avg_r_multiple": 0.0,
            "realized_pnl_usdt": 0.0,
            "lane_scorecards": [],
        }

    monkeypatch.setattr(
        trader.repository,
        "latest_performance_scorecard",
        fake_scorecard,
    )
    monkeypatch.setattr(
        trader,
        "_live_authority_context",
        lambda: {"status": "ok", "live_grade": "insufficient"},
    )

    trader.status()

    assert calls.count(20) == 1
    assert calls.count(trader._performance_scorecard_feedback_limit()) == 1


def test_live_authority_context_reuses_recent_successful_provider_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trader = _trader(tmp_path)
    calls = 0
    now = {"value": 10.0}

    def provider() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"status": "ok", "seq": calls}

    trader.live_authority_provider = provider
    monkeypatch.setattr(
        "tradecraft.services.binance_block_trader.time.monotonic",
        lambda: now["value"],
    )

    first = trader._live_authority_context()
    second = trader._live_authority_context()
    now["value"] += 10.0
    third = trader._live_authority_context()

    assert first["seq"] == 1
    assert second["seq"] == 1
    assert third["seq"] == 1
    assert calls == 1


def test_repository_migrates_existing_spot_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_binance_blocks.db"
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                qty_initial REAL NOT NULL,
                qty_open REAL NOT NULL DEFAULT 0,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                thesis TEXT NOT NULL DEFAULT '',
                llm_reason TEXT NOT NULL DEFAULT '',
                risk_note TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT 'llm',
                manager_run_id INTEGER,
                status TEXT NOT NULL,
                force_exit_requested INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                opened_at TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE block_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'MARKET',
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price REAL,
                source TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                error_message TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

    trader = BinanceBlockTrader(
        config=BinanceBlockTraderConfig(db_path=str(db_path)),
        adapter=_FakeBinance(),
        codex_runtime=_FakeLLM({"create_blocks": []}),
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 110.0,
            "margin_type": "isolated",
            "leverage": 1,
            "liquidation_price": 130.0,
        }
    )

    assert block["market"] == "futures"
    assert block["side"] == "short"
    assert block["leverage"] == 1


def test_manager_accepts_complete_json_actions_and_creates_paper_blocks(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "qty": 0.1,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                    "thesis": "manager paper block",
                }
            ],
            "unexpected_key": [{"ignored": True}],
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    blocks = trader.list_blocks()

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "created"
    assert len(blocks) == 1
    assert blocks[0]["status"] == "proposed"
    assert trader.repository.list_manager_runs()[0]["actions"]["create_blocks"]


def test_manager_accepts_selected_contract_payload_and_merges_candidate_plan(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "selected_contract_id": "block_action_contract",
            "payload": {
                "contract_id": "block_action_contract",
                "decision": "create_blocks",
                "symbol": "BTCUSDT",
                "market_or_account_scope": "futures",
                "horizon": "futures",
                "entry_style": "wait_for_price",
                "quantity_or_quote_budget": 20.0,
                "target_price": 95.0,
                "stop_price": 105.0,
                "confidence": 0.76,
                "source_id": "candidates:BTCUSDT",
                "claim": "BTCUSDT futures 숏 대기 블록 생성",
                "thesis": "risk-off regime short continuation",
                "risk_note": "recheck if upside reclaim persists",
                "reasons": ["후보 계산 플랜을 선택한다."],
            },
        }
    )
    trader = _trader(tmp_path, llm=llm)
    candidate = {
        "symbol": "BTCUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "futures",
        "confidence": 0.76,
        "entry_price": 101.0,
        "entry_price_usdt": 101.0,
        "target_price": 95.0,
        "stop_price": 105.0,
        "quote_budget_usdt": 20.0,
        "entry_style": "wait_for_price",
        "entry_trigger_price": 101.0,
        "entry_trigger_operator": ">=",
        "margin_type": "isolated",
        "leverage": 1,
        "liquidation_price": 150.0,
        "calculated_price_plan": {
            "entry_price": 101.0,
            "target_price": 95.0,
            "stop_price": 105.0,
            "quote_budget_usdt": 20.0,
            "entry_style": "wait_for_price",
            "entry_trigger_price": 101.0,
            "entry_trigger_operator": ">=",
            "margin_type": "isolated",
            "leverage": 1,
            "liquidation_price": 150.0,
        },
        "evidence_refs": ["candidates:BTCUSDT"],
    }

    result = asyncio.run(trader.run_manager_once(candidates=[candidate]))
    run = trader.repository.list_manager_runs()[0]
    blocks = trader.list_blocks()

    assert result["status"] == "ok"
    assert result["actions"]["create_blocks"][0]["symbol"] == "BTCUSDT"
    assert result["actions"]["create_blocks"][0]["side"] == "short"
    assert result["actions"]["create_blocks"][0]["entry_price"] == pytest.approx(101.0)
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert run["actions"]["create_blocks"][0]["metadata"]["manager_contract_decision"] == "create_blocks"
    assert blocks[0]["market"] == "futures"
    assert blocks[0]["side"] == "short"
    assert blocks[0]["entry_price"] == pytest.approx(101.0)
    assert blocks[0]["metadata"]["entry_trigger_operator"] == ">="


def test_manager_direct_create_scope_overrides_conflicting_spot_market(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "market_or_account_scope": "binance:futures",
                    "side": "long",
                    "horizon": "futures",
                    "entry_style": "wait_for_price",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                    "thesis": "Use the 1x isolated futures long lane, not spot.",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "horizon": "futures",
                    "entry_price": 100.0,
                    "target_price": 104.0,
                    "stop_price": 98.0,
                    "liquidation_price": 60.0,
                }
            ]
        )
    )
    blocks = trader.list_blocks()

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] in {"created", "waiting_entry"}
    assert len(blocks) == 1
    assert blocks[0]["market"] == "futures"
    assert blocks[0]["side"] == "long"
    assert blocks[0]["horizon"] == "futures"
    assert blocks[0]["metadata"]["market_or_account_scope"] == "binance:futures"


def test_manager_run_persists_applied_action_results(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "qty": 0.1,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                    "thesis": "manager paper block",
                }
            ],
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    run = trader.repository.list_manager_runs()[0]

    assert result["applied"]["created"][0]["status"] == "created"
    assert run["response"]["applied"]["created"][0]["status"] == "created"
    assert run["response"]["applied"]["created"][0]["block_id"]


def test_manager_response_emergency_compaction_keeps_applied_results() -> None:
    payload = {
        "status": "ok",
        "selected_contract_id": "binance_cycle_v1",
        "hold_decision": {
            "summary": "대기",
            "reasons": ["검증 게이트가 아직 probe입니다."],
        },
        "lane_review": {"selected_lanes": ["futures:short"]},
        "applied": {
            "created": [
                {
                    "status": "rejected",
                    "reason": "live_authority_restricted",
                    "block_id": "candidate-only",
                }
            ],
            "updated": [],
            "closed": [],
            "paused": [],
        },
        "large_model_trace": {
            f"candidate_{idx:03d}": {
                "symbol": "BTCUSDT",
                "reason": "x" * 120,
                "risk": "y" * 120,
            }
            for idx in range(300)
        },
    }

    compacted = compact_manager_storage_payload(
        payload,
        limit=1000,
        label="binance_manager_response",
    )

    assert compacted["_storage_compaction"]["emergency"] is True
    assert compacted["selected_contract_id"] == "binance_cycle_v1"
    assert compacted["applied"]["created"][0]["status"] == "rejected"
    assert compacted["applied"]["created"][0]["reason"] == "live_authority_restricted"
    assert compacted["lane_review"]["selected_lanes"] == ["futures:short"]


def test_manager_response_emergency_compaction_bounds_large_applied_actions() -> None:
    payload = {
        "status": "ok",
        "selected_contract_id": "binance_cycle_v1",
        "hold_decision": {"summary": "대기"},
        "applied": {
            "created": {
                "item_count": 1,
                "items": [
                    {
                        "symbol": "LINKUSDT",
                        "status": "rejected",
                        "reason": "exchange_min_order_rejected",
                        "input": {"raw": "input evidence " * 500},
                        "metadata": {"raw": "metadata evidence " * 500},
                    }
                ],
                "omitted_item_count": 0,
            },
            "create_blocks": [
                {
                    "symbol": f"ALT{index}USDT",
                    "status": "rejected",
                    "reason": "live_authority_restricted",
                    "thesis": "candidate thesis " * 400,
                    "risk_note": "risk evidence " * 400,
                }
                for index in range(80)
            ],
            "close_blocks": [],
            "pause_blocks": [],
            "update_blocks": [],
        },
    }

    compacted = compact_manager_storage_payload(
        payload,
        limit=1000,
        label="binance_manager_response",
    )

    assert prompt_chars(compacted) <= 1000
    assert compacted["_storage_compaction"]["emergency"] is True
    assert compacted["applied"]["created"]["item_count"] == 1
    assert len(compacted["applied"]["created"]["items"]) == 1
    assert compacted["applied"]["create_blocks"]["item_count"] == 80
    assert len(compacted["applied"]["create_blocks"]["items"]) <= 8
    encoded = json.dumps(compacted, ensure_ascii=False)
    assert "candidate thesis candidate thesis candidate thesis" not in encoded
    assert "risk evidence risk evidence risk evidence" not in encoded
    assert "metadata evidence metadata evidence" not in encoded


def test_spot_short_horizon_payload_keeps_horizon_lane(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "lane": "short",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "status": "proposed",
            "thesis": "short horizon spot long should not poison lane stats",
        }
    )

    assert block["metadata"]["horizon"] == "short"
    assert block["metadata"]["lane"] == "short"
    assert block["lane"] == "short"


def test_spot_short_horizon_performance_keeps_horizon_lane_and_side_card(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "lane": "short",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "status": "open",
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "buy",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "sell",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 104.0},
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 104.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    reflection = trader.block_detail(block["block_id"])["performance_reflection"]
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert result["reflection_count"] == 1
    assert reflection["lane"] == "spot:long:short"
    assert scorecard["lane_scorecards"][0]["lane"] == "spot:long:short"
    assert scorecard["side_scorecards"][0]["side"] == "spot:long"


def test_spot_short_horizon_row_lanes_do_not_create_spot_short_side_lane() -> None:
    lanes = BinanceBlockTrader._growth_governor_row_lanes(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "lane": "short",
            "validation_repair": {
                "discipline_ids": ["walk_forward_analysis"],
            },
        }
    )

    assert "spot:long" in lanes
    assert "short" in lanes
    assert "spot:long:short" in lanes
    assert "spot:long:validation:walk_forward_analysis" in lanes
    assert "spot:short" not in lanes
    assert "spot:short:validation:walk_forward_analysis" not in lanes


def test_growth_governor_uses_side_lane_for_weak_spot_long_horizon() -> None:
    growth_governor = BinanceBlockTrader._growth_governor_context(
        growth_target={"status": "behind_target", "required_daily_return_pct": 2.0},
        risk_guard={"status": "ok", "allow_new_entries": True},
        performance={
            "sample_count": 6,
            "win_rate_pct": 33.0,
            "avg_r_multiple": -0.2,
            "realized_pnl_usdt": -1.2,
            "side_scorecards": [
                {
                    "side": "spot:long",
                    "sample_count": 6,
                    "win_rate_pct": 33.0,
                    "avg_r_multiple": -0.2,
                    "pnl_usdt": -1.2,
                }
            ],
            "lane_scorecards": [
                {
                    "lane": "short",
                    "sample_count": 6,
                    "win_rate_pct": 33.0,
                    "avg_r_multiple": -0.2,
                    "pnl_usdt": -1.2,
                }
            ],
        },
    )

    assert growth_governor["mode"] == "edge_rebuild"
    assert growth_governor["weak_lanes"] == ["spot:long:short"]
    assert BinanceBlockTrader._growth_governor_applies_to_row(
        growth_governor,
        {"symbol": "BTCUSDT", "market": "spot", "side": "long", "horizon": "short"},
    )
    assert not BinanceBlockTrader._growth_governor_applies_to_row(
        growth_governor,
        {"symbol": "BTCUSDT", "market": "spot", "side": "long", "horizon": "mid"},
    )


def test_growth_governor_surfaces_positive_lane_during_global_edge_rebuild() -> None:
    growth_governor = BinanceBlockTrader._growth_governor_context(
        growth_target={"status": "behind_target", "required_daily_return_pct": 2.0},
        risk_guard={"status": "ok", "allow_new_entries": True},
        performance={
            "sample_count": 19,
            "win_rate_pct": 36.8,
            "avg_r_multiple": -0.046,
            "realized_pnl_usdt": -0.46,
            "side_scorecards": [
                {
                    "side": "futures:long",
                    "sample_count": 6,
                    "win_rate_pct": 16.7,
                    "avg_r_multiple": -0.51,
                    "pnl_usdt": -0.53,
                    "profit_factor": 0.51,
                },
                {
                    "side": "spot:long",
                    "sample_count": 6,
                    "win_rate_pct": 33.3,
                    "avg_r_multiple": 0.07,
                    "pnl_usdt": -0.38,
                    "profit_factor": 0.49,
                },
                {
                    "side": "futures:short",
                    "sample_count": 7,
                    "win_rate_pct": 57.1,
                    "avg_r_multiple": 0.248,
                    "pnl_usdt": 0.448,
                    "profit_factor": 2.99,
                },
            ],
            "lane_scorecards": [
                {
                    "lane": "futures:short",
                    "sample_count": 7,
                    "win_rate_pct": 57.1,
                    "avg_r_multiple": 0.248,
                    "pnl_usdt": 0.448,
                    "profit_factor": 2.99,
                }
            ],
        },
    )

    assert growth_governor["mode"] == "edge_rebuild"
    assert growth_governor["positive_lanes"] == ["futures:short"]
    assert growth_governor["positive_lane_count"] == 1
    assert "positive_lane_recovery" in growth_governor["reasons"]
    assert growth_governor["weak_lanes"] == ["futures:long", "spot:long"]
    assert BinanceBlockTrader._growth_governor_applies_to_row(
        growth_governor,
        {"symbol": "BTCUSDT", "market": "futures", "side": "long"},
    )
    assert not BinanceBlockTrader._growth_governor_applies_to_row(
        growth_governor,
        {"symbol": "BTCUSDT", "market": "futures", "side": "short"},
    )


def test_growth_governor_marks_recent_only_positive_lane_as_probation() -> None:
    growth_governor = BinanceBlockTrader._growth_governor_context(
        growth_target={"status": "behind_target", "required_daily_return_pct": 2.0},
        risk_guard={"status": "ok", "allow_new_entries": True},
        performance={
            "sample_count": 19,
            "win_rate_pct": 36.8,
            "avg_r_multiple": -0.046,
            "realized_pnl_usdt": -0.46,
            "side_scorecards": [
                {
                    "side": "futures:long",
                    "sample_count": 6,
                    "win_rate_pct": 16.7,
                    "avg_r_multiple": -0.51,
                    "pnl_usdt": -0.53,
                    "profit_factor": 0.51,
                },
                {
                    "side": "futures:short",
                    "sample_count": 7,
                    "win_rate_pct": 57.1,
                    "avg_r_multiple": 0.248,
                    "pnl_usdt": 0.448,
                    "profit_factor": 2.99,
                },
            ],
        },
        long_performance={
            "sample_count": 99,
            "win_rate_pct": 41.4,
            "avg_r_multiple": -0.118,
            "realized_pnl_usdt": -14.47,
            "side_scorecards": [
                {
                    "side": "futures:short",
                    "sample_count": 67,
                    "win_rate_pct": 40.3,
                    "avg_r_multiple": -0.138,
                    "pnl_usdt": -6.55,
                    "profit_factor": 0.59,
                    "recovery_factor": -0.2,
                    "max_drawdown_r_multiple": -5.0,
                }
            ],
        },
    )

    assert growth_governor["mode"] == "edge_rebuild"
    assert growth_governor["positive_lanes"] == []
    assert growth_governor["probation_lanes"] == ["futures:short"]
    assert BinanceBlockTrader._growth_governor_applies_to_row(
        growth_governor,
        {"symbol": "BTCUSDT", "market": "futures", "side": "short"},
    )


def test_legacy_blank_performance_lane_reads_as_market_side_lane(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_performance_reflection(
        {
            "block_id": "legacy-futures-short",
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "lane": "futures:short",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "stop_price": 101.0,
            "target_price": 98.0,
            "pnl_usdt": 2.0,
            "r_multiple": 2.0,
            "lesson": {},
        }
    )
    with trader.repository._connect() as conn:
        conn.execute(
            "UPDATE block_performance_reflections SET lane = '' WHERE block_id = ?",
            ("legacy-futures-short",),
        )

    reflection = trader.repository.get_performance_reflection("legacy-futures-short")
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert reflection["lane"] == "futures:short"
    assert scorecard["lane_scorecards"][0]["lane"] == "futures:short"


def test_broad_futures_performance_lane_reads_as_side_specific_lane(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_performance_reflection(
        {
            "block_id": "futures-long-loss",
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "long",
            "lane": "futures",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "stop_price": 98.0,
            "target_price": 104.0,
            "pnl_usdt": -1.0,
            "r_multiple": -1.0,
            "lesson": {},
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": "futures-short-win",
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "lane": "futures",
            "entry_price": 100.0,
            "exit_price": 96.0,
            "stop_price": 102.0,
            "target_price": 96.0,
            "pnl_usdt": 2.0,
            "r_multiple": 2.0,
            "lesson": {},
        }
    )
    with trader.repository._connect() as conn:
        conn.execute(
            "UPDATE block_performance_reflections SET lane = ? WHERE block_id IN (?, ?)",
            ("futures", "futures-long-loss", "futures-short-win"),
        )

    long_reflection = trader.repository.get_performance_reflection("futures-long-loss")
    short_reflection = trader.repository.get_performance_reflection("futures-short-win")
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert long_reflection["lane"] == "futures:long"
    assert short_reflection["lane"] == "futures:short"
    assert {
        row["lane"]: row["sample_count"]
        for row in scorecard["lane_scorecards"]
    } == {
        "futures:long": 1,
        "futures:short": 1,
    }


def test_performance_reflection_uses_block_closed_at_for_created_at(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "status": "open",
        }
    )
    closed_at = "2026-05-25T03:21:00+00:00"
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "buy",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": closed_at},
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 104.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    reflection = trader.repository.get_performance_reflection(block["block_id"])

    assert result["reflection_count"] == 1
    assert reflection["created_at"] == closed_at


def test_manager_marks_llm_error_payload_as_error(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "ok": False,
            "mode": "sdk",
            "error": "codex native runtime sdk timed out after 60.0s",
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    run = trader.repository.list_manager_runs()[0]

    assert result["status"] == "error"
    assert "timed out" in result["error_message"]
    assert run["status"] == "error"
    assert "timed out" in run["error_message"]
    assert run["actions"]["create_blocks"] == []


def test_manager_complete_uses_configured_llm_timeout(tmp_path: Path) -> None:
    llm = _FakeCompleteOnlyLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)  # type: ignore[arg-type]

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    assert result["status"] == "ok"
    assert llm.calls[0]["timeout_ms"] == 123000


def test_manager_no_action_run_records_hold_decision(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [],
            "hold_decision": {
                "summary": "관망: BTC는 추격 매수보다 눌림 확인이 우선",
                "reasons": ["성과 피드백상 즉시 진입 기대값이 낮음"],
                "watch_symbols": ["BTCUSDT", "ETHUSDT"],
                "next_triggers": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "condition": "pullback",
                        "price": 99000.0,
                        "reason": "눌림 후 재돌파 확인",
                    }
                ],
                "data_gaps": ["crypto_alpha outcome 부족"],
            },
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    run = trader.repository.list_manager_runs()[0]
    compact = asyncio.run(trader.snapshot_compact())["manager_runs"][0]

    assert result["status"] == "ok"
    assert result["hold_decision"]["summary"].startswith("관망")
    assert run["response"]["hold_decision"]["watch_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert compact["hold_decision"]["next_triggers"][0]["price"] == 99000.0


def test_manager_compact_run_includes_no_action_diagnostics(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [],
            "hold_decision": {
                "summary": "관망: 현물 후보는 패턴 근거가 부족하고 observe_only라 대기",
                "reasons": [
                    "live_authority가 observe_only라 신규 빈도를 낮춥니다.",
                    "BNBUSDT spot long 후보는 pattern_prior가 부족합니다.",
                    "futures:short 레인이 과밀하고 최근 성과가 약합니다.",
                ],
                "data_gaps": [
                    "spot 후보의 pattern prior 부족",
                    "orderbook_depth_usdt가 0입니다.",
                ],
            },
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "observe_only",
        "allow_scale_up": False,
    }

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    compact = asyncio.run(trader.snapshot_compact())["manager_runs"][0]
    full = asyncio.run(trader.snapshot())["manager_runs"][0]
    diagnostics = compact["decision_context"]["diagnostics"]

    assert diagnostics["action_count"] == 0
    assert diagnostics["blocker_tags"]["observe_only"] >= 1
    assert diagnostics["blocker_tags"]["pattern_prior_missing"] >= 1
    assert diagnostics["blocker_tags"]["lane_concentration"] >= 1
    assert diagnostics["blocker_tags"]["weak_recent_edge"] >= 1
    assert diagnostics["top_blockers"][0]["tag"] in diagnostics["blocker_tags"]
    assert full["decision_context"]["diagnostics"]["blocker_tags"]["observe_only"] >= 1


def test_manager_errors_when_runtime_has_no_completion_method(tmp_path: Path) -> None:
    trader = _trader(tmp_path, llm=_NoCompleteLLM())  # type: ignore[arg-type]

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    run = trader.repository.list_manager_runs()[0]

    assert result["status"] == "error"
    assert "completion method" in result["error_message"]
    assert run["status"] == "error"
    assert run["actions"] == {key: [] for key in sorted(binance_block_trader_module.ALLOWED_MANAGER_ACTIONS)}


def test_manager_no_action_without_hold_decision_gets_fallback_note(tmp_path: Path) -> None:
    trader = _trader(tmp_path, llm=_FakeLLM({"create_blocks": []}))

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    run = trader.repository.list_manager_runs()[0]

    assert result["hold_decision"]["summary"] == "관망: 이번 사이클에서는 실행할 블록 변화가 없습니다."
    assert run["response"]["hold_decision"]["reasons"] == [
        "이번 사이클에서는 실행할 블록 변화가 없습니다."
    ]


def test_manager_sparse_hold_decision_is_enriched_from_payload(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [],
            "hold_decision": {
                "summary": "관망: 이번 사이클에서는 실행할 블록 변화가 없습니다.",
                "reasons": ["이번 사이클에서는 실행할 블록 변화가 없습니다."],
            },
            "payload": {
                "decision": "hold_watch",
                "symbol": "BTCUSDT",
                "market_or_account_scope": "futures",
                "claim": "BTCUSDT 숏은 반등 가격 확인 전까지 대기합니다.",
                "entry_style": "wait_for_price >= 61004.36",
                "entry_price": 61004.36,
                "reasons": [
                    "주문서 스프레드는 얇지만 현재가는 추격 진입 위치가 아닙니다.",
                    "최근 futures short 성과가 약해 검증된 트리거 전까지 기다립니다.",
                ],
                "next_actions": ["BTCUSDT 61004.36 이상 반등 시 재검토"],
                "risks": ["숏 스퀴즈 위험"],
                "data_gaps": ["미체결 주문 목록 없음"],
            },
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    compact = asyncio.run(trader.snapshot_compact())["manager_runs"][0]

    assert result["actions"]["create_blocks"] == []
    assert result["hold_decision"]["summary"] == (
        "BTCUSDT 숏은 반등 가격 확인 전까지 대기합니다."
    )
    assert result["hold_decision"]["planned_actions"] == [
        "BTCUSDT 61004.36 이상 반등 시 재검토"
    ]
    assert result["hold_decision"]["next_triggers"][0]["price"] == pytest.approx(61004.36)
    assert compact["hold_decision"]["risk_notes"] == ["숏 스퀴즈 위험"]
    assert compact["decision_payload"]["claim"] == (
        "BTCUSDT 숏은 반등 가격 확인 전까지 대기합니다."
    )
    assert "decision_context" in compact


def test_binance_snapshot_includes_lane_allocation_and_history(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    short_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 96.0,
            "status": "open",
            "horizon": "short",
        }
    )
    proposed_block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "qty": 0.5,
            "entry_price": 200.0,
            "target_price": 230.0,
            "stop_price": 180.0,
            "status": "proposed",
            "horizon": "mid",
        }
    )
    trader.create_block(
        {
            "symbol": "KRW-BTC",
            "market": "upbit_spot",
            "qty": 0.001,
            "entry_price": 1_000_000.0,
            "target_price": 1_050_000.0,
            "stop_price": 980_000.0,
            "status": "open",
            "horizon": "mid",
            "quote_currency": "KRW",
        }
    )
    error_block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "qty": 0.3,
            "qty_open": 0.3,
            "entry_price": 300.0,
            "target_price": 330.0,
            "stop_price": 280.0,
            "status": "error",
            "horizon": "futures",
        }
    )
    closed_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.2,
            "entry_price": 400.0,
            "target_price": 480.0,
            "stop_price": 360.0,
            "status": "closed",
            "horizon": "long",
            "closed_at": "2026-05-25T00:10:00+00:00",
        }
    )

    snapshot = trader.snapshot_sync()
    lane_items = {
        str(row["lane"]): row
        for row in snapshot["lane_allocation"]["items"]
    }
    history_ids = {row["block_id"] for row in snapshot["block_history"]}

    assert lane_items["short"]["block_count"] == 1
    assert lane_items["short"]["value_usdt"] == pytest.approx(10.0)
    assert lane_items["mid"]["block_count"] == 1
    assert lane_items["mid"]["value_usdt"] == pytest.approx(1.0)
    assert lane_items["long"]["block_count"] == 0
    assert lane_items["long"]["value_usdt"] == pytest.approx(0.0)
    assert lane_items["futures"]["block_count"] == 0
    assert lane_items["futures"]["value_usdt"] == pytest.approx(0.0)
    assert snapshot["lane_allocation"]["total_value_usdt"] == pytest.approx(11.0)
    assert closed_block["block_id"] in history_ids
    assert error_block["block_id"] in history_ids
    assert proposed_block["block_id"] not in history_ids
    assert all(row["status"] in {"closed", "error"} for row in snapshot["block_history"])
    assert trader.get_block(short_block["block_id"])["lane"] == "short"  # type: ignore[index]


def test_binance_snapshot_history_includes_realized_performance(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    closed_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 0.25,
            "entry_price": 100.0,
            "target_price": 92.0,
            "stop_price": 104.0,
            "status": "closed",
            "closed_at": binance_block_trader_module.utc_now_iso(),
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": closed_block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "entry_price": 100.0,
            "exit_price": 96.0,
            "stop_price": 104.0,
            "target_price": 92.0,
            "pnl_usdt": 1.0,
            "r_multiple": 1.0,
            "lesson": {"result": "positive", "thesis": "failed breakout fade"},
        }
    )

    snapshot = trader.snapshot_sync()
    history_row = next(
        row for row in snapshot["block_history"] if row["block_id"] == closed_block["block_id"]
    )

    assert history_row["performance"]["pnl_usdt"] == pytest.approx(1.0)
    assert history_row["performance"]["r_multiple"] == pytest.approx(1.0)
    assert history_row["realized_pnl_usdt"] == pytest.approx(1.0)
    assert history_row["r_multiple"] == pytest.approx(1.0)
    assert snapshot["performance"]["realized_pnl_usdt"] == pytest.approx(1.0)
    assert snapshot["performance"]["gross_profit_usdt"] == pytest.approx(1.0)
    assert snapshot["performance"]["gross_loss_usdt"] == pytest.approx(0.0)
    assert snapshot["performance_today"]["realized_pnl_usdt"] == pytest.approx(1.0)
    assert snapshot["performance_today"]["window"]["kind"] == "kst_day_by_block_close"


def test_performance_reflection_prefers_filled_exit_order_price_over_claim_quote(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    closed_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "long",
            "qty": 2.0,
            "entry_price": 100.0,
            "target_price": 112.0,
            "stop_price": 95.0,
            "status": "closed",
            "closed_at": binance_block_trader_module.utc_now_iso(),
        }
    )
    trader.repository.add_event(
        closed_block["block_id"],
        "exit_claimed",
        "stop_reached",
        {"price": 90.0, "side": "sell", "qty": 2.0},
    )
    trader.repository.add_order(
        {
            "block_id": closed_block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 2.0,
            "status": "sent",
            "reason": "entry_order",
            "response": {
                "status": "FILLED",
                "executedQty": "2",
                "raw": {"avgPrice": "100.0", "executedQty": "2"},
            },
        }
    )
    trader.repository.add_order(
        {
            "block_id": closed_block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 2.0,
            "status": "sent",
            "reason": "stop_reached",
            "response": {
                "status": "FILLED",
                "executed_qty": "2",
                "raw": {"avgPrice": "96.0", "executedQty": "2"},
            },
        }
    )

    reflection = trader._build_performance_reflection(  # noqa: SLF001
        trader.get_block(closed_block["block_id"]) or closed_block
    )

    assert reflection is not None
    assert reflection["exit_price"] == pytest.approx(96.0)
    assert reflection["gross_pnl_usdt"] == pytest.approx(-8.0)
    assert reflection["r_multiple"] == pytest.approx(-0.8)


def test_today_performance_uses_close_date_and_excludes_wallet_adoption(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    old_block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "long",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "status": "closed",
            "closed_at": "2026-05-24T00:10:00+00:00",
        }
    )
    wallet_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.01,
            "entry_price": 60000.0,
            "target_price": 68000.0,
            "stop_price": 58000.0,
            "status": "closed",
            "created_by": "wallet_adoption",
            "closed_at": binance_block_trader_module.utc_now_iso(),
        }
    )
    live_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 0.1,
            "entry_price": 600.0,
            "target_price": 580.0,
            "stop_price": 610.0,
            "status": "closed",
            "created_by": "llm",
            "closed_at": binance_block_trader_module.utc_now_iso(),
        }
    )
    for block, pnl in ((old_block, 9.0), (wallet_block, 25.0), (live_block, -0.3)):
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": block["market"],
                "side": block["side"],
                "entry_price": block["entry_price"],
                "exit_price": block["target_price"],
                "stop_price": block["stop_price"],
                "target_price": block["target_price"],
                "pnl_usdt": pnl,
                "r_multiple": 1.0 if pnl > 0 else -1.0,
            }
        )

    today = trader.repository.today_performance_scorecard()
    latest = trader.repository.latest_performance_scorecard(limit=10)

    assert today["sample_count"] == 1
    assert today["realized_pnl_usdt"] == pytest.approx(-0.3)
    assert today["gross_profit_usdt"] == pytest.approx(0.0)
    assert today["gross_loss_usdt"] == pytest.approx(-0.3)
    assert latest["realized_pnl_usdt"] == pytest.approx(8.7)
    assert latest["window"]["kind"] == "latest_by_block_close"


def test_live_spot_manager_create_submits_entry_and_opens_block(tmp_path: Path) -> None:
    adapter = _FillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    block = trader.list_blocks()[0]

    assert result["applied"]["created"][0]["status"] == "opened"
    assert block["status"] == "open"
    assert block["qty_open"] == pytest.approx(0.1)
    assert adapter.spot_orders[0]["side"] == "buy"
    assert adapter.spot_orders[0]["limit_price"] > 100.0


def test_live_spot_entry_rebases_target_stop_to_actual_better_fill(
    tmp_path: Path,
) -> None:
    adapter = _AvgPriceFillingEntryBinance(avg_price=97.0)
    trader = _trader(
        tmp_path,
        adapter=adapter,
        enabled=True,
        execute_spot=True,
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "status": "proposed",
            "metadata": {"calculated_price_plan": {"risk_pct": 2.0, "target_pct": 4.0}},
        }
    )

    result = asyncio.run(trader._submit_entry_for_block(block))
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "opened"
    assert updated is not None
    assert updated["entry_price"] == pytest.approx(97.0)
    assert updated["stop_price"] == pytest.approx(95.06)
    assert updated["target_price"] == pytest.approx(100.88)
    assert updated["metadata"]["entry_fill_price_rebase"]["rebased"] is True
    assert updated["metadata"]["entry_fill_price_rebase"]["old_structure_status"] == (
        "invalid_price_structure"
    )
    assert updated["stop_price"] < updated["entry_price"] < updated["target_price"]


def test_live_futures_short_entry_rebases_target_stop_to_actual_higher_fill(
    tmp_path: Path,
) -> None:
    adapter = _AvgPriceFillingEntryBinance(avg_price=105.0)
    trader = _trader(
        tmp_path,
        adapter=adapter,
        enabled=True,
        execute_futures=True,
    )
    block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 96.0,
            "stop_price": 102.0,
            "liquidation_price": 150.0,
            "status": "proposed",
            "metadata": {"calculated_price_plan": {"risk_pct": 2.0, "target_pct": 4.0}},
        }
    )

    result = asyncio.run(trader._submit_entry_for_block(block))
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "opened"
    assert updated is not None
    assert updated["entry_price"] == pytest.approx(105.0)
    assert updated["stop_price"] == pytest.approx(107.1)
    assert updated["target_price"] == pytest.approx(100.8)
    assert updated["metadata"]["entry_fill_price_rebase"]["rebased"] is True
    assert updated["metadata"]["entry_fill_price_rebase"]["old_structure_status"] == (
        "invalid_price_structure"
    )
    assert updated["target_price"] < updated["entry_price"] < updated["stop_price"]


def test_live_spot_entry_uses_best_ask_for_immediate_limit(tmp_path: Path) -> None:
    adapter = _BookTickerFillingEntryBinance()
    adapter.book_tickers[("spot", "BTCUSDT")] = {"bid": 99.9, "ask": 100.1}
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    block = trader.list_blocks()[0]

    assert result["applied"]["created"][0]["status"] == "opened"
    assert block["status"] == "open"
    assert adapter.spot_orders[0]["limit_price"] == pytest.approx(100.3002)
    assert trader.repository.list_orders()[0]["order_type"] == "LIMIT_IOC"


def test_live_spot_entry_waits_when_best_ask_exceeds_entry_tolerance(
    tmp_path: Path,
) -> None:
    adapter = _BookTickerFillingEntryBinance()
    adapter.book_tickers[("spot", "BTCUSDT")] = {"bid": 100.8, "ask": 101.0}
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    block = trader.list_blocks()[0]

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert adapter.spot_orders == []
    assert block["status"] == "proposed"
    assert block["qty_open"] == pytest.approx(0.0)
    assert block["metadata"]["entry_style"] == "wait_for_price"
    assert block["metadata"]["entry_trigger_operator"] == "<="
    assert block["metadata"]["entry_trigger_price"] == pytest.approx(100.2)


def test_executor_tick_triggers_waiting_spot_entry_without_llm(
    tmp_path: Path,
) -> None:
    adapter = _BookTickerFillingEntryBinance()
    adapter.book_tickers[("spot", "BTCUSDT")] = {"bid": 99.8, "ask": 100.0}
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "proposed",
            "metadata": {
                "entry_style": "wait_for_price",
                "entry_trigger_operator": "<=",
                "entry_trigger_price": 100.0,
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "opened"
    assert llm.calls == []
    assert len(adapter.spot_orders) == 1
    assert adapter.spot_orders[0]["limit_price"] == pytest.approx(100.2)
    assert updated is not None
    assert updated["status"] == "open"
    assert adapter.book_calls


def test_waiting_futures_entry_rejects_exchange_minimum_before_order(
    tmp_path: Path,
) -> None:
    adapter = _FilteredFillingEntryBinance()
    adapter.prices["LTCUSDT"] = 45.7
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )
    block = trader.repository.create_block(
        {
            "block_id": "legacy_ltc_waiting_min_notional",
            "symbol": "LTCUSDT",
            "market": "futures",
            "side": "short",
            "qty_initial": 0.21,
            "qty_open": 0.0,
            "entry_price": 45.62,
            "target_price": 42.0,
            "stop_price": 49.0,
            "thesis": "legacy under-minimum waiting entry",
            "llm_reason": "",
            "risk_note": "",
            "status": "proposed",
            "margin_type": "isolated",
            "leverage": 2,
            "liquidation_price": 70.0,
            "metadata": {
                "entry_style": "wait_for_price",
                "entry_trigger_operator": ">=",
                "entry_trigger_price": 45.62,
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "ENTRY_PREFLIGHT_BLOCKED"
    assert "order notional below minimum" in result["actions"][0]["reason"]
    assert adapter.futures_orders == []
    assert trader.repository.list_orders(block_id=block["block_id"]) == []
    assert updated is not None
    assert updated["status"] == "error"
    assert "exchange_min_order_rejected" in updated["risk_note"]
    assert events[0]["event_type"] == "entry_preflight_blocked"
    assert events[0]["payload"]["stage"] == "exchange_min_order"


def test_waiting_entry_does_not_submit_when_book_preflight_is_stale(
    tmp_path: Path,
) -> None:
    adapter = _BookTickerFillingEntryBinance()
    adapter.book_tickers[("spot", "BTCUSDT")] = {"bid": 0.0, "ask": 100.0}
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "proposed",
            "metadata": {
                "entry_style": "wait_for_price",
                "entry_trigger_operator": "<=",
                "entry_trigger_price": 100.0,
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "blocked"
    assert adapter.spot_orders == []
    assert trader.repository.list_orders(block_id=block["block_id"]) == []
    assert updated is not None
    assert updated["status"] == "paused"
    assert "preflight_book_invalid" in updated["risk_note"]
    assert events[0]["event_type"] == "entry_preflight_blocked"
    assert events[0]["payload"]["reason"] == "preflight_book_invalid"
    assert events[0]["payload"]["book"]["bid"] == pytest.approx(0.0)


def test_waiting_entry_does_not_submit_when_book_preflight_spread_is_wide(
    tmp_path: Path,
) -> None:
    adapter = _BookTickerFillingEntryBinance()
    adapter.book_tickers[("spot", "BTCUSDT")] = {"bid": 99.0, "ask": 100.0}
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "proposed",
            "metadata": {
                "entry_style": "wait_for_price",
                "entry_trigger_operator": "<=",
                "entry_trigger_price": 100.0,
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "blocked"
    assert adapter.spot_orders == []
    assert trader.repository.list_orders(block_id=block["block_id"]) == []
    assert updated is not None
    assert updated["status"] == "paused"
    assert "preflight_spread_too_wide" in updated["risk_note"]
    assert events[0]["event_type"] == "entry_preflight_blocked"
    assert events[0]["payload"]["reason"] == "preflight_spread_too_wide"
    assert events[0]["payload"]["spread_bps"] > events[0]["payload"]["max_spread_bps"]


def test_waiting_entry_does_not_submit_when_final_order_book_preflight_turns_bad(
    tmp_path: Path,
) -> None:
    adapter = _SequencedBookTickerFillingEntryBinance()
    adapter.book_sequences[("spot", "BTCUSDT")] = [
        {"bid": 99.8, "ask": 100.0},
        {"bid": 99.8, "ask": 100.0},
        {"bid": 0.0, "ask": 100.0},
    ]
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "proposed",
            "metadata": {
                "entry_style": "wait_for_price",
                "entry_trigger_operator": "<=",
                "entry_trigger_price": 100.0,
            },
        }
    )

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block_id=block["block_id"], limit=10)

    assert result["action_count"] == 1
    assert result["actions"][0]["status"] == "blocked"
    assert result["actions"][0]["reason"] == "preflight_book_invalid"
    assert adapter.book_calls == [("spot", "BTCUSDT")] * 3
    assert adapter.spot_orders == []
    assert trader.repository.list_orders(block_id=block["block_id"]) == []
    assert updated is not None
    assert updated["status"] == "paused"
    assert "preflight_book_invalid" in updated["risk_note"]
    assert events[0]["event_type"] == "entry_preflight_blocked"
    assert events[0]["payload"]["book"]["bid"] == pytest.approx(0.0)


def test_live_spot_entry_ioc_expiry_returns_to_waiting_entry(tmp_path: Path) -> None:
    adapter = _ExpiringEntryBookTickerBinance()
    adapter.book_tickers[("spot", "BTCUSDT")] = {"bid": 99.9, "ask": 100.1}
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "qty": 0.1,
                    "entry_price": 100.0,
                    "target_price": 120.0,
                    "stop_price": 90.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_spot=True,
    )

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    block = trader.list_blocks()[0]

    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert len(adapter.spot_orders) == 1
    assert block["status"] == "proposed"
    assert block["qty_open"] == pytest.approx(0.0)
    assert block["metadata"]["entry_trigger_status"] == "waiting"
    assert "UNFILLED_IOC_QUANTITY_EXPIRED" in block["risk_note"]


def test_live_futures_manager_create_sets_risk_and_submits_short_entry(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "qty": 0.01,
                    "entry_price": 100.0,
                    "target_price": 90.0,
                    "stop_price": 105.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 130.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    block = trader.list_blocks()[0]

    assert result["applied"]["created"][0]["status"] == "opened"
    assert block["status"] == "open"
    assert block["market"] == "futures"
    assert block["side"] == "short"
    assert adapter.margin_calls == [{"symbol": "BTCUSDT", "margin_type": "isolated"}]
    assert adapter.leverage_calls == [{"symbol": "BTCUSDT", "leverage": 2}]
    assert adapter.futures_orders[0]["side"] == "sell"
    assert adapter.futures_orders[0]["reduce_only"] is False
    assert adapter.futures_orders[0]["limit_price"] < 100.0


def test_live_futures_entry_quantizes_to_exchange_filters(tmp_path: Path) -> None:
    adapter = _FilteredFillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "short",
                    "qty": 0.12368,
                    "entry_price": 593.472846,
                    "target_price": 570.0,
                    "stop_price": 615.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 800.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **kwargs: {
                "status": "ok",
                "candidates": [{"symbol": "BNBUSDT", "market": "futures"}],
            }
        },
    )()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    order = adapter.futures_orders[0]

    assert result["applied"]["created"][0]["status"] == "opened"
    assert order["quantity"] == pytest.approx(0.12)
    assert order["limit_price"] == pytest.approx(592.28)
    assert trader.list_blocks()[0]["qty_open"] == pytest.approx(0.12)


def test_live_futures_entry_rejects_quantity_below_exchange_minimum(
    tmp_path: Path,
) -> None:
    adapter = _FilteredFillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "short",
                    "qty": 0.001,
                    "entry_price": 593.47,
                    "target_price": 570.0,
                    "stop_price": 615.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 800.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **kwargs: {
                "status": "ok",
                "candidates": [{"symbol": "BNBUSDT", "market": "futures"}],
            }
        },
    )()

    result = asyncio.run(trader.run_manager_once(candidates=[]))

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert "exchange_min_order_rejected" in result["applied"]["created"][0]["reason"]
    assert "below min quantity" in result["applied"]["created"][0]["reason"]
    assert adapter.futures_orders == []
    assert trader.list_blocks(include_closed=True) == []


def test_live_futures_entry_rejects_notional_below_exchange_minimum_before_block_create(
    tmp_path: Path,
) -> None:
    adapter = _FilteredFillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "LTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "qty": 0.21,
                    "entry_price": 45.62,
                    "target_price": 42.0,
                    "stop_price": 49.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 70.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **kwargs: {
                "status": "ok",
                "candidates": [{"symbol": "LTCUSDT", "market": "futures"}],
            }
        },
    )()

    result = asyncio.run(trader.run_manager_once(candidates=[]))

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert "exchange_min_order_rejected" in result["applied"]["created"][0]["reason"]
    assert "order notional below minimum" in result["applied"]["created"][0]["reason"]
    assert adapter.futures_orders == []
    assert trader.list_blocks(include_closed=True) == []


def test_live_futures_entry_bumps_quantity_when_quote_budget_rounds_below_minimum(
    tmp_path: Path,
) -> None:
    adapter = _FilteredFillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "LINKUSDT",
                    "market": "futures",
                    "side": "short",
                    "quote_budget_usdt": 20.0,
                    "entry_price": 7.9362,
                    "target_price": 7.8092,
                    "stop_price": 7.9997,
                    "margin_type": "isolated",
                    "leverage": 1,
                    "liquidation_price": 10.714,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **kwargs: {
                "status": "ok",
                "candidates": [{"symbol": "LINKUSDT", "market": "futures"}],
            }
        },
    )()

    result = asyncio.run(
        trader.run_manager_once(candidates=[{"symbol": "LINKUSDT", "market": "futures"}])
    )
    order = adapter.futures_orders[0]
    block = trader.list_blocks()[0]

    assert result["applied"]["created"][0]["status"] == "opened"
    assert order["quantity"] == pytest.approx(2.53)
    assert order["quantity"] * order["limit_price"] >= 20.0
    assert block["qty_open"] == pytest.approx(order["quantity"])


def test_live_futures_entry_rejects_quote_budget_far_below_exchange_minimum(
    tmp_path: Path,
) -> None:
    adapter = _FilteredFillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "LINKUSDT",
                    "market": "futures",
                    "side": "short",
                    "quote_budget_usdt": 5.0,
                    "entry_price": 7.9362,
                    "target_price": 7.8092,
                    "stop_price": 7.9997,
                    "margin_type": "isolated",
                    "leverage": 1,
                    "liquidation_price": 10.714,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )
    trader.crypto_research_provider = type(
        "CryptoResearch",
        (),
        {
            "latest_context": lambda self, **kwargs: {
                "status": "ok",
                "candidates": [{"symbol": "LINKUSDT", "market": "futures"}],
            }
        },
    )()

    result = asyncio.run(
        trader.run_manager_once(candidates=[{"symbol": "LINKUSDT", "market": "futures"}])
    )

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert "exchange_min_order_rejected" in result["applied"]["created"][0]["reason"]
    assert "order notional below minimum" in result["applied"]["created"][0]["reason"]
    assert adapter.futures_orders == []
    assert trader.list_blocks(include_closed=True) == []


def test_futures_create_rejects_symbol_outside_futures_universe(
    tmp_path: Path,
) -> None:
    adapter = _FillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "short",
                    "qty": 0.01,
                    "entry_price": 600.0,
                    "target_price": 570.0,
                    "stop_price": 615.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 800.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BNBUSDT"}]))

    assert result["applied"]["created"][0]["status"] == "rejected"
    assert "futures_symbol_not_in_universe:BNBUSDT" in result["applied"]["created"][0][
        "reason"
    ]
    assert trader.list_blocks() == []
    assert adapter.futures_orders == []


def test_futures_create_allows_dynamic_crypto_research_candidate(
    tmp_path: Path,
) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "requested_symbols": symbols or [],
                "limit": limit,
                "items": [
                    {
                        "symbol": "BNBUSDT",
                        "features": {"derivatives_status": "available", "score": 80},
                    }
                ],
                "candidates": [
                    {
                        "symbol": "BNBUSDT",
                        "market": "futures",
                        "stance": "short_watch",
                        "horizon": "intraday",
                        "score": 80,
                    }
                ],
            }

    adapter = _FillingEntryBinance()
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "short",
                    "qty": 0.01,
                    "entry_price": 600.0,
                    "target_price": 570.0,
                    "stop_price": 615.0,
                    "margin_type": "isolated",
                    "leverage": 2,
                    "liquidation_price": 800.0,
                }
            ]
        }
    )
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=llm,
        enabled=True,
        execute_futures=True,
    )
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert "BNBUSDT" in prompt["market_universe"]["futures"]
    assert result["applied"]["created"][0]["status"] == "opened"
    assert trader.list_blocks()[0]["symbol"] == "BNBUSDT"
    assert adapter.futures_orders


def test_manager_created_block_requires_risk_prices_when_sizer_present(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "qty": 0.1,
                    "thesis": "missing risk prices",
                }
            ],
        }
    )
    trader = _trader(tmp_path, llm=llm)

    class RejectingRiskSizer:
        config = object()

        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("risk sizer should not run without prices")

    trader.risk_sizer = RejectingRiskSizer()

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "rejected"
    assert "risk_sizer_requires_entry_target_stop" in result["applied"]["created"][0]["reason"]
    assert trader.list_blocks() == []


def test_manager_update_cannot_mutate_execution_state(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": [], "update_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.5,
            "qty_open": 0.5,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    llm.payload = {
        "create_blocks": [],
        "update_blocks": [
            {
                "block_id": block["block_id"],
                "status": "closed",
                "qty_open": 0,
                "force_exit_requested": 0,
                "target_price": 125.0,
            }
        ],
    }

    result = asyncio.run(trader.run_manager_once())
    updated = trader.get_block(block["block_id"])

    assert result["applied"]["updated"][0]["status"] == "updated"
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["qty_open"] == pytest.approx(0.5)
    assert updated["force_exit_requested"] is False
    assert updated["target_price"] == pytest.approx(125.0)


def test_manager_requests_binance_scoped_memory(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def memory_provider(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "ok",
            "scoped_memory": {
                "status": "ok",
                "target_scope": kwargs.get("target_scope"),
                "local": [{"key": "BTCUSDT", "summary_md": "local crypto memory"}],
            },
        }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm, memory_provider=memory_provider)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert calls[0]["target_scope"] == "binance"
    assert calls[0]["source_scope"] == "binance"
    assert prompt["memory"]["scoped_memory"]["target_scope"] == "binance"
    assert "translated KIS memories" in prompt["policy"]["memory_scope_policy"]


def test_manager_candidates_include_binance_symbol_memory_hint(tmp_path: Path) -> None:
    def memory_provider(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "symbols": kwargs.get("symbols", []),
            "symbol_analyses": {
                "BTCUSDT": [
                    {
                        "stance": "spot_pullback_watch",
                        "confidence": 0.76,
                        "summary": "BTC는 Binance 전용 분석에서 급등 추격보다 눌림 대기 우선.",
                        "risks": ["펀딩 과열 구간의 급등 추격 손실"],
                        "data_gaps": ["펀딩과 오더북 두께 재확인"],
                    }
                ]
            },
            "scoped_memory": {
                "status": "ok",
                "target_scope": "binance",
                "local": [
                    {
                        "memory_type": "symbol_note",
                        "key": "BTCUSDT",
                        "summary_md": "BTCUSDT local memory: spot long only after pullback reclaim.",
                        "confidence": 0.81,
                    }
                ],
                "translated": [
                    {
                        "memory_type": "translated_kis_lesson",
                        "key": "BTCUSDT",
                        "summary_md": "translated lesson should remain explicitly marked.",
                        "confidence": 0.61,
                    }
                ],
            },
        }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm, memory_provider=memory_provider)

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "score": 82.0,
                    "confidence": 0.73,
                    "entry_price": 100000.0,
                    "target_price": 106000.0,
                    "stop_price": 97000.0,
                }
            ]
        )
    )
    prompt = llm.calls[0]["payload"]
    btc = next(row for row in prompt["candidates"] if row["symbol"] == "BTCUSDT")

    assert result["status"] == "ok"
    assert btc["memory_hint"]["status"] == "available"
    assert "symbol_analysis_memory" in btc["memory_hint"]["sources"]
    assert "scoped_local_memory" in btc["memory_hint"]["sources"]
    assert "scoped_translated_memory" in btc["memory_hint"]["sources"]
    assert "눌림 대기" in " ".join(btc["memory_hint"]["reasons"])
    assert "급등 추격 손실" in " ".join(btc["memory_hint"]["risks"])
    assert "펀딩과 오더북" in " ".join(btc["memory_hint"]["checks"])
    assert "candidate_memory_hint_policy" in prompt["decision_inputs"]
    assert prompt["candidate_memory_hint_policy"]["required"] is True
    assert prompt["candidate_memory_hint_policy"]["action_contract"] == (
        "cite_or_reject_candidate_memory_hint"
    )


def test_manager_prompt_includes_crypto_research_context(tmp_path: Path) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "symbols": symbols or [],
                "limit": limit,
                "items": [{"symbol": "BTCUSDT", "features": {"trend_1m": "up"}}],
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "score": 78,
                    }
                ],
                "symbol_notes": {"BTCUSDT": {"summary_md": "BTC research note"}},
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert "crypto_research" not in prompt
    assert prompt["raw_context_refs"]["crypto_research"]["candidate_count"] == 1
    assert "BTCUSDT" in prompt["raw_context_refs"]["crypto_research"]["requested_symbols"]
    assert "BTCUSDT" in prompt["market_universe"]["spot"]
    assert "decision_packet" in prompt["decision_inputs"]
    assert "crypto_research" not in prompt["decision_inputs"]


def test_manager_prompt_includes_crypto_market_pulse(tmp_path: Path) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            _ = (symbols, limit)
            return {
                "status": "ok",
                "regime": "btc_led_risk_on",
                "regime_brief": {
                    "version": "crypto_regime_brief_v1",
                    "status": "ok",
                    "regime": "btc_led_risk_on",
                    "market_direction": "btc_led_risk_on",
                    "risk_posture": "constructive",
                    "breadth": {
                        "bearish_pct": 10.0,
                        "bullish_pct": 60.0,
                        "neutral_pct": 30.0,
                        "symbol_count": 3,
                    },
                    "lane_bias": {
                        "spot_long": "favored_on_pullback_or_breakout",
                        "futures_long": "allowed_with_liquidity_and_spread_confirmation",
                        "futures_short": "hedge_or_exhaustion_only",
                    },
                    "horizon_bias": {
                        "short": "long_momentum_with_spread_control",
                        "mid": "spot_or_swing_candidates_allowed",
                        "long": "core_candidates_allowed_if_external_context_agrees",
                    },
                    "rotation_notes": ["breadth_supports_long_rotation"],
                    "external_notes": [
                        {
                            "source_id": "fear_greed",
                            "key": "BTC",
                            "headline": "fear_greed=65 Greed",
                        }
                    ],
                    "derivatives_notes": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": 0.0002,
                            "squeeze_risk": "normal",
                        }
                    ],
                    "operator_summary_ko": "현재 크립토 레짐은 상승 우위입니다.",
                    "data_gaps": [],
                },
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "change_pct_24h": 2.4,
                            "spread_bps": 1.1,
                            "entry_quality": "actionable_now",
                        },
                    },
                    {
                        "symbol": "ETHUSDT",
                        "features": {
                            "change_pct_24h": 1.2,
                            "spread_bps": 1.4,
                            "entry_quality": "conditional",
                        },
                    },
                    {
                        "symbol": "SOLUSDT",
                        "features": {
                            "change_pct_24h": -0.6,
                            "spread_bps": 2.2,
                            "entry_quality": "wait_pullback",
                        },
                    },
                ],
                "candidates": [
                    {"symbol": "BTCUSDT", "market": "spot", "stance": "long_watch"},
                    {"symbol": "ETHUSDT", "market": "futures", "stance": "short_watch"},
                    {"symbol": "SOLUSDT", "market": "spot", "stance": "hold"},
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["crypto_market_pulse"]["status"] == "ok"
    assert prompt["crypto_market_pulse"]["regime"] == "btc_led_risk_on"
    assert (
        prompt["crypto_market_pulse"]["regime_brief"]["version"]
        == "crypto_regime_brief_v1"
    )
    assert prompt["crypto_market_pulse"]["market_direction"] == "btc_led_risk_on"
    assert prompt["crypto_market_pulse"]["risk_posture"] == "constructive"
    assert (
        prompt["crypto_market_pulse"]["lane_bias"]["spot_long"]
        == "favored_on_pullback_or_breakout"
    )
    assert prompt["crypto_market_pulse"]["external_notes"][0]["source_id"] == "fear_greed"
    assert prompt["crypto_market_pulse"]["derivatives_notes"][0]["symbol"] == "BTCUSDT"
    assert "상승 우위" in prompt["crypto_market_pulse"]["operator_summary_ko"]
    assert prompt["crypto_market_pulse"]["major_count"] == 3
    assert prompt["crypto_market_pulse"]["major_rows"] == [
        {
            "symbol": "BTCUSDT",
            "change_pct_24h": 2.4,
            "spread_bps": 1.1,
            "entry_quality": "actionable_now",
        },
        {
            "symbol": "ETHUSDT",
            "change_pct_24h": 1.2,
            "spread_bps": 1.4,
            "entry_quality": "conditional",
        },
        {
            "symbol": "SOLUSDT",
            "change_pct_24h": -0.6,
            "spread_bps": 2.2,
            "entry_quality": "wait_pullback",
        },
    ]
    assert prompt["crypto_market_pulse"]["avg_major_change_pct_24h"] == pytest.approx(1.0)
    assert prompt["crypto_market_pulse"]["avg_spread_bps"] == pytest.approx(1.5667)
    assert prompt["crypto_market_pulse"]["candidate_count"] == 3
    assert prompt["crypto_market_pulse"]["long_candidate_count"] == 1
    assert prompt["crypto_market_pulse"]["short_candidate_count"] == 1
    assert prompt["crypto_market_pulse"]["hold_candidate_count"] == 1
    assert "crypto_market_pulse" in prompt["decision_inputs"]


def test_crypto_market_pulse_uses_live_book_spread(tmp_path: Path) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            assert symbol == "BTCUSDT"
            assert market == "spot"
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 100.0,
                "ask_price": 100.1,
                "spread_bps": 9.99,
                "source": "pulse_live_book",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            _ = (symbols, limit)
            return {
                "status": "ok",
                "regime": "btc_led_risk_on",
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "change_pct_24h": 2.4,
                            "spread_bps": 88.8,
                            "entry_quality": "actionable_now",
                        },
                    }
                ],
                "candidates": [
                    {"symbol": "BTCUSDT", "market": "spot", "stance": "long_watch"},
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["crypto_market_pulse"]["major_rows"][0]["spread_bps"] == pytest.approx(9.99)
    assert prompt["crypto_market_pulse"]["avg_spread_bps"] == pytest.approx(9.99)


def test_spot_waiting_entry_uses_independent_exploration_gate(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader._last_manager_entry_gate_policy = {
        "version": "binance_entry_gate_v1",
        "adjustment": "tightened_by_recent_reflections",
        "base_min_confidence": 0.58,
        "base_min_expected_r": 0.55,
        "base_min_directional_score": 62.0,
        "min_confidence": 0.63,
        "min_expected_r": 0.60,
        "min_directional_score": 67.0,
        "lane_scorecards": {
            "spot:long": {
                "sample_count": 7,
                "win_rate_pct": 42.8,
                "avg_r_multiple": -0.02,
                "pnl_usdt": -7.4,
            },
            "futures:short": {
                "sample_count": 20,
                "win_rate_pct": 40.0,
                "avg_r_multiple": -0.08,
                "pnl_usdt": -1.2,
            },
        },
    }
    trader._last_manager_candidate_index = {
        ("XAUTUSDT", "spot", "long", "short"): {
            "symbol": "XAUTUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "stance": "long_watch",
            "confidence": 0.66,
        },
    }
    trader._last_manager_quant_context = {
        "items": [
            {
                "symbol": "XAUTUSDT",
                "horizon": "intraday",
                "long_score": 58.0,
                "short_score": 53.0,
                "no_trade_score": 50.0,
                "expected_r_long": 0.42,
                "expected_r_short": 0.10,
            }
        ]
    }

    gate = trader._manager_entry_gate(
        {
            "symbol": "XAUTUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "entry_style": "wait_for_price",
            "confidence": 0.66,
            "evidence_refs": ["research:XAUTUSDT"],
        }
    )

    assert gate["ok"] is True
    assert gate["effective_policy"]["effective_adjustment"] == "spot_waiting_entry_exploration"
    assert gate["effective_policy"]["min_expected_r"] == pytest.approx(0.40)
    assert gate["effective_policy"]["min_directional_score"] == pytest.approx(55.0)
    assert gate["effective_policy"]["min_direction_margin"] == pytest.approx(4.0)


def test_spot_exploration_gate_does_not_relax_futures(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader._last_manager_entry_gate_policy = {
        "version": "binance_entry_gate_v1",
        "adjustment": "tightened_by_recent_reflections",
        "base_min_confidence": 0.58,
        "base_min_expected_r": 0.55,
        "base_min_directional_score": 62.0,
        "min_confidence": 0.63,
        "min_expected_r": 0.60,
        "min_directional_score": 67.0,
        "lane_scorecards": {},
    }
    trader._last_manager_candidate_index = {
        ("ETHUSDT", "futures", "short", "futures"): {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "stance": "short_watch",
            "confidence": 0.66,
        },
    }
    trader._last_manager_quant_context = {
        "items": [
            {
                "symbol": "ETHUSDT",
                "horizon": "futures",
                "long_score": 53.0,
                "short_score": 58.0,
                "no_trade_score": 50.0,
                "expected_r_long": 0.10,
                "expected_r_short": 0.42,
            }
        ]
    }

    gate = trader._manager_entry_gate(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "entry_style": "wait_for_price",
            "confidence": 0.66,
            "evidence_refs": ["research:ETHUSDT"],
        }
    )

    assert gate["ok"] is False
    assert "quant_expected_r_too_low" in gate["reasons"]
    assert "quant_directional_score_too_low" in gate["reasons"]
    assert "quant_direction_margin_too_thin" in gate["reasons"]


def test_spot_long_observe_only_becomes_waiting_entry_price_plan(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    plan = trader._design_crypto_candidate_price_plan(
        candidate={"symbol": "BNBUSDT", "entry_quality": "actionable_now"},
        features={
            "symbol": "BNBUSDT",
            "price": 600.0,
            "bid_price": 599.8,
            "ask_price": 600.2,
            "spread_bps": 6.0,
            "book_fresh": True,
            "change_pct_24h": 2.0,
            "entry_quality": "actionable_now",
        },
        market="spot",
        side="long",
        horizon="short",
        account={"spot_cash_usdt": 1000.0},
        pattern_prior={},
        live_authority={
            "status": "ok",
            "live_grade": "observe_only",
            "allow_scale_up": False,
            "max_budget_multiplier": 0.5,
        },
    )

    assert plan["entry_style"] == "wait_for_price"
    assert plan["raw_entry_quality"] == "wait_for_live_confluence"
    assert plan["entry_trigger_operator"] == "<="
    assert plan["entry_price"] < 600.0


def test_validation_probe_forces_futures_waiting_entry_price_plan(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    plan = trader._design_crypto_candidate_price_plan(
        candidate={"symbol": "BTCUSDT", "entry_quality": "actionable_now"},
        features={
            "symbol": "BTCUSDT",
            "price": 100.0,
            "bid_price": 99.95,
            "ask_price": 100.05,
            "spread_bps": 10.0,
            "book_fresh": True,
            "change_pct_24h": -2.0,
            "derivatives_status": "available",
            "funding_rate": 0.0001,
        },
        market="futures",
        side="short",
        horizon="futures",
        account={"futures_cash_usdt": 500.0},
        pattern_prior={
            "trade_count": 24,
            "win_rate": 0.58,
            "expectancy_r": 0.31,
            "profit_factor": 1.42,
            "objective_score": 88.0,
            "parameter_set": {"stop_pct": 0.012, "target_pct": 0.026},
        },
        live_authority={
            "status": "ok",
            "live_grade": "scale_candidate",
            "allow_scale_up": False,
            "max_budget_multiplier": 1.0,
            "scorecard_count": 12,
            "validation_gate": {
                "status": "validation_probe",
                "readiness": "probe",
                "reason": "validation_readiness_probe_not_scale_ready",
            },
        },
    )

    crosscheck = plan["pattern_live_crosscheck"]
    assert plan["entry_style"] == "wait_for_price"
    assert plan["raw_entry_quality"] == "wait_for_live_confluence"
    assert crosscheck["status"] == "wait"
    assert "live_authority_validation_probe" in crosscheck["wait_reasons"]
    assert crosscheck["live_authority"]["validation_gate_status"] == "validation_probe"
    assert crosscheck["live_authority"]["validation_readiness"] == "probe"


def test_candidate_price_plan_uses_matching_futures_side_performance_budget(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.futures_quote_budget_pct = 10.0
    trader.config.futures_min_quote_budget_usdt = 25.0
    trader.config.futures_max_quote_budget_usdt = 150.0
    trader.config.budget_performance_scale_enabled = True
    trader.config.budget_performance_scale_min_samples = 3
    trader.config.budget_performance_scale_win_rate_pct = 60.0
    trader.config.budget_performance_scale_multiplier = 1.5
    for index in range(1, 4):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"side-long-win-{index}",
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 103.0,
                "stop_price": 98.0,
                "target_price": 106.0,
                "pnl_usdt": 0.4,
                "r_multiple": 1.0,
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": f"side-short-loss-{index}",
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "stop_price": 102.0,
                "target_price": 96.0,
                "pnl_usdt": -0.2,
                "r_multiple": -1.0,
            }
        )
    features = {
        "symbol": "BTCUSDT",
        "price": 100.0,
        "bid_price": 99.95,
        "ask_price": 100.05,
        "spread_bps": 10.0,
        "book_fresh": True,
        "change_pct_24h": 2.0,
        "derivatives_status": "available",
        "funding_rate": 0.0,
        "entry_quality": "actionable_now",
    }
    account = {"futures_cash_usdt": 500.0}

    long_plan = trader._design_crypto_candidate_price_plan(
        candidate={"symbol": "BTCUSDT", "entry_quality": "actionable_now"},
        features=features,
        market="futures",
        side="long",
        horizon="futures",
        account=account,
        pattern_prior={},
        live_authority={},
    )
    short_plan = trader._design_crypto_candidate_price_plan(
        candidate={"symbol": "BTCUSDT", "entry_quality": "actionable_now"},
        features=features,
        market="futures",
        side="short",
        horizon="futures",
        account=account,
        pattern_prior={},
        live_authority={},
    )

    assert long_plan["quote_budget_usdt"] == pytest.approx(75.0)
    assert long_plan["sizing_inputs"]["quote_budget_usdt"] == pytest.approx(75.0)
    assert long_plan["sizing_inputs"]["performance_budget_multiplier"] == pytest.approx(1.5)
    assert short_plan["quote_budget_usdt"] == pytest.approx(25.0)
    assert short_plan["sizing_inputs"]["quote_budget_usdt"] == pytest.approx(25.0)
    assert short_plan["sizing_inputs"]["performance_budget_multiplier"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("gate_status", "readiness"),
    [
        ("blocked_by_validation", "blocked_by_validation"),
        ("validation_incomplete", "scale_ready"),
        ("validation_stale", "scale_ready"),
    ],
)
def test_validation_gate_rejects_immediate_manager_create_block(
    tmp_path: Path,
    gate_status: str,
    readiness: str,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "confidence": 0.82,
                    "qty": 0.01,
                    "entry_price": 100.0,
                    "target_price": 96.0,
                    "stop_price": 102.0,
                    "margin_type": "isolated",
                    "leverage": 1,
                    "liquidation_price": 140.0,
                    "thesis": "immediate short should not pass blocked validation gate",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.5,
        "scorecard_count": 12,
        "validation_gate": {
            "status": gate_status,
            "readiness": readiness,
            "reason": f"test gate {gate_status}",
        },
    }

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "horizon": "futures",
                    "stance": "short_watch",
                    "confidence": 0.82,
                    "price": 100.0,
                }
            ]
        )
    )

    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == f"live_authority_validation_gate:{gate_status}"


def test_binance_live_authority_metadata_keeps_validation_failure_context(
    tmp_path: Path,
) -> None:
    raw_marker = "BINANCE_VALIDATION_RAW_MARKER"
    trader = _trader(tmp_path)
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.5,
        "scorecard_count": 3,
        "active_revision_evidence": {
            "version": "active_revision_evidence_v1",
            "venue": "binance",
            "strategy_revision_id": "jue_edge_repair_v1",
            "status": "no_active_revision_samples",
            "authority_posture": "observe_only_until_new_revision_trades_close",
            "effective_sample_count": 0,
            "validation_sample_count": 0,
            "lane_alpha_count": 0,
            "min_samples_to_scale": 20,
            "scorecard_count": 0,
            "performance_lane_count": 0,
            "validation_fail_count": 0,
            "validation_missing_count": 16,
            "hard_blocking_count": 3,
            "scale_up_allowed": False,
            "block_design_requirement": (
                "새 revision Binance 표본이 쌓일 때까지 대기진입/소액 검증만 허용"
            ),
            "raw_rows": ["BINANCE_ACTIVE_REVISION_METADATA_RAW_ROWS"] * 100,
        },
        "lane_authority": {
            "version": "lane_authority_v1",
            "global_scale_up_allowed": False,
            "weak_lanes": ["futures:short"],
            "scale_candidate_lanes": ["spot:long"],
            "block_design_requirements": [
                "weak_lanes_use_observe_small_probe_or_waiting_entry"
            ],
        },
        "trading_validation": {
            "payload": {
                "summary": {
                    "total_score": 38.5,
                    "readiness": "blocked_by_validation",
                    "pass_count": 1,
                    "warn_count": 0,
                    "fail_count": 1,
                    "missing_count": 0,
                },
                "disciplines": [
                    {
                        "id": "data_validation",
                        "label": "데이터 검증",
                        "status": "pass",
                        "action": "data usable",
                    },
                    {
                        "id": "monte_carlo",
                        "label": "몬테카를로 시뮬레이션",
                        "status": "fail",
                        "action": "sequence risk 축소",
                    },
                ],
                "raw_diagnostics": raw_marker * 30,
            }
        },
            "validation_gate": {
                "status": "blocked_by_validation",
                "readiness": "blocked_by_validation",
                "reason": "readiness=blocked_by_validation, fail_count=2",
                "risk_governor_action": "halt_new_risk",
                "risk_governor_source": "ruin_profile",
                "risk_governor_reasons": ["ruin_profile:halt_new_risk"],
                "failed_disciplines": [
                    {
                        "id": "monte_carlo",
                    "label": "몬테카를로 시뮬레이션",
                    "status": "fail",
                }
            ],
            "capacity_bottleneck": {
                "tightest_symbol": "ATOMUSDT",
                "min_capacity_ratio": 72.9,
                "capacity_method": "orderbook_depth_and_turnover",
            },
            "failure_attribution": {
                "recovery_focus": [
                    "symbol=ZECUSDT net -1.39, PF 0.00, expectancy -2.77%"
                ],
                "worst_groups": [
                    {
                        "group_type": "symbol",
                        "group": "ZECUSDT",
                        "risk_score": 55.14,
                    }
                ],
            },
            "loss_cooldown": {
                "symbols": [
                    {
                        "symbol": "ZECUSDT",
                        "risk_score": 55.14,
                        "action": "do_not_scale_or_create_live_entry_without_new_evidence",
                    }
                ],
                "instruction": "recent loss-cooldown context",
            },
            "validation_recovery_focus": [
                {
                    "source": "pattern_lab",
                    "reason": "active_walk_forward_windows_missing",
                    "action": "Re-run rolling WFA windows for active optimized sets.",
                    "active_set_count": 1,
                }
            ],
            "operator_guidance": ["몬테카를로: sequence risk 축소"],
            "remediation_plan": {
                "status": "blocked",
                "primary_next_action": "Binance rolling WFA 재생성",
                "weak_count": 5,
                "failed_count": 2,
                "categories": [
                    {
                        "id": "research_validation_work",
                        "label": "연구/백테스트 보강",
                        "weak_count": 2,
                        "fail_count": 2,
                        "items": [
                            {
                                "discipline_id": "walk_forward_analysis",
                                "label": "Walk Forward Analysis",
                                "status": "fail",
                                "action": "Binance rolling WFA 재생성",
                            }
                        ],
                    }
                ],
            },
        },
    }

    metadata = trader._live_authority_metadata()

    assert raw_marker not in str(metadata)
    assert "BINANCE_ACTIVE_REVISION_METADATA_RAW_ROWS" not in str(metadata)
    assert metadata["validation_gate_status"] == "blocked_by_validation"
    assert metadata["active_revision_evidence"]["strategy_revision_id"] == (
        "jue_edge_repair_v1"
    )
    assert metadata["active_revision_evidence"]["status"] == (
        "no_active_revision_samples"
    )
    assert metadata["active_revision_evidence"]["hard_blocking_count"] == 3
    assert metadata["validation_gate_reason"] == (
        "readiness=blocked_by_validation, fail_count=2"
    )
    assert metadata["discipline_matrix"]["expected_count"] == 19
    assert metadata["discipline_matrix"]["summary"]["readiness"] == (
        "blocked_by_validation"
    )
    matrix_status_ids = [row["id"] for row in metadata["discipline_matrix"]["statuses"]]
    assert matrix_status_ids[:2] == [
        "data_validation",
        "monte_carlo",
    ]
    assert len(matrix_status_ids) == 19
    assert "walk_forward_analysis" in matrix_status_ids
    assert any(
        row["id"] == "walk_forward_analysis" and row["status"] == "missing"
        for row in metadata["discipline_matrix"]["statuses"]
    )
    assert metadata["failed_disciplines"][0]["id"] == "monte_carlo"
    assert metadata["capacity_bottleneck"]["tightest_symbol"] == "ATOMUSDT"
    assert metadata["failure_attribution"]["worst_groups"][0]["group"] == "ZECUSDT"
    assert metadata["loss_cooldown"]["symbols"][0]["symbol"] == "ZECUSDT"
    assert metadata["validation_recovery_focus"][0]["reason"] == (
        "active_walk_forward_windows_missing"
    )
    assert metadata["operator_guidance"] == ["몬테카를로: sequence risk 축소"]
    assert metadata["risk_governor_action"] == "halt_new_risk"
    assert metadata["risk_governor_source"] == "ruin_profile"
    assert metadata["risk_governor_reasons"] == ["ruin_profile:halt_new_risk"]
    assert metadata["validation_pressure"]["version"] == "validation_pressure_v1"
    assert metadata["validation_pressure"]["hard_block"] is True
    assert metadata["validation_pressure"]["entry_posture"] == "no_new_entry"
    assert "monte_carlo" in metadata["validation_pressure"]["fail_ids"]
    assert "use_fractional_kelly_with_drawdown_and_ruin_caps" in (
        metadata["validation_pressure"]["block_design_requirements"]
    )
    assert metadata["lane_authority"]["weak_lanes"] == ["futures:short"]
    assert metadata["lane_authority"]["scale_candidate_lanes"] == ["spot:long"]
    assert metadata["remediation_plan"]["status"] == "blocked"
    assert metadata["remediation_plan"]["primary_next_action"] == (
        "Binance rolling WFA 재생성"
    )


def test_volatile_attack_price_plan_uses_small_waiting_wide_rr_block(
    tmp_path: Path,
) -> None:
    regular_trader = _trader(tmp_path)
    regular_trader.config.volatile_attack_enabled = False
    trader = _trader(tmp_path)
    features = {
        "symbol": "MEMEUSDT",
        "price": 1.0,
        "bid_price": 0.999,
        "ask_price": 1.001,
        "spread_bps": 18.0,
        "book_fresh": True,
        "change_pct_24h": 22.0,
        "quote_volume_usdt": 75_000_000,
        "volume_expansion_ratio": 3.2,
        "wick_risk_score": 48.0,
        "squeeze_risk_score": 76.0,
        "entry_quality": "actionable_now",
        "derivatives_status": "available",
        "funding_rate": -0.00012,
        "open_interest": 12_000_000,
    }

    regular = regular_trader._design_crypto_candidate_price_plan(
        candidate={"symbol": "MEMEUSDT", "entry_quality": "actionable_now"},
        features=features,
        market="spot",
        side="long",
        horizon="short",
        account={"spot_cash_usdt": 1000.0},
        pattern_prior={},
        live_authority={},
    )
    volatile = trader._design_crypto_candidate_price_plan(
        candidate={
            "symbol": "MEMEUSDT",
            "entry_quality": "actionable_now",
            "lane": "volatile_attack",
        },
        features=features,
        market="spot",
        side="long",
        horizon="short",
        account={"spot_cash_usdt": 1000.0},
        pattern_prior={},
        live_authority={},
    )

    assert volatile["entry_style"] == "wait_for_price"
    assert volatile["raw_entry_quality"] == "wait_for_price"
    assert volatile["lane"] == "volatile_attack"
    assert volatile["risk_pct"] > regular["risk_pct"]
    assert volatile["sizing_inputs"]["target_reward_risk"] >= 2.0
    assert volatile["quote_budget_usdt"] < regular["quote_budget_usdt"]
    assert volatile["market_inputs"]["volume_expansion_ratio"] == pytest.approx(3.2)
    assert volatile["technical_inputs"]["wick_risk_score"] == pytest.approx(48.0)


def test_manager_prompt_builds_executable_price_design_from_crypto_research(
    tmp_path: Path,
) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "requested_symbols": symbols or [],
                "limit": limit,
                "items": [
                    {
                        "symbol": "INJUSDT",
                        "features": {
                            "price": 5.488,
                            "bid_price": 5.488,
                            "ask_price": 5.489,
                            "spread_bps": 1.82,
                            "change_pct_24h": -4.2,
                            "quote_volume_usdt": 9_870_000,
                            "entry_quality": "wait_pullback",
                            "entry_quality_score": 72,
                            "derivatives_status": "available",
                            "funding_rate": 0.00002395,
                            "open_interest": 4_640_000,
                            "timeframe_alignment": "bearish",
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "INJUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "horizon": "intraday",
                        "score": 82,
                        "confidence": 0.71,
                        "reason_md": "Pullback candidate with acceptable liquidity.",
                        "block_template": {"entry_style": "wait_pullback"},
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["candidate_generation"]["source"] == "crypto_research_price_design_v1"
    candidate = prompt["candidates"][0]
    assert candidate["symbol"] == "INJUSDT"
    assert candidate["market"] == "spot"
    assert candidate["side"] == "long"
    assert candidate["entry_price"] == candidate["entry_price_usdt"]
    assert candidate["target_price"] == candidate["target_price_usdt"]
    assert candidate["stop_price"] == candidate["stop_price_usdt"]
    assert candidate["stop_price"] < candidate["entry_price"] < candidate["target_price"]
    assert candidate["calculated"]["reward_risk"] >= 1.5
    assert candidate["calculated"]["market_inputs"]["last_price"] == pytest.approx(5.488)
    assert candidate["calculated"]["market_inputs"]["spread_bps"] == 0
    assert candidate["calculated"]["market_inputs"]["book_fresh"] is False
    assert candidate["calculated"]["technical_inputs"]["entry_quality"] == "wait_pullback"
    assert candidate["calculated"]["derivatives_inputs"]["derivatives_status"] == "available"
    assert candidate["calculated"]["sizing_inputs"]["quote_budget_usdt"] > 0
    assert candidate["calculated"]["decision_notes"]
    assert "calculated values" in prompt["policy"]["price_design_policy"]


def test_manager_prompt_exposes_volatile_attack_candidate_packet(
    tmp_path: Path,
) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "observed_symbol_count": 300,
                "research_universe_count": 120,
                "items": [
                    {
                        "symbol": "MEMEUSDT",
                        "features": {
                            "price": 1.0,
                            "spread_bps": 16.0,
                            "book_fresh": False,
                            "change_pct_24h": 21.0,
                            "quote_volume_usdt": 80_000_000,
                            "volume_expansion_ratio": 3.4,
                            "wick_risk_score": 45.0,
                            "squeeze_risk_score": 78.0,
                            "entry_quality": "actionable_now",
                        },
                    }
                ],
                "candidate_packets": {
                    "volatile_candidates": [
                        {
                            "symbol": "MEMEUSDT",
                            "score": 88.0,
                            "change_pct_24h": 21.0,
                            "volume_expansion_ratio": 3.4,
                        }
                    ]
                },
                "candidates": [
                    {
                        "symbol": "MEMEUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "horizon": "intraday",
                        "score": 88,
                        "confidence": 0.68,
                        "lane": "volatile_attack",
                        "reason_md": "Explosive volume with squeeze setup.",
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    candidate = prompt["candidates"][0]

    assert result["status"] == "ok"
    assert prompt["candidate_generation"]["stage_counts"]["observe_universe"] == 300
    assert prompt["candidate_generation"]["stage_counts"]["research_universe"] == 120
    assert prompt["candidate_generation"]["candidate_packets"]["volatile_candidates"][0]["symbol"] == "MEMEUSDT"
    assert (
        prompt["candidate_generation"]["candidate_packets"]["volatile_candidates"][0][
            "alpha_score_v3"
        ]["version"]
        == "crypto_alpha_score_v3"
    )
    assert prompt["candidate_generation"]["volatile_attack_candidate_count"] == 1
    assert candidate["lane"] == "volatile_attack"
    assert candidate["calculated"]["lane"] == "volatile_attack"
    assert candidate["entry_style"] == "wait_for_price"
    assert "volatile_attack_policy" in prompt["policy"]


def test_manager_prompt_synthesizes_volatile_attack_when_packet_is_partial(
    tmp_path: Path,
) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "observed_symbol_count": 300,
                "research_universe_count": 120,
                "items": [
                    {
                        "symbol": "NFPUSDT",
                        "features": {
                            "price": 0.42,
                            "last_price": 0.42,
                            "change_pct_24h": 67.0,
                            "quote_volume_usdt": 92_000_000,
                            "volume_expansion_ratio": 4.1,
                            "wick_risk_score": 42.0,
                            "squeeze_risk_score": 72.0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                            "funding_rate": -0.00008,
                            "open_interest": 8_500_000,
                        },
                    }
                ],
                "candidate_packets": {
                    "top_movers": [
                        {
                            "symbol": "NFPUSDT",
                            "change_pct_24h": 67.0,
                            "quote_volume_usdt": 92_000_000,
                        }
                    ],
                    "volatile_candidates": [],
                },
                "candidates": [
                    {
                        "symbol": "NFPUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "horizon": "intraday",
                        "score": 86,
                        "confidence": 0.66,
                        "reason_md": "High-volume mover needs a small probe plan, not passive omission.",
                    }
                ],
            }

    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(
            self,
            symbol: str,
            *,
            market: str = "spot",
        ) -> dict[str, Any]:
            _ = market
            if symbol == "KRW-NFP":
                return {
                    "symbol": symbol,
                    "market": "upbit_spot",
                    "bid_price": 419.0,
                    "ask_price": 421.0,
                    "spread_bps": 47.62,
                    "source": "fake_book",
                }
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 0.4198,
                "ask_price": 0.4202,
                "spread_bps": 9.52,
                "source": "fake_book",
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["candidate_generation"]["candidate_packets"]["volatile_candidates"]
    assert (
        prompt["candidate_generation"]["candidate_packets"]["volatile_candidates"][0][
            "symbol"
        ]
        == "NFPUSDT"
    )
    assert prompt["candidate_generation"]["volatile_attack_candidate_count"] >= 1
    volatile = [
        row for row in prompt["candidates"] if row.get("lane") == "volatile_attack"
    ]
    assert volatile
    assert volatile[0]["symbol"] == "NFPUSDT"
    assert volatile[0]["entry_style"] == "wait_for_price"
    assert volatile[0]["calculated"]["lane"] == "volatile_attack"


def test_manager_prompt_promotes_packet_only_volatile_attack_to_trade_candidate(
    tmp_path: Path,
) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "observed_symbol_count": 300,
                "research_universe_count": 120,
                "items": [
                    {
                        "symbol": "NFPUSDT",
                        "features": {
                            "price": 0.42,
                            "last_price": 0.42,
                            "spread_bps": 9.52,
                            "change_pct_24h": 67.0,
                            "quote_volume_usdt": 92_000_000,
                            "volume_expansion_ratio": 4.1,
                            "wick_risk_score": 42.0,
                            "squeeze_risk_score": 72.0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                            "funding_rate": -0.00008,
                            "open_interest": 8_500_000,
                        },
                    },
                    {
                        "symbol": "AAVEUSDT",
                        "features": {
                            "price": 85.0,
                            "last_price": 85.0,
                            "spread_bps": 8.0,
                            "change_pct_24h": 1.2,
                            "quote_volume_usdt": 28_000_000,
                            "entry_quality": "wait_pullback",
                            "derivatives_status": "available",
                        },
                    },
                ],
                "candidate_packets": {
                    "top_movers": [
                        {
                            "symbol": "NFPUSDT",
                            "change_pct_24h": 67.0,
                            "quote_volume_usdt": 92_000_000,
                        }
                    ],
                    "volatile_candidates": [
                        {
                            "symbol": "NFPUSDT",
                            "change_pct_24h": 67.0,
                            "quote_volume_usdt": 92_000_000,
                            "volume_expansion_ratio": 4.1,
                        }
                    ],
                },
                "candidates": [
                    {
                        "symbol": "AAVEUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "horizon": "intraday",
                        "score": 82,
                        "confidence": 0.62,
                        "reason_md": "Ordinary pullback candidate.",
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["candidate_generation"]["volatile_attack_candidate_count"] >= 1
    volatile = [
        row for row in prompt["candidates"] if row.get("lane") == "volatile_attack"
    ]
    assert volatile
    assert volatile[0]["symbol"] == "NFPUSDT"
    assert volatile[0]["calculated"]["lane"] == "volatile_attack"
    assert volatile[0]["entry_style"] == "wait_for_price"


def test_manager_prompt_promotes_explicit_volatile_packet_even_when_context_score_is_low(
    tmp_path: Path,
) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "observed_symbol_count": 300,
                "research_universe_count": 120,
                "items": [
                    {
                        "symbol": "PONDUSDT",
                        "features": {
                            "price": 0.012,
                            "last_price": 0.012,
                            "spread_bps": 76.0,
                            "change_pct_24h": 41.9,
                            "quote_volume_usdt": 8_000_000,
                            "volume_expansion_ratio": 4.6,
                            "wick_risk_score": 40.0,
                            "squeeze_risk_score": 0.0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                        },
                    }
                ],
                "candidate_packets": {
                    "volatile_candidates": [
                        {
                            "symbol": "PONDUSDT",
                            "change_pct_24h": 41.9,
                            "quote_volume_usdt": 8_000_000,
                            "volume_expansion_ratio": 4.6,
                            "spread_bps": 76.0,
                        }
                    ]
                },
                "candidates": [],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["candidate_generation"]["volatile_attack_candidate_count"] >= 1
    volatile = [
        row for row in prompt["candidates"] if row.get("lane") == "volatile_attack"
    ]
    assert volatile
    assert volatile[0]["symbol"] == "PONDUSDT"
    assert volatile[0]["calculated"]["volatile_attack"] is True
    assert volatile[0]["entry_style"] == "wait_for_price"


def test_manager_candidate_packets_overlay_selected_performance_budget(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)

    packets = trader._manager_candidate_packets(
        crypto_research={
            "candidate_packets": {
                "volatile_candidates": [
                    {
                        "symbol": "LOSSUSDT",
                        "market": "futures",
                        "side": "short",
                        "score": 88.0,
                        "change_pct_24h": -18.0,
                    }
                ]
            }
        },
        selected_candidates=[
            {
                "symbol": "LOSSUSDT",
                "market": "futures",
                "side": "short",
                "empirical_edge_score": 51.25,
                "performance_budget_multiplier": 0.35,
                "calculated": {
                    "performance_budget_multiplier": 0.35,
                    "sizing_inputs": {
                        "performance_budget_multiplier": 0.35,
                        "lane_performance_loss_multiplier": 0.35,
                    },
                },
            }
        ],
    )

    row = packets["volatile_candidates"][0]
    assert row["performance_budget_multiplier"] == pytest.approx(0.35)
    assert row["empirical_edge_score"] == pytest.approx(51.25)


def test_manager_prompt_enriches_candidates_with_live_book_ticker(tmp_path: Path) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 5.48,
                "ask_price": 5.49,
                "spread_bps": 18.21,
                "source": "fake_book",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "INJUSDT",
                        "features": {
                            "price": 5.488,
                            "spread_bps": 0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "INJUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "score": 82,
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    by_lane = {
        (candidate["market"], candidate["side"]): candidate
        for candidate in prompt["candidates"]
    }
    candidate = by_lane[("spot", "long")]
    calculated = candidate["calculated"]

    assert result["status"] == "ok"
    assert candidate["symbol"] == "INJUSDT"
    assert calculated["market_inputs"]["bid_price"] == pytest.approx(5.48)
    assert calculated["market_inputs"]["ask_price"] == pytest.approx(5.49)
    assert calculated["market_inputs"]["spread_bps"] == pytest.approx(18.21)
    assert calculated["market_inputs"]["book_source"] == "fake_book"
    assert calculated["market_inputs"]["book_fresh"] is True
    assert candidate["entry_price"] == pytest.approx(5.49)
    assert by_lane[("futures", "long")]["futures_shadow"] is True
    assert prompt["candidate_generation"]["book_enriched_count"] == 2


def test_manager_candidate_uses_pattern_prior_only_with_live_crosscheck(
    tmp_path: Path,
) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 100.0,
                "ask_price": 100.1,
                "spread_bps": 9.99,
                "source": "fake_book",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "price": 100.05,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                            "funding_rate": 0.0002,
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "stance": "short_watch",
                        "score": 84,
                    }
                ],
            }

    class PatternProvider:
        def context_pack(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 12,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "optimized_strategy_sets": [
                    {
                        "set_id": "set-btc-short",
                        "symbol": "BTCUSDT",
                        "pattern_key": "bollinger_squeeze:short:15m",
                        "direction": "short",
                        "interval": "15m",
                        "objective": "risk_adjusted_net_r_v1",
                        "objective_score": 101.0,
                        "trade_count": 40,
                        "win_rate": 0.62,
                        "expectancy_r": 0.42,
                        "profit_factor": 1.9,
                        "parameter_set": {
                            "stop_pct": 0.008,
                            "target_pct": 0.016,
                            "holding_bars": 10,
                        },
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()
    trader.crypto_pattern_provider = PatternProvider()
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "scorecard_count": 4,
    }

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    candidate = prompt["candidates"][0]
    calculated = candidate["calculated"]

    assert result["status"] == "ok"
    assert "pattern_live_confluence_policy" in prompt["policy"]
    assert prompt["candidate_generation"]["optimized_strategy_set_count"] == 1
    assert calculated["method_version"] == "crypto_research_price_design_v2_pattern_confluence"
    assert calculated["risk_pct"] == pytest.approx(0.8)
    assert calculated["target_pct"] == pytest.approx(1.6)
    assert calculated["pattern_inputs"]["geometry_used"] is True
    assert calculated["pattern_live_crosscheck"]["status"] == "aligned"
    assert calculated["pattern_live_crosscheck"]["immediate_block_allowed"] is True
    assert candidate["metadata"]["pattern_live_crosscheck"]["status"] == "aligned"


def test_manager_prompt_includes_pattern_performance_policy(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))

    assert result["status"] == "ok"
    prompt = llm.calls[0]["payload"]
    policy = prompt["policy"]["pattern_performance_policy"]
    assert "candidate.pattern_performance_scorecard" in policy
    assert "de_risk" in policy
    assert "scale_candidate" in policy


def test_manager_candidate_edge_ranking_prefers_valid_optimized_prior() -> None:
    weak = {
        "symbol": "WEAKUSDT",
        "market": "spot",
        "side": "long",
        "score": 96,
        "confidence": 0.9,
        "calculated": {
            "reward_risk": 1.55,
            "pattern_live_crosscheck": {"status": "no_pattern_prior"},
        },
    }
    edge = {
        "symbol": "EDGEUSDT",
        "market": "spot",
        "side": "long",
        "score": 72,
        "confidence": 0.7,
        "calculated": {
            "reward_risk": 2.25,
            "pattern_live_crosscheck": {"status": "aligned"},
            "pattern_inputs": {
                "prior_quality": {"passed": True},
                "prior": {
                    "objective_score": 120.0,
                    "trade_count": 42,
                    "expectancy_r": 0.44,
                    "out_of_sample_expectancy_r": 0.32,
                    "profit_factor": 1.82,
                },
            },
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge([weak, edge])

    assert ranked[0]["symbol"] == "EDGEUSDT"
    assert BinanceBlockTrader._manager_candidate_empirical_edge_score(edge) > (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(weak)
    )


def test_manager_candidate_edge_ranking_demotes_performance_cooldown_symbol() -> None:
    cooldown_candidate = {
        "symbol": "WLDUSDT",
        "market": "spot",
        "side": "long",
        "score": 98,
        "confidence": 0.95,
        "calculated": {
            "reward_risk": 3.0,
            "pattern_live_crosscheck": {"status": "aligned"},
            "pattern_inputs": {
                "prior_quality": {"passed": True},
                "prior": {
                    "objective_score": 150.0,
                    "trade_count": 60,
                    "expectancy_r": 0.5,
                    "out_of_sample_expectancy_r": 0.35,
                    "profit_factor": 1.8,
                },
            },
        },
    }
    healthy_candidate = {
        "symbol": "ZECUSDT",
        "market": "futures",
        "side": "short",
        "score": 72,
        "confidence": 0.72,
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }
    entry_gate_policy = {
        "cooldown_symbol_keys": ["WLDUSDT"],
        "cooldown_symbols": {
            "WLDUSDT": {
                "status": "cooldown",
                "sample_count": 9,
                "pnl_usdt": -0.96,
                "profit_factor": 0.34,
            }
        },
        "cooldown_lane_keys": ["spot:long"],
        "cooldown_lanes": {
            "spot:long": {
                "status": "cooldown",
                "sample_count": 11,
                "pnl_usdt": -0.81,
                "profit_factor": 0.37,
            }
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge(
        [cooldown_candidate, healthy_candidate],
        entry_gate_policy=entry_gate_policy,
    )

    assert ranked[0]["symbol"] == "ZECUSDT"
    assert (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(
            cooldown_candidate,
            entry_gate_policy=entry_gate_policy,
        )
        < BinanceBlockTrader._manager_candidate_empirical_edge_score(
            healthy_candidate,
            entry_gate_policy=entry_gate_policy,
        )
    )


def test_manager_candidate_edge_ranking_demotes_de_risked_budget_lane() -> None:
    de_risked_candidate = {
        "symbol": "LOSSUSDT",
        "market": "futures",
        "side": "short",
        "score": 98,
        "confidence": 0.95,
        "calculated": {
            "reward_risk": 3.0,
            "pattern_live_crosscheck": {"status": "aligned"},
            "sizing_inputs": {
                "performance_budget_multiplier": 0.4,
            },
        },
    }
    healthy_candidate = {
        "symbol": "EDGEUSDT",
        "market": "futures",
        "side": "long",
        "score": 74,
        "confidence": 0.74,
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "aligned"},
            "sizing_inputs": {
                "performance_budget_multiplier": 1.0,
            },
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge(
        [de_risked_candidate, healthy_candidate]
    )

    assert ranked[0]["symbol"] == "EDGEUSDT"
    assert (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(de_risked_candidate)
        < BinanceBlockTrader._manager_candidate_empirical_edge_score(healthy_candidate)
    )


def test_manager_candidate_edge_ranking_demotes_weak_pattern_scorecard() -> None:
    weak_pattern_candidate = {
        "symbol": "LOSSPATTERNUSDT",
        "market": "futures",
        "side": "short",
        "score": 99,
        "confidence": 0.95,
        "pattern_performance_scorecard": {
            "pattern_key": "bollinger_squeeze:short:5m",
            "sample_count": 5,
            "pnl_usdt": -1.25,
            "avg_r_multiple": -0.42,
            "win_rate_pct": 20.0,
            "profit_factor": 0.35,
            "recovery_factor": -1.0,
        },
        "calculated": {
            "reward_risk": 3.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }
    healthy_candidate = {
        "symbol": "HEALTHYUSDT",
        "market": "futures",
        "side": "short",
        "score": 74,
        "confidence": 0.74,
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge(
        [weak_pattern_candidate, healthy_candidate]
    )

    assert ranked[0]["symbol"] == "HEALTHYUSDT"
    assert (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(
            weak_pattern_candidate
        )
        < BinanceBlockTrader._manager_candidate_empirical_edge_score(healthy_candidate)
    )


def test_manager_candidate_edge_ranking_demotes_execution_blocker() -> None:
    blocked_candidate = {
        "symbol": "ETHUSDT",
        "market": "futures",
        "side": "short",
        "score": 99,
        "confidence": 0.95,
        "execution_blockers": {
            "status": "would_reject_current_gates",
            "blocker_count": 1,
            "ranking_penalty": 95.0,
            "blockers": [
                {
                    "kind": "symbol_performance_cooldown",
                    "reason": "symbol_performance_cooldown:ETHUSDT:futures:short",
                }
            ],
        },
        "calculated": {
            "reward_risk": 3.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }
    executable_candidate = {
        "symbol": "ZECUSDT",
        "market": "futures",
        "side": "short",
        "score": 72,
        "confidence": 0.72,
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge(
        [blocked_candidate, executable_candidate]
    )

    assert ranked[0]["symbol"] == "ZECUSDT"
    assert (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(blocked_candidate)
        < BinanceBlockTrader._manager_candidate_empirical_edge_score(
            executable_candidate
        )
    )


def test_manager_candidate_edge_ranking_demotes_stale_research_candidate() -> None:
    stale_candidate = {
        "symbol": "STALEUSDT",
        "market": "futures",
        "side": "short",
        "score": 99,
        "confidence": 0.95,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "calculated": {
            "reward_risk": 3.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }
    fresh_candidate = {
        "symbol": "FRESHUSDT",
        "market": "futures",
        "side": "short",
        "score": 74,
        "confidence": 0.74,
        "updated_at": binance_block_trader_module.utc_now_iso(),
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge(
        [stale_candidate, fresh_candidate]
    )

    assert ranked[0]["symbol"] == "FRESHUSDT"
    assert (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(stale_candidate)
        < BinanceBlockTrader._manager_candidate_empirical_edge_score(fresh_candidate)
    )


def test_manager_candidate_edge_ranking_demotes_near_duplicate_active_block() -> None:
    near_duplicate = {
        "symbol": "PAXGUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "futures",
        "score": 99,
        "confidence": 0.95,
        "near_duplicate_active_block": {
            "status": "review_required",
            "existing_block_id": "bnb_futures_PAXGUSDT_open",
            "action_hint": "manage_existing_block",
        },
        "calculated": {
            "reward_risk": 3.0,
            "pattern_live_crosscheck": {"status": "aligned"},
            "pattern_inputs": {
                "prior_quality": {"passed": True},
                "prior": {
                    "objective_score": 180.0,
                    "trade_count": 80,
                    "expectancy_r": 0.55,
                    "out_of_sample_expectancy_r": 0.25,
                    "profit_factor": 2.2,
                },
            },
        },
    }
    independent = {
        "symbol": "ZECUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "futures",
        "score": 72,
        "confidence": 0.7,
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge(
        [near_duplicate, independent]
    )

    assert ranked[0]["symbol"] == "ZECUSDT"
    assert (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(near_duplicate)
        < BinanceBlockTrader._manager_candidate_empirical_edge_score(independent)
    )


def test_manager_candidate_edge_ranking_promotes_positive_sample_building_lane() -> None:
    weak_spot = {
        "symbol": "BIOUSDT",
        "market": "spot",
        "side": "long",
        "horizon": "short",
        "score": 98,
        "confidence": 0.94,
        "lane_authority_candidate": {
            "lane": "spot",
            "grade": "weak",
            "action": "de_risk_or_waiting_entry",
            "sample_count": 32,
            "expectancy_pct": -1.2,
            "win_rate_pct": 15.6,
            "profit_factor": 0.04,
            "requires_waiting_entry": True,
        },
        "calculated": {
            "reward_risk": 1.8,
            "pattern_live_crosscheck": {"status": "aligned"},
        },
    }
    positive_futures_short = {
        "symbol": "PAXGUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "futures",
        "score": 72,
        "confidence": 0.72,
        "lane_authority_candidate": {
            "lane": "futures_short",
            "grade": "insufficient",
            "action": "small_probe_until_sample_builds",
            "sample_count": 7,
            "expectancy_pct": 0.25,
            "win_rate_pct": 57.1,
            "profit_factor": 2.88,
            "requires_waiting_entry": True,
        },
        "calculated": {
            "reward_risk": 2.0,
            "pattern_live_crosscheck": {"status": "wait"},
        },
    }

    ranked = BinanceBlockTrader._rank_manager_candidates_by_edge(
        [weak_spot, positive_futures_short]
    )

    assert ranked[0]["symbol"] == "PAXGUSDT"
    assert (
        BinanceBlockTrader._manager_candidate_empirical_edge_score(
            positive_futures_short
        )
        > BinanceBlockTrader._manager_candidate_empirical_edge_score(weak_spot)
    )


def test_manager_executable_candidates_annotates_lane_authority_context(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    live_authority = {
        "status": "ok",
        "max_budget_multiplier": 0.25,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["spot"],
            "insufficient_lanes": ["futures_short"],
            "lane_actions": {
                "spot": {
                    "grade": "weak",
                    "action": "de_risk_or_waiting_entry",
                    "sample_count": 32,
                    "expectancy_pct": -1.2,
                    "win_rate_pct": 15.6,
                    "profit_factor": 0.04,
                    "requires_waiting_entry": True,
                },
                "futures_short": {
                    "grade": "insufficient",
                    "action": "small_probe_until_sample_builds",
                    "sample_count": 7,
                    "expectancy_pct": 0.25,
                    "win_rate_pct": 57.1,
                    "profit_factor": 2.88,
                    "requires_waiting_entry": True,
                },
            },
        },
    }

    selected, generation = trader._manager_executable_candidates(
        provided_candidates=[
            {
                "symbol": "BIOUSDT",
                "market": "spot",
                "side": "long",
                "horizon": "short",
                "score": 98,
                "confidence": 0.94,
                "entry_price": 0.034,
                "target_price": 0.035,
                "stop_price": 0.033,
            },
            {
                "symbol": "PAXGUSDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "score": 72,
                "confidence": 0.72,
                "entry_price": 4172.0,
                "target_price": 4106.0,
                "stop_price": 4206.0,
            },
        ],
        crypto_research={},
        crypto_patterns={},
        live_authority=live_authority,
        market_universe={"spot": ["BIOUSDT"], "futures": ["PAXGUSDT"], "upbit_spot": []},
        account={"spot_cash_usdt": 500.0, "futures_cash_usdt": 500.0},
    )

    assert selected[0]["symbol"] == "PAXGUSDT"
    assert selected[0]["lane_authority_candidate"]["selection_bias"] == (
        "positive_sample_building"
    )
    assert generation["lane_authority_candidate_count"] == 2


def test_manager_executable_candidates_annotates_pattern_performance(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.lane_performance_min_samples = 3
    trader.config.lane_performance_loss_multiplier = 0.5
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"weak-pattern-candidate-{index}",
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures:short:futures",
                "pattern_key": "bollinger_squeeze:short:5m",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 104.0,
                "target_price": 98.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
            }
        )

    selected, generation = trader._manager_executable_candidates(
        provided_candidates=[
            {
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "score": 90,
                "confidence": 0.9,
                "price": 50_000.0,
                "entry_price": 50_000.0,
                "target_price": 48_000.0,
                "stop_price": 51_000.0,
            }
        ],
        crypto_research={},
        crypto_patterns={
            "optimized_strategy_sets": [
                {
                    "symbol": "BTCUSDT",
                    "direction": "short",
                    "pattern_key": "bollinger_squeeze:short:5m",
                    "interval": "5m",
                    "objective_score": 100.0,
                    "trade_count": 20,
                    "win_rate": 0.6,
                    "expectancy_r": 0.3,
                    "profit_factor": 1.5,
                    "parameter_set": {"stop_pct": 0.02, "target_pct": 0.04},
                }
            ]
        },
        live_authority={},
        market_universe={"spot": [], "futures": ["BTCUSDT"], "upbit_spot": []},
        account={"futures_cash_usdt": 5_000.0},
    )

    assert selected
    candidate = selected[0]
    scorecard = candidate["pattern_performance_scorecard"]
    assert scorecard["pattern_key"] == "bollinger_squeeze:short:5m"
    assert scorecard["sample_count"] == 3
    assert scorecard["status"] == "de_risk"
    assert candidate["metadata"]["pattern_performance_scorecard"] == scorecard
    assert candidate["calculated"]["sizing_inputs"]["pattern_performance_multiplier"] == (
        pytest.approx(0.5)
    )
    assert generation["pattern_performance_candidate_count"] == 1


def test_manager_executable_candidates_deprioritizes_runtime_cooldown_symbol(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    for index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"eth-cooldown-{index}",
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 105.0,
                "stop_price": 105.0,
                "target_price": 90.0,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    selected, generation = trader._manager_executable_candidates(
        provided_candidates=[
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "score": 99,
                "confidence": 0.95,
                "entry_price": 1713.35,
                "target_price": 1685.93,
                "stop_price": 1727.05,
            },
            {
                "symbol": "ZECUSDT",
                "market": "futures",
                "side": "short",
                "horizon": "futures",
                "score": 72,
                "confidence": 0.72,
                "entry_price": 30.0,
                "target_price": 28.8,
                "stop_price": 30.6,
            },
        ],
        crypto_research={},
        crypto_patterns={},
        live_authority={},
        market_universe={"spot": [], "futures": ["ETHUSDT", "ZECUSDT"], "upbit_spot": []},
        account={"futures_cash_usdt": 500.0},
    )

    blocked = next(row for row in selected if row["symbol"] == "ETHUSDT")
    assert selected[0]["symbol"] == "ZECUSDT"
    assert blocked["execution_blockers"]["status"] == "would_reject_current_gates"
    assert blocked["execution_blockers"]["blockers"][0]["reason"].startswith(
        "symbol_performance_cooldown:ETHUSDT"
    )
    assert blocked["metadata"]["execution_blockers"]["blocker_count"] >= 1
    assert generation["execution_blocked_candidate_count"] >= 1


def test_symbol_performance_cooldown_uses_symbol_lookup_when_top_cards_truncated(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.config.symbol_lane_cooldown_min_samples = 5

    for symbol_index in range(9):
        for sample_index in range(6):
            trader.repository.save_performance_reflection(
                {
                    "block_id": f"worse-symbol-{symbol_index}-{sample_index}",
                    "symbol": f"WORSE{symbol_index}USDT",
                    "market": "spot",
                    "side": "long",
                    "entry_price": 100.0,
                    "exit_price": 98.0,
                    "stop_price": 98.0,
                    "target_price": 104.0,
                    "pnl_usdt": -2.0,
                    "r_multiple": -1.0,
                }
            )

    for sample_index in range(5):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"target-symbol-loss-{sample_index}",
                "symbol": "TARGETUSDT",
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "stop_price": 98.0,
                "target_price": 104.0,
                "pnl_usdt": -0.2,
                "r_multiple": -0.5,
            }
        )

    performance = trader.repository.latest_performance_scorecard(limit=120)
    assert all(
        row["symbol"] != "TARGETUSDT"
        for row in performance["symbol_scorecards"]
    )

    rejection = trader._symbol_performance_cooldown_rejection(
        {"symbol": "TARGETUSDT", "market": "spot", "side": "long"}
    )

    assert rejection is not None
    assert rejection["reason"] == "symbol_performance_cooldown:TARGETUSDT:spot:long"
    assert rejection["symbol_performance_cooldown"]["sample_count"] == 5
    assert rejection["symbol_performance_cooldown"]["pnl_usdt"] == pytest.approx(-1.0)


def test_candidate_lane_authority_context_prefers_positive_consolidated_lane() -> None:
    context = BinanceBlockTrader._candidate_lane_authority_context(
        {
            "lane_authority": {
                "version": "lane_authority_v1",
                "weak_lanes": ["futures:short:validation:capacity_analysis"],
                "insufficient_lanes": [
                    "futures:short:validation:capacity_analysis",
                    "futures:short:futures",
                ],
                "lane_actions": {
                    "futures:short:validation:capacity_analysis": {
                        "grade": "insufficient",
                        "action": "validation_evidence_repair_waiting_probe",
                        "sample_count": 1,
                        "expectancy_pct": -0.04,
                        "profit_factor": 0.0,
                        "requires_waiting_entry": True,
                    },
                    "futures:short:futures": {
                        "grade": "insufficient",
                        "action": "validation_evidence_repair_waiting_probe",
                        "sample_count": 7,
                        "expectancy_pct": 0.25,
                        "profit_factor": 2.13,
                        "requires_waiting_entry": True,
                    },
                },
            }
        },
        {
            "symbol": "PAXGUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
        },
    )

    assert context["lane"] == "futures:short:futures"
    assert context["selection_bias"] == "positive_sample_building"
    assert context["profit_factor"] == pytest.approx(2.13)


def test_candidate_near_duplicate_context_marks_existing_active_block(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    candidate = {
        "symbol": "PAXGUSDT",
        "market": "futures",
        "side": "short",
        "horizon": "futures",
        "entry_price": 4172.94,
        "target_price": 4106.18,
        "stop_price": 4206.33,
    }
    active_blocks = [
        {
            "block_id": "bnb_futures_PAXGUSDT_open",
            "symbol": "PAXGUSDT",
            "market": "futures",
            "side": "short",
            "status": "open",
            "entry_price": 4154.65,
            "target_price": 4088.18,
            "stop_price": 4187.89,
            "metadata": {"horizon": "futures"},
        }
    ]

    context = trader._candidate_near_duplicate_active_block_context(
        candidate,
        active_blocks,
    )

    assert context["status"] == "review_required"
    assert context["action_hint"] == "manage_existing_block"
    assert context["existing_block_id"] == "bnb_futures_PAXGUSDT_open"
    assert context["candidate"]["entry_price"] == pytest.approx(4172.94)
    assert context["existing"]["entry_price"] == pytest.approx(4154.65)


def test_compact_manager_candidate_includes_near_duplicate_action_hint() -> None:
    compact = BinanceBlockTrader._compact_manager_candidate_for_prompt(
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
        }
    )

    assert compact["near_duplicate_active_block"]["status"] == "review_required"
    assert (
        compact["near_duplicate_active_block"]["action_hint"]
        == "manage_existing_block"
    )
    assert (
        compact["near_duplicate_active_block"]["existing_block_id"]
        == "bnb_futures_PAXGUSDT_open"
    )


def test_compact_manager_candidate_preserves_symbol_memory_hint() -> None:
    compact = BinanceBlockTrader._compact_manager_candidate_for_prompt(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "score": 82.0,
            "confidence": 0.73,
            "entry_price": 100000.0,
            "target_price": 106000.0,
            "stop_price": 97000.0,
            "memory_hint": {
                "status": "available",
                "source_count": 2,
                "reasons": ["BTC는 Binance 전용 메모리에서 눌림 대기 우선"],
                "risks": ["급등 추격 시 손실 기록"],
                "checks": ["funding 재확인"],
            },
        }
    )

    assert compact["memory_hint"]["status"] == "available"
    assert compact["memory_hint"]["source_count"] == 2
    assert "눌림 대기" in compact["memory_hint"]["reasons"][0]
    assert "급등 추격" in compact["memory_hint"]["risks"][0]


def test_compact_manager_candidate_exposes_performance_budget_multiplier() -> None:
    compact = BinanceBlockTrader._compact_manager_candidate_for_prompt(
        {
            "symbol": "LOSSUSDT",
            "market": "futures",
            "side": "short",
            "score": 91.0,
            "confidence": 0.81,
            "quote_budget_usdt": 8.0,
            "calculated": {
                "reward_risk": 2.4,
                "quote_budget_usdt": 8.0,
                "performance_budget_multiplier": 0.45,
                "sizing_inputs": {
                    "performance_budget_multiplier": 0.45,
                    "lane_performance_loss_multiplier": 0.45,
                    "lane_performance": {
                        "sample_count": 24,
                        "realized_pnl_usdt": -12.5,
                    },
                },
            },
        }
    )

    assert compact["performance_budget_multiplier"] == pytest.approx(0.45)
    assert compact["calculated"]["performance_budget_multiplier"] == pytest.approx(0.45)
    assert compact["calculated"]["sizing_inputs"]["performance_budget_multiplier"] == pytest.approx(0.45)


def test_compact_manager_candidate_exposes_pattern_performance_scorecard() -> None:
    compact = BinanceBlockTrader._compact_manager_candidate_for_prompt(
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
                "avg_r_multiple": -0.32,
                "win_rate_pct": 20.0,
                "profit_factor": 0.35,
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
                    "avg_r_multiple": -0.32,
                    "win_rate_pct": 20.0,
                    "profit_factor": 0.35,
                },
                "sizing_inputs": {
                    "performance_budget_multiplier": 0.5,
                    "pattern_performance_multiplier": 0.5,
                },
            },
        }
    )

    assert compact["pattern_performance_scorecard"]["pattern_key"] == (
        "bollinger_squeeze:short:5m"
    )
    assert compact["pattern_performance_scorecard"]["status"] == "de_risk"
    assert compact["calculated"]["pattern_performance_multiplier"] == pytest.approx(0.5)
    assert compact["calculated"]["sizing_inputs"]["pattern_performance_multiplier"] == (
        pytest.approx(0.5)
    )


def test_manager_executable_candidates_annotates_near_duplicate_active_block(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)

    selected, _generation = trader._manager_executable_candidates(
        provided_candidates=[
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
            }
        ],
        crypto_research={},
        crypto_patterns={},
        live_authority={},
        market_universe={"spot": [], "futures": ["PAXGUSDT"], "upbit_spot": []},
        account={"futures_cash_usdt": 500.0},
        active_blocks=[
            {
                "block_id": "bnb_futures_PAXGUSDT_open",
                "symbol": "PAXGUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "entry_price": 4154.65,
                "target_price": 4088.18,
                "stop_price": 4187.89,
                "metadata": {"horizon": "futures"},
            }
        ],
    )

    assert selected
    assert (
        selected[0]["near_duplicate_active_block"]["existing_block_id"]
        == "bnb_futures_PAXGUSDT_open"
    )
    assert (
        selected[0]["near_duplicate_active_block"]["action_hint"]
        == "manage_existing_block"
    )


def test_entry_gate_cooldown_lanes_use_precise_side_and_horizon_keys() -> None:
    performance = {
        "side_scorecards": [
            {
                "side": "futures:long",
                "sample_count": 5,
                "pnl_usdt": -0.5,
                "avg_r_multiple": -0.4,
                "win_rate_pct": 20.0,
                "profit_factor": 0.5,
                "recovery_factor": -0.5,
                "max_drawdown_r_multiple": -4.0,
            },
            {
                "side": "futures:short",
                "sample_count": 5,
                "pnl_usdt": 0.3,
                "avg_r_multiple": 0.17,
                "win_rate_pct": 60.0,
                "profit_factor": 2.4,
                "recovery_factor": 1.4,
                "max_drawdown_r_multiple": -1.5,
            },
            {
                "side": "spot:long",
                "sample_count": 10,
                "pnl_usdt": -0.7,
                "avg_r_multiple": 0.01,
                "win_rate_pct": 40.0,
                "profit_factor": 0.4,
                "recovery_factor": -0.7,
                "max_drawdown_r_multiple": -4.5,
            },
        ],
        "lane_scorecards": [
            {
                "lane": "futures",
                "sample_count": 10,
                "pnl_usdt": -0.2,
                "avg_r_multiple": -0.2,
                "win_rate_pct": 40.0,
                "profit_factor": 0.8,
                "recovery_factor": -0.2,
                "max_drawdown_r_multiple": -5.0,
            },
            {
                "lane": "short",
                "sample_count": 10,
                "pnl_usdt": -0.7,
                "avg_r_multiple": 0.01,
                "win_rate_pct": 40.0,
                "profit_factor": 0.4,
                "recovery_factor": -0.7,
                "max_drawdown_r_multiple": -4.5,
            },
        ],
    }

    cooldowns = BinanceBlockTrader._entry_gate_cooldown_lanes(
        performance,
        min_samples=3,
    )

    assert set(cooldowns) == {"futures:long", "spot:long:short"}
    assert "futures" not in cooldowns
    assert "futures:short" not in cooldowns
    assert "short" not in cooldowns
    assert "spot:long" not in cooldowns


def test_entry_gate_cooldown_lanes_add_broad_spot_long_after_severe_live_drag() -> None:
    performance = {
        "side_scorecards": [
            {
                "side": "spot:long",
                "sample_count": 26,
                "pnl_usdt": -7.39,
                "avg_r_multiple": 0.02,
                "win_rate_pct": 50.0,
                "profit_factor": 0.52,
                "recovery_factor": -0.52,
                "max_drawdown_r_multiple": -5.5,
            }
        ],
        "lane_scorecards": [
            {
                "lane": "short",
                "sample_count": 26,
                "pnl_usdt": -7.39,
                "avg_r_multiple": 0.02,
                "win_rate_pct": 50.0,
                "profit_factor": 0.52,
                "recovery_factor": -0.52,
                "max_drawdown_r_multiple": -5.5,
            }
        ],
    }

    cooldowns = BinanceBlockTrader._entry_gate_cooldown_lanes(
        performance,
        min_samples=10,
    )

    assert "spot:long" in cooldowns
    assert "spot:long:short" in cooldowns
    assert cooldowns["spot:long"]["scope"] == "broad"


def test_manager_candidate_cooldown_penalty_matches_precise_horizon_lane() -> None:
    entry_gate_policy = {
        "cooldown_lane_keys": ["spot:long:short", "futures:long"],
        "cooldown_lanes": {
            "spot:long:short": {"status": "cooldown"},
            "futures:long": {"status": "cooldown"},
        },
    }

    assert BinanceBlockTrader._manager_candidate_performance_cooldown_penalty(
        {"symbol": "ETHUSDT", "market": "spot", "side": "long", "horizon": "short"},
        entry_gate_policy=entry_gate_policy,
    ) == pytest.approx(35.0)
    assert BinanceBlockTrader._manager_candidate_performance_cooldown_penalty(
        {"symbol": "ETHUSDT", "market": "spot", "side": "long", "horizon": "mid"},
        entry_gate_policy=entry_gate_policy,
    ) == pytest.approx(0.0)
    assert BinanceBlockTrader._manager_candidate_performance_cooldown_penalty(
        {
            "symbol": "ZECUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
        },
        entry_gate_policy=entry_gate_policy,
    ) == pytest.approx(0.0)
    assert BinanceBlockTrader._manager_candidate_performance_cooldown_penalty(
        {
            "symbol": "WLDUSDT",
            "market": "futures",
            "side": "long",
            "horizon": "futures",
        },
        entry_gate_policy=entry_gate_policy,
    ) == pytest.approx(35.0)


def test_manager_candidate_cooldown_penalty_applies_broad_spot_long_lane() -> None:
    entry_gate_policy = {
        "cooldown_lane_keys": ["spot:long"],
        "cooldown_lanes": {
            "spot:long": {"status": "cooldown", "scope": "broad"},
        },
    }

    assert BinanceBlockTrader._manager_candidate_performance_cooldown_penalty(
        {"symbol": "ETHUSDT", "market": "spot", "side": "long", "horizon": "mid"},
        entry_gate_policy=entry_gate_policy,
    ) == pytest.approx(35.0)
    assert BinanceBlockTrader._manager_candidate_performance_cooldown_penalty(
        {"symbol": "ETHUSDT", "market": "futures", "side": "long", "horizon": "futures"},
        entry_gate_policy=entry_gate_policy,
    ) == pytest.approx(0.0)


def test_distressed_lane_detects_low_win_rate_futures_long_with_near_threshold_pf(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    lane_card = {
        "side": "futures:long",
        "sample_count": 6,
        "pnl_usdt": -0.53,
        "avg_r_multiple": -0.51,
        "win_rate_pct": 16.7,
        "profit_factor": 0.514,
        "recovery_factor": -0.49,
        "max_drawdown_r_multiple": -5.45,
    }

    assert trader._lane_card_is_distressed(lane_card) is True


def test_manager_prompt_edge_ranks_late_optimized_candidate(
    tmp_path: Path,
) -> None:
    class CryptoResearch:
        def __init__(self) -> None:
            self.limits: list[int] = []

        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            _ = symbols
            self.limits.append(limit)
            return {"status": "ok", "items": [], "candidates": []}

    class PatternProvider:
        def context_pack(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 12,
        ) -> dict[str, Any]:
            _ = (symbols, limit)
            return {
                "status": "ok",
                "optimized_strategy_sets": [
                    {
                        "set_id": "set-edge-long",
                        "symbol": "EDGEUSDT",
                        "pattern_key": "breakout_reclaim:long:15m",
                        "direction": "long",
                        "interval": "15m",
                        "objective": "risk_adjusted_net_r_v1",
                        "objective_score": 125.0,
                        "trade_count": 44,
                        "win_rate": 0.64,
                        "expectancy_r": 0.46,
                        "out_of_sample_expectancy_r": 0.34,
                        "profit_factor": 1.88,
                        "parameter_set": {
                            "stop_pct": 0.009,
                            "target_pct": 0.021,
                            "holding_bars": 12,
                        },
                    }
                ],
            }

    provided = [
        {
            "symbol": f"WEAK{index}USDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "score": 96 - index,
            "confidence": 0.9,
            "price": 10.0 + index,
            "entry_quality": "actionable_now",
        }
        for index in range(8)
    ]
    provided.append(
        {
            "symbol": "EDGEUSDT",
            "market": "spot",
            "side": "long",
            "horizon": "short",
            "score": 72,
            "confidence": 0.7,
            "price": 25.0,
            "entry_quality": "actionable_now",
        }
    )

    llm = _FakeLLM({"create_blocks": []})
    research = CryptoResearch()
    trader = _trader(tmp_path, llm=llm, quant_context_limit=8)
    trader.config.max_manager_symbols = 8
    trader.crypto_research_provider = research
    trader.crypto_pattern_provider = PatternProvider()
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "scorecard_count": 5,
    }

    result = asyncio.run(trader.run_manager_once(candidates=provided))
    prompt = llm.calls[0]["payload"]
    top = prompt["candidates"][0]

    assert result["status"] == "ok"
    assert research.limits == [80]
    assert prompt["candidate_generation"]["edge_ranked"] is True
    assert prompt["candidate_generation"]["candidate_total_before_limit"] == 9
    assert top["symbol"] == "EDGEUSDT"
    assert top["empirical_edge_score"] > 0
    assert top["calculated"]["empirical_edge_score"] == pytest.approx(
        top["empirical_edge_score"]
    )
    assert top["calculated"]["pattern_live_crosscheck"]["status"] in {"aligned", "wait"}
    assert top["calculated"]["pattern_inputs"]["geometry_used"] is True


def test_manager_rejects_overfit_optimized_strategy_prior(tmp_path: Path) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 100.0,
                "ask_price": 100.1,
                "spread_bps": 9.99,
                "source": "fake_book",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            _ = (symbols, limit)
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "price": 100.05,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                            "funding_rate": 0.0002,
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "stance": "short_watch",
                        "score": 84,
                    }
                ],
            }

    class PatternProvider:
        def context_pack(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 12,
        ) -> dict[str, Any]:
            _ = (symbols, limit)
            return {
                "status": "ok",
                "optimized_strategy_sets": [
                    {
                        "set_id": "set-overfit",
                        "symbol": "BTCUSDT",
                        "pattern_key": "bollinger_squeeze:short:15m",
                        "direction": "short",
                        "interval": "15m",
                        "objective": "risk_adjusted_net_r_v1",
                        "objective_score": 101.0,
                        "trade_count": 40,
                        "win_rate": 0.62,
                        "expectancy_r": 0.42,
                        "profit_factor": 1.9,
                        "parameter_set": {
                            "stop_pct": 0.008,
                            "target_pct": 0.016,
                            "holding_bars": 10,
                        },
                        "walk_forward_quality": {
                            "passed": False,
                            "status": "failed",
                            "reasons": ["out_of_sample_expectancy_negative"],
                        },
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()
    trader.crypto_pattern_provider = PatternProvider()
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": True,
        "max_budget_multiplier": 1.25,
        "scorecard_count": 4,
    }

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    calculated = prompt["candidates"][0]["calculated"]

    assert result["status"] == "ok"
    assert calculated["pattern_inputs"]["geometry_used"] is False
    assert calculated["pattern_live_crosscheck"]["status"] == "contradicted"
    assert "walk_forward_failed" in calculated["pattern_live_crosscheck"]["contradictions"]


def test_manager_prompt_keeps_live_books_scoped_by_market(tmp_path: Path) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            books = {
                "spot": {
                    "bid_price": 100.0,
                    "ask_price": 100.5,
                    "spread_bps": 49.88,
                    "source": "spot_book",
                },
                "futures": {
                    "bid_price": 101.0,
                    "ask_price": 101.5,
                    "spread_bps": 49.38,
                    "source": "futures_book",
                },
            }
            return {
                "symbol": symbol,
                "market": market,
                "fetched_at": "2026-05-25T12:00:00+00:00",
                **books[market],
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "price": 100.25,
                            "spread_bps": 0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "score": 82,
                    },
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "stance": "short_watch",
                        "score": 81,
                    },
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    by_market = {candidate["market"]: candidate for candidate in prompt["candidates"]}
    spot_inputs = by_market["spot"]["calculated"]["market_inputs"]
    futures_inputs = by_market["futures"]["calculated"]["market_inputs"]

    assert result["status"] == "ok"
    assert spot_inputs["bid_price"] == pytest.approx(100.0)
    assert spot_inputs["ask_price"] == pytest.approx(100.5)
    assert spot_inputs["book_source"] == "spot_book"
    assert by_market["spot"]["entry_price"] == pytest.approx(100.5)
    assert futures_inputs["bid_price"] == pytest.approx(101.0)
    assert futures_inputs["ask_price"] == pytest.approx(101.5)
    assert futures_inputs["book_source"] == "futures_book"
    assert by_market["futures"]["entry_price"] == pytest.approx(101.0)
    assert prompt["candidate_generation"]["book_enriched_count"] == 2


def test_manager_prompt_adds_spot_shadow_for_futures_long_candidate(
    tmp_path: Path,
) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            books = {
                "spot": {"bid_price": 99.8, "ask_price": 100.0, "source": "spot_book"},
                "futures": {
                    "bid_price": 100.1,
                    "ask_price": 100.3,
                    "source": "futures_book",
                },
            }
            return {
                "symbol": symbol,
                "market": market,
                "spread_bps": 20.0,
                "fetched_at": "2026-05-26T00:00:00+00:00",
                **books[market],
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            _ = (symbols, limit)
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "price": 100.0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "stance": "long_watch",
                        "horizon": "intraday",
                        "score": 70,
                        "confidence": 0.6,
                        "reason_md": "long exposure candidate",
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    by_market = {candidate["market"]: candidate for candidate in prompt["candidates"]}

    assert result["status"] == "ok"
    assert set(by_market) == {"spot", "futures"}
    assert by_market["spot"]["symbol"] == "BTCUSDT"
    assert by_market["spot"]["side"] == "long"
    assert by_market["spot"]["source_market"] == "futures"
    assert by_market["spot"]["spot_shadow"] is True
    assert by_market["spot"]["calculated"]["market_inputs"]["book_source"] == "spot_book"
    assert by_market["futures"]["calculated"]["market_inputs"]["book_source"] == "futures_book"
    assert prompt["candidate_generation"]["spot_shadow_candidate_count"] == 1


def test_manager_prompt_adds_futures_long_shadow_for_spot_long_candidate(
    tmp_path: Path,
) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            books = {
                "spot": {"bid_price": 99.8, "ask_price": 100.0, "source": "spot_book"},
                "futures": {
                    "bid_price": 100.1,
                    "ask_price": 100.3,
                    "source": "futures_book",
                },
            }
            return {
                "symbol": symbol,
                "market": market,
                "spread_bps": 20.0,
                "fetched_at": "2026-05-26T00:00:00+00:00",
                **books[market],
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            _ = (symbols, limit)
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "BTCUSDT",
                        "features": {
                            "price": 100.0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "horizon": "short",
                        "score": 72,
                        "confidence": 0.62,
                        "reason_md": "spot long exposure candidate",
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    by_lane = {
        (candidate["market"], candidate["side"]): candidate
        for candidate in prompt["candidates"]
    }

    assert result["status"] == "ok"
    assert ("spot", "long") in by_lane
    assert ("futures", "long") in by_lane
    assert by_lane[("futures", "long")]["source_market"] == "spot"
    assert by_lane[("futures", "long")]["futures_shadow"] is True
    assert by_lane[("futures", "long")]["horizon"] == "futures"
    assert (
        by_lane[("futures", "long")]["calculated"]["market_inputs"]["book_source"]
        == "futures_book"
    )
    assert by_lane[("futures", "long")]["calculated"]["market_inputs"]["book_market"] == "futures"
    assert by_lane[("futures", "long")]["calculated"]["liquidation_price"] > 0
    assert by_lane[("futures", "long")]["calculated"]["margin_type"] == "isolated"
    assert prompt["candidate_generation"]["futures_shadow_candidate_count"] == 1
    assert (
        prompt["candidate_generation"]["raw_lane_distribution"]["items"]["futures:long"]["count"]
        == 1
    )


def test_manager_prompt_enriches_provided_candidates_with_live_book_ticker(
    tmp_path: Path,
) -> None:
    class BookBinance(_FakeBinance):
        def __init__(self) -> None:
            super().__init__()
            self.book_calls: list[tuple[str, str]] = []

        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            self.book_calls.append((market, symbol))
            books = {
                "spot": {
                    "bid_price": 250.0,
                    "ask_price": 251.0,
                    "spread_bps": 39.92,
                    "source": "spot_provided_book",
                },
                "futures": {
                    "bid_price": 260.0,
                    "ask_price": 261.0,
                    "spread_bps": 38.39,
                    "source": "futures_provided_book",
                },
            }
            return {
                "symbol": symbol,
                "market": market,
                "fetched_at": "2026-05-25T12:00:00+00:00",
                **books[market],
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "BNBUSDT",
                        "features": {
                            "price": 245.0,
                            "bid_price": 240.0,
                            "ask_price": 241.0,
                            "spread_bps": 40.0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                        },
                    }
                ],
                "candidates": [],
            }

    adapter = BookBinance()
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BNBUSDT",
                    "market": "spot",
                    "side": "long",
                    "price": 245.0,
                    "bid_price": 242.0,
                    "ask_price": 243.0,
                },
                {
                    "symbol": "BNBUSDT",
                    "market": "futures",
                    "side": "short",
                    "price": 245.0,
                    "bid_price": 242.0,
                    "ask_price": 243.0,
                },
            ]
        )
    )
    prompt = llm.calls[0]["payload"]
    by_market = {candidate["market"]: candidate for candidate in prompt["candidates"]}
    spot_inputs = by_market["spot"]["calculated"]["market_inputs"]
    futures_inputs = by_market["futures"]["calculated"]["market_inputs"]

    assert result["status"] == "ok"
    assert adapter.book_calls == [("spot", "BNBUSDT"), ("futures", "BNBUSDT")]
    assert spot_inputs["bid_price"] == pytest.approx(250.0)
    assert spot_inputs["ask_price"] == pytest.approx(251.0)
    assert spot_inputs["spread_bps"] == pytest.approx(39.92)
    assert spot_inputs["book_source"] == "spot_provided_book"
    assert spot_inputs["book_fresh"] is True
    assert by_market["spot"]["entry_price"] == pytest.approx(251.0)
    assert futures_inputs["bid_price"] == pytest.approx(260.0)
    assert futures_inputs["ask_price"] == pytest.approx(261.0)
    assert futures_inputs["spread_bps"] == pytest.approx(38.39)
    assert futures_inputs["book_source"] == "futures_provided_book"
    assert futures_inputs["book_fresh"] is True
    assert by_market["futures"]["entry_price"] == pytest.approx(260.0)
    assert prompt["candidate_generation"]["book_enriched_count"] == 2


def test_manager_prompt_rejects_crossed_live_book(tmp_path: Path) -> None:
    class CrossedBookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 101.0,
                "ask_price": 100.0,
                "spread_bps": 0,
                "source": "crossed_book",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [],
                "candidates": [],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=CrossedBookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "CROSSUSDT",
                    "market": "spot",
                    "side": "long",
                    "price": 100.5,
                }
            ]
        )
    )
    prompt = llm.calls[0]["payload"]
    candidate = prompt["candidates"][0]
    market_inputs = candidate["calculated"]["market_inputs"]

    assert result["status"] == "ok"
    assert "bid_price" not in candidate
    assert "ask_price" not in candidate
    assert market_inputs["bid_price"] == 0
    assert market_inputs["ask_price"] == 0
    assert market_inputs["spread_bps"] == 0
    assert market_inputs["book_fresh"] is False
    assert candidate["entry_price"] == pytest.approx(100.5)
    assert candidate["entry_price"] != pytest.approx(100.0)
    assert prompt["candidate_generation"]["book_enriched_count"] == 0
    assert prompt["candidate_generation"]["book_errors"][0]["error"] == "book_crossed"


def test_manager_prompt_enriches_all_provided_prompt_candidates_past_universe_cap(
    tmp_path: Path,
) -> None:
    class BookBinance(_FakeBinance):
        def __init__(self) -> None:
            super().__init__()
            self.book_calls: list[tuple[str, str]] = []

        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            self.book_calls.append((market, symbol))
            index = int(symbol[1:3])
            bid = 1_000.0 + index
            ask = bid + 0.5
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": bid,
                "ask_price": ask,
                "spread_bps": 4.99,
                "source": f"live_book_{symbol}",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [],
                "candidates": [],
            }

    provided = [
        {
            "symbol": f"T{index:02d}USDT",
            "market": "spot",
            "side": "long",
            "price": 100.0 + index,
            "bid_price": 1.0,
            "ask_price": 2.0,
            "spread_bps": 100.0,
        }
        for index in range(16)
    ]
    adapter = BookBinance()
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=provided))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert len(prompt["candidates"]) == 16
    assert adapter.book_calls == [("spot", row["symbol"]) for row in provided]
    for candidate in prompt["candidates"]:
        assert candidate["symbol"] in prompt["market_universe"][candidate["market"]]
    by_symbol = {candidate["symbol"]: candidate for candidate in prompt["candidates"]}
    for index, row in enumerate(provided):
        candidate = by_symbol[row["symbol"]]
        market_inputs = candidate["calculated"]["market_inputs"]
        assert market_inputs["bid_price"] == pytest.approx(1_000.0 + index)
        assert market_inputs["ask_price"] == pytest.approx(1_000.5 + index)
        assert market_inputs["book_source"] == f"live_book_T{index:02d}USDT"
        assert market_inputs["book_fresh"] is True
        assert candidate["entry_price"] == pytest.approx(1_000.5 + index)
    assert prompt["candidate_generation"]["book_enriched_count"] == 16


def test_manager_prompt_enriches_late_crypto_candidate_after_skipped_prefix(
    tmp_path: Path,
) -> None:
    class BookBinance(_FakeBinance):
        def __init__(self) -> None:
            super().__init__()
            self.book_calls: list[tuple[str, str]] = []

        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            self.book_calls.append((market, symbol))
            prices = {
                "DUPUSDT": (10.0, 10.1),
                "LATEUSDT": (20.0, 20.2),
            }
            bid, ask = prices[symbol]
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": bid,
                "ask_price": ask,
                "spread_bps": 99.5,
                "source": f"live_{symbol}",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            duplicate = {
                "symbol": "DUPUSDT",
                "market": "spot",
                "stance": "long_watch",
                "score": 80,
            }
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "DUPUSDT",
                        "features": {
                            "price": 10.05,
                            "bid_price": 1.0,
                            "ask_price": 2.0,
                            "entry_quality": "actionable_now",
                        },
                    },
                    {
                        "symbol": "LATEUSDT",
                        "features": {
                            "price": 20.1,
                            "bid_price": 3.0,
                            "ask_price": 4.0,
                            "entry_quality": "actionable_now",
                        },
                    },
                ],
                "candidates": [
                    dict(duplicate)
                    for _ in range(8)
                ]
                + [
                    {
                        "symbol": "LATEUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "score": 79,
                    }
                ],
            }

    adapter = BookBinance()
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=adapter, llm=llm, quant_context_limit=8)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    by_symbol = {candidate["symbol"]: candidate for candidate in prompt["candidates"]}
    late_inputs = by_symbol["LATEUSDT"]["calculated"]["market_inputs"]

    assert result["status"] == "ok"
    assert ("spot", "LATEUSDT") in adapter.book_calls
    assert late_inputs["bid_price"] == pytest.approx(20.0)
    assert late_inputs["ask_price"] == pytest.approx(20.2)
    assert late_inputs["book_source"] == "live_LATEUSDT"
    assert late_inputs["book_fresh"] is True
    assert by_symbol["LATEUSDT"]["entry_price"] == pytest.approx(20.2)


def test_manager_prompt_ignores_stale_bid_ask_when_live_book_missing(
    tmp_path: Path,
) -> None:
    class MissingBookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": None,
                "ask_price": None,
                "source": "missing_book",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "STALEUSDT",
                        "features": {
                            "price": 50.0,
                            "bid_price": 50.7,
                            "ask_price": 50.8,
                            "spread_bps": 1.97,
                            "book_source": "stale_research_book",
                            "book_fetched_at": "2026-05-24T00:00:00+00:00",
                            "book_market": "spot",
                            "book_fresh": True,
                            "entry_quality": "actionable_now",
                        },
                    }
                ],
                "candidates": [],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=MissingBookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "STALEUSDT",
                    "market": "spot",
                    "side": "long",
                    "price": 50.0,
                    "bid_price": 51.0,
                    "ask_price": 52.0,
                }
            ]
        )
    )
    prompt = llm.calls[0]["payload"]
    candidate = prompt["candidates"][0]
    market_inputs = candidate["calculated"]["market_inputs"]

    assert result["status"] == "ok"
    assert "bid_price" not in candidate
    assert "ask_price" not in candidate
    assert market_inputs["bid_price"] == 0
    assert market_inputs["ask_price"] == 0
    assert market_inputs["spread_bps"] == 0
    assert market_inputs["book_source"] == ""
    assert market_inputs["book_fetched_at"] == ""
    assert market_inputs["book_market"] == ""
    assert market_inputs["book_fresh"] is False
    assert candidate["entry_price"] == pytest.approx(50.0)
    assert candidate["entry_price"] != pytest.approx(50.8)
    assert candidate["entry_price"] != pytest.approx(52.0)
    assert prompt["candidate_generation"]["book_enriched_count"] == 0


def test_manager_prompt_derives_last_price_from_fresh_book_for_provided_candidate(
    tmp_path: Path,
) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 30.0,
                "ask_price": 30.2,
                "spread_bps": 66.45,
                "source": "fresh_book_only",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [],
                "candidates": [],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(
        trader.run_manager_once(
            candidates=[
                {
                    "symbol": "BOOKUSDT",
                    "market": "spot",
                    "side": "long",
                    "bid_price": 1.0,
                    "ask_price": 2.0,
                }
            ]
        )
    )
    prompt = llm.calls[0]["payload"]
    candidate = prompt["candidates"][0]
    market_inputs = candidate["calculated"]["market_inputs"]

    assert result["status"] == "ok"
    assert "data_gaps" not in candidate
    assert market_inputs["last_price"] == pytest.approx(30.1)
    assert market_inputs["bid_price"] == pytest.approx(30.0)
    assert market_inputs["ask_price"] == pytest.approx(30.2)
    assert market_inputs["book_source"] == "fresh_book_only"
    assert market_inputs["book_fresh"] is True
    assert candidate["entry_price"] == pytest.approx(30.2)


def test_manager_prompt_includes_crypto_alpha_context(tmp_path: Path) -> None:
    class CryptoAlpha:
        def context_pack(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "scope": "binance_crypto_alpha",
                "events": [
                    {
                        "event_id": 1,
                        "event_type": "listing",
                        "title": "Binance Will List ACME",
                        "symbols": ["BTCUSDT"],
                    }
                ],
                "scorecards": [{"pattern_key": "binance_announcements:listing"}],
                "active_lessons": ["listing catalysts need entry discipline"],
                "data_gaps": [],
                "symbols": symbols or [],
                "limit": limit,
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_alpha_provider = CryptoAlpha()

    result = asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert "crypto_alpha" not in prompt
    assert prompt["raw_context_refs"]["crypto_alpha"]["event_count"] == 1
    assert "BTCUSDT" in prompt["raw_context_refs"]["crypto_alpha"]["requested_symbols"]
    assert "decision_packet" in prompt["decision_inputs"]
    assert "crypto_alpha" not in prompt["decision_inputs"]


def test_live_spot_exit_uses_real_adapter_limit_signature(tmp_path: Path) -> None:
    class StrictSpotBinance(_FakeBinance):
        async def submit_spot_order(
            self,
            *,
            symbol: str,
            side: str,
            quantity: float,
            limit_price: float,
            client_order_id: str,
            time_in_force: str = "IOC",
        ) -> dict[str, Any]:
            self.spot_orders.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "client_order_id": client_order_id,
                    "time_in_force": time_in_force,
                }
            )
            return {"status": "FILLED", "order_id": "S1", "executed_qty": quantity}

    adapter = StrictSpotBinance()
    adapter.account = {
        "status": "ok",
        "spot_assets": [
            {
                "asset": "BTC",
                "symbol": "BTCUSDT",
                "qty": 0.25,
                "available": 0.25,
                "locked": 0.0,
            }
        ],
    }
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.25,
            "qty_open": 0.25,
            "entry_price": 100.0,
            "target_price": 101.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 101.5

    result = asyncio.run(trader.executor_tick())
    updated = trader.get_block(block["block_id"])

    assert result["action_count"] == 1
    assert adapter.spot_orders[0]["limit_price"] == pytest.approx(101.297)
    assert adapter.spot_orders[0]["client_order_id"]
    assert updated is not None
    assert updated["status"] == "closed"


def test_enabled_false_prevents_live_order_even_if_flag_is_on(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=False)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "qty_open": 0.1,
            "target_price": 101.0,
            "stop_price": 90.0,
            "status": "open",
        }
    )
    adapter.prices["BTCUSDT"] = 102.0

    result = asyncio.run(trader.executor_tick())

    assert result["action_count"] == 1
    assert adapter.spot_orders == []
    assert trader.repository.list_orders()[0]["status"] == "paper"


def test_kill_switch_persists_across_service_instances(tmp_path: Path) -> None:
    db_path = str(tmp_path / "binance_blocks.db")
    first = BinanceBlockTrader(
        config=BinanceBlockTraderConfig(db_path=db_path),
        adapter=_FakeBinance(),
        codex_runtime=_FakeLLM({"create_blocks": []}),
    )
    first.set_kill_switch(True, reason="shared")

    second = BinanceBlockTrader(
        config=BinanceBlockTraderConfig(db_path=db_path),
        adapter=_FakeBinance(),
        codex_runtime=_FakeLLM({"create_blocks": []}),
    )

    assert second.kill_switch()["enabled"] is True
    assert second.kill_switch()["reason"] == "shared"


def test_manager_plan_shaped_schema_creates_block(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "venue": "spot",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quote_budget_usdt": 100,
                    "entry_price_usdt": 100,
                    "target_price_usdt": 110,
                    "stop_price_usdt": 95,
                    "thesis": "plan-shaped output",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        }
    )
    trader = _trader(tmp_path, llm=llm)

    result = asyncio.run(trader.run_manager_once())
    block = trader.list_blocks()[0]

    assert result["status"] == "ok"
    assert block["market"] == "spot"
    assert block["qty_initial"] == pytest.approx(1.0)
    assert block["target_price"] == pytest.approx(110.0)
    assert block["stop_price"] == pytest.approx(95.0)


def test_manager_create_block_uses_risk_sizer_quantity(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.05,
                "risk_budget_usdt": 25.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 9.0,
                        "thesis": "test",
                    }
                ]
            }
        ),
    )
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert block["qty_initial"] == pytest.approx(0.05)
    assert block["metadata"]["risk_sizing"]["risk_budget_usdt"] == pytest.approx(25.0)
    assert calls[0]["account_equity_usdt"] == pytest.approx(10_000.0)
    assert calls[0]["proposed_qty"] == pytest.approx(9.0)
    assert calls[0]["lane"] == "short"


def test_manager_create_block_preserves_top_level_jue_wiki_decision_adjustment_metadata(
    tmp_path: Path,
) -> None:
    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 0.01,
                        "thesis": "위키 힌트 보존",
                        "jue_wiki_decision_adjustments": [
                            {
                                "action": "audit_or_repair_probe_only",
                                "execution_hint": "cap_to_audit_or_repair_probe",
                                "evidence_grade": {"status": "negative"},
                            }
                        ],
                        "jue_wiki_decision_adjustment_resolution": {
                            "status": "repair_probe",
                            "action": "create_spot_probe",
                            "reason": "cap_to_audit_or_repair_probe respected",
                        },
                    }
                ]
            }
        ),
    )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert block["metadata"]["jue_wiki_decision_adjustments"][0]["execution_hint"] == (
        "cap_to_audit_or_repair_probe"
    )
    assert block["metadata"]["jue_wiki_decision_adjustment_resolution"] == {
        "status": "repair_probe",
        "action": "create_spot_probe",
        "reason": "cap_to_audit_or_repair_probe respected",
    }


def test_manager_create_block_attaches_prompt_jue_wiki_decision_adjustments(
    tmp_path: Path,
) -> None:
    def wiki_context_provider(**_: object) -> dict[str, object]:
        return {
            "status": "ok",
            "selection_run_id": "selection:binance-adjust-auto",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "binance",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 16,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "status": "degraded",
                                "sample_count": 8,
                                "avg_return_pct": -0.35,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "status": "active",
                                "sample_count": 5,
                                "avg_return_pct": 0.24,
                            },
                        ],
                        "decision_adjustment_metrics": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "repair_probe",
                                "reason": "current_risk_posture_degraded",
                                "status": "active",
                                "sample_count": 5,
                                "avg_return_pct": 0.24,
                                "confidence": 0.75,
                            }
                        ],
                    }
                ],
            },
            "pages": [{"page_id": "binance.playbook.supporting"}],
            "budget_report": {"selected_count": 1},
        }

    trader = _trader(
        tmp_path,
        wiki_context_provider=wiki_context_provider,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 0.01,
                        "thesis": "프롬프트 위키 힌트 자동 보존",
                        "metadata": {
                            "jue_wiki_decision_adjustment_resolution": {
                                "status": "repair_probe",
                                "action": "create_spot_probe",
                                "reason": (
                                    "shift_to_preferred_risk_posture toward "
                                    "repair_probe with usable_with_live_cross_check"
                                ),
                            }
                        },
                    }
                ]
            }
        ),
    )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert block["metadata"]["jue_wiki_decision_adjustments"][0]["target_risk_posture"] == (
        "repair_probe"
    )
    assert block["metadata"]["jue_wiki_decision_adjustments"][0]["evidence_grade"][
        "instruction"
    ] == "usable_with_live_cross_check"
    assert block["metadata"]["jue_wiki_decision_adjustment_resolution"]["status"] == (
        "repair_probe"
    )


def test_manager_risk_sizer_uses_live_authority_budget_multiplier(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 9.0,
                        "thesis": "test",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.25,
        "scorecard_count": 1,
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
            "risk_governor_action": "de_risk",
            "risk_governor_source": "kelly_sizing",
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert block["qty_initial"] == pytest.approx(0.01)
    assert calls[0]["account_equity_usdt"] == pytest.approx(2_500.0)
    assert block["metadata"]["risk_sizing"]["raw_account_equity_usdt"] == (
        pytest.approx(10_000.0)
    )
    assert block["metadata"]["risk_sizing"]["live_authority_budget_multiplier"] == (
        pytest.approx(0.25)
    )
    assert block["metadata"]["risk_sizing"]["live_authority_adjusted_equity_usdt"] == (
        pytest.approx(2_500.0)
    )
    assert block["metadata"]["risk_sizing"]["sizing_equity_usdt"] == (
        pytest.approx(2_500.0)
    )


def test_manager_risk_sizer_reduces_budget_for_execution_defect_risk(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "horizon": "short",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 9.0,
                        "thesis": "test",
                    }
                ]
            }
        ),
    )
    malformed = trader.repository.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 1800.0,
            "target_price": 1836.0,
            "stop_price": 1782.0,
            "status": "closed",
            "created_by": "llm",
            "closed_at": "2026-06-18T01:00:00+00:00",
            "metadata": {
                "horizon": "futures",
                "block_color": "futures",
                "market_or_account_scope": "binance:futures",
            },
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": malformed["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 1800.0,
            "exit_price": 1782.0,
            "stop_price": 1782.0,
            "target_price": 1836.0,
            "pnl_usdt": -10.0,
            "net_pnl_usdt": -10.0,
            "r_multiple": -1.0,
            "lesson": {"result": "negative"},
        }
    )
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]
    sizing = block["metadata"]["risk_sizing"]

    assert result["status"] == "ok"
    assert calls[0]["account_equity_usdt"] == pytest.approx(5_000.0)
    assert sizing["execution_defect_budget_multiplier"] == pytest.approx(0.5)
    assert sizing["execution_defect_risk"]["status"] == "elevated"
    assert sizing["sizing_equity_usdt"] == pytest.approx(5_000.0)


def test_manager_risk_sizer_does_not_apply_spot_execution_defect_to_futures_short(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 9.0,
                        "thesis": "futures short should not inherit unrelated spot execution defects",
                    }
                ]
            }
        ),
    )
    malformed_spot = trader.repository.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 1800.0,
            "target_price": 1836.0,
            "stop_price": 1782.0,
            "status": "closed",
            "created_by": "llm",
            "closed_at": "2026-06-18T01:00:00+00:00",
            "metadata": {
                "horizon": "futures",
                "block_color": "futures",
                "market_or_account_scope": "binance:futures",
            },
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": malformed_spot["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 1800.0,
            "exit_price": 1782.0,
            "stop_price": 1782.0,
            "target_price": 1836.0,
            "pnl_usdt": -10.0,
            "net_pnl_usdt": -10.0,
            "r_multiple": -1.0,
            "lesson": {"result": "negative"},
        }
    )
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]
    sizing = block["metadata"]["risk_sizing"]

    assert result["status"] == "ok"
    assert calls[0]["account_equity_usdt"] == pytest.approx(10_000.0)
    assert sizing["execution_defect_budget_multiplier"] == pytest.approx(1.0)
    assert sizing["execution_defect_risk"]["status"] == "scoped_clear"
    assert sizing["sizing_equity_usdt"] == pytest.approx(10_000.0)


def test_manager_risk_sizer_uses_lane_authority_budget_multiplier(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 9.0,
                        "margin_type": "isolated",
                        "leverage": 2,
                        "liquidation_price": 60_000,
                        "thesis": "weak futures short lane should stay a small probe",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 10,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["futures:short"],
            "insufficient_lanes": [],
        },
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert calls[0]["account_equity_usdt"] == pytest.approx(2_500.0)
    assert block["metadata"]["lane_authority_gate"]["budget_multiplier"] == pytest.approx(
        0.25
    )
    assert block["metadata"]["risk_sizing"]["lane_authority_budget_multiplier"] == (
        pytest.approx(0.25)
    )


def test_manager_risk_sizer_prefers_applied_lane_authority_budget_multiplier(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 9.0,
                        "margin_type": "isolated",
                        "leverage": 2,
                        "liquidation_price": 60_000,
                        "thesis": "weak futures short lane should use live authority applied budget",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 10,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["futures:short"],
            "insufficient_lanes": [],
            "lane_actions": {
                "futures:short": {
                    "grade": "observe_only",
                    "action": "observe_or_waiting_entry",
                    "max_budget_multiplier": 0.5,
                    "applied_max_budget_multiplier": 0.4,
                    "scale_up_allowed": False,
                }
            },
        },
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert calls[0]["account_equity_usdt"] == pytest.approx(4_000.0)
    assert block["metadata"]["lane_authority_gate"]["budget_multiplier"] == pytest.approx(
        0.4
    )
    assert block["metadata"]["lane_authority_gate"]["applied_max_budget_multiplier"] == (
        pytest.approx(0.4)
    )
    assert block["metadata"]["risk_sizing"]["lane_authority_budget_multiplier"] == (
        pytest.approx(0.4)
    )
    assert block["metadata"]["risk_sizing"]["raw_account_equity_usdt"] == (
        pytest.approx(10_000.0)
    )
    assert block["metadata"]["risk_sizing"]["live_authority_budget_multiplier"] == (
        pytest.approx(1.0)
    )
    assert block["metadata"]["risk_sizing"]["live_authority_adjusted_equity_usdt"] == (
        pytest.approx(10_000.0)
    )
    assert block["metadata"]["risk_sizing"]["sizing_equity_usdt"] == (
        pytest.approx(4_000.0)
    )


def test_recent_futures_short_recovery_lifts_lane_authority_probe_floor(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(
        tmp_path,
        adapter=adapter,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "DOGEUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 0.083,
                        "entry_trigger_operator": ">=",
                        "entry_price": 0.083,
                        "target_price": 0.081,
                        "stop_price": 0.084,
                        "quote_budget_usdt": 200.0,
                        "margin_type": "isolated",
                        "leverage": 1,
                        "liquidation_price": 0.112,
                        "thesis": "recently recovered futures short lane should not be trapped in stale 0.1 validation probe sizing.",
                    }
                ]
            }
        ),
        execute_futures=False,
        enabled=True,
    )
    trader.config.lane_performance_min_samples = 3
    trader.config.performance_scorecard_feedback_limit = 120
    trader.config.futures_universe = "DOGEUSDT"
    for index in range(6):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"recent-short-recovery-{index}",
                "symbol": "RECENTUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures",
                "entry_price": 100.0,
                "exit_price": 95.0,
                "stop_price": 105.0,
                "target_price": 90.0,
                "pnl_usdt": 1.0,
                "r_multiple": 1.0,
                "created_at": f"2026-02-01T00:{index:02d}:00+00:00",
            }
        )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 10,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["futures:short"],
            "insufficient_lanes": [],
            "lane_actions": {
                "futures:short": {
                    "grade": "restricted",
                    "action": "validation_evidence_repair_waiting_probe",
                    "max_budget_multiplier": 0.25,
                    "applied_max_budget_multiplier": 0.1,
                    "risk_budget_passport": {
                        "effective_risk_budget_multiplier": 0.1,
                        "budget_status": "reduced",
                        "reasons": ["stale validation evidence"],
                    },
                    "scale_blockers": ["validation_evidence_repair"],
                    "validation_missing_dimensions": ["live_shadow"],
                    "scale_up_allowed": False,
                }
            },
        },
        "validation_gate": {
            "status": "validation_normal",
            "readiness": "normal",
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["DOGEUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]
    metadata = block["metadata"]
    sizing = metadata["risk_sizing"]
    adjustment = metadata["lane_authority_budget_adjustment"]

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert metadata["lane_authority_gate"]["budget_multiplier"] == pytest.approx(0.25)
    assert metadata["lane_authority_gate"]["budget_multiplier_floor"]["reason"] == (
        "recent_lane_recovery"
    )
    assert adjustment["budget_multiplier"] == pytest.approx(0.25)
    assert adjustment["to_quote_budget_usdt"] == pytest.approx(50.0)
    assert calls[0]["account_equity_usdt"] == pytest.approx(2_500.0)
    assert calls[0]["proposed_qty"] == pytest.approx(50.0 / 0.083)
    assert sizing["lane_authority_budget_multiplier"] == pytest.approx(0.25)
    assert sizing["sizing_equity_usdt"] == pytest.approx(2_500.0)


def test_manager_risk_sizer_uses_risk_budget_passport_as_final_lane_cap(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ok",
                "qty": 0.01,
                "risk_budget_usdt": 5.0,
                "reward_risk": 2.0,
            }

    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 50_000,
                        "entry_trigger_operator": ">=",
                        "entry_price": 50_000,
                        "target_price": 48_000,
                        "stop_price": 51_000,
                        "qty": 9.0,
                        "margin_type": "isolated",
                        "leverage": 2,
                        "liquidation_price": 60_000,
                        "thesis": "risk budget passport should be the final Binance lane cap.",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 10,
        "lane_authority": {
            "version": "lane_authority_v1",
            "weak_lanes": ["futures:short"],
            "insufficient_lanes": [],
            "lane_actions": {
                "futures:short": {
                    "grade": "observe_only",
                    "action": "observe_or_waiting_entry",
                    "max_budget_multiplier": 0.5,
                    "applied_max_budget_multiplier": 0.4,
                    "risk_budget_passport": {
                        "effective_risk_budget_multiplier": 0.25,
                        "budget_status": "reduced",
                        "reasons": ["weak live expectancy"],
                        "risk_of_ruin_pct": 5.5,
                        "recommended_risk_fraction": 0.004,
                    },
                    "scale_up_allowed": False,
                }
            },
        },
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
        },
    }
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]
    lane_gate = block["metadata"]["lane_authority_gate"]

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "waiting_entry"
    assert calls[0]["account_equity_usdt"] == pytest.approx(2_500.0)
    assert lane_gate["budget_multiplier"] == pytest.approx(0.25)
    assert lane_gate["budget_multiplier_source"] == "risk_budget_passport"
    assert lane_gate["risk_budget_passport_multiplier"] == pytest.approx(0.25)
    assert lane_gate["risk_budget_passport_status"] == "reduced"
    assert lane_gate["risk_budget_passport_reasons"] == ["weak live expectancy"]
    assert lane_gate["risk_of_ruin_pct"] == pytest.approx(5.5)
    assert lane_gate["recommended_risk_fraction"] == pytest.approx(0.004)
    assert block["metadata"]["risk_sizing"]["lane_authority_budget_multiplier"] == (
        pytest.approx(0.25)
    )
    assert block["metadata"]["risk_sizing"]["sizing_equity_usdt"] == (
        pytest.approx(2_500.0)
    )


def test_manager_rejects_new_binance_block_when_risk_governor_halts_even_if_gate_clear(
    tmp_path: Path,
) -> None:
    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_style": "wait_for_price",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 0.1,
                        "thesis": "risk governor halt should dominate clear gate",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "scale_candidate",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.0,
        "scorecard_count": 1,
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
            "risk_governor_action": "halt_new_risk",
            "risk_governor_source": "ruin_profile",
        },
    }

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == (
        "live_authority_risk_governor:halt_new_risk"
    )
    assert trader.list_blocks() == []


def test_manager_rejects_immediate_binance_block_when_risk_governor_is_risk_off(
    tmp_path: Path,
) -> None:
    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_style": "immediate",
                        "entry_price": 100.0,
                        "target_price": 104.0,
                        "stop_price": 98.0,
                        "qty": 0.1,
                        "thesis": "diagnostic fail risk_off 상태에서는 즉시진입하지 않는다.",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "restricted",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.25,
        "scorecard_count": 4,
        "validation_gate": {
            "status": "validation_normal",
            "readiness": "normal",
            "reason": "diagnostic failures only; hard gate clear",
            "risk_governor_action": "risk_off",
            "risk_governor_source": "kelly_sizing",
            "risk_governor_reasons": [
                "kelly_sizing:halt_new_risk",
                "hard_gate_clear:diagnostic_halt_demoted_to_risk_off",
            ],
            "failed_disciplines": [
                {"id": "monte_carlo", "label": "몬테카를로", "status": "fail"},
                {"id": "profit_factor", "label": "수익팩터", "status": "fail"},
            ],
        },
    }

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))

    assert result["status"] == "ok"
    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == "live_authority_requires_waiting_entry:risk_governor:risk_off"
    assert trader.list_blocks() == []


def test_manager_rejects_immediate_binance_block_when_exposure_gate_needs_waiting(
    tmp_path: Path,
) -> None:
    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "futures",
                        "side": "long",
                        "entry_style": "immediate",
                        "entry_price": 100.0,
                        "target_price": 104.0,
                        "stop_price": 98.0,
                        "qty": 0.1,
                        "thesis": "상관/팩터 검증 전 새 futures long을 즉시 진입하려 한다.",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": False,
        "max_budget_multiplier": 1.0,
        "scorecard_count": 0,
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
        },
        "lane_authority": {
            "version": "lane_authority_v1",
            "global_scale_up_allowed": False,
            "validation_exposure_gate": {
                "status": "regime_correlation_factor_review_required",
                "blocks_scale_up": True,
                "requires_waiting_entry": True,
                "cap_multiplier": 0.75,
            },
            "lane_actions": {},
        },
    }

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))

    assert result["status"] == "ok"
    created = result["applied"]["created"][0]
    assert created["status"] == "rejected"
    assert created["reason"] == (
        "live_authority_requires_waiting_entry:"
        "validation_exposure_gate:regime_correlation_factor_review_required"
    )
    assert trader.list_blocks() == []


def test_manager_rejects_new_binance_block_when_live_authority_budget_is_zero(
    tmp_path: Path,
) -> None:
    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_style": "wait_for_price",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 0.1,
                        "thesis": "zero budget should block new risk",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "ok",
        "live_grade": "qualified",
        "allow_scale_up": False,
        "max_budget_multiplier": 0.0,
        "scorecard_count": 1,
        "validation_gate": {
            "status": "clear",
            "readiness": "scale_ready",
        },
    }

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "live_authority_budget_zero"
    assert trader.list_blocks() == []


def test_manager_rejects_new_binance_block_when_live_authority_errors(
    tmp_path: Path,
) -> None:
    trader = _trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_style": "wait_for_price",
                        "entry_price": 50_000,
                        "target_price": 51_000,
                        "stop_price": 49_500,
                        "qty": 0.1,
                        "thesis": "live authority error should reject",
                    }
                ]
            }
        ),
    )
    trader.live_authority_provider = lambda: {
        "status": "error",
        "error_message": "live evaluator timeout",
    }

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))

    assert result["status"] == "ok"
    assert result["applied"]["created"][0]["status"] == "rejected"
    assert result["applied"]["created"][0]["reason"] == "live_authority_error"
    assert trader.list_blocks() == []


def test_closed_block_generates_performance_reflection(tmp_path: Path) -> None:
    class QuantProvider:
        def __init__(self) -> None:
            self.outcomes: list[dict[str, Any]] = []

        def save_outcome(self, payload: dict[str, Any]) -> None:
            self.outcomes.append(payload)

    quant = QuantProvider()
    trader = _trader(tmp_path, quant_provider=quant)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100,
            "target_price": 110,
            "stop_price": 95,
            "status": "open",
            "thesis": "trend pullback",
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.save_quotes(
        [
            {
                "symbol": "BTCUSDT",
                "market": "spot",
                "price": price,
                "source": "test",
                "fetched_at": block["created_at"],
            }
            for price in [97, 108, 112]
        ]
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 108, "reason": "target_near"},
    )

    result = trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert result["status"] == "ok"
    assert result["reflection_count"] == 1
    assert detail is not None
    assert detail["performance_reflection"]["r_multiple"] == pytest.approx(1.6)
    assert detail["performance_reflection"]["mfe_r_multiple"] == pytest.approx(2.4)
    assert detail["performance_reflection"]["mae_r_multiple"] == pytest.approx(-0.6)
    assert scorecard["sample_count"] == 1
    assert scorecard["avg_r_multiple"] == pytest.approx(1.6)
    assert scorecard["avg_mfe_r_multiple"] == pytest.approx(2.4)
    assert scorecard["pattern_scorecards"][0]["sample_count"] == 1
    assert scorecard["win_rate_pct"] == pytest.approx(100.0)
    assert quant.outcomes[0]["symbol"] == "BTCUSDT"
    assert quant.outcomes[0]["outcome"] == "closed_positive"
    assert quant.outcomes[0]["r_multiple"] == pytest.approx(1.6)


def test_existing_position_reflection_tracks_management_without_entry_alpha(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 1.5,
            "qty_open": 1.5,
            "entry_price": 90.0,
            "target_price": 84.0,
            "stop_price": 93.0,
            "liquidation_price": 126.0,
            "margin_type": "isolated",
            "status": "open",
            "created_by": "existing_position",
            "thesis": "기존 BTCUSDT 숏 포지션을 쥬가 목표/손절로 관리한다.",
            "metadata": {
                "adoption": {
                    "source": "manager_existing_position",
                    "adopted_qty": 1.5,
                }
            },
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "qty_open": 0.0, "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 1.5,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 84.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 84.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])
    scorecard = trader.repository.latest_performance_scorecard(limit=20)
    live_row = next(
        item
        for item in LivePerformanceRepository(tmp_path / "live_performance.db").latest(
            venue="binance",
            limit=5,
        )
        if item["block_id"] == block["block_id"]
    )
    live_source = json.loads(live_row["source_json"])

    assert result["reflection_count"] == 1
    assert result["live_performance_count"] == 1
    assert detail is not None
    reflection = detail["performance_reflection"]
    assert reflection["r_multiple"] == pytest.approx(2.0)
    assert reflection["lesson"]["entry_attribution"] == "external_existing_position"
    assert reflection["lesson"]["management_attribution"] == "jue_block_management"
    assert reflection["lesson"]["scorecard_attribution"] == "risk_management_only"
    assert reflection["lesson"]["created_by"] == "existing_position"
    assert scorecard["sample_count"] == 0
    assert scorecard["window"]["excluded_created_by"] == [
        "wallet_adoption",
        "existing_position",
    ]
    assert live_row["attribution"] == "adopted_existing_position"
    assert live_row["include_in_jue_alpha"] == 0
    assert live_row["include_in_risk_management"] == 1
    assert live_source["metadata"]["entry_attribution"] == "external_existing_position"
    assert live_source["metadata"]["management_attribution"] == "jue_block_management"


def test_closed_block_reflection_preserves_quant_pattern_key(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 96.0,
            "stop_price": 102.0,
            "liquidation_price": 135.0,
            "status": "open",
            "metadata": {
                "calculated_price_plan": {
                    "pattern_inputs": {
                        "prior": {
                            "pattern_key": "bollinger_squeeze:short:1h",
                            "symbol": "BTCUSDT",
                        }
                    }
                }
            },
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 98.0, "reason": "target_near"},
    )

    result = trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert result["reflection_count"] == 1
    assert detail["performance_reflection"]["pattern_key"] == (
        "bollinger_squeeze:short:1h"
    )
    assert scorecard["pattern_scorecards"][0]["pattern_key"] == (
        "bollinger_squeeze:short:1h"
    )
    assert scorecard["pattern_scorecards"][0]["pnl_usdt"] == pytest.approx(
        detail["performance_reflection"]["pnl_usdt"]
    )
    assert scorecard["pattern_scorecards"][0]["profit_factor"] > 1.0


def test_reconciled_missing_asset_close_is_excluded_from_performance(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "status": "open",
            "metadata": {
                "exit_reconciled_missing_asset": {
                    "status": "missing_asset_confirmed",
                    "previous_qty_open": 0.1,
                }
            },
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "qty_open": 0.0, "closed_at": block["created_at"]},
    )
    trader.repository.add_event(
        block["block_id"],
        "exit_reconciled_missing_asset",
        payload={"exit_price": 90.0},
    )

    result = trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])

    assert result["reflection_count"] == 0
    assert detail is not None
    assert detail["performance_reflection"] is None

    trader.repository.save_performance_reflection(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 90.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "pnl_usdt": -1.0,
            "net_pnl_usdt": -1.0,
            "r_multiple": -2.0,
            "created_at": block["created_at"],
        }
    )
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert scorecard["sample_count"] == 0
    assert scorecard["window"]["excluded_execution_defect_count"] == 1
    assert scorecard["window"]["excluded_execution_defect_pnl_usdt"] == pytest.approx(-1.0)


def test_reconciled_missing_asset_demotes_existing_live_alpha_sample(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "liquidation_price": 70.0,
            "margin_type": "isolated",
            "status": "open",
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "qty_open": 0.0, "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "buy",
            "qty": 0.1,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "sell",
            "qty": 0.1,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "stop_reached",
            "response": {"status": "FILLED", "avg_fill_price": 90.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 90.0, "reason": "stop_reached"},
    )

    first = trader.run_performance_feedback_once()
    first_row = next(
        item
        for item in LivePerformanceRepository(tmp_path / "live_performance.db").latest(
            venue="binance",
            limit=5,
        )
        if item["block_id"] == block["block_id"]
    )

    updated_block = trader.get_block(block["block_id"])
    assert updated_block is not None
    trader.repository.update_block(
        block["block_id"],
        {
            "metadata": {
                **updated_block["metadata"],
                "exit_reconciled_missing_asset": {
                    "status": "missing_asset_confirmed",
                    "previous_qty_open": 0.1,
                },
            },
        },
    )

    second = trader.run_performance_feedback_once()
    second_row = next(
        item
        for item in LivePerformanceRepository(tmp_path / "live_performance.db").latest(
            venue="binance",
            limit=5,
        )
        if item["block_id"] == block["block_id"]
    )
    second_source = json.loads(second_row["source_json"])

    assert first["live_performance_count"] == 1
    assert first_row["include_in_jue_alpha"] == 1
    assert second["live_performance_count"] == 1
    assert second_row["include_in_jue_alpha"] == 0
    assert second_row["include_in_risk_management"] == 1
    assert second_row["include_in_execution_quality"] == 1
    assert second_row["attribution"] == "execution_defect_reconciliation_only_close"
    assert second_source["metadata"]["execution_defect"] is True
    assert second_source["metadata"]["execution_defect_reason"] == "reconciliation_only_close"


def test_performance_reflection_uses_original_stop_after_profit_lock(
    tmp_path: Path,
) -> None:
    class FakeQuant:
        def __init__(self) -> None:
            self.outcomes: list[dict[str, Any]] = []

        def save_outcome(self, payload: dict[str, Any]) -> None:
            self.outcomes.append(payload)

    quant = FakeQuant()
    trader = _trader(tmp_path, quant_provider=quant)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 110.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "open",
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {
            "status": "closed",
            "stop_price": 99.5,
            "closed_at": block["created_at"],
            "metadata": {
                **block["metadata"],
                "profit_lock_original_stop_price": 110.0,
            },
        },
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 95.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 95.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])

    assert result["status"] == "ok"
    assert result["reflection_count"] == 1
    assert len(quant.outcomes) == 1
    assert detail is not None
    assert detail["performance_reflection"]["gross_pnl_usdt"] == pytest.approx(5.0)
    assert detail["performance_reflection"]["total_cost_usdt"] == pytest.approx(0.14)
    assert detail["performance_reflection"]["pnl_usdt"] == pytest.approx(4.86)
    assert detail["performance_reflection"]["r_multiple"] == pytest.approx(0.5)
    assert detail["performance_reflection"]["mfe_r_multiple"] == pytest.approx(0.5)
    assert detail["performance_reflection"]["lesson"]["risk_stop_price"] == pytest.approx(110.0)
    assert detail["performance_reflection"]["lesson"]["final_stop_price"] == pytest.approx(99.5)

    stale_reflection = dict(detail["performance_reflection"])
    stale_reflection["r_multiple"] = 10.0
    trader.repository.save_performance_reflection(stale_reflection)
    skipped = trader.run_performance_feedback_once()
    stale_detail = trader.block_detail(block["block_id"])
    refreshed = trader.run_performance_feedback_once(refresh_existing=True)
    refreshed_detail = trader.block_detail(block["block_id"])

    assert skipped["reflection_count"] == 0
    assert stale_detail is not None
    assert stale_detail["performance_reflection"]["r_multiple"] == pytest.approx(10.0)
    assert refreshed["reflection_count"] == 1
    assert refreshed_detail is not None
    assert refreshed_detail["performance_reflection"]["r_multiple"] == pytest.approx(0.5)
    assert len(quant.outcomes) == 1


def test_closed_block_without_effective_entry_is_excluded_from_performance(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 0.01,
            "entry_price": 100.0,
            "target_price": 95.0,
            "stop_price": 103.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "proposed",
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 0.01,
            "order_type": "LIMIT_IOC",
            "status": "error",
            "reason": "entry_order",
            "response": {"status": "error", "error_message": "notional below minimum"},
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 101.0, "reason": "manager_close_after_error"},
    )

    result = trader.run_performance_feedback_once()
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert result["status"] == "ok"
    assert result["reflection_count"] == 0
    assert scorecard["sample_count"] == 0


def test_performance_reflection_tracks_net_pnl_after_explicit_costs(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.0,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 104.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "open",
            "metadata": {
                "fees_usdt": 0.10,
                "funding_usdt": 0.02,
                "slippage_usdt": 0.03,
                "spread_usdt": 0.04,
            },
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 90.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 90.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert result["status"] == "ok"
    assert result["reflection_count"] == 1
    assert detail["performance_reflection"]["gross_pnl_usdt"] == pytest.approx(20.0)
    assert detail["performance_reflection"]["fee_usdt"] == pytest.approx(0.10)
    assert detail["performance_reflection"]["funding_usdt"] == pytest.approx(0.02)
    assert detail["performance_reflection"]["slippage_usdt"] == pytest.approx(0.03)
    assert detail["performance_reflection"]["spread_usdt"] == pytest.approx(0.04)
    assert detail["performance_reflection"]["total_cost_usdt"] == pytest.approx(0.19)
    assert detail["performance_reflection"]["net_pnl_usdt"] == pytest.approx(19.81)
    assert detail["performance_reflection"]["pnl_usdt"] == pytest.approx(19.81)
    assert scorecard["realized_pnl_usdt"] == pytest.approx(19.81)
    assert scorecard["gross_realized_pnl_usdt"] == pytest.approx(20.0)
    assert scorecard["total_cost_usdt"] == pytest.approx(0.19)


def test_performance_reflection_estimates_cost_when_fill_costs_are_missing(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.0,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 104.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "open",
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 90.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 90.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    reflection = trader.block_detail(block["block_id"])["performance_reflection"]
    latest = LivePerformanceRepository(tmp_path / "live_performance.db").latest(
        venue="binance",
        limit=5,
    )
    row = next(item for item in latest if item["block_id"] == block["block_id"])
    source = json.loads(row["source_json"])

    assert result["reflection_count"] == 1
    assert reflection["gross_pnl_usdt"] == pytest.approx(20.0)
    assert reflection["cost_source"] == "estimated_from_notional"
    assert reflection["total_cost_usdt"] == pytest.approx(0.28)
    assert reflection["slippage_usdt"] == pytest.approx(0.28)
    assert reflection["net_pnl_usdt"] == pytest.approx(19.72)
    assert reflection["pnl_usdt"] == pytest.approx(19.72)
    assert reflection["lesson"]["estimated_cost_model"] == {
        "version": "binance_missing_cost_notional_estimate_v1",
        "market": "futures",
        "notional_usdt": pytest.approx(200.0),
        "round_trip_cost_pct": pytest.approx(0.14),
        "estimated_cost_usdt": pytest.approx(0.28),
    }
    assert row["cost_total"] == pytest.approx(0.28)
    assert row["net_pnl"] == pytest.approx(19.72)
    assert row["cost_model_status"] == "estimated_from_notional"
    assert row["cost_precision"] == "estimated"
    assert source["metadata"]["cost_source"] == (
        "binance_block_reflection:estimated_from_notional"
    )


def test_performance_feedback_syncs_precise_costs_to_live_performance(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.0,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 104.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "open",
            "metadata": {
                "fees_usdt": 0.10,
                "funding_usdt": 0.02,
                "slippage_usdt": 0.03,
                "spread_usdt": 0.04,
            },
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 90.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 90.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    latest = LivePerformanceRepository(tmp_path / "live_performance.db").latest(
        venue="binance",
        limit=5,
    )
    row = next(item for item in latest if item["block_id"] == block["block_id"])
    source = json.loads(row["source_json"])
    metadata = source["metadata"]

    assert result["live_performance_count"] == 1
    assert row["gross_pnl"] == pytest.approx(20.0)
    assert row["fees"] == pytest.approx(0.10)
    assert row["funding"] == pytest.approx(0.02)
    assert row["slippage"] == pytest.approx(0.03)
    assert row["spread"] == pytest.approx(0.04)
    assert row["cost_total"] == pytest.approx(0.19)
    assert row["net_pnl"] == pytest.approx(19.81)
    assert row["cost_precision"] == "recorded"
    assert metadata["present_cost_components"] == [
        "fees",
        "funding",
        "slippage",
        "spread",
    ]


def test_performance_feedback_keeps_live_performance_partial_when_costs_are_thin(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 95.0,
            "stop_price": 103.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "open",
            "metadata": {"fees_usdt": 0.05},
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 95.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 95.0, "reason": "target_reached"},
    )

    trader.run_performance_feedback_once()
    latest = LivePerformanceRepository(tmp_path / "live_performance.db").latest(
        venue="binance",
        limit=5,
    )
    row = next(item for item in latest if item["block_id"] == block["block_id"])
    source = json.loads(row["source_json"])

    assert row["cost_precision"] == "partial"
    assert source["metadata"]["present_cost_components"] == ["fees"]
    assert source["metadata"]["missing_cost_components"] == [
        "funding",
        "slippage",
        "spread",
    ]


def test_performance_feedback_syncs_existing_reflection_without_rewriting_it(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.5,
            "entry_price": 200.0,
            "target_price": 220.0,
            "stop_price": 190.0,
            "status": "closed",
            "closed_at": binance_block_trader_module.utc_now_iso(),
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": block["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 200.0,
            "exit_price": 210.0,
            "stop_price": 190.0,
            "target_price": 220.0,
            "pnl_usdt": 4.8,
            "gross_pnl_usdt": 5.0,
            "net_pnl_usdt": 4.8,
            "fee_usdt": 0.1,
            "slippage_usdt": 0.06,
            "spread_usdt": 0.04,
            "cost_source": "explicit",
            "r_multiple": 1.0,
            "lesson": {"result": "positive"},
            "present_cost_components": ["fees", "slippage", "spread"],
        }
    )

    result = trader.run_performance_feedback_once()
    latest = LivePerformanceRepository(tmp_path / "live_performance.db").latest(
        venue="binance",
        limit=5,
    )
    row = next(item for item in latest if item["block_id"] == block["block_id"])
    source = json.loads(row["source_json"])

    assert result["reflection_count"] == 0
    assert result["live_performance_count"] == 1
    assert row["net_pnl"] == pytest.approx(4.8)
    assert row["cost_precision"] == "recorded"
    assert source["metadata"]["present_cost_components"] == [
        "fees",
        "slippage",
        "spread",
    ]


def test_performance_feedback_does_not_promote_reflection_without_fill_evidence(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.5,
            "entry_price": 200.0,
            "target_price": 220.0,
            "stop_price": 190.0,
            "status": "closed",
            "closed_at": binance_block_trader_module.utc_now_iso(),
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": block["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 200.0,
            "exit_price": 210.0,
            "stop_price": 190.0,
            "target_price": 220.0,
            "pnl_usdt": 4.8,
            "gross_pnl_usdt": 5.0,
            "net_pnl_usdt": 4.8,
            "fee_usdt": 0.1,
            "slippage_usdt": 0.06,
            "spread_usdt": 0.04,
            "cost_source": "explicit",
            "r_multiple": 1.0,
            "lesson": {"result": "positive"},
            "present_cost_components": ["fees", "slippage", "spread"],
        }
    )

    result = trader.run_performance_feedback_once()
    row = next(
        item
        for item in LivePerformanceRepository(tmp_path / "live_performance.db").latest(
            venue="binance",
            limit=5,
        )
        if item["block_id"] == block["block_id"]
    )
    source = json.loads(row["source_json"])

    assert result["live_performance_count"] == 1
    assert row["filled"] == 0
    assert row["include_in_jue_alpha"] == 0
    assert row["include_in_risk_management"] == 1
    assert row["attribution"] == "unfilled_or_unrealized"
    assert source["metadata"]["fill_evidence_status"] == "missing_entry_evidence"
    assert source["metadata"]["fill_evidence_reason"] == "no_opened_at_or_entry_fill"


def test_performance_feedback_does_not_stamp_current_revision_on_legacy_blocks(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "long",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "liquidation_price": 70.0,
            "margin_type": "isolated",
            "status": "open",
        }
    )
    legacy_metadata = dict(block["metadata"])
    legacy_metadata.pop("strategy_revision_id", None)
    trader.repository.update_block(block["block_id"], {"metadata": legacy_metadata})
    block = trader.get_block(block["block_id"])
    assert block is not None
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "paper",
            "reason": "target_reached",
            "response": {"status": "FILLED", "avg_fill_price": 110.0},
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 110.0, "reason": "target_reached"},
    )

    trader.run_performance_feedback_once()
    row = next(
        item
        for item in LivePerformanceRepository(tmp_path / "live_performance.db").latest(
            venue="binance",
            limit=5,
        )
        if item["block_id"] == block["block_id"]
    )
    source = json.loads(row["source_json"])

    assert row["filled"] == 1
    assert row["include_in_jue_alpha"] == 1
    assert row["strategy_revision_id"] == ""
    assert "strategy_revision_id" not in source["metadata"]


def test_performance_feedback_upgrades_existing_missing_cost_reflection(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "status": "open",
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "buy",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "entry_order",
            "response": {
                "status": "FILLED",
                "avg_fill_price": 100.0,
                "fills": [
                    {
                        "price": "100.0",
                        "qty": "1.0",
                        "commission": "0.03",
                        "commissionAsset": "USDT",
                    }
                ],
            },
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "sell",
            "qty": 1.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "target_reached",
            "response": {
                "status": "FILLED",
                "avg_fill_price": 105.0,
                "fills": [
                    {
                        "price": "105.0",
                        "qty": "1.0",
                        "commission": "0.04",
                        "commissionAsset": "USDT",
                    }
                ],
            },
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 105.0, "reason": "target_reached"},
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": block["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "stop_price": 95.0,
            "target_price": 110.0,
            "pnl_usdt": 5.0,
            "gross_pnl_usdt": 5.0,
            "net_pnl_usdt": 5.0,
            "cost_source": "missing",
            "r_multiple": 1.0,
            "lesson": {"result": "positive"},
        }
    )

    result = trader.run_performance_feedback_once()
    reflection = trader.repository.get_performance_reflection(block["block_id"])
    latest = LivePerformanceRepository(tmp_path / "live_performance.db").latest(
        venue="binance",
        limit=5,
    )
    row = next(item for item in latest if item["block_id"] == block["block_id"])

    assert result["reflection_count"] == 1
    assert result["live_performance_count"] == 1
    assert reflection is not None
    assert reflection["cost_source"] == "explicit"
    assert reflection["fee_usdt"] == pytest.approx(0.07)
    assert reflection["gross_pnl_usdt"] == pytest.approx(5.0)
    assert reflection["net_pnl_usdt"] == pytest.approx(4.93)
    assert reflection["pnl_usdt"] == pytest.approx(4.93)
    assert row["cost_precision"] == "partial"
    assert row["net_pnl"] == pytest.approx(4.93)


def test_performance_feedback_sync_keeps_futures_long_short_lanes_separate(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    short_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 1.0,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 105.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "leverage": 1,
            "status": "open",
            "metadata": {
                "fees_usdt": 0.05,
                "funding_usdt": 0.0,
                "slippage_usdt": 0.0,
                "spread_usdt": 0.0,
            },
        }
    )
    long_block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "long",
            "qty": 1.0,
            "entry_price": 200.0,
            "target_price": 220.0,
            "stop_price": 190.0,
            "liquidation_price": 120.0,
            "margin_type": "isolated",
            "leverage": 1,
            "status": "open",
            "metadata": {
                "fees_usdt": 0.05,
                "funding_usdt": 0.0,
                "slippage_usdt": 0.0,
                "spread_usdt": 0.0,
            },
        }
    )
    for block, side, exit_price in (
        (short_block, "buy", 90.0),
        (long_block, "sell", 220.0),
    ):
        trader.repository.update_block(
            block["block_id"],
            {"status": "closed", "closed_at": block["created_at"]},
        )
        trader.repository.add_order(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "futures",
                "side": side,
                "qty": 1.0,
                "order_type": "LIMIT_IOC",
                "status": "paper",
                "reason": "target_reached",
                "response": {"status": "FILLED", "avg_fill_price": exit_price},
            }
        )
        trader.repository.add_event(
            block["block_id"],
            "closed",
            payload={"exit_price": exit_price, "reason": "target_reached"},
        )

    result = trader.run_performance_feedback_once()
    summary = LivePerformanceRepository(tmp_path / "live_performance.db").summary()
    lanes = {
        (row["venue"], row["lane"]): row
        for row in summary["lanes"]
    }

    assert result["live_performance_count"] == 2
    assert lanes[("binance", "futures_short")]["sample_count"] == 1
    assert lanes[("binance", "futures_long")]["sample_count"] == 1
    assert lanes[("binance", "futures")]["sample_count"] == 2
    assert lanes[("binance", "futures_short")]["alpha_net_pnl"] == pytest.approx(9.95)
    assert lanes[("binance", "futures_long")]["alpha_net_pnl"] == pytest.approx(19.95)


def test_performance_reflection_parses_binance_fills_and_funding_costs(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.0,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 104.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "open",
            "metadata": {
                "slippage_usdt": 0.03,
                "spread_usdt": 0.04,
            },
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "entry_order",
            "response": {
                "status": "FILLED",
                "avg_fill_price": 100.0,
                "fills": [
                    {
                        "price": "100.0",
                        "qty": "2.0",
                        "commission": "0.05",
                        "commissionAsset": "USDT",
                    }
                ],
            },
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "target_reached",
            "response": {
                "status": "FILLED",
                "avg_fill_price": 90.0,
                "fills": [
                    {
                        "price": "90.0",
                        "qty": "2.0",
                        "commission": "0.001",
                        "commissionAsset": "BTC",
                    }
                ],
                "funding_history": [
                    {
                        "incomeType": "FUNDING_FEE",
                        "income": "-0.02",
                        "asset": "USDT",
                    }
                ],
            },
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 90.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])
    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert result["reflection_count"] == 1
    reflection = detail["performance_reflection"]
    assert reflection["gross_pnl_usdt"] == pytest.approx(20.0)
    assert reflection["fee_usdt"] == pytest.approx(0.14)
    assert reflection["funding_usdt"] == pytest.approx(0.02)
    assert reflection["slippage_usdt"] == pytest.approx(0.03)
    assert reflection["spread_usdt"] == pytest.approx(0.04)
    assert reflection["total_cost_usdt"] == pytest.approx(0.23)
    assert reflection["net_pnl_usdt"] == pytest.approx(19.77)
    assert reflection["pnl_usdt"] == pytest.approx(19.77)
    assert reflection["cost_source"] == "explicit"
    assert scorecard["realized_pnl_usdt"] == pytest.approx(19.77)
    assert scorecard["total_cost_usdt"] == pytest.approx(0.23)


def test_futures_order_cost_enrichment_feeds_performance_reflection(
    tmp_path: Path,
) -> None:
    adapter = _CostHistoryBinance()
    trader = _trader(tmp_path, adapter=adapter)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "short",
            "qty": 2.0,
            "entry_price": 100.0,
            "target_price": 90.0,
            "stop_price": 104.0,
            "liquidation_price": 140.0,
            "margin_type": "isolated",
            "status": "open",
            "opened_at": "2026-06-16T00:00:00+00:00",
        }
    )
    enriched_exit = asyncio.run(
        trader._enrich_order_response_for_costs(
            block=block,
            market="futures",
            symbol="BTCUSDT",
            response={
                "status": "FILLED",
                "order_id": "456",
                "executed_qty": "2.0",
                "avg_fill_price": "90.0",
            },
            include_funding=True,
        )
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": "2026-06-16T01:00:00+00:00"},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "sell",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "entry_order",
            "response": {"status": "FILLED", "avg_fill_price": 100.0},
        }
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "futures",
            "side": "buy",
            "qty": 2.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "target_reached",
            "response": enriched_exit,
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 90.0, "reason": "target_reached"},
    )

    result = trader.run_performance_feedback_once()
    reflection = trader.block_detail(block["block_id"])["performance_reflection"]

    assert result["reflection_count"] == 1
    assert adapter.trade_calls[0]["order_id"] == "456"
    assert adapter.income_calls[0]["income_type"] == "FUNDING_FEE"
    assert enriched_exit["cost_enrichment"]["sources"] == [
        "futures_user_trades",
        "futures_income_funding",
    ]
    assert reflection["fee_usdt"] == pytest.approx(0.07)
    assert reflection["funding_usdt"] == pytest.approx(0.03)
    assert reflection["total_cost_usdt"] == pytest.approx(0.10)
    assert reflection["net_pnl_usdt"] == pytest.approx(19.90)
    assert reflection["cost_source"] == "explicit"


def test_performance_reflection_flags_unconverted_binance_fee_asset(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "status": "open",
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "sell",
            "qty": 0.1,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "target_reached",
            "response": {
                "status": "FILLED",
                "avg_fill_price": 108.0,
                "fills": [
                    {
                        "price": "108.0",
                        "qty": "0.1",
                        "commission": "0.0002",
                        "commissionAsset": "BNB",
                    }
                ],
            },
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 108.0, "reason": "target_reached"},
    )

    trader.run_performance_feedback_once()
    detail = trader.block_detail(block["block_id"])

    lesson = detail["performance_reflection"]["lesson"]
    assert detail["performance_reflection"]["cost_source"] == "partial_unconverted_fee"
    assert lesson["unconverted_fee_assets"] == [
        {
            "asset": "BNB",
            "amount": pytest.approx(0.0002),
            "reason": "missing_conversion_price",
        }
    ]


def test_performance_reflection_converts_bnb_fee_from_saved_quote(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    trader.repository.save_quotes(
        [
            {
                "symbol": "BNBUSDT",
                "market": "spot",
                "price": 600.0,
                "source": "test",
                "fetched_at": "2026-06-18T00:00:00+00:00",
                "status": "ok",
            }
        ]
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 10.0,
            "entry_price": 0.60,
            "target_price": 0.66,
            "stop_price": 0.57,
            "status": "open",
        }
    )
    trader.repository.update_block(
        block["block_id"],
        {"status": "closed", "closed_at": block["created_at"]},
    )
    trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "WLDUSDT",
            "market": "spot",
            "side": "sell",
            "qty": 10.0,
            "order_type": "LIMIT_IOC",
            "status": "FILLED",
            "reason": "target_reached",
            "response": {
                "status": "FILLED",
                "avg_fill_price": 0.64,
                "fills": [
                    {
                        "price": "0.64",
                        "qty": "10.0",
                        "commission": "0.0002",
                        "commissionAsset": "BNB",
                    }
                ],
            },
        }
    )
    trader.repository.add_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 0.64, "reason": "target_reached"},
    )

    trader.run_performance_feedback_once()
    reflection = trader.block_detail(block["block_id"])["performance_reflection"]

    assert reflection["fee_usdt"] == pytest.approx(0.12)
    assert reflection["cost_source"] == "explicit"
    assert reflection["net_pnl_usdt"] == pytest.approx(0.28)
    assert reflection["lesson"]["unconverted_fee_assets"] == []


def test_collect_quotes_tracks_bnbusdt_for_fee_conversion(tmp_path: Path) -> None:
    adapter = _FakeBinance()
    adapter.prices["BTCUSDT"] = 0.62
    adapter.prices["BNBUSDT"] = 600.0
    trader = _trader(tmp_path, adapter=adapter)

    quotes = asyncio.run(
        trader._collect_quotes(
            [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "status": "open",
                }
            ]
        )
    )

    assert ("spot", "BTCUSDT") in quotes
    assert ("spot", "BNBUSDT") in quotes
    assert quotes[("spot", "BNBUSDT")]["price"] == pytest.approx(600.0)


def test_manager_prompt_includes_performance_scorecard(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.repository.save_performance_reflection(
        {
            "block_id": "b1",
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100,
            "exit_price": 104,
            "stop_price": 95,
            "target_price": 110,
            "r_multiple": 0.8,
            "lesson": {"result": "positive", "thesis": "clean trend"},
        }
    )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["performance"]["sample_count"] == 1
    assert prompt["performance"]["avg_r_multiple"] == pytest.approx(0.8)
    assert "performance" in prompt["decision_inputs"]


def test_manager_prompt_surfaces_execution_defect_risk(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    malformed = trader.repository.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 1800.0,
            "target_price": 1836.0,
            "stop_price": 1782.0,
            "status": "closed",
            "created_by": "llm",
            "closed_at": "2026-06-18T01:00:00+00:00",
            "metadata": {
                "horizon": "futures",
                "block_color": "futures",
                "market_or_account_scope": "binance:futures",
            },
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": malformed["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 1800.0,
            "exit_price": 1782.0,
            "stop_price": 1782.0,
            "target_price": 1836.0,
            "pnl_usdt": -10.0,
            "net_pnl_usdt": -10.0,
            "r_multiple": -1.0,
            "lesson": {"result": "negative"},
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": "valid-spot-win",
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "stop_price": 98.0,
            "target_price": 104.0,
            "pnl_usdt": 3.0,
            "net_pnl_usdt": 3.0,
            "r_multiple": 1.5,
            "lesson": {"result": "positive"},
        }
    )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT", "ETHUSDT"]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    risk = prompt["performance"]["execution_defect_risk"]
    assert risk["status"] == "elevated"
    assert risk["excluded_count"] == 1
    assert risk["excluded_pnl_usdt"] == pytest.approx(-10.0)
    assert "malformed_market_scope" in risk["reasons"]
    assert "Do not scale" in risk["instruction"]


def test_manager_prompt_uses_configured_feedback_performance_window(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.config.performance_scorecard_feedback_limit = 25

    for index in range(21):
        block = trader.repository.create_block(
            {
                "symbol": f"BAD{index:02d}USDT",
                "market": "spot",
                "side": "long",
                "qty": 1.0,
                "entry_price": 100.0,
                "target_price": 104.0,
                "stop_price": 98.0,
                "status": "closed",
                "created_by": "llm",
                "closed_at": f"2026-06-18T01:{index:02d}:00+00:00",
                "metadata": {
                    "horizon": "futures",
                    "market_or_account_scope": "binance:futures",
                },
            }
        )
        trader.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": "spot",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 98.0,
                "target_price": 104.0,
                "pnl_usdt": -1.0,
                "net_pnl_usdt": -1.0,
                "r_multiple": -1.0,
                "created_at": f"2026-06-18T01:{index:02d}:01+00:00",
            }
        )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["recent_performance"]["window"]["limit"] == 20
    assert prompt["performance"]["window"]["limit"] == 25
    assert prompt["performance"]["execution_defect_risk"]["excluded_count"] == 21


def test_performance_scorecard_surfaces_loss_diagnostics(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    for index, pnl in enumerate([-0.4, -0.3, 0.1], start=1):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"ondo-{index}",
                "symbol": "ONDOUSDT",
                "market": "futures",
                "side": "long",
                "entry_price": 1.0,
                "exit_price": 0.98 if pnl < 0 else 1.02,
                "stop_price": 0.98,
                "target_price": 1.04,
                "pnl_usdt": pnl,
                "r_multiple": -1.0 if pnl < 0 else 1.0,
                "lesson": {"result": "negative" if pnl < 0 else "positive"},
            }
        )
    for index in range(1, 4):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"short-{index}",
                "symbol": f"SHORT{index}USDT",
                "market": "futures",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "stop_price": 102.0,
                "target_price": 96.0,
                "pnl_usdt": -0.2,
                "r_multiple": -1.0,
                "lesson": {"result": "negative"},
            }
        )

    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert scorecard["realized_pnl_usdt"] == pytest.approx(-1.2)
    assert scorecard["symbol_scorecards"][0]["symbol"] == "ONDOUSDT"
    assert scorecard["symbol_scorecards"][0]["pnl_usdt"] == pytest.approx(-0.6)
    assert any("ONDOUSDT" in item for item in scorecard["improvement_points"])
    assert any("short" in item for item in scorecard["improvement_points"])


def test_performance_scorecard_excludes_malformed_futures_scope_spot_blocks(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    malformed = trader.repository.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 1800.0,
            "target_price": 1836.0,
            "stop_price": 1782.0,
            "status": "closed",
            "created_by": "llm",
            "closed_at": "2026-06-18T01:00:00+00:00",
            "metadata": {
                "horizon": "futures",
                "block_color": "futures",
                "market_or_account_scope": "binance:futures",
            },
        }
    )
    normal = trader.repository.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 104.0,
            "stop_price": 98.0,
            "status": "closed",
            "created_by": "llm",
            "closed_at": "2026-06-18T02:00:00+00:00",
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": malformed["block_id"],
            "symbol": "ETHUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 1800.0,
            "exit_price": 1782.0,
            "stop_price": 1782.0,
            "target_price": 1836.0,
            "pnl_usdt": -10.0,
            "net_pnl_usdt": -10.0,
            "r_multiple": -1.0,
            "lesson": {"result": "negative"},
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": normal["block_id"],
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "stop_price": 98.0,
            "target_price": 104.0,
            "pnl_usdt": 3.0,
            "net_pnl_usdt": 3.0,
            "r_multiple": 1.5,
            "lesson": {"result": "positive"},
        }
    )

    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert scorecard["sample_count"] == 1
    assert scorecard["realized_pnl_usdt"] == pytest.approx(3.0)
    assert scorecard["window"]["excluded_execution_defect_count"] == 1
    assert scorecard["window"]["excluded_execution_defect_pnl_usdt"] == pytest.approx(-10.0)
    assert scorecard["symbol_scorecards"][0]["symbol"] == "BTCUSDT"


def test_performance_scorecard_excludes_text_only_futures_scope_spot_blocks(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)
    malformed = trader.repository.create_block(
        {
            "symbol": "BNBUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 600.0,
            "target_price": 612.0,
            "stop_price": 594.0,
            "status": "closed",
            "created_by": "llm",
            "closed_at": "2026-06-18T01:00:00+00:00",
            "thesis": (
                "BNBUSDT futures long is a 1x isolated waiting-entry block "
                "with clear 2R structure."
            ),
            "metadata": {"block_color": "short"},
        }
    )
    normal = trader.repository.create_block(
        {
            "symbol": "WLDUSDT",
            "market": "spot",
            "side": "long",
            "qty": 10.0,
            "entry_price": 1.0,
            "target_price": 1.04,
            "stop_price": 0.98,
            "status": "closed",
            "created_by": "llm",
            "closed_at": "2026-06-18T02:00:00+00:00",
            "thesis": "WLDUSDT spot long short-horizon pullback probe.",
            "metadata": {"block_color": "short"},
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": malformed["block_id"],
            "symbol": "BNBUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 600.0,
            "exit_price": 594.0,
            "stop_price": 594.0,
            "target_price": 612.0,
            "pnl_usdt": -6.0,
            "net_pnl_usdt": -6.0,
            "r_multiple": -1.0,
            "lesson": {"result": "negative"},
        }
    )
    trader.repository.save_performance_reflection(
        {
            "block_id": normal["block_id"],
            "symbol": "WLDUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 1.0,
            "exit_price": 1.03,
            "stop_price": 0.98,
            "target_price": 1.04,
            "pnl_usdt": 3.0,
            "net_pnl_usdt": 3.0,
            "r_multiple": 1.5,
            "lesson": {"result": "positive"},
        }
    )

    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert scorecard["sample_count"] == 1
    assert scorecard["realized_pnl_usdt"] == pytest.approx(3.0)
    assert scorecard["window"]["excluded_execution_defect_count"] == 1
    assert scorecard["window"]["excluded_execution_defect_pnl_usdt"] == pytest.approx(-6.0)
    assert scorecard["symbol_scorecards"][0]["symbol"] == "WLDUSDT"


def test_performance_scorecard_tracks_volatile_attack_lane(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    for index, r_multiple in enumerate([-1.0, -0.7, 1.2], start=1):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"volatile-{index}",
                "symbol": f"MEME{index}USDT",
                "market": "futures",
                "side": "long",
                "lane": "volatile_attack",
                "entry_price": 1.0,
                "exit_price": 0.95 if r_multiple < 0 else 1.06,
                "stop_price": 0.95,
                "target_price": 1.15,
                "pnl_usdt": r_multiple,
                "r_multiple": r_multiple,
                "lesson": {"result": "positive" if r_multiple > 0 else "negative"},
            }
        )

    scorecard = trader.repository.latest_performance_scorecard(limit=20)

    assert scorecard["lane_scorecards"][0]["lane"] == "volatile_attack"
    assert scorecard["lane_scorecards"][0]["sample_count"] == 3
    assert scorecard["lane_scorecards"][0]["avg_r_multiple"] == pytest.approx(-0.166666, abs=1e-5)
    assert scorecard["lane_scorecards"][0]["win_rate_pct"] == pytest.approx(100 / 3)


def test_weak_volatile_attack_lane_reduces_risk_sizing(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "long",
                    "lane": "volatile_attack",
                    "entry_price": 100.0,
                    "target_price": 130.0,
                    "stop_price": 90.0,
                    "qty": 999.0,
                    "leverage": 1,
                    "margin_type": "isolated",
                    "liquidation_price": 70.0,
                    "thesis": "volatile expansion setup",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.risk_sizer = BinanceRiskSizer(
        BinanceRiskConfig(account_risk_pct=1.0, max_symbol_exposure_pct=100.0)
    )
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"weak-volatile-{index}",
                "symbol": "ALTUSDT",
                "market": "futures",
                "side": "long",
                "lane": "volatile_attack",
                "entry_price": 1.0,
                "exit_price": 0.95,
                "stop_price": 0.95,
                "target_price": 1.15,
                "pnl_usdt": -1.0,
                "r_multiple": -1.0,
            }
        )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]
    sizing = block["metadata"]["risk_sizing"]

    assert result["status"] == "ok"
    assert sizing["lane"] == "volatile_attack"
    assert sizing["lane_risk_multiplier"] == pytest.approx(0.35)
    assert sizing["performance_multiplier"] == pytest.approx(0.5)
    assert sizing["effective_risk_multiplier"] == pytest.approx(0.175)
    assert sizing["risk_budget_usdt"] == pytest.approx(17.5)


def test_futures_short_risk_sizing_uses_side_scorecard_over_futures_aggregate(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "entry_price": 100.0,
                    "target_price": 85.0,
                    "stop_price": 105.0,
                    "qty": 999.0,
                    "leverage": 1,
                    "margin_type": "isolated",
                    "liquidation_price": 130.0,
                    "thesis": "futures short setup should respect short-side damage",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.risk_sizer = BinanceRiskSizer(
        BinanceRiskConfig(account_risk_pct=1.0, max_symbol_exposure_pct=100.0)
    )
    for index in range(3):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"weak-futures-short-{index}",
                "symbol": "SHORTBADUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures",
                "entry_price": 100.0,
                "exit_price": 105.0,
                "stop_price": 105.0,
                "target_price": 85.0,
                "pnl_usdt": -1.0,
                "r_multiple": -0.9,
            }
        )
    for index in range(10):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"strong-futures-long-{index}",
                "symbol": "LONGGOODUSDT",
                "market": "futures",
                "side": "long",
                "lane": "futures",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "stop_price": 95.0,
                "target_price": 110.0,
                "pnl_usdt": 2.0,
                "r_multiple": 1.5,
            }
        )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]
    sizing = block["metadata"]["risk_sizing"]

    assert result["status"] == "ok"
    assert sizing["lane"] == "futures"
    assert sizing["performance_multiplier"] == pytest.approx(0.5)
    assert sizing["effective_risk_multiplier"] == pytest.approx(0.45)


def test_recent_futures_short_recovery_uses_probation_risk_sizing(
    tmp_path: Path,
) -> None:
    adapter = _FakeBinance()
    adapter.account = {
        "status": "ok",
        "spot_cash_usdt": 5_000.0,
        "futures_cash_usdt": 5_000.0,
        "positions": [],
    }
    trader = _trader(tmp_path, adapter=adapter)
    trader.config.performance_scorecard_feedback_limit = 120
    trader.config.lane_performance_min_samples = 3
    trader.risk_sizer = BinanceRiskSizer(
        BinanceRiskConfig(account_risk_pct=1.0, max_symbol_exposure_pct=100.0)
    )
    for index in range(8):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"stale-futures-short-loss-{index}",
                "symbol": "OLDUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures",
                "entry_price": 100.0,
                "exit_price": 105.0,
                "stop_price": 105.0,
                "target_price": 90.0,
                "pnl_usdt": -3.0,
                "r_multiple": -1.0,
                "created_at": f"2026-01-01T00:{index:02d}:00+00:00",
            }
        )
    for index in range(20):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"recent-futures-short-win-{index}",
                "symbol": "RECENTUSDT",
                "market": "futures",
                "side": "short",
                "lane": "futures",
                "entry_price": 100.0,
                "exit_price": 95.0,
                "stop_price": 105.0,
                "target_price": 90.0,
                "pnl_usdt": 1.0,
                "r_multiple": 1.0,
                "created_at": f"2026-02-01T00:{index:02d}:00+00:00",
            }
        )

    trader._last_account_snapshot = adapter.account
    payload = trader._apply_risk_sizing(
        {
            "symbol": "DOGEUSDT",
            "market": "futures",
            "side": "short",
            "lane": "futures",
            "entry_price": 0.083,
            "target_price": 0.081,
            "stop_price": 0.084,
            "qty": 10_000.0,
            "leverage": 1,
            "created_by": "llm",
            "metadata": {},
        }
    )
    sizing = payload["metadata"]["risk_sizing"]

    assert sizing["lane"] == "futures"
    assert sizing["performance_multiplier"] == pytest.approx(0.75)
    assert sizing["effective_risk_multiplier"] == pytest.approx(0.675)


def test_strong_volatile_attack_lane_scales_only_slightly(tmp_path: Path) -> None:
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "side": "short",
                    "lane": "volatile_attack",
                    "entry_price": 100.0,
                    "target_price": 70.0,
                    "stop_price": 110.0,
                    "qty": 999.0,
                    "leverage": 1,
                    "margin_type": "isolated",
                    "liquidation_price": 140.0,
                    "thesis": "verified volatile breakdown",
                }
            ]
        }
    )
    trader = _trader(tmp_path, llm=llm)
    trader.config.budget_performance_scale_multiplier = 2.0
    trader.risk_sizer = BinanceRiskSizer(
        BinanceRiskConfig(account_risk_pct=1.0, max_symbol_exposure_pct=100.0)
    )
    for index in range(10):
        trader.repository.save_performance_reflection(
            {
                "block_id": f"strong-volatile-{index}",
                "symbol": "ALTUSDT",
                "market": "futures",
                "side": "short",
                "lane": "volatile_attack",
                "entry_price": 1.0,
                "exit_price": 0.9,
                "stop_price": 1.05,
                "target_price": 0.85,
                "pnl_usdt": 1.0,
                "r_multiple": 1.0,
            }
        )

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]
    sizing = block["metadata"]["risk_sizing"]

    assert result["status"] == "ok"
    assert sizing["lane"] == "volatile_attack"
    assert sizing["performance_multiplier"] == pytest.approx(1.15)
    assert sizing["effective_risk_multiplier"] == pytest.approx(0.4025)


def test_manager_close_open_block_requests_rule_exit(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": [], "close_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "qty_open": 0.1,
            "target_price": 120.0,
            "stop_price": 80.0,
            "status": "open",
        }
    )
    llm.payload = {
        "create_blocks": [],
        "close_blocks": [
            {
                "block_id": block["block_id"],
                "reason": "thesis invalidated by adverse liquidity risk",
            }
        ],
    }

    result = asyncio.run(trader.run_manager_once())
    updated = trader.get_block(block["block_id"])

    assert result["applied"]["closed"][0]["status"] == "exit_requested"
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["force_exit_requested"] is True


def test_manager_rejects_generic_close_for_open_block_without_adverse_evidence(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM({"create_blocks": [], "close_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "qty_open": 0.1,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 80.0,
            "status": "open",
        }
    )
    llm.payload = {
        "create_blocks": [],
        "close_blocks": [
            {
                "block_id": block["block_id"],
                "reason": "manager close",
            }
        ],
    }

    result = asyncio.run(trader.run_manager_once())
    updated = trader.get_block(block["block_id"])
    events = trader.repository.list_events(block["block_id"], limit=5)

    assert result["applied"]["closed"] == [
        {
            "status": "rejected",
            "reason": "manager_close_requires_adverse_evidence",
            "block_id": block["block_id"],
        }
    ]
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["force_exit_requested"] is False
    assert events[0]["event_type"] == "manager_close_rejected"


def test_active_futures_without_liquidation_inputs_is_rejected(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    with pytest.raises(ValueError, match="liquidation distance inputs"):
        trader.create_block(
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "long",
                "qty": 1.0,
                "status": "open",
                "margin_type": "isolated",
                "leverage": 2,
                "entry_price": 200.0,
            }
        )


def test_upbit_spot_entry_routes_to_upbit_adapter(tmp_path: Path) -> None:
    binance = _FillingEntryBinance()
    upbit = _FakeUpbit()
    trader = _trader(
        tmp_path,
        adapter=binance,
        upbit=upbit,
        enabled=True,
        execute_upbit=True,
    )
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "upbit_spot",
            "side": "long",
            "qty": 0.001,
            "entry_price": 100_000_000.0,
            "target_price": 108_000_000.0,
            "stop_price": 95_000_000.0,
            "status": "proposed",
            "created_by": "llm",
        }
    )

    result = asyncio.run(trader._submit_entry_for_block(block))
    updated = trader.get_block(block["block_id"])

    assert result["status"] == "opened"
    assert updated is not None
    assert updated["status"] == "open"
    assert updated["symbol"] == "KRW-BTC"
    assert not binance.spot_orders
    assert upbit.orders
    assert upbit.orders[0]["symbol"] == "KRW-BTC"
    assert upbit.orders[0]["side"] == "buy"
    assert upbit.orders[0]["limit_price"] % 1000 == 0


def test_upbit_spot_quote_budget_derives_qty_in_krw(tmp_path: Path) -> None:
    trader = _trader(tmp_path, upbit=_FakeUpbit())

    block = trader.create_block(
        {
            "symbol": "KRW-BTC",
            "market": "upbit_spot",
            "side": "long",
            "quote_budget_krw": 50_000.0,
            "entry_price": 100_000_000.0,
            "target_price": 108_000_000.0,
            "stop_price": 95_000_000.0,
            "status": "proposed",
            "created_by": "llm",
        }
    )

    assert block["qty_initial"] == pytest.approx(0.0005)
    assert block["metadata"]["quote_budget_krw"] == pytest.approx(50_000.0)
    assert block["metadata"]["lane"] == "upbit_spot:long"


def test_upbit_spot_qty_hint_string_derives_qty_from_embedded_quote_budget(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path, upbit=_FakeUpbit())

    block = trader.create_block(
        {
            "symbol": "KRW-BTC",
            "market": "upbit_spot",
            "side": "long",
            "qty": "quote_budget_krw=65534.30; approx_qty=0.56226",
            "entry_style": "wait_for_price",
            "entry_trigger_operator": "<=",
            "entry_price": 116_555.16,
            "target_price": 119_352.74,
            "stop_price": 115_157.06,
            "status": "proposed",
            "created_by": "llm",
        }
    )

    assert block["qty_initial"] == pytest.approx(65_534.30 / 116_555.16)
    assert block["metadata"]["quote_budget_krw"] == pytest.approx(65_534.30)
    assert block["metadata"]["quantity_hint"] == (
        "quote_budget_krw=65534.30; approx_qty=0.56226"
    )


def test_spot_qty_hint_string_derives_qty_from_embedded_usdt_quote_budget(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path)

    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": "quote_budget_usdt=50.0",
            "entry_style": "wait_for_price",
            "entry_trigger_operator": "<=",
            "entry_price": 100_000.0,
            "target_price": 104_000.0,
            "stop_price": 98_000.0,
            "status": "proposed",
            "created_by": "llm",
        }
    )

    assert block["qty_initial"] == pytest.approx(50.0 / 100_000.0)
    assert block["metadata"]["quote_budget_usdt"] == pytest.approx(50.0)
    assert block["metadata"]["quantity_hint"] == "quote_budget_usdt=50.0"


def test_upbit_spot_block_rejects_usdt_scaled_prices_against_recent_krw_quote(
    tmp_path: Path,
) -> None:
    trader = _trader(tmp_path, upbit=_FakeUpbit())
    trader.repository.save_quotes(
        [
            {
                "symbol": "KRW-BTC",
                "market": "upbit_spot",
                "price": 100_000_000.0,
                "source": "test",
            }
        ]
    )

    with pytest.raises(ValueError, match="upbit_spot_price_scale_mismatch"):
        trader.create_block(
            {
                "symbol": "KRW-BTC",
                "market": "upbit_spot",
                "side": "long",
                "quote_budget_krw": 50_000.0,
                "entry_price": 65_000.0,
                "target_price": 66_500.0,
                "stop_price": 64_000.0,
                "status": "proposed",
                "created_by": "llm",
            }
        )


def test_upbit_spot_short_is_rejected(tmp_path: Path) -> None:
    trader = _trader(tmp_path, upbit=_FakeUpbit())

    with pytest.raises(ValueError, match="upbit_spot blocks only support long side"):
        trader.create_block(
            {
                "symbol": "KRW-BTC",
                "market": "upbit_spot",
                "side": "short",
                "qty": 0.001,
                "entry_price": 100_000_000.0,
                "target_price": 95_000_000.0,
                "stop_price": 105_000_000.0,
            }
        )


def test_upbit_wallet_position_can_be_adopted(tmp_path: Path) -> None:
    upbit = _FakeUpbit()
    upbit.assets = [
        {
            "asset": "KRW",
            "symbol": "KRW",
            "market": "upbit_spot",
            "kind": "cash",
            "qty": 100_000.0,
            "available": 100_000.0,
        },
        {
            "asset": "BTC",
            "symbol": "KRW-BTC",
            "market": "upbit_spot",
            "kind": "position",
            "qty": 0.002,
            "available": 0.002,
            "locked": 0.0,
            "avg_price": 90_000_000.0,
            "mark_price": 100_000_000.0,
            "value_krw": 200_000.0,
        },
    ]
    trader = _trader(tmp_path, upbit=upbit)

    result = asyncio.run(trader.run_upbit_adoption_once())
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["adopted_count"] == 1
    assert block["market"] == "upbit_spot"
    assert block["symbol"] == "KRW-BTC"
    assert block["status"] == "open"
    assert block["qty_open"] == pytest.approx(0.002)
def test_binance_jue_wiki_prompt_context_attaches_application_metadata() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance",
            "prompt_mode": "assist",
            "configured_prompt_mode": "assist",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:binance-assist",
                "recommended_mode": "assist",
                "sample_count": 30,
                "confidence": 0.65,
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_assist_recommendation",
            },
            "trust_profile_effectiveness": {
                "target_scope": "binance",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 9,
                    }
                ],
            },
            "pages": [{"page_id": "binance.symbol.BTCUSDT"}],
            "requested_symbol_summaries": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "symbol": "ETHUSDT",
                    "summary": "ETHUSDT 압축 종목 기억",
                    "memory_card": {"stance": "spot patience"},
                }
            ],
            "repair_priorities": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "page_type": "symbol",
                    "symbols": ["ETHUSDT"],
                    "sample_count": 8,
                    "win_rate": 0.25,
                    "expectancy": -0.7,
                    "helpful_score": -8.0,
                    "drawdown_pressure": 1.4,
                    "repair_action": "revise entry/exit design",
                }
            ],
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 3,
                "missed_count": 2,
                "resolved_count": 1,
                "resolution_rate": 1 / 3,
                "metric_count": 1,
                "repair_required_count": 1,
                "top_degraded": [
                    {
                        "decision_scope": "binance",
                        "priority_type": "evidence_quality",
                        "action_type": "refresh_symbol_financials",
                        "decision_use": "evidence_quality_cross_check",
                        "source_id": "ETHUSDT:financials",
                        "sample_count": 3,
                        "missed_count": 2,
                        "resolved_count": 1,
                        "resolution_rate": 1 / 3,
                        "status": "repair_required",
                    }
                ],
            },
            "budget_report": {"char_count": 10},
        },
        max_chars=1000,
    )

    assert prompt["decision_inputs"] == [
        "jue_wiki_memory_card_quality",
        "jue_wiki_repair_contract",
    ]
    assert prompt["jue_wiki_memory_card_quality"] == {
        "version": "jue_wiki_memory_card_quality_input_v1",
        "summary": {
            "version": "jue_wiki_memory_card_quality_v1",
            "requested_symbol_summary_count": 1,
            "status": "weak",
            "strong_count": 0,
            "partial_count": 0,
            "weak_count": 1,
            "weak_symbols": ["ETHUSDT"],
            "missing_fields_by_symbol": [
                {
                    "symbol": "ETHUSDT",
                    "status": "weak",
                    "missing_fields": [
                        "durable_facts",
                        "lessons",
                        "open_questions",
                    ],
                }
            ],
            "missing_field_counts": {
                "durable_facts": 1,
                "lessons": 1,
                "open_questions": 1,
            },
        },
        "action_plan": {
            "status": "active",
            "hard_blocker": False,
            "decision_policy": (
                "do_not_overtrust_thin_requested_symbol_memory_cards"
            ),
            "required_action": "cross_check_live_research_before_high_confidence",
            "reason": "requested_symbol_memory_cards_are_thin",
            "symbols": ["ETHUSDT"],
            "missing_fields_by_symbol": [
                {
                    "symbol": "ETHUSDT",
                    "status": "weak",
                    "missing_fields": [
                        "durable_facts",
                        "lessons",
                        "open_questions",
                    ],
                }
            ],
            "required_checks": [
                "refresh_durable_facts_from_reports_fundamentals_and_market_context",
                "review_block_history_and_reflections_for_lessons",
                "record_open_questions_and_data_gaps_before_confident_action",
            ],
        },
    }
    assert prompt["jue_wiki_repair_contract"]["status"] == "active"
    assert prompt["jue_wiki_repair_contract"]["top_priorities"][0]["page_id"] == (
        "binance.symbol.ETHUSDT"
    )
    assert prompt["jue_wiki_repair_contract"]["repair_loop_effectiveness"][
        "status"
    ] == "repair_required"
    assert prompt["jue_wiki_repair_contract"]["repair_loop_effectiveness"][
        "top_degraded"
    ][0]["source_id"] == "ETHUSDT:financials"
    assert prompt["jue_wiki_application"] | {"budget_report": {"char_count": 10}} == {
        "status": "ok",
        "selection_run_id": "selection:binance",
        "prompt_mode": "assist",
        "configured_prompt_mode": "assist",
        "mode_recommendation": {
            "recommendation_id": "wiki-mode:binance-assist",
            "recommended_mode": "assist",
            "sample_count": 30,
            "confidence": 0.65,
        },
        "prompt_mode_policy": {
            "source": "mode_recommendation",
            "reason": "validated_assist_recommendation",
        },
        "trust_profile": {
            "prompt_mode": "assist",
            "authority": "supporting_evidence",
            "trust_level": "medium",
            "decision_use": (
                "use selected wiki pages as supporting evidence alongside live "
                "quotes, account state, research, and risk gates"
            ),
            "posture": "validated_mode_recommendation",
            "configured_prompt_mode": "assist",
            "recommended_mode": "assist",
            "recommendation_id": "wiki-mode:binance-assist",
            "sample_count": 30,
            "confidence": 0.65,
            "policy_reason": "validated_assist_recommendation",
            "authority_effectiveness": {
                "status": "active",
                "sample_count": 9,
            },
            "usage_contract": {
                "version": "jue_wiki_usage_contract_v1",
                "decision_role": "supporting_evidence",
                "effectiveness_status": "active",
                "risk_posture": "supporting_evidence",
                "standalone_trade_authority": False,
                "requires_live_cross_check": True,
                "hard_blocker": False,
                "allowed_uses": [
                    "candidate_ranking",
                    "target_stop_context",
                    "risk_note_context",
                    "follow_up_research",
                ],
                "required_cross_checks": [
                    "live_quote",
                    "account_state",
                    "risk_gate",
                    "fresh_research_conflicts",
                    "current_price_structure",
                ],
                "conflict_resolution": (
                    "prefer_live_execution_data_and_record_wiki_repair"
                ),
            },
        },
        "trust_profile_effectiveness": {
            "target_scope": "binance",
            "trust_profile_count": 1,
            "trust_profiles": [
                {
                    "authority": "supporting_evidence",
                    "status": "active",
                    "sample_count": 9,
                }
            ],
        },
        "selected_page_ids": ["binance.symbol.BTCUSDT"],
        "requested_symbol_summary_page_ids": ["binance.symbol.ETHUSDT"],
        "applied_page_ids": ["binance.symbol.BTCUSDT", "binance.symbol.ETHUSDT"],
        "requested_symbol_summary_count": 1,
        "memory_card_quality_summary": {
            "version": "jue_wiki_memory_card_quality_v1",
            "requested_symbol_summary_count": 1,
            "status": "weak",
            "strong_count": 0,
            "partial_count": 0,
            "weak_count": 1,
            "weak_symbols": ["ETHUSDT"],
            "missing_fields_by_symbol": [
                {
                    "symbol": "ETHUSDT",
                    "status": "weak",
                    "missing_fields": [
                        "durable_facts",
                        "lessons",
                        "open_questions",
                    ],
                }
            ],
            "missing_field_counts": {
                "durable_facts": 1,
                "lessons": 1,
                "open_questions": 1,
            },
        },
        "memory_card_quality_action_plan": {
            "status": "active",
            "hard_blocker": False,
            "decision_policy": (
                "do_not_overtrust_thin_requested_symbol_memory_cards"
            ),
            "required_action": "cross_check_live_research_before_high_confidence",
            "reason": "requested_symbol_memory_cards_are_thin",
            "symbols": ["ETHUSDT"],
            "missing_fields_by_symbol": [
                {
                    "symbol": "ETHUSDT",
                    "status": "weak",
                    "missing_fields": [
                        "durable_facts",
                        "lessons",
                        "open_questions",
                    ],
                }
            ],
            "required_checks": [
                "refresh_durable_facts_from_reports_fundamentals_and_market_context",
                "review_block_history_and_reflections_for_lessons",
                "record_open_questions_and_data_gaps_before_confident_action",
            ],
        },
        "budget_report": {"char_count": 10},
    }


def test_binance_jue_wiki_application_metadata_summarizes_selection_audit() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-audit",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "binance.ops.manager_runs",
                    "rank": 1,
                    "score": 139.0,
                    "selection_reasons": [
                        "scope_match:binance",
                        "operational_memory:manager_runs",
                        "operational_memory:manager_contract_recovery",
                    ],
                    "selection_penalties": [],
                    "char_count": 1400,
                },
                {
                    "page_id": "binance.symbol.NEARUSDT",
                    "rank": 2,
                    "score": 81.0,
                    "selection_reasons": [
                        "scope_match:binance",
                        "symbol_overlap:NEARUSDT",
                    ],
                    "selection_penalties": ["freshness:stale"],
                    "char_count": 920,
                },
            ],
            "budget_report": {"selected_count": 2},
        },
        max_chars=4_000,
    )

    assert prompt["jue_wiki_application"]["selection_audit"] == {
        "selected_page_count": 2,
        "reason_counts": {
            "scope_match:binance": 2,
            "operational_memory:manager_contract_recovery": 1,
            "operational_memory:manager_runs": 1,
            "symbol_overlap:NEARUSDT": 1,
        },
        "penalty_counts": {"freshness:stale": 1},
        "top_pages": [
            {
                "page_id": "binance.ops.manager_runs",
                "rank": 1,
                "score": 139.0,
                "selection_reasons": [
                    "scope_match:binance",
                    "operational_memory:manager_runs",
                    "operational_memory:manager_contract_recovery",
                ],
            },
            {
                "page_id": "binance.symbol.NEARUSDT",
                "rank": 2,
                "score": 81.0,
                "selection_reasons": [
                    "scope_match:binance",
                    "symbol_overlap:NEARUSDT",
                ],
                "selection_penalties": ["freshness:stale"],
            },
        ],
    }


def test_binance_jue_wiki_prompt_context_preserves_guidance_metadata() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-guidance",
            "prompt_mode": "assist",
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
                                "expectancy": 0.55,
                                "helpful_score": 6.5,
                                "confidence": 0.74,
                                "reasons": [
                                    "usage_guidance:risk_posture:standard_block_design"
                                ],
                            }
                        ],
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
                                "reasons": ["quality_warning_source_page"],
                            }
                        ],
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "funding_missing",
                            "page_id": "binance.symbol.ETHUSDT",
                            "status": "degraded",
                            "sample_count": 4,
                            "expectancy": -0.8,
                            "helpful_score": -6.5,
                        }
                    ],
                    "quality_warning_effectiveness_statuses": ["degraded"],
                }
            ],
            "budget_report": {"char_count": 10},
        },
        max_chars=5000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    assert page["usage_guidance"]["risk_posture"] == "standard_block_design"
    assert page["usage_guidance"]["required_cross_checks"] == [
        "live_quote",
        "funding_rate",
    ]
    assert page["usage_guidance_effectiveness"]["metrics"][0]["page_id"] == (
        "usage_guidance.risk_posture.standard_block_design"
    )
    assert page["memory_card_quality_effectiveness"]["metrics"][0]["page_id"] == (
        "memory_card_quality.required_check.refresh_crypto_alpha_facts"
    )
    assert page["quality_warning_source_effectiveness"]["metrics"][0][
        "page_id"
    ] == "binance.symbol.ETHUSDT"
    assert page["quality_warning_effectiveness"][0]["warning"] == "funding_missing"
    assert page["quality_warning_effectiveness_statuses"] == ["degraded"]


def test_binance_jue_wiki_prompt_context_derives_effectiveness_attention_items() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-attention-derived",
            "prompt_mode": "assist",
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
            "budget_report": {"char_count": 10},
        },
        max_chars=5000,
    )

    assert prompt["jue_wiki"]["effectiveness_attention_items"] == [
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
    assert prompt["jue_wiki_application"]["effectiveness_attention_items"] == (
        prompt["jue_wiki"]["effectiveness_attention_items"]
    )


def test_binance_jue_wiki_application_flags_partial_requested_symbol_coverage() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-partial-symbols",
            "prompt_mode": "assist",
            "pages": [{"page_id": "binance.symbol.BTCUSDT"}],
            "requested_symbol_summaries": [
                {"page_id": "binance.symbol.BTCUSDT", "symbol": "BTCUSDT"}
            ],
            "budget_report": {
                "requested_symbol_count": 4,
                "requested_symbol_summary_count": 1,
                "requested_symbol_summary_coverage_status": "partial",
                "requested_symbol_unsummarized_count": 3,
                "requested_symbol_unsummarized_symbols": [
                    "ETHUSDT",
                    "SOLUSDT",
                    "XRPUSDT",
                ],
            },
        },
        max_chars=1000,
    )

    assert prompt["jue_wiki_application"]["requested_symbol_coverage_action_plan"] == {
        "status": "partial",
        "hard_blocker": False,
        "decision_policy": "do_not_assume_unsummarized_symbols_were_reviewed",
        "requested_symbol_count": 4,
        "summarized_symbol_count": 1,
        "unsummarized_symbol_count": 3,
        "unsummarized_symbols": ["ETHUSDT", "SOLUSDT", "XRPUSDT"],
        "required_adjustments": [
            {
                "adjustment_type": "coverage_gap_follow_up",
                "reason": "requested_symbols_missing_from_wiki_summary",
                "symbols": ["ETHUSDT", "SOLUSDT", "XRPUSDT"],
                "resolution": "defer_confident_decision_until_summary_or_live_cross_check",
            }
        ],
    }
    assert "jue_wiki_requested_symbol_coverage" in prompt["decision_inputs"]
    assert prompt["jue_wiki_requested_symbol_coverage"] == {
        "version": "jue_wiki_requested_symbol_coverage_v1",
        "status": "partial",
        "hard_blocker": False,
        "decision_policy": "do_not_assume_unsummarized_symbols_were_reviewed",
        "required_action": (
            "before confident decisions on unsummarized symbols, perform live "
            "cross-check or request/record a fresh wiki summary"
        ),
        "unsummarized_symbols": ["ETHUSDT", "SOLUSDT", "XRPUSDT"],
        "required_adjustments": [
            {
                "adjustment_type": "coverage_gap_follow_up",
                "reason": "requested_symbols_missing_from_wiki_summary",
                "symbols": ["ETHUSDT", "SOLUSDT", "XRPUSDT"],
                "resolution": "defer_confident_decision_until_summary_or_live_cross_check",
            }
        ],
    }


def test_binance_jue_wiki_memory_card_quality_names_missing_fields() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-memory-fields",
            "prompt_mode": "assist",
            "pages": [{"page_id": "binance.symbol.BTCUSDT"}],
            "requested_symbol_summaries": [
                {
                    "page_id": "binance.symbol.ETHUSDT",
                    "symbol": "ETHUSDT",
                    "summary": "ETHUSDT compressed memory",
                    "memory_card": {"stance": "spot patience"},
                }
            ],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    quality = prompt["jue_wiki_memory_card_quality"]
    assert quality["summary"]["missing_fields_by_symbol"] == [
        {
            "symbol": "ETHUSDT",
            "status": "weak",
            "missing_fields": ["durable_facts", "lessons", "open_questions"],
        }
    ]
    assert quality["action_plan"]["missing_fields_by_symbol"] == [
        {
            "symbol": "ETHUSDT",
            "status": "weak",
            "missing_fields": ["durable_facts", "lessons", "open_questions"],
        }
    ]
    assert quality["action_plan"]["required_checks"] == [
        "refresh_durable_facts_from_reports_fundamentals_and_market_context",
        "review_block_history_and_reflections_for_lessons",
        "record_open_questions_and_data_gaps_before_confident_action",
    ]


def test_binance_jue_wiki_requested_symbol_coverage_distinguishes_missing_and_omitted() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-missing-vs-omitted",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [{"page_id": "binance.symbol.BTCUSDT"}],
            "requested_symbol_summaries": [
                {"page_id": "binance.symbol.BTCUSDT", "symbol": "BTCUSDT"}
            ],
            "budget_report": {
                "requested_symbol_count": 4,
                "requested_symbol_summary_count": 2,
                "requested_symbol_summary_coverage_status": "partial",
                "requested_symbol_unsummarized_count": 2,
                "requested_symbol_unsummarized_symbols": ["ETHUSDT", "SOLUSDT"],
                "requested_symbol_missing_summary_count": 1,
                "requested_symbol_missing_summary_symbols": ["ETHUSDT"],
                "requested_symbol_prompt_omitted_count": 1,
                "requested_symbol_prompt_omitted_symbols": ["SOLUSDT"],
            },
        },
        max_chars=1000,
    )

    plan = prompt["jue_wiki_application"]["requested_symbol_coverage_action_plan"]
    assert plan["missing_summary_count"] == 1
    assert plan["missing_summary_symbols"] == ["ETHUSDT"]
    assert plan["prompt_omitted_count"] == 1
    assert plan["prompt_omitted_symbols"] == ["SOLUSDT"]
    assert plan["required_adjustments"] == [
        {
            "adjustment_type": "coverage_gap_follow_up",
            "reason": "requested_symbols_missing_from_wiki_summary",
            "symbols": ["ETHUSDT"],
            "resolution": "collect_or_rebuild_summary_before_confident_decision",
        },
        {
            "adjustment_type": "prompt_omission_follow_up",
            "reason": "requested_symbols_omitted_from_prompt_summary",
            "symbols": ["SOLUSDT"],
            "resolution": "treat_as_reviewed_but_lower_confidence_until_direct_summary_check",
        },
    ]
    contract = prompt["jue_wiki_requested_symbol_coverage"]
    assert contract["missing_summary_symbols"] == ["ETHUSDT"]
    assert contract["prompt_omitted_symbols"] == ["SOLUSDT"]
    assert contract["required_adjustments"] == plan["required_adjustments"]


def test_binance_jue_wiki_requested_symbol_coverage_flags_degraded_summaries() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-degraded-summary",
            "prompt_mode": "assist",
            "target_scope": "binance",
            "pages": [{"page_id": "binance.symbol.BTCUSDT"}],
            "requested_symbol_summaries": [
                {
                    "page_id": "binance.symbol.BTCUSDT",
                    "symbol": "BTCUSDT",
                    "freshness": "stale",
                    "quality_status": "degraded",
                    "quality_warnings": ["funding_data_stale"],
                }
            ],
            "budget_report": {
                "requested_symbol_count": 1,
                "requested_symbol_summary_count": 1,
                "requested_symbol_summary_coverage_status": "full",
                "requested_symbol_unsummarized_count": 0,
                "requested_symbol_unsummarized_symbols": [],
                "requested_symbol_degraded_summary_count": 1,
                "requested_symbol_degraded_summary_symbols": ["BTCUSDT"],
                "requested_symbol_degraded_summary_reasons": [
                    {
                        "symbol": "BTCUSDT",
                        "freshness": "stale",
                        "freshness_status": "stale",
                        "freshness_warnings": ["freshness_label_stale"],
                        "quality_status": "degraded",
                        "quality_warnings": ["funding_data_stale"],
                    }
                ],
            },
        },
        max_chars=1000,
    )

    plan = prompt["jue_wiki_application"]["requested_symbol_coverage_action_plan"]
    assert plan["degraded_summary_count"] == 1
    assert plan["degraded_summary_symbols"] == ["BTCUSDT"]
    assert plan["degraded_summary_reasons"] == [
        {
            "symbol": "BTCUSDT",
            "freshness": "stale",
            "freshness_status": "stale",
            "freshness_warnings": ["freshness_label_stale"],
            "quality_status": "weak",
            "quality_warnings": ["funding_data_stale"],
        }
    ]
    assert plan["required_adjustments"] == [
        {
            "adjustment_type": "degraded_summary_cross_check",
            "reason": "requested_symbol_summary_stale_or_weak",
            "symbols": ["BTCUSDT"],
            "resolution": "cross_check_live_research_and_lower_confidence_until_refreshed",
        }
    ]
    contract = prompt["jue_wiki_requested_symbol_coverage"]
    assert contract["status"] == "full"
    assert contract["degraded_summary_symbols"] == ["BTCUSDT"]
    assert contract["degraded_summary_reasons"] == [
        {
            "symbol": "BTCUSDT",
            "freshness": "stale",
            "freshness_status": "stale",
            "freshness_warnings": ["freshness_label_stale"],
            "quality_status": "weak",
            "quality_warnings": ["funding_data_stale"],
        }
    ]


def test_binance_jue_wiki_observe_prompt_context_preserves_mode_policy() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-observe-protective",
            "prompt_mode": "observe",
            "configured_prompt_mode": "primary",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:binance-observe",
                "recommended_mode": "observe",
                "sample_count": 36,
                "confidence": 0.72,
                "reasons": [
                    "prompt_mode_effectiveness:primary:degraded",
                    "primary_avg_return_pct:-0.4500",
                ],
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_observe_recommendation",
            },
            "pages": [{"page_id": "binance.playbook.degraded_primary"}],
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 6,
                "missed_count": 5,
                "resolved_count": 1,
                "resolution_rate": 1 / 6,
                "raw_debug": "DROP_ME",
                "repair_loop_status_metrics": [
                    {
                        "decision_scope": "binance",
                        "repair_loop_status": "repair_required",
                        "action_type": "refresh_crypto_market_structure",
                        "sample_count": 6,
                        "missed_count": 5,
                        "resolved_count": 1,
                        "resolution_rate": 1 / 6,
                        "status": "repair_required",
                    }
                ],
            },
            "validation_repair_effectiveness": {
                "status": "repair_required",
                "sample_count": 9,
                "missed_count": 7,
                "resolved_count": 2,
                "resolution_rate": 2 / 9,
                "raw_debug": "DROP_ME",
                "top_degraded": [
                    {
                        "decision_scope": "binance",
                        "discipline_id": "cost_simulation",
                        "repair_action_id": "collect_spread_fee_edge",
                        "entry_bias": "cost_verified_waiting_entry",
                        "sample_count": 9,
                        "missed_count": 7,
                        "resolved_count": 2,
                        "resolution_rate": 2 / 9,
                        "status": "repair_required",
                        "allowed_entry_postures": ["fractional_kelly_probe"],
                        "blocks_new_entries": ["cost_weak_immediate_entries"],
                        "risk_budget_multiplier": 0.25,
                        "sources": ["binance_validation_repair"],
                        "source_counts": {"binance_validation_repair": 9},
                    }
                ],
            },
            "wiki_application_coverage": {
                "status": "warning",
                "decision_scope": "binance",
                "raw_debug": "DROP_ME",
                "coverage": {
                    "decision_scope": "binance",
                    "decision_link_count": 3,
                    "decision_links_with_selected_wiki_pages": 1,
                    "decision_links_with_selected_wiki_pages_pct": 33.3,
                    "selection_outcome_count": 1,
                    "selection_outcomes_with_selected_wiki_page": 0,
                    "selection_outcomes_with_selected_wiki_page_pct": 0.0,
                    "closed_block_outcomes_without_horizon": 2,
                    "closed_block_outcomes_without_horizon_pct": 50.0,
                },
                "alerts": [
                    {
                        "severity": "warning",
                        "code": "wiki_outcome_feedback_missing",
                        "decision_scope": "binance",
                        "message": "DROP_ME_LONG_MESSAGE",
                        "action": "project_selection_outcomes_and_page_effectiveness",
                    },
                    {
                        "severity": "warning",
                        "code": "wiki_outcome_horizon_missing",
                        "decision_scope": "binance",
                        "message": "closed block feedback lacks lane",
                        "action": "project_selection_outcomes_and_page_effectiveness",
                    }
                ],
            },
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert "jue_wiki" not in prompt
    application = prompt["jue_wiki_application"]
    assert application["configured_prompt_mode"] == "primary"
    assert application["mode_recommendation"]["recommendation_id"] == (
        "wiki-mode:binance-observe"
    )
    assert application["prompt_mode_policy"]["reason"] == (
        "validated_observe_recommendation"
    )
    assert application["trust_profile"]["authority"] == "observation_only"
    assert application["trust_profile"]["posture"] == (
        "primary_demoted_after_underperformance"
    )
    observation_text = json.dumps(
        prompt["jue_wiki_selection_observation"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "DROP_ME" not in observation_text
    assert prompt["jue_wiki_selection_observation"]["repair_priority_effectiveness"][
        "repair_loop_status_summary"
    ]["primary_repair_action_type"] == "refresh_crypto_market_structure"
    validation = prompt["jue_wiki_selection_observation"][
        "validation_repair_effectiveness"
    ]
    assert validation["status"] == "repair_required"
    assert validation["top_degraded"][0]["discipline_id"] == "cost_simulation"
    assert "raw_debug" not in validation
    assert prompt["jue_wiki_application"]["validation_repair_effectiveness"][
        "top_degraded"
    ][0]["repair_action_id"] == "collect_spread_fee_edge"
    assert prompt["jue_wiki_validation_repair_effectiveness"]["top_degraded"][0][
        "entry_bias"
    ] == "cost_verified_waiting_entry"
    validation_plan = prompt["jue_wiki_validation_repair_effectiveness"][
        "validation_repair_action_plan"
    ]
    assert validation_plan["top_disciplines"] == ["cost_simulation"]
    assert validation_plan["allowed_entry_postures"] == ["fractional_kelly_probe"]
    assert validation_plan["blocked_entry_patterns"] == [
        "cost_weak_immediate_entries"
    ]
    assert validation_plan["requires_validation_repair_resolution"] is True
    assert "jue_wiki_validation_repair_effectiveness" in prompt["decision_inputs"]
    validation_contract = prompt["jue_wiki_validation_repair_contract"]
    assert validation_contract["version"] == "jue_wiki_validation_repair_contract_v1"
    assert validation_contract["status"] == "repair_required"
    assert validation_contract["hard_blocker"] is False
    assert validation_contract["requires_validation_repair_resolution"] is True
    assert validation_contract["top_disciplines"] == ["cost_simulation"]
    assert validation_contract["repair_action_ids"] == ["collect_spread_fee_edge"]
    assert validation_contract["contract_feedback_gap"]["legacy_sample_count"] == 9
    assert validation_contract["accepted_resolutions"] == [
        "smaller_probe_block",
        "waiting_entry_with_validation_repair_resolution",
        "candidate_reject_with_missing_validation_named",
        "regime_confirmed_wait",
        "risk_check_defer",
        "new_watch_with_trigger",
        "no_new_entry_until_required_validation_repair_is_resolved",
    ]
    assert "jue_wiki_validation_repair_contract" in prompt["decision_inputs"]
    assert prompt["jue_wiki_contract_feedback_gap"] == {
        "status": "missing_contract_outcomes",
        "legacy_sample_count": 9,
        "contract_sample_count": 0,
        "required_response": (
            "record validation_repair_resolution and resolved_candidates so "
            "future wiki updates can measure contract effectiveness"
        ),
        "source_contract": "jue_wiki_validation_repair_contract",
    }
    assert "jue_wiki_contract_feedback_gap" in prompt["decision_inputs"]
    assert prompt["jue_wiki_selection_observation"]["wiki_application_coverage"][
        "coverage"
    ]["decision_link_count"] == 3
    assert "raw_debug" not in prompt["jue_wiki_selection_observation"][
        "wiki_application_coverage"
    ]
    assert prompt["jue_wiki_application"]["wiki_application_coverage"][
        "alerts"
    ][0]["code"] == "wiki_outcome_feedback_missing"
    assert prompt["jue_wiki_application_coverage"]["coverage"][
        "decision_links_with_selected_wiki_pages_pct"
    ] == 33.3
    assert "jue_wiki_application_coverage" in prompt["decision_inputs"]
    assert prompt["jue_wiki_outcome_horizon_gap"] == {
        "status": "warning",
        "closed_block_outcomes_without_horizon": 2,
        "closed_block_outcomes_without_horizon_pct": 50.0,
        "required_response": (
            "treat wiki closed-block effectiveness as horizon-ambiguous until "
            "outcomes are reprojected with block horizon/lane"
        ),
        "source_contract": "jue_wiki_application_coverage",
    }
    assert "jue_wiki_outcome_horizon_gap" in prompt["decision_inputs"]
    assert prompt["jue_wiki_repair_contract"]["repair_loop_effectiveness"][
        "repair_loop_status_summary"
    ]["repair_action_targets"] == [
        {
            "decision_scope": "binance",
            "status": "repair_required",
            "action_type": "refresh_crypto_market_structure",
            "sample_count": 6,
            "missed_count": 5,
            "resolved_count": 1,
            "metric_count": 1,
            "resolution_rate": 1 / 6,
            "miss_rate": round(5 / 6, 6),
            "repair_pressure_score": round(5 * (5 / 6), 6),
            "recommended_resolution": (
                "refresh_crypto_context_then_rebuild_executable_price"
            ),
            "resolution_steps": [
                "refresh_crypto_research_and_microstructure",
                "rebuild_executable_price_plan",
                "reject_if_depth_spread_funding_conflict",
            ],
            "resolution_success_criteria": [
                "crypto_context_refreshed",
                "executable_price_plan_present",
                "depth_spread_funding_conflict_checked",
            ],
        }
    ]


def test_binance_jue_wiki_observe_prompt_context_preserves_growth_metadata() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-observe-growth",
            "prompt_mode": "observe",
            "target_scope": "binance",
            "pages": [{"page_id": "binance.symbol.NEARUSDT"}],
            "repair_action_batches": [
                {
                    "scope": "binance",
                    "action_type": "refresh_crypto_microstructure",
                    "status": "repair_required",
                    "count": 4,
                    "symbols": ["NEARUSDT", "LTCUSDT"],
                    "raw_debug": "DROP_ME",
                }
            ],
            "evidence_quality": {
                "summary_line": "evidence_quality sources=3 partial=1",
                "status_counts": {"Partial": 1, "strong": 2},
                "top_warnings": ["funding_missing"],
                "raw_debug": "KEEP_AS_EVIDENCE_PAYLOAD",
            },
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    observation = prompt["jue_wiki_selection_observation"]
    assert observation["repair_action_batches"] == [
        {
            "scope": "binance",
            "action_type": "refresh_crypto_microstructure",
            "count": 4,
            "symbols": ["NEARUSDT", "LTCUSDT"],
        }
    ]
    assert observation["evidence_quality"]["summary_line"] == (
        "evidence_quality sources=3 partial=1"
    )
    assert observation["evidence_quality"]["status_counts"] == {
        "partial": 1,
        "strong": 2,
    }
    assert prompt["jue_wiki_repair_contract"]["action_batches"][0][
        "action_type"
    ] == "refresh_crypto_microstructure"


def test_binance_jue_wiki_assist_prompt_compacts_growth_metadata() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-assist-growth",
            "prompt_mode": "assist",
            "pages": [{"page_id": "binance.symbol.NEARUSDT"}],
            "repair_action_batches": [
                {
                    "scope": "binance",
                    "action_type": "refresh_crypto_microstructure",
                    "status": "repair_required",
                    "count": 4,
                    "symbols": ["NEARUSDT", "LTCUSDT"],
                    "raw_debug": "DROP_ME",
                }
            ],
            "evidence_quality": {
                "summary_line": "evidence_quality sources=3 partial=1",
                "status_counts": {"Partial": 1, "strong": 2},
                "raw_debug": "DROP_ME",
            },
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    jue_wiki = prompt["jue_wiki"]
    assert jue_wiki["repair_action_batches"] == [
        {
            "scope": "binance",
            "action_type": "refresh_crypto_microstructure",
            "count": 4,
            "symbols": ["NEARUSDT", "LTCUSDT"],
        }
    ]
    assert jue_wiki["evidence_quality"]["status_counts"] == {
        "partial": 1,
        "strong": 2,
    }
    assert "raw_debug" not in json.dumps(
        jue_wiki,
        ensure_ascii=False,
        sort_keys=True,
    )


def test_binance_jue_wiki_prompt_context_compacts_repair_queue_summary() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    base_payload = {
        "status": "ok",
        "selection_run_id": "selection:binance-repair-queue",
        "target_scope": "binance",
        "pages": [{"page_id": "binance.symbol.NEARUSDT"}],
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
                    "raw_debug": "DROP_QUEUE",
                }
            ],
            "raw_debug": "DROP_QUEUE",
        },
        "budget_report": {"selected_count": 1},
    }

    assist_prompt: dict[str, object] = {}
    _attach_jue_wiki_prompt_context(
        assist_prompt,
        {**base_payload, "prompt_mode": "assist"},
        max_chars=1000,
    )

    assert assist_prompt["jue_wiki"]["repair_queue"] == {
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
            }
        ],
    }
    assert assist_prompt["jue_wiki_application"]["repair_queue"] == (
        assist_prompt["jue_wiki"]["repair_queue"]
    )
    assert "DROP_QUEUE" not in json.dumps(assist_prompt, ensure_ascii=False)

    observe_prompt: dict[str, object] = {}
    _attach_jue_wiki_prompt_context(
        observe_prompt,
        {**base_payload, "prompt_mode": "observe"},
        max_chars=1000,
    )

    assert observe_prompt["jue_wiki_selection_observation"]["repair_queue"] == (
        assist_prompt["jue_wiki"]["repair_queue"]
    )
    assert observe_prompt["jue_wiki_application"]["repair_queue"] == (
        assist_prompt["jue_wiki"]["repair_queue"]
    )
    assert "DROP_QUEUE" not in json.dumps(observe_prompt, ensure_ascii=False)


def test_binance_jue_wiki_observe_prompt_derives_effectiveness_attention_items() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-observe-attention",
            "prompt_mode": "observe",
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
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert "jue_wiki" not in prompt
    expected = [
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
    assert prompt["jue_wiki_selection_observation"][
        "effectiveness_attention_items"
    ] == expected
    assert prompt["jue_wiki_application"]["effectiveness_attention_items"] == expected


def test_binance_jue_wiki_application_exposes_decision_adjustments() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "binance",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 16,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "status": "degraded",
                                "sample_count": 8,
                                "avg_return_pct": -0.35,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "status": "active",
                                "sample_count": 5,
                                "avg_return_pct": 0.24,
                            },
                        ],
                        "decision_adjustment_metrics": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "repair_probe",
                                "reason": "current_risk_posture_degraded",
                                "status": "active",
                                "sample_count": 5,
                                "avg_return_pct": 0.24,
                                "confidence": 0.75,
                            }
                        ],
                    }
                ],
            },
            "pages": [{"page_id": "binance.playbook.supporting"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert prompt["jue_wiki_application"]["decision_adjustments"] == [
        {
            "source": "usage_contract.risk_posture_guidance",
            "action": "shift_to_preferred_risk_posture",
            "target_risk_posture": "repair_probe",
            "reason": "current_risk_posture_degraded",
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
                "status": "active",
                "avg_return_pct": 0.24,
                "confidence": 0.75,
            },
            "evidence_grade": {
                "status": "positive",
                "basis": "decision_adjustment_effectiveness",
                "sample_count": 5,
                "avg_return_pct": 0.24,
                "confidence": 0.75,
                "instruction": "usable_with_live_cross_check",
            },
        }
    ]
    contract = prompt["jue_wiki_decision_adjustments"]
    assert contract["status"] == "active"
    assert contract["hard_filters"] is False
    assert contract["safety_gates_still_override"] is True
    assert contract["evidence_grade_policy"] == {
        "positive": "usable_with_live_cross_check",
        "negative": "audit_or_repair_probe_only",
        "thin_sample": "probe_only_until_more_samples",
        "unproven": "require_live_cross_check",
    }
    assert contract["adjustments"][0]["action"] == "shift_to_preferred_risk_posture"
    assert contract["adjustments"][0]["target_risk_posture"] == "repair_probe"
    assert contract["adjustments"][0]["evidence_grade"] == {
        "status": "positive",
        "basis": "decision_adjustment_effectiveness",
        "sample_count": 5,
        "avg_return_pct": 0.24,
        "confidence": 0.75,
        "instruction": "usable_with_live_cross_check",
    }
    assert "jue_wiki_decision_adjustments" in prompt["decision_inputs"]


def test_binance_jue_wiki_prompt_context_removes_stale_decision_adjustments_input() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {
        "decision_inputs": ["account", "jue_wiki_decision_adjustments"],
    }

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-no-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "binance",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 16,
                    }
                ],
            },
            "pages": [{"page_id": "binance.playbook.clean"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert "decision_adjustments" not in prompt["jue_wiki_application"]
    assert "jue_wiki_decision_adjustments" not in prompt
    assert prompt["decision_inputs"] == ["account"]


def test_binance_jue_wiki_decision_adjustment_audit_contract_attaches_and_clears() -> None:
    from tradecraft.services.binance_block_trader import (
        _attach_jue_wiki_prompt_context,
    )

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-audit-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "binance",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 20,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "status": "degraded",
                                "sample_count": 9,
                                "avg_return_pct": -0.45,
                                "confidence": 1.0,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "status": "active",
                                "sample_count": 6,
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
                            }
                        ],
                    }
                ],
            },
            "pages": [{"page_id": "binance.playbook.audit"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    application = prompt["jue_wiki_application"]
    assert application["decision_adjustments"][0]["action"] == (
        "audit_preferred_risk_posture_before_shift"
    )
    assert prompt["jue_wiki_decision_adjustment_audit_contract"] == {
        "version": "jue_wiki_decision_adjustment_audit_contract_v1",
        "status": "active",
        "adjustment_count": 1,
        "actions": ["audit_preferred_risk_posture_before_shift"],
        "target_risk_postures": ["repair_probe"],
        "required_review": [
            "verify why prior shift_to_preferred_risk_posture underperformed",
            "compare live quote, account state, risk gate, and fresh evidence before adopting target risk posture",
            "if evidence remains weak, use repair probe, waiting block, or explicit rejection instead of direct escalation",
        ],
        "accepted_resolutions": [
            "adopt target risk posture with explicit live evidence override",
            "create a smaller repair probe or waiting block",
            "keep current posture and record what evidence is missing",
            "reject the shift and create a wiki repair note",
        ],
        "hard_blocker": False,
        "safety_gates_still_override": True,
    }
    assert "jue_wiki_decision_adjustment_audit_contract" in prompt["decision_inputs"]

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:binance-no-audit-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "binance",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 20,
                    }
                ],
            },
            "pages": [{"page_id": "binance.playbook.clean"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert "decision_adjustments" not in prompt["jue_wiki_application"]
    assert "jue_wiki_decision_adjustment_audit_contract" not in prompt
    assert "jue_wiki_decision_adjustment_audit_contract" not in prompt.get(
        "decision_inputs", []
    )
