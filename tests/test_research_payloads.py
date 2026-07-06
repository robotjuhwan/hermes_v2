from __future__ import annotations

import asyncio

from tradecraft.api.research_payloads import (
    build_reports_backfill_symbol_links_payload,
    build_reports_crawl_once_payload,
    build_reports_repair_metadata_payload,
    build_reports_status_payload,
    build_rag_search_payload,
    build_rag_status_payload,
    build_rag_sync_payload,
)


class _FakeRAGStore:
    def status(self) -> dict[str, object]:
        return {"available": True, "count": 3}


class _DegradedRAGStore:
    def status(self) -> dict[str, object]:
        return {
            "status": "degraded",
            "available": True,
            "count": 3,
            "count_source": "sqlite_fallback",
            "count_error": "RuntimeError: count unavailable",
        }


class _QueryRAGStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query(self, **kwargs) -> list[dict[str, object]]:
        self.calls.append(kwargs)
        return [{"content": kwargs["query"], "symbol": kwargs["symbol"]}]


class _EmptyDegradedQueryRAGStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query(self, **kwargs) -> list[dict[str, object]]:
        self.calls.append(kwargs)
        return []

    def status(self) -> dict[str, object]:
        return {
            "status": "degraded",
            "last_query_error": "InternalError: metadata segment failed",
        }


class _FallbackRepository:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []

    def resolve_symbol_from_text(self, query):
        return {
            "symbol": "005930",
            "company_name": "삼성전자",
            "match_type": "company_name",
            "confidence": 1.0,
        }

    def search(self, **kwargs) -> list[dict[str, object]]:
        self.search_calls.append(kwargs)
        query = str(kwargs.get("query") or "")
        symbol = str(kwargs.get("symbol") or "")
        if query or symbol != "005930":
            return []
        return [
            {
                "report_id": 7,
                "doc_id": "doc-7",
                "category": "company_analysis",
                "title": "삼성전자 실적 전망",
                "company_name": "삼성전자",
                "broker": "하나증권",
                "symbol": "005930",
                "published_at": "2026-06-18",
                "snippet": "삼성전자 HBM 매출액 추이 및 실적 전망.",
                "pdf_url": "https://example.com/report.pdf",
                "detail_url": "https://example.com/detail",
            }
        ]


def test_build_rag_status_payload_when_disabled() -> None:
    payload = build_rag_status_payload(
        rag_enabled=False,
        rag_store=_FakeRAGStore(),
        persist_path=".runtime/rag",
        collection_name="reports",
    )

    assert payload == {
        "status": "ok",
        "enabled": False,
        "rag": {
            "available": False,
            "reason": "rag_disabled",
            "persist_path": ".runtime/rag",
            "collection_name": "reports",
        },
    }


def test_build_rag_status_payload_when_store_missing() -> None:
    payload = build_rag_status_payload(
        rag_enabled=True,
        rag_store=None,
        persist_path=".runtime/rag",
        collection_name="reports",
    )

    assert payload["enabled"] is True
    assert payload["rag"]["available"] is False
    assert payload["rag"]["reason"] == "rag_store_missing"


def test_build_rag_status_payload_when_store_ready() -> None:
    payload = build_rag_status_payload(
        rag_enabled=True,
        rag_store=_FakeRAGStore(),
        persist_path=".runtime/rag",
        collection_name="reports",
    )

    assert payload == {
        "status": "ok",
        "enabled": True,
        "rag": {"available": True, "count": 3},
    }


def test_build_rag_status_payload_surfaces_degraded_store_status() -> None:
    payload = build_rag_status_payload(
        rag_enabled=True,
        rag_store=_DegradedRAGStore(),
        persist_path=".runtime/rag",
        collection_name="reports",
    )

    assert payload["status"] == "degraded"
    assert payload["enabled"] is True
    assert payload["rag"]["status"] == "degraded"
    assert payload["rag"]["count_source"] == "sqlite_fallback"
    assert "count unavailable" in str(payload["rag"]["count_error"])


def test_build_reports_status_payload_combines_repository_rag_and_services() -> None:
    payload = build_reports_status_payload(
        naver_reports_enabled=True,
        repository_status={"total_reports": 3},
        intelligence_status={"enabled": True},
        rag_status={"available": True, "count": 9},
        fundamentals_status={"total_snapshots": 2},
    )

    assert payload == {
        "status": "ok",
        "enabled": True,
        "report_count": 3,
        "latest_report_at": "",
        "latest_published_at": "",
        "db_path": "",
        "symbol_count": 0,
        "symbol_link_count": 0,
        "rag_available": True,
        "rag_count": 9,
        "fundamentals_symbol_count": 0,
        "fundamentals_stale_ratio": 0.0,
        "fundamentals_latest_symbols_stale_ratio": 0.0,
        "repository": {"total_reports": 3},
        "intelligence": {"enabled": True},
        "rag": {"available": True, "count": 9},
        "fundamentals": {"total_snapshots": 2},
    }


