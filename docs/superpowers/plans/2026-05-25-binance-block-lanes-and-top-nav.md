# Binance Block Lanes And Top Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Binance Jue blocks as legible as KIS blocks by adding horizon/lane concepts, block history, and a simplified top-level navigation that survives refresh.

**Architecture:** Keep the backend block ledger compatible by storing Binance horizon/lane in block `metadata_json`, not by adding schema columns. Extend Binance manager prompts, snapshots, and UI renderers so active blocks are grouped into short/mid/long/futures lanes and closed/error blocks are browsable as history. Remove the duplicated inner helper tabs and make the left navigation the only major tab surface.

**Tech Stack:** Python 3.10, FastAPI, SQLite, static JavaScript/CSS, pytest, Node `--check`.

---

## File Structure

- Modify `src/tradecraft/services/binance_block_trader.py`
  - Add Binance horizon normalization helpers.
  - Preserve `horizon`, `block_color`, and `lane` in block metadata.
  - Add `block_history`, `lane_allocation`, and lane-enriched block payloads to snapshots.
  - Update BlockManager prompt schema/policy so Jue chooses `short|mid|long|futures`.
- Modify `tests/test_binance_block_trader.py`
  - Cover metadata storage, prompt schema, lane grouping, and history.
- Modify `src/tradecraft/web/static/index.html`
  - Remove inner `helperTabs` buttons.
  - Add left-nav buttons for the remaining large pages.
  - Bump cache-busting version.
- Modify `src/tradecraft/web/static/app.js`
  - Replace helper-subtab assumptions with top-level helper page routing.
  - Preserve the current major page/tab across refresh.
  - Add Binance lane board and history UI.
- Modify `src/tradecraft/web/static/style.css`
  - Remove/retire helper-tab stack layout pressure.
  - Add lane board, history filter, and history timeline styles.
- Modify `tests/test_static_ui.py` or create it if missing
  - Assert duplicated helper tab stack no longer exists.
  - Assert large nav entries exist.
  - Assert cache-busting version changed.
- Modify `tests/test_binance_trader_api.py`
  - Assert compact Binance payload includes lane/history summaries.

---

### Task 1: Add Binance Horizon/Lane Metadata

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write the failing test for metadata preservation**

Add this test near the existing Binance block creation tests:

```python
def test_binance_block_horizon_is_stored_and_exposed(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.01,
            "entry_price": 100.0,
            "target_price": 120.0,
            "stop_price": 92.0,
            "status": "proposed",
            "horizon": "mid_term",
            "thesis": "mid swing block",
        }
    )

    assert block["horizon"] == "mid"
    assert block["lane"] == "mid"
    assert block["metadata"]["horizon"] == "mid"
    assert block["metadata"]["block_color"] == "mid"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py::test_binance_block_horizon_is_stored_and_exposed -q
```

Expected: FAIL because `block["horizon"]` and `block["lane"]` are missing.

- [ ] **Step 3: Implement horizon normalization**

In `src/tradecraft/services/binance_block_trader.py`, add constants and helpers near the existing block constants:

```python
BINANCE_BLOCK_HORIZONS = {"short", "mid", "long", "futures"}
BINANCE_HORIZON_ALIASES = {
    "short_term": "short",
    "shortterm": "short",
    "scalp": "short",
    "intraday": "short",
    "day": "short",
    "mid_term": "mid",
    "midterm": "mid",
    "swing": "mid",
    "medium": "mid",
    "long_term": "long",
    "longterm": "long",
    "position": "long",
    "core": "long",
    "future": "futures",
    "futures": "futures",
    "perp": "futures",
    "perpetual": "futures",
}
BINANCE_HORIZON_COLORS = {
    "short": "short",
    "mid": "mid",
    "long": "long",
    "futures": "futures",
}
```

Add helper functions near `_normalize_position_side`:

```python
def _normalize_binance_horizon(value: Any, *, market: str = "spot") -> str:
    if market == "futures":
        return "futures"
    raw = str(value or "short").strip().lower()
    compact = re.sub(r"[\s/_-]+", "_", raw)
    squashed = re.sub(r"[\s/_-]+", "", raw)
    return (
        BINANCE_HORIZON_ALIASES.get(raw)
        or BINANCE_HORIZON_ALIASES.get(compact)
        or BINANCE_HORIZON_ALIASES.get(squashed)
        or (raw if raw in BINANCE_BLOCK_HORIZONS else "short")
    )


def _binance_block_lane(*, market: str, horizon: str) -> str:
    if market == "futures":
        return "futures"
    return horizon if horizon in {"short", "mid", "long"} else "short"
```

- [ ] **Step 4: Wire metadata into normalization and row rendering**

In `_normalize_block_payload`, after `normalized["market"]` and `normalized["side"]` are set, add:

```python
        horizon = _normalize_binance_horizon(
            payload.get("horizon") or metadata.get("horizon"),
            market=normalized["market"],
        )
        metadata["horizon"] = horizon
        metadata["block_color"] = BINANCE_HORIZON_COLORS.get(horizon, horizon)
        metadata["lane"] = _binance_block_lane(market=normalized["market"], horizon=horizon)
```

In `_row_to_block`, after loading metadata, return horizon fields:

```python
        metadata = _json_loads(row["metadata_json"], {})
        if not isinstance(metadata, dict):
            metadata = {}
        market = _normalize_market(row["market"])
        horizon = _normalize_binance_horizon(metadata.get("horizon"), market=market)
        lane = _binance_block_lane(market=market, horizon=horizon)
```

Then include in the returned dict:

```python
            "metadata": metadata,
            "horizon": horizon,
            "lane": lane,
            "block_color": metadata.get("block_color") or BINANCE_HORIZON_COLORS.get(horizon, horizon),
```

