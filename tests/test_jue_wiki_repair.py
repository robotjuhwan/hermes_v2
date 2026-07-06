from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_application import JueWikiApplicationService
from tradecraft.services.jue_wiki_repair import JueWikiRepairService


def _service(tmp_path: Path) -> JueWikiService:
    return JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )


def _write_stale_page(
    service: JueWikiService,
    *,
    scope: str,
    key: str,
    symbol: str,
) -> None:
    service.write_page(
        scope=scope,
        page_type="symbol",
        key=key,
        title=f"{symbol} stale page",
        symbols=[symbol],
        content_sections={
            "Current Stance": "Wait for fresh evidence.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- No linked evidence.",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Re-check stale thesis.",
            "Contradictions": "- None.",
            "Open Questions": "- Does the thesis still hold?",
            "Next Context Pack Summary": "Use only after source refresh.",
        },
        source_refs=[],
        confidence=0.4,
        freshness="stale",
    )


def test_repair_once_records_actions_for_open_lint_findings(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_stale_page(service, scope="kis", key="005930", symbol="005930")

    lint_result = service.lint(scope="kis")
    repair_result = service.repair_once(scope="kis")

    assert lint_result["open_findings"]
    assert repair_result["status"] == "ok"
    assert repair_result["actions"]
    assert {action["action_type"] for action in repair_result["actions"]} == {
        "rebuild_page"
    }
    assert {action["status"] for action in repair_result["actions"]} == {
        "scheduled"
    }

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM wiki_repair_actions"
        ).fetchone()[0]

    assert count >= 1


def test_repair_once_records_source_ref_identity_gap_actions_idempotently(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="005930 weak source identity",
        symbols=["005930"],
        content_sections={
            "Current Stance": "Source identity needs repair.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- compressed source refs.",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Source refs must be auditable.",
            "Contradictions": "- None.",
            "Open Questions": "- Which upstream source created the gap?",
            "Next Context Pack Summary": "Repair source refs before strong reuse.",
        },
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
        confidence=0.68,
        freshness="fresh",
    )

    lint_result = service.lint(scope="kis")
    first = service.repair_once(scope="kis")
    second = service.repair_once(scope=None)

    assert lint_result["status"] == "warn"
    action = next(
        row
        for row in first["lint_actions"]
        if row["action_type"] == "repair_source_ref_identity_gap"
    )
    assert action["status"] == "scheduled"
    assert action["details"]["finding_type"] == "source_ref_identity_gap"
    assert action["details"]["gap_count"] == 1
    assert action["details"]["repair_action"] == (
        "repair source_type/source_id provenance before strong wiki reuse"
    )
    assert second["lint_actions"][0]["action_id"] == action["action_id"]

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_type, status, details_json
            FROM wiki_repair_actions
            WHERE action_type = 'repair_source_ref_identity_gap'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "repair_source_ref_identity_gap"
    assert rows[0][1] == "scheduled"
    assert json.loads(rows[0][2])["gap_count"] == 1


def test_repair_once_records_evidence_quality_actions_idempotently(
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
            "Current Stance": "Fundamentals need repair.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- symbol_fundamentals:005930:2026-07-03",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Do not trust sparse fundamentals blindly.",
            "Contradictions": "- Financial table parser rejected noisy rows.",
            "Open Questions": "- Can WiseReport financials be refreshed?",
            "Next Context Pack Summary": "Repair fundamentals before mid/long sizing.",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "partial",
                "quality_warnings": [
                    "financial_rows_rejected_credit_rating",
                    "financials_missing",
                ],
            }
        ],
        confidence=0.72,
        freshness="fresh",
    )

    first = service.repair_once(scope="kis")
    second = service.repair_once(scope="kis")

    assert first["status"] == "ok"
    assert first["evidence_quality_actions"]
    assert second["evidence_quality_actions"]
    action = first["evidence_quality_actions"][0]
    assert action["action_type"] == "refresh_symbol_financials"
    assert action["status"] == "scheduled"
    assert action["details"]["finding_type"] == "evidence_quality"
    assert action["details"]["symbols"] == ["005930"]
    assert action["details"]["quality_warnings"] == [
        "financial_rows_rejected_credit_rating",
        "financials_missing",
    ]
    assert second["evidence_quality_actions"][0]["action_id"] == action["action_id"]

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_type, status, details_json
            FROM wiki_repair_actions
            WHERE finding_id LIKE 'evidence_quality:%'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "refresh_symbol_financials"
    assert rows[0][1] == "scheduled"


