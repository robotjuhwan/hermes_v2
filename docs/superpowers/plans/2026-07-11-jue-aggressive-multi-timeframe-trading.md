# HERMES/Jue Aggressive Multi-Timeframe Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an evidence-gated multi-timeframe trend system for KIS and Binance that can earn full 0.75% per-symbol risk only after cost-aware, exchange-fill-proven validation.

**Architecture:** Preserve the current venue adapters, block repositories, order coordinators, reconciliation, kill switches, and live-authority gates. Add focused deterministic services for market bars, multi-horizon signals, KIS research packets, risk intents, and validation passports; managers consume these contracts but cannot override them.

**Tech Stack:** Python 3.10+, dataclasses, SQLite, `httpx`, FastAPI, pytest, existing TradeCraft runners and adapters; no new runtime dependency.

## Global Constraints

- KIS and Binance each have an independent 12% maximum-drawdown boundary.
- Maximum stop-defined risk is 0.75% of venue equity per symbol.
- Each venue may have at most six active symbols and at most 4.5% nominal open stop risk.
- A correlated sector/theme/crypto cluster may have at most 1.5% open stop risk.
- Binance futures use isolated margin and never exceed 3x leverage.
- KIS uses cash positions; no averaging down and no post-entry stop widening.
- New KIS stock risk requires fresh source-linked research; ETFs may use current ETF/index/sector research.
- Existing KIS positions are normalized deliberately; the implementation must not force a bulk market liquidation.
- Existing API paths, environment aliases, kill switches, order defaults, and paper/live behavior remain compatible.
- Tests must isolate runtime paths before application imports and must not touch live `.runtime` files.
- Do not commit, delete live data, change live settings, or increase real-money authority unless the user separately requests that action.
- Follow TDD for every task: failing focused test, minimal implementation, focused pass, domain pass.

## File Structure

New focused modules:

- `src/tradecraft/services/market_bars.py`: normalized bar contract and optional SQLite cache for reproducible KIS inputs.
- `src/tradecraft/services/multi_horizon_signal.py`: pure three-horizon trend/breakout calculation.
- `src/tradecraft/services/kis_research_packet.py`: source-linked Naver evidence, revision, conflict, and freshness calculation.
- `src/tradecraft/services/unified_risk_intent.py`: stop-risk sizing, cluster caps, leverage, and drawdown ladder.
- `src/tradecraft/services/strategy_validation.py`: replay metrics, DSR, PBO, promotion floors, and validation passport.

Existing integration points:

- `src/tradecraft/services/kis.py`: official KIS daily item chart adapter call only.
- `src/tradecraft/services/crypto_market_research.py`: collect the additional Binance `1d` horizon and expose normalized bar inputs.
- `src/tradecraft/services/jue_research_spine.py`: carry `KisResearchPacketV2` as canonical evidence.
- `src/tradecraft/services/kis_manager_prompt.py`: expose bounded signal/risk/research contracts.
- `src/tradecraft/services/kis_block_trader.py`: orchestrate the new KIS services and enforce their output after LLM response.
- `src/tradecraft/services/binance_manager_prompt.py`: expose Binance signal and risk contracts.
- `src/tradecraft/services/binance_block_trader.py`: orchestrate Binance signals and enforce venue risk intent.
- `src/tradecraft/services/live_authority.py`: map existing grades onto the new risk ladder without breaking `live_grade`.
- `src/tradecraft/services/manager_run_telemetry.py`: record signal, evidence, risk, and validation attribution.
- `src/tradecraft/runtime/live_evaluator_runner.py`: publish validation passports into existing authority packets.
- `src/tradecraft/config.py` and `src/tradecraft/services/settings_catalog.py`: additive read-only settings for the signal cache and fixed strategy revision.

---

### Task 1: Reproducible Market-Bar Inputs

**Files:**
- Create: `src/tradecraft/services/market_bars.py`
- Modify: `src/tradecraft/services/kis.py` (`KISConfig`, `KISAdapter`)
- Modify: `src/tradecraft/services/crypto_market_research.py` (`_resolved_kline_intervals`)
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/services/settings_catalog.py`
- Test: `tests/test_market_bars.py`
- Test: `tests/test_kis_adapter.py`
- Test: `tests/test_crypto_market_research.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `MarketBarV1`, `MarketBarRepository.save_bars(*, venue, symbol, interval, rows, source)`, and `MarketBarRepository.list_bars(*, venue, symbol, interval, limit)`.
- Produces: `KISAdapter.fetch_domestic_daily_prices(symbol, start_date, end_date, adjusted=True)`.
- Consumes later: Task 2 passes the returned rows through the exact `bars_by_horizon` keyword of `build_multi_horizon_signal`.

- [ ] **Step 1: Add failing normalized-bar repository tests**

