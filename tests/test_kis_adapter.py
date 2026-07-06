from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tradecraft.services.kis import (
    KISAPIError,
    KISAdapter,
    KISConfig,
    KISSharedRateLimiter,
)


def test_kis_to_assets_maps_balance_rows() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    rows = [
        {
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "hldg_qty": "12",
            "ord_psbl_qty": "10",
            "pchs_avg_pric": "70000",
            "prpr": "75000",
            "evlu_amt": "900000",
            "evlu_pfls_amt": "60000",
        },
        {
            "pdno": "000660",
            "hldg_qty": "0",
            "ord_psbl_qty": "0",
            "pchs_avg_pric": "180000",
            "prpr": "190000",
            "evlu_amt": "0",
            "evlu_pfls_amt": "0",
        },
    ]
    summary = {
        "dnca_tot_amt": "250000",
    }

    assets = adapter._to_assets(rows, summary)

    krw = next(item for item in assets if item["asset"] == "KRW")
    samsung = next(item for item in assets if item["asset"] == "005930")
    symbols = [item["asset"] for item in assets]

    assert krw["kind"] == "cash"
    assert krw["value_krw"] == pytest.approx(250_000.0)
    assert krw["settled_cash_krw"] == pytest.approx(250_000.0)
    assert krw["orderable_cash_krw"] == pytest.approx(250_000.0)
    assert samsung["kind"] == "position"
    assert samsung["asset_name"] == "삼성전자"
    assert samsung["qty"] == pytest.approx(12.0)
    assert samsung["available"] == pytest.approx(10.0)
    assert samsung["locked"] == pytest.approx(2.0)
    assert samsung["value_krw"] == pytest.approx(900_000.0)
    assert samsung["pnl_krw"] == pytest.approx(60_000.0)
    assert "000660" not in symbols


def test_kis_to_assets_keeps_same_day_sell_proceeds_in_cash_breakdown() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    assets = adapter._to_assets(
        [],
        {
            "dnca_tot_amt": "336752",
            "prvs_rcdl_excc_amt": "4264740",
            "nxdy_excc_amt": "336752",
            "scts_evlu_amt": "233600",
            "nass_amt": "4498340",
            "thdt_sll_amt": "3936000",
            "thdt_tlex_amt": "8012",
        },
    )

    krw = next(item for item in assets if item["asset"] == "KRW")
    assert krw["value_krw"] == pytest.approx(4_264_740.0)
    assert krw["settled_cash_krw"] == pytest.approx(336_752.0)
    assert krw["receivable_cash_krw"] == pytest.approx(3_927_988.0)
    assert krw["orderable_cash_krw"] == pytest.approx(4_264_740.0)
    assert krw["today_sell_amount_krw"] == pytest.approx(3_936_000.0)
    assert krw["today_fee_tax_krw"] == pytest.approx(8_012.0)


def test_kis_shared_rate_limiter_spaces_rest_calls(tmp_path) -> None:
    limiter = KISSharedRateLimiter(
        db_path=tmp_path / "kis_rate_limit.db",
        rest_rate_limit_per_sec=5.0,
        token_min_interval_sec=0.0,
    )

    async def run() -> float:
        start = time.perf_counter()
        await limiter.wait("rest")
        await limiter.wait("rest")
        return time.perf_counter() - start

    assert asyncio.run(run()) >= 0.15


def test_kis_shared_rate_limiter_uses_slower_account_bucket(tmp_path) -> None:
    limiter = KISSharedRateLimiter(
        db_path=tmp_path / "kis_rate_limit.db",
        rest_rate_limit_per_sec=100.0,
        token_min_interval_sec=0.0,
        account_min_interval_sec=0.2,
    )

    async def run() -> float:
        start = time.perf_counter()
        await limiter.wait("account")
        await limiter.wait("account")
        return time.perf_counter() - start

    assert asyncio.run(run()) >= 0.15


