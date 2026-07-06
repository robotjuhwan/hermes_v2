from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime import jue_wiki_runner
from tradecraft.runtime.jue_wiki_runner import build_service, run_once
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService


def test_jue_wiki_runner_run_once_rebuilds_and_writes_state(
    tmp_path: Path,
) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            context_max_chars=24000,
            page_max_chars=12000,
            context_page_limit=8,
            kis_blocks_db_path=tmp_path / "missing_kis_blocks.db",
            binance_blocks_db_path=tmp_path / "missing_binance_blocks.db",
        )
    )
    state_path = tmp_path / "jue_wiki_state.json"

    result = run_once(
        service=service,
        state_path=state_path,
        market_judgment_db_path=tmp_path / "missing_market_judgment.db",
    )

    assert result["status"] == "ok"
    assert result["rebuild"]["scope"] == "all"
    assert result["lint"]["scope"] == "all"
    assert state_path.exists()
    assert '"status": "ok"' in state_path.read_text(encoding="utf-8")


def test_jue_wiki_runner_build_service_wires_report_rag_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Stack:
        rag_store = object()

    monkeypatch.setattr(
        jue_wiki_runner,
        "build_report_intelligence_stack",
        lambda settings: Stack(),
        raising=False,
    )
    settings = AppSettings(
        jue_wiki_root_path=str(tmp_path / "jue_wiki"),
        jue_wiki_db_path=str(tmp_path / "jue_wiki" / "wiki.db"),
        rag_persist_path=str(tmp_path / "rag"),
    )
    settings.naver_reports_db_path = str(tmp_path / "naver_reports.db")

    service = build_service(settings)

    assert service.rag_store is Stack.rag_store
    assert service.config.naver_reports_db_path == tmp_path / "naver_reports.db"


def test_jue_wiki_runner_build_service_wires_analysis_db_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Stack:
        rag_store = object()

    monkeypatch.setattr(
        jue_wiki_runner,
        "build_report_intelligence_stack",
        lambda settings: Stack(),
        raising=False,
    )
    settings = AppSettings(
        jue_wiki_root_path=str(tmp_path / "jue_wiki"),
        jue_wiki_db_path=str(tmp_path / "jue_wiki" / "wiki.db"),
    )
    settings.market_pulse_db_path = str(tmp_path / "market_pulse.db")
    settings.etf_research_db_path = str(tmp_path / "etf_research.db")
    settings.strategy_insight_db_path = str(tmp_path / "strategy_insights.db")
    settings.valuation_db_path = str(tmp_path / "symbol_fundamentals.db")
    settings.crypto_quant_db_path = str(tmp_path / "crypto_quant.db")
    settings.crypto_pattern_lab_db_path = str(tmp_path / "crypto_pattern_lab.db")
    settings.crypto_alpha_db_path = str(tmp_path / "crypto_alpha.db")

    service = build_service(settings)

    assert service.config.market_pulse_db_path == tmp_path / "market_pulse.db"
    assert service.config.etf_research_db_path == tmp_path / "etf_research.db"
    assert service.config.strategy_insights_db_path == tmp_path / "strategy_insights.db"
    assert service.config.symbol_fundamentals_db_path == (
        tmp_path / "symbol_fundamentals.db"
    )
    assert service.config.crypto_quant_db_path == tmp_path / "crypto_quant.db"
    assert service.config.crypto_pattern_lab_db_path == tmp_path / "crypto_pattern_lab.db"
    assert service.config.crypto_alpha_db_path == tmp_path / "crypto_alpha.db"


def test_jue_wiki_runner_cycle_reports_phase2_steps(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            context_max_chars=24000,
            page_max_chars=12000,
            context_page_limit=8,
            investment_memory_db_path=tmp_path / "investment_memory.db",
            kis_blocks_db_path=tmp_path / "missing_kis_blocks.db",
            binance_blocks_db_path=tmp_path / "missing_binance_blocks.db",
        )
    )

    result = run_once(
        service=service,
        state_path=tmp_path / "jue_wiki_state.json",
        investment_memory_db_path=tmp_path / "investment_memory.db",
        performance_db_path=tmp_path / "performance.db",
    )

    assert "rebuild" in result
    assert "lint" in result
    assert "repair" in result
    assert "playbooks" in result
    assert "performance" in result


