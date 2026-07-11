# Research & Memory

HERMES/쥬 uses research and memory as active block-trading inputs. These layers do not place orders directly; they shape candidate selection, block thesis, horizon, sizing caution, reflection, and policy memory before deterministic safety gates decide what can execute.

## Research Layers

| Layer | Storage | Purpose |
| --- | --- | --- |
| Naver Reports | `.runtime/naver_reports.db` | Korean report corpus, metadata, symbol identity, report evidence, extracted facts, report chunks, and stock/ETF report links. |
| RAG Store | `.runtime/rag_chroma` plus report DB | Chroma-backed retrieval over report/research text, with chunk metadata for report id, symbol, broker, date, title, PDF/detail URLs, linked symbols, and linked asset classes. |
| Valuation | `.runtime/symbol_fundamentals.db` | Naver/WiseReport valuation, quality, growth, overvaluation risk, and financial features used by symbol analysis and strategy suitability. |
| ETF Research | `.runtime/etf_research.db` | ETF universe, market snapshots, liquidity/momentum/core-fit/risk scores, score labels, stale/error states, and core allocation context. |
| Whale/세시반 Insights | `.runtime/strategy_insights.db` when configured, otherwise `.runtime/insights/whale_insight.jsonl` and `.runtime/insights/sesiban.jsonl` | External market/supply-demand insights: Whale Insight large-holder/institutional signals and 세시반 after-close flow, sector treemap, and closing candidates. |
| Daily Discovery | `.runtime/jue_daily_discovery.db` | Random/targeted Korean stock discovery from the symbol directory, followed by instant symbol analysis to surface study and block-candidate ideas. |
| Crypto Research | `.runtime/crypto_market_research.db`, `.runtime/crypto_quant.db`, `.runtime/crypto_pattern_lab.db`, `.runtime/crypto_alpha.db` | Binance-specific source, feature, quant, pattern, and alpha layers feeding Binance 쥬 context rather than KIS equity scoring. |

Naver reports are the core Korean equity evidence layer. `NaverReportRepository` stores `reports`, `report_chunks`, `report_facts`, `report_symbol_links`, and `symbol_directory`. The symbol directory is also an identity repair layer: candidate code-only or generic names should be resolved through `resolve_symbol_names`, KRX/pykrx refreshes, or linked-report evidence before 쥬 treats them as named trade candidates.

RAG is intentionally tied to provenance. `RAGStore` stores vectors in Chroma but keeps report metadata attached, including `report_id`, `chunk_index`, `symbol`, `title`, `broker`, `published_at`, `pdf_url`, `detail_url`, and linked symbol fields. Missing Chroma availability is an operational data gap, not a reason to fabricate report context.

Symbol analysis combines valuation, quote, reports, RAG chunks, block history, and recent memory into a saved symbol analysis. Daily discovery reuses that path with the `daily_random_deep_research` trigger, so discovery output can become memory-backed future context instead of a one-off screen result.

Crypto research stays venue-scoped. Binance 쥬 may share process lessons with KIS only through scoped memory, but crypto quant/pattern/alpha evidence should not be silently mixed into KIS stock suitability.

## Jue Wiki Layer

Jue Wiki is the compiled interpretation layer above RAG, block ledgers, and investment memory. RAG remains the retrieval layer for report/research text. KIS and Binance block ledgers remain the source of truth for blocks, orders, fills, rule events, and PnL. Investment memory remains the source of truth for reflections, policy revisions, journals, and learning provenance.

The wiki writes scoped Markdown pages under `.runtime/jue_wiki/` and indexes them in `.runtime/jue_wiki/wiki.db`. Manager prompts should prefer compact wiki context before raw memory or raw RAG chunks. Raw source references must remain attached so a wiki claim can be traced back to reports, block ledgers, or memory rows.

### Jue Wiki Selector

The Jue Wiki Selector is the Phase 2 retrieval contract for manager prompts. It
does not ask the model to browse every wiki page. It ranks candidate pages for a
specific decision target using scope, requested symbols, page types, confidence,
freshness, source-reference count, prompt budget, page count limits, and optional
lint exclusion. The selected context is returned with a `selection_run_id`,
ranked pages, rejected pages, source refs, and a budget report.