```python
def test_market_bar_repository_round_trips_in_source_order(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "signals.db")
    repo.save_bars(
        venue="kis",
        symbol="005930",
        interval="1d",
        rows=[
            {"open_time": "2026-07-09", "open": 60000, "high": 62000,
             "low": 59500, "close": 61500, "volume": 1000},
            {"open_time": "2026-07-10", "open": 61500, "high": 63000,
             "low": 61000, "close": 62800, "volume": 1200},
        ],
        source="kis:FHKST03010100",
    )
    rows = repo.list_bars(venue="kis", symbol="005930", interval="1d", limit=20)
    assert [row["close"] for row in rows] == [61500.0, 62800.0]
    assert rows[-1]["source_id"].startswith("kis:FHKST03010100:")
```

- [ ] **Step 2: Run the repository test and verify the missing module failure**

Run: `pytest tests/test_market_bars.py -q`

Expected: collection fails with `ModuleNotFoundError: tradecraft.services.market_bars`.

- [ ] **Step 3: Implement the bar contract and idempotent SQLite cache**

```python
@dataclass(frozen=True, slots=True)
class MarketBarV1:
    venue: str
    symbol: str
    interval: str
    open_time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_id: str

class MarketBarRepository:
    def save_bars(
        self, *, venue: str, symbol: str, interval: str,
        rows: list[dict[str, Any]], source: str,
    ) -> int:
        """Validate and upsert normalized rows; return the affected row count."""

    def list_bars(
        self, *, venue: str, symbol: str, interval: str, limit: int,
    ) -> list[dict[str, Any]]:
        """Return at most limit rows in ascending open-time order."""
```

Use a unique key on `(venue, symbol, interval, open_time)`, validate positive
OHLC values, reject `high < low`, and return oldest-to-newest rows. Construct
`source_id` from source, symbol, interval, and open time; do not hash or retain
credentials or request headers.

- [ ] **Step 4: Add the failing KIS daily-chart adapter contract test**

```python
def test_fetch_domestic_daily_prices_normalizes_output2(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fake the authenticated GET and assert the official path/TR and parameters.
    rows = asyncio.run(adapter.fetch_domestic_daily_prices(
        "005930", start_date="20260601", end_date="20260710"
    ))
    assert request.path.endswith("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice")
    assert request.headers["tr_id"] == "FHKST03010100"
    assert request.params["FID_PERIOD_DIV_CODE"] == "D"
    assert rows[0] == {
        "open_time": "2026-07-10", "open": 61500.0, "high": 63000.0,
        "low": 61000.0, "close": 62800.0, "volume": 1200.0,
    }
```

- [ ] **Step 5: Implement the official KIS read-only request**

Add `tr_id_daily_chart: str = "FHKST03010100"` to `KISConfig`. Reuse the
adapter's token, shared rate limiter, timeout, response-code validation, and
`KISAPIError` behavior. Send `FID_COND_MRKT_DIV_CODE=J`, `FID_INPUT_ISCD`,
`FID_INPUT_DATE_1`, `FID_INPUT_DATE_2`, `FID_PERIOD_DIV_CODE=D`, and
`FID_ORG_ADJ_PRC=0` for adjusted prices. Normalize `stck_bsop_date`,
`stck_oprc`, `stck_hgpr`, `stck_lwpr`, `stck_clpr`, and `acml_vol`.

Primary API reference: `koreainvestment/open-trading-api`, domestic stock
`inquire_daily_itemchartprice`, TR `FHKST03010100`.

- [ ] **Step 6: Add Binance `1d` collection without removing existing intervals**

Change the default interval string and fallback map to include `1d:90`:

```python
"1m:120,5m:96,15m:96,1h:168,4h:180,1d:90"
```

Assert existing user-provided interval settings remain authoritative.

- [ ] **Step 7: Run focused and configuration contract tests**

Run: `pytest tests/test_market_bars.py tests/test_kis_adapter.py tests/test_crypto_market_research.py tests/test_config.py -q`

Expected: all pass; no network call leaves the fake adapters.

- [ ] **Step 8: Review checkpoint**

Inspect `git diff --check`. Do not commit unless the user explicitly requests a commit.

---

### Task 2: Deterministic Multi-Horizon Signal Engine

**Files:**
- Create: `src/tradecraft/services/multi_horizon_signal.py`
- Test: `tests/test_multi_horizon_signal.py`

**Interfaces:**
- Consumes: normalized bar dictionaries from Task 1 and existing Binance klines.
- Produces: `MultiHorizonSignalV1.to_dict()` and `build_multi_horizon_signal(*, venue, symbol, evaluated_at, bars_by_horizon, freshness_limits)`.
- Consumers: KIS and Binance manager integrations in Tasks 5 and 6.

- [ ] **Step 1: Write failing agreement, stale-data, and stop tests**

