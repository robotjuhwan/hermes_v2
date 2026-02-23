# AGENTS.md
Repository guide for coding agents in `/Users/juhwan/hermes_v2`.

## Scope
- Primary scope: top-level TradeCraft app (`src/tradecraft`, `tests`, runtime entrypoints).
- Vendored subproject: `third_party/freqtrade` (has its own `third_party/freqtrade/AGENTS.md`).
- If touching vendored code, follow both this file and the subproject guide.

## Environment
- Python: `>=3.10` (`pyproject.toml`).
- Run commands from repo root.
- Runtime integrations are env-driven (`.env`).

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Build / Lint / Test Commands
Primary documented commands:
```bash
pytest
```

Optional (dependency exists, no strict top-level lint policy documented):
```bash
ruff check src tests
```

Pytest config (`pyproject.toml`):
- `testpaths = ["tests"]`
- `pythonpath = ["src"]`
- `addopts = "-q"`

No top-level documented `mypy` / `pyright` workflow.

## Single-Test Commands (important)
```bash
pytest tests/test_api_smoke.py
pytest tests/test_api_smoke.py::test_health_and_dashboard
pytest -k portfolio_coach
```

## Runtime Entry Points
```bash
tradecraft-control
tradecraft-runtime
tradecraft-research
tradecraft-kis-trader
tradecraft-naver-reports
```

## Codebase Map
- `src/tradecraft/main.py`: FastAPI app setup + route wiring.
- `src/tradecraft/config.py`: `AppSettings` and env aliases.
- `src/tradecraft/services/*`: adapters/bridges/domain services.
- `src/tradecraft/runtime/*`: runners/schedulers/state sync.
- `src/tradecraft/web/static/*`: frontend (`index.html`, `app.js`, `style.css`).
- `tests/*`: pytest tests.

## Python Style Rules

### Imports
- Prefer `from __future__ import annotations` in typed modules.
- Import order: stdlib -> third-party -> local `tradecraft.*`.
- Separate import groups with a blank line.

### Typing
- Use explicit return types on functions/methods.
- Use modern typing (`dict[str, Any]`, `list[T]`, `X | None`).
- Use dataclasses for structured config/data payloads where already established.
- Use `Protocol` for contract-style interfaces.

### Naming
- `snake_case`: variables/functions/methods/fields.
- `PascalCase`: classes.
- `UPPER_SNAKE_CASE`: constants.

### Error Handling
- Raise domain-specific exceptions in service adapters (example: `KISAPIError`).
- Validate external payloads before nested field access.
- Convert service failures to `HTTPException` in API layer with clear status/messages.
- Do not silently swallow exceptions.

### Logging
- Module logger pattern: `logger = logging.getLogger(__name__)`.
- Prefer structured logging (`logger.info("... %s", value)`).
- Keep logs concise and operational.

### Async / IO
- Use async for network/process IO (`httpx.AsyncClient`, async subprocess).
- Keep daemon polling loops only where architecture already uses sync loops.
- Set explicit timeouts on external calls.

## Frontend Style Rules
- Frontend is static (no bundler at top-level app).
- Use `camelCase` in JavaScript.
- Keep shared UI state centralized (single `state` object pattern).
- Centralize fetch + error parsing helpers.
- Use CSS variables (`:root`) and preserve responsive breakpoints.

## Testing Rules
- Use pytest function-style tests (`test_*`).
- Use `fastapi.testclient.TestClient` for API endpoints.
- Use `monkeypatch` for env/service isolation.
- Keep assertions deterministic with explicit expected structures.
- Use `pytest.approx(...)` for floating-point comparisons.

## Change Discipline
- Make surgical changes only.
- Match existing local style in touched files.
- Avoid unrelated refactors.
- Avoid introducing new dependencies unless required.
- Never alter secrets/credentials as part of normal code edits.
- Do not commit unless user explicitly requests commit.

## Cursor / Copilot Rules Check
Checked and not found at repo root:
- `.cursor/rules/**`
- `.cursorrules`
- `.github/copilot-instructions.md`

If added later, merge their instructions into this file.

## Agent Workflow Checklist
- Confirm scope and touched modules.
- Implement minimal change.
- Run focused tests first (single file/function where possible).
- Run broader `pytest` when change scope is larger.
- Verify no new diagnostics in changed files.
- Report changed paths and verification commands in handoff.

## Command Confidence Notes
- High confidence: `pytest`, single-file pytest, function-node pytest commands above.
- Medium confidence: `ruff check src tests` (tool exists as dev dependency; no strict policy section).
- Low confidence: any top-level build/type-check command not listed in this file.

## Practical Agent Defaults
- Prefer root app conventions when editing `src/tradecraft` and `tests`.
- Treat `third_party/freqtrade` as external-style code unless task explicitly targets it.
- Keep API responses backward-compatible unless requested otherwise.
- Preserve existing env alias behavior in `AppSettings` fields.
- For runtime jobs, avoid changing schedules/interval defaults unless requested.
- If uncertain between refactor vs fix, choose minimal fix and note tradeoffs.
