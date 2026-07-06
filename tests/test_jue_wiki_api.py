from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tradecraft.api.wiki import WikiRouteDeps, build_wiki_router
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService


@dataclass
class FakeWikiService:
    calls: list[tuple[str, dict[str, Any]]]

    def status(self) -> dict[str, Any]:
        self.calls.append(("status", {}))
        return {"status": "ok", "page_count": 3, "enabled": True}

    def context_pack(
        self,
        *,
        target_scope: str = "",
        symbols: list[str] | None = None,
        page_types: list[str] | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "context_pack",
                {
                    "target_scope": target_scope,
                    "symbols": symbols or [],
                    "page_types": page_types or [],
                    "max_chars": max_chars,
                },
            )
        )
        return {
            "status": "ok",
            "target_scope": target_scope,
            "pages": [{"page_id": "kis.symbol.005930", "summary": "Samsung compact"}],
        }

    def read_page(self, page_id: str) -> dict[str, Any]:
        self.calls.append(("read_page", {"page_id": page_id}))
        return {"status": "ok", "page_id": page_id, "content": "# Samsung"}

    def rebuild(self, *, scope: str = "", force: bool = False) -> dict[str, Any]:
        self.calls.append(("rebuild", {"scope": scope, "force": force}))
        return {"status": "ok", "scope": scope, "updated_count": 1}

    def lint(self, *, scope: str = "") -> dict[str, Any]:
        self.calls.append(("lint", {"scope": scope}))
        return {"status": "ok", "open_findings": []}


def fake_admin_auth() -> None:
    return None


def test_wiki_router_requires_admin_auth_for_content_read_routes() -> None:
    service = FakeWikiService(calls=[])
    app = FastAPI()

    def require_admin_auth() -> None:
        raise HTTPException(status_code=401, detail="admin auth required")

    app.include_router(
        build_wiki_router(
            WikiRouteDeps(service=service, require_admin_auth=require_admin_auth)
        )
    )

    with TestClient(app) as client:
        status = client.get("/api/wiki/status")
        context = client.get(
            "/api/wiki/context?scope=kis&symbol=005930&page_type=symbol&max_chars=500"
        )
        page = client.get("/api/wiki/pages/kis.symbol.005930")

    assert status.status_code == 200
    assert status.json()["page_count"] == 3
    assert context.status_code == 401
    assert page.status_code == 401
    assert not any(name == "context_pack" for name, _ in service.calls)
    assert not any(name == "read_page" for name, _ in service.calls)


def test_wiki_router_allows_authenticated_content_read_routes() -> None:
    service = FakeWikiService(calls=[])
    app = FastAPI()
    app.include_router(
        build_wiki_router(
            WikiRouteDeps(service=service, require_admin_auth=fake_admin_auth)
        )
    )

    with TestClient(app) as client:
        context = client.get(
            "/api/wiki/context?scope=kis&symbol=005930&page_type=symbol&max_chars=500"
        ).json()
        page = client.get("/api/wiki/pages/kis.symbol.005930").json()

    assert context["target_scope"] == "kis"
    assert context["pages"][0]["page_id"] == "kis.symbol.005930"
    assert page["content"] == "# Samsung"
    assert (
        "context_pack",
        {
            "target_scope": "kis",
            "symbols": ["005930"],
            "page_types": ["symbol"],
            "max_chars": 500,
        },
    ) in service.calls


def test_wiki_router_requires_admin_auth_for_mutation_routes() -> None:
    service = FakeWikiService(calls=[])
    app = FastAPI()

    def require_admin_auth() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="admin auth required")

    app.include_router(
        build_wiki_router(
            WikiRouteDeps(service=service, require_admin_auth=require_admin_auth)
        )
    )

    with TestClient(app) as client:
        rebuild = client.post("/api/wiki/rebuild", json={"scope": "kis", "force": True})
        lint = client.post("/api/wiki/lint", json={"scope": "kis"})

    assert rebuild.status_code == 401
    assert lint.status_code == 401
    assert ("rebuild", {"scope": "kis", "force": True}) not in service.calls
    assert ("lint", {"scope": "kis"}) not in service.calls


def test_wiki_router_allows_authenticated_mutation_routes() -> None:
    service = FakeWikiService(calls=[])
    app = FastAPI()
    app.include_router(
        build_wiki_router(
            WikiRouteDeps(service=service, require_admin_auth=fake_admin_auth)
        )
    )

    with TestClient(app) as client:
        rebuild = client.post(
            "/api/wiki/rebuild", json={"scope": "kis", "force": True}
        )
        lint = client.post("/api/wiki/lint", json={"scope": "kis"})

    assert rebuild.status_code == 200
    assert rebuild.json()["updated_count"] == 1
    assert lint.status_code == 200
    assert lint.json()["status"] == "ok"
    assert ("rebuild", {"scope": "kis", "force": True}) in service.calls
    assert ("lint", {"scope": "kis"}) in service.calls


def test_main_app_uses_real_jue_wiki_service() -> None:
    from tradecraft.main import app
    from tradecraft import main

    with TestClient(app) as client:
        response = client.get("/api/wiki/status")

    assert response.status_code == 200
    assert isinstance(main.jue_wiki_service, JueWikiService)
    assert response.json()["enabled"] is True
    assert main.jue_wiki_service.config.market_pulse_db_path is not None
    assert main.jue_wiki_service.config.etf_research_db_path is not None
    assert main.jue_wiki_service.config.strategy_insights_db_path is not None
    assert main.jue_wiki_service.config.crypto_quant_db_path is not None
    assert main.jue_wiki_service.config.crypto_pattern_lab_db_path is not None
    assert main.jue_wiki_service.config.crypto_alpha_db_path is not None