def test_build_reports_crawl_once_payload_runs_collection_cycle_and_shapes_result() -> None:
    crawler = object()
    repository = object()
    rag_store = _FakeRAGStore()
    calls: list[dict[str, object]] = []

    async def run_report_collection_cycle(**kwargs):
        calls.append(kwargs)
        return {
            "snapshot": {"inserted": 2},
            "metadata_repair": {"updated_reports": 1},
            "rag_sync": {"synced": 3},
            "rag_metadata_sync": {"metadata_updated": 4},
            "internal_debug": "not exposed",
        }

    payload = asyncio.run(
        build_reports_crawl_once_payload(
            crawler=crawler,
            repository=repository,
            rag_store=rag_store,
            rag_enabled=True,
            rag_sync_chunk_limit=17,
            run_report_collection_cycle=run_report_collection_cycle,
        )
    )

    assert payload == {
        "status": "ok",
        "snapshot": {"inserted": 2},
        "metadata_repair": {"updated_reports": 1},
        "rag_sync": {"synced": 3},
        "rag_metadata_sync": {"metadata_updated": 4},
    }
    assert calls == [
        {
            "crawler": crawler,
            "repository": repository,
            "rag_store": rag_store,
            "rag_enabled": True,
            "rag_sync_chunk_limit": 17,
            "refresh_symbol_directory": False,
        }
    ]


def test_build_reports_repair_metadata_payload_skips_rag_sync_when_disabled() -> None:
    class _Repository:
        def repair_metadata_quality(self) -> dict[str, object]:
            return {"status": "ok", "updated_reports": 2}

    calls: list[dict[str, object]] = []

    payload = build_reports_repair_metadata_payload(
        repository=_Repository(),
        rag_store=_FakeRAGStore(),
        rag_enabled=True,
        rag_sync_chunk_limit=50,
        sync_report_rag=lambda **kwargs: calls.append(kwargs) or {"status": "ok"},
        sync_rag_after=False,
        prune_orphans=True,
    )

    assert payload == {
        "status": "ok",
        "repair": {"status": "ok", "updated_reports": 2},
        "rag_sync": None,
    }
    assert calls == []


def test_build_reports_repair_metadata_payload_syncs_metadata_when_requested() -> None:
    class _Repository:
        def repair_metadata_quality(self) -> dict[str, object]:
            return {"status": "ok", "updated_reports": 1}

    repository = _Repository()
    rag_store = _FakeRAGStore()
    calls: list[dict[str, object]] = []

    payload = build_reports_repair_metadata_payload(
        repository=repository,
        rag_store=rag_store,
        rag_enabled=True,
        rag_sync_chunk_limit=7,
        sync_report_rag=lambda **kwargs: calls.append(kwargs) or {"metadata_updated": 1},
        sync_rag_after=True,
        prune_orphans=False,
    )

    assert payload["rag_sync"] == {"metadata_updated": 1}
    assert calls == [
        {
            "repository": repository,
            "rag_store": rag_store,
            "enabled": True,
            "limit": 7,
            "metadata_only": True,
            "prune_missing": False,
        }
    ]


def test_build_reports_backfill_symbol_links_payload_seeds_backfills_and_syncs() -> None:
    events: list[str] = []

    class _Repository:
        def backfill_report_symbol_links(self, *, limit: int, asset_class: str) -> dict[str, object]:
            events.append(f"backfill:{limit}:{asset_class}")
            return {"updated_reports": 3}

    repository = _Repository()
    rag_store = _FakeRAGStore()
    calls: list[dict[str, object]] = []

    payload = build_reports_backfill_symbol_links_payload(
        repository=repository,
        rag_store=rag_store,
        rag_enabled=True,
        rag_sync_chunk_limit=11,
        sync_report_rag=lambda **kwargs: calls.append(kwargs) or {"metadata_updated": 3},
        seed_symbol_directory=lambda: events.append("seed"),
        sync_rag_after=True,
        limit=-5,
    )

    assert payload == {
        "status": "ok",
        "backfill": {"updated_reports": 3},
        "rag_sync": {"metadata_updated": 3},
    }
    assert events == ["seed", "backfill:0:etf"]
    assert calls == [
        {
            "repository": repository,
            "rag_store": rag_store,
            "enabled": True,
            "limit": 11,
            "metadata_only": True,
            "prune_missing": False,
        }
    ]


def test_build_rag_sync_payload_when_disabled() -> None:
    payload = build_rag_sync_payload(
        rag_enabled=False,
        rag_store=_FakeRAGStore(),
        repository=object(),
        limit=20,
        sync_report_rag=lambda **kwargs: {"status": "should_not_run"},
    )

    assert payload == {
        "status": "ok",
        "enabled": False,
        "result": {"status": "skipped", "reason": "rag_disabled"},
    }


def test_build_rag_sync_payload_when_store_missing() -> None:
    payload = build_rag_sync_payload(
        rag_enabled=True,
        rag_store=None,
        repository=object(),
        limit=20,
        sync_report_rag=lambda **kwargs: {"status": "should_not_run"},
    )

    assert payload == {
        "status": "ok",
        "enabled": True,
        "result": {"status": "skipped", "reason": "rag_store_missing"},
    }


