from __future__ import annotations

import os
from datetime import timezone
from pathlib import Path
from typing import Any

from tradecraft.api.ops_process_payloads import (
    DUPLICATE_SCAN_RUNNER_KEYS,
    build_app_core_runner_processes,
    iso_to_utc,
    light_process_with_staleness,
    process_with_code_staleness,
    runner_process_status_light,
)


def test_iso_to_utc_parses_z_and_naive_values() -> None:
    zulu = iso_to_utc("2026-07-06T01:02:03Z")
    naive = iso_to_utc("2026-07-06T01:02:03")

    assert zulu is not None
    assert zulu.tzinfo == timezone.utc
    assert zulu.isoformat() == "2026-07-06T01:02:03+00:00"
    assert naive is not None
    assert naive.tzinfo == timezone.utc
    assert iso_to_utc("") is None
    assert iso_to_utc("not-a-date") is None


def test_process_with_code_staleness_marks_only_alive_stale_processes(
    tmp_path: Path,
) -> None:
    code = tmp_path / "runner.py"
    code.write_text("# code\n", encoding="utf-8")
    os.utime(code, (1_700_000_010.0, 1_700_000_010.0))

    stale = process_with_code_staleness(
        {"alive": True, "started_at_epoch": 1_700_000_000.0},
        code_paths=[code],
    )
    fresh = process_with_code_staleness(
        {"alive": True, "started_at_epoch": 1_700_000_010.0},
        code_paths=[code],
    )
    stopped = process_with_code_staleness(
        {"alive": False, "started_at_epoch": 1_700_000_000.0},
        code_paths=[code],
    )

    assert stale["stale_process"] is True
    assert stale["code_mtime_epoch"] == 1_700_000_010.0
    assert fresh["stale_process"] is False
    assert stopped["stale_process"] is False


def test_runner_process_status_light_scans_alive_duplicate_runners() -> None:
    calls: list[tuple[str, bool]] = []

    def status_fn(key: str, *, include_matches: bool = True) -> dict[str, Any]:
        calls.append((key, include_matches))
        return {"key": key, "alive": True, "matches": [1] if include_matches else []}

    runner_process_status_light("kis_block_trader", status_fn)
    runner_process_status_light("watchdog", status_fn)

    assert "kis_block_trader" in DUPLICATE_SCAN_RUNNER_KEYS
    assert calls == [
        ("kis_block_trader", False),
        ("kis_block_trader", True),
        ("watchdog", False),
    ]


def test_light_process_with_staleness_adds_code_metadata(tmp_path: Path) -> None:
    code = tmp_path / "service.py"
    code.write_text("# service\n", encoding="utf-8")
    os.utime(code, (1_700_000_010.0, 1_700_000_010.0))

    def status_fn(key: str, *, include_matches: bool = True) -> dict[str, Any]:
        return {
            "key": key,
            "alive": True,
            "direct_alive": True,
            "started_at_epoch": 1_700_000_000.0,
        }

    payload = light_process_with_staleness(
        "market_judge",
        runner_status=status_fn,
        code_paths=[code],
    )

    assert payload["key"] == "market_judge"
    assert payload["stale_process"] is True
    assert payload["code_mtime_epoch"] == 1_700_000_010.0


def test_build_app_core_runner_processes_omits_legacy_and_disabled_research(
    tmp_path: Path,
) -> None:
    base = tmp_path / "tradecraft"
    (base / "runtime").mkdir(parents=True)
    (base / "services").mkdir(parents=True)
    (base / "api").mkdir(parents=True)

    def status_fn(key: str, *, include_matches: bool = True) -> dict[str, Any]:
        return {
            "key": key,
            "label": key,
            "status": "running",
            "alive": True,
            "pid": 123,
            "started_at_epoch": None,
            "matches": [] if include_matches else [],
        }

    payload = build_app_core_runner_processes(
        base=base,
        runner_status=status_fn,
        research_enabled=False,
    )

    assert "control" in payload
    assert "jue_wiki" in payload
    assert "naver_reports" in payload
    assert "intelligence" not in payload
    assert "research" not in payload
    assert all("stale_process" in process for process in payload.values())
