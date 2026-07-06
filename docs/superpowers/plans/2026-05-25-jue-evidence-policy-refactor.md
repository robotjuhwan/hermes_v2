# Jue Evidence-To-Policy Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Jue's existing crypto research, quant, pattern, memory, and block-trading layers into one evidence → hypothesis → scorecard → policy → decision loop, without adding a separate “Jue Lab” subsystem.

**Architecture:** Keep the current service boundaries and databases. Standardize evidence emitted by `crypto_market_research`, `crypto_alpha`, `crypto_quant`, and `crypto_pattern_lab`; let `investment_memory` consume that evidence and convert proven findings into versioned policy rules; make KIS and Binance block managers receive a compact decision packet built from evidence, scorecards, active policies, and recent block outcomes. UI changes reorganize existing tabs around this loop instead of adding another standalone tab.

**Tech Stack:** Python 3.10, SQLite, FastAPI, static JavaScript/CSS, pytest, existing HERMES runtime runners.

---

## Mac Capacity Guardrails

- Current local machine: iMac21,1, Apple M1, 8 cores, 16GB RAM.
- This refactor must remain local-first and lightweight:
  - no mandatory InfluxDB, DuckDB, ClickHouse, or separate lab daemon;
  - no full Binance universe × all intervals × multi-year backtest cycle;
  - no local large-model training;
  - no direct GPL/AGPL code copying into HERMES.
- Existing bounded services are reused:
  - `crypto_market_research`: market snapshots, features, candidates, LLM crypto notes;
  - `crypto_alpha`: public catalyst events and outcome labels;
  - `crypto_quant`: compact directional quant signal/history/outcome store;
  - `crypto_pattern_lab`: external strategy idea extraction and local pattern scorecards;
  - `investment_memory`: reflections, policy scorecards, versioned policy rules;
  - `kis_block_trader` / `binance_block_trader`: block decision managers.

---

## File Structure

- Create `src/tradecraft/services/evidence_policy.py`
  - Shared evidence normalization, TTL, scorecard, and decision-packet helpers.
- Modify `src/tradecraft/services/crypto_market_research.py`
  - Expose market research notes/candidates/features as normalized evidence.
- Modify `src/tradecraft/services/crypto_alpha.py`
  - Expose catalyst events/outcomes/scorecards as normalized evidence.
- Modify `src/tradecraft/services/crypto_quant.py`
  - Expose quant signals and outcome history as normalized evidence.
- Modify `src/tradecraft/services/crypto_pattern_lab.py`
  - Store external source license policy inside existing pattern source tables and expose pattern backtests as scorecard evidence.
- Modify `src/tradecraft/services/investment_memory.py`
  - Ingest evidence scorecards and convert them into existing versioned policy rules.
- Modify `src/tradecraft/services/binance_block_trader.py`
  - Replace loose research blobs in the manager prompt with a compact decision packet.
- Modify `src/tradecraft/services/kis_block_trader.py`
  - Add generalized policy lessons from the same packet, excluding crypto-specific evidence from KIS asset evidence.
- Modify `src/tradecraft/main.py`
  - Add API endpoints to inspect evidence and policy flow using existing admin auth.
- Modify `src/tradecraft/web/static/index.html`
  - Keep existing major tabs; do not add `쥬 연구소`.
- Modify `src/tradecraft/web/static/app.js`
  - Reorganize `크립토 리서치` and `쥬 메모리` around evidence/scorecard/policy flow.
- Modify `src/tradecraft/web/static/style.css`
  - Add small flow/scorecard UI styles.
- Tests:
  - Create `tests/test_evidence_policy.py`
  - Update `tests/test_crypto_market_research.py`
  - Update `tests/test_crypto_alpha.py`
  - Update `tests/test_crypto_quant.py`
  - Update `tests/test_crypto_pattern_lab.py`
  - Update `tests/test_investment_memory.py`
  - Update `tests/test_binance_block_trader.py`
  - Update `tests/test_kis_block_trader.py`
  - Update `tests/test_api_smoke.py`
  - Update `tests/test_static_ui.py`

---

### Task 1: Add Shared Evidence And Scorecard Normalization

