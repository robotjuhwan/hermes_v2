from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from tradecraft.services.crypto_alpha_score import score_crypto_candidate
from tradecraft.services.crypto_quant import CryptoQuantEngine, CryptoQuantRepository
from tradecraft.services.db_retention import (
    RetentionRule,
    SQLiteRetentionPruner,
)
from tradecraft.services.evidence_policy import evidence_from_signal
from tradecraft.services.jue_language_policy import jue_language_policy
from tradecraft.services.jue_skill_registry import JueSkillRegistry, JueSkillValidationError


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def _json_list(value: Any) -> list[Any]:
    decoded = json_loads(value, [])
    if isinstance(decoded, list):
        return decoded
    return []


def _json_dict(value: Any) -> dict[str, Any]:
    decoded = json_loads(value, {})
    if isinstance(decoded, dict):
        return decoded
    return {}


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


def _normalize_symbol(value: Any) -> str:
    return str(value or "").upper().strip()


def _base_key(symbol: str) -> str:
    for suffix in ("USDT", "USDC", "BUSD", "FDUSD"):
        if symbol.endswith(suffix):
            return symbol.removesuffix(suffix)
    return symbol


def _prompt_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    return text[: max(int(limit), 1)]


def _prompt_scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 8)
    return _prompt_text(value, limit=160)


def _compact_feature_payload(features: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "symbol",
        "price",
        "change_pct_24h",
        "quote_volume_usdt",
        "spread_bps",
        "trend_1m",
        "timeframe_alignment",
        "funding_rate",
        "mark_index_basis_pct",
        "open_interest",
        "squeeze_risk",
        "squeeze_risk_score",
        "entry_quality",
        "entry_quality_score",
        "entry_quality_reasons",
        "score",
        "regime",
    )
    compact = {
        key: (
            [_prompt_text(item, limit=90) for item in features.get(key, [])[:3]]
            if isinstance(features.get(key), list)
            else _prompt_scalar(features.get(key))
        )
        for key in keys
        if key in features
    }
    timeframes = features.get("timeframes")
    if isinstance(timeframes, dict):
        compact["timeframes"] = {
            str(interval): {
                key: _prompt_scalar(row.get(key))
                for key in ("trend", "momentum_pct", "last_close")
                if isinstance(row, dict) and key in row
            }
            for interval, row in list(timeframes.items())[:4]
            if isinstance(row, dict)
        }
    return {key: value for key, value in compact.items() if value not in ({}, [], "", None)}


def _compact_kline_raw_for_storage(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        if len(value) >= 6:
            return {
                "_raw_compacted": True,
                "_raw_type": "exchange_kline_array",
                "_raw_item_count": len(value),
            }
        return list(value)
    if isinstance(value, dict):
        try:
            raw_text = json_dumps(value)
        except (TypeError, ValueError):
            return {"_raw_compacted": True, "_raw_error": "non_json_serializable"}
        if len(raw_text) <= 220:
            return value
        compact = {
            key: value.get(key)
            for key in (
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
                "close_time",
            )
            if key in value
        }
        compact["_raw_compacted"] = True
        compact["_raw_key_count"] = len(value)
        compact["_raw_original_chars"] = len(raw_text)
        return compact
    return value


def _compact_exchange_raw(value: Any, *, keys: tuple[str, ...]) -> Any:
    if not isinstance(value, dict):
        return value
    compact = {
        key: value.get(key)
        for key in keys
        if value.get(key) not in (None, "", [], {})
    }
    compact["_raw_compacted"] = True
    compact["_raw_key_count"] = len(value)
    return compact


def _compact_exchange_payload_for_storage(
    value: Any,
    *,
    raw_keys: tuple[str, ...],
) -> Any:
    if not isinstance(value, dict):
        return value
    compact: dict[str, Any] = {}
    for key, item in value.items():
        if key == "raw":
            compact[key] = _compact_exchange_raw(item, keys=raw_keys)
        elif key == "error_message":
            compact[key] = _prompt_text(item, limit=180)
        else:
            compact[key] = item
    return compact


def _compact_market_snapshot_raw_for_storage(
    *,
    ticker: dict[str, Any],
    book: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticker": _compact_exchange_payload_for_storage(
            ticker,
            raw_keys=(
                "symbol",
                "lastPrice",
                "priceChangePercent",
                "quoteVolume",
                "volume",
                "closeTime",
            ),
        ),
        "book": _compact_exchange_payload_for_storage(
            book,
            raw_keys=(
                "symbol",
                "bidPrice",
                "bidQty",
                "askPrice",
                "askQty",
            ),
        ),
    }


def _compact_derivatives_raw_for_storage(
    *,
    premium: dict[str, Any],
    open_interest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "premium": _compact_exchange_payload_for_storage(
            premium,
            raw_keys=(
                "symbol",
                "markPrice",
                "indexPrice",
                "lastFundingRate",
                "nextFundingTime",
                "time",
            ),
        ),
        "open_interest": _compact_exchange_payload_for_storage(
            open_interest,
            raw_keys=(
                "symbol",
                "openInterest",
                "time",
            ),
        ),
    }


def _compact_context_items(rows: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "symbol": row.get("symbol"),
                "score": _prompt_scalar(row.get("score")),
                "regime": row.get("regime"),
                "updated_at": row.get("updated_at"),
                "features": _compact_feature_payload(_dict_or_empty(row.get("features"))),
            }
        )
        if len(out) >= max(int(limit), 1):
            break
    return out


def _compact_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    block_template = _dict_or_empty(row.get("block_template"))
    return {
        key: value
        for key, value in {
            "symbol": row.get("symbol"),
            "market": row.get("market"),
            "stance": row.get("stance"),
            "horizon": row.get("horizon"),
            "score": _prompt_scalar(row.get("score")),
            "confidence": _prompt_scalar(row.get("confidence")),
            "reason_md": _prompt_text(row.get("reason_md"), limit=180),
            "block_template": {
                name: _prompt_scalar(block_template.get(name))
                for name in (
                    "entry_style",
                    "entry_price",
                    "target_price",
                    "stop_price",
                    "reward_risk",
                    "market",
                    "side",
                    "horizon",
                    "invalidation",
                )
                if name in block_template
            },
        }.items()
        if value not in ({}, [], "", None)
    }


def _compact_note_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "symbol": row.get("symbol"),
            "stance": row.get("stance"),
            "horizon": row.get("horizon"),
            "confidence": _prompt_scalar(row.get("confidence")),
            "summary_md": _prompt_text(row.get("summary_md"), limit=160),
            "reasons": [
                _prompt_text(item, limit=90)
                for item in _bounded_list(row.get("reasons"), limit=2)
            ],
            "risks": [
                _prompt_text(item, limit=90)
                for item in _bounded_list(row.get("risks"), limit=2)
            ],
            "triggers": [
                _prompt_text(item, limit=90)
                for item in _bounded_list(row.get("triggers"), limit=2)
            ],
        }.items()
        if value not in ({}, [], "", None)
    }


def _compact_policy_effect(effect: Any) -> dict[str, Any]:
    payload = _dict_or_empty(effect)
    keys = (
        "action",
        "status",
        "policy_mode",
        "entry_bias",
        "sizing_policy",
        "target_stop_review",
        "scale_blocker",
        "risk_budget_multiplier",
        "max_budget_multiplier",
        "min_reward_risk",
        "hard_filter",
        "safety_gate_override",
    )
    return {
        key: _prompt_scalar(payload.get(key))
        for key in keys
        if key in payload and _prompt_scalar(payload.get(key)) not in ({}, [], "", None)
    }


def _compact_policy_rule(row: Any, *, include_effect: bool = True) -> dict[str, Any]:
    payload = _dict_or_empty(row)
    effect = _compact_policy_effect(payload.get("effect")) if include_effect else {}
    compact = {
        "policy_id": _prompt_text(payload.get("policy_id"), limit=90),
        "rule_id": _prompt_text(payload.get("rule_id"), limit=110),
        "action": _prompt_text(payload.get("action"), limit=32),
        "status": _prompt_text(payload.get("status"), limit=48),
        "version": _prompt_scalar(payload.get("version")),
        "reason": _prompt_text(payload.get("reason"), limit=180),
    }
    if effect:
        compact["effect"] = effect
    return {
        key: value
        for key, value in compact.items()
        if value not in ({}, [], "", None)
    }


def _compact_policy_rule_list(rows: Any, *, limit: int = 6, include_effect: bool = True) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        compact = _compact_policy_rule(row, include_effect=include_effect)
        signature = "|".join(
            str(compact.get(key) or "")
            for key in ("policy_id", "rule_id", "action", "status", "version")
        )
        if signature in seen:
            continue
        seen.add(signature)
        out.append(compact)
        if len(out) >= max(int(limit), 1):
            break
    return out


def _compact_policy_rule_evaluation(value: Any) -> dict[str, Any]:
    payload = _dict_or_empty(value)
    by_symbol = _dict_or_empty(payload.get("by_symbol"))
    symbol_rows: list[dict[str, Any]] = []
    for symbol, rows in sorted(by_symbol.items())[:12]:
        if not isinstance(rows, list):
            continue
        compact_rules = _compact_policy_rule_list(
            rows,
            limit=3,
            include_effect=False,
        )
        symbol_rows.append(
            {
                "symbol": _prompt_text(symbol, limit=24),
                "rule_count": len(rows),
                "top_rules": compact_rules,
            }
        )
    compact = {
        "status": payload.get("status"),
        "active_rule_count": _prompt_scalar(payload.get("active_rule_count")),
        "applied_count": _prompt_scalar(payload.get("applied_count")),
        "active_rules": _compact_policy_rule_list(
            payload.get("active_rules"),
            limit=6,
            include_effect=True,
        ),
        "global": _compact_policy_rule_list(
            payload.get("global"),
            limit=6,
            include_effect=True,
        ),
        "by_symbol_count": len(by_symbol),
        "by_symbol": symbol_rows,
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in ({}, [], "", None)
    }


def _compact_market_pulse(value: Any) -> dict[str, Any]:
    payload = _dict_or_empty(value)
    keys = (
        "status",
        "regime",
        "market_regime",
        "summary",
        "summary_md",
        "updated_at",
        "captured_at",
    )
    compact = {
        key: _prompt_text(payload.get(key), limit=300)
        for key in keys
        if key in payload
    }
    for key in ("signals", "risks", "opportunities"):
        items = [
            _prompt_text(item, limit=120)
            for item in _bounded_list(payload.get(key), limit=4)
        ]
        if items:
            compact[key] = items
    return {
        key: value
        for key, value in compact.items()
        if value not in ({}, [], "", None)
    }


def _compact_research_packet(packet: dict[str, Any]) -> dict[str, Any]:
    market_context = _dict_or_empty(packet.get("market_context"))
    compact_market = {
        "status": market_context.get("status"),
        "market_regime": market_context.get("market_regime"),
        "regime_brief": market_context.get("regime_brief"),
        "observed_symbol_count": market_context.get("observed_symbol_count"),
        "focus_symbol_count": market_context.get("focus_symbol_count"),
        "items": _compact_context_items(market_context.get("items"), limit=8),
        "candidates": [
            _compact_candidate_row(row)
            for row in _bounded_list(market_context.get("candidates"), limit=6)
            if isinstance(row, dict)
        ],
        "symbol_notes": [
            _compact_note_row(row)
            for row in _bounded_list(market_context.get("symbol_notes"), limit=6)
            if isinstance(row, dict)
        ],
        "quant": {
            "status": _dict_or_empty(market_context.get("quant")).get("status"),
            "items": _bounded_list(
                _dict_or_empty(market_context.get("quant")).get("items"),
                limit=4,
            ),
        },
        "external_context": [
            {
                "source_id": row.get("source_id"),
                "key": row.get("key"),
                "captured_at": row.get("captured_at"),
                "payload": {
                    key: _prompt_scalar(value)
                    for key, value in list(_dict_or_empty(row.get("payload")).items())[:6]
                },
            }
            for row in _bounded_list(market_context.get("external_context"), limit=4)
            if isinstance(row, dict)
        ],
    }
    memory_context = _dict_or_empty(packet.get("memory_context"))
    compact_memory = {
        key: value
        for key, value in {
            "status": memory_context.get("status"),
            "market_pulse": _compact_market_pulse(memory_context.get("market_pulse")),
            "scoped_memory": {
                "local": _bounded_list(
                    _dict_or_empty(memory_context.get("scoped_memory")).get("local"),
                    limit=4,
                ),
                "transferred": _bounded_list(
                    _dict_or_empty(memory_context.get("scoped_memory")).get("transferred"),
                    limit=2,
                ),
            },
            "policy_rule_evaluation": _compact_policy_rule_evaluation(
                memory_context.get("policy_rule_evaluation")
            ),
        }.items()
        if value not in ({}, [], "", None)
    }
    return {
        "generated_at": packet.get("generated_at"),
        "symbols": _bounded_list(packet.get("symbols"), limit=8),
        "observed_symbol_count": packet.get("observed_symbol_count"),
        "focus_symbol_count": min(int(packet.get("focus_symbol_count") or 0), 8),
        "market_regime": packet.get("market_regime"),
        "market_context": {
            key: value
            for key, value in compact_market.items()
            if value not in ({}, [], "", None)
        },
        "memory_context": compact_memory or {"status": "missing"},
    }


