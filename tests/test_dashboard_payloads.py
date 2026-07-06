from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradecraft.api.dashboard_payloads import (
    DashboardPayloadCache,
    DashboardPayloadDeps,
    apply_dashboard_fx_rates,
    build_dashboard_payload,
    is_fx_source_degraded,
    mark_venue_fresh_with_cached_assets,
    mark_venue_stale_with_cached_assets,
    mark_venue_unavailable,
    safe_positive_float,
)
from tradecraft.services.market import (
    empty_dashboard_template,
    replace_venue_assets,
    upsert_venue_assets,
)


class _Settings:
    upbit_ready = False
    bithumb_ready = False
    binance_spot_ready = False
    binance_futures_ready = False
    kis_primary_ready = False
    kis_secondary_ready = False
    research_enabled = False


class _FXRates:
    async def get_snapshot(self) -> dict:
        return {
            "usdt_krw": "1400",
            "usd_krw": "1350",
            "usdt_source": "upbit",
            "usd_source": "hana",
            "status": "ok",
            "fetched_at": "2026-06-20T00:00:00+00:00",
        }


class _RuntimeReader:
    def read_snapshot(self) -> tuple[dict, str]:
        return (
            {
                "sessions": [{"id": "runtime"}],
                "runtime": {"mode": "live"},
                "updated_at": "2026-06-20T00:00:00+00:00",
                "age_sec": 1,
                "max_age_sec": 60,
            },
            "fresh",
        )


class _MissingRuntimeReader:
    def read_snapshot(self) -> tuple[None, str]:
        return None, "missing"


class _ResearchReader:
    def read_feed(self, *, allow_stale: bool) -> tuple[dict | None, str]:
        raise AssertionError("research feed should not be read when disabled")


class _Telegram:
    def status(self) -> dict:
        return {"ready": True}


class _Logger:
    def warning(self, *_args: object, **_kwargs: object) -> None:
        return None


def test_base_dashboard_events_do_not_claim_risk_engine_is_not_connected() -> None:
    payload = empty_dashboard_template()
    messages = [str(row.get("message") or "") for row in payload.get("events") or []]

    assert not any("리스크 엔진 연결 전" in message for message in messages)
    assert not any("관측 모드" in message for message in messages)
    assert any("리스크 게이트" in message for message in messages)


def test_build_dashboard_payload_reports_missing_runtime_without_mock_sessions() -> None:
    payload = asyncio.run(
        build_dashboard_payload(
            DashboardPayloadDeps(
                settings=_Settings(),
                fx_rates=_FXRates(),
                upbit=None,
                bithumb=None,
                binance=None,
                kis_primary=None,
                kis_secondary=None,
                runtime_reader=_MissingRuntimeReader(),
                research_reader=_ResearchReader(),
                telegram=_Telegram(),
                dashboard_template=empty_dashboard_template,
                replace_venue_assets=replace_venue_assets,
                upsert_venue_assets=upsert_venue_assets,
                logger=_Logger(),
            ),
            DashboardPayloadCache(),
            include_telegram=False,
        )
    )

    messages = [str(row.get("message") or "") for row in payload.get("events") or []]

    assert payload["sessions"] == []
    assert payload["runtime"]["status"] == "missing"
    assert any("세션 상태 런타임 미연결" in message for message in messages)
    assert not any("mock" in message.lower() for message in messages)


class _FakeKISBalance:
    def __init__(self) -> None:
        self.balance_calls = 0
        self.us_balance_calls = 0

    async def fetch_balance_assets(self) -> list[dict]:
        self.balance_calls += 1
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 500_000.0,
                "value_krw": 500_000.0,
                "pnl_krw": 0.0,
            }
        ]

    async def fetch_us_balance_assets(self, *, usd_krw_rate: float) -> list[dict]:
        self.us_balance_calls += 1
        return [
            {
                "asset": "USD",
                "kind": "cash",
                "qty": 10.0,
                "value_krw": 10.0 * usd_krw_rate,
                "pnl_krw": 0.0,
            }
        ]


class _FailingKISBalance:
    def __init__(self) -> None:
        self.balance_calls = 0
        self.us_balance_calls = 0

    async def fetch_balance_assets(self) -> list[dict]:
        self.balance_calls += 1
        raise RuntimeError("kis balance request rejected")

    async def fetch_us_balance_assets(self, *, usd_krw_rate: float) -> list[dict]:
        self.us_balance_calls += 1
        raise RuntimeError("kis us balance request rejected")


class _SlowPrimaryKISBalance:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def fetch_balance_assets(self) -> list[dict]:
        self.events.append("primary_kr_start")
        await asyncio.sleep(0.01)
        self.events.append("primary_kr_end")
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 500_000.0,
                "value_krw": 500_000.0,
                "pnl_krw": 0.0,
            }
        ]

    async def fetch_us_balance_assets(self, *, usd_krw_rate: float) -> list[dict]:
        self.events.append("primary_us_start")
        await asyncio.sleep(0.01)
        self.events.append("primary_us_end")
        return [
            {
                "asset": "USD",
                "kind": "cash",
                "qty": 10.0,
                "value_krw": 10.0 * usd_krw_rate,
                "pnl_krw": 0.0,
            }
        ]


