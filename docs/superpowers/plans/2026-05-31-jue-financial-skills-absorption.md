# Jue Financial Skills Absorption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Jue's internal decision layer around explicit financial skills, workflow manifests, output contracts, and evidence-linked memory so KIS Jue and Binance Jue become more institutional, self-improving trading partners rather than prompt-heavy ad hoc managers.

**Architecture:** Add a file-based `jue` domain layer containing skill markdown, workflow JSON manifests, and lightweight contract definitions inspired by Anthropic's `financial-services` repository. A new registry/loader compiles the right skill pack for each workflow, validates references, enforces prompt budgets, and injects compact skill guidance into KIS, Binance, research, memory, and reflection services. Existing block traders, memory DBs, evidence policies, LLM usage telemetry, and UI tabs are reused; the change is a structural spine, not a detached lab.

**Tech Stack:** Python 3.10, SQLite, FastAPI, static JavaScript/CSS, pytest, existing CodexNativeRuntime, existing HERMES runtime runners, no new runtime dependency.

---

## Strategic Direction

Anthropic's `financial-services` repository should be treated as a reference architecture, not a drop-in dependency.

The reusable ideas are:

- Skills are first-class reusable operating procedures, not casual prompt snippets.
- Workflows declare which skills, data sources, tools, and outputs they require.
- Untrusted external content is read by restricted readers and converted into structured facts before it can influence decisions.
- Write/execution authority is isolated from research/reading authority.
- Handoffs and generated outputs are validated by allowlists and schemas.
- Every artifact is traceable to source material, workflow, and worker role.

Jue should absorb these ideas as:

- `jue_skills`: how Jue thinks.
- `jue_workflows`: when and why Jue thinks.
- `jue_contracts`: what Jue is allowed to output.
- `jue_evidence`: what Jue must cite before acting.
- `jue_memory`: what Jue learns after acting.
- `jue_ops`: how the operator verifies Jue is using the right brain.

This plan does not copy full upstream skill text into HERMES. It creates HERMES-native skill markdown with source attribution links and project-specific trading semantics.

---

## Current HERMES Pieces To Reuse

- `src/tradecraft/services/kis_block_trader.py`
  - KIS block manager, waiting-entry blocks, horizon policy, creative hypothesis loop, policy rule evaluation.
- `src/tradecraft/services/binance_block_trader.py`
  - Binance block manager, crypto price plan, spot/futures lanes, execution gates, quant/research integration.
- `src/tradecraft/services/investment_memory.py`
  - Persona, journals, seed memory, block reflections, policy scorecards, versioned policy rules, context packing.
- `src/tradecraft/services/evidence_policy.py`
  - Evidence normalization and decision-packet helpers.
- `src/tradecraft/services/jue_decision_packet.py`
  - Normalized account/block/quote pressure packets.
- `src/tradecraft/services/strategy_intelligence.py`
  - KIS strategy candidates and suitability.
- `src/tradecraft/services/crypto_market_research.py`
  - Crypto candidate generation and LLM research notes.
- `src/tradecraft/services/crypto_quant.py`
  - Crypto time-series/quant signals.
- `src/tradecraft/services/crypto_pattern_lab.py`
  - Strategy pattern extraction and scorecards.
- `src/tradecraft/services/llm_usage.py`
  - LLM usage telemetry.
- `src/tradecraft/web/static/*`
  - Existing static UI.

---

## New File Structure

Create:

- `src/tradecraft/jue/__init__.py`
  - Jue domain package marker.
- `src/tradecraft/jue/skills/idea_generation.md`
  - Candidate discovery procedure for KIS and crypto.
- `src/tradecraft/jue/skills/thesis_tracker.md`
  - Symbol/block thesis lifecycle procedure.
- `src/tradecraft/jue/skills/catalyst_calendar.md`
  - Event/catalyst scanning and outcome archiving procedure.
- `src/tradecraft/jue/skills/block_design.md`
  - Executable block design procedure.
- `src/tradecraft/jue/skills/risk_sizing.md`
  - Position sizing and risk budget procedure.
- `src/tradecraft/jue/skills/execution_review.md`
  - Post-trade quality review procedure.
- `src/tradecraft/jue/skills/portfolio_balance.md`
  - Cash, horizon, ETF, symbol concentration, and account balance procedure.
- `src/tradecraft/jue/skills/crypto_market_sweep.md`
  - Binance-specific 24h market sweep procedure.
- `src/tradecraft/jue/skills/evidence_audit.md`
  - Evidence freshness, source strength, contradiction, and data-gap procedure.
- `src/tradecraft/jue/skills/policy_revision.md`
  - Reflection-to-policy update procedure.
- `src/tradecraft/jue/workflows/kis_pre_open.json`
  - KIS 08:30 pre-open workflow.
- `src/tradecraft/jue/workflows/kis_intraday_manager.json`
  - KIS regular-market block manager workflow.
- `src/tradecraft/jue/workflows/kis_post_close.json`
  - KIS 15:45 review/reflection workflow.
- `src/tradecraft/jue/workflows/binance_cycle.json`
  - Binance recurring spot/futures manager workflow.
- `src/tradecraft/jue/workflows/crypto_research.json`
  - Crypto market research and candidate generation workflow.
- `src/tradecraft/jue/workflows/instant_symbol_analysis.json`
  - User-bought or manually requested symbol analysis workflow.
- `src/tradecraft/jue/workflows/block_reflection.json`
  - Closed/error/stale block reflection workflow.
- `src/tradecraft/jue/contracts/block_action_contract.json`
  - Required fields and validation rules for block actions.
- `src/tradecraft/jue/contracts/evidence_claim_contract.json`
  - Required fields for source-linked claims.
- `src/tradecraft/jue/contracts/thesis_update_contract.json`
  - Required fields for thesis updates.
- `src/tradecraft/jue/contracts/reflection_contract.json`
  - Required fields for block reflection outputs.
- `src/tradecraft/jue/contracts/policy_revision_contract.json`
  - Required fields for policy revision outputs.
- `src/tradecraft/services/jue_skill_registry.py`
  - Skill/workflow/contract loader, validator, and prompt compiler.
- `scripts/check_jue_workflows.py`
  - Local validation for Jue skill/workflow/contract reference integrity.
- `tests/test_jue_skill_registry.py`
  - Unit tests for loader, validation, and prompt compilation.
- `tests/test_jue_workflow_manifests.py`
  - Manifest and contract integrity tests.

Modify:

- `src/tradecraft/services/kis_block_trader.py`
  - Inject `kis_intraday_manager` skill pack into prompt.
- `src/tradecraft/services/binance_block_trader.py`
  - Inject `binance_cycle` skill pack into prompt.
- `src/tradecraft/services/crypto_market_research.py`
  - Inject `crypto_research` skill pack into prompt.
- `src/tradecraft/services/investment_memory.py`
  - Inject `block_reflection`, `kis_pre_open`, `kis_post_close`, and `policy_revision` skill packs into ritual/reflection contexts.
- `src/tradecraft/main.py`
  - Add admin-protected workflow/skill status endpoint.
- `src/tradecraft/web/static/index.html`
  - Add compact "쥬 운영체계" settings section.
- `src/tradecraft/web/static/app.js`
  - Fetch and render active workflows, skills, contracts, prompt budget, and validation state.
- `src/tradecraft/web/static/style.css`
  - Add styles for workflow cards and skill chips.
- `docs/spec/05_llm_system.md`
  - Document the Jue skill/workflow injection model.
- `docs/spec/08_research_memory.md`
  - Document how skill outputs become memory.
- `docs/spec/16_refactor_roadmap.md`
  - Add this as the next structural refactor milestone.

---

## Data Contracts

### Skill Markdown Frontmatter

Every skill file must start with this shape:

```markdown
---
skill_id: idea_generation
name: Idea Generation
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md
required_outputs:
  - candidate_screen
  - hypothesis
  - data_gaps
max_prompt_chars: 1800
---
```

