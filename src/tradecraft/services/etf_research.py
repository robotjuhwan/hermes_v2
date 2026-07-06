from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.request import Request, urlopen

from tradecraft.services.db_retention import RetentionRule, SQLiteRetentionPruner

DEFAULT_ETF_UNIVERSE_CONFIG = (
    "069500:KODEX 200,"
    "102110:TIGER 200,"
    "091160:KODEX 반도체,"
    "122630:KODEX 레버리지,"
    "229200:KODEX 코스닥150,"
    "360750:TIGER 미국S&P500,"
    "379800:KODEX 미국S&P500,"
    "133690:TIGER 미국나스닥100,"
    "379810:KODEX 미국나스닥100,"
    "396500:TIGER 반도체TOP10,"
    "459580:KODEX CD금리액티브(합성)"
)
NAVER_ETF_ITEM_LIST_URL = (
    "https://finance.naver.com/api/sise/etfItemList.nhn"
    "?etfType=0&targetColumn=market_sum&sortOrder=desc"
)
_NAVER_ETF_UNIVERSE_CACHE: list["ETFUniverseItem"] = []
_NAVER_ETF_UNIVERSE_CACHE_AT: datetime | None = None
_NAVER_ETF_UNIVERSE_FAILURE_UNTIL: datetime | None = None


@dataclass(slots=True)
class ETFUniverseItem:
    symbol: str
    name: str
    category: str = "core"
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ETFMarketSnapshot:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    turnover_krw: float
    source: str
    raw: dict[str, Any]
    captured_at: str
    status: str
    error_message: str = ""


@dataclass(slots=True)
class ETFScore:
    symbol: str
    label: str
    liquidity_score: float
    momentum_score: float
    core_fit_score: float
    risk_score: float
    reasons: list[str]
    risks: list[str]
    scored_at: str


