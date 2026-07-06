from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from tradecraft.services.db_retention import (
    SQLiteRetentionPruner,
    gzip_base64_archive_text,
)


class StrategySignalProvider(Protocol):
    def list_external_signals(
        self,
        source_id: str = "",
        symbol: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class MarketPulseConfig:
    db_path: str = ".runtime/market_pulse.db"
    enabled: bool = True
    timeout_sec: float = 8.0
    index_codes: str = "KOSPI,KOSDAQ,KPI200,FUT"
    sector_signal_limit: int = 240
    investor_flow_enabled: bool = True
    investor_flow_markets: str = "KOSPI,KOSDAQ,FUT"
    program_trading_enabled: bool = True
    program_trading_markets: str = "KOSPI,KOSDAQ"
    fx_enabled: bool = True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def _clean_text(value: Any, *, limit: int = 200) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(int(limit), 1)]


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


def _first_number(patterns: list[str], text: str) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _safe_float(match.group(1))
            if value:
                return value
    return 0.0


def _direction_from_text(text: str, *, change: float, change_pct: float) -> str:
    lowered = text.lower()
    if "하락" in text or "quotient dn" in lowered or "ico_down" in lowered:
        return "down"
    if "상승" in text or "quotient up" in lowered or "ico_up" in lowered:
        return "up"
    if change < 0 or change_pct < 0:
        return "down"
    if change > 0 or change_pct > 0:
        return "up"
    return "flat"


INDEX_LABELS = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
    "KPI200": "KOSPI200",
    "FUT": "KOSPI200 선물",
}

INVESTOR_MARKETS = {
    "KOSPI": {"name": "KOSPI", "sosok": ""},
    "KOSDAQ": {"name": "KOSDAQ", "sosok": "02"},
    "FUT": {"name": "KOSPI200 선물", "sosok": "03"},
}

PROGRAM_MARKETS = {
    "KOSPI": {"name": "KOSPI", "sosok": ""},
    "KOSDAQ": {"name": "KOSDAQ", "sosok": "02"},
}


def parse_naver_index_html(
    raw_html: str,
    *,
    code: str,
    source_url: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    text = str(raw_html or "")
    value = _first_number(
        [
            r'id\s*=\s*["\']now_value["\'][^>]*>\s*(?:<[^>]+>\s*)?([0-9,.]+)',
            r'<em[^>]+id\s*=\s*["\']now_value["\'][^>]*>\s*([0-9,.]+)',
        ],
        text,
    )
    change = _first_number(
        [
            r'id\s*=\s*["\']change_value_and_rate["\'][\s\S]{0,180}?<span[^>]*>\s*([+-]?[0-9,.]+)',
            r'id\s*=\s*["\']change_value["\'][\s\S]{0,220}?<span[^>]*>\s*([+-]?[0-9,.]+)',
        ],
        text,
    )
    change_pct = _first_number(
        [
            r'id\s*=\s*["\']change_value_and_rate["\'][\s\S]{0,220}?([+-]?[0-9.]+)\s*%',
            r'id\s*=\s*["\']change_rate["\'][\s\S]{0,220}?([+-]?[0-9.]+)\s*%',
        ],
        text,
    )
    snippet_match = re.search(
        r'(?:id\s*=\s*["\']quotient["\'][\s\S]{0,500}|id\s*=\s*["\']change_value["\'][\s\S]{0,500})',
        text,
        re.IGNORECASE,
    )
    snippet = snippet_match.group(0) if snippet_match else text[:800]
    direction = _direction_from_text(snippet, change=change, change_pct=change_pct)
    if direction == "down":
        change = -abs(change)
        change_pct = -abs(change_pct)
    elif direction == "up":
        change = abs(change)
        change_pct = abs(change_pct)
    if value <= 0:
        raise ValueError(f"naver index value missing: {code}")
    return {
        "code": code,
        "name": INDEX_LABELS.get(code, code),
        "value": value,
        "change": change,
        "change_pct": change_pct,
        "direction": direction,
        "source": "naver",
        "source_url": source_url,
        "fetched_at": fetched_at or utc_now_iso(),
        "status": "ok",
        "error_message": "",
    }


def _html_cells(row_html: str) -> list[str]:
    cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row_html, re.IGNORECASE)
    return [_clean_text(cell.replace("<br>", " "), limit=120) for cell in cells]


def _flow_bias(foreign: float, institution: float) -> str:
    if foreign > 0 and institution > 0:
        return "foreign_institution_buy"
    if foreign < 0 and institution < 0:
        return "foreign_institution_sell"
    if foreign > 0:
        return "foreign_buy"
    if institution > 0:
        return "institution_buy"
    return "neutral"


