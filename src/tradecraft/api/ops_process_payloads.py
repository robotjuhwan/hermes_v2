from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradecraft.api.ops_readiness import (
    build_core_runner_processes,
    light_runner_process_status,
    runner_status_with_cover,
)


DUPLICATE_SCAN_RUNNER_KEYS: set[str] = {
    "control",
    "kis_block_trader",
    "binance_block_trader",
    "market_judge",
    "market_pulse",
    "investment_memory",
    "live_evaluator",
}


def iso_to_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def max_code_mtime(paths: list[Path]) -> dict[str, Any]:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return {"code_mtime": "", "code_mtime_epoch": None}
    latest = max(path.stat().st_mtime for path in existing)
    return {
        "code_mtime": datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(),
        "code_mtime_epoch": latest,
    }


def process_with_code_staleness(
    process: dict[str, Any],
    *,
    code_paths: list[Path],
) -> dict[str, Any]:
    payload = dict(process)
    code = max_code_mtime(code_paths)
    started_epoch = payload.get("started_at_epoch")
    code_epoch = code.get("code_mtime_epoch")
    stale = False
    if bool(payload.get("direct_alive") or payload.get("alive")):
        try:
            stale = bool(
                started_epoch is not None
                and code_epoch is not None
                and float(code_epoch) > float(started_epoch) + 1.0
            )
        except (TypeError, ValueError):
            stale = False
    payload.update(code)
    payload["stale_process"] = stale
    return payload


def runner_process_status_light(
    key: str,
    runner_status: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    return light_runner_process_status(
        key,
        runner_status,
        scan_alive_matches=key in DUPLICATE_SCAN_RUNNER_KEYS,
    )


def light_process_with_staleness(
    key: str,
    *,
    runner_status: Callable[..., dict[str, Any]],
    code_paths: list[Path],
) -> dict[str, Any]:
    return process_with_code_staleness(
        runner_status_with_cover(runner_process_status_light(key, runner_status)),
        code_paths=code_paths,
    )


def build_app_core_runner_processes(
    *,
    base: Path,
    runner_status: Callable[..., dict[str, Any]],
    research_enabled: bool,
) -> dict[str, dict[str, Any]]:
    processes = build_core_runner_processes(
        base=base,
        runner_status=lambda key: runner_status_with_cover(
            runner_process_status_light(key, runner_status)
        ),
        apply_code_staleness=process_with_code_staleness,
    )
    processes["jue_wiki"] = process_with_code_staleness(
        runner_status_with_cover(runner_process_status_light("jue_wiki", runner_status)),
        code_paths=[
            base / "runtime" / "jue_wiki_runner.py",
            base / "services" / "jue_wiki.py",
        ],
    )
    processes.pop("intelligence", None)
    if not bool(research_enabled):
        processes.pop("research", None)
    return processes
