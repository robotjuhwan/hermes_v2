from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.market import MarketRouteDeps, build_market_router


class _MarketJudgment:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def clock(self) -> dict[str, Any]:
        self.calls.append(("clock", {}))
        return {"session": "regular"}

    def latest_quotes(
        self,
        *,
        limit: int,
        symbols: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("quotes", {"limit": limit, "symbols": symbols}))
        return {"status": "ok", "limit": limit, "symbols": symbols}

    def latest_account(self) -> dict[str, Any]:
        self.calls.append(("account", {}))
        return {"status": "ok", "cash_krw": 1000}

    def latest_judgment(self) -> dict[str, Any]:
        self.calls.append(("latest_judgment", {}))
        return {"status": "ok", "judgments": []}

    def schedule(self) -> dict[str, Any]:
        self.calls.append(("schedule", {}))
        return {"status": "ok", "interval_sec": 1800}

    async def run_once(self, *, use_llm: bool) -> dict[str, Any]:
        self.calls.append(("run_once", {"use_llm": use_llm}))
        return {"status": "ok", "use_llm": use_llm}


class _MarketPulse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def latest(self) -> dict[str, Any]:
        self.calls.append(("latest", {}))
        return {"status": "ok", "pulse": "latest"}

    def history(self, *, limit: int) -> dict[str, Any]:
        self.calls.append(("history", {"limit": limit}))
        return {"status": "ok", "limit": limit}

    async def collect(self, *, clock: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("collect", {"clock": clock}))
        return {"status": "ok", "clock": clock}


def _client(
    *,
    market_judgment: _MarketJudgment,
    market_pulse: _MarketPulse,
    kis_primary_ready: bool = True,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_market_router(
            MarketRouteDeps(
                require_admin_auth=lambda: None,
                build_dashboard_payload=lambda include_telegram=True, force_refresh=False: {
                    "status": "ok",
                    "include_telegram": include_telegram,
                    "force_refresh": force_refresh,
                },
                market_judgment_engine=lambda: market_judgment,
                market_pulse_service=lambda: market_pulse,
                kis_primary_ready=lambda: kis_primary_ready,
            )
        )
    )
    return TestClient(app)


def test_market_router_serves_dashboard_quotes_and_pulse() -> None:
    market_judgment = _MarketJudgment()
    market_pulse = _MarketPulse()

    with _client(
        market_judgment=market_judgment,
        market_pulse=market_pulse,
    ) as client:
        dashboard = client.get("/api/dashboard")
        legacy_portfolio = client.get("/api/portfolio")
        quotes = client.get("/api/market/quotes?limit=999&symbols=005930,abc,000660,005930")
        pulse_status = client.get("/api/market/pulse/status")
        pulse = client.post("/api/market/pulse/run-once")

    assert dashboard.json()["include_telegram"] is True
    assert legacy_portfolio.status_code == 200
    assert legacy_portfolio.json() == dashboard.json()
    assert quotes.json()["limit"] == 300
    assert quotes.json()["symbols"] == ["005930", "000660"]
    assert pulse_status.json()["pulse"] == "latest"
    assert pulse.json()["clock"] == {"session": "regular"}
    assert ("quotes", {"limit": 300, "symbols": ["005930", "000660"]}) in (
        market_judgment.calls
    )


def test_market_router_forwards_dashboard_force_refresh_query() -> None:
    calls: list[dict[str, Any]] = []
    app = FastAPI()

    def build_dashboard_payload(
        *,
        include_telegram: bool = True,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        calls.append(
            {
                "include_telegram": include_telegram,
                "force_refresh": force_refresh,
            }
        )
        return {"status": "ok", "force_refresh": force_refresh}

    app.include_router(
        build_market_router(
            MarketRouteDeps(
                require_admin_auth=lambda: None,
                build_dashboard_payload=build_dashboard_payload,
                market_judgment_engine=lambda: _MarketJudgment(),
                market_pulse_service=lambda: _MarketPulse(),
                kis_primary_ready=lambda: True,
            )
        )
    )

    with TestClient(app) as client:
        refresh_response = client.get("/api/dashboard?refresh=true")
        force_refresh_response = client.get("/api/dashboard?force_refresh=true")

    assert refresh_response.status_code == 200
    assert refresh_response.json()["force_refresh"] is True
    assert force_refresh_response.status_code == 200
    assert force_refresh_response.json()["force_refresh"] is True
    assert calls == [
        {"include_telegram": True, "force_refresh": True},
        {"include_telegram": True, "force_refresh": True},
    ]


@pytest.mark.parametrize("use_llm", [True, False])
def test_market_router_runs_judgment_when_kis_is_ready(use_llm: bool) -> None:
    market_judgment = _MarketJudgment()
    market_pulse = _MarketPulse()

    with _client(
        market_judgment=market_judgment,
        market_pulse=market_pulse,
        kis_primary_ready=True,
    ) as client:
        response = client.post(f"/api/market/judgments/run-once?use_llm={str(use_llm).lower()}")

    assert response.status_code == 200
    assert response.json()["use_llm"] is use_llm


def test_market_router_rejects_judgment_run_when_kis_is_not_ready() -> None:
    with _client(
        market_judgment=_MarketJudgment(),
        market_pulse=_MarketPulse(),
        kis_primary_ready=False,
    ) as client:
        response = client.post("/api/market/judgments/run-once")

    assert response.status_code == 400
    assert response.json()["detail"] == "kis primary account not configured"
