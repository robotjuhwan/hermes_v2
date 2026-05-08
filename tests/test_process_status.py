from __future__ import annotations

import os
from types import SimpleNamespace

from tradecraft.runtime import process_status


def test_runner_pid_file_write_and_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)

    path = process_status.write_current_runner_pid("runtime")

    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())

    process_status.clear_current_runner_pid("runtime")

    assert not path.exists()


def test_runner_process_status_marks_dead_pid_file_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)
    path = process_status.runner_pid_path("runtime")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("999999999\n", encoding="utf-8")

    status = process_status.runner_process_status("runtime", include_matches=False)

    assert status["alive"] is False
    assert status["pid_file_pid"] == 999999999
    assert status["pid_file_status"] == "stale"


def test_control_status_can_match_current_process_without_pid_file(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)
    monkeypatch.setattr(process_status.os, "getpid", lambda: 4242)

    def fake_run(
        cmd: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> SimpleNamespace:
        _ = (capture_output, text, check)
        if cmd == ["ps", "-axo", "pid=,command="]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "4242 .venv/bin/python -m uvicorn tradecraft.main:app "
                    "--host 127.0.0.1 --port 18080\n"
                ),
            )
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(process_status.subprocess, "run", fake_run)

    status = process_status.runner_process_status("control")

    assert status["alive"] is True
    assert status["pid"] == 4242
    assert status["pid_file_status"] == "missing"
