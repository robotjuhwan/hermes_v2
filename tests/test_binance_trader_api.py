from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main
from tradecraft.services.unavailable_services import (
    UnavailableCryptoMarketResearchService,
)


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def test_binance_blocks_status_requires_admin(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")

    with TestClient(main.app) as client:
        response = client.get("/api/binance/blocks/status")

    assert response.status_code == 401


def test_binance_blocks_status_returns_snapshot(monkeypatch) -> None:
    async def fake_snapshot() -> dict:
        return {
            "status": "ok",
            "blocks": [],
            "execution": {"spot_mode": "paper", "futures_mode": "paper"},
            "risk": {
                "account_risk_pct": 0.25,
                "max_symbol_exposure_pct": 25.0,
                "min_reward_risk": 1.3,
            },
            "performance": {
                "sample_count": 2,
                "avg_r_multiple": 0.4,
                "win_rate_pct": 50.0,
            },
        }

    monkeypatch.setattr(main.binance_block_trader, "snapshot", fake_snapshot)
    monkeypatch.setattr(
        main.investment_memory_service,
        "validation_repair_ops_summary",
        lambda **_: {
            "status": "needs_repair",
            "scope": "binance",
            "backlog_count": 1,
            "top_backlog": [{"discipline_id": "walk_forward_analysis"}],
        },
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/binance/blocks/status?compact=0",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    assert response.json()["execution"]["spot_mode"] == "paper"
    assert response.json()["risk"]["account_risk_pct"] == 0.25
    assert response.json()["performance"]["sample_count"] == 2
    assert response.json()["validation_repair_ops"]["status"] == "needs_repair"
    assert (
        response.json()["validation_repair_ops"]["top_backlog"][0]["discipline_id"]
        == "walk_forward_analysis"
    )


def test_binance_blocks_status_defaults_to_compact_snapshot(monkeypatch) -> None:
    async def fake_snapshot() -> dict:
        raise AssertionError("status endpoint should not use full Binance snapshot by default")

    async def fake_compact_snapshot() -> dict:
        return {
            "status": "ok",
            "compact": True,
            "active_blocks": [{"block_id": "b1", "symbol": "BTCUSDT"}],
            "manager_runs": [],
        }

    monkeypatch.setattr(main.binance_block_trader, "snapshot", fake_snapshot)
    monkeypatch.setattr(
        main.binance_block_trader,
        "snapshot_compact",
        fake_compact_snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        main.investment_memory_service,
        "validation_repair_ops_summary",
        lambda **_: {"status": "ok", "top_backlog": []},
    )
    monkeypatch.setattr(
        main,
        "_build_ops_readiness",
        lambda: (_ for _ in ()).throw(
            AssertionError("Binance status must not build full ops readiness")
        ),
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/binance/blocks/status",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    assert response.json()["compact"] is True
    assert response.json()["active_blocks"][0]["symbol"] == "BTCUSDT"


def test_binance_blocks_status_compact_uses_compact_snapshot(monkeypatch) -> None:
    async def fake_compact_snapshot() -> dict:
        return {
            "status": "ok",
            "compact": True,
            "active_blocks": [{"block_id": "b1", "symbol": "SOLUSDT"}],
            "block_history": [{"block_id": "closed-1", "symbol": "BTCUSDT", "status": "closed"}],
            "lane_allocation": {"items": [{"lane": "short", "block_count": 1}]},
            "manager_runs": [
                {
                    "id": 7,
                    "status": "error",
                    "prompt": {"large": "payload"},
                    "response": {
                        "error": "timeout",
                        "hold_decision": {
                            "summary": "관망",
                            "reasons": ["timeout 이후 재시도 대기"],
                        },
                    },
                    "actions": {},
                }
            ],
        }

    monkeypatch.setattr(
        main.binance_block_trader,
        "snapshot_compact",
        fake_compact_snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        main.investment_memory_service,
        "validation_repair_ops_summary",
        lambda **_: {
            "status": "needs_repair",
            "scope": "binance",
            "backlog_count": 1,
            "top_backlog": [{"discipline_id": "cost_simulation"}],
        },
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/binance/blocks/status?compact=1",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["compact"] is True
    assert payload["active_blocks"][0]["symbol"] == "SOLUSDT"
    assert "prompt" not in payload["manager_runs"][0]
    assert "response" not in payload["manager_runs"][0]
    assert payload["manager_runs"][0]["hold_decision"]["summary"] == "관망"
    assert payload["block_history"][0]["block_id"] == "closed-1"
    assert payload["lane_allocation"]["items"][0]["lane"] == "short"
    assert payload["validation_repair_ops"]["top_backlog"][0]["discipline_id"] == (
        "cost_simulation"
    )


def test_binance_manager_run_once(monkeypatch) -> None:
    async def fake_run() -> dict:
        return {"status": "ok", "created_blocks": [{"block_id": "b1"}]}

    monkeypatch.setattr(main.binance_block_trader, "run_manager_once", fake_run)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/binance/blocks/manager/run-once",
            headers=_admin_headers(monkeypatch),
            json={"confirm_live_manager_run": True},
        )

    assert response.status_code == 200
    assert response.json()["created_blocks"][0]["block_id"] == "b1"


def test_binance_kill_switch_routes(monkeypatch) -> None:
    with TestClient(main.app) as client:
        enabled = client.post(
            "/api/binance/blocks/kill-switch",
            headers=_admin_headers(monkeypatch),
            json={"reason": "test"},
        )
        released = client.post(
            "/api/binance/blocks/kill-switch/release",
            headers=_admin_headers(monkeypatch),
            json={"reason": "test_release"},
        )

    assert enabled.status_code == 200
    assert enabled.json()["kill_switch"]["enabled"] is True
    assert released.status_code == 200
    assert released.json()["kill_switch"]["enabled"] is False


def test_crypto_research_status_requires_admin(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")

    with TestClient(main.app) as client:
        response = client.get("/api/crypto/research/status")

    assert response.status_code == 401


def test_crypto_research_routes_use_service(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _FakeCryptoResearchService:
        def status(self) -> dict:
            calls.append(("status", {}))
            return {"status": "ok", "snapshot_count": 2, "candidate_count": 1}

        def latest_context(
            self,
            symbols: list[str] | None = None,
            limit: int = 20,
        ) -> dict:
            calls.append(("context", {"symbols": symbols, "limit": limit}))
            return {
                "status": "ok",
                "candidates": [{"symbol": "BTCUSDT", "score": 80}],
                "symbol_notes": {"BTCUSDT": {"summary_md": "테스트"}},
            }

        async def collect_market_structure(self, symbols: list[str]) -> dict:
            calls.append(("collect", symbols))
            return {"status": "ok", "symbols": symbols}

        async def run_research_once(
            self,
            symbols: list[str] | None = None,
        ) -> dict:
            calls.append(("run", symbols))
            return {"status": "ok", "symbols": symbols}

    monkeypatch.setattr(
        main,
        "crypto_market_research_service",
        _FakeCryptoResearchService(),
    )

    with TestClient(main.app) as client:
        headers = _admin_headers(monkeypatch)
        status = client.get("/api/crypto/research/status", headers=headers)
        context = client.get(
            "/api/crypto/research/context?symbols=btcusdt,ethusdt&limit=2",
            headers=headers,
        )
        collect = client.post(
            "/api/crypto/research/collect",
            headers=headers,
            json={"symbols": ["btcusdt", "ETHUSDT"]},
        )
        collect_default = client.post(
            "/api/crypto/research/collect",
            headers=headers,
            json={},
        )
        run_once = client.post(
            "/api/crypto/research/run-once",
            headers=headers,
            json={"symbols": ["solusdt"]},
        )

    assert status.status_code == 200
    assert status.json()["status"] == "ok"
    assert context.status_code == 200
    assert context.json()["candidates"][0]["symbol"] == "BTCUSDT"
    assert collect.status_code == 200
    assert collect.json()["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert collect_default.status_code == 200
    assert collect_default.json()["symbols"]
    assert run_once.status_code == 200
    assert run_once.json()["symbols"] == ["SOLUSDT"]
    assert ("context", {"symbols": ["BTCUSDT", "ETHUSDT"], "limit": 2}) in calls


def test_unavailable_crypto_research_context_keeps_regime_shape(monkeypatch) -> None:
    service = UnavailableCryptoMarketResearchService(
        reason="missing import",
        db_path=main.settings.crypto_market_research_db_path,
    )
    monkeypatch.setattr(main, "crypto_market_research_service", service)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/crypto/research/context",
            headers=_admin_headers(monkeypatch),
        )

    assert response.status_code == 200
    assert response.json()["market_regime"]["regime"] == "unknown"
    assert response.json()["items"] == []


def test_crypto_alpha_routes_require_admin_and_use_service(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _FakeCryptoAlphaService:
        def status(self) -> dict:
            calls.append(("status", {}))
            return {"status": "ok", "events": 2, "outcomes": 1}

        def context_pack(
            self,
            *,
            symbols: list[str] | None = None,
            limit: int = 12,
        ) -> dict:
            calls.append(("context", {"symbols": symbols, "limit": limit}))
            return {
                "status": "ok",
                "scope": "binance_crypto_alpha",
                "events": [{"symbol": "BTCUSDT"}],
                "limit": limit,
            }

        async def collect_once(self) -> dict:
            calls.append(("collect", {}))
            return {"status": "ok", "created_events": 1}

        async def label_due_outcomes(self) -> dict:
            calls.append(("outcomes", {}))
            return {"status": "ok", "labeled": 1}

    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    monkeypatch.setattr(main, "crypto_alpha_service", _FakeCryptoAlphaService())

    with TestClient(main.app) as client:
        unauth = client.get("/api/crypto/alpha/status")
        headers = {"Authorization": "Bearer test-admin"}
        status = client.get("/api/crypto/alpha/status", headers=headers)
        context = client.get(
            "/api/crypto/alpha/context?symbols=btcusdt&limit=4",
            headers=headers,
        )
        collect = client.post("/api/crypto/alpha/collect", headers=headers)
        outcomes = client.post("/api/crypto/alpha/outcomes/run-once", headers=headers)

    assert unauth.status_code == 401
    assert status.status_code == 200
    assert status.json()["events"] == 2
    assert context.status_code == 200
    assert context.json()["scope"] == "binance_crypto_alpha"
    assert collect.status_code == 200
    assert outcomes.status_code == 200
    assert ("context", {"symbols": ["BTCUSDT"], "limit": 4}) in calls
