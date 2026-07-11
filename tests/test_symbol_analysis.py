from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.symbol_analysis import SymbolAnalysisService


class _FakeLLM:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "summary": "피노는 급락 후 단기 블록만 가능한 고변동 종목이다.",
                    "stance": "risk_check",
                    "confidence": 0.74,
                    "short_view": "단기 반등 확인",
                    "mid_view": "재무 공백 보강 필요",
                    "long_view": "중장기 확신 부족",
                    "reasons": ["당일 저점 방어"],
                    "risks": ["EPS 음수", "PBR 부담"],
                    "data_gaps": ["리포트 없음"],
                    "triggers": ["13800원 회복"],
                    "target_candidates": [13800],
                    "stop_candidates": [13340],
                },
                ensure_ascii=False,
            ),
        }


class _LeaseBusyThenOkLLM(_FakeLLM):
    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(payload)
        if len(self.calls) == 1:
            raise RuntimeError(
                "codex native thread lease unavailable: symbol_analysis:033790:2026-06-30"
            )
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "summary": "피노는 lease 대기 후 정상 분석됐다.",
                    "stance": "risk_check",
                    "confidence": 0.71,
                    "short_view": "단기 변동성 점검",
                    "mid_view": "중기 확인",
                    "long_view": "장기 보류",
                    "reasons": ["동시 분석이 끝난 뒤 재시도 성공"],
                    "risks": ["동시 실행 지연"],
                    "data_gaps": [],
                    "triggers": ["다음 가격 확인"],
                    "target_candidates": [13800],
                    "stop_candidates": [13340],
                },
                ensure_ascii=False,
            ),
        }


class _LeaseBusyPayloadThenOkLLM(_FakeLLM):
    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(payload)
        if len(self.calls) == 1:
            return {
                "ok": False,
                "error": (
                    "codex native thread lease unavailable: "
                    "symbol_analysis:033790:2026-07-02"
                ),
            }
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "summary": "피노는 payload lease 대기 후 정상 분석됐다.",
                    "stance": "watch",
                    "confidence": 0.7,
                    "short_view": "단기 확인",
                    "mid_view": "중기 관찰",
                    "long_view": "장기 보류",
                    "reasons": ["ok:false lease 후 재시도 성공"],
                    "risks": [],
                    "data_gaps": [],
                    "triggers": ["다음 가격 확인"],
                    "target_candidates": [13800],
                    "stop_candidates": [13340],
                },
                ensure_ascii=False,
            ),
        }


