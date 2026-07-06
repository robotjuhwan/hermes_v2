from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


DISCOVERY_MARKETS = ("KOSPI", "KOSDAQ")
DISCOVERY_TRIGGER = "daily_random_deep_research"
COMPLETED_DISCOVERY_STATUSES = {"ok", "partial", "empty", "completed", "success"}
PRE_SURGE_SETUP_TOKENS = (
    "저평가",
    "저per",
    "저pbr",
    "per",
    "pbr",
    "roe",
    "배당",
    "52주 저점",
    "저점권",
    "눌림",
    "지지",
    "박스권",
    "바닥",
    "반등 초입",
    "거래대금",
    "수급",
    "순환매",
    "섹터",
    "리포트",
    "실적",
    "턴어라운드",
    "재평가",
    "모멘텀 초입",
)
PRE_SURGE_REJECT_TOKENS = (
    "관리종목",
    "거래정지",
    "상장폐지",
    "유효 시세 없음",
    "가격 없음",
    "감사의견",
)


class SymbolDirectorySource(Protocol):
    def list_symbol_directory(
        self,
        *,
        market: str = "",
        limit: int = 100,
        exclude_symbols: set[str] | None = None,
    ) -> list[dict[str, Any]]: ...


class SymbolAnalysisRunner(Protocol):
    async def run(
        self,
        symbol_or_name: str,
        *,
        trigger: str = "user_request",
        force_collect: bool = True,
    ) -> dict[str, Any]: ...


@dataclass
class DailyDiscoveryConfig:
    db_path: str = ".runtime/jue_daily_discovery.db"
    enabled: bool = True
    kospi_count: int = 5
    kosdaq_count: int = 5
    exclude_recent_days: int = 10
    candidate_limit_per_market: int = 300
    force_collect: bool = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default
    return parsed


def _stable_sample(
    rows: list[dict[str, Any]],
    *,
    seed: str,
    count: int,
) -> list[dict[str, Any]]:
    decorated: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        digest = hashlib.sha256(f"{seed}:{symbol}".encode("utf-8")).hexdigest()
        decorated.append((int(digest[:16], 16), row))
    decorated.sort(key=lambda item: item[0])
    return [dict(row) for _, row in decorated[: max(int(count), 0)]]


