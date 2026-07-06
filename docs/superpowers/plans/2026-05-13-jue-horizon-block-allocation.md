# Jue Horizon Block Allocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 쥬 manage blocks as a balanced portfolio across cash, short-term, mid-term, long-term, and ETF/core horizons instead of treating every new block as a one-share short-term scout trade.

**Architecture:** Keep the current block ledger and rule executor, but add horizon metadata, target allocation bands, and horizon-aware action authority. During regular KRX market hours, the LLM manager reviews the full portfolio every 30 minutes across short, mid, long, ETF/core, and cash; outside market hours it only runs scheduled rituals/manual calls. The rule executor still watches prices continuously, but automatic tick-based exits are reserved for short-term blocks unless a force-close is explicitly requested. UI and Telegram show the current vs target balance by color.

**Tech Stack:** Python/FastAPI/SQLite services in `src/tradecraft`, static frontend in `src/tradecraft/web/static`, pytest tests.

---

## File Structure

- Modify `src/tradecraft/services/kis_block_trader.py`
  - Add horizon constants, allocation targets, ETF/core universe payload, metadata persistence, full-horizon manager prompts, horizon-aware rule execution.
- Modify `src/tradecraft/config.py`
  - Add optional env knobs for target allocation and ETF universe.
- Modify `src/tradecraft/services/investment_memory.py`
  - Add persona/skill memory language so 쥬 understands block horizons and portfolio balance.
- Modify `src/tradecraft/services/telegram_cli.py`
  - Show horizon and allocation summary in block/market messages.
- Modify `src/tradecraft/web/static/app.js`
  - Render horizon color chips, allocation bars, and block cards grouped by horizon.
- Modify `src/tradecraft/web/static/style.css`
  - Add dark-theme color tokens for short/mid/long/ETF/cash.
- Test `tests/test_kis_block_trader.py`
  - Unit coverage for horizon persistence, manager schema, allocation summary, rule executor behavior.
- Test `tests/test_investment_memory.py`
  - Memory context includes horizon policy and no “all blocks are short-term” drift.
- Test `tests/test_telegram_cli.py`
  - Telegram text includes horizon/portfolio balance.

---

### Task 1: Add Horizon Metadata and Allocation Summary

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write failing tests**

Add tests that create blocks with horizons and verify metadata plus allocation summary:

```python
def test_block_horizon_is_stored_in_metadata_and_exposed_in_snapshot(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    block = trader.repository.create_block(
        {
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "qty": 2,
            "qty_open": 2,
            "entry_price": 100_000,
            "target_price": 130_000,
            "stop_price": 90_000,
            "status": "open",
            "metadata": {"horizon": "mid", "block_color": "mid"},
        }
    )
    trader.kis.prices["277810"] = 110_000  # type: ignore[attr-defined]

    snapshot = asyncio.run(trader.snapshot())
    rendered = next(row for row in snapshot["blocks"] if row["block_id"] == block["block_id"])

    assert rendered["horizon"] == "mid"
    assert rendered["block_color"] == "mid"
    assert snapshot["horizon_allocation"]["items"][0]["horizon"] == "mid"
```

Add a second test for cash:

```python
def test_horizon_allocation_includes_cash_bucket(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    snapshot = asyncio.run(trader.snapshot())

    horizons = {row["horizon"] for row in snapshot["horizon_allocation"]["items"]}
    assert "cash" in horizons
    assert snapshot["horizon_allocation"]["targets"]["cash"] > 0
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_block_horizon_is_stored_in_metadata_and_exposed_in_snapshot \
  tests/test_kis_block_trader.py::test_horizon_allocation_includes_cash_bucket -q
```

Expected: FAIL because `horizon` and `horizon_allocation` do not exist yet.

- [ ] **Step 3: Implement minimal horizon support**

In `kis_block_trader.py`, add constants near `ACTIVE_BLOCK_STATUSES`:

```python
BLOCK_HORIZONS = {"short", "mid", "long", "core_etf"}
HORIZON_COLORS = {
    "short": "short",
    "mid": "mid",
    "long": "long",
    "core_etf": "etf",
    "cash": "cash",
}
DEFAULT_HORIZON_TARGETS = {
    "cash": 0.30,
    "short": 0.15,
    "mid": 0.30,
    "long": 0.15,
    "core_etf": 0.10,
}
```

