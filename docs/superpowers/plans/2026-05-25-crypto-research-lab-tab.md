# Crypto Research Lab Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Binance crypto research, alpha, quant, and pattern panels into a dedicated top-level Crypto Research Lab tab while keeping the Crypto Block tab focused on active blocks and block history.

**Architecture:** Reuse the existing static frontend state and API loaders. Add `crypto_research` as a helper page, move existing research panels out of `renderBinanceTraderTab()`, and render them from a new `renderCryptoResearchLabTab()` function. Keep all existing backend APIs unchanged.

**Tech Stack:** Static HTML, plain JavaScript, CSS variables, pytest source/static tests, Node `--check`.

---

### Task 1: Add Crypto Research Lab As A Major Tab

**Files:**
- Modify: `src/tradecraft/web/static/index.html`
- Modify: `src/tradecraft/web/static/app.js`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add failing static assertions**

Update `tests/test_static_ui.py` so `test_helper_inner_tabs_removed_from_static_html` expects:

```python
        "crypto_research",
```

in the large `data-nav-helper-tab` list. Add a new test:

```python
def test_crypto_research_lab_is_top_level_page() -> None:
    html = _html()
    js = _js()

    assert 'data-nav-helper-tab="crypto_research"' in html
    assert "crypto_research" in js
    assert "function renderCryptoResearchLabTab" in js
    assert "20260525_crypto_research_lab_v1" in html
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py::test_crypto_research_lab_is_top_level_page -q
```

Expected: FAIL because the tab and renderer do not exist yet.

- [ ] **Step 3: Add the top-level nav item**

In `src/tradecraft/web/static/index.html`, add a nav item after the Binance block button:

```html
          <button class="nav-item" type="button" data-nav-helper-tab="crypto_research">
            <span>Crypto Lab</span>
            <strong>크립토 리서치</strong>
          </button>
```

- [ ] **Step 4: Register the helper tab**

In `src/tradecraft/web/static/app.js`, add `"crypto_research"` to `HELPER_TABS`.

- [ ] **Step 5: Bump cache busting**

In `index.html`, change both static asset versions to:

```text
20260525_crypto_research_lab_v1
```

---

### Task 2: Move Research Panels Out Of Crypto Block View

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add source-level separation test**

Add this test:

```python
def test_binance_block_tab_does_not_embed_research_lab_panels() -> None:
    js = _js()
    start = js.index("function renderBinanceTraderTab")
    end = js.index("function renderBinanceQuantBoard", start)
    body = js[start:end]

    assert "renderCryptoResearchPanel()" not in body
    assert "renderCryptoAlphaPanel()" not in body
    assert "renderBinanceQuantBoard()" not in body
    assert "renderBinancePatternBoard()" not in body
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py::test_binance_block_tab_does_not_embed_research_lab_panels -q
```

Expected: FAIL because the Binance tab currently embeds those panels.

- [ ] **Step 3: Add `renderCryptoResearchLabTab()`**

In `app.js`, add:

```javascript
function renderCryptoResearchLabTab() {
  return `
    <div class="crypto-research-lab-shell">
      <section class="block-trader-hero crypto-research-lab-hero">
        <div>
          <span class="section-kicker">Crypto Research Lab</span>
          <h3>크립토 리서치 랩</h3>
          <p>바이낸스 쥬가 보는 시장 리서치, 알파 이벤트, 정량 신호, 패턴 검증을 블록 화면과 분리해서 봅니다.</p>
        </div>
        <div class="strategy-intel-actions">
          <button class="btn ghost" type="button" data-binance-action="refresh">퀀트·패턴 갱신</button>
          <button class="btn" type="button" data-crypto-research-action="refresh">리서치 갱신</button>
          <button class="btn warm" type="button" data-crypto-research-action="run" ${state.cryptoResearch.running ? "disabled" : ""}>
            ${state.cryptoResearch.running ? "Spark 리서치 중..." : "Spark 리서치"}
          </button>
        </div>
      </section>
      ${renderCryptoResearchPanel()}
      ${renderCryptoAlphaPanel()}
      ${renderBinanceQuantBoard()}
      ${renderBinancePatternBoard()}
    </div>
  `;
}
```

- [ ] **Step 4: Remove research calls from `renderBinanceTraderTab()`**

Remove these calls from the Binance block page:

```javascript
      ${renderBinanceQuantBoard()}
      ${renderBinancePatternBoard()}
      ${renderCryptoResearchPanel()}
      ${renderCryptoAlphaPanel()}
```

- [ ] **Step 5: Add helper routing**

In `renderHelperAgent()`, add:

```javascript
  } else if (state.activeHelperTab === "crypto_research") {
    contentHtml = renderCryptoResearchLabTab();
    updatedAt = pickUpdatedAt(state.cryptoResearch.status) || pickUpdatedAt(state.cryptoAlpha.status) || updatedAt;
```

Add to `titleMap`:

```javascript
    crypto_research: "크립토 리서치",
```

---

### Task 3: Load Crypto Research Data Only When Needed

**Files:**
- Modify: `src/tradecraft/web/static/app.js`

- [ ] **Step 1: Update helper data loading**

In `ensureHelperTabData()`, move the crypto research and alpha loaders from the `binance_trader` condition to `crypto_research`:

```javascript
  if (tab === "crypto_research" && !state.binanceTrader.status && !state.binanceTrader.loading) {
    loadBinanceBlocks(false, { includeContext: true });
  }
  if (tab === "crypto_research" && !state.cryptoResearch.context && !state.cryptoResearch.loading) {
    loadCryptoResearch(false);
  }
  if (tab === "crypto_research" && !state.cryptoAlpha.context && !state.cryptoAlpha.loading) {
    loadCryptoAlpha(false);
  }
```

Leave `binance_trader` responsible for `loadBinanceBlocks(false)`.

- [ ] **Step 2: Keep action handlers reusable**

No click handler changes are needed because `data-binance-action`, `data-crypto-research-action`, and `data-crypto-alpha-action` are already delegated from `helperContent`.

---

### Task 4: Add Lab Styling And Verification

**Files:**
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add light wrapper styles**

Add:

```css
.crypto-research-lab-shell {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.crypto-research-lab-hero {
  border-color: color-mix(in srgb, var(--source-blue) 30%, var(--line));
}
```

- [ ] **Step 2: Run static and JS checks**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
git diff --check -- src/tradecraft/web/static/index.html src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css tests/test_static_ui.py
```

Expected: PASS and no JS syntax output.

---

## Self-Review

- Spec coverage: The plan creates a dedicated crypto research top-level tab, keeps block view block-first, and preserves existing research/alpha/quant/pattern panels.
- Placeholder scan: No placeholders or deferred decisions remain.
- Type consistency: The new helper page id is consistently `crypto_research`.