**Files:**
- Create: `src/tradecraft/services/evidence_policy.py`
- Test: `tests/test_evidence_policy.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_evidence_policy.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradecraft.services.evidence_policy import (
    EvidenceItem,
    build_decision_packet,
    evidence_from_signal,
    normalize_scope,
    scorecard_from_evidence,
)


def test_evidence_from_signal_has_stable_identity_and_ttl() -> None:
    captured_at = datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc).isoformat()

    item = evidence_from_signal(
        source="crypto_quant",
        signal_type="directional_quant",
        symbol="btcusdt",
        scope="binance",
        confidence=0.72,
        ttl_sec=900,
        captured_at=captured_at,
        payload={"bias": "long", "long_score": 82},
    )

    assert item.evidence_id.startswith("ev_")
    assert item.symbol == "BTCUSDT"
    assert item.scope == "binance"
    assert item.signal_type == "directional_quant"
    assert item.expires_at > captured_at
    assert item.payload["bias"] == "long"


def test_evidence_item_marks_expired() -> None:
    item = EvidenceItem(
        evidence_id="ev_old",
        source="crypto_alpha",
        signal_type="catalyst",
        symbol="ETHUSDT",
        scope="binance",
        confidence=0.5,
        captured_at="2026-05-25T00:00:00+00:00",
        expires_at="2026-05-25T00:10:00+00:00",
        payload={},
    )

    assert item.is_expired(now="2026-05-25T00:11:00+00:00") is True
    assert item.is_expired(now="2026-05-25T00:09:00+00:00") is False


def test_scorecard_and_decision_packet_compact_evidence() -> None:
    fresh = evidence_from_signal(
        source="crypto_pattern_lab",
        signal_type="pattern_scorecard",
        symbol="BTCUSDT",
        scope="binance",
        confidence=0.8,
        ttl_sec=3600,
        captured_at="2026-05-25T01:00:00+00:00",
        payload={"pattern_key": "breakout:long", "expectancy_r": 0.6, "sample_count": 8},
    )
    expired = evidence_from_signal(
        source="crypto_alpha",
        signal_type="catalyst",
        symbol="BTCUSDT",
        scope="binance",
        confidence=0.4,
        ttl_sec=60,
        captured_at="2026-05-25T00:00:00+00:00",
        payload={"event_type": "listing"},
    )

    scorecard = scorecard_from_evidence(
        policy_id="binance.breakout.long",
        evidence=[fresh, expired],
        now="2026-05-25T01:01:00+00:00",
    )
    packet = build_decision_packet(
        target_scope="binance",
        symbols=["BTCUSDT"],
        evidence=[fresh, expired],
        scorecards=[scorecard],
        active_policies=[{"policy_id": "binance.breakout.long", "action": "prefer"}],
        max_items=5,
        now="2026-05-25T01:01:00+00:00",
    )

    assert scorecard["fresh_count"] == 1
    assert scorecard["expired_count"] == 1
    assert packet["target_scope"] == "binance"
    assert packet["evidence"][0]["evidence_id"] == fresh.evidence_id
    assert packet["scorecards"][0]["policy_id"] == "binance.breakout.long"
    assert packet["active_policies"][0]["action"] == "prefer"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BINANCE", "binance"),
        ("kis", "kis"),
        ("global", "global"),
        ("anything else", "global"),
    ],
)
def test_normalize_scope(raw: str, expected: str) -> None:
    assert normalize_scope(raw) == expected
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_evidence_policy.py -q
```

Expected: FAIL because `evidence_policy.py` does not exist.

- [ ] **Step 3: Implement the shared module**

Create `src/tradecraft/services/evidence_policy.py`:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_iso(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()


def normalize_scope(scope: Any) -> str:
    clean = str(scope or "").lower().strip()
    if clean in {"binance", "crypto"}:
        return "binance"
    if clean in {"kis", "krx", "domestic"}:
        return "kis"
    return "global"


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


@dataclass(slots=True)
class EvidenceItem:
    evidence_id: str
    source: str
    signal_type: str
    symbol: str
    scope: str
    confidence: float
    captured_at: str
    expires_at: str
    payload: dict[str, Any]
    outcome_status: str = "pending"
    used_by_block_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["used_by_block_ids"] = list(self.used_by_block_ids or [])
        return row

    def is_expired(self, *, now: str | None = None) -> bool:
        return _parse_iso(self.expires_at) <= _parse_iso(now or utc_now_iso())


def evidence_from_signal(
    *,
    source: str,
    signal_type: str,
    symbol: Any,
    scope: Any,
    confidence: float,
    ttl_sec: int,
    captured_at: str | None = None,
    payload: dict[str, Any] | None = None,
    outcome_status: str = "pending",
    used_by_block_ids: list[str] | None = None,
) -> EvidenceItem:
    captured = _parse_iso(captured_at or utc_now_iso())
    clean_payload = payload if isinstance(payload, dict) else {}
    clean_symbol = normalize_symbol(symbol)
    clean_scope = normalize_scope(scope)
    clean_source = str(source or "unknown").strip()
    clean_type = str(signal_type or "signal").strip()
    evidence_id = _stable_id(
        "ev",
        clean_source,
        clean_type,
        clean_symbol,
        clean_scope,
        captured.isoformat(),
        clean_payload,
    )
    return EvidenceItem(
        evidence_id=evidence_id,
        source=clean_source,
        signal_type=clean_type,
        symbol=clean_symbol,
        scope=clean_scope,
        confidence=max(min(float(confidence or 0.0), 1.0), 0.0),
        captured_at=captured.isoformat(),
        expires_at=(captured + timedelta(seconds=max(int(ttl_sec), 1))).isoformat(),
        payload=clean_payload,
        outcome_status=str(outcome_status or "pending"),
        used_by_block_ids=list(used_by_block_ids or []),
    )


def scorecard_from_evidence(
    *,
    policy_id: str,
    evidence: list[EvidenceItem],
    now: str | None = None,
) -> dict[str, Any]:
    fresh = [item for item in evidence if not item.is_expired(now=now)]
    expired = [item for item in evidence if item.is_expired(now=now)]
    confidences = [item.confidence for item in fresh]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    r_values = [
        float(item.payload.get("expectancy_r") or item.payload.get("r_multiple") or 0.0)
        for item in fresh
        if isinstance(item.payload, dict)
    ]
    expectancy_r = sum(r_values) / len(r_values) if r_values else 0.0
    return {
        "policy_id": str(policy_id),
        "fresh_count": len(fresh),
        "expired_count": len(expired),
        "sample_count": len(evidence),
        "confidence": avg_confidence,
        "expectancy_r": expectancy_r,
        "status": "candidate" if fresh and avg_confidence >= 0.55 else "observation",
        "evidence_ids": [item.evidence_id for item in fresh[:20]],
        "updated_at": now or utc_now_iso(),
    }


def build_decision_packet(
    *,
    target_scope: str,
    symbols: list[str],
    evidence: list[EvidenceItem],
    scorecards: list[dict[str, Any]],
    active_policies: list[dict[str, Any]],
    max_items: int = 12,
    now: str | None = None,
) -> dict[str, Any]:
    clean_scope = normalize_scope(target_scope)
    clean_symbols = {normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)}
    filtered = [
        item
        for item in evidence
        if item.scope in {clean_scope, "global"}
        and (not clean_symbols or item.symbol in clean_symbols or not item.symbol)
        and not item.is_expired(now=now)
    ]
    filtered.sort(key=lambda item: (item.confidence, item.captured_at), reverse=True)
    return {
        "target_scope": clean_scope,
        "symbols": sorted(clean_symbols),
        "evidence": [item.to_dict() for item in filtered[: max(int(max_items), 1)]],
        "scorecards": scorecards[: max(int(max_items), 1)],
        "active_policies": active_policies[: max(int(max_items), 1)],
        "generated_at": now or utc_now_iso(),
        "policy": {
            "hard_filters": False,
            "use": "Adjust conviction, horizon, sizing hints, target/stop quality, and risk notes.",
        },
    }
