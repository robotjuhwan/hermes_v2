from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import tradecraft.main as tradecraft_main
from tradecraft.main import (
    app,
    research_reader,
    binance,
    bithumb,
    fx_rates,
    kis_primary,
    kis_secondary,
    settings,
    upbit,
)
from tradecraft.runtime.state_store import RuntimeStateStore


@pytest.fixture(autouse=True)
def _mock_fx_snapshot(monkeypatch):
    async def fake_get_snapshot() -> dict:
        return {
            "usdt_krw": 1400.0,
            "usd_krw": 1350.0,
            "usdt_source": "test",
            "usd_source": "test",
            "status": "ok",
            "fetched_at": "2026-02-15T00:00:00+00:00",
        }

    monkeypatch.setattr(fx_rates, "get_snapshot", fake_get_snapshot)


def test_health_and_dashboard(monkeypatch) -> None:
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert "runtime_connected" in health.json()
        assert "runtime_runner_alive" in health.json()
        assert "llm_bridge_mode" in health.json()
        assert "llm_bridge_ready" in health.json()
        assert "research_runner_alive" in health.json()
        assert "kis_trader_runner_alive" in health.json()
        assert "naver_reports_runner_alive" in health.json()

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert "venues" in payload
        assert "fx" in payload
        assert payload["fx"]["status"] == "ok"
        assert "portfolio_total_krw" in payload
        assert "sessions" in payload
        assert len(payload["venues"]) >= 4
        assert all("assets" in venue for venue in payload["venues"])
        venue_ids = {venue["id"] for venue in payload["venues"]}
        assert all("venue_id" in session for session in payload["sessions"])
        assert all(session["venue_id"] in venue_ids for session in payload["sessions"])
        assert any(session["mode"] == "short_term" for session in payload["sessions"])
        assert any(
            session["mode"] == "mid_long_term" for session in payload["sessions"]
        )


def test_health_distinguishes_runner_process_from_covered_service(
    monkeypatch,
) -> None:
    def fake_runner_process_status(key: str) -> dict:
        alive = key == "intelligence"
        return {
            "key": key,
            "label": key,
            "status": "running" if alive else "stopped",
            "alive": alive,
            "pid": 123 if alive else None,
            "pid_file": f".runtime/pids/{key}.pid",
            "pid_file_pid": 123 if alive else None,
            "pid_file_status": "ok" if alive else "missing",
            "matched_count": 1 if alive else 0,
            "matches": [],
        }

    monkeypatch.setattr(
        tradecraft_main,
        "runner_process_status",
        fake_runner_process_status,
    )

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["intelligence_runner_alive"] is True
    assert payload["research_runner_alive"] is False
    assert payload["research_service_alive"] is True
    assert payload["naver_reports_runner_alive"] is False
    assert payload["naver_reports_service_alive"] is True
    assert payload["runner_processes"]["research"]["status"] == "covered"


def test_symbol_fundamentals_api_routes(monkeypatch) -> None:
    class FakeSymbolFundamentalsService:
        def __init__(self) -> None:
            self.collected_symbols: list[str] = []

        def latest(self, symbol: str) -> dict:
            return {
                "status": "ok",
                "symbol": symbol,
                "valuation": {"per": 7.0, "pbr": 0.9, "industry_per": 28.9},
                "score": {"label": "undervalued", "undervalued_score": 82},
                "financials": [],
            }

        async def collect_symbols(
            self,
            symbols: list[str],
            *,
            force: bool = False,
        ) -> dict:
            self.collected_symbols = list(symbols)
            return {
                "status": "ok",
                "target_count": len(symbols),
                "force": force,
                "items": [{"symbol": symbol, "status": "ok"} for symbol in symbols],
            }

        def status(self) -> dict:
            return {
                "status": "ok",
                "db_path": ".runtime/symbol_fundamentals.db",
                "total_snapshots": 1,
                "error_count": 0,
                "latest_crawled_at": "2026-05-06T00:00:00+00:00",
            }

    fake_service = FakeSymbolFundamentalsService()
    monkeypatch.setattr(
        tradecraft_main,
        "symbol_fundamentals_service",
        fake_service,
    )

    with TestClient(app) as client:
        latest = client.get("/api/symbols/005930/fundamentals")
        assert latest.status_code == 200
        assert latest.json()["score"]["label"] == "undervalued"

        invalid = client.get("/api/symbols/ABC/fundamentals")
        assert invalid.status_code == 400

        collected = client.post(
            "/api/symbols/fundamentals/collect",
            json={"symbols": ["005930", "000660"], "force": True},
        )
        assert collected.status_code == 200
        assert collected.json()["target_count"] == 2
        assert fake_service.collected_symbols == ["005930", "000660"]

        status = client.get("/api/reports/status")
        assert status.status_code == 200
        assert status.json()["fundamentals"]["total_snapshots"] == 1


