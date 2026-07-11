# HERMES Jue Operational Readiness Green Design

Date: 2026-07-10
Status: approved architecture, written-spec review pending

## 1. Problem and Baseline

HERMES/Jue currently reports `status=yellow` even though no hard blocker is
present. The authenticated compact readiness response has no blockers, eight
operational warnings, and five strategy advisories. The global UI banner also
stays visible when only strategy advisories remain.

The current signals represent three different concerns as if they were one:

1. Actual operational faults, such as stale source code, stale report content,
   a hung reports crawler, or an oversized LLM prompt.
2. Recoverable maintenance work, such as a Jue Wiki repair queue that is making
   healthy progress.
3. Strategy restrictions, such as insufficient samples, poor cost-adjusted
   performance, or lane authority being reduced.

This conflation makes “operational warning” permanent and encourages unsafe
suppression. It also obscures real faults. In particular, compact readiness has
recently taken 12–38 seconds, the Naver Reports runner is alive but has not
completed its current stage, and Market Judge prompt input exceeds the warning
budget.

The baseline warning set is:

- `restart_required`
- `reports_db_stale`
- `llm_prompt_payload_large`
- `jue_wiki_repair_queue_open`
- `jue_wiki_requested_symbol_repair_pressure_open`
- `jue_wiki_financials_repair_pressure_open`
- `jue_wiki_requested_symbol_summaries_prompt_omitted`
- `jue_wiki_requested_symbol_summaries_degraded`

The baseline strategy advisory set is:

- KIS validation probe and reduced lane authority
- Binance diagnostic failures, validation probe, and reduced lane authority

These strategy restrictions are intentional safety controls and must not be
removed to make the UI green.

## 2. Goals

The change must produce an honest green operational state by fixing real
failures and separating strategy diagnostics from operations.

The completion contract is:

- `GET /api/ops/readiness?compact=true` returns `status=green`,
  `warnings=[]`, and `blockers=[]`.
- The global operational banner is hidden when there are no operational
  blockers or warnings.
- Strategy advisories remain visible in the KIS and Binance workspaces, and all
  existing validation restrictions remain effective.
- Compact readiness warm p95 is at most 500 ms.
- Full readiness cold p95 is at most 2 seconds.
- Readiness requests perform zero SQLite writes.
- All default runners are alive, source-fresh, and within their heartbeat or
  stage deadlines.
- Verification causes no real order, fill, live setting change, data deletion,
  or commit.

## 3. Non-goals

- Do not hide, rename, or downgrade an actual operational fault merely to make
  the UI green.
- Do not expand KIS or Binance strategy authority.
- Do not change kill switches, paper/live defaults, API paths, or existing
  environment-variable aliases.
- Do not redesign unrelated screens.
- Do not delete repair data, logs, report data, or protected rehearsal data.
- Do not introduce a shared KIS/Binance trading base class as part of this work.

## 4. Signal Contract

### 4.1 Operational readiness

The existing readiness payload remains backward compatible. Its fields have the
following precise meaning:

- `blockers`: a safety or availability failure that requires an immediate stop
  or prevents safe operation.
- `warnings`: an actionable operational abnormality that needs recovery or
  intervention but does not currently require a global stop.
- `advisories`: non-operational strategy, evidence, and optimization state.
- `status`: `red` when blockers exist, `yellow` when warnings exist, and `green`
  when both are empty. Advisories do not determine operational status.

No signal may be classified solely by how visually inconvenient it is. The
classification follows impact and required response.

### 4.2 Strategy status

Probe mode, sample shortage, cost/performance diagnostics, and lane-authority
limits remain advisories. Venue-specific screens own their display and explain
their effect on authority. The server-side validation gates remain the source of
truth; UI placement cannot enable an action that the server would reject.

### 4.3 Global banner

The global banner renders only operational blockers and warnings:

- blocker present: show the stop/critical state and recovery action;
- warning present: show the operational-check state and recovery action;
- neither present: hide the global banner, even if advisories exist.

