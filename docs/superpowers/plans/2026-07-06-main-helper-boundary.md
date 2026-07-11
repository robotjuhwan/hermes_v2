# Main Helper Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract low-risk helper and fallback-service logic from `src/tradecraft/main.py` into focused, testable modules without changing trading behavior or API compatibility.

**Architecture:** Keep service construction, route registration, auth, readiness, and trading gates in `main.py`. Move pure helpers and optional-service fallback adapters into small modules that accept explicit inputs and can be tested without app globals, live exchange adapters, or runtime databases.

**Tech Stack:** Python 3.10+, FastAPI, pytest, existing static-router and route-group architecture.

---

## Repository Constraint

Do not commit during this plan. `AGENTS.md` says not to commit unless the user explicitly requests it. Where the generic Superpowers workflow would commit, run `git diff --check` and focused verification instead.

## File Structure

- Create `src/tradecraft/api/jue_workflow_payloads.py`
  - Owns preferred Jue workflow order and workflow id discovery.
- Create `tests/test_jue_workflow_payloads.py`
  - Covers preferred ordering and discovery of additional workflow JSON files.
- Create `src/tradecraft/api/crypto_payloads.py`
  - Owns crypto research symbol parsing and kline interval parsing.
- Create `tests/test_crypto_payloads.py`
  - Covers normalization, filtering, deduplication, and fallback intervals.
- Create `src/tradecraft/services/unavailable_services.py`
  - Owns unavailable crypto research and alpha fallback service adapters.
- Create `tests/test_unavailable_services.py`
  - Covers status/context/skipped operation payload compatibility.
- Create `src/tradecraft/services/strategy_collect_sources.py`
  - Owns strategy collect source aliasing, allowed-host replacement, and cache-path guarding.
- Create `tests/test_strategy_collect_sources.py`
  - Covers allowed hosts, unsafe host replacement, safe cache paths, aliases, and unknown-source filtering.
- Modify `src/tradecraft/main.py`
  - Import and use the new helpers.
  - Remove the moved helper constants, functions, and fallback classes.

## Task 1: Extract Jue Workflow Discovery

**Files:**
- Create: `src/tradecraft/api/jue_workflow_payloads.py`
- Create: `tests/test_jue_workflow_payloads.py`
- Modify: `src/tradecraft/main.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jue_workflow_payloads.py`:

```python
from __future__ import annotations

from tradecraft.api.jue_workflow_payloads import (
    PREFERRED_JUE_WORKFLOW_IDS,
    available_jue_workflow_ids,
)


def test_available_jue_workflow_ids_returns_preferred_then_extra_sorted(tmp_path) -> None:
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    for workflow_id in ("z_extra", "kis_post_close", "a_extra", "kis_pre_open"):
        (workflow_dir / f"{workflow_id}.json").write_text("{}", encoding="utf-8")

    assert available_jue_workflow_ids(workflow_dir) == [
        "kis_pre_open",
        "kis_post_close",
        "a_extra",
        "z_extra",
    ]


def test_preferred_jue_workflow_ids_preserve_active_trading_order() -> None:
    assert PREFERRED_JUE_WORKFLOW_IDS[:3] == (
        "kis_pre_open",
        "kis_intraday_manager",
        "kis_post_close",
    )
    assert "binance_cycle" in PREFERRED_JUE_WORKFLOW_IDS
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_jue_workflow_payloads.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'tradecraft.api.jue_workflow_payloads'`.

- [ ] **Step 3: Add the module**

Create `src/tradecraft/api/jue_workflow_payloads.py`:

```python
from __future__ import annotations

from pathlib import Path


PREFERRED_JUE_WORKFLOW_IDS: tuple[str, ...] = (
    "kis_pre_open",
    "kis_intraday_manager",
    "kis_post_close",
    "block_reflection",
    "policy_revision",
    "crypto_research",
    "binance_cycle",
)


def available_jue_workflow_ids(workflow_dir: str | Path) -> list[str]:
    directory = Path(workflow_dir)
    discovered = sorted(
        path.stem for path in directory.glob("*.json") if path.is_file()
    )
    preferred = [
        workflow_id
        for workflow_id in PREFERRED_JUE_WORKFLOW_IDS
        if workflow_id in discovered
    ]
    preferred_set = set(preferred)
    return preferred + [
        workflow_id for workflow_id in discovered if workflow_id not in preferred_set
    ]
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_jue_workflow_payloads.py -q
```

Expected: pass.

- [ ] **Step 5: Wire `main.py` to the new helper**

Modify `src/tradecraft/main.py`:

```python
from tradecraft.api.jue_workflow_payloads import available_jue_workflow_ids
```

Remove the local `JUE_WORKFLOW_IDS` tuple and `_available_jue_workflow_ids` function. In the `AssistantSupportRouteGroupDeps` wiring, replace:

```python
jue_available_workflow_ids=lambda registry: _available_jue_workflow_ids(
    registry
),
```

with:

```python
jue_available_workflow_ids=lambda registry: available_jue_workflow_ids(
    registry.root / "workflows"
),
```

- [ ] **Step 6: Run integration checks for this task**

Run:

```bash
pytest tests/test_jue_workflow_payloads.py tests/test_app_routes.py tests/test_app_route_specs.py -q
```

Expected: pass.

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

## Task 2: Extract Crypto Payload Helpers and Unavailable Services

**Files:**
- Create: `src/tradecraft/api/crypto_payloads.py`
- Create: `tests/test_crypto_payloads.py`
- Create: `src/tradecraft/services/unavailable_services.py`
- Create: `tests/test_unavailable_services.py`
- Modify: `src/tradecraft/main.py`

- [ ] **Step 1: Write failing crypto payload tests**

Create `tests/test_crypto_payloads.py`:

```python
from __future__ import annotations

from tradecraft.api.crypto_payloads import (
    DEFAULT_CRYPTO_KLINE_INTERVALS,
    crypto_research_symbols,
    default_crypto_research_symbols,
    parse_crypto_kline_intervals,
)


def test_crypto_research_symbols_normalizes_filters_and_deduplicates() -> None:
    assert crypto_research_symbols("btc, eth;bad symbol BTC BTCUSDT") == [
        "BTC",
        "ETH",
        "BTCUSDT",
    ]
    assert crypto_research_symbols(["sol", "SOL", "x:y", "bad symbol"]) == [
        "SOL",
        "X:Y",
    ]


def test_default_crypto_research_symbols_uses_configured_universe() -> None:
    assert default_crypto_research_symbols("btc,eth") == ["BTC", "ETH"]


def test_parse_crypto_kline_intervals_keeps_positive_integer_limits() -> None:
    assert parse_crypto_kline_intervals("1m:120,5m:96,bad,1h:nope,4h:0") == {
        "1m": 120,
        "5m": 96,
    }


def test_parse_crypto_kline_intervals_returns_default_when_empty() -> None:
    assert parse_crypto_kline_intervals("bad") == DEFAULT_CRYPTO_KLINE_INTERVALS
```

- [ ] **Step 2: Write failing unavailable-service tests**

Create `tests/test_unavailable_services.py`:

```python
from __future__ import annotations

import asyncio

from tradecraft.services.unavailable_services import (
    UnavailableCryptoAlphaService,
    UnavailableCryptoMarketResearchService,
)


def test_unavailable_crypto_market_research_payloads_preserve_status_shape() -> None:
    service = UnavailableCryptoMarketResearchService(
        reason="missing dependency",
        db_path=".runtime/crypto_market_research.db",
    )

    status = service.status()
    context = service.latest_context(symbols=["BTCUSDT"], limit=3)
    collect = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    research = asyncio.run(service.run_research_once(["BTCUSDT"]))

    assert status["available"] is False
    assert status["db_path"] == ".runtime/crypto_market_research.db"
    assert context["symbols"] == ["BTCUSDT"]
    assert context["reason"] == "missing dependency"
    assert collect == {
        "status": "skipped",
        "available": False,
        "symbols": ["BTCUSDT"],
        "reason": "missing dependency",
    }
    assert research["status"] == "skipped"
    assert research["symbols"] == ["BTCUSDT"]


def test_unavailable_crypto_alpha_payloads_preserve_status_shape() -> None:
    service = UnavailableCryptoAlphaService(
        reason="missing dependency",
        db_path=".runtime/crypto_alpha.db",
    )

    status = service.status()
    context = service.context_pack(symbols=["ETHUSDT"], limit=2)
    collect = asyncio.run(service.collect_once())
    labels = asyncio.run(service.label_due_outcomes())

    assert status["available"] is False
    assert status["db_path"] == ".runtime/crypto_alpha.db"
    assert context["scope"] == "binance_crypto_alpha"
    assert context["symbols"] == ["ETHUSDT"]
    assert context["data_gaps"] == ["crypto_alpha_unavailable"]
    assert collect == {
        "status": "skipped",
        "available": False,
        "reason": "missing dependency",
    }
    assert labels["labeled"] == 0
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
pytest tests/test_crypto_payloads.py tests/test_unavailable_services.py -q
```

Expected: fail with module import errors for the new modules.

- [ ] **Step 4: Add `crypto_payloads.py`**

Create `src/tradecraft/api/crypto_payloads.py`:

```python
from __future__ import annotations

import re
from typing import Any


DEFAULT_CRYPTO_KLINE_INTERVALS: dict[str, int] = {
    "1m": 120,
    "5m": 96,
    "15m": 96,
    "1h": 168,
    "4h": 180,
}


def crypto_research_symbols(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = re.split(r"[\s,;]+", raw)
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        values = [str(raw)]
    return [
        symbol
        for symbol in dict.fromkeys(item.strip().upper() for item in values)
        if symbol and re.fullmatch(r"[A-Z0-9:_-]{2,30}", symbol)
    ]


def default_crypto_research_symbols(universe: Any) -> list[str]:
    return crypto_research_symbols(universe)


def parse_crypto_kline_intervals(value: Any) -> dict[str, int]:
    intervals: dict[str, int] = {}
    for part in re.split(r"[,;]+", str(value or "")):
        if ":" not in part:
            continue
        key, raw_limit = part.split(":", 1)
        interval = key.strip()
        try:
            limit = int(str(raw_limit).strip())
        except ValueError:
            continue
        if interval and limit > 0:
            intervals[interval] = limit
    return intervals or dict(DEFAULT_CRYPTO_KLINE_INTERVALS)
```

- [ ] **Step 5: Add `unavailable_services.py`**

Create `src/tradecraft/services/unavailable_services.py`:

```python
from __future__ import annotations

from typing import Any


class UnavailableCryptoMarketResearchService:
    def __init__(self, *, reason: str, db_path: str) -> None:
        self.reason = reason
        self.db_path = db_path

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "db_path": self.db_path,
            "snapshot_count": 0,
            "candidate_count": 0,
            "reason": self.reason,
        }

    def latest_context(
        self,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "symbols": symbols or [],
            "limit": limit,
            "items": [],
            "market_regime": {"status": "missing", "regime": "unknown"},
            "observed_symbol_count": len(symbols or []),
            "focus_symbol_count": 0,
            "candidates": [],
            "symbol_notes": {},
            "features": {},
            "reason": self.reason,
        }

    async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "symbols": symbols,
            "reason": self.reason,
        }

    async def run_research_once(
        self,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "symbols": symbols or [],
            "reason": self.reason,
        }


class UnavailableCryptoAlphaService:
    def __init__(self, *, reason: str, db_path: str) -> None:
        self.reason = reason
        self.db_path = db_path

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "db_path": self.db_path,
            "sources": 0,
            "snapshots": 0,
            "events": 0,
            "outcomes": 0,
            "hypotheses": 0,
            "reason": self.reason,
        }

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "scope": "binance_crypto_alpha",
            "symbols": symbols or [],
            "limit": limit,
            "events": [],
            "similar_outcomes": [],
            "scorecards": [],
            "active_lessons": [],
            "contradictions": [],
            "data_gaps": ["crypto_alpha_unavailable"],
            "reason": self.reason,
        }

    async def collect_once(self) -> dict[str, Any]:
        return {"status": "skipped", "available": False, "reason": self.reason}

    async def label_due_outcomes(self) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "reason": self.reason,
            "labeled": 0,
        }
```

