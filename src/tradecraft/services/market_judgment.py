from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

import httpx

from tradecraft.services.kis import KISAdapter
from tradecraft.services.krx_holiday import KRXHolidayCalendar
from tradecraft.services.llm_bridge import LLMBridge

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

ACCOUNT_ACTIONS = {
    "hold",
    "watch_add",
    "avoid_add",
    "trim_watch",
    "risk_check",
    "new_watch",
}
STANCES = {"watch", "confirm", "hold", "risk_check", "avoid", "stale"}
HORIZONS = {"intraday", "short_term", "mid_term", "long_term", "unknown"}


class ReportRepository(Protocol):
    def search(
        self,
        query: str,
        symbol: str = "",
        category: str = "",
        limit: int = 10,
        broker: str = "",
        analyst: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]: ...

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]: ...


class StrategyEngine(Protocol):
    def build_candidates(
        self,
        *,
        query: str,
        research_feed: dict[str, Any] | None,
        limit: int | None = None,
    ) -> dict[str, Any]: ...

    def list_external_signals(
        self,
        source_id: str = "",
        symbol: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> dict[str, Any]: ...


class FundamentalsRepository(Protocol):
    def latest(self, symbol: str) -> dict[str, Any] | None: ...


class RAGQueryStore(Protocol):
    def query(
        self,
        query: str,
        symbol: str = "",
        limit: int = 8,
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]: ...


ResearchFeedProvider = Callable[[], dict[str, Any] | None]


@dataclass(slots=True)
class MarketJudgmentConfig:
    db_path: str = ".runtime/market_judgment.db"
    state_path: str = ".runtime/market_judge.json"
    quote_interval_sec: int = 60
    judge_interval_sec: int = 600
    max_symbols: int = 60
    llm_max_symbols: int = 12
    use_naver_fallback: bool = True
    query: str = "장중 현재 움직임과 내 국장1 계좌를 반영해 관심/보류 판단을 정리해줘"
    request_timeout_sec: float = 8.0
    quote_concurrency: int = 4


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
    return int(round(_safe_float(value)))


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default
    return parsed


def _clean_text(value: Any, *, limit: int = 260) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(int(limit), 1)]


def _market_session_for(local: datetime, *, is_open_day: bool) -> str:
    if not is_open_day:
        return "closed"
    current = local.time()
    if time(8, 30) <= current < time(9, 0):
        return "pre_open"
    if time(9, 0) <= current < time(15, 20):
        return "regular"
    if time(15, 20) <= current < time(15, 30):
        return "closing_watch"
    if time(15, 30) <= current < time(16, 0):
        return "post_close_review"
    return "closed"


def build_market_clock(
    *,
    now: datetime | None = None,
    calendar: KRXHolidayCalendar | None = None,
) -> dict[str, Any]:
    local = (now or datetime.now(KST)).astimezone(KST)
    current_date = local.date()
    is_open_day = current_date.weekday() < 5
    if is_open_day and calendar is not None:
        is_open_day = calendar.is_open_day(current_date)
    session = _market_session_for(local, is_open_day=is_open_day)
    next_open = _next_open_datetime(local, calendar=calendar)
    return {
        "status": "ok",
        "timezone": "Asia/Seoul",
        "now": local.isoformat(),
        "date": current_date.isoformat(),
        "is_open_day": is_open_day,
        "session": session,
        "is_market_open": session in {"regular", "closing_watch"},
        "next_open_at": next_open.isoformat() if next_open else "",
    }


def _next_open_datetime(
    local: datetime,
    *,
    calendar: KRXHolidayCalendar | None = None,
) -> datetime | None:
    open_today = local.replace(hour=9, minute=0, second=0, microsecond=0)
    probe = local.date()
    if local < open_today and _is_open_date(probe, calendar=calendar):
        return open_today
    for _ in range(14):
        probe = probe + timedelta(days=1)
        if _is_open_date(probe, calendar=calendar):
            return datetime.combine(probe, time(9, 0), tzinfo=KST)
    return None


def _is_open_date(value: date, *, calendar: KRXHolidayCalendar | None = None) -> bool:
    if value.weekday() >= 5:
        return False
    if calendar is None:
        return True
    return calendar.is_open_day(value)


