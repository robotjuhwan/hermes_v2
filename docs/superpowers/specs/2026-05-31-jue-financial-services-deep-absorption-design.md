# Jue Financial Services Deep Absorption Design

## Context

The first financial-services absorption pass created a file-based Jue registry:
skills, workflows, contracts, prompt-pack compilation, manager-run provenance,
and an operator status endpoint. That pass brought HERMES closer to the
Anthropic financial-services repository's core shape: agents own workflows,
vertical skills hold domain methods, and workflow assets are file-based.

The next pass should go deeper. The reference repository is not a trading bot;
it is a financial-workflow library for equity research, wealth management,
modeling, and managed agents. The useful idea for HERMES is not to copy the repo
verbatim. The useful idea is to turn Jue's trading process into repeatable
financial work products: morning note, idea screen, thesis tracker, catalyst
calendar, earnings setup, model refresh, sector overview, portfolio balance,
block proposal, execution review, and policy revision.

Primary references:

- <https://github.com/anthropics/financial-services>
- <https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md>
- <https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md>
- <https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/wealth-management/skills/portfolio-rebalance/SKILL.md>

## Brainstormed Approaches

### Approach A: Add More Skill Markdown Only

Add `earnings_analysis`, `sector_overview`, `morning_note`, and `model_update`
skills to `src/tradecraft/jue/skills`, then reference them from existing
workflows.

This is low risk and fast, but too shallow. Jue would receive more text, yet the
runtime would not gain lifecycle state, source provenance, or a better way to
prove that a trade flowed from a research workflow.

### Approach B: Build a Separate Research-Lab Subsystem

Create a separate Jue research lab with its own routes, DB, workers, UI, and
analysis lifecycle.

This is powerful but repeats a problem the project is trying to avoid: features
growing beside the trading engine instead of improving the current engine. It
would risk becoming another disconnected panel.

### Approach C: Upgrade the Current Jue Workflow Layer Into a Decision Lifecycle

Extend the existing Jue registry and decision packet rather than creating a
separate lab. Add richer financial-services-inspired skills, add contracts for
research stages, store lifecycle artifacts in the existing memory/provenance
system, and expose the lifecycle inside current KIS/Binance/Memory surfaces.

This is the recommended approach. It keeps HERMES centered on active block
trading while giving Jue a professional analyst workflow before and after each
block.

## Chosen Design

### 1. Source Manifest

Add a local source manifest that records which external financial-services
skills influenced which Jue skills and workflows. This manifest is not an
auto-sync mechanism and does not pull external code at runtime. It is a
provenance map for humans and tests.

Example fields:

- `source_id`
- `source_url`
- `source_vertical`
- `source_skill`
- `adopted_into`
- `adopted_principles`
- `excluded_principles`

This keeps the project honest about what was inspired by the reference repo
without turning HERMES into a dependency on another repository.

### 2. Expanded Jue Skill Catalog

Add finance-workflow skills that Jue can use as compact operating procedures:

- `earnings_preview`: pre-event setup, key metrics, scenario expectations.
- `earnings_analysis`: post-event beat/miss, guidance, thesis impact.
- `sector_overview`: sector map, value chain, rotation, leaders/laggards.
- `model_update`: estimate/valuation refresh after data changes.
- `morning_note`: compact daily synthesis for KIS pre-open.
- `valuation_frame`: ties fundamentals, peer context, and price burden into
  block-level target/stop thinking.

The existing skills stay:

- `idea_generation`
- `thesis_tracker`
- `catalyst_calendar`
- `portfolio_balance`
- `block_design`
- `risk_sizing`
- `execution_review`
- `policy_revision`
- `evidence_audit`
- `crypto_market_sweep`

### 3. Workflow Lifecycle

Add workflow manifests that express how Jue should move from research to action:

- `kis_morning_note`: pre-open market and position briefing.
- `kis_idea_screen`: candidate generation from reports, RAG, fundamentals,
  ETF/core context, market pulse, and daily discovery.
- `kis_symbol_deep_dive`: one-symbol thesis and valuation workup.
- `kis_earnings_update`: event-driven pre/post earnings workflow.
- `kis_sector_rotation`: sector overview plus leader/laggard watchlist.
- `portfolio_rebalance`: cash, horizon, ETF/core, and concentration review.

Existing workflows continue:

- `kis_pre_open`
- `kis_intraday_manager`
- `kis_post_close`
- `binance_cycle`
- `crypto_research`
- `block_reflection`
- `policy_revision`

### 4. Contracts

Add contracts so skill outputs are structured enough to influence blocks:

- `morning_note_contract`
- `earnings_event_contract`
- `sector_rotation_contract`
- `model_update_contract`
- `portfolio_rebalance_contract`
- `decision_lifecycle_contract`

Contracts should reject vague outputs. Examples of rejection reasons:

- missing symbol or asset class
- no evidence reference
- no invalidation condition
- no block implication
- no source freshness
- no model/valuation delta when claiming valuation change

### 5. Decision Packet v3

Extend the current decision packet layer into a lifecycle packet:

```json
{
  "lifecycle_stage": "morning_note|idea_screen|deep_dive|manager_run|reflection|policy_revision",
  "workflow_id": "kis_idea_screen",
  "source_manifest_ids": ["equity_research.idea_generation"],
  "thesis": {},
  "valuation": {},
  "catalysts": [],
  "sector_context": {},
  "portfolio_context": {},
  "block_implications": [],
  "evidence_gaps": [],
  "rejected_actions": []
}
```

This packet should be compact enough for prompts, but backed by full provenance
in DB and Markdown memory.

### 6. Memory Integration

Jue memory should store not only block reflections, but analyst work products:

- daily morning notes
- symbol thesis snapshots
- catalyst observations
- earnings event notes
- sector rotation notes
- model/valuation deltas
- rejected ideas and why they were rejected

These should feed future manager prompts through `InvestmentMemoryService.context_pack()`.
The manager should see compact high-signal summaries, not the full history.

### 7. UI Integration

Do not add a separate "Jue Lab" top-level island. Add visibility to existing
surfaces:

- Settings: source manifest and workflow health.
- Memory: analyst notes, lifecycle artifacts, active thesis snapshots.
- KIS Blocks: which analyst workflow influenced a block.
- Strategy/Research: idea screen and sector/earnings artifacts.

The operator should be able to answer:

- Why did Jue consider this symbol?
- Which workflow produced this idea?
- What thesis, catalyst, sector, valuation, and portfolio facts mattered?
- What did Jue reject?
- Which repeated lessons changed policy?

## Non-Goals

- Do not integrate paid MCP connectors from the reference repo.
- Do not copy long external skill text verbatim.
- Do not create a separate standalone research lab.
- Do not let research workflows submit orders directly.
- Do not weaken safety gates.
- Do not add hard trading bans as policy revisions.

## Success Criteria

- `scripts/check_jue_workflows.py` validates all new skills, workflows, contracts,
  and source manifest links.
- `/api/jue/workflows/status` includes the new workflows and reports no errors.
- KIS manager prompts include lifecycle summaries from memory/decision packet v3.
- Blocks can display the workflow and analyst artifacts that influenced them.
- Morning notes, symbol deep dives, sector rotation notes, and earnings notes are
  saved with provenance and compacted into future context.
- Tests prove that vague, evidence-free research artifacts do not become block
  implications.
