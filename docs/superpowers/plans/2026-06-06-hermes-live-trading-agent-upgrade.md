# HERMES Live Trading Agent Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade HERMES/Jue from a research-oriented trading agent into a live-trading-grade agent whose authority, sizing, and strategy behavior are governed by measured edge, execution quality, cost-aware PnL, and operational readiness.

**Architecture:** Keep the existing block-trading core intact. Add a live-trading evaluation layer that turns filled blocks, evidence packets, costs, slippage, and reflections into venue-scoped authority scorecards. Feed those scorecards back into KIS/Binance manager prompts, risk budgets, UI readiness, Telegram reports, and weekly replay without giving the LLM direct execution authority.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, static JS/CSS UI, pytest, native Codex SDK through `CodexNativeRuntime`, existing KIS/Binance adapters and block ledgers.

---

## Brainstorming Summary

The specbook already describes a solid experimental loop: research, LLM manager intent, deterministic execution gates, block ledger, reflection, memory, and policy revision. The missing live-trading-grade pieces are not more signals; they are stronger accountability and authority control.

### What Makes Jue Research-Grade Today

- Jue has many evidence sources, but not every source has proven edge.
- Memory and reflection exist, but the promotion path from reflection to live authority is still soft.
- Block records exist, but performance attribution can be contaminated by existing-position adoption, wallet adoption, operational rejects, paper fills, fees, taxes, slippage, and open unrealized PnL.
- Binance has quant/pattern/alpha layers; KIS does not yet have equivalent deterministic price/flow validation.
- UI shows operations, but it does not yet present a single "live trading grade" that tells the operator whether Jue should scale up, hold size, or shrink.

### What Makes Jue Live-Trading-Grade

- Every filled block is assigned clean realized/unrealized PnL with cost and attribution.
- Every decision is linked to evidence categories that can later be scored.
- Position sizing authority is earned by sample size, expectancy, drawdown, rule-follow rate, and execution quality.
- Bad or stale evidence reduces authority before it damages capital.
- Existing holdings and adopted wallets are managed, but excluded from Jue-created alpha.
- KIS and Binance are scored separately.
- LLM judgment remains creative, but risk budget is deterministic and measured.
- The UI makes the current live-trading grade obvious before the user deposits more money or enables larger size.

### Recommended Upgrade Approach

Use a three-layer upgrade:

1. **Truth Layer:** cost-aware, attribution-clean block performance.
2. **Edge Layer:** evidence-to-outcome and strategy family scorecards.
3. **Authority Layer:** deterministic live-trading grade, budget multiplier, and scale-up/down rules.

This is better than only adding new indicators because it makes Jue learn which indicators actually made money. It is also better than hardcoding one "value strategy" because Jue can keep multiple playbooks but must earn capital authority per playbook.

---

## File Structure

### New Files

- `src/tradecraft/services/live_performance.py`
  - Cost-aware realized/unrealized block performance normalization across KIS and Binance.
  - Attribution buckets for `llm`, `existing_position`, `wallet_adoption`, `manual`, `paper`, and `operational_failure`.
  - Venue-specific cost models.

- `src/tradecraft/services/live_edge.py`
  - Evidence-to-outcome scoring.
  - Strategy family scorecards.
  - Live authority grade calculation.

- `src/tradecraft/services/live_authority.py`
  - Deterministic budget multipliers and scale-up/down gates based on live edge scorecards.
  - Venue-scoped capital authority packets for managers.

- `src/tradecraft/runtime/live_evaluator_runner.py`
  - Periodic scorecard refresh runner.
  - Consumes block ledgers, performance reflections, memory policies, and LLM usage health.

- `tests/test_live_performance.py`
  - Unit tests for cost-aware block performance, adoption exclusion, and failed-entry classification.

- `tests/test_live_edge.py`
  - Unit tests for evidence scorecards, strategy family scoring, and confidence/sample-size gates.

- `tests/test_live_authority.py`
  - Unit tests for budget multiplier and live-trading grade behavior.

- `tests/test_live_evaluator_runner.py`
  - Runner state and idempotency tests.

### Modified Files

- `src/tradecraft/config.py`
  - Add live evaluator settings and authority thresholds.

- `pyproject.toml`
  - Add `tradecraft-live-evaluator` entrypoint.

- `src/tradecraft/main.py`
  - Add `/api/live/performance`, `/api/live/edge`, `/api/live/authority`, and readiness integration.

- `src/tradecraft/runtime/process_status.py`
  - Register `live_evaluator` process key and command pattern.

- `src/tradecraft/services/kis_block_trader.py`
  - Include live authority packet in manager prompt.
  - Record strategy family/evidence tags for each block.
  - Apply deterministic size multiplier caps.

- `src/tradecraft/services/binance_block_trader.py`
  - Include live authority packet in manager prompt.
  - Record strategy family/evidence tags for spot/futures blocks.
  - Apply venue/side-specific deterministic size multiplier caps.

- `src/tradecraft/services/investment_memory.py`
  - Ingest live scorecards in memory context and policy revision evidence.

- `src/tradecraft/web/static/app.js`
  - Render live-trading grade, authority, clean PnL, and evidence scorecards.

- `src/tradecraft/web/static/index.html`
  - Add containers for live trading grade and performance attribution.

- `src/tradecraft/web/static/style.css`
  - Add compact scorecard and live-grade UI styling.

- `docs/spec/14_observability.md`
  - Document live evaluator telemetry.

- `docs/spec/16_refactor_roadmap.md`
  - Add live-trading upgrade phase.

- `docs/spec/18_data_model_reference.md`
  - Add live evaluator DB schema.

---

## Task 1: Add Live Performance Repository

**Files:**
- Create: `src/tradecraft/services/live_performance.py`
- Create: `tests/test_live_performance.py`
- Modify: `docs/spec/18_data_model_reference.md`

- [ ] **Step 1: Write failing tests for attribution-clean block classification**

Add `tests/test_live_performance.py`:

```python
from __future__ import annotations

import pytest

from tradecraft.services.live_performance import (
    BlockPerformanceInput,
    classify_block_attribution,
    compute_realized_pnl,
)


def test_existing_position_is_managed_but_not_jue_created_alpha() -> None:
    row = BlockPerformanceInput(
        venue="kis",
        block_id="kis-1",
        symbol="005930",
        created_by="existing_position",
        status="closed",
        entry_price=70000,
        exit_price=73500,
        qty=2,
        fees=20,
        taxes=30,
        slippage=0,
        filled=True,
    )

    classified = classify_block_attribution(row)

    assert classified["attribution"] == "adopted_existing_position"
    assert classified["include_in_jue_alpha"] is False
    assert classified["include_in_risk_management"] is True


def test_llm_filled_block_realized_pnl_is_cost_aware() -> None:
    row = BlockPerformanceInput(
        venue="kis",
        block_id="kis-2",
        symbol="000660",
        created_by="llm",
        status="closed",
        entry_price=120000,
        exit_price=123000,
        qty=1,
        fees=15,
        taxes=25,
        slippage=10,
        filled=True,
    )

    pnl = compute_realized_pnl(row)

    assert pnl["gross_pnl"] == 3000
    assert pnl["net_pnl"] == 2950
    assert pnl["cost_total"] == 50
    assert pnl["include_in_jue_alpha"] is True


def test_pre_fill_operational_failure_is_not_realized_loss() -> None:
    row = BlockPerformanceInput(
        venue="binance",
        block_id="bn-1",
        symbol="LTCUSDT",
        created_by="llm",
        status="error",
        entry_price=100,
        exit_price=0,
        qty=0,
        fees=0,
        taxes=0,
        slippage=0,
        filled=False,
        error_type="exchange_filter_reject",
    )

    classified = classify_block_attribution(row)

    assert classified["attribution"] == "operational_failure_pre_fill"
    assert classified["include_in_jue_alpha"] is False
    assert classified["include_in_execution_quality"] is True
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_live_performance.py -q
```

Expected: import failure because `tradecraft.services.live_performance` does not exist.

- [ ] **Step 3: Implement minimal live performance service**

Create `src/tradecraft/services/live_performance.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class BlockPerformanceInput:
    venue: str
    block_id: str
    symbol: str
    created_by: str
    status: str
    entry_price: float
    exit_price: float
    qty: float
    fees: float = 0.0
    taxes: float = 0.0
    funding: float = 0.0
    slippage: float = 0.0
    filled: bool = False
    error_type: str = ""
    metadata: dict[str, Any] | None = None


def classify_block_attribution(row: BlockPerformanceInput) -> dict[str, Any]:
    created_by = str(row.created_by or "").strip().lower()
    status = str(row.status or "").strip().lower()
    filled = bool(row.filled)

    if created_by in {"existing_position", "wallet_adoption"}:
        return {
            "attribution": (
                "adopted_existing_position"
                if created_by == "existing_position"
                else "adopted_wallet_position"
            ),
            "include_in_jue_alpha": False,
            "include_in_risk_management": True,
            "include_in_execution_quality": False,
        }

    if status == "error" and not filled:
        return {
            "attribution": "operational_failure_pre_fill",
            "include_in_jue_alpha": False,
            "include_in_risk_management": False,
            "include_in_execution_quality": True,
        }

    return {
        "attribution": "jue_created_live_or_paper",
        "include_in_jue_alpha": created_by in {"llm", "jue", "manager"},
        "include_in_risk_management": True,
        "include_in_execution_quality": True,
    }


def compute_realized_pnl(row: BlockPerformanceInput) -> dict[str, Any]:
    classification = classify_block_attribution(row)
    gross = (float(row.exit_price) - float(row.entry_price)) * float(row.qty)
    cost_total = (
        float(row.fees)
        + float(row.taxes)
        + float(row.funding)
        + float(row.slippage)
    )
    net = gross - cost_total
    capital = abs(float(row.entry_price) * float(row.qty))
    pnl_pct = (net / capital * 100.0) if capital > 0 else 0.0
    return {
        **classification,
        "venue": row.venue,
        "block_id": row.block_id,
        "symbol": row.symbol,
        "gross_pnl": gross,
        "net_pnl": net,
        "cost_total": cost_total,
        "pnl_pct": pnl_pct,
        "filled": bool(row.filled),
    }


class LivePerformanceRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_block_performance (
                    block_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    attribution TEXT NOT NULL DEFAULT '',
                    include_in_jue_alpha INTEGER NOT NULL DEFAULT 0,
                    include_in_risk_management INTEGER NOT NULL DEFAULT 0,
                    include_in_execution_quality INTEGER NOT NULL DEFAULT 0,
                    gross_pnl REAL NOT NULL DEFAULT 0,
                    net_pnl REAL NOT NULL DEFAULT 0,
                    cost_total REAL NOT NULL DEFAULT 0,
                    pnl_pct REAL NOT NULL DEFAULT 0,
                    filled INTEGER NOT NULL DEFAULT 0,
                    source_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (venue, block_id)
                );
                CREATE INDEX IF NOT EXISTS idx_live_perf_venue_symbol
                    ON live_block_performance(venue, symbol, computed_at DESC);
                """
            )

    def upsert_performance(
        self,
        row: BlockPerformanceInput,
        *,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = compute_realized_pnl(row)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_block_performance (
                    block_id, venue, symbol, attribution,
                    include_in_jue_alpha, include_in_risk_management,
                    include_in_execution_quality, gross_pnl, net_pnl,
                    cost_total, pnl_pct, filled, source_json, computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue, block_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    attribution=excluded.attribution,
                    include_in_jue_alpha=excluded.include_in_jue_alpha,
                    include_in_risk_management=excluded.include_in_risk_management,
                    include_in_execution_quality=excluded.include_in_execution_quality,
                    gross_pnl=excluded.gross_pnl,
                    net_pnl=excluded.net_pnl,
                    cost_total=excluded.cost_total,
                    pnl_pct=excluded.pnl_pct,
                    filled=excluded.filled,
                    source_json=excluded.source_json,
                    computed_at=excluded.computed_at
                """,
                (
                    payload["block_id"],
                    payload["venue"],
                    payload["symbol"],
                    payload["attribution"],
                    int(bool(payload["include_in_jue_alpha"])),
                    int(bool(payload["include_in_risk_management"])),
                    int(bool(payload["include_in_execution_quality"])),
                    float(payload["gross_pnl"]),
                    float(payload["net_pnl"]),
                    float(payload["cost_total"]),
                    float(payload["pnl_pct"]),
                    int(bool(payload["filled"])),
                    json.dumps(source or {}, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
        return payload
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_live_performance.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Document DB in spec**

Modify `docs/spec/18_data_model_reference.md` and add:

```markdown
| `.runtime/live_performance.db` | `LivePerformanceRepository` | `live_block_performance` | Cost-aware, attribution-clean block PnL across KIS and Binance. |
```

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/live_performance.py tests/test_live_performance.py docs/spec/18_data_model_reference.md
git commit -m "feat: add live performance attribution layer"
```

