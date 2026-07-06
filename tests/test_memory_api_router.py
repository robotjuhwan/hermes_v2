from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.memory import MemoryRouteDeps, build_memory_router


def _admin() -> None:
    return None


class FakeMemoryService:
    def status(self, *, scope: str = "", compact: bool = False) -> dict[str, Any]:
        return {"status": "ok", "memory_scope": scope or "all", "compact": compact}

    def today(self, *, scope: str = "", compact: bool = False) -> dict[str, Any]:
        return {
            "trading_day": "2026-05-08",
            "memory_scope": scope or "all",
            "compact": compact,
        }

    def symbol_memory(self, symbol: str) -> dict[str, Any]:
        if symbol == "BAD":
            return {"status": "invalid_symbol"}
        return {"status": "ok", "symbol": symbol}

    def block_memory(self, block_id: str) -> dict[str, Any]:
        if block_id == "bad":
            return {"status": "invalid_block_id"}
        return {"status": "ok", "block_id": block_id}

    def initialize(self, *, force: bool = False) -> dict[str, Any]:
        return {"status": "ok", "force": force}

    async def run_ritual(
        self,
        *,
        slot: str,
        context: dict[str, Any],
        send_telegram: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "slot": slot,
            "context": context,
            "send_telegram": send_telegram,
            "force": force,
        }

    async def run_update(
        self,
        *,
        context: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        return {"status": "ok", "context": context, "force": force}

    def seed_current(
        self,
        *,
        context: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        return {"status": "ok", "slot": "seed", "context": context, "force": force}

    def run_due_reflections(
        self,
        *,
        context: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        return {"status": "ok", "created_count": 1, "context": context, "force": force}

    def latest_period_review(self, period_type: str) -> dict[str, Any]:
        return {"status": "ok", "period_type": period_type}

    def period_reviews(self, *, period_type: str = "", limit: int = 12) -> dict[str, Any]:
        return {"status": "ok", "period_type": period_type, "limit": limit}

    async def run_period_review(
        self,
        *,
        period_type: str,
        context: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        return {"status": "ok", "period_type": period_type, "context": context, "force": force}

    def latest_historical_replay(self, period_type: str) -> dict[str, Any]:
        return {"status": "ok", "period_type": period_type}

    def historical_replays(
        self,
        *,
        period_type: str = "",
        limit: int = 12,
    ) -> dict[str, Any]:
        return {"status": "ok", "period_type": period_type, "limit": limit}

    async def run_historical_replay(
        self,
        *,
        period_type: str,
        context: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        return {"status": "ok", "period_type": period_type, "context": context, "force": force}

    def policy_scorecards(self, *, limit: int = 30) -> dict[str, Any]:
        return {"status": "ok", "limit": limit}

    def policy_rules(
        self,
        *,
        active_only: bool = False,
        limit: int = 30,
    ) -> dict[str, Any]:
        return {"status": "ok", "active_only": active_only, "limit": limit}

    def policy_revisions(self, *, status: str = "", limit: int = 30) -> dict[str, Any]:
        return {"status": "ok", "filter_status": status, "limit": limit}

    def activate_policy_revision(self, revision_id: str) -> dict[str, Any]:
        if revision_id == "missing":
            return {"status": "missing"}
        return {"status": "ok", "revision_id": revision_id, "activated": True}

    def reject_policy_revision(self, revision_id: str) -> dict[str, Any]:
        if revision_id == "missing":
            return {"status": "missing"}
        return {"status": "ok", "revision_id": revision_id, "rejected": True}


async def _context() -> dict[str, Any]:
    return {"account": {"cash_krw": 1_000_000}}


def _client() -> TestClient:
    service = FakeMemoryService()
    app = FastAPI()
    app.include_router(
        build_memory_router(
            MemoryRouteDeps(
                require_admin_auth=_admin,
                status=service.status,
                today=service.today,
                symbol_memory=service.symbol_memory,
                block_memory=service.block_memory,
                initialize=service.initialize,
                build_context=_context,
                run_ritual=service.run_ritual,
                run_update=service.run_update,
                seed_current=service.seed_current,
                run_due_reflections=service.run_due_reflections,
                latest_period_review=service.latest_period_review,
                period_reviews=service.period_reviews,
                run_period_review=service.run_period_review,
                latest_historical_replay=service.latest_historical_replay,
                historical_replays=service.historical_replays,
                run_historical_replay=service.run_historical_replay,
                policy_scorecards=service.policy_scorecards,
                policy_rules=service.policy_rules,
                policy_revisions=service.policy_revisions,
                activate_policy_revision=service.activate_policy_revision,
                reject_policy_revision=service.reject_policy_revision,
            )
        )
    )
    return TestClient(app)


def test_memory_router_serves_core_memory_routes() -> None:
    with _client() as client:
        assert client.get("/api/memory/status").json()["status"] == "ok"
        assert client.get("/api/memory/status").json()["memory_scope"] == "all"
        assert client.get("/api/memory/status?scope=kis").json()["memory_scope"] == "kis"
        assert client.get("/api/memory/status?scope=binance").json()["memory_scope"] == "binance"
        assert client.get("/api/memory/status").json()["compact"] is True
        assert client.get("/api/memory/status?scope=kis&compact=true").json()[
            "compact"
        ] is True
        assert "compact" not in client.get(
            "/api/memory/status?scope=kis&compact=false"
        ).json()
        assert client.get("/api/memory/today").json()["trading_day"] == "2026-05-08"
        assert client.get("/api/memory/today").json()["memory_scope"] == "all"
        assert client.get("/api/memory/today").json()["compact"] is True
        assert client.get("/api/memory/today?scope=kis").json()["memory_scope"] == "kis"
        assert client.get("/api/memory/today?scope=binance").json()["memory_scope"] == "binance"
        assert client.get("/api/memory/today?scope=kis&compact=true").json()[
            "compact"
        ] is True
        assert "compact" not in client.get(
            "/api/memory/today?scope=kis&compact=false"
        ).json()
        assert client.get("/api/memory/symbols/005930").json()["symbol"] == "005930"
        assert client.get("/api/memory/blocks/blk_1").json()["block_id"] == "blk_1"
        assert client.post("/api/memory/init", json={"force": True}).json()["force"] is True
        ritual = client.post(
            "/api/memory/rituals/run-once",
            json={"slot": "midday", "send_telegram": True, "force": False},
        ).json()
        assert ritual["slot"] == "midday"
        assert ritual["send_telegram"] is True
        assert ritual["context"]["account"]["cash_krw"] == 1_000_000


def test_memory_router_serves_reviews_replays_and_policy_routes() -> None:
    with _client() as client:
        assert client.post("/api/memory/update/run-once", json={"force": True}).json()["force"] is True
        assert client.post("/api/memory/seed-current", json={"force": True}).json()["slot"] == "seed"
        assert client.post("/api/memory/reflections/run-due", json={"force": True}).json()["created_count"] == 1
        assert client.get("/api/memory/reviews/latest?period_type=monthly").json()["period_type"] == "monthly"
        assert client.get("/api/memory/reviews/history?limit=500").json()["limit"] == 100
        assert client.post("/api/memory/reviews/run-once", json={"period_type": "weekly"}).json()["period_type"] == "weekly"
        assert client.get("/api/memory/replays/latest?period_type=weekly").json()["period_type"] == "weekly"
        assert client.get("/api/memory/replays/history?limit=500").json()["limit"] == 100
        assert client.post("/api/memory/replays/run-once", json={"period_type": "weekly"}).json()["period_type"] == "weekly"
        assert client.get("/api/memory/policies/scorecards?limit=500").json()["limit"] == 200
        rules = client.get("/api/memory/policies/rules?active_only=true&limit=5").json()
        assert rules["active_only"] is True
        assert rules["limit"] == 5
        revisions = client.get("/api/memory/policies/revisions?status=active_caution").json()
        assert revisions["filter_status"] == "active_caution"
        assert client.post("/api/memory/policies/revisions/rev_1/activate").json()["activated"] is True
        assert client.post("/api/memory/policies/revisions/rev_1/reject").json()["rejected"] is True


def test_memory_router_maps_invalid_resources_to_http_errors() -> None:
    with _client() as client:
        assert client.get("/api/memory/symbols/BAD").status_code == 400
        assert client.get("/api/memory/blocks/bad").status_code == 400
        assert client.post("/api/memory/policies/revisions/missing/activate").status_code == 404
        assert client.post("/api/memory/policies/revisions/missing/reject").status_code == 404
