# HERMES Structure Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first safe refactor layer for HERMES/Jue by adding contract checks, removing generated tracked artifacts, and centralizing runner identity before touching trading behavior.

**Architecture:** This plan preserves the active trading invariant: LLM managers produce intent, deterministic executors and venue-specific safety gates enforce orders. The implementation adds guardrails around documentation drift, generated files, and runtime process metadata so later KIS/Binance/service/UI decomposition can proceed with smaller blast radius.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic Settings, pytest, TOML via `tomllib`, local `git`, existing static UI and runner entrypoints.

---

## Scope Check

The full improvement effort spans independent subsystems: runtime operations, API/main decomposition, KIS/Binance trading services, memory/wiki, settings, and UI. This plan intentionally covers only the first independently testable slice:

- keep trading behavior unchanged;
- add drift checks for settings, routes, entrypoints, and generated artifacts;
- remove tracked generated package metadata;
- centralize runner process metadata without changing runner commands;
- document the next decomposition boundaries.

KIS/Binance service splitting and UI information architecture should be planned as separate follow-up plans after this one lands.

## File Structure

- Create `scripts/check_project_contracts.py`
  - Owns reusable project contract checks that can run from shell and pytest.
  - Checks `AppSettings` count in docs, console script imports, process-status runner maps, and tracked generated artifacts.
- Modify `tests/test_docs_spec.py`
  - Adds pytest coverage for settings-count drift and tracked generated artifacts.
- Create `src/tradecraft/runtime/runner_manifest.py`
  - Owns runner key, PID filename, pattern, label, restart command, session names, log path, and default restart ordering.
- Modify `src/tradecraft/runtime/process_status.py`
  - Imports runner manifest data and keeps existing process status behavior.
- Modify `tests/test_process_status.py`
  - Ensures `process_status.py` maps are generated from the runner manifest and still expose current Jue/Naver runner specs.
- Modify `docs/spec/12_config_env.md`
  - Updates captured settings count from the actual `AppSettings.model_fields` count.
- Modify `docs/spec/16_refactor_roadmap.md`
  - Adds the explicit follow-up decomposition sequence after this stabilization pass.
- Remove from git tracking:
  - `src/tradecraft_ui.egg-info/PKG-INFO`
  - `src/tradecraft_ui.egg-info/SOURCES.txt`
  - `src/tradecraft_ui.egg-info/entry_points.txt`
  - `src/tradecraft_ui.egg-info/requires.txt`

## Task 1: Add Project Contract Checker

**Files:**
- Create: `scripts/check_project_contracts.py`
- Modify: `tests/test_docs_spec.py`
- Test: `tests/test_docs_spec.py`

- [ ] **Step 1: Write failing tests for contract checks**

Append these tests to `tests/test_docs_spec.py`:

```python
def test_config_spec_captured_settings_count_matches_app_settings() -> None:
    from scripts.check_project_contracts import (
        docs_config_settings_count,
        settings_model_field_count,
    )

    assert docs_config_settings_count(ROOT / "docs/spec/12_config_env.md") == (
        settings_model_field_count()
    )


def test_repository_has_no_tracked_generated_package_metadata() -> None:
    from scripts.check_project_contracts import tracked_generated_files

    assert tracked_generated_files(ROOT) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_docs_spec.py::test_config_spec_captured_settings_count_matches_app_settings tests/test_docs_spec.py::test_repository_has_no_tracked_generated_package_metadata -q
```

Expected:

```text
FAILED tests/test_docs_spec.py::test_config_spec_captured_settings_count_matches_app_settings
FAILED tests/test_docs_spec.py::test_repository_has_no_tracked_generated_package_metadata
```

The first failure should show the documented settings count is stale. The second failure should show `src/tradecraft_ui.egg-info/*` is still tracked.

- [ ] **Step 3: Create the contract checker script**