Strategy advisories stay available through the KIS/Binance workspace summaries
and details. This prevents permanent global alarm fatigue without losing
strategy evidence or restrictions.

## 5. Architecture

### 5.1 Snapshot producer and read-only API

Readiness is divided into a producer path and a consumer path.

The `tradecraft-jue-wiki`/operations producer calculates expensive effectiveness
metrics, repair projections, provider aggregation, and derived readiness
sections. It writes a versioned `OpsSectionSnapshotV1` atomically after a
successful cycle. Each section includes its generation timestamp, source
timestamp, freshness deadline, status, and last-known-good provenance.

The readiness API only reads stored snapshots and lightweight process metadata.
Compact readiness directly assembles the compact contract; it does not build the
full payload and then discard fields. Independent lightweight providers may be
read in parallel, each with a timeout and last-known-good fallback. A request
must not update snapshots, repair queues, metrics, or databases.

If a snapshot is missing or too old, the API returns a bounded response with an
explicit operational warning. It never performs the expensive projection inline.

### 5.2 Runtime recovery coordinator

Process source freshness and liveness are separate checks. A source-stale runner
is recovered with a rolling restart:

1. restart one runner;
2. wait for a new PID, current source start time, and a healthy heartbeat;
3. verify the runner's domain state;
4. continue to the next runner only after success.

KIS live execution and Binance execution are never restarted simultaneously.
The coordinator preserves kill switches and does not submit any trading action.
Repeated restart failure remains an operational warning with the failed runner
and last error recorded.

### 5.3 Naver Reports stage supervision

Process liveness is insufficient for the reports crawler. Each crawl stage emits
`started_at`, `heartbeat_at`, `stage_name`, and `deadline_at`. The runner updates
the heartbeat during long stages. When a heartbeat or stage deadline is missed,
the runner terminates the stuck child operation, records a bounded failure, and
restarts the cycle with backoff.

Successful report persistence advances the report-content source timestamp. A
process restart alone does not clear `reports_db_stale`; only fresh persisted
content or a verified no-new-content result with source provenance can clear it.

### 5.4 Prompt budget contracts

Market Judge and KIS use the same invariant already enforced for the Binance
manager:

- runtime LLM inputs preserve the typed core payload;
- audit/storage compaction never leaks into runtime input;
- repeated context is generated once and reused within a cycle;
- optional evidence is ranked and bounded before serialization;
- compaction records original counts, included counts, omissions, and token/byte
  estimates;
- if the typed core cannot fit, the run terminates with
  `prompt_budget_contract_violation` before any LLM or order call.

The warning clears only after recorded prompt telemetry is below the warning
threshold for the configured healthy window. A single small prompt does not
erase a recent sustained breach.

### 5.5 Jue Wiki repair health

An open queue is work, not automatically an incident. Wiki operational health is
derived from progress and deadlines:

- warn when the oldest actionable item exceeds its deadline;
- warn when no item has completed within the expected progress window;
- warn when backlog growth exceeds completions for a sustained window;
- warn when the same repair key repeatedly fails beyond its retry policy;
- do not warn solely because `open_count > 0`;
- keep repair pressure, omissions, and degraded summary counts visible as
  workspace metrics/advisories when they are progressing within policy.

Queue insertion uses a stable repair identity so duplicate open work is merged.
Successful application resolves all equivalent open entries atomically. Failed
entries retain their audit history and bounded retry state. No historical audit
row is deleted to clear readiness.

## 6. Data Flow

1. Runtime services emit heartbeats, stage progress, prompt telemetry, and domain
   results.
2. The snapshot producer reads these sources, calculates bounded projections,
   classifies signals, and atomically publishes `OpsSectionSnapshotV1`.
3. The readiness API reads the latest snapshots plus lightweight process status,
   applies freshness checks, and returns compact or full output without writes.
4. The global UI renders only blockers/warnings. Venue workspaces render their
   strategy advisories and authority consequences.
5. The watchdog/recovery coordinator acts only on operational recovery policies,
   records outcomes, and never changes strategy authority.

