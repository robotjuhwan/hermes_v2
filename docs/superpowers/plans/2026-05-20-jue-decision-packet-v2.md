# Jue Decision Packet v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen Jue's 30-minute trading judgment by adding a standardized decision packet: previous decision review, compact technical features, horizon-aware stop policy, block budget reasoning, and stricter JSON outputs.

**Architecture:** Keep the current KIS block ledger, rule executor, memory, market pulse, ETF research, and strategy intelligence intact. Add a focused `jue_decision_packet` service that composes the data Jue must review before each manager run, then wire it into `KISBlockTrader.run_manager_once()`. The rule executor still owns actual order execution; Jue only creates/updates/closes blocks through the existing gated action flow.

**Tech Stack:** Python 3.10+, pytest, SQLite-backed block repository, existing `KISBlockTrader`, existing `InvestmentMemoryService`, existing static UI.

---

## File Structure

- Create `src/tradecraft/services/jue_decision_packet.py`: pure helper functions for technical feature extraction, previous decision review, stop policy summaries, and final packet assembly.
- Modify `src/tradecraft/services/kis_block_trader.py`: add `decision_packet_v2` to the manager prompt, extend output schema, and persist accepted decision metadata into block metadata/events.
- Modify `src/tradecraft/services/investment_memory.py`: include compact decision packet outcomes in memory context and reflection prompts.
- Modify `src/tradecraft/web/static/app.js`: expose packet highlights in the block detail/history UI.
- Modify `src/tradecraft/web/static/style.css`: style decision packet chips and review rows.
- Test `tests/test_jue_decision_packet.py`: isolated tests for packet construction.
- Modify `tests/test_kis_block_trader.py`: prompt/schema/action persistence tests.
- Modify `tests/test_investment_memory.py`: memory context includes packet review summaries.
- Modify `tests/test_api_smoke.py` if snapshot shape changes are exposed through `/api/kis/blocks`.

---

## Task 1: Decision Packet Service

**Files:**
- Create: `src/tradecraft/services/jue_decision_packet.py`
- Test: `tests/test_jue_decision_packet.py`

- [ ] **Step 1: Write failing tests for stop policy and technical features**

Create `tests/test_jue_decision_packet.py`:

```python
from __future__ import annotations

from tradecraft.services.jue_decision_packet import build_decision_packet


def test_mid_block_stop_touch_requires_manager_review_not_immediate_exit() -> None:
    packet = build_decision_packet(
        account={"cash_krw": 1_000_000, "positions": []},
        blocks=[
            {
                "block_id": "blk_mid",
                "symbol": "012330",
                "name": "현대모비스",
                "status": "open",
                "qty_open": 1,
                "entry_price": 546000,
                "target_price": 563000,
                "stop_price": 520000,
                "metadata": {"horizon": "mid"},
            }
        ],
        quotes=[
            {
                "symbol": "012330",
                "price": 520000,
                "raw": {
                    "stck_oprc": "532000",
                    "stck_hgpr": "561000",
                    "stck_lwpr": "515000",
                    "acml_vol": "787096",
                    "prdy_ctrt": "-0.95",
                    "pgtr_ntby_qty": "-236120",
                },
            }
        ],
        recent_events=[
            {
                "block_id": "blk_mid",
                "event_type": "exit_signal",
                "message": "mid block touched stop_reached; next 30m manager review will decide action",
                "payload": {"reason": "stop_reached", "price": 520000},
                "created_at": "2026-05-20T04:55:05+00:00",
            }
        ],
        previous_manager_runs=[],
        market_pulse={"status": "ok", "regime": "risk_off"},
    )

    block = packet["blocks"][0]
    assert block["horizon"] == "mid"
    assert block["stop_policy"]["touch_action"] == "manager_review"
    assert block["stop_policy"]["latest_signal"]["reason"] == "stop_reached"
    assert block["technical"]["intraday_position_pct"] == 13.04
    assert block["technical"]["program_net_qty"] == -236120


def test_short_block_stop_touch_is_rule_exit() -> None:
    packet = build_decision_packet(
        account={"cash_krw": 1_000_000, "positions": []},
        blocks=[
            {
                "block_id": "blk_short",
                "symbol": "277810",
                "name": "레인보우로보틱스",
                "status": "open",
                "qty_open": 2,
                "entry_price": 624000,
                "target_price": 650000,
                "stop_price": 622000,
                "metadata": {"horizon": "short"},
            }
        ],
        quotes=[{"symbol": "277810", "price": 621000, "raw": {"stck_hgpr": "635000", "stck_lwpr": "620000"}}],
        recent_events=[],
        previous_manager_runs=[],
        market_pulse={},
    )

    assert packet["blocks"][0]["stop_policy"]["touch_action"] == "rule_exit"
```

