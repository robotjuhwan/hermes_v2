# Jue Wiki Phase 3 Applied Intelligence Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Phase 2 Jue Wiki selection into a measurable applied intelligence loop that shows whether selected knowledge actually improved KIS Jue, Binance Jue, and market judge decisions.

**Architecture:** Keep Phase 2 selector, repair, playbook, and performance projection intact. Add a thin application layer that links `wiki_selection_runs` to manager runs, block actions, market judgments, and realized outcomes, then projects effectiveness back into wiki metadata and selector ranking. The loop must stay advisory: it changes context ranking, mode recommendations, and operator visibility, but never bypasses exchange safety gates, kill switches, reconciliation, or deterministic rule execution.

**Tech Stack:** Python 3.10+, SQLite via stdlib `sqlite3`, FastAPI routers in `src/tradecraft/api`, existing static frontend in `src/tradecraft/web/static`, existing pytest suite, existing runner/process model, `.runtime/jue_wiki/wiki.db`, KIS/Binance block DBs, market judgment DB, live performance DB, and investment memory DB.

---

## Phase 3 Positioning

Phase 1 answered: "Can HERMES compile growing RAG, memory, and trading ledgers into scoped wiki pages?"

Phase 2 answered: "Can Jue select relevant wiki pages with budget, quality, repair, and performance metadata?"

Phase 3 answers: "Did the selected wiki context actually help Jue make better decisions, and how should that evidence change future selection, prompt mode, and operator trust?"

This is the application layer. It must make every selected page accountable:

- Which decision saw this page?
- What did Jue decide after seeing it?
- Did the resulting block, watch action, or market judgment perform?
- Was the page helpful, neutral, stale, misleading, or over-weighted?
- Should this page become more trusted, remain probe-only, or be demoted?

## Non-Goals

- Do not add a new trading strategy.
- Do not make wiki pages source-of-truth for blocks, orders, fills, balances, PnL, reports, or memory.
- Do not create hard strategy bans from wiki effectiveness.
- Do not auto-promote `primary` mode globally.
- Do not weaken admin auth on wiki read routes.
- Do not hide attribution failures with synthetic success.
- Do not require every old manager run to backfill perfectly; backfill must report gaps.

## Design Brainstorm

### Approach A: Passive Reporting Only

Record selected pages and show dashboards, but do not feed results back into ranking.

Pros:
- Lowest risk.
- Easy to reason about.
- Useful for operator review.

Cons:
- Jue does not materially improve from outcomes.
- Bad pages remain equally likely to be selected unless manually fixed.
- Phase 3 becomes mostly observability.

### Approach B: Effectiveness-Weighted Selection

Record selection outcomes, compute page/page-type/playbook effectiveness, and use those metrics as a bounded selector score adjustment.

Pros:
- Directly closes the loop from "selected knowledge" to "future knowledge ranking."
- Keeps changes advisory and auditable.
- Works for KIS, Binance, and market judge without changing trading execution.

Cons:
- Requires careful source and venue separation.
- Needs conservative defaults to avoid overfitting small samples.
- Needs UI so the operator can see why selection changed.

### Approach C: Mode Rollout Controller

Use measured effectiveness to recommend `observe`, `assist`, or `primary` per scope, page type, lane, and horizon.

Pros:
- Gives a clean path to "잘 적용하는 단계."
- Lets KIS and Binance evolve differently.
- Prevents accidental global primary-mode overreach.

Cons:
- Needs enough sample size.
- Recommendations can be confusing if UI is weak.
- Must remain advisory unless explicitly enabled by config.

### Recommendation

Implement B plus a conservative slice of C. Phase 3 should first build attribution and effectiveness projection, then feed a small bounded bonus/penalty into selector ranking, and finally expose mode recommendations. This gives Jue learning pressure without making wiki metrics a hidden trading rule.

## File Structure

### Create

- `src/tradecraft/services/jue_wiki_application.py`
  - Owns selection application links, outcome attribution, effectiveness aggregation, and selector feedback packets.
- `tests/test_jue_wiki_application.py`
  - Unit tests for link ingestion, outcome projection, effectiveness scoring, and no hidden fallback behavior.

### Modify

- `src/tradecraft/services/jue_wiki.py`
  - Add Phase 3 schema tables and repository helpers.
- `src/tradecraft/services/jue_wiki_selector.py`
  - Add optional effectiveness-weighted score adjustment with caps.
- `src/tradecraft/services/kis_block_trader.py`
  - Persist `wiki_selection_run_id` and selected page IDs in manager run metadata/block action metadata.
- `src/tradecraft/services/binance_block_trader.py`
  - Persist the same wiki attribution metadata for Binance manager runs and block actions.
- `src/tradecraft/services/market_judgment.py`
  - Persist wiki attribution metadata for market judgment runs and per-symbol judgments.
- `src/tradecraft/runtime/jue_wiki_runner.py`
  - Run Phase 3 attribution/effectiveness projection after Phase 2 performance projection.
- `src/tradecraft/api/wiki.py`
  - Add admin-protected application/effectiveness endpoints.
- `src/tradecraft/api/ops_payloads.py`
  - Add Phase 3 readiness fields and warnings.
- `src/tradecraft/web/static/app.js`
  - Add applied-intelligence panels to the Jue Wiki UI area.
- `docs/spec/08_research_memory.md`
  - Document applied intelligence loop.
- `docs/spec/21_memory_learning_contracts.md`
  - Document attribution/effectiveness contracts.
- `docs/spec/11_api_reference.md`
  - Document new admin-protected wiki application endpoints.
- `docs/spec/12_config_env.md`
  - Document Phase 3 config.
- `.env.example`
  - Add non-secret Phase 3 env examples.

## New Data Model

Extend `.runtime/jue_wiki/wiki.db`:

```sql
CREATE TABLE IF NOT EXISTS wiki_decision_links (
    link_id TEXT PRIMARY KEY,
    selection_run_id TEXT NOT NULL,
    manager_run_id TEXT NOT NULL DEFAULT '',
    decision_scope TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    horizon TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    prompt_mode TEXT NOT NULL DEFAULT '',
    selected_pages_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    linked_at TEXT NOT NULL
);
```

