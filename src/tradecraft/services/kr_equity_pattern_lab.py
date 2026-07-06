from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class KREquityPatternLabConfig:
    db_path: str | Path = ".runtime/kr_equity_pattern_lab.db"
    live_performance_db_path: str | Path = ".runtime/live_performance.db"
    market_judgment_db_path: str | Path = ""
    min_samples: int = 3
    max_sets: int = 100
    interval: str = "1d"
    replay_min_confidence: float = 0.55
    replay_exit_delay_minutes: int = 30
    replay_max_samples: int = 1000


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _clean_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_kr_symbol(value: Any) -> bool:
    raw = str(value or "").strip()
    return len(raw) == 6 and raw.isdigit()


def _metadata_for_row(row: dict[str, Any]) -> dict[str, Any]:
    source = _json_loads(row.get("source_json"))
    if isinstance(source, dict):
        metadata = source.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        block = source.get("block")
        if isinstance(block, dict):
            block_metadata = _json_loads(block.get("metadata_json"))
            if isinstance(block_metadata, dict):
                return block_metadata
    return {}


def _profit_factor(values: list[float]) -> float:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_loss = abs(sum(losses))
    if gross_loss <= 0:
        return 999.0 if wins else 0.0
    return sum(wins) / gross_loss


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _derive_family(metadata: dict[str, Any]) -> str:
    direct = _clean_key(metadata.get("pattern_family") or metadata.get("family"))
    if direct:
        return direct
    valuation = _clean_key(metadata.get("valuation_label"))
    if any(token in valuation for token in ("undervalued", "저평가", "cheap", "value")):
        return "value_cycle"
    if any(token in valuation for token in ("momentum", "breakout", "모멘텀", "돌파")):
        return "momentum"
    if metadata.get("sector") or metadata.get("sector_rotation"):
        return "sector_rotation"
    horizon = _clean_key(metadata.get("horizon"))
    if horizon:
        return f"horizon_{horizon}"
    return "kr_block_outcome"


REJECTION_REASON_GUIDANCE: dict[str, dict[str, str]] = {
    "out_of_sample_expectancy_negative": {
        "focus": "oos_expectancy",
        "block_design_constraint": (
            "Do not size up this family until the next candidate has positive "
            "out-of-sample expectancy or live-shadow expectancy after costs."
        ),
        "research_task": (
            "Rebuild the sample around better entry location, valuation/price "
            "discount, and post-entry holding window before proposing scale."
        ),
    },
    "out_of_sample_profit_factor_low": {
        "focus": "oos_profit_factor",
        "block_design_constraint": (
            "Require wider target-to-cost room and avoid thin intraday targets "
            "that cannot survive fees, taxes, slippage, and spread."
        ),
        "research_task": (
            "Retest target/stop distances with realistic KIS costs and reject "
            "setups whose profit factor stays below 1.05 out of sample."
        ),
    },
    "walk_forward_pass_rate_low": {
        "focus": "walk_forward",
        "block_design_constraint": (
            "Keep this family in probe or waiting-entry mode until rolling "
            "walk-forward pass rate improves across multiple windows."
        ),
        "research_task": (
            "Split the evidence by regime, horizon, and entry trigger to find "
            "which sub-condition breaks during forward windows."
        ),
    },
    "train_test_gap_large": {
        "focus": "overfit_gap",
        "block_design_constraint": (
            "Treat strong in-sample results as suspect unless current evidence "
            "matches the tested regime and price-location assumptions."
        ),
        "research_task": (
            "Add multiple-testing penalty and prefer simpler parameter sets "
            "with smaller train/test expectancy gaps."
        ),
    },
    "walk_forward_windows_missing": {
        "focus": "missing_wfa",
        "block_design_constraint": (
            "Use only small probes or live-shadow tracking until rolling "
            "walk-forward windows exist for this family."
        ),
        "research_task": (
            "Collect more dated samples and rerun rolling windows before "
            "promoting the family as a positive prior."
        ),
    },
    "out_of_sample_missing": {
        "focus": "missing_oos",
        "block_design_constraint": (
            "Do not treat this as a validated edge until an unused sample "
            "period exists and is positive after costs."
        ),
        "research_task": (
            "Create a chronological holdout split and keep current live blocks "
            "as evidence-building samples."
        ),
    },
}