COINGECKO_IDS: dict[str, str] = {
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "BNB": "binancecoin",
    "BTC": "bitcoin",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "ETH": "ethereum",
    "LINK": "chainlink",
    "SOL": "solana",
    "TON": "the-open-network",
    "XRP": "ripple",
}

DEFILLAMA_CHAIN_NAMES: dict[str, str] = {
    "AVAX": "Avalanche",
    "BNB": "BSC",
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "TON": "TON",
}

STABLE_BASE_ASSETS = {
    "AEUR",
    "BUSD",
    "DAI",
    "EURI",
    "FDUSD",
    "RLUSD",
    "TUSD",
    "U",
    "USDC",
    "USDP",
    "USDT",
    "USD1",
    "XUSD",
}
FIAT_BASE_ASSETS = {"AUD", "BRL", "EUR", "GBP", "JPY", "KRW", "TRY", "USD"}
LEVERAGED_TOKEN_SUFFIXES = (
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
)

_RESEARCH_RUN_COMPACTION_REASON = "crypto_research_run_payload_retention"


@dataclass(slots=True)
class CryptoMarketResearchConfig:
    db_path: str = ".runtime/crypto_market_research.db"
    max_symbols: int = 300
    llm_model: str = "gpt-5.5"
    llm_reasoning_effort: str = "xhigh"
    external_enabled: bool = True
    external_sources: str = "coingecko,defillama,fear_greed"
    auto_universe_enabled: bool = True
    auto_universe_limit: int = 300
    research_universe_limit: int = 80
    llm_top_symbols: int = 30
    min_quote_volume_usdt: float = 100_000.0
    kline_intervals: dict[str, int] | None = None
    kline_hot_window_rows: int = 720
    market_hot_window_rows: int = 720
    regime_enabled: bool = True
    squeeze_guard_enabled: bool = True
    collect_symbol_timeout_sec: float = 20.0
    collect_cycle_timeout_sec: float = 240.0
    collect_concurrency: int = 4


class CryptoMarketResearchRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

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
                CREATE TABLE IF NOT EXISTS crypto_symbols (
                    symbol TEXT PRIMARY KEY,
                    base_asset TEXT NOT NULL DEFAULT '',
                    quote_asset TEXT NOT NULL DEFAULT '',
                    spot_enabled INTEGER NOT NULL DEFAULT 0,
                    futures_enabled INTEGER NOT NULL DEFAULT 0,
                    liquidity_tier TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crypto_market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    price REAL NOT NULL DEFAULT 0,
                    quote_volume_usdt REAL NOT NULL DEFAULT 0,
                    change_pct_24h REAL NOT NULL DEFAULT 0,
                    spread_bps REAL NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_market_snapshots_symbol
                    ON crypto_market_snapshots(symbol, market, captured_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_klines (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    interval TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL DEFAULT 0,
                    high REAL NOT NULL DEFAULT 0,
                    low REAL NOT NULL DEFAULT 0,
                    close REAL NOT NULL DEFAULT 0,
                    volume REAL NOT NULL DEFAULT 0,
                    quote_volume REAL NOT NULL DEFAULT 0,
                    close_time INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(symbol, market, interval, open_time)
                );

                CREATE TABLE IF NOT EXISTS crypto_derivatives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    mark_price REAL NOT NULL DEFAULT 0,
                    index_price REAL NOT NULL DEFAULT 0,
                    funding_rate REAL NOT NULL DEFAULT 0,
                    next_funding_time INTEGER NOT NULL DEFAULT 0,
                    open_interest REAL NOT NULL DEFAULT 0,
                    long_short_ratio REAL NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_derivatives_symbol
                    ON crypto_derivatives(symbol, captured_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_features (
                    symbol TEXT PRIMARY KEY,
                    feature_json TEXT NOT NULL DEFAULT '{}',
                    score REAL NOT NULL DEFAULT 0,
                    regime TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crypto_observed_universe (
                    symbol TEXT PRIMARY KEY,
                    rank INTEGER NOT NULL DEFAULT 0,
                    quote_volume_usdt REAL NOT NULL DEFAULT 0,
                    change_pct_24h REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_observed_universe_rank
                    ON crypto_observed_universe(rank ASC, observed_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_regime_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    regime TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_regime_snapshots_captured
                    ON crypto_regime_snapshots(captured_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_external_context (
                    source_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, key)
                );

                CREATE TABLE IF NOT EXISTS crypto_research_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'llm',
                    model TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS crypto_symbol_notes (
                    symbol TEXT PRIMARY KEY,
                    stance TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    summary_md TEXT NOT NULL DEFAULT '',
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    triggers_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crypto_candidates (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    stance TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    reason_md TEXT NOT NULL DEFAULT '',
                    block_template_json TEXT NOT NULL DEFAULT '{}',
                    source_run_id INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, market, stance, horizon)
                );
                """
            )
            self._ensure_candidate_lane_schema(conn)

    def _ensure_candidate_lane_schema(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(crypto_candidates)").fetchall()
        pk_cols = [
            str(row["name"])
            for row in sorted(rows, key=lambda item: int(item["pk"] or 0))
            if int(row["pk"] or 0) > 0
        ]
        if pk_cols == ["symbol"]:
            conn.execute("ALTER TABLE crypto_candidates RENAME TO crypto_candidates_legacy")
            conn.execute(
                """
                CREATE TABLE crypto_candidates (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    stance TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    reason_md TEXT NOT NULL DEFAULT '',
                    block_template_json TEXT NOT NULL DEFAULT '{}',
                    source_run_id INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, market, stance, horizon)
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO crypto_candidates (
                    symbol, market, stance, horizon, score, confidence, reason_md,
                    block_template_json, source_run_id, updated_at
                )
                SELECT symbol, market, stance, horizon, score, confidence, reason_md,
                    block_template_json, source_run_id, updated_at
                FROM crypto_candidates_legacy
                """
            )
            conn.execute("DROP TABLE crypto_candidates_legacy")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_crypto_candidates_score
                ON crypto_candidates(score DESC, updated_at DESC)
            """
        )

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            snapshot_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM crypto_market_snapshots"
                ).fetchone()[0]
            )
            candidate_count = int(
                conn.execute("SELECT COUNT(*) FROM crypto_candidates").fetchone()[0]
            )
            feature_count = int(
                conn.execute("SELECT COUNT(*) FROM crypto_features").fetchone()[0]
            )
            observed_count = int(
                conn.execute("SELECT COUNT(*) FROM crypto_observed_universe").fetchone()[0]
            )
            latest_feature_at = conn.execute(
                "SELECT MAX(updated_at) FROM crypto_features"
            ).fetchone()[0]
            latest_observed_at = conn.execute(
                "SELECT MAX(observed_at) FROM crypto_observed_universe"
            ).fetchone()[0]
        return {
            "status": "ok",
            "db_path": str(self.path),
            "snapshot_count": snapshot_count,
            "candidate_count": candidate_count,
            "feature_count": feature_count,
            "observed_count": observed_count,
            "latest_feature_at": latest_feature_at or "",
            "latest_observed_at": latest_observed_at or "",
        }

    def upsert_symbol(self, symbol: str, *, spot: bool = True, futures: bool = True) -> None:
        base_asset = _base_key(symbol)
        quote_asset = symbol.removeprefix(base_asset)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_symbols (
                    symbol, base_asset, quote_asset, spot_enabled,
                    futures_enabled, liquidity_tier, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    base_asset = excluded.base_asset,
                    quote_asset = excluded.quote_asset,
                    spot_enabled = excluded.spot_enabled,
                    futures_enabled = excluded.futures_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    base_asset,
                    quote_asset,
                    int(spot),
                    int(futures),
                    "",
                    utc_now_iso(),
                ),
            )

    def save_market_snapshot(
        self,
        *,
        symbol: str,
        market: str,
        ticker: dict[str, Any],
        book: dict[str, Any],
        captured_at: str,
    ) -> None:
        bid = _to_float(book.get("bid"))
        ask = _to_float(book.get("ask"))
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        price = _to_float(ticker.get("price")) or mid
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_market_snapshots (
                    symbol, market, price, quote_volume_usdt, change_pct_24h,
                    spread_bps, raw_json, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    market,
                    price,
                    _to_float(ticker.get("quote_volume")),
                    _to_float(ticker.get("change_pct_24h")),
                    _to_float(book.get("spread_bps")),
                    json_dumps(
                        _compact_market_snapshot_raw_for_storage(
                            ticker=ticker,
                            book=book,
                        )
                    ),
                    captured_at,
                ),
            )

    def save_klines(
        self,
        *,
        symbol: str,
        market: str,
        interval: str,
        rows: list[dict[str, Any]],
    ) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO crypto_klines (
                    symbol, market, interval, open_time, open, high, low, close,
                    volume, quote_volume, close_time, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, market, interval, open_time) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    quote_volume = excluded.quote_volume,
                    close_time = excluded.close_time,
                    raw_json = excluded.raw_json
                """,
                [
                    (
                        symbol,
                        market,
                        interval,
                        int(_to_float(row.get("open_time"))),
                        _to_float(row.get("open")),
                        _to_float(row.get("high")),
                        _to_float(row.get("low")),
                        _to_float(row.get("close")),
                        _to_float(row.get("volume")),
                        _to_float(row.get("quote_volume")),
                        int(_to_float(row.get("close_time"))),
                        json_dumps(_compact_kline_raw_for_storage(row.get("raw") or row)),
                    )
                    for row in rows
                    if isinstance(row, dict)
                ],
            )

    def prune_kline_hot_windows(self, *, max_rows_per_group: int) -> dict[str, Any]:
        limit = int(max_rows_per_group)
        if limit <= 0:
            return {
                "status": "skipped",
                "reason": "kline_hot_window_disabled",
                "limit": limit,
                "deleted": 0,
            }
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT interval, COUNT(*) AS count
                FROM crypto_klines
                WHERE rowid IN (
                    SELECT rowid
                    FROM (
                        SELECT
                            rowid,
                            ROW_NUMBER() OVER (
                                PARTITION BY symbol, market, interval
                                ORDER BY close_time DESC, open_time DESC
                            ) AS row_number
                        FROM crypto_klines
                    )
                    WHERE row_number > ?
                )
                GROUP BY interval
                """,
                (limit,),
            ).fetchall()
            deleted_by_interval = {
                str(row["interval"]): int(row["count"] or 0) for row in rows
            }
            deleted = int(
                conn.execute(
                    """
                    DELETE FROM crypto_klines
                    WHERE rowid IN (
                        SELECT rowid
                        FROM (
                            SELECT
                                rowid,
                                ROW_NUMBER() OVER (
                                    PARTITION BY symbol, market, interval
                                    ORDER BY close_time DESC, open_time DESC
                                ) AS row_number
                            FROM crypto_klines
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
            "limit": limit,
            "deleted": deleted,
            "deleted_by_interval": deleted_by_interval,
        }

    def prune_market_hot_windows(self, *, max_rows_per_group: int) -> dict[str, Any]:
        limit = int(max_rows_per_group)
        if limit <= 0:
            return {
                "status": "skipped",
                "reason": "market_hot_window_disabled",
                "limit": limit,
                "snapshots_deleted": 0,
                "derivatives_deleted": 0,
            }
        with self._connect() as conn:
            snapshots_deleted = int(
                conn.execute(
                    """
                    DELETE FROM crypto_market_snapshots
                    WHERE id IN (
                        SELECT id
                        FROM (
                            SELECT
                                id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY symbol, market
                                    ORDER BY captured_at DESC, id DESC
                                ) AS row_number
                            FROM crypto_market_snapshots
                        )
                        WHERE row_number > ?
                    )
                    """,
                    (limit,),
                ).rowcount
                or 0
            )
            derivatives_deleted = int(
                conn.execute(
                    """
                    DELETE FROM crypto_derivatives
                    WHERE id IN (
                        SELECT id
                        FROM (
                            SELECT
                                id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY symbol
                                    ORDER BY captured_at DESC, id DESC
                                ) AS row_number
                            FROM crypto_derivatives
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
            "limit": limit,
            "snapshots_deleted": snapshots_deleted,
            "derivatives_deleted": derivatives_deleted,
        }

    def save_derivatives(
        self,
        *,
        symbol: str,
        premium: dict[str, Any],
        open_interest: dict[str, Any],
        captured_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_derivatives (
                    symbol, mark_price, index_price, funding_rate,
                    next_funding_time, open_interest, long_short_ratio,
                    raw_json, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    _to_float(premium.get("mark_price")),
                    _to_float(premium.get("index_price")),
                    _to_float(premium.get("funding_rate")),
                    int(_to_float(premium.get("next_funding_time"))),
                    _to_float(open_interest.get("open_interest")),
                    _to_float(premium.get("long_short_ratio")),
                    json_dumps(
                        _compact_derivatives_raw_for_storage(
                            premium=premium,
                            open_interest=open_interest,
                        )
                    ),
                    captured_at,
                ),
            )

    def compact_verbose_raw_payloads(
        self,
        *,
        batch_size: int = 5_000,
        vacuum: bool = False,
    ) -> dict[str, Any]:
        limit = max(int(batch_size), 1)
        specs = {
            "crypto_market_snapshots": lambda payload: (
                _compact_market_snapshot_raw_for_storage(
                    ticker=_dict_or_empty(payload.get("ticker")),
                    book=_dict_or_empty(payload.get("book")),
                )
            ),
            "crypto_derivatives": lambda payload: (
                _compact_derivatives_raw_for_storage(
                    premium=_dict_or_empty(payload.get("premium")),
                    open_interest=_dict_or_empty(payload.get("open_interest")),
                )
            ),
        }
        updated: dict[str, int] = {}
        remaining: dict[str, int] = {}
        with self._connect() as conn:
            for table, transform in specs.items():
                rows = conn.execute(
                    f"""
                    SELECT id, raw_json
                    FROM {table}
                    WHERE raw_json LIKE '%"raw"%'
                      AND raw_json NOT LIKE '%"_raw_compacted"%'
                    ORDER BY id
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                changed = 0
                for row in rows:
                    payload = _json_dict(row["raw_json"])
                    if not payload:
                        continue
                    compact_json = json_dumps(transform(payload))
                    if compact_json == str(row["raw_json"] or ""):
                        continue
                    conn.execute(
                        f"UPDATE {table} SET raw_json = ? WHERE id = ?",
                        (compact_json, row["id"]),
                    )
                    changed += 1
                updated[table] = changed
                remaining[table] = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {table}
                        WHERE raw_json LIKE '%"raw"%'
                          AND raw_json NOT LIKE '%"_raw_compacted"%'
                        """
                    ).fetchone()[0]
                    or 0
                )
        vacuumed = False
        if vacuum and any(updated.values()):
            with self._connect() as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "batch_size": limit,
            "updated": updated,
            "remaining": remaining,
            "vacuumed": vacuumed,
        }

    def upsert_features(self, symbol: str, features: dict[str, Any]) -> None:
        regime = str(features.get("regime") or features.get("trend_1m") or "")
        score = _to_float(features.get("score"))
        if score <= 0:
            score = self._score_features(features)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_features (symbol, feature_json, score, regime, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    feature_json = excluded.feature_json,
                    score = excluded.score,
                    regime = excluded.regime,
                    updated_at = excluded.updated_at
                """,
                (symbol, json_dumps(features), score, regime, utc_now_iso()),
            )

    def latest_features(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        max_rows = max(int(limit), 1)
        clean_symbols = [_normalize_symbol(symbol) for symbol in symbols or []]
        clean_symbols = [symbol for symbol in clean_symbols if symbol]
        with self._connect() as conn:
            if clean_symbols:
                placeholders = ",".join("?" for _ in clean_symbols)
                rows = conn.execute(
                    f"""
                    SELECT symbol, feature_json, score, regime, updated_at
                    FROM crypto_features
                    WHERE symbol IN ({placeholders})
                    ORDER BY score DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (*clean_symbols, max_rows),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT symbol, feature_json, score, regime, updated_at
                    FROM crypto_features
                    ORDER BY score DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (max_rows,),
                ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "features": _json_dict(row["feature_json"]),
                "score": float(row["score"]),
                "regime": str(row["regime"] or ""),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def save_observed_universe(
        self,
        symbols: list[str],
        *,
        dynamic_rows: list[dict[str, Any]] | None = None,
        source: str = "auto_universe",
    ) -> dict[str, Any]:
        clean_symbols = [_normalize_symbol(symbol) for symbol in symbols]
        clean_symbols = [symbol for symbol in clean_symbols if symbol]
        by_symbol = {
            str(row.get("symbol") or "").upper().strip(): row
            for row in dynamic_rows or []
            if isinstance(row, dict)
        }
        observed_at = utc_now_iso()
        with self._connect() as conn:
            for rank, symbol in enumerate(clean_symbols, start=1):
                row = by_symbol.get(symbol, {})
                conn.execute(
                    """
                    INSERT INTO crypto_observed_universe (
                        symbol, rank, quote_volume_usdt, change_pct_24h,
                        source, observed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        rank = excluded.rank,
                        quote_volume_usdt = excluded.quote_volume_usdt,
                        change_pct_24h = excluded.change_pct_24h,
                        source = excluded.source,
                        observed_at = excluded.observed_at
                    """,
                    (
                        symbol,
                        rank,
                        _to_float(row.get("quote_volume_usdt")),
                        _to_float(row.get("change_pct_24h")),
                        source,
                        observed_at,
                    ),
                )
        return {
            "status": "ok",
            "observed_count": len(clean_symbols),
            "observed_at": observed_at,
        }

    def observed_symbol_count(self) -> int:
        with self._connect() as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM crypto_observed_universe").fetchone()[0]
            )

    def feature_symbol_count(self, *, symbols: list[str] | None = None) -> int:
        clean_symbols = [_normalize_symbol(symbol) for symbol in symbols or []]
        clean_symbols = [symbol for symbol in clean_symbols if symbol]
        with self._connect() as conn:
            if clean_symbols:
                placeholders = ",".join("?" for _ in clean_symbols)
                return int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM crypto_features WHERE symbol IN ({placeholders})",
                        tuple(clean_symbols),
                    ).fetchone()[0]
                )
            return int(conn.execute("SELECT COUNT(*) FROM crypto_features").fetchone()[0])

    def save_regime_snapshot(self, payload: dict[str, Any]) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO crypto_regime_snapshots (regime, payload_json, captured_at)
                VALUES (?, ?, ?)
                """,
                (
                    str(payload.get("regime") or ""),
                    json_dumps(payload),
                    utc_now_iso(),
                ),
            )
            return int(cursor.lastrowid)

    def prune_history(
        self,
        *,
        retention_days: int = 30,
        archive_retention_days: int = 0,
        research_run_recent_count: int = 48,
        research_run_payload_min_chars: int = 20_000,
    ) -> dict[str, Any]:
        if int(retention_days) <= 0:
            return {"status": "skipped", "reason": "retention_disabled"}
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=int(retention_days))
        cutoff_iso = cutoff_dt.isoformat()
        archive_floor_iso: str | None = None
        archive_floor_ms: int | None = None
        cold_retention: dict[str, Any] = {
            "status": "skipped",
            "reason": "cold_retention_disabled",
        }
        if int(archive_retention_days) > int(retention_days):
            archive_floor_dt = datetime.now(timezone.utc) - timedelta(
                days=int(archive_retention_days)
            )
            archive_floor_iso = archive_floor_dt.isoformat()
            archive_floor_ms = int(archive_floor_dt.timestamp() * 1000)
            cold_retention = SQLiteRetentionPruner(self.path).prune(
                [
                    RetentionRule(
                        table="crypto_market_snapshots",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                    ),
                    RetentionRule(
                        table="crypto_derivatives",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                    ),
                    RetentionRule(
                        table="crypto_regime_snapshots",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                    ),
                    RetentionRule(
                        table="crypto_klines",
                        timestamp_column="close_time",
                        retention_days=int(archive_retention_days),
                        timestamp_kind="unix_ms",
                        minimum_timestamp_value=0,
                    ),
                ]
            )
        retention = SQLiteRetentionPruner(self.path).prune(
            [
                RetentionRule(
                    table="crypto_market_snapshots",
                    timestamp_column="captured_at",
                    retention_days=int(retention_days),
                    archive_table="crypto_market_snapshots_archive",
                    archive_compress_columns=("raw_json",),
                    minimum_timestamp_value=archive_floor_iso,
                ),
                RetentionRule(
                    table="crypto_derivatives",
                    timestamp_column="captured_at",
                    retention_days=int(retention_days),
                    archive_table="crypto_derivatives_archive",
                    archive_compress_columns=("raw_json",),
                    minimum_timestamp_value=archive_floor_iso,
                ),
                RetentionRule(
                    table="crypto_regime_snapshots",
                    timestamp_column="captured_at",
                    retention_days=int(retention_days),
                    archive_table="crypto_regime_snapshots_archive",
                    archive_compress_columns=("payload_json",),
                    minimum_timestamp_value=archive_floor_iso,
                ),
                RetentionRule(
                    table="crypto_klines",
                    timestamp_column="close_time",
                    retention_days=int(retention_days),
                    timestamp_kind="unix_ms",
                    archive_table="crypto_klines_archive",
                    archive_compress_columns=("raw_json",),
                    minimum_timestamp_value=archive_floor_ms or 0,
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
                        table="crypto_market_snapshots_archive",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                        vacuum_after_delete=True,
                    ),
                    RetentionRule(
                        table="crypto_derivatives_archive",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                    ),
                    RetentionRule(
                        table="crypto_regime_snapshots_archive",
                        timestamp_column="captured_at",
                        retention_days=int(archive_retention_days),
                    ),
                    RetentionRule(
                        table="crypto_klines_archive",
                        timestamp_column="close_time",
                        retention_days=int(archive_retention_days),
                        timestamp_kind="unix_ms",
                        minimum_timestamp_value=0,
                    ),
                ]
            )
        deleted: dict[str, int] = {}
        archived: dict[str, int] = {}
        for table, row in dict(retention.get("tables") or {}).items():
            if not isinstance(row, dict) or row.get("status") != "ok":
                continue
            deleted[str(table)] = int(row.get("deleted") or 0)
            archived[str(table)] = int(row.get("archived") or 0)
        archive_deleted: dict[str, int] = {}
        for table, row in dict(archive_retention.get("tables") or {}).items():
            if not isinstance(row, dict) or row.get("status") != "ok":
                continue
            archive_deleted[str(table)] = int(row.get("deleted") or 0)
        cold_deleted: dict[str, int] = {}
        for table, row in dict(cold_retention.get("tables") or {}).items():
            if not isinstance(row, dict) or row.get("status") != "ok":
                continue
            cold_deleted[str(table)] = int(row.get("deleted") or 0)
        compacted = self._compact_old_research_run_payloads(
            recent_count=research_run_recent_count,
            min_chars=research_run_payload_min_chars,
        )
        return {
            "status": "ok",
            "cutoff": cutoff_iso,
            "market_snapshots_deleted": deleted.get("crypto_market_snapshots", 0),
            "derivatives_deleted": deleted.get("crypto_derivatives", 0),
            "regime_snapshots_deleted": deleted.get("crypto_regime_snapshots", 0),
            "klines_deleted": deleted.get("crypto_klines", 0),
            "archived": archived,
            "archive_deleted": archive_deleted,
            "cold_deleted": cold_deleted,
            "retention": retention,
            "archive_retention": archive_retention,
            "cold_retention": cold_retention,
            "compacted": compacted,
        }

    def _compact_old_research_run_payloads(
        self,
        *,
        recent_count: int,
        min_chars: int,
    ) -> dict[str, Any]:
        keep_recent = max(int(recent_count or 0), 0)
        threshold = max(int(min_chars or 0), 0)
        if threshold <= 0:
            return {
                "crypto_research_runs": 0,
                "status": "skipped",
                "reason": "payload_compaction_disabled",
                "recent_count": keep_recent,
                "min_chars": threshold,
            }
        compacted = 0
        skipped_recent = 0
        skipped_small = 0
        skipped_already_compacted = 0
        compacted_at = utc_now_iso()
        with self._connect() as conn:
            recent_ids: set[int] = set()
            if keep_recent > 0:
                recent_rows = conn.execute(
                    """
                    SELECT id
                    FROM crypto_research_runs
                    ORDER BY run_at DESC, id DESC
                    LIMIT ?
                    """,
                    (keep_recent,),
                ).fetchall()
                recent_ids = {int(row["id"]) for row in recent_rows}
            rows = conn.execute(
                """
                SELECT id, run_at, status, mode, model, prompt_json, response_json
                FROM crypto_research_runs
                ORDER BY run_at ASC, id ASC
                """
            ).fetchall()
            for row in rows:
                run_id = int(row["id"])
                if run_id in recent_ids:
                    skipped_recent += 1
                    continue
                prompt_text = str(row["prompt_json"] or "{}")
                response_text = str(row["response_json"] or "{}")
                if len(prompt_text) + len(response_text) < threshold:
                    skipped_small += 1
                    continue
                prompt_compacted = self._is_research_run_payload_compacted(prompt_text)
                response_compacted = self._is_research_run_payload_compacted(response_text)
                if prompt_compacted and response_compacted:
                    skipped_already_compacted += 1
                    continue
                prompt_next = (
                    prompt_text
                    if prompt_compacted
                    else json_dumps(
                        self._research_run_compaction_marker(
                            row,
                            field="prompt_json",
                            original_chars=len(prompt_text),
                            compacted_at=compacted_at,
                            recent_count=keep_recent,
                        )
                    )
                )
                response_next = (
                    response_text
                    if response_compacted
                    else json_dumps(
                        self._research_run_compaction_marker(
                            row,
                            field="response_json",
                            original_chars=len(response_text),
                            compacted_at=compacted_at,
                            recent_count=keep_recent,
                        )
                    )
                )
                conn.execute(
                    """
                    UPDATE crypto_research_runs
                    SET prompt_json = ?, response_json = ?
                    WHERE id = ?
                    """,
                    (prompt_next, response_next, run_id),
                )
                compacted += 1
        if compacted:
            with sqlite3.connect(str(self.path), isolation_level=None) as conn:
                conn.execute("VACUUM")
        return {
            "crypto_research_runs": compacted,
            "status": "ok",
            "recent_count": keep_recent,
            "min_chars": threshold,
            "skipped_recent": skipped_recent,
            "skipped_small": skipped_small,
            "skipped_already_compacted": skipped_already_compacted,
            "vacuumed": bool(compacted),
        }

    @staticmethod
    def _is_research_run_payload_compacted(value: str) -> bool:
        payload = _json_dict(value)
        return (
            payload.get("compacted") is True
            and payload.get("reason") == _RESEARCH_RUN_COMPACTION_REASON
        )

    @staticmethod
    def _research_run_compaction_marker(
        row: sqlite3.Row,
        *,
        field: str,
        original_chars: int,
        compacted_at: str,
        recent_count: int,
    ) -> dict[str, Any]:
        return {
            "compacted": True,
            "reason": _RESEARCH_RUN_COMPACTION_REASON,
            "field": field,
            "run_id": int(row["id"]),
            "run_at": str(row["run_at"] or ""),
            "status": str(row["status"] or ""),
            "mode": str(row["mode"] or ""),
            "model": str(row["model"] or ""),
            "original_chars": int(original_chars),
            "compacted_at": compacted_at,
            "recent_run_count": int(recent_count),
        }

    def upsert_external_context(
        self,
        *,
        source_id: str,
        key: str,
        payload: dict[str, Any],
        captured_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_external_context (
                    source_id, key, payload_json, captured_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id, key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    captured_at = excluded.captured_at
                """,
                (source_id, key, json_dumps(payload), captured_at),
            )

    def list_external_context(
        self,
        *,
        keys: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        max_rows = max(int(limit), 1)
        clean_keys = [str(key or "").upper().strip() for key in keys or []]
        clean_keys = [key for key in clean_keys if key]
        with self._connect() as conn:
            if clean_keys:
                placeholders = ",".join("?" for _ in clean_keys)
                rows = conn.execute(
                    f"""
                    SELECT source_id, key, payload_json, captured_at
                    FROM crypto_external_context
                    WHERE UPPER(key) IN ({placeholders})
                    ORDER BY captured_at DESC
                    LIMIT ?
                    """,
                    (*clean_keys, max_rows),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT source_id, key, payload_json, captured_at
                    FROM crypto_external_context
                    ORDER BY captured_at DESC
                    LIMIT ?
                    """,
                    (max_rows,),
                ).fetchall()
        return [
            {
                "source_id": str(row["source_id"]),
                "key": str(row["key"]),
                "payload": _json_dict(row["payload_json"]),
                "captured_at": str(row["captured_at"]),
            }
            for row in rows
        ]

    def save_research_run(
        self,
        *,
        status: str,
        mode: str,
        model: str,
        prompt: dict[str, Any],
        response: dict[str, Any],
        error_message: str,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO crypto_research_runs (
                    run_at, status, mode, model, prompt_json, response_json,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    status,
                    mode,
                    model,
                    json_dumps(prompt),
                    json_dumps(response),
                    error_message,
                ),
            )
            return int(cursor.lastrowid)

    def upsert_symbol_note(self, note: dict[str, Any]) -> None:
        symbol = _normalize_symbol(note.get("symbol"))
        if not symbol:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_symbol_notes (
                    symbol, stance, horizon, confidence, summary_md,
                    reasons_json, risks_json, triggers_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    stance = excluded.stance,
                    horizon = excluded.horizon,
                    confidence = excluded.confidence,
                    summary_md = excluded.summary_md,
                    reasons_json = excluded.reasons_json,
                    risks_json = excluded.risks_json,
                    triggers_json = excluded.triggers_json,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    str(note.get("stance") or ""),
                    str(note.get("horizon") or ""),
                    _to_float(note.get("confidence")),
                    str(note.get("summary_md") or "")[:2400],
                    json_dumps(_bounded_list(note.get("reasons"), limit=12)),
                    json_dumps(_bounded_list(note.get("risks"), limit=12)),
                    json_dumps(_bounded_list(note.get("triggers"), limit=12)),
                    utc_now_iso(),
                ),
            )

    def upsert_candidate(self, candidate: dict[str, Any], *, source_run_id: int) -> None:
        symbol = _normalize_symbol(candidate.get("symbol"))
        if not symbol:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_candidates (
                    symbol, market, stance, horizon, score, confidence, reason_md,
                    block_template_json, source_run_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, market, stance, horizon) DO UPDATE SET
                    score = excluded.score,
                    confidence = excluded.confidence,
                    reason_md = excluded.reason_md,
                    block_template_json = excluded.block_template_json,
                    source_run_id = excluded.source_run_id,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    str(candidate.get("market") or "spot"),
                    str(candidate.get("stance") or ""),
                    str(candidate.get("horizon") or ""),
                    _to_float(candidate.get("score")),
                    _to_float(candidate.get("confidence")),
                    str(candidate.get("reason_md") or "")[:2400],
                    json_dumps(_dict_or_empty(candidate.get("block_template"))),
                    int(source_run_id),
                    utc_now_iso(),
                ),
            )

    def latest_candidates(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
        max_age_sec: int = 86_400,
    ) -> list[dict[str, Any]]:
        max_rows = max(int(limit), 1)
        clean_symbols = [_normalize_symbol(symbol) for symbol in symbols or []]
        clean_symbols = [symbol for symbol in clean_symbols if symbol]
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(seconds=max(int(max_age_sec), 1))).isoformat()
            if int(max_age_sec) > 0
            else ""
        )
        with self._connect() as conn:
            if clean_symbols:
                placeholders = ",".join("?" for _ in clean_symbols)
                age_clause = "AND updated_at >= ?" if cutoff else ""
                params: tuple[Any, ...] = (
                    (*clean_symbols, cutoff, max_rows)
                    if cutoff
                    else (*clean_symbols, max_rows)
                )
                rows = conn.execute(
                    f"""
                    SELECT symbol, market, stance, horizon, score, confidence,
                        reason_md, block_template_json, source_run_id, updated_at
                    FROM crypto_candidates
                    WHERE symbol IN ({placeholders})
                    {age_clause}
                    ORDER BY score DESC, updated_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            else:
                age_clause = "WHERE updated_at >= ?" if cutoff else ""
                params = (cutoff, max_rows) if cutoff else (max_rows,)
                rows = conn.execute(
                    f"""
                    SELECT symbol, market, stance, horizon, score, confidence,
                        reason_md, block_template_json, source_run_id, updated_at
                    FROM crypto_candidates
                    {age_clause}
                    ORDER BY score DESC, updated_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "market": str(row["market"]),
                "stance": str(row["stance"]),
                "horizon": str(row["horizon"]),
                "score": float(row["score"]),
                "confidence": float(row["confidence"]),
                "reason_md": str(row["reason_md"]),
                "block_template": _json_dict(row["block_template_json"]),
                "source_run_id": int(row["source_run_id"] or 0),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def latest_symbol_notes(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, dict[str, Any]]:
        max_rows = max(int(limit), 1)
        clean_symbols = [_normalize_symbol(symbol) for symbol in symbols or []]
        clean_symbols = [symbol for symbol in clean_symbols if symbol]
        with self._connect() as conn:
            if clean_symbols:
                placeholders = ",".join("?" for _ in clean_symbols)
                rows = conn.execute(
                    f"""
                    SELECT symbol, stance, horizon, confidence, summary_md,
                        reasons_json, risks_json, triggers_json, updated_at
                    FROM crypto_symbol_notes
                    WHERE symbol IN ({placeholders})
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (*clean_symbols, max_rows),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT symbol, stance, horizon, confidence, summary_md,
                        reasons_json, risks_json, triggers_json, updated_at
                    FROM crypto_symbol_notes
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (max_rows,),
                ).fetchall()
        return {
            str(row["symbol"]): {
                "symbol": str(row["symbol"]),
                "stance": str(row["stance"]),
                "horizon": str(row["horizon"]),
                "confidence": float(row["confidence"]),
                "summary_md": str(row["summary_md"]),
                "reasons": _json_list(row["reasons_json"]),
                "risks": _json_list(row["risks_json"]),
                "triggers": _json_list(row["triggers_json"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        }

    @staticmethod
    def _score_features(features: dict[str, Any]) -> float:
        volume_score = min(_to_float(features.get("quote_volume_usdt")) / 100_000.0, 40.0)
        spread_penalty = min(_to_float(features.get("spread_bps")) / 5.0, 20.0)
        trend_score = 20.0 if features.get("trend_1m") == "up" else 10.0
        funding_abs = abs(_to_float(features.get("funding_rate")))
        funding_penalty = min(funding_abs * 100_000.0, 15.0)
        return max(min(volume_score + trend_score - spread_penalty - funding_penalty, 100.0), 0.0)


class CryptoMarketResearchService:
    def __init__(
        self,
        *,
        config: CryptoMarketResearchConfig,
        binance: Any | None = None,
        codex_runtime: Any | None = None,
        memory_provider: Any | None = None,
        quant_repository: CryptoQuantRepository | None = None,
    ) -> None:
        self.config = config
        self.repository = CryptoMarketResearchRepository(config.db_path)
        self.binance = binance
        self.codex_runtime = codex_runtime
        self.memory_provider = memory_provider
        self.quant_repository = quant_repository
        self.quant_engine = CryptoQuantEngine()

    def status(self) -> dict[str, Any]:
        return {
            **self.repository.status(),
            "model": self.config.llm_model,
            "reasoning_effort": self.config.llm_reasoning_effort,
            "max_symbols": self.config.max_symbols,
            "research_universe_limit": self.config.research_universe_limit,
            "llm_top_symbols": self.config.llm_top_symbols,
            "auto_universe_enabled": self.config.auto_universe_enabled,
            "kline_intervals": self._resolved_kline_intervals(),
            "kline_hot_window_rows": self.config.kline_hot_window_rows,
            "market_hot_window_rows": self.config.market_hot_window_rows,
            "regime_enabled": self.config.regime_enabled,
            "squeeze_guard_enabled": self.config.squeeze_guard_enabled,
        }

    def prune_history(
        self,
        *,
        market_retention_days: int = 30,
        quant_retention_days: int = 30,
        market_archive_retention_days: int = 0,
        quant_archive_retention_days: int = 0,
        quant_hot_window_rows: int = 0,
        quant_archive_window_rows: int = 0,
    ) -> dict[str, Any]:
        market = self.repository.prune_history(
            retention_days=market_retention_days,
            archive_retention_days=market_archive_retention_days,
        )
        if isinstance(market, dict):
            market["kline_window"] = self.repository.prune_kline_hot_windows(
                max_rows_per_group=int(self.config.kline_hot_window_rows)
            )
            market["market_window"] = self.repository.prune_market_hot_windows(
                max_rows_per_group=int(self.config.market_hot_window_rows)
            )
        quant = (
            self.quant_repository.prune_history(
                retention_days=quant_retention_days,
                archive_retention_days=quant_archive_retention_days,
                hot_window_rows=quant_hot_window_rows,
                archive_window_rows=quant_archive_window_rows,
            )
            if self.quant_repository is not None
            else {"status": "missing"}
        )
        return {"status": "ok", "market": market, "quant": quant}

    async def resolve_universe(
        self,
        seed_symbols: list[str],
        *,
        auto_enabled: bool | None = None,
        auto_limit: int | None = None,
        max_symbols: int | None = None,
    ) -> dict[str, Any]:
        static_symbols = self._clean_symbol_list(seed_symbols)
        dynamic_symbols: list[str] = []
        excluded_symbols: list[str] = []
        dynamic_rows: list[dict[str, Any]] = []
        should_auto = self.config.auto_universe_enabled if auto_enabled is None else auto_enabled
        dynamic_limit = max(int(auto_limit or self.config.auto_universe_limit), 0)
        resolved_max = max(int(max_symbols or self.config.max_symbols), 1)

        if should_auto and dynamic_limit > 0 and self.binance is not None:
            fetch_24h_tickers = getattr(self.binance, "fetch_24h_tickers", None)
            if fetch_24h_tickers is not None:
                try:
                    payload = fetch_24h_tickers(market="spot")
                    if inspect.isawaitable(payload):
                        payload = await payload
                    rows = payload if isinstance(payload, list) else []
                    candidates: list[dict[str, Any]] = []
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        symbol = _normalize_symbol(row.get("symbol"))
                        if not symbol:
                            continue
                        if not self._is_universe_candidate(row):
                            excluded_symbols.append(symbol)
                            continue
                        candidates.append(
                            {
                                "symbol": symbol,
                                "quote_volume_usdt": _to_float(row.get("quote_volume")),
                                "change_pct_24h": _to_float(row.get("change_pct_24h")),
                            }
                        )
                    candidates.sort(
                        key=lambda item: (
                            _to_float(item.get("quote_volume_usdt")),
                            abs(_to_float(item.get("change_pct_24h"))),
                        ),
                        reverse=True,
                    )
                    dynamic_rows = candidates[:dynamic_limit]
                    dynamic_symbols = [str(row["symbol"]) for row in dynamic_rows]
                except Exception as exc:
                    return {
                        "status": "partial",
                        "symbols": static_symbols[:resolved_max],
                        "static_count": min(len(static_symbols), resolved_max),
                        "dynamic_count": 0,
                        "dynamic_candidates": [],
                        "excluded_symbols": sorted(set(excluded_symbols))[:80],
                        "error_message": str(exc),
                    }

        symbols = self._clean_symbol_list([*static_symbols, *dynamic_symbols])[:resolved_max]
        included_dynamic = [symbol for symbol in symbols if symbol not in static_symbols]
        self.repository.save_observed_universe(
            symbols,
            dynamic_rows=dynamic_rows,
            source="auto_universe" if should_auto and dynamic_rows else "static_universe",
        )
        return {
            "status": "ok",
            "symbols": symbols,
            "static_count": len([symbol for symbol in symbols if symbol in static_symbols]),
            "dynamic_count": len(included_dynamic),
            "dynamic_candidates": [
                row for row in dynamic_rows if str(row.get("symbol") or "") in included_dynamic
            ],
            "excluded_symbols": sorted(set(excluded_symbols))[:80],
            "max_symbols": resolved_max,
            "auto_enabled": bool(should_auto),
        }

    def _is_universe_candidate(self, row: dict[str, Any]) -> bool:
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol.endswith("USDT"):
            return False
        base = _base_key(symbol)
        if base in STABLE_BASE_ASSETS or base in FIAT_BASE_ASSETS:
            return False
        if any(base.endswith(suffix) for suffix in LEVERAGED_TOKEN_SUFFIXES):
            return False
        price = _to_float(row.get("price"))
        change_pct = abs(_to_float(row.get("change_pct_24h")))
        if 0.985 <= price <= 1.015 and change_pct <= 0.25:
            return False
        return _to_float(row.get("quote_volume")) >= float(self.config.min_quote_volume_usdt)

    def _clean_symbol_list(self, symbols: list[str]) -> list[str]:
        out = [
            _normalize_symbol(symbol)
            for symbol in symbols
            if _normalize_symbol(symbol)
        ]
        return list(dict.fromkeys(out))

    async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
        collected: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        max_symbols = max(int(self.config.max_symbols), 1)
        cycle_timeout = max(float(self.config.collect_cycle_timeout_sec), 1.0)
        symbol_timeout = max(float(self.config.collect_symbol_timeout_sec), 1.0)
        concurrency = max(int(self.config.collect_concurrency), 1)
        candidate_symbols = symbols[:max_symbols]
        semaphore = asyncio.Semaphore(concurrency)

        async def collect_one(raw_symbol: str) -> dict[str, Any]:
            symbol = _normalize_symbol(raw_symbol)
            if not symbol:
                return {"status": "skipped", "symbol": ""}
            try:
                async with semaphore:
                    snapshot = await asyncio.wait_for(
                        self._collect_symbol_market(symbol),
                        timeout=symbol_timeout,
                    )
                    features = self._build_features(snapshot)
                    self.repository.upsert_features(symbol, features)
                    self._save_quant_signals_from_snapshot(
                        symbol=symbol,
                        snapshot=snapshot,
                        features=features,
                    )
                    return {
                        "status": "ok",
                        "symbol": symbol,
                        "item": {"symbol": symbol, "features": features},
                    }
            except asyncio.TimeoutError:
                return {
                    "status": "error",
                    "symbol": symbol,
                    "error_message": "collect_symbol_timeout",
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "symbol": symbol,
                    "error_message": str(exc),
                }

        tasks = [asyncio.create_task(collect_one(symbol)) for symbol in candidate_symbols]
        done, pending = await asyncio.wait(tasks, timeout=cycle_timeout)
        for task in pending:
            task.cancel()
        if pending:
            errors.append(
                {
                    "symbol": "*",
                    "error_message": "collect_cycle_timeout",
                    "remaining_count": len(pending),
                }
            )
        for task in done:
            try:
                result = task.result()
            except Exception as exc:
                errors.append({"symbol": "*", "error_message": str(exc)})
                continue
            if result.get("status") == "ok":
                item = result.get("item")
                if isinstance(item, dict):
                    collected.append(item)
            elif result.get("status") == "error":
                errors.append(
                    {
                        "symbol": str(result.get("symbol") or ""),
                        "error_message": str(result.get("error_message") or ""),
                    }
                )
        status = "partial" if errors and collected else "error" if errors else "ok"
        return {
            "status": status,
            "collected_count": len(collected),
            "error_count": len(errors),
            "requested_count": len(candidate_symbols),
            "concurrency": concurrency,
            "items": collected,
            "errors": errors[:10],
        }

    async def _collect_symbol_market(self, symbol: str) -> dict[str, Any]:
        if self.binance is None:
            raise RuntimeError("binance adapter missing")
        ticker = await self.binance.fetch_24h_ticker(symbol, market="spot")
        book = await self.binance.fetch_book_ticker(symbol, market="spot")
        klines_by_interval: dict[str, list[dict[str, Any]]] = {}
        for interval, limit in self._resolved_kline_intervals().items():
            rows = await self.binance.fetch_klines(
                symbol,
                market="spot",
                interval=interval,
                limit=limit,
            )
            klines_by_interval[interval] = rows
        premium = await self._safe_futures_payload(
            "premium_index",
            self.binance.fetch_futures_premium_index,
            symbol,
        )
        open_interest = await self._safe_futures_payload(
            "open_interest",
            self.binance.fetch_futures_open_interest,
            symbol,
        )
        captured_at = utc_now_iso()
        self.repository.upsert_symbol(symbol)
        self.repository.save_market_snapshot(
            symbol=symbol,
            market="spot",
            ticker=ticker,
            book=book,
            captured_at=captured_at,
        )
        for interval, rows in klines_by_interval.items():
            self.repository.save_klines(
                symbol=symbol,
                market="spot",
                interval=interval,
                rows=rows,
            )
        self.repository.save_derivatives(
            symbol=symbol,
            premium=premium,
            open_interest=open_interest,
            captured_at=captured_at,
        )
        return {
            "symbol": symbol,
            "ticker": ticker,
            "book": book,
            "captured_at": captured_at,
            "klines_by_interval": klines_by_interval,
            "klines_1m": klines_by_interval.get("1m", []),
            "premium": premium,
            "open_interest": open_interest,
        }

    def _resolved_kline_intervals(self) -> dict[str, int]:
        return self.config.kline_intervals or {
            "1m": 120,
            "5m": 96,
            "15m": 96,
            "1h": 168,
            "4h": 180,
        }

    async def _safe_futures_payload(
        self,
        kind: str,
        fetcher: Any,
        symbol: str,
    ) -> dict[str, Any]:
        try:
            payload = fetcher(symbol)
            if inspect.isawaitable(payload):
                payload = await payload
            return payload if isinstance(payload, dict) else {"status": "malformed"}
        except Exception as exc:
            return {
                "status": "unavailable",
                "kind": kind,
                "symbol": symbol,
                "error_message": str(exc),
            }

    def _build_features(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        klines = [
            row for row in list(snapshot.get("klines_1m") or []) if isinstance(row, dict)
        ]
        first_close = _to_float((klines[0] if klines else {}).get("close"))
        last_close = _to_float((klines[-1] if klines else {}).get("close"))
        trend = "up" if last_close > first_close else "down" if last_close < first_close else "flat"
        ticker = _dict_or_empty(snapshot.get("ticker"))
        book = _dict_or_empty(snapshot.get("book"))
        premium = _dict_or_empty(snapshot.get("premium"))
        open_interest = _dict_or_empty(snapshot.get("open_interest"))
        bid_price = _to_float(
            book.get("bid_price")
            or book.get("bid")
            or book.get("bidPrice")
            or book.get("best_bid")
            or book.get("bestBid")
        )
        ask_price = _to_float(
            book.get("ask_price")
            or book.get("ask")
            or book.get("askPrice")
            or book.get("best_ask")
            or book.get("bestAsk")
        )
        book_fresh = bid_price > 0 and ask_price > 0 and ask_price > bid_price
        if not book_fresh:
            bid_price = 0.0
            ask_price = 0.0
        spread_bps = _to_float(book.get("spread_bps")) if book_fresh else 0.0
        if spread_bps <= 0 and book_fresh:
            mid_price = (bid_price + ask_price) / 2.0
            spread_bps = (
                max((ask_price - bid_price) / mid_price * 10_000.0, 0.0)
                if mid_price > 0
                else 0.0
            )
        book_source = str(
            book.get("book_source") or book.get("source") or "book_ticker"
        ).strip()
        book_fetched_at = str(
            book.get("book_fetched_at")
            or book.get("fetched_at")
            or book.get("captured_at")
            or snapshot.get("captured_at")
            or utc_now_iso()
        )
        derivatives_status = "available"
        if (
            premium.get("status") in {"unavailable", "malformed"}
            or open_interest.get("status") in {"unavailable", "malformed"}
        ):
            derivatives_status = "partial"
        index_price = _to_float(premium.get("index_price"))
        basis_pct = 0.0
        if index_price > 0:
            basis_pct = (
                (_to_float(premium.get("mark_price")) - index_price) / index_price * 100.0
            )
        klines_by_interval = _dict_or_empty(snapshot.get("klines_by_interval"))
        primary_rows = (
            klines_by_interval.get("1m")
            or klines_by_interval.get("5m")
            or []
        )
        features = {
            "symbol": _normalize_symbol(snapshot.get("symbol")),
            "trend_1m": trend,
            "regime": trend,
            "price": last_close,
            "change_pct_24h": _to_float(ticker.get("change_pct_24h")),
            "quote_volume_usdt": _to_float(ticker.get("quote_volume")),
            "bid_price": bid_price,
            "ask_price": ask_price,
            "spread_bps": spread_bps,
            "book_source": book_source or "book_ticker",
            "book_fetched_at": book_fetched_at,
            "book_fresh": book_fresh,
            "funding_rate": _to_float(premium.get("funding_rate")),
            "mark_index_basis_pct": basis_pct,
            "open_interest": _to_float(open_interest.get("open_interest")),
            "derivatives_status": derivatives_status,
            "volume_expansion_ratio": self._volume_expansion_ratio(primary_rows),
            "wick_risk_score": self._wick_risk_score(primary_rows),
        }
        timeframes = {
            interval: self._timeframe_feature(rows)
            for interval, rows in klines_by_interval.items()
            if isinstance(rows, list)
        }
        if timeframes:
            trends = [str(row.get("trend")) for row in timeframes.values()]
            up_count = trends.count("up")
            down_count = trends.count("down")
            if up_count >= 3:
                alignment = "bullish"
            elif down_count >= 3:
                alignment = "bearish"
            else:
                alignment = "mixed"
            features["timeframes"] = timeframes
            features["timeframe_alignment"] = alignment
        if self.config.squeeze_guard_enabled:
            features.update(self._squeeze_risk_feature(features))
        features.update(self._entry_quality_feature(features))
        features["score"] = CryptoMarketResearchRepository._score_features(features)
        return features

    def _save_quant_signals_from_snapshot(
        self,
        *,
        symbol: str,
        snapshot: dict[str, Any],
        features: dict[str, Any],
    ) -> None:
        if self.quant_repository is None:
            return
        klines_by_interval = _dict_or_empty(snapshot.get("klines_by_interval"))
        if not klines_by_interval:
            return
        clean_klines = {
            str(interval): rows
            for interval, rows in klines_by_interval.items()
            if isinstance(rows, list)
        }
        if not clean_klines:
            return
        for horizon in ("scalp", "intraday", "swing"):
            signal = self.quant_engine.build_signal(
                symbol=symbol,
                horizon=horizon,
                klines_by_interval=clean_klines,
                market_features=features,
            )
            self.quant_repository.save_signal(
                {
                    "symbol": symbol,
                    "horizon": horizon,
                    "long_score": signal["long_score"],
                    "short_score": signal["short_score"],
                    "no_trade_score": signal["no_trade_score"],
                    "expected_r_long": signal["expected_r_long"],
                    "expected_r_short": signal["expected_r_short"],
                    "signal_json": signal,
                    "updated_at": utc_now_iso(),
                }
            )

    def _timeframe_feature(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        closes = [_to_float(row.get("close")) for row in rows if isinstance(row, dict)]
        closes = [value for value in closes if value > 0]
        if len(closes) < 2:
            return {"trend": "unknown", "momentum_pct": 0.0, "bar_count": len(closes)}
        first = closes[0]
        last = closes[-1]
        momentum_pct = (last - first) / first * 100.0 if first > 0 else 0.0
        if momentum_pct > 0.15:
            trend = "up"
        elif momentum_pct < -0.15:
            trend = "down"
        else:
            trend = "flat"
        return {
            "trend": trend,
            "momentum_pct": momentum_pct,
            "bar_count": len(closes),
            "first_close": first,
            "last_close": last,
        }

    @staticmethod
    def _volume_expansion_ratio(rows: Any) -> float:
        if not isinstance(rows, list) or len(rows) < 6:
            return 0.0
        volumes = [
            _to_float(row.get("quote_volume") or row.get("volume"))
            for row in rows
            if isinstance(row, dict)
        ]
        volumes = [value for value in volumes if value > 0]
        if len(volumes) < 6:
            return 0.0
        recent = sum(volumes[-3:]) / 3.0
        baseline_values = volumes[:-3]
        baseline = sum(baseline_values) / len(baseline_values) if baseline_values else 0.0
        if baseline <= 0:
            return 0.0
        return round(recent / baseline, 4)

    @staticmethod
    def _wick_risk_score(rows: Any) -> float:
        if not isinstance(rows, list) or not rows:
            return 0.0
        scores: list[float] = []
        for row in rows[-8:]:
            if not isinstance(row, dict):
                continue
            high = _to_float(row.get("high"))
            low = _to_float(row.get("low"))
            open_price = _to_float(row.get("open"))
            close = _to_float(row.get("close"))
            candle_range = high - low
            if high <= 0 or low <= 0 or candle_range <= 0:
                continue
            upper_wick = high - max(open_price, close)
            lower_wick = min(open_price, close) - low
            scores.append(max(upper_wick, lower_wick, 0.0) / candle_range * 100.0)
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 4)

    def _squeeze_risk_feature(self, features: dict[str, Any]) -> dict[str, Any]:
        funding = _to_float(features.get("funding_rate"))
        basis = _to_float(features.get("mark_index_basis_pct"))
        open_interest = _to_float(features.get("open_interest"))
        trend = str(features.get("trend_1m") or "")
        score = 0.0
        reasons: list[str] = []
        if funding <= -0.0005:
            score += 35
            reasons.append("negative funding indicates crowded shorts")
        if basis <= -0.10:
            score += 25
            reasons.append("mark below index indicates bearish crowding")
        if open_interest >= 1_000_000:
            score += 20
            reasons.append("open interest is elevated")
        if trend == "down":
            score += 10
            reasons.append("short-term trend aligns with crowding")
        if score >= 80:
            label = "high_short_squeeze"
        elif score >= 60:
            label = "short_squeeze"
        elif score <= 20 and funding >= 0.0005 and basis >= 0.10:
            label = "long_squeeze"
        else:
            label = "normal"
        return {
            "squeeze_risk": label,
            "squeeze_risk_score": min(score, 100.0),
            "squeeze_risk_reasons": reasons,
        }

    def _entry_quality_feature(self, features: dict[str, Any]) -> dict[str, Any]:
        timeframes = _dict_or_empty(features.get("timeframes"))
        momentum_values = [
            abs(_to_float(row.get("momentum_pct")))
            for row in timeframes.values()
            if isinstance(row, dict)
        ]
        max_momentum = max(momentum_values or [0.0])
        spread = _to_float(features.get("spread_bps"))
        squeeze_score = _to_float(features.get("squeeze_risk_score"))
        score = 70.0
        reasons: list[str] = []
        if max_momentum >= 10.0:
            score -= 25
            reasons.append("move extended across timeframes")
        if spread >= 8.0:
            score -= 15
            reasons.append("spread is wide")
        if squeeze_score >= 70.0:
            score -= 20
            reasons.append("squeeze risk is elevated")
        if score >= 70:
            label = "actionable_now"
        elif score >= 55:
            label = "conditional"
        else:
            label = "wait_pullback"
        return {
            "entry_quality": label,
            "entry_quality_score": max(min(score, 100.0), 0.0),
            "entry_quality_reasons": reasons,
        }

    def compute_market_regime(
        self,
        *,
        symbols: list[str],
        persist: bool = True,
    ) -> dict[str, Any]:
        rows = self.repository.latest_features(
            symbols=symbols,
            limit=max(len(symbols), 1),
        )
        if not rows:
            return {"status": "missing", "regime": "unknown"}
        feature_by_symbol = {str(row["symbol"]): row["features"] for row in rows}
        btc = feature_by_symbol.get("BTCUSDT", {})
        bearish = 0
        bullish = 0
        for feature in feature_by_symbol.values():
            alignment = str(feature.get("timeframe_alignment") or "")
            change = _to_float(feature.get("change_pct_24h"))
            if alignment == "bearish" or change <= -4.0:
                bearish += 1
            if alignment == "bullish" or change >= 4.0:
                bullish += 1
        total = max(len(feature_by_symbol), 1)
        bearish_pct = bearish / total * 100.0
        bullish_pct = bullish / total * 100.0
        btc_change = _to_float(btc.get("change_pct_24h"))
        if btc_change <= -3.0 and bearish_pct >= 60.0:
            regime = "risk_off_downtrend"
        elif btc_change >= 3.0 and bullish_pct >= 50.0:
            regime = "btc_led_risk_on"
        elif bearish_pct >= 45.0 and bullish_pct >= 25.0:
            regime = "high_dispersion_chop"
        else:
            regime = "mixed_rotation"
        payload = {
            "status": "ok",
            "regime": regime,
            "btc_change_pct_24h": btc_change,
            "bearish_breadth_pct": bearish_pct,
            "bullish_breadth_pct": bullish_pct,
            "symbol_count": total,
        }
        if persist:
            self.repository.save_regime_snapshot(payload)
        return payload

    def _regime_brief_major_rows(self, items: list[Any]) -> list[dict[str, Any]]:
        majors = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
        by_symbol: dict[str, dict[str, Any]] = {}
        for row in items:
            if not isinstance(row, dict):
                continue
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            symbol = _normalize_symbol(row.get("symbol") or features.get("symbol"))
            if symbol not in majors or symbol in by_symbol:
                continue
            by_symbol[symbol] = {
                "symbol": symbol,
                "change_pct_24h": round(_to_float(features.get("change_pct_24h")), 4),
                "trend_1m": str(features.get("trend_1m") or ""),
                "timeframe_alignment": str(features.get("timeframe_alignment") or ""),
                "spread_bps": round(_to_float(features.get("spread_bps")), 4),
                "entry_quality": str(features.get("entry_quality") or ""),
            }
        return [by_symbol[symbol] for symbol in majors if symbol in by_symbol]

    def _regime_brief_external_notes(
        self,
        external_context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        for row in _bounded_list(external_context, limit=6):
            if not isinstance(row, dict):
                continue
            payload = _dict_or_empty(row.get("payload"))
            source_id = str(row.get("source_id") or "").strip()
            key = str(row.get("key") or "").strip()
            headline = ""
            if source_id == "fear_greed":
                headline = "fear_greed={value} {label}".format(
                    value=payload.get("value", ""),
                    label=payload.get("value_classification", ""),
                ).strip()
            elif source_id == "coingecko":
                headline = "rank={rank} developer={developer} community={community}".format(
                    rank=payload.get("market_cap_rank", ""),
                    developer=payload.get("developer_score", ""),
                    community=payload.get("community_score", ""),
                ).strip()
            elif source_id == "defillama":
                headline = "tvl={tvl} change_24h={change}".format(
                    tvl=payload.get("tvl") or payload.get("tvl_usd") or "",
                    change=payload.get("change_1d") or payload.get("change_24h") or "",
                ).strip()
            else:
                headline = ", ".join(
                    f"{field}={_prompt_scalar(value)}"
                    for field, value in list(payload.items())[:3]
                )
            notes.append(
                {
                    "source_id": source_id,
                    "key": key,
                    "captured_at": row.get("captured_at"),
                    "headline": _prompt_text(headline, limit=180),
                }
            )
        return [note for note in notes if note.get("source_id")]

    def _regime_brief_derivatives_notes(self, items: list[Any]) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            symbol = _normalize_symbol(row.get("symbol") or features.get("symbol"))
            if not symbol:
                continue
            squeeze_score = _to_float(features.get("squeeze_risk_score"))
            funding = _to_float(features.get("funding_rate"))
            basis = _to_float(features.get("mark_index_basis_pct"))
            if abs(funding) < 0.0004 and abs(basis) < 0.08 and squeeze_score < 60.0:
                continue
            notes.append(
                {
                    "symbol": symbol,
                    "funding_rate": round(funding, 8),
                    "basis_pct": round(basis, 4),
                    "squeeze_risk": str(features.get("squeeze_risk") or ""),
                    "squeeze_risk_score": round(squeeze_score, 2),
                }
            )
        notes.sort(
            key=lambda row: (
                abs(_to_float(row.get("funding_rate"))),
                _to_float(row.get("squeeze_risk_score")),
            ),
            reverse=True,
        )
        return notes[:6]

    def _build_regime_brief(
        self,
        *,
        market_regime: dict[str, Any],
        items: list[Any],
        candidates: list[dict[str, Any]],
        external_context: list[dict[str, Any]],
        quant_context: dict[str, Any],
    ) -> dict[str, Any]:
        regime = str(market_regime.get("regime") or "unknown")
        bearish_pct = _to_float(market_regime.get("bearish_breadth_pct"))
        bullish_pct = _to_float(market_regime.get("bullish_breadth_pct"))
        neutral_pct = max(0.0, 100.0 - bearish_pct - bullish_pct)
        btc_change = _to_float(market_regime.get("btc_change_pct_24h"))
        candidate_longs = sum(
            1 for row in candidates if "long" in str(row.get("stance") or "").lower()
        )
        candidate_shorts = sum(
            1 for row in candidates if "short" in str(row.get("stance") or "").lower()
        )
        if regime == "risk_off_downtrend" or (btc_change <= -3.0 and bearish_pct >= 50.0):
            market_direction = "risk_off_downtrend"
            risk_posture = "defensive"
            lane_bias = {
                "spot_long": "wait_for_discount_or_accumulation_only",
                "futures_long": "countertrend_only_with_strict_trigger",
                "futures_short": "favored_when_not_crowded",
            }
            horizon_bias = {
                "short": "short_or_wait",
                "mid": "selective_accumulation_after_flush",
                "long": "research_watch_before_size",
            }
        elif regime == "btc_led_risk_on" or (btc_change >= 3.0 and bullish_pct >= 45.0):
            market_direction = "btc_led_risk_on"
            risk_posture = "constructive"
            lane_bias = {
                "spot_long": "favored_on_pullback_or_breakout",
                "futures_long": "allowed_with_liquidity_and_spread_confirmation",
                "futures_short": "hedge_or_exhaustion_only",
            }
            horizon_bias = {
                "short": "long_momentum_with_spread_control",
                "mid": "spot_or_swing_candidates_allowed",
                "long": "core_candidates_allowed_if_external_context_agrees",
            }
        elif regime == "high_dispersion_chop" or (bearish_pct >= 35.0 and bullish_pct >= 20.0):
            market_direction = "dispersion_chop"
            risk_posture = "selective"
            lane_bias = {
                "spot_long": "only_strong_relative_strength_or_wait_pullback",
                "futures_long": "tactical_only",
                "futures_short": "tactical_only",
            }
            horizon_bias = {
                "short": "pair_specific_tactical",
                "mid": "wait_for_rotation_confirmation",
                "long": "small_accumulation_only_on_clear_discount",
            }
        else:
            market_direction = "mixed_rotation"
            risk_posture = "balanced_selective"
            lane_bias = {
                "spot_long": "selective",
                "futures_long": "selective",
                "futures_short": "selective",
            }
            horizon_bias = {
                "short": "use_live_book_and_quant_confirmation",
                "mid": "prefer_symbols_with_improving_structure",
                "long": "require_external_or_memory_support",
            }

        external_notes = self._regime_brief_external_notes(external_context)
        derivatives_notes = self._regime_brief_derivatives_notes(items)
        major_rows = self._regime_brief_major_rows(items)
        rotation_notes: list[str] = []
        if candidate_longs or candidate_shorts:
            rotation_notes.append(
                f"candidate_lane_mix long={candidate_longs} short={candidate_shorts}"
            )
        if bearish_pct >= 55.0:
            rotation_notes.append("breadth_pressure_is_broad")
        elif bullish_pct >= 45.0:
            rotation_notes.append("breadth_supports_long_rotation")
        elif bullish_pct >= 20.0 and bearish_pct >= 35.0:
            rotation_notes.append("dispersion_requires_symbol_selection")
        if external_notes:
            rotation_notes.append("external_context_available")

        data_gaps: list[str] = []
        if not major_rows:
            data_gaps.append("major_coin_context_missing")
        if not external_notes:
            data_gaps.append("external_context_missing")
        if not derivatives_notes:
            data_gaps.append("derivatives_context_thin")
        if not _bounded_list(_dict_or_empty(quant_context).get("items"), limit=1):
            data_gaps.append("quant_context_missing")

        summary_ko = (
            f"현재 크립토 레짐은 {regime}이며 방향성은 {market_direction}, "
            f"운용 자세는 {risk_posture}입니다. BTC 24h {btc_change:.2f}%, "
            f"상승 breadth {bullish_pct:.1f}%, 하락 breadth {bearish_pct:.1f}%라서 "
            f"쥬는 레인별로 현물 롱·선물 롱·선물 숏을 분리해서 검토해야 합니다."
        )
        return {
            "version": "crypto_regime_brief_v1",
            "status": "partial" if data_gaps else "ok",
            "regime": regime,
            "market_direction": market_direction,
            "risk_posture": risk_posture,
            "breadth": {
                "bearish_pct": round(bearish_pct, 2),
                "bullish_pct": round(bullish_pct, 2),
                "neutral_pct": round(neutral_pct, 2),
                "symbol_count": int(_to_float(market_regime.get("symbol_count"))),
            },
            "btc_change_pct_24h": round(btc_change, 4),
            "major_rows": major_rows,
            "rotation_notes": rotation_notes[:6],
            "derivatives_notes": derivatives_notes,
            "external_notes": external_notes,
            "lane_bias": lane_bias,
            "horizon_bias": horizon_bias,
            "operator_summary_ko": summary_ko,
            "data_gaps": data_gaps,
        }

    def latest_context(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        max_rows = max(int(limit), 1)
        clean_symbols = [_normalize_symbol(symbol) for symbol in symbols or []]
        clean_symbols = [symbol for symbol in clean_symbols if symbol]
        items = self.repository.latest_features(symbols=clean_symbols, limit=max_rows)
        context_symbols = clean_symbols or [str(row.get("symbol") or "") for row in items]
        observed_symbol_count = (
            len(clean_symbols)
            if clean_symbols
            else max(
                self.repository.observed_symbol_count(),
                self.repository.feature_symbol_count(),
            )
        )
        external_keys = [_base_key(symbol) for symbol in context_symbols if symbol]
        market_regime = (
            self.compute_market_regime(symbols=context_symbols, persist=False)
            if context_symbols and self.config.regime_enabled
            else {"status": "missing", "regime": "unknown"}
        )
        candidates = self.repository.latest_candidates(
            symbols=context_symbols,
            limit=max_rows,
        )
        quant_context = (
            self.quant_repository.retrieval_context(
                symbols=context_symbols,
                horizon="intraday",
                points_per_symbol=12,
            )
            if self.quant_repository is not None
            else {"status": "missing", "items": []}
        )
        external_context = self.repository.list_external_context(
            keys=external_keys,
            limit=max_rows,
        )
        regime_brief = self._build_regime_brief(
            market_regime=market_regime,
            items=items,
            candidates=candidates,
            external_context=external_context,
            quant_context=quant_context,
        )
        candidate_packets = self._build_candidate_packets(
            items=items,
            candidates=candidates,
        )
        evidence = [
            evidence_from_signal(
                source="crypto_market_research",
                signal_type="research_candidate",
                symbol=candidate.get("symbol"),
                scope="binance",
                confidence=_to_float(candidate.get("confidence")),
                ttl_sec=3600,
                captured_at=str(candidate.get("updated_at") or utc_now_iso()),
                payload={
                    "stance": candidate.get("stance"),
                    "horizon": candidate.get("horizon"),
                    "score": candidate.get("score"),
                    "reason_md": candidate.get("reason_md"),
                    "market": candidate.get("market"),
                },
            ).to_dict()
            for candidate in candidates
        ]
        return {
            "status": "ok",
            "items": items,
            "quant": quant_context,
            "market_regime": market_regime,
            "regime_brief": regime_brief,
            "observed_symbol_count": observed_symbol_count,
            "observe_universe_count": observed_symbol_count,
            "focus_symbol_count": min(len(context_symbols), max_rows),
            "research_universe_count": max(len(items), len(candidates)),
            "candidate_packets": candidate_packets,
            "candidates": candidates,
            "evidence": evidence,
            "symbol_notes": self.repository.latest_symbol_notes(
                symbols=context_symbols,
                limit=max_rows,
            ),
            "external_context": external_context,
        }

    def _build_candidate_packets(
        self,
        *,
        items: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        feature_rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper().strip()
            features = item.get("features") if isinstance(item.get("features"), dict) else {}
            if symbol:
                feature_rows.append({"symbol": symbol, **features})
        return {
            "top_movers": [
                self._packet_row(row)
                for row in sorted(
                    feature_rows,
                    key=lambda value: abs(_to_float(value.get("change_pct_24h"))),
                    reverse=True,
                )[:12]
            ],
            "volatile_candidates": [
                self._packet_row(row)
                for row in sorted(
                    [
                        row
                        for row in feature_rows
                        if abs(_to_float(row.get("change_pct_24h"))) >= 8.0
                        or _to_float(row.get("volume_expansion_ratio")) >= 1.8
                        or _to_float(row.get("squeeze_risk_score")) >= 65.0
                    ],
                    key=lambda value: (
                        abs(_to_float(value.get("change_pct_24h")))
                        + _to_float(value.get("volume_expansion_ratio")) * 4.0
                        + _to_float(value.get("squeeze_risk_score")) * 0.15
                    ),
                    reverse=True,
                )[:12]
            ],
            "regime_leaders": [
                self._packet_row(row)
                for row in sorted(
                    candidates,
                    key=lambda value: _to_float(value.get("score")),
                    reverse=True,
                )[:12]
            ],
            "failed_breakout": [
                self._packet_row(row)
                for row in feature_rows
                if "failed" in str(row.get("entry_quality") or row.get("timeframe_alignment") or "").lower()
            ][:12],
            "squeeze_setup": [
                self._packet_row(row)
                for row in sorted(
                    [
                        row
                        for row in feature_rows
                        if _to_float(row.get("squeeze_risk_score") or row.get("squeeze_score")) >= 65.0
                    ],
                    key=lambda value: _to_float(
                        value.get("squeeze_risk_score") or value.get("squeeze_score")
                    ),
                    reverse=True,
                )[:12]
            ],
        }

    @staticmethod
    def _packet_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": str(row.get("symbol") or "").upper().strip(),
            "market": str(row.get("market") or "spot"),
            "stance": str(row.get("stance") or ""),
            "score": _to_float(row.get("score")),
            "change_pct_24h": _to_float(row.get("change_pct_24h")),
            "quote_volume_usdt": _to_float(row.get("quote_volume_usdt")),
            "volume_expansion_ratio": _to_float(row.get("volume_expansion_ratio")),
            "spread_bps": _to_float(row.get("spread_bps")),
            "squeeze_risk_score": _to_float(row.get("squeeze_risk_score") or row.get("squeeze_score")),
            "alpha_score_v3": score_crypto_candidate(row),
        }

    def save_external_context(
        self,
        *,
        source_id: str,
        key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        clean_source_id = str(source_id or "").strip()
        clean_key = str(key or "").upper().strip()
        if not clean_source_id or not clean_key:
            return {"status": "error", "error_message": "source_id_and_key_required"}
        clean_payload = self._compact_external_payload(payload)
        self.repository.upsert_external_context(
            source_id=clean_source_id,
            key=clean_key,
            payload=clean_payload,
            captured_at=utc_now_iso(),
        )
        return {"status": "ok", "source_id": clean_source_id, "key": clean_key}

    def external_context(
        self,
        *,
        keys: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "items": self.repository.list_external_context(keys=keys or [], limit=limit),
        }

    async def collect_external_context(
        self,
        *,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.config.external_enabled:
            return {"status": "skipped", "reason": "external_context_disabled"}

        clean_symbols = [_normalize_symbol(symbol) for symbol in symbols or []]
        bases = sorted({_base_key(symbol) for symbol in clean_symbols if symbol})
        source_ids = {
            item.strip().lower()
            for item in str(self.config.external_sources or "").split(",")
            if item.strip()
        }
        if not bases or not source_ids:
            return {"status": "skipped", "reason": "no_external_targets"}

        saved = 0
        errors: list[dict[str, str]] = []
        async with httpx.AsyncClient(timeout=8.0) as client:
            if "coingecko" in source_ids:
                result = await self._collect_coingecko_context(client, bases)
                saved += int(result.get("saved_count") or 0)
                errors.extend(result.get("errors") or [])
            if "defillama" in source_ids:
                result = await self._collect_defillama_context(client, bases)
                saved += int(result.get("saved_count") or 0)
                errors.extend(result.get("errors") or [])
            if "fear_greed" in source_ids:
                result = await self._collect_fear_greed_context(client)
                saved += int(result.get("saved_count") or 0)
                errors.extend(result.get("errors") or [])

        status = "partial" if errors and saved else "error" if errors else "ok"
        return {
            "status": status,
            "saved_count": saved,
            "error_count": len(errors),
            "errors": errors[:10],
        }

    async def _collect_coingecko_context(
        self,
        client: httpx.AsyncClient,
        bases: list[str],
    ) -> dict[str, Any]:
        id_to_base = {
            COINGECKO_IDS[base]: base
            for base in bases
            if base in COINGECKO_IDS
        }
        if not id_to_base:
            return {"saved_count": 0, "errors": []}
        try:
            response = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": ",".join(sorted(id_to_base)),
                    "vs_currencies": "usd",
                    "include_market_cap": "true",
                    "include_24hr_vol": "true",
                    "include_24hr_change": "true",
                    "include_last_updated_at": "true",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {
                "saved_count": 0,
                "errors": [{"source_id": "coingecko", "error_message": str(exc)}],
            }

        saved = 0
        coin_rows = payload.items() if isinstance(payload, dict) else []
        for coin_id, row in coin_rows:
            base = id_to_base.get(str(coin_id))
            if not base or not isinstance(row, dict):
                continue
            self.save_external_context(source_id="coingecko", key=base, payload=row)
            saved += 1
        return {"saved_count": saved, "errors": []}

    async def _collect_defillama_context(
        self,
        client: httpx.AsyncClient,
        bases: list[str],
    ) -> dict[str, Any]:
        wanted = {
            DEFILLAMA_CHAIN_NAMES[base].lower(): base
            for base in bases
            if base in DEFILLAMA_CHAIN_NAMES
        }
        if not wanted:
            return {"saved_count": 0, "errors": []}
        try:
            response = await client.get("https://api.llama.fi/v2/chains")
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {
                "saved_count": 0,
                "errors": [{"source_id": "defillama", "error_message": str(exc)}],
            }

        saved = 0
        chain_rows = payload if isinstance(payload, list) else []
        for row in chain_rows:
            if not isinstance(row, dict):
                continue
            base = wanted.get(str(row.get("name") or "").lower())
            if not base:
                continue
            self.save_external_context(source_id="defillama", key=base, payload=row)
            saved += 1
        return {"saved_count": saved, "errors": []}

    async def _collect_fear_greed_context(
        self,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        try:
            response = await client.get("https://api.alternative.me/fng/", params={"limit": 1})
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {
                "saved_count": 0,
                "errors": [{"source_id": "fear_greed", "error_message": str(exc)}],
            }
        self.save_external_context(source_id="fear_greed", key="MARKET", payload=payload)
        return {"saved_count": 1, "errors": []}

    async def run_research_once(
        self,
        *,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        packet = self._research_packet(symbols=symbols or [])
        prompt = self._build_research_prompt(packet)
        model = str(getattr(self.codex_runtime, "resolved_model", self.config.llm_model))
        try:
            output = await self._complete_research_json(prompt)
        except Exception as exc:
            run_id = self.repository.save_research_run(
                status="error",
                mode="llm",
                model=model,
                prompt=prompt,
                response={"symbol_notes": [], "candidates": []},
                error_message=str(exc),
            )
            return {
                "status": "error",
                "run_id": run_id,
                "note_count": 0,
                "candidate_count": 0,
                "error_message": str(exc),
            }

        run_id = self.repository.save_research_run(
            status="ok",
            mode="llm",
            model=model,
            prompt=prompt,
            response=output,
            error_message="",
        )
        note_count = self._save_symbol_notes(output.get("symbol_notes") or [])
        candidate_count = self._save_candidates(
            output.get("candidates") or [],
            source_run_id=run_id,
        )
        return {
            "status": "ok",
            "run_id": run_id,
            "note_count": note_count,
            "candidate_count": candidate_count,
        }

    def _research_packet(self, *, symbols: list[str]) -> dict[str, Any]:
        observed_symbols = self._clean_symbol_list(symbols)
        focus_symbols = self.select_llm_focus_symbols(symbols=observed_symbols)
        context = self.latest_context(
            symbols=focus_symbols,
            limit=max(len(focus_symbols), 1),
        )
        if observed_symbols and self.config.regime_enabled:
            market_regime = self.compute_market_regime(symbols=observed_symbols)
        else:
            market_regime = {"status": "missing", "regime": "unknown"}
        return {
            "generated_at": utc_now_iso(),
            "symbols": focus_symbols,
            "observed_symbol_count": len(observed_symbols),
            "focus_symbol_count": len(focus_symbols),
            "market_regime": market_regime,
            "market_context": context,
            "memory_context": self._memory_context(symbols=focus_symbols),
        }

    def select_llm_focus_symbols(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int | None = None,
    ) -> list[str]:
        clean_symbols = self._clean_symbol_list(symbols or [])
        top_n = max(int(limit or self.config.llm_top_symbols), 1)
        feature_rows = self.repository.latest_features(
            symbols=clean_symbols,
            limit=max(len(clean_symbols), top_n),
        )
        ranked_symbols = [
            str(row.get("symbol") or "")
            for row in feature_rows
            if str(row.get("symbol") or "")
        ]
        fallback = clean_symbols or ranked_symbols
        return self._clean_symbol_list([*ranked_symbols, *fallback])[:top_n]

    def _build_research_prompt(self, packet: dict[str, Any]) -> dict[str, Any]:
        try:
            jue_workflow = JueSkillRegistry().compile_prompt_pack("crypto_research")
        except JueSkillValidationError as exc:
            jue_workflow = {
                "workflow_id": "crypto_research",
                "status": "error",
                "error_message": str(exc),
            }
        if isinstance(jue_workflow, dict) and "contracts" in jue_workflow:
            jue_workflow = dict(jue_workflow)
            jue_workflow["reference_contracts"] = jue_workflow.pop("contracts")
        language_policy = jue_language_policy()
        return {
            "system": (
                "You are Jue, the HERMES Binance research partner. Analyze crypto "
                "market structure and derivatives signals in English, draft the "
                "operator-facing conclusion in English, then translate display "
                "fields into natural Korean."
            ),
            "operation": "crypto_market_research",
            "native_thread_mode": "ephemeral",
            "telemetry": {
                "component": "crypto_market_research",
                "operation": "run_research_once",
            },
            "task": "Return JSON only. Build crypto symbol notes and candidates.",
            "language_policy": language_policy,
            "candidate_policy": [
                (
                    "Do not treat direction as entry. entry_quality must decide "
                    "actionable_now, conditional, or wait_pullback."
                ),
                (
                    "If entry_quality is wait_pullback, produce "
                    "block_template.entry_style='wait_pullback'."
                ),
                (
                    "Do not collapse every actionable idea into futures. For directional "
                    "long exposure, include a spot candidate unless the thesis explicitly "
                    "requires futures leverage. Use futures primarily for short, hedge, "
                    "basis, or liquidation-aware tactical blocks."
                ),
                (
                    "Use inputs.market_context.regime_brief as the market narrative layer. "
                    "Before selecting symbols, explain whether the regime favors spot "
                    "accumulation, futures long, futures short, or waiting. Candidate "
                    "direction must agree with regime_brief.lane_bias or state the "
                    "contradiction explicitly in reason_md."
                ),
            ],
            "model_role": "crypto_market_research_analyst",
            "scope": "binance",
            "jue_workflow": jue_workflow,
            "inputs": _compact_research_packet(packet),
            "output_schema": {
                "symbol_notes": [
                    {
                        "symbol": "BTCUSDT",
                        "stance": "long_watch|short_watch|hold|avoid",
                        "horizon": "scalp|intraday|swing|core",
                        "confidence": 0.0,
                        "summary_md": "string",
                        "reasons": ["string"],
                        "risks": ["string"],
                        "triggers": ["string"],
                    }
                ],
                "candidates": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot|futures",
                        "stance": "long_watch|short_watch|hold|avoid",
                        "horizon": "scalp|intraday|swing|core",
                        "score": 0,
                        "confidence": 0.0,
                        "reason_md": "string",
                        "block_template": {
                            "entry_style": "actionable_now|wait_pullback|conditional",
                            "entry_price": 0.0,
                            "target_price": 0.0,
                            "stop_price": 0.0,
                            "reward_risk": 0.0,
                            "market": "spot|futures",
                            "side": "long|short",
                            "horizon": "scalp|intraday|swing|core",
                            "invalidation": "string",
                            "evidence_refs": ["string"],
                        },
                    }
                ],
            },
        }

    async def _complete_research_json(self, prompt: dict[str, Any]) -> dict[str, Any]:
        if self.codex_runtime is None:
            raise RuntimeError("crypto research codex native runtime missing")

        complete_json = getattr(self.codex_runtime, "complete_json", None)
        if complete_json is not None:
            payload = complete_json(
                prompt,
                model=self.config.llm_model,
                reasoning_effort=self.config.llm_reasoning_effort,
            )
            if inspect.isawaitable(payload):
                payload = await payload
            return self._validate_research_payload(payload)

        complete = getattr(self.codex_runtime, "complete", None)
        if complete is None:
            raise RuntimeError("crypto research codex native runtime has no completion method")
        payload = complete(
            {
                "operation": "crypto_market_research",
                "model": self.config.llm_model,
                "native_thread_mode": "ephemeral",
                "messages": [
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": json_dumps(prompt)},
                ],
                "telemetry": {
                    "component": "crypto_market_research",
                    "operation": "run_research_once",
                },
            }
        )
        if inspect.isawaitable(payload):
            payload = await payload
        if not isinstance(payload, dict):
            raise RuntimeError("crypto research llm response malformed")
        if payload.get("ok") is False:
            error_message = str(payload.get("error") or "crypto research llm failed")
            raise RuntimeError(error_message)
        content = payload.get("content")
        if isinstance(content, str):
            parsed = json_loads(content, {})
            if isinstance(parsed, dict):
                return self._validate_research_payload(parsed)
        return self._validate_research_payload(payload)

    def _validate_research_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RuntimeError("crypto research llm response malformed")
        if payload.get("ok") is False:
            error_message = str(payload.get("error") or "crypto research llm failed")
            raise RuntimeError(error_message)
        has_notes = "symbol_notes" in payload
        has_candidates = "candidates" in payload
        if not has_notes and not has_candidates:
            raise RuntimeError("crypto research llm response missing outputs")
        symbol_notes = payload.get("symbol_notes") if has_notes else []
        candidates = payload.get("candidates") if has_candidates else []
        if not isinstance(symbol_notes, list) or not isinstance(candidates, list):
            raise RuntimeError("crypto research llm response outputs malformed")
        return {**payload, "symbol_notes": symbol_notes, "candidates": candidates}

    def _save_symbol_notes(self, rows: Any) -> int:
        count = 0
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            self.repository.upsert_symbol_note(row)
            count += 1
        return count

    def _save_candidates(self, rows: Any, *, source_run_id: int) -> int:
        count = 0
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            self.repository.upsert_candidate(row, source_run_id=source_run_id)
            count += 1
        return count

    def _memory_context(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.memory_provider is None:
            return {"status": "missing"}
        provider = self.memory_provider
        try:
            latest_context = getattr(provider, "latest_context", None)
            if latest_context is not None:
                result = latest_context(symbols=symbols, limit=12)
                if isinstance(result, dict):
                    return result
            build_context = getattr(provider, "build_context", None)
            if build_context is not None:
                result = build_context(symbols=symbols)
                if isinstance(result, dict):
                    return result
            if callable(provider):
                result = provider(
                    symbols=symbols,
                    target_scope="binance",
                    source_scope="binance",
                )
                if inspect.isawaitable(result):
                    return {
                        "status": "deferred",
                        "reason": "async_memory_provider_not_supported_here",
                    }
                if isinstance(result, dict):
                    return result
        except TypeError:
            try:
                if callable(provider):
                    result = provider(symbols=symbols)
                else:
                    result = provider.latest_context(limit=12)
                if isinstance(result, dict):
                    return result
            except Exception as exc:
                return {"status": "error", "error_message": str(exc)}
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}
        return {"status": "missing"}

    @staticmethod
    def _compact_external_payload(payload: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in payload.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            if isinstance(value, str):
                clean[clean_key] = value[:1200]
            elif isinstance(value, (int, float, bool)) or value is None:
                clean[clean_key] = value
            elif isinstance(value, list):
                clean[clean_key] = value[:20]
            elif isinstance(value, dict):
                clean[clean_key] = {str(k): v for k, v in list(value.items())[:20]}
        return clean


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded_list(value: Any, *, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[: max(int(limit), 1)]
