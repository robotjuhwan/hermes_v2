# HERMES/Jue Aggressive Multi-Timeframe Trading Design

Date: 2026-07-11
Status: approved design; implementation not started

## 1. Objective

HERMES/Jue will add an aggressive but evidence-gated trading posture for KIS
and Binance. The posture uses one reproducible strategy family—multi-timeframe
trend and breakout confirmation—rather than unrestricted LLM discretion or a
mixture of unrelated strategies.

The design has five fixed portfolio constraints:

- maximum drawdown is 12% for KIS and 12% for Binance, measured independently;
- maximum stop-defined risk is 0.75% of the applicable venue equity per symbol;
- each venue may hold at most six active symbols;
- Binance futures use isolated margin with leverage capped at 3x;
- KIS uses cash positions and makes Naver research a primary decision input.

The 12% drawdown value is a hard loss boundary, not a return promise. Full risk
is available only after venue-specific validation and exchange-fill evidence.
Existing API paths, environment aliases, kill switches, order defaults, and
paper/live semantics remain compatible.

## 2. Current Constraints

The existing application already has useful boundaries:

- Naver report collection and `NaverReportRepository`;
- strategy intelligence and symbol-linked report candidates;
- `research_spine` and `decision_packet_v2` inputs for the KIS manager;
- venue-specific live-authority gates;
- `ManagerRunTelemetryV1` and fill-provenance separation;
- block, order, fill, reflection, and policy-review records.

The current performance evidence does not justify immediate scale-up. Recorded
history includes weak or negative lanes, insufficient active-revision samples,
and many fills whose provenance is not exchange-proven. KIS also currently has
more than the target six positions. The design therefore extends the existing
authority and telemetry structures instead of bypassing them.

## 3. Chosen Method and Rejected Alternatives

### 3.1 Chosen: multi-timeframe trend ensemble

The chosen method evaluates the same trend/breakout thesis at fast, medium, and
slow horizons. A symbol has one external position and one aggregate risk budget,
with internal horizon tranches used for staged entry and exit.

This keeps the strategy attributable and testable while allowing the requested
composite time-horizon behavior. It is based on the empirically documented
time-series momentum family, with conservative volatility scaling rather than
unbounded leverage.

### 3.2 Rejected for the first implementation

- Regime-switching trend plus mean reversion is deferred because classifier
  error and parameter selection would materially increase overfitting risk.
- News-only or LLM-discretionary catalyst trading is deferred as a primary
  method because attribution and reproducibility are currently inadequate.
- A shared KIS/Binance trading base class is not introduced. The venues retain
  separate risk, accounting, research, margin, and execution behavior.

Research and news may confirm, rank, veto, or reduce a candidate. They do not
form a separate unbounded trading strategy and cannot directly place an order.

## 4. Architecture and Contracts

The feature is split into independently testable units.

### 4.1 `MultiHorizonSignalV1`

Produces a deterministic signal per venue and symbol:

- venue, symbol, evaluated timestamp, and market-data freshness;
- fast, medium, and slow direction, strength, volatility, and invalidation;
- agreement count and agreed direction;
- entry trigger, initial stop reference, and time expiry;
- source-bar identities and calculation version.

The signal builder does not know account balances, call an LLM, or place orders.
Missing or stale bars yield an unavailable signal rather than a neutral value.

### 4.2 `KisResearchEvidenceV1`

Normalizes each Naver or equivalent KIS research item:

- report ID, source URL or archive identity, broker, publication timestamp;
- exact symbol identity and symbol-link confidence;
- recommendation, target price, earnings estimates, valuation observations;
- catalysts, risks, and source spans supporting extracted facts;
- confirmed facts, model interpretation, missing fields, and conflicts;
- extraction version, freshness class, and confidence.

Raw report text is retained in its existing repository. Managers receive the
normalized evidence and bounded supporting excerpts, not uncontrolled full PDFs.

### 4.3 `KisResearchPacketV2`

Creates one decision packet per symbol by deduplicating reports and comparing
the newest evidence with prior evidence. It calculates:

- target-price, earnings-estimate, and recommendation revisions;
- broker agreement or conflict;
- thesis, catalyst, and downside-risk summaries;
- evidence freshness and symbol-identity quality;
- eligibility for discovery, entry confirmation, holding review, or veto.

`research_spine` remains the canonical delivery boundary. The packet is added
there instead of creating a competing raw-report source of truth.

### 4.4 `UnifiedRiskIntentV1`

Converts an approved signal into a venue-specific risk intent:

- venue equity snapshot and drawdown state;
- entry, initial stop, risk per unit, fee/slippage/gap allowance;
- target aggregate risk and horizon-tranche allocation;
- correlation-cluster risk and venue open-risk totals;
- proposed quantity, leverage/margin mode, and all applied caps;
- rejection or reduction reasons.

Quantity is derived from stop distance and costs. Binance leverage never
determines the desired loss amount; it only constrains margin use. KIS rounds to
valid cash quantities, and Binance rounds to exchange filters.

### 4.5 Existing authority and telemetry integration

The manager proposes a block from the research and signal contracts. Existing
live-authority, risk, execution, reconciliation, and kill-switch gates remain
final authority. `ManagerRunTelemetryV1` is extended or reused to record each
input, gate result, order, fill, cost, and outcome without introducing a second
performance ledger.

## 5. KIS Naver Research Upgrade

Naver research becomes active throughout the KIS block lifecycle.

### 5.1 Collection and normalization

- Preserve the current runner, repository, PDF archive, metadata repair, symbol
  backfill, and RAG synchronization boundaries.
- Extract source-linked structured facts and record the extraction version.
- Resolve ticker/name aliases deterministically and expose ambiguous links.
- Deduplicate repeated publication pages and materially identical reports.
- Track collection freshness separately from report publication freshness.

### 5.2 Decision use

Research participates in five decisions:

1. discover symbols with positive estimate, target, or thesis revisions;
2. confirm or reject a multi-timeframe entry;
3. monitor held symbols for thesis and estimate changes;
4. block additions and trigger a reduction review after material deterioration;
5. attribute the closed-trade result to the evidence used at entry.

A new KIS individual-stock position requires fresh, correctly linked, traceable
research evidence plus a valid 2-of-3 multi-timeframe signal. An ETF may satisfy
the research requirement through current ETF, index, sector, or asset-exposure
research when no company report is applicable.

Research evidence alone cannot create an order. Stale, ambiguous, boilerplate,
or conflicting evidence cannot support an aggressive entry. Conflict reduces
confidence and risk or produces `waiting_entry`; it is never silently merged
into a falsely unanimous thesis.

### 5.3 Holding review

When a new report changes a held symbol, the packet compares it with the entry
thesis. Material negative revisions immediately block additions. Reduction or
exit still requires the manager and deterministic gates to consider current
price, liquidity, stop state, and the multi-timeframe signal so one noisy report
does not cause an uncontrolled market order.

## 6. Multi-Timeframe Trading Rules

### 6.1 Horizons

| Venue | Fast | Medium | Slow |
|---|---|---|---|
| KIS | 2–5 trading days | 5–10 trading days | 10–20 trading days |
| Binance | 4–12 hours | 1–3 days | 3–7 days |

The exact bar features and thresholds are versioned strategy parameters selected
through walk-forward validation. Implementations may not optimize them against
the full historical sample and then report that same sample as validation.

### 6.2 Entry and addition

- At least two horizons must agree on direction.
- A 2-of-3 agreement may deploy at most 60% of the symbol risk budget.
- The remaining 40% may be added only after 3-of-3 confirmation or a profitable
  continuation confirmation defined before entry.
- Additions are prohibited when the position is losing or when they would worsen
  the predeclared invalidation structure.
- A stop may tighten but cannot be widened after entry.
- KIS remains long/cash. Binance explicitly selects spot long, futures long, or
  futures short; spot and futures exposure on the same symbol share one risk
  budget.

Each horizon may maintain an internal tranche identity for attribution and
partial exit. The venue sees a single net position, and all tranches use one
aggregate 0.75% maximum risk budget.

### 6.3 Exit

- Fast-only reversal reduces or closes the fast tranche.
- Two-horizon reversal closes most exposure and blocks additions.
- Three-horizon reversal, stop breach, or thesis invalidation closes the
  remaining strategy exposure subject to executable order safety.
- Positions that fail to progress within their declared horizon use a time stop.
- KIS material research deterioration triggers an immediate review and addition
  block; it does not widen a stop or create a new counter-position.

## 7. Portfolio and Drawdown Risk

Risk is calculated from the applicable venue equity and includes expected fees,
slippage, funding where applicable, and a gap/wick allowance.

- Maximum risk per symbol: 0.75%.
- Maximum active symbols per venue: six.
- Maximum nominal simultaneous stop risk per venue: 4.5%.
- Maximum aggregate risk for a strongly correlated sector, theme, or crypto
  cluster: 1.5%.
- Binance futures: isolated margin and maximum 3x leverage.
- Cross margin, loss-position averaging down, and stop widening are forbidden.

Drawdown is measured from each venue's own high-water mark. The drawdown
governor uses the lower of live-authority size and the following cap:

| Venue drawdown | Maximum new-symbol risk | Action |
|---|---:|---|
| less than 4% | 0.75% | validated posture allowed |
| 4% to less than 7% | 0.56% | reduce new risk |
| 7% to less than 10% | 0.375% | block unvalidated signals |
| 10% to less than 12% | 0% | halt new entries; manage existing risk |
| 12% or more | 0% | engage venue kill switch |

Recovery does not occur from time passage alone. The high-water mark, reconciled
equity, closed exchange fills, and current authority state must support it.

### 7.1 Existing KIS position migration

The existing KIS holdings are grandfathered for controlled normalization; they
do not make the six-position cap pass. No indiscriminate market liquidation is
performed. Holdings are ranked by research freshness, thesis integrity, trend
agreement, liquidity, loss risk, and execution feasibility. Weak holdings are
reduced first, and new-symbol entries remain blocked until there are six or
fewer active symbols unless a separate operator-approved migration exception is
defined. Existing protective exits always remain permitted.

## 8. Validation and Authority Promotion

Validation is calculated independently for KIS and Binance and for materially
different lanes such as Binance spot, futures long, and futures short.

The required sequence is:

1. deterministic historical replay with fees, slippage, gaps, and funding;
2. chronological walk-forward analysis;
3. untouched out-of-sample evaluation;
4. live shadow decisions with executable quote comparison;
5. restricted live trading with exchange-proven fills.

A strategy revision is eligible for `validated` only when all applicable checks
pass:

- net-of-cost expectancy is positive;
- profit factor is at least 1.20;
- validation maximum drawdown is at most 9%;
- Deflated Sharpe confidence is at least 95%;
- estimated Probability of Backtest Overfitting is at most 20%;
- performance is not concentrated in a single walk-forward window;
- every counted live order and fill has known provenance;
- adoption, rejected entries, unfilled records, and paper fills are excluded
  from exchange-fill alpha.

The live-authority risk ladder is:

| Authority | Maximum per-symbol risk |
|---|---:|
| `observe_only` | 0% |
| `restricted` | 0.1875% |
| `proving` | 0.375% |
| `validated` | 0.75% |

There is no calendar waiting period. Promotion happens as soon as the configured
sample, provenance, cost, performance, and drawdown contracts pass. The sample
floors are fixed before strategy evaluation:

- `restricted` requires passing replay, walk-forward, and untouched
  out-of-sample checks plus at least 30 executable live-shadow observations;
- `proving` requires at least 20 exchange-proven closed fills, positive
  net-of-cost expectancy, zero unknown-fill attribution, and live drawdown below
  4%;
- `validated` requires at least 60 exchange-proven closed fills or the
  statistically calculated minimum track record, whichever is greater, plus
  every validation threshold in this section.

These floors apply independently to materially different lanes. Parameter
trials, rejected variants, and the minimum-track-record calculation are stored
with the validation passport so the threshold cannot be selected after seeing
results. Authority degrades immediately when expectancy, fill quality,
validation, or drawdown ceases to satisfy its current grade.

## 9. Data Flow

For KIS:

```text
Naver collection -> repository/facts -> KisResearchPacketV2 -> research_spine
market bars -> MultiHorizonSignalV1 ---------------------------> KIS manager
account/positions/live authority -> UnifiedRiskIntentV1 -------> safety gates
safety gates -> block/order coordinator -> KIS reconciliation -> telemetry
telemetry -> reflection/validation -> venue authority
```

For Binance:

```text
crypto research/quant/funding/spread + market bars
    -> MultiHorizonSignalV1 -> Binance manager -> UnifiedRiskIntentV1
    -> futures/spot safety gates -> executor/reconciliation -> telemetry
    -> reflection/validation -> lane and venue authority
```

Research, account, and market contexts are generated once per manager cycle and
reused. API status readers consume stored snapshots and do not compute strategy
projections or write SQLite during readiness requests.

## 10. Failure Handling

- Stale or failed Naver collection blocks new KIS individual-stock risk while
  preserving existing stop, reduction, and reconciliation actions.
- Ambiguous symbol identity or missing source evidence excludes the report.
- Missing or stale market bars make the affected horizon unavailable. Fewer
  than two valid agreeing horizons cannot enter.
- Quote, account, position, order, or fill disagreement halts new orders and
  starts venue reconciliation.
- Prompt contract or budget violation results in zero LLM and order calls for
  that decision.
- Binance spread, funding, liquidation-distance, or exchange-filter failure
  produces `waiting_entry` or rejection.
- Telemetry or audit persistence failure halts creation of new risk because the
  outcome could not be attributed safely.
- The existing kill switches override every manager and promotion decision.

## 11. Testing Strategy

### 11.1 Unit tests