def test_market_judgment_api_routes(monkeypatch) -> None:
    class FakeMarketJudgmentEngine:
        def clock(self) -> dict:
            return {"status": "ok", "session": "regular", "is_market_open": True}

        def latest_quotes(self, limit: int = 100) -> dict:
            return {
                "status": "ok",
                "count": 1,
                "limit": limit,
                "items": [{"symbol": "005930", "price": 76000}],
            }

        def latest_account(self) -> dict:
            return {
                "status": "ok",
                "account_label": "국장1",
                "cash_krw": 1_000_000,
                "positions": [{"symbol": "005930"}],
            }

        def latest_judgment(self) -> dict:
            return {
                "status": "ok",
                "run": {"mode": "llm"},
                "judgments": [{"symbol": "005930", "account_action": "hold"}],
            }

        async def run_once(self, *, use_llm: bool = True) -> dict:
            return {
                "status": "ok",
                "use_llm": use_llm,
                "judgments": [{"symbol": "005930"}],
            }

        def status(self) -> dict:
            return {"status": "ok", "run_count": 1}

    monkeypatch.setattr(
        tradecraft_main,
        "market_judgment_engine",
        FakeMarketJudgmentEngine(),
    )
    monkeypatch.setattr(settings, "kis_primary_app_key", "app")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "secret")
    monkeypatch.setattr(settings, "kis_primary_account_no", "12345678")
    monkeypatch.setattr(settings, "kis_primary_product_code", "01")

    with TestClient(app) as client:
        clock = client.get("/api/market/clock")
        assert clock.status_code == 200
        assert clock.json()["session"] == "regular"

        account = client.get("/api/market/account")
        assert account.status_code == 200
        assert account.json()["account_label"] == "국장1"

        latest = client.get("/api/market/judgments/latest")
        assert latest.status_code == 200
        assert latest.json()["judgments"][0]["symbol"] == "005930"

        run = client.post("/api/market/judgments/run-once")
        assert run.status_code == 200
        assert run.json()["use_llm"] is True


def test_dashboard_uses_upbit_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 10_000.0,
                "available": 10_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 10_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "BTC",
                "kind": "position",
                "qty": 0.1,
                "available": 0.1,
                "locked": 0.0,
                "avg_price": 100_000_000.0,
                "mark_price": 110_000_000.0,
                "value_krw": 11_000_000.0,
                "pnl_krw": 1_000_000.0,
            },
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "dummy")
    monkeypatch.setattr(settings, "upbit_secret_key", "dummy")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(upbit, "fetch_balance_assets", fake_fetch_balance_assets)

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        upbit_venue = next(v for v in payload["venues"] if v["id"] == "upbit")
        assert upbit_venue["cash_krw"] == 10_000.0
        assert upbit_venue["invested_krw"] == 11_000_000.0
        assert upbit_venue["unrealized_pnl_krw"] == 1_000_000.0
        assert any(event["type"] == "upbit" for event in payload["events"])


def test_dashboard_uses_bithumb_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 400_000.0,
                "available": 400_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 400_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "XRP",
                "kind": "position",
                "qty": 300.0,
                "available": 300.0,
                "locked": 0.0,
                "avg_price": 850.0,
                "mark_price": 920.0,
                "value_krw": 276_000.0,
                "pnl_krw": 21_000.0,
            },
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "dummy")
    monkeypatch.setattr(settings, "bithumb_secret_key", "dummy")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    monkeypatch.setattr(bithumb, "fetch_balance_assets", fake_fetch_balance_assets)

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        venue = next(v for v in payload["venues"] if v["id"] == "bithumb")
        assert venue["cash_krw"] == 400_000.0
        assert venue["invested_krw"] == 276_000.0
        assert venue["unrealized_pnl_krw"] == 21_000.0
        assert any(
            "빗썸" in str(event.get("message") or "") for event in payload["events"]
        )