```

- [ ] **Step 4: Run evidence tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_evidence_policy.py -q
```

Expected: PASS.

---

### Task 2: Emit Normalized Evidence From Existing Crypto Services

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Modify: `src/tradecraft/services/crypto_alpha.py`
- Modify: `src/tradecraft/services/crypto_quant.py`
- Modify: `src/tradecraft/services/crypto_pattern_lab.py`
- Test: `tests/test_crypto_market_research.py`
- Test: `tests/test_crypto_alpha.py`
- Test: `tests/test_crypto_quant.py`
- Test: `tests/test_crypto_pattern_lab.py`

- [ ] **Step 1: Add tests for market research evidence**

Append to `tests/test_crypto_market_research.py`:

```python
def test_crypto_market_research_context_includes_normalized_evidence(tmp_path: Path) -> None:
    repo = CryptoMarketResearchRepository(tmp_path / "research.db")
    repo.save_candidate(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "stance": "watch_add",
            "horizon": "short",
            "score": 82,
            "confidence": 0.7,
            "reason_md": "momentum plus liquidity",
            "block_template": {},
        }
    )

    context = repo.latest_context(limit=10)

    assert context["evidence"][0]["source"] == "crypto_market_research"
    assert context["evidence"][0]["symbol"] == "BTCUSDT"
    assert context["evidence"][0]["signal_type"] == "research_candidate"
```

- [ ] **Step 2: Add tests for alpha evidence**

Append to `tests/test_crypto_alpha.py`:

```python
def test_crypto_alpha_context_includes_normalized_evidence(tmp_path: Path) -> None:
    service = CryptoAlphaService(CryptoAlphaConfig(db_path=str(tmp_path / "alpha.db")))
    service.repository.save_event(
        {
            "event_id": "ev1",
            "source_id": "binance_announcements",
            "event_type": "listing",
            "title": "Binance will list TEST",
            "summary": "TESTUSDT listing",
            "importance": 0.9,
            "detected_at": "2026-05-25T00:00:00+00:00",
            "symbols": ["TESTUSDT"],
        }
    )

    context = service.context_pack(symbols=["TESTUSDT"], limit=5)

    assert context["evidence"][0]["source"] == "crypto_alpha"
    assert context["evidence"][0]["signal_type"] == "catalyst_event"
    assert context["evidence"][0]["symbol"] == "TESTUSDT"
```

- [ ] **Step 3: Add tests for quant evidence**

Append to `tests/test_crypto_quant.py`:

```python
def test_crypto_quant_repository_exposes_evidence(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(tmp_path / "quant.db")
    repo.save_signal(
        {
            "symbol": "BTCUSDT",
            "horizon": "intraday",
            "long_score": 80,
            "short_score": 20,
            "no_trade_score": 10,
            "expected_r_long": 0.8,
            "signal_json": {"bias": "long"},
            "updated_at": "2026-05-25T00:00:00+00:00",
        }
    )

    evidence = repo.latest_evidence(symbols=["BTCUSDT"], limit=5)

    assert evidence[0]["source"] == "crypto_quant"
    assert evidence[0]["signal_type"] == "directional_quant"
    assert evidence[0]["payload"]["bias"] == "long"
```

- [ ] **Step 4: Add tests for pattern evidence**

Append to `tests/test_crypto_pattern_lab.py`:

```python
def test_crypto_pattern_lab_context_exposes_evidence_and_license_policy(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_strategy_source(
        {
            "source_id": "freqtrade:rsi",
            "path": "external/freqtrade/rsi.py",
            "strategy_name": "RSI",
            "source_hash": "abc",
            "status": "ok",
            "license": "GPL-3.0",
            "license_policy": "reference_only",
        }
    )
    repo.save_patterns(
        [
            {
                "pattern_id": "p1",
                "source_id": "freqtrade:rsi",
                "name": "RSI long",
                "family": "rsi_mean_reversion",
                "direction": "long",
                "timeframe": "15m",
                "indicators": ["rsi"],
                "expression": {},
            }
        ]
    )
    repo.save_backtest(
        {
            "pattern_id": "p1",
            "symbol": "BTCUSDT",
            "interval": "15m",
            "trade_count": 8,
            "win_rate": 62.5,
            "expectancy_r": 0.4,
            "score": 72,
            "evaluated_at": "2026-05-25T00:00:00+00:00",
        }
    )

    context = repo.context_pack(symbols=["BTCUSDT"], limit=5)

    assert context["scorecards"][0]["pattern_key"] == "rsi_mean_reversion:long:15m"
    assert context["evidence"][0]["source"] == "crypto_pattern_lab"
    assert context["sources"][0]["license_policy"] == "reference_only"
```