def test_repair_once_records_nested_compact_evidence_quality_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 compact nested evidence",
        symbols=["005930"],
        content_sections={
            "Current Stance": "Nested fundamentals need repair.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- compressed source refs.",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Compact evidence quality must still trigger repair.",
            "Contradictions": "- None.",
            "Open Questions": "- Can the nested source be refreshed?",
            "Next Context Pack Summary": "Repair nested compact evidence.",
        },
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:compact-quality",
                        "evidence_quality": {
                            "source_count": 2,
                            "status_counts": {"partial": 1, "weak": 1},
                            "warning_counts": {
                                "financials_missing": 1,
                                "price_missing": 1,
                            },
                            "top_warnings": [
                                {"warning": "financials_missing", "count": 1},
                                {"warning": "price_missing", "count": 1},
                            ],
                        },
                    }
                ],
            }
        ],
        confidence=0.72,
        freshness="fresh",
    )

    result = service.repair_once(scope="kis")

    action = result["evidence_quality_actions"][0]
    assert action["action_type"] == "refresh_symbol_quote"
    assert action["status"] == "scheduled"
    assert action["details"]["finding_type"] == "evidence_quality"
    assert action["details"]["symbols"] == ["005930"]
    assert action["details"]["source_type"] == "symbol_fundamentals"
    assert action["details"]["source_id"] == "005930:compact-quality"
    assert action["details"]["quality_status"] == "weak"
    assert action["details"]["quality_warnings"] == [
        "financials_missing",
        "price_missing",
    ]


def test_repair_once_records_degraded_quality_warning_effectiveness_actions(
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
            "Current Stance": "Financial evidence needs repair.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- symbol_fundamentals:005930:2026-07-03",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Do not size mid blocks from missing financials.",
            "Contradictions": "- Financial rows are missing.",
            "Open Questions": "- Can WiseReport rows be recovered?",
            "Next Context Pack Summary": "Refresh financials before reuse.",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.71,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 12,
            "win_rate": 0.25,
            "expectancy": -1.4,
            "helpful_score": -8.0,
            "confidence": 0.85,
            "status": "degraded",
            "reasons": ["quality_warning:financials_missing"],
        }
    )

    first = service.repair_once(scope="kis")
    second = service.repair_once(scope="kis")

    assert first["status"] == "ok"
    assert first["quality_warning_effectiveness_actions"]
    action = first["quality_warning_effectiveness_actions"][0]
    assert action["page_id"] == "quality_warning.financials_missing"
    assert action["action_type"] == "repair_quality_warning_effectiveness"
    assert action["status"] == "scheduled"
    assert action["details"]["finding_type"] == "quality_warning_effectiveness"
    assert action["details"]["decision_scope"] == "kis"
    assert action["details"]["warning"] == "financials_missing"
    assert action["details"]["quality_warnings"] == ["financials_missing"]
    assert action["details"]["sample_count"] == 12
    assert action["details"]["repair_action"] == (
        "repair or downgrade evidence carrying financials_missing before trusting "
        "this warning-bearing memory again"
    )
    assert action["details"]["reasons"] == ["quality_warning:financials_missing"]
    assert second["quality_warning_effectiveness_actions"][0]["action_id"] == (
        action["action_id"]
    )

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_type, status, details_json
            FROM wiki_repair_actions
            WHERE finding_id LIKE 'quality_warning_effectiveness:%'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "repair_quality_warning_effectiveness"
    assert rows[0][1] == "scheduled"


