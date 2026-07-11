# Binance Manager Contract Candidate Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop non-hard requested-symbol wiki coverage gaps from failing unrelated Binance manager actions, and preserve enough lane-diverse candidate detail in compacted prompts for the manager to see Binance opportunities.

**Architecture:** Keep the validation scoreboard intact, including the 19 diagnostic checks. Narrow only the manager contract gate so a non-hard `jue_wiki_requested_symbol_coverage` gap blocks actions on the affected symbols, but does not fail unrelated actions or concrete no-action repair decisions. Adjust candidate compaction locally in `binance_manager_prompt.py` so candidate rows selected for lane diversity are not re-truncated to four rows by generic storage compaction.

**Tech Stack:** Python 3.10+, pytest, existing `tradecraft.services.binance_manager_prompt` helpers.

## Global Constraints

- Run commands from repo root `/Users/juhwan/hermes_v2`.
- Make surgical changes only.
- Do not alter secrets/credentials or enable/disable live trading configuration.
- Preserve the 19 validation checks as diagnostic and scale-up gating signals.
- No new dependencies.
- Use pytest function-style tests in `tests/test_binance_manager_prompt.py`.
- Use `apply_patch` for manual file edits.

---

### Task 1: Scope Requested-Symbol Coverage Blocking

**Files:**
- Modify: `src/tradecraft/services/binance_manager_prompt.py`
- Test: `tests/test_binance_manager_prompt.py`

**Interfaces:**
- Consumes: `manager_response_contract_error(prompt, response, actions, hold_decision) -> str`
- Produces: A narrowed requested-symbol coverage rule where unrelated actions pass when `hard_blocker` is false and the action payload does not mention the coverage-gap symbols.

- [ ] **Step 1: Write the failing tests**

Add tests near the existing requested-symbol coverage contract tests:

```python
def test_binance_manager_response_contract_allows_unrelated_action_for_non_hard_requested_symbol_coverage() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "hard_blocker": False,
                "missing_summary_symbols": ["ETHUSDT"],
                "prompt_omitted_symbols": ["SOLUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "created unrelated action"},
    )

    assert error == ""
```

```python
def test_binance_manager_response_contract_keeps_hard_requested_symbol_coverage_blocking_unrelated_action() -> None:
    error = manager_response_contract_error(
        prompt={
            "jue_wiki_requested_symbol_coverage": {
                "status": "partial",
                "hard_blocker": True,
                "missing_summary_symbols": ["ETHUSDT"],
            },
            "execution_gate": {
                "status": "ok",
                "execution": {"spot_orders_enabled": True},
            },
        },
        response={},
        actions={
            "create_blocks": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "side": "long",
                    "metadata": {},
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        hold_decision={"summary": "created unrelated action"},
    )

    assert error == "validation_repair_resolution_missing_from_model"
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
pytest tests/test_binance_manager_prompt.py::test_binance_manager_response_contract_allows_unrelated_action_for_non_hard_requested_symbol_coverage tests/test_binance_manager_prompt.py::test_binance_manager_response_contract_keeps_hard_requested_symbol_coverage_blocking_unrelated_action -q
```

Expected: first test fails with `validation_repair_resolution_missing_from_model`; second test passes or continues to fail only if the implementation is too permissive.

- [ ] **Step 3: Write minimal implementation**

Add a helper in `src/tradecraft/services/binance_manager_prompt.py` near `_manager_actions_resolve_requested_symbol_coverage`:

```python
def _manager_requested_symbol_coverage_blocks_actions(
    *,
    prompt: dict[str, Any],
    actions: dict[str, Any],
) -> bool:
    coverage = _manager_scoped_requested_symbol_coverage(prompt)
    if bool(coverage.get("hard_blocker")):
        return True
    terms = _manager_requested_symbol_coverage_terms(prompt)
    if not terms:
        return True
    for key in BINANCE_MANAGER_ACTION_SECTIONS:
        for row in _as_list(actions.get(key)):
            if _manager_payload_mentions_any_term(row, terms):
                return True
    return False
```

Use it in `manager_response_contract_error`:

```python
requested_symbol_coverage_blocks_actions = (
    requested_symbol_coverage_gap
    and _manager_requested_symbol_coverage_blocks_actions(
        prompt=prompt,
        actions=actions,
    )
)
```

Replace action-branch checks that currently require requested coverage resolution with `requested_symbol_coverage_blocks_actions`.

- [ ] **Step 4: Run tests to verify green**

Run the same command from Step 2. Expected: both tests pass.

### Task 2: Preserve Lane-Diverse Candidate Rows In Final Compaction

**Files:**
- Modify: `src/tradecraft/services/binance_manager_prompt.py`
- Test: `tests/test_binance_manager_prompt.py`