```python
def test_two_of_three_up_signals_allow_partial_risk() -> None:
    signal = build_multi_horizon_signal(
        venue="kis", symbol="005930", evaluated_at="2026-07-11T00:00:00Z",
        bars_by_horizon={"fast": rising(5), "medium": rising(10), "slow": flat(20)},
        freshness_limits={"fast": 4 * 86400, "medium": 4 * 86400, "slow": 4 * 86400},
    )
    assert signal.agreement_count == 2
    assert signal.agreed_direction == "long"
    assert signal.max_risk_fraction == pytest.approx(0.60)
    assert signal.initial_stop_reference < signal.entry_trigger

def test_stale_horizon_is_unavailable_not_flat() -> None:
    signal = build_multi_horizon_signal(
        venue="kis",
        symbol="005930",
        evaluated_at="2026-07-11T00:00:00Z",
        bars_by_horizon={
            "fast": rising_ending(5, "2026-06-01"),
            "medium": rising_ending(10, "2026-07-10"),
            "slow": rising_ending(20, "2026-07-10"),
        },
        freshness_limits={"fast": 4 * 86400, "medium": 4 * 86400, "slow": 4 * 86400},
    )
    assert signal.horizons["fast"]["status"] == "unavailable"
    assert signal.entry_eligible is False
```

- [ ] **Step 2: Run the tests and verify the missing implementation**

Run: `pytest tests/test_multi_horizon_signal.py -q`

Expected: fail because the module does not exist.

- [ ] **Step 3: Implement a pure, versioned signal contract**

```python
@dataclass(frozen=True, slots=True)
class MultiHorizonSignalV1:
    venue: str
    symbol: str
    evaluated_at: str
    horizons: dict[str, dict[str, Any]]
    agreement_count: int
    agreed_direction: str
    entry_eligible: bool
    max_risk_fraction: float
    entry_trigger: float
    initial_stop_reference: float
    expires_at: str
    source_bar_ids: tuple[str, ...]
    version: str = "multi_horizon_signal_v1"

def build_multi_horizon_signal(
    *, venue: str, symbol: str, evaluated_at: str,
    bars_by_horizon: dict[str, list[dict[str, Any]]],
    freshness_limits: dict[str, int],
) -> MultiHorizonSignalV1:
    """Return the versioned three-horizon signal described below."""
```

For each horizon calculate close-location, breakout versus prior high/low,
short/long return, ATR, volume expansion, and a trend direction. Keep the first
revision intentionally simple: deterministic thresholds declared as constants,
no LLM feature, no learned weights, and no per-symbol optimization.

- [ ] **Step 4: Encode entry and invalidation invariants**

Two valid agreeing horizons set `max_risk_fraction=0.60`; three set `1.0`.
Fewer than two set `entry_eligible=False`. The initial stop is the more
conservative of structure invalidation and an ATR stop. Missing values, invalid
OHLC, non-monotonic timestamps, or future bars make the affected horizon
unavailable and add an audit reason.

- [ ] **Step 5: Add property-style table tests without a new dependency**

Use `pytest.mark.parametrize` to verify:

- monotonic rising/falling/flat data;
- 2-of-3 and 3-of-3 agreement;
- gaps and zero volume;
- no signal can widen its own initial stop;
- source bar IDs are stable under replay.

- [ ] **Step 6: Run signal tests and lint the module**

Run: `pytest tests/test_multi_horizon_signal.py -q`

Run: `ruff check src/tradecraft/services/multi_horizon_signal.py tests/test_multi_horizon_signal.py`

Expected: both pass.

- [ ] **Step 7: Review checkpoint**

Inspect deterministic output for at least one KIS-long, Binance-long,
Binance-short, and no-trade fixture. Do not commit without explicit approval.

---

### Task 3: Source-Linked KIS Research Packets

**Files:**
- Create: `src/tradecraft/services/kis_research_packet.py`
- Modify: `src/tradecraft/services/naver_reports.py` (`NaverReportRepository` read methods only unless migration is required by a test)
- Modify: `src/tradecraft/services/jue_research_spine.py` (`build_research_spine`)
- Test: `tests/test_kis_research_packet.py`
- Test: `tests/test_strategy_intelligence.py`
- Test: `tests/test_kis_manager_prompt.py`

**Interfaces:**
- Consumes: `latest_symbol_linked_reports`, `get_report`, `get_report_facts`, and report symbol links.
- Produces: `KisResearchEvidenceV1`, `KisResearchPacketV2`, and `build_kis_research_packet(*, symbol, asset_class, reports, facts_by_report, now)`.
- Consumer: KIS prompt and post-LLM gate in Task 5.

- [ ] **Step 1: Write failing freshness, revision, conflict, and ETF tests**

