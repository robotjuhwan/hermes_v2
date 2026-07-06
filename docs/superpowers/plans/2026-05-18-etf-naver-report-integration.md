# ETF Naver Report Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ETFs first-class report/RAG instruments so Naver reports that discuss ETFs are linked to ETF symbols and used by Jue's strategy/block decisions without duplicating a separate ETF text-research silo.

**Architecture:** Keep `reports.symbol` as the single primary company symbol for one-company reports, but add a many-to-many `report_symbol_links` layer for ETFs, multi-symbol strategy reports, and future basket/theme links. Keep `.runtime/etf_research.db` only for ETF market snapshots, liquidity, momentum, and allocation scoring; textual knowledge comes from `naver_reports.db` and RAG. Strategy intelligence merges report/RAG links plus ETF snapshots into one candidate object.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, existing `NaverReportRepository`, existing RAG sync, existing `ETFResearchRepository`, existing static frontend.

---

## Scope

This plan fixes the current gap where ETF-related Naver reports are collected but not mapped to ETF codes.

Confirmed current behavior:
- `.runtime/naver_reports.db` has many ETF keyword reports.
- Core ETF symbols such as `069500`, `102110`, `091160` have zero linked reports.
- `src/tradecraft/services/naver_reports.py` clears `symbol/company_name` for non-`company_analysis` reports.
- `src/tradecraft/services/strategy_intelligence.py` currently gets ETF candidates mainly from `etf_research.db`, so ETF text knowledge and ETF market snapshots are disconnected.

Out of scope:
- No order execution logic changes.
- No new ETF-only text crawler.
- No company PER/PBR valuation logic for ETFs.

## File Structure

- Modify `src/tradecraft/services/naver_reports.py`
  - Add `report_symbol_links` table.
  - Add symbol-link extraction, save, search, status, and backfill methods.
  - Seed configured ETF universe into `symbol_directory`.
- Modify `src/tradecraft/services/intelligence.py`
  - Ensure report collection cycle refreshes symbol links and RAG metadata after collection.
- Modify `src/tradecraft/main.py`
  - Wire ETF universe seeding into Naver report repository.
  - Add protected symbol-link backfill/status API if needed.
- Modify `src/tradecraft/services/strategy_intelligence.py`
  - Merge ETF report/RAG evidence with ETF snapshot/score candidates.
- Modify `src/tradecraft/web/static/app.js`
  - Show ETF report-link coverage in research/status areas.
- Modify `src/tradecraft/web/static/index.html`
  - Bump cache-busting version.
- Test `tests/test_naver_reports.py`
  - Repository schema, ETF seed, extraction, search, backfill.
- Test `tests/test_strategy_intelligence.py`
  - ETF candidates use report evidence and snapshot evidence together.
- Test `tests/test_api_smoke.py` or create `tests/test_etf_report_links_api.py`
  - API status/backfill smoke.

## Task 1: Add Report Symbol Link Repository Layer

**Files:**
- Modify: `src/tradecraft/services/naver_reports.py`
- Test: `tests/test_naver_reports.py`

- [ ] **Step 1: Write failing repository tests**

Add tests for a report that links to multiple symbols without overwriting `reports.symbol`.

```python
def test_report_symbol_links_store_etf_mentions(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    report_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=1",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=1",
        pdf_url="https://example.com/etf.pdf",
        pdf_sha256="etfhash",
        pdf_archived_path="",
        title="ETF 전략: KODEX 200과 TIGER 200 점검",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500), TIGER 200(102110)을 중심으로 코어 ETF 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )
    repo.upsert_report_symbol_links(
        report_id,
        [
            {
                "symbol": "069500",
                "name": "KODEX 200",
                "asset_class": "etf",
                "link_type": "mention",
                "source": "text_extract",
                "confidence": 0.95,
                "evidence": "KODEX 200(069500)",
            },
            {
                "symbol": "102110",
                "name": "TIGER 200",
                "asset_class": "etf",
                "link_type": "mention",
                "source": "text_extract",
                "confidence": 0.95,
                "evidence": "TIGER 200(102110)",
            },
        ],
    )

    links = repo.list_report_symbol_links(report_id)
    assert [item["symbol"] for item in links] == ["069500", "102110"]
    assert repo.search(query="", symbol="069500", limit=5)[0]["report_id"] == report_id
```

