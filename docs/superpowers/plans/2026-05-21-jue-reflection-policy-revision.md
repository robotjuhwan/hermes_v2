# Jue Reflection Policy Revision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 쥬 change its operating behavior from weekly/monthly reflection outcomes by producing measurable period reviews, policy revision proposals, versioned policy rules, and prompt-injected operating changes.

**Architecture:** Extend `InvestmentMemoryService` with a focused period review/revision layer over the existing memory DB, block reflections, policy scorecards, and policy rules. Weekly/monthly review aggregation is deterministic, while gpt-5.5 creates policy revision proposals that pass validation before becoming active soft policy. The KIS block manager continues using existing safety gates; revisions are injected as context, not direct orders.

**Tech Stack:** Python 3.10+, SQLite, FastAPI, pytest, existing static JS/CSS frontend, existing `CodexNativeRuntime`, existing `InvestmentMemoryService` and `KISBlockTrader`.

---

## File Structure

- Modify `src/tradecraft/services/investment_memory.py`
  - Add period review tables, review aggregation, LLM review runner, policy revision validation, policy outcome scoring, context-pack injection.
- Modify `src/tradecraft/runtime/investment_memory_runner.py`
  - Run weekly/monthly due reviews without duplicating existing daily rituals.
- Modify `src/tradecraft/main.py`
  - Add admin-protected APIs for reviews and revisions.
- Modify `src/tradecraft/services/kis_block_trader.py`
  - Ensure revised policy context is visible in manager prompts and metadata.
- Modify `src/tradecraft/web/static/app.js`
  - Render review history, active revisions, and manual review controls.
- Modify `src/tradecraft/web/static/style.css`
  - Add compact AI research-room styling for review cards and policy revision chips.
- Modify `tests/test_investment_memory.py`
  - Add unit tests for due schedules, aggregation, revision validation, context injection, and policy retirement.
- Modify `tests/test_investment_memory_api.py`
  - Add API tests for review/revision endpoints and admin auth.
- Modify `tests/test_kis_block_trader.py`
  - Verify prompt receives weekly/monthly revision context.

No git commit is part of this implementation unless the user explicitly asks for a commit. Each task ends with a diff review/checkpoint instead.

---

## Task 1: Period Review Schema and Repository Methods

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write failing repository tests**

Append to `tests/test_investment_memory.py`:

```python
def test_period_review_repository_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    review = service.repository.upsert_period_review(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "metrics": {"closed_blocks": 4, "win_rate": 0.5},
            "review_md": "이번 주는 손절 속도가 빨랐다.",
            "policy_revision_ids": ["rev_1"],
        }
    )
    revision = service.repository.upsert_policy_revision(
        {
            "revision_id": "rev_1",
            "period_key": "2026-W21",
            "period_type": "weekly",
            "policy_id": "slow_down_mid_term_stops",
            "action": "create",
            "status": "candidate",
            "scope": "mid",
            "condition": {"horizon": "mid"},
            "effect": {"stop_review": "less_intraday_noise"},
            "evidence": {"sample_count": 4},
            "reason_md": "중기 블록이 일중 노이즈에 너무 빨리 닫혔다.",
            "confidence": 0.71,
        }
    )
    outcome = service.repository.upsert_policy_outcome(
        {
            "policy_id": "slow_down_mid_term_stops",
            "rule_id": "slow_down_mid_term_stops@v1",
            "period_key": "2026-W21",
            "period_type": "weekly",
            "sample_count": 4,
            "avg_pnl_pct": 1.2,
            "win_rate": 0.5,
            "expectancy_pct": 1.2,
            "max_drawdown_pct": -2.1,
            "rule_follow_rate": 0.75,
            "helped_count": 2,
            "hurt_count": 1,
            "notes_md": "손절 완화가 일부 도움이 됐다.",
        }
    )

    assert review["period_key"] == "2026-W21"
    assert revision["policy_id"] == "slow_down_mid_term_stops"
    assert outcome["helped_count"] == 2
    assert service.repository.latest_period_review("weekly")["period_key"] == "2026-W21"
    assert service.repository.list_period_reviews(period_type="weekly", limit=5)[0]["review_md"].startswith("이번 주")
    assert service.repository.list_policy_revisions(limit=5)[0]["revision_id"] == "rev_1"
    assert service.repository.list_policy_outcomes(limit=5)[0]["policy_id"] == "slow_down_mid_term_stops"
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_investment_memory.py::test_period_review_repository_round_trip -q
```

Expected: failure with `AttributeError` for missing repository methods.

- [ ] **Step 3: Add tables to `_ensure_schema`**

In `InvestmentMemoryRepository._ensure_schema`, add:

```python
                CREATE TABLE IF NOT EXISTS period_reviews (
                    period_key TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    mode TEXT NOT NULL DEFAULT 'deterministic',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    review_md TEXT NOT NULL DEFAULT '',
                    policy_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(period_key, period_type)
                );
                CREATE INDEX IF NOT EXISTS idx_period_reviews_type_updated
                    ON period_reviews(period_type, updated_at DESC);

                CREATE TABLE IF NOT EXISTS policy_revisions (
                    revision_id TEXT PRIMARY KEY,
                    period_key TEXT NOT NULL DEFAULT '',
                    period_type TEXT NOT NULL DEFAULT '',
                    policy_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT 'keep',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    scope TEXT NOT NULL DEFAULT 'general',
                    condition_json TEXT NOT NULL DEFAULT '{}',
                    effect_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    reason_md TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    activated_at TEXT NOT NULL DEFAULT '',
                    retired_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_policy_revisions_status
                    ON policy_revisions(status, period_type, created_at DESC);

                CREATE TABLE IF NOT EXISTS policy_outcomes (
                    policy_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    avg_pnl_pct REAL NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    expectancy_pct REAL NOT NULL DEFAULT 0,
                    max_drawdown_pct REAL NOT NULL DEFAULT 0,
                    rule_follow_rate REAL NOT NULL DEFAULT 0,
                    helped_count INTEGER NOT NULL DEFAULT 0,
                    hurt_count INTEGER NOT NULL DEFAULT 0,
                    notes_md TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(policy_id, rule_id, period_key, period_type)
                );
```

- [ ] **Step 4: Add repository methods**

Add these methods to `InvestmentMemoryRepository` near policy rule methods:

