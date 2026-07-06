# Binance Jue Data Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five recurring Binance Jue data gaps so manager decisions include fresh executable book data, usable catalyst context, crypto-specific market pulse, pre-entry freshness checks, and visible UI diagnostics.

**Architecture:** Keep the current "wide scan -> compact evidence -> LLM manager -> rule executor" architecture. Add deterministic enrichment immediately before the manager prompt and before waiting-entry execution; keep LLM qualitative judgment separate from execution safety gates. Avoid adding a new service unless needed; prefer small focused helpers inside existing Binance/Crypto services.

**Tech Stack:** Python 3.10+, FastAPI service layer, SQLite runtime DBs, static JavaScript UI, pytest, `node --check`.

---

## File Structure

- Modify `src/tradecraft/services/binance_block_trader.py`
  - Enrich executable candidates with live bid/ask before prompt.
  - Add crypto market pulse summary to prompt.
  - Add waiting-entry preflight freshness checks.
- Modify `src/tradecraft/services/crypto_market_research.py`
  - Persist bid/ask into feature JSON and latest context items where available.
- Modify `src/tradecraft/services/crypto_alpha.py`
  - Exclude invalid event-symbol links from context.
  - Prevent invalid symbols from being repeatedly outcome-labeled.
- Modify `src/tradecraft/web/static/app.js`
  - Show freshness and candidate flow diagnostics in Binance hold-note full view.
- Modify `src/tradecraft/web/static/index.html`
  - Bump cache busting version.
- Test `tests/test_binance_block_trader.py`
  - Manager prompt book enrichment.
  - Crypto pulse context.
  - Waiting-entry preflight behavior.
- Test `tests/test_crypto_market_research.py`
  - Bid/ask persistence into features/latest context.
- Test `tests/test_crypto_alpha.py`
  - Invalid links excluded from context and outcome labeling.
- Test `tests/test_static_ui.py`
  - Full view exposes freshness and pipeline counts.

---

### Task 1: Fresh Bid/Ask Enrichment Before Binance Manager Prompt

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write the failing test**

Add this test near the existing manager prompt tests in `tests/test_binance_block_trader.py`.

```python
def test_manager_prompt_enriches_candidates_with_live_book_ticker(tmp_path: Path) -> None:
    class BookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 5.48,
                "ask_price": 5.49,
                "spread_bps": 18.21,
                "source": "fake_book",
                "fetched_at": "2026-05-25T12:00:00+00:00",
            }

    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "items": [
                    {
                        "symbol": "INJUSDT",
                        "features": {
                            "price": 5.488,
                            "spread_bps": 0,
                            "entry_quality": "actionable_now",
                            "derivatives_status": "available",
                        },
                    }
                ],
                "candidates": [
                    {
                        "symbol": "INJUSDT",
                        "market": "spot",
                        "stance": "long_watch",
                        "score": 82,
                    }
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, adapter=BookBinance(), llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]
    candidate = prompt["candidates"][0]
    calculated = candidate["calculated"]

    assert result["status"] == "ok"
    assert candidate["symbol"] == "INJUSDT"
    assert calculated["market_inputs"]["bid_price"] == pytest.approx(5.48)
    assert calculated["market_inputs"]["ask_price"] == pytest.approx(5.49)
    assert calculated["market_inputs"]["spread_bps"] == pytest.approx(18.21)
    assert calculated["market_inputs"]["book_source"] == "fake_book"
    assert calculated["market_inputs"]["book_fresh"] is True
    assert candidate["entry_price"] == pytest.approx(5.49)
    assert prompt["candidate_generation"]["book_enriched_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_enriches_candidates_with_live_book_ticker -q
```

Expected: FAIL because `book_source`, `book_fresh`, and `book_enriched_count` are not present.

- [ ] **Step 3: Implement async book enrichment**

In `src/tradecraft/services/binance_block_trader.py`, add this helper inside `BinanceBlockTrader` before `_manager_executable_candidates`.