- [ ] **Step 5: Run failing tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  tests/test_crypto_market_research.py::test_crypto_market_research_context_includes_normalized_evidence \
  tests/test_crypto_alpha.py::test_crypto_alpha_context_includes_normalized_evidence \
  tests/test_crypto_quant.py::test_crypto_quant_repository_exposes_evidence \
  tests/test_crypto_pattern_lab.py::test_crypto_pattern_lab_context_exposes_evidence_and_license_policy \
  -q
```

Expected: FAIL because normalized evidence is not exposed yet.

- [ ] **Step 6: Implement market research evidence**

In `crypto_market_research.py`, import:

```python
from tradecraft.services.evidence_policy import evidence_from_signal
```

In `latest_context`, add an `evidence` array made from candidates and notes:

```python
        evidence = [
            evidence_from_signal(
                source="crypto_market_research",
                signal_type="research_candidate",
                symbol=row.get("symbol"),
                scope="binance",
                confidence=float(row.get("confidence") or 0) if float(row.get("confidence") or 0) <= 1 else float(row.get("confidence") or 0) / 100,
                ttl_sec=3600,
                captured_at=str(row.get("updated_at") or utc_now_iso()),
                payload={
                    "stance": row.get("stance"),
                    "horizon": row.get("horizon"),
                    "score": row.get("score"),
                    "reason_md": row.get("reason_md"),
                    "market": row.get("market"),
                },
            ).to_dict()
            for row in candidates[:limit]
        ]
        payload["evidence"] = evidence
```

If `latest_context` currently returns directly, assign to `payload` first and include the existing keys unchanged.

- [ ] **Step 7: Implement alpha evidence**

In `crypto_alpha.py`, import `evidence_from_signal`.

In `CryptoAlphaService.context_pack`, add:

```python
        evidence = []
        for event in events[:limit]:
            event_symbols = event.get("symbols") if isinstance(event.get("symbols"), list) else []
            for symbol in event_symbols or [""]:
                evidence.append(
                    evidence_from_signal(
                        source="crypto_alpha",
                        signal_type="catalyst_event",
                        symbol=symbol,
                        scope="binance",
                        confidence=min(float(event.get("importance") or 0.0), 1.0),
                        ttl_sec=72 * 3600,
                        captured_at=str(event.get("detected_at") or utc_now_iso()),
                        payload={
                            "event_type": event.get("event_type"),
                            "title": event.get("title"),
                            "source_id": event.get("source_id"),
                            "summary": event.get("summary"),
                        },
                    ).to_dict()
                )
        payload["evidence"] = evidence[:limit]
```

- [ ] **Step 8: Implement quant evidence**

In `crypto_quant.py`, import `evidence_from_signal`.

Add method to `CryptoQuantRepository`:

```python
    def latest_evidence(self, *, symbols: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.latest_signals(symbols=symbols, limit=limit)
        evidence: list[dict[str, Any]] = []
        for row in rows:
            signal = row.get("signal") if isinstance(row.get("signal"), dict) else row.get("signal_json") or {}
            bias = signal.get("bias") or row.get("bias") or (
                "long" if float(row.get("long_score") or 0) > float(row.get("short_score") or 0) else "short"
            )
            max_score = max(
                float(row.get("long_score") or 0),
                float(row.get("short_score") or 0),
                float(row.get("no_trade_score") or 0),
            )
            evidence.append(
                evidence_from_signal(
                    source="crypto_quant",
                    signal_type="directional_quant",
                    symbol=row.get("symbol"),
                    scope="binance",
                    confidence=min(max_score / 100.0, 1.0),
                    ttl_sec=900,
                    captured_at=str(row.get("updated_at") or _utc_now()),
                    payload={
                        "bias": bias,
                        "horizon": row.get("horizon"),
                        "long_score": row.get("long_score"),
                        "short_score": row.get("short_score"),
                        "no_trade_score": row.get("no_trade_score"),
                        "expected_r_long": row.get("expected_r_long"),
                        "expected_r_short": row.get("expected_r_short"),
                    },
                ).to_dict()
            )
        return evidence
```

- [ ] **Step 9: Implement pattern source license policy and evidence**

In `crypto_pattern_lab.py`:

1. Extend `freqtrade_strategy_sources` with columns:

```sql
license TEXT NOT NULL DEFAULT '',
license_policy TEXT NOT NULL DEFAULT ''
```

Add an `_ensure_columns` migration like existing services use, or use `ALTER TABLE` guarded by `PRAGMA table_info`.

2. Update `save_strategy_source` to accept `license` and `license_policy`.

3. Add `evidence` to `context_pack` from latest scorecards:

```python
from tradecraft.services.evidence_policy import evidence_from_signal

...
evidence = [
    evidence_from_signal(
        source="crypto_pattern_lab",
        signal_type="pattern_scorecard",
        symbol=row.get("symbol"),
        scope="binance",
        confidence=min(float(row.get("score") or 0) / 100.0, 1.0),
        ttl_sec=24 * 3600,
        captured_at=str(row.get("evaluated_at") or utc_now_iso()),
        payload={
            "pattern_key": row.get("pattern_key"),
            "family": row.get("family"),
            "direction": row.get("direction"),
            "expectancy_r": row.get("expectancy_r"),
            "sample_count": row.get("trade_count"),
            "win_rate": row.get("win_rate"),
            "license_policy": row.get("license_policy"),
        },
    ).to_dict()
    for row in scorecards[:limit]
]
payload["evidence"] = evidence
```

- [ ] **Step 10: Run evidence emission tests**

Run the command from Step 5 again.

Expected: PASS.

---

### Task 3: Build Unified Decision Packet For Binance And KIS

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write Binance packet test**

Append to `tests/test_binance_block_trader.py`:

```python
def test_binance_manager_prompt_uses_decision_packet(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})

    class FakeQuant:
        def latest_signals(self, *, symbols, limit):
            return []

        def retrieval_context(self, *, symbols, horizon, points_per_symbol):
            return {"items": []}

        def latest_evidence(self, *, symbols, limit):
            return [
                {
                    "evidence_id": "ev_quant",
                    "source": "crypto_quant",
                    "signal_type": "directional_quant",
                    "symbol": "BTCUSDT",
                    "scope": "binance",
                    "confidence": 0.8,
                    "captured_at": "2026-05-25T00:00:00+00:00",
                    "expires_at": "2026-05-25T01:00:00+00:00",
                    "payload": {"bias": "long"},
                }
            ]

    trader = _trader(tmp_path, llm=llm, quant_provider=FakeQuant())

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert "decision_packet" in prompt
    assert prompt["decision_packet"]["target_scope"] == "binance"
    assert prompt["decision_packet"]["evidence"][0]["evidence_id"] == "ev_quant"
    assert "crypto_quant" not in prompt["decision_inputs"]
