from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tradecraft import main
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def test_investment_memory_api_routes(monkeypatch) -> None:
    class FakeMemoryService:
        def status(self, scope: str = "", compact: bool = False) -> dict:
            return {
                "status": "ok",
                "scope": scope,
                "compact": compact,
                "active_policies": [],
                "decision_skill_status": {"count": 4, "missing": []},
                "decision_skills": {
                    "block_manager": {
                        "version": "jue.block_manager.v1",
                        "preview": "block skill",
                    }
                },
            }

        def today(self, scope: str = "", compact: bool = False) -> dict:
            return {
                "status": "ok",
                "scope": scope,
                "compact": compact,
                "trading_day": "2026-05-08",
                "journals": [],
                "decision_skill_status": {"count": 4, "missing": []},
                "decision_skills": {
                    "block_manager": {
                        "version": "jue.block_manager.v1",
                        "preview": "block skill",
                    }
                },
            }

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

        def seed_current(self, *, context: dict, force: bool = False) -> dict:
            return {"status": "ok", "slot": "seed", "force": force, "context": context}

        def run_due_reflections(self, *, context: dict, force: bool = False) -> dict:
            return {
                "status": "ok",
                "created_count": 1,
                "force": force,
                "context": context,
            }

        def policy_scorecards(self, *, limit: int = 30) -> dict:
            return {"status": "ok", "limit": limit, "items": []}

        def policy_rules(self, *, limit: int = 30, active_only: bool = False) -> dict:
            return {
                "status": "ok",
                "limit": limit,
                "active_only": active_only,
                "items": [],
            }

        def latest_historical_replay(self, period_type: str) -> dict:
            return {
                "status": "ok",
                "period_type": period_type,
                "period_key": "2026-W21",
            }

        def historical_replays(self, *, period_type: str = "", limit: int = 12) -> dict:
            return {
                "status": "ok",
                "period_type": period_type,
                "limit": limit,
                "items": [{"period_key": "2026-W21"}],
            }

        async def run_historical_replay(
            self,
            *,
            period_type: str,
            context: dict,
            force: bool = False,
        ) -> dict:
            return {
                "status": "ok",
                "period_type": period_type,
                "force": force,
                "context": context,
            }

    monkeypatch.setattr(main, "investment_memory_service", FakeMemoryService())

    async def fake_context() -> dict:
        return {"account": {"cash_krw": 1_000_000}}

    monkeypatch.setattr(main, "_build_investment_memory_context", fake_context)

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        status = client.get("/api/memory/status", headers=headers)
        status_compact = client.get(
            "/api/memory/status?compact=true",
            headers=headers,
        )
        today = client.get("/api/memory/today", headers=headers)
        today_compact = client.get("/api/memory/today?compact=true", headers=headers)
        symbol = client.get("/api/memory/symbols/005930", headers=headers)
        block = client.get("/api/memory/blocks/blk_1", headers=headers)
        init = client.post("/api/memory/init", json={"force": True}, headers=headers)
        ritual = client.post(
            "/api/memory/rituals/run-once",
            json={"slot": "midday", "send_telegram": True, "force": False},
            headers=headers,
        )
        update = client.post(
            "/api/memory/update/run-once",
            json={"force": True},
            headers=headers,
        )
        seed = client.post(
            "/api/memory/seed-current",
            json={"force": True},
            headers=headers,
        )
        reflections = client.post(
            "/api/memory/reflections/run-due",
            json={"force": True},
            headers=headers,
        )
        scorecards = client.get(
            "/api/memory/policies/scorecards?limit=5",
            headers=headers,
        )
        rules = client.get(
            "/api/memory/policies/rules?limit=5&active_only=true",
            headers=headers,
        )
        latest_replay = client.get(
            "/api/memory/replays/latest?period_type=weekly",
            headers=headers,
        )
        replay_history = client.get(
            "/api/memory/replays/history?period_type=weekly&limit=5",
            headers=headers,
        )
        replay_run = client.post(
            "/api/memory/replays/run-once",
            json={"period_type": "weekly", "force": True},
            headers=headers,
        )

    assert status.status_code == 200
    assert status.json()["status"] == "ok"
    assert status_compact.status_code == 200
    assert status_compact.json()["compact"] is True
    assert today.json()["trading_day"] == "2026-05-08"
    assert today_compact.json()["compact"] is True
    assert symbol.json()["content"] == "symbol memory"
    assert block.json()["block_id"] == "blk_1"
    assert init.json()["force"] is True
    assert ritual.json()["slot"] == "midday"
    assert ritual.json()["send_telegram"] is True
    assert update.json()["slot"] == "weekly"
    assert seed.json()["slot"] == "seed"
    assert reflections.json()["created_count"] == 1
    assert scorecards.json()["limit"] == 5
    assert rules.json()["active_only"] is True
    assert latest_replay.json()["period_key"] == "2026-W21"
    assert replay_history.json()["items"][0]["period_key"] == "2026-W21"
    assert replay_run.json()["period_type"] == "weekly"
    assert replay_run.json()["context"]["account"]["cash_krw"] == 1_000_000