```python
    async def _enrich_crypto_research_with_live_books(
        self,
        *,
        crypto_research: dict[str, Any],
        market_universe: dict[str, list[str]],
        max_items: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(crypto_research, dict):
            return crypto_research, {"book_enriched_count": 0, "book_errors": []}
        next_context = dict(crypto_research)
        items = [dict(row) for row in crypto_research.get("items") or [] if isinstance(row, dict)]
        candidates = [
            dict(row)
            for row in crypto_research.get("candidates") or []
            if isinstance(row, dict)
        ]
        feature_by_symbol: dict[str, dict[str, Any]] = {}
        for item in items:
            symbol = str(item.get("symbol") or "").upper().strip()
            features = item.get("features") if isinstance(item.get("features"), dict) else {}
            if symbol:
                feature_by_symbol[symbol] = dict(features)
        targets: list[tuple[str, str]] = []
        for row in candidates[: max(int(max_items), 1)]:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            features = feature_by_symbol.get(symbol, {})
            side = self._side_from_crypto_research_candidate(row)
            market = self._market_from_crypto_research_candidate(
                candidate=row,
                features=features,
                side=side,
            )
            if symbol in set(market_universe.get(market) or []):
                targets.append((symbol, market))
        enriched = 0
        errors: list[dict[str, Any]] = []
        for symbol, market in list(dict.fromkeys(targets)):
            try:
                book = await self._fetch_book_ticker(symbol=symbol, market=market)
            except Exception as exc:
                errors.append({"symbol": symbol, "market": market, "error": str(exc)})
                continue
            bid = _safe_float(book.get("bid_price") or book.get("bid"))
            ask = _safe_float(book.get("ask_price") or book.get("ask"))
            spread = _safe_float(book.get("spread_bps"))
            if bid <= 0 or ask <= 0:
                errors.append({"symbol": symbol, "market": market, "error": "book_bid_ask_missing"})
                continue
            features = feature_by_symbol.setdefault(symbol, {})
            features.update(
                {
                    "bid_price": bid,
                    "ask_price": ask,
                    "spread_bps": spread,
                    "book_source": book.get("source") or "book_ticker",
                    "book_fetched_at": book.get("fetched_at") or utc_now_iso(),
                    "book_market": market,
                    "book_fresh": True,
                }
            )
            enriched += 1
        for item in items:
            symbol = str(item.get("symbol") or "").upper().strip()
            if symbol in feature_by_symbol:
                item["features"] = feature_by_symbol[symbol]
        next_context["items"] = items
        return next_context, {
            "book_enriched_count": enriched,
            "book_errors": errors[:8],
        }
```

Then in `run_manager_once`, immediately after `market_universe = self._build_runtime_market_universe(...)`, call:

```python
        crypto_research, book_generation = await self._enrich_crypto_research_with_live_books(
            crypto_research=crypto_research,
            market_universe=market_universe,
            max_items=max(int(self.config.quant_context_limit), 8),
        )
        market_universe = self._build_runtime_market_universe(
            blocks=blocks,
            candidates=candidates or [],
            crypto_research=crypto_research,
        )
```

Then after `_manager_executable_candidates(...)` returns `candidate_generation`, merge:

```python
        candidate_generation = {**candidate_generation, **book_generation}
```

In `_design_crypto_candidate_price_plan`, add the book fields to `market_inputs`:

```python
                "book_source": _clean_text(features.get("book_source"), limit=80),
                "book_fetched_at": _clean_text(features.get("book_fetched_at"), limit=80),
                "book_market": _clean_text(features.get("book_market"), limit=20),
                "book_fresh": bool(features.get("book_fresh")),
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_enriches_candidates_with_live_book_ticker tests/test_binance_block_trader.py::test_manager_prompt_builds_executable_price_design_from_crypto_research -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
git commit -m "feat: enrich binance manager candidates with live books"
```

---

### Task 2: Persist Bid/Ask in Crypto Market Research Context

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write failing persistence test**

Add this test to `tests/test_crypto_market_research.py`.

