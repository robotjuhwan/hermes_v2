# Jue Financial Services Deep Absorption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deepen Jue's financial-services-inspired workflow layer from simple prompt-pack injection into a full analyst-to-block decision lifecycle.

**Architecture:** Extend the existing package-local `src/tradecraft/jue` registry with source provenance, richer skills, new workflow manifests, and stricter contracts. Add a compact lifecycle artifact service that stores analyst work products in the existing memory/provenance path and injects high-signal lifecycle context into KIS manager prompts without creating a separate research lab.

**Tech Stack:** Python 3.10, FastAPI, SQLite, static HTML/CSS/JS, pytest, existing `JueSkillRegistry`, `InvestmentMemoryService`, `KISBlockTrader`, and `jue_decision_packet`.

---

## File Structure

- Create `src/tradecraft/jue/sources/financial_services_manifest.json`
  - Maps external financial-services skills to local Jue adaptations.
- Modify `src/tradecraft/services/jue_skill_registry.py`
  - Load source manifests and include source provenance in compiled packs.
- Create new skills in `src/tradecraft/jue/skills/`
  - `earnings_preview.md`
  - `earnings_analysis.md`
  - `sector_overview.md`
  - `model_update.md`
  - `morning_note.md`
  - `valuation_frame.md`
- Create new workflows in `src/tradecraft/jue/workflows/`
  - `kis_morning_note.json`
  - `kis_idea_screen.json`
  - `kis_symbol_deep_dive.json`
  - `kis_earnings_update.json`
  - `kis_sector_rotation.json`
  - `portfolio_rebalance.json`
- Create new contracts in `src/tradecraft/jue/contracts/`
  - `morning_note_contract.json`
  - `earnings_event_contract.json`
  - `sector_rotation_contract.json`
  - `model_update_contract.json`
  - `portfolio_rebalance_contract.json`
  - `decision_lifecycle_contract.json`
- Create `src/tradecraft/services/jue_lifecycle.py`
  - Stores lifecycle artifacts in `.runtime/investment_memory.db` under focused tables.
- Modify `src/tradecraft/services/jue_decision_packet.py`
  - Add `build_decision_lifecycle_packet`.
- Modify `src/tradecraft/services/investment_memory.py`
  - Compact lifecycle artifacts into `context_pack`.
- Modify `src/tradecraft/services/kis_block_trader.py`
  - Include lifecycle packet and relevant artifact summaries in the KIS manager prompt.
- Modify `src/tradecraft/main.py`
  - Add lifecycle/source-manifest API endpoints.
- Modify `src/tradecraft/web/static/app.js` and `style.css`
  - Show source manifest and lifecycle artifacts in existing settings/memory/strategy areas.
- Modify docs under `docs/spec/`
  - Document lifecycle data flow and operational meaning.

Do not commit during implementation unless the user explicitly requests it.

---

## Task 1: Source Manifest and Registry Provenance

**Files:**
- Create: `src/tradecraft/jue/sources/financial_services_manifest.json`
- Modify: `src/tradecraft/services/jue_skill_registry.py`
- Modify: `scripts/check_jue_workflows.py`
- Test: `tests/test_jue_skill_registry.py`
- Test: `tests/test_jue_workflow_manifests.py`

- [ ] **Step 1: Write failing tests for source manifest loading**

Add tests:

```python
def test_registry_loads_financial_services_source_manifest() -> None:
    registry = JueSkillRegistry(root=Path("src/tradecraft/jue"))

    manifest = registry.load_source_manifest("financial_services")

    assert manifest["source_id"] == "financial_services"
    assert manifest["repository_url"] == "https://github.com/anthropics/financial-services"
    assert any(row["local_skill_id"] == "idea_generation" for row in manifest["mappings"])
```

```python
def test_prompt_pack_includes_source_manifest_links() -> None:
    registry = JueSkillRegistry(root=Path("src/tradecraft/jue"))

    pack = registry.compile_prompt_pack("kis_intraday_manager")

    assert "source_manifest_links" in pack
    assert any(row["source_id"] == "financial_services" for row in pack["source_manifest_links"])
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_skill_registry.py::test_registry_loads_financial_services_source_manifest tests/test_jue_skill_registry.py::test_prompt_pack_includes_source_manifest_links -q
```

