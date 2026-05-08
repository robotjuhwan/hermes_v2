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
from tradecraft.services.kis_block_trader import (
    KISBlockTrader,
    KISBlockTraderConfig,
    run_due_manager,
)
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
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


def _build_block_trader(settings: AppSettings) -> KISBlockTrader:
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
    kis = KISAdapter(
        KISConfig(
            app_key=settings.kis_primary_app_key,
            app_secret=settings.kis_primary_app_secret,
            account_no=settings.kis_primary_account_no,
            product_code=settings.kis_primary_product_code,
            base_url=settings.kis_base_url,
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
    market_judgment = MarketJudgmentEngine(
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
        research_feed_provider=lambda: read_active_research_feed(settings)[0],
        watchlist=_symbols_from_csv(settings.valuation_watchlist),
    )

    def read_research() -> dict[str, Any] | None:
        return read_active_research_feed(settings)[0]

    investment_memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=settings.investment_memory_root_path,
            db_path=settings.investment_memory_db_path,
            strategy_md_path=settings.research_strategy_md_path,
            policy_mode=settings.investment_memory_policy_mode,
            persona_tone=settings.investment_memory_persona_tone,
            telegram_enabled=settings.investment_memory_send_telegram,
            context_max_chars=settings.investment_memory_context_max_chars,
        ),
        llm_bridge=bridge,
    )

    return KISBlockTrader(
        config=KISBlockTraderConfig(
            db_path=settings.kis_block_trader_db_path,
            state_path=settings.kis_block_trader_state_path,
            enabled=settings.kis_block_trader_enabled,
            execute_orders=settings.kis_block_trader_execute_orders,
            rule_interval_sec=settings.kis_block_trader_rule_interval_sec,
            manager_interval_sec=settings.kis_block_trader_manager_interval_sec,
            aggressive_limit_bps=settings.kis_block_trader_aggressive_limit_bps,
            pending_reconcile_timeout_sec=(
                settings.kis_block_trader_pending_reconcile_timeout_sec
            ),
            max_manager_symbols=settings.kis_block_trader_max_manager_symbols,
            use_naver_fallback=settings.market_judge_use_naver_fallback,
            manager_query=settings.kis_block_trader_manager_query,
        ),
        kis=kis,
        llm_bridge=bridge,
        strategy_engine=strategy_engine,
        market_judgment_provider=market_judgment,
        research_feed_provider=read_research,
        memory_context_provider=investment_memory.context_pack,
    )


async def run_kis_block_trader_loop(
    *,
    settings: AppSettings | None = None,
    trader: KISBlockTrader | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    resolved_settings = settings or AppSettings()
    store = RuntimeStateStore(resolved_settings.kis_block_trader_state_path)
    resolved_trader = trader or _build_block_trader(resolved_settings)
    interval = max(int(resolved_settings.kis_block_trader_rule_interval_sec), 1)
    cycle = 0
    last_manager_at: datetime | None = None

    while True:
        cycle += 1
        manager_used = False
        manager_result: dict[str, Any] | None = None
        try:
            manager_used, manager_result = await run_due_manager(
                resolved_trader,
                last_manager_at=last_manager_at,
            )
            if manager_used:
                last_manager_at = datetime.now(timezone.utc)
            tick_result = await resolved_trader.executor_tick()
            status = str(tick_result.get("status") or "ok")
        except Exception as exc:
            logger.exception("kis block trader cycle failed")
            status = "error"
            tick_result = {"status": "error", "detail": str(exc)}

        snapshot = {
            "service": "tradecraft-kis-block-trader",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interval_sec": interval,
            "manager_interval_sec": int(
                resolved_settings.kis_block_trader_manager_interval_sec
            ),
            "manager_used": manager_used,
            "manager_result": manager_result,
            "tick_result": tick_result,
        }
        store.write_snapshot(snapshot)
        logger.info(
            "kis block trader cycle=%s status=%s manager=%s actions=%s",
            cycle,
            status,
            manager_used,
            len(list(tick_result.get("actions") or []))
            if isinstance(tick_result, dict)
            else 0,
        )
        if resolved_settings.kis_block_trader_once:
            return
        await sleep(float(interval))


def run() -> None:
    write_current_runner_pid("kis_block_trader")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not settings.kis_block_trader_enabled:
        logger.info(
            "kis block trader disabled: TRADECRAFT_KIS_BLOCK_TRADER_ENABLED=false"
        )
        return
    if not settings.kis_primary_ready:
        logger.info("kis block trader disabled: KIS primary account not configured")
        return
    asyncio.run(run_kis_block_trader_loop(settings=settings))


if __name__ == "__main__":
    run()
