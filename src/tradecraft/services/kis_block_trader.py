from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from tradecraft.services.kis import KISAdapter
from tradecraft.services.krx_holiday import KRXHolidayCalendar
from tradecraft.services.llm_bridge import LLMBridge
from tradecraft.services.market_judgment import (
    MarketQuoteService,
    build_market_clock,
    normalize_account_assets,
)

BLOCK_STATUSES = {
    "proposed",
    "entry_pending",
    "open",
    "exit_pending",
    "closed",
    "paused",
    "error",
}
ACTIVE_BLOCK_STATUSES = {"entry_pending", "open", "exit_pending"}


class StrategyEngine(Protocol):
    def build_candidates(
        self,
        *,
        query: str,
        research_feed: dict[str, Any] | None,
        limit: int | None = None,
    ) -> dict[str, Any]: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_int(value: Any) -> int:
    return int(math.floor(_safe_float(value)))


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def krx_tick_size(price: float) -> int:
    value = max(float(price), 0.0)
    if value < 1_000:
        return 1
    if value < 5_000:
        return 5
    if value < 10_000:
        return 10
    if value < 50_000:
        return 50
    if value < 100_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def aggressive_limit_price(price: float, *, side: str, bps: float = 30.0) -> int:
    base = max(float(price), 0.0)
    if base <= 0:
        return 0
    ratio = max(float(bps), 0.0) / 10_000.0
    raw = base * (1 + ratio) if str(side).lower() == "buy" else base * (1 - ratio)
    tick = krx_tick_size(raw)
    if str(side).lower() == "buy":
        return int(math.ceil(raw / tick) * tick)
    return int(max(math.floor(raw / tick) * tick, tick))


@dataclass(slots=True)
class KISBlockTraderConfig:
    db_path: str = ".runtime/kis_blocks.db"
    state_path: str = ".runtime/kis_block_trader.json"
    enabled: bool = False
    execute_orders: bool = False
    rule_interval_sec: int = 5
    manager_interval_sec: int = 1800
    quote_concurrency: int = 4
    use_naver_fallback: bool = True
    request_timeout_sec: float = 8.0
    aggressive_limit_bps: float = 30.0
    pending_reconcile_timeout_sec: int = 300
    max_manager_symbols: int = 12
    manager_query: str = "국장1 계좌와 전략 지식을 바탕으로 블록 매매 계획을 관리해줘"


class KISBlockRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS blocks (
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
                CREATE INDEX IF NOT EXISTS idx_blocks_status_symbol
                    ON blocks(status, symbol);

                CREATE TABLE IF NOT EXISTS block_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    block_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_block_events_block
                    ON block_events(block_id, id DESC);

                CREATE TABLE IF NOT EXISTS block_orders (
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
                    last_checked_at TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_order_no TEXT NOT NULL DEFAULT '',
                    cancel_response_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_block_orders_block
                    ON block_orders(block_id, id DESC);

                CREATE TABLE IF NOT EXISTS manager_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    market_session TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    actions_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS quote_snapshots (
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
                CREATE INDEX IF NOT EXISTS idx_kis_block_quotes_symbol
                    ON quote_snapshots(symbol, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS reconciliation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    account_json TEXT NOT NULL DEFAULT '{}',
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                """
            )
            for column, definition in {
                "order_orgno": "TEXT NOT NULL DEFAULT ''",
                "filled_qty": "INTEGER NOT NULL DEFAULT 0",
                "remaining_qty": "INTEGER NOT NULL DEFAULT 0",
                "avg_fill_price": "REAL",
                "last_checked_at": "TEXT NOT NULL DEFAULT ''",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "cancel_order_no": "TEXT NOT NULL DEFAULT ''",
                "cancel_response_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                self._ensure_column(conn, "block_orders", column, definition)

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column in {str(row[1]) for row in rows}:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_block(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        block_id = str(payload.get("block_id") or self._new_block_id(payload)).strip()
        status = str(payload.get("status") or "proposed")
        if status not in BLOCK_STATUSES:
            status = "proposed"
        row = {
            "block_id": block_id,
            "symbol": str(payload.get("symbol") or ""),
            "name": str(payload.get("name") or payload.get("symbol") or ""),
            "qty_initial": max(_safe_int(payload.get("qty_initial") or payload.get("qty")), 1),
            "qty_open": max(_safe_int(payload.get("qty_open")), 0),
            "entry_price": _safe_float(payload.get("entry_price")) or None,
            "target_price": _safe_float(payload.get("target_price")) or None,
            "stop_price": _safe_float(payload.get("stop_price")) or None,
            "thesis": _clean_text(payload.get("thesis"), limit=2000),
            "llm_reason": _clean_text(payload.get("llm_reason") or payload.get("reason"), limit=2000),
            "risk_note": _clean_text(payload.get("risk_note"), limit=2000),
            "created_by": str(payload.get("created_by") or "llm"),
            "manager_run_id": payload.get("manager_run_id"),
            "status": status,
            "force_exit_requested": 1 if payload.get("force_exit_requested") else 0,
            "metadata_json": _json_dumps(payload.get("metadata") or {}),
            "created_at": now,
            "updated_at": now,
            "opened_at": str(payload.get("opened_at") or ""),
            "closed_at": str(payload.get("closed_at") or ""),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO blocks (
                    block_id, symbol, name, qty_initial, qty_open, entry_price,
                    target_price, stop_price, thesis, llm_reason, risk_note,
                    created_by, manager_run_id, status, force_exit_requested,
                    metadata_json, created_at, updated_at, opened_at, closed_at
                )
                VALUES (
                    :block_id, :symbol, :name, :qty_initial, :qty_open, :entry_price,
                    :target_price, :stop_price, :thesis, :llm_reason, :risk_note,
                    :created_by, :manager_run_id, :status, :force_exit_requested,
                    :metadata_json, :created_at, :updated_at, :opened_at, :closed_at
                )
                """,
                row,
            )
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    block_id,
                    "created",
                    f"block created: {row['symbol']} x{row['qty_initial']}",
                    _json_dumps(row),
                    now,
                ),
            )
        return self.get_block(block_id) or row

    def update_block(self, block_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "name",
            "qty_open",
            "entry_price",
            "target_price",
            "stop_price",
            "thesis",
            "llm_reason",
            "risk_note",
            "status",
            "force_exit_requested",
            "opened_at",
            "closed_at",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "status" and str(value) not in BLOCK_STATUSES:
                continue
            updates[key] = value
        if not updates:
            return self.get_block(block_id)
        updates["updated_at"] = utc_now_iso()
        set_clause = ", ".join(f"{key} = :{key}" for key in updates)
        updates["block_id"] = block_id
        with self._connect() as conn:
            conn.execute(f"UPDATE blocks SET {set_clause} WHERE block_id = :block_id", updates)
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (block_id, "updated", "block updated", _json_dumps(fields), utc_now_iso()),
            )
        return self.get_block(block_id)

    def add_event(
        self,
        block_id: str,
        event_type: str,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(block_id),
                    str(event_type),
                    str(message or ""),
                    _json_dumps(payload or {}),
                    utc_now_iso(),
                ),
            )

    def add_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        row = {
            "block_id": str(payload.get("block_id") or ""),
            "symbol": str(payload.get("symbol") or ""),
            "side": str(payload.get("side") or ""),
            "qty": max(_safe_int(payload.get("qty")), 0),
            "limit_price": max(_safe_int(payload.get("limit_price")), 0),
            "order_type": str(payload.get("order_type") or "00"),
            "status": str(payload.get("status") or "planned"),
            "order_no": str(payload.get("order_no") or ""),
            "order_orgno": str(payload.get("order_orgno") or ""),
            "reason": str(payload.get("reason") or ""),
            "filled_qty": max(_safe_int(payload.get("filled_qty")), 0),
            "remaining_qty": max(_safe_int(payload.get("remaining_qty")), 0),
            "avg_fill_price": _safe_float(payload.get("avg_fill_price")) or None,
            "last_checked_at": str(payload.get("last_checked_at") or ""),
            "cancel_requested": 1 if payload.get("cancel_requested") else 0,
            "cancel_order_no": str(payload.get("cancel_order_no") or ""),
            "cancel_response_json": _json_dumps(payload.get("cancel_response") or {}),
            "response_json": _json_dumps(payload.get("response") or {}),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO block_orders (
                    block_id, symbol, side, qty, limit_price, order_type, status,
                    order_no, order_orgno, reason, filled_qty, remaining_qty,
                    avg_fill_price, last_checked_at, cancel_requested,
                    cancel_order_no, cancel_response_json, response_json,
                    created_at, updated_at
                )
                VALUES (
                    :block_id, :symbol, :side, :qty, :limit_price, :order_type, :status,
                    :order_no, :order_orgno, :reason, :filled_qty, :remaining_qty,
                    :avg_fill_price, :last_checked_at, :cancel_requested,
                    :cancel_order_no, :cancel_response_json, :response_json,
                    :created_at, :updated_at
                )
                """,
                row,
            )
            order_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO block_events (block_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["block_id"],
                    "order",
                    f"{row['side']} {row['qty']} @ {row['limit_price']} {row['status']}",
                    _json_dumps({"order_id": order_id, **row}),
                    now,
                ),
            )
        return {"id": order_id, **row}

    def save_manager_run(
        self,
        *,
        run: dict[str, Any],
        actions: dict[str, Any],
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO manager_runs (
                    run_at, market_session, status, mode, model, error_message,
                    prompt_json, response_json, actions_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.get("run_at") or utc_now_iso()),
                    str(run.get("market_session") or "closed"),
                    str(run.get("status") or "ok"),
                    str(run.get("mode") or "llm"),
                    str(run.get("model") or ""),
                    str(run.get("error_message") or ""),
                    _json_dumps(run.get("prompt") or {}),
                    _json_dumps(run.get("response") or {}),
                    _json_dumps(actions),
                ),
            )
            return int(cursor.lastrowid)

    def save_quotes(self, quotes: list[dict[str, Any]]) -> None:
        if not quotes:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO quote_snapshots (
                    symbol, name, price, source, fetched_at, status, error_message, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row.get("symbol") or ""),
                        str(row.get("name") or row.get("symbol") or ""),
                        row.get("price"),
                        str(row.get("source") or ""),
                        str(row.get("fetched_at") or utc_now_iso()),
                        str(row.get("status") or "ok"),
                        str(row.get("error_message") or ""),
                        _json_dumps(row.get("raw") or {}),
                    )
                    for row in quotes
                ],
            )

    def save_reconciliation(self, account: dict[str, Any], summary: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reconciliation_runs (run_at, status, account_json, summary_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    str(summary.get("status") or "ok"),
                    _json_dumps(account),
                    _json_dumps(summary),
                ),
            )

    def list_blocks(self, *, include_closed: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM blocks"
        params: tuple[Any, ...] = ()
        if not include_closed:
            query += " WHERE status NOT IN ('closed')"
        query += " ORDER BY created_at DESC, block_id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_block(row) for row in rows]

    def get_block(self, block_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM blocks WHERE block_id = ? LIMIT 1",
                (str(block_id),),
            ).fetchone()
        return self._row_to_block(row) if row else None

    def list_orders(self, block_id: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        params: tuple[Any, ...]
        if block_id:
            query = "SELECT * FROM block_orders WHERE block_id = ? ORDER BY id DESC LIMIT ?"
            params = (block_id, max(int(limit), 1))
        else:
            query = "SELECT * FROM block_orders ORDER BY id DESC LIMIT ?"
            params = (max(int(limit), 1),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_order(row) for row in rows]

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM block_orders WHERE id = ? LIMIT 1",
                (int(order_id),),
            ).fetchone()
        return self._row_to_order(row) if row else None

    def list_pending_orders(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM block_orders
                WHERE status IN ('sent','partially_filled','cancel_requested')
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_order(row) for row in rows]

    def update_order(self, order_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "status",
            "order_no",
            "order_orgno",
            "reason",
            "filled_qty",
            "remaining_qty",
            "avg_fill_price",
            "last_checked_at",
            "cancel_requested",
            "cancel_order_no",
            "cancel_response_json",
            "response_json",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"cancel_response_json", "response_json"} and not isinstance(value, str):
                updates[key] = _json_dumps(value)
            elif key == "cancel_requested":
                updates[key] = 1 if value else 0
            else:
                updates[key] = value
        if not updates:
            return self.get_order(order_id)
        updates["updated_at"] = utc_now_iso()
        updates["id"] = int(order_id)
        set_clause = ", ".join(f"{key} = :{key}" for key in updates)
        with self._connect() as conn:
            conn.execute(f"UPDATE block_orders SET {set_clause} WHERE id = :id", updates)
        return self.get_order(order_id)

    def list_events(self, block_id: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        params: tuple[Any, ...]
        if block_id:
            query = "SELECT * FROM block_events WHERE block_id = ? ORDER BY id DESC LIMIT ?"
            params = (block_id, max(int(limit), 1))
        else:
            query = "SELECT * FROM block_events ORDER BY id DESC LIMIT ?"
            params = (max(int(limit), 1),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def latest_manager_run(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM manager_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {"status": "missing"}
        return self._row_to_manager_run(row)

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM system_state WHERE key = ? LIMIT 1",
                (str(key),),
            ).fetchone()
        if row is None:
            return default
        return _json_loads(row["value_json"], default)

    def set_state(self, key: str, value: Any) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO system_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (str(key), _json_dumps(value), now),
            )

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            block_count = int(conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0])
            open_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM blocks WHERE status IN ('entry_pending','open','exit_pending')"
                ).fetchone()[0]
            )
            order_count = int(conn.execute("SELECT COUNT(*) FROM block_orders").fetchone()[0])
            pending_order_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM block_orders
                    WHERE status IN ('sent','partially_filled','cancel_requested')
                    """
                ).fetchone()[0]
            )
            manager_count = int(conn.execute("SELECT COUNT(*) FROM manager_runs").fetchone()[0])
            latest_run = conn.execute(
                "SELECT run_at, status, mode FROM manager_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
        kill = self.get_state("kill_switch", {"enabled": False})
        return {
            "status": "ok",
            "db_path": str(self.path),
            "block_count": block_count,
            "open_block_count": open_count,
            "order_count": order_count,
            "pending_order_count": pending_order_count,
            "manager_run_count": manager_count,
            "latest_manager_run_at": str(latest_run["run_at"]) if latest_run else "",
            "latest_manager_status": str(latest_run["status"]) if latest_run else "missing",
            "latest_manager_mode": str(latest_run["mode"]) if latest_run else "",
            "kill_switch": kill if isinstance(kill, dict) else {"enabled": False},
        }

    def _new_block_id(self, payload: dict[str, Any]) -> str:
        symbol = str(payload.get("symbol") or "000000")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"blk_{symbol}_{stamp}"

    @staticmethod
    def _row_to_block(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "block_id": row["block_id"],
            "symbol": row["symbol"],
            "name": row["name"],
            "qty_initial": int(row["qty_initial"] or 0),
            "qty_open": int(row["qty_open"] or 0),
            "entry_price": row["entry_price"],
            "target_price": row["target_price"],
            "stop_price": row["stop_price"],
            "thesis": row["thesis"],
            "llm_reason": row["llm_reason"],
            "risk_note": row["risk_note"],
            "created_by": row["created_by"],
            "manager_run_id": row["manager_run_id"],
            "status": row["status"],
            "force_exit_requested": bool(row["force_exit_requested"]),
            "metadata": _json_loads(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "opened_at": row["opened_at"],
            "closed_at": row["closed_at"],
        }

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "block_id": row["block_id"],
            "symbol": row["symbol"],
            "side": row["side"],
            "qty": int(row["qty"] or 0),
            "limit_price": int(row["limit_price"] or 0),
            "order_type": row["order_type"],
            "status": row["status"],
            "order_no": row["order_no"],
            "order_orgno": row["order_orgno"],
            "reason": row["reason"],
            "filled_qty": int(row["filled_qty"] or 0),
            "remaining_qty": int(row["remaining_qty"] or 0),
            "avg_fill_price": row["avg_fill_price"],
            "last_checked_at": row["last_checked_at"],
            "cancel_requested": bool(row["cancel_requested"]),
            "cancel_order_no": row["cancel_order_no"],
            "cancel_response": _json_loads(row["cancel_response_json"], {}),
            "response": _json_loads(row["response_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "block_id": row["block_id"],
            "event_type": row["event_type"],
            "message": row["message"],
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_manager_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_at": row["run_at"],
            "market_session": row["market_session"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "error_message": row["error_message"],
            "prompt": _json_loads(row["prompt_json"], {}),
            "response": _json_loads(row["response_json"], {}),
            "actions": _json_loads(row["actions_json"], {}),
        }


class KISBlockTrader:
    def __init__(
        self,
        *,
        config: KISBlockTraderConfig,
        kis: KISAdapter,
        llm_bridge: LLMBridge,
        strategy_engine: StrategyEngine | None = None,
        market_judgment_provider: Any | None = None,
        research_feed_provider: Any | None = None,
        memory_context_provider: Callable[..., dict[str, Any] | None] | None = None,
        calendar: KRXHolidayCalendar | None = None,
    ) -> None:
        self.config = config
        self.kis = kis
        self.llm_bridge = llm_bridge
        self.strategy_engine = strategy_engine
        self.market_judgment_provider = market_judgment_provider
        self.research_feed_provider = research_feed_provider
        self.memory_context_provider = memory_context_provider
        self.calendar = calendar or KRXHolidayCalendar()
        self.repository = KISBlockRepository(config.db_path)
        self.quote_service = MarketQuoteService(
            kis,
            use_naver_fallback=config.use_naver_fallback,
            timeout_sec=config.request_timeout_sec,
        )

    def clock(self, *, now: datetime | None = None) -> dict[str, Any]:
        return build_market_clock(now=now, calendar=self.calendar)

    def kill_switch(self) -> dict[str, Any]:
        payload = self.repository.get_state("kill_switch", {"enabled": False})
        return payload if isinstance(payload, dict) else {"enabled": False}

    def set_kill_switch(self, enabled: bool, *, reason: str = "") -> dict[str, Any]:
        payload = {
            "enabled": bool(enabled),
            "reason": str(reason or ""),
            "updated_at": utc_now_iso(),
        }
        self.repository.set_state("kill_switch", payload)
        return payload

    def status(self) -> dict[str, Any]:
        clock = self.clock()
        return {
            **self.repository.status(),
            "enabled": bool(self.config.enabled),
            "execution_mode": "live" if self.config.execute_orders else "paper",
            "execute_orders": bool(self.config.execute_orders),
            "clock": clock,
            "kis_ready": bool(getattr(getattr(self.kis, "config", None), "ready", False)),
            "llm_ready": bool(getattr(self.llm_bridge, "ready", False)),
            "model": str(getattr(self.llm_bridge, "resolved_model", "")),
            "config": {
                "rule_interval_sec": int(self.config.rule_interval_sec),
                "manager_interval_sec": int(self.config.manager_interval_sec),
                "aggressive_limit_bps": float(self.config.aggressive_limit_bps),
                "pending_reconcile_timeout_sec": int(
                    self.config.pending_reconcile_timeout_sec
                ),
                "max_manager_symbols": int(self.config.max_manager_symbols),
            },
        }

    async def collect_account(self) -> dict[str, Any]:
        try:
            assets = await self.kis.fetch_balance_assets()
            return normalize_account_assets(assets)
        except Exception as exc:
            return {
                "status": "error",
                "captured_at": utc_now_iso(),
                "account_label": "국장1",
                "cash_krw": 0.0,
                "position_value_krw": 0.0,
                "total_value_krw": 0.0,
                "position_count": 0,
                "positions": [],
                "error_message": str(exc),
            }

    async def snapshot(self) -> dict[str, Any]:
        account = await self.collect_account()
        blocks = self.repository.list_blocks(include_closed=True)
        symbols = self._symbols_for_quotes(blocks, account)
        quotes = await self.quote_service.collect_quotes(
            symbols,
            concurrency=self.config.quote_concurrency,
        )
        self.repository.save_quotes(quotes)
        quote_map = {str(row.get("symbol") or ""): row for row in quotes}
        allocation = self._allocation_summary(account=account, blocks=blocks, quotes=quote_map)
        return {
            "status": "ok",
            "updated_at": utc_now_iso(),
            "summary": self.status(),
            "account": account,
            "blocks": [self._decorate_block(row, quote_map) for row in blocks],
            "allocation": allocation,
            "orders": self.repository.list_orders(limit=50),
            "events": self.repository.list_events(limit=80),
            "latest_manager_run": self.repository.latest_manager_run(),
        }

    async def run_manager_once(self) -> dict[str, Any]:
        run_at = utc_now_iso()
        clock = self.clock()
        account = await self.collect_account()
        blocks = self.repository.list_blocks(include_closed=False)
        symbols = self._manager_symbols(account=account, blocks=blocks)
        quotes = await self.quote_service.collect_quotes(
            symbols,
            concurrency=self.config.quote_concurrency,
        )
        self.repository.save_quotes(quotes)
        quote_map = {str(row.get("symbol") or ""): row for row in quotes}
        strategy_payload = self._strategy_payload()
        latest_judgment = self._latest_market_judgment()
        allocation = self._allocation_summary(
            account=account,
            blocks=blocks,
            quotes=quote_map,
        )
        memory_context = self._investment_memory_context(
            symbols=symbols,
            block_ids=[
                str(row.get("block_id") or "")
                for row in blocks
                if str(row.get("block_id") or "")
            ],
        )
        prompt = {
            "task": "Manage independent KIS stock trading blocks. Return JSON only.",
            "policy": {
                "block_unit": "Each block is independent even when symbols overlap.",
                "llm_permissions": "full_block_management",
                "execution_guard": "orders are validated by a rule/gate layer",
                "memory_guard": "investment memory is advisory; hard safety gates override it",
                "existing_position_adoption": (
                    "Use adopt_existing_blocks to assign unallocated account positions "
                    "to block ledger entries without sending buy orders."
                ),
                "allowed_actions": [
                    "adopt_existing_blocks",
                    "create_blocks",
                    "update_blocks",
                    "close_blocks",
                    "pause_blocks",
                ],
            },
            "clock": clock,
            "account": account,
            "blocks": blocks,
            "quotes": quotes,
            "allocation": allocation,
            "strategy": strategy_payload,
            "market_judgment": latest_judgment,
            "investment_memory": memory_context,
            "output_schema": {
                "adopt_existing_blocks": [
                    {
                        "symbol": "6-digit existing holding",
                        "qty": "integer <= unallocated account quantity",
                        "target_price": "number",
                        "stop_price": "number",
                        "thesis": "string",
                        "confidence": "0.0-1.0",
                        "risk_note": "string",
                    }
                ],
                "create_blocks": [
                    {
                        "symbol": "6-digit",
                        "qty": "integer",
                        "target_price": "number",
                        "stop_price": "number",
                        "entry_style": "aggressive_limit",
                        "thesis": "string",
                        "confidence": "0.0-1.0",
                        "risk_note": "string",
                    }
                ],
                "update_blocks": [
                    {
                        "block_id": "string",
                        "target_price": "number",
                        "stop_price": "number",
                        "reason": "string",
                    }
                ],
                "close_blocks": [{"block_id": "string", "reason": "string"}],
                "pause_blocks": [{"block_id": "string", "reason": "string"}],
            },
        }
        parsed, error_message = await self._complete_manager(prompt)
        actions = self._sanitize_actions(
            parsed,
            blocks=blocks,
            quotes=quote_map,
            account=account,
        )
        status = "ok" if not error_message else "llm_unavailable"
        mode = "llm" if not error_message else "deterministic"
        manager_run_id = self.repository.save_manager_run(
            run={
                "run_at": run_at,
                "market_session": str(clock.get("session") or "closed"),
                "status": status,
                "mode": mode,
                "model": str(getattr(self.llm_bridge, "resolved_model", "")),
                "error_message": error_message,
                "prompt": prompt,
                "response": parsed or {},
            },
            actions=actions,
        )
        applied = await self._apply_manager_actions(
            actions,
            manager_run_id=manager_run_id,
            account=account,
            quote_map=quote_map,
            clock=clock,
        )
        return {
            "status": status,
            "run_id": manager_run_id,
            "run_at": run_at,
            "mode": mode,
            "error_message": error_message,
            "actions": actions,
            "applied": applied,
        }

    async def run_adoption_once(self) -> dict[str, Any]:
        run_at = utc_now_iso()
        clock = self.clock()
        account = await self.collect_account()
        blocks = self.repository.list_blocks(include_closed=False)
        symbols = self._symbols_for_quotes(blocks, account)
        quotes = await self.quote_service.collect_quotes(
            symbols,
            concurrency=self.config.quote_concurrency,
        )
        self.repository.save_quotes(quotes)
        quote_map = {str(row.get("symbol") or ""): row for row in quotes}
        allocation = self._allocation_summary(
            account=account,
            blocks=blocks,
            quotes=quote_map,
        )
        prompt = {
            "task": "Adopt unallocated existing KIS holdings into independent trading blocks. Return JSON only.",
            "persona": "쥬는 기존 보유분을 새 매수 주문 없이 블록 원장에 배정한다.",
            "policy": {
                "adoption_only": True,
                "no_buy_orders": True,
                "block_unit": "Existing same-symbol holdings may be split into multiple independent blocks.",
                "execution_guard": "adoption writes ledger blocks only; rule executor handles later exits",
            },
            "clock": clock,
            "account": account,
            "blocks": blocks,
            "quotes": quotes,
            "allocation": allocation,
            "strategy": self._strategy_payload(),
            "market_judgment": self._latest_market_judgment(),
            "investment_memory": self._investment_memory_context(
                symbols=symbols,
                block_ids=[
                    str(row.get("block_id") or "")
                    for row in blocks
                    if str(row.get("block_id") or "")
                ],
            ),
            "output_schema": {
                "adopt_existing_blocks": [
                    {
                        "symbol": "6-digit existing holding",
                        "qty": "integer <= unallocated account quantity",
                        "target_price": "number",
                        "stop_price": "number",
                        "thesis": "string",
                        "confidence": "0.0-1.0",
                        "risk_note": "string",
                    }
                ]
            },
        }
        parsed, error_message = await self._complete_manager(prompt)
        sanitized = self._sanitize_actions(
            parsed,
            blocks=blocks,
            quotes=quote_map,
            account=account,
        )
        actions = {"adopt_existing_blocks": sanitized.get("adopt_existing_blocks", [])}
        status = "ok" if not error_message else "llm_unavailable"
        mode = "adoption_llm" if not error_message else "deterministic"
        manager_run_id = self.repository.save_manager_run(
            run={
                "run_at": run_at,
                "market_session": str(clock.get("session") or "closed"),
                "status": status,
                "mode": mode,
                "model": str(getattr(self.llm_bridge, "resolved_model", "")),
                "error_message": error_message,
                "prompt": prompt,
                "response": parsed or {},
            },
            actions=actions,
        )
        applied = await self._apply_manager_actions(
            actions,
            manager_run_id=manager_run_id,
            account=account,
            quote_map=quote_map,
            clock=clock,
        )
        return {
            "status": status,
            "run_id": manager_run_id,
            "run_at": run_at,
            "mode": mode,
            "error_message": error_message,
            "actions": actions,
            "applied": applied,
            "allocation": allocation,
        }

    async def executor_tick(self, *, manual: bool = False) -> dict[str, Any]:
        clock = self.clock()
        if self.kill_switch().get("enabled"):
            return {
                "status": "blocked",
                "reason": "kill_switch_enabled",
                "clock": clock,
                "actions": [],
            }
        if not bool(clock.get("is_market_open")) and not manual:
            return {
                "status": "skipped",
                "reason": "market_closed",
                "clock": clock,
                "actions": [],
            }
        account = await self.collect_account()
        blocks = [
            row
            for row in self.repository.list_blocks(include_closed=False)
            if str(row.get("status") or "") in {"open", "entry_pending", "exit_pending"}
        ]
        order_reconciliation = await self._reconcile_pending_orders()
        reconciliation = self._reconcile(account=account, blocks=blocks)
        blocks = [
            row
            for row in self.repository.list_blocks(include_closed=False)
            if str(row.get("status") or "") == "open"
        ]
        symbols = sorted({str(row.get("symbol") or "") for row in blocks if _is_symbol(row.get("symbol"))})
        quotes = await self.quote_service.collect_quotes(
            symbols,
            concurrency=self.config.quote_concurrency,
        )
        self.repository.save_quotes(quotes)
        quote_map = {str(row.get("symbol") or ""): row for row in quotes}
        actions: list[dict[str, Any]] = []
        for block in blocks:
            action = await self._maybe_exit_block(block, quote_map=quote_map, manual=manual)
            if action:
                actions.append(action)
        return {
            "status": "ok",
            "clock": clock,
            "order_reconciliation": order_reconciliation,
            "reconciliation": reconciliation,
            "actions": actions,
            "action_count": len(actions),
        }

    async def close_block(self, block_id: str, *, reason: str = "manual_close") -> dict[str, Any]:
        block = self.repository.get_block(block_id)
        if not block:
            return {"status": "missing", "block_id": block_id}
        self.repository.update_block(
            block_id,
            {"force_exit_requested": 1, "llm_reason": reason},
        )
        return await self.executor_tick(manual=True)

    async def cancel_order(
        self,
        order_id: int,
        *,
        reason: str = "manual_cancel",
    ) -> dict[str, Any]:
        order = self.repository.get_order(int(order_id))
        if not order:
            return {"status": "missing", "order_id": int(order_id)}
        if str(order.get("status") or "") not in {
            "sent",
            "partially_filled",
            "cancel_requested",
        }:
            return {"status": "skipped", "reason": "order_not_pending", "order": order}
        if not self.config.execute_orders:
            updated = self.repository.update_order(
                int(order_id),
                {
                    "status": "canceled",
                    "cancel_requested": 1,
                    "last_checked_at": utc_now_iso(),
                },
            )
            self.repository.add_event(
                str(order.get("block_id") or ""),
                "order_cancel_paper",
                f"paper cancel requested for order {order_id}",
                {"reason": reason, "order": updated},
            )
            return {"status": "ok", "mode": "paper", "order": updated}

        order_no = str(order.get("order_no") or "").strip()
        if not order_no:
            return {"status": "skipped", "reason": "order_no_missing", "order": order}
        try:
            response = await self.kis.cancel_domestic_order(
                order_no=order_no,
                order_orgno=str(order.get("order_orgno") or ""),
                quantity=_safe_int(order.get("remaining_qty")),
                order_type=str(order.get("order_type") or "00"),
            )
            updated = self.repository.update_order(
                int(order_id),
                {
                    "status": "cancel_requested",
                    "cancel_requested": 1,
                    "cancel_order_no": str(response.get("cancel_order_no") or ""),
                    "cancel_response_json": response,
                    "last_checked_at": utc_now_iso(),
                },
            )
            self.repository.add_event(
                str(order.get("block_id") or ""),
                "order_cancel_requested",
                f"cancel requested for order {order_no}",
                {"reason": reason, "response": response},
            )
            return {"status": "ok", "order": updated, "response": response}
        except Exception as exc:
            updated = self.repository.update_order(
                int(order_id),
                {
                    "status": "cancel_failed",
                    "last_checked_at": utc_now_iso(),
                    "cancel_response_json": {"error": str(exc), "reason": reason},
                },
            )
            self.repository.add_event(
                str(order.get("block_id") or ""),
                "order_cancel_failed",
                str(exc),
                {"reason": reason, "order": updated},
            )
            return {"status": "error", "error_message": str(exc), "order": updated}

    def pause_block(self, block_id: str, *, reason: str = "manual_pause") -> dict[str, Any]:
        block = self.repository.get_block(block_id)
        if not block:
            return {"status": "missing", "block_id": block_id}
        if str(block.get("status") or "") not in {"open", "entry_pending", "error"}:
            return {"status": "skipped", "reason": "status_not_pauseable", "block": block}
        updated = self.repository.update_block(block_id, {"status": "paused", "llm_reason": reason})
        return {"status": "ok", "block": updated}

    def resume_block(self, block_id: str, *, reason: str = "manual_resume") -> dict[str, Any]:
        block = self.repository.get_block(block_id)
        if not block:
            return {"status": "missing", "block_id": block_id}
        if str(block.get("status") or "") != "paused":
            return {"status": "skipped", "reason": "status_not_paused", "block": block}
        updated = self.repository.update_block(block_id, {"status": "open", "llm_reason": reason})
        return {"status": "ok", "block": updated}

    def block_detail(self, block_id: str) -> dict[str, Any]:
        block = self.repository.get_block(block_id)
        if not block:
            return {"status": "missing", "block_id": block_id}
        return {
            "status": "ok",
            "block": block,
            "orders": self.repository.list_orders(block_id=block_id, limit=100),
            "events": self.repository.list_events(block_id=block_id, limit=100),
        }

    async def _complete_manager(self, prompt: dict[str, Any]) -> tuple[Any | None, str]:
        if not getattr(self.llm_bridge, "ready", False):
            return None, "llm_bridge_unavailable"
        payload = {
            "model": getattr(self.llm_bridge, "resolved_model", "gpt-5.5"),
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only JSON matching the schema."},
                {"role": "user", "content": _json_dumps(prompt)},
            ],
        }
        result = await self.llm_bridge.complete(payload)
        if not bool(result.get("ok")):
            return None, str(result.get("error") or "llm_failed")
        text = str(result.get("content") or "").strip()
        if not text:
            return None, "llm_empty_response"
        try:
            return json.loads(text), ""
        except json.JSONDecodeError as exc:
            return None, f"llm_json_error:{exc}"

    def _sanitize_actions(
        self,
        parsed: Any,
        *,
        blocks: list[dict[str, Any]],
        quotes: dict[str, dict[str, Any]],
        account: dict[str, Any],
    ) -> dict[str, Any]:
        block_ids = {str(row.get("block_id") or "") for row in blocks}
        source = parsed if isinstance(parsed, dict) else {}
        unallocated = self._unallocated_qty_by_symbol(account=account, blocks=blocks)
        positions = self._positions_by_symbol(account)
        adopt_existing_blocks: list[dict[str, Any]] = []
        for row in _normalize_list(source.get("adopt_existing_blocks")):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            qty = max(_safe_int(row.get("qty")), 0)
            target = _safe_float(row.get("target_price"))
            stop = _safe_float(row.get("stop_price"))
            position = positions.get(symbol) or {}
            quote_price = _safe_float((quotes.get(symbol) or {}).get("price"))
            reference_price = (
                quote_price
                or _safe_float(position.get("mark_price"))
                or _safe_float(position.get("avg_price"))
            )
            if not _is_symbol(symbol) or qty <= 0 or target <= 0 or stop <= 0:
                continue
            if qty > max(int(unallocated.get(symbol, 0)), 0):
                continue
            if reference_price > 0 and not (stop < reference_price < target):
                continue
            unallocated[symbol] = max(int(unallocated.get(symbol, 0)) - qty, 0)
            adopt_existing_blocks.append(
                {
                    "symbol": symbol,
                    "qty": qty,
                    "target_price": target,
                    "stop_price": stop,
                    "thesis": _clean_text(row.get("thesis"), limit=2000),
                    "confidence": _safe_float(row.get("confidence")),
                    "risk_note": _clean_text(row.get("risk_note"), limit=2000),
                }
            )
        create_blocks: list[dict[str, Any]] = []
        for row in _normalize_list(source.get("create_blocks")):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            qty = max(_safe_int(row.get("qty")), 0)
            target = _safe_float(row.get("target_price"))
            stop = _safe_float(row.get("stop_price"))
            quote_price = _safe_float((quotes.get(symbol) or {}).get("price"))
            if not _is_symbol(symbol) or qty <= 0 or target <= 0 or stop <= 0:
                continue
            if quote_price > 0 and not (stop < quote_price < target):
                continue
            create_blocks.append(
                {
                    "symbol": symbol,
                    "qty": qty,
                    "target_price": target,
                    "stop_price": stop,
                    "entry_style": str(row.get("entry_style") or "aggressive_limit"),
                    "thesis": _clean_text(row.get("thesis"), limit=2000),
                    "confidence": _safe_float(row.get("confidence")),
                    "risk_note": _clean_text(row.get("risk_note"), limit=2000),
                }
            )
        update_blocks: list[dict[str, Any]] = []
        for row in _normalize_list(source.get("update_blocks")):
            if not isinstance(row, dict):
                continue
            block_id = str(row.get("block_id") or "").strip()
            if block_id not in block_ids:
                continue
            update_blocks.append(
                {
                    "block_id": block_id,
                    "target_price": _safe_float(row.get("target_price")),
                    "stop_price": _safe_float(row.get("stop_price")),
                    "reason": _clean_text(row.get("reason"), limit=1000),
                }
            )
        close_blocks = self._sanitize_block_id_actions(source.get("close_blocks"), block_ids)
        pause_blocks = self._sanitize_block_id_actions(source.get("pause_blocks"), block_ids)
        return {
            "adopt_existing_blocks": adopt_existing_blocks,
            "create_blocks": create_blocks,
            "update_blocks": update_blocks,
            "close_blocks": close_blocks,
            "pause_blocks": pause_blocks,
        }

    def _sanitize_block_id_actions(self, rows: Any, block_ids: set[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in _normalize_list(rows):
            if not isinstance(row, dict):
                continue
            block_id = str(row.get("block_id") or "").strip()
            if block_id not in block_ids:
                continue
            out.append({"block_id": block_id, "reason": _clean_text(row.get("reason"), limit=1000)})
        return out

    async def _apply_manager_actions(
        self,
        actions: dict[str, Any],
        *,
        manager_run_id: int,
        account: dict[str, Any],
        quote_map: dict[str, dict[str, Any]],
        clock: dict[str, Any],
    ) -> dict[str, Any]:
        applied: dict[str, Any] = {
            "adopted": [],
            "created": [],
            "updated": [],
            "closed_requested": [],
            "paused": [],
            "rejected": [],
        }
        kill = self.kill_switch()
        allow_create = (
            not kill.get("enabled")
            and str(clock.get("session") or "") in {"pre_open", "regular"}
        )
        for row in actions.get("adopt_existing_blocks") or []:
            adopted = self._adopt_existing_block(
                row,
                manager_run_id=manager_run_id,
                account=account,
                quote_map=quote_map,
            )
            applied["adopted"].append(adopted)
        for row in actions.get("update_blocks") or []:
            fields = {
                "target_price": row.get("target_price") or None,
                "stop_price": row.get("stop_price") or None,
                "llm_reason": row.get("reason") or "",
            }
            updated = self.repository.update_block(str(row.get("block_id") or ""), fields)
            if updated:
                applied["updated"].append(updated)
        for row in actions.get("pause_blocks") or []:
            paused = self.pause_block(str(row.get("block_id") or ""), reason=str(row.get("reason") or "llm_pause"))
            applied["paused"].append(paused)
        for row in actions.get("close_blocks") or []:
            block_id = str(row.get("block_id") or "")
            self.repository.update_block(
                block_id,
                {"force_exit_requested": 1, "llm_reason": row.get("reason") or "llm_close"},
            )
            applied["closed_requested"].append({"block_id": block_id})
        for row in actions.get("create_blocks") or []:
            if not allow_create:
                applied["rejected"].append({"action": "create", "row": row, "reason": "create_not_allowed"})
                continue
            created = await self._create_and_enter_block(
                row,
                manager_run_id=manager_run_id,
                account=account,
                quote_map=quote_map,
            )
            applied["created"].append(created)
        return applied

    def _adopt_existing_block(
        self,
        row: dict[str, Any],
        *,
        manager_run_id: int,
        account: dict[str, Any],
        quote_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        symbol = str(row.get("symbol") or "")
        qty = max(_safe_int(row.get("qty")), 0)
        if qty <= 0:
            return {"status": "rejected", "reason": "qty_invalid", "symbol": symbol}
        existing_blocks = self.repository.list_blocks(include_closed=False)
        unallocated = self._unallocated_qty_by_symbol(
            account=account,
            blocks=existing_blocks,
        )
        if qty > max(int(unallocated.get(symbol, 0)), 0):
            return {
                "status": "rejected",
                "reason": "unallocated_qty_insufficient",
                "symbol": symbol,
            }
        position = self._positions_by_symbol(account).get(symbol) or {}
        quote = quote_map.get(symbol) or {}
        entry_price = _safe_float(position.get("avg_price"))
        current_price = (
            _safe_float(quote.get("price"))
            or _safe_float(position.get("mark_price"))
            or entry_price
        )
        if entry_price <= 0:
            entry_price = current_price
        block = self.repository.create_block(
            {
                "symbol": symbol,
                "name": str(position.get("name") or quote.get("name") or symbol),
                "qty": qty,
                "qty_open": qty,
                "entry_price": entry_price,
                "target_price": row.get("target_price"),
                "stop_price": row.get("stop_price"),
                "thesis": row.get("thesis"),
                "llm_reason": f"adopt_existing_position confidence={row.get('confidence')}",
                "risk_note": row.get("risk_note"),
                "created_by": "existing_position",
                "manager_run_id": manager_run_id,
                "status": "open",
                "opened_at": utc_now_iso(),
                "metadata": {
                    "adopted_from_account": True,
                    "confidence": row.get("confidence"),
                    "position": position,
                    "quote": quote,
                },
            }
        )
        self.repository.add_event(
            str(block["block_id"]),
            "adopted_existing_position",
            f"existing holding adopted: {symbol} x{qty}",
            {
                "manager_run_id": manager_run_id,
                "position": position,
                "quote": quote,
            },
        )
        return {"status": "ok", "block": self.repository.get_block(block["block_id"])}

    async def _create_and_enter_block(
        self,
        row: dict[str, Any],
        *,
        manager_run_id: int,
        account: dict[str, Any],
        quote_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        symbol = str(row.get("symbol") or "")
        quote = quote_map.get(symbol) or {}
        price = _safe_float(quote.get("price"))
        qty = max(_safe_int(row.get("qty")), 1)
        limit_price = aggressive_limit_price(
            price,
            side="buy",
            bps=self.config.aggressive_limit_bps,
        )
        if limit_price <= 0:
            return {"status": "rejected", "reason": "quote_missing", "symbol": symbol}
        cash = _safe_float(account.get("cash_krw"))
        if cash > 0 and limit_price * qty > cash:
            return {"status": "rejected", "reason": "cash_insufficient", "symbol": symbol}
        block = self.repository.create_block(
            {
                "symbol": symbol,
                "name": str(quote.get("name") or symbol),
                "qty": qty,
                "qty_open": qty if not self.config.execute_orders else 0,
                "entry_price": price,
                "target_price": row.get("target_price"),
                "stop_price": row.get("stop_price"),
                "thesis": row.get("thesis"),
                "llm_reason": f"confidence={row.get('confidence')}",
                "risk_note": row.get("risk_note"),
                "created_by": "llm",
                "manager_run_id": manager_run_id,
                "status": "open" if not self.config.execute_orders else "entry_pending",
                "opened_at": utc_now_iso() if not self.config.execute_orders else "",
                "metadata": {"entry_style": row.get("entry_style"), "paper": not self.config.execute_orders},
            }
        )
        order_status = "planned"
        order_no = ""
        order_orgno = ""
        response: dict[str, Any] = {}
        if self.config.execute_orders:
            try:
                response = await self.kis.submit_domestic_order(
                    symbol=symbol,
                    side="buy",
                    quantity=qty,
                    price=limit_price,
                    order_type="00",
                )
                order_status = "sent"
                order_no = str(response.get("order_no") or "")
                order_orgno = str(response.get("order_orgno") or "")
            except Exception as exc:
                self.repository.update_block(
                    str(block["block_id"]),
                    {"status": "error", "llm_reason": str(exc)},
                )
                order_status = "failed"
                response = {"error": str(exc)}
        order = self.repository.add_order(
            {
                "block_id": block["block_id"],
                "symbol": symbol,
                "side": "buy",
                "qty": qty,
                "limit_price": limit_price,
                "order_type": "00",
                "status": order_status,
                "order_no": order_no,
                "order_orgno": order_orgno,
                "reason": "llm_block_entry",
                "response": response,
            }
        )
        return {"status": "ok", "block": self.repository.get_block(block["block_id"]), "order": order}

    async def _maybe_exit_block(
        self,
        block: dict[str, Any],
        *,
        quote_map: dict[str, dict[str, Any]],
        manual: bool,
    ) -> dict[str, Any] | None:
        symbol = str(block.get("symbol") or "")
        quote = quote_map.get(symbol) or {}
        price = _safe_float(quote.get("price"))
        if price <= 0:
            return None
        reason = ""
        if block.get("force_exit_requested"):
            reason = "force_exit_requested"
        elif _safe_float(block.get("target_price")) > 0 and price >= _safe_float(block.get("target_price")):
            reason = "target_reached"
        elif _safe_float(block.get("stop_price")) > 0 and price <= _safe_float(block.get("stop_price")):
            reason = "stop_reached"
        if not reason:
            return None
        qty = max(_safe_int(block.get("qty_open")), 0)
        if qty <= 0:
            return None
        limit_price = aggressive_limit_price(
            price,
            side="sell",
            bps=self.config.aggressive_limit_bps,
        )
        if not self.config.execute_orders:
            order = self.repository.add_order(
                {
                    "block_id": block["block_id"],
                    "symbol": symbol,
                    "side": "sell",
                    "qty": qty,
                    "limit_price": limit_price,
                    "order_type": "00",
                    "status": "planned",
                    "reason": reason,
                    "response": {"manual": manual, "price": price},
                }
            )
            updated = self.repository.update_block(
                str(block["block_id"]),
                {
                    "status": "closed",
                    "qty_open": 0,
                    "closed_at": utc_now_iso(),
                    "force_exit_requested": 0,
                    "llm_reason": reason,
                },
            )
            return {"status": "closed_paper", "reason": reason, "block": updated, "order": order}
        self.repository.update_block(str(block["block_id"]), {"status": "exit_pending"})
        try:
            response = await self.kis.submit_domestic_order(
                symbol=symbol,
                side="sell",
                quantity=qty,
                price=limit_price,
                order_type="00",
            )
            order_status = "sent"
            order_no = str(response.get("order_no") or "")
            order_orgno = str(response.get("order_orgno") or "")
        except Exception as exc:
            response = {"error": str(exc)}
            order_status = "failed"
            order_no = ""
            order_orgno = ""
            self.repository.update_block(str(block["block_id"]), {"status": "error", "llm_reason": str(exc)})
        order = self.repository.add_order(
            {
                "block_id": block["block_id"],
                "symbol": symbol,
                "side": "sell",
                "qty": qty,
                "limit_price": limit_price,
                "order_type": "00",
                "status": order_status,
                "order_no": order_no,
                "order_orgno": order_orgno,
                "reason": reason,
                "response": response,
            }
        )
        return {"status": order_status, "reason": reason, "block_id": block["block_id"], "order": order}

    async def _reconcile_pending_orders(self) -> dict[str, Any]:
        pending = self.repository.list_pending_orders(limit=50)
        if not pending:
            return {"status": "ok", "checked": 0, "changes": []}
        if not self.config.execute_orders:
            return {"status": "skipped", "reason": "paper_mode", "checked": 0, "changes": []}

        changes: list[dict[str, Any]] = []
        for order in pending:
            change = await self._reconcile_pending_order(order)
            if change:
                changes.append(change)
        return {
            "status": "ok",
            "checked": len(pending),
            "change_count": len(changes),
            "changes": changes,
        }

    async def _reconcile_pending_order(self, order: dict[str, Any]) -> dict[str, Any] | None:
        order_id = int(order.get("id") or 0)
        order_no = str(order.get("order_no") or "").strip()
        block_id = str(order.get("block_id") or "")
        if not order_id or not order_no:
            return None

        try:
            inquiry = await self.kis.fetch_domestic_order_daily(
                symbol=str(order.get("symbol") or ""),
                order_no=order_no,
                order_orgno=str(order.get("order_orgno") or ""),
                start_date=self._order_query_start_date(order),
                end_date=datetime.now(timezone.utc)
                .astimezone(ZoneInfo("Asia/Seoul"))
                .strftime("%Y%m%d"),
                ccld_dvsn="00",
                max_pages=2,
            )
            match = self._match_inquired_order(order, inquiry)
        except Exception as exc:
            self.repository.add_event(
                block_id,
                "order_reconcile_error",
                str(exc),
                {"order_id": order_id, "order_no": order_no},
            )
            return await self._handle_stale_pending_order(order, reason=str(exc))

        if not match:
            return await self._handle_stale_pending_order(order, reason="order_inquiry_missing")

        filled_qty = max(_safe_int(match.get("filled_qty")), 0)
        remaining_qty = max(_safe_int(match.get("remaining_qty")), 0)
        avg_price = _safe_float(match.get("avg_fill_price"))
        order_status = self._status_from_order_fill(order, match)
        updated_order = self.repository.update_order(
            order_id,
            {
                "status": order_status,
                "order_orgno": str(match.get("order_orgno") or order.get("order_orgno") or ""),
                "filled_qty": filled_qty,
                "remaining_qty": remaining_qty,
                "avg_fill_price": avg_price or None,
                "last_checked_at": utc_now_iso(),
                "response_json": match.get("raw") or {},
            },
        )
        block_change = self._apply_order_fill_to_block(order, match, order_status)
        if order_status in {"filled", "canceled", "partially_filled"}:
            self.repository.add_event(
                block_id,
                "order_reconciled",
                f"{order_no} {order_status} fill={filled_qty} remain={remaining_qty}",
                {"order": updated_order, "inquiry": match, "block_change": block_change},
            )
        if order_status in {"sent", "partially_filled", "cancel_requested"}:
            stale_change = await self._handle_stale_pending_order(updated_order or order)
            if stale_change:
                return stale_change
        return {
            "type": "order_reconciled",
            "order_id": order_id,
            "status": order_status,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "block_change": block_change,
        }

    def _match_inquired_order(
        self,
        order: dict[str, Any],
        inquiry: dict[str, Any],
    ) -> dict[str, Any] | None:
        order_no = str(order.get("order_no") or "")
        symbol = str(order.get("symbol") or "")
        rows = [row for row in list(inquiry.get("orders") or []) if isinstance(row, dict)]
        for row in rows:
            if str(row.get("order_no") or "") == order_no:
                return row
        for row in rows:
            if str(row.get("symbol") or "") == symbol:
                return row
        return None

    def _status_from_order_fill(
        self,
        order: dict[str, Any],
        match: dict[str, Any],
    ) -> str:
        qty = max(_safe_int(order.get("qty")), 0)
        filled_qty = max(_safe_int(match.get("filled_qty")), 0)
        remaining_qty = max(_safe_int(match.get("remaining_qty")), 0)
        if bool(match.get("canceled")) and filled_qty <= 0:
            return "canceled"
        if qty > 0 and filled_qty >= qty and remaining_qty <= 0:
            return "filled"
        if filled_qty > 0:
            if remaining_qty <= 0:
                return "filled"
            return "partially_filled"
        if str(order.get("status") or "") == "cancel_requested":
            return "cancel_requested"
        return "sent"

    def _apply_order_fill_to_block(
        self,
        order: dict[str, Any],
        match: dict[str, Any],
        order_status: str,
    ) -> dict[str, Any] | None:
        block = self.repository.get_block(str(order.get("block_id") or ""))
        if not block:
            return None
        filled_qty = max(_safe_int(match.get("filled_qty")), 0)
        avg_price = _safe_float(match.get("avg_fill_price"))
        side = str(order.get("side") or "")
        if side == "buy":
            if order_status == "filled":
                return self.repository.update_block(
                    str(block["block_id"]),
                    {
                        "status": "open",
                        "qty_open": filled_qty,
                        "entry_price": avg_price or block.get("entry_price"),
                        "opened_at": utc_now_iso(),
                        "llm_reason": "filled_reconciled_by_order",
                    },
                )
            if order_status == "partially_filled":
                return self.repository.update_block(
                    str(block["block_id"]),
                    {
                        "qty_open": filled_qty,
                        "entry_price": avg_price or block.get("entry_price"),
                        "llm_reason": "partial_entry_reconciled",
                    },
                )
            if order_status == "canceled":
                next_status = "open" if filled_qty > 0 else "error"
                return self.repository.update_block(
                    str(block["block_id"]),
                    {
                        "status": next_status,
                        "qty_open": filled_qty,
                        "entry_price": avg_price or block.get("entry_price"),
                        "opened_at": utc_now_iso() if filled_qty > 0 else "",
                        "llm_reason": "entry_order_canceled",
                    },
                )
        if side == "sell":
            remaining_open = max(_safe_int(block.get("qty_open")) - filled_qty, 0)
            if order_status == "filled" or remaining_open <= 0:
                return self.repository.update_block(
                    str(block["block_id"]),
                    {
                        "status": "closed",
                        "qty_open": 0,
                        "closed_at": utc_now_iso(),
                        "force_exit_requested": 0,
                        "llm_reason": "exit_filled_reconciled_by_order",
                    },
                )
            if filled_qty > 0:
                return self.repository.update_block(
                    str(block["block_id"]),
                    {
                        "qty_open": remaining_open,
                        "llm_reason": "partial_exit_reconciled",
                    },
                )
            if order_status == "canceled":
                return self.repository.update_block(
                    str(block["block_id"]),
                    {
                        "status": "open",
                        "force_exit_requested": 0,
                        "llm_reason": "exit_order_canceled",
                    },
                )
        return None

    async def _handle_stale_pending_order(
        self,
        order: dict[str, Any] | None,
        *,
        reason: str = "pending_timeout",
    ) -> dict[str, Any] | None:
        if not order or not self._is_order_stale(order):
            return None
        order_id = int(order.get("id") or 0)
        status = str(order.get("status") or "")
        if status in {"sent", "partially_filled"} and not order.get("cancel_requested"):
            cancel_result = await self.cancel_order(order_id, reason=reason)
            return {
                "type": "stale_cancel_requested",
                "order_id": order_id,
                "result": cancel_result,
            }
        block_id = str(order.get("block_id") or "")
        self.repository.update_order(
            order_id,
            {
                "status": "stale",
                "last_checked_at": utc_now_iso(),
                "cancel_response_json": {"reason": reason},
            },
        )
        self.repository.update_block(
            block_id,
            {
                "status": "error",
                "llm_reason": f"pending_order_stale:{reason}",
            },
        )
        self.repository.add_event(
            block_id,
            "order_stale",
            f"order {order.get('order_no')} stale: {reason}",
            {"order": order},
        )
        return {"type": "stale_error", "order_id": order_id, "reason": reason}

    def _is_order_stale(self, order: dict[str, Any]) -> bool:
        created = _parse_iso_datetime(order.get("created_at"))
        if created is None:
            return False
        age = (datetime.now(timezone.utc) - created).total_seconds()
        return age >= max(int(self.config.pending_reconcile_timeout_sec), 30)

    def _order_query_start_date(self, order: dict[str, Any]) -> str:
        created = _parse_iso_datetime(order.get("created_at"))
        if created is None:
            return (
                datetime.now(timezone.utc)
                .astimezone(ZoneInfo("Asia/Seoul"))
                .strftime("%Y%m%d")
            )
        return created.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")

    def _reconcile(
        self,
        *,
        account: dict[str, Any],
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        positions = {
            str(row.get("symbol") or ""): _safe_int(row.get("available_qty") or row.get("qty"))
            for row in list(account.get("positions") or [])
            if isinstance(row, dict)
        }
        by_symbol: dict[str, dict[str, int]] = {}
        changes: list[dict[str, Any]] = []
        for block in blocks:
            symbol = str(block.get("symbol") or "")
            bucket = by_symbol.setdefault(
                symbol,
                {"account_qty": int(positions.get(symbol, 0)), "allocated_qty": 0},
            )
            status = str(block.get("status") or "")
            if status == "entry_pending":
                available = max(bucket["account_qty"] - bucket["allocated_qty"], 0)
                qty = max(_safe_int(block.get("qty_initial")), 0)
                if available >= qty:
                    updated = self.repository.update_block(
                        str(block["block_id"]),
                        {
                            "status": "open",
                            "qty_open": qty,
                            "opened_at": utc_now_iso(),
                            "llm_reason": "filled_reconciled",
                        },
                    )
                    bucket["allocated_qty"] += qty
                    changes.append({"type": "entry_filled", "block": updated})
                else:
                    bucket["allocated_qty"] += _safe_int(block.get("qty_open"))
            elif status == "exit_pending":
                qty = max(_safe_int(block.get("qty_open")), 0)
                available = max(bucket["account_qty"] - bucket["allocated_qty"], 0)
                if available < qty:
                    updated = self.repository.update_block(
                        str(block["block_id"]),
                        {
                            "status": "closed",
                            "qty_open": 0,
                            "closed_at": utc_now_iso(),
                            "force_exit_requested": 0,
                            "llm_reason": "exit_reconciled",
                        },
                    )
                    changes.append({"type": "exit_filled", "block": updated})
                else:
                    bucket["allocated_qty"] += qty
            elif status == "open":
                bucket["allocated_qty"] += max(_safe_int(block.get("qty_open")), 0)
        summary = {
            "status": "ok",
            "symbols": by_symbol,
            "changes": changes,
            "change_count": len(changes),
        }
        self.repository.save_reconciliation(account, summary)
        return summary

    def _allocation_summary(
        self,
        *,
        account: dict[str, Any],
        blocks: list[dict[str, Any]],
        quotes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        positions = {
            str(row.get("symbol") or ""): row
            for row in list(account.get("positions") or [])
            if isinstance(row, dict)
        }
        symbols = sorted(
            {
                *positions.keys(),
                *[str(row.get("symbol") or "") for row in blocks if _is_symbol(row.get("symbol"))],
            }
        )
        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            block_qty = sum(
                _safe_int(row.get("qty_open") or row.get("qty_initial"))
                for row in blocks
                if str(row.get("symbol") or "") == symbol
                and str(row.get("status") or "") in ACTIVE_BLOCK_STATUSES
            )
            position = positions.get(symbol) or {}
            account_qty = _safe_int(position.get("available_qty") or position.get("qty"))
            quote = quotes.get(symbol) or {}
            rows.append(
                {
                    "symbol": symbol,
                    "name": str(position.get("name") or quote.get("name") or symbol),
                    "account_qty": account_qty,
                    "block_qty": block_qty,
                    "unallocated_qty": max(account_qty - block_qty, 0),
                    "overallocated_qty": max(block_qty - account_qty, 0),
                }
            )
        return {"status": "ok", "items": rows}

    def _positions_by_symbol(self, account: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("symbol") or ""): row
            for row in list(account.get("positions") or [])
            if isinstance(row, dict) and _is_symbol(row.get("symbol"))
        }

    def _unallocated_qty_by_symbol(
        self,
        *,
        account: dict[str, Any],
        blocks: list[dict[str, Any]],
    ) -> dict[str, int]:
        positions = self._positions_by_symbol(account)
        out: dict[str, int] = {}
        for symbol, position in positions.items():
            out[symbol] = max(
                _safe_int(position.get("available_qty") or position.get("qty")),
                0,
            )
        for block in blocks:
            status = str(block.get("status") or "")
            if status not in ACTIVE_BLOCK_STATUSES:
                continue
            symbol = str(block.get("symbol") or "")
            if not _is_symbol(symbol):
                continue
            qty = max(_safe_int(block.get("qty_open") or block.get("qty_initial")), 0)
            out[symbol] = max(int(out.get(symbol, 0)) - qty, 0)
        return out

    def _decorate_block(self, block: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> dict[str, Any]:
        quote = quotes.get(str(block.get("symbol") or "")) or {}
        price = _safe_float(quote.get("price"))
        entry = _safe_float(block.get("entry_price"))
        qty = _safe_int(block.get("qty_open") or block.get("qty_initial"))
        pnl = (price - entry) * qty if price > 0 and entry > 0 else 0.0
        return {
            **block,
            "quote": quote,
            "current_price": price if price > 0 else None,
            "unrealized_pnl_krw": pnl,
            "next_rule_action": self._next_rule_action(block, price),
        }

    def _next_rule_action(self, block: dict[str, Any], price: float) -> str:
        if str(block.get("status") or "") != "open":
            return str(block.get("status") or "")
        if block.get("force_exit_requested"):
            return "force_exit"
        if price > 0 and _safe_float(block.get("target_price")) > 0 and price >= _safe_float(block.get("target_price")):
            return "target_exit"
        if price > 0 and _safe_float(block.get("stop_price")) > 0 and price <= _safe_float(block.get("stop_price")):
            return "stop_exit"
        return "watch"

    def _symbols_for_quotes(self, blocks: list[dict[str, Any]], account: dict[str, Any]) -> list[str]:
        symbols: list[str] = []
        for row in list(account.get("positions") or []):
            symbol = str(row.get("symbol") or "")
            if _is_symbol(symbol) and symbol not in symbols:
                symbols.append(symbol)
        for row in blocks:
            symbol = str(row.get("symbol") or "")
            if _is_symbol(symbol) and symbol not in symbols:
                symbols.append(symbol)
        return symbols[: max(int(self.config.max_manager_symbols), 1)]

    def _manager_symbols(self, *, account: dict[str, Any], blocks: list[dict[str, Any]]) -> list[str]:
        symbols = self._symbols_for_quotes(blocks, account)
        strategy = self._strategy_payload()
        for row in list(strategy.get("candidates") or []):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if _is_symbol(symbol) and symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= max(int(self.config.max_manager_symbols), 1):
                break
        return symbols[: max(int(self.config.max_manager_symbols), 1)]

    def _strategy_payload(self) -> dict[str, Any]:
        if self.strategy_engine is None:
            return {"status": "missing", "candidates": []}
        research = None
        if callable(self.research_feed_provider):
            research = self.research_feed_provider()
        try:
            return self.strategy_engine.build_candidates(
                query=self.config.manager_query,
                research_feed=research if isinstance(research, dict) else None,
                limit=self.config.max_manager_symbols,
            )
        except Exception as exc:
            return {"status": "error", "error_message": str(exc), "candidates": []}

    def _latest_market_judgment(self) -> dict[str, Any]:
        provider = self.market_judgment_provider
        if provider is None:
            return {"status": "missing"}
        try:
            return provider.latest_judgment()
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}

    def _investment_memory_context(
        self,
        *,
        symbols: list[str],
        block_ids: list[str],
    ) -> dict[str, Any]:
        provider = self.memory_context_provider
        if provider is None:
            return {"status": "missing"}
        try:
            payload = provider(symbols=symbols, block_ids=block_ids)
        except TypeError:
            try:
                payload = provider(symbols)
            except Exception as exc:
                return {"status": "error", "error_message": str(exc)}
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}
        return payload if isinstance(payload, dict) else {"status": "invalid"}


async def run_due_manager(
    trader: KISBlockTrader,
    *,
    last_manager_at: datetime | None,
) -> tuple[bool, dict[str, Any] | None]:
    clock = trader.clock()
    session = str(clock.get("session") or "closed")
    if session == "pre_open":
        local_date = datetime.now(timezone.utc).date()
        if last_manager_at is None or last_manager_at.date() != local_date:
            return True, await trader.run_manager_once()
    if session in {"regular", "closing_watch"}:
        if session == "closing_watch":
            return False, None
        if last_manager_at is None:
            return True, await trader.run_manager_once()
        elapsed = (datetime.now(timezone.utc) - last_manager_at).total_seconds()
        if elapsed >= max(int(trader.config.manager_interval_sec), 60):
            return True, await trader.run_manager_once()
    return False, None