def test_repair_once_records_degraded_application_repair_queue_pressure_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = service.page_id(scope="kis", page_type="symbol", key="005930")
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 7,
            "win_rate": 0.14,
            "expectancy": -2.2,
            "helpful_score": -6.5,
            "confidence": 0.72,
            "status": "degraded",
            "reasons": [
                "application_repair_queue_pressure",
                "repair_queue_open_count:2",
                "repair_queue_action:refresh_symbol_financials",
            ],
        }
    )

    first = service.repair_once(scope="kis")
    second = service.repair_once(scope="kis")

    assert first["status"] == "ok"
    assert first["application_repair_queue_pressure_actions"]
    action = first["application_repair_queue_pressure_actions"][0]
    assert action["page_id"] == page_id
    assert action["action_type"] == "repair_application_repair_queue_pressure"
    assert action["status"] == "scheduled"
    assert action["details"]["finding_type"] == "application_repair_queue_pressure"
    assert action["details"]["decision_scope"] == "kis"
    assert action["details"]["repair_queue_open_count"] == 2
    assert action["details"]["repair_queue_action_types"] == [
        "refresh_symbol_financials"
    ]
    assert action["details"]["quality_warnings"] == [
        "application_repair_queue_pressure"
    ]
    assert action["details"]["repair_targets"] == [
        {
            "page_id": page_id,
            "recommended_action": "resolve_open_repair_queue_before_reusing_page",
        }
    ]
    assert second["application_repair_queue_pressure_actions"][0]["action_id"] == (
        action["action_id"]
    )

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_type, status, details_json
            FROM wiki_repair_actions
            WHERE finding_id LIKE 'application_repair_queue_pressure:%'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "repair_application_repair_queue_pressure"
    assert rows[0][1] == "scheduled"
    details = json.loads(rows[0][2])
    assert details["reasons"] == [
        "application_repair_queue_pressure",
        "repair_queue_open_count:2",
        "repair_queue_action:refresh_symbol_financials",
    ]


def test_repair_once_resolves_application_repair_queue_pressure_actions_when_clean(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = service.page_id(scope="kis", page_type="symbol", key="005930")
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 7,
            "win_rate": 0.14,
            "expectancy": -2.2,
            "helpful_score": -6.5,
            "confidence": 0.72,
            "status": "degraded",
            "reasons": [
                "application_repair_queue_pressure",
                "repair_queue_open_count:2",
            ],
        }
    )
    first = service.repair_once(scope="kis")
    assert first["application_repair_queue_pressure_actions"]

    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 9,
            "win_rate": 0.56,
            "expectancy": 0.9,
            "helpful_score": 3.4,
            "confidence": 0.74,
            "status": "active",
            "reasons": ["application_repair_queue_clean"],
        }
    )
    second = service.repair_once(scope="kis")

    assert not second["application_repair_queue_pressure_actions"]
    assert second["application_repair_queue_pressure_resolved_actions"]
    resolved = second["application_repair_queue_pressure_resolved_actions"][0]
    assert resolved["status"] == "resolved"
    assert resolved["details"]["resolved_by"] == "application_repair_queue_clean"
    assert resolved["details"]["resolved_application_repair_queue_pressure"] is True

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT status, details_json
            FROM wiki_repair_actions
            WHERE finding_id LIKE 'application_repair_queue_pressure:%'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "resolved"


def test_quality_warning_effectiveness_actions_include_impacted_pages(
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
            "Current Stance": "Financial evidence needs cross-check.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- symbol_fundamentals:005930:2026-07-03",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Avoid sizing without fresh financials.",
            "Contradictions": "- Missing financial table.",
            "Open Questions": "- Can WiseReport rows be repaired?",
            "Next Context Pack Summary": "Repair financials before mid sizing.",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.71,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 8,
            "win_rate": 0.25,
            "expectancy": -0.9,
            "helpful_score": -4.0,
            "confidence": 0.74,
            "status": "degraded",
            "reasons": ["quality_warning:financials_missing"],
        }
    )

    result = service.repair_once(scope="kis")

    action = result["quality_warning_effectiveness_actions"][0]
    assert action["details"]["impacted_page_ids"] == ["kis.symbol.005930"]
    assert action["details"]["impacted_symbols"] == ["005930"]
    assert action["details"]["impacted_source_refs"] == [
        {
            "page_id": "kis.symbol.005930",
            "source_type": "symbol_fundamentals",
            "source_id": "005930:2026-07-03",
            "quality_status": "partial",
        }
    ]
    assert action["details"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]


def test_quality_warning_effectiveness_actions_include_nested_impacted_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 nested financial warning",
        symbols=["005930"],
        content_sections={
            "Current Stance": "Nested financial warning needs repair.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- compressed source refs.",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Nested warnings still affect the page.",
            "Contradictions": "- None.",
            "Open Questions": "- Can the source be refreshed?",
            "Next Context Pack Summary": "Repair nested financial warning.",
        },
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:nested-financials",
                        "quality_status": "partial",
                        "quality_warnings": ["financials_missing"],
                    }
                ],
            }
        ],
        confidence=0.71,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 8,
            "win_rate": 0.25,
            "expectancy": -0.9,
            "helpful_score": -4.0,
            "confidence": 0.74,
            "status": "degraded",
            "reasons": ["quality_warning:financials_missing"],
        }
    )

    result = service.repair_once(scope="kis")

    action = result["quality_warning_effectiveness_actions"][0]
    assert action["details"]["impacted_page_ids"] == ["kis.symbol.005930"]
    assert action["details"]["impacted_source_refs"] == [
        {
            "page_id": "kis.symbol.005930",
            "source_type": "symbol_fundamentals",
            "source_id": "005930:nested-financials",
            "quality_status": "partial",
        }
    ]