Add helpers:

```python
def _normalize_horizon(value: Any) -> str:
    horizon = str(value or "short").strip().lower()
    return horizon if horizon in BLOCK_HORIZONS else "short"
```

Update `_decorate_block()` so returned blocks include:

```python
metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
horizon = _normalize_horizon(metadata.get("horizon"))
payload["horizon"] = horizon
payload["block_color"] = HORIZON_COLORS.get(horizon, "short")
```

Add `_horizon_allocation_summary(account, blocks, quotes)` that returns:

```python
{
    "status": "ok",
    "targets": DEFAULT_HORIZON_TARGETS,
    "items": [
        {"horizon": "cash", "current_value_krw": cash, "current_weight": 0.0, "target_weight": 0.30, "drift": 0.0},
        ...
    ],
}
```

Include it in `snapshot()` as `horizon_allocation`.

- [ ] **Step 4: Run tests**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_block_horizon_is_stored_in_metadata_and_exposed_in_snapshot \
  tests/test_kis_block_trader.py::test_horizon_allocation_includes_cash_bucket -q
```

Expected: PASS.

---

### Task 2: Make BlockManager Review All Horizons Every 30 Minutes During Market Hours

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/config.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write failing manager schema test**

```python
def test_manager_prompt_requires_horizon_and_portfolio_balance(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = KISBlockTrader(
        config=KISBlockTraderConfig(db_path=str(tmp_path / "kis_blocks.db"), use_naver_fallback=False),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
    )
    trader.clock = lambda: {"session": "regular", "is_market_open": True}  # type: ignore[method-assign]

    asyncio.run(trader.run_manager_once())
    prompt = json.loads(llm.calls[0]["messages"][1]["content"])

    assert prompt["portfolio_balance"]["targets"]["cash"] == 0.30
    assert "etf_universe" in prompt
    assert prompt["horizon_review"]["cadence"] == "regular_market_30m_full_portfolio"
    assert prompt["output_schema"]["create_blocks"][0]["horizon"] == "short|mid|long|core_etf"
    assert prompt["horizon_action_authority"]["short"] == "active_trade"
    assert prompt["horizon_action_authority"]["core_etf"] == "rebalance_bias"
```

Add persistence test:

```python
def test_manager_create_block_preserves_horizon_metadata(tmp_path: Path) -> None:
    trader = _trader(
        tmp_path,
        llm_payload={
            "create_blocks": [
                {
                    "symbol": "069500",
                    "qty": 3,
                    "target_price": 50_000,
                    "stop_price": 45_000,
                    "horizon": "core_etf",
                    "thesis": "시장 대표 ETF core 블록",
                    "confidence": 0.72,
                }
            ]
        },
    )
    trader.kis.prices["069500"] = 48_000  # type: ignore[attr-defined]
    trader.clock = lambda: {"session": "regular", "is_market_open": True}  # type: ignore[method-assign]

    asyncio.run(trader.run_manager_once())
    block = trader.repository.list_blocks()[0]

    assert block["metadata"]["horizon"] == "core_etf"
```

- [ ] **Step 2: Run tests**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_requires_horizon_and_portfolio_balance \
  tests/test_kis_block_trader.py::test_manager_create_block_preserves_horizon_metadata -q
```

Expected: FAIL.

- [ ] **Step 3: Implement prompt/schema changes**

Add config fields:

```python
block_horizon_targets: str = Field(
    default="cash:0.30,short:0.15,mid:0.30,long:0.15,core_etf:0.10",
    validation_alias=AliasChoices("TRADECRAFT_KIS_BLOCK_HORIZON_TARGETS"),
)
kis_block_trader_etf_universe: str = Field(
    default="069500:KODEX 200,102110:TIGER 200,091160:KODEX 반도체",
    validation_alias=AliasChoices("TRADECRAFT_KIS_BLOCK_TRADER_ETF_UNIVERSE"),
)
```

In `KISBlockTraderConfig`, add:

```python
horizon_targets: dict[str, float] | None = None
etf_universe: list[dict[str, str]] | None = None
```

Add to manager prompt:

```python
"portfolio_balance": self._horizon_allocation_summary(account=account, blocks=blocks, quotes=quote_map),
"etf_universe": self.config.etf_universe or DEFAULT_ETF_UNIVERSE,
"horizon_review": {
    "cadence": "regular_market_30m_full_portfolio",
    "market_hours_only": True,
    "instruction": (
        "Every regular-market manager run reviews all horizons: short, mid, "
        "long, core_etf, and cash. Reviewing all horizons does not mean all "
        "horizons have the same trading authority."
    ),
},
"horizon_policy": {
    "short": "intraday to 1 week; tick target/stop allowed",
    "mid": "2 weeks to 3 months; reviewed every 30m during market hours, action requires thesis or allocation reason",
    "long": "3 months plus; reviewed every 30m during market hours, default action is hold/add/rebalance unless thesis breaks",
    "core_etf": "ETF/core allocation; reviewed every 30m during market hours, rebalance and drift management",
    "cash": "dry powder and volatility buffer",
},
"horizon_action_authority": {
    "short": "active_trade",
    "mid": "selective_adjust_or_close",
    "long": "hold_add_rebalance_bias",
    "core_etf": "rebalance_bias",
    "cash": "allocation_buffer",
},
```

Update output schema for `create_blocks` and `adopt_existing_blocks`:

```python
"horizon": "short|mid|long|core_etf",
"allocation_reason": "why this block improves portfolio balance",
```

Update `_sanitize_manager_actions()` to copy normalized `horizon` and `allocation_reason`.

Update `_create_and_enter_block()` and `_adopt_existing_block()` metadata:

```python
"horizon": _normalize_horizon(row.get("horizon")),
"allocation_reason": row.get("allocation_reason") or "",
```

- [ ] **Step 4: Wire runtime settings**

In `main.py` and `runtime/kis_block_trader_runner.py`, parse settings into config:

```python
horizon_targets=_parse_horizon_targets(settings.block_horizon_targets),
etf_universe=_parse_etf_universe(settings.kis_block_trader_etf_universe),
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_requires_horizon_and_portfolio_balance \
  tests/test_kis_block_trader.py::test_manager_create_block_preserves_horizon_metadata -q
```

Expected: PASS.

---

### Task 3: Horizon-Aware Rule Executor With Full-Horizon Manager Review

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write failing tests**

```python
def test_mid_block_target_touch_creates_exit_signal_without_auto_sell(tmp_path: Path) -> None:
    trader = _trader(tmp_path, execute_orders=True)
    block = trader.repository.create_block(
        {
            "symbol": "277810",
            "qty": 1,
            "qty_open": 1,
            "entry_price": 100_000,
            "target_price": 110_000,
            "stop_price": 95_000,
            "status": "open",
            "metadata": {"horizon": "mid"},
        }
    )
    trader.kis.prices["277810"] = 111_000  # type: ignore[attr-defined]

    tick = asyncio.run(trader.executor_tick(manual=True))

    assert tick["action_count"] == 1
    assert trader.repository.list_orders(block["block_id"]) == []
    assert trader.repository.list_events(block_id=block["block_id"])[0]["event_type"] == "exit_signal"
```

Add short-block regression:

```python
def test_short_block_target_touch_still_sells_by_rule(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    block = trader.repository.create_block(
        {
            "symbol": "277810",
            "qty": 1,
            "qty_open": 1,
            "entry_price": 100_000,
            "target_price": 101_000,
            "stop_price": 95_000,
            "status": "open",
            "metadata": {"horizon": "short"},
        }
    )
    trader.kis.prices["277810"] = 102_000  # type: ignore[attr-defined]

    tick = asyncio.run(trader.executor_tick(manual=True))

    assert tick["action_count"] == 1
    assert trader.repository.list_orders(block["block_id"])[0]["side"] == "sell"
```

- [ ] **Step 2: Run tests**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_mid_block_target_touch_creates_exit_signal_without_auto_sell \
  tests/test_kis_block_trader.py::test_short_block_target_touch_still_sells_by_rule -q
```

Expected: first FAIL, second may PASS.

- [ ] **Step 3: Implement horizon-aware exits**

In `_maybe_exit_block()`, before placing sell order:

```python
horizon = _normalize_horizon((block.get("metadata") or {}).get("horizon"))
if reason and horizon != "short" and not block.get("force_exit_requested"):
    self.repository.add_event(
        str(block["block_id"]),
        "exit_signal",
        f"{horizon} block touched {reason}; next 30m manager review will decide action",
        {
            "horizon": horizon,
            "reason": reason,
            "price": price,
            "manager_review": "regular_market_30m_full_portfolio",
        },
    )
    return {"status": "exit_signal", "reason": reason, "block_id": block["block_id"]}
```

Guard duplicate `exit_signal` spam by checking the latest event for the same block/reason within the current tick cycle. The next normal 30-minute manager run receives pending `exit_signal` events in its prompt and can choose `update_blocks`, `close_blocks`, `pause_blocks`, or no action. This keeps mid/long/ETF visible to 쥬 every 30 minutes while preventing the low-level price watcher from turning them into accidental short-term trades.

- [ ] **Step 4: Run tests**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_mid_block_target_touch_creates_exit_signal_without_auto_sell \
  tests/test_kis_block_trader.py::test_short_block_target_touch_still_sells_by_rule -q
```

Expected: PASS.

---

### Task 4: Memory Skills and Prompt Identity Update

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write failing test**

```python
def test_memory_skills_describe_horizon_balanced_portfolio(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize(force=True)
    pack = service.context_pack(max_chars=5000)
    text = json.dumps(pack, ensure_ascii=False)

    assert "단기" in text
    assert "중기" in text
    assert "장기" in text
    assert "ETF" in text
    assert "현금" in text
    assert "모든 블록을 단기처럼 취급하지 않는다" in text
```

- [ ] **Step 2: Run test**

Run:

```bash
pytest tests/test_investment_memory.py::test_memory_skills_describe_horizon_balanced_portfolio -q
```

Expected: FAIL.

- [ ] **Step 3: Update default memory files**

In `_default_memory_files()`, update:

- `persona.md`: 쥬 is a portfolio/block allocation partner.
- `skills/block_manager.md`: block horizons and ETF/core blocks.
- `skills/risk_manager.md`: short tick exits vs mid/long manager review.
- `policies/trading.md`: cash is a managed allocation, not idle money.

Required Korean sentence:

```text
모든 블록을 단기처럼 취급하지 않는다. 단기, 중기, 장기, ETF/core, 현금은 서로 다른 역할과 청산 기준을 가진다.
정규장 30분 매니저 루프에서는 모든 horizon을 함께 검토하지만, 단기·중기·장기·ETF/core의 행동 권한은 다르게 적용한다.
```

- [ ] **Step 4: Run test**

Run:

```bash
pytest tests/test_investment_memory.py::test_memory_skills_describe_horizon_balanced_portfolio -q
```

Expected: PASS.

---

### Task 5: UI Allocation Colors and Block Grouping

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`

- [ ] **Step 1: Add frontend helpers**

In `app.js`, add:

```javascript
function blockHorizonLabel(value) {
  return ({
    short: "단기",
    mid: "중기",
    long: "장기",
    core_etf: "ETF/Core",
    cash: "현금",
  })[value] || "단기";
}
```

Add allocation rendering:

```javascript
function renderHorizonAllocation(payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  return `
    <section class="horizon-allocation-board">
      ${items.map((row) => `
        <article class="horizon-allocation-card ${escapeHTML(row.horizon || "short")}">
          <span>${escapeHTML(blockHorizonLabel(row.horizon))}</span>
          <strong>${escapeHTML(formatPercent(row.current_weight || 0))}</strong>
          <small>목표 ${escapeHTML(formatPercent(row.target_weight || 0))}</small>
          <div class="horizon-bar"><i style="width:${escapeHTML(String(Math.min(100, Math.max(0, (row.current_weight || 0) * 100))))}%"></i></div>
        </article>
      `).join("")}
    </section>
  `;
}
```

- [ ] **Step 2: Add CSS tokens**

In `style.css`, add:

```css
:root {
  --horizon-short: #f0b35f;
  --horizon-mid: #5ee0c2;
  --horizon-long: #9ab8c8;
  --horizon-etf: #d8b56d;
  --horizon-cash: #9aaea8;
}
```

Add `.horizon-allocation-board`, `.horizon-allocation-card`, `.block-card.short|mid|long|core_etf`.

- [ ] **Step 3: Verify static JS**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no syntax errors.

---

### Task 6: Telegram Summary for Horizons

**Files:**
- Modify: `src/tradecraft/services/telegram_cli.py`
- Test: `tests/test_telegram_cli.py`

- [ ] **Step 1: Write failing test**

```python
def test_memory_status_text_shows_horizon_allocation() -> None:
    cli = TelegramCLI()
    text = cli.memory_status_text(
        {
            "status": "ok",
            "horizon_allocation": {
                "items": [
                    {"horizon": "cash", "current_weight": 0.30, "target_weight": 0.30},
                    {"horizon": "short", "current_weight": 0.10, "target_weight": 0.15},
                    {"horizon": "core_etf", "current_weight": 0.20, "target_weight": 0.10},
                ]
            },
            "active_policies": [],
        }
    )

    assert "현금 30%" in text
    assert "단기 10%" in text
    assert "ETF/Core 20%" in text
```

- [ ] **Step 2: Run test**

Run:

```bash
pytest tests/test_telegram_cli.py::test_memory_status_text_shows_horizon_allocation -q
```

Expected: FAIL.

- [ ] **Step 3: Implement compact horizon text**

In `telegram_cli.py`, add helper:

```python
def _horizon_label(value: Any) -> str:
    return {
        "cash": "현금",
        "short": "단기",
        "mid": "중기",
        "long": "장기",
        "core_etf": "ETF/Core",
    }.get(str(value or ""), str(value or "-"))
```

Use it in `memory_status_text()` when `horizon_allocation.items` exists.

- [ ] **Step 4: Run test**

Run:

```bash
pytest tests/test_telegram_cli.py::test_memory_status_text_shows_horizon_allocation -q
```

Expected: PASS.

---

### Task 7: Final Verification

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_kis_block_trader.py tests/test_investment_memory.py tests/test_telegram_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run smoke tests**

```bash
pytest tests/test_api_smoke.py tests/test_kis_trader_api.py -q
```

Expected: PASS.

- [ ] **Step 3: Static frontend check**

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no syntax errors.

- [ ] **Step 4: Diff hygiene**

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 5: Runtime restart**

```bash
tmux kill-session -t hermes-control 2>/dev/null || true
tmux kill-session -t hermes-kis-block-trader 2>/dev/null || true
tmux kill-session -t hermes-investment-memory 2>/dev/null || true
tmux new-session -d -s hermes-control 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m uvicorn tradecraft.main:app --host 127.0.0.1 --port 18080 2>&1 | tee -a .runtime/tradecraft-control-18080.log'
tmux new-session -d -s hermes-kis-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.kis_block_trader_runner 2>&1 | tee -a .runtime/kis-block-trader-runner.log'
tmux new-session -d -s hermes-investment-memory 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.investment_memory_runner 2>&1 | tee -a .runtime/investment-memory-runner.log'
```

- [ ] **Step 6: API readiness**

```bash
curl -fsS http://127.0.0.1:18080/api/health
```

Expected: `status` is `ok`, block trader alive, memory runner alive.

---

## Self-Review

- Spec coverage: Covers horizon color, short/mid/long/core ETF/cash, ETF investing, 쥬-managed balance, and 30-minute full-horizon regular-market review with differentiated action authority.
- Placeholder scan: No TBD/TODO placeholders.
- Type consistency: Uses `horizon`, `block_color`, `horizon_allocation`, `core_etf`, and `allocation_reason` consistently.
- Scope: This is one cohesive feature. It does not change brokerage adapters or order API semantics beyond horizon-aware exit behavior.