Create `scripts/check_project_contracts.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


GENERATED_TRACKED_PATTERNS = (
    re.compile(r"(^|/)(__pycache__|.*\.pyc$)"),
    re.compile(r"(^|/)\.runtime/"),
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)node_modules/"),
    re.compile(r"(^|/)web_dist/"),
    re.compile(r"(^|/).*\.egg-info/"),
)


def settings_model_field_count() -> int:
    from tradecraft.config import AppSettings

    return len(AppSettings.model_fields)


def docs_config_settings_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"Captured setting names.*?:\s+(\d+)\s+fields", text)
    if not match:
        raise AssertionError(f"settings count marker missing in {path}")
    return int(match.group(1))


def project_scripts(root: Path = ROOT) -> dict[str, str]:
    pyproject = root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    if not isinstance(scripts, dict):
        return {}
    return {str(key): str(value) for key, value in scripts.items()}


def tracked_files(root: Path = ROOT) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git ls-files failed")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def tracked_generated_files(root: Path = ROOT) -> list[str]:
    generated: list[str] = []
    for path in tracked_files(root):
        if any(pattern.search(path) for pattern in GENERATED_TRACKED_PATTERNS):
            generated.append(path)
    return sorted(generated)


def process_status_contract() -> dict[str, Any]:
    from tradecraft.runtime import process_status

    key_sets = {
        "pid_files": set(process_status.RUNNER_PID_FILES),
        "patterns": set(process_status.RUNNER_PATTERNS),
        "labels": set(process_status.RUNNER_LABELS),
        "restart_specs": set(process_status.RUNNER_RESTART_SPECS),
    }
    return {
        "key_sets": key_sets,
        "all_equal": len({frozenset(value) for value in key_sets.values()}) == 1,
        "default_keys": tuple(process_status.DEFAULT_RESTART_RUNNER_KEYS),
    }


def validate_all(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    docs_count = docs_config_settings_count(root / "docs/spec/12_config_env.md")
    actual_count = settings_model_field_count()
    if docs_count != actual_count:
        errors.append(
            f"docs/spec/12_config_env.md captured {docs_count} settings, "
            f"but AppSettings exposes {actual_count}"
        )

    generated = tracked_generated_files(root)
    if generated:
        errors.append(
            "generated files are tracked: " + ", ".join(generated[:20])
        )

    process_contract = process_status_contract()
    if not process_contract["all_equal"]:
        errors.append("process_status runner maps do not expose the same key set")

    return errors


def main() -> int:
    errors = validate_all(ROOT)
    if errors:
        for error in errors:
            print(f"Project contract check FAIL: {error}", file=sys.stderr)
        return 1
    print("Project contract check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the checker directly**

Run:

```bash
python3 scripts/check_project_contracts.py
```

Expected before Tasks 2 and 3:

```text
Project contract check FAIL: docs/spec/12_config_env.md captured 266 settings, but AppSettings exposes 475
Project contract check FAIL: generated files are tracked: src/tradecraft_ui.egg-info/PKG-INFO, ...
```

- [ ] **Step 5: Commit this task after Tasks 2 and 3 pass**

Do not commit yet if the checker still fails. This task becomes committable after Task 2 updates the settings count and Task 3 removes tracked generated files.

## Task 2: Sync Config Spec With Current AppSettings

**Files:**
- Modify: `docs/spec/12_config_env.md`
- Test: `tests/test_docs_spec.py`

- [ ] **Step 1: Confirm actual settings count**

Run:

```bash
python3 - <<'PY'
from tradecraft.config import AppSettings
print(len(AppSettings.model_fields))
PY
```

Expected:

```text
475
```

- [ ] **Step 2: Update the captured settings count**

In `docs/spec/12_config_env.md`, replace this sentence near the top:

```markdown
Captured setting names from `AppSettings.model_fields` on 2026-05-24: 266 fields.
```

with:

```markdown
Captured setting names from `AppSettings.model_fields` on 2026-07-06: 475 fields.
```

- [ ] **Step 3: Run the focused settings-count test**

Run:

```bash
pytest tests/test_docs_spec.py::test_config_spec_captured_settings_count_matches_app_settings -q
```

Expected:

```text
.
```

- [ ] **Step 4: Review whether the full captured field list needs regeneration**

Run:

```bash
python3 - <<'PY'
from tradecraft.config import AppSettings
for name in AppSettings.model_fields:
    print(name)