Run:

```bash
pytest tests/test_jue_decision_packet.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'tradecraft.services.jue_decision_packet'`.

- [ ] **Step 2: Implement minimal packet builder**

Create `src/tradecraft/services/jue_decision_packet.py`:

```python
from __future__ import annotations

import json
from typing import Any


BLOCK_HORIZONS = {"short", "mid", "long", "core_etf"}


def _safe_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "").replace("%", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_horizon(value: Any) -> str:
    text = str(value or "short").strip().lower()
    return text if text in BLOCK_HORIZONS else "short"


def _raw_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _technical_features(quote: dict[str, Any]) -> dict[str, Any]:
    raw = _raw_dict(quote.get("raw") or quote.get("raw_json"))
    price = _safe_float(quote.get("price") or raw.get("stck_prpr"))
    high = _safe_float(raw.get("stck_hgpr"))
    low = _safe_float(raw.get("stck_lwpr"))
    open_price = _safe_float(raw.get("stck_oprc"))
    range_width = max(high - low, 0.0)
    intraday_position_pct = 0.0
    if price > 0 and range_width > 0:
        intraday_position_pct = round((price - low) / range_width * 100, 2)
    drawdown_from_high_pct = round((price - high) / high * 100, 2) if high else 0.0
    rebound_from_low_pct = round((price - low) / low * 100, 2) if low else 0.0
    return {
        "price": price,
        "open": open_price,
        "high": high,
        "low": low,
        "change_pct": _safe_float(raw.get("prdy_ctrt") or quote.get("change_pct")),
        "volume": _safe_int(raw.get("acml_vol") or quote.get("volume")),
        "program_net_qty": _safe_int(raw.get("pgtr_ntby_qty")),
        "intraday_position_pct": intraday_position_pct,
        "drawdown_from_high_pct": drawdown_from_high_pct,
        "rebound_from_low_pct": rebound_from_low_pct,
    }


def _latest_exit_signal(block_id: str, recent_events: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(recent_events):
        if str(row.get("block_id") or "") != block_id:
            continue
        if str(row.get("event_type") or "") != "exit_signal":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return {
            "reason": str(payload.get("reason") or ""),
            "price": _safe_float(payload.get("price")),
            "created_at": str(row.get("created_at") or ""),
        }
    return {}


def _stop_policy(block: dict[str, Any], technical: dict[str, Any], recent_events: list[dict[str, Any]]) -> dict[str, Any]:
    block_id = str(block.get("block_id") or "")
    horizon = _normalize_horizon((block.get("metadata") or {}).get("horizon"))
    stop_price = _safe_float(block.get("stop_price"))
    target_price = _safe_float(block.get("target_price"))
    price = _safe_float(technical.get("price"))
    stop_touched = bool(stop_price and price and price <= stop_price)
    target_touched = bool(target_price and price and price >= target_price)
    touch_action = "rule_exit" if horizon == "short" else "manager_review"
    return {
        "horizon": horizon,
        "stop_price": stop_price,
        "target_price": target_price,
        "stop_touched_now": stop_touched,
        "target_touched_now": target_touched,
        "touch_action": touch_action,
        "latest_signal": _latest_exit_signal(block_id, recent_events),
        "instruction": (
            "Short blocks may exit by rule. Mid/long/core_etf blocks require manager review after touch."
        ),
    }


def _previous_decision_reviews(previous_manager_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for run in list(previous_manager_runs)[-5:]:
        reviews.append(
            {
                "run_id": run.get("id"),
                "run_at": run.get("run_at"),
                "status": run.get("status"),
                "model": run.get("model"),
                "action_counts": {
                    key: len(run.get("actions", {}).get(key, []))
                    for key in ("create_blocks", "update_blocks", "close_blocks", "adopt_existing_blocks")
                    if isinstance(run.get("actions"), dict)
                },
            }
        )
    return reviews


def build_decision_packet(
    *,
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
    previous_manager_runs: list[dict[str, Any]],
    market_pulse: dict[str, Any],
) -> dict[str, Any]:
    quote_map = {str(row.get("symbol") or ""): row for row in quotes}
    packet_blocks: list[dict[str, Any]] = []
    for block in blocks:
        metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        horizon = _normalize_horizon(metadata.get("horizon"))
        quote = quote_map.get(str(block.get("symbol") or ""), {})
        technical = _technical_features(quote)
        packet_blocks.append(
            {
                "block_id": block.get("block_id"),
                "symbol": block.get("symbol"),
                "name": block.get("name"),
                "status": block.get("status"),
                "horizon": horizon,
                "qty_open": _safe_int(block.get("qty_open")),
                "entry_price": _safe_float(block.get("entry_price")),
                "technical": technical,
                "stop_policy": _stop_policy(block, technical, recent_events),
            }
        )
    return {
        "version": "jue.decision_packet.v2",
        "account": account,
        "blocks": packet_blocks,
        "previous_decision_reviews": _previous_decision_reviews(previous_manager_runs),
        "market_pulse": market_pulse,
    }
```

