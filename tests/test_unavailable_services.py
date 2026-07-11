from __future__ import annotations

import asyncio

from tradecraft.services.unavailable_services import (
    UnavailableCryptoAlphaService,
    UnavailableCryptoMarketResearchService,
)


def test_unavailable_crypto_market_research_payloads_preserve_status_shape() -> None:
    service = UnavailableCryptoMarketResearchService(
        reason="missing dependency",
        db_path=".runtime/crypto_market_research.db",
    )

    status = service.status()
    context = service.latest_context(symbols=["BTCUSDT"], limit=3)
    collect = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    research = asyncio.run(service.run_research_once(["BTCUSDT"]))

    assert status["available"] is False
    assert status["db_path"] == ".runtime/crypto_market_research.db"
    assert context["symbols"] == ["BTCUSDT"]
    assert context["reason"] == "missing dependency"
    assert collect == {
        "status": "skipped",
        "available": False,
        "symbols": ["BTCUSDT"],
        "reason": "missing dependency",
    }
    assert research["status"] == "skipped"
    assert research["symbols"] == ["BTCUSDT"]


def test_unavailable_crypto_alpha_payloads_preserve_status_shape() -> None:
    service = UnavailableCryptoAlphaService(
        reason="missing dependency",
        db_path=".runtime/crypto_alpha.db",
    )

    status = service.status()
    context = service.context_pack(symbols=["ETHUSDT"], limit=2)
    collect = asyncio.run(service.collect_once())
    labels = asyncio.run(service.label_due_outcomes())

    assert status["available"] is False
    assert status["db_path"] == ".runtime/crypto_alpha.db"
    assert context["scope"] == "binance_crypto_alpha"
    assert context["symbols"] == ["ETHUSDT"]
    assert context["data_gaps"] == ["crypto_alpha_unavailable"]
    assert collect == {
        "status": "skipped",
        "available": False,
        "reason": "missing dependency",
    }
    assert labels["labeled"] == 0