def test_dashboard_uses_binance_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_spot_assets(usdt_krw_rate: float | None = None) -> list[dict]:
        assert usdt_krw_rate == pytest.approx(1400.0)
        return [
            {
                "asset": "USDT",
                "kind": "cash",
                "qty": 1000.0,
                "available": 1000.0,
                "locked": 0.0,
                "avg_price": 1380.0,
                "mark_price": 1380.0,
                "value_krw": 1_380_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "BTC",
                "kind": "position",
                "qty": 0.01,
                "available": 0.01,
                "locked": 0.0,
                "avg_price": 0.0,
                "mark_price": 140_000_000.0,
                "value_krw": 1_400_000.0,
                "pnl_krw": 0.0,
            },
        ]

    async def fake_fetch_futures_assets(
        usdt_krw_rate: float | None = None,
    ) -> list[dict]:
        assert usdt_krw_rate == pytest.approx(1400.0)
        return [
            {
                "asset": "USDT-FUT",
                "kind": "cash",
                "qty": 200.0,
                "available": 200.0,
                "locked": 0.0,
                "avg_price": 1380.0,
                "mark_price": 1380.0,
                "value_krw": 276_000.0,
                "pnl_krw": 0.0,
            }
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "dummy")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "dummy")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(binance, "fetch_spot_assets", fake_fetch_spot_assets)
    monkeypatch.setattr(binance, "fetch_futures_assets", fake_fetch_futures_assets)

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        spot_venue = next(v for v in payload["venues"] if v["id"] == "binance")
        futures_venue = next(
            v for v in payload["venues"] if v["id"] == "binance_futures"
        )
        assert spot_venue["cash_krw"] == 1_380_000.0
        assert spot_venue["invested_krw"] == 1_400_000.0
        assert futures_venue["cash_krw"] == 276_000.0
        assert futures_venue["invested_krw"] == 0.0
        assert any(
            "바이낸스 Spot" in str(event.get("message") or "")
            for event in payload["events"]
        )
        assert any(
            "바이낸스 Futures" in str(event.get("message") or "")
            for event in payload["events"]
        )


def test_dashboard_uses_kis_primary_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 500_000.0,
                "available": 500_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 500_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "005930",
                "kind": "position",
                "qty": 10.0,
                "available": 10.0,
                "locked": 0.0,
                "avg_price": 70_000.0,
                "mark_price": 75_000.0,
                "value_krw": 750_000.0,
                "pnl_krw": 50_000.0,
            },
        ]

    async def fake_fetch_us_balance_assets(
        usd_krw_rate: float | None = None,
    ) -> list[dict]:
        assert usd_krw_rate == pytest.approx(1350.0)
        return [
            {
                "asset": "USD",
                "kind": "cash",
                "qty": 100.0,
                "available": 100.0,
                "locked": 0.0,
                "avg_price": 1300.0,
                "mark_price": 1300.0,
                "value_krw": 130_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "AAPL",
                "kind": "position",
                "qty": 2.0,
                "available": 2.0,
                "locked": 0.0,
                "avg_price": 260_000.0,
                "mark_price": 270_000.0,
                "value_krw": 540_000.0,
                "pnl_krw": 20_000.0,
            },
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "dummy")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "dummy")
    monkeypatch.setattr(settings, "kis_primary_account_no", "12345678")
    monkeypatch.setattr(settings, "kis_primary_product_code", "01")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    monkeypatch.setattr(kis_primary, "fetch_balance_assets", fake_fetch_balance_assets)
    monkeypatch.setattr(
        kis_primary, "fetch_us_balance_assets", fake_fetch_us_balance_assets
    )

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        kr_venue = next(v for v in payload["venues"] if v["id"] == "kr_stock")
        us_venue = next(v for v in payload["venues"] if v["id"] == "us_stock")
        assert kr_venue["cash_krw"] == 500_000.0
        assert kr_venue["invested_krw"] == 750_000.0
        assert kr_venue["unrealized_pnl_krw"] == 50_000.0
        assert us_venue["cash_krw"] == 130_000.0
        assert us_venue["invested_krw"] == 540_000.0
        assert us_venue["unrealized_pnl_krw"] == 20_000.0
        assert any(
            "KIS 1번" in str(event.get("message") or "") for event in payload["events"]
        )