def test_jue_wiki_runner_keeps_live_outcomes_effectiveness_after_application(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    source_json = json.dumps(
        {
            "metadata": {
                "horizon": "mid",
                "entry_attribution": "external_existing_position",
                "management_attribution": "jue_block_management",
                "scorecard_attribution": "risk_management_only",
            }
        },
        ensure_ascii=False,
    )
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                gross_pnl REAL,
                cost_total REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                include_in_risk_management INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'kis', 'kis', 'kis_existing_runner_1', '005930',
                9000.0, 11000.0, 2000.0, 2.2, 0, 1, ?, ?
            )
            """,
            (source_json, "2026-07-02T06:00:00+00:00"),
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=tmp_path / "missing_kis_blocks.db",
            binance_blocks_db_path=tmp_path / "missing_binance_blocks.db",
        )
    )

    result = run_once(
        service=service,
        state_path=tmp_path / "state.json",
        performance_db_path=performance_db,
        market_judgment_db_path=tmp_path / "missing_market_judgment.db",
        effectiveness_min_samples=1,
    )

    selector_metrics = service.page_effectiveness_map(
        decision_scope="kis",
        horizons=["mid"],
    )
    metric = selector_metrics["kis.performance.live_outcomes"]
    reasons = json.loads(metric["reasons_json"])
    assert result["status"] == "ok"
    assert result["performance"]["status"] == "ok"
    assert result["performance"]["updated_count"] == 1
    assert metric["sample_count"] == 1
    assert metric["fallback_reason"] == "general_horizon_metric"
    assert "metric_source=live_block_risk_management" in reasons


def test_jue_wiki_runner_cycle_reports_phase3_application(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=tmp_path / "missing_kis_blocks.db",
            binance_blocks_db_path=tmp_path / "missing_binance_blocks.db",
        )
    )

    result = run_once(
        service=service,
        state_path=tmp_path / "state.json",
        market_judgment_db_path=tmp_path / "missing_market_judgment.db",
    )

    assert "application" in result
    assert "effectiveness" in result["application"]
    assert result["application"]["status"] in {"ok", "disabled", "error"}


def test_jue_wiki_runner_surfaces_application_warning_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class WarningApplicationService:
        def __init__(self, service: JueWikiService) -> None:
            self.service = service

        def project_decision_links(self, **_: Any) -> dict[str, Any]:
            return {"status": "ok", "inserted_count": 0}

        def backfill_decision_link_selected_wiki_pages(self) -> dict[str, Any]:
            return {"status": "ok", "updated_count": 0, "skipped_count": 0}

        def project_selection_outcomes(self, **_: Any) -> dict[str, Any]:
            return {"status": "ok", "projected_count": 0}

        def project_page_effectiveness(self, *, min_samples: int) -> dict[str, Any]:
            return {"status": "ok", "updated_count": 0, "min_samples": min_samples}

        def project_mode_recommendations(
            self,
            *,
            min_samples: int,
            current_modes: dict[str, str],
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "recommendations": [],
                "min_samples": min_samples,
                "current_modes": current_modes,
            }

        def status(self) -> dict[str, Any]:
            return {
                "status": "ok",
                "wiki_application_health": "warning",
                "wiki_application_alerts": [
                    {
                        "severity": "warning",
                        "code": "wiki_outcome_feedback_missing",
                        "decision_scope": "kis",
                    }
                ],
            }

    monkeypatch.setattr(
        jue_wiki_runner,
        "JueWikiApplicationService",
        WarningApplicationService,
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    result = run_once(service=service, state_path=tmp_path / "state.json")

    assert result["status"] == "warning"
    assert result["application"]["status"] == "warning"
    assert result["application"]["warning_count"] == 1
    assert result["application"]["wiki_application_alerts"][0]["code"] == (
        "wiki_outcome_feedback_missing"
    )


def test_jue_wiki_runner_finalizes_repairs_after_application_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events: list[str] = []

    class ApplicationService:
        def __init__(self, service: JueWikiService) -> None:
            self.service = service

        def project_decision_links(self, **_: Any) -> dict[str, Any]:
            events.append("application:decision_links")
            return {"status": "ok", "inserted_count": 0}

        def backfill_decision_link_selected_wiki_pages(self) -> dict[str, Any]:
            events.append("application:backfill")
            return {"status": "ok", "updated_count": 0, "skipped_count": 0}

        def project_selection_outcomes(self, **_: Any) -> dict[str, Any]:
            events.append("application:outcomes")
            return {"status": "ok", "projected_count": 0}

        def project_page_effectiveness(self, *, min_samples: int) -> dict[str, Any]:
            events.append("application:effectiveness")
            return {"status": "ok", "updated_count": 0, "min_samples": min_samples}

        def project_mode_recommendations(
            self,
            *,
            min_samples: int,
            current_modes: dict[str, str],
        ) -> dict[str, Any]:
            events.append("application:mode")
            return {"status": "ok", "recommendations": []}

        def status(self) -> dict[str, Any]:
            events.append("application:status")
            return {"status": "ok", "wiki_application_health": "ok"}

    monkeypatch.setattr(
        jue_wiki_runner,
        "JueWikiApplicationService",
        ApplicationService,
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    def repair_once(*, scope: str | None = None) -> dict[str, Any]:
        events.append("repair")
        return {"status": "ok", "actions": [], "scope": scope}

    monkeypatch.setattr(service, "repair_once", repair_once)

    result = run_once(service=service, state_path=tmp_path / "state.json")

    assert result["repair"]["status"] == "ok"
    assert result["repair_finalization"]["status"] == "ok"
    assert events == [
        "repair",
        "application:decision_links",
        "application:backfill",
        "application:outcomes",
        "application:effectiveness",
        "application:mode",
        "application:status",
        "repair",
    ]


def test_jue_wiki_runner_runs_decision_link_backfill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class BackfillApplicationService:
        def __init__(self, service: JueWikiService) -> None:
            self.service = service

        def project_decision_links(self, **_: Any) -> dict[str, Any]:
            return {"status": "ok", "inserted_count": 0}

        def backfill_decision_link_selected_wiki_pages(self) -> dict[str, Any]:
            return {"status": "ok", "updated_count": 7, "skipped_count": 1}

        def project_selection_outcomes(self, **_: Any) -> dict[str, Any]:
            return {"status": "ok", "projected_count": 0}

        def project_page_effectiveness(self, *, min_samples: int) -> dict[str, Any]:
            return {"status": "ok", "updated_count": 0, "min_samples": min_samples}

        def project_mode_recommendations(
            self,
            *,
            min_samples: int,
            current_modes: dict[str, str],
        ) -> dict[str, Any]:
            return {"status": "ok", "recommendations": []}

        def status(self) -> dict[str, Any]:
            return {"status": "ok", "wiki_application_health": "ok"}

    monkeypatch.setattr(
        jue_wiki_runner,
        "JueWikiApplicationService",
        BackfillApplicationService,
    )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            kis_blocks_db_path=tmp_path / "missing_kis_blocks.db",
            binance_blocks_db_path=tmp_path / "missing_binance_blocks.db",
        )
    )

    result = run_once(
        service=service,
        state_path=tmp_path / "state.json",
        market_judgment_db_path=tmp_path / "missing_market_judgment.db",
    )

    assert result["application"]["decision_link_backfill"] == {
        "status": "ok",
        "updated_count": 7,
        "skipped_count": 1,
    }


def test_jue_wiki_runner_projects_market_judgment_decision_links(
    tmp_path: Path,
) -> None:
    market_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(market_db) as conn:
        conn.execute(
            """
            CREATE TABLE judgment_runs (
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
            INSERT INTO judgment_runs (run_at, status, mode, model, prompt_json)
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
                            "selection_run_id": "selection:market-1",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.regime.opening"],
                        }
                    }
                ),
            ),
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    run_once(
        service=service,
        state_path=tmp_path / "state.json",
        market_judgment_db_path=market_db,
    )

    with sqlite3.connect(service.config.db_path) as conn:
        row = conn.execute(
            """
            SELECT selection_run_id, decision_type
            FROM wiki_decision_links
            WHERE selection_run_id = ?
            """,
            ("selection:market-1",),
        ).fetchone()

    assert row == ("selection:market-1", "market_judgment")