- horizon calculations, agreement, expiry, and stale-data behavior;
- report normalization, deduplication, revisions, conflicts, and symbol links;
- stop-distance sizing, fees, slippage, rounding, leverage, and cluster caps;
- drawdown thresholds and independent venue high-water marks;
- tranche additions, reversals, time stops, and no-stop-widening invariants.

### 11.2 Contract tests

- stable schemas for signals, research packets, risk intents, and telemetry;
- `research_spine` remains canonical and carries evidence IDs;
- LLM output cannot bypass deterministic authority or execution gates;
- adoption, paper fills, rejections, and unknown fills cannot enter live alpha;
- API paths, environment aliases, kill switches, and paper/live defaults remain
  backward compatible.

### 11.3 Replay and integration tests

- KIS and Binance historical replay with realistic cost stress;
- purged chronological walk-forward and untouched out-of-sample windows;
- PBO and Deflated Sharpe calculations with recorded parameter-trial counts;
- shadow price versus executable quote and final fill comparison;
- Naver outage, stale evidence, symbol ambiguity, partial service failure,
  exchange rejection, timeout, and reconciliation injection;
- migration behavior with more than six pre-existing KIS positions.

Tests must use isolated runtime paths established before importing application
modules. No test may read or write live `.runtime` databases or state files.

## 12. Operational Visibility

Stored snapshots and the existing venue views expose:

- time-horizon agreement and unavailable horizons;
- current research evidence, revisions, freshness, and conflicts;
- entry, stop, quantity, costs, and applied risk caps;
- symbol, cluster, venue open risk, and venue drawdown tier;
- authority grade, failed validation dimensions, and next promotion requirement;
- exchange-fill provenance and strategy/entry/execution/exit attribution.

This work does not require a broad UI redesign. Any later product UI change
must follow the repository's separate Lazyweb design process.

## 13. Implementation Boundaries and Sequence

Implementation is decomposed so strategy behavior is not mixed with a large
refactor:

1. freeze current prompt, risk, fill-attribution, and order-safety contracts;
2. add deterministic signal and research packet contracts with replay tests;
3. improve Naver normalization, revisions, and research-spine delivery;
4. add unified risk-intent calculation and drawdown governor;
5. integrate KIS manager and normalize existing positions;
6. integrate Binance lane selection and isolated-margin sizing;
7. add validation passport, shadow comparison, and authority promotion;
8. expose stored operational snapshots and run full verification;
9. request separate approval before changing any live setting or enabling new
   real-money authority.

No commit, live configuration change, order submission, or forced liquidation
is authorized by this design document alone.

## 14. Acceptance Criteria

- KIS and Binance produce deterministic three-horizon signals with reproducible
  source data and at least two agreeing horizons for every new entry.
- A symbol's aggregate stop-defined risk never exceeds the applicable authority
  cap or 0.75%, whichever is lower.
- Venue active symbols never grow above six; existing KIS holdings are reduced
  through the approved normalization flow.
- Venue open stop risk, correlation caps, and separate 12% drawdown governors
  are enforced before any order call.
- Binance futures never exceed isolated 3x leverage and pass liquidation and
  exchange-filter checks.
- Every KIS individual-stock entry cites fresh, correctly linked research; ETF
  entries cite applicable ETF/sector/index research.
- Naver revisions affect discovery, entry, holding review, addition blocking,
  and outcome attribution as designed.
- Validation metrics are net of costs and correct for trial selection; only
  exchange-proven fills contribute to live alpha.
- Contract, replay, failure-injection, domain, and full tests pass without live
  `.runtime` access.
- No live authority is increased until the user separately approves the live
  setting change after reviewing validation evidence.

## 15. Empirical References

- Moskowitz, Ooi, and Pedersen, *Time Series Momentum*, Journal of Financial
  Economics 104 (2012):
  https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf
- Moreira and Muir, *Volatility Managed Portfolios*, Journal of Finance 72
  (2017): https://www.nber.org/papers/w22208
- Bailey, Borwein, López de Prado, and Zhu, *The Probability of Backtest
  Overfitting*: https://escholarship.org/uc/item/4w1110bb
- Bailey and López de Prado, *The Deflated Sharpe Ratio*:
  https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf

## 16. Self-Review

The design contains no placeholders or optional strategy branches. KIS and
Binance drawdown and authority are explicitly independent. Composite behavior
means multiple horizons within one attributable trend strategy, not a mixture
of unrelated strategies. The Naver research requirement applies to new KIS
individual-stock risk, with an explicit ETF research substitute. Existing
positions, authority promotion, failure modes, validation, and live-change
approval are all defined without bypassing current safety contracts.