PY
```

Expected: field names print without import errors. If `docs/spec/12_config_env.md` lacks a large block of new setting names, append the missing names inside the existing captured field list code block, preserving the current order from `AppSettings.model_fields`.

## Task 3: Remove Tracked Generated Package Metadata

**Files:**
- Remove from git index and working tree:
  - `src/tradecraft_ui.egg-info/PKG-INFO`
  - `src/tradecraft_ui.egg-info/SOURCES.txt`
  - `src/tradecraft_ui.egg-info/entry_points.txt`
  - `src/tradecraft_ui.egg-info/requires.txt`
- Test: `tests/test_docs_spec.py`

- [ ] **Step 1: Verify `.gitignore` already ignores egg-info**

Run:

```bash
rg -n '\*\.egg-info' .gitignore
```

Expected:

```text
2:*.egg-info/
```

- [ ] **Step 2: Remove tracked egg-info files**

Run:

```bash
git rm -r src/tradecraft_ui.egg-info
```

Expected:

```text
rm 'src/tradecraft_ui.egg-info/PKG-INFO'
rm 'src/tradecraft_ui.egg-info/SOURCES.txt'
rm 'src/tradecraft_ui.egg-info/entry_points.txt'
rm 'src/tradecraft_ui.egg-info/requires.txt'
```

- [ ] **Step 3: Run generated-file test**

Run:

```bash
pytest tests/test_docs_spec.py::test_repository_has_no_tracked_generated_package_metadata -q
```

Expected:

```text
.
```

- [ ] **Step 4: Run the contract checker**

Run:

```bash
python3 scripts/check_project_contracts.py
```

Expected after Tasks 2 and 3:

```text
Project contract check OK
```

- [ ] **Step 5: Commit Tasks 1-3**

Run:

```bash
git add scripts/check_project_contracts.py tests/test_docs_spec.py docs/spec/12_config_env.md .gitignore
git add -u src/tradecraft_ui.egg-info
git commit -m "chore: add project contract checks"
```

Expected: commit succeeds and `git status --short` shows only uncommitted work from later tasks, if any.

## Task 4: Centralize Runtime Runner Manifest

**Files:**
- Create: `src/tradecraft/runtime/runner_manifest.py`
- Modify: `src/tradecraft/runtime/process_status.py`
- Modify: `tests/test_process_status.py`
- Test: `tests/test_process_status.py`

- [ ] **Step 1: Write failing manifest consistency test**

Append this test to `tests/test_process_status.py`:

```python
def test_process_status_maps_are_generated_from_runner_manifest() -> None:
    from tradecraft.runtime.runner_manifest import (
        DEFAULT_RESTART_RUNNER_KEYS,
        RUNNER_SPECS,
    )

    manifest_keys = set(RUNNER_SPECS)
    assert set(process_status.RUNNER_PID_FILES) == manifest_keys
    assert set(process_status.RUNNER_PATTERNS) == manifest_keys
    assert set(process_status.RUNNER_LABELS) == manifest_keys
    assert set(process_status.RUNNER_RESTART_SPECS) == manifest_keys
    assert process_status.DEFAULT_RESTART_RUNNER_KEYS == DEFAULT_RESTART_RUNNER_KEYS
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_process_status.py::test_process_status_maps_are_generated_from_runner_manifest -q
```

Expected:

```text
ModuleNotFoundError: No module named 'tradecraft.runtime.runner_manifest'
```

- [ ] **Step 3: Create `runner_manifest.py`**

Create `src/tradecraft/runtime/runner_manifest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerSpec:
    key: str
    pid_file: str
    pattern: str
    label: str
    session_names: tuple[str, ...]
    command: str
    log_path: str

    @property
    def primary_session(self) -> str:
        return self.session_names[0]


