from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.symbols import SymbolRouteDeps, build_symbols_router


class _FakeFundamentals:
    def __init__(self) -> None:
        self.collected: list[dict[str, Any]] = []
        self.latest_payload: dict[str, Any] | None = {
            "status": "ok",
            "symbol": "005930",
            "score": {"label": "fair"},
        }

    async def collect_symbols(
        self,
        symbols: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        self.collected.append({"symbols": symbols, "force": force})
        return {"status": "ok", "target_count": len(symbols)}

    def latest(self, symbol: str) -> dict[str, Any] | None:
        if symbol == "005930":
            return self.latest_payload
        return None

    def status(self) -> dict[str, Any]:
        return {"status": "ok", "snapshot_count": 3}


class _FakeAnalysis:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def special_watch(self) -> dict[str, Any]:
        self.calls.append({"method": "special_watch"})
        return {"status": "ok", "items": [{"symbol": "033790"}]}

    async def run(
        self,
        symbol: str,
        *,
        trigger: str,
        force_collect: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "run",
                "symbol": symbol,
                "trigger": trigger,
                "force_collect": force_collect,
            }
        )
        return {"status": "ok", "symbol": symbol, "trigger": trigger}

    def history(self, symbol: str, *, limit: int) -> dict[str, Any]:
        self.calls.append({"method": "history", "symbol": symbol, "limit": limit})
        return {"status": "ok", "symbol": symbol, "limit": limit}


def _client(
    fundamentals: _FakeFundamentals,
    analysis: _FakeAnalysis,
    *,
    strategy_targets: list[str] | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_symbols_router(
            SymbolRouteDeps(
                require_admin_auth=lambda: None,
                fundamentals_service=lambda: fundamentals,
                analysis_service=lambda: analysis,
                symbols_from_csv=lambda value: [
                    item.strip()
                    for item in str(value or "").replace(";", ",").split(",")
                    if item.strip().isdigit() and len(item.strip()) == 6
                ],
                strategy_fundamental_targets=lambda: strategy_targets or ["005930"],
                is_krx_symbol=lambda value: str(value or "").isdigit()
                and len(str(value or "").strip()) == 6,
                max_symbols_per_collect=lambda: 2,
            )
        )
    )
    return TestClient(app)


def test_symbol_fundamentals_collect_uses_explicit_list_and_clamps_reported_targets() -> None:
    fundamentals = _FakeFundamentals()
    analysis = _FakeAnalysis()

    with _client(fundamentals, analysis) as client:
        response = client.post(
            "/api/symbols/fundamentals/collect",
            json={"symbols": ["005930", " 000660 ", "not-code"], "force": True},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "target_count": 3,
        "target_source": "explicit",
        "target_symbols": ["005930", "000660"],
    }
    assert fundamentals.collected == [
        {"symbols": ["005930", "000660", "not-code"], "force": True}
    ]


def test_symbol_fundamentals_collect_uses_strategy_targets_when_symbols_missing() -> None:
    fundamentals = _FakeFundamentals()
    analysis = _FakeAnalysis()

    with _client(fundamentals, analysis, strategy_targets=["005930", "000660"]) as client:
        response = client.post("/api/symbols/fundamentals/collect", json={})

    assert response.status_code == 200
    assert response.json()["target_source"] == "strategy_targets"
    assert response.json()["target_symbols"] == ["005930", "000660"]
    assert fundamentals.collected == [
        {"symbols": ["005930", "000660"], "force": False}
    ]


def test_symbol_fundamentals_status_delegates_to_service() -> None:
    fundamentals = _FakeFundamentals()
    analysis = _FakeAnalysis()

    with _client(fundamentals, analysis) as client:
        response = client.get("/api/symbols/fundamentals/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "snapshot_count": 3}


def test_symbol_fundamentals_latest_validates_krx_symbol_and_reports_missing() -> None:
    fundamentals = _FakeFundamentals()
    analysis = _FakeAnalysis()

    with _client(fundamentals, analysis) as client:
        latest = client.get("/api/symbols/005930/fundamentals")
        missing = client.get("/api/symbols/000660/fundamentals")
        invalid = client.get("/api/symbols/ABC/fundamentals")

    assert latest.status_code == 200
    assert latest.json()["score"]["label"] == "fair"
    assert missing.status_code == 200
    assert missing.json() == {"status": "missing", "symbol": "000660"}
    assert invalid.status_code == 400


def test_symbol_analysis_routes_delegate_and_clamp_history_limit() -> None:
    fundamentals = _FakeFundamentals()
    analysis = _FakeAnalysis()

    with _client(fundamentals, analysis) as client:
        watch = client.get("/api/symbols/special-watch")
        run = client.post(
            "/api/symbols/033790/analysis/run",
            json={"trigger": "manual", "force_collect": False},
        )
        history = client.get("/api/symbols/033790/analysis/history?limit=500")

    assert watch.status_code == 200
    assert watch.json()["items"][0]["symbol"] == "033790"
    assert run.status_code == 200
    assert history.status_code == 200
    assert history.json()["limit"] == 50
    assert analysis.calls == [
        {"method": "special_watch"},
        {
            "method": "run",
            "symbol": "033790",
            "trigger": "manual",
            "force_collect": False,
        },
        {"method": "history", "symbol": "033790", "limit": 50},
    ]