Run:

```bash
pytest tests/test_naver_reports.py::test_report_symbol_links_store_etf_mentions -q
```

Expected: fail because the table and methods do not exist.

- [ ] **Step 2: Implement table and repository methods**

Add this table in the repository schema:

```sql
CREATE TABLE IF NOT EXISTS report_symbol_links (
    report_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    asset_class TEXT NOT NULL DEFAULT 'stock',
    link_type TEXT NOT NULL DEFAULT 'mention',
    source TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL DEFAULT 0.0,
    evidence TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (report_id, symbol, link_type),
    FOREIGN KEY (report_id) REFERENCES reports(report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_report_symbol_links_symbol
    ON report_symbol_links(symbol, asset_class, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_report_symbol_links_report
    ON report_symbol_links(report_id);
```

Add methods:

```python
def upsert_report_symbol_links(
    self,
    report_id: int,
    links: list[dict[str, Any]],
) -> int: ...

def list_report_symbol_links(self, report_id: int) -> list[dict[str, Any]]: ...

def latest_symbol_linked_reports(
    self,
    symbol: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]: ...
```

Modify `search(symbol=...)` so it returns reports where either:
- `reports.symbol = symbol`
- or an entry exists in `report_symbol_links`.

Run:

```bash
pytest tests/test_naver_reports.py::test_report_symbol_links_store_etf_mentions -q
```

Expected: pass.

## Task 2: Seed ETF Universe Into the Existing Symbol Directory

**Files:**
- Modify: `src/tradecraft/services/naver_reports.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_naver_reports.py`

- [ ] **Step 1: Write failing seed tests**

```python
def test_seed_etf_universe_into_symbol_directory(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))

    updated = repo.seed_symbol_directory(
        [
            {"symbol": "069500", "name": "KODEX 200", "market": "ETF", "source": "configured_etf"},
            {"symbol": "102110", "name": "TIGER 200", "market": "ETF", "source": "configured_etf"},
        ]
    )

    assert updated == 2
    assert repo.resolve_symbol_names(["069500", "102110"]) == {
        "069500": "KODEX 200",
        "102110": "TIGER 200",
    }
```

Run:

```bash
pytest tests/test_naver_reports.py::test_seed_etf_universe_into_symbol_directory -q
```

Expected: fail until `seed_symbol_directory` exists.

- [ ] **Step 2: Implement seed method**

Add a generic method that accepts configured instruments and calls existing `_upsert_symbol_directory_with_conn`.

Rules:
- Accept only six-digit symbols.
- Use `market="ETF"` for ETF universe items.
- Use `source="configured_etf"` with confidence `1.0`.
- Never overwrite a cleaner `pykrx` ETF name with a blank or generic name.

Wire this from `main.py` wherever `_seed_etf_research_universe()` runs, so the ETF universe is visible to Naver report matching too.

Run:

```bash
pytest tests/test_naver_reports.py::test_seed_etf_universe_into_symbol_directory -q
```

Expected: pass.

## Task 3: Extract ETF Links From Non-Company Reports

**Files:**
- Modify: `src/tradecraft/services/naver_reports.py`
- Test: `tests/test_naver_reports.py`

- [ ] **Step 1: Write failing extraction tests**

Add tests for:
- explicit code and name: `KODEX 200(069500)`
- name-only title mention: `TIGER 200 ETF`
- generic `ETF` keyword without a known symbol should not create a fake link.