No YAML dependency is introduced. `jue_skill_registry.py` parses this limited frontmatter with a small deterministic parser:

- only string keys, integer `version`, and list-of-string fields are supported;
- unsupported structures raise `JueSkillValidationError`;
- the markdown body after frontmatter is treated as prompt content.

### Workflow Manifest

Each workflow is JSON:

```json
{
  "workflow_id": "kis_intraday_manager",
  "version": 1,
  "scope": "kis",
  "model_policy": {
    "default_model": "settings.llm_model",
    "default_reasoning_effort": "settings.llm_reasoning_effort",
    "expected_runtime_model": "gpt-5.5",
    "expected_reasoning_effort": "xhigh"
  },
  "cadence": {
    "kind": "market_hours",
    "interval_sec": 1800
  },
  "required_skills": [
    "thesis_tracker",
    "block_design",
    "risk_sizing",
    "execution_review",
    "portfolio_balance",
    "evidence_audit"
  ],
  "required_context": [
    "account",
    "blocks",
    "quotes",
    "strategy",
    "investment_memory",
    "decision_packet_v2"
  ],
  "output_contracts": [
    "block_action_contract",
    "thesis_update_contract"
  ],
  "authority": {
    "can_read_untrusted_research": false,
    "can_write_memory": true,
    "can_create_blocks": true,
    "can_submit_orders": false
  },
  "safety_gates": [
    "kill_switch",
    "cash_available",
    "position_available",
    "duplicate_order",
    "rate_limit",
    "executable_price_structure"
  ],
  "prompt_budget": {
    "max_skill_chars": 5200,
    "max_workflow_chars": 1600,
    "max_contract_chars": 2600
  }
}
```

### Compiled Prompt Pack

`JueSkillRegistry.compile_prompt_pack("kis_intraday_manager")` returns:

```python
{
    "workflow_id": "kis_intraday_manager",
    "workflow_version": 1,
    "scope": "kis",
    "skills": [
        {"skill_id": "block_design", "version": 1, "content_md": "..."},
    ],
    "contracts": [
        {"contract_id": "block_action_contract", "version": 1, "content": {...}},
    ],
    "authority": {"can_create_blocks": True, "can_submit_orders": False},
    "safety_gates": ["kill_switch", "cash_available", "executable_price_structure"],
    "prompt_budget": {"used_skill_chars": 4312, "max_skill_chars": 5200},
}
```

This pack is appended to the existing manager prompt under the key `jue_workflow`.

---

## Task 1: Create Jue Skill Registry And Validation Errors

**Files:**

- Create: `src/tradecraft/jue/__init__.py`
- Create: `src/tradecraft/services/jue_skill_registry.py`
- Test: `tests/test_jue_skill_registry.py`

- [ ] **Step 1: Write failing tests for skill parsing**

Create `tests/test_jue_skill_registry.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.services.jue_skill_registry import (
    JueSkillRegistry,
    JueSkillValidationError,
    parse_skill_markdown,
)


def test_parse_skill_markdown_extracts_frontmatter_and_body() -> None:
    text = """---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
max_prompt_chars: 1200
---
# Block Design

Always produce entry, target, stop, horizon, sizing reason, and invalidation.
"""

    skill = parse_skill_markdown(text)

    assert skill["skill_id"] == "block_design"
    assert skill["name"] == "Block Design"
    assert skill["version"] == 1
    assert skill["scope"] == "shared"
    assert skill["source_inspiration"] == ["https://github.com/anthropics/financial-services"]
    assert skill["required_outputs"] == ["executable_block"]
    assert "Always produce entry" in skill["content_md"]


def test_parse_skill_markdown_rejects_missing_required_keys() -> None:
    text = """---
skill_id: broken
version: 1
---
# Broken
"""

    with pytest.raises(JueSkillValidationError, match="missing required skill metadata"):
        parse_skill_markdown(text)


def test_registry_loads_skills_workflows_and_contracts(tmp_path: Path) -> None:
    root = tmp_path / "jue"
    (root / "skills").mkdir(parents=True)
    (root / "workflows").mkdir()
    (root / "contracts").mkdir()
    (root / "skills" / "block_design.md").write_text(
        """---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
max_prompt_chars: 1200
---
# Block Design

Produce executable blocks.
""",
        encoding="utf-8",
    )
    (root / "contracts" / "block_action_contract.json").write_text(
        '{"contract_id":"block_action_contract","version":1,"required":["symbol","target_price","stop_price"]}',
        encoding="utf-8",
    )
    (root / "workflows" / "kis_intraday_manager.json").write_text(
        """{
          "workflow_id": "kis_intraday_manager",
          "version": 1,
          "scope": "kis",
          "model_policy": {
            "default_model": "settings.llm_model",
            "default_reasoning_effort": "settings.llm_reasoning_effort",
            "expected_runtime_model": "gpt-5.5",
            "expected_reasoning_effort": "xhigh"
          },
          "cadence": {"kind": "market_hours", "interval_sec": 1800},
          "required_skills": ["block_design"],
          "required_context": ["account", "blocks"],
          "output_contracts": ["block_action_contract"],
          "authority": {"can_read_untrusted_research": false, "can_write_memory": true, "can_create_blocks": true, "can_submit_orders": false},
          "safety_gates": ["kill_switch", "executable_price_structure"],
          "prompt_budget": {"max_skill_chars": 2000, "max_workflow_chars": 1200, "max_contract_chars": 1200}
        }""",
        encoding="utf-8",
    )

    registry = JueSkillRegistry(root=root)
    pack = registry.compile_prompt_pack("kis_intraday_manager")

    assert pack["workflow_id"] == "kis_intraday_manager"
    assert pack["skills"][0]["skill_id"] == "block_design"
    assert pack["contracts"][0]["contract_id"] == "block_action_contract"
    assert pack["authority"]["can_submit_orders"] is False
    assert "executable_price_structure" in pack["safety_gates"]
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_skill_registry.py -q
```

Expected: import failure because `jue_skill_registry.py` does not exist.

- [ ] **Step 3: Implement the registry**

Create `src/tradecraft/jue/__init__.py` with:

```python
"""Jue skill, workflow, and contract assets."""
```

