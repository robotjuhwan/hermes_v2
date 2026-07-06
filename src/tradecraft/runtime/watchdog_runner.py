from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import (
    restart_runner_processes,
    runner_process_status,
    write_current_runner_pid,
)
from tradecraft.runtime.state_store import RuntimeStateStore

logger = logging.getLogger(__name__)

DEFAULT_WATCHDOG_RUNNER_KEYS: tuple[str, ...] = (
    "control",
    "runtime",
    "strategy_insights",
    "kis_block_trader",
    "binance_block_trader",
    "market_judge",
    "market_pulse",
    "investment_memory",
    "live_evaluator",
    "crypto_market_research",
    "crypto_pattern_lab",
    "crypto_alpha",
)

ENABLED_CRITICAL_RUNNERS: tuple[tuple[str, str], ...] = (
    ("strategy_insights", "strategy_insights_enabled"),
    ("kis_block_trader", "kis_block_trader_enabled"),
    ("binance_block_trader", "binance_block_trader_enabled"),
    ("market_judge", "market_judge_enabled"),
    ("market_pulse", "market_pulse_enabled"),
    ("investment_memory", "investment_memory_enabled"),
    ("live_evaluator", "live_evaluator_enabled"),
    ("crypto_market_research", "crypto_market_research_enabled"),
    ("crypto_pattern_lab", "crypto_pattern_lab_enabled"),
    ("crypto_alpha", "crypto_alpha_enabled"),
)