```python
    def upsert_period_review(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        period_key = str(row.get("period_key") or "")
        period_type = str(row.get("period_type") or "")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO period_reviews (
                    period_key, period_type, start_date, end_date, status, mode,
                    metrics_json, review_md, policy_revision_ids_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(period_key, period_type) DO UPDATE SET
                    start_date=excluded.start_date,
                    end_date=excluded.end_date,
                    status=excluded.status,
                    mode=excluded.mode,
                    metrics_json=excluded.metrics_json,
                    review_md=excluded.review_md,
                    policy_revision_ids_json=excluded.policy_revision_ids_json,
                    updated_at=excluded.updated_at
                """,
                (
                    period_key,
                    period_type,
                    str(row.get("start_date") or ""),
                    str(row.get("end_date") or ""),
                    str(row.get("status") or "ok"),
                    str(row.get("mode") or "deterministic"),
                    _json_dumps(row.get("metrics") or {}),
                    str(row.get("review_md") or ""),
                    _json_dumps(list(row.get("policy_revision_ids") or [])),
                    now,
                    now,
                ),
            )
        return self.get_period_review(period_key, period_type) or {}

    def get_period_review(self, period_key: str, period_type: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM period_reviews
                WHERE period_key = ? AND period_type = ?
                LIMIT 1
                """,
                (period_key, period_type),
            ).fetchone()
        return self._row_to_period_review(row) if row else None

    def latest_period_review(self, period_type: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM period_reviews
                WHERE period_type = ?
                ORDER BY end_date DESC, updated_at DESC
                LIMIT 1
                """,
                (period_type,),
            ).fetchone()
        return self._row_to_period_review(row) if row else {"status": "missing", "period_type": period_type}

    def list_period_reviews(self, *, period_type: str = "", limit: int = 12) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if period_type:
            where = "WHERE period_type = ?"
            params.append(period_type)
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM period_reviews
                {where}
                ORDER BY end_date DESC, updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_period_review(row) for row in rows]
```

Also add `upsert_policy_revision`, `list_policy_revisions`, `upsert_policy_outcome`, and `list_policy_outcomes`:

```python
    def upsert_policy_revision(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        revision_id = str(row.get("revision_id") or "")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_revisions (
                    revision_id, period_key, period_type, policy_id, action, status,
                    scope, condition_json, effect_json, evidence_json, reason_md,
                    confidence, created_at, activated_at, retired_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(revision_id) DO UPDATE SET
                    period_key=excluded.period_key,
                    period_type=excluded.period_type,
                    policy_id=excluded.policy_id,
                    action=excluded.action,
                    status=excluded.status,
                    scope=excluded.scope,
                    condition_json=excluded.condition_json,
                    effect_json=excluded.effect_json,
                    evidence_json=excluded.evidence_json,
                    reason_md=excluded.reason_md,
                    confidence=excluded.confidence,
                    activated_at=excluded.activated_at,
                    retired_at=excluded.retired_at
                """,
                (
                    revision_id,
                    str(row.get("period_key") or ""),
                    str(row.get("period_type") or ""),
                    str(row.get("policy_id") or ""),
                    str(row.get("action") or "keep"),
                    str(row.get("status") or "candidate"),
                    str(row.get("scope") or "general"),
                    _json_dumps(row.get("condition") or {}),
                    _json_dumps(row.get("effect") or {}),
                    _json_dumps(row.get("evidence") or {}),
                    str(row.get("reason_md") or ""),
                    _safe_float(row.get("confidence")),
                    now,
                    str(row.get("activated_at") or ""),
                    str(row.get("retired_at") or ""),
                ),
            )
        rows = self.list_policy_revisions(limit=1, revision_id=revision_id)
        return rows[0] if rows else {}

    def list_policy_revisions(
        self,
        *,
        status: str = "",
        period_type: str = "",
        revision_id: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if period_type:
            where.append("period_type = ?")
            params.append(period_type)
        if revision_id:
            where.append("revision_id = ?")
            params.append(revision_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM policy_revisions
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_policy_revision(row) for row in rows]

    def upsert_policy_outcome(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_outcomes (
                    policy_id, rule_id, period_key, period_type, sample_count,
                    avg_pnl_pct, win_rate, expectancy_pct, max_drawdown_pct,
                    rule_follow_rate, helped_count, hurt_count, notes_md, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id, rule_id, period_key, period_type) DO UPDATE SET
                    sample_count=excluded.sample_count,
                    avg_pnl_pct=excluded.avg_pnl_pct,
                    win_rate=excluded.win_rate,
                    expectancy_pct=excluded.expectancy_pct,
                    max_drawdown_pct=excluded.max_drawdown_pct,
                    rule_follow_rate=excluded.rule_follow_rate,
                    helped_count=excluded.helped_count,
                    hurt_count=excluded.hurt_count,
                    notes_md=excluded.notes_md,
                    updated_at=excluded.updated_at
                """,
                (
                    str(row.get("policy_id") or ""),
                    str(row.get("rule_id") or ""),
                    str(row.get("period_key") or ""),
                    str(row.get("period_type") or ""),
                    _safe_int(row.get("sample_count")),
                    _safe_float(row.get("avg_pnl_pct")),
                    _safe_float(row.get("win_rate")),
                    _safe_float(row.get("expectancy_pct")),
                    _safe_float(row.get("max_drawdown_pct")),
                    _safe_float(row.get("rule_follow_rate")),
                    _safe_int(row.get("helped_count")),
                    _safe_int(row.get("hurt_count")),
                    str(row.get("notes_md") or ""),
                    now,
                ),
            )
        return self.list_policy_outcomes(limit=1)[0]

    def list_policy_outcomes(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM policy_outcomes
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_policy_outcome(row) for row in rows]
```

Add row converters:

```python
    @staticmethod
    def _row_to_period_review(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "period_key": row["period_key"],
            "period_type": row["period_type"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "status": row["status"],
            "mode": row["mode"],
            "metrics": _json_loads(row["metrics_json"], {}),
            "review_md": row["review_md"],
            "policy_revision_ids": _json_loads(row["policy_revision_ids_json"], []),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_policy_revision(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "revision_id": row["revision_id"],
            "period_key": row["period_key"],
            "period_type": row["period_type"],
            "policy_id": row["policy_id"],
            "action": row["action"],
            "status": row["status"],
            "scope": row["scope"],
            "condition": _json_loads(row["condition_json"], {}),
            "effect": _json_loads(row["effect_json"], {}),
            "evidence": _json_loads(row["evidence_json"], {}),
            "reason_md": row["reason_md"],
            "confidence": float(row["confidence"] or 0),
            "created_at": row["created_at"],
            "activated_at": row["activated_at"],
            "retired_at": row["retired_at"],
        }

    @staticmethod
    def _row_to_policy_outcome(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "policy_id": row["policy_id"],
            "rule_id": row["rule_id"],
            "period_key": row["period_key"],
            "period_type": row["period_type"],
            "sample_count": int(row["sample_count"] or 0),
            "avg_pnl_pct": float(row["avg_pnl_pct"] or 0),
            "win_rate": float(row["win_rate"] or 0),
            "expectancy_pct": float(row["expectancy_pct"] or 0),
            "max_drawdown_pct": float(row["max_drawdown_pct"] or 0),
            "rule_follow_rate": float(row["rule_follow_rate"] or 0),
            "helped_count": int(row["helped_count"] or 0),
            "hurt_count": int(row["hurt_count"] or 0),
            "notes_md": row["notes_md"],
            "updated_at": row["updated_at"],
        }
```