```sql
CREATE TABLE IF NOT EXISTS wiki_selection_outcomes (
    outcome_id TEXT PRIMARY KEY,
    link_id TEXT NOT NULL,
    selection_run_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    decision_scope TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    horizon TEXT NOT NULL DEFAULT '',
    outcome_kind TEXT NOT NULL,
    outcome_status TEXT NOT NULL,
    pnl_value REAL NOT NULL DEFAULT 0.0,
    pnl_currency TEXT NOT NULL DEFAULT '',
    return_pct REAL NOT NULL DEFAULT 0.0,
    mfe_pct REAL NOT NULL DEFAULT 0.0,
    mae_pct REAL NOT NULL DEFAULT 0.0,
    holding_minutes REAL NOT NULL DEFAULT 0.0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    computed_at TEXT NOT NULL
);
```

```sql
CREATE TABLE IF NOT EXISTS wiki_page_effectiveness (
    page_id TEXT NOT NULL,
    decision_scope TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT '',
    horizon TEXT NOT NULL DEFAULT '',
    sample_count INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0.0,
    expectancy REAL NOT NULL DEFAULT 0.0,
    avg_return_pct REAL NOT NULL DEFAULT 0.0,
    median_mae_pct REAL NOT NULL DEFAULT 0.0,
    drawdown_pressure REAL NOT NULL DEFAULT 0.0,
    helpful_score REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'probe',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (page_id, decision_scope, venue, horizon)
);
```

```sql
CREATE TABLE IF NOT EXISTS wiki_mode_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    decision_scope TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT '',
    page_type TEXT NOT NULL DEFAULT '',
    horizon TEXT NOT NULL DEFAULT '',
    recommended_mode TEXT NOT NULL,
    current_mode TEXT NOT NULL DEFAULT '',
    sample_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
```

## New Config

Add to `AppSettings`:

```python
jue_wiki_application_enabled: bool = Field(
    default=True,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_APPLICATION_ENABLED", "jue_wiki_application_enabled"),
)
jue_wiki_effectiveness_weight: float = Field(
    default=0.12,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT", "jue_wiki_effectiveness_weight"),
)
jue_wiki_effectiveness_max_adjustment: float = Field(
    default=8.0,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MAX_ADJUSTMENT", "jue_wiki_effectiveness_max_adjustment"),
)
jue_wiki_effectiveness_min_samples: int = Field(
    default=5,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MIN_SAMPLES", "jue_wiki_effectiveness_min_samples"),
)
jue_wiki_mode_recommendation_min_samples: int = Field(
    default=20,
    validation_alias=AliasChoices("TRADECRAFT_JUE_WIKI_MODE_RECOMMENDATION_MIN_SAMPLES", "jue_wiki_mode_recommendation_min_samples"),
)
```

## Task 1: Add Phase 3 Config And Schema

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_config.py`
- Test: `tests/test_jue_wiki.py`

- [ ] **Step 1: Add failing config test**

Add to `tests/test_config.py`:

```python
def test_jue_wiki_phase3_settings_have_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_APPLICATION_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MAX_ADJUSTMENT", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MIN_SAMPLES", raising=False)
    monkeypatch.delenv("TRADECRAFT_JUE_WIKI_MODE_RECOMMENDATION_MIN_SAMPLES", raising=False)

    settings = AppSettings()

    assert settings.jue_wiki_application_enabled is True
    assert settings.jue_wiki_effectiveness_weight == 0.12
    assert settings.jue_wiki_effectiveness_max_adjustment == 8.0
    assert settings.jue_wiki_effectiveness_min_samples == 5
    assert settings.jue_wiki_mode_recommendation_min_samples == 20
```

- [ ] **Step 2: Run config test and confirm failure**

Run:

```bash
pytest tests/test_config.py::test_jue_wiki_phase3_settings_have_safe_defaults -q
```

Expected: fail because settings do not exist.

- [ ] **Step 3: Add config fields**

Add the fields from "New Config" to `src/tradecraft/config.py` next to existing Jue Wiki settings.

- [ ] **Step 4: Add failing schema test**

Add to `tests/test_jue_wiki.py`:

```python
def test_jue_wiki_phase3_tables_are_created(tmp_path: Path) -> None:
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

    assert "wiki_decision_links" in names
    assert "wiki_selection_outcomes" in names
    assert "wiki_page_effectiveness" in names
    assert "wiki_mode_recommendations" in names
```

- [ ] **Step 5: Run schema test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki.py::test_jue_wiki_phase3_tables_are_created -q
```

Expected: fail because tables do not exist.

- [ ] **Step 6: Add schema creation**

In `JueWikiService._init_db()`, add the SQL tables from "New Data Model".

- [ ] **Step 7: Verify Task 1**

Run:

```bash
pytest tests/test_config.py::test_jue_wiki_phase3_settings_have_safe_defaults tests/test_jue_wiki.py::test_jue_wiki_phase3_tables_are_created -q
```

Expected: pass.

## Task 2: Build Jue Wiki Application Service

**Files:**
- Create: `src/tradecraft/services/jue_wiki_application.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki_application.py`

- [ ] **Step 1: Write failing link recording test**

Create `tests/test_jue_wiki_application.py`:

```python
from __future__ import annotations

from pathlib import Path

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_application import JueWikiApplicationService


def test_record_decision_link_persists_selection_trace(tmp_path: Path) -> None:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service = JueWikiApplicationService(wiki)

    link = service.record_decision_link(
        selection_run_id="selection:abc",
        manager_run_id="kis-manager-1",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930", "kis.playbook.reflection_lessons"],
        symbol="005930",
        block_id="blk-1",
        horizon="mid_term",
        action="create_block",
        prompt_mode="assist",
        metadata={"source": "unit"},
    )

    rows = service.list_decision_links(selection_run_id="selection:abc")

    assert link["status"] == "ok"
    assert rows[0]["manager_run_id"] == "kis-manager-1"
    assert rows[0]["selected_pages"] == [
        "kis.symbol.005930",
        "kis.playbook.reflection_lessons",
    ]
```