def test_repair_once_updates_existing_quality_warning_effectiveness_action_details(
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
            "Current Stance": "Financial evidence needs repair.",
            "Durable Facts": "- Tracked symbol.",
            "Evidence Links": "- symbol_fundamentals:005930:2026-07-03",
            "Trading History": "- No blocks yet.",
            "Lessons": "- Do not size mid blocks from missing financials.",
            "Contradictions": "- Financial rows are missing.",
            "Open Questions": "- Can WiseReport rows be recovered?",
            "Next Context Pack Summary": "Refresh financials before reuse.",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.71,
        freshness="fresh",
    )
    page_id = "quality_warning.financials_missing"
    finding_id = JueWikiRepairService._quality_warning_effectiveness_finding_id(
        page_id=page_id,
        decision_scope="kis",
        venue="kis",
        horizon="mid",
    )
    old_action = service.record_repair_action(
        finding_id=finding_id,
        page_id=page_id,
        action_type="repair_quality_warning_effectiveness",
        status="scheduled",
        details={
            "finding_type": "quality_warning_effectiveness",
            "decision_scope": "kis",
            "warning": "financials_missing",
            "quality_warnings": ["financials_missing"],
            "reasons": [],
        },
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 12,
            "win_rate": 0.25,
            "expectancy": -1.4,
            "helpful_score": -8.0,
            "confidence": 0.85,
            "status": "degraded",
            "reasons": ["quality_warning:financials_missing"],
        }
    )

    result = service.repair_once(scope="kis")

    action = result["quality_warning_effectiveness_actions"][0]
    assert action["action_id"] == old_action["action_id"]
    assert action["details"]["reasons"] == ["quality_warning:financials_missing"]
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        details_json = conn.execute(
            """
            SELECT details_json
            FROM wiki_repair_actions
            WHERE action_id = ?
            """,
            (old_action["action_id"],),
        ).fetchone()[0]

    assert json.loads(details_json)["reasons"] == [
        "quality_warning:financials_missing"
    ]


def test_repair_once_records_degraded_usage_guidance_effectiveness_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.upsert_page_effectiveness(
        {
            "page_id": "usage_guidance.risk_posture.repair_cross_check",
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 9,
            "win_rate": 0.22,
            "expectancy": -1.3,
            "avg_return_pct": -1.3,
            "helpful_score": -7.0,
            "confidence": 0.82,
            "status": "degraded",
            "reasons": [
                "usage_guidance:risk_posture:repair_cross_check",
                "usage_guidance_trust_level:low",
            ],
        }
    )

    first = service.repair_once(scope="kis")
    second = service.repair_once(scope="kis")

    assert first["status"] == "ok"
    assert first["usage_guidance_effectiveness_actions"]
    action = first["usage_guidance_effectiveness_actions"][0]
    assert action["page_id"] == "usage_guidance.risk_posture.repair_cross_check"
    assert action["action_type"] == "repair_usage_guidance_contract"
    assert action["status"] == "scheduled"
    assert action["details"]["finding_type"] == "usage_guidance_effectiveness"
    assert action["details"]["decision_scope"] == "kis"
    assert action["details"]["usage_guidance_id"] == (
        "risk_posture.repair_cross_check"
    )
    assert action["details"]["quality_warnings"] == ["usage_guidance_degraded"]
    assert action["details"]["sample_count"] == 9
    assert action["details"]["repair_action"] == (
        "repair degraded wiki usage guidance before reusing this page usage pattern"
    )
    assert action["details"]["reasons"] == [
        "usage_guidance:risk_posture:repair_cross_check",
        "usage_guidance_trust_level:low",
    ]
    assert second["usage_guidance_effectiveness_actions"][0]["action_id"] == (
        action["action_id"]
    )

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_type, status, details_json
            FROM wiki_repair_actions
            WHERE finding_id LIKE 'usage_guidance_effectiveness:%'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "repair_usage_guidance_contract"
    assert rows[0][1] == "scheduled"


