from __future__ import annotations

import json
import os
import signal
from pathlib import Path

from tradecraft.services.freqtrade import FreqtradeBotConfig
from tradecraft.services.freqtrade_process import (
    FreqtradeProcessManager,
    FreqtradeProcessManagerConfig,
)


def _build_manager(tmp_path: Path) -> FreqtradeProcessManager:
    executable = tmp_path / "freqtrade"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    config_path = tmp_path / "spot.json"
    config_path.write_text(json.dumps({"available_capital": 100.0}), encoding="utf-8")
    runtime_dir = tmp_path / "runtime"

    return FreqtradeProcessManager(
        FreqtradeProcessManagerConfig(
            executable_path=str(executable),
            workdir=str(tmp_path),
            runtime_dir=str(runtime_dir),
            stop_timeout_sec=0.2,
        ),
        bots=[
            FreqtradeBotConfig(
                bot_id="spot",
                label="Spot",
                config_path=str(config_path),
            )
        ],
    )


def test_freqtrade_process_manager_start_and_status(
    monkeypatch, tmp_path: Path
) -> None:
    manager = _build_manager(tmp_path)

    class DummyProc:
        pid = 43210

    calls: list[list[str]] = []

    def fake_popen(*args, **kwargs):
        calls.append(list(args[0]))
        _ = kwargs
        return DummyProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(manager, "_is_pid_alive", lambda pid: pid == 43210)

    action = manager.start("spot")
    assert action["action"] == "started"
    assert action["pid"] == 43210

    status = manager.list_statuses()[0]
    assert status["running"] is True
    assert status["pid"] == 43210
    assert status["usdt_limit"] == 100.0
    assert calls
    assert ".override.json" in " ".join(calls[0])


def test_freqtrade_process_manager_set_usdt_limit_override(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)

    action = manager.set_usdt_limit("spot", 250.0)
    assert action["action"] == "usdt_limit_updated"
    assert action["usdt_limit"] == 250.0
    assert action["usdt_limit_source"] == "override"

    limits_path = tmp_path / "runtime" / "usdt_limits.json"
    payload = json.loads(limits_path.read_text(encoding="utf-8"))
    assert payload["spot"] == 250.0


def test_freqtrade_process_manager_stop_running_process(
    monkeypatch, tmp_path: Path
) -> None:
    manager = _build_manager(tmp_path)
    pid_path = tmp_path / "runtime" / "spot.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("24680", encoding="utf-8")

    alive = {"value": True}

    def fake_alive(pid: int) -> bool:
        _ = pid
        return alive["value"]

    calls: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        _ = pid
        calls.append(sig)
        if sig == signal.SIGTERM:
            alive["value"] = False

    monkeypatch.setattr(manager, "_is_pid_alive", fake_alive)
    monkeypatch.setattr(os, "kill", fake_kill)

    action = manager.stop("spot")
    assert action["action"] == "stopped"
    assert action["forced"] is False
    assert signal.SIGTERM in calls
    assert not pid_path.exists()


def test_freqtrade_process_manager_clears_stale_pid(
    monkeypatch, tmp_path: Path
) -> None:
    manager = _build_manager(tmp_path)
    pid_path = tmp_path / "runtime" / "spot.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("99999", encoding="utf-8")

    monkeypatch.setattr(manager, "_is_pid_alive", lambda pid: False)

    status = manager.list_statuses()[0]
    assert status["running"] is False
    assert status["pid"] is None
    assert not pid_path.exists()