- [ ] **Step 2: Run test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_application.py::test_record_decision_link_persists_selection_trace -q
```

Expected: fail because service does not exist.

- [ ] **Step 3: Implement service skeleton**

Create `src/tradecraft/services/jue_wiki_application.py`:

```python
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from tradecraft.services.jue_wiki import JueWikiService


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


class JueWikiApplicationService:
    def __init__(self, wiki: JueWikiService) -> None:
        self.wiki = wiki

    def record_decision_link(
        self,
        *,
        selection_run_id: str,
        manager_run_id: str,
        decision_scope: str,
        decision_type: str,
        selected_pages: list[str],
        symbol: str = "",
        block_id: str = "",
        venue: str = "",
        horizon: str = "",
        action: str = "",
        prompt_mode: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        link_id = f"wiki-link:{uuid.uuid4().hex}"
        now = _utc_now_iso()
        with self.wiki._connect() as conn:
            conn.execute(
                """
                INSERT INTO wiki_decision_links (
                    link_id, selection_run_id, manager_run_id, decision_scope,
                    decision_type, symbol, block_id, venue, horizon, action,
                    prompt_mode, selected_pages_json, metadata_json, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    selection_run_id,
                    manager_run_id,
                    decision_scope,
                    decision_type,
                    symbol,
                    block_id,
                    venue,
                    horizon,
                    action,
                    prompt_mode,
                    _json_dumps(selected_pages),
                    _json_dumps(metadata or {}),
                    now,
                ),
            )
        return {"status": "ok", "link_id": link_id, "linked_at": now}

    def list_decision_links(
        self,
        *,
        selection_run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.wiki.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        if selection_run_id:
            clauses.append("selection_run_id = ?")
            params.append(selection_run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(int(limit), 1))
        with self.wiki._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_decision_links
                {where}
                ORDER BY linked_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {
                **dict(row),
                "selected_pages": _json_loads_list(row["selected_pages_json"]),
            }
            for row in rows
        ]
```

- [ ] **Step 4: Verify Task 2**

Run:

```bash
pytest tests/test_jue_wiki_application.py::test_record_decision_link_persists_selection_trace -q
```

Expected: pass.

## Task 3: Attribute Selection Outcomes

**Files:**
- Modify: `src/tradecraft/services/jue_wiki_application.py`
- Test: `tests/test_jue_wiki_application.py`

- [ ] **Step 1: Write failing outcome projection test**

Add:

```python
def test_project_outcomes_records_page_level_result(tmp_path: Path) -> None:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service = JueWikiApplicationService(wiki)
    link = service.record_decision_link(
        selection_run_id="selection:abc",
        manager_run_id="binance-manager-1",
        decision_scope="binance",
        decision_type="block_manager",
        selected_pages=["binance.playbook.live.binance.edge"],
        symbol="BTCUSDT",
        block_id="bin-blk-1",
        venue="binance",
        horizon="short_term",
        action="create_block",
        prompt_mode="assist",
    )

    result = service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        pnl_value=1.25,
        pnl_currency="USDT",
        return_pct=0.42,
        mfe_pct=0.9,
        mae_pct=-0.2,
        holding_minutes=48,
        evidence={"exit_reason": "target"},
    )

    rows = service.list_selection_outcomes(selection_run_id="selection:abc")

    assert result["status"] == "ok"
    assert result["outcome_count"] == 1
    assert rows[0]["page_id"] == "binance.playbook.live.binance.edge"
    assert rows[0]["outcome_status"] == "win"
```

- [ ] **Step 2: Run test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_application.py::test_project_outcomes_records_page_level_result -q
```

Expected: fail because outcome methods do not exist.

- [ ] **Step 3: Implement outcome recording**

Add to `JueWikiApplicationService`:

```python
    def record_selection_outcomes(
        self,
        *,
        link_id: str,
        outcome_kind: str,
        outcome_status: str,
        pnl_value: float = 0.0,
        pnl_currency: str = "",
        return_pct: float = 0.0,
        mfe_pct: float = 0.0,
        mae_pct: float = 0.0,
        holding_minutes: float = 0.0,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        links = self.list_decision_links(limit=1_000)
        link = next((row for row in links if row["link_id"] == link_id), None)
        if link is None:
            return {
                "status": "error",
                "error_message": f"decision link not found: {link_id}",
                "outcome_count": 0,
            }
        now = _utc_now_iso()
        pages = [str(page) for page in link.get("selected_pages", []) if str(page)]
        with self.wiki._connect() as conn:
            for page_id in pages:
                outcome_id = f"wiki-outcome:{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO wiki_selection_outcomes (
                        outcome_id, link_id, selection_run_id, page_id,
                        decision_scope, venue, symbol, block_id, horizon,
                        outcome_kind, outcome_status, pnl_value, pnl_currency,
                        return_pct, mfe_pct, mae_pct, holding_minutes,
                        evidence_json, computed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome_id,
                        link_id,
                        link["selection_run_id"],
                        page_id,
                        link["decision_scope"],
                        link["venue"],
                        link["symbol"],
                        link["block_id"],
                        link["horizon"],
                        outcome_kind,
                        outcome_status,
                        float(pnl_value),
                        pnl_currency,
                        float(return_pct),
                        float(mfe_pct),
                        float(mae_pct),
                        float(holding_minutes),
                        _json_dumps(evidence or {}),
                        now,
                    ),
                )
        return {"status": "ok", "outcome_count": len(pages)}

    def list_selection_outcomes(
        self,
        *,
        selection_run_id: str | None = None,
        page_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.wiki.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        if selection_run_id:
            clauses.append("selection_run_id = ?")
            params.append(selection_run_id)
        if page_id:
            clauses.append("page_id = ?")
            params.append(page_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(int(limit), 1))
        with self.wiki._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_selection_outcomes
                {where}
                ORDER BY computed_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Add duplicate prevention test**

Add:

```python
def test_record_selection_outcomes_requires_existing_link(tmp_path: Path) -> None:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service = JueWikiApplicationService(wiki)

    result = service.record_selection_outcomes(
        link_id="missing",
        outcome_kind="closed_block",
        outcome_status="loss",
    )

    assert result["status"] == "error"
    assert "not found" in result["error_message"]
```

- [ ] **Step 5: Verify Task 3**

Run:

```bash
pytest tests/test_jue_wiki_application.py -q
```

Expected: pass.

## Task 4: Compute Page Effectiveness

**Files:**
- Modify: `src/tradecraft/services/jue_wiki_application.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki_application.py`

- [ ] **Step 1: Write failing effectiveness test**

Add:

```python
def test_project_page_effectiveness_marks_helpful_page(tmp_path: Path) -> None:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service = JueWikiApplicationService(wiki)
    for idx, return_pct in enumerate([1.2, 0.8, -0.2, 1.1, 0.4, 0.9], start=1):
        link = service.record_decision_link(
            selection_run_id=f"selection:{idx}",
            manager_run_id=f"manager:{idx}",
            decision_scope="kis",
            decision_type="block_manager",
            selected_pages=["kis.playbook.reflection_lessons"],
            symbol="005930",
            block_id=f"blk-{idx}",
            venue="kis",
            horizon="mid_term",
            action="create_block",
            prompt_mode="assist",
        )
        service.record_selection_outcomes(
            link_id=link["link_id"],
            outcome_kind="closed_block",
            outcome_status="win" if return_pct > 0 else "loss",
            return_pct=return_pct,
            mae_pct=-0.3,
        )

    result = service.project_page_effectiveness(min_samples=5)
    metric = service.page_effectiveness(
        page_id="kis.playbook.reflection_lessons",
        decision_scope="kis",
        venue="kis",
        horizon="mid_term",
    )

    assert result["status"] == "ok"
    assert metric["sample_count"] == 6
    assert metric["win_rate"] > 0.6
    assert metric["helpful_score"] > 0
    assert metric["status"] in {"active", "probe"}
```

- [ ] **Step 2: Run test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_application.py::test_project_page_effectiveness_marks_helpful_page -q
```

Expected: fail.

- [ ] **Step 3: Implement effectiveness projection**

Add to `JueWikiApplicationService`:

```python
    def project_page_effectiveness(self, *, min_samples: int = 5) -> dict[str, Any]:
        self.wiki.initialize()
        outcomes = self.list_selection_outcomes(limit=10_000)
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in outcomes:
            key = (
                str(row["page_id"]),
                str(row["decision_scope"]),
                str(row["venue"]),
                str(row["horizon"]),
            )
            groups.setdefault(key, []).append(row)

        updated = 0
        now = _utc_now_iso()
        with self.wiki._connect() as conn:
            for (page_id, scope, venue, horizon), rows in groups.items():
                sample_count = len(rows)
                wins = sum(1 for row in rows if float(row.get("return_pct") or 0.0) > 0)
                returns = [float(row.get("return_pct") or 0.0) for row in rows]
                maes = sorted(float(row.get("mae_pct") or 0.0) for row in rows)
                expectancy = sum(returns) / sample_count if sample_count else 0.0
                win_rate = wins / sample_count if sample_count else 0.0
                avg_return = expectancy
                median_mae = maes[len(maes) // 2] if maes else 0.0
                drawdown_pressure = abs(min(maes)) if maes else 0.0
                confidence = min(sample_count / max(int(min_samples), 1), 1.0)
                helpful_score = max(min(expectancy * 10.0 + (win_rate - 0.5) * 12.0, 10.0), -10.0)
                if sample_count < min_samples:
                    status = "probe"
                elif helpful_score > 1.0 and expectancy > 0:
                    status = "active"
                elif helpful_score < -2.0 or expectancy < 0:
                    status = "degraded"
                else:
                    status = "probe"
                reasons = [
                    f"samples:{sample_count}",
                    f"win_rate:{win_rate:.4f}",
                    f"expectancy:{expectancy:.4f}",
                    f"median_mae:{median_mae:.4f}",
                ]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wiki_page_effectiveness (
                        page_id, decision_scope, venue, horizon, sample_count,
                        win_rate, expectancy, avg_return_pct, median_mae_pct,
                        drawdown_pressure, helpful_score, confidence, status,
                        reasons_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        page_id,
                        scope,
                        venue,
                        horizon,
                        sample_count,
                        win_rate,
                        expectancy,
                        avg_return,
                        median_mae,
                        drawdown_pressure,
                        helpful_score,
                        confidence,
                        status,
                        _json_dumps(reasons),
                        now,
                    ),
                )
                updated += 1
        return {"status": "ok", "updated_count": updated}

    def page_effectiveness(
        self,
        *,
        page_id: str,
        decision_scope: str,
        venue: str = "",
        horizon: str = "",
    ) -> dict[str, Any]:
        self.wiki.initialize()
        with self.wiki._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wiki_page_effectiveness
                WHERE page_id = ? AND decision_scope = ? AND venue = ? AND horizon = ?
                """,
                (page_id, decision_scope, venue, horizon),
            ).fetchone()
        return dict(row) if row else {"status": "missing", "page_id": page_id}
```

- [ ] **Step 4: Add low-sample protection test**

Add:

```python
def test_page_effectiveness_keeps_low_sample_pages_probe(tmp_path: Path) -> None:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service = JueWikiApplicationService(wiki)
    link = service.record_decision_link(
        selection_run_id="selection:one",
        manager_run_id="manager:one",
        decision_scope="kis",
        decision_type="block_manager",
        selected_pages=["kis.symbol.005930"],
        venue="kis",
        horizon="short_term",
    )
    service.record_selection_outcomes(
        link_id=link["link_id"],
        outcome_kind="closed_block",
        outcome_status="win",
        return_pct=3.0,
    )

    service.project_page_effectiveness(min_samples=5)
    metric = service.page_effectiveness(
        page_id="kis.symbol.005930",
        decision_scope="kis",
        venue="kis",
        horizon="short_term",
    )

    assert metric["sample_count"] == 1
    assert metric["status"] == "probe"
    assert metric["confidence"] < 1.0
```

- [ ] **Step 5: Verify Task 4**

Run:

```bash
pytest tests/test_jue_wiki_application.py -q
```

Expected: pass.

## Task 5: Feed Effectiveness Into Selector Ranking

**Files:**
- Modify: `src/tradecraft/services/jue_wiki_selector.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki_selector.py`

- [ ] **Step 1: Add repository helper test**

Add to `tests/test_jue_wiki_selector.py`:

```python
def test_selector_applies_bounded_effectiveness_adjustment(tmp_path: Path) -> None:
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
        content="# 삼성전자\n\n## Next Context Pack Summary\nBase.",
        source_refs=[{"source_type": "test", "source_id": "base"}],
        confidence=0.5,
        freshness="fresh",
    )
    service.write_page(
        page_id="kis.symbol.000660",
        scope="kis",
        page_type="symbol",
        title="SK하이닉스",
        symbols=["005930"],
        content="# SK하이닉스\n\n## Next Context Pack Summary\nStrong effectiveness.",
        source_refs=[{"source_type": "test", "source_id": "effect"}],
        confidence=0.5,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.000660",
            "decision_scope": "kis",
            "venue": "",
            "horizon": "",
            "sample_count": 12,
            "win_rate": 0.75,
            "expectancy": 1.2,
            "avg_return_pct": 1.2,
            "median_mae_pct": -0.2,
            "drawdown_pressure": 0.2,
            "helpful_score": 9.0,
            "confidence": 1.0,
            "status": "active",
            "reasons_json": ["effective"],
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_chars=10_000,
            effectiveness_weight=0.12,
            effectiveness_max_adjustment=8.0,
        )
    )

    selected = {page.page_id: page for page in result.pages}
    assert "effectiveness:active" in selected["kis.symbol.000660"].reasons
    assert selected["kis.symbol.000660"].score > selected["kis.symbol.005930"].score
