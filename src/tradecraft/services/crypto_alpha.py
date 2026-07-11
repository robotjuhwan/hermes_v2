from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from tradecraft.services.evidence_policy import evidence_from_signal

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value or "0").replace(",", "").strip())
    except ValueError:
        return 0.0


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class CryptoAlphaConfig:
    db_path: str = ".runtime/crypto_alpha.db"
    source_ids: str = "binance_announcements,coinbase_blog,kraken_blog"
    rate_limit_sec: float = 2.0
    context_limit: int = 12
    llm_model: str = "gpt-5.6-luna"
    llm_reasoning_effort: str = "medium"


DEFAULT_SOURCES: dict[str, dict[str, Any]] = {
    "binance_announcements": {
        "source_id": "binance_announcements",
        "label": "Binance Announcements",
        "url": "https://www.binance.com/en/support/announcement",
        "source_type": "exchange_announcement",
        "trust_score": 0.95,
    },
    "coinbase_blog": {
        "source_id": "coinbase_blog",
        "label": "Coinbase Blog",
        "url": "https://www.coinbase.com/blog",
        "source_type": "exchange_blog",
        "trust_score": 0.8,
    },
    "kraken_blog": {
        "source_id": "kraken_blog",
        "label": "Kraken Blog",
        "url": "https://blog.kraken.com",
        "source_type": "exchange_blog",
        "trust_score": 0.75,
    },
}


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.parts.append(text)


def html_to_text(html: str) -> str:
    parser = _TextHTMLParser()
    parser.feed(html or "")
    return "\n".join(parser.parts)


EXPLICIT_USDT_SYMBOL_PATTERN = re.compile(r"\b([A-Z0-9]{2,12})(?:/USDT|USDT)\b")
PAREN_SYMBOL_PATTERN = re.compile(r"\b[A-Z][A-Z0-9 .&'/-]{1,80}?\s+\(([A-Z0-9]{2,12})\)")
PAREN_SYMBOL_CONTEXT_KEYWORDS = (
    "WILL LIST",
    "NEW LISTING",
    "LISTING",
    "ADD SUPPORT",
    "ADDS SUPPORT",
    "TRADING WILL OPEN",
    "SPOT TRADING",
    "TRADING PAIR",
    "FUTURES CONTRACT",
    "LAUNCHPOOL",
    "AIRDROP",
    "NETWORK UPGRADE",
    "MAINNET",
    "TOKEN",
    "COIN",
)
IGNORED_BASE_ASSETS = {
    "API",
    "USD",
    "USDT",
}
IGNORED_SYMBOL_WORDS = {
    "BINANCE",
    "COINBASE",
    "KRAKEN",
    "LIST",
    "WILL",
}
OUTCOME_HORIZONS: tuple[tuple[str, int], ...] = (
    ("1h", 1),
    ("4h", 4),
    ("24h", 24),
    ("72h", 72),
)
USABLE_ALPHA_LINK_VALIDITY_STATUSES = frozenset({"unknown", "valid"})


def is_usable_alpha_symbol_link_status(status: Any) -> bool:
    normalized = str(status or "unknown").strip().lower() or "unknown"
    return normalized in USABLE_ALPHA_LINK_VALIDITY_STATUSES


def _alpha_symbol_link_status_sql(alias: str = "es") -> str:
    return f"LOWER(COALESCE(NULLIF(TRIM({alias}.validity_status), ''), 'unknown'))"


def _usable_alpha_symbol_link_sql(alias: str = "es") -> str:
    values = ", ".join(
        f"'{status}'" for status in sorted(USABLE_ALPHA_LINK_VALIDITY_STATUSES)
    )
    return f"{_alpha_symbol_link_status_sql(alias)} IN ({values})"


def _invalid_alpha_symbol_link_sql(alias: str = "es") -> str:
    return f"{_alpha_symbol_link_status_sql(alias)} = 'invalid'"


