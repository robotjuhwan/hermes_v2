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
    "naver_reports",
    "crypto_market_research",
    "crypto_pattern_lab",
    "crypto_alpha",
    "watchdog",
)

ENABLED_CRITICAL_RUNNERS: tuple[tuple[str, str], ...] = (
    ("strategy_insights", "strategy_insights_enabled"),
    ("kis_block_trader", "kis_block_trader_enabled"),
    ("binance_block_trader", "binance_block_trader_enabled"),
    ("market_judge", "market_judge_enabled"),
    ("market_pulse", "market_pulse_enabled"),
    ("investment_memory", "investment_memory_enabled"),
    ("live_evaluator", "live_evaluator_enabled"),
    ("naver_reports", "naver_reports_enabled"),
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
    if not _truthy(getattr(settings, "naver_reports_enabled", False)):
        keys = [key for key in keys if key != "naver_reports"]
    for key, enabled_attr in ENABLED_CRITICAL_RUNNERS:
        if key not in keys and _truthy(getattr(settings, enabled_attr, False)):
            keys.append(key)
    if "watchdog" not in keys and _truthy(getattr(settings, "watchdog_enabled", True)):
        keys.append("watchdog")
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


def _binance_manager_run_row(
    settings: Any,
    *,
    run_id: int,
) -> dict[str, Any]:
    db_path = str(getattr(settings, "binance_block_trader_db_path", "") or "").strip()
    if not db_path:
        return {}
    path = Path(db_path)
    if not path.exists():
        return {}
    query = (
        """
        SELECT id, run_at, status, error_message
        FROM manager_runs
        WHERE id = ?
        LIMIT 1
        """
        if run_id > 0
        else """
        SELECT id, run_at, status, error_message
        FROM manager_runs
        ORDER BY id DESC
        LIMIT 1
        """
    )
    params: tuple[Any, ...] = (run_id,) if run_id > 0 else ()
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(query, params).fetchone()
    except sqlite3.Error:
        return {}
    return dict(row) if row else {}


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


def _watchdog_runner_code_paths(key: str) -> list[Path]:
    base = Path(__file__).resolve().parents[1]
    if key == "watchdog":
        return [
            base / "runtime" / "watchdog_runner.py",
            base / "runtime" / "process_status.py",
            base / "runtime" / "runner_manifest.py",
        ]
    if key == "binance_block_trader":
        return [
            base / "runtime" / "binance_block_trader_runner.py",
            base / "services" / "binance_block_trader.py",
            base / "services" / "binance_manager_prompt.py",
            base / "services" / "binance_manager_contract.py",
        ]
    return []


def _annotate_source_code_staleness(key: str, row: dict[str, Any]) -> dict[str, Any]:
    paths = _watchdog_runner_code_paths(key)
    if not paths:
        return row
    existing = [path for path in paths if path.exists()]
    if not existing:
        return row
    latest_epoch = max(path.stat().st_mtime for path in existing)
    started_epoch = row.get("started_at_epoch")
    alive = bool(row.get("direct_alive") or row.get("alive"))
    stale_paths: list[str] = []
    stale = False
    if alive and started_epoch is not None:
        try:
            start = float(started_epoch)
        except (TypeError, ValueError):
            start = 0.0
        if start > 0:
            stale_paths = [
                str(path)
                for path in existing
                if path.stat().st_mtime > start + 1.0
            ]
            stale = bool(stale_paths)
    row["code_mtime"] = datetime.fromtimestamp(
        latest_epoch,
        tz=timezone.utc,
    ).isoformat()
    row["code_mtime_epoch"] = latest_epoch
    row["stale_source_paths"] = stale_paths[:8]
    row["stale_code_process"] = stale
    if stale:
        row["stale_process"] = True
    else:
        row["stale_process"] = bool(row.get("stale_process"))
    return row


def _annotate_runtime_state_health(
    key: str,
    row: dict[str, Any],
    settings: Any,
    *,
    now: datetime,
) -> dict[str, Any]:
    if key == "naver_reports":
        state_path = str(
            getattr(settings, "naver_reports_state_path", "") or ""
        ).strip()
        if not state_path:
            return row
        snapshot = RuntimeStateStore(state_path).read_snapshot() or {}
        state_status = str(snapshot.get("status") or "").strip()
        heartbeat_at = _parse_iso_datetime(snapshot.get("heartbeat_at"))
        deadline_at = _parse_iso_datetime(snapshot.get("deadline_at"))
        heartbeat_interval_sec = max(
            float(getattr(settings, "naver_reports_heartbeat_interval_sec", 5.0)),
            0.1,
        )
        heartbeat_age_sec = (
            int((now - heartbeat_at).total_seconds()) if heartbeat_at else None
        )
        heartbeat_timeout_sec = max(int(heartbeat_interval_sec * 3), 15)
        stale_running = False
        stale_reason = ""
        if state_status == "collecting":
            if heartbeat_age_sec is None or heartbeat_age_sec > heartbeat_timeout_sec:
                stale_running = True
                stale_reason = "naver_reports_heartbeat_overdue"
            elif deadline_at is not None and now > deadline_at:
                stale_running = True
                stale_reason = "naver_reports_deadline_overdue"
        row.update(
            {
                "runtime_state_status": state_status,
                "runtime_state_updated_at": str(snapshot.get("updated_at") or ""),
                "runtime_state_age_sec": heartbeat_age_sec,
                "runtime_state_timeout_sec": heartbeat_timeout_sec,
                "runtime_state_heartbeat_at": (
                    heartbeat_at.isoformat() if heartbeat_at else ""
                ),
                "runtime_state_deadline_at": (
                    deadline_at.isoformat() if deadline_at else ""
                ),
                "stale_runtime_state": stale_running,
            }
        )
        if stale_running:
            row["stale_runtime_reason"] = stale_reason
            row["stale_process"] = True
        return row
    if key == "research":
        state_path = str(getattr(settings, "research_state_path", "") or "").strip()
        timeout_sec = max(int(getattr(settings, "naver_reports_cycle_timeout_sec", 3600)), 1)
        stale_statuses = ("_running",)
        stale_reason = "stale_running_state"
    elif key == "binance_block_trader":
        state_path = str(
            getattr(settings, "binance_block_trader_state_path", "") or ""
        ).strip()
        manager_timeout_sec = int(
            max(
                float(
                    getattr(
                        settings,
                        "binance_block_trader_llm_timeout_ms",
                        getattr(settings, "codex_runtime_timeout_ms", 0),
                    )
                    or 0
                )
                / 1000.0
                + 30.0,
                0.0,
            )
        )
        timeout_sec = max(
            int(getattr(settings, "binance_block_trader_manager_error_retry_sec", 300)),
            manager_timeout_sec,
            1,
        )
        stale_statuses = ("manager_error",)
        stale_reason = "manager_error"
    else:
        return row
    if not state_path:
        return row
    snapshot = RuntimeStateStore(state_path).read_snapshot() or {}
    state_status = str(snapshot.get("status") or "").strip()
    updated_at = _parse_iso_datetime(snapshot.get("updated_at"))
    age_sec = int((now - updated_at).total_seconds()) if updated_at else None
    stale_running = bool(
        any(
            state_status == token or state_status.endswith(token)
            for token in stale_statuses
        )
        and (age_sec is None or age_sec >= timeout_sec)
    )
    manager_result = (
        snapshot.get("manager_result")
        if isinstance(snapshot.get("manager_result"), dict)
        else {}
    )
    manager_started_at = _parse_iso_datetime(manager_result.get("started_at"))
    manager_age_sec = (
        int((now - manager_started_at).total_seconds())
        if manager_started_at is not None
        else None
    )
    last_manager = (
        snapshot.get("last_manager_result")
        if isinstance(snapshot.get("last_manager_result"), dict)
        else {}
    )
    if key == "binance_block_trader":
        current_manager_status = str(manager_result.get("status") or "").strip().lower()
        last_manager_status = str(last_manager.get("status") or "").strip().lower()
        manager_due_reason = str(snapshot.get("manager_due_reason") or "").strip()
        retry_stalled = bool(
            manager_due_reason == "retry_after_manager_error"
            and current_manager_status == "running"
            and last_manager_status == "error"
            and manager_age_sec is not None
            and manager_age_sec >= timeout_sec
        )
        if retry_stalled:
            stale_running = True
            stale_reason = "manager_error_retry_stalled"
        if not stale_running and last_manager_status == "error":
            try:
                manager_run_id = int(
                    last_manager.get("manager_run_id")
                    or last_manager.get("run_id")
                    or 0
                )
            except (TypeError, ValueError):
                manager_run_id = 0
            manager_run = _binance_manager_run_row(settings, run_id=manager_run_id)
            manager_run_status = str(manager_run.get("status") or "").strip().lower()
            manager_run_at = _parse_iso_datetime(manager_run.get("run_at"))
            manager_run_age_sec = (
                int((now - manager_run_at).total_seconds())
                if manager_run_at is not None
                else None
            )
            if manager_run.get("id") is not None:
                row["runtime_state_manager_run_id"] = int(manager_run["id"])
            if manager_run_age_sec is not None:
                row["runtime_state_manager_age_sec"] = manager_run_age_sec
            manager_run_error = str(manager_run.get("error_message") or "").strip()
            if manager_run_error:
                row["runtime_state_error_message"] = manager_run_error[:240]
            if (
                manager_run_status == "error"
                and manager_run_age_sec is not None
                and manager_run_age_sec >= timeout_sec
            ):
                stale_running = True
                stale_reason = "manager_error_db_stale"
    row["runtime_state_status"] = state_status
    row["runtime_state_updated_at"] = updated_at.isoformat() if updated_at else ""
    row["runtime_state_age_sec"] = age_sec
    row["runtime_state_timeout_sec"] = timeout_sec
    if manager_age_sec is not None:
        row["runtime_state_manager_age_sec"] = manager_age_sec
    if stale_running:
        row["stale_runtime_reason"] = stale_reason
    if isinstance(last_manager, dict):
        error = str(last_manager.get("error_message") or "").strip()
        if error:
            row["runtime_state_error_message"] = error[:240]
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
    row = _annotate_runtime_state_health(key, row, settings, now=now)
    return _annotate_source_code_staleness(key, row)


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
    deferred_restart_keys = restart_candidates[1:]

    for key in restart_candidates[:1]:
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
        "deferred_restart_keys": deferred_restart_keys,
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


def watchdog_status(
    settings: Any,
    *,
    now: datetime | None = None,
    status_provider: StatusProvider = runner_process_status,
) -> dict[str, Any]:
    snapshot = RuntimeStateStore(settings.watchdog_state_path).read_snapshot() or {}
    events = WatchdogEventRepository(settings.watchdog_db_path).status(limit=10)
    checked_at = _parse_iso_datetime(
        snapshot.get("checked_at") or snapshot.get("updated_at")
    )
    checked_now = now or _utc_now()
    interval_sec = max(int(getattr(settings, "watchdog_interval_sec", 1800)), 0)
    age_sec = _watchdog_snapshot_age_sec(snapshot, now=checked_now)
    stale_after_sec = interval_sec * 1.5 if interval_sec else 0
    latest_stale = age_sec is None or (
        stale_after_sec > 0 and age_sec > stale_after_sec
    )
    runner_keys = _effective_runner_keys(settings)
    current_processes = {
        key: _runner_status_row(
            key,
            settings,
            status_provider=status_provider,
            now=checked_now,
        )
        for key in runner_keys
    }
    current_restart_candidates = [
        key
        for key, row in current_processes.items()
        if _needs_restart(dict(row or {}))
    ]
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
        "current_runner_keys": runner_keys,
        "current_restart_candidates": current_restart_candidates,
        "current_processes": current_processes,
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
