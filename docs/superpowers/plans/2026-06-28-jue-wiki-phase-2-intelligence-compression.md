# Jue Wiki Phase 2 Intelligence And Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote Jue Wiki from a compiled archive into a budgeted, quality-gated, performance-aware decision layer for KIS Jue, Binance Jue, and market judge prompts.

**Architecture:** Keep existing source-of-truth DBs unchanged and add a selector, repair loop, playbook compiler, and performance projector around `JueWikiService`. Manager prompts should request ranked wiki context with an auditable selection trace and budget report, then attach live state and raw evidence only inside explicit caps. UI and ops should expose wiki health, page quality, prompt budget pressure, and selected-page traces.

**Tech Stack:** Python 3.10+, SQLite via stdlib `sqlite3`, FastAPI routers in `src/tradecraft/api`, existing static frontend in `src/tradecraft/web/static`, existing pytest suite, existing runtime process model, existing Jue Wiki files under `.runtime/jue_wiki`.

---

## Phase 2 Positioning

Phase 1 answered: "Can HERMES compile Jue's growing RAG, memory, and trading ledgers into scoped Markdown wiki pages?"

Phase 2 answers: "Can Jue reliably pick the most useful wiki pages for each decision, understand their quality, stay inside context budget, and prefer playbooks that have evidence of working?"

This plan intentionally does not add a new trading strategy. It improves the knowledge and context path that existing KIS, Binance, and market judge decisions use.

## Non-Goals

- Do not remove RAG, investment memory, or block ledgers.
- Do not make wiki pages the source of truth for orders, fills, balances, or PnL.
- Do not add exchange execution logic.
- Do not hide failures with synthetic summaries.
- Do not add hard strategy filters. Performance status should influence ranking and caution, not bypass user-controlled safety gates or block execution rules.

## File Structure

### Create

- `src/tradecraft/services/jue_wiki_selector.py`
  - Ranks wiki pages and builds context packs for a decision request.
- `src/tradecraft/services/jue_wiki_repair.py`
  - Converts lint findings into repair actions and safe rebuild requests.
- `src/tradecraft/services/jue_wiki_playbooks.py`
  - Compiles playbook, lesson, lane, and regime pages from reflections and validation data.
- `src/tradecraft/services/jue_wiki_performance.py`
  - Projects realized performance metrics into wiki playbook metadata.
- `tests/test_jue_wiki_selector.py`
  - Selector ranking, budget, stale exclusion, and trace tests.
- `tests/test_jue_wiki_repair.py`
  - Repair action tests for missing sources, oversized pages, stale pages, and scope leakage.
- `tests/test_jue_wiki_playbooks.py`
  - Playbook compilation tests for KIS and Binance outcomes.
- `tests/test_jue_wiki_performance.py`
  - Performance projection tests for expectancy, win rate, profit factor, drawdown, and page status.
- `tests/test_jue_wiki_phase2_api.py`
  - Search, lint findings, repair, and source reference API tests.

### Modify

- `src/tradecraft/services/jue_wiki.py`
  - Add repository helpers for page search, source lookup, selection history, and repair action persistence.
- `src/tradecraft/services/manager_prompt_budget.py`
  - Add wiki-aware budget reports and enforce the configured full prompt cap.
- `src/tradecraft/services/kis_block_trader.py`
  - Use wiki selector output in manager prompt.
- `src/tradecraft/services/binance_block_trader.py`
  - Use wiki selector output in manager prompt.
- `src/tradecraft/services/market_judgment.py`
  - Use wiki selector output in market judge payload.
- `src/tradecraft/api/wiki.py`
  - Add search, findings, repair, and source-reference endpoints.
- `src/tradecraft/runtime/jue_wiki_runner.py`
  - Run rebuild, lint, repair, playbook compile, and performance projection in one cycle.
- `src/tradecraft/api/ops_payloads.py`
  - Add wiki quality and prompt-budget readiness fields.
- `src/tradecraft/config.py`
  - Add Phase 2 env settings.
- `src/tradecraft/web/static/app.js`
  - Add wiki health, search, selected pages, source refs, lint findings, repair actions, and budget reports.
- `src/tradecraft/web/static/index.html`
  - Add wiki detail panels if existing mount points are insufficient.
- `docs/spec/08_research_memory.md`
  - Document selector, repair loop, and wiki-first context policy.
- `docs/spec/21_memory_learning_contracts.md`
  - Document playbook performance and context selection contracts.
- `docs/spec/11_api_reference.md`
  - Document new wiki endpoints.
- `docs/spec/12_config_env.md`
  - Document new Phase 2 env settings.

## New Data Model

Extend `.runtime/jue_wiki/wiki.db` with these tables:

```sql
CREATE TABLE IF NOT EXISTS wiki_selection_runs (
    run_id TEXT PRIMARY KEY,
    target_scope TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    selected_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    max_chars INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

```sql
CREATE TABLE IF NOT EXISTS wiki_selection_pages (
    run_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    penalties_json TEXT NOT NULL DEFAULT '[]',
    char_count INTEGER NOT NULL DEFAULT 0,
    included INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, page_id)
);
```

```sql
CREATE TABLE IF NOT EXISTS wiki_repair_actions (
    action_id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT ''
);
```

```sql
CREATE TABLE IF NOT EXISTS wiki_playbook_metrics (
    page_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    playbook_id TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0.0,
    expectancy REAL NOT NULL DEFAULT 0.0,
    profit_factor REAL NOT NULL DEFAULT 0.0,
    max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
    avg_holding_minutes REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'probe',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
```

## New Config

Add these settings to `AppSettings`:

```python
jue_wiki_prompt_mode: str = Field(
    default="assist",
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_PROMPT_MODE", "jue_wiki_prompt_mode"),
)
jue_wiki_selector_max_pages: int = Field(
    default=12,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_SELECTOR_MAX_PAGES", "jue_wiki_selector_max_pages"),
)
jue_wiki_selector_min_confidence: float = Field(
    default=0.15,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_SELECTOR_MIN_CONFIDENCE", "jue_wiki_selector_min_confidence"),
)
jue_wiki_exclude_lint_warnings: bool = Field(
    default=False,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_EXCLUDE_LINT_WARNINGS", "jue_wiki_exclude_lint_warnings"),
)
jue_wiki_repair_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_REPAIR_ENABLED", "jue_wiki_repair_enabled"),
)
jue_wiki_full_prompt_max_chars: int = Field(
    default=190_000,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_FULL_PROMPT_MAX_CHARS", "jue_wiki_full_prompt_max_chars"),
)
```

Mode semantics:

- `observe`: selector runs and records traces, but manager prompts keep the existing context structure.
- `assist`: manager prompts include selected wiki context and budget report, while raw memory/RAG remains available inside caps.
- `primary`: selected wiki context becomes the primary compiled knowledge section; raw memory/RAG is trimmed to explicit evidence requests.

Start with `assist`.

---

## Task 1: Add Phase 2 Config And Schema Tests

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_config.py`
- Test: `tests/test_jue_wiki.py`

- [ ] **Step 1: Write config default tests**

Add to `tests/test_config.py`:

```python
def test_jue_wiki_phase2_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_PROMPT_MODE", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_SELECTOR_MAX_PAGES", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_SELECTOR_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_EXCLUDE_LINT_WARNINGS", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_REPAIR_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_FULL_PROMPT_MAX_CHARS", raising=False)

    settings = AppSettings()

    assert settings.jue_wiki_prompt_mode == "assist"
    assert settings.jue_wiki_selector_max_pages == 12
    assert settings.jue_wiki_selector_min_confidence == 0.15
    assert settings.jue_wiki_exclude_lint_warnings is False
    assert settings.jue_wiki_repair_enabled is True
    assert settings.jue_wiki_full_prompt_max_chars == 190_000
```

- [ ] **Step 2: Run the config test and confirm failure**

Run:

```bash
pytest tests/test_config.py::test_jue_wiki_phase2_settings_have_safe_defaults -q
```

Expected: fail because the new attributes are not present.

- [ ] **Step 3: Add the settings**

Add the config fields from the "New Config" section to `src/tradecraft/config.py` next to the existing Jue Wiki settings.

- [ ] **Step 4: Add schema migration tests**

Add to `tests/test_jue_wiki.py`:

```python
def test_jue_wiki_phase2_tables_are_created(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    service.status()

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "wiki_selection_runs" in names
    assert "wiki_selection_pages" in names
    assert "wiki_repair_actions" in names
    assert "wiki_playbook_metrics" in names
```

- [ ] **Step 5: Run the schema test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki.py::test_jue_wiki_phase2_tables_are_created -q
```

Expected: fail because the new tables are missing.

- [ ] **Step 6: Create the tables**

Update `JueWikiService._init_db()` in `src/tradecraft/services/jue_wiki.py` with the SQL from the "New Data Model" section.

- [ ] **Step 7: Verify Task 1**

Run:

```bash
pytest tests/test_config.py::test_jue_wiki_phase2_settings_have_safe_defaults tests/test_jue_wiki.py::test_jue_wiki_phase2_tables_are_created -q
```

Expected: both tests pass.

---

## Task 2: Build The Wiki Selector

**Files:**
- Create: `src/tradecraft/services/jue_wiki_selector.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki_selector.py`

- [ ] **Step 1: Write selector tests**

Create `tests/test_jue_wiki_selector.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_selector import JueWikiSelectionRequest, JueWikiSelector


def _service(tmp_path: Path) -> JueWikiService:
    return JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
            context_max_chars=2_000,
            context_page_limit=8,
        )
    )


def test_selector_prefers_scope_symbol_freshness_and_sources(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.write_page(
        page_id="kis.symbol.005930",
        scope="kis",
        page_type="symbol",
        title="삼성전자",
        symbols=["005930"],
        content="# 삼성전자\n\n## Next Context Pack Summary\nKIS relevant source-backed page.",
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk1"}],
        confidence=0.8,
        freshness="fresh",
    )
    service.write_page(
        page_id="binance.symbol.BTCUSDT",
        scope="binance",
        page_type="symbol",
        title="BTCUSDT",
        symbols=["BTCUSDT"],
        content="# BTCUSDT\n\n## Next Context Pack Summary\nWrong scope page.",
        source_refs=[{"source_type": "binance_blocks", "source_id": "blk2"}],
        confidence=0.9,
        freshness="fresh",
    )

    selector = JueWikiSelector(service)
    result = selector.select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            page_types=["symbol"],
            max_chars=1_500,
            max_pages=5,
        )
    )

    assert result.status == "ok"
    assert [page.page_id for page in result.pages] == ["kis.symbol.005930"]
    assert result.budget_report["char_count"] <= 1_500
    assert result.pages[0].score > 0.0
    assert "symbol_match" in result.pages[0].reasons


