from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradecraft.api.ops_payloads import build_ops_jue_wiki_payload
from tradecraft.services.daily_discovery import DailyDiscoveryRepository
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_contract import (
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiSnapshotV1,
)
from tradecraft.services.trading_validation import TradingValidationRepository


class FakeRagStore:
    def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict[str, object]]:
        _ = top_k, filters
        return [
            {
                "text": "삼성전자 메모리 가격 회복과 HBM 수요가 핵심이다.",
                "metadata": {
                    "symbol": query,
                    "title": "반도체 리포트",
                    "report_id": "r1",
                },
                "score": 0.91,
            }
        ]


class FailingRagStore:
    def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict[str, object]]:
        _ = query, top_k, filters
        raise RuntimeError("rag offline")


class LimitOnlyRagStore:
    def query(
        self,
        query: str,
        *,
        limit: int = 8,
        symbol: str = "",
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, object]]:
        _ = limit, symbol, broker, doc_id, date_from, date_to
        return [
            {
                "text": f"{query} 리포트 근거가 정상 조회된다.",
                "metadata": {
                    "symbol": query,
                    "title": "실제 RAG 계약 리포트",
                    "report_id": "rag-limit-1",
                },
                "score": 0.88,
            }
        ]


class TopLevelRagStore:
    def query(
        self,
        query: str,
        *,
        limit: int = 8,
        symbol: str = "",
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, object]]:
        _ = query, limit, symbol, broker, doc_id, date_from, date_to
        return [
            {
                "content": "실제 RAGStore 반환 형태는 content와 report_id가 top-level이다.",
                "report_id": 5253,
                "title": "삼성전자 실제 리포트",
                "symbol": "005930",
                "broker": "현대차증권",
            }
        ]


def _service(
    tmp_path: Path,
    *,
    context_max_chars: int = 24000,
    page_max_chars: int = 12000,
    context_page_limit: int = 8,
) -> JueWikiService:
    return JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            context_max_chars=context_max_chars,
            page_max_chars=page_max_chars,
            context_page_limit=context_page_limit,
        )
    )


def test_source_refs_quality_summary_reads_nested_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930",
                "evidence_quality": {
                    "source_count": 2,
                    "status_counts": {"partial": 1, "weak": 1},
                    "warning_counts": {
                        "financials_missing": 1,
                        "price_missing": 1,
                    },
                    "source_type_counts": {
                        "symbol_fundamentals": 1,
                        "rag_report": 1,
                    },
                    "top_warnings": [
                        {"warning": "financials_missing", "count": 1},
                        {"warning": "price_missing", "count": 1},
                    ],
                },
            }
        ]
    )

    assert summary["source_count"] == 2
    assert summary["status_counts"] == {"partial": 1, "weak": 1}
    assert summary["warning_counts"] == {
        "financials_missing": 1,
        "price_missing": 1,
    }
    assert summary["source_type_counts"] == {
        "symbol_fundamentals": 1,
        "rag_report": 1,
    }
    assert summary["top_warnings"] == [
        {"warning": "financials_missing", "count": 1},
        {"warning": "price_missing", "count": 1},
    ]


def test_source_refs_quality_summary_counts_unlabeled_sources_as_unknown(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "naver_reports",
                "source_id": "005930:report",
            }
        ]
    )

    assert summary["source_count"] == 1
    assert summary["status_counts"] == {"unknown": 1}
    assert summary["source_type_counts"] == {"naver_reports": 1}
    assert summary["summary_line"] == "evidence_quality sources=1, unknown=1"


def test_source_refs_quality_summary_canonicalizes_quality_status_aliases(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "naver_reports",
                "source_id": "005930:ok",
                "quality_status": "ok",
            },
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:healthy",
                "quality_status": "healthy",
            },
            {
                "source_type": "rag",
                "source_id": "005930:limited",
                "quality_status": "limited",
            },
            {
                "source_type": "repair_queue",
                "source_id": "005930:degraded",
                "quality_status": "degraded",
            },
        ]
    )

    assert summary["source_count"] == 4
    assert summary["status_counts"] == {
        "strong": 2,
        "partial": 1,
        "weak": 1,
    }
    assert summary["summary_line"] == (
        "evidence_quality sources=4, strong=2, partial=1, weak=1"
    )


def test_source_refs_quality_summary_preserves_direct_weak_status_with_nested_warnings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:degraded-warning-only",
                "quality_status": "degraded",
                "evidence_quality": {
                    "top_warnings": [
                        {"warning": "valuation_metrics_sparse", "count": 1}
                    ],
                    "warning_counts": {"valuation_metrics_sparse": 1},
                },
            }
        ]
    )

    assert summary["source_count"] == 1
    assert summary["status_counts"] == {"weak": 1}
    assert summary["warning_counts"] == {"valuation_metrics_sparse": 1}
    assert summary["source_type_counts"] == {"symbol_fundamentals": 1}
    assert summary["summary_line"] == (
        "evidence_quality sources=1, weak=1, "
        "warnings=valuation_metrics_sparse:1"
    )


def test_source_refs_quality_summary_direct_status_overrides_single_nested_status(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:wrapper-degraded",
                "quality_status": "degraded",
                "evidence_quality": {
                    "source_count": 1,
                    "status_counts": {"ok": 1},
                    "source_type_counts": {"symbol_fundamentals": 1},
                },
            }
        ]
    )

    assert summary["source_count"] == 1
    assert summary["status_counts"] == {"weak": 1}
    assert summary["source_type_counts"] == {"symbol_fundamentals": 1}
    assert summary["summary_line"] == "evidence_quality sources=1, weak=1"


def test_source_refs_quality_summary_direct_status_overrides_single_nested_ref(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:wrapper-degraded",
                "quality_status": "degraded",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:ok",
                        "quality_status": "ok",
                    }
                ],
            }
        ]
    )

    assert summary["source_count"] == 1
    assert summary["status_counts"] == {"weak": 1}
    assert summary["source_type_counts"] == {"symbol_fundamentals": 1}
    assert summary["summary_line"] == "evidence_quality sources=1, weak=1"


def test_merge_evidence_quality_canonicalizes_status_aliases(tmp_path: Path) -> None:
    service = _service(tmp_path)

    summary = service.merge_evidence_quality(
        [
            {
                "source_count": 2,
                "status_counts": {"ok": 1, "degraded": 1},
                "warning_counts": {"valuation_metrics_sparse": 1},
                "source_type_counts": {"symbol_fundamentals": 2},
            }
        ]
    )

    assert summary["source_count"] == 2
    assert summary["status_counts"] == {"strong": 1, "weak": 1}
    assert summary["summary_line"] == (
        "evidence_quality_total sources=2, strong=1, weak=1, "
        "warnings=valuation_metrics_sparse:1"
    )


def test_source_refs_quality_summary_reads_nested_source_ref_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:2026-07-03",
                        "evidence_quality": {
                            "source_count": 2,
                            "status_counts": {"partial": 1, "weak": 1},
                            "warning_counts": {
                                "financials_missing": 1,
                                "price_missing": 1,
                            },
                            "source_type_counts": {
                                "symbol_fundamentals": 1,
                                "rag_report": 1,
                            },
                            "top_warnings": [
                                {"warning": "financials_missing", "count": 1},
                                {"warning": "price_missing", "count": 1},
                            ],
                        },
                    }
                ],
            }
        ]
    )

    assert summary["source_count"] == 2
    assert summary["status_counts"] == {"partial": 1, "weak": 1}
    assert summary["warning_counts"] == {
        "financials_missing": 1,
        "price_missing": 1,
    }
    assert summary["source_type_counts"] == {
        "symbol_fundamentals": 1,
        "rag_report": 1,
    }


def test_source_refs_quality_summary_avoids_double_counting_nested_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930",
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
        ]
    )

    assert summary["source_count"] == 1
    assert summary["status_counts"] == {"weak": 1}
    assert summary["warning_counts"] == {"financials_missing": 1}
    assert summary["source_type_counts"] == {"symbol_fundamentals": 1}


def test_source_refs_quality_summary_avoids_double_counting_nested_source_refs_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    summary = service.source_refs_quality_summary(
        [
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "quality_status": "weak",
                "quality_warnings": ["financials_missing"],
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:2026-07-03",
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
            }
        ]
    )

    assert summary["source_count"] == 1
    assert summary["status_counts"] == {"weak": 1}
    assert summary["warning_counts"] == {"financials_missing": 1}
    assert summary["source_type_counts"] == {"symbol_fundamentals": 1}


def test_evidence_quality_rows_flatten_nested_source_refs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 compressed evidence",
        symbols=["005930"],
        content_sections={
            "Current Stance": "compressed page",
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
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:fundamentals",
                        "quality_status": "weak",
                        "quality_warnings": ["financials_missing"],
                    },
                    {
                        "source_type": "rag_report",
                        "source_id": "005930:rag",
                        "quality_status": "partial",
                        "quality_warnings": ["price_stale"],
                    },
                ],
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )

    rows, total_source_refs, active_page_count = service._evidence_quality_rows(
        scope="kis"
    )

    assert total_source_refs == 3
    assert active_page_count == 1
    assert [
        {
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "quality_status": row["quality_status"],
            "quality_warnings": row["quality_warnings"],
        }
        for row in rows
    ] == [
        {
            "source_type": "symbol_fundamentals",
            "source_id": "005930:fundamentals",
            "quality_status": "weak",
            "quality_warnings": ["financials_missing"],
        },
        {
            "source_type": "rag_report",
            "source_id": "005930:rag",
            "quality_status": "partial",
            "quality_warnings": ["price_stale"],
        },
    ]


def test_evidence_quality_rows_reads_compact_nested_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 compact evidence quality",
        symbols=["005930"],
        content_sections={
            "Current Stance": "compact evidence",
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
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:summary",
                "source_refs": [
                    {
                        "source_type": "symbol_fundamentals",
                        "source_id": "005930:fundamentals",
                        "evidence_quality": {
                            "source_count": 2,
                            "status_counts": {"partial": 1, "weak": 1},
                            "warning_counts": {
                                "financials_missing": 1,
                                "price_stale": 1,
                            },
                            "top_warnings": [
                                {"warning": "financials_missing", "count": 1},
                                {"warning": "price_stale", "count": 1},
                            ],
                        },
                    }
                ],
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )

    rows, total_source_refs, active_page_count = service._evidence_quality_rows(
        scope="kis"
    )

    assert total_source_refs == 2
    assert active_page_count == 1
    assert [
        {
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "quality_status": row["quality_status"],
            "quality_warnings": row["quality_warnings"],
        }
        for row in rows
    ] == [
        {
            "source_type": "symbol_fundamentals",
            "source_id": "005930:fundamentals",
            "quality_status": "weak",
            "quality_warnings": ["financials_missing", "price_stale"],
        }
    ]