def test_kis_shared_rate_limiter_preserves_credential_scoped_account_bucket(
    tmp_path,
) -> None:
    limiter = KISSharedRateLimiter(
        db_path=tmp_path / "kis_rate_limit.db",
        rest_rate_limit_per_sec=100.0,
        token_min_interval_sec=0.0,
        account_min_interval_sec=0.2,
    )

    async def run() -> tuple[str, float]:
        bucket = limiter._bucket_name("account:primary")
        start = time.perf_counter()
        await limiter.wait("account:primary")
        await limiter.wait("account:primary")
        return bucket, time.perf_counter() - start

    bucket, elapsed = asyncio.run(run())

    assert bucket == "account:primary"
    assert elapsed >= 0.15


def test_kis_shared_rate_limiter_penalizes_account_bucket(tmp_path) -> None:
    limiter = KISSharedRateLimiter(
        db_path=tmp_path / "kis_rate_limit.db",
        rest_rate_limit_per_sec=0.0,
        token_min_interval_sec=0.0,
        account_min_interval_sec=0.0,
    )

    async def run() -> float:
        await limiter.wait("account")
        await limiter.penalize("account", delay_sec=0.2)
        start = time.perf_counter()
        await limiter.wait("account")
        return time.perf_counter() - start

    assert asyncio.run(run()) >= 0.15


def test_kis_request_waits_global_and_scoped_account_buckets(monkeypatch) -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    waited_buckets: list[str] = []

    class FakeRateLimiter:
        async def wait(self, bucket: str) -> None:
            waited_buckets.append(bucket)

        async def penalize(self, bucket: str, *, delay_sec: float) -> None:
            _ = (bucket, delay_sec)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        async def get(self, url: str, **kwargs):
            _ = (url, kwargs)
            return httpx.Response(200, json={"rt_cd": "0", "output": []})

    adapter._rate_limiter = FakeRateLimiter()  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr("tradecraft.services.kis.httpx.AsyncClient", FakeAsyncClient)

    asyncio.run(
        adapter._request_json(  # noqa: SLF001
            "get",
            "https://kis.example.test/account",
            bucket="account:primary",
            retry_rate_limit=False,
        )
    )

    assert waited_buckets == ["account", "account:primary"]


