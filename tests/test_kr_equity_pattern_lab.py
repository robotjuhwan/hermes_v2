from __future__ import annotations

import sqlite3
from pathlib import Path

from tradecraft.services.kr_equity_pattern_lab import (
    KREquityPatternLabConfig,
    KREquityPatternLabRepository,
    KREquityPatternLabService,
)
from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    LivePerformanceRepository,
)
from tradecraft.services.market_judgment import MarketJudgmentRepository
from tradecraft.services.trading_validation import (
    TradingValidationConfig,
    TradingValidationService,
)


def _seed_kis_live_outcomes(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        ("kr-1", "005930", 70_000, 72_100, "mid", "undervalued", "risk_on"),
        ("kr-2", "005930", 71_000, 72_420, "mid", "undervalued", "risk_on"),
        ("kr-3", "005930", 72_000, 70_920, "mid", "undervalued", "risk_off"),
        ("kr-4", "005930", 71_500, 74_360, "mid", "undervalued", "risk_on"),
        ("kr-5", "000660", 180_000, 176_400, "short", "momentum", "risk_off"),
        ("kr-6", "000660", 181_000, 177_380, "short", "momentum", "risk_off"),
    ]
    for block_id, symbol, entry, exit_price, horizon, label, regime in rows:
        metadata = {
            "horizon": horizon,
            "valuation_label": label,
            "market_regime": regime,
            "sector": "semiconductor",
            "cost_model_status": "recorded",
            "cost_components": {"fees": 20, "taxes": 30, "slippage": 10},
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=entry,
                exit_price=exit_price,
                qty=1,
                fees=20,
                taxes=30,
                slippage=10,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def test_kr_equity_pattern_lab_builds_optimized_sets_from_kis_live_alpha(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    lab_path = tmp_path / "kr_equity_pattern_lab.db"
    _seed_kis_live_outcomes(live_path)

    service = KREquityPatternLabService(
        KREquityPatternLabConfig(
            db_path=lab_path,
            live_performance_db_path=live_path,
            min_samples=3,
        )
    )

    result = service.run_once()

    assert result["status"] == "ok"
    assert result["source_scope"] == "kr_equity_pattern_lab"
    assert result["optimized_set_count"] >= 1
    assert result["active_set_count"] >= 1
    context = KREquityPatternLabRepository(lab_path).context(limit=10)
    assert context["status"] == "ok"
    assert context["optimization"]["set_count"] >= 1
    samsung = next(
        row for row in context["optimized_strategy_sets"] if row["symbol"] == "005930"
    )
    assert samsung["interval"] == "1d"
    assert samsung["family"] == "value_cycle"
    assert samsung["direction"] == "long"
    assert samsung["out_of_sample_trade_count"] >= 1
    assert samsung["walk_forward_quality"]["passed"] is True


def test_trading_validation_uses_generated_kr_equity_pattern_lab(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    lab_path = tmp_path / "kr_equity_pattern_lab.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_kis_live_outcomes(live_path)
    KREquityPatternLabService(
        KREquityPatternLabConfig(
            db_path=lab_path,
            live_performance_db_path=live_path,
            min_samples=3,
        )
    ).run_once()

    payload = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            kr_equity_pattern_lab_db_path=lab_path,
            min_sample_count=3,
            monte_carlo_iterations=64,
        )
    ).run_once(venue="kis")

    pattern_lab = payload["metrics"]["pattern_lab"]
    assert pattern_lab["status"] == "ok"
    assert pattern_lab["source_scope"] == "kr_equity_pattern_lab"
    assert pattern_lab["optimized_set_count"] >= 1
    assert any(
        row["status"] in {"pass", "warn"}
        for row in payload["disciplines"]
        if row["id"] in {
            "overfit_validation",
            "walk_forward_analysis",
            "out_of_sample_test",
        }
    )


def test_kr_equity_pattern_lab_reports_insufficient_samples_without_fake_sets(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    lab_path = tmp_path / "kr_equity_pattern_lab.db"
    _seed_kis_live_outcomes(live_path)

    result = KREquityPatternLabService(
        KREquityPatternLabConfig(
            db_path=lab_path,
            live_performance_db_path=live_path,
            min_samples=10,
        )
    ).run_once()

    assert result["status"] == "insufficient_samples"
    with sqlite3.connect(lab_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM optimized_strategy_sets").fetchone()[0]
    assert count == 0


def test_kr_equity_pattern_lab_builds_replay_sets_from_market_judgments(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    market_judge_path = tmp_path / "market_judgment.db"
    lab_path = tmp_path / "kr_equity_pattern_lab.db"
    market_repo = MarketJudgmentRepository(market_judge_path)
    for index, (run_at, entry, exit_price) in enumerate(
        [
            ("2026-06-10T00:30:00+00:00", 70_000, 72_100),
            ("2026-06-11T00:30:00+00:00", 71_000, 73_130),
            ("2026-06-12T00:30:00+00:00", 72_000, 74_880),
            ("2026-06-13T00:30:00+00:00", 72_500, 75_400),
        ],
        start=1,
    ):
        entry_quote = {
            "symbol": "005930",
            "name": "삼성전자",
            "price": entry,
            "change_pct": 1.2,
            "source": "kis",
            "status": "ok",
            "fetched_at": run_at,
        }
        market_repo.save_quotes(
            [
                entry_quote,
                {
                    **entry_quote,
                    "price": exit_price,
                    "fetched_at": run_at.replace("00:30:00", "01:30:00"),
                },
            ]
        )
        market_repo.save_judgment_run(
            run={
                "run_at": run_at,
                "market_session": "regular",
                "status": "ok",
                "mode": "llm",
                "model": "gpt-5.5",
                "query": "시장판단 리플레이",
            },
            judgments=[
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "stance": "watch",
                    "account_action": "watch_add",
                    "horizon": "mid_term",
                    "confidence": 0.72,
                    "quote": entry_quote,
                    "strategy": {
                        "valuation": {"label": "undervalued"},
                        "data_coverage": {"source_count": 3},
                    },
                    "reasons": ["저평가 눌림목"],
                }
            ],
        )

    result = KREquityPatternLabService(
        KREquityPatternLabConfig(
            db_path=lab_path,
            live_performance_db_path=live_path,
            market_judgment_db_path=market_judge_path,
            min_samples=3,
        )
    ).run_once()

    assert result["status"] == "ok"
    assert result["live_sample_count"] == 0
    assert result["replay_sample_count"] == 4
    assert result["active_set_count"] >= 1
    context = KREquityPatternLabRepository(lab_path).context(limit=10)
    samsung = context["optimized_strategy_sets"][0]
    assert samsung["symbol"] == "005930"
    assert samsung["family"] == "value_cycle"
    assert samsung["parameter_set"]["source_types"] == [
        "kis_market_judgment_replay_v1"
    ]


def test_kr_equity_pattern_lab_records_rolling_walk_forward_windows(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    lab_path = tmp_path / "kr_equity_pattern_lab.db"
    _seed_kis_live_outcomes(live_path)

    KREquityPatternLabService(
        KREquityPatternLabConfig(
            db_path=lab_path,
            live_performance_db_path=live_path,
            min_samples=3,
        )
    ).run_once()

    samsung = next(
        row
        for row in KREquityPatternLabRepository(lab_path).context(
            limit=10
        )["optimized_strategy_sets"]
        if row["symbol"] == "005930"
    )
    quality = samsung["walk_forward_quality"]
    assert quality["method"] == "chronological_60_40_split_plus_rolling_wfa_v1"
    assert quality["window_count"] >= 1
    assert quality["passed_window_count"] >= 1
    assert quality["windows"][0]["test_trade_count"] >= 1


def test_kr_equity_pattern_lab_status_summarizes_rejected_sets(
    tmp_path: Path,
) -> None:
    lab_path = tmp_path / "kr_equity_pattern_lab.db"
    repo = KREquityPatternLabRepository(lab_path)
    repo.replace_sets(
        run_payload={
            "run_id": "kr-pattern-rejected",
            "status": "ok",
            "eligible_sample_count": 6,
            "optimized_set_count": 2,
            "active_set_count": 0,
            "rejected_set_count": 2,
            "computed_at": "2026-06-14T12:00:00+00:00",
        },
        sets=[
            {
                "set_id": "kr-005930-value-rejected",
                "trial_id": "kr-005930-value-rejected-trial",
                "pattern_id": "kr-live-value_cycle",
                "symbol": "005930",
                "family": "value_cycle",
                "status": "rejected",
                "out_of_sample_trade_count": 2,
                "out_of_sample_expectancy_r": -0.015,
                "out_of_sample_profit_factor": 0.0,
                "walk_forward_quality": {
                    "passed": False,
                    "reasons": [
                        "out_of_sample_expectancy_negative",
                        "out_of_sample_profit_factor_low",
                    ],
                },
                "promoted_at": "2026-06-14T12:00:00+00:00",
            },
            {
                "set_id": "kr-017670-value-rejected",
                "trial_id": "kr-017670-value-rejected-trial",
                "pattern_id": "kr-live-value_cycle",
                "symbol": "017670",
                "family": "value_cycle",
                "status": "rejected",
                "out_of_sample_trade_count": 2,
                "out_of_sample_expectancy_r": -0.011,
                "out_of_sample_profit_factor": 0.15,
                "walk_forward_quality": {
                    "passed": False,
                    "reasons": ["out_of_sample_expectancy_negative"],
                },
                "promoted_at": "2026-06-14T12:00:01+00:00",
            },
        ],
    )

    status = repo.status()

    assert status["status"] == "ok"
    assert status["optimized_set_count"] == 0
    assert status["active_optimized_set_count"] == 0
    assert status["rejected_optimized_set_count"] == 2
    assert status["total_optimized_set_count"] == 2
    assert status["latest_optimized_set_at"] == "2026-06-14T12:00:01+00:00"
    assert status["validation_hint"]["status"] == "needs_revalidation"
    assert status["top_rejection_reasons"][0] == {
        "reason": "out_of_sample_expectancy_negative",
        "count": 2,
    }
    assert status["repair_priorities"][0]["priority"] == "active_edge_rebuild"
    assert status["repair_priorities"][1]["focus"] == "oos_expectancy"
    assert status["next_block_design_constraints"]

    context = repo.context()

    assert context["validation_hint"]["status"] == "needs_revalidation"
    assert context["top_rejection_reasons"][0]["reason"] == (
        "out_of_sample_expectancy_negative"
    )
    assert context["repair_priorities"][0]["reason"] == "no_active_kr_pattern_sets"
    assert "probe/waiting-entry" in context["next_block_design_constraints"][0]
    assert status["top_rejection_reasons"][0] == {
        "reason": "out_of_sample_expectancy_negative",
        "count": 2,
    }
    assert status["validation_hint"]["status"] == "needs_revalidation"
    assert "out_of_sample_expectancy_negative" in status["validation_hint"]["reasons"]