- [ ] **Step 5: Run repository test**

Run:

```bash
pytest tests/test_investment_memory.py::test_period_review_repository_round_trip -q
```

Expected: pass.

---

## Task 2: Period Metrics Aggregation

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write failing metrics tests**

Append:

```python
def test_period_review_metrics_split_source_and_horizon(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_short_win",
            "symbol": "005930",
            "name": "삼성전자",
            "status": "closed",
            "exit_reason": "target_reached",
            "pnl_pct": 2.0,
            "rule_followed": True,
            "lesson_md": "단기 목표가 준수",
            "metrics": {
                "horizon": "short",
                "created_by": "jue",
                "holding_minutes": 80,
                "policy_id": "protect_winning_blocks",
            },
        },
        source_run_id=1,
    )
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_mid_loss",
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "status": "closed",
            "exit_reason": "stop_reached",
            "pnl_pct": -1.5,
            "rule_followed": True,
            "lesson_md": "중기 블록 손절이 빨랐다",
            "metrics": {
                "horizon": "mid",
                "created_by": "user",
                "holding_minutes": 35,
                "policy_id": "respect_defined_stops",
            },
        },
        source_run_id=1,
    )

    metrics = service.build_period_metrics(
        period_type="weekly",
        period_key="2026-W21",
        start_date="2026-05-18",
        end_date="2026-05-22",
    )

    assert metrics["closed_blocks"] == 2
    assert metrics["win_rate"] == 0.5
    assert metrics["avg_pnl_pct"] == 0.25
    assert metrics["by_horizon"]["short"]["sample_count"] == 1
    assert metrics["by_horizon"]["mid"]["avg_pnl_pct"] == -1.5
    assert metrics["by_source"]["user"]["sample_count"] == 1
    assert metrics["policy_impacts"]["respect_defined_stops"]["sample_count"] == 1
```

- [ ] **Step 2: Run failing metrics test**

Run:

```bash
pytest tests/test_investment_memory.py::test_period_review_metrics_split_source_and_horizon -q
```

Expected: failure with missing `build_period_metrics`.

- [ ] **Step 3: Add aggregation helpers**

Add to `InvestmentMemoryService`:

```python
    def build_period_metrics(
        self,
        *,
        period_type: str,
        period_key: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        reflections = self.repository.list_block_reflections(limit=5000)
        rows = [
            row
            for row in reflections
            if isinstance(row, dict)
            and start_date <= str(row.get("updated_at") or row.get("created_at") or end_date)[:10] <= end_date
        ]
        if not rows:
            return {
                "period_type": period_type,
                "period_key": period_key,
                "start_date": start_date,
                "end_date": end_date,
                "closed_blocks": 0,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "expectancy_pct": 0.0,
                "by_horizon": {},
                "by_source": {},
                "policy_impacts": {},
            }
        pnl_values = [_safe_float(row.get("pnl_pct")) for row in rows]
        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        metrics = {
            "period_type": period_type,
            "period_key": period_key,
            "start_date": start_date,
            "end_date": end_date,
            "closed_blocks": len(rows),
            "win_rate": len(wins) / len(rows),
            "avg_pnl_pct": sum(pnl_values) / len(rows),
            "avg_win_pct": sum(wins) / len(wins) if wins else 0.0,
            "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
            "expectancy_pct": sum(pnl_values) / len(rows),
            "by_horizon": self._group_reflection_metrics(rows, key="horizon"),
            "by_source": self._group_reflection_metrics(rows, key="created_by"),
            "policy_impacts": self._group_reflection_metrics(rows, key="policy_id"),
            "exit_reasons": self._count_reflection_values(rows, key="exit_reason"),
        }
        return metrics

    def _group_reflection_metrics(self, rows: list[dict[str, Any]], *, key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            value = str(metrics.get(key) or row.get(key) or "unknown")
            grouped.setdefault(value, []).append(row)
        return {
            group_key: self._reflection_group_stats(group_rows)
            for group_key, group_rows in grouped.items()
        }

    @staticmethod
    def _reflection_group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pnl_values = [_safe_float(row.get("pnl_pct")) for row in rows]
        wins = [value for value in pnl_values if value > 0]
        return {
            "sample_count": len(rows),
            "win_rate": len(wins) / len(rows) if rows else 0.0,
            "avg_pnl_pct": sum(pnl_values) / len(rows) if rows else 0.0,
            "rule_follow_rate": (
                sum(1 for row in rows if bool(row.get("rule_followed"))) / len(rows)
                if rows
                else 0.0
            ),
        }

    @staticmethod
    def _count_reflection_values(rows: list[dict[str, Any]], *, key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts
```

- [ ] **Step 4: Run metrics test**

Run:

```bash
pytest tests/test_investment_memory.py::test_period_review_metrics_split_source_and_horizon -q
```

Expected: pass.

---

## Task 3: Weekly and Monthly Review Runner

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Add period key and review tests**

Append:

```python
def test_weekly_and_monthly_review_run_once_creates_review_and_revision(tmp_path: Path) -> None:
    class _LLM:
        resolved_model = "gpt-5.5"
        ready = True

        async def complete(self, payload, timeout_ms=60000):
            _ = timeout_ms
            return {
                "status": "ok",
                "content": json.dumps(
                    {
                        "review_title": "주간 반성",
                        "review_md": "중기 블록 손절이 빠르게 나갔다.",
                        "observations": ["중기 블록은 일중 노이즈와 분리해서 본다."],
                        "policy_revisions": [
                            {
                                "policy_id": "slow_down_mid_term_stops",
                                "action": "create",
                                "scope": "mid",
                                "condition": {"horizon": "mid"},
                                "effect": {"stop_review": "confirm_with_daily_context"},
                                "reason_md": "중기 블록의 손절 판단은 장중 흔들림만으로 결정하지 않는다.",
                                "confidence": 0.72,
                            }
                        ],
                        "memory_updates": {"notes": []},
                    },
                    ensure_ascii=False,
                ),
                "model": "gpt-5.5",
            }

    service = _service(tmp_path, codex_runtime=_LLM())
    service.initialize()
    service.repository.upsert_block_reflection(
        {
            "block_id": "blk_mid_loss",
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "status": "closed",
            "exit_reason": "stop_reached",
            "pnl_pct": -1.5,
            "rule_followed": True,
            "lesson_md": "중기 블록 손절이 빠름",
            "metrics": {"horizon": "mid", "created_by": "user", "policy_id": "respect_defined_stops"},
        },
        source_run_id=1,
    )

    result = asyncio.run(
        service.run_period_review(
            period_type="weekly",
            now=datetime(2026, 5, 24, 20, 30, tzinfo=KST),
            force=True,
        )
    )

    assert result["status"] == "ok"
    assert result["period_type"] == "weekly"
    assert result["review"]["period_key"] == "2026-W21"
    assert result["revision_count"] == 1
    revisions = service.repository.list_policy_revisions(limit=5)
    assert revisions[0]["policy_id"] == "slow_down_mid_term_stops"
    assert revisions[0]["status"] == "active_caution"
```