```

- [ ] **Step 2: Run test and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_selector.py::test_selector_applies_bounded_effectiveness_adjustment -q
```

Expected: fail because selector request and helper do not support effectiveness.

- [ ] **Step 3: Add service helper**

Add to `JueWikiService`:

```python
def upsert_page_effectiveness(self, metric: dict[str, Any]) -> None:
    self.initialize()
    with self._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO wiki_page_effectiveness (
                page_id, decision_scope, venue, horizon, sample_count, win_rate,
                expectancy, avg_return_pct, median_mae_pct, drawdown_pressure,
                helpful_score, confidence, status, reasons_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(metric["page_id"]),
                str(metric.get("decision_scope") or ""),
                str(metric.get("venue") or ""),
                str(metric.get("horizon") or ""),
                int(metric.get("sample_count") or 0),
                float(metric.get("win_rate") or 0.0),
                float(metric.get("expectancy") or 0.0),
                float(metric.get("avg_return_pct") or 0.0),
                float(metric.get("median_mae_pct") or 0.0),
                float(metric.get("drawdown_pressure") or 0.0),
                float(metric.get("helpful_score") or 0.0),
                float(metric.get("confidence") or 0.0),
                str(metric.get("status") or "probe"),
                _json_dumps(metric.get("reasons_json") or []),
                _utc_now_iso(),
            ),
        )

def page_effectiveness_map(self, *, decision_scope: str) -> dict[str, dict[str, Any]]:
    self.initialize()
    with self._connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM wiki_page_effectiveness
            WHERE decision_scope = ?
            ORDER BY updated_at DESC
            """,
            (decision_scope,),
        ).fetchall()
    return {str(row["page_id"]): dict(row) for row in rows}
```

