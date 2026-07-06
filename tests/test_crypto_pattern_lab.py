from __future__ import annotations

import ast
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.crypto_pattern_lab import (
    CryptoPatternLabConfig,
    CryptoPatternLabRepository,
    CryptoPatternLabService,
    FreqtradeOHLCVImporter,
    FreqtradeStrategyExtractor,
    FreqtradeDataCatalog,
    HermesKlineReader,
    PatternBacktestLab,
    PatternOptimizationLab,
)


def test_repository_saves_strategy_source_and_patterns(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_strategy_source(
        {
            "source_id": "sha256:abc",
            "path": "/tmp/SampleStrategy.py",
            "strategy_name": "SampleStrategy",
            "source_hash": "abc",
            "status": "ok",
        }
    )
    repo.save_patterns(
        [
            {
                "pattern_id": "sha256:abc:rsi_mean_reversion:long:5m",
                "source_id": "sha256:abc",
                "name": "SampleStrategy rsi_mean_reversion long",
                "family": "rsi_mean_reversion",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["rsi", "volume"],
                "expression": {"enter_column": "enter_long"},
                "risk_tags": ["oversold_bounce"],
            }
        ]
    )

    context = repo.pattern_context(symbols=["BTCUSDT"], limit=10)

    assert context["status"] == "ok"
    assert context["patterns"][0]["family"] == "rsi_mean_reversion"
    assert context["patterns"][0]["direction"] == "long"


def test_repository_configures_sqlite_for_concurrent_runner_access(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")

    with repo._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout_ms >= 30000


def test_hermes_kline_reader_uses_busy_timeout_for_concurrent_reads(
    tmp_path: Path,
) -> None:
    reader = HermesKlineReader(tmp_path / "crypto_market_research.db")

    with reader._connect() as conn:
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert busy_timeout_ms >= 30000


def test_crypto_pattern_lab_context_exposes_evidence_and_license_policy(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_strategy_source(
        {
            "source_id": "sha256:gpl",
            "path": "/tmp/GplStrategy.py",
            "strategy_name": "GplStrategy",
            "source_hash": "gpl",
            "license": "GPL-3.0",
            "license_policy": "reference_only",
            "status": "ok",
        }
    )
    repo.save_patterns(
        [
            {
                "pattern_id": "sha256:gpl:rsi_mean_reversion:long:15m",
                "source_id": "sha256:gpl",
                "name": "GplStrategy rsi_mean_reversion long",
                "family": "rsi_mean_reversion",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["rsi"],
                "expression": {"enter_column": "enter_long"},
                "risk_tags": ["mean_reversion"],
            }
        ]
    )
    repo.save_backtest(
        {
            "pattern_id": "sha256:gpl:rsi_mean_reversion:long:15m",
            "symbol": "BTCUSDT",
            "interval": "15m",
            "trade_count": 40,
            "win_rate": 0.55,
            "expectancy_r": 0.2,
            "avg_r": 0.1,
            "profit_factor": 1.4,
            "max_loss_r": -1.0,
            "mfe_r": 1.0,
            "mae_r": -0.5,
            "score": 82.0,
        }
    )

    context = repo.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["scorecards"][0]["pattern_key"] == "rsi_mean_reversion:long:15m"
    assert context["evidence"][0]["source"] == "crypto_pattern_lab"
    assert context["sources"][0]["license_policy"] == "reference_only"


def test_pattern_context_only_promotes_qualified_backtests_as_evidence(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "good-pattern",
                "source_id": "source-good",
                "name": "Good pattern",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": [],
            },
            {
                "pattern_id": "weak-pattern",
                "source_id": "source-weak",
                "name": "Weak pattern",
                "family": "rsi_mean_reversion",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["rsi"],
                "expression": {},
                "risk_tags": [],
            },
        ]
    )
    base = {
        "symbol": "BTCUSDT",
        "interval": "15m",
        "max_loss_r": -1.0,
        "mfe_r": 1.0,
        "mae_r": -0.5,
    }
    repo.save_backtest(
        {
            **base,
            "pattern_id": "good-pattern",
            "trade_count": 12,
            "win_rate": 0.58,
            "expectancy_r": 0.22,
            "avg_r": 0.12,
            "profit_factor": 1.45,
            "score": 80.0,
        }
    )
    repo.save_backtest(
        {
            **base,
            "pattern_id": "weak-pattern",
            "trade_count": 40,
            "win_rate": 0.48,
            "expectancy_r": -0.05,
            "avg_r": -0.02,
            "profit_factor": 0.94,
            "score": 79.0,
        }
    )

    context = repo.pattern_context(symbols=["BTCUSDT"], limit=5)

    assert [row["pattern_id"] for row in context["qualified_scorecards"]] == [
        "good-pattern"
    ]
    assert [row["payload"]["pattern_key"] for row in context["evidence"]] == [
        "ema_trend:long:15m"
    ]
    weak = next(
        row for row in context["scorecards"] if row["pattern_id"] == "weak-pattern"
    )
    assert weak["entry_quality"]["qualifies_for_entry"] is False
    assert "win_rate" in weak["entry_quality"]["failed_reasons"]


def test_repository_prunes_old_pattern_backtests(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    base = {
        "pattern_id": "p1",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "trade_count": 1,
        "win_rate": 1.0,
        "expectancy_r": 1.0,
        "avg_r": 1.0,
        "profit_factor": 2.0,
        "max_loss_r": 0.0,
        "mfe_r": 1.0,
        "mae_r": 0.0,
        "score": 80.0,
    }
    repo.save_backtest({**base, "evaluated_at": old})
    repo.save_backtest({**base, "evaluated_at": recent})

    result = repo.prune_history(retention_days=90)
    scorecards = repo.latest_scorecards(symbols=["BTCUSDT"], limit=10)

    assert result["backtests_deleted"] == 1
    assert len(scorecards) == 1


def test_repository_prunes_stale_backtests_and_optimization_trials(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    now = datetime.now(timezone.utc)
    base = {
        "pattern_id": "p1",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "trade_count": 1,
        "win_rate": 1.0,
        "expectancy_r": 1.0,
        "avg_r": 1.0,
        "profit_factor": 2.0,
        "max_loss_r": 0.0,
        "mfe_r": 1.0,
        "mae_r": 0.0,
        "score": 80.0,
    }
    for idx in range(6):
        repo.save_backtest(
            {
                **base,
                "score": 80.0 + idx,
                "evaluated_at": (now + timedelta(seconds=idx)).isoformat(),
            }
        )
    repo.save_optimization_result(
        {
            "run_id": "run-1",
            "pattern_id": "p1",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "objective": "expectancy",
            "status": "ok",
            "trials": [
                {
                    "trial_id": f"trial-{idx}",
                    "parameter_set": {"x": idx},
                    "objective_score": float(idx),
                    "evaluated_at": (now + timedelta(seconds=idx)).isoformat(),
                }
                for idx in range(6)
            ],
        }
    )

    result = repo.prune_history(
        retention_days=90,
        backtests_per_tuple=3,
        optimizer_trials_per_run=2,
    )

    assert result["stale_backtests_deleted"] == 3
    assert result["stale_optimization_trials_deleted"] == 4
    assert result["vacuumed"] is True
    with sqlite3.connect(repo.path) as conn:
        backtests = conn.execute("SELECT COUNT(*) FROM pattern_backtests").fetchone()[0]
        trials = conn.execute("SELECT COUNT(*) FROM optimization_trials").fetchone()[0]
    assert backtests == 3
    assert trials == 2


def test_repository_prunes_stale_optimization_runs_per_tuple(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    now = datetime.now(timezone.utc)
    for idx in range(5):
        repo.save_optimization_result(
            {
                "run_id": f"run-{idx}",
                "pattern_id": "p1",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "objective": "expectancy",
                "status": "ok",
                "started_at": (now + timedelta(minutes=idx)).isoformat(),
                "finished_at": (now + timedelta(minutes=idx, seconds=30)).isoformat(),
                "trials": [
                    {
                        "trial_id": f"trial-{idx}-{trial_idx}",
                        "parameter_set": {"x": trial_idx},
                        "objective_score": float(trial_idx),
                        "evaluated_at": (
                            now + timedelta(minutes=idx, seconds=trial_idx)
                        ).isoformat(),
                    }
                    for trial_idx in range(3)
                ],
            }
        )

    result = repo.prune_history(
        retention_days=90,
        optimizer_runs_per_tuple=2,
        optimizer_trials_per_run=2,
    )

    assert result["stale_optimization_runs_deleted"] == 3
    assert result["vacuumed"] is True
    with sqlite3.connect(repo.path) as conn:
        runs = conn.execute(
            "SELECT run_id FROM optimization_runs ORDER BY finished_at"
        ).fetchall()
        trials = conn.execute(
            "SELECT run_id, COUNT(*) FROM optimization_trials GROUP BY run_id"
        ).fetchall()
    assert [row[0] for row in runs] == ["run-3", "run-4"]
    assert trials == [("run-3", 2), ("run-4", 2)]


def test_repository_prunes_global_pattern_lab_row_caps(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    now = datetime.now(timezone.utc)
    base = {
        "trade_count": 1,
        "win_rate": 1.0,
        "expectancy_r": 1.0,
        "avg_r": 1.0,
        "profit_factor": 2.0,
        "max_loss_r": 0.0,
        "mfe_r": 1.0,
        "mae_r": 0.0,
        "score": 80.0,
    }
    for idx in range(6):
        repo.save_backtest(
            {
                **base,
                "pattern_id": f"p{idx}",
                "symbol": f"S{idx}USDT",
                "interval": "5m",
                "score": 80.0 + idx,
                "evaluated_at": (now + timedelta(seconds=idx)).isoformat(),
            }
        )
    for idx in range(4):
        repo.save_optimization_result(
            {
                "run_id": f"run-{idx}",
                "pattern_id": f"p{idx}",
                "symbol": f"S{idx}USDT",
                "interval": "5m",
                "objective": "expectancy",
                "status": "ok",
                "started_at": (now + timedelta(minutes=idx)).isoformat(),
                "finished_at": (now + timedelta(minutes=idx, seconds=30)).isoformat(),
                "trials": [
                    {
                        "trial_id": f"trial-{idx}",
                        "parameter_set": {"x": idx},
                        "objective_score": float(idx),
                        "evaluated_at": (
                            now + timedelta(minutes=idx, seconds=31)
                        ).isoformat(),
                    }
                ],
            }
        )

    result = repo.prune_history(
        retention_days=90,
        backtests_per_tuple=10,
        optimizer_runs_per_tuple=10,
        optimizer_trials_per_run=10,
        max_backtest_rows=3,
        max_optimizer_runs=2,
        max_optimizer_trials=2,
    )

    assert result["capped_backtests_deleted"] == 3
    assert result["capped_optimization_runs_deleted"] == 2
    assert result["capped_optimization_run_trials_deleted"] == 2
    assert result["capped_optimization_trials_deleted"] == 0
    assert result["vacuumed"] is True
    with sqlite3.connect(repo.path) as conn:
        backtests = conn.execute(
            "SELECT pattern_id FROM pattern_backtests ORDER BY evaluated_at"
        ).fetchall()
        runs = conn.execute(
            "SELECT run_id FROM optimization_runs ORDER BY finished_at"
        ).fetchall()
        trials = conn.execute(
            "SELECT trial_id FROM optimization_trials ORDER BY evaluated_at"
        ).fetchall()
    assert [row[0] for row in backtests] == ["p3", "p4", "p5"]
    assert [row[0] for row in runs] == ["run-2", "run-3"]
    assert [row[0] for row in trials] == ["trial-2", "trial-3"]


def test_latest_scorecards_uses_latest_per_pattern_tuple_not_best_ever(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p1",
                "source_id": "s1",
                "name": "EMA trend long",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": [],
            }
        ]
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    base = {
        "pattern_id": "p1",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "trade_count": 10,
        "win_rate": 0.5,
        "expectancy_r": 0.1,
        "avg_r": 0.1,
        "profit_factor": 1.2,
        "max_loss_r": -1.0,
        "mfe_r": 1.0,
        "mae_r": -0.5,
    }
    repo.save_backtest({**base, "score": 95.0, "evaluated_at": old})
    repo.save_backtest({**base, "score": 20.0, "evaluated_at": recent})

    scorecards = repo.latest_scorecards(symbols=["BTCUSDT"], limit=10)

    assert len(scorecards) == 1
    assert scorecards[0]["score"] == 20.0


def test_latest_scorecards_diversifies_repeated_pattern_families(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    patterns = [
        ("rsi-1", "rsi_mean_reversion", "long", ["rsi"]),
        ("rsi-2", "rsi_mean_reversion", "long", ["rsi"]),
        ("rsi-3", "rsi_mean_reversion", "long", ["rsi"]),
        ("ema-1", "ema_trend", "long", ["ema_fast", "ema_slow"]),
        ("volume-1", "volume_confirmation", "long", ["volume"]),
    ]
    repo.save_patterns(
        [
            {
                "pattern_id": pattern_id,
                "source_id": f"source-{pattern_id}",
                "name": pattern_id,
                "family": family,
                "direction": direction,
                "timeframe": "5m",
                "indicators": indicators,
                "expression": {},
                "risk_tags": [],
            }
            for pattern_id, family, direction, indicators in patterns
        ]
    )
    for pattern_id, score in [
        ("rsi-1", 99.0),
        ("rsi-2", 98.0),
        ("rsi-3", 97.0),
        ("ema-1", 80.0),
        ("volume-1", 70.0),
    ]:
        repo.save_backtest(
            {
                "pattern_id": pattern_id,
                "symbol": "BTCUSDT",
                "interval": "5m",
                "trade_count": 10,
                "win_rate": 0.5,
                "expectancy_r": 0.1,
                "avg_r": 0.1,
                "profit_factor": 1.2,
                "max_loss_r": -1.0,
                "mfe_r": 1.0,
                "mae_r": -0.5,
                "score": score,
            }
        )

    scorecards = repo.latest_scorecards(symbols=["BTCUSDT"], limit=3)

    families = [row["family"] for row in scorecards]
    assert families == ["rsi_mean_reversion", "ema_trend", "volume_confirmation"]


def test_extractor_detects_freqtrade_patterns_without_executing_code(tmp_path: Path) -> None:
    strategy = tmp_path / "SampleStrategy.py"
    strategy.write_text(
        '''
import os
from freqtrade.strategy import IStrategy

class SampleStrategy(IStrategy):
    timeframe = "5m"

    def populate_indicators(self, dataframe, metadata):
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=8)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=21)
        os.environ["HERMES_SHOULD_NOT_EXECUTE"] = "1"
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["rsi"] < 30) & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        dataframe.loc[
            (dataframe["ema_fast"] < dataframe["ema_slow"]),
            "enter_short",
        ] = 1
        return dataframe
''',
        encoding="utf-8",
    )

    result = FreqtradeStrategyExtractor().extract_file(strategy)

    assert result["status"] == "ok"
    assert result["strategy_name"] == "SampleStrategy"
    families = {row["family"] for row in result["patterns"]}
    directions = {row["direction"] for row in result["patterns"]}
    assert "rsi_mean_reversion" in families
    assert "ema_trend" in families
    assert {"long", "short"}.issubset(directions)
    assert "HERMES_SHOULD_NOT_EXECUTE" not in __import__("os").environ


def test_extractor_rejects_non_python_file(tmp_path: Path) -> None:
    path = tmp_path / "strategy.txt"
    path.write_text("not python", encoding="utf-8")

    result = FreqtradeStrategyExtractor().extract_file(path)

    assert result["status"] == "error"
    assert "python" in result["error_message"].lower()


def test_extractor_does_not_mark_short_for_can_short_false(tmp_path: Path) -> None:
    strategy = tmp_path / "LongOnlyStrategy.py"
    strategy.write_text(
        '''
class LongOnlyStrategy:
    timeframe = "5m"
    can_short = False

    def populate_entry_trend(self, dataframe, metadata):
        short_window = 5
        dataframe.loc[(dataframe["rsi"] < 30), "enter_long"] = 1
        return dataframe
''',
        encoding="utf-8",
    )

    result = FreqtradeStrategyExtractor().extract_file(strategy)

    assert result["status"] == "ok"
    assert {row["direction"] for row in result["patterns"]} == {"long"}


def test_extractor_does_not_depend_on_ast_unparse(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    strategy = tmp_path / "HugeStrategy.py"
    strategy.write_text(
        '''
class HugeStrategy:
    timeframe = "5m"

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[(dataframe["close"] > 0) & (dataframe["volume"] > 0), "enter_long"] = 1
        return dataframe
''',
        encoding="utf-8",
    )

    def boom(tree: ast.AST) -> str:
        raise RecursionError("public strategy expression is too deep")

    monkeypatch.setattr(ast, "unparse", boom)

    result = FreqtradeStrategyExtractor().extract_file(strategy)

    assert result["status"] == "ok"
    assert "volume_confirmation" in {row["family"] for row in result["patterns"]}


def test_freqtrade_json_ohlcv_importer_normalizes_rows(tmp_path: Path) -> None:
    path = tmp_path / "BTC_USDT-5m.json"
    path.write_text(
        json.dumps(
            [
                [1700000000000, 100.0, 110.0, 95.0, 105.0, 1234.0],
                [1700000300000, 105.0, 112.0, 101.0, 108.0, 999.0],
            ]
        ),
        encoding="utf-8",
    )

    rows = FreqtradeOHLCVImporter().read_file(path, symbol="BTCUSDT", interval="5m")

    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["interval"] == "5m"
    assert rows[0]["open_time"] == 1700000000000
    assert rows[0]["close"] == 105.0
    assert rows[0]["volume"] == 1234.0


def test_freqtrade_jsongz_ohlcv_importer_normalizes_rows(tmp_path: Path) -> None:
    path = tmp_path / "ETH_USDT-15m.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump([[1700000000000, 10, 11, 9, 10.5, 88]], handle)

    rows = FreqtradeOHLCVImporter().read_file(path, symbol="ETHUSDT", interval="15m")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETHUSDT"
    assert rows[0]["close"] == 10.5


def test_freqtrade_data_catalog_reads_configured_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "BTC_USDT-5m.json"
    path.write_text(
        json.dumps([[1700000000000, 100.0, 110.0, 95.0, 105.0, 1234.0]]),
        encoding="utf-8",
    )

    rows = FreqtradeDataCatalog(str(data_dir)).read(
        symbol="BTCUSDT",
        interval="5m",
        limit=10,
    )

    assert len(rows) == 1
    assert rows[0]["close"] == 105.0


def test_hermes_kline_reader_reads_existing_crypto_klines(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto_market_research.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_klines (
                symbol TEXT,
                market TEXT,
                interval TEXT,
                open_time INTEGER,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                quote_volume REAL,
                close_time INTEGER,
                raw_json TEXT,
                PRIMARY KEY(symbol, market, interval, open_time)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO crypto_klines VALUES (
                'BTCUSDT', 'spot', '5m', 1, 100, 110, 90, 105, 1000, 105000, 2, '{}'
            )
            """
        )

    rows = HermesKlineReader(db_path).read(symbol="BTCUSDT", interval="5m", limit=10)

    assert len(rows) == 1
    assert rows[0]["close"] == 105.0


def test_pattern_backtest_lab_scores_ema_trend_long() -> None:
    rows = [
        {
            "open_time": index,
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1000 + index,
        }
        for index in range(80)
    ]
    pattern = {
        "pattern_id": "p1",
        "family": "ema_trend",
        "direction": "long",
        "timeframe": "5m",
    }

    result = PatternBacktestLab().evaluate(
        pattern=pattern,
        symbol="BTCUSDT",
        interval="5m",
        rows=rows,
    )

    assert result["symbol"] == "BTCUSDT"
    assert result["pattern_id"] == "p1"
    assert result["trade_count"] > 0
    assert result["score"] > 0
    assert "expectancy_r" in result


def test_pattern_backtest_lab_requires_enough_rows() -> None:
    pattern = {
        "pattern_id": "p1",
        "family": "rsi_mean_reversion",
        "direction": "long",
        "timeframe": "5m",
    }

    result = PatternBacktestLab().evaluate(
        pattern=pattern,
        symbol="BTCUSDT",
        interval="5m",
        rows=[],
    )

    assert result["trade_count"] == 0
    assert result["score"] == 0
    assert "insufficient_rows" in result["warnings"]


def test_pattern_optimizer_selects_profitable_parameter_set() -> None:
    rows: list[dict[str, Any]] = []
    price = 100.0
    for index in range(120):
        phase = index % 8
        close = price * (1.012 if phase in {3, 4} else 0.998 if phase == 7 else 1.003)
        high = max(price, close) * (1.004 if phase in {3, 4} else 1.001)
        low = min(price, close) * 0.998
        rows.append(
            {
                "open_time": index,
                "open": price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000 + index,
            }
        )
        price = close
    pattern = {
        "pattern_id": "p-opt",
        "family": "ema_trend",
        "direction": "long",
        "timeframe": "5m",
    }

    result = PatternOptimizationLab(
        parameter_grid=[
            {"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4},
            {"stop_pct": 0.02, "target_pct": 0.04, "holding_bars": 4},
        ]
    ).optimize(pattern=pattern, symbol="BTCUSDT", interval="5m", rows=rows)

    assert result["status"] == "ok"
    assert result["trial_count"] == 2
    assert result["best"]["parameter_set"] == {
        "stop_pct": 0.008,
        "target_pct": 0.012,
        "holding_bars": 4,
    }
    assert result["best"]["objective_score"] > 0
    assert result["best"]["net_r"] > 0


def test_pattern_optimizer_emits_out_of_sample_evidence(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = []
    price = 100.0
    for index in range(360):
        phase = index % 8
        close = price * (1.012 if phase in {3, 4} else 0.998 if phase == 7 else 1.003)
        high = max(price, close) * (1.004 if phase in {3, 4} else 1.001)
        low = min(price, close) * 0.998
        rows.append(
            {
                "open_time": index,
                "open": price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000 + index,
            }
        )
        price = close
    pattern = {
        "pattern_id": "p-opt-oos",
        "family": "ema_trend",
        "direction": "long",
        "timeframe": "5m",
    }

    result = PatternOptimizationLab(
        parameter_grid=[
            {"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4},
            {"stop_pct": 0.02, "target_pct": 0.04, "holding_bars": 4},
        ]
    ).optimize(pattern=pattern, symbol="BTCUSDT", interval="5m", rows=rows)

    best = result["best"]
    assert result["status"] == "ok"
    assert best["in_sample"]["sample_start"] == "0"
    assert int(best["in_sample"]["sample_end"]) < int(best["out_of_sample"]["sample_start"])
    assert best["out_of_sample"]["trade_count"] >= 8
    assert best["out_of_sample"]["expectancy_r"] > 0
    assert best["out_of_sample"]["profit_factor"] >= 1.05
    assert best["walk_forward"]["window_count"] >= 3
    assert best["walk_forward"]["passed_window_count"] >= 2
    assert best["walk_forward"]["pass_rate_pct"] >= 70.0

    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p-opt-oos",
                "source_id": "source-opt-oos",
                "name": "Optimized OOS trend long",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    repo.save_optimization_result(result)

    context = repo.context_pack(symbols=["BTCUSDT"], limit=5)
    assert context["optimization"]["set_count"] == 1
    assert context["optimized_strategy_sets"][0]["status"] == "active"
    assert context["optimized_strategy_sets"][0]["out_of_sample_trade_count"] >= 8
    assert context["optimized_strategy_sets"][0]["walk_forward_quality"][
        "window_count"
    ] >= 3


def test_optimized_set_requires_rolling_walk_forward_windows(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p-oos-only",
                "source_id": "source-oos-only",
                "name": "OOS only trend",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["ema"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    repo.save_optimization_result(
        {
            "run_id": "run-oos-only",
            "pattern_id": "p-oos-only",
            "symbol": "BTCUSDT",
            "interval": "15m",
            "objective": "risk_adjusted_net_r_v1",
            "status": "ok",
            "best": {
                "trial_id": "trial-oos-only",
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
            },
            "trials": [],
        }
    )

    context = repo.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["optimized_strategy_sets"] == []
    assert context["optimization"]["rejected_set_count"] == 1
    rejected = context["rejected_optimized_strategy_sets"][0]
    assert rejected["walk_forward_quality"]["passed"] is False
    assert "walk_forward_windows_missing" in rejected["walk_forward_quality"]["reasons"]


def test_repository_rejects_optimized_strategy_sets_without_oos(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p-opt",
                "source_id": "source-opt",
                "name": "Optimized trend long",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    repo.save_optimization_result(
        {
            "run_id": "run-1",
            "pattern_id": "p-opt",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "search_space": [{"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4}],
            "objective": "risk_adjusted_net_r_v1",
            "trial_count": 1,
            "status": "ok",
            "best": {
                "trial_id": "trial-1",
                "parameter_set": {"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4},
                "trade_count": 18,
                "win_rate": 0.61,
                "expectancy_r": 0.24,
                "avg_r": 0.24,
                "profit_factor": 1.8,
                "max_loss_r": -1.0,
                "mfe_r": 1.2,
                "mae_r": -0.4,
                "net_r": 4.32,
                "objective_score": 68.5,
                "sample_start": "1",
                "sample_end": "120",
                "warnings": [],
            },
            "trials": [
                {
                    "trial_id": "trial-1",
                    "parameter_set": {"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4},
                    "trade_count": 18,
                    "win_rate": 0.61,
                    "expectancy_r": 0.24,
                    "avg_r": 0.24,
                    "profit_factor": 1.8,
                    "max_loss_r": -1.0,
                    "mfe_r": 1.2,
                    "mae_r": -0.4,
                    "net_r": 4.32,
                    "objective_score": 68.5,
                    "sample_start": "1",
                    "sample_end": "120",
                    "warnings": [],
                }
            ],
        }
    )

    context = repo.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["optimized_strategy_sets"] == []
    assert context["optimization"]["set_count"] == 0
    assert context["optimization"]["rejected_set_count"] == 1
    rejected = context["rejected_optimized_strategy_sets"][0]
    assert rejected["symbol"] == "BTCUSDT"
    assert rejected["parameter_set"]["target_pct"] == 0.012
    assert rejected["objective"] == "risk_adjusted_net_r_v1"
    assert rejected["walk_forward_quality"]["passed"] is False
    assert "out_of_sample_missing" in rejected["walk_forward_quality"]["reasons"]
    assert context["optimization_evidence"] == []


def test_repository_status_summarizes_optimized_set_rejection_reasons(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p-status",
                "source_id": "source-status",
                "name": "Status trend long",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    repo.save_optimization_result(
        {
            "run_id": "run-status",
            "pattern_id": "p-status",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "objective": "risk_adjusted_net_r_v1",
            "status": "ok",
            "finished_at": "2026-06-14T12:00:00+00:00",
            "best": {
                "trial_id": "trial-status",
                "parameter_set": {
                    "stop_pct": 0.008,
                    "target_pct": 0.016,
                    "holding_bars": 6,
                },
                "trade_count": 18,
                "win_rate": 0.61,
                "expectancy_r": 0.24,
                "profit_factor": 1.8,
                "max_loss_r": -1.0,
                "objective_score": 68.5,
                "sample_start": "1",
                "sample_end": "120",
            },
            "trials": [],
        }
    )

    status = repo.status()

    assert status["optimized_set_count"] == 0
    assert status["rejected_optimized_set_count"] == 1
    assert status["total_optimized_set_count"] == 1
    assert status["latest_optimized_set_at"] == "2026-06-14T12:00:00+00:00"
    assert status["top_rejection_reasons"][0] == {
        "reason": "out_of_sample_missing",
        "count": 1,
    }
    assert status["validation_hint"]["status"] == "needs_revalidation"
    assert "out_of_sample_missing" in status["validation_hint"]["reasons"]


def test_repository_reclassifies_legacy_unverified_active_sets(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "patterns.db"
    repo = CryptoPatternLabRepository(db_path)
    repo.save_patterns(
        [
            {
                "pattern_id": "p-legacy",
                "source_id": "source-legacy",
                "name": "Legacy unverified trend",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimized_strategy_sets (
                set_id, run_id, trial_id, pattern_id, symbol, interval,
                family, direction, parameter_set_json, objective,
                objective_score, trade_count, win_rate, expectancy_r,
                profit_factor, max_loss_r, in_sample_expectancy_r,
                out_of_sample_trade_count, out_of_sample_expectancy_r,
                out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                overfit_risk, walk_forward_quality_json, status, promoted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-set",
                "run-legacy",
                "trial-legacy",
                "p-legacy",
                "BTCUSDT",
                "5m",
                "ema_trend",
                "long",
                '{"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4}',
                "risk_adjusted_net_r_v1",
                68.5,
                18,
                0.61,
                0.24,
                1.8,
                -1.0,
                0.24,
                0,
                0.0,
                0.0,
                0.0,
                "unknown",
                (
                    '{"passed": false, "status": "legacy_unverified", '
                    '"reasons": ["out_of_sample_missing"], '
                    '"overfit_risk": "unknown"}'
                ),
                "active",
                "2026-06-14T09:54:01+00:00",
            ),
        )

    repaired = CryptoPatternLabRepository(db_path)
    context = repaired.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["optimized_strategy_sets"] == []
    assert context["optimization"]["set_count"] == 0
    assert context["optimization"]["rejected_set_count"] == 1
    rejected = context["rejected_optimized_strategy_sets"][0]
    assert rejected["status"] == "rejected"
    assert rejected["overfit_risk"] == "unknown"
    assert rejected["out_of_sample_trade_count"] == 0


def test_repository_reclassifies_active_sets_with_low_walk_forward_pass_rate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "patterns.db"
    repo = CryptoPatternLabRepository(db_path)
    repo.save_patterns(
        [
            {
                "pattern_id": "p-low-wfa",
                "source_id": "source-low-wfa",
                "name": "Low WFA trend",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimized_strategy_sets (
                set_id, run_id, trial_id, pattern_id, symbol, interval,
                family, direction, parameter_set_json, objective,
                objective_score, trade_count, win_rate, expectancy_r,
                profit_factor, max_loss_r, in_sample_expectancy_r,
                out_of_sample_trade_count, out_of_sample_expectancy_r,
                out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                overfit_risk, walk_forward_quality_json, status, promoted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "low-wfa-set",
                "run-low-wfa",
                "trial-low-wfa",
                "p-low-wfa",
                "BTCUSDT",
                "5m",
                "ema_trend",
                "long",
                '{"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4}',
                "risk_adjusted_net_r_v1",
                68.5,
                18,
                0.61,
                0.24,
                1.8,
                -1.0,
                0.24,
                18,
                0.15,
                1.35,
                -2.0,
                "low",
                (
                    '{"passed": true, "window_count": 4, '
                    '"passed_window_count": 2, "pass_rate_pct": 50.0}'
                ),
                "active",
                "2026-06-14T09:54:01+00:00",
            ),
        )

    repaired = CryptoPatternLabRepository(db_path)
    context = repaired.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["optimized_strategy_sets"] == []
    assert context["optimization"]["set_count"] == 0
    assert context["optimization"]["rejected_set_count"] == 1
    rejected = context["rejected_optimized_strategy_sets"][0]
    assert rejected["status"] == "rejected"
    assert rejected["walk_forward_quality"]["pass_rate_pct"] == 50.0


def test_repository_rejects_inconsistent_walk_forward_pass_rate(
    tmp_path: Path,
) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p-inconsistent-wfa",
                "source_id": "source-inconsistent-wfa",
                "name": "Inconsistent WFA trend",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["ema"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    repo.save_optimization_result(
        {
            "run_id": "run-inconsistent-wfa",
            "pattern_id": "p-inconsistent-wfa",
            "symbol": "BTCUSDT",
            "interval": "15m",
            "objective": "risk_adjusted_net_r_v1",
            "status": "ok",
            "best": {
                "trial_id": "trial-inconsistent-wfa",
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
                    "passed_window_count": 0,
                    "pass_rate_pct": 100.0,
                    "windows": [
                        {"index": 1, "passed": False},
                        {"index": 2, "passed": False},
                        {"index": 3, "passed": False},
                        {"index": 4, "passed": False},
                    ],
                },
            },
            "trials": [],
        }
    )

    context = repo.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["optimized_strategy_sets"] == []
    assert context["optimization"]["rejected_set_count"] == 1
    rejected = context["rejected_optimized_strategy_sets"][0]
    assert rejected["walk_forward_quality"]["passed"] is False
    assert "walk_forward_pass_rate_inconsistent" in rejected[
        "walk_forward_quality"
    ]["reasons"]


def test_repository_reclassifies_legacy_active_sets_with_inconsistent_wfa_rate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "patterns.db"
    repo = CryptoPatternLabRepository(db_path)
    repo.save_patterns(
        [
            {
                "pattern_id": "p-legacy-inconsistent-wfa",
                "source_id": "source-legacy-inconsistent-wfa",
                "name": "Legacy inconsistent WFA trend",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO optimized_strategy_sets (
                set_id, run_id, trial_id, pattern_id, symbol, interval,
                family, direction, parameter_set_json, objective,
                objective_score, trade_count, win_rate, expectancy_r,
                profit_factor, max_loss_r, in_sample_expectancy_r,
                out_of_sample_trade_count, out_of_sample_expectancy_r,
                out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                overfit_risk, walk_forward_quality_json, status, promoted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-inconsistent-wfa-set",
                "run-legacy-inconsistent-wfa",
                "trial-legacy-inconsistent-wfa",
                "p-legacy-inconsistent-wfa",
                "BTCUSDT",
                "5m",
                "ema_trend",
                "long",
                '{"stop_pct": 0.008, "target_pct": 0.012, "holding_bars": 4}',
                "risk_adjusted_net_r_v1",
                68.5,
                18,
                0.61,
                0.24,
                1.8,
                -1.0,
                0.24,
                18,
                0.15,
                1.35,
                -2.0,
                "low",
                (
                    '{"passed": true, "window_count": 4, '
                    '"passed_window_count": 0, "pass_rate_pct": 100.0}'
                ),
                "active",
                "2026-06-14T09:54:01+00:00",
            ),
        )

    repaired = CryptoPatternLabRepository(db_path)
    context = repaired.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["optimized_strategy_sets"] == []
    assert context["optimization"]["rejected_set_count"] == 1
    rejected = context["rejected_optimized_strategy_sets"][0]
    assert rejected["walk_forward_quality"]["passed_window_count"] == 0
    assert rejected["walk_forward_quality"]["pass_rate_pct"] == 100.0


def test_optimized_set_requires_out_of_sample_expectancy(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
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
            }
        ]
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
                "parameter_set": {"stop_pct": 0.01, "target_pct": 0.02, "holding_bars": 8},
                "trade_count": 40,
                "win_rate": 0.62,
                "expectancy_r": 0.35,
                "profit_factor": 1.6,
                "max_loss_r": -1.0,
                "objective_score": 82.0,
                "out_of_sample": {
                    "trade_count": 12,
                    "expectancy_r": -0.05,
                    "profit_factor": 0.92,
                    "max_drawdown_r": -3.2,
                },
            },
            "trials": [],
        }
    )

    context = repo.context_pack(symbols=["ALTUSDT"], limit=5)

    assert context["optimized_strategy_sets"] == []
    assert context["optimization"]["rejected_set_count"] == 1
    rejected = context["rejected_optimized_strategy_sets"][0]
    assert rejected["set_id"]
    assert rejected["walk_forward_quality"]["passed"] is False
    assert "out_of_sample_expectancy_negative" in rejected["walk_forward_quality"]["reasons"]


def test_optimized_set_with_good_out_of_sample_is_active(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p-wf",
                "source_id": "source-wf",
                "name": "Walk forward trend",
                "family": "ema_trend",
                "direction": "short",
                "timeframe": "15m",
                "indicators": ["ema"],
                "expression": {},
                "risk_tags": ["trend"],
            }
        ]
    )
    repo.save_optimization_result(
        {
            "run_id": "run-wf",
            "pattern_id": "p-wf",
            "symbol": "BTCUSDT",
            "interval": "15m",
            "objective": "risk_adjusted_net_r_v1",
            "status": "ok",
            "best": {
                "trial_id": "trial-wf",
                "parameter_set": {"stop_pct": 0.008, "target_pct": 0.016, "holding_bars": 10},
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
                    "pass_rate_pct": 75.0,
                    "windows": [
                        {"index": 1, "passed": True, "expectancy_r": 0.08},
                        {"index": 2, "passed": True, "expectancy_r": 0.11},
                        {"index": 3, "passed": False, "expectancy_r": -0.02},
                        {"index": 4, "passed": True, "expectancy_r": 0.09},
                    ],
                },
            },
            "trials": [],
        }
    )

    context = repo.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["optimization"]["set_count"] == 1
    assert context["optimization"]["rejected_set_count"] == 0
    optimized = context["optimized_strategy_sets"][0]
    assert optimized["status"] == "active"
    assert optimized["walk_forward_quality"]["passed"] is True
    assert optimized["out_of_sample_expectancy_r"] == 0.12
    assert optimized["test_start"] == "301"


def test_pattern_lab_service_imports_and_backtests_strategy(tmp_path: Path) -> None:
    strategy = tmp_path / "SampleStrategy.py"
    strategy.write_text(
        '''
class SampleStrategy:
    timeframe = "5m"
    def populate_indicators(self, dataframe, metadata):
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=8)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=21)
        return dataframe
    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[(dataframe["ema_fast"] > dataframe["ema_slow"]), "enter_long"] = 1
        return dataframe
''',
        encoding="utf-8",
    )

    class Reader:
        def read(
            self,
            *,
            symbol: str,
            interval: str,
            market: str = "spot",
            limit: int = 500,
        ) -> list[dict[str, Any]]:
            return [
                {
                    "open_time": index,
                    "open": 100 + index,
                    "high": 102 + index,
                    "low": 99 + index,
                    "close": 101 + index,
                    "volume": 1000 + index,
                }
                for index in range(100)
            ]

    service = CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=str(tmp_path / "patterns.db"),
            strategy_paths=str(strategy),
            max_symbols=1,
            intervals="5m",
        ),
        kline_reader=Reader(),
    )

    result = service.run_once(symbols=["BTCUSDT"])
    context = service.context_pack(symbols=["BTCUSDT"], limit=5)

    assert result["status"] == "ok"
    assert result["imported_source_count"] == 1
    assert result["backtest_count"] > 0
    assert context["scorecards"][0]["symbol"] == "BTCUSDT"


def test_pattern_lab_reuses_kline_rows_per_symbol_interval(tmp_path: Path) -> None:
    class CountingReader:
        def __init__(self) -> None:
            self.calls = 0

        def read(
            self,
            *,
            symbol: str,
            interval: str,
            market: str = "spot",
            limit: int = 500,
        ) -> list[dict[str, Any]]:
            self.calls += 1
            return [
                {
                    "open_time": index,
                    "open": 100 + index,
                    "high": 102 + index,
                    "low": 99 + index,
                    "close": 101 + index,
                    "volume": 1000 + index,
                }
                for index in range(100)
            ]

    reader = CountingReader()
    service = CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=str(tmp_path / "patterns.db"),
            max_symbols=1,
            intervals="5m",
            optimizer_enabled=False,
        ),
        kline_reader=reader,
    )
    service.repository.save_patterns(
        [
            {
                "pattern_id": "p1",
                "source_id": "source",
                "name": "Trend",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": [],
                "expression": {},
                "risk_tags": [],
            },
            {
                "pattern_id": "p2",
                "source_id": "source",
                "name": "Squeeze",
                "family": "bollinger_squeeze",
                "direction": "long",
                "timeframe": "5m",
                "indicators": [],
                "expression": {},
                "risk_tags": [],
            },
        ]
    )

    result = service.run_backtests(symbols=["BTCUSDT"])

    assert result["backtest_count"] == 2
    assert reader.calls == 1