**Interfaces:**
- Consumes: `compact_manager_sections_for_final_budget(prompt, target_chars) -> list[dict[str, Any]]`
- Produces: Candidate compaction that can retain more than four already-minimized lane-diverse rows when the caller requested a larger candidate `list_limit`.

- [ ] **Step 1: Write the failing test**

Add the test near existing final-budget candidate compaction tests:

```python
def test_final_budget_candidate_compaction_keeps_more_than_four_lane_diverse_rows() -> None:
    candidates = [
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 100.0,
            "target_price": 106.0,
            "stop_price": 97.0,
            "reason_md": "spot candidate " * 20,
        },
        {
            "symbol": "ETHUSDT",
            "market": "futures",
            "side": "long",
            "entry_price": 200.0,
            "target_price": 212.0,
            "stop_price": 194.0,
            "reason_md": "futures long candidate " * 20,
        },
        {
            "symbol": "SOLUSDT",
            "market": "futures",
            "side": "short",
            "entry_price": 50.0,
            "target_price": 47.0,
            "stop_price": 51.5,
            "reason_md": "futures short candidate " * 20,
        },
        {
            "symbol": "KRW-XRP",
            "market": "upbit_spot",
            "side": "long",
            "entry_price": 700.0,
            "target_price": 735.0,
            "stop_price": 682.0,
            "reason_md": "upbit candidate " * 20,
        },
        {
            "symbol": "HEIUSDT",
            "market": "futures",
            "side": "short",
            "lane": "volatile_attack",
            "entry_price": 0.12,
            "target_price": 0.108,
            "stop_price": 0.123,
            "reason_md": "volatile candidate " * 20,
            "calculated": {"lane": "volatile_attack", "volatile_attack": True},
        },
        {
            "symbol": "BNBUSDT",
            "market": "spot",
            "side": "long",
            "entry_price": 600.0,
            "target_price": 630.0,
            "stop_price": 585.0,
            "reason_md": "extra binance candidate " * 20,
        },
    ]
    prompt = {
        "critical_response_contract": {"text": "keep"},
        "candidates": candidates,
        "memory": {"blob": "M" * 80_000},
        "jue_wiki_application": {"blob": "W" * 20_000},
    }

    compact_manager_sections_for_final_budget(prompt, target_chars=70_000)

    rows = prompt["candidates"]["items"]
    selected_symbols = {row["symbol"] for row in rows}
    assert len(rows) >= 5
    assert {"BTCUSDT", "ETHUSDT", "SOLUSDT", "KRW-XRP", "HEIUSDT"}.issubset(
        selected_symbols
    )
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
pytest tests/test_binance_manager_prompt.py::test_final_budget_candidate_compaction_keeps_more_than_four_lane_diverse_rows -q
```

Expected: fails because `_compact_prompt_storage_sequence_section` re-limits compacted candidate `items` to at most four rows.

- [ ] **Step 3: Write minimal implementation**

Change `_compact_prompt_storage_sequence_section`:

```python
item_list_limit = bounded_limit if lane_diverse else max(min(bounded_limit, 4), 1)
```

Then pass `item_list_limit` to `compact_value(..., list_limit=item_list_limit)`.

- [ ] **Step 4: Run tests to verify green**

Run the same test command from Step 2. Expected: pass.

### Task 3: Focused Regression Suite

**Files:**
- Verify: `src/tradecraft/services/binance_manager_prompt.py`
- Verify: `tests/test_binance_manager_prompt.py`

**Interfaces:**
- Consumes: Tasks 1 and 2 changes.
- Produces: Verified behavior for requested-symbol coverage, candidate compaction, and no regression in the Binance manager prompt suite subset.

- [ ] **Step 1: Run requested-symbol coverage tests**

Run:

```bash
pytest tests/test_binance_manager_prompt.py::test_binance_manager_response_contract_rejects_unresolved_requested_symbol_coverage tests/test_binance_manager_prompt.py::test_binance_manager_response_contract_rejects_requested_symbol_coverage_trigger_for_other_symbol tests/test_binance_manager_prompt.py::test_binance_manager_response_contract_allows_unrelated_action_for_non_hard_requested_symbol_coverage tests/test_binance_manager_prompt.py::test_binance_manager_response_contract_keeps_hard_requested_symbol_coverage_blocking_unrelated_action -q
```

Expected: all pass.

- [ ] **Step 2: Run candidate compaction tests**

Run:

```bash
pytest tests/test_binance_manager_prompt.py::test_candidate_compaction_preserves_volatile_attack_lane_under_tight_limit tests/test_binance_manager_prompt.py::test_final_budget_candidate_compaction_preserves_deep_volatile_attack tests/test_binance_manager_prompt.py::test_final_budget_candidate_compaction_keeps_more_than_four_lane_diverse_rows -q
```

