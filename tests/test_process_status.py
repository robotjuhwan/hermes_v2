from __future__ import annotations

import asyncio
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


def test_runner_process_status_ignores_git_commands_that_mention_runner_logs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)

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
                    "111 /Library/Developer/CommandLineTools/usr/bin/git "
                    "add -- .runtime/tradecraft-strategy-insights.log\n"
                    "222 .venv/bin/python .venv/bin/tradecraft-strategy-insights\n"
                ),
            )
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(process_status.subprocess, "run", fake_run)

    status = process_status.runner_process_status("strategy_insights")

    assert status["alive"] is True
    assert status["pid"] == 222
    assert status["matched_count"] == 1
    assert all("git add" not in row["command"] for row in status["matches"])


def test_runner_process_status_does_not_treat_only_git_match_as_alive(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)

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
                    "111 /Library/Developer/CommandLineTools/usr/bin/git "
                    "add -- .runtime/tradecraft-binance-block-trader.log\n"
                ),
            )
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(process_status.subprocess, "run", fake_run)

    status = process_status.runner_process_status("binance_block_trader")

    assert status["alive"] is False
    assert status["status"] == "stopped"
    assert status["matched_count"] == 0


def test_runner_process_status_ignores_pytest_commands_that_mention_runner_modules(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)

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
                    "111 .venv/bin/python -m pytest "
                    "tests/test_live_evaluator_runner.py\n"
                    "222 .venv/bin/python .venv/bin/tradecraft-live-evaluator\n"
                ),
            )
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(process_status.subprocess, "run", fake_run)

    status = process_status.runner_process_status("live_evaluator")

    assert status["alive"] is True
    assert status["pid"] == 222
    assert status["matched_count"] == 1
    assert all("pytest" not in row["command"] for row in status["matches"])


def test_runner_process_status_ignores_tee_commands_that_mention_runner_logs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)

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
                    "111 tee -a .runtime/binance_block_trader.log "
                    ".runtime/logs/tradecraft-binance-block-trader.log\n"
                    "222 .venv/bin/python .venv/bin/tradecraft-binance-block-trader\n"
                ),
            )
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(process_status.subprocess, "run", fake_run)

    status = process_status.runner_process_status("binance_block_trader")

    assert status["alive"] is True
    assert status["pid"] == 222
    assert status["matched_count"] == 1
    assert all("tee -a" not in row["command"] for row in status["matches"])


def test_runner_process_status_ignores_shell_diagnostic_commands_that_embed_runner_pattern(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(process_status, "RUNNER_PID_DIR", tmp_path)

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
                    "111 /bin/zsh -c ps -eo pid,command | "
                    "rg \"tradecraft-kis-block-trader|tradecraft-control\"\n"
                    "222 .venv/bin/python .venv/bin/tradecraft-kis-block-trader\n"
                ),
            )
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(process_status.subprocess, "run", fake_run)

    status = process_status.runner_process_status("kis_block_trader")

    assert status["alive"] is True
    assert status["pid"] == 222
    assert status["matched_count"] == 1
    assert all("/bin/zsh -c ps" not in row["command"] for row in status["matches"])


def test_jue_wiki_runner_process_key_has_pid_file_and_label() -> None:
    assert process_status.runner_pid_path("jue_wiki").as_posix().endswith(
        ".runtime/pids/tradecraft-jue-wiki.pid"
    )
    assert process_status.RUNNER_LABELS["jue_wiki"] == "Jue wiki runner"
    assert "tradecraft-jue-wiki" in process_status.RUNNER_PATTERNS["jue_wiki"]
    spec = process_status.RUNNER_RESTART_SPECS["jue_wiki"]
    assert spec.command == ".venv/bin/tradecraft-jue-wiki"
    assert spec.log_path == ".runtime/jue_wiki_runner.log"


