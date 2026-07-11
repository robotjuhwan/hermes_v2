from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.intelligence import sync_report_rag
from tradecraft.services.research_pipeline import (
    ResearchPipeline,
    ResearchPipelineConfig,
)


def test_research_pipeline_writes_snapshot_and_markdown(tmp_path: Path) -> None:
    state_path = tmp_path / "research.json"
    md_path = tmp_path / "strategy.md"
    cfg = ResearchPipelineConfig(
        state_path=str(state_path),
        strategy_md_path=str(md_path),
        market_scope="KRX",
        codex_command="",
        codex_query="국장 모멘텀",
        codex_timeout_sec=10,
        report_urls=[],
        max_items=10,
    )
    pipeline = ResearchPipeline(cfg)

    async def fake_codex() -> dict:
        return {
            "source": "codex_cli",
            "status": "ok",
            "title": "KRX signal",
            "summary": "005930 and 000660 are active",
            "picks": ["005930", "000660"],
        }

    async def fake_report(url: str) -> dict:
        _ = url
        return {
            "source": "report_crawl",
            "status": "ok",
            "title": "Broker report",
            "summary": "005930 target update",
            "url": "https://example.com/r1",
            "picks": ["005930"],
        }

    setattr(pipeline, "_collect_codex_item", fake_codex)
    setattr(pipeline, "_collect_report_item", fake_report)
    pipeline.config.report_urls = ["https://example.com/r1"]

    snapshot = asyncio.run(pipeline.run_once())
    assert snapshot["count"] == 2
    assert state_path.exists()
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "Watchlist Codes: 005930, 000660" in content
    assert "KRX signal" in content


