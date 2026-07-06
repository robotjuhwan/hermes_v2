# KIS Aggressive Opportunity + Wiki Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make KIS Jue actively pursue profit opportunities by surfacing pre-surge candidates, explaining no-action streaks, exposing the latest decision input, and writing research/decision evidence into the LLM wiki loop.

**Architecture:** Extend the existing KIS decision packet instead of adding a parallel trader. Add a compact aggressive opportunity packet produced from quotes, daily discovery, research spine, strategy, fundamentals, and market pulse, then pass it through the manager prompt, persisted run payload, ops/UI APIs, and wiki context.

**Tech Stack:** Python 3.10, FastAPI, SQLite runtime DBs, existing KIS block trader services, Jue Wiki markdown/SQLite, pytest.

---

### Task 1: Opportunity Packet

**Files:**
- Create: `src/tradecraft/services/kis_aggressive_opportunity.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_aggressive_opportunity.py`

- [ ] **Step 1: Add tests for limit-up/pre-surge scoring**

```python
def test_aggressive_packet_prioritizes_limit_up_proximity_and_pre_surge():
    packet = build_aggressive_opportunity_packet(
        quotes=[{"symbol": "123450", "name": "테스트", "price": 12900, "raw": {"stck_mxpr": "13000", "acml_tr_pbmn": "5000000000", "prdy_ctrt": "18.2"}}],
        daily_discovery={"pre_surge_candidates": [{"symbol": "123450", "name": "테스트", "pre_surge": {"score": 82, "reasons": ["저점권", "거래대금"]}}]},
        research_spine={},
        strategy={},
        fundamentals_status={},
        market_pulse={},
        limit=10,
    )
    assert packet["status"] == "ok"
    assert packet["candidates"][0]["symbol"] == "123450"
    assert "limit_up_proximity" in packet["candidates"][0]["signals"]
```

- [ ] **Step 2: Implement compact packet builder**

Create a pure function that merges source rows by symbol, computes an aggressive score from limit-up proximity, positive change, trading value, pre-surge score, report/research presence, valuation support, and market-pulse regime, then returns only the top rows with audit-ready reasons.

- [ ] **Step 3: Inject packet into KIS manager prompt**

Add `aggressive_opportunities` to the KIS manager prompt and to `decision_inputs`. Ensure prompt-budget compaction preserves it with the same priority as quotes and daily discovery.

### Task 2: No-Action Streak Visibility

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/runtime/kis_block_trader_runner.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Persist no-action streak summary**

After every manager run, compute consecutive recent manager runs where applied action counts are all zero. Store `no_action_streak`, `no_action_reason`, and top opportunity symbols in run metadata.

- [ ] **Step 2: Telegram/report hook**

When the streak reaches 3 during regular/pre-open sessions, emit a compact operator-facing note through existing notification plumbing, without blocking trading if Telegram fails.

### Task 3: Last Decision Input Exposure

**Files:**
- Modify: `src/tradecraft/api/kis_blocks.py`
- Modify: `src/tradecraft/services/kis_manager_prompt.py`
- Test: `tests/test_kis_blocks_api_router.py`

- [ ] **Step 1: Add latest input summary payload**

Expose latest manager run prompt budget, retained critical sections, daily discovery count, aggressive candidate count, quote count, and omitted sections in `/api/kis/blocks/status`.

- [ ] **Step 2: Add compact detail endpoint payload**

Ensure `/api/kis/blocks` and detail payloads can show whether Jue actually saw opportunity context.

### Task 4: Exploration Budget

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/kis_manager_prompt.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Add explicit exploration budget prompt section**

Summarize cash, live authority state, existing risk, and the current small-probe allowance so Jue can propose waiting probes even under reduced authority.

- [ ] **Step 2: Keep safety gates authoritative**

Allow only structured waiting-entry probes when reduced authority is active; never bypass kill switch, no-cash, duplicate order, or budget-zero gates.

### Task 5: Wiki Accumulation

**Files:**
- Modify: `src/tradecraft/services/jue_wiki.py`
- Modify: `src/tradecraft/runtime/jue_wiki_runner.py`
- Test: `tests/test_jue_wiki.py`

- [ ] **Step 1: Add opportunity/no-action source pages**

Write KIS opportunity radar pages and no-action reflection pages from manager runs into `kis/playbooks`, `kis/lessons`, or `kis/symbols`.

- [ ] **Step 2: Select pages back into future prompts**

Ensure the wiki selector can retrieve those pages by symbols, page types, and KIS scope.

### Task 6: Verification

**Files:**
- Test only.

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_kis_aggressive_opportunity.py tests/test_kis_block_trader.py tests/test_kis_block_trader_runner.py tests/test_discovery_api_router.py tests/test_daily_discovery_api.py -q
```

- [ ] **Step 2: Run static checks**

Run:

```bash
python3 -m py_compile src/tradecraft/services/kis_aggressive_opportunity.py src/tradecraft/services/kis_block_trader.py src/tradecraft/services/kis_manager_prompt.py src/tradecraft/runtime/kis_block_trader_runner.py
git diff --check
```

- [ ] **Step 3: Restart and verify readiness**

Use `/api/ops/restart` for `control`, `kis_block_trader`, `market_judge`, `investment_memory`, `jue_wiki`, and `watchdog`; then confirm `/api/ops/readiness?compact=true` is green.