def test_selector_records_trace_and_rejected_pages(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.write_page(
        page_id="kis.symbol.277810",
        scope="kis",
        page_type="symbol",
        title="레인보우로보틱스",
        symbols=["277810"],
        content="# 레인보우로보틱스\n\n" + ("large text " * 800),
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk3"}],
        confidence=0.7,
        freshness="fresh",
    )

    selector = JueWikiSelector(service)
    result = selector.select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["277810"],
            max_chars=300,
            max_pages=5,
        )
    )

    assert result.status == "ok"
    assert result.rejected_pages
    assert result.selection_run_id

    with sqlite3.connect(tmp_path / "jue_wiki" / "wiki.db") as conn:
        stored = conn.execute(
            "SELECT selected_count, rejected_count FROM wiki_selection_runs WHERE run_id = ?",
            (result.selection_run_id,),
        ).fetchone()

    assert stored == (0, 1)
```

- [ ] **Step 2: Run selector tests and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_selector.py -q
```

Expected: fail because `jue_wiki_selector.py` does not exist.

- [ ] **Step 3: Implement selector dataclasses**

Create `src/tradecraft/services/jue_wiki_selector.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class JueWikiSelectionRequest:
    target_scope: str
    symbols: list[str] = field(default_factory=list)
    page_types: list[str] = field(default_factory=list)
    lanes: list[str] = field(default_factory=list)
    regimes: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    max_chars: int = 24_000
    max_pages: int = 12
    min_confidence: float = 0.15
    exclude_lint_warnings: bool = False


@dataclass(frozen=True)
class JueWikiSelectedPage:
    page_id: str
    rank: int
    score: float
    reasons: list[str]
    penalties: list[str]
    char_count: int
    content: str
    source_refs: list[dict[str, Any]]


@dataclass(frozen=True)
class JueWikiSelectionResult:
    status: str
    selection_run_id: str
    target_scope: str
    pages: list[JueWikiSelectedPage]
    rejected_pages: list[dict[str, Any]]
    content: str
    budget_report: dict[str, Any]
```

- [ ] **Step 4: Add repository helpers to `JueWikiService`**

Add methods with these signatures:

```python
def search_pages(
    self,
    *,
    scope: str | None = None,
    symbols: list[str] | None = None,
    page_types: list[str] | None = None,
    include_content: bool = True,
) -> list[dict[str, Any]]:
    ...

def record_selection_run(
    self,
    *,
    run_id: str,
    target_scope: str,
    request: dict[str, Any],
    selected_pages: list[dict[str, Any]],
    rejected_pages: list[dict[str, Any]],
    char_count: int,
    max_chars: int,
    status: str,
    error_message: str = "",
) -> None:
    ...
```

`search_pages()` should read from `wiki_pages`, load Markdown content from each page path when `include_content=True`, and return dictionaries containing `page_id`, `scope`, `page_type`, `title`, `symbols`, `confidence`, `freshness`, `char_count`, `source_refs`, `content`, and `updated_at`.

- [ ] **Step 5: Implement scoring**

Implement `JueWikiSelector.select()` with this score formula:

```python
score = 0.0
if page["scope"] == request.target_scope:
    score += 35.0
if set(page["symbols"]) & set(request.symbols):
    score += 30.0
if page["page_type"] in request.page_types:
    score += 12.0
if page["freshness"] == "fresh":
    score += 10.0
elif page["freshness"] == "stale":
    score -= 15.0
score += min(float(page["confidence"]) * 10.0, 10.0)
score += min(len(page["source_refs"]) * 1.5, 9.0)
```

Reject a page when:

- scope does not match and no requested symbol overlaps.
- confidence is below `min_confidence`.
- content would exceed `max_chars`.
- `exclude_lint_warnings=True` and an open lint finding exists for the page.

- [ ] **Step 6: Verify Task 2**

Run:

```bash
pytest tests/test_jue_wiki_selector.py -q
```

Expected: pass.

---

## Task 3: Add Prompt Budget Reports

**Files:**
- Modify: `src/tradecraft/services/manager_prompt_budget.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/services/market_judgment.py`
- Test: `tests/test_manager_prompt_budget.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_market_judgment.py`

- [ ] **Step 1: Add budget report tests**

Add to `tests/test_manager_prompt_budget.py`:

```python
def test_budget_report_tracks_wiki_live_and_raw_sections() -> None:
    payload = {
        "account": {"cash": 1_000_000},
        "jue_wiki": {"content": "wiki" * 100, "pages": ["kis.symbol.005930"]},
        "raw_rag": {"content": "rag" * 1000},
    }

    trimmed, report = enforce_manager_prompt_budget_with_report(
        payload,
        max_chars=2_000,
        protected_keys=("account", "jue_wiki"),
    )

    assert "account" in trimmed
    assert "jue_wiki" in trimmed
    assert report["max_chars"] == 2_000
    assert report["sections"]["jue_wiki"]["protected"] is True
    assert report["total_chars"] <= 2_000
```

- [ ] **Step 2: Run budget report test and confirm failure**

Run:

```bash
pytest tests/test_manager_prompt_budget.py::test_budget_report_tracks_wiki_live_and_raw_sections -q
```

Expected: fail because `enforce_manager_prompt_budget_with_report` is missing.

- [ ] **Step 3: Implement budget report function**