```python
def test_packet_computes_target_revision_from_traceable_reports() -> None:
    packet = build_kis_research_packet(
        symbol="005930", asset_class="stock", reports=[new_report, old_report],
        facts_by_report={2: new_facts, 1: old_facts}, now="2026-07-11T00:00:00Z",
    )
    assert packet.status == "eligible"
    assert packet.revisions["target_price_pct"] == pytest.approx(10.0)
    assert packet.evidence[0].report_id == 2
    assert packet.evidence[0].source_ref["pdf_sha256"]

def test_conflicting_broker_directions_reduce_entry_eligibility() -> None:
    packet = build_kis_research_packet(
        symbol="005930",
        asset_class="stock",
        reports=[
            report_row(report_id=2, broker="A", published_at="2026-07-10"),
            report_row(report_id=1, broker="B", published_at="2026-07-09"),
        ],
        facts_by_report={
            2: facts_row(rating="BUY", target_price=90000),
            1: facts_row(rating="SELL", target_price=50000),
        },
        now="2026-07-11T00:00:00Z",
    )
    assert packet.conflict_status == "material"
    assert packet.entry_support == "waiting_entry"
```

- [ ] **Step 2: Run focused tests and verify the missing module**

Run: `pytest tests/test_kis_research_packet.py -q`

Expected: module collection failure.

- [ ] **Step 3: Implement explicit fact and interpretation separation**

```python
@dataclass(frozen=True, slots=True)
class KisResearchEvidenceV1:
    report_id: int
    symbol: str
    published_at: str
    broker: str
    rating: str
    target_price: float
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_quotes: tuple[str, ...]
    source_ref: dict[str, str]
    link_confidence: float
    freshness: str

@dataclass(frozen=True, slots=True)
class KisResearchPacketV2:
    symbol: str
    asset_class: str
    status: str
    entry_support: str
    revisions: dict[str, float | str]
    conflict_status: str
    confirmed_facts: tuple[str, ...]
    interpretation: tuple[str, ...]
    missing_data: tuple[str, ...]
    evidence: tuple[KisResearchEvidenceV1, ...]
    version: str = "kis_research_packet_v2"
```

Use publication time for evidence freshness and crawl time for collection
health. Reject stock evidence below the existing symbol-link confidence floor,
without a report ID, or without a stable PDF/archive/source identity.

- [ ] **Step 4: Implement deterministic revision and conflict rules**

Compare the newest valid report with the nearest earlier valid report from the
same broker, then compute cross-broker consensus separately. Material conflict
means both bullish and bearish directions exist among fresh sources or target
dispersion exceeds the declared threshold. Never let the LLM fabricate a
missing numeric revision.

- [ ] **Step 5: Attach packets to `research_spine` without duplicating raw data**

Add a `kis_research` field inside each symbol packet and add compact quality
counts to `quality_summary`. Preserve all existing keys. The packet carries at
most the newest six evidence records and bounded evidence quotes; raw PDFs and
full report content stay in the repository.

- [ ] **Step 6: Test active-use contracts**

Assert that:

- fresh positive revision can support discovery;
- stale/ambiguous evidence cannot support a stock entry;
- material negative revision sets `addition_allowed=False`;
- ETF packets accept current ETF/index/sector research;
- every exposed claim has an evidence ID.

- [ ] **Step 7: Run KIS research domain tests**

Run: `pytest tests/test_kis_research_packet.py tests/test_strategy_intelligence.py tests/test_kis_manager_prompt.py -q`

Expected: all pass and existing `research_spine` consumers remain compatible.

- [ ] **Step 8: Review checkpoint**

Run `git diff --check`; inspect a compact packet for prompt size and source
traceability. Do not commit without explicit approval.

---

### Task 4: Unified Risk Intent and Drawdown Governor

**Files:**
- Create: `src/tradecraft/services/unified_risk_intent.py`
- Test: `tests/test_unified_risk_intent.py`

**Interfaces:**
- Consumes: venue equity, high-water mark, signal stop, costs, positions,
  cluster labels, exchange quantity filters, and authority risk grade.
- Produces: `UnifiedRiskIntentV1` and the fully keyword-only `build_unified_risk_intent` signature shown in Step 1.
- Consumers: Tasks 5 and 6.

- [ ] **Step 1: Write failing sizing and cap tests**

```python
def test_validated_risk_sizing_includes_round_trip_cost() -> None:
    intent = build_unified_risk_intent(
        venue="binance", symbol="BTCUSDT", equity=10_000,
        high_water_equity=10_000, entry_price=100, stop_price=98,
        round_trip_cost_per_unit=0.10, authority_grade="validated",
        signal_risk_fraction=1.0, leverage=3.0, margin_mode="isolated",
        open_positions=[], cluster="btc_beta", quantity_step=0.001,
    )
    assert intent.max_loss_amount == pytest.approx(75.0)
    assert intent.quantity == pytest.approx(35.714, abs=0.001)
    assert intent.leverage <= 3.0

def test_twelve_percent_drawdown_engages_kill_switch_intent() -> None:
    intent = build_unified_risk_intent(
        venue="kis", symbol="005930", equity=8800, high_water_equity=10000,
        entry_price=100, stop_price=95, round_trip_cost_per_unit=0.20,
        authority_grade="validated", signal_risk_fraction=1.0,
        leverage=1.0, margin_mode="cash", open_positions=[],
        cluster="semiconductor", quantity_step=1.0,
    )
    assert intent.allowed is False
    assert intent.action == "kill_switch"
```