def normalize_crypto_symbol(base: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", str(base or "").upper())
    if not cleaned or _is_ignored_base_asset(cleaned):
        return ""
    if cleaned.endswith("USDT"):
        return cleaned
    return f"{cleaned}USDT"


def _is_ignored_base_asset(base: str) -> bool:
    cleaned = re.sub(r"[^A-Z0-9]", "", str(base or "").upper())
    return cleaned in IGNORED_BASE_ASSETS or cleaned in IGNORED_SYMBOL_WORDS


def _is_parenthetical_symbol_context(line: str) -> bool:
    return any(
        re.search(rf"(?<![A-Z0-9]){re.escape(keyword)}(?![A-Z0-9])", line)
        for keyword in PAREN_SYMBOL_CONTEXT_KEYWORDS
    )


def _is_binance_invalid_symbol_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "-1121" in text or "invalid symbol" in text


def extract_symbol_links(text: str) -> list[dict[str, Any]]:
    bases: set[str] = set()
    upper_text = text.upper()
    for match in EXPLICIT_USDT_SYMBOL_PATTERN.finditer(upper_text):
        base = str(match.group(1)).removesuffix("USDT")
        if base and not _is_ignored_base_asset(base):
            bases.add(base)
    for line in upper_text.splitlines():
        if not _is_parenthetical_symbol_context(line):
            continue
        for match in PAREN_SYMBOL_PATTERN.finditer(line):
            base = str(match.group(1)).removesuffix("USDT")
            if base and not _is_ignored_base_asset(base):
                bases.add(base)
    return [
        {
            "symbol": normalize_crypto_symbol(base),
            "base_asset": base,
            "link_confidence": 0.86,
            "impact_direction": "bullish_watch",
            "impact_horizon": "1h_72h",
            "reason": "symbol appeared in public catalyst text",
        }
        for base in sorted(bases)
        if normalize_crypto_symbol(base)
    ]


def classify_event_type(title: str, text: str) -> tuple[str, float]:
    haystack = f"{title}\n{text}".lower()
    if "will list" in haystack or "new listing" in haystack or "add support" in haystack:
        return "listing", 0.9
    if "will delist" in haystack or "remove trading" in haystack:
        return "delisting", 0.92
    if "airdrop" in haystack or "launchpool" in haystack or "reward" in haystack:
        return "supply_or_incentive", 0.78
    if "network upgrade" in haystack or "mainnet" in haystack or "hard fork" in haystack:
        return "protocol_update", 0.72
    if "exploit" in haystack or "incident" in haystack or "halt" in haystack:
        return "incident", 0.82
    return "project_update", 0.55


class CryptoAlphaRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()
        self.upsert_default_sources()

    def _connect(self) -> sqlite3.Connection:
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crypto_alpha_sources (
                    source_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT '',
                    trust_score REAL NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_crawled_at TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crypto_alpha_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    raw_text TEXT NOT NULL DEFAULT '',
                    summary_text TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    crawled_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error_message TEXT NOT NULL DEFAULT '',
                    UNIQUE(source_id, content_hash)
                );
                CREATE TABLE IF NOT EXISTS crypto_alpha_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER,
                    source_id TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    event_time TEXT NOT NULL DEFAULT '',
                    detected_at TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    importance REAL NOT NULL DEFAULT 0,
                    decay_hours REAL NOT NULL DEFAULT 72,
                    status TEXT NOT NULL DEFAULT 'active',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS crypto_alpha_event_symbols (
                    event_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    base_asset TEXT NOT NULL DEFAULT '',
                    link_confidence REAL NOT NULL DEFAULT 0,
                    impact_direction TEXT NOT NULL DEFAULT '',
                    impact_horizon TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    validity_status TEXT NOT NULL DEFAULT 'unknown',
                    validity_reason TEXT NOT NULL DEFAULT '',
                    validity_checked_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(event_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS crypto_alpha_event_outcomes (
                    event_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    return_pct REAL NOT NULL DEFAULT 0,
                    mfe_pct REAL NOT NULL DEFAULT 0,
                    mae_pct REAL NOT NULL DEFAULT 0,
                    r_multiple REAL NOT NULL DEFAULT 0,
                    regime TEXT NOT NULL DEFAULT '',
                    measured_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, symbol, horizon)
                );

                CREATE TABLE IF NOT EXISTS crypto_alpha_hypotheses (
                    hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_key TEXT NOT NULL UNIQUE,
                    summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    confidence REAL NOT NULL DEFAULT 0,
                    support_count INTEGER NOT NULL DEFAULT 0,
                    avg_r_multiple REAL NOT NULL DEFAULT 0,
                    win_rate_pct REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crypto_alpha_context_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_columns(conn)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_alpha_snapshots_source
                    ON crypto_alpha_snapshots(source_id, crawled_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_crypto_alpha_snapshots_source_hash
                    ON crypto_alpha_snapshots(source_id, content_hash);
                CREATE INDEX IF NOT EXISTS idx_crypto_alpha_events_type_time
                    ON crypto_alpha_events(event_type, detected_at DESC);
                CREATE INDEX IF NOT EXISTS idx_crypto_alpha_event_symbols_symbol
                    ON crypto_alpha_event_symbols(symbol, event_id DESC);
                """
            )

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        expected: dict[str, dict[str, str]] = {
            "crypto_alpha_sources": {
                "label": "TEXT NOT NULL DEFAULT ''",
                "url": "TEXT NOT NULL DEFAULT ''",
                "source_type": "TEXT NOT NULL DEFAULT ''",
                "trust_score": "REAL NOT NULL DEFAULT 0",
                "enabled": "INTEGER NOT NULL DEFAULT 1",
                "last_crawled_at": "TEXT NOT NULL DEFAULT ''",
                "last_status": "TEXT NOT NULL DEFAULT ''",
                "error_message": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
            "crypto_alpha_snapshots": {
                "source_id": "TEXT NOT NULL DEFAULT ''",
                "url": "TEXT NOT NULL DEFAULT ''",
                "title": "TEXT NOT NULL DEFAULT ''",
                "content_hash": "TEXT NOT NULL DEFAULT ''",
                "raw_text": "TEXT NOT NULL DEFAULT ''",
                "summary_text": "TEXT NOT NULL DEFAULT ''",
                "raw_json": "TEXT NOT NULL DEFAULT '{}'",
                "crawled_at": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'ok'",
                "error_message": "TEXT NOT NULL DEFAULT ''",
            },
            "crypto_alpha_events": {
                "snapshot_id": "INTEGER",
                "source_id": "TEXT NOT NULL DEFAULT ''",
                "event_type": "TEXT NOT NULL DEFAULT ''",
                "title": "TEXT NOT NULL DEFAULT ''",
                "summary": "TEXT NOT NULL DEFAULT ''",
                "event_time": "TEXT NOT NULL DEFAULT ''",
                "detected_at": "TEXT NOT NULL DEFAULT ''",
                "confidence": "REAL NOT NULL DEFAULT 0",
                "importance": "REAL NOT NULL DEFAULT 0",
                "decay_hours": "REAL NOT NULL DEFAULT 72",
                "status": "TEXT NOT NULL DEFAULT 'active'",
                "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "crypto_alpha_event_symbols": {
                "event_id": "INTEGER NOT NULL DEFAULT 0",
                "symbol": "TEXT NOT NULL DEFAULT ''",
                "base_asset": "TEXT NOT NULL DEFAULT ''",
                "link_confidence": "REAL NOT NULL DEFAULT 0",
                "impact_direction": "TEXT NOT NULL DEFAULT ''",
                "impact_horizon": "TEXT NOT NULL DEFAULT ''",
                "reason": "TEXT NOT NULL DEFAULT ''",
                "validity_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "validity_reason": "TEXT NOT NULL DEFAULT ''",
                "validity_checked_at": "TEXT NOT NULL DEFAULT ''",
            },
            "crypto_alpha_event_outcomes": {
                "event_id": "INTEGER NOT NULL DEFAULT 0",
                "symbol": "TEXT NOT NULL DEFAULT ''",
                "horizon": "TEXT NOT NULL DEFAULT ''",
                "return_pct": "REAL NOT NULL DEFAULT 0",
                "mfe_pct": "REAL NOT NULL DEFAULT 0",
                "mae_pct": "REAL NOT NULL DEFAULT 0",
                "r_multiple": "REAL NOT NULL DEFAULT 0",
                "regime": "TEXT NOT NULL DEFAULT ''",
                "measured_at": "TEXT NOT NULL DEFAULT ''",
            },
            "crypto_alpha_hypotheses": {
                "pattern_key": "TEXT NOT NULL DEFAULT ''",
                "summary": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'candidate'",
                "confidence": "REAL NOT NULL DEFAULT 0",
                "support_count": "INTEGER NOT NULL DEFAULT 0",
                "avg_r_multiple": "REAL NOT NULL DEFAULT 0",
                "win_rate_pct": "REAL NOT NULL DEFAULT 0",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in expected.items():
            existing = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column, ddl in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def upsert_default_sources(self) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            for source in DEFAULT_SOURCES.values():
                conn.execute(
                    """
                    INSERT INTO crypto_alpha_sources (
                        source_id, label, url, source_type, trust_score, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        label=excluded.label,
                        url=excluded.url,
                        source_type=excluded.source_type,
                        trust_score=excluded.trust_score,
                        updated_at=excluded.updated_at
                    """,
                    (
                        source["source_id"],
                        source["label"],
                        source["url"],
                        source["source_type"],
                        float(source["trust_score"]),
                        now,
                    ),
                )

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            return {
                "sources": int(
                    conn.execute("SELECT COUNT(*) FROM crypto_alpha_sources").fetchone()[0]
                ),
                "snapshots": int(
                    conn.execute("SELECT COUNT(*) FROM crypto_alpha_snapshots").fetchone()[0]
                ),
                "events": int(
                    conn.execute("SELECT COUNT(*) FROM crypto_alpha_events").fetchone()[0]
                ),
                "outcomes": int(
                    conn.execute(
                        "SELECT COUNT(*) FROM crypto_alpha_event_outcomes"
                    ).fetchone()[0]
                ),
                "hypotheses": int(
                    conn.execute("SELECT COUNT(*) FROM crypto_alpha_hypotheses").fetchone()[
                        0
                    ]
                ),
            }

    def upsert_snapshot(
        self,
        *,
        source_id: str,
        url: str,
        title: str,
        raw_text: str,
        raw_json: dict[str, Any] | None = None,
        status: str = "ok",
        error_message: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        digest = content_hash(raw_text)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_alpha_snapshots (
                    source_id, url, title, content_hash, raw_text, raw_json,
                    crawled_at, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, content_hash) DO UPDATE SET
                    url=excluded.url,
                    title=excluded.title,
                    raw_json=excluded.raw_json,
                    crawled_at=excluded.crawled_at,
                    status=excluded.status,
                    error_message=excluded.error_message
                """,
                (
                    source_id,
                    url,
                    title,
                    digest,
                    raw_text,
                    json_dumps(raw_json or {}),
                    now,
                    status,
                    error_message,
                ),
            )
            row = conn.execute(
                """
                SELECT snapshot_id, source_id, url, title, content_hash, crawled_at, status
                FROM crypto_alpha_snapshots
                WHERE source_id=? AND content_hash=?
                """,
                (source_id, digest),
            ).fetchone()
        return dict(row)

    def get_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM crypto_alpha_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        return dict(row) if row else None

    def insert_event(
        self,
        *,
        snapshot_id: int,
        source_id: str,
        event_type: str,
        title: str,
        summary: str,
        confidence: float,
        importance: float,
        symbols: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT event_id
                FROM crypto_alpha_events
                WHERE snapshot_id=? AND event_type=? AND title=?
                ORDER BY event_id ASC
                LIMIT 1
                """,
                (snapshot_id, event_type, title),
            ).fetchone()
            if existing is not None:
                event_id = int(existing["event_id"])
                for item in symbols:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO crypto_alpha_event_symbols (
                            event_id, symbol, base_asset, link_confidence,
                            impact_direction, impact_horizon, reason,
                            validity_status, validity_reason, validity_checked_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id, symbol) DO UPDATE SET
                            base_asset=excluded.base_asset,
                            link_confidence=excluded.link_confidence,
                            impact_direction=excluded.impact_direction,
                            impact_horizon=excluded.impact_horizon,
                            reason=excluded.reason,
                            validity_status=CASE
                                WHEN crypto_alpha_event_symbols.validity_status='invalid'
                                     AND excluded.validity_status='unknown'
                                THEN crypto_alpha_event_symbols.validity_status
                                ELSE excluded.validity_status
                            END,
                            validity_reason=CASE
                                WHEN crypto_alpha_event_symbols.validity_status='invalid'
                                     AND excluded.validity_status='unknown'
                                THEN crypto_alpha_event_symbols.validity_reason
                                ELSE excluded.validity_reason
                            END,
                            validity_checked_at=CASE
                                WHEN crypto_alpha_event_symbols.validity_status='invalid'
                                     AND excluded.validity_status='unknown'
                                THEN crypto_alpha_event_symbols.validity_checked_at
                                ELSE excluded.validity_checked_at
                            END
                        """,
                        self._event_symbol_values(event_id, item),
                    )
                return {"event_id": event_id, "created": False}
            cursor = conn.execute(
                """
                INSERT INTO crypto_alpha_events (
                    snapshot_id, source_id, event_type, title, summary, event_time,
                    detected_at, confidence, importance, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    source_id,
                    event_type,
                    title,
                    summary,
                    now,
                    now,
                    confidence,
                    importance,
                    json_dumps({"extractor": "rules_v1"}),
                ),
            )
            event_id = int(cursor.lastrowid)
            for item in symbols:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO crypto_alpha_event_symbols (
                        event_id, symbol, base_asset, link_confidence,
                        impact_direction, impact_horizon, reason,
                        validity_status, validity_reason, validity_checked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, symbol) DO UPDATE SET
                        base_asset=excluded.base_asset,
                        link_confidence=excluded.link_confidence,
                        impact_direction=excluded.impact_direction,
                        impact_horizon=excluded.impact_horizon,
                        reason=excluded.reason,
                        validity_status=excluded.validity_status,
                        validity_reason=excluded.validity_reason,
                        validity_checked_at=excluded.validity_checked_at
                    """,
                    self._event_symbol_values(event_id, item),
                )
        return {"event_id": event_id, "created": True}

    @staticmethod
    def _event_symbol_values(event_id: int, item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            event_id,
            item["symbol"],
            item["base_asset"],
            item["link_confidence"],
            item["impact_direction"],
            item["impact_horizon"],
            item["reason"],
            str(item.get("validity_status") or "unknown"),
            str(item.get("validity_reason") or ""),
            str(item.get("validity_checked_at") or ""),
        )

    def mark_event_symbol_validity(
        self,
        *,
        event_id: int,
        symbol: str,
        status: str,
        reason: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE crypto_alpha_event_symbols
                SET validity_status=?, validity_reason=?, validity_checked_at=?
                WHERE event_id=? AND symbol=?
                """,
                (status, reason, utc_now_iso(), event_id, symbol),
            )

    def recent_events(self, *, symbols: list[str], limit: int) -> list[dict[str, Any]]:
        normalized = [str(symbol).upper() for symbol in symbols if symbol]
        params: list[Any] = []
        where_clauses = [_usable_alpha_symbol_link_sql("es")]
        if normalized:
            marks = ",".join("?" for _ in normalized)
            where_clauses.append(f"es.symbol IN ({marks})")
            params.extend(normalized)
        symbol_filter = "WHERE " + " AND ".join(where_clauses)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*, GROUP_CONCAT(es.symbol) AS linked_symbols
                FROM crypto_alpha_events e
                JOIN crypto_alpha_event_symbols es ON es.event_id=e.event_id
                {symbol_filter}
                GROUP BY e.event_id
                ORDER BY e.detected_at DESC, e.event_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def unlabeled_event_symbols(self, *, limit: int = 20) -> list[dict[str, Any]]:
        usable_symbol_link = _usable_alpha_symbol_link_sql("es")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.event_id, e.event_type, e.event_time, e.detected_at, es.symbol,
                       GROUP_CONCAT(o.horizon) AS labeled_horizons
                FROM crypto_alpha_events e
                JOIN crypto_alpha_event_symbols es ON es.event_id=e.event_id
                LEFT JOIN crypto_alpha_event_outcomes o
                    ON o.event_id=e.event_id AND o.symbol=es.symbol
                WHERE {usable_symbol_link}
                GROUP BY e.event_id, es.symbol
                HAVING COUNT(o.horizon) < ?
                ORDER BY e.detected_at DESC, e.event_id DESC
                LIMIT ?
                """,
                (len(OUTCOME_HORIZONS), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def skipped_invalid_event_symbol_count(self, *, limit: int = 20) -> int:
        invalid_symbol_link = _invalid_alpha_symbol_link_sql("es")
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT e.event_id, es.symbol
                    FROM crypto_alpha_events e
                    JOIN crypto_alpha_event_symbols es ON es.event_id=e.event_id
                    LEFT JOIN crypto_alpha_event_outcomes o
                        ON o.event_id=e.event_id AND o.symbol=es.symbol
                    WHERE {invalid_symbol_link}
                    GROUP BY e.event_id, es.symbol
                    HAVING COUNT(o.horizon) < ?
                    ORDER BY e.detected_at DESC, e.event_id DESC
                    LIMIT ?
                )
                """,
                (len(OUTCOME_HORIZONS), limit),
            ).fetchone()
        return int(row[0] or 0)

    def upsert_outcome(
        self,
        *,
        event_id: int,
        symbol: str,
        horizon: str,
        return_pct: float,
        mfe_pct: float,
        mae_pct: float,
        r_multiple: float,
        regime: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_alpha_event_outcomes (
                    event_id, symbol, horizon, return_pct, mfe_pct, mae_pct,
                    r_multiple, regime, measured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, symbol, horizon) DO UPDATE SET
                    return_pct=excluded.return_pct,
                    mfe_pct=excluded.mfe_pct,
                    mae_pct=excluded.mae_pct,
                    r_multiple=excluded.r_multiple,
                    regime=excluded.regime,
                    measured_at=excluded.measured_at
                """,
                (
                    event_id,
                    symbol,
                    horizon,
                    return_pct,
                    mfe_pct,
                    mae_pct,
                    r_multiple,
                    regime,
                    utc_now_iso(),
                ),
            )

    def outcomes_for_symbols(
        self,
        *,
        symbols: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized = [str(symbol).upper() for symbol in symbols if symbol]
        if not normalized:
            return []
        marks = ",".join("?" for _ in normalized)
        usable_symbol_link = _usable_alpha_symbol_link_sql("es")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT o.*, e.event_type, e.source_id
                FROM crypto_alpha_event_outcomes o
                JOIN crypto_alpha_events e ON e.event_id=o.event_id
                JOIN crypto_alpha_event_symbols es
                    ON es.event_id=o.event_id AND es.symbol=o.symbol
                WHERE o.symbol IN ({marks}) AND {usable_symbol_link}
                ORDER BY o.measured_at DESC
                LIMIT ?
                """,
                (*normalized, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def scorecards(self, *, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM crypto_alpha_hypotheses
                WHERE support_count > 0
                ORDER BY status='active' DESC, confidence DESC, support_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_source_status(
        self,
        *,
        source_id: str,
        status: str,
        error_message: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE crypto_alpha_sources
                SET last_crawled_at=?, last_status=?, error_message=?, updated_at=?
                WHERE source_id=?
                """,
                (utc_now_iso(), status, error_message, utc_now_iso(), source_id),
            )


class CryptoAlphaService:
    def __init__(
        self,
        config: CryptoAlphaConfig | None = None,
        *,
        binance: Any | None = None,
        codex_runtime: Any | None = None,
    ) -> None:
        self.config = config or CryptoAlphaConfig()
        self.repository = CryptoAlphaRepository(self.config.db_path)
        self.binance = binance
        self.codex_runtime = codex_runtime

    def status(self) -> dict[str, Any]:
        counts = self.repository.counts()
        return {
            "status": "ok",
            "db_path": str(self.repository.path),
            **counts,
        }

    def store_snapshot(
        self,
        *,
        source_id: str,
        url: str,
        title: str,
        raw_text: str,
        raw_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if source_id not in DEFAULT_SOURCES:
            raise ValueError(f"crypto alpha source not allowlisted: {source_id}")
        return self.repository.upsert_snapshot(
            source_id=source_id,
            url=url,
            title=title,
            raw_text=raw_text,
            raw_json=raw_json,
        )

    async def fetch_source_snapshot(
        self,
        *,
        source_id: str,
        url: str,
        title: str = "",
        timeout_sec: float = 12.0,
    ) -> dict[str, Any]:
        allowed = DEFAULT_SOURCES.get(source_id)
        if allowed is None or not self._is_allowed_url(url, str(allowed["url"])):
            raise ValueError(f"crypto alpha source not allowlisted: {source_id}")

        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "TradeCraft-HERMES/1.0"},
            )
            response.raise_for_status()
        if not self._is_allowed_url(str(response.url), str(allowed["url"])):
            raise ValueError(f"crypto alpha redirect not allowlisted: {response.url}")
        raw_text = html_to_text(response.text)
        fallback_title = raw_text.splitlines()[0][:160] if raw_text else source_id
        snapshot = self.store_snapshot(
            source_id=source_id,
            url=str(response.url),
            title=title or fallback_title,
            raw_text=raw_text,
            raw_json={"http_status": response.status_code},
        )
        self.repository.mark_source_status(source_id=source_id, status="ok")
        return snapshot

    def extract_events_from_snapshot(self, snapshot_id: int) -> dict[str, Any]:
        snapshot = self.repository.get_snapshot(snapshot_id)
        if snapshot is None:
            return {"status": "not_found", "created_events": 0}

        title = str(snapshot.get("title") or "")
        raw_text = str(snapshot.get("raw_text") or "")
        event_type, confidence = classify_event_type(title, raw_text)
        symbols = extract_symbol_links(f"{title}\n{raw_text}")
        if not symbols:
            return {"status": "no_symbols", "created_events": 0}

        event_result = self.repository.insert_event(
            snapshot_id=int(snapshot_id),
            source_id=str(snapshot["source_id"]),
            event_type=event_type,
            title=title,
            summary=raw_text[:500],
            confidence=confidence,
            importance=min(1.0, confidence + 0.05 * len(symbols)),
            symbols=symbols,
        )
        return {
            "status": "ok",
            "created_events": 1 if event_result.get("created") else 0,
            "event_ids": [int(event_result["event_id"])],
        }

    async def label_due_outcomes(self, *, limit: int = 20) -> dict[str, Any]:
        if self.binance is None:
            return {
                "status": "skipped",
                "reason": "binance_adapter_missing",
                "labeled": 0,
                "skipped_invalid_symbols": 0,
            }

        skipped_invalid_symbols = self.repository.skipped_invalid_event_symbol_count(
            limit=limit
        )
        candidates = self.repository.unlabeled_event_symbols(limit=limit)
        labeled = 0
        skipped_recent = 0
        errors: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for item in candidates:
            symbol = str(item["symbol"])
            event_time = _parse_iso_datetime(item.get("event_time")) or _parse_iso_datetime(
                item.get("detected_at")
            )
            if event_time is None:
                errors.append({"symbol": symbol, "error": "event_time_invalid"})
                continue
            labeled_horizons = {
                part.strip()
                for part in str(item.get("labeled_horizons") or "").split(",")
                if part.strip()
            }
            for horizon, horizon_hours in OUTCOME_HORIZONS:
                if horizon in labeled_horizons:
                    continue
                outcome_end = event_time + timedelta(hours=horizon_hours)
                if now < outcome_end:
                    skipped_recent += 1
                    continue
                try:
                    start_ms = int(event_time.timestamp() * 1000)
                    end_ms = int(outcome_end.timestamp() * 1000)
                    klines = await self.binance.fetch_klines(
                        symbol,
                        market="spot",
                        interval="1h",
                        limit=min(max(horizon_hours + 2, 2), 1000),
                        start_time=start_ms,
                        end_time=end_ms,
                    )
                    self.repository.mark_event_symbol_validity(
                        event_id=int(item["event_id"]),
                        symbol=symbol,
                        status="valid",
                        reason="binance_spot_klines_ok",
                    )
                except TypeError as exc:
                    logger.warning(
                        "crypto alpha outcome label skipped for %s %s: %s",
                        symbol,
                        horizon,
                        exc,
                    )
                    errors.append(
                        {
                            "symbol": symbol,
                            "horizon": horizon,
                            "error": "binance_klines_time_window_unsupported",
                        }
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "crypto alpha outcome label skipped for %s %s: %s",
                        symbol,
                        horizon,
                        exc,
                    )
                    if _is_binance_invalid_symbol_error(exc):
                        self.repository.mark_event_symbol_validity(
                            event_id=int(item["event_id"]),
                            symbol=symbol,
                            status="invalid",
                            reason="binance_spot_invalid_symbol",
                        )
                        errors.append(
                            {
                                "symbol": symbol,
                                "horizon": horizon,
                                "error": "binance_spot_invalid_symbol",
                            }
                        )
                        break
                    errors.append(
                        {
                            "symbol": symbol,
                            "horizon": horizon,
                            "error": str(exc),
                        }
                    )
                    continue
                try:
                    if len(klines) < 2:
                        continue
                    first = _to_float(klines[0].get("open"))
                    if first <= 0:
                        continue
                    last = _to_float(klines[-1].get("close"))
                    high = max(_to_float(row.get("high")) for row in klines)
                    low = min(_to_float(row.get("low")) for row in klines)
                    return_pct = ((last - first) / first) * 100
                    mfe_pct = ((high - first) / first) * 100
                    mae_pct = ((low - first) / first) * 100
                    r_multiple = return_pct / max(abs(mae_pct), 1.0)
                    self.repository.upsert_outcome(
                        event_id=int(item["event_id"]),
                        symbol=symbol,
                        horizon=horizon,
                        return_pct=return_pct,
                        mfe_pct=mfe_pct,
                        mae_pct=mae_pct,
                        r_multiple=r_multiple,
                    )
                    labeled += 1
                except Exception as exc:
                    logger.warning(
                        "crypto alpha outcome label failed for %s %s: %s",
                        symbol,
                        horizon,
                        exc,
                    )
                    errors.append({"symbol": symbol, "horizon": horizon, "error": str(exc)})

        self.refresh_scorecards()
        return {
            "status": "ok",
            "labeled": labeled,
            "skipped_recent": skipped_recent,
            "skipped_invalid_symbols": skipped_invalid_symbols,
            "errors": errors[:5],
        }

    def refresh_scorecards(self) -> dict[str, Any]:
        usable_symbol_link = _usable_alpha_symbol_link_sql("es")
        with self.repository._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.event_type, e.source_id,
                       COUNT(*) AS n,
                       AVG(o.r_multiple) AS avg_r,
                       AVG(CASE WHEN o.return_pct > 0 THEN 1.0 ELSE 0.0 END) * 100 AS win_rate
                FROM crypto_alpha_event_outcomes o
                JOIN crypto_alpha_events e ON e.event_id=o.event_id
                JOIN crypto_alpha_event_symbols es
                    ON es.event_id=o.event_id AND es.symbol=o.symbol
                WHERE {usable_symbol_link}
                GROUP BY e.event_type, e.source_id
                """
            ).fetchall()
            stale_rows = conn.execute(
                f"""
                SELECT DISTINCT e.event_type, e.source_id
                FROM crypto_alpha_event_outcomes o
                JOIN crypto_alpha_events e ON e.event_id=o.event_id
                JOIN crypto_alpha_event_symbols es
                    ON es.event_id=o.event_id AND es.symbol=o.symbol
                WHERE NOT ({usable_symbol_link})
                """
            ).fetchall()
            updated = 0
            now = utc_now_iso()
            refreshed_pattern_keys: set[str] = set()
            for row in rows:
                pattern_key = f"{row['source_id']}:{row['event_type']}"
                refreshed_pattern_keys.add(pattern_key)
                n = int(row["n"] or 0)
                avg_r = float(row["avg_r"] or 0)
                win_rate = float(row["win_rate"] or 0)
                confidence = min(0.9, 0.25 + n * 0.08)
                status = "active" if n >= 3 else "candidate"
                conn.execute(
                    """
                    INSERT INTO crypto_alpha_hypotheses (
                        pattern_key, summary, status, confidence, support_count,
                        avg_r_multiple, win_rate_pct, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pattern_key) DO UPDATE SET
                        summary=excluded.summary,
                        status=excluded.status,
                        confidence=excluded.confidence,
                        support_count=excluded.support_count,
                        avg_r_multiple=excluded.avg_r_multiple,
                        win_rate_pct=excluded.win_rate_pct,
                        updated_at=excluded.updated_at
                    """,
                    (
                        pattern_key,
                        f"{pattern_key} n={n} avgR={avg_r:.2f} win={win_rate:.1f}%",
                        status,
                        confidence,
                        n,
                        avg_r,
                        win_rate,
                        now,
                    ),
                )
                updated += 1
            for row in stale_rows:
                pattern_key = f"{row['source_id']}:{row['event_type']}"
                if pattern_key in refreshed_pattern_keys:
                    continue
                cursor = conn.execute(
                    """
                    UPDATE crypto_alpha_hypotheses
                    SET summary=?, status='candidate', confidence=0,
                        support_count=0, avg_r_multiple=0, win_rate_pct=0,
                        updated_at=?
                    WHERE pattern_key=?
                    """,
                    (
                        f"{pattern_key} n=0 avgR=0.00 win=0.0%",
                        now,
                        pattern_key,
                    ),
                )
                updated += int(cursor.rowcount or 0)
        return {"status": "ok", "updated": updated}

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        configured_limit = max(1, int(self.config.context_limit))
        requested_limit = max(1, int(limit or configured_limit))
        max_items = min(requested_limit, configured_limit, 30)
        events = self.repository.recent_events(symbols=symbols or [], limit=max_items)
        outcomes = self.repository.outcomes_for_symbols(
            symbols=symbols or [],
            limit=max_items,
        )
        scorecards = self.repository.scorecards(limit=6)
        packed_events = [
            {
                "event_id": event["event_id"],
                "source_id": event["source_id"],
                "event_type": event["event_type"],
                "title": event["title"],
                "summary": str(event["summary"])[:360],
                "confidence": event["confidence"],
                "importance": event["importance"],
                "detected_at": event["detected_at"],
                "symbols": [
                    value
                    for value in str(event.get("linked_symbols") or "").split(",")
                    if value
                ],
            }
            for event in events
        ]
        evidence = []
        for event in events:
            linked_symbols = [
                value
                for value in str(event.get("linked_symbols") or "").split(",")
                if value
            ]
            for symbol in linked_symbols:
                evidence.append(
                    evidence_from_signal(
                        source="crypto_alpha",
                        signal_type="catalyst_event",
                        symbol=symbol,
                        scope="binance",
                        confidence=_to_float(event.get("importance")),
                        ttl_sec=72 * 60 * 60,
                        captured_at=str(event.get("detected_at") or utc_now_iso()),
                        payload={
                            "event_type": event.get("event_type"),
                            "title": event.get("title"),
                            "source_id": event.get("source_id"),
                            "summary": str(event.get("summary") or "")[:360],
                        },
                    ).to_dict()
                )
        result = {
            "status": "ok",
            "scope": "binance_crypto_alpha",
            "events": packed_events,
            "evidence": evidence[:max_items],
            "similar_outcomes": [
                {
                    "event_id": row["event_id"],
                    "symbol": row["symbol"],
                    "event_type": row["event_type"],
                    "source_id": row["source_id"],
                    "horizon": row["horizon"],
                    "return_pct": row["return_pct"],
                    "mfe_pct": row["mfe_pct"],
                    "mae_pct": row["mae_pct"],
                    "r_multiple": row["r_multiple"],
                    "regime": row["regime"],
                }
                for row in outcomes
            ],
            "scorecards": [
                {
                    "pattern_key": row["pattern_key"],
                    "status": row["status"],
                    "confidence": row["confidence"],
                    "support_count": row["support_count"],
                    "avg_r_multiple": row["avg_r_multiple"],
                    "win_rate_pct": row["win_rate_pct"],
                    "summary": row["summary"],
                }
                for row in scorecards
            ],
            "active_lessons": [
                row["summary"] for row in scorecards[:4] if row["status"] == "active"
            ],
            "contradictions": [],
            "data_gaps": [
                "no_labeled_outcomes" if not outcomes else "",
                "no_recent_symbol_events" if not packed_events else "",
            ],
            "event_count": len(packed_events),
            "limit": max_items,
        }
        result["data_gaps"] = [gap for gap in result["data_gaps"] if gap]
        return result

    async def collect_once(self) -> dict[str, Any]:
        source_ids = self._configured_source_ids()
        snapshots = 0
        created_events = 0
        errors: list[dict[str, str]] = []
        for index, source_id in enumerate(source_ids):
            source = DEFAULT_SOURCES[source_id]
            try:
                snapshot = await self.fetch_source_snapshot(
                    source_id=source_id,
                    url=str(source["url"]),
                    title=str(source["label"]),
                )
                snapshots += 1
                extracted = self.extract_events_from_snapshot(int(snapshot["snapshot_id"]))
                created_events += int(extracted.get("created_events") or 0)
            except Exception as exc:
                logger.warning("crypto alpha collect failed for %s: %s", source_id, exc)
                self.repository.mark_source_status(
                    source_id=source_id,
                    status="error",
                    error_message=str(exc),
                )
                errors.append({"source_id": source_id, "error": str(exc)})
            if index < len(source_ids) - 1 and self.config.rate_limit_sec > 0:
                await asyncio.sleep(float(self.config.rate_limit_sec))

        return {
            "status": "ok" if not errors else "partial",
            "sources": source_ids,
            "created_snapshots": snapshots,
            "created_events": created_events,
            "errors": errors[:5],
        }

    def _configured_source_ids(self) -> list[str]:
        values = [
            item.strip()
            for item in str(self.config.source_ids or "").split(",")
            if item.strip()
        ]
        return [source_id for source_id in values if source_id in DEFAULT_SOURCES]

    @staticmethod
    def _is_allowed_url(url: str, allowed_url: str) -> bool:
        requested = urlparse(url)
        allowed = urlparse(allowed_url)
        return (
            requested.scheme in {"http", "https"}
            and requested.netloc == allowed.netloc
            and requested.path.startswith(allowed.path.rstrip("/"))
        )
