# Binance Lane Context Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Binance manager lane distribution and near-duplicate context logic out of `BinanceBlockTrader` into a focused helper module without changing trading behavior.

**Architecture:** `src/tradecraft/services/binance_manager_lane_context.py` owns pure lane/duplicate context builders. `BinanceBlockTrader` keeps backward-compatible private wrapper methods that delegate to the helper so existing tests and callers continue to work.

**Tech Stack:** Python 3.10+, pytest, existing static service modules.

---

### Task 1: Add Helper Contract Tests

**Files:**
- Create: `tests/test_binance_manager_lane_context.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import pytest

from tradecraft.services.binance_manager_lane_context import (
    candidate_near_duplicate_active_block_context,
    lane_distribution,
    manager_lane_balance_context,
    near_duplicate_active_blocks_context,
)


def test_lane_distribution_counts_canonical_binance_lanes() -> None:
    payload = lane_distribution(
        [
            {"market": "spot", "side": "long"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
        ]
    )
    assert payload["total"] == 3
    assert payload["items"]["spot:long"]["count"] == 1
    assert payload["items"]["futures:short"]["count"] == 2
    assert payload["dominant_lane"] == "futures:short"


def test_near_duplicate_context_groups_active_blocks_by_price_geometry() -> None:
    payload = near_duplicate_active_blocks_context(
        [
            {
                "block_id": "a",
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "entry_price": 100.0,
                "target_price": 90.0,
                "stop_price": 105.0,
                "metadata": {"horizon": "futures"},
            },
            {
                "block_id": "b",
                "symbol": "BTCUSDT",
                "market": "futures",
                "side": "short",
                "status": "proposed",
                "entry_price": 100.2,
                "target_price": 90.1,
                "stop_price": 105.1,
                "metadata": {"horizon": "futures"},
            },
        ],
        tolerance_bps=75.0,
    )
    assert payload["status"] == "review_required"
    assert payload["groups"][0]["block_ids"] == ["a", "b"]


def test_candidate_duplicate_context_requires_same_horizon_and_prices() -> None:
    payload = candidate_near_duplicate_active_block_context(
        {
            "symbol": "PAXGUSDT",
            "market": "futures",
            "side": "short",
            "horizon": "futures",
            "entry_price": 4172.94,
            "target_price": 4106.18,
            "stop_price": 4206.33,
        },
        [
            {
                "block_id": "bnb_futures_PAXGUSDT_open",
                "symbol": "PAXGUSDT",
                "market": "futures",
                "side": "short",
                "status": "open",
                "entry_price": 4154.65,
                "target_price": 4088.18,
                "stop_price": 4187.89,
                "metadata": {"horizon": "futures"},
            }
        ],
        tolerance_bps=75.0,
    )
    assert payload["status"] == "review_required"
    assert payload["existing_block_id"] == "bnb_futures_PAXGUSDT_open"
    assert payload["candidate"]["entry_price"] == pytest.approx(4172.94)


def test_manager_lane_balance_context_combines_recent_active_candidate_and_performance() -> None:
    payload = manager_lane_balance_context(
        recent_blocks=[
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "futures", "side": "short"},
            {"market": "spot", "side": "long"},
        ],
        active_blocks=[],
        candidates=[{"market": "spot", "side": "long"}],
        performance={"side_scorecards": [{"side": "spot:long", "sample_count": 2}]},
        tolerance_bps=75.0,
    )
    assert payload["version"] == "binance_lane_balance_v1"
    assert payload["dominant_lane"] == "futures:short"
    assert payload["recent_blocks"]["requires_review"] is True
    assert payload["candidate_lanes"]["items"]["spot:long"]["count"] == 1
    assert payload["performance_lanes"][0]["lane"] == "spot:long"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_binance_manager_lane_context.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'tradecraft.services.binance_manager_lane_context'`.

### Task 2: Extract Pure Helper Module

**Files:**
- Create: `src/tradecraft/services/binance_manager_lane_context.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`

- [ ] **Step 1: Implement helper functions**

Move the pure logic from `BinanceBlockTrader._lane_distribution`, `_near_duplicate_active_blocks_context`, `_candidate_near_duplicate_active_block_context`, and `_manager_lane_balance_context` into the new module. The helper functions take explicit arguments, including `tolerance_bps` and `recent_blocks`, and import lane/symbol normalization from existing focused modules.

- [ ] **Step 2: Delegate existing class methods**

Keep the private `BinanceBlockTrader` methods as compatibility wrappers. Each wrapper should call the new helper and pass `self.config.near_duplicate_block_price_tolerance_bps` or `self.repository.list_recent_strategy_blocks(limit=40)` where needed.

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest tests/test_binance_manager_lane_context.py \
  tests/test_binance_block_trader.py::test_candidate_near_duplicate_context_marks_existing_active_block \
  tests/test_binance_block_trader.py::test_manager_executable_candidates_annotates_near_duplicate_active_block \
  tests/test_binance_block_trader.py::test_binance_manager_prompt_surfaces_near_duplicate_active_blocks -q
```

Expected: PASS.

### Task 3: Static Verification

**Files:**
- Verify: `src/tradecraft/services/binance_manager_lane_context.py`
- Verify: `src/tradecraft/services/binance_block_trader.py`
- Verify: `tests/test_binance_manager_lane_context.py`

- [ ] **Step 1: Compile changed Python files**

Run:

```bash
python3 -m py_compile \
  src/tradecraft/services/binance_manager_lane_context.py \
  src/tradecraft/services/binance_block_trader.py \
  tests/test_binance_manager_lane_context.py
```

Expected: exit code 0.

- [ ] **Step 2: Check whitespace**

Run:

```bash
git diff --check -- \
  src/tradecraft/services/binance_manager_lane_context.py \
  src/tradecraft/services/binance_block_trader.py \
  tests/test_binance_manager_lane_context.py
```

Expected: exit code 0.