def test_dashboard_adds_kis_secondary_venue_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_balance_assets() -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 120_000.0,
                "available": 120_000.0,
                "locked": 0.0,
                "avg_price": 1.0,
                "mark_price": 1.0,
                "value_krw": 120_000.0,
                "pnl_krw": 0.0,
            }
        ]

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "dummy")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "dummy")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "12345678")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "01")
    monkeypatch.setattr(
        kis_secondary, "fetch_balance_assets", fake_fetch_balance_assets
    )

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        secondary = next(v for v in payload["venues"] if v["id"] == "kr_stock_2")
        assert secondary["cash_krw"] == 120_000.0
        assert any(
            "KIS 2번" in str(event.get("message") or "") for event in payload["events"]
        )


def test_telegram_status_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")
    with TestClient(app) as client:
        res = client.get("/api/telegram/status")
        assert res.status_code == 200
        assert "ready" in res.json()


def test_dashboard_includes_research_feed(monkeypatch, tmp_path) -> None:
    path = tmp_path / "research.json"
    RuntimeStateStore(path).write_snapshot(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "openai",
            "query": "crypto",
            "items": [
                {
                    "title": "Daily brief",
                    "summary": "market sentiment remains positive",
                    "source": "openai",
                    "url": "https://example.com/research",
                }
            ],
        }
    )

    monkeypatch.setattr(
        "tradecraft.main.research_reader",
        type(research_reader)(str(path), max_age_sec=60),
    )
    monkeypatch.setattr(settings, "research_enabled", True)

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert payload["research"]["status"] == "ok"
        assert payload["research"]["query"] == "crypto"
        assert payload["research"]["source"] == "openai"
        assert payload["research"]["count"] == 1
        assert payload["research"]["items"][0]["title"] == "Daily brief"


def test_dashboard_includes_stale_research_cache(monkeypatch, tmp_path) -> None:
    path = tmp_path / "research.json"
    RuntimeStateStore(path).write_snapshot(
        {
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "source": "openai",
            "query": "crypto",
            "items": [
                {
                    "title": "Old daily brief",
                    "summary": "old but visible as stale cache",
                    "source": "openai",
                }
            ],
        }
    )

    monkeypatch.setattr(
        "tradecraft.main.research_reader",
        type(research_reader)(str(path), max_age_sec=60),
    )
    monkeypatch.setattr(settings, "research_enabled", True)

    monkeypatch.setattr(settings, "upbit_access_key", "")
    monkeypatch.setattr(settings, "upbit_secret_key", "")
    monkeypatch.setattr(settings, "bithumb_access_key", "")
    monkeypatch.setattr(settings, "bithumb_secret_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_key", "")
    monkeypatch.setattr(settings, "binance_spot_api_secret", "")
    monkeypatch.setattr(settings, "binance_futures_api_key", "")
    monkeypatch.setattr(settings, "binance_futures_api_secret", "")
    monkeypatch.setattr(settings, "kis_primary_app_key", "")
    monkeypatch.setattr(settings, "kis_primary_app_secret", "")
    monkeypatch.setattr(settings, "kis_primary_account_no", "")
    monkeypatch.setattr(settings, "kis_primary_product_code", "")
    monkeypatch.setattr(settings, "kis_secondary_app_key", "")
    monkeypatch.setattr(settings, "kis_secondary_app_secret", "")
    monkeypatch.setattr(settings, "kis_secondary_account_no", "")
    monkeypatch.setattr(settings, "kis_secondary_product_code", "")

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert payload["research"]["status"] == "stale"
        assert payload["research"]["stale"] is True
        assert payload["research"]["count"] == 1
        assert payload["research"]["items"][0]["title"] == "Old daily brief"
        assert any(
            "리서치 스냅샷 오래됨" in str(event.get("message") or "")
            for event in payload.get("events") or []
        )