def test_repair_once_records_wiki_application_horizon_gap_action_idempotently(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    application = JueWikiApplicationService(service)
    link = application.record_decision_link(
        selection_run_id="selection:kis-repair-horizon-gap",
        manager_run_id="kis-manager-repair-horizon-gap",
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
                "repair-horizon-gap-outcome",
                link["link_id"],
                "selection:kis-repair-horizon-gap",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "blk-repair-horizon-gap",
                "closed_block",
                "win",
                1.2,
                json.dumps(
                    {
                        "symbol": "005930",
                        "block_id": "blk-repair-horizon-gap",
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

    first = service.repair_once(scope="kis")
    second = service.repair_once(scope="kis")

    assert first["status"] == "ok"
    assert first["wiki_application_coverage_actions"]
    action = first["wiki_application_coverage_actions"][0]
    assert action["action_type"] == "reproject_closed_block_outcome_horizons"
    assert action["status"] == "scheduled"
    assert action["details"]["finding_type"] == "wiki_application_coverage"
    assert action["details"]["decision_scope"] == "kis"
    assert action["details"]["closed_block_outcomes_without_horizon"] == 1
    assert action["details"]["closed_block_outcomes_without_horizon_pct"] == 100.0
    assert action["details"]["quality_warnings"] == [
        "closed_block_outcome_horizon_missing"
    ]
    assert action["details"]["repair_targets"] == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
    ]
    assert second["wiki_application_coverage_actions"][0]["action_id"] == (
        action["action_id"]
    )

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_type, status, details_json
            FROM wiki_repair_actions
            WHERE finding_id = 'wiki_application_coverage:kis:outcome_horizon'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "reproject_closed_block_outcome_horizons"
    assert rows[0][1] == "scheduled"


def test_repair_once_resolves_wiki_application_horizon_gap_action_when_clean(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    application = JueWikiApplicationService(service)
    link = application.record_decision_link(
        selection_run_id="selection:kis-repair-horizon-gap-clean",
        manager_run_id="kis-manager-repair-horizon-gap-clean",
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
                "repair-horizon-gap-clean-outcome",
                link["link_id"],
                "selection:kis-repair-horizon-gap-clean",
                "kis.symbol.005930",
                "kis",
                "kis",
                "005930",
                "blk-repair-horizon-gap-clean",
                "closed_block",
                "win",
                1.2,
                json.dumps(
                    {
                        "symbol": "005930",
                        "block_id": "blk-repair-horizon-gap-clean",
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

    first = service.repair_once(scope="kis")
    action = first["wiki_application_coverage_actions"][0]
    with service._connect() as conn:
        conn.execute(
            """
            UPDATE wiki_selection_outcomes
            SET horizon = 'mid'
            WHERE outcome_id = 'repair-horizon-gap-clean-outcome'
            """
        )

    second = service.repair_once(scope="kis")

    resolved = second["wiki_application_coverage_actions"][0]
    assert resolved["action_id"] == action["action_id"]
    assert resolved["status"] == "resolved"
    assert resolved["details"]["resolved_by"] == "wiki_application_coverage_clean"
    assert resolved["details"]["resolved_closed_block_outcome_horizon_gap"] is True

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, finished_at, details_json
            FROM wiki_repair_actions
            WHERE action_id = ?
            """,
            (action["action_id"],),
        ).fetchone()

    assert row[0] == "resolved"
    assert row[1]
    assert json.loads(row[2])["resolved_by"] == "wiki_application_coverage_clean"


def test_like_metacharacter_scopes_do_not_match_or_resolve_unrelated_findings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_stale_page(service, scope="kis", key="005930", symbol="005930")
    _write_stale_page(service, scope="binance", key="BTCUSDT", symbol="BTCUSDT")
    service.lint(scope="all")

    for scope in ("%", "k_s"):
        scoped_findings = service.list_lint_findings(scope=scope)
        scoped_lint = service.lint(scope=scope)
        remaining_findings = service.list_lint_findings(scope="all")

        assert scoped_findings == []
        assert scoped_lint["open_findings"] == []
        assert len(remaining_findings) >= 4
        assert {finding["status"] for finding in remaining_findings} == {"open"}