RUNNER_SPECS: dict[str, RunnerSpec] = {
    "control": RunnerSpec(
        key="control",
        pid_file="tradecraft-control.pid",
        pattern=(
            r"tradecraft-control|tradecraft\.main:app|"
            r"uvicorn .*tradecraft\.main:app"
        ),
        label="control API",
        session_names=("tradecraft-control", "hermes-control"),
        command=".venv/bin/tradecraft-control --host 127.0.0.1 --port 18080",
        log_path=".runtime/control.log",
    ),
    "runtime": RunnerSpec(
        key="runtime",
        pid_file="tradecraft-runtime.pid",
        pattern=r"tradecraft-runtime|tradecraft\.runtime\.runner|runtime/runner\.py",
        label="runtime runner",
        session_names=("tradecraft-runtime", "hermes-runtime"),
        command=".venv/bin/tradecraft-runtime",
        log_path=".runtime/logs/runtime.log",
    ),
    "intelligence": RunnerSpec(
        key="intelligence",
        pid_file="tradecraft-intelligence.pid",
        pattern=(
            r"tradecraft-intelligence|tradecraft\.runtime\.intelligence_runner|"
            r"intelligence_runner\.py"
        ),
        label="intelligence runner",
        session_names=("tradecraft-intelligence", "hermes-intelligence"),
        command=".venv/bin/tradecraft-intelligence",
        log_path=".runtime/tradecraft-intelligence.log",
    ),
    "research": RunnerSpec(
        key="research",
        pid_file="tradecraft-research.pid",
        pattern=(
            r"tradecraft-research|tradecraft\.runtime\.research_runner|"
            r"research_runner\.py"
        ),
        label="research runner",
        session_names=("tradecraft-research", "hermes-research"),
        command=".venv/bin/tradecraft-research",
        log_path=".runtime/tradecraft-research.log",
    ),
    "kis_block_trader": RunnerSpec(
        key="kis_block_trader",
        pid_file="tradecraft-kis-block-trader.pid",
        pattern=(
            r"tradecraft-kis-block-trader|"
            r"tradecraft\.runtime\.kis_block_trader_runner|"
            r"kis_block_trader_runner\.py"
        ),
        label="KIS block trader runner",
        session_names=("tradecraft-kis-block-trader", "hermes-kis-block-trader"),
        command=".venv/bin/tradecraft-kis-block-trader",
        log_path=".runtime/kis_block_trader.log",
    ),
    "binance_block_trader": RunnerSpec(
        key="binance_block_trader",
        pid_file="tradecraft-binance-block-trader.pid",
        pattern=(
            r"tradecraft-binance-block-trader|"
            r"tradecraft\.runtime\.binance_block_trader_runner|"
            r"binance_block_trader_runner\.py"
        ),
        label="Binance block trader runner",
        session_names=(
            "tradecraft-binance-block-trader",
            "hermes-binance-block-trader",
        ),
        command=".venv/bin/tradecraft-binance-block-trader",
        log_path=".runtime/binance_block_trader.log",
    ),
    "crypto_market_research": RunnerSpec(
        key="crypto_market_research",
        pid_file="tradecraft-crypto-market-research.pid",
        pattern=(
            r"tradecraft-crypto-market-research|"
            r"tradecraft\.runtime\.crypto_market_research_runner|"
            r"crypto_market_research_runner\.py"
        ),
        label="crypto market research runner",
        session_names=(
            "tradecraft-crypto-market-research",
            "hermes-crypto-market-research",
        ),
        command=".venv/bin/tradecraft-crypto-market-research",
        log_path=".runtime/crypto_market_research.log",
    ),
    "crypto_pattern_lab": RunnerSpec(
        key="crypto_pattern_lab",
        pid_file="tradecraft-crypto-pattern-lab.pid",
        pattern=(
            r"tradecraft-crypto-pattern-lab|"
            r"tradecraft\.runtime\.crypto_pattern_lab_runner|"
            r"crypto_pattern_lab_runner\.py"
        ),
        label="crypto pattern lab runner",
        session_names=("tradecraft-crypto-pattern-lab", "hermes-crypto-pattern-lab"),
        command=".venv/bin/tradecraft-crypto-pattern-lab",
        log_path=".runtime/crypto_pattern_lab.log",
    ),
    "crypto_alpha": RunnerSpec(
        key="crypto_alpha",
        pid_file="tradecraft-crypto-alpha.pid",
        pattern=(
            r"tradecraft-crypto-alpha|tradecraft\.runtime\.crypto_alpha_runner|"
            r"crypto_alpha_runner\.py"
        ),
        label="crypto alpha runner",
        session_names=("tradecraft-crypto-alpha", "hermes-crypto-alpha"),
        command=".venv/bin/tradecraft-crypto-alpha",
        log_path=".runtime/crypto_alpha.log",
    ),
    "jue_wiki": RunnerSpec(
        key="jue_wiki",
        pid_file="tradecraft-jue-wiki.pid",
        pattern=(
            r"tradecraft-jue-wiki|tradecraft\.runtime\.jue_wiki_runner|"
            r"jue_wiki_runner\.py"
        ),
        label="Jue wiki runner",
        session_names=("tradecraft-jue-wiki", "hermes-jue-wiki"),
        command=".venv/bin/tradecraft-jue-wiki",
        log_path=".runtime/jue_wiki_runner.log",
    ),
    "investment_memory": RunnerSpec(
        key="investment_memory",
        pid_file="tradecraft-investment-memory.pid",
        pattern=(
            r"tradecraft-investment-memory|"
            r"tradecraft\.runtime\.investment_memory_runner|"
            r"investment_memory_runner\.py"
        ),
        label="investment memory runner",
        session_names=("tradecraft-investment-memory", "hermes-investment-memory"),
        command=".venv/bin/tradecraft-investment-memory",
        log_path=".runtime/investment_memory.log",
    ),
    "live_evaluator": RunnerSpec(
        key="live_evaluator",
        pid_file="tradecraft-live-evaluator.pid",
        pattern=(
            r"tradecraft-live-evaluator|"
            r"tradecraft\.runtime\.live_evaluator_runner|"
            r"live_evaluator_runner\.py"
        ),
        label="live evaluator runner",
        session_names=("tradecraft-live-evaluator", "hermes-live-evaluator"),
        command=".venv/bin/tradecraft-live-evaluator",
        log_path=".runtime/live_evaluator.log",
    ),
    "naver_reports": RunnerSpec(
        key="naver_reports",
        pid_file="tradecraft-naver-reports.pid",
        pattern=(
            r"tradecraft-naver-reports|tradecraft\.runtime\.naver_reports_runner|"
            r"naver_reports_runner\.py"
        ),
        label="reports crawler",
        session_names=("tradecraft-naver-reports", "hermes-naver-reports"),
        command=".venv/bin/tradecraft-naver-reports",
        log_path=".runtime/naver_reports.log",
    ),
    "strategy_insights": RunnerSpec(
        key="strategy_insights",
        pid_file="tradecraft-strategy-insights.pid",
        pattern=(
            r"tradecraft-strategy-insights|"
            r"tradecraft\.runtime\.strategy_insights_runner|"
            r"strategy_insights_runner\.py"
        ),
        label="strategy insight runner",
        session_names=("tradecraft-strategy-insights", "hermes-strategy-insights"),
        command=".venv/bin/tradecraft-strategy-insights",
        log_path=".runtime/strategy_insights.log",
    ),
    "market_judge": RunnerSpec(
        key="market_judge",
        pid_file="tradecraft-market-judge.pid",
        pattern=(
            r"tradecraft-market-judge|tradecraft\.runtime\.market_judge_runner|"
            r"market_judge_runner\.py"
        ),
        label="market judge runner",
        session_names=("tradecraft-market-judge", "hermes-market-judge"),
        command=".venv/bin/tradecraft-market-judge",
        log_path=".runtime/market_judge.log",
    ),
    "market_pulse": RunnerSpec(
        key="market_pulse",
        pid_file="tradecraft-market-pulse.pid",
        pattern=(
            r"tradecraft-market-pulse|tradecraft\.runtime\.market_pulse_runner|"
            r"market_pulse_runner\.py"
        ),
        label="market pulse runner",
        session_names=("tradecraft-market-pulse", "hermes-market-pulse"),
        command=".venv/bin/tradecraft-market-pulse",
        log_path=".runtime/market_pulse.log",
    ),
    "watchdog": RunnerSpec(
        key="watchdog",
        pid_file="tradecraft-watchdog.pid",
        pattern=(
            r"tradecraft-watchdog|tradecraft\.runtime\.watchdog_runner|"
            r"watchdog_runner\.py"
        ),
        label="watchdog runner",
        session_names=("tradecraft-watchdog", "hermes-watchdog"),
        command=".venv/bin/tradecraft-watchdog",
        log_path=".runtime/watchdog.log",
    ),
}

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
```

- [ ] **Step 4: Update `process_status.py` to derive maps**

In `src/tradecraft/runtime/process_status.py`, replace the current `RunnerRestartSpec` dataclass and the four duplicated maps with imports and derived maps:

```python
from tradecraft.runtime.runner_manifest import (
    DEFAULT_RESTART_RUNNER_KEYS,
    RUNNER_SPECS,
    RunnerSpec,
)

