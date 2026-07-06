# Trading Validation Lab

The Trading Validation Lab is the common verification layer for HERMES/쥬.
It turns the 19 practical auto-trading disciplines into a structured packet
that can be inspected by operators and injected into Jue's live authority
context.

The first implementation is `jue_validation_lab_v1`.

## Runtime Ownership

Primary code:

- `src/tradecraft/services/trading_validation.py`
- `src/tradecraft/services/kr_equity_pattern_lab.py`
- `src/tradecraft/runtime/live_evaluator_runner.py`
- `src/tradecraft/main.py`

Primary DB:

- `.runtime/trading_validation.db`
- `.runtime/kr_equity_pattern_lab.db`

Config:

- `TRADECRAFT_TRADING_VALIDATION_DB_PATH`
- `TRADECRAFT_TRADING_VALIDATION_MAX_AGE_SEC`
- `TRADECRAFT_TRADING_VALIDATION_PAYLOAD_COMPACTION_ENABLED`
- `TRADECRAFT_TRADING_VALIDATION_PAYLOAD_RECENT_ROWS_PER_GROUP`
- `TRADECRAFT_TRADING_VALIDATION_PAYLOAD_COMPACT_MIN_CHARS`
- `TRADECRAFT_BINANCE_VALIDATION_SPOT_FEE_RATE`
- `TRADECRAFT_BINANCE_VALIDATION_FUTURES_FEE_RATE`
- `TRADECRAFT_BINANCE_VALIDATION_SLIPPAGE_BPS`
- `TRADECRAFT_BINANCE_VALIDATION_INITIAL_EQUITY_USDT`
- `TRADECRAFT_KIS_VALIDATION_BUY_FEE_RATE`
- `TRADECRAFT_KIS_VALIDATION_SELL_FEE_RATE`
- `TRADECRAFT_KIS_VALIDATION_SELL_TAX_RATE`
- `TRADECRAFT_KIS_VALIDATION_SLIPPAGE_BPS`
- `TRADECRAFT_KIS_VALIDATION_SPREAD_BPS`
- `TRADECRAFT_KIS_VALIDATION_INITIAL_EQUITY_KRW`
- `TRADECRAFT_CRYPTO_PATTERN_LAB_DB_PATH`
- `TRADECRAFT_KR_EQUITY_PATTERN_LAB_DB_PATH`
- `TRADECRAFT_KR_EQUITY_PATTERN_LAB_ENABLED`

Historical `validation_runs.payload_json` rows are compacted by default after
live evaluator cycles. The latest rows per venue/scope/strategy revision keep
full detail for readiness and diagnosis; older oversized rows retain scores,
readiness, counts, and provenance metadata without carrying the full diagnostics
JSON forever.
- `TRADECRAFT_KR_EQUITY_PATTERN_LAB_MIN_SAMPLES`

The live evaluator refreshes validation packets for `kis` and `binance` after
syncing live block performance and before building the live authority payload.
This means KIS 쥬 and Binance 쥬 receive the latest validation summary through
their existing `live_authority` context.

Validation equity is venue-specific. KIS validation uses KRW account-scale
equity from `TRADECRAFT_KIS_VALIDATION_INITIAL_EQUITY_KRW`; Binance validation
uses USDT account-scale equity from
`TRADECRAFT_BINANCE_VALIDATION_INITIAL_EQUITY_USDT`. Do not run KRW realized PnL
against a small USD-style default or vice versa, because MDD, Calmar,
drawdown-budget, Kelly, and risk-of-ruin will be materially distorted.

## API Contract

Admin-token protected endpoints:

- `GET /api/trading/validation/status?venue=binance`
- `POST /api/trading/validation/run-once?venue=binance`

Readiness surface:

- `GET /api/ops/readiness` includes `trading_validation`.

Live authority surface:

- `GET /api/live/authority` includes `venues.<venue>.trading_validation`.
- `venues.<venue>.validation_gate` applies the latest trading validation summary
  to final budget authority.
- `trading_validation.stale=true` forces `validation_gate.status =
  validation_stale`, disables scale-up, and caps the venue to observe-only
  budget authority until a fresh validation run is available.

## Discipline Packet

Each validation run returns exactly 19 discipline rows. A row has:

- `id`
- `label`
- `purpose`
- `status`: `pass`, `warn`, `fail`, or `missing`
- `evidence`
- `action`
- `metric`

The packet also contains:

- `summary.total_score`
- `summary.readiness`: `research_only`, `probe`, `normal`, `scale_ready`, or
  `blocked_by_validation`
- `age_sec`, `max_age_sec`, `stale`, and `stale_reason`
- `metrics`
- `monte_carlo`
- `operator_guidance`

`metrics.failure_attribution` breaks weak live results down by symbol, horizon,
strategy family, and market regime. It exposes `worst_groups`, `best_groups`,
and `recovery_focus` so Jue can see whether losses came from a specific
instrument, holding period, entry family, or regime instead of treating a venue
failure as one undifferentiated stop signal. `operator_guidance` should surface
the highest-priority recovery focus before generic discipline actions.

## The 19 Disciplines

| # | Discipline | v1 Behavior |
|---|---|---|
| 1 | Data validation | Checks Jue-created filled live samples plus source metadata for stale quotes, upstream errors, fallback/proxy sources, missing cost models, and explicit invalid price/quantity fields. |
| 2 | Overfit validation | Reads `optimized_strategy_sets` from the crypto pattern lab and checks active/rejected sets, high-overfit counts, and train/test expectancy gap. |
| 3 | Walk-forward analysis | Reads pattern lab walk-forward quality summaries, requires active sets to carry rolling window evidence, and fails active sets that only have a legacy boolean pass flag. |
| 4 | Out-of-sample test | Uses crypto pattern lab OOS trade count, expectancy, profit factor, and drawdown when available; otherwise uses live block performance as a forward-sample proxy. |
| 5 | Monte Carlo simulation | Bootstraps realized trade returns and calculates final-return percentiles, tail expected shortfall, drawdown expected shortfall, loss-streak distribution, sequence risk level, and drawdown ruin probability. |
| 6 | Stress test | Replays metadata-provided crisis returns when available, otherwise applies live-return shock scenarios: liquidity shock, fee/slippage shock, and trend reversal. |
| 7 | Cost simulation | Reads gross PnL, net PnL, recorded costs, cost components, missing cost samples, and 2x/3x cost-stress net PnL from live performance. |
| 8 | Capacity analysis | Uses orderbook-depth and daily-turnover participation caps when present; otherwise falls back to metadata `capacity_usdt`/`capacity_krw` over notional. |
| 9 | Kelly sizing | Calculates full Kelly, 0.25 fractional Kelly, evidence quality, per-block risk cap, recommended risk fraction, and cap reason from win rate, payoff ratio, drawdown, and risk-of-ruin inputs. |
| 10 | MDD limit | Calculates equity-curve max drawdown plus current drawdown budget, peak/current equity, recovery-to-peak need, and risk multiplier guidance. |
| 11 | Sharpe ratio | Calculates trade-level Sharpe and shares the common risk-adjusted performance packet with volatility and quality grade. |
| 12 | Sortino ratio | Calculates downside-return Sortino and shares the common risk-adjusted performance packet with downside deviation and primary risk flag. |
| 13 | Calmar ratio | Calculates return-to-drawdown efficiency and shares the common risk-adjusted performance packet with Calmar and drawdown efficiency. |
| 14 | Profit factor | Calculates gross profit, gross loss, loss absorption ratio, average win/loss, edge grade, and Profit Factor. |
| 15 | Recovery factor | Calculates net profit divided by max drawdown cash loss plus whether max drawdown recovered and how many trades recovery required. |
| 16 | Risk of ruin | Builds a ruin profile from Monte Carlo drawdown threshold breaches, including probability, event count, time-to-ruin, severity, and governor action. |
| 17 | Regime test | Reads metadata `regime`, `market_regime`, or `regime_label`, then builds per-regime scorecards with best/worst regime, negative-regime count, and coverage. |
| 18 | Correlation | Uses rolling return windows when present; otherwise falls back to metadata cluster concentration via `correlation_cluster`, `sector`, or `asset_cluster`. |
| 19 | Factor exposure | Reads metadata `factor_exposures` or `factors`, then summarizes raw and notional-weighted factor concentration. |

## Metadata Inputs

Validation can deepen when live performance rows include structured metadata in
`source_json.metadata`:

- `regime`, `market_regime`, `regime_label`
- `horizon`, `time_horizon`, `holding_period`, `block_horizon`
- `strategy_family`, `family`, `lane`, `entry_setup`, `setup`
- `quote_status`, `data_status`, `status`
- `quote_stale`, `stale`
- `error_message`, `quote_error`
- `quote_source`, `book_source`, `source`
- `cost_model_status`
- `cost_components`, `cost_breakdown`
- `crisis_returns_pct`, `stress_returns_pct`, `crisis_replay_returns_pct`
- direct cost component keys when available: `fees`, `fee`, `taxes`, `tax`,
  `funding`, `funding_fee`, `slippage`, `spread`, `spread_cost`
- `entry_price`, `exit_price`, `qty` when the upstream component can provide
  explicit execution prices in metadata
- `capacity_usdt`, `capacity_krw`, `orderbook_capacity_usdt`
- `orderbook_depth_usdt`, `orderbook_depth_30bps_usdt`,
  `orderbook_depth_usdt_by_bps`
- `daily_turnover_usdt`, `daily_volume_usdt`, `quote_volume_usdt`
- `max_participation_rate`
- `notional_usdt`, `notional_krw`, `block_notional_usdt`, `position_notional`
- `correlation_cluster`, `sector`, `asset_cluster`
- `return_window_pct`, `returns_window_pct`, `rolling_returns_pct`
- `factor_exposures`, `factors`

Missing metadata must not be treated as success. The corresponding discipline
must remain `missing`, `warn`, or `fail` until evidence exists.

For data validation specifically:

- explicit invalid price/quantity is a hard failure
- upstream quote/orderbook/research errors warn or fail depending on affected
  sample concentration
- stale sources, fallback/proxy sources, and missing cost models produce warning
  pressure rather than a silent pass
- live performance rows that do not carry raw entry/exit fields are not marked
  invalid solely because the performance table stores only derived PnL fields

For cost simulation specifically, the packet exposes:

- `total_gross_pnl`, `total_net_pnl`, and `total_cost`
- `cost_drag_pct_of_gross_pnl`
- `net_retention_pct_of_gross_pnl`
- `breakeven_cost_multiplier`
- `stressed_net_pnl_by_cost_multiplier` for 1x, 2x, and 3x cost pressure
- `cost_by_component` such as fees, taxes, funding, slippage, spread, and
  unclassified costs
- `recorded_cost_sample_count`, `missing_cost_sample_count`,
  `missing_cost_sample_rate_pct`, and missing examples

Cost simulation may not pass when any cost samples are missing. If at least
half of the live outcomes lack a recorded or estimated cost model, the cost
simulation row fails because gross/net profitability cannot be trusted.

For Binance specifically, `live_evaluator_runner` enriches closed block
performance before validation. If explicit reflection costs are present, they
are marked `cost_model_status=recorded`. If Binance order/reflection rows do
not include commission or slippage, HERMES applies a conservative round-trip
notional estimate and marks it `cost_model_status=estimated_from_notional`.
This keeps validation from pretending cost is zero while still making the
estimate visible to Jue and operators.

For KIS specifically, `live_evaluator_runner` also estimates validation costs
when closed block rows lack explicit broker costs. It applies configurable
buy/sell commission rates, a configurable sell-side transaction tax rate, and
round-trip slippage bps to produce `cost_components`, `cost_model_status`,
`cost_source`, and `round_trip_notional_krw`. This prevents KIS gross PnL from
being treated as net PnL and stops data validation from flagging every KIS
  sample as `missing_cost` merely because broker cost fields were absent.
Known ETF blocks are exempted from the sell-side stock transaction tax estimate
using block/security metadata or recognizable ETF product names such as KODEX,
TIGER, ACE, KBSTAR, SOL, RISE, HANARO, ARIRANG, KOSEF, and TIMEFOLIO. Broker
commission and slippage estimates still apply.

