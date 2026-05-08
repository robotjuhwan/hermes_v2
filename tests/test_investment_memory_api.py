from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main


def test_investment_memory_api_routes(monkeypatch) -> None:
    class FakeMemoryService:
        def status(self) -> dict:
            return {"status": "ok", "active_policies": []}

        def today(self) -> dict:
            return {"status": "ok", "trading_day": "2026-05-08", "journals": []}

        def symbol_memory(self, symbol: str) -> dict:
            return {"status": "ok", "symbol": symbol, "content": "symbol memory"}

        def block_memory(self, block_id: str) -> dict:
            return {"status": "ok", "block_id": block_id, "content": "block memory"}

        def initialize(self, *, force: bool = False) -> dict:
            return {"status": "ok", "force": force}

        async def run_ritual(
            self,
            *,
            slot: str,
            context: dict,
            send_telegram: bool = False,
            force: bool = False,
        ) -> dict:
            return {
                "status": "ok",
                "slot": slot,
                "send_telegram": send_telegram,
                "force": force,
                "context": context,
            }

        async def run_update(self, *, context: dict, force: bool = False) -> dict:
            return {"status": "ok", "slot": "weekly", "force": force, "context": context}

    monkeypatch.setattr(main, "investment_memory_service", FakeMemoryService())

    async def fake_context() -> dict:
        return {"account": {"cash_krw": 1_000_000}}

    monkeypatch.setattr(main, "_build_investment_memory_context", fake_context)

    with TestClient(main.app) as client:
        status = client.get("/api/memory/status")
        today = client.get("/api/memory/today")
        symbol = client.get("/api/memory/symbols/005930")
        block = client.get("/api/memory/blocks/blk_1")
        init = client.post("/api/memory/init", json={"force": True})
        ritual = client.post(
            "/api/memory/rituals/run-once",
            json={"slot": "midday", "send_telegram": True, "force": False},
        )
        update = client.post("/api/memory/update/run-once", json={"force": True})

    assert status.status_code == 200
    assert status.json()["status"] == "ok"
    assert today.json()["trading_day"] == "2026-05-08"
    assert symbol.json()["content"] == "symbol memory"
    assert block.json()["block_id"] == "blk_1"
    assert init.json()["force"] is True
    assert ritual.json()["slot"] == "midday"
    assert ritual.json()["send_telegram"] is True
    assert update.json()["slot"] == "weekly"