class _SlowUpbitBalance:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def fetch_balance_assets(self) -> list[dict]:
        self.events.append("upbit_start")
        await asyncio.sleep(0.02)
        self.events.append("upbit_end")
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 100_000.0,
                "value_krw": 100_000.0,
                "pnl_krw": 0.0,
            }
        ]


class _SlowBithumbBalance:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def fetch_balance_assets(self) -> list[dict]:
        self.events.append("bithumb_start")
        await asyncio.sleep(0.02)
        self.events.append("bithumb_end")
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 200_000.0,
                "value_krw": 200_000.0,
                "pnl_krw": 0.0,
            }
        ]


class _SlowBinanceBalance:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def fetch_spot_assets(self, *, usdt_krw_rate: float) -> list[dict]:
        self.events.append("binance_spot_start")
        await asyncio.sleep(0.02)
        self.events.append("binance_spot_end")
        return [
            {
                "asset": "USDT",
                "kind": "cash",
                "qty": 10.0,
                "value_krw": 10.0 * usdt_krw_rate,
                "pnl_krw": 0.0,
            }
        ]

    async def fetch_futures_assets(self, *, usdt_krw_rate: float) -> list[dict]:
        self.events.append("binance_futures_start")
        await asyncio.sleep(0.02)
        self.events.append("binance_futures_end")
        return [
            {
                "asset": "USDT-FUT",
                "kind": "cash",
                "qty": 5.0,
                "value_krw": 5.0 * usdt_krw_rate,
                "pnl_krw": 0.0,
            }
        ]


class _SlowSecondaryKISBalance:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def fetch_balance_assets(self) -> list[dict]:
        self.events.append("secondary_kr_start")
        await asyncio.sleep(0.01)
        self.events.append("secondary_kr_end")
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 250_000.0,
                "value_krw": 250_000.0,
                "pnl_krw": 0.0,
            }
        ]


class _SlowCountingKISBalance:
    def __init__(self) -> None:
        self.balance_calls = 0
        self.us_balance_calls = 0

    async def fetch_balance_assets(self) -> list[dict]:
        self.balance_calls += 1
        await asyncio.sleep(0.03)
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 500_000.0,
                "value_krw": 500_000.0,
                "pnl_krw": 0.0,
            }
        ]

    async def fetch_us_balance_assets(self, *, usd_krw_rate: float) -> list[dict]:
        self.us_balance_calls += 1
        await asyncio.sleep(0.03)
        return [
            {
                "asset": "USD",
                "kind": "cash",
                "qty": 10.0,
                "value_krw": 10.0 * usd_krw_rate,
                "pnl_krw": 0.0,
            }
        ]


def test_safe_positive_float_accepts_positive_numeric_text_only() -> None:
    assert safe_positive_float("1,234.5") == 1234.5
    assert safe_positive_float(7) == 7.0
    assert safe_positive_float("-1") == 0.0
    assert safe_positive_float("not-a-number") == 0.0
    assert safe_positive_float(None) == 0.0


def test_fx_source_degraded_flags_fallback_and_empty_sources() -> None:
    assert is_fx_source_degraded("")
    assert is_fx_source_degraded("fallback_static")
    assert is_fx_source_degraded("bithumb_proxy")
    assert not is_fx_source_degraded("upbit")


def test_apply_dashboard_fx_rates_updates_stablecoin_cash_and_totals() -> None:
    dashboard = {
        "venues": [
            {
                "id": "binance",
                "assets": [
                    {
                        "asset": "USDT",
                        "kind": "cash",
                        "qty": 2,
                        "avg_price": 1,
                        "mark_price": 1,
                        "value_krw": 2,
                    },
                    {
                        "asset": "BTC",
                        "kind": "position",
                        "qty": 1,
                        "value_krw": 100,
                        "pnl_krw": 3,
                    },
                ],
            },
            {
                "id": "binance_futures",
                "assets": [
                    {
                        "asset": "USDT-FUT",
                        "kind": "cash",
                        "qty": 3,
                        "avg_price": 1,
                        "mark_price": 1,
                        "value_krw": 3,
                    },
                ],
            },
            {
                "id": "us_stock",
                "assets": [
                    {
                        "asset": "USD",
                        "kind": "cash",
                        "qty": 4,
                        "avg_price": 1,
                        "mark_price": 1,
                        "value_krw": 4,
                    },
                ],
            },
        ],
    }

    apply_dashboard_fx_rates(dashboard, usdt_krw=1400, usd_krw=1350)

    spot_cash = dashboard["venues"][0]["assets"][0]
    fut_cash = dashboard["venues"][1]["assets"][0]
    usd_cash = dashboard["venues"][2]["assets"][0]
    assert spot_cash["value_krw"] == 2800
    assert fut_cash["value_krw"] == 4200
    assert usd_cash["value_krw"] == 5400
    assert dashboard["portfolio_total_krw"] == 12500
    assert dashboard["cash_total_krw"] == 12400
    assert dashboard["invested_total_krw"] == 100


def test_mark_venue_unavailable_clears_assets_and_records_event() -> None:
    dashboard = {
        "events": [],
        "venues": [
            {
                "id": "binance",
                "label": "old",
                "market": "old",
                "assets": [{"asset": "USDT", "kind": "cash", "value_krw": 100}],
            }
        ],
    }

    mark_venue_unavailable(
        dashboard,
        venue_id="binance",
        label="바이낸스 현물",
        market="해외 가상자산 (Spot)",
        status="error",
        event_type="binance",
        message="바이낸스 조회 실패",
        error_message="boom",
    )

    venue = dashboard["venues"][0]
    assert venue["assets"] == []
    assert venue["status"] == "error"
    assert venue["error_message"] == "boom"
    assert dashboard["portfolio_total_krw"] == 0
    assert dashboard["events"] == [
        {"type": "binance", "message": "바이낸스 조회 실패"}
    ]