- [ ] **Step 2: Run the tests and verify missing implementation**

Run: `pytest tests/test_unified_risk_intent.py -q`

Expected: module collection failure.

- [ ] **Step 3: Implement the immutable risk result**

```python
@dataclass(frozen=True, slots=True)
class UnifiedRiskIntentV1:
    venue: str
    symbol: str
    authority_grade: str
    drawdown_pct: float
    allowed: bool
    action: str
    max_risk_pct: float
    max_loss_amount: float
    entry_price: float
    stop_price: float
    risk_per_unit: float
    quantity: float
    leverage: float
    margin_mode: str
    applied_caps: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    version: str = "unified_risk_intent_v1"
```

Implement authority caps `observe_only=0`, `restricted=0.1875`,
`proving=0.375`, and `validated=0.75`, then take the minimum of authority,
drawdown, signal fraction, symbol, cluster, open-risk, cash/margin, and exchange
quantity constraints.

- [ ] **Step 4: Implement the independent drawdown ladder**

Use venue-specific reconciled equity high-water marks:

```python
def drawdown_risk_cap_pct(drawdown_pct: float) -> tuple[float, str]:
    if drawdown_pct >= 12.0: return 0.0, "kill_switch"
    if drawdown_pct >= 10.0: return 0.0, "halt_new_entries"
    if drawdown_pct >= 7.0: return 0.375, "de_risk_50"
    if drawdown_pct >= 4.0: return 0.56, "de_risk_25"
    return 0.75, "normal"
```

Protective reductions and exits remain allowed at every tier.

- [ ] **Step 5: Add invariant tests**

Test cash insufficiency, Binance quantity steps/min notional, KIS integer shares,
cluster risk 1.5%, venue risk 4.5%, six-symbol cap, gap allowance, funding,
isolated-only futures, leverage above 3x rejection, same-symbol spot/futures
aggregation, and no stop widening.

- [ ] **Step 6: Run tests and lint**

Run: `pytest tests/test_unified_risk_intent.py -q`

Run: `ruff check src/tradecraft/services/unified_risk_intent.py tests/test_unified_risk_intent.py`

Expected: all pass.

- [ ] **Step 7: Review checkpoint**

Manually recompute at least four sizing fixtures from the printed audit fields.
Do not commit without explicit approval.

---

### Task 5: KIS Manager Integration and Existing-Position Normalization

**Files:**
- Modify: `src/tradecraft/services/kis_manager_prompt.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/runtime/kis_block_trader_runner.py`
- Test: `tests/test_kis_manager_prompt.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_kis_block_trader_runner.py`

**Interfaces:**
- Consumes: Tasks 1–4 contracts.
- Produces: prompt sections `multi_horizon_signals`, `kis_research_packets`, and
  `risk_intents`, plus deterministic post-response enforcement.

- [ ] **Step 1: Add failing prompt-shape tests**

```python
def test_kis_prompt_exposes_bounded_canonical_decision_contracts() -> None:
    prompt = build_prompt_fixture(
        multi_horizon_signals={"005930": signal_fixture()},
        research_spine=research_spine_fixture(report_id=42),
        risk_intents={"005930": risk_intent_fixture()},
    )
    assert prompt["multi_horizon_signals"]["005930"]["version"] == "multi_horizon_signal_v1"
    assert prompt["research_spine"]["packets"][0]["kis_research"]["version"] == "kis_research_packet_v2"
    assert "raw_pdf" not in str(prompt)
```

- [ ] **Step 2: Add failing action-gate tests**

Assert that an LLM-created stock block is rejected when research is stale,
fewer than two horizons agree, the risk intent is denied, or six symbols are
already active. Assert a protective close remains allowed.

- [ ] **Step 3: Build the contexts once per manager cycle**

In `run_manager_once`, fetch/cache KIS daily bars only for bounded candidate,
held, and pending-block symbols. Build signals, research packets, and provisional
risk intents before prompt construction. Reuse them for prompt, response
validation, telemetry, and status; do not refetch after the LLM response except
for the existing executable quote confirmation.

- [ ] **Step 4: Enforce decisions after the LLM response**

Add one focused method:

```python
def _apply_multi_horizon_risk_contract(
    self, actions: dict[str, list[dict[str, Any]]], *,
    signals: dict[str, dict[str, Any]],
    research_packets: dict[str, dict[str, Any]],
    risk_intents: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return actions that are unchanged or safer; never increase new risk."""
```