def test_sync_report_rag_forwards_prune_missing_to_full_sync() -> None:
    class FakeRepository:
        def list_chunks_for_rag(self, limit: int) -> list[dict[str, Any]]:
            assert limit == 10
            return [
                {"report_id": 1, "chunk_index": 0, "content": "문서 1"},
                {"report_id": 2, "chunk_index": 0, "content": "문서 2"},
            ]

    class FakeRagStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def sync_documents(self, docs: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
            self.calls.append({"docs": docs, **kwargs})
            return {"status": "ok", "synced": len(docs), "deleted_orphans": 1}

    rag_store = FakeRagStore()

    result = sync_report_rag(
        repository=FakeRepository(),
        rag_store=rag_store,
        enabled=True,
        limit=10,
        prune_missing=True,
    )

    assert result == {"status": "ok", "synced": 2, "deleted_orphans": 1}
    assert rag_store.calls == [
        {
            "docs": [
                {"report_id": 1, "chunk_index": 0, "content": "문서 1"},
                {"report_id": 2, "chunk_index": 0, "content": "문서 2"},
            ],
            "force_update": False,
            "prune_missing": True,
        }
    ]


def test_sync_report_rag_can_limit_repository_chunks_by_updated_since() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def list_chunks_for_rag(
            self,
            limit: int,
            *,
            updated_since: str | None = None,
        ) -> list[dict[str, Any]]:
            self.calls.append({"limit": limit, "updated_since": updated_since})
            return [{"report_id": 3, "chunk_index": 0, "content": "새 문서"}]

    class FakeRagStore:
        def status(self) -> dict[str, Any]:
            return {"available": True, "count": 100}

        def sync_documents(self, docs: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok", "synced": len(docs), "input_docs": len(docs)}

    repository = FakeRepository()
    result = sync_report_rag(
        repository=repository,
        rag_store=FakeRagStore(),
        enabled=True,
        limit=50_000,
        updated_since="2026-06-30T10:00:00+00:00",
    )

    assert result == {"status": "ok", "synced": 1, "input_docs": 1}
    assert repository.calls == [
        {
            "limit": 50_000,
            "updated_since": "2026-06-30T10:00:00+00:00",
        }
    ]


def test_research_pipeline_persists_learning_total_count(tmp_path: Path) -> None:
    state_path = tmp_path / "research.json"
    cfg = ResearchPipelineConfig(
        state_path=str(state_path),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=10,
        report_urls=[],
    )
    pipeline = ResearchPipeline(cfg)

    async def fake_codex() -> dict:
        return {
            "source": "codex_cli",
            "status": "ok",
            "title": "KRX signal",
            "summary": "005930 momentum",
            "picks": ["005930"],
        }

    setattr(pipeline, "_collect_codex_item", fake_codex)

    snap1 = asyncio.run(pipeline.run_once())
    snap2 = asyncio.run(pipeline.run_once())

    assert snap1["learning_total_count"] == 1
    assert snap2["learning_total_count"] == 2

    persisted = RuntimeStateStore(state_path).read_snapshot()
    assert isinstance(persisted, dict)
    assert persisted.get("learning_total_count") == 2


def test_research_pipeline_embeds_market_intelligence_playbook(tmp_path: Path) -> None:
    md_path = tmp_path / "strategy.md"
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(md_path),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=10,
        report_urls=[],
        market_intelligence_sources=[
            {
                "source_id": "whale_insight",
                "label": "Whale Insight",
                "role": "고래 포지션 변동을 참고 신호로 추적",
                "coverage": ["KOSPI", "NASDAQ"],
                "signal_types": ["large_holder_change"],
                "caution": "자동 수집 오류 가능성",
            }
        ],
    )
    pipeline = ResearchPipeline(cfg)

    async def no_codex_item() -> None:
        return None

    setattr(pipeline, "_collect_codex_item", no_codex_item)

    snapshot = asyncio.run(pipeline.run_once())
    content = md_path.read_text(encoding="utf-8")

    assert snapshot["market_intelligence_sources"][0]["source_id"] == "whale_insight"
    assert any(
        str(item.get("source") or "") == "market_intelligence_source"
        for item in list(snapshot.get("items") or [])
    )
    assert "## Source Playbook" in content
    assert "Whale Insight" in content


def test_research_pipeline_dedupes_by_fingerprint(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=10,
        report_urls=[],
        max_items=10,
    )
    pipeline = ResearchPipeline(cfg)

    items = [
        {
            "source": "report_crawl",
            "title": "A",
            "url": "https://example.com/a",
            "summary": "same",
        },
        {
            "source": "report_crawl",
            "title": "A",
            "url": "https://example.com/a",
            "summary": "same",
        },
    ]
    deduped = pipeline._dedupe_items(items)
    assert len(deduped) == 1
    assert "fingerprint" in deduped[0]


def test_research_pipeline_limits_knowledge_markdown_size(tmp_path: Path) -> None:
    state_path = tmp_path / "research.json"
    md_path = tmp_path / "strategy.md"
    cfg = ResearchPipelineConfig(
        state_path=str(state_path),
        strategy_md_path=str(md_path),
        market_scope="KRX",
        codex_command="",
        codex_query="국장 모멘텀",
        codex_timeout_sec=10,
        report_urls=[],
        max_items=10,
        knowledge_max_chars=350,
    )
    pipeline = ResearchPipeline(cfg)

    async def fake_codex() -> dict:
        return {
            "source": "codex_cli",
            "status": "ok",
            "title": "KRX signal",
            "summary": "A" * 5000,
            "picks": ["005930", "000660"],
        }

    setattr(pipeline, "_collect_codex_item", fake_codex)

    _ = asyncio.run(pipeline.run_once())
    content = md_path.read_text(encoding="utf-8")
    assert len(content) <= 350
    assert "Compression Note" in content


def test_research_pipeline_uses_report_db_rows(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="반도체",
        codex_timeout_sec=10,
        report_urls=[],
        report_db_path="",
        report_db_top_k=3,
    )
    pipeline = ResearchPipeline(cfg)

    class _Repo:
        def search(self, query: str, symbol: str = "", limit: int = 10) -> list[dict]:
            _ = (query, symbol, limit)
            return [
                {
                    "title": "Semiconductor cycle update",
                    "snippet": "005930 demand recovery remains strong",
                    "detail_url": "https://example.com/detail",
                    "pdf_url": "",
                }
            ]

    setattr(pipeline, "report_repo", _Repo())

    async def no_codex_item() -> None:
        return None

    setattr(pipeline, "_collect_codex_item", no_codex_item)

    snapshot = asyncio.run(pipeline.run_once())
    assert snapshot["count"] >= 1
    assert any(
        str(item.get("source") or "") == "naver_report_db"
        for item in list(snapshot.get("items") or [])
    )


def test_research_pipeline_prefers_rag_rows_when_available(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="반도체",
        codex_timeout_sec=10,
        report_urls=[],
        rag_enabled=True,
        rag_query_top_k=2,
    )
    pipeline = ResearchPipeline(cfg)

    class _Rag:
        available = True

        def query(self, query: str, symbol: str = "", limit: int = 8) -> list[dict]:
            _ = (query, symbol, limit)
            return [
                {
                    "title": "RAG semiconductor insight",
                    "content": "005930 메모리 업사이클 시그널",
                    "detail_url": "https://example.com/rag",
                    "pdf_url": "",
                }
            ]

    class _Repo:
        def search(self, query: str, symbol: str = "", limit: int = 10) -> list[dict]:
            _ = (query, symbol, limit)
            return [
                {
                    "title": "DB fallback row",
                    "snippet": "000660",
                    "detail_url": "https://example.com/db",
                }
            ]

    setattr(pipeline, "rag_store", _Rag())
    setattr(pipeline, "report_repo", _Repo())

    async def no_codex_item() -> None:
        return None

    setattr(pipeline, "_collect_codex_item", no_codex_item)

    snapshot = asyncio.run(pipeline.run_once())
    assert snapshot["count"] >= 1
    assert any(
        str(item.get("source") or "") == "naver_report_rag"
        for item in list(snapshot.get("items") or [])
    )


def test_research_pipeline_accumulates_persistent_lessons(tmp_path: Path) -> None:
    md_path = tmp_path / "strategy.md"
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(md_path),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=10,
        report_urls=[],
        report_db_path="",
    )
    pipeline = ResearchPipeline(cfg)

    async def first_codex() -> dict:
        return {
            "source": "codex_cli",
            "status": "ok",
            "title": "Cycle one",
            "summary": "first lesson",
            "picks": ["005930"],
        }

    async def second_codex() -> dict:
        return {
            "source": "codex_cli",
            "status": "ok",
            "title": "Cycle two",
            "summary": "second lesson",
            "picks": ["000660"],
        }

    setattr(pipeline, "_collect_codex_item", first_codex)
    _ = asyncio.run(pipeline.run_once())

    setattr(pipeline, "_collect_codex_item", second_codex)
    _ = asyncio.run(pipeline.run_once())

    content = md_path.read_text(encoding="utf-8")
    assert "## Persistent Lessons" in content
    assert "first lesson" in content
    assert "second lesson" in content


