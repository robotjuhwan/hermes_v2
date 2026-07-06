from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from tradecraft.runtime.live_evaluator_runner import (
    build_live_authority_payload,
    _binance_block_fill_evidence,
    _binance_validation_costs,
    _compact_live_evaluator_state,
    _enriched_kis_validation_metadata,
    _kis_validation_costs,
    _latest_order_price,
    _pending_active_revision_block_evidence,
    _repair_open_block_strategy_revision_metadata,
    _execute_validation_repair_manifest,
    _ingest_trading_validation_memory_signals,
    _ingest_validation_repair_execution_memory,
    _outcomes_from_performance,
    _latest_kis_quote_context,
    _refresh_live_edge_scorecards,
    refresh_trading_validation,
    run_live_evaluator_once,
)
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    LivePerformanceRepository,
)
from tradecraft.services.live_edge import LiveEdgeRepository
from tradecraft.services.trading_validation import (
    TradingValidationConfig,
    TradingValidationRepository,
    TradingValidationService,
)


def test_outcomes_from_performance_splits_binance_volatile_attack_lane() -> None:
    rows = [
        {
            "venue": "binance",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": 1.2,
            "gross_pnl": 2.0,
            "cost_total": 0.8,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "market": "futures",
                        "side": "long",
                        "horizon": "futures",
                        "lane": "volatile_attack",
                        "strategy_family": "squeeze_breakout",
                        "r_multiple": 1.5,
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="binance")

    assert ("futures:volatile_attack", "squeeze_breakout") in grouped
    assert ("futures:futures", "squeeze_breakout") not in grouped
    outcome = grouped[("futures:volatile_attack", "squeeze_breakout")][0]
    assert outcome.gross_pnl == 2.0
    assert outcome.cost_total == 0.8


def test_outcomes_from_performance_names_spot_short_horizon_as_long_side() -> None:
    rows = [
        {
            "venue": "binance",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": -0.8,
            "gross_pnl": -0.4,
            "cost_total": 0.05,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "market": "spot",
                        "side": "long",
                        "horizon": "short",
                        "lane": "short",
                        "r_multiple": -1.0,
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="binance")

    assert ("spot:long:short", "short") in grouped
    assert ("spot:short", "short") not in grouped
    outcome = grouped[("spot:long:short", "short")][0]
    assert outcome.strategy_family == "spot:long:short"


def test_outcomes_from_performance_carries_cost_precision_into_edge_outcome() -> None:
    rows = [
        {
            "venue": "binance",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": 1.0,
            "gross_pnl": 1.8,
            "cost_total": 0.4,
            "cost_precision": "estimated_from_notional",
            "fill_evidence_status": "order_round_trip_filled",
            "source_json": json.dumps(
                {
                    "metadata": {
                        "market": "futures",
                        "side": "long",
                        "strategy_family": "trend_breakout",
                        "r_multiple": 0.8,
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="binance")

    outcome = grouped[("futures:long", "trend_breakout")][0]
    assert outcome.cost_precision == "estimated_from_notional"
    assert outcome.fill_evidence_status == "order_round_trip_filled"


def test_binance_validation_costs_preserve_reflection_cost_source() -> None:
    class Settings:
        binance_validation_futures_fee_rate = 0.0005
        binance_validation_spot_fee_rate = 0.001
        binance_validation_slippage_bps = 2.0

    recorded = _binance_validation_costs(
        block={"market": "futures"},
        metadata={},
        entry_price=100.0,
        exit_price=105.0,
        qty=0.2,
        reflection_fee=0.12,
        reflection_funding=0.03,
        reflection_slippage=0.02,
        reflection_spread=0.01,
        reflection_cost_source="explicit",
        settings=Settings(),
    )
    assert recorded["status"] == "recorded"
    assert recorded["source"] == "explicit"
    assert recorded["fees"] == pytest.approx(0.12)
    assert recorded["funding"] == pytest.approx(0.03)
    assert recorded["slippage"] == pytest.approx(0.02)
    assert recorded["spread"] == pytest.approx(0.01)

    partial = _binance_validation_costs(
        block={"market": "futures"},
        metadata={},
        entry_price=100.0,
        exit_price=105.0,
        qty=0.2,
        reflection_fee=0.12,
        reflection_funding=0.0,
        reflection_slippage=0.0,
        reflection_spread=0.0,
        reflection_cost_source="partial_unconverted_fee",
        settings=Settings(),
    )
    assert partial["status"] == "partial_unconverted_fee"
    assert partial["source"] == "partial_unconverted_fee"


def test_live_performance_keeps_partial_cost_precision_out_of_recorded(
    tmp_path: Path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="partial-cost",
            symbol="ALTUSDT",
            created_by="llm",
            status="closed",
            entry_price=10.0,
            exit_price=11.0,
            qty=1.0,
            fees=0.2,
            filled=True,
            metadata={
                "cost_model_status": "partial_unconverted_fee",
                "cost_source": "partial_unconverted_fee",
            },
        ),
        source={},
    )

    row = repo.latest(venue="binance", limit=1)[0]
    assert row["cost_precision"] == "partial"


def test_outcomes_from_performance_carries_entry_quality_into_edge_outcome() -> None:
    rows = [
        {
            "venue": "kis",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": -0.5,
            "gross_pnl": -300.0,
            "cost_total": 40.0,
            "entry_quality_score": 35.0,
            "entry_quality_label": "extended_momentum",
            "source_json": json.dumps(
                {
                    "metadata": {
                        "horizon": "short",
                        "entry_setup": "late_chase",
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="kis")

    outcome = grouped[("short", "late_chase")][0]
    assert outcome.entry_quality_score == 35.0
    assert outcome.entry_quality_label == "extended_momentum"


def test_outcomes_from_performance_carries_strategy_revision_id() -> None:
    rows = [
        {
            "venue": "kis",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": 0.7,
            "gross_pnl": 2100.0,
            "cost_total": 40.0,
            "strategy_revision_id": "jue_edge_repair_v2",
            "source_json": json.dumps(
                {
                    "metadata": {
                        "horizon": "mid",
                        "entry_setup": "value_pullback",
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="kis")

    outcome = grouped[("mid", "value_pullback")][0]
    assert outcome.strategy_revision_id == "jue_edge_repair_v2"


def test_repairs_open_block_strategy_revision_without_rewriting_closed_blocks(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "blocks.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE block_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO blocks (
                block_id, status, created_by, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "open-missing-revision",
                    "open",
                    "llm",
                    json.dumps({"horizon": "mid"}),
                    "2026-06-17T00:00:00+00:00",
                    "2026-06-17T00:00:00+00:00",
                ),
                (
                    "closed-legacy",
                    "closed",
                    "llm",
                    json.dumps({"horizon": "mid"}),
                    "2026-06-16T00:00:00+00:00",
                    "2026-06-16T01:00:00+00:00",
                ),
                (
                    "open-already-tagged",
                    "open",
                    "llm",
                    json.dumps({"strategy_revision_id": "existing_rev"}),
                    "2026-06-17T00:00:00+00:00",
                    "2026-06-17T00:00:00+00:00",
                ),
            ],
        )

    result = _repair_open_block_strategy_revision_metadata(
        db_path,
        strategy_revision_id="jue_edge_repair_v2",
        venue="kis",
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            row["block_id"]: json.loads(row["metadata_json"])
            for row in conn.execute("SELECT block_id, metadata_json FROM blocks")
        }
        event_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM block_events
            WHERE event_type = 'strategy_revision_repaired'
            """
        ).fetchone()[0]

    assert result["updated_count"] == 1
    assert rows["open-missing-revision"]["strategy_revision_id"] == (
        "jue_edge_repair_v2"
    )
    assert rows["open-missing-revision"]["strategy_revision_source"] == (
        "live_evaluator_open_block_metadata_repair"
    )
    assert "strategy_revision_id" not in rows["closed-legacy"]
    assert rows["open-already-tagged"]["strategy_revision_id"] == "existing_rev"
    assert event_count == 1


def test_live_authority_surfaces_pending_active_revision_blocks(
    tmp_path: Path,
) -> None:
    block_db = tmp_path / "kis_blocks.db"
    validation_repo = TradingValidationRepository(tmp_path / "trading_validation.db")
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "pending-active-validation",
            "venue": "kis",
            "scope": "live",
            "strategy_revision_id": "jue_edge_repair_v2",
            "computed_at": "2026-06-17T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 7.89,
                "readiness": "blocked_by_validation",
                "pass_count": 0,
                "warn_count": 3,
                "fail_count": 0,
                "missing_count": 16,
                "core_fail_count": 0,
                "core_missing_count": 3,
                "hard_fail_count": 0,
                "hard_missing_count": 3,
                "hard_blocking_count": 3,
            },
        }
    )
    with sqlite3.connect(block_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO blocks (
                block_id, symbol, status, created_by, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "pending-mid",
                    "005930",
                    "open",
                    "llm",
                    json.dumps(
                        {
                            "strategy_revision_id": "jue_edge_repair_v2",
                            "horizon": "mid",
                        }
                    ),
                    "2026-06-17T00:00:00+00:00",
                    "2026-06-17T00:10:00+00:00",
                ),
                (
                    "pending-etf",
                    "069500",
                    "proposed",
                    "llm",
                    json.dumps(
                        {
                            "strategy_revision_id": "jue_edge_repair_v2",
                            "horizon": "core_etf",
                            "name": "KODEX 200",
                        }
                    ),
                    "2026-06-17T00:00:00+00:00",
                    "2026-06-17T00:11:00+00:00",
                ),
                (
                    "closed-ignored",
                    "005930",
                    "closed",
                    "llm",
                    json.dumps({"strategy_revision_id": "jue_edge_repair_v2"}),
                    "2026-06-16T00:00:00+00:00",
                    "2026-06-16T01:00:00+00:00",
                ),
                (
                    "wallet-ignored",
                    "005930",
                    "open",
                    "wallet_adoption",
                    json.dumps({"strategy_revision_id": "jue_edge_repair_v2"}),
                    "2026-06-17T00:00:00+00:00",
                    "2026-06-17T00:12:00+00:00",
                ),
            ],
        )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000
        kis_block_trader_db_path = str(block_db)
        binance_block_trader_db_path = str(tmp_path / "missing_binance.db")
        jue_strategy_revision_id = "jue_edge_repair_v2"

    pending = _pending_active_revision_block_evidence(
        block_db,
        strategy_revision_id="jue_edge_repair_v2",
        venue="kis",
    )
    payload = build_live_authority_payload(Settings())
    evidence = payload["venues"]["kis"]["active_revision_evidence"]

    assert pending["pending_block_count"] == 2
    assert pending["pending_block_status_counts"] == {"open": 1, "proposed": 1}
    assert pending["pending_block_lane_counts"] == {"core_etf": 1, "mid": 1}
    assert evidence["status"] == "active_revision_samples_pending_close"
    assert evidence["authority_posture"] == "small_probe_until_pending_blocks_close"
    assert evidence["pending_block_count"] == 2
    assert evidence["pending_block_lane_counts"] == {"core_etf": 1, "mid": 1}
    assert evidence["effective_sample_count"] == 0
    assert evidence["scale_up_allowed"] is False


def test_live_authority_evidence_keeps_legacy_proxy_out_of_active_counts(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    block_db = tmp_path / "kis_blocks.db"
    performance_repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 98.0, 107.0), start=1):
        metadata = {"strategy_revision_id": "legacy_rev", "horizon": "mid"}
        performance_repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"legacy-kis-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            strategy_revision_id="jue_edge_repair_v2",
            min_sample_count=5,
            monte_carlo_iterations=16,
        )
    ).run_once(venue="kis")
    with sqlite3.connect(block_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, status, created_by, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pending-active-mid",
                "005930",
                "open",
                "llm",
                json.dumps(
                    {
                        "strategy_revision_id": "jue_edge_repair_v2",
                        "horizon": "mid",
                    }
                ),
                "2026-06-17T00:00:00+00:00",
                "2026-06-17T00:10:00+00:00",
            ),
        )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(live_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 5
        trading_validation_db_path = str(validation_path)
        trading_validation_max_age_sec = 864000000
        kis_block_trader_db_path = str(block_db)
        binance_block_trader_db_path = str(tmp_path / "missing_binance.db")
        jue_strategy_revision_id = "jue_edge_repair_v2"

    payload = build_live_authority_payload(Settings())
    evidence = payload["venues"]["kis"]["active_revision_evidence"]

    assert evidence["status"] == "active_revision_samples_pending_close_with_proxy"
    assert evidence["validation_sample_role"] == "legacy_proxy_metrics_no_scale"
    assert evidence["legacy_proxy_gate_mode"] == "probe_only"
    assert evidence["active_sample_count"] == 0
    assert evidence["validation_sample_count"] == 0
    assert evidence["effective_sample_count"] == 0
    assert evidence["legacy_proxy_sample_count"] == 3
    assert evidence["pending_block_count"] == 1
    assert evidence["pending_block_lane_counts"] == {"mid": 1}
    assert evidence["can_scale_from_proxy"] is False
    assert evidence["scale_up_allowed"] is False
    assert "cost_simulation" in evidence["legacy_proxy_failed_discipline_ids"]


def test_pending_active_revision_binance_prefers_row_market_side_over_generic_lane(
    tmp_path: Path,
) -> None:
    block_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(block_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT 'spot',
                side TEXT NOT NULL DEFAULT 'long',
                status TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, market, side, status, created_by,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "spot-open-with-stale-short-lane",
                "BNBUSDT",
                "spot",
                "long",
                "open",
                "llm",
                json.dumps(
                    {
                        "strategy_revision_id": "jue_edge_repair_v2",
                        "horizon": "futures",
                        "lane": "short",
                    }
                ),
                "2026-06-17T00:00:00+00:00",
                "2026-06-17T00:10:00+00:00",
            ),
        )

    pending = _pending_active_revision_block_evidence(
        block_db,
        strategy_revision_id="jue_edge_repair_v2",
        venue="binance",
    )

    assert pending["pending_block_count"] == 1
    assert pending["pending_block_lane_counts"] == {"spot:long": 1}
    assert pending["sample_blocks"][0]["lane"] == "spot:long"


def test_outcomes_from_performance_carries_scale_validation_evidence() -> None:
    rows = [
        {
            "venue": "binance",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": 0.7,
            "gross_pnl": 2.1,
            "cost_total": 0.2,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "market": "futures",
                        "side": "long",
                        "strategy_family": "ema_trend",
                        "pattern_inputs": {
                            "prior": {
                                "trade_count": 18,
                                "expectancy_r": 0.08,
                                "profit_factor": 1.4,
                                "walk_forward_quality": {
                                    "passed": True,
                                    "window_count": 4,
                                    "passed_window_count": 3,
                                    "pass_rate_pct": 75.0,
                                },
                                "out_of_sample_trade_count": 9,
                                "out_of_sample_expectancy_r": 0.05,
                                "out_of_sample_profit_factor": 1.2,
                            }
                        },
                        "validation_evidence": {
                            "live_shadow_passed": True,
                        },
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="binance")

    outcome = grouped[("futures:long", "ema_trend")][0]
    assert outcome.backtest_passed is True
    assert outcome.walk_forward_passed is True
    assert outcome.out_of_sample_passed is True
    assert outcome.live_shadow_passed is True


def test_outcomes_from_performance_uses_binance_volatile_attack_flag() -> None:
    rows = [
        {
            "venue": "binance",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": -0.9,
            "gross_pnl": -1.8,
            "cost_total": 0.4,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "market": "futures",
                        "side": "short",
                        "horizon": "futures",
                        "volatile_attack": True,
                        "entry_setup": "wick_reversal",
                        "r_multiple": -0.7,
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="binance")

    assert ("futures:volatile_attack", "wick_reversal") in grouped
    assert ("futures:short", "wick_reversal") not in grouped


def test_outcomes_from_performance_splits_kis_etf_from_horizon_lane() -> None:
    rows = [
        {
            "venue": "kis",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": 0.8,
            "gross_pnl": 2400.0,
            "cost_total": 40.0,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "horizon": "mid",
                        "asset_type": "ETF",
                        "name": "KODEX 200",
                        "strategy_family": "index_pullback",
                        "r_multiple": 0.7,
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="kis")

    assert ("core_etf", "index_pullback") in grouped
    assert ("mid", "index_pullback") not in grouped
    outcome = grouped[("core_etf", "index_pullback")][0]
    assert outcome.gross_pnl == 2400.0
    assert outcome.cost_total == 40.0


def test_outcomes_from_performance_uses_kis_is_etf_boolean_hint() -> None:
    rows = [
        {
            "venue": "kis",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": 0.4,
            "gross_pnl": 1200.0,
            "cost_total": 35.0,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "horizon": "mid",
                        "is_etf": True,
                        "entry_setup": "discount_rebalance",
                        "r_multiple": 0.5,
                    }
                }
            ),
        }
    ]

    grouped = _outcomes_from_performance(rows, venue="kis")

    assert ("core_etf", "discount_rebalance") in grouped
    assert ("mid", "discount_rebalance") not in grouped


def test_outcomes_from_performance_uses_entry_setup_when_strategy_family_missing() -> None:
    rows = [
        {
            "venue": "kis",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": -0.6,
            "gross_pnl": -1800.0,
            "cost_total": 80.0,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "horizon": "short",
                        "entry_setup": "late_chase",
                        "r_multiple": -0.8,
                    }
                }
            ),
        },
        {
            "venue": "binance",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": 1.2,
            "gross_pnl": 4.0,
            "cost_total": 0.6,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "market": "futures",
                        "side": "long",
                        "horizon": "futures",
                        "setup": "pullback_reclaim",
                        "r_multiple": 1.1,
                    }
                }
            ),
        },
    ]

    kis_grouped = _outcomes_from_performance(rows, venue="kis")
    binance_grouped = _outcomes_from_performance(rows, venue="binance")

    assert ("short", "late_chase") in kis_grouped
    assert ("short", "all") not in kis_grouped
    assert ("futures:long", "pullback_reclaim") in binance_grouped
    assert ("futures:long", "all") not in binance_grouped


def test_outcomes_from_performance_adds_validation_repair_scorecard_groups() -> None:
    rows = [
        {
            "venue": "kis",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": -0.4,
            "gross_pnl": -1200.0,
            "cost_total": 90.0,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "horizon": "short",
                        "entry_setup": "late_chase",
                        "r_multiple": -0.5,
                        "validation_repair": {
                            "discipline_ids": ["cost_simulation"],
                            "entry_biases": ["cost_verified_waiting_entry"],
                        },
                    }
                }
            ),
        },
        {
            "venue": "binance",
            "attribution": "jue_created_live_or_paper",
            "include_in_jue_alpha": 1,
            "pnl_pct": -0.7,
            "gross_pnl": -2.0,
            "cost_total": 0.3,
            "source_json": json.dumps(
                {
                    "metadata": {
                        "market": "futures",
                        "side": "long",
                        "entry_setup": "breakout",
                        "r_multiple": -0.8,
                        "validation_repair": {
                            "block_design_constraints": [
                                {"discipline_id": "correlation"}
                            ],
                        },
                    }
                }
            ),
        },
    ]

    kis_grouped = _outcomes_from_performance(rows, venue="kis")
    binance_grouped = _outcomes_from_performance(rows, venue="binance")

    assert ("short", "late_chase") in kis_grouped
    assert ("short:validation:cost_simulation", "all") in kis_grouped
    assert (
        kis_grouped[("short:validation:cost_simulation", "all")][0].net_pnl_pct
        == pytest.approx(-0.4)
    )
    assert ("futures:long", "breakout") in binance_grouped
    assert ("futures:long:validation:correlation", "all") in binance_grouped
    assert (
        binance_grouped[("futures:long:validation:correlation", "all")][0].cost_total
        == pytest.approx(0.3)
    )


def test_live_evaluator_ingests_validation_signals_into_memory(tmp_path: Path) -> None:
    class Settings:
        investment_memory_enabled = True
        investment_memory_root_path = str(tmp_path / "memory")
        investment_memory_db_path = str(tmp_path / "memory.db")
        investment_memory_policy_mode = "soft_auto"
        live_evaluator_db_path = str(tmp_path / "empty_live_edge.db")
        jue_strategy_revision_id = "test_revision"

    validation = {
        "kis": {
            "status": "ok",
            "run_id": "validation-kis-run-1",
            "venue": "kis",
            "computed_at": "2026-06-17T00:00:00+00:00",
            "disciplines": [
                {
                    "id": "data_validation",
                    "label": "데이터 검증",
                    "status": "fail",
                    "action": "quote/fill evidence repair",
                }
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 18,
                "fail_count": 1,
                "warn_count": 0,
                "missing_count": 0,
            },
            "remediation_plan": {
                "work_queue": [
                    {
                        "discipline_id": "data_validation",
                        "status": "fail",
                        "priority": "p0",
                        "owner": "data_pipeline",
                        "cadence": "before_next_manager_run",
                        "lane_policy_hint": "quote_verified_only",
                        "blocks_scaling": "no_scale_up_until_data_clean",
                        "blocks_new_entries": (
                            "scale_up_and_unverified_immediate_entries"
                        ),
                        "runner_hint": (
                            "sync_live_performance_and_edges -> "
                            "refresh_trading_validation"
                        ),
                        "verification_artifact": (
                            "data_quality shows invalid_price_count=0"
                        ),
                        "exit_criteria": "data_validation returns to pass",
                        "validation_mode": "data_repair_before_trade",
                        "allowed_entry_posture": "verified_quote_waiting_entry",
                        "scale_up_blocked": True,
                        "evidence_targets": {
                            "max_invalid_price_count": 0,
                            "max_upstream_error_count": 0,
                        },
                    }
                ]
            },
        },
        "binance": {
            "status": "ok",
            "run_id": "validation-binance-run-1",
            "venue": "binance",
            "computed_at": "2026-06-17T00:00:00+00:00",
            "disciplines": [
                {
                    "id": "cost_simulation",
                    "label": "거래비용 시뮬레이션",
                    "status": "fail",
                    "action": "fee/slippage/funding repair",
                }
            ],
            "summary": {
                "readiness": "normal",
                "pass_count": 18,
                "fail_count": 1,
                "warn_count": 0,
                "missing_count": 0,
            },
            "remediation_plan": {
                "work_queue": [
                    {
                        "discipline_id": "cost_simulation",
                        "status": "fail",
                        "priority": "p0",
                        "owner": "cost_model",
                        "cadence": "before_next_manager_run",
                        "lane_policy_hint": "cost_verified_waiting_entry",
                        "blocks_scaling": "reduce_cost_weak_lanes",
                        "blocks_new_entries": "cost_weak_immediate_entries",
                        "runner_hint": (
                            "sync precise fills/costs -> "
                            "refresh_trading_validation"
                        ),
                        "verification_artifact": (
                            "recorded fee/tax/spread/slippage/funding components"
                        ),
                        "exit_criteria": "cost_simulation returns to pass",
                        "validation_mode": "cost_evidence_repair",
                        "allowed_entry_posture": "cost_verified_waiting_entry",
                        "scale_up_blocked": True,
                        "evidence_targets": {
                            "min_recorded_cost_coverage_pct": 60.0,
                            "min_cost_stress_net_pnl_multiplier": "2x_positive",
                        },
                    }
                ]
            },
        },
    }

    result = _ingest_trading_validation_memory_signals(Settings(), validation)
    duplicate = _ingest_trading_validation_memory_signals(Settings(), validation)
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=Settings.investment_memory_root_path,
            db_path=Settings.investment_memory_db_path,
        )
    )
    scorecards = {
        row["policy_id"]: row
        for row in memory.policy_scorecards(limit=20)["items"]
    }

    assert result["status"] == "ok"
    assert result["venues"]["kis"]["processed_count"] == 1
    assert result["venues"]["binance"]["processed_count"] == 1
    assert result["repair_backlog"]["status"] == "needs_repair"
    assert result["repair_backlog"]["total_item_count"] == 2
    assert (
        result["repair_backlog"]["venues"]["kis"]["items"][0]["discipline_id"]
        == "data_validation"
    )
    assert (
        result["repair_backlog"]["venues"]["binance"]["items"][0]["discipline_id"]
        == "cost_simulation"
    )
    assert {
        row["discipline_id"]
        for row in result["repair_backlog"]["primary_items"]
    } == {"data_validation", "cost_simulation"}
    manifest = result["repair_manifest"]
    assert manifest["status"] == "needs_repair"
    assert manifest["item_count"] == 2
    assert manifest["scale_up_blocked_count"] == 2
    assert manifest["m1_execution_posture"] == "sequential_priority_queue"
    assert manifest["next_cadences"] == ["before_next_manager_run"]
    by_discipline = {
        row["discipline_id"]: row
        for row in manifest["queues"]["before_next_manager_run"]
    }
    assert by_discipline["data_validation"]["validation_mode"] == (
        "data_repair_before_trade"
    )
    assert by_discipline["data_validation"]["allowed_entry_posture"] == (
        "verified_quote_waiting_entry"
    )
    assert by_discipline["data_validation"]["scale_up_blocked"] is True
    assert by_discipline["data_validation"]["evidence_targets"][
        "max_invalid_price_count"
    ] == 0
    assert by_discipline["cost_simulation"]["validation_mode"] == (
        "cost_evidence_repair"
    )
    assert by_discipline["cost_simulation"]["allowed_entry_posture"] == (
        "cost_verified_waiting_entry"
    )
    assert by_discipline["cost_simulation"]["evidence_targets"][
        "min_recorded_cost_coverage_pct"
    ] == 60.0
    assert duplicate["venues"]["kis"]["reason"] == (
        "validation_run_already_ingested"
    )
    assert scorecards["validation.kis.data_validation"]["memory_scope"] == "kis"
    assert scorecards["validation.binance.cost_simulation"]["memory_scope"] == (
        "binance"
    )


def test_live_evaluator_queues_live_edge_validation_evidence_repair_when_memory_off(
    tmp_path: Path,
) -> None:
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    edge_repo.upsert_scorecard(
        venue="binance",
        strategy_family="futures:long",
        evidence_key="ema_trend",
        scorecard={
            "sample_count": 14,
            "expectancy_pct": 0.6,
            "win_rate": 57.0,
            "profit_factor": 1.8,
            "grade": "qualified",
            "authority_multiplier": 1.0,
            "strategy_revision_id": "jue_edge_repair_v2",
            "validation_evidence_status": "missing",
            "validation_missing_dimensions": [
                "backtest",
                "walk_forward",
                "out_of_sample",
                "live_shadow",
            ],
            "scale_blocked_by_validation_evidence": True,
        },
    )

    class Settings:
        investment_memory_enabled = False
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        jue_strategy_revision_id = "jue_edge_repair_v2"

    result = _ingest_trading_validation_memory_signals(Settings(), {})

    manifest = result["repair_manifest"]
    item = manifest["items"][0]
    assert result["status"] == "skipped"
    assert result["repair_backlog"]["status"] == "needs_repair"
    assert manifest["status"] == "needs_repair"
    assert item["venue"] == "binance"
    assert item["discipline_id"] == "lane_validation_evidence"
    assert item["validation_mode"] == "backtest_wfa_oos_rebuild"
    assert item["allowed_entry_posture"] == "shadow_or_waiting_entry_only"
    assert item["scale_up_blocked"] is True
    assert item["live_shadow_required"] is True
    assert item["evidence_targets"]["target_lanes"] == ["futures:long:ema_trend"]
    assert "walk_forward" in item["evidence_targets"]["missing_dimensions"]


def test_validation_repair_execution_classifies_lightweight_actions(
    tmp_path: Path,
) -> None:
    manifest = {
        "items": [
            {
                "venue": "kis",
                "discipline_id": "data_validation",
                "repair_action_id": (
                    "validation_repair.data_repair_before_trade.data_validation"
                ),
                "priority": "p0",
                "status": "fail",
                "automation_hook": "sync_live_performance_and_edges",
                "execution_weight": "lightweight",
                "validation_mode": "data_repair_before_trade",
                "scale_up_blocked": True,
            },
            {
                "venue": "kis",
                "discipline_id": "walk_forward_analysis",
                "repair_action_id": (
                    "validation_repair.backtest_wfa_oos_rebuild."
                    "walk_forward_analysis"
                ),
                "priority": "p0",
                "status": "fail",
                "automation_hook": "pattern_lab_rebuild_wfa_oos",
                "execution_weight": "external_runner",
                "validation_mode": "backtest_wfa_oos_rebuild",
                "scale_up_blocked": True,
                "live_shadow_required": True,
                "evidence_targets": {"min_active_strategy_sets": 1},
            },
            {
                "venue": "binance",
                "discipline_id": "walk_forward_analysis",
                "priority": "p0",
                "status": "fail",
                "validation_mode": "backtest_wfa_oos_rebuild",
                "scale_up_blocked": True,
                "live_shadow_required": True,
                "evidence_targets": {"min_active_strategy_sets": 1},
            },
            {
                "venue": "binance",
                "discipline_id": "kelly_sizing",
                "priority": "p1",
                "status": "warn",
                "validation_mode": "risk_budget_recalibration",
            },
        ]
    }

    class Settings:
        crypto_pattern_lab_state_path = str(tmp_path / "missing_crypto_lab.json")
        live_evaluator_repair_execution_max_items = 8

    result = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok", "synced_blocks": {"kis": 2}},
        validation={
            "kr_equity_pattern_lab": {
                "status": "ok",
                "pattern_count": 3,
                "backtest_count": 9,
                "active_optimized_set_count": 1,
            },
            "binance": {
                "summary": {
                    "readiness": "probe",
                    "fail_count": 0,
                    "warn_count": 2,
                },
                "disciplines": [
                    {
                        "id": "kelly_sizing",
                        "status": "warn",
                        "metric": {
                            "risk_of_ruin_pct": 2.0,
                            "profit_factor": 1.2,
                            "recovery_factor": 1.1,
                            "recommended_risk_fraction": 0.005,
                        },
                    }
                ],
            },
        },
    )

    by_key = {
        (row["venue"], row["discipline_id"]): row
        for row in result["actions"]
    }

    assert result["status"] == "queued"
    assert result["executed_count"] == 3
    assert result["queued_count"] == 1
    assert result["m1_execution_posture"] == "sequential_priority_queue"
    assert by_key[("kis", "data_validation")]["status"] == "executed"
    assert by_key[("kis", "data_validation")]["repair_action_id"] == (
        "validation_repair.data_repair_before_trade.data_validation"
    )
    assert by_key[("kis", "data_validation")]["automation_hook"] == (
        "sync_live_performance_and_edges"
    )
    assert by_key[("kis", "data_validation")]["execution_weight"] == "lightweight"
    assert by_key[("kis", "data_validation")]["artifact"] == (
        "sync_live_performance_and_edges"
    )
    assert by_key[("kis", "walk_forward_analysis")]["status"] == "executed"
    assert by_key[("kis", "walk_forward_analysis")]["repair_action_id"] == (
        "validation_repair.backtest_wfa_oos_rebuild.walk_forward_analysis"
    )
    assert by_key[("kis", "walk_forward_analysis")]["automation_hook"] == (
        "pattern_lab_rebuild_wfa_oos"
    )
    assert by_key[("kis", "walk_forward_analysis")]["execution_weight"] == (
        "external_runner"
    )
    assert by_key[("kis", "walk_forward_analysis")]["artifact"] == (
        "kr_equity_pattern_lab"
    )
    assert by_key[("kis", "walk_forward_analysis")]["backtest_count"] == 9
    assert by_key[("kis", "walk_forward_analysis")]["evidence_status"] == (
        "passed"
    )
    assert by_key[("kis", "walk_forward_analysis")][
        "active_optimized_set_count"
    ] == 1
    assert by_key[("binance", "walk_forward_analysis")]["status"] == (
        "queued_external_runner"
    )
    assert by_key[("binance", "walk_forward_analysis")]["artifact"] == (
        "crypto_pattern_lab_runner"
    )
    assert by_key[("binance", "walk_forward_analysis")]["evidence_status"] == (
        "insufficient_evidence"
    )
    assert by_key[("binance", "kelly_sizing")]["status"] == "executed"
    assert by_key[("binance", "kelly_sizing")]["artifact"] == (
        "trading_validation_refresh"
    )
    assert by_key[("binance", "kelly_sizing")]["evidence_status"] == "passed"


def test_validation_repair_execution_keeps_failed_risk_budget_queued() -> None:
    manifest = {
        "items": [
            {
                "venue": "binance",
                "discipline_id": "kelly_sizing",
                "priority": "p0",
                "status": "fail",
                "validation_mode": "risk_budget_recalibration",
                "scale_up_blocked": True,
                "evidence_targets": {
                    "max_risk_of_ruin_pct": 5.0,
                    "min_profit_factor": 1.05,
                },
            }
        ]
    }

    class Settings:
        live_evaluator_repair_execution_max_items = 8

    result = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={
            "binance": {
                "summary": {
                    "readiness": "normal",
                    "fail_count": 2,
                    "warn_count": 1,
                },
                "disciplines": [
                    {
                        "id": "kelly_sizing",
                        "status": "fail",
                        "metric": {
                            "risk_of_ruin_pct": 8.5,
                            "profit_factor": 1.01,
                            "recommended_risk_fraction": 0.0,
                        },
                    }
                ],
            }
        },
    )

    action = result["actions"][0]
    assert result["status"] == "queued"
    assert result["executed_count"] == 0
    assert result["queued_count"] == 1
    assert action["status"] == "queued_risk_rebuild"
    assert action["evidence_status"] == "insufficient_evidence"
    assert "discipline_status:fail" in action["evidence_reasons"]
    assert "risk_of_ruin:8.50/5.00" in action["evidence_reasons"]
    assert "recommended_risk_fraction:0" in action["evidence_reasons"]


def test_validation_repair_execution_does_not_invent_missing_risk_metric() -> None:
    manifest = {
        "items": [
            {
                "venue": "kis",
                "discipline_id": "sharpe_ratio",
                "priority": "p0",
                "status": "fail",
                "validation_mode": "risk_budget_recalibration",
                "scale_up_blocked": True,
                "evidence_targets": {"max_risk_of_ruin_pct": 5.0},
            }
        ]
    }

    class Settings:
        live_evaluator_repair_execution_max_items = 8

    result = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={
            "kis": {
                "summary": {"readiness": "normal", "fail_count": 1},
                "disciplines": [
                    {
                        "id": "sharpe_ratio",
                        "status": "fail",
                        "metric": {"sharpe_ratio": -0.2},
                    }
                ],
            }
        },
    )

    action = result["actions"][0]
    assert action["status"] == "queued_risk_rebuild"
    assert action["evidence_reasons"] == ["discipline_status:fail"]
    assert "risk_of_ruin_pct" not in action


def test_validation_repair_execution_keeps_failed_cost_evidence_queued() -> None:
    manifest = {
        "items": [
            {
                "venue": "binance",
                "discipline_id": "cost_simulation",
                "priority": "p0",
                "status": "fail",
                "validation_mode": "cost_evidence_repair",
                "scale_up_blocked": True,
                "evidence_targets": {
                    "min_recorded_cost_coverage_pct": 60.0,
                    "required_cost_components": [
                        "fees",
                        "taxes_or_funding",
                        "spread",
                        "slippage",
                    ],
                    "min_cost_stress_net_pnl_multiplier": "2x_positive",
                },
            }
        ]
    }

    class Settings:
        live_evaluator_repair_execution_max_items = 8

    result = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={
            "binance": {
                "disciplines": [
                    {
                        "id": "cost_simulation",
                        "status": "fail",
                        "metric": {
                            "sample_count": 10,
                            "recorded_cost_sample_count": 5,
                            "cost_by_component": {
                                "fees": 1.0,
                                "spread": 0.5,
                            },
                            "stressed_net_pnl_by_cost_multiplier": {
                                "2x": -1.25,
                            },
                        },
                    }
                ],
            }
        },
    )

    action = result["actions"][0]
    assert result["status"] == "queued"
    assert action["status"] == "queued_cost_repair"
    assert action["evidence_status"] == "insufficient_evidence"
    assert action["recorded_cost_coverage_pct"] == 50.0
    assert action["cost_stress_2x_net_pnl"] == -1.25
    assert "discipline_status:fail" in action["evidence_reasons"]
    assert "recorded_cost_coverage:50.00/60.00" in action["evidence_reasons"]
    assert "missing_cost_components:taxes_or_funding,slippage" in (
        action["evidence_reasons"]
    )


def test_validation_repair_execution_uses_present_zero_cost_components() -> None:
    manifest = {
        "items": [
            {
                "venue": "binance",
                "discipline_id": "cost_simulation",
                "priority": "p0",
                "status": "fail",
                "validation_mode": "cost_evidence_repair",
                "scale_up_blocked": True,
                "evidence_targets": {
                    "min_recorded_cost_coverage_pct": 60.0,
                    "required_cost_components": [
                        "fees",
                        "taxes_or_funding",
                        "spread",
                        "slippage",
                    ],
                    "min_cost_stress_net_pnl_multiplier": "2x_positive",
                },
            }
        ]
    }

    class Settings:
        live_evaluator_repair_execution_max_items = 8

    result = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={
            "binance": {
                "disciplines": [
                    {
                        "id": "cost_simulation",
                        "status": "warn",
                        "metric": {
                            "sample_count": 10,
                            "recorded_cost_sample_count": 10,
                            "cost_by_component": {
                                "fees": 1.0,
                            },
                            "present_cost_component_counts": {
                                "fees": 10,
                                "funding": 10,
                                "spread": 10,
                                "slippage": 10,
                            },
                            "stressed_net_pnl_by_cost_multiplier": {
                                "2x": 2.5,
                            },
                        },
                    }
                ],
            }
        },
    )

    action = result["actions"][0]
    assert result["queued_count"] == 0
    assert action["status"] == "executed"
    assert action["evidence_status"] == "passed"
    assert action["recorded_cost_coverage_pct"] == 100.0
    assert action["reason"] == "cost evidence passes coverage/component/stress targets"


def test_validation_repair_execution_keeps_failed_generic_discipline_queued() -> None:
    manifest = {
        "items": [
            {
                "venue": "kis",
                "discipline_id": "capacity_analysis",
                "priority": "p0",
                "status": "fail",
                "validation_mode": "capacity_depth_check",
                "scale_up_blocked": True,
                "evidence_targets": {
                    "min_capacity_ratio": 5.0,
                    "requires_orderbook_depth": True,
                },
            }
        ]
    }

    class Settings:
        live_evaluator_repair_execution_max_items = 8

    result = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={
            "kis": {
                "disciplines": [
                    {
                        "id": "capacity_analysis",
                        "status": "fail",
                        "metric": {"capacity_ratio": 2.0},
                    }
                ],
            }
        },
    )

    action = result["actions"][0]
    assert result["status"] == "queued"
    assert action["status"] == "queued_capacity_depth_check"
    assert action["evidence_status"] == "insufficient_evidence"
    assert "discipline_status:fail" in action["evidence_reasons"]
    assert "capacity_ratio:2.00/5.00" in action["evidence_reasons"]


def test_validation_repair_execution_executes_generic_discipline_after_evidence_passes() -> None:
    manifest = {
        "items": [
            {
                "venue": "binance",
                "discipline_id": "correlation",
                "priority": "p1",
                "status": "warn",
                "validation_mode": "portfolio_exposure_check",
                "scale_up_blocked": True,
                "evidence_targets": {
                    "max_top_cluster_share_pct": 60.0,
                },
            }
        ]
    }

    class Settings:
        live_evaluator_repair_execution_max_items = 8

    result = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={
            "binance": {
                "disciplines": [
                    {
                        "id": "correlation",
                        "status": "warn",
                        "metric": {"top_cluster_share_pct": 44.0},
                    }
                ],
            }
        },
    )

    action = result["actions"][0]
    assert result["status"] == "executed"
    assert action["status"] == "executed"
    assert action["evidence_status"] == "passed"
    assert "evidence_reasons" not in action


def test_validation_repair_execution_requires_active_crypto_wfa_evidence(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "crypto_pattern_lab.json"
    manifest = {
        "items": [
            {
                "venue": "binance",
                "discipline_id": "walk_forward_analysis",
                "priority": "p0",
                "status": "fail",
                "validation_mode": "backtest_wfa_oos_rebuild",
                "scale_up_blocked": True,
                "live_shadow_required": True,
                "evidence_targets": {"min_active_strategy_sets": 1},
            }
        ]
    }

    class Settings:
        crypto_pattern_lab_state_path = str(state_path)
        live_evaluator_repair_execution_max_items = 8

    state_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "updated_at": "2026-06-01T00:00:00+00:00",
                "service_status": {
                    "status": "ok",
                    "active_optimized_set_count": 0,
                    "total_optimized_set_count": 3,
                    "validation_hint": {
                        "status": "needs_revalidation",
                        "reasons": ["out_of_sample_missing"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    queued = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={},
    )
    queued_action = queued["actions"][0]
    assert queued["status"] == "queued"
    assert queued_action["status"] == "queued_external_runner"
    assert queued_action["evidence_status"] == "insufficient_evidence"
    assert queued_action["active_optimized_set_count"] == 0
    assert "active_strategy_sets:0/1" in queued_action["evidence_reasons"]
    assert "out_of_sample_missing" in queued_action["evidence_reasons"]

    state_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "updated_at": "2026-06-01T00:30:00+00:00",
                "service_status": {
                    "status": "ok",
                    "active_optimized_set_count": 2,
                    "total_optimized_set_count": 3,
                    "validation_hint": {"status": "passed", "reasons": []},
                },
            }
        ),
        encoding="utf-8",
    )
    observed = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={},
    )
    observed_action = observed["actions"][0]
    assert observed["status"] == "executed"
    assert observed_action["status"] == "observed_external_runner"
    assert observed_action["evidence_status"] == "passed"
    assert observed_action["active_optimized_set_count"] == 2


def test_validation_repair_execution_requires_target_lane_and_shadow_evidence(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "crypto_pattern_lab.json"
    manifest = {
        "items": [
            {
                "venue": "binance",
                "discipline_id": "lane_validation_evidence",
                "priority": "p1",
                "status": "missing",
                "validation_mode": "backtest_wfa_oos_rebuild",
                "scale_up_blocked": True,
                "live_shadow_required": True,
                "evidence_targets": {
                    "target_lanes": ["futures:long:ema_trend"],
                    "missing_dimensions": ["live_shadow"],
                },
            }
        ]
    }

    class Settings:
        crypto_pattern_lab_state_path = str(state_path)
        live_evaluator_repair_execution_max_items = 8

    state_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "service_status": {
                    "status": "ok",
                    "active_optimized_set_count": 2,
                    "optimized_strategy_sets": [
                        {
                            "symbol": "BTCUSDT",
                            "family": "mean_reversion",
                            "direction": "long",
                        }
                    ],
                    "validation_hint": {"status": "passed", "reasons": []},
                },
            }
        ),
        encoding="utf-8",
    )
    lane_missing = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={},
    )
    lane_action = lane_missing["actions"][0]
    assert lane_missing["status"] == "queued"
    assert lane_action["status"] == "queued_external_runner"
    assert lane_action["matched_target_lane_count"] == 0
    assert lane_action["missing_target_lanes"] == ["futures:long:ema_trend"]
    assert "target_lane_evidence_missing:futures:long:ema_trend" in (
        lane_action["evidence_reasons"]
    )

    state_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "service_status": {
                    "status": "ok",
                    "active_optimized_set_count": 2,
                    "optimized_strategy_sets": [
                        {
                            "symbol": "BTCUSDT",
                            "family": "ema_trend",
                            "direction": "long",
                        }
                    ],
                    "validation_hint": {"status": "passed", "reasons": []},
                },
            }
        ),
        encoding="utf-8",
    )
    shadow_missing = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={},
    )
    shadow_action = shadow_missing["actions"][0]
    assert shadow_missing["status"] == "queued"
    assert shadow_action["matched_target_lane_count"] == 1
    assert shadow_action["live_shadow_evidence_required"] is True
    assert shadow_action["live_shadow_evidence_passed"] is False
    assert "live_shadow_evidence_missing" in shadow_action["evidence_reasons"]

    state_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "service_status": {
                    "status": "ok",
                    "active_optimized_set_count": 2,
                    "optimized_strategy_sets": [
                        {
                            "symbol": "BTCUSDT",
                            "family": "ema_trend",
                            "direction": "long",
                        }
                    ],
                    "live_shadow_passed": True,
                    "validation_hint": {"status": "passed", "reasons": []},
                },
            }
        ),
        encoding="utf-8",
    )
    repaired = _execute_validation_repair_manifest(
        Settings(),
        manifest,
        sync={"status": "ok"},
        validation={},
    )
    repaired_action = repaired["actions"][0]
    assert repaired["status"] == "executed"
    assert repaired_action["status"] == "observed_external_runner"
    assert repaired_action["evidence_status"] == "passed"
    assert repaired_action["matched_target_lane_count"] == 1
    assert repaired_action["live_shadow_evidence_passed"] is True


def test_validation_repair_execution_is_ingested_into_memory(
    tmp_path: Path,
) -> None:
    class Settings:
        investment_memory_enabled = True
        investment_memory_root_path = str(tmp_path / "memory")
        investment_memory_db_path = str(tmp_path / "memory.db")
        investment_memory_policy_mode = "soft_auto"

    result = _ingest_validation_repair_execution_memory(
        Settings(),
        {
            "status": "queued",
            "actions": [
                {
                    "venue": "binance",
                    "discipline_id": "walk_forward_analysis",
                    "status": "queued_external_runner",
                    "validation_mode": "backtest_wfa_oos_rebuild",
                    "artifact": "crypto_pattern_lab_runner",
                    "scale_up_blocked": True,
                    "live_shadow_required": True,
                }
            ],
        },
    )
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=Settings.investment_memory_root_path,
            db_path=Settings.investment_memory_db_path,
        )
    )
    scorecards = {
        row["policy_id"]: row
        for row in memory.policy_scorecards(limit=20)["items"]
    }

    assert result["status"] == "ok"
    assert result["processed_count"] == 1
    policy = scorecards["validation_repair.binance.walk_forward_analysis"]
    assert policy["status"] == "active_caution"
    assert policy["repair_status"] == "queued_external_runner"
    assert policy["scale_up_blocked"] is True


def test_binance_validation_costs_use_exact_spread_without_dropping_fee_estimates() -> None:
    class Settings:
        binance_validation_futures_fee_rate = 0.001
        binance_validation_spot_fee_rate = 0.001
        binance_validation_slippage_bps = 2.0

    costs = _binance_validation_costs(
        block={"market": "futures"},
        metadata={},
        entry_price=100.0,
        exit_price=110.0,
        qty=2.0,
        reflection_fee=0.0,
        reflection_funding=0.0,
        reflection_slippage=0.0,
        reflection_spread=0.42,
        settings=Settings(),
    )

    assert costs["spread"] == 0.42
    assert costs["fees"] == 0.42
    assert costs["slippage"] == 0.084
    assert costs["status"] == "estimated_from_notional"


def test_refresh_live_edge_scorecards_removes_stale_binance_lane_families(
    tmp_path: Path,
) -> None:
    performance_repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    edge_repo.upsert_scorecard(
        venue="binance",
        strategy_family="futures:futures",
        evidence_key="all",
        scorecard={
            "sample_count": 20,
            "expectancy_pct": 0.5,
            "win_rate": 55.0,
            "rule_follow_rate": 100.0,
            "execution_error_rate": 0.0,
            "max_drawdown_pct": -1.0,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
        },
    )
    metadata = {
        "market": "futures",
        "side": "long",
        "horizon": "futures",
        "strategy_family": "all",
    }
    performance_repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="fresh-long",
            symbol="BTCUSDT",
            created_by="llm",
            status="closed",
            entry_price=100,
            exit_price=105,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    class Settings:
        live_authority_min_samples_to_scale = 1

    _refresh_live_edge_scorecards(
        performance_repo,
        edge_repo,
        settings=Settings(),
    )

    families = {
        row["strategy_family"]
        for row in edge_repo.list_scorecards(venue="binance", limit=20)
    }
    assert "futures:long" in families
    assert "futures:futures" not in families


def test_refresh_live_edge_scorecards_preserves_setup_specific_evidence_key(
    tmp_path: Path,
) -> None:
    performance_repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    metadata = {
        "horizon": "short",
        "entry_setup": "late_chase",
        "r_multiple": -0.5,
    }
    performance_repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-late-chase-loss",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=70_000,
            exit_price=69_000,
            qty=1,
            filled=True,
            metadata=metadata,
        ),
        source={"metadata": metadata},
    )

    class Settings:
        live_authority_min_samples_to_scale = 1

    _refresh_live_edge_scorecards(
        performance_repo,
        edge_repo,
        settings=Settings(),
    )

    rows = edge_repo.list_scorecards(venue="kis", limit=20)
    keys = {
        (row["strategy_family"], row["evidence_key"])
        for row in rows
    }
    assert ("short", "late_chase") in keys
    assert ("short", "all") not in keys


def test_refresh_live_edge_scorecards_splits_same_lane_by_strategy_revision(
    tmp_path: Path,
) -> None:
    performance_repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")

    for index, revision_id in enumerate(("legacy_rev", "jue_edge_repair_v2")):
        metadata = {
            "horizon": "mid",
            "entry_setup": "value_pullback",
            "strategy_revision_id": revision_id,
            "cost_model_status": "recorded",
        }
        performance_repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-rev-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=1000.0,
                exit_price=1015.0 + index,
                qty=1,
                fees=1.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    class Settings:
        live_authority_min_samples_to_scale = 1

    result = _refresh_live_edge_scorecards(
        performance_repo,
        edge_repo,
        settings=Settings(),
    )
    legacy = edge_repo.list_scorecards(
        venue="kis",
        strategy_revision_id="legacy_rev",
    )
    active = edge_repo.list_scorecards(
        venue="kis",
        strategy_revision_id="jue_edge_repair_v2",
    )

    assert result["updated_scorecards"] == 2
    assert legacy[0]["strategy_family"] == "mid"
    assert active[0]["strategy_family"] == "mid"
    assert legacy[0]["evidence_key"] == "value_pullback"
    assert active[0]["evidence_key"] == "value_pullback"
    assert legacy[0]["sample_count"] == 1
    assert active[0]["sample_count"] == 1


def test_cost_only_churn_becomes_cost_weak_setup_lane_authority(
    tmp_path: Path,
) -> None:
    performance_repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    metadata = {
        "horizon": "short",
        "entry_setup": "late_chase",
        "r_multiple": -0.1,
    }
    for index in range(5):
        performance_repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-cost-churn-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=70_000,
                exit_price=70_000,
                qty=1,
                fees=80.0,
                taxes=120.0,
                slippage=40.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    class RefreshSettings:
        live_authority_min_samples_to_scale = 5

    _refresh_live_edge_scorecards(
        performance_repo,
        edge_repo,
        settings=RefreshSettings(),
    )

    class AuthoritySettings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 1800
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 5

    payload = build_live_authority_payload(AuthoritySettings())

    lane_authority = payload["venues"]["kis"]["lane_authority"]
    assert "short:late_chase" in lane_authority["weak_lanes"]
    assert "short:late_chase" in lane_authority["cost_weak_lanes"]
    performance_lane = payload["venues"]["kis"]["performance_lanes"][0]
    assert performance_lane["lane"] == "short:late_chase"
    assert performance_lane["quality_hint"] == "weak_review"
    assert "waiting_entry" in performance_lane["action_hint"]
    lane_action = lane_authority["lane_actions"]["short:late_chase"]
    assert lane_action["grade"] == "restricted"
    assert lane_action["requires_waiting_entry"] is True
    assert lane_action["cost_drag_pct_of_gross_pnl"] == 999.0


def test_live_evaluator_writes_state(tmp_path: Path) -> None:
    state_path = tmp_path / "live_evaluator.json"

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10

    result = asyncio.run(run_live_evaluator_once(Settings()))

    assert result["status"] == "ok"
    assert state_path.exists()
    assert "authority" in result
    assert result["repair_execution"]["version"] == "validation_repair_execution_v1"
    assert "kis" in result["authority"]["venues"]
    assert result["validation"]["binance"]["discipline_count"] == 19
    assert (
        result["authority"]["venues"]["binance"]["trading_validation"]["run_id"]
        == result["validation"]["binance"]["run_id"]
    )


def test_live_evaluator_loop_logs_success_cycle(monkeypatch, caplog) -> None:
    from tradecraft.runtime import live_evaluator_runner

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_once = True
        live_evaluator_interval_sec = 30

    async def fake_run_once(_settings) -> dict:
        return {
            "status": "ok",
            "performance": {"sample_count": 12},
            "validation": {
                "kis": {"status": "ok"},
                "binance": {"status": "warn"},
            },
        }

    monkeypatch.setattr(
        live_evaluator_runner,
        "run_live_evaluator_once",
        fake_run_once,
    )
    caplog.set_level(
        logging.INFO,
        logger="tradecraft.runtime.live_evaluator_runner",
    )

    asyncio.run(live_evaluator_runner.run_live_evaluator_loop(Settings()))

    assert "live evaluator cycle status=ok" in caplog.text
    assert "kis_validation=ok" in caplog.text
    assert "binance_validation=warn" in caplog.text


def test_live_evaluator_state_compaction_keeps_status_without_raw_payload_bloat() -> None:
    result = {
        "service": "tradecraft-live-evaluator",
        "status": "ok",
        "ran_at": "2026-06-29T00:00:00+00:00",
        "enabled": True,
        "sync": {"status": "ok", "synced": [{"raw": "x" * 50_000}]},
        "validation": {
            "kis": {
                "status": "ok",
                "run_id": "kis-run",
                "computed_at": "2026-06-29T00:00:00+00:00",
                "discipline_count": 19,
                "summary": {"readiness": "scale_ready", "huge": "v" * 80_000},
                "payload": {"raw": "p" * 200_000},
            },
            "binance": {
                "status": "warn",
                "run_id": "binance-run",
                "discipline_count": 19,
                "payload": {"raw": "q" * 200_000},
            },
        },
        "memory_signals": {
            "status": "ok",
            "venues": {"kis": {"huge": "m" * 200_000}},
            "repair_manifest": {"status": "needs_repair", "items": [{"raw": "r" * 20_000}]},
        },
        "repair_execution": {"version": "validation_repair_execution_v1", "items": [{"x": "y" * 1000}]},
        "repair_memory": {"status": "ok", "huge": "z" * 50_000},
        "performance": {"status": "ok", "lanes": [{"raw": "l" * 50_000} for _ in range(20)]},
        "authority": {
            "status": "ok",
            "edge": {"status": "ok", "raw": "e" * 50_000},
            "performance": {"lanes": [{"raw": "a" * 50_000} for _ in range(20)]},
            "venues": {
                "kis": {
                    "status": "ok",
                    "live_grade": "probe",
                    "lane_authority": {
                        "lane_actions": {
                            f"lane-{idx}": {"raw": "b" * 50_000}
                            for idx in range(50)
                        },
                    },
                    "validation_gate": {"reason": "c" * 5000},
                    "performance_lanes": [{"raw": "d" * 20_000} for _ in range(20)],
                },
                "binance": {
                    "status": "ok",
                    "live_grade": "restricted",
                    "lane_authority": {
                        "lane_actions": {
                            f"lane-{idx}": {"raw": "f" * 50_000}
                            for idx in range(50)
                        },
                    },
                    "validation_gate": {"reason": "g" * 5000},
                    "performance_lanes": [{"raw": "h" * 20_000} for _ in range(20)],
                },
            },
        },
    }

    compact = _compact_live_evaluator_state(result)
    encoded = json.dumps(compact, ensure_ascii=False)

    assert compact["status"] == "ok"
    assert compact["state_compacted"] is True
    assert compact["validation"]["kis"]["run_id"] == "kis-run"
    assert compact["authority"]["venues"]["kis"]["live_grade"] == "probe"
    assert "payload" not in compact["validation"]["kis"]
    assert "raw" not in encoded
    assert len(encoded) < 80_000


def test_refresh_trading_validation_uses_venue_specific_initial_equity(
    tmp_path: Path,
) -> None:
    performance_path = tmp_path / "live_performance.db"
    repo = LivePerformanceRepository(performance_path)
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kis-loss",
            symbol="005930",
            created_by="llm",
            status="closed",
            entry_price=1_000_000,
            exit_price=940_000,
            qty=1,
            fees=0,
            taxes=0,
            slippage=0,
            filled=True,
            metadata={
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0},
            },
        ),
        source={"metadata": {"cost_model_status": "recorded"}},
    )
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="bn-win",
            symbol="BTCUSDT",
            created_by="llm",
            status="closed",
            entry_price=100,
            exit_price=110,
            qty=1,
            fees=0,
            filled=True,
            metadata={"cost_model_status": "recorded"},
        ),
        source={"metadata": {"cost_model_status": "recorded"}},
    )

    class Settings:
        live_performance_db_path = str(performance_path)
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        kis_validation_initial_equity_krw = 4_000_000
        binance_validation_initial_equity_usdt = 1_000

    result = refresh_trading_validation(Settings())

    kis_metrics = result["kis"]["metrics"]
    binance_metrics = result["binance"]["metrics"]
    assert kis_metrics["drawdown_budget"]["initial_equity"] == 4_000_000
    assert kis_metrics["total_return_pct"] == -1.5
    assert kis_metrics["max_drawdown_pct"] == -1.5
    assert binance_metrics["drawdown_budget"]["initial_equity"] == 1_000
    assert binance_metrics["total_return_pct"] == 1.0


def test_refresh_trading_validation_builds_kis_pattern_lab_before_validation(
    tmp_path: Path,
) -> None:
    performance_path = tmp_path / "live_performance.db"
    lab_path = tmp_path / "kr_equity_pattern_lab.db"
    repo = LivePerformanceRepository(performance_path)
    for index, (entry, exit_price) in enumerate(
        [
            (70_000, 72_100),
            (71_000, 72_420),
            (72_000, 70_920),
            (71_500, 74_360),
        ],
        start=1,
    ):
        metadata = {
            "horizon": "mid",
            "valuation_label": "undervalued",
            "market_regime": "risk_on",
            "cost_model_status": "recorded",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-pattern-{index}",
                symbol="005930",
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

    class Settings:
        live_performance_db_path = str(performance_path)
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        kr_equity_pattern_lab_db_path = str(lab_path)
        kr_equity_pattern_lab_enabled = True
        kr_equity_pattern_lab_min_samples = 3
        kis_validation_initial_equity_krw = 4_000_000
        binance_validation_initial_equity_usdt = 1_000

    result = refresh_trading_validation(Settings())

    assert lab_path.exists()
    assert result["kr_equity_pattern_lab"]["status"] == "ok"
    assert result["kis"]["metrics"]["pattern_lab"]["source_scope"] == (
        "kr_equity_pattern_lab"
    )
    assert result["kis"]["metrics"]["pattern_lab"]["status"] == "ok"


def test_live_authority_payload_includes_trading_validation_summary(
    tmp_path: Path,
) -> None:
    validation_repo = TradingValidationRepository(tmp_path / "trading_validation.db")
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-test",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 42.0,
                "readiness": "probe",
                "pass_count": 4,
                "warn_count": 3,
                "fail_count": 0,
                "missing_count": 12,
            },
        }
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000

    payload = build_live_authority_payload(Settings())

    validation = payload["venues"]["binance"]["trading_validation"]
    assert validation["run_id"] == "validation-test"
    assert validation["summary"]["readiness"] == "probe"


def test_live_authority_payload_prefers_active_strategy_revision_scorecards(
    tmp_path: Path,
) -> None:
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    edge_repo.upsert_scorecard(
        venue="kis",
        strategy_family="mid",
        evidence_key="value_pullback",
        scorecard={
            "sample_count": 12,
            "expectancy_pct": -0.4,
            "win_rate": 33.0,
            "profit_factor": 0.7,
            "grade": "restricted",
            "authority_multiplier": 0.5,
            "strategy_revision_id": "legacy_rev",
        },
    )
    edge_repo.upsert_scorecard(
        venue="kis",
        strategy_family="mid",
        evidence_key="value_pullback",
        scorecard={
            "sample_count": 12,
            "expectancy_pct": 0.9,
            "win_rate": 67.0,
            "profit_factor": 2.1,
            "recovery_factor": 1.5,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
            "strategy_revision_id": "jue_edge_repair_v2",
        },
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000
        jue_strategy_revision_id = "jue_edge_repair_v2"

    payload = build_live_authority_payload(Settings())

    kis = payload["venues"]["kis"]
    assert kis["active_strategy_revision_id"] == "jue_edge_repair_v2"
    assert kis["scorecards"][0]["strategy_revision_id"] == "jue_edge_repair_v2"
    assert kis["scorecards"][0]["expectancy_pct"] == 0.9


def test_live_authority_payload_does_not_fallback_to_legacy_scorecards_for_active_revision(
    tmp_path: Path,
) -> None:
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    edge_repo.upsert_scorecard(
        venue="kis",
        strategy_family="mid",
        evidence_key="value_pullback",
        scorecard={
            "sample_count": 12,
            "expectancy_pct": 0.9,
            "win_rate": 67.0,
            "profit_factor": 2.1,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
            "strategy_revision_id": "legacy_rev",
        },
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000
        jue_strategy_revision_id = "jue_edge_repair_v2"

    payload = build_live_authority_payload(Settings())

    kis = payload["venues"]["kis"]
    assert kis["active_strategy_revision_id"] == "jue_edge_repair_v2"
    assert kis["scorecards"] == []


def test_live_authority_payload_prefers_active_strategy_revision_validation(
    tmp_path: Path,
) -> None:
    validation_repo = TradingValidationRepository(tmp_path / "trading_validation.db")
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "active-validation",
            "venue": "binance",
            "scope": "live",
            "strategy_revision_id": "jue_edge_repair_v2",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 82.0,
                "readiness": "probe",
                "pass_count": 12,
                "warn_count": 7,
                "fail_count": 0,
                "missing_count": 0,
            },
        }
    )
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "legacy-validation-newer",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-02T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {"id": "cost_simulation", "status": "fail"},
            ],
            "summary": {
                "total_score": 35.0,
                "readiness": "blocked_by_validation",
                "pass_count": 5,
                "warn_count": 8,
                "fail_count": 6,
                "missing_count": 0,
            },
        }
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000
        jue_strategy_revision_id = "jue_edge_repair_v2"

    payload = build_live_authority_payload(Settings())

    validation = payload["venues"]["binance"]["trading_validation"]
    assert validation["run_id"] == "active-validation"
    assert validation["strategy_revision_id"] == "jue_edge_repair_v2"
    assert validation["summary"]["fail_count"] == 0


def test_live_authority_gate_separates_hard_fail_from_hard_blocking(
    tmp_path: Path,
) -> None:
    validation_repo = TradingValidationRepository(tmp_path / "trading_validation.db")
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "active-missing-validation",
            "venue": "binance",
            "scope": "live",
            "strategy_revision_id": "jue_edge_repair_v2",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 7.89,
                "readiness": "blocked_by_validation",
                "pass_count": 0,
                "warn_count": 3,
                "fail_count": 0,
                "missing_count": 16,
                "core_fail_count": 0,
                "core_missing_count": 3,
                "hard_fail_count": 0,
                "hard_missing_count": 3,
                "hard_blocking_count": 3,
            },
        }
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000
        jue_strategy_revision_id = "jue_edge_repair_v2"

    payload = build_live_authority_payload(Settings())

    gate = payload["venues"]["binance"]["validation_gate"]
    pressure = gate["validation_pressure"]
    evidence = payload["venues"]["binance"]["active_revision_evidence"]
    assert gate["status"] == "blocked_by_validation"
    assert gate["hard_fail_count"] == 0
    assert gate["hard_missing_count"] == 3
    assert gate["hard_blocking_count"] == 3
    assert pressure["hard_fail_count"] == 0
    assert pressure["hard_blocking_count"] == 3
    assert pressure["severity"] == "blocked"
    assert evidence["strategy_revision_id"] == "jue_edge_repair_v2"
    assert evidence["status"] == "no_active_revision_samples"
    assert evidence["authority_posture"] == (
        "observe_only_until_new_revision_trades_close"
    )
    assert evidence["effective_sample_count"] == 0
    assert evidence["scale_up_allowed"] is False


def test_live_authority_payload_uses_active_revision_performance_lanes(
    tmp_path: Path,
) -> None:
    performance_repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    for block_id, exit_price, revision_id in (
        ("legacy-loss", 98.0, "legacy_rev"),
        ("repair-win", 104.0, "jue_edge_repair_v2"),
    ):
        metadata = {
            "horizon": "mid",
            "strategy_revision_id": revision_id,
        }
        performance_repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=block_id,
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000
        jue_strategy_revision_id = "jue_edge_repair_v2"

    payload = build_live_authority_payload(Settings())

    lanes = payload["venues"]["kis"]["performance_lanes"]
    evidence = payload["venues"]["kis"]["active_revision_evidence"]
    assert lanes[0]["lane"] == "mid"
    assert lanes[0]["alpha_count"] == 1
    assert lanes[0]["alpha_net_pnl"] == 4.0
    assert lanes[0]["strategy_revision_counts"] == {
        "jue_edge_repair_v2": 1
    }
    assert evidence["status"] == "insufficient_active_revision_samples"
    assert evidence["effective_sample_count"] == 1
    assert evidence["lane_alpha_count"] == 1
    assert evidence["scorecard_count"] == 0
    assert evidence["scale_up_allowed"] is False


def test_live_authority_payload_includes_latest_repair_execution_context(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "live_evaluator.json"
    state_path.write_text(
        json.dumps(
            {
                "repair_execution": {
                    "version": "validation_repair_execution_v1",
                    "status": "queued",
                    "m1_execution_posture": "sequential_priority_queue",
                    "actions": [
                        {
                            "venue": "binance",
                            "discipline_id": "walk_forward_analysis",
                            "priority": "p0",
                            "status": "queued_external_runner",
                            "validation_mode": "backtest_wfa_oos_rebuild",
                            "scale_up_blocked": True,
                            "live_shadow_required": True,
                            "artifact": "crypto_pattern_lab_runner",
                            "reason": "crypto pattern lab must rebuild WFA/OOS",
                            "runner_status": "missing",
                        },
                        {
                            "venue": "kis",
                            "discipline_id": "data_validation",
                            "priority": "p0",
                            "status": "executed",
                            "validation_mode": "data_repair_before_trade",
                            "scale_up_blocked": True,
                            "artifact": "sync_live_performance_and_edges",
                        },
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        live_evaluator_state_path = str(state_path)
        trading_validation_max_age_sec = 864000000
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10

    payload = build_live_authority_payload(Settings())

    binance_repair = payload["venues"]["binance"]["repair_execution"]
    kis_repair = payload["venues"]["kis"]["repair_execution"]
    assert binance_repair["status"] == "queued"
    assert binance_repair["queued_count"] == 1
    assert binance_repair["actions"][0]["discipline_id"] == "walk_forward_analysis"
    assert binance_repair["actions"][0]["live_shadow_required"] is True
    assert binance_repair["actions"][0]["artifact"] == "crypto_pattern_lab_runner"
    assert kis_repair["executed_count"] == 1
    assert kis_repair["actions"][0]["artifact"] == "sync_live_performance_and_edges"


def test_live_authority_payload_preserves_validation_payload_for_gate_context(
    tmp_path: Path,
) -> None:
    validation_repo = TradingValidationRepository(tmp_path / "trading_validation.db")
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-fail-context",
            "venue": "kis",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [
                {
                    "id": "monte_carlo",
                    "label": "몬테카를로 시뮬레이션",
                    "status": "fail",
                    "action": "Reduce sequence risk before scale-up.",
                }
            ],
            "summary": {
                "total_score": 71.0,
                "readiness": "blocked_by_validation",
                "pass_count": 11,
                "warn_count": 5,
                "fail_count": 1,
                "missing_count": 0,
            },
            "metrics": {
                "capacity": {
                    "status": "fail",
                    "capacity_method": "metadata_capacity_ratio",
                    "min_capacity_ratio": 0.79563,
                    "tightest_symbol": "023810",
                    "tightest_block_id": "blk_023810",
                }
            },
            "operator_guidance": ["몬테카를로: sequence risk를 낮추기"],
        }
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000

    payload = build_live_authority_payload(Settings())

    kis = payload["venues"]["kis"]
    assert kis["trading_validation"]["payload"]["disciplines"][0]["id"] == (
        "monte_carlo"
    )
    assert kis["validation_gate"]["failed_disciplines"][0]["id"] == "monte_carlo"
    passport = kis["validation_gate"]["validation_passport"]
    assert passport["version"] == "trading_validation_passport_v1"
    assert passport["status"] == "blocked_by_validation"
    assert passport["readiness"] == "blocked_by_validation"
    assert passport["score"] == 71.0
    assert passport["expected_count"] == 19
    assert passport["row_detail_count"] == 1
    assert passport["row_detail_complete"] is False
    assert passport["is_complete"] is False
    assert passport["fail_count"] == 1
    assert passport["missing_count"] == 18
    assert passport["failed_ids"] == ["monte_carlo"]
    assert "monte_carlo" in passport["weak_ids"]
    assert "data_validation" in passport["weak_ids"]
    assert passport["requires_revalidation"] is True
    assert kis["validation_gate"]["capacity_bottleneck"]["tightest_symbol"] == "023810"
    assert kis["validation_gate"]["operator_guidance"] == [
        "몬테카를로: sequence risk를 낮추기"
    ]


def test_live_authority_blocks_scale_up_when_validation_is_blocked(
    tmp_path: Path,
) -> None:
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    edge_repo.upsert_scorecard(
        venue="binance",
        strategy_family="futures_momentum",
        evidence_key="verified-edge",
        scorecard={
            "sample_count": 30,
            "expectancy_pct": 0.8,
            "win_rate": 58.0,
            "rule_follow_rate": 92.0,
            "execution_error_rate": 0.0,
            "max_drawdown_pct": -2.0,
            "profit_factor": 1.9,
            "recovery_factor": 1.4,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
        },
    )
    validation_repo = TradingValidationRepository(tmp_path / "trading_validation.db")
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-blocked",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 70.0,
                "readiness": "blocked_by_validation",
                "pass_count": 12,
                "warn_count": 4,
                "fail_count": 1,
                "missing_count": 2,
            },
        }
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000

    payload = build_live_authority_payload(Settings())
    binance = payload["venues"]["binance"]

    assert binance["live_grade"] == "scale_candidate"
    assert binance["allow_scale_up"] is False
    assert binance["max_budget_multiplier"] == 0.5
    assert binance["validation_gate"]["status"] == "blocked_by_validation"
    assert binance["validation_gate"]["original_max_budget_multiplier"] == 1.25


def test_live_authority_prevents_scale_up_until_validation_is_scale_ready(
    tmp_path: Path,
) -> None:
    edge_repo = LiveEdgeRepository(tmp_path / "live_edge.db")
    edge_repo.upsert_scorecard(
        venue="binance",
        strategy_family="futures_momentum",
        evidence_key="verified-edge",
        scorecard={
            "sample_count": 30,
            "expectancy_pct": 0.8,
            "win_rate": 58.0,
            "rule_follow_rate": 92.0,
            "execution_error_rate": 0.0,
            "max_drawdown_pct": -2.0,
            "profit_factor": 1.9,
            "recovery_factor": 1.4,
            "grade": "scale_candidate",
            "authority_multiplier": 1.25,
        },
    )
    validation_repo = TradingValidationRepository(tmp_path / "trading_validation.db")
    validation_repo.save_run(
        {
            "status": "ok",
            "run_id": "validation-probe",
            "venue": "binance",
            "scope": "live",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "discipline_count": 19,
            "disciplines": [],
            "summary": {
                "total_score": 58.0,
                "readiness": "probe",
                "pass_count": 5,
                "warn_count": 7,
                "fail_count": 0,
                "missing_count": 7,
            },
        }
    )

    class Settings:
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 10
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        trading_validation_max_age_sec = 864000000

    payload = build_live_authority_payload(Settings())
    binance = payload["venues"]["binance"]

    assert binance["live_grade"] == "scale_candidate"
    assert binance["allow_scale_up"] is False
    assert binance["max_budget_multiplier"] == 1.0
    assert binance["validation_gate"]["status"] == "validation_probe"
    assert binance["validation_gate"]["original_max_budget_multiplier"] == 1.25
    assert binance["validation_gate"]["applied_max_budget_multiplier"] == 1.0


def test_live_evaluator_syncs_kis_closed_block_into_edge(tmp_path: Path) -> None:
    state_path = tmp_path / "live_evaluator.json"
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
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
                qty INTEGER NOT NULL,
                limit_price INTEGER NOT NULL DEFAULT 0,
                order_type TEXT NOT NULL DEFAULT '00',
                status TEXT NOT NULL,
                order_no TEXT NOT NULL DEFAULT '',
                order_orgno TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                filled_qty INTEGER NOT NULL DEFAULT 0,
                remaining_qty INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL,
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, qty_initial, qty_open, entry_price,
                target_price, stop_price, created_by, status, metadata_json,
                created_at, updated_at, opened_at, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "blk_005930_1",
                "005930",
                2,
                0,
                70000,
                76000,
                67000,
                "llm",
                "closed",
                '{"horizon":"mid"}',
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T01:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T01:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO block_orders (
                block_id, symbol, side, qty, limit_price, status,
                filled_qty, remaining_qty,
                avg_fill_price, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "blk_005930_1",
                "005930",
                "buy",
                2,
                70000,
                "filled",
                2,
                0,
                70000,
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO block_orders (
                block_id, symbol, side, qty, limit_price, status,
                filled_qty, remaining_qty,
                avg_fill_price, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "blk_005930_1",
                "005930",
                "sell",
                2,
                73500,
                "filled",
                2,
                0,
                73500,
                "2026-06-01T01:00:00+00:00",
                "2026-06-01T01:00:00+00:00",
            ),
        )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(kis_db)
        binance_block_trader_db_path = str(tmp_path / "missing_binance.db")
        kis_validation_spread_bps = 1.5

    result = asyncio.run(run_live_evaluator_once(Settings()))

    kis = result["authority"]["venues"]["kis"]
    assert result["sync"]["synced_blocks"]["kis"] == 1
    assert kis["scorecard_count"] >= 1
    assert kis["scorecards"][0]["strategy_family"] == "mid"
    with sqlite3.connect(tmp_path / "live_performance.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT cost_total, spread, funding, cost_precision, source_json
            FROM live_block_performance
            WHERE venue = 'kis' AND block_id = 'blk_005930_1'
            """
        ).fetchone()
    source = json.loads(row["source_json"])
    metadata = source["metadata"]
    assert row["cost_total"] > 0
    assert row["spread"] > 0
    assert row["funding"] == 0
    assert row["cost_precision"] == "estimated"
    assert metadata["cost_model_status"] == "estimated_from_notional"
    assert metadata["cost_components"]["taxes"] > 0
    assert metadata["cost_components"]["spread"] > 0
    assert metadata["cost_components"]["funding"] == 0
    assert result["validation"]["kis"]["metrics"]["data_quality"]["missing_cost_count"] == 0


def test_live_evaluator_uses_kis_filled_qty_and_avg_prices_for_closed_block(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "live_evaluator.json"
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
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
                qty INTEGER NOT NULL,
                limit_price INTEGER NOT NULL DEFAULT 0,
                order_type TEXT NOT NULL DEFAULT '00',
                status TEXT NOT NULL,
                order_no TEXT NOT NULL DEFAULT '',
                order_orgno TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                filled_qty INTEGER NOT NULL DEFAULT 0,
                remaining_qty INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL,
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, name, qty_initial, qty_open, entry_price,
                target_price, stop_price, created_by, status, metadata_json,
                created_at, updated_at, opened_at, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "kis-partial-round-trip",
                "277810",
                "레인보우로보틱스",
                3,
                0,
                99_000,
                106_000,
                96_000,
                "llm",
                "closed",
                json.dumps(
                    {
                        "horizon": "mid",
                        "entry_quality": "pullback_reclaim",
                    },
                    ensure_ascii=False,
                ),
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T01:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T01:00:00+00:00",
            ),
        )
        for side, price in (("buy", 100_000), ("sell", 104_000)):
            conn.execute(
                """
                INSERT INTO block_orders (
                    block_id, symbol, side, qty, limit_price, status,
                    filled_qty, remaining_qty, avg_fill_price, response_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "kis-partial-round-trip",
                    "277810",
                    side,
                    3,
                    price,
                    "filled",
                    1,
                    0,
                    price,
                    json.dumps(
                        {"status": "filled", "raw": {"tot_ccld_qty": "1"}},
                        ensure_ascii=False,
                    ),
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                ),
            )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(kis_db)
        binance_block_trader_db_path = str(tmp_path / "missing_binance.db")
        kis_validation_spread_bps = 0.0

    result = asyncio.run(run_live_evaluator_once(Settings()))

    assert result["sync"]["synced_blocks"]["kis"] == 1
    with sqlite3.connect(tmp_path / "live_performance.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT entry_price, exit_price, qty, gross_pnl, net_pnl,
                   entry_quality_score, source_json
            FROM live_block_performance
            WHERE venue = 'kis' AND block_id = 'kis-partial-round-trip'
            """
        ).fetchone()
    source = json.loads(row["source_json"])
    metadata = source["metadata"]

    assert row["entry_price"] == pytest.approx(100_000)
    assert row["exit_price"] == pytest.approx(104_000)
    assert row["qty"] == pytest.approx(1)
    assert row["gross_pnl"] == pytest.approx(4_000)
    assert row["net_pnl"] < row["gross_pnl"]
    assert row["entry_quality_score"] == pytest.approx(80.0)
    assert metadata["fill_evidence_status"] == "round_trip_filled"
    assert metadata["filled_qty"] == pytest.approx(1)
    assert metadata["buy_filled_qty"] == pytest.approx(1)
    assert metadata["sell_filled_qty"] == pytest.approx(1)
    assert metadata["entry_price_source"] == "buy_fill_avg"
    assert metadata["exit_price_source"] == "sell_fill_avg"


def test_live_evaluator_keeps_cancelled_waiting_kis_block_out_of_alpha(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "live_evaluator.json"
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
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
                qty INTEGER NOT NULL,
                limit_price INTEGER NOT NULL DEFAULT 0,
                order_type TEXT NOT NULL DEFAULT '00',
                status TEXT NOT NULL,
                order_no TEXT NOT NULL DEFAULT '',
                order_orgno TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                filled_qty INTEGER NOT NULL DEFAULT 0,
                remaining_qty INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL,
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, name, qty_initial, qty_open, entry_price,
                target_price, stop_price, created_by, status, metadata_json,
                created_at, updated_at, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "kis-cancelled-wait",
                "023810",
                "인팩",
                10,
                0,
                5000,
                5650,
                4850,
                "llm",
                "closed",
                json.dumps(
                    {
                        "entry_style": "wait_for_price",
                        "entry_trigger_status": "cancelled",
                        "entry_cancelled_at": "2026-06-12T00:11:34+00:00",
                        "entry_cancel_reason": "thesis_invalidated_before_fill",
                        "horizon": "mid",
                    },
                    ensure_ascii=False,
                ),
                "2026-06-11T04:20:36+00:00",
                "2026-06-12T00:11:34+00:00",
                "2026-06-12T00:11:34+00:00",
            ),
        )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(kis_db)
        binance_block_trader_db_path = str(tmp_path / "missing_binance.db")

    result = asyncio.run(run_live_evaluator_once(Settings()))

    with sqlite3.connect(tmp_path / "live_performance.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT filled, include_in_jue_alpha, attribution, gross_pnl, net_pnl,
                   exit_price, source_json
            FROM live_block_performance
            WHERE venue = 'kis' AND block_id = 'kis-cancelled-wait'
            """
        ).fetchone()

    source = json.loads(row["source_json"])
    assert result["sync"]["synced_blocks"]["kis"] == 1
    assert row["filled"] == 0
    assert row["include_in_jue_alpha"] == 0
    assert row["attribution"] == "unfilled_or_unrealized"
    assert row["gross_pnl"] == 0
    assert row["net_pnl"] == 0
    assert row["exit_price"] == 5000
    assert source["metadata"]["fill_evidence_status"] == "cancelled_before_fill"
    assert result["validation"]["kis"]["metrics"]["sample_count"] == 0


def test_live_evaluator_enriches_binance_validation_metadata(tmp_path: Path) -> None:
    state_path = tmp_path / "live_evaluator.json"
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'futures',
                side TEXT NOT NULL DEFAULT 'long',
                qty_initial REAL NOT NULL,
                qty_open REAL NOT NULL DEFAULT 0,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                leverage INTEGER NOT NULL DEFAULT 1,
                margin_type TEXT NOT NULL DEFAULT '',
                liquidation_price REAL,
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
            CREATE TABLE block_performance_reflections (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'futures',
                side TEXT NOT NULL DEFAULT 'long',
                entry_price REAL NOT NULL DEFAULT 0,
                exit_price REAL NOT NULL DEFAULT 0,
                stop_price REAL NOT NULL DEFAULT 0,
                target_price REAL NOT NULL DEFAULT 0,
                pnl_usdt REAL NOT NULL DEFAULT 0,
                r_multiple REAL NOT NULL DEFAULT 0,
                lesson_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                fee_usdt REAL NOT NULL DEFAULT 0,
                funding_usdt REAL NOT NULL DEFAULT 0,
                slippage_usdt REAL NOT NULL DEFAULT 0,
                spread_usdt REAL NOT NULL DEFAULT 0,
                cost_source TEXT NOT NULL DEFAULT ''
            );
            """
        )
        rows = [
            (
                "bnb-test-btc",
                "BTCUSDT",
                "futures",
                "long",
                0.01,
                100.0,
                106.0,
                95.0,
                {
                    "horizon": "futures",
                    "strategy_family": "breakout",
                    "crypto_market_pulse": {
                        "regime_brief": {"regime": "risk_on"}
                    },
                    "return_window_pct": [1.0, 1.8, -0.4, 1.2],
                },
            ),
            (
                "bnb-test-eth",
                "ETHUSDT",
                "futures",
                "short",
                0.02,
                80.0,
                72.0,
                85.0,
                {
                    "horizon": "futures",
                    "strategy_family": "mean_reversion",
                    "crypto_market_pulse": {
                        "regime_brief": {"regime": "risk_off"}
                    },
                    "return_window_pct": [-0.8, -1.1, 0.3, -0.4],
                },
            ),
        ]
        for block_id, symbol, market, side, qty, entry, target, stop, metadata in rows:
            conn.execute(
                """
                INSERT INTO blocks (
                    block_id, symbol, market, side, qty_initial, qty_open,
                    entry_price, target_price, stop_price, leverage, created_by,
                    status, metadata_json, created_at, updated_at, opened_at, closed_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 2, 'llm', 'closed', ?, ?, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    market,
                    side,
                    qty,
                    entry,
                    target,
                    stop,
                    json.dumps(metadata),
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO block_performance_reflections (
                    block_id, symbol, market, side, entry_price, exit_price,
                    stop_price, target_price, created_at, fee_usdt, funding_usdt,
                    slippage_usdt, spread_usdt, cost_source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    market,
                    side,
                    entry,
                    target,
                    stop,
                    target,
                    "2026-06-01T01:00:00+00:00",
                    0.01,
                    0.002,
                    0.003,
                    0.004,
                    "explicit",
                ),
            )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(tmp_path / "missing_kis.db")
        binance_block_trader_db_path = str(binance_db)
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        trading_validation_max_age_sec = 864000000

    result = asyncio.run(run_live_evaluator_once(Settings()))
    validation = result["validation"]["binance"]
    metrics = validation["metrics"]

    assert metrics["sample_count"] == 2
    assert metrics["cost_simulation"]["status"] != "missing"
    assert metrics["cost_simulation"]["cost_by_component"]["fees"] > 0
    assert metrics["cost_simulation"]["recorded_cost_sample_count"] == 2
    with sqlite3.connect(tmp_path / "live_performance.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT cost_precision, cost_model_status, cost_source, fees, funding,
                   slippage, spread
            FROM live_block_performance
            WHERE venue = 'binance' AND block_id = 'bnb-test-btc'
            """
        ).fetchone()
    assert row["cost_precision"] == "recorded"
    assert row["cost_model_status"] == "recorded"
    assert row["cost_source"] == "explicit"
    assert row["fees"] == pytest.approx(0.01)
    assert row["funding"] == pytest.approx(0.002)
    assert row["slippage"] == pytest.approx(0.003)
    assert row["spread"] == pytest.approx(0.004)
    assert metrics["regime_scorecards"]["status"] == "pass"
    assert metrics["regime_scorecards"]["regime_count"] == 2
    assert metrics["correlation_proxy"]["status"] != "missing"
    assert metrics["factor_exposure"]["status"] != "missing"


def test_binance_fill_evidence_does_not_accept_reflection_without_entry_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "binance_blocks.db"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE block_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'spot',
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'LIMIT_IOC',
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        evidence = _binance_block_fill_evidence(
            conn,
            block={
                "block_id": "reflection-only",
                "symbol": "ETHUSDT",
                "market": "spot",
                "side": "long",
                "status": "closed",
                "opened_at": "",
            },
            metadata={},
            entry_price=200.0,
            exit_price=210.0,
            qty=0.5,
            reflection_exit_price=210.0,
        )

    assert evidence["filled"] is False
    assert evidence["status"] == "missing_entry_evidence"
    assert evidence["reason"] == "no_opened_at_or_entry_fill"


def test_live_evaluator_excludes_binance_closed_block_without_exit_fill(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "live_evaluator.json"
    binance_db = tmp_path / "binance_blocks.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'futures',
                side TEXT NOT NULL DEFAULT 'long',
                qty_initial REAL NOT NULL,
                qty_open REAL NOT NULL DEFAULT 0,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                leverage INTEGER NOT NULL DEFAULT 1,
                margin_type TEXT NOT NULL DEFAULT '',
                liquidation_price REAL,
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
                market TEXT NOT NULL DEFAULT 'futures',
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'LIMIT_IOC',
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE block_performance_reflections (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'futures',
                side TEXT NOT NULL DEFAULT 'long',
                entry_price REAL NOT NULL DEFAULT 0,
                exit_price REAL NOT NULL DEFAULT 0,
                stop_price REAL NOT NULL DEFAULT 0,
                target_price REAL NOT NULL DEFAULT 0,
                pnl_usdt REAL NOT NULL DEFAULT 0,
                r_multiple REAL NOT NULL DEFAULT 0,
                lesson_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                fee_usdt REAL NOT NULL DEFAULT 0,
                funding_usdt REAL NOT NULL DEFAULT 0,
                slippage_usdt REAL NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, market, side, qty_initial, qty_open,
                entry_price, target_price, stop_price, leverage, created_by,
                status, metadata_json, created_at, updated_at, opened_at, closed_at
            )
            VALUES (?, ?, 'futures', 'long', 1.0, 0, 100.0, 120.0, 90.0, 2,
                    'llm', 'closed', ?, ?, ?, ?, ?)
            """,
            (
                "bnb-missing-exit-fill",
                "NEARUSDT",
                json.dumps({"horizon": "futures", "strategy_family": "breakout"}),
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T01:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T01:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO block_orders (
                block_id, symbol, market, side, qty, order_type, status,
                reason, response_json, created_at, updated_at
            )
            VALUES (?, ?, 'futures', 'buy', 1.0, 'LIMIT_IOC', 'sent',
                    'entry_order', ?, ?, ?)
            """,
            (
                "bnb-missing-exit-fill",
                "NEARUSDT",
                json.dumps(
                    {
                        "status": "FILLED",
                        "executedQty": "1.0",
                        "avgPrice": "100.0",
                    }
                ),
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ),
        )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(tmp_path / "missing_kis.db")
        binance_block_trader_db_path = str(binance_db)
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        trading_validation_max_age_sec = 864000000

    result = asyncio.run(run_live_evaluator_once(Settings()))

    with sqlite3.connect(tmp_path / "live_performance.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT filled, include_in_jue_alpha, attribution, gross_pnl, net_pnl,
                   exit_price, source_json
            FROM live_block_performance
            WHERE venue = 'binance' AND block_id = 'bnb-missing-exit-fill'
            """
        ).fetchone()

    source = json.loads(row["source_json"])
    assert result["sync"]["synced_blocks"]["binance"] == 1
    assert row["filled"] == 0
    assert row["include_in_jue_alpha"] == 0
    assert row["attribution"] == "unfilled_or_unrealized"
    assert row["gross_pnl"] == 0
    assert row["net_pnl"] == 0
    assert row["exit_price"] == 100.0
    assert source["metadata"]["fill_evidence_status"] == "missing_exit_fill"
    assert source["metadata"]["synthetic_exit_reference_price"] == 120.0
    assert result["validation"]["binance"]["metrics"]["sample_count"] == 0


def test_live_evaluator_backfills_binance_capacity_and_regime_from_research_db(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "live_evaluator.json"
    binance_db = tmp_path / "binance_blocks.db"
    crypto_research_db = tmp_path / "crypto_market_research.db"
    with sqlite3.connect(binance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'futures',
                side TEXT NOT NULL DEFAULT 'long',
                qty_initial REAL NOT NULL,
                qty_open REAL NOT NULL DEFAULT 0,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                leverage INTEGER NOT NULL DEFAULT 1,
                margin_type TEXT NOT NULL DEFAULT '',
                liquidation_price REAL,
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
            CREATE TABLE block_performance_reflections (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'futures',
                side TEXT NOT NULL DEFAULT 'long',
                entry_price REAL NOT NULL DEFAULT 0,
                exit_price REAL NOT NULL DEFAULT 0,
                stop_price REAL NOT NULL DEFAULT 0,
                target_price REAL NOT NULL DEFAULT 0,
                pnl_usdt REAL NOT NULL DEFAULT 0,
                r_multiple REAL NOT NULL DEFAULT 0,
                lesson_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                fee_usdt REAL NOT NULL DEFAULT 0,
                funding_usdt REAL NOT NULL DEFAULT 0,
                slippage_usdt REAL NOT NULL DEFAULT 0
            );
            """
        )
        for block_id, symbol, side, qty, entry, target, stop in [
            ("bnb-research-btc", "BTCUSDT", "long", 0.01, 100.0, 106.0, 96.0),
            ("bnb-research-sol", "SOLUSDT", "short", 0.2, 50.0, 46.0, 53.0),
        ]:
            conn.execute(
                """
                INSERT INTO blocks (
                    block_id, symbol, market, side, qty_initial, qty_open,
                    entry_price, target_price, stop_price, leverage, created_by,
                    status, metadata_json, created_at, updated_at, opened_at, closed_at
                )
                VALUES (?, ?, 'futures', ?, ?, 0, ?, ?, ?, 2, 'llm', 'closed', '{}', ?, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    side,
                    qty,
                    entry,
                    target,
                    stop,
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO block_performance_reflections (
                    block_id, symbol, market, side, entry_price, exit_price,
                    stop_price, target_price, created_at
                )
                VALUES (?, ?, 'futures', ?, ?, ?, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    side,
                    entry,
                    target,
                    stop,
                    target,
                    "2026-06-01T01:00:00+00:00",
                ),
            )

    with sqlite3.connect(crypto_research_db) as conn:
        conn.executescript(
            """
            CREATE TABLE crypto_features (
                symbol TEXT PRIMARY KEY,
                feature_json TEXT NOT NULL DEFAULT '{}',
                score REAL NOT NULL DEFAULT 0,
                regime TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE crypto_regime_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                regime TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                captured_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO crypto_regime_snapshots (regime, payload_json, captured_at)
            VALUES (?, ?, ?)
            """,
            (
                "risk_on_rotation",
                json.dumps({"regime": "risk_on_rotation", "status": "ok"}),
                "2026-06-01T00:30:00+00:00",
            ),
        )
        for symbol, regime in [("BTCUSDT", "up"), ("SOLUSDT", "rotation")]:
            conn.execute(
                """
                INSERT INTO crypto_features (symbol, feature_json, score, regime, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    json.dumps(
                        {
                            "symbol": symbol,
                            "price": 100,
                            "quote_volume_usdt": 5_000_000,
                            "spread_bps": 1.5,
                            "bid_price": 99.99,
                            "ask_price": 100.01,
                            "regime": regime,
                            "timeframe_alignment": "bullish",
                            "funding_rate": 0.0001,
                        }
                    ),
                    71.0,
                    regime,
                    "2026-06-01T00:30:00+00:00",
                ),
            )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(tmp_path / "missing_kis.db")
        binance_block_trader_db_path = str(binance_db)
        crypto_market_research_db_path = str(crypto_research_db)
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        trading_validation_max_age_sec = 864000000

    result = asyncio.run(run_live_evaluator_once(Settings()))
    metrics = result["validation"]["binance"]["metrics"]

    assert metrics["capacity"]["status"] != "missing"
    assert metrics["capacity"]["covered_sample_count"] == 2
    assert metrics["regime_scorecards"]["status"] == "pass"
    assert metrics["regime_scorecards"]["regime_count"] >= 2
    latest = sqlite3.connect(tmp_path / "live_performance.db")
    latest.row_factory = sqlite3.Row
    performance_row = latest.execute(
        """
        SELECT source_json, spread, cost_total
        FROM live_block_performance
        WHERE venue = 'binance' AND block_id = 'bnb-research-btc'
        """
    ).fetchone()
    metadata = json.loads(performance_row["source_json"])["metadata"]
    assert metadata["capacity_source"] == "crypto_market_research_features"
    assert metadata["market_regime_source"] == "crypto_market_research_db"
    assert metadata["cost_components"]["spread"] > 0
    assert performance_row["spread"] > 0
    assert performance_row["cost_total"] > performance_row["spread"]
    assert metrics["cost_simulation"]["cost_by_component"]["spread"] > 0


def test_live_evaluator_backfills_kis_regime_and_factor_context(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "live_evaluator.json"
    kis_db = tmp_path / "kis_blocks.db"
    market_pulse_db = tmp_path / "market_pulse.db"
    valuation_db = tmp_path / "symbol_fundamentals.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
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
                qty INTEGER NOT NULL,
                limit_price INTEGER NOT NULL DEFAULT 0,
                order_type TEXT NOT NULL DEFAULT '00',
                status TEXT NOT NULL,
                order_no TEXT NOT NULL DEFAULT '',
                order_orgno TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                filled_qty INTEGER NOT NULL DEFAULT 0,
                remaining_qty INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL,
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for block_id, symbol, name, entry, exit_price in [
            ("kis-005930", "005930", "삼성전자", 70000, 73500),
            ("kis-000660", "000660", "SK하이닉스", 180000, 171000),
        ]:
            conn.execute(
                """
                INSERT INTO blocks (
                    block_id, symbol, name, qty_initial, qty_open, entry_price,
                    target_price, stop_price, created_by, status, metadata_json,
                    created_at, updated_at, opened_at, closed_at
                )
                VALUES (?, ?, ?, 1, 0, ?, ?, ?, 'llm', 'closed', '{}', ?, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    name,
                    entry,
                    exit_price,
                    entry * 0.95,
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO block_orders (
                    block_id, symbol, side, qty, limit_price, status,
                    filled_qty, remaining_qty,
                    avg_fill_price, created_at, updated_at
                )
                VALUES (?, ?, 'buy', 1, ?, 'filled', 1, 0, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    entry,
                    entry,
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO block_orders (
                    block_id, symbol, side, qty, limit_price, status,
                    filled_qty, remaining_qty,
                    avg_fill_price, created_at, updated_at
                )
                VALUES (?, ?, 'sell', 1, ?, 'filled', 1, 0, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    exit_price,
                    exit_price,
                    "2026-06-01T01:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                ),
            )

    with sqlite3.connect(market_pulse_db) as conn:
        conn.executescript(
            """
            CREATE TABLE market_pulse_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                trading_day TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ok',
                regime TEXT NOT NULL DEFAULT '',
                score REAL NOT NULL DEFAULT 0,
                indices_json TEXT NOT NULL DEFAULT '[]',
                sector_json TEXT NOT NULL DEFAULT '{}',
                block_alignment_json TEXT NOT NULL DEFAULT '{}',
                risk_flags_json TEXT NOT NULL DEFAULT '[]',
                data_gaps_json TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO market_pulse_snapshots (
                captured_at, trading_day, status, regime, score, sector_json
            )
            VALUES (?, ?, 'ok', 'rotation', 74.2, ?)
            """,
            (
                "2026-06-01T00:30:00+00:00",
                "2026-06-01",
                json.dumps(
                    {
                        "status": "ok",
                        "items": [
                            {
                                "name": "반도체",
                                "direction": "positive",
                                "avg_strength": 81.0,
                                "symbols": ["005930", "000660"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    with sqlite3.connect(valuation_db) as conn:
        conn.executescript(
            """
            CREATE TABLE valuation_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                price REAL,
                market_cap_krw REAL,
                per REAL,
                eps REAL,
                pbr REAL,
                bps REAL,
                dividend_yield_pct REAL,
                industry_per REAL,
                industry_name TEXT NOT NULL DEFAULT '',
                as_of TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}',
                crawled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                error_message TEXT NOT NULL DEFAULT '',
                last_attempt_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE valuation_scores (
                symbol TEXT PRIMARY KEY,
                undervalued_score REAL NOT NULL DEFAULT 0,
                overvalued_risk REAL NOT NULL DEFAULT 0,
                quality_score REAL NOT NULL DEFAULT 0,
                growth_score REAL NOT NULL DEFAULT 0,
                relative_per_discount_pct REAL,
                pbr_roe_fit REAL,
                label TEXT NOT NULL DEFAULT '',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                risks_json TEXT NOT NULL DEFAULT '[]',
                scored_at TEXT NOT NULL
            );
            """
        )
        for symbol, name, label, industry in [
            ("005930", "삼성전자", "undervalued", "반도체"),
            ("000660", "SK하이닉스", "fair", "반도체"),
        ]:
            conn.execute(
                """
                INSERT INTO valuation_snapshots (
                    symbol, name, price, market_cap_krw, per, pbr,
                    industry_per, industry_name, crawled_at, status
                )
                VALUES (?, ?, 70000, 400000000000000, 12.0, 1.2, 18.0, ?, ?, 'ok')
                """,
                (symbol, name, industry, "2026-06-01T00:30:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO valuation_scores (
                    symbol, undervalued_score, overvalued_risk, quality_score,
                    growth_score, relative_per_discount_pct, pbr_roe_fit,
                    label, scored_at
                )
                VALUES (?, 72, 25, 68, 55, 20, 1.1, ?, ?)
                """,
                (symbol, label, "2026-06-01T00:30:00+00:00"),
            )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(kis_db)
        binance_block_trader_db_path = str(tmp_path / "missing_binance.db")
        market_pulse_db_path = str(market_pulse_db)
        valuation_db_path = str(valuation_db)
        trading_validation_max_age_sec = 864000000

    result = asyncio.run(run_live_evaluator_once(Settings()))
    metrics = result["validation"]["kis"]["metrics"]

    assert metrics["regime_scorecards"]["status"] == "warn"
    assert metrics["regime_scorecards"]["regime_count"] == 1
    assert metrics["correlation_proxy"]["status"] != "missing"
    assert metrics["factor_exposure"]["status"] != "missing"
    latest = sqlite3.connect(tmp_path / "live_performance.db")
    latest.row_factory = sqlite3.Row
    source = latest.execute(
        """
        SELECT source_json
        FROM live_block_performance
        WHERE venue = 'kis' AND block_id = 'kis-005930'
        """
    ).fetchone()["source_json"]
    metadata = json.loads(source)["metadata"]
    assert metadata["market_regime_source"] == "market_pulse_db"
    assert metadata["sector"] == "반도체"
    assert metadata["factor_exposures"]["valuation_undervalued"] > 0


def test_kis_enrichment_does_not_invent_correlation_cluster_without_sector() -> None:
    metadata = _enriched_kis_validation_metadata(
        block={"block_id": "kis-unknown", "symbol": "123456", "name": "테스트"},
        metadata={},
        entry_price=10_000,
        exit_price=10_500,
        qty=1,
        costs={
            "status": "estimated_from_notional",
            "source": "test",
            "fees": 0,
            "taxes": 0,
            "slippage": 0,
        },
        market_pulse={"status": "ok", "regime": "rotation", "sectors": {"items": []}},
        valuation_context={},
    )

    assert metadata["regime"] == "rotation"
    assert "correlation_cluster" not in metadata
    assert "asset_cluster" not in metadata
    assert metadata["factor_exposures"]["kr_equity_beta"] == 1.0


def test_kis_quote_context_adds_return_window_for_correlation_proxy(
    tmp_path: Path,
) -> None:
    kis_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                price REAL,
                source TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                error_message TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        for index, price in enumerate([100.0, 101.0, 100.5, 102.0]):
            conn.execute(
                """
                INSERT INTO quote_snapshots (
                    symbol, name, price, source, fetched_at, status
                )
                VALUES ('005930', '삼성전자', ?, 'kis_test', ?, 'ok')
                """,
                (price, f"2026-06-01T00:0{index}:00+00:00"),
            )

    class Settings:
        market_judge_db_path = str(tmp_path / "missing_market_judgment.db")
        kis_block_trader_db_path = str(kis_db)

    context = _latest_kis_quote_context(Settings(), symbol="005930")
    metadata = _enriched_kis_validation_metadata(
        block={"block_id": "kis-005930", "symbol": "005930", "name": "삼성전자"},
        metadata={},
        entry_price=100.0,
        exit_price=102.0,
        qty=1,
        costs={
            "status": "estimated_from_notional",
            "source": "test",
            "fees": 0,
            "taxes": 0,
            "slippage": 0,
            "spread": 0,
        },
        market_pulse={},
        valuation_context={},
        quote_context=context,
    )

    assert context["price"] == pytest.approx(102.0)
    assert context["return_window_sample_count"] == 3
    assert metadata["return_window_source"] == "kis_block_quote_turnover"
    assert len(metadata["return_window_pct"]) == 3
    assert "correlation_cluster" not in metadata


def test_kis_validation_costs_do_not_apply_stock_tax_to_etf_blocks() -> None:
    class Settings:
        kis_validation_buy_fee_rate = 0.00015
        kis_validation_sell_fee_rate = 0.00015
        kis_validation_sell_tax_rate = 0.002
        kis_validation_slippage_bps = 5.0

    etf_costs = _kis_validation_costs(
        block={"symbol": "069500", "name": "KODEX 200"},
        metadata={},
        entry_price=30_000,
        exit_price=31_000,
        qty=10,
        settings=Settings(),
    )
    stock_costs = _kis_validation_costs(
        block={"symbol": "005930", "name": "삼성전자"},
        metadata={},
        entry_price=70_000,
        exit_price=73_000,
        qty=10,
        settings=Settings(),
    )

    assert etf_costs["taxes"] == 0
    assert etf_costs["tax_exempt_reason"] == "etf"
    assert stock_costs["taxes"] > 0


def test_kis_validation_costs_separate_spread_from_slippage() -> None:
    class Settings:
        kis_validation_buy_fee_rate = 0.00015
        kis_validation_sell_fee_rate = 0.00015
        kis_validation_sell_tax_rate = 0.002
        kis_validation_slippage_bps = 5.0
        kis_validation_spread_bps = 2.0

    costs = _kis_validation_costs(
        block={"symbol": "277810", "name": "레인보우로보틱스"},
        metadata={},
        entry_price=100_000,
        exit_price=110_000,
        qty=1,
        settings=Settings(),
    )

    assert costs["status"] == "estimated_from_notional"
    assert costs["slippage"] == pytest.approx(105.0)
    assert costs["spread"] == pytest.approx(42.0)
    assert costs["funding"] == 0.0
    assert costs["total"] == pytest.approx(
        costs["fees"] + costs["taxes"] + costs["slippage"] + costs["spread"]
    )


def test_kis_validation_costs_prefer_recorded_block_performance() -> None:
    class Settings:
        kis_validation_buy_fee_rate = 0.00015
        kis_validation_sell_fee_rate = 0.00015
        kis_validation_sell_tax_rate = 0.002
        kis_validation_slippage_bps = 5.0

    costs = _kis_validation_costs(
        block={"symbol": "277810", "name": "레인보우로보틱스"},
        metadata={
            "performance": {
                "cost_model_status": "estimated_from_notional",
                "cost_source": "kis_validation_cost_model",
                "cost_components": {
                    "fees": 31.65,
                    "taxes": 221.0,
                    "slippage": 105.5,
                    "spread": 42.2,
                    "funding": 0.0,
                },
                "total_cost_krw": 400.35,
                "round_trip_notional_krw": 211_000,
                "buy_notional_krw": 100_500,
                "sell_notional_krw": 110_500,
            }
        },
        entry_price=100_500,
        exit_price=110_500,
        qty=1,
        settings=Settings(),
    )

    assert costs["status"] == "estimated_from_notional"
    assert costs["source"] == "kis_validation_cost_model"
    assert costs["performance_metadata_source"] == "block_metadata_performance"
    assert costs["fees"] == 31.65
    assert costs["taxes"] == 221.0
    assert costs["slippage"] == 105.5
    assert costs["spread"] == 42.2
    assert costs["funding"] == 0.0
    assert costs["total"] == 400.35


def test_kis_validation_costs_preserve_explicit_order_payload_provenance() -> None:
    class Settings:
        kis_validation_buy_fee_rate = 0.00015
        kis_validation_sell_fee_rate = 0.00015
        kis_validation_sell_tax_rate = 0.002
        kis_validation_slippage_bps = 5.0

    costs = _kis_validation_costs(
        block={"symbol": "277810", "name": "레인보우로보틱스"},
        metadata={
            "performance": {
                "cost_model_status": "explicit_order_costs_plus_estimated_market_costs",
                "cost_source": "kis_order_payload",
                "cost_components": {
                    "fees": 20.0,
                    "taxes": 150.0,
                    "slippage": 105.5,
                    "spread": 0.0,
                    "funding": 0.0,
                },
                "explicit_components": {"fees": 20.0, "taxes": 150.0},
                "estimated_components": {
                    "fees": 31.65,
                    "taxes": 221.0,
                    "slippage": 105.5,
                    "spread": 0.0,
                    "funding": 0.0,
                },
                "component_sources": {"fees": "fee_krw", "taxes": "tax_krw"},
                "total_cost_krw": 275.5,
                "round_trip_notional_krw": 211_000,
            }
        },
        entry_price=100_500,
        exit_price=110_500,
        qty=1,
        settings=Settings(),
    )

    assert costs["status"] == "explicit_order_costs_plus_estimated_market_costs"
    assert costs["source"] == "kis_order_payload"
    assert costs["performance_metadata_source"] == "block_metadata_performance"
    assert costs["explicit_components"] == {"fees": 20.0, "taxes": 150.0}
    assert costs["estimated_components"]["taxes"] == pytest.approx(221.0)
    assert costs["component_sources"] == {"fees": "fee_krw", "taxes": "tax_krw"}
    assert costs["total"] == pytest.approx(275.5)


def test_live_evaluator_backfills_kis_capacity_from_quote_turnover(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "live_evaluator.json"
    kis_db = tmp_path / "kis_blocks.db"
    market_judge_db = tmp_path / "market_judgment.db"
    with sqlite3.connect(kis_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
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
                qty INTEGER NOT NULL,
                limit_price INTEGER NOT NULL DEFAULT 0,
                order_type TEXT NOT NULL DEFAULT '00',
                status TEXT NOT NULL,
                order_no TEXT NOT NULL DEFAULT '',
                order_orgno TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                filled_qty INTEGER NOT NULL DEFAULT 0,
                remaining_qty INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL,
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for block_id, symbol, entry, exit_price in [
            ("kis-capacity-005930", "005930", 70_000, 73_500),
            ("kis-capacity-000660", "000660", 180_000, 189_000),
        ]:
            conn.execute(
                """
                INSERT INTO blocks (
                    block_id, symbol, qty_initial, qty_open, entry_price,
                    target_price, stop_price, created_by, status, metadata_json,
                    created_at, updated_at, opened_at, closed_at
                )
                VALUES (?, ?, 1, 0, ?, ?, ?, 'llm', 'closed', '{}', ?, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    entry,
                    exit_price,
                    entry * 0.95,
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO block_orders (
                    block_id, symbol, side, qty, limit_price, status,
                    filled_qty, remaining_qty,
                    avg_fill_price, created_at, updated_at
                )
                VALUES (?, ?, 'buy', 1, ?, 'filled', 1, 0, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    entry,
                    entry,
                    "2026-06-01T00:00:00+00:00",
                    "2026-06-01T00:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO block_orders (
                    block_id, symbol, side, qty, limit_price, status,
                    filled_qty, remaining_qty,
                    avg_fill_price, created_at, updated_at
                )
                VALUES (?, ?, 'sell', 1, ?, 'filled', 1, 0, ?, ?, ?)
                """,
                (
                    block_id,
                    symbol,
                    exit_price,
                    exit_price,
                    "2026-06-01T01:00:00+00:00",
                    "2026-06-01T01:00:00+00:00",
                ),
            )

    with sqlite3.connect(market_judge_db) as conn:
        conn.executescript(
            """
            CREATE TABLE quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                price REAL,
                change REAL,
                change_pct REAL,
                open_price REAL,
                high_price REAL,
                low_price REAL,
                volume REAL,
                trading_value REAL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        for symbol, trading_value in [("005930", 450_000_000_000), ("000660", 320_000_000_000)]:
            conn.execute(
                """
                INSERT INTO quote_snapshots (
                    symbol, name, price, volume, trading_value, source,
                    fetched_at, status, raw_json
                )
                VALUES (?, ?, 70000, 1000000, ?, 'kis', ?, 'ok', '{}')
                """,
                (symbol, symbol, trading_value, "2026-06-01T00:30:00+00:00"),
            )

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        trading_validation_db_path = str(tmp_path / "trading_validation.db")
        crypto_pattern_lab_db_path = str(tmp_path / "missing_pattern_lab.db")
        live_evaluator_state_path = str(state_path)
        live_authority_max_scale_multiplier = 1.5
        live_authority_min_samples_to_scale = 1
        kis_block_trader_db_path = str(kis_db)
        binance_block_trader_db_path = str(tmp_path / "missing_binance.db")
        market_judge_db_path = str(market_judge_db)
        market_pulse_db_path = str(tmp_path / "missing_market_pulse.db")
        valuation_db_path = str(tmp_path / "missing_valuation.db")
        trading_validation_max_age_sec = 864000000

    result = asyncio.run(run_live_evaluator_once(Settings()))
    metrics = result["validation"]["kis"]["metrics"]

    assert metrics["capacity"]["status"] != "missing"
    assert metrics["capacity"]["covered_sample_count"] == 2
    assert metrics["capacity"]["min_capacity_ratio"] > 20
    latest = sqlite3.connect(tmp_path / "live_performance.db")
    latest.row_factory = sqlite3.Row
    source = latest.execute(
        """
        SELECT source_json
        FROM live_block_performance
        WHERE venue = 'kis' AND block_id = 'kis-capacity-005930'
        """
    ).fetchone()["source_json"]
    metadata = json.loads(source)["metadata"]
    assert metadata["capacity_source"] == "market_judgment_quote_turnover"
    assert metadata["daily_turnover_krw"] == 450_000_000_000


def test_latest_order_price_reads_response_json_when_fill_columns_absent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE block_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id TEXT NOT NULL,
            side TEXT NOT NULL,
            response_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.execute(
        """
        INSERT INTO block_orders (block_id, side, response_json)
        VALUES (?, ?, ?)
        """,
        ("blk_binance_1", "buy", json.dumps({"avgPrice": "12.34"})),
    )

    price = _latest_order_price(
        conn,
        block_id="blk_binance_1",
        side="buy",
        default=10.0,
    )

    assert price == 12.34