def test_kis_request_penalizes_global_and_scoped_account_buckets_on_rate_limit(
    monkeypatch,
) -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    penalized_buckets: list[tuple[str, float]] = []
    payloads = [
        {
            "rt_cd": "1",
            "msg_cd": "EGW00215",
            "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다.",
        },
        {"rt_cd": "0", "output": []},
    ]

    class FakeRateLimiter:
        async def wait(self, bucket: str) -> None:
            _ = bucket

        async def penalize(self, bucket: str, *, delay_sec: float) -> None:
            penalized_buckets.append((bucket, delay_sec))

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        async def get(self, url: str, **kwargs):
            _ = (url, kwargs)
            return httpx.Response(200, json=payloads.pop(0))

    async def fake_sleep(delay: float) -> None:
        _ = delay

    adapter._rate_limiter = FakeRateLimiter()  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr("tradecraft.services.kis.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("tradecraft.services.kis.asyncio.sleep", fake_sleep)

    _, payload = asyncio.run(
        adapter._request_json(  # noqa: SLF001
            "get",
            "https://kis.example.test/account",
            bucket="account:primary",
        )
    )

    assert payload["rt_cd"] == "0"
    assert penalized_buckets == [("account", 16.0), ("account:primary", 16.0)]


def test_kis_config_defaults_to_conservative_account_rate_limit() -> None:
    assert KISConfig().account_min_interval_sec == 8.0


def test_kis_request_retries_account_rate_limit_more_conservatively(monkeypatch) -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
            rate_limit_enabled=False,
        )
    )
    payloads = [
        {
            "rt_cd": "1",
            "msg_cd": "EGW00215",
            "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다.",
        },
        {
            "rt_cd": "1",
            "msg_cd": "EGW00215",
            "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다.",
        },
        {"rt_cd": "0", "output": []},
    ]
    sleep_delays: list[float] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = (args, kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        async def get(self, url: str, **kwargs):
            _ = (url, kwargs)
            return httpx.Response(200, json=payloads.pop(0))

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr("tradecraft.services.kis.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("tradecraft.services.kis.asyncio.sleep", fake_sleep)

    _, payload = asyncio.run(
        adapter._request_json("get", "https://kis.example.test/account", bucket="account")
    )

    assert payload["rt_cd"] == "0"
    assert sleep_delays == [16.0, 24.0]
    assert payloads == []


def test_kis_shared_rate_limiter_caches_access_tokens(tmp_path) -> None:
    limiter = KISSharedRateLimiter(
        db_path=tmp_path / "kis_rate_limit.db",
        rest_rate_limit_per_sec=8.0,
        token_min_interval_sec=0.0,
    )
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    async def run() -> tuple[str, datetime] | None:
        await limiter.save_token(
            token_key="primary",
            access_token="token-1",
            expires_at=expiry,
        )
        return await limiter.get_token("primary")

    cached = asyncio.run(run())

    assert cached is not None
    assert cached[0] == "token-1"
    assert abs((cached[1] - expiry).total_seconds()) < 0.01


def test_kis_domestic_order_tr_ids_use_current_cash_order_family() -> None:
    config = KISConfig()

    assert config.tr_id_order_buy == "TTTC0012U"
    assert config.tr_id_order_sell == "TTTC0011U"
    assert config.tr_id_order_revise_cancel == "TTTC0013U"
    assert config.tr_id_order_daily == "TTTC0081R"
    assert config.tr_id_order_cancelable == "TTTC0084R"


def test_kis_balance_pages_use_account_rate_bucket(monkeypatch) -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
            rate_limit_enabled=False,
        )
    )
    buckets: list[str] = []

    async def fake_get_access_token() -> str:
        return "token"

    async def fake_request_json(method: str, url: str, **kwargs):
        _ = (method, url)
        buckets.append(str(kwargs.get("bucket") or ""))
        return httpx.Response(200), {"rt_cd": "0", "output1": [], "output2": {}}

    monkeypatch.setattr(adapter, "_get_access_token", fake_get_access_token)
    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    asyncio.run(adapter._inquire_balance_page())
    asyncio.run(adapter._inquire_us_present_balance_page())

    assert len(buckets) == 2
    assert all(bucket.startswith("account:") for bucket in buckets)
    assert buckets[0] == buckets[1]


def test_kis_rate_limit_detector_includes_ledger_limit_code() -> None:
    payload = {
        "rt_cd": "1",
        "msg_cd": "EGW00215",
        "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다.",
    }

    assert KISAdapter._is_rest_rate_limit_payload(payload) is True


def test_kis_domestic_order_refreshes_expired_token_and_retries(monkeypatch) -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
            rate_limit_enabled=False,
        )
    )
    order_authorizations: list[str] = []
    token_requests = 0

    async def fake_request_json(method: str, url: str, **kwargs):
        nonlocal token_requests
        _ = method
        if url.endswith("/oauth2/tokenP"):
            token_requests += 1
            return httpx.Response(200), {
                "access_token": f"token-{token_requests}",
                "expires_in": 86400,
            }
        if url.endswith("/order-cash"):
            headers = kwargs.get("headers") or {}
            order_authorizations.append(str(headers.get("Authorization") or ""))
            if len(order_authorizations) == 1:
                return httpx.Response(200), {
                    "rt_cd": "1",
                    "msg_cd": "EGW00123",
                    "msg1": "기간이 만료된 token 입니다.",
                }
            return httpx.Response(200), {
                "rt_cd": "0",
                "output": {"ODNO": "0000000002"},
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    result = asyncio.run(
        adapter.submit_domestic_order(
            symbol="005930",
            side="buy",
            quantity=1,
            price=70000,
            order_type="00",
        )
    )

    assert result["order_no"] == "0000000002"
    assert order_authorizations == ["Bearer token-1", "Bearer token-2"]
    assert token_requests == 2


def test_kis_normalize_domestic_order_row_maps_fill_fields() -> None:
    adapter = KISAdapter(KISConfig())
    row = {
        "ord_dt": "20260508",
        "ord_gno_brno": "00123",
        "odno": "0000007777",
        "pdno": "005930",
        "prdt_name": "삼성전자",
        "sll_buy_dvsn_cd": "02",
        "ord_qty": "3",
        "ord_unpr": "70000",
        "tot_ccld_qty": "2",
        "tot_ccld_amt": "142000",
        "rmn_qty": "1",
        "psbl_qty": "1",
    }

    order = adapter.normalize_domestic_order_row(row)

    assert order["order_no"] == "0000007777"
    assert order["order_orgno"] == "00123"
    assert order["symbol"] == "005930"
    assert order["filled_qty"] == 2
    assert order["remaining_qty"] == 1
    assert order["cancelable_qty"] == 1
    assert order["avg_fill_price"] == pytest.approx(71_000.0)


def test_kis_cancel_domestic_order_reports_cancelable_lookup_failure(
    monkeypatch,
) -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
            rate_limit_enabled=False,
        )
    )

    async def fake_cancelable_orders(*, max_pages: int = 3) -> dict[str, object]:
        _ = max_pages
        raise KISAPIError("rate limit while fetching cancelable orders")

    monkeypatch.setattr(
        adapter,
        "fetch_domestic_cancelable_orders",
        fake_cancelable_orders,
    )

    with pytest.raises(KISAPIError, match="rate limit while fetching cancelable orders"):
        asyncio.run(adapter.cancel_domestic_order(order_no="0000007777"))