def test_search_reports_flattened_nested_source_ref_count(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 compressed evidence",
        symbols=["005930"],
        content_sections={
            "Current Stance": "compressed page",
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

    rows = service.search(query="005930", scope="kis", page_type="symbol")

    assert rows[0]["page_id"] == "kis.symbol.005930"
    assert rows[0]["source_count"] == 3


def test_initialize_creates_schema_index_log_directories_and_db(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    result = service.initialize()

    root = tmp_path / "jue_wiki"
    assert result["status"] == "ok"
    assert (root / "AGENTS.md").exists()
    assert (root / "schema.md").exists()
    assert (root / "index.md").exists()
    assert (root / "log.md").exists()
    assert (root / "freshness.md").exists()
    assert (root / "contradictions.md").exists()
    assert (root / "wiki.db").exists()
    assert (root / "core").is_dir()
    assert (root / "kis").is_dir()
    assert (root / "binance").is_dir()
    with sqlite3.connect(root / "wiki.db") as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "wiki_pages",
        "wiki_source_refs",
        "wiki_runs",
        "wiki_lint_findings",
    } <= table_names
    assert (
        service.page_id(scope="kis", page_type="symbol", key="005930")
        == "kis.symbol.005930"
    )
    assert service.page_path(page_id="kis.symbol.005930").as_posix().endswith(
        "kis/symbols/005930.md"
    )


def test_jue_wiki_phase2_tables_are_created(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    service.project_status_snapshot()

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        repair_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(wiki_repair_actions)").fetchall()
        }

    assert "wiki_selection_runs" in names
    assert "wiki_selection_pages" in names
    assert "wiki_repair_actions" in names
    assert {"repair_lane", "repair_lane_registered"} <= repair_columns
    assert "wiki_playbook_metrics" in names


def test_jue_wiki_phase3_tables_are_created(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    service.project_status_snapshot()

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "wiki_decision_links" in names
    assert "wiki_selection_outcomes" in names
    assert "wiki_page_effectiveness" in names
    assert "wiki_mode_recommendations" in names


def test_playbook_metric_omits_missing_metrics_but_keeps_explicit_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.upsert_playbook_metric(
        {
            "page_id": "kis.playbook.missing",
            "scope": "kis",
            "playbook_id": "missing",
            "status": "probe",
            "reasons": ["not measured yet"],
        }
    )
    missing = service.playbook_metric("kis.playbook.missing")

    assert missing == {
        "page_id": "kis.playbook.missing",
        "scope": "kis",
        "playbook_id": "missing",
        "status": "probe",
        "reasons": ["not measured yet"],
        "updated_at": missing["updated_at"],
    }

    service.upsert_playbook_metric(
        {
            "page_id": "kis.playbook.explicit_zero",
            "scope": "kis",
            "playbook_id": "explicit_zero",
            "sample_count": 0,
            "win_rate": 0.0,
            "expectancy": 0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0,
            "avg_holding_minutes": 0.0,
            "status": "probe",
            "reasons": ["explicit zero baseline"],
        }
    )
    explicit_zero = service.playbook_metric("kis.playbook.explicit_zero")

    assert explicit_zero["sample_count"] == 0
    assert explicit_zero["win_rate"] == 0.0
    assert explicit_zero["expectancy"] == 0.0
    assert explicit_zero["profit_factor"] == 0.0
    assert explicit_zero["max_drawdown_pct"] == 0.0
    assert explicit_zero["avg_holding_minutes"] == 0.0


def test_status_reflects_disabled_config(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            enabled=False,
        )
    )

    assert service.project_status_snapshot()["enabled"] is False


def test_status_without_snapshot_does_not_initialize_wiki(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    status = service.status()

    assert status["status"] == "unavailable"
    assert status["snapshot_version"] == "ops_section_snapshot_v1"
    assert status["snapshot_section"] == "jue_wiki"
    assert status["reason"] == "ops_snapshot_missing"
    assert not service.config.db_path.exists()


def test_status_reads_ops_snapshot_without_recomputing_or_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    projected = service.project_status_snapshot()
    before_bytes = service.config.db_path.read_bytes()
    before_mtime_ns = service.config.db_path.stat().st_mtime_ns

    monkeypatch.setattr(
        service,
        "initialize",
        lambda: (_ for _ in ()).throw(
            AssertionError("status read path must not initialize")
        ),
    )
    monkeypatch.setattr(
        service,
        "_research_coverage_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("status read path must not recompute coverage")
        ),
    )

    status = service.status()

    assert status == projected
    assert service.config.db_path.read_bytes() == before_bytes
    assert service.config.db_path.stat().st_mtime_ns == before_mtime_ns


def test_rebuild_reports_malformed_source_db_as_error(tmp_path: Path) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.execute("CREATE TABLE blocks (block_id TEXT, name TEXT)")
        conn.execute("INSERT INTO blocks VALUES (?, ?)", ("blk-1", "No Symbol"))

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)

    assert result["status"] == "error"
    assert result["updated_count"] == 0
    assert "missing symbol column" in result["error_message"]


def test_rebuild_kis_symbols_uses_manager_opportunity_observations(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.execute(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T10:00:00+09:00",
                "regular",
                "error",
                "codex native sdk timed out after 900.0s",
                json.dumps(
                    {
                        "aggressive_opportunities": {
                            "candidates": [
                                {
                                    "symbol": "123450",
                                    "name": "숨은후보",
                                    "aggressive_score": 88,
                                    "signals": ["pre_surge", "large_trading_value"],
                                    "sources": ["daily_discovery", "quote"],
                                }
                            ]
                        },
                        "opportunity_research_brief": {
                            "status": "ok",
                            "role": "minimum_surviving_opportunity_context",
                            "pre_surge_candidates": [
                                {
                                    "symbol": "123450",
                                    "name": "숨은후보",
                                    "source": "daily_discovery.pre_surge",
                                    "bucket": "pre_surge",
                                    "score": 91,
                                    "confidence": 0.72,
                                    "stance": "waiting_entry",
                                    "summary": "눌림 이후 선행 수급이 재점화되는 후보",
                                    "reasons": ["pre_surge", "volume_expansion"],
                                }
                            ],
                        },
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "zero_action_streak": 2,
                            "candidate_count": 3,
                            "required_resolution": (
                                "강한 후보는 대기블록, 탐색블록, 후보별 기각 조건 중 "
                                "하나로 해소해야 한다."
                            ),
                            "top_candidates": [
                                {
                                    "symbol": "123450",
                                    "name": "숨은후보",
                                    "aggressive_score": 88,
                                    "signals": ["pre_surge"],
                                    "sources": ["daily_discovery"],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "no_action_watch": {
                            "status": "attention",
                            "reason": "aggressive_candidates_seen_but_no_block_action",
                            "hold_summary": "가격 조건 대기",
                            "top_candidates": [
                                {
                                    "symbol": "123450",
                                    "name": "숨은후보",
                                    "aggressive_score": 88,
                                    "signals": ["pre_surge"],
                                    "sources": ["daily_discovery"],
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.123450")
    sources = service.page_sources("kis.symbol.123450")

    assert result["updated_count"] == 2
    assert page["status"] == "ok"
    assert service.read_page("kis.ops.manager_runs")["status"] == "ok"
    assert "Manager Opportunity Observations" in page["content"]
    assert "proactive_decision_pressure" in page["content"]
    assert "run_status=error" in page["content"]
    assert "codex native sdk timed out" in page["content"]
    assert "Manager run errors are repair memory" in page["content"]
    assert "pressure=action_required/high" in page["content"]
    assert "Action-required pressure is unresolved memory" in page["content"]
    assert "opportunity_research_brief.pre_surge" in page["content"]
    assert "눌림 이후 선행 수급" in page["content"]
    assert "pre_surge" in page["content"]
    assert "가격 조건 대기" in page["content"]
    assert any(
        row["source_type"] == "kis_manager_runs"
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_records_unresolved_wiki_attention_from_manager_runs(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T10:30:00+09:00",
                "regular",
                "ok",
                "",
                json.dumps(
                    {
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "top_candidates": [
                                {
                                    "symbol": "245450",
                                    "name": "씨앤에스링크",
                                    "aggressive_score": 87,
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "latest_input_summary": {
                            "jue_wiki_attention": {
                                "status": "active",
                                "must_address": ["repair_now"],
                                "resolution_status": "unresolved",
                                "repair_now": {
                                    "component": "repair_learning_resolution_metrics",
                                    "action_type": "wiki_repair",
                                    "recommended_resolution": (
                                        "반복 무시되는 repair_now를 다음 판단에서 "
                                        "명시적으로 해소한다."
                                    ),
                                    "impacted_symbols": ["245450"],
                                    "repair_targets": ["kis.symbol.245450"],
                                },
                            }
                        },
                        "no_action_watch": {
                            "status": "attention",
                            "hold_summary": "위키 수리 과제를 아직 처리하지 못했다.",
                            "top_candidates": [
                                {
                                    "symbol": "245450",
                                    "name": "씨앤에스링크",
                                    "aggressive_score": 87,
                                    "signals": ["wiki_attention"],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.245450")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "resolution=unresolved" in page["content"]
    assert "repair_learning_resolution_metrics" in page["content"]
    assert "kis.symbol.245450" in page["content"]


def test_rebuild_kis_symbols_records_additional_wiki_attention_from_manager_runs(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T10:35:00+09:00",
                "regular",
                "ok",
                "",
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "latest_input_summary": {
                            "jue_wiki_attention": {
                                "status": "active",
                                "must_address": [
                                    "repair_now",
                                    "additional_attention",
                                ],
                                "resolution_status": "unresolved",
                                "repair_now": {
                                    "component": "repair_learning_resolution_metrics",
                                    "action_type": "wiki_repair",
                                    "recommended_resolution": (
                                        "245450 수리 계약을 먼저 처리한다."
                                    ),
                                    "impacted_symbols": ["245450"],
                                    "repair_targets": ["kis.symbol.245450"],
                                },
                                "additional_attention": [
                                    {
                                        "component": "memory_card_quality",
                                        "action_type": (
                                            "cross_check_memory_card_quality"
                                        ),
                                        "recommended_resolution": (
                                            "005930 위키 기억을 최신 리서치와 "
                                            "교차검증한다."
                                        ),
                                        "impacted_symbols": ["005930"],
                                        "impacted_page_ids": ["kis.symbol.005930"],
                                    }
                                ],
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.005930")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "memory_card_quality" in page["content"]
    assert "cross_check_memory_card_quality" in page["content"]
    assert "005930 위키 기억을 최신 리서치" in page["content"]


def test_rebuild_kis_symbols_records_memory_card_quality_attention(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T11:00:00+09:00",
                "regular",
                "ok",
                "",
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "latest_input_summary": {
                            "jue_wiki_memory_card_quality": {
                                "status": "active",
                                "weak_symbols": ["005930"],
                                "required_action": (
                                    "cross_check_live_research_before_high_confidence"
                                ),
                                "missing_fields_by_symbol": [
                                    {
                                        "symbol": "005930",
                                        "status": "weak",
                                        "missing_fields": [
                                            "durable_facts",
                                            "lessons",
                                        ],
                                    }
                                ],
                                "required_checks": [
                                    (
                                        "refresh_durable_facts_from_reports_"
                                        "fundamentals_and_market_context"
                                    ),
                                    (
                                        "review_block_history_and_reflections_"
                                        "for_lessons"
                                    ),
                                ],
                                "resolution_status": "unresolved",
                            }
                        },
                        "hold_decision": {
                            "summary": "005930 위키 기억이 얇어 교차확인 필요",
                            "watch_symbols": ["005930"],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.005930")
    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        max_chars=1200,
    )

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Memory Card Quality" in page["content"]
    assert "resolution=unresolved" in page["content"]
    assert "cross_check_live_research_before_high_confidence" in page["content"]
    assert "missing_fields=durable_facts|lessons" in page["content"]
    assert (
        "required_checks=refresh_durable_facts_from_reports_fundamentals_and_market_context|"
        "review_block_history_and_reflections_for_lessons"
    ) in page["content"]
    assert "cross_check_live_research_before_high_confidence" in pack["content"]
    assert "missing_fields=durable_facts|lessons" in pack["content"]


def test_rebuild_kis_symbols_records_prompt_diagnostics_as_wiki_repair_memory(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T11:10:00+09:00",
                "regular",
                "ok",
                "",
                json.dumps(
                    {
                        "diagnostics": {
                            "version": "kis_manager_diagnostics_v1",
                            "blocker_tags": {
                                "unresolved_jue_wiki_requested_symbol_coverage": 2,
                                "unresolved_jue_wiki_attention_plan": 3,
                            },
                            "jue_wiki_missing_summary_symbols": ["000660"],
                            "jue_wiki_attention_must_address": [
                                "requested_symbol_summary_missing"
                            ],
                        },
                        "compact_manager_context": {
                            "diagnostics": {
                                "version": "kis_manager_diagnostics_v1",
                                "blocker_tags": {
                                    "unresolved_jue_wiki_requested_symbol_coverage": 2,
                                    "unresolved_jue_wiki_attention_plan": 3,
                                },
                                "jue_wiki_missing_summary_symbols": ["000660"],
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"hold_decision": {"summary": "관망"}}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.000660")
    pack = service.context_pack(
        target_scope="kis",
        symbols=["000660"],
        page_types=["symbol"],
        max_chars=1200,
    )

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "unresolved_jue_wiki_requested_symbol_coverage" in page["content"]
    assert "collect_or_rebuild_requested_symbol_wiki_summary" in page["content"]
    assert "prompt.diagnostics" in page["content"]
    assert "000660" in pack["content"]
    assert "collect_or_rebuild_requested_symbol_wiki_summary" in pack["content"]


def test_rebuild_kis_symbols_records_compact_context_wiki_evidence_quality(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T11:15:00+09:00",
                "regular",
                "ok",
                "",
                json.dumps(
                    {
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "top_candidates": [
                                {
                                    "symbol": "005930",
                                    "name": "삼성전자",
                                    "aggressive_score": 84,
                                    "signals": ["wiki_context"],
                                }
                            ],
                        },
                        "compact_manager_context": {
                            "jue_wiki_selection_observation": {
                                "selection_run_id": "selection:kis-context-quality",
                                "repair_action_batches": [
                                    {
                                        "scope": "kis",
                                        "action_type": "refresh_symbol_financials",
                                        "count": 3,
                                        "symbols": ["005930"],
                                    }
                                ],
                                "evidence_quality": {
                                    "summary_line": (
                                        "evidence_quality sources=2 weak=1"
                                    ),
                                    "status_counts": {"weak": 1, "strong": 1},
                                    "top_warnings": ["valuation_missing"],
                                },
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"hold_decision": {"summary": "관망"}}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.005930")
    queue_page = service.read_page("kis.research.repair_queue")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "refresh_symbol_financials" in page["content"]
    assert "Jue Wiki Evidence Quality" in page["content"]
    assert "evidence_quality sources=2 weak=1" in page["content"]
    assert "valuation_missing" in page["content"]
    assert queue_page["status"] == "ok"
    assert "refresh_symbol_financials" in queue_page["content"]
    assert "005930" in queue_page["content"]
    assert "valuation_missing" in queue_page["content"]


def test_rebuild_kis_symbols_resolves_manager_context_repair_after_strong_evidence(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            );
            """
        )
        for run_at, observation in [
            (
                "2026-07-01T10:00:00+09:00",
                {
                    "selection_run_id": "selection:kis-weak",
                    "repair_action_batches": [
                        {
                            "scope": "kis",
                            "action_type": "refresh_symbol_financials",
                            "count": 1,
                            "symbols": ["005930"],
                        }
                    ],
                    "evidence_quality": {
                        "summary_line": "evidence_quality sources=2 weak=1",
                        "status_counts": {"weak": 1, "strong": 1},
                        "top_warnings": ["valuation_missing"],
                    },
                },
            ),
            (
                "2026-07-01T11:00:00+09:00",
                {
                    "selection_run_id": "selection:kis-strong",
                    "evidence_quality": {
                        "summary_line": "evidence_quality sources=3 strong=3",
                        "status_counts": {"strong": 3},
                        "top_warnings": [],
                    },
                },
            ),
        ]:
            conn.execute(
                """
                INSERT INTO manager_runs (
                    run_at, market_session, status, error_message,
                    prompt_json, response_json, actions_json
                ) VALUES (?, 'regular', 'ok', '', ?, ?, ?)
                """,
                (
                    run_at,
                    json.dumps(
                        {
                            "proactive_decision_pressure": {
                                "top_candidates": [
                                    {
                                        "symbol": "005930",
                                        "name": "삼성전자",
                                        "aggressive_score": 78,
                                    }
                                ],
                            },
                            "compact_manager_context": {
                                "jue_wiki_selection_observation": observation
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps({"hold_decision": {"summary": "관망"}}, ensure_ascii=False),
                    json.dumps({}, ensure_ascii=False),
                ),
            )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    status = service.project_status_snapshot()

    assert result["status"] == "ok"
    assert status["repair_queue"]["open_count"] == 0
    assert status["repair_queue"]["resolved_count"] == 1
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, details_json, repair_lane, repair_lane_registered
            FROM wiki_repair_actions
            WHERE action_id = ?
            """,
            ("repair:manager_context:kis:refresh_symbol_financials:005930",),
        ).fetchone()
    assert row is not None
    assert row[0] == "resolved"
    assert row[2:] == ("evidence", 1)
    details = json.loads(row[1])
    assert details["resolved_by"] == "manager_context_evidence_quality_recovered"
    assert details["resolved_manager_run_id"]


@pytest.mark.parametrize("resolution_status", ["action_metadata", "hold_trigger"])
def test_rebuild_kis_symbols_does_not_mark_handled_wiki_attention_unresolved(
    tmp_path: Path,
    resolution_status: str,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T10:45:00+09:00",
                "regular",
                "ok",
                "",
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "latest_input_summary": {
                            "jue_wiki_attention": {
                                "status": "active",
                                "must_address": ["repair_now"],
                                "resolution_status": resolution_status,
                                "repair_now": {
                                    "component": "wiki_attention",
                                    "action_type": "live_probe",
                                    "recommended_resolution": (
                                        "위키 attention을 처리한 후보"
                                    ),
                                    "impacted_symbols": ["245450"],
                                    "repair_targets": ["kis.symbol.245450"],
                                },
                            }
                        },
                        "no_action_watch": {
                            "status": "attention",
                            "hold_summary": "위키 attention 처리 상태 기록",
                            "top_candidates": [
                                {
                                    "symbol": "245450",
                                    "name": "씨앤에스링크",
                                    "aggressive_score": 81,
                                    "signals": ["wiki_attention"],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.245450")

    assert f"resolution={resolution_status}" in page["content"]
    assert "Jue Wiki attention is unresolved repair memory" not in page["content"]


def test_rebuild_writes_manager_run_error_ops_pages_without_symbol_candidates(
    tmp_path: Path,
) -> None:
    for scope, table_extra, config_key, page_id, source_type in (
        (
            "kis",
            "market_session TEXT,",
            "kis_blocks_db_path",
            "kis.ops.manager_runs",
            "kis_manager_runs",
        ),
        (
            "binance",
            "mode TEXT, model TEXT,",
            "binance_blocks_db_path",
            "binance.ops.manager_runs",
            "binance_manager_runs",
        ),
    ):
        blocks_db = tmp_path / f"{scope}_blocks.db"
        with sqlite3.connect(blocks_db) as conn:
            conn.execute(
                """
                CREATE TABLE blocks (
                    block_id TEXT,
                    symbol TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE manager_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT,
                    {table_extra}
                    status TEXT,
                    error_message TEXT,
                    prompt_json TEXT,
                    response_json TEXT,
                    actions_json TEXT
                )
                """
            )
            extra_values = (
                ("regular",)
                if scope == "kis"
                else ("llm", "gpt-5.5")
            )
            placeholders = ",".join(["?"] * (6 + len(extra_values)))
            extra_columns = "market_session," if scope == "kis" else "mode, model,"
            conn.execute(
                f"""
                INSERT INTO manager_runs (
                    run_at, {extra_columns} status, error_message,
                    prompt_json, response_json, actions_json
                ) VALUES ({placeholders})
                """,
                (
                    "2026-07-02T01:00:00+00:00",
                    *extra_values,
                    "error",
                    "prompt_budget_exceeded: total_chars=250000 max_chars=190000",
                    json.dumps(
                        {
                            "prompt_budget": {"total_chars": 250000},
                            "_storage_compaction": {
                                "emergency": True,
                                "priority_reason": "manager_contract_recovery",
                                "dropped_keys": ["output_schema", "research_spine"],
                                "dropped_key_count": 2,
                            },
                        }
                    ),
                    json.dumps({}),
                    json.dumps({}),
                ),
            )

        service = JueWikiService(
            JueWikiConfig(
                root_path=tmp_path / f"{scope}_wiki",
                db_path=tmp_path / f"{scope}_wiki" / "wiki.db",
                **{config_key: blocks_db},
            )
        )

        result = service.rebuild(scope=scope, force=True)
        page = service.read_page(page_id)
        sources = service.page_sources(page_id)

        assert result["status"] == "ok"
        assert page["status"] == "ok"
        assert "Manager Run Operations" in page["content"]
        assert "prompt_budget_exceeded" in page["content"]
        assert "250,000" in page["content"]
        assert "priority=manager_contract_recovery" in page["content"]
        assert "dropped=output_schema,research_spine" in page["content"]
        assert service.lint(scope=scope)["status"] == "ok"
        assert any(
            row["source_type"] == source_type and row["source_id"] == "1"
            for row in sources["source_refs"]
        )


def test_rebuild_writes_action_pressure_ops_pages_from_no_action_runs(
    tmp_path: Path,
) -> None:
    for scope, table_extra, config_key, page_id in (
        (
            "kis",
            "market_session TEXT,",
            "kis_blocks_db_path",
            "kis.ops.action_pressure",
        ),
        (
            "binance",
            "mode TEXT, model TEXT,",
            "binance_blocks_db_path",
            "binance.ops.action_pressure",
        ),
    ):
        blocks_db = tmp_path / f"{scope}_blocks.db"
        with sqlite3.connect(blocks_db) as conn:
            conn.execute(
                """
                CREATE TABLE blocks (
                    block_id TEXT,
                    symbol TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE manager_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT,
                    {table_extra}
                    status TEXT,
                    error_message TEXT,
                    prompt_json TEXT,
                    response_json TEXT,
                    actions_json TEXT
                )
                """
            )
            prompt = {
                "proactive_decision_pressure": {
                    "status": "action_required",
                    "pressure_level": "high",
                    "zero_action_streak": 3,
                    "candidate_count": 9,
                    "strong_candidate_count": 2,
                    "required_resolution": (
                        "강한 후보는 대기블록, 탐색블록, 후보별 기각 조건 중 "
                        "하나로 해소해야 한다."
                    ),
                    "top_candidates": [
                        {"symbol": "005930" if scope == "kis" else "BTCUSDT"},
                    ],
                },
            }
            response = (
                {
                    "no_action_watch": {
                        "status": "attention",
                        "reason": "aggressive_candidates_seen_but_no_block_action",
                        "hold_summary": "후보는 보였지만 실행 구조를 못 만들었다.",
                    },
                }
                if scope == "kis"
                else {
                    "hold_decision": {
                        "summary": "관망했지만 다음 가격 트리거가 필요하다.",
                        "watch_symbols": ["BTCUSDT"],
                    },
                }
            )
            extra_values = ("regular",) if scope == "kis" else ("llm", "gpt-5.5")
            placeholders = ",".join(["?"] * (6 + len(extra_values)))
            extra_columns = "market_session," if scope == "kis" else "mode, model,"
            conn.execute(
                f"""
                INSERT INTO manager_runs (
                    run_at, {extra_columns} status, error_message,
                    prompt_json, response_json, actions_json
                ) VALUES ({placeholders})
                """,
                (
                    "2026-07-02T06:00:00+00:00",
                    *extra_values,
                    "ok",
                    "",
                    json.dumps(prompt, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                    json.dumps({"create_blocks": []}, ensure_ascii=False),
                ),
            )

        service = JueWikiService(
            JueWikiConfig(
                root_path=tmp_path / f"{scope}_wiki",
                db_path=tmp_path / f"{scope}_wiki" / "wiki.db",
                **{config_key: blocks_db},
            )
        )

        result = service.rebuild(scope=scope, force=True)
        page = service.read_page(page_id)
        sources = service.page_sources(page_id)

        assert result["status"] == "ok"
        assert page["status"] == "ok"
        assert "Action Pressure" in page["content"]
        assert "no_action_run_count=1" in page["content"]
        assert "zero_action_streak_max=3" in page["content"]
        assert "candidate_count_total=9" in page["content"]
        assert "probe/waiting block" in page["content"]
        assert any(row["source_id"] == "1" for row in sources["source_refs"])


def test_rebuild_writes_opportunity_pipeline_ops_pages_from_manager_backlog(
    tmp_path: Path,
) -> None:
    for scope, table_extra, config_key, page_id, symbol in (
        (
            "kis",
            "market_session TEXT,",
            "kis_blocks_db_path",
            "kis.ops.opportunity_pipeline",
            "123450",
        ),
        (
            "binance",
            "mode TEXT, model TEXT,",
            "binance_blocks_db_path",
            "binance.ops.opportunity_pipeline",
            "BTCUSDT",
        ),
    ):
        blocks_db = tmp_path / f"{scope}_opportunity_blocks.db"
        with sqlite3.connect(blocks_db) as conn:
            conn.execute(
                """
                CREATE TABLE blocks (
                    block_id TEXT,
                    symbol TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE manager_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT,
                    {table_extra}
                    status TEXT,
                    error_message TEXT,
                    prompt_json TEXT,
                    response_json TEXT,
                    actions_json TEXT
                )
                """
            )
            if scope == "kis":
                prompt = {
                    "aggressive_opportunities": {
                        "candidates": [
                            {
                                "symbol": symbol,
                                "name": "숨은후보",
                                "aggressive_score": 91,
                                "signals": ["pre_surge", "volume_expansion"],
                                "sources": ["daily_discovery", "quote"],
                                "summary": "급등 전 눌림 후보",
                            }
                        ]
                    },
                    "opportunity_research_brief": {
                        "pre_surge_candidates": [
                            {
                                "symbol": symbol,
                                "name": "숨은후보",
                                "score": 88,
                                "confidence": 0.7,
                                "stance": "waiting_entry",
                                "summary": "선행 수급과 거래대금 확장",
                            }
                        ]
                    },
                    "missed_upside_reviews": [
                        {
                            "symbol": symbol,
                            "name": "숨은후보",
                            "move_pct": 17.4,
                            "miss_reason": "대기 가격 구조가 없어서 블록화하지 못함",
                        }
                    ],
                    "proactive_decision_pressure": {
                        "status": "action_required",
                        "pressure_level": "high",
                        "candidate_count": 4,
                        "top_candidates": [{"symbol": symbol, "score": 91}],
                    },
                }
                response = {
                    "no_action_watch": {
                        "status": "attention",
                        "hold_summary": "후보는 있었지만 실행 가격 구조를 만들지 못했다.",
                    },
                    "creative_hypotheses": [
                        {
                            "symbol": symbol,
                            "idea": "전일 눌림 지지선을 기다리는 소형 대기블록",
                            "next_trigger": "전일 거래대금 재확장",
                        }
                    ],
                }
                extra_values = ("regular",)
                extra_columns = "market_session,"
            else:
                prompt = {
                    "candidates": {
                        "items": [
                            {
                                "symbol": symbol,
                                "market": "futures",
                                "lane": "volatile_attack",
                                "side": "long",
                                "horizon": "short",
                                "score": 83,
                                "confidence": 0.66,
                                "entry_style": "wait_for_price",
                                "entry_trigger_price": 62000,
                                "target_price": 63800,
                                "stop_price": 61200,
                                "reason_md": "스퀴즈 이후 눌림 대기 후보",
                            }
                        ]
                    },
                    "proactive_decision_pressure": {
                        "status": "action_required",
                        "pressure_level": "medium",
                        "candidate_count": 6,
                        "top_candidates": [{"symbol": symbol, "score": 83}],
                    },
                    "missed_upside_reviews": [
                        {
                            "symbol": symbol,
                            "move_pct": 8.2,
                            "miss_reason": "오더북 확인 지연으로 눌림 재진입을 놓침",
                        }
                    ],
                }
                response = {
                    "hold_decision": {
                        "summary": "실행 전 오더북 재확인이 필요했다.",
                        "next_triggers": [
                            {
                                "symbol": symbol,
                                "market": "futures",
                                "condition": "spread stable and 62000 retest",
                                "price": 62000,
                            }
                        ],
                    },
                    "creative_hypotheses": [
                        {
                            "symbol": symbol,
                            "idea": "변동성 lane 소액 breakout-probe",
                            "next_trigger": "funding neutral and depth recovered",
                        }
                    ],
                }
                extra_values = ("llm", "gpt-5.5")
                extra_columns = "mode, model,"
            placeholders = ",".join(["?"] * (6 + len(extra_values)))
            conn.execute(
                f"""
                INSERT INTO manager_runs (
                    run_at, {extra_columns} status, error_message,
                    prompt_json, response_json, actions_json
                ) VALUES ({placeholders})
                """,
                (
                    "2026-07-02T06:00:00+00:00",
                    *extra_values,
                    "ok",
                    "",
                    json.dumps(prompt, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                    json.dumps({}, ensure_ascii=False),
                ),
            )

        service = JueWikiService(
            JueWikiConfig(
                root_path=tmp_path / f"{scope}_opportunity_wiki",
                db_path=tmp_path / f"{scope}_opportunity_wiki" / "wiki.db",
                **{config_key: blocks_db},
            )
        )

        result = service.rebuild(scope=scope, force=True)
        page = service.read_page(page_id)
        sources = service.page_sources(page_id)

        assert result["status"] == "ok"
        assert page["status"] == "ok"
        assert "Opportunity Pipeline" in page["content"]
        assert symbol in page["content"]
        assert "candidate_backlog_count=1" in page["content"]
        assert "missed_upside_count=1" in page["content"]
        assert "creative_hypothesis_count=1" in page["content"]
        assert "waiting/probe block" in page["content"]
        assert any(row["source_id"] == "1" for row in sources["source_refs"])


def test_rebuild_manager_run_ops_page_reads_archived_manager_failures(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.execute(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                status TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_runs_archive (
                id INTEGER,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs_archive (
                id, run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1843,
                "2026-06-17T10:39:42+00:00",
                "error",
                "codex native sdk timed out after 600.0s",
                "llm",
                "gpt-5.5",
                json.dumps({"prompt_budget": {"total_chars": 210000, "max_chars": 190000}}),
                json.dumps({}),
                json.dumps({}),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.ops.manager_runs")
    sources = service.page_sources("binance.ops.manager_runs")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "manager_run=1843" in page["content"]
    assert "codex native sdk timed out" in page["content"]
    assert "210,000/190,000" in page["content"]
    assert any(
        row["source_type"] == "binance_manager_runs_archive"
        and row["source_id"] == "1843"
        for row in sources["source_refs"]
    )


def test_manager_run_ops_page_reserves_archive_failures_when_current_errors_are_many(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (block_id TEXT, symbol TEXT, status TEXT);
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            CREATE TABLE manager_runs_archive (
                id INTEGER,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        for idx in range(30):
            conn.execute(
                """
                INSERT INTO manager_runs (
                    run_at, status, error_message, mode, model,
                    prompt_json, response_json, actions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"2026-07-02T{idx % 23:02d}:00:00+00:00",
                    "error",
                    f"current_prompt_error_{idx}",
                    "llm",
                    "gpt-5.5",
                    "{}",
                    "{}",
                    "{}",
                ),
            )
        conn.execute(
            """
            INSERT INTO manager_runs_archive (
                id, run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1843,
                "2026-06-17T10:39:42+00:00",
                "error",
                "archived_timeout_should_remain_visible",
                "llm",
                "gpt-5.5",
                "{}",
                "{}",
                "{}",
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.ops.manager_runs")

    assert result["status"] == "ok"
    assert "archived_timeout_should_remain_visible" in page["content"]
    assert "current_prompt_error_29" in page["content"]


def test_rebuild_manager_run_ops_page_records_invalid_archived_json(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.execute(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                status TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_runs_archive (
                id INTEGER,
                run_at TEXT,
                market_session TEXT,
                status TEXT,
                error_message TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs_archive (
                id, run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                345,
                "2026-06-17T04:32:39+00:00",
                "regular",
                "error",
                "",
                "not-json-payload",
                "{}",
                "{}",
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.ops.manager_runs")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "manager_run=345" in page["content"]
    assert "invalid_json:manager_runs_archive.prompt_json" in page["content"]
    assert "not-json-payload" not in page["content"]


def test_rebuild_kis_symbols_records_manager_rejected_actions(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.execute(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, name TEXT, status TEXT,
                qty_initial INTEGER, entry_price REAL, target_price REAL,
                stop_price REAL, thesis TEXT, llm_reason TEXT, risk_note TEXT,
                created_at TEXT, updated_at TEXT, closed_at TEXT,
                realized_pnl REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO blocks VALUES (
                'blk_004980_1', '004980', '성신양회', 'open',
                3, 8963, 9800, 8650, 'mid horizon value-cycle probe',
                '', '', '2026-07-02T02:48:38+00:00',
                '2026-07-02T03:52:19+00:00', '', 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, market_session TEXT, status TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, market_session, status, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-02T12:52:19+09:00",
                "regular",
                "ok",
                "",
                json.dumps({}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "close_blocks": [
                            {
                                "block_id": "blk_004980_1",
                                "reason": "회복 확인 실패로 청산 제안",
                                "close_trigger": "thesis_invalidated",
                                "decision_class": "close_invalidated_open_probe",
                            }
                        ],
                        "_applied": {
                            "rejected": {
                                "item_count": 1,
                                "items": [
                                    {
                                        "action": "close",
                                        "block_id": "blk_004980_1",
                                        "reason": "horizon_patience_guard",
                                        "horizon": "mid",
                                        "target_price": 9800,
                                        "stop_price": 8650,
                                    }
                                ],
                                "omitted_item_count": 0,
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.004980")
    sources = service.page_sources("kis.symbol.004980")

    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "manager_applied_rejected" in page["content"]
    assert "horizon_patience_guard" in page["content"]
    assert "blk_004980_1" in page["content"]
    assert "회복 확인 실패로 청산 제안" in page["content"]
    assert "게이트가 거절한 적극 제안" in page["content"]
    assert any(
        row["source_type"] == "kis_manager_runs"
        and row["source_id"] == "1"
        for row in sources["source_refs"]
    )


def test_rebuild_binance_symbols_records_applied_rejected_create_actions(
    tmp_path: Path,
) -> None:
    blocks_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(blocks_db) as conn:
        conn.execute(
            """
            CREATE TABLE blocks (
                block_id TEXT, symbol TEXT, market TEXT, lane TEXT, side TEXT,
                status TEXT, qty_initial REAL, entry_price REAL,
                target_price REAL, stop_price REAL, thesis TEXT, llm_reason TEXT,
                risk_note TEXT, created_at TEXT, updated_at TEXT, closed_at TEXT,
                pnl_usdt REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT, status TEXT, mode TEXT, model TEXT, error_message TEXT,
                prompt_json TEXT, response_json TEXT, actions_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, mode, model, error_message,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-02T04:16:09+00:00",
                "ok",
                "live",
                "gpt-5.5",
                "",
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "applied": {
                            "created": [
                                {
                                    "status": "rejected",
                                    "reason": "entry_gate_rejected:weak_evidence",
                                    "input": {
                                        "symbol": "LINKUSDT",
                                        "market": "spot",
                                        "lane": "volatile_attack",
                                        "side": "long",
                                        "horizon": "short",
                                        "entry_style": "wait_for_price",
                                        "entry_trigger_price": 12.3,
                                        "target_price": 13.2,
                                        "stop_price": 11.8,
                                        "thesis": "breakout pullback probe",
                                    },
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "create_blocks": [
                            {
                                "symbol": "LINKUSDT",
                                "market": "spot",
                                "lane": "volatile_attack",
                                "side": "long",
                                "horizon": "short",
                                "entry_style": "wait_for_price",
                                "entry_trigger_price": 12.3,
                                "target_price": 13.2,
                                "stop_price": 11.8,
                                "thesis": "breakout pullback probe",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=blocks_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.symbol.LINKUSDT")
    sources = service.page_sources("binance.symbol.LINKUSDT")

    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "manager_applied_created_rejected" in page["content"]
    assert "entry_gate_rejected:weak_evidence" in page["content"]
    assert "volatile_attack" in page["content"]
    assert "게이트가 거절한 적극 제안" in page["content"]
    assert any(
        row["source_type"] == "binance_manager_runs"
        and row["source_id"] == "1"
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_uses_daily_discovery_research_without_blocks(
    tmp_path: Path,
) -> None:
    discovery_db = tmp_path / "jue_daily_discovery.db"
    DailyDiscoveryRepository(str(discovery_db)).save_run(
        {
            "trading_day": "2026-07-01",
            "status": "ok",
            "selected_symbols": [{"symbol": "178920", "name": "피아이첨단소재"}],
            "results": [
                {
                    "symbol": "178920",
                    "name": "피아이첨단소재",
                    "market": "KOSPI",
                    "status": "ok",
                    "score": 84.5,
                    "analysis": {
                        "name": "피아이첨단소재",
                        "stance": "block_candidate",
                        "confidence": 0.78,
                        "summary": "저평가와 수급 개선이 동시에 관측된다.",
                        "reasons": ["저점권", "거래대금 증가", "섹터 재평가"],
                        "risks": ["실적 확인 필요"],
                    },
                    "pre_surge": {
                        "is_candidate": True,
                        "score": 88,
                        "reasons": ["저점권", "거래대금"],
                    },
                }
            ],
            "summary": {
                "selected_count": 1,
                "pre_surge_candidate_count": 1,
            },
        }
    )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            daily_discovery_db_path=discovery_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.178920")
    sources = service.page_sources("kis.symbol.178920")

    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "Daily Discovery Research" in page["content"]
    assert "저평가와 수급 개선" in page["content"]
    assert "대기블록/1주 프로브/기각 조건" in page["content"]
    assert any(
        row["source_type"] == "daily_discovery"
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_marks_etf_daily_discovery_research(
    tmp_path: Path,
) -> None:
    discovery_db = tmp_path / "jue_daily_discovery.db"
    DailyDiscoveryRepository(str(discovery_db)).save_run(
        {
            "trading_day": "2026-07-03",
            "status": "ok",
            "selected_symbols": [
                {
                    "symbol": "069500",
                    "name": "KODEX 200",
                    "market": "ETF",
                    "asset_class": "etf",
                }
            ],
            "results": [
                {
                    "symbol": "069500",
                    "name": "KODEX 200",
                    "market": "ETF",
                    "asset_class": "etf",
                    "status": "ok",
                    "score": 76.0,
                    "analysis": {
                        "name": "KODEX 200",
                        "stance": "confirm",
                        "confidence": 0.72,
                        "summary": "KODEX 200 ETF discovery: core_etf 후보로 유동성과 추종지수 확인.",
                        "reasons": ["거래대금 충분", "시장 대표 익스포저"],
                        "risks": ["추적오차와 괴리 확인 필요"],
                    },
                }
            ],
            "summary": {
                "selected_count": 1,
                "etf_count": 1,
            },
        }
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            daily_discovery_db_path=discovery_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.069500")

    assert result["updated_count"] == 1
    assert "Daily Discovery Research" in page["content"]
    assert "market=ETF" in page["content"]
    assert "asset_class=etf" in page["content"]
    assert "core_etf 후보" in page["content"]


def test_rebuild_kis_symbols_reconstructs_daily_discovery_pre_surge(
    tmp_path: Path,
) -> None:
    discovery_db = tmp_path / "jue_daily_discovery.db"
    DailyDiscoveryRepository(str(discovery_db)).save_run(
        {
            "trading_day": "2026-07-02",
            "status": "ok",
            "selected_symbols": [{"symbol": "001390", "name": "KG케미칼"}],
            "results": [
                {
                    "symbol": "001390",
                    "name": "KG케미칼",
                    "market": "KOSPI",
                    "status": "ok",
                    "score": 91.2,
                    "analysis": {
                        "name": "KG케미칼",
                        "stance": "block_candidate",
                        "confidence": 0.82,
                        "summary": "저평가 눌림목에서 거래대금과 순환매 가능성이 붙었다.",
                        "reasons": ["저평가", "눌림목", "거래대금 증가"],
                        "risks": ["추격 매수 주의"],
                    },
                }
            ],
            "summary": {
                "selected_count": 1,
                "pre_surge_candidate_count": 1,
            },
        }
    )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            daily_discovery_db_path=discovery_db,
        )
    )

    service.rebuild(scope="kis", force=True)
    page = service.read_page("kis.symbol.001390")

    assert page["status"] == "ok"
    assert "pre_surge=True" in page["content"]
    assert "scout_or_waiting_block" in page["content"]


def test_rebuild_adds_trading_validation_risk_page_without_blocks(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    TradingValidationRepository(validation_db).save_run(
        {
            "run_id": "validation-binance-1",
            "scope": "live",
            "venue": "binance",
            "strategy_revision_id": "jue_edge_repair_v1",
            "computed_at": "2026-07-01T00:00:00+00:00",
            "status": "ok",
            "summary": {
                "total_score": 34.21,
                "readiness": "probe",
                "diagnostic_status": "risk_repair",
                "pass_count": 4,
                "warn_count": 5,
                "fail_count": 10,
                "missing_count": 0,
                "active_revision_sample_count": 51,
                "min_samples_to_scale": 30,
                "scale_up_allowed": True,
            },
            "discipline_count": 19,
            "disciplines": [
                {
                    "id": "cost_simulation",
                    "label": "거래비용 시뮬레이션",
                    "status": "fail",
                    "evidence": "비용 2배 stress 후 net PnL이 음수입니다.",
                    "action": "비용 검증 전까지 즉시진입보다 대기진입을 우선합니다.",
                },
                {
                    "id": "data_validation",
                    "label": "데이터 검증",
                    "status": "pass",
                    "evidence": "데이터 이슈 0개입니다.",
                    "action": "데이터 품질은 유지합니다.",
                },
            ],
            "metrics": {
                "sample_count": 51,
                "total_net_pnl": -29.28,
                "win_rate_pct": 31.4,
                "profit_factor": 0.098,
            },
            "remediation_plan": {
                "status": "active_repair",
                "primary_next_action": "sync precise fills/costs",
                "work_queue": [
                    {
                        "task_id": "validation:cost_simulation:fail",
                        "discipline_id": "cost_simulation",
                        "priority": "p0",
                        "automation_hook": "sync_live_performance_and_edges",
                        "allowed_entry_posture": "cost_verified_waiting_entry",
                        "exit_criteria": "2x cost stress stays net-positive.",
                    }
                ],
            },
            "lane_authority_summary": {
                "status": "probe",
                "probe_lane_count": 2,
                "probe_lane_names": ["futures:short", "volatile_attack"],
                "scale_blocked_lane_count": 1,
                "scale_blocked_lanes": ["spot"],
                "execution_posture": "probe_allowed_scale_blocked",
            },
        }
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            trading_validation_db_path=validation_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)

    page = service.read_page("binance.risk.trading_validation")
    sources = service.page_sources("binance.risk.trading_validation")
    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "Trading Validation Risk" in page["content"]
    assert "거래비용 시뮬레이션" in page["content"]
    assert "cost_verified_waiting_entry" in page["content"]
    assert "probe_lane_count=2" in page["content"]
    assert "probe_lanes=futures:short, volatile_attack" in page["content"]
    assert "execution_posture=probe_allowed_scale_blocked" in page["content"]
    assert any(
        row["source_type"] == "trading_validation"
        and row["source_id"] == "validation-binance-1"
        for row in sources["source_refs"]
    )


def test_rebuild_writes_codex_lab_green_path_pages(tmp_path: Path) -> None:
    lab_db = tmp_path / "codex_lab.db"
    store = JueCodexLabStore(lab_db)
    store.initialize()
    store.record_green_path_progress(
        venue="binance",
        discipline_id="cost_simulation",
        before_status="fail",
        after_status="warn",
        before_score=0.24,
        after_score=0.71,
        validation_run_before="validation-before",
        validation_run_after="validation-after",
        repair_task_id="binance:validation:cost_simulation",
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            jue_codex_lab_db_path=lab_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.ops.codex_lab_green_path")

    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "cost_simulation" in page["content"]
    assert "fail -> warn" in page["content"]
    assert "binance:validation:cost_simulation" in page["content"]
    assert "validation-before" in page["content"]
    assert "validation-after" in page["content"]


def test_rebuild_codex_lab_green_path_filters_venue_before_limit(
    tmp_path: Path,
) -> None:
    lab_db = tmp_path / "codex_lab.db"
    store = JueCodexLabStore(lab_db)
    store.initialize()
    target = store.record_green_path_progress(
        venue="binance",
        discipline_id="cost_simulation",
        before_status="fail",
        after_status="warn",
        before_score=0.24,
        after_score=0.71,
        validation_run_before="validation-binance-before",
        validation_run_after="validation-binance-after",
        repair_task_id="binance:validation:cost_simulation",
    )
    with sqlite3.connect(lab_db) as conn:
        conn.execute(
            "UPDATE green_path_progress SET created_at = ? WHERE progress_id = ?",
            ("2026-07-02T00:00:00+00:00", target["progress_id"]),
        )
    for index in range(55):
        row = store.record_green_path_progress(
            venue="kis",
            discipline_id=f"kis_noise_{index}",
            before_status="fail",
            after_status="warn",
            before_score=0.1,
            after_score=0.2,
            validation_run_before=f"validation-kis-before-{index}",
            validation_run_after=f"validation-kis-after-{index}",
            repair_task_id=f"kis:validation:noise:{index}",
        )
        with sqlite3.connect(lab_db) as conn:
            conn.execute(
                "UPDATE green_path_progress SET created_at = ? WHERE progress_id = ?",
                (f"2026-07-02T00:{index + 1:02d}:00+00:00", row["progress_id"]),
            )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            jue_codex_lab_db_path=lab_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.ops.codex_lab_green_path")

    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "cost_simulation" in page["content"]
    assert "fail -> warn" in page["content"]


def test_rebuild_removes_codex_lab_green_path_page_when_rows_disappear(
    tmp_path: Path,
) -> None:
    lab_db = tmp_path / "codex_lab.db"
    store = JueCodexLabStore(lab_db)
    store.initialize()
    store.record_green_path_progress(
        venue="binance",
        discipline_id="cost_simulation",
        before_status="fail",
        after_status="warn",
        before_score=0.24,
        after_score=0.71,
        validation_run_before="validation-before",
        validation_run_after="validation-after",
        repair_task_id="binance:validation:cost_simulation",
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            jue_codex_lab_db_path=lab_db,
        )
    )

    first = service.rebuild(scope="binance", force=True)
    with sqlite3.connect(lab_db) as conn:
        conn.execute("DELETE FROM green_path_progress")
    second = service.rebuild(scope="binance", force=True)
    sources = service.page_sources("binance.ops.codex_lab_green_path")

    assert first["updated_count"] == 1
    assert second["status"] == "ok"
    assert second["updated_count"] == 0
    assert (
        service.read_page("binance.ops.codex_lab_green_path")["status"] == "not_found"
    )
    assert sources["status"] == "not_found"
    assert sources["source_refs"] == []


def test_rebuild_ignores_missing_codex_lab_green_path_db(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            jue_codex_lab_db_path=tmp_path / "missing_codex_lab.db",
        )
    )

    result = service.rebuild(scope="binance", force=True)

    assert result["status"] == "ok"
    assert (
        service.read_page("binance.ops.codex_lab_green_path")["status"] == "not_found"
    )


def test_rebuild_reports_corrupt_codex_lab_green_path_db(tmp_path: Path) -> None:
    lab_db = tmp_path / "corrupt_codex_lab.db"
    lab_db.write_text("not a sqlite database", encoding="utf-8")
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            jue_codex_lab_db_path=lab_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)

    assert result["status"] == "error"
    assert "failed to read Codex Lab green path DB" in result["error_message"]


def test_trading_validation_risk_page_derives_lane_authority_from_scorecards(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    TradingValidationRepository(validation_db).save_run(
        {
            "run_id": "validation-kis-1",
            "scope": "live",
            "venue": "kis",
            "computed_at": "2026-07-01T00:00:00+00:00",
            "status": "ok",
            "summary": {
                "total_score": 65.0,
                "readiness": "probe",
                "diagnostic_status": "watch",
                "pass_count": 6,
                "warn_count": 13,
                "fail_count": 0,
                "missing_count": 0,
                "scale_up_allowed": True,
            },
            "metrics": {
                "lane_scorecards": {
                    "version": "lane_scorecards_v1",
                    "insufficient_lanes": ["mid"],
                    "lane_actions": {
                        "mid": {
                            "grade": "insufficient",
                            "action": "small_probe_until_sample_builds",
                            "requires_waiting_entry": True,
                            "scale_up_allowed": False,
                        },
                        "long": {
                            "grade": "qualified",
                            "action": "shadow_or_waiting_entry_until_validation_rebuilt",
                            "requires_waiting_entry": True,
                            "scale_up_blocked_by_shadow_gate": True,
                        },
                    },
                }
            },
            "disciplines": [],
        }
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            trading_validation_db_path=validation_db,
        )
    )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.risk.trading_validation")
    assert "probe_lane_count=2" in page["content"]
    assert "probe_lanes=mid, long" in page["content"]
    assert "scale_blocked_lane_count=1" in page["content"]
    assert "execution_posture=probe_allowed_scale_blocked" in page["content"]


def test_trading_validation_risk_page_keeps_sample_building_reduced_lanes(
    tmp_path: Path,
) -> None:
    validation_db = tmp_path / "trading_validation.db"
    TradingValidationRepository(validation_db).save_run(
        {
            "run_id": "validation-kis-sample-building",
            "scope": "live",
            "venue": "kis",
            "computed_at": "2026-07-02T00:00:00+00:00",
            "status": "ok",
            "summary": {
                "total_score": 65.79,
                "readiness": "probe",
                "diagnostic_status": "watch",
                "warn_count": 13,
                "fail_count": 0,
                "active_revision_sample_count": 5,
                "min_samples_to_scale": 30,
                "scale_up_allowed": False,
            },
            "metrics": {
                "sample_count": 5,
                "lane_scorecards": {
                    "version": "lane_scorecards_v1",
                    "status": "warn",
                    "insufficient_lanes": ["core_etf", "long", "mid"],
                    "lane_actions": {
                        "core_etf": {
                            "grade": "insufficient",
                            "action": "small_probe_until_sample_builds",
                        },
                        "long": {
                            "grade": "insufficient",
                            "action": "small_probe_until_sample_builds",
                        },
                        "mid": {
                            "grade": "insufficient",
                            "action": "small_probe_until_sample_builds",
                        },
                    },
                },
            },
            "disciplines": [],
        }
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            trading_validation_db_path=validation_db,
        )
    )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.risk.trading_validation")
    assert "execution_posture=probe_allowed_sample_building" in page["content"]
    assert "probe_lanes=core_etf, long, mid" in page["content"]
    assert "reduced_lane_count=3" in page["content"]
    assert "reduced_lanes=core_etf, long, mid" in page["content"]
    assert "scale_blocked_lane_count=0" in page["content"]


def test_rebuild_writes_research_coverage_pages_for_kis_and_binance(
    tmp_path: Path,
) -> None:
    reports_db = tmp_path / "naver_reports.db"
    with sqlite3.connect(reports_db) as conn:
        conn.execute(
            """
            CREATE TABLE reports (
                report_id TEXT,
                symbol TEXT,
                title TEXT,
                company_name TEXT,
                broker TEXT,
                detail_url TEXT,
                pdf_url TEXT,
                published_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "r1",
                    "005930",
                    "삼성전자 리포트",
                    "삼성전자",
                    "테스트증권",
                    "",
                    "",
                    "2026-07-01T00:00:00+00:00",
                ),
                (
                    "r2",
                    "000660",
                    "SK하이닉스 리포트",
                    "SK하이닉스",
                    "테스트증권",
                    "",
                    "",
                    "2026-07-02T00:00:00+00:00",
                ),
            ],
        )
    etf_db = tmp_path / "etf_research.db"
    with sqlite3.connect(etf_db) as conn:
        conn.execute(
            """
            CREATE TABLE etf_market_snapshots (
                id INTEGER,
                symbol TEXT,
                captured_at TEXT,
                status TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO etf_market_snapshots VALUES (1, '069500', ?, 'ok')",
            ("2026-07-02T01:00:00+00:00",),
        )
    fundamentals_db = tmp_path / "symbol_fundamentals.db"
    with sqlite3.connect(fundamentals_db) as conn:
        conn.execute(
            """
            CREATE TABLE valuation_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                name TEXT,
                crawled_at TEXT,
                as_of TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO valuation_snapshots (symbol, name, crawled_at, as_of)
            VALUES ('005930', '삼성전자', ?, '2026-07-02')
            """,
            ("2026-07-02T01:30:00+00:00",),
        )
    crypto_db = tmp_path / "crypto_market_research.db"
    with sqlite3.connect(crypto_db) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_market_snapshots (
                id INTEGER,
                symbol TEXT,
                captured_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO crypto_market_snapshots VALUES (1, 'BTCUSDT', ?)",
            ("2026-07-02T02:00:00+00:00",),
        )
    quant_db = tmp_path / "crypto_quant.db"
    with sqlite3.connect(quant_db) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_quant_signals (
                symbol TEXT,
                horizon TEXT,
                long_score REAL,
                short_score REAL,
                no_trade_score REAL,
                expected_r_long REAL,
                expected_r_short REAL,
                signal_json TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO crypto_quant_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "BTCUSDT",
                "intraday",
                0.8,
                0.2,
                0.1,
                1.2,
                -0.2,
                "{}",
                "2026-07-02T03:00:00+00:00",
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            naver_reports_db_path=reports_db,
            symbol_fundamentals_db_path=fundamentals_db,
            etf_research_db_path=etf_db,
            crypto_market_research_db_path=crypto_db,
            crypto_quant_db_path=quant_db,
        )
    )

    result = service.rebuild(scope="all", force=True)

    kis_page = service.read_page("kis.research.coverage")
    binance_page = service.read_page("binance.research.coverage")
    kis_sources = service.page_sources("kis.research.coverage")
    assert result["status"] == "ok"
    assert kis_page["status"] == "ok"
    assert "naver_reports" in kis_page["content"]
    assert "rows=2" in kis_page["content"]
    assert "symbol_fundamentals" in kis_page["content"]
    assert "latest_at=2026-07-02T01:30:00+00:00" in kis_page["content"]
    assert "etf_research" in kis_page["content"]
    assert "latest_at=2026-07-02T01:00:00+00:00" in kis_page["content"]
    assert binance_page["status"] == "ok"
    assert "crypto_market_research" in binance_page["content"]
    assert "crypto_quant" in binance_page["content"]
    assert "latest_at=2026-07-02T03:00:00+00:00" in binance_page["content"]
    assert any(
        row["source_type"] == "research_coverage"
        and row["source_id"] == "naver_reports"
        for row in kis_sources["source_refs"]
    )
    assert any(
        row["source_type"] == "research_coverage"
        and row["source_id"] == "symbol_fundamentals"
        for row in kis_sources["source_refs"]
    )


def test_rebuild_writes_evidence_quality_page_from_quality_refs(
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
            "Current Stance": "valuation aware page",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "삼성전자 compact summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "observed_at": "2026-07-03T00:00:00+00:00",
                "quality_status": "partial",
                "quality_warnings": ["financial_metrics_sparse"],
            },
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-02",
                "quality_status": "weak",
                "quality_warnings": ["price_missing", "valuation_metrics_sparse"],
            },
            {
                "source_type": "naver_reports",
                "source_id": "005930:2026-07-04",
                "quality_status": "ok",
            },
            {
                "source_type": "repair_queue",
                "source_id": "005930:2026-07-05",
                "quality_status": "degraded",
            },
        ],
        confidence=0.8,
        freshness="fresh",
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.evidence_quality")
    sources = service.page_sources("kis.research.evidence_quality")
    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "quality_tagged_source_count=4" in page["content"]
    assert "strong_source_count=1" in page["content"]
    assert "partial_source_count=1" in page["content"]
    assert "weak_source_count=2" in page["content"]
    assert "financial_metrics_sparse:1" in page["content"]
    assert "valuation_metrics_sparse:1" in page["content"]
    assert "page=kis.symbol.005930" in page["content"]
    assert "weak=2, partial=1" in page["content"]
    assert any(
        row["source_type"] == "wiki_evidence_quality"
        and "kis.symbol.005930:symbol_fundamentals" in row["source_id"]
        for row in sources["source_refs"]
    )


def test_rebuild_writes_evidence_quality_page_from_compact_evidence_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 compact evidence quality",
        symbols=["005930"],
        content_sections={
            "Current Stance": "valuation aware page",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "삼성전자 compact summary",
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
                                "financial_metrics_sparse": 1,
                                "price_missing": 1,
                            },
                            "source_type_counts": {
                                "symbol_fundamentals": 2,
                            },
                            "top_warnings": [
                                {
                                    "warning": "financial_metrics_sparse",
                                    "count": 1,
                                },
                                {"warning": "price_missing", "count": 1},
                            ],
                        },
                    }
                ],
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.evidence_quality")
    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "quality_tagged_source_count=2" in page["content"]
    assert "partial_source_count=1" in page["content"]
    assert "weak_source_count=1" in page["content"]
    assert "financial_metrics_sparse:1" in page["content"]
    assert "price_missing:1" in page["content"]
    assert "source_type=symbol_fundamentals, quality_tagged_refs=2" in page[
        "content"
    ]


def test_rebuild_evidence_quality_page_reads_alias_only_nested_status_counts(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 alias-only evidence quality",
        symbols=["005930"],
        content_sections={
            "Current Stance": "Nested evidence quality has alias status only.",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "삼성전자 alias-only summary",
        },
        source_refs=[
            {
                "source_type": "wiki_symbol_summary",
                "source_id": "kis.symbol.005930:alias-only",
                "evidence_quality": {
                    "source_count": 1,
                    "status_counts": {"degraded": 1},
                    "source_type_counts": {"symbol_fundamentals": 1},
                },
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.evidence_quality")
    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "quality_tagged_source_count=1" in page["content"]
    assert "weak_source_count=1" in page["content"]
    assert "source_type=symbol_fundamentals, quality_tagged_refs=1" in page[
        "content"
    ]
    assert "page=kis.symbol.005930" in page["content"]
    assert "weak=1, partial=0" in page["content"]


def test_rebuild_writes_repair_queue_page_from_wiki_repair_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:financials', 'evidence_quality:financials',
                'kis.symbol.245450', 'refresh_symbol_financials', 'scheduled',
                '{"symbols":["245450"],"quality_warnings":["financials_missing"],"repair_action":"collect or cross-check financial statements","impacted_page_ids":["kis.symbol.245450"],"impacted_symbols":["245450"],"repair_targets":[{"page_id":"kis.symbol.245450","symbol":"245450","recommended_action":"refresh_symbol_financials_and_rewrite_page_evidence"}]}',
                '2026-07-03T01:00:00+00:00', '', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:done', 'evidence_quality:done',
                'kis.symbol.033100', 'refresh_symbol_fundamentals', 'resolved',
                '{"symbols":["033100"],"resolved_by":"symbol_fundamentals_collect"}',
                '2026-07-03T00:00:00+00:00',
                '2026-07-03T02:00:00+00:00', ''
            )
            """
        )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.repair_queue")
    sources = service.page_sources("kis.research.repair_queue")
    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "open_action_count=1" in page["content"]
    assert "resolved_action_count=1" in page["content"]
    assert "245450" in page["content"]
    assert "refresh_symbol_financials" in page["content"]
    assert "financials_missing" in page["content"]
    assert "warning=financials_missing, count=1" in page["content"]
    assert "## Action Batches" in page["content"]
    assert (
        "- action_type=refresh_symbol_financials, count=1, symbols=245450, "
        "warnings=financials_missing, "
        "recommended_actions=refresh_symbol_financials_and_rewrite_page_evidence"
    ) in page["content"]
    assert "impacted_pages=kis.symbol.245450" in page["content"]
    assert "recommended_actions=refresh_symbol_financials_and_rewrite_page_evidence" in page[
        "content"
    ]
    assert any(
        row["source_type"] == "wiki_repair_queue"
        and row["source_id"] == "repair:financials"
        for row in sources["source_refs"]
    )
    repair_ref = next(
        row
        for row in sources["source_refs"]
        if row["source_type"] == "wiki_repair_queue"
        and row["source_id"] == "repair:financials"
    )
    assert repair_ref["action_type"] == "refresh_symbol_financials"
    assert repair_ref["status"] == "scheduled"
    assert repair_ref["symbols"] == ["245450"]
    assert repair_ref["quality_warnings"] == ["financials_missing"]
    assert repair_ref["impacted_page_ids"] == ["kis.symbol.245450"]
    assert repair_ref["impacted_symbols"] == ["245450"]
    assert repair_ref["repair_targets"] == [
        {
            "page_id": "kis.symbol.245450",
            "symbol": "245450",
            "recommended_action": (
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ),
        }
    ]


def test_record_selection_run_canonicalizes_degraded_summary_repair_quality_alias(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.record_selection_run(
        run_id="selection:kis:alias-repair",
        target_scope="kis",
        request={"symbols": ["005930"]},
        selected_pages=[],
        rejected_pages=[],
        char_count=0,
        max_chars=20_000,
        status="ok",
        budget_report={
            "requested_symbol_count": 1,
            "requested_symbol_summary_count": 1,
            "requested_symbol_summary_coverage_status": "full",
            "requested_symbol_degraded_summary_count": 1,
            "requested_symbol_degraded_summary_symbols": ["005930"],
            "requested_symbol_degraded_summary_reasons": [
                {
                    "symbol": "005930",
                    "freshness": "fresh",
                    "quality_status": "degraded",
                    "quality_warnings": ["valuation_stale_gt_30d"],
                }
            ],
        },
    )

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        details_json = conn.execute(
            """
            SELECT details_json
            FROM wiki_repair_actions
            WHERE action_id = 'repair:degraded_summary:kis:005930'
            """
        ).fetchone()[0]
    details = json.loads(details_json)
    assert details["quality_status"] == "weak"
    assert "quality_status:weak" in details["reasons"]
    assert "quality_status:degraded" not in details["reasons"]


def test_repair_queue_page_prioritizes_quality_warning_effectiveness_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        for idx in range(20):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')
                """,
                (
                    f"repair:generic:{idx}",
                    f"evidence_quality:generic:{idx}",
                    f"kis.symbol.{idx:06d}",
                    "refresh_symbol_fundamentals",
                    "scheduled",
                    '{"symbols":["000000"],"quality_warnings":["partial_evidence"],"repair_action":"generic refresh"}',
                    f"2026-07-03T02:{idx:02d}:00+00:00",
                ),
            )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:quality-warning:financials',
                'quality_warning_effectiveness:financials',
                'quality_warning.financials_missing',
                'repair_quality_warning_effectiveness',
                'scheduled',
                '{"decision_scope":"kis","quality_warnings":["financials_missing"],"sample_count":24,"win_rate":0.3333,"expectancy":-2.0013,"repair_action":"repair or downgrade evidence carrying financials_missing","reasons":["samples:24","expectancy:-2.0013"]}',
                '2026-07-03T01:00:00+00:00', '', ''
            )
            """
        )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.repair_queue")
    sources = service.page_sources("kis.research.repair_queue")

    assert page["status"] == "ok"
    assert "repair_quality_warning_effectiveness" in page["content"]
    assert "quality_effectiveness_reasons=samples:24 | expectancy:-2.0013" in page[
        "content"
    ]
    data_gaps = page["content"].split("## Data Gaps", 1)[1]
    assert data_gaps.find("repair_quality_warning_effectiveness") < data_gaps.find(
        "refresh_symbol_fundamentals"
    )
    symbols_line = next(
        line for line in page["content"].splitlines() if line.startswith("symbols:")
    )
    assert "FINANCIALS_MISSING" not in symbols_line
    quality_warning_source = next(
        row
        for row in sources["source_refs"]
        if row["source_id"] == "repair:quality-warning:financials"
    )
    assert quality_warning_source["symbols"] == []


def test_repair_queue_page_prioritizes_research_coverage_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        for idx in range(20):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')
                """,
                (
                    f"repair:generic:{idx}",
                    f"evidence_quality:generic:{idx}",
                    f"kis.symbol.{idx:06d}",
                    "refresh_symbol_fundamentals",
                    "scheduled",
                    '{"symbols":["000000"],"quality_warnings":["partial_evidence"],"repair_action":"generic refresh"}',
                    f"2026-07-03T02:{idx:02d}:00+00:00",
                ),
            )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:research-coverage:daily-discovery',
                'research_coverage:kis:daily_discovery:missing_table',
                'kis.research.coverage',
                'repair_research_source_schema',
                'scheduled',
                '{"scope":"kis","source_id":"daily_discovery","source_status":"missing_table","source_reported_status":"ok","quality_warnings":["research_coverage_unhealthy"],"table_issues":[{"table":"discovery_samples","status":"missing_table"}],"repair_action":"repair daily_discovery schema migration before rebuilding wiki"}',
                '2026-07-03T03:00:00+00:00', '', ''
            )
            """
        )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.repair_queue")
    data_gaps = page["content"].split("## Data Gaps", 1)[1]
    assert "source=daily_discovery" in data_gaps
    assert "source_status=missing_table" in data_gaps
    assert data_gaps.find("repair_research_source_schema") < data_gaps.find(
        "refresh_symbol_fundamentals"
    )


def test_repair_queue_page_surfaces_application_horizon_repairs_without_fake_symbol(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:generic:late',
                'evidence_quality:generic:late',
                'kis.symbol.005930',
                'cross_check_evidence_quality',
                'scheduled',
                '{"symbols":["005930"],"quality_warnings":["partial_evidence"],"repair_action":"generic cross-check"}',
                '2026-07-03T00:00:00+00:00', '', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:outcome-horizon:kis',
                'wiki_application_coverage:kis:outcome_horizon',
                'kis.application.closed_block_outcomes',
                'reproject_closed_block_outcome_horizons',
                'scheduled',
                '{"decision_scope":"kis","quality_warnings":["closed_block_outcome_horizon_missing"],"closed_block_outcomes_without_horizon":3,"closed_block_outcomes_without_horizon_pct":75.0,"repair_action":"reproject closed block outcomes so page effectiveness is credited to the block horizon or crypto lane","reasons":["closed_block_outcomes_without_horizon:3"],"repair_targets":[{"page_id":"kis.application.closed_block_outcomes","recommended_action":"reproject_closed_block_outcomes_with_block_horizon_or_lane"}]}',
                '2026-07-03T02:00:00+00:00', '', ''
            )
            """
        )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.repair_queue")
    sources = service.page_sources("kis.research.repair_queue")

    assert page["status"] == "ok"
    assert "reproject_closed_block_outcome_horizons" in page["content"]
    assert "closed_block_outcome_horizon_missing" in page["content"]
    assert (
        "recommended_actions="
        "reproject_closed_block_outcomes_with_block_horizon_or_lane"
    ) in page["content"]
    assert "diagnostic_reasons=closed_block_outcomes_without_horizon:3" in page[
        "content"
    ]
    data_gaps = page["content"].split("## Data Gaps", 1)[1]
    assert data_gaps.find("reproject_closed_block_outcome_horizons") < (
        data_gaps.find("cross_check_evidence_quality")
    )
    symbols_line = next(
        line for line in page["content"].splitlines() if line.startswith("symbols:")
    )
    assert "CLOSED_BLOCK_OUTCOMES" not in symbols_line
    repair_source = next(
        row
        for row in sources["source_refs"]
        if row["source_id"] == "repair:outcome-horizon:kis"
    )
    assert repair_source["symbols"] == []
    assert repair_source["quality_warnings"] == [
        "closed_block_outcome_horizon_missing"
    ]
    assert repair_source["decision_scope"] == "kis"
    assert repair_source["closed_block_outcomes_without_horizon"] == 3
    assert repair_source["closed_block_outcomes_without_horizon_pct"] == 75.0
    assert repair_source["diagnostic_reasons"] == [
        "closed_block_outcomes_without_horizon:3"
    ]
    assert repair_source["repair_targets"] == [
        {
            "page_id": "kis.application.closed_block_outcomes",
            "recommended_action": (
                "reproject_closed_block_outcomes_with_block_horizon_or_lane"
            ),
        }
    ]


def test_repair_queue_page_prioritizes_requested_symbol_and_financial_repairs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        for idx in range(16):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')
                """,
                (
                    f"repair:generic:{idx}",
                    f"evidence_quality:generic:{idx}",
                    f"kis.symbol.{idx:06d}",
                    "cross_check_evidence_quality",
                    "scheduled",
                    '{"symbols":["000000"],"quality_warnings":["partial_evidence"],"repair_action":"generic cross-check"}',
                    f"2026-07-03T01:{idx:02d}:00+00:00",
                ),
            )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:coverage:kis:000660',
                'requested_symbol_coverage:kis:000660',
                'kis.symbol.000660',
                'refresh_requested_symbol_summary',
                'scheduled',
                '{"symbols":["000660"],"quality_warnings":["requested_symbol_summary_missing"],"repair_action":"collect_or_rebuild_requested_symbol_wiki_summary"}',
                '2026-07-03T03:00:00+00:00', '', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:financials:kis:245450',
                'evidence_quality:kis:245450',
                'kis.symbol.245450',
                'refresh_symbol_financials',
                'scheduled',
                '{"symbols":["245450"],"quality_warnings":["financials_missing"],"repair_action":"refresh_symbol_financials_and_rewrite_page_evidence"}',
                '2026-07-03T03:01:00+00:00', '', ''
            )
            """
        )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.repair_queue")
    data_gaps = page["content"].split("## Data Gaps", 1)[1]
    requested_idx = data_gaps.find("refresh_requested_symbol_summary")
    financials_idx = data_gaps.find("refresh_symbol_financials")
    generic_idx = data_gaps.find("cross_check_evidence_quality")

    assert requested_idx >= 0
    assert financials_idx >= 0
    assert generic_idx >= 0
    assert requested_idx < generic_idx
    assert financials_idx < generic_idx


def test_repair_queue_source_refs_prioritize_quality_warning_effectiveness_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        for idx in range(90):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')
                """,
                (
                    f"repair:generic:{idx}",
                    f"evidence_quality:generic:{idx}",
                    f"kis.symbol.{idx:06d}",
                    "cross_check_evidence_quality",
                    "scheduled",
                    '{"symbols":["005930"],"quality_warnings":["financials_missing"],"repair_action":"generic cross-check"}',
                    f"2026-07-03T02:{idx % 60:02d}:00+00:00",
                ),
            )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:quality-warning:financials',
                'quality_warning_effectiveness:financials',
                'quality_warning.financials_missing',
                'repair_quality_warning_effectiveness',
                'scheduled',
                '{"decision_scope":"kis","quality_warnings":["financials_missing"],"sample_count":24,"win_rate":0.3333,"expectancy":-2.0013,"repair_action":"repair or downgrade evidence carrying financials_missing","reasons":["samples:24","expectancy:-2.0013"]}',
                '2026-07-03T01:00:00+00:00', '', ''
            )
            """
        )

    service.rebuild(scope="kis", force=True)
    sources = service.page_sources("kis.research.repair_queue")

    quality_warning_source = next(
        (
            row
            for row in sources["source_refs"]
            if row["source_id"] == "repair:quality-warning:financials"
        ),
        None,
    )
    assert quality_warning_source is not None
    assert quality_warning_source["action_type"] == (
        "repair_quality_warning_effectiveness"
    )
    assert quality_warning_source["symbols"] == []


def test_repair_queue_source_refs_prioritize_usage_guidance_effectiveness_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        for idx in range(90):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')
                """,
                (
                    f"repair:generic:{idx}",
                    f"evidence_quality:generic:{idx}",
                    f"kis.symbol.{idx:06d}",
                    "cross_check_evidence_quality",
                    "scheduled",
                    '{"symbols":["005930"],"quality_warnings":["financials_missing"],"repair_action":"generic cross-check"}',
                    f"2026-07-03T00:{idx % 60:02d}:00+00:00",
                ),
            )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:usage-guidance:risk-posture',
                'usage_guidance_effectiveness:risk-posture',
                'usage_guidance.risk_posture.repair_cross_check',
                'repair_usage_guidance_contract',
                'scheduled',
                '{"decision_scope":"kis","quality_warnings":["usage_guidance_degraded"],"sample_count":18,"win_rate":0.2222,"expectancy":-1.7012,"repair_action":"repair degraded wiki usage guidance before reusing this page usage pattern","reasons":["usage_guidance:risk_posture:repair_cross_check","expectancy:-1.7012"]}',
                '2026-07-03T02:00:00+00:00', '', ''
            )
            """
        )

    service.rebuild(scope="kis", force=True)
    sources = service.page_sources("kis.research.repair_queue")

    usage_guidance_source = next(
        (
            row
            for row in sources["source_refs"]
            if row["source_id"] == "repair:usage-guidance:risk-posture"
        ),
        None,
    )
    assert usage_guidance_source is not None
    assert usage_guidance_source["action_type"] == "repair_usage_guidance_contract"
    assert usage_guidance_source["symbols"] == []
    assert usage_guidance_source["quality_warnings"] == [
        "usage_guidance_degraded"
    ]


def test_context_pack_prioritizes_requested_symbol_page_beyond_broad_limit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_page_limit=4)
    service.initialize()
    for index in range(230):
        service.write_page(
            scope="kis",
            page_type="symbol",
            key=f"8{index:05d}",
            title=f"고신뢰{index}",
            symbols=[f"8{index:05d}"],
            content_sections={
                "Current Stance": "high confidence page",
                "Durable Facts": "- high",
                "Evidence Links": "- fixture",
                "Trading History": "- none",
                "Lessons": "- none",
                "Contradictions": "- none",
                "Open Questions": "- none",
                "Next Context Pack Summary": f"고신뢰{index} summary.",
            },
            source_refs=[{"source_type": "fixture", "source_id": str(index)}],
            confidence=0.95,
            freshness="fresh",
        )
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="002290",
        title="삼일기업공사",
        symbols=["002290"],
        content_sections={
            "Current Stance": "daily discovery only",
            "Durable Facts": "- requested",
            "Evidence Links": "- daily_discovery:2026-07-01",
            "Trading History": "- none",
            "Lessons": "- requested symbol must not be lost",
            "Contradictions": "- none",
            "Open Questions": "- none",
            "Next Context Pack Summary": "삼일기업공사 discovery summary.",
        },
        source_refs=[{"source_type": "daily_discovery", "source_id": "2026-07-01"}],
        confidence=0.56,
        freshness="fresh",
    )

    payload = service.context_pack(
        target_scope="kis",
        symbols=["002290"],
        page_types=["symbol"],
        max_chars=3000,
    )

    assert payload["status"] == "ok"
    assert payload["pages"][0]["page_id"] == "kis.symbol.002290"
    assert "삼일기업공사 discovery summary" in payload["content"]


def test_context_pack_surfaces_open_repair_queue_pressure_on_page_quality(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_page_limit=2)
    service.initialize()
    page = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "fresh source-backed summary",
            "Durable Facts": "- semiconductor leader",
            "Evidence Links": "- symbol_fundamentals:005930:2026-07-03",
            "Trading History": "- none",
            "Lessons": "- wait for value-cycle confirmation",
            "Contradictions": "- none",
            "Open Questions": "- are financials refreshed?",
            "Next Context Pack Summary": "삼성전자 selector summary.",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "strong",
            }
        ],
        confidence=0.85,
        freshness="fresh",
    )
    page_id = str(page["page_id"])
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

    payload = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        max_chars=3000,
    )

    assert payload["status"] == "ok"
    selected = payload["pages"][0]
    assert selected["page_id"] == page_id
    assert selected["evidence_quality"]["status_counts"] == {
        "strong": 1,
        "partial": 1,
    }
    assert selected["evidence_quality"]["warning_counts"] == {
        "application_repair_queue_pressure": 1,
        "open_repair_queue": 1,
    }
    assert selected["evidence_quality"]["repair_queue"]["open_count"] == 1
    assert selected["evidence_quality"]["repair_queue"]["actions"] == [
        {
            "action_type": "repair_application_repair_queue_pressure",
            "status": "scheduled",
            "quality_warnings": ["application_repair_queue_pressure"],
        }
    ]
    assert payload["evidence_quality"]["warning_counts"]["open_repair_queue"] == 1


def test_context_pack_prefers_current_page_over_higher_confidence_stale_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_page_limit=1)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="111111",
        title="높은 신뢰 낡은 기억",
        symbols=["111111"],
        content_sections={
            "Current Stance": "old stale page",
            "Durable Facts": "old facts",
            "Evidence Links": "- fixture:stale",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "stale high confidence summary",
        },
        source_refs=[{"source_type": "fixture", "source_id": "stale"}],
        confidence=0.99,
        freshness="stale",
    )
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="222222",
        title="현재성 있는 기억",
        symbols=["222222"],
        content_sections={
            "Current Stance": "current page",
            "Durable Facts": "current facts",
            "Evidence Links": "- fixture:current",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "current lower confidence summary",
        },
        source_refs=[{"source_type": "fixture", "source_id": "current"}],
        confidence=0.55,
        freshness="current",
    )

    pack = service.context_pack(
        target_scope="kis",
        page_types=["symbol"],
        max_chars=1200,
    )

    assert [page["page_id"] for page in pack["pages"]] == ["kis.symbol.222222"]
    assert "current lower confidence summary" in pack["content"]
    assert "stale high confidence summary" not in pack["content"]


def test_context_pack_sql_candidate_window_keeps_recent_current_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_page_limit=1)
    service.initialize()
    old_page_ids: list[str] = []
    for index in range(205):
        result = service.write_page(
            scope="kis",
            page_type="symbol",
            key=f"7{index:05d}",
            title=f"오래된 current 고신뢰 {index}",
            symbols=[f"7{index:05d}"],
            content_sections={
                "Current Stance": "old current label",
                "Durable Facts": "old facts",
                "Evidence Links": "- fixture:old-current",
                "Trading History": "history",
                "Lessons": "lesson",
                "Contradictions": "none",
                "Open Questions": "question",
                "Next Context Pack Summary": f"old current high confidence {index}",
            },
            source_refs=[{"source_type": "fixture", "source_id": f"old-{index}"}],
            confidence=0.99,
            freshness="current",
        )
        old_page_ids.append(str(result["page_id"]))
    recent = service.write_page(
        scope="kis",
        page_type="symbol",
        key="222222",
        title="최근 current 저신뢰",
        symbols=["222222"],
        content_sections={
            "Current Stance": "recent current page",
            "Durable Facts": "recent facts",
            "Evidence Links": "- fixture:recent-current",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "recent current lower confidence summary",
        },
        source_refs=[{"source_type": "fixture", "source_id": "recent"}],
        confidence=0.55,
        freshness="current",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        placeholders = ",".join(["?"] * len(old_page_ids))
        conn.execute(
            f"""
            UPDATE wiki_pages
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE page_id IN ({placeholders})
            """,
            old_page_ids,
        )

    pack = service.context_pack(
        target_scope="kis",
        page_types=["symbol"],
        max_chars=1200,
    )

    assert [page["page_id"] for page in pack["pages"]] == [recent["page_id"]]
    assert "recent current lower confidence summary" in pack["content"]


def test_context_pack_prefers_fresh_research_over_stale_direct_symbol_page(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_page_limit=1)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 오래된 symbol memory",
        symbols=["005930"],
        content_sections={
            "Current Stance": "stale direct symbol page",
            "Durable Facts": "old facts",
            "Evidence Links": "- fixture:stale-symbol",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "stale direct symbol summary",
        },
        source_refs=[{"source_type": "fixture", "source_id": "stale-symbol"}],
        confidence=0.99,
        freshness="stale",
    )
    fresh_research = service.write_page(
        scope="kis",
        page_type="research",
        key="005930_latest_research",
        title="삼성전자 최신 research memory",
        symbols=["005930"],
        content_sections={
            "Current Stance": "fresh research page",
            "Durable Facts": "fresh facts",
            "Evidence Links": "- naver_reports:latest",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "fresh research summary",
        },
        source_refs=[{"source_type": "naver_reports", "source_id": "latest"}],
        confidence=0.62,
        freshness="fresh",
    )

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        max_chars=1200,
    )

    assert [page["page_id"] for page in pack["pages"]] == [fresh_research["page_id"]]
    assert "fresh research summary" in pack["content"]
    assert "stale direct symbol summary" not in pack["content"]


def test_context_pack_marks_age_stale_current_page_when_no_fresh_alternative(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_page_limit=1)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 오래된 current memory",
        symbols=["005930"],
        content_sections={
            "Current Stance": "age-stale current page",
            "Durable Facts": "old facts",
            "Evidence Links": "- fixture:age-stale",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "age stale current summary",
        },
        source_refs=[{"source_type": "fixture", "source_id": "age-stale"}],
        confidence=0.7,
        freshness="current",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            UPDATE wiki_pages
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE page_id = 'kis.symbol.005930'
            """
        )

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        max_chars=1200,
    )

    assert [page["page_id"] for page in pack["pages"]] == ["kis.symbol.005930"]
    assert pack["pages"][0]["freshness"] == "current"
    assert pack["pages"][0]["freshness_status"] == "stale"
    assert pack["pages"][0]["freshness_warnings"] == ["updated_at_stale_gt_14d"]
    assert "freshness_status=stale" in pack["content"]
    assert "updated_at_stale_gt_14d" in pack["content"]


def test_context_pack_exposes_aggregate_freshness_summary(tmp_path: Path) -> None:
    service = _service(tmp_path, context_page_limit=3)
    service.initialize()
    for symbol, freshness, summary in (
        ("005930", "fresh", "fresh page summary"),
        ("000660", "current", "age stale page summary"),
        ("035420", "unknown", "unknown freshness page summary"),
    ):
        service.write_page(
            scope="kis",
            page_type="symbol",
            key=symbol,
            title=f"{symbol} memory",
            symbols=[symbol],
            content_sections={
                "Current Stance": summary,
                "Durable Facts": "facts",
                "Evidence Links": f"- fixture:{symbol}",
                "Trading History": "history",
                "Lessons": "lesson",
                "Contradictions": "none",
                "Open Questions": "question",
                "Next Context Pack Summary": summary,
            },
            source_refs=[{"source_type": "fixture", "source_id": symbol}],
            confidence=0.7,
            freshness=freshness,
        )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            UPDATE wiki_pages
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE page_id = 'kis.symbol.000660'
            """
        )

    pack = service.context_pack(
        target_scope="kis",
        page_types=["symbol"],
        max_chars=2400,
    )

    assert pack["freshness_summary"] == {
        "page_count": 3,
        "status_counts": {"fresh": 1, "stale": 1, "unknown": 1},
        "warning_counts": {
            "freshness_unknown": 1,
            "updated_at_stale_gt_14d": 1,
        },
        "stale_page_ids": ["kis.symbol.000660"],
        "unknown_page_ids": ["kis.symbol.035420"],
    }


def test_status_feeds_ops_payload_with_phase2_health_fields(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "관찰.",
            "Durable Facts": "- 사실.",
            "Evidence Links": "- source.",
            "Trading History": "- none.",
            "Lessons": "- lesson.",
            "Contradictions": "- none.",
            "Open Questions": "- question.",
            "Next Context Pack Summary": "summary.",
        },
        source_refs=[{"source_type": "manual", "source_id": "seed"}],
        confidence=0.9,
        freshness="stale",
    )
    service.record_selection_run(
        run_id="selection:test",
        target_scope="kis",
        request={},
        selected_pages=[],
        rejected_pages=[],
        char_count=92000,
        max_chars=100000,
        status="ok",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_lint_findings (
                finding_id, page_id, severity, finding_type, message,
                evidence_json, status, created_at, resolved_at
            ) VALUES (
                'finding:test', 'kis.symbol.005930', 'warn', 'stale_page',
                'stale', '{}', 'open', '2026-06-28T01:00:00+00:00', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:test', 'finding:test', 'kis.symbol.005930',
                'rebuild_page', 'scheduled', '{}',
                '2026-06-28T01:05:00+00:00',
                '2026-06-28T01:06:00+00:00', ''
            )
            """
            )

    service.initialize()
    status = service.project_status_snapshot()
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status=status,
        runner={"direct_alive": True},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["wiki_open_lint_count"] == 1
    assert payload["wiki_stale_page_count"] == 1
    assert payload["wiki_last_selection_at"]
    assert payload["wiki_last_repair_at"] == "2026-06-28T01:06:00+00:00"
    assert payload["wiki_prompt_pressure"] == {
        "char_count": 92000,
        "max_chars": 100000,
        "ratio": 0.92,
    }
    assert payload["warnings"] == [
        "jue_wiki_stale_pages_high",
        "jue_wiki_prompt_pressure_high",
        "jue_wiki_repair_queue_overdue",
        "jue_wiki_repair_queue_stalled",
    ]
    assert "jue_wiki_repair_queue_open" in payload["advisories"]
    assert "jue_wiki_shadow_knowledge_degraded" in payload["advisories"]


def test_status_counts_age_stale_current_pages_as_stale(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 current but old",
        symbols=["005930"],
        content_sections={
            "Current Stance": "current label alone is not enough.",
            "Durable Facts": "facts",
            "Evidence Links": "- manual:seed",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "manual", "source_id": "seed"}],
        confidence=0.8,
        freshness="current",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            UPDATE wiki_pages
            SET updated_at = '2026-06-01T00:00:00+00:00'
            WHERE page_id = 'kis.symbol.005930'
            """
        )

    status = service.project_status_snapshot()

    assert status["stale_page_count"] == 1


def test_status_exposes_repair_queue_counts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:kis-open', 'evidence_quality:kis-open',
                'kis.symbol.245450', 'refresh_symbol_financials', 'scheduled',
                '{"symbols":["245450"],"quality_warnings":["financials_missing"]}',
                '2026-07-03T01:00:00+00:00', '', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:binance-open', 'evidence_quality:binance-open',
                'binance.symbol.BTCUSDT', 'cross_check_evidence_quality',
                'unresolved', '{"symbols":["BTCUSDT"]}',
                '2026-07-03T01:01:00+00:00', '', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:kis-done', 'evidence_quality:kis-done',
                'kis.symbol.033100', 'refresh_symbol_fundamentals', 'resolved',
                '{"symbols":["033100"]}', '2026-07-03T00:00:00+00:00',
                '2026-07-03T02:00:00+00:00', ''
            )
            """
        )

    service.initialize()
    status = service.project_status_snapshot()

    queue = status["repair_queue"]
    legacy_queue = {
        key: queue[key]
        for key in (
            "open_count",
            "resolved_count",
            "by_scope",
            "open_by_action_type",
            "open_by_warning",
            "open_symbols",
            "open_action_batches",
        )
    }
    assert legacy_queue == {
        "open_count": 2,
        "resolved_count": 1,
        "by_scope": {
            "binance": {"open_count": 1, "resolved_count": 0},
            "kis": {"open_count": 1, "resolved_count": 1},
        },
        "open_by_action_type": {
            "cross_check_evidence_quality": 1,
            "refresh_symbol_financials": 1,
        },
        "open_by_warning": {
            "financials_missing": 1,
        },
        "open_symbols": ["245450", "BTCUSDT"],
        "open_action_batches": [
            {
                "scope": "binance",
                "action_type": "cross_check_evidence_quality",
                "count": 1,
                "symbols": ["BTCUSDT"],
                "warnings": [],
                "recommended_actions": [],
            },
            {
                "scope": "kis",
                "action_type": "refresh_symbol_financials",
                "count": 1,
                "symbols": ["245450"],
                "warnings": ["financials_missing"],
                "recommended_actions": [],
            },
        ],
    }
    assert queue["oldest_open_at"] == "2026-07-03T01:00:00+00:00"
    assert queue["last_resolved_at"] == "2026-07-03T02:00:00+00:00"
    assert queue["opened_in_window"] == 0
    assert queue["resolved_in_window"] == 0
    assert queue["by_lane"]["evidence"]["repair_health"]["status"] == "warning"
    assert queue["by_lane"]["evidence"]["repair_health"]["warning_signals"] == [
        "jue_wiki_repair_queue_overdue",
        "jue_wiki_repair_queue_stalled",
    ]
    assert queue["repair_health"]["status"] == "idle"
    assert queue["repair_health"]["warning_signals"] == []
    assert queue["repair_health"]["advisory_signals"] == []
    assert status["wiki_repair_queue_open_count"] == 2


def test_status_exposes_progressing_repair_queue_health(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    now = datetime.now(timezone.utc)
    old_opened_at = (now - timedelta(days=2)).isoformat()
    recently_created_at = (now - timedelta(minutes=10)).isoformat()
    recently_resolved_at = (now - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (?, 'finding:open', 'kis.symbol.005930',
                'refresh_financials', 'scheduled', '{}', ?, '', '')
            """,
            ("repair:open", old_opened_at),
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (?, 'finding:done', 'kis.symbol.000660',
                'refresh_financials', 'resolved', '{}', ?, ?, '')
            """,
            ("repair:done", recently_created_at, recently_resolved_at),
        )

    queue = service.project_status_snapshot()["repair_queue"]

    assert queue["oldest_open_at"] == old_opened_at
    assert queue["last_resolved_at"] == recently_resolved_at
    assert queue["opened_in_window"] == 1
    assert queue["resolved_in_window"] == 1
    assert queue["repair_health"]["status"] == "progressing"
    assert queue["repair_health"]["warning_signals"] == []
    assert queue["repair_health"]["advisory_signals"] == [
        "jue_wiki_repair_queue_open"
    ]


def test_record_repair_action_reuses_equivalent_open_work(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.record_repair_action(
        finding_id="financials:005930",
        page_id="kis.symbol.005930",
        action_type="refresh_financials",
        status="scheduled",
        details={"decision_scope": "kis", "symbols": ["005930"]},
    )
    second = service.record_repair_action(
        finding_id="financials:005930",
        page_id="kis.symbol.005930",
        action_type="refresh_financials",
        status="scheduled",
        details={"decision_scope": "kis", "symbols": ["005930"]},
    )

    assert second["action_id"] == first["action_id"]
    assert second["details"]["repair_identity"]
    assert service.project_status_snapshot()["repair_queue"]["open_count"] == 1


def test_repair_queue_health_uses_integrity_lane_only(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with service._connect() as conn:
        for index in range(30):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type,
                    repair_lane, repair_lane_registered, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, 'book_depth_gap', 'strategy', 1,
                          'scheduled', '{}', ?, '', '')
                """,
                (f"strategy-{index}", f"strategy-finding-{index}", "binance.ops.queue", now),
            )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type,
                repair_lane, repair_lane_registered, status,
                details_json, created_at, finished_at, error_message
            ) VALUES ('integrity-1', 'integrity-finding-1', 'core.ops.queue',
                      'repair_research_source_schema', 'integrity', 1,
                      'scheduled', '{}', ?, '', '')
            """,
            (now,),
        )

    queue = service.project_status_snapshot()["repair_queue"]

    assert queue["open_count"] == 31
    assert queue["by_lane"]["strategy"]["open_count"] == 30
    assert queue["by_lane"]["integrity"]["open_count"] == 1
    assert queue["repair_health_inputs"]["open_count"] == 1


def test_resolving_one_identity_resolves_all_equivalent_open_rows(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    details = {
        "decision_scope": "kis",
        "symbols": ["005930"],
        "repair_identity": "kis:financials:005930",
    }
    with service._connect() as conn:
        for index in range(2):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, '', '')
                """,
                (
                    f"legacy-{index}",
                    "financials:005930",
                    "kis.symbol.005930",
                    "refresh_financials",
                    json.dumps(details),
                    f"2026-07-10T00:00:0{index}+00:00",
                ),
            )

    result = service.resolve_repair_identity(
        repair_identity="kis:financials:005930",
        resolved_by="financials_refreshed",
    )

    assert result["resolved_count"] == 2
    assert all(row["status"] == "resolved" for row in result["rows"])
    assert all(
        row["details"]["resolved_by"] == "financials_refreshed"
        for row in result["rows"]
    )


def test_duplicate_open_repairs_are_preserved_as_resolved_audit_rows(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    details = {"decision_scope": "kis", "symbols": ["005930"]}
    with service._connect() as conn:
        for index in range(2):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, 'financials:005930', 'kis.symbol.005930',
                    'refresh_financials', 'scheduled', ?, ?, '', '')
                """,
                (
                    f"duplicate-{index}",
                    json.dumps(details),
                    f"2026-07-10T00:00:0{index}+00:00",
                ),
            )

    result = service.resolve_duplicate_open_repair_actions()

    assert result["resolved_count"] == 1
    assert service.project_status_snapshot()["repair_queue"]["open_count"] == 1
    with service._connect() as conn:
        rows = conn.execute(
            """
            SELECT status, details_json
            FROM wiki_repair_actions
            ORDER BY created_at, action_id
            """
        ).fetchall()
    assert len(rows) == 2
    assert [row["status"] for row in rows] == ["scheduled", "resolved"]
    assert json.loads(rows[1]["details_json"])["resolved_by"] == (
        "duplicate_open_repair_action"
    )


def test_repair_once_compacts_stable_identity_duplicates_before_work(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    details = {"decision_scope": "kis", "symbols": ["005930"]}
    with service._connect() as conn:
        for index in range(2):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, 'financials:005930', 'kis.symbol.005930',
                    'refresh_financials', 'scheduled', ?, ?, '', '')
                """,
                (
                    f"cycle-duplicate-{index}",
                    json.dumps(details),
                    f"2026-07-10T00:00:0{index}+00:00",
                ),
            )

    result = service.repair_once(scope="kis")

    assert result["stable_identity_duplicates"]["resolved_count"] == 1


def test_clean_target_resolution_is_identity_wide_in_one_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    details = {
        "decision_scope": "kis",
        "symbols": ["005930"],
        "repair_identity": "kis:financials:005930",
        "quality_warnings": ["financials_missing"],
        "impacted_page_ids": ["kis.symbol.005930"],
    }
    with service._connect() as conn:
        for index in range(2):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, 'financials:005930', 'kis.symbol.005930',
                    'refresh_financials', 'scheduled', ?, ?, '', '')
                """,
                (
                    f"target-duplicate-{index}",
                    json.dumps(details),
                    f"2026-07-10T00:00:0{index}+00:00",
                ),
            )
        checks = iter([True, False])
        monkeypatch.setattr(
            service,
            "_repair_action_targets_are_clean",
            lambda *_args, **_kwargs: next(checks),
        )
        service._resolve_repair_actions_for_clean_targets(
            conn,
            "2026-07-10T01:00:00+00:00",
        )
        statuses = conn.execute(
            """
            SELECT status
            FROM wiki_repair_actions
            ORDER BY created_at, action_id
            """
        ).fetchall()

    assert [row["status"] for row in statuses] == ["resolved", "resolved"]


def test_manager_evidence_recovery_is_identity_wide(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    details = {
        "decision_scope": "kis",
        "symbols": ["005930"],
        "repair_identity": "kis:manager-evidence:005930",
        "requires_manager_confirmation": True,
        "manager_observed_at": "2026-07-10T00:00:00+00:00",
        "quality_warnings": ["financials_missing"],
    }
    with service._connect() as conn:
        for index in range(2):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, 'manager:005930', 'kis.symbol.005930',
                    'refresh_manager_evidence', 'scheduled', ?, ?, '', '')
                """,
                (
                    f"manager-duplicate-{index}",
                    json.dumps(details),
                    f"2026-07-10T00:00:0{index}+00:00",
                ),
            )
    checks = iter([True, False])
    monkeypatch.setattr(
        service,
        "_manager_observation_evidence_quality_is_clean",
        lambda *_args, **_kwargs: next(checks),
    )

    resolved_count = service._resolve_manager_observation_repair_actions(
        scope="kis",
        observations_by_symbol={
            "005930": [
                {
                    "observed_at": "2026-07-10T01:00:00+00:00",
                    "manager_run_id": "manager:new",
                    "wiki_evidence_quality_summary": "strong",
                    "wiki_evidence_quality_status_counts": {"strong": 4},
                }
            ]
        },
    )

    with service._connect() as conn:
        statuses = conn.execute(
            """
            SELECT status
            FROM wiki_repair_actions
            ORDER BY created_at, action_id
            """
        ).fetchall()
    assert resolved_count == 2
    assert [row["status"] for row in statuses] == ["resolved", "resolved"]


def test_status_repair_queue_open_symbols_include_impacted_and_target_symbols(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:impacted', 'coverage:kis-impacted',
                'kis.symbol.000660', 'refresh_requested_symbol_summary',
                'scheduled',
                '{"impacted_symbols":["000660"],"repair_targets":[{"symbol":"402340"}]}',
                '2026-07-03T01:00:00+00:00', '', ''
            )
            """
        )

    status = service.project_status_snapshot()

    assert status["repair_queue"]["open_symbols"] == ["000660", "402340"]


def test_status_repair_queue_batches_use_decision_scope_for_meta_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:quality-warning', 'quality_warning_effectiveness:financials',
                'quality_warning.financials_missing',
                'repair_quality_warning_effectiveness', 'scheduled',
                '{"decision_scope":"kis","impacted_symbols":["245450"],"quality_warnings":["financials_missing"],"repair_targets":[{"symbol":"245450","recommended_action":"refresh_symbol_financials_and_rewrite_page_evidence"}]}',
                '2026-07-03T01:00:00+00:00', '', ''
            )
            """
        )

    status = service.project_status_snapshot()

    assert status["repair_queue"]["by_scope"] == {
        "kis": {"open_count": 1, "resolved_count": 0}
    }
    assert status["repair_queue"]["open_action_batches"] == [
        {
            "scope": "kis",
            "action_type": "repair_quality_warning_effectiveness",
            "count": 1,
            "symbols": ["245450"],
            "warnings": ["financials_missing"],
            "recommended_actions": [
                "refresh_symbol_financials_and_rewrite_page_evidence"
            ],
        }
    ]


def test_repair_once_skips_and_resolves_repair_queue_evidence_shadow_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["005930"],
        content_sections={
            "Current Stance": "repair queue meta page",
            "Durable Facts": "- open_action_count=1",
            "Evidence Links": "- wiki_repair_queue:repair:source",
            "Trading History": "- meta only",
            "Lessons": "- do not recurse",
            "Contradictions": "- none",
            "Open Questions": "- none",
            "Next Context Pack Summary": "meta queue",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:source",
                "source_scope": "kis",
                "quality_status": "weak",
                "quality_warnings": ["financials_missing"],
                "repair_action": "repair source evidence",
            }
        ],
        confidence=0.7,
        freshness="fresh",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:queue-shadow',
                'evidence_quality:queue-shadow',
                'kis.research.repair_queue',
                'cross_check_evidence_quality',
                'scheduled',
                '{"finding_type":"evidence_quality","scope":"kis","source_type":"wiki_repair_queue","source_id":"repair:source","quality_warnings":["financials_missing"],"repair_action":"stale meta repair"}',
                '2026-07-03T03:00:00+00:00', '', ''
            )
            """
        )

    result = service.repair_once(scope="kis")
    status = service.project_status_snapshot()

    assert result["evidence_quality_actions"] == []
    assert result["repair_queue_shadow_resolved_actions"][0]["action_id"] == (
        "repair:queue-shadow"
    )
    assert status["repair_queue"]["open_by_warning"].get("financials_missing", 0) == 0


def test_repair_once_resolves_duplicate_open_symbol_repair_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    duplicate_details = {
        "source_type": "symbol_fundamentals",
        "source_id": "178920:2026-07-03",
        "symbols": ["178920"],
        "quality_warnings": [
            "financial_rows_rejected_empty",
            "financial_metrics_sparse",
        ],
        "repair_action": "refresh_symbol_fundamentals_and_rewrite_page_evidence",
    }
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:canonical', 'evidence_quality:older',
                'kis.symbol.178920', 'refresh_symbol_fundamentals',
                'scheduled', ?, '2026-07-05T01:00:00+00:00', '', ''
            )
            """,
            (json.dumps(duplicate_details, ensure_ascii=False, sort_keys=True),),
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:duplicate', 'evidence_quality:newer',
                'kis.symbol.178920', 'refresh_symbol_fundamentals',
                'scheduled', ?, '2026-07-05T01:01:00+00:00', '', ''
            )
            """,
            (
                json.dumps(
                    {**duplicate_details, "source_id": "178920:2026-07-02"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )

    result = service.repair_once(scope="kis")

    resolved = result["duplicate_repair_actions_resolved"]
    assert [row["action_id"] for row in resolved] == ["repair:duplicate"]
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT action_id, status, finished_at, details_json
            FROM wiki_repair_actions
            ORDER BY action_id
            """
        ).fetchall()
    assert rows[0][0:3] == ("repair:canonical", "scheduled", "")
    assert rows[1][0:2] == ("repair:duplicate", "resolved")
    assert rows[1][2]
    resolved_details = json.loads(rows[1][3])
    assert resolved_details["resolved_by"] == "duplicate_repair_action_compacted"
    assert resolved_details["canonical_action_id"] == "repair:canonical"


def test_repair_once_quality_warning_effectiveness_excludes_repair_queue_targets(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="research",
        key="repair_queue",
        title="KIS Repair Queue",
        symbols=["005930"],
        content_sections={
            "Current Stance": "repair queue meta page",
            "Durable Facts": "- open_action_count=1",
            "Evidence Links": "- wiki_repair_queue:repair:source",
            "Trading History": "- meta only",
            "Lessons": "- do not target repair queue for warning repairs",
            "Contradictions": "- none",
            "Open Questions": "- none",
            "Next Context Pack Summary": "meta queue",
        },
        source_refs=[
            {
                "source_type": "wiki_repair_queue",
                "source_id": "repair:source",
                "source_scope": "kis",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.7,
        freshness="fresh",
    )
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "needs financials",
            "Durable Facts": "- symbol",
            "Evidence Links": "- symbol_fundamentals:005930",
            "Trading History": "- none",
            "Lessons": "- none",
            "Contradictions": "- none",
            "Open Questions": "- financials",
            "Next Context Pack Summary": "symbol queue",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930",
                "source_scope": "kis",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.7,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financials_missing",
            "decision_scope": "kis",
            "sample_count": 12,
            "win_rate": 0.25,
            "expectancy": -1.7,
            "status": "degraded",
            "reasons": ["samples:12", "expectancy:-1.7"],
        }
    )

    result = service.repair_once(scope="kis")
    action = result["quality_warning_effectiveness_actions"][0]

    assert action["action_type"] == "repair_quality_warning_effectiveness"
    assert action["details"]["impacted_page_ids"] == ["kis.symbol.005930"]
    assert action["details"]["repair_targets"] == [
        {
            "page_id": "kis.symbol.005930",
            "symbol": "005930",
            "recommended_action": "refresh_symbol_financials_and_rewrite_page_evidence",
        }
    ]


def test_repair_once_resolves_quality_warning_action_without_impacted_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.upsert_page_effectiveness(
        {
            "page_id": "quality_warning.financial_rows_rejected_credit_rating",
            "decision_scope": "kis",
            "sample_count": 10,
            "win_rate": 0.2,
            "expectancy": -2.0,
            "status": "degraded",
            "reasons": ["samples:10", "expectancy:-2.0"],
        }
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:no-target-warning',
                'quality_warning_effectiveness:old',
                'quality_warning.financial_rows_rejected_credit_rating',
                'repair_quality_warning_effectiveness',
                'scheduled',
                '{"finding_type":"quality_warning_effectiveness","quality_warnings":["financial_rows_rejected_credit_rating"],"repair_action":"old no-target warning"}',
                '2026-07-03T03:00:00+00:00', '', ''
            )
            """
        )

    result = service.repair_once(scope="kis")
    status = service.project_status_snapshot()

    assert result["quality_warning_effectiveness_actions"] == []
    assert result["quality_warning_effectiveness_resolved_actions"][0][
        "action_id"
    ] == "repair:no-target-warning"
    assert status["repair_queue"]["open_by_warning"].get(
        "financial_rows_rejected_credit_rating", 0
    ) == 0


def test_page_effectiveness_map_prefers_specific_horizon_over_general_metric(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    page_id = "usage_guidance.risk_posture.supporting_cross_check"
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "",
            "sample_count": 10,
            "win_rate": 0.2,
            "expectancy": -0.03,
            "avg_return_pct": -0.03,
            "median_mae_pct": -0.04,
            "drawdown_pressure": 0.04,
            "helpful_score": -4.0,
            "confidence": 1.0,
            "status": "degraded",
            "reasons": ["general usage degraded"],
        }
    )
    service.upsert_page_effectiveness(
        {
            "page_id": page_id,
            "decision_scope": "kis",
            "venue": "kis",
            "horizon": "mid",
            "sample_count": 3,
            "win_rate": 1.0,
            "expectancy": 0.03,
            "avg_return_pct": 0.03,
            "median_mae_pct": -0.01,
            "drawdown_pressure": 0.01,
            "helpful_score": 3.0,
            "confidence": 1.0,
            "status": "active",
            "reasons": ["mid usage active"],
        }
    )

    result = service.page_effectiveness_map(
        decision_scope="kis",
        horizons=["mid"],
    )

    metric = result[page_id]
    assert metric["status"] == "active"
    assert metric["horizon"] == "mid"
    assert metric["sample_count"] == 3
    assert service._parse_json(
        metric["reasons_json"],
        [],
        field="test",
    ) == ["mid usage active"]


def test_status_exposes_configured_research_source_coverage(
    tmp_path: Path,
) -> None:
    missing_reports_db = tmp_path / "missing_naver_reports.db"
    fundamentals_db = tmp_path / "symbol_fundamentals.db"
    with sqlite3.connect(fundamentals_db) as conn:
        conn.execute(
            """
            CREATE TABLE valuation_snapshots (
                symbol TEXT,
                name TEXT,
                crawled_at TEXT,
                as_of TEXT
            )
            """
        )
    market_pulse_db = tmp_path / "market_pulse.db"
    with sqlite3.connect(market_pulse_db) as conn:
        conn.execute(
            """
            CREATE TABLE market_pulse_snapshots (
                id INTEGER,
                regime TEXT,
                captured_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO market_pulse_snapshots VALUES (1, 'risk_on', ?)",
            ("2026-07-03T09:00:00+09:00",),
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            naver_reports_db_path=missing_reports_db,
            symbol_fundamentals_db_path=fundamentals_db,
            market_pulse_db_path=market_pulse_db,
        )
    )

    status = service.project_status_snapshot()
    coverage = status["research_coverage"]
    kis = coverage["by_scope"]["kis"]

    assert kis["configured_count"] == 3
    assert kis["ok_count"] == 1
    assert kis["warning_count"] == 2
    assert coverage["warning_count"] == 2
    assert coverage["unhealthy_source_ids"] == [
        "kis.naver_reports",
        "kis.symbol_fundamentals",
    ]
    assert kis["sources"]["naver_reports"]["status"] == "missing_db"
    assert kis["sources"]["symbol_fundamentals"]["status"] == "empty"
    assert kis["sources"]["market_pulse"]["status"] == "ok"
    assert kis["sources"]["market_pulse"]["latest_at"] == "2026-07-03T09:00:00+09:00"


def test_status_treats_current_daily_discovery_schema_as_covered(
    tmp_path: Path,
) -> None:
    repo = DailyDiscoveryRepository(str(tmp_path / "jue_daily_discovery.db"))
    repo.save_run(
        {
            "trading_day": "2026-07-03",
            "status": "ok",
            "selected_symbols": ["005930"],
            "results": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "market": "KOSPI",
                    "status": "ok",
                    "analysis": {"stance": "watch", "confidence": 0.66},
                }
            ],
        }
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            daily_discovery_db_path=tmp_path / "jue_daily_discovery.db",
        )
    )

    daily_discovery = service.project_status_snapshot()["research_coverage"][
        "by_scope"
    ]["kis"]["sources"]["daily_discovery"]

    assert daily_discovery["status"] == "ok"
    assert daily_discovery["warning"] is False
    assert daily_discovery["table_status_counts"] == {"ok": 2}


def test_repair_once_resolves_stale_research_coverage_actions(
    tmp_path: Path,
) -> None:
    repo = DailyDiscoveryRepository(str(tmp_path / "jue_daily_discovery.db"))
    repo.save_run(
        {
            "trading_day": "2026-07-03",
            "status": "ok",
            "selected_symbols": ["005930"],
            "results": [{"symbol": "005930", "status": "ok"}],
        }
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            daily_discovery_db_path=tmp_path / "jue_daily_discovery.db",
        )
    )
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:stale-research-coverage',
                'research_coverage:kis:daily_discovery:missing_table',
                'kis.research.coverage',
                'repair_research_source_schema',
                'scheduled',
                '{"finding_type":"research_coverage","scope":"kis","source_id":"daily_discovery","source_status":"missing_table","quality_warnings":["research_coverage_unhealthy"],"repair_action":"stale schema repair"}',
                '2026-07-03T03:00:00+00:00', '', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:stale-research-coverage-shadow',
                'evidence_quality:stale-research-coverage-shadow',
                'kis.research.repair_queue',
                'cross_check_evidence_quality',
                'scheduled',
                '{"finding_type":"evidence_quality","scope":"kis","source_type":"wiki_repair_queue","source_id":"repair:stale-research-coverage","quality_warnings":["research_coverage_unhealthy"],"repair_action":"cross-check stale research coverage warning"}',
                '2026-07-03T03:05:00+00:00', '', ''
            )
            """
        )

    result = service.repair_once(scope="kis")
    status = service.project_status_snapshot()

    assert result["research_coverage_actions"] == []
    resolved_ids = {
        row["action_id"] for row in result["research_coverage_resolved_actions"]
    }
    assert resolved_ids == {
        "repair:stale-research-coverage",
        "repair:stale-research-coverage-shadow",
    }
    assert all(
        row["status"] == "resolved"
        for row in result["research_coverage_resolved_actions"]
    )
    assert status["repair_queue"]["open_by_warning"].get(
        "research_coverage_unhealthy", 0
    ) == 0


def test_repair_once_resolves_stale_research_coverage_shadow_chain(
    tmp_path: Path,
) -> None:
    repo = DailyDiscoveryRepository(str(tmp_path / "jue_daily_discovery.db"))
    repo.save_run(
        {
            "trading_day": "2026-07-03",
            "status": "ok",
            "selected_symbols": ["005930"],
            "results": [{"symbol": "005930", "status": "ok"}],
        }
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            daily_discovery_db_path=tmp_path / "jue_daily_discovery.db",
        )
    )
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:resolved-research-coverage',
                'research_coverage:kis:daily_discovery:missing_table',
                'kis.research.coverage',
                'repair_research_source_schema',
                'resolved',
                '{"finding_type":"research_coverage","scope":"kis","source_id":"daily_discovery","source_status":"missing_table","quality_warnings":["research_coverage_unhealthy"],"resolved_by":"research_coverage_clean"}',
                '2026-07-03T03:00:00+00:00',
                '2026-07-03T03:30:00+00:00',
                ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:shadow-one',
                'evidence_quality:shadow-one',
                'kis.research.repair_queue',
                'cross_check_evidence_quality',
                'scheduled',
                '{"finding_type":"evidence_quality","scope":"kis","source_type":"wiki_repair_queue","source_id":"repair:resolved-research-coverage","quality_warnings":["research_coverage_unhealthy"],"repair_action":"cross-check stale research coverage warning"}',
                '2026-07-03T03:31:00+00:00', '', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:shadow-two',
                'evidence_quality:shadow-two',
                'kis.research.repair_queue',
                'cross_check_evidence_quality',
                'scheduled',
                '{"finding_type":"evidence_quality","scope":"kis","source_type":"wiki_repair_queue","source_id":"repair:shadow-one","quality_warnings":["research_coverage_unhealthy"],"repair_action":"cross-check stale research coverage shadow warning"}',
                '2026-07-03T03:32:00+00:00', '', ''
            )
            """
        )

    result = service.repair_once(scope="kis")
    status = service.project_status_snapshot()

    resolved_ids = {
        row["action_id"] for row in result["research_coverage_resolved_actions"]
    }
    assert {"repair:shadow-one", "repair:shadow-two"} <= resolved_ids
    assert status["repair_queue"]["open_by_warning"].get(
        "research_coverage_unhealthy", 0
    ) == 0


def test_ops_payload_warns_for_configured_unhealthy_wiki_research_sources(
    tmp_path: Path,
) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            naver_reports_db_path=tmp_path / "missing_naver_reports.db",
        )
    )
    status = service.project_status_snapshot()

    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status=status,
        runner={"direct_alive": True},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["research_coverage"]["warning_count"] == 1
    assert "jue_wiki_research_coverage_unhealthy" in payload["warnings"]


def test_repair_once_records_research_coverage_repair_actions(
    tmp_path: Path,
) -> None:
    fundamentals_db = tmp_path / "symbol_fundamentals.db"
    with sqlite3.connect(fundamentals_db) as conn:
        conn.execute(
            """
            CREATE TABLE valuation_snapshots (
                symbol TEXT,
                name TEXT,
                crawled_at TEXT,
                as_of TEXT
            )
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            naver_reports_db_path=tmp_path / "missing_naver_reports.db",
            symbol_fundamentals_db_path=fundamentals_db,
        )
    )
    service.rebuild(scope="kis", force=True)

    result = service.repair_once(scope="kis")
    status = service.project_status_snapshot()

    actions = result["research_coverage_actions"]
    action_types = {row["action_type"] for row in actions}
    assert "restore_research_source_db" in action_types
    assert "populate_research_source_rows" in action_types
    assert status["repair_queue"]["open_by_action_type"][
        "restore_research_source_db"
    ] == 1
    assert status["repair_queue"]["open_by_action_type"][
        "populate_research_source_rows"
    ] == 1
    assert status["repair_queue"]["open_by_warning"][
        "research_coverage_unhealthy"
    ] == 2

    page = service.read_page("kis.research.repair_queue")
    assert "restore_research_source_db" in page["content"]
    assert "populate_research_source_rows" in page["content"]
    assert "source=naver_reports" in page["content"]
    assert "source=symbol_fundamentals" in page["content"]


def test_repair_once_records_table_level_research_coverage_warnings(
    tmp_path: Path,
) -> None:
    daily_discovery_db = tmp_path / "jue_daily_discovery.db"
    with sqlite3.connect(daily_discovery_db) as conn:
        conn.execute(
            """
            CREATE TABLE discovery_runs (
                run_id TEXT,
                run_at TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO discovery_runs VALUES ('run-1', ?, ?)",
            (
                "2026-07-03T01:18:28+00:00",
                "2026-07-03T01:18:28+00:00",
            ),
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            daily_discovery_db_path=daily_discovery_db,
        )
    )
    service.rebuild(scope="kis", force=True)

    result = service.repair_once(scope="kis")
    status = service.project_status_snapshot()

    actions = result["research_coverage_actions"]
    daily_discovery = [
        row
        for row in actions
        if row.get("details", {}).get("source_id") == "daily_discovery"
    ]
    assert len(daily_discovery) == 1
    assert daily_discovery[0]["action_type"] == "repair_research_source_schema"
    assert daily_discovery[0]["details"]["source_status"] == "missing_table"
    assert daily_discovery[0]["details"]["source_reported_status"] == "ok"
    assert daily_discovery[0]["details"]["table_issues"] == [
        {"table": "discovery_samples", "status": "missing_table"}
    ]
    assert status["repair_queue"]["open_by_action_type"][
        "repair_research_source_schema"
    ] == 1
    assert status["repair_queue"]["open_by_warning"][
        "research_coverage_unhealthy"
    ] == 1
    assert result["repair_queue_pages"] == {"kis": 1}

    page = service.read_page("kis.research.repair_queue")
    assert "source=daily_discovery" in page["content"]
    assert "source_status=missing_table" in page["content"]


def test_repair_queue_page_symbols_include_impacted_and_target_symbols(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:page-symbols', 'coverage:kis-page-symbols',
                'quality_warning.requested_symbol_summary_missing',
                'refresh_requested_symbol_summary', 'scheduled',
                '{"decision_scope":"kis","impacted_symbols":["000660"],"repair_targets":[{"symbol":"402340"}],"quality_warnings":["requested_symbol_summary_missing"]}',
                '2026-07-03T01:00:00+00:00', '', ''
            )
            """
        )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.research.repair_queue")

    assert 'symbols: ["000660", "402340"]' in page["content"]
    assert "open_symbols=000660,402340" in page["content"]


def test_status_preserves_latest_selection_requested_symbol_coverage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.record_selection_run(
        run_id="selection:coverage",
        target_scope="kis",
        request={"symbols": ["005930", "000660", "178920"]},
        selected_pages=[],
        rejected_pages=[],
        char_count=12_000,
        max_chars=24_000,
        status="ok",
        budget_report={
            "status": "ok",
            "requested_symbol_count": 3,
            "requested_symbol_available_summary_count": 1,
            "requested_symbol_available_summary_symbols": ["005930"],
            "requested_symbol_summary_coverage_status": "partial",
            "requested_symbol_missing_summary_count": 1,
            "requested_symbol_missing_summary_symbols": ["000660"],
            "requested_symbol_prompt_omitted_count": 1,
            "requested_symbol_prompt_omitted_symbols": ["178920"],
        },
    )

    status = service.project_status_snapshot()

    budget_report = status["latest_selection"]["budget_report"]
    assert budget_report["requested_symbol_count"] == 3
    assert budget_report["requested_symbol_missing_summary_symbols"] == ["000660"]
    assert budget_report["requested_symbol_prompt_omitted_symbols"] == ["178920"]


def test_selection_repair_infers_missing_requested_symbols_without_status(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.record_selection_run(
        run_id="selection:coverage-inferred",
        target_scope="kis",
        request={"symbols": ["005930", "000660"]},
        selected_pages=[],
        rejected_pages=[],
        char_count=12_000,
        max_chars=24_000,
        status="ok",
        budget_report={
            "requested_symbol_count": 2,
            "requested_symbol_missing_summary_count": 1,
            "requested_symbol_missing_summary_symbols": ["000660"],
        },
    )

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT action_id, page_id, details_json
            FROM wiki_repair_actions
            WHERE action_type = 'refresh_requested_symbol_summary'
            """
        ).fetchone()

    assert row is not None
    assert row[0] == "repair:coverage:kis:000660"
    assert row[1] == "kis.symbol.000660"
    details = json.loads(row[2])
    assert details["coverage_status"] == "partial"
    assert details["missing_symbol_count"] == 1


def test_status_uses_full_selection_pressure_before_compact_selection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_max_chars=24000)
    service.initialize()
    service.record_selection_run(
        run_id="selection:full",
        target_scope="binance",
        request={"max_chars": 190000},
        selected_pages=[],
        rejected_pages=[],
        char_count=76000,
        max_chars=190000,
        status="ok",
    )
    service.record_selection_run(
        run_id="selection:compact",
        target_scope="binance",
        request={"max_chars": 4000},
        selected_pages=[],
        rejected_pages=[],
        char_count=3314,
        max_chars=4000,
        status="ok",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            "UPDATE wiki_selection_runs SET created_at = ? WHERE run_id = ?",
            ("2026-06-28T01:00:00+00:00", "selection:full"),
        )
        conn.execute(
            "UPDATE wiki_selection_runs SET created_at = ? WHERE run_id = ?",
            ("2026-06-28T01:05:00+00:00", "selection:compact"),
        )

    status = service.project_status_snapshot()
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status=status,
        runner={"direct_alive": True},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert status["latest_selection"]["run_id"] == "selection:compact"
    assert status["latest_full_selection"]["run_id"] == "selection:full"
    assert status["latest_compact_selection"]["run_id"] == "selection:compact"
    assert status["prompt_pressure"] == {
        "char_count": 76000,
        "max_chars": 190000,
    }
    assert status["compact_prompt_pressure"] == {
        "char_count": 3314,
        "max_chars": 4000,
    }
    assert payload["wiki_prompt_pressure"] == {
        "char_count": 76000,
        "max_chars": 190000,
        "ratio": 0.4,
    }
    assert "jue_wiki_prompt_pressure_high" not in payload["warnings"]


def test_write_page_records_frontmatter_sections_and_repository_row(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    result = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "메모리 기반 대기.",
            "Durable Facts": "- 대형 반도체 종목.",
            "Evidence Links": "- source: fixture",
            "Trading History": "- 아직 없음.",
            "Lessons": "- 고점 추격 주의.",
            "Contradictions": "- 없음.",
            "Open Questions": "- 밸류 최신성 확인.",
            "Next Context Pack Summary": (
                "삼성전자 판단에는 밸류와 반도체 업황을 함께 확인."
            ),
        },
        source_refs=[{"source_type": "test", "source_id": "fixture-1"}],
        confidence=0.7,
        freshness="fresh",
    )

    assert result["status"] == "ok"
    page = service.read_page("kis.symbol.005930")
    assert page["status"] == "ok"
    assert "scope: kis" in page["content"]
    assert "## Current Stance" in page["content"]
    assert "## Next Context Pack Summary" in page["content"]
    status = service.project_status_snapshot()
    assert status["page_count"] == 1
    assert status["scopes"]["kis"] == 1


def test_write_page_counts_and_indexes_nested_source_refs(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 compressed source refs",
        symbols=["005930"],
        content_sections={
            "Current Stance": "compressed evidence",
            "Durable Facts": "facts",
            "Evidence Links": "nested refs",
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
        confidence=0.7,
        freshness="fresh",
    )

    page = service.read_page("kis.symbol.005930")
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        source_refs = conn.execute(
            """
            SELECT source_type, source_id
            FROM wiki_source_refs
            WHERE page_id = ?
            ORDER BY source_type, source_id
            """,
            ("kis.symbol.005930",),
        ).fetchall()

    assert "source_count: 3" in page["content"]
    assert [(row[0], row[1]) for row in source_refs] == [
        ("rag_report", "005930:rag"),
        ("symbol_fundamentals", "005930:fundamentals"),
        ("wiki_symbol_summary", "kis.symbol.005930:summary"),
    ]


def test_page_sources_preserves_nested_source_ref_metadata(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 nested source metadata",
        symbols=["005930"],
        content_sections={
            "Current Stance": "compressed evidence",
            "Durable Facts": "facts",
            "Evidence Links": "nested refs",
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
                        "quality_status": "weak",
                        "quality_warnings": ["financials_missing"],
                    }
                ],
            }
        ],
        confidence=0.7,
        freshness="fresh",
    )

    sources = service.page_sources("kis.symbol.005930")
    fundamentals_ref = next(
        row
        for row in sources["source_refs"]
        if row["source_type"] == "symbol_fundamentals"
    )

    assert fundamentals_ref["quality_status"] == "weak"
    assert fundamentals_ref["quality_warnings"] == ["financials_missing"]


def test_write_page_indexes_distinct_source_refs_without_source_ids(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 source refs without ids",
        symbols=["005930"],
        content_sections={
            "Current Stance": "compressed evidence",
            "Durable Facts": "facts",
            "Evidence Links": "nested refs",
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
                        "source_type": "wiki_repair_queue",
                        "symbols": ["005930"],
                        "action_type": "refresh_financials",
                    },
                    {
                        "source_type": "wiki_repair_queue",
                        "symbols": ["000660"],
                        "action_type": "refresh_financials",
                    },
                ],
            }
        ],
        confidence=0.7,
        freshness="fresh",
    )

    sources = service.page_sources("kis.symbol.005930")
    repair_refs = [
        row
        for row in sources["source_refs"]
        if row["source_type"] == "wiki_repair_queue"
    ]

    assert len(repair_refs) == 2
    assert {tuple(row["symbols"]) for row in repair_refs} == {
        ("005930",),
        ("000660",),
    }
    assert all(row["source_id"].startswith("generated:wiki_repair_queue:") for row in repair_refs)


def test_list_pages_raises_on_corrupt_page_json_instead_of_silent_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "watch",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[],
        confidence=0.7,
        freshness="fresh",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            UPDATE wiki_pages
            SET symbols_json = ?
            WHERE page_id = 'kis.symbol.005930'
            """,
            ("[not-json",),
        )

    with pytest.raises(RuntimeError, match="wiki_pages.symbols_json"):
        service.search_pages(scope="kis")


def test_context_pack_prefers_scope_symbol_and_budget_summary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_max_chars=500, context_page_limit=2)
    service.initialize()

    for symbol, title in [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("BTCUSDT", "BTCUSDT"),
    ]:
        scope = "binance" if symbol.endswith("USDT") else "kis"
        service.write_page(
            scope=scope,
            page_type="symbol",
            key=symbol,
            title=title,
            symbols=[symbol],
            content_sections={
                "Current Stance": f"{title} stance",
                "Durable Facts": "facts",
                "Evidence Links": "evidence",
                "Trading History": "history",
                "Lessons": "lesson " * 100,
                "Contradictions": "none",
                "Open Questions": "questions",
                "Next Context Pack Summary": f"{title} compact summary",
            },
            source_refs=[],
            confidence=0.8,
            freshness="fresh",
        )

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        max_chars=500,
    )

    assert pack["status"] == "ok"
    assert pack["target_scope"] == "kis"
    assert pack["char_count"] <= 500
    assert [page["page_id"] for page in pack["pages"]] == ["kis.symbol.005930"]
    assert "삼성전자 compact summary" in pack["content"]
    assert "BTCUSDT" not in pack["content"]

    tiny_pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        max_chars=20,
    )
    assert tiny_pack["char_count"] <= 20


def test_context_pack_surfaces_evidence_quality_summary(tmp_path: Path) -> None:
    service = _service(tmp_path, context_max_chars=1200, context_page_limit=2)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "valuation aware page",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "삼성전자 compact summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:2026-07-03",
                "quality_status": "partial",
                "quality_warnings": ["financial_metrics_sparse"],
            }
        ],
        confidence=0.8,
        freshness="fresh",
    )

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        max_chars=1200,
    )

    assert pack["evidence_quality"]["status_counts"] == {"partial": 1}
    assert pack["evidence_quality_summary"].startswith("evidence_quality_total")
    assert pack["pages"][0]["evidence_quality"]["top_warnings"] == [
        {"warning": "financial_metrics_sparse", "count": 1}
    ]
    assert "evidence_quality sources=1, partial=1" in pack["content"]
    assert "financial_metrics_sparse:1" in pack["content"]


def test_context_pack_includes_symbol_repair_queue_batches_with_symbol_page_filter(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_max_chars=1600, context_page_limit=1)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "symbol page",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "삼성전자 compact summary",
        },
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, '', '')
            """,
            (
                "repair:manager_context:kis:refresh_symbol_financials:005930",
                "manager_context_repair:kis:refresh_symbol_financials:005930",
                "kis.symbol.005930",
                "refresh_symbol_financials",
                json.dumps(
                    {
                        "decision_scope": "kis",
                        "symbols": ["005930"],
                        "impacted_symbols": ["005930"],
                        "impacted_page_ids": ["kis.symbol.005930"],
                        "quality_warnings": ["valuation_missing"],
                        "repair_action": "refresh_symbol_financials",
                        "repair_targets": [
                            {
                                "page_id": "kis.symbol.005930",
                                "symbol": "005930",
                                "recommended_action": "refresh_symbol_financials",
                            }
                        ],
                        "requires_manager_confirmation": True,
                    },
                    ensure_ascii=False,
                ),
                "2026-07-03T00:00:00+00:00",
            ),
        )

    pack = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        max_chars=1600,
    )

    assert [page["page_id"] for page in pack["pages"]] == ["kis.symbol.005930"]
    assert pack["repair_queue"]["open_count"] == 1
    assert pack["repair_action_batches"] == [
        {
            "scope": "kis",
            "action_type": "refresh_symbol_financials",
            "count": 1,
            "symbols": ["005930"],
            "warnings": ["valuation_missing"],
            "recommended_actions": ["refresh_symbol_financials"],
        }
    ]
    assert "Jue Wiki Repair Queue" in pack["content"]
    assert "refresh_symbol_financials" in pack["content"]
    assert "valuation_missing" in pack["content"]


def test_truncated_page_preserves_next_context_pack_summary(tmp_path: Path) -> None:
    service = _service(tmp_path, page_max_chars=900, context_max_chars=1200)
    long_evidence = "\n".join(
        f"- oversized evidence line {index} BTCUSDT quant/pattern note"
        for index in range(80)
    )

    service.write_page(
        scope="binance",
        page_type="symbol",
        key="BTCUSDT",
        title="BTCUSDT",
        symbols=["BTCUSDT"],
        content_sections={
            "Current Stance": "Large page should truncate.",
            "Durable Facts": "- scope=binance\n- symbol=BTCUSDT",
            "Evidence Links": long_evidence,
            "Trading History": long_evidence,
            "Lessons": long_evidence,
            "Contradictions": "- none",
            "Open Questions": "- none",
            "Next Context Pack Summary": (
                "BTCUSDT compact summary must survive page truncation."
            ),
        },
        source_refs=[{"source_type": "test", "source_id": "btc"}],
        confidence=0.8,
        freshness="fresh",
    )

    page = service.read_page("binance.symbol.BTCUSDT")
    pack = service.context_pack(
        target_scope="binance",
        symbols=["BTCUSDT"],
        page_types=["symbol"],
        max_chars=1200,
    )

    assert "## Truncation Note" in page["content"]
    assert "## Next Context Pack Summary" in page["content"]
    assert "BTCUSDT compact summary must survive" in pack["content"]
    assert "oversized evidence line 70" not in pack["content"]


def test_rebuild_compiles_kis_symbol_page_from_block_and_reflection_sources(
    tmp_path: Path,
) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                status TEXT,
                qty_initial INTEGER,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                thesis TEXT,
                llm_reason TEXT,
                risk_note TEXT,
                created_at TEXT,
                closed_at TEXT,
                realized_pnl REAL
            );
            INSERT INTO blocks VALUES (
                'blk_005930_1', '005930', '삼성전자', 'closed', 1, 70000,
                73500, 68200, '저평가 눌림목', '반도체 회복 기대',
                '고점 추격 금지', '2026-06-20T00:00:00+00:00',
                '2026-06-21T00:00:00+00:00', 1200
            );
            """
        )
    with sqlite3.connect(memory_db) as conn:
        conn.executescript(
            """
            CREATE TABLE block_reflections (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                summary TEXT,
                lesson TEXT,
                metrics_json TEXT,
                created_at TEXT
            );
            INSERT INTO block_reflections VALUES (
                'blk_005930_1', '005930',
                '목표 근처 익절은 좋았으나 진입 근거가 얕았다.',
                '반도체 대형주는 밸류와 수급을 같이 확인한다.',
                '{"memory_scope":"kis"}',
                '2026-06-21T01:00:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=kis_db,
            binance_blocks_db_path=tmp_path / "missing_binance.db",
            investment_memory_db_path=memory_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)

    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    page = service.read_page("kis.symbol.005930")
    assert page["status"] == "ok"
    assert "삼성전자" in page["content"]
    assert "저평가 눌림목" in page["content"]
    assert "반도체 대형주는 밸류와 수급을 같이 확인한다" in page["content"]
    assert "kis_blocks:blk_005930_1" in page["content"]
    assert "investment_memory:blk_005930_1" in page["content"]


def test_rebuild_compiles_binance_symbol_page_from_spot_and_futures_blocks(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                market TEXT,
                lane TEXT,
                side TEXT,
                status TEXT,
                qty_initial REAL,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                thesis TEXT,
                llm_reason TEXT,
                risk_note TEXT,
                created_at TEXT,
                closed_at TEXT,
                pnl_usdt REAL
            );
            INSERT INTO blocks VALUES (
                'bnb_futures_BTCUSDT_1', 'BTCUSDT', 'futures', 'intraday',
                'short', 'closed', 0.001, 64000, 62000, 65200,
                '펀딩 과열 숏', '레버리지 축소', '숏 편향 점검',
                '2026-06-20T00:00:00+00:00',
                '2026-06-21T00:00:00+00:00', -1.2
            );
            """
        )
    with sqlite3.connect(memory_db) as conn:
        conn.executescript(
            """
            CREATE TABLE block_reflections (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                summary TEXT,
                lesson TEXT,
                metrics_json TEXT,
                created_at TEXT
            );
            INSERT INTO block_reflections VALUES (
                'bnb_futures_BTCUSDT_1', 'BTCUSDT', '숏 편향이 과했다.',
                'BTC는 방향성보다 레짐과 펀딩 확인을 우선한다.',
                '{"memory_scope":"binance"}',
                '2026-06-21T01:00:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=tmp_path / "missing_kis.db",
            binance_blocks_db_path=binance_db,
            investment_memory_db_path=memory_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)

    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    page = service.read_page("binance.symbol.BTCUSDT")
    assert page["status"] == "ok"
    assert "BTCUSDT" in page["content"]
    assert "market=futures" in page["content"]
    assert "lane=intraday" in page["content"]
    assert "side=short" in page["content"]
    assert "pnl_usdt=-1.2" in page["content"]
    assert "펀딩 과열 숏" in page["content"]
    assert "숏 편향이 과했다" in page["content"]
    assert "binance_blocks:bnb_futures_BTCUSDT_1" in page["content"]


def test_rebuild_binance_symbols_uses_crypto_research_without_blocks(
    tmp_path: Path,
) -> None:
    crypto_db = tmp_path / "crypto_market_research.db"
    with sqlite3.connect(crypto_db) as conn:
        conn.executescript(
            """
            CREATE TABLE crypto_symbol_notes (
                symbol TEXT PRIMARY KEY,
                stance TEXT,
                horizon TEXT,
                confidence REAL,
                summary_md TEXT,
                reasons_json TEXT,
                risks_json TEXT,
                triggers_json TEXT,
                updated_at TEXT
            );
            CREATE TABLE crypto_candidates (
                symbol TEXT,
                market TEXT,
                stance TEXT,
                horizon TEXT,
                score REAL,
                confidence REAL,
                reason_md TEXT,
                block_template_json TEXT,
                source_run_id INTEGER,
                updated_at TEXT,
                PRIMARY KEY(symbol, market, stance, horizon)
            );
            INSERT INTO crypto_symbol_notes VALUES (
                'BTCUSDT', 'long_watch', 'intraday', 0.74,
                'BTC는 ETF 수급과 오더북 흡수 이후 돌파 후보입니다.',
                '["거래대금 확장", "스프레드 양호"]',
                '["펀딩 과열"]',
                '["상단 돌파 후 재안착"]',
                '2026-07-01T08:00:00+00:00'
            );
            INSERT INTO crypto_candidates VALUES (
                'BTCUSDT', 'futures', 'long_watch', 'intraday', 82, 0.71,
                '돌파 재안착 대기 블록 후보',
                '{"entry_price": 62000, "target_price": 63800, "stop_price": 61200, "reward_risk": 2.25}',
                7,
                '2026-07-01T08:05:00+00:00'
            );
            """
        )
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.execute(
            "CREATE TABLE blocks (block_id TEXT, symbol TEXT, market TEXT, status TEXT)"
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
            crypto_market_research_db_path=crypto_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)

    page = service.read_page("binance.symbol.BTCUSDT")
    sources = service.page_sources("binance.symbol.BTCUSDT")
    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "Crypto Market Research" in page["content"]
    assert "ETF 수급과 오더북 흡수" in page["content"]
    assert "entry=62000" in page["content"]
    assert any(
        row["source_type"] == "crypto_market_research"
        and row["source_id"] == "BTCUSDT:note"
        for row in sources["source_refs"]
    )
    assert any(
        row["source_type"] == "crypto_candidates"
        and row["source_id"] == "7:BTCUSDT:futures:long_watch:intraday"
        for row in sources["source_refs"]
    )


def test_rebuild_binance_symbols_uses_manager_candidate_observations(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                market TEXT,
                status TEXT
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T17:39:47+00:00",
                "error",
                "manager_task_timeout_after_630s",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "candidates": {
                            "items": [
                                {
                                    "symbol": "BTCUSDT",
                                    "market": "futures",
                                    "lane": "futures:long",
                                    "side": "long",
                                    "horizon": "short",
                                    "score": 82,
                                    "confidence": 0.67,
                                    "entry_style": "wait_for_price",
                                    "entry_trigger_operator": "<=",
                                    "entry_trigger_price": 62000,
                                    "target_price": 63800,
                                    "stop_price": 61200,
                                    "calculated": {
                                        "reward_risk": 2.25,
                                        "pattern_live_crosscheck": {
                                            "status": "no_pattern_prior",
                                            "recommended_entry_mode": "research_only",
                                        },
                                    },
                                    "reason_md": "오더북 확인 후 눌림 대기 후보",
                                }
                            ]
                        },
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "high",
                            "zero_action_streak": 2,
                            "candidate_count": 5,
                            "top_candidates": [{"symbol": "BTCUSDT", "score": 82}],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "hold_decision": {
                            "summary": "패턴 prior가 없어 실행 전환을 보류했다.",
                            "data_gaps": ["fresh order book missing"],
                            "next_triggers": [
                                {
                                    "symbol": "BTCUSDT",
                                    "market": "futures",
                                    "condition": "book_fresh=true and pattern prior 확보",
                                    "price": 62000,
                                    "reason": "가격 구조는 있으나 research_only 상태",
                                }
                            ],
                            "watch_symbols": ["BTCUSDT"],
                        }
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.symbol.BTCUSDT")
    sources = service.page_sources("binance.symbol.BTCUSDT")

    assert result["status"] == "ok"
    assert result["updated_count"] == 2
    assert page["status"] == "ok"
    assert service.read_page("binance.ops.manager_runs")["status"] == "ok"
    assert "Manager Candidate Observations" in page["content"]
    assert "manager_candidates" in page["content"]
    assert "run_status=error" in page["content"]
    assert "manager_task_timeout_after_630s" in page["content"]
    assert "Manager run errors are repair memory" in page["content"]
    assert "hold_decision.next_trigger" in page["content"]
    assert "pressure=action_required/high" in page["content"]
    assert "Action-required pressure is unresolved memory" in page["content"]
    assert "패턴 prior가 없어" in page["content"]
    assert "fresh order book missing" in page["content"]
    assert any(
        row["source_type"] == "binance_manager_runs" and row["source_id"] == "1"
        for row in sources["source_refs"]
    )


def test_rebuild_binance_symbols_records_unresolved_wiki_attention_from_manager_runs(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                market TEXT,
                status TEXT
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "",
                "llm",
                "gpt-5.5",
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "latest_input_summary": {
                            "jue_wiki_attention": {
                                "status": "active",
                                "must_address": ["probe_next"],
                                "resolution_status": "unresolved",
                                "probe_next": {
                                    "component": "spot_underuse_live_probe",
                                    "action_type": "live_probe",
                                    "recommended_resolution": (
                                        "현물 미사용 문제를 다음 Binance 판단에서 "
                                        "실행 가능한 spot 대기블록으로 검증한다."
                                    ),
                                    "impacted_symbols": ["ETHUSDT"],
                                    "repair_targets": ["binance.symbol.ETHUSDT"],
                                },
                            }
                        },
                        "hold_decision": {
                            "summary": "spot lane 재검증 필요",
                            "watch_symbols": ["ETHUSDT"],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.symbol.ETHUSDT")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "resolution=unresolved" in page["content"]
    assert "spot_underuse_live_probe" in page["content"]
    assert "binance.symbol.ETHUSDT" in page["content"]


def test_rebuild_binance_symbols_records_prompt_diagnostics_as_wiki_repair_memory(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                market TEXT,
                status TEXT
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:02:00+00:00",
                "ok",
                "",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "diagnostics": {
                            "version": "binance_manager_diagnostics_v1",
                            "blocker_tags": {
                                "unresolved_jue_wiki_requested_symbol_coverage": 2,
                                "unresolved_jue_wiki_attention_plan": 3,
                            },
                            "jue_wiki_missing_summary_symbols": ["BTCUSDT"],
                            "jue_wiki_attention_must_address": [
                                "crypto_symbol_summary_missing"
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"hold_decision": {"summary": "관망"}}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.symbol.BTCUSDT")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "collect_or_rebuild_requested_symbol_wiki_summary" in page["content"]
    assert "prompt.diagnostics" in page["content"]


def test_rebuild_binance_symbols_records_compact_context_wiki_evidence_quality(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                market TEXT,
                status TEXT
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:05:00+00:00",
                "ok",
                "",
                "llm",
                "gpt-5.5",
                json.dumps(
                    {
                        "proactive_decision_pressure": {
                            "status": "action_required",
                            "pressure_level": "medium",
                            "top_candidates": [
                                {
                                    "symbol": "NEARUSDT",
                                    "market": "futures",
                                    "side": "long",
                                    "score": 76,
                                    "signals": ["wiki_context"],
                                }
                            ],
                        },
                        "compact_manager_context": {
                            "jue_wiki_selection_observation": {
                                "selection_run_id": (
                                    "selection:binance-context-quality"
                                ),
                                "repair_action_batches": [
                                    {
                                        "scope": "binance",
                                        "action_type": (
                                            "refresh_crypto_microstructure"
                                        ),
                                        "count": 4,
                                        "symbols": ["NEARUSDT"],
                                    }
                                ],
                                "evidence_quality": {
                                    "summary_line": (
                                        "evidence_quality sources=3 partial=1"
                                    ),
                                    "status_counts": {"partial": 1, "strong": 2},
                                    "top_warnings": ["funding_missing"],
                                },
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"hold_decision": {"summary": "관망"}}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.symbol.NEARUSDT")
    queue_page = service.read_page("binance.research.repair_queue")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "refresh_crypto_microstructure" in page["content"]
    assert "Jue Wiki Evidence Quality" in page["content"]
    assert "evidence_quality sources=3 partial=1" in page["content"]
    assert "funding_missing" in page["content"]
    assert queue_page["status"] == "ok"
    assert "refresh_crypto_microstructure" in queue_page["content"]
    assert "NEARUSDT" in queue_page["content"]
    assert "funding_missing" in queue_page["content"]


def test_rebuild_binance_symbols_resolves_manager_context_repair_after_strong_evidence(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                market TEXT,
                status TEXT
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        for run_at, observation in [
            (
                "2026-07-01T18:00:00+00:00",
                {
                    "selection_run_id": "selection:binance-weak",
                    "repair_action_batches": [
                        {
                            "scope": "binance",
                            "action_type": "refresh_crypto_microstructure",
                            "count": 1,
                            "symbols": ["NEARUSDT"],
                        }
                    ],
                    "evidence_quality": {
                        "summary_line": "evidence_quality sources=3 partial=1",
                        "status_counts": {"partial": 1, "strong": 2},
                        "top_warnings": ["funding_missing"],
                    },
                },
            ),
            (
                "2026-07-01T18:20:00+00:00",
                {
                    "selection_run_id": "selection:binance-strong",
                    "evidence_quality": {
                        "summary_line": "evidence_quality sources=4 strong=4",
                        "status_counts": {"strong": 4},
                        "top_warnings": [],
                    },
                },
            ),
        ]:
            conn.execute(
                """
                INSERT INTO manager_runs (
                    run_at, status, error_message, mode, model,
                    prompt_json, response_json, actions_json
                ) VALUES (?, 'ok', '', 'llm', 'gpt-5.5', ?, ?, ?)
                """,
                (
                    run_at,
                    json.dumps(
                        {
                            "proactive_decision_pressure": {
                                "top_candidates": [
                                    {
                                        "symbol": "NEARUSDT",
                                        "market": "futures",
                                        "score": 74,
                                    }
                                ],
                            },
                            "compact_manager_context": {
                                "jue_wiki_selection_observation": observation
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps({"hold_decision": {"summary": "관망"}}, ensure_ascii=False),
                    json.dumps({}, ensure_ascii=False),
                ),
            )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    status = service.project_status_snapshot()

    assert result["status"] == "ok"
    assert status["repair_queue"]["open_count"] == 0
    assert status["repair_queue"]["resolved_count"] == 1
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, details_json
            FROM wiki_repair_actions
            WHERE action_id = ?
            """,
            (
                "repair:manager_context:binance:"
                "refresh_crypto_microstructure:NEARUSDT",
            ),
        ).fetchone()
    assert row is not None
    assert row[0] == "resolved"
    details = json.loads(row[1])
    assert details["resolved_by"] == "manager_context_evidence_quality_recovered"
    assert details["resolved_manager_run_id"]


def test_rebuild_binance_symbols_records_additional_wiki_attention_from_manager_runs(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                market TEXT,
                status TEXT
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:05:00+00:00",
                "ok",
                "",
                "llm",
                "gpt-5.5",
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "latest_input_summary": {
                            "jue_wiki_attention": {
                                "status": "active",
                                "must_address": [
                                    "repair_now",
                                    "additional_attention",
                                ],
                                "resolution_status": "unresolved",
                                "repair_now": {
                                    "component": "spot_underuse_live_probe",
                                    "action_type": "live_probe",
                                    "recommended_resolution": (
                                        "ETHUSDT spot lane을 먼저 검증한다."
                                    ),
                                    "impacted_symbols": ["ETHUSDT"],
                                    "repair_targets": ["binance.symbol.ETHUSDT"],
                                },
                                "additional_attention": [
                                    {
                                        "component": "memory_card_quality",
                                        "action_type": (
                                            "cross_check_memory_card_quality"
                                        ),
                                        "recommended_resolution": (
                                            "SOLUSDT 위키 기억을 최근 체결/리서치와 "
                                            "교차검증한다."
                                        ),
                                        "impacted_symbols": ["SOLUSDT"],
                                        "impacted_page_ids": [
                                            "binance.symbol.SOLUSDT"
                                        ],
                                    }
                                ],
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.symbol.SOLUSDT")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Attention" in page["content"]
    assert "memory_card_quality" in page["content"]
    assert "cross_check_memory_card_quality" in page["content"]
    assert "SOLUSDT 위키 기억을 최근 체결" in page["content"]


def test_rebuild_binance_symbols_records_memory_card_quality_attention(
    tmp_path: Path,
) -> None:
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT,
                symbol TEXT,
                market TEXT,
                status TEXT
            );
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT,
                status TEXT,
                error_message TEXT,
                mode TEXT,
                model TEXT,
                prompt_json TEXT,
                response_json TEXT,
                actions_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (
                run_at, status, error_message, mode, model,
                prompt_json, response_json, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-01T18:00:00+00:00",
                "ok",
                "",
                "llm",
                "gpt-5.5",
                json.dumps({}, ensure_ascii=False),
                json.dumps(
                    {
                        "latest_input_summary": {
                            "jue_wiki_memory_card_quality": {
                                "status": "active",
                                "weak_symbols": ["ETHUSDT"],
                                "required_action": (
                                    "cross_check_crypto_research_before_leverage"
                                ),
                                "missing_fields_by_symbol": [
                                    {
                                        "symbol": "ETHUSDT",
                                        "status": "weak",
                                        "missing_fields": [
                                            "durable_facts",
                                            "open_questions",
                                        ],
                                    }
                                ],
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
                                "resolution_status": "unresolved",
                            }
                        },
                        "hold_decision": {
                            "summary": "ETHUSDT crypto memory card is thin",
                            "watch_symbols": ["ETHUSDT"],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps({}, ensure_ascii=False),
            ),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.symbol.ETHUSDT")
    pack = service.context_pack(
        target_scope="binance",
        symbols=["ETHUSDT"],
        page_types=["symbol"],
        max_chars=1200,
    )

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "Jue Wiki Memory Card Quality" in page["content"]
    assert "resolution=unresolved" in page["content"]
    assert "cross_check_crypto_research_before_leverage" in page["content"]
    assert "missing_fields=durable_facts|open_questions" in page["content"]
    assert (
        "required_checks=refresh_durable_facts_from_reports_fundamentals_and_market_context|"
        "record_open_questions_and_data_gaps_before_confident_action"
    ) in page["content"]
    assert "cross_check_crypto_research_before_leverage" in pack["content"]
    assert "missing_fields=durable_facts|open_questions" in pack["content"]


def test_rebuild_binance_symbols_skips_non_tradable_crypto_research_symbols(
    tmp_path: Path,
) -> None:
    crypto_db = tmp_path / "crypto_market_research.db"
    with sqlite3.connect(crypto_db) as conn:
        conn.executescript(
            """
            CREATE TABLE crypto_symbol_notes (
                symbol TEXT PRIMARY KEY,
                stance TEXT,
                horizon TEXT,
                confidence REAL,
                summary_md TEXT,
                reasons_json TEXT,
                risks_json TEXT,
                triggers_json TEXT,
                updated_at TEXT
            );
            CREATE TABLE crypto_candidates (
                symbol TEXT,
                market TEXT,
                stance TEXT,
                horizon TEXT,
                score REAL,
                confidence REAL,
                reason_md TEXT,
                block_template_json TEXT,
                source_run_id INTEGER,
                updated_at TEXT,
                PRIMARY KEY(symbol, market, stance, horizon)
            );
            INSERT INTO crypto_symbol_notes VALUES (
                'MARKET_REGIME', 'observe', 'intraday', 0.9,
                'This is a regime note, not a tradable symbol.',
                '[]', '[]', '[]', '2026-07-01T08:00:00+00:00'
            );
            INSERT INTO crypto_symbol_notes VALUES (
                char(5) || '2' || char(1), 'long_watch', 'intraday', 0.5,
                'Malformed symbol must not become a wiki symbol page.',
                '[]', '[]', '[]', '2026-07-01T08:00:00+00:00'
            );
            INSERT INTO crypto_candidates VALUES (
                '币安人生USDT', 'spot', 'long_watch', 'intraday', 80, 0.6,
                'Non-ASCII meme label is not a verified executable symbol.',
                '{}', 11, '2026-07-01T08:05:00+00:00'
            );
            INSERT INTO crypto_symbol_notes VALUES (
                'ETHUSDT', 'long_watch', 'intraday', 0.74,
                'ETH is a valid executable symbol.',
                '[]', '[]', '[]', '2026-07-01T08:00:00+00:00'
            );
            """
        )
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.execute(
            "CREATE TABLE blocks (block_id TEXT, symbol TEXT, market TEXT, status TEXT)"
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            binance_blocks_db_path=binance_db,
            crypto_market_research_db_path=crypto_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)

    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert service.read_page("binance.symbol.ETHUSDT")["status"] == "ok"
    assert service.read_page("binance.symbol.MARKET_REGIME")["status"] == "not_found"
    assert service.read_page("binance.symbol.2")["status"] == "not_found"
    assert service.read_page("binance.symbol.USDT")["status"] == "not_found"


def test_rebuild_all_compiles_kis_and_binance_symbol_pages(tmp_path: Path) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                status TEXT,
                thesis TEXT,
                created_at TEXT
            );
            INSERT INTO blocks VALUES (
                'blk_000660_1', '000660', 'SK하이닉스', 'open',
                'HBM 모멘텀', '2026-06-20T00:00:00+00:00'
            );
            """
        )
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                market TEXT,
                side TEXT,
                status TEXT,
                thesis TEXT,
                created_at TEXT
            );
            INSERT INTO blocks VALUES (
                'bnb_spot_ETHUSDT_1', 'ETHUSDT', 'spot', 'long', 'open',
                'ETH spot rotation', '2026-06-20T00:00:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=kis_db,
            binance_blocks_db_path=binance_db,
            investment_memory_db_path=tmp_path / "missing_memory.db",
        )
    )

    result = service.rebuild(scope="all", force=True)

    assert result["status"] == "ok"
    assert result["scope"] == "all"
    assert result["updated_count"] == 2
    assert service.read_page("kis.symbol.000660")["status"] == "ok"
    assert service.read_page("binance.symbol.ETHUSDT")["status"] == "ok"


def test_rebuild_reports_missing_source_dbs_and_tables(tmp_path: Path) -> None:
    empty_kis_db = tmp_path / "empty_kis.db"
    with sqlite3.connect(empty_kis_db) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=empty_kis_db,
            binance_blocks_db_path=tmp_path / "missing_binance.db",
            investment_memory_db_path=tmp_path / "missing_memory.db",
        )
    )

    result = service.rebuild(scope="all", force=True)

    assert result["status"] == "warn"
    assert result["updated_count"] == 0
    assert any("missing_kis_blocks_table" in row for row in result["warnings"])
    assert any("missing_binance_blocks_db" in row for row in result["warnings"])


def test_rebuild_attaches_rag_research_to_existing_symbol_page(
    tmp_path: Path,
) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                status TEXT,
                qty_initial INTEGER,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                thesis TEXT,
                llm_reason TEXT,
                risk_note TEXT,
                created_at TEXT,
                closed_at TEXT,
                realized_pnl REAL
            );
            INSERT INTO blocks VALUES (
                'blk_005930_1', '005930', '삼성전자', 'open', 1, 70000, 73500,
                68200, '저평가 눌림목', '', '', '2026-06-20T00:00:00+00:00',
                '', 0
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=kis_db,
        ),
        rag_store=FakeRagStore(),
    )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.005930")
    assert "HBM 수요" in page["content"]
    assert "rag:r1" in page["content"]
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        source_refs = conn.execute(
            """
            SELECT source_type, source_id
            FROM wiki_source_refs
            WHERE page_id = 'kis.symbol.005930' AND source_type = 'rag'
            """
        ).fetchall()
    assert source_refs == [("rag", "r1")]


def test_rebuild_kis_symbols_uses_naver_reports_without_existing_blocks(
    tmp_path: Path,
) -> None:
    reports_db = tmp_path / "naver_reports.db"
    with sqlite3.connect(reports_db) as conn:
        conn.executescript(
            """
            CREATE TABLE reports (
                report_id INTEGER PRIMARY KEY,
                title TEXT,
                company_name TEXT,
                symbol TEXT,
                broker TEXT,
                published_at TEXT,
                detail_url TEXT,
                pdf_url TEXT
            );
            CREATE TABLE report_symbol_links (
                report_id INTEGER,
                symbol TEXT,
                name TEXT,
                link_type TEXT,
                confidence REAL
            );
            CREATE TABLE report_facts (
                report_id INTEGER PRIMARY KEY,
                rating TEXT,
                target_price_value REAL,
                target_price_currency TEXT,
                summary_bullets_json TEXT,
                investment_thesis_json TEXT,
                risks_json TEXT,
                catalysts_json TEXT
            );
            INSERT INTO reports VALUES (
                42, '삼성전자 2Q Preview', '삼성전자', '005930', '교보증권',
                '2026-07-01T00:00:00+09:00',
                'https://finance.naver.com/research/company_read.naver?nid=42',
                ''
            );
            INSERT INTO report_symbol_links VALUES (
                42, '005930', '삼성전자', 'primary', 0.98
            );
            INSERT INTO report_facts VALUES (
                42, 'BUY', 500000, 'KRW',
                '["HBM 수요 회복", "메모리 가격 반등"]',
                '["실적 추정 상향과 목표가 상향이 동시에 제시됨"]',
                '["환율과 서버 수요 변동성"]',
                '["2분기 실적 발표"]'
            );
            """
        )
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.execute(
            "CREATE TABLE blocks (block_id TEXT, symbol TEXT, name TEXT, status TEXT)"
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=kis_db,
            naver_reports_db_path=reports_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.005930")
    sources = service.page_sources("kis.symbol.005930")
    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "Naver Reports" in page["content"]
    assert "삼성전자 2Q Preview" in page["content"]
    assert "rating=BUY" in page["content"]
    assert "HBM 수요 회복" in page["content"]
    assert any(
        row["source_type"] == "naver_reports" and row["source_id"] == "42"
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_attaches_symbol_fundamentals_source_refs(
    tmp_path: Path,
) -> None:
    fundamentals_db = tmp_path / "symbol_fundamentals.db"
    observed_at = datetime.now(timezone.utc)
    as_of = observed_at.date().isoformat()
    crawled_at = observed_at.isoformat()
    with sqlite3.connect(fundamentals_db) as conn:
        conn.executescript(
            f"""
            CREATE TABLE valuation_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                price INTEGER,
                market_cap_krw INTEGER,
                per REAL,
                eps INTEGER,
                pbr REAL,
                bps INTEGER,
                dividend_yield_pct REAL,
                industry_per REAL,
                industry_name TEXT NOT NULL DEFAULT '',
                as_of TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{{}}',
                crawled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                error_message TEXT NOT NULL DEFAULT '',
                last_attempt_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE valuation_scores (
                symbol TEXT PRIMARY KEY,
                undervalued_score INTEGER NOT NULL DEFAULT 0,
                overvalued_risk INTEGER NOT NULL DEFAULT 0,
                quality_score INTEGER NOT NULL DEFAULT 0,
                growth_score INTEGER NOT NULL DEFAULT 0,
                relative_per_discount_pct REAL,
                pbr_roe_fit REAL,
                label TEXT NOT NULL DEFAULT 'unknown',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                risks_json TEXT NOT NULL DEFAULT '[]',
                scored_at TEXT NOT NULL
            );
            CREATE TABLE financial_snapshots (
                financial_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period_type TEXT NOT NULL DEFAULT '',
                period TEXT NOT NULL,
                revenue REAL,
                operating_profit REAL,
                net_income REAL,
                roe REAL,
                debt_ratio REAL,
                operating_margin REAL,
                raw_json TEXT NOT NULL DEFAULT '{{}}',
                crawled_at TEXT NOT NULL
            );
            INSERT INTO valuation_snapshots (
                symbol, name, price, market_cap_krw, per, eps, pbr, bps,
                dividend_yield_pct, industry_per, industry_name, as_of,
                source_url, crawled_at, status
            ) VALUES (
                '005930', '삼성전자', 73000, 435000000000000, 13.4, 5450,
                1.21, 60300, 1.9, 17.8, '반도체와반도체장비',
                    '{as_of}',
                    'https://finance.naver.com/item/coinfo.naver?code=005930',
                    '{crawled_at}', 'ok'
            );
            INSERT INTO valuation_scores VALUES (
                '005930', 72, 18, 81, 64, 24.7, 0.83, 'undervalued',
                '["업종 PER 대비 할인", "PBR 부담 낮음"]',
                '["메모리 사이클 변동성"]',
                    '{crawled_at}'
            );
            INSERT INTO financial_snapshots (
                symbol, period_type, period, revenue, operating_profit,
                net_income, roe, debt_ratio, operating_margin, crawled_at
            ) VALUES (
                '005930', 'annual', '2025', 300000000000000, 32000000000000,
                26000000000000, 9.8, 27.5, 10.7,
                    '{crawled_at}'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            symbol_fundamentals_db_path=fundamentals_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.005930")
    sources = service.page_sources("kis.symbol.005930")
    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert page["status"] == "ok"
    assert "Naver / WiseReport Fundamentals" in page["content"]
    assert "valuation quality=strong" in page["content"]
    assert "label=undervalued" in page["content"]
    assert "PER=13.40" in page["content"]
    assert "industry_PER=17.80" in page["content"]
    assert "업종 PER 대비 할인" in page["content"]
    assert "annual:2025 revenue=300,000,000,000,000" in page["content"]
    assert any(
        row["source_type"] == "symbol_fundamentals"
        and row["source_id"] == f"005930:{as_of}"
        and row["quality_status"] == "strong"
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_marks_sparse_symbol_fundamentals_as_weak(
    tmp_path: Path,
) -> None:
    fundamentals_db = tmp_path / "symbol_fundamentals.db"
    with sqlite3.connect(fundamentals_db) as conn:
        conn.executescript(
            """
            CREATE TABLE valuation_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                price INTEGER,
                as_of TEXT NOT NULL,
                crawled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok'
            );
            INSERT INTO valuation_snapshots (
                symbol, name, price, as_of, crawled_at, status
            ) VALUES (
                '402340', '', NULL, '2026-07-03',
                '2026-07-03T00:00:00+00:00', 'ok'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            symbol_fundamentals_db_path=fundamentals_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.402340")
    sources = service.page_sources("kis.symbol.402340")
    page_rows = service.search_pages(
        scope="kis",
        symbols=["402340"],
        page_types=["symbol"],
        include_content=False,
    )
    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert page_rows[0]["confidence"] == 0.48
    assert "valuation quality=weak" in page["content"]
    assert "identity_name_missing" in page["content"]
    assert "price_missing" in page["content"]
    assert "valuation_metrics_sparse" in page["content"]
    assert any(
        row["source_type"] == "symbol_fundamentals"
        and row["source_id"] == "402340:2026-07-03"
        and row["quality_status"] == "weak"
        and "price_missing" in row["quality_warnings"]
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_canonicalizes_symbol_fundamentals_quality_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_read_symbol_fundamentals_by_symbol",
        lambda: {
            "005930": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "source_id": "005930:alias",
                    "source_url": "https://finance.naver.com/item/coinfo.naver?code=005930",
                    "as_of": "2026-07-03",
                    "crawled_at": "2026-07-03T00:00:00+00:00",
                    "quality_status": "degraded",
                    "quality_confidence": 0.9,
                    "quality_warnings": ["valuation_metrics_sparse"],
                }
            ]
        },
    )

    result = service.rebuild(scope="kis", force=True)

    page_rows = service.search_pages(
        scope="kis",
        symbols=["005930"],
        page_types=["symbol"],
        include_content=False,
    )
    page = service.read_page("kis.symbol.005930")
    sources = service.page_sources("kis.symbol.005930")
    assert result["status"] == "ok"
    assert page_rows[0]["confidence"] == 0.5
    assert "valuation quality=weak" in page["content"]
    assert any(
        row["source_type"] == "symbol_fundamentals"
        and row["source_id"] == "005930:alias"
        and row["quality_status"] == "weak"
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_flags_credit_rating_financial_noise(
    tmp_path: Path,
) -> None:
    fundamentals_db = tmp_path / "symbol_fundamentals.db"
    with sqlite3.connect(fundamentals_db) as conn:
        conn.executescript(
            """
            CREATE TABLE valuation_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                price INTEGER,
                market_cap_krw INTEGER,
                per REAL,
                eps INTEGER,
                pbr REAL,
                bps INTEGER,
                industry_per REAL,
                as_of TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                crawled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok'
            );
            CREATE TABLE valuation_scores (
                symbol TEXT PRIMARY KEY,
                undervalued_score INTEGER NOT NULL DEFAULT 0,
                overvalued_risk INTEGER NOT NULL DEFAULT 0,
                quality_score INTEGER NOT NULL DEFAULT 0,
                growth_score INTEGER NOT NULL DEFAULT 0,
                relative_per_discount_pct REAL,
                pbr_roe_fit REAL,
                label TEXT NOT NULL DEFAULT 'unknown',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                risks_json TEXT NOT NULL DEFAULT '[]',
                scored_at TEXT NOT NULL
            );
            CREATE TABLE financial_snapshots (
                financial_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period_type TEXT NOT NULL DEFAULT '',
                period TEXT NOT NULL,
                revenue REAL,
                operating_profit REAL,
                net_income REAL,
                roe REAL,
                debt_ratio REAL,
                operating_margin REAL,
                crawled_at TEXT NOT NULL
            );
            INSERT INTO valuation_snapshots (
                symbol, name, price, market_cap_krw, per, eps, pbr, bps,
                industry_per, as_of, source_url, crawled_at, status
            ) VALUES (
                '402340', '테스트기업', 100000, 1000000000000, 10.0, 10000,
                1.0, 100000, 18.0, '2026-07-03',
                'https://finance.naver.com/item/coinfo.naver?code=402340',
                '2026-07-03T00:00:00+00:00', 'ok'
            );
            INSERT INTO valuation_scores VALUES (
                '402340', 70, 12, 55, 20, 44.4, 0.5, 'undervalued',
                '["업종 대비 할인"]', '[]',
                '2026-07-03T00:01:00+00:00'
            );
            INSERT INTO financial_snapshots (
                symbol, period_type, period, revenue, operating_profit,
                net_income, roe, debt_ratio, operating_margin, crawled_at
            ) VALUES (
                '402340', 'mixed', 'A1 [20260331]', NULL, NULL, NULL,
                NULL, NULL, NULL, '2026-07-03T00:02:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            symbol_fundamentals_db_path=fundamentals_db,
        )
    )

    service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.402340")
    sources = service.page_sources("kis.symbol.402340")
    assert "valuation quality=partial" in page["content"]
    assert "financial_rows_rejected_credit_rating" in page["content"]
    assert "financial_rows_rejected_empty" in page["content"]
    assert "financials_missing" in page["content"]
    assert "financials=-" in page["content"]
    assert "financials=mixed:A1 [20260331]" not in page["content"]
    assert any(
        row["source_type"] == "symbol_fundamentals"
        and row["quality_status"] == "partial"
        and "financial_rows_rejected_credit_rating" in row["quality_warnings"]
        for row in sources["source_refs"]
    )


def test_rebuild_kis_symbols_accumulates_etf_and_strategy_insights(
    tmp_path: Path,
) -> None:
    etf_db = tmp_path / "etf_research.db"
    with sqlite3.connect(etf_db) as conn:
        conn.executescript(
            """
            CREATE TABLE etf_market_snapshots (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                price REAL,
                change_pct REAL,
                volume INTEGER,
                turnover_krw REAL,
                captured_at TEXT
            );
            CREATE TABLE etf_scores (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                label TEXT,
                liquidity_score REAL,
                momentum_score REAL,
                core_fit_score REAL,
                risk_score REAL,
                reasons_json TEXT,
                risks_json TEXT,
                scored_at TEXT
            );
            CREATE TABLE etf_universe (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                category TEXT
            );
            INSERT INTO etf_market_snapshots VALUES (
                1, '069500', 'KODEX 200', 40000, 1.2, 1000000,
                40000000000, '2026-07-01T00:00:00+00:00'
            );
            INSERT INTO etf_scores VALUES (
                1, '069500', 'core_fit', 98, 62, 91, 8,
                '["대형주 코어 익스포저", "유동성 충분"]',
                '["지수 급등 후 눌림 필요"]',
                '2026-07-01T00:01:00+00:00'
            );
            INSERT INTO etf_universe VALUES ('069500', 'KODEX 200', 'core');
            """
        )
    insights_db = tmp_path / "strategy_insights.db"
    with sqlite3.connect(insights_db) as conn:
        conn.executescript(
            """
            CREATE TABLE strategy_signals (
                signal_id TEXT PRIMARY KEY,
                source_id TEXT,
                symbol TEXT,
                name TEXT,
                signal_type TEXT,
                direction TEXT,
                strength REAL,
                summary TEXT,
                as_of TEXT,
                tags_json TEXT,
                collected_at TEXT,
                updated_at TEXT
            );
            INSERT INTO strategy_signals VALUES (
                'sig-1', 'after_close_330', '069500', 'KODEX 200',
                'sector_treemap', 'positive', 88,
                '세시반 시장 대표 ETF 수급 강도 개선',
                '2026-07-01T15:30:00+09:00',
                '["sesiban", "flow"]',
                '2026-07-01T06:30:00+00:00',
                '2026-07-01T06:31:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            etf_research_db_path=etf_db,
            strategy_insights_db_path=insights_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.069500")
    sources = service.page_sources("kis.symbol.069500")
    assert result["status"] == "ok"
    assert "ETF / Flow Research" in page["content"]
    assert "KODEX 200" in page["content"]
    assert "대형주 코어 익스포저" in page["content"]
    assert "세시반 시장 대표 ETF 수급 강도 개선" in page["content"]
    assert any(row["source_type"] == "etf_research" for row in sources["source_refs"])
    assert any(
        row["source_type"] == "strategy_insight" and row["source_id"] == "sig-1"
        for row in sources["source_refs"]
    )


def test_rebuild_kis_writes_market_pulse_regime_page(tmp_path: Path) -> None:
    pulse_db = tmp_path / "market_pulse.db"
    with sqlite3.connect(pulse_db) as conn:
        conn.executescript(
            """
            CREATE TABLE market_pulse_snapshots (
                id INTEGER PRIMARY KEY,
                captured_at TEXT,
                trading_day TEXT,
                status TEXT,
                regime TEXT,
                score REAL,
                indices_json TEXT,
                sector_json TEXT,
                block_alignment_json TEXT,
                risk_flags_json TEXT,
                data_gaps_json TEXT
            );
            INSERT INTO market_pulse_snapshots VALUES (
                7, '2026-07-01T00:00:00+00:00', '2026-07-01',
                'ok', 'risk_on', 82.5,
                '[{"code":"KOSPI","value":3000,"change_pct":1.5,"direction":"up"}]',
                '[{"name":"반도체","score":91,"change_pct":2.4,"direction":"up"}]',
                '{}',
                '["program_buying_reversal_watch"]',
                '[]'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            market_pulse_db_path=pulse_db,
        )
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.regime.market_pulse")
    sources = service.page_sources("kis.regime.market_pulse")
    assert result["status"] == "ok"
    assert "risk_on" in page["content"]
    assert "반도체" in page["content"]
    assert "program_buying_reversal_watch" in page["content"]
    assert any(
        row["source_type"] == "market_pulse"
        and row["source_id"] == "7"
        and row["source_scope"] == "kis"
        for row in sources["source_refs"]
    )


def test_rebuild_binance_symbols_accumulates_quant_pattern_and_alpha(
    tmp_path: Path,
) -> None:
    quant_db = tmp_path / "crypto_quant.db"
    with sqlite3.connect(quant_db) as conn:
        conn.executescript(
            """
            CREATE TABLE crypto_quant_signals (
                symbol TEXT,
                horizon TEXT,
                long_score REAL,
                short_score REAL,
                no_trade_score REAL,
                expected_r_long REAL,
                expected_r_short REAL,
                signal_json TEXT,
                updated_at TEXT
            );
            INSERT INTO crypto_quant_signals VALUES (
                'BTCUSDT', 'intraday', 82, 24, 15, 0.62, -0.2,
                '{"bias":"long","drivers":["volume expansion"],"risks":["spread check"],"metrics":{"rsi":58,"atr_pct":1.2,"spread_bps":2.1}}',
                '2026-07-01T00:00:00+00:00'
            );
            """
        )
    pattern_db = tmp_path / "crypto_pattern_lab.db"
    with sqlite3.connect(pattern_db) as conn:
        conn.executescript(
            """
            CREATE TABLE optimized_strategy_sets (
                set_id TEXT,
                run_id TEXT,
                trial_id TEXT,
                pattern_id TEXT,
                symbol TEXT,
                interval TEXT,
                family TEXT,
                direction TEXT,
                parameter_set_json TEXT,
                objective TEXT,
                objective_score REAL,
                trade_count INTEGER,
                win_rate REAL,
                expectancy_r REAL,
                profit_factor REAL,
                max_loss_r REAL,
                out_of_sample_trade_count INTEGER,
                out_of_sample_expectancy_r REAL,
                out_of_sample_profit_factor REAL,
                out_of_sample_max_drawdown_r REAL,
                overfit_risk TEXT,
                status TEXT,
                promoted_at TEXT
            );
            INSERT INTO optimized_strategy_sets VALUES (
                'set-1', 'run-1', 'trial-1', 'pattern-1', 'BTCUSDT',
                '15m', 'breakout', 'long', '{"stop_pct":0.01,"target_pct":0.025}',
                'risk_adjusted_net_r_v1', 12.4, 80, 0.61, 0.42, 1.9,
                -1.0, 20, 0.35, 1.7, -2.4, 'low', 'active',
                '2026-07-01T00:01:00+00:00'
            );
            """
        )
    alpha_db = tmp_path / "crypto_alpha.db"
    with sqlite3.connect(alpha_db) as conn:
        conn.executescript(
            """
            CREATE TABLE crypto_alpha_event_symbols (
                event_id INTEGER,
                symbol TEXT,
                impact_direction TEXT,
                impact_horizon TEXT,
                link_confidence REAL,
                reason TEXT,
                validity_status TEXT,
                validity_reason TEXT
            );
            CREATE TABLE crypto_alpha_events (
                event_id INTEGER,
                event_type TEXT,
                title TEXT,
                summary TEXT,
                event_time TEXT,
                detected_at TEXT,
                confidence REAL,
                importance REAL,
                status TEXT
            );
            INSERT INTO crypto_alpha_events VALUES (
                3, 'listing', 'BTC catalyst', '기관 수요 이벤트',
                '2026-07-01T00:00:00+00:00',
                '2026-07-01T00:02:00+00:00',
                0.8, 0.9, 'active'
            );
            INSERT INTO crypto_alpha_event_symbols VALUES (
                3, 'BTCUSDT', 'bullish_watch', '1h_72h', 0.9,
                'public catalyst matched BTC', 'valid', ''
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            crypto_quant_db_path=quant_db,
            crypto_pattern_lab_db_path=pattern_db,
            crypto_alpha_db_path=alpha_db,
        )
    )

    result = service.rebuild(scope="binance", force=True)

    page = service.read_page("binance.symbol.BTCUSDT")
    sources = service.page_sources("binance.symbol.BTCUSDT")
    assert result["status"] == "ok"
    assert "Quant / Pattern / Alpha Research" in page["content"]
    assert "volume expansion" in page["content"]
    assert "pattern set=set-1" in page["content"]
    assert "BTC catalyst" in page["content"]
    assert any(row["source_type"] == "crypto_quant" for row in sources["source_refs"])
    assert any(
        row["source_type"] == "crypto_pattern_lab" and row["source_id"] == "set-1"
        for row in sources["source_refs"]
    )
    assert any(
        row["source_type"] == "crypto_alpha" and row["source_id"] == "3"
        for row in sources["source_refs"]
    )


def test_rebuild_keeps_rag_provider_failures_non_fatal(tmp_path: Path) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                status TEXT,
                thesis TEXT,
                created_at TEXT
            );
            INSERT INTO blocks VALUES (
                'blk_005930_1', '005930', '삼성전자', 'open',
                '저평가 눌림목', '2026-06-20T00:00:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=kis_db,
        ),
        rag_store=FailingRagStore(),
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.005930")
    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert "rag_error:RuntimeError:rag offline" in page["content"]
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        source_refs = conn.execute(
            """
            SELECT source_type
            FROM wiki_source_refs
            WHERE page_id = 'kis.symbol.005930' AND source_type = 'rag'
            """
        ).fetchall()
    assert source_refs == []


def test_rebuild_queries_limit_style_rag_store(tmp_path: Path) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                status TEXT,
                thesis TEXT,
                created_at TEXT
            );
            INSERT INTO blocks VALUES (
                'blk_005930_1', '005930', '삼성전자', 'open',
                '저평가 눌림목', '2026-06-20T00:00:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=kis_db,
        ),
        rag_store=LimitOnlyRagStore(),
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.005930")
    sources = service.page_sources("kis.symbol.005930")
    assert result["status"] == "ok"
    assert result["updated_count"] == 1
    assert "실제 RAG 계약 리포트" in page["content"]
    assert "rag_error" not in page["content"]
    assert any(row["source_type"] == "rag" for row in sources["source_refs"])


def test_rebuild_reads_top_level_rag_store_rows(tmp_path: Path) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                status TEXT,
                thesis TEXT,
                created_at TEXT
            );
            INSERT INTO blocks VALUES (
                'blk_005930_1', '005930', '삼성전자', 'open',
                '저평가 눌림목', '2026-06-20T00:00:00+00:00'
            );
            """
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=kis_db,
        ),
        rag_store=TopLevelRagStore(),
    )

    result = service.rebuild(scope="kis", force=True)

    page = service.read_page("kis.symbol.005930")
    sources = service.page_sources("kis.symbol.005930")
    assert result["status"] == "ok"
    assert "삼성전자 실제 리포트" in page["content"]
    assert "실제 RAGStore 반환 형태" in page["content"]
    assert "rag:5253" in page["content"]
    assert any(
        row["source_type"] == "rag" and row["source_id"] == "5253"
        for row in sources["source_refs"]
    )


def test_lint_detects_stale_oversized_missing_sources_and_scope_leakage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=420)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "BTCUSDT should not appear in KIS notes.",
            "Durable Facts": "KIS stock memo.",
            "Evidence Links": "- No linked evidence.",
            "Trading History": "- No trading history.",
            "Lessons": "- No lessons.",
            "Contradictions": "- No contradiction.",
            "Open Questions": "- No question.",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[],
        confidence=0.2,
        freshness="stale",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            UPDATE wiki_pages
            SET char_count = ?
            WHERE page_id = 'kis.symbol.005930'
            """,
            (service.config.page_max_chars + 1,),
        )

    result = service.lint(scope="kis")

    finding_types = {row["finding_type"] for row in result["open_findings"]}
    assert result["status"] == "warn"
    assert result["scope"] == "kis"
    assert {
        "missing_sources",
        "oversized_page",
        "scope_leakage",
        "stale_page",
    } <= finding_types


def test_lint_detects_nested_source_ref_identity_gaps(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 weak nested source identity",
        symbols=["005930"],
        content_sections={
            "Current Stance": "watch",
            "Durable Facts": "facts",
            "Evidence Links": "nested source refs need repair",
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
                        "quality_status": "weak",
                        "quality_warnings": ["financials_missing"],
                    },
                    {
                        "source_id": "missing-source-type",
                        "quality_status": "partial",
                    },
                ],
            }
        ],
        confidence=0.7,
        freshness="fresh",
    )

    result = service.lint(scope="kis")
    finding = next(
        row
        for row in result["open_findings"]
        if row["finding_type"] == "source_ref_identity_gap"
    )

    assert result["status"] == "warn"
    assert finding["page_id"] == "kis.symbol.005930"
    assert finding["evidence"]["gap_count"] == 2
    assert finding["evidence"]["examples"] == [
        {
            "index": 1,
            "source_type": "symbol_fundamentals",
            "source_id": "generated:symbol_fundamentals:"
            + service._source_ref_index_id(
                {
                    "source_type": "symbol_fundamentals",
                    "quality_status": "weak",
                    "quality_warnings": ["financials_missing"],
                }
            ).rsplit(":", 1)[-1],
            "missing": ["source_id"],
        },
        {
            "index": 2,
            "source_type": "",
            "source_id": "missing-source-type",
            "missing": ["source_type"],
        },
    ]


def test_lint_filters_scopes_and_resolves_previous_findings(tmp_path: Path) -> None:
    service = _service(tmp_path, page_max_chars=500)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "KIS-only note.",
            "Durable Facts": "facts",
            "Evidence Links": "- source",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "test", "source_id": "kis-1"}],
        confidence=0.9,
        freshness="fresh",
    )
    service.write_page(
        scope="binance",
        page_type="symbol",
        key="BTCUSDT",
        title="BTCUSDT",
        symbols=["BTCUSDT"],
        content_sections={
            "Current Stance": "Review with 005930 leakage.",
            "Durable Facts": "facts",
            "Evidence Links": "- source",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "test", "source_id": "binance-1"}],
        confidence=0.9,
        freshness="fresh",
    )

    kis_result = service.lint(scope="kis")
    binance_result = service.lint(scope="binance")
    all_result = service.lint(scope="")

    assert kis_result["status"] == "ok"
    assert kis_result["open_findings"] == []
    assert binance_result["status"] == "warn"
    assert [row["page_id"] for row in binance_result["open_findings"]] == [
        "binance.symbol.BTCUSDT"
    ]
    assert {
        row["finding_type"] for row in binance_result["open_findings"]
    } == {"scope_leakage"}
    assert [row["page_id"] for row in all_result["open_findings"]] == [
        "binance.symbol.BTCUSDT"
    ]
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        rows = conn.execute(
            """
            SELECT status
            FROM wiki_lint_findings
            WHERE page_id = 'binance.symbol.BTCUSDT'
            """
        ).fetchall()
    assert rows == [("open",)]


def test_lint_resolves_repair_actions_when_findings_are_cleaned(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=500)
    service.initialize()
    service.write_page(
        scope="binance",
        page_type="symbol",
        key="KRW-AVAX",
        title="KRW-AVAX",
        symbols=["KRW-AVAX"],
        content_sections={
            "Current Stance": "Clean crypto page.",
            "Durable Facts": "facts",
            "Evidence Links": "- source",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "test", "source_id": "binance-1"}],
        confidence=0.9,
        freshness="fresh",
    )
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_lint_findings (
                finding_id, page_id, severity, finding_type, message,
                evidence_json, status, created_at, resolved_at
            ) VALUES (
                'finding:old', 'binance.symbol.KRW-AVAX', 'warn',
                'scope_leakage', 'old false positive', '{}',
                'open', '2026-06-28T01:00:00+00:00', ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:old', 'finding:old', 'binance.symbol.KRW-AVAX',
                'mark_unresolved', 'unresolved', '{}',
                '2026-06-28T01:05:00+00:00', '', ''
            )
            """
        )

    result = service.lint(scope="binance")

    assert result["status"] == "ok"
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, finished_at
            FROM wiki_repair_actions
            WHERE action_id = 'repair:old'
            """
        ).fetchone()
    assert row[0] == "resolved"
    assert row[1]


def test_lint_resolves_repair_actions_when_impacted_targets_are_cleaned(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "Financials refreshed.",
            "Durable Facts": "facts",
            "Evidence Links": "- symbol_fundamentals:005930:fresh",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:fresh",
                "quality_status": "strong",
                "quality_warnings": [],
            }
        ],
        confidence=0.9,
        freshness="fresh",
    )
    details = {
        "finding_type": "quality_warning_effectiveness",
        "quality_warnings": ["financials_missing"],
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
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:financials-target', 'quality_warning_effectiveness:old',
                'quality_warning.financials_missing',
                'repair_quality_warning_effectiveness', 'scheduled', ?,
                '2026-07-03T01:05:00+00:00', '', ''
            )
            """,
            (json.dumps(details, ensure_ascii=False, sort_keys=True),),
        )

    result = service.lint(scope="kis")

    assert result["status"] == "ok"
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, finished_at, details_json
            FROM wiki_repair_actions
            WHERE action_id = 'repair:financials-target'
            """
        ).fetchone()
    assert row[0] == "resolved"
    assert row[1]
    assert json.loads(row[2])["resolved_by"] == "repair_targets_cleaned"


def test_lint_keeps_repair_actions_open_when_impacted_targets_still_warn(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "Financials still missing.",
            "Durable Facts": "facts",
            "Evidence Links": "- symbol_fundamentals:005930:partial",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:partial",
                "quality_status": "partial",
                "quality_warnings": ["financials_missing"],
            }
        ],
        confidence=0.7,
        freshness="fresh",
    )
    details = {
        "finding_type": "quality_warning_effectiveness",
        "quality_warnings": ["financials_missing"],
        "impacted_page_ids": ["kis.symbol.005930"],
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
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:financials-still-dirty',
                'quality_warning_effectiveness:dirty',
                'quality_warning.financials_missing',
                'repair_quality_warning_effectiveness', 'scheduled', ?,
                '2026-07-03T01:05:00+00:00', '', ''
            )
            """,
            (json.dumps(details, ensure_ascii=False, sort_keys=True),),
        )

    result = service.lint(scope="kis")

    assert result["status"] == "ok"
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, finished_at
            FROM wiki_repair_actions
            WHERE action_id = 'repair:financials-still-dirty'
            """
        ).fetchone()
    assert row == ("scheduled", "")


def test_lint_keeps_degraded_summary_repair_open_until_page_is_fresh(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    content_sections = {
        "Current Stance": "Summary has clean refs but stale freshness.",
        "Durable Facts": "facts",
        "Evidence Links": "- symbol_fundamentals:005930:fresh",
        "Trading History": "history",
        "Lessons": "lesson",
        "Contradictions": "none",
        "Open Questions": "question",
        "Next Context Pack Summary": "summary",
    }
    source_refs = [
        {
            "source_type": "symbol_fundamentals",
            "source_id": "005930:fresh",
            "quality_status": "strong",
            "quality_warnings": [],
        }
    ]
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections=content_sections,
        source_refs=source_refs,
        confidence=0.9,
        freshness="stale",
    )
    details = {
        "finding_type": "requested_symbol_degraded_summary",
        "quality_warnings": ["requested_symbol_summary_degraded"],
        "impacted_page_ids": ["kis.symbol.005930"],
        "impacted_symbols": ["005930"],
        "repair_targets": [
            {
                "page_id": "kis.symbol.005930",
                "symbol": "005930",
                "recommended_action": (
                    "refresh_stale_or_weak_requested_symbol_wiki_summary"
                ),
            }
        ],
    }
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:degraded-summary-stale',
                'requested_symbol_degraded_summary:kis:005930',
                'kis.symbol.005930',
                'refresh_requested_symbol_summary', 'scheduled', ?,
                '2026-07-03T01:05:00+00:00', '', ''
            )
            """,
            (json.dumps(details, ensure_ascii=False, sort_keys=True),),
        )

    service.lint(scope="kis")

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, finished_at
            FROM wiki_repair_actions
            WHERE action_id = 'repair:degraded-summary-stale'
            """
        ).fetchone()
    assert row == ("scheduled", "")

    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            **content_sections,
            "Current Stance": "Summary has been refreshed.",
        },
        source_refs=source_refs,
        confidence=0.9,
        freshness="fresh",
    )
    service.lint(scope="kis")

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        status, finished_at, details_json = conn.execute(
            """
            SELECT status, finished_at, details_json
            FROM wiki_repair_actions
            WHERE action_id = 'repair:degraded-summary-stale'
            """
        ).fetchone()
    assert status == "resolved"
    assert finished_at
    assert json.loads(details_json)["resolved_by"] == "repair_targets_cleaned"


def test_lint_resolves_degraded_summary_repair_when_page_is_current(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    content_sections = {
        "Current Stance": "Summary is current and source backed.",
        "Durable Facts": "facts",
        "Evidence Links": "- symbol_fundamentals:005930:current",
        "Trading History": "history",
        "Lessons": "lesson",
        "Contradictions": "none",
        "Open Questions": "question",
        "Next Context Pack Summary": "summary",
    }
    source_refs = [
        {
            "source_type": "symbol_fundamentals",
            "source_id": "005930:current",
            "quality_status": "strong",
            "quality_warnings": [],
        }
    ]
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections=content_sections,
        source_refs=source_refs,
        confidence=0.9,
        freshness="current",
    )
    details = {
        "finding_type": "requested_symbol_degraded_summary",
        "quality_warnings": ["requested_symbol_summary_degraded"],
        "impacted_page_ids": ["kis.symbol.005930"],
        "impacted_symbols": ["005930"],
        "repair_targets": [
            {
                "page_id": "kis.symbol.005930",
                "symbol": "005930",
                "recommended_action": (
                    "refresh_stale_or_weak_requested_symbol_wiki_summary"
                ),
            }
        ],
    }
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:degraded-summary-current',
                'requested_symbol_degraded_summary:kis:005930',
                'kis.symbol.005930',
                'refresh_requested_symbol_summary', 'scheduled', ?,
                '2026-07-03T01:05:00+00:00', '', ''
            )
            """,
            (json.dumps(details, ensure_ascii=False, sort_keys=True),),
        )

    service.lint(scope="kis")

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        status, finished_at, details_json = conn.execute(
            """
            SELECT status, finished_at, details_json
            FROM wiki_repair_actions
            WHERE action_id = 'repair:degraded-summary-current'
            """
        ).fetchone()
    assert status == "resolved"
    assert finished_at
    assert json.loads(details_json)["resolved_by"] == "repair_targets_cleaned"


def test_lint_keeps_degraded_summary_repair_open_for_quality_status_alias(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "Summary is fresh but evidence is degraded.",
            "Durable Facts": "facts",
            "Evidence Links": "- symbol_fundamentals:005930:degraded",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:degraded",
                "quality_status": "degraded",
                "quality_warnings": [],
            }
        ],
        confidence=0.9,
        freshness="fresh",
    )
    details = {
        "finding_type": "requested_symbol_degraded_summary",
        "quality_warnings": ["requested_symbol_summary_degraded"],
        "impacted_page_ids": ["kis.symbol.005930"],
        "impacted_symbols": ["005930"],
        "repair_targets": [
            {
                "page_id": "kis.symbol.005930",
                "symbol": "005930",
                "recommended_action": (
                    "refresh_stale_or_weak_requested_symbol_wiki_summary"
                ),
            }
        ],
    }
    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at, finished_at, error_message
            ) VALUES (
                'repair:degraded-summary-alias',
                'requested_symbol_degraded_summary:kis:005930',
                'kis.symbol.005930',
                'refresh_requested_symbol_summary', 'scheduled', ?,
                '2026-07-03T01:05:00+00:00', '', ''
            )
            """,
            (json.dumps(details, ensure_ascii=False, sort_keys=True),),
        )

    service.lint(scope="kis")

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        row = conn.execute(
            """
            SELECT status, finished_at
            FROM wiki_repair_actions
            WHERE action_id = 'repair:degraded-summary-alias'
            """
        ).fetchone()
    assert row == ("scheduled", "")


def test_lint_ignores_timestamp_microseconds_in_binance_pages(tmp_path: Path) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    service.write_page(
        scope="binance",
        page_type="symbol",
        key="BTCUSDT",
        title="BTCUSDT",
        symbols=["BTCUSDT"],
        content_sections={
            "Current Stance": "last_reviewed_at: 2026-06-28T13:54:42.434524+00:00",
            "Durable Facts": "BTCUSDT crypto memo.",
            "Evidence Links": "- source",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "test", "source_id": "binance-1"}],
        confidence=0.9,
        freshness="fresh",
    )

    result = service.lint(scope="binance")

    assert result["status"] == "ok"
    assert result["open_findings"] == []


def test_lint_ignores_prompt_budget_metric_numbers_in_binance_pages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    service.write_page(
        scope="binance",
        page_type="symbol",
        key="KRW-AVAX",
        title="KRW-AVAX",
        symbols=["KRW-AVAX"],
        content_sections={
            "Current Stance": (
                "manager_run=2354, run_status=error, "
                "error=prompt_budget_exceeded: total_chars=203170 "
                "max_chars=190000, market=upbit_spot"
            ),
            "Durable Facts": "KRW-AVAX is a crypto market symbol.",
            "Evidence Links": "- source",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "test", "source_id": "binance-1"}],
        confidence=0.9,
        freshness="fresh",
    )

    result = service.lint(scope="binance")

    assert result["status"] == "ok"
    assert result["open_findings"] == []


def test_lint_returns_ok_for_clean_pages(tmp_path: Path) -> None:
    service = _service(tmp_path, page_max_chars=1200)
    service.initialize()
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "KIS-only note.",
            "Durable Facts": "facts",
            "Evidence Links": "- source",
            "Trading History": "history",
            "Lessons": "lesson",
            "Contradictions": "none",
            "Open Questions": "question",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[{"source_type": "test", "source_id": "kis-1"}],
        confidence=0.9,
        freshness="fresh",
    )

    result = service.lint(scope="")

    assert result["status"] == "ok"
    assert result["scope"] == "all"
    assert result["open_findings"] == []


def test_context_pack_adds_legacy_metadata_when_v3_tables_are_unavailable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, context_max_chars=1200)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "current",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "legacy summary",
        },
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
    )

    payload = service.context_pack(target_scope="kis", symbols=["005930"])

    assert "legacy summary" in payload["content"]
    assert payload["snapshot_id"] == ""
    assert payload["read_mode"] == "shadow"
    assert payload["coverage_status"] == "legacy"
    assert payload["repair_required"] is False
    assert payload["wiki_context_contract"]["status"] == "legacy"
    assert payload["wiki_context_contract"]["selected_pages"] == ()


def test_context_pack_adds_published_v3_snapshot_metadata(tmp_path: Path) -> None:
    service = _service(tmp_path, context_max_chars=12_000)
    service.initialize()
    evidence = EvidenceRefV1(
        evidence_id="naver-report:42",
        source_type="naver_report",
        source_id="42",
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )
    claim = WikiClaimV3(
        claim_id="claim:kis:005930:direction",
        claim_type="interpretation",
        text="Revision direction is supported.",
        status="verified",
        scope="kis",
        evidence=(evidence,),
        symbols=("005930",),
        confidence=0.9,
    )
    page = JueWikiPageV3(
        page_id="kis.symbol.005930.v3",
        page_type="symbol",
        scope="kis",
        title="005930",
        summary="Current verified V3 research.",
        claims=(claim,),
        relationships=(),
        status="verified",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )
    snapshot = WikiSnapshotV1(
        snapshot_id="snapshot:kis:published",
        scope="kis",
        candidate_artifact_ids=(),
        pages=(page,),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-11T00:00:00+00:00",
    )
    repository = service.repository()
    repository.initialize()
    repository.register_evidence(evidence)
    repository.publish_snapshot(snapshot)

    payload = service.context_pack(target_scope="kis", symbols=["005930"])

    assert payload["snapshot_id"] == snapshot.snapshot_id
    assert payload["read_mode"] == "shadow"
    assert payload["coverage_status"] == "sufficient"
    assert payload["repair_required"] is False
    assert payload["wiki_context_contract"]["snapshot_id"] == snapshot.snapshot_id
    assert payload["wiki_context_contract"]["selected_pages"] == (page.to_dict(),)


def test_context_pack_reads_prior_v3_evidence_schema_without_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path, context_max_chars=12_000)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "current",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "legacy selection remains available",
        },
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
    )
    evidence = EvidenceRefV1(
        evidence_id="naver-report:prior-schema",
        source_type="naver_report",
        source_id="prior-schema",
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )
    claim = WikiClaimV3(
        claim_id="claim:kis:005930:prior-schema",
        claim_type="fact",
        text="Prior-schema evidence remains readable.",
        status="verified",
        scope="kis",
        evidence=(evidence,),
        symbols=("005930",),
        confidence=0.9,
    )
    page = JueWikiPageV3(
        page_id="kis.symbol.005930.prior-schema",
        page_type="symbol",
        scope="kis",
        title="005930 prior schema",
        summary="Prior-schema V3 snapshot.",
        claims=(claim,),
        relationships=(),
        status="verified",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )
    db_path = service.config.db_path
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE wiki_evidence_v1 (
                evidence_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE wiki_snapshots_v1 (
                snapshot_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                compiler_version TEXT NOT NULL,
                candidate_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE wiki_pages_v3 (
                snapshot_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                page_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (snapshot_id, page_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_evidence_v1 (
                evidence_id, source_type, source_id, content_hash,
                observed_at, source_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.evidence_id,
                evidence.source_type,
                evidence.source_id,
                evidence.content_hash,
                evidence.observed_at,
                evidence.source_path,
                "2026-07-11T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO wiki_snapshots_v1 (
                snapshot_id, scope, schema_version, compiler_version,
                candidate_ids_json, created_at, published
            ) VALUES (?, ?, ?, ?, '[]', ?, 1)
            """,
            (
                "snapshot:kis:prior-schema",
                "kis",
                "jue_wiki_page_v3",
                "wiki_compiler_v1",
                "2026-07-11T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO wiki_pages_v3 (
                snapshot_id, page_id, page_json, content_hash
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "snapshot:kis:prior-schema",
                page.page_id,
                json.dumps(page.to_dict(), ensure_ascii=False, sort_keys=True),
                "b" * 64,
            ),
        )

    monkeypatch.setattr(service, "initialize", lambda: {"status": "ok"})
    before_bytes = db_path.read_bytes()
    before_mtime_ns = db_path.stat().st_mtime_ns

    payload = service.context_pack(target_scope="kis", symbols=["005930"])

    assert db_path.read_bytes() == before_bytes
    assert db_path.stat().st_mtime_ns == before_mtime_ns
    assert payload["read_mode"] == "shadow"
    assert payload["snapshot_id"] == "snapshot:kis:prior-schema"
    assert payload["coverage_status"] == "sufficient"
    assert payload["wiki_context_contract"]["selected_pages"] == (page.to_dict(),)


@pytest.mark.parametrize(
    ("configured_budget", "requested_budget", "expected_budget"),
    [
        pytest.param(1200, 1, 1, id="explicit-one"),
        pytest.param(0, None, 0, id="zero-config"),
    ],
)
def test_context_pack_tiny_legacy_budget_uses_legal_v3_probe_floor(
    tmp_path: Path,
    monkeypatch,
    configured_budget: int,
    requested_budget: int | None,
    expected_budget: int,
) -> None:
    service = _service(tmp_path, context_max_chars=configured_budget)
    service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자",
        symbols=["005930"],
        content_sections={
            "Current Stance": "current",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "tiny legacy budget",
        },
        source_refs=[],
        confidence=0.8,
        freshness="fresh",
    )
    fallback_packet = service._legacy_context_packet()
    fallback_metadata = {
        "wiki_context_contract": fallback_packet.to_dict(),
        "snapshot_id": fallback_packet.snapshot_id,
        "read_mode": fallback_packet.read_mode,
        "coverage_status": fallback_packet.coverage_status,
        "repair_required": fallback_packet.repair_required,
    }
    real_probe = service._context_pack_v3_metadata
    monkeypatch.setattr(
        service,
        "_context_pack_v3_metadata",
        lambda **_kwargs: fallback_metadata,
    )
    baseline = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        max_chars=requested_budget,
    )
    monkeypatch.setattr(service, "_context_pack_v3_metadata", real_probe)

    payload = service.context_pack(
        target_scope="kis",
        symbols=["005930"],
        max_chars=requested_budget,
    )

    assert payload == baseline
    assert payload["budget"] == expected_budget
    assert payload["char_count"] <= expected_budget
    assert payload["coverage_status"] == "legacy"
    assert payload["read_mode"] == "shadow"
    assert payload["snapshot_id"] == ""
    assert payload["wiki_context_contract"]["status"] == "legacy"
    assert payload["wiki_context_contract"]["char_count"] <= max(
        expected_budget,
        2,
    )