def test_mark_venue_stale_with_cached_assets_keeps_last_good_assets() -> None:
    dashboard = {
        "events": [],
        "venues": [
            {
                "id": "kr_stock",
                "label": "국장",
                "market": "KRX",
                "assets": [],
            }
        ],
    }
    cached_assets = [
        {
            "asset": "KRW",
            "kind": "cash",
            "qty": 500_000.0,
            "value_krw": 500_000.0,
            "pnl_krw": 0.0,
        },
        {
            "asset": "005930",
            "kind": "position",
            "qty": 1.0,
            "value_krw": 80_000.0,
            "pnl_krw": 1_000.0,
        },
    ]

    used_cache = mark_venue_stale_with_cached_assets(
        dashboard,
        venue_id="kr_stock",
        label="국장",
        market="KRX",
        cached_assets=cached_assets,
        event_type="kis",
        message="KIS 조회 실패: 최근 성공 잔고 유지",
        error_message="SESSION FULL",
    )

    venue = dashboard["venues"][0]
    assert used_cache is True
    assert venue["status"] == "stale"
    assert venue["error_message"] == "SESSION FULL"
    assert venue["assets"] == [
        {**cached_assets[0], "symbol": "KRW"},
        {**cached_assets[1], "symbol": "005930"},
    ]
    assert "symbol" not in cached_assets[0]
    assert venue["cash_krw"] == 500_000.0
    assert venue["invested_krw"] == 80_000.0
    assert dashboard["events"] == [
        {"type": "kis", "message": "KIS 조회 실패: 최근 성공 잔고 유지"}
    ]


def test_mark_venue_fresh_with_cached_assets_reuses_recent_cache() -> None:
    fetched_at = datetime.now(timezone.utc)
    dashboard = {
        "events": [],
        "venues": [
            {
                "id": "kr_stock",
                "label": "국장",
                "market": "KRX",
                "assets": [],
            }
        ],
    }
    cached_assets = [
        {
            "asset": "KRW",
            "kind": "cash",
            "qty": 500_000.0,
            "value_krw": 500_000.0,
            "pnl_krw": 0.0,
        }
    ]

    used_cache = mark_venue_fresh_with_cached_assets(
        dashboard,
        venue_id="kr_stock",
        label="국장",
        market="KRX",
        cached_assets=cached_assets,
        fetched_at=fetched_at,
        ttl_sec=60,
        now=fetched_at,
        event_type="kis",
        message="KIS 최근 잔고 캐시 사용",
    )

    venue = dashboard["venues"][0]
    assert used_cache is True
    assert venue["cache_status"] == "fresh"
    assert venue["cached_at"] == fetched_at.isoformat()
    assert venue["cash_krw"] == 500_000.0
    assert dashboard["events"] == [
        {"type": "kis", "message": "KIS 최근 잔고 캐시 사용"}
    ]


def test_build_dashboard_payload_reuses_recent_kis_balance_cache() -> None:
    class _KISCacheSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 3600

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    cache = DashboardPayloadCache()
    fake_kis = _FakeKISBalance()
    deps = DashboardPayloadDeps(
        settings=_KISCacheSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=fake_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    first = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))
    second = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))
    forced = asyncio.run(
        build_dashboard_payload(
            deps,
            cache,
            include_telegram=False,
            force_refresh=True,
        )
    )

    first_venues = {row["id"]: row for row in first["venues"]}
    second_venues = {row["id"]: row for row in second["venues"]}
    forced_venues = {row["id"]: row for row in forced["venues"]}
    assert fake_kis.balance_calls == 2
    assert fake_kis.us_balance_calls == 2
    assert first_venues["kr_stock"]["status"] == "ok"
    assert first_venues["kr_stock"]["cash_krw"] == 500_000.0
    assert second_venues["kr_stock"]["cache_status"] == "fresh"
    assert second_venues["kr_stock"]["status"] == "ok"
    assert second_venues["us_stock"]["cache_status"] == "fresh"
    assert forced_venues["kr_stock"]["status"] == "ok"
    assert forced_venues["kr_stock"]["cash_krw"] == 500_000.0
    assert "cache_status" not in forced_venues["kr_stock"]
    assert forced_venues["us_stock"]["status"] == "ok"
    assert "cache_status" not in forced_venues["us_stock"]