It may reduce quantity, convert to waiting entry, or reject. It may not enlarge
quantity, widen stops, or turn a hold/close into an entry. Save all reasons into
block metadata.

- [ ] **Step 5: Implement the 19-to-6 normalization queue**

Create a deterministic ranking payload, not an automatic liquidation routine:

```python
{
  "status": "normalizing",
  "active_symbol_count": 19,
  "target_count": 6,
  "new_symbol_entries_allowed": False,
  "ranked_reduction_candidates": [
    {"symbol": "000001", "rank": 1, "reasons": ["stale_research", "trend_reversal"]}
  ],
}
```

Rank stale/invalid research, broken thesis, 2-of-3 bearish/reversal state,
loss risk, and liquidity. Existing stops, user-owned positions, and orderable
quantity remain authoritative. Require normal safe order paths for any reduction.

- [ ] **Step 6: Verify KIS failure behavior**

Test Naver outage, partial bar failure, ambiguous symbol, prompt contract error,
quote mismatch, and telemetry write failure. Each must create zero new risk but
must not block reconciliation or protective exits.

- [ ] **Step 7: Run KIS domain verification**

Run: `python scripts/verify.py domain --area kis`

If the domain selector does not yet recognize `kis`, run:
`pytest -m kis tests/test_kis_adapter.py tests/test_kis_manager_prompt.py tests/test_kis_block_trader.py tests/test_kis_block_trader_runner.py -q`.

Expected: all pass; runtime isolation checksum test remains green.

- [ ] **Step 8: Review checkpoint**

Inspect a recorded dry-run prompt/action pair and verify research IDs, signal
bar IDs, and risk arithmetic. Do not call a live manager endpoint and do not
commit without explicit approval.

---

### Task 6: Binance Spot/Futures Integration

