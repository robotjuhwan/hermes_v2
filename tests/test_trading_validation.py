from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    LivePerformanceRepository,
)
from tradecraft.services.crypto_pattern_lab import CryptoPatternLabRepository
from tradecraft.services.trading_validation import (
    DISCIPLINE_DEFINITIONS,
    TradingValidationConfig,
    TradingValidationRepository,
    TradingValidationService,
)


def _seed_live_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        ("b-1", "BTCUSDT", 100.0, 104.0, 1.0, 0.2),
        ("b-2", "ETHUSDT", 100.0, 97.0, 1.0, 0.2),
        ("b-3", "SOLUSDT", 100.0, 106.0, 1.0, 0.2),
        ("b-4", "BNBUSDT", 100.0, 99.0, 1.0, 0.2),
        ("b-5", "NEARUSDT", 100.0, 108.0, 1.0, 0.2),
        ("b-6", "XRPUSDT", 100.0, 103.0, 1.0, 0.2),
    ]
    for block_id, symbol, entry, exit_price, qty, fees in rows:
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=entry,
                exit_price=exit_price,
                qty=qty,
                fees=fees,
                filled=True,
            ),
            source={"test": True},
        )


def _seed_kis_live_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        ("kr-1", "005930", 76000.0, 77520.0, 1.0, 180.0),
        ("kr-2", "000660", 180000.0, 176400.0, 1.0, 410.0),
        ("kr-3", "277810", 100000.0, 104500.0, 1.0, 250.0),
        ("kr-4", "005830", 326400.0, 323136.0, 1.0, 900.0),
        ("kr-5", "033790", 12300.0, 12792.0, 10.0, 140.0),
        ("kr-6", "009450", 64500.0, 65145.0, 2.0, 120.0),
    ]
    for block_id, symbol, entry, exit_price, qty, fees in rows:
        metadata = {
            "market_regime": "kr_rotation",
            "factor_exposures": {"kr_equity_beta": 1.0},
            "capacity_krw": 50_000_000,
            "notional_krw": entry * qty,
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
                qty=qty,
                fees=fees,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_metadata_rich_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        {
            "block_id": "m-1",
            "symbol": "BTCUSDT",
            "exit_price": 104.0,
            "regime": "risk_on",
            "cluster": "major",
            "factor_exposures": {"momentum": 0.45, "quality": 0.2},
            "capacity_usdt": 10_000,
            "notional_usdt": 100,
        },
        {
            "block_id": "m-2",
            "symbol": "ETHUSDT",
            "exit_price": 97.0,
            "regime": "risk_off",
            "cluster": "major",
            "factor_exposures": {"momentum": 0.2, "value": 0.3},
            "capacity_usdt": 8_000,
            "notional_usdt": 120,
        },
        {
            "block_id": "m-3",
            "symbol": "SOLUSDT",
            "exit_price": 106.0,
            "regime": "risk_on",
            "cluster": "l1_alt",
            "factor_exposures": {"momentum": 0.35, "growth": 0.25},
            "capacity_usdt": 7_500,
            "notional_usdt": 90,
        },
        {
            "block_id": "m-4",
            "symbol": "AAVEUSDT",
            "exit_price": 99.0,
            "regime": "choppy",
            "cluster": "defi",
            "factor_exposures": {"value": 0.4, "quality": 0.25},
            "capacity_usdt": 6_000,
            "notional_usdt": 100,
        },
        {
            "block_id": "m-5",
            "symbol": "NEARUSDT",
            "exit_price": 108.0,
            "regime": "risk_on",
            "cluster": "l1_alt",
            "factor_exposures": {"growth": 0.35, "momentum": 0.25},
            "capacity_usdt": 9_000,
            "notional_usdt": 100,
        },
        {
            "block_id": "m-6",
            "symbol": "LINKUSDT",
            "exit_price": 103.0,
            "regime": "risk_off",
            "cluster": "oracle",
            "factor_exposures": {"quality": 0.35, "value": 0.2},
            "capacity_usdt": 5_000,
            "notional_usdt": 110,
        },
    ]
    for row in rows:
        metadata = {
            "side": "long",
            "regime": row["regime"],
            "correlation_cluster": row["cluster"],
            "factor_exposures": row["factor_exposures"],
            "capacity_usdt": row["capacity_usdt"],
            "notional_usdt": row["notional_usdt"],
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=row["block_id"],
                symbol=row["symbol"],
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=row["exit_price"],
                qty=1.0,
                fees=0.2,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_data_quality_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        {
            "block_id": "dq-1",
            "symbol": "BTCUSDT",
            "entry_price": 100.0,
            "exit_price": 104.0,
            "fees": 0.2,
            "metadata": {
                "quote_status": "ok",
                "quote_stale": False,
                "quote_source": "binance_book",
            },
        },
        {
            "block_id": "dq-2",
            "symbol": "ETHUSDT",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "fees": 0.2,
            "metadata": {
                "quote_status": "stale",
                "quote_stale": True,
                "quote_source": "stale_research_book",
            },
        },
        {
            "block_id": "dq-3",
            "symbol": "SOLUSDT",
            "entry_price": 100.0,
            "exit_price": 101.0,
            "fees": 0.0,
            "metadata": {
                "quote_status": "error",
                "quote_source": "fallback_proxy",
                "error_message": "orderbook unavailable",
            },
        },
        {
            "block_id": "dq-4",
            "symbol": "BNBUSDT",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "fees": 0.0,
            "metadata": {
                "quote_status": "ok",
                "quote_source": "binance_book",
                "cost_model_status": "missing",
            },
        },
        {
            "block_id": "dq-5",
            "symbol": "XRPUSDT",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "fees": 0.2,
            "metadata": {
                "quote_status": "ok",
                "quote_source": "binance_book",
            },
        },
    ]
    for row in rows:
        metadata = row["metadata"]
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=row["block_id"],
                symbol=row["symbol"],
                created_by="llm",
                status="closed",
                entry_price=row["entry_price"],
                exit_price=row["exit_price"],
                qty=1.0,
                fees=row["fees"],
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_cost_simulation_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        {
            "block_id": "cost-1",
            "symbol": "BTCUSDT",
            "exit_price": 105.0,
            "fees": 0.2,
            "slippage": 0.1,
            "metadata": {
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2, "spread": 0.0, "slippage": 0.1},
                "horizon": "mid",
                "strategy_family": "trend_pullback",
            },
        },
        {
            "block_id": "cost-2",
            "symbol": "ETHUSDT",
            "exit_price": 104.0,
            "fees": 0.2,
            "funding": 0.1,
            "metadata": {
                "cost_model_status": "recorded",
                "cost_components": {
                    "fees": 0.2,
                    "funding": 0.1,
                    "spread": 0.0,
                    "slippage": 0.0,
                },
                "horizon": "mid",
                "strategy_family": "trend_pullback",
            },
        },
        {
            "block_id": "cost-3",
            "symbol": "SOLUSDT",
            "exit_price": 102.0,
            "fees": 1.3,
            "slippage": 0.6,
            "metadata": {
                "cost_model_status": "recorded",
                "cost_components": {"fees": 1.3, "spread": 0.0, "slippage": 0.6},
                "horizon": "short",
                "strategy_family": "scalp",
            },
        },
        {
            "block_id": "cost-4",
            "symbol": "BNBUSDT",
            "exit_price": 101.0,
            "fees": 0.0,
            "metadata": {"cost_model_status": "missing", "horizon": "short"},
        },
    ]
    for row in rows:
        metadata = row["metadata"]
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=row["block_id"],
                symbol=row["symbol"],
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=row["exit_price"],
                qty=1.0,
                fees=row.get("fees", 0.0),
                funding=row.get("funding", 0.0),
                slippage=row.get("slippage", 0.0),
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def test_validation_reads_cost_status_columns_when_source_metadata_is_missing(
    tmp_path: Path,
) -> None:
    repo = LivePerformanceRepository(tmp_path / "live_performance.db")
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="binance",
            block_id="column-cost-status",
            symbol="BTCUSDT",
            created_by="llm",
            status="closed",
            entry_price=100.0,
            exit_price=102.0,
            qty=1.0,
            fees=0.2,
            filled=True,
            metadata={
                "cost_model_status": "recorded",
                "cost_source": "exchange_fill",
                "fill_evidence_status": "order_round_trip_filled",
                "cost_components": {
                    "fees": 0.2,
                    "spread": 0.0,
                    "slippage": 0.0,
                },
            },
        ),
        source={},
    )
    row = repo.latest(venue="binance", limit=1)[0]
    service = TradingValidationService(
        config=TradingValidationConfig(
            validation_db_path=tmp_path / "validation.db",
            live_performance_db_path=tmp_path / "live_performance.db",
        )
    )

    data_quality = service._data_quality_metrics([row])
    cost_simulation = service._cost_simulation_metrics([row])

    assert data_quality["missing_cost_count"] == 0
    assert data_quality["status"] == "pass"
    assert cost_simulation["missing_cost_sample_count"] == 0
    assert cost_simulation["recorded_cost_sample_count"] == 1


def _seed_sparse_cost_simulation_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "NEARUSDT", "XRPUSDT"],
        start=1,
    ):
        metadata = {"cost_model_status": "missing"}
        fees = 0.0
        if index == 1:
            metadata = {
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2, "spread": 0.0, "slippage": 0.1},
            }
            fees = 0.2
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"sparse-cost-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0 if index % 2 else 99.0,
                qty=1.0,
                fees=fees,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_estimated_cost_simulation_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
        start=1,
    ):
        metadata = {
            "cost_model_status": "estimated_from_notional",
            "cost_components": {"fees": 0.2, "spread": 0.0, "slippage": 0.1},
            "market": "spot",
            "side": "long",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"estimated-cost-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=104.0,
                qty=1.0,
                fees=0.2,
                slippage=0.1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_mixed_cost_simulation_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(
        ["005930", "000660", "277810", "402340"],
        start=1,
    ):
        metadata = {
            "cost_model_status": "explicit_order_costs_plus_estimated_market_costs",
            "cost_source": "kis_order_payload",
            "cost_components": {
                "fees": 20.0,
                "taxes": 150.0,
                "slippage": 105.5,
                "spread": 42.2,
                "funding": 0.0,
            },
            "component_sources": {"fees": "fee_krw", "taxes": "tax_krw"},
            "horizon": "mid",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"mixed-cost-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100_000.0,
                exit_price=102_000.0,
                qty=1.0,
                fees=20.0,
                taxes=150.0,
                slippage=105.5,
                spread=42.2,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_high_edge_kelly_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        ("kelly-1", "BTCUSDT", 108.0),
        ("kelly-2", "ETHUSDT", 98.0),
        ("kelly-3", "SOLUSDT", 107.0),
        ("kelly-4", "BNBUSDT", 104.0),
        ("kelly-5", "NEARUSDT", 99.0),
        ("kelly-6", "XRPUSDT", 105.0),
    ]
    for block_id, symbol, exit_price in rows:
        metadata = {
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
            "cost_model_status": "recorded",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_drawdown_budget_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        ("dd-1", "BTCUSDT", 100.0, 115.0),
        ("dd-2", "ETHUSDT", 100.0, 110.0),
        ("dd-3", "SOLUSDT", 100.0, 75.0),
        ("dd-4", "BNBUSDT", 100.0, 90.0),
    ]
    for block_id, symbol, entry, exit_price in rows:
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=entry,
                exit_price=exit_price,
                qty=100.0,
                fees=0.0,
                filled=True,
            ),
            source={"test": True},
        )


def _seed_profit_recovery_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        ("pr-1", "BTCUSDT", 100.0, 110.0),
        ("pr-2", "ETHUSDT", 100.0, 80.0),
        ("pr-3", "SOLUSDT", 100.0, 115.0),
        ("pr-4", "BNBUSDT", 100.0, 107.0),
    ]
    for block_id, symbol, entry, exit_price in rows:
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=entry,
                exit_price=exit_price,
                qty=100.0,
                fees=0.0,
                filled=True,
            ),
            source={"test": True},
        )


def _seed_failure_attribution_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        {
            "block_id": "fa-loss-1",
            "symbol": "005930",
            "exit_price": 98.0,
            "metadata": {
                "horizon": "short",
                "strategy_family": "late_chase",
                "market_regime": "rotation",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2, "slippage": 0.3},
            },
        },
        {
            "block_id": "fa-loss-2",
            "symbol": "005930",
            "exit_price": 97.0,
            "metadata": {
                "horizon": "short",
                "strategy_family": "late_chase",
                "market_regime": "rotation",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2, "slippage": 0.4},
            },
        },
        {
            "block_id": "fa-loss-3",
            "symbol": "005930",
            "exit_price": 99.0,
            "metadata": {
                "horizon": "short",
                "strategy_family": "late_chase",
                "market_regime": "rotation",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2, "slippage": 0.2},
            },
        },
        {
            "block_id": "fa-win-1",
            "symbol": "000660",
            "exit_price": 104.0,
            "metadata": {
                "horizon": "mid",
                "strategy_family": "pullback_reclaim",
                "market_regime": "risk_on",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2},
            },
        },
        {
            "block_id": "fa-win-2",
            "symbol": "000660",
            "exit_price": 103.0,
            "metadata": {
                "horizon": "mid",
                "strategy_family": "pullback_reclaim",
                "market_regime": "risk_on",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2},
            },
        },
        {
            "block_id": "fa-win-3",
            "symbol": "000660",
            "exit_price": 105.0,
            "metadata": {
                "horizon": "mid",
                "strategy_family": "pullback_reclaim",
                "market_regime": "risk_on",
                "cost_model_status": "recorded",
                "cost_components": {"fees": 0.2},
            },
        },
    ]
    for row in rows:
        metadata = row["metadata"]
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=row["block_id"],
                symbol=row["symbol"],
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=row["exit_price"],
                qty=10.0,
                fees=metadata["cost_components"]["fees"],
                slippage=metadata["cost_components"].get("slippage", 0.0),
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_ruin_profile_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        ("ruin-1", "BTCUSDT", 100.0, 96.0),
        ("ruin-2", "ETHUSDT", 100.0, 94.0),
        ("ruin-3", "SOLUSDT", 100.0, 98.0),
        ("ruin-4", "BNBUSDT", 100.0, 103.0),
        ("ruin-5", "NEARUSDT", 100.0, 95.0),
    ]
    for block_id, symbol, entry, exit_price in rows:
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=entry,
                exit_price=exit_price,
                qty=1.0,
                fees=0.0,
                filled=True,
            ),
            source={"test": True},
        )