```python
def test_extract_report_symbol_links_for_etfs() -> None:
    symbol_names = {
        "069500": "KODEX 200",
        "102110": "TIGER 200",
        "091160": "KODEX 반도체",
    }

    links = _extract_report_symbol_links(
        "ETF 전략: KODEX 200(069500), TIGER 200 ETF. 단순 ETF 단어는 코드가 아니다.",
        symbol_names=symbol_names,
        asset_class_by_symbol={code: "etf" for code in symbol_names},
        published_at="2026-05-18",
    )

    assert [item["symbol"] for item in links] == ["069500", "102110"]
    assert links[0]["confidence"] >= 0.9
    assert all(item["asset_class"] == "etf" for item in links)
```

Run:

```bash
pytest tests/test_naver_reports.py::test_extract_report_symbol_links_for_etfs -q
```

Expected: fail until helper exists.

- [ ] **Step 2: Implement extraction helper**

Add `_extract_report_symbol_links(...)`.

Scoring rules:
- `name + code` near each other: confidence `0.95`
- known six-digit ETF code only: confidence `0.85`
- exact known ETF name in title/first 1,500 chars: confidence `0.75`
- exact known ETF name in body: confidence `0.55`
- generic words such as `ETF`, `펀드`, `투자유망`, `정보` never become names.
- Limit links per report to `20`.

Normalize aliases:
- remove duplicate spaces
- allow `KODEX200` to match `KODEX 200`
- allow uppercase/lowercase English differences

Run:

```bash
pytest tests/test_naver_reports.py::test_extract_report_symbol_links_for_etfs -q
```

Expected: pass.

## Task 4: Attach Links During Crawl and Backfill Existing Reports

**Files:**
- Modify: `src/tradecraft/services/naver_reports.py`
- Modify: `src/tradecraft/services/intelligence.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_naver_reports.py`

- [ ] **Step 1: Write failing upsert/backfill tests**

```python
def test_upsert_report_auto_links_etf_mentions(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [{"symbol": "069500", "name": "KODEX 200", "market": "ETF", "source": "configured_etf"}]
    )

    report_id = repo.upsert_report(
        category="invest_info",
        source_url="https://finance.naver.com/research/invest_read.naver?nid=10",
        detail_url="https://finance.naver.com/research/invest_read.naver?nid=10",
        pdf_url="https://example.com/kodex200.pdf",
        pdf_sha256="kodexhash",
        pdf_archived_path="",
        title="ETF 전략",
        company_name="",
        broker="테스트증권",
        analyst="",
        symbol="",
        published_at="2026-05-18",
        crawled_at="2026-05-18T00:00:00+00:00",
        content_source="pdf_extract",
        content="KODEX 200(069500) 비중을 점검한다.",
        chunk_size=200,
        max_chunks_per_report=10,
    )

    links = repo.list_report_symbol_links(report_id)
    assert links[0]["symbol"] == "069500"
    assert repo.search(query="", symbol="069500", limit=5)[0]["report_id"] == report_id
```

Run:

```bash
pytest tests/test_naver_reports.py::test_upsert_report_auto_links_etf_mentions -q
```

Expected: fail until `upsert_report` auto-linking is implemented.

- [ ] **Step 2: Implement auto-linking**

In `upsert_report`:
- Keep current behavior for `reports.symbol`.
- For all categories, build candidate symbol map from `symbol_directory`.
- Extract links from `normalized_title + content head + content tail`.
- Save links after insert/update when `report_id` is known.
- For `company_analysis`, also add a `link_type="primary"` link for `reports.symbol`.

Add `backfill_report_symbol_links(limit: int = 0, asset_class: str = "etf")`.

Backfill scope:
- reports with `title/content LIKE '%ETF%'`
- reports from `invest_info`, `market_info`, `industry_analysis`
- latest first
- idempotent updates

Add a protected API:
- `POST /api/reports/backfill-symbol-links`

Run:

```bash
pytest tests/test_naver_reports.py::test_upsert_report_auto_links_etf_mentions -q
pytest tests/test_api_smoke.py -q
```

Expected: pass.

## Task 5: Include Linked ETF Reports in RAG Metadata

**Files:**
- Modify: `src/tradecraft/services/intelligence.py`
- Modify: `src/tradecraft/services/naver_reports.py`
- Test: `tests/test_naver_reports.py`

- [ ] **Step 1: Write failing RAG document metadata test**

Add a test around the existing report-to-RAG document builder proving ETF links appear in document metadata:

```python
def test_rag_documents_include_linked_etf_symbols(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [{"symbol": "069500", "name": "KODEX 200", "market": "ETF", "source": "configured_etf"}]
    )
    report_id = repo.upsert_report(... content="KODEX 200(069500) 비중 점검" ...)

    docs = repo.recent_documents_for_rag(limit=10)

    doc = next(item for item in docs if item["report_id"] == report_id)
    assert "069500" in doc["metadata"]["linked_symbols"]
    assert "etf" in doc["metadata"]["linked_asset_classes"]
```

Use the actual existing RAG document method name in the implementation pass.

Run:

```bash
pytest tests/test_naver_reports.py -k "rag_documents_include_linked_etf_symbols" -q
```

Expected: fail until metadata joins links.

- [ ] **Step 2: Join link metadata into RAG docs**

When building RAG documents:
- include `linked_symbols`
- include `linked_names`
- include `linked_asset_classes`
- keep existing `symbol` metadata for backward compatibility

Run:

```bash
pytest tests/test_naver_reports.py -k "rag_documents_include_linked_etf_symbols" -q
```

Expected: pass.

## Task 6: Merge ETF Report Evidence Into Strategy Intelligence

**Files:**
- Modify: `src/tradecraft/services/strategy_intelligence.py`
- Test: `tests/test_strategy_intelligence.py`

- [ ] **Step 1: Write failing strategy tests**

Test that an ETF can become a candidate from linked Naver reports even before ETF snapshot data exists.

```python
def test_etf_candidate_uses_naver_report_links_without_snapshot() -> None:
    repository = FakeReportRepositoryWithLinkedReports(
        symbol="069500",
        name="KODEX 200",
        reports=[
            {
                "symbol": "069500",
                "company_name": "KODEX 200",
                "title": "ETF 전략: 코어 ETF 비중 점검",
                "category": "invest_info",
                "published_at": "2026-05-18",
                "content": "KODEX 200(069500) 비중 점검",
                "link_confidence": 0.95,
                "asset_class": "etf",
            }
        ],
    )

    service = StrategyIntelligenceService(repository=repository, etf_research_repository=FakeEmptyETFRepo())
    brief = service.build_brief(query="ETF도 검토해줘")
    candidate = next(item for item in brief["candidates"] if item["symbol"] == "069500")

    assert candidate["asset_class"] == "etf"
    assert "naver_reports" in candidate["sources"]
    assert candidate["data_coverage"]["has_report"] is True
    assert candidate["data_coverage"]["has_etf_research"] is False
```

Run:

```bash
pytest tests/test_strategy_intelligence.py -k "etf_candidate_uses_naver_report_links_without_snapshot" -q
```

Expected: fail until strategy intelligence reads linked reports.

- [ ] **Step 2: Implement merge behavior**

Modify report candidate collection so linked-symbol reports can create/update candidates.

Rules:
- If `asset_class="etf"`, set:

```python
{
    "asset_class": "etf",
    "horizon_bias": "core_etf",
    "valuation": {"status": "not_applicable", "label": "etf"},
}
```

- Merge ETF snapshot/score if available.
- If only report evidence exists, candidate is allowed but `data_coverage.has_etf_research=False`.
- Do not add `밸류 미수집` warnings to ETFs.
- ETF report evidence should raise evidence/recency, not company valuation/quality/growth.