def test_kis_select_cash_value_infers_from_total_minus_stocks() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    cash = adapter._select_cash_value(
        {
            "tot_evlu_amt": "5000000",
            "scts_evlu_amt": "4200000",
        }
    )
    assert cash == pytest.approx(800_000.0)


def test_kis_to_us_assets_maps_present_balance_rows() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    rows = [
        {
            "pdno": "AAPL",
            "ovrs_item_name": "Apple",
            "cblc_qty13": "3",
            "ord_psbl_qty1": "2",
            "avg_unpr3": "180",
            "ovrs_now_pric1": "190",
            "frcr_evlu_amt2": "570",
            "evlu_pfls_amt2": "30",
            "bass_exrt": "1300",
        },
    ]
    summary = {
        "frcr_dncl_amt_2": "100",
        "frst_bltn_exrt": "1300",
    }

    assets = adapter._to_us_assets(rows, summary)
    usd = next(item for item in assets if item["asset"] == "USD")
    aapl = next(item for item in assets if item["asset"] == "AAPL")

    assert usd["kind"] == "cash"
    assert usd["qty"] == pytest.approx(100.0)
    assert usd["value_krw"] == pytest.approx(130_000.0)
    assert aapl["kind"] == "position"
    assert aapl["asset_name"] == "Apple"
    assert aapl["available"] == pytest.approx(2.0)
    assert aapl["locked"] == pytest.approx(1.0)
    assert aapl["value_krw"] == pytest.approx(741_000.0)
    assert aapl["pnl_krw"] == pytest.approx(39_000.0)


def test_kis_select_usd_cash_value_falls_back_to_krw_summary() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    usd, krw = adapter._select_usd_cash_value({"tot_dncl_amt": "260000"}, 1300.0)
    assert usd == pytest.approx(200.0)
    assert krw == pytest.approx(260_000.0)


def test_kis_to_us_assets_uses_runtime_fx_override() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    rows = [
        {
            "pdno": "AAPL",
            "ovrs_item_name": "Apple",
            "cblc_qty13": "2",
            "ord_psbl_qty1": "2",
            "avg_unpr3": "100",
            "ovrs_now_pric1": "110",
            "frcr_evlu_amt2": "220",
            "evlu_pfls_amt2": "20",
            "bass_exrt": "1300",
        },
    ]
    summary = {
        "frcr_dncl_amt_2": "10",
        "frst_bltn_exrt": "1300",
    }

    assets = adapter._to_us_assets(rows, summary, usd_krw_rate=1500.0)
    usd = next(item for item in assets if item["asset"] == "USD")
    aapl = next(item for item in assets if item["asset"] == "AAPL")
    assert usd["mark_price"] == pytest.approx(1500.0)
    assert aapl["mark_price"] == pytest.approx(165000.0)
