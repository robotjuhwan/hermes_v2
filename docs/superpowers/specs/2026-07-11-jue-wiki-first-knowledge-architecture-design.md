# Jue Wiki-First Knowledge Architecture Design

## Status

Approved design. This document extends the Phase 2 Jue Wiki design from
2026-06-28. It does not replace the existing trading ledgers, live account
state, kill switches, or execution gates.

## Purpose

HERMES should treat Jue Wiki as Jue's canonical interpretation and learning
layer. Raw research remains immutable evidence, and operational databases
remain the source of truth for accounts, orders, fills, positions, blocks, and
PnL. Retrieval-augmented generation remains available as a repair and indexing
mechanism, but it stops owning knowledge and stops being the normal prompt
payload.

This follows the useful part of Andrej Karpathy's LLM Wiki model: preserve raw
sources, let an LLM maintain a persistent and interlinked synthesis under a
schema, and continuously lint the result for contradictions, stale claims,
orphan pages, and coverage gaps. The reference concept is documented at
<https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>.

## Decision

Adopt a Wiki-first, evidence-backed architecture with four separate ownership
layers:

1. Immutable evidence owns source documents and observations.
2. Operational ledgers own trading facts.
3. Jue Wiki owns interpretations, hypotheses, playbooks, policies, and lessons.
4. Search indexes are disposable projections of the Wiki and its evidence.

The normal manager path reads live state directly and selects compact Wiki
knowledge. It does not inject the raw RAG corpus into the prompt. Missing or
unreliable Wiki knowledge causes reduced risk or a deferred entry while a Wiki
repair is queued.

## Approaches Considered

### Keep RAG as the primary knowledge layer

This has the lowest migration cost but continues to mix retrieval quality with
knowledge quality. It also makes decision context large, weakly structured,
and difficult to audit or reproduce.

### Remove RAG and store everything only in Wiki pages

This is simple conceptually but unsafe. A generated synthesis must not replace
immutable source evidence, and a Wiki must not become the source of truth for
orders, fills, or account state.

### Wiki-first with immutable evidence and disposable retrieval

This is the selected approach. The Wiki becomes the durable interpretation
layer, while raw sources and trading ledgers retain their narrower truth
responsibilities. Retrieval becomes a tool used to repair and extend the Wiki.

## Ownership Boundaries

### Immutable evidence

Examples include Naver research reports, disclosures, web snapshots, exchange
and broker responses, extracted market research, and recorded research inputs.
Each source has a stable identity, source type, acquisition time, content hash,
and version. Wiki compilation never edits evidence in place.

### Operational truth

The existing account, quote, order, fill, position, block, and PnL stores remain
authoritative. Jue Wiki may reference their audit identifiers and summarize
historical outcomes, but it cannot mutate or override them. Live prices,
balances, positions, kill switches, and exchange constraints are read directly
from operational services at decision time.

### Canonical Jue knowledge

Jue Wiki owns:

- symbol and entity knowledge;
- market-regime interpretations;
- theses and counter-theses;
- strategy and execution playbooks;
- policy interpretations and safety lessons;
- failure modes, contradictions, and open knowledge gaps;
- outcome-backed reflections that pass promotion thresholds.

Structured Wiki records are canonical within this layer. Markdown pages,
manager packets, UI summaries, and reports are compiled projections of those
records.

### Search and indexing

FTS, BM25, page indexes, relationship graphs, and optional embeddings are
rebuildable artifacts. They must be reproducible from Wiki records and source
metadata, and their loss must not destroy canonical knowledge.

## Jue Wiki Page Contract

The target persistence contract is `JueWikiPageV3`. It contains:

- `page_id`, `page_type`, `scope`, `title`, and `summary`;
- structured `claims`;
- typed `relationships`;
- source references and source-content hashes;
- coverage, freshness, lint, and compilation metadata;
- page status and immutable version identity;
- compiler and schema versions.

Each claim contains:

- a stable `claim_id`;
- one of `fact`, `interpretation`, `hypothesis`, or `policy`;
- normalized claim content;
- applicable symbols, venues, strategies, and regimes;
- `valid_from` and optional `valid_to`;
- confidence and freshness state;
- one or more evidence identifiers for a verified claim;
- one of `draft`, `verified`, `stale`, `conflicted`, `superseded`, or
  `rejected`;
- provenance linking the claim to a compiler run or audited human action.

Relationships use explicit types such as `supports`, `contradicts`,
`supersedes`, `depends_on`, and `applies_to`. A conflict never silently
overwrites an older claim. Both claims remain addressable until evidence and
policy permit one to supersede the other.

A `verified` claim must have at least one resolvable evidence identifier and
content hash. A manager may use stale or conflicted claims as warnings or
counter-evidence, but not as the sole support for increasing risk.

## Write Path

1. A source event registers immutable evidence and its identity.
2. A versioned extractor creates normalized candidate facts and metadata, then
   persists its input identity, output, model, prompt, and configuration as an
   immutable candidate artifact.
3. The Wiki compiler compares candidate claims with current pages and
   relationships.
4. It creates a transactional page diff rather than mutating the current
   published version.
5. Lint checks provenance, schema, scope, freshness, conflicts, orphan pages,
   and missing relationships.
6. Only a complete, passing version is published.
7. Markdown, indexes, manager-ready packets, and UI projections are rebuilt
   from the published version.

Compilation is deterministic and idempotent for the same persisted candidate
artifact set, schema version, and compiler version. Re-extracting a raw source
creates a new candidate artifact rather than silently changing an older one. A
failed compilation leaves the previous published version intact.

Decision outcomes and reflections can write back only through audit-linked
candidate claims. A single win or loss cannot become a verified policy. A
lesson or playbook change requires fill provenance, cost-aware attribution, an
explicit, versioned sample threshold, and the existing policy-review gate. If a
promotion threshold is not configured for a venue and playbook type, automatic
promotion is prohibited.

