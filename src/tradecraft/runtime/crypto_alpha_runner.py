from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.binance import BinanceAdapter, BinanceConfig
from tradecraft.services.crypto_alpha import CryptoAlphaConfig, CryptoAlphaService

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


def _setting(settings: AppSettings, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _build_binance_adapter(settings: AppSettings) -> BinanceAdapter:
    return BinanceAdapter(
        BinanceConfig(
            spot_api_key=settings.binance_spot_api_key,
            spot_api_secret=settings.binance_spot_api_secret,
            spot_base_url=settings.binance_spot_base_url,
            futures_api_key=settings.binance_futures_api_key,
            futures_api_secret=settings.binance_futures_api_secret,
            futures_base_url=settings.binance_futures_base_url,
            usdt_krw_rate=settings.binance_usdt_krw,
        )
    )


def build_crypto_alpha_service(settings: AppSettings) -> CryptoAlphaService:
    return CryptoAlphaService(
        config=CryptoAlphaConfig(
            db_path=str(_setting(settings, "crypto_alpha_db_path", ".runtime/crypto_alpha.db")),
            source_ids=str(
                _setting(
                    settings,
                    "crypto_alpha_source_ids",
                    "binance_announcements,coinbase_blog,kraken_blog",
                )
            ),
            rate_limit_sec=float(_setting(settings, "crypto_alpha_rate_limit_sec", 2.0)),
            context_limit=int(_setting(settings, "crypto_alpha_context_limit", 12)),
            llm_model=str(
                _setting(settings, "crypto_alpha_llm_model", "gpt-5.5")
            ),
            llm_reasoning_effort=str(
                _setting(settings, "crypto_alpha_llm_reasoning_effort", "xhigh")
            ),
        ),
        binance=_build_binance_adapter(settings),
    )


async def run_crypto_alpha_loop(
    *,
    settings: AppSettings,
    service: CryptoAlphaService | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    store = RuntimeStateStore(
        str(_setting(settings, "crypto_alpha_state_path", ".runtime/crypto_alpha.json"))
    )
    if not bool(_setting(settings, "crypto_alpha_enabled", True)):
        store.write_snapshot(
            {
                "service": "tradecraft-crypto-alpha",
                "status": "disabled",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    try:
        resolved = service or build_crypto_alpha_service(settings)
    except Exception as exc:
        logger.exception("crypto alpha service build failed")
        store.write_snapshot(
            {
                "service": "tradecraft-crypto-alpha",
                "status": "error",
                "error_message": str(exc),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    crawl_interval = max(int(_setting(settings, "crypto_alpha_crawl_interval_sec", 3600)), 300)
    outcome_interval = max(
        int(_setting(settings, "crypto_alpha_outcome_interval_sec", 900)),
        300,
    )
    once = bool(_setting(settings, "crypto_alpha_once", False))
    cycle = 0
    last_outcome_at = 0.0

    while True:
        cycle += 1
        now_ts = datetime.now(timezone.utc).timestamp()
        outcome: dict[str, Any] = {"status": "skipped", "reason": "cadence"}
        try:
            collect = await resolved.collect_once()
            if now_ts - last_outcome_at >= outcome_interval:
                outcome = await resolved.label_due_outcomes()
                last_outcome_at = now_ts
            status = str(collect.get("status") or "ok")
        except Exception as exc:
            logger.exception("crypto alpha cycle failed")
            status = "error"
            collect = {"status": "error", "error_message": str(exc)}
        snapshot = {
            "service": "tradecraft-crypto-alpha",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "crawl_interval_sec": crawl_interval,
            "outcome_interval_sec": outcome_interval,
            "collect": collect,
            "outcome": outcome,
            "service_status": resolved.status(),
        }
        store.write_snapshot(snapshot)
        logger.info(
            "crypto alpha cycle=%s status=%s events=%s outcome=%s",
            cycle,
            status,
            collect.get("created_events"),
            outcome.get("status"),
        )
        if once:
            return
        await sleep(float(crawl_interval))


def run() -> None:
    write_current_runner_pid("crypto_alpha")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        if not bool(_setting(settings, "crypto_alpha_enabled", True)):
            logger.info("crypto alpha disabled: TRADECRAFT_CRYPTO_ALPHA_ENABLED=false")
            return
        asyncio.run(run_crypto_alpha_loop(settings=settings))
    except KeyboardInterrupt:
        logger.info("crypto alpha runner interrupted; stopping")
    finally:
        clear_current_runner_pid("crypto_alpha")
