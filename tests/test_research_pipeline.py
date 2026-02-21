from __future__ import annotations

import asyncio
from pathlib import Path

from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
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


def test_research_pipeline_adds_trader_feedback_lessons(tmp_path: Path) -> None:
    md_path = tmp_path / "strategy.md"
    trader_state = tmp_path / "kis_trader.json"
    RuntimeStateStore(trader_state).write_snapshot(
        {
            "updated_at": utc_now_iso(),
            "decisions": [
                {
                    "symbol": "005930",
                    "side": "buy",
                    "confidence": 0.81,
                    "reason": "earnings revision up",
                }
            ],
            "orders": [
                {
                    "status": "sent",
                    "symbol": "005930",
                    "side": "buy",
                    "qty": 2,
                    "reason": "breakout + liquidity",
                }
            ],
        }
    )

    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(md_path),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=10,
        report_urls=[],
        trader_state_path=str(trader_state),
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
    assert "Decision 005930 buy" in content
    assert "Executed 005930 buy" in content


def test_codex_bridge_uses_codex_timeout_seconds(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=123,
        report_urls=[],
        llm_bridge_command="dummy",
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

    setattr(pipeline.llm_bridge, "complete", fake_complete)

    item = asyncio.run(pipeline._collect_codex_item_via_bridge())
    assert isinstance(item, dict)
    assert item["status"] == "ok"
    assert seen["calls"] == 1
    assert seen["timeout_ms"] == 123000


def test_collect_codex_item_skips_bridge_timeout_error(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=30,
        report_urls=[],
        llm_bridge_command="dummy",
    )
    pipeline = ResearchPipeline(cfg)

    async def fake_bridge_item() -> dict:
        return {
            "source": "codex_cli",
            "status": "error",
            "title": "Codex research failed",
            "summary": "llm bridge command timed out after 120.0s",
        }

    setattr(pipeline, "_collect_codex_item_via_bridge", fake_bridge_item)

    item = asyncio.run(pipeline._collect_codex_item())
    assert item is None


def test_codex_bridge_retries_once_after_first_failure(tmp_path: Path) -> None:
    cfg = ResearchPipelineConfig(
        state_path=str(tmp_path / "research.json"),
        strategy_md_path=str(tmp_path / "strategy.md"),
        market_scope="KRX",
        codex_command="",
        codex_query="국장",
        codex_timeout_sec=60,
        report_urls=[],
        llm_bridge_command="dummy",
    )
    pipeline = ResearchPipeline(cfg)

    seen = {"calls": 0}

    async def fake_complete(payload: dict, timeout_ms: int | None = None) -> dict:
        _ = (payload, timeout_ms)
        seen["calls"] += 1
        if seen["calls"] == 1:
            return {
                "ok": False,
                "error": "llm bridge command failed (code=1): codex 브릿지 타임아웃",
            }
        return {
            "ok": True,
            "content": '{"query":"국장","summary":"반도체 강세","picks":["005930"],"self_score_100":65}',
        }

    setattr(pipeline.llm_bridge, "complete", fake_complete)

    item = asyncio.run(pipeline._collect_codex_item_via_bridge())
    assert isinstance(item, dict)
    assert item["status"] == "ok"
    assert item["title"].startswith("Codex KRX Research")
    assert seen["calls"] == 2