Every selection run must leave an audit trail in `.runtime/jue_wiki/wiki.db`.
`wiki_selection_runs` stores the request, status, selected/rejected counts,
prompt character count, and configured budget. `wiki_selection_pages` stores the
rank, score, reasons, penalties, included flag, and character count for each
selected or rejected page. A future refactor should preserve this trace because
it proves why a KIS manager, Binance manager, or market judge saw a page and why
other pages were excluded.

Prompt modes define how selected wiki context affects the decision packet:

- `observe`: run the selector and record traces, but keep existing prompt
  structure unchanged.
- `assist`: include selected wiki context and the budget report while keeping
  raw memory/RAG available inside normal caps.
- `primary`: make selected wiki context the primary compiled knowledge section;
  raw RAG, reports, memory rows, and live state are still available only as
  bounded evidence, not as unbounded parallel context.

The source-of-truth boundary is absolute in all modes. Wiki pages are compiled
interpretation. Reports, RAG metadata/chunks, KIS and Binance block ledgers,
live performance/edge stores, and `investment_memory.db` remain the source of
truth for facts, fills, outcomes, and learned policy provenance.

### Repair Loop And Lint Findings

Wiki lint findings are quality gates for compiled pages. Findings such as stale
pages, missing sources, oversized pages, weak provenance, or broken structure
must be visible through the API and runner state. The repair loop reads open
lint findings and records `wiki_repair_actions` rather than silently rewriting
history. Rebuildable problems are scheduled as page rebuilds; unresolved or
manual-only findings remain explicit so operators can inspect them.

Selector behavior may optionally exclude pages with open lint warnings. This is
useful for `primary` mode and conservative live trading, but it should remain a
configuration choice because a stale or warning-marked page can still be useful
in `observe` or diagnosis mode when the trace clearly marks the penalty.

### Playbook Compiler And Performance Projection

The playbook compiler turns repeated block reflections and validation lessons
into scoped playbook pages, currently for KIS and Binance. These pages should
compress lessons by lane, setup, symbol class, regime, and execution failure
pattern without replacing the original reflection rows. Their source references
must point back to block reflections or other durable memory records.

The performance projector attaches realized playbook metrics to wiki metadata in
`wiki_playbook_metrics`. Metrics include sample count, win rate, expectancy,
profit factor, max drawdown, average holding time, status, and reasons. These
metrics make playbooks falsifiable: a prompt can prefer a playbook only when the
compiled lesson and the outcome evidence still agree. Weak sample counts,
negative expectancy, or high drawdown should downgrade or caution the page
rather than deleting the underlying history.

### Applied Intelligence Loop

Phase 3 makes selected wiki context accountable after it reaches a live Jue
decision. The selector still chooses pages by scope, symbol, freshness,
confidence, provenance, lint status, and prompt budget, but every selected page
can now be linked to the manager run, block action, market judgment, horizon,
venue, and eventual outcome that followed.

The loop is:

1. A KIS manager, Binance manager, or market judge requests wiki context and
   receives a `selection_run_id` plus selected page ids.
2. The prompt stores compact `jue_wiki_application` metadata so the decision can
   later be joined to that selection run without replaying the full prompt.
3. Closed blocks, watch decisions, or judgment outcomes write page-level rows in
   `wiki_selection_outcomes`.
4. `JueWikiApplicationService.project_page_effectiveness()` aggregates outcomes
   into `wiki_page_effectiveness`.
5. The selector may apply a bounded effectiveness adjustment, controlled by
   `TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT` and
   `TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MAX_ADJUSTMENT`.
6. Mode recommendations remain advisory and visible in ops/UI; they do not
   bypass exchange safety gates, kill switches, reconciliation, or deterministic
   block-rule execution.

Applied intelligence is therefore feedback for context ranking and operator
trust, not a hidden order rule.

## Memory Structure

