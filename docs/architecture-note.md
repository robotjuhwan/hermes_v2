# RAG Integration Architecture Note

## 1) LLM (Codex) call path

- Preferred unified entrypoint:
  - `tradecraft-intelligence` -> `src/tradecraft/runtime/intelligence_runner.py`
- Compatibility research entrypoint:
  - `tradecraft-research` -> `src/tradecraft/runtime/research_runner.py`
- Pipeline builder: `_build_pipeline(settings)` in `src/tradecraft/runtime/research_runner.py`
- Prompt assembly and bridge request:
  - `_collect_codex_item_via_bridge()` in `src/tradecraft/services/research_pipeline.py`
  - `_request_self_score_via_bridge()` in `src/tradecraft/services/research_pipeline.py`
- Shared LLM wrapper: `CodexNativeRuntime` in `src/tradecraft/services/codex_native.py`
- Report facts LLM enrichment is opt-in via `TRADECRAFT_NAVER_REPORTS_LLM_FACTS_ENABLED`; plain report collection/RAG can run without per-report LLM calls.

## 2) Crawling output locations

- Reports DB: `.runtime/naver_reports.db`
- Archived PDFs: `.runtime/naver_reports/pdfs/`
- Shared report intelligence assembly/cycle:
  - `build_report_intelligence_stack()` in `src/tradecraft/services/intelligence.py`
  - `run_report_collection_cycle()` in `src/tradecraft/services/intelligence.py`
- Crawler loop: `run()` in `src/tradecraft/runtime/naver_reports_runner.py`
- Single-cycle trigger: `POST /api/reports/crawl-once` in `src/tradecraft/main.py`

## 3) Storage model

- Settings and env loading: `AppSettings` in `src/tradecraft/config.py`
- Primary storage:
  - SQLite reports/chunks/facts: `NaverReportRepository` in `src/tradecraft/services/naver_reports.py`
  - Runtime snapshots: `RuntimeStateStore` in `src/tradecraft/runtime/state_store.py`
  - Optional vector store: `RAGStore` in `src/tradecraft/services/rag_store.py`

## 4) API / worker entrypoints

- Web API: `src/tradecraft/main.py`
- Reports API/worker share the same report intelligence cycle through
  `src/tradecraft/services/intelligence.py`.
- Workers:
  - `tradecraft-intelligence` -> `src/tradecraft/runtime/intelligence_runner.py`
  - `tradecraft-research` -> `src/tradecraft/runtime/research_runner.py`
  - `tradecraft-naver-reports` -> `src/tradecraft/runtime/naver_reports_runner.py`
  - `tradecraft-kis-block-trader` -> `src/tradecraft/runtime/kis_block_trader_runner.py`

## 5) Existing document processing/search code

- PDF ingest and parse:
  - `NaverSecuritiesCrawler._ingest_pdf()` in `src/tradecraft/services/naver_reports.py`
- Chunk export for RAG:
  - `NaverReportRepository.list_chunks_for_rag()` in `src/tradecraft/services/naver_reports.py`
- Vector sync/search:
  - `RAGStore.sync_documents()` and `RAGStore.query()` in `src/tradecraft/services/rag_store.py`
  - `TRADECRAFT_RAG_SYNC_BATCH_SIZE` keeps large report syncs in bounded ChromaDB upsert batches.
  - `TRADECRAFT_RAG_SKIP_EXISTING` skips already indexed vector ids by default, keeping periodic syncs incremental.
- API surfaces:
  - `/api/reports/search`, `/api/rag/search`, `/api/rag/sync` in `src/tradecraft/main.py`

## 6) Market intelligence source playbook

- `TRADECRAFT_MARKET_INTELLIGENCE_SOURCES_JSON` defines curated reference
  sources used by research/advice prompts.
- Defaults include:
  - Whale Insight-style holder/portfolio signals: whale positions, 5% holder
    changes, and legend portfolio tracking.
  - After-close 3:30-style briefs: flow, sector map, and closing candidate
    signals.
- These are reference sources, not live facts. The pipeline instructs the LLM
  not to claim actual whale/flow values unless concrete rows are present in the
  current research/report context.
