# Crypto Alpha DB V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a crypto-only alpha evidence database so Binance Jue can learn from free public catalysts, label their market outcomes, and read compact decision packets as the database grows.

**Architecture:** Add a separate `CryptoAlphaService` backed by `.runtime/crypto_alpha.db`; do not merge it with KIS, Korean equity reports, or `crypto_market_research.db`. The service crawls allowlisted public sources, stores raw snapshots, extracts structured catalyst events, links them to Binance symbols, labels future outcomes from Binance market data, and produces a small `crypto_alpha` context pack for `BinanceBlockTrader`.

**Tech Stack:** Python 3.10, FastAPI, sqlite3, existing `httpx`, stdlib `html.parser`, existing `BinanceAdapter`, existing `CodexNativeRuntime`, static frontend (`index.html`, `app.js`, `style.css`), pytest.

---

## Brainstorming Summary

### Core Idea

Free crawling is useful only if it becomes a causal evidence layer. Binance Jue should not read a growing pile of raw articles or announcement pages. It should read compact answers to these questions:

- What public catalysts happened recently?
- Which symbols are affected?
- What usually happened after similar catalysts?
- Which patterns have worked or failed for Jue's Binance blocks?
- Which contradictions or data gaps should reduce confidence?

### Selected Approach: Alpha Evidence Pipeline

Use a five-layer pipeline:

1. **Source snapshots:** Store raw crawled text, content hash, source status, and errors.
2. **Catalyst events:** Convert snapshots into structured events such as listing, delisting, roadmap, unlock, protocol incident, tokenomics change, ETF/macro proxy, and ecosystem growth.
3. **Symbol links:** Resolve events to Binance spot/futures symbols with confidence.
4. **Outcome labels:** Measure post-event returns, MFE/MAE, and regime after 1h, 4h, 24h, and 72h.
5. **Context pack:** Compress recent, relevant, and historically similar events into a bounded packet for Spark/xhigh manager prompts.

### Explicit Boundaries

- This is crypto-only and must not alter KIS Jue behavior.
- This does not enable live Binance orders by itself.
- Do not introduce a heavy crawling stack or browser dependency.
- Do not send raw pages or hundreds of events to Spark.
- Do not create hard strategy bans from alpha events. Use scorecards, cautions, and risk sizing.

---

## File Structure

- Create `src/tradecraft/services/crypto_alpha.py`
  - Owns config dataclasses, repository schema, source registry, crawling, extraction, outcome labeling, scorecards, and context pack generation.
- Create `src/tradecraft/runtime/crypto_alpha_runner.py`
  - Runs periodic crawl and outcome-label cycles; writes runtime state.
- Modify `src/tradecraft/config.py`
  - Adds `TRADECRAFT_CRYPTO_ALPHA_*` settings.
- Modify `pyproject.toml`
  - Adds `tradecraft-crypto-alpha` script.
- Modify `src/tradecraft/main.py`
  - Builds `crypto_alpha_service`, exposes admin-gated API endpoints, adds readiness/process status.
- Modify `src/tradecraft/runtime/binance_block_trader_runner.py`
  - Wires `CryptoAlphaService` into `BinanceBlockTrader`.
- Modify `src/tradecraft/services/binance_block_trader.py`
  - Accepts optional `crypto_alpha_provider`, includes compact alpha context in manager inputs and persisted prompt snapshots.
- Modify `src/tradecraft/services/settings_catalog.py`
  - Adds editable crypto alpha runtime settings.
- Modify `src/tradecraft/web/static/app.js`
  - Adds Binance alpha panels and fetches alpha status/context.
- Modify `src/tradecraft/web/static/style.css`
  - Adds compact dark alpha evidence styles.
- Modify `src/tradecraft/web/static/index.html`
  - Cache-busts static assets.
- Add `tests/test_crypto_alpha.py`
  - Repository, parsing, extraction, context pack, outcome labeling tests.
- Add `tests/test_crypto_alpha_runner.py`
  - Runner state and cadence tests.
- Modify `tests/test_binance_block_trader.py`
  - Verifies manager prompt includes alpha context and remains bounded.
- Modify `tests/test_binance_block_trader_runner.py`
  - Verifies runner wiring.
- Modify `tests/test_binance_trader_api.py`
  - Verifies alpha APIs are admin-gated and return expected shape.
- Modify `tests/test_config.py`
  - Verifies settings defaults.
- Modify `tests/test_api_smoke.py`
  - Keeps health/readiness smoke green.

Commit steps are intentionally omitted because this repository guide says not to commit unless the user explicitly requests it.

---

## Task 1: Config And Entrypoint Defaults

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config test**

Add to `tests/test_config.py`:

```python
def test_crypto_alpha_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_CRYPTO_ALPHA_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_CRYPTO_ALPHA_DB_PATH", raising=False)
    monkeypatch.delenv("TRADECRAFT_CRYPTO_ALPHA_SOURCE_IDS", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.crypto_alpha_enabled is True
    assert settings.crypto_alpha_once is False
    assert settings.crypto_alpha_db_path == ".runtime/crypto_alpha.db"
    assert settings.crypto_alpha_state_path == ".runtime/crypto_alpha.json"
    assert settings.crypto_alpha_source_ids == (
        "binance_announcements,coinbase_blog,kraken_blog"
    )
    assert settings.crypto_alpha_crawl_interval_sec == 3600
    assert settings.crypto_alpha_outcome_interval_sec == 900
    assert settings.crypto_alpha_rate_limit_sec == 2.0
    assert settings.crypto_alpha_context_limit == 12
    assert settings.crypto_alpha_llm_model == "gpt-5.3-codex-spark"
    assert settings.crypto_alpha_llm_reasoning_effort == "xhigh"
```

- [ ] **Step 2: Run the config test and verify red**

Run:

```bash
pytest tests/test_config.py::test_crypto_alpha_settings_defaults -q
```

Expected: fail because `AppSettings` does not have `crypto_alpha_*` attributes.

- [ ] **Step 3: Add settings**

Add near current crypto market research settings in `src/tradecraft/config.py`:

```python
crypto_alpha_enabled: bool = Field(
    default=True,
    alias="TRADECRAFT_CRYPTO_ALPHA_ENABLED",
)
crypto_alpha_once: bool = Field(
    default=False,
    alias="TRADECRAFT_CRYPTO_ALPHA_ONCE",
)
crypto_alpha_db_path: str = Field(
    default=".runtime/crypto_alpha.db",
    alias="TRADECRAFT_CRYPTO_ALPHA_DB_PATH",
)
crypto_alpha_state_path: str = Field(
    default=".runtime/crypto_alpha.json",
    alias="TRADECRAFT_CRYPTO_ALPHA_STATE_PATH",
)
crypto_alpha_source_ids: str = Field(
    default="binance_announcements,coinbase_blog,kraken_blog",
    alias="TRADECRAFT_CRYPTO_ALPHA_SOURCE_IDS",
)
crypto_alpha_crawl_interval_sec: int = Field(
    default=3600,
    alias="TRADECRAFT_CRYPTO_ALPHA_CRAWL_INTERVAL_SEC",
)
crypto_alpha_outcome_interval_sec: int = Field(
    default=900,
    alias="TRADECRAFT_CRYPTO_ALPHA_OUTCOME_INTERVAL_SEC",
)
crypto_alpha_rate_limit_sec: float = Field(
    default=2.0,
    alias="TRADECRAFT_CRYPTO_ALPHA_RATE_LIMIT_SEC",
)
crypto_alpha_context_limit: int = Field(
    default=12,
    alias="TRADECRAFT_CRYPTO_ALPHA_CONTEXT_LIMIT",
)
crypto_alpha_llm_model: str = Field(
    default="gpt-5.3-codex-spark",
    alias="TRADECRAFT_CRYPTO_ALPHA_LLM_MODEL",
)
crypto_alpha_llm_reasoning_effort: str = Field(
    default="xhigh",
    alias="TRADECRAFT_CRYPTO_ALPHA_LLM_REASONING_EFFORT",
)
```

