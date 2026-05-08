from __future__ import annotations

import atexit
import os
import re
import subprocess
from pathlib import Path
from typing import Any

RUNNER_PID_DIR = Path(".runtime/pids")

RUNNER_PID_FILES: dict[str, str] = {
    "control": "tradecraft-control.pid",
    "runtime": "tradecraft-runtime.pid",
    "intelligence": "tradecraft-intelligence.pid",
    "research": "tradecraft-research.pid",
    "kis_trader": "tradecraft-kis-trader.pid",
    "kis_block_trader": "tradecraft-kis-block-trader.pid",
    "investment_memory": "tradecraft-investment-memory.pid",
    "naver_reports": "tradecraft-naver-reports.pid",
    "strategy_insights": "tradecraft-strategy-insights.pid",
    "market_judge": "tradecraft-market-judge.pid",
}

RUNNER_PATTERNS: dict[str, str] = {
    "control": (
        r"tradecraft-control|tradecraft\.main:app|"
        r"uvicorn .*tradecraft\.main:app"
    ),
    "runtime": (
        r"tradecraft-runtime|tradecraft\.runtime\.runner|runtime/runner\.py"
    ),
    "intelligence": (
        r"tradecraft-intelligence|tradecraft\.runtime\.intelligence_runner|"
        r"intelligence_runner\.py"
    ),
    "research": (
        r"tradecraft-research|tradecraft\.runtime\.research_runner|"
        r"research_runner\.py"
    ),
    "kis_trader": (
        r"tradecraft-kis-trader|tradecraft\.runtime\.kis_trader_runner|"
        r"kis_trader_runner\.py"
    ),
    "kis_block_trader": (
        r"tradecraft-kis-block-trader|"
        r"tradecraft\.runtime\.kis_block_trader_runner|"
        r"kis_block_trader_runner\.py"
    ),
    "investment_memory": (
        r"tradecraft-investment-memory|"
        r"tradecraft\.runtime\.investment_memory_runner|"
        r"investment_memory_runner\.py"
    ),
    "naver_reports": (
        r"tradecraft-naver-reports|tradecraft\.runtime\.naver_reports_runner|"
        r"naver_reports_runner\.py"
    ),
    "strategy_insights": (
        r"tradecraft-strategy-insights|"
        r"tradecraft\.runtime\.strategy_insights_runner|"
        r"strategy_insights_runner\.py"
    ),
    "market_judge": (
        r"tradecraft-market-judge|tradecraft\.runtime\.market_judge_runner|"
        r"market_judge_runner\.py"
    ),
}

RUNNER_LABELS: dict[str, str] = {
    "control": "control API",
    "runtime": "runtime runner",
    "intelligence": "intelligence runner",
    "research": "research runner",
    "kis_trader": "KIS trader runner",
    "kis_block_trader": "KIS block trader runner",
    "investment_memory": "investment memory runner",
    "naver_reports": "reports crawler",
    "strategy_insights": "strategy insight runner",
    "market_judge": "market judge runner",
}

_REGISTERED_CLEANUPS: set[tuple[str, int]] = set()


def runner_pid_path(key: str) -> Path:
    filename = RUNNER_PID_FILES.get(key)
    if not filename:
        raise ValueError(f"unknown runner key: {key}")
    return RUNNER_PID_DIR / filename


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _command_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return str(proc.stdout or "").strip()


def _matches(pattern: str, command: str) -> bool:
    try:
        return bool(re.search(pattern, command))
    except re.error:
        return False


def _is_launcher_wrapper(command: str) -> bool:
    prefixes = (
        "tmux new-session ",
        "zsh -c ",
        "/bin/zsh -lc ",
        "bash -lc ",
        "/bin/bash -lc ",
    )
    return command.startswith(prefixes)


def list_matching_processes(
    pattern: str,
    *,
    include_current: bool = False,
) -> list[dict[str, Any]]:
    query = str(pattern or "").strip()
    if not query:
        return []
    try:
        regex = re.compile(query)
    except re.error:
        return []
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []

    current_pid = os.getpid()
    matches: list[dict[str, Any]] = []
    for raw_line in str(proc.stdout or "").splitlines():
        parts = raw_line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == current_pid and not include_current:
            continue
        command = parts[1].strip()
        if not command:
            continue
        if _is_launcher_wrapper(command):
            continue
        if regex.search(command):
            matches.append({"pid": pid, "command": command[:500]})
    return matches


def write_current_runner_pid(key: str) -> Path:
    path = runner_pid_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    path.write_text(f"{pid}\n", encoding="utf-8")

    cleanup_key = (str(path), pid)
    if cleanup_key not in _REGISTERED_CLEANUPS:
        atexit.register(clear_current_runner_pid, key, pid)
        _REGISTERED_CLEANUPS.add(cleanup_key)
    return path


def clear_current_runner_pid(key: str, pid: int | None = None) -> None:
    path = runner_pid_path(key)
    expected_pid = os.getpid() if pid is None else int(pid)
    if _read_pid(path) != expected_pid:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def runner_process_status(
    key: str,
    *,
    include_matches: bool = True,
) -> dict[str, Any]:
    pattern = RUNNER_PATTERNS.get(key, "")
    pid_path = runner_pid_path(key)
    pid_file_pid = _read_pid(pid_path)
    pid_file_alive = _pid_is_alive(pid_file_pid) if pid_file_pid else False
    pid_file_command = _command_for_pid(pid_file_pid) if pid_file_alive else ""
    pid_file_matches = bool(
        pid_file_pid and pid_file_alive and _matches(pattern, pid_file_command)
    )
    matches = (
        list_matching_processes(pattern, include_current=key == "control")
        if include_matches
        else []
    )

    if pid_file_matches:
        matches = [row for row in matches if row.get("pid") != pid_file_pid]
        matches.insert(
            0,
            {"pid": pid_file_pid, "command": pid_file_command[:500]},
        )

    alive = bool(matches) or pid_file_matches
    pid_file_status = "missing"
    if pid_file_pid:
        if not pid_file_alive:
            pid_file_status = "stale"
        elif pid_file_matches:
            pid_file_status = "ok"
        else:
            pid_file_status = "mismatch"

    return {
        "key": key,
        "label": RUNNER_LABELS.get(key, key),
        "status": "running" if alive else "stopped",
        "alive": alive,
        "pid": int(matches[0]["pid"]) if matches else None,
        "pid_file": str(pid_path),
        "pid_file_pid": pid_file_pid,
        "pid_file_status": pid_file_status,
        "matched_count": len(matches),
        "matches": matches[:5],
    }