- [ ] **Step 4: Extend selector request and scoring**

In `JueWikiSelectionRequest`, add:

```python
effectiveness_weight: float = 0.0
effectiveness_max_adjustment: float = 0.0
```

In `JueWikiSelector.select()`, load:

```python
effectiveness_by_page = self.service.page_effectiveness_map(decision_scope=target_scope)
```

Pass the map and request values into `_score_page`.

Inside `_score_page`, after base source scoring:

```python
metric = effectiveness_by_page.get(str(page.get("page_id") or ""))
if metric:
    raw_adjustment = float(metric.get("helpful_score") or 0.0) * float(effectiveness_weight)
    cap = abs(float(effectiveness_max_adjustment))
    adjustment = max(min(raw_adjustment, cap), -cap) if cap else 0.0
    score += adjustment
    reasons.append(f"effectiveness:{metric.get('status') or 'unknown'}")
    reasons.append(f"effectiveness_adjustment:{adjustment:.4f}")
```

- [ ] **Step 5: Ensure low confidence metrics do not overrule relevance**

Add:

```python
def test_selector_caps_negative_effectiveness_adjustment(tmp_path: Path) -> None:
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
        content="# 삼성전자\n\n## Next Context Pack Summary\nBase.",
        source_refs=[{"source_type": "test", "source_id": "base"}],
        confidence=0.8,
        freshness="fresh",
    )
    service.upsert_page_effectiveness(
        {
            "page_id": "kis.symbol.005930",
            "decision_scope": "kis",
            "helpful_score": -100.0,
            "confidence": 1.0,
            "status": "degraded",
        }
    )

    result = JueWikiSelector(service).select(
        JueWikiSelectionRequest(
            target_scope="kis",
            symbols=["005930"],
            max_chars=10_000,
            effectiveness_weight=1.0,
            effectiveness_max_adjustment=8.0,
        )
    )

    assert result.pages
    assert "effectiveness_adjustment:-8.0000" in result.pages[0].reasons
```

- [ ] **Step 6: Verify Task 5**

Run:

```bash
pytest tests/test_jue_wiki_selector.py -q
```

Expected: pass.