def test_build_dashboard_payload_inserts_kis_primary_venues_when_base_dashboard_is_empty() -> None:
    class _KISSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 3600

    def mock_dashboard() -> dict:
        return {"events": [], "venues": []}

    fake_kis = _FakeKISBalance()
    deps = DashboardPayloadDeps(
        settings=_KISSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=fake_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(
        build_dashboard_payload(deps, DashboardPayloadCache(), include_telegram=False)
    )

    venues = {row["id"]: row for row in payload["venues"]}
    assert venues["kr_stock"]["label"] == "국장1"
    assert venues["kr_stock"]["status"] == "ok"
    assert venues["kr_stock"]["cash_krw"] == 500_000.0
    assert venues["us_stock"]["label"] == "미장"
    assert venues["us_stock"]["status"] == "ok"
    assert venues["us_stock"]["cash_krw"] == 13_500.0


def test_build_dashboard_payload_hydrates_recent_disk_cache_after_restart(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "dashboard_payload_cache.json"

    class _DiskCacheSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 3600
        dashboard_payload_disk_cache_enabled = True
        dashboard_payload_cache_path = str(cache_path)

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    first_kis = _FakeKISBalance()
    first_deps = DashboardPayloadDeps(
        settings=_DiskCacheSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=first_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )
    first = asyncio.run(
        build_dashboard_payload(first_deps, DashboardPayloadCache(), include_telegram=False)
    )
    assert first_kis.balance_calls == 1
    assert first_kis.us_balance_calls == 1
    assert cache_path.exists()

    second_kis = _FakeKISBalance()
    second_deps = DashboardPayloadDeps(
        settings=_DiskCacheSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=second_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    second = asyncio.run(
        build_dashboard_payload(second_deps, DashboardPayloadCache(), include_telegram=False)
    )

    first_venues = {row["id"]: row for row in first["venues"]}
    second_venues = {row["id"]: row for row in second["venues"]}
    assert second_kis.balance_calls == 0
    assert second_kis.us_balance_calls == 0
    assert first_venues["kr_stock"]["cash_krw"] == 500_000.0
    assert second_venues["kr_stock"]["cache_status"] == "fresh"
    assert second_venues["kr_stock"]["cash_krw"] == 500_000.0
    assert second_venues["us_stock"]["cache_status"] == "fresh"


def test_build_dashboard_payload_ignores_disk_cache_for_different_identity(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "dashboard_payload_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 2,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "venues": {
                    "kis_primary": {
                        "assets": [
                            {
                                "asset": "KRW",
                                "kind": "cash",
                                "qty": 9_999_999.0,
                                "value_krw": 9_999_999.0,
                                "pnl_krw": 0.0,
                            }
                        ],
                        "cache_identity": "different-account",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            }
        )
    )

    class _DiskCacheSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 3600
        dashboard_payload_disk_cache_enabled = True
        dashboard_payload_cache_path = str(cache_path)

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    fake_kis = _FakeKISBalance()
    deps = DashboardPayloadDeps(
        settings=_DiskCacheSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=fake_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(
        build_dashboard_payload(deps, DashboardPayloadCache(), include_telegram=False)
    )

    venues = {row["id"]: row for row in payload["venues"]}
    assert fake_kis.balance_calls == 1
    assert venues["kr_stock"]["status"] == "ok"
    assert venues["kr_stock"]["cash_krw"] == 500_000.0
    assert venues["kr_stock"]["cash_krw"] != 9_999_999.0


def test_build_dashboard_payload_shares_inflight_kis_balance_refresh() -> None:
    class _KISCacheSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 3600

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    cache = DashboardPayloadCache()
    fake_kis = _SlowCountingKISBalance()
    deps = DashboardPayloadDeps(
        settings=_KISCacheSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=fake_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    async def run_concurrent() -> list[dict]:
        return await asyncio.gather(
            build_dashboard_payload(deps, cache, include_telegram=False),
            build_dashboard_payload(deps, cache, include_telegram=False),
        )

    first, second = asyncio.run(run_concurrent())

    assert fake_kis.balance_calls == 1
    assert fake_kis.us_balance_calls == 1
    first_venues = {row["id"]: row for row in first["venues"]}
    second_venues = {row["id"]: row for row in second["venues"]}
    assert first_venues["kr_stock"]["cash_krw"] == 500_000.0
    assert second_venues["kr_stock"]["cache_status"] == "fresh"
    assert second_venues["us_stock"]["cache_status"] == "fresh"


def test_build_dashboard_payload_refetches_incomplete_kis_cash_only_cache() -> None:
    class _KISCacheSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 3600

    class _KISWithPositions(_FakeKISBalance):
        async def fetch_balance_assets(self) -> list[dict]:
            self.balance_calls += 1
            return [
                {
                    "asset": "KRW",
                    "kind": "cash",
                    "qty": 4_000_000.0,
                    "value_krw": 4_000_000.0,
                    "net_asset_krw": 4_500_000.0,
                    "pnl_krw": 0.0,
                },
                {
                    "asset": "005930",
                    "asset_name": "삼성전자",
                    "kind": "position",
                    "qty": 5.0,
                    "value_krw": 500_000.0,
                    "pnl_krw": 10_000.0,
                },
            ]

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    cache = DashboardPayloadCache(
        kis_primary_assets=[
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 4_000_000.0,
                "value_krw": 4_000_000.0,
                "net_asset_krw": 4_500_000.0,
                "pnl_krw": 0.0,
            }
        ],
        kis_primary_fetched_at=datetime.now(timezone.utc),
    )
    fake_kis = _KISWithPositions()
    deps = DashboardPayloadDeps(
        settings=_KISCacheSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=fake_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))

    venues = {row["id"]: row for row in payload["venues"]}
    kr_stock_assets = venues["kr_stock"]["assets"]
    assert fake_kis.balance_calls == 1
    assert any(row.get("asset") == "005930" for row in kr_stock_assets)
    samsung = next(row for row in kr_stock_assets if row.get("asset") == "005930")
    assert samsung["symbol"] == "005930"
    assert venues["kr_stock"]["invested_krw"] == 500_000.0


def test_build_dashboard_payload_serializes_kis_account_fetches() -> None:
    class _KISConcurrentSettings(_Settings):
        kis_primary_ready = True
        kis_secondary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 0
        dashboard_kis_balance_error_cooldown_sec = 0

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    events: list[str] = []
    deps = DashboardPayloadDeps(
        settings=_KISConcurrentSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=_SlowPrimaryKISBalance(events),
        kis_secondary=_SlowSecondaryKISBalance(events),
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(
        build_dashboard_payload(deps, DashboardPayloadCache(), include_telegram=False)
    )

    assert events.index("primary_kr_start") < events.index("primary_kr_end")
    assert events.index("primary_kr_end") < events.index("primary_us_start")
    assert events.index("primary_us_end") < events.index("secondary_kr_start")
    venues = {row["id"]: row for row in payload["venues"]}
    assert venues["kr_stock"]["label"] == "국장1"
    assert venues["kr_stock"]["cash_krw"] == 500_000.0
    assert venues["us_stock"]["cash_krw"] == 13_500.0
    assert venues["kr_stock_2"]["label"] == "국장2"
    assert venues["kr_stock_2"]["cash_krw"] == 250_000.0


def test_build_dashboard_payload_fetches_kis_after_slow_crypto_balance_finishes() -> None:
    class _KISAndCryptoSettings(_Settings):
        upbit_ready = True
        kis_primary_ready = True
        kis_secondary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 0
        dashboard_kis_balance_error_cooldown_sec = 0

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "upbit",
                    "label": "업비트",
                    "market": "국내 가상자산",
                    "assets": [],
                },
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    events: list[str] = []
    deps = DashboardPayloadDeps(
        settings=_KISAndCryptoSettings(),
        fx_rates=_FXRates(),
        upbit=_SlowUpbitBalance(events),
        bithumb=None,
        binance=None,
        kis_primary=_SlowPrimaryKISBalance(events),
        kis_secondary=_SlowSecondaryKISBalance(events),
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    asyncio.run(
        build_dashboard_payload(deps, DashboardPayloadCache(), include_telegram=False)
    )

    assert events.index("upbit_end") < events.index("primary_kr_start")
    assert events.index("primary_kr_end") < events.index("primary_us_start")
    assert events.index("primary_us_end") < events.index("secondary_kr_start")


def test_build_dashboard_payload_fetches_crypto_balances_concurrently() -> None:
    class _CryptoSettings(_Settings):
        upbit_ready = True
        bithumb_ready = True
        binance_spot_ready = True
        binance_futures_ready = True

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {"id": "upbit", "label": "업비트", "market": "국내 가상자산", "assets": []},
                {"id": "bithumb", "label": "빗썸", "market": "국내 가상자산", "assets": []},
                {
                    "id": "binance",
                    "label": "바이낸스 현물",
                    "market": "해외 가상자산 (Spot)",
                    "assets": [],
                },
                {
                    "id": "binance_futures",
                    "label": "바이낸스 선물",
                    "market": "해외 가상자산 (Futures)",
                    "assets": [],
                },
            ],
        }

    events: list[str] = []
    deps = DashboardPayloadDeps(
        settings=_CryptoSettings(),
        fx_rates=_FXRates(),
        upbit=_SlowUpbitBalance(events),
        bithumb=_SlowBithumbBalance(events),
        binance=_SlowBinanceBalance(events),
        kis_primary=None,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(
        build_dashboard_payload(deps, DashboardPayloadCache(), include_telegram=False)
    )

    first_end = min(index for index, event in enumerate(events) if event.endswith("_end"))
    assert set(events[:first_end]) == {
        "upbit_start",
        "bithumb_start",
        "binance_spot_start",
        "binance_futures_start",
    }
    venues = {row["id"]: row for row in payload["venues"]}
    assert venues["upbit"]["status"] == "ok"
    assert venues["bithumb"]["status"] == "ok"
    assert venues["binance"]["status"] == "ok"
    assert venues["binance_futures"]["status"] == "ok"


def test_build_dashboard_payload_reuses_recent_crypto_balance_cache() -> None:
    class _CryptoCacheSettings(_Settings):
        upbit_ready = True
        bithumb_ready = True
        binance_spot_ready = True
        binance_futures_ready = True
        dashboard_crypto_balance_cache_ttl_sec = 3600

    class _CountingUpbit:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_balance_assets(self) -> list[dict]:
            self.calls += 1
            return [
                {
                    "asset": "KRW",
                    "kind": "cash",
                    "qty": 100_000.0,
                    "value_krw": 100_000.0,
                    "pnl_krw": 0.0,
                }
            ]

    class _CountingBithumb:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_balance_assets(self) -> list[dict]:
            self.calls += 1
            return [
                {
                    "asset": "KRW",
                    "kind": "cash",
                    "qty": 200_000.0,
                    "value_krw": 200_000.0,
                    "pnl_krw": 0.0,
                }
            ]

    class _CountingBinance:
        def __init__(self) -> None:
            self.spot_calls = 0
            self.futures_calls = 0

        async def fetch_spot_assets(self, *, usdt_krw_rate: float) -> list[dict]:
            self.spot_calls += 1
            return [
                {
                    "asset": "USDT",
                    "kind": "cash",
                    "qty": 10.0,
                    "value_krw": 10.0 * usdt_krw_rate,
                    "pnl_krw": 0.0,
                }
            ]

        async def fetch_futures_assets(self, *, usdt_krw_rate: float) -> list[dict]:
            self.futures_calls += 1
            return [
                {
                    "asset": "USDT-FUT",
                    "kind": "cash",
                    "qty": 5.0,
                    "value_krw": 5.0 * usdt_krw_rate,
                    "pnl_krw": 0.0,
                }
            ]

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {"id": "upbit", "label": "업비트", "market": "국내 가상자산", "assets": []},
                {"id": "bithumb", "label": "빗썸", "market": "국내 가상자산", "assets": []},
                {
                    "id": "binance",
                    "label": "바이낸스 현물",
                    "market": "해외 가상자산 (Spot)",
                    "assets": [],
                },
                {
                    "id": "binance_futures",
                    "label": "바이낸스 선물",
                    "market": "해외 가상자산 (Futures)",
                    "assets": [],
                },
            ],
        }

    upbit = _CountingUpbit()
    bithumb = _CountingBithumb()
    binance = _CountingBinance()
    cache = DashboardPayloadCache()
    deps = DashboardPayloadDeps(
        settings=_CryptoCacheSettings(),
        fx_rates=_FXRates(),
        upbit=upbit,
        bithumb=bithumb,
        binance=binance,
        kis_primary=None,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    first = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))
    second = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))

    assert upbit.calls == 1
    assert bithumb.calls == 1
    assert binance.spot_calls == 1
    assert binance.futures_calls == 1
    first_venues = {row["id"]: row for row in first["venues"]}
    second_venues = {row["id"]: row for row in second["venues"]}
    assert first_venues["binance"]["status"] == "ok"
    assert second_venues["upbit"]["cache_status"] == "fresh"
    assert second_venues["bithumb"]["cache_status"] == "fresh"
    assert second_venues["binance"]["cache_status"] == "fresh"
    assert second_venues["binance_futures"]["cache_status"] == "fresh"


