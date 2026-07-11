#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / ".verification"
SUPPORTED_AREAS = (
    "binance",
    "kis",
    "jue",
    "readiness",
    "memory",
    "runtime",
    "reports",
    "crypto",
)
PROFILE_BUDGET_SECONDS = {
    "fast": 90,
    "domain": 5 * 60,
    "full": 15 * 60,
}


class VerificationStep(NamedTuple):
    name: str
    command: list[str]


def _has_xdist() -> bool:
    return importlib.util.find_spec("xdist") is not None


def build_steps(
    profile: str,
    *,
    area: str | None = None,
    xdist_available: bool | None = None,
) -> list[VerificationStep]:
    normalized = str(profile or "").strip().lower()
    if normalized not in PROFILE_BUDGET_SECONDS:
        raise ValueError(f"unsupported verification profile: {profile}")
    normalized_area = str(area or "").strip().lower()
    if normalized == "domain" and not normalized_area:
        raise ValueError("domain verification requires --area")
    if normalized_area and normalized_area not in SUPPORTED_AREAS:
        raise ValueError(f"unsupported area: {normalized_area}")

    steps = [
        VerificationStep(
            "project-contracts",
            [sys.executable, "scripts/check_project_contracts.py"],
        ),
        VerificationStep(
            "ruff",
            [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
        ),
    ]
    pytest_command = [
        sys.executable,
        "-m",
        "pytest",
        "--durations=50",
        "--durations-min=0",
    ]
    if normalized == "fast":
        pytest_command.extend(["-m", "(unit or contract) and not slow"])
        parallel = _has_xdist() if xdist_available is None else xdist_available
        if parallel:
            pytest_command.extend(["-n", "auto", "--dist", "loadscope"])
    elif normalized == "domain":
        pytest_command.extend(["-m", normalized_area])
    pytest_command.append("tests")
    steps.append(VerificationStep(f"pytest-{normalized}", pytest_command))
    return steps


def _write_report(
    *,
    profile: str,
    area: str | None,
    elapsed_seconds: float,
    exit_code: int,
    step_results: list[dict[str, object]],
) -> Path:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = f"-{area}" if area else ""
    path = REPORT_ROOT / f"latest-{profile}{suffix}.json"
    budget = PROFILE_BUDGET_SECONDS[profile]
    payload = {
        "version": "tradecraft_verification_report_v1",
        "profile": profile,
        "area": area or "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "budget_seconds": budget,
        "within_budget": elapsed_seconds <= budget,
        "exit_code": exit_code,
        "steps": step_results,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def run_steps(
    profile: str,
    *,
    area: str | None = None,
    steps: Sequence[VerificationStep] | None = None,
) -> int:
    selected_steps = list(steps or build_steps(profile, area=area))
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = f"-{area}" if area else ""
    log_path = REPORT_ROOT / f"latest-{profile}{suffix}.log"
    started = time.monotonic()
    exit_code = 0
    step_results: list[dict[str, object]] = []

    with log_path.open("w", encoding="utf-8") as log:
        for step in selected_steps:
            step_started = time.monotonic()
            header = f"\n[{step.name}] {' '.join(step.command)}\n"
            print(header, end="", flush=True)
            log.write(header)
            log.flush()
            process = subprocess.Popen(
                step.command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
            code = process.wait()
            elapsed = time.monotonic() - step_started
            step_results.append(
                {
                    "name": step.name,
                    "command": step.command,
                    "elapsed_seconds": round(elapsed, 3),
                    "exit_code": code,
                }
            )
            if code:
                exit_code = code
                break

    elapsed_seconds = time.monotonic() - started
    report_path = _write_report(
        profile=profile,
        area=area,
        elapsed_seconds=elapsed_seconds,
        exit_code=exit_code,
        step_results=step_results,
    )
    budget = PROFILE_BUDGET_SECONDS[profile]
    within_budget = elapsed_seconds <= budget
    print(
        f"Verification {profile}{suffix}: {elapsed_seconds:.1f}s / {budget}s; "
        f"report={report_path}"
    )
    if exit_code == 0 and not within_budget:
        print(
            f"Verification time budget exceeded: {elapsed_seconds:.1f}s > {budget}s",
            file=sys.stderr,
        )
        return 2
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run TradeCraft verification profiles")
    subparsers = parser.add_subparsers(dest="profile", required=True)
    subparsers.add_parser("fast", help="unit and contract feedback loop")
    domain = subparsers.add_parser("domain", help="one domain's complete test slice")
    domain.add_argument("--area", choices=SUPPORTED_AREAS, required=True)
    subparsers.add_parser("full", help="all project tests")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return run_steps(args.profile, area=getattr(args, "area", None))


if __name__ == "__main__":
    raise SystemExit(main())