```

- [ ] **Step 2: Write KIS generalized lesson test**

Append to `tests/test_kis_block_trader.py`:

```python
def test_kis_manager_prompt_uses_generalized_policy_packet(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": [], "update_blocks": [], "close_blocks": []})

    def memory_context_provider(**_: Any) -> dict[str, Any]:
        return {
            "policy_rules": [
                {
                    "policy_id": "global.avoid_noise_exit",
                    "scope": "global",
                    "action": "caution",
                    "effect": {"horizon": "mid", "hint": "avoid closing mid blocks on one noisy candle"},
                },
                {
                    "policy_id": "binance.breakout.long",
                    "scope": "binance",
                    "action": "prefer",
                    "effect": {"hint": "crypto-only"},
                },
            ],
            "policy_scorecards": [],
        }

    trader = _block_trader(tmp_path, llm=llm, memory_context_provider=memory_context_provider)

    asyncio.run(trader.run_manager_once())
    prompt = llm.calls[0]["payload"]

    assert prompt["decision_packet"]["target_scope"] == "kis"
    policy_ids = [row["policy_id"] for row in prompt["decision_packet"]["active_policies"]]
    assert "global.avoid_noise_exit" in policy_ids
    assert "binance.breakout.long" not in policy_ids
```

- [ ] **Step 3: Run failing tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  tests/test_binance_block_trader.py::test_binance_manager_prompt_uses_decision_packet \
  tests/test_kis_block_trader.py::test_kis_manager_prompt_uses_generalized_policy_packet \
  -q
```

Expected: FAIL because managers still pass separate loose blobs.

- [ ] **Step 4: Implement Binance decision packet**

In `binance_block_trader.py`, import:

```python
from tradecraft.services.evidence_policy import EvidenceItem, build_decision_packet
```

Add helper:

```python
def _evidence_items_from_dicts(rows: list[dict[str, Any]]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            items.append(
                EvidenceItem(
                    evidence_id=str(row["evidence_id"]),
                    source=str(row.get("source") or ""),
                    signal_type=str(row.get("signal_type") or ""),
                    symbol=str(row.get("symbol") or ""),
                    scope=str(row.get("scope") or "global"),
                    confidence=float(row.get("confidence") or 0.0),
                    captured_at=str(row.get("captured_at") or ""),
                    expires_at=str(row.get("expires_at") or ""),
                    payload=row.get("payload") if isinstance(row.get("payload"), dict) else {},
                    outcome_status=str(row.get("outcome_status") or "pending"),
                    used_by_block_ids=list(row.get("used_by_block_ids") or []),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return items
```

In `run_manager_once`, after collecting crypto contexts:

```python
        evidence_rows: list[dict[str, Any]] = []
        for source_payload in (crypto_research, crypto_alpha, crypto_quant, crypto_patterns):
            if isinstance(source_payload, dict):
                evidence_rows.extend(
                    row for row in source_payload.get("evidence") or [] if isinstance(row, dict)
                )
        memory_context = self._memory_context(symbols=symbols, blocks=blocks)
        active_policies = [
            row for row in memory_context.get("policy_rules") or []
            if isinstance(row, dict) and str(row.get("scope") or "global") in {"global", "binance", ""}
        ]
        decision_packet = build_decision_packet(
            target_scope="binance",
            symbols=symbols,
            evidence=_evidence_items_from_dicts(evidence_rows),
            scorecards=list(memory_context.get("policy_scorecards") or []),
            active_policies=active_policies,
            max_items=max(int(self.config.quant_context_limit), 8),
        )
```

Use `memory_context` instead of calling `_memory_context(...)` again inside the prompt.

Replace prompt keys:

```python
            "memory": memory_context,
            "decision_packet": decision_packet,
            "raw_context_refs": {
                "crypto_research_status": crypto_research.get("status"),
                "crypto_alpha_status": crypto_alpha.get("status"),
                "crypto_quant_status": crypto_quant.get("status"),
                "crypto_patterns_status": crypto_patterns.get("status"),
            },
```

Change `decision_inputs` to:

```python
            "decision_inputs": [
                "account",
                "decision_packet",
                "performance",
                "candidates",
                "blocks",
            ],
```

Keep raw `crypto_research`, `crypto_alpha`, `crypto_quant`, and `crypto_patterns` out of the top-level prompt or move them under `debug_context` only when a local setting explicitly asks for verbose prompts.

- [ ] **Step 5: Implement KIS generalized packet**

In `kis_block_trader.py`, import `build_decision_packet`.

Add helper:

```python
def _generalized_policies_for_scope(memory_context: dict[str, Any], *, target_scope: str) -> list[dict[str, Any]]:
    allowed = {"global", target_scope, ""}
    rows = memory_context.get("policy_rules") if isinstance(memory_context, dict) else []
    return [
        row
        for row in rows or []
        if isinstance(row, dict) and str(row.get("scope") or "global") in allowed
    ]
```

In the KIS manager prompt builder, after `memory_context` and `policy_rule_evaluation`:

```python
        decision_packet = build_decision_packet(
            target_scope="kis",
            symbols=[str(row.get("symbol") or "") for row in positions + candidates if isinstance(row, dict)],
            evidence=[],
            scorecards=list(memory_context.get("policy_scorecards") or []),
            active_policies=_generalized_policies_for_scope(memory_context, target_scope="kis"),
            max_items=12,
        )
```

Add to prompt:

```python
            "decision_packet": decision_packet,
```

Keep `investment_memory` for backward compatibility during v1, but add instruction:

```python
            "decision_packet_policy": (
                "Use decision_packet as the primary normalized policy context. "
                "Crypto-specific policies must not be treated as KIS asset evidence."
            ),
```

- [ ] **Step 6: Run manager packet tests**

Run the command from Step 3 again.

Expected: PASS.

---

### Task 4: Promote Evidence Scorecards Into Existing Memory Policies

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_investment_memory.py`:

```python
def test_memory_ingests_evidence_scorecard_as_versioned_policy(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.ingest_evidence_scorecards(
        [
            {
                "policy_id": "binance.breakout.long",
                "scope": "binance",
                "status": "candidate",
                "fresh_count": 4,
                "sample_count": 8,
                "confidence": 0.72,
                "expectancy_r": 0.35,
                "evidence_ids": ["ev1", "ev2"],
            }
        ]
    )

    assert result["ingested"] == 1
    rules = service.policy_rules(active_only=False)["items"]
    assert rules[0]["policy_id"] == "binance.breakout.long"
    assert rules[0]["scope"] == "binance"
    assert rules[0]["action"] in {"observe", "prefer", "caution"}
    assert rules[0]["source_scorecard"]["evidence_ids"] == ["ev1", "ev2"]
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_investment_memory.py::test_memory_ingests_evidence_scorecard_as_versioned_policy -q
```

Expected: FAIL because `ingest_evidence_scorecards` does not exist.

- [ ] **Step 3: Implement evidence scorecard ingestion**

In `InvestmentMemoryService`, add:

```python
    def ingest_evidence_scorecards(self, scorecards: list[dict[str, Any]]) -> dict[str, Any]:
        ingested = 0
        skipped = 0
        for row in scorecards:
            if not isinstance(row, dict):
                skipped += 1
                continue
            policy_id = _clean_text(row.get("policy_id"), limit=160)
            if not policy_id:
                skipped += 1
                continue
            confidence = _safe_float(row.get("confidence"))
            expectancy_r = _safe_float(row.get("expectancy_r"))
            sample_count = _safe_int(row.get("sample_count"))
            action = "observe"
            status = "candidate"
            if sample_count >= 5 and confidence >= 0.65 and expectancy_r > 0:
                action = "prefer"
                status = "active"
            elif sample_count >= 5 and confidence >= 0.65 and expectancy_r < 0:
                action = "caution"
                status = "active"
            self.repository.upsert_policy_scorecard(
                {
                    "policy_id": policy_id,
                    "scope": _clean_text(row.get("scope") or "global", limit=40),
                    "status": status,
                    "action": action,
                    "sample_count": sample_count,
                    "win_rate": _safe_float(row.get("win_rate")),
                    "avg_pnl_pct": 0.0,
                    "expectancy_pct": expectancy_r,
                    "rule_follow_rate": confidence,
                    "confidence": confidence,
                    "reason": _clean_text(
                        row.get("summary")
                        or f"evidence scorecard expR={expectancy_r:.2f} confidence={confidence:.2f}",
                        limit=1200,
                    ),
                    "source_scorecard": row,
                }
            )
            ingested += 1
        sync = self.sync_policy_rules()
        return {"status": "ok", "ingested": ingested, "skipped": skipped, "sync": sync}
```

If `upsert_policy_scorecard` expects different key names, adapt only at this ingestion boundary and preserve existing DB schema.

- [ ] **Step 4: Run memory ingestion test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_investment_memory.py::test_memory_ingests_evidence_scorecard_as_versioned_policy -q
```

Expected: PASS.

---

### Task 5: Add Evidence/Policy Inspection APIs Without New Lab DB

**Files:**
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_api_smoke.py`

- [ ] **Step 1: Write failing API smoke test**

Append to `tests/test_api_smoke.py`:

```python
def test_evidence_policy_status_api(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get("/api/evidence-policy/status", headers=admin_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "sources" in payload
    assert "policy" in payload
```

If this test file uses a different authenticated-client fixture name, follow the existing pattern in that file.

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_api_smoke.py::test_evidence_policy_status_api -q
```

Expected: FAIL because the endpoint does not exist.

- [ ] **Step 3: Add API endpoint**

In `main.py`, add a protected endpoint:

```python
@app.get("/api/evidence-policy/status")
def evidence_policy_status(_: None = Depends(require_admin)) -> dict[str, Any]:
    crypto_research = build_crypto_research_service().repository.status()
    crypto_alpha = build_crypto_alpha_service().status()
    crypto_quant = build_crypto_quant_repository().status()
    crypto_patterns = build_crypto_pattern_lab_service(settings).status()
    memory = build_investment_memory_service().status()
    return {
        "status": "ok",
        "sources": {
            "crypto_market_research": crypto_research,
            "crypto_alpha": crypto_alpha,
            "crypto_quant": crypto_quant,
            "crypto_pattern_lab": crypto_patterns,
        },
        "policy": {
            "memory_status": memory,
            "loop": "evidence -> scorecard -> policy_rule -> decision_packet -> block_outcome",
        },
    }
```

Use existing factory names in `main.py`; if a factory is absent, instantiate the existing repository/service the same way nearby endpoints do.

- [ ] **Step 4: Add context endpoint**

Add:

```python
@app.get("/api/evidence-policy/context")
def evidence_policy_context(limit: int = 12, _: None = Depends(require_admin)) -> dict[str, Any]:
    memory = build_investment_memory_service()
    memory.sync_policy_rules()
    return {
        "status": "ok",
        "policy_rules": memory.policy_rules(limit=limit, active_only=True)["items"],
        "policy_scorecards": memory.policy_scorecards(limit=limit)["items"],
    }
```

- [ ] **Step 5: Run API smoke test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_api_smoke.py::test_evidence_policy_status_api -q
```

Expected: PASS.

---

### Task 6: Reorganize UI Around Evidence And Policy Flow

**Files:**
- Modify: `src/tradecraft/web/static/index.html`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add static UI tests**

Append to `tests/test_static_ui.py`:

```python
def test_no_separate_jue_lab_tab_and_evidence_policy_ui_exists() -> None:
    html = _html()
    js = _js()

    assert 'data-nav-helper-tab="jue_lab"' not in html
    assert "renderJueLabTab" not in js
    assert "function renderEvidencePolicyFlow" in js
    assert "/evidence-policy/status" in js
    assert "/evidence-policy/context" in js
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py::test_no_separate_jue_lab_tab_and_evidence_policy_ui_exists -q
```

Expected: FAIL because the flow UI does not exist.

- [ ] **Step 3: Add state and loader**

In `app.js`, add to `state`:

```javascript
  evidencePolicy: {
    status: null,
    context: null,
    loading: false,
    error: "",
  },
```

Add loader:

```javascript
async function loadEvidencePolicy() {
  state.evidencePolicy.loading = true;
  state.evidencePolicy.error = "";
  renderHelperAgent();
  try {
    const [status, context] = await Promise.all([
      getJSON("/evidence-policy/status"),
      getJSON("/evidence-policy/context?limit=12"),
    ]);
    state.evidencePolicy.status = status;
    state.evidencePolicy.context = context;
  } catch (error) {
    state.evidencePolicy.error = getErrorMessage(error);
  } finally {
    state.evidencePolicy.loading = false;
    renderHelperAgent();
  }
}
```

In `ensureHelperTabData()`, load this for `crypto_research` and `memory`:

```javascript
  if ((tab === "crypto_research" || tab === "memory") && !state.evidencePolicy.status && !state.evidencePolicy.loading) {
    loadEvidencePolicy();
  }
```

- [ ] **Step 4: Add flow renderer**

Add:

```javascript
function renderEvidencePolicyFlow() {
  const status = state.evidencePolicy.status || {};
  const context = state.evidencePolicy.context || {};
  const sources = status.sources || {};
  const scorecards = Array.isArray(context.policy_scorecards) ? context.policy_scorecards : [];
  const rules = Array.isArray(context.policy_rules) ? context.policy_rules : [];
  if (state.evidencePolicy.error) {
    return `<section class="memory-section"><div class="notice">Evidence/Policy 조회 실패: ${escapeHTML(state.evidencePolicy.error)}</div></section>`;
  }
  return `
    <section class="memory-section evidence-policy-flow">
      <div class="panel-head compact">
        <div>
          <span class="section-kicker">Evidence → Policy</span>
          <h3>검증 루프</h3>
        </div>
        <button class="btn ghost" type="button" data-evidence-policy-action="refresh">갱신</button>
      </div>
      <div class="evidence-flow-grid">
        <article><span>Evidence</span><strong>${escapeHTML(fmtNum(Object.keys(sources).length, 0))}</strong><small>리서치·알파·퀀트·패턴</small></article>
        <article><span>Scorecard</span><strong>${escapeHTML(fmtNum(scorecards.length, 0))}</strong><small>검증된 성과 요약</small></article>
        <article><span>Policy</span><strong>${escapeHTML(fmtNum(rules.length, 0))}</strong><small>쥬 판단에 들어가는 규칙</small></article>
        <article><span>Decision</span><strong>Packet</strong><small>KIS/Binance 매니저 입력</small></article>
      </div>
      <div class="strategy-chip-row">
        ${rules.slice(0, 8).map((rule) => `<span class="strategy-data-chip">${escapeHTML(rule.policy_id || "-")}</span>`).join("") || '<span class="strategy-data-chip">active policy 대기</span>'}
      </div>
    </section>
  `;
}
```

Insert `${renderEvidencePolicyFlow()}` near the top of `renderCryptoResearchLabTab()` and `renderInvestmentMemoryTab()`.

- [ ] **Step 5: Wire refresh action**

In `helperContent` click listener:

```javascript
    const evidenceAction = target ? target.closest("[data-evidence-policy-action]") : null;
    if (evidenceAction) {
      loadEvidencePolicy();
      return;
    }
```

- [ ] **Step 6: Add CSS**

Add:

```css
.evidence-policy-flow {
  border-color: var(--source-line);
}

.evidence-flow-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  gap: 10px;
}

.evidence-flow-grid article {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-soft);
  padding: 10px;
  min-width: 0;
}

.evidence-flow-grid span,
.evidence-flow-grid small {
  display: block;
  color: var(--muted);
}

.evidence-flow-grid strong {
  display: block;
  margin: 4px 0;
  color: var(--ink);
}

@media (max-width: 760px) {
  .evidence-flow-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 7: Run UI tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py::test_no_separate_jue_lab_tab_and_evidence_policy_ui_exists -q
node --check src/tradecraft/web/static/app.js
```

Expected: PASS.

---

### Task 7: Retire The Old Separate Jue Lab Plan And Guard Against Reintroduction

**Files:**
- Delete: `docs/superpowers/plans/2026-05-25-jue-trading-lab.md`
- Modify: `tests/test_static_ui.py`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Add guard test**

Append to `tests/test_static_ui.py`:

```python
def test_no_standalone_trading_lab_artifacts_are_introduced() -> None:
    root = ROOT
    assert not (root / "src/tradecraft/services/trading_lab.py").exists()
    assert not (root / "src/tradecraft/runtime/trading_lab_runner.py").exists()
```

- [ ] **Step 2: Delete obsolete plan**

Remove:

```text
docs/superpowers/plans/2026-05-25-jue-trading-lab.md
```

- [ ] **Step 3: Run guard test**

Run:

```bash
.venv/bin/python3 -m pytest tests/test_static_ui.py::test_no_standalone_trading_lab_artifacts_are_introduced -q
```

Expected: PASS.

---

### Task 8: Final Verification And Runtime Restart

**Files:**
- All touched files.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  tests/test_evidence_policy.py \
  tests/test_crypto_market_research.py \
  tests/test_crypto_alpha.py \
  tests/test_crypto_quant.py \
  tests/test_crypto_pattern_lab.py \
  tests/test_investment_memory.py \
  tests/test_binance_block_trader.py \
  tests/test_kis_block_trader.py \
  tests/test_api_smoke.py \
  tests/test_static_ui.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run static checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
python -m py_compile \
  src/tradecraft/services/evidence_policy.py \
  src/tradecraft/services/crypto_market_research.py \
  src/tradecraft/services/crypto_alpha.py \
  src/tradecraft/services/crypto_quant.py \
  src/tradecraft/services/crypto_pattern_lab.py \
  src/tradecraft/services/investment_memory.py \
  src/tradecraft/services/binance_block_trader.py \
  src/tradecraft/services/kis_block_trader.py \
  src/tradecraft/main.py
git diff --check -- \
  src/tradecraft/services/evidence_policy.py \
  src/tradecraft/services/crypto_market_research.py \
  src/tradecraft/services/crypto_alpha.py \
  src/tradecraft/services/crypto_quant.py \
  src/tradecraft/services/crypto_pattern_lab.py \
  src/tradecraft/services/investment_memory.py \
  src/tradecraft/services/binance_block_trader.py \
  src/tradecraft/services/kis_block_trader.py \
  src/tradecraft/main.py \
  src/tradecraft/web/static/index.html \
  src/tradecraft/web/static/app.js \
  src/tradecraft/web/static/style.css \
  tests/test_evidence_policy.py \
  tests/test_static_ui.py
```

Expected: no output from static checks.

- [ ] **Step 3: Restart processes that consume prompts or memory**

Run:

```bash
tmux kill-session -t hermes-control 2>/dev/null || true
tmux kill-session -t hermes-binance-block-trader 2>/dev/null || true
tmux kill-session -t tradecraft-kis-block-trader 2>/dev/null || true
tmux kill-session -t hermes-investment-memory 2>/dev/null || true
sleep 1
tmux new-session -d -s hermes-control 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-control --host 127.0.0.1 --port 18080 2>&1 | tee -a .runtime/control.log'
tmux new-session -d -s hermes-binance-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-binance-block-trader 2>&1 | tee -a .runtime/binance_block_trader.log'
tmux new-session -d -s tradecraft-kis-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.kis_block_trader_runner 2>&1 | tee -a .runtime/kis_block_trader.log'
tmux new-session -d -s hermes-investment-memory 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.investment_memory_runner 2>&1 | tee -a .runtime/investment_memory.log'
```

- [ ] **Step 4: Verify API shape**

Run:

```bash
python - <<'PY'
import json, urllib.request
import tradecraft.main as main

token = str(main.settings.admin_token or '').strip()
if not token and main.settings.admin_token_list:
    token = main.settings.admin_token_list[0]
headers = {'Authorization': f'Bearer {token}'} if token else {}
for path in ['/api/ops/readiness', '/api/evidence-policy/status', '/api/evidence-policy/context']:
    req = urllib.request.Request(f'http://127.0.0.1:18080{path}', headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode())
    print(path, payload.get('status'), list(payload.keys()))
PY
```

Expected: readiness responds, evidence-policy endpoints return `status: ok`.

---

## Self-Review

- Spec coverage: This plan replaces the standalone lab idea with a structural refactor of existing services into evidence, scorecard, policy, and decision-packet flow.
- Placeholder scan: No `TBD`, `TODO`, or vague “handle later” steps remain.
- Type consistency: Shared terms are consistent: `EvidenceItem`, `evidence`, `scorecard`, `policy_rules`, `decision_packet`, `target_scope`.
- Scope check: The plan is large but cohesive: it does not introduce new independent services, databases, or tabs; it changes the backbone of current Jue decision-making.