Expected: fail because `load_source_manifest` and `source_manifest_links` do not exist.

- [ ] **Step 3: Add source manifest JSON**

Create `src/tradecraft/jue/sources/financial_services_manifest.json`:

```json
{
  "source_id": "financial_services",
  "repository_url": "https://github.com/anthropics/financial-services",
  "source_license_note": "Reference inspiration only; local skills are compact HERMES adaptations.",
  "mappings": [
    {
      "source_vertical": "equity-research",
      "source_skill": "idea-generation",
      "source_url": "https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md",
      "local_skill_id": "idea_generation",
      "adopted_principles": ["systematic screens", "style buckets", "mispricing hypothesis", "reject reasons"],
      "excluded_principles": ["US-only assumptions", "long report format"]
    },
    {
      "source_vertical": "equity-research",
      "source_skill": "thesis-tracker",
      "source_url": "https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md",
      "local_skill_id": "thesis_tracker",
      "adopted_principles": ["falsifiable thesis", "pillar status", "catalyst tracking", "conviction delta"],
      "excluded_principles": ["quarterly-only cadence"]
    },
    {
      "source_vertical": "wealth-management",
      "source_skill": "portfolio-rebalance",
      "source_url": "https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/wealth-management/skills/portfolio-rebalance/SKILL.md",
      "local_skill_id": "portfolio_balance",
      "adopted_principles": ["allocation drift", "cash plan", "rebalance action", "position concentration"],
      "excluded_principles": ["US tax account assumptions"]
    }
  ]
}
```

- [ ] **Step 4: Implement source manifest loading**

Add to `JueSkillRegistry`:

```python
def load_source_manifest(self, source_id: str) -> dict[str, Any]:
    path = self.root / "sources" / f"{source_id}.json"
    if not path.exists():
        raise JueSkillValidationError(f"source manifest not found: {source_id}")
    data = _load_json_asset(path, f"source manifest {source_id}")
    if data.get("source_id") != source_id:
        raise JueSkillValidationError(f"source manifest id mismatch: {source_id}")
    mappings = data.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise JueSkillValidationError(f"source manifest mappings missing: {source_id}")
    return data
```

Add helper:

```python
def _source_links_for_skills(self, skill_ids: list[str]) -> list[dict[str, Any]]:
    try:
        manifest = self.load_source_manifest("financial_services")
    except JueSkillValidationError:
        return []
    links = []
    for row in manifest.get("mappings") or []:
        if row.get("local_skill_id") in skill_ids:
            links.append({
                "source_id": manifest["source_id"],
                "source_skill": row.get("source_skill"),
                "source_url": row.get("source_url"),
                "local_skill_id": row.get("local_skill_id"),
                "adopted_principles": list(row.get("adopted_principles") or [])[:8],
            })
    return links
```

In `compile_prompt_pack`, include:

```python
skill_ids = [skill["skill_id"] for skill in skills]
"source_manifest_links": self._source_links_for_skills(skill_ids),
```

- [ ] **Step 5: Update workflow checker**

In `scripts/check_jue_workflows.py`, add checks that every manifest mapping points to an existing local skill:

```python
manifest = registry.load_source_manifest("financial_services")
for row in manifest["mappings"]:
    registry.load_skill(row["local_skill_id"])
```