- [ ] **Step 6: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_crypto_payloads.py tests/test_unavailable_services.py -q
```

Expected: pass.

- [ ] **Step 7: Wire `main.py` to the new modules**

Modify `src/tradecraft/main.py` imports:

```python
from tradecraft.api.crypto_payloads import (
    crypto_research_symbols,
    default_crypto_research_symbols,
    parse_crypto_kline_intervals,
)
from tradecraft.services.unavailable_services import (
    UnavailableCryptoAlphaService,
    UnavailableCryptoMarketResearchService,
)
```

Remove local classes `_UnavailableCryptoMarketResearchService` and `_UnavailableCryptoAlphaService`.

Remove local functions `_crypto_research_symbols`, `_default_crypto_research_symbols`, and `_parse_crypto_kline_intervals`.

In `_build_crypto_market_research_service`, replace the unavailable branch with:

```python
return UnavailableCryptoMarketResearchService(
    reason=reason,
    db_path=settings.crypto_market_research_db_path,
)
```

In `_build_crypto_alpha_service`, replace the unavailable branch with:

```python
return UnavailableCryptoAlphaService(
    reason=reason,
    db_path=settings.crypto_alpha_db_path,
)
```

In the crypto market config, replace `_parse_crypto_kline_intervals(...)` with `parse_crypto_kline_intervals(...)`.

In `ResearchRouteGroupDeps`, replace:

```python
crypto_research_symbols=_crypto_research_symbols,
default_crypto_research_symbols=_default_crypto_research_symbols,
```

with:

```python
crypto_research_symbols=crypto_research_symbols,
default_crypto_research_symbols=lambda: default_crypto_research_symbols(
    settings.crypto_market_research_universe
),
```

- [ ] **Step 8: Run integration checks for this task**

Run:

```bash
pytest tests/test_crypto_payloads.py tests/test_unavailable_services.py tests/test_api_smoke.py -q
```

Expected: pass.

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

## Task 3: Extract Strategy Collect Source Normalization

**Files:**
- Create: `src/tradecraft/services/strategy_collect_sources.py`
- Create: `tests/test_strategy_collect_sources.py`
- Modify: `src/tradecraft/main.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_collect_sources.py`:

```python
from __future__ import annotations

from tradecraft.services.strategy_collect_sources import (
    safe_runtime_cache_path,
    safe_strategy_collect_sources,
)


def test_safe_runtime_cache_path_accepts_only_runtime_cache_children(tmp_path) -> None:
    root = tmp_path
    default = ".runtime/cache/default.json"

    assert (
        safe_runtime_cache_path(".runtime/cache/custom.json", default, root=root)
        == ".runtime/cache/custom.json"
    )
    assert safe_runtime_cache_path("../outside.json", default, root=root) == default
    assert safe_runtime_cache_path("/tmp/outside.json", default, root=root) == default


