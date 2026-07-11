from __future__ import annotations

import json
import gzip
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tradecraft.services.jue_wiki_application as jue_wiki_application_module
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_application import JueWikiApplicationService


class _ShadowEligibilityReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def eligibility(self, venue: str) -> dict[str, object]:
        self.calls.append(venue)
        now = datetime.now(timezone.utc).isoformat()
        if venue == "kis":
            return {
                "version": "wiki_shadow_eligibility_v1",
                "venue": "kis",
                "required_eligible": True,
                "complete_sample_count": 500,
                "blockers": [],
                "reason": "required_acceptance_gates_passed",
                "evaluated_at": now,
                "evaluated_through": now,
            }
        return {
            "version": "wiki_shadow_eligibility_v1",
            "venue": "binance",
            "required_eligible": False,
            "complete_sample_count": 120,
            "blockers": ["insufficient_complete_comparisons"],
            "reason": "insufficient_complete_comparisons",
            "evaluated_at": now,
            "evaluated_through": now,
        }


def test_shadow_mode_recommendations_are_read_only_and_per_venue() -> None:
    class _NoWriteWiki:
        def initialize(self) -> None:
            raise AssertionError("read-only recommendations must not initialize Wiki")

    reader = _ShadowEligibilityReader()
    service = JueWikiApplicationService(  # type: ignore[arg-type]
        _NoWriteWiki(),
        shadow_eligibility_reader=reader,
    )

    result = service.project_wiki_mode_recommendations()

    assert reader.calls == ["binance", "kis"]
    assert result["status"] == "ok"
    assert result["read_only"] is True
    by_venue = {row["venue"]: row for row in result["recommendations"]}
    assert by_venue["kis"]["recommended_mode"] == "required_eligible"
    assert by_venue["kis"]["sample_count"] == 500
    assert by_venue["binance"]["recommended_mode"] == "prefer"
    assert by_venue["binance"]["blockers"] == ["insufficient_complete_comparisons"]


def test_wiki_mode_recommendation_malformed_reader_fails_closed() -> None:
    class _Malformed:
        def eligibility(self, venue: str) -> dict[str, object]:
            return {
                "version": "wiki_shadow_eligibility_v1",
                "venue": venue,
                "required_eligible": True,
                "complete_sample_count": "not-an-int",
                "blockers": [],
            }

    service = JueWikiApplicationService(  # type: ignore[arg-type]
        object(),
        shadow_eligibility_reader=_Malformed(),
    )

    result = service.project_wiki_mode_recommendations()

    assert all(row["required_eligible"] is False for row in result["recommendations"])
    assert all("eligibility_invalid" in row["blockers"] for row in result["recommendations"])


def test_wiki_mode_recommendation_requires_signed_contract_identity() -> None:
    class _MissingVersion:
        def eligibility(self, venue: str) -> dict[str, object]:
            now = datetime.now(timezone.utc).isoformat()
            return {
                "venue": venue,
                "required_eligible": True,
                "complete_sample_count": 500,
                "blockers": [],
                "evaluated_at": now,
                "evaluated_through": now,
            }

    result = JueWikiApplicationService(  # type: ignore[arg-type]
        object(),
        shadow_eligibility_reader=_MissingVersion(),
    ).project_wiki_mode_recommendations()

    assert all(row["required_eligible"] is False for row in result["recommendations"])
    assert all("eligibility_version_invalid" in row["blockers"] for row in result["recommendations"])


def test_wiki_mode_recommendation_stale_reader_has_stale_blocker() -> None:
    class _Stale:
        def eligibility(self, venue: str) -> dict[str, object]:
            return {
                "version": "wiki_shadow_eligibility_v1",
                "venue": venue,
                "required_eligible": True,
                "complete_sample_count": 500,
                "blockers": [],
                "evaluated_at": "2026-07-01T00:00:00+00:00",
                "evaluated_through": "2026-07-01T00:00:00+00:00",
            }

    service = JueWikiApplicationService(  # type: ignore[arg-type]
        object(), shadow_eligibility_reader=_Stale()
    )

    result = service.project_wiki_mode_recommendations()

    assert all(row["required_eligible"] is False for row in result["recommendations"])
    assert all("eligibility_stale" in row["blockers"] for row in result["recommendations"])
    assert all(row["reason"] == row["blockers"][0] for row in result["recommendations"])


def _service(tmp_path: Path) -> JueWikiApplicationService:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    return JueWikiApplicationService(wiki)


def _kis_mid_effectiveness_metric(metric: dict[str, object]) -> dict[str, object]:
    return {
        **metric,
        "venue": "kis",
        "horizon": "mid",
    }


def test_status_without_ops_snapshot_is_read_only(tmp_path: Path) -> None:
    service = _service(tmp_path)
    db_path = service.wiki.config.db_path

    status = service.status()

    assert status == {
        "status": "unavailable",
        "snapshot_version": "ops_section_snapshot_v1",
        "snapshot_section": "jue_wiki_application",
        "reason": "ops_snapshot_missing",
    }
    assert not db_path.exists()


def test_status_reads_persisted_ops_snapshot_without_projection_or_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    projected = service.project_status_snapshot()
    db_path = service.wiki.config.db_path
    before_bytes = db_path.read_bytes()
    before_mtime_ns = db_path.stat().st_mtime_ns

    def fail_projection(*_args, **_kwargs):
        raise AssertionError("status read path must not project or repair")

    for method_name in (
        "project_page_effectiveness",
        "project_mode_recommendations",
        "project_prompt_mode_effectiveness",
        "project_trust_profile_effectiveness",
        "project_repair_priority_effectiveness",
        "project_validation_repair_effectiveness",
    ):
        monkeypatch.setattr(service, method_name, fail_projection)

    status = service.status()

    assert status == projected
    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime_ns


def test_old_raw_outcomes_move_to_compressed_archive_after_projection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                outcome_kind, outcome_status, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-outcome",
                "link-old",
                "selection-old",
                "page-old",
                "kis",
                "closed_block",
                "win",
                '{"audit":"keep"}',
                "2026-05-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                outcome_kind, outcome_status, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "recent-outcome",
                "link-recent",
                "selection-recent",
                "page-recent",
                "binance",
                "closed_block",
                "loss",
                "{}",
                "2026-06-20T00:00:00+00:00",
            ),
        )

    dry_run = service.archive_selection_outcomes(
        retention_days=30,
        now_iso="2026-07-01T00:00:00+00:00",
        dry_run=True,
    )
    result = service.archive_selection_outcomes(
        retention_days=30,
        now_iso="2026-07-01T00:00:00+00:00",
        dry_run=False,
    )

    assert dry_run["candidate_count"] == 1
    assert dry_run["archived_count"] == 0
    assert result["archived_count"] == 1
    with service.wiki._connect() as conn:
        active_ids = {
            str(row[0])
            for row in conn.execute(
                "SELECT outcome_id FROM wiki_selection_outcomes"
            ).fetchall()
        }
        archived = conn.execute(
            """
            SELECT outcome_id, evidence_gzip, evidence_sha256
            FROM wiki_selection_outcomes_archive
            """
        ).fetchone()
    assert active_ids == {"recent-outcome"}
    assert archived["outcome_id"] == "old-outcome"
    assert gzip.decompress(archived["evidence_gzip"]).decode("utf-8") == (
        '{"audit":"keep"}'
    )
    assert len(str(archived["evidence_sha256"])) == 64


def test_compact_prompt_string_list_drops_none_null_strings() -> None:
    assert jue_wiki_application_module._compact_prompt_string_list(
        [
            None,
            "",
            "None",
            " none ",
            "null",
            "NULL",
            "valuation_stale",
            "valuation_stale",
            "fresh_quote_required",
        ],
        limit=20,
        max_len=40,
    ) == ["valuation_stale", "fresh_quote_required"]


def test_compact_prompt_repair_target_effectiveness_omits_missing_metrics_but_keeps_zero() -> None:
    missing_metrics = jue_wiki_application_module._compact_prompt_repair_target_effectiveness(
        {
            "page_id": "repair_target.refresh_symbol_financials",
            "status": "unknown",
        }
    )
    explicit_zero = jue_wiki_application_module._compact_prompt_repair_target_effectiveness(
        {
            "page_id": "repair_target.refresh_symbol_fundamentals",
            "status": "degraded",
            "sample_count": 0,
            "win_rate": 0.0,
            "expectancy": 0,
            "helpful_score": 0.0,
            "confidence": 0,
        }
    )

    assert missing_metrics == [
        {
            "page_id": "repair_target.refresh_symbol_financials",
            "status": "unknown",
        }
    ]
    assert explicit_zero == [
        {
            "page_id": "repair_target.refresh_symbol_fundamentals",
            "status": "degraded",
            "sample_count": 0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "helpful_score": 0.0,
            "confidence": 0.0,
        }
    ]


def test_compact_prompt_repair_targets_drops_none_null_values() -> None:
    assert jue_wiki_application_module._compact_prompt_repair_targets(
        [
            None,
            "kis.repair.fake",
            {},
            {
                "page_id": "None",
                "symbol": "null",
                "recommended_action": "",
            },
            {
                "page_id": "kis.application.closed_block_outcomes",
                "symbol": "005930",
                "recommended_action": (
                    "reproject_closed_block_outcomes_with_block_horizon"
                ),
            },
        ]
    ) == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "symbol": "005930",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon"
            ),
        }
    ]


def test_compact_prompt_repair_component_targets_omits_missing_metrics_but_keeps_zero() -> None:
    compact = jue_wiki_application_module._compact_prompt_repair_component_targets(
        [
            {
                "component": "repair_loop_status",
                "action_type": "repair_decision_adjustment_audit_contract",
                "status": "repair_required",
            },
            {
                "component": "repair_success_criteria",
                "action_type": "repair_success_criterion",
                "status": "active",
                "sample_count": 0,
                "missed_count": 0,
                "resolved_count": 0,
                "resolution_rate": 0.0,
            },
        ]
    )

    missing_metrics, explicit_zero = compact
    assert missing_metrics == {
        "component": "repair_loop_status",
        "status": "repair_required",
        "action_type": "repair_decision_adjustment_audit_contract",
    }
    assert explicit_zero == {
        "component": "repair_success_criteria",
        "status": "active",
        "action_type": "repair_success_criterion",
        "sample_count": 0,
        "missed_count": 0,
        "resolved_count": 0,
        "resolution_rate": 0.0,
    }


def test_application_top_missed_summaries_require_present_missed_count() -> None:
    missing_loop = JueWikiApplicationService._repair_loop_status_summary(
        [
            {
                "decision_scope": "kis",
                "action_type": "refresh_symbol_financials",
                "status": "probe",
            }
        ]
    )
    missing_directives = (
        JueWikiApplicationService._repair_learning_directive_effectiveness_summary(
            [
                {
                    "decision_scope": "kis",
                    "recommended_action": (
                        "repair_or_demote_success_criterion_before_reuse"
                    ),
                    "status": "probe",
                }
            ]
        )
    )
    missing_steps = (
        JueWikiApplicationService._repair_learning_step_effectiveness_summary(
            [
                {
                    "decision_scope": "kis",
                    "resolution_step": "inspect_failed_repair_directive_outcomes",
                    "status": "probe",
                }
            ]
        )
    )
    missing_resolutions = (
        JueWikiApplicationService._repair_learning_resolution_effectiveness_summary(
            [
                {
                    "decision_scope": "kis",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                    "status": "probe",
                }
            ]
        )
    )

    for summary, top_key, target_key in (
        (missing_loop, "top_missed_action_types", "repair_action_targets"),
        (missing_directives, "top_missed_actions", "action_targets"),
        (missing_steps, "top_missed_steps", "step_targets"),
        (missing_resolutions, "top_missed_resolutions", "resolution_targets"),
    ):
        assert top_key not in summary
        for key in ("max_missed_count", "max_sample_count", "min_resolution_rate"):
            assert key not in summary
        target = summary[target_key][0]
        for key in (
            "sample_count",
            "missed_count",
            "resolved_count",
            "resolution_rate",
            "miss_rate",
            "repair_pressure_score",
        ):
            assert key not in target

    explicit_loop = JueWikiApplicationService._repair_loop_status_summary(
        [
            {
                "decision_scope": "kis",
                "action_type": "refresh_symbol_financials",
                "sample_count": 0,
                "missed_count": 0,
                "resolved_count": 0,
                "resolution_rate": 0.0,
                "status": "probe",
            }
        ]
    )
    explicit_directives = (
        JueWikiApplicationService._repair_learning_directive_effectiveness_summary(
            [
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
            ]
        )
    )
    explicit_steps = (
        JueWikiApplicationService._repair_learning_step_effectiveness_summary(
            [
                {
                    "decision_scope": "kis",
                    "resolution_step": "inspect_failed_repair_directive_outcomes",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "status": "probe",
                }
            ]
        )
    )
    explicit_resolutions = (
        JueWikiApplicationService._repair_learning_resolution_effectiveness_summary(
            [
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
            ]
        )
    )

    assert explicit_loop["top_missed_action_types"] == [
        "refresh_symbol_financials"
    ]
    assert explicit_directives["top_missed_actions"] == [
        "repair_or_demote_success_criterion_before_reuse"
    ]
    assert explicit_steps["top_missed_steps"] == [
        "inspect_failed_repair_directive_outcomes"
    ]
    assert explicit_resolutions["top_missed_resolutions"] == [
        "revise_repair_step_contract_then_probe"
    ]
    for summary, target_key in (
        (explicit_loop, "repair_action_targets"),
        (explicit_directives, "action_targets"),
        (explicit_steps, "step_targets"),
        (explicit_resolutions, "resolution_targets"),
    ):
        assert summary["max_missed_count"] == 0
        assert summary["max_sample_count"] == 0
        assert summary["min_resolution_rate"] == 0.0
        target = summary[target_key][0]
        assert target["sample_count"] == 0
        assert target["missed_count"] == 0
        assert target["resolved_count"] == 0
        assert target["resolution_rate"] == 0.0
        assert target["miss_rate"] == 0.0
        assert target["repair_pressure_score"] == 0.0


def test_quality_warning_effectiveness_omits_missing_metrics_but_keeps_zero() -> None:
    missing_metrics = (
        jue_wiki_application_module._compact_quality_warning_effectiveness_for_prompt(
            {
                "warning": "source_ref_stale",
                "status": "probe",
            }
        )
    )
    explicit_zero = (
        jue_wiki_application_module._compact_quality_warning_effectiveness_for_prompt(
            {
                "warning": "freshness_gap",
                "status": "degraded",
                "sample_count": 0,
                "win_rate": 0,
                "expectancy": 0.0,
                "helpful_score": 0,
                "confidence": 0.0,
            }
        )
    )

    assert missing_metrics == {
        "warning": "source_ref_stale",
        "status": "probe",
    }
    assert explicit_zero == {
        "warning": "freshness_gap",
        "status": "degraded",
        "sample_count": 0,
        "win_rate": 0,
        "expectancy": 0.0,
        "helpful_score": 0,
        "confidence": 0.0,
    }


def test_selected_wiki_page_row_omits_missing_confidence_but_keeps_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    missing_confidence = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
        },
    )
    explicit_zero = service._selected_wiki_page_row(
        page_id="kis.symbol.000660",
        page={
            "page_id": "kis.symbol.000660",
            "page_type": "symbol",
            "confidence": 0,
        },
    )

    assert missing_confidence == {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
    }
    assert explicit_zero == {
        "page_id": "kis.symbol.000660",
        "page_type": "symbol",
        "symbols": ["000660"],
        "confidence": 0.0,
    }


def test_selected_wiki_page_row_recovers_symbols_without_market_name_pollution(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "market": "kis",
        },
    )
    crypto_row = service._selected_wiki_page_row(
        page_id="binance.symbol.BTCUSDT",
        page={
            "page_id": "binance.symbol.BTCUSDT",
            "page_type": "symbol",
            "market": "BTCUSDT",
        },
    )

    assert row["symbols"] == ["005930"]
    assert "KIS" not in row["symbols"]
    assert crypto_row["symbols"] == ["BTCUSDT"]


def test_selected_wiki_page_row_rejects_generic_symbol_text(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.178920",
        page={
            "page_id": "kis.symbol.178920",
            "page_type": "symbol",
            "symbols": ["정보", "투자"],
            "symbol": "정보",
            "ticker": "투자",
            "market": "kis",
        },
    )
    crypto_row = service._selected_wiki_page_row(
        page_id="binance.playbook.breakout",
        page={
            "page_id": "binance.playbook.breakout",
            "page_type": "playbook",
            "symbols": ["정보", "BTCUSDC"],
            "market": "binance",
        },
    )

    assert row["symbols"] == ["178920"]
    assert crypto_row["symbols"] == ["BTCUSDC"]


def test_outcome_symbol_ignores_generic_symbol_text_before_link_symbol() -> None:
    assert (
        JueWikiApplicationService._outcome_symbol(
            link={"symbol": "178920"},
            evidence={"symbol": "정보", "ticker": "투자", "market": "kis"},
        )
        == "178920"
    )


def test_outcome_row_attribution_ignores_generic_evidence_symbol(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert service._outcome_row_is_attributable(
        {
            "page_id": "kis.symbol.178920",
            "evidence": {
                "symbol": "정보",
                "ticker": "투자",
                "selected_wiki_page": {
                    "page_id": "kis.symbol.178920",
                    "page_type": "symbol",
                },
            },
        }
    )


def test_usage_guidance_effectiveness_metric_omits_missing_metrics_and_prefers_explicit_zero_expectancy() -> None:
    missing_metrics = (
        JueWikiApplicationService._compact_usage_guidance_effectiveness_metric(
            {
                "page_id": "usage_guidance.cross_check.live_quote",
                "status": "probe",
                "reasons_json": '["awaiting_live_samples"]',
            }
        )
    )
    explicit_zero = (
        JueWikiApplicationService._compact_usage_guidance_effectiveness_metric(
            {
                "page_id": "usage_guidance.cross_check.fresh_financials",
                "status": "degraded",
                "sample_count": 0,
                "win_rate": 0,
                "expectancy": 0,
                "avg_return_pct": 8.5,
                "helpful_score": 0.0,
                "confidence": 0,
                "reasons_json": '["explicit_zero_expectancy"]',
            }
        )
    )

    assert missing_metrics == {
        "page_id": "usage_guidance.cross_check.live_quote",
        "status": "probe",
        "reasons": ["awaiting_live_samples"],
    }
    assert explicit_zero == {
        "page_id": "usage_guidance.cross_check.fresh_financials",
        "status": "degraded",
        "sample_count": 0,
        "win_rate": 0.0,
        "expectancy": 0.0,
        "helpful_score": 0.0,
        "confidence": 0.0,
        "reasons": ["explicit_zero_expectancy"],
    }


def test_selected_wiki_page_row_summarizes_nested_source_ref_quality_and_repair_queue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.78,
            "symbols": ["005930"],
            "source_refs": [
                {
                    "source_type": "wiki_symbol_summary",
                    "source_id": "kis.symbol.005930:summary",
                    "source_refs": [
                        {
                            "source_type": "symbol_fundamentals",
                            "source_id": "005930:2026-07-03",
                            "evidence_quality": {
                                "status_counts": {"partial": 1},
                                "top_warnings": [
                                    {
                                        "warning": "financials_missing",
                                        "count": 2,
                                    }
                                ],
                            },
                        },
                        {
                            "source_type": "wiki_repair_queue",
                            "status": "scheduled",
                            "action_type": action,
                            "symbols": ["005930"],
                        },
                    ],
                }
            ],
        },
    )

    assert row["quality_status"] == "partial"
    assert row["quality_warnings"] == ["financials_missing"]
    assert row["evidence_quality"]["top_warnings"] == [
        {
            "warning": "financials_missing",
            "count": 1,
        }
    ]
    assert row["repair_queue"] == {
        "status": "scheduled",
        "action_type": action,
        "symbols": ["005930"],
    }


def test_selected_wiki_page_row_exposes_usage_guidance_repair_queue_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    row = service._selected_wiki_page_row(
        page_id="kis.research.repair_queue",
        page={
            "page_id": "kis.research.repair_queue",
            "page_type": "research",
            "confidence": 0.84,
            "symbols": ["005930"],
            "source_refs": [
                {
                    "source_type": "wiki_repair_queue",
                    "source_id": "repair:usage-guidance",
                    "status": "scheduled",
                    "action_type": "repair_usage_guidance_contract",
                    "symbols": ["005930"],
                    "quality_warnings": ["usage_guidance_degraded"],
                    "repair_action": (
                        "repair degraded wiki usage guidance before reusing this "
                        "page usage pattern"
                    ),
                }
            ],
        },
    )

    assert row["repair_queue"] == {
        "status": "scheduled",
        "source_id": "repair:usage-guidance",
        "action_type": "repair_usage_guidance_contract",
        "symbols": ["005930"],
        "quality_warnings": ["usage_guidance_degraded"],
        "repair_action": (
            "repair degraded wiki usage guidance before reusing this page "
            "usage pattern"
        ),
        "decision_use": "usage_guidance_effectiveness_repair",
        "hard_blocker": False,
        "candidate_resolution_required": True,
    }


def test_selected_wiki_page_row_exposes_horizon_repair_diagnostics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    row = service._selected_wiki_page_row(
        page_id="kis.research.repair_queue",
        page={
            "page_id": "kis.research.repair_queue",
            "page_type": "research",
            "confidence": 0.84,
            "symbols": [],
            "source_refs": [
                {
                    "source_type": "wiki_repair_queue",
                    "source_id": "repair:outcome-horizon:kis",
                    "status": "scheduled",
                    "action_type": "reproject_closed_block_outcome_horizons",
                    "symbols": [],
                    "quality_warnings": [
                        "closed_block_outcome_horizon_missing",
                    ],
                    "diagnostic_reasons": [
                        "closed_block_outcomes_without_horizon:3",
                    ],
                    "closed_block_outcomes_without_horizon": 3,
                    "closed_block_outcomes_without_horizon_pct": 75.0,
                    "repair_action": (
                        "reproject closed block outcomes by block horizon or lane"
                    ),
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
        },
    )

    assert row["repair_queue"] == {
        "status": "scheduled",
        "source_id": "repair:outcome-horizon:kis",
        "action_type": "reproject_closed_block_outcome_horizons",
        "quality_warnings": ["closed_block_outcome_horizon_missing"],
        "diagnostic_reasons": ["closed_block_outcomes_without_horizon:3"],
        "closed_block_outcomes_without_horizon": 3,
        "closed_block_outcomes_without_horizon_pct": 75.0,
        "repair_action": "reproject closed block outcomes by block horizon or lane",
        "repair_targets": [
            {
                "page_id": "kis.application.closed_block_outcomes",
                "recommended_action": (
                    "reproject_closed_block_outcomes_with_block_horizon_or_lane"
                ),
            }
        ],
        "decision_use": "horizon_lane_attribution_repair",
        "hard_blocker": False,
        "candidate_resolution_required": True,
    }


def test_selected_wiki_page_row_exposes_memory_card_quality_repair(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.72,
            "symbols": ["005930"],
            "memory_card": {
                "trading_history": (
                    "### Jue Wiki Memory Card Quality\n"
                    "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                    "status=active, resolution=unresolved, symbols=005930, "
                    "required=cross_check_live_research_before_high_confidence"
                )
            },
        },
    )

    assert row["memory_card_quality"] == {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "required_action": "cross_check_live_research_before_high_confidence",
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }


def test_selected_wiki_page_row_preserves_memory_card_quality_repair_details(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.72,
            "symbols": ["005930"],
            "memory_card": {
                "trading_history": (
                    "### Jue Wiki Memory Card Quality\n"
                    "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                    "status=active, resolution=unresolved, symbols=005930, "
                    "required=cross_check_live_research_before_high_confidence, "
                    "missing_fields=durable_facts|lessons, "
                    "required_checks=refresh_durable_facts|inspect_block_lessons\n"
                    "- manager_run=11, observed_at=2026-07-01T09:31:00+09:00, "
                    "status=active, resolution=unresolved, symbols=005930, "
                    "required=record_open_questions_before_confident_action, "
                    "missing_fields=open_questions, "
                    "required_checks=capture_unanswered_research_questions"
                )
            },
        },
    )

    assert row["memory_card_quality"] == {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "required_action": "cross_check_live_research_before_high_confidence",
        "missing_fields": ["durable_facts", "lessons", "open_questions"],
        "required_checks": [
            "refresh_durable_facts",
            "inspect_block_lessons",
            "capture_unanswered_research_questions",
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
            },
            {
                "status": "active",
                "resolution": "unresolved",
                "symbols": ["005930"],
                "required_action": (
                    "record_open_questions_before_confident_action"
                ),
                "missing_fields": ["open_questions"],
                "required_checks": [
                    "capture_unanswered_research_questions",
                ],
            },
        ],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }


def test_selected_wiki_page_row_attaches_memory_card_quality_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "memory_card_quality.missing_field.durable_facts",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 5,
            "win_rate": 0.2,
            "expectancy": -0.8,
            "avg_return_pct": -0.8,
            "median_mae_pct": -1.3,
            "drawdown_pressure": 1.8,
            "helpful_score": -7.5,
            "confidence": 0.85,
            "status": "degraded",
            "reasons": [
                "memory_card_quality:missing_field:durable_facts",
                "memory_card_quality_status:active",
                "memory_card_quality_resolution:unresolved",
            ],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "memory_card_quality.required_check.refresh_durable_facts",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 5,
            "win_rate": 0.6,
            "expectancy": 0.4,
            "avg_return_pct": 0.4,
            "median_mae_pct": -0.5,
            "drawdown_pressure": 0.4,
            "helpful_score": 5.0,
            "confidence": 0.7,
            "status": "active",
            "reasons": [
                "memory_card_quality:required_check:refresh_durable_facts",
                "memory_card_quality_resolution:unresolved",
            ],
        }
    )

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.72,
            "symbols": ["005930"],
            "memory_card": {
                "trading_history": (
                    "### Jue Wiki Memory Card Quality\n"
                    "- manager_run=11, observed_at=2026-07-01T09:30:00+09:00, "
                    "status=active, resolution=unresolved, symbols=005930, "
                    "required=cross_check_live_research_before_high_confidence, "
                    "missing_fields=durable_facts|lessons, "
                    "required_checks=refresh_durable_facts|inspect_block_lessons"
                )
            },
        },
    )

    assert row["memory_card_quality_effectiveness"] == {
        "status": "degraded",
        "metrics": [
            _kis_mid_effectiveness_metric(
                {
                    "page_id": "memory_card_quality.missing_field.durable_facts",
                    "status": "degraded",
                    "sample_count": 5,
                    "win_rate": 0.2,
                    "expectancy": -0.8,
                    "helpful_score": -7.5,
                    "confidence": 0.85,
                    "reasons": [
                        "memory_card_quality:missing_field:durable_facts",
                        "memory_card_quality_status:active",
                        "memory_card_quality_resolution:unresolved",
                    ],
                }
            ),
            _kis_mid_effectiveness_metric(
                {
                    "page_id": (
                        "memory_card_quality.required_check.refresh_durable_facts"
                    ),
                    "status": "active",
                    "sample_count": 5,
                    "win_rate": 0.6,
                    "expectancy": 0.4,
                    "helpful_score": 5.0,
                    "confidence": 0.7,
                    "reasons": [
                        "memory_card_quality:required_check:refresh_durable_facts",
                        "memory_card_quality_resolution:unresolved",
                    ],
                }
            ),
        ],
        "decision_use": (
            "prior memory card quality evidence is degraded; resolve missing "
            "memory fields and required checks before confident block design"
        ),
    }


def test_selected_wiki_page_row_uses_precomputed_memory_card_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "memory_card_quality.missing_field.durable_facts",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 5,
            "win_rate": 0.2,
            "expectancy": -0.8,
            "avg_return_pct": -0.8,
            "median_mae_pct": -1.3,
            "drawdown_pressure": 1.8,
            "helpful_score": -7.5,
            "confidence": 0.85,
            "status": "degraded",
            "reasons": [
                "memory_card_quality:missing_field:durable_facts",
                "memory_card_quality_resolution:unresolved",
            ],
        }
    )
    precomputed_quality = {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "missing_fields": ["durable_facts"],
        "required_checks": ["refresh_durable_facts"],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.72,
            "symbols": ["005930"],
            "memory_card_quality": precomputed_quality,
        },
    )

    assert row["memory_card_quality"] == precomputed_quality
    assert row["memory_card_quality_effectiveness"] == {
        "status": "degraded",
        "metrics": [
            _kis_mid_effectiveness_metric(
                {
                    "page_id": "memory_card_quality.missing_field.durable_facts",
                    "status": "degraded",
                    "sample_count": 5,
                    "win_rate": 0.2,
                    "expectancy": -0.8,
                    "helpful_score": -7.5,
                    "confidence": 0.85,
                    "reasons": [
                        "memory_card_quality:missing_field:durable_facts",
                        "memory_card_quality_resolution:unresolved",
                    ],
                }
            )
        ],
        "decision_use": (
            "prior memory card quality evidence is degraded; resolve missing "
            "memory fields and required checks before confident block design"
        ),
    }


def test_selected_wiki_page_row_uses_precomputed_memory_card_quality_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    precomputed_quality = {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "missing_fields": ["durable_facts"],
        "required_checks": ["refresh_durable_facts"],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }
    precomputed_effectiveness = {
        "status": "active",
        "metrics": [
            {
                "page_id": "memory_card_quality.missing_field.durable_facts",
                "status": "active",
                "sample_count": 4,
                "win_rate": 0.75,
                "expectancy": 0.7,
                "helpful_score": 6.0,
                "confidence": 0.72,
                "reasons": [
                    "memory_card_quality:missing_field:durable_facts",
                    "memory_card_quality_resolution:unresolved",
                ],
            }
        ],
        "decision_use": (
            "prior memory card quality evidence is positive; still keep the "
            "listed memory checks explicit before increasing conviction"
        ),
    }

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.72,
            "symbols": ["005930"],
            "memory_card_quality": precomputed_quality,
            "memory_card_quality_effectiveness": precomputed_effectiveness,
        },
    )

    assert row["memory_card_quality"] == precomputed_quality
    assert row["memory_card_quality_effectiveness"] == precomputed_effectiveness


def test_selected_wiki_page_row_uses_precomputed_usage_guidance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "usage_guidance.risk_posture.standard_block_design",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 7,
            "win_rate": 0.64,
            "expectancy": 0.55,
            "avg_return_pct": 0.55,
            "helpful_score": 6.5,
            "confidence": 0.74,
            "status": "active",
            "reasons": [
                "usage_guidance:risk_posture:standard_block_design",
                "usage_guidance_trust_level:high",
            ],
        }
    )
    precomputed_guidance = {
        "trust_level": "high",
        "risk_posture": "standard_block_design",
        "decision_use": "eligible_for_standard_block_design_after_live_checks",
        "allowed_uses": ["standard_block", "target_stop_context"],
        "required_cross_checks": ["live_quote", "sector_flow"],
        "hard_blocker": False,
    }

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.78,
            "symbols": ["005930"],
            "quality_status": "strong",
            "usage_guidance": precomputed_guidance,
        },
    )

    assert row["usage_guidance"] == precomputed_guidance
    assert row["usage_guidance_effectiveness"] == {
        "status": "active",
        "metrics": [
            _kis_mid_effectiveness_metric(
                {
                    "page_id": "usage_guidance.risk_posture.standard_block_design",
                    "status": "active",
                    "sample_count": 7,
                    "win_rate": 0.64,
                    "expectancy": 0.55,
                    "helpful_score": 6.5,
                    "confidence": 0.74,
                    "reasons": [
                        "usage_guidance:risk_posture:standard_block_design",
                        "usage_guidance_trust_level:high",
                    ],
                }
            )
        ],
        "decision_use": (
            "prior usage guidance has positive evidence; still cross-check live "
            "execution data"
        ),
    }


def test_selected_wiki_page_row_uses_precomputed_usage_guidance_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    precomputed_guidance = {
        "trust_level": "high",
        "risk_posture": "standard_block_design",
        "decision_use": "eligible_for_standard_block_design_after_live_checks",
        "allowed_uses": ["standard_block", "target_stop_context"],
        "required_cross_checks": ["live_quote", "sector_flow"],
        "hard_blocker": False,
    }
    precomputed_effectiveness = {
        "status": "active",
        "metrics": [
            {
                "page_id": "usage_guidance.risk_posture.standard_block_design",
                "status": "active",
                "sample_count": 5,
                "win_rate": 0.6,
                "expectancy": 0.42,
                "helpful_score": 5.8,
                "confidence": 0.7,
                "reasons": [
                    "usage_guidance:risk_posture:standard_block_design",
                    "usage_guidance_trust_level:high",
                ],
            }
        ],
        "decision_use": (
            "prior usage guidance has positive evidence; still cross-check live "
            "execution data"
        ),
    }

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.78,
            "symbols": ["005930"],
            "quality_status": "strong",
            "usage_guidance": precomputed_guidance,
            "usage_guidance_effectiveness": precomputed_effectiveness,
        },
    )

    assert row["usage_guidance"] == precomputed_guidance
    assert row["usage_guidance_effectiveness"] == precomputed_effectiveness


def test_selected_wiki_page_row_guides_weak_pages_to_repair_cross_checks(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.62,
            "symbols": ["005930"],
            "source_refs": [
                {
                    "source_type": "symbol_fundamentals",
                    "source_id": "005930:weak-financials",
                    "quality_status": "weak",
                    "quality_warnings": [
                        "financials_missing",
                        "price_missing",
                    ],
                }
            ],
        },
    )

    assert row["usage_guidance"] == {
        "trust_level": "low",
        "risk_posture": "repair_cross_check",
        "decision_use": (
            "use this page to design repair, waiting, or small probe blocks only "
            "after live cross-checks"
        ),
        "allowed_uses": [
            "repair_candidate_design",
            "waiting_block",
            "small_probe_block",
            "candidate_level_reject",
        ],
        "required_cross_checks": [
            "live_quote",
            "fresh_financials_or_valuation_cross_check",
        ],
        "hard_blocker": False,
    }


def test_selected_wiki_page_row_canonicalizes_quality_status_aliases(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.62,
            "symbols": ["005930"],
            "quality_status": "degraded",
        },
    )

    assert row["quality_status"] == "weak"
    assert row["usage_guidance"]["trust_level"] == "low"
    assert row["usage_guidance"]["risk_posture"] == "repair_cross_check"


def test_selected_wiki_page_usage_guidance_canonicalizes_status_alias() -> None:
    usage = JueWikiApplicationService._selected_wiki_page_usage_guidance(
        quality_status="degraded",
        quality_warnings=[],
    )

    assert usage["trust_level"] == "low"
    assert usage["risk_posture"] == "repair_cross_check"
    assert usage["required_cross_checks"] == ["live_quote", "fresh_research_conflicts"]


def test_quality_pressure_summary_canonicalizes_evidence_quality_status_aliases() -> None:
    summary = jue_wiki_application_module.summarize_jue_wiki_quality_pressure_for_prompt(
        [
            {
                "page_id": "kis.symbol.005930",
                "evidence_quality": {
                    "status_counts": {"ok": 1},
                },
            },
            {
                "page_id": "kis.symbol.245450",
                "evidence_quality": {
                    "status_counts": {"degraded": 1},
                    "top_warnings": [{"warning": "financials_missing", "count": 1}],
                },
            },
        ]
    )

    assert summary["row_count"] == 2
    assert summary["status_counts"] == {"strong": 1, "weak": 1}
    assert summary["weak_page_ids"] == ["kis.symbol.245450"]
    assert summary["caution_page_ids"] == ["kis.symbol.245450"]
    assert summary["warning_counts"] == {"financials_missing": 1}


def test_quality_pressure_summary_tracks_warning_page_ids() -> None:
    summary = jue_wiki_application_module.summarize_jue_wiki_quality_pressure_for_prompt(
        [
            {
                "page_id": "kis.symbol.005930",
                "quality_status": "strong",
                "freshness_status": "stale",
                "freshness_warnings": ["updated_at_stale_gt_14d"],
            },
            {
                "page_id": "kis.symbol.277810",
                "quality_status": "partial",
                "quality_warnings": ["price_missing"],
            },
        ]
    )

    assert summary["warning_page_ids"] == {
        "price_missing": ["kis.symbol.277810"],
        "updated_at_stale_gt_14d": ["kis.symbol.005930"],
    }


def test_prompt_wiki_quality_summary_canonicalizes_status_aliases() -> None:
    summary = JueWikiApplicationService._prompt_wiki_quality_summary(
        {
            "quality_summary": {
                "row_count": 2,
                "status_counts": {"ok": 1, "degraded": 1},
                "top_warnings": [{"warning": "valuation_stale", "count": 1}],
                "weak_page_ids": ["kis.symbol.005930"],
                "caution_page_ids": ["kis.symbol.005930"],
                "warning_page_ids": {
                    "valuation_stale": ["kis.symbol.005930"],
                    "ignored_bad_shape": "kis.symbol.000000",
                },
            }
        }
    )

    assert summary["status_counts"] == {"strong": 1, "weak": 1}
    assert summary["top_warnings"] == [{"warning": "valuation_stale", "count": 1}]
    assert summary["weak_page_ids"] == ["kis.symbol.005930"]
    assert summary["warning_page_ids"] == {
        "valuation_stale": ["kis.symbol.005930"]
    }


def test_selected_wiki_page_row_attaches_usage_guidance_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "usage_guidance.risk_posture.repair_cross_check",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 6,
            "win_rate": 0.25,
            "expectancy": -0.7,
            "avg_return_pct": -0.7,
            "median_mae_pct": -1.1,
            "drawdown_pressure": 1.4,
            "helpful_score": -8.0,
            "confidence": 0.9,
            "status": "degraded",
            "reasons": [
                "usage_guidance:risk_posture:repair_cross_check",
                "usage_guidance_trust_level:low",
            ],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "usage_guidance.cross_check.live_quote",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 6,
            "win_rate": 0.25,
            "expectancy": -0.7,
            "avg_return_pct": -0.7,
            "median_mae_pct": -1.1,
            "drawdown_pressure": 1.4,
            "helpful_score": -8.0,
            "confidence": 0.9,
            "status": "degraded",
            "reasons": ["usage_guidance:cross_check:live_quote"],
        }
    )

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.005930",
        page={
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.62,
            "symbols": ["005930"],
            "source_refs": [
                {
                    "source_type": "symbol_fundamentals",
                    "source_id": "005930:weak-financials",
                    "quality_status": "weak",
                    "quality_warnings": ["financials_missing"],
                }
            ],
        },
    )

    assert row["usage_guidance_effectiveness"] == {
        "status": "degraded",
        "metrics": [
            _kis_mid_effectiveness_metric(
                {
                    "page_id": "usage_guidance.risk_posture.repair_cross_check",
                    "status": "degraded",
                    "sample_count": 6,
                    "win_rate": 0.25,
                    "expectancy": -0.7,
                    "helpful_score": -8.0,
                    "confidence": 0.9,
                    "reasons": [
                        "usage_guidance:risk_posture:repair_cross_check",
                        "usage_guidance_trust_level:low",
                    ],
                }
            ),
            _kis_mid_effectiveness_metric(
                {
                    "page_id": "usage_guidance.cross_check.live_quote",
                    "status": "degraded",
                    "sample_count": 6,
                    "win_rate": 0.25,
                    "expectancy": -0.7,
                    "helpful_score": -8.0,
                    "confidence": 0.9,
                    "reasons": ["usage_guidance:cross_check:live_quote"],
                }
            ),
        ],
        "decision_use": (
            "prior usage guidance is degraded; audit this page usage before "
            "letting it shape block design"
        ),
    }


def test_selected_wiki_page_row_exposes_quality_warning_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.277810",
        page={
            "page_id": "kis.symbol.277810",
            "page_type": "symbol",
            "confidence": 0.58,
            "symbols": ["277810"],
            "evidence_quality": {
                "status_counts": {"weak": 1},
                "top_warnings": [{"warning": "price_missing", "count": 1}],
                "warning_effectiveness": [
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
            },
        },
    )

    assert row["quality_warning_effectiveness"] == [
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
    assert row["quality_warning_effectiveness_statuses"] == ["degraded"]


def test_selected_wiki_page_row_attaches_quality_warning_source_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.277810",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 3,
            "win_rate": 0.0,
            "expectancy": -1.4,
            "avg_return_pct": -1.4,
            "median_mae_pct": -2.1,
            "drawdown_pressure": 2.6,
            "helpful_score": -9.5,
            "confidence": 0.82,
            "status": "degraded",
            "reasons": [
                "quality_warning_source_page",
                "quality_warning:price_missing",
            ],
        }
    )

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.277810",
        page={
            "page_id": "kis.symbol.277810",
            "page_type": "symbol",
            "confidence": 0.58,
            "symbols": ["277810"],
        },
    )

    assert row["quality_warning_source_effectiveness"] == {
        "status": "degraded",
        "metrics": [
            _kis_mid_effectiveness_metric(
                {
                    "page_id": "kis.symbol.277810",
                    "status": "degraded",
                    "sample_count": 3,
                    "win_rate": 0.0,
                    "expectancy": -1.4,
                    "helpful_score": -9.5,
                    "confidence": 0.82,
                    "reasons": [
                        "quality_warning_source_page",
                        "quality_warning:price_missing",
                    ],
                }
            )
        ],
        "decision_use": (
            "this selected page previously contributed to unresolved quality "
            "pressure; require warning-specific repair or live cross-checks before "
            "using it as a block thesis"
        ),
    }


def test_selected_wiki_page_row_uses_precomputed_quality_warning_source_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    precomputed_effectiveness = {
        "status": "active",
        "metrics": [
            {
                "page_id": "kis.symbol.277810",
                "status": "active",
                "sample_count": 4,
                "win_rate": 0.75,
                "expectancy": 0.62,
                "helpful_score": 6.2,
                "confidence": 0.78,
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:valuation_stale_gt_7d",
                ],
            }
        ],
        "decision_use": (
            "this selected page has positive quality-warning source evidence; "
            "still keep warning-specific live checks explicit"
        ),
    }

    row = service._selected_wiki_page_row(
        page_id="kis.symbol.277810",
        page={
            "page_id": "kis.symbol.277810",
            "page_type": "symbol",
            "confidence": 0.58,
            "symbols": ["277810"],
            "quality_warning_source_effectiveness": precomputed_effectiveness,
        },
    )

    assert row["quality_warning_source_effectiveness"] == precomputed_effectiveness


def test_current_wiki_page_summary_flattens_nested_source_refs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 compressed source refs",
        symbols=["005930"],
        content_sections={
            "Current Stance": "compressed evidence",
            "Durable Facts": "facts",
            "Evidence Links": "links",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:fundamentals",
                    },
                    {
                        "source_type": "rag_report",
                        "source_id": "005930:rag",
                    },
                ],
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )

    summary = service._current_wiki_page_summary("kis.symbol.005930")

    assert [
        (ref["source_type"], ref["source_id"])
        for ref in summary["source_refs"]
    ] == [
        ("wiki_symbol_summary", "kis.symbol.005930:summary"),
        ("symbol_fundamentals", "005930:fundamentals"),
        ("rag_report", "005930:rag"),
    ]


def test_build_quality_pressure_action_plan_turns_weak_wiki_into_cross_checks() -> None:
    plan = jue_wiki_application_module.build_jue_wiki_quality_pressure_action_plan_for_prompt(
        {
            "row_count": 2,
            "status_counts": {"partial": 1, "weak": 1},
            "top_warnings": [
                {"warning": "price_missing", "count": 2},
                {"warning": "valuation_stale_gt_30d", "count": 1},
            ],
            "weak_page_ids": ["kis.symbol.005930"],
            "caution_page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
            "warning_page_ids": {
                "price_missing": ["kis.symbol.005930", "kis.symbol.277810"],
                "valuation_stale_gt_30d": ["kis.symbol.005930"],
            },
        }
    )

    assert plan == {
        "status": "repair_required",
        "hard_blocker": False,
        "decision_policy": (
            "use_quality_warnings_as_candidate_level_cross_checks_not_blanket_holds"
        ),
        "required_adjustments": [
            {
                "adjustment_type": "candidate_level_cross_check",
                "reason": "weak_wiki_pages",
                "page_ids": ["kis.symbol.005930"],
                "resolution": "refresh_or_cross_check_before_sizing",
            },
            {
                "adjustment_type": "quality_warning_resolution",
                "warning": "price_missing",
                "count": 2,
                "resolution": "refresh_or_cross_check_before_sizing",
                "page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
            },
            {
                "adjustment_type": "quality_warning_resolution",
                "warning": "valuation_stale_gt_30d",
                "count": 1,
                "resolution": "refresh_or_cross_check_before_sizing",
                "page_ids": ["kis.symbol.005930"],
            },
        ],
        "repair_focus": [
            {
                "priority_type": "evidence_quality",
                "warning": "price_missing",
                "count": 2,
                "decision_use": "evidence_quality_cross_check",
                "page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
            },
            {
                "priority_type": "evidence_quality",
                "warning": "valuation_stale_gt_30d",
                "count": 1,
                "decision_use": "evidence_quality_cross_check",
                "page_ids": ["kis.symbol.005930"],
            },
        ],
        "caution_page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
    }


def test_build_quality_pressure_action_plan_canonicalizes_status_aliases() -> None:
    plan = jue_wiki_application_module.build_jue_wiki_quality_pressure_action_plan_for_prompt(
        {
            "row_count": 1,
            "status_counts": {"degraded": 1},
        }
    )

    assert plan["status"] == "repair_required"
    assert plan["hard_blocker"] is False


def test_quality_pressure_action_plan_ignores_invalid_warning_page_ids() -> None:
    plan = jue_wiki_application_module.build_jue_wiki_quality_pressure_action_plan_for_prompt(
        {
            "row_count": 1,
            "status_counts": {"partial": 1},
            "top_warnings": [{"warning": "price_missing", "count": 1}],
            "warning_page_ids": {"price_missing": "kis.symbol.005930"},
        }
    )

    assert plan["required_adjustments"] == [
        {
            "adjustment_type": "quality_warning_resolution",
            "warning": "price_missing",
            "count": 1,
            "resolution": "refresh_or_cross_check_before_sizing",
        }
    ]


def test_quality_pressure_action_plan_keeps_warning_effectiveness() -> None:
    summary = jue_wiki_application_module.summarize_jue_wiki_quality_pressure_for_prompt(
        [
            {
                "page_id": "kis.symbol.005930",
                "quality_status": "partial",
                "evidence_quality": {
                    "top_warnings": [
                        {"warning": "financials_missing", "count": 1}
                    ],
                    "warning_effectiveness": [
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
                    ],
                },
            }
        ]
    )
    plan = (
        jue_wiki_application_module.build_jue_wiki_quality_pressure_action_plan_for_prompt(
            summary
        )
    )

    expected_effectiveness = {
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
    assert summary["top_warnings"] == [
        {
            "warning": "financials_missing",
            "count": 1,
            "effectiveness": expected_effectiveness,
        }
    ]
    assert plan["required_adjustments"] == [
        {
            "adjustment_type": "quality_warning_resolution",
            "warning": "financials_missing",
            "count": 1,
            "resolution": "repair_or_cross_check_before_sizing",
            "effectiveness": expected_effectiveness,
            "page_ids": ["kis.symbol.005930"],
        }
    ]
    assert plan["repair_focus"] == [
        {
            "priority_type": "evidence_quality",
            "warning": "financials_missing",
            "count": 1,
            "decision_use": "evidence_quality_cross_check",
            "effectiveness_status": "degraded",
            "effectiveness": expected_effectiveness,
            "page_ids": ["kis.symbol.005930"],
        }
    ]
    assert plan["status"] == "repair_required"
    assert plan["quality_effectiveness_pressure"] == {
        "status": "repair_required",
        "degraded_warnings": ["financials_missing"],
        "probe_warnings": [],
        "active_warnings": [],
    }


def test_record_decision_link_persists_selection_trace(tmp_path: Path) -> None:
    service = _service(tmp_path)

    link = service.record_decision_link(
        selection_run_id="selection:abc",
        manager_run_id="kis-manager-1",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930", "kis.playbook.reflection_lessons"],
        symbol="005930",
        block_id="blk-1",
        horizon="mid_term",
        action="create_block",
        prompt_mode="assist",
        metadata={"source": "unit"},
    )

    rows = service.list_decision_links(selection_run_id="selection:abc")

    assert link["status"] == "ok"
    assert rows[0]["manager_run_id"] == "kis-manager-1"
    assert rows[0]["selected_pages"] == [
        "kis.symbol.005930",
        "kis.playbook.reflection_lessons",
    ]
    assert rows[0]["metadata"] == {"source": "unit"}


def test_record_decision_link_cleans_selected_page_ids(tmp_path: Path) -> None:
    service = _service(tmp_path)

    service.record_decision_link(
        selection_run_id="selection:clean-pages",
        manager_run_id="kis-manager-clean-pages",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=[
            None,
            "",
            "null",
            "None",
            "kis.symbol.005930",
            "kis.symbol.005930",
            "kis.playbook.reflection_lessons",
        ],
        symbol="005930",
        block_id="blk-clean-pages",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
    )

    rows = service.list_decision_links(selection_run_id="selection:clean-pages")

    assert rows[0]["selected_pages"] == [
        "kis.symbol.005930",
        "kis.playbook.reflection_lessons",
    ]


def test_list_decision_links_cleans_legacy_selected_page_ids(tmp_path: Path) -> None:
    service = _service(tmp_path)

    link = service.record_decision_link(
        selection_run_id="selection:legacy-dirty-pages",
        manager_run_id="kis-manager-legacy-dirty-pages",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        block_id="blk-legacy-dirty-pages",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            UPDATE wiki_decision_links
            SET selected_pages_json = ?
            WHERE link_id = ?
            """,
            (
                json.dumps(
                    [
                        None,
                        "",
                        "null",
                        "None",
                        "kis.symbol.005930",
                        "kis.symbol.005930",
                        "kis.playbook.reflection_lessons",
                    ]
                ),
                link["link_id"],
            ),
        )

    rows = service.list_decision_links(
        selection_run_id="selection:legacy-dirty-pages"
    )

    assert rows[0]["selected_pages"] == [
        "kis.symbol.005930",
        "kis.playbook.reflection_lessons",
    ]


def test_selected_page_summaries_from_link_cleans_and_merges_metadata_pages() -> None:
    summaries = JueWikiApplicationService._selected_page_summaries_from_link(
        {
            "metadata": {
                "selected_wiki_pages": {
                    "pages": [
                        None,
                        "kis.symbol.fake",
                        {},
                        {"page_id": ""},
                        {"page_id": "None", "confidence": 0.9},
                        {"page_id": "null", "symbols": ["000000"]},
                        {
                            "page_id": "kis.symbol.005930",
                            "confidence": 0.62,
                        },
                        {
                            "page_id": "kis.symbol.005930",
                            "symbols": ["005930"],
                            "quality_warnings": ["valuation_stale"],
                        },
                        {
                            "page_id": "kis.playbook.reflection_lessons",
                            "page_type": "playbook",
                        },
                    ]
                }
            }
        }
    )

    assert summaries == {
        "kis.symbol.005930": {
            "page_id": "kis.symbol.005930",
            "confidence": 0.62,
            "symbols": ["005930"],
            "quality_warnings": ["valuation_stale"],
        },
        "kis.playbook.reflection_lessons": {
            "page_id": "kis.playbook.reflection_lessons",
            "page_type": "playbook",
        },
    }


def test_record_decision_link_infers_unique_symbol_page_when_symbol_generic(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.record_decision_link(
        selection_run_id="selection:infer-symbol",
        manager_run_id="kis-manager-infer",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.178920", "kis.playbook.reflection_lessons"],
        symbol="정보",
        block_id="blk-178920",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
    )

    rows = service.list_decision_links(selection_run_id="selection:infer-symbol")

    assert rows[0]["symbol"] == "178920"


def test_record_decision_link_infers_unique_symbol_from_selected_page_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.record_decision_link(
        selection_run_id="selection:infer-symbol-metadata",
        manager_run_id="kis-manager-infer-metadata",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.playbook.reflection_lessons"],
        symbol="정보",
        block_id="blk-178920",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.178920",
                        "page_type": "symbol",
                        "symbols": ["178920"],
                    },
                    {
                        "page_id": "kis.playbook.reflection_lessons",
                        "page_type": "playbook",
                    },
                ]
            }
        },
    )

    rows = service.list_decision_links(
        selection_run_id="selection:infer-symbol-metadata"
    )

    assert rows[0]["symbol"] == "178920"


def test_record_decision_link_infers_unique_symbol_from_fallback_summary_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.record_decision_link(
        selection_run_id="selection:infer-symbol-fallback-summary",
        manager_run_id="kis-manager-infer-fallback",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.playbook.reflection_lessons"],
        symbol="정보",
        block_id="blk-178920",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "effectiveness_fallback_symbols": ["178920"],
                "effectiveness_fallback_page_symbols": [
                    {
                        "page_id": "kis.playbook.reflection_lessons",
                        "symbols": ["178920"],
                    }
                ],
            }
        },
    )

    rows = service.list_decision_links(
        selection_run_id="selection:infer-symbol-fallback-summary"
    )

    assert rows[0]["symbol"] == "178920"


def test_record_decision_link_does_not_infer_ambiguous_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.record_decision_link(
        selection_run_id="selection:ambiguous-symbol",
        manager_run_id="kis-manager-ambiguous",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=[
            "kis.symbol.178920",
            "kis.symbol.005930",
            "kis.playbook.reflection_lessons",
        ],
        symbol="정보",
        block_id="blk-ambiguous",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
    )

    rows = service.list_decision_links(selection_run_id="selection:ambiguous-symbol")

    assert rows[0]["symbol"] == ""


def test_record_decision_link_does_not_infer_ambiguous_fallback_summary_symbols(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.record_decision_link(
        selection_run_id="selection:ambiguous-fallback-summary-symbol",
        manager_run_id="kis-manager-ambiguous-fallback",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.playbook.reflection_lessons"],
        symbol="정보",
        block_id="blk-ambiguous-fallback",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "effectiveness_fallback_symbols": ["178920", "005930"],
            }
        },
    )

    rows = service.list_decision_links(
        selection_run_id="selection:ambiguous-fallback-summary-symbol"
    )

    assert rows[0]["symbol"] == ""


def test_record_decision_link_does_not_infer_ambiguous_metadata_symbols(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.record_decision_link(
        selection_run_id="selection:ambiguous-metadata-symbol",
        manager_run_id="kis-manager-ambiguous-metadata",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.playbook.reflection_lessons"],
        symbol="정보",
        block_id="blk-ambiguous-metadata",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.178920",
                        "page_type": "symbol",
                        "symbols": ["178920"],
                    },
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    },
                ]
            }
        },
    )

    rows = service.list_decision_links(
        selection_run_id="selection:ambiguous-metadata-symbol"
    )

    assert rows[0]["symbol"] == ""


def test_project_outcomes_records_page_level_result(tmp_path: Path) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:abc",
        manager_run_id="binance-manager-1",
        decision_scope="binance",
        decision_type="block_manager",
        selected_pages=["binance.playbook.live.binance.edge"],
        symbol="BTCUSDT",
        block_id="bin-blk-1",
        venue="binance",
        horizon="short_term",
        action="create_block",
        prompt_mode="assist",
    )

    result = service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1.25,
        pnl_currency="USDT",
        return_pct=0.42,
        mfe_pct=0.9,
        mae_pct=-0.2,
        holding_minutes=48,
        evidence={"exit_reason": "target"},
    )

    rows = service.list_selection_outcomes(selection_run_id="selection:abc")

    assert result["status"] == "ok"
    assert result["outcome_count"] == 1
    assert rows[0]["page_id"] == "binance.playbook.live.binance.edge"
    assert rows[0]["outcome_status"] == "win"
    assert rows[0]["evidence"] == {"exit_reason": "target"}


def test_project_prompt_mode_effectiveness_aggregates_decision_level_outcomes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.record_selection_run(
        run_id="selection:primary-win",
        target_scope="kis",
        request={
            "target_scope": "kis",
            "prompt_mode_application": {
                "target_scope": "kis",
                "mode_recommendation": {
                    "recommendation_id": "wiki-mode:kis-primary",
                    "recommended_mode": "primary",
                    "sample_count": 45,
                    "confidence": 0.75,
                },
            },
        },
        selected_pages=[
            {"page_id": "kis.symbol.005930", "rank": 1, "score": 10.0},
            {"page_id": "kis.playbook.value_cycle", "rank": 2, "score": 8.0},
        ],
        rejected_pages=[],
        char_count=100,
        max_chars=1000,
        status="ok",
    )
    primary_link = service.record_decision_link(
        selection_run_id="selection:primary-win",
        manager_run_id="kis-manager-primary",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930", "kis.playbook.value_cycle"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="primary",
    )
    service.record_selection_outcomes(
        link_id=primary_link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        return_pct=2.4,
        evidence={"block_id": "kis-primary-block"},
    )
    service.wiki.record_selection_run(
        run_id="selection:observe-loss",
        target_scope="kis",
        request={
            "target_scope": "kis",
            "prompt_mode_application": {
                "target_scope": "kis",
                "mode_recommendation": {
                    "recommendation_id": "wiki-mode:kis-observe",
                    "recommended_mode": "observe",
                    "sample_count": 0,
                    "confidence": 0.65,
                },
            },
        },
        selected_pages=[{"page_id": "kis.symbol.000660", "rank": 1, "score": 8.0}],
        rejected_pages=[],
        char_count=90,
        max_chars=1000,
        status="ok",
    )
    observe_link = service.record_decision_link(
        selection_run_id="selection:observe-loss",
        manager_run_id="kis-manager-observe",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.000660"],
        symbol="000660",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="observe",
    )
    service.record_selection_outcomes(
        link_id=observe_link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        return_pct=-1.2,
        evidence={"block_id": "kis-observe-block"},
    )

    result = service.project_prompt_mode_effectiveness(min_samples=1)

    by_mode = {row["prompt_mode"]: row for row in result["modes"]}
    assert by_mode["primary"]["decision_scope"] == "kis"
    assert by_mode["primary"]["sample_count"] == 1
    assert by_mode["primary"]["win_rate"] == 1.0
    assert by_mode["primary"]["avg_return_pct"] == 2.4
    assert by_mode["primary"]["status"] == "active"
    assert by_mode["primary"]["recommended_mode_counts"] == {"primary": 1}
    assert by_mode["observe"]["sample_count"] == 1
    assert by_mode["observe"]["win_rate"] == 0.0
    assert by_mode["observe"]["avg_return_pct"] == -1.2
    assert by_mode["observe"]["status"] == "degraded"
    assert result["decision_sample_count"] == 2


def test_project_trust_profile_effectiveness_groups_by_authority_and_posture(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    primary_link = service.record_decision_link(
        selection_run_id="selection:trust-primary-win",
        manager_run_id="kis-manager-trust-primary",
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
                "recommendation_id": "wiki-mode:trust-primary",
                "usage_contract": {
                    "risk_posture": "knowledge_spine",
                    "allowed_uses": ["block_thesis_design", "target_stop_design"],
                    "required_cross_checks": ["live_quote", "risk_gate"],
                },
            }
        },
    )
    service.record_selection_outcomes(
        link_id=primary_link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        return_pct=1.8,
        evidence={"block_id": "trust-primary-win"},
    )
    observe_link = service.record_decision_link(
        selection_run_id="selection:trust-observe-loss",
        manager_run_id="kis-manager-trust-observe",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.000660"],
        symbol="000660",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="observe",
        metadata={
            "jue_wiki_trust_profile": {
                "authority": "observation_only",
                "trust_level": "low",
                "posture": "primary_demoted_after_underperformance",
                "prompt_mode": "observe",
                "recommendation_id": "wiki-mode:trust-observe",
                "usage_contract": {
                    "risk_posture": "observe_repair",
                    "allowed_uses": ["audit_context", "repair_candidate_design"],
                    "required_cross_checks": ["live_quote", "risk_gate"],
                },
            }
        },
    )
    service.record_selection_outcomes(
        link_id=observe_link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        return_pct=-0.9,
        evidence={"block_id": "trust-observe-loss"},
    )

    result = service.project_trust_profile_effectiveness(min_samples=1)

    by_authority = {row["authority"]: row for row in result["trust_profiles"]}
    assert by_authority["primary_compiled_knowledge"]["sample_count"] == 1
    assert by_authority["primary_compiled_knowledge"]["status"] == "active"
    assert by_authority["primary_compiled_knowledge"]["avg_return_pct"] == 1.8
    assert by_authority["primary_compiled_knowledge"]["usage_contract_counts"] == {
        "risk_posture": {"knowledge_spine": 1},
        "allowed_uses": {
            "block_thesis_design": 1,
            "target_stop_design": 1,
        },
        "required_cross_checks": {
            "live_quote": 1,
            "risk_gate": 1,
        },
    }
    assert by_authority["observation_only"]["sample_count"] == 1
    assert by_authority["observation_only"]["status"] == "degraded"
    assert by_authority["observation_only"]["posture_counts"] == {
        "primary_demoted_after_underperformance": 1
    }
    assert by_authority["observation_only"]["usage_contract_counts"] == {
        "risk_posture": {"observe_repair": 1},
        "allowed_uses": {
            "audit_context": 1,
            "repair_candidate_design": 1,
        },
        "required_cross_checks": {
            "live_quote": 1,
            "risk_gate": 1,
        },
    }
    assert result["decision_sample_count"] == 2


def test_project_trust_profile_effectiveness_breaks_down_risk_posture_performance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, (risk_posture, return_pct) in enumerate(
        [
            ("supporting_evidence", 1.2),
            ("repair_probe", -1.4),
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:trust-posture-{idx}",
            manager_run_id=f"kis-manager-trust-posture-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.symbol.00593{idx}"],
            symbol=f"00593{idx}",
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
                        "risk_posture": risk_posture,
                        "allowed_uses": ["candidate_ranking"],
                        "required_cross_checks": ["live_quote", "risk_gate"],
                    },
                }
            },
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="win" if return_pct > 0 else "loss",
            return_pct=return_pct,
            evidence={"block_id": f"trust-posture-{idx}"},
        )

    result = service.project_trust_profile_effectiveness(min_samples=1)

    profile = result["trust_profiles"][0]
    by_posture = {
        row["risk_posture"]: row
        for row in profile["risk_posture_metrics"]
    }
    assert by_posture["supporting_evidence"]["sample_count"] == 1
    assert by_posture["supporting_evidence"]["status"] == "active"
    assert by_posture["supporting_evidence"]["avg_return_pct"] == 1.2
    assert by_posture["repair_probe"]["sample_count"] == 1
    assert by_posture["repair_probe"]["status"] == "degraded"
    assert by_posture["repair_probe"]["avg_return_pct"] == -1.4


def test_project_trust_profile_effectiveness_tracks_decision_adjustment_performance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate([1.6, -0.4]):
        link = service.record_decision_link(
            selection_run_id=f"selection:trust-adjustment-{idx}",
            manager_run_id=f"kis-manager-trust-adjustment-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.symbol.00593{idx}"],
            symbol=f"00593{idx}",
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
                "jue_wiki_decision_adjustments": [
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
                        ],
                        "deprioritized_allowed_uses": [
                            "candidate_ranking",
                            "target_stop_context",
                        ],
                        "evidence_grade": {
                            "status": "positive" if return_pct > 0 else "negative",
                            "basis": "decision_adjustment_effectiveness",
                            "sample_count": 6,
                            "avg_return_pct": 0.72 if return_pct > 0 else -0.7,
                            "confidence": 0.9,
                            "instruction": (
                                "usable_with_live_cross_check"
                                if return_pct > 0
                                else "audit_or_repair_probe_only"
                            ),
                        },
                    }
                ],
            },
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="win" if return_pct > 0 else "loss",
            return_pct=return_pct,
            evidence={"block_id": f"trust-adjustment-{idx}"},
        )

    result = service.project_trust_profile_effectiveness(min_samples=1)

    profile = result["trust_profiles"][0]
    metric = profile["decision_adjustment_metrics"][0]
    assert metric["action"] == "shift_to_preferred_risk_posture"
    assert metric["target_risk_posture"] == "repair_probe"
    assert metric["reason"] == "current_risk_posture_degraded"
    assert metric["current_risk_posture"] == "supporting_evidence"
    assert metric["sample_count"] == 2
    assert metric["win_rate"] == 0.5
    assert metric["avg_return_pct"] == 0.6000000000000001
    assert metric["status"] == "active"
    assert metric["recommended_allowed_uses_counts"] == {
        "repair_candidate_design": 2,
        "small_probe_block": 2,
    }
    assert metric["deprioritized_allowed_uses_counts"] == {
        "candidate_ranking": 2,
        "target_stop_context": 2,
    }
    assert metric["evidence_grade_counts"] == {
        "negative": 1,
        "positive": 1,
    }
    assert metric["evidence_grade_instruction_counts"] == {
        "audit_or_repair_probe_only": 1,
        "usable_with_live_cross_check": 1,
    }
    assert metric["evidence_grade_performance"] == [
        {
            "status": "negative",
            "instruction": "audit_or_repair_probe_only",
            "basis": "decision_adjustment_effectiveness",
            "sample_count": 1,
            "win_rate": 0.0,
            "avg_return_pct": -0.4,
        },
        {
            "status": "positive",
            "instruction": "usable_with_live_cross_check",
            "basis": "decision_adjustment_effectiveness",
            "sample_count": 1,
            "win_rate": 1.0,
            "avg_return_pct": 1.6,
        },
    ]


def test_prompt_wiki_decision_adjustments_summary_adds_evidence_grade() -> None:
    rows = JueWikiApplicationService._prompt_wiki_decision_adjustments_summary(
        {
            "decision_adjustments": [
                {
                    "source": "usage_contract.risk_posture_guidance",
                    "action": "audit_preferred_risk_posture_before_shift",
                    "target_risk_posture": "repair_probe",
                    "reason": "prior_decision_adjustment_degraded",
                    "decision_adjustment_effectiveness": {
                        "action": "shift_to_preferred_risk_posture",
                        "target_risk_posture": "repair_probe",
                        "reason": "current_risk_posture_degraded",
                        "sample_count": 5,
                        "status": "degraded",
                        "avg_return_pct": -0.7,
                        "confidence": 0.9,
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
                            }
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
                }
            ]
        }
    )

    assert rows[0]["evidence_grade"] == {
        "status": "positive",
        "basis": "decision_adjustment_audit_effectiveness",
        "sample_count": 4,
        "avg_return_pct": 0.5,
        "confidence": 0.8,
        "instruction": "usable_with_live_cross_check",
    }
    assert rows[0]["decision_adjustment_effectiveness"][
        "evidence_grade_performance"
    ] == [
        {
            "status": "negative",
            "instruction": "audit_or_repair_probe_only",
            "basis": "decision_adjustment_effectiveness",
            "sample_count": 3,
            "win_rate": 0.0,
            "avg_return_pct": -1.2,
        }
    ]
    assert rows[0]["decision_adjustment_effectiveness"]["execution_hint"] == (
        "cap_to_audit_or_repair_probe"
    )


def test_project_trust_profile_effectiveness_tracks_decision_adjustment_audit_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate([1.1, -0.3]):
        link = service.record_decision_link(
            selection_run_id=f"selection:trust-audit-{idx}",
            manager_run_id=f"kis-manager-trust-audit-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.symbol.00594{idx}"],
            symbol=f"00594{idx}",
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
                    "status": "active",
                    "adjustment_count": 1,
                    "actions": [
                        "audit_preferred_risk_posture_before_shift",
                    ],
                    "target_risk_postures": ["repair_probe"],
                    "required_review": [
                        "verify why prior shift_to_preferred_risk_posture underperformed",
                        "compare live quote, account state, risk gate, and fresh evidence before adopting target risk posture",
                    ],
                    "accepted_resolutions": [
                        "create a smaller repair probe or waiting block",
                        "reject the shift and create a wiki repair note",
                    ],
                    "hard_blocker": False,
                    "safety_gates_still_override": True,
                },
            },
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="win" if return_pct > 0 else "loss",
            return_pct=return_pct,
            evidence={"block_id": f"trust-audit-{idx}"},
        )

    result = service.project_trust_profile_effectiveness(min_samples=1)

    profile = result["trust_profiles"][0]
    metric = profile["decision_adjustment_audit_metrics"][0]
    assert metric["action"] == "audit_preferred_risk_posture_before_shift"
    assert metric["target_risk_posture"] == "repair_probe"
    assert metric["sample_count"] == 2
    assert metric["win_rate"] == 0.5
    assert metric["avg_return_pct"] == 0.4
    assert metric["status"] == "active"
    assert metric["required_review_counts"] == {
        "compare live quote, account state, risk gate, and fresh evidence before adopting target risk posture": 2,
        "verify why prior shift_to_preferred_risk_posture underperformed": 2,
    }
    assert metric["accepted_resolution_counts"] == {
        "create a smaller repair probe or waiting block": 2,
        "reject the shift and create a wiki repair note": 2,
    }


def test_status_includes_prompt_mode_effectiveness_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.record_selection_run(
        run_id="selection:status-primary-win",
        target_scope="kis",
        request={
            "target_scope": "kis",
            "prompt_mode_application": {
                "target_scope": "kis",
                "mode_recommendation": {
                    "recommendation_id": "wiki-mode:status-primary",
                    "recommended_mode": "primary",
                    "sample_count": 45,
                    "confidence": 0.75,
                },
            },
        },
        selected_pages=[{"page_id": "kis.symbol.005930", "rank": 1, "score": 10.0}],
        rejected_pages=[],
        char_count=100,
        max_chars=1000,
        status="ok",
    )
    link = service.record_decision_link(
        selection_run_id="selection:status-primary-win",
        manager_run_id="kis-manager-status-primary",
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
            }
        },
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        return_pct=1.1,
        evidence={"block_id": "status-primary-block"},
    )

    status = service.project_status_snapshot()

    mode_effectiveness = status["prompt_mode_effectiveness"]
    assert mode_effectiveness["decision_sample_count"] == 1
    assert mode_effectiveness["modes"][0]["decision_scope"] == "kis"
    assert mode_effectiveness["modes"][0]["prompt_mode"] == "primary"
    assert mode_effectiveness["modes"][0]["recommended_mode_counts"] == {
        "primary": 1
    }
    trust_effectiveness = status["trust_profile_effectiveness"]
    assert trust_effectiveness["decision_sample_count"] == 1
    assert trust_effectiveness["trust_profiles"][0]["authority"] == (
        "primary_compiled_knowledge"
    )


def test_status_summarizes_degraded_decision_adjustment_audit_metrics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(5):
        link = service.record_decision_link(
            selection_run_id=f"selection:status-audit-{idx}",
            manager_run_id=f"kis-manager-status-audit-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.symbol.00595{idx}"],
            symbol=f"00595{idx}",
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
                    "actions": [
                        "audit_preferred_risk_posture_before_shift",
                    ],
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
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=-0.6,
            evidence={"block_id": f"status-audit-{idx}"},
        )

    status = service.project_status_snapshot()

    audit_status = status["decision_adjustment_audit_status"]
    assert audit_status["status"] == "repair_required"
    assert audit_status["metric_count"] == 1
    assert audit_status["degraded_count"] == 1
    assert audit_status["repair_required_count"] == 1
    assert audit_status["top_degraded"][0] == {
        "decision_scope": "kis",
        "authority": "supporting_evidence",
        "action": "audit_preferred_risk_posture_before_shift",
        "target_risk_posture": "repair_probe",
        "sample_count": 5,
        "avg_return_pct": -0.6,
        "status": "degraded",
        "contract_status": "repair_required",
    }


def test_status_summarizes_repair_priority_effectiveness(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-effectiveness-{idx}",
            manager_run_id=f"kis-manager-repair-effectiveness-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.005930"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_jue_wiki_repair_contract",
                "repair_priority_types": ["decision_adjustment_audit"],
                "repair_action_types": [
                    "repair_decision_adjustment_audit_contract"
                ],
                "repair_source_ids": [
                    "kis:supporting_evidence:"
                    "audit_preferred_risk_posture_before_shift:repair_probe"
                ],
                "repair_decision_uses": ["decision_adjustment_audit_repair"],
                "repair_loop_statuses": ["repair_required"],
                "repair_loop_action_types": [
                    "repair_decision_adjustment_audit_contract"
                ],
                "repair_loop_sample_counts": [3],
                "repair_loop_missed_counts": [2],
                "repair_loop_resolved_counts": [0],
                "repair_loop_resolution_rates": [0.0],
                "repair_resolution_success_criteria": [
                    "audit_contract_repaired_or_demoted"
                ],
                "repair_learning_recommended_actions": [
                    "repair_or_demote_success_criterion_before_reuse"
                ],
                "repair_learning_resolution_steps": [
                    "inspect_failed_repair_directive_outcomes",
                    "revise_or_demote_learning_directive",
                    "record_next_outcome_before_reuse",
                ],
                "repair_learning_step_recommended_resolutions": [
                    "revise_repair_step_contract_then_probe"
                ],
            },
        )

    status = service.project_status_snapshot()

    repair_effectiveness = status["repair_priority_effectiveness"]
    assert repair_effectiveness["status"] == "repair_required"
    assert repair_effectiveness["sample_count"] == 3
    assert repair_effectiveness["missed_count"] == 2
    assert repair_effectiveness["resolved_count"] == 1
    assert repair_effectiveness["resolution_rate"] == 1 / 3
    assert repair_effectiveness["top_degraded"][0] == {
        "decision_scope": "kis",
        "priority_type": "decision_adjustment_audit",
        "action_type": "repair_decision_adjustment_audit_contract",
        "decision_use": "decision_adjustment_audit_repair",
        "source_id": (
            "kis:supporting_evidence:"
            "audit_preferred_risk_posture_before_shift:repair_probe"
        ),
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
    }
    assert repair_effectiveness["repair_loop_status_metrics"][0] == {
        "decision_scope": "kis",
        "repair_loop_status": "repair_required",
        "action_type": "repair_decision_adjustment_audit_contract",
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
    assert repair_effectiveness["repair_success_criteria_metrics"][0] == {
        "decision_scope": "kis",
        "criterion": "audit_contract_repaired_or_demoted",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
    }
    assert repair_effectiveness["repair_success_criteria_summary"] == {
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
    assert repair_effectiveness["repair_learning_resolution_metrics"][0] == {
        "decision_scope": "kis",
        "recommended_resolution": "revise_repair_step_contract_then_probe",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
    }
    assert repair_effectiveness["repair_learning_resolution_summary"] == {
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
    assert repair_effectiveness["repair_learning_directive_metrics"][0] == {
        "decision_scope": "kis",
        "recommended_action": "repair_or_demote_success_criterion_before_reuse",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
    }
    assert repair_effectiveness["repair_learning_directive_summary"] == {
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
    assert repair_effectiveness["repair_learning_step_metrics"][0] == {
        "decision_scope": "kis",
        "resolution_step": "inspect_failed_repair_directive_outcomes",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
    }
    assert repair_effectiveness["repair_learning_step_summary"] == {
        "metric_count": 3,
        "repair_required_count": 3,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_resolution_step": "inspect_failed_repair_directive_outcomes",
        "repair_required_steps": [
            "inspect_failed_repair_directive_outcomes",
            "record_next_outcome_before_reuse",
            "revise_or_demote_learning_directive",
        ],
        "top_missed_steps": [
            "inspect_failed_repair_directive_outcomes",
            "record_next_outcome_before_reuse",
            "revise_or_demote_learning_directive",
        ],
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
            },
            {
                "decision_scope": "kis",
                "status": "repair_required",
                "resolution_step": "record_next_outcome_before_reuse",
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
            },
            {
                "decision_scope": "kis",
                "status": "repair_required",
                "resolution_step": "revise_or_demote_learning_directive",
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
            },
        ],
        "max_missed_count": 2,
        "max_sample_count": 3,
        "min_resolution_rate": 1 / 3,
    }


def test_status_summarizes_validation_repair_effectiveness(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_validation_probe",
            "missed_validation_probe",
            "resolved_validation_probe",
            "missed_validation_probe",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:validation-effectiveness-{idx}",
            manager_run_id=f"kis-manager-validation-effectiveness-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.risk.trading_validation"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="manager_run",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.06,
            evidence={
                "source": (
                    "kis_validation_repair_contract"
                    if idx == 3
                    else "kis_validation_repair"
                ),
                "discipline_ids": ["cost_simulation"],
                "repair_action_ids": ["collect_cost_edge"],
                "entry_biases": ["waiting_probe_until_cost_edge_clean"],
                "allowed_entry_postures": ["shadow_or_waiting_entry_only"],
                "blocks_new_entries": [
                    "scale_up_and_unvalidated_immediate_entries"
                ],
                "risk_budget_multiplier": 0.25,
                "max_budget_multiplier": 0.25,
                **(
                    {
                        "degraded_metric_evidence": [
                            {
                                "discipline_id": "cost_simulation",
                                "repair_action_id": "collect_cost_edge",
                                "entry_bias": (
                                    "waiting_probe_until_cost_edge_clean"
                                ),
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
                    if idx == 3
                    else {}
                ),
            },
        )

    status = service.project_status_snapshot()

    validation = status["validation_repair_effectiveness"]
    assert validation["status"] == "repair_required"
    assert validation["sample_count"] == 4
    assert validation["missed_count"] == 3
    assert validation["resolved_count"] == 1
    assert validation["resolution_rate"] == 0.25
    assert validation["top_degraded"][0] == {
        "decision_scope": "kis",
        "discipline_id": "cost_simulation",
        "repair_action_id": "collect_cost_edge",
        "entry_bias": "waiting_probe_until_cost_edge_clean",
        "sample_count": 4,
        "missed_count": 3,
        "resolved_count": 1,
        "resolution_rate": 0.25,
        "status": "repair_required",
        "allowed_entry_postures": ["shadow_or_waiting_entry_only"],
        "blocks_new_entries": ["scale_up_and_unvalidated_immediate_entries"],
        "risk_budget_multiplier": 0.25,
        "max_budget_multiplier": 0.25,
        "sources": ["kis_validation_repair", "kis_validation_repair_contract"],
        "source_counts": {
            "kis_validation_repair": 3,
            "kis_validation_repair_contract": 1,
        },
        "contract_basis_sample_count": 9,
        "contract_basis_missed_count": 6,
        "contract_basis_resolved_count": 3,
        "contract_basis_resolution_rate": 1 / 3,
        "contract_basis_miss_rate": 2 / 3,
        "contract_basis_repair_pressure_score": 4.0,
        "contract_basis_status": "repair_required",
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
                "source_counts": {"kis_validation_repair_contract": 9},
            }
        ],
    }


def test_status_preserves_explicit_zero_validation_repair_metrics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(3):
        link = service.record_decision_link(
            selection_run_id=f"selection:validation-explicit-zero-{idx}",
            manager_run_id=f"kis-manager-validation-explicit-zero-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.risk.trading_validation"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="manager_run",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="missed_validation_probe",
            outcome_status="loss",
            return_pct=-0.03,
            evidence={
                "source": "kis_validation_repair_contract",
                "discipline_ids": ["cost_simulation"],
                "repair_action_ids": ["collect_cost_edge"],
                "entry_biases": ["waiting_probe_until_cost_edge_clean"],
                "risk_budget_multiplier": 0.0,
                "max_budget_multiplier": 0.0,
                "degraded_metric_evidence": [
                    {
                        "discipline_id": "cost_simulation",
                        "repair_action_id": "collect_cost_edge",
                        "entry_bias": "waiting_probe_until_cost_edge_clean",
                        "sample_count": 3,
                        "missed_count": 3,
                        "resolved_count": 0,
                        "resolution_rate": 0.0,
                        "status": "repair_required",
                        "source_counts": {"kis_validation_repair_contract": 3},
                    }
                ],
            },
        )

    validation = service.project_status_snapshot()[
        "validation_repair_effectiveness"
    ]

    assert validation["top_degraded"][0]["sample_count"] == 3
    assert validation["top_degraded"][0]["missed_count"] == 3
    assert validation["top_degraded"][0]["resolved_count"] == 0
    assert validation["top_degraded"][0]["resolution_rate"] == 0.0
    assert validation["top_degraded"][0]["risk_budget_multiplier"] == 0.0
    assert validation["top_degraded"][0]["max_budget_multiplier"] == 0.0
    assert validation["top_degraded"][0]["contract_basis_resolved_count"] == 0
    assert validation["top_degraded"][0]["contract_basis_resolution_rate"] == 0.0


def test_repair_priority_effectiveness_preserves_quality_warnings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:usage-guidance-repair-{idx}",
            manager_run_id=f"kis-manager-usage-guidance-repair-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.research.repair_queue"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="repair_queue",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_selected_wiki_pages_repair_queue",
                "repair_priority_types": ["repair_queue"],
                "repair_action_types": ["repair_usage_guidance_contract"],
                "repair_source_ids": ["repair:usage-guidance"],
                "repair_decision_uses": [
                    "usage_guidance_effectiveness_repair"
                ],
                "quality_warnings": ["usage_guidance_degraded"],
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness(
        min_samples=1
    )

    assert repair_effectiveness["top_degraded"][0] == {
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


def test_repair_priority_effectiveness_preserves_memory_card_quality_gaps(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:memory-card-repair-{idx}",
            manager_run_id=f"kis-manager-memory-card-repair-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.005930"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="repair_queue",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_selected_wiki_pages_repair_queue",
                "repair_priority_types": ["memory_card_quality"],
                "repair_action_types": ["cross_check_memory_card_quality"],
                "repair_source_ids": ["kis.symbol.005930:memory_card_quality"],
                "repair_decision_uses": [
                    "memory_card_quality_resolution_check"
                ],
                "repair_missing_fields": ["durable_facts", "lessons"],
                "repair_required_checks": [
                    "refresh_durable_facts",
                    "inspect_block_lessons",
                ],
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness(
        min_samples=1
    )

    assert repair_effectiveness["top_degraded"][0] == {
        "decision_scope": "kis",
        "priority_type": "memory_card_quality",
        "action_type": "cross_check_memory_card_quality",
        "decision_use": "memory_card_quality_resolution_check",
        "source_id": "kis.symbol.005930:memory_card_quality",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "status": "repair_required",
        "repair_missing_fields": ["durable_facts", "lessons"],
        "repair_required_checks": [
            "refresh_durable_facts",
            "inspect_block_lessons",
        ],
    }


def test_repair_priority_effectiveness_summarizes_memory_card_quality_gaps(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    rows = [
        (
            "005930",
            "missed_repair_priority",
            ["durable_facts", "lessons"],
            ["refresh_durable_facts", "inspect_block_lessons"],
        ),
        (
            "005930",
            "missed_repair_priority",
            ["durable_facts"],
            ["refresh_durable_facts"],
        ),
        (
            "000660",
            "resolved_repair_priority",
            ["durable_facts", "risk_notes"],
            ["refresh_durable_facts", "inspect_risk_notes"],
        ),
    ]
    for idx, (symbol, outcome_kind, missing_fields, required_checks) in enumerate(
        rows
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:memory-card-gap-summary-{idx}",
            manager_run_id=f"kis-manager-memory-card-gap-summary-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.symbol.{symbol}"],
            symbol=symbol,
            venue="kis",
            horizon="mid",
            action="repair_queue",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_selected_wiki_pages_repair_queue",
                "repair_priority_types": ["memory_card_quality"],
                "repair_action_types": ["cross_check_memory_card_quality"],
                "repair_source_ids": [f"kis.symbol.{symbol}:memory_card_quality"],
                "repair_decision_uses": [
                    "memory_card_quality_resolution_check"
                ],
                "repair_missing_fields": missing_fields,
                "repair_required_checks": required_checks,
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness(
        min_samples=1
    )

    assert repair_effectiveness["memory_card_quality_gap_summary"] == {
        "status": "repair_required",
        "missing_field_counts": {
            "durable_facts": 3,
            "lessons": 1,
            "risk_notes": 1,
        },
        "missing_field_missed_counts": {
            "durable_facts": 2,
            "lessons": 1,
            "risk_notes": 0,
        },
        "required_check_counts": {
            "inspect_block_lessons": 1,
            "inspect_risk_notes": 1,
            "refresh_durable_facts": 3,
        },
        "required_check_missed_counts": {
            "inspect_block_lessons": 1,
            "inspect_risk_notes": 0,
            "refresh_durable_facts": 2,
        },
        "priority_missing_fields": ["durable_facts", "lessons"],
        "priority_required_checks": [
            "refresh_durable_facts",
            "inspect_block_lessons",
        ],
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
            {"field": "durable_facts", "sample_count": 3, "missed_count": 2},
            {"field": "lessons", "sample_count": 1, "missed_count": 1},
            {"field": "risk_notes", "sample_count": 1, "missed_count": 0},
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
            {
                "check": "inspect_risk_notes",
                "sample_count": 1,
                "missed_count": 0,
            },
        ],
    }


def test_repair_priority_effectiveness_prioritizes_missed_memory_card_quality_gaps(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    rows = [
        (
            "missed_repair_priority",
            ["durable_facts"],
            ["refresh_durable_facts"],
        ),
        (
            "missed_repair_priority",
            ["durable_facts"],
            ["refresh_durable_facts"],
        ),
        (
            "resolved_repair_priority",
            ["risk_notes"],
            ["inspect_risk_notes"],
        ),
    ]
    for idx, (outcome_kind, missing_fields, required_checks) in enumerate(rows):
        link = service.record_decision_link(
            selection_run_id=f"selection:memory-card-priority-gap-{idx}",
            manager_run_id=f"kis-manager-memory-card-priority-gap-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.005930"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="repair_queue",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_selected_wiki_pages_repair_queue",
                "repair_priority_types": ["memory_card_quality"],
                "repair_action_types": ["cross_check_memory_card_quality"],
                "repair_source_ids": ["kis.symbol.005930:memory_card_quality"],
                "repair_decision_uses": [
                    "memory_card_quality_resolution_check"
                ],
                "repair_missing_fields": missing_fields,
                "repair_required_checks": required_checks,
            },
        )

    summary = service.project_repair_priority_effectiveness(
        min_samples=1
    )["memory_card_quality_gap_summary"]

    assert summary["priority_missing_fields"] == ["durable_facts"]
    assert summary["priority_required_checks"] == ["refresh_durable_facts"]
    assert summary["priority_focus"] == {
        "missing_field": "durable_facts",
        "missing_field_sample_count": 2,
        "missing_field_missed_count": 2,
        "required_check": "refresh_durable_facts",
        "required_check_sample_count": 2,
        "required_check_missed_count": 2,
        "instruction": "resolve_priority_memory_card_quality_gap_first",
    }


def test_repair_loop_action_targets_preserve_quality_warnings() -> None:
    targets = JueWikiApplicationService._repair_loop_action_targets(
        [
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
        ]
    )

    assert targets[0]["quality_warnings"] == ["usage_guidance_degraded"]


def test_repair_loop_action_targets_preserve_impacted_identifiers() -> None:
    targets = JueWikiApplicationService._repair_loop_action_targets(
        [
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
        ]
    )

    assert targets[0]["impacted_page_ids"] == ["kis.symbol.245450"]
    assert targets[0]["impacted_symbols"] == ["245450"]
    assert targets[0]["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]


def test_repair_loop_action_targets_preserve_repair_target_effectiveness() -> None:
    targets = JueWikiApplicationService._repair_loop_action_targets(
        [
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
        ]
    )

    assert targets[0]["repair_target_effectiveness"] == [
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
    assert targets[0]["repair_target_effectiveness_statuses"] == ["degraded"]


def test_prompt_repair_target_effectiveness_omits_missing_metrics_but_keeps_zero() -> None:
    missing_metrics = (
        JueWikiApplicationService._prompt_repair_target_effectiveness_summary(
            {
                "page_id": "repair_target.refresh_symbol_financials",
                "status": "unknown",
            }
        )
    )
    explicit_zero = (
        JueWikiApplicationService._prompt_repair_target_effectiveness_summary(
            {
                "page_id": "repair_target.refresh_symbol_fundamentals",
                "status": "degraded",
                "sample_count": 0,
                "win_rate": 0.0,
                "expectancy": 0,
                "helpful_score": 0.0,
                "confidence": 0,
            }
        )
    )

    assert missing_metrics == {
        "page_id": "repair_target.refresh_symbol_financials",
        "status": "unknown",
    }
    assert explicit_zero == {
        "page_id": "repair_target.refresh_symbol_fundamentals",
        "status": "degraded",
        "sample_count": 0,
        "win_rate": 0.0,
        "expectancy": 0.0,
        "helpful_score": 0.0,
        "confidence": 0.0,
    }


def test_prompt_repair_contract_summary_omits_missing_priority_metrics_but_keeps_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    summary = service._prompt_repair_contract_summary(
        {
            "jue_wiki_repair_contract": {
                "status": "active",
                "repair_priority_count": 2,
                "top_priorities": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "priority_type": "freshness",
                        "repair_loop_status": "probe",
                    },
                    {
                        "page_id": "kis.symbol.000660",
                        "page_type": "symbol",
                        "priority_type": "quality",
                        "sample_count": 0,
                        "win_rate": 0.0,
                        "expectancy": 0,
                        "helpful_score": 0.0,
                        "drawdown_pressure": 0,
                        "repair_loop_sample_count": 0,
                        "repair_loop_missed_count": 0,
                        "repair_loop_resolved_count": 0,
                        "repair_loop_resolution_rate": 0.0,
                    },
                ],
            }
        }
    )

    missing_metrics, explicit_zero = summary["top_priorities"]
    assert missing_metrics == {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "priority_type": "freshness",
        "repair_loop_status": "probe",
    }
    assert explicit_zero == {
        "page_id": "kis.symbol.000660",
        "page_type": "symbol",
        "sample_count": 0,
        "win_rate": 0.0,
        "expectancy": 0.0,
        "helpful_score": 0.0,
        "drawdown_pressure": 0.0,
        "priority_type": "quality",
        "repair_loop_sample_count": 0,
        "repair_loop_missed_count": 0,
        "repair_loop_resolved_count": 0,
        "repair_loop_resolution_rate": 0.0,
    }


def test_prompt_repair_contract_summary_canonicalizes_quality_status_alias(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service._prompt_repair_contract_summary(
        {
            "jue_wiki_repair_contract": {
                "status": "active",
                "repair_priority_count": 1,
                "top_priorities": [
                    {
                        "page_id": "decision_adjustment_audit.kis.sample",
                        "page_type": "policy",
                        "priority_type": "decision_adjustment_audit",
                        "quality_status": "degraded",
                        "quality_warnings": ["decision_adjustment_audit_degraded"],
                    }
                ],
            }
        }
    )

    assert summary["top_priorities"][0]["quality_status"] == "weak"


def test_prompt_repair_loop_effectiveness_omits_missing_metrics_but_keeps_zero() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
        {
            "status": "probe",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "priority_type": "freshness",
                    "status": "probe",
                },
                {
                    "decision_scope": "kis",
                    "priority_type": "quality",
                    "status": "degraded",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                },
            ],
        }
    )

    rows = {
        row["priority_type"]: row
        for row in summary["top_degraded"]
    }
    assert summary["status"] == "probe"
    assert "sample_count" not in summary
    assert "missed_count" not in summary
    assert "resolved_count" not in summary
    assert "resolution_rate" not in summary
    assert rows["freshness"] == {
        "decision_scope": "kis",
        "priority_type": "freshness",
        "status": "probe",
    }
    assert rows["quality"] == {
        "decision_scope": "kis",
        "priority_type": "quality",
        "sample_count": 0,
        "missed_count": 0,
        "resolved_count": 0,
        "resolution_rate": 0.0,
        "status": "degraded",
    }


def test_prompt_repair_loop_effectiveness_preserves_memory_card_quality_gaps() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
        {
            "status": "repair_required",
            "top_degraded": [
                {
                    "decision_scope": "kis",
                    "priority_type": "memory_card_quality",
                    "action_type": "cross_check_memory_card_quality",
                    "decision_use": "memory_card_quality_resolution_check",
                    "source_id": "kis.symbol.005930:memory_card_quality",
                    "sample_count": 3,
                    "missed_count": 2,
                    "resolved_count": 1,
                    "resolution_rate": 1 / 3,
                    "status": "repair_required",
                    "repair_missing_fields": [
                        "durable_facts",
                        "lessons",
                        "unused_extra_field",
                    ],
                    "repair_required_checks": [
                        "refresh_durable_facts",
                        "inspect_block_lessons",
                        "unused_extra_check",
                    ],
                }
            ],
        }
    )

    assert summary["top_degraded"][0] == {
        "decision_scope": "kis",
        "priority_type": "memory_card_quality",
        "action_type": "cross_check_memory_card_quality",
        "decision_use": "memory_card_quality_resolution_check",
        "source_id": "kis.symbol.005930:memory_card_quality",
        "status": "repair_required",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "repair_missing_fields": [
            "durable_facts",
            "lessons",
            "unused_extra_field",
        ],
        "repair_required_checks": [
            "refresh_durable_facts",
            "inspect_block_lessons",
            "unused_extra_check",
        ],
    }


def test_prompt_repair_loop_effectiveness_preserves_memory_card_gap_summary() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
        {
            "status": "repair_required",
            "memory_card_quality_gap_summary": {
                "status": "repair_required",
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
        }
    )

    assert summary["memory_card_quality_gap_summary"] == {
        "status": "repair_required",
        "missing_field_counts": {"durable_facts": 3, "lessons": 1},
        "missing_field_missed_counts": {"durable_facts": 2, "lessons": 1},
        "required_check_counts": {
            "inspect_block_lessons": 1,
            "refresh_durable_facts": 3,
        },
        "required_check_missed_counts": {
            "inspect_block_lessons": 1,
            "refresh_durable_facts": 2,
        },
        "priority_missing_fields": ["durable_facts", "lessons"],
        "priority_required_checks": [
            "refresh_durable_facts",
            "inspect_block_lessons",
        ],
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
            {"field": "durable_facts", "sample_count": 3, "missed_count": 2},
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
    }


def test_prompt_memory_card_quality_gap_summary_prioritizes_missed_terms() -> None:
    summary = JueWikiApplicationService._prompt_memory_card_quality_gap_summary(
        {
            "status": "repair_required",
            "missing_field_counts": {"durable_facts": 3, "risk_notes": 2},
            "missing_field_missed_counts": {
                "durable_facts": 2,
                "risk_notes": 0,
            },
            "required_check_counts": {
                "refresh_durable_facts": 3,
                "inspect_risk_notes": 2,
            },
            "required_check_missed_counts": {
                "refresh_durable_facts": 2,
                "inspect_risk_notes": 0,
            },
            "top_missing_fields": [
                {"field": "durable_facts", "sample_count": 3, "missed_count": 2},
                {"field": "risk_notes", "sample_count": 2, "missed_count": 0},
            ],
            "top_required_checks": [
                {
                    "check": "refresh_durable_facts",
                    "sample_count": 3,
                    "missed_count": 2,
                },
                {"check": "inspect_risk_notes", "sample_count": 2, "missed_count": 0},
            ],
        }
    )

    assert summary["priority_missing_fields"] == ["durable_facts"]
    assert summary["priority_required_checks"] == ["refresh_durable_facts"]
    assert summary["priority_focus"] == {
        "missing_field": "durable_facts",
        "missing_field_sample_count": 3,
        "missing_field_missed_count": 2,
        "required_check": "refresh_durable_facts",
        "required_check_sample_count": 3,
        "required_check_missed_count": 2,
        "instruction": "resolve_priority_memory_card_quality_gap_first",
    }


def test_prompt_repair_component_summaries_omit_missing_metrics_but_keep_zero() -> None:
    target_summary = (
        JueWikiApplicationService._prompt_repair_component_target_summary(
            {
                "worst_status": "probe",
                "primary_component_target": "repair_loop_status_metrics",
                "top_component_targets": ["repair_loop_status_metrics"],
            }
        )
    )
    status_summary = (
        JueWikiApplicationService._prompt_repair_component_status_summary(
            {
                "worst_status": "probe",
                "components": [
                    {
                        "component": "repair_loop_status_metrics",
                        "worst_status": "probe",
                    },
                    {
                        "component": "repair_success_criteria_metrics",
                        "metric_count": 0,
                        "repair_required_count": 0,
                        "probe_count": 0,
                        "active_count": 0,
                        "unknown_count": 0,
                        "worst_status": "active",
                    },
                ],
            }
        )
    )

    assert target_summary == {
        "worst_status": "probe",
        "primary_component_target": "repair_loop_status_metrics",
        "top_component_targets": ["repair_loop_status_metrics"],
    }
    missing_metrics, explicit_zero = status_summary["components"]
    assert status_summary == {
        "worst_status": "probe",
        "components": [missing_metrics, explicit_zero],
    }
    assert missing_metrics == {
        "component": "repair_loop_status_metrics",
        "worst_status": "probe",
    }
    assert explicit_zero == {
        "component": "repair_success_criteria_metrics",
        "metric_count": 0,
        "repair_required_count": 0,
        "probe_count": 0,
        "active_count": 0,
        "unknown_count": 0,
        "worst_status": "active",
    }


def test_prompt_validation_repair_contract_omits_missing_metrics_but_keeps_zero_false(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    missing = service._prompt_validation_repair_contract_summary(
        {
            "jue_wiki_validation_repair_contract": {
                "status": "probe",
                "contract_basis_pressure_summary": {
                    "status": "probe",
                },
                "contract_feedback_gap": {
                    "status": "needs_contract_feedback",
                },
            }
        }
    )
    explicit_zero = service._prompt_validation_repair_contract_summary(
        {
            "jue_wiki_validation_repair_contract": {
                "status": "active",
                "hard_blocker": False,
                "requires_validation_repair_resolution": False,
                "risk_budget_multiplier": 0.0,
                "legacy_sample_count": 0,
                "contract_sample_count": 0,
                "safety_gates_still_override": False,
                "contract_basis_pressure_summary": {
                    "status": "active",
                    "sample_count": 0,
                    "missed_count": 0,
                    "resolved_count": 0,
                    "resolution_rate": 0.0,
                    "miss_rate": 0.0,
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
        "status": "probe",
        "contract_basis_pressure_summary": {"status": "probe"},
        "contract_feedback_gap": {"status": "needs_contract_feedback"},
    }
    assert explicit_zero == {
        "status": "active",
        "hard_blocker": False,
        "requires_validation_repair_resolution": False,
        "risk_budget_multiplier": 0.0,
        "legacy_sample_count": 0,
        "contract_sample_count": 0,
        "contract_basis_pressure_summary": {
            "sample_count": 0,
            "missed_count": 0,
            "resolved_count": 0,
            "resolution_rate": 0.0,
            "miss_rate": 0.0,
            "repair_pressure_score": 0.0,
            "status": "active",
        },
        "contract_feedback_gap": {
            "status": "closed",
            "legacy_sample_count": 0,
            "contract_sample_count": 0,
        },
        "safety_gates_still_override": False,
    }


def test_prompt_decision_adjustment_audit_contract_omits_missing_counts_but_keeps_zero_false(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    missing = service._prompt_decision_adjustment_audit_contract_summary(
        {
            "jue_wiki_decision_adjustment_audit_contract": {
                "status": "probe",
                "actions": ["review_degraded_adjustment"],
            }
        }
    )
    explicit_zero = service._prompt_decision_adjustment_audit_contract_summary(
        {
            "jue_wiki_decision_adjustment_audit_contract": {
                "status": "active",
                "adjustment_count": 0,
                "hard_blocker": False,
                "safety_gates_still_override": False,
                "actions": ["review_degraded_adjustment"],
            }
        }
    )

    assert missing == {
        "status": "probe",
        "actions": ["review_degraded_adjustment"],
    }
    assert explicit_zero == {
        "status": "active",
        "adjustment_count": 0,
        "actions": ["review_degraded_adjustment"],
        "hard_blocker": False,
        "safety_gates_still_override": False,
    }


def test_compact_contract_basis_pressure_omits_missing_metrics_but_keeps_zero() -> None:
    missing_pressure = JueWikiApplicationService._compact_contract_basis_pressure_summary(
        {
            "status": "probe",
        }
    )
    missing_gap = JueWikiApplicationService._compact_contract_feedback_gap(
        {
            "status": "waiting_for_contract_outcomes",
        }
    )
    explicit_zero_pressure = (
        JueWikiApplicationService._compact_contract_basis_pressure_summary(
            {
                "status": "active",
                "sample_count": 0,
                "missed_count": 0,
                "resolved_count": 0,
                "resolution_rate": 0.0,
                "miss_rate": 0,
                "repair_pressure_score": 0.0,
            }
        )
    )
    explicit_zero_gap = JueWikiApplicationService._compact_contract_feedback_gap(
        {
            "status": "closed",
            "legacy_sample_count": 0,
            "contract_sample_count": 0,
        }
    )

    assert missing_pressure == {"status": "probe"}
    assert missing_gap == {"status": "waiting_for_contract_outcomes"}
    assert explicit_zero_pressure == {
        "sample_count": 0,
        "missed_count": 0,
        "resolved_count": 0,
        "resolution_rate": 0.0,
        "miss_rate": 0.0,
        "repair_pressure_score": 0.0,
        "status": "active",
    }
    assert explicit_zero_gap == {
        "status": "closed",
        "legacy_sample_count": 0,
        "contract_sample_count": 0,
    }


def test_degraded_metric_evidence_omits_missing_metrics_but_keeps_zero() -> None:
    compact = JueWikiApplicationService._compact_validation_repair_degraded_metric_evidence(
        [
            {
                "discipline_id": "walk_forward_analysis",
                "repair_action_id": "run_walk_forward_replay",
                "status": "probe",
            },
            {
                "discipline_id": "monte_carlo_simulation",
                "repair_action_id": "run_monte_carlo_probe",
                "entry_bias": "depth_checked_probe",
                "sample_count": 0,
                "missed_count": 0,
                "resolved_count": 0,
                "resolution_rate": 0.0,
                "status": "repair_required",
            },
        ]
    )

    missing_metrics, explicit_zero = compact
    assert missing_metrics == {
        "discipline_id": "walk_forward_analysis",
        "repair_action_id": "run_walk_forward_replay",
        "status": "probe",
    }
    assert explicit_zero == {
        "discipline_id": "monte_carlo_simulation",
        "repair_action_id": "run_monte_carlo_probe",
        "entry_bias": "depth_checked_probe",
        "sample_count": 0,
        "missed_count": 0,
        "resolved_count": 0,
        "resolution_rate": 0.0,
        "status": "repair_required",
    }


def test_repair_priority_effectiveness_uses_resolution_targets(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-resolution-targets-{idx}",
            manager_run_id=f"kis-manager-repair-resolution-targets-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.005930"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_jue_wiki_repair_contract",
                "repair_priority_types": ["decision_adjustment_audit"],
                "repair_action_types": [
                    "repair_decision_adjustment_audit_contract"
                ],
                "repair_learning_resolution_targets": [
                    {
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 4,
                        "missed_count": 3,
                        "resolved_count": 1,
                        "resolution_rate": 0.25,
                    }
                ],
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness()

    assert repair_effectiveness["repair_learning_resolution_metrics"] == [
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
    assert repair_effectiveness["repair_learning_resolution_summary"][
        "primary_recommended_resolution"
    ] == "revise_repair_step_contract_then_probe"


def test_repair_priority_effectiveness_counts_action_metadata_resolution(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(3):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-action-metadata-resolution-{idx}",
            manager_run_id=f"kis-manager-repair-action-metadata-resolution-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.005930"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="resolved_repair_priority",
            outcome_status="flat",
            return_pct=0.0,
            evidence={
                "source": "kis_jue_wiki_repair_contract",
                "repair_priority_types": ["repair_queue"],
                "repair_action_types": ["refresh_symbol_financials"],
                "repair_source_ids": ["repair:valuation"],
                "repair_decision_uses": ["valuation_cross_check"],
                "resolution": {
                    "required": "jue_wiki_repair_action_metadata",
                    "resolved_candidates": [
                        {
                            "symbol": "005930",
                            "resolution": "action_metadata_resolution",
                            "next_trigger": (
                                "small waiting block until valuation refresh"
                            ),
                            "evidence_gap": "valuation page stale before entry",
                        }
                    ],
                },
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness()

    assert repair_effectiveness["repair_learning_resolution_metrics"] == [
        {
            "decision_scope": "kis",
            "recommended_resolution": "action_metadata_resolution",
            "sample_count": 3,
            "missed_count": 0,
            "resolved_count": 3,
            "resolution_rate": 1.0,
            "status": "active",
        }
    ]
    assert repair_effectiveness["repair_learning_resolution_summary"][
        "primary_recommended_resolution"
    ] == "action_metadata_resolution"


def test_repair_priority_effectiveness_splits_action_metadata_by_lane_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.write_page(
        scope="binance",
        page_type="symbol",
        key="NEARUSDT",
        title="NEARUSDT",
        symbols=["NEARUSDT"],
        content_sections={
            "Current Stance": "lane context repair fixture",
            "Next Context Pack Summary": "NEAR lane context repair fixture",
        },
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
    )
    samples = [
        ("resolved_repair_priority", "futures", "short", "intraday"),
        ("resolved_repair_priority", "futures", "short", "intraday"),
        ("missed_repair_priority", "spot", "long", "swing"),
        ("missed_repair_priority", "spot", "long", "swing"),
    ]
    for idx, (outcome_kind, market, side, horizon) in enumerate(samples):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-lane-context-{idx}",
            manager_run_id=f"binance-manager-repair-lane-context-{idx}",
            decision_scope="binance",
            decision_type="block_manager",
            selected_pages=["binance.symbol.NEARUSDT"],
            symbol="NEARUSDT",
            venue="binance",
            horizon=horizon,
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "binance_jue_wiki_repair_contract",
                "repair_priority_types": ["repair_queue"],
                "repair_action_types": ["refresh_crypto_microstructure"],
                "repair_source_ids": ["repair:near:microstructure"],
                "repair_decision_uses": ["microstructure_cross_check"],
                "resolution": {
                    "required": "jue_wiki_repair_action_metadata",
                    "resolved_candidates": [
                        {
                            "symbol": "NEARUSDT",
                            "market": market,
                            "side": side,
                            "horizon": horizon,
                            "resolution": "action_metadata_resolution",
                            "next_trigger": "lane-specific repair handling",
                            "evidence_gap": "stale microstructure wiki",
                        }
                    ],
                },
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness(
        decision_scope="binance",
        min_samples=2,
    )
    metrics = repair_effectiveness["repair_learning_resolution_metrics"]
    futures = next(
        row
        for row in metrics
        if row.get("market") == "futures" and row.get("side") == "short"
    )
    spot = next(
        row
        for row in metrics
        if row.get("market") == "spot" and row.get("side") == "long"
    )

    assert len(metrics) == 2
    assert futures["status"] == "active"
    assert futures["sample_count"] == 2
    assert futures["resolved_count"] == 2
    assert futures["horizon"] == "intraday"
    assert spot["status"] == "repair_required"
    assert spot["sample_count"] == 2
    assert spot["missed_count"] == 2
    assert spot["horizon"] == "swing"
    targets = repair_effectiveness["repair_learning_resolution_summary"][
        "resolution_targets"
    ]
    assert {
        (
            target.get("market"),
            target.get("side"),
            target.get("horizon"),
            target.get("status"),
        )
        for target in targets
    } == {
        ("futures", "short", "intraday", "active"),
        ("spot", "long", "swing", "repair_required"),
    }


def test_repair_priority_metrics_split_by_lane_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.write_page(
        scope="binance",
        page_type="symbol",
        key="NEARUSDT",
        title="NEARUSDT",
        symbols=["NEARUSDT"],
        content_sections={
            "Current Stance": "lane context repair fixture",
            "Next Context Pack Summary": "NEAR lane context repair fixture",
        },
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
    )
    samples = [
        ("resolved_repair_priority", "futures", "short", "intraday"),
        ("resolved_repair_priority", "futures", "short", "intraday"),
        ("missed_repair_priority", "spot", "long", "swing"),
        ("missed_repair_priority", "spot", "long", "swing"),
    ]
    for idx, (outcome_kind, market, side, horizon) in enumerate(samples):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-priority-lane-{idx}",
            manager_run_id=f"binance-manager-repair-priority-lane-{idx}",
            decision_scope="binance",
            decision_type="block_manager",
            selected_pages=["binance.symbol.NEARUSDT"],
            symbol="NEARUSDT",
            venue="binance",
            horizon=horizon,
            action="repair_queue",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "binance_jue_wiki_repair_contract",
                "market": market,
                "side": side,
                "horizon": horizon,
                "repair_priority_types": ["repair_queue"],
                "repair_action_types": ["refresh_crypto_microstructure"],
                "repair_source_ids": ["repair:near:microstructure"],
                "repair_decision_uses": ["microstructure_cross_check"],
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness(
        decision_scope="binance",
        min_samples=2,
    )
    metrics = repair_effectiveness["repair_priority_metrics"]
    futures = next(
        row
        for row in metrics
        if row.get("market") == "futures" and row.get("side") == "short"
    )
    spot = next(
        row
        for row in metrics
        if row.get("market") == "spot" and row.get("side") == "long"
    )

    assert len(metrics) == 2
    assert futures["status"] == "active"
    assert futures["sample_count"] == 2
    assert futures["resolved_count"] == 2
    assert futures["horizon"] == "intraday"
    assert spot["status"] == "repair_required"
    assert spot["sample_count"] == 2
    assert spot["missed_count"] == 2
    assert spot["horizon"] == "swing"
    assert repair_effectiveness["top_degraded"][0]["market"] == "spot"
    assert repair_effectiveness["top_degraded"][0]["side"] == "long"


def test_repair_priority_metrics_keep_market_side_for_plural_horizons(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:repair-priority-plural-horizons",
        manager_run_id="binance-manager-repair-priority-plural-horizons",
        decision_scope="binance",
        decision_type="block_manager",
        selected_pages=["binance.symbol.NEARUSDT"],
        symbol="NEARUSDT",
        venue="binance",
        action="repair_queue",
        prompt_mode="assist",
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="missed_repair_priority",
        outcome_status="loss",
        return_pct=-0.08,
        evidence={
            "source": "binance_selected_wiki_pages_repair_queue",
            "market": "binance",
            "side": "long",
            "repair_requested_horizons": ["mid", "long"],
            "repair_priority_types": ["horizon_effectiveness_fallback"],
            "repair_action_types": [
                "collect_horizon_specific_wiki_effectiveness"
            ],
            "repair_source_ids": [
                "binance.symbol.NEARUSDT:horizon_effectiveness_fallback"
            ],
            "repair_decision_uses": [
                "horizon_specific_effectiveness_repair"
            ],
        },
    )

    repair_effectiveness = service.project_repair_priority_effectiveness(
        decision_scope="binance",
        min_samples=1,
    )
    contexts = {
        (
            row.get("market"),
            row.get("side"),
            row.get("horizon"),
        )
        for row in repair_effectiveness["repair_priority_metrics"]
    }

    assert contexts == {
        ("binance", "long", "mid"),
        ("binance", "long", "long"),
    }


def test_repair_priority_effectiveness_uses_component_targets(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-component-targets-{idx}",
            manager_run_id=f"kis-manager-repair-component-targets-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.245450"],
            symbol="245450",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_jue_wiki_repair_contract",
                "repair_component_targets": [
                    {
                        "component": "repair_learning_resolution_metrics",
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "priority_type": "repair_queue",
                        "action_type": "refresh_symbol_financials",
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
                                    "refresh_symbol_financials"
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
                            "reasons": ["financial_refresh_failed"],
                        },
                    }
                ],
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness()

    assert repair_effectiveness["repair_component_target_metrics"] == [
        {
            "decision_scope": "kis",
            "component": "repair_learning_resolution_metrics",
            "target_status": "repair_required",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
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
            "repair_target_effectiveness": [
                {
                    "page_id": "kis.symbol.245450",
                    "status": "degraded",
                    "sample_count": 5,
                    "win_rate": 0.2,
                    "expectancy": -0.04,
                    "helpful_score": -0.1,
                    "confidence": 0.7,
                    "reasons": ["financial_refresh_failed"],
                }
            ],
            "repair_target_effectiveness_statuses": ["degraded"],
        }
    ]
    assert repair_effectiveness["repair_component_target_summary"][
        "primary_component_target"
    ] == "repair_learning_resolution_metrics"
    assert repair_effectiveness["repair_component_target_summary"][
        "top_component_target_details"
    ] == repair_effectiveness["repair_component_target_metrics"]
    assert repair_effectiveness["repair_component_target_summary"][
        "repair_required_component_target_details"
    ] == repair_effectiveness["repair_component_target_metrics"]
    assert repair_effectiveness["repair_component_target_summary"][
        "primary_repair_required_component_target_detail"
    ] == repair_effectiveness["repair_component_target_metrics"][0]
    assert repair_effectiveness["component_status_summary"][
        "repair_required_components"
    ] == ["repair_component_target_metrics"]


def test_repair_priority_status_reflects_resolution_target_only_failures(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-resolution-only-{idx}",
            manager_run_id=f"kis-manager-repair-resolution-only-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.005930"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_jue_wiki_repair_contract",
                "repair_learning_resolution_targets": [
                    {
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "sample_count": 4,
                        "missed_count": 3,
                        "resolved_count": 1,
                        "resolution_rate": 0.25,
                    }
                ],
            },
        )

    repair_effectiveness = service.project_repair_priority_effectiveness()

    assert repair_effectiveness["metric_count"] == 0
    assert repair_effectiveness["repair_required_count"] == 0
    assert repair_effectiveness["status"] == "repair_required"
    assert repair_effectiveness["repair_learning_resolution_summary"][
        "worst_status"
    ] == "repair_required"
    assert repair_effectiveness["component_status_summary"] == {
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
    }


def test_prompt_repair_effectiveness_reconstructs_steps_from_action_targets() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
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


def test_prompt_repair_effectiveness_reconstructs_resolution_from_step_targets() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
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


def test_prompt_repair_effectiveness_preserves_component_status_summary() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
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


def test_prompt_repair_effectiveness_reconstructs_component_status_summary() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
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
    }


def test_prompt_repair_effectiveness_reconstructs_component_targets_from_metrics() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
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
    resolution_component = next(
        row
        for row in component_summary["components"]
        if row.get("component") == "repair_learning_resolution_metrics"
    )
    assert resolution_component["component_targets"] == [
        {
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "sample_count": 5,
            "missed_count": 4,
            "resolved_count": 1,
            "resolution_rate": 0.2,
            "impacted_symbols": ["245450"],
        }
    ]
    assert component_summary["repair_required_component_targets"] == [
        {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
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
            "action_type": "probe_live_authority_gate",
            "criterion": "probe_live_authority_alignment",
            "sample_count": 6,
            "missed_count": 2,
            "resolved_count": 4,
            "resolution_rate": 2 / 3,
            "impacted_symbols": ["NEARUSDT"],
        }
    ]
    assert component_summary["top_component_targets"] == [
        {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "sample_count": 5,
            "missed_count": 4,
            "resolved_count": 1,
            "resolution_rate": 0.2,
            "impacted_symbols": ["245450"],
        },
        {
            "component": "repair_success_criteria_metrics",
            "decision_scope": "binance",
            "status": "probe",
            "priority_type": "probe_queue",
            "action_type": "probe_live_authority_gate",
            "criterion": "probe_live_authority_alignment",
            "sample_count": 6,
            "missed_count": 2,
            "resolved_count": 4,
            "resolution_rate": 2 / 3,
            "impacted_symbols": ["NEARUSDT"],
        },
        {
            "component": "repair_learning_directive_metrics",
            "decision_scope": "binance",
            "status": "active",
            "recommended_action": "collect_more_outcomes_before_policy_shift",
            "sample_count": 6,
            "missed_count": 2,
            "resolved_count": 4,
            "resolution_rate": 2 / 3,
        },
        {
            "component": "repair_learning_step_metrics",
            "decision_scope": "binance",
            "status": "active",
            "resolution_step": "inspect_failed_repair_directive_outcomes",
            "sample_count": 6,
            "missed_count": 2,
            "resolved_count": 4,
            "resolution_rate": 2 / 3,
        },
        {
            "component": "repair_learning_step_metrics",
            "decision_scope": "binance",
            "status": "active",
            "resolution_step": "record_next_outcome_before_reuse",
            "sample_count": 6,
            "missed_count": 2,
            "resolved_count": 4,
            "resolution_rate": 2 / 3,
        },
    ]


def test_prompt_repair_effectiveness_preserves_component_target_metrics() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
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

    assert summary["repair_component_target_metrics"] == [
        {
            "decision_scope": "kis",
            "component": "repair_learning_resolution_metrics",
            "target_status": "repair_required",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "status": "repair_required",
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
                "priority_type": "repair_queue",
                "action_type": "refresh_symbol_financials",
                "target_status": "repair_required",
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
                "priority_type": "repair_queue",
                "action_type": "refresh_symbol_financials",
                "target_status": "repair_required",
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
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "target_status": "repair_required",
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


def test_application_component_target_summary_omits_missing_metrics_but_keeps_zero() -> None:
    missing = (
        JueWikiApplicationService._repair_component_target_effectiveness_summary(
            [
                {
                    "decision_scope": "kis",
                    "component": "repair_learning_resolution_metrics",
                    "status": "probe",
                    "recommended_resolution": (
                        "revise_repair_step_contract_then_probe"
                    ),
                }
            ]
        )
    )

    for key in ("max_missed_count", "max_sample_count", "min_resolution_rate"):
        assert key not in missing
    target_detail = missing["top_component_target_details"][0]
    for key in (
        "sample_count",
        "missed_count",
        "resolved_count",
        "resolution_rate",
    ):
        assert key not in target_detail

    explicit_zero = (
        JueWikiApplicationService._repair_component_target_effectiveness_summary(
            [
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
            ]
        )
    )

    assert explicit_zero["max_missed_count"] == 0
    assert explicit_zero["max_sample_count"] == 0
    assert explicit_zero["min_resolution_rate"] == 0.0
    explicit_detail = explicit_zero["top_component_target_details"][0]
    assert explicit_detail["sample_count"] == 0
    assert explicit_detail["missed_count"] == 0
    assert explicit_detail["resolved_count"] == 0
    assert explicit_detail["resolution_rate"] == 0.0


def test_prompt_repair_effectiveness_splits_component_target_details_by_status() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
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


def test_prompt_repair_effectiveness_preserves_component_target_details() -> None:
    summary = JueWikiApplicationService._prompt_repair_loop_effectiveness_summary(
        {
            "component_status_summary": {
                "component_count": 2,
                "metric_count": 2,
                "repair_required_count": 1,
                "probe_count": 1,
                "active_count": 0,
                "unknown_count": 0,
                "worst_status": "repair_required",
                "repair_required_components": [
                    "repair_learning_resolution_metrics"
                ],
                "probe_components": ["repair_success_criteria_metrics"],
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
                                "priority_type": "repair_queue",
                                "priority_types": ["repair_queue"],
                                "action_type": "refresh_symbol_financials",
                                "sample_count": 5,
                                "missed_count": 4,
                                "resolved_count": 1,
                                "resolution_rate": 0.2,
                                "impacted_symbols": ["245450"],
                            }
                        ],
                    },
                    {
                        "component": "repair_success_criteria_metrics",
                        "metric_count": 1,
                        "repair_required_count": 0,
                        "probe_count": 1,
                        "active_count": 0,
                        "unknown_count": 0,
                        "worst_status": "probe",
                        "component_targets": [
                            {
                                "decision_scope": "binance",
                                "status": "probe",
                                "criterion": "probe_live_authority_alignment",
                                "priority_type": "probe_queue",
                                "priority_types": ["probe_queue"],
                                "action_type": "probe_live_authority_gate",
                                "sample_count": 6,
                                "missed_count": 2,
                                "resolved_count": 4,
                                "resolution_rate": 2 / 3,
                                "impacted_symbols": ["NEARUSDT"],
                            }
                        ],
                    },
                ],
                "repair_required_component_targets": [
                    {
                        "component": "repair_learning_resolution_metrics",
                        "decision_scope": "kis",
                        "status": "repair_required",
                        "recommended_resolution": (
                            "revise_repair_step_contract_then_probe"
                        ),
                        "priority_type": "repair_queue",
                        "priority_types": ["repair_queue"],
                        "action_type": "refresh_symbol_financials",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "impacted_symbols": ["245450"],
                    }
                ],
                "probe_component_targets": [
                    {
                        "component": "repair_success_criteria_metrics",
                        "decision_scope": "binance",
                        "status": "probe",
                        "criterion": "probe_live_authority_alignment",
                        "priority_type": "probe_queue",
                        "priority_types": ["probe_queue"],
                        "action_type": "probe_live_authority_gate",
                        "sample_count": 6,
                        "missed_count": 2,
                        "resolved_count": 4,
                        "resolution_rate": 2 / 3,
                        "impacted_symbols": ["NEARUSDT"],
                    }
                ],
            }
        }
    )

    component_summary = summary["component_status_summary"]
    assert component_summary["components"][0]["component_targets"] == [
        {
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
            "criterion": "probe_live_authority_alignment",
            "priority_type": "probe_queue",
            "priority_types": ["probe_queue"],
            "action_type": "probe_live_authority_gate",
            "sample_count": 6,
            "missed_count": 2,
            "resolved_count": 4,
            "resolution_rate": 2 / 3,
            "impacted_symbols": ["NEARUSDT"],
        }
    ]


def test_repair_loop_status_metrics_preserve_matching_loop_counts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, outcome_kind in enumerate(
        [
            "missed_repair_priority",
            "missed_repair_priority",
            "resolved_repair_priority",
        ]
    ):
        link = service.record_decision_link(
            selection_run_id=f"selection:repair-loop-counts-{idx}",
            manager_run_id=f"kis-manager-repair-loop-counts-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.symbol.005930"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind=outcome_kind,
            outcome_status="flat" if outcome_kind.startswith("resolved") else "loss",
            return_pct=0.0 if outcome_kind.startswith("resolved") else -0.08,
            evidence={
                "source": "kis_jue_wiki_repair_contract",
                "repair_priority_types": [
                    "evidence_quality",
                    "decision_adjustment_audit",
                ],
                "repair_action_types": [
                    "refresh_symbol_financials",
                    "repair_decision_adjustment_audit_contract",
                ],
                "repair_source_ids": [
                    "005930:financials",
                    "kis:supporting_evidence:audit_repair_probe",
                ],
                "repair_decision_uses": [
                    "evidence_quality_cross_check",
                    "decision_adjustment_audit_repair",
                ],
                "repair_loop_statuses": ["probe", "repair_required"],
                "repair_loop_action_types": [
                    "refresh_symbol_financials",
                    "repair_decision_adjustment_audit_contract",
                ],
                "repair_loop_sample_counts": [1, 7],
                "repair_loop_missed_counts": [0, 5],
                "repair_loop_resolved_counts": [1, 2],
                "repair_loop_resolution_rates": [1.0, 2 / 7],
            },
        )

    effectiveness = service.project_repair_priority_effectiveness(
        decision_scope="kis",
    )
    metrics_by_action = {
        row["action_type"]: row
        for row in effectiveness["repair_loop_status_metrics"]
    }

    assert metrics_by_action["refresh_symbol_financials"][
        "repair_loop_status"
    ] == "probe"
    assert metrics_by_action["refresh_symbol_financials"]["loop_sample_count"] == 1
    assert metrics_by_action["refresh_symbol_financials"]["loop_missed_count"] == 0
    assert metrics_by_action["refresh_symbol_financials"]["loop_resolved_count"] == 1
    assert metrics_by_action["refresh_symbol_financials"][
        "loop_resolution_rate"
    ] == 1.0
    assert metrics_by_action["repair_decision_adjustment_audit_contract"][
        "repair_loop_status"
    ] == "repair_required"
    assert (
        metrics_by_action["repair_decision_adjustment_audit_contract"][
            "loop_sample_count"
        ]
        == 7
    )
    assert (
        metrics_by_action["repair_decision_adjustment_audit_contract"][
            "loop_missed_count"
        ]
        == 5
    )
    assert (
        metrics_by_action["repair_decision_adjustment_audit_contract"][
            "loop_resolved_count"
        ]
        == 2
    )
    assert (
        metrics_by_action["repair_decision_adjustment_audit_contract"][
            "loop_resolution_rate"
        ]
        == 2 / 7
    )


def test_record_selection_outcomes_preserves_selected_wiki_page_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:quality-outcome",
        manager_run_id="kis-manager-1",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.245450"],
        symbol="245450",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.245450",
                        "page_type": "symbol",
                        "confidence": 0.72,
                        "symbols": ["245450"],
                        "quality_status": "partial",
                        "quality_warnings": [
                            "valuation_metrics_sparse",
                            "financials_missing",
                        ],
                        "repair_queue": {
                            "status": "scheduled",
                            "action_type": "refresh_symbol_financials",
                            "symbols": ["245450"],
                        },
                    }
                ]
            }
        },
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        pnl_value=-1200,
        pnl_currency="KRW",
        return_pct=-0.8,
        mae_pct=-1.2,
        evidence={"exit_reason": "stop"},
    )

    rows = service.list_selection_outcomes(
        selection_run_id="selection:quality-outcome"
    )

    assert rows[0]["evidence"]["exit_reason"] == "stop"
    assert rows[0]["evidence"]["selected_wiki_page"] == {
        "page_id": "kis.symbol.245450",
        "page_type": "symbol",
        "confidence": 0.72,
        "symbols": ["245450"],
        "quality_status": "partial",
        "quality_warnings": ["valuation_metrics_sparse", "financials_missing"],
        "repair_queue": {
            "status": "scheduled",
            "action_type": "refresh_symbol_financials",
            "symbols": ["245450"],
        },
    }


def test_record_selection_outcomes_attaches_quality_pressure_provenance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:quality-pressure-record",
        manager_run_id="kis-manager-quality-pressure-record",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    }
                ]
            },
            "jue_wiki_quality_summary": {
                "row_count": 1,
                "status_counts": {"weak": 1},
                "top_warnings": [{"warning": "price_missing", "count": 1}],
                "warning_page_ids": {
                    "price_missing": [
                        "kis.symbol.005930",
                        "kis.symbol.277810",
                    ]
                },
            },
            "jue_wiki_quality_pressure_action_plan": {
                "status": "repair_required",
                "hard_blocker": False,
                "repair_focus": [
                    {
                        "priority_type": "evidence_quality",
                        "warning": "price_missing",
                        "count": 1,
                        "page_ids": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    }
                ],
                "caution_page_ids": ["kis.symbol.005930"],
            },
        },
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        pnl_value=-1200,
        pnl_currency="KRW",
        return_pct=-0.8,
        mae_pct=-1.2,
        evidence={"exit_reason": "stop"},
    )

    rows = service.list_selection_outcomes(
        selection_run_id="selection:quality-pressure-record"
    )

    assert rows[0]["evidence"]["exit_reason"] == "stop"
    assert rows[0]["evidence"]["warning_page_ids"] == {
        "price_missing": ["kis.symbol.005930"]
    }
    assert rows[0]["evidence"]["repair_focus_page_ids"] == ["kis.symbol.005930"]
    assert rows[0]["evidence"]["quality_warnings"] == ["price_missing"]
    assert rows[0]["evidence"]["caution_page_ids"] == ["kis.symbol.005930"]

    service.project_page_effectiveness(min_samples=1)
    source_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert source_metric["status"] == "degraded"
    assert "quality_warning_source_page" in source_metric["reasons"]
    assert unrelated_metric["status"] == "missing"


def test_record_selection_outcomes_attaches_application_repair_queue_provenance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:application-repair-queue-record",
        manager_run_id="kis-manager-application-repair-queue-record",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    }
                ]
            },
            "jue_wiki_application": {
                "repair_queue": {
                    "open_count": 2,
                    "resolved_count": 1,
                    "open_symbols": ["005930", "000660"],
                    "open_action_batches": [
                        {
                            "scope": "kis",
                            "action_type": "refresh_symbol_financials",
                            "count": 2,
                            "symbols": ["005930", "000660"],
                            "warnings": ["valuation_missing"],
                            "raw_debug": "DROP_QUEUE",
                        }
                    ],
                    "raw_debug": "DROP_QUEUE",
                }
            },
        },
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        pnl_value=-1200,
        pnl_currency="KRW",
        return_pct=-0.8,
        mae_pct=-1.2,
        evidence={"exit_reason": "stop"},
    )

    rows = service.list_selection_outcomes(
        selection_run_id="selection:application-repair-queue-record"
    )

    assert rows[0]["evidence"]["jue_wiki_repair_queue"] == {
        "open_count": 2,
        "resolved_count": 1,
        "open_symbols": ["005930", "000660"],
        "open_action_batches": [
            {
                "scope": "kis",
                "action_type": "refresh_symbol_financials",
                "count": 2,
                "symbols": ["005930", "000660"],
                "warnings": ["valuation_missing"],
            }
        ],
    }
    assert "DROP_QUEUE" not in json.dumps(rows[0]["evidence"], ensure_ascii=False)

    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert "application_repair_queue_pressure" in metric["reasons"]
    assert "repair_queue_open_count:2" in metric["reasons"]
    assert "repair_queue_action:refresh_symbol_financials" in metric["reasons"]


def test_record_selection_outcomes_attaches_quality_warning_source_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.277810",
        "page_type": "symbol",
        "symbols": ["277810"],
        "quality_warning_source_effectiveness": {
            "status": "degraded",
            "metrics": [
                {
                    "page_id": "kis.symbol.277810",
                    "status": "degraded",
                    "sample_count": 3,
                    "win_rate": 0.0,
                    "expectancy": -1.4,
                    "helpful_score": -9.5,
                    "confidence": 0.82,
                    "reasons": [
                        "quality_warning_source_page",
                        "quality_warning:price_missing",
                    ],
                }
            ],
            "decision_use": (
                "this selected page previously contributed to unresolved quality "
                "pressure; require warning-specific repair or live cross-checks "
                "before using it as a block thesis"
            ),
        },
    }
    link = service.record_decision_link(
        selection_run_id="selection:quality-source-effectiveness",
        manager_run_id="manager-quality-source-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.277810"],
        symbol="277810",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        pnl_value=-1400,
        pnl_currency="KRW",
        return_pct=-1.4,
        mae_pct=-2.1,
        evidence={"exit_reason": "stop"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:quality-source-effectiveness"
    )

    assert outcomes[0]["evidence"]["quality_warning_source_effectiveness"] == {
        "status": "degraded",
        "metrics": selected_page["quality_warning_source_effectiveness"]["metrics"],
        "decision_use": selected_page["quality_warning_source_effectiveness"][
            "decision_use"
        ],
    }
    assert outcomes[0]["evidence"][
        "quality_warning_source_effectiveness_statuses"
    ] == ["degraded"]

    service.project_page_effectiveness(min_samples=1)
    source_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert source_metric["status"] == "degraded"
    assert "quality_warning_source_page" in source_metric["reasons"]
    assert (
        "quality_warning_source_prior_status:degraded" in source_metric["reasons"]
    )


def test_record_selection_outcomes_filters_quality_warning_effectiveness_to_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_warning_effectiveness = {
        "warning": "valuation_stale",
        "page_id": "kis.symbol.005930",
        "status": "active",
        "sample_count": 8,
        "helpful_score": 5.5,
        "reasons": [
            "quality_warning:valuation_stale",
            "quality_warning_prior_status:active",
        ],
    }
    unrelated_warning_effectiveness = {
        "warning": "price_missing",
        "page_id": "kis.symbol.277810",
        "status": "degraded",
        "sample_count": 3,
        "helpful_score": -6.0,
        "reasons": [
            "quality_warning:price_missing",
            "quality_warning_prior_status:degraded",
        ],
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "evidence_quality": {
            "warning_effectiveness": [
                selected_warning_effectiveness,
                unrelated_warning_effectiveness,
            ]
        },
    }
    link = service.record_decision_link(
        selection_run_id="selection:quality-warning-effectiveness-filter",
        manager_run_id="manager-quality-warning-effectiveness-filter",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=1.2,
        mae_pct=-0.4,
        evidence={
            "exit_reason": "target",
            "quality_warnings": ["valuation_stale"],
        },
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:quality-warning-effectiveness-filter"
    )

    assert outcomes[0]["evidence"]["quality_warning_effectiveness"] == [
        selected_warning_effectiveness
    ]
    assert outcomes[0]["evidence"]["quality_warning_effectiveness_statuses"] == [
        "active"
    ]

    service.project_page_effectiveness(min_samples=1)
    selected_warning_metric = service.page_effectiveness(
        page_id="quality_warning.valuation_stale",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    unrelated_warning_metric = service.page_effectiveness(
        page_id="quality_warning.price_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert selected_warning_metric["status"] == "active"
    assert unrelated_warning_metric["status"] == "missing"


def test_record_selection_outcomes_filters_quality_warning_source_effectiveness_to_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_metric = {
        "page_id": "kis.symbol.005930",
        "status": "active",
        "sample_count": 9,
        "win_rate": 0.67,
        "expectancy": 1.2,
        "helpful_score": 6.5,
        "confidence": 0.9,
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:valuation_stale",
        ],
    }
    unrelated_metric = {
        "page_id": "kis.symbol.277810",
        "status": "degraded",
        "sample_count": 3,
        "win_rate": 0.0,
        "expectancy": -1.4,
        "helpful_score": -9.5,
        "confidence": 0.82,
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:price_missing",
        ],
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "quality_warning_source_effectiveness": {
            "status": "degraded",
            "metrics": [selected_metric, unrelated_metric],
            "decision_use": "prior source page performance should be page-specific",
        },
    }
    link = service.record_decision_link(
        selection_run_id="selection:quality-source-effectiveness-filter",
        manager_run_id="manager-quality-source-effectiveness-filter",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=1.2,
        mae_pct=-0.4,
        evidence={"exit_reason": "target"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:quality-source-effectiveness-filter"
    )

    assert outcomes[0]["evidence"]["quality_warning_source_effectiveness"] == {
        "status": "active",
        "metrics": [selected_metric],
        "decision_use": selected_page["quality_warning_source_effectiveness"][
            "decision_use"
        ],
    }
    assert outcomes[0]["evidence"][
        "quality_warning_source_effectiveness_statuses"
    ] == ["active"]

    service.project_page_effectiveness(min_samples=1)
    selected_source_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    unrelated_source_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert selected_source_metric["status"] == "active"
    assert "quality_warning_source_prior_status:active" in selected_source_metric[
        "reasons"
    ]
    assert unrelated_source_metric["status"] == "missing"


def test_record_selection_outcomes_attaches_quality_warning_source_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_summary = {
        "source_count": 1,
        "degraded_count": 1,
        "top_degraded_sources": [
            {
                "page_id": "kis.symbol.277810",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 4,
                "win_rate": 0.0,
                "expectancy": -1.4,
                "helpful_score": -9.5,
                "confidence": 0.82,
                "quality_warnings": ["price_missing"],
                "prior_statuses": ["degraded"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:price_missing",
                    "quality_warning_source_prior_status:degraded",
                ],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:source-summary-only",
        manager_run_id="manager-source-summary-only",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.277810"],
        symbol="277810",
        venue="kis",
        horizon="mid",
        metadata={"jue_wiki_quality_warning_source_summary": source_summary},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        pnl_value=-900,
        pnl_currency="KRW",
        return_pct=-0.9,
        mae_pct=-1.5,
        evidence={"exit_reason": "stop"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:source-summary-only"
    )

    assert outcomes[0]["evidence"]["quality_warning_source_summary"] == source_summary
    assert outcomes[0]["evidence"]["quality_warnings"] == ["price_missing"]

    service.project_page_effectiveness(min_samples=1)
    source_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert source_metric["status"] == "degraded"
    assert "quality_warning_source_page" in source_metric["reasons"]
    assert (
        "quality_warning_source_prior_status:degraded" in source_metric["reasons"]
    )


def test_record_selection_outcomes_projects_active_quality_warning_source_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_summary = {
        "source_count": 1,
        "active_count": 1,
        "top_active_sources": [
            {
                "page_id": "kis.symbol.005930",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 9,
                "win_rate": 0.67,
                "expectancy": 1.2,
                "helpful_score": 6.5,
                "confidence": 0.9,
                "quality_warnings": ["valuation_stale"],
                "prior_statuses": ["active"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:valuation_stale",
                    "quality_warning_source_prior_status:active",
                ],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:active-source-summary",
        manager_run_id="manager-active-source-summary",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"jue_wiki_quality_warning_source_summary": source_summary},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=1.2,
        mae_pct=-0.4,
        evidence={"exit_reason": "target"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:active-source-summary"
    )

    assert outcomes[0]["evidence"]["quality_warning_source_summary"] == source_summary
    assert outcomes[0]["evidence"]["quality_warnings"] == ["valuation_stale"]
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "valuation_stale": ["kis.symbol.005930"]
    }

    service.project_page_effectiveness(min_samples=1)
    source_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert source_metric["status"] == "active"
    assert "quality_warning_source_page" in source_metric["reasons"]
    assert "quality_warning:valuation_stale" in source_metric["reasons"]
    assert (
        "quality_warning_source_prior_status:active" in source_metric["reasons"]
    )


def test_record_selection_outcomes_filters_source_summary_to_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_source = {
        "page_id": "kis.symbol.005930",
        "decision_scope": "kis",
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 9,
        "win_rate": 0.67,
        "expectancy": 1.2,
        "helpful_score": 6.5,
        "confidence": 0.9,
        "quality_warnings": ["valuation_stale"],
        "prior_statuses": ["active"],
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:valuation_stale",
            "quality_warning_source_prior_status:active",
        ],
    }
    unrelated_degraded_source = {
        "page_id": "kis.symbol.277810",
        "decision_scope": "kis",
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 4,
        "win_rate": 0.0,
        "expectancy": -1.4,
        "helpful_score": -9.5,
        "confidence": 0.82,
        "quality_warnings": ["price_missing"],
        "prior_statuses": ["degraded"],
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:price_missing",
            "quality_warning_source_prior_status:degraded",
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:source-summary-selected-page-filter",
        manager_run_id="manager-source-summary-selected-page-filter",
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
            },
            "jue_wiki_quality_warning_source_summary": {
                "source_count": 2,
                "active_count": 1,
                "degraded_count": 1,
                "top_active_sources": [active_source],
                "top_degraded_sources": [unrelated_degraded_source],
            },
        },
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=1.2,
        mae_pct=-0.4,
        evidence={"exit_reason": "target"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:source-summary-selected-page-filter"
    )

    assert outcomes[0]["evidence"]["quality_warning_source_summary"] == {
        "source_count": 1,
        "active_count": 1,
        "top_active_sources": [active_source],
    }
    assert outcomes[0]["evidence"]["quality_warnings"] == ["valuation_stale"]
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "valuation_stale": ["kis.symbol.005930"]
    }

    service.project_page_effectiveness(min_samples=1)
    active_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert active_metric["status"] == "active"
    assert "quality_warning_source_page" in active_metric["reasons"]
    assert "quality_warning:valuation_stale" in active_metric["reasons"]
    assert (
        "quality_warning_source_prior_status:active" in active_metric["reasons"]
    )
    assert unrelated_metric["status"] == "missing"


def test_record_selection_outcomes_drops_source_summary_without_selected_page_match(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    unrelated_source = {
        "page_id": "kis.symbol.277810",
        "decision_scope": "kis",
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 4,
        "win_rate": 0.0,
        "expectancy": -1.4,
        "helpful_score": -9.5,
        "confidence": 0.82,
        "quality_warnings": ["price_missing"],
        "prior_statuses": ["degraded"],
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:price_missing",
            "quality_warning_source_prior_status:degraded",
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:source-summary-no-selected-page-match",
        manager_run_id="manager-source-summary-no-selected-page-match",
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
            },
            "jue_wiki_quality_warning_source_summary": {
                "source_count": 1,
                "degraded_count": 1,
                "top_degraded_sources": [unrelated_source],
            },
        },
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=1.2,
        mae_pct=-0.4,
        evidence={"exit_reason": "target"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:source-summary-no-selected-page-match"
    )

    assert "quality_warning_source_summary" not in outcomes[0]["evidence"]
    assert "quality_warnings" not in outcomes[0]["evidence"]
    assert "warning_page_ids" not in outcomes[0]["evidence"]

    service.project_page_effectiveness(min_samples=1)
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert unrelated_metric["status"] == "missing"


def test_record_selection_outcomes_ignores_source_summary_rows_without_page_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_summary = {
        "source_count": 1,
        "degraded_count": 1,
        "top_degraded_sources": [
            {
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 3,
                "win_rate": 0.0,
                "expectancy": -1.0,
                "helpful_score": -5.0,
                "confidence": 0.6,
                "quality_warnings": ["price_missing"],
                "prior_statuses": ["degraded"],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:source-summary-missing-page-id",
        manager_run_id="manager-source-summary-missing-page-id",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.playbook.evidence_quality"],
        symbol="",
        venue="kis",
        horizon="mid",
        metadata={"jue_wiki_quality_warning_source_summary": source_summary},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        pnl_value=-900,
        pnl_currency="KRW",
        return_pct=-0.9,
        mae_pct=-1.5,
        evidence={"exit_reason": "stop"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:source-summary-missing-page-id"
    )

    assert outcomes[0]["evidence"]["quality_warnings"] == ["price_missing"]
    assert "warning_page_ids" not in outcomes[0]["evidence"]

    service.project_page_effectiveness(min_samples=1)
    none_metric = service.page_effectiveness(
        page_id="None",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert none_metric["status"] == "missing"


def test_record_selection_outcomes_filters_quality_pressure_pages_to_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:quality-pressure-selected-page-filter",
        manager_run_id="manager-quality-pressure-selected-page-filter",
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
            },
            "jue_wiki_quality_summary": {
                "row_count": 2,
                "status_counts": {"weak": 2},
                "top_warnings": [{"warning": "price_missing", "count": 2}],
                "warning_page_ids": {
                    "price_missing": [
                        "kis.symbol.005930",
                        "kis.symbol.277810",
                    ],
                },
            },
            "jue_wiki_quality_pressure_action_plan": {
                "status": "repair_required",
                "hard_blocker": False,
                "repair_focus": [
                    {
                        "priority_type": "evidence_quality",
                        "warning": "price_missing",
                        "count": 2,
                        "page_ids": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    }
                ],
                "required_adjustments": [
                    {
                        "adjustment_type": "quality_warning_resolution",
                        "warning": "price_missing",
                        "count": 2,
                        "page_ids": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    }
                ],
                "caution_page_ids": [
                    "kis.symbol.005930",
                    "kis.symbol.277810",
                ],
            },
        },
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        pnl_value=-900,
        pnl_currency="KRW",
        return_pct=-0.9,
        mae_pct=-1.5,
        evidence={"exit_reason": "stop"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:quality-pressure-selected-page-filter"
    )

    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "price_missing": ["kis.symbol.005930"]
    }
    assert outcomes[0]["evidence"]["repair_focus_page_ids"] == [
        "kis.symbol.005930"
    ]
    assert outcomes[0]["evidence"]["caution_page_ids"] == ["kis.symbol.005930"]

    service.project_page_effectiveness(min_samples=1)
    selected_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert selected_metric["status"] == "degraded"
    assert "quality_warning_source_page" in selected_metric["reasons"]
    assert unrelated_metric["status"] == "missing"


def test_record_selection_outcomes_drops_unmatched_quality_top_warnings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:quality-top-warning-selected-page-filter",
        manager_run_id="manager-quality-top-warning-selected-page-filter",
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
            },
            "jue_wiki_quality_summary": {
                "row_count": 1,
                "status_counts": {"weak": 1},
                "top_warnings": [{"warning": "price_missing", "count": 1}],
                "warning_page_ids": {
                    "price_missing": ["kis.symbol.277810"],
                },
            },
        },
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=1.2,
        mae_pct=-0.4,
        evidence={"exit_reason": "target"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:quality-top-warning-selected-page-filter"
    )

    assert "quality_warnings" not in outcomes[0]["evidence"]
    assert "warning_page_ids" not in outcomes[0]["evidence"]

    service.project_page_effectiveness(min_samples=1)
    warning_metric = service.page_effectiveness(
        page_id="quality_warning.price_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert warning_metric["status"] == "missing"
    assert unrelated_metric["status"] == "missing"


def test_record_selection_outcomes_projects_source_row_status_without_prior_statuses(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_summary = {
        "source_count": 1,
        "active_count": 1,
        "top_active_sources": [
            {
                "page_id": "kis.symbol.005930",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "status": "active",
                "sample_count": 9,
                "win_rate": 0.67,
                "expectancy": 1.2,
                "helpful_score": 6.5,
                "confidence": 0.9,
                "quality_warnings": ["valuation_stale"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:valuation_stale",
                ],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:active-source-row-status",
        manager_run_id="manager-active-source-row-status",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"jue_wiki_quality_warning_source_summary": source_summary},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=1.2,
        mae_pct=-0.4,
        evidence={"exit_reason": "target"},
    )
    service.project_page_effectiveness(min_samples=1)

    source_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert source_metric["status"] == "active"
    assert (
        "quality_warning_source_prior_status:active" in source_metric["reasons"]
    )


def test_record_selection_outcomes_filters_unrelated_symbol_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:symbol-filter",
        manager_run_id="kis-manager-filter",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=[
            "kis.symbol.005930",
            "kis.symbol.000660",
            "kis.playbook.reflection_lessons",
        ],
        symbol="005930",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                    },
                    {
                        "page_id": "kis.symbol.000660",
                        "page_type": "symbol",
                        "symbols": ["000660"],
                    },
                    {
                        "page_id": "kis.playbook.reflection_lessons",
                        "page_type": "playbook",
                    },
                ]
            }
        },
    )

    result = service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        return_pct=1.2,
        evidence={"block_id": "blk-005930"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:symbol-filter",
        limit=10,
    )

    assert result["outcome_count"] == 2
    assert {row["page_id"] for row in outcomes} == {
        "kis.symbol.005930",
        "kis.playbook.reflection_lessons",
    }


def test_record_selection_outcomes_filters_symbol_pages_from_evidence_symbol(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:evidence-symbol-filter",
        manager_run_id="binance-manager-filter",
        decision_scope="binance",
        decision_type="block_manager",
        selected_pages=[
            "binance.symbol.BTCUSDT",
            "binance.symbol.ETHUSDT",
            "binance.playbook.live.binance.edge",
        ],
        venue="binance",
        horizon="short",
        action="manager_run",
        prompt_mode="assist",
    )

    result = service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        return_pct=-0.7,
        evidence={"symbol": "ETHUSDT", "block_id": "binance-eth-block"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:evidence-symbol-filter",
        limit=10,
    )

    assert result["outcome_count"] == 2
    assert {row["page_id"] for row in outcomes} == {
        "binance.symbol.ETHUSDT",
        "binance.playbook.live.binance.edge",
    }


def test_record_selection_outcomes_stores_normalized_outcome_symbol(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:normalized-outcome-symbol",
        manager_run_id="kis-manager-normalized-symbol",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=[
            "kis.symbol.178920",
            "kis.symbol.005930",
            "kis.playbook.live.kis.edge",
        ],
        symbol="정보",
        venue="kis",
        horizon="mid",
        action="create_block",
        prompt_mode="assist",
    )

    result = service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        return_pct=1.4,
        evidence={"symbol": "178920", "block_id": "blk-178920"},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:normalized-outcome-symbol",
        limit=10,
    )

    assert result["outcome_count"] == 2
    assert {row["page_id"] for row in outcomes} == {
        "kis.symbol.178920",
        "kis.playbook.live.kis.edge",
    }
    assert {row["symbol"] for row in outcomes} == {"178920"}


def test_record_selection_outcomes_does_not_treat_market_lane_as_symbol(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:market-lane-not-symbol",
        manager_run_id="binance-manager-lane",
        decision_scope="binance",
        decision_type="block_manager",
        selected_pages=[
            "binance.symbol.BTCUSDT",
            "binance.symbol.ETHUSDT",
            "binance.playbook.live.binance.edge",
        ],
        venue="binance",
        horizon="intraday",
        action="manager_run",
        prompt_mode="assist",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "binance.symbol.BTCUSDT",
                        "page_type": "symbol",
                        "symbols": ["BTCUSDT"],
                    },
                    {
                        "page_id": "binance.symbol.ETHUSDT",
                        "page_type": "symbol",
                        "symbols": ["ETHUSDT"],
                    },
                    {
                        "page_id": "binance.playbook.live.binance.edge",
                        "page_type": "playbook",
                    },
                ]
            }
        },
    )

    result = service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        return_pct=-0.7,
        evidence={
            "market": "futures",
            "side": "short",
            "block_id": "manager-lane-outcome",
        },
    )
    service.project_page_effectiveness(min_samples=1)
    btc_metric = service.page_effectiveness(
        page_id="binance.symbol.BTCUSDT",
        decision_scope="binance",
        venue="binance",
        horizon="intraday",
    )

    assert result["outcome_count"] == 3
    assert btc_metric["sample_count"] == 1
    assert btc_metric["status"] == "degraded"


def test_record_selection_outcomes_requires_existing_link(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.record_selection_outcomes(
        link_id="missing",
        outcome_kind="closed_block",
        outcome_status="loss",
    )

    assert result["status"] == "error"
    assert "not found" in result["error_message"]


def test_record_selection_outcomes_is_idempotent_for_same_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:idempotent",
        manager_run_id="manager:one",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
    )

    for _ in range(2):
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="win",
            return_pct=1.0,
            evidence={"block_id": "blk-1"},
        )

    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:idempotent"
    )

    assert len(outcomes) == 1


def test_project_page_effectiveness_ignores_non_final_outcomes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:pending",
        manager_run_id="manager:one",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        venue="kis",
        horizon="short_term",
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="open_block",
        outcome_status="pending",
        return_pct=0.0,
    )

    result = service.project_page_effectiveness(min_samples=1)

    assert result == {"status": "ok", "updated_count": 0}


def test_project_page_effectiveness_ignores_legacy_misattributed_symbol_outcome(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:legacy-misattributed",
        manager_run_id="kis-manager-legacy-misattributed",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-misattributed-outcome",
                link["link_id"],
                "selection:legacy-misattributed",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                3.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    result = service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["updated_count"] == 0
    assert result["attribution_filtered_count"] == 1
    assert metric["status"] == "missing"


def test_project_page_effectiveness_removes_legacy_misattributed_symbol_metric(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 1,
            "win_rate": 1.0,
            "expectancy": 3.0,
            "avg_return_pct": 3.0,
            "helpful_score": 5.0,
            "confidence": 0.8,
            "status": "active",
            "reasons": ["legacy polluted"],
        }
    )
    link = service.record_decision_link(
        selection_run_id="selection:legacy-metric-misattributed",
        manager_run_id="kis-manager-legacy-metric",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-misattributed-metric-outcome",
                link["link_id"],
                "selection:legacy-metric-misattributed",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                3.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    result = service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["updated_count"] == 0
    assert result["attribution_filtered_count"] == 1
    assert result["stale_effectiveness_removed_count"] == 1
    assert metric["status"] == "missing"


def test_page_effectiveness_prunes_stale_misattributed_metric_on_read(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
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
    link = service.record_decision_link(
        selection_run_id="selection:direct-page-effectiveness-stale",
        manager_run_id="kis-manager-direct-page-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "direct-page-effectiveness-stale-outcome",
                link["link_id"],
                "selection:direct-page-effectiveness-stale",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "missing"


def test_list_page_effectiveness_prunes_stale_misattributed_metric_on_read(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["legacy polluted"],
        }
    )
    link = service.record_decision_link(
        selection_run_id="selection:list-page-effectiveness-stale",
        manager_run_id="kis-manager-list-page-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "list-page-effectiveness-stale-outcome",
                link["link_id"],
                "selection:list-page-effectiveness-stale",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    metrics = service.list_page_effectiveness(decision_scope="kis")

    assert metrics == []


def test_project_page_effectiveness_ignores_quality_warning_from_misattributed_symbol(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:legacy-warning-misattributed",
        manager_run_id="kis-manager-legacy-warning",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                        "quality_warnings": ["financials_missing"],
                    }
                ]
            }
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-warning-misattributed-outcome",
                link["link_id"],
                "selection:legacy-warning-misattributed",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "loss",
                -2.0,
                json.dumps(
                    {
                        "symbol": "000660",
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                            "quality_warnings": ["financials_missing"],
                        },
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    result = service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["updated_count"] == 0
    assert result["attribution_filtered_count"] == 1
    assert "quality_warning_updated_count" not in result
    assert metric["status"] == "missing"


def test_project_page_effectiveness_removes_legacy_misattributed_warning_metric(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 1,
            "win_rate": 0.0,
            "expectancy": -2.0,
            "avg_return_pct": -2.0,
            "helpful_score": -5.0,
            "confidence": 0.8,
            "status": "degraded",
            "reasons": ["legacy polluted warning"],
        }
    )
    link = service.record_decision_link(
        selection_run_id="selection:legacy-warning-metric",
        manager_run_id="kis-manager-legacy-warning-metric",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                        "quality_warnings": ["financials_missing"],
                    }
                ]
            }
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-warning-metric-outcome",
                link["link_id"],
                "selection:legacy-warning-metric",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "loss",
                -2.0,
                json.dumps(
                    {
                        "symbol": "000660",
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                            "quality_warnings": ["financials_missing"],
                        },
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    result = service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["updated_count"] == 0
    assert result["attribution_filtered_count"] == 1
    assert result["stale_effectiveness_removed_count"] == 1
    assert metric["status"] == "missing"


def test_project_page_effectiveness_tracks_quality_warning_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        link = service.record_decision_link(
            selection_run_id=f"selection:warning-{idx}",
            manager_run_id=f"kis-manager-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.symbol.24545{idx}"],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={
                "selected_wiki_pages": {
                    "pages": [
                        {
                            "page_id": f"kis.symbol.24545{idx}",
                            "page_type": "symbol",
                            "quality_warnings": [
                                "financials_missing",
                                "valuation_metrics_sparse",
                            ],
                        }
                    ]
                }
            },
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
            evidence={
                "quality_warning_effectiveness_statuses": ["degraded"],
            },
        )

    result = service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["quality_warning_updated_count"] == 2
    assert metric["sample_count"] == 2
    assert metric["status"] == "degraded"
    assert metric["win_rate"] == 0.0
    assert metric["expectancy"] == -1.0
    assert "quality_warning:financials_missing" in metric["reasons"]
    assert "quality_warning_prior_status:degraded" in metric["reasons"]


def test_project_page_effectiveness_tracks_usage_guidance_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    usage_guidance = {
        "trust_level": "low",
        "risk_posture": "repair_cross_check",
        "allowed_uses": ["waiting_block", "small_probe_block"],
        "required_cross_checks": [
            "live_quote",
            "fresh_financials_or_valuation_cross_check",
        ],
        "hard_blocker": False,
    }
    for idx, return_pct in enumerate((-1.1, -0.9), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "quality_status": "weak",
            "usage_guidance": usage_guidance,
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:usage-guidance-{idx}",
            manager_run_id=f"kis-manager-usage-guidance-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    result = service.project_page_effectiveness(min_samples=2)
    posture_metric = service.page_effectiveness(
        page_id="usage_guidance.risk_posture.repair_cross_check",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    live_quote_metric = service.page_effectiveness(
        page_id="usage_guidance.cross_check.live_quote",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["usage_guidance_updated_count"] == 3
    assert posture_metric["sample_count"] == 2
    assert posture_metric["status"] == "degraded"
    assert posture_metric["win_rate"] == 0.0
    assert posture_metric["expectancy"] == -1.0
    assert "usage_guidance:risk_posture:repair_cross_check" in posture_metric[
        "reasons"
    ]
    assert "usage_guidance_trust_level:low" in posture_metric["reasons"]
    assert live_quote_metric["status"] == "degraded"
    assert "usage_guidance:cross_check:live_quote" in live_quote_metric["reasons"]


def test_project_page_effectiveness_derives_quality_warning_prior_status_from_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        link = service.record_decision_link(
            selection_run_id=f"selection:auto-warning-{idx}",
            manager_run_id=f"kis-manager-auto-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.symbol.24545{idx}"],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={
                "selected_wiki_pages": {
                    "pages": [
                        {
                            "page_id": f"kis.symbol.24545{idx}",
                            "page_type": "symbol",
                            "quality_warnings": ["financials_missing"],
                            "evidence_quality": {
                                "warning_effectiveness": [
                                    {
                                        "warning": "financials_missing",
                                        "page_id": (
                                            "quality_warning.financials_missing"
                                        ),
                                        "status": "degraded",
                                        "sample_count": 4,
                                        "helpful_score": -5.0,
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert "quality_warning:financials_missing" in metric["reasons"]
    assert "quality_warning_prior_status:degraded" in metric["reasons"]


def test_project_page_effectiveness_reads_quality_warnings_from_selected_page_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "evidence_quality": {
                "status_counts": {"partial": 1},
                "top_warnings": [
                    {
                        "warning": "financials_missing",
                        "count": 2,
                    }
                ],
            },
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:evidence-warning-outcome-{idx}",
            manager_run_id=f"kis-manager-evidence-warning-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert metric["sample_count"] == 2
    assert "quality_warning:financials_missing" in metric["reasons"]


def test_project_page_effectiveness_reads_quality_warnings_from_selected_page_source_ref_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "source_refs": [
                {
                    "source_type": "symbol_fundamentals",
                    "source_id": f"24545{idx}:2026-07-03",
                    "evidence_quality": {
                        "status_counts": {"partial": 1},
                        "top_warnings": [
                            {
                                "warning": "financials_missing",
                                "count": 2,
                            }
                        ],
                    },
                }
            ],
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:ref-evidence-warning-outcome-{idx}",
            manager_run_id=f"kis-manager-ref-evidence-warning-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert metric["sample_count"] == 2
    assert "quality_warning:financials_missing" in metric["reasons"]


def test_project_page_effectiveness_reads_quality_warnings_from_nested_source_ref_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "source_refs": [
                {
                    "source_type": "wiki_symbol_summary",
                    "source_id": f"kis.symbol.24545{idx}:summary",
                    "source_refs": [
                        {
                            "source_type": "symbol_fundamentals",
                            "source_id": f"24545{idx}:2026-07-03",
                            "evidence_quality": {
                                "status_counts": {"partial": 1},
                                "top_warnings": [
                                    {
                                        "warning": "financials_missing",
                                        "count": 2,
                                    }
                                ],
                                "warning_effectiveness": [
                                    {
                                        "warning": "financials_missing",
                                        "page_id": (
                                            "quality_warning.financials_missing"
                                        ),
                                        "status": "degraded",
                                        "sample_count": 4,
                                        "helpful_score": -5.0,
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:nested-ref-warning-{idx}",
            manager_run_id=f"kis-manager-nested-ref-warning-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert metric["sample_count"] == 2
    assert "quality_warning:financials_missing" in metric["reasons"]
    assert "quality_warning_prior_status:degraded" in metric["reasons"]


def test_project_page_effectiveness_reads_quality_warning_prior_status_from_source_ref_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "source_refs": [
                {
                    "source_type": "symbol_fundamentals",
                    "source_id": f"24545{idx}:2026-07-03",
                    "evidence_quality": {
                        "status_counts": {"partial": 1},
                        "top_warnings": [
                            {
                                "warning": "financials_missing",
                                "count": 2,
                            }
                        ],
                        "warning_effectiveness": [
                            {
                                "warning": "financials_missing",
                                "page_id": "quality_warning.financials_missing",
                                "status": "degraded",
                                "sample_count": 4,
                                "helpful_score": -5.0,
                            }
                        ],
                    },
                }
            ],
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:ref-warning-prior-{idx}",
            manager_run_id=f"kis-manager-ref-warning-prior-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert "quality_warning:financials_missing" in metric["reasons"]
    assert "quality_warning_prior_status:degraded" in metric["reasons"]


def test_project_page_effectiveness_derives_repair_target_from_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "repair_targets": [
                {
                    "page_id": f"kis.symbol.24545{idx}",
                    "symbol": f"24545{idx}",
                    "recommended_action": action,
                }
            ],
            "repair_target_effectiveness": [
                {
                    "page_id": f"repair_target.{action}",
                    "status": "degraded",
                    "sample_count": 4,
                    "helpful_score": -5.0,
                    "reasons": [
                        "samples:4",
                        f"repair_target:{action}",
                        "repair_target_prior_status:degraded",
                    ],
                }
            ],
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:auto-repair-{idx}",
            manager_run_id=f"kis-manager-auto-repair-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id=f"repair_target.{action}",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert f"repair_target:{action}" in metric["reasons"]
    assert "repair_target_prior_status:degraded" in metric["reasons"]


def test_project_page_effectiveness_derives_repair_target_from_selected_page_source_ref(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "source_refs": [
                {
                    "source_type": "wiki_repair_queue",
                    "repair_targets": [
                        {
                            "page_id": f"kis.symbol.24545{idx}",
                            "symbol": f"24545{idx}",
                            "recommended_action": action,
                        }
                    ],
                    "repair_target_effectiveness": {
                        "page_id": f"repair_target.{action}",
                        "status": "degraded",
                        "sample_count": 3,
                        "helpful_score": -5.0,
                        "reasons": [
                            f"repair_target:{action}",
                            "repair_target_prior_status:degraded",
                        ],
                    },
                }
            ],
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:ref-repair-outcome-{idx}",
            manager_run_id=f"kis-manager-ref-repair-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id=f"repair_target.{action}",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert f"repair_target:{action}" in metric["reasons"]
    assert "repair_target_prior_status:degraded" in metric["reasons"]


def test_project_page_effectiveness_reads_repair_target_from_legacy_selected_page_source_ref(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    for idx, return_pct in enumerate((-1.2, -0.8), start=1):
        page_id = f"kis.symbol.24545{idx}"
        link = service.record_decision_link(
            selection_run_id=f"selection:legacy-ref-repair-{idx}",
            manager_run_id=f"kis-manager-legacy-ref-repair-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[page_id],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"source": "legacy_without_selected_page_metadata"},
        )
        evidence = {
            "selected_wiki_page": {
                "page_id": page_id,
                "page_type": "symbol",
                "symbols": [f"24545{idx}"],
                "source_refs": [
                    {
                        "source_type": "wiki_repair_queue",
                        "repair_targets": [
                            {
                                "page_id": page_id,
                                "symbol": f"24545{idx}",
                                "recommended_action": action,
                            }
                        ],
                        "repair_target_effectiveness": {
                            "page_id": f"repair_target.{action}",
                            "status": "degraded",
                            "sample_count": 3,
                            "helpful_score": -5.0,
                            "reasons": [
                                f"repair_target:{action}",
                                "repair_target_prior_status:degraded",
                            ],
                        },
                    }
                ],
            }
        }
        with service.wiki._connect() as conn:
            conn.execute(
                """
                INSERT INTO wiki_selection_outcomes (
                    outcome_id, link_id, selection_run_id, page_id,
                    decision_scope, venue, symbol, block_id, horizon,
                    outcome_kind, outcome_status, return_pct, mae_pct,
                    evidence_json, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"legacy-ref-repair-outcome-{idx}",
                    link["link_id"],
                    f"selection:legacy-ref-repair-{idx}",
                    page_id,
                    "kis",
                    "kis",
                    f"24545{idx}",
                    "mid",
                    "closed_block",
                    "loss",
                    return_pct,
                    return_pct,
                    json.dumps(evidence, sort_keys=True),
                    "2026-06-28T00:00:00+00:00",
                ),
            )

    service.project_page_effectiveness(min_samples=2)
    metric = service.page_effectiveness(
        page_id=f"repair_target.{action}",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert metric["status"] == "degraded"
    assert f"repair_target:{action}" in metric["reasons"]
    assert "repair_target_prior_status:degraded" in metric["reasons"]


def test_status_summarizes_quality_warning_effectiveness(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 7,
            "win_rate": 0.14,
            "expectancy": -1.1,
            "avg_return_pct": -1.1,
            "median_mae_pct": -1.7,
            "drawdown_pressure": 2.4,
            "helpful_score": -7.5,
            "confidence": 0.8,
            "status": "degraded",
            "reasons": ["quality_warning:financials_missing"],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 7,
            "win_rate": 0.71,
            "expectancy": 0.9,
            "helpful_score": 5.0,
            "confidence": 0.8,
            "status": "active",
            "reasons": ["symbol"],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.277810",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 4,
            "win_rate": 0.0,
            "expectancy": -1.4,
            "avg_return_pct": -1.4,
            "median_mae_pct": -2.1,
            "drawdown_pressure": 2.6,
            "helpful_score": -9.5,
            "confidence": 0.82,
            "status": "degraded",
            "reasons": [
                "quality_warning_source_page",
                "quality_warning:price_missing",
                "quality_warning_source_prior_status:degraded",
            ],
        }
    )

    status = service.project_status_snapshot()

    assert status["effectiveness_count"] == 3
    assert status["quality_warning_effectiveness_count"] == 1
    assert status["quality_warning_degraded_count"] == 1
    assert status["quality_warning_source_effectiveness_count"] == 1
    assert status["quality_warning_source_degraded_count"] == 1
    assert status["top_degraded_quality_warnings"] == [
        {
            "warning": "financials_missing",
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 7,
            "win_rate": 0.14,
            "expectancy": -1.1,
            "helpful_score": -7.5,
            "confidence": 0.8,
            "reasons": ["quality_warning:financials_missing"],
        }
    ]
    assert status["top_degraded_quality_warning_sources"] == [
        {
            "page_id": "kis.symbol.277810",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "status": "degraded",
            "sample_count": 4,
            "win_rate": 0.0,
            "expectancy": -1.4,
            "helpful_score": -9.5,
            "confidence": 0.82,
            "quality_warnings": ["price_missing"],
            "prior_statuses": ["degraded"],
            "reasons": [
                "quality_warning_source_page",
                "quality_warning:price_missing",
                "quality_warning_source_prior_status:degraded",
            ],
        }
    ]


def test_status_summarizes_active_quality_warning_sources(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 9,
            "win_rate": 0.67,
            "expectancy": 1.2,
            "avg_return_pct": 1.2,
            "median_mae_pct": -0.5,
            "drawdown_pressure": 0.8,
            "helpful_score": 6.5,
            "confidence": 0.9,
            "status": "active",
            "reasons": [
                "quality_warning_source_page",
                "quality_warning:valuation_stale",
                "quality_warning_source_prior_status:probe",
            ],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.277810",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 4,
            "win_rate": 0.0,
            "expectancy": -1.4,
            "avg_return_pct": -1.4,
            "helpful_score": -9.5,
            "confidence": 0.82,
            "status": "degraded",
            "reasons": [
                "quality_warning_source_page",
                "quality_warning:price_missing",
                "quality_warning_source_prior_status:degraded",
            ],
        }
    )

    status = service.project_status_snapshot()

    assert status["quality_warning_source_active_count"] == 1
    assert status["top_active_quality_warning_sources"] == [
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "status": "active",
            "sample_count": 9,
            "win_rate": 0.67,
            "expectancy": 1.2,
            "helpful_score": 6.5,
            "confidence": 0.9,
            "quality_warnings": ["valuation_stale"],
            "prior_statuses": ["probe"],
            "reasons": [
                "quality_warning_source_page",
                "quality_warning:valuation_stale",
                "quality_warning_source_prior_status:probe",
            ],
        }
    ]


def test_prompt_wiki_quality_warning_source_summary_keeps_active_sources() -> None:
    summary = {
        "source_count": 2,
        "degraded_count": 1,
        "active_count": 1,
        "top_degraded_sources": [
            {
                "page_id": "kis.symbol.277810",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 4,
                "win_rate": 0.0,
                "expectancy": -1.4,
                "helpful_score": -9.5,
                "confidence": 0.82,
                "quality_warnings": ["price_missing"],
                "prior_statuses": ["degraded"],
                "reasons": ["quality_warning_source_page"],
            }
        ],
        "top_active_sources": [
            {
                "page_id": "kis.symbol.005930",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 9,
                "win_rate": 0.67,
                "expectancy": 1.2,
                "helpful_score": 6.5,
                "confidence": 0.9,
                "status": "active",
                "quality_warnings": ["valuation_stale"],
                "prior_statuses": ["probe"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:valuation_stale",
                ],
            }
        ],
    }

    compact = JueWikiApplicationService._prompt_wiki_quality_warning_source_summary(
        {"quality_warning_source_summary": summary}
    )

    assert compact["active_count"] == 1
    assert compact["top_active_sources"] == [
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "status": "active",
            "quality_warnings": ["valuation_stale"],
            "prior_statuses": ["probe"],
            "reasons": [
                "quality_warning_source_page",
                "quality_warning:valuation_stale",
            ],
            "sample_count": 9,
            "win_rate": 0.67,
            "expectancy": 1.2,
            "helpful_score": 6.5,
            "confidence": 0.9,
        }
    ]


def test_prompt_wiki_quality_warning_source_summary_accepts_status_aliases() -> None:
    status_style_summary = {
        "quality_warning_source_effectiveness_count": 2,
        "quality_warning_source_degraded_count": 1,
        "quality_warning_source_active_count": 1,
        "top_degraded_quality_warning_sources": [
            {
                "page_id": "kis.symbol.277810",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 4,
                "win_rate": 0.0,
                "expectancy": -1.4,
                "helpful_score": -9.5,
                "confidence": 0.82,
                "quality_warnings": ["price_missing"],
                "prior_statuses": ["degraded"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:price_missing",
                ],
            }
        ],
        "top_active_quality_warning_sources": [
            {
                "page_id": "kis.symbol.005930",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 9,
                "win_rate": 0.67,
                "expectancy": 1.2,
                "helpful_score": 6.5,
                "confidence": 0.9,
                "quality_warnings": ["valuation_stale"],
                "prior_statuses": ["active"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:valuation_stale",
                ],
            }
        ],
    }

    compact = JueWikiApplicationService._prompt_wiki_quality_warning_source_summary(
        {"quality_warning_source_summary": status_style_summary}
    )

    assert compact["source_count"] == 2
    assert compact["degraded_count"] == 1
    assert compact["active_count"] == 1
    assert compact["top_degraded_sources"][0]["page_id"] == "kis.symbol.277810"
    assert compact["top_active_sources"][0]["page_id"] == "kis.symbol.005930"
    assert compact["top_active_sources"][0]["quality_warnings"] == [
        "valuation_stale"
    ]


def test_quality_warning_source_rows_preserve_row_status() -> None:
    rows = JueWikiApplicationService._quality_warning_source_status_rows(
        [
            {
                "page_id": "kis.symbol.005930",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 9,
                "win_rate": 0.67,
                "expectancy": 1.2,
                "helpful_score": 6.5,
                "confidence": 0.9,
                "status": "active",
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:valuation_stale",
                ],
            }
        ],
        limit=1,
        strongest_first=True,
    )

    assert rows[0]["status"] == "active"


def test_status_summarizes_wiki_application_coverage(tmp_path: Path) -> None:
    service = _service(tmp_path)
    kis_link = service.record_decision_link(
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
                "page_count": 1,
                "quality_warning_count": 1,
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                        "quality_warnings": ["financials_missing"],
                    }
                ],
            }
        },
    )
    service.record_decision_link(
        selection_run_id="selection:binance-legacy",
        manager_run_id="binance-manager-legacy",
        decision_scope="binance",
        decision_type="block_manager",
        selected_pages=["binance.symbol.BTCUSDT"],
        symbol="BTCUSDT",
        venue="binance",
        horizon="short",
    )
    service.record_selection_outcomes(
        link_id=kis_link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1200,
        pnl_currency="KRW",
        return_pct=0.7,
    )

    status = service.project_status_snapshot()

    assert status["wiki_application_coverage"] == {
        "decision_link_count": 2,
        "decision_links_with_selected_wiki_pages": 1,
        "decision_links_with_selected_wiki_pages_pct": 50.0,
        "selection_outcome_count": 1,
        "selection_outcomes_with_selected_wiki_page": 1,
        "selection_outcomes_with_selected_wiki_page_pct": 100.0,
        "selection_outcomes_with_quality_warnings": 1,
        "selection_outcome_attribution_filtered_count": 0,
        "closed_block_outcomes_without_horizon": 0,
        "closed_block_outcomes_without_horizon_pct": 0.0,
    }
    assert status["wiki_application_scopes"] == [
        {
            "decision_scope": "binance",
            "decision_link_count": 1,
            "decision_links_with_selected_wiki_pages": 0,
            "decision_links_with_selected_wiki_pages_pct": 0.0,
            "selection_outcome_count": 0,
            "selection_outcomes_with_selected_wiki_page": 0,
            "selection_outcomes_with_selected_wiki_page_pct": 0.0,
            "selection_outcomes_with_quality_warnings": 0,
            "selection_outcome_attribution_filtered_count": 0,
            "closed_block_outcomes_without_horizon": 0,
            "closed_block_outcomes_without_horizon_pct": 0.0,
        },
        {
            "decision_scope": "kis",
            "decision_link_count": 1,
            "decision_links_with_selected_wiki_pages": 1,
            "decision_links_with_selected_wiki_pages_pct": 100.0,
            "selection_outcome_count": 1,
            "selection_outcomes_with_selected_wiki_page": 1,
            "selection_outcomes_with_selected_wiki_page_pct": 100.0,
            "selection_outcomes_with_quality_warnings": 1,
            "selection_outcome_attribution_filtered_count": 0,
            "closed_block_outcomes_without_horizon": 0,
            "closed_block_outcomes_without_horizon_pct": 0.0,
        },
    ]


def test_status_counts_only_attributable_selected_outcomes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:status-attribution",
        manager_run_id="kis-manager-status-attribution",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                        "quality_warnings": ["financials_missing"],
                    }
                ]
            }
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "status-misattributed-outcome",
                link["link_id"],
                "selection:status-attribution",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                1.4,
                json.dumps(
                    {
                        "symbol": "000660",
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                            "quality_warnings": ["financials_missing"],
                        },
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    status = service.project_status_snapshot()
    kis_status = next(
        item
        for item in status["wiki_application_scopes"]
        if item["decision_scope"] == "kis"
    )

    assert status["wiki_application_coverage"]["selection_outcome_count"] == 1
    assert status["wiki_application_coverage"][
        "selection_outcomes_with_selected_wiki_page"
    ] == 0
    assert status["wiki_application_coverage"][
        "selection_outcomes_with_quality_warnings"
    ] == 0
    assert status["wiki_application_coverage"][
        "selection_outcome_attribution_filtered_count"
    ] == 1
    assert kis_status["selection_outcome_count"] == 1
    assert kis_status["selection_outcomes_with_selected_wiki_page"] == 0
    assert kis_status["selection_outcomes_with_quality_warnings"] == 0
    assert kis_status["selection_outcome_attribution_filtered_count"] == 1


def test_status_counts_playbook_outcomes_as_attributable_even_with_symbol_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:playbook-attribution",
        manager_run_id="kis-manager-playbook-attribution",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.playbook.reflection_lessons"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.playbook.reflection_lessons",
                        "page_type": "playbook",
                        "symbols": ["000660"],
                    }
                ]
            }
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "status-playbook-context-symbol-outcome",
                link["link_id"],
                "selection:playbook-attribution",
                "kis.playbook.reflection_lessons",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.4,
                json.dumps(
                    {
                        "symbol": "005930",
                        "selected_wiki_page": {
                            "page_id": "kis.playbook.reflection_lessons",
                            "page_type": "playbook",
                            "symbols": ["000660"],
                        },
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    status = service.project_status_snapshot()
    kis_status = next(
        item
        for item in status["wiki_application_scopes"]
        if item["decision_scope"] == "kis"
    )

    assert kis_status["selection_outcome_count"] == 1
    assert kis_status["selection_outcomes_with_selected_wiki_page"] == 1
    assert kis_status["selection_outcomes_with_selected_wiki_page_pct"] == 100.0
    assert kis_status["selection_outcome_attribution_filtered_count"] == 0


def test_status_flags_closed_block_outcomes_without_horizon(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:horizon-gap",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
            """,
            (
                "status-horizon-gap-outcome",
                link["link_id"],
                "selection:horizon-gap",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "blk-horizon-gap",
                "closed_block",
                "win",
                1.4,
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

    status = service.project_status_snapshot()
    kis_status = next(
        item
        for item in status["wiki_application_scopes"]
        if item["decision_scope"] == "kis"
    )

    assert kis_status["closed_block_outcomes_without_horizon"] == 1
    assert kis_status["closed_block_outcomes_without_horizon_pct"] == 100.0
    assert status["wiki_application_alerts"] == [
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


def test_status_prunes_stale_misattributed_effectiveness_metrics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
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
    link = service.record_decision_link(
        selection_run_id="selection:status-stale-metric",
        manager_run_id="kis-manager-status-stale",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "status-stale-misattributed-outcome",
                link["link_id"],
                "selection:status-stale-metric",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    status = service.project_status_snapshot()
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert status["effectiveness_count"] == 0
    assert status["degraded_count"] == 0
    assert status["effectiveness_cleanup"]["stale_effectiveness_removed_count"] == 1
    assert metric["status"] == "missing"


def test_status_supersedes_stale_latest_mode_recommendation_after_cleanup(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:status-legacy-primary",
                "kis",
                "primary",
                "assist",
                40,
                0.75,
                json.dumps(["legacy polluted recommendation"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
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
    link = service.record_decision_link(
        selection_run_id="selection:status-stale-recommendation",
        manager_run_id="kis-manager-status-stale-recommendation",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "status-stale-recommendation-outcome",
                link["link_id"],
                "selection:status-stale-recommendation",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    status = service.project_status_snapshot()

    assert status["effectiveness_cleanup"]["stale_effectiveness_removed_count"] == 1
    assert status["latest_recommendation"]["recommendation_id"] != (
        "wiki-mode:status-legacy-primary"
    )
    assert status["latest_recommendation"]["recommended_mode"] == "observe"
    assert (
        "stale_effectiveness_removed:1"
        in status["latest_recommendation"]["reasons"]
    )


def test_status_flags_wiki_application_coverage_gaps(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.record_decision_link(
        selection_run_id="selection:binance-gap",
        manager_run_id="binance-manager-gap",
        decision_scope="binance",
        decision_type="block_manager",
        selected_pages=["binance.symbol.BTCUSDT"],
        symbol="BTCUSDT",
        venue="binance",
        horizon="short",
    )
    service.record_decision_link(
        selection_run_id="selection:kis-no-outcomes",
        manager_run_id="kis-manager-no-outcomes",
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

    status = service.project_status_snapshot()

    assert status["wiki_application_health"] == "warning"
    assert status["wiki_application_alerts"] == [
        {
            "severity": "warning",
            "code": "wiki_selected_pages_missing",
            "decision_scope": "binance",
            "message": (
                "binance wiki selected page trace coverage is 0.0%; "
                "manager prompts need selected_wiki_pages metadata."
            ),
            "action": "project_decision_links_or_restart_stale_runner",
        },
        {
            "severity": "warning",
            "code": "wiki_outcome_feedback_missing",
            "decision_scope": "binance",
            "message": (
                "binance wiki outcome feedback coverage is 0.0%; "
                "closed/error block outcomes need selected_wiki_page evidence."
            ),
            "action": "project_selection_outcomes_and_page_effectiveness",
        },
        {
            "severity": "warning",
            "code": "wiki_outcome_feedback_missing",
            "decision_scope": "kis",
            "message": (
                "kis wiki outcome feedback coverage is 0.0%; "
                "closed/error block outcomes need selected_wiki_page evidence."
            ),
            "action": "project_selection_outcomes_and_page_effectiveness",
        },
    ]


def test_backfill_decision_link_selected_wiki_pages_from_current_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={"Current Stance": "Large cap memory fixture."},
        source_refs=[
            {
                "source_type": "naver_fundamentals",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.74,
        freshness="current",
    )
    service.record_decision_link(
        selection_run_id="selection:legacy-page-ids",
        manager_run_id="kis-manager-legacy",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"source": "legacy_link"},
    )

    result = service.backfill_decision_link_selected_wiki_pages()
    links = service.list_decision_links(selection_run_id="selection:legacy-page-ids")

    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 0
    assert links[0]["metadata"]["source"] == "legacy_link"
    assert links[0]["metadata"]["selected_wiki_pages"]["pages"] == [
        {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "confidence": 0.74,
            "symbols": ["005930"],
            "quality_status": "partial",
            "quality_warnings": ["financials_missing"],
                "evidence_quality": {
                    "row_count": 1,
                    "status_counts": {"partial": 1},
                    "warning_counts": {"financials_missing": 1},
                    "top_warnings": [
                    {
                        "warning": "financials_missing",
                        "count": 1,
                        }
                    ],
                    "warning_page_ids": {
                        "financials_missing": ["kis.symbol.005930"],
                    },
                    "caution_page_ids": ["kis.symbol.005930"],
                },
                "usage_guidance": {
                "trust_level": "medium",
                "risk_posture": "supporting_cross_check",
                "decision_use": (
                    "use this page as supporting context after resolving "
                    "candidate-level evidence gaps"
                ),
                "allowed_uses": [
                    "supporting_context",
                    "follow_up_research",
                    "target_stop_context",
                    "risk_note_context",
                ],
                "required_cross_checks": [
                    "live_quote",
                    "fresh_financials_or_valuation_cross_check",
                ],
                "hard_blocker": False,
            },
        }
    ]
    assert links[0]["metadata"]["selected_wiki_pages_backfill_source"] == (
        "current_wiki_pages"
    )


def test_project_page_effectiveness_backfills_quality_warning_from_link_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:backfill-warning",
        manager_run_id="kis-manager-backfill",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.245450"],
        symbol="245450",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.245450",
                        "page_type": "symbol",
                        "quality_warnings": ["financials_missing"],
                    }
                ]
            }
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "manual-outcome:backfill",
                link["link_id"],
                "selection:backfill-warning",
                "kis.symbol.245450",
                "kis",
                "kis",
                "245450",
                "mid",
                "closed_block",
                "loss",
                -1.4,
                -1.5,
                json.dumps({"exit_reason": "stop"}),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    result = service.project_page_effectiveness(min_samples=1)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-warning"
    )
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["selected_page_evidence_backfilled_count"] == 1
    assert outcomes[0]["evidence"]["selected_wiki_page"]["quality_warnings"] == [
        "financials_missing"
    ]
    assert metric["status"] == "degraded"


def test_project_page_effectiveness_marks_helpful_page(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for idx, return_pct in enumerate([1.2, 0.8, -0.2, 1.1, 0.4, 0.9], start=1):
        link = service.record_decision_link(
            selection_run_id=f"selection:{idx}",
            manager_run_id=f"manager:{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.playbook.reflection_lessons"],
            symbol="005930",
            block_id=f"blk-{idx}",
            venue="kis",
            horizon="mid_term",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="win" if return_pct > 0 else "loss",
            return_pct=return_pct,
            mae_pct=-0.3,
        )

    result = service.project_page_effectiveness(min_samples=5)
    metric = service.page_effectiveness(
        page_id="kis.playbook.reflection_lessons",
        decision_scope="kis",
        venue="kis",
        horizon="mid_term",
    )

    assert result["status"] == "ok"
    assert metric["sample_count"] == 6
    assert metric["win_rate"] > 0.6
    assert metric["helpful_score"] > 0
    assert metric["status"] in {"active", "probe"}


def test_page_effectiveness_falls_back_to_general_metric_for_specific_horizon(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.playbook.reflection_lessons",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "",
            "sample_count": 8,
            "win_rate": 0.75,
            "expectancy": 0.8,
            "avg_return_pct": 0.8,
            "median_mae_pct": -0.3,
            "drawdown_pressure": 0.3,
            "helpful_score": 5.0,
            "confidence": 0.9,
            "status": "active",
            "reasons": ["general reflection memory active"],
        }
    )

    metric = service.page_effectiveness(
        page_id="kis.playbook.reflection_lessons",
        decision_scope="kis",
        venue="kis",
        horizon="mid_term",
    )

    assert metric["status"] == "active"
    assert metric["horizon"] == ""
    assert metric["requested_horizon"] == "mid_term"
    assert metric["fallback_reason"] == "general_horizon_metric"
    assert metric["reasons"] == ["general reflection memory active"]


def test_page_effectiveness_keeps_low_sample_pages_probe(tmp_path: Path) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:one",
        manager_run_id="manager:one",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        venue="kis",
        horizon="short_term",
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        return_pct=3.0,
    )

    service.project_page_effectiveness(min_samples=5)
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="short_term",
    )

    assert metric["sample_count"] == 1
    assert metric["status"] == "probe"
    assert metric["confidence"] < 1.0


def test_mode_recommendations_promote_primary_only_with_enough_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(25):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"kis.playbook.{idx}",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid_term",
                "sample_count": 8,
                "win_rate": 0.75,
                "expectancy": 1.1,
                "avg_return_pct": 1.1,
                "median_mae_pct": -0.2,
                "drawdown_pressure": 0.2,
                "helpful_score": 7.0,
                "confidence": 1.0,
                "status": "active",
                "reasons": ["fixture"],
            }
        )

    result = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"kis": "assist"},
    )

    assert result["status"] == "ok"
    assert result["recommendations"]
    assert result["recommendations"][0]["recommended_mode"] in {"assist", "primary"}


def test_mode_recommendations_reuse_unchanged_projection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(25):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"kis.playbook.stable.{idx}",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid_term",
                "sample_count": 8,
                "helpful_score": 7.0,
                "status": "active",
            }
        )

    first = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"kis": "assist"},
    )
    second = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"kis": "assist"},
    )
    with service.wiki._connect() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM wiki_mode_recommendations
            WHERE decision_scope = 'kis'
            """
        ).fetchone()[0]

    assert count == 1
    assert second["recommendations"][0]["recommendation_id"] == (
        first["recommendations"][0]["recommendation_id"]
    )
    assert second["recommendations"][0]["reused"] is True


def test_latest_mode_recommendation_refreshes_from_prompt_mode_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(25):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"kis.playbook.latest_refresh.{idx}",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid_term",
                "sample_count": 8,
                "helpful_score": 7.0,
                "status": "active",
            }
        )
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:latest-stale-primary",
                "kis",
                "primary",
                "",
                200,
                0.75,
                json.dumps(["old primary"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    for idx in range(20):
        selection_run_id = f"selection:latest-primary-loss-{idx}"
        service.wiki.record_selection_run(
            run_id=selection_run_id,
            target_scope="kis",
            request={
                "target_scope": "kis",
                "prompt_mode_application": {
                    "target_scope": "kis",
                    "mode_recommendation": {
                        "recommendation_id": "wiki-mode:latest-stale-primary",
                        "recommended_mode": "primary",
                        "sample_count": 200,
                        "confidence": 0.75,
                    },
                },
            },
            selected_pages=[
                {
                    "page_id": f"kis.playbook.latest_primary_loss.{idx}",
                    "rank": 1,
                    "score": 9.0,
                }
            ],
            rejected_pages=[],
            char_count=100,
            max_chars=1000,
            status="ok",
        )
        link = service.record_decision_link(
            selection_run_id=selection_run_id,
            manager_run_id=f"kis-manager-latest-primary-loss-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.playbook.latest_primary_loss.{idx}"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="primary",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=-0.8,
            evidence={"block_id": f"latest-primary-loss-{idx}"},
        )

    latest = service.latest_mode_recommendation()

    assert latest["recommendation_id"] != "wiki-mode:latest-stale-primary"
    assert latest["recommended_mode"] == "assist"
    assert "prompt_mode_effectiveness:primary:degraded" in latest["reasons"]


def test_mode_recommendations_demote_primary_when_primary_prompt_mode_degraded(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(25):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"kis.playbook.active.{idx}",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid_term",
                "sample_count": 8,
                "win_rate": 0.75,
                "expectancy": 1.1,
                "avg_return_pct": 1.1,
                "median_mae_pct": -0.2,
                "drawdown_pressure": 0.2,
                "helpful_score": 7.0,
                "confidence": 1.0,
                "status": "active",
                "reasons": ["fixture"],
            }
        )
    for idx, return_pct in enumerate([-1.2, -0.8]):
        selection_run_id = f"selection:kis-primary-loss-{idx}"
        service.wiki.record_selection_run(
            run_id=selection_run_id,
            target_scope="kis",
            request={
                "target_scope": "kis",
                "prompt_mode_application": {
                    "target_scope": "kis",
                    "mode_recommendation": {
                        "recommendation_id": f"wiki-mode:kis-primary-{idx}",
                        "recommended_mode": "primary",
                        "sample_count": 40,
                        "confidence": 0.75,
                    },
                },
            },
            selected_pages=[
                {
                    "page_id": f"kis.playbook.primary_loss.{idx}",
                    "rank": 1,
                    "score": 9.0,
                }
            ],
            rejected_pages=[],
            char_count=100,
            max_chars=1000,
            status="ok",
        )
        link = service.record_decision_link(
            selection_run_id=selection_run_id,
            manager_run_id=f"kis-manager-primary-loss-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.playbook.primary_loss.{idx}"],
            symbol="005930",
            venue="kis",
            horizon="mid",
            action="create_block",
            prompt_mode="primary",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            evidence={"block_id": f"primary-loss-{idx}"},
        )

    result = service.project_mode_recommendations(
        min_samples=2,
        current_modes={"kis": "assist"},
    )

    recommendation = result["recommendations"][0]
    assert recommendation["recommended_mode"] == "assist"
    assert "prompt_mode_effectiveness:primary:degraded" in recommendation["reasons"]


def test_mode_recommendations_demote_primary_when_primary_trust_profile_degraded(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(25):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"kis.playbook.trust_active.{idx}",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid_term",
                "sample_count": 8,
                "win_rate": 0.75,
                "expectancy": 1.1,
                "avg_return_pct": 1.1,
                "median_mae_pct": -0.2,
                "drawdown_pressure": 0.2,
                "helpful_score": 7.0,
                "confidence": 1.0,
                "status": "active",
                "reasons": ["fixture"],
            }
        )
    for idx, return_pct in enumerate([-1.4, -0.7]):
        link = service.record_decision_link(
            selection_run_id=f"selection:kis-primary-trust-loss-{idx}",
            manager_run_id=f"kis-manager-primary-trust-loss-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[f"kis.playbook.trust_active.{idx}"],
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
                    "recommendation_id": f"wiki-mode:kis-primary-trust-{idx}",
                }
            },
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            evidence={"block_id": f"primary-trust-loss-{idx}"},
        )

    result = service.project_mode_recommendations(
        min_samples=2,
        current_modes={"kis": "assist"},
    )

    recommendation = result["recommendations"][0]
    assert recommendation["recommended_mode"] == "assist"
    assert (
        "trust_profile_effectiveness:primary_compiled_knowledge:degraded"
        in recommendation["reasons"]
    )


def test_mode_recommendations_prune_legacy_misattributed_metrics_first(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
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
    link = service.record_decision_link(
        selection_run_id="selection:mode-stale-metric",
        manager_run_id="kis-manager-mode-stale",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-mode-stale-metric-outcome",
                link["link_id"],
                "selection:mode-stale-metric",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    result = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"kis": "assist"},
    )
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["stale_effectiveness_removed_count"] == 1
    assert result["recommendations"][0]["decision_scope"] == "kis"
    assert result["recommendations"][0]["recommended_mode"] == "observe"
    assert "stale_effectiveness_removed:1" in result["recommendations"][0]["reasons"]
    assert metric["status"] == "missing"


def test_mode_recommendations_supersede_stale_primary_after_metric_cleanup(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:legacy-primary",
                "kis",
                "primary",
                "assist",
                40,
                0.75,
                json.dumps(["legacy polluted recommendation"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
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
    link = service.record_decision_link(
        selection_run_id="selection:mode-stale-primary",
        manager_run_id="kis-manager-mode-stale-primary",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-mode-stale-primary-outcome",
                link["link_id"],
                "selection:mode-stale-primary",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    result = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"kis": "assist"},
    )
    latest = service.latest_mode_recommendation()

    assert result["stale_effectiveness_removed_count"] == 1
    assert result["recommendations"][0]["decision_scope"] == "kis"
    assert result["recommendations"][0]["recommended_mode"] == "observe"
    assert latest["recommendation_id"] != "wiki-mode:legacy-primary"
    assert latest["recommended_mode"] == "observe"
    assert "stale_effectiveness_removed:1" in latest["reasons"]


def test_latest_mode_recommendation_prunes_stale_primary_before_read(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:direct-latest-primary",
                "kis",
                "primary",
                "assist",
                40,
                0.75,
                json.dumps(["legacy polluted recommendation"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["legacy polluted"],
        }
    )
    link = service.record_decision_link(
        selection_run_id="selection:direct-latest-stale",
        manager_run_id="kis-manager-direct-latest-stale",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "direct-latest-stale-outcome",
                link["link_id"],
                "selection:direct-latest-stale",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    latest = service.latest_mode_recommendation()

    assert latest["recommendation_id"] != "wiki-mode:direct-latest-primary"
    assert latest["recommended_mode"] == "observe"
    assert "stale_effectiveness_removed:1" in latest["reasons"]


def test_stale_prune_preserves_active_auxiliary_effectiveness_metrics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    repair_action = "refresh_symbol_financials"
    usage_page_id = "usage_guidance.cross_check.live_quote"
    repair_page_id = f"repair_target.{repair_action}"
    for page_id in (usage_page_id, repair_page_id):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": page_id,
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 3,
                "helpful_score": -2.0,
                "confidence": 0.8,
                "status": "degraded",
                "reasons": ["existing auxiliary metric"],
            }
        )

    stale_link = service.record_decision_link(
        selection_run_id="selection:aux-stale",
        manager_run_id="kis-manager-aux-stale",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                        "usage_guidance": {
                            "risk_posture": "repair_cross_check",
                            "required_cross_checks": ["live_quote"],
                        },
                        "repair_targets": [
                            {"recommended_action": repair_action},
                        ],
                    }
                ]
            }
        },
    )
    active_link = service.record_decision_link(
        selection_run_id="selection:aux-active",
        manager_run_id="kis-manager-aux-active",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.000660"],
        symbol="000660",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {
                "pages": [
                    {
                        "page_id": "kis.symbol.000660",
                        "page_type": "symbol",
                        "symbols": ["000660"],
                        "usage_guidance": {
                            "risk_posture": "repair_cross_check",
                            "required_cross_checks": ["live_quote"],
                        },
                        "repair_targets": [
                            {"recommended_action": repair_action},
                        ],
                    }
                ]
            }
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "stale-auxiliary-mismatch-outcome",
                stale_link["link_id"],
                "selection:aux-stale",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                2.0,
                json.dumps(
                    {
                        "symbol": "000660",
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                            "usage_guidance": {
                                "risk_posture": "repair_cross_check",
                                "required_cross_checks": ["live_quote"],
                            },
                            "repair_targets": [
                                {"recommended_action": repair_action},
                            ],
                        },
                        "usage_guidance": {
                            "risk_posture": "repair_cross_check",
                            "required_cross_checks": ["live_quote"],
                        },
                        "repair_targets": [
                            {"recommended_action": repair_action},
                        ],
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )
    service.record_selection_outcomes(
        link_id=active_link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        return_pct=-1.0,
    )

    result = service._prune_stale_page_effectiveness_metrics()
    usage_metric = service.page_effectiveness(
        page_id=usage_page_id,
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    repair_metric = service.page_effectiveness(
        page_id=repair_page_id,
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert "stale_effectiveness_removed_count" not in result
    assert usage_metric["status"] == "degraded"
    assert repair_metric["status"] == "degraded"


def test_list_mode_recommendations_prunes_stale_primary_before_read(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:list-legacy-primary",
                "kis",
                "primary",
                "assist",
                40,
                0.75,
                json.dumps(["legacy polluted recommendation"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["legacy polluted"],
        }
    )
    link = service.record_decision_link(
        selection_run_id="selection:list-mode-stale",
        manager_run_id="kis-manager-list-mode-stale",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "list-mode-stale-outcome",
                link["link_id"],
                "selection:list-mode-stale",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    rows = service.list_mode_recommendations(limit=3)

    assert rows[0]["recommendation_id"] != "wiki-mode:list-legacy-primary"
    assert rows[0]["recommended_mode"] == "observe"
    assert "stale_effectiveness_removed:1" in rows[0]["reasons"]
    assert "wiki-mode:list-legacy-primary" not in {
        row["recommendation_id"] for row in rows
    }


def test_list_mode_recommendations_recomputes_from_remaining_valid_metrics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:list-stale-primary-with-valid-metric",
                "kis",
                "primary",
                "assist",
                40,
                0.75,
                json.dumps(["legacy polluted recommendation"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["legacy polluted"],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.playbook.valid",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "helpful_score": 7.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["valid surviving evidence"],
        }
    )
    link = service.record_decision_link(
        selection_run_id="selection:list-mode-stale-with-valid",
        manager_run_id="kis-manager-list-mode-stale-with-valid",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "list-mode-stale-with-valid-outcome",
                link["link_id"],
                "selection:list-mode-stale-with-valid",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    rows = service.list_mode_recommendations(limit=3)

    assert rows[0]["recommended_mode"] == "primary"
    assert rows[0]["sample_count"] == 40
    assert "samples:40" in rows[0]["reasons"]
    assert "wiki-mode:list-stale-primary-with-valid-metric" not in {
        row["recommendation_id"] for row in rows
    }


def test_status_recomputes_mode_recommendation_from_remaining_valid_metrics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:status-stale-primary-with-valid-metric",
                "kis",
                "primary",
                "assist",
                40,
                0.75,
                json.dumps(["legacy polluted recommendation"]),
                "2026-06-27T00:00:00+00:00",
            ),
        )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["legacy polluted"],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": "kis.playbook.valid",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 40,
            "helpful_score": 7.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["valid surviving evidence"],
        }
    )
    link = service.record_decision_link(
        selection_run_id="selection:status-mode-stale-with-valid",
        manager_run_id="kis-manager-status-mode-stale-with-valid",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="000660",
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
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "status-mode-stale-with-valid-outcome",
                link["link_id"],
                "selection:status-mode-stale-with-valid",
                "kis.symbol.005930",
                "kis",
                "kis",
                "000660",
                "mid",
                "closed_block",
                "win",
                4.0,
                json.dumps(
                    {
                        "symbol": "000660",
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

    status = service.project_status_snapshot()

    assert status["effectiveness_cleanup"]["stale_effectiveness_removed_count"] == 1
    assert status["latest_recommendation"]["recommended_mode"] == "primary"
    assert status["latest_recommendation"]["sample_count"] == 40
    assert "samples:40" in status["latest_recommendation"]["reasons"]


def test_status_exposes_latest_mode_recommendation_by_scope(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, created_at
            ) VALUES
                (?, ?, '', '', '', ?, ?, ?, ?, ?, ?),
                (?, ?, '', '', '', ?, ?, ?, ?, ?, ?),
                (?, ?, '', '', '', ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:kis-old",
                "kis",
                "observe",
                "assist",
                3,
                0.15,
                json.dumps(["old"]),
                "2026-06-27T00:00:00+00:00",
                "wiki-mode:binance-latest",
                "binance",
                "assist",
                "assist",
                30,
                0.65,
                json.dumps(["binance current"]),
                "2026-06-28T01:00:00+00:00",
                "wiki-mode:kis-latest",
                "kis",
                "primary",
                "assist",
                60,
                0.75,
                json.dumps(["kis current"]),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    status = service.project_status_snapshot()

    by_scope = status["mode_recommendations_by_scope"]
    assert by_scope["kis"]["recommendation_id"] == "wiki-mode:kis-latest"
    assert by_scope["kis"]["recommended_mode"] == "primary"
    assert by_scope["binance"]["recommendation_id"] == "wiki-mode:binance-latest"
    assert by_scope["binance"]["recommended_mode"] == "assist"


def test_mode_recommendation_omits_missing_metrics_but_keeps_explicit_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, metric_presence_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:missing-metrics",
                "kis",
                "observe",
                "assist",
                0,
                0.0,
                json.dumps(["missing metric projection"]),
                json.dumps({"__tracked__": True}),
                "2026-06-28T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO wiki_mode_recommendations (
                recommendation_id, decision_scope, venue, page_type, horizon,
                recommended_mode, current_mode, sample_count, confidence,
                reasons_json, metric_presence_json, created_at
            ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wiki-mode:explicit-zero",
                "binance",
                "observe",
                "assist",
                0,
                0.0,
                json.dumps(["explicit zero metric projection"]),
                json.dumps(
                    {
                        "__tracked__": True,
                        "sample_count": True,
                        "confidence": True,
                    }
                ),
                "2026-06-28T01:00:00+00:00",
            ),
        )

    rows = {
        row["decision_scope"]: row
        for row in service.list_mode_recommendations(limit=5, refresh=False)
    }

    assert "sample_count" not in rows["kis"]
    assert "confidence" not in rows["kis"]
    assert rows["binance"]["sample_count"] == 0
    assert rows["binance"]["confidence"] == 0.0


def test_mode_recommendations_demote_degraded_scope_to_observe(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(8):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"binance.playbook.{idx}",
                "decision_scope": "binance",
                "venue": "binance",
                "sample_count": 4,
                "helpful_score": -5.0,
                "status": "degraded",
            }
        )

    result = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"binance": "assist"},
    )

    assert result["recommendations"][0]["recommended_mode"] == "observe"


def test_mode_recommendations_keep_mixed_evidence_in_assist_mode(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for idx in range(4):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"binance.playbook.active.{idx}",
                "decision_scope": "binance",
                "venue": "binance",
                "sample_count": 8,
                "helpful_score": 6.0,
                "status": "active",
            }
        )
    for idx in range(10):
        service.wiki.upsert_page_effectiveness(
            {
                "page_id": f"binance.playbook.degraded.{idx}",
                "decision_scope": "binance",
                "venue": "binance",
                "sample_count": 8,
                "helpful_score": -6.0,
                "status": "degraded",
            }
        )

    result = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"binance": "assist"},
    )

    assert result["recommendations"][0]["recommended_mode"] == "assist"


def test_project_decision_links_ingests_manager_prompt_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-1",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.005930"],
                            "trust_profile": {
                                "prompt_mode": "assist",
                                "authority": "supporting_evidence",
                                "trust_level": "medium",
                                "posture": "validated_mode_recommendation",
                                "decision_use": "use selected wiki pages as supporting evidence",
                                "recommendation_id": "wiki-mode:kis-assist",
                                "recommended_mode": "assist",
                                "configured_prompt_mode": "primary",
                                "sample_count": 28,
                                "confidence": 0.65,
                                "reasons": ["samples:28"],
                                "policy_reason": "validated_assist_recommendation",
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
                                    ],
                                    "required_cross_checks": [
                                        "live_quote",
                                        "risk_gate",
                                    ],
                                    "conflict_resolution": (
                                        "prefer_live_execution_data_and_record_wiki_repair"
                                    ),
                                    "risk_posture_guidance": {
                                        "current_risk_posture": "supporting_evidence",
                                        "current_status": "degraded",
                                        "preferred_risk_postures": ["repair_probe"],
                                        "degraded_risk_postures": [
                                            "supporting_evidence"
                                        ],
                                        "recommended_allowed_uses": [
                                            "repair_candidate_design",
                                            "small_probe_block",
                                        ],
                                        "deprioritized_allowed_uses": [
                                            "candidate_ranking",
                                            "target_stop_context",
                                        ],
                                        "decision_adjustment": {
                                            "action": (
                                                "shift_to_preferred_risk_posture"
                                            ),
                                            "target_risk_posture": "repair_probe",
                                            "reason": (
                                                "current_risk_posture_degraded"
                                            ),
                                        },
                                        "guidance": (
                                            "prefer active risk postures and reduce degraded "
                                            "postures unless live cross-checks override"
                                        ),
                                    },
                                },
                            },
                            "trust_profile_effectiveness": {
                                "target_scope": "kis",
                                "trust_profile_count": 1,
                                "trust_profiles": [
                                    {
                                        "decision_scope": "kis",
                                        "authority": "supporting_evidence",
                                        "sample_count": 12,
                                        "status": "active",
                                        "avg_return_pct": 0.8,
                                        "reasons": ["samples:12"],
                                    }
                                ],
                            },
                            "decision_adjustments": [
                                {
                                    "source": (
                                        "usage_contract.risk_posture_guidance"
                                    ),
                                    "action": (
                                        "shift_to_preferred_risk_posture"
                                    ),
                                    "target_risk_posture": "repair_probe",
                                    "reason": "current_risk_posture_degraded",
                                    "current_risk_posture": "supporting_evidence",
                                    "current_status": "degraded",
                                    "recommended_allowed_uses": [
                                        "repair_candidate_design",
                                        "small_probe_block",
                                    ],
                                    "deprioritized_allowed_uses": [
                                        "candidate_ranking",
                                        "target_stop_context",
                                    ],
                                    "decision_adjustment_effectiveness": {
                                        "action": (
                                            "shift_to_preferred_risk_posture"
                                        ),
                                        "target_risk_posture": "repair_probe",
                                        "reason": "current_risk_posture_degraded",
                                        "sample_count": 6,
                                        "status": "active",
                                        "avg_return_pct": 0.72,
                                        "confidence": 1.0,
                                    },
                                }
                            ],
                            "budget_report": {"char_count": 123},
                        },
                        "jue_wiki_decision_adjustment_audit_contract": {
                            "version": (
                                "jue_wiki_decision_adjustment_audit_contract_v1"
                            ),
                            "status": "repair_required",
                            "adjustment_count": 1,
                            "actions": [
                                "audit_preferred_risk_posture_before_shift"
                            ],
                            "target_risk_postures": ["repair_probe"],
                            "required_review": [
                                "verify why prior shift_to_preferred_risk_posture underperformed",
                                "compare live quote, account state, risk gate, and fresh evidence before adopting target risk posture",
                            ],
                            "accepted_resolutions": [
                                "create a smaller repair probe or waiting block",
                                "reject the shift and create a wiki repair note",
                            ],
                            "hard_blocker": False,
                            "safety_gates_still_override": True,
                            "audit_policies": [
                                {
                                    "action": "repair_audit_contract_before_reuse",
                                    "reason": "prior_audit_contract_degraded",
                                    "target_risk_posture": "repair_probe",
                                    "hard_blocker": False,
                                }
                            ],
                            "audit_effectiveness": [
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
                        "decision_inputs": [
                            "account",
                            "opportunity_research_brief",
                            "jue_wiki_decision_adjustments",
                            "jue_wiki_decision_adjustment_audit_contract",
                        ],
                        "execution_gate": {
                            "status": "ok",
                            "execution_mode": "live",
                            "execute_orders": True,
                            "kill_switch": {"enabled": False},
                            "market_session": "regular",
                            "new_entry_allowed_by_session": True,
                            "cash_available": {
                                "cash_krw": 1_000_000,
                                "orderable_cash_krw": 800_000,
                                "total_equity_usdt": 999,
                                "ignored": "drop",
                            },
                            "active_block_count": 2,
                            "waiting_entry_block_count": 1,
                            "pending_order_block_count": 0,
                            "duplicate_order_guard": {"status": "ok"},
                        },
                        "opportunity_research_brief": {
                            "status": "ok",
                            "role": "minimum_surviving_opportunity_context",
                            "source_status": {
                                "daily_discovery": "ok",
                                "research_spine": "ok",
                            },
                            "pre_surge_candidates": [{"symbol": "005930"}],
                            "block_candidates": [],
                            "daily_discovery_candidates": [],
                            "aggressive_candidates": [{"symbol": "000660"}],
                        },
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "zero_action_streak": 2,
                            "candidate_count": 3,
                            "strong_candidate_count": 1,
                            "top_candidates": [
                                {"symbol": "005930"},
                                {"symbol": "000660"},
                            ],
                            "required_resolution": "resolve at least one candidate",
                        },
                    }
                ),
            ),
        )

    result = service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-1")

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    assert links[0]["decision_scope"] == "kis"
    assert links[0]["decision_type"] == "block_manager"
    assert links[0]["selected_pages"] == ["kis.symbol.005930"]
    assert links[0]["metadata"]["jue_wiki_trust_profile"] == {
        "prompt_mode": "assist",
        "authority": "supporting_evidence",
        "trust_level": "medium",
        "posture": "validated_mode_recommendation",
        "decision_use": "use selected wiki pages as supporting evidence",
        "recommendation_id": "wiki-mode:kis-assist",
        "recommended_mode": "assist",
        "configured_prompt_mode": "primary",
        "sample_count": 28,
        "confidence": 0.65,
        "reasons": ["samples:28"],
        "policy_reason": "validated_assist_recommendation",
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
            ],
            "required_cross_checks": [
                "live_quote",
                "risk_gate",
            ],
            "conflict_resolution": (
                "prefer_live_execution_data_and_record_wiki_repair"
            ),
            "risk_posture_guidance": {
                "current_risk_posture": "supporting_evidence",
                "current_status": "degraded",
                "preferred_risk_postures": ["repair_probe"],
                "degraded_risk_postures": ["supporting_evidence"],
                "recommended_allowed_uses": [
                    "repair_candidate_design",
                    "small_probe_block",
                ],
                "deprioritized_allowed_uses": [
                    "candidate_ranking",
                    "target_stop_context",
                ],
                "decision_adjustment": {
                    "action": "shift_to_preferred_risk_posture",
                    "target_risk_posture": "repair_probe",
                    "reason": "current_risk_posture_degraded",
                },
                "guidance": (
                    "prefer active risk postures and reduce degraded "
                    "postures unless live cross-checks override"
                ),
            },
        },
    }
    assert links[0]["metadata"]["jue_wiki_trust_profile_effectiveness"] == {
        "target_scope": "kis",
        "trust_profile_count": 1,
        "trust_profiles": [
            {
                "decision_scope": "kis",
                "authority": "supporting_evidence",
                "sample_count": 12,
                "status": "active",
                "avg_return_pct": 0.8,
                "reasons": ["samples:12"],
            }
        ],
    }
    assert links[0]["metadata"]["jue_wiki_decision_adjustments"] == [
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
            ],
            "deprioritized_allowed_uses": [
                "candidate_ranking",
                "target_stop_context",
            ],
            "decision_adjustment_effectiveness": {
                "action": "shift_to_preferred_risk_posture",
                "target_risk_posture": "repair_probe",
                "reason": "current_risk_posture_degraded",
                "sample_count": 6,
                "status": "active",
                "avg_return_pct": 0.72,
                "confidence": 1.0,
            },
            "evidence_grade": {
                "status": "positive",
                "basis": "decision_adjustment_effectiveness",
                "sample_count": 6,
                "avg_return_pct": 0.72,
                "confidence": 1.0,
                "instruction": "usable_with_live_cross_check",
            },
        }
    ]
    assert links[0]["metadata"]["jue_wiki_decision_adjustment_audit_contract"] == {
        "version": "jue_wiki_decision_adjustment_audit_contract_v1",
        "status": "repair_required",
        "adjustment_count": 1,
        "actions": ["audit_preferred_risk_posture_before_shift"],
        "target_risk_postures": ["repair_probe"],
        "required_review": [
            "verify why prior shift_to_preferred_risk_posture underperformed",
            "compare live quote, account state, risk gate, and fresh evidence before adopting target risk posture",
        ],
        "accepted_resolutions": [
            "create a smaller repair probe or waiting block",
            "reject the shift and create a wiki repair note",
        ],
        "hard_blocker": False,
        "safety_gates_still_override": True,
        "audit_policies": [
            {
                "action": "repair_audit_contract_before_reuse",
                "reason": "prior_audit_contract_degraded",
                "target_risk_posture": "repair_probe",
                "hard_blocker": False,
            }
        ],
        "audit_effectiveness": [
            {
                "action": "audit_preferred_risk_posture_before_shift",
                "target_risk_posture": "repair_probe",
                "sample_count": 4,
                "status": "degraded",
                "avg_return_pct": -0.8,
            }
        ],
    }
    assert links[0]["metadata"]["decision_inputs"] == [
        "account",
        "opportunity_research_brief",
        "jue_wiki_decision_adjustments",
        "jue_wiki_decision_adjustment_audit_contract",
    ]
    assert links[0]["metadata"]["opportunity_research_brief"]["counts"] == {
        "pre_surge": 1,
        "block": 0,
        "daily_discovery": 0,
        "aggressive": 1,
    }
    assert links[0]["metadata"]["opportunity_research_brief"]["top_symbols"] == [
        "005930",
        "000660",
    ]
    assert links[0]["metadata"]["proactive_decision_pressure"] == {
        "status": "action_required",
        "pressure_level": "high",
        "zero_action_streak": 2,
        "candidate_count": 3,
        "strong_candidate_count": 1,
        "top_symbols": ["005930", "000660"],
        "required_resolution": "resolve at least one candidate",
    }
    assert links[0]["metadata"]["execution_gate"] == {
        "status": "ok",
        "execution_mode": "live",
        "execute_orders": True,
        "kill_switch_enabled": False,
        "market_session": "regular",
        "new_entry_allowed_by_session": True,
        "cash_available": {
            "cash_krw": 1_000_000,
            "orderable_cash_krw": 800_000,
            "total_equity_usdt": 999,
        },
        "active_block_count": 2,
        "waiting_entry_block_count": 1,
        "pending_order_block_count": 0,
        "duplicate_order_guard_status": "ok",
    }


def test_project_decision_links_preserves_zero_trust_profile_count(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-zero-trust",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.005930"],
                            "trust_profile_effectiveness": {
                                "target_scope": "kis",
                                "trust_profile_count": 0,
                                "trust_profiles": [
                                    {
                                        "decision_scope": "kis",
                                        "authority": "supporting_evidence",
                                        "sample_count": 12,
                                        "status": "active",
                                    }
                                ],
                            },
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-zero-trust")
    trust_effectiveness = links[0]["metadata"]["jue_wiki_trust_profile_effectiveness"]

    assert trust_effectiveness["trust_profile_count"] == 0
    assert "trust_profiles" not in trust_effectiveness


def test_project_decision_links_tracks_requested_symbol_memory_cards(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-summary",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.risk.trading_validation"],
                            "requested_symbol_summary_page_ids": [
                                "kis.symbol.005930"
                            ],
                            "applied_page_ids": [
                                "kis.risk.trading_validation",
                                "kis.symbol.005930",
                            ],
                        },
                        "jue_wiki": {
                            "selection_run_id": "selection:kis-summary",
                            "prompt_mode": "assist",
                            "pages": [
                                {
                                    "page_id": "kis.risk.trading_validation",
                                    "page_type": "risk",
                                    "confidence": 0.8,
                                }
                            ],
                            "requested_symbol_summaries": [
                                {
                                    "page_id": "kis.symbol.005930",
                                    "page_type": "symbol",
                                    "symbol": "005930",
                                    "symbols": ["005930"],
                                    "summary": "삼성전자 중기 메모리",
                                    "confidence": 0.73,
                                    "memory_card": {
                                        "stance": "저점 대기",
                                        "lessons": ["추격보다 눌림"],
                                    },
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    result = service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-summary")

    assert result["status"] == "ok"
    assert links[0]["selected_pages"] == [
        "kis.risk.trading_validation",
        "kis.symbol.005930",
    ]
    summary = links[0]["metadata"]["selected_wiki_pages"]
    assert summary["page_count"] == 2
    assert summary["pages"][1] == {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "confidence": 0.73,
        "symbols": ["005930"],
        "applied_via": "requested_symbol_summary",
        "prompt_summary_present": True,
        "memory_card_keys": ["lessons", "stance"],
    }


def test_project_decision_links_summarizes_selected_wiki_page_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-quality",
                            "prompt_mode": "assist",
                            "selected_page_ids": [
                                "kis.research.repair_queue",
                                "kis.symbol.245450",
                            ],
                        },
                        "jue_wiki": {
                            "selection_run_id": "selection:kis-quality",
                            "prompt_mode": "assist",
                            "pages": [
                                {
                                    "page_id": "kis.research.repair_queue",
                                    "page_type": "research",
                                    "confidence": 0.91,
                                    "symbols": ["245450"],
                                    "quality_warnings": [
                                        "financials_missing",
                                        "valuation_aging_gt_7d",
                                    ],
                                    "source_refs": [
                                        {
                                            "source_type": "wiki_repair_queue",
                                            "status": "scheduled",
                                            "action_type": "refresh_symbol_financials",
                                            "symbols": ["245450"],
                                        }
                                    ],
                                },
                                {
                                    "page_id": "kis.symbol.245450",
                                    "page_type": "symbol",
                                    "confidence": 0.72,
                                    "symbols": ["245450"],
                                    "quality_status": "partial",
                                    "quality_warnings": ["valuation_metrics_sparse"],
                                },
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-quality")

    summary = links[0]["metadata"]["selected_wiki_pages"]
    assert summary["page_count"] == 2
    assert summary["repair_queue_count"] == 1
    assert summary["quality_warning_count"] == 3
    assert summary["warning_counts"] == {
        "financials_missing": 1,
        "valuation_aging_gt_7d": 1,
        "valuation_metrics_sparse": 1,
    }
    assert summary["pages"][0] == {
        "page_id": "kis.research.repair_queue",
        "page_type": "research",
        "confidence": 0.91,
        "symbols": ["245450"],
        "quality_warnings": ["financials_missing", "valuation_aging_gt_7d"],
        "repair_targets": [
            {
                "page_id": "kis.research.repair_queue",
                "symbol": "245450",
                "recommended_action": "refresh_symbol_financials",
            }
        ],
        "repair_queue": {
            "status": "scheduled",
            "action_type": "refresh_symbol_financials",
            "symbols": ["245450"],
        },
        "usage_guidance": {
            "trust_level": "low",
            "risk_posture": "supporting_cross_check",
            "decision_use": (
                "use this page as supporting context after resolving "
                "candidate-level evidence gaps"
            ),
            "allowed_uses": [
                "supporting_context",
                "follow_up_research",
                "target_stop_context",
                "risk_note_context",
            ],
            "required_cross_checks": [
                "live_quote",
                "fresh_financials_or_valuation_cross_check",
                "fresh_valuation_cross_check",
            ],
            "hard_blocker": False,
        },
    }


def test_prompt_selected_wiki_pages_summary_lifts_usage_guidance_repair_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    summary = service._prompt_selected_wiki_pages_summary(
        {
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.research.repair_queue",
                        "page_type": "research",
                        "confidence": 0.84,
                        "symbols": ["005930"],
                        "source_refs": [
                            {
                                "source_type": "wiki_repair_queue",
                                "source_id": "repair:usage-guidance",
                                "status": "scheduled",
                                "action_type": "repair_usage_guidance_contract",
                                "symbols": ["005930"],
                                "quality_warnings": ["usage_guidance_degraded"],
                                "repair_action": (
                                    "repair degraded wiki usage guidance before "
                                    "reusing this page usage pattern"
                                ),
                            }
                        ],
                    }
                ]
            }
        },
        ["kis.research.repair_queue"],
    )

    assert summary["repair_queue_count"] == 1
    assert summary["repair_action_types"] == ["repair_usage_guidance_contract"]
    assert summary["repair_decision_uses"] == [
        "usage_guidance_effectiveness_repair"
    ]
    assert summary["repair_quality_warnings"] == ["usage_guidance_degraded"]
    assert summary["pages"][0]["repair_queue"]["decision_use"] == (
        "usage_guidance_effectiveness_repair"
    )


def test_prompt_selected_wiki_pages_summary_cleans_semantic_null_warnings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    summary = service._prompt_selected_wiki_pages_summary(
        {
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.research.repair_queue",
                        "page_type": "research",
                        "quality_warnings": ["None", "price_missing"],
                        "source_refs": [
                            {
                                "source_type": "wiki_repair_queue",
                                "source_id": "repair:null",
                                "status": "scheduled",
                                "action_type": "None",
                                "quality_warnings": ["null", "horizon_missing"],
                                "diagnostic_reasons": [
                                    "None",
                                    "closed_block_outcomes_without_horizon:1",
                                ],
                                "repair_targets": [
                                    {
                                        "page_id": "None",
                                        "recommended_action": "null",
                                    },
                                    {
                                        "page_id": (
                                            "kis.application."
                                            "closed_block_outcomes"
                                        ),
                                        "recommended_action": (
                                            "reproject_closed_block_outcomes_"
                                            "with_block_horizon"
                                        ),
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        },
        ["kis.research.repair_queue"],
    )

    assert summary["quality_warning_count"] == 2
    assert summary["warning_counts"] == {
        "horizon_missing": 1,
        "price_missing": 1,
    }
    assert summary["repair_quality_warnings"] == ["horizon_missing"]
    assert summary["repair_diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:1"
    ]
    assert "repair_action_types" not in summary
    assert summary["pages"][0]["quality_warnings"] == [
        "price_missing",
        "horizon_missing",
    ]
    assert summary["pages"][0]["repair_queue"]["quality_warnings"] == [
        "horizon_missing"
    ]
    assert summary["pages"][0]["repair_queue"]["diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:1"
    ]


def test_prompt_selected_wiki_pages_summary_uses_horizon_specific_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    metric_page_id = JueWikiApplicationService._usage_guidance_page_id(
        category="risk_posture",
        value="supporting_cross_check",
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": metric_page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "short",
            "sample_count": 4,
            "win_rate": 0.0,
            "expectancy": -0.03,
            "avg_return_pct": -0.03,
            "median_mae_pct": -0.04,
            "drawdown_pressure": 0.04,
            "helpful_score": -4.0,
            "confidence": 1.0,
            "status": "degraded",
            "reasons": ["short-horizon usage degraded"],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": metric_page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 4,
            "win_rate": 0.75,
            "expectancy": 0.04,
            "avg_return_pct": 0.04,
            "median_mae_pct": -0.01,
            "drawdown_pressure": 0.01,
            "helpful_score": 4.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["mid-horizon usage active"],
        }
    )

    summary = service._prompt_selected_wiki_pages_summary(
        {
            "horizon": "mid",
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "confidence": 0.81,
                        "symbols": ["005930"],
                        "quality_status": "partial",
                        "market": "kis",
                        "side": "long",
                    }
                ]
            },
        },
        ["kis.symbol.005930"],
    )

    effectiveness = summary["pages"][0]["usage_guidance_effectiveness"]
    metric = effectiveness["metrics"][0]
    assert effectiveness["status"] == "active"
    assert metric["status"] == "active"
    assert metric["horizon"] == "mid"
    assert metric["reasons"] == ["mid-horizon usage active"]


def test_prompt_selected_wiki_pages_summary_marks_general_effectiveness_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    metric_page_id = JueWikiApplicationService._usage_guidance_page_id(
        category="risk_posture",
        value="supporting_cross_check",
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": metric_page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "",
            "sample_count": 8,
            "win_rate": 0.75,
            "expectancy": 0.03,
            "avg_return_pct": 0.03,
            "median_mae_pct": -0.01,
            "drawdown_pressure": 0.01,
            "helpful_score": 3.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["general usage active"],
        }
    )

    summary = service._prompt_selected_wiki_pages_summary(
        {
            "horizon": "mid",
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "confidence": 0.81,
                        "symbols": ["005930"],
                        "quality_status": "partial",
                        "market": "kis",
                        "side": "long",
                    }
                ]
            },
        },
        ["kis.symbol.005930"],
    )

    metric = summary["pages"][0]["usage_guidance_effectiveness"]["metrics"][0]
    assert summary["effectiveness_fallback_counts"] == {
        "general_horizon_metric": 1
    }
    assert summary["effectiveness_fallback_page_ids"] == ["kis.symbol.005930"]
    assert summary["effectiveness_fallback_symbols"] == ["005930"]
    assert summary["effectiveness_fallback_requested_horizons"] == ["mid"]
    assert summary["effectiveness_fallback_markets"] == ["kis"]
    assert summary["effectiveness_fallback_sides"] == ["long"]
    assert metric["status"] == "active"
    assert metric["fallback_reason"] == "general_horizon_metric"
    assert metric["requested_horizons"] == ["mid"]
    assert metric["reasons"] == ["general usage active"]


def test_prompt_selected_wiki_pages_summary_cleans_selected_page_ids(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service._prompt_selected_wiki_pages_summary(
        {
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                        "confidence": 0.77,
                    },
                    {
                        "page_id": "kis.playbook.reflection_lessons",
                        "page_type": "playbook",
                        "confidence": 0.66,
                    },
                ]
            }
        },
        [
            None,
            "",
            "null",
            "None",
            "kis.symbol.005930",
            "kis.symbol.005930",
            "kis.playbook.reflection_lessons",
        ],
    )

    assert summary["page_count"] == 2
    assert [row["page_id"] for row in summary["pages"]] == [
        "kis.symbol.005930",
        "kis.playbook.reflection_lessons",
    ]


def test_prompt_selected_wiki_pages_summary_extracts_fallback_symbol_from_page_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    metric_page_id = JueWikiApplicationService._usage_guidance_page_id(
        category="risk_posture",
        value="supporting_cross_check",
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": metric_page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "",
            "sample_count": 4,
            "win_rate": 0.5,
            "expectancy": 0.01,
            "avg_return_pct": 0.01,
            "median_mae_pct": -0.01,
            "drawdown_pressure": 0.02,
            "helpful_score": 0.5,
            "confidence": 0.7,
            "status": "probe",
            "reasons": ["general page-id symbol fallback"],
        }
    )

    summary = service._prompt_selected_wiki_pages_summary(
        {
            "horizon": "mid",
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "quality_status": "partial",
                        "market": "kis",
                        "side": "long",
                    }
                ]
            },
        },
        ["kis.symbol.005930"],
    )

    assert summary["effectiveness_fallback_symbols"] == ["005930"]
    assert summary["pages"][0]["symbols"] == ["005930"]
    assert summary["effectiveness_fallback_page_symbols"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbols": ["005930"],
        }
    ]


def test_prompt_selected_wiki_pages_summary_preserves_fallback_page_symbols(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    metric_page_id = JueWikiApplicationService._usage_guidance_page_id(
        category="risk_posture",
        value="supporting_cross_check",
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": metric_page_id,
            "decision_scope": "binance",
            "venue": "binance",
            "horizon": "",
            "sample_count": 5,
            "win_rate": 0.4,
            "expectancy": -0.01,
            "avg_return_pct": -0.01,
            "median_mae_pct": -0.03,
            "drawdown_pressure": 0.03,
            "helpful_score": -1.0,
            "confidence": 0.8,
            "status": "degraded",
            "reasons": ["general playbook weak"],
        }
    )

    summary = service._prompt_selected_wiki_pages_summary(
        {
            "horizon": "intraday",
            "jue_wiki": {
                "pages": [
                        {
                            "page_id": "binance.playbook.pullback_reclaim",
                            "page_type": "playbook",
                            "symbols": ["NEARUSDT"],
                            "quality_status": "partial",
                            "market": "futures",
                            "side": "long",
                        },
                        {
                            "page_id": "binance.playbook.breakout_retest",
                            "page_type": "playbook",
                            "symbols": ["ETHUSDT"],
                            "quality_status": "partial",
                            "market": "futures",
                            "side": "long",
                        },
                ]
            },
        },
        [
            "binance.playbook.pullback_reclaim",
            "binance.playbook.breakout_retest",
        ],
    )

    assert summary["effectiveness_fallback_symbols"] == ["NEARUSDT", "ETHUSDT"]
    assert summary["effectiveness_fallback_page_symbols"] == [
        {
            "page_id": "binance.playbook.pullback_reclaim",
            "symbols": ["NEARUSDT"],
        },
        {
            "page_id": "binance.playbook.breakout_retest",
            "symbols": ["ETHUSDT"],
        },
    ]


def test_prompt_selected_wiki_pages_summary_aggregates_horizon_repair_diagnostics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    summary = service._prompt_selected_wiki_pages_summary(
        {
            "jue_wiki": {
                "pages": [
                    {
                        "page_id": "kis.research.repair_queue",
                        "page_type": "research",
                        "confidence": 0.91,
                        "source_refs": [
                            {
                                "source_type": "wiki_repair_queue",
                                "source_id": "repair:outcome-horizon:kis",
                                "status": "scheduled",
                                "action_type": (
                                    "reproject_closed_block_outcome_horizons"
                                ),
                                "quality_warnings": [
                                    "closed_block_outcome_horizon_missing",
                                ],
                                "diagnostic_reasons": [
                                    "closed_block_outcomes_without_horizon:3",
                                ],
                                "closed_block_outcomes_without_horizon": 3,
                                "closed_block_outcomes_without_horizon_pct": 75.0,
                                "repair_targets": [
                                    {
                                        "page_id": (
                                            "kis.application.closed_block_outcomes"
                                        ),
                                        "recommended_action": (
                                            "reproject_closed_block_outcomes_"
                                            "with_block_horizon_or_lane"
                                        ),
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        },
        ["kis.research.repair_queue"],
    )

    assert summary["repair_queue_count"] == 1
    assert summary["repair_action_types"] == [
        "reproject_closed_block_outcome_horizons"
    ]
    assert summary["repair_decision_uses"] == [
        "horizon_lane_attribution_repair"
    ]
    assert summary["repair_diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:3"
    ]
    assert summary["repair_horizon_gap_total"] == 3
    assert summary["repair_horizon_gap_max_pct"] == 75.0
    assert summary["repair_targets"] == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
    ]


def test_effectiveness_context_helpers_ignore_semantic_null_values() -> None:
    dirty_effectiveness = {
        "fallback_reason": "None",
        "requested_horizon": " null ",
        "metrics": [
            {
                "fallback_reason": "null",
                "requested_horizons": ["None", " null "],
                "requested_horizon": "None",
            }
        ],
    }
    clean_effectiveness = {
        "fallback_reason": "general_horizon_metric",
        "requested_horizon": "mid",
        "metrics": [
            {
                "fallback_reason": "general_horizon_metric",
                "requested_horizons": ["mid", "long"],
            }
        ],
    }
    service = JueWikiApplicationService

    assert service._effectiveness_fallback_reasons_for_value(dirty_effectiveness) == []
    assert service._effectiveness_requested_horizons_for_value(dirty_effectiveness) == []
    assert service._effectiveness_fallback_reasons_for_value(clean_effectiveness) == [
        "general_horizon_metric"
    ]
    assert service._effectiveness_requested_horizons_for_value(clean_effectiveness) == [
        "mid",
        "long",
    ]


def test_prompt_context_horizons_ignores_semantic_null_values(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert service._prompt_context_horizons(
        {
            "horizon": "None",
            "decision_context": {"target_horizon": " null "},
            "market_context": {"selected_horizon": ""},
        }
    ) == []
    assert service._prompt_context_horizons(
        {
            "horizon": "mid",
            "decision_context": {"target_horizon": "long"},
            "market_context": {"selected_horizon": "mid"},
        }
    ) == ["mid", "long"]


def test_effectiveness_status_and_attention_helpers_ignore_semantic_null_values() -> None:
    service = JueWikiApplicationService
    dirty_effectiveness = {
        "status": "None",
        "metrics": [
            {
                "page_id": "None",
                "source_id": "null",
                "status": "null",
                "warning": " ",
            }
        ],
    }
    clean_effectiveness = {
        "status": "active",
        "metrics": [
            {
                "page_id": "kis.symbol.005930",
                "status": "degraded",
                "warning": "price_missing",
            }
        ],
    }

    assert service._selected_page_effectiveness_statuses(dirty_effectiveness) == []
    assert service._selected_page_effectiveness_statuses(clean_effectiveness) == [
        "active",
        "degraded",
    ]
    assert (
        service._selected_page_effectiveness_attention_items(
            {
                "page_id": "kis.symbol.005930",
                "usage_guidance_effectiveness": dirty_effectiveness,
            }
        )
        == []
    )
    assert service._selected_page_effectiveness_attention_items(
        {
            "page_id": "kis.symbol.005930",
            "usage_guidance_effectiveness": clean_effectiveness,
        }
    ) == [
        {
            "page_id": "kis.symbol.005930",
            "kind": "usage_guidance",
            "status": "degraded",
            "evidence_id": "kis.symbol.005930",
            "warning": "price_missing",
        }
    ]


def test_selected_wiki_pages_summary_score_values_repair_decision_uses() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "repair_queue_count": 1,
        "pages": [
            {
                "page_id": "kis.research.repair_queue",
                "page_type": "research",
                "repair_queue": {
                    "action_type": "repair_usage_guidance_contract",
                },
            }
        ],
    }
    repair_summary = {
        **base_summary,
        "repair_action_types": ["repair_usage_guidance_contract"],
        "repair_decision_uses": ["usage_guidance_effectiveness_repair"],
        "repair_quality_warnings": ["usage_guidance_degraded"],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        repair_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(base_summary)


def test_selected_wiki_pages_summary_score_ignores_invalid_repair_identifiers() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
    }
    dirty_summary = {
        **base_summary,
        "repair_action_types": ["", "None", "null"],
        "repair_decision_uses": ["", "None"],
        "repair_quality_warnings": ["null"],
        "repair_diagnostic_reasons": ["None"],
        "repair_targets": [
            None,
            "kis.repair.fake",
            {},
            {"page_id": ""},
            {"page_id": "None"},
            {"recommended_action": "null"},
        ],
    }
    clean_summary = {
        **base_summary,
        "repair_action_types": ["repair_usage_guidance_contract"],
        "repair_decision_uses": ["usage_guidance_effectiveness_repair"],
        "repair_quality_warnings": ["usage_guidance_degraded"],
        "repair_diagnostic_reasons": ["closed_block_outcomes_without_horizon:3"],
        "repair_targets": [
            {
                "page_id": "kis.application.closed_block_outcomes",
                "recommended_action": (
                    "reproject_closed_block_outcomes_with_block_horizon"
                ),
            }
        ],
    }

    base_score = JueWikiApplicationService._selected_wiki_pages_summary_score(
        base_summary
    )

    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(dirty_summary)
        == base_score
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(clean_summary)
        > base_score
    )


def test_selected_wiki_pages_summary_score_values_horizon_repair_diagnostics() -> None:
    base_horizon_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "repair_queue_count": 1,
        "repair_action_types": ["reproject_closed_block_outcome_horizons"],
        "repair_decision_uses": ["horizon_lane_attribution_repair"],
        "pages": [
            {
                "page_id": "kis.research.repair_queue",
                "page_type": "research",
                "repair_queue": {
                    "action_type": "reproject_closed_block_outcome_horizons",
                    "decision_use": "horizon_lane_attribution_repair",
                },
            }
        ],
    }
    diagnostic_horizon_summary = {
        **base_horizon_summary,
        "repair_diagnostic_reasons": [
            "closed_block_outcomes_without_horizon:3",
        ],
        "repair_targets": [
            {
                "page_id": "kis.application.closed_block_outcomes",
                "recommended_action": (
                    "reproject_closed_block_outcomes_with_block_horizon_or_lane"
                ),
            }
        ],
        "repair_horizon_gap_total": 3,
        "repair_horizon_gap_max_pct": 75.0,
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        diagnostic_horizon_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(
        base_horizon_summary
    )


def test_selected_wiki_pages_summary_score_values_effectiveness_fallbacks() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "pages": [
            {
                "page_id": "kis.symbol.005930",
                "page_type": "symbol",
                "symbols": ["005930"],
            }
        ],
    }
    fallback_summary = {
        **base_summary,
        "effectiveness_fallback_counts": {
            "general_horizon_metric": 1,
        },
        "effectiveness_fallback_page_ids": ["kis.symbol.005930"],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        fallback_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(base_summary)


def test_selected_wiki_pages_summary_score_values_fallback_lane_context() -> None:
    fallback_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "effectiveness_fallback_counts": {
            "general_horizon_metric": 1,
        },
        "effectiveness_fallback_page_ids": ["binance.symbol.NEARUSDT"],
    }
    lane_summary = {
        **fallback_summary,
        "effectiveness_fallback_symbols": ["NEARUSDT"],
        "effectiveness_fallback_markets": ["futures"],
        "effectiveness_fallback_sides": ["short"],
        "effectiveness_fallback_requested_horizons": ["intraday"],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        lane_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(
        fallback_summary
    )


def test_selected_wiki_pages_summary_score_values_fallback_symbols() -> None:
    fallback_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "effectiveness_fallback_counts": {
            "general_horizon_metric": 1,
        },
        "effectiveness_fallback_page_ids": ["binance.playbook.pullback_reclaim"],
    }
    symbol_summary = {
        **fallback_summary,
        "effectiveness_fallback_symbols": ["NEARUSDT"],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        symbol_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(
        fallback_summary
    )


def test_selected_wiki_pages_summary_score_ignores_invalid_fallback_identifiers() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
    }
    dirty_summary = {
        **base_summary,
        "effectiveness_fallback_page_ids": [
            "",
            "None",
            "null",
        ],
        "effectiveness_fallback_page_symbols": [
            None,
            "kis.playbook.fake",
            {},
            {"page_id": ""},
            {"page_id": "None", "symbols": ["005930"]},
            {"page_id": "kis.playbook.pullback", "symbols": ["정보", "kis"]},
        ],
        "effectiveness_fallback_symbols": ["정보", "kis", "binance"],
    }
    clean_summary = {
        **base_summary,
        "effectiveness_fallback_page_ids": ["kis.playbook.pullback"],
        "effectiveness_fallback_page_symbols": [
            {"page_id": "kis.playbook.pullback", "symbols": ["005930"]}
        ],
        "effectiveness_fallback_symbols": ["005930"],
    }

    base_score = JueWikiApplicationService._selected_wiki_pages_summary_score(
        base_summary
    )

    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(dirty_summary)
        == base_score
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(clean_summary)
        > base_score
    )


def test_selected_wiki_pages_summary_score_values_row_horizon_repair_diagnostics() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "repair_queue_count": 1,
        "pages": [
            {
                "page_id": "kis.research.repair_queue",
                "page_type": "research",
                "repair_queue": {
                    "action_type": "reproject_closed_block_outcome_horizons",
                    "decision_use": "horizon_lane_attribution_repair",
                },
            }
        ],
    }
    row_diagnostic_summary = {
        **base_summary,
        "pages": [
            {
                "page_id": "kis.research.repair_queue",
                "page_type": "research",
                "repair_queue": {
                    "action_type": "reproject_closed_block_outcome_horizons",
                    "decision_use": "horizon_lane_attribution_repair",
                    "diagnostic_reasons": [
                        "closed_block_outcomes_without_horizon:2",
                    ],
                    "repair_targets": [
                        {
                            "page_id": "kis.application.closed_block_outcomes",
                            "recommended_action": (
                                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
                            ),
                        }
                    ],
                    "closed_block_outcomes_without_horizon": 2,
                    "closed_block_outcomes_without_horizon_pct": 50.0,
                },
            }
        ],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        row_diagnostic_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(base_summary)


def test_selected_wiki_pages_summary_score_values_quality_warning_source_effectiveness() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "pages": [
            {
                "page_id": "kis.symbol.277810",
                "page_type": "symbol",
                "symbols": ["277810"],
            }
        ],
    }
    effectiveness_summary = {
        **base_summary,
        "pages": [
            {
                "page_id": "kis.symbol.277810",
                "page_type": "symbol",
                "symbols": ["277810"],
                "quality_warning_source_effectiveness": {
                    "status": "degraded",
                    "metrics": [
                        {
                            "page_id": "kis.symbol.277810",
                            "status": "degraded",
                            "sample_count": 3,
                            "expectancy": -1.4,
                        }
                    ],
                },
            }
        ],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        effectiveness_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(base_summary)


def test_selected_wiki_pages_summary_score_values_effectiveness_status_counts() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "pages": [{"page_id": "kis.symbol.005930", "page_type": "symbol"}],
    }
    effectiveness_summary = {
        **base_summary,
        "usage_guidance_effectiveness_status_counts": {"active": 1},
        "memory_card_quality_effectiveness_status_counts": {"probe": 1},
        "quality_warning_source_effectiveness_status_counts": {"degraded": 1},
        "quality_warning_effectiveness_status_counts": {"degraded": 1},
        "effectiveness_attention_page_ids": ["kis.symbol.005930"],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        effectiveness_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(base_summary)


def test_selected_wiki_pages_summary_score_ignores_semantic_null_context_counts() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
    }
    dirty_summary = {
        **base_summary,
        "usage_guidance_effectiveness_status_counts": {
            "None": 8,
            " null ": 4,
        },
        "memory_card_quality_effectiveness_status_counts": {"null": 3},
        "effectiveness_fallback_counts": {"None": 5, " null ": 2},
        "effectiveness_fallback_markets": ["None", " null "],
        "effectiveness_fallback_sides": ["None"],
        "effectiveness_fallback_requested_horizons": ["null"],
        "effectiveness_attention_items": [
            {
                "page_id": "kis.symbol.005930",
                "kind": "None",
                "status": "null",
                "warning": " ",
            }
        ],
    }
    clean_summary = {
        **base_summary,
        "usage_guidance_effectiveness_status_counts": {"active": 1},
        "effectiveness_fallback_counts": {"general_horizon_metric": 1},
        "effectiveness_fallback_markets": ["futures"],
        "effectiveness_fallback_sides": ["long"],
        "effectiveness_fallback_requested_horizons": ["intraday"],
        "effectiveness_attention_items": [
            {
                "page_id": "kis.symbol.005930",
                "kind": "usage_guidance",
                "status": "active",
            }
        ],
    }

    base_score = JueWikiApplicationService._selected_wiki_pages_summary_score(
        base_summary
    )

    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(dirty_summary)
        == base_score
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(clean_summary)
        > base_score
    )


def test_selected_wiki_pages_effectiveness_score_ignores_semantic_null_metrics() -> None:
    dirty_dict_effectiveness = {
        "status": "None",
        "metrics": [
            {
                "page_id": "null",
                "status": "None",
                "warning": " ",
                "sample_count": 99,
            }
        ],
    }
    clean_dict_effectiveness = {
        "status": "degraded",
        "metrics": [
            {
                "page_id": "kis.symbol.005930",
                "status": "degraded",
                "sample_count": 3,
            }
        ],
    }
    dirty_list_effectiveness = [
        {
            "page_id": "None",
            "status": "null",
            "warning": " ",
            "sample_count": 99,
        }
    ]
    clean_list_effectiveness = [
        {
            "page_id": "kis.symbol.005930",
            "status": "degraded",
            "sample_count": 3,
        }
    ]

    assert (
        JueWikiApplicationService._selected_wiki_pages_effectiveness_score(
            dirty_dict_effectiveness
        )
        == 0
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_effectiveness_score(
            dirty_list_effectiveness
        )
        == 0
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_effectiveness_score(
            clean_dict_effectiveness
        )
        > 0
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_effectiveness_score(
            clean_list_effectiveness
        )
        > 0
    )


def test_selected_wiki_pages_summary_score_ignores_invalid_attention_identifiers() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
    }
    dirty_summary = {
        **base_summary,
        "effectiveness_attention_page_ids": ["", "None", "null"],
        "effectiveness_attention_items": [
            None,
            "kis.symbol.fake",
            {},
            {"page_id": ""},
            {"page_id": "None", "kind": "usage_guidance", "status": "active"},
            {"page_id": "null", "kind": "quality_warning", "warning": "stale"},
        ],
    }
    clean_summary = {
        **base_summary,
        "effectiveness_attention_page_ids": ["kis.symbol.005930"],
        "effectiveness_attention_items": [
            {
                "page_id": "kis.symbol.005930",
                "kind": "usage_guidance",
                "status": "active",
            }
        ],
    }

    base_score = JueWikiApplicationService._selected_wiki_pages_summary_score(
        base_summary
    )

    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(dirty_summary)
        == base_score
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(clean_summary)
        > base_score
    )


def test_selected_wiki_pages_summary_score_ignores_invalid_page_rows() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
    }
    dirty_summary = {
        **base_summary,
        "pages": [
            None,
            "kis.symbol.fake",
            {},
            {"page_id": ""},
            {
                "page_id": "None",
                "page_type": "symbol",
                "confidence": 0.9,
                "symbols": ["정보", "kis"],
                "quality_warnings": ["ghost_warning"],
            },
            {
                "page_id": "null",
                "page_type": "symbol",
                "symbols": ["binance"],
            },
        ],
    }
    clean_summary = {
        **base_summary,
        "pages": [
            {
                "page_id": "kis.symbol.005930",
                "page_type": "symbol",
                "confidence": 0.7,
                "symbols": ["정보", "005930", "kis"],
            }
        ],
    }

    base_score = JueWikiApplicationService._selected_wiki_pages_summary_score(
        base_summary
    )

    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(dirty_summary)
        == base_score
    )
    assert (
        JueWikiApplicationService._selected_wiki_pages_summary_score(clean_summary)
        > base_score
    )


def test_selected_wiki_pages_summary_score_values_row_effectiveness_metadata() -> None:
    base_summary = {
        "page_count": 1,
        "reported_page_count": 1,
        "pages": [{"page_id": "kis.symbol.005930", "page_type": "symbol"}],
    }
    row_effectiveness_summary = {
        **base_summary,
        "pages": [
            {
                "page_id": "kis.symbol.005930",
                "page_type": "symbol",
                "usage_guidance_effectiveness": {
                    "status": "active",
                    "metrics": [
                        {
                            "page_id": (
                                "usage_guidance.risk_posture."
                                "patient_waiting_entry"
                            ),
                            "status": "active",
                            "sample_count": 7,
                        }
                    ],
                },
                "memory_card_quality_effectiveness": {
                    "status": "probe",
                    "metrics": [
                        {
                            "page_id": "memory_card_quality.missing_field.lessons",
                            "status": "probe",
                            "sample_count": 2,
                        }
                    ],
                },
                "quality_warning_effectiveness": [
                    {
                        "warning": "valuation_stale_gt_30d",
                        "status": "degraded",
                        "sample_count": 4,
                    }
                ],
            }
        ],
    }

    assert JueWikiApplicationService._selected_wiki_pages_summary_score(
        row_effectiveness_summary
    ) > JueWikiApplicationService._selected_wiki_pages_summary_score(base_summary)


def test_preserve_richer_selected_wiki_pages_keeps_effectiveness_counts() -> None:
    new_metadata_json = json.dumps(
        {
            "selected_wiki_pages": {
                "page_count": 1,
                "reported_page_count": 1,
                "pages": [{"page_id": "kis.symbol.005930", "page_type": "symbol"}],
            }
        }
    )
    existing_metadata = {
        "selected_wiki_pages": {
            "page_count": 1,
            "reported_page_count": 1,
            "usage_guidance_effectiveness_status_counts": {"active": 1},
            "memory_card_quality_effectiveness_status_counts": {"probe": 1},
            "quality_warning_source_effectiveness_status_counts": {"degraded": 1},
            "quality_warning_effectiveness_status_counts": {"degraded": 1},
            "effectiveness_attention_page_ids": ["kis.symbol.005930"],
            "pages": [{"page_id": "kis.symbol.005930", "page_type": "symbol"}],
        },
        "selected_wiki_pages_backfilled_at": "2026-06-30T00:00:00+00:00",
    }

    preserved = json.loads(
        JueWikiApplicationService._preserve_richer_selected_wiki_pages(
            new_metadata_json=new_metadata_json,
            existing_metadata=existing_metadata,
        )
    )

    assert preserved["selected_wiki_pages"][
        "usage_guidance_effectiveness_status_counts"
    ] == {"active": 1}
    assert preserved["selected_wiki_pages"][
        "effectiveness_attention_page_ids"
    ] == ["kis.symbol.005930"]
    assert (
        preserved["selected_wiki_pages_backfilled_at"]
        == "2026-06-30T00:00:00+00:00"
    )


def test_preserve_richer_selected_wiki_pages_merges_complementary_metadata() -> None:
    new_metadata_json = json.dumps(
        {
            "selected_wiki_pages": {
                "page_count": 1,
                "reported_page_count": 1,
                "repair_queue_count": 1,
                "repair_action_types": ["repair_usage_guidance_contract"],
                "repair_decision_uses": ["usage_guidance_effectiveness_repair"],
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "repair_queue": {
                            "action_type": "repair_usage_guidance_contract",
                            "decision_use": "usage_guidance_effectiveness_repair",
                        },
                    }
                ],
            }
        }
    )
    existing_metadata = {
        "selected_wiki_pages": {
            "page_count": 1,
            "reported_page_count": 1,
            "usage_guidance_effectiveness_status_counts": {"active": 1},
            "quality_warning_effectiveness_status_counts": {"degraded": 1},
            "effectiveness_attention_page_ids": ["kis.symbol.005930"],
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "page_type": "symbol",
                    "usage_guidance_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "patient_waiting_entry"
                                ),
                                "status": "active",
                            }
                        ],
                    },
                }
            ],
        }
    }

    preserved = json.loads(
        JueWikiApplicationService._preserve_richer_selected_wiki_pages(
            new_metadata_json=new_metadata_json,
            existing_metadata=existing_metadata,
        )
    )

    selected = preserved["selected_wiki_pages"]
    assert selected["repair_queue_count"] == 1
    assert selected["usage_guidance_effectiveness_status_counts"] == {"active": 1}
    assert selected["quality_warning_effectiveness_status_counts"] == {
        "degraded": 1
    }
    assert selected["effectiveness_attention_page_ids"] == ["kis.symbol.005930"]
    page = selected["pages"][0]
    assert page["repair_queue"]["action_type"] == "repair_usage_guidance_contract"
    assert page["usage_guidance_effectiveness"]["status"] == "active"


def test_preserve_richer_selected_wiki_pages_merges_fallback_page_symbols() -> None:
    new_metadata_json = json.dumps(
        {
            "selected_wiki_pages": {
                "page_count": 2,
                "reported_page_count": 2,
                "effectiveness_fallback_counts": {
                    "general_horizon_metric": 2,
                },
                "effectiveness_fallback_page_ids": [
                    "binance.playbook.pullback_reclaim",
                    "binance.playbook.breakout_retest",
                ],
                "effectiveness_fallback_symbols": ["NEARUSDT", "ETHUSDT"],
                "effectiveness_fallback_page_symbols": [
                    {
                        "page_id": "binance.playbook.pullback_reclaim",
                        "symbols": ["NEARUSDT"],
                    },
                    {
                        "page_id": "binance.playbook.breakout_retest",
                        "symbols": ["ETHUSDT"],
                    },
                ],
                "effectiveness_fallback_requested_horizons": ["intraday"],
            }
        }
    )
    existing_metadata = {
        "selected_wiki_pages": {
            "page_count": 6,
            "reported_page_count": 6,
            "quality_warning_count": 12,
            "usage_guidance_effectiveness_status_counts": {"active": 4},
            "effectiveness_attention_page_ids": [
                f"binance.symbol.TEST{i}USDT" for i in range(6)
            ],
            "pages": [
                {
                    "page_id": f"binance.symbol.TEST{i}USDT",
                    "page_type": "symbol",
                }
                for i in range(6)
            ],
        }
    }

    preserved = json.loads(
        JueWikiApplicationService._preserve_richer_selected_wiki_pages(
            new_metadata_json=new_metadata_json,
            existing_metadata=existing_metadata,
        )
    )

    selected = preserved["selected_wiki_pages"]
    assert selected["quality_warning_count"] == 12
    assert selected["effectiveness_fallback_page_symbols"] == [
        {
            "page_id": "binance.playbook.pullback_reclaim",
            "symbols": ["NEARUSDT"],
        },
        {
            "page_id": "binance.playbook.breakout_retest",
            "symbols": ["ETHUSDT"],
        },
    ]
    assert selected["effectiveness_fallback_symbols"] == ["NEARUSDT", "ETHUSDT"]
    assert selected["effectiveness_fallback_requested_horizons"] == ["intraday"]


def test_merge_selected_wiki_pages_summary_combines_fallback_page_symbol_rows() -> None:
    merged = JueWikiApplicationService._merge_selected_wiki_pages_summary(
        new_summary={
            "effectiveness_fallback_page_symbols": [
                {
                    "page_id": "binance.playbook.pullback_reclaim",
                    "symbols": ["NEARUSDT"],
                }
            ],
        },
        existing_summary={
            "effectiveness_fallback_page_symbols": [
                {
                    "page_id": "binance.playbook.pullback_reclaim",
                    "symbols": ["ETHUSDT", "NEARUSDT"],
                },
                {
                    "page_id": "binance.playbook.breakout_retest",
                    "symbols": ["SOLUSDT"],
                },
            ],
        },
    )

    assert merged["effectiveness_fallback_page_symbols"] == [
        {
            "page_id": "binance.playbook.pullback_reclaim",
            "symbols": ["NEARUSDT", "ETHUSDT"],
        },
        {
            "page_id": "binance.playbook.breakout_retest",
            "symbols": ["SOLUSDT"],
        },
    ]
    assert JueWikiApplicationService._summary_fallback_symbols_by_page_id(
        merged
    ) == {
        "binance.playbook.pullback_reclaim": ["NEARUSDT", "ETHUSDT"],
        "binance.playbook.breakout_retest": ["SOLUSDT"],
    }


def test_merge_selected_wiki_pages_summary_drops_invalid_fallback_page_symbol_rows() -> None:
    merged = JueWikiApplicationService._merge_selected_wiki_pages_summary(
        new_summary={
            "effectiveness_fallback_page_symbols": [
                None,
                "kis.playbook.fake",
                {},
                {"page_id": ""},
                {"page_id": "None", "symbols": ["005930"]},
                {"page_id": "null", "symbols": ["000660"]},
                {
                    "page_id": "kis.playbook.pullback",
                    "symbols": ["005930", "정보", "kis", "005930"],
                    "market": "kis",
                },
            ]
        },
        existing_summary={
            "effectiveness_fallback_page_symbols": [
                {
                    "page_id": "kis.playbook.pullback",
                    "symbols": ["000660", "005930"],
                    "side": "long",
                }
            ]
        },
    )

    assert merged["effectiveness_fallback_page_symbols"] == [
        {
            "page_id": "kis.playbook.pullback",
            "market": "kis",
            "side": "long",
            "symbols": ["005930", "000660"],
        }
    ]


def test_summary_fallback_symbols_by_page_id_cleans_invalid_rows_and_symbols() -> None:
    assert JueWikiApplicationService._summary_fallback_symbols_by_page_id(
        {
            "effectiveness_fallback_page_symbols": [
                None,
                "binance.playbook.fake",
                {},
                {"page_id": ""},
                {"page_id": "None", "symbols": ["BTCUSDT"]},
                {"page_id": "null", "symbols": ["ETHUSDT"]},
                {
                    "page_id": "binance.playbook.pullback_reclaim",
                    "symbols": ["nearusdt", "정보", "binance", "NEARUSDT"],
                },
            ]
        }
    ) == {
        "binance.playbook.pullback_reclaim": ["NEARUSDT"],
    }


def test_merge_selected_wiki_pages_summary_drops_invalid_page_rows() -> None:
    merged = JueWikiApplicationService._merge_selected_wiki_pages_summary(
        new_summary={
            "pages": [
                None,
                "kis.symbol.fake",
                {},
                {"page_id": ""},
                {"page_id": "None", "confidence": 0.9},
                {"page_id": "kis.symbol.005930", "confidence": 0.7},
            ]
        },
        existing_summary={
            "pages": [
                {"page_id": "kis.symbol.005930", "symbols": ["005930"]},
                {"page_id": "null", "confidence": 0.1},
                {
                    "page_id": "kis.playbook.reflection_lessons",
                    "page_type": "playbook",
                },
            ]
        },
    )

    assert merged["pages"] == [
        {
            "page_id": "kis.symbol.005930",
            "confidence": 0.7,
            "symbols": ["005930"],
        },
        {
            "page_id": "kis.playbook.reflection_lessons",
            "page_type": "playbook",
        },
    ]


def test_merge_selected_wiki_pages_summary_drops_none_null_list_strings() -> None:
    merged = JueWikiApplicationService._merge_selected_wiki_pages_summary(
        new_summary={
            "repair_action_types": [
                "None",
                "repair_usage_guidance_contract",
                "repair_usage_guidance_contract",
            ],
            "repair_quality_warnings": [" null ", "missing_symbol_context"],
        },
        existing_summary={
            "repair_action_types": [
                "null",
                "repair_quality_pressure",
            ],
            "repair_quality_warnings": [
                "",
                "None",
                "missing_symbol_context",
                "missing_horizon_context",
            ],
        },
    )

    assert merged["repair_action_types"] == [
        "repair_usage_guidance_contract",
        "repair_quality_pressure",
    ]
    assert merged["repair_quality_warnings"] == [
        "missing_symbol_context",
        "missing_horizon_context",
    ]


def test_merge_selected_wiki_pages_summary_cleans_page_row_semantic_null_fields() -> None:
    merged = JueWikiApplicationService._merge_selected_wiki_pages_summary(
        new_summary={
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "quality_status": "None",
                    "decision_use": " null ",
                    "symbols": ["005930", "None"],
                    "usage_guidance": {
                        "trust_level": "null",
                        "risk_posture": "standard_block_design",
                        "required_cross_checks": ["", "None", "live_quote"],
                    },
                }
            ],
        },
        existing_summary={
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "quality_warnings": ["None", "price_missing"],
                    "usage_guidance": {
                        "allowed_uses": ["None", "waiting_block"],
                        "hard_blocker": False,
                    },
                }
            ],
        },
    )

    assert merged["pages"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbols": ["005930"],
            "usage_guidance": {
                "risk_posture": "standard_block_design",
                "required_cross_checks": ["live_quote"],
                "allowed_uses": ["waiting_block"],
                "hard_blocker": False,
            },
            "quality_warnings": ["price_missing"],
        }
    ]


def test_merge_selected_wiki_pages_summary_cleans_count_dict_null_keys() -> None:
    merged = JueWikiApplicationService._merge_selected_wiki_pages_summary(
        new_summary={
            "quality_warning_counts": {
                None: 5,
                "": 9,
                "None": 7,
                " null ": 8,
                "price_missing": 2,
                "sector_gap": "None",
                "fresh_quote_required": 0,
            },
        },
        existing_summary={
            "quality_warning_counts": {
                "price_missing": 1,
                "valuation_stale": 3,
                "null": 4,
                "liquidity_gap": "null",
            },
        },
    )

    assert merged["quality_warning_counts"] == {
        "price_missing": 2,
        "fresh_quote_required": 0,
        "valuation_stale": 3,
    }


def test_selected_wiki_pages_material_checks_ignore_semantic_null_values() -> None:
    assert not JueWikiApplicationService._selected_wiki_pages_has_material_value(
        "None"
    )
    assert not JueWikiApplicationService._selected_wiki_pages_has_material_value(
        ["", " null ", "None"]
    )
    assert not JueWikiApplicationService._selected_wiki_pages_has_material_value(
        {
            "None": 3,
            "quality_warnings": ["None", "null"],
            "usage_guidance": {"trust_level": "null"},
        }
    )
    assert not JueWikiApplicationService._selected_wiki_pages_has_added_material(
        ["None", " null "],
        ["price_missing"],
    )
    assert not JueWikiApplicationService._selected_wiki_pages_has_added_material(
        {
            "None": 7,
            "quality_warnings": ["None"],
            "usage_guidance": {"trust_level": "null"},
        },
        {
            "quality_warnings": ["price_missing"],
            "usage_guidance": {"risk_posture": "waiting_block"},
        },
    )


def test_selected_wiki_pages_complementary_metadata_ignores_invalid_page_ids() -> None:
    assert not JueWikiApplicationService._selected_wiki_pages_has_complementary_metadata(
        new_summary={
            "pages": [
                {
                    "page_id": "None",
                    "usage_guidance": {"risk_posture": "standard_block_design"},
                },
                {
                    "page_id": " null ",
                    "memory_card_quality": {"status": "active"},
                },
                {
                    "page_id": "",
                    "evidence_quality": {"status_counts": {"strong": 1}},
                },
            ],
        },
        existing_summary={
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "usage_guidance": {"risk_posture": "waiting_block"},
                }
            ],
        },
    )


def test_merge_selected_wiki_pages_summary_cleans_new_summary_without_existing_merge() -> None:
    merged = JueWikiApplicationService._merge_selected_wiki_pages_summary(
        new_summary={
            "repair_action_types": ["None", "repair_quality_pressure"],
            "quality_warning_counts": {
                "None": 7,
                "price_missing": 2,
                "fresh_quote_required": 0,
                "liquidity_gap": "null",
            },
            "pages": [
                {
                    "page_id": "None",
                    "usage_guidance": {"risk_posture": "standard_block_design"},
                },
                {
                    "page_id": "kis.symbol.005930",
                    "quality_status": "null",
                    "symbols": ["005930", "None"],
                    "usage_guidance": {
                        "trust_level": "None",
                        "risk_posture": "waiting_block",
                    },
                },
            ],
            "effectiveness_fallback_page_symbols": [
                {"page_id": "null", "symbols": ["005930"]},
                {
                    "page_id": "kis.playbook.pullback",
                    "symbols": ["005930", "None"],
                },
            ],
        },
        existing_summary={},
    )

    assert merged == {
        "repair_action_types": ["repair_quality_pressure"],
        "quality_warning_counts": {
            "price_missing": 2,
            "fresh_quote_required": 0,
        },
        "pages": [
            {
                "page_id": "kis.symbol.005930",
                "symbols": ["005930"],
                "usage_guidance": {"risk_posture": "waiting_block"},
            }
        ],
        "effectiveness_fallback_page_symbols": [
            {
                "page_id": "kis.playbook.pullback",
                "symbols": ["005930"],
            }
        ],
    }


def test_preserve_richer_selected_wiki_pages_merges_added_fallback_page_symbol() -> None:
    new_metadata_json = json.dumps(
        {
            "selected_wiki_pages": {
                "page_count": 1,
                "reported_page_count": 1,
                "effectiveness_fallback_page_symbols": [
                    {
                        "page_id": "binance.playbook.pullback_reclaim",
                        "symbols": ["ETHUSDT"],
                    }
                ],
                "effectiveness_fallback_symbols": ["ETHUSDT"],
            }
        }
    )
    existing_metadata = {
        "selected_wiki_pages": {
            "page_count": 4,
            "reported_page_count": 4,
            "quality_warning_count": 10,
            "usage_guidance_effectiveness_status_counts": {"active": 4},
            "effectiveness_fallback_page_symbols": [
                {
                    "page_id": "binance.playbook.pullback_reclaim",
                    "symbols": ["NEARUSDT"],
                }
            ],
            "effectiveness_fallback_symbols": ["NEARUSDT"],
            "pages": [
                {
                    "page_id": f"binance.symbol.TEST{i}USDT",
                    "page_type": "symbol",
                }
                for i in range(4)
            ],
        }
    }

    preserved = json.loads(
        JueWikiApplicationService._preserve_richer_selected_wiki_pages(
            new_metadata_json=new_metadata_json,
            existing_metadata=existing_metadata,
        )
    )

    selected = preserved["selected_wiki_pages"]
    assert selected["quality_warning_count"] == 10
    assert selected["effectiveness_fallback_page_symbols"] == [
        {
            "page_id": "binance.playbook.pullback_reclaim",
            "symbols": ["ETHUSDT", "NEARUSDT"],
        }
    ]
    assert selected["effectiveness_fallback_symbols"] == ["ETHUSDT", "NEARUSDT"]


def test_summary_fallback_symbols_by_page_id_combines_duplicate_rows() -> None:
    assert JueWikiApplicationService._summary_fallback_symbols_by_page_id(
        {
            "effectiveness_fallback_page_symbols": [
                {
                    "page_id": "binance.playbook.pullback_reclaim",
                    "symbols": ["NEARUSDT"],
                },
                {
                    "page_id": "binance.playbook.pullback_reclaim",
                    "symbols": ["ETHUSDT", "NEARUSDT"],
                },
            ]
        }
    ) == {
        "binance.playbook.pullback_reclaim": ["NEARUSDT", "ETHUSDT"],
    }


def test_prompt_selected_wiki_pages_summary_counts_effectiveness_statuses(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service._prompt_selected_wiki_pages_summary(
        {
            "jue_wiki": {
                "selection_run_id": "selection:effectiveness-counts",
                "pages": [
                    {
                        "page_id": "kis.symbol.005930",
                        "page_type": "symbol",
                        "symbols": ["005930"],
                        "usage_guidance": {
                            "risk_posture": "patient_waiting_entry",
                        },
                        "usage_guidance_effectiveness": {
                            "status": "active",
                            "metrics": [
                                {
                                    "page_id": (
                                        "usage_guidance.risk_posture."
                                        "patient_waiting_entry"
                                    ),
                                    "status": "active",
                                    "sample_count": 7,
                                }
                            ],
                        },
                        "memory_card_quality_effectiveness": {
                            "status": "probe",
                            "metrics": [
                                {
                                    "page_id": "memory_card_quality.missing_field.lessons",
                                    "status": "probe",
                                    "sample_count": 2,
                                }
                            ],
                        },
                        "quality_warning_source_effectiveness": {
                            "status": "degraded",
                            "metrics": [
                                {
                                    "page_id": "kis.symbol.005930",
                                    "source_type": "symbol_fundamentals",
                                    "warning": "valuation_stale_gt_30d",
                                    "status": "degraded",
                                    "sample_count": 4,
                                }
                            ],
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
            }
        },
        ["kis.symbol.005930"],
    )

    assert summary["usage_guidance_effectiveness_status_counts"] == {"active": 1}
    assert summary["memory_card_quality_effectiveness_status_counts"] == {
        "probe": 1
    }
    assert summary["quality_warning_source_effectiveness_status_counts"] == {
        "degraded": 1
    }
    assert summary["quality_warning_effectiveness_status_counts"] == {
        "degraded": 1
    }
    assert summary["effectiveness_attention_page_ids"] == ["kis.symbol.005930"]
    assert summary["effectiveness_attention_items"] == [
        {
            "page_id": "kis.symbol.005930",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.patient_waiting_entry",
        },
        {
            "page_id": "kis.symbol.005930",
            "kind": "memory_card_quality",
            "status": "probe",
            "evidence_id": "memory_card_quality.missing_field.lessons",
        },
        {
            "page_id": "kis.symbol.005930",
            "kind": "quality_warning_source",
            "status": "degraded",
            "evidence_id": "kis.symbol.005930",
            "warning": "valuation_stale_gt_30d",
        },
        {
            "page_id": "kis.symbol.005930",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "valuation_stale_gt_30d",
        },
    ]


def test_manager_repair_outcome_uses_selected_wiki_pages_repair_queue_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 77,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "repair_queue_count": 1,
                    "repair_action_types": ["repair_usage_guidance_contract"],
                    "repair_decision_uses": [
                        "usage_guidance_effectiveness_repair"
                    ],
                    "repair_quality_warnings": ["usage_guidance_degraded"],
                    "pages": [
                        {
                            "page_id": "kis.research.repair_queue",
                            "page_type": "research",
                            "symbols": ["005930"],
                            "repair_queue": {
                                "status": "scheduled",
                                "source_id": "repair:usage-guidance",
                                "action_type": "repair_usage_guidance_contract",
                                "symbols": ["005930"],
                                "quality_warnings": [
                                    "usage_guidance_degraded"
                                ],
                                "repair_action": (
                                    "repair degraded wiki usage guidance before "
                                    "reusing this page usage pattern"
                                ),
                                "decision_use": (
                                    "usage_guidance_effectiveness_repair"
                                ),
                            },
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "missed_repair_priority"
    assert outcome["evidence"]["source"] == "kis_selected_wiki_pages_repair_queue"
    assert outcome["evidence"]["repair_priority_types"] == ["repair_queue"]
    assert outcome["evidence"]["repair_action_types"] == [
        "repair_usage_guidance_contract"
    ]
    assert outcome["evidence"]["repair_decision_uses"] == [
        "usage_guidance_effectiveness_repair"
    ]
    assert outcome["evidence"]["repair_source_ids"] == ["repair:usage-guidance"]
    assert outcome["evidence"]["quality_warnings"] == [
        "usage_guidance_degraded"
    ]
    assert outcome["evidence"]["repair_horizon_gap_totals"] == []
    assert outcome["evidence"]["repair_horizon_gap_pcts"] == []


def test_manager_repair_outcome_uses_selected_wiki_memory_card_quality_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 78,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
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
                                "decision_use": (
                                    "memory_card_quality_resolution_check"
                                ),
                                "candidate_resolution_required": True,
                            },
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "missed_repair_priority"
    assert outcome["evidence"]["source"] == "kis_selected_wiki_pages_repair_queue"
    assert outcome["evidence"]["repair_priority_types"] == ["memory_card_quality"]
    assert outcome["evidence"]["repair_action_types"] == [
        "cross_check_memory_card_quality"
    ]
    assert outcome["evidence"]["repair_decision_uses"] == [
        "memory_card_quality_resolution_check"
    ]
    assert outcome["evidence"]["repair_source_ids"] == [
        "kis.symbol.005930:memory_card_quality"
    ]
    assert outcome["evidence"]["quality_warnings"] == [
        "memory_card_quality_unresolved"
    ]
    assert outcome["evidence"]["repair_missing_fields"] == [
        "durable_facts",
        "lessons",
    ]
    assert outcome["evidence"]["repair_required_checks"] == [
        "refresh_durable_facts",
        "inspect_block_lessons",
    ]
    assert outcome["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": "cross_check_memory_card_quality",
        }
    ]


def test_manager_repair_outcome_turns_selected_wiki_horizon_metric_fallback_into_repair_priority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 82,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "effectiveness_fallback_counts": {
                        "general_horizon_metric": 1,
                    },
                    "effectiveness_fallback_page_ids": ["kis.symbol.005930"],
                    "pages": [
                        {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                            "usage_guidance_effectiveness": {
                                "status": "active",
                                "metrics": [
                                    {
                                        "page_id": (
                                            "usage_guidance.risk_posture."
                                            "supporting_cross_check"
                                        ),
                                        "status": "active",
                                        "horizon": "",
                                        "requested_horizons": ["mid"],
                                        "fallback_reason": (
                                            "general_horizon_metric"
                                        ),
                                    }
                                ],
                            },
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "missed_repair_priority"
    assert outcome["evidence"]["source"] == "kis_selected_wiki_pages_repair_queue"
    assert outcome["evidence"]["repair_priority_types"] == [
        "horizon_effectiveness_fallback"
    ]
    assert outcome["evidence"]["repair_action_types"] == [
        "collect_horizon_specific_wiki_effectiveness"
    ]
    assert outcome["evidence"]["repair_decision_uses"] == [
        "horizon_specific_effectiveness_repair"
    ]
    assert outcome["evidence"]["quality_warnings"] == [
        "general_horizon_metric_fallback"
    ]
    assert outcome["evidence"]["repair_diagnostic_reasons"] == [
        "fallback_reason:general_horizon_metric",
        "requested_horizon:mid",
    ]
    assert outcome["evidence"]["horizon"] == "mid"
    assert outcome["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": (
                "collect_horizon_specific_wiki_effectiveness:mid"
            ),
        }
    ]

    link = service.record_decision_link(
        selection_run_id="selection:kis-horizon-fallback",
        manager_run_id="kis-manager-horizon-fallback",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        action="repair_queue",
        prompt_mode="assist",
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind=outcome["outcome_kind"],
        outcome_status=outcome["outcome_status"],
        return_pct=outcome["return_pct"],
        evidence=outcome["evidence"],
    )

    repair_effectiveness = service.project_repair_priority_effectiveness(
        decision_scope="kis",
        min_samples=1,
    )
    metric = repair_effectiveness["repair_priority_metrics"][0]
    assert metric["priority_type"] == "horizon_effectiveness_fallback"
    assert metric["action_type"] == "collect_horizon_specific_wiki_effectiveness"
    assert metric["horizon"] == "mid"


def test_manager_repair_outcome_preserves_selected_wiki_fallback_lane_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="binance",
        venue="binance",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 83,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "pages": [
                        {
                            "page_id": "binance.symbol.NEARUSDT",
                            "page_type": "symbol",
                            "symbols": ["NEARUSDT"],
                            "market": "futures",
                            "side": "short",
                            "usage_guidance_effectiveness": {
                                "status": "active",
                                "metrics": [
                                    {
                                        "page_id": (
                                            "usage_guidance.entry_posture."
                                            "pullback_reclaim"
                                        ),
                                        "status": "active",
                                        "horizon": "",
                                        "requested_horizons": ["intraday"],
                                        "fallback_reason": (
                                            "general_horizon_metric"
                                        ),
                                    }
                                ],
                            },
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    evidence = outcome["evidence"]
    assert evidence["repair_priority_types"] == [
        "horizon_effectiveness_fallback"
    ]
    assert evidence["market"] == "futures"
    assert evidence["side"] == "short"
    assert evidence["horizon"] == "intraday"


def test_manager_repair_outcome_preserves_selected_repair_queue_lane_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="binance",
        venue="binance",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 84,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "pages": [
                        {
                            "page_id": "binance.research.repair_queue",
                            "page_type": "research",
                            "symbols": ["NEARUSDT"],
                            "market": "futures",
                            "side": "short",
                            "horizon": "intraday",
                            "repair_queue": {
                                "status": "scheduled",
                                "source_id": "repair:near:orderbook",
                                "action_type": "refresh_orderbook_depth",
                                "decision_use": "microstructure_cross_check",
                                "quality_warnings": ["orderbook_depth_stale"],
                            },
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    evidence = outcome["evidence"]
    assert evidence["repair_priority_types"] == ["repair_queue"]
    assert evidence["market"] == "futures"
    assert evidence["side"] == "short"
    assert evidence["horizon"] == "intraday"


def test_manager_repair_outcome_uses_summary_only_effectiveness_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="binance",
        venue="binance",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 85,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "effectiveness_fallback_counts": {
                        "general_horizon_metric": 2,
                    },
                    "effectiveness_fallback_requested_horizons": [
                        "intraday",
                    ],
                    "effectiveness_fallback_page_ids": [
                        "binance.symbol.NEARUSDT",
                        "binance.symbol.ETHUSDT",
                    ],
                    "effectiveness_fallback_markets": ["futures"],
                    "effectiveness_fallback_sides": ["long"],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    evidence = outcome["evidence"]
    assert evidence["repair_priority_types"] == [
        "horizon_effectiveness_fallback"
    ]
    assert evidence["repair_action_types"] == [
        "collect_horizon_specific_wiki_effectiveness"
    ]
    assert evidence["repair_decision_uses"] == [
        "horizon_specific_effectiveness_repair"
    ]
    assert evidence["repair_diagnostic_reasons"] == [
        "fallback_reason:general_horizon_metric",
        "fallback_page_id:binance.symbol.NEARUSDT",
        "fallback_page_id:binance.symbol.ETHUSDT",
        "requested_horizon:intraday",
    ]
    assert evidence["top_symbols"] == ["NEARUSDT", "ETHUSDT"]
    assert evidence["repair_targets"] == [
        {
            "page_id": "binance.symbol.NEARUSDT",
            "symbol": "NEARUSDT",
            "recommended_action": (
                "collect_horizon_specific_wiki_effectiveness:intraday"
            ),
        },
        {
            "page_id": "binance.symbol.ETHUSDT",
            "symbol": "ETHUSDT",
            "recommended_action": (
                "collect_horizon_specific_wiki_effectiveness:intraday"
            ),
        },
    ]
    assert evidence["market"] == "futures"
    assert evidence["side"] == "long"
    assert evidence["horizon"] == "intraday"


def test_summary_only_effectiveness_fallback_extracts_kr_symbol_from_page_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 87,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "effectiveness_fallback_counts": {
                        "general_horizon_metric": 1,
                    },
                    "effectiveness_fallback_requested_horizons": ["mid"],
                    "effectiveness_fallback_page_ids": [
                        "kis.symbol.005930",
                    ],
                    "effectiveness_fallback_markets": ["kis"],
                    "effectiveness_fallback_sides": ["long"],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    evidence = outcome["evidence"]
    assert evidence["top_symbols"] == ["005930"]
    assert evidence["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": (
                "collect_horizon_specific_wiki_effectiveness:mid"
            ),
        }
    ]


def test_summary_only_effectiveness_fallback_uses_summary_symbols(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="binance",
        venue="binance",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 88,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "effectiveness_fallback_counts": {
                        "general_horizon_metric": 1,
                    },
                    "effectiveness_fallback_requested_horizons": [
                        "intraday",
                    ],
                    "effectiveness_fallback_page_ids": [
                        "binance.playbook.pullback_reclaim",
                    ],
                    "effectiveness_fallback_symbols": ["NEARUSDT"],
                    "effectiveness_fallback_markets": ["futures"],
                    "effectiveness_fallback_sides": ["long"],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    evidence = outcome["evidence"]
    assert evidence["top_symbols"] == ["NEARUSDT"]
    assert evidence["repair_targets"] == [
        {
            "page_id": "binance.playbook.pullback_reclaim",
            "symbol": "NEARUSDT",
            "recommended_action": (
                "collect_horizon_specific_wiki_effectiveness:intraday"
            ),
        }
    ]


def test_summary_only_effectiveness_fallback_uses_page_symbol_mapping(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="binance",
        venue="binance",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 89,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "effectiveness_fallback_counts": {
                        "general_horizon_metric": 2,
                    },
                    "effectiveness_fallback_requested_horizons": [
                        "intraday",
                    ],
                    "effectiveness_fallback_page_ids": [
                        "binance.playbook.pullback_reclaim",
                        "binance.playbook.breakout_retest",
                    ],
                    "effectiveness_fallback_symbols": ["NEARUSDT", "ETHUSDT"],
                    "effectiveness_fallback_page_symbols": [
                        {
                            "page_id": "binance.playbook.pullback_reclaim",
                            "symbols": ["NEARUSDT"],
                        },
                        {
                            "page_id": "binance.playbook.breakout_retest",
                            "symbols": ["ETHUSDT"],
                        },
                    ],
                    "effectiveness_fallback_markets": ["futures"],
                    "effectiveness_fallback_sides": ["long"],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    evidence = outcome["evidence"]
    assert evidence["top_symbols"] == ["NEARUSDT", "ETHUSDT"]
    assert evidence["repair_targets"] == [
        {
            "page_id": "binance.playbook.pullback_reclaim",
            "symbol": "NEARUSDT",
            "recommended_action": (
                "collect_horizon_specific_wiki_effectiveness:intraday"
            ),
        },
        {
            "page_id": "binance.playbook.breakout_retest",
            "symbol": "ETHUSDT",
            "recommended_action": (
                "collect_horizon_specific_wiki_effectiveness:intraday"
            ),
        },
    ]


def test_summary_only_effectiveness_fallback_accepts_scalar_requested_horizon(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="binance",
        venue="binance",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 86,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "effectiveness_fallback_counts": {
                        "general_horizon_metric": 1,
                    },
                    "effectiveness_fallback_page_ids": [
                        "binance.symbol.NEARUSDT",
                    ],
                    "market": "futures",
                    "side": "short",
                    "requested_horizons": "intraday",
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    evidence = outcome["evidence"]
    assert evidence["horizon"] == "intraday"
    assert evidence["repair_diagnostic_reasons"] == [
        "fallback_reason:general_horizon_metric",
        "fallback_page_id:binance.symbol.NEARUSDT",
        "requested_horizon:intraday",
    ]


def test_manager_repair_outcome_ignores_stale_priorities_when_count_is_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 79,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "jue_wiki_repair_contract": {
                    "status": "active",
                    "repair_priority_count": 0,
                    "top_priorities": [
                        {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                            "priority_type": "requested_symbol_coverage",
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is None


def test_manager_repair_outcome_preserves_selected_wiki_horizon_diagnostics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 78,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "repair_queue_count": 1,
                    "repair_action_types": [
                        "reproject_closed_block_outcome_horizons",
                    ],
                    "repair_decision_uses": [
                        "horizon_lane_attribution_repair",
                    ],
                    "repair_diagnostic_reasons": [
                        "closed_block_outcomes_without_horizon:3",
                    ],
                    "repair_horizon_gap_total": 3,
                    "repair_horizon_gap_max_pct": 75.0,
                    "pages": [
                        {
                            "page_id": "kis.research.repair_queue",
                            "page_type": "research",
                            "repair_queue": {
                                "status": "scheduled",
                                "source_id": "repair:outcome-horizon:kis",
                                "action_type": (
                                    "reproject_closed_block_outcome_horizons"
                                ),
                                "quality_warnings": [
                                    "closed_block_outcome_horizon_missing",
                                ],
                                "diagnostic_reasons": [
                                    "closed_block_outcomes_without_horizon:3",
                                ],
                                "closed_block_outcomes_without_horizon": 3,
                                "closed_block_outcomes_without_horizon_pct": 75.0,
                                "repair_targets": [
                                    {
                                        "page_id": (
                                            "kis.application.closed_block_outcomes"
                                        ),
                                        "recommended_action": (
                                            "reproject_closed_block_outcomes_"
                                            "with_block_horizon_or_lane"
                                        ),
                                    }
                                ],
                                "decision_use": "horizon_lane_attribution_repair",
                            },
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "missed_repair_priority"
    assert outcome["evidence"]["source"] == "kis_selected_wiki_pages_repair_queue"
    assert outcome["evidence"]["repair_action_types"] == [
        "reproject_closed_block_outcome_horizons"
    ]
    assert outcome["evidence"]["repair_decision_uses"] == [
        "horizon_lane_attribution_repair"
    ]
    assert outcome["evidence"]["repair_diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:3"
    ]
    assert outcome["evidence"]["repair_horizon_gap_totals"] == [3]
    assert outcome["evidence"]["repair_horizon_gap_pcts"] == [75.0]
    assert outcome["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
    ]


def test_manager_repair_outcome_uses_selected_wiki_summary_only_repair_pressure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 80,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "repair_queue_count": 1,
                    "repair_action_types": [
                        "reproject_closed_block_outcome_horizons",
                    ],
                    "repair_decision_uses": [
                        "horizon_lane_attribution_repair",
                    ],
                    "repair_quality_warnings": [
                        "closed_block_outcome_horizon_missing",
                    ],
                    "repair_diagnostic_reasons": [
                        "closed_block_outcomes_without_horizon:4",
                    ],
                    "repair_horizon_gap_total": 4,
                    "repair_horizon_gap_max_pct": 80.0,
                    "repair_targets": [
                        {
                            "page_id": "kis.application.closed_block_outcomes",
                            "recommended_action": (
                                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
                            ),
                        }
                    ],
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "missed_repair_priority"
    assert outcome["evidence"]["source"] == "kis_selected_wiki_pages_repair_queue"
    assert outcome["evidence"]["repair_action_types"] == [
        "reproject_closed_block_outcome_horizons"
    ]
    assert outcome["evidence"]["repair_decision_uses"] == [
        "horizon_lane_attribution_repair"
    ]
    assert outcome["evidence"]["repair_diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:4"
    ]
    assert outcome["evidence"]["repair_horizon_gap_totals"] == [4]
    assert outcome["evidence"]["repair_horizon_gap_pcts"] == [80.0]
    assert outcome["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
    ]


def test_manager_repair_outcome_preserves_summary_only_multiple_repair_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 81,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "selected_wiki_pages": {
                    "repair_queue_count": 2,
                    "repair_action_types": [
                        "reproject_closed_block_outcome_horizons",
                        "repair_usage_guidance_contract",
                    ],
                    "repair_decision_uses": [
                        "horizon_lane_attribution_repair",
                        "usage_guidance_effectiveness_repair",
                    ],
                    "repair_quality_warnings": [
                        "closed_block_outcome_horizon_missing",
                        "usage_guidance_degraded",
                    ],
                    "repair_diagnostic_reasons": [
                        "closed_block_outcomes_without_horizon:4",
                    ],
                    "repair_horizon_gap_total": 4,
                    "repair_horizon_gap_max_pct": 80.0,
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "missed_repair_priority"
    assert outcome["evidence"]["repair_action_types"] == [
        "reproject_closed_block_outcome_horizons",
        "repair_usage_guidance_contract",
    ]
    assert outcome["evidence"]["repair_decision_uses"] == [
        "horizon_lane_attribution_repair",
        "usage_guidance_effectiveness_repair",
    ]
    assert outcome["evidence"]["quality_warnings"] == [
        "closed_block_outcome_horizon_missing",
        "usage_guidance_degraded",
    ]


def test_manager_repair_outcome_uses_wiki_repair_action_metadata_as_resolution(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 79,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "jue_wiki_repair_contract": {
                    "repair_priority_count": 1,
                    "top_priorities": [
                        {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                            "priority_type": "repair_queue",
                            "action_type": "refresh_symbol_financials",
                            "decision_use": "valuation_cross_check",
                            "source_id": "repair:valuation",
                        }
                    ],
                },
                "manager_response": {
                    "jue_wiki_repair_action_metadata": {
                        "count": 1,
                        "actions": [
                            {
                                "action_type": "create_blocks",
                                "symbol": "005930",
                                "jue_wiki_repair_pressure": (
                                    "valuation page stale before entry"
                                ),
                                "jue_wiki_repair_resolution": (
                                    "small waiting block until valuation refresh"
                                ),
                            }
                        ],
                    }
                },
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "resolved_repair_priority"
    resolution = outcome["evidence"]["resolution"]
    assert resolution["required"] == "jue_wiki_repair_action_metadata"
    assert resolution["resolved_candidates"] == [
        {
            "symbol": "005930",
            "resolution": "action_metadata_resolution",
            "next_trigger": "small waiting block until valuation refresh",
            "evidence_gap": "valuation page stale before entry",
        }
    ]


def test_manager_repair_outcome_preserves_component_target_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link={
            "metadata": {
                "source_row_id": 88,
                "execution_gate": {
                    "status": "ok",
                    "kill_switch": {"enabled": False},
                },
                "jue_wiki_repair_contract": {
                    "repair_priority_count": 1,
                    "top_priorities": [
                        {
                            "page_id": "kis.symbol.245450",
                            "page_type": "symbol",
                            "symbols": ["245450"],
                            "priority_type": "repair_queue",
                            "action_type": "refresh_symbol_financials",
                            "decision_use": "evidence_quality_cross_check",
                            "source_id": "repair:financials",
                        }
                    ],
                    "repair_loop_effectiveness": {
                        "component_status_summary": {
                            "top_component_targets": [
                                {
                                    "component": (
                                        "repair_learning_resolution_metrics"
                                    ),
                                    "decision_scope": "kis",
                                    "status": "repair_required",
                                    "recommended_resolution": (
                                        "revise_repair_step_contract_then_probe"
                                    ),
                                    "priority_type": "repair_queue",
                                    "action_type": "refresh_symbol_financials",
                                    "sample_count": 5,
                                    "missed_count": 4,
                                    "resolved_count": 1,
                                    "resolution_rate": 0.2,
                                    "impacted_symbols": ["245450"],
                                }
                            ],
                            "repair_required_component_targets": [
                                {
                                    "component": (
                                        "repair_learning_resolution_metrics"
                                    ),
                                    "decision_scope": "kis",
                                    "status": "repair_required",
                                    "recommended_resolution": (
                                        "revise_repair_step_contract_then_probe"
                                    ),
                                    "priority_type": "repair_queue",
                                    "action_type": "refresh_symbol_financials",
                                    "sample_count": 5,
                                    "missed_count": 4,
                                    "resolved_count": 1,
                                    "resolution_rate": 0.2,
                                    "impacted_symbols": ["245450"],
                                }
                            ],
                            "probe_component_targets": [
                                {
                                    "component": "repair_success_criteria_metrics",
                                    "decision_scope": "kis",
                                    "status": "probe",
                                    "criterion": "probe_live_quote_alignment",
                                    "priority_type": "probe_queue",
                                    "action_type": "probe_live_quote_gate",
                                    "sample_count": 3,
                                    "missed_count": 1,
                                    "resolved_count": 2,
                                    "resolution_rate": 2 / 3,
                                    "impacted_symbols": ["005930"],
                                }
                            ],
                        }
                    },
                },
                "manager_response": {"create_blocks": []},
            }
        },
    )

    assert outcome is not None
    assert outcome["outcome_kind"] == "missed_repair_priority"
    assert outcome["evidence"]["repair_component_targets"] == [
        {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "sample_count": 5,
            "missed_count": 4,
            "resolved_count": 1,
            "resolution_rate": 0.2,
            "impacted_symbols": ["245450"],
        }
    ]
    assert outcome["evidence"]["repair_required_component_targets"] == [
        {
            "component": "repair_learning_resolution_metrics",
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_resolution": "revise_repair_step_contract_then_probe",
            "priority_type": "repair_queue",
            "action_type": "refresh_symbol_financials",
            "sample_count": 5,
            "missed_count": 4,
            "resolved_count": 1,
            "resolution_rate": 0.2,
            "impacted_symbols": ["245450"],
        }
    ]
    assert outcome["evidence"]["repair_probe_component_targets"] == [
        {
            "component": "repair_success_criteria_metrics",
            "decision_scope": "kis",
            "status": "probe",
            "criterion": "probe_live_quote_alignment",
            "priority_type": "probe_queue",
            "action_type": "probe_live_quote_gate",
            "sample_count": 3,
            "missed_count": 1,
            "resolved_count": 2,
            "resolution_rate": 2 / 3,
            "impacted_symbols": ["005930"],
        }
    ]


def test_project_decision_links_preserves_selected_page_repair_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-repair-page",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.245450"],
                        },
                        "jue_wiki": {
                            "selection_run_id": "selection:kis-repair-page",
                            "prompt_mode": "assist",
                            "pages": [
                                {
                                    "page_id": "kis.symbol.245450",
                                    "page_type": "symbol",
                                    "confidence": 0.72,
                                    "symbols": ["245450"],
                                    "repair_targets": [
                                        {
                                            "page_id": "kis.symbol.245450",
                                            "symbol": "245450",
                                            "recommended_action": action,
                                        }
                                    ],
                                    "repair_target_effectiveness": {
                                        "page_id": f"repair_target.{action}",
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
                                            f"repair_target:{action}",
                                            "repair_target_prior_status:degraded",
                                        ],
                                    },
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-repair-page")

    page = links[0]["metadata"]["selected_wiki_pages"]["pages"][0]
    assert page["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": action,
        }
    ]
    assert page["repair_target_effectiveness"] == {
        "page_id": f"repair_target.{action}",
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
            f"repair_target:{action}",
            "repair_target_prior_status:degraded",
        ],
    }


def test_project_decision_links_derives_repair_targets_from_source_refs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-ref-repair-page",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.245450"],
                        },
                        "jue_wiki": {
                            "selection_run_id": "selection:kis-ref-repair-page",
                            "prompt_mode": "assist",
                            "pages": [
                                {
                                    "page_id": "kis.symbol.245450",
                                    "page_type": "symbol",
                                    "confidence": 0.72,
                                    "symbols": ["245450"],
                                    "source_refs": [
                                        {
                                            "source_type": "wiki_repair_queue",
                                            "repair_targets": [
                                                {
                                                    "page_id": "kis.symbol.245450",
                                                    "symbol": "245450",
                                                    "recommended_action": action,
                                                }
                                            ],
                                            "repair_target_effectiveness": {
                                                "page_id": f"repair_target.{action}",
                                                "status": "degraded",
                                                "sample_count": 3,
                                                "helpful_score": -5.0,
                                            },
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-ref-repair-page")

    page = links[0]["metadata"]["selected_wiki_pages"]["pages"][0]
    assert page["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": action,
        }
    ]
    assert page["repair_target_effectiveness"] == {
        "page_id": f"repair_target.{action}",
        "status": "degraded",
        "sample_count": 3,
        "helpful_score": -5.0,
    }


def test_project_decision_links_derives_quality_warnings_from_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-evidence-warning",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.245450"],
                        },
                        "jue_wiki": {
                            "selection_run_id": "selection:kis-evidence-warning",
                            "prompt_mode": "assist",
                            "pages": [
                                {
                                    "page_id": "kis.symbol.245450",
                                    "page_type": "symbol",
                                    "confidence": 0.72,
                                    "symbols": ["245450"],
                                    "evidence_quality": {
                                        "status_counts": {"partial": 1},
                                        "top_warnings": [
                                            {
                                                "warning": "financials_missing",
                                                "count": 2,
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(
        selection_run_id="selection:kis-evidence-warning"
    )

    summary = links[0]["metadata"]["selected_wiki_pages"]
    assert summary["quality_warning_count"] == 1
    assert summary["warning_counts"] == {"financials_missing": 1}
    assert summary["pages"][0]["quality_status"] == "partial"
    assert summary["pages"][0]["quality_warnings"] == ["financials_missing"]


def test_project_decision_links_derives_quality_warnings_from_source_ref_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-ref-evidence-warning",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.245450"],
                        },
                        "jue_wiki": {
                            "selection_run_id": "selection:kis-ref-evidence-warning",
                            "prompt_mode": "assist",
                            "pages": [
                                {
                                    "page_id": "kis.symbol.245450",
                                    "page_type": "symbol",
                                    "confidence": 0.72,
                                    "symbols": ["245450"],
                                    "source_refs": [
                                        {
                                            "source_type": "symbol_fundamentals",
                                            "source_id": "245450:2026-07-03",
                                            "evidence_quality": {
                                                "status_counts": {"partial": 1},
                                                "top_warnings": [
                                                    {
                                                        "warning": (
                                                            "financials_missing"
                                                        ),
                                                        "count": 2,
                                                    }
                                                ],
                                            },
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(
        selection_run_id="selection:kis-ref-evidence-warning"
    )

    summary = links[0]["metadata"]["selected_wiki_pages"]
    assert summary["quality_warning_count"] == 1
    assert summary["warning_counts"] == {"financials_missing": 1}
    assert summary["pages"][0]["quality_status"] == "partial"
    assert summary["pages"][0]["quality_warnings"] == ["financials_missing"]
    assert summary["pages"][0]["evidence_quality"]["warning_counts"] == {
        "financials_missing": 1
    }


def test_project_decision_links_enriches_selected_pages_from_wiki_db(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page = service.wiki.write_page(
        scope="kis",
        page_type="symbol",
        key="245450",
        title="씨앤씨인터내셔널",
        symbols=["245450"],
        content_sections={
            "Current Stance": "needs fundamentals repair",
            "Durable Facts": "facts",
            "Evidence Links": "links",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "245450:2026-07-03",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.82,
        freshness="fresh",
    )
    page_id = str(page["page_id"])
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-enrich",
                            "prompt_mode": "assist",
                            "selected_page_ids": [page_id],
                        },
                        "jue_wiki": {
                            "selection_run_id": "selection:kis-enrich",
                            "prompt_mode": "assist",
                            "pages": [{"page_id": page_id}],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-enrich")

    summary = links[0]["metadata"]["selected_wiki_pages"]
    assert summary["quality_warning_count"] == 1
    assert summary["pages"][0]["page_type"] == "symbol"
    assert summary["pages"][0]["confidence"] == 0.82
    assert summary["pages"][0]["symbols"] == ["245450"]
    assert summary["pages"][0]["quality_status"] == "partial"
    assert summary["pages"][0]["quality_warnings"] == ["financials_missing"]
    assert summary["pages"][0]["evidence_quality"] == {
        "row_count": 1,
        "status_counts": {"partial": 1},
        "warning_counts": {"financials_missing": 1},
        "top_warnings": [
            {
                "warning": "financials_missing",
                "count": 1,
            }
        ],
        "warning_page_ids": {"financials_missing": [page_id]},
        "caution_page_ids": [page_id],
    }


def test_project_decision_links_ingests_manager_response_resolution(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-response",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["binance.risk.trading_validation"],
                        },
                        "execution_gate": {"status": "ok"},
                    }
                ),
                json.dumps(
                    {
                        "final_action_count": 0,
                        "validation_repair_resolution": {
                            "blanket_hold_allowed": False,
                            "resolved_candidates": [
                                {
                                    "symbol": "ETHUSDT",
                                    "market": "futures",
                                    "resolution": "candidate_rejected",
                                    "next_trigger": "spread < 0.08% and funding neutral",
                                    "evidence_gap": "orderbook depth was too thin",
                                }
                            ],
                        },
                        "hold_decision": {
                            "summary": "ETH는 호가 깊이 회복 전까지 대기",
                            "watch_symbols": ["ETHUSDT"],
                            "next_triggers": [
                                {
                                    "symbol": "ETHUSDT",
                                    "market": "futures",
                                    "condition": "spread normalizes",
                                    "price": 3200,
                                    "reason": "실행비용 완화",
                                }
                            ],
                            "data_gaps": ["depth"],
                        },
                    }
                ),
                json.dumps({"create_blocks": []}),
            ),
        )

    result = service.project_decision_links(binance_db_path=binance_db)
    links = service.list_decision_links(selection_run_id="selection:binance-response")

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    response = links[0]["metadata"]["manager_response"]
    assert response["final_action_count"] == 0
    assert response["validation_repair_resolution"]["blanket_hold_allowed"] is False
    assert response["validation_repair_resolution"]["resolved_candidates"][0] == {
        "symbol": "ETHUSDT",
        "market": "futures",
        "resolution": "candidate_rejected",
        "next_trigger": "spread < 0.08% and funding neutral",
        "evidence_gap": "orderbook depth was too thin",
    }
    assert response["hold_decision"]["watch_symbols"] == ["ETHUSDT"]
    assert response["hold_decision"]["next_triggers"][0]["price"] == 3200


def test_project_decision_links_preserves_explicit_zero_final_action_count(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:40:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-zero-actions",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["binance.risk.trading_validation"],
                        },
                    }
                ),
                json.dumps(
                    {
                        "final_action_count": 0,
                        "hold_decision": {
                            "summary": "stale action payload must not override zero",
                            "watch_symbols": ["ETHUSDT"],
                        },
                    }
                ),
                json.dumps(
                    {
                        "create_blocks": [
                            {
                                "symbol": "ETHUSDT",
                                "qty": 1,
                            }
                        ]
                    }
                ),
            ),
        )

    service.project_decision_links(binance_db_path=binance_db)
    links = service.list_decision_links(
        selection_run_id="selection:binance-zero-actions"
    )
    response = links[0]["metadata"]["manager_response"]

    assert response["final_action_count"] == 0
    assert response["action_counts"] == {"create_blocks": 1}


def test_project_decision_links_preserves_manager_wiki_repair_action_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T09:45:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-repair-action",
                            "prompt_mode": "primary",
                            "selected_page_ids": ["kis.symbol.005930"],
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 7,
                            "omitted_priority_count": 4,
                        },
                    }
                ),
                json.dumps(
                    {
                        "final_action_count": 1,
                        "create_blocks": [
                            {
                                "symbol": "005930",
                                "jue_wiki_repair_pressure": (
                                    "repair queue omitted 4 삼성전자 valuation items"
                                ),
                                "jue_wiki_repair_resolution": (
                                    "created only a small wait block until wiki refresh"
                                ),
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "create_blocks": [
                            {
                                "symbol": "005930",
                                "jue_wiki_repair_pressure": (
                                    "repair queue omitted 4 삼성전자 valuation items"
                                ),
                                "jue_wiki_repair_resolution": (
                                    "created only a small wait block until wiki refresh"
                                ),
                            }
                        ],
                    }
                ),
            ),
        )

    result = service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-repair-action")

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    response = links[0]["metadata"]["manager_response"]
    assert response["jue_wiki_repair_action_metadata"] == {
        "count": 1,
        "actions": [
            {
                "action_type": "create_blocks",
                "symbol": "005930",
                "jue_wiki_repair_pressure": (
                    "repair queue omitted 4 삼성전자 valuation items"
                ),
                "jue_wiki_repair_resolution": (
                    "created only a small wait block until wiki refresh"
                ),
            }
        ],
    }


def test_project_decision_links_reads_compacted_wiki_repair_action_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T10:15:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-compacted-repair",
                            "prompt_mode": "primary",
                            "selected_page_ids": ["kis.symbol.005930"],
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 1,
                            "top_priorities": [
                                {
                                    "page_id": "kis.symbol.005930",
                                    "symbols": ["005930"],
                                    "priority_type": "memory_card_quality",
                                    "action_type": "cross_check_memory_card_quality",
                                    "decision_use": (
                                        "memory_card_quality_resolution_check"
                                    ),
                                    "source_id": (
                                        "kis.symbol.005930:memory_card_quality"
                                    ),
                                }
                            ],
                        },
                    }
                ),
                json.dumps({"final_action_count": 1}),
                json.dumps(
                    {
                        "create_blocks": [
                            {
                                "symbol": "005930",
                                "metadata_summary": {
                                    "jue_wiki_repair_pressure": {
                                        "status": "required",
                                        "page_id": "kis.symbol.005930",
                                        "reason": (
                                            "memory card degraded before sizing"
                                        ),
                                    },
                                    "jue_wiki_repair_resolution": {
                                        "status": "resolved_by_action",
                                        "reason": (
                                            "created only a small waiting block "
                                            "after valuation cross-check"
                                        ),
                                    },
                                    "jue_wiki_memory_card_quality": {
                                        "status": "degraded",
                                        "missing": ["lessons"],
                                    },
                                    "jue_wiki_memory_card_cross_check": {
                                        "status": "cross_checked",
                                        "sources": ["valuation_snapshot"],
                                    },
                                },
                            }
                        ],
                        "_applied": {
                            "created": {
                                "item_count": 1,
                                "items": [
                                    {
                                        "block_id": "blk_005930_compacted",
                                        "symbol": "005930",
                                        "context_summary": {
                                            "jue_wiki_repair_pressure": {
                                                "status": "required",
                                                "reason": (
                                                    "applied compacted block "
                                                    "kept wiki pressure"
                                                ),
                                            },
                                            "jue_wiki_repair_resolution": {
                                                "status": "resolved_by_action",
                                                "reason": (
                                                    "applied block kept wiki "
                                                    "resolution"
                                                ),
                                            },
                                        },
                                    }
                                ],
                                "omitted_item_count": 0,
                            }
                        },
                    }
                ),
            ),
        )

    result = service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(
        selection_run_id="selection:kis-compacted-repair"
    )
    outcome = service._manager_repair_outcome(
        scope="kis",
        venue="kis",
        related_block_count=0,
        link=links[0],
    )

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    response = links[0]["metadata"]["manager_response"]
    action = response["jue_wiki_repair_action_metadata"]["actions"][0]
    assert action["action_type"] == "create_blocks"
    assert action["symbol"] == "005930"
    assert "memory card degraded" in action["jue_wiki_repair_pressure"]
    assert "small waiting block" in action["jue_wiki_repair_resolution"]
    assert "valuation_snapshot" in action["jue_wiki_memory_card_cross_check"]
    assert outcome is not None
    assert outcome["outcome_kind"] == "resolved_repair_priority"
    assert outcome["evidence"]["resolution"]["resolved_count"] == 1


def test_project_decision_links_reads_compacted_binance_wiki_action_lane_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T11:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-compact-repair",
                            "prompt_mode": "primary",
                            "selected_page_ids": ["binance.symbol.NEARUSDT"],
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 1,
                            "top_priorities": [
                                {
                                    "page_id": "binance.symbol.NEARUSDT",
                                    "symbols": ["NEARUSDT"],
                                    "priority_type": "repair_queue",
                                    "action_type": "refresh_crypto_microstructure",
                                    "decision_use": "microstructure_cross_check",
                                    "source_id": "repair:near:microstructure",
                                }
                            ],
                        },
                    }
                ),
                json.dumps({"final_action_count": 1}),
                json.dumps(
                    {
                        "create_blocks": [
                            {
                                "symbol": "NEARUSDT",
                                "market": "futures",
                                "side": "short",
                                "horizon": "intraday",
                                "metadata_summary": {
                                    "jue_wiki_repair_pressure": {
                                        "status": "required",
                                        "reason": (
                                            "NEARUSDT microstructure wiki was stale"
                                        ),
                                    },
                                    "jue_wiki_repair_resolution": {
                                        "status": "resolved_by_action",
                                        "reason": (
                                            "futures short block used live "
                                            "funding and spread cross-check"
                                        ),
                                    },
                                },
                            }
                        ],
                    }
                ),
            ),
        )

    result = service.project_decision_links(binance_db_path=binance_db)
    links = service.list_decision_links(
        selection_run_id="selection:binance-compact-repair"
    )
    outcome = service._manager_repair_outcome(
        scope="binance",
        venue="binance",
        related_block_count=0,
        link=links[0],
    )

    assert result["status"] == "ok"
    response = links[0]["metadata"]["manager_response"]
    action = response["jue_wiki_repair_action_metadata"]["actions"][0]
    assert action["symbol"] == "NEARUSDT"
    assert action["market"] == "futures"
    assert action["side"] == "short"
    assert action["horizon"] == "intraday"
    assert outcome is not None
    resolution = outcome["evidence"]["resolution"]["resolved_candidates"][0]
    assert resolution["market"] == "futures"
    assert resolution["horizon"] == "intraday"


def test_project_decision_links_updates_existing_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    base_prompt = {
        "jue_wiki_application": {
            "selection_run_id": "selection:kis-refresh",
            "prompt_mode": "assist",
            "selected_page_ids": ["kis.symbol.005930"],
        },
        "decision_inputs": ["account"],
    }
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(base_prompt),
            ),
        )
        row_id = cursor.lastrowid

    first = service.project_decision_links(kis_db_path=kis_db)
    first_links = service.list_decision_links(selection_run_id="selection:kis-refresh")
    assert first["inserted_count"] == 1
    assert len(first_links) == 1
    assert first_links[0]["metadata"]["decision_inputs"] == ["account"]
    assert first_links[0]["metadata"]["execution_gate"] == {}

    updated_prompt = {
        **base_prompt,
        "jue_wiki_application": {
            "selection_run_id": "selection:kis-refresh",
            "prompt_mode": "assist",
            "selected_page_ids": [
                "kis.symbol.005930",
                "kis.risk.trading_validation",
            ],
        },
        "decision_inputs": ["account", "execution_gate"],
        "execution_gate": {
            "status": "ok",
            "execution_mode": "live",
            "execute_orders": True,
            "kill_switch": {"enabled": False},
            "cash_available": {"orderable_cash_krw": 700_000},
        },
    }
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            "UPDATE manager_runs SET prompt_json = ? WHERE id = ?",
            (json.dumps(updated_prompt), row_id),
        )

    second = service.project_decision_links(kis_db_path=kis_db)
    refreshed_links = service.list_decision_links(
        selection_run_id="selection:kis-refresh"
    )

    assert second["inserted_count"] == 1
    assert len(refreshed_links) == 1
    assert refreshed_links[0]["selected_pages"] == [
        "kis.symbol.005930",
        "kis.risk.trading_validation",
    ]
    assert refreshed_links[0]["metadata"]["decision_inputs"] == [
        "account",
        "execution_gate",
    ]
    assert refreshed_links[0]["metadata"]["execution_gate"][
        "cash_available"
    ] == {"orderable_cash_krw": 700_000}


def test_project_decision_links_preserves_backfilled_selected_page_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page = service.wiki.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={"Current Stance": "Backfilled summary should persist."},
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "quality_warnings": ["valuation_stale_gt_30d"],
            }
        ],
        confidence=0.88,
        freshness="current",
    )
    page_id = str(page["page_id"])
    kis_db = tmp_path / "kis_blocks.db"
    prompt = {
        "jue_wiki_application": {
            "selection_run_id": "selection:kis-preserve-backfill",
            "prompt_mode": "assist",
            "selected_page_ids": [page_id],
        },
        "decision_inputs": ["account"],
    }
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(prompt),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    service.backfill_decision_link_selected_wiki_pages()
    links_after_backfill = service.list_decision_links(
        selection_run_id="selection:kis-preserve-backfill"
    )
    expected_page_summary = {
        "page_id": page_id,
        "page_type": "symbol",
        "confidence": 0.88,
        "symbols": ["005930"],
        "quality_warnings": ["valuation_stale_gt_30d"],
            "evidence_quality": {
                "row_count": 1,
                "status_counts": {},
                "warning_counts": {"valuation_stale_gt_30d": 1},
                "top_warnings": [
                {
                    "warning": "valuation_stale_gt_30d",
                        "count": 1,
                    }
                ],
                "warning_page_ids": {
                    "valuation_stale_gt_30d": [page_id],
                },
            },
            "usage_guidance": {
            "trust_level": "low",
            "risk_posture": "supporting_cross_check",
            "decision_use": (
                "use this page as supporting context after resolving "
                "candidate-level evidence gaps"
            ),
            "allowed_uses": [
                "supporting_context",
                "follow_up_research",
                "target_stop_context",
                "risk_note_context",
            ],
            "required_cross_checks": [
                "live_quote",
                "fresh_valuation_cross_check",
            ],
            "hard_blocker": False,
        },
    }
    assert (
        links_after_backfill[0]["metadata"]["selected_wiki_pages"]["pages"][0]
        == expected_page_summary
    )

    service.project_decision_links(kis_db_path=kis_db)
    links_after_reproject = service.list_decision_links(
        selection_run_id="selection:kis-preserve-backfill"
    )

    assert (
        links_after_reproject[0]["metadata"]["selected_wiki_pages"]["pages"][0]
        == expected_page_summary
    )


def test_backfill_decision_link_selected_wiki_pages_uses_selection_run_trace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page = service.wiki.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={"Current Stance": "Selection trace should survive."},
        source_refs=[],
        confidence=0.81,
        freshness="current",
    )
    page_id = str(page["page_id"])
    service.wiki.record_selection_run(
        run_id="selection:trace-backfill",
        target_scope="kis",
        request={"target_scope": "kis", "symbols": ["005930"]},
        selected_pages=[
            {
                "page_id": page_id,
                "rank": 2,
                "score": 18.75,
                "reasons": ["symbol_overlap:005930", "freshness:current"],
                "penalties": ["evidence_quality:partial:1"],
                "char_count": 321,
            }
        ],
        rejected_pages=[],
        char_count=321,
        max_chars=20_000,
        status="ok",
        budget_report={"selected_count": 1},
    )
    service.record_decision_link(
        selection_run_id="selection:trace-backfill",
        manager_run_id="kis-manager-trace-backfill",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=[page_id],
        symbol="005930",
        venue="kis",
        horizon="mid",
    )

    result = service.backfill_decision_link_selected_wiki_pages()
    links = service.list_decision_links(selection_run_id="selection:trace-backfill")
    summary = links[0]["metadata"]["selected_wiki_pages"]
    selected_page = summary["pages"][0]

    assert result["updated_count"] == 1
    assert links[0]["metadata"]["selected_wiki_pages_backfill_source"] == (
        "selection_run_pages"
    )
    assert selected_page["selection_rank"] == 2
    assert selected_page["selection_score"] == 18.75
    assert selected_page["selection_reasons"] == [
        "symbol_overlap:005930",
        "freshness:current",
    ]
    assert selected_page["selection_penalties"] == ["evidence_quality:partial:1"]
    assert selected_page["selection_char_count"] == 321


def test_project_decision_links_does_not_replace_rich_selected_page_summary_with_sparse_one(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    selection_run_id = "selection:kis-preserve-rich-existing"
    page_id = "kis.symbol.999999"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": selection_run_id,
                            "prompt_mode": "assist",
                            "selected_page_ids": [page_id],
                        }
                    }
                ),
            ),
        )
        row_id = cursor.lastrowid
    link_id = f"wiki-link:kis:block_manager:{row_id}:{selection_run_id}"
    rich_summary = {
        "page_count": 1,
        "quality_warning_count": 1,
        "pages": [
            {
                "page_id": page_id,
                "page_type": "symbol",
                "confidence": 0.91,
                "symbols": ["999999"],
                "quality_warnings": ["financials_missing"],
            }
        ],
    }
    service.wiki.initialize()
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_decision_links (
                link_id, selection_run_id, manager_run_id, decision_scope,
                decision_type, symbol, block_id, venue, horizon, action,
                prompt_mode, selected_pages_json, metadata_json, linked_at
            ) VALUES (?, ?, ?, ?, ?, '', '', ?, '', ?, ?, ?, ?, ?)
            """,
            (
                link_id,
                selection_run_id,
                f"kis:block_manager:{row_id}",
                "kis",
                "block_manager",
                "kis",
                "manager_run",
                "assist",
                json.dumps([page_id]),
                json.dumps(
                    {
                        "selected_wiki_pages": rich_summary,
                        "selected_wiki_pages_backfill_source": "current_wiki_pages",
                    }
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id=selection_run_id)

    assert links[0]["metadata"]["selected_wiki_pages"] == rich_summary
    assert links[0]["metadata"]["source_row_id"] == row_id


def test_project_selection_outcomes_ingests_closed_kis_blocks(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                manager_run_id INTEGER,
                horizon TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                opened_at TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE block_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_id TEXT NOT NULL,
                side TEXT NOT NULL,
                limit_price INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL
            );
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T00:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-closed",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.005930"],
                        }
                    }
                ),
            ),
        )
        manager_run_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, qty_initial, qty_open, entry_price,
                target_price, stop_price, manager_run_id, horizon, status,
                created_at, updated_at, opened_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "blk-1",
                "005930",
                2,
                0,
                70000,
                76000,
                68000,
                manager_run_id,
                "mid",
                "closed",
                "2026-06-28T00:00:00+00:00",
                "2026-06-28T01:00:00+00:00",
                "2026-06-28T00:05:00+00:00",
                "2026-06-28T01:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO block_orders (block_id, side, limit_price, avg_fill_price)
            VALUES (?, ?, ?, ?)
            """,
            ("blk-1", "sell", 0, 73500),
        )

    service.project_decision_links(kis_db_path=kis_db)
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(selection_run_id="selection:kis-closed")
    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_status"] == "win"
    assert outcomes[0]["block_id"] == ""
    assert outcomes[0]["horizon"] == "mid"
    assert outcomes[0]["return_pct"] == 5.0
    assert outcomes[0]["evidence"]["block_id"] == "blk-1"
    assert metric["status"] == "active"
    assert metric["sample_count"] == 1


def test_project_selection_outcomes_maps_binance_lane_to_effectiveness_horizon(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'spot',
                side TEXT NOT NULL DEFAULT 'long',
                lane TEXT NOT NULL DEFAULT '',
                manager_run_id INTEGER,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                opened_at TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE block_performance_reflections (
                block_id TEXT PRIMARY KEY,
                entry_price REAL,
                exit_price REAL,
                net_pnl_usdt REAL,
                pnl_usdt REAL,
                mfe_r_multiple REAL,
                mae_r_multiple REAL
            );
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-futures-closed",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["binance.symbol.NEARUSDT"],
                        }
                    }
                ),
            ),
        )
        manager_run_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, market, side, lane, manager_run_id, status,
                metadata_json, created_at, updated_at, opened_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bn-fut-1",
                "NEARUSDT",
                "futures",
                "short",
                "futures_short",
                manager_run_id,
                "closed",
                json.dumps({"horizon": "intraday"}),
                "2026-07-01T18:00:00+00:00",
                "2026-07-01T18:40:00+00:00",
                "2026-07-01T18:05:00+00:00",
                "2026-07-01T18:40:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO block_performance_reflections (
                block_id, entry_price, exit_price, net_pnl_usdt, pnl_usdt,
                mfe_r_multiple, mae_r_multiple
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("bn-fut-1", 3.0, 2.85, 1.7, 1.7, 1.2, -0.2),
        )

    service.project_decision_links(binance_db_path=binance_db)
    result = service.project_selection_outcomes(binance_db_path=binance_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:binance-futures-closed"
    )
    service.project_page_effectiveness(min_samples=1)
    futures_metric = service.page_effectiveness(
        page_id="binance.symbol.NEARUSDT",
        decision_scope="binance",
        venue="binance",
        horizon="futures",
    )
    spot_metric = service.page_effectiveness(
        page_id="binance.symbol.NEARUSDT",
        decision_scope="binance",
        venue="binance",
        horizon="spot",
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_status"] == "win"
    assert outcomes[0]["horizon"] == "futures"
    assert round(outcomes[0]["return_pct"], 6) == 5.0
    assert futures_metric["status"] == "active"
    assert futures_metric["sample_count"] == 1
    assert spot_metric["status"] == "missing"


def test_project_selection_outcomes_marks_unresolved_pressure_as_loss(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-pressure",
                            "prompt_mode": "assist",
                            "selected_page_ids": [
                                "binance.symbol.BTCUSDT",
                            ],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "zero_action_streak": 3,
                            "candidate_count": 8,
                            "strong_candidate_count": 4,
                            "top_candidates": [{"symbol": "BTCUSDT"}],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(binance_db_path=binance_db)
    result = service.project_selection_outcomes(binance_db_path=binance_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:binance-pressure"
    )
    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="binance.symbol.BTCUSDT",
        decision_scope="binance",
        venue="binance",
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "missed_action_pressure"
    assert outcomes[0]["outcome_status"] == "loss"
    assert outcomes[0]["return_pct"] == -0.1
    assert outcomes[0]["evidence"]["reason"] == "action_required_without_block"
    assert outcomes[0]["evidence"]["top_symbols"] == ["BTCUSDT"]
    assert metric["status"] == "degraded"


def test_project_decision_links_stores_wiki_action_pressure_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T09:20:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-action-contract",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.ops.action_pressure"],
                        },
                        "jue_wiki_action_pressure_contract": {
                            "status": "active",
                            "page_ids": ["kis.ops.action_pressure"],
                            "core_rule": "resolve candidate backlog",
                            "accepted_resolutions": [
                                "create a wait_for_price block",
                                "reject top candidates with exact missing evidence",
                            ],
                        },
                    }
                ),
            ),
        )

    result = service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-action-contract")

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    assert links[0]["metadata"]["jue_wiki_action_pressure_contract"] == {
        "status": "active",
        "page_ids": ["kis.ops.action_pressure"],
        "core_rule": "resolve candidate backlog",
        "accepted_resolutions": [
            "create a wait_for_price block",
            "reject top candidates with exact missing evidence",
        ],
    }


def test_project_decision_links_stores_wiki_validation_repair_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T09:22:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-validation-contract",
                            "prompt_mode": "assist",
                            "selected_page_ids": [
                                "kis.validation.walk_forward_analysis"
                            ],
                        },
                        "jue_wiki_validation_repair_contract": {
                            "version": "jue_wiki_validation_repair_contract_v1",
                            "status": "repair_required",
                            "hard_blocker": False,
                            "requires_validation_repair_resolution": True,
                            "top_disciplines": ["walk_forward_analysis"],
                            "repair_action_ids": ["run_walk_forward_replay"],
                            "entry_biases": ["depth_checked_probe"],
                            "allowed_entry_postures": ["depth_checked_probe"],
                            "blocked_entry_patterns": ["unvalidated_scale_up"],
                            "risk_budget_multiplier": 0.25,
                            "source_counts": {
                                "kis_validation_repair": 3,
                                "kis_validation_repair_contract": 2,
                            },
                            "legacy_source_counts": {"kis_validation_repair": 3},
                            "contract_source_counts": {
                                "kis_validation_repair_contract": 2
                            },
                            "legacy_sample_count": 3,
                            "contract_sample_count": 2,
                            "source_mix_status": "mixed_contract_and_legacy",
                            "source_mix_count_basis": (
                                "top_degraded_metric_signal_count"
                            ),
                            "contract_feedback_gap": {
                                "status": "missing_contract_outcomes",
                                "legacy_sample_count": 3,
                                "contract_sample_count": 0,
                                "required_response": (
                                    "record validation_repair_resolution and "
                                    "resolved_candidates so future wiki updates "
                                    "can measure contract effectiveness"
                                ),
                            },
                            "contract_basis_pressure_summary": {
                                "sample_count": 9,
                                "missed_count": 6,
                                "resolved_count": 3,
                                "resolution_rate": 1 / 3,
                                "miss_rate": 2 / 3,
                                "repair_pressure_score": 4.0,
                                "status": "repair_required",
                            },
                            "degraded_metric_evidence": [
                                {
                                    "discipline_id": "walk_forward_analysis",
                                    "repair_action_id": "run_walk_forward_replay",
                                    "entry_bias": "depth_checked_probe",
                                    "sample_count": 5,
                                    "missed_count": 3,
                                    "resolved_count": 2,
                                    "resolution_rate": 0.4,
                                    "status": "repair_required",
                                    "source_counts": {
                                        "kis_validation_repair": 3,
                                        "kis_validation_repair_contract": 2,
                                    },
                                }
                            ],
                            "required_response": (
                                "resolve validation repair before scale-up"
                            ),
                            "accepted_resolutions": [
                                "smaller_probe_block",
                                "waiting_entry_with_validation_repair_resolution",
                                "candidate_reject_with_missing_validation_named",
                            ],
                            "safety_gates_still_override": True,
                        },
                    }
                ),
            ),
        )

    result = service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(
        selection_run_id="selection:kis-validation-contract"
    )

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    assert links[0]["metadata"]["jue_wiki_validation_repair_contract"] == {
        "version": "jue_wiki_validation_repair_contract_v1",
        "status": "repair_required",
        "hard_blocker": False,
        "requires_validation_repair_resolution": True,
        "top_disciplines": ["walk_forward_analysis"],
        "repair_action_ids": ["run_walk_forward_replay"],
        "entry_biases": ["depth_checked_probe"],
        "allowed_entry_postures": ["depth_checked_probe"],
        "blocked_entry_patterns": ["unvalidated_scale_up"],
        "risk_budget_multiplier": 0.25,
        "source_counts": {
            "kis_validation_repair": 3,
            "kis_validation_repair_contract": 2,
        },
        "legacy_source_counts": {"kis_validation_repair": 3},
        "contract_source_counts": {"kis_validation_repair_contract": 2},
        "legacy_sample_count": 3,
        "contract_sample_count": 2,
        "source_mix_status": "mixed_contract_and_legacy",
        "source_mix_count_basis": "top_degraded_metric_signal_count",
        "contract_feedback_gap": {
            "status": "missing_contract_outcomes",
            "legacy_sample_count": 3,
            "contract_sample_count": 0,
            "required_response": (
                "record validation_repair_resolution and resolved_candidates so "
                "future wiki updates can measure contract effectiveness"
            ),
        },
        "contract_basis_pressure_summary": {
            "sample_count": 9,
            "missed_count": 6,
            "resolved_count": 3,
            "resolution_rate": 1 / 3,
            "miss_rate": 2 / 3,
            "repair_pressure_score": 4.0,
            "status": "repair_required",
        },
        "degraded_metric_evidence": [
            {
                "discipline_id": "walk_forward_analysis",
                "repair_action_id": "run_walk_forward_replay",
                "entry_bias": "depth_checked_probe",
                "sample_count": 5,
                "missed_count": 3,
                "resolved_count": 2,
                "resolution_rate": 0.4,
                "status": "repair_required",
                "source_counts": {
                    "kis_validation_repair": 3,
                    "kis_validation_repair_contract": 2,
                },
            }
        ],
        "required_response": "resolve validation repair before scale-up",
        "accepted_resolutions": [
            "smaller_probe_block",
            "waiting_entry_with_validation_repair_resolution",
            "candidate_reject_with_missing_validation_named",
        ],
        "safety_gates_still_override": True,
    }


def test_project_decision_links_stores_wiki_quality_pressure_action_plan(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T09:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-quality-pressure",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.005930"],
                            "quality_summary": {
                                "row_count": 2,
                                "status_counts": {"partial": 1, "weak": 1},
                                "top_warnings": [
                                    {"warning": "price_missing", "count": 2},
                                    {
                                        "warning": "valuation_stale_gt_30d",
                                        "count": 1,
                                    },
                                ],
                                "weak_page_ids": ["kis.symbol.005930"],
                                "caution_page_ids": [
                                    "kis.symbol.005930",
                                    "kis.symbol.277810",
                                ],
                                "warning_page_ids": {
                                    "price_missing": [
                                        "kis.symbol.005930",
                                        "kis.symbol.277810",
                                    ],
                                    "valuation_stale_gt_30d": [
                                        "kis.symbol.005930"
                                    ],
                                },
                            },
                            "quality_pressure_action_plan": {
                                "status": "repair_required",
                                "hard_blocker": False,
                                "decision_policy": (
                                    "use_quality_warnings_as_candidate_level_cross_checks_not_blanket_holds"
                                ),
                                "required_adjustments": [
                                    {
                                        "adjustment_type": (
                                            "candidate_level_cross_check"
                                        ),
                                        "reason": "weak_wiki_pages",
                                        "page_ids": ["kis.symbol.005930"],
                                        "resolution": (
                                            "refresh_or_cross_check_before_sizing"
                                        ),
                                    },
                                    {
                                        "adjustment_type": (
                                            "quality_warning_resolution"
                                        ),
                                        "warning": "price_missing",
                                        "count": 2,
                                        "resolution": (
                                            "refresh_or_cross_check_before_sizing"
                                        ),
                                        "page_ids": [
                                            "kis.symbol.005930",
                                            "kis.symbol.277810",
                                        ],
                                    },
                                ],
                                "repair_focus": [
                                    {
                                        "priority_type": "evidence_quality",
                                        "warning": "price_missing",
                                        "count": 2,
                                        "page_ids": [
                                            "kis.symbol.005930",
                                            "kis.symbol.277810",
                                        ],
                                        "effectiveness_status": "degraded",
                                        "effectiveness": {
                                            "warning": "price_missing",
                                            "page_id": (
                                                "quality_warning.price_missing"
                                            ),
                                            "status": "degraded",
                                            "sample_count": 4,
                                            "expectancy": -0.7,
                                        },
                                        "decision_use": (
                                            "evidence_quality_cross_check"
                                        ),
                                    }
                                ],
                                "quality_effectiveness_pressure": {
                                    "status": "repair_required",
                                    "degraded_warnings": ["price_missing"],
                                    "probe_warnings": [],
                                    "active_warnings": [],
                                },
                                "caution_page_ids": [
                                    "kis.symbol.005930",
                                    "kis.symbol.277810",
                                ],
                            },
                            "quality_warning_source_summary": {
                                "source_count": 1,
                                "degraded_count": 1,
                                "top_degraded_sources": [
                                    {
                                        "page_id": "kis.symbol.277810",
                                        "decision_scope": "kis",
                                        "venue": "kis",
                                        "horizon": "mid",
                                        "sample_count": 4,
                                        "win_rate": 0.0,
                                        "expectancy": -1.4,
                                        "helpful_score": -9.5,
                                        "confidence": 0.82,
                                        "quality_warnings": ["price_missing"],
                                        "prior_statuses": ["degraded"],
                                        "reasons": [
                                            "quality_warning_source_page",
                                            "quality_warning:price_missing",
                                            "quality_warning_source_prior_status:degraded",
                                        ],
                                    }
                                ],
                            },
                        },
                    }
                ),
            ),
        )

    result = service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-quality-pressure")

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    assert links[0]["metadata"]["jue_wiki_quality_summary"] == {
        "row_count": 2,
        "status_counts": {"partial": 1, "weak": 1},
        "top_warnings": [
            {"warning": "price_missing", "count": 2},
            {"warning": "valuation_stale_gt_30d", "count": 1},
        ],
        "warning_page_ids": {
            "price_missing": ["kis.symbol.005930", "kis.symbol.277810"],
            "valuation_stale_gt_30d": ["kis.symbol.005930"],
        },
        "weak_page_ids": ["kis.symbol.005930"],
        "caution_page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
    }
    assert links[0]["metadata"]["jue_wiki_quality_pressure_action_plan"] == {
        "status": "repair_required",
        "hard_blocker": False,
        "decision_policy": (
            "use_quality_warnings_as_candidate_level_cross_checks_not_blanket_holds"
        ),
        "required_adjustments": [
            {
                "adjustment_type": "candidate_level_cross_check",
                "reason": "weak_wiki_pages",
                "page_ids": ["kis.symbol.005930"],
                "resolution": "refresh_or_cross_check_before_sizing",
            },
            {
                "adjustment_type": "quality_warning_resolution",
                "warning": "price_missing",
                "count": 2,
                "resolution": "refresh_or_cross_check_before_sizing",
                "page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
            },
        ],
        "repair_focus": [
            {
                "priority_type": "evidence_quality",
                "warning": "price_missing",
                "count": 2,
                "page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
                "effectiveness_status": "degraded",
                "effectiveness": {
                    "warning": "price_missing",
                    "page_id": "quality_warning.price_missing",
                    "status": "degraded",
                    "sample_count": 4,
                    "expectancy": -0.7,
                },
                "decision_use": "evidence_quality_cross_check",
            }
        ],
        "quality_effectiveness_pressure": {
            "status": "repair_required",
            "degraded_warnings": ["price_missing"],
        },
        "caution_page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
    }
    assert links[0]["metadata"]["jue_wiki_quality_warning_source_summary"] == {
        "source_count": 1,
        "degraded_count": 1,
        "top_degraded_sources": [
            {
                "page_id": "kis.symbol.277810",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 4,
                "win_rate": 0.0,
                "expectancy": -1.4,
                "helpful_score": -9.5,
                "confidence": 0.82,
                "quality_warnings": ["price_missing"],
                "prior_statuses": ["degraded"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:price_missing",
                    "quality_warning_source_prior_status:degraded",
                ],
            }
        ],
    }


def test_project_selection_outcomes_marks_unresolved_wiki_action_pressure_as_loss(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-wiki-pressure",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["binance.ops.action_pressure"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "jue_wiki_action_pressure_contract": {
                            "status": "active",
                            "page_ids": ["binance.ops.action_pressure"],
                            "core_rule": "resolve candidate backlog",
                        },
                    }
                ),
                json.dumps({"hold_decision": {"summary": "관망"}}),
                json.dumps({"create_blocks": []}),
            ),
        )

    service.project_decision_links(binance_db_path=binance_db)
    result = service.project_selection_outcomes(binance_db_path=binance_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:binance-wiki-pressure"
    )
    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="binance.ops.action_pressure",
        decision_scope="binance",
        venue="binance",
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "missed_wiki_action_pressure"
    assert outcomes[0]["outcome_status"] == "loss"
    assert outcomes[0]["return_pct"] == -0.09
    assert outcomes[0]["evidence"]["reason"] == "wiki_action_pressure_without_resolution"
    assert outcomes[0]["evidence"]["page_ids"] == ["binance.ops.action_pressure"]
    assert metric["status"] == "degraded"


def test_project_selection_outcomes_marks_unresolved_wiki_quality_pressure_as_loss(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T10:10:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-quality-pressure-loss",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.005930"],
                            "quality_summary": {
                                "row_count": 1,
                                "status_counts": {"weak": 1},
                                "top_warnings": [
                                    {"warning": "price_missing", "count": 1}
                                ],
                                "warning_page_ids": {
                                    "price_missing": [
                                        "kis.symbol.005930",
                                        "kis.symbol.277810",
                                    ]
                                },
                                "weak_page_ids": ["kis.symbol.005930"],
                                "caution_page_ids": ["kis.symbol.005930"],
                            },
                            "quality_pressure_action_plan": {
                                "status": "repair_required",
                                "hard_blocker": False,
                                "decision_policy": (
                                    "use_quality_warnings_as_candidate_level_cross_checks_not_blanket_holds"
                                ),
                                "required_adjustments": [
                                    {
                                        "adjustment_type": (
                                            "candidate_level_cross_check"
                                        ),
                                        "reason": "weak_wiki_pages",
                                        "page_ids": ["kis.symbol.005930"],
                                        "resolution": (
                                            "refresh_or_cross_check_before_sizing"
                                        ),
                                    }
                                ],
                                "repair_focus": [
                                    {
                                        "priority_type": "evidence_quality",
                                        "warning": "price_missing",
                                        "count": 1,
                                        "page_ids": [
                                            "kis.symbol.005930",
                                            "kis.symbol.277810",
                                        ],
                                        "decision_use": (
                                            "evidence_quality_cross_check"
                                        ),
                                    }
                                ],
                                "caution_page_ids": ["kis.symbol.005930"],
                            },
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                    }
                ),
                json.dumps({"hold_decision": {"summary": "그냥 관망"}}),
                json.dumps({"create_blocks": []}),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-quality-pressure-loss"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "missed_wiki_quality_pressure"
    assert outcomes[0]["outcome_status"] == "loss"
    assert outcomes[0]["return_pct"] == -0.07
    assert outcomes[0]["evidence"]["reason"] == "wiki_quality_pressure_without_resolution"
    assert outcomes[0]["evidence"]["warnings"] == ["price_missing"]
    assert outcomes[0]["evidence"]["quality_warnings"] == ["price_missing"]
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "price_missing": ["kis.symbol.005930"]
    }
    assert outcomes[0]["evidence"]["repair_focus_page_ids"] == ["kis.symbol.005930"]
    assert outcomes[0]["evidence"]["caution_page_ids"] == ["kis.symbol.005930"]
    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="quality_warning.price_missing",
        decision_scope="kis",
        venue="kis",
    )
    assert metric["sample_count"] == 1
    assert metric["status"] == "degraded"
    assert metric["expectancy"] == -0.07
    source_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
    )
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
    )
    assert source_metric["sample_count"] == 1
    assert source_metric["status"] == "degraded"
    assert unrelated_metric["status"] == "missing"
    assert "quality_warning_source_page" in source_metric["reasons"]


def test_project_selection_outcomes_records_resolved_wiki_quality_pressure_as_flat(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:40:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": (
                                "selection:binance-quality-pressure-flat"
                            ),
                            "prompt_mode": "assist",
                            "selected_page_ids": ["binance.symbol.BTCUSDT"],
                            "quality_summary": {
                                "row_count": 1,
                                "status_counts": {"weak": 1},
                                "top_warnings": [
                                    {"warning": "funding_missing", "count": 1}
                                ],
                                "weak_page_ids": ["binance.symbol.BTCUSDT"],
                                "caution_page_ids": ["binance.symbol.BTCUSDT"],
                            },
                            "quality_pressure_action_plan": {
                                "status": "repair_required",
                                "hard_blocker": False,
                                "required_adjustments": [
                                    {
                                        "adjustment_type": (
                                            "quality_warning_resolution"
                                        ),
                                        "warning": "funding_missing",
                                        "count": 1,
                                        "resolution": (
                                            "refresh_or_cross_check_before_sizing"
                                        ),
                                    }
                                ],
                                "repair_focus": [
                                    {
                                        "priority_type": "evidence_quality",
                                        "warning": "funding_missing",
                                        "count": 1,
                                        "decision_use": (
                                            "evidence_quality_cross_check"
                                        ),
                                    }
                                ],
                                "caution_page_ids": ["binance.symbol.BTCUSDT"],
                            },
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                    }
                ),
                json.dumps(
                    {
                        "hold_decision": {
                            "summary": "펀딩 데이터 확인 후 대기",
                            "next_triggers": [
                                {
                                    "symbol": "BTCUSDT",
                                    "trigger": "funding refresh then retest",
                                }
                            ],
                        }
                    }
                ),
                json.dumps({"create_blocks": []}),
            ),
        )

    service.project_decision_links(binance_db_path=binance_db)
    result = service.project_selection_outcomes(binance_db_path=binance_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:binance-quality-pressure-flat"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "resolved_wiki_quality_pressure"
    assert outcomes[0]["outcome_status"] == "flat"
    assert outcomes[0]["return_pct"] == 0.0
    assert outcomes[0]["evidence"]["reason"] == (
        "wiki_quality_pressure_resolved_without_block"
    )
    assert outcomes[0]["evidence"]["warnings"] == ["funding_missing"]
    assert outcomes[0]["evidence"]["next_triggers"] == [
        {"symbol": "BTCUSDT", "trigger": "funding refresh then retest"}
    ]


def test_project_selection_outcomes_records_resolved_wiki_action_pressure_as_flat(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T09:50:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-wiki-pressure-flat",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.ops.action_pressure"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "jue_wiki_action_pressure_contract": {
                            "status": "active",
                            "page_ids": ["kis.ops.action_pressure"],
                        },
                    }
                ),
                json.dumps(
                    {
                        "hold_decision": {
                            "summary": "대기블록 후보를 조건으로 남김",
                            "watch_symbols": ["005930"],
                            "next_triggers": [
                                {
                                    "symbol": "005930",
                                    "condition": "pullback <= 70000",
                                    "price": 70000,
                                    "reason": "저점 대기",
                                }
                            ],
                        }
                    }
                ),
                json.dumps({"create_blocks": []}),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-wiki-pressure-flat"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "resolved_wiki_action_pressure"
    assert outcomes[0]["outcome_status"] == "flat"
    assert outcomes[0]["return_pct"] == 0.0
    assert outcomes[0]["evidence"]["next_triggers"][0]["symbol"] == "005930"


def test_project_selection_outcomes_does_not_penalize_safety_gate_blocked_pressure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-kill",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["binance.symbol.ETHUSDT"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": True},
                        },
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "top_candidates": [{"symbol": "ETHUSDT"}],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(binance_db_path=binance_db)
    result = service.project_selection_outcomes(binance_db_path=binance_db)
    outcomes = service.list_selection_outcomes(selection_run_id="selection:binance-kill")

    assert result["status"] == "ok"
    assert result["projected_count"] == 0
    assert outcomes == []


def test_project_selection_outcomes_records_manager_contract_error_as_loss(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, error_message,
                prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:10:00+00:00",
                "error",
                "contract_error",
                "gpt-5.5",
                "validation_repair_resolution_missing_from_model",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-contract-error",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["binance.symbol.NEARUSDT"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "top_candidates": [{"symbol": "NEARUSDT"}],
                        },
                        "validation_repair": {
                            "status": "risk_repair",
                            "repair_item_count": 1,
                            "constraint_count": 1,
                        },
                    }
                ),
                json.dumps(
                    {
                        "contract_error": (
                            "validation_repair_resolution_missing_from_model"
                        ),
                        "final_action_count": 0,
                        "hold_decision": {"summary": "generic wait"},
                    }
                ),
                json.dumps({"create_blocks": []}),
            ),
        )

    service.project_decision_links(binance_db_path=binance_db)
    result = service.project_selection_outcomes(binance_db_path=binance_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:binance-contract-error"
    )
    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="binance.symbol.NEARUSDT",
        decision_scope="binance",
        venue="binance",
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert len(outcomes) == 1
    assert outcomes[0]["outcome_kind"] == "manager_contract_error"
    assert outcomes[0]["outcome_status"] == "loss"
    assert outcomes[0]["return_pct"] == -0.12
    assert outcomes[0]["evidence"]["reason"] == (
        "validation_repair_resolution_missing_from_model"
    )
    assert outcomes[0]["evidence"]["proactive_pressure_status"] == "action_required"
    assert metric["status"] == "degraded"


def test_project_selection_outcomes_records_market_judgment_contract_error_as_loss(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    market_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(market_db) as conn:
        conn.executescript(
            """
            CREATE TABLE judgment_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                market_session TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                query TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                source_snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO judgment_runs (
                run_at, market_session, status, mode, model, query, error_message,
                prompt_json, response_json, source_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T01:10:00+00:00",
                "regular",
                "error",
                "error",
                "gpt-5.5",
                "장중 판단",
                "validation_repair_resolution_missing_from_model",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:market-contract-error",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.market.regime-test"],
                        },
                        "jue_wiki_validation_repair_contract": {
                            "status": "repair_required",
                            "requires_validation_repair_resolution": True,
                            "top_disciplines": ["regime_test"],
                            "repair_action_ids": ["refresh_regime_validation"],
                            "allowed_entry_postures": [
                                "waiting_entry_with_validation_repair_resolution"
                            ],
                            "risk_budget_multiplier": 0.5,
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                    }
                ),
                json.dumps({}),
                json.dumps({"focus_symbols": ["005930"]}),
            ),
        )

    service.project_decision_links(market_judgment_db_path=market_db)
    result = service.project_selection_outcomes(market_judgment_db_path=market_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:market-contract-error"
    )
    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="kis.market.regime-test",
        decision_scope="kis",
        venue="kis",
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert len(outcomes) == 1
    assert outcomes[0]["outcome_kind"] == "manager_contract_error"
    assert outcomes[0]["outcome_status"] == "loss"
    assert outcomes[0]["evidence"]["source"] == "kis_market_judgment_contract_error"
    assert outcomes[0]["evidence"]["reason"] == (
        "validation_repair_resolution_missing_from_model"
    )
    assert outcomes[0]["evidence"]["market_judgment_context"] == {
        "market_session": "regular",
        "query": "장중 판단",
        "source_snapshot": {"focus_symbols": ["005930"]},
    }
    assert metric["status"] == "degraded"


def test_project_decision_links_preserves_market_judgment_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    market_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(market_db) as conn:
        conn.executescript(
            """
            CREATE TABLE judgment_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                market_session TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                query TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                source_snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO judgment_runs (
                run_at, market_session, status, mode, model, query, error_message,
                prompt_json, response_json, source_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T05:45:00+00:00",
                "closing_watch",
                "ok",
                "llm",
                "gpt-5.5",
                "마감 전 리스크 점검",
                "",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:market-context",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.market.closing-watch"],
                        },
                        "decision_inputs": [
                            "account",
                            "quotes",
                            "jue_wiki_application",
                        ],
                    }
                ),
                json.dumps({"judgments": []}),
                json.dumps(
                    {
                        "focus_symbols": ["005930", "000660"],
                        "clock": {"session": "closing_watch", "is_open": True},
                        "account": {
                            "status": "ok",
                            "cash_krw": 1_234_567,
                            "positions": [
                                {"symbol": "005930", "qty": 3},
                                {"symbol": "000660", "qty": 1},
                            ],
                        },
                        "quotes": [
                            {"symbol": "005930", "price": 81200},
                            {"symbol": "000660", "price": 301000},
                        ],
                        "large_payload": "x" * 2000,
                    }
                ),
            ),
        )

    result = service.project_decision_links(market_judgment_db_path=market_db)
    links = service.list_decision_links(selection_run_id="selection:market-context")

    assert result["status"] == "ok"
    assert result["inserted_count"] == 1
    assert links[0]["decision_type"] == "market_judgment"
    assert links[0]["metadata"]["market_judgment_context"] == {
        "market_session": "closing_watch",
        "query": "마감 전 리스크 점검",
        "source_snapshot": {
            "focus_symbols": ["005930", "000660"],
            "clock": {"session": "closing_watch", "is_open": True},
            "account": {
                "status": "ok",
                "cash_krw": 1_234_567.0,
                "position_count": 2,
            },
            "quote_count": 2,
            "quote_symbols": ["005930", "000660"],
        },
    }


def test_project_decision_links_uses_market_session_specific_wiki_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    metric_page_id = JueWikiApplicationService._usage_guidance_page_id(
        category="risk_posture",
        value="supporting_cross_check",
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": metric_page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "short",
            "sample_count": 3,
            "win_rate": 0.0,
            "expectancy": -0.02,
            "avg_return_pct": -0.02,
            "median_mae_pct": -0.03,
            "drawdown_pressure": 0.03,
            "helpful_score": -3.0,
            "confidence": 1.0,
            "status": "degraded",
            "reasons": ["short usage degraded"],
        }
    )
    service.wiki.upsert_page_effectiveness(
        {
            "page_id": metric_page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "market_session:regular",
            "sample_count": 3,
            "win_rate": 1.0,
            "expectancy": 0.03,
            "avg_return_pct": 0.03,
            "median_mae_pct": -0.01,
            "drawdown_pressure": 0.01,
            "helpful_score": 3.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["regular session usage active"],
        }
    )
    market_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(market_db) as conn:
        conn.executescript(
            """
            CREATE TABLE judgment_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                market_session TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                query TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                source_snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO judgment_runs (
                run_at, market_session, status, mode, model, query, error_message,
                prompt_json, response_json, source_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T01:10:00+00:00",
                "regular",
                "ok",
                "llm",
                "gpt-5.5",
                "장중 판단",
                "",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": (
                                "selection:market-session-effectiveness"
                            ),
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.market.regime-effectiveness"],
                        },
                        "jue_wiki": {
                            "pages": [
                                {
                                    "page_id": "kis.market.regime-effectiveness",
                                    "page_type": "market",
                                    "confidence": 0.8,
                                    "quality_status": "partial",
                                }
                            ],
                        },
                    }
                ),
                json.dumps({"judgments": []}),
                json.dumps({"focus_symbols": ["005930"]}),
            ),
        )

    result = service.project_decision_links(market_judgment_db_path=market_db)
    links = service.list_decision_links(
        selection_run_id="selection:market-session-effectiveness"
    )
    effectiveness = links[0]["metadata"]["selected_wiki_pages"]["pages"][0][
        "usage_guidance_effectiveness"
    ]
    metric = effectiveness["metrics"][0]

    assert result["status"] == "ok"
    assert effectiveness["status"] == "active"
    assert metric["status"] == "active"
    assert metric["horizon"] == "market_session:regular"
    assert metric["reasons"] == ["regular session usage active"]


def test_market_snapshot_compact_preserves_explicit_zero_state() -> None:
    assert JueWikiApplicationService._compact_market_snapshot_account(
        {
            "status": "ok",
            "cash_krw": 0,
            "orderable_cash_krw": 0,
            "total_equity_krw": 0,
            "positions": [],
        }
    ) == {
        "status": "ok",
        "cash_krw": 0.0,
        "orderable_cash_krw": 0.0,
        "total_equity_krw": 0.0,
        "position_count": 0,
    }
    assert JueWikiApplicationService._compact_market_snapshot_quotes([]) == {
        "quote_count": 0,
        "quote_symbols": [],
    }


def test_project_selection_outcomes_records_market_judgment_resolved_validation_repair(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    market_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(market_db) as conn:
        conn.executescript(
            """
            CREATE TABLE judgment_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                market_session TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                query TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                source_snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO judgment_runs (
                run_at, market_session, status, mode, model, query, error_message,
                prompt_json, response_json, source_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T02:00:00+00:00",
                "regular",
                "ok",
                "llm",
                "gpt-5.5",
                "장중 판단",
                "",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:market-validation-resolved",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.market.regime-resolved"],
                        },
                        "jue_wiki_validation_repair_contract": {
                            "version": "jue_wiki_validation_repair_contract_v1",
                            "status": "repair_required",
                            "requires_validation_repair_resolution": True,
                            "top_disciplines": ["regime_test"],
                            "repair_action_ids": ["refresh_regime_validation"],
                            "allowed_entry_postures": [
                                "waiting_entry_with_validation_repair_resolution"
                            ],
                            "risk_budget_multiplier": 0.5,
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                    }
                ),
                json.dumps(
                    {
                        "validation_repair_resolution": {
                            "required": "jue_wiki_validation_repair_contract",
                            "blanket_hold_allowed": False,
                            "resolved_count": 1,
                            "resolved_candidates": [
                                {
                                    "symbol": "005930",
                                    "resolution": "regime_confirmed_wait",
                                    "horizon": "short_term",
                                    "next_trigger": (
                                        "시장 breadth와 거래대금이 동시에 회복되면 "
                                        "삼성전자 신규 후보를 다시 점검한다"
                                    ),
                                    "evidence_gap": (
                                        "장중 판단은 현재 국면 확인 전 신규 진입을 "
                                        "보류해야 한다"
                                    ),
                                }
                            ],
                        },
                        "judgments": [],
                    }
                ),
                json.dumps({"focus_symbols": ["005930"]}),
            ),
        )

    service.project_decision_links(market_judgment_db_path=market_db)
    result = service.project_selection_outcomes(market_judgment_db_path=market_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:market-validation-resolved"
    )
    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="kis.market.regime-resolved",
        decision_scope="kis",
        venue="kis",
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert len(outcomes) == 1
    assert outcomes[0]["outcome_kind"] == "resolved_validation_probe"
    assert outcomes[0]["outcome_status"] == "flat"
    assert outcomes[0]["evidence"]["source"] == "kis_market_judgment_validation_repair_contract"
    assert outcomes[0]["evidence"]["reason"] == (
        "validation_repair_probe_contract_resolved_without_block"
    )
    assert outcomes[0]["evidence"]["resolution"]["resolved_candidates"][0][
        "resolution"
    ] == "regime_confirmed_wait"
    assert outcomes[0]["evidence"]["market_judgment_context"] == {
        "market_session": "regular",
        "query": "장중 판단",
        "source_snapshot": {"focus_symbols": ["005930"]},
    }
    assert metric["status"] == "active"


def test_project_page_effectiveness_separates_market_judgment_by_session(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    market_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(market_db) as conn:
        conn.executescript(
            """
            CREATE TABLE judgment_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                market_session TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                query TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                source_snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        for idx, (session, status, error_message, response) in enumerate(
            [
                (
                    "regular",
                    "ok",
                    "",
                    {
                        "validation_repair_resolution": {
                            "required": "jue_wiki_validation_repair_contract",
                            "blanket_hold_allowed": False,
                            "resolved_count": 1,
                            "resolved_candidates": [
                                {
                                    "symbol": "005930",
                                    "resolution": "regime_confirmed_wait",
                                    "horizon": "short_term",
                                    "next_trigger": "정규장 breadth 회복 시 재점검",
                                    "evidence_gap": "마감 전 수급 왜곡과 분리해 정규장만 검증",
                                }
                            ],
                        }
                    },
                ),
                (
                    "closing_watch",
                    "error",
                    "validation_repair_resolution_missing_from_model",
                    {},
                ),
            ]
        ):
            conn.execute(
                """
                INSERT INTO judgment_runs (
                    run_at, market_session, status, mode, model, query, error_message,
                    prompt_json, response_json, source_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"2026-07-01T0{idx + 2}:00:00+00:00",
                    session,
                    status,
                    "llm" if status == "ok" else "error",
                    "gpt-5.5",
                    "장중 판단",
                    error_message,
                    json.dumps(
                        {
                            "jue_wiki_application": {
                                "selection_run_id": (
                                    f"selection:market-session-{session}"
                                ),
                                "prompt_mode": "assist",
                                "selected_page_ids": ["kis.market.regime-session"],
                            },
                            "jue_wiki_validation_repair_contract": {
                                "version": "jue_wiki_validation_repair_contract_v1",
                                "status": "repair_required",
                                "requires_validation_repair_resolution": True,
                                "top_disciplines": ["regime_test"],
                                "repair_action_ids": ["refresh_regime_validation"],
                                "allowed_entry_postures": [
                                    "waiting_entry_with_validation_repair_resolution"
                                ],
                                "risk_budget_multiplier": 0.5,
                            },
                            "execution_gate": {
                                "status": "ok",
                                "kill_switch": {"enabled": False},
                            },
                        }
                    ),
                    json.dumps(response),
                    json.dumps({"focus_symbols": ["005930"]}),
                ),
            )

    service.project_decision_links(market_judgment_db_path=market_db)
    service.project_selection_outcomes(market_judgment_db_path=market_db)
    service.project_page_effectiveness(min_samples=1)
    regular_metric = service.page_effectiveness(
        page_id="kis.market.regime-session",
        decision_scope="kis",
        venue="kis",
        horizon="market_session:regular",
    )
    closing_metric = service.page_effectiveness(
        page_id="kis.market.regime-session",
        decision_scope="kis",
        venue="kis",
        horizon="market_session:closing_watch",
    )

    assert regular_metric["sample_count"] == 1
    assert regular_metric["status"] == "active"
    assert closing_metric["sample_count"] == 1
    assert closing_metric["status"] == "degraded"


def test_project_selection_outcomes_marks_unresolved_repair_contract_as_loss(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-repair",
                            "prompt_mode": "assist",
                            "selected_page_ids": [
                                "kis.symbol.277810",
                            ],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 2,
                            "top_priorities": [
                                {
                                    "page_id": "kis.symbol.277810",
                                    "page_type": "symbol",
                                    "symbols": ["277810"],
                                    "symbol_overlap": ["277810"],
                                    "sample_count": 10,
                                    "win_rate": 0.2,
                                    "expectancy": -0.9,
                                    "helpful_score": -10,
                                    "quality_warnings": ["financials_missing"],
                                    "repair_action": "probe with smaller sizing",
                                    "impacted_page_ids": ["kis.symbol.277810"],
                                    "impacted_symbols": ["277810"],
                                    "repair_targets": [
                                        {
                                            "page_id": "kis.symbol.277810",
                                            "symbol": "277810",
                                            "recommended_action": (
                                                "refresh_symbol_financials_and_"
                                                "rewrite_page_evidence"
                                            ),
                                        }
                                    ],
                                    "repair_target_effectiveness": {
                                        "page_id": (
                                            "repair_target."
                                            "refresh_symbol_financials_and_"
                                            "rewrite_page_evidence"
                                        ),
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
                                            (
                                                "repair_target:"
                                                "refresh_symbol_financials_and_"
                                                "rewrite_page_evidence"
                                            ),
                                            "repair_target_prior_status:degraded",
                                        ],
                                    },
                                }
                            ],
                            "required_resolution": "repair priority",
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-repair")
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(selection_run_id="selection:kis-repair")

    assert links[0]["metadata"]["jue_wiki_repair_contract"]["status"] == "active"
    assert links[0]["metadata"]["jue_wiki_repair_contract"]["top_priorities"][0][
        "page_id"
    ] == "kis.symbol.277810"
    assert links[0]["metadata"]["jue_wiki_repair_contract"]["top_priorities"][0][
        "quality_warnings"
    ] == ["financials_missing"]
    assert links[0]["metadata"]["jue_wiki_repair_contract"]["top_priorities"][0][
        "repair_targets"
    ] == [
        {
            "page_id": "kis.symbol.277810",
            "symbol": "277810",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert links[0]["metadata"]["jue_wiki_repair_contract"]["top_priorities"][0][
        "repair_target_effectiveness"
    ] == {
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
    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "missed_repair_priority"
    assert outcomes[0]["outcome_status"] == "loss"
    assert outcomes[0]["return_pct"] == -0.08
    assert outcomes[0]["evidence"]["reason"] == (
        "repair_priority_without_block_or_candidate_resolution"
    )
    assert outcomes[0]["evidence"]["top_symbols"] == ["277810"]
    assert outcomes[0]["evidence"]["quality_warnings"] == ["financials_missing"]
    assert outcomes[0]["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.277810",
            "symbol": "277810",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]
    assert outcomes[0]["evidence"]["repair_target_effectiveness"] == [
        {
            "page_id": (
                "repair_target.refresh_symbol_financials_and_rewrite_page_evidence"
            ),
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
    ]
    assert outcomes[0]["evidence"]["repair_target_effectiveness_statuses"] == [
        "degraded"
    ]

    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="quality_warning.financials_missing",
        decision_scope="kis",
        venue="kis",
        horizon="",
    )
    repair_metric = service.page_effectiveness(
        page_id=(
            "repair_target.refresh_symbol_financials_and_rewrite_page_evidence"
        ),
        decision_scope="kis",
        venue="kis",
        horizon="",
    )
    assert metric["status"] == "degraded"
    assert metric["sample_count"] == 1
    assert repair_metric["status"] == "degraded"
    assert repair_metric["sample_count"] == 1
    assert repair_metric["expectancy"] == -0.08
    assert (
        "repair_target:refresh_symbol_financials_and_rewrite_page_evidence"
        in repair_metric["reasons"]
    )
    assert "repair_target_prior_status:degraded" in repair_metric["reasons"]


def test_project_decision_links_does_not_synthesize_repair_contract_priorities_when_count_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-repair-zero",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.277810"],
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 0,
                            "top_priorities": [
                                {
                                    "page_id": "kis.symbol.277810",
                                    "page_type": "symbol",
                                    "symbols": ["277810"],
                                    "quality_warnings": ["financials_missing"],
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-repair-zero")
    repair_contract = links[0]["metadata"]["jue_wiki_repair_contract"]

    assert repair_contract["repair_priority_count"] == 0
    assert "top_priorities" not in repair_contract


def test_project_selection_outcomes_preserves_audit_repair_contract_provenance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:05:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-audit-repair",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.005930"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 1,
                            "top_priorities": [
                                {
                                    "page_id": (
                                        "decision_adjustment_audit.kis."
                                        "supporting_evidence."
                                        "audit_preferred_risk_posture_before_shift."
                                        "repair_probe"
                                    ),
                                    "page_type": "policy",
                                    "priority_type": "decision_adjustment_audit",
                                    "symbols": ["005930"],
                                    "symbol_overlap": ["005930"],
                                    "source_type": (
                                        "jue_wiki_decision_adjustment_audit_metric"
                                    ),
                                    "source_id": (
                                        "kis:supporting_evidence:"
                                        "audit_preferred_risk_posture_before_shift:"
                                        "repair_probe"
                                    ),
                                    "action_type": (
                                        "repair_decision_adjustment_audit_contract"
                                    ),
                                    "decision_use": (
                                        "decision_adjustment_audit_repair"
                                    ),
                                    "repair_status": "repair_required",
                                    "quality_status": "degraded",
                                    "quality_warnings": [
                                        "decision_adjustment_audit_degraded"
                                    ],
                                    "repair_action": (
                                        "repair degraded decision adjustment audit "
                                        "contract before reusing repair_probe "
                                        "escalation"
                                    ),
                                    "repair_loop_status": "repair_required",
                                    "repair_loop_sample_count": 3,
                                    "repair_loop_missed_count": 2,
                                    "repair_loop_resolved_count": 0,
                                    "repair_loop_resolution_rate": 0.0,
                                    "repair_loop_action_type": (
                                        "repair_decision_adjustment_audit_contract"
                                    ),
                                }
                            ],
                            "repair_loop_effectiveness": {
                                "status": "repair_required",
                                "sample_count": 3,
                                "missed_count": 2,
                                "resolved_count": 0,
                                "resolution_rate": 0.0,
                                "metric_count": 1,
                                "repair_required_count": 1,
                                "top_degraded": [
                                    {
                                        "decision_scope": "kis",
                                        "priority_type": (
                                            "decision_adjustment_audit"
                                        ),
                                        "action_type": f"aaa_active_{idx}",
                                        "decision_use": (
                                            "decision_adjustment_audit_repair"
                                        ),
                                        "source_id": f"active:{idx}",
                                        "sample_count": 5,
                                        "missed_count": 1,
                                        "resolved_count": 4,
                                        "resolution_rate": 0.8,
                                        "status": "active",
                                    }
                                    for idx in range(6)
                                ]
                                + [
                                    {
                                        "decision_scope": "kis",
                                        "priority_type": (
                                            "decision_adjustment_audit"
                                        ),
                                        "action_type": (
                                            "repair_decision_adjustment_audit_contract"
                                        ),
                                        "decision_use": (
                                            "decision_adjustment_audit_repair"
                                        ),
                                        "source_id": (
                                            "kis:supporting_evidence:"
                                            "audit_preferred_risk_posture_before_shift:"
                                            "repair_probe"
                                        ),
                                        "sample_count": 3,
                                        "missed_count": 2,
                                        "resolved_count": 0,
                                        "resolution_rate": 0.0,
                                        "status": "repair_required",
                                    }
                                ],
                                "repair_loop_status_metrics": [
                                    {
                                        "decision_scope": "kis",
                                        "repair_loop_status": "active",
                                        "action_type": f"aaa_active_{idx}",
                                        "sample_count": 5,
                                        "missed_count": 1,
                                        "resolved_count": 4,
                                        "resolution_rate": 0.8,
                                        "status": "active",
                                    }
                                    for idx in range(6)
                                ]
                                + [
                                    {
                                        "decision_scope": "kis",
                                        "repair_loop_status": "repair_required",
                                        "priority_type": (
                                            "decision_adjustment_audit"
                                        ),
                                        "action_type": (
                                            "repair_decision_adjustment_audit_contract"
                                        ),
                                        "decision_use": (
                                            "decision_adjustment_audit_repair"
                                        ),
                                        "sample_count": 3,
                                        "missed_count": 2,
                                        "resolved_count": 0,
                                        "resolution_rate": 0.0,
                                        "status": "repair_required",
                                        "loop_sample_count": 3,
                                        "loop_missed_count": 2,
                                        "loop_resolved_count": 0,
                                        "loop_resolution_rate": 0.0,
                                    },
                                    {
                                        "decision_scope": "kis",
                                        "repair_loop_status": "repair_required",
                                        "priority_type": (
                                            "decision_adjustment_audit"
                                        ),
                                        "action_type": (
                                            "repair_decision_adjustment_audit_contract"
                                        ),
                                        "decision_use": (
                                            "decision_adjustment_audit_repair"
                                        ),
                                        "sample_count": 2,
                                        "missed_count": 1,
                                        "resolved_count": 1,
                                        "resolution_rate": 0.5,
                                        "status": "repair_required",
                                    }
                                ],
                                "repair_success_criteria_metrics": [
                                    {
                                        "decision_scope": "kis",
                                        "criterion": (
                                            "audit_contract_repaired_or_demoted"
                                        ),
                                        "sample_count": 3,
                                        "missed_count": 2,
                                        "resolved_count": 1,
                                        "resolution_rate": 1 / 3,
                                        "status": "repair_required",
                                    }
                                ],
                            },
                            "required_resolution": "repair audit priority",
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(selection_run_id="selection:kis-audit-repair")
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-audit-repair"
    )

    priority = links[0]["metadata"]["jue_wiki_repair_contract"]["top_priorities"][0]
    assert priority["source_id"] == (
        "kis:supporting_evidence:"
        "audit_preferred_risk_posture_before_shift:repair_probe"
    )
    assert priority["decision_use"] == "decision_adjustment_audit_repair"
    assert priority["repair_loop_status"] == "repair_required"
    assert priority["repair_loop_sample_count"] == 3
    assert priority["repair_loop_missed_count"] == 2
    assert priority["repair_loop_resolved_count"] == 0
    assert priority["repair_loop_resolution_rate"] == 0.0
    assert priority["repair_loop_action_type"] == (
        "repair_decision_adjustment_audit_contract"
    )
    assert links[0]["metadata"]["jue_wiki_repair_contract"][
        "repair_loop_effectiveness"
    ]["top_degraded"][0]["source_id"] == (
        "kis:supporting_evidence:"
        "audit_preferred_risk_posture_before_shift:repair_probe"
    )
    assert links[0]["metadata"]["jue_wiki_repair_contract"][
        "repair_loop_effectiveness"
    ]["repair_loop_status_metrics"][0] == {
        "decision_scope": "kis",
        "repair_loop_status": "repair_required",
        "action_type": "repair_decision_adjustment_audit_contract",
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 0,
        "resolution_rate": 0.0,
        "status": "repair_required",
        "loop_sample_count": 3,
        "loop_missed_count": 2,
        "loop_resolved_count": 0,
        "loop_resolution_rate": 0.0,
    }
    assert links[0]["metadata"]["jue_wiki_repair_contract"][
        "repair_loop_effectiveness"
    ]["repair_loop_status_summary"] == {
        "metric_count": 8,
        "repair_required_count": 2,
        "probe_count": 0,
        "active_count": 6,
        "unknown_count": 0,
        "worst_status": "repair_required",
        "primary_repair_action_type": "repair_decision_adjustment_audit_contract",
        "repair_required_action_types": [
            "repair_decision_adjustment_audit_contract"
        ],
        "top_missed_action_types": [
            "repair_decision_adjustment_audit_contract"
        ],
        "repair_action_targets": [
            {
                "decision_scope": "kis",
                "status": "repair_required",
                "action_type": "repair_decision_adjustment_audit_contract",
                "primary_decision_use": "decision_adjustment_audit_repair",
                "decision_uses": ["decision_adjustment_audit_repair"],
                "priority_types": ["decision_adjustment_audit"],
                "sample_count": 5,
                "missed_count": 3,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "miss_rate": 0.6,
                "repair_pressure_score": 1.8,
                "recommended_resolution": "repair_contract_then_probe_or_downgrade",
                "resolution_steps": [
                    "repair_audit_contract",
                    "use_probe_or_downgrade_until_resolved",
                    "record_repair_outcome",
                ],
                "resolution_success_criteria": [
                    "audit_contract_repaired_or_demoted",
                    "probe_or_downgrade_result_recorded",
                    "future_reuse_has_outcome_link",
                ],
                "metric_count": 2,
            }
        ],
        "max_missed_count": 2,
        "max_sample_count": 5,
        "min_resolution_rate": 0.0,
    }
    assert links[0]["metadata"]["jue_wiki_repair_contract"][
        "repair_loop_effectiveness"
    ]["repair_success_criteria_summary"] == {
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
    assert links[0]["metadata"]["jue_wiki_repair_contract"][
        "repair_loop_effectiveness"
    ]["repair_learning_directive_summary"] == {
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
    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "missed_repair_priority"
    assert outcomes[0]["evidence"]["repair_priority_types"] == [
        "decision_adjustment_audit"
    ]
    assert outcomes[0]["evidence"]["repair_action_types"] == [
        "repair_decision_adjustment_audit_contract"
    ]
    assert outcomes[0]["evidence"]["repair_source_ids"] == [
        "kis:supporting_evidence:"
        "audit_preferred_risk_posture_before_shift:repair_probe"
    ]
    assert outcomes[0]["evidence"]["repair_decision_uses"] == [
        "decision_adjustment_audit_repair"
    ]
    assert outcomes[0]["evidence"]["repair_loop_statuses"] == ["repair_required"]
    assert outcomes[0]["evidence"]["repair_loop_action_types"] == [
        "repair_decision_adjustment_audit_contract"
    ]
    assert outcomes[0]["evidence"]["repair_loop_sample_counts"] == [3]
    assert outcomes[0]["evidence"]["repair_loop_missed_counts"] == [2]
    assert outcomes[0]["evidence"]["repair_loop_resolved_counts"] == [0]
    assert outcomes[0]["evidence"]["repair_loop_resolution_rates"] == [0.0]
    assert outcomes[0]["evidence"]["repair_resolution_success_criteria"] == [
        "audit_contract_repaired_or_demoted",
        "probe_or_downgrade_result_recorded",
        "future_reuse_has_outcome_link",
    ]
    assert outcomes[0]["evidence"]["repair_learning_recommended_actions"] == [
        "repair_or_demote_success_criterion_before_reuse"
    ]
    assert outcomes[0]["evidence"]["repair_learning_action_targets"] == [
        {
            "decision_scope": "kis",
            "status": "repair_required",
            "recommended_action": (
                "repair_or_demote_success_criterion_before_reuse"
            ),
            "recommended_resolution": "revise_learning_directive_then_probe",
            "resolution_steps": [
                "inspect_failed_repair_directive_outcomes",
                "revise_or_demote_learning_directive",
                "record_next_outcome_before_reuse",
            ],
            "sample_count": 3,
            "missed_count": 2,
            "resolved_count": 1,
            "resolution_rate": 1 / 3,
            "miss_rate": 2 / 3,
            "repair_pressure_score": round(2 * (2 / 3), 6),
            "metric_count": 1,
        }
    ]
    assert outcomes[0]["evidence"]["repair_learning_resolution_steps"] == [
        "inspect_failed_repair_directive_outcomes",
        "revise_or_demote_learning_directive",
        "record_next_outcome_before_reuse",
    ]
    assert [
        row["resolution_step"]
        for row in outcomes[0]["evidence"]["repair_learning_step_targets"]
    ] == [
        "inspect_failed_repair_directive_outcomes",
        "record_next_outcome_before_reuse",
        "revise_or_demote_learning_directive",
    ]
    assert outcomes[0]["evidence"]["repair_learning_step_targets"][0] == {
        "decision_scope": "kis",
        "status": "repair_required",
        "resolution_step": "inspect_failed_repair_directive_outcomes",
        "recommended_resolution": "revise_repair_step_contract_then_probe",
        "resolution_steps": [
            "inspect_failed_resolution_step_outcomes",
            "revise_repair_step_contract",
            "record_next_outcome_before_reuse",
        ],
        "sample_count": 3,
        "missed_count": 2,
        "resolved_count": 1,
        "resolution_rate": 1 / 3,
        "miss_rate": 2 / 3,
        "repair_pressure_score": round(2 * (2 / 3), 6),
        "metric_count": 1,
    }
    assert outcomes[0]["evidence"]["repair_learning_step_recommended_resolutions"] == [
        "revise_repair_step_contract_then_probe"
    ]
    assert outcomes[0]["evidence"]["repair_learning_resolution_targets"] == [
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
    ]


def test_project_selection_outcomes_does_not_penalize_safety_gate_blocked_repair(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-repair-kill",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.277810"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": True},
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 1,
                            "top_priorities": [
                                {
                                    "page_id": "kis.symbol.277810",
                                    "symbols": ["277810"],
                                    "repair_action": "probe with smaller sizing",
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-repair-kill"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 0
    assert outcomes == []


def test_record_selection_outcomes_omits_empty_quality_warnings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:no-warning-repair",
        manager_run_id="kis-manager-no-warning",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        venue="kis",
    )

    result = service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="missed_repair_priority",
        outcome_status="loss",
        evidence={
            "source": "kis_jue_wiki_repair_contract",
            "quality_warnings": [],
        },
    )

    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:no-warning-repair"
    )

    assert result["status"] == "ok"
    assert "quality_warnings" not in outcomes[0]["evidence"]


def test_record_selection_outcomes_deduplicates_cleaned_empty_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:clean-evidence-idempotent",
        manager_run_id="manager-clean",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        venue="kis",
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="missed_repair_priority",
        outcome_status="loss",
        evidence={"source": "repair", "quality_warnings": []},
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="missed_repair_priority",
        outcome_status="loss",
        evidence={"source": "repair"},
    )

    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:clean-evidence-idempotent"
    )

    assert len(outcomes) == 1
    assert outcomes[0]["evidence"] == {"source": "repair"}


def test_record_selection_outcomes_removes_existing_semantic_duplicate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:semantic-duplicate",
        manager_run_id="manager-duplicate",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        venue="kis",
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', '', '', ?, ?, ?, ?)
            """,
            (
                "legacy-duplicate",
                link["link_id"],
                "selection:semantic-duplicate",
                "kis.symbol.005930",
                "kis",
                "kis",
                "closed_block",
                "loss",
                json.dumps({"source": "repair"}, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        evidence={"source": "repair"},
    )

    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:semantic-duplicate"
    )

    assert len(outcomes) == 1
    assert outcomes[0]["outcome_id"] != "legacy-duplicate"


def test_backfill_outcome_selected_page_evidence_removes_semantic_duplicate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "quality_warnings": ["financials_missing"],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-dedupe",
        manager_run_id="manager-backfill-dedupe",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        venue="kis",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        evidence={},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', '', '', ?, ?, ?, ?)
            """,
            (
                "legacy-missing-selected-page",
                link["link_id"],
                "selection:backfill-dedupe",
                "kis.symbol.005930",
                "kis",
                "kis",
                "closed_block",
                "loss",
                "{}",
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-dedupe"
    )

    assert updated == 1
    assert len(outcomes) == 1
    assert outcomes[0]["evidence"] == {"selected_wiki_page": selected_page}


def test_backfill_outcome_selected_page_evidence_repairs_stale_selected_page_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "quality_status": "active",
        "usage_guidance": {
            "trust_level": "high",
            "risk_posture": "normal",
            "required_cross_checks": ["live_quote"],
        },
    }
    stale_selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["277810"],
        "quality_status": "weak",
        "usage_guidance": {
            "trust_level": "low",
            "risk_posture": "repair_cross_check",
            "required_cross_checks": ["manual_identity_recheck"],
        },
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-stale-selected-page",
        manager_run_id="manager-backfill-stale-selected-page",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-stale-selected-page",
                link["link_id"],
                "selection:backfill-stale-selected-page",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {"selected_wiki_page": stale_selected_page},
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-stale-selected-page"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["selected_wiki_page"] == selected_page
    assert service._outcome_row_is_attributable(outcomes[0]) is True


def test_backfill_outcome_selected_page_evidence_enriches_existing_selected_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "repair_targets": [
            {
                "page_id": "kis.symbol.005930",
                "symbol": "005930",
                "recommended_action": action,
            }
        ],
        "repair_target_effectiveness": [
            {
                "page_id": f"repair_target.{action}",
                "status": "degraded",
                "sample_count": 3,
                "helpful_score": -6.0,
                "reasons": [
                    f"repair_target:{action}",
                    "repair_target_prior_status:degraded",
                ],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-enrich-selected-page",
        manager_run_id="manager-backfill-enrich-selected-page",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    legacy_evidence = {
        "selected_wiki_page": {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "symbols": ["005930"],
        }
    }
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-without-repair",
                link["link_id"],
                "selection:backfill-enrich-selected-page",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(legacy_evidence, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-enrich-selected-page"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["selected_wiki_page"] == selected_page
    assert outcomes[0]["evidence"]["repair_targets"] == selected_page[
        "repair_targets"
    ]
    assert outcomes[0]["evidence"]["repair_target_effectiveness_statuses"] == [
        "degraded"
    ]

    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id=f"repair_target.{action}",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert metric["status"] == "degraded"
    assert f"repair_target:{action}" in metric["reasons"]
    assert "repair_target_prior_status:degraded" in metric["reasons"]


def test_record_selection_outcomes_promotes_selected_page_repair_queue_to_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
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
                "actions": [
                    {
                        "action_type": "repair_application_repair_queue_pressure",
                        "status": "scheduled",
                        "quality_warnings": ["application_repair_queue_pressure"],
                    }
                ],
            },
        },
    }
    link = service.record_decision_link(
        selection_run_id="selection:selected-page-repair-queue-effectiveness",
        manager_run_id="manager-selected-page-repair-queue-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )

    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="loss",
        return_pct=-0.8,
        mae_pct=-1.1,
        evidence={},
    )
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:selected-page-repair-queue-effectiveness"
    )

    assert outcomes[0]["evidence"]["selected_wiki_page"] == selected_page
    assert outcomes[0]["evidence"]["jue_wiki_repair_queue"] == {
        "open_count": 1,
        "open_action_batches": [
            {
                "action_type": "repair_application_repair_queue_pressure",
                "count": 1,
                "warnings": ["application_repair_queue_pressure"],
            }
        ],
    }

    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert metric["status"] == "degraded"
    assert "application_repair_queue_pressure" in metric["reasons"]
    assert "repair_queue_open_count:1" in metric["reasons"]
    assert (
        "repair_queue_action:repair_application_repair_queue_pressure"
        in metric["reasons"]
    )


def test_backfill_outcome_selected_page_evidence_adds_usage_guidance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    usage_guidance = {
        "trust_level": "low",
        "risk_posture": "repair_cross_check",
        "decision_use": "use weak page only after live cross-checks",
        "allowed_uses": ["waiting_block", "small_probe_block"],
        "required_cross_checks": [
            "live_quote",
            "fresh_financials_or_valuation_cross_check",
        ],
        "hard_blocker": False,
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "quality_status": "weak",
        "quality_warnings": ["financials_missing"],
        "usage_guidance": usage_guidance,
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-usage-guidance",
        manager_run_id="manager-backfill-usage-guidance",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    legacy_evidence = {
        "selected_wiki_page": {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "symbols": ["005930"],
        }
    }
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-without-usage-guidance",
                link["link_id"],
                "selection:backfill-usage-guidance",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(legacy_evidence, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-usage-guidance"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["selected_wiki_page"] == selected_page
    assert outcomes[0]["evidence"]["usage_guidance"] == usage_guidance
    assert outcomes[0]["evidence"]["usage_guidance_required_cross_checks"] == [
        "live_quote",
        "fresh_financials_or_valuation_cross_check",
    ]


def test_backfill_outcome_selected_page_evidence_repairs_contaminated_usage_guidance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_guidance = {
        "trust_level": "high",
        "risk_posture": "normal",
        "decision_use": "eligible_for_standard_block_design",
        "allowed_uses": ["standard_block", "waiting_block"],
        "required_cross_checks": ["live_quote", "sector_flow"],
        "hard_blocker": False,
    }
    legacy_guidance = {
        "trust_level": "low",
        "risk_posture": "repair_cross_check",
        "decision_use": "legacy_unrelated_repair_only",
        "allowed_uses": ["small_probe_block"],
        "required_cross_checks": [
            "stale_financials_repair",
            "manual_identity_recheck",
        ],
        "hard_blocker": False,
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "usage_guidance": selected_guidance,
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-contaminated-usage-guidance",
        manager_run_id="manager-backfill-contaminated-usage-guidance",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-contaminated-usage-guidance",
                link["link_id"],
                "selection:backfill-contaminated-usage-guidance",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "usage_guidance": legacy_guidance,
                        "usage_guidance_required_cross_checks": [
                            "stale_financials_repair",
                            "manual_identity_recheck",
                        ],
                        "usage_guidance_risk_posture": "repair_cross_check",
                        "usage_guidance_trust_level": "low",
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-contaminated-usage-guidance"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["usage_guidance"] == selected_guidance
    assert outcomes[0]["evidence"]["usage_guidance_required_cross_checks"] == [
        "live_quote",
        "sector_flow",
    ]
    assert outcomes[0]["evidence"]["usage_guidance_risk_posture"] == "normal"
    assert outcomes[0]["evidence"]["usage_guidance_trust_level"] == "high"


def test_backfill_outcome_selected_page_evidence_adds_usage_guidance_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    usage_guidance = {
        "trust_level": "low",
        "risk_posture": "repair_cross_check",
        "required_cross_checks": ["live_quote"],
        "hard_blocker": False,
    }
    usage_effectiveness = {
        "status": "degraded",
        "metrics": [
            {
                "page_id": "usage_guidance.risk_posture.repair_cross_check",
                "status": "degraded",
                "sample_count": 4,
                "helpful_score": -5.5,
                "reasons": [
                    "usage_guidance:risk_posture:repair_cross_check",
                    "usage_guidance_trust_level:low",
                ],
            },
            {
                "page_id": "usage_guidance.cross_check.live_quote",
                "status": "probe",
                "sample_count": 1,
                "helpful_score": 0.0,
                "reasons": ["usage_guidance:cross_check:live_quote"],
            },
        ],
        "decision_use": (
            "prior usage guidance is degraded; audit this page usage before "
            "letting it shape block design"
        ),
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "usage_guidance": usage_guidance,
        "usage_guidance_effectiveness": usage_effectiveness,
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-usage-guidance-effectiveness",
        manager_run_id="manager-backfill-usage-guidance-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-without-usage-guidance-effectiveness",
                link["link_id"],
                "selection:backfill-usage-guidance-effectiveness",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -1.2,
                -1.8,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        }
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-usage-guidance-effectiveness"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["usage_guidance_effectiveness"] == (
        usage_effectiveness
    )
    assert outcomes[0]["evidence"]["usage_guidance_effectiveness_statuses"] == [
        "degraded",
        "probe",
    ]

    service.project_page_effectiveness(min_samples=1)
    posture_metric = service.page_effectiveness(
        page_id="usage_guidance.risk_posture.repair_cross_check",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert "usage_guidance_prior_status:degraded" in posture_metric["reasons"]


def test_backfill_outcome_selected_page_evidence_adds_memory_card_quality_warning(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    memory_card_quality = {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "required_action": "cross_check_live_research_before_high_confidence",
        "missing_fields": ["durable_facts", "lessons"],
        "required_checks": [
            "refresh_durable_facts",
            "inspect_block_lessons",
        ],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "memory_card_quality": memory_card_quality,
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-memory-card-quality",
        manager_run_id="manager-backfill-memory-card-quality",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-with-memory-card-quality",
                link["link_id"],
                "selection:backfill-memory-card-quality",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -1.2,
                -1.8,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        }
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-memory-card-quality"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["memory_card_quality"] == memory_card_quality
    assert outcomes[0]["evidence"]["memory_card_quality_statuses"] == ["active"]
    assert outcomes[0]["evidence"]["quality_warnings"] == [
        "memory_card_quality_unresolved"
    ]

    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="quality_warning.memory_card_quality_unresolved",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert metric["status"] == "degraded"
    assert "quality_warning:memory_card_quality_unresolved" in metric["reasons"]


def test_project_page_effectiveness_tracks_memory_card_quality_detail_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    memory_card_quality = {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "required_action": "cross_check_live_research_before_high_confidence",
        "missing_fields": ["durable_facts", "lessons"],
        "required_checks": [
            "refresh_durable_facts",
            "inspect_block_lessons",
        ],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }
    for idx, return_pct in enumerate((-1.4, -0.8), start=1):
        selected_page = {
            "page_id": f"kis.symbol.24545{idx}",
            "page_type": "symbol",
            "symbols": [f"24545{idx}"],
            "memory_card_quality": memory_card_quality,
        }
        link = service.record_decision_link(
            selection_run_id=f"selection:memory-card-quality-detail-{idx}",
            manager_run_id=f"manager-memory-card-quality-detail-{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=[selected_page["page_id"]],
            symbol=f"24545{idx}",
            venue="kis",
            horizon="mid",
            metadata={"selected_wiki_pages": {"pages": [selected_page]}},
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="loss",
            return_pct=return_pct,
            mae_pct=return_pct,
        )

    result = service.project_page_effectiveness(min_samples=2)
    missing_field_metric = service.page_effectiveness(
        page_id="memory_card_quality.missing_field.durable_facts",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    required_check_metric = service.page_effectiveness(
        page_id="memory_card_quality.required_check.refresh_durable_facts",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )

    assert result["memory_card_quality_updated_count"] == 4
    assert missing_field_metric["status"] == "degraded"
    assert missing_field_metric["sample_count"] == 2
    assert "memory_card_quality:missing_field:durable_facts" in missing_field_metric[
        "reasons"
    ]
    assert "memory_card_quality_status:active" in missing_field_metric["reasons"]
    assert "memory_card_quality_resolution:unresolved" in missing_field_metric[
        "reasons"
    ]
    assert required_check_metric["status"] == "degraded"
    assert "memory_card_quality:required_check:refresh_durable_facts" in (
        required_check_metric["reasons"]
    )


def test_backfill_outcome_selected_page_evidence_adds_memory_card_quality_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    memory_card_quality = {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "missing_fields": ["durable_facts"],
        "required_checks": ["refresh_durable_facts"],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }
    memory_card_quality_effectiveness = {
        "status": "degraded",
        "metrics": [
            {
                "page_id": "memory_card_quality.missing_field.durable_facts",
                "status": "degraded",
                "sample_count": 4,
                "helpful_score": -6.0,
                "reasons": [
                    "memory_card_quality:missing_field:durable_facts",
                    "memory_card_quality_status:active",
                ],
            },
            {
                "page_id": "memory_card_quality.required_check.refresh_durable_facts",
                "status": "active",
                "sample_count": 4,
                "helpful_score": 4.0,
                "reasons": [
                    "memory_card_quality:required_check:refresh_durable_facts",
                    "memory_card_quality_resolution:unresolved",
                ],
            },
        ],
        "decision_use": (
            "prior memory card quality evidence is degraded; resolve missing "
            "memory fields and required checks before confident block design"
        ),
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "memory_card_quality": memory_card_quality,
        "memory_card_quality_effectiveness": memory_card_quality_effectiveness,
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-memory-card-quality-effectiveness",
        manager_run_id="manager-backfill-memory-card-quality-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-with-memory-card-quality-effectiveness",
                link["link_id"],
                "selection:backfill-memory-card-quality-effectiveness",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -1.1,
                -1.7,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        }
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-memory-card-quality-effectiveness"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["memory_card_quality_effectiveness"] == (
        memory_card_quality_effectiveness
    )
    assert outcomes[0]["evidence"]["memory_card_quality_effectiveness_statuses"] == [
        "degraded",
        "active",
    ]

    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id="memory_card_quality.missing_field.durable_facts",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert "memory_card_quality_prior_status:degraded" in metric["reasons"]


def test_backfill_outcome_selected_page_evidence_filters_memory_card_quality_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    memory_card_quality = {
        "status": "active",
        "resolution": "unresolved",
        "symbols": ["005930"],
        "missing_fields": ["durable_facts"],
        "required_checks": ["refresh_durable_facts"],
        "decision_use": "memory_card_quality_resolution_check",
        "candidate_resolution_required": True,
    }
    selected_metric = {
        "page_id": "memory_card_quality.missing_field.durable_facts",
        "status": "active",
        "sample_count": 4,
        "helpful_score": 4.0,
        "reasons": [
            "memory_card_quality:missing_field:durable_facts",
            "memory_card_quality_status:active",
        ],
    }
    unrelated_metric = {
        "page_id": "memory_card_quality.missing_field.open_questions",
        "status": "degraded",
        "sample_count": 4,
        "helpful_score": -6.0,
        "reasons": [
            "memory_card_quality:missing_field:open_questions",
            "memory_card_quality_status:active",
        ],
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "memory_card_quality": memory_card_quality,
        "memory_card_quality_effectiveness": {
            "status": "degraded",
            "metrics": [selected_metric, unrelated_metric],
            "decision_use": "mixed legacy memory card quality effectiveness",
        },
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-filter-memory-card-quality-effectiveness",
        manager_run_id="manager-backfill-filter-memory-card-quality-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-contaminated-memory-card-quality-effectiveness",
                link["link_id"],
                "selection:backfill-filter-memory-card-quality-effectiveness",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.0,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "memory_card_quality_effectiveness": {
                            "status": "degraded",
                            "metrics": [selected_metric, unrelated_metric],
                            "decision_use": (
                                "mixed legacy memory card quality effectiveness"
                            ),
                        },
                        "memory_card_quality_effectiveness_statuses": [
                            "degraded",
                        ],
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-filter-memory-card-quality-effectiveness"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["memory_card_quality_effectiveness"] == {
        "status": "active",
        "metrics": [selected_metric],
        "decision_use": "mixed legacy memory card quality effectiveness",
    }
    assert outcomes[0]["evidence"]["memory_card_quality_effectiveness_statuses"] == [
        "active"
    ]

    service.project_page_effectiveness(min_samples=1)
    unrelated = service.page_effectiveness(
        page_id="memory_card_quality.missing_field.open_questions",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert unrelated["status"] == "missing"


def test_backfill_outcome_selected_page_evidence_enriches_source_ref_repair_queue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "source_refs": [
            {
                "source_type": "wiki_repair_queue",
                "action_type": action,
                "symbols": ["005930"],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-source-ref-repair",
        manager_run_id="manager-backfill-source-ref-repair",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    legacy_evidence = {
        "selected_wiki_page": {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "symbols": ["005930"],
        }
    }
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-with-source-ref-repair",
                link["link_id"],
                "selection:backfill-source-ref-repair",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(legacy_evidence, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-source-ref-repair"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": action,
        }
    ]


def test_backfill_outcome_selected_page_evidence_enriches_nested_repair_queue(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "source_refs": [
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "wiki_repair_queue",
                        "action_type": action,
                        "symbols": ["005930"],
                    }
                ],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-nested-repair",
        manager_run_id="manager-backfill-nested-repair",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-with-nested-repair",
                link["link_id"],
                "selection:backfill-nested-repair",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        }
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-nested-repair"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": action,
        }
    ]


def test_backfill_outcome_selected_page_evidence_enriches_nested_repair_target_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "source_refs": [
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "wiki_repair_queue",
                        "action_type": action,
                        "symbols": ["005930"],
                        "repair_target_effectiveness": [
                            {
                                "page_id": f"repair_target.{action}",
                                "status": "degraded",
                                "sample_count": 4,
                                "helpful_score": -6.0,
                                "reasons": [
                                    f"repair_target:{action}",
                                    "repair_target_prior_status:degraded",
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-nested-repair-effectiveness",
        manager_run_id="manager-backfill-nested-repair-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-with-nested-repair-effectiveness",
                link["link_id"],
                "selection:backfill-nested-repair-effectiveness",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        }
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-nested-repair-effectiveness"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": action,
        }
    ]
    assert outcomes[0]["evidence"]["repair_target_effectiveness"] == [
        {
            "page_id": f"repair_target.{action}",
            "status": "degraded",
            "sample_count": 4,
            "helpful_score": -6.0,
            "reasons": [
                f"repair_target:{action}",
                "repair_target_prior_status:degraded",
            ],
        }
    ]
    assert outcomes[0]["evidence"]["repair_target_effectiveness_statuses"] == [
        "degraded"
    ]

    service.project_page_effectiveness(min_samples=1)
    metric = service.page_effectiveness(
        page_id=f"repair_target.{action}",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert metric["status"] == "degraded"
    assert f"repair_target:{action}" in metric["reasons"]
    assert "repair_target_prior_status:degraded" in metric["reasons"]


def test_backfill_outcome_selected_page_evidence_adds_quality_pressure_provenance(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-quality-pressure",
        manager_run_id="manager-backfill-quality-pressure",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {"pages": [selected_page]},
            "jue_wiki_quality_summary": {
                "row_count": 1,
                "status_counts": {"weak": 1},
                "top_warnings": [{"warning": "price_missing", "count": 1}],
                "warning_page_ids": {
                    "price_missing": [
                        "kis.symbol.005930",
                        "kis.symbol.277810",
                    ],
                },
            },
            "jue_wiki_quality_pressure_action_plan": {
                "status": "repair_required",
                "hard_blocker": False,
                "required_adjustments": [
                    {
                        "adjustment_type": "quality_warning_resolution",
                        "warning": "price_missing",
                        "count": 1,
                        "page_ids": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    }
                ],
                "repair_focus": [
                    {
                        "priority_type": "evidence_quality",
                        "warning": "price_missing",
                        "count": 1,
                        "page_ids": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    }
                ],
                "caution_page_ids": ["kis.symbol.005930"],
            },
        },
    )
    legacy_evidence = {
        "selected_wiki_page": {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "symbols": ["005930"],
        }
    }
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-without-quality-pressure",
                link["link_id"],
                "selection:backfill-quality-pressure",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(legacy_evidence, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-quality-pressure"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "price_missing": ["kis.symbol.005930"]
    }
    assert outcomes[0]["evidence"]["repair_focus_page_ids"] == ["kis.symbol.005930"]
    assert outcomes[0]["evidence"]["quality_warnings"] == ["price_missing"]
    assert outcomes[0]["evidence"]["caution_page_ids"] == ["kis.symbol.005930"]

    service.project_page_effectiveness(min_samples=1)
    warning_metric = service.page_effectiveness(
        page_id="quality_warning.price_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    source_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert warning_metric["status"] == "degraded"
    assert source_metric["status"] == "degraded"
    assert "quality_warning_source_page" in source_metric["reasons"]
    assert unrelated_metric["status"] == "missing"


def test_backfill_outcome_selected_page_evidence_adds_quality_warning_source_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_summary = {
        "source_count": 1,
        "degraded_count": 1,
        "top_degraded_sources": [
            {
                "page_id": "kis.symbol.277810",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 4,
                "win_rate": 0.0,
                "expectancy": -1.4,
                "helpful_score": -9.5,
                "confidence": 0.82,
                "quality_warnings": ["price_missing"],
                "prior_statuses": ["degraded"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:price_missing",
                    "quality_warning_source_prior_status:degraded",
                ],
            }
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-source-summary",
        manager_run_id="manager-backfill-source-summary",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.277810"],
        symbol="277810",
        venue="kis",
        horizon="mid",
        metadata={"jue_wiki_quality_warning_source_summary": source_summary},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-without-source-summary",
                link["link_id"],
                "selection:backfill-source-summary",
                "kis.symbol.277810",
                "kis",
                "kis",
                "277810",
                "mid",
                "closed_block",
                "loss",
                -0.9,
                -1.5,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.277810",
                            "page_type": "symbol",
                            "symbols": ["277810"],
                        }
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-source-summary"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["quality_warning_source_summary"] == source_summary
    assert outcomes[0]["evidence"]["quality_warnings"] == ["price_missing"]

    service.project_page_effectiveness(min_samples=1)
    source_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert source_metric["status"] == "degraded"
    assert "quality_warning_source_page" in source_metric["reasons"]
    assert (
        "quality_warning_source_prior_status:degraded" in source_metric["reasons"]
    )


def test_backfill_outcome_selected_page_evidence_adds_active_source_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_source = {
        "page_id": "kis.symbol.005930",
        "decision_scope": "kis",
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 9,
        "win_rate": 0.67,
        "expectancy": 1.2,
        "helpful_score": 6.5,
        "confidence": 0.9,
        "quality_warnings": ["valuation_stale"],
        "prior_statuses": ["active"],
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:valuation_stale",
            "quality_warning_source_prior_status:active",
        ],
    }
    source_summary = {
        "source_count": 2,
        "degraded_count": 1,
        "active_count": 1,
        "top_degraded_sources": [
            {
                "page_id": "kis.symbol.277810",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid",
                "sample_count": 4,
                "win_rate": 0.0,
                "expectancy": -1.4,
                "helpful_score": -9.5,
                "confidence": 0.82,
                "quality_warnings": ["price_missing"],
                "prior_statuses": ["degraded"],
                "reasons": [
                    "quality_warning_source_page",
                    "quality_warning:price_missing",
                    "quality_warning_source_prior_status:degraded",
                ],
            }
        ],
        "top_active_sources": [active_source],
    }
    legacy_summary = {
        "source_count": 1,
        "degraded_count": 1,
        "top_degraded_sources": source_summary["top_degraded_sources"],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-active-source-summary",
        manager_run_id="manager-backfill-active-source-summary",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"jue_wiki_quality_warning_source_summary": source_summary},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-source-summary-without-active-sources",
                link["link_id"],
                "selection:backfill-active-source-summary",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "quality_warning_source_summary": legacy_summary,
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-active-source-summary"
    )

    expected_summary = {
        "source_count": 1,
        "active_count": 1,
        "top_active_sources": [active_source],
    }
    assert updated == 1
    assert outcomes[0]["evidence"]["quality_warning_source_summary"] == expected_summary
    assert outcomes[0]["evidence"]["quality_warnings"] == ["valuation_stale"]
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "valuation_stale": ["kis.symbol.005930"],
    }

    service.project_page_effectiveness(min_samples=1)
    active_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert active_metric["status"] == "active"
    assert "quality_warning_source_page" in active_metric["reasons"]
    assert "quality_warning:valuation_stale" in active_metric["reasons"]
    assert (
        "quality_warning_source_prior_status:active" in active_metric["reasons"]
    )


def test_backfill_outcome_selected_page_evidence_repairs_contaminated_source_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_source = {
        "page_id": "kis.symbol.005930",
        "decision_scope": "kis",
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 9,
        "win_rate": 0.67,
        "expectancy": 1.2,
        "helpful_score": 6.5,
        "confidence": 0.9,
        "quality_warnings": ["valuation_stale"],
        "prior_statuses": ["active"],
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:valuation_stale",
            "quality_warning_source_prior_status:active",
        ],
    }
    unrelated_degraded_source = {
        "page_id": "kis.symbol.277810",
        "decision_scope": "kis",
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 4,
        "win_rate": 0.0,
        "expectancy": -1.4,
        "helpful_score": -9.5,
        "confidence": 0.82,
        "quality_warnings": ["price_missing"],
        "prior_statuses": ["degraded"],
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:price_missing",
            "quality_warning_source_prior_status:degraded",
        ],
    }
    contaminated_summary = {
        "source_count": 2,
        "active_count": 1,
        "degraded_count": 1,
        "top_active_sources": [active_source],
        "top_degraded_sources": [unrelated_degraded_source],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-contaminated-source-summary",
        manager_run_id="manager-backfill-contaminated-source-summary",
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
            },
            "jue_wiki_quality_warning_source_summary": contaminated_summary,
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-contaminated-source-summary",
                link["link_id"],
                "selection:backfill-contaminated-source-summary",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "quality_warning_source_summary": contaminated_summary,
                        "quality_warnings": [
                            "valuation_stale",
                            "price_missing",
                        ],
                        "warning_page_ids": {
                            "valuation_stale": ["kis.symbol.005930"],
                            "price_missing": ["kis.symbol.277810"],
                        },
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-contaminated-source-summary"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["quality_warning_source_summary"] == {
        "source_count": 1,
        "active_count": 1,
        "top_active_sources": [active_source],
    }
    assert outcomes[0]["evidence"]["quality_warnings"] == ["valuation_stale"]
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "valuation_stale": ["kis.symbol.005930"]
    }

    service.project_page_effectiveness(min_samples=1)
    unrelated_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert unrelated_metric["status"] == "missing"


def test_backfill_outcome_selected_page_evidence_repairs_contaminated_source_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_metric = {
        "page_id": "kis.symbol.005930",
        "status": "active",
        "sample_count": 9,
        "win_rate": 0.67,
        "expectancy": 1.2,
        "helpful_score": 6.5,
        "confidence": 0.9,
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:valuation_stale",
        ],
    }
    unrelated_metric = {
        "page_id": "kis.symbol.277810",
        "status": "degraded",
        "sample_count": 3,
        "win_rate": 0.0,
        "expectancy": -1.4,
        "helpful_score": -9.5,
        "confidence": 0.82,
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:price_missing",
        ],
    }
    contaminated_effectiveness = {
        "status": "degraded",
        "metrics": [selected_metric, unrelated_metric],
        "decision_use": "legacy mixed source effectiveness should be repaired",
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "quality_warning_source_effectiveness": contaminated_effectiveness,
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-contaminated-source-effectiveness",
        manager_run_id="manager-backfill-contaminated-source-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-contaminated-source-effectiveness",
                link["link_id"],
                "selection:backfill-contaminated-source-effectiveness",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "quality_warning_source_effectiveness": contaminated_effectiveness,
                        "quality_warning_source_effectiveness_statuses": [
                            "degraded"
                        ],
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-contaminated-source-effectiveness"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["quality_warning_source_effectiveness"] == {
        "status": "active",
        "metrics": [selected_metric],
        "decision_use": contaminated_effectiveness["decision_use"],
    }
    assert outcomes[0]["evidence"][
        "quality_warning_source_effectiveness_statuses"
    ] == ["active"]

    service.project_page_effectiveness(min_samples=1)
    unrelated_source_metric = service.page_effectiveness(
        page_id="kis.symbol.277810",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert unrelated_source_metric["status"] == "missing"


def test_backfill_outcome_selected_page_evidence_repairs_contaminated_warning_effectiveness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_warning_effectiveness = {
        "warning": "valuation_stale",
        "page_id": "kis.symbol.005930",
        "status": "active",
        "sample_count": 8,
        "helpful_score": 5.5,
        "reasons": [
            "quality_warning:valuation_stale",
            "quality_warning_prior_status:active",
        ],
    }
    unrelated_warning_effectiveness = {
        "warning": "price_missing",
        "page_id": "kis.symbol.277810",
        "status": "degraded",
        "sample_count": 3,
        "helpful_score": -6.0,
        "reasons": [
            "quality_warning:price_missing",
            "quality_warning_prior_status:degraded",
        ],
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "evidence_quality": {
            "warning_effectiveness": [
                selected_warning_effectiveness,
                unrelated_warning_effectiveness,
            ]
        },
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-contaminated-warning-effectiveness",
        manager_run_id="manager-backfill-contaminated-warning-effectiveness",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-contaminated-warning-effectiveness",
                link["link_id"],
                "selection:backfill-contaminated-warning-effectiveness",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "quality_warning_effectiveness": [
                            selected_warning_effectiveness,
                            unrelated_warning_effectiveness,
                        ],
                        "quality_warning_effectiveness_statuses": ["degraded"],
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-contaminated-warning-effectiveness"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["quality_warning_effectiveness"] == [
        selected_warning_effectiveness
    ]
    assert outcomes[0]["evidence"]["quality_warning_effectiveness_statuses"] == [
        "active"
    ]

    service.project_page_effectiveness(min_samples=1)
    unrelated_warning_metric = service.page_effectiveness(
        page_id="quality_warning.price_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert unrelated_warning_metric["status"] == "missing"


def test_backfill_outcome_selected_page_evidence_repairs_contaminated_repair_targets(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    action = "refresh_symbol_financials_and_rewrite_page_evidence"
    unrelated_action = "rebuild_unrelated_symbol_page"
    selected_target = {
        "page_id": "kis.symbol.005930",
        "symbol": "005930",
        "recommended_action": action,
    }
    unrelated_target = {
        "page_id": "kis.symbol.277810",
        "symbol": "277810",
        "recommended_action": unrelated_action,
    }
    selected_effectiveness = {
        "page_id": f"repair_target.{action}",
        "status": "active",
        "sample_count": 6,
        "helpful_score": 4.2,
        "reasons": [
            f"repair_target:{action}",
            "repair_target_prior_status:active",
        ],
    }
    unrelated_effectiveness = {
        "page_id": f"repair_target.{unrelated_action}",
        "status": "degraded",
        "sample_count": 3,
        "helpful_score": -7.0,
        "reasons": [
            f"repair_target:{unrelated_action}",
            "repair_target_prior_status:degraded",
        ],
    }
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
        "repair_targets": [selected_target, unrelated_target],
        "repair_target_effectiveness": [
            selected_effectiveness,
            unrelated_effectiveness,
        ],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-contaminated-repair-targets",
        manager_run_id="manager-backfill-contaminated-repair-targets",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={"selected_wiki_pages": {"pages": [selected_page]}},
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-contaminated-repair-targets",
                link["link_id"],
                "selection:backfill-contaminated-repair-targets",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "repair_targets": [selected_target, unrelated_target],
                        "repair_target_effectiveness": [
                            selected_effectiveness,
                            unrelated_effectiveness,
                        ],
                        "repair_target_effectiveness_statuses": ["degraded"],
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-contaminated-repair-targets"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["repair_targets"] == [selected_target]
    assert outcomes[0]["evidence"]["repair_target_effectiveness"] == [
        selected_effectiveness
    ]
    assert outcomes[0]["evidence"]["repair_target_effectiveness_statuses"] == [
        "active"
    ]

    service.project_page_effectiveness(min_samples=1)
    unrelated_repair_metric = service.page_effectiveness(
        page_id=f"repair_target.{unrelated_action}",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert unrelated_repair_metric["status"] == "missing"


def test_backfill_outcome_selected_page_evidence_repairs_quality_warnings_only_contamination(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    link = service.record_decision_link(
        selection_run_id="selection:backfill-quality-warnings-only-contamination",
        manager_run_id="manager-backfill-quality-warnings-only-contamination",
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
            },
            "jue_wiki_quality_summary": {
                "row_count": 1,
                "status_counts": {"weak": 1},
                "top_warnings": [{"warning": "price_missing", "count": 1}],
                "warning_page_ids": {
                    "price_missing": ["kis.symbol.277810"],
                },
            },
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-quality-warnings-only-contamination",
                link["link_id"],
                "selection:backfill-quality-warnings-only-contamination",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "quality_warnings": ["price_missing"],
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-quality-warnings-only-contamination"
    )

    assert updated == 1
    assert "quality_warnings" not in outcomes[0]["evidence"]
    assert "warning_page_ids" not in outcomes[0]["evidence"]

    service.project_page_effectiveness(min_samples=1)
    warning_metric = service.page_effectiveness(
        page_id="quality_warning.price_missing",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert warning_metric["status"] == "missing"


def test_backfill_outcome_selected_page_evidence_accepts_status_source_aliases(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    active_source = {
        "page_id": "kis.symbol.005930",
        "decision_scope": "kis",
        "venue": "kis",
        "horizon": "mid",
        "sample_count": 9,
        "win_rate": 0.67,
        "expectancy": 1.2,
        "helpful_score": 6.5,
        "confidence": 0.9,
        "quality_warnings": ["valuation_stale"],
        "prior_statuses": ["active"],
        "reasons": [
            "quality_warning_source_page",
            "quality_warning:valuation_stale",
            "quality_warning_source_prior_status:active",
        ],
    }
    status_style_summary = {
        "quality_warning_source_effectiveness_count": 1,
        "quality_warning_source_active_count": 1,
        "top_active_quality_warning_sources": [active_source],
    }
    legacy_summary = {
        "source_count": 0,
        "degraded_count": 0,
    }
    expected_summary = {
        "source_count": 1,
        "active_count": 1,
        "top_active_sources": [active_source],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-status-source-aliases",
        manager_run_id="manager-backfill-status-source-aliases",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={
            "jue_wiki_quality_warning_source_summary": status_style_summary
        },
    )
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-source-summary-with-status-aliases",
                link["link_id"],
                "selection:backfill-status-source-aliases",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "win",
                1.2,
                -0.4,
                json.dumps(
                    {
                        "selected_wiki_page": {
                            "page_id": "kis.symbol.005930",
                            "page_type": "symbol",
                            "symbols": ["005930"],
                        },
                        "quality_warning_source_summary": legacy_summary,
                    },
                    sort_keys=True,
                ),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-status-source-aliases"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["quality_warning_source_summary"] == expected_summary
    assert outcomes[0]["evidence"]["quality_warnings"] == ["valuation_stale"]

    service.project_page_effectiveness(min_samples=1)
    active_metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    assert active_metric["status"] == "active"
    assert "quality_warning_source_page" in active_metric["reasons"]
    assert (
        "quality_warning_source_prior_status:active" in active_metric["reasons"]
    )


def test_backfill_outcome_selected_page_evidence_completes_partial_quality_pressure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-partial-quality-pressure",
        manager_run_id="manager-backfill-partial-quality-pressure",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {"pages": [selected_page]},
            "jue_wiki_quality_summary": {
                "row_count": 1,
                "status_counts": {"weak": 1},
                "top_warnings": [{"warning": "price_missing", "count": 1}],
                "warning_page_ids": {
                    "price_missing": [
                        "kis.symbol.005930",
                        "kis.symbol.277810",
                    ],
                },
            },
            "jue_wiki_quality_pressure_action_plan": {
                "status": "repair_required",
                "hard_blocker": False,
                "repair_focus": [
                    {
                        "priority_type": "evidence_quality",
                        "warning": "price_missing",
                        "count": 1,
                        "page_ids": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    }
                ],
                "caution_page_ids": ["kis.symbol.005930"],
            },
        },
    )
    partial_evidence = {
        "selected_wiki_page": {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "symbols": ["005930"],
        },
        "warning_page_ids": {"price_missing": ["kis.symbol.005930"]},
        "quality_warnings": ["price_missing"],
    }
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-with-partial-quality-pressure",
                link["link_id"],
                "selection:backfill-partial-quality-pressure",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(partial_evidence, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-partial-quality-pressure"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "price_missing": ["kis.symbol.005930"]
    }
    assert outcomes[0]["evidence"]["repair_focus_page_ids"] == ["kis.symbol.005930"]


def test_backfill_outcome_selected_page_evidence_merges_complete_but_partial_quality_pressure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-complete-partial-quality-pressure",
        manager_run_id="manager-backfill-complete-partial-quality-pressure",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {"pages": [selected_page]},
            "jue_wiki_quality_summary": {
                "row_count": 1,
                "status_counts": {"weak": 1},
                "top_warnings": [{"warning": "price_missing", "count": 1}],
                "warning_page_ids": {
                    "price_missing": [
                        "kis.symbol.005930",
                        "kis.symbol.277810",
                    ],
                },
            },
            "jue_wiki_quality_pressure_action_plan": {
                "status": "repair_required",
                "hard_blocker": False,
                "repair_focus": [
                    {
                        "priority_type": "evidence_quality",
                        "warning": "price_missing",
                        "count": 1,
                        "page_ids": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    }
                ],
                "caution_page_ids": ["kis.symbol.005930"],
            },
        },
    )
    partial_evidence = {
        "selected_wiki_page": {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "symbols": ["005930"],
        },
        "warning_page_ids": {"price_missing": ["kis.symbol.005930"]},
        "repair_focus_page_ids": ["kis.symbol.005930"],
        "quality_warnings": ["price_missing"],
        "caution_page_ids": ["kis.symbol.005930"],
    }
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-complete-but-partial-quality-pressure",
                link["link_id"],
                "selection:backfill-complete-partial-quality-pressure",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(partial_evidence, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-complete-partial-quality-pressure"
    )

    assert updated == 0
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "price_missing": ["kis.symbol.005930"]
    }
    assert outcomes[0]["evidence"]["repair_focus_page_ids"] == ["kis.symbol.005930"]


def test_backfill_outcome_selected_page_evidence_reads_nested_quality_pressure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    selected_page = {
        "page_id": "kis.symbol.005930",
        "page_type": "symbol",
        "symbols": ["005930"],
    }
    link = service.record_decision_link(
        selection_run_id="selection:backfill-nested-quality-pressure",
        manager_run_id="manager-backfill-nested-quality-pressure",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        symbol="005930",
        venue="kis",
        horizon="mid",
        metadata={
            "selected_wiki_pages": {"pages": [selected_page]},
            "jue_wiki_application": {
                "quality_summary": {
                    "row_count": 1,
                    "status_counts": {"weak": 1},
                    "top_warnings": [
                        {"warning": "updated_at_stale_gt_14d", "count": 1}
                    ],
                    "warning_page_ids": {
                        "updated_at_stale_gt_14d": [
                            "kis.symbol.005930",
                            "kis.symbol.277810",
                        ],
                    },
                },
                "quality_pressure_action_plan": {
                    "status": "repair_required",
                    "hard_blocker": False,
                    "repair_focus": [
                        {
                            "priority_type": "evidence_quality",
                            "warning": "updated_at_stale_gt_14d",
                            "count": 1,
                            "page_ids": [
                                "kis.symbol.005930",
                                "kis.symbol.277810",
                            ],
                        }
                    ],
                    "caution_page_ids": ["kis.symbol.005930"],
                },
            },
        },
    )
    legacy_evidence = {
        "selected_wiki_page": {
            "page_id": "kis.symbol.005930",
            "page_type": "symbol",
            "symbols": ["005930"],
        }
    }
    with service.wiki._connect() as conn:
        conn.execute(
            """
            INSERT INTO wiki_selection_outcomes (
                outcome_id, link_id, selection_run_id, page_id, decision_scope,
                venue, symbol, block_id, horizon, outcome_kind, outcome_status,
                return_pct, mae_pct, evidence_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-selected-page-without-nested-quality-pressure",
                link["link_id"],
                "selection:backfill-nested-quality-pressure",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "mid",
                "closed_block",
                "loss",
                -0.7,
                -0.9,
                json.dumps(legacy_evidence, sort_keys=True),
                "2026-06-28T00:00:00+00:00",
            ),
        )

    updated = service.backfill_outcome_selected_page_evidence()
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:backfill-nested-quality-pressure"
    )

    assert updated == 1
    assert outcomes[0]["evidence"]["warning_page_ids"] == {
        "updated_at_stale_gt_14d": ["kis.symbol.005930"]
    }
    assert outcomes[0]["evidence"]["repair_focus_page_ids"] == ["kis.symbol.005930"]
    assert outcomes[0]["evidence"]["quality_warnings"] == [
        "updated_at_stale_gt_14d"
    ]


def test_project_selection_outcomes_marks_unresolved_validation_probe_as_loss(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-validation-probe",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.risk.trading_validation"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
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
                                    "repair_action_id": (
                                        "validation_repair.cost_evidence_repair"
                                    ),
                                    "entry_bias": "waiting_entry_until_cost_edge_clean",
                                    "allowed_entry_posture": (
                                        "shadow_or_waiting_entry_only"
                                    ),
                                    "blocks_new_entries": (
                                        "scale_up_and_unvalidated_immediate_entries"
                                    ),
                                    "risk_budget_multiplier": 0.25,
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(
        selection_run_id="selection:kis-validation-probe"
    )
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-validation-probe"
    )

    assert links[0]["metadata"]["validation_repair"]["discipline_ids"] == [
        "cost_simulation"
    ]
    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "missed_validation_probe"
    assert outcomes[0]["outcome_status"] == "loss"
    assert outcomes[0]["return_pct"] == -0.06
    assert outcomes[0]["evidence"]["reason"] == (
        "validation_repair_probe_contract_without_block"
    )
    assert outcomes[0]["evidence"]["discipline_ids"] == ["cost_simulation"]


def test_project_decision_links_does_not_synthesize_validation_repair_backlog_when_counts_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-validation-zero",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.risk.trading_validation"],
                        },
                        "validation_repair": {
                            "version": "validation_repair_prompt_v1",
                            "scope": "kis",
                            "status": "clear",
                            "repair_item_count": 0,
                            "constraint_count": 0,
                            "repair_backlog": [
                                {
                                    "discipline_id": "cost_simulation",
                                    "repair_action_id": (
                                        "validation_repair.cost_evidence_repair"
                                    ),
                                }
                            ],
                            "block_design_constraints": [
                                {
                                    "discipline_id": "mdd_limit",
                                    "scale_blocker": "drawdown_guard_missing",
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    links = service.list_decision_links(
        selection_run_id="selection:kis-validation-zero"
    )
    validation_repair = links[0]["metadata"]["validation_repair"]

    assert validation_repair["repair_item_count"] == 0
    assert validation_repair["constraint_count"] == 0
    assert "discipline_ids" not in validation_repair
    assert "repair_action_ids" not in validation_repair
    assert "scale_blockers" not in validation_repair


def test_project_selection_outcomes_uses_jue_wiki_validation_repair_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:35:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": (
                                "selection:kis-validation-contract-outcome"
                            ),
                            "prompt_mode": "assist",
                            "selected_page_ids": [
                                "kis.validation.walk_forward_analysis"
                            ],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "jue_wiki_validation_repair_contract": {
                            "version": "jue_wiki_validation_repair_contract_v1",
                            "status": "repair_required",
                            "requires_validation_repair_resolution": True,
                            "top_disciplines": ["walk_forward_analysis"],
                            "repair_action_ids": ["run_walk_forward_replay"],
                            "entry_biases": ["depth_checked_probe"],
                            "allowed_entry_postures": ["depth_checked_probe"],
                            "blocked_entry_patterns": ["unvalidated_scale_up"],
                            "risk_budget_multiplier": 0.25,
                            "contract_feedback_gap": {
                                "status": "missing_contract_outcomes",
                                "legacy_sample_count": 4,
                                "contract_sample_count": 0,
                                "required_response": (
                                    "record validation_repair_resolution and "
                                    "resolved_candidates so future wiki updates "
                                    "can measure contract effectiveness"
                                ),
                            },
                            "contract_basis_pressure_summary": {
                                "sample_count": 7,
                                "missed_count": 5,
                                "resolved_count": 2,
                                "resolution_rate": 2 / 7,
                                "miss_rate": 5 / 7,
                                "repair_pressure_score": round(5 * (5 / 7), 6),
                                "status": "repair_required",
                            },
                            "degraded_metric_evidence": [
                                {
                                    "discipline_id": "walk_forward_analysis",
                                    "repair_action_id": "run_walk_forward_replay",
                                    "entry_bias": "depth_checked_probe",
                                    "sample_count": 7,
                                    "missed_count": 5,
                                    "resolved_count": 2,
                                    "resolution_rate": 2 / 7,
                                    "status": "repair_required",
                                    "source_counts": {
                                        "kis_validation_repair_contract": 7
                                    },
                                }
                            ],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-validation-contract-outcome"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "missed_validation_probe"
    assert outcomes[0]["evidence"]["source"] == "kis_validation_repair_contract"
    assert outcomes[0]["evidence"]["discipline_ids"] == [
        "walk_forward_analysis"
    ]
    assert outcomes[0]["evidence"]["repair_action_ids"] == [
        "run_walk_forward_replay"
    ]
    assert outcomes[0]["evidence"]["allowed_entry_postures"] == [
        "depth_checked_probe"
    ]
    assert outcomes[0]["evidence"]["blocks_new_entries"] == [
        "unvalidated_scale_up"
    ]
    assert outcomes[0]["evidence"]["contract_feedback_gap"] == {
        "status": "missing_contract_outcomes",
        "legacy_sample_count": 4,
        "contract_sample_count": 0,
        "required_response": (
            "record validation_repair_resolution and resolved_candidates so "
            "future wiki updates can measure contract effectiveness"
        ),
    }
    assert outcomes[0]["evidence"]["contract_basis_pressure_summary"] == {
        "sample_count": 7,
        "missed_count": 5,
        "resolved_count": 2,
        "resolution_rate": 2 / 7,
        "miss_rate": 5 / 7,
        "repair_pressure_score": round(5 * (5 / 7), 6),
        "status": "repair_required",
    }
    assert outcomes[0]["evidence"]["degraded_metric_evidence"] == [
        {
            "discipline_id": "walk_forward_analysis",
            "repair_action_id": "run_walk_forward_replay",
            "entry_bias": "depth_checked_probe",
            "sample_count": 7,
            "missed_count": 5,
            "resolved_count": 2,
            "resolution_rate": 2 / 7,
            "status": "repair_required",
            "source_counts": {"kis_validation_repair_contract": 7},
        }
    ]


def test_project_selection_outcomes_records_resolved_validation_probe_as_flat(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:binance-validation-resolved",
                            "prompt_mode": "assist",
                            "selected_page_ids": [
                                "binance.risk.trading_validation"
                            ],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "validation_repair": {
                            "version": "validation_repair_prompt_v1",
                            "scope": "binance",
                            "status": "risk_repair",
                            "repair_item_count": 1,
                            "constraint_count": 1,
                            "repair_backlog": [
                                {
                                    "discipline_id": "cost_simulation",
                                    "repair_action_id": "collect_cost_edge",
                                    "entry_bias": "waiting_probe",
                                    "risk_budget_multiplier": 0.25,
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "validation_repair_resolution": {
                            "blanket_hold_allowed": False,
                            "resolved_candidates": [
                                {
                                    "symbol": "BNBUSDT",
                                    "market": "spot",
                                    "resolution": "candidate_rejected",
                                    "next_trigger": "spread < 0.06%",
                                    "evidence_gap": "net edge after fee was negative",
                                }
                            ],
                        },
                        "hold_decision": {
                            "summary": "비용 우위 회복 전까지 대기",
                            "watch_symbols": ["BNBUSDT"],
                            "next_triggers": [
                                {
                                    "symbol": "BNBUSDT",
                                    "market": "spot",
                                    "condition": "spread < 0.06%",
                                    "reason": "비용 우위 회복",
                                }
                            ],
                        },
                    }
                ),
                json.dumps({"create_blocks": []}),
            ),
        )

    service.project_decision_links(binance_db_path=binance_db)
    result = service.project_selection_outcomes(binance_db_path=binance_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:binance-validation-resolved"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "resolved_validation_probe"
    assert outcomes[0]["outcome_status"] == "flat"
    assert outcomes[0]["return_pct"] == 0.0
    assert outcomes[0]["evidence"]["reason"] == (
        "validation_repair_probe_contract_resolved_without_block"
    )
    assert outcomes[0]["evidence"]["resolution"]["resolved_candidates"][0][
        "symbol"
    ] == "BNBUSDT"


def test_project_selection_outcomes_records_resolved_wiki_repair_as_flat(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, prompt_json, response_json, actions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-repair-resolved",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.symbol.005930"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "jue_wiki_repair_contract": {
                            "status": "active",
                            "repair_priority_count": 1,
                            "top_priorities": [
                                {
                                    "page_id": "kis.symbol.005930",
                                    "priority_type": "decision_adjustment_audit",
                                    "symbols": ["005930"],
                                    "source_id": (
                                        "kis:supporting_evidence:"
                                        "audit_preferred_risk_posture_before_shift:"
                                        "repair_probe"
                                    ),
                                    "action_type": (
                                        "repair_decision_adjustment_audit_contract"
                                    ),
                                    "decision_use": (
                                        "decision_adjustment_audit_repair"
                                    ),
                                    "repair_action": (
                                        "probe with smaller sizing or waiting-entry only"
                                    ),
                                    "repair_loop_status": "repair_required",
                                    "repair_loop_sample_count": 5,
                                    "repair_loop_missed_count": 3,
                                    "repair_loop_resolved_count": 2,
                                    "repair_loop_resolution_rate": 0.4,
                                    "repair_loop_action_type": (
                                        "repair_decision_adjustment_audit_contract"
                                    ),
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "validation_repair_resolution": {
                            "blanket_hold_allowed": False,
                            "resolved_candidates": [
                                {
                                    "symbol": "005930",
                                    "resolution": "small_waiting_block",
                                    "horizon": "mid",
                                    "next_trigger": "72000원 이하 눌림 확인",
                                }
                            ],
                        },
                        "hold_decision": {
                            "summary": "삼성전자 눌림 대기",
                            "watch_symbols": ["005930"],
                        },
                    }
                ),
                json.dumps({"create_blocks": []}),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-repair-resolved"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 1
    assert outcomes[0]["outcome_kind"] == "resolved_repair_priority"
    assert outcomes[0]["outcome_status"] == "flat"
    assert outcomes[0]["evidence"]["reason"] == "repair_priority_resolved_without_block"
    assert outcomes[0]["evidence"]["repair_priority_types"] == [
        "decision_adjustment_audit"
    ]
    assert outcomes[0]["evidence"]["repair_action_types"] == [
        "repair_decision_adjustment_audit_contract"
    ]
    assert outcomes[0]["evidence"]["repair_decision_uses"] == [
        "decision_adjustment_audit_repair"
    ]
    assert outcomes[0]["evidence"]["repair_loop_statuses"] == ["repair_required"]
    assert outcomes[0]["evidence"]["repair_loop_sample_counts"] == [5]
    assert outcomes[0]["evidence"]["repair_loop_missed_counts"] == [3]
    assert outcomes[0]["evidence"]["repair_loop_resolved_counts"] == [2]
    assert outcomes[0]["evidence"]["repair_loop_resolution_rates"] == [0.4]
    assert outcomes[0]["evidence"]["resolution"]["resolved_candidates"][0][
        "resolution"
    ] == "small_waiting_block"


def test_project_selection_outcomes_does_not_penalize_validation_hard_block(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                manager_run_id INTEGER,
                status TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (run_at, status, mode, model, prompt_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:30:00+00:00",
                "ok",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:kis-validation-blocked",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.risk.trading_validation"],
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                        "validation_repair": {
                            "version": "validation_repair_prompt_v1",
                            "scope": "kis",
                            "status": "risk_repair",
                            "repair_item_count": 1,
                            "hard_filter": True,
                            "blocks_new_entries": ["all_new_entries"],
                        },
                    }
                ),
            ),
        )

    service.project_decision_links(kis_db_path=kis_db)
    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(
        selection_run_id="selection:kis-validation-blocked"
    )

    assert result["status"] == "ok"
    assert result["projected_count"] == 0
    assert outcomes == []


def test_project_selection_outcomes_backfills_closed_kis_blocks_without_prompt_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.wiki.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={"Current Stance": "Backfill fixture."},
        source_refs=[],
        confidence=0.9,
        freshness="current",
    )
    service.wiki.write_page(
        scope="kis",
        page_type="playbook",
        key="reflection_lessons",
        title="KIS Reflection Lessons",
        symbols=[],
        content_sections={"Current Stance": "Backfill fixture."},
        source_refs=[],
        confidence=0.9,
        freshness="current",
    )
    service.wiki.write_page(
        scope="kis",
        page_type="risk",
        key="trading_validation",
        title="KIS Trading Validation Risk",
        symbols=[],
        content_sections={"Current Stance": "Backfill fixture."},
        source_refs=[],
        confidence=0.9,
        freshness="current",
    )
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                manager_run_id INTEGER,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                opened_at TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE block_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_id TEXT NOT NULL,
                side TEXT NOT NULL,
                limit_price INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, qty_initial, qty_open, entry_price,
                target_price, stop_price, manager_run_id, status, metadata_json,
                created_at, updated_at, opened_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "blk-old",
                "005930",
                1,
                0,
                70000,
                76000,
                68000,
                301,
                "closed",
                json.dumps(
                    {
                        "applied_policy_versions": [
                            "wait_for_clean_block_validation@v1"
                        ],
                        "jue_wiki_repair_pressure": (
                            "삼성전자 valuation page had stale PER evidence"
                        ),
                        "jue_wiki_repair_resolution": (
                            "closed block outcome should refresh symbol wiki confidence"
                        ),
                    }
                ),
                "2026-06-12T00:00:00+00:00",
                "2026-06-13T00:00:00+00:00",
                "2026-06-12T00:05:00+00:00",
                "2026-06-13T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO block_orders (block_id, side, limit_price, avg_fill_price)
            VALUES (?, ?, ?, ?)
            """,
            ("blk-old", "sell", 0, 73500),
        )

    result = service.project_selection_outcomes(kis_db_path=kis_db)
    outcomes = service.list_selection_outcomes(page_id="kis.symbol.005930")
    links = service.list_decision_links(decision_scope="kis")

    assert result["status"] == "ok"
    assert result["backfilled_count"] == 3
    assert outcomes[0]["outcome_status"] == "win"
    assert outcomes[0]["selection_run_id"].startswith("backfill:kis:")
    assert links[0]["action"] == "closed_block_backfill"
    assert links[0]["metadata"]["selected_wiki_pages"]["page_count"] == 3
    assert links[0]["metadata"]["selected_wiki_pages"]["pages"][0]["page_id"] == (
        "kis.symbol.005930"
    )
    assert links[0]["metadata"]["jue_wiki_repair_pressure"] == (
        "삼성전자 valuation page had stale PER evidence"
    )
    assert links[0]["metadata"]["jue_wiki_repair_resolution"] == (
        "closed block outcome should refresh symbol wiki confidence"
    )
    assert "kis.playbook.reflection_lessons" in links[0]["selected_pages"]
    assert "kis.risk.trading_validation" in links[0]["selected_pages"]