**Files:**
- Modify: `src/tradecraft/services/binance_manager_prompt.py`
- Modify: `src/tradecraft/services/binance_manager_candidates.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Test: `tests/test_binance_manager_prompt.py`
- Test: `tests/test_binance_manager_candidates.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_binance_block_trader_runner.py`

**Interfaces:**
- Consumes: existing crypto klines/research/funding/spread plus Tasks 2 and 4.
- Produces: lane-specific signal/risk context and deterministic enforcement.

- [ ] **Step 1: Add failing lane and horizon tests**

Test `4h`, `1d`, and rolling `3–7d` inputs; futures long/short direction; spot
long only; same-symbol spot/futures risk aggregation; and unavailable `1d`
history preventing a full 3-of-3 signal.

- [ ] **Step 2: Expose bounded Binance signal context**

Add `multi_horizon_signal` and `provisional_risk_intent` to each executable
candidate. Keep the existing candidate array type and manager prompt contract.
Do not add raw kline arrays to the LLM prompt.

- [ ] **Step 3: Add deterministic post-response enforcement**

```python
def _apply_multi_horizon_risk_contract(
    self, actions: dict[str, list[dict[str, Any]]], *,
    signals: dict[str, dict[str, Any]],
    risk_intents: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return actions that satisfy lane, margin, stop, and portfolio caps."""
```

Require executable price structure, 2-of-3 agreement, stop, funding/spread
acceptability, isolated margin, liquidation buffer, quantity filters, and a
permitted lane authority. Ensure the method cannot increase risk.

- [ ] **Step 4: Enforce leverage and margin at both block and executor boundaries**

Reject cross margin, missing margin mode, leverage above 3, or stop/liquidation
geometry that violates the existing futures safety buffer. The executor repeats
the check so stored/edited blocks cannot bypass manager enforcement.

- [ ] **Step 5: Preserve waiting-entry behavior**

Spread, wick, funding, churn, partial horizon agreement, or price-extension
failures create a waiting-entry block only when risk intent permits a probe;
otherwise reject. Loss positions cannot be increased.

- [ ] **Step 6: Run Binance contract and domain tests**

Run: `python scripts/verify.py domain --area binance`

Expected: all Binance contract, prompt, candidate, ledger, block trader, runner,
and runtime-prompt tests pass.

- [ ] **Step 7: Review checkpoint**

Inspect spot-long, futures-long, futures-short, waiting-entry, and reject dry-run
fixtures. Do not call live order methods and do not commit without explicit approval.

---

### Task 7: Cost-Aware Validation Passport and Authority Ladder

**Files:**
- Create: `src/tradecraft/services/strategy_validation.py`
- Modify: `src/tradecraft/services/live_authority.py`
- Modify: `src/tradecraft/runtime/live_evaluator_runner.py`
- Modify: `src/tradecraft/services/trading_validation.py`
- Test: `tests/test_strategy_validation.py`
- Test: `tests/test_trading_validation.py`
- Test: `tests/test_live_authority.py`
- Test: `tests/test_live_evaluator_runner.py`

**Interfaces:**
- Consumes: parameter-trial returns, chronological windows, shadow observations,
  exchange-proven closed fills, costs, and drawdowns.
- Produces: `StrategyValidationPassportV1` and `risk_authority` while preserving
  the existing `live_grade` field.

- [ ] **Step 1: Add failing statistical metric fixtures**

Use fixed small datasets with hand-checked expected values for net expectancy,
profit factor, drawdown, skew, kurtosis, Sharpe, Deflated Sharpe probability,
and combinatorial PBO. Include a deliberately overfit parameter set that fails.

- [ ] **Step 2: Implement metrics without adding NumPy/SciPy**

```python
@dataclass(frozen=True, slots=True)
class StrategyValidationPassportV1:
    venue: str
    lane: str
    revision_id: str
    replay_status: str
    walk_forward_status: str
    out_of_sample_status: str
    shadow_count: int
    exchange_fill_count: int
    net_expectancy: float
    profit_factor: float
    max_drawdown_pct: float
    deflated_sharpe_probability: float
    pbo_probability: float
    attribution_complete: bool
    risk_authority: str
    failed_reasons: tuple[str, ...]
    version: str = "strategy_validation_passport_v1"
```

Use `math`, `statistics`, and bounded `itertools.combinations`. Store the number
of tried parameter variants and the exact chronological fold identities.

- [ ] **Step 3: Encode immutable promotion floors**

```python
def authority_from_passport(p: StrategyValidationPassportV1) -> str:
    if not replay_wfa_oos_pass(p) or p.shadow_count < 30:
        return "observe_only"
    if p.exchange_fill_count < 20 or p.net_expectancy <= 0 or not p.attribution_complete:
        return "restricted"
    if p.exchange_fill_count < max(60, minimum_track_record(p)):
        return "proving"
    if p.profit_factor < 1.20 or p.max_drawdown_pct > 9.0:
        return "proving"
    if p.deflated_sharpe_probability < 0.95 or p.pbo_probability > 0.20:
        return "proving"
    return "validated"
```

The `proving` grade additionally requires live drawdown below 4% and zero
unknown-fill attribution. Validation is lane-specific for Binance spot,
futures-long, and futures-short.

- [ ] **Step 4: Preserve existing live-authority compatibility**

Keep `live_grade` and existing aliases. Add `risk_authority` and
`max_symbol_risk_pct` to the packet. Map legacy grades only as an upper bound:
`observe_only/insufficient -> observe_only`, `restricted -> restricted`,
`qualified -> proving`, and `scale_candidate -> validated` only when the new
passport itself validates. Existing validation failures may always lower this.

- [ ] **Step 5: Test immediate degradation**

Assert that negative recent expectancy, incomplete provenance, stale validation,
10% drawdown, 12% drawdown, or a failed discipline lowers authority immediately.
No passing historical passport may override a current risk-off/kill switch.

- [ ] **Step 6: Run validation and authority tests**

Run: `pytest tests/test_strategy_validation.py tests/test_trading_validation.py tests/test_live_authority.py tests/test_live_evaluator_runner.py -q`

Expected: all pass and existing compact authority payload assertions remain valid.

- [ ] **Step 7: Review checkpoint**

Print one pass and one fail passport with exact failed reasons. Do not publish it
to live authority or commit without explicit approval.

---

### Task 8: Telemetry, Reflection, and Stored Status

**Files:**
- Modify: `src/tradecraft/services/manager_run_telemetry.py`
- Modify: `src/tradecraft/services/investment_memory.py`
- Modify: `src/tradecraft/services/kis_status_reader.py`
- Modify: `src/tradecraft/services/binance_status_reader.py`
- Modify: `src/tradecraft/api/kis_blocks.py`
- Modify: `src/tradecraft/api/binance_blocks.py`
- Test: `tests/test_manager_run_telemetry.py`
- Test: `tests/test_investment_memory.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_readiness_performance.py`

**Interfaces:**
- Consumes: signal, research, risk, validation, action, order, fill, and outcome IDs.
- Produces: audit-ready attribution and read-only stored status summaries.

- [ ] **Step 1: Add failing telemetry attribution tests**

```python
def test_telemetry_links_signal_research_risk_and_exchange_fill() -> None:
    telemetry = ManagerRunTelemetryV1(
        venue="kis", context_generation_ms=10, prompt_chars=1000,
        llm_latency_ms=20, signal_ids=("sig-1",), evidence_ids=("report:42",),
        risk_intent_ids=("risk-1",), validation_passport_id="passport-1",
        fill_provenance={"exchange_fill_count": 1, "alpha_fill_count": 1},
    ).to_dict()
    assert telemetry["evidence_ids"] == ["report:42"]
```

- [ ] **Step 2: Extend telemetry additively**

Add tuple fields with empty defaults for signal IDs, source-bar IDs, evidence
IDs, risk-intent IDs, and validation passport ID. Preserve every current key and
`manager_run_telemetry_v1` version for backward compatibility; add a nested
`decision_attribution_v1` field rather than changing existing scalar types.

- [ ] **Step 3: Separate reflection dimensions**

Reflection input records thesis quality, research quality, entry quality,
execution quality, exit quality, regime, MFE/MAE, costs, and missed data.
Policy promotion remains a soft caution/preference and cannot create a hard
strategy ban or increase risk authority.

- [ ] **Step 4: Expose stored status only**

Add compact status fields for horizon agreement, research freshness/conflict,
symbol/cluster/venue open risk, drawdown tier, `risk_authority`, failed validation
dimensions, and next promotion requirement. Status readers consume already
stored manager/evaluator snapshots; readiness calls perform no strategy
calculation and no SQLite write.

- [ ] **Step 5: Verify alpha attribution exclusions**

Assert KIS existing-position adoption, Binance wallet adoption, rejected or
unfilled entries, paper fills, and unknown fills never increment exchange-fill
alpha. Recorded costs and realized/unrealized PnL remain separate.

- [ ] **Step 6: Run telemetry, memory, and readiness tests**

Run: `pytest tests/test_manager_run_telemetry.py tests/test_investment_memory.py tests/test_readiness_performance.py -q`

Expected: all pass and readiness write-count assertions remain zero.

- [ ] **Step 7: Review checkpoint**

Inspect a complete audit chain from report/bar through closed exchange fill.
Do not commit without explicit approval.

---

### Task 9: End-to-End Replay, Failure Injection, and Safe Rollout Evidence

**Files:**
- Create: `tests/test_multi_timeframe_trading_replay.py`
- Create: `tests/test_multi_timeframe_failure_modes.py`
- Modify: `scripts/verify.py`
- Modify: `docs/superpowers/plans/2026-07-10-hermes-continuous-implementation-log.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: reproducible validation artifacts and a no-live-change handoff.

- [ ] **Step 1: Build deterministic replay fixtures**

Create KIS rising/falling/gap regimes and Binance trend/reversal/funding/spread
regimes with fixed timestamps. Include realistic fees, tax where applicable,
slippage, funding, and gap/wick costs. The replay must use the same signal and
risk functions as runtime, not copied formulas.

- [ ] **Step 2: Add chronological validation tests**

Assert training parameters are frozen before each test fold, the untouched OOS
fold never participates in selection, all attempted variants are counted for
PBO/DSR, and lane results cannot be pooled to rescue a losing lane.

- [ ] **Step 3: Add full failure matrix**

Inject Naver outage, missing KIS bars, Binance 1d gap, stale quotes, account
mismatch, duplicate order, exchange rejection, reconciliation drift, telemetry
write failure, prompt contract failure, 10% drawdown, and 12% drawdown. Assert
zero unintended new-risk order calls for every failure.

- [ ] **Step 4: Add `strategy` verification area**

Extend `scripts/verify.py domain --area strategy` to run the focused contracts,
KIS/Binance integration tests, validation tests, and runtime isolation test.
Keep `fast`, existing domains, and `full` backward compatible.

- [ ] **Step 5: Run the complete verification ladder**

Run: `python scripts/verify.py fast`

Run: `python scripts/verify.py domain --area strategy`

Run: `ruff check src tests`

Run: `python scripts/verify.py full`

Expected: every command exits 0. Capture duration and the slowest 50 tests.

- [ ] **Step 6: Verify live runtime isolation**

Capture checksums and mtimes for live `.runtime` databases/state before and
after the test ladder. Assert they are identical. Verify no live manager or
order endpoint was called.

- [ ] **Step 7: Produce the rollout evidence packet**

Record changed files, commands, durations, replay metrics, failed validation
dimensions, shadow readiness, unresolved risks, and exact authority grade. If
the passport is not `validated`, the rollout packet must say so plainly and the
system remains at the lower risk grade.

- [ ] **Step 8: Stop before external mutation**

Do not change `.env`, restart live runners, publish a new live authority, delete
data, submit orders, stage, or commit. Present validation evidence first. A
separate user request is required for any external mutation.

## Plan Self-Review

- Spec coverage: all design sections map to Tasks 1–9, including KIS market data,
  Naver research, three horizons, risk/drawdown, KIS migration, Binance margin,
  validation, telemetry, failure handling, and stored status.
- Scope: order execution, reconciliation, kill switches, and current storage
  boundaries are reused; no unrelated UI redesign or strategy family is added.
- Type consistency: `MultiHorizonSignalV1`, `KisResearchPacketV2`,
  `UnifiedRiskIntentV1`, and `StrategyValidationPassportV1` are defined before
  their integration tasks and retain the names used by later tasks.
- Placeholder scan: no TBD/TODO or unspecified error-handling step remains.
- Safety: code and tests may proceed autonomously, but this plan does not grant
  permission for commits, live-setting changes, data deletion, or real orders.