- [ ] **Step 5: Run the test to verify it passes**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py::test_binance_block_horizon_is_stored_and_exposed -q
```

Expected: PASS.

---

### Task 2: Add Binance Manager Horizon Policy

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write the failing prompt-schema test**

Add this test near the existing manager prompt tests:

```python
def test_binance_manager_prompt_requires_horizon_lanes(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert prompt["output_schema"]["create_blocks"][0]["horizon"] == "short|mid|long|futures"
    assert prompt["horizon_policy"]["short"].startswith("Short")
    assert "Do not close long horizon spot blocks" in prompt["horizon_policy"]["long"]
    assert prompt["horizon_action_authority"]["futures"] == "active_high_risk_trade"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py::test_binance_manager_prompt_requires_horizon_lanes -q
```

Expected: FAIL because `horizon_policy` is missing.

- [ ] **Step 3: Update BlockManager prompt**

In `run_manager_once`, add these top-level prompt entries next to `policy` and `output_schema`:

```python
            "horizon_policy": {
                "short": (
                    "Short spot blocks are active trading blocks. They may react to intraday "
                    "momentum, quant deterioration, target/stop touches, and catalyst decay."
                ),
                "mid": (
                    "Mid spot blocks are swing blocks. They should not be closed only because "
                    "of one short-term noisy candle; require thesis deterioration, risk breach, "
                    "or better allocation opportunity."
                ),
                "long": (
                    "Long spot blocks are position blocks. Do not close long horizon spot blocks "
                    "because of short-term noise; prefer update_blocks or hold_decision unless "
                    "the long thesis is invalidated."
                ),
                "futures": (
                    "Futures blocks are high-risk directional trades. Keep them separate from "
                    "spot horizons and require explicit liquidation-distance/risk reasoning."
                ),
            },
            "horizon_action_authority": {
                "short": "active_trade",
                "mid": "swing_trade",
                "long": "position_trade",
                "futures": "active_high_risk_trade",
            },
```

In `output_schema["create_blocks"][0]`, add:

```python
                        "horizon": "short|mid|long|futures",
```

- [ ] **Step 4: Ensure create actions preserve horizon**

In `_apply_manager_actions`, no extra code is needed if Task 1 normalized `horizon` from payload. Confirm that `payload = {**row, ...}` keeps `row["horizon"]`.

- [ ] **Step 5: Run prompt tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py::test_binance_manager_prompt_requires_horizon_lanes tests/test_binance_block_trader.py::test_manager_prompt_allows_same_symbol_blocks_when_thesis_differs -q
```

Expected: PASS.

---

### Task 3: Add Binance Lane Allocation And Block History To Snapshots

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing snapshot tests**

Add these tests:

```python
def test_binance_snapshot_includes_lane_allocation_and_history(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    short_block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "qty": 0.1,
            "entry_price": 100.0,
            "target_price": 110.0,
            "stop_price": 96.0,
            "status": "open",
            "horizon": "short",
        }
    )
    closed_block = trader.create_block(
        {
            "symbol": "ETHUSDT",
            "market": "spot",
            "qty": 0.2,
            "entry_price": 200.0,
            "target_price": 240.0,
            "stop_price": 180.0,
            "status": "closed",
            "horizon": "long",
            "closed_at": "2026-05-25T00:10:00+00:00",
        }
    )

    snapshot = trader.snapshot_sync()

    assert snapshot["lane_allocation"]["items"][0]["lane"] in {"short", "cash"}
    assert any(row["block_id"] == closed_block["block_id"] for row in snapshot["block_history"])
    assert all(row["status"] in {"closed", "error"} for row in snapshot["block_history"])
    assert trader.get_block(short_block["block_id"])["lane"] == "short"  # type: ignore[index]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py::test_binance_snapshot_includes_lane_allocation_and_history -q
```

Expected: FAIL because `lane_allocation` and `block_history` are missing.

- [ ] **Step 3: Add lane summary helpers**

In `BinanceBlockTrader`, add methods near `_enrich_blocks_with_latest_quotes`:

```python
    def _lane_allocation_summary(self, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        values = {"short": 0.0, "mid": 0.0, "long": 0.0, "futures": 0.0}
        counts = {"short": 0, "mid": 0, "long": 0, "futures": 0}
        for block in blocks:
            if str(block.get("status") or "") not in VISIBLE_BLOCK_STATUSES:
                continue
            lane = str(block.get("lane") or "short")
            if lane not in values:
                lane = "short"
            entry = _safe_float(block.get("entry_price"))
            qty = _safe_float(block.get("qty_open") or block.get("qty_initial"))
            values[lane] += max(entry * qty, 0.0)
            counts[lane] += 1
        total = sum(values.values())
        return {
            "items": [
                {
                    "lane": lane,
                    "value_usdt": values[lane],
                    "weight_pct": (values[lane] / total * 100.0) if total > 0 else 0.0,
                    "block_count": counts[lane],
                }
                for lane in ("short", "mid", "long", "futures")
            ],
            "total_value_usdt": total,
        }

    @staticmethod
    def _block_history_rows(blocks: list[dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
        rows = [
            block
            for block in blocks
            if str(block.get("status") or "") in {"closed", "error"}
        ]
        rows.sort(
            key=lambda row: str(row.get("closed_at") or row.get("updated_at") or row.get("created_at") or ""),
            reverse=True,
        )
        return rows[: max(int(limit), 1)]
```

- [ ] **Step 4: Add fields to snapshots**

In `snapshot_sync`, compute enriched blocks once and include history/allocation:

```python
        enriched_blocks = self._enrich_blocks_with_latest_quotes(blocks)
        enriched_active = self._enrich_blocks_with_latest_quotes(active_blocks)
        return {
            **self.status(),
            "blocks": enriched_blocks,
            "active_blocks": enriched_active,
            "block_history": self._block_history_rows(enriched_blocks),
            "lane_allocation": self._lane_allocation_summary(enriched_active),
            ...
        }
```

In `snapshot_compact`, add:

```python
        all_blocks = self.repository.list_blocks(include_closed=True)
        enriched_active = self._enrich_blocks_with_latest_quotes(active_blocks)
        enriched_all = self._enrich_blocks_with_latest_quotes(all_blocks)
        return {
            ...
            "active_blocks": enriched_active,
            "block_history": self._block_history_rows(enriched_all, limit=30),
            "lane_allocation": self._lane_allocation_summary(enriched_active),
            ...
        }
```

- [ ] **Step 5: Run snapshot test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py::test_binance_snapshot_includes_lane_allocation_and_history -q
```

Expected: PASS.

---

### Task 4: Compact Binance API Includes History And Lane Summaries

**Files:**
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_binance_trader_api.py`

- [ ] **Step 1: Write failing compact API test**

Extend `test_binance_blocks_status_compact_uses_compact_snapshot` so the fake snapshot includes:

```python
            "block_history": [{"block_id": "closed-1", "symbol": "BTCUSDT", "status": "closed"}],
            "lane_allocation": {"items": [{"lane": "short", "block_count": 1}]},
```

Add assertions:

```python
    assert payload["block_history"][0]["block_id"] == "closed-1"
    assert payload["lane_allocation"]["items"][0]["lane"] == "short"
```

- [ ] **Step 2: Run the test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_trader_api.py::test_binance_blocks_status_compact_uses_compact_snapshot -q
```

Expected: PASS if `_compact_binance_blocks_payload` preserves new fields. If it fails, update the compact helper.

- [ ] **Step 3: If needed, preserve new fields in compact helper**

In `_compact_binance_blocks_payload`, do not remove `block_history` or `lane_allocation`. The existing comprehension only removes `blocks` and `manager_runs`, so no code change should be required.

---

### Task 5: Simplify Navigation To Large Tabs Only

**Files:**
- Modify: `src/tradecraft/web/static/index.html`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Write failing static UI tests**

Create `tests/test_static_ui.py` if it does not exist:

```python
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_helper_inner_tabs_removed_from_static_html() -> None:
    html = (ROOT / "src/tradecraft/web/static/index.html").read_text()

    assert 'id="helperTabs"' not in html
    assert 'data-helper-tab="ask"' not in html
    assert 'data-nav-helper-tab="binance_trader"' in html
    assert 'data-nav-helper-tab="kis_trader"' in html


def test_static_cache_buster_mentions_top_nav_version() -> None:
    html = (ROOT / "src/tradecraft/web/static/index.html").read_text()

    assert "20260525_binance_lanes_nav_v1" in html
```

- [ ] **Step 2: Run static UI tests to verify failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py -q
```

Expected: FAIL because `helperTabs` still exists and cache buster has the older value.

- [ ] **Step 3: Remove inner helper tabs from HTML**

In `src/tradecraft/web/static/index.html`, remove this entire block:

```html
            <div id="helperTabs" class="helper-tab-stack">
              ...
            </div>
```

Keep the helper rail identity and back button.

- [ ] **Step 4: Expand left nav to all large pages**

In the main `.nav-stack`, replace the generic AI 리서치룸 helper button with explicit top-level helper buttons:

```html
          <button class="nav-item" type="button" data-nav-helper-tab="ask">
            <span>Ask</span>
            <strong>AI 질문</strong>
          </button>
          <button class="nav-item" type="button" data-nav-helper-tab="strategy_intel">
            <span>Research</span>
            <strong>전략·리서치</strong>
          </button>
          <button class="nav-item" type="button" data-nav-helper-tab="memory">
            <span>Memory</span>
            <strong>쥬 메모리</strong>
          </button>
```

Keep existing buttons for `kis_trader`, `binance_trader`, `runtime`, and `settings`. Remove the old `id="helperNavBtn"` button or convert it to `data-nav-helper-tab="ask"`; do not keep two separate routes to the same conceptual page.

- [ ] **Step 5: Update JS bindings for missing helperTabs/helperNavBtn**

In `init()`, replace:

```javascript
  qs("helperNavBtn").addEventListener("click", () => {
    openHelperPage("ask");
    ensureHelperTabData();
  });
```

with guarded logic:

```javascript
  const helperNavBtn = qs("helperNavBtn");
  if (helperNavBtn) {
    helperNavBtn.addEventListener("click", () => {
      openHelperPage(state.activeHelperTab || "ask");
      ensureHelperTabData();
    });
  }
```

In `renderHelperAgent()`, remove hard dependency on `helperTabs`:

```javascript
  const tabsRoot = qs("helperTabs");
  const contentRoot = qs("helperContent");
  const updatedRoot = qs("helperUpdatedAt");
  const scoreRoot = qs("helperScorePill");
  if (!contentRoot || !updatedRoot || !scoreRoot) return;
```

Guard the tab-button active update:

```javascript
  if (tabsRoot) {
    tabsRoot.querySelectorAll("[data-helper-tab]").forEach((button) => {
      const active = button.dataset.helperTab === state.activeHelperTab;
      button.classList.toggle("active", active);
    });
  }
```

In `init()`, guard the `helperTabs` click listener:

```javascript
  const helperTabs = qs("helperTabs");
  if (helperTabs) {
    helperTabs.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const button = target ? target.closest("[data-helper-tab]") : null;
      if (!button) return;
      state.activeHelperTab = String(button.dataset.helperTab || "ask");
      state.helperDetailModal = null;
      saveUiState();
      renderHelperAgent();
      ensureHelperTabData();
      syncActiveBlockRefresh();
    });
  }
```

- [ ] **Step 6: Update active nav rendering**

In `renderPageMode()`, make `data-nav-helper-tab` buttons the source of truth. Keep the main dashboard active when `state.activePage === "main"`, and mark a nav helper button active only when its tab equals `state.activeHelperTab`.

Use this behavior:

```javascript
  document.querySelectorAll("[data-nav-helper-tab]").forEach((button) => {
    const targetTab = String(button.dataset.navHelperTab || "");
    button.classList.toggle("active", isHelper && targetTab === state.activeHelperTab);
  });
```

Remove grouped aliases that make one large nav item active for multiple hidden subtabs.

- [ ] **Step 7: Retire helper tab CSS pressure**

In `style.css`, keep `.helper-tab-stack` styles harmless for backward compatibility or remove the block. Add:

```css
.helper-rail {
  gap: 16px;
}

.helper-identity {
  min-width: 0;
}
```

- [ ] **Step 8: Run static and JS checks**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
```

Expected: PASS and no JS syntax output.

---

### Task 6: Preserve Current Large Tab Across Refresh

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Write failing source-level routing test**

Append to `tests/test_static_ui.py`:

```python
def test_frontend_uses_hash_tab_before_default_ask() -> None:
    js = (ROOT / "src/tradecraft/web/static/app.js").read_text()

    assert "function resolveInitialHelperTab" in js
    assert "window.location.hash" in js
    assert 'openHelperPage("ask")' not in js
```

This source-level test intentionally prevents reintroducing hard-coded AI-question routing.

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py::test_frontend_uses_hash_tab_before_default_ask -q
```

Expected: FAIL because `resolveInitialHelperTab` does not exist and `openHelperPage("ask")` exists.

- [ ] **Step 3: Add initial tab resolver**

In `app.js`, near restore helpers, add:

```javascript
function resolveInitialHelperTab(fallback = "ask") {
  const hash = String(window.location.hash || "").replace(/^#/, "");
  if (hash.startsWith("helper/")) {
    const tab = hash.split("/")[1] || "";
    if (HELPER_TABS.has(tab)) return tab;
  }
  const stored = String(state.activeHelperTab || fallback || "ask");
  return HELPER_TABS.has(stored) ? stored : "ask";
}
```

- [ ] **Step 4: Stop hard-coding AI 질문 on generic helper opens**

In `init()`, change any remaining direct AI-question opens used for generic helper navigation:

```javascript
openHelperPage(resolveInitialHelperTab());
```

For quick-question actions that intentionally route to ask, keep a helper function:

```javascript
function openAskPageWithQuery(query = "") {
  if (query) state.helperAsk.query = query;
  openHelperPage("ask");
  renderHelperAgent();
}
```

Replace `openHelperPage("ask")` quick-question cases with `openAskPageWithQuery(...)` so the static test can be adjusted to allow intentional ask routing:

```python
assert 'openHelperPage("ask")' not in js
assert "function openAskPageWithQuery" in js
```

- [ ] **Step 5: Ensure hash is written on every top-level navigation**

Confirm `saveUiState()` writes `#helper/${state.activeHelperTab}`. If needed, keep this existing behavior:

```javascript
  const nextHash = state.activePage === "helper" && HELPER_TABS.has(state.activeHelperTab)
    ? `#helper/${state.activeHelperTab}`
    : "#main";
```

- [ ] **Step 6: Run checks**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
```

Expected: PASS.

---

### Task 7: Build Binance Lane Board UI

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`

- [ ] **Step 1: Add lane helpers**

In `app.js`, before `renderBinanceTraderTab`, add:

```javascript
const BINANCE_LANES = [
  { id: "short", label: "단기 현물", description: "빠른 모멘텀·촉매 대응" },
  { id: "mid", label: "중기 현물", description: "스윙 thesis 관리" },
  { id: "long", label: "장기 현물", description: "포지션 thesis 관리" },
  { id: "futures", label: "선물", description: "고위험 방향성 블록" },
];

function binanceBlockLane(block) {
  const market = String(block.market || block.venue || "spot").toLowerCase();
  if (market === "futures") return "futures";
  const horizon = String(block.horizon || block.metadata?.horizon || "short").toLowerCase();
  return ["short", "mid", "long"].includes(horizon) ? horizon : "short";
}

function groupBinanceBlocksByLane(blocks) {
  return BINANCE_LANES.reduce((acc, lane) => {
    acc[lane.id] = blocks.filter((block) => binanceBlockLane(block) === lane.id);
    return acc;
  }, {});
}
```

- [ ] **Step 2: Render lane columns**

In `renderBinanceTraderTab`, replace the single `cards` grid with:

```javascript
  const blocksByLane = groupBinanceBlocksByLane(blocks);
  const laneBoard = BINANCE_LANES.map((lane) => {
    const laneBlocks = blocksByLane[lane.id] || [];
    return `
      <section class="binance-lane-column ${escapeHTML(lane.id)}">
        <div class="binance-lane-head">
          <div>
            <h4>${escapeHTML(lane.label)}</h4>
            <p>${escapeHTML(lane.description)}</p>
          </div>
          <span>${escapeHTML(fmtNum(laneBlocks.length, 0))}</span>
        </div>
        <div class="binance-lane-list">
          ${laneBlocks.map(renderBinanceBlockCard).join("") || '<div class="notice compact">블록 없음</div>'}
        </div>
      </section>
    `;
  }).join("");
```

Extract the existing card markup into:

```javascript
function renderBinanceBlockCard(block) {
  const status = String(block.status || "-");
  const tone = status === "open" ? "ok" : (status === "error" ? "bad" : "warn");
  const venue = String(block.venue || block.market || "spot").toLowerCase();
  const lane = binanceBlockLane(block);
  return `
    <article class="binance-block-card ${escapeHTML(venue)} ${escapeHTML(lane)}">
      ...
    </article>
  `;
}
```

Then render:

```javascript
      <div class="binance-lane-board">${laneBoard}</div>
```

- [ ] **Step 3: Add lane CSS**

In `style.css`, add:

```css
.binance-lane-board {
  display: grid;
  grid-template-columns: repeat(4, minmax(220px, 1fr));
  gap: 12px;
  align-items: start;
}

.binance-lane-column {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-soft);
  padding: 10px;
  display: grid;
  gap: 10px;
}