- [ ] **Step 2: Run failing test**

Run:

```bash
pytest tests/test_investment_memory.py::test_weekly_and_monthly_review_run_once_creates_review_and_revision -q
```

Expected: failure with missing `run_period_review`.

- [ ] **Step 3: Add period window helpers**

Add:

```python
    def period_window(
        self,
        *,
        period_type: str,
        now: datetime | None = None,
    ) -> dict[str, str]:
        local = (now or datetime.now(KST)).astimezone(KST)
        if period_type == "monthly":
            first = local.date().replace(day=1)
            next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            last = next_month - timedelta(days=1)
            return {
                "period_type": "monthly",
                "period_key": first.strftime("%Y-%m"),
                "start_date": first.isoformat(),
                "end_date": last.isoformat(),
            }
        iso = local.date().isocalendar()
        monday = local.date() - timedelta(days=local.weekday())
        friday = monday + timedelta(days=4)
        return {
            "period_type": "weekly",
            "period_key": f"{iso.year}-W{iso.week:02d}",
            "start_date": monday.isoformat(),
            "end_date": friday.isoformat(),
        }
```

- [ ] **Step 4: Add LLM review runner**

Add:

```python
    async def run_period_review(
        self,
        *,
        period_type: str,
        now: datetime | None = None,
        force: bool = False,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        normalized = "monthly" if str(period_type) == "monthly" else "weekly"
        window = self.period_window(period_type=normalized, now=now)
        existing = self.repository.get_period_review(window["period_key"], normalized)
        if existing and not force:
            return {
                "status": "skipped",
                "reason": "period_review_already_exists",
                "period_type": normalized,
                "review": existing,
            }
        metrics = self.build_period_metrics(**window)
        prompt = self._build_period_review_prompt(
            period_type=normalized,
            window=window,
            metrics=metrics,
            context=context or {},
        )
        output, mode, error = await self._complete_json(prompt)
        if not isinstance(output, dict):
            output = self._deterministic_period_review(
                period_type=normalized,
                window=window,
                metrics=metrics,
                error_message=error,
            )
            mode = "deterministic"
        run_id = self.repository.save_run(
            kind="period_review",
            slot=normalized,
            status="ok" if not error else "llm_unavailable",
            mode=mode,
            model=str(getattr(self.codex_runtime, "resolved_model", "gpt-5.5")),
            error_message=error,
            input_payload=prompt,
            output_payload=output,
        )
        revisions = self._save_policy_revisions(
            output.get("policy_revisions") if isinstance(output.get("policy_revisions"), list) else [],
            period_type=normalized,
            period_key=window["period_key"],
            metrics=metrics,
        )
        review = self.repository.upsert_period_review(
            {
                **window,
                "status": "ok" if not error else "llm_unavailable",
                "mode": mode,
                "metrics": metrics,
                "review_md": str(output.get("review_md") or ""),
                "policy_revision_ids": [row["revision_id"] for row in revisions],
            }
        )
        self._apply_memory_updates(output, source_run_id=run_id)
        self._sync_revisions_to_policy_rules()
        return {
            "status": review["status"],
            "period_type": normalized,
            "period_key": window["period_key"],
            "run_id": run_id,
            "review": review,
            "revision_count": len(revisions),
            "revisions": revisions,
        }
```

Add prompt and fallback:

```python
    def _build_period_review_prompt(
        self,
        *,
        period_type: str,
        window: dict[str, str],
        metrics: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "system": "너는 HERMES 투자 파트너 쥬다. 반성 결과가 다음 운용 방식에 반영되도록 정책 개정안을 JSON으로 만든다.",
            "task": "Return JSON only. Create weekly/monthly reflection and policy revisions.",
            "period_type": period_type,
            "window": window,
            "metrics": metrics,
            "context": _compact_ritual_context(context, limit=6000),
            "allowed_revision_status": ["candidate", "active_caution", "active_preference", "retired", "rejected"],
            "allowed_actions": ["create", "strengthen", "weaken", "retire", "keep"],
            "allowed_scopes": ["short", "mid", "long", "core_etf", "cash", "user_position", "discovery", "general"],
            "hard_filter_policy": "Do not create hard filters. Safety gates remain separate.",
            "output_schema": {
                "review_title": "string",
                "review_md": "markdown string",
                "observations": ["string"],
                "policy_revisions": [
                    {
                        "policy_id": "string",
                        "action": "create|strengthen|weaken|retire|keep",
                        "scope": "short|mid|long|core_etf|cash|user_position|discovery|general",
                        "condition": {},
                        "effect": {},
                        "reason_md": "string",
                        "confidence": 0.0,
                    }
                ],
                "memory_updates": {"notes": [], "symbols": [], "blocks": []},
            },
        }

    def _deterministic_period_review(
        self,
        *,
        period_type: str,
        window: dict[str, str],
        metrics: dict[str, Any],
        error_message: str = "",
    ) -> dict[str, Any]:
        closed = _safe_int(metrics.get("closed_blocks"))
        avg = _safe_float(metrics.get("avg_pnl_pct"))
        review_md = (
            f"## {window['period_key']} {period_type} review\n\n"
            f"- 닫힌 블록: {closed}개\n"
            f"- 평균 손익: {avg:+.2f}%\n"
            f"- LLM 오류: {error_message or '-'}\n"
            "- 자동 정책 승격은 보류한다.\n"
        )
        return {
            "review_title": f"{window['period_key']} {period_type} review",
            "review_md": review_md,
            "observations": [],
            "policy_revisions": [],
            "memory_updates": {"notes": []},
        }
```

- [ ] **Step 5: Run review test**

Run:

```bash
pytest tests/test_investment_memory.py::test_weekly_and_monthly_review_run_once_creates_review_and_revision -q
```

Expected: pass.

---

## Task 4: Policy Revision Validation and Rule Activation

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Add validation tests**

Append:

```python
def test_policy_revision_rejects_hard_filter_and_syncs_active_rule(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    safe = service._save_policy_revisions(
        [
            {
                "policy_id": "prefer_mid_user_positions",
                "action": "create",
                "scope": "user_position",
                "condition": {"created_by": "user"},
                "effect": {"horizon_bias": "mid", "position_sizing": "review"},
                "reason_md": "사용자가 직접 산 보유분은 단기 노이즈보다 중기 thesis를 먼저 확인한다.",
                "confidence": 0.72,
            },
            {
                "policy_id": "ban_all_loss_after_open",
                "action": "create",
                "scope": "short",
                "condition": {"time": "09:00"},
                "effect": {"hard_filter": True, "ban": True},
                "reason_md": "하드 필터는 허용하지 않는다.",
                "confidence": 0.9,
            },
        ],
        period_type="weekly",
        period_key="2026-W21",
        metrics={"closed_blocks": 5},
    )

    assert len(safe) == 2
    by_id = {row["policy_id"]: row for row in safe}
    assert by_id["prefer_mid_user_positions"]["status"] == "active_caution"
    assert by_id["ban_all_loss_after_open"]["status"] == "rejected"

    result = service._sync_revisions_to_policy_rules()
    rules = service.policy_rules(active_only=True)["items"]

    assert result["created_count"] == 1
    assert rules[0]["policy_id"] == "prefer_mid_user_positions"
    assert rules[0]["effect"]["hard_filter"] is False
```

- [ ] **Step 2: Run failing validation test**

Run:

```bash
pytest tests/test_investment_memory.py::test_policy_revision_rejects_hard_filter_and_syncs_active_rule -q
```

Expected: failure with missing `_save_policy_revisions`.

- [ ] **Step 3: Add revision validation**

Add:

```python
    def _save_policy_revisions(
        self,
        revisions: list[Any],
        *,
        period_type: str,
        period_key: str,
        metrics: dict[str, Any],
    ) -> list[dict[str, Any]]:
        saved: list[dict[str, Any]] = []
        for idx, raw in enumerate(revisions, start=1):
            if not isinstance(raw, dict):
                continue
            row = self._normalize_policy_revision(
                raw,
                period_type=period_type,
                period_key=period_key,
                index=idx,
                metrics=metrics,
            )
            saved.append(self.repository.upsert_policy_revision(row))
        return saved

    def _normalize_policy_revision(
        self,
        raw: dict[str, Any],
        *,
        period_type: str,
        period_key: str,
        index: int,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        policy_id = _clean_text(raw.get("policy_id"), limit=120) or f"period_revision_{index}"
        action = str(raw.get("action") or "keep")
        scope = str(raw.get("scope") or "general")
        confidence = min(max(_safe_float(raw.get("confidence")), 0.0), 0.95)
        condition = raw.get("condition") if isinstance(raw.get("condition"), dict) else {}
        effect = raw.get("effect") if isinstance(raw.get("effect"), dict) else {}
        reason_md = _clean_text(raw.get("reason_md") or raw.get("reason"), limit=2400)
        status = self._revision_status(
            action=action,
            confidence=confidence,
            effect=effect,
            metrics=metrics,
        )
        revision_id = f"{period_key}:{policy_id}:{index}"
        return {
            "revision_id": revision_id,
            "period_key": period_key,
            "period_type": period_type,
            "policy_id": policy_id,
            "action": action if action in {"create", "strengthen", "weaken", "retire", "keep"} else "keep",
            "status": status,
            "scope": scope if scope in {"short", "mid", "long", "core_etf", "cash", "user_position", "discovery", "general"} else "general",
            "condition": condition,
            "effect": {**effect, "hard_filter": False, "safety_gate_override": False},
            "evidence": {
                "period_key": period_key,
                "period_type": period_type,
                "metrics": metrics,
                "raw_confidence": confidence,
            },
            "reason_md": reason_md,
            "confidence": confidence,
        }

    @staticmethod
    def _revision_status(
        *,
        action: str,
        confidence: float,
        effect: dict[str, Any],
        metrics: dict[str, Any],
    ) -> str:
        if bool(effect.get("hard_filter")) or bool(effect.get("ban")) or bool(effect.get("safety_gate_override")):
            return "rejected"
        if action == "retire":
            return "retired"
        sample_count = _safe_int(metrics.get("closed_blocks"))
        if sample_count >= 5 and confidence >= 0.72 and action in {"create", "strengthen"}:
            return "active_preference"
        if sample_count >= 3 and confidence >= 0.65 and action in {"create", "strengthen", "weaken"}:
            return "active_caution"
        return "candidate"
```

- [ ] **Step 4: Add revision-to-rule sync**

Add:

```python
    def _sync_revisions_to_policy_rules(self) -> dict[str, Any]:
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        revisions = self.repository.list_policy_revisions(limit=200)
        for revision in revisions:
            status = str(revision.get("status") or "")
            if status not in {"active_caution", "active_preference"}:
                skipped.append(revision)
                continue
            rule = {
                "policy_id": revision["policy_id"],
                "status": status,
                "action": "prefer" if status == "active_preference" else "caution",
                "condition": revision.get("condition") or {},
                "effect": {
                    **(revision.get("effect") or {}),
                    "policy_mode": "soft_period_revision",
                    "hard_filter": False,
                    "safety_gate_override": False,
                },
                "reason": revision.get("reason_md") or "",
                "evidence": revision.get("evidence") or {},
                "source_scorecard": {"source": "policy_revision", "revision_id": revision["revision_id"]},
            }
            latest = self.repository.latest_policy_rule(rule["policy_id"])
            if latest and self._policy_rule_signature(latest) == self._policy_rule_signature(rule):
                skipped.append(latest)
                continue
            version = int(latest.get("version") or 0) + 1 if latest else 1
            rule["version"] = version
            rule["rule_id"] = f"{rule['policy_id']}@v{version}"
            file_path = self._write_policy_rule_file(rule)
            rule["file_path"] = str(file_path)
            saved = self.repository.upsert_policy_rule(rule)
            if latest:
                self.repository.retire_policy_rule(rule["policy_id"], _safe_int(latest.get("version")))
            created.append(saved)
        return {"status": "ok", "created_count": len(created), "skipped_count": len(skipped), "created": created}
```

Update `sync_policy_rules()` to call `_sync_revisions_to_policy_rules()` after existing scorecard logic and merge counts.

- [ ] **Step 5: Run validation test**

Run:

```bash
pytest tests/test_investment_memory.py::test_policy_revision_rejects_hard_filter_and_syncs_active_rule -q
```

Expected: pass.

---

