from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketBarV1:
    venue: str
    symbol: str
    interval: str
    open_time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketBarRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_bars (
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    open_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    source_id TEXT NOT NULL,
                    PRIMARY KEY (venue, symbol, interval, open_time)
                )
                """
            )

    def save_bars(
        self,
        *,
        venue: str,
        symbol: str,
        interval: str,
        rows: list[dict[str, Any]],
        source: str,
    ) -> int:
        venue_key = str(venue).strip().lower()
        symbol_key = str(symbol).strip().upper()
        interval_key = str(interval).strip().lower()
        source_key = str(source).strip()
        values = []
        for row in rows:
            open_time = str(row.get("open_time") or "").strip()
            if not open_time:
                continue
            open_price = float(row.get("open") or 0.0)
            high_price = float(row.get("high") or 0.0)
            low_price = float(row.get("low") or 0.0)
            close_price = float(row.get("close") or 0.0)
            if (
                min(open_price, high_price, low_price, close_price) <= 0
                or high_price < low_price
                or high_price < max(open_price, close_price)
                or low_price > min(open_price, close_price)
            ):
                raise ValueError(
                    f"invalid market bar: {symbol_key}:{interval_key}:{open_time}"
                )
            values.append(
                (
                    venue_key,
                    symbol_key,
                    interval_key,
                    open_time,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    float(row.get("volume") or 0.0),
                    f"{source_key}:{symbol_key}:{interval_key}:{open_time}",
                )
            )
        if not values:
            return 0
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO market_bars (
                    venue, symbol, interval, open_time, open, high, low, close,
                    volume, source_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue, symbol, interval, open_time) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    source_id = excluded.source_id
                """,
                values,
            )
        return len(values)

    def list_bars(
        self,
        *,
        venue: str,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        max_rows = max(int(limit), 1)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    venue, symbol, interval, open_time, open, high, low, close,
                    volume, source_id
                FROM market_bars
                WHERE venue = ? AND symbol = ? AND interval = ?
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (
                    str(venue).strip().lower(),
                    str(symbol).strip().upper(),
                    str(interval).strip().lower(),
                    max_rows,
                ),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