```python
def test_crypto_market_research_features_include_bid_ask_from_book(tmp_path: Path) -> None:
    class FakeBinance:
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 99.9,
                "ask_price": 100.1,
                "spread_bps": 20.0,
                "price": 100.0,
                "quote_volume_usdt": 1_000_000,
                "change_pct_24h": 1.2,
            }

        async def fetch_klines(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {"open_time": 1, "open": 99, "high": 101, "low": 98, "close": 100, "volume": 10},
                {"open_time": 2, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 12},
            ]

    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(tmp_path / "crypto_market_research.db"),
            enabled=True,
            universe="TESTUSDT",
            candidate_limit=1,
        ),
        binance=FakeBinance(),
        codex_runtime=None,
    )

    result = asyncio.run(service.collect_once(symbols=["TESTUSDT"]))
    context = service.latest_context(symbols=["TESTUSDT"], limit=1)
    features = context["items"][0]["features"]

    assert result["status"] == "ok"
    assert features["bid_price"] == pytest.approx(99.9)
    assert features["ask_price"] == pytest.approx(100.1)
    assert features["spread_bps"] == pytest.approx(20.0)
    assert features["book_fresh"] is True
```

If constructor names differ in the current file, adapt only the constructor wiring, not the assertion contract.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_crypto_market_research_features_include_bid_ask_from_book -q
```

Expected: FAIL because latest feature JSON does not expose bid/ask.

- [ ] **Step 3: Add bid/ask to feature JSON**

In `src/tradecraft/services/crypto_market_research.py`, find the feature-building section around the existing `spread_bps` field. Extend the feature dict:

```python
            "bid_price": _to_float(book.get("bid_price") or book.get("bid")),
            "ask_price": _to_float(book.get("ask_price") or book.get("ask")),
            "book_source": str(book.get("source") or "book_ticker"),
            "book_fetched_at": str(book.get("fetched_at") or captured_at),
            "book_fresh": _to_float(book.get("bid_price") or book.get("bid")) > 0
            and _to_float(book.get("ask_price") or book.get("ask")) > 0,
```

If the service currently stores only `feature_json`, do not add DB columns. Store these values inside `feature_json` to avoid schema churn.

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_crypto_market_research_features_include_bid_ask_from_book -q
pytest tests/test_crypto_market_research.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/crypto_market_research.py tests/test_crypto_market_research.py
git commit -m "feat: persist crypto research book bid ask"
```

---

### Task 3: Crypto Alpha Invalid Symbol Cleanup

**Files:**
- Modify: `src/tradecraft/services/crypto_alpha.py`
- Test: `tests/test_crypto_alpha.py`

- [ ] **Step 1: Write failing context exclusion test**

Add this test to `tests/test_crypto_alpha.py`.

```python
def test_crypto_alpha_context_excludes_invalid_symbol_links(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"), enabled=True),
        binance=None,
    )
    service.repository.save_event(
        {
            "source_id": "coinbase_blog",
            "event_type": "listing",
            "title": "Bad symbol scrape",
            "summary": "navigation text was misread",
            "event_time": "2026-05-25T12:00:00+00:00",
            "detected_at": "2026-05-25T12:00:00+00:00",
            "confidence": 0.8,
            "importance": 0.8,
            "symbols": [
                {
                    "symbol": "REQUIREDUSDT",
                    "base_asset": "REQUIRED",
                    "validity_status": "invalid",
                    "validity_reason": "binance_spot_invalid_symbol",
                }
            ],
        }
    )

    context = service.context_pack(symbols=["REQUIREDUSDT"], limit=10)

    assert context["event_count"] == 0
    assert context["events"] == []
    assert context["data_gaps"]
```

If `save_event` helper does not exist, use the repository insert method already present in `tests/test_crypto_alpha.py`; preserve the same expected context contract.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_crypto_alpha_context_excludes_invalid_symbol_links -q
```

Expected: FAIL because invalid event links are still visible or counted.

- [ ] **Step 3: Filter invalid links in context and outcome labeling**

In `src/tradecraft/services/crypto_alpha.py`, add a reusable predicate:

```python
def _is_valid_alpha_symbol_link(row: dict[str, Any]) -> bool:
    status = str(row.get("validity_status") or "unknown").strip().lower()
    symbol = str(row.get("symbol") or "").upper().strip()
    if status == "invalid":
        return False
    if not symbol.endswith("USDT"):
        return False
    invalid_reasons = str(row.get("validity_reason") or "").lower()
    return "invalid_symbol" not in invalid_reasons