## Task 5: Weekly/Monthly Due Schedule and Runner Integration

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Modify: `src/tradecraft/runtime/investment_memory_runner.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Add due schedule tests**

Append:

```python
def test_due_slots_include_weekly_and_monthly_reviews(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    friday_close = datetime(2026, 5, 22, 16, 5, tzinfo=KST)
    month_end_close = datetime(2026, 5, 29, 16, 5, tzinfo=KST)

    assert "weekly_review" in service.due_slots(now=friday_close)
    assert "monthly_review" in service.due_slots(now=month_end_close)

    asyncio.run(service.run_period_review(period_type="weekly", now=friday_close, force=True))
    assert "weekly_review" not in service.due_slots(now=friday_close)
```

- [ ] **Step 2: Run failing due test**

Run:

```bash
pytest tests/test_investment_memory.py::test_due_slots_include_weekly_and_monthly_reviews -q
```

Expected: failure because `weekly_review` and `monthly_review` are not scheduled.

- [ ] **Step 3: Extend valid slots and due schedule**

At top constants, add:

```python
    "weekly_review",
    "monthly_review",
```

And labels:

```python
    "weekly_review": "주간 운용 반성",
    "monthly_review": "월간 운용 반성",
```

Update `due_slots()`:

```python
        if local.weekday() == 4 and time(16, 0) <= current <= time(17, 30):
            window = self.period_window(period_type="weekly", now=local)
            if self.repository.get_period_review(window["period_key"], "weekly") is None:
                slots.append("weekly_review")
        tomorrow = local.date() + timedelta(days=1)
        is_month_end_window = tomorrow.month != local.date().month
        if is_month_end_window and time(16, 0) <= current <= time(18, 0):
            window = self.period_window(period_type="monthly", now=local)
            if self.repository.get_period_review(window["period_key"], "monthly") is None:
                slots.append("monthly_review")
```

Keep existing Sunday weekly fallback as a backup by returning `["weekly_review"]` instead of `["weekly"]`.

- [ ] **Step 4: Update runner handling**

In `run_investment_memory_loop`, before daily ritual loop:

```python
            review_slots = [slot for slot in due_slots if slot in {"weekly_review", "monthly_review"}]
            for review_slot in review_slots:
                period_type = "monthly" if review_slot == "monthly_review" else "weekly"
                review_result = await resolved_service.run_period_review(
                    period_type=period_type,
                    context=context,
                    force=False,
                )
                results.append(
                    {
                        "status": review_result.get("status"),
                        "slot": review_slot,
                        "period_key": review_result.get("period_key"),
                        "revision_count": review_result.get("revision_count", 0),
                    }
                )
            ritual_slots = [
                slot for slot in due_slots
                if slot not in {"weekly_review", "monthly_review"}
            ]
```

Change the existing `if due_slots:` loop to use `ritual_slots`.

- [ ] **Step 5: Run due schedule test**

Run:

```bash
pytest tests/test_investment_memory.py::test_due_slots_include_weekly_and_monthly_reviews -q
```

Expected: pass.

---

## Task 6: Context Pack and KIS Prompt Injection

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_investment_memory.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Add context-pack test**

Append:

```python
def test_context_pack_includes_period_reviews_and_policy_revisions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.upsert_period_review(
        {
            "period_key": "2026-W21",
            "period_type": "weekly",
            "start_date": "2026-05-18",
            "end_date": "2026-05-22",
            "status": "ok",
            "mode": "llm",
            "metrics": {"closed_blocks": 3},
            "review_md": "중기 블록 손절 기준을 조정한다.",
            "policy_revision_ids": ["2026-W21:prefer_mid_user_positions:1"],
        }
    )
    service.repository.upsert_policy_revision(
        {
            "revision_id": "2026-W21:prefer_mid_user_positions:1",
            "period_key": "2026-W21",
            "period_type": "weekly",
            "policy_id": "prefer_mid_user_positions",
            "action": "create",
            "status": "active_caution",
            "scope": "user_position",
            "condition": {"created_by": "user"},
            "effect": {"horizon_bias": "mid", "hard_filter": False},
            "evidence": {"sample_count": 3},
            "reason_md": "사용자 직접 매수분은 중기 관리 우선.",
            "confidence": 0.7,
        }
    )

    pack = service.context_pack(max_chars=6000)

    assert pack["period_reviews"]["weekly"]["period_key"] == "2026-W21"
    assert pack["policy_revisions"][0]["policy_id"] == "prefer_mid_user_positions"
```

- [ ] **Step 2: Run failing context test**

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_period_reviews_and_policy_revisions -q
```

Expected: failure because context pack lacks period review data.

- [ ] **Step 3: Add context fields**

In `context_pack()`, after policy rules are loaded:

```python
        period_reviews = {
            "weekly": self.repository.latest_period_review("weekly"),
            "monthly": self.repository.latest_period_review("monthly"),
        }
        policy_revisions = self.repository.list_policy_revisions(limit=12)
        policy_outcomes = self.repository.list_policy_outcomes(limit=12)
```

Add to payload:

```python
            "period_reviews": {
                key: {
                    "period_key": value.get("period_key"),
                    "status": value.get("status"),
                    "metrics": value.get("metrics") or {},
                    "review_md": _truncate(value.get("review_md"), 900),
                    "updated_at": value.get("updated_at"),
                }
                for key, value in period_reviews.items()
                if value.get("status") != "missing"
            },
            "policy_revisions": [
                {
                    "revision_id": row.get("revision_id"),
                    "policy_id": row.get("policy_id"),
                    "action": row.get("action"),
                    "status": row.get("status"),
                    "scope": row.get("scope"),
                    "effect": row.get("effect") or {},
                    "reason_md": _truncate(row.get("reason_md"), 500),
                    "confidence": row.get("confidence"),
                }
                for row in policy_revisions
            ],
            "policy_outcomes": policy_outcomes,
```

- [ ] **Step 4: Add KIS prompt assertion**

Append to `tests/test_kis_block_trader.py`:

```python
def test_manager_prompt_receives_policy_revision_context(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = KISBlockTrader(
        config=KISBlockTraderConfig(
            db_path=str(tmp_path / "kis_blocks.db"),
            execute_orders=False,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),
        codex_runtime=llm,
        strategy_engine=_FakeStrategy(),
        memory_context_provider=lambda **kwargs: {
            "status": "ok",
            "period_reviews": {"weekly": {"period_key": "2026-W21", "review_md": "중기 손절 조정"}},
            "policy_revisions": [{"policy_id": "prefer_mid_user_positions", "status": "active_caution"}],
            "policy_rule_evaluation": {"status": "ok", "active_rule_count": 1},
        },
    )
    trader.clock = lambda: {"session": "regular", "is_market_open": True}

    asyncio.run(trader.run_manager_once())
    prompt = json.loads(llm.calls[0]["messages"][1]["content"])

    assert prompt["investment_memory"]["period_reviews"]["weekly"]["period_key"] == "2026-W21"
    assert prompt["investment_memory"]["policy_revisions"][0]["policy_id"] == "prefer_mid_user_positions"
```

- [ ] **Step 5: Run context and KIS tests**

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_period_reviews_and_policy_revisions tests/test_kis_block_trader.py::test_manager_prompt_receives_policy_revision_context -q
```

Expected: pass.

---

## Task 7: API Endpoints

**Files:**
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_investment_memory_api.py`

- [ ] **Step 1: Add API tests**

Append to `tests/test_investment_memory_api.py`:

```python
def test_memory_review_and_revision_api_routes(monkeypatch) -> None:
    class FakeMemoryService:
        def latest_period_review(self, period_type: str) -> dict:
            return {"status": "ok", "period_type": period_type, "period_key": "2026-W21"}

        def period_reviews(self, *, period_type: str = "", limit: int = 12) -> dict:
            return {"status": "ok", "period_type": period_type, "items": [{"period_key": "2026-W21"}]}

        async def run_period_review(self, *, period_type: str, context: dict, force: bool = False) -> dict:
            return {"status": "ok", "period_type": period_type, "force": force, "context": context}

        def policy_revisions(self, *, status: str = "", limit: int = 30) -> dict:
            return {"status": "ok", "filter_status": status, "items": [{"revision_id": "rev_1"}]}

        def activate_policy_revision(self, revision_id: str) -> dict:
            return {"status": "ok", "revision_id": revision_id, "activated": True}

        def reject_policy_revision(self, revision_id: str) -> dict:
            return {"status": "ok", "revision_id": revision_id, "rejected": True}

    monkeypatch.setattr(main, "investment_memory_service", FakeMemoryService())

    async def fake_context() -> dict:
        return {"account": {"cash_krw": 1_000_000}}

    monkeypatch.setattr(main, "_build_investment_memory_context", fake_context)

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        latest = client.get("/api/memory/reviews/latest?period_type=weekly", headers=headers)
        history = client.get("/api/memory/reviews/history?period_type=weekly", headers=headers)
        run = client.post("/api/memory/reviews/run-once", json={"period_type": "monthly", "force": True}, headers=headers)
        revisions = client.get("/api/memory/policies/revisions?status=active_caution", headers=headers)
        activate = client.post("/api/memory/policies/revisions/rev_1/activate", headers=headers)
        reject = client.post("/api/memory/policies/revisions/rev_1/reject", headers=headers)

    assert latest.status_code == 200
    assert history.json()["items"][0]["period_key"] == "2026-W21"
    assert run.json()["period_type"] == "monthly"
    assert revisions.json()["filter_status"] == "active_caution"
    assert activate.json()["activated"] is True
    assert reject.json()["rejected"] is True
```

- [ ] **Step 2: Run failing API test**

Run:

```bash
pytest tests/test_investment_memory_api.py::test_memory_review_and_revision_api_routes -q
```

Expected: failure with 404.

- [ ] **Step 3: Add service public methods**

Add to `InvestmentMemoryService`:

```python
    def latest_period_review(self, period_type: str) -> dict[str, Any]:
        normalized = "monthly" if period_type == "monthly" else "weekly"
        return self.repository.latest_period_review(normalized)

    def period_reviews(self, *, period_type: str = "", limit: int = 12) -> dict[str, Any]:
        normalized = period_type if period_type in {"weekly", "monthly"} else ""
        return {
            "status": "ok",
            "period_type": normalized,
            "items": self.repository.list_period_reviews(period_type=normalized, limit=limit),
        }

    def policy_revisions(self, *, status: str = "", limit: int = 30) -> dict[str, Any]:
        return {
            "status": "ok",
            "filter_status": status,
            "items": self.repository.list_policy_revisions(status=status, limit=limit),
        }

    def activate_policy_revision(self, revision_id: str) -> dict[str, Any]:
        rows = self.repository.list_policy_revisions(revision_id=revision_id, limit=1)
        if not rows:
            return {"status": "missing", "revision_id": revision_id}
        row = {**rows[0], "status": "active_caution", "activated_at": utc_now_iso()}
        saved = self.repository.upsert_policy_revision(row)
        sync = self._sync_revisions_to_policy_rules()
        return {"status": "ok", "revision_id": revision_id, "activated": True, "revision": saved, "sync": sync}

    def reject_policy_revision(self, revision_id: str) -> dict[str, Any]:
        rows = self.repository.list_policy_revisions(revision_id=revision_id, limit=1)
        if not rows:
            return {"status": "missing", "revision_id": revision_id}
        row = {**rows[0], "status": "rejected"}
        saved = self.repository.upsert_policy_revision(row)
        return {"status": "ok", "revision_id": revision_id, "rejected": True, "revision": saved}
```

- [ ] **Step 4: Add FastAPI routes**

In `main.py` near memory policy routes:

```python
@app.get("/api/memory/reviews/latest")
async def investment_memory_review_latest(
    period_type: str = "weekly",
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    return investment_memory_service.latest_period_review(period_type)


@app.get("/api/memory/reviews/history")
async def investment_memory_review_history(
    period_type: str = "",
    limit: int = 12,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    return investment_memory_service.period_reviews(
        period_type=period_type,
        limit=max(min(int(limit), 100), 1),
    )


@app.post("/api/memory/reviews/run-once")
async def investment_memory_review_run_once(
    payload: dict[str, Any] | None = None,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    body = payload or {}
    return await investment_memory_service.run_period_review(
        period_type=str(body.get("period_type") or "weekly"),
        context=await _build_investment_memory_context(),
        force=bool(body.get("force", True)),
    )


@app.get("/api/memory/policies/revisions")
async def investment_memory_policy_revisions(
    status: str = "",
    limit: int = 30,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    return investment_memory_service.policy_revisions(
        status=status,
        limit=max(min(int(limit), 200), 1),
    )


@app.post("/api/memory/policies/revisions/{revision_id}/activate")
async def investment_memory_policy_revision_activate(
    revision_id: str,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    result = investment_memory_service.activate_policy_revision(revision_id)
    if result.get("status") == "missing":
        raise HTTPException(status_code=404, detail="revision not found")
    return result


@app.post("/api/memory/policies/revisions/{revision_id}/reject")
async def investment_memory_policy_revision_reject(
    revision_id: str,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    result = investment_memory_service.reject_policy_revision(revision_id)
    if result.get("status") == "missing":
        raise HTTPException(status_code=404, detail="revision not found")
    return result
```

- [ ] **Step 5: Run API test**

Run:

```bash
pytest tests/test_investment_memory_api.py::test_memory_review_and_revision_api_routes -q
```

Expected: pass.

---

## Task 8: UI and Telegram Surface

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Modify: `src/tradecraft/services/telegram_cli.py`
- Test: `tests/test_telegram_cli.py`

- [ ] **Step 1: Add frontend state and fetchers**

In `app.js` state:

```js
  memoryReviews: null,
  memoryRevisions: null,
  memoryReviewRunning: false,
  memoryReviewError: "",
```

Add:

```js
async function loadMemoryReviews() {
  try {
    const [weekly, monthly, revisions] = await Promise.all([
      getJSON("/memory/reviews/latest?period_type=weekly"),
      getJSON("/memory/reviews/latest?period_type=monthly"),
      getJSON("/memory/policies/revisions?limit=12"),
    ]);
    state.memoryReviews = { weekly, monthly };
    state.memoryRevisions = revisions;
    state.memoryReviewError = "";
  } catch (error) {
    state.memoryReviewError = getErrorMessage(error);
  }
}

async function runMemoryPeriodReview(periodType) {
  state.memoryReviewRunning = true;
  render();
  try {
    await getJSON("/memory/reviews/run-once", {
      method: "POST",
      body: JSON.stringify({ period_type: periodType, force: true }),
    });
    await loadMemoryReviews();
    await loadInvestmentMemory();
  } catch (error) {
    state.memoryReviewError = getErrorMessage(error);
  } finally {
    state.memoryReviewRunning = false;
    render();
  }
}
```

- [ ] **Step 2: Add review renderer**

Add:

```js
function renderPeriodReviewPanel() {
  const reviews = state.memoryReviews || {};
  const revisions = Array.isArray(state.memoryRevisions?.items) ? state.memoryRevisions.items : [];
  const busy = state.memoryReviewRunning ? "disabled" : "";
  const card = (label, review) => `
    <article class="period-review-card">
      <span class="eyebrow">${escapeHTML(label)}</span>
      <strong>${escapeHTML(review?.period_key || "missing")}</strong>
      <p>${escapeHTML(review?.review_md || "아직 누적 반성이 없습니다.")}</p>
      <div class="strategy-data-strip compact">
        <span class="strategy-data-chip">closed ${escapeHTML(String(review?.metrics?.closed_blocks ?? 0))}</span>
        <span class="strategy-data-chip">avg ${escapeHTML(fmtPct(review?.metrics?.avg_pnl_pct ?? 0))}</span>
      </div>
    </article>
  `;
  return `
    <section class="memory-section period-review-panel">
      <div class="helper-row-head">
        <div>
          <span class="eyebrow">REFLECTION LOOP</span>
          <h4>주간/월간 운용 반성</h4>
          <p>반성 결과를 정책 개정안으로 바꿔 다음 블록 판단에 반영합니다.</p>
        </div>
        <div class="daily-discovery-actions">
          <button class="btn small" data-period-review="weekly" ${busy}>주간 반성 실행</button>
          <button class="btn small" data-period-review="monthly" ${busy}>월간 반성 실행</button>
        </div>
      </div>
      ${state.memoryReviewError ? `<div class="notice warn">${escapeHTML(state.memoryReviewError)}</div>` : ""}
      <div class="period-review-grid">
        ${card("weekly", reviews.weekly)}
        ${card("monthly", reviews.monthly)}
      </div>
      <div class="policy-revision-list">
        ${revisions.length ? revisions.map((row) => `
          <article class="policy-revision-chip">
            <strong>${escapeHTML(row.policy_id || "-")}</strong>
            <span>${escapeHTML(row.status || "-")} · ${escapeHTML(row.scope || "general")}</span>
            <p>${escapeHTML(row.reason_md || "")}</p>
          </article>
        `).join("") : '<div class="notice compact">정책 개정안 없음</div>'}
      </div>
    </section>
  `;
}
```

Insert `renderPeriodReviewPanel()` in `renderInvestmentMemoryTab()` after active policy area.

Event handler:

```js
const periodReviewAction = target ? target.closest("[data-period-review]") : null;
if (periodReviewAction) {
  runMemoryPeriodReview(String(periodReviewAction.dataset.periodReview || "weekly"));
  return;
}
```

Call `loadMemoryReviews()` when memory tab opens and during main bootstrap where memory is loaded.

- [ ] **Step 3: Add CSS**

Add:

```css
.period-review-panel {
  display: grid;
  gap: 12px;
}

.period-review-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}

.period-review-card,
.policy-revision-chip {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-soft);
  padding: 12px;
}

.period-review-card strong,
.policy-revision-chip strong {
  color: var(--ink);
}

.period-review-card p,
.policy-revision-chip p {
  margin: 8px 0 0;
  color: var(--muted-strong);
  line-height: 1.5;
}

.policy-revision-list {
  display: grid;
  gap: 8px;
}

.policy-revision-chip span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
}
```

- [ ] **Step 4: Add Telegram commands**

In `telegram_cli.py`, add command aliases:

```python
        if command in {"weekly-review", "weekly_review"}:
            return await self._memory_period_review("weekly")
        if command in {"monthly-review", "monthly_review"}:
            return await self._memory_period_review("monthly")
        if command == "policy":
            return await self._memory_policy_summary(args)
```

Add helper methods using existing memory service hooks:

```python
    async def _memory_period_review(self, period_type: str) -> dict[str, Any]:
        if not self.memory_service:
            return await self._send_text("메모리 서비스가 연결되어 있지 않습니다.")
        result = await self.memory_service.run_period_review(
            period_type=period_type,
            context={},
            force=True,
        )
        review = result.get("review") if isinstance(result.get("review"), dict) else {}
        return await self._send_text(
            f"쥬 {period_type} 반성\n"
            f"{review.get('period_key', '-')}\n"
            f"{str(review.get('review_md') or '')[:1200]}"
        )

    async def _memory_policy_summary(self, args: list[str]) -> dict[str, Any]:
        if not self.memory_service:
            return await self._send_text("메모리 서비스가 연결되어 있지 않습니다.")
        payload = self.memory_service.policy_revisions(limit=8)
        rows = payload.get("items") if isinstance(payload, dict) else []
        lines = ["쥬 정책 개정안"]
        for row in list(rows or [])[:8]:
            lines.append(
                f"- {row.get('policy_id')} · {row.get('status')} · {row.get('scope')}"
            )
        return await self._send_text("\n".join(lines))
```

- [ ] **Step 5: Run UI and Telegram checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
pytest tests/test_telegram_cli.py -q
```

Expected: pass.

---

## Task 9: Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_investment_memory.py tests/test_investment_memory_api.py tests/test_kis_block_trader.py::test_manager_prompt_receives_policy_revision_context -q
```

Expected: pass.

- [ ] **Step 2: Run related smoke tests**

Run:

```bash
pytest tests/test_api_smoke.py tests/test_telegram_cli.py tests/test_kis_block_trader.py -q
```

Expected: pass.

- [ ] **Step 3: Run frontend checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: command exits 0.

- [ ] **Step 4: Run lint and whitespace checks**

Run:

```bash
ruff check src/tradecraft/services/investment_memory.py src/tradecraft/runtime/investment_memory_runner.py src/tradecraft/main.py tests/test_investment_memory.py tests/test_investment_memory_api.py
git diff --check
```

Expected: `ruff` passes and `git diff --check` exits 0.

- [ ] **Step 5: Run full test suite**

Run:

```bash
pytest -q
```

Expected: pass.

---

## Self-Review

- Spec coverage: Covers weekly review, monthly review, period metrics, LLM policy revisions, active soft policy rules, prompt injection, API, UI, Telegram, and verification.
- Placeholder scan: No task relies on unspecified future work. Every new API, method, table, and test has explicit names and expected behavior.
- Type consistency: Uses `period_review`, `policy_revision`, `policy_outcome`, `run_period_review`, and `period_window` consistently across service, API, UI, and tests.
- Safety: No hard filters are introduced. Safety gates remain outside memory policy and override all policy revisions.