| Storage | Purpose |
| --- | --- |
| `.runtime/investment_memory/` | Markdown persona, trading/update/Telegram policies, decision skills, journals, symbol notes, block notes, and generated policy-rule files. |
| `.runtime/investment_memory.db` | Provenance for memory runs, daily journals, memory insights, memory events, block reflections, policy changes, policy scorecards, period reviews, policy revisions/outcomes, policy rules, symbol analyses, and 쥬 lifecycle artifacts. |

The Markdown layer is 쥬's readable working memory. Default files include persona, trading policies, update policy, Telegram tone, block-manager skill, market-judge skill, risk-manager skill, reflection skill, symbol notes, and block notes.

The SQLite DB layer is the audit/provenance layer. It records why memory changed, what input/output was used, what confidence was attached, and which scope the memory belongs to. `memory_scope` separates core, KIS, and Binance reuse; `memory_type`, keys, notes, evidence, and Markdown content distinguish regime, block, symbol, sector, and policy lessons so they can be reused deliberately instead of leaking across venues.

Policy memory is a growth loop, not a hidden order rule. `policy_changes`, `policy_scorecards`, `policy_revisions`, and `policy_rules` can promote repeated observations into candidate soft data rules, but kill switch, cash limits, position limits, duplicate-order guards, and venue safety checks remain absolute.

When live authority carries trading-validation context into a block,
reflections preserve the compact failed disciplines, capacity bottleneck,
operator guidance, and failure attribution. Repeated failed disciplines build
`validation.{discipline_id}` scorecards. Repeated attribution groups build
`validation_attribution.{group_type}.{group}` scorecards, for example
`validation_attribution.strategy_family.late_chase`. These attribution rules
are soft caution/preference signals for sizing, entry timing, and target/stop
review; they must not become hidden bans. `context_pack.policy_rule_evaluation`
must preserve the matched attribution metric, such as
`{"group_type":"strategy_family","group":"late_chase"}`, under the affected
symbol when a current candidate matches the learned attribution.

## Lifecycle Artifacts

The financial-services absorption layer adds `jue_lifecycle_artifacts` to the
investment memory DB. These rows store structured analyst work products:
morning notes, idea screens, symbol deep dives, earnings updates, sector
rotation notes, valuation/model updates, portfolio balance reviews, and rejected
ideas. Each artifact has workflow provenance, a symbol when relevant, compact
Markdown summary, payload JSON, evidence JSON, status, and timestamps.

`InvestmentMemoryService.context_pack()` includes recent symbol-filtered
`lifecycle_artifacts`. KIS 쥬 then receives `decision_lifecycle_v3`, a compact
packet that turns lifecycle artifacts into accepted artifacts, flattened block
implications, and rejected artifact reasons. This makes a future block traceable
to the analyst workflow that produced the idea rather than only to the latest
manager-run prompt.

## Workflow-Guided Memory

Memory context now carries explicit 쥬 workflow packs where the ritual or
reflection has a defined operating procedure:

- `pre_open` loads `kis_pre_open`.
- `midday` loads `kis_intraday_manager`.
- `post_close` loads `kis_post_close`.
- `block_reflection` loads `block_reflection`.
- weekly/monthly policy work loads `policy_revision`.

`InvestmentMemoryService.context_pack()` also includes compact active insights
and lifecycle artifacts alongside seed memory, active policies, reflections,
scorecards, policy rules, period reviews, and scoped memory. This makes the
growth loop less dependent on the latest journal alone: repeated insights and
analyst work products can be visible to future KIS and Binance managers with
scope, evidence, and confidence attached.

The workflow pack is intentionally compacted before journal storage. Full
workflow assets remain in `src/tradecraft/jue/`; journals and DB rows store
enough provenance to prove which workflow, skill ids, contract ids, model policy,
authority, and safety gates shaped the reflection or ritual.

## Daily Rituals

- Pre-open mindset: prepares the day using account state, open blocks, market context, ETF/core status, and memory policies before the first active KIS decisions.
- Midday review: checks whether morning block assumptions still hold, especially short-term price/supply-demand changes and unwanted chase behavior.
- Post-close review: compresses the trading day into process lessons, LLM usage awareness, next-day cautions, and memory updates.
- Block reflection: runs after a block closes, errors, or otherwise becomes due for review; it extracts execution quality and lessons tied to the block id and symbol.
- Weekly/monthly compression where implemented: reviews periods, policy outcomes, and policy revision candidates so recurring lessons can mature from observation to preference or caution.

