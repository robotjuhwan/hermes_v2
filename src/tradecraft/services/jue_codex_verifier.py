from __future__ import annotations

import os
import re
import select
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

OUTPUT_EXCERPT_CHARS = 4000
OUTPUT_TAIL_BYTES = OUTPUT_EXCERPT_CHARS * 4
REJECTED_COMMAND_MESSAGE = "Command is not allowed for verification."
SHELL_METACHARS = frozenset(";|&<>`")
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*")


class JueCodexVerifier:
    def __init__(self, workdir: str | Path, timeout_sec: float = 300.0) -> None:
        self.workdir = Path(workdir)
        self.timeout_sec = timeout_sec

    def run_commands(self, commands: list[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []

        for command in commands:
            parsed = _parse_allowed_command(command)
            if parsed is None:
                results.append(
                    {
                        "command": command,
                        "status": "rejected",
                        "returncode": None,
                        "message": REJECTED_COMMAND_MESSAGE,
                        "output_excerpt": "",
                        "elapsed_sec": 0.0,
                    }
                )
                return {"status": "fail", "results": results}

            started = time.perf_counter()
            process: subprocess.Popen[bytes] | None = None
            try:
                process = subprocess.Popen(
                    parsed,
                    cwd=self.workdir,
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                capture = _capture_bounded_output(process, self.timeout_sec)
            except subprocess.TimeoutExpired:
                elapsed_sec = time.perf_counter() - started
                message = f"Command timed out after {self.timeout_sec} seconds."
                capture = _terminate_process_group(process, message)
                results.append(
                    {
                        "command": command,
                        "status": "timeout",
                        "returncode": None,
                        "message": message,
                        "output_excerpt": capture["output_excerpt"],
                        "output_truncated": capture["output_truncated"],
                        "elapsed_sec": elapsed_sec,
                    }
                )
                return {"status": "fail", "results": results}

            elapsed_sec = time.perf_counter() - started
            returncode = process.returncode if process is not None else 1
            status = "pass" if returncode == 0 else "fail"
            results.append(
                {
                    "command": command,
                    "status": status,
                    "returncode": returncode,
                    "output_excerpt": capture["output_excerpt"],
                    "output_truncated": capture["output_truncated"],
                    "elapsed_sec": elapsed_sec,
                }
            )

            if returncode != 0:
                return {"status": "fail", "results": results}

        return {"status": "pass", "results": results}


def _parse_allowed_command(command: str) -> list[str] | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None

    if not argv:
        return None
    if _contains_shell_syntax(command, argv):
        return None
    if not _is_allowed_argv(argv):
        return None

    return argv


def _contains_shell_syntax(command: str, argv: list[str]) -> bool:
    if "$(" in command:
        return True
    if ENV_ASSIGNMENT_RE.match(argv[0]):
        return True
    return any(any(char in token for char in SHELL_METACHARS) for token in argv)


def _is_allowed_argv(argv: list[str]) -> bool:
    command = argv[0]
    if command == "pytest":
        return True
    if command in {"python", "python3"}:
        return len(argv) >= 4 and argv[1:3] == ["-m", "py_compile"]
    if command == "ruff":
        return len(argv) >= 2 and argv[1] == "check"
    return False


def _capture_bounded_output(
    process: subprocess.Popen[bytes],
    timeout_sec: float,
) -> dict[str, Any]:
    if process.stdout is None:
        process.wait(timeout=timeout_sec)
        return {"output_excerpt": "", "output_truncated": False}

    deadline = time.monotonic() + timeout_sec
    tail = bytearray()
    output_truncated = False
    stdout_fd = process.stdout.fileno()
    os.set_blocking(stdout_fd, False)

    while True:
        tail, output_truncated = _read_available_tail(
            stdout_fd,
            tail,
            output_truncated,
        )

        if process.poll() is not None:
            tail, output_truncated = _drain_tail(stdout_fd, tail, output_truncated)
            break

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_sec)

        select.select([stdout_fd], [], [], min(0.05, remaining))

    process.wait(timeout=0)
    return _capture_result(tail, output_truncated)


def _terminate_process_group(
    process: subprocess.Popen[bytes] | None,
    message: str,
) -> dict[str, Any]:
    if process is None:
        return {"output_excerpt": message[-OUTPUT_EXCERPT_CHARS:], "output_truncated": False}

    tail = bytearray()
    output_truncated = False
    stdout_fd: int | None = None
    if process.stdout is not None:
        stdout_fd = process.stdout.fileno()
        os.set_blocking(stdout_fd, False)
        tail, output_truncated = _read_available_tail(
            stdout_fd,
            tail,
            output_truncated,
        )

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1.0)

    if stdout_fd is not None:
        tail, output_truncated = _drain_tail(stdout_fd, tail, output_truncated)
    tail, output_truncated = _append_tail_bytes(
        tail,
        message.encode("utf-8", errors="replace"),
        output_truncated,
    )
    return _capture_result(tail, output_truncated)


def _read_available_tail(
    fd: int,
    tail: bytearray,
    output_truncated: bool,
) -> tuple[bytearray, bool]:
    while True:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            return tail, output_truncated
        except OSError:
            return tail, output_truncated

        if not chunk:
            return tail, output_truncated

        tail, output_truncated = _append_tail_bytes(tail, chunk, output_truncated)


def _drain_tail(
    fd: int,
    tail: bytearray,
    output_truncated: bool,
) -> tuple[bytearray, bool]:
    while True:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            return tail, output_truncated
        except OSError:
            return tail, output_truncated

        if not chunk:
            return tail, output_truncated

        tail, output_truncated = _append_tail_bytes(tail, chunk, output_truncated)


def _append_tail_bytes(
    tail: bytearray,
    chunk: bytes,
    output_truncated: bool,
) -> tuple[bytearray, bool]:
    tail.extend(chunk)
    if len(tail) <= OUTPUT_TAIL_BYTES:
        return tail, output_truncated

    del tail[: len(tail) - OUTPUT_TAIL_BYTES]
    return tail, True


def _capture_result(tail: bytearray, output_truncated: bool) -> dict[str, Any]:
    output = bytes(tail).decode("utf-8", errors="replace")
    excerpt = output[-OUTPUT_EXCERPT_CHARS:]
    return {
        "output_excerpt": excerpt,
        "output_truncated": output_truncated or len(output) > len(excerpt),
    }
