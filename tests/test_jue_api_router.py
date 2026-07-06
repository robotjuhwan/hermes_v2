from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.jue import JueRouteDeps, build_jue_router


class _FakeRegistry:
    def compile_prompt_pack(self, workflow_id: str) -> dict[str, Any]:
        if workflow_id == "bad_workflow":
            raise ValueError("bad workflow")
        return {"workflow_id": workflow_id, "model_policy": {"model": "gpt-5.5"}}

    def load_source_manifest(self, source_id: str) -> dict[str, Any]:
        if source_id == "financial_services":
            return {
                "source_id": source_id,
                "repository_url": "https://example.test/repo",
                "mappings": [{"local_skill_id": "jue-kis-trading"}],
            }
        raise ValueError("manifest missing")


class _FakeLifecycleRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_artifacts(
        self,
        *,
        symbols: list[str] | None = None,
        workflow_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {"symbols": symbols, "workflow_id": workflow_id, "limit": limit}
        )
        return [
            {
                "artifact_id": "deep_005930",
                "symbol": "005930",
                "workflow_id": workflow_id or "kis_symbol_deep_dive",
            }
        ]


def _client(repository: _FakeLifecycleRepository | None = None) -> TestClient:
    lifecycle_repository = repository or _FakeLifecycleRepository()
    app = FastAPI()
    app.state.lifecycle_repository = lifecycle_repository
    app.include_router(
        build_jue_router(
            JueRouteDeps(
                require_admin_auth=lambda: None,
                registry_factory=_FakeRegistry,
                available_workflow_ids=lambda registry: [
                    "kis_intraday_manager",
                    "bad_workflow",
                ],
                validation_error_type=ValueError,
                lifecycle_repository_factory=lambda _: lifecycle_repository,
                investment_memory_db_path=lambda: ".runtime/investment_memory.db",
            )
        )
    )
    return TestClient(app)


def test_jue_workflows_status_collects_packs_and_reports_errors() -> None:
    with _client() as client:
        response = client.get("/api/jue/workflows/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "workflow_count": 1,
        "error_count": 1,
        "workflows": {
            "kis_intraday_manager": {
                "workflow_id": "kis_intraday_manager",
                "model_policy": {"model": "gpt-5.5"},
            }
        },
        "errors": {"bad_workflow": "bad workflow"},
    }


def test_jue_source_manifest_returns_mapping_summary() -> None:
    with _client() as client:
        response = client.get("/api/jue/source-manifest")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "source_id": "financial_services",
        "repository_url": "https://example.test/repo",
        "mapping_count": 1,
        "manifest": {
            "source_id": "financial_services",
            "repository_url": "https://example.test/repo",
            "mappings": [{"local_skill_id": "jue-kis-trading"}],
        },
    }


def test_jue_lifecycle_latest_filters_symbol_workflow_and_clamps_limit() -> None:
    repository = _FakeLifecycleRepository()

    with _client(repository) as client:
        response = client.get(
            "/api/jue/lifecycle/latest?symbol=005930&workflow_id=kis_symbol_deep_dive&limit=500"
        )

    assert response.status_code == 200
    assert response.json()["filters"] == {
        "symbol": "005930",
        "workflow_id": "kis_symbol_deep_dive",
        "limit": 100,
    }
    assert response.json()["items"][0]["artifact_id"] == "deep_005930"
    assert repository.calls == [
        {
            "symbols": ["005930"],
            "workflow_id": "kis_symbol_deep_dive",
            "limit": 100,
        }
    ]
