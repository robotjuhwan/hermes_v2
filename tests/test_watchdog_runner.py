from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tradecraft.runtime import watchdog_runner
from tradecraft.runtime.watchdog_runner import run_watchdog_once, watchdog_status


class WatchdogSettings:
    watchdog_enabled = True
    watchdog_db_path = ""
    watchdog_state_path = ""
    watchdog_runner_keys = "runtime,research,market_judge"
    watchdog_cooldown_sec = 300
    watchdog_flap_window_sec = 1800
    watchdog_max_restarts_per_window = 3
    watchdog_interval_sec = 1800
    research_enabled = True
    research_state_path = ""
    naver_reports_cycle_timeout_sec = 3600


def test_watchdog_marks_stale_naver_report_heartbeat_for_restart(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "naver_reports.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "collecting",
                "heartbeat_at": "2026-07-10T00:00:00+00:00",
                "deadline_at": "2026-07-10T00:30:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        naver_reports_state_path=str(state_path),
        naver_reports_cycle_timeout_sec=1800,
        naver_reports_heartbeat_interval_sec=5.0,
    )

    row = watchdog_runner._annotate_runtime_state_health(
        "naver_reports",
        {"alive": True, "direct_alive": True},
        settings,
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )

    assert row["stale_runtime_reason"] == "naver_reports_heartbeat_overdue"
    assert row["stale_runtime_state"] is True
    assert row["stale_process"] is True


def test_watchdog_restarts_stopped_allowlisted_runner(tmp_path: Path) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "stopped" if key == "research" else "running",
            "alive": key != "research",
            "pid_file_status": "stale" if key == "research" else "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["research"]
    assert calls == [(["research"], 0.5)]
    assert Path(settings.watchdog_state_path).exists()