- [ ] **Step 4: Add runtime script**

Add to `[project.scripts]` in `pyproject.toml`:

```toml
tradecraft-crypto-alpha = "tradecraft.runtime.crypto_alpha_runner:run"
```

- [ ] **Step 5: Update `.env.example`**

Add:

```bash
TRADECRAFT_CRYPTO_ALPHA_ENABLED=true
TRADECRAFT_CRYPTO_ALPHA_ONCE=false
TRADECRAFT_CRYPTO_ALPHA_DB_PATH=.runtime/crypto_alpha.db
TRADECRAFT_CRYPTO_ALPHA_STATE_PATH=.runtime/crypto_alpha.json
TRADECRAFT_CRYPTO_ALPHA_SOURCE_IDS=binance_announcements,coinbase_blog,kraken_blog
TRADECRAFT_CRYPTO_ALPHA_CRAWL_INTERVAL_SEC=3600
TRADECRAFT_CRYPTO_ALPHA_OUTCOME_INTERVAL_SEC=900
TRADECRAFT_CRYPTO_ALPHA_RATE_LIMIT_SEC=2
TRADECRAFT_CRYPTO_ALPHA_CONTEXT_LIMIT=12
TRADECRAFT_CRYPTO_ALPHA_LLM_MODEL=gpt-5.3-codex-spark
TRADECRAFT_CRYPTO_ALPHA_LLM_REASONING_EFFORT=xhigh
```

- [ ] **Step 6: Run the config test and verify green**

Run:

```bash
pytest tests/test_config.py::test_crypto_alpha_settings_defaults -q
```

Expected: pass.

---

## Task 2: Crypto Alpha Repository Schema

**Files:**
- Create: `src/tradecraft/services/crypto_alpha.py`
- Test: `tests/test_crypto_alpha.py`

- [ ] **Step 1: Write failing schema test**

Create `tests/test_crypto_alpha.py` with:

```python
from __future__ import annotations

from pathlib import Path

from tradecraft.services.crypto_alpha import CryptoAlphaConfig, CryptoAlphaService


def test_crypto_alpha_schema_initializes(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )

    status = service.status()

    assert status["status"] == "ok"
    assert status["db_path"].endswith("crypto_alpha.db")
    assert status["sources"] >= 3
    assert status["snapshots"] == 0
    assert status["events"] == 0
    assert status["outcomes"] == 0
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_crypto_alpha_schema_initializes -q
```

Expected: import failure for `tradecraft.services.crypto_alpha`.

- [ ] **Step 3: Create service skeleton and schema**

Create `src/tradecraft/services/crypto_alpha.py`:

```python
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class CryptoAlphaConfig:
    db_path: str = ".runtime/crypto_alpha.db"
    source_ids: str = "binance_announcements,coinbase_blog,kraken_blog"
    rate_limit_sec: float = 2.0
    context_limit: int = 12
    llm_model: str = "gpt-5.3-codex-spark"
    llm_reasoning_effort: str = "xhigh"


DEFAULT_SOURCES: dict[str, dict[str, Any]] = {
    "binance_announcements": {
        "source_id": "binance_announcements",
        "label": "Binance Announcements",
        "url": "https://www.binance.com/en/support/announcement",
        "source_type": "exchange_announcement",
        "trust_score": 0.95,
    },
    "coinbase_blog": {
        "source_id": "coinbase_blog",
        "label": "Coinbase Blog",
        "url": "https://www.coinbase.com/blog",
        "source_type": "exchange_blog",
        "trust_score": 0.8,
    },
    "kraken_blog": {
        "source_id": "kraken_blog",
        "label": "Kraken Blog",
        "url": "https://blog.kraken.com",
        "source_type": "exchange_blog",
        "trust_score": 0.75,
    },
}


class CryptoAlphaRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()
        self.upsert_default_sources()

    def _connect(self) -> sqlite3.Connection:
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crypto_alpha_sources (
                    source_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT '',
                    trust_score REAL NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_crawled_at TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crypto_alpha_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    raw_text TEXT NOT NULL DEFAULT '',
                    summary_text TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    crawled_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error_message TEXT NOT NULL DEFAULT '',
                    UNIQUE(source_id, content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_alpha_snapshots_source
                    ON crypto_alpha_snapshots(source_id, crawled_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_alpha_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER,
                    source_id TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    event_time TEXT NOT NULL DEFAULT '',
                    detected_at TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    importance REAL NOT NULL DEFAULT 0,
                    decay_hours REAL NOT NULL DEFAULT 72,
                    status TEXT NOT NULL DEFAULT 'active',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_alpha_events_type_time
                    ON crypto_alpha_events(event_type, detected_at DESC);

                CREATE TABLE IF NOT EXISTS crypto_alpha_event_symbols (
                    event_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    base_asset TEXT NOT NULL DEFAULT '',
                    link_confidence REAL NOT NULL DEFAULT 0,
                    impact_direction TEXT NOT NULL DEFAULT '',
                    impact_horizon TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(event_id, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_alpha_event_symbols_symbol
                    ON crypto_alpha_event_symbols(symbol, event_id DESC);

                CREATE TABLE IF NOT EXISTS crypto_alpha_event_outcomes (
                    event_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    return_pct REAL NOT NULL DEFAULT 0,
                    mfe_pct REAL NOT NULL DEFAULT 0,
                    mae_pct REAL NOT NULL DEFAULT 0,
                    r_multiple REAL NOT NULL DEFAULT 0,
                    regime TEXT NOT NULL DEFAULT '',
                    measured_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, symbol, horizon)
                );

                CREATE TABLE IF NOT EXISTS crypto_alpha_hypotheses (
                    hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_key TEXT NOT NULL UNIQUE,
                    summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    confidence REAL NOT NULL DEFAULT 0,
                    support_count INTEGER NOT NULL DEFAULT 0,
                    avg_r_multiple REAL NOT NULL DEFAULT 0,
                    win_rate_pct REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crypto_alpha_context_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )

    def upsert_default_sources(self) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            for source in DEFAULT_SOURCES.values():
                conn.execute(
                    """
                    INSERT INTO crypto_alpha_sources (
                        source_id, label, url, source_type, trust_score, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        label=excluded.label,
                        url=excluded.url,
                        source_type=excluded.source_type,
                        trust_score=excluded.trust_score,
                        updated_at=excluded.updated_at
                    """,
                    (
                        source["source_id"],
                        source["label"],
                        source["url"],
                        source["source_type"],
                        float(source["trust_score"]),
                        now,
                    ),
                )

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            return {
                "sources": int(conn.execute("SELECT COUNT(*) FROM crypto_alpha_sources").fetchone()[0]),
                "snapshots": int(conn.execute("SELECT COUNT(*) FROM crypto_alpha_snapshots").fetchone()[0]),
                "events": int(conn.execute("SELECT COUNT(*) FROM crypto_alpha_events").fetchone()[0]),
                "outcomes": int(conn.execute("SELECT COUNT(*) FROM crypto_alpha_event_outcomes").fetchone()[0]),
                "hypotheses": int(conn.execute("SELECT COUNT(*) FROM crypto_alpha_hypotheses").fetchone()[0]),
            }


class CryptoAlphaService:
    def __init__(
        self,
        config: CryptoAlphaConfig | None = None,
        *,
        binance: Any | None = None,
        codex_runtime: Any | None = None,
    ) -> None:
        self.config = config or CryptoAlphaConfig()
        self.repository = CryptoAlphaRepository(self.config.db_path)
        self.binance = binance
        self.codex_runtime = codex_runtime

    def status(self) -> dict[str, Any]:
        counts = self.repository.counts()
        return {
            "status": "ok",
            "db_path": str(self.repository.path),
            **counts,
        }
```