- [ ] **Step 3: Run packet tests**

Run:

```bash
pytest tests/test_jue_decision_packet.py -q
```

Expected: pass.

- [ ] **Step 4: Commit Task 1**

```bash
git add src/tradecraft/services/jue_decision_packet.py tests/test_jue_decision_packet.py
git commit -m "feat: add jue decision packet builder"
```

---

## Task 2: Wire Decision Packet into Jue Manager Prompt

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write failing prompt test**

Add this test near the existing manager prompt tests in `tests/test_kis_block_trader.py`:

```python
def test_manager_prompt_includes_decision_packet_v2_for_horizon_stop_review(tmp_path: Path) -> None:
    kis = _FakeKIS()
    kis.positions["012330"] = 1
    kis.prices["012330"] = 520000
    llm = _FakeLLM({"create_blocks": [], "update_blocks": [], "close_blocks": [], "pause_blocks": [], "adopt_existing_blocks": []})
    trader = KISBlockTrader(
        config=KISBlockTraderConfig(db_path=str(tmp_path / "blocks.db"), enabled=True, execute_orders=False),
        kis=kis,  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
    )
    trader.repository.create_block(
        {
            "block_id": "blk_mid",
            "symbol": "012330",
            "name": "현대모비스",
            "qty_initial": 1,
            "qty_open": 1,
            "entry_price": 546000,
            "target_price": 563000,
            "stop_price": 520000,
            "thesis": "중기 확인형",
            "llm_reason": "",
            "risk_note": "",
            "created_by": "llm",
            "manager_run_id": None,
            "status": "open",
            "metadata": {"horizon": "mid"},
        }
    )

    asyncio.run(trader.run_manager_once())

    prompt = json.loads(llm.calls[0]["messages"][1]["content"])
    assert prompt["decision_packet_v2"]["version"] == "jue.decision_packet.v2"
    packet_block = prompt["decision_packet_v2"]["blocks"][0]
    assert packet_block["horizon"] == "mid"
    assert packet_block["stop_policy"]["touch_action"] == "manager_review"
    assert "decision_class" in prompt["output_schema"]["close_blocks"][0]
    assert "target_block_value_krw" in prompt["output_schema"]["create_blocks"][0]
```

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_includes_decision_packet_v2_for_horizon_stop_review -q
```

Expected: fail because `decision_packet_v2` is not in the prompt.

- [ ] **Step 2: Import and call packet builder**

Modify `src/tradecraft/services/kis_block_trader.py`:

```python
from tradecraft.services.jue_decision_packet import build_decision_packet
```

Inside `run_manager_once()`, after `market_pulse = self._market_pulse_context(...)`, add:

```python
previous_manager_runs = self.repository.list_manager_runs(limit=5)
decision_packet_v2 = build_decision_packet(
    account=account,
    blocks=blocks,
    quotes=quotes,
    recent_events=self.repository.list_events(limit=80),
    previous_manager_runs=previous_manager_runs,
    market_pulse=market_pulse,
)
```

If `KISBlockRepository` does not expose `list_manager_runs`, add:

```python
def list_manager_runs(self, *, limit: int = 5) -> list[dict[str, Any]]:
    with self._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, run_at, market_session, status, mode, model, error_message,
                   prompt_json, response_json, actions_json
            FROM manager_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        ).fetchall()
    return [self._row_to_manager_run(row) for row in reversed(rows)]