Create `src/tradecraft/services/jue_skill_registry.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


REQUIRED_SKILL_KEYS = {
    "skill_id",
    "name",
    "version",
    "scope",
    "source_inspiration",
    "required_outputs",
    "max_prompt_chars",
}
REQUIRED_WORKFLOW_KEYS = {
    "workflow_id",
    "version",
    "scope",
    "model_policy",
    "cadence",
    "required_skills",
    "required_context",
    "output_contracts",
    "authority",
    "safety_gates",
    "prompt_budget",
}


class JueSkillValidationError(ValueError):
    """Raised when Jue skill/workflow assets are malformed."""


@dataclass(frozen=True)
class JueSkillRegistry:
    root: Path = Path("src/tradecraft/jue")

    def load_skill(self, skill_id: str) -> dict[str, Any]:
        path = self.root / "skills" / f"{skill_id}.md"
        if not path.exists():
            raise JueSkillValidationError(f"skill not found: {skill_id}")
        return parse_skill_markdown(path.read_text(encoding="utf-8"))

    def load_contract(self, contract_id: str) -> dict[str, Any]:
        path = self.root / "contracts" / f"{contract_id}.json"
        if not path.exists():
            raise JueSkillValidationError(f"contract not found: {contract_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("contract_id") != contract_id:
            raise JueSkillValidationError(f"contract id mismatch: {contract_id}")
        if int(data.get("version") or 0) <= 0:
            raise JueSkillValidationError(f"contract version missing: {contract_id}")
        return data

    def load_workflow(self, workflow_id: str) -> dict[str, Any]:
        path = self.root / "workflows" / f"{workflow_id}.json"
        if not path.exists():
            raise JueSkillValidationError(f"workflow not found: {workflow_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = sorted(REQUIRED_WORKFLOW_KEYS - set(data))
        if missing:
            raise JueSkillValidationError(
                f"missing required workflow metadata for {workflow_id}: {', '.join(missing)}"
            )
        if data.get("workflow_id") != workflow_id:
            raise JueSkillValidationError(f"workflow id mismatch: {workflow_id}")
        return data

    def compile_prompt_pack(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.load_workflow(workflow_id)
        budget = workflow.get("prompt_budget") or {}
        max_skill_chars = max(int(budget.get("max_skill_chars") or 0), 1)
        max_contract_chars = max(int(budget.get("max_contract_chars") or 0), 1)
        skills = [self.load_skill(skill_id) for skill_id in workflow.get("required_skills") or []]
        contracts = [
            self.load_contract(contract_id)
            for contract_id in workflow.get("output_contracts") or []
        ]
        compact_skills = _compact_skills(skills, max_chars=max_skill_chars)
        compact_contracts = _compact_contracts(contracts, max_chars=max_contract_chars)
        return {
            "workflow_id": workflow["workflow_id"],
            "workflow_version": workflow["version"],
            "scope": workflow["scope"],
            "model_policy": workflow["model_policy"],
            "cadence": workflow["cadence"],
            "required_context": workflow["required_context"],
            "skills": compact_skills,
            "contracts": compact_contracts,
            "authority": workflow["authority"],
            "safety_gates": workflow["safety_gates"],
            "prompt_budget": {
                **budget,
                "used_skill_chars": sum(len(row.get("content_md") or "") for row in compact_skills),
                "used_contract_chars": len(json.dumps(compact_contracts, ensure_ascii=False)),
            },
        }


def parse_skill_markdown(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        raise JueSkillValidationError("skill frontmatter missing")
    try:
        _, frontmatter, body = text.split("---", 2)
    except ValueError as exc:
        raise JueSkillValidationError("skill frontmatter malformed") from exc
    meta = _parse_limited_frontmatter(frontmatter)
    missing = sorted(REQUIRED_SKILL_KEYS - set(meta))
    if missing:
        raise JueSkillValidationError(
            f"missing required skill metadata: {', '.join(missing)}"
        )
    meta["version"] = int(meta["version"])
    meta["max_prompt_chars"] = int(meta["max_prompt_chars"])
    meta["content_md"] = body.strip()
    return meta


def _parse_limited_frontmatter(frontmatter: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key = ""
    for raw_line in frontmatter.strip().splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - "):
            if not current_list_key:
                raise JueSkillValidationError("frontmatter list item without key")
            result.setdefault(current_list_key, [])
            result[current_list_key].append(line[4:].strip())
            continue
        if ":" not in line:
            raise JueSkillValidationError(f"frontmatter line malformed: {line}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        current_list_key = ""
        if raw_value == "":
            result[key] = []
            current_list_key = key
        else:
            result[key] = raw_value
    return result


def _compact_skills(skills: list[dict[str, Any]], *, max_chars: int) -> list[dict[str, Any]]:
    remaining = max_chars
    rows: list[dict[str, Any]] = []
    for skill in skills:
        content = str(skill.get("content_md") or "")
        allowed = max(min(int(skill.get("max_prompt_chars") or remaining), remaining), 0)
        compact_content = content[:allowed]
        remaining -= len(compact_content)
        rows.append(
            {
                "skill_id": skill["skill_id"],
                "name": skill["name"],
                "version": skill["version"],
                "scope": skill["scope"],
                "required_outputs": skill["required_outputs"],
                "source_inspiration": skill["source_inspiration"],
                "content_md": compact_content,
            }
        )
        if remaining <= 0:
            break
    return rows


def _compact_contracts(contracts: list[dict[str, Any]], *, max_chars: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    remaining = max_chars
    for contract in contracts:
        text = json.dumps(contract, ensure_ascii=False, sort_keys=True)
        if len(text) > remaining:
            rows.append(
                {
                    "contract_id": contract["contract_id"],
                    "version": contract["version"],
                    "truncated": True,
                    "required": contract.get("required") or [],
                }
            )
            break
        rows.append(contract)
        remaining -= len(text)
    return rows
```

- [ ] **Step 4: Run registry tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_skill_registry.py -q
```

Expected: PASS.

---

## Task 2: Add Jue-Native Skill Markdown Pack

**Files:**

- Create: `src/tradecraft/jue/skills/idea_generation.md`
- Create: `src/tradecraft/jue/skills/thesis_tracker.md`
- Create: `src/tradecraft/jue/skills/catalyst_calendar.md`
- Create: `src/tradecraft/jue/skills/block_design.md`
- Create: `src/tradecraft/jue/skills/risk_sizing.md`
- Create: `src/tradecraft/jue/skills/execution_review.md`
- Create: `src/tradecraft/jue/skills/portfolio_balance.md`
- Create: `src/tradecraft/jue/skills/crypto_market_sweep.md`
- Create: `src/tradecraft/jue/skills/evidence_audit.md`
- Create: `src/tradecraft/jue/skills/policy_revision.md`
- Test: `tests/test_jue_workflow_manifests.py`

- [ ] **Step 1: Write failing tests for required skill IDs**

Create `tests/test_jue_workflow_manifests.py`:

```python
from __future__ import annotations

from pathlib import Path

from tradecraft.services.jue_skill_registry import JueSkillRegistry


JUE_ROOT = Path("src/tradecraft/jue")
REQUIRED_SKILLS = {
    "idea_generation",
    "thesis_tracker",
    "catalyst_calendar",
    "block_design",
    "risk_sizing",
    "execution_review",
    "portfolio_balance",
    "crypto_market_sweep",
    "evidence_audit",
    "policy_revision",
}


def test_required_jue_skills_exist_and_parse() -> None:
    registry = JueSkillRegistry(root=JUE_ROOT)

    for skill_id in sorted(REQUIRED_SKILLS):
        skill = registry.load_skill(skill_id)
        assert skill["skill_id"] == skill_id
        assert skill["version"] >= 1
        assert skill["content_md"].strip()
        assert skill["source_inspiration"]
        assert skill["required_outputs"]