def _seed_crisis_stress_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        {
            "block_id": "stress-1",
            "symbol": "BTCUSDT",
            "exit_price": 103.0,
            "crisis_returns_pct": {
                "covid_liquidity_crash": -7.0,
                "luna_depeg": -10.0,
                "ftx_credit_event": -5.0,
            },
        },
        {
            "block_id": "stress-2",
            "symbol": "ETHUSDT",
            "exit_price": 102.0,
            "crisis_returns_pct": {
                "covid_liquidity_crash": -8.0,
                "luna_depeg": -12.0,
                "ftx_credit_event": -6.0,
            },
        },
        {
            "block_id": "stress-3",
            "symbol": "SOLUSDT",
            "exit_price": 101.0,
            "crisis_returns_pct": {
                "covid_liquidity_crash": -12.0,
                "luna_depeg": -18.0,
                "ftx_credit_event": -9.0,
            },
        },
        {
            "block_id": "stress-4",
            "symbol": "BNBUSDT",
            "exit_price": 101.0,
            "crisis_returns_pct": {
                "covid_liquidity_crash": 3.0,
                "luna_depeg": -6.0,
                "ftx_credit_event": 2.0,
            },
        },
    ]
    for row in rows:
        metadata = {"crisis_returns_pct": row["crisis_returns_pct"]}
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=row["block_id"],
                symbol=row["symbol"],
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=row["exit_price"],
                qty=1.0,
                fees=0.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_sparse_crisis_stress_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "NEARUSDT", "XRPUSDT"],
        start=1,
    ):
        metadata = {}
        if index == 1:
            metadata = {
                "crisis_returns_pct": {
                    "covid_liquidity_crash": -1.0,
                    "luna_depeg": -2.0,
                    "ftx_credit_event": -1.5,
                }
            }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"sparse-stress-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0 if index % 2 else 99.0,
                qty=1.0,
                fees=0.1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_capacity_curve_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        {
            "block_id": "cap-1",
            "symbol": "BTCUSDT",
            "notional": 100.0,
            "depth_by_bps": {"10": 800.0, "30": 1_500.0, "50": 2_400.0},
            "turnover": 200_000.0,
            "participation": 0.01,
        },
        {
            "block_id": "cap-2",
            "symbol": "ALTUSDT",
            "notional": 120.0,
            "depth_by_bps": {"10": 80.0, "30": 350.0, "50": 620.0},
            "turnover": 15_000.0,
            "participation": 0.01,
        },
        {
            "block_id": "cap-3",
            "symbol": "NEARUSDT",
            "notional": 90.0,
            "depth_by_bps": {"10": 500.0, "30": 900.0, "50": 1_200.0},
            "turnover": 80_000.0,
            "participation": 0.01,
        },
    ]
    for row in rows:
        metadata = {
            "block_notional_usdt": row["notional"],
            "orderbook_depth_usdt_by_bps": row["depth_by_bps"],
            "daily_turnover_usdt": row["turnover"],
            "max_participation_rate": row["participation"],
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=row["block_id"],
                symbol=row["symbol"],
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0,
                qty=1.0,
                fees=0.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_sparse_capacity_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "NEARUSDT", "XRPUSDT"],
        start=1,
    ):
        metadata = {}
        if index == 1:
            metadata = {
                "capacity_usdt": 100_000.0,
                "notional_usdt": 1_000.0,
            }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"sparse-cap-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0 if index % 2 else 99.0,
                qty=1.0,
                fees=0.1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_low_metadata_capacity_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(["005930", "000660", "277810"], start=1):
        metadata = {
            "capacity_krw": 1_000_000.0,
            "notional_krw": 100_000_000.0,
            "capacity_source": "symbol_metadata_proxy",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"metadata-cap-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100_000.0,
                exit_price=103_000.0,
                qty=1.0,
                fees=200.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_sparse_factor_exposure_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "NEARUSDT", "XRPUSDT"],
        start=1,
    ):
        metadata = {}
        if index == 1:
            metadata = {
                "factor_exposures": {"momentum": 0.45, "value": 0.35},
                "position_notional": 1_000.0,
            }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"sparse-factor-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0 if index % 2 else 99.0,
                qty=1.0,
                fees=0.1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_sparse_correlation_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    series_by_symbol = {
        "BTCUSDT": [1.0, 2.0, 3.0, 4.0, 5.0],
        "ETHUSDT": [2.0, 5.0, 1.0, 4.0, 3.0],
    }
    for index, symbol in enumerate(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "NEARUSDT", "XRPUSDT"],
        start=1,
    ):
        metadata = {}
        if symbol in series_by_symbol:
            metadata = {"return_window_pct": series_by_symbol[symbol]}
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"sparse-corr-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0 if index % 2 else 99.0,
                qty=1.0,
                fees=0.1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_single_symbol_correlation_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    for index, symbol in enumerate(["005930", "000660", "009150"], start=1):
        metadata = {}
        if index == 1:
            metadata = {
                "return_window_pct": [0.2, -0.1, 0.4, 0.3, -0.2],
            }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"single-corr-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0 if index % 2 else 99.0,
                qty=1.0,
                fees=0.1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_sparse_regime_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    regimes_by_symbol = {
        "BTCUSDT": "risk_on",
        "ETHUSDT": "choppy",
    }
    for index, symbol in enumerate(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "NEARUSDT", "XRPUSDT"],
        start=1,
    ):
        metadata = {}
        if symbol in regimes_by_symbol:
            metadata = {"market_regime": regimes_by_symbol[symbol]}
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"sparse-regime-{index}",
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=103.0,
                qty=1.0,
                fees=0.1,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_regime_correlation_factor_performance(path: Path) -> None:
    repo = LivePerformanceRepository(path)
    rows = [
        {
            "block_id": "rcf-1",
            "symbol": "BTCUSDT",
            "exit_price": 105.0,
            "regime": "risk_on",
            "return_window_pct": [1.0, 2.0, -0.5, 2.5, 1.2],
            "factor_exposures": {"momentum": 0.8, "quality": 0.1},
            "notional": 100.0,
        },
        {
            "block_id": "rcf-2",
            "symbol": "ETHUSDT",
            "exit_price": 104.0,
            "regime": "risk_on",
            "return_window_pct": [0.9, 1.8, -0.4, 2.3, 1.0],
            "factor_exposures": {"momentum": 0.7, "growth": 0.1},
            "notional": 120.0,
        },
        {
            "block_id": "rcf-3",
            "symbol": "SOLUSDT",
            "exit_price": 93.0,
            "regime": "risk_off",
            "return_window_pct": [1.5, -0.8, 0.4, -0.2, 0.6],
            "factor_exposures": {"momentum": 0.6, "growth": 0.2},
            "notional": 90.0,
        },
        {
            "block_id": "rcf-4",
            "symbol": "BNBUSDT",
            "exit_price": 101.0,
            "regime": "choppy",
            "return_window_pct": [-0.3, 0.2, -0.1, 0.3, -0.2],
            "factor_exposures": {"momentum": 0.5, "value": 0.1},
            "notional": 80.0,
        },
    ]
    for row in rows:
        metadata = {
            "market_regime": row["regime"],
            "return_window_pct": row["return_window_pct"],
            "factor_exposures": row["factor_exposures"],
            "position_notional": row["notional"],
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=row["block_id"],
                symbol=row["symbol"],
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=row["exit_price"],
                qty=1.0,
                fees=0.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )


def _seed_pattern_lab(path: Path) -> None:
    repo = CryptoPatternLabRepository(path)
    repo.save_patterns(
        [
            {
                "pattern_id": "p-good",
                "source_id": "source-good",
                "name": "Good trend",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["ema"],
                "expression": {},
                "risk_tags": ["trend"],
            },
            {
                "pattern_id": "p-overfit",
                "source_id": "source-overfit",
                "name": "Overfit breakout",
                "family": "breakout",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["high", "volume"],
                "expression": {},
                "risk_tags": ["breakout"],
            },
        ]
    )
    repo.save_optimization_result(
        {
            "run_id": "run-good",
            "pattern_id": "p-good",
            "symbol": "BTCUSDT",
            "interval": "15m",
            "objective": "risk_adjusted_net_r_v1",
            "status": "ok",
            "best": {
                "trial_id": "trial-good",
                "parameter_set": {
                    "stop_pct": 0.008,
                    "target_pct": 0.016,
                    "holding_bars": 10,
                },
                "trade_count": 42,
                "win_rate": 0.58,
                "expectancy_r": 0.31,
                "profit_factor": 1.55,
                "max_loss_r": -1.0,
                "objective_score": 88.0,
                "in_sample": {
                    "sample_start": "1",
                    "sample_end": "300",
                    "expectancy_r": 0.31,
                },
                "out_of_sample": {
                    "sample_start": "301",
                    "sample_end": "420",
                    "trade_count": 14,
                    "expectancy_r": 0.12,
                    "profit_factor": 1.18,
                    "max_drawdown_r": -2.4,
                },
                "walk_forward": {
                    "passed": True,
                    "window_count": 4,
                    "passed_window_count": 3,
                    "reasons": ["rolling_oos_positive"],
                },
            },
            "trials": [],
        }
    )
    repo.save_optimization_result(
        {
            "run_id": "run-overfit",
            "pattern_id": "p-overfit",
            "symbol": "ALTUSDT",
            "interval": "15m",
            "objective": "risk_adjusted_net_r_v1",
            "status": "ok",
            "best": {
                "trial_id": "trial-overfit",
                "parameter_set": {
                    "stop_pct": 0.01,
                    "target_pct": 0.02,
                    "holding_bars": 8,
                },
                "trade_count": 40,
                "win_rate": 0.62,
                "expectancy_r": 0.35,
                "profit_factor": 1.6,
                "max_loss_r": -1.0,
                "objective_score": 82.0,
                "in_sample": {"expectancy_r": 0.35},
                "out_of_sample": {
                    "trade_count": 12,
                    "expectancy_r": -0.05,
                    "profit_factor": 0.92,
                    "max_drawdown_r": -3.2,
                },
                "walk_forward": {
                    "passed": False,
                    "window_count": 4,
                    "passed_window_count": 1,
                    "reasons": ["oos_negative"],
                },
            },
            "trials": [],
        }
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            UPDATE optimized_strategy_sets
            SET walk_forward_quality_json = ?
            WHERE symbol = ?
            """,
            (
                (
                    '{"passed": true, "window_count": 4, '
                    '"passed_window_count": 3, '
                    '"reasons": ["rolling_oos_positive"]}'
                ),
                "BTCUSDT",
            ),
        )
        conn.execute(
            """
            UPDATE optimized_strategy_sets
            SET walk_forward_quality_json = ?
            WHERE symbol = ?
            """,
            (
                (
                    '{"passed": false, "window_count": 4, '
                    '"passed_window_count": 1, '
                    '"reasons": ["oos_negative"]}'
                ),
                "ALTUSDT",
            ),
        )


def _seed_kr_equity_pattern_lab(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE optimized_strategy_sets (
                set_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL DEFAULT '',
                family TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                objective_score REAL NOT NULL DEFAULT 0,
                in_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_trade_count INTEGER NOT NULL DEFAULT 0,
                out_of_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_profit_factor REAL NOT NULL DEFAULT 0,
                out_of_sample_max_drawdown_r REAL NOT NULL DEFAULT 0,
                overfit_risk TEXT NOT NULL DEFAULT '',
                walk_forward_quality_json TEXT NOT NULL DEFAULT '{}',
                promoted_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO optimized_strategy_sets (
                set_id, symbol, interval, family, direction, status,
                objective_score, in_sample_expectancy_r,
                out_of_sample_trade_count, out_of_sample_expectancy_r,
                out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                overfit_risk, walk_forward_quality_json, promoted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "kr-set-value-cycle",
                    "005930",
                    "1d",
                    "value_cycle",
                    "long",
                    "active",
                    78.0,
                    0.24,
                    18,
                    0.11,
                    1.22,
                    -2.6,
                    "low",
                    (
                        '{"passed": true, "window_count": 4, '
                        '"passed_window_count": 3, '
                        '"reasons": ["rolling_oos_positive"]}'
                    ),
                    "2026-06-14T00:00:00+00:00",
                ),
                (
                    "kr-set-chase-reject",
                    "277810",
                    "1d",
                    "extended_momentum",
                    "long",
                    "rejected",
                    42.0,
                    0.31,
                    9,
                    -0.08,
                    0.82,
                    -4.2,
                    "high",
                    (
                        '{"passed": false, "window_count": 4, '
                        '"passed_window_count": 1, '
                        '"reasons": ["oos_negative"]}'
                    ),
                    "2026-06-13T00:00:00+00:00",
                ),
            ],
        )


def _seed_rejected_kr_equity_pattern_lab(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE optimized_strategy_sets (
                set_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL DEFAULT '',
                family TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                objective_score REAL NOT NULL DEFAULT 0,
                in_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_trade_count INTEGER NOT NULL DEFAULT 0,
                out_of_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_profit_factor REAL NOT NULL DEFAULT 0,
                out_of_sample_max_drawdown_r REAL NOT NULL DEFAULT 0,
                overfit_risk TEXT NOT NULL DEFAULT '',
                walk_forward_quality_json TEXT NOT NULL DEFAULT '{}',
                promoted_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO optimized_strategy_sets (
                set_id, symbol, interval, family, direction, status,
                objective_score, in_sample_expectancy_r,
                out_of_sample_trade_count, out_of_sample_expectancy_r,
                out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                overfit_risk, walk_forward_quality_json, promoted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "kr-rejected-samsung",
                    "005930",
                    "1d",
                    "value_cycle",
                    "long",
                    "rejected",
                    0.0,
                    0.02,
                    2,
                    -0.015,
                    0.0,
                    -0.02,
                    "medium",
                    (
                        '{"passed": false, "window_count": 2, '
                        '"passed_window_count": 0, '
                        '"reasons": ["out_of_sample_expectancy_negative", '
                        '"out_of_sample_profit_factor_low"]}'
                    ),
                    "2026-06-14T12:00:00+00:00",
                ),
                (
                    "kr-rejected-sk",
                    "017670",
                    "1d",
                    "value_cycle",
                    "long",
                    "rejected",
                    0.0,
                    0.01,
                    2,
                    -0.011,
                    0.15,
                    -0.01,
                    "medium",
                    (
                        '{"passed": false, "window_count": 2, '
                        '"passed_window_count": 0, '
                        '"reasons": ["out_of_sample_expectancy_negative"]}'
                    ),
                    "2026-06-14T12:00:01+00:00",
                ),
            ],
        )


def _seed_unverified_active_pattern_lab(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE optimized_strategy_sets (
                set_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL DEFAULT '',
                family TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                objective_score REAL NOT NULL DEFAULT 0,
                in_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_trade_count INTEGER NOT NULL DEFAULT 0,
                out_of_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_profit_factor REAL NOT NULL DEFAULT 0,
                out_of_sample_max_drawdown_r REAL NOT NULL DEFAULT 0,
                overfit_risk TEXT NOT NULL DEFAULT '',
                walk_forward_quality_json TEXT NOT NULL DEFAULT '{}',
                promoted_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO optimized_strategy_sets (
                set_id, symbol, interval, family, direction, status,
                objective_score, in_sample_expectancy_r,
                out_of_sample_trade_count, out_of_sample_expectancy_r,
                out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                overfit_risk, walk_forward_quality_json, promoted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "unverified-aave",
                    "AAVEUSDT",
                    "1h",
                    "macd_momentum",
                    "short",
                    "active",
                    214.5,
                    0.45,
                    0,
                    0.0,
                    0.0,
                    0.0,
                    "unknown",
                    "{}",
                    "2026-06-14T09:54:01+00:00",
                ),
                (
                    "unverified-chip",
                    "CHIPUSDT",
                    "5m",
                    "ema_trend",
                    "long",
                    "active",
                    207.6,
                    0.38,
                    0,
                    0.0,
                    0.0,
                    0.0,
                    "unknown",
                    "{}",
                    "2026-06-14T09:54:00+00:00",
                ),
            ],
        )


def _seed_active_pattern_lab_without_wfa_windows(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE optimized_strategy_sets (
                set_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL DEFAULT '',
                family TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                objective_score REAL NOT NULL DEFAULT 0,
                in_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_trade_count INTEGER NOT NULL DEFAULT 0,
                out_of_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                out_of_sample_profit_factor REAL NOT NULL DEFAULT 0,
                out_of_sample_max_drawdown_r REAL NOT NULL DEFAULT 0,
                overfit_risk TEXT NOT NULL DEFAULT '',
                walk_forward_quality_json TEXT NOT NULL DEFAULT '{}',
                promoted_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO optimized_strategy_sets (
                set_id, symbol, interval, family, direction, status,
                objective_score, in_sample_expectancy_r,
                out_of_sample_trade_count, out_of_sample_expectancy_r,
                out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                overfit_risk, walk_forward_quality_json, promoted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-bool-only",
                "BTCUSDT",
                "15m",
                "ema_trend",
                "long",
                "active",
                91.0,
                0.34,
                24,
                0.18,
                1.36,
                -2.1,
                "low",
                '{"passed": true, "reasons": ["legacy_bool_only"]}',
                "2026-06-14T10:00:00+00:00",
            ),
        )


def test_validation_service_builds_19_discipline_packet(tmp_path: Path) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_live_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=5,
            monte_carlo_iterations=128,
            monte_carlo_seed=7,
        )
    )

    payload = service.run_once(venue="binance")

    assert payload["status"] == "ok"
    assert payload["venue"] == "binance"
    assert payload["discipline_count"] == 19
    assert len(payload["disciplines"]) == 19
    assert payload["metrics"]["sample_count"] == 6
    assert payload["metrics"]["profit_factor"] > 1.5
    assert payload["metrics"]["kelly_fraction"] > 0
    assert payload["metrics"]["fractional_kelly_025"] == pytest.approx(
        payload["metrics"]["kelly_fraction"] * 0.25
    )
    assert payload["monte_carlo"]["iterations"] == 128
    assert "risk_of_ruin_pct" in payload["metrics"]
    assert any(row["id"] == "monte_carlo" for row in payload["disciplines"])
    assert any(row["id"] == "sortino_ratio" for row in payload["disciplines"])


def test_validation_summary_normalizes_legacy_discipline_statuses(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(validation_db_path=tmp_path / "validation.db")
    )

    summary = service._summarize_disciplines(
        [
            {"status": "ok"},
            {"status": "warning"},
            {"status": "error"},
            {"status": "legacy_unverified"},
        ]
    )

    assert summary["total_score"] == 7.89
    assert summary["readiness"] == "blocked_by_validation"
    assert summary["pass_count"] == 1
    assert summary["warn_count"] == 1
    assert summary["fail_count"] == 1
    assert summary["missing_count"] == 16
    assert summary["hard_fail_count"] == 1
    assert summary["hard_blocking_count"] == 1


def test_validation_summary_keeps_diagnostic_failures_out_of_hard_gate(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(validation_db_path=tmp_path / "validation.db")
    )
    core_ids = {"data_validation", "capacity_analysis", "mdd_limit"}
    disciplines = [
        {
            "id": definition["id"],
            "status": "pass" if definition["id"] in core_ids else "fail",
        }
        for definition in DISCIPLINE_DEFINITIONS
    ]

    summary = service._summarize_disciplines(disciplines)

    assert summary["fail_count"] == 16
    assert summary["core_fail_count"] == 0
    assert summary["hard_fail_count"] == 0
    assert summary["hard_blocking_count"] == 0
    assert summary["readiness"] == "probe"
    assert summary["diagnostic_status"] == "risk_repair"


def test_validation_summary_blocks_when_operational_core_fails(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(validation_db_path=tmp_path / "validation.db")
    )
    disciplines = [
        {
            "id": definition["id"],
            "status": "fail" if definition["id"] == "data_validation" else "pass",
        }
        for definition in DISCIPLINE_DEFINITIONS
    ]

    summary = service._summarize_disciplines(disciplines)

    assert summary["fail_count"] == 1
    assert summary["core_fail_count"] == 1
    assert summary["hard_fail_count"] == 1
    assert summary["hard_blocking_count"] == 1
    assert summary["readiness"] == "blocked_by_validation"


def test_validation_summary_counts_absent_disciplines_as_missing(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(validation_db_path=tmp_path / "validation.db")
    )

    summary = service._summarize_disciplines([{"status": "pass"} for _ in range(10)])

    assert summary["pass_count"] == 10
    assert summary["missing_count"] == 9
    assert summary["total_score"] == pytest.approx(52.63)
    assert summary["readiness"] == "normal"


def test_validation_remediation_plan_surfaces_absent_disciplines() -> None:
    partial_disciplines = [
        {
            "id": definition["id"],
            "label": definition["label"],
            "status": "pass",
            "action": definition["purpose"],
        }
        for definition in DISCIPLINE_DEFINITIONS[:10]
    ]

    plan = TradingValidationService._build_remediation_plan(partial_disciplines)

    assert plan["status"] == "needs_work"
    assert plan["missing_count"] == 9
    assert plan["weak_count"] == 9
    assert plan["top_priority"][0]["status"] == "missing"
    assert any(
        item["discipline_id"] == "sharpe_ratio"
        for category in plan["categories"]
        for item in category["items"]
    )


def test_validation_remediation_plan_treats_core_missing_as_probe_gate() -> None:
    disciplines = [
        {
            "id": definition["id"],
            "label": definition["label"],
            "status": "pass"
            if definition["id"]
            not in {"data_validation", "capacity_analysis", "mdd_limit"}
            else "missing",
            "action": definition["purpose"],
        }
        for definition in DISCIPLINE_DEFINITIONS
    ]

    plan = TradingValidationService._build_remediation_plan(disciplines)
    hints = plan["lane_policy_hints"]
    queue_by_discipline = {
        row["discipline_id"]: row for row in plan["work_queue"]
    }

    assert plan["status"] == "needs_work"
    assert plan["failed_count"] == 0
    assert plan["missing_count"] == 3
    assert hints["version"] == "validation_lane_policy_hints_v2"
    assert hints["scale_up_allowed"] is False
    assert hints["entry_mode"] == "verified_waiting_probe"
    assert hints["requires_verified_quotes"] is True
    assert hints["requires_capacity_check"] is True
    assert hints["risk_budget_mode"] == "probe"
    assert set(hints["core_missing_ids"]) == {
        "data_validation",
        "capacity_analysis",
        "mdd_limit",
    }
    assert set(hints["scale_up_blocked_discipline_ids"]) == {
        "data_validation",
        "capacity_analysis",
        "mdd_limit",
    }
    assert queue_by_discipline["data_validation"]["priority"] == "p0"
    assert queue_by_discipline["capacity_analysis"]["validation_mode"] == (
        "capacity_depth_check"
    )
    assert queue_by_discipline["mdd_limit"]["validation_mode"] == (
        "risk_budget_recalibration"
    )


def test_validation_remediation_plan_keeps_non_blocking_warnings_active() -> None:
    disciplines = [
        {
            "id": definition["id"],
            "label": definition["label"],
            "status": "warn"
            if definition["id"] in {"sortino_ratio", "profit_factor"}
            else "pass",
            "action": definition["purpose"],
        }
        for definition in DISCIPLINE_DEFINITIONS
    ]

    plan = TradingValidationService._build_remediation_plan(disciplines)

    assert plan["status"] == "needs_work"
    assert plan["trade_blocking"] is False
    assert "좋은 위치의 대기진입 probe" in plan["primary_next_action"]
    assert "표본" in plan["primary_next_action"]


def test_validation_remediation_plan_distinguishes_diagnostic_failures_from_trade_blocking() -> None:
    core_ids = {"data_validation", "capacity_analysis", "mdd_limit"}
    disciplines = [
        {
            "id": definition["id"],
            "label": definition["label"],
            "status": "pass" if definition["id"] in core_ids else "fail",
            "action": definition["purpose"],
        }
        for definition in DISCIPLINE_DEFINITIONS
    ]

    plan = TradingValidationService._build_remediation_plan(disciplines)
    hints = plan["lane_policy_hints"]

    assert plan["status"] == "probe_rebuild"
    assert plan["trade_blocking"] is False
    assert plan["blocking_scope"] == "scale_up_only"
    assert "대기진입 probe" in plan["primary_next_action"]
    assert "표본을 쌓" in plan["primary_next_action"]
    assert hints["trade_blocking"] is False
    assert hints["blocking_scope"] == "scale_up_only"
    assert hints["entry_mode"] == "risk_off_recovery"


def test_validation_repository_returns_latest_run(tmp_path: Path) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_live_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=5,
            monte_carlo_iterations=32,
            monte_carlo_seed=11,
        )
    )
    generated = service.run_once(venue="binance")

    latest = TradingValidationRepository(validation_path).latest(venue="binance")

    assert latest["status"] == "ok"
    assert latest["run_id"] == generated["run_id"]
    assert latest["venue"] == "binance"
    assert latest["payload"]["discipline_count"] == 19


def test_validation_repository_latest_can_filter_strategy_revision(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    repo = TradingValidationRepository(validation_path)
    repo.save_run(
        {
            "status": "ok",
            "run_id": "active-revision-run",
            "venue": "binance",
            "scope": "live",
            "strategy_revision_id": "jue_edge_repair_v2",
            "computed_at": "2026-06-01T00:00:00+00:00",
            "summary": {
                "total_score": 81.0,
                "readiness": "probe",
                "pass_count": 12,
                "warn_count": 7,
                "fail_count": 0,
                "missing_count": 0,
            },
        }
    )
    repo.save_run(
        {
            "status": "ok",
            "run_id": "legacy-newer-run",
            "venue": "binance",
            "scope": "live",
            "strategy_revision_id": "",
            "computed_at": "2026-06-02T00:00:00+00:00",
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

    latest_any = repo.latest(venue="binance")
    latest_active = repo.latest(
        venue="binance",
        strategy_revision_id="jue_edge_repair_v2",
    )
    missing_revision = repo.latest(
        venue="binance",
        strategy_revision_id="jue_edge_repair_v3",
    )

    assert latest_any["run_id"] == "legacy-newer-run"
    assert latest_active["run_id"] == "active-revision-run"
    assert latest_active["strategy_revision_id"] == "jue_edge_repair_v2"
    assert latest_active["summary"]["fail_count"] == 0
    assert missing_revision["status"] == "empty"
    assert missing_revision["strategy_revision_id"] == "jue_edge_repair_v3"


def test_validation_repository_compacts_old_payloads_but_keeps_latest_detail(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    repo = TradingValidationRepository(validation_path)
    huge_marker = "x" * 40_000

    for index in range(4):
        repo.save_run(
            {
                "status": "ok",
                "run_id": f"run-{index}",
                "venue": "binance",
                "scope": "live",
                "strategy_revision_id": "jue_edge_repair_v1",
                "computed_at": f"2026-06-01T00:0{index}:00+00:00",
                "summary": {
                    "total_score": 50.0 + index,
                    "readiness": "probe",
                    "pass_count": 10 + index,
                    "warn_count": 3,
                    "fail_count": 1,
                    "missing_count": 0,
                },
                "disciplines": [{"id": "data_validation", "status": "pass"}],
                "huge_detail": huge_marker,
            }
        )

    result = repo.compact_history(recent_rows_per_group=1)

    assert result["status"] == "ok"
    assert result["compacted_count"] == 3
    latest = repo.latest(venue="binance", strategy_revision_id="jue_edge_repair_v1")
    assert latest["run_id"] == "run-3"
    assert latest["payload"]["huge_detail"] == huge_marker

    with sqlite3.connect(validation_path) as conn:
        conn.row_factory = sqlite3.Row
        old_row = conn.execute(
            "SELECT payload_json FROM validation_runs WHERE run_id = 'run-0'"
        ).fetchone()
    compacted_payload = json.loads(str(old_row["payload_json"]))
    assert compacted_payload["compacted"] is True
    assert compacted_payload["version"] == "jue_validation_compacted_v1"
    assert compacted_payload["summary"]["total_score"] == 50.0
    assert compacted_payload["compaction"]["original_payload_chars"] > 40_000
    assert "huge_detail" not in compacted_payload


def test_validation_repository_compaction_partitions_by_venue_scope_revision(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    repo = TradingValidationRepository(validation_path)
    for venue in ("kis", "binance"):
        for index in range(2):
            repo.save_run(
                {
                    "status": "ok",
                    "run_id": f"{venue}-{index}",
                    "venue": venue,
                    "scope": "live",
                    "strategy_revision_id": "rev",
                    "computed_at": f"2026-06-01T00:0{index}:00+00:00",
                    "summary": {"total_score": 40 + index, "readiness": "probe"},
                    "detail": "y" * 20_000,
                }
            )

    result = repo.compact_history(recent_rows_per_group=1)

    assert result["compacted_count"] == 2
    assert repo.latest(venue="kis", strategy_revision_id="rev")["payload"]["detail"]
    assert repo.latest(venue="binance", strategy_revision_id="rev")["payload"][
        "detail"
    ]


def test_validation_repository_compaction_deletes_rows_beyond_group_retention(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "trading_validation.db"
    repo = TradingValidationRepository(validation_path)
    for index in range(5):
        repo.save_run(
            {
                "status": "ok",
                "run_id": f"run-{index}",
                "venue": "kis",
                "scope": "live",
                "strategy_revision_id": "rev",
                "computed_at": f"2026-06-01T00:0{index}:00+00:00",
                "summary": {"total_score": 40 + index, "readiness": "probe"},
                "detail": "z" * 20_000,
            }
        )

    result = repo.compact_history(
        recent_rows_per_group=1,
        max_rows_per_group=3,
        min_payload_chars=1_000,
        vacuum=True,
    )

    assert result["recent_rows_per_group"] == 1
    assert result["max_rows_per_group"] == 3
    assert result["compacted_count"] == 2
    assert result["deleted_count"] == 2
    assert result["vacuum"] is True
    latest = repo.latest(venue="kis", strategy_revision_id="rev")
    assert latest["run_id"] == "run-4"
    assert latest["payload"]["detail"]
    with sqlite3.connect(validation_path) as conn:
        rows = conn.execute(
            "SELECT run_id, payload_json FROM validation_runs ORDER BY computed_at"
        ).fetchall()
    assert [row[0] for row in rows] == ["run-2", "run-3", "run-4"]
    compacted = json.loads(rows[0][1])
    assert compacted["compacted"] is True
    assert "detail" not in compacted


def test_validation_service_filters_live_outcomes_by_strategy_revision(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    rows = [
        ("legacy-loss-1", "BTCUSDT", 100.0, 90.0, "legacy_rev"),
        ("legacy-loss-2", "ETHUSDT", 100.0, 92.0, "legacy_rev"),
        ("repair-win-1", "SOLUSDT", 100.0, 110.0, "jue_edge_repair_v2"),
        ("repair-win-2", "NEARUSDT", 100.0, 106.0, "jue_edge_repair_v2"),
    ]
    for block_id, symbol, entry, exit_price, revision_id in rows:
        metadata = {"strategy_revision_id": revision_id}
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=entry,
                exit_price=exit_price,
                qty=1.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            strategy_revision_id="jue_edge_repair_v2",
            min_sample_count=1,
            monte_carlo_iterations=16,
            monte_carlo_seed=21,
        )
    )

    payload = service.run_once(venue="binance")

    assert payload["strategy_revision_id"] == "jue_edge_repair_v2"
    assert payload["metrics"]["sample_count"] == 2
    assert payload["metrics"]["total_net_pnl"] == 16.0
    assert payload["metrics"]["strategy_revision_counts"] == {
        "jue_edge_repair_v2": 2
    }
    assert payload["metrics"]["symbols"] == ["NEARUSDT", "SOLUSDT"]


def test_validation_service_treats_empty_active_revision_as_missing_not_failed(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    LivePerformanceRepository(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            strategy_revision_id="jue_edge_repair_v2",
            min_sample_count=5,
            monte_carlo_iterations=16,
        )
    )

    payload = service.run_once(venue="binance")
    disciplines = {row["id"]: row for row in payload["disciplines"]}

    assert payload["metrics"]["sample_count"] == 0
    assert payload["monte_carlo"]["status"] == "missing"
    assert payload["monte_carlo"]["risk_of_ruin_pct"] == 0.0
    assert payload["monte_carlo"]["sequence_risk_level"] == "missing"
    assert disciplines["monte_carlo"]["status"] == "missing"
    assert payload["summary"]["fail_count"] == 0
    assert payload["summary"]["hard_fail_count"] == 0
    assert payload["summary"]["hard_blocking_count"] == 3
    assert payload["summary"]["missing_count"] >= 1


def test_validation_service_surfaces_legacy_proxy_without_scaling_active_revision(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 98.0, 107.0), start=1):
        metadata = {"strategy_revision_id": "legacy_rev"}
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"legacy-{index}",
                symbol="BTCUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            strategy_revision_id="jue_edge_repair_v2",
            min_sample_count=5,
            monte_carlo_iterations=16,
        )
    )

    payload = service.run_once(venue="binance")
    active_evidence = payload["metrics"]["active_revision_evidence"]
    lane_hints = payload["remediation_plan"]["lane_policy_hints"]

    assert payload["metrics"]["sample_count"] == 3
    assert active_evidence["status"] == "no_active_revision_samples_with_proxy"
    assert active_evidence["proxy_sample_used_for_metrics"] is True
    assert active_evidence["validation_sample_role"] == "legacy_proxy_metrics_no_scale"
    assert active_evidence["active_sample_count"] == 0
    assert active_evidence["effective_sample_count"] == 0
    assert active_evidence["validation_sample_count"] == 0
    assert active_evidence["min_samples_to_scale"] == 5
    assert active_evidence["legacy_proxy_sample_count"] == 3
    assert active_evidence["legacy_proxy_total_net_pnl"] == pytest.approx(9.0)
    assert active_evidence["can_scale_from_proxy"] is False
    assert active_evidence["scale_up_allowed"] is False
    assert active_evidence["authority_posture"] == (
        "probe_only_until_active_revision_samples_close"
    )
    assert active_evidence["evidence_role"] == "proxy_only_not_scale_up"
    assert active_evidence["legacy_proxy_gate_mode"] == "probe_only"
    assert "cost_simulation" in active_evidence[
        "legacy_proxy_failed_discipline_ids"
    ]
    assert lane_hints["active_revision_sample_mode"] == (
        "no_active_revision_samples_with_proxy"
    )
    assert lane_hints["legacy_proxy_sample_count"] == 3
    assert lane_hints["can_scale_from_proxy"] is False
    assert payload["summary"]["validation_sample_role"] == (
        "legacy_proxy_metrics_no_scale"
    )
    assert payload["summary"]["can_scale_from_proxy"] is False
    assert "cost_simulation" in payload["summary"][
        "legacy_proxy_failed_discipline_ids"
    ]
    assert payload["summary"]["hard_blocking_count"] == 0
    assert payload["summary"]["readiness"] == "probe"


def test_validation_service_demotes_sample_building_performance_fails_to_probe(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    metadata = {
        "strategy_revision_id": "jue_edge_repair_v2",
        "market": "futures",
        "side": "short",
        "cost_model_status": "recorded",
        "cost_components": {
            "fees": 0.01,
            "funding": 0.0,
            "spread": 0.0,
            "slippage": 0.0,
        },
        "quote_volume_usdt": 1_000_000.0,
        "max_participation_rate": 0.01,
        "notional_usdt": 100.0,
    }
    for index, exit_price in enumerate((101.0, 101.5), start=1):
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"active-probe-loss-{index}",
                symbol="BTCUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            strategy_revision_id="jue_edge_repair_v2",
            min_sample_count=5,
            monte_carlo_iterations=16,
        )
    )

    payload = service.run_once(venue="binance")
    active_evidence = payload["metrics"]["active_revision_evidence"]
    disciplines = {row["id"]: row for row in payload["disciplines"]}

    assert active_evidence["status"] == "active_revision_sample_building"
    assert active_evidence["active_sample_count"] == 2
    assert active_evidence["scale_up_allowed"] is False
    assert active_evidence["sample_building_gate_mode"] == "probe_only"
    assert "profit_factor" in active_evidence[
        "active_revision_sample_building_failed_discipline_ids"
    ]
    assert disciplines["profit_factor"]["status"] == "warn"
    assert disciplines["profit_factor"][
        "active_revision_sample_building_status"
    ] == "fail"
    assert payload["summary"]["fail_count"] == 0
    assert payload["summary"]["hard_fail_count"] == 0
    assert payload["summary"]["readiness"] == "probe"
    assert payload["summary"]["active_revision_sample_mode"] == (
        "active_revision_sample_building"
    )
    assert payload["summary"]["active_revision_sample_count"] == 2


def test_validation_service_uses_metadata_for_regime_capacity_correlation_and_factor(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_metadata_rich_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=13,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}

    assert payload["metrics"]["regime_scorecards"]["regime_count"] == 3
    assert discipline_by_id["regime_test"]["status"] in {"pass", "warn"}
    assert discipline_by_id["stress_test"]["status"] in {"pass", "warn"}
    assert discipline_by_id["capacity_analysis"]["status"] == "pass"
    assert discipline_by_id["correlation"]["status"] == "pass"
    assert discipline_by_id["factor_exposure"]["status"] == "pass"
    assert payload["metrics"]["capacity"]["min_capacity_ratio"] > 20
    assert payload["metrics"]["capacity"]["status"] == "pass"
    assert payload["metrics"]["capacity"]["capacity_method"] == "metadata_capacity_ratio"
    assert payload["metrics"]["capacity"]["tightest_symbol"] == "LINKUSDT"
    assert payload["metrics"]["capacity"]["tightest_block_id"] == "m-6"
    assert payload["metrics"]["capacity"]["examples"][0]["symbol"] == "LINKUSDT"
    assert payload["metrics"]["correlation_proxy"]["top_cluster_share_pct"] < 60
    assert "momentum" in payload["metrics"]["factor_exposure"]["factor_totals"]


def test_validation_service_uses_pattern_lab_for_overfit_walk_forward_and_oos(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    pattern_path = tmp_path / "crypto_pattern_lab.db"
    kr_pattern_path = tmp_path / "kr_equity_pattern_lab.db"
    _seed_live_performance(live_path)
    _seed_pattern_lab(pattern_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=pattern_path,
            kr_equity_pattern_lab_db_path=kr_pattern_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=17,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    pattern_lab = payload["metrics"]["pattern_lab"]

    assert pattern_lab["status"] == "ok"
    assert pattern_lab["active_set_count"] == 1
    assert pattern_lab["rejected_set_count"] == 1
    assert pattern_lab["walk_forward_pass_rate_pct"] == pytest.approx(50.0)
    assert pattern_lab["min_out_of_sample_profit_factor"] == pytest.approx(0.92)
    assert discipline_by_id["overfit_validation"]["status"] == "warn"
    assert discipline_by_id["walk_forward_analysis"]["status"] == "warn"
    assert discipline_by_id["out_of_sample_test"]["status"] == "warn"


def test_validation_service_does_not_pass_unverified_active_pattern_sets(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    pattern_path = tmp_path / "crypto_pattern_lab.db"
    _seed_live_performance(live_path)
    _seed_unverified_active_pattern_lab(pattern_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=pattern_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=17,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    pattern_lab = payload["metrics"]["pattern_lab"]

    assert pattern_lab["active_set_count"] == 2
    assert pattern_lab["unknown_overfit_count"] == 2
    assert pattern_lab["missing_out_of_sample_set_count"] == 2
    assert pattern_lab["out_of_sample_coverage_rate_pct"] == pytest.approx(0.0)
    assert pattern_lab["active_missing_out_of_sample_set_count"] == 2
    assert pattern_lab["active_out_of_sample_coverage_rate_pct"] == pytest.approx(0.0)
    assert pattern_lab["walk_forward_pass_rate_pct"] == pytest.approx(0.0)
    assert discipline_by_id["overfit_validation"]["status"] == "warn"
    assert discipline_by_id["walk_forward_analysis"]["status"] == "fail"
    assert discipline_by_id["out_of_sample_test"]["status"] == "fail"


def test_validation_service_fails_active_pattern_sets_without_rolling_wfa_windows(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    pattern_path = tmp_path / "crypto_pattern_lab.db"
    _seed_live_performance(live_path)
    _seed_active_pattern_lab_without_wfa_windows(pattern_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=pattern_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=17,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    pattern_lab = payload["metrics"]["pattern_lab"]
    kelly = payload["metrics"]["kelly_sizing"]

    assert pattern_lab["status"] == "ok"
    assert pattern_lab["validation_status"] == "fail"
    assert pattern_lab["active_missing_walk_forward_set_count"] == 1
    assert pattern_lab["active_walk_forward_coverage_rate_pct"] == pytest.approx(0.0)
    assert discipline_by_id["walk_forward_analysis"]["status"] == "fail"
    assert kelly["status"] == "fail"
    assert kelly["cap_reason"] == "validation_quality_fail"
    assert kelly["recommended_risk_fraction"] == pytest.approx(0.0)
    assert payload["operator_guidance"][0].startswith(
        "패턴랩: active set의 rolling WFA window를 재생성"
    )
    assert "active_walk_forward_windows_missing" in payload["operator_guidance"][0]


def test_kis_validation_does_not_use_crypto_pattern_lab_for_wfa_or_oos(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    pattern_path = tmp_path / "crypto_pattern_lab.db"
    kr_pattern_path = tmp_path / "kr_equity_pattern_lab.db"
    _seed_kis_live_performance(live_path)
    _seed_pattern_lab(pattern_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=pattern_path,
            kr_equity_pattern_lab_db_path=kr_pattern_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=23,
            initial_equity=4_000_000.0,
        )
    )

    payload = service.run_once(venue="kis")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    pattern_lab = payload["metrics"]["pattern_lab"]

    assert pattern_lab["status"] == "proxy"
    assert pattern_lab["source_scope"] == "kis_live_forward_proxy"
    assert pattern_lab["db_path"] != str(pattern_path)
    assert "optimized_set_count" not in pattern_lab
    assert discipline_by_id["overfit_validation"]["metric"]["source_scope"] == (
        "kis_live_forward_proxy"
    )
    assert "KIS live-forward" in discipline_by_id["walk_forward_analysis"]["evidence"]
    assert "crypto" not in discipline_by_id["out_of_sample_test"]["evidence"].lower()


def test_kis_validation_uses_kr_equity_pattern_lab_when_available(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    crypto_pattern_path = tmp_path / "crypto_pattern_lab.db"
    kr_pattern_path = tmp_path / "kr_equity_pattern_lab.db"
    _seed_kis_live_performance(live_path)
    _seed_pattern_lab(crypto_pattern_path)
    _seed_kr_equity_pattern_lab(kr_pattern_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=crypto_pattern_path,
            kr_equity_pattern_lab_db_path=kr_pattern_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=29,
            initial_equity=4_000_000.0,
        )
    )

    payload = service.run_once(venue="kis")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    pattern_lab = payload["metrics"]["pattern_lab"]

    assert pattern_lab["status"] == "ok"
    assert pattern_lab["source_scope"] == "kr_equity_pattern_lab"
    assert pattern_lab["db_path"] == str(kr_pattern_path)
    assert pattern_lab["active_set_count"] == 1
    assert pattern_lab["rejected_set_count"] == 1
    assert pattern_lab["walk_forward_pass_rate_pct"] == pytest.approx(50.0)
    assert pattern_lab["min_out_of_sample_profit_factor"] == pytest.approx(0.82)
    assert discipline_by_id["overfit_validation"]["metric"]["source_scope"] == (
        "kr_equity_pattern_lab"
    )
    assert discipline_by_id["walk_forward_analysis"]["metric"]["db_path"] == str(
        kr_pattern_path
    )


def test_kis_validation_guides_repair_when_kr_pattern_lab_has_only_rejected_sets(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    crypto_pattern_path = tmp_path / "crypto_pattern_lab.db"
    kr_pattern_path = tmp_path / "kr_equity_pattern_lab.db"
    _seed_kis_live_performance(live_path)
    _seed_pattern_lab(crypto_pattern_path)
    _seed_rejected_kr_equity_pattern_lab(kr_pattern_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=crypto_pattern_path,
            kr_equity_pattern_lab_db_path=kr_pattern_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=31,
            initial_equity=4_000_000.0,
        )
    )

    payload = service.run_once(venue="kis")
    pattern_lab = payload["metrics"]["pattern_lab"]

    assert pattern_lab["status"] == "ok"
    assert pattern_lab["source_scope"] == "kr_equity_pattern_lab"
    assert pattern_lab["active_set_count"] == 0
    assert pattern_lab["rejected_set_count"] == 2
    assert pattern_lab["failed_reasons"]["out_of_sample_expectancy_negative"] == 2
    assert pattern_lab["top_failed_reasons"][0] == {
        "reason": "out_of_sample_expectancy_negative",
        "count": 2,
    }
    assert pattern_lab["repair_priorities"][0]["priority"] == "active_edge_rebuild"
    assert pattern_lab["repair_priorities"][1]["focus"] == "oos_expectancy"
    assert any(
        "out_of_sample_expectancy_negative" in row
        for row in payload["operator_guidance"]
    )


def test_validation_service_flags_stale_error_and_missing_cost_data(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_data_quality_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=5,
            monte_carlo_iterations=32,
            monte_carlo_seed=19,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    quality = payload["metrics"]["data_quality"]

    assert quality["status"] == "warn"
    assert quality["sample_count"] == 5
    assert quality["stale_count"] == 1
    assert quality["upstream_error_count"] == 1
    assert quality["missing_cost_count"] == 2
    assert quality["fallback_source_count"] == 2
    assert discipline_by_id["data_validation"]["status"] == "warn"


def test_validation_service_models_transaction_cost_drag_and_stress(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_cost_simulation_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=23,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    cost = payload["metrics"]["cost_simulation"]

    assert cost["sample_count"] == 4
    assert cost["recorded_cost_sample_count"] == 3
    assert cost["missing_cost_sample_count"] == 1
    assert cost["total_gross_pnl"] == pytest.approx(12.0)
    assert cost["total_cost"] == pytest.approx(2.5)
    assert cost["cost_drag_pct_of_gross_pnl"] == pytest.approx(20.833333)
    assert cost["cost_by_component"]["fees"] == pytest.approx(1.7)
    assert cost["cost_by_component"]["slippage"] == pytest.approx(0.7)
    assert cost["cost_by_component"]["funding"] == pytest.approx(0.1)
    assert cost["stressed_net_pnl_by_cost_multiplier"]["2x"] == pytest.approx(7.0)
    assert cost["breakeven_cost_multiplier"] == pytest.approx(4.8)
    assert cost["worst_cost_rows"][0]["block_id"] == "cost-3"
    assert cost["worst_cost_rows"][0]["symbol"] == "SOLUSDT"
    assert cost["worst_cost_rows"][0]["horizon"] == "short"
    assert cost["worst_cost_rows"][0]["cost_drag_pct_of_abs_gross_pnl"] == pytest.approx(
        95.0
    )
    horizon_groups = {
        (row["group_type"], row["group"]): row
        for row in cost["worst_cost_groups"]
    }
    assert horizon_groups[("horizon", "short")]["sample_count"] == 2
    assert horizon_groups[("horizon", "short")]["total_cost"] == pytest.approx(1.9)
    assert horizon_groups[("horizon", "short")][
        "cost_drag_pct_of_abs_gross_pnl"
    ] == pytest.approx(63.333333)
    assert discipline_by_id["cost_simulation"]["status"] == "warn"
    assert discipline_by_id["cost_simulation"]["metric"] == cost


def test_validation_service_fails_cost_simulation_when_cost_coverage_is_sparse(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_sparse_cost_simulation_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=6,
            monte_carlo_iterations=32,
            monte_carlo_seed=66,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    cost = payload["metrics"]["cost_simulation"]
    sizing = payload["metrics"]["kelly_sizing"]

    assert cost["sample_count"] == 6
    assert cost["recorded_cost_sample_count"] == 1
    assert cost["missing_cost_sample_count"] == 5
    assert cost["missing_cost_sample_rate_pct"] == pytest.approx(83.333333)
    assert cost["status"] == "fail"
    assert discipline_by_id["cost_simulation"]["status"] == "fail"
    assert sizing["recommended_risk_fraction"] == pytest.approx(0.0)
    assert sizing["cap_reason"] == "validation_quality_fail"
    assert sizing["status"] == "fail"
    assert discipline_by_id["kelly_sizing"]["status"] == "fail"


def test_validation_service_warns_when_costs_are_only_estimated(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_estimated_cost_simulation_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=67,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    cost = payload["metrics"]["cost_simulation"]

    assert cost["sample_count"] == 4
    assert cost["recorded_cost_sample_count"] == 0
    assert cost["estimated_cost_sample_count"] == 4
    assert cost["missing_cost_sample_count"] == 0
    assert cost["cost_precision_verified_rate_pct"] == pytest.approx(0.0)
    assert cost["cost_precision_usable_rate_pct"] == pytest.approx(100.0)
    assert cost["status"] == "warn"
    assert discipline_by_id["cost_simulation"]["status"] == "warn"


def test_validation_service_treats_mixed_explicit_estimated_costs_as_hybrid(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_mixed_cost_simulation_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=71,
        )
    )

    payload = service.run_once(venue="kis")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    cost = payload["metrics"]["cost_simulation"]

    assert cost["sample_count"] == 4
    assert cost["recorded_cost_sample_count"] == 0
    assert cost["hybrid_cost_sample_count"] == 4
    assert cost["estimated_cost_sample_count"] == 0
    assert cost["missing_cost_sample_count"] == 0
    assert cost["cost_precision_counts"] == {
        "recorded": 0,
        "hybrid": 4,
        "estimated": 0,
        "partial": 0,
        "missing": 0,
    }
    assert cost["cost_precision_verified_rate_pct"] == pytest.approx(0.0)
    assert cost["cost_precision_usable_rate_pct"] == pytest.approx(100.0)
    assert cost["status"] == "warn"
    assert discipline_by_id["cost_simulation"]["status"] == "warn"


def test_validation_service_builds_actionable_remediation_plan(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    pattern_path = tmp_path / "crypto_pattern_lab.db"
    _seed_data_quality_performance(live_path)
    _seed_active_pattern_lab_without_wfa_windows(pattern_path)

    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=tmp_path / "validation.db",
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=pattern_path,
            monte_carlo_iterations=32,
            monte_carlo_seed=67,
        )
    )

    payload = service.run_once(venue="binance")
    plan = payload["remediation_plan"]

    assert plan["status"] == "blocked"
    assert plan["failed_count"] >= 1
    assert plan["weak_count"] >= plan["failed_count"]
    assert plan["primary_next_action"]
    assert [row["id"] for row in plan["categories"]] == [
        "immediate_ops_controls",
        "research_validation_work",
        "sizing_risk_controls",
    ]

    categories = {row["id"]: row for row in plan["categories"]}
    assert any(
        item["discipline_id"] == "data_validation"
        for item in categories["immediate_ops_controls"]["items"]
    )
    assert any(
        item["discipline_id"] == "walk_forward_analysis"
        for item in categories["research_validation_work"]["items"]
    )
    assert any(
        item["discipline_id"] in {"kelly_sizing", "risk_of_ruin", "monte_carlo"}
        for item in categories["sizing_risk_controls"]["items"]
    )
    assert plan["top_priority"][0]["status"] == "fail"
    assert plan["lane_policy_hints"]["scale_up_allowed"] is False
    assert plan["lane_policy_hints"]["entry_mode"] in {
        "verified_waiting_probe",
        "risk_off_recovery",
    }
    work_queue = plan["work_queue"]
    assert work_queue
    assert len(work_queue) <= 12
    queue_by_discipline = {row["discipline_id"]: row for row in work_queue}
    assert queue_by_discipline["data_validation"]["owner"] == "data_pipeline"
    assert queue_by_discipline["data_validation"]["repair_action_id"] == (
        "validation_repair.data_repair_before_trade.data_validation"
    )
    assert queue_by_discipline["data_validation"]["automation_hook"] == (
        "sync_live_performance_and_edges"
    )
    assert queue_by_discipline["data_validation"]["execution_weight"] == (
        "lightweight"
    )
    assert queue_by_discipline["data_validation"]["blocks_scaling"] == (
        "no_scale_up_until_data_clean"
    )
    assert queue_by_discipline["data_validation"]["blocks_new_entries"] == (
        "scale_up_and_unverified_immediate_entries"
    )
    assert "sync_live_performance" in queue_by_discipline["data_validation"][
        "runner_hint"
    ]
    assert "data_quality" in queue_by_discipline["data_validation"][
        "verification_artifact"
    ]
    assert queue_by_discipline["walk_forward_analysis"]["owner"] == "pattern_lab"
    assert queue_by_discipline["walk_forward_analysis"]["repair_action_id"] == (
        "validation_repair.backtest_wfa_oos_rebuild.walk_forward_analysis"
    )
    assert queue_by_discipline["walk_forward_analysis"]["automation_hook"] == (
        "pattern_lab_rebuild_wfa_oos"
    )
    assert queue_by_discipline["walk_forward_analysis"]["execution_weight"] == (
        "external_runner"
    )
    assert queue_by_discipline["walk_forward_analysis"]["lane_policy_hint"] == (
        "shadow_or_waiting_only_until_wfa_rebuilt"
    )
    assert "pattern_lab" in queue_by_discipline["walk_forward_analysis"][
        "runner_hint"
    ]
    assert "WFA" in queue_by_discipline["walk_forward_analysis"][
        "verification_artifact"
    ]
    wfa_repair = queue_by_discipline["walk_forward_analysis"]
    assert wfa_repair["validation_mode"] == "backtest_wfa_oos_rebuild"
    assert wfa_repair["allowed_entry_posture"] == "shadow_or_waiting_entry_only"
    assert wfa_repair["live_shadow_required"] is True
    assert wfa_repair["scale_up_blocked"] is True
    assert wfa_repair["evidence_targets"]["min_walk_forward_windows"] == 3
    assert wfa_repair["evidence_targets"]["min_walk_forward_pass_rate_pct"] == 70.0
    assert wfa_repair["evidence_targets"][
        "requires_live_shadow_before_scale_up"
    ] is True
    assert wfa_repair["pass_path"]["current_gap"] in {
        "evidence_failed_threshold",
        "evidence_thin_or_not_scalable",
        "evidence_missing",
    }
    assert wfa_repair["pass_path"]["collection_hook"] == (
        "pattern_lab_rebuild_wfa_oos"
    )
    assert wfa_repair["pass_path"]["required_evidence"][
        "min_walk_forward_windows"
    ] == 3
    assert wfa_repair["pass_path"]["jue_behavior_until_pass"][
        "allowed_entry_posture"
    ] == "shadow_or_waiting_entry_only"
    assert wfa_repair["pass_path"]["jue_behavior_until_pass"][
        "scale_up_blocked"
    ] is True
    assert wfa_repair["pass_path"]["m1_runtime_profile"][
        "avoid_full_rebuild_in_manager_prompt"
    ] is True
    assert queue_by_discipline["kelly_sizing"]["owner"] == "risk_engine"
    assert queue_by_discipline["kelly_sizing"]["blocks_scaling"] == (
        "fractional_kelly_probe_only"
    )
    assert queue_by_discipline["kelly_sizing"]["blocks_new_entries"] == (
        "risk_budget_expansion"
    )
    kelly_repair = queue_by_discipline["kelly_sizing"]
    assert kelly_repair["validation_mode"] == "risk_budget_recalibration"
    assert kelly_repair["allowed_entry_posture"] == "fractional_kelly_probe"
    assert kelly_repair["evidence_targets"]["max_risk_of_ruin_pct"] == 5.0
    assert kelly_repair["evidence_targets"]["max_full_kelly_used_fraction"] == 0.25
    assert all(row["exit_criteria"] for row in work_queue)
    assert all(row["runner_hint"] for row in work_queue)
    assert all(row["verification_artifact"] for row in work_queue)
    assert plan["pass_path_summary"]["scale_up_blocked_count"] >= 1
    assert "pattern_lab_rebuild_wfa_oos" in plan["pass_path_summary"][
        "automation_hooks"
    ]


def test_validation_service_caps_kelly_sizing_guidance(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_high_edge_kelly_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            crypto_pattern_lab_db_path=tmp_path / "missing_pattern_lab.db",
            min_sample_count=6,
            monte_carlo_iterations=64,
            monte_carlo_seed=29,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    sizing = payload["metrics"]["kelly_sizing"]

    assert sizing["status"] == "warn"
    assert sizing["sample_count"] == 6
    assert sizing["full_kelly_fraction"] > 0.5
    assert sizing["fractional_kelly_025"] > 0.12
    assert sizing["max_risk_cap_fraction"] == pytest.approx(0.02)
    assert sizing["recommended_risk_fraction"] == pytest.approx(0.01)
    assert sizing["recommended_risk_pct"] == pytest.approx(1.0)
    assert sizing["cap_reason"] == "validation_quality_missing_cap"
    assert sizing["evidence_quality"] == "sufficient"
    assert sizing["validation_quality_pressure"]["missing_count"] >= 1
    assert discipline_by_id["kelly_sizing"]["metric"] == sizing


def test_kelly_sizing_allows_max_cap_when_validation_quality_is_clean(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(validation_db_path=tmp_path / "validation.db")
    )
    metrics = {
        "sample_count": 80,
        "win_rate_pct": 62.0,
        "payoff_ratio": 1.8,
        "expectancy_pct": 1.2,
        "profit_factor": 2.2,
        "kelly_fraction": 0.408889,
        "fractional_kelly_025": 0.102222,
        "risk_of_ruin_pct": 0.0,
        "max_drawdown_pct": -2.0,
        "validation_quality_pressure": {
            "status": "pass",
            "fail_count": 0,
            "warn_count": 0,
            "missing_count": 0,
            "failures": [],
            "warnings": [],
            "missing": [],
        },
    }

    sizing = service._kelly_sizing_metrics(metrics)

    assert sizing["status"] == "pass"
    assert sizing["recommended_risk_fraction"] == pytest.approx(0.02)
    assert sizing["recommended_risk_pct"] == pytest.approx(2.0)
    assert sizing["cap_reason"] == "max_per_block_cap"


def test_kelly_sizing_caps_risk_when_validation_quality_is_warning(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(validation_db_path=tmp_path / "validation.db")
    )
    metrics = {
        "sample_count": 80,
        "win_rate_pct": 62.0,
        "payoff_ratio": 1.8,
        "expectancy_pct": 1.2,
        "profit_factor": 2.2,
        "kelly_fraction": 0.408889,
        "fractional_kelly_025": 0.102222,
        "risk_of_ruin_pct": 0.0,
        "max_drawdown_pct": -2.0,
        "validation_quality_pressure": {
            "status": "warn",
            "fail_count": 0,
            "warn_count": 2,
            "warnings": ["correlation_proxy", "factor_exposure"],
        },
    }

    sizing = service._kelly_sizing_metrics(metrics)

    assert sizing["status"] == "warn"
    assert sizing["recommended_risk_fraction"] == pytest.approx(0.01)
    assert sizing["recommended_risk_pct"] == pytest.approx(1.0)
    assert sizing["cap_reason"] == "validation_quality_warning_cap"
    assert sizing["validation_quality_warning_cap_fraction"] == pytest.approx(0.01)


def test_validation_quality_pressure_treats_missing_context_as_warning(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(validation_db_path=tmp_path / "validation.db")
    )

    pressure = service._validation_quality_pressure(
        {
            "capacity": {"status": "missing"},
            "regime_scorecards": {"status": "missing"},
            "correlation_proxy": {"status": "missing"},
            "factor_exposure": {"status": "missing"},
        }
    )

    assert pressure["status"] == "warn"
    assert pressure["missing_count"] == 4
    assert pressure["missing"] == [
        "capacity",
        "regime_scorecards",
        "correlation_proxy",
        "factor_exposure",
    ]


def test_validation_service_builds_lane_scorecards_without_global_halt(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    rows = [
        (
            "spot-1",
            "BTCUSDT",
            100.0,
            103.0,
            1.0,
            {
                "market": "spot",
                "side": "long",
                "cost_model_status": "recorded",
                "cost_components": {
                    "fees": 0.05,
                    "spread": 0.0,
                    "slippage": 0.0,
                },
                "validation_evidence": {
                    "backtest_passed": True,
                    "walk_forward_passed": True,
                    "out_of_sample_passed": True,
                    "live_shadow_passed": True,
                },
            },
        ),
        (
            "spot-2",
            "ETHUSDT",
            100.0,
            102.0,
            1.0,
            {
                "market": "spot",
                "side": "long",
                "cost_model_status": "recorded",
                "cost_components": {
                    "fees": 0.05,
                    "spread": 0.0,
                    "slippage": 0.0,
                },
                "validation_evidence": {
                    "backtest_passed": True,
                    "walk_forward_passed": True,
                    "out_of_sample_passed": True,
                    "live_shadow_passed": True,
                },
            },
        ),
        (
            "spot-3",
            "SOLUSDT",
            100.0,
            99.0,
            1.0,
            {
                "market": "spot",
                "side": "long",
                "cost_model_status": "recorded",
                "cost_components": {
                    "fees": 0.05,
                    "spread": 0.0,
                    "slippage": 0.0,
                },
                "validation_evidence": {
                    "backtest_passed": True,
                    "walk_forward_passed": True,
                    "out_of_sample_passed": True,
                    "live_shadow_passed": True,
                },
            },
        ),
        (
            "spot-4",
            "BNBUSDT",
            100.0,
            102.0,
            1.0,
            {
                "market": "spot",
                "side": "long",
                "cost_model_status": "recorded",
                "cost_components": {
                    "fees": 0.05,
                    "spread": 0.0,
                    "slippage": 0.0,
                },
                "validation_evidence": {
                    "backtest_passed": True,
                    "walk_forward_passed": True,
                    "out_of_sample_passed": True,
                    "live_shadow_passed": True,
                },
            },
        ),
        (
            "fut-short-1",
            "NEARUSDT",
            100.0,
            101.0,
            1.0,
            {"market": "futures", "side": "short"},
        ),
        (
            "fut-short-2",
            "LTCUSDT",
            100.0,
            102.0,
            1.0,
            {"market": "futures", "side": "short"},
        ),
        (
            "fut-short-3",
            "XRPUSDT",
            100.0,
            101.0,
            1.0,
            {"market": "futures", "side": "short"},
        ),
        (
            "fut-short-4",
            "DOGEUSDT",
            100.0,
            102.0,
            1.0,
            {"market": "futures", "side": "short"},
        ),
        (
            "vol-1",
            "ALTUSDT",
            100.0,
            106.0,
            1.0,
            {"market": "futures", "side": "long", "lane": "volatile_attack"},
        ),
        (
            "vol-2",
            "MEMEUSDT",
            100.0,
            107.0,
            1.0,
            {"market": "futures", "side": "long", "lane": "volatile_attack"},
        ),
    ]
    for block_id, symbol, entry, exit_price, qty, metadata in rows:
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=entry,
                exit_price=exit_price,
                qty=qty,
                fees=0.05,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=3,
            monte_carlo_iterations=32,
            monte_carlo_seed=72,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]

    assert lanes["version"] == "lane_scorecards_v1"
    assert lanes["status"] == "warn"
    assert "spot" in lanes["scale_candidate_lanes"]
    assert "futures_short" in lanes["weak_lanes"]
    assert "volatile_attack" in lanes["insufficient_lanes"]
    assert lanes["lane_actions"]["futures_short"]["action"] == (
        "de_risk_or_waiting_entry"
    )
    assert lanes["lane_actions"]["futures_short"]["authority_multiplier"] <= 0.5
    assert lanes["lane_actions"]["futures_short"]["risk_of_ruin_pct"] >= 0
    assert lanes["scorecards"][0]["risk_of_ruin_pct"] >= 0
    assert lanes["scorecards"][0]["sequence_risk_level"] in {
        "low",
        "medium",
        "high",
        "critical",
    }
    assert lanes["lane_actions"]["spot"]["action"] in {
        "eligible_to_press_when_validation_clear",
        "normal_or_selective_press",
    }
    assert lanes["lane_actions"]["spot"]["authority_multiplier"] >= 1.0
    assert lanes["lane_actions"]["spot"]["risk_budget_multiplier"] >= 1.0
    assert lanes["lane_actions"]["spot"]["raw_kelly_fraction"] > 0.0
    spot_scorecard = next(
        row for row in lanes["scorecards"] if row["lane"] == "spot"
    )
    spot_action = lanes["lane_actions"]["spot"]
    for key in (
        "expectancy_pct",
        "win_rate_pct",
        "profit_factor",
        "max_drawdown_pct",
        "recovery_factor",
        "cumulative_return_pct",
    ):
        assert key in spot_action
        assert spot_action[key] == pytest.approx(spot_scorecard[key])
    assert "lane_scorecards" not in payload["metrics"]["kelly_sizing"][
        "validation_quality_pressure"
    ]["failures"]


def test_trading_validation_keeps_upbit_spot_lane_separate_from_binance_spot(
    tmp_path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "validation.db"
    repo = LivePerformanceRepository(live_path)
    rows = [
        ("binance-spot-1", "BTCUSDT", {"market": "spot", "side": "long"}),
        ("binance-spot-2", "ETHUSDT", {"market": "spot", "side": "long"}),
        ("upbit-spot-1", "KRW-BTC", {"market": "upbit_spot", "side": "long"}),
        ("upbit-spot-2", "KRW-ETH", {"market": "upbit_spot", "side": "long"}),
    ]
    for block_id, symbol, metadata in rows:
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=block_id,
                symbol=symbol,
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=101.0,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    payload = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=2,
            monte_carlo_iterations=32,
            monte_carlo_seed=72,
        )
    ).run_once(venue="binance")

    lane_actions = payload["metrics"]["lane_scorecards"]["lane_actions"]
    assert "spot" in lane_actions
    assert "upbit_spot" in lane_actions


def test_validation_lane_scorecard_blocks_scaling_when_costs_are_estimated(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_estimated_cost_simulation_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=73,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]

    assert "spot" not in lanes["scale_candidate_lanes"]
    assert "spot" in lanes["qualified_lanes"]
    assert "spot" in lanes["cost_evidence_weak_lanes"]
    assert spot["action"] == "cost_evidence_repair_before_scale"
    assert spot["scale_blocked_by_cost_precision"] is True
    assert spot["cost_precision_verified_rate_pct"] == pytest.approx(0.0)
    assert spot["requires_waiting_entry"] is True
    assert spot["authority_multiplier"] == pytest.approx(0.5)
    assert spot["risk_budget_multiplier"] == pytest.approx(0.5)
    assert spot["risk_budget_scale_decision"] == "capped_until_repairs"
    assert "cost_precision_cap" in spot["risk_budget_blockers"]
    assert "validation_evidence_cap" in spot["risk_budget_blockers"]


def test_validation_lane_scorecard_blocks_scaling_without_validation_passport(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "entry_quality": "wait_pullback",
            "entry_quality_score": 82.0,
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-missing-validation-{index}",
                symbol=f"VALID{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=173,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]

    assert "spot" not in lanes["scale_candidate_lanes"]
    assert "spot" in lanes["qualified_lanes"]
    assert "spot" in lanes["validation_evidence_weak_lanes"]
    assert spot["action"] == "validation_evidence_repair_before_scale"
    assert spot["requires_waiting_entry"] is True
    assert spot["scale_blocked_by_validation_evidence"] is True
    assert spot["validation_evidence_status"] == "missing"
    assert spot["validation_missing_dimensions"] == [
        "backtest",
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert spot["authority_multiplier"] == pytest.approx(0.5)
    assert "require_backtest_walk_forward_oos_live_shadow_before_scale" in spot[
        "entry_quality_requirements"
    ]


def test_validation_lane_scorecard_blocks_scaling_with_thin_validation_evidence(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "entry_quality": "wait_pullback",
            "entry_quality_score": 82.0,
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
        }
        if index == 1:
            metadata["validation_evidence"] = {
                "backtest_passed": True,
                "walk_forward_passed": True,
                "out_of_sample_passed": True,
                "live_shadow_passed": True,
            }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-thin-validation-{index}",
                symbol=f"THIN{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=174,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]

    assert "spot" not in lanes["scale_candidate_lanes"]
    assert "spot" in lanes["validation_evidence_weak_lanes"]
    assert spot["action"] == "validation_evidence_repair_before_scale"
    assert spot["validation_evidence_status"] == "thin"
    assert spot["validation_evidence_sample_count"] == 1
    assert spot["validation_evidence_coverage_rate_pct"] == pytest.approx(25.0)
    assert spot["validation_thin_dimensions"] == [
        "backtest",
        "walk_forward",
        "out_of_sample",
        "live_shadow",
    ]
    assert spot["validation_passed_dimension_count"] == 0
    assert spot["scale_blocked_by_validation_evidence"] is True
    assert spot["requires_waiting_entry"] is True
    assert spot["risk_budget_scale_decision"] == "capped_until_repairs"
    assert "validation_evidence_cap" in spot["risk_budget_blockers"]


def test_validation_lane_scorecard_surfaces_validation_evidence_pass_path(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "entry_quality": "wait_pullback",
            "entry_quality_score": 82.0,
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
            "validation_evidence": {
                "source": "validation_repair",
                "status": "repair_required",
                "required_evidence": ["fee", "spread", "slippage"],
                "required_checks": ["positive_net_edge"],
                "pass_collection_hooks": [
                    "sync precise fills/costs -> refresh_trading_validation"
                ],
                "pass_current_gaps": ["precise cost evidence missing"],
                "pass_criteria": [
                    "net edge remains positive after 2x cost stress"
                ],
                "verification_artifacts": [
                    "recorded cost components survive 2x stress"
                ],
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-pass-path-{index}",
                symbol=f"PATH{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=174,
        )
    )

    payload = service.run_once(venue="binance")
    spot = payload["metrics"]["lane_scorecards"]["lane_actions"]["spot"]

    assert spot["validation_evidence_status"] == "missing"
    assert spot["scale_blocked_by_validation_evidence"] is True
    assert spot["validation_evidence_required_evidence"] == [
        "fee",
        "slippage",
        "spread",
    ]
    assert spot["validation_evidence_required_checks"] == ["positive_net_edge"]
    assert spot["validation_evidence_pass_collection_hooks"] == [
        "sync precise fills/costs -> refresh_trading_validation"
    ]
    assert spot["validation_evidence_pass_current_gaps"] == [
        "precise cost evidence missing"
    ]
    assert spot["validation_evidence_pass_criteria"] == [
        "net edge remains positive after 2x cost stress"
    ]
    assert spot["validation_evidence_verification_artifacts"] == [
        "recorded cost components survive 2x stress"
    ]


def test_binance_futures_recorded_cost_requires_market_cost_components(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "futures",
            "side": "long",
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"futures-missing-market-cost-{index}",
                symbol=f"TEST{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=173,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    futures = lanes["lane_actions"]["futures_long"]

    assert "futures_long" not in lanes["scale_candidate_lanes"]
    assert "futures_long" in lanes["cost_evidence_weak_lanes"]
    assert futures["action"] == "cost_evidence_repair_before_scale"
    assert futures["cost_precision_counts"]["recorded"] == 0
    assert futures["cost_precision_counts"]["partial"] == 4
    assert futures["cost_precision_verified_rate_pct"] == pytest.approx(0.0)
    assert futures["scale_blocked_by_cost_precision"] is True


def test_binance_futures_zero_market_cost_components_can_be_recorded(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "futures",
            "side": "long",
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "funding": 0.0,
                "spread": 0.0,
                "slippage": 0.0,
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"futures-recorded-market-cost-{index}",
                symbol=f"TEST{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=174,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    futures = lanes["lane_actions"]["futures_long"]
    cost = payload["metrics"]["cost_simulation"]

    assert "futures_long" not in lanes["cost_evidence_weak_lanes"]
    assert futures["cost_precision_counts"]["recorded"] == 4
    assert futures["cost_precision_counts"]["partial"] == 0
    assert futures["cost_precision_verified_rate_pct"] == pytest.approx(100.0)
    assert futures["scale_blocked_by_cost_precision"] is False
    assert cost["present_cost_component_counts"] == {
        "fees": 4,
        "funding": 4,
        "slippage": 4,
        "spread": 4,
    }


def test_binance_spot_recorded_cost_requires_spread_and_slippage_components(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "cost_model_status": "recorded",
            "cost_components": {"fees": 0.01},
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-missing-market-cost-{index}",
                symbol=f"SPOT{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=177,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]

    assert "spot" not in lanes["scale_candidate_lanes"]
    assert "spot" in lanes["cost_evidence_weak_lanes"]
    assert spot["cost_precision_counts"]["recorded"] == 0
    assert spot["cost_precision_counts"]["partial"] == 4
    assert spot["cost_precision_verified_rate_pct"] == pytest.approx(0.0)
    assert spot["scale_blocked_by_cost_precision"] is True
    assert spot["requires_waiting_entry"] is True


def test_binance_spot_zero_market_cost_components_can_be_recorded(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-recorded-market-cost-{index}",
                symbol=f"SPOT{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=178,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]
    cost = payload["metrics"]["cost_simulation"]

    assert "spot" not in lanes["cost_evidence_weak_lanes"]
    assert spot["cost_precision_counts"]["recorded"] == 4
    assert spot["cost_precision_counts"]["partial"] == 0
    assert spot["cost_precision_verified_rate_pct"] == pytest.approx(100.0)
    assert spot["scale_blocked_by_cost_precision"] is False
    assert cost["present_cost_component_counts"] == {
        "fees": 4,
        "slippage": 4,
        "spread": 4,
    }


def test_kis_recorded_cost_requires_tax_spread_and_slippage_components(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((72_000, 72_500, 71_500, 72_300), start=1):
        metadata = {
            "horizon": "mid",
            "cost_model_status": "recorded",
            "cost_components": {"fees": 20.0},
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-missing-market-cost-{index}",
                symbol="005930",
                created_by="llm",
                status="closed",
                entry_price=70_000,
                exit_price=exit_price,
                qty=1,
                fees=20,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=175,
        )
    )

    payload = service.run_once(venue="kis")
    lanes = payload["metrics"]["lane_scorecards"]
    mid = lanes["lane_actions"]["mid"]

    assert "mid" not in lanes["scale_candidate_lanes"]
    assert "mid" in lanes["cost_evidence_weak_lanes"]
    assert mid["cost_precision_counts"]["recorded"] == 0
    assert mid["cost_precision_counts"]["partial"] == 4
    assert mid["scale_blocked_by_cost_precision"] is True
    assert mid["requires_waiting_entry"] is True


def test_kis_explicit_zero_market_cost_components_can_be_recorded(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((72_000, 72_500, 71_500, 72_300), start=1):
        metadata = {
            "horizon": "mid",
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 20.0,
                "taxes": 0.0,
                "spread": 0.0,
                "slippage": 0.0,
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-recorded-market-cost-{index}",
                symbol="455850",
                created_by="llm",
                status="closed",
                entry_price=70_000,
                exit_price=exit_price,
                qty=1,
                fees=20,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=176,
        )
    )

    payload = service.run_once(venue="kis")
    lanes = payload["metrics"]["lane_scorecards"]
    mid = lanes["lane_actions"]["mid"]
    cost = payload["metrics"]["cost_simulation"]

    assert "mid" not in lanes["cost_evidence_weak_lanes"]
    assert mid["cost_precision_counts"]["recorded"] == 4
    assert mid["cost_precision_counts"]["partial"] == 0
    assert mid["cost_precision_verified_rate_pct"] == pytest.approx(100.0)
    assert mid["scale_blocked_by_cost_precision"] is False
    assert cost["present_cost_component_counts"] == {
        "fees": 4,
        "slippage": 4,
        "spread": 4,
        "taxes": 4,
    }


def test_validation_lane_scorecard_blocks_scaling_when_entry_quality_is_chase(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "entry_quality": "late_chase",
            "entry_quality_score": 35.0,
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-chase-{index}",
                symbol=f"TEST{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=74,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]

    assert "spot" not in lanes["scale_candidate_lanes"]
    assert "spot" in lanes["entry_quality_weak_lanes"]
    assert spot["action"] == "entry_quality_repair_before_scale"
    assert spot["scale_blocked_by_entry_quality"] is True
    assert spot["requires_waiting_entry"] is True
    assert spot["authority_multiplier"] == pytest.approx(0.5)
    assert spot["avg_entry_quality_score"] == pytest.approx(35.0)
    assert spot["bad_entry_quality_rate_pct"] == pytest.approx(100.0)
    assert spot["bad_entry_quality_label_counts"] == {"late_chase": 4}
    assert spot["dominant_bad_entry_quality_label"] == "late_chase"


def test_validation_lane_scorecard_blocks_scaling_when_validation_repair_enforced(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    repair_action_id = "validation_repair.wfa"
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "entry_quality": "wait_pullback",
            "entry_quality_score": 80.0,
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
            "validation_repair_enforcement": {
                "version": "validation_repair_enforcement_v1",
                "repair_action_ids": [repair_action_id],
                "scale_up_blocked": True,
                "waiting_entry_required": True,
                "budget_multiplier": 0.25,
                "adjustments": [
                    {
                        "field": "quote_budget_usdt",
                        "from": 400.0,
                        "to": 100.0,
                        "reason": "validation_repair_scale_up_blocked_probe_budget",
                    }
                ],
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-validation-repair-{index}",
                symbol=f"REPAIR{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=76,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]

    assert "spot" not in lanes["scale_candidate_lanes"]
    assert "spot" in lanes["qualified_lanes"]
    assert "spot" in lanes["validation_repair_weak_lanes"]
    assert spot["action"] == "validation_repair_enforced_before_scale"
    assert spot["scale_blocked_by_validation_repair"] is True
    assert spot["requires_waiting_entry"] is True
    assert spot["authority_multiplier"] == pytest.approx(0.25)
    assert spot["validation_repair_enforced_count"] == 4
    assert spot["validation_repair_scale_up_blocked_count"] == 4
    assert spot["validation_repair_waiting_entry_count"] == 4
    assert spot["validation_repair_avg_budget_multiplier"] == pytest.approx(0.25)
    assert spot["validation_repair_action_counts"] == {repair_action_id: 4}
    assert spot["validation_repair_adjustment_reason_counts"] == {
        "validation_repair_scale_up_blocked_probe_budget": 4
    }
    assert spot["validation_repair_requirements"] == [
        "respect_validation_repair_enforcement_until_repair_passes",
        "keep_probe_or_waiting_entry_when_repair_blocks_scale_up",
    ]
    assert "respect_validation_repair_enforcement_until_repair_passes" in spot[
        "entry_quality_requirements"
    ]


def test_validation_lane_scorecard_does_not_treat_wait_pullback_as_chase(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index, exit_price in enumerate((104.0, 105.0, 103.5, 104.5), start=1):
        metadata = {
            "market": "spot",
            "side": "long",
            "entry_quality": "wait_pullback",
            "entry_quality_score": 80.0,
            "cost_model_status": "recorded",
            "cost_components": {
                "fees": 0.01,
                "spread": 0.0,
                "slippage": 0.0,
            },
            "validation_evidence": {
                "backtest_passed": True,
                "walk_forward_passed": True,
                "out_of_sample_passed": True,
                "live_shadow_passed": True,
            },
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"spot-wait-pullback-{index}",
                symbol=f"TEST{index}USDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=exit_price,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=75,
        )
    )

    payload = service.run_once(venue="binance")
    lanes = payload["metrics"]["lane_scorecards"]
    spot = lanes["lane_actions"]["spot"]

    assert "spot" not in lanes["entry_quality_weak_lanes"]
    assert "spot" in lanes["scale_candidate_lanes"]
    assert spot["scale_blocked_by_entry_quality"] is False
    assert spot["risk_budget_multiplier"] >= 1.0
    assert spot["risk_budget_scale_decision"] == "eligible_to_scale"
    assert spot["raw_kelly_fraction"] > 0.0
    assert spot["bad_entry_quality_rate_pct"] == pytest.approx(0.0)
    assert spot["good_entry_quality_label_counts"] == {"wait_pullback": 4}
    assert spot["dominant_good_entry_quality_label"] == "wait_pullback"


def test_validation_lane_scorecard_normalizes_etf_and_binance_alias_lanes(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    for index in range(3):
        metadata = {
            "horizon": "mid",
            "name": "PLUS 고배당주",
            "cost_model_status": "recorded",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="kis",
                block_id=f"kis-plus-etf-{index}",
                symbol="455850",
                created_by="llm",
                status="closed",
                entry_price=10_000,
                exit_price=10_200,
                qty=1,
                fees=20,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )
    for index in range(3):
        metadata = {
            "lane": "futures:short",
            "market": "perp",
            "cost_model_status": "recorded",
        }
        repo.upsert_performance(
            BlockPerformanceInput(
                venue="binance",
                block_id=f"bn-futures-short-alias-{index}",
                symbol="ETHUSDT",
                created_by="llm",
                status="closed",
                entry_price=100.0,
                exit_price=98.0,
                qty=1.0,
                fees=0.01,
                filled=True,
                metadata=metadata,
            ),
            source={"metadata": metadata},
        )

    kis_payload = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=3,
            monte_carlo_iterations=32,
            monte_carlo_seed=76,
        )
    ).run_once(venue="kis")
    binance_payload = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=3,
            monte_carlo_iterations=32,
            monte_carlo_seed=77,
        )
    ).run_once(venue="binance")

    kis_actions = kis_payload["metrics"]["lane_scorecards"]["lane_actions"]
    binance_actions = binance_payload["metrics"]["lane_scorecards"]["lane_actions"]

    assert "core_etf" in kis_actions
    assert "mid" not in kis_actions
    assert "futures_short" in binance_actions
    assert "futures:short" not in binance_actions


def test_validation_service_models_monte_carlo_sequence_tail_risk(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_live_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=5,
            monte_carlo_iterations=128,
            monte_carlo_seed=31,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    monte_carlo = payload["monte_carlo"]

    assert monte_carlo["status"] == "ok"
    assert monte_carlo["sample_count"] == 6
    assert monte_carlo["max_consecutive_loss_p95"] >= 1
    assert monte_carlo["probability_loss_streak_ge_3_pct"] >= 0
    assert monte_carlo["final_return_expected_shortfall_p05_pct"] <= monte_carlo[
        "final_return_p05_pct"
    ]
    assert monte_carlo["max_drawdown_expected_shortfall_p05_pct"] <= monte_carlo[
        "max_drawdown_p05_pct"
    ]
    assert monte_carlo["sequence_risk_level"] in {"low", "medium", "high", "critical"}
    assert discipline_by_id["monte_carlo"]["metric"] == monte_carlo


def test_validation_service_does_not_pass_monte_carlo_with_weak_sample(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_live_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=30,
            monte_carlo_iterations=128,
            monte_carlo_seed=31,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    monte_carlo = payload["monte_carlo"]

    assert monte_carlo["sample_count"] == 6
    assert monte_carlo["min_sample_count"] == 30
    assert monte_carlo["sample_adequacy"] == "weak"
    assert discipline_by_id["monte_carlo"]["status"] == "warn"


def test_validation_service_builds_risk_of_ruin_profile(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_ruin_profile_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            initial_equity=100.0,
            min_sample_count=5,
            monte_carlo_iterations=128,
            monte_carlo_seed=47,
            ruin_drawdown_pct=5.0,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    profile = payload["metrics"]["ruin_profile"]

    assert profile["status"] in {"warn", "fail"}
    assert profile["ruin_drawdown_pct"] == pytest.approx(5.0)
    assert profile["risk_of_ruin_pct"] == payload["monte_carlo"]["risk_of_ruin_pct"]
    assert profile["ruin_event_count"] > 0
    assert profile["earliest_trade_index_to_ruin"] >= 1
    assert profile["median_trade_index_to_ruin"] >= 1
    assert profile["ruin_severity"] in {"low", "medium", "high", "critical"}
    assert profile["governor_action"] in {
        "de_risk",
        "risk_off",
        "halt_new_risk",
    }
    assert discipline_by_id["risk_of_ruin"]["metric"] == profile


def test_validation_service_replays_metadata_crisis_stress_scenarios(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_crisis_stress_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            max_drawdown_limit_pct=20.0,
            monte_carlo_iterations=32,
            monte_carlo_seed=53,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    stress = payload["metrics"]["stress"]

    assert stress["scenario_source"] == "metadata_crisis_returns"
    assert stress["covered_crisis_sample_count"] == 4
    assert stress["scenario_count"] == 3
    assert stress["worst_crisis_scenario_id"] == "luna_depeg"
    assert stress["worst_drawdown_pct"] < -20.0
    assert any(row["scenario_id"] == "luna_depeg" for row in stress["scenarios"])
    assert discipline_by_id["stress_test"]["status"] == "fail"
    assert discipline_by_id["stress_test"]["metric"] == stress


def test_validation_service_does_not_pass_sparse_crisis_stress_coverage(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_sparse_crisis_stress_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=6,
            max_drawdown_limit_pct=20.0,
            monte_carlo_iterations=32,
            monte_carlo_seed=65,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    stress = payload["metrics"]["stress"]

    assert stress["scenario_source"] == "metadata_crisis_returns"
    assert stress["covered_crisis_sample_count"] == 1
    assert stress["stress_coverage_rate_pct"] == pytest.approx(16.666667)
    assert stress["worst_drawdown_pct"] > -20.0
    assert stress["status"] == "warn"
    assert discipline_by_id["stress_test"]["status"] == "warn"


def test_validation_service_builds_orderbook_capacity_curve(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_capacity_curve_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=3,
            monte_carlo_iterations=32,
            monte_carlo_seed=59,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    capacity = payload["metrics"]["capacity"]

    assert capacity["status"] == "fail"
    assert capacity["liquidity_sample_count"] == 3
    assert capacity["target_depth_bps"] == pytest.approx(30.0)
    assert capacity["min_practical_capacity_usdt"] == pytest.approx(150.0)
    assert capacity["min_capacity_ratio"] == pytest.approx(1.25)
    assert capacity["tightest_symbol"] == "ALTUSDT"
    assert capacity["capacity_method"] == "orderbook_depth_and_turnover"
    assert capacity["examples"][0]["symbol"] == "ALTUSDT"
    assert discipline_by_id["capacity_analysis"]["status"] == "fail"
    assert discipline_by_id["capacity_analysis"]["metric"] == capacity


def test_validation_service_does_not_pass_sparse_capacity_coverage(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_sparse_capacity_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=6,
            monte_carlo_iterations=32,
            monte_carlo_seed=60,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    capacity = payload["metrics"]["capacity"]

    assert capacity["covered_sample_count"] == 1
    assert capacity["capacity_coverage_rate_pct"] == pytest.approx(16.666667)
    assert capacity["min_capacity_ratio"] == pytest.approx(100.0)
    assert capacity["status"] == "warn"
    assert discipline_by_id["capacity_analysis"]["status"] == "warn"


def test_validation_service_treats_low_metadata_capacity_as_repair_warning(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_low_metadata_capacity_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=3,
            monte_carlo_iterations=32,
            monte_carlo_seed=61,
        )
    )

    payload = service.run_once(venue="kis")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    capacity = payload["metrics"]["capacity"]

    assert capacity["capacity_method"] == "metadata_capacity_ratio"
    assert capacity["min_capacity_ratio"] == pytest.approx(0.01)
    assert capacity["proxy_status"] == "fail"
    assert capacity["status"] == "warn"
    assert discipline_by_id["capacity_analysis"]["status"] == "warn"
    assert payload["summary"]["core_fail_count"] == 0
    assert payload["summary"]["hard_blocking_count"] == 0


def test_validation_service_does_not_pass_sparse_factor_exposure_coverage(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_sparse_factor_exposure_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=6,
            monte_carlo_iterations=32,
            monte_carlo_seed=62,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    factor = payload["metrics"]["factor_exposure"]

    assert factor["covered_sample_count"] == 1
    assert factor["factor_coverage_rate_pct"] == pytest.approx(16.666667)
    assert factor["top_factor_share_pct"] <= 70.0
    assert factor["status"] == "warn"
    assert discipline_by_id["factor_exposure"]["status"] == "warn"


def test_validation_service_does_not_pass_sparse_correlation_coverage(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_sparse_correlation_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=6,
            monte_carlo_iterations=32,
            monte_carlo_seed=63,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    correlation = payload["metrics"]["correlation_proxy"]

    assert correlation["method"] == "rolling_return_window"
    assert correlation["covered_sample_count"] == 2
    assert correlation["correlation_coverage_rate_pct"] == pytest.approx(33.333333)
    assert correlation["max_abs_correlation"] < 0.65
    assert correlation["status"] == "warn"
    assert discipline_by_id["correlation"]["status"] == "warn"


def test_validation_service_treats_single_symbol_correlation_as_warn_not_missing(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_single_symbol_correlation_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=3,
            monte_carlo_iterations=32,
            monte_carlo_seed=65,
        )
    )

    payload = service.run_once(venue="kis")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    correlation = payload["metrics"]["correlation_proxy"]

    assert correlation["method"] == "rolling_return_window_single_symbol"
    assert correlation["covered_sample_count"] == 1
    assert correlation["pair_count"] == 0
    assert correlation["pair_adequacy"] == "needs_at_least_two_symbols"
    assert correlation["status"] == "warn"
    assert discipline_by_id["correlation"]["status"] == "warn"
    assert "1개 종목" in discipline_by_id["correlation"]["evidence"]
    assert "두 종목 이상" in discipline_by_id["correlation"]["action"]


def test_validation_service_derives_kis_correlation_cluster_from_quote_sector(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    repo = LivePerformanceRepository(live_path)
    repo.upsert_performance(
        BlockPerformanceInput(
            venue="kis",
            block_id="kr-sector-1",
            symbol="009150",
            created_by="llm",
            status="closed",
            entry_price=100_000.0,
            exit_price=102_000.0,
            qty=1.0,
            fees=100.0,
            filled=True,
            metadata={
                "quote": {
                    "raw": {
                        "bstp_kor_isnm": "전기·전자",
                        "rprs_mrkt_kor_name": "KOSPI200",
                    }
                },
                "regime": "risk_on",
                "factor_exposures": {"kr_equity_beta": 1.0},
                "capacity_krw": 10_000_000,
                "notional_krw": 100_000,
            },
        ),
        source={
            "metadata": {
                "quote": {
                    "raw": {
                        "bstp_kor_isnm": "전기·전자",
                        "rprs_mrkt_kor_name": "KOSPI200",
                    }
                },
                "regime": "risk_on",
                "factor_exposures": {"kr_equity_beta": 1.0},
                "capacity_krw": 10_000_000,
                "notional_krw": 100_000,
            }
        },
    )
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=3,
            monte_carlo_iterations=16,
            monte_carlo_seed=73,
        )
    )

    payload = service.run_once(venue="kis")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    correlation = payload["metrics"]["correlation_proxy"]

    assert correlation["method"] == "metadata_cluster_concentration_proxy"
    assert correlation["status"] == "warn"
    assert correlation["covered_sample_count"] == 1
    assert correlation["clusters"] == {"sector:전기·전자": 1}
    assert correlation["cluster_source_counts"] == {"kis_quote_sector": 1}
    assert discipline_by_id["correlation"]["status"] == "warn"


def test_validation_service_does_not_pass_sparse_regime_coverage(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_sparse_regime_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=6,
            monte_carlo_iterations=32,
            monte_carlo_seed=64,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    regime = payload["metrics"]["regime_scorecards"]

    assert regime["regime_count"] == 2
    assert regime["covered_sample_count"] == 2
    assert regime["regime_coverage_rate_pct"] == pytest.approx(33.333333)
    assert regime["worst_expectancy_pct"] > 0
    assert regime["status"] == "warn"
    assert discipline_by_id["regime_test"]["status"] == "warn"


def test_validation_service_deepens_regime_correlation_and_factor_exposure(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_regime_correlation_factor_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=4,
            monte_carlo_iterations=32,
            monte_carlo_seed=61,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    regime = payload["metrics"]["regime_scorecards"]
    correlation = payload["metrics"]["correlation_proxy"]
    factor = payload["metrics"]["factor_exposure"]

    assert regime["regime_count"] == 3
    assert regime["worst_regime"] == "risk_off"
    assert regime["negative_regime_count"] == 1
    assert regime["regime_coverage_rate_pct"] == pytest.approx(100.0)
    assert correlation["method"] == "rolling_return_window"
    assert correlation["pair_count"] >= 1
    assert correlation["max_abs_correlation"] > 0.95
    assert correlation["top_pair"] == ["BTCUSDT", "ETHUSDT"]
    assert factor["factor_coverage_rate_pct"] == pytest.approx(100.0)
    assert factor["dominant_factor"] == "momentum"
    assert factor["weighted_factor_totals"]["momentum"] > factor[
        "weighted_factor_totals"
    ]["growth"]
    assert discipline_by_id["regime_test"]["status"] == "warn"
    assert discipline_by_id["correlation"]["status"] == "fail"
    assert discipline_by_id["factor_exposure"]["status"] == "warn"


def test_validation_service_builds_drawdown_budget_guidance(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_drawdown_budget_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            initial_equity=10_000.0,
            min_sample_count=4,
            max_drawdown_limit_pct=20.0,
            monte_carlo_iterations=64,
            monte_carlo_seed=37,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    budget = payload["metrics"]["drawdown_budget"]

    assert budget["status"] == "fail"
    assert budget["initial_equity"] == pytest.approx(10_000.0)
    assert budget["peak_equity"] == pytest.approx(12_500.0)
    assert budget["current_equity"] == pytest.approx(9_000.0)
    assert budget["current_drawdown_pct"] == pytest.approx(-28.0)
    assert budget["max_drawdown_pct"] == pytest.approx(-28.0)
    assert budget["drawdown_limit_pct"] == pytest.approx(-20.0)
    assert budget["remaining_budget_pct"] == pytest.approx(-8.0)
    assert budget["recovery_to_peak_pct"] == pytest.approx(38.888889)
    assert budget["risk_multiplier"] == pytest.approx(0.0)
    assert budget["governor_action"] == "risk_off"
    assert discipline_by_id["mdd_limit"]["status"] == "fail"
    assert discipline_by_id["mdd_limit"]["metric"] == budget


def test_validation_service_builds_risk_adjusted_performance_packet(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_live_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=5,
            monte_carlo_iterations=64,
            monte_carlo_seed=41,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    risk = payload["metrics"]["risk_adjusted_performance"]

    assert risk["status"] == "pass"
    assert risk["sample_count"] == 6
    assert risk["volatility_pct"] > 0
    assert risk["downside_deviation_pct"] > 0
    assert risk["sharpe_ratio"] == pytest.approx(payload["metrics"]["sharpe_ratio"])
    assert risk["sortino_ratio"] == pytest.approx(payload["metrics"]["sortino_ratio"])
    assert risk["calmar_ratio"] == pytest.approx(payload["metrics"]["calmar_ratio"])
    assert risk["return_to_drawdown_ratio"] == pytest.approx(
        payload["metrics"]["calmar_ratio"]
    )
    assert risk["quality_grade"] in {"A", "B", "C", "D", "F"}
    assert risk["primary_risk_flag"] in {
        "none",
        "total_volatility",
        "downside_volatility",
        "drawdown_efficiency",
        "negative_edge",
    }
    assert discipline_by_id["sharpe_ratio"]["metric"] == risk
    assert discipline_by_id["sortino_ratio"]["metric"] == risk
    assert discipline_by_id["calmar_ratio"]["metric"] == risk


def test_validation_service_does_not_pass_risk_adjusted_metrics_with_weak_sample(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_live_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            min_sample_count=30,
            monte_carlo_iterations=64,
            monte_carlo_seed=41,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    risk = payload["metrics"]["risk_adjusted_performance"]

    assert risk["sample_count"] == 6
    assert risk["min_sample_count"] == 30
    assert risk["sample_adequacy"] == "weak"
    assert risk["status"] == "warn"
    assert discipline_by_id["sharpe_ratio"]["status"] == "warn"
    assert discipline_by_id["sortino_ratio"]["status"] == "warn"
    assert discipline_by_id["calmar_ratio"]["status"] == "warn"


def test_validation_service_grades_risk_ratios_independently(
    tmp_path: Path,
) -> None:
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=tmp_path / "trading_validation.db",
            live_performance_db_path=tmp_path / "live_performance.db",
            min_sample_count=10,
            sharpe_min=0.5,
            sortino_min=0.75,
            calmar_min=0.5,
        )
    )
    metrics = {
        "sample_count": 30,
        "total_return_pct": 4.0,
        "expectancy_pct": 0.25,
        "cost_total": 1.0,
        "profit_factor": 1.8,
        "max_drawdown_pct": -4.0,
        "sharpe_ratio": 1.1,
        "sortino_ratio": 0.4,
        "calmar_ratio": 1.0,
        "recovery_factor": 1.4,
        "kelly_fraction": 0.12,
        "fractional_kelly_025": 0.03,
        "risk_of_ruin_pct": 0.0,
        "data_quality": {"status": "pass", "sample_count": 30},
        "cost_simulation": {
            "status": "pass",
            "sample_count": 30,
            "cost_drag_pct_of_gross_pnl": 8.0,
            "stressed_net_pnl_by_cost_multiplier": {"2x": 2.0},
        },
        "stress": {"status": "pass", "worst_drawdown_pct": -6.0},
        "capacity": {
            "status": "pass",
            "covered_sample_count": 30,
            "min_capacity_ratio": 25.0,
        },
        "regime_scorecards": {
            "status": "pass",
            "regime_count": 2,
            "worst_expectancy_pct": 0.1,
        },
        "correlation_proxy": {
            "status": "pass",
            "method": "rolling_return_window",
            "covered_sample_count": 10,
            "max_abs_correlation": 0.2,
        },
        "factor_exposure": {
            "status": "pass",
            "covered_sample_count": 30,
            "top_factor_share_pct": 30.0,
        },
        "pattern_lab": {
            "status": "ok",
            "validation_status": "pass",
            "active_set_count": 1,
            "rejected_set_count": 0,
            "high_overfit_count": 0,
            "unknown_overfit_count": 0,
            "active_missing_walk_forward_set_count": 0,
            "active_walk_forward_coverage_rate_pct": 100.0,
            "walk_forward_pass_rate_pct": 80.0,
            "walk_forward_window_pass_rate_pct": 80.0,
            "missing_out_of_sample_set_count": 0,
            "active_missing_out_of_sample_set_count": 0,
            "active_out_of_sample_coverage_rate_pct": 100.0,
            "out_of_sample_coverage_rate_pct": 100.0,
            "min_out_of_sample_profit_factor": 1.2,
            "worst_out_of_sample_expectancy_r": 0.1,
            "avg_train_test_expectancy_gap_r": 0.05,
        },
    }
    metrics["risk_adjusted_performance"] = service._risk_adjusted_performance_metrics(
        metrics
    )
    metrics["profitability_quality"] = service._profitability_quality_metrics(metrics)
    metrics["recovery_profile"] = {
        "status": "pass",
        "recovery_factor": 1.4,
        "recovery_trade_count": 3,
    }
    metrics["drawdown_budget"] = {
        "status": "pass",
        "current_drawdown_pct": -1.0,
        "max_drawdown_pct": -4.0,
        "risk_multiplier": 1.0,
    }
    metrics["ruin_profile"] = {
        "status": "pass",
        "risk_of_ruin_pct": 0.0,
        "median_trade_index_to_ruin": 0,
    }
    metrics["validation_quality_pressure"] = (
        service._validation_quality_pressure(metrics)
    )
    metrics["kelly_sizing"] = service._kelly_sizing_metrics(metrics)

    disciplines = service._build_disciplines(
        metrics=metrics,
        monte_carlo={
            "sequence_risk_level": "low",
            "sample_adequacy": "sufficient",
            "max_consecutive_loss_p95": 2,
            "max_drawdown_expected_shortfall_p05_pct": -5.0,
        },
    )
    discipline_by_id = {row["id"]: row for row in disciplines}

    assert metrics["risk_adjusted_performance"]["status"] == "warn"
    assert discipline_by_id["sharpe_ratio"]["status"] == "pass"
    assert discipline_by_id["sortino_ratio"]["status"] == "warn"
    assert discipline_by_id["calmar_ratio"]["status"] == "pass"


def test_validation_service_builds_profitability_and_recovery_packets(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_profit_recovery_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            initial_equity=10_000.0,
            min_sample_count=4,
            monte_carlo_iterations=64,
            monte_carlo_seed=43,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    profitability = payload["metrics"]["profitability_quality"]
    recovery = payload["metrics"]["recovery_profile"]

    assert profitability["status"] == "pass"
    assert profitability["gross_profit"] == pytest.approx(3200.0)
    assert profitability["gross_loss"] == pytest.approx(2000.0)
    assert profitability["profit_factor"] == pytest.approx(1.6)
    assert profitability["loss_absorption_ratio"] == pytest.approx(1.6)
    assert profitability["edge_grade"] == "good"
    assert profitability["average_win"] == pytest.approx(1066.666667)
    assert profitability["average_loss"] == pytest.approx(2000.0)
    assert recovery["status"] == "warn"
    assert recovery["max_drawdown_cash"] == pytest.approx(-2000.0)
    assert recovery["recovery_factor"] == pytest.approx(0.6)
    assert recovery["recovered_from_max_drawdown"] is True
    assert recovery["trough_trade_index"] == 2
    assert recovery["recovery_trade_index"] == 4
    assert recovery["recovery_trade_count"] == 2
    assert recovery["required_gain_from_trough_pct"] == pytest.approx(22.222222)
    assert discipline_by_id["profit_factor"]["metric"] == profitability
    assert discipline_by_id["recovery_factor"]["metric"] == recovery


def test_validation_service_does_not_pass_profitability_with_weak_sample(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_profit_recovery_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            initial_equity=10_000.0,
            min_sample_count=30,
            monte_carlo_iterations=64,
            monte_carlo_seed=43,
        )
    )

    payload = service.run_once(venue="binance")
    discipline_by_id = {row["id"]: row for row in payload["disciplines"]}
    profitability = payload["metrics"]["profitability_quality"]
    recovery = payload["metrics"]["recovery_profile"]

    assert profitability["sample_count"] == 4
    assert profitability["min_sample_count"] == 30
    assert profitability["sample_adequacy"] == "weak"
    assert profitability["profit_factor"] == pytest.approx(1.6)
    assert profitability["status"] == "warn"
    assert recovery["sample_count"] == 4
    assert recovery["sample_adequacy"] == "weak"
    assert recovery["status"] == "warn"
    assert discipline_by_id["profit_factor"]["status"] == "warn"
    assert discipline_by_id["recovery_factor"]["status"] == "warn"


def test_validation_service_attributes_failure_by_symbol_horizon_family_and_regime(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live_performance.db"
    validation_path = tmp_path / "trading_validation.db"
    _seed_failure_attribution_performance(live_path)
    service = TradingValidationService(
        TradingValidationConfig(
            validation_db_path=validation_path,
            live_performance_db_path=live_path,
            initial_equity=10_000.0,
            min_sample_count=3,
            monte_carlo_iterations=64,
            monte_carlo_seed=47,
        )
    )

    payload = service.run_once(venue="kis")
    attribution = payload["metrics"]["failure_attribution"]

    assert attribution["status"] == "ok"
    assert attribution["sample_count"] == 6
    worst_labels = {
        (row["group_type"], row["group"])
        for row in attribution["worst_groups"]
    }
    assert ("symbol", "005930") in worst_labels
    assert ("horizon", "short") in worst_labels
    assert ("strategy_family", "late_chase") in worst_labels
    assert ("market_regime", "rotation") in worst_labels
    best_labels = {
        (row["group_type"], row["group"])
        for row in attribution["best_groups"]
    }
    assert ("symbol", "000660") in best_labels
    assert ("horizon", "mid") in best_labels
    assert any("005930" in item or "late_chase" in item for item in attribution["recovery_focus"])
    assert any(item.startswith("실패 귀속:") for item in payload["operator_guidance"])
