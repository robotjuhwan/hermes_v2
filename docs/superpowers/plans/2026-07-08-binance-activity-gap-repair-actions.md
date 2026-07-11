# Binance Activity Gap Repair Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Binance inactivity warnings point operators toward safe evidence refresh actions before live manager/executor actions.

**Architecture:** Keep the trading loop unchanged. Add compact, read-only remediation payloads in ops API builders so activity pressure with candidate symbols exposes crypto research, market structure, and alpha refresh actions.

**Tech Stack:** Python 3.10+, FastAPI payload helpers, pytest.

## Global Constraints

- Do not invoke live Binance manager, executor, or order endpoints.
- Preserve existing warning IDs and restart remediation actions for backward compatibility.
- Keep changes scoped to ops payload construction and tests.
- Prefer candidate-symbol scoped research requests when candidate symbols are available.

---

### Task 1: Add Binance Activity Repair Payload Actions

**Files:**
- Modify: `tests/test_ops_payloads.py`
- Modify: `src/tradecraft/api/ops_payloads.py`

**Interfaces:**
- Consumes: `build_ops_binance_block_trader_payload(...)`
- Produces: top-level `activity_repair_actions` in the Binance block trader ops payload.

- [ ] **Step 1: Write the failing test**

Add a test proving `binance_activity_pressure_open` payloads with candidate symbols include POST actions for `/api/crypto/research/run-once`, `/api/crypto/research/collect`, and `/api/crypto/alpha/collect`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ops_payloads.py::test_binance_block_trader_payload_exposes_activity_repair_actions_for_candidate_symbols -q`

- [ ] **Step 3: Write minimal implementation**

Add a helper in `ops_payloads.py` that sanitizes candidate symbols from compact activity pressure and returns compact action dictionaries.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ops_payloads.py::test_binance_block_trader_payload_exposes_activity_repair_actions_for_candidate_symbols -q`

### Task 2: Add Global Remediation Action

**Files:**
- Modify: `tests/test_ops_payloads.py`
- Modify: `src/tradecraft/api/ops_payloads.py`

**Interfaces:**
- Consumes: `build_ops_remediation_actions(...)`
- Produces: `refresh_binance_crypto_research_context` remediation action.

- [ ] **Step 1: Write the failing assertion**

Extend the existing remediation mapping test to expect a safe POST `/api/crypto/research/run-once` action for `binance_activity_pressure_open`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ops_payloads.py::test_build_ops_remediation_actions_maps_signals_to_operator_actions -q`

- [ ] **Step 3: Write minimal implementation**

Add the remediation action before the restart action in the Binance recovery branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ops_payloads.py::test_build_ops_remediation_actions_maps_signals_to_operator_actions -q`

### Task 3: Preserve Actions In Compact Ops Readiness

**Files:**
- Modify: `tests/test_ops_api_router.py`
- Modify: `src/tradecraft/api/ops.py`

**Interfaces:**
- Consumes: `activity_repair_actions` from section payloads.
- Produces: compact `/api/ops/readiness?compact=true` Binance section that still contains safe repair actions.

- [ ] **Step 1: Write the failing test**

Extend the compact ops readiness test to assert the Binance section preserves `activity_repair_actions`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ops_api_router.py::test_ops_readiness_compact_preserves_binance_activity_gap_context -q`

- [ ] **Step 3: Write minimal implementation**

Teach `_compact_readiness_section` to compact `activity_repair_actions` with the existing remediation action key set.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ops_api_router.py::test_ops_readiness_compact_preserves_binance_activity_gap_context -q`