For pattern-lab validation specifically, `status=active` is not proof of
walk-forward or out-of-sample validity. `TradingValidationService` tracks
`unknown_overfit_count`, `missing_out_of_sample_set_count`,
`out_of_sample_coverage_rate_pct`,
`active_missing_out_of_sample_set_count`, and
`active_out_of_sample_coverage_rate_pct`. It also tracks rolling WFA coverage:
`missing_walk_forward_set_count`, `walk_forward_coverage_rate_pct`,
`active_missing_walk_forward_set_count`,
`active_walk_forward_coverage_rate_pct`, `walk_forward_window_count`, and
`walk_forward_window_pass_rate_pct`. Active sets with unknown overfit risk, zero
active OOS coverage, or missing rolling WFA windows must remain `fail` in the
19-test packet until empirical evidence exists. Jue must not treat unverified
active sets as validated alpha. The metric keeps read availability in `status`
and exposes decision quality through `validation_status`; Kelly sizing and Live
Authority use the validation status when applying quality pressure.

`CryptoPatternLabRepository` enforces the same boundary at the source. New
optimization results without OOS evidence are saved as `rejected`, not `active`.
When the repository opens an older DB, legacy active rows with
`legacy_unverified`, empty/unknown overfit risk, failed walk-forward JSON, or no
OOS trades are reclassified to `rejected`. This prevents stale historical rows
from leaking back into Binance Jue as positive pattern evidence.

KIS live performance attribution requires explicit round-trip fill evidence.
`closed` is a block lifecycle state, not proof that the entry and exit both
filled. A KIS block enters Jue alpha validation only when `block_orders` has
filled buy evidence and filled sell evidence for the same `block_id`.
Cancelled waiting-entry blocks, expired pullback blocks, pre-fill operational
errors, and closed thesis-invalidated watch blocks are still synced for audit,
but they must be marked `filled=false`, `include_in_jue_alpha=false`, and
annotated with `fill_evidence_status` such as `cancelled_before_fill`,
`missing_buy_fill`, or `missing_sell_fill`. This prevents unfilled waiting
blocks from polluting Monte Carlo, Kelly, capacity, cost, and profitability
tests.

When closed Binance blocks have sparse `metadata_json`, the live evaluator may
backfill validation-only context from `.runtime/crypto_market_research.db`.
Feature rows can provide `quote_volume_usdt`, spread, funding, open interest,
and symbol regime so capacity/regime tests do not collapse to PnL-only
validation. Every such field must carry provenance such as
`capacity_source=crypto_market_research_features` and
`market_regime_source=crypto_market_research_db`.

For KIS specifically, sparse closed block metadata can be enriched from the
latest market pulse and symbol fundamentals DB. The evaluator may attach market
regime, sector cluster, valuation label, quality/growth scores, and derived
factor exposure to the validation metadata. These enrichments are context for
validation and learning, not hidden success assumptions; unavailable liquidity
or cost evidence must remain `missing` or `warn` rather than being invented.
When quote snapshots are available from `.runtime/market_judgment.db` or the KIS
block trader DB, the evaluator may convert recorded `trading_value` into
`capacity_krw = trading_value * max_participation_rate` with a default
participation rate of 1%. The metadata must retain provenance such as
`capacity_source=market_judgment_quote_turnover` and `daily_turnover_krw`.
If no quote turnover evidence exists, KIS capacity remains `missing`.

For Kelly sizing specifically, the packet exposes:

- `full_kelly_fraction`
- `fractional_kelly_025`
- `max_risk_cap_fraction`
- `weak_sample_cap_fraction`
- `recommended_risk_fraction`
- `recommended_risk_pct`
- `cap_reason`
- `evidence_quality`
- `risk_of_ruin_pct`
- `max_drawdown_pct`
- `validation_quality_pressure`

Jue must not treat full Kelly as an order size. The validation layer turns Kelly
into a conservative risk-governor input by applying fractional Kelly, sample
quality, drawdown state, risk-of-ruin state, validation-quality failures, and a
per-block cap. If data quality, cost simulation, stress, capacity, regime,
correlation, factor, or pattern-lab validation fails, recommended Kelly risk is
set to zero with `cap_reason=validation_quality_fail`.

For capacity analysis specifically, the `capacity` packet exposes:

- `capacity_method`: `orderbook_depth_and_turnover` or
  `metadata_capacity_ratio`
- `target_depth_bps`
- `covered_sample_count`
- `capacity_coverage_rate_pct`
- `liquidity_sample_count`
- `min_capacity_ratio`
- `avg_capacity_ratio`
- `min_practical_capacity_usdt`
- `avg_practical_capacity_usdt`
- `tightest_symbol`
- `examples`