```

Add to the manager prompt:

```python
"decision_packet_v2": decision_packet_v2,
```

- [ ] **Step 3: Extend manager output schema**

Replace each schema item with richer fields while keeping old fields valid:

```python
"create_blocks": [
    {
        "symbol": "6-digit",
        "qty": "integer",
        "target_block_value_krw": "number; desired capital assigned to this block",
        "max_loss_krw": "number; estimated loss at stop price",
        "target_price": "number",
        "stop_price": "number",
        "stop_policy": "immediate_for_short|intraday_touch_review|close_below|manager_confirmed_close",
        "decision_class": "opportunity_add|risk_reduce|rebalance|thesis_confirm|noise_ignore",
        "entry_style": "aggressive_limit",
        "horizon": "short|mid|long|core_etf",
        "allocation_reason": "why this block improves portfolio balance",
        "thesis": "string",
        "confidence": "0.0-1.0",
        "risk_note": "string",
        "what_would_change_my_mind": "specific price/data condition",
        "post_review_required": "boolean",
    }
],
"close_blocks": [
    {
        "block_id": "string",
        "reason": "string",
        "decision_class": "rule_follow|thesis_broken|risk_reduce|manual_instruction",
        "stop_policy": "manager_confirmed_close|close_below|manual_close",
        "what_would_change_my_mind": "condition that would have prevented this close",
        "post_review_required": "boolean",
    }
],
```

- [ ] **Step 4: Run prompt test**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_includes_decision_packet_v2_for_horizon_stop_review -q
```