Expected: all pass.

- [ ] **Step 3: Run full focused file if practical**

Run:

```bash
pytest tests/test_binance_manager_prompt.py -q
```

Expected: pass. If runtime is too long, report the partial targeted commands and the reason full file was not completed.

### Task 4: Promote Response Repair Resolution Into Action Metadata

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

**Interfaces:**
- Consumes: `BinanceBlockTrader._apply_validation_repair_to_actions(actions, validation_repair=...)`
- Produces: Optional `manager_response` and `prompt` arguments that attach same-symbol `validation_repair_resolution.resolved_candidates[]` details to action metadata without weakening manager contract checks.

- [ ] **Step 1: Write the failing test**

Add a test near existing Binance validation repair action tests:

```python
def test_validation_repair_response_resolution_adds_wiki_repair_metadata_for_action_contract() -> None:
    prompt = {
        "execution_gate": {
            "status": "ok",
            "execution": {"spot_orders_enabled": True, "upbit_orders_enabled": True},
        },
        "jue_wiki": {
            "pages": [
                {
                    "page_id": "binance.risk.trading_validation",
                    "effectiveness": {
                        "status": "degraded",
                        "reasons": ["validation repair evidence degraded"],
                    },
                }
            ]
        },
        "validation_repair": {
            "repair_item_count": 1,
            "repair_backlog": [
                {
                    "discipline_id": "cost_simulation",
                    "allowed_entry_posture": "fractional_kelly_probe",
                    "scale_up_blocked": True,
                }
            ],
        },
    }
    response = {
        "validation_repair_resolution": {
            "resolved_candidates": [
                {
                    "symbol": "KRW-XPL",
                    "market": "upbit_spot",
                    "resolution": "updated_price_geometry",
                    "evidence_gap": "원 후보 진입가가 2R 조건에 부족해 가격을 낮췄다.",
                    "memory_contract_resolution": (
                        "cite_memory_and_apply: 검증 복구 메모리를 적용해 "
                        "대기형 소액 프로브로 재설계"
                    ),
                    "next_trigger": "162.69원 이하 대기 진입",
                }
            ]
        }
    }

    adjusted = BinanceBlockTrader._apply_validation_repair_to_actions(
        {
            "create_blocks": [
                {
                    "symbol": "KRW-XPL",
                    "market": "upbit_spot",
                    "side": "long",
                    "entry_style": "wait_for_price",
                    "entry_price": 162.69,
                    "target_price": 166.02,
                    "stop_price": 161.03,
                    "qty": 30.7,
                    "thesis": "검증 복구 가격 재설계",
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        },
        validation_repair=prompt["validation_repair"],
        manager_response=response,
        prompt=prompt,
    )

    row = adjusted["create_blocks"][0]
    metadata = row["metadata"]
    assert metadata["validation_repair_resolution"]["symbol"] == "KRW-XPL"
    assert "binance.risk.trading_validation" in json.dumps(
        metadata["jue_wiki_repair_pressure"],
        ensure_ascii=False,
    )
    assert (
        manager_response_contract_error(
            prompt=prompt,
            response=response,
            actions=adjusted,
            hold_decision={"summary": "액션 실행"},
        )
        == ""
    )
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_validation_repair_response_resolution_adds_wiki_repair_metadata_for_action_contract -q
```

Expected: fail before implementation because `_apply_validation_repair_to_actions` does not accept or use `manager_response` / `prompt`.

- [ ] **Step 3: Write minimal implementation**

Update `_apply_validation_repair_to_actions` to accept `manager_response: dict[str, Any] | None = None` and `prompt: dict[str, Any] | None = None`. For each action row, find a same-symbol row from `manager_response["validation_repair_resolution"]["resolved_candidates"]`, then attach compact metadata:

```python
row_metadata.setdefault("validation_repair_resolution", matched_resolution)
row_metadata.setdefault("jue_wiki_repair_pressure", {...})
row_metadata.setdefault("jue_wiki_repair_resolution", {...})
```

Include degraded wiki `page_id`s from `prompt["jue_wiki"]["pages"]` where `effectiveness.status == "degraded"` so the action metadata is prompt-linked.

- [ ] **Step 4: Run test to verify green**

Run the same command from Step 2. Expected: pass.

- [ ] **Step 5: Run focused regression**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_validation_repair_forces_binance_waiting_probe_create_action tests/test_binance_block_trader.py::test_validation_repair_action_metadata_drops_full_prompt_sections tests/test_binance_block_trader.py::test_validation_repair_response_resolution_adds_wiki_repair_metadata_for_action_contract -q
```

Expected: pass.