## Task 6: Persist Selection Links From Managers

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/services/market_judgment.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_market_judgment.py`

- [ ] **Step 1: Add prompt metadata extraction helper tests**

Add to each manager test module a focused assertion that stored prompt metadata includes:

```python
assert run["prompt"]["jue_wiki"]["selection_run_id"]
assert run["prompt"]["jue_wiki"]["pages"][0]["page_id"]
assert run["prompt"]["jue_wiki_application"]["selection_run_id"] == run["prompt"]["jue_wiki"]["selection_run_id"]
assert run["prompt"]["jue_wiki_application"]["selected_page_ids"]
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_kis_manager_prompt_contains_jue_workflow_pack tests/test_binance_block_trader.py::test_binance_manager_prompt_contains_jue_workflow_pack tests/test_market_judgment.py::test_market_judgment_run_includes_account_and_position_first -q
```

Expected: fail because `jue_wiki_application` prompt metadata does not exist.

- [ ] **Step 3: Add local prompt metadata helper**

In each service module, add a small helper near wiki prompt helpers:

```python
def _jue_wiki_application_metadata(jue_wiki: dict[str, Any]) -> dict[str, Any]:
    pages = jue_wiki.get("pages") if isinstance(jue_wiki.get("pages"), list) else []
    return {
        "status": "ok" if jue_wiki.get("selection_run_id") else "missing",
        "selection_run_id": str(jue_wiki.get("selection_run_id") or ""),
        "prompt_mode": str(jue_wiki.get("prompt_mode") or ""),
        "selected_page_ids": [
            str(page.get("page_id") or "")
            for page in pages
            if isinstance(page, dict) and str(page.get("page_id") or "").strip()
        ],
        "budget_report": jue_wiki.get("budget_report") if isinstance(jue_wiki.get("budget_report"), dict) else {},
    }
```

- [ ] **Step 4: Attach metadata after wiki context**

After `prompt["jue_wiki"] = jue_wiki` in KIS/Binance/market judge assist/primary paths:

```python
prompt["jue_wiki_application"] = _jue_wiki_application_metadata(jue_wiki)
```

For observe mode, attach:

```python
prompt["jue_wiki_application"] = _jue_wiki_application_metadata(
    prompt["jue_wiki_selection_observation"]
)
```

- [ ] **Step 5: Verify Task 6**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_kis_manager_prompt_contains_jue_workflow_pack tests/test_binance_block_trader.py::test_binance_manager_prompt_contains_jue_workflow_pack tests/test_market_judgment.py::test_market_judgment_run_includes_account_and_position_first -q
```

Expected: pass.

## Task 7: Extend Runner With Application Projection

**Files:**
- Modify: `src/tradecraft/runtime/jue_wiki_runner.py`
- Test: `tests/test_jue_wiki_runner.py`

- [ ] **Step 1: Add failing runner test**

Add:

```python
def test_jue_wiki_runner_cycle_reports_phase3_application(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )

    result = run_once(service=service, state_path=tmp_path / "state.json")

    assert "application" in result
    assert "effectiveness" in result["application"]
    assert result["application"]["status"] in {"ok", "disabled", "error"}
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_runner.py::test_jue_wiki_runner_cycle_reports_phase3_application -q
```

Expected: fail.

- [ ] **Step 3: Add optional application step**

In `run_once()`, after `performance`:

```python
if application_enabled:
    application = _run_step(
        "application",
        lambda: {
            "status": "ok",
            "effectiveness": JueWikiApplicationService(service).project_page_effectiveness(
                min_samples=effectiveness_min_samples
            ),
        },
    )
else:
    application = {"status": "disabled", "effectiveness": {"status": "disabled"}}
```

Add optional args:

```python
application_enabled: bool = True
effectiveness_min_samples: int = 5
```

Ensure `run()` passes settings.

- [ ] **Step 4: Verify Task 7**

Run:

```bash
pytest tests/test_jue_wiki_runner.py -q
```

Expected: pass.

## Task 8: Add Wiki Application API

**Files:**
- Modify: `src/tradecraft/api/wiki.py`
- Test: `tests/test_jue_wiki_phase2_api.py`

- [ ] **Step 1: Add API protocol methods**

Extend `WikiServiceProtocol` only if needed, or instantiate `JueWikiApplicationService` inside routes when service is `JueWikiService`. Keep routes admin-protected.

- [ ] **Step 2: Write failing API test**

Add:

```python
def test_wiki_application_api_requires_admin_and_returns_status() -> None:
    service = FakeWiki(calls=[])

    with TestClient(_app(service, admin_ok=False)) as client:
        blocked = client.get("/api/wiki/application/status")

    assert blocked.status_code == 401

    with TestClient(_app(service, admin_ok=True)) as client:
        allowed = client.get("/api/wiki/application/status")

    assert allowed.status_code == 200
    assert allowed.json()["status"] in {"ok", "unavailable"}
```

- [ ] **Step 3: Add routes**

Add:

```python
@router.get("/api/wiki/application/status")
async def wiki_application_status(_: Any = Depends(deps.require_admin_auth)) -> dict[str, Any]:
    if not hasattr(service, "_connect"):
        return {"status": "unavailable", "reason": "service_does_not_support_application"}
    application = JueWikiApplicationService(service)  # type: ignore[arg-type]
    return {
        "status": "ok",
        "recent_links": application.list_decision_links(limit=20),
    }

@router.get("/api/wiki/application/effectiveness")
async def wiki_application_effectiveness(
    scope: str = "",
    _: Any = Depends(deps.require_admin_auth),
) -> dict[str, Any]:
    if not hasattr(service, "_connect"):
        return {"status": "unavailable", "pages": []}
    application = JueWikiApplicationService(service)  # type: ignore[arg-type]
    return {
        "status": "ok",
        "scope": scope,
        "pages": application.list_page_effectiveness(decision_scope=scope),
    }
```

Add `list_page_effectiveness()` to `JueWikiApplicationService`.

- [ ] **Step 4: Verify Task 8**

Run:

```bash
pytest tests/test_jue_wiki_phase2_api.py -q
```

Expected: pass.

## Task 9: Add UI Applied Intelligence Panel

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add static UI test**

Add:

```python
def test_jue_wiki_phase3_applied_intelligence_ui_exists() -> None:
    app = Path("src/tradecraft/web/static/app.js").read_text(encoding="utf-8")

    assert "/wiki/application/status" in app
    assert "/wiki/application/effectiveness" in app
    assert "renderJueWikiAppliedIntelligence" in app
    assert "wiki-effectiveness" in app
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
pytest tests/test_static_ui.py::test_jue_wiki_phase3_applied_intelligence_ui_exists -q
```

Expected: fail.

- [ ] **Step 3: Add JS state and fetchers**

In `app.js`, extend existing Jue Wiki state:

```javascript
state.jueWikiApplication = {
  status: null,
  effectiveness: null,
  loading: false,
  error: "",
};
```