def test_jue_wiki_runner_projects_market_judgment_contract_error_outcomes(
    tmp_path: Path,
) -> None:
    market_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(market_db) as conn:
        conn.execute(
            """
            CREATE TABLE judgment_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                prompt_json TEXT NOT NULL DEFAULT '{}',
                response_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO judgment_runs (
                run_at, status, mode, model, error_message, prompt_json,
                response_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-28T01:00:00+00:00",
                "error",
                "error",
                "gpt-5.5",
                "validation_repair_resolution_missing_from_model",
                json.dumps(
                    {
                        "jue_wiki_application": {
                            "selection_run_id": "selection:market-contract-runner",
                            "prompt_mode": "assist",
                            "selected_page_ids": ["kis.regime.contract"],
                        },
                        "jue_wiki_validation_repair_contract": {
                            "status": "repair_required",
                            "requires_validation_repair_resolution": True,
                        },
                        "execution_gate": {
                            "status": "ok",
                            "kill_switch": {"enabled": False},
                        },
                    }
                ),
                json.dumps({}),
            ),
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    result = run_once(
        service=service,
        state_path=tmp_path / "state.json",
        market_judgment_db_path=market_db,
    )

    with sqlite3.connect(service.config.db_path) as conn:
        row = conn.execute(
            """
            SELECT outcome_kind, outcome_status, return_pct, evidence_json
            FROM wiki_selection_outcomes
            WHERE selection_run_id = ?
            """,
            ("selection:market-contract-runner",),
        ).fetchone()

    assert result["application"]["outcomes"]["projected_count"] == 1
    assert row is not None
    assert row[0] == "manager_contract_error"
    assert row[1] == "loss"
    assert row[2] == -0.12
    evidence = json.loads(row[3])
    assert evidence["source"] == "kis_market_judgment_contract_error"