def test_memory_review_and_revision_api_routes(monkeypatch) -> None:
    class FakeMemoryService:
        def latest_period_review(self, period_type: str) -> dict:
            return {"status": "ok", "period_type": period_type, "period_key": "2026-W21"}

        def period_reviews(self, *, period_type: str = "", limit: int = 12) -> dict:
            return {
                "status": "ok",
                "period_type": period_type,
                "limit": limit,
                "items": [{"period_key": "2026-W21"}],
            }

        async def run_period_review(
            self,
            *,
            period_type: str,
            context: dict,
            force: bool = False,
        ) -> dict:
            return {
                "status": "ok",
                "period_type": period_type,
                "force": force,
                "context": context,
            }

        def policy_revisions(self, *, status: str = "", limit: int = 30) -> dict:
            return {
                "status": "ok",
                "filter_status": status,
                "limit": limit,
                "items": [{"revision_id": "rev_1"}],
            }

        def activate_policy_revision(self, revision_id: str) -> dict:
            return {"status": "ok", "revision_id": revision_id, "activated": True}

        def reject_policy_revision(self, revision_id: str) -> dict:
            return {"status": "ok", "revision_id": revision_id, "rejected": True}

    monkeypatch.setattr(main, "investment_memory_service", FakeMemoryService())

    async def fake_context() -> dict:
        return {"account": {"cash_krw": 1_000_000}}

    monkeypatch.setattr(main, "_build_investment_memory_context", fake_context)

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        latest = client.get(
            "/api/memory/reviews/latest?period_type=weekly",
            headers=headers,
        )
        history = client.get(
            "/api/memory/reviews/history?period_type=weekly",
            headers=headers,
        )
        run = client.post(
            "/api/memory/reviews/run-once",
            json={"period_type": "monthly", "force": True},
            headers=headers,
        )
        revisions = client.get(
            "/api/memory/policies/revisions?status=active_caution",
            headers=headers,
        )
        activate = client.post(
            "/api/memory/policies/revisions/rev_1/activate",
            headers=headers,
        )
        reject = client.post(
            "/api/memory/policies/revisions/rev_1/reject",
            headers=headers,
        )

    assert latest.status_code == 200
    assert latest.json()["period_key"] == "2026-W21"
    assert history.json()["items"][0]["period_key"] == "2026-W21"
    assert run.json()["period_type"] == "monthly"
    assert run.json()["context"]["account"]["cash_krw"] == 1_000_000
    assert revisions.json()["filter_status"] == "active_caution"
    assert activate.json()["activated"] is True
    assert reject.json()["rejected"] is True


def test_memory_ritual_context_includes_daily_discovery(monkeypatch) -> None:
    class FakeMemoryService:
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
                "context": context,
                "send_telegram": send_telegram,
                "force": force,
            }

    async def fake_snapshot() -> dict:
        return {"status": "ok", "account": {"cash_krw": 1_000_000}, "blocks": []}

    monkeypatch.setattr(main, "investment_memory_service", FakeMemoryService())
    monkeypatch.setattr(main.kis_block_trader, "snapshot", fake_snapshot)
    monkeypatch.setattr(main.market_judgment_engine, "latest_judgment", lambda: {"status": "ok"})
    monkeypatch.setattr(main.market_judgment_engine, "clock", lambda: {"session": "regular"})
    monkeypatch.setattr(main, "_read_strategy_research_feed", lambda: {"status": "ok"})
    monkeypatch.setattr(main.naver_report_repository, "status", lambda: {"status": "ok"})
    monkeypatch.setattr(
        main.strategy_intelligence,
        "build_candidates",
        lambda **_: {"status": "ok", "candidates": [], "sources": []},
    )
    monkeypatch.setattr(main.symbol_fundamentals_service, "status", lambda: {"status": "ok"})
    monkeypatch.setattr(main, "_llm_usage_summary", lambda: {"status": "ok"})
    monkeypatch.setattr(
        main.daily_discovery_service,
        "latest_context",
        lambda *, limit=10: {
            "status": "ok",
            "trading_day": "2026-05-21",
            "items": [{"symbol": "005930", "summary": "daily study"}],
            "block_candidates": [{"symbol": "005930"}],
        },
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/memory/rituals/run-once",
            json={"slot": "pre_open", "force": True},
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["context"]["daily_discovery"]["status"] == "ok"
    assert payload["context"]["daily_discovery"]["items"][0]["symbol"] == "005930"


def test_memory_status_exposes_decision_skill_status(
    monkeypatch,
    tmp_path: Path,
) -> None:
    strategy = tmp_path / "strategy_krx.md"
    strategy.write_text("# 전략\n\n- 블록 손절 약속을 우선한다.\n", encoding="utf-8")
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(strategy),
        ),
    )
    monkeypatch.setattr(main, "investment_memory_service", service)

    with TestClient(main.app) as client:
        response = client.get("/api/memory/status", headers=_admin_headers(monkeypatch))

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_skill_status"]["count"] >= 4
    assert "block_manager" in payload["decision_skills"]
    assert (
        payload["decision_skills"]["block_manager"]["version"]
        == "jue.block_manager.v1"
    )
    assert "preview" in payload["decision_skills"]["block_manager"]
    assert "content_md" not in payload["decision_skills"]["block_manager"]


def test_memory_today_exposes_preview_only_decision_skills(
    monkeypatch,
    tmp_path: Path,
) -> None:
    strategy = tmp_path / "strategy_krx.md"
    strategy.write_text("# 전략\n\n- 블록 손절 약속을 우선한다.\n", encoding="utf-8")
    service = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(strategy),
        ),
    )
    monkeypatch.setattr(main, "investment_memory_service", service)

    with TestClient(main.app) as client:
        response = client.get("/api/memory/today", headers=_admin_headers(monkeypatch))

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_skill_status"]["count"] >= 4
    assert "block_manager" in payload["decision_skills"]
    block_manager = payload["decision_skills"]["block_manager"]
    assert block_manager["version"] == "jue.block_manager.v1"
    assert "preview" in block_manager
    assert "content_md" not in block_manager
    nested_block_manager = payload["context_pack"]["decision_skills"]["block_manager"]
    assert nested_block_manager["version"] == "jue.block_manager.v1"
    assert "preview" in nested_block_manager
    assert "content_md" not in nested_block_manager