def test_jue_skills_do_not_reintroduce_old_disclaimer_identity() -> None:
    forbidden = [
        "매매 추천 아님",
        "정보 제공용",
        "financial advice",
        "not recommendation",
        "not recommendations",
    ]
    for path in sorted((JUE_ROOT / "skills").glob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase.lower() not in text, f"{path} contains forbidden phrase: {phrase}"
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_workflow_manifests.py::test_required_jue_skills_exist_and_parse -q
```

Expected: FAIL because skill files do not exist.

- [ ] **Step 3: Create skill markdown files**

Create the files with these responsibilities:

`src/tradecraft/jue/skills/idea_generation.md`

```markdown
---
skill_id: idea_generation
name: Idea Generation
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md
required_outputs:
  - candidate_screen
  - hypothesis
  - reject_reason
  - data_gaps
max_prompt_chars: 1800
---
# Idea Generation

Jue must search beyond obvious movers. For every discovery pass, classify candidates by style: value, growth, quality, event, theme, pullback, breakout, sector rotation, ETF/core allocation, and contrarian recovery.

For each candidate, produce:
- symbol and name
- style bucket
- current catalyst or pattern
- why this could be mispriced now
- what would prove the idea wrong
- whether the right action is create-now block, waiting-entry block, watch note, or reject

Screens surface candidates, not conclusions. A candidate becomes a block only when block_design and risk_sizing can produce an executable structure.
```

`src/tradecraft/jue/skills/thesis_tracker.md`

```markdown
---
skill_id: thesis_tracker
name: Thesis Tracker
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md
required_outputs:
  - thesis_statement
  - thesis_pillars
  - invalidation
  - conviction_delta
max_prompt_chars: 1700
---
# Thesis Tracker

Every active position, waiting block, and special-watch user holding needs a falsifiable thesis. Track what must happen for the block to work, what would weaken it, and which new data changed the view.

For each update, record:
- original thesis
- current evidence that strengthens it
- current evidence that weakens it
- catalysts still pending
- thesis impact: strengthen, neutral, weaken, broken
- action implication: hold, add, wait, tighten risk, loosen runner, close, or avoid new block

Do not call a trade good or bad only from PnL. Separate thesis quality, entry quality, execution quality, and market regime.
```

`src/tradecraft/jue/skills/catalyst_calendar.md`

```markdown
---
skill_id: catalyst_calendar
name: Catalyst Calendar
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/catalyst-calendar/SKILL.md
required_outputs:
  - catalyst_event
  - expected_impact
  - event_risk
  - follow_up_check
max_prompt_chars: 1500
---
# Catalyst Calendar

Jue must connect price action to scheduled or plausible catalysts. For KIS, include earnings, sector policy, supply chain news, institutional/foreign flow, index/ETF rotation, and macro events. For Binance, include funding, unlocks, listing news, ETF/market structure, major protocol events, and volatility regime changes.

For each event, record:
- event date or expected window
- affected symbols or sectors
- impact level: high, medium, low
- expected direction or uncertainty
- pre-event positioning idea
- post-event review requirement

Archive past catalysts with the actual outcome so memory can learn which catalyst classes have worked.
```

`src/tradecraft/jue/skills/block_design.md`

```markdown
---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
  - price_structure
  - invalidation
  - override_reason
max_prompt_chars: 1900
---
# Block Design

A block is not an opinion. A block is an executable structure with independent identity, time horizon, size, entry rule, target rule, stop rule, thesis, risk note, and invalidation.

Required block fields:
- symbol
- market or account scope
- side when relevant
- horizon: short, mid, long, core_etf, futures, or cash
- quantity or quote budget
- entry style: immediate/aggressive_limit or wait_for_price
- entry trigger price and operator for waiting blocks
- target price
- stop price
- thesis
- risk note
- confidence
- evidence references

Calculated price plans are defaults. Jue may override them only when it carries the calculated inputs and writes an override reason that mentions the evidence or market microstructure that justifies the change.
```

`src/tradecraft/jue/skills/risk_sizing.md`

```markdown
---
skill_id: risk_sizing
name: Risk Sizing
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - size_reason
  - risk_budget
  - exposure_check
  - rejection_reason
max_prompt_chars: 1600
---
# Risk Sizing

Jue sizes blocks from account pressure, horizon, confidence, evidence quality, volatility, stop distance, concentration, and current open risk. Quantity is a decision output, not a fixed one-share habit.

Risk sizing must check:
- available cash or orderable balance
- unallocated position quantity for adoption/sell decisions
- exposure by symbol
- exposure by horizon
- stop distance and expected reward/risk
- liquidity and spread
- recent loss cluster or churn
- whether ETF/core exposure is a better vehicle than a single name

When the correct size is zero, say why. When exploratory size is used, label it as exploratory and define what would justify increasing it.
```

`src/tradecraft/jue/skills/execution_review.md`

```markdown
---
skill_id: execution_review
name: Execution Review
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - execution_quality
  - slippage_note
  - rule_compliance
  - next_lesson
max_prompt_chars: 1500
---
# Execution Review

After every closed, errored, stale, or manually overridden block, review execution separately from idea quality.

Record:
- whether entry happened as designed
- whether exit happened by target, stop, manual close, timeout, stale quote, order error, or rule adjustment
- MFE and MAE when available
- slippage or non-fill cause
- whether a waiting-entry block would have been better
- whether target/stop distance was too tight, too loose, or structurally wrong
- lesson to feed policy_revision
```

`src/tradecraft/jue/skills/portfolio_balance.md`

```markdown
---
skill_id: portfolio_balance
name: Portfolio Balance
version: 1
scope: shared
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/wealth-management/skills/portfolio-rebalance/SKILL.md
required_outputs:
  - allocation_state
  - drift_note
  - rebalance_action
  - cash_plan
max_prompt_chars: 1600
---
# Portfolio Balance

Jue manages the portfolio, not only isolated trades. Review cash, short/mid/long/core ETF balance, single-symbol concentration, sector concentration, and idle capital.

For KIS:
- use official account total value for scale
- use orderable cash for buy sizing
- treat user-bought positions as special-watch until thesis and horizon are assigned
- consider ETF/core blocks for market exposure and long-horizon balance

For Binance:
- separate spot cash, futures margin, open futures risk, and existing spot holdings
- compare spot long vs futures long/short exposure
- reduce churn when recent block outcomes show low edge
```

`src/tradecraft/jue/skills/crypto_market_sweep.md`

```markdown
---
skill_id: crypto_market_sweep
name: Crypto Market Sweep
version: 1
scope: binance
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - market_regime
  - symbol_shortlist
  - spot_futures_choice
  - data_gaps
max_prompt_chars: 1700
---
# Crypto Market Sweep

Jue must scan Binance as a 24h market with separate spot and futures lanes. Start from liquid universe, quote volume, volatility, spread, funding, open interest, trend, reversal risk, and recent block outcomes.

For each candidate, decide:
- spot long, futures long, futures short, waiting-entry, or reject
- why this market lane is better than the other lane
- whether order book/spread is fresh enough
- whether recent churn argues for waiting instead of immediate entry
- which quant features matter most

Short candidates require futures availability and extra liquidation-distance awareness.
```

`src/tradecraft/jue/skills/evidence_audit.md`

```markdown
---
skill_id: evidence_audit
name: Evidence Audit
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - evidence_score
  - contradiction
  - freshness
  - missing_data
max_prompt_chars: 1500
---
# Evidence Audit

Before a decision becomes an action, inspect evidence quality.

Check:
- source freshness
- source type: quote, order book, account, report, RAG, valuation, whale, closing-flow, quant, catalyst, memory, user directive
- whether evidence is directly about the symbol or only sector/market context
- whether evidence conflicts with current price action
- whether a stale data gap should block action, shrink size, or merely reduce confidence

Every create/update/close action should include at least one concrete evidence reference or a clear reason why it is a rule-driven risk action.
```

`src/tradecraft/jue/skills/policy_revision.md`

```markdown
---
skill_id: policy_revision
name: Policy Revision
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - policy_candidate
  - scorecard_update
  - revision_action
  - confidence
max_prompt_chars: 1600
---
# Policy Revision

Jue improves by converting repeated block outcomes into policy candidates, then into preference or caution rules when evidence is strong enough.

Policy revision must:
- use multiple block reflections when available
- separate symbol-specific lessons from transferable process lessons
- avoid hard trading bans except system safety gates
- record whether the policy changes sizing, confirmation, target/stop design, waiting-entry preference, or review cadence
- track sample count, average PnL, win rate, expectancy, and rule compliance

When evidence is thin, keep the result as an observation. When evidence repeats, promote it to caution or preference.
```

- [ ] **Step 4: Run skill tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_workflow_manifests.py::test_required_jue_skills_exist_and_parse tests/test_jue_workflow_manifests.py::test_jue_skills_do_not_reintroduce_old_disclaimer_identity -q
```

Expected: PASS.

---

## Task 3: Add Workflow Manifests And Contracts

**Files:**

- Create all `src/tradecraft/jue/workflows/*.json`
- Create all `src/tradecraft/jue/contracts/*.json`
- Modify: `tests/test_jue_workflow_manifests.py`

- [ ] **Step 1: Add tests for workflow reference integrity**

Append to `tests/test_jue_workflow_manifests.py`:

```python
import json


REQUIRED_WORKFLOWS = {
    "kis_pre_open",
    "kis_intraday_manager",
    "kis_post_close",
    "binance_cycle",
    "crypto_research",
    "instant_symbol_analysis",
    "block_reflection",
}


def test_required_workflows_compile() -> None:
    registry = JueSkillRegistry(root=JUE_ROOT)

    for workflow_id in sorted(REQUIRED_WORKFLOWS):
        pack = registry.compile_prompt_pack(workflow_id)
        assert pack["workflow_id"] == workflow_id
        assert pack["skills"]
        assert pack["contracts"]
        assert pack["safety_gates"]
        assert pack["prompt_budget"]["used_skill_chars"] <= pack["prompt_budget"]["max_skill_chars"]


def test_workflow_model_policy_matches_jue_split() -> None:
    registry = JueSkillRegistry(root=JUE_ROOT)

    kis = registry.compile_prompt_pack("kis_intraday_manager")
    binance = registry.compile_prompt_pack("binance_cycle")

    assert kis["model_policy"]["expected_runtime_model"] == "gpt-5.5"
    assert kis["model_policy"]["expected_reasoning_effort"] == "xhigh"
    assert binance["model_policy"]["expected_runtime_model"] == "gpt-5.3-codex-spark"
    assert binance["model_policy"]["expected_reasoning_effort"] == "xhigh"


def test_contracts_have_required_and_rejection_rules() -> None:
    for path in sorted((JUE_ROOT / "contracts").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["contract_id"] == path.stem
        assert data["version"] >= 1
        assert data["required"]
        assert data["reject_when"]
```

- [ ] **Step 2: Run failing workflow tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_workflow_manifests.py -q
```

Expected: FAIL because workflow and contract files do not exist.

- [ ] **Step 3: Create shared contracts**

Create `src/tradecraft/jue/contracts/block_action_contract.json`:

```json
{
  "contract_id": "block_action_contract",
  "version": 1,
  "required": ["symbol", "horizon", "target_price", "stop_price", "thesis", "risk_note", "confidence"],
  "action_keys": ["adopt_existing_blocks", "create_blocks", "update_blocks", "close_blocks", "pause_blocks"],
  "price_fields": ["entry_price", "entry_trigger_price", "target_price", "stop_price"],
  "evidence_fields": ["evidence_refs", "reasons", "risks", "data_gaps"],
  "reject_when": [
    "missing_symbol",
    "missing_horizon",
    "missing_target_price",
    "missing_stop_price",
    "non_positive_price",
    "invalid_long_price_order",
    "invalid_short_price_order",
    "missing_executable_price_structure",
    "missing_thesis",
    "missing_risk_note"
  ]
}
```

Create `src/tradecraft/jue/contracts/evidence_claim_contract.json`:

```json
{
  "contract_id": "evidence_claim_contract",
  "version": 1,
  "required": ["claim", "source_type", "source_id", "captured_at", "freshness", "confidence"],
  "source_types": ["quote", "order_book", "account", "report", "rag", "valuation", "whale", "closing_flow", "quant", "catalyst", "memory", "user_directive"],
  "reject_when": [
    "missing_source_id",
    "missing_captured_at",
    "claim_without_source",
    "stale_source_used_as_fresh",
    "confidence_outside_0_1"
  ]
}
```

Create `src/tradecraft/jue/contracts/thesis_update_contract.json`:

```json
{
  "contract_id": "thesis_update_contract",
  "version": 1,
  "required": ["symbol", "thesis_statement", "impact", "conviction_delta", "invalidation", "evidence_refs"],
  "impact_values": ["strengthen", "neutral", "weaken", "broken"],
  "reject_when": [
    "missing_symbol",
    "missing_thesis_statement",
    "missing_invalidation",
    "impact_not_allowed",
    "conviction_delta_missing"
  ]
}
```

Create `src/tradecraft/jue/contracts/reflection_contract.json`:

```json
{
  "contract_id": "reflection_contract",
  "version": 1,
  "required": ["block_id", "symbol", "thesis_quality", "entry_quality", "execution_quality", "exit_reason", "lesson_md"],
  "quality_values": ["strong", "acceptable", "weak", "unknown"],
  "reject_when": [
    "missing_block_id",
    "missing_symbol",
    "missing_exit_reason",
    "missing_lesson",
    "pnl_only_reflection"
  ]
}
```

Create `src/tradecraft/jue/contracts/policy_revision_contract.json`:

```json
{
  "contract_id": "policy_revision_contract",
  "version": 1,
  "required": ["policy_id", "scope", "action", "effect", "sample_count", "confidence", "reason_md"],
  "allowed_actions": ["observe", "promote_caution", "promote_preference", "deprecate", "keep"],
  "allowed_effect_keys": ["sizing", "confirmation", "target_stop", "waiting_entry", "review_cadence", "lane_choice", "portfolio_balance"],
  "reject_when": [
    "missing_policy_id",
    "hard_ban_policy",
    "confidence_outside_0_1",
    "sample_count_negative",
    "missing_reason"
  ]
}
```

- [ ] **Step 4: Create workflow manifests**

Create the seven workflow files using the common fields from the Data Contracts section. Use these exact skill/contract pairings:

| Workflow | Skills | Contracts | Expected model |
|---|---|---|---|
| `kis_pre_open` | `portfolio_balance`, `catalyst_calendar`, `idea_generation`, `evidence_audit` | `evidence_claim_contract`, `thesis_update_contract` | `gpt-5.5` |
| `kis_intraday_manager` | `thesis_tracker`, `block_design`, `risk_sizing`, `execution_review`, `portfolio_balance`, `evidence_audit` | `block_action_contract`, `thesis_update_contract` | `gpt-5.5` |
| `kis_post_close` | `execution_review`, `thesis_tracker`, `policy_revision`, `portfolio_balance` | `reflection_contract`, `policy_revision_contract` | `gpt-5.5` |
| `binance_cycle` | `crypto_market_sweep`, `block_design`, `risk_sizing`, `execution_review`, `evidence_audit`, `portfolio_balance` | `block_action_contract`, `evidence_claim_contract` | `gpt-5.3-codex-spark` |
| `crypto_research` | `crypto_market_sweep`, `idea_generation`, `catalyst_calendar`, `evidence_audit` | `evidence_claim_contract`, `thesis_update_contract` | `gpt-5.3-codex-spark` |
| `instant_symbol_analysis` | `thesis_tracker`, `catalyst_calendar`, `evidence_audit`, `block_design` | `thesis_update_contract`, `block_action_contract` | `gpt-5.5` |
| `block_reflection` | `execution_review`, `thesis_tracker`, `policy_revision` | `reflection_contract`, `policy_revision_contract` | `gpt-5.5` |

For `binance_cycle.json`, set:

```json
"cadence": {"kind": "always_on", "interval_sec": 3600}
```

For `kis_intraday_manager.json`, set:

```json
"cadence": {"kind": "market_hours", "interval_sec": 1800}
```

For every workflow, set:

```json
"authority": {
  "can_read_untrusted_research": false,
  "can_write_memory": true,
  "can_create_blocks": true,
  "can_submit_orders": false
}
```

For `crypto_research.json`, set `can_create_blocks` to `false`.

For `block_reflection.json`, set `can_create_blocks` to `false`.

- [ ] **Step 5: Run workflow tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_workflow_manifests.py -q
```

Expected: PASS.

---

## Task 4: Add Jue Workflow Check Script

**Files:**

- Create: `scripts/check_jue_workflows.py`
- Test: `tests/test_jue_workflow_manifests.py`

- [ ] **Step 1: Add script-level test**

Append to `tests/test_jue_workflow_manifests.py`:

```python
import subprocess
import sys


def test_check_jue_workflows_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_jue_workflows.py"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Jue workflow check OK" in result.stdout
```

- [ ] **Step 2: Create script**

Create `scripts/check_jue_workflows.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tradecraft.services.jue_skill_registry import (  # noqa: E402
    JueSkillRegistry,
    JueSkillValidationError,
)


def main() -> int:
    root = ROOT / "src" / "tradecraft" / "jue"
    registry = JueSkillRegistry(root=root)
    errors: list[str] = []
    for workflow_path in sorted((root / "workflows").glob("*.json")):
        workflow_id = workflow_path.stem
        try:
            registry.compile_prompt_pack(workflow_id)
        except (JueSkillValidationError, ValueError, OSError) as exc:
            errors.append(f"{workflow_id}: {exc}")
    for skill_path in sorted((root / "skills").glob("*.md")):
        try:
            registry.load_skill(skill_path.stem)
        except (JueSkillValidationError, ValueError, OSError) as exc:
            errors.append(f"{skill_path.name}: {exc}")
    if errors:
        for error in errors:
            print(f"Jue workflow check FAIL: {error}", file=sys.stderr)
        return 1
    print("Jue workflow check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run script test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_jue_workflow_manifests.py::test_check_jue_workflows_script_passes -q
```

Expected: PASS.

---

## Task 5: Inject Workflow Pack Into KIS Jue

**Files:**

- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Add test that KIS prompt contains workflow pack**

Add a focused test near existing prompt/manager tests:

```python
def test_kis_manager_prompt_contains_jue_workflow_pack(fake_kis_adapter, tmp_path):
    trader = _make_kis_block_trader(fake_kis_adapter, tmp_path)
    prompt = trader._build_manager_prompt(
        clock={"phase": "regular"},
        account={"total_value_krw": 1_000_000, "orderable_cash_krw": 500_000},
        blocks=[],
        quotes=[],
        strategy={},
        latest_judgment={},
        market_pulse={},
        memory_context={},
        etf_research={},
        allocation={},
        portfolio_balance={},
        pre_adoption_symbol_analysis={},
        daily_discovery={},
    )

    assert prompt["jue_workflow"]["workflow_id"] == "kis_intraday_manager"
    assert prompt["jue_workflow"]["model_policy"]["expected_runtime_model"] == "gpt-5.5"
    skill_ids = {row["skill_id"] for row in prompt["jue_workflow"]["skills"]}
    assert {"block_design", "risk_sizing", "thesis_tracker"}.issubset(skill_ids)
    assert "executable_price_structure" in prompt["jue_workflow"]["safety_gates"]
```

If the helper signature differs, use the local `_build_manager_prompt` invocation pattern already present in `tests/test_kis_block_trader.py`.

- [ ] **Step 2: Implement KIS workflow injection**

In `src/tradecraft/services/kis_block_trader.py`:

```python
from tradecraft.services.jue_skill_registry import JueSkillRegistry, JueSkillValidationError
```

Inside the prompt builder, before returning `prompt`, add:

```python
try:
    prompt["jue_workflow"] = JueSkillRegistry().compile_prompt_pack("kis_intraday_manager")
except JueSkillValidationError as exc:
    prompt["jue_workflow"] = {
        "workflow_id": "kis_intraday_manager",
        "status": "error",
        "error_message": str(exc),
    }
```

Keep the existing hardcoded KIS policy text during this task. The first integration should be additive so behavior remains stable.

- [ ] **Step 3: Run KIS test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_kis_block_trader.py -q
```

Expected: PASS.

---

## Task 6: Inject Workflow Pack Into Binance Jue And Crypto Research

**Files:**

- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Add Binance prompt test**

Add:

```python
def test_binance_manager_prompt_contains_jue_workflow_pack(tmp_path):
    trader = _make_binance_block_trader(tmp_path)
    prompt = trader._build_manager_prompt(
        account={"spot_cash_usdt": 100, "futures_cash_usdt": 100},
        blocks=[],
        quotes=[],
        crypto_research={"items": [], "candidates": []},
        quant_context={},
        alpha_context={},
        pattern_context={},
        memory_context={},
        recent_events=[],
    )

    assert prompt["jue_workflow"]["workflow_id"] == "binance_cycle"
    assert prompt["jue_workflow"]["model_policy"]["expected_runtime_model"] == "gpt-5.3-codex-spark"
    skill_ids = {row["skill_id"] for row in prompt["jue_workflow"]["skills"]}
    assert {"crypto_market_sweep", "block_design", "risk_sizing"}.issubset(skill_ids)
```

Use the existing test helper names in `tests/test_binance_block_trader.py`.

- [ ] **Step 2: Add crypto research prompt test**

Add:

```python
def test_crypto_research_prompt_contains_research_workflow(tmp_path):
    service = _make_crypto_market_research_service(tmp_path)
    prompt = service._build_research_prompt(
        {"symbols": ["BTCUSDT"], "market_features": {}, "account": {}}
    )

    assert prompt["jue_workflow"]["workflow_id"] == "crypto_research"
    skill_ids = {row["skill_id"] for row in prompt["jue_workflow"]["skills"]}
    assert "crypto_market_sweep" in skill_ids
    assert "evidence_audit" in skill_ids
```

Use the existing service helper names in `tests/test_crypto_market_research.py`.

- [ ] **Step 3: Implement Binance workflow injection**

In `src/tradecraft/services/binance_block_trader.py`, import the registry and add:

```python
try:
    prompt["jue_workflow"] = JueSkillRegistry().compile_prompt_pack("binance_cycle")
except JueSkillValidationError as exc:
    prompt["jue_workflow"] = {
        "workflow_id": "binance_cycle",
        "status": "error",
        "error_message": str(exc),
    }
```

- [ ] **Step 4: Implement crypto research workflow injection**

In `src/tradecraft/services/crypto_market_research.py`, import the registry and add:

```python
try:
    prompt["jue_workflow"] = JueSkillRegistry().compile_prompt_pack("crypto_research")
except JueSkillValidationError as exc:
    prompt["jue_workflow"] = {
        "workflow_id": "crypto_research",
        "status": "error",
        "error_message": str(exc),
    }
```

- [ ] **Step 5: Run Binance and crypto research tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_binance_block_trader.py tests/test_crypto_market_research.py -q
```

Expected: PASS.

---

## Task 7: Wire Workflows Into Investment Memory Rituals And Reflections

**Files:**

- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Add ritual workflow test**

Add:

```python
def test_memory_ritual_context_contains_slot_workflow(tmp_path):
    service = _make_investment_memory_service(tmp_path)
    context = service.build_ritual_context(
        slot="pre_open",
        trading_day="2026-05-31",
        account={},
        blocks={},
    )

    assert context["jue_workflow"]["workflow_id"] == "kis_pre_open"
    skill_ids = {row["skill_id"] for row in context["jue_workflow"]["skills"]}
    assert "portfolio_balance" in skill_ids
    assert "catalyst_calendar" in skill_ids
```

Add:

```python
def test_block_reflection_context_contains_reflection_workflow(tmp_path):
    service = _make_investment_memory_service(tmp_path)
    context = service.build_block_reflection_context(
        block={"block_id": "blk_1", "symbol": "005930", "status": "closed"},
        orders=[],
        events=[],
    )

    assert context["jue_workflow"]["workflow_id"] == "block_reflection"
    skill_ids = {row["skill_id"] for row in context["jue_workflow"]["skills"]}
    assert {"execution_review", "policy_revision"}.issubset(skill_ids)
```

If `build_block_reflection_context` does not exist, create it as a small public helper around the existing private reflection context construction.

- [ ] **Step 2: Implement slot-to-workflow mapping**

In `investment_memory.py`, add:

```python
RITUAL_WORKFLOW_BY_SLOT = {
    "pre_open": "kis_pre_open",
    "midday": "kis_intraday_manager",
    "post_close": "kis_post_close",
    "block_reflection": "block_reflection",
}
```

In `build_ritual_context`, add:

```python
workflow_id = RITUAL_WORKFLOW_BY_SLOT.get(slot, "kis_intraday_manager")
try:
    context["jue_workflow"] = JueSkillRegistry().compile_prompt_pack(workflow_id)
except JueSkillValidationError as exc:
    context["jue_workflow"] = {
        "workflow_id": workflow_id,
        "status": "error",
        "error_message": str(exc),
    }
```

- [ ] **Step 3: Add block reflection workflow helper**

Add:

```python
def build_block_reflection_context(
    self,
    *,
    block: dict[str, Any],
    orders: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context = {
        "block": block,
        "orders": orders or [],
        "events": events or [],
    }
    try:
        context["jue_workflow"] = JueSkillRegistry().compile_prompt_pack("block_reflection")
    except JueSkillValidationError as exc:
        context["jue_workflow"] = {
            "workflow_id": "block_reflection",
            "status": "error",
            "error_message": str(exc),
        }
    return context
```

Use this helper from the existing reflection generation code so the LLM reflection sees the same workflow pack.

- [ ] **Step 4: Run memory tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_investment_memory.py -q
```

Expected: PASS.

---

## Task 8: Add API And UI Visibility For Jue Brain State

**Files:**

- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/web/static/index.html`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_api_smoke.py`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add API test**

Add to `tests/test_api_smoke.py`:

```python
def test_jue_workflows_status_requires_admin(client):
    response = client.get("/api/jue/workflows/status")

    assert response.status_code in {401, 403}


def test_jue_workflows_status_returns_compiled_packs(admin_client):
    response = admin_client.get("/api/jue/workflows/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "kis_intraday_manager" in payload["workflows"]
    assert payload["workflows"]["kis_intraday_manager"]["model_policy"]["expected_runtime_model"] == "gpt-5.5"
```

Use existing admin client fixture names from the file; if the fixture is named differently, follow the local pattern.

- [ ] **Step 2: Implement API endpoint**

In `main.py`, add an admin-protected route:

```python
@app.get("/api/jue/workflows/status")
def jue_workflows_status(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    registry = JueSkillRegistry()
    workflow_ids = [
        "kis_pre_open",
        "kis_intraday_manager",
        "kis_post_close",
        "binance_cycle",
        "crypto_research",
        "instant_symbol_analysis",
        "block_reflection",
    ]
    workflows: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for workflow_id in workflow_ids:
        try:
            workflows[workflow_id] = registry.compile_prompt_pack(workflow_id)
        except JueSkillValidationError as exc:
            errors[workflow_id] = str(exc)
    return {
        "status": "ok" if not errors else "error",
        "workflows": workflows,
        "errors": errors,
    }
```

- [ ] **Step 3: Add static UI test**

Add to `tests/test_static_ui.py`:

```python
def test_settings_contains_jue_workflow_panel() -> None:
    html = Path("src/tradecraft/web/static/index.html").read_text(encoding="utf-8")
    js = Path("src/tradecraft/web/static/app.js").read_text(encoding="utf-8")

    assert "jue-workflow-panel" in html
    assert "/api/jue/workflows/status" in js
    assert "renderJueWorkflowStatus" in js
```

- [ ] **Step 4: Add UI section**

In `index.html`, add under the settings/operations area:

```html
<section class="panel jue-workflow-panel" id="jue-workflow-panel">
  <div class="panel-header">
    <div>
      <h2>쥬 운영체계</h2>
      <p>활성 워크플로우, 스킬팩, 계약 검증 상태</p>
    </div>
    <button class="ghost-button" id="refresh-jue-workflows">새로고침</button>
  </div>
  <div id="jue-workflow-status" class="workflow-grid"></div>
</section>
```

- [ ] **Step 5: Add UI fetch/render**

In `app.js`, add:

```javascript
async function loadJueWorkflowStatus() {
  const panel = document.getElementById("jue-workflow-status");
  if (!panel) return;
  panel.innerHTML = '<div class="muted">쥬 운영체계 확인 중...</div>';
  try {
    const payload = await apiFetchJson("/api/jue/workflows/status");
    renderJueWorkflowStatus(payload);
  } catch (error) {
    panel.innerHTML = `<div class="status-error">${escapeHtml(error.message || "쥬 운영체계 조회 실패")}</div>`;
  }
}

function renderJueWorkflowStatus(payload) {
  const panel = document.getElementById("jue-workflow-status");
  if (!panel) return;
  const workflows = payload.workflows || {};
  panel.innerHTML = Object.entries(workflows).map(([workflowId, row]) => {
    const skills = (row.skills || []).map((skill) => `<span class="chip">${escapeHtml(skill.skill_id)}</span>`).join("");
    const contracts = (row.contracts || []).map((contract) => `<span class="chip subtle">${escapeHtml(contract.contract_id)}</span>`).join("");
    return `
      <article class="workflow-card">
        <div class="workflow-card-title">${escapeHtml(workflowId)}</div>
        <div class="workflow-card-meta">${escapeHtml(row.scope || "")} · ${escapeHtml(row.model_policy?.expected_runtime_model || "")} · ${escapeHtml(row.model_policy?.expected_reasoning_effort || "")}</div>
        <div class="chip-row">${skills}</div>
        <div class="chip-row">${contracts}</div>
      </article>
    `;
  }).join("");
}
```

Wire the button:

```javascript
document.getElementById("refresh-jue-workflows")?.addEventListener("click", loadJueWorkflowStatus);
```

Call `loadJueWorkflowStatus()` when settings/ops page loads using the existing tab render pattern.

- [ ] **Step 6: Add CSS**

In `style.css`, add:

```css
.workflow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.workflow-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-2);
  padding: 14px;
}

.workflow-card-title {
  color: var(--text);
  font-weight: 700;
}

.workflow-card-meta {
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
```

- [ ] **Step 7: Run API/UI tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_api_smoke.py tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
```

Expected: PASS and `node --check` exits 0.

---

## Task 9: Add Workflow/Skill Provenance To Manager Runs

**Files:**

- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Add manager-run provenance tests**

For KIS:

```python
def test_kis_manager_run_records_jue_workflow_metadata(fake_kis_adapter, tmp_path):
    trader = _make_kis_block_trader(fake_kis_adapter, tmp_path)
    run = trader.repository.save_manager_run(
        {
            "status": "ok",
            "prompt_json": {"jue_workflow": {"workflow_id": "kis_intraday_manager", "workflow_version": 1}},
            "response_json": {"hold_decision": {"summary": "hold"}},
            "actions_json": {},
        }
    )

    assert run["workflow_id"] == "kis_intraday_manager"
    assert run["workflow_version"] == 1
```

For Binance:

```python
def test_binance_manager_run_records_jue_workflow_metadata(tmp_path):
    trader = _make_binance_block_trader(tmp_path)
    run = trader.repository.save_manager_run(
        {
            "status": "ok",
            "market": "futures",
            "prompt_json": {"jue_workflow": {"workflow_id": "binance_cycle", "workflow_version": 1}},
            "response_json": {"hold_decision": {"summary": "hold"}},
            "actions_json": {},
        }
    )

    assert run["workflow_id"] == "binance_cycle"
    assert run["workflow_version"] == 1
```

- [ ] **Step 2: Extend manager run schemas**

Add nullable columns to both KIS and Binance `manager_runs` tables:

```sql
workflow_id TEXT NOT NULL DEFAULT ''
workflow_version INTEGER NOT NULL DEFAULT 0
skill_ids_json TEXT NOT NULL DEFAULT '[]'
contract_ids_json TEXT NOT NULL DEFAULT '[]'
```

When saving a manager run, extract:

```python
workflow = (payload.get("prompt_json") or {}).get("jue_workflow") or {}
payload["workflow_id"] = str(workflow.get("workflow_id") or "")
payload["workflow_version"] = int(workflow.get("workflow_version") or 0)
payload["skill_ids_json"] = json.dumps([row.get("skill_id") for row in workflow.get("skills") or []], ensure_ascii=False)
payload["contract_ids_json"] = json.dumps([row.get("contract_id") for row in workflow.get("contracts") or []], ensure_ascii=False)
```

- [ ] **Step 3: Run manager tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_kis_block_trader.py tests/test_binance_block_trader.py -q
```

Expected: PASS.

---

## Task 10: Make Skill Outputs Feed Memory More Explicitly

**Files:**

- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Add tests for skill-linked memory insight**

Add:

```python
def test_memory_insight_can_store_skill_and_workflow_ids(tmp_path):
    service = _make_investment_memory_service(tmp_path)
    service.initialize()
    row = service.repository.upsert_insight(
        {
            "key": "skill:block_design:blk_1",
            "memory_type": "block_reflection",
            "scope": "kis",
            "summary_md": "Block used wait_for_price correctly.",
            "status": "active",
            "confidence": 0.72,
            "metadata": {
                "workflow_id": "block_reflection",
                "skill_ids": ["execution_review", "policy_revision"],
                "contract_ids": ["reflection_contract"],
            },
        }
    )

    pack = service.context_pack(target_scope="kis", symbols=["005930"], block_ids=["blk_1"])

    assert row["key"] == "skill:block_design:blk_1"
    assert any(item["key"] == "skill:block_design:blk_1" for item in pack["seed_memory"] + pack.get("active_insights", []))
```

If `active_insights` is not currently emitted, add it as a compact list in `context_pack`.

- [ ] **Step 2: Add `active_insights` to context pack**

In `context_pack`, add:

```python
"active_insights": [
    {
        "key": row.get("key"),
        "memory_type": row.get("memory_type"),
        "scope": row.get("scope"),
        "summary_md": _truncate(row.get("summary_md"), 500),
        "confidence": row.get("confidence"),
        "metadata": row.get("metadata") or {},
    }
    for row in active_insights[:12]
],
```

- [ ] **Step 3: Run memory test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_investment_memory.py -q
```

Expected: PASS.

---

## Task 11: Document The New Jue Brain Architecture

**Files:**

- Modify: `docs/spec/05_llm_system.md`
- Modify: `docs/spec/08_research_memory.md`
- Modify: `docs/spec/16_refactor_roadmap.md`

- [ ] **Step 1: Add documentation tests**

Add to `tests/test_static_ui.py` or create `tests/test_docs_spec.py`:

```python
from pathlib import Path


def test_spec_documents_jue_skill_workflow_architecture() -> None:
    llm_doc = Path("docs/spec/05_llm_system.md").read_text(encoding="utf-8")
    memory_doc = Path("docs/spec/08_research_memory.md").read_text(encoding="utf-8")
    roadmap_doc = Path("docs/spec/16_refactor_roadmap.md").read_text(encoding="utf-8")

    assert "Jue Skill Pack" in llm_doc
    assert "workflow manifest" in llm_doc
    assert "skill-linked memory" in memory_doc
    assert "financial skills absorption" in roadmap_doc
```

- [ ] **Step 2: Update `docs/spec/05_llm_system.md`**

Add:

```markdown
## Jue Skill Pack And Workflow Manifests

Jue LLM prompts are compiled from persona, live context, memory context, workflow manifest, skill markdown, and output contracts. Workflow manifests live in `src/tradecraft/jue/workflows/*.json`; skill markdown lives in `src/tradecraft/jue/skills/*.md`; output contracts live in `src/tradecraft/jue/contracts/*.json`.

KIS Jue uses `gpt-5.5` with `xhigh` reasoning for KIS workflows. Binance Jue uses `gpt-5.3-codex-spark` with `xhigh` reasoning for Binance workflows. The workflow manifest records the expected model split so ops checks can detect accidental drift.
```

- [ ] **Step 3: Update `docs/spec/08_research_memory.md`**

Add:

```markdown
## Skill-Linked Memory

Memory insights may include `workflow_id`, `skill_ids`, and `contract_ids` in metadata. This allows later manager runs to know not only what Jue learned, but which operating procedure produced the lesson. Block reflections should distinguish thesis quality, entry quality, execution quality, and market regime before policy revision.
```

- [ ] **Step 4: Update `docs/spec/16_refactor_roadmap.md`**

Add:

```markdown
## Financial Skills Absorption

Next structural refactor: absorb institutional financial-agent patterns into Jue through native skill markdown, workflow manifests, output contracts, and validation checks. This reduces prompt drift, clarifies model split, improves evidence discipline, and makes reflection-to-policy growth easier to audit.
```

- [ ] **Step 5: Run docs test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_docs_spec.py tests/test_static_ui.py -q
```

Expected: PASS. If `tests/test_docs_spec.py` is newly created, run that file directly.

---

## Task 12: Full Verification

**Files:**

- All files above.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  tests/test_jue_skill_registry.py \
  tests/test_jue_workflow_manifests.py \
  tests/test_kis_block_trader.py \
  tests/test_binance_block_trader.py \
  tests/test_crypto_market_research.py \
  tests/test_investment_memory.py \
  tests/test_api_smoke.py \
  tests/test_static_ui.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run workflow checker**

Run:

```bash
.venv/bin/python3 scripts/check_jue_workflows.py
```

Expected:

```text
Jue workflow check OK
```

- [ ] **Step 3: Run JavaScript syntax check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: exits 0.

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check -- \
  src/tradecraft/jue \
  src/tradecraft/services/jue_skill_registry.py \
  src/tradecraft/services/kis_block_trader.py \
  src/tradecraft/services/binance_block_trader.py \
  src/tradecraft/services/crypto_market_research.py \
  src/tradecraft/services/investment_memory.py \
  src/tradecraft/main.py \
  src/tradecraft/web/static/index.html \
  src/tradecraft/web/static/app.js \
  src/tradecraft/web/static/style.css \
  docs/spec
```

Expected: no output.

- [ ] **Step 5: Manual runtime smoke**

With control app running and admin token available:

```bash
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" \
  http://127.0.0.1:18080/api/jue/workflows/status | python3 -m json.tool | head -80
```

Expected:

- `status` is `ok`;
- `kis_intraday_manager` is present;
- `binance_cycle` is present;
- KIS expected model is `gpt-5.5`;
- Binance expected model is `gpt-5.3-codex-spark`.

---

## Rollout Strategy

1. Add files and validation without changing trading behavior.
2. Inject workflow packs into prompts additively.
3. Record workflow provenance in manager runs.
4. Expose state in UI.
5. Observe at least one KIS trading day and one Binance overnight cycle.
6. Only after stable observation, remove duplicated hardcoded prompt sections that are now represented by skill markdown.

This is intentionally staged. The first deployment should not suddenly change Jue's trade aggressiveness. It should make Jue's thought process more inspectable and easier to improve.

---

## Acceptance Criteria

- `scripts/check_jue_workflows.py` passes.
- Every workflow compiles into a prompt pack.
- KIS manager prompt contains `jue_workflow.workflow_id = kis_intraday_manager`.
- Binance manager prompt contains `jue_workflow.workflow_id = binance_cycle`.
- Crypto research prompt contains `jue_workflow.workflow_id = crypto_research`.
- Memory ritual/reflection contexts contain appropriate workflow packs.
- Manager runs store workflow id, workflow version, skill ids, and contract ids.
- UI shows active workflows, skill chips, contract chips, and expected model split.
- No old identity/disclaimer phrases are introduced in new skill markdown.
- Existing KIS/Binance execution gates remain authoritative over LLM output.

---

## Future Extensions After This Plan

- Add replay-based skill attribution: measure whether blocks influenced by `idea_generation`, `risk_sizing`, or `crypto_market_sweep` perform differently.
- Add per-skill scorecards: hit rate, average PnL, MFE/MAE, non-fill rate, stop-out rate.
- Add skill version migration: when a skill is edited, record which future manager runs used the new version.
- Add contradiction engine: detect when memory says "prefer waiting entry" but current prompt creates immediate blocks repeatedly.
- Add prompt diet dashboard: show which skills are consuming context and which were truncated.

---

## Self-Review

**Spec coverage:** The plan covers upstream skill absorption, Jue-native skill markdown, workflow manifests, output contracts, KIS/Binance/research/memory integration, UI visibility, validation, docs, and staged rollout.

**Placeholder scan:** The plan avoids placeholder tokens and gives exact file paths, field names, commands, and expected outcomes. Some test helper names may need to be adapted to local helper names where existing files already define them; the required assertions and behavior are explicit.

**Type consistency:** Workflow IDs, skill IDs, contract IDs, and prompt keys are consistently named across tests, implementation snippets, UI, API, and docs.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-31-jue-financial-skills-absorption.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fastest and safest for this multi-surface refactor.
2. **Inline Execution** - execute tasks in this session using checkpointed batches.