def test_build_dashboard_payload_uses_stale_crypto_cache_within_display_window() -> None:
    class _StaleDisplaySettings(_Settings):
        upbit_ready = True
        dashboard_crypto_balance_cache_ttl_sec = 30
        dashboard_stale_balance_cache_ttl_sec = 600

    class _UnexpectedUpbitFetch:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_balance_assets(self) -> list[dict]:
            self.calls += 1
            raise AssertionError("stale display cache should avoid foreground fetch")

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {"id": "upbit", "label": "업비트", "market": "국내 가상자산", "assets": []},
            ],
        }

    upbit = _UnexpectedUpbitFetch()
    cache = DashboardPayloadCache(
        upbit_assets=[
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 100_000.0,
                "value_krw": 100_000.0,
                "pnl_krw": 0.0,
            }
        ],
        upbit_fetched_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    deps = DashboardPayloadDeps(
        settings=_StaleDisplaySettings(),
        fx_rates=_FXRates(),
        upbit=upbit,
        bithumb=None,
        binance=None,
        kis_primary=None,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))

    venues = {row["id"]: row for row in payload["venues"]}
    assert upbit.calls == 0
    assert venues["upbit"]["status"] == "stale"
    assert venues["upbit"]["cache_status"] == "stale"
    assert venues["upbit"]["assets"][0]["asset"] == "KRW"
    assert any("stale" in str(event.get("message") or "") for event in payload["events"])