```

Use it in:

```python
context_pack(...)
label_due_outcomes(...)
```

For `label_due_outcomes`, skip invalid links and record skipped counts in the result:

```python
if not _is_valid_alpha_symbol_link(link):
    skipped_invalid += 1
    continue
```

Return:

```python
"skipped_invalid_symbols": skipped_invalid
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_crypto_alpha_context_excludes_invalid_symbol_links tests/test_crypto_alpha.py -q
```

Expected: PASS and logs no longer repeatedly request invalid symbols.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/crypto_alpha.py tests/test_crypto_alpha.py
git commit -m "fix: exclude invalid crypto alpha symbols"
```

---

### Task 4: Add Binance-Specific Crypto Market Pulse to Manager Prompt

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing prompt test**

Add this test to `tests/test_binance_block_trader.py`.

```python
def test_manager_prompt_includes_crypto_market_pulse(tmp_path: Path) -> None:
    class CryptoResearch:
        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            return {
                "status": "ok",
                "regime": {"label": "risk_off", "summary": "majors weak"},
                "items": [
                    {"symbol": "BTCUSDT", "features": {"price": 100, "change_pct_24h": -2.0, "spread_bps": 1}},
                    {"symbol": "ETHUSDT", "features": {"price": 200, "change_pct_24h": -3.0, "spread_bps": 1}},
                    {"symbol": "SOLUSDT", "features": {"price": 50, "change_pct_24h": 4.0, "spread_bps": 3}},
                ],
                "candidates": [
                    {"symbol": "BTCUSDT", "market": "spot", "stance": "hold"},
                    {"symbol": "ETHUSDT", "market": "futures", "stance": "short_watch"},
                    {"symbol": "SOLUSDT", "market": "spot", "stance": "long_watch"},
                ],
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    pulse = llm.calls[0]["payload"]["crypto_market_pulse"]

    assert result["status"] == "ok"
    assert pulse["status"] == "ok"
    assert pulse["major_count"] == 3
    assert pulse["long_candidate_count"] == 1
    assert pulse["short_candidate_count"] == 1
    assert "crypto_market_pulse" in llm.calls[0]["payload"]["decision_inputs"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_market_pulse -q
```

Expected: FAIL because `crypto_market_pulse` is missing.

- [ ] **Step 3: Implement pulse builder**

Add this method to `BinanceBlockTrader`.

```python
    @staticmethod
    def _crypto_market_pulse_context(crypto_research: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(crypto_research, dict):
            return {"status": "missing", "data_gaps": ["crypto_research_missing"]}
        items = [row for row in crypto_research.get("items") or [] if isinstance(row, dict)]
        candidates = [row for row in crypto_research.get("candidates") or [] if isinstance(row, dict)]
        major_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}
        major_rows = []
        spread_values = []
        for row in items:
            symbol = str(row.get("symbol") or "").upper().strip()
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            if symbol in major_symbols:
                major_rows.append(
                    {
                        "symbol": symbol,
                        "change_pct_24h": _safe_float(features.get("change_pct_24h")),
                        "spread_bps": _safe_float(features.get("spread_bps")),
                        "entry_quality": _clean_text(features.get("entry_quality"), limit=40),
                    }
                )
            spread = _safe_float(features.get("spread_bps"))
            if spread > 0:
                spread_values.append(spread)
        long_count = sum(1 for row in candidates if "long" in str(row.get("stance") or "").lower())
        short_count = sum(1 for row in candidates if "short" in str(row.get("stance") or "").lower())
        avg_major_change = (
            sum(row["change_pct_24h"] for row in major_rows) / len(major_rows)
            if major_rows
            else 0.0
        )
        avg_spread = sum(spread_values) / len(spread_values) if spread_values else 0.0
        data_gaps = []
        if not major_rows:
            data_gaps.append("major_crypto_rows_missing")
        if not candidates:
            data_gaps.append("crypto_candidates_missing")
        return {
            "status": "ok" if not data_gaps else "partial",
            "regime": crypto_research.get("regime") or {},
            "major_count": len(major_rows),
            "major_rows": major_rows[:6],
            "avg_major_change_pct_24h": round(avg_major_change, 4),
            "avg_spread_bps": round(avg_spread, 4),
            "candidate_count": len(candidates),
            "long_candidate_count": long_count,
            "short_candidate_count": short_count,
            "hold_candidate_count": max(len(candidates) - long_count - short_count, 0),
            "data_gaps": data_gaps,
        }
```