def test_research_pipeline_ignores_non_ok_lessons(tmp_path: Path) -> None:
    md_path = tmp_path / "strategy.md"
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(md_path),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=10,
        report_urls=[],
        report_db_path="",
    )
    pipeline = ResearchPipeline(cfg)

    async def bad_codex() -> dict:
        return {
            "source": "codex_cli",
            "status": "error",
            "title": "timeout",
            "summary": "should not enter lessons",
            "picks": [],
        }

    setattr(pipeline, "_collect_codex_item", bad_codex)
    _ = asyncio.run(pipeline.run_once())

    content = md_path.read_text(encoding="utf-8")
    assert "Persistent Lessons" in content
    persistent_section = content.split("## Persistent Lessons", 1)[1]
    assert "should not enter lessons" not in persistent_section


def test_research_pipeline_adds_kis_block_feedback_lessons(tmp_path: Path) -> None:
    md_path = tmp_path / "strategy.md"
    block_db = tmp_path / "kis_blocks.db"
    with sqlite3.connect(block_db) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                status TEXT,
                qty_initial INTEGER,
                qty_open INTEGER,
                entry_price REAL,
                target_price REAL,
                stop_price REAL,
                thesis TEXT,
                risk_note TEXT,
                updated_at TEXT,
                closed_at TEXT
            );
            CREATE TABLE block_orders (
                id INTEGER PRIMARY KEY,
                block_id TEXT,
                symbol TEXT,
                side TEXT,
                qty INTEGER,
                limit_price INTEGER,
                status TEXT,
                reason TEXT,
                updated_at TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, name, status, qty_initial, qty_open,
                entry_price, target_price, stop_price, thesis, risk_note,
                updated_at, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "kis-1",
                "005930",
                "삼성전자",
                "open",
                2,
                2,
                70000,
                76000,
                67000,
                "earnings revision up",
                "gap risk",
                "2026-06-30T00:00:00+00:00",
                "",
            ),
        )
        conn.execute(
            """
            INSERT INTO block_orders (
                id, block_id, symbol, side, qty, limit_price, status, reason,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "kis-1",
                "005930",
                "buy",
                2,
                70100,
                "sent",
                "breakout + liquidity",
                "2026-06-30T00:01:00+00:00",
            ),
        )

    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(md_path),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=10,
        report_urls=[],
        kis_block_db_path=str(block_db),
    )
    pipeline = ResearchPipeline(cfg)

    async def codex_ok() -> dict:
        return {
            "source": "codex_cli",
            "status": "ok",
            "title": "Cycle one",
            "summary": "normal lesson",
            "picks": ["005930"],
        }

    setattr(pipeline, "_collect_codex_item", codex_ok)
    _ = asyncio.run(pipeline.run_once())

    content = md_path.read_text(encoding="utf-8")
    assert "KIS Block 삼성전자(005930) status=open" in content
    assert "KIS Order 005930 buy status=sent" in content


def test_codex_native_uses_codex_timeout_seconds(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=123,
        report_urls=[],
    )
    pipeline = ResearchPipeline(cfg)

    seen: dict[str, int] = {"timeout_ms": 0, "calls": 0}

    async def fake_complete(payload: dict, timeout_ms: int | None = None) -> dict:
        _ = payload
        seen["calls"] += 1
        seen["timeout_ms"] = int(timeout_ms or 0)
        return {
            "ok": True,
            "content": '{"query":"국장","summary":"반도체 모멘텀","picks":["005930"],"self_score_100":72}',
        }

    setattr(pipeline.codex_runtime, "complete", fake_complete)

    item = asyncio.run(pipeline._collect_codex_item_via_native())
    assert isinstance(item, dict)
    assert item["status"] == "ok"
    assert seen["calls"] == 1
    assert seen["timeout_ms"] == 123000


def test_collect_codex_item_skips_native_timeout_error(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=30,
        report_urls=[],
    )
    pipeline = ResearchPipeline(cfg)

    async def fake_native_item() -> dict:
        return {
            "source": "codex_cli",
            "status": "error",
            "title": "Codex research failed",
            "summary": "codex native runtime sdk timed out after 120.0s",
        }

    setattr(pipeline, "_collect_codex_item_via_native", fake_native_item)

    item = asyncio.run(pipeline._collect_codex_item())
    assert item is None


def test_codex_native_retries_once_after_first_failure(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=60,
        report_urls=[],
    )
    pipeline = ResearchPipeline(cfg)

    seen = {"calls": 0}

    async def fake_complete(payload: dict, timeout_ms: int | None = None) -> dict:
        _ = (payload, timeout_ms)
        seen["calls"] += 1
        if seen["calls"] == 1:
            return {
                "ok": False,
                "error": "codex native runtime sdk failed: codex native runtime 타임아웃",
            }
        return {
            "ok": True,
            "content": '{"query":"국장","summary":"반도체 강세","picks":["005930"],"self_score_100":65}',
        }

    setattr(pipeline.codex_runtime, "complete", fake_complete)

    item = asyncio.run(pipeline._collect_codex_item_via_native())
    assert isinstance(item, dict)
    assert item["status"] == "ok"
    assert item["title"].startswith("Codex KRX Research")
    assert seen["calls"] == 2