RUNNER_PID_FILES: dict[str, str] = {
    key: spec.pid_file for key, spec in RUNNER_SPECS.items()
}
RUNNER_PATTERNS: dict[str, str] = {
    key: spec.pattern for key, spec in RUNNER_SPECS.items()
}
RUNNER_LABELS: dict[str, str] = {
    key: spec.label for key, spec in RUNNER_SPECS.items()
}
RUNNER_RESTART_SPECS: dict[str, RunnerSpec] = dict(RUNNER_SPECS)
```

Keep the rest of `process_status.py` unchanged. Existing code can continue to call `spec.primary_session`, `spec.session_names`, `spec.command`, and `spec.log_path` because `RunnerSpec` exposes the same attributes.

- [ ] **Step 5: Run process status tests**

Run:

```bash
pytest tests/test_process_status.py -q
```

Expected:

```text
.....................
```

- [ ] **Step 6: Run entrypoint tests**

Run:

```bash
pytest tests/test_entrypoints.py -q
```

Expected:

```text
..
```

- [ ] **Step 7: Commit runner manifest extraction**

Run:

```bash
git add src/tradecraft/runtime/runner_manifest.py src/tradecraft/runtime/process_status.py tests/test_process_status.py
git commit -m "refactor: centralize runner process manifest"
```

Expected: commit succeeds.

## Task 5: Document Follow-Up Decomposition Boundaries

**Files:**
- Modify: `docs/spec/16_refactor_roadmap.md`
- Test: `tests/test_docs_spec.py`

- [ ] **Step 1: Add roadmap section**

Append this section after the existing `## Refactor Acceptance Criteria` section in `docs/spec/16_refactor_roadmap.md`:

```markdown
## Next Decomposition Plans

After the structure-stabilization pass, split future work into separate plans:

1. Control/API decomposition: move service construction and ops readiness helpers out of `src/tradecraft/main.py` while preserving route auth behavior and API payload compatibility.
2. Memory decomposition: split `src/tradecraft/services/investment_memory.py` into repository, context compaction, rituals/reflections, policy reviews, and public payload helpers.
3. Binance block-trader decomposition: split ledger access, manager run orchestration, executor ticks, status payloads, and prompt/wiki context helpers without changing order behavior.
4. KIS block-trader decomposition: split ledger access, KRX/session-aware manager orchestration, executor/reconciliation, status payloads, and prompt/wiki context helpers without changing order behavior.
5. UI information architecture: keep the static frontend, but consolidate navigation into operator groups and prevent `app.js`/`style.css` from absorbing new tab-specific logic.
```

- [ ] **Step 2: Add doc test for the decomposition section**

Append this test to `tests/test_docs_spec.py`:

```python
def test_refactor_roadmap_documents_next_decomposition_plans() -> None:
    roadmap = _doc("16_refactor_roadmap.md")

    assert "## Next Decomposition Plans" in roadmap
    assert "Control/API decomposition" in roadmap
    assert "Memory decomposition" in roadmap
    assert "Binance block-trader decomposition" in roadmap
    assert "KIS block-trader decomposition" in roadmap
    assert "UI information architecture" in roadmap
```

