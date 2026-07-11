# Ops Process Boundary Design

## Context

The first helper-boundary iteration moved independent Jue workflow, crypto
payload, unavailable-service, and strategy collect-source logic out of
`src/tradecraft/main.py`. A re-scan shows `main.py` still owns process-liveness
helpers that are not trading-domain logic:

- ISO timestamp parsing for readiness scheduling.
- code mtime and stale-process annotation.
- duplicate-scan runner key policy.
- light runner status lookup with optional alive-process match scanning.
- core runner process map assembly for ops readiness.

These helpers sit next to Telegram, KIS/Binance status, and readiness payload
assembly in `main.py`, but their responsibility is operational process metadata.

## Goal

Move process-liveness and code-staleness helper logic out of `main.py` into a
focused API support module while preserving ops readiness payloads, runner
status semantics, and all trading behavior.

## Keep

- Keep `runner_process_status` and restart operations in
  `tradecraft.runtime.process_status`.
- Keep `build_core_runner_processes` and cover helpers in
  `tradecraft.api.ops_readiness` as the lower-level generic API helpers.
- Keep KIS/Binance block readiness payload assembly in `main.py` for this
  iteration.
- Keep tests that use `tradecraft.main` wrappers working, either by preserving
  thin compatibility wrappers or updating tests to the new helper when that is
  clearly better.

## Reduce

Move these pieces from `main.py`:

- `_iso_to_utc`
- `_max_code_mtime`
- `_process_with_code_staleness`
- `DUPLICATE_SCAN_RUNNER_KEYS`
- `_runner_process_status_light` implementation
- `_build_core_runner_processes` implementation
- `_light_process_with_staleness` implementation

`main.py` may keep thin wrappers for call sites that need app globals such as
`settings.research_enabled`, `runner_process_status`, and the package base path.

## Architecture

Create `src/tradecraft/api/ops_process_payloads.py`.

Responsibilities:

- Provide pure functions:
  - `iso_to_utc(value)`
  - `max_code_mtime(paths)`
  - `process_with_code_staleness(process, code_paths=...)`
  - `light_process_with_staleness(key, runner_status, code_paths=...)`
  - `build_app_core_runner_processes(base, runner_status, research_enabled)`
- Export `DUPLICATE_SCAN_RUNNER_KEYS`.
- Compose existing `tradecraft.api.ops_readiness` helpers rather than copying
  their generic behavior.

`main.py` imports these helpers and keeps the app-specific wrappers:

- `_runner_process_status_light(key)` passes `runner_process_status`.
- `_build_core_runner_processes()` passes `Path(__file__).resolve().parent` and
  `settings.research_enabled`.
- `_light_process_with_staleness(...)` passes `runner_process_status`.

This removes implementation details from `main.py` without changing how route
dependencies call the wrappers today.

## Data Flow

Ops readiness still calls `_build_core_runner_processes()` from `main.py`.
That wrapper delegates to `build_app_core_runner_processes`, which:

- queries runner status through the provided status function;
- applies cover semantics through `ops_readiness.runner_status_with_cover`;
- applies code staleness through `process_with_code_staleness`;
- adds `jue_wiki`;
- removes legacy `intelligence`;
- removes `research` when research is disabled.

KIS block status readiness still calls `_light_process_with_staleness` for KIS
and market-judge process rows. The wrapper delegates to the new module with the
same code paths and status function.

## Error Handling

- Invalid timestamps return `None` from `iso_to_utc` and produce empty next-run
  strings through the existing main scheduling helpers.
- Missing code paths return blank `code_mtime` and `None` epoch.
- Malformed process epochs do not mark a process stale.
- Staleness is only true for alive or direct-alive processes whose code mtime is
  more than one second newer than process start time.

## Testing

Add deterministic tests for the new module:

- timestamp parsing handles `Z`, naive timestamps, invalid values, and empty
  input;
- `process_with_code_staleness` marks alive processes stale only when code is
  newer than start time by more than one second;
- `light_process_with_staleness` uses the light runner status path and appends
  code staleness;
- `build_app_core_runner_processes` removes legacy intelligence, honors disabled
  research, includes Jue wiki, and applies code staleness.

Focused verification:

- `pytest tests/test_ops_process_payloads.py tests/test_ops_payloads.py tests/test_api_smoke.py -q`
- `python3 scripts/check_project_contracts.py`
- `git diff --check`
- `ruff check src/tradecraft/main.py src/tradecraft/api/ops_process_payloads.py tests/test_ops_process_payloads.py`

## Out Of Scope

- Changing restart commands or runner manifests.
- Changing ops readiness response keys.
- Changing KIS/Binance trading readiness semantics.
- Moving `_next_from_latest_or_krx_clock`; it couples generic timestamp math to
  KRX schedule policy and should stay in `main.py` until market-readiness
  extraction.
- Committing changes.

## Next Iteration Candidates

After this slice lands, the remaining safe `main.py` candidates are:

- ETF universe and auto-collect helpers.
- helper/research answer assembly.
- ops readiness section assembly.
- a separate plan for `investment_memory.py` decomposition.