def test_watchdog_includes_enabled_critical_runner_when_config_omits_it(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "runtime,research"
    settings.crypto_pattern_lab_enabled = True
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "stopped" if key == "crypto_pattern_lab" else "running",
            "alive": key != "crypto_pattern_lab",
            "pid_file_status": "missing" if key == "crypto_pattern_lab" else "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert "crypto_pattern_lab" in result["runner_keys"]
    assert result["restart_keys"] == ["crypto_pattern_lab"]
    assert calls == [(["crypto_pattern_lab"], 0.5)]


def test_watchdog_keeps_self_in_runner_keys_when_config_omits_it(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "binance_block_trader"

    result = run_watchdog_once(
        settings,
        status_provider=lambda key: {
            "key": key,
            "status": "running",
            "alive": True,
            "direct_alive": True,
            "pid_file_status": "ok",
        },
        restart_func=lambda keys, *, delay_sec=0.5: {},
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert "watchdog" in result["runner_keys"]


def test_watchdog_skips_disabled_legacy_research_runner(tmp_path: Path) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = ""
    settings.research_enabled = False
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "stopped" if key == "research" else "running",
            "alive": key != "research",
            "pid_file_status": "missing" if key == "research" else "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert "research" not in result["runner_keys"]
    assert result["restart_keys"] == []
    assert calls == []


def test_watchdog_restarts_alive_stale_runner(tmp_path: Path) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
            "stale_process": key == "market_judge",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["market_judge"]
    assert calls == [(["market_judge"], 0.5)]


def test_watchdog_schedules_only_one_restart_candidate_per_check(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "runtime,market_judge"
    calls: list[list[str]] = []

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        _ = delay_sec
        calls.append(keys)
        return {"status": "scheduled", "keys": keys}

    result = run_watchdog_once(
        settings,
        status_provider=lambda key: {
            "key": key,
            "status": "stopped",
            "alive": False,
            "pid_file_status": "stale",
        },
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert calls == [["runtime"]]
    assert result["restart_keys"] == ["runtime"]
    assert result["deferred_restart_keys"] == ["market_judge", "watchdog"]


def test_watchdog_restarts_binance_runner_when_source_changed_after_start(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "binance_block_trader"
    code = tmp_path / "binance_manager_prompt.py"
    code.write_text("# patched contract\n", encoding="utf-8")
    code_mtime = datetime(2026, 6, 7, 5, 5, tzinfo=timezone.utc).timestamp()
    os.utime(code, (code_mtime, code_mtime))
    monkeypatch.setattr(
        watchdog_runner,
        "_watchdog_runner_code_paths",
        lambda key: [code] if key == "binance_block_trader" else [],
    )
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "direct_alive": True,
            "pid_file_status": "ok",
            "started_at_epoch": datetime(
                2026,
                6,
                7,
                5,
                0,
                tzinfo=timezone.utc,
            ).timestamp(),
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 10, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["binance_block_trader"]
    assert result["processes"]["binance_block_trader"]["stale_process"] is True
    assert calls == [(["binance_block_trader"], 0.5)]


def test_watchdog_restarts_self_when_source_changed_after_start(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "watchdog"
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "direct_alive": True,
            "pid_file_status": "ok",
            "started_at_epoch": datetime(
                2026,
                1,
                1,
                0,
                0,
                tzinfo=timezone.utc,
            ).timestamp(),
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 10, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["watchdog"]
    assert result["processes"]["watchdog"]["stale_code_process"] is True
    assert calls == [(["watchdog"], 0.5)]


def test_watchdog_status_exposes_current_stale_code_diagnostics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "binance_block_trader"
    code = tmp_path / "binance_block_trader.py"
    code.write_text("# current source\n", encoding="utf-8")
    code_mtime = datetime(2026, 6, 7, 5, 5, tzinfo=timezone.utc).timestamp()
    os.utime(code, (code_mtime, code_mtime))
    Path(settings.watchdog_state_path).write_text(
        '{"status":"ok","checked_at":"2026-06-07T05:10:00+00:00",'
        '"processes":{"binance_block_trader":{"status":"running","alive":true}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        watchdog_runner,
        "_watchdog_runner_code_paths",
        lambda key: [code] if key == "binance_block_trader" else [],
    )

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "direct_alive": True,
            "pid_file_status": "ok",
            "started_at_epoch": datetime(
                2026,
                6,
                7,
                5,
                0,
                tzinfo=timezone.utc,
            ).timestamp(),
        }

    status = watchdog_status(
        settings,
        status_provider=status_provider,
        now=datetime(2026, 6, 7, 5, 12, tzinfo=timezone.utc),
    )

    current = status["current_processes"]["binance_block_trader"]
    assert current["stale_code_process"] is True
    assert current["stale_process"] is True
    assert status["current_restart_candidates"] == ["binance_block_trader"]
    assert status["latest"]["processes"]["binance_block_trader"]["status"] == "running"


def test_watchdog_preserves_per_runner_status_provider_failure(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        if key == "market_judge":
            raise RuntimeError("process scan failed")
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["market_judge"]
    assert calls == [(["market_judge"], 0.5)]
    assert result["processes"]["market_judge"]["status"] == "error"
    assert result["processes"]["market_judge"]["alive"] is False
    assert result["processes"]["market_judge"]["error_message"] == "process scan failed"


def test_watchdog_preserves_restart_function_failure(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "stopped" if key == "market_judge" else "running",
            "alive": key != "market_judge",
            "pid_file_status": "missing" if key == "market_judge" else "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        _ = (keys, delay_sec)
        raise RuntimeError("supervisor spawn failed")

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restart_failed"
    assert result["restart_keys"] == ["market_judge"]
    assert result["restart_result"] == {
        "status": "error",
        "error_message": "supervisor spawn failed",
        "keys": ["market_judge"],
    }
    assert Path(settings.watchdog_state_path).exists()
    event = result["events"]["latest_events"][0]
    assert event["runner_key"] == "market_judge"
    assert event["status"] == "failed"
    assert event["message"] == "supervisor spawn failed"


def test_watchdog_restarts_research_with_stale_running_state(tmp_path: Path) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.research_state_path = str(tmp_path / "research.json")
    Path(settings.research_state_path).write_text(
        '{"updated_at":"2026-06-07T03:30:00+00:00",'
        '"status":"report_collection_running"}',
        encoding="utf-8",
    )
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["research"]
    assert result["processes"]["research"]["stale_runtime_state"] is True
    assert calls == [(["research"], 0.5)]


def test_watchdog_restarts_binance_after_stale_manager_error_state(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "binance_block_trader"
    settings.binance_block_trader_state_path = str(tmp_path / "binance.json")
    settings.binance_block_trader_manager_error_retry_sec = 300
    Path(settings.binance_block_trader_state_path).write_text(
        '{"updated_at":"2026-06-07T04:50:00+00:00",'
        '"status":"manager_error",'
        '"last_manager_result":{"status":"error",'
        '"error_message":"validation_repair_resolution_missing_from_model"}}',
        encoding="utf-8",
    )
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["binance_block_trader"]
    binance = result["processes"]["binance_block_trader"]
    assert binance["runtime_state_status"] == "manager_error"
    assert binance["stale_runtime_state"] is True
    assert binance["stale_runtime_reason"] == "manager_error"
    assert calls == [(["binance_block_trader"], 0.5)]


def test_watchdog_allows_recent_binance_manager_error_retry_window(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "binance_block_trader"
    settings.binance_block_trader_state_path = str(tmp_path / "binance.json")
    settings.binance_block_trader_manager_error_retry_sec = 300
    Path(settings.binance_block_trader_state_path).write_text(
        '{"updated_at":"2026-06-07T04:58:00+00:00",'
        '"status":"manager_error",'
        '"last_manager_result":{"status":"error","error_message":"temporary"}}',
        encoding="utf-8",
    )
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    assert result["restart_keys"] == []
    binance = result["processes"]["binance_block_trader"]
    assert binance["runtime_state_status"] == "manager_error"
    assert binance["stale_runtime_state"] is False
    assert calls == []


def test_watchdog_restarts_legacy_binance_state_with_stalled_error_retry(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "binance_block_trader"
    settings.binance_block_trader_state_path = str(tmp_path / "binance.json")
    settings.binance_block_trader_manager_error_retry_sec = 300
    Path(settings.binance_block_trader_state_path).write_text(
        '{"updated_at":"2026-06-07T04:59:50+00:00",'
        '"status":"ok",'
        '"manager_due_reason":"retry_after_manager_error",'
        '"manager_result":{"status":"running",'
        '"started_at":"2026-06-07T04:50:00+00:00"},'
        '"last_manager_result":{"status":"error",'
        '"error_message":"validation_repair_resolution_missing_from_model"}}',
        encoding="utf-8",
    )
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["binance_block_trader"]
    binance = result["processes"]["binance_block_trader"]
    assert binance["runtime_state_status"] == "ok"
    assert binance["stale_runtime_state"] is True
    assert binance["stale_runtime_reason"] == "manager_error_retry_stalled"
    assert binance["runtime_state_manager_age_sec"] == 600
    assert calls == [(["binance_block_trader"], 0.5)]


def test_watchdog_restarts_legacy_binance_state_from_stale_manager_run_db(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    settings.watchdog_runner_keys = "binance_block_trader"
    settings.binance_block_trader_state_path = str(tmp_path / "binance.json")
    settings.binance_block_trader_db_path = str(tmp_path / "binance_blocks.db")
    settings.binance_block_trader_manager_error_retry_sec = 300
    Path(settings.binance_block_trader_state_path).write_text(
        '{"updated_at":"2026-06-07T04:59:50+00:00",'
        '"status":"ok",'
        '"manager_result":null,'
        '"last_manager_result":{"status":"error",'
        '"manager_run_id":77,'
        '"error_message":"validation_repair_resolution_missing_from_model"}}',
        encoding="utf-8",
    )
    with sqlite3.connect(settings.binance_block_trader_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE manager_runs (
                id INTEGER PRIMARY KEY,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manager_runs (id, run_at, status, error_message)
            VALUES (77, '2026-06-07T04:50:00+00:00', 'error', ?)
            """,
            ("validation_repair_resolution_missing_from_model",),
        )
    calls: list[tuple[list[str], float]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append((keys, delay_sec))
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "restarted"
    assert result["restart_keys"] == ["binance_block_trader"]
    binance = result["processes"]["binance_block_trader"]
    assert binance["runtime_state_status"] == "ok"
    assert binance["stale_runtime_state"] is True
    assert binance["stale_runtime_reason"] == "manager_error_db_stale"
    assert binance["runtime_state_manager_run_id"] == 77
    assert binance["runtime_state_manager_age_sec"] == 600
    assert calls == [(["binance_block_trader"], 0.5)]


def test_watchdog_respects_per_runner_cooldown(tmp_path: Path) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")
    calls: list[list[str]] = []

    def status_provider(key: str) -> dict:
        return {
            "key": key,
            "status": "stopped",
            "alive": False,
            "pid_file_status": "stale",
        }

    def restart_func(keys: list[str], *, delay_sec: float = 0.5) -> dict:
        calls.append(keys)
        return {"status": "scheduled", "keys": keys, "supervisor_pid": 1234}

    run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )
    result = run_watchdog_once(
        settings,
        status_provider=status_provider,
        restart_func=restart_func,
        now=datetime(2026, 6, 7, 5, 1, tzinfo=timezone.utc),
    )

    assert calls == [["runtime"]]
    assert result["status"] == "cooldown"
    assert result["restart_keys"] == []
    assert result["cooldown_keys"] == ["runtime"]
    assert result["deferred_restart_keys"] == [
        "research",
        "market_judge",
        "watchdog",
    ]


def test_watchdog_status_exposes_snapshot_age_and_current_status_hint(
    tmp_path: Path,
) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")

    run_watchdog_once(
        settings,
        status_provider=lambda key: {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        },
        restart_func=lambda keys, *, delay_sec=0.5: {},
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )

    status = watchdog_status(
        settings,
        now=datetime(2026, 6, 7, 5, 10, tzinfo=timezone.utc),
    )

    assert status["status"] == "ok"
    assert status["latest_source"] == "watchdog_state_file"
    assert status["latest_checked_at"] == "2026-06-07T05:00:00+00:00"
    assert status["latest_age_sec"] == 600.0
    assert status["latest_stale"] is False
    assert status["current_status_endpoint"] == "/api/ops/readiness"


def test_watchdog_status_marks_missing_or_old_snapshot_stale(tmp_path: Path) -> None:
    settings = WatchdogSettings()
    settings.watchdog_db_path = str(tmp_path / "watchdog_events.db")
    settings.watchdog_state_path = str(tmp_path / "watchdog.json")

    missing = watchdog_status(
        settings,
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )
    assert missing["status"] == "missing"
    assert missing["latest_age_sec"] is None
    assert missing["latest_stale"] is True

    run_watchdog_once(
        settings,
        status_provider=lambda key: {
            "key": key,
            "status": "running",
            "alive": True,
            "pid_file_status": "ok",
        },
        restart_func=lambda keys, *, delay_sec=0.5: {},
        now=datetime(2026, 6, 7, 5, 0, tzinfo=timezone.utc),
    )
    old = watchdog_status(
        settings,
        now=datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc),
    )
    assert old["latest_age_sec"] == 3600.0
    assert old["latest_stale"] is True