def test_build_dashboard_payload_uses_hour_old_kis_stale_cache_without_fetch() -> None:
    class _StaleKISSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 180
        dashboard_stale_balance_cache_ttl_sec = 7200

    class _UnexpectedKISFetch:
        async def fetch_balance_assets(self) -> list[dict]:
            raise AssertionError("hour-old dashboard cache should avoid KIS foreground fetch")

        async def fetch_us_balance_assets(self, usd_krw_rate: float | None = None) -> list[dict]:
            _ = usd_krw_rate
            raise AssertionError("hour-old dashboard cache should avoid KIS US foreground fetch")

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {"id": "kr_stock", "label": "국장", "market": "KRX", "assets": []},
                {"id": "us_stock", "label": "미장", "market": "NASDAQ/NYSE", "assets": []},
            ],
        }

    fetched_at = datetime.now(timezone.utc) - timedelta(seconds=3600)
    cache = DashboardPayloadCache(
        kis_primary_assets=[
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 500_000.0,
                "value_krw": 500_000.0,
                "pnl_krw": 0.0,
            },
            {
                "asset": "005930",
                "kind": "position",
                "qty": 1.0,
                "value_krw": 70_000.0,
                "pnl_krw": 0.0,
            },
        ],
        kis_primary_fetched_at=fetched_at,
        kis_primary_us_assets=[],
        kis_primary_us_fetched_at=fetched_at,
    )
    deps = DashboardPayloadDeps(
        settings=_StaleKISSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=_UnexpectedKISFetch(),
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))

    venues = {row["id"]: row for row in payload["venues"]}
    assert venues["kr_stock"]["status"] == "stale"
    assert venues["kr_stock"]["cache_status"] == "stale"
    assert venues["kr_stock"]["cash_krw"] == 500_000.0
    assert venues["kr_stock"]["cached_at"] == fetched_at.isoformat()