.binance-lane-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: start;
}

.binance-lane-head h4,
.binance-lane-head p {
  margin: 0;
}

.binance-lane-head p {
  color: var(--muted);
  font-size: 12px;
}

.binance-lane-list {
  display: grid;
  gap: 10px;
}

@media (max-width: 1200px) {
  .binance-lane-board {
    grid-template-columns: repeat(2, minmax(220px, 1fr));
  }
}

@media (max-width: 760px) {
  .binance-lane-board {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Run JS check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no output.

---

### Task 8: Build Binance Block History UI

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`

- [ ] **Step 1: Add UI state**

In the `state.binanceTrader` object, add:

```javascript
    historyDate: "",
    historyStatus: "closed_error",
    historyLane: "all",
    historyQuery: "",
```

- [ ] **Step 2: Add history helpers**

In `app.js`, add:

```javascript
function binanceHistoryDateKey(block) {
  const raw = String(block.closed_at || block.updated_at || block.created_at || "");
  return raw ? fmtKST(raw).slice(0, 10) : "";
}

function filteredBinanceHistory(payload) {
  const rows = Array.isArray(payload?.block_history)
    ? payload.block_history
    : (Array.isArray(payload?.blocks) ? payload.blocks : []).filter((block) => (
        ["closed", "error"].includes(String(block.status || ""))
      ));
  const date = String(state.binanceTrader.historyDate || "");
  const lane = String(state.binanceTrader.historyLane || "all");
  const query = String(state.binanceTrader.historyQuery || "").trim().toUpperCase();
  return rows.filter((block) => {
    if (date && binanceHistoryDateKey(block) !== date) return false;
    if (lane !== "all" && binanceBlockLane(block) !== lane) return false;
    if (query && !String(block.symbol || "").toUpperCase().includes(query)) return false;
    return true;
  });
}
```

- [ ] **Step 3: Render history section**

Add:

```javascript
function renderBinanceBlockHistory(payload) {
  const rows = filteredBinanceHistory(payload);
  return `
    <section class="memory-section binance-history-panel">
      <div class="panel-head compact">
        <h3>블록 히스토리</h3>
        <p>닫힌 블록과 오류 블록을 날짜·레인·심볼로 다시 봅니다.</p>
      </div>
      <div class="binance-history-toolbar">
        <input id="binanceHistoryDate" type="date" value="${escapeHTML(state.binanceTrader.historyDate || "")}" />
        <select id="binanceHistoryLane">
          ${["all", "short", "mid", "long", "futures"].map((lane) => (
            `<option value="${lane}" ${state.binanceTrader.historyLane === lane ? "selected" : ""}>${escapeHTML(lane)}</option>`
          )).join("")}
        </select>
        <input id="binanceHistoryQuery" type="search" value="${escapeHTML(state.binanceTrader.historyQuery || "")}" placeholder="심볼 검색" />
      </div>
      <div class="binance-history-list">
        ${rows.slice(0, 40).map((block) => `
          <article class="binance-history-row">
            <strong>${escapeHTML(block.symbol || "-")}</strong>
            <span>${escapeHTML(`${binanceBlockLane(block)} · ${block.status || "-"}`)}</span>
            <span>${escapeHTML(binanceHistoryDateKey(block) || "-")}</span>
            <span>${escapeHTML(`R ${fmtNum(block.r_multiple ?? block.performance?.r_multiple ?? 0, 2)}`)}</span>
          </article>
        `).join("") || '<div class="notice">조건에 맞는 히스토리가 없습니다.</div>'}
      </div>
    </section>
  `;
}
```

Add `${renderBinanceBlockHistory(payload)}` below the lane board.

- [ ] **Step 4: Wire history inputs**

In the `helperContent` input listener, add:

```javascript
    } else if (target.id === "binanceHistoryDate") {
      state.binanceTrader.historyDate = target.value;
      renderHelperAgent();
    } else if (target.id === "binanceHistoryQuery") {
      state.binanceTrader.historyQuery = target.value;
      renderHelperAgent();
```

In the `helperContent` change listener, create one if missing:

```javascript
  qs("helperContent").addEventListener("change", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.id === "binanceHistoryLane") {
      state.binanceTrader.historyLane = target.value;
      renderHelperAgent();
    }
  });
