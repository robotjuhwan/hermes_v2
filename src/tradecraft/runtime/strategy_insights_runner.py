from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
    codex_native_thread_config_kwargs,
)
from tradecraft.services.llm_model_policy import llm_model_config_kwargs
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
    StrategyInsightCollector,
)

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


def _build_strategy_intelligence(settings: AppSettings) -> StrategyIntelligenceEngine:
    stack = build_report_intelligence_stack(settings)
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=settings.codex_runtime_mode,
            sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
            timeout_ms=settings.codex_runtime_timeout_ms,
            **llm_model_config_kwargs(settings, component="strategy_intelligence"),
            usage_enabled=settings.llm_usage_enabled,
            usage_db_path=settings.llm_usage_db_path,
            usage_component="strategy_intelligence",
            **codex_native_thread_config_kwargs(settings),
        )
    )
    return StrategyIntelligenceEngine(
        repository=stack.repository,
        rag_store=stack.rag_store,
        codex_runtime=bridge,
        config=StrategyIntelligenceConfig(
            insight_db_path=settings.strategy_insight_db_path,
            model_timeout_ms=settings.codex_runtime_timeout_ms,
            migrate_legacy_jsonl=settings.strategy_insight_migrate_legacy_jsonl,
            legacy_jsonl_sidecar_max_lines=settings.strategy_insight_sidecar_max_lines,
        ),
    )


def _build_collector(settings: AppSettings) -> StrategyInsightCollector:
    return StrategyInsightCollector(
        engine=_build_strategy_intelligence(settings),
        sources=settings.strategy_insight_source_list,
        timeout_sec=settings.strategy_insight_request_timeout_sec,
    )


def _strategy_insight_sleep_seconds(
    result: dict[str, Any],
    *,
    interval: int,
    error_backoff_sec: int,
) -> int:
    base = max(int(interval), 30)
    backoff = max(int(error_backoff_sec), base)
    if str(result.get("status") or "") != "error":
        return base
    errors = list(result.get("errors") or [])
    sources = list(result.get("sources") or [])
    haystack = " ".join(
        [
            str(row.get("source_id") or "")
            + " "
            + str(row.get("detail") or row.get("error") or row.get("status") or "")
            for row in errors + sources
            if isinstance(row, dict)
        ]
    ).lower()
    if "after_close_330" in haystack and ("404" in haystack or "not found" in haystack):
        return backoff
    return base


async def run_strategy_insight_loop(
    *,
    settings: AppSettings | None = None,
    collector: StrategyInsightCollector | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    resolved_settings = settings or AppSettings()
    resolved_collector = collector or _build_collector(resolved_settings)
    interval = max(int(resolved_settings.strategy_insight_collect_interval_sec), 30)
    error_backoff_sec = max(
        int(getattr(resolved_settings, "strategy_insight_error_backoff_sec", 3600)),
        interval,
    )
    store = RuntimeStateStore(resolved_settings.strategy_insight_state_path)
    cycle = 0

    while True:
        cycle += 1
        try:
            result = await resolved_collector.collect_once()
            repository = getattr(
                getattr(resolved_collector, "engine", None),
                "insight_repository",
                None,
            )
            prune_method = getattr(repository, "prune_history", None)
            if callable(prune_method):
                result["retention"] = prune_method(
                    retention_days=int(
                        getattr(
                            resolved_settings,
                            "strategy_insight_retention_days",
                            45,
                        )
                    ),
                    signal_row_cap_per_symbol=int(
                        getattr(
                            resolved_settings,
                            "strategy_insight_signal_row_cap_per_symbol",
                            96,
                        )
                    ),
                )
            compact_method = getattr(
                getattr(resolved_collector, "engine", None),
                "compact_legacy_jsonl_sidecars",
                None,
            )
            if callable(compact_method):
                result["sidecar_compaction"] = compact_method(
                    max_lines_per_source=int(
                        getattr(
                            resolved_settings,
                            "strategy_insight_sidecar_max_lines",
                            500,
                        )
                    ),
                )
            status = str(result.get("status") or "ok")
        except Exception as exc:
            logger.exception("strategy insight collection failed")
            status = "error"
            result = {"status": "error", "detail": str(exc)}

        sleep_sec = _strategy_insight_sleep_seconds(
            result,
            interval=interval,
            error_backoff_sec=error_backoff_sec,
        )
        snapshot: dict[str, Any] = {
            "service": "tradecraft-strategy-insights",
            "status": status,
            "cycle": cycle,
            "updated_at": utc_now_iso(),
            "sources_configured": len(resolved_settings.strategy_insight_source_list),
            "interval_sec": interval,
            "error_backoff_sec": error_backoff_sec,
            "next_sleep_sec": sleep_sec,
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
        await sleep(float(sleep_sec))


def run() -> None:
    write_current_runner_pid("strategy_insights")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        asyncio.run(run_strategy_insight_loop())
    except KeyboardInterrupt:
        logger.info("strategy insights runner interrupted; stopping")
    finally:
        clear_current_runner_pid("strategy_insights")


if __name__ == "__main__":
    run()