StatusProvider = Callable[[str], dict[str, Any]]
RestartFunc = Callable[..., dict[str, Any]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_runner_keys(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return list(DEFAULT_WATCHDOG_RUNNER_KEYS)
    keys: list[str] = []
    for item in text.replace(";", ",").split(","):
        key = item.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _effective_runner_keys(settings: Any) -> list[str]:
    keys = _parse_runner_keys(getattr(settings, "watchdog_runner_keys", ""))
    if not _truthy(getattr(settings, "research_enabled", False)):
        keys = [key for key in keys if key != "research"]
    for key, enabled_attr in ENABLED_CRITICAL_RUNNERS:
        if key not in keys and _truthy(getattr(settings, enabled_attr, False)):
            keys.append(key)
    return keys


def _parse_iso_datetime(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _needs_restart(row: dict[str, Any]) -> bool:
    if bool(row.get("stale_process")):
        return True
    if str(row.get("status") or "") != "running":
        return True
    if not bool(row.get("alive")):
        return True
    if str(row.get("pid_file_status") or "") in {"stale", "mismatch"} and not bool(
        row.get("alive")
    ):
        return True
    return False


def _annotate_runtime_state_health(
    key: str,
    row: dict[str, Any],
    settings: Any,
    *,
    now: datetime,
) -> dict[str, Any]:
    if key != "research":
        return row
    state_path = str(getattr(settings, "research_state_path", "") or "").strip()
    if not state_path:
        return row
    snapshot = RuntimeStateStore(state_path).read_snapshot() or {}
    state_status = str(snapshot.get("status") or "").strip()
    updated_at = _parse_iso_datetime(snapshot.get("updated_at"))
    timeout_sec = max(int(getattr(settings, "naver_reports_cycle_timeout_sec", 3600)), 1)
    age_sec = int((now - updated_at).total_seconds()) if updated_at else None
    stale_running = bool(
        state_status.endswith("_running")
        and (age_sec is None or age_sec >= timeout_sec)
    )
    row["runtime_state_status"] = state_status
    row["runtime_state_updated_at"] = updated_at.isoformat() if updated_at else ""
    row["runtime_state_age_sec"] = age_sec
    row["runtime_state_timeout_sec"] = timeout_sec
    row["stale_runtime_state"] = stale_running
    if stale_running:
        row["stale_process"] = True
    return row


def _runner_status_row(
    key: str,
    settings: Any,
    *,
    status_provider: StatusProvider,
    now: datetime,
) -> dict[str, Any]:
    try:
        row = dict(status_provider(key) or {})
    except Exception as exc:
        logger.exception("failed to read watchdog runner status for %s", key)
        row = {
            "key": key,
            "status": "error",
            "alive": False,
            "pid_file_status": "unknown",
            "error_message": str(exc) or exc.__class__.__name__,
        }
    return _annotate_runtime_state_health(key, row, settings, now=now)


class WatchdogEventRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchdog_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    runner_key TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_watchdog_events_key_created
                ON watchdog_events (runner_key, created_at)
                """
            )

    def record(
        self,
        *,
        runner_key: str,
        event_type: str,
        status: str,
        message: str,
        created_at: datetime,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watchdog_events (
                    runner_key, event_type, status, message, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (runner_key, event_type, status, message, _iso(created_at)),
            )

    def recent_restart_count(
        self,
        *,
        runner_key: str,
        since: datetime,
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM watchdog_events
                WHERE runner_key = ?
                  AND event_type = 'restart'
                  AND status = 'scheduled'
                  AND created_at >= ?
                """,
                (runner_key, _iso(since)),
            ).fetchone()
        return int((row or {})["count"] or 0)

    def latest_restart_at(self, *, runner_key: str) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT created_at
                FROM watchdog_events
                WHERE runner_key = ?
                  AND event_type = 'restart'
                  AND status = 'scheduled'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (runner_key,),
            ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(str(row["created_at"]))
        except ValueError:
            return None

    def status(self, *, limit: int = 20) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS count FROM watchdog_events").fetchone()
            latest = conn.execute(
                """
                SELECT runner_key, event_type, status, message, created_at
                FROM watchdog_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return {
            "db_path": str(self.path),
            "event_count": int((total or {})["count"] or 0),
            "latest_events": [dict(row) for row in latest],
        }


def run_watchdog_once(
    settings: Any,
    *,
    status_provider: StatusProvider = runner_process_status,
    restart_func: RestartFunc = restart_runner_processes,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked_at = now or _utc_now()
    store = RuntimeStateStore(settings.watchdog_state_path)
    repository = WatchdogEventRepository(settings.watchdog_db_path)
    runner_keys = _effective_runner_keys(settings)
    cooldown_sec = max(int(getattr(settings, "watchdog_cooldown_sec", 300)), 0)
    flap_window_sec = max(int(getattr(settings, "watchdog_flap_window_sec", 1800)), 1)
    max_restarts = max(int(getattr(settings, "watchdog_max_restarts_per_window", 3)), 1)

    statuses = {
        key: _runner_status_row(
            key,
            settings,
            status_provider=status_provider,
            now=checked_at,
        )
        for key in runner_keys
    }
    restart_candidates = [
        key for key, row in statuses.items() if _needs_restart(dict(row or {}))
    ]
    restart_keys: list[str] = []
    cooldown_keys: list[str] = []
    flapping_keys: list[str] = []

    for key in restart_candidates:
        latest = repository.latest_restart_at(runner_key=key)
        if latest is not None and checked_at - latest < timedelta(seconds=cooldown_sec):
            cooldown_keys.append(key)
            continue
        window_start = checked_at - timedelta(seconds=flap_window_sec)
        if (
            repository.recent_restart_count(runner_key=key, since=window_start)
            >= max_restarts
        ):
            flapping_keys.append(key)
            repository.record(
                runner_key=key,
                event_type="restart",
                status="flapping_blocked",
                message=f"blocked after {max_restarts} restarts in {flap_window_sec}s",
                created_at=checked_at,
            )
            continue
        restart_keys.append(key)

    restart_result: dict[str, Any] = {}
    restart_error_message = ""
    if bool(getattr(settings, "watchdog_enabled", True)) and restart_keys:
        try:
            restart_result = restart_func(restart_keys, delay_sec=0.5)
        except Exception as exc:
            restart_error_message = str(exc) or exc.__class__.__name__
            logger.exception("watchdog failed to schedule runner restart")
            restart_result = {
                "status": "error",
                "error_message": restart_error_message,
                "keys": restart_keys,
            }
            for key in restart_keys:
                repository.record(
                    runner_key=key,
                    event_type="restart",
                    status="failed",
                    message=restart_error_message,
                    created_at=checked_at,
                )
        else:
            for key in restart_keys:
                repository.record(
                    runner_key=key,
                    event_type="restart",
                    status="scheduled",
                    message="watchdog scheduled runner restart",
                    created_at=checked_at,
                )

    if restart_error_message:
        status = "restart_failed"
    elif restart_keys:
        status = "restarted"
    elif cooldown_keys:
        status = "cooldown"
    elif flapping_keys:
        status = "flapping"
    elif restart_candidates:
        status = "blocked"
    else:
        status = "ok"

    snapshot = {
        "service": "tradecraft-watchdog",
        "status": status,
        "checked_at": _iso(checked_at),
        "enabled": bool(getattr(settings, "watchdog_enabled", True)),
        "interval_sec": int(getattr(settings, "watchdog_interval_sec", 1800)),
        "runner_keys": runner_keys,
        "restart_candidates": restart_candidates,
        "restart_keys": restart_keys,
        "cooldown_keys": cooldown_keys,
        "flapping_keys": flapping_keys,
        "restart_result": restart_result,
        "processes": statuses,
        "events": repository.status(limit=10),
    }
    store.write_snapshot(snapshot)
    return snapshot


def _watchdog_snapshot_age_sec(
    snapshot: dict[str, Any],
    *,
    now: datetime,
) -> float | None:
    checked_at = _parse_iso_datetime(
        snapshot.get("checked_at") or snapshot.get("updated_at")
    )
    if checked_at is None:
        return None
    return max((now - checked_at).total_seconds(), 0.0)


def watchdog_status(settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    snapshot = RuntimeStateStore(settings.watchdog_state_path).read_snapshot() or {}
    events = WatchdogEventRepository(settings.watchdog_db_path).status(limit=10)
    checked_at = _parse_iso_datetime(
        snapshot.get("checked_at") or snapshot.get("updated_at")
    )
    interval_sec = max(int(getattr(settings, "watchdog_interval_sec", 1800)), 0)
    age_sec = _watchdog_snapshot_age_sec(snapshot, now=now or _utc_now())
    stale_after_sec = interval_sec * 1.5 if interval_sec else 0
    latest_stale = age_sec is None or (
        stale_after_sec > 0 and age_sec > stale_after_sec
    )
    return {
        "status": snapshot.get("status") or "missing",
        "enabled": bool(getattr(settings, "watchdog_enabled", True)),
        "interval_sec": interval_sec,
        "state_path": str(getattr(settings, "watchdog_state_path", "")),
        "db_path": str(getattr(settings, "watchdog_db_path", "")),
        "latest_source": "watchdog_state_file",
        "latest_checked_at": _iso(checked_at) if checked_at else "",
        "latest_age_sec": age_sec,
        "latest_stale": latest_stale,
        "current_status_endpoint": "/api/ops/readiness",
        "latest": snapshot,
        "events": events,
    }


def run() -> None:
    write_current_runner_pid("watchdog")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    interval = max(int(settings.watchdog_interval_sec), 60)
    try:
        while True:
            try:
                if settings.watchdog_enabled:
                    result = run_watchdog_once(settings)
                    logger.info(
                        "watchdog cycle status=%s restart_keys=%s",
                        result.get("status"),
                        result.get("restart_keys"),
                    )
                if bool(getattr(settings, "watchdog_once", False)):
                    return
            except Exception:
                logger.exception("watchdog cycle failed")
                if bool(getattr(settings, "watchdog_once", False)):
                    raise
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("watchdog runner interrupted; stopping")