## Read And Decision Path

The manager assembles a decision packet in this order:

1. Read live account, market, order, and safety state directly.
2. Select relevant verified Wiki claims and pages through the Wiki index and
   relationship graph.
3. Record the selected Wiki snapshot, claims, exclusions, freshness, and budget
   trace.
4. Decide only when coverage and quality satisfy the target manager contract.
5. If coverage is insufficient, queue raw-evidence retrieval and Wiki repair.

The live manager does not wait for an unbounded repair. A knowledge gap yields
`waiting_entry`, `observe_only`, or a reduced-risk decision according to the
existing venue policy. Exit management and kill switches continue to operate
from operational truth even when Wiki is unavailable.

Raw evidence may be read synchronously only by a bounded repair workflow. Its
result is not used to bypass Wiki provenance, lint, or publication contracts.

## Migration

Migration is controlled by verified gates, not dates.

### `shadow`

- Existing manager behavior remains authoritative.
- Wiki-first packets are built from the same recorded inputs.
- Candidate selection, safety gates, actions, latency, and prompt cost are
  compared without changing orders.
- New schema and publication paths are exercised against isolated data.

### `prefer`

- New Naver reports, research, and reflections are compiled into Wiki first.
- Managers use verified Wiki knowledge when coverage is sufficient.
- Missing coverage falls back to the existing safe behavior while scheduling
  repair; it does not authorize more risk.
- Existing RAG material is backfilled by domain and source identity.

### `required`

- Normal manager prompts contain no direct raw-RAG corpus injection.
- A valid, quality-qualified Wiki snapshot is required for aggressive new
  entries.
- Raw retrieval is limited to bounded repair, backfill, audit, and index rebuild
  workflows.

Read mode remains independently reversible so an operational rollback does not
delete Wiki data or change live trading configuration. Existing environment
aliases, API routes, paper/live defaults, and safety controls remain compatible.

## Failure And Recovery

- Source collection failure preserves the latest verified page and marks its
  dependent claims stale when their freshness contract expires.
- Compilation or lint failure publishes nothing and retains the previous
  snapshot.
- Contradictory evidence creates a conflict record instead of an overwrite.
- Index corruption triggers a rebuild from published Wiki records.
- Wiki repository failure blocks new risk expansion but does not block exits,
  reconciliation, or kill switches.
- A bad publication rolls back by immutable snapshot identity.
- Repair queues use bounded retries and expose the last failure; they do not
  spin indefinitely or hide an unhealthy source.
- Live databases, source evidence, and protected runtime artifacts are never
  deleted by Wiki repair or migration jobs.

## Observability And Audit

Each manager run records:

- Wiki mode and snapshot ID;
- selected and rejected page and claim IDs;
- source coverage and freshness;
- conflicts and safety-relevant knowledge gaps;
- context assembly latency and size;
- repair actions scheduled;
- resulting actions, execution outcome, and fill provenance.

Readiness exposes publication age, stale and conflicted claim counts, orphan
pages, failed compilations, repair backlog, index rebuild status, decision
coverage, and mode eligibility. Status endpoints read stored snapshots and do
not compile pages or mutate SQLite state.

## Verification Strategy

### Contract and unit tests

- Page, claim, relationship, source, and snapshot schema validation.
- Verified-claim provenance enforcement.
- Conflict and supersession state transitions.
- Deterministic compilation and transactional publication.
- Manager rejection of unsupported risk-increasing claims.
- Index rebuild equivalence.

### Integration tests

- Naver source to evidence to Wiki to KIS manager packet.
- Crypto research source to evidence to Wiki to Binance manager packet.
- Reflection and fill provenance to candidate lesson and policy review.
- Wiki outage with working exits, reconciliation, and kill switch.
- Repair failure with the prior Wiki snapshot preserved.
- Runtime-test isolation proving that live `.runtime` data is unchanged.

### Replay and shadow validation

- Compare existing and Wiki-first decisions using recorded manager inputs.
- Attribute every behavioral difference to selected claims and safety gates.
- Measure context generation time, prompt size, LLM use, actions, rejected
  actions, and execution outcomes.
- Do not enable `required` mode until the acceptance gates pass.

## Acceptance Gates

- One hundred percent of verified claims have resolvable evidence IDs and
  hashes.
- One hundred percent of Wiki-informed manager decisions record a snapshot ID.
- Existing order and risk safety gates have zero unexplained loss in replay.
- At least 500 recorded shadow decisions per venue cover KIS and Binance before
  required mode is eligible; both venues are assessed separately.
- Normal required-mode prompts contain zero direct raw-RAG corpus injections.
- Stale or conflicted evidence alone causes zero aggressive new entries.
- Wiki failure causes zero new risk expansion.
- Published Wiki versions and all search indexes can be reproduced from their
  persisted candidate artifact set, schema version, and compiler version.
- Adoption, failed or rejected entries, paper fills, exchange fills, realized
  PnL, and unrealized PnL remain separately attributed.

Passing these gates makes a mode eligible; it does not modify live settings or
activate trading. Any live configuration change remains a separately approved
operation.

## Scope And Non-Goals

This design covers the durable knowledge model, Wiki-first manager context,
migration modes, repair behavior, and verification. It does not redesign the
UI, expand strategy authority, replace venue-specific risk logic, delete the
existing RAG corpus, alter live leverage or exposure, or migrate operational
ledgers into Jue Wiki.

Implementation should be decomposed into independent changes: schema and
repository, compiler and lint, selector and manager contracts, source adapters,
migration modes, observability, and replay validation. A feature change and a
large refactor of the same area must not be combined.