def normalize_account_assets(assets: list[dict[str, Any]]) -> dict[str, Any]:
    cash_krw = 0.0
    positions: list[dict[str, Any]] = []
    for row in assets:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        symbol = str(row.get("asset") or "").strip()
        value_krw = _safe_float(row.get("value_krw"))
        if kind == "cash" and symbol.upper() == "KRW":
            cash_krw += max(value_krw or _safe_float(row.get("qty")), 0.0)
            continue
        if kind != "position" or not _is_symbol(symbol):
            continue
        qty = _safe_float(row.get("qty"))
        available_qty = _safe_float(row.get("available") or qty)
        avg_price = _safe_float(row.get("avg_price"))
        mark_price = _safe_float(row.get("mark_price"))
        pnl_krw = _safe_float(row.get("pnl_krw"))
        cost_krw = avg_price * qty if avg_price > 0 and qty > 0 else value_krw - pnl_krw
        pnl_pct = (pnl_krw / cost_krw * 100.0) if cost_krw > 0 else 0.0
        positions.append(
            {
                "symbol": symbol,
                "name": str(row.get("asset_name") or symbol),
                "qty": qty,
                "available_qty": available_qty,
                "avg_price": avg_price,
                "mark_price": mark_price,
                "value_krw": value_krw,
                "unrealized_pnl_krw": pnl_krw,
                "unrealized_pnl_pct": pnl_pct,
            }
        )
    position_value_krw = sum(float(row.get("value_krw") or 0.0) for row in positions)
    total_value_krw = cash_krw + position_value_krw
    for row in positions:
        value_krw = float(row.get("value_krw") or 0.0)
        row["position_weight"] = value_krw / total_value_krw if total_value_krw > 0 else 0.0
    return {
        "status": "ok",
        "captured_at": utc_now_iso(),
        "account_label": "국장1",
        "cash_krw": cash_krw,
        "position_value_krw": position_value_krw,
        "total_value_krw": total_value_krw,
        "position_count": len(positions),
        "positions": sorted(
            positions,
            key=lambda row: float(row.get("value_krw") or 0.0),
            reverse=True,
        ),
    }