def test_naver_reports_runner_process_key_is_restartable() -> None:
    assert process_status.runner_pid_path("naver_reports").as_posix().endswith(
        ".runtime/pids/tradecraft-naver-reports.pid"
    )
    assert process_status.RUNNER_LABELS["naver_reports"] == "reports crawler"
    assert "tradecraft-naver-reports" in process_status.RUNNER_PATTERNS["naver_reports"]
    spec = process_status.RUNNER_RESTART_SPECS["naver_reports"]
    assert spec.command == ".venv/bin/tradecraft-naver-reports"
    assert spec.log_path == ".runtime/naver_reports.log"


def test_control_run_writes_pid_file_around_uvicorn(monkeypatch) -> None:
    import uvicorn
    import tradecraft.main as main_module

    events: list[tuple[str, object]] = []

    def fake_write_current_runner_pid(key: str) -> None:
        events.append(("write", key))

    def fake_clear_current_runner_pid(key: str) -> None:
        events.append(("clear", key))

    def fake_uvicorn_run(app_path: str, **kwargs: object) -> None:
        events.append(("uvicorn", {"app_path": app_path, **kwargs}))

    monkeypatch.setattr(
        main_module,
        "write_current_runner_pid",
        fake_write_current_runner_pid,
    )
    monkeypatch.setattr(
        main_module,
        "clear_current_runner_pid",
        fake_clear_current_runner_pid,
    )
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    main_module.run()

    assert events[0] == ("write", "control")
    assert events[1][0] == "uvicorn"
    assert events[2] == ("clear", "control")


def test_control_lifespan_does_not_overwrite_runner_pid_file(monkeypatch) -> None:
    import tradecraft.main as main_module

    events: list[tuple[str, object]] = []

    def fake_write_current_runner_pid(key: str) -> None:
        events.append(("write", key))

    def fake_clear_current_runner_pid(key: str) -> None:
        events.append(("clear", key))

    monkeypatch.setattr(
        main_module,
        "write_current_runner_pid",
        fake_write_current_runner_pid,
    )
    monkeypatch.setattr(
        main_module,
        "clear_current_runner_pid",
        fake_clear_current_runner_pid,
    )
    monkeypatch.setattr(main_module.telegram, "config", SimpleNamespace(ready=False))

    async def run_lifespan_once() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(run_lifespan_once())

    assert events == []