class _FakeFundamentals:
    def __init__(self) -> None:
        self.collected: list[tuple[list[str], bool]] = []

    async def collect_symbols(
        self,
        symbols: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        self.collected.append((symbols, force))
        return {"status": "ok", "items": [{"symbol": symbols[0], "status": "ok"}]}

    def latest(self, symbol: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "symbol": symbol,
            "name": "피노",
            "valuation": {"pbr": 9.19, "eps": -135},
            "score": {"risks": ["PBR 9.19배 부담", "EPS가 0 이하"]},
        }


class _VeryHeavyFundamentals(_FakeFundamentals):
    async def collect_symbols(
        self,
        symbols: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        self.collected.append((symbols, force))
        return {
            "status": "ok",
            "items": [{"symbol": symbols[0], "status": "ok"}],
            "raw_json": "RAW_FUNDAMENTALS_CONTEXT " * 20_000,
            "wide_metrics": {f"metric_{idx}": idx for idx in range(5_000)},
        }

    def latest(self, symbol: str) -> dict[str, Any]:
        payload = super().latest(symbol)
        payload["raw_json"] = "RAW_LATEST_FUNDAMENTALS_CONTEXT " * 20_000
        payload["wide_metrics"] = {f"latest_metric_{idx}": idx for idx in range(5_000)}
        return payload


class _FakeQuoteProvider:
    async def fetch_quote(self, symbol: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "symbol": symbol,
            "name": "피노",
            "price": 13660,
            "change_pct": -8.32,
        }


class _FakeReports:
    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        return {symbols[0]: "피노"}

    def search(
        self,
        query: str,
        symbol: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if query == "피노":
            return [{"symbol": "033790", "company_name": "피노", "title": "피노"}]
        return []


class _HeavyReports(_FakeReports):
    def search(
        self,
        query: str,
        symbol: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if query == "피노":
            return super().search(query, symbol=symbol, limit=limit)
        blob = "LONG_REPORT_CONTEXT " * 1000
        return [
            {
                "symbol": "033790",
                "company_name": "피노",
                "title": f"피노 리포트 {idx}",
                "summary": blob,
                "text": blob,
            }
            for idx in range(12)
        ]


class _FakeDirectoryReports(_FakeReports):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE symbol_directory (
                    symbol TEXT,
                    company_name TEXT,
                    confidence REAL,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO symbol_directory(symbol, company_name, confidence, updated_at)
                VALUES('033790', '피노', 1.0, '2026-05-19T00:00:00+00:00')
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        if not symbols:
            return {}
        return super().resolve_symbol_names(symbols)

    def search(
        self,
        query: str,
        symbol: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if symbol == "033790":
            return [{"symbol": "033790", "company_name": "피노", "title": "피노"}]
        return []


class _FakeRAG:
    def query(
        self,
        text: str,
        *,
        symbol: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return []


class _HeavyRAG:
    def query(
        self,
        text: str,
        *,
        symbol: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        blob = "LONG_RAG_CONTEXT " * 1000
        return [
            {
                "symbol": symbol,
                "title": f"RAG {idx}",
                "summary": blob,
                "text": blob,
            }
            for idx in range(12)
        ]


class _FakeBlocks:
    def blocks(self) -> dict[str, Any]:
        return {
            "blocks": [
                {
                    "block_id": "blk_033790_1",
                    "symbol": "033790",
                    "name": "피노",
                    "status": "closed",
                    "created_by": "existing_position",
                    "entry_price": 13660,
                    "target_price": 13800,
                    "stop_price": 13340,
                    "metadata": {"adopted_from_account": True},
                }
            ]
        }


class _VeryHeavyBlocks:
    def blocks(self) -> dict[str, Any]:
        return {
            "blocks": [
                {
                    "block_id": f"blk_033790_{idx}",
                    "symbol": "033790",
                    "name": "피노",
                    "status": "closed",
                    "entry_price": 13660 + idx,
                    "target_price": 14000 + idx,
                    "stop_price": 13000 - idx,
                    "metadata": {
                        "raw_json": "RAW_BLOCK_CONTEXT " * 2_000,
                        "wide_metrics": {f"block_metric_{sub}": sub for sub in range(600)},
                    },
                }
                for idx in range(80)
            ]
        }


def test_run_instant_analysis_collects_fundamentals_and_persists(
    tmp_path: Path,
) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _FakeLLM()
    fundamentals = _FakeFundamentals()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=fundamentals,
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeReports(),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
    )

    result = asyncio.run(
        service.run("033790", trigger="user_request", force_collect=True)
    )

    assert result["status"] == "ok"
    assert result["symbol"] == "033790"
    assert result["analysis"]["summary"].startswith("피노는 급락")
    assert fundamentals.collected == [(["033790"], True)]
    assert llm.calls
    assert "fundamentals" in llm.calls[0]["context"]
    assert llm.calls[0]["language_policy"]["internal_reasoning_language"] == "en-US"
    assert llm.calls[0]["language_policy"]["operator_display_language"] == "ko-KR"
    assert llm.calls[0]["language_policy"]["user_visible_generation_order"] == (
        "draft_conclusion_in_english_then_translate_to_korean_for_display"
    )
    history = memory.repository.list_symbol_analyses("033790")
    assert history["count"] == 1
    assert history["items"][0]["stance"] == "risk_check"
    assert (tmp_path / "memory" / "symbols" / "033790.md").exists()
    assert result["lifecycle_artifact"]["status"] == "ok"
    artifacts = memory.lifecycle_repository.list_artifacts(
        symbols=["033790"],
        workflow_id="instant_symbol_analysis",
        limit=5,
    )
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_type"] == "symbol_analysis"
    assert artifacts[0]["symbol"] == "033790"
    assert artifacts[0]["evidence"]
    assert artifacts[0]["payload"]["block_implications"][0]["action"] == "risk_check"


def test_symbol_analysis_prompt_compacts_context_without_duplicate_payload(
    tmp_path: Path,
) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _FakeLLM()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_HeavyReports(),
        rag_store=_HeavyRAG(),
        block_provider=_FakeBlocks(),
    )

    result = asyncio.run(service.run("033790", trigger="user_request"))

    assert result["status"] == "ok"
    prompt = llm.calls[0]
    prompt_text = json.dumps(prompt, ensure_ascii=False)
    assert len(prompt_text) < 45_000
    assert "LONG_REPORT_CONTEXT" not in prompt_text
    assert "LONG_RAG_CONTEXT" not in prompt_text
    user_payload = json.loads(prompt["messages"][1]["content"])
    assert user_payload["context"] == prompt["context"]


def test_symbol_analysis_prompt_has_hard_budget_for_extreme_payloads(
    tmp_path: Path,
) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    memory.repository.save_symbol_analysis(
        {
            "symbol": "033790",
            "name": "피노",
            "summary": "OLD_SYMBOL_ANALYSIS_CONTEXT " * 10_000,
            "stance": "watch",
            "prompt": {"raw_json": "OLD_PROMPT_CONTEXT " * 10_000},
            "raw_response": {"content": "OLD_RESPONSE_CONTEXT " * 10_000},
            "snapshot": {"raw_json": "OLD_SNAPSHOT_CONTEXT " * 10_000},
        }
    )
    llm = _FakeLLM()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_VeryHeavyFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_HeavyReports(),
        rag_store=_HeavyRAG(),
        block_provider=_VeryHeavyBlocks(),
    )

    result = asyncio.run(service.run("033790", trigger="ops_recovery_probe"))

    assert result["status"] == "ok"
    prompt_text = json.dumps(llm.calls[0], ensure_ascii=False)
    assert len(prompt_text) < 120_000
    assert "RAW_FUNDAMENTALS_CONTEXT" not in prompt_text
    assert "RAW_BLOCK_CONTEXT" not in prompt_text
    assert "OLD_PROMPT_CONTEXT" not in prompt_text
    assert llm.calls[0]["context"]["prompt_compaction"]["over_budget"] is False


def test_symbol_analysis_prompt_uses_symbol_scoped_native_thread(
    tmp_path: Path,
) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _FakeLLM()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeReports(),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
    )

    result = asyncio.run(service.run("033790", trigger="ops_recovery_probe"))

    assert result["status"] == "ok"
    prompt = llm.calls[0]
    assert prompt["native_thread_key"].startswith("symbol_analysis:033790:")
    assert prompt["jue_workflow"]["workflow_id"] == "instant_symbol_analysis"


def test_symbol_analysis_native_thread_key_is_scoped_by_trigger(
    tmp_path: Path,
) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _FakeLLM()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeReports(),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
    )

    result = asyncio.run(
        service.run("033790", trigger="daily_random_deep_research")
    )

    assert result["status"] == "ok"
    prompt = llm.calls[0]
    assert prompt["native_thread_key"] == (
        "symbol_analysis:033790:daily_random_deep_research:{date}"
    )


def test_symbol_analysis_etf_trigger_adds_etf_specific_guidance(
    tmp_path: Path,
) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _FakeLLM()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeReports(),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
    )

    result = asyncio.run(service.run("069500", trigger="daily_etf_deep_research"))

    assert result["status"] == "ok"
    prompt = llm.calls[0]
    prompt_text = json.dumps(prompt, ensure_ascii=False)
    assert prompt["native_thread_key"] == (
        "symbol_analysis:069500:daily_etf_deep_research:{date}"
    )
    assert "ETF-specific checklist" in prompt_text
    assert "tracking error" in prompt_text
    assert "core_etf" in prompt_text


def test_symbol_analysis_retries_busy_native_thread_lease(tmp_path: Path) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _LeaseBusyThenOkLLM()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeReports(),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
        lease_retry_count=1,
        lease_retry_delay_sec=0,
    )

    result = asyncio.run(service.run("033790", trigger="user_request"))

    assert result["status"] == "ok"
    assert result["analysis"]["status"] == "ok"
    assert result["analysis"]["stance"] == "risk_check"
    assert len(llm.calls) == 2


def test_symbol_analysis_retries_busy_native_thread_lease_payload(
    tmp_path: Path,
) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _LeaseBusyPayloadThenOkLLM()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeReports(),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
        lease_retry_count=1,
        lease_retry_delay_sec=0,
    )

    result = asyncio.run(service.run("033790", trigger="user_request"))

    assert result["status"] == "ok"
    assert result["analysis"]["summary"].startswith("피노는 payload lease")
    assert result["analysis"]["stance"] == "watch"
    assert len(llm.calls) == 2


def test_run_accepts_exact_symbol_name(tmp_path: Path) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    service = SymbolAnalysisService(
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeDirectoryReports(tmp_path / "reports.db"),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
    )

    result = asyncio.run(service.run("피노", trigger="user_request"))

    assert result["status"] == "ok"
    assert result["symbol"] == "033790"


def test_special_watch_extracts_existing_position_blocks(tmp_path: Path) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    service = SymbolAnalysisService(
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),
        quote_provider=_FakeQuoteProvider(),
        report_repository=_FakeReports(),
        rag_store=_FakeRAG(),
        block_provider=_FakeBlocks(),
    )

    payload = service.special_watch()

    assert payload["status"] == "ok"
    assert payload["items"][0]["symbol"] == "033790"