Expected: pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/tradecraft/services/kis_block_trader.py tests/test_kis_block_trader.py
git commit -m "feat: feed decision packet into jue manager prompt"
```

---

## Task 3: Persist Decision Metadata on Accepted Actions

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write failing persistence test**

Add:

```python
def test_manager_persists_decision_packet_fields_on_created_block(tmp_path: Path) -> None:
    kis = _FakeKIS()
    kis.prices["005930"] = 80000
    llm = _FakeLLM(
        {
            "create_blocks": [
                {
                    "symbol": "005930",
                    "qty": 3,
                    "target_block_value_krw": 240000,
                    "max_loss_krw": 12000,
                    "target_price": 84000,
                    "stop_price": 76000,
                    "stop_policy": "intraday_touch_review",
                    "decision_class": "opportunity_add",
                    "entry_style": "aggressive_limit",
                    "horizon": "mid",
                    "allocation_reason": "중기 현금 배분",
                    "thesis": "리포트와 수급이 동조",
                    "confidence": 0.66,
                    "risk_note": "지수 약세 시 축소",
                    "what_would_change_my_mind": "종가 76000 이탈",
                    "post_review_required": True,
                }
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
            "adopt_existing_blocks": [],
        }
    )
    trader = KISBlockTrader(
        config=KISBlockTraderConfig(db_path=str(tmp_path / "blocks.db"), enabled=True, execute_orders=False),
        kis=kis,  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
    )

    result = asyncio.run(trader.run_manager_once())
    block_id = result["applied"]["created"][0]["block"]["block_id"]
    block = trader.repository.get_block(block_id)

    assert block["metadata"]["target_block_value_krw"] == 240000
    assert block["metadata"]["max_loss_krw"] == 12000
    assert block["metadata"]["stop_policy"] == "intraday_touch_review"
    assert block["metadata"]["decision_class"] == "opportunity_add"
    assert block["metadata"]["what_would_change_my_mind"] == "종가 76000 이탈"
    assert block["metadata"]["post_review_required"] is True
```

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_persists_decision_packet_fields_on_created_block -q
```

Expected: fail because metadata fields are not persisted.

- [ ] **Step 2: Add metadata extraction helper**

In `src/tradecraft/services/kis_block_trader.py`, add:

```python
DECISION_METADATA_KEYS = (
    "target_block_value_krw",
    "max_loss_krw",
    "stop_policy",
    "decision_class",
    "what_would_change_my_mind",
    "post_review_required",
)


def _decision_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in DECISION_METADATA_KEYS:
        if key in row:
            metadata[key] = row[key]
    return metadata
```

In create/adopt block payload construction, merge:

```python
metadata = {
    **metadata,
    **_decision_metadata(row),
}
```

In update/close event payloads, include `_decision_metadata(row)` so block history can show why Jue acted.

- [ ] **Step 3: Run persistence test**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_persists_decision_packet_fields_on_created_block -q
```

Expected: pass.

- [ ] **Step 4: Run nearby block tests**

Run:

```bash
pytest tests/test_kis_block_trader.py -q
```

Expected: pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/tradecraft/services/kis_block_trader.py tests/test_kis_block_trader.py
git commit -m "feat: persist jue decision metadata on blocks"
```

---

## Task 4: Previous Decision Outcome Review

**Files:**
- Modify: `src/tradecraft/services/jue_decision_packet.py`
- Test: `tests/test_jue_decision_packet.py`

- [ ] **Step 1: Write failing outcome review test**

Append:

```python
def test_previous_decision_review_summarizes_action_counts_and_close_reasons() -> None:
    packet = build_decision_packet(
        account={},
        blocks=[],
        quotes=[],
        recent_events=[
            {
                "block_id": "blk_mid",
                "event_type": "order",
                "message": "sell 1 @ 525000 sent",
                "payload": {"reason": "force_exit_requested", "side": "sell"},
                "created_at": "2026-05-20T05:04:46+00:00",
            }
        ],
        previous_manager_runs=[
            {
                "id": 111,
                "run_at": "2026-05-20T05:04:20+00:00",
                "status": "ok",
                "model": "gpt-5.5",
                "actions": {"close_blocks": [{"block_id": "blk_mid"}], "create_blocks": []},
            }
        ],
        market_pulse={},
    )

    review = packet["previous_decision_reviews"][0]
    assert review["run_id"] == 111
    assert review["action_counts"]["close_blocks"] == 1
    assert packet["recent_execution_summary"]["sell_reasons"]["force_exit_requested"] == 1
```

Run:

```bash
pytest tests/test_jue_decision_packet.py::test_previous_decision_review_summarizes_action_counts_and_close_reasons -q
```

Expected: fail because `recent_execution_summary` does not exist.

- [ ] **Step 2: Implement recent execution summary**

Add:

```python
def _recent_execution_summary(recent_events: list[dict[str, Any]]) -> dict[str, Any]:
    sell_reasons: dict[str, int] = {}
    exit_signals: dict[str, int] = {}
    for row in recent_events:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if str(row.get("event_type") or "") == "order" and payload.get("side") == "sell":
            reason = str(payload.get("reason") or "unknown")
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        if str(row.get("event_type") or "") == "exit_signal":
            reason = str(payload.get("reason") or "unknown")
            exit_signals[reason] = exit_signals.get(reason, 0) + 1
    return {"sell_reasons": sell_reasons, "exit_signals": exit_signals}
```

Return it from `build_decision_packet()`:

```python
"recent_execution_summary": _recent_execution_summary(recent_events),
```

- [ ] **Step 3: Run packet tests**

Run:

```bash
pytest tests/test_jue_decision_packet.py -q
```

Expected: pass.

- [ ] **Step 4: Commit Task 4**

```bash
git add src/tradecraft/services/jue_decision_packet.py tests/test_jue_decision_packet.py
git commit -m "feat: summarize recent jue decision outcomes"
```

---

## Task 5: Memory Context Uses Decision Packet Outcomes

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write failing memory context test**

Add:

```python
def test_context_pack_includes_decision_packet_outcome_summary(tmp_path: Path) -> None:
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        )
    )
    service.initialize()
    pack = service.context_pack(
        context={
            "decision_packet_v2": {
                "version": "jue.decision_packet.v2",
                "recent_execution_summary": {
                    "sell_reasons": {"force_exit_requested": 1},
                    "exit_signals": {"stop_reached": 1},
                },
            }
        }
    )

    assert pack["decision_packet_v2"]["version"] == "jue.decision_packet.v2"
    assert pack["decision_packet_v2"]["recent_execution_summary"]["sell_reasons"]["force_exit_requested"] == 1
```

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_decision_packet_outcome_summary -q
```

Expected: fail until context pack preserves `decision_packet_v2`.

- [ ] **Step 2: Preserve compact packet fields**

In `InvestmentMemoryService.context_pack()`, when `context` contains `decision_packet_v2`, add:

```python
packet = context.get("decision_packet_v2") if isinstance(context, dict) else None
if isinstance(packet, dict):
    out["decision_packet_v2"] = {
        "version": str(packet.get("version") or ""),
        "recent_execution_summary": packet.get("recent_execution_summary") or {},
        "previous_decision_reviews": list(packet.get("previous_decision_reviews") or [])[-5:],
    }
```

Keep only compact fields so prompts stay bounded.

- [ ] **Step 3: Feed packet into memory context provider call**

In `KISBlockTrader.run_manager_once()`, extend `_investment_memory_context(...)` call to include the packet:

```python
memory_context = self._investment_memory_context(
    symbols=symbols,
    block_ids=[...],
    blocks=blocks,
    account=account,
    quotes=quotes,
    strategy=strategy_payload,
    market_judgment=latest_judgment,
    allocation=allocation,
    portfolio_balance=portfolio_balance,
    etf_research=etf_research,
    decision_packet_v2=decision_packet_v2,
)
```

If `_investment_memory_context()` currently has a fixed signature, add `decision_packet_v2: dict[str, Any] | None = None` and put it into the context dict passed to the provider.

- [ ] **Step 4: Run memory test**

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_decision_packet_outcome_summary -q
```

Expected: pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/tradecraft/services/investment_memory.py src/tradecraft/services/kis_block_trader.py tests/test_investment_memory.py
git commit -m "feat: feed jue decision outcomes into memory"
```

---

## Task 6: UI Exposure for Decision Packet Highlights

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `node --check src/tradecraft/web/static/app.js`

- [ ] **Step 1: Add rendering expectations**

In the block card/detail renderer, show these metadata fields when present:

```js
const decisionMeta = block.metadata || {};
const decisionClass = decisionMeta.decision_class || "";
const stopPolicy = decisionMeta.stop_policy || "";
const maxLoss = Number(decisionMeta.max_loss_krw || 0);
const mindChange = decisionMeta.what_would_change_my_mind || "";
```

Render chips:

```js
[
  decisionClass && `<span class="block-chip decision">${escapeHtml(decisionClass)}</span>`,
  stopPolicy && `<span class="block-chip stop-policy">${escapeHtml(stopPolicy)}</span>`,
  maxLoss > 0 && `<span class="block-chip risk">최대손실 ${formatKrw(maxLoss)}</span>`,
].filter(Boolean).join("")
```

Render detail text:

```js
mindChange
  ? `<div class="block-decision-note"><span>판단 변경 조건</span><strong>${escapeHtml(mindChange)}</strong></div>`
  : ""
```

- [ ] **Step 2: Add CSS**

Add:

```css
.block-chip.decision {
  border-color: rgba(94, 224, 194, 0.34);
  color: var(--focus-accent);
}

.block-chip.stop-policy {
  border-color: rgba(154, 184, 200, 0.34);
  color: var(--source-blue);
}

.block-chip.risk {
  border-color: rgba(216, 181, 109, 0.34);
  color: var(--evidence-gold);
}

.block-decision-note {
  display: grid;
  gap: 4px;
  padding: 8px 10px;
  border: 1px solid var(--border-subtle);
  background: var(--surface-2);
}

.block-decision-note span {
  color: var(--text-muted);
  font-size: 12px;
}

.block-decision-note strong {
  color: var(--text-main);
  font-size: 13px;
  line-height: 1.45;
}
```

- [ ] **Step 3: Static JS check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no output and exit code 0.

- [ ] **Step 4: Commit Task 6**

```bash
git add src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css
git commit -m "feat: show jue decision metadata in block UI"
```

---

## Task 7: Final Verification

**Files:**
- Verify changed files only.

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_jue_decision_packet.py tests/test_kis_block_trader.py tests/test_investment_memory.py -q
```

Expected: pass.

- [ ] **Step 2: Run API smoke tests**

```bash
pytest tests/test_api_smoke.py tests/test_kis_trader_api.py -q
```

Expected: pass.

- [ ] **Step 3: Run static frontend check**

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no output and exit code 0.

- [ ] **Step 4: Run whitespace check**

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 5: Restart local services for manual verification**

Use the existing local process pattern:

```bash
ps -ax -o pid=,command= | rg "uvicorn tradecraft\\.main:app|tradecraft\\.runtime\\.kis_block_trader_runner"
```

Terminate only the matching old control/block-trader processes, then restart:

```bash
.venv/bin/python -m uvicorn tradecraft.main:app --host 127.0.0.1 --port 18080 >> .runtime/tradecraft-control-18080.log 2>&1 &
.venv/bin/python -m tradecraft.runtime.kis_block_trader_runner >> .runtime/tradecraft-kis-block-trader.log 2>&1 &
```

Verify:

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path(".runtime/kis_block_trader.json")
print(json.dumps(json.loads(p.read_text()), ensure_ascii=False, indent=2)[:2000])
PY
```

Expected: snapshot updates and manager prompt preview contains `decision_packet_v2`.

---

## Self-Review

- Spec coverage: The plan implements previous decision review, technical feature pack, richer JSON output, block budget metadata, stop policy semantics, memory feedback, and UI visibility.
- Placeholder scan: No open placeholders remain. Each task has explicit files, commands, and expected results.
- Type consistency: `decision_packet_v2`, `target_block_value_krw`, `max_loss_krw`, `stop_policy`, `decision_class`, `what_would_change_my_mind`, and `post_review_required` are introduced once and reused consistently.
- Scope check: This plan does not add chart-image analysis or new KIS OHLCV endpoints. It uses existing quote snapshots and KIS raw quote fields first, which keeps the first strengthening round small and testable.