```

- [ ] **Step 5: Add history CSS**

Add:

```css
.binance-history-toolbar {
  display: grid;
  grid-template-columns: 160px 160px minmax(180px, 1fr);
  gap: 8px;
}

.binance-history-toolbar input,
.binance-history-toolbar select {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  padding: 9px 10px;
}

.binance-history-list {
  display: grid;
  gap: 8px;
}

.binance-history-row {
  display: grid;
  grid-template-columns: minmax(110px, 1fr) 110px 120px 80px;
  gap: 8px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 9px 10px;
  background: var(--surface-soft);
}

@media (max-width: 760px) {
  .binance-history-toolbar,
  .binance-history-row {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 6: Run JS check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no output.

---

### Task 9: Cache Bust, Restart, And Manual UI Verification

**Files:**
- Modify: `src/tradecraft/web/static/index.html`

- [ ] **Step 1: Bump cache busting**

In `index.html`, set both static asset versions to:

```html
20260525_binance_lanes_nav_v1
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py tests/test_binance_trader_api.py tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
python -m py_compile src/tradecraft/services/binance_block_trader.py src/tradecraft/main.py
git diff --check -- src/tradecraft/services/binance_block_trader.py src/tradecraft/main.py src/tradecraft/web/static/index.html src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css tests/test_binance_block_trader.py tests/test_binance_trader_api.py tests/test_static_ui.py
```

Expected: all tests pass, `node --check` and `git diff --check` produce no output.

- [ ] **Step 3: Restart HERMES control and Binance runner**

Run:

```bash
tmux kill-session -t hermes-control 2>/dev/null || true
tmux kill-session -t hermes-binance-block-trader 2>/dev/null || true
sleep 1
tmux new-session -d -s hermes-control 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-control --host 127.0.0.1 --port 18080 2>&1 | tee -a .runtime/control.log'
tmux new-session -d -s hermes-binance-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-binance-block-trader 2>&1 | tee -a .runtime/binance_block_trader.log'
```

- [ ] **Step 4: Verify API shape**

Run:

```bash
python - <<'PY'
import json, urllib.request
import tradecraft.main as main

token = str(main.settings.admin_token or '').strip()
if not token and main.settings.admin_token_list:
    token = main.settings.admin_token_list[0]
req = urllib.request.Request(
    'http://127.0.0.1:18080/api/binance/blocks/status?compact=1',
    headers={'Authorization': f'Bearer {token}'},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    payload = json.loads(resp.read().decode())
print('status', payload.get('status'))
print('active_blocks', len(payload.get('active_blocks') or []))
print('history', len(payload.get('block_history') or []))
print('lanes', [row.get('lane') for row in (payload.get('lane_allocation') or {}).get('items', [])])
PY
```

Expected:

```text
status ok
active_blocks <number>
history <number>
lanes ['short', 'mid', 'long', 'futures']
```

- [ ] **Step 5: Manual UI verification**

Open `http://127.0.0.1:18080/#helper/binance_trader` and verify:

- Left nav has no duplicated inner helper tab stack.
- Refresh keeps the Binance block page selected.
- Binance page shows lane board with `단기 현물`, `중기 현물`, `장기 현물`, `선물`.
- History section shows closed/error blocks and filters do not break layout.
- Mobile width does not create horizontal overflow.

---

### Task 10: Final Regression Sweep

**Files:**
- All touched files.

- [ ] **Step 1: Run broader relevant suite**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_kis_block_trader.py tests/test_api_smoke.py tests/test_static_ui.py -q
```

Expected: PASS.

- [ ] **Step 2: Check runtime status**

Run:

```bash
python - <<'PY'
import json, urllib.request
import tradecraft.main as main

token = str(main.settings.admin_token or '').strip()
if not token and main.settings.admin_token_list:
    token = main.settings.admin_token_list[0]
for path in ['/api/health', '/api/ops/readiness', '/api/binance/blocks/status?compact=1']:
    req = urllib.request.Request(
        f'http://127.0.0.1:18080{path}',
        headers={'Authorization': f'Bearer {token}'} if path != '/api/health' else {},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode())
    print(path, payload.get('status') or payload.get('ok') or 'ok')
PY
```

Expected: health and readiness endpoints respond, Binance status is `ok`.

- [ ] **Step 3: Handoff summary**

Report:

- Changed files.
- Verification commands and results.
- Whether HERMES processes were restarted.
- Current Binance active/history block counts from the API.

---

## Self-Review

- Spec coverage: The plan covers Binance short/mid/long/futures lanes, Binance block history, refresh/tab persistence, removal of inner helper tabs, and large-tab-only navigation.
- Placeholder scan: No `TBD`, `TODO`, or open-ended “handle edge cases” steps are present.
- Type consistency: Backend names are `horizon`, `lane`, `block_history`, and `lane_allocation`; frontend uses the same names.
- Risk note: Backend schema migration is avoided by using `metadata_json`; this keeps existing `.runtime/binance_blocks.db` compatible.