def _top_rejection_reasons_from_sets(
    rows: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        quality = row.get("walk_forward_quality")
        if not isinstance(quality, dict):
            quality = _json_loads(row.get("walk_forward_quality_json"))
        reasons = quality.get("reasons") if isinstance(quality, dict) else []
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            clean_reason = str(reason or "").strip()
            if clean_reason:
                counts[clean_reason] = counts.get(clean_reason, 0) + 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[: max(int(limit), 1)]
    ]


def _repair_priorities_from_rejections(
    top_rejection_reasons: list[dict[str, Any]],
    *,
    active_count: int = 0,
    rejected_count: int = 0,
) -> list[dict[str, Any]]:
    priorities: list[dict[str, Any]] = []
    if active_count <= 0 and rejected_count > 0:
        priorities.append(
            {
                "priority": "active_edge_rebuild",
                "reason": "no_active_kr_pattern_sets",
                "count": int(rejected_count),
                "focus": "validated_edge_absent",
                "block_design_constraint": (
                    "KIS 신규 블록은 active pattern prior가 생길 때까지 "
                    "probe/waiting-entry 중심으로 설계한다."
                ),
                "research_task": (
                    "rejected set의 공통 실패 사유를 줄이는 새 entry family를 "
                    "만들고 live-shadow/OOS 표본을 다시 쌓는다."
                ),
            }
        )
    for item in top_rejection_reasons:
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        guidance = REJECTION_REASON_GUIDANCE.get(
            reason,
            {
                "focus": "unknown_rejection",
                "block_design_constraint": (
                    "Keep this rejected family as caution evidence until the "
                    "failure reason is explained by fresh research."
                ),
                "research_task": (
                    "Inspect rejected samples and convert the failure into a "
                    "specific entry, sizing, target, or stop adjustment."
                ),
            },
        )
        priorities.append(
            {
                "priority": f"repair_{reason}",
                "reason": reason,
                "count": int(item.get("count") or 0),
                **guidance,
            }
        )
    return priorities[:6]


