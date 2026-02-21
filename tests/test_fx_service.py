from __future__ import annotations

import asyncio

import pytest

from tradecraft.services.fx import FxRateConfig, FxRateService, FxRateSnapshot


def test_fx_service_prefers_external_sources(monkeypatch) -> None:
    service = FxRateService(
        FxRateConfig(
            fallback_usdt_krw=1387.0,
            fallback_usd_krw=1387.0,
            cache_ttl_sec=30,
        )
    )

    async def fake_upbit(_):
        return 1480.0

    async def fake_bithumb(_):
        return 1479.0

    async def fake_manana(_):
        return 1440.5

    async def fake_er_api(_):
        return 1438.0

    monkeypatch.setattr(service, "_fetch_upbit_usdt_krw", fake_upbit)
    monkeypatch.setattr(service, "_fetch_bithumb_usdt_krw", fake_bithumb)
    monkeypatch.setattr(service, "_fetch_manana_usd_krw", fake_manana)
    monkeypatch.setattr(service, "_fetch_er_api_usd_krw", fake_er_api)

    snapshot = asyncio.run(service.get_snapshot())
    assert snapshot["usdt_krw"] == pytest.approx(1480.0)
    assert snapshot["usd_krw"] == pytest.approx(1440.5)
    assert snapshot["usdt_source"] == "upbit"
    assert snapshot["usd_source"] == "manana"


def test_fx_service_falls_back_to_proxy_then_fallback(monkeypatch) -> None:
    service = FxRateService(
        FxRateConfig(
            fallback_usdt_krw=1387.0,
            fallback_usd_krw=1391.0,
            cache_ttl_sec=30,
        )
    )

    async def fake_upbit(_):
        return 0.0

    async def fake_bithumb(_):
        return 1475.0

    async def fake_manana(_):
        return 0.0

    async def fake_er_api(_):
        return 0.0

    monkeypatch.setattr(service, "_fetch_upbit_usdt_krw", fake_upbit)
    monkeypatch.setattr(service, "_fetch_bithumb_usdt_krw", fake_bithumb)
    monkeypatch.setattr(service, "_fetch_manana_usd_krw", fake_manana)
    monkeypatch.setattr(service, "_fetch_er_api_usd_krw", fake_er_api)

    snapshot = asyncio.run(service.get_snapshot())
    assert snapshot["usdt_krw"] == pytest.approx(1475.0)
    assert snapshot["usdt_source"] == "bithumb"
    assert snapshot["usd_krw"] == pytest.approx(1475.0)
    assert snapshot["usd_source"] == "bithumb_proxy"

    service_fallback = FxRateService(
        FxRateConfig(
            fallback_usdt_krw=1387.0,
            fallback_usd_krw=1391.0,
            cache_ttl_sec=30,
        )
    )

    async def zero(_):
        return 0.0

    monkeypatch.setattr(service_fallback, "_fetch_upbit_usdt_krw", zero)
    monkeypatch.setattr(service_fallback, "_fetch_bithumb_usdt_krw", zero)
    monkeypatch.setattr(service_fallback, "_fetch_manana_usd_krw", zero)
    monkeypatch.setattr(service_fallback, "_fetch_er_api_usd_krw", zero)

    snapshot2 = asyncio.run(service_fallback.get_snapshot())
    assert snapshot2["usdt_krw"] == pytest.approx(1387.0)
    assert snapshot2["usdt_source"] == "fallback"
    assert snapshot2["usd_krw"] == pytest.approx(1391.0)
    assert snapshot2["usd_source"] == "fallback"


def test_fx_service_cache(monkeypatch) -> None:
    service = FxRateService(FxRateConfig(cache_ttl_sec=3600))
    call_count = {"count": 0}

    async def fake_fetch_snapshot():
        call_count["count"] += 1
        return FxRateSnapshot(
            usdt_krw=1400.0,
            usd_krw=1350.0,
            usdt_source="test",
            usd_source="test",
            fetched_at="2026-02-15T00:00:00+00:00",
        )

    monkeypatch.setattr(service, "_fetch_snapshot", fake_fetch_snapshot)

    first = asyncio.run(service.get_snapshot())
    second = asyncio.run(service.get_snapshot())
    assert first == second
    assert call_count["count"] == 1