In `run_manager_once`, after `crypto_research` enrichment:

```python
        crypto_market_pulse = self._crypto_market_pulse_context(crypto_research)
```

Add to prompt:

```python
            "crypto_market_pulse": crypto_market_pulse,
```

Add to `decision_inputs`:

```python
                "crypto_market_pulse",
```

Update `hold_decision_policy` text to say:

```python
"Use crypto_market_pulse for market-wide crypto regime; do not report market_pulse missing when crypto_market_pulse is present."
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_market_pulse tests/test_binance_block_trader.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
git commit -m "feat: add crypto market pulse to binance manager"
```

---

### Task 5: Waiting-Entry Preflight Freshness Check

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing preflight test**

Add this test to `tests/test_binance_block_trader.py`.

```python
def test_waiting_entry_does_not_submit_when_book_preflight_is_stale(tmp_path: Path) -> None:
    class BadBookBinance(_FakeBinance):
        async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
            return {
                "symbol": symbol,
                "market": market,
                "bid_price": 0,
                "ask_price": 0,
                "spread_bps": 0,
                "source": "fake_bad_book",
            }

    adapter = BadBookBinance()
    trader = _trader(tmp_path, adapter=adapter, execute_spot=True, enabled=True)
    trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 95.0,
            "entry_style": "wait_for_price",
            "entry_trigger_price": 101.0,
            "entry_trigger_operator": "<=",
            "status": "proposed",
            "metadata": {"calculated_price_plan": {"reward_risk": 2.0}},
        }
    )

    result = asyncio.run(trader.executor_tick())
    block = trader.list_blocks()[0]

    assert result["action_count"] == 0
    assert adapter.spot_orders == []
    assert block["status"] == "paused"
    assert "preflight_book_invalid" in block["risk_note"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_waiting_entry_does_not_submit_when_book_preflight_is_stale -q
```

Expected: FAIL because current waiting entry path does not pause on invalid book preflight.

- [ ] **Step 3: Implement preflight helper**

In `src/tradecraft/services/binance_block_trader.py`, add:

```python
    async def _preflight_waiting_entry(self, block: dict[str, Any]) -> dict[str, Any]:
        symbol = str(block.get("symbol") or "").upper()
        market = _normalize_market(block.get("market"))
        book = await self._fetch_book_ticker(symbol=symbol, market=market)
        bid = _safe_float(book.get("bid_price") or book.get("bid"))
        ask = _safe_float(book.get("ask_price") or book.get("ask"))
        spread = _safe_float(book.get("spread_bps"))
        if bid <= 0 or ask <= 0:
            return {
                "status": "blocked",
                "reason": "preflight_book_invalid",
                "book": book,
            }
        if spread > 35:
            return {
                "status": "blocked",
                "reason": f"preflight_spread_too_wide:{round(spread, 2)}bps",
                "book": book,
            }
        return {"status": "ok", "book": book}
```

In `_maybe_create_entry`, immediately before submitting an entry order:

```python
        preflight = await self._preflight_waiting_entry(block)
        if preflight.get("status") != "ok":
            self.repository.update_block(
                block["block_id"],
                {
                    "status": "paused",
                    "risk_note": _clean_text(preflight.get("reason"), limit=2000),
                    "metadata": {
                        **(block.get("metadata") if isinstance(block.get("metadata"), dict) else {}),
                        "entry_preflight": preflight,
                    },
                },
            )
            self.repository.add_event(
                block["block_id"],
                "entry_preflight_blocked",
                message=str(preflight.get("reason") or "preflight_blocked"),
                payload=preflight,
            )
            return None
```