def test_restart_runner_processes_uses_allowlisted_tmux_commands(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 4321

    def fake_popen(
        cmd: list[str],
        cwd: str,
        start_new_session: bool,
        stdout: object,
        stderr: object,
    ) -> FakeProcess:
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "start_new_session": start_new_session,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        return FakeProcess()

    monkeypatch.setattr(process_status.subprocess, "Popen", fake_popen)

    result = process_status.restart_runner_processes(
        ["market_judge", "binance_block_trader"],
        delay_sec=0.1,
    )

    assert result["status"] == "scheduled"
    assert result["keys"] == ["market_judge", "binance_block_trader"]
    assert result["supervisor_pid"] == 4321
    assert len(calls) == 1
    cmd = calls[0]["cmd"]
    assert cmd[:2] == ["/bin/zsh", "-lc"]
    script = str(cmd[2])
    assert 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"' in script
    assert "tmux kill-session -t hermes-market-judge" in script
    assert "tmux kill-session -t tradecraft-market-judge" in script
    assert "tmux new-session -d -s tradecraft-market-judge" in script
    assert ".venv/bin/tradecraft-market-judge" in script
    assert "tmux new-session -d -s tradecraft-binance-block-trader" in script
    assert ".venv/bin/tradecraft-binance-block-trader" in script
    assert "HERMES_LOG_MAX_BYTES" in script
    assert ".runtime/binance_block_trader.log.1" in script


def test_restart_runner_processes_terminates_existing_matching_pids(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 9876

    def fake_popen(
        cmd: list[str],
        cwd: str,
        start_new_session: bool,
        stdout: object,
        stderr: object,
    ) -> FakeProcess:
        _ = (cwd, start_new_session, stdout, stderr)
        calls.append({"cmd": cmd})
        return FakeProcess()

    def fake_matches(pattern: str, *, include_current: bool = False) -> list[dict[str, object]]:
        _ = include_current
        if "tradecraft-market-judge" in pattern:
            return [
                {"pid": 111, "command": ".venv/bin/python .venv/bin/tradecraft-market-judge"},
                {"pid": 222, "command": ".venv/bin/python -m tradecraft.runtime.market_judge_runner"},
            ]
        return []

    monkeypatch.setattr(process_status.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(process_status, "list_matching_processes", fake_matches)

    result = process_status.restart_runner_processes(["market_judge"], delay_sec=0.1)

    assert result["terminated_existing_pids"] == {"market_judge": [111, 222]}
    script = str(calls[0]["cmd"][2])
    assert "kill -TERM 111 222" in script
    assert "kill -KILL 111 222" in script
    assert "tmux new-session -d -s tradecraft-market-judge" in script


def test_crypto_pattern_lab_runner_is_registered_for_ops_restart() -> None:
    assert process_status.RUNNER_PID_FILES["crypto_pattern_lab"] == (
        "tradecraft-crypto-pattern-lab.pid"
    )
    assert "crypto_pattern_lab" in process_status.RUNNER_PATTERNS
    assert process_status.RUNNER_RESTART_SPECS["crypto_pattern_lab"].command == (
        ".venv/bin/tradecraft-crypto-pattern-lab"
    )
    assert "crypto_pattern_lab" in process_status.DEFAULT_RESTART_RUNNER_KEYS


def test_jue_codex_lab_runner_is_retired_from_process_ops() -> None:
    assert "jue_codex_lab" not in process_status.RUNNER_PID_FILES
    assert "jue_codex_lab" not in process_status.RUNNER_LABELS
    assert "jue_codex_lab" not in process_status.RUNNER_PATTERNS
    assert "jue_codex_lab" not in process_status.RUNNER_RESTART_SPECS
    assert "jue_codex_lab" not in process_status.DEFAULT_RESTART_RUNNER_KEYS


def test_retired_kis_trader_runner_is_not_registered_for_process_ops() -> None:
    assert "kis_trader" not in process_status.RUNNER_PID_FILES
    assert "kis_trader" not in process_status.RUNNER_PATTERNS
    assert "kis_trader" not in process_status.RUNNER_LABELS
    assert "kis_trader" not in process_status.RUNNER_RESTART_SPECS
    assert "kis_trader" not in process_status.DEFAULT_RESTART_RUNNER_KEYS


def test_research_and_watchdog_runners_are_registered_for_ops() -> None:
    assert process_status.RUNNER_PID_FILES["intelligence"] == (
        "tradecraft-intelligence.pid"
    )
    assert process_status.RUNNER_RESTART_SPECS["intelligence"].command == (
        ".venv/bin/tradecraft-intelligence"
    )
    assert "intelligence" not in process_status.DEFAULT_RESTART_RUNNER_KEYS

    assert process_status.RUNNER_PID_FILES["research"] == "tradecraft-research.pid"
    assert process_status.RUNNER_RESTART_SPECS["research"].command == (
        ".venv/bin/tradecraft-research"
    )
    assert "research" not in process_status.DEFAULT_RESTART_RUNNER_KEYS

    assert process_status.RUNNER_PID_FILES["watchdog"] == "tradecraft-watchdog.pid"
    assert process_status.RUNNER_RESTART_SPECS["watchdog"].command == (
        ".venv/bin/tradecraft-watchdog"
    )


def test_restart_runner_processes_rejects_unknown_keys() -> None:
    try:
        process_status.restart_runner_processes(["market_judge; rm -rf /"])
    except ValueError as exc:
        assert "unknown runner key" in str(exc)
    else:
        raise AssertionError("unknown runner key was not rejected")