Run:

```bash
pytest tests/test_strategy_intelligence.py -q
```

Expected: pass.

## Task 7: Status/UI Surfacing

**Files:**
- Modify: `src/tradecraft/services/naver_reports.py`
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/index.html`

- [ ] **Step 1: Add repository status counts**

Extend report status with:
- `symbol_link_count`
- `etf_link_count`
- `linked_report_count`
- `unlinked_etf_keyword_report_count`
- `last_symbol_link_updated_at`

Run:

```bash
pytest tests/test_api_smoke.py::test_reports_status -q
```

Expected: update expected payload only if the test asserts exact keys.

- [ ] **Step 2: Add UI indication**

In the investment/research status area, show:
- ETF-linked report count
- ETF keyword reports still unlinked
- last link backfill time

Bump static asset version in `index.html`.

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

## Task 8: Backfill Runtime DB and Verify

**Files:**
- Runtime DB only: `.runtime/naver_reports.db`

- [ ] **Step 1: Restart report/control services after code change**

Use existing tmux/service conventions in this repo. Do not edit secrets.

- [ ] **Step 2: Seed ETF symbols**

Call the report status path or ETF status path that seeds configured ETFs, then verify:

```bash
sqlite3 -header -column .runtime/naver_reports.db \
"SELECT symbol, company_name, market, source FROM symbol_directory WHERE symbol IN ('069500','102110','091160');"
```

Expected: three ETF rows with names.

- [ ] **Step 3: Backfill symbol links**

Call:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" \
  http://127.0.0.1:18080/api/reports/backfill-symbol-links
```

Expected: response includes positive `updated_reports` or `linked_symbols`.

- [ ] **Step 4: Verify ETF reports are linked**

```bash
sqlite3 -header -column .runtime/naver_reports.db \
"SELECT symbol, name, asset_class, COUNT(*) AS reports
 FROM report_symbol_links
 WHERE asset_class='etf'
 GROUP BY symbol, name, asset_class
 ORDER BY reports DESC
 LIMIT 20;"
```

Expected: ETF codes appear with linked report counts.

- [ ] **Step 5: Verify strategy brief**

Call `/api/strategy/brief` and confirm:
- ETF candidates can appear from Naver report evidence.
- ETF candidates also merge market snapshot if `.runtime/etf_research.db` has data.
- candidate `sources` can contain both `naver_reports` and `etf_research`.

## Acceptance Criteria

- ETF reports already collected in `naver_reports.db` become symbol-linked without a new ETF text crawler.
- `reports.symbol` remains backward-compatible.
- ETF candidates can be backed by:
  - Naver report/RAG evidence
  - ETF quote/liquidity/momentum snapshots
  - both together when available
- ETF-specific market snapshot DB remains useful but no longer owns ETF textual knowledge.
- Strategy intelligence does not treat ETF missing PER/PBR as a defect.
- UI exposes whether ETF report-linking is healthy.

## Verification Commands

Run focused tests first:

```bash
pytest tests/test_naver_reports.py -k "etf or symbol_link" -q
pytest tests/test_strategy_intelligence.py -k "etf" -q
```

Run broader regression:

```bash
pytest tests/test_naver_reports.py tests/test_strategy_intelligence.py tests/test_api_smoke.py -q
node --check src/tradecraft/web/static/app.js
git diff --check
```

## Implementation Notes

- Prefer additive schema migration. Do not rewrite existing `reports` rows destructively.
- Keep old RAG metadata fields while adding linked metadata.
- Avoid fuzzy matching broad words like `ETF`, `펀드`, `주식`, `정보`.
- Use exact configured ETF names/codes first; pykrx ETF refresh is a bonus source, not a blocker.
- The previous `2026-05-14-jue-etf-research-layer.md` plan remains valid for ETF market snapshots, but this plan supersedes it for ETF textual research ownership.
