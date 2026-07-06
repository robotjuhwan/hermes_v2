from __future__ import annotations

import ast
import gzip
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.evidence_policy import evidence_from_signal


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def stable_id(*parts: Any) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def normalize_symbol(symbol: Any) -> str:
    text = str(symbol or "").upper().strip()
    return text.replace("/", "").replace(":", "").replace("-", "").replace("_", "")


def safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


PATTERN_ENTRY_MIN_TRADE_COUNT = 8
PATTERN_ENTRY_MIN_WIN_RATE = 0.50
PATTERN_ENTRY_MIN_EXPECTANCY_R = 0.0
PATTERN_ENTRY_MIN_PROFIT_FACTOR = 1.05
OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_TRADES = 8
OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_EXPECTANCY_R = 0.0
OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_PROFIT_FACTOR = 1.05
OPTIMIZED_SET_MAX_OUT_OF_SAMPLE_DRAWDOWN_R = -6.0
OPTIMIZED_SET_MIN_WALK_FORWARD_WINDOWS = 3
OPTIMIZED_SET_MIN_WALK_FORWARD_PASS_RATE_PCT = 70.0


@dataclass(slots=True)
class CryptoPatternLabConfig:
    db_path: str = ".runtime/crypto_pattern_lab.db"
    enabled: bool = True
    strategy_paths: str = ""
    freqtrade_data_paths: str = ""
    max_symbols: int = 30
    intervals: str = "5m,15m,1h"
    lookback_bars: int = 500
    context_limit: int = 12
    retention_days: int = 90
    backtests_per_tuple_retention: int = 8
    optimizer_runs_per_tuple_retention: int = 8
    optimizer_trials_per_run_retention: int = 12
    max_backtest_rows: int = 80_000
    max_optimizer_runs: int = 2_500
    max_optimizer_trials: int = 24_000
    optimizer_enabled: bool = True
    optimizer_max_scorecards: int = 60
    optimizer_max_trials_per_scorecard: int = 24


class CryptoPatternLabRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS freqtrade_strategy_sources (
                    source_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    strategy_name TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL,
                    license TEXT NOT NULL DEFAULT '',
                    license_policy TEXT NOT NULL DEFAULT '',
                    imported_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS strategy_patterns (
                    pattern_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    family TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    indicators_json TEXT NOT NULL DEFAULT '[]',
                    expression_json TEXT NOT NULL DEFAULT '{}',
                    risk_tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_patterns_family
                    ON strategy_patterns(family, direction, timeframe);
                CREATE TABLE IF NOT EXISTS pattern_backtests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    sample_start TEXT NOT NULL DEFAULT '',
                    sample_end TEXT NOT NULL DEFAULT '',
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    expectancy_r REAL NOT NULL DEFAULT 0,
                    avg_r REAL NOT NULL DEFAULT 0,
                    profit_factor REAL NOT NULL DEFAULT 0,
                    max_loss_r REAL NOT NULL DEFAULT 0,
                    mfe_r REAL NOT NULL DEFAULT 0,
                    mae_r REAL NOT NULL DEFAULT 0,
                    regime TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    evaluated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pattern_backtests_symbol_time
                    ON pattern_backtests(symbol, interval, evaluated_at DESC);
                CREATE TABLE IF NOT EXISTS freqtrade_ohlcv_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS optimization_runs (
                    run_id TEXT PRIMARY KEY,
                    pattern_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    search_space_json TEXT NOT NULL DEFAULT '[]',
                    trial_count INTEGER NOT NULL DEFAULT 0,
                    best_trial_id TEXT NOT NULL DEFAULT '',
                    best_score REAL NOT NULL DEFAULT 0,
                    sample_start TEXT NOT NULL DEFAULT '',
                    sample_end TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS optimization_trials (
                    trial_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    pattern_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    parameter_set_json TEXT NOT NULL DEFAULT '{}',
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    expectancy_r REAL NOT NULL DEFAULT 0,
                    avg_r REAL NOT NULL DEFAULT 0,
                    profit_factor REAL NOT NULL DEFAULT 0,
                    max_loss_r REAL NOT NULL DEFAULT 0,
                    mfe_r REAL NOT NULL DEFAULT 0,
                    mae_r REAL NOT NULL DEFAULT 0,
                    net_r REAL NOT NULL DEFAULT 0,
                    objective_score REAL NOT NULL DEFAULT 0,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    evaluated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_optimization_trials_run
                    ON optimization_trials(run_id, objective_score DESC);
                CREATE TABLE IF NOT EXISTS optimized_strategy_sets (
                    set_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    trial_id TEXT NOT NULL,
                    pattern_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    family TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT '',
                    parameter_set_json TEXT NOT NULL DEFAULT '{}',
                    objective TEXT NOT NULL DEFAULT '',
                    objective_score REAL NOT NULL DEFAULT 0,
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    expectancy_r REAL NOT NULL DEFAULT 0,
                    profit_factor REAL NOT NULL DEFAULT 0,
                    max_loss_r REAL NOT NULL DEFAULT 0,
                    train_start TEXT NOT NULL DEFAULT '',
                    train_end TEXT NOT NULL DEFAULT '',
                    test_start TEXT NOT NULL DEFAULT '',
                    test_end TEXT NOT NULL DEFAULT '',
                    in_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                    out_of_sample_trade_count INTEGER NOT NULL DEFAULT 0,
                    out_of_sample_expectancy_r REAL NOT NULL DEFAULT 0,
                    out_of_sample_profit_factor REAL NOT NULL DEFAULT 0,
                    out_of_sample_max_drawdown_r REAL NOT NULL DEFAULT 0,
                    overfit_risk TEXT NOT NULL DEFAULT '',
                    walk_forward_quality_json TEXT NOT NULL DEFAULT '{}',
                    sample_start TEXT NOT NULL DEFAULT '',
                    sample_end TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    promoted_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_optimized_strategy_sets_symbol
                    ON optimized_strategy_sets(symbol, interval, objective_score DESC);
                """
            )
            self._ensure_columns(conn)
            self._reclassify_unverified_optimized_sets(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(freqtrade_strategy_sources)").fetchall()
        }
        for column, definition in {
            "license": "TEXT NOT NULL DEFAULT ''",
            "license_policy": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE freqtrade_strategy_sources ADD COLUMN {column} {definition}"
                )
        optimized_existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(optimized_strategy_sets)").fetchall()
        }
        for column, definition in {
            "train_start": "TEXT NOT NULL DEFAULT ''",
            "train_end": "TEXT NOT NULL DEFAULT ''",
            "test_start": "TEXT NOT NULL DEFAULT ''",
            "test_end": "TEXT NOT NULL DEFAULT ''",
            "in_sample_expectancy_r": "REAL NOT NULL DEFAULT 0",
            "out_of_sample_trade_count": "INTEGER NOT NULL DEFAULT 0",
            "out_of_sample_expectancy_r": "REAL NOT NULL DEFAULT 0",
            "out_of_sample_profit_factor": "REAL NOT NULL DEFAULT 0",
            "out_of_sample_max_drawdown_r": "REAL NOT NULL DEFAULT 0",
            "overfit_risk": "TEXT NOT NULL DEFAULT ''",
            "walk_forward_quality_json": "TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if column not in optimized_existing:
                conn.execute(
                    f"ALTER TABLE optimized_strategy_sets ADD COLUMN {column} {definition}"
                )

    @staticmethod
    def _reclassify_unverified_optimized_sets(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE optimized_strategy_sets
            SET status = 'rejected'
            WHERE status = 'active'
              AND (
                out_of_sample_trade_count <= 0
                OR lower(coalesce(overfit_risk, '')) IN ('', 'unknown', 'missing', 'n/a', 'none')
                OR (
                    json_valid(walk_forward_quality_json)
                    AND json_extract(walk_forward_quality_json, '$.status') = 'legacy_unverified'
                )
                OR (
                    json_valid(walk_forward_quality_json)
                    AND json_extract(walk_forward_quality_json, '$.passed') = 0
                )
                OR (
                    json_valid(walk_forward_quality_json)
                    AND coalesce(
                        json_extract(walk_forward_quality_json, '$.window_count'),
                        0
                    ) <= 0
                )
                OR (
                    json_valid(walk_forward_quality_json)
                    AND coalesce(
                        json_extract(walk_forward_quality_json, '$.window_count'),
                        0
                    ) < 3
                )
                OR (
                    json_valid(walk_forward_quality_json)
                    AND coalesce(
                        json_extract(walk_forward_quality_json, '$.pass_rate_pct'),
                        (
                            coalesce(
                                json_extract(
                                    walk_forward_quality_json,
                                    '$.passed_window_count'
                                ),
                                0
                            ) * 100.0
                            / max(
                                coalesce(
                                    json_extract(
                                        walk_forward_quality_json,
                                        '$.window_count'
                                    ),
                                    0
                                ),
                                1
                            )
                        )
                    ) < 70.0
                )
                OR (
                    json_valid(walk_forward_quality_json)
                    AND abs(
                        coalesce(
                            json_extract(
                                walk_forward_quality_json,
                                '$.pass_rate_pct'
                            ),
                            (
                                coalesce(
                                    json_extract(
                                        walk_forward_quality_json,
                                        '$.passed_window_count'
                                    ),
                                    0
                                ) * 100.0
                                / max(
                                    coalesce(
                                        json_extract(
                                            walk_forward_quality_json,
                                            '$.window_count'
                                        ),
                                        0
                                    ),
                                    1
                                )
                            )
                        )
                        - (
                            coalesce(
                                json_extract(
                                    walk_forward_quality_json,
                                    '$.passed_window_count'
                                ),
                                0
                            ) * 100.0
                            / max(
                                coalesce(
                                    json_extract(
                                        walk_forward_quality_json,
                                        '$.window_count'
                                    ),
                                    0
                                ),
                                1
                            )
                        )
                    ) > 0.01
                )
              )
            """
        )

    def save_strategy_source(self, payload: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO freqtrade_strategy_sources (
                    source_id, path, strategy_name, source_hash, license,
                    license_policy, imported_at, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    path=excluded.path,
                    strategy_name=excluded.strategy_name,
                    source_hash=excluded.source_hash,
                    license=excluded.license,
                    license_policy=excluded.license_policy,
                    imported_at=excluded.imported_at,
                    status=excluded.status,
                    error_message=excluded.error_message
                """,
                (
                    str(payload["source_id"]),
                    str(payload.get("path") or ""),
                    str(payload.get("strategy_name") or ""),
                    str(payload.get("source_hash") or ""),
                    str(payload.get("license") or ""),
                    str(payload.get("license_policy") or ""),
                    str(payload.get("imported_at") or now),
                    str(payload.get("status") or "ok"),
                    str(payload.get("error_message") or ""),
                ),
            )

    def save_patterns(self, patterns: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            for pattern in patterns:
                conn.execute(
                    """
                    INSERT INTO strategy_patterns (
                        pattern_id, source_id, name, family, direction, timeframe,
                        indicators_json, expression_json, risk_tags_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pattern_id) DO UPDATE SET
                        name=excluded.name,
                        family=excluded.family,
                        direction=excluded.direction,
                        timeframe=excluded.timeframe,
                        indicators_json=excluded.indicators_json,
                        expression_json=excluded.expression_json,
                        risk_tags_json=excluded.risk_tags_json
                    """,
                    (
                        str(pattern["pattern_id"]),
                        str(pattern.get("source_id") or ""),
                        str(pattern.get("name") or ""),
                        str(pattern.get("family") or ""),
                        str(pattern.get("direction") or "long"),
                        str(pattern.get("timeframe") or "15m"),
                        json_dumps(pattern.get("indicators") or []),
                        json_dumps(pattern.get("expression") or {}),
                        json_dumps(pattern.get("risk_tags") or []),
                        str(pattern.get("created_at") or now),
                    ),
                )

    def list_sources(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM freqtrade_strategy_sources
                ORDER BY imported_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_source(row) for row in rows]

    def list_patterns(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM strategy_patterns
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_pattern(row) for row in rows]

    def save_backtest(self, payload: dict[str, Any]) -> None:
        self.save_backtests([payload])

    def save_backtests(self, payloads: list[dict[str, Any]]) -> None:
        if not payloads:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO pattern_backtests (
                    pattern_id, symbol, interval, sample_start, sample_end, trade_count,
                    win_rate, expectancy_r, avg_r, profit_factor, max_loss_r,
                    mfe_r, mae_r, regime, score, warnings_json, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(payload.get("pattern_id") or ""),
                        normalize_symbol(payload.get("symbol")),
                        str(payload.get("interval") or ""),
                        str(payload.get("sample_start") or ""),
                        str(payload.get("sample_end") or ""),
                        int(payload.get("trade_count") or 0),
                        safe_float(payload.get("win_rate")),
                        safe_float(payload.get("expectancy_r")),
                        safe_float(payload.get("avg_r")),
                        safe_float(payload.get("profit_factor")),
                        safe_float(payload.get("max_loss_r")),
                        safe_float(payload.get("mfe_r")),
                        safe_float(payload.get("mae_r")),
                        str(payload.get("regime") or ""),
                        safe_float(payload.get("score")),
                        json_dumps(payload.get("warnings") or []),
                        str(payload.get("evaluated_at") or utc_now_iso()),
                    )
                    for payload in payloads
                ],
            )

    def save_optimization_result(self, payload: dict[str, Any]) -> None:
        run_id = str(payload.get("run_id") or stable_id(payload, utc_now_iso()))
        pattern_id = str(payload.get("pattern_id") or "")
        symbol = normalize_symbol(payload.get("symbol"))
        interval = str(payload.get("interval") or "")
        objective = str(payload.get("objective") or "risk_adjusted_net_r_v1")
        now = utc_now_iso()
        trials = [
            trial for trial in list(payload.get("trials") or []) if isinstance(trial, dict)
        ]
        best = payload.get("best") if isinstance(payload.get("best"), dict) else {}
        best_trial_id = str(best.get("trial_id") or "")
        with self._connect() as conn:
            meta = self._pattern_meta(conn, pattern_id)
            conn.execute(
                """
                INSERT INTO optimization_runs (
                    run_id, pattern_id, symbol, interval, objective, search_space_json,
                    trial_count, best_trial_id, best_score, sample_start, sample_end,
                    status, error_message, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    trial_count=excluded.trial_count,
                    best_trial_id=excluded.best_trial_id,
                    best_score=excluded.best_score,
                    sample_start=excluded.sample_start,
                    sample_end=excluded.sample_end,
                    status=excluded.status,
                    error_message=excluded.error_message,
                    finished_at=excluded.finished_at
                """,
                (
                    run_id,
                    pattern_id,
                    symbol,
                    interval,
                    objective,
                    json_dumps(payload.get("search_space") or []),
                    int(payload.get("trial_count") or len(trials)),
                    best_trial_id,
                    safe_float(best.get("objective_score")),
                    str(best.get("sample_start") or payload.get("sample_start") or ""),
                    str(best.get("sample_end") or payload.get("sample_end") or ""),
                    str(payload.get("status") or "ok"),
                    str(payload.get("error_message") or ""),
                    str(payload.get("started_at") or now),
                    str(payload.get("finished_at") or now),
                ),
            )
            for trial in trials:
                parameter_set = trial.get("parameter_set") or {}
                trial_id = str(
                    trial.get("trial_id")
                    or stable_id(run_id, json_dumps(parameter_set), trial.get("objective_score"))
                )
                conn.execute(
                    """
                    INSERT INTO optimization_trials (
                        trial_id, run_id, pattern_id, symbol, interval, parameter_set_json,
                        trade_count, win_rate, expectancy_r, avg_r, profit_factor,
                        max_loss_r, mfe_r, mae_r, net_r, objective_score,
                        warnings_json, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trial_id) DO UPDATE SET
                        trade_count=excluded.trade_count,
                        win_rate=excluded.win_rate,
                        expectancy_r=excluded.expectancy_r,
                        avg_r=excluded.avg_r,
                        profit_factor=excluded.profit_factor,
                        max_loss_r=excluded.max_loss_r,
                        mfe_r=excluded.mfe_r,
                        mae_r=excluded.mae_r,
                        net_r=excluded.net_r,
                        objective_score=excluded.objective_score,
                        warnings_json=excluded.warnings_json,
                        evaluated_at=excluded.evaluated_at
                    """,
                    (
                        trial_id,
                        run_id,
                        pattern_id,
                        symbol,
                        interval,
                        json_dumps(parameter_set),
                        int(trial.get("trade_count") or 0),
                        safe_float(trial.get("win_rate")),
                        safe_float(trial.get("expectancy_r")),
                        safe_float(trial.get("avg_r")),
                        safe_float(trial.get("profit_factor")),
                        safe_float(trial.get("max_loss_r")),
                        safe_float(trial.get("mfe_r")),
                        safe_float(trial.get("mae_r")),
                        safe_float(trial.get("net_r")),
                        safe_float(trial.get("objective_score")),
                        json_dumps(trial.get("warnings") or []),
                        str(trial.get("evaluated_at") or now),
                    ),
                )
            if best:
                parameter_set = best.get("parameter_set") or {}
                set_id = str(
                    best.get("set_id")
                    or stable_id(pattern_id, symbol, interval, json_dumps(parameter_set))
                )
                walk_forward_quality = self._qualify_optimized_set(best)
                out_of_sample = (
                    best.get("out_of_sample")
                    if isinstance(best.get("out_of_sample"), dict)
                    else {}
                )
                in_sample = (
                    best.get("in_sample")
                    if isinstance(best.get("in_sample"), dict)
                    else {}
                )
                set_status = (
                    "active"
                    if bool(walk_forward_quality.get("passed"))
                    else "rejected"
                )
                conn.execute(
                    """
                    INSERT INTO optimized_strategy_sets (
                        set_id, run_id, trial_id, pattern_id, symbol, interval,
                        family, direction, parameter_set_json, objective,
                        objective_score, trade_count, win_rate, expectancy_r,
                        profit_factor, max_loss_r, train_start, train_end,
                        test_start, test_end, in_sample_expectancy_r,
                        out_of_sample_trade_count, out_of_sample_expectancy_r,
                        out_of_sample_profit_factor, out_of_sample_max_drawdown_r,
                        overfit_risk, walk_forward_quality_json, sample_start,
                        sample_end, status, promoted_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(set_id) DO UPDATE SET
                        run_id=excluded.run_id,
                        trial_id=excluded.trial_id,
                        objective=excluded.objective,
                        objective_score=excluded.objective_score,
                        trade_count=excluded.trade_count,
                        win_rate=excluded.win_rate,
                        expectancy_r=excluded.expectancy_r,
                        profit_factor=excluded.profit_factor,
                        max_loss_r=excluded.max_loss_r,
                        train_start=excluded.train_start,
                        train_end=excluded.train_end,
                        test_start=excluded.test_start,
                        test_end=excluded.test_end,
                        in_sample_expectancy_r=excluded.in_sample_expectancy_r,
                        out_of_sample_trade_count=excluded.out_of_sample_trade_count,
                        out_of_sample_expectancy_r=excluded.out_of_sample_expectancy_r,
                        out_of_sample_profit_factor=excluded.out_of_sample_profit_factor,
                        out_of_sample_max_drawdown_r=excluded.out_of_sample_max_drawdown_r,
                        overfit_risk=excluded.overfit_risk,
                        walk_forward_quality_json=excluded.walk_forward_quality_json,
                        sample_start=excluded.sample_start,
                        sample_end=excluded.sample_end,
                        status=excluded.status,
                        promoted_at=excluded.promoted_at
                    """,
                    (
                        set_id,
                        run_id,
                        best_trial_id,
                        pattern_id,
                        symbol,
                        interval,
                        str(payload.get("family") or meta.get("family") or ""),
                        str(payload.get("direction") or meta.get("direction") or ""),
                        json_dumps(parameter_set),
                        objective,
                        safe_float(best.get("objective_score")),
                        int(best.get("trade_count") or 0),
                        safe_float(best.get("win_rate")),
                        safe_float(best.get("expectancy_r")),
                        safe_float(best.get("profit_factor")),
                        safe_float(best.get("max_loss_r")),
                        str(best.get("train_start") or in_sample.get("sample_start") or ""),
                        str(best.get("train_end") or in_sample.get("sample_end") or ""),
                        str(best.get("test_start") or out_of_sample.get("sample_start") or ""),
                        str(best.get("test_end") or out_of_sample.get("sample_end") or ""),
                        safe_float(in_sample.get("expectancy_r") or best.get("expectancy_r")),
                        int(out_of_sample.get("trade_count") or 0),
                        safe_float(out_of_sample.get("expectancy_r")),
                        safe_float(out_of_sample.get("profit_factor")),
                        safe_float(
                            out_of_sample.get("max_drawdown_r")
                            or out_of_sample.get("max_loss_r")
                        ),
                        str(walk_forward_quality.get("overfit_risk") or ""),
                        json_dumps(walk_forward_quality),
                        str(best.get("sample_start") or ""),
                        str(best.get("sample_end") or ""),
                        set_status,
                        str(payload.get("finished_at") or now),
                    ),
                )

    @staticmethod
    def _qualify_optimized_set(result: dict[str, Any]) -> dict[str, Any]:
        out_of_sample = (
            result.get("out_of_sample")
            if isinstance(result.get("out_of_sample"), dict)
            else {}
        )
        if not out_of_sample:
            return {
                "passed": False,
                "status": "legacy_unverified",
                "reasons": ["out_of_sample_missing"],
                "overfit_risk": "unknown",
                "min_out_of_sample_trade_count": OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_TRADES,
                "min_out_of_sample_expectancy_r": OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_EXPECTANCY_R,
                "min_out_of_sample_profit_factor": OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_PROFIT_FACTOR,
            }
        trade_count = int(out_of_sample.get("trade_count") or 0)
        expectancy_r = safe_float(out_of_sample.get("expectancy_r"))
        profit_factor = safe_float(out_of_sample.get("profit_factor"))
        max_drawdown_r = safe_float(
            out_of_sample.get("max_drawdown_r") or out_of_sample.get("max_loss_r")
        )
        walk_forward = (
            result.get("walk_forward")
            if isinstance(result.get("walk_forward"), dict)
            else {}
        )
        reasons: list[str] = []
        if trade_count < OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_TRADES:
            reasons.append("out_of_sample_trade_count")
        if expectancy_r <= OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_EXPECTANCY_R:
            reasons.append("out_of_sample_expectancy_negative")
        if profit_factor < OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_PROFIT_FACTOR:
            reasons.append("out_of_sample_profit_factor_low")
        if max_drawdown_r and max_drawdown_r < OPTIMIZED_SET_MAX_OUT_OF_SAMPLE_DRAWDOWN_R:
            reasons.append("out_of_sample_drawdown_excessive")
        walk_forward_window_count = int(walk_forward.get("window_count") or 0)
        walk_forward_passed_window_count = int(
            walk_forward.get("passed_window_count") or 0
        )
        walk_forward_pass_rate = safe_float(walk_forward.get("pass_rate_pct"))
        computed_walk_forward_pass_rate = 0.0
        if walk_forward_window_count > 0:
            computed_walk_forward_pass_rate = (
                walk_forward_passed_window_count / walk_forward_window_count * 100.0
            )
        if walk_forward_window_count > 0 and walk_forward_pass_rate <= 0:
            walk_forward_pass_rate = computed_walk_forward_pass_rate
        if not walk_forward or walk_forward_window_count <= 0:
            reasons.append("walk_forward_windows_missing")
        elif walk_forward_window_count < OPTIMIZED_SET_MIN_WALK_FORWARD_WINDOWS:
            reasons.append("walk_forward_window_count")
        if walk_forward and walk_forward_window_count > 0:
            if abs(walk_forward_pass_rate - computed_walk_forward_pass_rate) > 0.01:
                reasons.append("walk_forward_pass_rate_inconsistent")
            if walk_forward_pass_rate < OPTIMIZED_SET_MIN_WALK_FORWARD_PASS_RATE_PCT:
                reasons.append("walk_forward_pass_rate_low")
            if not bool(walk_forward.get("passed")):
                for reason in list(walk_forward.get("reasons") or []):
                    key = str(reason or "").strip()
                    if key and key not in reasons:
                        reasons.append(key)
        passed = not reasons
        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "reasons": reasons,
            "overfit_risk": "low" if passed else "high",
            "out_of_sample_trade_count": trade_count,
            "out_of_sample_expectancy_r": round(expectancy_r, 4),
            "out_of_sample_profit_factor": round(profit_factor, 4),
            "out_of_sample_max_drawdown_r": round(max_drawdown_r, 4),
            "window_count": walk_forward_window_count,
            "passed_window_count": walk_forward_passed_window_count,
            "pass_rate_pct": round(walk_forward_pass_rate, 4),
            "windows": list(walk_forward.get("windows") or [])[:12]
            if walk_forward
            else [],
            "min_out_of_sample_trade_count": OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_TRADES,
            "min_out_of_sample_expectancy_r": OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_EXPECTANCY_R,
            "min_out_of_sample_profit_factor": OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_PROFIT_FACTOR,
            "max_out_of_sample_drawdown_r": OPTIMIZED_SET_MAX_OUT_OF_SAMPLE_DRAWDOWN_R,
            "min_window_count": OPTIMIZED_SET_MIN_WALK_FORWARD_WINDOWS,
            "min_pass_rate_pct": OPTIMIZED_SET_MIN_WALK_FORWARD_PASS_RATE_PCT,
        }

    @staticmethod
    def _pattern_meta(conn: sqlite3.Connection, pattern_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT family, direction, name FROM strategy_patterns WHERE pattern_id = ?",
            (pattern_id,),
        ).fetchone()
        return dict(row) if row is not None else {}

    def latest_optimized_strategy_sets(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_limit = max(int(limit), 1)
        clean_symbols = [
            normalize_symbol(symbol)
            for symbol in symbols or []
            if normalize_symbol(symbol)
        ]
        params: list[Any] = []
        where = "WHERE o.status = 'active'"
        if clean_symbols:
            placeholders = ",".join("?" for _ in clean_symbols)
            where += f" AND o.symbol IN ({placeholders})"
            params.extend(clean_symbols)
        params.append(clean_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT o.*, p.name, p.risk_tags_json, s.license, s.license_policy
                FROM optimized_strategy_sets o
                LEFT JOIN strategy_patterns p ON p.pattern_id = o.pattern_id
                LEFT JOIN freqtrade_strategy_sources s ON s.source_id = p.source_id
                {where}
                ORDER BY o.objective_score DESC, o.promoted_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_optimized_strategy_set(row) for row in rows]

    def latest_rejected_optimized_strategy_sets(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_limit = max(int(limit), 1)
        clean_symbols = [
            normalize_symbol(symbol)
            for symbol in symbols or []
            if normalize_symbol(symbol)
        ]
        params: list[Any] = []
        where = "WHERE o.status = 'rejected'"
        if clean_symbols:
            placeholders = ",".join("?" for _ in clean_symbols)
            where += f" AND o.symbol IN ({placeholders})"
            params.extend(clean_symbols)
        params.append(clean_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT o.*, p.name, p.risk_tags_json, s.license, s.license_policy
                FROM optimized_strategy_sets o
                LEFT JOIN strategy_patterns p ON p.pattern_id = o.pattern_id
                LEFT JOIN freqtrade_strategy_sources s ON s.source_id = p.source_id
                {where}
                ORDER BY o.objective_score DESC, o.promoted_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_optimized_strategy_set(row) for row in rows]

    def latest_scorecards(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_limit = max(int(limit), 1)
        clean_symbols = [
            normalize_symbol(symbol)
            for symbol in symbols or []
            if normalize_symbol(symbol)
        ]
        params: list[Any] = []
        where = ""
        if clean_symbols:
            placeholders = ",".join("?" for _ in clean_symbols)
            where = f"WHERE b.symbol IN ({placeholders})"
            params.extend(clean_symbols)
        params.append(max(clean_limit * 64, 256))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        b.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY b.pattern_id, b.symbol, b.interval
                            ORDER BY b.evaluated_at DESC, b.id DESC
                        ) AS recency_rank
                    FROM pattern_backtests b
                    {where}
                )
                SELECT ranked.*, p.family, p.direction, p.name, p.risk_tags_json,
                    p.source_id, s.license, s.license_policy
                FROM ranked
                LEFT JOIN strategy_patterns p ON p.pattern_id = ranked.pattern_id
                LEFT JOIN freqtrade_strategy_sources s ON s.source_id = p.source_id
                WHERE ranked.recency_rank = 1
                ORDER BY ranked.score DESC, ranked.evaluated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        scorecards = [self._row_to_scorecard(row) for row in rows]
        return self._diversify_scorecards(scorecards, limit=clean_limit)

    def _diversify_scorecards(
        self,
        scorecards: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        clean_limit = max(int(limit), 1)
        if len(scorecards) <= clean_limit:
            return scorecards[:clean_limit]

        selected: list[dict[str, Any]] = []
        selected_ids: set[tuple[str, str, str]] = set()
        exact_groups: set[tuple[str, str, str, str]] = set()
        family_counts: dict[str, int] = {}
        max_per_family = max(1, (clean_limit + 3) // 4)

        def row_id(row: dict[str, Any]) -> tuple[str, str, str]:
            return (
                str(row.get("pattern_id") or ""),
                str(row.get("symbol") or ""),
                str(row.get("interval") or ""),
            )

        def exact_group(row: dict[str, Any]) -> tuple[str, str, str, str]:
            return (
                str(row.get("symbol") or ""),
                str(row.get("interval") or ""),
                str(row.get("family") or ""),
                str(row.get("direction") or ""),
            )

        def add(row: dict[str, Any], *, enforce_family_cap: bool) -> bool:
            identity = row_id(row)
            if identity in selected_ids:
                return False
            group = exact_group(row)
            if group in exact_groups:
                return False
            family = str(row.get("family") or "unknown")
            if enforce_family_cap and family_counts.get(family, 0) >= max_per_family:
                return False
            selected.append(row)
            selected_ids.add(identity)
            exact_groups.add(group)
            family_counts[family] = family_counts.get(family, 0) + 1
            return len(selected) >= clean_limit

        for row in scorecards:
            if add(row, enforce_family_cap=True):
                return selected
        for row in scorecards:
            if add(row, enforce_family_cap=False):
                return selected

        return selected[:clean_limit]

    def pattern_context(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        patterns = self.list_patterns(limit=limit)
        scorecards = self.latest_scorecards(symbols=symbols, limit=limit)
        optimized_strategy_sets = self.latest_optimized_strategy_sets(
            symbols=symbols,
            limit=limit,
        )
        rejected_optimized_strategy_sets = self.latest_rejected_optimized_strategy_sets(
            symbols=symbols,
            limit=limit,
        )
        qualified_scorecards = [
            scorecard
            for scorecard in scorecards
            if (scorecard.get("entry_quality") or {}).get("qualifies_for_entry") is True
        ]
        sources = self.list_sources(limit=limit)
        evidence = [
            evidence_from_signal(
                source="crypto_pattern_lab",
                signal_type="pattern_scorecard",
                symbol=scorecard.get("symbol"),
                scope="binance",
                confidence=safe_float(scorecard.get("score")) / 100.0,
                ttl_sec=24 * 60 * 60,
                captured_at=str(scorecard.get("evaluated_at") or utc_now_iso()),
                payload={
                    "pattern_key": scorecard.get("pattern_key"),
                    "family": scorecard.get("family"),
                    "direction": scorecard.get("direction"),
                    "expectancy_r": scorecard.get("expectancy_r"),
                    "sample_count": scorecard.get("trade_count"),
                    "win_rate": scorecard.get("win_rate"),
                    "license_policy": scorecard.get("license_policy"),
                },
            ).to_dict()
            for scorecard in qualified_scorecards
        ]
        optimization_evidence = [
            evidence_from_signal(
                source="crypto_pattern_lab",
                signal_type="optimized_strategy_set",
                symbol=item.get("symbol"),
                scope="binance",
                confidence=min(max(safe_float(item.get("objective_score")) / 100.0, 0.0), 1.0),
                ttl_sec=24 * 60 * 60,
                captured_at=str(item.get("promoted_at") or utc_now_iso()),
                payload={
                    "set_id": item.get("set_id"),
                    "pattern_key": item.get("pattern_key"),
                    "objective": item.get("objective"),
                    "objective_score": item.get("objective_score"),
                    "parameter_set": item.get("parameter_set"),
                    "trade_count": item.get("trade_count"),
                    "win_rate": item.get("win_rate"),
                    "expectancy_r": item.get("expectancy_r"),
                    "profit_factor": item.get("profit_factor"),
                    "max_loss_r": item.get("max_loss_r"),
                },
            ).to_dict()
            for item in optimized_strategy_sets
        ]
        return {
            "status": "ok",
            "patterns": patterns,
            "scorecards": scorecards,
            "qualified_scorecards": qualified_scorecards,
            "optimized_strategy_sets": optimized_strategy_sets,
            "rejected_optimized_strategy_sets": rejected_optimized_strategy_sets,
            "sources": sources,
            "evidence": [*evidence, *optimization_evidence],
            "optimization_evidence": optimization_evidence,
            "optimization": {
                "objective": "risk_adjusted_net_r_v1",
                "set_count": len(optimized_strategy_sets),
                "rejected_set_count": len(rejected_optimized_strategy_sets),
                "meaning": (
                    "Optimized strategy sets are parameterized pattern candidates. "
                    "Use them as empirical price-geometry priors, not as order bypasses."
                ),
            },
            "policy": {
                "meaning": (
                    "Use qualified_scorecards as empirical entry evidence. "
                    "Scorecards below the threshold are diagnostics or cautions, "
                    "not positive entry support."
                ),
                "source": "freqtrade_static_extract_plus_hermes_backtest",
                "entry_quality_threshold": {
                    "min_trade_count": PATTERN_ENTRY_MIN_TRADE_COUNT,
                    "min_win_rate": PATTERN_ENTRY_MIN_WIN_RATE,
                    "min_expectancy_r": PATTERN_ENTRY_MIN_EXPECTANCY_R,
                    "min_profit_factor": PATTERN_ENTRY_MIN_PROFIT_FACTOR,
                },
            },
        }

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        return self.pattern_context(symbols=symbols, limit=limit)

    def _row_to_source(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "path": row["path"],
            "strategy_name": row["strategy_name"],
            "source_hash": row["source_hash"],
            "license": row["license"],
            "license_policy": row["license_policy"],
            "imported_at": row["imported_at"],
            "status": row["status"],
            "error_message": row["error_message"],
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            source_count = conn.execute(
                "SELECT COUNT(*) FROM freqtrade_strategy_sources"
            ).fetchone()[0]
            pattern_count = conn.execute(
                "SELECT COUNT(*) FROM strategy_patterns"
            ).fetchone()[0]
            backtest_count = conn.execute(
                "SELECT COUNT(*) FROM pattern_backtests"
            ).fetchone()[0]
            optimization_run_count = conn.execute(
                "SELECT COUNT(*) FROM optimization_runs"
            ).fetchone()[0]
            optimized_set_count = conn.execute(
                "SELECT COUNT(*) FROM optimized_strategy_sets WHERE status = 'active'"
            ).fetchone()[0]
            rejected_optimized_set_count = conn.execute(
                "SELECT COUNT(*) FROM optimized_strategy_sets WHERE status = 'rejected'"
            ).fetchone()[0]
            total_optimized_set_count = conn.execute(
                "SELECT COUNT(*) FROM optimized_strategy_sets"
            ).fetchone()[0]
            latest_optimized_set_at = conn.execute(
                "SELECT MAX(promoted_at) FROM optimized_strategy_sets"
            ).fetchone()[0]
            rejection_rows = conn.execute(
                """
                SELECT walk_forward_quality_json
                FROM optimized_strategy_sets
                WHERE status = 'rejected'
                ORDER BY promoted_at DESC
                LIMIT 200
                """
            ).fetchall()
        rejection_counts: dict[str, int] = {}
        for row in rejection_rows:
            quality = json_loads(row["walk_forward_quality_json"], {})
            reasons = quality.get("reasons") if isinstance(quality, dict) else []
            if not isinstance(reasons, list):
                continue
            for reason in reasons:
                clean_reason = str(reason or "").strip()
                if clean_reason:
                    rejection_counts[clean_reason] = (
                        rejection_counts.get(clean_reason, 0) + 1
                    )
        top_rejection_reasons = [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                rejection_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:8]
        ]
        validation_hint_status = "ok"
        validation_hint_reasons: list[str] = []
        if int(total_optimized_set_count or 0) == 0:
            validation_hint_status = "needs_optimization"
            validation_hint_reasons.append("optimized_sets_missing")
        elif int(optimized_set_count or 0) <= 0:
            validation_hint_status = "needs_revalidation"
            validation_hint_reasons.extend(
                row["reason"] for row in top_rejection_reasons[:4]
            )
        return {
            "status": "ok",
            "db_path": str(self.path),
            "source_count": int(source_count),
            "pattern_count": int(pattern_count),
            "backtest_count": int(backtest_count),
            "optimization_run_count": int(optimization_run_count),
            "optimized_set_count": int(optimized_set_count),
            "active_optimized_set_count": int(optimized_set_count),
            "rejected_optimized_set_count": int(rejected_optimized_set_count),
            "total_optimized_set_count": int(total_optimized_set_count),
            "latest_optimized_set_at": str(latest_optimized_set_at or ""),
            "top_rejection_reasons": top_rejection_reasons,
            "validation_hint": {
                "status": validation_hint_status,
                "reasons": validation_hint_reasons,
            },
        }

    def prune_history(
        self,
        *,
        retention_days: int = 90,
        backtests_per_tuple: int = 8,
        optimizer_runs_per_tuple: int = 8,
        optimizer_trials_per_run: int = 12,
        max_backtest_rows: int = 0,
        max_optimizer_runs: int = 0,
        max_optimizer_trials: int = 0,
    ) -> dict[str, Any]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(int(retention_days), 1))
        ).isoformat()
        keep_backtests = max(int(backtests_per_tuple), 1)
        keep_runs = max(int(optimizer_runs_per_tuple), 1)
        keep_trials = max(int(optimizer_trials_per_run), 1)
        backtest_row_cap = max(int(max_backtest_rows or 0), 0)
        optimizer_run_cap = max(int(max_optimizer_runs or 0), 0)
        optimizer_trial_cap = max(int(max_optimizer_trials or 0), 0)
        with self._connect() as conn:
            backtests = conn.execute(
                "DELETE FROM pattern_backtests WHERE evaluated_at < ?",
                (cutoff,),
            ).rowcount
            imports = conn.execute(
                "DELETE FROM freqtrade_ohlcv_imports WHERE imported_at < ?",
                (cutoff,),
            ).rowcount
            optimization_runs = conn.execute(
                "DELETE FROM optimization_runs WHERE finished_at < ?",
                (cutoff,),
            ).rowcount
            optimization_trials = conn.execute(
                """
                DELETE FROM optimization_trials
                WHERE run_id NOT IN (SELECT run_id FROM optimization_runs)
                """,
            ).rowcount
            stale = conn.execute(
                """
                DELETE FROM pattern_backtests
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT
                            id,
                            ROW_NUMBER() OVER (
                                PARTITION BY pattern_id, symbol, interval
                                ORDER BY evaluated_at DESC, id DESC
                            ) AS keep_rank
                        FROM pattern_backtests
                    )
                    WHERE keep_rank <= ?
                )
                """,
                (keep_backtests,),
            ).rowcount
            stale_runs = conn.execute(
                """
                DELETE FROM optimization_runs
                WHERE run_id NOT IN (
                    SELECT run_id FROM (
                        SELECT
                            run_id,
                            ROW_NUMBER() OVER (
                                PARTITION BY pattern_id, symbol, interval, objective
                                ORDER BY finished_at DESC, started_at DESC, run_id DESC
                            ) AS keep_rank
                        FROM optimization_runs
                    )
                    WHERE keep_rank <= ?
                )
                  AND run_id NOT IN (
                    SELECT run_id
                    FROM optimized_strategy_sets
                    WHERE TRIM(COALESCE(run_id, '')) <> ''
                  )
                """,
                (keep_runs,),
            ).rowcount
            stale_run_trials = conn.execute(
                """
                DELETE FROM optimization_trials
                WHERE run_id NOT IN (SELECT run_id FROM optimization_runs)
                """
            ).rowcount
            capped_backtests = 0
            if backtest_row_cap > 0:
                capped_backtests = conn.execute(
                    """
                    DELETE FROM pattern_backtests
                    WHERE id NOT IN (
                        SELECT id
                        FROM pattern_backtests
                        ORDER BY evaluated_at DESC, id DESC
                        LIMIT ?
                    )
                    """,
                    (backtest_row_cap,),
                ).rowcount
            capped_runs = 0
            if optimizer_run_cap > 0:
                capped_runs = conn.execute(
                    """
                    DELETE FROM optimization_runs
                    WHERE run_id NOT IN (
                        SELECT run_id
                        FROM optimization_runs
                        ORDER BY finished_at DESC, started_at DESC, run_id DESC
                        LIMIT ?
                    )
                      AND run_id NOT IN (
                        SELECT run_id
                        FROM optimized_strategy_sets
                        WHERE TRIM(COALESCE(run_id, '')) <> ''
                      )
                    """,
                    (optimizer_run_cap,),
                ).rowcount
            capped_run_trials = conn.execute(
                """
                DELETE FROM optimization_trials
                WHERE run_id NOT IN (SELECT run_id FROM optimization_runs)
                """
            ).rowcount
            stale_trials = conn.execute(
                """
                DELETE FROM optimization_trials
                WHERE trial_id NOT IN (
                    SELECT trial_id FROM (
                        SELECT
                            trial_id,
                            ROW_NUMBER() OVER (
                                PARTITION BY run_id
                                ORDER BY objective_score DESC, evaluated_at DESC, trial_id DESC
                            ) AS keep_rank
                        FROM optimization_trials
                    )
                    WHERE keep_rank <= ?
                )
                  AND trial_id NOT IN (
                    SELECT best_trial_id
                    FROM optimization_runs
                    WHERE TRIM(COALESCE(best_trial_id, '')) <> ''
                  )
                  AND trial_id NOT IN (
                    SELECT trial_id
                    FROM optimized_strategy_sets
                    WHERE TRIM(COALESCE(trial_id, '')) <> ''
                  )
                """,
                (keep_trials,),
            ).rowcount
            capped_trials = 0
            if optimizer_trial_cap > 0:
                capped_trials = conn.execute(
                    """
                    DELETE FROM optimization_trials
                    WHERE trial_id NOT IN (
                        SELECT trial_id
                        FROM optimization_trials
                        ORDER BY evaluated_at DESC, objective_score DESC, trial_id DESC
                        LIMIT ?
                    )
                      AND trial_id NOT IN (
                        SELECT best_trial_id
                        FROM optimization_runs
                        WHERE TRIM(COALESCE(best_trial_id, '')) <> ''
                      )
                      AND trial_id NOT IN (
                        SELECT trial_id
                        FROM optimized_strategy_sets
                        WHERE TRIM(COALESCE(trial_id, '')) <> ''
                      )
                    """,
                    (optimizer_trial_cap,),
                ).rowcount
            should_vacuum = any(
                int(value or 0) > 0
                for value in (
                    backtests,
                    imports,
                    optimization_runs,
                    optimization_trials,
                    stale,
                    stale_runs,
                    stale_run_trials,
                    capped_backtests,
                    capped_runs,
                    capped_run_trials,
                    stale_trials,
                    capped_trials,
                )
            )
        vacuumed = False
        if should_vacuum:
            with self._connect() as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "cutoff": cutoff,
            "backtests_per_tuple": keep_backtests,
            "optimizer_runs_per_tuple": keep_runs,
            "optimizer_trials_per_run": keep_trials,
            "max_backtest_rows": backtest_row_cap,
            "max_optimizer_runs": optimizer_run_cap,
            "max_optimizer_trials": optimizer_trial_cap,
            "backtests_deleted": int(backtests or 0),
            "imports_deleted": int(imports or 0),
            "optimization_runs_deleted": int(optimization_runs or 0),
            "optimization_trials_deleted": int(optimization_trials or 0),
            "stale_backtests_deleted": int(stale or 0),
            "stale_optimization_runs_deleted": int(stale_runs or 0),
            "stale_optimization_run_trials_deleted": int(stale_run_trials or 0),
            "capped_backtests_deleted": int(capped_backtests or 0),
            "capped_optimization_runs_deleted": int(capped_runs or 0),
            "capped_optimization_run_trials_deleted": int(capped_run_trials or 0),
            "stale_optimization_trials_deleted": int(stale_trials or 0),
            "capped_optimization_trials_deleted": int(capped_trials or 0),
            "vacuumed": vacuumed,
        }

    def _row_to_pattern(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "pattern_id": row["pattern_id"],
            "source_id": row["source_id"],
            "name": row["name"],
            "family": row["family"],
            "direction": row["direction"],
            "timeframe": row["timeframe"],
            "indicators": json_loads(row["indicators_json"], []),
            "expression": json_loads(row["expression_json"], {}),
            "risk_tags": json_loads(row["risk_tags_json"], []),
            "created_at": row["created_at"],
        }

    def _row_to_scorecard(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = {
            "pattern_id": row["pattern_id"],
            "pattern_key": f"{row['family']}:{row['direction']}:{row['interval']}",
            "symbol": row["symbol"],
            "interval": row["interval"],
            "family": row["family"],
            "direction": row["direction"],
            "name": row["name"],
            "source_id": row["source_id"],
            "license": row["license"],
            "license_policy": row["license_policy"],
            "trade_count": int(row["trade_count"]),
            "win_rate": float(row["win_rate"]),
            "expectancy_r": float(row["expectancy_r"]),
            "profit_factor": float(row["profit_factor"]),
            "score": float(row["score"]),
            "warnings": json_loads(row["warnings_json"], []),
            "evaluated_at": row["evaluated_at"],
        }
        payload["entry_quality"] = self._entry_quality(payload)
        return payload

    def _row_to_optimized_strategy_set(self, row: sqlite3.Row) -> dict[str, Any]:
        family = row["family"] or ""
        direction = row["direction"] or ""
        interval = row["interval"] or ""
        return {
            "set_id": row["set_id"],
            "run_id": row["run_id"],
            "trial_id": row["trial_id"],
            "pattern_id": row["pattern_id"],
            "pattern_key": f"{family}:{direction}:{interval}",
            "symbol": row["symbol"],
            "interval": interval,
            "family": family,
            "direction": direction,
            "name": row["name"] or "",
            "parameter_set": json_loads(row["parameter_set_json"], {}),
            "objective": row["objective"],
            "objective_score": float(row["objective_score"]),
            "trade_count": int(row["trade_count"]),
            "win_rate": float(row["win_rate"]),
            "expectancy_r": float(row["expectancy_r"]),
            "profit_factor": float(row["profit_factor"]),
            "max_loss_r": float(row["max_loss_r"]),
            "train_start": row["train_start"],
            "train_end": row["train_end"],
            "test_start": row["test_start"],
            "test_end": row["test_end"],
            "in_sample_expectancy_r": float(row["in_sample_expectancy_r"]),
            "out_of_sample_trade_count": int(row["out_of_sample_trade_count"]),
            "out_of_sample_expectancy_r": float(row["out_of_sample_expectancy_r"]),
            "out_of_sample_profit_factor": float(row["out_of_sample_profit_factor"]),
            "out_of_sample_max_drawdown_r": float(row["out_of_sample_max_drawdown_r"]),
            "overfit_risk": row["overfit_risk"] or "",
            "walk_forward_quality": json_loads(row["walk_forward_quality_json"], {}),
            "sample_start": row["sample_start"],
            "sample_end": row["sample_end"],
            "status": row["status"],
            "promoted_at": row["promoted_at"],
            "license": row["license"] or "",
            "license_policy": row["license_policy"] or "",
            "risk_tags": json_loads(row["risk_tags_json"], []),
        }

    @staticmethod
    def _entry_quality(scorecard: dict[str, Any]) -> dict[str, Any]:
        trade_count = int(scorecard.get("trade_count") or 0)
        win_rate = safe_float(scorecard.get("win_rate"))
        expectancy_r = safe_float(scorecard.get("expectancy_r"))
        profit_factor = safe_float(scorecard.get("profit_factor"))
        failed: list[str] = []
        if trade_count < PATTERN_ENTRY_MIN_TRADE_COUNT:
            failed.append("trade_count")
        if win_rate < PATTERN_ENTRY_MIN_WIN_RATE:
            failed.append("win_rate")
        if expectancy_r <= PATTERN_ENTRY_MIN_EXPECTANCY_R:
            failed.append("expectancy_r")
        if profit_factor < PATTERN_ENTRY_MIN_PROFIT_FACTOR:
            failed.append("profit_factor")
        return {
            "qualifies_for_entry": not failed,
            "failed_reasons": failed,
            "min_trade_count": PATTERN_ENTRY_MIN_TRADE_COUNT,
            "min_win_rate": PATTERN_ENTRY_MIN_WIN_RATE,
            "min_expectancy_r": PATTERN_ENTRY_MIN_EXPECTANCY_R,
            "min_profit_factor": PATTERN_ENTRY_MIN_PROFIT_FACTOR,
        }


class FreqtradeStrategyExtractor:
    def extract_file(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        if file_path.suffix != ".py":
            return {
                "status": "error",
                "path": str(file_path),
                "error_message": "Freqtrade strategy source must be a Python .py file",
                "patterns": [],
            }
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception as exc:
            return {
                "status": "error",
                "path": str(file_path),
                "error_message": str(exc),
                "patterns": [],
            }
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        strategy_name = self._strategy_name(tree)
        timeframe = self._timeframe(tree) or "15m"
        indicators = self._indicator_names(tree)
        directions = self._directions(tree)
        families = self._families(indicators=indicators, tree=tree)
        patterns = []
        source_id = f"sha256:{source_hash[:24]}"
        for family in families:
            for direction in directions:
                pattern_id = f"{source_id}:{family}:{direction}:{timeframe}"
                patterns.append(
                    {
                        "pattern_id": pattern_id,
                        "source_id": source_id,
                        "name": f"{strategy_name} {family} {direction}",
                        "family": family,
                        "direction": direction,
                        "timeframe": timeframe,
                        "indicators": sorted(indicators),
                        "expression": {
                            "enter_column": (
                                "enter_short" if direction == "short" else "enter_long"
                            ),
                            "source": "static_ast",
                        },
                        "risk_tags": self._risk_tags(family),
                    }
                )
        return {
            "status": "ok",
            "path": str(file_path),
            "source_id": source_id,
            "source_hash": source_hash,
            "strategy_name": strategy_name,
            "patterns": patterns,
        }

    def _strategy_name(self, tree: ast.AST) -> str:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                return node.name
        return "FreqtradeStrategy"

    def _timeframe(self, tree: ast.AST) -> str:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [
                    target.id for target in node.targets if isinstance(target, ast.Name)
                ]
                if "timeframe" in names and isinstance(node.value, ast.Constant):
                    return str(node.value.value)
        return ""

    def _indicator_names(self, tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                value = self._constant_slice_value(node)
                if value in {
                    "rsi",
                    "ema_fast",
                    "ema_slow",
                    "macd",
                    "bb_lowerband",
                    "bb_upperband",
                    "volume",
                }:
                    names.add(value)
            if isinstance(node, ast.Attribute):
                attr = node.attr.lower()
                if attr in {"rsi", "ema", "macd", "bollinger_bands"}:
                    names.add(attr)
        return names

    def _constant_slice_value(self, node: ast.Subscript) -> str:
        if isinstance(node.slice, ast.Constant):
            return str(node.slice.value).lower()
        return ""

    def _directions(self, tree: ast.AST) -> list[str]:
        directions: list[str] = []
        written_columns = self._entry_columns(tree)
        can_short = self._can_short(tree)
        if {"enter_long", "buy"}.intersection(written_columns):
            directions.append("long")
        if {"enter_short", "sell"}.intersection(written_columns) and can_short is not False:
            directions.append("short")
        return directions or ["long"]

    def _can_short(self, tree: ast.AST) -> bool | None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "can_short" not in names:
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool):
                return bool(node.value.value)
        return None

    def _entry_columns(self, tree: ast.AST) -> set[str]:
        columns: set[str] = set()
        for function in [
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ]:
            if function.name not in {"populate_entry_trend", "populate_buy_trend"}:
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    columns.update(self._column_names_from_target(target))
        return columns

    def _column_names_from_target(self, target: ast.AST) -> set[str]:
        columns: set[str] = set()
        for node in ast.walk(target):
            if isinstance(node, ast.Subscript):
                value = self._constant_slice_value(node)
                if value:
                    columns.add(value)
            if isinstance(node, ast.Tuple):
                for element in node.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        columns.add(element.value.lower())
        return columns

    def _families(self, *, indicators: set[str], tree: ast.AST) -> list[str]:
        families: list[str] = []
        if "rsi" in indicators:
            families.append("rsi_mean_reversion")
        if "ema" in indicators or {"ema_fast", "ema_slow"}.intersection(indicators):
            families.append("ema_trend")
        if (
            "bollinger_bands" in indicators
            or "bb_lowerband" in indicators
            or "bb_upperband" in indicators
        ):
            families.append("bollinger_squeeze")
        if "macd" in indicators:
            families.append("macd_momentum")
        if "volume" in indicators or self._tree_mentions(tree, {"volume"}):
            families.append("volume_confirmation")
        return list(dict.fromkeys(families or ["generic_price_action"]))

    def _tree_mentions(self, tree: ast.AST, names: set[str]) -> bool:
        wanted = {name.lower() for name in names}
        for node in ast.walk(tree):
            values: list[str] = []
            if isinstance(node, ast.Name):
                values.append(node.id)
            elif isinstance(node, ast.Attribute):
                values.append(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                values.append(node.value)
            elif isinstance(node, ast.Subscript):
                values.append(self._constant_slice_value(node))
            if any(value.lower() in wanted for value in values if value):
                return True
        return False

    def _risk_tags(self, family: str) -> list[str]:
        mapping = {
            "rsi_mean_reversion": ["mean_reversion", "can_fight_trend"],
            "ema_trend": ["trend_following", "late_entry_risk"],
            "bollinger_squeeze": ["volatility_expansion", "false_breakout_risk"],
            "macd_momentum": ["momentum", "lagging_indicator"],
            "volume_confirmation": ["liquidity_sensitive"],
        }
        return mapping.get(family, ["needs_manual_review"])


class FreqtradeOHLCVImporter:
    def read_file(
        self,
        path: str | Path,
        *,
        symbol: str,
        interval: str,
    ) -> list[dict[str, Any]]:
        file_path = Path(path)
        if file_path.suffix == ".gz" or file_path.name.endswith(".json.gz"):
            with gzip.open(file_path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, list) or len(item) < 6:
                continue
            open_time = int(safe_float(item[0]))
            rows.append(
                {
                    "symbol": normalize_symbol(symbol),
                    "interval": str(interval),
                    "open_time": open_time,
                    "open": safe_float(item[1]),
                    "high": safe_float(item[2]),
                    "low": safe_float(item[3]),
                    "close": safe_float(item[4]),
                    "volume": safe_float(item[5]),
                    "quote_volume": safe_float(item[5]) * safe_float(item[4]),
                    "close_time": open_time,
                }
            )
        return rows


class FreqtradeDataCatalog:
    def __init__(self, paths: str) -> None:
        self.paths = [
            Path(item.strip()).expanduser()
            for item in str(paths or "").replace(";", ",").split(",")
            if item.strip()
        ]
        self.importer = FreqtradeOHLCVImporter()

    def read(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        for path in self._candidate_files(symbol=symbol, interval=interval):
            try:
                rows = self.importer.read_file(path, symbol=symbol, interval=interval)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if rows:
                return rows[-max(int(limit), 1) :]
        return []

    def _candidate_files(self, *, symbol: str, interval: str) -> list[Path]:
        normalized = normalize_symbol(symbol)
        pair = self._freqtrade_pair(symbol)
        names = [
            f"{pair}-{interval}.json",
            f"{pair}-{interval}.json.gz",
            f"{normalized}-{interval}.json",
            f"{normalized}-{interval}.json.gz",
        ]
        out: list[Path] = []
        for root in self.paths:
            if root.is_file() and root.name in names:
                out.append(root)
            elif root.is_dir():
                for name in names:
                    out.extend(root.rglob(name))
        return list(dict.fromkeys(out))

    def _freqtrade_pair(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        if normalized.endswith("USDT") and len(normalized) > 4:
            return f"{normalized[:-4]}_USDT"
        return normalized


class HermesKlineReader:
    def __init__(self, crypto_market_db_path: str | Path) -> None:
        self.path = Path(crypto_market_db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def read(
        self,
        *,
        symbol: str,
        interval: str,
        market: str = "spot",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, interval, open_time, open, high, low, close,
                       volume, quote_volume, close_time
                FROM crypto_klines
                WHERE symbol = ? AND market = ? AND interval = ?
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (normalize_symbol(symbol), str(market), str(interval), max(int(limit), 1)),
            ).fetchall()
        return [
            {
                "symbol": row["symbol"],
                "interval": row["interval"],
                "open_time": int(row["open_time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "quote_volume": float(row["quote_volume"]),
                "close_time": int(row["close_time"]),
            }
            for row in reversed(rows)
        ]


class PatternBacktestLab:
    def evaluate(
        self,
        *,
        pattern: dict[str, Any],
        symbol: str,
        interval: str,
        rows: list[dict[str, Any]],
        max_trades: int = 80,
        stop_pct: float = 0.012,
        target_pct: float = 0.024,
        holding_bars: int = 6,
    ) -> dict[str, Any]:
        clean_rows = [row for row in rows if safe_float(row.get("close")) > 0]
        pattern_id = str(pattern.get("pattern_id") or "")
        family = str(pattern.get("family") or "generic_price_action")
        direction = str(pattern.get("direction") or "long")
        warnings: list[str] = []
        if len(clean_rows) < 30:
            return {
                "pattern_id": pattern_id,
                "symbol": normalize_symbol(symbol),
                "interval": interval,
                "trade_count": 0,
                "win_rate": 0.0,
                "expectancy_r": 0.0,
                "avg_r": 0.0,
                "profit_factor": 0.0,
                "max_loss_r": 0.0,
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "net_r": 0.0,
                "score": 0.0,
                "warnings": ["insufficient_rows"],
                "sample_start": "",
                "sample_end": "",
            }

        trades: list[float] = []
        mfe_values: list[float] = []
        mae_values: list[float] = []
        clean_holding_bars = max(int(holding_bars), 2)
        clean_stop_pct = max(safe_float(stop_pct), 0.001)
        clean_target_pct = max(safe_float(target_pct), 0.001)
        for index in range(21, len(clean_rows) - clean_holding_bars):
            if len(trades) >= max_trades:
                break
            if not self._entry_signal(
                family=family,
                direction=direction,
                rows=clean_rows,
                index=index,
            ):
                continue
            outcome = self._simulate_trade(
                direction=direction,
                rows=clean_rows[index : index + clean_holding_bars + 1],
                stop_pct=clean_stop_pct,
                target_pct=clean_target_pct,
            )
            trades.append(outcome["r"])
            mfe_values.append(outcome["mfe_r"])
            mae_values.append(outcome["mae_r"])

        wins = [value for value in trades if value > 0]
        losses = [value for value in trades if value <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        trade_count = len(trades)
        win_rate = len(wins) / trade_count if trade_count else 0.0
        avg_r = sum(trades) / trade_count if trade_count else 0.0
        net_r = sum(trades)
        profit_factor = gross_win / gross_loss if gross_loss > 0 else gross_win
        max_loss_r = min(trades) if trades else 0.0
        expectancy_r = avg_r
        if trade_count < 5:
            warnings.append("low_sample")
        if profit_factor < 1.0 and trade_count:
            warnings.append("negative_profit_factor")
        score = max(
            min(
                (expectancy_r * 35.0)
                + (win_rate * 35.0)
                + min(profit_factor, 3.0) * 10.0,
                100.0,
            ),
            0.0,
        )
        sample_start = clean_rows[0].get("open_time")
        sample_end = clean_rows[-1].get("open_time")
        return {
            "pattern_id": pattern_id,
            "symbol": normalize_symbol(symbol),
            "interval": interval,
            "sample_start": "" if sample_start is None else str(sample_start),
            "sample_end": "" if sample_end is None else str(sample_end),
            "trade_count": trade_count,
            "win_rate": round(win_rate, 4),
            "expectancy_r": round(expectancy_r, 4),
            "avg_r": round(avg_r, 4),
            "profit_factor": round(profit_factor, 4),
            "max_loss_r": round(max_loss_r, 4),
            "mfe_r": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else 0.0,
            "mae_r": round(sum(mae_values) / len(mae_values), 4) if mae_values else 0.0,
            "net_r": round(net_r, 4),
            "score": round(score, 2),
            "warnings": warnings,
        }

    def _entry_signal(
        self,
        *,
        family: str,
        direction: str,
        rows: list[dict[str, Any]],
        index: int,
    ) -> bool:
        recent = [
            safe_float(row.get("close"))
            for row in rows[max(index - 13, 0) : index + 1]
        ]
        previous = [
            safe_float(row.get("close"))
            for row in rows[max(index - 20, 0) : max(index - 6, 0)]
        ]
        if len(recent) < 14 or len(previous) < 10:
            return False
        momentum = (recent[-1] - recent[0]) / recent[0] * 100.0 if recent[0] > 0 else 0.0
        prior_momentum = (
            (previous[-1] - previous[0]) / previous[0] * 100.0
            if previous[0] > 0
            else 0.0
        )
        prior_volumes = [
            safe_float(row.get("volume"))
            for row in rows[max(index - 19, 0) : index]
        ]
        avg_volume = sum(prior_volumes) / max(len(prior_volumes), 1)
        current_volume = safe_float(rows[index].get("volume"))
        volume_ok = current_volume >= avg_volume * 0.8
        if family == "ema_trend":
            return momentum > 0.2 if direction == "long" else momentum < -0.2
        if family == "rsi_mean_reversion":
            return (
                prior_momentum < -1.0 and momentum > -0.2
                if direction == "long"
                else prior_momentum > 1.0 and momentum < 0.2
            )
        if family == "volume_confirmation":
            return volume_ok and abs(momentum) > 0.25
        if family == "bollinger_squeeze":
            recent_range = max(recent) - min(recent)
            return recent_range / recent[-1] < 0.03 if recent[-1] > 0 else False
        return abs(momentum) > 0.3

    def _simulate_trade(
        self,
        *,
        direction: str,
        rows: list[dict[str, Any]],
        stop_pct: float = 0.012,
        target_pct: float = 0.024,
    ) -> dict[str, float]:
        entry = safe_float(rows[0].get("close"))
        if entry <= 0:
            return {"r": 0.0, "mfe_r": 0.0, "mae_r": 0.0}
        risk = entry * stop_pct
        if direction == "short":
            stop = entry * (1.0 + stop_pct)
            target = entry * (1.0 - target_pct)
        else:
            stop = entry * (1.0 - stop_pct)
            target = entry * (1.0 + target_pct)
        exit_price = safe_float(rows[-1].get("close"))
        mfe_r = 0.0
        mae_r = 0.0
        for row in rows[1:]:
            high = safe_float(row.get("high"))
            low = safe_float(row.get("low"))
            if direction == "short":
                mfe_r = max(mfe_r, (entry - low) / risk)
                mae_r = min(mae_r, (entry - high) / risk)
                if high >= stop:
                    exit_price = stop
                    break
                if low <= target:
                    exit_price = target
                    break
            else:
                mfe_r = max(mfe_r, (high - entry) / risk)
                mae_r = min(mae_r, (low - entry) / risk)
                if low <= stop:
                    exit_price = stop
                    break
                if high >= target:
                    exit_price = target
                    break
        r_value = (
            (entry - exit_price) / risk
            if direction == "short"
            else (exit_price - entry) / risk
        )
        return {
            "r": round(r_value, 4),
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
        }


class PatternOptimizationLab:
    objective = "risk_adjusted_net_r_v1"

    def __init__(
        self,
        *,
        parameter_grid: list[dict[str, Any]] | None = None,
        backtest_lab: PatternBacktestLab | None = None,
    ) -> None:
        self.parameter_grid = parameter_grid or self.default_parameter_grid()
        self.backtest_lab = backtest_lab or PatternBacktestLab()

    @staticmethod
    def default_parameter_grid() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for stop_pct in (0.008, 0.012, 0.018):
            for reward_risk in (1.25, 1.5, 2.0):
                for holding_bars in (4, 6, 10):
                    out.append(
                        {
                            "stop_pct": stop_pct,
                            "target_pct": round(stop_pct * reward_risk, 6),
                            "holding_bars": holding_bars,
                        }
                    )
        return out

    def optimize(
        self,
        *,
        pattern: dict[str, Any],
        symbol: str,
        interval: str,
        rows: list[dict[str, Any]],
        max_trials: int | None = None,
    ) -> dict[str, Any]:
        pattern_id = str(pattern.get("pattern_id") or "")
        clean_symbol = normalize_symbol(symbol)
        started_at = utc_now_iso()
        grid = list(self.parameter_grid)
        if max_trials is not None:
            grid = grid[: max(int(max_trials), 1)]
        run_id = stable_id(pattern_id, clean_symbol, interval, started_at)
        train_rows, test_rows = self._train_test_split(rows)
        evaluation_rows = train_rows or rows
        trials: list[dict[str, Any]] = []
        for index, params in enumerate(grid, start=1):
            result = self.backtest_lab.evaluate(
                pattern=pattern,
                symbol=clean_symbol,
                interval=interval,
                rows=evaluation_rows,
                stop_pct=safe_float(params.get("stop_pct")),
                target_pct=safe_float(params.get("target_pct")),
                holding_bars=int(params.get("holding_bars") or 6),
            )
            trial = {
                **result,
                "trial_id": stable_id(run_id, index, json_dumps(params)),
                "parameter_set": self._normalize_parameter_set(params),
                "objective_score": self._objective_score(result),
                "evaluated_at": utc_now_iso(),
            }
            trials.append(trial)
        best = max(
            trials,
            key=lambda row: (
                safe_float(row.get("objective_score")),
                safe_float(row.get("net_r")),
                safe_float(row.get("expectancy_r")),
            ),
            default=None,
        )
        if best is not None:
            best = dict(best)
            in_sample = self._sample_evidence(best)
            best["in_sample"] = in_sample
            best["train_start"] = in_sample["sample_start"]
            best["train_end"] = in_sample["sample_end"]
            if test_rows:
                parameter_set = best.get("parameter_set") or {}
                out_of_sample = self.backtest_lab.evaluate(
                    pattern=pattern,
                    symbol=clean_symbol,
                    interval=interval,
                    rows=test_rows,
                    stop_pct=safe_float(parameter_set.get("stop_pct")),
                    target_pct=safe_float(parameter_set.get("target_pct")),
                    holding_bars=int(parameter_set.get("holding_bars") or 6),
                )
                oos_evidence = self._sample_evidence(out_of_sample)
                best["out_of_sample"] = oos_evidence
                best["test_start"] = oos_evidence["sample_start"]
                best["test_end"] = oos_evidence["sample_end"]
            parameter_set = best.get("parameter_set") or {}
            best["walk_forward"] = self._walk_forward_evidence(
                pattern=pattern,
                symbol=clean_symbol,
                interval=interval,
                rows=rows,
                parameter_set=parameter_set,
            )
        return {
            "status": "ok" if best is not None else "empty",
            "run_id": run_id,
            "pattern_id": pattern_id,
            "family": str(pattern.get("family") or ""),
            "direction": str(pattern.get("direction") or ""),
            "symbol": clean_symbol,
            "interval": interval,
            "objective": self.objective,
            "search_space": [self._normalize_parameter_set(params) for params in grid],
            "trial_count": len(trials),
            "trials": trials,
            "best": best or {},
            "started_at": started_at,
            "finished_at": utc_now_iso(),
        }

    @staticmethod
    def _train_test_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        clean_rows = [row for row in rows if isinstance(row, dict)]
        if len(clean_rows) < 120:
            return clean_rows, []
        split_at = int(len(clean_rows) * 0.7)
        split_at = min(max(split_at, 60), len(clean_rows) - 60)
        if split_at <= 0 or split_at >= len(clean_rows):
            return clean_rows, []
        return clean_rows[:split_at], clean_rows[split_at:]

    def _walk_forward_evidence(
        self,
        *,
        pattern: dict[str, Any],
        symbol: str,
        interval: str,
        rows: list[dict[str, Any]],
        parameter_set: dict[str, Any],
    ) -> dict[str, Any]:
        clean_rows = [
            row
            for row in rows
            if isinstance(row, dict) and safe_float(row.get("close")) > 0
        ]
        if len(clean_rows) < 180:
            return {
                "passed": False,
                "status": "insufficient_rows",
                "reasons": ["walk_forward_rows_insufficient"],
                "window_count": 0,
                "passed_window_count": 0,
                "pass_rate_pct": 0.0,
                "windows": [],
                "min_window_count": OPTIMIZED_SET_MIN_WALK_FORWARD_WINDOWS,
                "min_pass_rate_pct": OPTIMIZED_SET_MIN_WALK_FORWARD_PASS_RATE_PCT,
            }

        train_size = max(int(len(clean_rows) / 3), 60)
        test_size = max(int(len(clean_rows) / 6), 40)
        step = test_size
        windows: list[dict[str, Any]] = []
        start = 0
        while start + train_size + test_size <= len(clean_rows):
            train_rows = clean_rows[start : start + train_size]
            test_rows = clean_rows[
                start + train_size : start + train_size + test_size
            ]
            result = self.backtest_lab.evaluate(
                pattern=pattern,
                symbol=symbol,
                interval=interval,
                rows=test_rows,
                stop_pct=safe_float(parameter_set.get("stop_pct")),
                target_pct=safe_float(parameter_set.get("target_pct")),
                holding_bars=int(parameter_set.get("holding_bars") or 6),
            )
            evidence = self._sample_evidence(result)
            reasons = self._sample_fail_reasons(evidence)
            train_start = train_rows[0].get("open_time")
            train_end = train_rows[-1].get("open_time")
            windows.append(
                {
                    "index": len(windows) + 1,
                    "train_start": "" if train_start is None else str(train_start),
                    "train_end": "" if train_end is None else str(train_end),
                    "test_start": evidence["sample_start"],
                    "test_end": evidence["sample_end"],
                    "trade_count": evidence["trade_count"],
                    "expectancy_r": evidence["expectancy_r"],
                    "profit_factor": evidence["profit_factor"],
                    "max_drawdown_r": evidence["max_drawdown_r"],
                    "passed": not reasons,
                    "reasons": reasons,
                }
            )
            start += step

        passed_windows = sum(1 for window in windows if bool(window.get("passed")))
        pass_rate = passed_windows / len(windows) * 100.0 if windows else 0.0
        reasons: list[str] = []
        if len(windows) < OPTIMIZED_SET_MIN_WALK_FORWARD_WINDOWS:
            reasons.append("walk_forward_window_count")
        if pass_rate < OPTIMIZED_SET_MIN_WALK_FORWARD_PASS_RATE_PCT:
            reasons.append("walk_forward_pass_rate_low")
        passed = not reasons
        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "reasons": reasons,
            "window_count": len(windows),
            "passed_window_count": passed_windows,
            "pass_rate_pct": round(pass_rate, 4),
            "windows": windows,
            "min_window_count": OPTIMIZED_SET_MIN_WALK_FORWARD_WINDOWS,
            "min_pass_rate_pct": OPTIMIZED_SET_MIN_WALK_FORWARD_PASS_RATE_PCT,
        }

    @staticmethod
    def _sample_fail_reasons(evidence: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if int(evidence.get("trade_count") or 0) < OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_TRADES:
            reasons.append("out_of_sample_trade_count")
        if safe_float(evidence.get("expectancy_r")) <= OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_EXPECTANCY_R:
            reasons.append("out_of_sample_expectancy_negative")
        if safe_float(evidence.get("profit_factor")) < OPTIMIZED_SET_MIN_OUT_OF_SAMPLE_PROFIT_FACTOR:
            reasons.append("out_of_sample_profit_factor_low")
        max_drawdown_r = safe_float(evidence.get("max_drawdown_r"))
        if max_drawdown_r and max_drawdown_r < OPTIMIZED_SET_MAX_OUT_OF_SAMPLE_DRAWDOWN_R:
            reasons.append("out_of_sample_drawdown_excessive")
        return reasons

    @staticmethod
    def _sample_evidence(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "sample_start": str(result.get("sample_start") or ""),
            "sample_end": str(result.get("sample_end") or ""),
            "trade_count": int(result.get("trade_count") or 0),
            "win_rate": safe_float(result.get("win_rate")),
            "expectancy_r": safe_float(result.get("expectancy_r")),
            "avg_r": safe_float(result.get("avg_r")),
            "profit_factor": safe_float(result.get("profit_factor")),
            "max_loss_r": safe_float(result.get("max_loss_r")),
            "max_drawdown_r": safe_float(result.get("max_drawdown_r") or result.get("max_loss_r")),
            "mfe_r": safe_float(result.get("mfe_r")),
            "mae_r": safe_float(result.get("mae_r")),
            "net_r": safe_float(result.get("net_r")),
            "score": safe_float(result.get("score")),
            "warnings": result.get("warnings") or [],
        }

    @staticmethod
    def _normalize_parameter_set(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "stop_pct": round(max(safe_float(params.get("stop_pct")), 0.0), 6),
            "target_pct": round(max(safe_float(params.get("target_pct")), 0.0), 6),
            "holding_bars": max(int(params.get("holding_bars") or 0), 1),
        }

    @staticmethod
    def _objective_score(result: dict[str, Any]) -> float:
        trade_count = int(result.get("trade_count") or 0)
        win_rate = safe_float(result.get("win_rate"))
        expectancy_r = safe_float(result.get("expectancy_r"))
        profit_factor = safe_float(result.get("profit_factor"))
        max_loss_r = safe_float(result.get("max_loss_r"))
        net_r = safe_float(result.get("net_r"))
        sample_multiplier = min(trade_count / 12.0, 1.0)
        low_sample_penalty = max(8 - trade_count, 0) * 4.0
        drawdown_penalty = abs(min(max_loss_r, 0.0)) * 8.0
        raw = (
            (net_r * 5.0)
            + (expectancy_r * 35.0)
            + ((win_rate - 0.5) * 30.0)
            + (min(profit_factor, 4.0) * 8.0)
            - drawdown_penalty
            - low_sample_penalty
        )
        return round(max(raw * sample_multiplier, 0.0), 4)


class CryptoPatternLabService:
    def __init__(
        self,
        *,
        config: CryptoPatternLabConfig | None = None,
        repository: CryptoPatternLabRepository | None = None,
        kline_reader: Any | None = None,
    ) -> None:
        self.config = config or CryptoPatternLabConfig()
        self.repository = repository or CryptoPatternLabRepository(self.config.db_path)
        self.extractor = FreqtradeStrategyExtractor()
        self.backtest_lab = PatternBacktestLab()
        self.optimization_lab = PatternOptimizationLab()
        self.kline_reader = kline_reader
        self.freqtrade_data = FreqtradeDataCatalog(self.config.freqtrade_data_paths)

    def run_once(self, *, symbols: list[str]) -> dict[str, Any]:
        if not self.config.enabled:
            return {
                "status": "disabled",
                "imported_source_count": 0,
                "backtest_count": 0,
                "optimization_count": 0,
            }
        imported = self.import_strategies()
        backtests = self.run_backtests(symbols=symbols)
        optimizations = self.run_optimizations(symbols=symbols)
        return {
            "status": "ok",
            "imported_source_count": int(imported.get("imported_source_count") or 0),
            "pattern_count": int(imported.get("pattern_count") or 0),
            "backtest_count": int(backtests.get("backtest_count") or 0),
            "optimization_count": int(optimizations.get("optimization_count") or 0),
            "errors": [
                *imported.get("errors", []),
                *backtests.get("errors", []),
                *optimizations.get("errors", []),
            ][:10],
        }

    def import_strategies(self) -> dict[str, Any]:
        paths = self._strategy_files()
        errors: list[dict[str, str]] = []
        imported = 0
        pattern_count = 0
        for path in paths:
            result = self.extractor.extract_file(path)
            if result.get("status") != "ok":
                errors.append(
                    {
                        "path": str(path),
                        "error_message": str(result.get("error_message") or ""),
                    }
                )
                continue
            self.repository.save_strategy_source(
                {
                    "source_id": result["source_id"],
                    "path": result["path"],
                    "strategy_name": result["strategy_name"],
                    "source_hash": result["source_hash"],
                    "status": "ok",
                }
            )
            self.repository.save_patterns(list(result.get("patterns") or []))
            imported += 1
            pattern_count += len(result.get("patterns") or [])
        return {
            "status": "ok" if not errors else "partial",
            "imported_source_count": imported,
            "pattern_count": pattern_count,
            "errors": errors,
        }

    def run_backtests(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.kline_reader is None:
            return {
                "status": "skipped",
                "backtest_count": 0,
                "errors": [{"symbol": "", "error_message": "kline_reader missing"}],
            }
        patterns = self.repository.list_patterns(limit=200)
        intervals = self._intervals()
        clean_symbols = [
            normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)
        ]
        clean_symbols = clean_symbols[: max(int(self.config.max_symbols), 1)]
        errors: list[dict[str, str]] = []
        count = 0
        row_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        results: list[dict[str, Any]] = []
        for pattern in patterns:
            for symbol in clean_symbols:
                for interval in intervals:
                    try:
                        key = (symbol, interval)
                        if key not in row_cache:
                            rows = self.kline_reader.read(
                                symbol=symbol,
                                interval=interval,
                                limit=max(int(self.config.lookback_bars), 30),
                            )
                            if not rows:
                                rows = self.freqtrade_data.read(
                                    symbol=symbol,
                                    interval=interval,
                                    limit=max(int(self.config.lookback_bars), 30),
                                )
                            row_cache[key] = rows
                        rows = row_cache[key]
                        result = self.backtest_lab.evaluate(
                            pattern=pattern,
                            symbol=symbol,
                            interval=interval,
                            rows=rows,
                        )
                        results.append(result)
                        count += 1
                    except Exception as exc:
                        errors.append({"symbol": symbol, "error_message": str(exc)})
        self.repository.save_backtests(results)
        return {
            "status": "ok" if not errors else "partial",
            "backtest_count": count,
            "errors": errors[:10],
        }

    def run_optimizations(self, *, symbols: list[str]) -> dict[str, Any]:
        if not bool(self.config.optimizer_enabled):
            return {"status": "disabled", "optimization_count": 0, "errors": []}
        if self.kline_reader is None:
            return {
                "status": "skipped",
                "optimization_count": 0,
                "errors": [{"symbol": "", "error_message": "kline_reader missing"}],
            }
        clean_symbols = [
            normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)
        ]
        scorecards = self.repository.latest_scorecards(
            symbols=clean_symbols,
            limit=max(int(self.config.optimizer_max_scorecards), 1),
        )
        pattern_by_id = {
            pattern["pattern_id"]: pattern
            for pattern in self.repository.list_patterns(limit=1000)
        }
        errors: list[dict[str, str]] = []
        count = 0
        row_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for scorecard in scorecards:
            quality = scorecard.get("entry_quality") or {}
            if not bool(quality.get("qualifies_for_entry")):
                continue
            pattern_id = str(scorecard.get("pattern_id") or "")
            pattern = pattern_by_id.get(pattern_id)
            if pattern is None:
                continue
            symbol = normalize_symbol(scorecard.get("symbol"))
            interval = str(scorecard.get("interval") or "")
            try:
                key = (symbol, interval)
                if key not in row_cache:
                    rows = self.kline_reader.read(
                        symbol=symbol,
                        interval=interval,
                        limit=max(int(self.config.lookback_bars), 30),
                    )
                    if not rows:
                        rows = self.freqtrade_data.read(
                            symbol=symbol,
                            interval=interval,
                            limit=max(int(self.config.lookback_bars), 30),
                        )
                    row_cache[key] = rows
                rows = row_cache[key]
                result = self.optimization_lab.optimize(
                    pattern=pattern,
                    symbol=symbol,
                    interval=interval,
                    rows=rows,
                    max_trials=max(int(self.config.optimizer_max_trials_per_scorecard), 1),
                )
                if result.get("status") == "ok":
                    self.repository.save_optimization_result(result)
                    count += 1
            except Exception as exc:
                errors.append({"symbol": symbol, "error_message": str(exc)})
        return {
            "status": "ok" if not errors else "partial",
            "optimization_count": count,
            "errors": errors[:10],
        }

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self.repository.pattern_context(
            symbols=symbols or [],
            limit=limit or int(self.config.context_limit),
        )

    def status(self) -> dict[str, Any]:
        return {
            **self.repository.status(),
            "enabled": bool(self.config.enabled),
            "strategy_paths": self.config.strategy_paths,
            "intervals": self._intervals(),
            "max_symbols": int(self.config.max_symbols),
            "optimizer_enabled": bool(self.config.optimizer_enabled),
            "optimizer_max_scorecards": int(self.config.optimizer_max_scorecards),
            "optimizer_max_trials_per_scorecard": int(
                self.config.optimizer_max_trials_per_scorecard
            ),
            "backtests_per_tuple_retention": int(
                self.config.backtests_per_tuple_retention
            ),
            "optimizer_runs_per_tuple_retention": int(
                self.config.optimizer_runs_per_tuple_retention
            ),
            "optimizer_trials_per_run_retention": int(
                self.config.optimizer_trials_per_run_retention
            ),
            "max_backtest_rows": int(self.config.max_backtest_rows),
            "max_optimizer_runs": int(self.config.max_optimizer_runs),
            "max_optimizer_trials": int(self.config.max_optimizer_trials),
        }

    def prune_history(self) -> dict[str, Any]:
        return self.repository.prune_history(
            retention_days=self.config.retention_days,
            backtests_per_tuple=self.config.backtests_per_tuple_retention,
            optimizer_runs_per_tuple=self.config.optimizer_runs_per_tuple_retention,
            optimizer_trials_per_run=self.config.optimizer_trials_per_run_retention,
            max_backtest_rows=self.config.max_backtest_rows,
            max_optimizer_runs=self.config.max_optimizer_runs,
            max_optimizer_trials=self.config.max_optimizer_trials,
        )

    def _strategy_files(self) -> list[Path]:
        out: list[Path] = []
        for raw in str(self.config.strategy_paths or "").replace(";", ",").split(","):
            text = raw.strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if path.is_dir():
                out.extend(sorted(path.rglob("*.py")))
            elif path.is_file():
                out.append(path)
        return list(dict.fromkeys(out))

    def _intervals(self) -> list[str]:
        return [
            item.strip()
            for item in str(self.config.intervals or "5m,15m,1h").replace(";", ",").split(",")
            if item.strip()
        ]