---

## Task 2: Add Live Edge Scorecards

**Files:**
- Create: `src/tradecraft/services/live_edge.py`
- Create: `tests/test_live_edge.py`
- Modify: `docs/spec/18_data_model_reference.md`

- [ ] **Step 1: Write failing tests for strategy-family edge scoring**

Add `tests/test_live_edge.py`:

```python
from __future__ import annotations

from tradecraft.services.live_edge import (
    EvidenceOutcome,
    compute_edge_scorecard,
    live_grade_from_scorecard,
)


def test_scorecard_requires_sample_size_before_high_grade() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="kis",
            strategy_family="value_pullback",
            evidence_key="valuation_discount",
            net_pnl_pct=2.0,
            r_multiple=1.2,
            rule_followed=True,
        )
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["sample_count"] == 1
    assert scorecard["grade"] in {"insufficient", "watch"}
    assert scorecard["authority_multiplier"] <= 1.0


def test_positive_expectancy_with_enough_samples_gets_scaling_grade() -> None:
    outcomes = [
        EvidenceOutcome(
            venue="binance",
            strategy_family="trend_breakout",
            evidence_key="quant_momentum",
            net_pnl_pct=value,
            r_multiple=r,
            rule_followed=True,
        )
        for value, r in [(1.1, 0.8), (0.9, 0.7), (-0.3, -0.2), (1.5, 1.0), (0.4, 0.3)]
    ]

    scorecard = compute_edge_scorecard(outcomes, min_samples_for_grade=5)

    assert scorecard["sample_count"] == 5
    assert scorecard["expectancy_pct"] > 0
    assert scorecard["grade"] in {"qualified", "scale_candidate"}
    assert scorecard["authority_multiplier"] >= 1.0


def test_live_grade_penalizes_drawdown_and_bad_rule_following() -> None:
    scorecard = {
        "sample_count": 20,
        "expectancy_pct": 0.5,
        "win_rate": 55.0,
        "max_drawdown_pct": -8.0,
        "rule_follow_rate": 45.0,
        "execution_error_rate": 20.0,
    }

    grade = live_grade_from_scorecard(scorecard)

    assert grade["grade"] in {"restricted", "observe_only"}
    assert grade["authority_multiplier"] < 1.0
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_live_edge.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement edge scoring**

Create `src/tradecraft/services/live_edge.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class EvidenceOutcome:
    venue: str
    strategy_family: str
    evidence_key: str
    net_pnl_pct: float
    r_multiple: float
    rule_followed: bool
    execution_error: bool = False


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_edge_scorecard(
    outcomes: list[EvidenceOutcome],
    *,
    min_samples_for_grade: int = 5,
) -> dict[str, Any]:
    sample_count = len(outcomes)
    pnl_values = [float(item.net_pnl_pct) for item in outcomes]
    wins = [item for item in outcomes if item.net_pnl_pct > 0]
    rule_follow = [item for item in outcomes if item.rule_followed]
    execution_errors = [item for item in outcomes if item.execution_error]
    expectancy = _avg(pnl_values)
    win_rate = (len(wins) / sample_count * 100.0) if sample_count else 0.0
    rule_follow_rate = (len(rule_follow) / sample_count * 100.0) if sample_count else 0.0
    execution_error_rate = (
        len(execution_errors) / sample_count * 100.0 if sample_count else 0.0
    )
    max_drawdown_pct = min(pnl_values) if pnl_values else 0.0

    base = {
        "sample_count": sample_count,
        "expectancy_pct": expectancy,
        "win_rate": win_rate,
        "rule_follow_rate": rule_follow_rate,
        "execution_error_rate": execution_error_rate,
        "max_drawdown_pct": max_drawdown_pct,
    }
    grade = live_grade_from_scorecard(
        base,
        min_samples_for_grade=min_samples_for_grade,
    )
    return {**base, **grade}


def live_grade_from_scorecard(
    scorecard: dict[str, Any],
    *,
    min_samples_for_grade: int = 5,
) -> dict[str, Any]:
    sample_count = int(scorecard.get("sample_count") or 0)
    expectancy = float(scorecard.get("expectancy_pct") or 0.0)
    win_rate = float(scorecard.get("win_rate") or 0.0)
    drawdown = float(scorecard.get("max_drawdown_pct") or 0.0)
    rule_follow = float(scorecard.get("rule_follow_rate") or 0.0)
    execution_errors = float(scorecard.get("execution_error_rate") or 0.0)

    if sample_count < min_samples_for_grade:
        return {"grade": "insufficient", "authority_multiplier": 0.75}
    if execution_errors >= 15.0 or rule_follow < 60.0 or drawdown <= -7.0:
        return {"grade": "restricted", "authority_multiplier": 0.5}
    if expectancy > 0.4 and win_rate >= 52.0 and rule_follow >= 80.0:
        return {"grade": "scale_candidate", "authority_multiplier": 1.25}
    if expectancy > 0.0 and win_rate >= 48.0:
        return {"grade": "qualified", "authority_multiplier": 1.0}
    return {"grade": "observe_only", "authority_multiplier": 0.5}


class LiveEdgeRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_edge_scorecards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue TEXT NOT NULL,
                    strategy_family TEXT NOT NULL,
                    evidence_key TEXT NOT NULL DEFAULT '',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    expectancy_pct REAL NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    rule_follow_rate REAL NOT NULL DEFAULT 0,
                    execution_error_rate REAL NOT NULL DEFAULT 0,
                    max_drawdown_pct REAL NOT NULL DEFAULT 0,
                    grade TEXT NOT NULL DEFAULT 'insufficient',
                    authority_multiplier REAL NOT NULL DEFAULT 1,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TEXT NOT NULL,
                    UNIQUE(venue, strategy_family, evidence_key)
                );
                CREATE INDEX IF NOT EXISTS idx_live_edge_venue_grade
                    ON live_edge_scorecards(venue, grade, computed_at DESC);
                """
            )

    def upsert_scorecard(
        self,
        *,
        venue: str,
        strategy_family: str,
        evidence_key: str,
        scorecard: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_edge_scorecards (
                    venue, strategy_family, evidence_key, sample_count,
                    expectancy_pct, win_rate, rule_follow_rate,
                    execution_error_rate, max_drawdown_pct, grade,
                    authority_multiplier, raw_json, computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue, strategy_family, evidence_key) DO UPDATE SET
                    sample_count=excluded.sample_count,
                    expectancy_pct=excluded.expectancy_pct,
                    win_rate=excluded.win_rate,
                    rule_follow_rate=excluded.rule_follow_rate,
                    execution_error_rate=excluded.execution_error_rate,
                    max_drawdown_pct=excluded.max_drawdown_pct,
                    grade=excluded.grade,
                    authority_multiplier=excluded.authority_multiplier,
                    raw_json=excluded.raw_json,
                    computed_at=excluded.computed_at
                """,
                (
                    venue,
                    strategy_family,
                    evidence_key,
                    int(scorecard.get("sample_count") or 0),
                    float(scorecard.get("expectancy_pct") or 0.0),
                    float(scorecard.get("win_rate") or 0.0),
                    float(scorecard.get("rule_follow_rate") or 0.0),
                    float(scorecard.get("execution_error_rate") or 0.0),
                    float(scorecard.get("max_drawdown_pct") or 0.0),
                    str(scorecard.get("grade") or "insufficient"),
                    float(scorecard.get("authority_multiplier") or 1.0),
                    json.dumps(scorecard, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
```

- [ ] **Step 4: Run focused tests**

```bash
pytest tests/test_live_edge.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Document DB in spec**

Add to `docs/spec/18_data_model_reference.md`:

```markdown
| `.runtime/live_edge.db` | `LiveEdgeRepository` | `live_edge_scorecards` | Evidence-to-outcome and strategy-family scorecards that control live authority. |
```

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/live_edge.py tests/test_live_edge.py docs/spec/18_data_model_reference.md
git commit -m "feat: add live edge scorecards"
```

---

## Task 3: Add Deterministic Live Authority Controller

**Files:**
- Create: `src/tradecraft/services/live_authority.py`
- Create: `tests/test_live_authority.py`
- Modify: `src/tradecraft/config.py`

- [ ] **Step 1: Write failing tests for authority packet calculation**

Add `tests/test_live_authority.py`:

```python
from __future__ import annotations

from tradecraft.services.live_authority import (
    LiveAuthorityConfig,
    build_authority_packet,
)


def test_restricted_grade_caps_budget_even_if_llm_is_confident() -> None:
    packet = build_authority_packet(
        venue="binance",
        scorecards=[
            {
                "strategy_family": "futures_momentum",
                "grade": "restricted",
                "authority_multiplier": 0.5,
                "sample_count": 20,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert packet["venue"] == "binance"
    assert packet["live_grade"] == "restricted"
    assert packet["max_budget_multiplier"] == 0.5
    assert packet["allow_scale_up"] is False


def test_scale_candidate_can_increase_budget_with_sample_size() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[
            {
                "strategy_family": "value_pullback",
                "grade": "scale_candidate",
                "authority_multiplier": 1.25,
                "sample_count": 15,
            }
        ],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0, max_scale_multiplier=1.5),
    )

    assert packet["live_grade"] == "scale_candidate"
    assert packet["max_budget_multiplier"] == 1.25
    assert packet["allow_scale_up"] is True


def test_no_scorecards_defaults_to_observe_only() -> None:
    packet = build_authority_packet(
        venue="kis",
        scorecards=[],
        config=LiveAuthorityConfig(base_budget_multiplier=1.0),
    )

    assert packet["live_grade"] == "observe_only"
    assert packet["max_budget_multiplier"] < 1.0
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_live_authority.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement authority controller**

Create `src/tradecraft/services/live_authority.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


GRADE_RANK = {
    "observe_only": 0,
    "insufficient": 1,
    "restricted": 2,
    "qualified": 3,
    "scale_candidate": 4,
}


@dataclass(frozen=True, slots=True)
class LiveAuthorityConfig:
    base_budget_multiplier: float = 1.0
    max_scale_multiplier: float = 1.5
    observe_only_multiplier: float = 0.5
    min_samples_to_scale: int = 10


def _lowest_effective_grade(scorecards: list[dict[str, Any]]) -> str:
    if not scorecards:
        return "observe_only"
    grades = [str(row.get("grade") or "observe_only") for row in scorecards]
    if any(grade in {"restricted", "observe_only"} for grade in grades):
        return "restricted" if "restricted" in grades else "observe_only"
    return max(grades, key=lambda grade: GRADE_RANK.get(grade, 0))


def build_authority_packet(
    *,
    venue: str,
    scorecards: list[dict[str, Any]],
    config: LiveAuthorityConfig,
) -> dict[str, Any]:
    grade = _lowest_effective_grade(scorecards)
    multipliers = [
        float(row.get("authority_multiplier") or 1.0)
        for row in scorecards
    ]
    if not multipliers:
        multiplier = float(config.observe_only_multiplier)
    elif grade in {"restricted", "observe_only"}:
        multiplier = min(multipliers)
    elif grade == "scale_candidate":
        enough_samples = any(
            int(row.get("sample_count") or 0) >= config.min_samples_to_scale
            for row in scorecards
        )
        multiplier = min(max(multipliers), config.max_scale_multiplier)
        if not enough_samples:
            multiplier = min(multiplier, 1.0)
    else:
        multiplier = min(max(multipliers), 1.0)

    return {
        "venue": venue,
        "live_grade": grade,
        "max_budget_multiplier": round(
            float(config.base_budget_multiplier) * float(multiplier),
            4,
        ),
        "allow_scale_up": grade == "scale_candidate" and multiplier > 1.0,
        "scorecard_count": len(scorecards),
        "scorecards": scorecards,
    }
```

- [ ] **Step 4: Run focused tests**

```bash
pytest tests/test_live_authority.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Add settings**

Modify `src/tradecraft/config.py`:

```python
live_evaluator_enabled: bool = Field(
    default=True,
    alias="TRADECRAFT_LIVE_EVALUATOR_ENABLED",
)
live_evaluator_db_path: str = Field(
    default=".runtime/live_edge.db",
    alias="TRADECRAFT_LIVE_EVALUATOR_DB_PATH",
)
live_performance_db_path: str = Field(
    default=".runtime/live_performance.db",
    alias="TRADECRAFT_LIVE_PERFORMANCE_DB_PATH",
)
live_evaluator_interval_sec: int = Field(
    default=300,
    alias="TRADECRAFT_LIVE_EVALUATOR_INTERVAL_SEC",
)
live_authority_max_scale_multiplier: float = Field(
    default=1.5,
    alias="TRADECRAFT_LIVE_AUTHORITY_MAX_SCALE_MULTIPLIER",
)
live_authority_min_samples_to_scale: int = Field(
    default=10,
    alias="TRADECRAFT_LIVE_AUTHORITY_MIN_SAMPLES_TO_SCALE",
)
```

- [ ] **Step 6: Add config tests**

Modify `tests/test_config.py`:

```python
def test_live_authority_defaults() -> None:
    from tradecraft.config import AppSettings

    settings = AppSettings()

    assert settings.live_evaluator_enabled is True
    assert settings.live_evaluator_interval_sec == 300
    assert settings.live_authority_max_scale_multiplier == 1.5
```

- [ ] **Step 7: Run focused tests**

```bash
pytest tests/test_live_authority.py tests/test_config.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/tradecraft/services/live_authority.py src/tradecraft/config.py tests/test_live_authority.py tests/test_config.py
git commit -m "feat: add live authority controller"
```

---

## Task 4: Build Live Evaluator Runner

**Files:**
- Create: `src/tradecraft/runtime/live_evaluator_runner.py`
- Create: `tests/test_live_evaluator_runner.py`
- Modify: `pyproject.toml`
- Modify: `src/tradecraft/runtime/process_status.py`

- [ ] **Step 1: Write runner test**

Add `tests/test_live_evaluator_runner.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from tradecraft.runtime.live_evaluator_runner import run_live_evaluator_once


def test_live_evaluator_writes_state(tmp_path: Path) -> None:
    state_path = tmp_path / "live_evaluator.json"

    class Settings:
        live_evaluator_enabled = True
        live_evaluator_db_path = str(tmp_path / "live_edge.db")
        live_performance_db_path = str(tmp_path / "live_performance.db")
        live_evaluator_state_path = str(state_path)

    result = asyncio.run(run_live_evaluator_once(Settings()))

    assert result["status"] == "ok"
    assert state_path.exists()
    assert "authority" in result
```

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_live_evaluator_runner.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement runner skeleton**

Create `src/tradecraft/runtime/live_evaluator_runner.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.live_authority import (
    LiveAuthorityConfig,
    build_authority_packet,
)
from tradecraft.services.live_edge import LiveEdgeRepository
from tradecraft.services.live_performance import LivePerformanceRepository

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_live_evaluator_once(settings: Any) -> dict[str, Any]:
    edge_repo = LiveEdgeRepository(settings.live_evaluator_db_path)
    LivePerformanceRepository(settings.live_performance_db_path)
    authority = {
        "kis": build_authority_packet(
            venue="kis",
            scorecards=[],
            config=LiveAuthorityConfig(),
        ),
        "binance": build_authority_packet(
            venue="binance",
            scorecards=[],
            config=LiveAuthorityConfig(),
        ),
    }
    result = {
        "service": "tradecraft-live-evaluator",
        "status": "ok",
        "ran_at": _now(),
        "edge_db_path": str(edge_repo.path),
        "authority": authority,
    }
    RuntimeStateStore(settings.live_evaluator_state_path).write_snapshot(result)
    return result


async def run_live_evaluator_loop(settings: AppSettings | None = None) -> None:
    settings = settings or AppSettings()
    interval = max(int(settings.live_evaluator_interval_sec), 30)
    while True:
        try:
            if bool(settings.live_evaluator_enabled):
                await run_live_evaluator_once(settings)
        except Exception:
            logger.exception("live evaluator cycle failed")
        await asyncio.sleep(interval)


def run() -> None:
    asyncio.run(run_live_evaluator_loop())
```

- [ ] **Step 4: Add missing state setting**

Modify `src/tradecraft/config.py`:

```python
live_evaluator_state_path: str = Field(
    default=".runtime/live_evaluator.json",
    alias="TRADECRAFT_LIVE_EVALUATOR_STATE_PATH",
)
```

- [ ] **Step 5: Add entrypoint**

Modify `pyproject.toml`:

```toml
tradecraft-live-evaluator = "tradecraft.runtime.live_evaluator_runner:run"
```

- [ ] **Step 6: Register process status**

Modify `src/tradecraft/runtime/process_status.py` to include a `live_evaluator`
process definition with:

```python
"live_evaluator": ProcessSpec(
    key="live_evaluator",
    label="Live Evaluator",
    command_pattern="tradecraft-live-evaluator",
    pid_file=".runtime/live_evaluator.pid",
)
```

Use the exact local `ProcessSpec` or equivalent pattern already present in the
file.

- [ ] **Step 7: Run focused tests**

```bash
pytest tests/test_live_evaluator_runner.py tests/test_process_status.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/tradecraft/runtime/live_evaluator_runner.py src/tradecraft/config.py pyproject.toml src/tradecraft/runtime/process_status.py tests/test_live_evaluator_runner.py
git commit -m "feat: add live evaluator runner"
```

---

## Task 5: Add Live Authority API And Readiness

**Files:**
- Modify: `src/tradecraft/main.py`
- Modify: `tests/test_api_smoke.py`
- Modify: `tests/test_admin_auth.py`
- Modify: `docs/spec/11_api_reference.md`
- Modify: `docs/spec/14_observability.md`

- [ ] **Step 1: Write API tests**

Modify `tests/test_api_smoke.py`:

```python
def test_live_authority_endpoint_requires_admin(client):
    response = client.get("/api/live/authority")
    assert response.status_code in {401, 403}


def test_live_authority_endpoint_with_admin(client, monkeypatch):
    monkeypatch.setenv("TRADECRAFT_ADMIN_TOKEN", "test-token")
    response = client.get(
        "/api/live/authority",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "venues" in payload
```

Adjust fixture names to match the local `TestClient` pattern if necessary.

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_api_smoke.py::test_live_authority_endpoint_requires_admin tests/test_api_smoke.py::test_live_authority_endpoint_with_admin -q
```

Expected: 404 or auth mismatch until route exists.

- [ ] **Step 3: Add API route**

Modify `src/tradecraft/main.py`:

```python
@app.get("/api/live/authority")
async def live_authority(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return {
        "status": "ok",
        "venues": {
            "kis": build_authority_packet(
                venue="kis",
                scorecards=[],
                config=LiveAuthorityConfig(
                    max_scale_multiplier=settings.live_authority_max_scale_multiplier,
                    min_samples_to_scale=settings.live_authority_min_samples_to_scale,
                ),
            ),
            "binance": build_authority_packet(
                venue="binance",
                scorecards=[],
                config=LiveAuthorityConfig(
                    max_scale_multiplier=settings.live_authority_max_scale_multiplier,
                    min_samples_to_scale=settings.live_authority_min_samples_to_scale,
                ),
            ),
        },
    }
```

Add imports:

```python
from tradecraft.services.live_authority import LiveAuthorityConfig, build_authority_packet
```

Later tasks can replace empty scorecards with repository-backed scorecards.

- [ ] **Step 4: Add readiness field**

Modify `_build_ops_readiness()` in `src/tradecraft/main.py` so the payload
includes:

```python
"live_evaluator": {
    "enabled": bool(settings.live_evaluator_enabled),
    "state_path": settings.live_evaluator_state_path,
    "authority_endpoint": "/api/live/authority",
}
```

- [ ] **Step 5: Document API**

Add to `docs/spec/11_api_reference.md`:

```markdown
## Live Trading Evaluation

- `GET /api/live/authority` returns KIS and Binance live-trading authority packets. Admin auth required.
```

Add to `docs/spec/14_observability.md`:

```markdown
Live evaluator readiness is visible through `/api/ops/readiness` and the authority packet is visible through `/api/live/authority`.
```

- [ ] **Step 6: Run focused tests**

```bash
pytest tests/test_api_smoke.py tests/test_admin_auth.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/main.py tests/test_api_smoke.py tests/test_admin_auth.py docs/spec/11_api_reference.md docs/spec/14_observability.md
git commit -m "feat: expose live authority API"
```

---

## Task 6: Feed Authority Packets Into KIS And Binance Managers

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `tests/test_kis_block_trader.py`
- Modify: `tests/test_binance_block_trader.py`
- Modify: `docs/spec/06_kis_ju.md`
- Modify: `docs/spec/07_binance_ju.md`

- [ ] **Step 1: Add tests that manager prompt includes live authority**

Modify `tests/test_kis_block_trader.py`:

```python
def test_kis_manager_prompt_includes_live_authority(fake_kis_trader):
    prompt = fake_kis_trader._build_manager_prompt(
        account={"cash_krw": 1000000},
        blocks=[],
        quotes={},
        extra_context={"live_authority": {"live_grade": "restricted"}},
    )

    assert "live_authority" in prompt
    assert "restricted" in prompt
```

Modify to match the actual prompt builder helper. If no public helper exists,
test the serialized `manager_runs.prompt_json` after a fake manager run.

Modify `tests/test_binance_block_trader.py`:

```python
def test_binance_manager_prompt_includes_live_authority(fake_binance_trader):
    prompt = fake_binance_trader._build_manager_prompt(
        account={"usdt": 100},
        blocks=[],
        quotes={},
        extra_context={"live_authority": {"live_grade": "observe_only"}},
    )

    assert "live_authority" in prompt
    assert "observe_only" in prompt
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_kis_block_trader.py tests/test_binance_block_trader.py -q
```

Expected: new assertions fail until prompt includes authority.

- [ ] **Step 3: Add authority provider dependency**

In both trader constructors, add an optional dependency:

```python
live_authority_provider: Callable[[str], dict[str, Any]] | None = None
```

Store as:

```python
self.live_authority_provider = live_authority_provider
```

Add helper:

```python
def _live_authority_context(self, venue: str) -> dict[str, Any]:
    if self.live_authority_provider is None:
        return {"status": "missing", "venue": venue}
    try:
        payload = self.live_authority_provider(venue)
    except Exception as exc:
        return {"status": "error", "venue": venue, "error_message": str(exc)}
    return payload if isinstance(payload, dict) else {"status": "invalid", "venue": venue}
```

- [ ] **Step 4: Add prompt field**

In KIS manager prompt payload:

```python
"live_authority": self._live_authority_context("kis"),
```

In Binance manager prompt payload:

```python
"live_authority": self._live_authority_context("binance"),
```

- [ ] **Step 5: Apply deterministic size cap**

When accepting LLM-created block quantity or quote budget, cap by:

```python
authority_multiplier = float(
    live_authority.get("max_budget_multiplier") or 1.0
)
```

For KIS, multiply candidate budget by `authority_multiplier` before converting
to share quantity. For Binance, multiply spot/futures quote budget by
`authority_multiplier` after venue-specific min/max budget checks and before
exchange filter normalization.

If the multiplier reduces size below minimum executable size, record a rejected
action:

```python
"reason": "live_authority_budget_below_minimum"
```

- [ ] **Step 6: Update docs**

Add to KIS/Binance spec:

```markdown
Live authority packets are prompt inputs and deterministic sizing caps. They do not create orders by themselves.
```

- [ ] **Step 7: Run focused tests**

```bash
pytest tests/test_kis_block_trader.py tests/test_binance_block_trader.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/tradecraft/services/kis_block_trader.py src/tradecraft/services/binance_block_trader.py tests/test_kis_block_trader.py tests/test_binance_block_trader.py docs/spec/06_kis_ju.md docs/spec/07_binance_ju.md
git commit -m "feat: feed live authority into Jue managers"
```

---

## Task 7: Add Evidence Tags To New Blocks

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `tests/test_kis_block_trader.py`
- Modify: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Add tests for strategy/evidence metadata**

KIS test expectation:

```python
def test_kis_created_block_persists_strategy_family_and_evidence_tags(fake_kis_trader):
    block = fake_kis_trader.create_test_block(
        symbol="005930",
        metadata={
            "strategy_family": "value_pullback",
            "evidence_tags": ["valuation_discount", "market_pulse_support"],
        },
    )

    assert block["metadata"]["strategy_family"] == "value_pullback"
    assert "valuation_discount" in block["metadata"]["evidence_tags"]
```

Binance test expectation:

```python
def test_binance_created_block_persists_strategy_family_and_evidence_tags(fake_binance_trader):
    block = fake_binance_trader.create_test_block(
        symbol="BTCUSDT",
        market="futures",
        metadata={
            "strategy_family": "trend_breakout",
            "evidence_tags": ["quant_momentum", "alpha_event_support"],
        },
    )

    assert block["metadata"]["strategy_family"] == "trend_breakout"
    assert "quant_momentum" in block["metadata"]["evidence_tags"]
```

Adapt helper names to actual local fixtures.

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_kis_block_trader.py tests/test_binance_block_trader.py -q
```

Expected: tests fail until metadata is normalized.

- [ ] **Step 3: Normalize action metadata**

When parsing LLM action JSON, ensure metadata contains:

```python
strategy_family = str(
    action.get("strategy_family")
    or metadata.get("strategy_family")
    or "unclassified"
).strip()

evidence_tags = action.get("evidence_tags") or metadata.get("evidence_tags") or []
if not isinstance(evidence_tags, list):
    evidence_tags = [str(evidence_tags)]
metadata["strategy_family"] = strategy_family
metadata["evidence_tags"] = [str(item).strip() for item in evidence_tags if str(item).strip()]
```

- [ ] **Step 4: Add prompt instruction**

In KIS and Binance manager prompts, add:

```text
For every create_blocks action, provide strategy_family and evidence_tags.
strategy_family should be one compact family such as value_pullback, sector_rotation, etf_core, trend_breakout, mean_reversion, event_alpha, risk_rebalance, or unclassified.
evidence_tags should list the concrete evidence classes used.
```

- [ ] **Step 5: Run focused tests**

```bash
pytest tests/test_kis_block_trader.py tests/test_binance_block_trader.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/kis_block_trader.py src/tradecraft/services/binance_block_trader.py tests/test_kis_block_trader.py tests/test_binance_block_trader.py
git commit -m "feat: persist block evidence tags"
```

---

## Task 8: Upgrade UI With Live Trading Grade

**Files:**
- Modify: `src/tradecraft/web/static/index.html`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Modify: `tests/test_static_ui.py`
- Modify: `docs/spec/22_ui_state_contracts.md`

- [ ] **Step 1: Add static UI test**

Modify `tests/test_static_ui.py`:

```python
def test_static_ui_contains_live_trading_grade_panel() -> None:
    html = (ROOT / "src/tradecraft/web/static/index.html").read_text()
    js = (ROOT / "src/tradecraft/web/static/app.js").read_text()

    assert "live-trading-grade" in html
    assert "/api/live/authority" in js
    assert "renderLiveAuthority" in js
```

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_static_ui.py::test_static_ui_contains_live_trading_grade_panel -q
```

Expected: failure until UI is added.

- [ ] **Step 3: Add HTML container**

In `index.html`, add:

```html
<section id="live-trading-grade" class="ops-band live-grade-panel">
  <div class="section-header">
    <h2>Live Trading Grade</h2>
    <span id="live-grade-updated" class="muted"></span>
  </div>
  <div id="live-authority-content" class="live-authority-grid"></div>
</section>
```

- [ ] **Step 4: Add JS state and fetch**

In `app.js`:

```javascript
state.liveAuthority = null;

async function loadLiveAuthority() {
  const payload = await apiFetch("/api/live/authority");
  state.liveAuthority = payload;
  renderLiveAuthority();
}

function renderLiveAuthority() {
  const root = document.getElementById("live-authority-content");
  if (!root) return;
  const venues = (state.liveAuthority && state.liveAuthority.venues) || {};
  root.innerHTML = ["kis", "binance"].map((venue) => {
    const item = venues[venue] || {};
    const grade = item.live_grade || "missing";
    const multiplier = item.max_budget_multiplier ?? "-";
    return `
      <article class="live-authority-card">
        <div class="label">${venue.toUpperCase()}</div>
        <strong>${grade}</strong>
        <span>budget x ${multiplier}</span>
      </article>
    `;
  }).join("");
}
```

Call `loadLiveAuthority()` in the existing dashboard/ops refresh path.

- [ ] **Step 5: Add CSS**

In `style.css`:

```css
.live-authority-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.live-authority-card {
  border: 1px solid var(--border-subtle);
  background: var(--surface-1);
  border-radius: 8px;
  padding: 12px;
}
```

Use existing local token names if they differ.

- [ ] **Step 6: Run UI checks**

```bash
pytest tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/web/static/index.html src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css tests/test_static_ui.py docs/spec/22_ui_state_contracts.md
git commit -m "feat: show live trading grade in UI"
```

---

## Task 9: Add Telegram Live Trading Reports

**Files:**
- Modify: `src/tradecraft/services/telegram_cli.py`
- Modify: `tests/test_telegram_cli.py`
- Modify: `docs/spec/23_operations_runbook.md`

- [ ] **Step 1: Add Telegram command tests**

Modify `tests/test_telegram_cli.py`:

```python
def test_live_command_summarizes_authority() -> None:
    text = render_live_authority_message(
        {
            "venues": {
                "kis": {"live_grade": "qualified", "max_budget_multiplier": 1.0},
                "binance": {"live_grade": "restricted", "max_budget_multiplier": 0.5},
            }
        }
    )

    assert "KIS" in text
    assert "qualified" in text
    assert "Binance" in text
    assert "restricted" in text
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_telegram_cli.py::test_live_command_summarizes_authority -q
```

Expected: helper missing.

- [ ] **Step 3: Implement `/live` command renderer**

Add to `telegram_cli.py`:

```python
def render_live_authority_message(payload: dict[str, Any]) -> str:
    venues = payload.get("venues") if isinstance(payload.get("venues"), dict) else {}
    lines = ["쥬 실전 운용 등급"]
    for key, label in (("kis", "KIS"), ("binance", "Binance")):
        item = venues.get(key) if isinstance(venues.get(key), dict) else {}
        grade = item.get("live_grade") or "missing"
        multiplier = item.get("max_budget_multiplier", "-")
        lines.append(f"- {label}: {grade} · budget x {multiplier}")
    return "\n".join(lines)
```

Wire `/live` to call `/api/live/authority` or the local service path following
the existing command architecture.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_telegram_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/telegram_cli.py tests/test_telegram_cli.py docs/spec/23_operations_runbook.md
git commit -m "feat: add live authority telegram command"
```

---

## Task 10: Add Weekly Live-Trading Review Gate

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Modify: `tests/test_investment_memory.py`
- Modify: `docs/spec/21_memory_learning_contracts.md`

- [ ] **Step 1: Add memory test for live scorecard inclusion**

Modify `tests/test_investment_memory.py`:

```python
def test_context_pack_includes_live_authority_scorecards(memory_service):
    context = memory_service.context_pack(
        target_scope="kis",
        source_scope="kis",
        symbols=["005930"],
        blocks=[],
        extra_context={
            "live_authority": {
                "live_grade": "qualified",
                "max_budget_multiplier": 1.0,
            }
        },
    )

    assert "live_authority" in context
    assert context["live_authority"]["live_grade"] == "qualified"
```

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_live_authority_scorecards -q
```

Expected: failure if extra live authority is not preserved.

- [ ] **Step 3: Extend context pack**

Modify `InvestmentMemoryService.context_pack()` so it accepts and compacts
`extra_context.live_authority`:

```python
live_authority = {}
if isinstance(extra_context, dict) and isinstance(extra_context.get("live_authority"), dict):
    live_authority = self._compact_mapping(extra_context["live_authority"], max_chars=1200)
payload["live_authority"] = live_authority
```

Use local compaction helpers instead of adding a new one if they already exist.

- [ ] **Step 4: Add review prompt requirement**

In weekly/monthly review generation, add live scorecard evidence to the context
payload and require review output to answer:

```text
Did any strategy family earn more authority, lose authority, or remain observation-only?
```

- [ ] **Step 5: Run focused tests**

```bash
pytest tests/test_investment_memory.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/investment_memory.py tests/test_investment_memory.py docs/spec/21_memory_learning_contracts.md
git commit -m "feat: connect live authority to memory reviews"
```

---

## Task 11: Add Live Trading Spec Updates

**Files:**
- Modify: `docs/spec/14_observability.md`
- Modify: `docs/spec/15_known_gaps.md`
- Modify: `docs/spec/16_refactor_roadmap.md`
- Modify: `docs/spec/18_data_model_reference.md`
- Modify: `docs/spec/19_trading_execution_contracts.md`
- Modify: `docs/spec/21_memory_learning_contracts.md`

- [ ] **Step 1: Update observability spec**

Add:

```markdown
## Live Trading Grade

Live trading grade is computed from cost-aware block performance, evidence scorecards, execution quality, and rule-follow rate. It is an operations control surface, not an LLM opinion.
```

- [ ] **Step 2: Update known gaps**

Move "performance interpretation" caveats into resolved/active work when tasks
1-10 are complete. Keep remaining caveats around sample size and exact broker
fee/tax reconciliation if still unverified.

- [ ] **Step 3: Update roadmap**

Add a phase:

```markdown
## Phase 6: Live Trading Grade

Jue earns or loses trading authority by measured edge. Scaling is deterministic and venue-scoped.
```

- [ ] **Step 4: Update trading execution contract**

Add:

```markdown
Managers can propose size, but live authority caps final accepted size.
```

- [ ] **Step 5: Run docs tests**

```bash
pytest tests/test_docs_spec.py -q
git diff --check -- docs/spec docs/superpowers/plans
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add docs/spec docs/superpowers/plans/2026-06-06-hermes-live-trading-agent-upgrade.md
git commit -m "docs: add live trading agent upgrade plan"
```

---

## Task 12: Full Verification And Rollout

**Files:**
- No new source files unless failures require fixes.

- [ ] **Step 1: Run focused new tests**

```bash
pytest tests/test_live_performance.py tests/test_live_edge.py tests/test_live_authority.py tests/test_live_evaluator_runner.py -q
```

Expected: all pass.

- [ ] **Step 2: Run affected trading tests**

```bash
pytest tests/test_kis_block_trader.py tests/test_kis_block_trader_runner.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py -q
```

Expected: all pass.

- [ ] **Step 3: Run affected API/UI tests**

```bash
pytest tests/test_api_smoke.py tests/test_admin_auth.py tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
```

Expected: all pass.

- [ ] **Step 4: Run memory/research regression**

```bash
pytest tests/test_investment_memory.py tests/test_jue_research_spine.py tests/test_strategy_intelligence.py -q
```

Expected: all pass.

- [ ] **Step 5: Run docs/spec regression**

```bash
pytest tests/test_docs_spec.py tests/test_jue_workflow_manifests.py tests/test_jue_skill_registry.py -q
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Manual runtime smoke**

Start or restart:

```bash
tradecraft-control
tradecraft-live-evaluator
tradecraft-kis-block-trader
tradecraft-binance-block-trader
tradecraft-investment-memory
```

Check:

```http
GET /api/ops/readiness
GET /api/live/authority
GET /api/llm/usage/status
```

Expected:

- readiness includes `live_evaluator`;
- `/api/live/authority` returns KIS and Binance packets;
- UI displays live trading grade;
- manager runs include `live_authority` in prompt payload;
- no live execution setting is changed by this rollout.

- [ ] **Step 7: Rollout policy**

Initial production rollout must use conservative authority:

```env
TRADECRAFT_LIVE_AUTHORITY_MAX_SCALE_MULTIPLIER=1.0
TRADECRAFT_LIVE_AUTHORITY_MIN_SAMPLES_TO_SCALE=10
```

After at least 10 clean Jue-created filled blocks per venue/strategy family,
raise:

```env
TRADECRAFT_LIVE_AUTHORITY_MAX_SCALE_MULTIPLIER=1.25
```

Only raise above `1.25` after:

- adoption-excluded realized PnL is positive;
- execution error rate is below threshold;
- rule-follow rate is acceptable;
- worst drawdown is acceptable;
- weekly review agrees with scorecard evidence.

- [ ] **Step 8: Final commit**

```bash
git status --short
git add src tests docs pyproject.toml
git commit -m "feat: upgrade Jue live trading authority"
```

---

## Success Criteria

The upgrade is complete when:

- HERMES can distinguish Jue-created alpha from adopted holdings and operational failures.
- KIS and Binance have separate live-trading authority packets.
- Jue manager prompts receive live authority and evidence scorecards.
- Final accepted size is capped by deterministic live authority.
- UI and Telegram show live-trading grade.
- Weekly memory review can promote, demote, or keep strategy authority based on scorecard evidence.
- No failed LLM call creates fallback trades.
- Existing safety gates remain stronger than learned policy.

## Non-Goals

- Do not add direct LLM exchange access.
- Do not add hard learned bans.
- Do not remove existing KIS/Binance block ledgers.
- Do not merge KIS and Binance memory scopes.
- Do not claim profitability from adopted holdings or wallet positions.
- Do not increase live order size automatically on day one.

## Open Follow-Up After This Plan

After this upgrade lands, the next live-trading-grade improvement should be a
KIS-specific quant layer with deterministic price-location, volatility,
relative-strength, volume, sector-rotation, and pullback metrics. That should be
a separate plan because it changes Korean-equity signal generation rather than
live authority and performance governance.