Use the repository method names already used elsewhere in the file; if `add_event` is named differently, use the existing event insert helper from nearby `_submit_entry_for_block`.

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_waiting_entry_does_not_submit_when_book_preflight_is_stale tests/test_binance_block_trader.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
git commit -m "feat: add binance waiting entry preflight"
```

---

### Task 6: UI Data Gap and Freshness Diagnostics

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/index.html`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Write failing UI static test**

Add this test to `tests/test_static_ui.py`.

```python
def test_binance_hold_full_view_exposes_freshness_diagnostics() -> None:
    js = _js()

    assert "book_fresh" in js
    assert "book_fetched_at" in js
    assert "crypto_market_pulse" in js
    assert "candidate_generation.book_enriched_count" in js
    assert "호가 보강" in js
    assert "프리플라이트" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_static_ui.py::test_binance_hold_full_view_exposes_freshness_diagnostics -q
```

Expected: FAIL because the current full view does not expose these strings.

- [ ] **Step 3: Extend Binance hold full view**

In `src/tradecraft/web/static/app.js`, inside `renderBinanceHoldDecisionDetailText`, add:

```javascript
  const pulse = prompt.crypto_market_pulse && typeof prompt.crypto_market_pulse === "object"
    ? prompt.crypto_market_pulse
    : {};
```

Extend `candidateLine`:

```javascript
    const marketInputs = calculated.market_inputs && typeof calculated.market_inputs === "object"
      ? calculated.market_inputs
      : {};
    const bookFresh = marketInputs.book_fresh ? "fresh" : "stale";
    const bookAt = marketInputs.book_fetched_at || "-";
```

Then include:

```javascript
      `book ${bookFresh}`,
      `book_at ${bookAt}`,
```

Extend the summary body:

```javascript
    `- 호가 보강: ${generation.book_enriched_count ?? "--"}`,
    `- 호가 오류: ${Array.isArray(generation.book_errors) ? generation.book_errors.length : "--"}`,
    `- 크립토 펄스: ${pulse.status || "--"} · majors ${pulse.major_count ?? "--"} · long ${pulse.long_candidate_count ?? "--"} · short ${pulse.short_candidate_count ?? "--"}`,
    `- 프리플라이트: 대기블록 체결 직전 book freshness/spread 재검증`,
```

In `renderBinanceHoldDecision`, update `flowChips`:

```javascript
    `호가 보강 ${generation.book_enriched_count ?? "--"}`,
    `펄스 ${prompt.crypto_market_pulse?.status || "--"}`,
```

Bump cache busting in `src/tradecraft/web/static/index.html`:

```html
?v=20260525_binance_data_gap_closure_v1
```

- [ ] **Step 4: Run UI checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
pytest tests/test_static_ui.py::test_binance_hold_full_view_exposes_freshness_diagnostics tests/test_static_ui.py -q
git diff --check -- src/tradecraft/web/static/app.js src/tradecraft/web/static/index.html tests/test_static_ui.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/web/static/app.js src/tradecraft/web/static/index.html tests/test_static_ui.py
git commit -m "feat: show binance data gap diagnostics"
```

---

### Task 7: Integration Verification and Runtime Restart

**Files:**
- No source changes expected.
- Verify runtime DBs and logs.

- [ ] **Step 1: Run focused regression suite**

Run:

```bash
pytest tests/test_binance_block_trader.py tests/test_crypto_market_research.py tests/test_crypto_alpha.py tests/test_static_ui.py -q
```

Expected: PASS.

- [ ] **Step 2: Run syntax and diff checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
python3 -m py_compile src/tradecraft/services/binance_block_trader.py src/tradecraft/services/crypto_market_research.py src/tradecraft/services/crypto_alpha.py
git diff --check
```

Expected: no output from `git diff --check`, no syntax errors.

- [ ] **Step 3: Restart runtime sessions**

Run:

```bash
tmux kill-session -t hermes-binance-block-trader 2>/dev/null || true
tmux new-session -d -s hermes-binance-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-binance-block-trader 2>&1 | tee -a .runtime/binance_block_trader.log'

tmux kill-session -t hermes-crypto-research 2>/dev/null || true
tmux new-session -d -s hermes-crypto-research 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-crypto-market-research 2>&1 | tee -a .runtime/crypto_market_research.log'

tmux kill-session -t hermes-crypto-alpha 2>/dev/null || true
tmux new-session -d -s hermes-crypto-alpha 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-crypto-alpha 2>&1 | tee -a .runtime/crypto_alpha.log'

tmux kill-session -t hermes-control 2>/dev/null || true
tmux new-session -d -s hermes-control 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-control --host 127.0.0.1 --port 18080 2>&1 | tee -a .runtime/control.log'
```

Expected: all sessions exist in `tmux list-sessions`.

- [ ] **Step 4: Verify health and readiness**

Run:

```bash
curl -sS http://127.0.0.1:18080/api/health
TOKEN=$(python3 - <<'PY'
from tradecraft.config import AppSettings
print(AppSettings().admin_token)
PY
)
curl -sS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:18080/api/ops/readiness
```

Expected:

```json
{"status":"ok","service":"tradecraft-control",...}
```

and readiness status should be `green` or have no blocker related to Binance/Crypto.

- [ ] **Step 5: Verify next manager prompt quality without forcing live action**

Do not manually run live manager unless the user explicitly asks. Instead inspect the latest prompt after the next scheduled run, or run a local dry construction script that does not call LLM:

```bash
python3 - <<'PY'
import asyncio, json
from tradecraft.config import AppSettings
from tradecraft.runtime.binance_block_trader_runner import _build_trader

async def main():
    trader = _build_trader(AppSettings())
    context = trader._crypto_research_context(symbols=[])
    universe = trader._build_runtime_market_universe(blocks=[], candidates=[], crypto_research=context)
    context, book = await trader._enrich_crypto_research_with_live_books(
        crypto_research=context,
        market_universe=universe,
        max_items=12,
    )
    account = trader._normalize_account_snapshot(await trader._collect_account_snapshot())
    candidates, generation = trader._manager_executable_candidates(
        provided_candidates=[],
        crypto_research=context,
        market_universe=universe,
        account=account,
    )
    print(json.dumps({
        "book": book,
        "candidate_generation": generation,
        "first_candidate": candidates[0] if candidates else {},
    }, ensure_ascii=False, indent=2)[:6000])

asyncio.run(main())
PY
```

Expected:

- `book_enriched_count > 0`
- first candidate has non-zero `market_inputs.bid_price`
- first candidate has non-zero `market_inputs.ask_price`
- `candidate_generation.candidate_count > 0`

- [ ] **Step 6: Final commit**

If Tasks 1-6 were not committed individually, commit all remaining changes:

```bash
git add src/tradecraft/services/binance_block_trader.py src/tradecraft/services/crypto_market_research.py src/tradecraft/services/crypto_alpha.py src/tradecraft/web/static/app.js src/tradecraft/web/static/index.html tests/test_binance_block_trader.py tests/test_crypto_market_research.py tests/test_crypto_alpha.py tests/test_static_ui.py docs/superpowers/plans/2026-05-25-binance-data-gap-closure.md
git commit -m "feat: close binance jue data gaps"
```

---

## Self-Review

**Spec coverage:**
- Fresh bid/ask gap: Task 1 and Task 2.
- Crypto alpha invalid/empty context: Task 3.
- Binance crypto market pulse: Task 4.
- Waiting entry freshness preflight: Task 5.
- UI full-view diagnostics: Task 6.
- Runtime verification: Task 7.

**Placeholder scan:** No `TBD`, `TODO`, or "implement later" instructions remain. Each task includes explicit tests, code contracts, commands, and expected results.

**Type consistency:** The plan consistently uses `candidate_generation`, `book_enriched_count`, `book_errors`, `crypto_market_pulse`, `calculated.market_inputs`, and `book_fresh` across backend, tests, and UI.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-25-binance-data-gap-closure.md`. Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