- [ ] **Step 3: Run the roadmap test**

Run:

```bash
pytest tests/test_docs_spec.py::test_refactor_roadmap_documents_next_decomposition_plans -q
```

Expected:

```text
.
```

- [ ] **Step 4: Commit roadmap update**

Run:

```bash
git add docs/spec/16_refactor_roadmap.md tests/test_docs_spec.py
git commit -m "docs: record Hermes refactor decomposition sequence"
```

Expected: commit succeeds.

## Task 6: Full Verification Before Handoff

**Files:**
- Read-only verification across the changed repo.

- [ ] **Step 1: Run contract check**

Run:

```bash
python3 scripts/check_project_contracts.py
```

Expected:

```text
Project contract check OK
```

- [ ] **Step 2: Run Jue workflow check**

Run:

```bash
python3 scripts/check_jue_workflows.py
```

Expected:

```text
Jue workflow check OK
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest tests/test_docs_spec.py tests/test_entrypoints.py tests/test_process_status.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 4: Run broader smoke tests**

Run:

```bash
pytest tests/test_api_smoke.py tests/test_admin_auth.py tests/test_ops_api_router.py tests/test_settings_api.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Check git status**

Run:

```bash
git status --short --branch
```

Expected:

```text
## codex/hermes-ju-block-trading-memory...origin/codex/hermes-ju-block-trading-memory [ahead N]
```

There should be no unstaged files.

- [ ] **Step 6: Restart only if code was deployed to the live workspace**

If these changes were made in the active `/Users/juhwan/hermes_v2` workspace, restart the running process set through the existing restart helper:

```bash
python3 - <<'PY'
from tradecraft.runtime.process_status import restart_runner_processes

keys = [
    "watchdog",
    "control",
    "runtime",
    "naver_reports",
    "kis_block_trader",
    "binance_block_trader",
    "investment_memory",
    "live_evaluator",
    "market_judge",
    "market_pulse",
    "crypto_market_research",
    "crypto_pattern_lab",
    "crypto_alpha",
    "jue_wiki",
    "strategy_insights",
]
print(restart_runner_processes(keys, delay_sec=0.5))
PY
```

Expected: scheduled restart result with all listed keys and no exception.

- [ ] **Step 7: Verify live readiness after restart**

Run:

```bash
python3 - <<'PY'
import json
import urllib.request
from tradecraft.config import AppSettings

settings = AppSettings()
token = settings.admin_token_list[0]
req = urllib.request.Request(
    "http://127.0.0.1:18080/api/ops/readiness?compact=true",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(req, timeout=15) as response:
    payload = json.loads(response.read().decode("utf-8"))
print(payload.get("status"))
print(payload.get("warnings") or [])
print(payload.get("blockers") or [])
PY
```

Expected: no stopped runner blockers. Existing Jue Wiki repair warnings may remain because this plan does not repair wiki content.

- [ ] **Step 8: Commit any verification-only doc updates**

If Task 6 caused only documentation/checklist edits, commit them:

```bash
git add docs/superpowers/plans/2026-07-06-hermes-structure-stabilization.md
git commit -m "docs: add Hermes structure stabilization plan"
```

Expected: commit succeeds. Do not bundle live runtime artifacts into this commit.

## Self-Review

**Spec coverage:** This plan covers the first stabilization slice from the structure review: contract checks, generated artifact cleanup, runner metadata centralization, and follow-up decomposition boundaries. It does not execute the later KIS/Binance/memory/UI splits; those are deliberately separate plan entries.

**Placeholder scan:** The plan contains exact file paths, commands, expected outputs, and code for new tests and new scripts. No task relies on unspecified trading behavior changes.

**Type consistency:** `RunnerSpec` intentionally exposes `primary_session`, `session_names`, `command`, and `log_path`, matching the existing `process_status.py` restart code. `scripts/check_project_contracts.py` exposes the exact helper names imported by the new tests.