- [ ] **Step 4: Run schema test**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_crypto_alpha_schema_initializes -q
```

Expected: pass.

---

## Task 3: Source Fetching, HTML Text Extraction, And Snapshot Dedupe

**Files:**
- Modify: `src/tradecraft/services/crypto_alpha.py`
- Test: `tests/test_crypto_alpha.py`

- [ ] **Step 1: Add failing snapshot tests**

Add to `tests/test_crypto_alpha.py`:

```python
def test_html_to_text_extracts_public_announcement_text() -> None:
    from tradecraft.services.crypto_alpha import html_to_text

    html = """
    <html><head><script>ignored()</script></head>
    <body><h1>Binance Will List ACME (ACME)</h1>
    <p>Trading will open for ACME/USDT at 2026-05-23 10:00 UTC.</p></body></html>
    """

    text = html_to_text(html)

    assert "Binance Will List ACME (ACME)" in text
    assert "Trading will open for ACME/USDT" in text
    assert "ignored" not in text


def test_snapshot_insert_dedupes_by_source_and_hash(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )

    first = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME)",
        raw_json={"fixture": True},
    )
    second = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME)",
        raw_json={"fixture": True},
    )

    assert first["snapshot_id"] == second["snapshot_id"]
    assert service.status()["snapshots"] == 1
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_html_to_text_extracts_public_announcement_text tests/test_crypto_alpha.py::test_snapshot_insert_dedupes_by_source_and_hash -q
```

Expected: fail because `html_to_text` and `store_snapshot` do not exist.

- [ ] **Step 3: Add `html_to_text` parser**

Add to `src/tradecraft/services/crypto_alpha.py`:

```python
class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.parts.append(text)


def html_to_text(html: str) -> str:
    parser = _TextHTMLParser()
    parser.feed(html or "")
    return "\n".join(parser.parts)