When orderbook and turnover metadata are available, practical capacity is the
smaller of depth available inside the target bps band and daily-turnover
participation capacity. This keeps Jue from scaling a block family beyond what
the book can plausibly absorb. Capacity ratio is interpreted consistently in
both the metric packet and the discipline row: `>=20x` passes, `>=5x` warns, and
`<5x` fails because the block family is too close to available liquidity. The
capacity metric also checks evidence coverage. If fewer than half of live
outcomes have capacity evidence, the row cannot pass even when the covered
examples have high capacity ratios. The metric must use the same `pass`, `warn`,
`fail`, or `missing` status vocabulary as the discipline row, even when it falls
back to metadata-only capacity fields.

### Live Authority Gate Payload

The validation output is not only a dashboard artifact. `build_live_authority_payload`
must preserve the full latest validation `payload` under each venue's
`trading_validation.payload` so the execution managers, Telegram reports, and
block metadata can all read the same failed tests and metric bottlenecks.

Each venue's `validation_gate` carries a compact operational summary:

- `status`, `reason`, `readiness`, `fail_count`
- `risk_governor_action`, `risk_governor_source`, and
  `risk_governor_reasons` when ruin, drawdown, or Kelly checks require
  reduced risk, risk-off, or halt-new-risk behavior
- `failed_disciplines`: failed 19-test rows, compacted to `id`, `label`,
  `status`, and `action`
- `weak_disciplines`: first non-pass rows in priority order
- `capacity_bottleneck`: `status`, `capacity_method`, `min_capacity_ratio`,
  `tightest_symbol`, and `tightest_block_id` when capacity evidence exists
- `failure_attribution`: compact `recovery_focus`, `worst_groups`, and
  `best_groups` from validation metrics so new blocks and reflections know
  which symbol, horizon, strategy family, or regime caused the weak validation
  state
- `operator_guidance`: first actionable guidance strings from the validation
  payload

KIS and Binance block metadata must preserve those compact gate fields when a
block is created, rejected, or adjusted. This gives the memory/reflection loop
evidence for why Jue reduced size, waited, or blocked a trade. The gate should
never flatten a failed validation into only `fail_count`; the actual failed
discipline names, bottleneck symbol, and failure-attribution focus must remain
inspectable.

When those compact fields reach `InvestmentMemoryService`, every failed
discipline also becomes a policy scorecard group named
`validation.{discipline_id}`. A repeated `monte_carlo` failure therefore builds a
separate caution memory from a repeated `capacity_analysis` failure. These
scorecards are soft operational policies only. Repeated failure-attribution
groups also become `validation_attribution.{group_type}.{group}` scorecards, so
losses repeatedly attributed to `strategy_family=late_chase` can reduce size,
require waiting entries, or request target/stop review without becoming a hidden
strategy ban or bypassing the configured safety gates.

KIS and Binance manager prompts expose those evaluated soft cautions under
`candidate_policy_impacts`. The field is keyed by candidate symbol and contains
the compact matched policy rules, including `matched_metric` when the rule came
from validation failure attribution. This makes repeated failure context visible
beside the candidate being considered, instead of burying it inside the full
memory packet. Jue should use it as an auditable sizing, waiting-entry, and
target/stop review signal.

`risk_governor_action=halt_new_risk` is different from a learned policy. It is a
validation safety gate derived from the current 19-test packet. KIS and Binance
block managers must preserve the governor metadata and block new risk creation
while leaving risk-reduction, close, pause, and reflection workflows available.

For Monte Carlo specifically, the packet exposes:

- `sample_count`, `min_sample_count`
- `sample_adequacy`: `missing`, `weak`, or `sufficient`
- `final_return_p05_pct`, `final_return_median_pct`, `final_return_p95_pct`
- `final_return_expected_shortfall_p05_pct`
- `max_drawdown_p05_pct`, `max_drawdown_median_pct`
- `max_drawdown_expected_shortfall_p05_pct`
- `max_consecutive_loss_p95`
- `probability_loss_streak_ge_3_pct`
- `risk_of_ruin_pct`
- `ruin_event_count`
- `earliest_trade_index_to_ruin`
- `median_trade_index_to_ruin`
- `sequence_risk_level`