def _analysis_payload(result: dict[str, Any]) -> dict[str, Any]:
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    return analysis if isinstance(analysis, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _score_analysis(analysis: dict[str, Any]) -> float:
    stance = str(analysis.get("stance") or "").strip()
    confidence = _safe_float(analysis.get("confidence"))
    base = {
        "block_candidate": 80,
        "confirm": 72,
        "hold": 62,
        "watch": 52,
        "risk_check": 42,
        "avoid": 18,
        "stale": 8,
    }.get(stance, 40)
    reasons = len(list(analysis.get("reasons") or []))
    risks = len(list(analysis.get("risks") or []))
    return round(base + confidence * 15 + min(reasons, 4) * 1.5 - min(risks, 5), 2)


def _compact_list(value: Any, limit: int = 4) -> list[Any]:
    return list(value or [])[: max(int(limit), 0)] if isinstance(value, list) else []


def _analysis_text(analysis: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("summary", "short_view", "mid_view", "long_view"):
        text = str(analysis.get(key) or "").strip()
        if text:
            parts.append(text)
    for key in ("reasons", "risks", "triggers", "checks"):
        for item in analysis.get(key) or []:
            text = str(item or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts).lower()


def _pre_surge_signal(row: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "").strip().lower()
    stance = str(analysis.get("stance") or "").strip().lower()
    confidence = _safe_float(analysis.get("confidence") or row.get("confidence"))
    confidence_unit = confidence / 100.0 if confidence > 1 else confidence
    score = _safe_float(row.get("score"))
    if score <= 0 and analysis:
        score = _score_analysis(analysis)
    text = _analysis_text(analysis)
    matched = [token for token in PRE_SURGE_SETUP_TOKENS if token in text]
    rejected = [token for token in PRE_SURGE_REJECT_TOKENS if token in text]
    if status in {"error", "failed"} or stance in {"avoid", "stale", "risk_check"} or rejected:
        return {
            "is_candidate": False,
            "lane": "pre_surge",
            "score": round(max(score, 0.0), 2),
            "entry_bias": "none",
            "preferred_horizon": "",
            "reasons": [],
            "risks": rejected[:3],
        }

    strong_candidate = (
        stance in {"block_candidate", "confirm"}
        and confidence_unit >= 0.45
        and score >= 65
    )
    watch_setup = (
        stance in {"watch", "hold"}
        and confidence_unit >= 0.4
        and score >= 58
        and len(matched) >= 2
    )
    is_candidate = strong_candidate or watch_setup
    signal_score = min(
        100.0,
        max(0.0, score * 0.65 + confidence_unit * 25 + min(len(matched), 5) * 3),
    )
    return {
        "is_candidate": bool(is_candidate),
        "lane": "pre_surge",
        "score": round(signal_score, 2),
        "entry_bias": "scout_or_waiting_block" if is_candidate else "watch_only",
        "preferred_horizon": "mid" if is_candidate else "",
        "reasons": matched[:5],
        "risks": _compact_list(analysis.get("risks"), 3),
    }


def enrich_discovery_result(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    if not isinstance(payload.get("pre_surge"), dict):
        payload["pre_surge"] = _pre_surge_signal(payload, analysis)
    return payload


def _compact_discovery_result(row: dict[str, Any]) -> dict[str, Any]:
    row = enrich_discovery_result(row)
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or analysis.get("name") or ""),
        "market": str(row.get("market") or ""),
        "status": str(row.get("status") or ""),
        "score": row.get("score"),
        "error_message": str(row.get("error_message") or "")[:240],
        "analysis": {
            "id": analysis.get("id"),
            "stance": analysis.get("stance"),
            "confidence": analysis.get("confidence"),
            "summary": str(analysis.get("summary") or "")[:500],
            "reasons": _compact_list(analysis.get("reasons")),
            "risks": _compact_list(analysis.get("risks")),
        },
        "pre_surge": row.get("pre_surge"),
    }


def _trading_day_key(value: str | date) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


class DailyDiscoveryRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    selected_symbols_json TEXT NOT NULL DEFAULT '[]',
                    results_json TEXT NOT NULL DEFAULT '[]',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discovery_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    market TEXT NOT NULL DEFAULT '',
                    rank INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'selected',
                    analysis_id INTEGER,
                    stance TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    score REAL,
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(trading_day, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_samples_symbol
                    ON discovery_samples(symbol, trading_day DESC);
                CREATE INDEX IF NOT EXISTS idx_discovery_samples_day
                    ON discovery_samples(trading_day DESC);
                """
            )

    def recent_symbols(self, *, before_day: date, days: int) -> set[str]:
        cutoff = (before_day - timedelta(days=max(int(days), 0))).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT symbol
                FROM discovery_samples
                WHERE trading_day >= ? AND trading_day < ?
                """,
                (cutoff, before_day.isoformat()),
            ).fetchall()
        return {str(row["symbol"] or "") for row in rows if row["symbol"]}

    def latest_run(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM discovery_runs
                ORDER BY trading_day DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_run(row) if row else {"status": "missing"}

    def run_for_day(self, trading_day: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_runs WHERE trading_day = ? LIMIT 1",
                (trading_day,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def save_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        trading_day = str(payload.get("trading_day") or "")
        selected = list(payload.get("selected_symbols") or [])
        results = [row for row in list(payload.get("results") or []) if isinstance(row, dict)]
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO discovery_runs (
                    trading_day, status, selected_symbols_json, results_json,
                    summary_json, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trading_day) DO UPDATE SET
                    status=excluded.status,
                    selected_symbols_json=excluded.selected_symbols_json,
                    results_json=excluded.results_json,
                    summary_json=excluded.summary_json,
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
                """,
                (
                    trading_day,
                    str(payload.get("status") or "ok"),
                    _json_dumps(selected),
                    _json_dumps(results),
                    _json_dumps(summary),
                    str(payload.get("error_message") or "")[:500],
                    now,
                    now,
                ),
            )
            sample_rows = self._sample_rows(selected)
            sample_rows.update({str(row.get("symbol") or ""): row for row in results})
            for rank, row in enumerate(sample_rows.values(), start=1):
                self._upsert_sample(conn, trading_day, rank, row, now)
        return self.run_for_day(trading_day) or {"status": "missing"}

    def _upsert_sample(
        self,
        conn: sqlite3.Connection,
        trading_day: str,
        rank: int,
        row: dict[str, Any],
        now: str,
    ) -> None:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            return
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        conn.execute(
            """
            INSERT INTO discovery_samples (
                trading_day, symbol, name, market, rank, status, analysis_id,
                stance, confidence, score, error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trading_day, symbol) DO UPDATE SET
                name=excluded.name,
                market=excluded.market,
                rank=excluded.rank,
                status=excluded.status,
                analysis_id=excluded.analysis_id,
                stance=excluded.stance,
                confidence=excluded.confidence,
                score=excluded.score,
                error_message=excluded.error_message,
                updated_at=excluded.updated_at
            """,
            (
                trading_day,
                symbol,
                str(row.get("name") or analysis.get("name") or ""),
                str(row.get("market") or ""),
                rank,
                str(row.get("status") or "selected"),
                analysis.get("id"),
                str(analysis.get("stance") or row.get("stance") or ""),
                analysis.get("confidence") or row.get("confidence"),
                row.get("score"),
                str(row.get("error_message") or "")[:240],
                now,
                now,
            ),
        )

    @staticmethod
    def _sample_rows(selected: list[Any]) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for item in selected:
            if isinstance(item, dict):
                symbol = str(item.get("symbol") or "").strip()
                row = dict(item)
            else:
                symbol = str(item or "").strip()
                row = {"symbol": symbol}
            if symbol:
                rows[symbol] = row
        return rows

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"] or 0),
            "trading_day": str(row["trading_day"] or ""),
            "status": str(row["status"] or ""),
            "selected_symbols": _json_loads(row["selected_symbols_json"], []),
            "results": _json_loads(row["results_json"], []),
            "summary": _json_loads(row["summary_json"], {}),
            "error_message": str(row["error_message"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }


class DailyDiscoveryService:
    def __init__(
        self,
        *,
        config: DailyDiscoveryConfig,
        directory_source: SymbolDirectorySource,
        symbol_analysis: SymbolAnalysisRunner | None,
    ) -> None:
        self.config = config
        self.repository = DailyDiscoveryRepository(config.db_path)
        self.directory_source = directory_source
        self.symbol_analysis = symbol_analysis

    def select_symbols(self, *, trading_day: date) -> list[dict[str, Any]]:
        excluded = self.repository.recent_symbols(
            before_day=trading_day,
            days=self.config.exclude_recent_days,
        )
        selected: list[dict[str, Any]] = []
        selected_symbols: set[str] = set()
        counts = {"KOSPI": self.config.kospi_count, "KOSDAQ": self.config.kosdaq_count}
        for market in DISCOVERY_MARKETS:
            market_excluded = set(excluded) | selected_symbols
            rows = self.directory_source.list_symbol_directory(
                market=market,
                limit=self.config.candidate_limit_per_market,
                exclude_symbols=market_excluded,
            )
            used_market_fallback = False
            if not rows:
                rows = self.directory_source.list_symbol_directory(
                    market="",
                    limit=max(int(self.config.candidate_limit_per_market), 3_000),
                    exclude_symbols=market_excluded,
                )
                used_market_fallback = bool(rows)
            sample = _stable_sample(
                rows,
                seed=f"{trading_day.isoformat()}:{market}",
                count=counts[market],
            )
            for row in sample:
                original_market = str(row.get("market") or "")
                row["market"] = market
                if used_market_fallback:
                    row["source_market"] = original_market
                    row["selection_market_fallback"] = True
                symbol = str(row.get("symbol") or "").strip()
                if symbol:
                    selected_symbols.add(symbol)
            selected.extend(sample)
        return selected

    def should_run_for_day(self, trading_day: str | date) -> bool:
        if not self.config.enabled:
            return False
        day = _trading_day_key(trading_day)
        if not day:
            return False
        existing = self.repository.run_for_day(day)
        if existing is None:
            return True
        status = str(existing.get("status") or "").strip().lower()
        return status not in COMPLETED_DISCOVERY_STATUSES

    async def run_once(
        self,
        *,
        trading_day: date,
        force: bool = False,
    ) -> dict[str, Any]:
        day = trading_day.isoformat()
        existing = self.repository.run_for_day(day)
        if not self.config.enabled and not force:
            return {"status": "skipped", "reason": "disabled"}
        if (
            existing
            and str(existing.get("status") or "").strip().lower()
            in COMPLETED_DISCOVERY_STATUSES
            and not force
        ):
            return {"status": "skipped", "reason": "already_completed", "run": existing}
        if self.symbol_analysis is None:
            return {"status": "error", "error_message": "symbol_analysis_missing"}

        selected = self.select_symbols(trading_day=trading_day)
        if not selected:
            summary = {
                "selected_count": 0,
                "analyzed_count": 0,
                "error_count": 0,
                "block_candidate_count": 0,
                "pre_surge_candidate_count": 0,
                "top_symbols": [],
            }
            run = self.repository.save_run(
                {
                    "trading_day": day,
                    "status": "empty",
                    "selected_symbols": [],
                    "results": [],
                    "summary": summary,
                }
            )
            return {
                "status": "empty",
                "trading_day": day,
                "selected_count": 0,
                "analyzed_count": 0,
                "summary": summary,
                "results": [],
                "run": run,
            }

        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for row in selected:
            symbol = str(row.get("symbol") or "").strip()
            try:
                result = await self.symbol_analysis.run(
                    symbol,
                    trigger=DISCOVERY_TRIGGER,
                    force_collect=self.config.force_collect,
                )
            except Exception as exc:
                error_message = str(exc)[:240]
                errors.append({"symbol": symbol, "error": error_message})
                results.append(self._error_result(row, error_message))
                continue
            results.append(self._success_result(row, result))

        results = [enrich_discovery_result(row) for row in results]
        results.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        analyzed_count = len([row for row in results if row.get("status") == "ok"])
        summary = {
            "selected_count": len(selected),
            "analyzed_count": analyzed_count,
            "error_count": len(errors),
            "block_candidate_count": len(
                [
                    row
                    for row in results
                    if (row.get("analysis") or {}).get("stance") == "block_candidate"
                ]
            ),
            "pre_surge_candidate_count": len(
                [
                    row
                    for row in results
                    if (row.get("pre_surge") or {}).get("is_candidate")
                ]
            ),
            "top_symbols": [row.get("symbol") for row in results[:5]],
        }
        run = self.repository.save_run(
            {
                "trading_day": day,
                "status": "partial" if errors else "ok",
                "selected_symbols": [dict(row) for row in selected],
                "results": results,
                "summary": summary,
                "error_message": "; ".join(
                    f"{row['symbol']}:{row['error']}" for row in errors
                )[:500],
            }
        )
        return {
            "status": run.get("status"),
            "trading_day": day,
            "selected_count": len(selected),
            "analyzed_count": analyzed_count,
            "summary": summary,
            "results": [_compact_discovery_result(row) for row in results],
            "run": run,
        }

    def latest_context(self, *, limit: int = 10) -> dict[str, Any]:
        run = self.repository.latest_run()
        if run.get("status") == "missing":
            return {
                "status": "missing",
                "trading_day": "",
                "summary": {},
                "items": [],
                "block_candidates": [],
                "pre_surge_candidates": [],
            }
        max_rows = max(int(limit), 1)
        results = [
            _compact_discovery_result(row)
            for row in list(run.get("results") or [])[:max_rows]
            if isinstance(row, dict)
        ]
        return {
            "status": run.get("status"),
            "trading_day": run.get("trading_day"),
            "summary": run.get("summary") or {},
            "items": results,
            "block_candidates": [
                row
                for row in results
                if (row.get("analysis") or {}).get("stance") == "block_candidate"
            ],
            "pre_surge_candidates": [
                row
                for row in results
                if (row.get("pre_surge") or {}).get("is_candidate")
            ],
            "updated_at": run.get("updated_at"),
        }

    @staticmethod
    def _success_result(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        analysis = _analysis_payload(result if isinstance(result, dict) else {})
        symbol = str(row.get("symbol") or result.get("symbol") or "").strip()
        return {
            "symbol": symbol,
            "name": str(row.get("name") or analysis.get("name") or ""),
            "market": str(row.get("market") or ""),
            "status": str(result.get("status") or "ok"),
            "score": _score_analysis(analysis),
            "analysis": analysis,
        }

    @staticmethod
    def _error_result(row: dict[str, Any], error_message: str) -> dict[str, Any]:
        return {
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or ""),
            "market": str(row.get("market") or ""),
            "status": "error",
            "error_message": error_message,
            "score": 0.0,
            "analysis": {
                "stance": "stale",
                "confidence": 0.0,
                "summary": "",
                "reasons": [],
                "risks": [error_message],
            },
        }