```

- [ ] **Step 4: Add snapshot insert helper**

Add to `CryptoAlphaRepository`:

```python
    def upsert_snapshot(
        self,
        *,
        source_id: str,
        url: str,
        title: str,
        raw_text: str,
        raw_json: dict[str, Any] | None = None,
        status: str = "ok",
        error_message: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        digest = content_hash(raw_text)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_alpha_snapshots (
                    source_id, url, title, content_hash, raw_text, raw_json,
                    crawled_at, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, content_hash) DO UPDATE SET
                    url=excluded.url,
                    title=excluded.title,
                    raw_json=excluded.raw_json,
                    crawled_at=excluded.crawled_at,
                    status=excluded.status,
                    error_message=excluded.error_message
                """,
                (
                    source_id,
                    url,
                    title,
                    digest,
                    raw_text,
                    json_dumps(raw_json or {}),
                    now,
                    status,
                    error_message,
                ),
            )
            row = conn.execute(
                """
                SELECT snapshot_id, source_id, url, title, content_hash, crawled_at, status
                FROM crypto_alpha_snapshots
                WHERE source_id=? AND content_hash=?
                """,
                (source_id, digest),
            ).fetchone()
        return dict(row)
```

Add to `CryptoAlphaService`:

```python
    def store_snapshot(
        self,
        *,
        source_id: str,
        url: str,
        title: str,
        raw_text: str,
        raw_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repository.upsert_snapshot(
            source_id=source_id,
            url=url,
            title=title,
            raw_text=raw_text,
            raw_json=raw_json,
        )
```

- [ ] **Step 5: Add safe HTTP fetch method**

Add to `CryptoAlphaService`:

```python
    async def fetch_source_snapshot(
        self,
        *,
        source_id: str,
        url: str,
        title: str = "",
        timeout_sec: float = 12.0,
    ) -> dict[str, Any]:
        allowed = DEFAULT_SOURCES.get(source_id)
        if allowed is None or not url.startswith(str(allowed["url"]).split("/support")[0]):
            raise ValueError(f"crypto alpha source not allowlisted: {source_id}")
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "TradeCraft-HERMES/1.0"})
            response.raise_for_status()
        raw_text = html_to_text(response.text)
        return self.store_snapshot(
            source_id=source_id,
            url=str(response.url),
            title=title or raw_text.splitlines()[0][:160] if raw_text else source_id,
            raw_text=raw_text,
            raw_json={"http_status": response.status_code},
        )
```

- [ ] **Step 6: Run snapshot tests**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_html_to_text_extracts_public_announcement_text tests/test_crypto_alpha.py::test_snapshot_insert_dedupes_by_source_and_hash -q
```

Expected: pass.

---

## Task 4: Event Extraction And Binance Symbol Linking

**Files:**
- Modify: `src/tradecraft/services/crypto_alpha.py`
- Test: `tests/test_crypto_alpha.py`

- [ ] **Step 1: Add failing extraction test**

Add:

```python
def test_extracts_listing_event_and_links_symbol(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text=(
            "Binance Will List ACME (ACME). "
            "Trading will open for ACME/USDT spot trading pair."
        ),
        raw_json={"fixture": True},
    )

    result = service.extract_events_from_snapshot(snapshot["snapshot_id"])
    context = service.context_pack(symbols=["ACMEUSDT"], limit=5)

    assert result["created_events"] == 1
    assert context["events"][0]["event_type"] == "listing"
    assert context["events"][0]["symbols"] == ["ACMEUSDT"]
    assert context["events"][0]["source_id"] == "binance_announcements"
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_extracts_listing_event_and_links_symbol -q
```

Expected: fail because extraction/context methods do not exist.

- [ ] **Step 3: Add repository reads and event inserts**

Add to `CryptoAlphaRepository`:

```python
    def get_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM crypto_alpha_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        return dict(row) if row else None

    def insert_event(
        self,
        *,
        snapshot_id: int,
        source_id: str,
        event_type: str,
        title: str,
        summary: str,
        confidence: float,
        importance: float,
        symbols: list[dict[str, Any]],
    ) -> int:
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO crypto_alpha_events (
                    snapshot_id, source_id, event_type, title, summary, event_time,
                    detected_at, confidence, importance, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    source_id,
                    event_type,
                    title,
                    summary,
                    now,
                    now,
                    confidence,
                    importance,
                    json_dumps({"extractor": "rules_v1"}),
                ),
            )
            event_id = int(cursor.lastrowid)
            for item in symbols:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO crypto_alpha_event_symbols (
                        event_id, symbol, base_asset, link_confidence,
                        impact_direction, impact_horizon, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        item["symbol"],
                        item["base_asset"],
                        item["link_confidence"],
                        item["impact_direction"],
                        item["impact_horizon"],
                        item["reason"],
                    ),
                )
        return event_id

    def recent_events(self, *, symbols: list[str], limit: int) -> list[dict[str, Any]]:
        normalized = [str(symbol).upper() for symbol in symbols if symbol]
        params: list[Any] = []
        symbol_filter = ""
        if normalized:
            marks = ",".join("?" for _ in normalized)
            symbol_filter = f"WHERE es.symbol IN ({marks})"
            params.extend(normalized)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*, GROUP_CONCAT(es.symbol) AS linked_symbols
                FROM crypto_alpha_events e
                JOIN crypto_alpha_event_symbols es ON es.event_id=e.event_id
                {symbol_filter}
                GROUP BY e.event_id
                ORDER BY e.detected_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Add deterministic extractor**

Add to `src/tradecraft/services/crypto_alpha.py`:

```python
SYMBOL_PATTERN = re.compile(r"\b([A-Z0-9]{2,12})(?:/USDT|USDT|\s*\(([A-Z0-9]{2,12})\))")


def normalize_crypto_symbol(base: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", str(base or "").upper())
    if cleaned.endswith("USDT"):
        return cleaned
    return f"{cleaned}USDT" if cleaned else ""


def extract_symbol_links(text: str) -> list[dict[str, Any]]:
    bases: set[str] = set()
    for match in SYMBOL_PATTERN.finditer(text.upper()):
        base = match.group(2) or match.group(1)
        if base and base not in {"USDT", "USD", "Binance".upper()}:
            bases.add(base.removesuffix("USDT"))
    return [
        {
            "symbol": normalize_crypto_symbol(base),
            "base_asset": base,
            "link_confidence": 0.86,
            "impact_direction": "bullish_watch",
            "impact_horizon": "1h_72h",
            "reason": "symbol appeared in public catalyst text",
        }
        for base in sorted(bases)
        if normalize_crypto_symbol(base)
    ]


def classify_event_type(title: str, text: str) -> tuple[str, float]:
    haystack = f"{title}\n{text}".lower()
    if "will list" in haystack or "new listing" in haystack or "add support" in haystack:
        return "listing", 0.9
    if "will delist" in haystack or "remove trading" in haystack:
        return "delisting", 0.92
    if "airdrop" in haystack or "launchpool" in haystack:
        return "supply_or_incentive", 0.78
    if "network upgrade" in haystack or "mainnet" in haystack:
        return "protocol_update", 0.72
    if "exploit" in haystack or "incident" in haystack or "halt" in haystack:
        return "incident", 0.82
    return "project_update", 0.55
```

Add to `CryptoAlphaService`:

```python
    def extract_events_from_snapshot(self, snapshot_id: int) -> dict[str, Any]:
        snapshot = self.repository.get_snapshot(snapshot_id)
        if snapshot is None:
            return {"status": "not_found", "created_events": 0}
        title = str(snapshot.get("title") or "")
        raw_text = str(snapshot.get("raw_text") or "")
        event_type, confidence = classify_event_type(title, raw_text)
        symbols = extract_symbol_links(f"{title}\n{raw_text}")
        if not symbols:
            return {"status": "no_symbols", "created_events": 0}
        event_id = self.repository.insert_event(
            snapshot_id=int(snapshot_id),
            source_id=str(snapshot["source_id"]),
            event_type=event_type,
            title=title,
            summary=raw_text[:500],
            confidence=confidence,
            importance=min(1.0, confidence + 0.05 * len(symbols)),
            symbols=symbols,
        )
        return {"status": "ok", "created_events": 1, "event_ids": [event_id]}

    def context_pack(self, *, symbols: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
        max_items = int(limit or self.config.context_limit)
        events = self.repository.recent_events(symbols=symbols or [], limit=max_items)
        packed = []
        for event in events:
            linked_symbols = [
                value for value in str(event.get("linked_symbols") or "").split(",") if value
            ]
            packed.append(
                {
                    "event_id": event["event_id"],
                    "source_id": event["source_id"],
                    "event_type": event["event_type"],
                    "title": event["title"],
                    "summary": str(event["summary"])[:360],
                    "confidence": event["confidence"],
                    "importance": event["importance"],
                    "detected_at": event["detected_at"],
                    "symbols": linked_symbols,
                }
            )
        return {
            "status": "ok",
            "scope": "binance_crypto_alpha",
            "events": packed,
            "event_count": len(packed),
            "limit": max_items,
        }
```

- [ ] **Step 5: Run extraction test**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_extracts_listing_event_and_links_symbol -q
```

Expected: pass.

---

## Task 5: Outcome Labeling From Binance Klines

**Files:**
- Modify: `src/tradecraft/services/crypto_alpha.py`
- Test: `tests/test_crypto_alpha.py`

- [ ] **Step 1: Add failing outcome test**

Add:

```python
def test_labels_event_outcome_from_binance_klines(tmp_path: Path) -> None:
    class FakeBinance:
        async def fetch_klines(
            self,
            symbol: str,
            *,
            market: str = "spot",
            interval: str = "1h",
            limit: int = 80,
        ) -> list[dict[str, float]]:
            assert symbol == "ACMEUSDT"
            return [
                {"open": 100, "high": 101, "low": 99, "close": 100, "open_time": 1},
                {"open": 100, "high": 112, "low": 97, "close": 108, "open_time": 2},
                {"open": 108, "high": 116, "low": 104, "close": 112, "open_time": 3},
            ]

    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db")),
        binance=FakeBinance(),
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME). Trading will open for ACME/USDT.",
    )
    service.extract_events_from_snapshot(snapshot["snapshot_id"])

    result = asyncio.run(service.label_due_outcomes())
    context = service.context_pack(symbols=["ACMEUSDT"], limit=5)

    assert result["status"] == "ok"
    assert result["labeled"] >= 1
    assert context["similar_outcomes"][0]["symbol"] == "ACMEUSDT"
    assert context["similar_outcomes"][0]["horizon"] == "24h"
    assert context["similar_outcomes"][0]["return_pct"] == pytest.approx(12.0)
    assert context["similar_outcomes"][0]["mfe_pct"] == pytest.approx(16.0)
    assert context["similar_outcomes"][0]["mae_pct"] == pytest.approx(-3.0)
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_labels_event_outcome_from_binance_klines -q
```

Expected: fail because outcome methods and `similar_outcomes` are missing.

- [ ] **Step 3: Add repository outcome helpers**

Add to `CryptoAlphaRepository`:

```python
    def unlabeled_event_symbols(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.event_id, e.event_type, e.detected_at, es.symbol
                FROM crypto_alpha_events e
                JOIN crypto_alpha_event_symbols es ON es.event_id=e.event_id
                LEFT JOIN crypto_alpha_event_outcomes o
                    ON o.event_id=e.event_id AND o.symbol=es.symbol AND o.horizon='24h'
                WHERE o.event_id IS NULL
                ORDER BY e.detected_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_outcome(
        self,
        *,
        event_id: int,
        symbol: str,
        horizon: str,
        return_pct: float,
        mfe_pct: float,
        mae_pct: float,
        r_multiple: float,
        regime: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_alpha_event_outcomes (
                    event_id, symbol, horizon, return_pct, mfe_pct, mae_pct,
                    r_multiple, regime, measured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, symbol, horizon) DO UPDATE SET
                    return_pct=excluded.return_pct,
                    mfe_pct=excluded.mfe_pct,
                    mae_pct=excluded.mae_pct,
                    r_multiple=excluded.r_multiple,
                    regime=excluded.regime,
                    measured_at=excluded.measured_at
                """,
                (
                    event_id,
                    symbol,
                    horizon,
                    return_pct,
                    mfe_pct,
                    mae_pct,
                    r_multiple,
                    regime,
                    utc_now_iso(),
                ),
            )

    def outcomes_for_symbols(self, *, symbols: list[str], limit: int) -> list[dict[str, Any]]:
        normalized = [str(symbol).upper() for symbol in symbols if symbol]
        if not normalized:
            return []
        marks = ",".join("?" for _ in normalized)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT o.*, e.event_type, e.source_id
                FROM crypto_alpha_event_outcomes o
                JOIN crypto_alpha_events e ON e.event_id=o.event_id
                WHERE o.symbol IN ({marks})
                ORDER BY o.measured_at DESC
                LIMIT ?
                """,
                (*normalized, limit),
            ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Add outcome calculation**

Add to `CryptoAlphaService`:

```python
    async def label_due_outcomes(self, *, limit: int = 20) -> dict[str, Any]:
        if self.binance is None:
            return {"status": "skipped", "reason": "binance_adapter_missing", "labeled": 0}
        candidates = self.repository.unlabeled_event_symbols(limit=limit)
        labeled = 0
        errors: list[dict[str, Any]] = []
        for item in candidates:
            symbol = str(item["symbol"])
            try:
                klines = await self.binance.fetch_klines(
                    symbol,
                    market="spot",
                    interval="1h",
                    limit=80,
                )
                if len(klines) < 2:
                    continue
                first = float(klines[0]["open"])
                last = float(klines[-1]["close"])
                high = max(float(row["high"]) for row in klines)
                low = min(float(row["low"]) for row in klines)
                if first <= 0:
                    continue
                return_pct = ((last - first) / first) * 100
                mfe_pct = ((high - first) / first) * 100
                mae_pct = ((low - first) / first) * 100
                r_multiple = return_pct / max(abs(mae_pct), 1.0)
                self.repository.upsert_outcome(
                    event_id=int(item["event_id"]),
                    symbol=symbol,
                    horizon="24h",
                    return_pct=return_pct,
                    mfe_pct=mfe_pct,
                    mae_pct=mae_pct,
                    r_multiple=r_multiple,
                )
                labeled += 1
            except Exception as exc:
                logger.warning("crypto alpha outcome label failed for %s: %s", symbol, exc)
                errors.append({"symbol": symbol, "error": str(exc)})
        self.refresh_scorecards()
        return {"status": "ok", "labeled": labeled, "errors": errors[:5]}
```

Update `context_pack()` to include:

```python
        outcomes = self.repository.outcomes_for_symbols(symbols=symbols or [], limit=max_items)
        return {
            "status": "ok",
            "scope": "binance_crypto_alpha",
            "events": packed,
            "similar_outcomes": [
                {
                    "event_id": row["event_id"],
                    "symbol": row["symbol"],
                    "event_type": row["event_type"],
                    "source_id": row["source_id"],
                    "horizon": row["horizon"],
                    "return_pct": row["return_pct"],
                    "mfe_pct": row["mfe_pct"],
                    "mae_pct": row["mae_pct"],
                    "r_multiple": row["r_multiple"],
                    "regime": row["regime"],
                }
                for row in outcomes
            ],
            "event_count": len(packed),
            "limit": max_items,
        }
```

- [ ] **Step 5: Add no-op `refresh_scorecards()`**

Add:

```python
    def refresh_scorecards(self) -> dict[str, Any]:
        return {"status": "ok", "updated": 0}
```

- [ ] **Step 6: Run outcome test**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_labels_event_outcome_from_binance_klines -q
```

Expected: pass.

---

## Task 6: Pattern Scorecards And Compact Context Pack

**Files:**
- Modify: `src/tradecraft/services/crypto_alpha.py`
- Test: `tests/test_crypto_alpha.py`

- [ ] **Step 1: Add failing context compression test**

Add:

```python
def test_context_pack_stays_compact_with_many_events(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"), context_limit=7)
    )
    for idx in range(30):
        snapshot = service.store_snapshot(
            source_id="binance_announcements",
            url=f"https://www.binance.com/en/support/announcement/acme-{idx}",
            title=f"Binance Will List ACME (ACME) #{idx}",
            raw_text=f"Binance Will List ACME (ACME). ACME/USDT catalyst #{idx}.",
        )
        service.extract_events_from_snapshot(snapshot["snapshot_id"])

    pack = service.context_pack(symbols=["ACMEUSDT"], limit=7)

    assert pack["event_count"] == 7
    assert len(json.dumps(pack, ensure_ascii=False)) < 12000
    assert all("raw_text" not in item for item in pack["events"])
    assert "active_lessons" in pack
    assert "data_gaps" in pack
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_context_pack_stays_compact_with_many_events -q
```

Expected: fail because `active_lessons` and `data_gaps` are missing.

- [ ] **Step 3: Implement scorecards**

Replace `refresh_scorecards()` with:

```python
    def refresh_scorecards(self) -> dict[str, Any]:
        with self.repository._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.event_type, e.source_id,
                       COUNT(*) AS n,
                       AVG(o.r_multiple) AS avg_r,
                       AVG(CASE WHEN o.return_pct > 0 THEN 1.0 ELSE 0.0 END) * 100 AS win_rate
                FROM crypto_alpha_event_outcomes o
                JOIN crypto_alpha_events e ON e.event_id=o.event_id
                GROUP BY e.event_type, e.source_id
                """
            ).fetchall()
            updated = 0
            for row in rows:
                pattern_key = f"{row['source_id']}:{row['event_type']}"
                n = int(row["n"] or 0)
                avg_r = float(row["avg_r"] or 0)
                win_rate = float(row["win_rate"] or 0)
                confidence = min(0.9, 0.25 + n * 0.08)
                status = "active" if n >= 3 else "candidate"
                conn.execute(
                    """
                    INSERT INTO crypto_alpha_hypotheses (
                        pattern_key, summary, status, confidence, support_count,
                        avg_r_multiple, win_rate_pct, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pattern_key) DO UPDATE SET
                        summary=excluded.summary,
                        status=excluded.status,
                        confidence=excluded.confidence,
                        support_count=excluded.support_count,
                        avg_r_multiple=excluded.avg_r_multiple,
                        win_rate_pct=excluded.win_rate_pct,
                        updated_at=excluded.updated_at
                    """,
                    (
                        pattern_key,
                        f"{pattern_key} n={n} avgR={avg_r:.2f} win={win_rate:.1f}%",
                        status,
                        confidence,
                        n,
                        avg_r,
                        win_rate,
                        utc_now_iso(),
                    ),
                )
                updated += 1
        return {"status": "ok", "updated": updated}