Add:

```javascript
async function loadJueWikiApplication() {
  state.jueWikiApplication.loading = true;
  try {
    const [status, effectiveness] = await Promise.all([
      getJSON("/wiki/application/status"),
      getJSON("/wiki/application/effectiveness"),
    ]);
    state.jueWikiApplication.status = status;
    state.jueWikiApplication.effectiveness = effectiveness;
    state.jueWikiApplication.error = "";
  } catch (error) {
    state.jueWikiApplication.error = parseErrorMessage(error);
  } finally {
    state.jueWikiApplication.loading = false;
  }
}
```

- [ ] **Step 4: Add renderer**

Add:

```javascript
function renderJueWikiAppliedIntelligence() {
  const payload = state.jueWikiApplication.effectiveness || {};
  const pages = Array.isArray(payload.pages) ? payload.pages.slice(0, 12) : [];
  return `
    <section class="wiki-effectiveness">
      <div class="section-heading">
        <h3>Applied Intelligence</h3>
        <button class="ghost-button" data-action="refresh-jue-wiki-application">새로고침</button>
      </div>
      <div class="mini-grid">
        ${(pages.length ? pages : [{ page_id: "No effectiveness samples yet", status: "probe", helpful_score: 0 }]).map((page) => `
          <div class="mini-card">
            <strong>${escapeHtml(page.page_id || "")}</strong>
            <span>${escapeHtml(page.status || "probe")} · score ${formatNumber(page.helpful_score || 0, 2)}</span>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}
```

Wire the renderer into the existing Jue Wiki panel.

- [ ] **Step 5: Add click action**

In the shared action handler:

```javascript
if (action === "refresh-jue-wiki-application") {
  await loadJueWikiApplication();
  render();
  return;
}
```

- [ ] **Step 6: Verify Task 9**

Run:

```bash
pytest tests/test_static_ui.py::test_jue_wiki_phase3_applied_intelligence_ui_exists -q
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

## Task 10: Add Mode Recommendation Projection

**Files:**
- Modify: `src/tradecraft/services/jue_wiki_application.py`
- Test: `tests/test_jue_wiki_application.py`

- [ ] **Step 1: Write failing mode recommendation test**

Add:

```python
def test_mode_recommendations_promote_primary_only_with_enough_evidence(tmp_path: Path) -> None:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service = JueWikiApplicationService(wiki)
    for idx in range(25):
        wiki.upsert_page_effectiveness(
            {
                "page_id": f"kis.playbook.{idx}",
                "decision_scope": "kis",
                "venue": "kis",
                "horizon": "mid_term",
                "sample_count": 8,
                "win_rate": 0.75,
                "expectancy": 1.1,
                "avg_return_pct": 1.1,
                "median_mae_pct": -0.2,
                "drawdown_pressure": 0.2,
                "helpful_score": 7.0,
                "confidence": 1.0,
                "status": "active",
                "reasons_json": ["fixture"],
            }
        )

    result = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"kis": "assist"},
    )

    assert result["status"] == "ok"
    assert result["recommendations"]
    assert result["recommendations"][0]["recommended_mode"] in {"assist", "primary"}
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
pytest tests/test_jue_wiki_application.py::test_mode_recommendations_promote_primary_only_with_enough_evidence -q
```

Expected: fail.

- [ ] **Step 3: Implement conservative recommendation logic**

Add:

```python
    def project_mode_recommendations(
        self,
        *,
        min_samples: int = 20,
        current_modes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.wiki.initialize()
        metrics = self.list_page_effectiveness(limit=10_000)
        by_scope: dict[str, list[dict[str, Any]]] = {}
        for row in metrics:
            by_scope.setdefault(str(row.get("decision_scope") or ""), []).append(row)

        recommendations: list[dict[str, Any]] = []
        now = _utc_now_iso()
        with self.wiki._connect() as conn:
            for scope, rows in by_scope.items():
                sample_count = sum(int(row.get("sample_count") or 0) for row in rows)
                active_count = sum(1 for row in rows if row.get("status") == "active")
                degraded_count = sum(1 for row in rows if row.get("status") == "degraded")
                avg_helpful = (
                    sum(float(row.get("helpful_score") or 0.0) for row in rows) / len(rows)
                    if rows else 0.0
                )
                current = (current_modes or {}).get(scope, "")
                if sample_count < min_samples:
                    mode = "observe"
                    confidence = min(sample_count / max(min_samples, 1), 1.0)
                elif degraded_count > active_count:
                    mode = "observe"
                    confidence = 0.65
                elif avg_helpful > 4.0 and active_count >= degraded_count * 2:
                    mode = "primary"
                    confidence = 0.75
                else:
                    mode = "assist"
                    confidence = 0.65
                recommendation_id = f"wiki-mode:{uuid.uuid4().hex}"
                reasons = [
                    f"samples:{sample_count}",
                    f"active:{active_count}",
                    f"degraded:{degraded_count}",
                    f"avg_helpful:{avg_helpful:.4f}",
                ]
                conn.execute(
                    """
                    INSERT INTO wiki_mode_recommendations (
                        recommendation_id, decision_scope, venue, page_type,
                        horizon, recommended_mode, current_mode, sample_count,
                        confidence, reasons_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recommendation_id,
                        scope,
                        "",
                        "",
                        "",
                        mode,
                        current,
                        sample_count,
                        confidence,
                        _json_dumps(reasons),
                        now,
                    ),
                )
                recommendations.append(
                    {
                        "recommendation_id": recommendation_id,
                        "decision_scope": scope,
                        "recommended_mode": mode,
                        "current_mode": current,
                        "sample_count": sample_count,
                        "confidence": confidence,
                        "reasons": reasons,
                    }
                )
        return {"status": "ok", "recommendations": recommendations}
```

- [ ] **Step 4: Add degraded recommendation test**

Add:

```python
def test_mode_recommendations_demote_degraded_scope_to_observe(tmp_path: Path) -> None:
    wiki = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    service = JueWikiApplicationService(wiki)
    for idx in range(8):
        wiki.upsert_page_effectiveness(
            {
                "page_id": f"binance.playbook.{idx}",
                "decision_scope": "binance",
                "venue": "binance",
                "sample_count": 4,
                "helpful_score": -5.0,
                "status": "degraded",
            }
        )

    result = service.project_mode_recommendations(
        min_samples=20,
        current_modes={"binance": "assist"},
    )

    assert result["recommendations"][0]["recommended_mode"] == "observe"
```

- [ ] **Step 5: Verify Task 10**

Run:

```bash
pytest tests/test_jue_wiki_application.py -q
```

Expected: pass.

## Task 11: Ops Readiness And Docs

**Files:**
- Modify: `src/tradecraft/api/ops_payloads.py`
- Modify: `docs/spec/08_research_memory.md`
- Modify: `docs/spec/21_memory_learning_contracts.md`
- Modify: `docs/spec/11_api_reference.md`
- Modify: `docs/spec/12_config_env.md`
- Modify: `.env.example`
- Test: `tests/test_ops_payloads.py`
- Test: `tests/test_docs_spec.py`

- [ ] **Step 1: Add ops payload test**

Add:

```python
def test_build_ops_jue_wiki_payload_exposes_phase3_application_fields() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "page_count": 10,
            "application": {
                "effectiveness_count": 12,
                "degraded_count": 2,
                "latest_recommendation": {
                    "decision_scope": "kis",
                    "recommended_mode": "assist",
                },
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["application"]["effectiveness_count"] == 12
    assert payload["application"]["latest_recommendation"]["recommended_mode"] == "assist"
```

- [ ] **Step 2: Implement payload fields**

In `build_ops_jue_wiki_payload`, include:

```python
application = status.get("application") if isinstance(status.get("application"), dict) else {}
payload["application"] = {
    "effectiveness_count": _safe_int(application.get("effectiveness_count")),
    "degraded_count": _safe_int(application.get("degraded_count")),
    "latest_recommendation": application.get("latest_recommendation") if isinstance(application.get("latest_recommendation"), dict) else {},
}
```

Add warning:

```python
if payload["application"]["degraded_count"] > 10:
    warnings.append("jue_wiki_effectiveness_degraded_high")
```

- [ ] **Step 3: Add docs test**

Add to `tests/test_docs_spec.py`:

```python
def test_specs_document_jue_wiki_phase3() -> None:
    research = Path("docs/spec/08_research_memory.md").read_text(encoding="utf-8")
    memory = Path("docs/spec/21_memory_learning_contracts.md").read_text(encoding="utf-8")
    api = Path("docs/spec/11_api_reference.md").read_text(encoding="utf-8")
    env = Path("docs/spec/12_config_env.md").read_text(encoding="utf-8")

    assert "Applied Intelligence Loop" in research
    assert "wiki_decision_links" in memory
    assert "/api/wiki/application/effectiveness" in api
    assert "TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT" in env
```

- [ ] **Step 4: Update docs and env example**

Add the Phase 3 architecture and env settings to the docs listed above. Add to `.env.example`:

```bash
TRADECRAFT_JUE_WIKI_APPLICATION_ENABLED=true
TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT=0.12
TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MAX_ADJUSTMENT=8.0
TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MIN_SAMPLES=5
TRADECRAFT_JUE_WIKI_MODE_RECOMMENDATION_MIN_SAMPLES=20
```

- [ ] **Step 5: Verify Task 11**

Run:

```bash
pytest tests/test_ops_payloads.py::test_build_ops_jue_wiki_payload_exposes_phase3_application_fields tests/test_docs_spec.py::test_specs_document_jue_wiki_phase3 -q
```

Expected: pass.

## Task 12: End-To-End Verification

**Files:**
- No new source files.
- Verify all files touched by Tasks 1-11.

- [ ] **Step 1: Run focused wiki/application tests**

Run:

```bash
pytest \
  tests/test_jue_wiki.py \
  tests/test_jue_wiki_selector.py \
  tests/test_jue_wiki_application.py \
  tests/test_jue_wiki_runner.py \
  tests/test_jue_wiki_phase2_api.py \
  tests/test_manager_prompt_budget.py \
  tests/test_docs_spec.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Run manager integration tests**

Run:

```bash
pytest \
  tests/test_kis_block_trader.py::test_kis_manager_prompt_contains_jue_workflow_pack \
  tests/test_binance_block_trader.py::test_binance_manager_prompt_contains_jue_workflow_pack \
  tests/test_market_judgment.py::test_market_judgment_run_includes_account_and_position_first \
  -q
```

Expected: pass.

- [ ] **Step 3: Run ops/static checks**

Run:

```bash
pytest tests/test_ops_payloads.py tests/test_static_ui.py::test_jue_wiki_phase3_applied_intelligence_ui_exists -q
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

- [ ] **Step 4: Run whitespace and lint checks**

Run:

```bash
git diff --check -- src/tradecraft tests docs/spec .env.example
ruff check \
  src/tradecraft/services/jue_wiki.py \
  src/tradecraft/services/jue_wiki_selector.py \
  src/tradecraft/services/jue_wiki_application.py \
  src/tradecraft/runtime/jue_wiki_runner.py \
  src/tradecraft/api/wiki.py \
  src/tradecraft/api/ops_payloads.py \
  tests/test_jue_wiki_application.py
```

Expected: pass.

## Execution Notes

- Do not commit unless the user explicitly asks. This repository currently has a large dirty/untracked worktree, so stage only Phase 3 files if a commit is later requested.
- If any step discovers an existing DB schema variant not covered here, add a test before changing behavior.
- Missing optional DB files may be `ok` with zero updates. Malformed existing source tables must be `error` or explicitly skipped with a warning, never fake healthy.
- Effectiveness and mode recommendations are advisory. They may adjust context ranking and operator visibility, but they must not place orders or bypass safety gates.

## Self-Review

- Spec coverage: Phase 3 attribution, outcome projection, effectiveness scoring, selector feedback, mode recommendations, API/UI, runner, ops, and docs are mapped to tasks.
- Placeholder scan: no unresolved placeholder markers remain.
- Type consistency: table names, method names, and payload keys are introduced before later tasks use them.
- Scope check: this is one cohesive subsystem around wiki application. It intentionally does not change trading execution or exchange adapters.