def _row_to_set(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    parameter_set = _json_loads(data.get("parameter_set_json"))
    quality = _json_loads(data.get("walk_forward_quality_json"))
    return {
        **data,
        "parameter_set": parameter_set if isinstance(parameter_set, dict) else {},
        "walk_forward_quality": quality if isinstance(quality, dict) else {},
    }


class KREquityPatternLabRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kr_equity_pattern_lab_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source_scope TEXT NOT NULL DEFAULT 'kr_equity_pattern_lab',
                    live_performance_db_path TEXT NOT NULL DEFAULT '',
                    eligible_sample_count INTEGER NOT NULL DEFAULT 0,
                    optimized_set_count INTEGER NOT NULL DEFAULT 0,
                    active_set_count INTEGER NOT NULL DEFAULT 0,
                    rejected_set_count INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS optimized_strategy_sets (
                    set_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    trial_id TEXT NOT NULL DEFAULT '',
                    pattern_id TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL DEFAULT '1d',
                    family TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT 'long',
                    parameter_set_json TEXT NOT NULL DEFAULT '{}',
                    objective TEXT NOT NULL DEFAULT 'kr_live_forward_edge_v1',
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
                CREATE INDEX IF NOT EXISTS idx_kr_optimized_sets_symbol
                    ON optimized_strategy_sets(symbol, interval, objective_score DESC);
                """
            )

    def replace_sets(self, *, run_payload: dict[str, Any], sets: list[dict[str, Any]]) -> None:
        run_id = str(run_payload.get("run_id") or "")
        with self._connect() as conn:
            conn.execute("DELETE FROM optimized_strategy_sets")
            for row in sets:
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
                    """,
                    (
                        str(row.get("set_id") or ""),
                        run_id,
                        str(row.get("trial_id") or ""),
                        str(row.get("pattern_id") or ""),
                        str(row.get("symbol") or ""),
                        str(row.get("interval") or "1d"),
                        str(row.get("family") or ""),
                        str(row.get("direction") or "long"),
                        _json_dumps(row.get("parameter_set") or {}),
                        str(row.get("objective") or "kr_live_forward_edge_v1"),
                        _safe_float(row.get("objective_score")),
                        int(row.get("trade_count") or 0),
                        _safe_float(row.get("win_rate")),
                        _safe_float(row.get("expectancy_r")),
                        _safe_float(row.get("profit_factor")),
                        _safe_float(row.get("max_loss_r")),
                        str(row.get("train_start") or ""),
                        str(row.get("train_end") or ""),
                        str(row.get("test_start") or ""),
                        str(row.get("test_end") or ""),
                        _safe_float(row.get("in_sample_expectancy_r")),
                        int(row.get("out_of_sample_trade_count") or 0),
                        _safe_float(row.get("out_of_sample_expectancy_r")),
                        _safe_float(row.get("out_of_sample_profit_factor")),
                        _safe_float(row.get("out_of_sample_max_drawdown_r")),
                        str(row.get("overfit_risk") or ""),
                        _json_dumps(row.get("walk_forward_quality") or {}),
                        str(row.get("sample_start") or ""),
                        str(row.get("sample_end") or ""),
                        str(row.get("status") or "active"),
                        str(row.get("promoted_at") or utc_now_iso()),
                    ),
                )
            conn.execute(
                """
                INSERT INTO kr_equity_pattern_lab_runs (
                    run_id, status, source_scope, live_performance_db_path,
                    eligible_sample_count, optimized_set_count, active_set_count,
                    rejected_set_count, message, payload_json, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    eligible_sample_count=excluded.eligible_sample_count,
                    optimized_set_count=excluded.optimized_set_count,
                    active_set_count=excluded.active_set_count,
                    rejected_set_count=excluded.rejected_set_count,
                    message=excluded.message,
                    payload_json=excluded.payload_json,
                    computed_at=excluded.computed_at
                """,
                (
                    run_id,
                    str(run_payload.get("status") or ""),
                    "kr_equity_pattern_lab",
                    str(run_payload.get("live_performance_db_path") or ""),
                    int(run_payload.get("eligible_sample_count") or 0),
                    int(run_payload.get("optimized_set_count") or 0),
                    int(run_payload.get("active_set_count") or 0),
                    int(run_payload.get("rejected_set_count") or 0),
                    str(run_payload.get("message") or ""),
                    _json_dumps(run_payload),
                    str(run_payload.get("computed_at") or utc_now_iso()),
                ),
            )

    def latest_sets(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM optimized_strategy_sets
                {where}
                ORDER BY objective_score DESC, promoted_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_set(row) for row in rows]

    def context(self, *, limit: int = 20) -> dict[str, Any]:
        active = self.latest_sets(status="active", limit=limit)
        rejected = self.latest_sets(status="rejected", limit=limit)
        with self._connect() as conn:
            rejection_reason_rows = conn.execute(
                """
                SELECT walk_forward_quality_json
                FROM optimized_strategy_sets
                WHERE status = 'rejected'
                ORDER BY promoted_at DESC
                LIMIT 200
                """
            ).fetchall()
            total_rejected_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM optimized_strategy_sets
                    WHERE status = 'rejected'
                    """
                ).fetchone()[0]
                or 0
            )
        top_rejection_reasons = _top_rejection_reasons_from_sets(
            [dict(row) for row in rejection_reason_rows]
        )
        repair_priorities = _repair_priorities_from_rejections(
            top_rejection_reasons,
            active_count=len(active),
            rejected_count=total_rejected_count,
        )
        with self._connect() as conn:
            latest_run = conn.execute(
                """
                SELECT *
                FROM kr_equity_pattern_lab_runs
                ORDER BY computed_at DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "status": str(latest_run["status"]) if latest_run is not None else "empty",
            "source_scope": "kr_equity_pattern_lab",
            "db_path": str(self.path),
            "optimized_strategy_sets": active,
            "rejected_optimized_strategy_sets": rejected,
            "optimization": {
                "objective": "kr_live_forward_edge_v1",
                "set_count": len(active),
                "rejected_set_count": len(rejected),
            },
            "validation_hint": {
                "status": (
                    "needs_revalidation"
                    if not active and rejected
                    else ("ok" if active else "needs_optimization")
                ),
                "reasons": [
                    str(item.get("reason") or "")
                    for item in top_rejection_reasons[:4]
                    if item.get("reason")
                ],
            },
            "top_rejection_reasons": top_rejection_reasons,
            "repair_priorities": repair_priorities,
            "next_block_design_constraints": [
                str(item.get("block_design_constraint") or "")
                for item in repair_priorities
                if item.get("block_design_constraint")
            ][:6],
            "latest_run": dict(latest_run) if latest_run is not None else {},
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            run_count = conn.execute(
                "SELECT COUNT(*) FROM kr_equity_pattern_lab_runs"
            ).fetchone()[0]
            latest_run = conn.execute(
                """
                SELECT *
                FROM kr_equity_pattern_lab_runs
                ORDER BY computed_at DESC
                LIMIT 1
                """
            ).fetchone()
            active_count = conn.execute(
                "SELECT COUNT(*) FROM optimized_strategy_sets WHERE status = 'active'"
            ).fetchone()[0]
            rejected_count = conn.execute(
                "SELECT COUNT(*) FROM optimized_strategy_sets WHERE status = 'rejected'"
            ).fetchone()[0]
            total_count = conn.execute(
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

        top_rejection_reasons = _top_rejection_reasons_from_sets(
            [dict(row) for row in rejection_rows]
        )
        repair_priorities = _repair_priorities_from_rejections(
            top_rejection_reasons,
            active_count=int(active_count or 0),
            rejected_count=int(rejected_count or 0),
        )

        validation_hint_status = "ok"
        validation_hint_reasons: list[str] = []
        if int(total_count or 0) == 0:
            validation_hint_status = "needs_optimization"
            validation_hint_reasons.append("optimized_sets_missing")
        elif int(active_count or 0) <= 0:
            validation_hint_status = "needs_revalidation"
            validation_hint_reasons.extend(
                row["reason"] for row in top_rejection_reasons[:4]
            )

        latest_run_payload = dict(latest_run) if latest_run is not None else {}
        return {
            "status": str(latest_run_payload.get("status") or "empty"),
            "source_scope": "kr_equity_pattern_lab",
            "db_path": str(self.path),
            "run_count": int(run_count or 0),
            "eligible_sample_count": int(
                latest_run_payload.get("eligible_sample_count") or 0
            ),
            "optimized_set_count": int(active_count or 0),
            "active_optimized_set_count": int(active_count or 0),
            "rejected_optimized_set_count": int(rejected_count or 0),
            "total_optimized_set_count": int(total_count or 0),
            "latest_optimized_set_at": str(latest_optimized_set_at or ""),
            "latest_run": latest_run_payload,
            "top_rejection_reasons": top_rejection_reasons,
            "repair_priorities": repair_priorities,
            "next_block_design_constraints": [
                str(item.get("block_design_constraint") or "")
                for item in repair_priorities
                if item.get("block_design_constraint")
            ][:6],
            "validation_hint": {
                "status": validation_hint_status,
                "reasons": validation_hint_reasons,
            },
        }


class KREquityPatternLabService:
    def __init__(self, config: KREquityPatternLabConfig) -> None:
        self.config = config
        self.repository = KREquityPatternLabRepository(config.db_path)

    def run_once(self) -> dict[str, Any]:
        live_outcomes = self._load_kis_live_outcomes()
        replay_outcomes = self._load_market_judgment_replay_outcomes()
        outcomes = live_outcomes + replay_outcomes
        grouped = self._group_outcomes(outcomes)
        sets: list[dict[str, Any]] = []
        for (symbol, family), rows in sorted(grouped.items()):
            if len(rows) < max(int(self.config.min_samples), 1):
                continue
            sets.append(self._build_set(symbol=symbol, family=family, rows=rows))
        sets = sorted(
            sets,
            key=lambda row: _safe_float(row.get("objective_score")),
            reverse=True,
        )[: max(int(self.config.max_sets), 1)]
        active_count = sum(1 for row in sets if row.get("status") == "active")
        rejected_count = sum(1 for row in sets if row.get("status") == "rejected")
        status = "ok" if sets else ("missing" if not outcomes else "insufficient_samples")
        payload = {
            "status": status,
            "source_scope": "kr_equity_pattern_lab",
            "run_id": f"kr-pattern-{_stable_id(utc_now_iso(), len(outcomes), len(sets))}",
            "computed_at": utc_now_iso(),
            "db_path": str(self.repository.path),
            "live_performance_db_path": str(self.config.live_performance_db_path),
            "market_judgment_db_path": str(self.config.market_judgment_db_path or ""),
            "eligible_sample_count": len(outcomes),
            "live_sample_count": len(live_outcomes),
            "replay_sample_count": len(replay_outcomes),
            "group_count": len(grouped),
            "min_samples": int(self.config.min_samples),
            "optimized_set_count": len(sets),
            "active_set_count": active_count,
            "rejected_set_count": rejected_count,
            "message": (
                "built_from_kis_live_alpha_and_replay"
                if sets
                else "eligible KIS live alpha/replay samples are not enough for grouped WFA"
            ),
        }
        self.repository.replace_sets(run_payload=payload, sets=sets)
        return payload

    def _load_kis_live_outcomes(self) -> list[dict[str, Any]]:
        path = Path(self.config.live_performance_db_path)
        if not path.exists():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM live_block_performance
                WHERE venue = 'kis'
                  AND filled = 1
                  AND include_in_jue_alpha = 1
                ORDER BY computed_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _load_market_judgment_replay_outcomes(self) -> list[dict[str, Any]]:
        raw_path = str(self.config.market_judgment_db_path or "").strip()
        if not raw_path:
            return []
        path = Path(raw_path)
        if not path.exists():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {"symbol_judgments", "judgment_runs", "quote_snapshots"}
            if not required.issubset(tables):
                return []
            rows = conn.execute(
                """
                SELECT
                    sj.id AS judgment_id,
                    sj.run_id,
                    sj.symbol,
                    sj.name,
                    sj.stance,
                    sj.account_action,
                    sj.horizon,
                    sj.confidence,
                    sj.quote_json,
                    sj.strategy_json,
                    jr.run_at,
                    jr.market_session
                FROM symbol_judgments sj
                JOIN judgment_runs jr ON jr.id = sj.run_id
                WHERE jr.status = 'ok'
                ORDER BY jr.run_at ASC, sj.id ASC
                LIMIT ?
                """,
                (max(int(self.config.replay_max_samples), 1),),
            ).fetchall()

            outcomes: list[dict[str, Any]] = []
            for row in rows:
                outcome = self._market_judgment_row_to_replay_outcome(conn, dict(row))
                if outcome:
                    outcomes.append(outcome)
            return outcomes

    def _market_judgment_row_to_replay_outcome(
        self,
        conn: sqlite3.Connection,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        symbol = str(row.get("symbol") or "").strip()
        if not _is_kr_symbol(symbol):
            return None
        action = _clean_key(row.get("account_action"))
        stance = _clean_key(row.get("stance"))
        if action not in {"watch_add", "new_watch", "hold"}:
            return None
        if stance not in {"watch", "confirm", "hold"}:
            return None
        confidence = _safe_float(row.get("confidence"))
        if confidence < float(self.config.replay_min_confidence):
            return None

        quote = _json_loads(row.get("quote_json"))
        if not isinstance(quote, dict):
            return None
        entry_price = _safe_float(quote.get("price"))
        if entry_price <= 0:
            return None
        entry_at = str(quote.get("fetched_at") or row.get("run_at") or "")
        parsed_entry_at = _parse_iso(entry_at)
        if parsed_entry_at is None:
            return None
        min_exit_at = (
            parsed_entry_at
            + timedelta(minutes=max(int(self.config.replay_exit_delay_minutes), 1))
        ).isoformat()
        exit_row = conn.execute(
            """
            SELECT price, fetched_at
            FROM quote_snapshots
            WHERE symbol = ?
              AND status = 'ok'
              AND price > 0
              AND fetched_at >= ?
            ORDER BY fetched_at ASC, id ASC
            LIMIT 1
            """,
            (symbol, min_exit_at),
        ).fetchone()
        if exit_row is None:
            return None
        exit_price = _safe_float(exit_row["price"])
        if exit_price <= 0:
            return None

        notional = entry_price
        cost_total = abs(notional) * 0.0018
        gross_pnl = exit_price - entry_price
        net_pnl = gross_pnl - cost_total
        pnl_pct = net_pnl / entry_price * 100.0 if entry_price > 0 else 0.0
        strategy = _json_loads(row.get("strategy_json"))
        strategy = strategy if isinstance(strategy, dict) else {}
        valuation = strategy.get("valuation") if isinstance(strategy.get("valuation"), dict) else {}
        metadata = {
            "source_type": "kis_market_judgment_replay_v1",
            "horizon": str(row.get("horizon") or "unknown"),
            "market_session": str(row.get("market_session") or ""),
            "confidence": round(confidence, 6),
            "account_action": action,
            "stance": stance,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_at": parsed_entry_at.isoformat(),
            "exit_at": str(exit_row["fetched_at"] or ""),
            "cost_model_status": "estimated_replay",
            "cost_components": {"fees_taxes_slippage": round(cost_total, 6)},
        }
        valuation_label = valuation.get("label") if isinstance(valuation, dict) else ""
        if valuation_label:
            metadata["valuation_label"] = valuation_label
        return {
            "block_id": f"replay-{row.get('run_id')}-{row.get('judgment_id')}-{symbol}",
            "venue": "kis",
            "symbol": symbol,
            "gross_pnl": round(gross_pnl, 6),
            "net_pnl": round(net_pnl, 6),
            "cost_total": round(cost_total, 6),
            "pnl_pct": round(pnl_pct, 6),
            "filled": 1,
            "source_json": _json_dumps({"metadata": metadata}),
            "computed_at": str(exit_row["fetched_at"] or utc_now_iso()),
        }

    def _group_outcomes(
        self,
        outcomes: list[dict[str, Any]],
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in outcomes:
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            metadata = _metadata_for_row(row)
            family = _derive_family(metadata)
            grouped.setdefault((symbol, family), []).append(row)
        return grouped

    def _build_set(
        self,
        *,
        symbol: str,
        family: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ordered = sorted(rows, key=lambda row: str(row.get("computed_at") or ""))
        split_index = max(1, int(len(ordered) * 0.6))
        if split_index >= len(ordered):
            split_index = len(ordered) - 1
        train = ordered[:split_index]
        test = ordered[split_index:]
        pnl_pct = [_safe_float(row.get("pnl_pct")) for row in ordered]
        train_pct = [_safe_float(row.get("pnl_pct")) for row in train]
        test_pct = [_safe_float(row.get("pnl_pct")) for row in test]
        net_pnls = [_safe_float(row.get("net_pnl")) for row in ordered]
        test_net_pnls = [_safe_float(row.get("net_pnl")) for row in test]
        wins = [value for value in net_pnls if value > 0]
        win_rate = len(wins) / len(net_pnls) if net_pnls else 0.0
        test_pf = _profit_factor(test_net_pnls)
        test_expectancy = _avg(test_pct) / 100.0
        train_expectancy = _avg(train_pct) / 100.0
        max_loss_r = min(pnl_pct) / 100.0 if pnl_pct else 0.0
        test_drawdown_r = _max_drawdown([value / 100.0 for value in test_pct])
        windows = self._rolling_walk_forward_windows(ordered)
        window_count = len(windows)
        passed_window_count = sum(1 for window in windows if window.get("passed"))
        window_pass_rate = (
            passed_window_count / window_count if window_count else 0.0
        )
        reasons: list[str] = []
        if len(test) < 1:
            reasons.append("out_of_sample_missing")
        if test_expectancy <= 0:
            reasons.append("out_of_sample_expectancy_negative")
        if test_pf < 1.05:
            reasons.append("out_of_sample_profit_factor_low")
        if train_expectancy > 0 and train_expectancy - test_expectancy > 0.03:
            reasons.append("train_test_gap_large")
        if not windows:
            reasons.append("walk_forward_windows_missing")
        elif window_pass_rate < 0.5:
            reasons.append("walk_forward_pass_rate_low")
        passed = not reasons
        overfit_risk = "low" if passed else ("high" if "train_test_gap_large" in reasons else "medium")
        objective_score = self._objective_score(
            expectancy_r=test_expectancy,
            profit_factor=test_pf,
            max_drawdown_r=test_drawdown_r,
            passed=passed,
        )
        now = utc_now_iso()
        set_id = f"kr-{symbol}-{family}-{_stable_id(symbol, family, len(rows), rows[-1].get('computed_at'))}"
        quality = {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "reasons": reasons,
            "overfit_risk": overfit_risk,
            "source": "kis_live_block_performance",
            "method": "chronological_60_40_split_plus_rolling_wfa_v1",
            "min_samples": int(self.config.min_samples),
            "window_count": window_count,
            "passed_window_count": passed_window_count,
            "window_pass_rate": round(window_pass_rate, 6),
            "windows": windows,
        }
        return {
            "set_id": set_id,
            "trial_id": f"{set_id}-trial",
            "pattern_id": f"kr-live-{family}",
            "symbol": symbol,
            "interval": str(self.config.interval or "1d"),
            "family": family,
            "direction": "long",
            "parameter_set": {
                "method": "kis_live_forward_group_v1",
                "family": family,
                "min_samples": int(self.config.min_samples),
                "sample_count": len(rows),
                "source_types": sorted(
                    {
                        str(_metadata_for_row(row).get("source_type") or "kis_live_alpha")
                        for row in rows
                    }
                ),
            },
            "objective": "kr_live_forward_edge_v1",
            "objective_score": objective_score,
            "trade_count": len(rows),
            "win_rate": round(win_rate, 6),
            "expectancy_r": round(_avg(pnl_pct) / 100.0, 6),
            "profit_factor": round(_profit_factor(net_pnls), 6),
            "max_loss_r": round(max_loss_r, 6),
            "train_start": str(train[0].get("computed_at") or ""),
            "train_end": str(train[-1].get("computed_at") or ""),
            "test_start": str(test[0].get("computed_at") or "") if test else "",
            "test_end": str(test[-1].get("computed_at") or "") if test else "",
            "in_sample_expectancy_r": round(train_expectancy, 6),
            "out_of_sample_trade_count": len(test),
            "out_of_sample_expectancy_r": round(test_expectancy, 6),
            "out_of_sample_profit_factor": round(test_pf, 6),
            "out_of_sample_max_drawdown_r": round(test_drawdown_r, 6),
            "overfit_risk": overfit_risk,
            "walk_forward_quality": quality,
            "sample_start": str(ordered[0].get("computed_at") or ""),
            "sample_end": str(ordered[-1].get("computed_at") or ""),
            "status": "active" if passed else "rejected",
            "promoted_at": now,
        }

    def _rolling_walk_forward_windows(
        self,
        ordered: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(ordered) < 3:
            return []
        initial_train_size = max(2, int(self.config.min_samples) - 1)
        if initial_train_size >= len(ordered):
            initial_train_size = len(ordered) - 1
        windows: list[dict[str, Any]] = []
        for test_index in range(initial_train_size, len(ordered)):
            train = ordered[:test_index]
            test = [ordered[test_index]]
            test_pct = [_safe_float(row.get("pnl_pct")) for row in test]
            test_net = [_safe_float(row.get("net_pnl")) for row in test]
            test_expectancy = _avg(test_pct) / 100.0
            test_pf = _profit_factor(test_net)
            reasons: list[str] = []
            if test_expectancy <= 0:
                reasons.append("out_of_sample_expectancy_negative")
            if test_pf < 1.05:
                reasons.append("out_of_sample_profit_factor_low")
            passed = not reasons
            windows.append(
                {
                    "window_index": len(windows) + 1,
                    "train_start": str(train[0].get("computed_at") or ""),
                    "train_end": str(train[-1].get("computed_at") or ""),
                    "test_start": str(test[0].get("computed_at") or ""),
                    "test_end": str(test[-1].get("computed_at") or ""),
                    "train_trade_count": len(train),
                    "test_trade_count": len(test),
                    "test_expectancy_r": round(test_expectancy, 6),
                    "test_profit_factor": round(test_pf, 6),
                    "passed": passed,
                    "reasons": reasons,
                }
            )
        return windows

    @staticmethod
    def _objective_score(
        *,
        expectancy_r: float,
        profit_factor: float,
        max_drawdown_r: float,
        passed: bool,
    ) -> float:
        pf_score = min(max(profit_factor, 0.0), 3.0) / 3.0 * 35.0
        expectancy_score = min(max(expectancy_r, -0.03), 0.05) / 0.05 * 45.0
        drawdown_penalty = min(abs(min(max_drawdown_r, 0.0)) * 120.0, 25.0)
        pass_bonus = 15.0 if passed else 0.0
        return round(max(min(pf_score + expectancy_score + pass_bonus - drawdown_penalty, 100.0), 0.0), 6)
