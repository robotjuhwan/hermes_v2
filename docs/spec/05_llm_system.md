# LLM System

## Model Roles

| Area | Intended Model | Reasoning | Role |
| --- | --- | --- | --- |
| KIS 쥬 | GPT-5.5 | xhigh when configured | Korean equities block manager, memory, strategy synthesis. |
| Binance 쥬 | GPT-5.5 | xhigh when configured | 24h crypto block manager with compact quant/research context. |
| Memory | GPT-5.5 unless configured otherwise | configured | Journals, reflection, policy updates, context compression. |
| Research/Report Intelligence | GPT-5.5 unless configured otherwise | configured | Report interpretation, strategy intelligence, RAG summaries. |

`src/tradecraft/services/codex_native.py` defaults to `gpt-5.5` and `xhigh`.
`src/tradecraft/config.py` exposes the global native runtime values through
`TRADECRAFT_LLM_MODEL`, `TRADECRAFT_LLM_REASONING_EFFORT`, and
`TRADECRAFT_CODEX_NATIVE_TIMEOUT_MS`. KIS 쥬 uses the shared native model, so
GPT-5.5/xhigh is the configured intent unless those environment settings change.
Binance 쥬 has venue-specific model settings:
`TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_MODEL` defaults to
`gpt-5.5`, and
`TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_REASONING_EFFORT` defaults to `xhigh`.
The crypto research and alpha runners also default their LLM model fields to
`gpt-5.5` with `xhigh`.

## Jue Workflow Registry

HERMES now keeps 쥬's repeatable judgment procedures in a package-local
registry under `src/tradecraft/jue/`.

| Asset | Location | Role |
| --- | --- | --- |
| Skills | `src/tradecraft/jue/skills/*.md` | Compact task skills for portfolio balance, evidence audit, scenario stress, block construction, reflection, crypto research, and policy revision. |
| Workflows | `src/tradecraft/jue/workflows/*.json` | Venue/task-specific workflow manifests such as `kis_pre_open`, `kis_intraday_manager`, `kis_post_close`, `block_reflection`, `crypto_research`, and `binance_cycle`. |
| Contracts | `src/tradecraft/jue/contracts/*.json` | Required input/output contracts for evidence packets, portfolio context, block proposals, risk limits, and reflection outcomes. |
| Source manifest | `src/tradecraft/jue/sources/*.json` | Provenance map from external financial-services patterns to local 쥬 skills, including adopted and excluded principles. |
| Registry | `src/tradecraft/services/jue_skill_registry.py` | Loads, validates, compacts, and exposes workflow prompt packs. |
| Checker | `scripts/check_jue_workflows.py` | Fails fast when referenced skills/contracts are missing or malformed. |

The registry is not a separate agent. It is a governance layer that makes each
LLM call declare which workflow, skills, contracts, model intent, authority, and
safety gates apply. KIS 쥬 receives `kis_intraday_manager`; Binance 쥬 receives
`binance_cycle`; crypto market research receives `crypto_research`; memory
rituals and block reflections receive the matching ritual/reflection workflow.

Manager runs persist workflow provenance where available: workflow id, version,
skill ids, contract ids, and source manifest links. Operators can inspect the
current loaded packs from `GET /api/jue/workflows/status`, source provenance
from `GET /api/jue/source-manifest`, and the settings page workflow panel.

## Decision Lifecycle Artifacts

Financial-services-inspired work products are stored as lifecycle artifacts in
the investment memory DB by `JueLifecycleRepository`. Each artifact carries an
artifact id, type, workflow id, symbol, title, compact summary, payload JSON,
evidence JSON, status, and timestamps. The lifecycle repository is deliberately
small: it stores analyst products such as morning notes, symbol deep dives,
earnings updates, sector rotation notes, model/valuation updates, portfolio
rebalance reviews, and rejected ideas without creating a detached research lab.

`build_decision_lifecycle_packet()` compacts these artifacts into
`decision_lifecycle_v3`. KIS manager runs receive this packet beside
`decision_packet_v2`: v2 describes current account/block/quote pressure, while
v3 links research work products to block implications and rejected actions. The
Memory page surfaces recent artifacts through `GET /api/jue/lifecycle/latest`.

## CodexNativeRuntime Responsibilities

- Normalize local LLM calls.
- Apply model, reasoning, timeout, and auth settings.
- Return normalized `{content, raw, usage}` payloads or explicit error payloads; manager-specific code parses and validates structured JSON actions.
- Preserve usage telemetry when configured.

`CodexNativeRuntime` supports Codex SDK native mode and an explicit `none` mode.
Native mode starts an ephemeral Codex thread with read-only sandboxing,
deny-all tool approval, the configured model/reasoning effort, optional native
output schema, and task-specific HERMES skills. It normalizes text, JSON,
code-fenced JSON, and stream-like line JSON into `{content, raw, usage}`.

Usage telemetry is written to `.runtime/llm_usage.db` when enabled. The recorded
component/operation comes from payload telemetry when present; KIS manager runs
send `component=kis_block_manager` and `operation=manager_run`. Telemetry stores
mode, model, status, latency, estimated or exact token counts, input/output
sizes, error text, payload keys, and timeout metadata.

## Prompt Inputs By Manager

### KIS 쥬

- account
- blocks
- quotes
- strategy
- investment_memory
- decision_packet_v2
- decision_lifecycle_v3
- market_pulse
- daily_discovery
- ETF research
- market judgment
- policy rules
- user directives

The primary KIS manager prompt is built in
`src/tradecraft/services/kis_block_trader.py`. It asks 쥬 to manage independent
KIS stock and ETF blocks and return JSON only. The prompt includes clock/session,
account, current block ledger, compact quotes, allocation reconciliation,
horizon allocation, ETF universe, ETF research, recent block events, market
judgment, market pulse, decision packet v2, decision lifecycle v3, daily
discovery when available, untrusted-data boundary notes, recent user directives,
and scoped investment memory. The separate adoption prompt is narrower: it lets
쥬 adopt unallocated existing KIS holdings into blocks without buy orders.

### Binance 쥬

- account
- blocks
- quotes
- crypto_market_research
- crypto_quant
- crypto_pattern_lab
- crypto_alpha
- Binance risk state
- venue-specific memory

The Binance manager prompt is built in
`src/tradecraft/services/binance_block_trader.py`. It asks 쥬 to manage
independent Binance spot and futures blocks and return JSON only. Inputs include
normalized spot/futures account state, scoped memory, crypto research,
crypto alpha, crypto quant, crypto pattern context, latest performance
scorecards, external candidates, configured plus research-expanded runtime
universes, and active blocks. The prompt separates spot and futures universes:
spot blocks must come from `market_universe.spot`; futures blocks must come from
`market_universe.futures`.

## Failure Handling

- LLM timeout should create an error manager run, not a fake empty successful decision.
- JSON parse failure should not create orders.
- Missing LLM should not trigger buy/sell fallback.
- Model/timeout/reasoning values should be visible in settings/readiness/usage telemetry.

In current behavior, unavailable native runtime, bridge error payloads, parsing
failures, and timeout exceptions create explicit `error` manager runs with empty
action lists. The managers persist the prompt and response/error evidence before
returning status to callers, and they do not synthesize deterministic buy/sell,
hold, ritual, or review content from a failed LLM call.