class MarketJudgmentRepository:
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
                CREATE TABLE IF NOT EXISTS quote_snapshots (
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
                CREATE INDEX IF NOT EXISTS idx_quote_symbol_time
                    ON quote_snapshots(symbol, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cash_krw REAL,
                    position_value_krw REAL,
                    total_value_krw REAL,
                    position_count INTEGER,
                    error_message TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS judgment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    market_session TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL,
                    query TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    source_snapshot_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS symbol_judgments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    stance TEXT NOT NULL,
                    account_action TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    confidence REAL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    triggers_json TEXT NOT NULL DEFAULT '[]',
                    data_gaps_json TEXT NOT NULL DEFAULT '[]',
                    quote_json TEXT NOT NULL DEFAULT '{}',
                    position_json TEXT NOT NULL DEFAULT '{}',
                    strategy_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(run_id) REFERENCES judgment_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_symbol_judgments_run
                    ON symbol_judgments(run_id, symbol);
                """
            )

    def save_quotes(self, quotes: list[dict[str, Any]]) -> None:
        if not quotes:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO quote_snapshots (
                    symbol, name, price, change, change_pct, open_price, high_price,
                    low_price, volume, trading_value, source, fetched_at, status,
                    error_message, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row.get("symbol") or ""),
                        str(row.get("name") or row.get("symbol") or ""),
                        row.get("price"),
                        row.get("change"),
                        row.get("change_pct"),
                        row.get("open_price"),
                        row.get("high_price"),
                        row.get("low_price"),
                        row.get("volume"),
                        row.get("trading_value"),
                        str(row.get("source") or ""),
                        str(row.get("fetched_at") or utc_now_iso()),
                        str(row.get("status") or "ok"),
                        str(row.get("error_message") or ""),
                        _json_dumps(row.get("raw") or {}),
                    )
                    for row in quotes
                ],
            )

    def save_account(self, account: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_snapshots (
                    captured_at, status, cash_krw, position_value_krw,
                    total_value_krw, position_count, error_message, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(account.get("captured_at") or utc_now_iso()),
                    str(account.get("status") or "ok"),
                    account.get("cash_krw"),
                    account.get("position_value_krw"),
                    account.get("total_value_krw"),
                    int(account.get("position_count") or 0),
                    str(account.get("error_message") or ""),
                    _json_dumps(account),
                ),
            )

    def save_judgment_run(
        self,
        *,
        run: dict[str, Any],
        judgments: list[dict[str, Any]],
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO judgment_runs (
                    run_at, market_session, status, mode, model, query,
                    error_message, prompt_json, response_json, source_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.get("run_at") or utc_now_iso()),
                    str(run.get("market_session") or "closed"),
                    str(run.get("status") or "ok"),
                    str(run.get("mode") or "deterministic"),
                    str(run.get("model") or ""),
                    str(run.get("query") or ""),
                    str(run.get("error_message") or ""),
                    _json_dumps(run.get("prompt") or {}),
                    _json_dumps(run.get("response") or {}),
                    _json_dumps(run.get("source_snapshot") or {}),
                ),
            )
            run_id = int(cursor.lastrowid)
            conn.executemany(
                """
                INSERT INTO symbol_judgments (
                    run_id, symbol, name, stance, account_action, horizon,
                    confidence, reasons_json, risks_json, triggers_json,
                    data_gaps_json, quote_json, position_json, strategy_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        str(row.get("symbol") or ""),
                        str(row.get("name") or row.get("symbol") or ""),
                        str(row.get("stance") or "confirm"),
                        str(row.get("account_action") or "hold"),
                        str(row.get("horizon") or "unknown"),
                        float(row.get("confidence") or 0.0),
                        _json_dumps(row.get("reasons") or []),
                        _json_dumps(row.get("risks") or []),
                        _json_dumps(row.get("triggers") or []),
                        _json_dumps(row.get("data_gaps") or []),
                        _json_dumps(row.get("quote") or {}),
                        _json_dumps(row.get("position") or {}),
                        _json_dumps(row.get("strategy") or {}),
                    )
                    for row in judgments
                ],
            )
            return run_id

    def latest_quotes(self, *, limit: int = 100) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT q.*
                FROM quote_snapshots q
                JOIN (
                    SELECT symbol, MAX(fetched_at) AS fetched_at
                    FROM quote_snapshots
                    GROUP BY symbol
                ) latest
                ON latest.symbol = q.symbol AND latest.fetched_at = q.fetched_at
                ORDER BY q.fetched_at DESC, q.symbol ASC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return {
            "status": "ok",
            "count": len(rows),
            "items": [self._row_to_quote(row) for row in rows],
        }

    def latest_account(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_snapshots ORDER BY captured_at DESC, id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {"status": "missing", "account_label": "국장1", "positions": []}
        payload = _json_loads(row["raw_json"], {})
        return payload if isinstance(payload, dict) else {"status": "invalid"}

    def latest_successful_account(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM account_snapshots
                WHERE status IN ('ok', 'stale')
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {"status": "missing", "account_label": "국장1", "positions": []}
        payload = _json_loads(row["raw_json"], {})
        return payload if isinstance(payload, dict) else {"status": "invalid"}

    def latest_judgment(self) -> dict[str, Any]:
        with self._connect() as conn:
            run = conn.execute(
                "SELECT * FROM judgment_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if run is None:
                return {"status": "missing", "judgments": []}
            rows = conn.execute(
                "SELECT * FROM symbol_judgments WHERE run_id = ? ORDER BY id ASC",
                (int(run["id"]),),
            ).fetchall()
        return {
            "status": str(run["status"] or "ok"),
            "run": self._row_to_run(run),
            "judgments": [self._row_to_judgment(row) for row in rows],
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            quote_count = int(conn.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0])
            account_count = int(conn.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0])
            run_count = int(conn.execute("SELECT COUNT(*) FROM judgment_runs").fetchone()[0])
            latest_run = conn.execute(
                "SELECT run_at, status, mode FROM judgment_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return {
            "status": "ok",
            "db_path": str(self.path),
            "quote_count": quote_count,
            "account_count": account_count,
            "run_count": run_count,
            "latest_run_at": str(latest_run["run_at"]) if latest_run else "",
            "latest_run_status": str(latest_run["status"]) if latest_run else "missing",
            "latest_run_mode": str(latest_run["mode"]) if latest_run else "",
        }

    @staticmethod
    def _row_to_quote(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": row["symbol"],
            "name": row["name"],
            "price": row["price"],
            "change": row["change"],
            "change_pct": row["change_pct"],
            "open_price": row["open_price"],
            "high_price": row["high_price"],
            "low_price": row["low_price"],
            "volume": row["volume"],
            "trading_value": row["trading_value"],
            "source": row["source"],
            "fetched_at": row["fetched_at"],
            "status": row["status"],
            "error_message": row["error_message"],
            "raw": _json_loads(row["raw_json"], {}),
        }

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_at": row["run_at"],
            "market_session": row["market_session"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "query": row["query"],
            "error_message": row["error_message"],
            "source_snapshot": _json_loads(row["source_snapshot_json"], {}),
        }

    @staticmethod
    def _row_to_judgment(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": row["symbol"],
            "name": row["name"],
            "stance": row["stance"],
            "account_action": row["account_action"],
            "horizon": row["horizon"],
            "confidence": row["confidence"],
            "reasons": _json_loads(row["reasons_json"], []),
            "risks": _json_loads(row["risks_json"], []),
            "triggers": _json_loads(row["triggers_json"], []),
            "data_gaps": _json_loads(row["data_gaps_json"], []),
            "quote": _json_loads(row["quote_json"], {}),
            "position": _json_loads(row["position_json"], {}),
            "strategy": _json_loads(row["strategy_json"], {}),
        }


class MarketQuoteService:
    def __init__(
        self,
        kis: KISAdapter,
        *,
        use_naver_fallback: bool = True,
        timeout_sec: float = 8.0,
    ) -> None:
        self.kis = kis
        self.use_naver_fallback = use_naver_fallback
        self.timeout_sec = max(float(timeout_sec), 1.0)

    async def collect_quotes(
        self,
        symbols: list[str],
        *,
        concurrency: int = 4,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(max(int(concurrency), 1))

        async def fetch(symbol: str) -> dict[str, Any]:
            async with semaphore:
                return await self.fetch_quote(symbol)

        tasks = [fetch(symbol) for symbol in symbols if _is_symbol(symbol)]
        if not tasks:
            return []
        return list(await asyncio.gather(*tasks))

    async def fetch_quote(self, symbol: str) -> dict[str, Any]:
        code = str(symbol or "").strip()
        fetched_at = utc_now_iso()
        try:
            payload = await self.kis.fetch_domestic_quote(code)
            return normalize_kis_quote(payload, fetched_at=fetched_at)
        except Exception as exc:
            kis_error = str(exc)
            if self.use_naver_fallback:
                try:
                    quote = await self._fetch_naver_quote(code)
                    quote["fallback_reason"] = kis_error
                    return quote
                except Exception as fallback_exc:
                    return {
                        "symbol": code,
                        "name": code,
                        "price": None,
                        "change": None,
                        "change_pct": None,
                        "source": "kis+naver",
                        "fetched_at": fetched_at,
                        "status": "error",
                        "error_message": f"{kis_error}; fallback:{fallback_exc}",
                        "raw": {},
                    }
            return {
                "symbol": code,
                "name": code,
                "price": None,
                "change": None,
                "change_pct": None,
                "source": "kis",
                "fetched_at": fetched_at,
                "status": "error",
                "error_message": kis_error,
                "raw": {},
            }

    async def _fetch_naver_quote(self, symbol: str) -> dict[str, Any]:
        code = str(symbol or "").strip()
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
        timeout = httpx.Timeout(self.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        response.encoding = response.encoding or "euc-kr"
        return parse_naver_quote_html(
            response.text,
            symbol=code,
            source_url=url,
            fetched_at=utc_now_iso(),
        )


def normalize_kis_quote(payload: dict[str, Any], *, fetched_at: str | None = None) -> dict[str, Any]:
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    price = _safe_float(payload.get("price") or raw.get("stck_prpr"))
    change = _safe_float(raw.get("prdy_vrss") or raw.get("prdy_vrss_sign"))
    sign = str(raw.get("prdy_vrss_sign") or "").strip()
    if sign in {"5", "-"} and change > 0:
        change = -change
    change_pct = _safe_float(raw.get("prdy_ctrt"))
    if change < 0 and change_pct > 0:
        change_pct = -change_pct
    return {
        "symbol": str(payload.get("symbol") or raw.get("stck_shrn_iscd") or ""),
        "name": str(payload.get("name") or raw.get("hts_kor_isnm") or payload.get("symbol") or ""),
        "price": price if price > 0 else None,
        "change": change,
        "change_pct": change_pct,
        "open_price": _safe_float(raw.get("stck_oprc")) or None,
        "high_price": _safe_float(raw.get("stck_hgpr")) or None,
        "low_price": _safe_float(raw.get("stck_lwpr")) or None,
        "volume": _safe_float(raw.get("acml_vol")) or None,
        "trading_value": _safe_float(raw.get("acml_tr_pbmn")) or None,
        "source": "kis",
        "fetched_at": fetched_at or utc_now_iso(),
        "status": "ok",
        "error_message": "",
        "raw": raw,
    }


def parse_naver_quote_html(
    raw_html: str,
    *,
    symbol: str,
    source_url: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    text = str(raw_html or "")
    title_match = re.search(r"<title>\s*([^:<]+)", text, re.IGNORECASE)
    name = _clean_text(title_match.group(1) if title_match else symbol, limit=60)
    price = _first_number(
        [
            r'class="no_today"[\s\S]{0,500}?<span class="blind">([0-9,]+)</span>',
            r"현재가\s*([0-9,]+)",
        ],
        text,
    )
    change_pct = _first_number(
        [
            r'class="no_exday"[\s\S]{0,900}?<span class="blind">([+-]?[0-9.]+)%?</span>',
            r"등락률\s*([+-]?[0-9.]+)",
        ],
        text,
    )
    volume = _first_number([r'거래량</span>[\s\S]{0,300}?<span class="blind">([0-9,]+)</span>'], text)
    if price <= 0:
        raise ValueError("naver quote price missing")
    return {
        "symbol": symbol,
        "name": name or symbol,
        "price": price,
        "change": None,
        "change_pct": change_pct if change_pct else None,
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "volume": volume if volume else None,
        "trading_value": None,
        "source": "naver",
        "source_url": source_url,
        "fetched_at": fetched_at or utc_now_iso(),
        "status": "ok",
        "error_message": "",
        "raw": {"source_url": source_url},
    }


def _first_number(patterns: list[str], text: str) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _safe_float(match.group(1))
    return 0.0


class MarketJudgmentEngine:
    def __init__(
        self,
        *,
        config: MarketJudgmentConfig,
        kis: KISAdapter,
        llm_bridge: LLMBridge,
        strategy_engine: StrategyEngine,
        report_repository: ReportRepository | None = None,
        fundamentals_repository: FundamentalsRepository | None = None,
        rag_store: RAGQueryStore | None = None,
        research_feed_provider: ResearchFeedProvider | None = None,
        calendar: KRXHolidayCalendar | None = None,
        watchlist: list[str] | None = None,
    ) -> None:
        self.config = config
        self.kis = kis
        self.llm_bridge = llm_bridge
        self.strategy_engine = strategy_engine
        self.report_repository = report_repository
        self.fundamentals_repository = fundamentals_repository
        self.rag_store = rag_store
        self.research_feed_provider = research_feed_provider
        self.calendar = calendar or KRXHolidayCalendar()
        self.watchlist = [symbol for symbol in list(watchlist or []) if _is_symbol(symbol)]
        self.repository = MarketJudgmentRepository(config.db_path)
        self.quote_service = MarketQuoteService(
            kis,
            use_naver_fallback=config.use_naver_fallback,
            timeout_sec=config.request_timeout_sec,
        )

    def clock(self, *, now: datetime | None = None) -> dict[str, Any]:
        return build_market_clock(now=now, calendar=self.calendar)

    def status(self) -> dict[str, Any]:
        return {
            **self.repository.status(),
            "config": {
                "quote_interval_sec": int(self.config.quote_interval_sec),
                "judge_interval_sec": int(self.config.judge_interval_sec),
                "max_symbols": int(self.config.max_symbols),
                "llm_max_symbols": int(self.config.llm_max_symbols),
                "use_naver_fallback": bool(self.config.use_naver_fallback),
            },
        }

    async def collect_account(self) -> dict[str, Any]:
        try:
            assets = await self.kis.fetch_balance_assets()
            account = normalize_account_assets(assets)
        except Exception as exc:
            fallback = self.repository.latest_successful_account()
            if str(fallback.get("status") or "") in {"ok", "stale"}:
                account = {
                    **fallback,
                    "status": "stale",
                    "stale": True,
                    "last_attempt_at": utc_now_iso(),
                    "error_message": str(exc),
                }
            else:
                account = {
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
        self.repository.save_account(account)
        return account

    async def run_once(self, *, use_llm: bool = True) -> dict[str, Any]:
        run_at = utc_now_iso()
        clock = self.clock()
        research_feed = self.research_feed_provider() if self.research_feed_provider else None
        account = await self.collect_account()
        strategy_payload = self._strategy_payload(research_feed)
        symbols = self._build_universe(
            account=account,
            strategy_payload=strategy_payload,
        )
        quotes = await self.quote_service.collect_quotes(
            symbols,
            concurrency=self.config.quote_concurrency,
        )
        self.repository.save_quotes(quotes)
        focus_symbols = self._focus_symbols(
            account=account,
            strategy_payload=strategy_payload,
            quotes=quotes,
        )
        prompt = self._build_prompt(
            clock=clock,
            account=account,
            strategy_payload=strategy_payload,
            quotes=quotes,
            focus_symbols=focus_symbols,
            research_feed=research_feed,
        )
        response_payload: dict[str, Any] = {}
        mode = "deterministic"
        status = "llm_unavailable"
        error_message = ""
        judgments = self._deterministic_judgments(
            focus_symbols=focus_symbols,
            account=account,
            strategy_payload=strategy_payload,
            quotes=quotes,
        )

        if use_llm and self.llm_bridge.ready and focus_symbols:
            llm_result = await self._run_llm(prompt)
            response_payload = llm_result
            if bool(llm_result.get("ok")):
                parsed = self._parse_llm_judgments(
                    llm_result.get("content"),
                    focus_symbols=focus_symbols,
                    account=account,
                    strategy_payload=strategy_payload,
                    quotes=quotes,
                )
                if parsed:
                    judgments = parsed
                    mode = "llm"
                    status = "ok"
                else:
                    status = "llm_empty"
                    error_message = "llm returned no valid symbol judgments"
            else:
                status = "llm_error"
                error_message = str(llm_result.get("error") or "llm failed")
        elif not use_llm:
            status = "quotes_only"
        run = {
            "run_at": run_at,
            "market_session": str(clock.get("session") or "closed"),
            "status": status,
            "mode": mode,
            "model": self.llm_bridge.resolved_model,
            "query": self.config.query,
            "error_message": error_message,
            "prompt": prompt,
            "response": response_payload,
            "source_snapshot": {
                "clock": clock,
                "account_status": account.get("status"),
                "quote_count": len(quotes),
                "focus_symbols": focus_symbols,
            },
        }
        should_persist_judgment = status != "quotes_only" or self.latest_judgment().get("status") == "missing"
        run_id = (
            self.repository.save_judgment_run(run=run, judgments=judgments)
            if should_persist_judgment
            else 0
        )
        payload = {
            "status": status,
            "run": {k: v for k, v in run.items() if k not in {"prompt", "response"}},
            "run_id": run_id,
            "clock": clock,
            "account": account,
            "quotes": quotes,
            "focus_symbols": focus_symbols,
            "judgments": judgments,
            "disclaimer": "정보 제공용 장중 판단 기록이며 매매 추천이 아닙니다.",
        }
        return payload

    def latest_quotes(self, *, limit: int = 100) -> dict[str, Any]:
        return self.repository.latest_quotes(limit=limit)

    def latest_account(self) -> dict[str, Any]:
        return self.repository.latest_account()

    def latest_judgment(self) -> dict[str, Any]:
        payload = self.repository.latest_judgment()
        payload["account"] = self.repository.latest_account()
        payload["disclaimer"] = "정보 제공용 장중 판단 기록이며 매매 추천이 아닙니다."
        return payload

    def _strategy_payload(self, research_feed: dict[str, Any] | None) -> dict[str, Any]:
        try:
            return self.strategy_engine.build_candidates(
                query=self.config.query,
                research_feed=research_feed,
                limit=max(int(self.config.llm_max_symbols), 3),
            )
        except Exception as exc:
            return {
                "status": "error",
                "query": self.config.query,
                "candidates": [],
                "exclusions": [],
                "error_message": str(exc),
            }

    def _build_universe(
        self,
        *,
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
    ) -> list[str]:
        out: list[str] = []
        out.extend(str(row.get("symbol") or "") for row in list(account.get("positions") or []))
        out.extend(self.watchlist)
        for key in ("candidates", "exclusions"):
            for row in list(strategy_payload.get(key) or []):
                if isinstance(row, dict):
                    out.append(str(row.get("symbol") or ""))
        try:
            signals = self.strategy_engine.list_external_signals(limit=300)
        except Exception:
            signals = {}
        for row in list(signals.get("items") or []):
            if isinstance(row, dict):
                out.append(str(row.get("symbol") or ""))
        if self.report_repository is not None:
            try:
                reports = self.report_repository.search(
                    query="",
                    category="company_analysis",
                    limit=max(int(self.config.max_symbols), 1),
                )
            except Exception:
                reports = []
            for row in reports:
                out.append(str(row.get("symbol") or ""))
        unique = [symbol for symbol in dict.fromkeys(out) if _is_symbol(symbol)]
        return unique[: max(int(self.config.max_symbols), 1)]

    def _focus_symbols(
        self,
        *,
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
    ) -> list[str]:
        out: list[str] = []
        out.extend(str(row.get("symbol") or "") for row in list(account.get("positions") or []))
        for row in list(strategy_payload.get("candidates") or []):
            if isinstance(row, dict):
                out.append(str(row.get("symbol") or ""))
        if not out:
            movers = sorted(
                quotes,
                key=lambda row: abs(float(row.get("change_pct") or 0.0)),
                reverse=True,
            )
            out.extend(str(row.get("symbol") or "") for row in movers)
        return [symbol for symbol in dict.fromkeys(out) if _is_symbol(symbol)][
            : max(int(self.config.llm_max_symbols), 1)
        ]

    def _build_prompt(
        self,
        *,
        clock: dict[str, Any],
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
        focus_symbols: list[str],
        research_feed: dict[str, Any] | None,
    ) -> dict[str, Any]:
        quote_by_symbol = {str(row.get("symbol") or ""): row for row in quotes}
        candidates = {
            str(row.get("symbol") or ""): row
            for row in list(strategy_payload.get("candidates") or [])
            if isinstance(row, dict)
        }
        positions = {
            str(row.get("symbol") or ""): row
            for row in list(account.get("positions") or [])
            if isinstance(row, dict)
        }
        context_rows: list[dict[str, Any]] = []
        for symbol in focus_symbols:
            context_rows.append(
                {
                    "symbol": symbol,
                    "quote": _trim_quote(quote_by_symbol.get(symbol) or {}),
                    "position": positions.get(symbol) or {},
                    "strategy": _trim_strategy(candidates.get(symbol) or {}),
                    "valuation": self._valuation(symbol),
                    "rag": self._rag_context(symbol),
                }
            )
        previous = self.repository.latest_judgment()
        return {
            "task": "한국 주식 장중 판단을 JSON으로 작성한다. 주문 지시가 아니라 정보 제공용 판단 기록이다.",
            "rules": [
                "매수/매도 명령으로 쓰지 말고 관심, 확인, 보류, 회피, 리스크 관리 표현만 사용한다.",
                "국장1 계좌의 현금, 보유 비중, 평가손익, 보유 종목을 반드시 반영한다.",
                "리포트/RAG/밸류/고래/세시반/시세가 부족하면 data_gaps에 적는다.",
                "자동주문, 수량, 주문가격, 즉시매수 같은 표현은 금지한다.",
            ],
            "allowed_account_actions": sorted(ACCOUNT_ACTIONS),
            "allowed_stances": sorted(STANCES),
            "allowed_horizons": sorted(HORIZONS),
            "query": self.config.query,
            "clock": clock,
            "account": account,
            "research": {
                "updated_at": (research_feed or {}).get("updated_at") if isinstance(research_feed, dict) else "",
                "count": (research_feed or {}).get("count") if isinstance(research_feed, dict) else 0,
            },
            "strategy_summary": {
                "status": strategy_payload.get("status"),
                "score_method_version": strategy_payload.get("score_method_version"),
                "regime": strategy_payload.get("regime"),
                "sources": strategy_payload.get("sources"),
            },
            "symbols": context_rows,
            "previous_judgment": previous.get("run") if isinstance(previous, dict) else {},
            "output_schema": {
                "judgments": [
                    {
                        "symbol": "6-digit KRX code",
                        "stance": "watch|confirm|hold|risk_check|avoid|stale",
                        "account_action": "hold|watch_add|avoid_add|trim_watch|risk_check|new_watch",
                        "horizon": "intraday|short_term|mid_term|long_term|unknown",
                        "confidence": "0.0-1.0",
                        "reasons": ["근거"],
                        "risks": ["반론"],
                        "triggers": ["확인 조건"],
                        "data_gaps": ["부족한 자료"],
                    }
                ]
            },
        }

    def _valuation(self, symbol: str) -> dict[str, Any]:
        if self.fundamentals_repository is None:
            return {"status": "missing"}
        try:
            return self.fundamentals_repository.latest(symbol) or {"status": "missing"}
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}

    def _rag_context(self, symbol: str) -> list[dict[str, Any]]:
        if self.rag_store is None:
            return []
        try:
            return [
                {
                    "report_id": row.get("report_id"),
                    "broker": row.get("broker"),
                    "published_at": row.get("published_at"),
                    "content": _clean_text(row.get("content"), limit=240),
                }
                for row in self.rag_store.query(query=self.config.query, symbol=symbol, limit=3)
            ]
        except Exception:
            return []

    async def _run_llm(self, prompt: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.llm_bridge.resolved_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only JSON. This is advisory market observation, not an order system.",
                },
                {"role": "user", "content": _json_dumps(prompt)},
            ],
        }
        return await self.llm_bridge.complete(payload)

    def _parse_llm_judgments(
        self,
        content: Any,
        *,
        focus_symbols: list[str],
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        text = str(content or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows = parsed.get("judgments") if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            return []
        base = self._context_maps(
            account=account,
            strategy_payload=strategy_payload,
            quotes=quotes,
        )
        out: list[dict[str, Any]] = []
        allowed = set(focus_symbols)
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            if symbol not in allowed:
                continue
            confidence = _safe_float(row.get("confidence"))
            if confidence > 1.0:
                confidence = confidence / 100.0
            stance = str(row.get("stance") or "confirm").strip()
            account_action = str(row.get("account_action") or "hold").strip()
            horizon = str(row.get("horizon") or "unknown").strip()
            out.append(
                {
                    "symbol": symbol,
                    "name": base["names"].get(symbol) or symbol,
                    "stance": stance if stance in STANCES else "confirm",
                    "account_action": account_action if account_action in ACCOUNT_ACTIONS else "hold",
                    "horizon": horizon if horizon in HORIZONS else "unknown",
                    "confidence": max(0.0, min(confidence, 1.0)),
                    "reasons": _string_list(row.get("reasons"), limit=4),
                    "risks": _string_list(row.get("risks"), limit=4),
                    "triggers": _string_list(row.get("triggers"), limit=4),
                    "data_gaps": _string_list(row.get("data_gaps"), limit=4),
                    "quote": base["quotes"].get(symbol) or {},
                    "position": base["positions"].get(symbol) or {},
                    "strategy": base["strategies"].get(symbol) or {},
                }
            )
        return out

    def _deterministic_judgments(
        self,
        *,
        focus_symbols: list[str],
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        base = self._context_maps(
            account=account,
            strategy_payload=strategy_payload,
            quotes=quotes,
        )
        out: list[dict[str, Any]] = []
        for symbol in focus_symbols:
            quote = base["quotes"].get(symbol) or {}
            position = base["positions"].get(symbol) or {}
            strategy = base["strategies"].get(symbol) or {}
            change_pct = _safe_float(quote.get("change_pct"))
            pnl_pct = _safe_float(position.get("unrealized_pnl_pct"))
            weight = _safe_float(position.get("position_weight"))
            has_position = bool(position)
            data_gaps: list[str] = []
            if not quote or str(quote.get("status") or "") != "ok":
                data_gaps.append("시세")
            if not strategy:
                data_gaps.append("전략후보")
            if has_position and (pnl_pct <= -5.0 or change_pct <= -3.0):
                stance = "risk_check"
                action = "risk_check"
                risks = ["보유 손익 또는 당일 움직임이 약해 리스크 관리 확인 필요"]
            elif has_position and weight >= 0.25:
                stance = "hold"
                action = "trim_watch"
                risks = ["국장1 내 단일 종목 비중이 높아 비중 점검 필요"]
            elif has_position:
                stance = "hold"
                action = "hold"
                risks = ["추가 자료 확인 전 보유 판단 유지"]
            elif int(strategy.get("score") or 0) >= 68 and change_pct >= -2.0:
                stance = "watch"
                action = "new_watch"
                risks = ["신규 후보는 가격/거래대금 확인 전 관망"]
            else:
                stance = "confirm"
                action = "watch_add" if strategy else "avoid_add"
                risks = ["근거와 시세 동조 여부 확인 필요"]
            reasons = []
            if has_position:
                reasons.append(f"국장1 보유 종목, 평가손익 {pnl_pct:.2f}%")
            if strategy:
                reasons.append(
                    f"전략 적합도 {strategy.get('score', '-')} · confidence {strategy.get('confidence', '-')}"
                )
            if quote.get("price"):
                reasons.append(f"현재가 {int(float(quote.get('price') or 0)):,}원, 등락률 {change_pct:.2f}%")
            out.append(
                {
                    "symbol": symbol,
                    "name": base["names"].get(symbol) or symbol,
                    "stance": stance,
                    "account_action": action,
                    "horizon": "short_term",
                    "confidence": 0.42 if data_gaps else 0.55,
                    "reasons": reasons or ["LLM 미사용 deterministic 요약"],
                    "risks": risks,
                    "triggers": ["거래대금, 섹터 수급, 리포트 근거 방향 재확인"],
                    "data_gaps": data_gaps,
                    "quote": quote,
                    "position": position,
                    "strategy": strategy,
                }
            )
        return out

    def _context_maps(
        self,
        *,
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        quotes_by_symbol = {str(row.get("symbol") or ""): row for row in quotes if isinstance(row, dict)}
        positions = {
            str(row.get("symbol") or ""): row
            for row in list(account.get("positions") or [])
            if isinstance(row, dict)
        }
        strategies = {
            str(row.get("symbol") or ""): row
            for row in list(strategy_payload.get("candidates") or [])
            if isinstance(row, dict)
        }
        names: dict[str, str] = {}
        for source in (quotes_by_symbol, positions, strategies):
            for symbol, row in source.items():
                name = str(row.get("name") or "").strip()
                if name:
                    names[symbol] = name
        if self.report_repository is not None:
            missing = [symbol for symbol in set([*quotes_by_symbol, *positions, *strategies]) if symbol not in names]
            if missing:
                try:
                    names.update(self.report_repository.resolve_symbol_names(missing))
                except Exception:
                    pass
        return {
            "quotes": quotes_by_symbol,
            "positions": positions,
            "strategies": strategies,
            "names": names,
        }


def _string_list(value: Any, *, limit: int = 4) -> list[str]:
    if isinstance(value, list):
        rows = value
    else:
        rows = [value] if value else []
    out = [_clean_text(row, limit=180) for row in rows if _clean_text(row, limit=180)]
    return out[: max(int(limit), 1)]


def _trim_quote(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "symbol",
            "name",
            "price",
            "change",
            "change_pct",
            "volume",
            "trading_value",
            "source",
            "fetched_at",
            "status",
            "error_message",
        )
        if key in row
    }


def _trim_strategy(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "symbol",
            "name",
            "score",
            "score_method_version",
            "suitability",
            "confidence",
            "stance",
            "reasons",
            "risks",
            "checks",
            "data_warnings",
            "data_coverage",
            "valuation",
        )
        if key in row
    }
