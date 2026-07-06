from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.db_retention import RetentionRule, SQLiteRetentionPruner
from tradecraft.services.evidence_policy import evidence_from_signal


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(min(value, high), low)


def _series_from_bars(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [_to_float(row.get(key)) for row in rows if _to_float(row.get(key)) > 0]


def _pct_change(values: list[float]) -> float:
    if len(values) < 2 or values[0] <= 0:
        return 0.0
    return (values[-1] - values[0]) / values[0] * 100.0


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent = deltas[-period:]
    gains = [max(delta, 0.0) for delta in recent]
    losses = [abs(min(delta, 0.0)) for delta in recent]
    avg_gain = sum(gains) / max(len(gains), 1)
    avg_loss = sum(losses) / max(len(losses), 1)
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _atr_pct(rows: list[dict[str, Any]], period: int = 14) -> float:
    if len(rows) < 2:
        return 0.0
    true_ranges: list[float] = []
    previous_close = _to_float(rows[0].get("close"))
    for row in rows[1:]:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close
    recent = true_ranges[-period:]
    last_close = _to_float(rows[-1].get("close"))
    if not recent or last_close <= 0:
        return 0.0
    return (sum(recent) / len(recent)) / last_close * 100.0


def _volume_zscore(rows: list[dict[str, Any]], lookback: int = 20) -> float:
    volumes = _series_from_bars(rows, "volume")[-lookback:]
    if len(volumes) < 3:
        return 0.0
    prior = volumes[:-1]
    mean = sum(prior) / len(prior)
    variance = sum((value - mean) ** 2 for value in prior) / len(prior)
    stdev = variance**0.5
    if stdev <= 0:
        return 0.0
    return (volumes[-1] - mean) / stdev


def _correlation(left: list[float], right: list[float]) -> float:
    length = min(len(left), len(right))
    if length < 3:
        return 0.0
    left = left[-length:]
    right = right[-length:]
    mean_left = sum(left) / length
    mean_right = sum(right) / length
    numerator = sum((left[index] - mean_left) * (right[index] - mean_right) for index in range(length))
    denom_left = sum((value - mean_left) ** 2 for value in left) ** 0.5
    denom_right = sum((value - mean_right) ** 2 for value in right) ** 0.5
    if denom_left <= 0 or denom_right <= 0:
        return 0.0
    return numerator / (denom_left * denom_right)


@dataclass(slots=True)
class CryptoQuantConfig:
    db_path: str = ".runtime/crypto_quant.db"
    enabled: bool = True
    context_limit: int = 16
    hot_window_rows: int = 360
    horizons: tuple[str, ...] = ("scalp", "intraday", "swing")


class CryptoQuantRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crypto_quant_signals (
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    long_score REAL NOT NULL DEFAULT 0,
                    short_score REAL NOT NULL DEFAULT 0,
                    no_trade_score REAL NOT NULL DEFAULT 0,
                    expected_r_long REAL NOT NULL DEFAULT 0,
                    expected_r_short REAL NOT NULL DEFAULT 0,
                    signal_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, horizon)
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_quant_signals_updated
                    ON crypto_quant_signals(updated_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_quant_signal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    long_score REAL NOT NULL DEFAULT 0,
                    short_score REAL NOT NULL DEFAULT 0,
                    no_trade_score REAL NOT NULL DEFAULT 0,
                    expected_r_long REAL NOT NULL DEFAULT 0,
                    expected_r_short REAL NOT NULL DEFAULT 0,
                    bias TEXT NOT NULL DEFAULT '',
                    signal_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_quant_signal_history_symbol_time
                    ON crypto_quant_signal_history(symbol, horizon, captured_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_quant_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    r_multiple REAL NOT NULL DEFAULT 0,
                    mfe_r REAL NOT NULL DEFAULT 0,
                    mae_r REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    labeled_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_quant_outcomes_symbol_time
                    ON crypto_quant_outcomes(symbol, labeled_at DESC);
                """
            )

    def save_signal(self, payload: dict[str, Any]) -> None:
        symbol = str(payload.get("symbol") or "").upper().strip()
        horizon = str(payload.get("horizon") or "intraday").lower().strip()
        if not symbol:
            raise ValueError("symbol is required")
        if not horizon:
            raise ValueError("horizon is required")

        signal = payload.get("signal_json")
        if not isinstance(signal, dict):
            signal = {}
        signal_json = json.dumps(signal, ensure_ascii=False, sort_keys=True)
        updated_at = str(payload.get("updated_at") or _utc_now())
        scores = {
            "long_score": _to_float(payload.get("long_score")),
            "short_score": _to_float(payload.get("short_score")),
            "no_trade_score": _to_float(payload.get("no_trade_score")),
            "expected_r_long": _to_float(payload.get("expected_r_long")),
            "expected_r_short": _to_float(payload.get("expected_r_short")),
        }

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_quant_signals (
                    symbol, horizon, long_score, short_score, no_trade_score,
                    expected_r_long, expected_r_short, signal_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, horizon) DO UPDATE SET
                    long_score = excluded.long_score,
                    short_score = excluded.short_score,
                    no_trade_score = excluded.no_trade_score,
                    expected_r_long = excluded.expected_r_long,
                    expected_r_short = excluded.expected_r_short,
                    signal_json = excluded.signal_json,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    horizon,
                    scores["long_score"],
                    scores["short_score"],
                    scores["no_trade_score"],
                    scores["expected_r_long"],
                    scores["expected_r_short"],
                    signal_json,
                    updated_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO crypto_quant_signal_history (
                    symbol, horizon, long_score, short_score, no_trade_score,
                    expected_r_long, expected_r_short, bias, signal_json, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    horizon,
                    scores["long_score"],
                    scores["short_score"],
                    scores["no_trade_score"],
                    scores["expected_r_long"],
                    scores["expected_r_short"],
                    str(signal.get("bias") or ""),
                    signal_json,
                    updated_at,
                ),
            )

    def latest_signals(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        clean_symbols = [str(symbol).upper().strip() for symbol in symbols or [] if str(symbol).strip()]
        limit = max(int(limit), 1)
        with self._connect() as conn:
            if clean_symbols:
                placeholders = ",".join("?" for _ in clean_symbols)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM crypto_quant_signals
                    WHERE symbol IN ({placeholders})
                    ORDER BY updated_at DESC, (long_score + short_score) DESC
                    LIMIT ?
                    """,
                    (*clean_symbols, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM crypto_quant_signals
                    ORDER BY updated_at DESC, (long_score + short_score) DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [self._signal_from_row(row) for row in rows]

    def latest_evidence(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        evidence = []
        for signal in self.latest_signals(symbols=symbols, limit=limit):
            signal_payload = signal.get("signal")
            if not isinstance(signal_payload, dict):
                signal_payload = {}
            bias = str(signal_payload.get("bias") or "").strip().lower()
            if not bias:
                long_score = float(signal.get("long_score") or 0.0)
                short_score = float(signal.get("short_score") or 0.0)
                no_trade_score = float(signal.get("no_trade_score") or 0.0)
                if no_trade_score >= max(long_score, short_score):
                    bias = "no_trade"
                elif long_score >= short_score:
                    bias = "long"
                else:
                    bias = "short"
            max_score = max(
                float(signal.get("long_score") or 0.0),
                float(signal.get("short_score") or 0.0),
                float(signal.get("no_trade_score") or 0.0),
            )
            evidence.append(
                evidence_from_signal(
                    source="crypto_quant",
                    signal_type="directional_quant",
                    symbol=signal.get("symbol"),
                    scope="binance",
                    confidence=max_score / 100.0,
                    ttl_sec=900,
                    captured_at=str(signal.get("updated_at") or _utc_now()),
                    payload={
                        "bias": bias,
                        "horizon": signal.get("horizon"),
                        "scores": {
                            "long": signal.get("long_score"),
                            "short": signal.get("short_score"),
                            "no_trade": signal.get("no_trade_score"),
                        },
                        "expected_r": {
                            "long": signal.get("expected_r_long"),
                            "short": signal.get("expected_r_short"),
                        },
                        "long_score": signal.get("long_score"),
                        "short_score": signal.get("short_score"),
                        "no_trade_score": signal.get("no_trade_score"),
                        "expected_r_long": signal.get("expected_r_long"),
                        "expected_r_short": signal.get("expected_r_short"),
                    },
                ).to_dict()
            )
        return evidence

    def save_outcome(self, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_quant_outcomes (
                    symbol, side, horizon, source_id, outcome,
                    r_multiple, mfe_r, mae_r, payload_json, labeled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("symbol") or "").upper(),
                    str(payload.get("side") or ""),
                    str(payload.get("horizon") or ""),
                    str(payload.get("source_id") or ""),
                    str(payload.get("outcome") or ""),
                    _to_float(payload.get("r_multiple")),
                    _to_float(payload.get("mfe_r")),
                    _to_float(payload.get("mae_r")),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    str(payload.get("labeled_at") or _utc_now()),
                ),
            )

    def prune_history(
        self,
        *,
        retention_days: int = 30,
        archive_retention_days: int = 0,
        hot_window_rows: int = 0,
        archive_window_rows: int = 0,
    ) -> dict[str, Any]:
        if int(retention_days) <= 0:
            return {"status": "skipped", "reason": "retention_disabled"}
        archive_floor_iso: str | None = None
        cold_retention: dict[str, Any] = {
            "status": "skipped",
            "reason": "cold_retention_disabled",
        }
        if int(archive_retention_days) > int(retention_days):
            archive_floor_iso = (
                datetime.now(timezone.utc) - timedelta(days=int(archive_retention_days))
            ).isoformat()
            cold_retention = SQLiteRetentionPruner(self.path).prune(
                [
                    RetentionRule(
                        table="crypto_quant_signal_history",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                    ),
                    RetentionRule(
                        table="crypto_quant_outcomes",
                        timestamp_column="labeled_at",
                        retention_days=int(archive_retention_days),
                    ),
                ]
            )
        retention = SQLiteRetentionPruner(self.path).prune(
            [
                RetentionRule(
                    table="crypto_quant_signal_history",
                    timestamp_column="captured_at",
                    retention_days=int(retention_days),
                    archive_table="crypto_quant_signal_history_archive",
                    archive_compress_columns=("signal_json",),
                    minimum_timestamp_value=archive_floor_iso,
                ),
                RetentionRule(
                    table="crypto_quant_outcomes",
                    timestamp_column="labeled_at",
                    retention_days=int(retention_days),
                    archive_table="crypto_quant_outcomes_archive",
                    archive_compress_columns=("payload_json",),
                    minimum_timestamp_value=archive_floor_iso,
                ),
            ]
        )
        archive_retention: dict[str, Any] = {
            "status": "skipped",
            "reason": "archive_retention_disabled",
        }
        if int(archive_retention_days) > 0:
            archive_retention = SQLiteRetentionPruner(self.path).prune(
                [
                    RetentionRule(
                        table="crypto_quant_signal_history_archive",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                        vacuum_after_delete=True,
                    ),
                    RetentionRule(
                        table="crypto_quant_outcomes_archive",
                        timestamp_column="labeled_at",
                        retention_days=int(archive_retention_days),
                    ),
                ]
            )
        deleted: dict[str, int] = {}
        archived: dict[str, int] = {}
        archive_deleted: dict[str, int] = {}
        cutoff = ""
        for table, row in dict(retention.get("tables") or {}).items():
            if not isinstance(row, dict) or row.get("status") != "ok":
                continue
            deleted[str(table)] = int(row.get("deleted") or 0)
            archived[str(table)] = int(row.get("archived") or 0)
            cutoff = cutoff or str(row.get("cutoff") or "")
        for table, row in dict(archive_retention.get("tables") or {}).items():
            if not isinstance(row, dict) or row.get("status") != "ok":
                continue
            archive_deleted[str(table)] = int(row.get("deleted") or 0)
        cold_deleted: dict[str, int] = {}
        for table, row in dict(cold_retention.get("tables") or {}).items():
            if not isinstance(row, dict) or row.get("status") != "ok":
                continue
            cold_deleted[str(table)] = int(row.get("deleted") or 0)
        history_window = self.prune_signal_history_windows(
            max_rows_per_group=int(hot_window_rows)
        )
        archive_window = self.prune_signal_history_archive_windows(
            max_rows_per_group=int(archive_window_rows)
        )
        return {
            "status": "ok",
            "cutoff": cutoff,
            "history_deleted": deleted.get("crypto_quant_signal_history", 0),
            "outcomes_deleted": deleted.get("crypto_quant_outcomes", 0),
            "archived": archived,
            "archive_deleted": archive_deleted,
            "cold_deleted": cold_deleted,
            "retention": retention,
            "archive_retention": archive_retention,
            "cold_retention": cold_retention,
            "history_window": history_window,
            "archive_window": archive_window,
        }

    def prune_signal_history_windows(self, *, max_rows_per_group: int) -> dict[str, Any]:
        return self._prune_signal_rows_by_group(
            table="crypto_quant_signal_history",
            max_rows_per_group=max_rows_per_group,
            disabled_reason="signal_history_hot_window_disabled",
        )

    def prune_signal_history_archive_windows(
        self,
        *,
        max_rows_per_group: int,
    ) -> dict[str, Any]:
        return self._prune_signal_rows_by_group(
            table="crypto_quant_signal_history_archive",
            max_rows_per_group=max_rows_per_group,
            disabled_reason="signal_history_archive_window_disabled",
        )

    def _prune_signal_rows_by_group(
        self,
        *,
        table: str,
        max_rows_per_group: int,
        disabled_reason: str,
    ) -> dict[str, Any]:
        limit = int(max_rows_per_group)
        if limit <= 0:
            return {
                "status": "skipped",
                "reason": disabled_reason,
                "limit": limit,
                "deleted": 0,
            }
        if table not in {
            "crypto_quant_signal_history",
            "crypto_quant_signal_history_archive",
        }:
            raise ValueError(f"unsupported signal history table: {table}")
        with self._connect() as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if table_exists is None:
                return {
                    "status": "skipped",
                    "reason": "table_missing",
                    "table": table,
                    "limit": limit,
                    "deleted": 0,
                }
            rows = conn.execute(
                f"""
                SELECT horizon, COUNT(*) AS count
                FROM {table}
                WHERE id IN (
                    SELECT id
                    FROM (
                        SELECT
                            id,
                            ROW_NUMBER() OVER (
                                PARTITION BY symbol, horizon
                                ORDER BY captured_at DESC, id DESC
                            ) AS row_number
                        FROM {table}
                    )
                    WHERE row_number > ?
                )
                GROUP BY horizon
                """,
                (limit,),
            ).fetchall()
            deleted_by_horizon = {
                str(row["horizon"]): int(row["count"] or 0) for row in rows
            }
            deleted = int(
                conn.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE id IN (
                        SELECT id
                        FROM (
                            SELECT
                                id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY symbol, horizon
                                    ORDER BY captured_at DESC, id DESC
                                ) AS row_number
                            FROM {table}
                        )
                        WHERE row_number > ?
                    )
                    """,
                    (limit,),
                ).rowcount
                or 0
            )
        return {
            "status": "ok",
            "table": table,
            "limit": limit,
            "deleted": deleted,
            "deleted_by_horizon": deleted_by_horizon,
        }

    def signal_history(
        self,
        *,
        symbol: str,
        horizon: str = "intraday",
        limit: int = 48,
    ) -> list[dict[str, Any]]:
        clean_symbol = str(symbol).upper().strip()
        clean_horizon = str(horizon).lower().strip()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM crypto_quant_signal_history
                WHERE symbol = ? AND horizon = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT ?
                """,
                (clean_symbol, clean_horizon, max(int(limit), 1)),
            ).fetchall()
        return [self._history_from_row(row) for row in rows]

    def retrieval_context(
        self,
        *,
        symbols: list[str],
        horizon: str = "intraday",
        points_per_symbol: int = 12,
    ) -> dict[str, Any]:
        clean_horizon = str(horizon).lower().strip()
        items: list[dict[str, Any]] = []
        for symbol in symbols:
            clean_symbol = str(symbol).upper().strip()
            if not clean_symbol:
                continue
            history = self.signal_history(
                symbol=clean_symbol,
                horizon=clean_horizon,
                limit=points_per_symbol,
            )
            if not history:
                continue
            latest = history[0]
            long_values = [float(row["long_score"]) for row in history]
            short_values = [float(row["short_score"]) for row in history]
            no_trade_values = [float(row["no_trade_score"]) for row in history]
            items.append(
                {
                    "symbol": clean_symbol,
                    "horizon": clean_horizon,
                    "latest": latest,
                    "history_points": len(history),
                    "trend": {
                        "long_score_delta": round(long_values[0] - long_values[-1], 3),
                        "short_score_delta": round(short_values[0] - short_values[-1], 3),
                        "no_trade_score_delta": round(no_trade_values[0] - no_trade_values[-1], 3),
                    },
                    "recent_biases": [str(row["bias"]) for row in history[:5]],
                }
            )
        return {
            "status": "ok",
            "horizon": clean_horizon,
            "items": items,
        }

    def _signal_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": str(row["symbol"]),
            "horizon": str(row["horizon"]),
            "long_score": float(row["long_score"]),
            "short_score": float(row["short_score"]),
            "no_trade_score": float(row["no_trade_score"]),
            "expected_r_long": float(row["expected_r_long"]),
            "expected_r_short": float(row["expected_r_short"]),
            "signal": _json_loads(str(row["signal_json"]), {}),
            "updated_at": str(row["updated_at"]),
        }

    def _history_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": str(row["symbol"]),
            "horizon": str(row["horizon"]),
            "long_score": float(row["long_score"]),
            "short_score": float(row["short_score"]),
            "no_trade_score": float(row["no_trade_score"]),
            "expected_r_long": float(row["expected_r_long"]),
            "expected_r_short": float(row["expected_r_short"]),
            "bias": str(row["bias"]),
            "signal": _json_loads(str(row["signal_json"]), {}),
            "captured_at": str(row["captured_at"]),
        }


class CryptoQuantEngine:
    def build_signal(
        self,
        *,
        symbol: str,
        horizon: str,
        klines_by_interval: dict[str, list[dict[str, Any]]],
        market_features: dict[str, Any],
        btc_closes: list[float] | None = None,
        eth_closes: list[float] | None = None,
    ) -> dict[str, Any]:
        primary_rows = (
            klines_by_interval.get("15m")
            or klines_by_interval.get("5m")
            or klines_by_interval.get("1h")
            or []
        )
        closes = _series_from_bars(primary_rows, "close")
        momentum_by_interval = {
            interval: _pct_change(_series_from_bars(rows, "close"))
            for interval, rows in klines_by_interval.items()
        }
        avg_momentum = sum(momentum_by_interval.values()) / max(len(momentum_by_interval), 1)
        ema_fast = _ema(closes[-21:], 8)
        ema_slow = _ema(closes[-34:], 21)
        ema_slope_pct = ((ema_fast - ema_slow) / ema_slow * 100.0) if ema_slow > 0 else 0.0
        rsi = _rsi(closes)
        atr_pct = _atr_pct(primary_rows)
        volume_z = _volume_zscore(primary_rows)
        spread_bps = _to_float(market_features.get("spread_bps"))
        funding = _to_float(market_features.get("funding_rate"))
        basis = _to_float(market_features.get("mark_index_basis_pct"))
        open_interest = _to_float(market_features.get("open_interest"))
        alignment = str(market_features.get("timeframe_alignment") or "").lower()
        btc_corr = _correlation(closes, btc_closes or [])
        eth_corr = _correlation(closes, eth_closes or [])

        long_score = 50.0
        short_score = 50.0
        no_trade_score = 20.0
        drivers: list[str] = []
        risks: list[str] = []

        if avg_momentum > 0.35:
            long_score += min(avg_momentum * 4.0, 25.0)
            short_score -= min(avg_momentum * 2.5, 20.0)
            drivers.append("multi-timeframe momentum is positive")
        elif avg_momentum < -0.35:
            short_score += min(abs(avg_momentum) * 4.0, 25.0)
            long_score -= min(abs(avg_momentum) * 2.5, 20.0)
            drivers.append("multi-timeframe momentum is negative")

        if ema_slope_pct > 0.1:
            long_score += 10.0
            drivers.append("fast EMA is above slow EMA")
        elif ema_slope_pct < -0.1:
            short_score += 10.0
            drivers.append("fast EMA is below slow EMA")

        if alignment == "bullish":
            long_score += 5.0
            drivers.append("timeframe alignment is bullish")
        elif alignment == "bearish":
            short_score += 5.0
            drivers.append("timeframe alignment is bearish")

        if volume_z >= 1.0:
            if avg_momentum >= 0:
                long_score += 5.0
            else:
                short_score += 5.0
            drivers.append("volume expansion confirms direction")

        if rsi >= 78.0:
            no_trade_score += 20.0
            short_score += 4.0
            risks.append("upside is extended by RSI")
        elif rsi <= 22.0:
            no_trade_score += 20.0
            long_score += 4.0
            risks.append("downside is extended by RSI")

        if spread_bps >= 8.0:
            no_trade_score += min(spread_bps, 30.0)
            risks.append("spread is expensive")
        if abs(funding) >= 0.0005:
            no_trade_score += 10.0
            risks.append("funding is crowded")
        if abs(basis) >= 0.15:
            no_trade_score += 10.0
            risks.append("mark/index basis is stretched")
        if atr_pct >= 8.0:
            no_trade_score += 10.0
            risks.append("intraday volatility is elevated")

        long_score = _clamp(long_score)
        short_score = _clamp(short_score)
        no_trade_score = _clamp(no_trade_score)
        if no_trade_score >= 60.0 or max(long_score, short_score) < 55.0:
            bias = "no_trade"
        elif long_score >= short_score:
            bias = "long"
        else:
            bias = "short"

        expected_r_long = (long_score - no_trade_score) / 100.0
        expected_r_short = (short_score - no_trade_score) / 100.0
        return {
            "symbol": str(symbol).upper(),
            "horizon": str(horizon),
            "bias": bias,
            "long_score": round(long_score, 2),
            "short_score": round(short_score, 2),
            "no_trade_score": round(no_trade_score, 2),
            "expected_r_long": round(expected_r_long, 3),
            "expected_r_short": round(expected_r_short, 3),
            "drivers": drivers,
            "risks": risks,
            "metrics": {
                "avg_momentum_pct": round(avg_momentum, 4),
                "ema_slope_pct": round(ema_slope_pct, 4),
                "rsi": round(rsi, 2),
                "atr_pct": round(atr_pct, 4),
                "volume_z": round(volume_z, 4),
                "spread_bps": round(spread_bps, 4),
                "funding_rate": round(funding, 8),
                "mark_index_basis_pct": round(basis, 4),
                "open_interest": round(open_interest, 4),
                "btc_corr": round(btc_corr, 4),
                "eth_corr": round(eth_corr, 4),
            },
        }


class CryptoQuantOutcomeLabeler:
    def label_path(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        rows: list[dict[str, Any]],
        horizon: str,
    ) -> dict[str, Any]:
        entry = _to_float(entry_price)
        stop = _to_float(stop_price)
        target = _to_float(target_price)
        normalized_side = str(side or "long").strip().lower()
        normalized_side = "short" if normalized_side in {"short", "sell"} else "long"
        risk = abs(entry - stop)
        if entry <= 0 or stop <= 0 or target <= 0 or risk <= 0:
            return {
                "symbol": str(symbol).upper(),
                "side": normalized_side,
                "horizon": horizon,
                "outcome": "invalid",
                "r_multiple": 0.0,
                "mfe_r": 0.0,
                "mae_r": 0.0,
            }

        outcome = "open_at_horizon"
        exit_price = _to_float(rows[-1].get("close")) if rows else entry
        mfe_r = 0.0
        mae_r = 0.0
        for row in rows:
            high = _to_float(row.get("high"))
            low = _to_float(row.get("low"))
            if normalized_side == "short":
                mfe_r = max(mfe_r, (entry - low) / risk)
                mae_r = min(mae_r, (entry - high) / risk)
                if high >= stop:
                    outcome = "stop_first"
                    exit_price = stop
                    break
                if low <= target:
                    outcome = "target_first"
                    exit_price = target
                    break
            else:
                mfe_r = max(mfe_r, (high - entry) / risk)
                mae_r = min(mae_r, (low - entry) / risk)
                if low <= stop:
                    outcome = "stop_first"
                    exit_price = stop
                    break
                if high >= target:
                    outcome = "target_first"
                    exit_price = target
                    break

        if outcome == "target_first":
            r_multiple = abs(target - entry) / risk
        elif outcome == "stop_first":
            r_multiple = -1.0
        elif normalized_side == "short":
            r_multiple = (entry - exit_price) / risk
        else:
            r_multiple = (exit_price - entry) / risk
        return {
            "symbol": str(symbol).upper(),
            "side": normalized_side,
            "horizon": horizon,
            "outcome": outcome,
            "exit_price": exit_price,
            "r_multiple": round(r_multiple, 4),
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
        }
