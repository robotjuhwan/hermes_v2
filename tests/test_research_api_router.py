from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.research import (
    ResearchRouteDeps,
    build_research_route_deps,
    build_research_router,
)


class _Settings:
    naver_reports_enabled = True
    rag_enabled = True
    rag_sync_chunk_limit = 7
    rag_persist_path = ".runtime/rag"
    rag_collection_name = "reports"
    rag_query_top_k = 4


class _Repository:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        return {"status": "repository_ok"}

    def search(
        self,
        *,
        query: str = "",
        symbol: str = "",
        category: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        self.search_calls.append(
            {
                "query": query,
                "symbol": symbol,
                "category": category,
                "limit": limit,
            }
        )
        return [{"query": query, "symbol": symbol, "category": category, "limit": limit}]

    def repair_metadata_quality(self) -> dict[str, Any]:
        return {"fixed": 1}

    def backfill_report_symbol_links(self, *, limit: int, asset_class: str) -> dict[str, Any]:
        return {"limit": limit, "asset_class": asset_class}

    def resolve_symbol_from_text(self, query: str) -> dict[str, Any]:
        return {"symbol": "005930", "name": query}


class _RagStore:
    def status(self) -> dict[str, Any]:
        return {"available": True}

    def search(self, **_: Any) -> list[dict[str, Any]]:
        return []


class _Fundamentals:
    def status(self) -> dict[str, Any]:
        return {"status": "fundamentals_ok"}


def _base_deps(**overrides: Any) -> ResearchRouteDeps:
    values: dict[str, Any] = {
        "require_admin_auth": lambda: None,
        "helper_ask": lambda payload: {"status": "ok", "payload": payload},
        "reports_status": lambda: {"status": "ok", "repository": {}},
        "reports_crawl_once": lambda: {"status": "ok", "snapshot": {}},
        "reports_repair_metadata": lambda sync_rag_after, prune_orphans: {
            "status": "ok",
            "sync_rag_after": sync_rag_after,
            "prune_orphans": prune_orphans,
        },
        "reports_backfill_symbol_links": lambda sync_rag_after, limit: {
            "status": "ok",
            "sync_rag_after": sync_rag_after,
            "limit": limit,
        },
        "reports_search": lambda query, symbol, category, limit: [
            {"title": query, "symbol": symbol, "category": category, "limit": limit}
        ],
        "rag_status": lambda: {"status": "ok", "enabled": True},
        "rag_sync": lambda force, metadata_only, prune_orphans, limit=None, rebuild=False: {
            "status": "ok",
            "force": force,
            "metadata_only": metadata_only,
            "prune_orphans": prune_orphans,
            "limit": limit,
            "rebuild": rebuild,
        },
        "rag_search": lambda query, symbol, broker, doc_id, date_from, date_to, limit: {
            "status": "ok",
            "query": query,
            "symbol": symbol,
            "limit": limit,
        },
    }
    values.update(overrides)
    return ResearchRouteDeps(**values)


def _app(deps: ResearchRouteDeps) -> FastAPI:
    app = FastAPI()
    app.include_router(build_research_router(deps))
    return app


def test_build_research_route_deps_wires_payload_builders() -> None:
    repository = _Repository()
    sync_calls: list[dict[str, Any]] = []
    seeded = {"called": False}

    def sync_report_rag(**kwargs: Any) -> dict[str, Any]:
        sync_calls.append(kwargs)
        return {"status": "synced", "metadata_only": kwargs.get("metadata_only")}

    deps = build_research_route_deps(
        require_admin_auth=lambda: None,
        helper_ask=lambda payload: {"status": "ok", "payload": payload},
        settings=_Settings(),
        naver_report_repository=repository,
        naver_report_crawler=object(),
        rag_store=_RagStore(),
        symbol_fundamentals_service=_Fundamentals(),
        build_report_intelligence_status=lambda settings: {
            "enabled": settings.naver_reports_enabled
        },
        run_report_collection_cycle=lambda **kwargs: {
            "snapshot": {"enabled": kwargs["rag_enabled"]},
            "rag_sync": {"limit": kwargs["rag_sync_chunk_limit"]},
        },
        sync_report_rag=sync_report_rag,
        seed_symbol_directory=lambda: seeded.update(called=True),
        on_rag_resolve_error=lambda exc: None,
    )

    assert deps.reports_status()["repository"] == {"status": "repository_ok"}
    assert asyncio.run(deps.reports_crawl_once())["rag_sync"] == {"limit": 7}
    assert deps.reports_repair_metadata(False, True)["rag_sync"] is None
    assert deps.rag_sync(False, True, True, limit=123)["result"] == {
        "status": "synced",
        "metadata_only": True,
    }
    assert sync_calls[-1]["limit"] == 123
    assert deps.reports_backfill_symbol_links(True, 3)["backfill"] == {
        "limit": 3,
        "asset_class": "etf",
    }
    assert seeded["called"] is True
    assert sync_calls[-1]["metadata_only"] is True
    assert deps.reports_search("반도체", "005930", "company", 5)[0]["limit"] == 5
    assert deps.rag_status()["rag"] == {"available": True}


def test_main_does_not_reown_research_payload_wrappers() -> None:
    source = Path("src/tradecraft/main.py").read_text()

    for marker in (
        "def _reports_status_payload(",
        "def _reports_crawl_once_payload(",
        "def _reports_repair_metadata_payload(",
        "def _reports_backfill_symbol_links_payload(",
        "def _reports_search_rows(",
        "def _rag_status_payload(",
        "def _rag_sync_payload(",
        "def _rag_search_payload(",
    ):
        assert marker not in source


def test_research_router_ask_builds_helper_payload_and_source() -> None:
    response = TestClient(_app(_base_deps())).get(
        "/api/research/ask",
        params={"query": "삼성전자", "symbol": "005930", "limit": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "research_ask"
    assert payload["payload"] == {
        "query": "삼성전자",
        "symbol": "005930",
        "broker": "",
        "date_from": "",
        "date_to": "",
        "limit": 3,
    }


def test_research_router_reports_search_wraps_rows_with_count() -> None:
    response = TestClient(_app(_base_deps())).get(
        "/api/reports/search",
        params={"query": "반도체", "symbol": "005930", "category": "company", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["items"][0]["limit"] == 5


def test_research_router_research_status_aliases_reports_status() -> None:
    calls: list[bool] = []

    def reports_status(*, compact: bool = False) -> dict[str, Any]:
        calls.append(compact)
        return {"status": "ok", "report_count": 42}

    response = TestClient(
        _app(_base_deps(reports_status=reports_status))
    ).get("/api/research/status", params={"compact": True})

    assert response.status_code == 200
    assert calls == [True]
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["report_count"] == 42
    assert payload["source"] == "research_status"
    assert payload["reports_status_endpoint"] == "/api/reports/status"


def test_research_router_repair_and_backfill_pass_query_flags() -> None:
    client = TestClient(_app(_base_deps()))

    repair = client.post(
        "/api/reports/repair-metadata",
        params={"sync_rag_after": False, "prune_orphans": False},
    )
    backfill = client.post(
        "/api/reports/backfill-symbol-links",
        params={"sync_rag_after": False, "limit": 12},
    )

    assert repair.status_code == 200
    assert repair.json()["sync_rag_after"] is False
    assert repair.json()["prune_orphans"] is False
    assert backfill.status_code == 200
    assert backfill.json()["limit"] == 12


def test_research_router_rag_search_rejects_blank_query() -> None:
    response = TestClient(_app(_base_deps())).get(
        "/api/rag/search",
        params={"query": "   "},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "query is required"


def test_research_router_rag_sync_awaits_callback() -> None:
    calls: list[dict[str, Any]] = []

    async def rag_sync(
        force: bool,
        metadata_only: bool,
        prune_orphans: bool,
        limit: int | None = None,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        calls.append(
            {
                "force": force,
                "metadata_only": metadata_only,
                "prune_orphans": prune_orphans,
                "limit": limit,
                "rebuild": rebuild,
            }
        )
        return {"status": "ok"}

    response = TestClient(_app(_base_deps(rag_sync=rag_sync))).post(
        "/api/rag/sync",
        params={
            "force": True,
            "metadata_only": True,
            "prune_orphans": True,
            "limit": 123,
            "rebuild": True,
            "confirm_heavy_sync": True,
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "force": True,
            "metadata_only": True,
            "prune_orphans": True,
            "limit": 123,
            "rebuild": True,
        }
    ]


def test_research_router_rag_sync_rejects_rebuild_without_confirmation() -> None:
    calls: list[dict[str, Any]] = []

    def rag_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {"status": "should_not_run"}

    response = TestClient(_app(_base_deps(rag_sync=rag_sync))).post(
        "/api/rag/sync",
        params={"rebuild": True, "limit": 100},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "rag sync is heavy; pass confirm_heavy_sync=true to run it"
    )
    assert calls == []


def test_research_router_rag_sync_rejects_large_limit_without_confirmation() -> None:
    calls: list[dict[str, Any]] = []

    def rag_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {"status": "should_not_run"}

    response = TestClient(_app(_base_deps(rag_sync=rag_sync))).post(
        "/api/rag/sync",
        params={"limit": 50000},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "rag sync is heavy; pass confirm_heavy_sync=true to run it"
    )
    assert calls == []