def test_build_rag_sync_payload_invokes_sync_callback_with_flags() -> None:
    repository = object()
    rag_store = _FakeRAGStore()
    calls: list[dict[str, object]] = []

    def sync_report_rag(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "metadata_updated": 2}

    payload = build_rag_sync_payload(
        rag_enabled=True,
        rag_store=rag_store,
        repository=repository,
        limit=7,
        sync_report_rag=sync_report_rag,
        force=True,
        metadata_only=True,
        prune_orphans=True,
        rebuild=True,
    )

    assert payload == {
        "status": "ok",
        "enabled": True,
        "result": {"status": "ok", "metadata_updated": 2},
    }
    assert calls == [
        {
            "repository": repository,
            "rag_store": rag_store,
            "enabled": True,
            "limit": 7,
            "force_update": True,
            "metadata_only": True,
            "prune_missing": True,
            "rebuild": True,
        }
    ]


def test_build_rag_sync_payload_uses_skipped_result_when_sync_returns_empty() -> None:
    payload = build_rag_sync_payload(
        rag_enabled=True,
        rag_store=_FakeRAGStore(),
        repository=object(),
        limit=3,
        sync_report_rag=lambda **kwargs: {},
    )

    assert payload == {
        "status": "ok",
        "enabled": True,
        "result": {"status": "skipped", "reason": "rag_store_missing"},
    }


def test_build_rag_search_payload_when_disabled() -> None:
    payload = build_rag_search_payload(
        rag_enabled=False,
        rag_store=_QueryRAGStore(),
        repository=object(),
        default_limit=13,
        query="반도체",
    )

    assert payload == {
        "status": "ok",
        "enabled": False,
        "count": 0,
        "items": [],
    }


def test_build_rag_search_payload_uses_default_limit_and_explicit_symbol() -> None:
    rag_store = _QueryRAGStore()

    payload = build_rag_search_payload(
        rag_enabled=True,
        rag_store=rag_store,
        repository=object(),
        default_limit=13,
        query="HBM",
        symbol="000660",
        broker="미래",
    )

    assert payload["status"] == "ok"
    assert payload["enabled"] is True
    assert payload["count"] == 1
    assert payload["auto_symbol"] is None
    assert rag_store.calls == [
        {
            "query": "HBM",
            "symbol": "000660",
            "broker": "미래",
            "doc_id": "",
            "date_from": "",
            "date_to": "",
            "limit": 13,
        }
    ]


def test_build_rag_search_payload_resolves_symbol_from_query_text() -> None:
    rag_store = _QueryRAGStore()

    class _Repository:
        def resolve_symbol_from_text(self, query):
            assert query == "SK하이닉스 HBM"
            return {
                "symbol": "000660",
                "company_name": "SK하이닉스",
                "match_type": "name",
                "confidence": "0.95",
            }

    payload = build_rag_search_payload(
        rag_enabled=True,
        rag_store=rag_store,
        repository=_Repository(),
        default_limit=8,
        query="SK하이닉스 HBM",
        limit=5,
    )

    assert rag_store.calls[0]["symbol"] == "000660"
    assert rag_store.calls[0]["limit"] == 5
    assert payload["auto_symbol"] == {
        "symbol": "000660",
        "company_name": "SK하이닉스",
        "match_type": "name",
        "confidence": 0.95,
    }


def test_build_rag_search_payload_falls_back_to_reports_when_chroma_query_degraded() -> None:
    rag_store = _EmptyDegradedQueryRAGStore()
    repository = _FallbackRepository()

    payload = build_rag_search_payload(
        rag_enabled=True,
        rag_store=rag_store,
        repository=repository,
        default_limit=8,
        query="삼성전자 HBM 실적",
        limit=3,
    )

    assert payload["status"] == "degraded"
    assert payload["enabled"] is True
    assert payload["count"] == 1
    assert payload["retrieval_source"] == "reports_fallback"
    assert payload["fallback_reason"] == "rag_query_degraded"
    assert payload["items"][0] == {
        "content": "삼성전자 HBM 매출액 추이 및 실적 전망.",
        "distance": None,
        "doc_id": "doc-7",
        "report_id": 7,
        "chunk_index": 0,
        "symbol": "005930",
        "category": "company_analysis",
        "title": "삼성전자 실적 전망",
        "broker": "하나증권",
        "published_at": "2026-06-18",
        "page_start": 0,
        "page_end": 0,
        "section_title": "reports_search_fallback",
        "pdf_url": "https://example.com/report.pdf",
        "detail_url": "https://example.com/detail",
        "linked_symbols": "005930",
        "linked_names": "삼성전자",
        "linked_asset_classes": "equity",
        "retrieval_source": "reports_fallback",
        "fallback_query": "",
    }
    assert repository.search_calls == [
        {
            "query": "삼성전자 HBM 실적",
            "symbol": "005930",
            "limit": 3,
        },
        {
            "query": "HBM 실적",
            "symbol": "005930",
            "limit": 3,
        },
        {
            "query": "삼성전자 HBM",
            "symbol": "005930",
            "limit": 3,
        },
        {
            "query": "",
            "symbol": "005930",
            "limit": 3,
        },
    ]