ETF_BRAND_PREFIXES = (
    "KODEX",
    "TIGER",
    "ACE",
    "RISE",
    "SOL",
    "PLUS",
    "KBSTAR",
    "HANARO",
    "ARIRANG",
    "TIMEFOLIO",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_utc_iso(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return _utc_now_iso()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _utc_now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


_ETF_RAW_ALLOWLIST = {
    "stck_shrn_iscd",
    "hts_kor_isnm",
    "stck_prpr",
    "prdy_vrss",
    "prdy_ctrt",
    "prdy_vrss_sign",
    "stck_oprc",
    "stck_hgpr",
    "stck_lwpr",
    "acml_vol",
    "acml_tr_pbmn",
    "aspr_unit",
    "bstp_kor_isnm",
    "d250_hgpr",
    "d250_hgpr_date",
    "d250_lwpr",
    "d250_lwpr_date",
    "etf_type",
    "market_sum",
    "marketSum",
    "nav",
    "source",
    "symbol",
    "name",
}


def _compact_snapshot_raw_for_storage(
    value: dict[str, Any],
    *,
    max_raw_chars: int = 1200,
) -> dict[str, Any]:
    try:
        raw_text = _json_dumps(value)
    except (TypeError, ValueError):
        return {"_raw_compacted": True, "_raw_error": "non_json_serializable"}
    if len(raw_text) <= max(int(max_raw_chars), 1):
        return value

    compact: dict[str, Any] = {
        key: item
        for key, item in value.items()
        if key in _ETF_RAW_ALLOWLIST and isinstance(item, (str, int, float, bool))
    }
    if not compact:
        for key, item in value.items():
            if len(compact) >= 24:
                break
            if isinstance(item, (int, float, bool)):
                compact[str(key)] = item
            elif isinstance(item, str) and len(item) <= 180:
                compact[str(key)] = item
    compact["_raw_compacted"] = True
    compact["_raw_key_count"] = len(value)
    compact["_raw_original_chars"] = len(raw_text)
    compact["_raw_stored_keys"] = len(compact)
    return compact


def _json_list(value: Any) -> list[Any]:
    decoded = _json_loads(value, [])
    if isinstance(decoded, list):
        return decoded
    return []


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _to_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def parse_etf_universe_config(value: str) -> list[ETFUniverseItem]:
    items: list[ETFUniverseItem] = []
    for raw_entry in str(value or "").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        symbol, _, name = entry.partition(":")
        symbol = symbol.strip()
        if not re.fullmatch(r"\d{6}", symbol):
            continue
        clean_name = name.strip() or symbol
        items.append(ETFUniverseItem(symbol=symbol, name=clean_name))
    return items


def _decode_naver_payload(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw
    for encoding in ("cp949", "euc-kr", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_naver_etf_item_list(raw: bytes | str) -> list[ETFUniverseItem]:
    payload = json.loads(_decode_naver_payload(raw))
    result = payload.get("result") if isinstance(payload, dict) else {}
    rows = result.get("etfItemList") if isinstance(result, dict) else []
    items: list[ETFUniverseItem] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("itemcode") or row.get("symbol") or "").strip()
        if not _is_krx_symbol(symbol) or symbol in seen:
            continue
        name = re.sub(
            r"\s+",
            " ",
            str(row.get("itemname") or row.get("name") or symbol).strip(),
        )
        if not name:
            name = symbol
        tab_code = str(row.get("etfTabCode") or "").strip()
        tags = ["naver_etf"]
        if tab_code:
            tags.append(f"tab_{tab_code}")
        seen.add(symbol)
        items.append(
            ETFUniverseItem(
                symbol=symbol,
                name=name,
                category="naver_etf",
                tags=tags,
            )
        )
    return items


def fetch_naver_etf_universe(
    *,
    limit: int = 200,
    timeout_sec: float = 8.0,
) -> list[ETFUniverseItem]:
    global _NAVER_ETF_UNIVERSE_CACHE
    global _NAVER_ETF_UNIVERSE_CACHE_AT
    global _NAVER_ETF_UNIVERSE_FAILURE_UNTIL

    now = datetime.now(timezone.utc)
    max_items = max(int(limit), 1)
    if _NAVER_ETF_UNIVERSE_FAILURE_UNTIL is not None:
        if now < _NAVER_ETF_UNIVERSE_FAILURE_UNTIL:
            if _NAVER_ETF_UNIVERSE_CACHE:
                return _NAVER_ETF_UNIVERSE_CACHE[:max_items]
            raise RuntimeError("naver_etf_universe_backoff_active")
        _NAVER_ETF_UNIVERSE_FAILURE_UNTIL = None

    if (
        _NAVER_ETF_UNIVERSE_CACHE
        and _NAVER_ETF_UNIVERSE_CACHE_AT is not None
        and now - _NAVER_ETF_UNIVERSE_CACHE_AT <= timedelta(hours=1)
    ):
        return _NAVER_ETF_UNIVERSE_CACHE[:max_items]

    request = Request(
        NAVER_ETF_ITEM_LIST_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.naver.com/sise/etf.naver",
        },
    )
    try:
        with urlopen(request, timeout=max(float(timeout_sec), 1.0)) as response:
            raw = response.read()
    except Exception:
        _NAVER_ETF_UNIVERSE_FAILURE_UNTIL = now + timedelta(minutes=30)
        raise
    items = parse_naver_etf_item_list(raw)
    _NAVER_ETF_UNIVERSE_CACHE = items
    _NAVER_ETF_UNIVERSE_CACHE_AT = now
    _NAVER_ETF_UNIVERSE_FAILURE_UNTIL = None
    return items[:max_items]


def expand_default_etf_universe(configured: list[ETFUniverseItem]) -> list[ETFUniverseItem]:
    by_symbol: dict[str, ETFUniverseItem] = {}
    for item in [*configured, *parse_etf_universe_config(DEFAULT_ETF_UNIVERSE_CONFIG)]:
        by_symbol.setdefault(item.symbol, item)
    return list(by_symbol.values())


def _is_krx_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _is_etf_like_directory_row(row: dict[str, Any]) -> bool:
    market = str(row.get("market") or "").strip().upper()
    if market in {"ETF", "ETN"}:
        return True
    name = str(row.get("company_name") or row.get("name") or "").strip().upper()
    return name.startswith(ETF_BRAND_PREFIXES)


def merge_etf_universe(
    *,
    configured: list[ETFUniverseItem],
    symbol_directory_rows: list[dict[str, Any]],
    limit: int = 200,
) -> list[ETFUniverseItem]:
    merged: list[ETFUniverseItem] = []
    seen: set[str] = set()
    max_items = max(int(limit), 1)

    for item in configured:
        symbol = str(item.symbol or "").strip()
        if not _is_krx_symbol(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        merged.append(
            ETFUniverseItem(
                symbol=symbol,
                name=str(item.name or symbol).strip() or symbol,
                category=item.category,
                tags=list(item.tags),
            )
        )
        if len(merged) >= max_items:
            return merged

    for row in symbol_directory_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not _is_krx_symbol(symbol) or symbol in seen:
            continue
        if not _is_etf_like_directory_row(row):
            continue
        name = str(row.get("company_name") or row.get("name") or symbol).strip() or symbol
        seen.add(symbol)
        merged.append(
            ETFUniverseItem(
                symbol=symbol,
                name=name,
                category="expanded",
                tags=["symbol_directory"],
            )
        )
        if len(merged) >= max_items:
            break
    return merged


def _quote_raw(quote: dict[str, Any]) -> dict[str, Any]:
    raw = quote.get("raw")
    if isinstance(raw, dict):
        return raw
    return dict(quote)


def _clean_quote_name(symbol: str, name: Any) -> str:
    text = str(name or "").strip()
    if not text or text == symbol or re.fullmatch(r"\d{6}", text):
        return ""
    return text


def etf_snapshot_from_quote(
    symbol: str,
    name: str,
    quote: dict[str, Any],
    *,
    source: str = "kis_primary",
) -> ETFMarketSnapshot:
    raw = _quote_raw(quote)
    price = _to_float(quote.get("price") or raw.get("stck_prpr") or raw.get("askp1"))
    volume = _to_int(quote.get("volume") or raw.get("acml_vol"))
    turnover_krw = _to_float(quote.get("turnover_krw") or raw.get("acml_tr_pbmn"))
    if turnover_krw <= 0 and price > 0 and volume > 0:
        turnover_krw = price * volume
    quote_name = _clean_quote_name(
        symbol,
        quote.get("name") or raw.get("hts_kor_isnm") or raw.get("bstp_kor_isnm"),
    )
    return ETFMarketSnapshot(
        symbol=symbol,
        name=name or quote_name or symbol,
        price=price,
        change_pct=_to_float(quote.get("change_pct") or raw.get("prdy_ctrt")),
        volume=volume,
        turnover_krw=turnover_krw,
        source=source,
        raw=raw,
        captured_at=_utc_now_iso(),
        status="ok",
        error_message="",
    )


def etf_error_snapshot(
    symbol: str,
    name: str,
    error_message: str,
    *,
    source: str = "kis_primary",
) -> ETFMarketSnapshot:
    return ETFMarketSnapshot(
        symbol=symbol,
        name=name or symbol,
        price=0.0,
        change_pct=0.0,
        volume=0,
        turnover_krw=0.0,
        source=source,
        raw={"error": error_message},
        captured_at=_utc_now_iso(),
        status="error",
        error_message=error_message,
    )


def is_etf_snapshot_stale(
    snapshot: dict[str, Any],
    *,
    stale_sec: int,
    now: datetime | None = None,
) -> bool:
    if str(snapshot.get("status") or "missing").lower() == "missing":
        return True
    captured_at = str(snapshot.get("captured_at") or "").strip()
    if not captured_at:
        return True
    try:
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_sec = (
        current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
    ).total_seconds()
    return age_sec >= max(int(stale_sec), 0)


def stale_etf_symbols(
    repository: "ETFResearchRepository",
    configured: list[ETFUniverseItem],
    *,
    stale_sec: int,
    max_symbols: int,
    rotation_key: str = "",
) -> list[str]:
    symbols: list[str] = []
    rows = list(configured)
    if rotation_key and len(rows) > max(max_symbols, 0) > 0:
        digest = sha256(str(rotation_key).encode("utf-8")).hexdigest()
        offset = int(digest[:8], 16) % len(rows)
        rows = rows[offset:] + rows[:offset]
    for item in rows:
        snapshot = repository.latest_snapshot(item.symbol)
        if is_etf_snapshot_stale(snapshot, stale_sec=stale_sec):
            symbols.append(item.symbol)
        if len(symbols) >= max(max_symbols, 0):
            break
    return symbols


async def collect_etf_research(
    *,
    repository: "ETFResearchRepository",
    configured: list[ETFUniverseItem],
    fetch_quote: Callable[[str], Awaitable[dict[str, Any]]],
    symbols: list[str],
    force: bool = False,
    retention_days: int = 7,
    archive_retention_days: int = 14,
) -> dict[str, Any]:
    if configured:
        repository.upsert_universe(configured)
    by_symbol = {item.symbol: item for item in configured}
    errors: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    collected = 0

    for symbol in symbols:
        name = by_symbol.get(symbol).name if symbol in by_symbol else symbol
        try:
            quote = await fetch_quote(symbol)
            snapshot = etf_snapshot_from_quote(symbol, name, quote)
            collected += 1
        except Exception as exc:
            error_message = str(exc)
            snapshot = etf_error_snapshot(symbol, name, error_message)
            errors.append({"symbol": symbol, "error": error_message})
        score = score_etf_snapshot(snapshot)
        repository.save_snapshot(snapshot)
        repository.save_score(score)
        items.append(
            {
                "symbol": symbol,
                "snapshot": repository.latest_snapshot(symbol),
                "score": repository.latest_score(symbol),
            }
        )

    if errors and collected:
        status = "partial"
    elif errors:
        status = "error"
    else:
        status = "ok"
    return {
        "status": status,
        "requested": symbols,
        "collected": collected,
        "errors": errors,
        "items": items,
        "force": bool(force),
        "retention": repository.prune_history(
            retention_days=retention_days,
            archive_retention_days=archive_retention_days,
        ),
    }


class ETFResearchRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        path = Path(db_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS etf_universe (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS etf_market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price REAL NOT NULL,
                    change_pct REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    turnover_krw REAL NOT NULL,
                    source TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_etf_snapshots_symbol_captured
                    ON etf_market_snapshots(symbol, captured_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS etf_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    label TEXT NOT NULL,
                    liquidity_score REAL NOT NULL,
                    momentum_score REAL NOT NULL,
                    core_fit_score REAL NOT NULL,
                    risk_score REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    risks_json TEXT NOT NULL,
                    scored_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_etf_scores_symbol_scored
                    ON etf_scores(symbol, scored_at DESC, id DESC);
                """
            )

    def upsert_universe(self, items: list[ETFUniverseItem]) -> None:
        updated_at = _utc_now_iso()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO etf_universe (
                    symbol, name, category, tags_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    tags_json = excluded.tags_json,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        item.symbol,
                        item.name,
                        item.category,
                        _json_dumps(item.tags),
                        updated_at,
                    )
                    for item in items
                ],
            )

    def list_universe(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, name, category, tags_json, updated_at
                FROM etf_universe
                ORDER BY symbol
                """
            ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "name": str(row["name"]),
                "category": str(row["category"]),
                "tags": _json_list(row["tags_json"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def save_snapshot(self, snapshot: ETFMarketSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO etf_market_snapshots (
                    symbol, name, price, change_pct, volume, turnover_krw,
                    source, raw_json, captured_at, status, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.symbol,
                    snapshot.name,
                    float(snapshot.price),
                    float(snapshot.change_pct),
                    int(snapshot.volume),
                    float(snapshot.turnover_krw),
                    snapshot.source,
                    _json_dumps(_compact_snapshot_raw_for_storage(snapshot.raw)),
                    _normalize_utc_iso(snapshot.captured_at),
                    snapshot.status,
                    snapshot.error_message,
                ),
            )

    def latest_snapshot(self, symbol: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT symbol, name, price, change_pct, volume, turnover_krw,
                    source, raw_json, captured_at, status, error_message
                FROM etf_market_snapshots
                WHERE symbol = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        if row is None:
            return {"status": "missing", "symbol": symbol}
        return {
            "symbol": str(row["symbol"]),
            "name": str(row["name"]),
            "price": float(row["price"]),
            "change_pct": float(row["change_pct"]),
            "volume": int(row["volume"]),
            "turnover_krw": float(row["turnover_krw"]),
            "source": str(row["source"]),
            "raw": _json_loads(row["raw_json"], {}),
            "captured_at": str(row["captured_at"]),
            "status": str(row["status"]),
            "error_message": str(row["error_message"] or ""),
        }

    def save_score(self, score: ETFScore) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO etf_scores (
                    symbol, label, liquidity_score, momentum_score,
                    core_fit_score, risk_score, reasons_json, risks_json, scored_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    score.symbol,
                    score.label,
                    float(score.liquidity_score),
                    float(score.momentum_score),
                    float(score.core_fit_score),
                    float(score.risk_score),
                    _json_dumps(score.reasons),
                    _json_dumps(score.risks),
                    _normalize_utc_iso(score.scored_at),
                ),
            )

    def latest_score(self, symbol: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT symbol, label, liquidity_score, momentum_score,
                    core_fit_score, risk_score, reasons_json, risks_json, scored_at
                FROM etf_scores
                WHERE symbol = ?
                ORDER BY scored_at DESC, id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        if row is None:
            return {"label": "unknown", "symbol": symbol}
        return {
            "symbol": str(row["symbol"]),
            "label": str(row["label"]),
            "liquidity_score": float(row["liquidity_score"]),
            "momentum_score": float(row["momentum_score"]),
            "core_fit_score": float(row["core_fit_score"]),
            "risk_score": float(row["risk_score"]),
            "reasons": _json_list(row["reasons_json"]),
            "risks": _json_list(row["risks_json"]),
            "scored_at": str(row["scored_at"]),
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            universe_count = int(
                conn.execute("SELECT COUNT(*) FROM etf_universe").fetchone()[0]
            )
            snapshot_count = int(
                conn.execute("SELECT COUNT(*) FROM etf_market_snapshots").fetchone()[0]
            )
            score_count = int(
                conn.execute("SELECT COUNT(*) FROM etf_scores").fetchone()[0]
            )
            latest_snapshot_at = conn.execute(
                "SELECT MAX(captured_at) FROM etf_market_snapshots"
            ).fetchone()[0]
            latest_score_at = conn.execute(
                "SELECT MAX(scored_at) FROM etf_scores"
            ).fetchone()[0]
        return {
            "db_path": self.db_path,
            "universe_count": universe_count,
            "snapshot_count": snapshot_count,
            "score_count": score_count,
            "latest_snapshot_at": latest_snapshot_at or "",
            "latest_score_at": latest_score_at or "",
        }

    def prune_history(
        self,
        *,
        retention_days: int = 7,
        archive_retention_days: int = 0,
    ) -> dict[str, Any]:
        if int(retention_days) <= 0:
            return {"status": "skipped", "reason": "retention_disabled"}
        hot_days = int(retention_days)
        archive_days = int(archive_retention_days)
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=hot_days)).isoformat()
        cold_retention: dict[str, Any] = {
            "status": "skipped",
            "reason": "cold_retention_disabled",
        }
        archive_cutoff = (
            (now - timedelta(days=archive_days)).isoformat()
            if archive_days > 0
            else ""
        )
        if archive_days > hot_days:
            cold_retention = SQLiteRetentionPruner(self.db_path).prune(
                [
                    RetentionRule(
                        table="etf_market_snapshots",
                        timestamp_column="captured_at",
                        retention_days=archive_days,
                    )
                ]
            )
        retention = SQLiteRetentionPruner(self.db_path).prune(
            [
                RetentionRule(
                    table="etf_market_snapshots",
                    timestamp_column="captured_at",
                    retention_days=hot_days,
                    minimum_timestamp_value=(
                        archive_cutoff if archive_days > hot_days else None
                    ),
                    archive_table="etf_market_snapshots_archive",
                    archive_compress_columns=("raw_json",),
                )
            ]
        )
        snapshot_result = dict(retention.get("tables") or {}).get(
            "etf_market_snapshots", {}
        )
        with self._connect() as conn:
            scores = conn.execute(
                "DELETE FROM etf_scores WHERE scored_at < ?",
                (cutoff,),
            ).rowcount
            archive_scores = 0
            if archive_days > hot_days:
                archive_scores = conn.execute(
                    "DELETE FROM etf_scores WHERE scored_at < ?",
                    (archive_cutoff,),
                ).rowcount
        archive_retention: dict[str, Any] = {
            "status": "skipped",
            "reason": "archive_retention_disabled",
        }
        if int(archive_retention_days) > 0:
            archive_retention = SQLiteRetentionPruner(self.db_path).prune(
                [
                    RetentionRule(
                        table="etf_market_snapshots_archive",
                        timestamp_column="captured_at",
                        retention_days=archive_days,
                    )
                ]
            )
        cold_deleted = int(
            dict((cold_retention.get("tables") or {})).get(
                "etf_market_snapshots",
                {},
            ).get("deleted")
            or 0
        )
        archive_deleted = int(
            dict((archive_retention.get("tables") or {})).get(
                "etf_market_snapshots_archive",
                {},
            ).get("deleted")
            or 0
        )
        vacuumed = False
        if any(
            value > 0
            for value in (
                int(snapshot_result.get("deleted") or 0),
                int(scores or 0),
                int(archive_scores or 0),
                cold_deleted,
                archive_deleted,
            )
        ):
            with sqlite3.connect(self.db_path, isolation_level=None) as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "cutoff": cutoff,
            "snapshots_deleted": int(snapshot_result.get("deleted") or 0),
            "scores_deleted": int(scores or 0),
            "archive_scores_deleted": int(archive_scores or 0),
            "archived": {
                "etf_market_snapshots": int(snapshot_result.get("archived") or 0)
            },
            "retention": retention,
            "archive_retention": archive_retention,
            "cold_retention": cold_retention,
            "vacuumed": vacuumed,
        }


class ConfiguredETFResearchProvider:
    def __init__(
        self,
        *,
        repository_factory: Callable[[], ETFResearchRepository],
        universe_provider: Callable[[], list[ETFUniverseItem]],
        symbol_directory_provider: Callable[[], list[dict[str, Any]]] | None = None,
        universe_limit: int = 200,
    ) -> None:
        self.repository_factory = repository_factory
        self.universe_provider = universe_provider
        self.symbol_directory_provider = symbol_directory_provider
        self.universe_limit = universe_limit

    def _repository(self) -> ETFResearchRepository:
        return self.repository_factory()

    def _configured_universe(self) -> list[ETFUniverseItem]:
        return self.universe_provider()

    def _symbol_directory_rows(self) -> list[dict[str, Any]]:
        if self.symbol_directory_provider is None:
            return []
        rows = self.symbol_directory_provider()
        return [row for row in rows if isinstance(row, dict)]

    def _expanded_universe(self) -> list[ETFUniverseItem]:
        return merge_etf_universe(
            configured=self._configured_universe(),
            symbol_directory_rows=self._symbol_directory_rows(),
            limit=max(int(self.universe_limit), 1),
        )

    def _seed_configured_universe(
        self,
        repository: ETFResearchRepository,
    ) -> list[ETFUniverseItem]:
        universe = self._expanded_universe()
        if universe:
            repository.upsert_universe(universe)
        return universe

    @staticmethod
    def _item_row(item: ETFUniverseItem) -> dict[str, Any]:
        return {
            "symbol": item.symbol,
            "name": item.name,
            "category": item.category,
            "tags": list(item.tags),
            "updated_at": "",
        }

    def _ordered_universe_rows(
        self,
        repository: ETFResearchRepository,
        universe: list[ETFUniverseItem],
    ) -> list[dict[str, Any]]:
        rows = repository.list_universe()
        if not universe:
            return rows
        by_symbol = {str(row.get("symbol") or ""): row for row in rows}
        return [
            by_symbol.get(item.symbol) or self._item_row(item)
            for item in universe
        ]

    def list_universe(self) -> list[dict[str, Any]]:
        repository = self._repository()
        universe = self._seed_configured_universe(repository)
        return self._ordered_universe_rows(repository, universe)

    def latest_snapshot(self, symbol: str) -> dict[str, Any]:
        return self._repository().latest_snapshot(symbol)

    def latest_score(self, symbol: str) -> dict[str, Any]:
        return self._repository().latest_score(symbol)

    def status(self) -> dict[str, Any]:
        repository = self._repository()
        universe = self._seed_configured_universe(repository)
        status = dict(repository.status())
        rows = self._ordered_universe_rows(repository, universe)
        usable_count = sum(
            1
            for row in rows
            if self._has_usable_research(repository, str(row.get("symbol") or ""))
        )
        status["usable_research_count"] = usable_count
        status["expanded_universe_count"] = len(universe)
        status["status"] = "active" if usable_count > 0 else "waiting"
        return status

    def _has_usable_research(
        self,
        repository: ETFResearchRepository,
        symbol: str,
    ) -> bool:
        snapshot = repository.latest_snapshot(symbol)
        if str(snapshot.get("status") or "").lower() == "ok":
            return True
        score = repository.latest_score(symbol)
        if str(score.get("label") or "unknown") != "unknown":
            return True
        return any(
            _to_float(score.get(key)) > 0
            for key in (
                "liquidity_score",
                "momentum_score",
                "core_fit_score",
            )
        )


def score_etf_snapshot(snapshot: ETFMarketSnapshot) -> ETFScore:
    if snapshot.status != "ok" or snapshot.error_message:
        risk = snapshot.error_message or f"snapshot status is {snapshot.status}"
        return ETFScore(
            symbol=snapshot.symbol,
            label="unknown",
            liquidity_score=0.0,
            momentum_score=0.0,
            core_fit_score=0.0,
            risk_score=90.0,
            reasons=["ETF snapshot is not usable for scoring."],
            risks=[risk],
            scored_at=_utc_now_iso(),
        )

    liquidity_score = _score_liquidity(snapshot.volume, snapshot.turnover_krw)
    momentum_score = _score_momentum(snapshot.change_pct)
    core_fit_score = _score_core_fit(snapshot)
    risk_score = _score_risk(snapshot, liquidity_score)

    reasons = [
        f"ETF turnover is {snapshot.turnover_krw:,.0f} KRW.",
        f"ETF volume is {snapshot.volume:,} shares.",
        f"ETF price move is {snapshot.change_pct:.2f}%.",
    ]
    risks: list[str] = []
    if liquidity_score < 45:
        risks.append("ETF liquidity is thin; use smaller order sizes.")
    if abs(snapshot.change_pct) >= 3:
        risks.append("ETF move is extended versus the latest snapshot.")

    if risk_score >= 60:
        label = "unknown"
    elif liquidity_score < 45:
        label = "liquidity_watch"
    elif momentum_score >= 70:
        label = "theme_momentum"
    elif core_fit_score >= 60:
        label = "core_fit"
    else:
        label = "unknown"

    return ETFScore(
        symbol=snapshot.symbol,
        label=label,
        liquidity_score=round(liquidity_score, 2),
        momentum_score=round(momentum_score, 2),
        core_fit_score=round(core_fit_score, 2),
        risk_score=round(risk_score, 2),
        reasons=reasons,
        risks=risks,
        scored_at=_utc_now_iso(),
    )


def _score_liquidity(volume: int, turnover_krw: float) -> float:
    turnover_score = _clamp(float(turnover_krw) / 100_000_000 * 8)
    volume_score = _clamp(float(volume) / 10_000 * 8)
    return max(turnover_score, volume_score)


def _score_momentum(change_pct: float) -> float:
    if change_pct <= -3:
        return 10.0
    if change_pct < 0:
        return _clamp(35 + change_pct * 8)
    return _clamp(50 + change_pct * 15)


def _score_core_fit(snapshot: ETFMarketSnapshot) -> float:
    name = snapshot.name.lower()
    core_terms = ("200", "kospi", "kodex", "tiger", "core", "market")
    term_bonus = 25 if any(term in name for term in core_terms) else 0
    liquidity_bonus = min(_score_liquidity(snapshot.volume, snapshot.turnover_krw), 35)
    stability_bonus = max(0.0, 40 - abs(snapshot.change_pct) * 8)
    return _clamp(term_bonus + liquidity_bonus + stability_bonus)


def _score_risk(snapshot: ETFMarketSnapshot, liquidity_score: float) -> float:
    risk = 0.0
    if liquidity_score < 30:
        risk += 35
    if abs(snapshot.change_pct) >= 3:
        risk += 25
    if snapshot.price <= 0:
        risk += 40
    return _clamp(risk)