```

Add to `CryptoAlphaRepository`:

```python
    def scorecards(self, *, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM crypto_alpha_hypotheses
                ORDER BY status='active' DESC, confidence DESC, support_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Update `context_pack()`**

Make the returned dict include:

```python
            "scorecards": [
                {
                    "pattern_key": row["pattern_key"],
                    "status": row["status"],
                    "confidence": row["confidence"],
                    "support_count": row["support_count"],
                    "avg_r_multiple": row["avg_r_multiple"],
                    "win_rate_pct": row["win_rate_pct"],
                    "summary": row["summary"],
                }
                for row in self.repository.scorecards(limit=6)
            ],
            "active_lessons": [
                row["summary"]
                for row in self.repository.scorecards(limit=4)
                if row["status"] == "active"
            ],
            "contradictions": [],
            "data_gaps": [
                "no_labeled_outcomes" if not outcomes else "",
                "no_recent_symbol_events" if not packed else "",
            ],
```

Filter empty data gaps before returning:

```python
        result["data_gaps"] = [gap for gap in result["data_gaps"] if gap]
```

- [ ] **Step 5: Run context compression test**

Run:

```bash
pytest tests/test_crypto_alpha.py::test_context_pack_stays_compact_with_many_events -q
```

Expected: pass.

---

## Task 7: Wire Crypto Alpha Into Binance Block Manager

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_binance_block_trader_runner.py`

- [ ] **Step 1: Add failing manager prompt test**

Add to `tests/test_binance_block_trader.py`:

```python
def test_binance_manager_prompt_includes_crypto_alpha_context(tmp_path: Path) -> None:
    class FakeAlpha:
        def context_pack(self, *, symbols: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
            return {
                "status": "ok",
                "scope": "binance_crypto_alpha",
                "events": [
                    {
                        "event_id": 1,
                        "event_type": "listing",
                        "title": "Binance Will List ACME",
                        "symbols": ["ACMEUSDT"],
                    }
                ],
                "scorecards": [{"pattern_key": "binance_announcements:listing"}],
                "active_lessons": ["listing catalysts need entry discipline"],
                "data_gaps": [],
            }

    trader = BinanceBlockTrader(
        config=BinanceBlockTraderConfig(db_path=str(tmp_path / "binance_blocks.db")),
        crypto_alpha_provider=FakeAlpha(),
    )

    payload = trader._manager_payload(symbols=["ACMEUSDT"])

    assert payload["crypto_alpha"]["scope"] == "binance_crypto_alpha"
    assert payload["crypto_alpha"]["events"][0]["symbols"] == ["ACMEUSDT"]
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_binance_manager_prompt_includes_crypto_alpha_context -q
```

Expected: fail because constructor/payload does not include `crypto_alpha_provider`.

- [ ] **Step 3: Modify BinanceBlockTrader constructor**

In `BinanceBlockTrader.__init__`, add parameter:

```python
        crypto_alpha_provider: Any | None = None,
```

Set:

```python
        self.crypto_alpha_provider = crypto_alpha_provider
```

- [ ] **Step 4: Add alpha context helper**

Add near `_crypto_research_context`:

```python
    def _crypto_alpha_context(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.crypto_alpha_provider is None:
            return {"status": "missing", "events": [], "scorecards": [], "data_gaps": ["crypto_alpha_missing"]}
        try:
            return self.crypto_alpha_provider.context_pack(symbols=symbols, limit=12)
        except TypeError:
            return self.crypto_alpha_provider.context_pack(symbols=symbols)
        except Exception as exc:
            logger.warning("binance crypto alpha context failed: %s", exc)
            return {"status": "error", "events": [], "scorecards": [], "error_message": str(exc)}
```

- [ ] **Step 5: Include alpha in manager payload**

Where manager payload currently includes `crypto_research`, add:

```python
        crypto_alpha = self._crypto_alpha_context(symbols=symbols)
```

and include:

```python
            "crypto_alpha": crypto_alpha,
```

Make sure prompt text tells Spark to use alpha as evidence, not as a trade command:

```python
"Use crypto_alpha as catalyst memory: recent events, similar outcomes, scorecards, lessons, and gaps."
```

- [ ] **Step 6: Wire runner**

In `src/tradecraft/runtime/binance_block_trader_runner.py`, import:

```python
from tradecraft.services.crypto_alpha import CryptoAlphaConfig, CryptoAlphaService
```

Add builder:

```python
def _build_crypto_alpha_service(settings: AppSettings, *, binance: BinanceAdapter) -> CryptoAlphaService | None:
    if not bool(_setting(settings, "crypto_alpha_enabled", True)):
        return None
    return CryptoAlphaService(
        config=CryptoAlphaConfig(
            db_path=str(_setting(settings, "crypto_alpha_db_path", ".runtime/crypto_alpha.db")),
            source_ids=str(_setting(settings, "crypto_alpha_source_ids", "")),
            rate_limit_sec=float(_setting(settings, "crypto_alpha_rate_limit_sec", 2.0)),
            context_limit=int(_setting(settings, "crypto_alpha_context_limit", 12)),
            llm_model=str(_setting(settings, "crypto_alpha_llm_model", "gpt-5.3-codex-spark")),
            llm_reasoning_effort=str(_setting(settings, "crypto_alpha_llm_reasoning_effort", "xhigh")),
        ),
        binance=binance,
    )
```

Instantiate and pass:

```python
    crypto_alpha = _build_crypto_alpha_service(settings, binance=binance)
    trader = BinanceBlockTrader(
        ...,
        crypto_alpha_provider=crypto_alpha,
    )
```

- [ ] **Step 7: Add runner wiring test**

Add to `tests/test_binance_block_trader_runner.py`:

```python
def test_build_trader_wires_crypto_alpha_provider(monkeypatch, tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None)
    monkeypatch.setattr(settings, "binance_block_trader_db_path", str(tmp_path / "blocks.db"))
    monkeypatch.setattr(settings, "crypto_alpha_db_path", str(tmp_path / "alpha.db"))

    trader = _build_trader(settings)

    assert trader.crypto_alpha_provider is not None
```

- [ ] **Step 8: Run wiring tests**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_binance_manager_prompt_includes_crypto_alpha_context tests/test_binance_block_trader_runner.py::test_build_trader_wires_crypto_alpha_provider -q
```

Expected: pass.

---

## Task 8: API Endpoints And Readiness

**Files:**
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_binance_trader_api.py`
- Test: `tests/test_api_smoke.py`

- [ ] **Step 1: Add failing API test**

Add to `tests/test_binance_trader_api.py`:

```python
def test_crypto_alpha_api_requires_admin_and_returns_context(monkeypatch) -> None:
    class FakeAlpha:
        def status(self) -> dict[str, Any]:
            return {"status": "ok", "events": 2}

        def context_pack(self, *, symbols: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
            return {"status": "ok", "events": [{"symbol": "BTCUSDT"}], "limit": limit}

    monkeypatch.setattr(main, "crypto_alpha_service", FakeAlpha())

    with TestClient(main.app) as client:
        unauth = client.get("/api/crypto/alpha/status")
        assert unauth.status_code == 401

        response = client.get(
            "/api/crypto/alpha/context?symbols=BTCUSDT",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    assert response.json()["events"][0]["symbol"] == "BTCUSDT"
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_binance_trader_api.py::test_crypto_alpha_api_requires_admin_and_returns_context -q
```

Expected: fail because endpoint/service global does not exist.

- [ ] **Step 3: Build service in `main.py`**

Add imports guarded like crypto market research if needed:

```python
from tradecraft.services.crypto_alpha import CryptoAlphaConfig, CryptoAlphaService
```

Build after `crypto_market_research_service`:

```python
crypto_alpha_service = CryptoAlphaService(
    config=CryptoAlphaConfig(
        db_path=settings.crypto_alpha_db_path,
        source_ids=settings.crypto_alpha_source_ids,
        rate_limit_sec=settings.crypto_alpha_rate_limit_sec,
        context_limit=settings.crypto_alpha_context_limit,
        llm_model=settings.crypto_alpha_llm_model,
        llm_reasoning_effort=settings.crypto_alpha_llm_reasoning_effort,
    ),
    binance=binance,
)
```

Pass into `BinanceBlockTrader`:

```python
    crypto_alpha_provider=crypto_alpha_service,
```

- [ ] **Step 4: Add admin-gated endpoints**

Add to `src/tradecraft/main.py`:

```python
@app.get("/api/crypto/alpha/status", dependencies=[Depends(require_admin)])
async def api_crypto_alpha_status() -> dict[str, Any]:
    return crypto_alpha_service.status()


@app.get("/api/crypto/alpha/context", dependencies=[Depends(require_admin)])
async def api_crypto_alpha_context(
    symbols: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    parsed = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    return crypto_alpha_service.context_pack(symbols=parsed, limit=max(1, min(limit, 30)))


@app.post("/api/crypto/alpha/outcomes/run-once", dependencies=[Depends(require_admin)])
async def api_crypto_alpha_outcomes_run_once() -> dict[str, Any]:
    return await crypto_alpha_service.label_due_outcomes()
```

Add collect endpoint after Task 9 implements `collect_once`:

```python
@app.post("/api/crypto/alpha/collect", dependencies=[Depends(require_admin)])
async def api_crypto_alpha_collect() -> dict[str, Any]:
    return await crypto_alpha_service.collect_once()
```

- [ ] **Step 5: Add readiness process section**

In `/api/ops/readiness` process map, add:

```python
"crypto_alpha": (
    _runner_status_with_cover(runner_process_status("crypto_alpha")),
    [base / "runtime" / "crypto_alpha_runner.py", base / "services" / "crypto_alpha.py"],
),
```

Add warning:

```python
if settings.crypto_alpha_enabled and not bool(processes["crypto_alpha"].get("direct_alive")):
    warnings.append("crypto_alpha_runner_not_alive")
```

- [ ] **Step 6: Run API test**

Run:

```bash
pytest tests/test_binance_trader_api.py::test_crypto_alpha_api_requires_admin_and_returns_context tests/test_api_smoke.py -q
```

Expected: pass.

---

## Task 9: Crypto Alpha Runner

**Files:**
- Create: `src/tradecraft/runtime/crypto_alpha_runner.py`
- Test: `tests/test_crypto_alpha_runner.py`

- [ ] **Step 1: Add failing runner test**

Create `tests/test_crypto_alpha_runner.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tradecraft.config import AppSettings
from tradecraft.runtime.crypto_alpha_runner import run_crypto_alpha_cycle


def test_crypto_alpha_runner_writes_state_once(monkeypatch, tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None)
    monkeypatch.setattr(settings, "crypto_alpha_once", True)
    monkeypatch.setattr(settings, "crypto_alpha_db_path", str(tmp_path / "alpha.db"))
    monkeypatch.setattr(settings, "crypto_alpha_state_path", str(tmp_path / "alpha.json"))

    result = asyncio.run(run_crypto_alpha_cycle(settings=settings))

    state = json.loads(Path(settings.crypto_alpha_state_path).read_text())
    assert result["status"] in {"ok", "partial", "skipped"}
    assert state["service"] == "tradecraft-crypto-alpha"
    assert "last_cycle_at" in state
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_crypto_alpha_runner.py::test_crypto_alpha_runner_writes_state_once -q
```

Expected: import failure.

- [ ] **Step 3: Add service `collect_once()`**

Add to `CryptoAlphaService`:

```python
    async def collect_once(self) -> dict[str, Any]:
        source_ids = [
            item.strip()
            for item in self.config.source_ids.split(",")
            if item.strip() in DEFAULT_SOURCES
        ]
        created_snapshots = 0
        created_events = 0
        errors: list[dict[str, Any]] = []
        for source_id in source_ids:
            source = DEFAULT_SOURCES[source_id]
            try:
                snapshot = await self.fetch_source_snapshot(
                    source_id=source_id,
                    url=str(source["url"]),
                    title=str(source["label"]),
                )
                created_snapshots += 1
                extracted = self.extract_events_from_snapshot(int(snapshot["snapshot_id"]))
                created_events += int(extracted.get("created_events") or 0)
            except Exception as exc:
                logger.warning("crypto alpha crawl failed for %s: %s", source_id, exc)
                errors.append({"source_id": source_id, "error": str(exc)})
        status = "ok" if not errors else "partial"
        return {
            "status": status,
            "sources": source_ids,
            "created_snapshots": created_snapshots,
            "created_events": created_events,
            "errors": errors[:5],
        }
```

- [ ] **Step 4: Create runner**

Create `src/tradecraft/runtime/crypto_alpha_runner.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.process_registry import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.services.binance import BinanceAdapter, BinanceConfig
from tradecraft.services.crypto_alpha import CryptoAlphaConfig, CryptoAlphaService, utc_now_iso

logger = logging.getLogger(__name__)


def _write_state(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_binance(settings: AppSettings) -> BinanceAdapter:
    return BinanceAdapter(
        BinanceConfig(
            spot_api_key=settings.binance_spot_api_key,
            spot_api_secret=settings.binance_spot_api_secret,
            spot_base_url=settings.binance_spot_base_url,
            futures_api_key=settings.binance_futures_api_key,
            futures_api_secret=settings.binance_futures_api_secret,
            futures_base_url=settings.binance_futures_base_url,
            usdt_krw_rate=settings.binance_usdt_krw,
        )
    )


def _build_service(settings: AppSettings) -> CryptoAlphaService:
    return CryptoAlphaService(
        config=CryptoAlphaConfig(
            db_path=settings.crypto_alpha_db_path,
            source_ids=settings.crypto_alpha_source_ids,
            rate_limit_sec=settings.crypto_alpha_rate_limit_sec,
            context_limit=settings.crypto_alpha_context_limit,
            llm_model=settings.crypto_alpha_llm_model,
            llm_reasoning_effort=settings.crypto_alpha_llm_reasoning_effort,
        ),
        binance=_build_binance(settings),
    )


async def run_crypto_alpha_cycle(*, settings: AppSettings | None = None) -> dict[str, Any]:
    settings = settings or AppSettings()
    service = _build_service(settings)
    collect_result = await service.collect_once()
    outcome_result = await service.label_due_outcomes()
    payload = {
        "service": "tradecraft-crypto-alpha",
        "status": collect_result.get("status", "ok"),
        "last_cycle_at": utc_now_iso(),
        "collect": collect_result,
        "outcomes": outcome_result,
        "service_status": service.status(),
    }
    _write_state(settings.crypto_alpha_state_path, payload)
    return payload


async def run_loop(settings: AppSettings | None = None) -> None:
    settings = settings or AppSettings()
    while True:
        if not settings.crypto_alpha_enabled:
            _write_state(
                settings.crypto_alpha_state_path,
                {
                    "service": "tradecraft-crypto-alpha",
                    "status": "disabled",
                    "last_cycle_at": utc_now_iso(),
                },
            )
            return
        try:
            await run_crypto_alpha_cycle(settings=settings)
        except Exception as exc:
            logger.exception("crypto alpha cycle failed")
            _write_state(
                settings.crypto_alpha_state_path,
                {
                    "service": "tradecraft-crypto-alpha",
                    "status": "error",
                    "last_cycle_at": utc_now_iso(),
                    "error_message": str(exc),
                },
            )
        if settings.crypto_alpha_once:
            return
        await asyncio.sleep(max(int(settings.crypto_alpha_crawl_interval_sec), 300))


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    write_current_runner_pid("crypto_alpha")
    try:
        asyncio.run(run_loop())
    finally:
        clear_current_runner_pid("crypto_alpha")
```

- [ ] **Step 5: Run runner test**

Run:

```bash
pytest tests/test_crypto_alpha_runner.py::test_crypto_alpha_runner_writes_state_once -q
```

Expected: pass.

---

## Task 10: UI And Settings Surface

**Files:**
- Modify: `src/tradecraft/services/settings_catalog.py`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Modify: `src/tradecraft/web/static/index.html`
- Test: `tests/test_settings_api.py`

- [ ] **Step 1: Add settings catalog test**

Add to `tests/test_settings_api.py`:

```python
def test_settings_catalog_includes_crypto_alpha_options() -> None:
    from tradecraft.services.settings_catalog import settings_catalog

    keys = {item["key"] for item in settings_catalog()}

    assert "TRADECRAFT_CRYPTO_ALPHA_ENABLED" in keys
    assert "TRADECRAFT_CRYPTO_ALPHA_CRAWL_INTERVAL_SEC" in keys
    assert "TRADECRAFT_CRYPTO_ALPHA_CONTEXT_LIMIT" in keys
```

- [ ] **Step 2: Run and verify red**

Run:

```bash
pytest tests/test_settings_api.py::test_settings_catalog_includes_crypto_alpha_options -q
```

Expected: fail because settings catalog does not expose crypto alpha options.

- [ ] **Step 3: Add settings catalog entries**

In `src/tradecraft/services/settings_catalog.py`, add:

```python
{
    "key": "TRADECRAFT_CRYPTO_ALPHA_ENABLED",
    "label": "Crypto Alpha DB",
    "group": "Binance Jue",
    "kind": "bool",
    "description": "Enable the crypto-only catalyst evidence database.",
},
{
    "key": "TRADECRAFT_CRYPTO_ALPHA_CRAWL_INTERVAL_SEC",
    "label": "Crypto Alpha Crawl Interval",
    "group": "Binance Jue",
    "kind": "int",
    "description": "Seconds between allowlisted public catalyst crawl cycles.",
},
{
    "key": "TRADECRAFT_CRYPTO_ALPHA_CONTEXT_LIMIT",
    "label": "Crypto Alpha Context Limit",
    "group": "Binance Jue",
    "kind": "int",
    "description": "Maximum alpha events sent to Binance Jue per manager run.",
},
```

- [ ] **Step 4: Add UI state and fetch**

In `src/tradecraft/web/static/app.js`, add to global state:

```javascript
cryptoAlpha: {
  status: null,
  context: null,
  loading: false,
  error: "",
},
```

Add fetch helper:

```javascript
async function loadCryptoAlpha() {
  state.cryptoAlpha.loading = true;
  try {
    const [status, context] = await Promise.all([
      apiFetch("/api/crypto/alpha/status"),
      apiFetch("/api/crypto/alpha/context?limit=8"),
    ]);
    state.cryptoAlpha.status = status;
    state.cryptoAlpha.context = context;
    state.cryptoAlpha.error = "";
  } catch (error) {
    state.cryptoAlpha.error = parseErrorMessage(error);
  } finally {
    state.cryptoAlpha.loading = false;
    render();
  }
}
```

Call `loadCryptoAlpha()` when Binance/agent data is refreshed.

- [ ] **Step 5: Render alpha evidence panel**

Add renderer:

```javascript
function renderCryptoAlphaPanel() {
  const status = state.cryptoAlpha.status || {};
  const context = state.cryptoAlpha.context || {};
  const events = Array.isArray(context.events) ? context.events : [];
  const scorecards = Array.isArray(context.scorecards) ? context.scorecards : [];
  return `
    <section class="ops-panel crypto-alpha-panel">
      <div class="panel-header">
        <div>
          <h3>Crypto Alpha DB</h3>
          <p>무료 공개 촉매, 결과 라벨, 패턴 점수화</p>
        </div>
        <span class="status-chip ${status.status === "ok" ? "ok" : "warn"}">${escapeHtml(status.status || "unknown")}</span>
      </div>
      <div class="alpha-metrics">
        <span>events ${formatNumber(status.events || 0)}</span>
        <span>outcomes ${formatNumber(status.outcomes || 0)}</span>
        <span>hypotheses ${formatNumber(status.hypotheses || 0)}</span>
      </div>
      <div class="alpha-event-list">
        ${events.map((event) => `
          <article class="alpha-event">
            <strong>${escapeHtml(event.event_type || "event")}</strong>
            <span>${escapeHtml(event.title || "")}</span>
            <small>${escapeHtml((event.symbols || []).join(", "))}</small>
          </article>
        `).join("") || `<p class="muted">아직 압축된 알파 이벤트가 없습니다.</p>`}
      </div>
      <div class="alpha-scorecards">
        ${scorecards.map((card) => `<span class="evidence-chip">${escapeHtml(card.pattern_key || "")}</span>`).join("")}
      </div>
    </section>
  `;
}
```

Insert this panel in the Binance tab near market research and risk panels.

- [ ] **Step 6: Add CSS**

Add to `src/tradecraft/web/static/style.css`:

```css
.crypto-alpha-panel {
  display: grid;
  gap: 12px;
}

.alpha-metrics,
.alpha-scorecards {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.alpha-metrics span,
.evidence-chip {
  border: 1px solid var(--border-muted);
  border-radius: 8px;
  color: var(--text-muted);
  padding: 5px 8px;
}

.alpha-event-list {
  display: grid;
  gap: 8px;
}

.alpha-event {
  border: 1px solid var(--border-muted);
  border-radius: 8px;
  padding: 10px;
}

.alpha-event strong,
.alpha-event span,
.alpha-event small {
  display: block;
}
```

- [ ] **Step 7: Cache-bust static assets**

In `src/tradecraft/web/static/index.html`, increment the query version on `style.css` and `app.js`.

- [ ] **Step 8: Run frontend checks**

Run:

```bash
pytest tests/test_settings_api.py::test_settings_catalog_includes_crypto_alpha_options -q
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

---

## Task 11: Final Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
pytest tests/test_crypto_alpha.py tests/test_crypto_alpha_runner.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_config.py tests/test_settings_api.py tests/test_api_smoke.py -q
```

Expected: all pass.

- [ ] **Step 2: Run lint on touched Python files**

Run:

```bash
ruff check src/tradecraft/services/crypto_alpha.py src/tradecraft/runtime/crypto_alpha_runner.py src/tradecraft/config.py src/tradecraft/main.py src/tradecraft/runtime/binance_block_trader_runner.py src/tradecraft/services/binance_block_trader.py tests/test_crypto_alpha.py tests/test_crypto_alpha_runner.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_config.py tests/test_settings_api.py tests/test_api_smoke.py
```

Expected: no diagnostics.

- [ ] **Step 3: Run frontend syntax check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no syntax errors.

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check -- src/tradecraft/services/crypto_alpha.py src/tradecraft/runtime/crypto_alpha_runner.py src/tradecraft/config.py src/tradecraft/main.py src/tradecraft/runtime/binance_block_trader_runner.py src/tradecraft/services/binance_block_trader.py src/tradecraft/services/settings_catalog.py src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css src/tradecraft/web/static/index.html tests/test_crypto_alpha.py tests/test_crypto_alpha_runner.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_config.py tests/test_settings_api.py tests/test_api_smoke.py pyproject.toml .env.example
```

Expected: no whitespace errors.

- [ ] **Step 5: Runtime smoke after restart**

Run:

```bash
.venv/bin/tradecraft-crypto-alpha
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" http://127.0.0.1:18080/api/crypto/alpha/status
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" "http://127.0.0.1:18080/api/crypto/alpha/context?symbols=BTCUSDT,ETHUSDT&limit=6"
```

Expected:

- Runner writes `.runtime/crypto_alpha.json`.
- Status returns `status=ok` or `status=partial` with source errors recorded.
- Context returns bounded `events`, `similar_outcomes`, `scorecards`, `active_lessons`, and `data_gaps`.

---

## Self-Review

- **Spec coverage:** The plan covers free crawling, crypto-only DB isolation, outcome labeling, scorecards, context compression, Binance manager integration, API, UI, and runner.
- **Scope control:** The plan avoids heavy browser crawling, new third-party parser dependencies, live-order changes, and KIS integration.
- **Type consistency:** `CryptoAlphaConfig`, `CryptoAlphaService`, `context_pack()`, `collect_once()`, and `label_due_outcomes()` names are consistent across tasks.
- **Prompt safety:** The manager receives `crypto_alpha` as evidence and scorecard memory, while risk sizing and order gates remain outside the LLM.
