from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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

    assert calls == [["runtime", "research", "market_judge"]]
    assert result["status"] == "cooldown"
    assert result["restart_keys"] == []
    assert sorted(result["cooldown_keys"]) == ["market_judge", "research", "runtime"]


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
