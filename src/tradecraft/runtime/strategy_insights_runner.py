from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
    StrategyInsightCollector,
)

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


def _build_strategy_intelligence(settings: AppSettings) -> StrategyIntelligenceEngine:
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
    return StrategyIntelligenceEngine(
        repository=stack.repository,
        rag_store=stack.rag_store,
        llm_bridge=bridge,
        config=StrategyIntelligenceConfig(
            insight_db_path=settings.strategy_insight_db_path,
            model_timeout_ms=settings.llm_bridge_timeout_ms,
        ),
    )


def _build_collector(settings: AppSettings) -> StrategyInsightCollector:
    return StrategyInsightCollector(
        engine=_build_strategy_intelligence(settings),
        sources=settings.strategy_insight_source_list,
        timeout_sec=settings.strategy_insight_request_timeout_sec,
    )


async def run_strategy_insight_loop(
    *,
    settings: AppSettings | None = None,
    collector: StrategyInsightCollector | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    resolved_settings = settings or AppSettings()
    resolved_collector = collector or _build_collector(resolved_settings)
    interval = max(int(resolved_settings.strategy_insight_collect_interval_sec), 30)
    store = RuntimeStateStore(resolved_settings.strategy_insight_state_path)
    cycle = 0

    while True:
        cycle += 1
        try:
            result = await resolved_collector.collect_once()
            status = str(result.get("status") or "ok")
        except Exception as exc:
            logger.exception("strategy insight collection failed")
            status = "error"
            result = {"status": "error", "detail": str(exc)}

        snapshot: dict[str, Any] = {
            "service": "tradecraft-strategy-insights",
            "status": status,
            "cycle": cycle,
            "updated_at": utc_now_iso(),
            "sources_configured": len(resolved_settings.strategy_insight_source_list),
            "interval_sec": interval,
            "result": result,
        }
        store.write_snapshot(snapshot)
        logger.info(
            "strategy insight collection finished: cycle=%s status=%s inserted=%s",
            cycle,
            status,
            result.get("inserted"),
        )

        if resolved_settings.strategy_insight_once:
            return
        await sleep(interval)


def run() -> None:
    write_current_runner_pid("strategy_insights")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    asyncio.run(run_strategy_insight_loop())


if __name__ == "__main__":
    run()
