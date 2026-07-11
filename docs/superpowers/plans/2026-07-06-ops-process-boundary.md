# Ops Process Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract process-liveness and code-staleness helper logic from `src/tradecraft/main.py` into a focused API helper module without changing ops readiness behavior.

**Architecture:** Keep app-global wiring in `main.py`, but move pure process timestamp/staleness logic and app core runner process composition to `tradecraft.api.ops_process_payloads`. The new module composes existing `tradecraft.api.ops_readiness` helpers rather than duplicating lower-level cover or process-map behavior.

**Tech Stack:** Python 3.10+, FastAPI ops readiness payload helpers, pytest, ruff.

---

## Repository Constraint

Do not commit during this plan. `AGENTS.md` says not to commit unless the user explicitly requests it. Use `git diff --check` and focused verification in place of commit steps.

## File Structure

- Create `src/tradecraft/api/ops_process_payloads.py`
  - Owns timestamp parsing, code mtime, stale-process annotation, duplicate-scan runner policy, light process status, and app core runner process composition.
- Create `tests/test_ops_process_payloads.py`
  - Covers the extracted module directly.
- Modify `src/tradecraft/main.py`
  - Imports the new helpers.
  - Removes local implementations for timestamp parsing, code mtime, stale-process annotation, duplicate-scan runner logic, and app core runner process composition.
  - Keeps thin wrappers that supply app globals.

## Task 1: Extract Ops Process Helpers

**Files:**
- Create: `src/tradecraft/api/ops_process_payloads.py`
- Create: `tests/test_ops_process_payloads.py`
- Modify: `src/tradecraft/main.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ops_process_payloads.py`:

```python
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


def test_process_with_code_staleness_marks_only_alive_stale_processes(tmp_path: Path) -> None:
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


def test_build_app_core_runner_processes_omits_legacy_and_disabled_research(tmp_path: Path) -> None:
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
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_ops_process_payloads.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'tradecraft.api.ops_process_payloads'`.

- [ ] **Step 3: Add the module**

Create `src/tradecraft/api/ops_process_payloads.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_ops_process_payloads.py -q
```

Expected: pass.

- [ ] **Step 5: Wire `main.py` to the new module**

Modify `src/tradecraft/main.py` imports:

```python
from tradecraft.api.ops_process_payloads import (
    DUPLICATE_SCAN_RUNNER_KEYS,
    build_app_core_runner_processes,
    iso_to_utc,
    light_process_with_staleness as build_light_process_with_staleness,
    runner_process_status_light as build_runner_process_status_light,
)
```

Remove local implementations:

- `_iso_to_utc`
- `_max_code_mtime`
- `_process_with_code_staleness`
- `DUPLICATE_SCAN_RUNNER_KEYS`

Update `_next_from_latest` and `_next_from_latest_or_krx_clock` to call `iso_to_utc`.

Replace `_runner_process_status_light` with:

```python
def _runner_process_status_light(key: str) -> dict[str, Any]:
    return build_runner_process_status_light(key, runner_process_status)
```

Replace `_build_core_runner_processes` with:

```python
def _build_core_runner_processes() -> dict[str, dict[str, Any]]:
    return build_app_core_runner_processes(
        base=Path(__file__).resolve().parent,
        runner_status=runner_process_status,
        research_enabled=bool(settings.research_enabled),
    )
```

Replace `_light_process_with_staleness` with:

```python
def _light_process_with_staleness(
    key: str,
    *,
    code_paths: list[Path],
) -> dict[str, Any]:
    return build_light_process_with_staleness(
        key,
        runner_status=runner_process_status,
        code_paths=code_paths,
    )
```

- [ ] **Step 6: Run integration checks**

Run:

```bash
pytest tests/test_ops_process_payloads.py tests/test_ops_payloads.py tests/test_api_smoke.py -q
```

Expected: pass.

Run:

```bash
ruff check src/tradecraft/main.py src/tradecraft/api/ops_process_payloads.py tests/test_ops_process_payloads.py
```

Expected: pass.

Run:

```bash
python3 scripts/check_project_contracts.py
git diff --check
```

Expected: contract check prints `Project contract check OK`; diff check exits 0 with no output.
