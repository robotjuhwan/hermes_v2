# Binance Manager Prompt Warn Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure Binance manager prompts that are under the hard max but over the warn budget continue through final compaction instead of returning early.

**Architecture:** Keep the fix inside `tradecraft.services.binance_manager_prompt.finalize_prompt_budget`. Add one regression test that models the current production shape: memory and Jue wiki application dominate prompt size, initial compaction drops the prompt below severe-recovery thresholds, and final compaction must still reduce it below warn.

**Tech Stack:** Python, pytest, existing prompt budget helpers.

## Global Constraints

- Do not run live Binance manager, executor, or order endpoints.
- Do not commit; project AGENTS.md says commits require explicit user request.
- Preserve core manager sections: `critical_response_contract`, `candidates`, `memory`, `jue_wiki_application`, `validation_repair`, and `prompt_budget`.
- Keep changes surgical to `src/tradecraft/services/binance_manager_prompt.py` and `tests/test_binance_manager_prompt.py`.

---

### Task 1: Continue Final Compaction For Non-Latency Warn Overages

**Files:**
- Modify: `tests/test_binance_manager_prompt.py`
- Modify: `src/tradecraft/services/binance_manager_prompt.py`

**Interfaces:**
- Consumes: `finalize_prompt_budget(prompt, target_chars, warn_chars, max_chars)`, `prompt_budget_error(prompt)`, and `prompt_chars(prompt)`.
- Produces: A finalized prompt where non-latency, over-warn, under-max payloads attempt final compaction before returning.

- [ ] **Step 1: Write the failing test**

Add a pytest function near the existing Binance prompt budget tests:

```python
def test_binance_prompt_budget_finalizes_under_warn_after_soft_recovery_case() -> None:
    marker = "BINANCE_SOFT_WARN_RECOVERY_BLOAT "
    prompt = {
        "critical_response_contract": {"lane_review": {"required": True}},
        "memory": {
            "memory_scope": "binance",
            "persona": "Jue searches for executable crypto asymmetry.",
            "policy_rule_evaluation": {
                f"rule_{idx}": {
                    "policy_id": f"binance.policy.{idx}",
                    "evidence": {"workflow_ids": ["binance_cycle"], "raw": marker * 18},
                    "raw_context": marker * 18,
                }
                for idx in range(120)
            },
            "active_insights": [
                {"summary": marker * 24, "symbols": ["ESPUSDT", "HMSTRUSDT"]}
                for _ in range(70)
            ],
        },
        "jue_wiki_application": {
            "pages": [{"page_id": f"binance.symbol.{idx}", "summary": marker * 22} for idx in range(45)],
            "trust_profile": {"notes": [marker * 18 for _ in range(20)]},
        },
        "validation_repair": {
            "status": "needs_repair",
            "min_reward_risk": 2.0,
            "max_stop_risk_pct": 3.0,
            "runner_hints": [marker * 18 for _ in range(20)],
        },
        "candidates": [
            {
                "symbol": "ESPUSDT",
                "market": "futures",
                "side": "short",
                "entry_price": 0.07156,
                "target_price": 0.06397,
                "stop_price": 0.075262,
                "reason_md": marker * 18,
            }
        ],
        "candidate_generation": {"observe_universe": [{"symbol": f"C{idx:03d}USDT", "reason": marker * 14} for idx in range(60)]},
        "entry_gate_policy": {"status": "active", "raw": marker * 160},
        "jue_wiki_repair_contract": {"status": "active", "raw": marker * 140},
        "live_authority": {"raw": marker * 120},
        "diagnostics": {"raw": marker * 100},
    }

    finalize_prompt_budget(
        prompt,
        target_chars=70_000,
        warn_chars=90_000,
        max_chars=190_000,
    )

    assert prompt_budget_error(prompt) == ""
    assert prompt["prompt_budget"]["over_warn"] is False
    assert prompt["prompt_budget"]["total_chars"] <= 90_000
    assert prompt_chars({"memory": prompt["memory"]}) < 12_000
    assert prompt["validation_repair"]["min_reward_risk"] == pytest.approx(2.0)
    assert prompt["validation_repair"]["max_stop_risk_pct"] == pytest.approx(3.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_binance_manager_prompt.py::test_binance_prompt_budget_finalizes_under_warn_after_soft_recovery_case -q`

Expected: FAIL because `prompt["prompt_budget"]["over_warn"]` is still `True` or total chars remain over `90_000`.

- [ ] **Step 3: Write minimal implementation**

In `finalize_prompt_budget`, replace the early return:

```python
if not prompt_budget_error(prompt) and (
    not latency_active or prompt_chars(prompt) <= effective_warn_chars
):
    return
```

with:

```python
if not prompt_budget_error(prompt) and prompt_chars(prompt) <= effective_warn_chars:
    return
```

This preserves the intended quick return only for prompts already at or under warn.

- [ ] **Step 4: Run focused verification**

Run:

```bash
.venv/bin/pytest tests/test_binance_manager_prompt.py::test_binance_prompt_budget_finalizes_under_warn_after_soft_recovery_case -q
.venv/bin/pytest tests/test_binance_manager_prompt.py::test_binance_prompt_budget_compacts_policy_rule_memory_bloat_under_warn tests/test_binance_manager_prompt.py::test_binance_prompt_budget_handles_operational_203k_pressure -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Run lint/diff hygiene**

Run:

```bash
.venv/bin/ruff check src/tradecraft/services/binance_manager_prompt.py tests/test_binance_manager_prompt.py
git diff --check -- src/tradecraft/services/binance_manager_prompt.py tests/test_binance_manager_prompt.py
```

Expected: both commands exit 0.