Jue should treat this as a sequence-risk governor. A strategy can look good on
average but still require reduced lane budget when the lower-tail drawdown,
loss-streak probability, or ruin probability worsens. Monte Carlo may not pass
when `sample_adequacy=weak`; small-sample bootstrap output is a caution signal,
not proof of survivability.

For stress testing specifically, the `stress` packet exposes:

- `scenario_source`: `metadata_crisis_returns` or
  `synthetic_live_return_shock`
- `scenario_count`
- `covered_crisis_sample_count`
- `stress_coverage_rate_pct`
- `worst_crisis_scenario_id`
- `worst_drawdown_pct`
- `scenarios[]` with `scenario_id`, `sample_count`, `final_return_pct`, and
  `max_drawdown_pct`

When live performance metadata includes crisis replay returns, those values
take precedence over synthetic shock assumptions. This lets Jue test a block
family against named crash, depeg, credit, or liquidity events instead of only
stretching normal realized returns. Metadata crisis stress may not pass when
fewer than half of live outcomes have crisis replay evidence; sparse crisis
coverage is a warning even when the covered examples survive the named shocks.

For Risk of Ruin specifically, the `ruin_profile` packet exposes:

- `ruin_drawdown_pct`
- `risk_of_ruin_pct`
- `ruin_event_count`
- `earliest_trade_index_to_ruin`
- `median_trade_index_to_ruin`
- `ruin_severity`: `low`, `medium`, `high`, or `critical`
- `governor_action`: `normal`, `de_risk`, `risk_off`, `halt_new_risk`, or
  `no_samples`

Jue should treat this as an explicit survival governor. A high ruin probability
or early time-to-ruin should not be hidden inside a generic score; it should
directly reduce lane budget, per-block risk, or new risk creation.

For MDD limit specifically, the packet exposes:

- `initial_equity`
- `peak_equity`
- `current_equity`
- `current_drawdown_pct`
- `max_drawdown_pct`
- `drawdown_limit_pct`
- `remaining_budget_pct`
- `drawdown_usage_ratio`
- `recovery_to_peak_pct`
- `risk_multiplier`
- `governor_action`

Positive `remaining_budget_pct` means drawdown budget remains. Negative means
the budget is already exceeded. Jue should use this as a recovery-mode governor:
normal, reduced, de-risk, risk-off, or halt-new-risk. Once
`drawdown_usage_ratio >= 1.0`, the packet must be `status=fail` with
`risk_multiplier=0.0`; severe breaches escalate the governor action to
`halt_new_risk`.

For Sharpe, Sortino, and Calmar specifically, the shared
`risk_adjusted_performance` packet exposes:

- `sample_count`, `min_sample_count`
- `sample_adequacy`: `missing`, `weak`, or `sufficient`
- `total_return_pct`
- `expectancy_pct`
- `volatility_pct`
- `downside_deviation_pct`
- `sharpe_ratio`
- `sortino_ratio`
- `calmar_ratio`
- `return_to_drawdown_ratio`
- `max_drawdown_pct`
- `quality_grade`
- `primary_risk_flag`
- `thresholds`

Jue should not interpret these ratios independently. Sharpe measures total
volatility efficiency, Sortino emphasizes downside volatility, and Calmar checks
whether return is worth the drawdown. The shared packet produces one quality
grade and one primary risk flag for live authority and sizing decisions. The
shared row may not pass when `sample_adequacy=weak`; small samples can show
flattering ratios by chance and must remain sizing cautions until the minimum
sample count is met.

For Profit Factor specifically, the `profitability_quality` packet exposes:

- `sample_count`, `min_sample_count`
- `sample_adequacy`: `missing`, `weak`, or `sufficient`
- `total_net_pnl`
- `gross_profit`
- `gross_loss`
- `profit_factor`
- `loss_absorption_ratio`
- `win_rate_pct`
- `payoff_ratio`
- `expectancy_pct`
- `average_win`
- `average_loss`
- `edge_grade`
- `thresholds`

For Recovery Factor specifically, the `recovery_profile` packet exposes:

- `sample_count`, `min_sample_count`
- `sample_adequacy`: `missing`, `weak`, or `sufficient`
- `initial_equity`
- `current_equity`
- `peak_before_trough`
- `peak_trade_index_before_trough`
- `trough_equity`
- `trough_trade_index`
- `recovery_trade_index`
- `recovery_trade_count`
- `recovered_from_max_drawdown`
- `required_gain_from_trough_pct`
- `max_drawdown_cash`
- `max_drawdown_pct`
- `recovery_factor`
- `recovery_state`
- `thresholds`

Jue should treat Profit Factor as loss absorption quality and Recovery Factor as
damage-repair quality. A system can have a positive PF but still deserve slower
sizing recovery if the max drawdown was not recovered or took too many trades to
recover. Neither packet may pass when `sample_adequacy=weak`; high PF or quick
recovery over a small number of trades is a sizing caution until the minimum
sample count is met.

For regime testing specifically, the `regime_scorecards` packet exposes:

- `status`
- `regime_count`
- `covered_sample_count`
- `regime_coverage_rate_pct`
- `best_regime`
- `worst_regime`
- `negative_regime_count`
- `worst_expectancy_pct`
- `scorecards[]` with sample count, expectancy, profit factor, and win rate

Regime testing may not pass when fewer than half of live outcomes have regime
metadata. Sparse regime labels are a warning even when the covered regimes have
positive expectancy, because Jue has not yet proven behavior across the actual
live book.

For correlation specifically, the `correlation_proxy` packet uses two tiers:

- if rolling return metadata exists: `method=rolling_return_window`,
  `covered_sample_count`, `correlation_coverage_rate_pct`, `pair_count`,
  `max_abs_correlation`, `top_correlation`, `top_pair`, and top `pairs[]`
- otherwise: `method=metadata_cluster_concentration_proxy`, top cluster share,
  cluster count, cluster sample counts, and `correlation_coverage_rate_pct`

Correlation may not pass when fewer than half of live outcomes have rolling
return windows or cluster metadata. Sparse correlation evidence is a warning
even if the covered pair or covered cluster looks diversified.

For factor exposure specifically, the `factor_exposure` packet exposes:

- `covered_sample_count`
- `factor_coverage_rate_pct`
- `factor_count`
- `top_factor`
- `dominant_factor`
- `top_factor_share_pct`
- `weighted_top_factor_share_pct`
- `factor_totals`
- `weighted_factor_totals`

Jue should treat these as diversification checks. Good standalone blocks can
still be throttled when the live book is concentrated in one regime failure,
one correlated return pair, or one dominant factor. Factor exposure may not pass
when fewer than half of live outcomes have factor metadata; sparse factor tags
are treated as a coverage warning even if the tagged examples look diversified.

## Live Authority Gate

Trading validation is not only displayed beside Live Edge. The live evaluator
must apply it as a final authority gate:

- `summary.readiness == blocked_by_validation` or `summary.fail_count > 0`
  disables `allow_scale_up` and caps `max_budget_multiplier` at the observe-only
  multiplier.
- `summary.readiness == research_only` also caps the venue at observe-only.
- `summary.readiness == probe` or `normal` is always surfaced as
  `validation_probe` or `validation_normal`, even when the current edge-derived
  multiplier is already `1.0x` or lower. These states can allow normal
  operation, but they must cap scale-up at `1.0x`; only `scale_ready` can
  preserve an edge-derived multiplier above `1.0x`.
- `status == error` does the same, because a broken validation layer must not
  grant scale-up permission.
- missing validation readiness also caps scale-up until a validation run exists.
- the original edge-derived budget is preserved in
  `validation_gate.original_max_budget_multiplier` so operators can see whether
  validation, not live edge, caused the cap.

This keeps a promising edge scorecard from scaling when the 19-discipline
validation layer has found a survival, capacity, cost, data, or diversification
problem.

KIS block creation must also consume this gate before order/block execution.
When a venue packet contains `blocked_by_validation`,
`validation_research_only`, `validation_error`, `validation_missing`,
`validation_stale`, or an authority error, new KIS immediate-entry blocks are
rejected before they are written to the block ledger. A KIS block may still be
staged only when Jue provides an explicit `wait_for_price` entry plan with a
valid trigger, target, and stop. Restricted waiting-entry blocks are reduced to a
one-share probe size and the original requested quantity is recorded in block
metadata. `validation_probe` also applies the one-share cap, while
`validation_normal` is visible in metadata and UI but does not force the cap by
itself. This makes the gate a hard execution control where risk is not yet
proven, not only a prompt hint.