Add to `src/tradecraft/services/manager_prompt_budget.py`:

```python
def enforce_manager_prompt_budget_with_report(
    payload: dict[str, Any],
    *,
    max_chars: int,
    protected_keys: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    trimmed = enforce_manager_prompt_budget(
        payload,
        max_chars=max_chars,
        protected_keys=protected_keys,
    )
    sections: dict[str, Any] = {}
    for key, value in trimmed.items():
        sections[key] = {
            "chars": len(json.dumps(value, ensure_ascii=False, default=str)),
            "protected": key in protected_keys,
        }
    total_chars = len(json.dumps(trimmed, ensure_ascii=False, default=str))
    return trimmed, {
        "max_chars": max_chars,
        "total_chars": total_chars,
        "sections": sections,
        "status": "ok" if total_chars <= max_chars else "over_budget",
    }
```

- [ ] **Step 4: Add manager prompt tests**

Add one assertion to each existing prompt test that already checks `jue_wiki`:

```python
assert run["prompt"]["jue_wiki_budget_report"]["status"] == "ok"
assert run["prompt"]["jue_wiki_budget_report"]["total_chars"] <= 190_000
```

- [ ] **Step 5: Wire budget reports into manager payloads**

In KIS, Binance, and market judge prompt assembly:

1. Attach selected wiki context under `jue_wiki`.
2. Enforce the full prompt cap.
3. Attach the returned report under `jue_wiki_budget_report`.

- [ ] **Step 6: Verify Task 3**

Run:

```bash
pytest tests/test_manager_prompt_budget.py tests/test_kis_block_trader.py::test_kis_manager_prompt_stays_under_budget_with_wiki_context tests/test_binance_block_trader.py::test_binance_manager_prompt_stays_under_budget_with_wiki_context tests/test_market_judgment.py::test_market_judge_payload_includes_jue_wiki_context -q
```

Expected: pass.

---

## Task 4: Add Lint Findings And Repair APIs

**Files:**
- Create: `src/tradecraft/services/jue_wiki_repair.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Modify: `src/tradecraft/api/wiki.py`
- Test: `tests/test_jue_wiki_repair.py`
- Test: `tests/test_jue_wiki_phase2_api.py`

- [ ] **Step 1: Write repair service tests**

Create `tests/test_jue_wiki_repair.py`:

```python
from __future__ import annotations

from pathlib import Path

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_repair import JueWikiRepairService