## 7. Failure and Safety Handling

- A provider timeout returns the last-known-good section marked with its age; if
  the freshness deadline has passed, an operational warning is emitted.
- A corrupted or incompatible snapshot fails closed with a warning and does not
  trigger request-time recomputation.
- Runner recovery is serialized and bounded. Failed verification stops the
  rolling sequence.
- Prompt-contract violations stop before LLM and execution calls.
- Readiness and UI verification use read-only endpoints and deterministic test
  doubles. They do not invoke manager runs or order endpoints.
- KIS/Binance safety gates, validation restrictions, kill switches, and paper/live
  defaults remain unchanged and are tested explicitly.
- Cleanup, live configuration changes, and commits require separate user approval.

## 8. Implementation Order

The work proceeds as a continuous queue. Each item starts with a failing contract
test and must pass focused, domain, and broader verification before the next
item begins.

1. Lock signal classification and global-banner semantics.
2. Establish snapshot-only, read-only readiness and the compact direct builder.
3. Recover source-stale runners through verified rolling restarts.
4. Add Naver Reports stage heartbeat, timeout, and safe cycle recovery.
5. Enforce Market Judge and KIS prompt budget contracts and reduce current input.
6. Replace count-only Wiki warnings with progress/deadline health; deduplicate and
   resolve equivalent open repairs.
7. Apply the changes to the running system and remeasure all completion criteria.

Only one structural improvement and one independent feature may be in progress.
Feature behavior and a large refactor of the same area are not mixed in one
change.

## 9. Testing and Verification

### Contract tests

- `status` depends only on blockers and warnings.
- Advisories remain in the API but do not render the global banner.
- KIS/Binance workspace advisory details and server safety gates remain present.
- Prompt core types remain invariant at normal, warning, maximum, and overflow
  input sizes.
- Contract overflow causes zero LLM calls and zero order calls.

### Readiness and repository tests

- Compact and full builders read snapshots without SQLite writes.
- Missing, stale, and corrupt snapshots return bounded warning responses.
- Provider timeout and last-known-good behavior is deterministic.
- Wiki repair state tests cover progressing backlog, stalled backlog, overdue
  items, duplicate work, retries, and atomic resolution.

### Runtime tests

- Rolling restart proves new PID, source freshness, heartbeat, and domain health.
- Reports stage timeout terminates only the stuck child and resumes with backoff.
- Report freshness advances only after a valid persisted outcome.
- No verification path calls an order endpoint.

### Performance and safety checks

- Measure at least 20 warm compact requests and report p50/p95/max.
- Measure cold full readiness after cache/snapshot initialization and report p95.
- Trace SQLite mutations during readiness requests and require zero writes.
- Compare live `.runtime` database/state checksums and mtimes before and after the
  test suite; they must be identical.
- Run focused tests, domain tests, `scripts/verify.py fast`, and the appropriate
  broader suite before declaring completion.

## 10. Rollout and Rollback

Signal/UI semantics ship first because they make the contract explicit without
weakening safety. Runtime recovery then proceeds one process at a time. Snapshot,
reports supervision, prompt budgets, and Wiki policies are enabled only after
their focused tests pass.

Rollback is component-scoped:

- UI rendering can revert independently while retaining server classification.
- Snapshot consumption can fall back to the previous stored snapshot, not to
  request-time writes or expensive computation.
- A failed runner restart halts the rolling sequence and leaves other runners
  untouched.
- A prompt compaction regression fails closed under the existing safety gate.

## 11. Acceptance Record

At completion, record:

- baseline and final blocker/warning/advisory sets;
- changed files;
- focused, domain, fast, and broader verification commands;
- readiness p50/p95/max and SQLite write count;
- runner PIDs, source freshness, and heartbeat timestamps;
- live `.runtime` checksum/mtime comparison;
- confirmation that no order, fill, setting change, data deletion, or commit was
  performed;
- any remaining strategy advisories and why their restrictions remain correct.