Binance block creation consumes the same venue packet. If `validation_gate` is
`blocked_by_validation`, `validation_research_only`, `validation_error`, or
`validation_missing`, new Binance blocks are rejected before they are written to
the block ledger. If the gate is `validation_probe` or `validation_normal`, a
new Binance block must be a waiting-entry block; immediate entries are rejected
and executable candidate price plans are downgraded to wait-for-price structure.
The gate status, readiness, and reason are copied into block metadata for audit.

## Pattern Lab Inputs

For Binance strategy validation, `TradingValidationService` reads
`.runtime/crypto_pattern_lab.db` when `venue=binance`:

- `optimized_strategy_sets.status`
- `in_sample_expectancy_r`
- `out_of_sample_trade_count`
- `out_of_sample_expectancy_r`
- `out_of_sample_profit_factor`
- `out_of_sample_max_drawdown_r`
- `overfit_risk`
- `walk_forward_quality_json`

This upgrades the overfit, walk-forward, and out-of-sample rows from pure live
performance proxies into pattern-lab-aware validation rows. A mixed book with
both active and rejected optimized sets should normally produce `warn`, not
`pass`, because Jue must understand that the optimizer is finding both usable
and overfit candidates. An active set with positive OOS but no rolling
walk-forward window evidence must produce a failed walk-forward row and zero
recommended Kelly risk until the missing windows are backfilled or re-run.

The crypto `PatternOptimizationLab` must not promote a parameter set from a
single in-sample backtest. It performs a chronological train/test split when
enough bars are available, selects the best parameter set on the train segment,
then replays only that selected set on the out-of-sample segment. It also runs
the selected parameter set across rolling forward windows and stores
`walk_forward.window_count`, `passed_window_count`, `pass_rate_pct`, and compact
window evidence on `best`. Repository promotion to `active` is allowed only when
the out-of-sample trade count, expectancy, profit factor, drawdown gates, and
minimum rolling WFA window/pass-rate gates pass. If the optimizer cannot produce
an out-of-sample packet or rolling WFA windows, the set remains `rejected`; there
is no deterministic or legacy fallback to active.

KIS validation must not consume Binance crypto pattern lab evidence. When
`venue=kis`, `TradingValidationService` first checks
`TRADECRAFT_KR_EQUITY_PATTERN_LAB_DB_PATH` for an `optimized_strategy_sets`
table. If that KIS-native DB exists, overfit, walk-forward, and out-of-sample
rows use `source_scope=kr_equity_pattern_lab`. If it is absent, those rows fall
back to a `kis_live_forward_proxy` packet derived from KIS live block outcomes
only. That proxy may produce `warn` when enough KIS samples exist, but it does
not count as a true rolling walk-forward or venue-native out-of-sample pass. The
current KIS pattern-lab builder v1 writes that DB from KIS live alpha outcomes:
it groups filled Jue-created KIS blocks by symbol and derived family, performs a
chronological train/test split, and stores active or rejected
`optimized_strategy_sets` rows. This removes Binance evidence contamination and
gives KIS a venue-native first-pass WFA/OOS packet. Later versions should add
Korean equity feature snapshots from reports, Naver fundamentals, market pulse,
ETF research, Whale/세시반 signals, and OHLCV path features before the split.

The live evaluator runs the KIS pattern-lab builder before refreshing
`TradingValidationService` when `TRADECRAFT_KR_EQUITY_PATTERN_LAB_ENABLED=true`.
If no grouped KIS sample reaches `TRADECRAFT_KR_EQUITY_PATTERN_LAB_MIN_SAMPLES`,
the builder records `insufficient_samples` and does not fabricate optimized
sets.

## Refactor Rules

- Keep validation independent from order execution. Validation informs Jue and
  authority sizing; it must not directly place orders.
- Do not silently mark unavailable tests as passed. Missing upstream data must
  produce `missing` or `warn`.
- Keep the discipline count stable at 19 unless the operator intentionally
  revises the checklist.
- Preserve the `live_authority.trading_validation` shape because both KIS 쥬
  and Binance 쥬 can consume it without new prompt plumbing.
- Future implementations should deepen the missing rows instead of replacing
  the packet contract.
