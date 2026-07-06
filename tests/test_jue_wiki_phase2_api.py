from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tradecraft.api.wiki import WikiRouteDeps, build_wiki_router


@dataclass
class FakeWiki:
    calls: list[tuple[str, dict[str, Any]]]

    def status(self) -> dict[str, Any]:
        return {"status": "ok", "enabled": True, "page_count": 1}

    def context_pack(
        self,
        *,
        target_scope: str = "",
        symbols: list[str] | None = None,
        page_types: list[str] | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        _ = target_scope, symbols, page_types, max_chars
        return {"status": "ok", "pages": []}

    def read_page(self, page_id: str) -> dict[str, Any]:
        return {"status": "ok", "page_id": page_id, "content": "# Page"}

    def rebuild(self, *, scope: str = "", force: bool = False) -> dict[str, Any]:
        _ = scope, force
        return {"status": "ok", "updated_count": 0}

    def lint(self, *, scope: str = "") -> dict[str, Any]:
        _ = scope
        return {"status": "ok", "open_findings": []}

    def list_lint_findings(
        self,
        *,
        scope: str | None = None,
        status: str = "open",
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("list_lint_findings", {"scope": scope, "status": status})
        )
        return [
            {
                "finding_id": "finding-1",
                "page_id": "kis.symbol.005930",
                "finding_type": "stale_page",
                "status": status,
                "evidence": {"freshness": "stale"},
            }
        ]

    def repair_once(self, *, scope: str | None = None) -> dict[str, Any]:
        self.calls.append(("repair_once", {"scope": scope}))
        return {
            "status": "ok",
            "actions": [
                {
                    "action_id": "action-1",
                    "page_id": "kis.symbol.005930",
                    "action_type": "rebuild_page",
                    "status": "scheduled",
                }
            ],
        }

    def page_sources(self, page_id: str) -> dict[str, Any]:
        self.calls.append(("page_sources", {"page_id": page_id}))
        return {
            "status": "ok",
            "page_id": page_id,
            "source_refs": [{"source_type": "test", "source_id": "fixture-1"}],
        }


def _app(service: FakeWiki, *, admin_ok: bool = True) -> FastAPI:
    app = FastAPI()

    def require_admin_auth() -> None:
        if not admin_ok:
            raise HTTPException(status_code=401, detail="admin auth required")

    app.include_router(
        build_wiki_router(
            WikiRouteDeps(service=service, require_admin_auth=require_admin_auth)
        )
    )
    return app


def test_wiki_search_api_returns_ranked_pages() -> None:
    @dataclass
    class SearchWiki(FakeWiki):
        def search(
            self,
            query: str = "",
            scope: str | None = None,
            page_type: str | None = None,
        ) -> list[dict[str, Any]]:
            self.calls.append(
                (
                    "search",
                    {"query": query, "scope": scope, "page_type": page_type},
                )
            )
            return [{"page_id": "kis.symbol.005930", "title": "삼성전자"}]

    service = SearchWiki(calls=[])

    with TestClient(_app(service)) as client:
        response = client.get("/api/wiki/search?query=삼성전자&scope=kis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["query"] == "삼성전자"
    assert payload["scope"] == "kis"
    assert payload["pages"][0]["page_id"] == "kis.symbol.005930"
    assert (
        "search",
        {"query": "삼성전자", "scope": "kis", "page_type": None},
    ) in service.calls


def test_wiki_search_api_preserves_error_status_without_explicit_status() -> None:
    @dataclass
    class SearchWiki(FakeWiki):
        def search(
            self,
            query: str = "",
            scope: str | None = None,
            page_type: str | None = None,
        ) -> dict[str, Any]:
            self.calls.append(
                (
                    "search",
                    {"query": query, "scope": scope, "page_type": page_type},
                )
            )
            return {"error": "boom"}

    service = SearchWiki(calls=[])

    with TestClient(_app(service)) as client:
        response = client.get("/api/wiki/search?query=삼성전자&scope=kis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error"] == "boom"
    assert payload["pages"] == []


def test_wiki_phase2_read_apis_require_admin_auth() -> None:
    service = FakeWiki(calls=[])

    with TestClient(_app(service, admin_ok=False)) as client:
        search = client.get("/api/wiki/search?query=삼성전자&scope=kis")
        findings = client.get("/api/wiki/lint/findings?scope=kis&status=open")
        sources = client.get("/api/wiki/pages/kis.symbol.005930/sources")
        page = client.get("/api/wiki/pages/kis.symbol.005930")

    assert search.status_code == 401
    assert findings.status_code == 401
    assert sources.status_code == 401
    assert page.status_code == 401
    assert service.calls == []


def test_wiki_phase2_findings_and_sources_routes() -> None:
    service = FakeWiki(calls=[])

    with TestClient(_app(service)) as client:
        findings = client.get(
            "/api/wiki/lint/findings?scope=kis&status=open"
        )
        sources = client.get("/api/wiki/pages/kis.symbol.005930/sources")

    assert findings.status_code == 200
    assert findings.json()["findings"][0]["finding_type"] == "stale_page"
    assert sources.status_code == 200
    assert sources.json()["source_refs"][0]["source_id"] == "fixture-1"
    assert (
        "list_lint_findings",
        {"scope": "kis", "status": "open"},
    ) in service.calls
    assert (
        "page_sources",
        {"page_id": "kis.symbol.005930"},
    ) in service.calls


def test_wiki_phase2_repair_route_requires_admin_auth() -> None:
    service = FakeWiki(calls=[])

    with TestClient(_app(service, admin_ok=False)) as client:
        response = client.post("/api/wiki/repair/run-once", json={"scope": "kis"})

    assert response.status_code == 401
    assert ("repair_once", {"scope": "kis"}) not in service.calls


def test_wiki_phase2_repair_route_runs_when_authorized() -> None:
    service = FakeWiki(calls=[])

    with TestClient(_app(service)) as client:
        response = client.post("/api/wiki/repair/run-once", json={"scope": "kis"})

    assert response.status_code == 200
    assert response.json()["actions"][0]["action_type"] == "rebuild_page"
    assert ("repair_once", {"scope": "kis"}) in service.calls


def test_wiki_application_api_requires_admin_and_returns_status() -> None:
    service = FakeWiki(calls=[])

    with TestClient(_app(service, admin_ok=False)) as client:
        blocked = client.get("/api/wiki/application/status")

    assert blocked.status_code == 401

    with TestClient(_app(service, admin_ok=True)) as client:
        allowed = client.get("/api/wiki/application/status")

    assert allowed.status_code == 200
    assert allowed.json()["status"] in {"ok", "unavailable"}


def test_wiki_application_effectiveness_api_returns_unavailable_for_fake() -> None:
    service = FakeWiki(calls=[])

    with TestClient(_app(service)) as client:
        response = client.get("/api/wiki/application/effectiveness?scope=kis")

    assert response.status_code == 200
    assert response.json() == {
        "status": "unavailable",
        "scope": "kis",
        "pages": [],
    }
