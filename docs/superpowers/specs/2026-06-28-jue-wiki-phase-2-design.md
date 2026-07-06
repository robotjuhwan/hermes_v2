# Jue Wiki Phase 2 Design

## Purpose

Phase 1 created Jue Wiki as a compiled Markdown knowledge layer above RAG, block ledgers, and investment memory. Phase 2 turns that layer into an operational decision substrate: Jue should not merely receive whatever wiki pages exist, but should receive the right pages, in the right order, under an explicit prompt budget, with stale or weak pages downgraded and performance-tested playbooks promoted.

The core question for Phase 2 is:

> When KIS Jue, Binance Jue, or the market judge makes a decision, can we prove which compact knowledge pages were selected, why they were selected, how fresh they were, how much prompt budget they consumed, and whether the playbooks inside them have actually worked?

## Brainstorming Options

### Option A: Add More Raw Data Sources

This would keep expanding research, market feeds, crawlers, and external sources. It helps coverage, but it worsens the original problem: prompt context grows, stale data becomes harder to detect, and Jue may see a noisy pile rather than an organized memory.

### Option B: Make The Wiki A Better Archive

This improves page quality, linting, and source references. It is useful, but still leaves the manager prompts with a weak retrieval strategy. A clean archive does not automatically mean a good decision packet.

### Option C: Make The Wiki A Budgeted Decision Layer

This adds a selector, quality gates, repair loop, playbook metrics, prompt budget reports, and manager integration modes. It keeps DBs as source of truth, but makes the wiki the first compact layer Jue consults. This is the recommended Phase 2 direction.

## Recommended Direction

Phase 2 should make Jue Wiki a budgeted, performance-aware decision layer.

The system should:

- Rank pages before each manager run.
- Prefer relevant, fresh, source-backed pages.
- Demote stale, oversized, source-poor, or scope-leaking pages.
- Compile strategy playbook pages from real block outcomes and reflections.
- Attach performance evidence to playbooks.
- Emit prompt budget reports for KIS, Binance, and market judge decisions.
- Keep raw RAG, raw memory, and live data available as evidence sources, but not as the default prompt bulk.

## Architecture

The existing `JueWikiService` remains the repository and compiler owner. Phase 2 adds focused collaborators:

- `JueWikiSelector`: chooses pages for a target decision using scope, symbols, lanes, regimes, block IDs, freshness, confidence, source count, lint health, and recent trading relevance.
- `JueWikiRepairService`: converts lint findings into concrete repair actions and safe page rebuild requests.
- `JueWikiPlaybookCompiler`: builds KIS and Binance playbook pages from reflections, policy rules, trading validation, and live performance data.
- `JueWikiPerformanceProjector`: projects win rate, expectancy, profit factor, drawdown, sample count, and status onto playbook pages.
- Manager prompt integration: KIS, Binance, and market judge use selected wiki context plus a budget report rather than blindly attaching a raw wiki pack.

## Data Flow

1. Source DBs keep raw truth:
   - KIS and Binance block ledgers
   - market judgment DB
   - investment memory DB
   - RAG/report/research DBs
   - live performance and validation DBs
2. Jue Wiki runner rebuilds pages and runs lint.
3. Repair loop resolves fixable page quality issues or marks unresolved findings.
4. Playbook compiler updates strategy, lane, symbol, and lesson pages.
5. Selector receives a decision request from KIS, Binance, or market judge.
6. Selector returns a ranked page list, compact content, source references, rejected page reasons, and budget report.
7. Manager prompt uses selected wiki first, live state second, raw evidence only when needed.
8. UI and ops expose health, selection traces, stale counts, repair actions, and budget pressure.

## Safety And Source Of Truth

Jue Wiki is not the order ledger, not the fill ledger, and not the account ledger. The wiki can influence judgment, but it cannot override live account state, exchange rules, kill switches, order gates, or block rule execution.

Errors should be visible. If selection, lint repair, or playbook projection fails, the API and runner state should show the failure. The system should not hide broken knowledge behind invented summaries.

## Success Criteria

- KIS, Binance, and market judge prompts include a wiki selection trace.
- Manager prompt payloads stay under the configured cap.
- Stale and lint-failing pages are excluded or explicitly marked.
- Playbook pages show real performance evidence and sample counts.
- UI can show which wiki pages influenced a decision.
- Ops readiness warns when the wiki runner stops, page quality decays, or prompt budget pressure rises.
- Raw context size decreases while decision traceability improves.