def test_safe_strategy_collect_sources_normalizes_aliases_and_hosts(tmp_path) -> None:
    sources = [
        {
            "source_id": "whale",
            "url": "https://evil.example/major_stock",
            "cache_path": "../unsafe.json",
            "symbol_cache_path": ".runtime/cache/symbols.json",
            "symbol_search_url": "https://api.lefthanders-new.xyz/api/v1/assets",
        },
        {
            "source_id": "sesiban",
            "url": "https://www.sesiban.site/rankings",
            "cache_path": ".runtime/cache/sesiban.json",
        },
        {"source_id": "unknown", "url": "https://example.com"},
    ]

    normalized = safe_strategy_collect_sources(sources, root=tmp_path)

    assert [row["source_id"] for row in normalized] == [
        "whale_insight",
        "after_close_330",
    ]
    assert normalized[0]["url"] == "https://whale-insight.com/major_stock"
    assert normalized[0]["cache_path"] == ".runtime/cache/whale_insight_public_signals.json"
    assert normalized[0]["symbol_cache_path"] == ".runtime/cache/symbols.json"
    assert normalized[1]["url"] == "https://www.sesiban.site/rankings"
    assert normalized[1]["cache_path"] == ".runtime/cache/sesiban.json"


def test_safe_strategy_collect_sources_filters_requested_source_ids(tmp_path) -> None:
    sources = [
        {"source_id": "whale_insight"},
        {"source_id": "after_close_330"},
    ]

    normalized = safe_strategy_collect_sources(
        sources,
        source_ids=["sesiban"],
        root=tmp_path,
    )

    assert [row["source_id"] for row in normalized] == ["after_close_330"]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_strategy_collect_sources.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'tradecraft.services.strategy_collect_sources'`.

- [ ] **Step 3: Add the module**

Create `src/tradecraft/services/strategy_collect_sources.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse


STRATEGY_COLLECT_ALLOWED_HOSTS: dict[str, set[str]] = {
    "whale_insight": {"whale-insight.com", "www.whale-insight.com"},
    "after_close_330": {
        "api.lefthanders-new.xyz",
        "www.sesiban.site",
        "sesiban.site",
    },
}

STRATEGY_COLLECT_DEFAULTS: dict[str, dict[str, str]] = {
    "whale_insight": {
        "url": "https://whale-insight.com/major_stock",
        "cache_path": ".runtime/cache/whale_insight_public_signals.json",
        "symbol_cache_path": ".runtime/cache/strategy_insight_symbol_cache.json",
        "symbol_search_url": "https://api.lefthanders-new.xyz/api/v1/assets",
    },
    "after_close_330": {
        "url": "https://api.lefthanders-new.xyz/api/v1/rankings/leading?market=KR",
        "cache_path": ".runtime/cache/sesiban_public_signals.json",
    },
}

STRATEGY_COLLECT_SOURCE_ALIASES: dict[str, str] = {
    "whale": "whale_insight",
    "whale_insight": "whale_insight",
    "sesiban": "after_close_330",
    "after_close": "after_close_330",
    "after_close_330": "after_close_330",
}