def test_repair_service_schedules_rebuild_for_stale_page(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service.write_page(
        page_id="kis.symbol.005930",
        scope="kis",
        page_type="symbol",
        title="삼성전자",
        symbols=["005930"],
        content="# 삼성전자\n\n## Next Context Pack Summary\nold",
        source_refs=[{"source_type": "kis_blocks", "source_id": "blk1"}],
        confidence=0.6,
        freshness="stale",
    )
    service.lint(scope="kis")

    repair = JueWikiRepairService(service)
    result = repair.run_once(scope="kis")

    assert result["status"] in {"ok", "warn"}
    assert result["actions"]
    assert result["actions"][0]["action_type"] in {"rebuild_page", "mark_unresolved"}
```

- [ ] **Step 2: Write API tests**

Create `tests/test_jue_wiki_phase2_api.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.wiki import WikiRouteDeps, build_wiki_router


class FakeWiki:
    def status(self):
        return {"status": "ok", "page_count": 1}

    def list_lint_findings(self, scope=None, status="open"):
        return [{"finding_id": "f1", "page_id": "kis.symbol.005930", "severity": "warn"}]

    def repair_once(self, scope=None):
        return {"status": "ok", "actions": [{"action_id": "a1"}]}

    def page_sources(self, page_id):
        return {"page_id": page_id, "sources": [{"source_type": "kis_blocks", "source_id": "blk1"}]}


def test_wiki_phase2_api_serves_findings_repair_and_sources() -> None:
    app = FastAPI()
    app.include_router(build_wiki_router(WikiRouteDeps(service=FakeWiki())))
    client = TestClient(app)

    assert client.get("/api/wiki/lint/findings").json()["findings"][0]["finding_id"] == "f1"
    assert client.post("/api/wiki/repair/run-once", json={"scope": "kis"}).json()["actions"][0]["action_id"] == "a1"
    assert client.get("/api/wiki/pages/kis.symbol.005930/sources").json()["sources"][0]["source_id"] == "blk1"
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_repair.py tests/test_jue_wiki_phase2_api.py -q
```

Expected: fail because repair service and endpoints are missing.

- [ ] **Step 4: Implement repair service**

Create `src/tradecraft/services/jue_wiki_repair.py` with:

```python
class JueWikiRepairService:
    def __init__(self, service: JueWikiService) -> None:
        self._service = service

    def run_once(self, *, scope: str | None = None) -> dict[str, Any]:
        findings = self._service.list_lint_findings(scope=scope, status="open")
        actions = []
        for finding in findings:
            action_type = self._action_type_for(finding)
            action = self._service.record_repair_action(
                finding_id=finding["finding_id"],
                page_id=finding["page_id"],
                action_type=action_type,
                status="scheduled" if action_type == "rebuild_page" else "unresolved",
                details={"finding_type": finding.get("finding_type", "")},
            )
            actions.append(action)
        return {"status": "ok" if actions else "ok", "actions": actions}

    def _action_type_for(self, finding: dict[str, Any]) -> str:
        if finding.get("finding_type") in {"stale_page", "missing_sources", "oversized_page"}:
            return "rebuild_page"
        return "mark_unresolved"
```

- [ ] **Step 5: Add repository helpers and API routes**

Add these `JueWikiService` methods:

```python
def list_lint_findings(self, *, scope: str | None = None, status: str = "open") -> list[dict[str, Any]]:
    ...

def record_repair_action(
    self,
    *,
    finding_id: str,
    page_id: str,
    action_type: str,
    status: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    ...

def repair_once(self, *, scope: str | None = None) -> dict[str, Any]:
    return JueWikiRepairService(self).run_once(scope=scope)

def page_sources(self, page_id: str) -> dict[str, Any]:
    ...
```

Add routes:

- `GET /api/wiki/lint/findings`
- `POST /api/wiki/repair/run-once`
- `GET /api/wiki/pages/{page_id}/sources`

Protect `repair/run-once` with admin auth.

- [ ] **Step 6: Verify Task 4**

Run:

```bash
pytest tests/test_jue_wiki_repair.py tests/test_jue_wiki_phase2_api.py -q
```

Expected: pass.

---

## Task 5: Compile Playbook Pages

**Files:**
- Create: `src/tradecraft/services/jue_wiki_playbooks.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki_playbooks.py`

- [ ] **Step 1: Write playbook compiler tests**

Create `tests/test_jue_wiki_playbooks.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_playbooks import JueWikiPlaybookCompiler


def test_playbook_compiler_writes_kis_and_binance_pages(tmp_path: Path) -> None:
    memory_db = tmp_path / "investment_memory.db"
    with sqlite3.connect(memory_db) as conn:
        conn.execute(
            """
            CREATE TABLE block_reflections (
                block_id TEXT PRIMARY KEY,
                scope TEXT,
                symbol TEXT,
                lesson TEXT,
                pnl_krw REAL DEFAULT 0,
                pnl_usdt REAL DEFAULT 0,
                created_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO block_reflections VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("kis-1", "kis", "005930", "Pullback entries need wider patience.", 12000, 0, "2026-06-28T09:00:00+09:00"),
        )
        conn.execute(
            "INSERT INTO block_reflections VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bin-1", "binance", "BTCUSDT", "Squeeze trades need spread confirmation.", 0, 1.2, "2026-06-28T09:00:00+09:00"),
        )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    compiler = JueWikiPlaybookCompiler(service, investment_memory_db_path=memory_db)
    result = compiler.compile_all()

    assert result["status"] == "ok"
    assert result["updated_count"] == 2
    assert service.read_page("kis.playbooks.reflection_lessons")["status"] == "ok"
    assert service.read_page("binance.playbooks.reflection_lessons")["status"] == "ok"
```

- [ ] **Step 2: Run playbook test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_playbooks.py -q
```

Expected: fail because the compiler is missing.

- [ ] **Step 3: Implement playbook compiler**

Create `src/tradecraft/services/jue_wiki_playbooks.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.services.jue_wiki import JueWikiService


class JueWikiPlaybookCompiler:
    def __init__(self, service: JueWikiService, *, investment_memory_db_path: str | Path) -> None:
        self._service = service
        self._memory_db_path = Path(investment_memory_db_path)

    def compile_all(self) -> dict[str, Any]:
        reflections = self._load_reflections()
        updated = 0
        for scope in ("kis", "binance"):
            scoped = [row for row in reflections if row["scope"] == scope]
            if not scoped:
                continue
            page_id = f"{scope}.playbooks.reflection_lessons"
            title = f"{scope.upper()} Reflection Lessons"
            content = self._render_page(title=title, scope=scope, reflections=scoped)
            self._service.write_page(
                page_id=page_id,
                scope=scope,
                page_type="playbook",
                title=title,
                symbols=sorted({row["symbol"] for row in scoped if row["symbol"]}),
                content=content,
                source_refs=[
                    {"source_type": "block_reflections", "source_id": row["block_id"]}
                    for row in scoped
                ],
                confidence=min(0.25 + len(scoped) * 0.05, 0.8),
                freshness="fresh",
            )
            updated += 1
        return {"status": "ok", "updated_count": updated}

    def _load_reflections(self) -> list[dict[str, Any]]:
        if not self._memory_db_path.exists():
            return []
        with sqlite3.connect(self._memory_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT block_id, scope, symbol, lesson, pnl_krw, pnl_usdt, created_at
                FROM block_reflections
                ORDER BY created_at DESC
                LIMIT 200
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _render_page(self, *, title: str, scope: str, reflections: list[dict[str, Any]]) -> str:
        lines = [
            f"# {title}",
            "",
            "## Current Stance",
            "Use these lessons as soft playbook evidence inside Jue manager reasoning.",
            "",
            "## Durable Facts",
        ]
        for row in reflections[:20]:
            lines.append(f"- {row['symbol']}: {row['lesson']}")
        lines.extend([
            "",
            "## Evidence Links",
            *[f"- block_reflections:{row['block_id']}" for row in reflections[:20]],
            "",
            "## Trading History",
            f"- Reflection samples: {len(reflections)}",
            "",
            "## Lessons",
            *[f"- {row['lesson']}" for row in reflections[:20]],
            "",
            "## Contradictions",
            "- None recorded in this compile pass.",
            "",
            "## Open Questions",
            "- Which lessons survive larger sample sizes?",
            "",
            "## Next Context Pack Summary",
            f"{scope.upper()} has {len(reflections)} recent reflection lessons available for playbook-aware decisions.",
        ])
        return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Verify Task 5**

Run:

```bash
pytest tests/test_jue_wiki_playbooks.py -q
```

Expected: pass.

---

## Task 6: Project Performance Metrics Into Wiki

**Files:**
- Create: `src/tradecraft/services/jue_wiki_performance.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki_performance.py`

- [ ] **Step 1: Write performance projection tests**

Create `tests/test_jue_wiki_performance.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_performance import JueWikiPerformanceProjector


def test_performance_projector_marks_profitable_playbook_active(tmp_path: Path) -> None:
    perf_db = tmp_path / "live_performance.db"
    with sqlite3.connect(perf_db) as conn:
        conn.execute(
            """
            CREATE TABLE playbook_outcomes (
                scope TEXT,
                playbook_id TEXT,
                pnl REAL,
                drawdown_pct REAL,
                created_at TEXT
            )
            """
        )
        for pnl in (1.0, 1.5, -0.2, 0.8, 0.4, 1.1):
            conn.execute(
                "INSERT INTO playbook_outcomes VALUES (?, ?, ?, ?, ?)",
                ("binance", "volatile_attack", pnl, 1.2, "2026-06-28T09:00:00+09:00"),
            )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service.write_page(
        page_id="binance.playbooks.volatile_attack",
        scope="binance",
        page_type="playbook",
        title="Volatile Attack",
        symbols=[],
        content="# Volatile Attack\n\n## Next Context Pack Summary\nSmall aggressive lane.",
        source_refs=[{"source_type": "playbook_outcomes", "source_id": "volatile_attack"}],
        confidence=0.5,
        freshness="fresh",
    )

    projector = JueWikiPerformanceProjector(service, performance_db_path=perf_db)
    result = projector.project_all()

    assert result["status"] == "ok"
    metric = service.playbook_metric("binance.playbooks.volatile_attack")
    assert metric["sample_count"] == 6
    assert metric["win_rate"] > 0.6
    assert metric["status"] in {"active", "probe"}
```

- [ ] **Step 2: Run performance test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_performance.py -q
```

Expected: fail because performance projector is missing.

- [ ] **Step 3: Implement performance projector**

Create `src/tradecraft/services/jue_wiki_performance.py` with methods:

- `project_all() -> dict[str, Any]`
- `_load_outcomes() -> list[dict[str, Any]]`
- `_metric_for(scope, playbook_id, outcomes) -> dict[str, Any]`

Status rules:

- `active`: sample count >= 10, expectancy > 0, profit factor >= 1.3, max drawdown <= 8%.
- `probe`: sample count < 10 or expectancy is slightly positive.
- `degraded`: sample count >= 10 and expectancy <= 0.
- `paused`: max drawdown > 12% or profit factor < 0.8 with sample count >= 10.

- [ ] **Step 4: Add `JueWikiService` metric helpers**

Add:

```python
def upsert_playbook_metric(self, metric: dict[str, Any]) -> None:
    ...

def playbook_metric(self, page_id: str) -> dict[str, Any]:
    ...
```

- [ ] **Step 5: Verify Task 6**

Run:

```bash
pytest tests/test_jue_wiki_performance.py -q
```

Expected: pass.

---

## Task 7: Integrate Selector Into Managers

**Files:**
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/runtime/kis_block_trader_runner.py`
- Modify: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Modify: `src/tradecraft/runtime/market_judge_runner.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/services/market_judgment.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_market_judgment.py`

- [ ] **Step 1: Add manager integration tests**

For each manager prompt test, assert this shape:

```python
wiki = run["prompt"]["jue_wiki"]
assert wiki["status"] == "ok"
assert wiki["selection_run_id"]
assert wiki["pages"]
assert wiki["budget_report"]["status"] == "ok"
assert "selection_reasons" in wiki["pages"][0]
```

- [ ] **Step 2: Run manager tests and confirm failure**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_kis_manager_prompt_stays_under_budget_with_wiki_context tests/test_binance_block_trader.py::test_binance_manager_prompt_stays_under_budget_with_wiki_context tests/test_market_judgment.py::test_market_judge_payload_includes_jue_wiki_context -q
```

Expected: fail because providers still expose plain `context_pack()` output.

- [ ] **Step 3: Build selector provider factory**

In each runner, change the wiki provider from `service.context_pack` to a function that constructs `JueWikiSelector(service).select(...)` and returns a dictionary:

```python
{
    "status": result.status,
    "selection_run_id": result.selection_run_id,
    "target_scope": result.target_scope,
    "content": result.content,
    "pages": [
        {
            "page_id": page.page_id,
            "rank": page.rank,
            "score": page.score,
            "selection_reasons": page.reasons,
            "selection_penalties": page.penalties,
            "char_count": page.char_count,
            "source_refs": page.source_refs,
        }
        for page in result.pages
    ],
    "rejected_pages": result.rejected_pages,
    "budget_report": result.budget_report,
}
```

- [ ] **Step 4: Respect prompt mode**

Manager behavior by `jue_wiki_prompt_mode`:

- `observe`: attach only `jue_wiki_selection_observation`.
- `assist`: attach `jue_wiki` plus capped raw memory/RAG.
- `primary`: attach `jue_wiki` and trim raw memory/RAG to explicit evidence summaries.

- [ ] **Step 5: Verify Task 7**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_kis_manager_prompt_stays_under_budget_with_wiki_context tests/test_binance_block_trader.py::test_binance_manager_prompt_stays_under_budget_with_wiki_context tests/test_market_judgment.py::test_market_judge_payload_includes_jue_wiki_context -q
```

Expected: pass.

---

## Task 8: Upgrade Wiki API And UI

**Files:**
- Modify: `src/tradecraft/api/wiki.py`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/index.html`
- Test: `tests/test_jue_wiki_phase2_api.py`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add API tests for search**

Add to `tests/test_jue_wiki_phase2_api.py`:

```python
def test_wiki_search_api_returns_ranked_pages() -> None:
    app = FastAPI()

    class SearchWiki(FakeWiki):
        def search(self, query="", scope=None, page_type=None):
            return {"status": "ok", "pages": [{"page_id": "kis.symbol.005930", "title": "삼성전자"}]}

    app.include_router(build_wiki_router(WikiRouteDeps(service=SearchWiki())))
    client = TestClient(app)

    payload = client.get("/api/wiki/search?query=삼성전자&scope=kis").json()
    assert payload["pages"][0]["page_id"] == "kis.symbol.005930"
```

- [ ] **Step 2: Add static UI tests**

Add to `tests/test_static_ui.py`:

```python
def test_jue_wiki_phase2_ui_controls_exist() -> None:
    root = Path("src/tradecraft/web/static")
    app = (root / "app.js").read_text()
    index = (root / "index.html").read_text()

    assert "/wiki/search" in app
    assert "/wiki/lint/findings" in app
    assert "/wiki/repair/run-once" in app
    assert "jue-wiki-search" in index or "renderJueWiki" in app
```

- [ ] **Step 3: Run UI/API tests and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_phase2_api.py::test_wiki_search_api_returns_ranked_pages tests/test_static_ui.py::test_jue_wiki_phase2_ui_controls_exist -q
```

Expected: fail because search and UI controls are missing.

- [ ] **Step 4: Add API route**

Add:

- `GET /api/wiki/search`

Return:

```python
{
    "status": "ok",
    "query": query,
    "scope": scope,
    "pages": service.search(query=query, scope=scope, page_type=page_type),
}
```

- [ ] **Step 5: Add UI panels**

Enhance the Jue Wiki panel with:

- Health strip: page count, stale count, open lint count, last run.
- Search input.
- Scope segmented control: all, KIS, Binance.
- Page list with confidence, freshness, source count, lint status.
- Page detail drawer.
- Source references.
- Repair button for admin-authenticated sessions.
- Latest selection trace with page rankings and prompt budget.

- [ ] **Step 6: Verify Task 8**

Run:

```bash
pytest tests/test_jue_wiki_phase2_api.py tests/test_static_ui.py::test_jue_wiki_phase2_ui_controls_exist -q
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

---

## Task 9: Extend The Wiki Runner

**Files:**
- Modify: `src/tradecraft/runtime/jue_wiki_runner.py`
- Modify: `src/tradecraft/api/ops_payloads.py`
- Test: `tests/test_jue_wiki_runner.py`
- Test: `tests/test_process_status.py`

- [ ] **Step 1: Add runner cycle test**

Add to `tests/test_jue_wiki_runner.py`:

```python
def test_jue_wiki_runner_cycle_reports_phase2_steps(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    result = run_once(service=service, state_path=tmp_path / "jue_wiki_state.json")

    assert "rebuild" in result
    assert "lint" in result
    assert "repair" in result
    assert "playbooks" in result
    assert "performance" in result
```

- [ ] **Step 2: Run runner test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_runner.py::test_jue_wiki_runner_cycle_reports_phase2_steps -q
```

Expected: fail because current runner reports only Phase 1 fields.

- [ ] **Step 3: Extend `run_once()`**

Update runner cycle order:

1. `service.rebuild(scope="all")`
2. `service.lint(scope="all")`
3. `service.repair_once(scope=None)` when repair is enabled
4. `JueWikiPlaybookCompiler(...).compile_all()`
5. `JueWikiPerformanceProjector(...).project_all()`
6. Write state JSON with each step's status and counts

- [ ] **Step 4: Add ops readiness fields**

Expose:

- `wiki_open_lint_count`
- `wiki_stale_page_count`
- `wiki_last_selection_at`
- `wiki_last_repair_at`
- `wiki_prompt_pressure`

Readiness warning rules:

- `jue_wiki_runner_stopped` when runner is not active.
- `jue_wiki_lint_findings_open` when open findings exceed 20.
- `jue_wiki_stale_pages_high` when stale pages exceed 30% of active pages.
- `jue_wiki_prompt_pressure_high` when latest selected context consumes more than 75% of configured wiki context budget.

- [ ] **Step 5: Verify Task 9**

Run:

```bash
pytest tests/test_jue_wiki_runner.py tests/test_process_status.py -q
```

Expected: pass.

---

## Task 10: Update Documentation And Specs

**Files:**
- Modify: `docs/spec/08_research_memory.md`
- Modify: `docs/spec/21_memory_learning_contracts.md`
- Modify: `docs/spec/11_api_reference.md`
- Modify: `docs/spec/12_config_env.md`
- Test: `tests/test_docs_spec.py`

- [ ] **Step 1: Add docs test**

Add to `tests/test_docs_spec.py`:

```python
def test_specs_document_jue_wiki_phase2() -> None:
    research = Path("docs/spec/08_research_memory.md").read_text()
    memory = Path("docs/spec/21_memory_learning_contracts.md").read_text()
    api = Path("docs/spec/11_api_reference.md").read_text()
    env = Path("docs/spec/12_config_env.md").read_text()

    assert "Jue Wiki Selector" in research
    assert "wiki_selection_runs" in memory
    assert "/api/wiki/search" in api
    assert "TRADECRAFT_JUE_WIKI_PROMPT_MODE" in env
```

- [ ] **Step 2: Run docs test and confirm failure**

Run:

```bash
pytest tests/test_docs_spec.py::test_specs_document_jue_wiki_phase2 -q
```

Expected: fail because specs do not describe Phase 2 yet.

- [ ] **Step 3: Update docs**

Document:

- Selector ranking.
- Repair loop.
- Playbook metrics.
- Prompt modes.
- API endpoints.
- Env settings.
- Source-of-truth boundary.

- [ ] **Step 4: Verify Task 10**

Run:

```bash
pytest tests/test_docs_spec.py::test_specs_document_jue_wiki_phase2 -q
```

Expected: pass.

---

## Task 11: End-To-End Verification

**Files:**
- No new source files.
- Verify all files touched by Tasks 1-10.

- [ ] **Step 1: Run focused pytest**

Run:

```bash
pytest \
  tests/test_jue_wiki.py \
  tests/test_jue_wiki_selector.py \
  tests/test_jue_wiki_repair.py \
  tests/test_jue_wiki_playbooks.py \
  tests/test_jue_wiki_performance.py \
  tests/test_jue_wiki_phase2_api.py \
  tests/test_jue_wiki_runner.py \
  tests/test_manager_prompt_budget.py \
  tests/test_docs_spec.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Run manager integration pytest**

Run:

```bash
pytest \
  tests/test_kis_block_trader.py::test_kis_manager_prompt_stays_under_budget_with_wiki_context \
  tests/test_binance_block_trader.py::test_binance_manager_prompt_stays_under_budget_with_wiki_context \
  tests/test_market_judgment.py::test_market_judge_payload_includes_jue_wiki_context \
  -q
```

Expected: pass.

- [ ] **Step 3: Run frontend syntax checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no syntax errors.

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check -- \
  src/tradecraft \
  tests \
  docs/spec \
  docs/superpowers/plans/2026-06-28-jue-wiki-phase-2-intelligence-compression.md \
  docs/superpowers/specs/2026-06-28-jue-wiki-phase-2-design.md
```

Expected: no trailing whitespace or patch errors.

- [ ] **Step 5: Manual local smoke**

Run:

```bash
python - <<'PY'
from pathlib import Path
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_selector import JueWikiSelectionRequest, JueWikiSelector

service = JueWikiService(
    JueWikiConfig(
        root_path=Path(".runtime/jue_wiki"),
        db_path=Path(".runtime/jue_wiki/wiki.db"),
    )
)
print(service.status())
print(JueWikiSelector(service).select(JueWikiSelectionRequest(target_scope="kis", symbols=["005930"], max_chars=4000)).budget_report)
PY
```

Expected: status prints without exception and selector returns a budget report.

- [ ] **Step 6: API smoke**

Run with a valid admin token for mutation endpoints:

```bash
curl -s http://127.0.0.1:18080/api/wiki/status
curl -s "http://127.0.0.1:18080/api/wiki/search?scope=kis&query=005930"
curl -s http://127.0.0.1:18080/api/wiki/lint/findings
```

Expected: each endpoint returns JSON with `status` or the expected list field.

---

## Rollout Plan

1. Land Phase 2 with `TRADECRAFT_JUE_WIKI_PROMPT_MODE=assist`.
2. Run one KIS session and one Binance overnight session.
3. Compare:
   - manager prompt char count
   - LLM timeout rate
   - selected page count
   - rejected stale/lint page count
   - decision trace readability
4. Move to `primary` only after:
   - no prompt-budget warnings for at least two trading sessions
   - no wiki runner stoppage
   - playbook pages show source references and performance metrics
   - UI can explain which pages influenced a decision

## Final Verification Command Set

```bash
pytest \
  tests/test_jue_wiki.py \
  tests/test_jue_wiki_selector.py \
  tests/test_jue_wiki_repair.py \
  tests/test_jue_wiki_playbooks.py \
  tests/test_jue_wiki_performance.py \
  tests/test_jue_wiki_phase2_api.py \
  tests/test_jue_wiki_runner.py \
  tests/test_manager_prompt_budget.py \
  tests/test_docs_spec.py \
  -q
pytest \
  tests/test_kis_block_trader.py::test_kis_manager_prompt_stays_under_budget_with_wiki_context \
  tests/test_binance_block_trader.py::test_binance_manager_prompt_stays_under_budget_with_wiki_context \
  tests/test_market_judgment.py::test_market_judge_payload_includes_jue_wiki_context \
  -q
node --check src/tradecraft/web/static/app.js
git diff --check
```

## Self-Review

- Spec coverage: selector, repair, playbooks, performance projection, manager integration, UI, runner, docs, and rollout are covered.
- Placeholder scan: no task depends on unspecified behavior; scoring, status rules, endpoints, and test expectations are explicit.
- Type consistency: request/result dataclass names match manager integration examples.
- Scope check: this is a focused Phase 2 of Jue Wiki, not a trading strategy rewrite.