def test_build_dashboard_payload_force_refresh_times_out_kis_and_keeps_cache() -> None:
    class _TimeoutKISSettings(_Settings):
        kis_primary_ready = True
        dashboard_balance_fetch_timeout_sec = 0.001

    class _HangingKISFetch:
        async def fetch_balance_assets(self) -> list[dict]:
            await asyncio.sleep(1)
            return []

        async def fetch_us_balance_assets(self, usd_krw_rate: float | None = None) -> list[dict]:
            _ = usd_krw_rate
            await asyncio.sleep(1)
            return []

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {"id": "kr_stock", "label": "국장", "market": "KRX", "assets": []},
                {"id": "us_stock", "label": "미장", "market": "NASDAQ/NYSE", "assets": []},
            ],
        }

    fetched_at = datetime.now(timezone.utc) - timedelta(seconds=3600)
    cache = DashboardPayloadCache(
        kis_primary_assets=[
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 500_000.0,
                "value_krw": 500_000.0,
                "pnl_krw": 0.0,
            }
        ],
        kis_primary_fetched_at=fetched_at,
    )
    deps = DashboardPayloadDeps(
        settings=_TimeoutKISSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=_HangingKISFetch(),
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(
        build_dashboard_payload(
            deps,
            cache,
            include_telegram=False,
            force_refresh=True,
        )
    )

    venues = {row["id"]: row for row in payload["venues"]}
    assert venues["kr_stock"]["status"] == "stale"
    assert venues["kr_stock"]["cash_krw"] == 500_000.0
    assert "timed out" in venues["kr_stock"]["error_message"]


def test_build_dashboard_payload_can_disable_kis_us_balance_fetch() -> None:
    class _KISNoUSSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_us_balance_enabled = False

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {"id": "kr_stock", "label": "국장", "market": "KRX", "assets": []},
                {"id": "us_stock", "label": "미장", "market": "NASDAQ/NYSE", "assets": []},
            ],
        }

    fake_kis = _FakeKISBalance()
    deps = DashboardPayloadDeps(
        settings=_KISNoUSSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=fake_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(
        build_dashboard_payload(
            deps,
            DashboardPayloadCache(),
            include_telegram=False,
            force_refresh=True,
        )
    )

    venues = {row["id"]: row for row in payload["venues"]}
    assert fake_kis.balance_calls == 1
    assert fake_kis.us_balance_calls == 0
    assert venues["kr_stock"]["status"] == "ok"
    assert venues["us_stock"]["status"] == "disabled"


def test_build_dashboard_payload_force_refresh_bypasses_stale_display_cache() -> None:
    class _StaleDisplaySettings(_Settings):
        upbit_ready = True
        dashboard_crypto_balance_cache_ttl_sec = 30
        dashboard_stale_balance_cache_ttl_sec = 600

    class _CountingUpbitFetch:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_balance_assets(self) -> list[dict]:
            self.calls += 1
            return [
                {
                    "asset": "KRW",
                    "kind": "cash",
                    "qty": 200_000.0,
                    "value_krw": 200_000.0,
                    "pnl_krw": 0.0,
                }
            ]

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {"id": "upbit", "label": "업비트", "market": "국내 가상자산", "assets": []},
            ],
        }

    upbit = _CountingUpbitFetch()
    cache = DashboardPayloadCache(
        upbit_assets=[
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 100_000.0,
                "value_krw": 100_000.0,
                "pnl_krw": 0.0,
            }
        ],
        upbit_fetched_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    deps = DashboardPayloadDeps(
        settings=_StaleDisplaySettings(),
        fx_rates=_FXRates(),
        upbit=upbit,
        bithumb=None,
        binance=None,
        kis_primary=None,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    payload = asyncio.run(
        build_dashboard_payload(
            deps,
            cache,
            include_telegram=False,
            force_refresh=True,
        )
    )

    venues = {row["id"]: row for row in payload["venues"]}
    assert upbit.calls == 1
    assert venues["upbit"]["status"] == "ok"
    assert venues["upbit"]["assets"][0]["qty"] == 200_000.0


def test_build_dashboard_payload_cools_down_repeated_kis_balance_errors() -> None:
    class _KISCacheSettings(_Settings):
        kis_primary_ready = True
        dashboard_kis_balance_cache_ttl_sec = 0
        dashboard_kis_balance_error_cooldown_sec = 60

    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [
                {
                    "id": "kr_stock",
                    "label": "국장",
                    "market": "KRX",
                    "assets": [],
                },
                {
                    "id": "us_stock",
                    "label": "미장",
                    "market": "NASDAQ/NYSE",
                    "assets": [],
                },
            ],
        }

    cache = DashboardPayloadCache()
    fake_kis = _FailingKISBalance()
    deps = DashboardPayloadDeps(
        settings=_KISCacheSettings(),
        fx_rates=_FXRates(),
        upbit=None,
        bithumb=None,
        binance=None,
        kis_primary=fake_kis,
        kis_secondary=None,
        runtime_reader=_RuntimeReader(),
        research_reader=_ResearchReader(),
        telegram=_Telegram(),
        dashboard_template=mock_dashboard,
        replace_venue_assets=replace_venue_assets,
        upsert_venue_assets=upsert_venue_assets,
        logger=_Logger(),
    )

    first = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))
    second = asyncio.run(build_dashboard_payload(deps, cache, include_telegram=False))
    forced = asyncio.run(
        build_dashboard_payload(
            deps,
            cache,
            include_telegram=False,
            force_refresh=True,
        )
    )

    first_venues = {row["id"]: row for row in first["venues"]}
    second_venues = {row["id"]: row for row in second["venues"]}
    forced_venues = {row["id"]: row for row in forced["venues"]}
    assert fake_kis.balance_calls == 2
    assert fake_kis.us_balance_calls == 2
    assert first_venues["kr_stock"]["status"] == "error"
    assert second_venues["kr_stock"]["status"] == "error_cooldown"
    assert second_venues["kr_stock"]["cache_status"] == "error_cooldown"
    assert "kis balance request rejected" in second_venues["kr_stock"]["error_message"]
    assert second_venues["us_stock"]["status"] == "error_cooldown"
    assert second_venues["us_stock"]["cache_status"] == "error_cooldown"
    assert forced_venues["kr_stock"]["status"] == "error"
    assert "kis balance request rejected" in forced_venues["kr_stock"]["error_message"]
    assert forced_venues["us_stock"]["status"] == "error"
    assert "kis us balance request rejected" in forced_venues["us_stock"]["error_message"]


