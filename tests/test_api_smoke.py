from fastapi.testclient import TestClient

from tradecraft.main import app, binance, bithumb, kis_primary, kis_secondary, settings, upbit


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

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert "venues" in payload
        assert "portfolio_total_krw" in payload
        assert "sessions" in payload
        assert len(payload["venues"]) >= 4
        assert all("assets" in venue for venue in payload["venues"])
        venue_ids = {venue["id"] for venue in payload["venues"]}
        assert all("venue_id" in session for session in payload["sessions"])
        assert all(session["venue_id"] in venue_ids for session in payload["sessions"])
        assert any(session["mode"] == "short_term" for session in payload["sessions"])
        assert any(session["mode"] == "mid_long_term" for session in payload["sessions"])


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
        assert any("빗썸" in str(event.get("message") or "") for event in payload["events"])


def test_dashboard_uses_binance_adapter_when_key_exists(monkeypatch) -> None:
    async def fake_fetch_spot_assets() -> list[dict]:
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

    async def fake_fetch_futures_assets() -> list[dict]:
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
        futures_venue = next(v for v in payload["venues"] if v["id"] == "binance_futures")
        assert spot_venue["cash_krw"] == 1_380_000.0
        assert spot_venue["invested_krw"] == 1_400_000.0
        assert futures_venue["cash_krw"] == 276_000.0
        assert futures_venue["invested_krw"] == 0.0
        assert any("바이낸스 Spot" in str(event.get("message") or "") for event in payload["events"])
        assert any("바이낸스 Futures" in str(event.get("message") or "") for event in payload["events"])


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

    async def fake_fetch_us_balance_assets() -> list[dict]:
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
    monkeypatch.setattr(kis_primary, "fetch_us_balance_assets", fake_fetch_us_balance_assets)

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
        assert any("KIS 1번" in str(event.get("message") or "") for event in payload["events"])


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
    monkeypatch.setattr(kis_secondary, "fetch_balance_assets", fake_fetch_balance_assets)

    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        secondary = next(v for v in payload["venues"] if v["id"] == "kr_stock_2")
        assert secondary["cash_krw"] == 120_000.0
        assert any("KIS 2번" in str(event.get("message") or "") for event in payload["events"])


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