- [ ] **Step 6: Run validation**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_skill_registry.py tests/test_jue_workflow_manifests.py -q
.venv/bin/python3 scripts/check_jue_workflows.py
```

Expected: all pass and checker prints `Jue workflow check OK`.

---

## Task 2: Expand Jue Skill, Workflow, and Contract Assets

**Files:**
- Create: six new `src/tradecraft/jue/skills/*.md`
- Create: six new `src/tradecraft/jue/workflows/*.json`
- Create: six new `src/tradecraft/jue/contracts/*.json`
- Modify: `tests/test_jue_workflow_manifests.py`

- [ ] **Step 1: Extend required asset tests**

Add required skills:

```python
REQUIRED_SKILLS.update({
    "earnings_preview",
    "earnings_analysis",
    "sector_overview",
    "model_update",
    "morning_note",
    "valuation_frame",
})
```

Add required workflows:

```python
REQUIRED_WORKFLOWS.update({
    "kis_morning_note",
    "kis_idea_screen",
    "kis_symbol_deep_dive",
    "kis_earnings_update",
    "kis_sector_rotation",
    "portfolio_rebalance",
})
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_workflow_manifests.py::test_required_jue_skills_exist_and_parse tests/test_jue_workflow_manifests.py::test_required_workflows_compile -q
```

Expected: fail for missing skills/workflows.

- [ ] **Step 3: Add new skill markdown files**

Each file must include frontmatter compatible with `parse_skill_markdown`.

Example for `morning_note.md`:

```markdown
---
skill_id: morning_note
name: Morning Note
version: 1
scope: kis
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/morning-note/SKILL.md
required_outputs:
  - overnight_developments
  - key_events
  - trade_implications
  - risk_focus
max_prompt_chars: 1500
---
# Morning Note

Jue prepares a compact KIS trading-day note before active decisions. Summarize
overnight market context, domestic catalysts, account pressure, open blocks,
sector rotation, ETF/core allocation, and the specific risk focus for today.

Output should be short, opinionated, and tied to block implications:
- candidate to research
- block to protect
- block to avoid adding
- waiting-entry condition
- cash discipline reminder
```

Write similarly compact local adaptations for:

- `earnings_preview.md`
- `earnings_analysis.md`
- `sector_overview.md`
- `model_update.md`
- `valuation_frame.md`

Do not copy long external text. Use HERMES-specific wording.

- [ ] **Step 4: Add new contracts**

Example `morning_note_contract.json`:

```json
{
  "contract_id": "morning_note_contract",
  "version": 1,
  "required": ["trading_day", "market_context", "account_focus", "block_implications", "risk_focus"],
  "source_types": ["account", "blocks", "market_pulse", "reports", "memory", "quotes"],
  "reject_when": ["missing_trading_day", "no_block_implication", "no_risk_focus", "stale_market_context"]
}
```

Each contract must include:

- `contract_id`
- `version`
- `required`
- `source_types`
- `reject_when`

- [ ] **Step 5: Add new workflows**

Example `kis_morning_note.json`:

```json
{
  "workflow_id": "kis_morning_note",
  "version": 1,
  "scope": "kis",
  "model_policy": {
    "default_model": "settings.llm_model",
    "default_reasoning_effort": "settings.llm_reasoning_effort",
    "expected_runtime_model": "gpt-5.5",
    "expected_reasoning_effort": "xhigh"
  },
  "cadence": {"kind": "scheduled", "local_time": "08:30"},
  "required_skills": ["morning_note", "portfolio_balance", "catalyst_calendar", "evidence_audit"],
  "required_context": ["account", "blocks", "market_pulse", "reports", "memory", "calendar"],
  "output_contracts": ["morning_note_contract", "evidence_claim_contract"],
  "authority": {
    "can_read_untrusted_research": false,
    "can_write_memory": true,
    "can_create_blocks": false,
    "can_submit_orders": false
  },
  "safety_gates": ["market_calendar", "evidence_freshness", "no_order_submission"],
  "prompt_budget": {"max_skill_chars": 7200, "max_workflow_chars": 1600, "max_contract_chars": 5200}
}
```

Create the remaining workflows with these skill sets:

- `kis_idea_screen`: `idea_generation`, `sector_overview`, `valuation_frame`, `evidence_audit`
- `kis_symbol_deep_dive`: `thesis_tracker`, `valuation_frame`, `catalyst_calendar`, `block_design`, `evidence_audit`
- `kis_earnings_update`: `earnings_preview`, `earnings_analysis`, `model_update`, `thesis_tracker`
- `kis_sector_rotation`: `sector_overview`, `idea_generation`, `portfolio_balance`, `evidence_audit`
- `portfolio_rebalance`: `portfolio_balance`, `risk_sizing`, `valuation_frame`, `policy_revision`

- [ ] **Step 6: Run asset validation**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_workflow_manifests.py -q
.venv/bin/python3 scripts/check_jue_workflows.py
```

Expected: all pass.

---

## Task 3: Decision Lifecycle Packet v3

**Files:**
- Modify: `src/tradecraft/services/jue_decision_packet.py`
- Test: `tests/test_jue_decision_packet.py`

- [ ] **Step 1: Add failing tests for lifecycle packet**

Add:

```python
def test_decision_lifecycle_packet_links_research_to_block_implications() -> None:
    packet = build_decision_lifecycle_packet(
        stage="idea_screen",
        workflow_id="kis_idea_screen",
        artifacts=[
            {
                "artifact_id": "art_1",
                "artifact_type": "idea_screen",
                "symbol": "005930",
                "thesis": {"summary": "메모리 업사이클 기대"},
                "evidence": [{"source_type": "report", "source_id": "r1"}],
                "block_implications": [{"action": "watch_add", "horizon": "mid"}],
                "rejected_actions": [],
            }
        ],
    )

    assert packet["version"] == "decision_lifecycle_v3"
    assert packet["workflow_id"] == "kis_idea_screen"
    assert packet["artifact_count"] == 1
    assert packet["symbols"] == ["005930"]
    assert packet["block_implications"][0]["action"] == "watch_add"
```

```python
def test_decision_lifecycle_packet_rejects_vague_artifacts() -> None:
    packet = build_decision_lifecycle_packet(
        stage="idea_screen",
        workflow_id="kis_idea_screen",
        artifacts=[{"artifact_id": "bad", "symbol": "005930", "thesis": {}}],
    )

    assert packet["artifact_count"] == 0
    assert packet["rejected_artifacts"][0]["reason"] == "missing_evidence"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_decision_packet.py::test_decision_lifecycle_packet_links_research_to_block_implications tests/test_jue_decision_packet.py::test_decision_lifecycle_packet_rejects_vague_artifacts -q
```

Expected: fail because `build_decision_lifecycle_packet` is missing.

- [ ] **Step 3: Implement `build_decision_lifecycle_packet`**

Add:

```python
def build_decision_lifecycle_packet(
    *,
    stage: str,
    workflow_id: str,
    artifacts: list[dict[str, Any]] | None = None,
    max_artifacts: int = 12,
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in list(artifacts or [])[: max(max_artifacts, 1)]:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        implications = (
            row.get("block_implications")
            if isinstance(row.get("block_implications"), list)
            else []
        )
        if not symbol:
            rejected.append({"artifact_id": row.get("artifact_id"), "reason": "missing_symbol"})
            continue
        if not evidence:
            rejected.append({"artifact_id": row.get("artifact_id"), "symbol": symbol, "reason": "missing_evidence"})
            continue
        accepted.append({
            "artifact_id": str(row.get("artifact_id") or ""),
            "artifact_type": str(row.get("artifact_type") or stage),
            "symbol": symbol,
            "workflow_id": str(row.get("workflow_id") or workflow_id),
            "thesis": row.get("thesis") if isinstance(row.get("thesis"), dict) else {},
            "valuation": row.get("valuation") if isinstance(row.get("valuation"), dict) else {},
            "catalysts": list(row.get("catalysts") or [])[:6],
            "sector_context": row.get("sector_context") if isinstance(row.get("sector_context"), dict) else {},
            "portfolio_context": row.get("portfolio_context") if isinstance(row.get("portfolio_context"), dict) else {},
            "evidence": evidence[:8],
            "block_implications": implications[:6],
            "rejected_actions": list(row.get("rejected_actions") or [])[:6],
        })
    symbols = sorted({row["symbol"] for row in accepted if row.get("symbol")})
    return {
        "version": "decision_lifecycle_v3",
        "stage": str(stage or ""),
        "workflow_id": str(workflow_id or ""),
        "artifact_count": len(accepted),
        "symbols": symbols,
        "artifacts": accepted,
        "block_implications": [
            {**item, "symbol": row["symbol"], "artifact_id": row["artifact_id"]}
            for row in accepted
            for item in row.get("block_implications", [])
            if isinstance(item, dict)
        ][:20],
        "rejected_artifacts": rejected[:20],
    }
```

- [ ] **Step 4: Run packet tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_decision_packet.py -q
```

Expected: all pass.

---

## Task 4: Lifecycle Artifact Repository

**Files:**
- Create: `src/tradecraft/services/jue_lifecycle.py`
- Test: `tests/test_jue_lifecycle.py`

- [ ] **Step 1: Add repository tests**

Create `tests/test_jue_lifecycle.py`:

```python
from pathlib import Path

from tradecraft.services.jue_lifecycle import JueLifecycleRepository


def test_lifecycle_repository_upserts_and_lists_artifacts(tmp_path: Path) -> None:
    repo = JueLifecycleRepository(tmp_path / "memory.db")

    saved = repo.upsert_artifact(
        {
            "artifact_id": "art_1",
            "artifact_type": "morning_note",
            "workflow_id": "kis_morning_note",
            "symbol": "005930",
            "title": "삼성전자 장전 점검",
            "summary_md": "메모리 업황과 수급을 함께 점검한다.",
            "payload": {"block_implications": [{"action": "watch_add"}]},
            "evidence": [{"source_type": "report", "source_id": "r1"}],
            "status": "active",
        }
    )
    rows = repo.list_artifacts(symbols=["005930"], limit=5)

    assert saved["artifact_id"] == "art_1"
    assert rows[0]["workflow_id"] == "kis_morning_note"
    assert rows[0]["payload"]["block_implications"][0]["action"] == "watch_add"
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_lifecycle.py -q
```

Expected: fail because module is missing.

- [ ] **Step 3: Implement repository**

Create `src/tradecraft/services/jue_lifecycle.py` with:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


class JueLifecycleRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jue_lifecycle_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_type TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary_md TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jue_lifecycle_symbol ON jue_lifecycle_artifacts(symbol, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jue_lifecycle_workflow ON jue_lifecycle_artifacts(workflow_id, updated_at)"
            )

    def upsert_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("artifact_id required")
        row = {
            "artifact_id": artifact_id,
            "artifact_type": str(artifact.get("artifact_type") or "note"),
            "workflow_id": str(artifact.get("workflow_id") or ""),
            "symbol": str(artifact.get("symbol") or ""),
            "title": str(artifact.get("title") or ""),
            "summary_md": str(artifact.get("summary_md") or ""),
            "payload_json": _json_dumps(artifact.get("payload") or {}),
            "evidence_json": _json_dumps(artifact.get("evidence") or []),
            "status": str(artifact.get("status") or "active"),
            "created_at": str(artifact.get("created_at") or now),
            "updated_at": str(artifact.get("updated_at") or now),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jue_lifecycle_artifacts (
                    artifact_id, artifact_type, workflow_id, symbol, title,
                    summary_md, payload_json, evidence_json, status, created_at, updated_at
                ) VALUES (
                    :artifact_id, :artifact_type, :workflow_id, :symbol, :title,
                    :summary_md, :payload_json, :evidence_json, :status, :created_at, :updated_at
                )
                ON CONFLICT(artifact_id) DO UPDATE SET
                    artifact_type=excluded.artifact_type,
                    workflow_id=excluded.workflow_id,
                    symbol=excluded.symbol,
                    title=excluded.title,
                    summary_md=excluded.summary_md,
                    payload_json=excluded.payload_json,
                    evidence_json=excluded.evidence_json,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                row,
            )
        return self.get_artifact(artifact_id) or row

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jue_lifecycle_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        return _decode_row(row) if row else None

    def list_artifacts(
        self,
        *,
        symbols: list[str] | None = None,
        workflow_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = ["status='active'"]
        params: list[Any] = []
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if workflow_id:
            clauses.append("workflow_id=?")
            params.append(workflow_id)
        params.append(max(int(limit), 1))
        sql = (
            "SELECT * FROM jue_lifecycle_artifacts WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode_row(row) for row in rows]


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = _json_loads(data.pop("payload_json", "{}"), {})
    data["evidence"] = _json_loads(data.pop("evidence_json", "[]"), [])
    return data
```

- [ ] **Step 4: Run lifecycle repository tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_lifecycle.py -q
```

Expected: pass.

---

## Task 5: Memory Context Integration

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Add failing context-pack test**

Add:

```python
def test_context_pack_includes_lifecycle_artifacts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.lifecycle_repository.upsert_artifact(
        {
            "artifact_id": "life_005930",
            "artifact_type": "symbol_deep_dive",
            "workflow_id": "kis_symbol_deep_dive",
            "symbol": "005930",
            "title": "삼성전자 딥다이브",
            "summary_md": "메모리 업황은 중기 thesis 확인 대상이다.",
            "payload": {"block_implications": [{"action": "watch_add", "horizon": "mid"}]},
            "evidence": [{"source_type": "report", "source_id": "r1"}],
        }
    )

    pack = service.context_pack(symbols=["005930"], max_chars=8000)

    assert pack["lifecycle_artifacts"][0]["workflow_id"] == "kis_symbol_deep_dive"
    assert pack["lifecycle_artifacts"][0]["symbol"] == "005930"
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_investment_memory.py::test_context_pack_includes_lifecycle_artifacts -q
```

Expected: fail because `lifecycle_repository` is missing.

- [ ] **Step 3: Wire lifecycle repository into InvestmentMemoryService**

Import:

```python
from tradecraft.services.jue_lifecycle import JueLifecycleRepository
```

In `InvestmentMemoryService.__init__`, add:

```python
self.lifecycle_repository = JueLifecycleRepository(self.config.db_path)
```

In `context_pack`, after `active_insights`, load artifacts:

```python
lifecycle_artifacts = self.lifecycle_repository.list_artifacts(
    symbols=symbols or [],
    limit=12,
)
```

Add compact field:

```python
"lifecycle_artifacts": [
    {
        "artifact_id": row.get("artifact_id"),
        "artifact_type": row.get("artifact_type"),
        "workflow_id": row.get("workflow_id"),
        "symbol": row.get("symbol"),
        "title": _truncate(row.get("title"), 120),
        "summary_md": _truncate(row.get("summary_md"), 600),
        "block_implications": list((row.get("payload") or {}).get("block_implications") or [])[:4],
        "evidence_count": len(row.get("evidence") or []),
        "updated_at": row.get("updated_at"),
    }
    for row in lifecycle_artifacts
],
```

In `_enforce_context_pack_budget`, clear `lifecycle_artifacts` when over budget.

- [ ] **Step 4: Run memory tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_investment_memory.py::test_context_pack_includes_lifecycle_artifacts tests/test_investment_memory.py -q
```

Expected: pass.

---

## Task 6: KIS Manager Prompt Integration

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Add failing prompt test**

Add:

```python
def test_kis_manager_prompt_includes_decision_lifecycle_packet(tmp_path: Path) -> None:
    trader = _make_trader(tmp_path)
    trader.memory_service.lifecycle_repository.upsert_artifact(
        {
            "artifact_id": "life_005930",
            "artifact_type": "symbol_deep_dive",
            "workflow_id": "kis_symbol_deep_dive",
            "symbol": "005930",
            "title": "삼성전자 딥다이브",
            "summary_md": "중기 thesis 확인 대상",
            "payload": {"block_implications": [{"action": "watch_add", "horizon": "mid"}]},
            "evidence": [{"source_type": "report", "source_id": "r1"}],
        }
    )

    prompt = trader._build_manager_prompt(
        account={"cash_krw": 1_000_000, "positions": []},
        blocks=[],
        quotes={"005930": {"symbol": "005930", "price": 70000}},
        recent_events=[],
        previous_manager_runs=[],
    )

    assert prompt["decision_lifecycle_v3"]["version"] == "decision_lifecycle_v3"
    assert prompt["decision_lifecycle_v3"]["symbols"] == ["005930"]
```

Adjust helper names to match the existing test factory in `tests/test_kis_block_trader.py`.

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_kis_block_trader.py::test_kis_manager_prompt_includes_decision_lifecycle_packet -q
```

Expected: fail because prompt lacks `decision_lifecycle_v3`.

- [ ] **Step 3: Build lifecycle packet in KIS prompt**

Import:

```python
from tradecraft.services.jue_decision_packet import (
    build_decision_packet,
    build_decision_lifecycle_packet,
)
```

When building the manager prompt, after memory context is available:

```python
lifecycle_artifacts = list((investment_memory or {}).get("lifecycle_artifacts") or [])
decision_lifecycle_v3 = build_decision_lifecycle_packet(
    stage="manager_run",
    workflow_id="kis_intraday_manager",
    artifacts=lifecycle_artifacts,
)
```

Add to prompt:

```python
"decision_lifecycle_v3": decision_lifecycle_v3,
```

Also pass `decision_lifecycle_v3` into memory context provider only if the provider accepts context dicts; do not break existing call signatures.

- [ ] **Step 4: Run KIS tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_kis_block_trader.py -q
```

Expected: pass.

---

## Task 7: API and UI Visibility

**Files:**
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_api_smoke.py`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add API tests**

Add:

```python
def test_jue_source_manifest_status_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")

    with TestClient(app) as client:
        unauthorized = client.get("/api/jue/source-manifest")
        response = client.get(
            "/api/jue/source-manifest",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["source_id"] == "financial_services"
```

```python
def test_jue_lifecycle_latest_requires_auth(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")

    with TestClient(app) as client:
        unauthorized = client.get("/api/jue/lifecycle/latest")
        response = client.get(
            "/api/jue/lifecycle/latest",
            headers={"Authorization": "Bearer test-admin"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert "items" in response.json()
```

- [ ] **Step 2: Implement API endpoints**

In `src/tradecraft/main.py`:

```python
from tradecraft.services.jue_lifecycle import JueLifecycleRepository
```

Add:

```python
@app.get("/api/jue/source-manifest")
async def jue_source_manifest(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return JueSkillRegistry().load_source_manifest("financial_services")
```

```python
@app.get("/api/jue/lifecycle/latest")
async def jue_lifecycle_latest(
    symbol: str | None = None,
    workflow_id: str | None = None,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    repo = JueLifecycleRepository(settings.investment_memory_db_path)
    symbols = [symbol] if symbol else None
    items = repo.list_artifacts(symbols=symbols, workflow_id=workflow_id, limit=30)
    return {"status": "ok", "items": items, "count": len(items)}
```

- [ ] **Step 3: Add UI static tests**

Add to `tests/test_static_ui.py`:

```python
def test_ui_exposes_jue_source_manifest_and_lifecycle() -> None:
    js = _js()

    assert '"/jue/source-manifest"' in js
    assert '"/jue/lifecycle/latest"' in js
    assert "function renderJueLifecycleArtifacts" in js
```

- [ ] **Step 4: Implement UI panel**

In `app.js`, add state:

```js
jueLifecycle: {
  manifest: null,
  artifacts: null,
  loading: false,
  error: "",
},
```

Add loaders:

```js
async function loadJueLifecycle() {
  state.jueLifecycle.loading = true;
  state.jueLifecycle.error = "";
  renderHelperAgent();
  try {
    const [manifest, artifacts] = await Promise.all([
      getJSON("/jue/source-manifest"),
      getJSON("/jue/lifecycle/latest"),
    ]);
    state.jueLifecycle.manifest = manifest;
    state.jueLifecycle.artifacts = artifacts;
  } catch (error) {
    state.jueLifecycle.error = getErrorMessage(error);
  } finally {
    state.jueLifecycle.loading = false;
    renderHelperAgent();
  }
}
```

Add renderer:

```js
function renderJueLifecycleArtifacts() {
  const artifacts = Array.isArray(state.jueLifecycle.artifacts?.items)
    ? state.jueLifecycle.artifacts.items
    : [];
  return `
    <section class="settings-workflows">
      <div class="settings-workflows-head">
        <div>
          <span class="section-kicker">Jue Lifecycle</span>
          <h4>분석 생애주기 기록</h4>
          <p>아이디어, 논지, 밸류, 섹터, 이벤트, 블록 영향이 저장되는 공간입니다.</p>
        </div>
      </div>
      <div class="jue-workflow-grid">
        ${artifacts.slice(0, 8).map((row) => `
          <article class="jue-workflow-card">
            <h4>${escapeHTML(row.title || row.artifact_id)}</h4>
            <p>${escapeHTML(row.workflow_id || "")} · ${escapeHTML(row.symbol || "")}</p>
            <p>${escapeHTML(row.summary_md || "")}</p>
          </article>
        `).join("") || '<div class="notice">아직 lifecycle 기록이 없습니다.</div>'}
      </div>
    </section>
  `;
}
```

Include this renderer in the memory tab or settings tab, not as a new top-level tab.

- [ ] **Step 5: Run API/UI checks**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_api_smoke.py::test_jue_source_manifest_status_requires_auth tests/test_api_smoke.py::test_jue_lifecycle_latest_requires_auth tests/test_static_ui.py::test_ui_exposes_jue_source_manifest_and_lifecycle -q
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

---

## Task 8: Docs and Final Verification

**Files:**
- Modify: `docs/spec/05_llm_system.md`
- Modify: `docs/spec/08_research_memory.md`
- Modify: `docs/spec/09_strategy_intelligence.md`
- Modify: `docs/spec/16_refactor_roadmap.md`
- Test: `tests/test_docs_spec.py`

- [ ] **Step 1: Add docs tests**

Extend `tests/test_docs_spec.py`:

```python
def test_spec_documents_jue_lifecycle_layer() -> None:
    llm = _doc("05_llm_system.md")
    memory = _doc("08_research_memory.md")
    strategy = _doc("09_strategy_intelligence.md")

    assert "Decision Lifecycle v3" in llm
    assert "jue_lifecycle_artifacts" in memory
    assert "kis_idea_screen" in strategy
```

- [ ] **Step 2: Update docs**

Add sections:

- `05_llm_system.md`: Decision Lifecycle v3 and workflow provenance.
- `08_research_memory.md`: lifecycle artifacts stored with memory provenance.
- `09_strategy_intelligence.md`: how idea screens and sector rotation feed strategy candidates.
- `16_refactor_roadmap.md`: future boundary cleanup around lifecycle artifacts.

- [ ] **Step 3: Run focused full verification**

Run:

```bash
.venv/bin/python3 scripts/check_jue_workflows.py
.venv/bin/python3 -m pytest tests/test_jue_skill_registry.py tests/test_jue_workflow_manifests.py tests/test_jue_decision_packet.py tests/test_jue_lifecycle.py tests/test_investment_memory.py tests/test_kis_block_trader.py tests/test_api_smoke.py tests/test_static_ui.py tests/test_docs_spec.py -q
node --check src/tradecraft/web/static/app.js
.venv/bin/python3 -m ruff check src/tradecraft/services/jue_skill_registry.py src/tradecraft/services/jue_decision_packet.py src/tradecraft/services/jue_lifecycle.py src/tradecraft/services/investment_memory.py src/tradecraft/services/kis_block_trader.py src/tradecraft/main.py tests/test_jue_lifecycle.py tests/test_jue_decision_packet.py tests/test_investment_memory.py tests/test_kis_block_trader.py tests/test_api_smoke.py tests/test_static_ui.py tests/test_docs_spec.py
git diff --check
```

Expected:

- workflow checker OK
- pytest pass
- JS syntax OK
- ruff pass
- diff check clean

- [ ] **Step 4: Runtime apply checklist**

After implementation passes, restart:

```bash
.venv/bin/python3 - <<'PY'
from tradecraft.runtime.process_status import restart_runner_processes
print(restart_runner_processes([
    "control",
    "runtime",
    "kis_block_trader",
    "investment_memory",
    "market_judge",
    "market_pulse",
    "binance_block_trader",
    "crypto_market_research",
    "crypto_alpha",
    "strategy_insights",
], delay_sec=0.5))
PY
```

Then verify:

```bash
curl -sS http://127.0.0.1:18080/api/health
.venv/bin/python3 scripts/check_jue_workflows.py
```

Expected:

- health returns `{"status":"ok", ...}`
- workflow check OK
- new manager runs after restart include workflow provenance and lifecycle packets.