def is_allowed_collect_url(source_id: str, value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    host = (urlparse(raw).hostname or "").lower()
    return host in STRATEGY_COLLECT_ALLOWED_HOSTS.get(source_id, set())


def safe_runtime_cache_path(
    value: Any,
    default_value: str,
    *,
    root: str | Path | None = None,
) -> str:
    raw = str(value or default_value).strip() or default_value
    project_root = Path.cwd() if root is None else Path(root)
    candidate = Path(raw).expanduser()
    base = (project_root / ".runtime" / "cache").resolve()
    resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        return default_value
    return raw


def safe_strategy_collect_sources(
    configured_sources: list[dict[str, Any]],
    source_ids: list[str] | None = None,
    *,
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    wanted = {
        STRATEGY_COLLECT_SOURCE_ALIASES.get(str(item or "").strip().lower(), "")
        for item in (source_ids or [])
        if str(item or "").strip()
    }
    wanted.discard("")
    safe_sources: list[dict[str, Any]] = []
    for source in configured_sources:
        source_id = STRATEGY_COLLECT_SOURCE_ALIASES.get(
            str(source.get("source_id") or "").strip().lower(),
            "",
        )
        if source_id not in STRATEGY_COLLECT_DEFAULTS:
            continue
        if wanted and source_id not in wanted:
            continue
        defaults = STRATEGY_COLLECT_DEFAULTS[source_id]
        row = dict(source)
        row["source_id"] = source_id
        row["url"] = (
            str(source.get("url")).strip()
            if is_allowed_collect_url(source_id, source.get("url"))
            else defaults["url"]
        )
        row["cache_path"] = safe_runtime_cache_path(
            source.get("cache_path"),
            defaults["cache_path"],
            root=root,
        )
        if source_id == "whale_insight":
            row["symbol_cache_path"] = safe_runtime_cache_path(
                source.get("symbol_cache_path"),
                defaults["symbol_cache_path"],
                root=root,
            )
            row["symbol_search_url"] = (
                str(source.get("symbol_search_url")).strip()
                if is_allowed_collect_url("after_close_330", source.get("symbol_search_url"))
                else defaults["symbol_search_url"]
            )
        safe_sources.append(row)
    return safe_sources
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_strategy_collect_sources.py -q
```

Expected: pass.

- [ ] **Step 5: Wire `main.py` to the new module**

Modify `src/tradecraft/main.py` imports:

```python
from tradecraft.services.strategy_collect_sources import (
    STRATEGY_COLLECT_SOURCE_ALIASES,
    safe_strategy_collect_sources as build_safe_strategy_collect_sources,
)
```

Remove local constants `_STRATEGY_COLLECT_ALLOWED_HOSTS`, `_STRATEGY_COLLECT_DEFAULTS`, and `_STRATEGY_COLLECT_SOURCE_ALIASES`.

Remove local functions `_is_allowed_collect_url` and `_safe_runtime_cache_path`.

In `_strategy_collect_source_ids`, replace `_STRATEGY_COLLECT_SOURCE_ALIASES` with `STRATEGY_COLLECT_SOURCE_ALIASES`.

Replace `_safe_strategy_collect_sources` with this app-specific wrapper:

```python
def _safe_strategy_collect_sources(
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    return build_safe_strategy_collect_sources(
        settings.strategy_insight_source_list,
        source_ids=source_ids,
        root=Path.cwd(),
    )
```

- [ ] **Step 6: Run integration checks for this task**

Run:

```bash
pytest tests/test_strategy_collect_sources.py tests/test_ops_api_router.py tests/test_api_smoke.py -q
```

Expected: pass.

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

## Task 4: Full Focused Verification and Structure Re-scan

**Files:**
- Modify: no production files unless verification exposes a regression.
- Inspect: `src/tradecraft/main.py`

- [ ] **Step 1: Run focused helper and route tests**

Run:

```bash
pytest tests/test_jue_workflow_payloads.py tests/test_crypto_payloads.py tests/test_unavailable_services.py tests/test_strategy_collect_sources.py tests/test_app_route_specs.py tests/test_app_routes.py tests/test_api_smoke.py -q
```

Expected: pass.

- [ ] **Step 2: Run project contract checks**

Run:

```bash
python3 scripts/check_project_contracts.py
```

Expected:

```text
Project contract check OK
```

- [ ] **Step 3: Run diff hygiene**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Re-scan `main.py` size and remaining extraction candidates**

Run:

```bash
wc -l src/tradecraft/main.py
rg -n "^def |^async def |^class |^[A-Z0-9_]+ =" src/tradecraft/main.py
```

Expected: `main.py` is smaller than before this plan, and remaining candidates are ready for the next iteration without requiring trading behavior changes.

- [ ] **Step 5: Record next iteration candidates in the handoff**

Report the remaining best candidates from the re-scan:

```text
Next candidates:
- ETF universe helper extraction
- readiness/process-staleness helper extraction
- investment_memory decomposition plan
- venue block-trader decomposition plan
```
