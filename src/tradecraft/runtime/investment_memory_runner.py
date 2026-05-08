from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.kis_block_trader_runner import _build_block_trader
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig
from tradecraft.services.telegram import TelegramBridge, TelegramConfig

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


def _build_memory_service(settings: AppSettings) -> InvestmentMemoryService:
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
    telegram = TelegramBridge(
        TelegramConfig(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    )
    return InvestmentMemoryService(
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
        telegram=telegram,
    )


async def _build_context(settings: AppSettings) -> dict[str, Any]:
    context: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        trader = _build_block_trader(settings)
        block_snapshot = await trader.snapshot()
        context["blocks"] = block_snapshot
        context["account"] = block_snapshot.get("account") or {}
        context["clock"] = (block_snapshot.get("summary") or {}).get("clock") or {}
        context["latest_manager_run"] = block_snapshot.get("latest_manager_run") or {}
    except Exception as exc:
        logger.warning("investment memory block context failed: %s", exc)
        context["blocks"] = {"status": "error", "error_message": str(exc)}
        context["account"] = {}
    try:
        research, research_status = read_active_research_feed(settings)
        context["research"] = (
            research if isinstance(research, dict) else {"status": research_status}
        )
    except Exception as exc:
        logger.warning("investment memory research context failed: %s", exc)
        context["research"] = {"status": "error", "error_message": str(exc)}
    return context


async def run_investment_memory_loop(
    *,
    settings: AppSettings | None = None,
    service: InvestmentMemoryService | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    resolved_settings = settings or AppSettings()
    resolved_service = service or _build_memory_service(resolved_settings)
    state_store = RuntimeStateStore(resolved_settings.investment_memory_state_path)
    interval = max(int(resolved_settings.investment_memory_poll_interval_sec), 10)
    cycle = 0
    resolved_service.initialize()

    while True:
        cycle += 1
        due_slots = resolved_service.due_slots()
        results: list[dict[str, Any]] = []
        status = "idle"
        try:
            if due_slots:
                context = await _build_context(resolved_settings)
                for slot in due_slots:
                    result = await resolved_service.run_ritual(
                        slot=slot,
                        context=context,
                        send_telegram=bool(
                            resolved_settings.investment_memory_send_telegram
                        ),
                    )
                    results.append(result)
                status = "ok"
        except Exception as exc:
            logger.exception("investment memory cycle failed")
            status = "error"
            results.append({"status": "error", "error_message": str(exc)})

        snapshot = {
            "service": "tradecraft-investment-memory",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interval_sec": interval,
            "due_slots": due_slots,
            "results": results,
            "memory": resolved_service.status(),
        }
        state_store.write_snapshot(snapshot)
        logger.info(
            "investment memory cycle=%s status=%s due=%s",
            cycle,
            status,
            ",".join(due_slots) if due_slots else "-",
        )
        if resolved_settings.investment_memory_once:
            return
        await sleep(float(interval))


def run() -> None:
    write_current_runner_pid("investment_memory")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not settings.investment_memory_enabled:
        logger.info(
            "investment memory disabled: TRADECRAFT_INVESTMENT_MEMORY_ENABLED=false"
        )
        return
    asyncio.run(run_investment_memory_loop(settings=settings))


if __name__ == "__main__":
    run()
