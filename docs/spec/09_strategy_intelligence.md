# Strategy Intelligence

## Purpose

Strategy intelligence turns reports, RAG, valuation, ETF research, Whale/세시반 signals, and research-feed context into candidate packets that 쥬 can evaluate for active block trading. Daily discovery and market pulse are adjacent 쥬/block-manager context unless they are explicitly bridged into the research feed or candidate payload.

It is a ranking and evidence-shaping layer, not an order executor. A high score means a candidate deserves review for a block decision; actual entry, adoption, update, pause, close, or avoidance still depends on 쥬's block manager, current positions, account constraints, quote/session checks, and safety gates.

## Suitability v2

| Horizon | Meaning | Dominant Evidence |
| --- | --- | --- |
| Short | Next day to 1 week | Supply/demand, price momentum, freshness, report momentum. |
| Mid | 2 weeks to 3 months | Report momentum, growth, valuation, supply/demand, whale signals. |
| Long | 3 months plus | Quality, growth, valuation, whale/institutional evidence. |
| Balanced | Default sorting score | Average of short/mid/long with confidence and coverage. |

`StrategyIntelligenceEngine.build_candidates` produces `score_method_version = "v2"` and separates short, mid, long, and balanced suitability buckets. Each bucket carries a score, grade, drivers, and risks. Stocks and ETFs use different suitability math: equities weight report evidence, RAG/research feed, Whale/세시반, valuation, quality, growth, recency, and risk; ETFs weight liquidity, momentum, core fit, report support, freshness, and risk.

`data_coverage` is not the same as `confidence`. Coverage describes what evidence families are present: report, research feed, Whale Insight, 세시반, valuation, quality/growth data, ETF research, and source count. Confidence is then capped or reduced by weak evidence, missing valuation for non-ETF symbols, one-source candidates, and risk penalties. A candidate can have decent coverage but low confidence if identity or evidence quality is suspect.

## Candidate Fields

- `symbol`
- `name`
- `asset_class`
- `horizon_bias`
- `score`
- `score_method_version`
- `score_components`
- `suitability`
- `risk_score`
- `confidence`
- `confidence_label`
- `data_coverage`
- `valuation`
- `identity_status`
- `data_warnings`
- `stance`
- `sources`
- `report_ids`
- `citations`
- `facts`
- `reasons`
- `risks`
- `checks`

The important operational fields for 쥬 are `suitability`, `risk_score`, `confidence`, `data_coverage`, `identity_status`, `data_warnings`, `reasons`, `risks`, and `checks`. These fields explain whether a symbol is a clean block candidate, a confirmation candidate, or an exclusion with missing data.

## ETF Integration

- ETF should be treated as a first-class symbol where data is available.
- ETF/core blocks use allocation and rebalance semantics rather than scalp-only semantics.
- ETF research should feed both strategy candidates and KIS block manager context.
- ETF names must be repaired when the display name is only a sponsor prefix or code-like generic name.
- ETF valuation is generally `not_applicable`; suitability should use ETF liquidity, turnover, momentum, core-fit, risk, ETF-linked reports, and ETF research freshness instead.

`ETFResearchRepository` stores `etf_universe`, `etf_market_snapshots`, and `etf_scores`. `ConfiguredETFResearchProvider` expands the configured ETF universe with ETF-like symbol-directory rows, seeds the repository, reports usable research count, and exposes latest snapshot/score data to strategy intelligence.

ETF candidates can enter strategy intelligence from the ETF universe, ETF snapshots/scores, and linked Naver ETF reports. When the query has ETF intent, strategy intelligence preserves ETF candidates in the top list even if ordinary equity candidates also score well.

## Market Pulse Integration

- Market pulse contributes index, sector, investor, program, futures, FX, and risk-regime context.
- It should influence sizing, aggressiveness, entry timing, and block management rather than replacing symbol evidence.
- It should be used to align active/open blocks with hot sectors, risk flags, and exposure concentration.
- 세시반 sector signals can feed market pulse sector summaries, while strategy intelligence uses 세시반 symbol signals as candidate evidence.

`MarketPulseService` stores snapshots in `.runtime/market_pulse.db` and exposes `context_for_blocks`, adding `block_alignment` and `block_exposure` around the latest regime snapshot. KIS 쥬 should treat market pulse as live trading context: a risk-off pulse may shrink or delay a candidate, while a supportive pulse can help confirm a block whose symbol evidence is already strong.

## Existing Position vs Fresh Alpha

Existing-position adoption and fresh alpha must be evaluated separately. A held KIS position can be adopted into a block without sending a buy order, which means the strategy question is often "manage, resize, hedge, or exit this existing risk" rather than "is this a new buy." Fresh alpha candidates should still pass price, volume, data quality, and safety checks before becoming new blocks.

Daily discovery and instant symbol analysis are good sources of fresh study ideas, but they should not overwrite adoption logic for existing positions. Conversely, an existing position should not receive a fresh-alpha score boost merely because it is already held.

## Symbol Identity Repair

Candidate identity is part of strategy quality. `identity_status` marks invalid symbols, missing/generic names, and low-quality names as suspect. Naver report symbol directory, KRX/pykrx refreshed names, ETF name preference logic, linked report names, and symbol analysis resolution should repair identity before 쥬 uses the candidate in active block decisions.

Known repair targets include code-only names, report-date-like six-digit strings, generic report/OCR names, and ETF sponsor prefixes without the full ETF product name.

## Known Strategy Caveats

- Data coverage is not the same as confidence.
- A high score is suitability for review, not an unconditional order.
- Existing-position adoption and fresh alpha must be evaluated separately.
- Generic names such as code-only names should be repaired through symbol identity layers.
- Whale/세시반 signals are confirmation and prioritization inputs, not standalone buy authority.
- Missing valuation should reduce non-ETF long-horizon suitability, but should not punish ETF/core candidates the same way.
- RAG chunks can contain PDF boilerplate, captions, and OCR noise; quality filters should stay in front of candidate scoring.
- Market pulse is regime and exposure context; it should tune block behavior, not replace symbol-level thesis.
- Strategy intelligence outputs active block-trading priorities for HERMES/쥬. They must stay connected to block intent, validation triggers, risk notes, and safety gates.