def test_main_jue_wiki_provider_honors_disabled_setting(monkeypatch) -> None:
    from tradecraft import main

    monkeypatch.setattr(main.settings, "jue_wiki_enabled", False)

    payload = main._jue_wiki_context_provider(target_scope="kis", symbols=["005930"])

    assert payload["status"] == "disabled"
    assert payload["enabled"] is False
    assert payload["content"] == ""
    assert payload["pages"] == []


def test_main_jue_wiki_provider_passes_horizon_hints_to_selector(monkeypatch) -> None:
    from types import SimpleNamespace

    from tradecraft import main

    seen: dict[str, Any] = {}

    class FakeSelector:
        def __init__(self, service: object) -> None:
            seen["service"] = service

        def select(self, request: object) -> SimpleNamespace:
            seen["horizons"] = list(getattr(request, "horizons", []))
            seen["symbols"] = list(getattr(request, "symbols", []))
            return SimpleNamespace(
                status="ok",
                selection_run_id="selection:horizon-hints",
                target_scope=getattr(request, "target_scope", ""),
                mode_recommendation={},
                content="",
                effectiveness_policy={},
                repair_priorities=[],
                requested_symbol_summaries=[],
                pages=[],
                rejected_pages=[],
                budget_report={"status": "ok"},
            )

    monkeypatch.setattr(main, "JueWikiSelector", FakeSelector)
    monkeypatch.setattr(main.settings, "jue_wiki_enabled", True)

    payload = main._jue_wiki_context_provider(
        target_scope="kis",
        symbols=["005930"],
        horizons=["mid", "long", "core_etf"],
    )

    assert payload["status"] == "ok"
    assert seen["symbols"] == ["005930"]
    assert seen["horizons"] == ["mid", "long", "core_etf"]


def test_main_jue_wiki_provider_preserves_selector_growth_metadata(monkeypatch) -> None:
    from types import SimpleNamespace

    from tradecraft import main

    class FakeSelector:
        def __init__(self, service: object) -> None:
            _ = service

        def select(self, request: object) -> SimpleNamespace:
            _ = request
            return SimpleNamespace(
                status="ok",
                selection_run_id="selection:growth-metadata",
                target_scope="kis",
                mode_recommendation={},
                content="",
                effectiveness_policy={"status": "active"},
                repair_priorities=[{"source_id": "repair:summary:kis:005930"}],
                repair_action_batches=[
                    {"scope": "kis", "action_type": "refresh_requested_symbol_summary"}
                ],
                evidence_quality={"summary_line": "evidence_quality sources=1"},
                requested_symbol_summaries=[],
                pages=[],
                rejected_pages=[],
                budget_report={"status": "ok"},
                trust_profile_effectiveness={"status": "active"},
                repair_priority_effectiveness={"status": "repair_required"},
                validation_repair_effectiveness={"status": "repair_required"},
                wiki_application_coverage={"status": "partial"},
            )

    monkeypatch.setattr(main, "JueWikiSelector", FakeSelector)
    monkeypatch.setattr(main.settings, "jue_wiki_enabled", True)

    payload = main._jue_wiki_context_provider(target_scope="kis", symbols=["005930"])

    assert payload["repair_action_batches"] == [
        {"scope": "kis", "action_type": "refresh_requested_symbol_summary"}
    ]
    assert payload["evidence_quality"] == {"summary_line": "evidence_quality sources=1"}
    assert payload["trust_profile_effectiveness"] == {"status": "active"}
    assert payload["repair_priority_effectiveness"] == {"status": "repair_required"}
    assert payload["validation_repair_effectiveness"] == {"status": "repair_required"}
    assert payload["wiki_application_coverage"] == {"status": "partial"}


def test_main_jue_wiki_provider_preserves_selected_page_quality_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    from tradecraft import main

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )
    page_id = service.write_page(
        scope="kis",
        page_type="symbol",
        key="005930",
        title="삼성전자 API quality",
        symbols=["005930"],
        content_sections={
            "Current Stance": "품질 메타 전달 테스트",
            "Durable Facts": "facts",
            "Evidence Links": "evidence",
            "Trading History": "history",
            "Lessons": "lessons",
            "Contradictions": "none",
            "Open Questions": "questions",
            "Next Context Pack Summary": "summary",
        },
        source_refs=[
            {
                "source_type": "symbol_fundamentals",
                "source_id": "005930:fund",
                "quality_status": "weak",
                "quality_warnings": ["valuation_stale_gt_30d", "price_missing"],
            }
        ],
        confidence=0.8,
        freshness="stale",
    )["page_id"]

    monkeypatch.setattr(main, "jue_wiki_service", service)
    monkeypatch.setattr(main.settings, "jue_wiki_enabled", True)
    monkeypatch.setattr(main.settings, "jue_wiki_full_prompt_max_chars", 20_000)
    monkeypatch.setattr(main.settings, "jue_wiki_selector_max_pages", 24)
    monkeypatch.setattr(main.settings, "jue_wiki_selector_min_confidence", 0.15)
    monkeypatch.setattr(main.settings, "jue_wiki_exclude_lint_warnings", False)
    monkeypatch.setattr(main.settings, "jue_wiki_effectiveness_weight", 0.0)
    monkeypatch.setattr(main.settings, "jue_wiki_effectiveness_max_adjustment", 0.0)

    payload = main._jue_wiki_context_provider(target_scope="kis", symbols=["005930"])

    page = payload["pages"][0]
    assert page["page_id"] == page_id
    assert page["quality_status"] == "weak"
    assert set(page["quality_warnings"]) == {
        "valuation_stale_gt_30d",
        "price_missing",
    }
