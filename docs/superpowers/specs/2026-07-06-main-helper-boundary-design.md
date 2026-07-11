# Main Helper Boundary Design

## Context

HERMES already completed a first structure-stabilization pass: project contract
checks were added, runner process metadata was centralized, and the refactor
roadmap now names the next decomposition sequence. The next safe step is the
first slice of Control/API decomposition.

`src/tradecraft/main.py` is still large because it combines service construction,
FastAPI app lifecycle, route dependency wiring, Telegram command handling,
readiness payload assembly, helper/research payload logic, and small pure
parsers. This design intentionally avoids changing trading behavior. It only
extracts helper logic that can be tested without live exchange adapters or
runtime databases.

## Goal

Reduce `main.py` by moving low-risk pure helper and fallback-service code into
focused modules, while preserving all endpoint paths, response shapes, runtime
service construction, admin auth, safety gates, and trading execution behavior.

## Keep

- Keep KIS and Binance trader construction in `main.py` for this iteration.
- Keep live/paper execution gates, readiness payloads, and kill-switch behavior
  unchanged.
- Keep existing route group registration through `build_*_route_specs`.
- Keep static UI files and route paths unchanged.
- Keep all current public function names in `main.py` that tests or route deps
  use, unless they are simple aliases to the new modules.

## Reduce

Move these independent pieces out of `main.py`:

- Jue workflow discovery and preferred ordering.
- Crypto research symbol parsing and kline interval parsing.
- Unavailable crypto research/alpha fallback service classes.
- Strategy insight collection source normalization, URL allow-listing, and safe
  runtime cache-path validation.

These pieces have no authority to submit orders, mutate trading ledgers, or
change manager decisions. They are good first extraction candidates because they
can be covered with deterministic unit tests.

## Architecture

Create small modules with one clear responsibility:

- `src/tradecraft/api/jue_workflow_payloads.py`
  - Owns the preferred workflow id order and available workflow id discovery.
  - Accepts a workflow root/path instead of importing app globals.
- `src/tradecraft/api/crypto_payloads.py`
  - Owns crypto symbol normalization and kline interval parsing.
  - Keeps the existing fallback interval defaults.
- `src/tradecraft/services/unavailable_services.py`
  - Owns lightweight unavailable-service adapters for optional crypto services.
  - Preserves current payload keys and status semantics.
- `src/tradecraft/services/strategy_collect_sources.py`
  - Owns source id aliasing, allowed-host checks, runtime cache path guarding,
    and safe source normalization.
  - Accepts `root` and configured source dictionaries as inputs so tests can
    avoid app-global mutation.

`main.py` imports these helpers and keeps the existing app-global objects,
route dependency wiring, and service construction. The extraction is therefore
behavior-preserving: route handlers still receive the same callables and
services, but the code that builds those callables becomes easier to inspect and
test.

## Data Flow

For Jue workflow route dependencies, `main.py` passes the registry workflow root
to `available_jue_workflow_ids`. The function returns preferred known workflows
first, then any discovered extra workflow ids in sorted order.

For crypto API route dependencies, `main.py` passes configured universe values
to `crypto_research_symbols`, and service builders use `parse_crypto_kline_intervals`
when constructing `CryptoMarketResearchConfig`.

For optional crypto services, `main.py` instantiates unavailable fallback
classes when optional imports fail. The fallback instances return the same
`status`, `latest_context`, `context_pack`, and async operation payloads as
before.

For strategy collection, `main.py` passes `settings.strategy_insight_source_list`
to `safe_strategy_collect_sources`. The helper normalizes source ids, rejects
unapproved hosts by replacing them with defaults, and only accepts cache paths
inside `.runtime/cache`.

## Error Handling

The extraction must preserve current fail-loud behavior:

- malformed strategy collect payloads still raise `HTTPException` in `main.py`
  before source normalization;
- optional crypto import failures remain visible through unavailable-service
  `reason` fields;
- invalid kline interval parts are ignored and existing default intervals are
  returned when no valid intervals are configured;
- unsafe strategy collect URLs and cache paths are replaced with approved
  defaults rather than being passed into collectors.

## Testing

Add deterministic tests for the extracted modules before moving production code:

- Jue workflow discovery keeps preferred ids first and appends unknown workflow
  json files sorted by id.
- Crypto symbol parsing accepts strings/lists, uppercases symbols, deduplicates
  order-preservingly, rejects invalid symbols, and preserves default kline
  interval behavior.
- Unavailable crypto fallbacks preserve current response keys and skipped async
  operation payloads.
- Strategy collect source normalization keeps allowed hosts, replaces unsafe
  hosts, constrains cache paths to `.runtime/cache`, honors source aliases, and
  filters unknown sources.

Focused verification:

- `pytest tests/test_jue_workflow_payloads.py tests/test_crypto_payloads.py tests/test_unavailable_services.py tests/test_strategy_collect_sources.py -q`
- `pytest tests/test_app_route_specs.py tests/test_app_routes.py tests/test_api_smoke.py -q`
- `python3 scripts/check_project_contracts.py`

## Out Of Scope

- Splitting KIS or Binance block trader services.
- Moving service construction out of `main.py`.
- Changing API payloads or endpoint paths.
- Changing UI navigation or static assets.
- Adding dependencies.
- Committing changes; repository instructions require explicit user request
  before commits.

## Next Iteration Candidates

After this slice lands and tests pass, re-scan the structure and choose one of:

- extract ETF universe helper logic from `main.py`;
- extract readiness staleness and process-cover helpers from `main.py`;
- plan a separate `investment_memory.py` decomposition;
- plan a separate Binance or KIS block-trader decomposition.