def parse_naver_investor_flow_html(
    raw_html: str,
    *,
    market: str,
    source_url: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    text = str(raw_html or "")
    for match in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", text, re.IGNORECASE):
        cells = _html_cells(match.group(1))
        if len(cells) < 11:
            continue
        as_of = cells[0]
        if not re.match(r"^(?:\d{2}\.\d{2}\.\d{2}|\d{1,2}:\d{2})$", as_of):
            continue
        numbers = [_safe_float(cell) for cell in cells[1:11]]
        foreign = numbers[1]
        institution = numbers[2]
        return {
            "market": market,
            "name": INVESTOR_MARKETS.get(market, {}).get("name", market),
            "as_of": as_of,
            "individual_net_buy_100m_krw": numbers[0],
            "foreign_net_buy_100m_krw": foreign,
            "institution_net_buy_100m_krw": institution,
            "finance_investment_net_buy_100m_krw": numbers[3],
            "insurance_net_buy_100m_krw": numbers[4],
            "trust_net_buy_100m_krw": numbers[5],
            "bank_net_buy_100m_krw": numbers[6],
            "other_finance_net_buy_100m_krw": numbers[7],
            "pension_net_buy_100m_krw": numbers[8],
            "other_corp_net_buy_100m_krw": numbers[9],
            "foreign_institution_sum_100m_krw": foreign + institution,
            "bias": _flow_bias(foreign, institution),
            "source": "naver",
            "source_url": source_url,
            "fetched_at": fetched_at or utc_now_iso(),
            "status": "ok",
            "error_message": "",
        }
    raise ValueError(f"naver investor flow row missing: {market}")


def _program_bias(total_net: float) -> str:
    if total_net >= 5_000:
        return "program_buy"
    if total_net <= -5_000:
        return "program_sell"
    return "neutral"


def parse_naver_program_trading_html(
    raw_html: str,
    *,
    market: str,
    source_url: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    text = str(raw_html or "")
    for match in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", text, re.IGNORECASE):
        cells = _html_cells(match.group(1))
        if len(cells) < 10:
            continue
        as_of = cells[0]
        if not re.match(r"^(?:\d{2}\.\d{2}\.\d{2}|\d{1,2}:\d{2})$", as_of):
            continue
        numbers = [_safe_float(cell) for cell in cells[1:10]]
        total_net = numbers[8]
        return {
            "market": market,
            "name": PROGRAM_MARKETS.get(market, {}).get("name", market),
            "as_of": as_of,
            "arbitrage_buy_100m_krw": numbers[0],
            "arbitrage_sell_100m_krw": numbers[1],
            "arbitrage_net_buy_100m_krw": numbers[2],
            "non_arbitrage_buy_100m_krw": numbers[3],
            "non_arbitrage_sell_100m_krw": numbers[4],
            "non_arbitrage_net_buy_100m_krw": numbers[5],
            "total_buy_100m_krw": numbers[6],
            "total_sell_100m_krw": numbers[7],
            "total_net_buy_100m_krw": total_net,
            "bias": _program_bias(total_net),
            "source": "naver",
            "source_url": source_url,
            "fetched_at": fetched_at or utc_now_iso(),
            "status": "ok",
            "error_message": "",
        }
    raise ValueError(f"naver program trading row missing: {market}")


def parse_naver_fx_html(
    raw_html: str,
    *,
    source_url: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    text = str(raw_html or "")
    match = re.search(
        r"marketindexCd=FX_USDKRW[\s\S]{0,900}?<span[^>]+class=[\"']value[\"'][^>]*>\s*([0-9,.]+)"
        r"[\s\S]{0,240}?<span[^>]+class=[\"']change[\"'][^>]*>\s*([+-]?[0-9,.]+)"
        r"[\s\S]{0,180}?<span[^>]+class=[\"']blind[\"'][^>]*>\s*(상승|하락|보합)",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("naver USD/KRW row missing")
    value = _safe_float(match.group(1))
    change = _safe_float(match.group(2))
    direction_text = match.group(3)
    direction = _direction_from_text(direction_text, change=change, change_pct=0.0)
    if direction == "down":
        change = -abs(change)
    elif direction == "up":
        change = abs(change)
    time_match = re.search(r"<span[^>]+class=[\"']time[\"'][^>]*>\s*([^<]+)", text, re.IGNORECASE)
    source_match = re.search(
        r"<span[^>]+class=[\"']source[\"'][^>]*>\s*([^<]+)",
        text,
        re.IGNORECASE,
    )
    return {
        "code": "USD/KRW",
        "name": "USD/KRW",
        "value": value,
        "change": change,
        "direction": direction,
        "as_of": _clean_text(time_match.group(1), limit=40) if time_match else "",
        "source_name": _clean_text(source_match.group(1), limit=80) if source_match else "",
        "source": "naver",
        "source_url": source_url,
        "fetched_at": fetched_at or utc_now_iso(),
        "status": "ok",
        "error_message": "",
    }


def build_futures_basis(indices: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {
        str(row.get("code") or ""): row
        for row in indices
        if row.get("status") == "ok"
    }
    spot = rows.get("KPI200")
    futures = rows.get("FUT")
    if not spot or not futures:
        return {"status": "missing", "data_gaps": ["kospi200_or_futures_missing"]}
    spot_value = _safe_float(spot.get("value"))
    futures_value = _safe_float(futures.get("value"))
    if spot_value <= 0 or futures_value <= 0:
        return {"status": "missing", "data_gaps": ["kospi200_or_futures_value_missing"]}
    basis = round(futures_value - spot_value, 2)
    basis_pct = round((basis / spot_value) * 100.0, 3)
    if basis >= 0.5:
        basis_signal = "contango"
    elif basis <= -0.5:
        basis_signal = "backwardation"
    else:
        basis_signal = "flat"
    return {
        "status": "ok",
        "spot_code": "KPI200",
        "futures_code": "FUT",
        "spot_value": spot_value,
        "futures_value": futures_value,
        "basis": basis,
        "basis_pct": basis_pct,
        "basis_signal": basis_signal,
        "spot_change_pct": _safe_float(spot.get("change_pct")),
        "futures_change_pct": _safe_float(futures.get("change_pct")),
        "source": "computed:naver_index",
    }


def _index_change_map(indices: list[dict[str, Any]]) -> dict[str, float]:
    return {
        str(row.get("code") or ""): _safe_float(row.get("change_pct"))
        for row in indices
        if row.get("status") == "ok"
    }


def _core_derivative_divergence(index_map: dict[str, float]) -> bool:
    core_values = [
        index_map[code]
        for code in ("KOSPI", "KOSDAQ")
        if code in index_map
    ]
    derivative_values = [
        index_map[code]
        for code in ("KPI200", "FUT")
        if code in index_map
    ]
    if not core_values or not derivative_values:
        return False
    core_avg = sum(core_values) / len(core_values)
    derivative_avg = sum(derivative_values) / len(derivative_values)
    return (
        core_avg >= 1.5
        and derivative_avg <= -1.0
        and core_avg - derivative_avg >= 3.0
    ) or (
        core_avg <= -1.5
        and derivative_avg >= 1.0
        and derivative_avg - core_avg >= 3.0
    )


def _risk_cap_from_components(
    *,
    equity_flow: float,
    futures_foreign: float,
    program_total: float,
    fx_change: float,
    dispersion: float,
    derivative_divergence: bool = False,
) -> dict[str, Any]:
    reasons: list[str] = []
    if equity_flow <= -30_000 and futures_foreign < 0:
        reasons.append("foreign_flow_pressure")
    if program_total <= -30_000:
        reasons.append("program_sell_pressure")
    if fx_change >= 10:
        reasons.append("usd_krw_up_pressure")
    if dispersion >= 1.5:
        reasons.append("index_dispersion_high")
    if derivative_divergence:
        reasons.append("index_derivative_divergence")

    cap = 100.0
    if len(reasons) >= 3:
        cap = 65.0
    elif len(reasons) == 2:
        cap = 75.0
    if derivative_divergence:
        cap = min(cap, 70.0)
    return {"active": cap < 100.0, "cap": cap, "reasons": reasons}


class MarketPulseRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
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
                CREATE TABLE IF NOT EXISTS market_pulse_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    trading_day TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    regime TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    indices_json TEXT NOT NULL DEFAULT '[]',
                    sector_json TEXT NOT NULL DEFAULT '{}',
                    block_alignment_json TEXT NOT NULL DEFAULT '[]',
                    risk_flags_json TEXT NOT NULL DEFAULT '[]',
                    data_gaps_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_market_pulse_captured
                    ON market_pulse_snapshots(captured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_market_pulse_captured_id
                    ON market_pulse_snapshots(captured_at DESC, id DESC);
                """
            )

    def save_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO market_pulse_snapshots (
                    captured_at, trading_day, status, regime, score,
                    indices_json, sector_json, block_alignment_json,
                    risk_flags_json, data_gaps_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("captured_at") or utc_now_iso()),
                    str(payload.get("trading_day") or ""),
                    str(payload.get("status") or "ok"),
                    str(payload.get("regime") or ""),
                    float(payload.get("score") or 0.0),
                    _json_dumps(payload.get("indices") or []),
                    _json_dumps(payload.get("sectors") or {}),
                    _json_dumps(payload.get("block_alignment") or []),
                    _json_dumps(payload.get("risk_flags") or []),
                    _json_dumps(payload.get("data_gaps") or []),
                    _json_dumps(payload),
                ),
            )
            row_id = int(cursor.lastrowid or 0)
        return {**payload, "id": row_id}

    def prune_history(
        self,
        *,
        retention_days: int = 7,
        archive_retention_days: int = 0,
        compact_recent_snapshot_count: int = 96,
        compact_raw_min_chars: int = 5_000,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if int(retention_days) <= 0:
            return {"status": "skipped", "reason": "retention_disabled"}
        base_now = now or datetime.now(timezone.utc)
        cutoff = (base_now - timedelta(days=int(retention_days))).isoformat()
        with self._connect() as conn:
            archived = self._archive_snapshots_before_cutoff(conn, cutoff=cutoff)
            deleted = int(
                conn.execute(
                    "DELETE FROM market_pulse_snapshots WHERE captured_at < ?",
                    (cutoff,),
                ).rowcount
                or 0
            )
            archive_deleted = 0
            if int(archive_retention_days) > 0:
                archive_cutoff = (
                    base_now - timedelta(days=int(archive_retention_days))
                ).isoformat()
                archive_deleted = self._delete_archive_before_cutoff(
                    conn,
                    cutoff=archive_cutoff,
                )
            compacted = self._compact_old_active_raw_payloads(
                conn,
                recent_snapshot_count=compact_recent_snapshot_count,
                min_chars=compact_raw_min_chars,
                compacted_at=base_now.isoformat(),
            )
        archive_compacted = SQLiteRetentionPruner(self.path).compact_archive_columns(
            table="market_pulse_snapshots_archive",
            columns=(
                "indices_json",
                "sector_json",
                "block_alignment_json",
                "risk_flags_json",
                "data_gaps_json",
                "raw_json",
            ),
            batch_size=5000,
            vacuum=False,
        )
        vacuumed = False
        if (
            deleted > 0
            or archive_deleted > 0
            or compacted > 0
            or int(archive_compacted.get("compacted") or 0) > 0
        ):
            with sqlite3.connect(str(self.path), isolation_level=None) as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "cutoff": cutoff,
            "snapshot_deleted": deleted,
            "archive_deleted": {
                "market_pulse_snapshots_archive": archive_deleted,
            },
            "archived": {"market_pulse_snapshots": archived},
            "compacted": {
                "market_pulse_snapshots": compacted,
                "recent_snapshot_count": max(int(compact_recent_snapshot_count), 0),
                "raw_min_chars": max(int(compact_raw_min_chars), 0),
            },
            "archive_compacted": {
                "market_pulse_snapshots_archive": archive_compacted,
            },
            "vacuumed": vacuumed,
        }

    @staticmethod
    def _compact_old_active_raw_payloads(
        conn: sqlite3.Connection,
        *,
        recent_snapshot_count: int,
        min_chars: int,
        compacted_at: str,
    ) -> int:
        keep_count = max(int(recent_snapshot_count), 0)
        threshold = max(int(min_chars), 0)
        keep_ids = [
            int(row[0])
            for row in conn.execute(
                """
                SELECT id
                FROM market_pulse_snapshots
                ORDER BY captured_at DESC, id DESC
                LIMIT ?
                """,
                (keep_count,),
            ).fetchall()
        ]
        params: list[Any] = [threshold, "%market_pulse_raw_payload_retention%"]
        keep_filter = ""
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            keep_filter = f"AND id NOT IN ({placeholders})"
            params.extend(keep_ids)
        rows = conn.execute(
            f"""
            SELECT id, captured_at, trading_day, status, regime, score, length(raw_json)
            FROM market_pulse_snapshots
            WHERE length(raw_json) > ?
              AND raw_json NOT LIKE ?
              {keep_filter}
            """,
            tuple(params),
        ).fetchall()
        compacted = 0
        for row in rows:
            marker = {
                "compacted": True,
                "compacted_at": compacted_at,
                "reason": "market_pulse_raw_payload_retention",
                "snapshot_id": int(row[0]),
                "captured_at": str(row[1] or ""),
                "trading_day": str(row[2] or ""),
                "status": str(row[3] or ""),
                "regime": str(row[4] or ""),
                "score": float(row[5] or 0.0),
                "recent_snapshot_count": keep_count,
                "original_chars": {"raw_json": int(row[6] or 0)},
            }
            conn.execute(
                """
                UPDATE market_pulse_snapshots
                SET raw_json = ?
                WHERE id = ?
                """,
                (_json_dumps(marker), int(row[0])),
            )
            compacted += 1
        return compacted

    @staticmethod
    def _delete_archive_before_cutoff(
        conn: sqlite3.Connection,
        *,
        cutoff: str,
    ) -> int:
        exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'market_pulse_snapshots_archive'
            """
        ).fetchone()
        if not exists:
            return 0
        return int(
            conn.execute(
                "DELETE FROM market_pulse_snapshots_archive WHERE captured_at < ?",
                (cutoff,),
            ).rowcount
            or 0
        )

    @staticmethod
    def _archive_snapshots_before_cutoff(
        conn: sqlite3.Connection,
        *,
        cutoff: str,
    ) -> int:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS market_pulse_snapshots_archive AS "
            "SELECT * FROM market_pulse_snapshots WHERE 0"
        )
        rows = conn.execute(
            "SELECT * FROM market_pulse_snapshots WHERE captured_at < ?",
            (cutoff,),
        ).fetchall()
        if not rows:
            return 0
        columns = [
            str(row[1])
            for row in conn.execute(
                'PRAGMA table_info("market_pulse_snapshots")'
            ).fetchall()
        ]
        raw_index = columns.index("raw_json")
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(f'"{column}"' for column in columns)
        archived = 0
        for row in rows:
            values = list(row)
            values[raw_index] = gzip_base64_archive_text(values[raw_index])
            conn.execute(
                f"INSERT INTO market_pulse_snapshots_archive ({column_sql}) "
                f"VALUES ({placeholders})",
                values,
            )
            archived += 1
        return archived

    def latest(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM market_pulse_snapshots
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {"status": "missing", "db_path": str(self.path)}
        return self._row_to_snapshot(row)

    def history(self, *, limit: int = 20) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM market_pulse_snapshots
                ORDER BY captured_at DESC, id DESC
                LIMIT ?
                """,
                (max(min(int(limit), 200), 1),),
            ).fetchall()
        return {
            "status": "ok",
            "db_path": str(self.path),
            "items": [self._row_to_snapshot(row) for row in rows],
        }

    def status(self) -> dict[str, Any]:
        latest = self.latest()
        with self._connect() as conn:
            count = int(
                conn.execute("SELECT COUNT(*) FROM market_pulse_snapshots").fetchone()[0]
            )
        return {
            "status": "ok",
            "db_path": str(self.path),
            "snapshot_count": count,
            "latest": latest if latest.get("status") != "missing" else {},
        }

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        raw = _json_loads(row["raw_json"], {})
        if isinstance(raw, dict) and raw and not raw.get("compacted"):
            return {**raw, "id": int(row["id"])}
        return {
            "id": int(row["id"]),
            "captured_at": row["captured_at"],
            "trading_day": row["trading_day"],
            "status": row["status"],
            "regime": row["regime"],
            "score": float(row["score"] or 0.0),
            "indices": _json_loads(row["indices_json"], []),
            "sectors": _json_loads(row["sector_json"], {}),
            "block_alignment": _json_loads(row["block_alignment_json"], []),
            "risk_flags": _json_loads(row["risk_flags_json"], []),
            "data_gaps": _json_loads(row["data_gaps_json"], []),
        }


class MarketPulseService:
    def __init__(
        self,
        *,
        config: MarketPulseConfig,
        strategy_signal_provider: StrategySignalProvider | None = None,
    ) -> None:
        self.config = config
        self.repository = MarketPulseRepository(config.db_path)
        self.strategy_signal_provider = strategy_signal_provider

    async def collect(
        self,
        *,
        clock: dict[str, Any] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        quotes: list[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"status": "disabled", "captured_at": utc_now_iso()}
        captured_at = utc_now_iso()
        indices = await self._collect_indices()
        investor_flows = await self._collect_investor_flows()
        program_trading = await self._collect_program_trading()
        fx = await self._collect_fx_snapshot()
        futures = build_futures_basis(indices)
        sectors = self._sector_summary()
        block_alignment = self._block_alignment(
            latest={"sectors": sectors},
            blocks=blocks or [],
            quotes=quotes or [],
        )
        data_gaps = self._data_gaps(
            indices=indices,
            sectors=sectors,
            investor_flows=investor_flows,
            program_trading=program_trading,
            fx=fx,
            futures=futures,
        )
        regime, _legacy_score, risk_flags = self._classify(
            indices=indices,
            sectors=sectors,
            investor_flows=investor_flows,
            program_trading=program_trading,
            fx=fx,
            futures=futures,
        )
        block_exposure = self._block_exposure(
            latest={"sectors": sectors, "regime": regime},
            blocks=blocks or [],
            quotes=quotes or [],
        )
        score_components = self._score_components(
            indices=indices,
            sectors=sectors,
            investor_flows=investor_flows,
            program_trading=program_trading,
            fx=fx,
            futures=futures,
            block_exposure=block_exposure,
        )
        score = score_components["total_score"]
        status = "ok" if any(row.get("status") == "ok" for row in indices) else "error"
        if data_gaps and status == "ok":
            status = "partial"
        payload = {
            "status": status,
            "captured_at": captured_at,
            "trading_day": str((clock or {}).get("date") or captured_at[:10]),
            "regime": regime,
            "score": score,
            "score_method_version": "v3",
            "score_components": score_components,
            "indices": indices,
            "investor_flows": investor_flows,
            "program_trading": program_trading,
            "futures": futures,
            "fx": fx,
            "sectors": sectors,
            "block_alignment": block_alignment,
            "block_exposure": block_exposure,
            "risk_flags": risk_flags,
            "data_gaps": data_gaps,
            "source": {
                "indices": "naver",
                "investor_flows": "naver",
                "program_trading": "naver",
                "futures": "computed:naver_index",
                "fx": "naver_marketindex",
                "sector_signals": "strategy_insights.after_close_330",
            },
        }
        return self.repository.save_snapshot(payload)

    def latest(self, **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return self.repository.latest()

    def history(self, *, limit: int = 20) -> dict[str, Any]:
        return self.repository.history(limit=limit)

    def status(self) -> dict[str, Any]:
        return {
            **self.repository.status(),
            "enabled": bool(self.config.enabled),
            "index_codes": self._index_codes(),
            "investor_flow_enabled": bool(self.config.investor_flow_enabled),
            "investor_flow_markets": self._investor_flow_markets(),
            "program_trading_enabled": bool(self.config.program_trading_enabled),
            "program_trading_markets": self._program_trading_markets(),
            "fx_enabled": bool(self.config.fx_enabled),
        }

    def context_for_blocks(
        self,
        *,
        blocks: list[dict[str, Any]] | None = None,
        quotes: list[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        _ = kwargs
        latest = self.latest()
        if latest.get("status") == "missing":
            return latest
        return {
            **latest,
            "block_alignment": self._block_alignment(
                latest=latest,
                blocks=blocks or [],
                quotes=quotes or [],
            ),
            "block_exposure": self._block_exposure(
                latest=latest,
                blocks=blocks or [],
                quotes=quotes or [],
            ),
        }

    async def _collect_indices(self) -> list[dict[str, Any]]:
        codes = self._index_codes()
        timeout = httpx.Timeout(max(float(self.config.timeout_sec), 1.0))
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

        async def fetch(code: str) -> dict[str, Any]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    response = await client.get(url, headers=headers)
                response.raise_for_status()
                response.encoding = response.encoding or "euc-kr"
                return parse_naver_index_html(
                    response.text,
                    code=code,
                    source_url=url,
                    fetched_at=utc_now_iso(),
                )
            except Exception as exc:
                return {
                    "code": code,
                    "name": INDEX_LABELS.get(code, code),
                    "value": None,
                    "change": None,
                    "change_pct": None,
                    "direction": "unknown",
                    "source": "naver",
                    "source_url": url,
                    "fetched_at": utc_now_iso(),
                    "status": "error",
                    "error_message": str(exc),
                }

        return list(await asyncio.gather(*(fetch(code) for code in codes)))

    def _index_codes(self) -> list[str]:
        out: list[str] = []
        for item in str(self.config.index_codes or "").replace(";", ",").split(","):
            code = item.strip().upper()
            if code and code not in out:
                out.append(code)
        return out or ["KOSPI", "KOSDAQ", "KPI200", "FUT"]

    def _investor_flow_markets(self) -> list[str]:
        out: list[str] = []
        for item in str(self.config.investor_flow_markets or "").replace(";", ",").split(","):
            market = item.strip().upper()
            if market in INVESTOR_MARKETS and market not in out:
                out.append(market)
        return out or ["KOSPI", "KOSDAQ", "FUT"]

    def _program_trading_markets(self) -> list[str]:
        out: list[str] = []
        for item in str(self.config.program_trading_markets or "").replace(";", ",").split(","):
            market = item.strip().upper()
            if market in PROGRAM_MARKETS and market not in out:
                out.append(market)
        return out or ["KOSPI", "KOSDAQ"]

    async def _collect_investor_flows(self) -> list[dict[str, Any]]:
        if not self.config.investor_flow_enabled:
            return []
        markets = self._investor_flow_markets()
        timeout = httpx.Timeout(max(float(self.config.timeout_sec), 1.0))
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

        async def fetch(market: str) -> dict[str, Any]:
            spec = INVESTOR_MARKETS.get(market, {})
            sosok = str(spec.get("sosok") or "")
            overview_url = "https://finance.naver.com/sise/sise_trans_style.naver"
            if sosok:
                overview_url = f"{overview_url}?sosok={sosok}"
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    overview = await client.get(overview_url, headers=headers)
                    overview.raise_for_status()
                    overview.encoding = overview.encoding or "euc-kr"
                    bizdate_match = re.search(
                        r"investorDealTrendTime\.naver\?bizdate=(\d+)&sosok=([^\"']*)",
                        overview.text,
                    )
                    if not bizdate_match:
                        raise ValueError(f"investor flow bizdate missing: {market}")
                    bizdate = bizdate_match.group(1)
                    data_url = (
                        "https://finance.naver.com/sise/investorDealTrendTime.naver"
                        f"?bizdate={bizdate}&sosok={sosok}"
                    )
                    response = await client.get(data_url, headers=headers)
                    response.raise_for_status()
                    response.encoding = response.encoding or "euc-kr"
                return parse_naver_investor_flow_html(
                    response.text,
                    market=market,
                    source_url=data_url,
                    fetched_at=utc_now_iso(),
                )
            except Exception as exc:
                return {
                    "market": market,
                    "name": str(spec.get("name") or market),
                    "as_of": "",
                    "source": "naver",
                    "source_url": overview_url,
                    "fetched_at": utc_now_iso(),
                    "status": "error",
                    "error_message": str(exc),
                }

        return list(await asyncio.gather(*(fetch(market) for market in markets)))

    async def _collect_program_trading(self) -> list[dict[str, Any]]:
        if not self.config.program_trading_enabled:
            return []
        markets = self._program_trading_markets()
        timeout = httpx.Timeout(max(float(self.config.timeout_sec), 1.0))
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

        async def fetch(market: str) -> dict[str, Any]:
            spec = PROGRAM_MARKETS.get(market, {})
            sosok = str(spec.get("sosok") or "")
            overview_url = "https://finance.naver.com/sise/sise_program.naver"
            if sosok:
                overview_url = f"{overview_url}?sosok={sosok}"
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    overview = await client.get(overview_url, headers=headers)
                    overview.raise_for_status()
                    overview.encoding = overview.encoding or "euc-kr"
                    bizdate_match = re.search(
                        r"programDealTrendTime\.naver\?bizdate=(\d+)&sosok=([^\"']*)",
                        overview.text,
                    )
                    if not bizdate_match:
                        raise ValueError(f"program trading bizdate missing: {market}")
                    bizdate = bizdate_match.group(1)
                    data_url = (
                        "https://finance.naver.com/sise/programDealTrendTime.naver"
                        f"?bizdate={bizdate}&sosok={sosok}"
                    )
                    response = await client.get(data_url, headers=headers)
                    response.raise_for_status()
                    response.encoding = response.encoding or "euc-kr"
                return parse_naver_program_trading_html(
                    response.text,
                    market=market,
                    source_url=data_url,
                    fetched_at=utc_now_iso(),
                )
            except Exception as exc:
                return {
                    "market": market,
                    "name": str(spec.get("name") or market),
                    "as_of": "",
                    "source": "naver",
                    "source_url": overview_url,
                    "fetched_at": utc_now_iso(),
                    "status": "error",
                    "error_message": str(exc),
                }

        return list(await asyncio.gather(*(fetch(market) for market in markets)))

    async def _collect_fx_snapshot(self) -> dict[str, Any]:
        if not self.config.fx_enabled:
            return {"status": "disabled"}
        url = "https://finance.naver.com/marketindex/"
        timeout = httpx.Timeout(max(float(self.config.timeout_sec), 1.0))
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
            response.raise_for_status()
            response.encoding = response.encoding or "euc-kr"
            return parse_naver_fx_html(
                response.text,
                source_url=url,
                fetched_at=utc_now_iso(),
            )
        except Exception as exc:
            return {
                "code": "USD/KRW",
                "name": "USD/KRW",
                "source": "naver",
                "source_url": url,
                "fetched_at": utc_now_iso(),
                "status": "error",
                "error_message": str(exc),
            }

    def _sector_summary(self) -> dict[str, Any]:
        if self.strategy_signal_provider is None:
            return {"status": "missing", "items": [], "data_gaps": ["strategy_signal_provider_missing"]}
        try:
            payload = self.strategy_signal_provider.list_external_signals(
                source_id="after_close_330",
                limit=max(int(self.config.sector_signal_limit), 1),
            )
        except Exception as exc:
            return {"status": "error", "items": [], "error_message": str(exc)}
        rows = [row for row in list(payload.get("items") or []) if isinstance(row, dict)]
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            sector = self._sector_from_signal(row)
            if not sector:
                continue
            item = grouped.setdefault(
                sector,
                {
                    "name": sector,
                    "signal_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "avg_strength": 0.0,
                    "symbols": [],
                    "latest_as_of": "",
                    "summaries": [],
                },
            )
            strength = _safe_float(row.get("strength"))
            item["signal_count"] += 1
            item["avg_strength"] += strength
            direction = str(row.get("direction") or "").lower()
            if direction == "positive":
                item["positive_count"] += 1
            elif direction == "negative":
                item["negative_count"] += 1
            symbol = str(row.get("symbol") or "").strip()
            if symbol and symbol not in item["symbols"]:
                item["symbols"].append(symbol)
            as_of = str(row.get("as_of") or "")
            if as_of > str(item.get("latest_as_of") or ""):
                item["latest_as_of"] = as_of
            summary = _clean_text(row.get("summary"), limit=180)
            if summary and len(item["summaries"]) < 3:
                item["summaries"].append(summary)
        sectors = []
        for item in grouped.values():
            count = max(int(item["signal_count"]), 1)
            item["avg_strength"] = round(float(item["avg_strength"]) / count, 1)
            item["direction"] = (
                "positive"
                if int(item["positive_count"]) >= int(item["negative_count"])
                else "negative"
            )
            item["symbols"] = item["symbols"][:8]
            sectors.append(item)
        sectors.sort(
            key=lambda row: (int(row.get("positive_count") or 0), float(row.get("avg_strength") or 0.0)),
            reverse=True,
        )
        return {
            "status": "ok",
            "source_id": "after_close_330",
            "as_of": str(payload.get("latest_collected_at") or ""),
            "items": sectors[:12],
            "raw_count": len(rows),
        }

    @staticmethod
    def _sector_from_signal(row: dict[str, Any]) -> str:
        summary = str(row.get("summary") or "")
        match = re.search(r"세시반\s+선도\s+섹터\s+[^ ]+\s+([^:：]+)", summary)
        if match:
            return _clean_text(match.group(1), limit=60)
        tags = row.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                text = _clean_text(tag, limit=60)
                if text and text not in {"sesiban", "sector_treemap", "after_close_flow"}:
                    return text
        return ""

    def _block_alignment(
        self,
        *,
        latest: dict[str, Any],
        blocks: list[dict[str, Any]],
        quotes: list[dict[str, Any]] | dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        quote_map = self._quote_map(quotes)
        sector_items = list(((latest.get("sectors") or {}).get("items") or []))
        hot_sectors = [
            str(row.get("name") or "")
            for row in sector_items
            if str(row.get("direction") or "positive") == "positive"
        ]
        out: list[dict[str, Any]] = []
        for block in blocks:
            symbol = str(block.get("symbol") or "")
            if not symbol:
                continue
            quote = quote_map.get(symbol) or {}
            raw = quote.get("raw") if isinstance(quote.get("raw"), dict) else {}
            sector = _clean_text(raw.get("bstp_kor_isnm") or block.get("sector"), limit=80)
            market = _clean_text(raw.get("rprs_mrkt_kor_name") or block.get("market"), limit=40)
            alignment = "unknown"
            matched_sector = ""
            if sector:
                for hot in hot_sectors:
                    if hot and (hot in sector or sector in hot):
                        alignment = "strong"
                        matched_sector = hot
                        break
                if alignment == "unknown":
                    alignment = "mixed"
            out.append(
                {
                    "block_id": block.get("block_id"),
                    "symbol": symbol,
                    "name": block.get("name") or quote.get("name") or symbol,
                    "sector": sector,
                    "market": market,
                    "sector_alignment": alignment,
                    "matched_sector_signal": matched_sector,
                    "market_alignment": self._market_alignment(latest, market),
                }
            )
        return out

    def _block_exposure(
        self,
        *,
        latest: dict[str, Any],
        blocks: list[dict[str, Any]],
        quotes: list[dict[str, Any]] | dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        quote_map = self._quote_map(quotes)
        sector_counts: dict[str, int] = {}
        market_counts: dict[str, int] = {}
        block_count = 0
        for block in blocks:
            symbol = str(block.get("symbol") or "")
            if not symbol:
                continue
            block_count += 1
            sector, market = self._block_sector_market(block=block, quote=quote_map.get(symbol) or {})
            sector_counts[sector or "unknown"] = sector_counts.get(sector or "unknown", 0) + 1
            market_counts[market or "unknown"] = market_counts.get(market or "unknown", 0) + 1
        if block_count <= 0:
            return {
                "status": "ok",
                "block_count": 0,
                "sector_weights": {},
                "market_weights": {},
                "concentration_flags": [],
                "pressure_flags": [],
            }
        sector_weights = self._weights(sector_counts, block_count)
        market_weights = self._weights(market_counts, block_count)
        concentration_flags: list[str] = []
        concentrated_sectors: list[str] = []
        for sector, weight in sector_weights.items():
            if block_count >= 3 and weight >= 0.6:
                concentration_flags.append(f"sector_concentration:{sector}")
                concentrated_sectors.append(sector)
        for market, weight in market_weights.items():
            if block_count >= 5 and weight >= 0.8:
                concentration_flags.append(f"market_concentration:{market}")
        pressure_flags: list[str] = []
        if str(latest.get("regime") or "") == "risk_off":
            pressure_flags.append("broad_market_pressure")
        positive_sectors = self._positive_sector_names(latest)
        for sector in concentrated_sectors:
            if not self._sector_supported(sector, positive_sectors):
                pressure_flags.append(f"sector_not_supported:{sector}")
        return {
            "status": "caution" if concentration_flags or pressure_flags else "ok",
            "block_count": block_count,
            "sector_weights": sector_weights,
            "market_weights": market_weights,
            "concentration_flags": concentration_flags,
            "pressure_flags": pressure_flags,
        }

    @staticmethod
    def _block_sector_market(block: dict[str, Any], quote: dict[str, Any]) -> tuple[str, str]:
        raw = quote.get("raw") if isinstance(quote.get("raw"), dict) else {}
        sector = _clean_text(
            raw.get("bstp_kor_isnm")
            or quote.get("sector")
            or block.get("sector"),
            limit=80,
        )
        market = _clean_text(
            raw.get("rprs_mrkt_kor_name")
            or quote.get("market")
            or block.get("market"),
            limit=40,
        )
        return sector, market

    @staticmethod
    def _weights(counts: dict[str, int], total: int) -> dict[str, float]:
        if total <= 0:
            return {}
        return {
            key: round(value / total, 4)
            for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        }

    @staticmethod
    def _positive_sector_names(latest: dict[str, Any]) -> list[str]:
        sector_items = list(((latest.get("sectors") or {}).get("items") or []))
        return [
            str(row.get("name") or "")
            for row in sector_items
            if str(row.get("direction") or "positive") == "positive"
        ]

    @staticmethod
    def _sector_supported(sector: str, positive_sectors: list[str]) -> bool:
        return any(hot and (hot in sector or sector in hot) for hot in positive_sectors)

    @staticmethod
    def _quote_map(
        quotes: list[dict[str, Any]] | dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if isinstance(quotes, dict):
            return {str(key): value for key, value in quotes.items() if isinstance(value, dict)}
        return {
            str(row.get("symbol") or ""): row
            for row in list(quotes or [])
            if isinstance(row, dict)
        }

    @staticmethod
    def _market_alignment(latest: dict[str, Any], market: str) -> str:
        market_text = _clean_text(market, limit=40)
        if not market_text:
            return "unknown"
        upper_market = market_text.upper()
        if "KOSDAQ" in upper_market or "코스닥" in market_text:
            code = "KOSDAQ"
        elif "KOSPI" in upper_market or "코스피" in market_text:
            code = "KOSPI"
        else:
            return "unknown"
        for row in list(latest.get("indices") or []):
            if str(row.get("code") or "") == code:
                pct = _safe_float(row.get("change_pct"))
                if pct >= 0.4:
                    return "positive"
                if pct <= -0.4:
                    return "negative"
                return "neutral"
        return "unknown"

    @staticmethod
    def _data_gaps(
        *,
        indices: list[dict[str, Any]],
        sectors: dict[str, Any],
        investor_flows: list[dict[str, Any]] | None = None,
        program_trading: list[dict[str, Any]] | None = None,
        fx: dict[str, Any] | None = None,
        futures: dict[str, Any] | None = None,
    ) -> list[str]:
        gaps: list[str] = []
        failed = [row.get("code") for row in indices if row.get("status") != "ok"]
        if failed:
            gaps.append(f"index_fetch_failed:{','.join(str(item) for item in failed)}")
        if _core_derivative_divergence(_index_change_map(indices)):
            gaps.append("index_coherence_warning:core_vs_derivatives")
        flow_rows = list(investor_flows or [])
        flow_failed = [row.get("market") for row in flow_rows if row.get("status") != "ok"]
        if flow_failed:
            gaps.append(f"investor_flow_fetch_failed:{','.join(str(item) for item in flow_failed)}")
        if flow_rows and not any(row.get("status") == "ok" for row in flow_rows):
            gaps.append("investor_flows_missing")
        program_rows = list(program_trading or [])
        program_failed = [row.get("market") for row in program_rows if row.get("status") != "ok"]
        if program_failed:
            gaps.append(f"program_trading_fetch_failed:{','.join(str(item) for item in program_failed)}")
        if program_rows and not any(row.get("status") == "ok" for row in program_rows):
            gaps.append("program_trading_missing")
        if fx and fx.get("status") not in {"ok", "disabled"}:
            gaps.append("fx_fetch_failed")
        if futures and futures.get("status") not in {"ok", "missing"}:
            gaps.append("futures_basis_failed")
        if not list(sectors.get("items") or []):
            gaps.append("sector_signals_missing")
        return gaps

    @staticmethod
    def _score_components(
        *,
        indices: list[dict[str, Any]],
        sectors: dict[str, Any],
        investor_flows: list[dict[str, Any]] | None = None,
        program_trading: list[dict[str, Any]] | None = None,
        fx: dict[str, Any] | None = None,
        futures: dict[str, Any] | None = None,
        block_exposure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        index_map = _index_change_map(indices)
        derivative_divergence = _core_derivative_divergence(index_map)
        core_index_values = [
            index_map[code]
            for code in ("KOSPI", "KOSDAQ")
            if code in index_map
        ]
        index_reason = ", ".join(
            f"{code} {index_map[code]:.2f}%"
            for code in ("KOSPI", "KOSDAQ")
            if code in index_map
        ) or "No core index data"
        avg = sum(core_index_values) / len(core_index_values) if core_index_values else 0.0
        kospi = index_map.get("KOSPI", 0.0)
        kosdaq = index_map.get("KOSDAQ", 0.0)
        dispersion = abs(kospi - kosdaq) if "KOSPI" in index_map and "KOSDAQ" in index_map else 0.0
        hot_count = len(
            [
                row
                for row in list(sectors.get("items") or [])
                if str(row.get("direction") or "") == "positive"
            ]
        )
        flow_map = {
            str(row.get("market") or ""): row
            for row in list(investor_flows or [])
            if row.get("status") == "ok"
        }
        equity_flow = sum(
            _safe_float((flow_map.get(market) or {}).get("foreign_institution_sum_100m_krw"))
            for market in ("KOSPI", "KOSDAQ")
        )
        futures_foreign = _safe_float((flow_map.get("FUT") or {}).get("foreign_net_buy_100m_krw"))
        flow_adjust = max(-8.0, min(8.0, equity_flow / 50_000.0 * 8.0))
        program_total = sum(
            _safe_float(row.get("total_net_buy_100m_krw"))
            for row in list(program_trading or [])
            if row.get("status") == "ok"
        )
        program_adjust = max(-6.0, min(6.0, program_total / 40_000.0 * 6.0))
        fx_change = _safe_float((fx or {}).get("change")) if (fx or {}).get("status") == "ok" else 0.0
        fx_adjust = -3.0 if fx_change >= 10 else 2.0 if fx_change <= -10 else 0.0
        basis = _safe_float((futures or {}).get("basis")) if (futures or {}).get("status") == "ok" else 0.0
        basis_adjust = max(-3.0, min(3.0, basis / 5.0 * 3.0))
        exposure = block_exposure or {}
        component_values = {
            "index_score": round(avg * 18.0, 1),
            "sector_score": round(min(hot_count, 8) * 2.0, 1),
            "investor_flow_score": round(flow_adjust, 1),
            "program_score": round(program_adjust, 1),
            "fx_risk_score": round(fx_adjust, 1),
            "futures_basis_score": round(basis_adjust, 1),
            "block_exposure_score": 0.0,
        }
        risk_cap = _risk_cap_from_components(
            equity_flow=equity_flow,
            futures_foreign=futures_foreign,
            program_total=program_total,
            fx_change=fx_change,
            dispersion=dispersion,
            derivative_divergence=derivative_divergence,
        )
        uncapped_total = max(0.0, min(100.0, 50.0 + sum(component_values.values())))
        total = min(uncapped_total, float(risk_cap["cap"])) if risk_cap["active"] else uncapped_total
        return {
            "index_score": {
                "score": component_values["index_score"],
                "label": "Index momentum",
                "reason": index_reason,
            },
            "sector_score": {
                "score": component_values["sector_score"],
                "label": "Sector breadth",
                "reason": f"{hot_count} positive sector signals",
            },
            "investor_flow_score": {
                "score": component_values["investor_flow_score"],
                "label": "Investor flow",
                "reason": f"Foreign plus institution equity flow {equity_flow:.0f}",
            },
            "program_score": {
                "score": component_values["program_score"],
                "label": "Program trading",
                "reason": f"Program net buy {program_total:.0f}",
            },
            "fx_risk_score": {
                "score": component_values["fx_risk_score"],
                "label": "FX risk",
                "reason": f"USD/KRW change {fx_change:.1f}",
            },
            "futures_basis_score": {
                "score": component_values["futures_basis_score"],
                "label": "Futures basis",
                "reason": f"KOSPI200 futures basis {basis:.2f}",
            },
            "block_exposure_score": {
                "score": component_values["block_exposure_score"],
                "label": "Block exposure",
                "reason": ", ".join(
                    list(exposure.get("concentration_flags") or [])
                    + list(exposure.get("pressure_flags") or [])
                )
                or "No block exposure pressure",
            },
            "total_score": round(total, 1),
            "risk_cap": risk_cap,
        }

    @staticmethod
    def _classify(
        *,
        indices: list[dict[str, Any]],
        sectors: dict[str, Any],
        investor_flows: list[dict[str, Any]] | None = None,
        program_trading: list[dict[str, Any]] | None = None,
        fx: dict[str, Any] | None = None,
        futures: dict[str, Any] | None = None,
    ) -> tuple[str, float, list[str]]:
        index_map = _index_change_map(indices)
        derivative_divergence = _core_derivative_divergence(index_map)
        core_index_values = [
            index_map[code]
            for code in ("KOSPI", "KOSDAQ")
            if code in index_map
        ]
        kospi = index_map.get("KOSPI", 0.0)
        kosdaq = index_map.get("KOSDAQ", 0.0)
        avg = sum(core_index_values) / len(core_index_values) if core_index_values else 0.0
        dispersion = abs(kospi - kosdaq) if "KOSPI" in index_map and "KOSDAQ" in index_map else 0.0
        hot_count = len(
            [
                row
                for row in list(sectors.get("items") or [])
                if str(row.get("direction") or "") == "positive"
            ]
        )
        flow_map = {
            str(row.get("market") or ""): row
            for row in list(investor_flows or [])
            if row.get("status") == "ok"
        }
        equity_flow = sum(
            _safe_float((flow_map.get(market) or {}).get("foreign_institution_sum_100m_krw"))
            for market in ("KOSPI", "KOSDAQ")
        )
        futures_foreign = _safe_float((flow_map.get("FUT") or {}).get("foreign_net_buy_100m_krw"))
        flow_adjust = max(-8.0, min(8.0, equity_flow / 50_000.0 * 8.0))
        program_total = sum(
            _safe_float(row.get("total_net_buy_100m_krw"))
            for row in list(program_trading or [])
            if row.get("status") == "ok"
        )
        program_adjust = max(-6.0, min(6.0, program_total / 40_000.0 * 6.0))
        fx_change = _safe_float((fx or {}).get("change")) if (fx or {}).get("status") == "ok" else 0.0
        fx_adjust = -3.0 if fx_change >= 10 else 2.0 if fx_change <= -10 else 0.0
        basis = _safe_float((futures or {}).get("basis")) if (futures or {}).get("status") == "ok" else 0.0
        basis_adjust = max(-3.0, min(3.0, basis / 5.0 * 3.0))
        risk_flags: list[str] = []
        if avg <= -0.7:
            regime = "risk_off"
            risk_flags.append("broad_index_pressure")
        elif avg >= 0.7:
            regime = "risk_on"
        elif dispersion >= 1.0 or hot_count >= 5:
            regime = "rotation"
        else:
            regime = "choppy"
        if equity_flow < -30_000 and futures_foreign < 0:
            risk_flags.append("foreign_flow_pressure")
        elif equity_flow > 30_000 and futures_foreign > 0 and regime == "choppy":
            regime = "risk_on"
        if program_total <= -30_000:
            risk_flags.append("program_sell_pressure")
        if fx_change >= 10:
            risk_flags.append("usd_krw_up_pressure")
        if derivative_divergence:
            risk_flags.append("index_derivative_divergence")
        risk_cap = _risk_cap_from_components(
            equity_flow=equity_flow,
            futures_foreign=futures_foreign,
            program_total=program_total,
            fx_change=fx_change,
            dispersion=dispersion,
            derivative_divergence=derivative_divergence,
        )
        if risk_cap["active"] and regime == "risk_on":
            regime = "risk_on_with_pressure"
        score = max(
            0.0,
            min(
                100.0,
                50.0
                + avg * 18.0
                + min(hot_count, 8) * 2.0
                + flow_adjust
                + program_adjust
                + fx_adjust
                + basis_adjust,
            ),
        )
        if risk_cap["active"]:
            score = min(score, float(risk_cap["cap"]))
        if dispersion >= 1.5:
            risk_flags.append("index_dispersion_high")
        return regime, round(score, 1), risk_flags
