from __future__ import annotations

import atexit
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

RUNNER_PID_DIR = Path(".runtime/pids")

RUNNER_PID_FILES: dict[str, str] = {
    "control": "tradecraft-control.pid",
    "runtime": "tradecraft-runtime.pid",
    "intelligence": "tradecraft-intelligence.pid",
    "research": "tradecraft-research.pid",
    "kis_block_trader": "tradecraft-kis-block-trader.pid",
    "binance_block_trader": "tradecraft-binance-block-trader.pid",
    "crypto_market_research": "tradecraft-crypto-market-research.pid",
    "crypto_pattern_lab": "tradecraft-crypto-pattern-lab.pid",
    "crypto_alpha": "tradecraft-crypto-alpha.pid",
    "jue_wiki": "tradecraft-jue-wiki.pid",
    "investment_memory": "tradecraft-investment-memory.pid",
    "live_evaluator": "tradecraft-live-evaluator.pid",
    "naver_reports": "tradecraft-naver-reports.pid",
    "strategy_insights": "tradecraft-strategy-insights.pid",
    "market_judge": "tradecraft-market-judge.pid",
    "market_pulse": "tradecraft-market-pulse.pid",
    "watchdog": "tradecraft-watchdog.pid",
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
    "kis_block_trader": (
        r"tradecraft-kis-block-trader|"
        r"tradecraft\.runtime\.kis_block_trader_runner|"
        r"kis_block_trader_runner\.py"
    ),
    "binance_block_trader": (
        r"tradecraft-binance-block-trader|"
        r"tradecraft\.runtime\.binance_block_trader_runner|"
        r"binance_block_trader_runner\.py"
    ),
    "crypto_market_research": (
        r"tradecraft-crypto-market-research|"
        r"tradecraft\.runtime\.crypto_market_research_runner|"
        r"crypto_market_research_runner\.py"
    ),
    "crypto_pattern_lab": (
        r"tradecraft-crypto-pattern-lab|"
        r"tradecraft\.runtime\.crypto_pattern_lab_runner|"
        r"crypto_pattern_lab_runner\.py"
    ),
    "crypto_alpha": (
        r"tradecraft-crypto-alpha|tradecraft\.runtime\.crypto_alpha_runner|"
        r"crypto_alpha_runner\.py"
    ),
    "jue_wiki": (
        r"tradecraft-jue-wiki|tradecraft\.runtime\.jue_wiki_runner|"
        r"jue_wiki_runner\.py"
    ),
    "investment_memory": (
        r"tradecraft-investment-memory|"
        r"tradecraft\.runtime\.investment_memory_runner|"
        r"investment_memory_runner\.py"
    ),
    "live_evaluator": (
        r"tradecraft-live-evaluator|"
        r"tradecraft\.runtime\.live_evaluator_runner|"
        r"live_evaluator_runner\.py"
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
    "market_pulse": (
        r"tradecraft-market-pulse|tradecraft\.runtime\.market_pulse_runner|"
        r"market_pulse_runner\.py"
    ),
    "watchdog": (
        r"tradecraft-watchdog|tradecraft\.runtime\.watchdog_runner|"
        r"watchdog_runner\.py"
    ),
}

RUNNER_LABELS: dict[str, str] = {
    "control": "control API",
    "runtime": "runtime runner",
    "intelligence": "intelligence runner",
    "research": "research runner",
    "kis_block_trader": "KIS block trader runner",
    "binance_block_trader": "Binance block trader runner",
    "crypto_market_research": "crypto market research runner",
    "crypto_pattern_lab": "crypto pattern lab runner",
    "crypto_alpha": "crypto alpha runner",
    "jue_wiki": "Jue wiki runner",
    "investment_memory": "investment memory runner",
    "live_evaluator": "live evaluator runner",
    "naver_reports": "reports crawler",
    "strategy_insights": "strategy insight runner",
    "market_judge": "market judge runner",
    "market_pulse": "market pulse runner",
    "watchdog": "watchdog runner",
}

_REGISTERED_CLEANUPS: set[tuple[str, int]] = set()


@dataclass(frozen=True)
class RunnerRestartSpec:
    key: str
    session_names: tuple[str, ...]
    command: str
    log_path: str

    @property
    def primary_session(self) -> str:
        return self.session_names[0]


RUNNER_RESTART_SPECS: dict[str, RunnerRestartSpec] = {
    "control": RunnerRestartSpec(
        key="control",
        session_names=("tradecraft-control", "hermes-control"),
        command=".venv/bin/tradecraft-control --host 127.0.0.1 --port 18080",
        log_path=".runtime/control.log",
    ),
    "runtime": RunnerRestartSpec(
        key="runtime",
        session_names=("tradecraft-runtime", "hermes-runtime"),
        command=".venv/bin/tradecraft-runtime",
        log_path=".runtime/logs/runtime.log",
    ),
    "intelligence": RunnerRestartSpec(
        key="intelligence",
        session_names=("tradecraft-intelligence", "hermes-intelligence"),
        command=".venv/bin/tradecraft-intelligence",
        log_path=".runtime/tradecraft-intelligence.log",
    ),
    "research": RunnerRestartSpec(
        key="research",
        session_names=("tradecraft-research", "hermes-research"),
        command=".venv/bin/tradecraft-research",
        log_path=".runtime/tradecraft-research.log",
    ),
    "kis_block_trader": RunnerRestartSpec(
        key="kis_block_trader",
        session_names=("tradecraft-kis-block-trader", "hermes-kis-block-trader"),
        command=".venv/bin/tradecraft-kis-block-trader",
        log_path=".runtime/kis_block_trader.log",
    ),
    "binance_block_trader": RunnerRestartSpec(
        key="binance_block_trader",
        session_names=("tradecraft-binance-block-trader", "hermes-binance-block-trader"),
        command=".venv/bin/tradecraft-binance-block-trader",
        log_path=".runtime/binance_block_trader.log",
    ),
    "crypto_market_research": RunnerRestartSpec(
        key="crypto_market_research",
        session_names=(
            "tradecraft-crypto-market-research",
            "hermes-crypto-market-research",
        ),
        command=".venv/bin/tradecraft-crypto-market-research",
        log_path=".runtime/crypto_market_research.log",
    ),
    "crypto_pattern_lab": RunnerRestartSpec(
        key="crypto_pattern_lab",
        session_names=("tradecraft-crypto-pattern-lab", "hermes-crypto-pattern-lab"),
        command=".venv/bin/tradecraft-crypto-pattern-lab",
        log_path=".runtime/crypto_pattern_lab.log",
    ),
    "crypto_alpha": RunnerRestartSpec(
        key="crypto_alpha",
        session_names=("tradecraft-crypto-alpha", "hermes-crypto-alpha"),
        command=".venv/bin/tradecraft-crypto-alpha",
        log_path=".runtime/crypto_alpha.log",
    ),
    "jue_wiki": RunnerRestartSpec(
        key="jue_wiki",
        session_names=("tradecraft-jue-wiki", "hermes-jue-wiki"),
        command=".venv/bin/tradecraft-jue-wiki",
        log_path=".runtime/jue_wiki_runner.log",
    ),
    "investment_memory": RunnerRestartSpec(
        key="investment_memory",
        session_names=("tradecraft-investment-memory", "hermes-investment-memory"),
        command=".venv/bin/tradecraft-investment-memory",
        log_path=".runtime/investment_memory.log",
    ),
    "live_evaluator": RunnerRestartSpec(
        key="live_evaluator",
        session_names=("tradecraft-live-evaluator", "hermes-live-evaluator"),
        command=".venv/bin/tradecraft-live-evaluator",
        log_path=".runtime/live_evaluator.log",
    ),
    "naver_reports": RunnerRestartSpec(
        key="naver_reports",
        session_names=("tradecraft-naver-reports", "hermes-naver-reports"),
        command=".venv/bin/tradecraft-naver-reports",
        log_path=".runtime/naver_reports.log",
    ),
    "market_judge": RunnerRestartSpec(
        key="market_judge",
        session_names=("tradecraft-market-judge", "hermes-market-judge"),
        command=".venv/bin/tradecraft-market-judge",
        log_path=".runtime/market_judge.log",
    ),
    "market_pulse": RunnerRestartSpec(
        key="market_pulse",
        session_names=("tradecraft-market-pulse", "hermes-market-pulse"),
        command=".venv/bin/tradecraft-market-pulse",
        log_path=".runtime/market_pulse.log",
    ),
    "strategy_insights": RunnerRestartSpec(
        key="strategy_insights",
        session_names=("tradecraft-strategy-insights", "hermes-strategy-insights"),
        command=".venv/bin/tradecraft-strategy-insights",
        log_path=".runtime/strategy_insights.log",
    ),
    "watchdog": RunnerRestartSpec(
        key="watchdog",
        session_names=("tradecraft-watchdog", "hermes-watchdog"),
        command=".venv/bin/tradecraft-watchdog",
        log_path=".runtime/watchdog.log",
    ),
}

RUNNER_LOG_MAX_BYTES = 50 * 1024 * 1024

DEFAULT_RESTART_RUNNER_KEYS: tuple[str, ...] = (
    "control",
    "runtime",
    "kis_block_trader",
    "investment_memory",
    "live_evaluator",
    "market_judge",
    "market_pulse",
    "binance_block_trader",
    "crypto_market_research",
    "crypto_pattern_lab",
    "crypto_alpha",
    "jue_wiki",
    "strategy_insights",
    "watchdog",
)


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


def _start_info_for_pid(pid: int | None) -> dict[str, Any]:
    if not pid or pid <= 0:
        return {"started_at": "", "started_at_epoch": None}
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return {"started_at": "", "started_at_epoch": None}
    if proc.returncode != 0:
        return {"started_at": "", "started_at_epoch": None}
    raw = str(proc.stdout or "").strip()
    if not raw:
        return {"started_at": "", "started_at_epoch": None}
    try:
        started = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").astimezone()
    except ValueError:
        return {"started_at": raw, "started_at_epoch": None}
    return {
        "started_at": started.isoformat(),
        "started_at_epoch": started.timestamp(),
    }


def _matches(pattern: str, command: str) -> bool:
    try:
        return bool(re.search(pattern, command))
    except re.error:
        return False


def _is_launcher_wrapper(command: str) -> bool:
    prefixes = (
        "tmux new-session ",
        "zsh -c ",
        "/bin/zsh -c ",
        "/bin/zsh -lc ",
        "bash -lc ",
        "/bin/bash -lc ",
    )
    return command.startswith(prefixes)


def _is_process_scan_noise(command: str) -> bool:
    parts = command.strip().split(None, 1)
    if not parts:
        return True
    executable = Path(parts[0]).name
    if executable in {
        "git",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ripgrep",
        "pytest",
        "tee",
    }:
        return True
    command_tokens = command.strip().split()
    if any(Path(token).name == "pytest" for token in command_tokens[:4]):
        return True
    if len(command_tokens) >= 3 and command_tokens[1:3] == ["-m", "pytest"]:
        return True
    return False


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
        if _is_process_scan_noise(command):
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

    primary_pid = int(matches[0]["pid"]) if matches else None
    start_info = _start_info_for_pid(primary_pid)
    return {
        "key": key,
        "label": RUNNER_LABELS.get(key, key),
        "status": "running" if alive else "stopped",
        "alive": alive,
        "pid": primary_pid,
        **start_info,
        "pid_file": str(pid_path),
        "pid_file_pid": pid_file_pid,
        "pid_file_status": pid_file_status,
        "matched_count": len(matches),
        "matches": matches[:5],
    }


def _normalize_restart_keys(keys: list[str] | tuple[str, ...] | None) -> list[str]:
    raw_keys = list(keys or DEFAULT_RESTART_RUNNER_KEYS)
    normalized: list[str] = []
    for raw in raw_keys:
        key = str(raw or "").strip()
        if not key:
            continue
        if key not in RUNNER_RESTART_SPECS:
            raise ValueError(f"unknown runner key: {key}")
        if key not in normalized:
            normalized.append(key)
    if not normalized:
        raise ValueError("at least one runner key is required")
    return normalized


def _existing_runner_pids(keys: list[str]) -> dict[str, list[int]]:
    current_pid = os.getpid()
    by_key: dict[str, list[int]] = {}
    for key in keys:
        pattern = RUNNER_PATTERNS.get(key, "")
        pids: list[int] = []
        for row in list_matching_processes(pattern, include_current=key == "control"):
            try:
                pid = int(row.get("pid") or 0)
            except (TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            if key != "control" and pid == current_pid:
                continue
            if pid not in pids:
                pids.append(pid)
        by_key[key] = pids
    return by_key


def _restart_script(
    keys: list[str],
    *,
    delay_sec: float,
    existing_pids_by_key: dict[str, list[int]] | None = None,
) -> str:
    cwd = shlex.quote(str(Path.cwd()))
    lines = [
        "set +e",
        (
            'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:'
            '/usr/sbin:/sbin:${PATH:-}"'
        ),
        f"sleep {max(float(delay_sec), 0.0):.2f}",
        "mkdir -p .runtime .runtime/logs",
        f"HERMES_LOG_MAX_BYTES={RUNNER_LOG_MAX_BYTES}",
    ]
    for key in keys:
        spec = RUNNER_RESTART_SPECS[key]
        for session in spec.session_names:
            lines.append(f"tmux kill-session -t {shlex.quote(session)} 2>/dev/null || true")
        existing_pids = [
            str(int(pid))
            for pid in (existing_pids_by_key or {}).get(key, [])
            if int(pid) > 0
        ]
        if existing_pids:
            pid_text = " ".join(existing_pids)
            lines.append(f"kill -TERM {pid_text} 2>/dev/null || true")
            lines.append("sleep 0.50")
            lines.append(f"kill -KILL {pid_text} 2>/dev/null || true")
        log_path = shlex.quote(spec.log_path)
        rotated_log_path = shlex.quote(f"{spec.log_path}.1")
        lines.extend(
            [
                f"mkdir -p $(dirname {log_path})",
                f"if [ -f {log_path} ]; then",
                (
                    f"  log_size=$(wc -c < {log_path} 2>/dev/null "
                    "| tr -d '[:space:]')"
                ),
                (
                    "  if [ ${log_size:-0} -gt "
                    "$HERMES_LOG_MAX_BYTES ]; then"
                ),
                f"    rm -f {rotated_log_path}",
                f"    mv {log_path} {rotated_log_path}",
                "  fi",
                "fi",
            ]
        )
        run_command = (
            f"cd {cwd} && {spec.command} 2>&1 | tee -a {log_path}"
        )
        lines.append(
            "tmux new-session -d "
            f"-s {shlex.quote(spec.primary_session)} "
            f"{shlex.quote(run_command)}"
        )
    return "\n".join(lines)


def restart_runner_processes(
    keys: list[str] | tuple[str, ...] | None = None,
    *,
    delay_sec: float = 0.5,
) -> dict[str, Any]:
    normalized = _normalize_restart_keys(keys)
    existing_pids_by_key = _existing_runner_pids(normalized)
    script = _restart_script(
        normalized,
        delay_sec=delay_sec,
        existing_pids_by_key=existing_pids_by_key,
    )
    proc = subprocess.Popen(
        ["/bin/zsh", "-lc", script],
        cwd=str(Path.cwd()),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        "status": "scheduled",
        "keys": normalized,
        "delay_sec": max(float(delay_sec), 0.0),
        "terminated_existing_pids": {
            key: pids for key, pids in existing_pids_by_key.items() if pids
        },
        "supervisor_pid": int(proc.pid),
    }
