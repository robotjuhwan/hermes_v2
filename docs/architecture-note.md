# RAG Integration Architecture Note

## 1) LLM (Codex) call path

- `tradecraft-research` entrypoint: `src/tradecraft/runtime/research_runner.py`
- Pipeline builder: `_build_pipeline(settings)` in `src/tradecraft/runtime/research_runner.py`
- Prompt assembly and bridge request:
  - `_collect_codex_item_via_bridge()` in `src/tradecraft/services/research_pipeline.py`
  - `_request_self_score_via_bridge()` in `src/tradecraft/services/research_pipeline.py`
- Shared LLM wrapper: `LLMBridge` in `src/tradecraft/services/llm_bridge.py`

## 2) Crawling output locations

- Reports DB: `.runtime/naver_reports.db`
- Archived PDFs: `.runtime/naver_reports/pdfs/`
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
- Workers:
  - `tradecraft-research` -> `src/tradecraft/runtime/research_runner.py`
  - `tradecraft-naver-reports` -> `src/tradecraft/runtime/naver_reports_runner.py`
  - `tradecraft-kis-trader` -> `src/tradecraft/runtime/kis_trader_runner.py`

## 5) Existing document processing/search code

- PDF ingest and parse:
  - `NaverSecuritiesCrawler._ingest_pdf()` in `src/tradecraft/services/naver_reports.py`
- Chunk export for RAG:
  - `NaverReportRepository.list_chunks_for_rag()` in `src/tradecraft/services/naver_reports.py`
- Vector sync/search:
  - `RAGStore.sync_documents()` and `RAGStore.query()` in `src/tradecraft/services/rag_store.py`
- API surfaces:
  - `/api/reports/search`, `/api/rag/search`, `/api/rag/sync` in `src/tradecraft/main.py`
