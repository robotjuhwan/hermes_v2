from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.evidence import EvidenceRouteDeps, build_evidence_router


class _FakeMemoryRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        self.calls.append({"method": "status"})
        return {"status": "ok", "policy_rule_count": 1}

    def list_policy_rules(
        self,
        *,
        active_only: bool = False,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {"method": "list_policy_rules", "active_only": active_only, "limit": limit}
        )
        return [{"policy_id": "rule-1", "active_only": active_only}]

    def list_policy_scorecards(self, *, limit: int = 30) -> list[dict[str, Any]]:
        self.calls.append({"method": "list_policy_scorecards", "limit": limit})
        return [{"policy_id": "scorecard-1"}]


def _client(repository: _FakeMemoryRepository) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_evidence_router(
            EvidenceRouteDeps(
                require_admin_auth=lambda: None,
                source_statuses={
                    "crypto_market_research": lambda: {
                        "status": "ok",
                        "source": "research",
                    },
                    "crypto_alpha": lambda: {
                        "status": "ok",
                        "source": "alpha",
                    },
                    "crypto_quant": lambda: {
                        "status": "ok",
                        "source": "quant",
                    },
                },
                memory_repository=lambda: repository,
            )
        )
    )
    return TestClient(app)


def test_evidence_policy_status_combines_sources_and_read_only_memory_status() -> None:
    repository = _FakeMemoryRepository()

    with _client(repository) as client:
        response = client.get("/api/evidence-policy/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["source_count"] == 3
    assert payload["sources"]["crypto_quant"]["source"] == "quant"
    assert payload["policy"]["memory_status"] == {
        "status": "ok",
        "policy_rule_count": 1,
        "read_only": True,
    }
    assert "decision_packet" in payload["policy"]["loop"]
    assert repository.calls == [{"method": "status"}]


def test_evidence_policy_context_clamps_limit_and_reads_repository_directly() -> None:
    repository = _FakeMemoryRepository()

    with _client(repository) as client:
        response = client.get("/api/evidence-policy/context?limit=500")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "policy_rules": [{"policy_id": "rule-1", "active_only": True}],
        "policy_scorecards": [{"policy_id": "scorecard-1"}],
    }
    assert repository.calls == [
        {"method": "list_policy_rules", "active_only": True, "limit": 50},
        {"method": "list_policy_scorecards", "limit": 50},
    ]
