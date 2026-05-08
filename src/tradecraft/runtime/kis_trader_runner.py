from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.kis_llm_trader import KISLLMTrader, KISLLMTraderConfig
from tradecraft.services.naver_reports import NaverReportRepository
from tradecraft.services.rag_store import RAGStore, RAGStoreConfig

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def _is_krx_open_at(now: datetime) -> bool:
    local = now.astimezone(KST)
    if local.weekday() >= 5:
        return False
    hhmm = local.hour * 100 + local.minute
    return 900 <= hhmm <= 1520


def _seconds_until_next_krx_open(now: datetime) -> float:
    local = now.astimezone(KST)
    next_open = local.replace(hour=9, minute=0, second=0, microsecond=0)
    if local.weekday() >= 5 or local >= next_open:
        probe = local
        while True:
            probe = probe + timedelta(days=1)
            if probe.weekday() < 5:
                break
        next_open = probe.replace(hour=9, minute=0, second=0, microsecond=0)
    return max((next_open - local).total_seconds(), 1.0)


def _compute_sleep_seconds(interval_sec: float, now: datetime | None = None) -> float:
    base = max(float(interval_sec), 1.0)
    current = now.astimezone(KST) if isinstance(now, datetime) else datetime.now(KST)
    if _is_krx_open_at(current):
        return base
    return min(base, _seconds_until_next_krx_open(current))


def _build_trader(settings: AppSettings) -> KISLLMTrader:
    kis = KISAdapter(
        KISConfig(
            app_key=settings.kis_primary_app_key,
            app_secret=settings.kis_primary_app_secret,
            account_no=settings.kis_primary_account_no,
            product_code=settings.kis_primary_product_code,
            base_url=settings.kis_base_url,
        )
    )
    report_repo = NaverReportRepository(settings.naver_reports_db_path)
    rag_store = (
        RAGStore(
            RAGStoreConfig(
                persist_path=settings.rag_persist_path,
                collection_name=settings.rag_collection_name,
                sync_batch_size=settings.rag_sync_batch_size,
                skip_existing=settings.rag_skip_existing,
                query_oversample_factor=settings.rag_query_oversample_factor,
            )
        )
        if settings.rag_enabled
        else None
    )
    config = KISLLMTraderConfig(
        research_state_path=settings.research_state_path,
        trader_state_path=settings.kis_trader_state_path,
        llm_command=settings.kis_trader_llm_command,
        llm_bridge_command=settings.llm_bridge_command,
        llm_bridge_args=settings.llm_bridge_args,
        llm_bridge_url=settings.llm_bridge_url,
        llm_bridge_token=settings.llm_bridge_token,
        llm_bridge_timeout_ms=settings.llm_bridge_timeout_ms,
        llm_model=settings.llm_model,
        execute_orders=settings.kis_trader_execute_orders,
        persona=settings.kis_trader_persona,
        max_orders_per_cycle=settings.kis_trader_max_orders_per_cycle,
        max_budget_per_order_krw=settings.kis_trader_max_budget_per_order_krw,
        min_confidence=settings.kis_trader_min_confidence,
        default_order_type=settings.kis_trader_default_order_type,
        allow_sell=settings.kis_trader_allow_sell,
        max_candidate_codes=settings.kis_trader_max_candidate_codes,
        report_context_top_k=settings.kis_trader_report_context_top_k,
    )
    return KISLLMTrader(
        config=config,
        kis=kis,
        report_repo=report_repo,
        rag_store=rag_store,
    )


def run() -> None:
    write_current_runner_pid("kis_trader")
    settings = AppSettings()
    interval = max(int(settings.kis_trader_interval_sec), 30)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if not settings.kis_trader_enabled:
        logger.info("kis trader disabled: TRADECRAFT_KIS_TRADER_ENABLED=false")
        return
    if not settings.kis_primary_ready:
        logger.info("kis trader disabled: KIS primary account not configured")
        return

    trader = _build_trader(settings)
    logger.info(
        "kis trader started: state_path=%s interval=%ss",
        settings.kis_trader_state_path,
        interval,
    )

    cycle = 0
    while True:
        cycle += 1
        try:
            snapshot = asyncio.run(trader.run_once())
            logger.info(
                "kis trader cycle=%s status=%s orders=%s",
                cycle,
                str(snapshot.get("status") or "unknown"),
                len(list(snapshot.get("orders") or [])),
            )
        except Exception as exc:
            logger.warning("kis trader cycle failed: %s", exc)
        sleep_sec = _compute_sleep_seconds(interval)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    run()