def test_build_dashboard_payload_uses_injected_deps_for_disabled_integrations() -> None:
    def mock_dashboard() -> dict:
        return {
            "events": [
                {"type": "telegram", "message": "Telegram 브릿지 연동 대기 중"},
            ],
            "venues": [
                {
                    "id": "upbit",
                    "label": "업비트",
                    "market": "국내 가상자산",
                    "assets": [{"asset": "KRW", "kind": "cash", "value_krw": 100}],
                },
                {
                    "id": "binance",
                    "label": "바이낸스 현물",
                    "market": "해외 가상자산 (Spot)",
                    "assets": [{"asset": "USDT", "kind": "cash", "value_krw": 100}],
                },
            ],
        }

    payload = asyncio.run(
        build_dashboard_payload(
            DashboardPayloadDeps(
                settings=_Settings(),
                fx_rates=_FXRates(),
                upbit=None,
                bithumb=None,
                binance=None,
                kis_primary=None,
                kis_secondary=None,
                runtime_reader=_RuntimeReader(),
                research_reader=_ResearchReader(),
                telegram=_Telegram(),
                dashboard_template=mock_dashboard,
                replace_venue_assets=replace_venue_assets,
                upsert_venue_assets=upsert_venue_assets,
                logger=_Logger(),
            ),
            DashboardPayloadCache(),
            include_telegram=True,
        ),
    )

    venues = {row["id"]: row for row in payload["venues"]}
    assert venues["upbit"]["status"] == "not_configured"
    assert venues["upbit"]["assets"] == []
    assert venues["binance"]["status"] == "not_configured"
    assert venues["binance"]["assets"] == []
    assert payload["research"]["status"] == "disabled"
    assert payload["runtime"]["status"] == "fresh"
    assert payload["telegram"] == {"ready": True}


def test_build_dashboard_payload_surfaces_reports_rag_when_legacy_research_disabled() -> None:
    def mock_dashboard() -> dict:
        return {
            "events": [],
            "venues": [],
        }

    def reports_status() -> dict:
        return {
            "status": "ok",
            "enabled": True,
            "report_count": 5281,
            "latest_report_at": "2026-06-30T19:21:31.716193+00:00",
            "latest_published_at": "2026-06-30",
            "symbol_count": 4109,
            "symbol_link_count": 11727,
            "rag_available": True,
            "rag_count": 52452,
            "fundamentals_symbol_count": 285,
            "fundamentals_latest_symbols_stale_ratio": 0.125,
            "intelligence": {
                "codex_runtime": {
                    "model": "gpt-5.5",
                    "reasoning_effort": "xhigh",
                },
            },
        }

    payload = asyncio.run(
        build_dashboard_payload(
            DashboardPayloadDeps(
                settings=_Settings(),
                fx_rates=_FXRates(),
                upbit=None,
                bithumb=None,
                binance=None,
                kis_primary=None,
                kis_secondary=None,
                runtime_reader=_RuntimeReader(),
                research_reader=_ResearchReader(),
                telegram=_Telegram(),
                dashboard_template=mock_dashboard,
                replace_venue_assets=replace_venue_assets,
                upsert_venue_assets=upsert_venue_assets,
                logger=_Logger(),
                research_status_provider=reports_status,
            ),
            DashboardPayloadCache(),
            include_telegram=True,
        ),
    )

    assert payload["research"]["status"] == "ok"
    assert payload["research"]["source"] == "reports_rag"
    assert payload["research"]["count"] == 5281
    assert payload["research"]["rag_available"] is True
    assert payload["research"]["rag_count"] == 52452
    assert payload["research"]["fundamentals_symbol_count"] == 285
    assert payload["research"]["fundamentals_latest_symbols_stale_ratio"] == 0.125
    assert payload["research"]["model"] == "gpt-5.5"
    assert payload["research"]["reasoning_effort"] == "xhigh"
    messages = [str(row.get("message") or "") for row in payload["events"]]
    assert any("리포트/RAG" in message for message in messages)
    assert not any(
        "연동 대기" in str(row.get("message") or "")
        for row in payload["events"]
        if row.get("type") == "telegram"
    )
