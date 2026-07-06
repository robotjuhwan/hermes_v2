from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.crypto import CryptoRouteDeps, build_crypto_router


def _symbols(raw: Any) -> list[str]:
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else str(raw).replace(",", " ").split()
    return [str(value).upper() for value in values if str(value).strip()]


def _client(deps: CryptoRouteDeps) -> TestClient:
    app = FastAPI()
    app.include_router(build_crypto_router(deps))
    return TestClient(app)


def test_crypto_router_uses_research_service_and_defaults() -> None:
    calls: list[tuple[str, Any]] = []

    class Research:
        def status(self) -> dict[str, Any]:
            calls.append(("status", {}))
            return {"status": "ok"}

        def latest_context(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            calls.append(("context", {"symbols": symbols, "limit": limit}))
            return {"status": "ok", "symbols": symbols, "limit": limit}

        async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
            calls.append(("collect", symbols))
            return {"status": "ok", "symbols": symbols}

        async def run_research_once(
            self,
            *,
            symbols: list[str] | None = None,
        ) -> dict[str, Any]:
            calls.append(("run", symbols))
            return {"status": "ok", "symbols": symbols}

    deps = CryptoRouteDeps(
        require_admin_auth=lambda: None,
        crypto_research_service=Research(),
        crypto_alpha_service=object(),
        crypto_research_symbols=_symbols,
        default_crypto_research_symbols=lambda: ["BTCUSDT", "ETHUSDT"],
    )

    with _client(deps) as client:
        status = client.get("/api/crypto/research/status")
        context = client.get("/api/crypto/research/context?symbols=btcusdt&limit=999")
        collect = client.post("/api/crypto/research/collect", json={})
        run_once = client.post(
            "/api/crypto/research/run-once",
            json={"symbols": ["solusdt"]},
        )

    assert status.status_code == 200
    assert context.json()["limit"] == 100
    assert collect.json()["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert run_once.json()["symbols"] == ["SOLUSDT"]
    assert ("context", {"symbols": ["BTCUSDT"], "limit": 100}) in calls


def test_crypto_router_uses_alpha_service() -> None:
    calls: list[tuple[str, Any]] = []

    class Alpha:
        def status(self) -> dict[str, Any]:
            calls.append(("status", {}))
            return {"status": "ok", "events": 2}

        def context_pack(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 12,
        ) -> dict[str, Any]:
            calls.append(("context", {"symbols": symbols, "limit": limit}))
            return {"status": "ok", "symbols": symbols, "limit": limit}

        async def collect_once(self) -> dict[str, Any]:
            calls.append(("collect", {}))
            return {"status": "ok", "created_events": 1}

        async def label_due_outcomes(self) -> dict[str, Any]:
            calls.append(("outcomes", {}))
            return {"status": "ok", "labeled": 1}

    deps = CryptoRouteDeps(
        require_admin_auth=lambda: None,
        crypto_research_service=object(),
        crypto_alpha_service=Alpha(),
        crypto_research_symbols=_symbols,
        default_crypto_research_symbols=lambda: ["BTCUSDT"],
    )

    with _client(deps) as client:
        status = client.get("/api/crypto/alpha/status")
        context = client.get("/api/crypto/alpha/context?symbols=ethusdt&limit=999")
        collect = client.post("/api/crypto/alpha/collect")
        outcomes = client.post("/api/crypto/alpha/outcomes/run-once")

    assert status.json()["events"] == 2
    assert context.json()["limit"] == 50
    assert collect.json()["created_events"] == 1
    assert outcomes.json()["labeled"] == 1
    assert ("context", {"symbols": ["ETHUSDT"], "limit": 50}) in calls
