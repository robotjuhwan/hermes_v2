from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig
from tradecraft.services.market_judgment import (
    MarketJudgmentConfig,
    MarketJudgmentEngine,
)
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
)
from tradecraft.services.symbol_fundamentals import (
    SymbolFundamentalsConfig,
    SymbolFundamentalsService,
)

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


def _symbols_from_csv(value: Any) -> list[str]:
    out: list[str] = []
    for item in str(value or "").replace(";", ",").split(","):
        symbol = item.strip()
        if len(symbol) == 6 and symbol.isdigit() and symbol not in out:
            out.append(symbol)
    return out


def _build_market_judge(settings: AppSettings) -> MarketJudgmentEngine:
    stack = build_report_intelligence_stack(settings)
    bridge = LLMBridge(
        LLMBridgeConfig(
            command=settings.llm_bridge_command,
            args=settings.llm_bridge_args,
            url=settings.llm_bridge_url,
            token=settings.llm_bridge_token,
            timeout_ms=settings.llm_bridge_timeout_ms,
            model=settings.llm_model,
        )
    )
    fundamentals = SymbolFundamentalsService(
        SymbolFundamentalsConfig(
            db_path=settings.valuation_db_path,
            timeout_sec=settings.valuation_timeout_sec,
            min_refresh_hours=settings.valuation_min_refresh_hours,
            max_symbols_per_collect=settings.valuation_max_symbols_per_collect,
        )
    )
    strategy_engine = StrategyIntelligenceEngine(
        repository=stack.repository,
        rag_store=stack.rag_store,
        llm_bridge=bridge,
        fundamentals_repository=fundamentals,
        config=StrategyIntelligenceConfig(
            insight_db_path=settings.strategy_insight_db_path,
            model_timeout_ms=settings.llm_bridge_timeout_ms,
        ),
    )
    kis = KISAdapter(
        KISConfig(
            app_key=settings.kis_primary_app_key,
            app_secret=settings.kis_primary_app_secret,
            account_no=settings.kis_primary_account_no,
            product_code=settings.kis_primary_product_code,
            base_url=settings.kis_base_url,
        )
    )
    def read_research() -> dict[str, Any] | None:
        return read_active_research_feed(settings)[0]

    return MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=settings.market_judge_db_path,
            state_path=settings.market_judge_state_path,
            quote_interval_sec=settings.market_quote_interval_sec,
            judge_interval_sec=settings.market_judge_interval_sec,
            max_symbols=settings.market_judge_max_symbols,
            llm_max_symbols=settings.market_judge_llm_max_symbols,
            use_naver_fallback=settings.market_judge_use_naver_fallback,
            query=settings.market_judge_query,
        ),
        kis=kis,
        llm_bridge=bridge,
        strategy_engine=strategy_engine,
        report_repository=stack.repository,
        fundamentals_repository=fundamentals,
        rag_store=stack.rag_store,
        research_feed_provider=read_research,
        watchlist=_symbols_from_csv(settings.valuation_watchlist),
    )


def _should_use_llm(
    *,
    last_judged_at: datetime | None,
    interval_sec: int,
    clock: dict[str, Any],
) -> bool:
    session = str(clock.get("session") or "closed")
    if session == "closed":
        return False
    if session == "post_close_review":
        if last_judged_at is None:
            return True
        local_date = datetime.now(timezone.utc).date()
        return last_judged_at.date() != local_date
    if last_judged_at is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last_judged_at).total_seconds()
    return elapsed >= max(int(interval_sec), 60)


async def run_market_judge_loop(
    *,
    settings: AppSettings | None = None,
    engine: MarketJudgmentEngine | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    resolved_settings = settings or AppSettings()
    store = RuntimeStateStore(resolved_settings.market_judge_state_path)
    resolved_engine = engine or _build_market_judge(resolved_settings)
    quote_interval = max(int(resolved_settings.market_quote_interval_sec), 15)
    judge_interval = max(int(resolved_settings.market_judge_interval_sec), 60)
    cycle = 0
    last_judged_at: datetime | None = None

    while True:
        cycle += 1
        clock = resolved_engine.clock()
        use_llm = _should_use_llm(
            last_judged_at=last_judged_at,
            interval_sec=judge_interval,
            clock=clock,
        )
        try:
            result = await resolved_engine.run_once(use_llm=use_llm)
            status = str(result.get("status") or "ok")
            if use_llm:
                last_judged_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.exception("market judge cycle failed")
            status = "error"
            result = {"status": "error", "detail": str(exc), "clock": clock}

        snapshot = {
            "service": "tradecraft-market-judge",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interval_sec": quote_interval,
            "judge_interval_sec": judge_interval,
            "llm_used": use_llm,
            "result": result,
        }
        store.write_snapshot(snapshot)
        logger.info(
            "market judge cycle=%s status=%s llm=%s judgments=%s",
            cycle,
            status,
            use_llm,
            len(list(result.get("judgments") or [])) if isinstance(result, dict) else 0,
        )

        if resolved_settings.market_judge_once:
            return
        session = str(clock.get("session") or "closed")
        sleep_sec = quote_interval if session != "closed" else min(judge_interval, 900)
        await sleep(max(float(sleep_sec), 1.0))


def run() -> None:
    write_current_runner_pid("market_judge")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not settings.market_judge_enabled:
        logger.info("market judge disabled: TRADECRAFT_MARKET_JUDGE_ENABLED=false")
        return
    asyncio.run(run_market_judge_loop(settings=settings))


if __name__ == "__main__":
    run()