Ritual output is intentionally trading-native. When a memory note implies action, it should be expressed as block intent, validation trigger, target/stop adjustment, sizing caution, or policy memory rather than generic commentary.

## Reflection Loop

1. Block closes, errors, or receives a notable event.
2. Reflection run extracts thesis, path, rule execution, PnL, MFE/MAE, hold time, exit reason, memory scope, and lesson.
3. Symbol notes, block notes, memory insights, validation failure-attribution, and policy scorecard candidates are updated with provenance.
4. Period review and scorecard logic can promote repeated lessons into policy revision/rule candidates.
5. Active policies are injected into future manager runs as soft rules for block sizing, confirmation, target/stop interpretation, and caution.
6. Safety gates remain absolute and cannot be weakened by memory.

## Refactor Concerns

- Keep raw provenance separate from compressed memory; report chunks, RAG metadata, journal context, and LLM prompt snapshots should remain inspectable.
- Do not let old prompt snapshots leak into active identity; current persona, decision skills, and policies should be the live source.
- Separate KIS and Binance memory scopes; cross-venue lessons need explicit transferability, not automatic reuse.
- Separate observations/preferences/cautions from hard safety gates.
- Treat `data_coverage` and confidence as separate concepts in memory updates; a broad source set can still be low quality, and a narrow but fresh insight can still require confirmation.
- Preserve symbol identity repair: generic names, OCR/date-like symbols, ETF prefix-only names, and code-only names must be resolved before they influence active block decisions.
- Keep ETF/core memory semantics distinct from scalp-only blocks; ETF/core notes should emphasize exposure, allocation, rebalance, and stale research checks.
- Keep policy/reflection growth loop observable so 쥬's behavior changes can be traced back to closed blocks, period reviews, and scorecards.

## Wiki-First V3 Ownership And Migration

Jue Wiki V3 is the canonical decision-knowledge layer. RAG remains useful, but
it is not the live decision authority. The ownership boundary has four layers:

1. Source evidence owns immutable source identity, content hashes, observation
   time, and source location. `evidence_id` identifies this layer.
2. Candidate artifacts own extractor/compiler inputs and provenance before
   publication. `artifact_id` and its source evidence refs identify this layer.
3. Canonical Wiki V3 owns reviewed claims, relationships, pages, and immutable
   publications. `claim_id`, `page_id`, and `snapshot_id` identify this layer.
4. Decision application owns selected-page traces, manager/action references,
   shadow comparisons, outcomes, and readiness audits. It may measure or block
   use, but it does not rewrite evidence or a published snapshot on a read.

Prompt mode (`observe`, `assist`, `primary`) controls how selected knowledge is
presented to the model. Read mode (`shadow`, `prefer`, `required`) controls the
safety authority of Wiki context. These are independent contracts: changing a
prompt mode never promotes a read mode, and a recommendation never changes a
live setting automatically.

Read authority advances only through `shadow -> prefer -> required`. Promotion
requires a fresh venue-matched V3 snapshot, all ingest/compile/lint/publish and
index gates healthy, zero stale/conflicted/orphan/backlog degradation, at least
500 signed complete shadow comparisons, full snapshot attribution, no safety
gate loss, no required-path raw RAG injection, and successful outage recovery.
Eligibility is advisory until a separate operator-approved configuration change.
Rollback sets read mode back to `shadow`; it does not delete snapshots or change
prompt mode. A rollback or failed current build cannot use an older valid
snapshot to expand risk until the stored health and signed eligibility gates are
fresh and valid again.

RAG is retained for bounded repair discovery, evidence audit, historical
backfill, and index rebuild. Its output first becomes evidence/candidate data and
must pass the V3 compiler and lint/publish contracts. Direct raw-RAG injection is
not allowed on the `required` decision path.
