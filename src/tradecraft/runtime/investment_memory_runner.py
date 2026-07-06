from __future__ import annotations

import asyncio
import inspect
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.daily_discovery import (
    DailyDiscoveryRepository,
    _compact_discovery_result,
)
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
    codex_native_thread_config_kwargs,
)
from tradecraft.services.llm_usage import KST, LLMUsageRepository
from tradecraft.services.telegram import TelegramBridge, TelegramConfig

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]
DEGRADED_RESULT_STATUSES = {
    "degraded",
    "llm_unavailable",
    "partial",
    "partial_success",
    "warning",
}
ERROR_RESULT_STATUSES = {"error", "failed", "exception"}
STATE_TEXT_LIMIT = 240
STATE_SAMPLE_LIMIT = 3
STATE_DICT_KEY_LIMIT = 18
DEFAULT_PERIOD_MEMORY_SCOPES = ("kis", "binance")
PERIOD_MEMORY_SCOPE_ALIASES = {
    "kr": "kis",
    "krx": "kis",
    "korea": "kis",
    "domestic": "kis",
    "crypto": "binance",
    "bnb": "binance",
    "global": "core",
    "general": "core",
}
PERIOD_MEMORY_SCOPES = {"kis", "binance", "core"}


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _normalize_period_memory_scope(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = PERIOD_MEMORY_SCOPE_ALIASES.get(text, text)
    return text if text in PERIOD_MEMORY_SCOPES else ""


def _period_memory_scopes(settings: Any) -> list[str]:
    raw = _setting(settings, "investment_memory_scopes", DEFAULT_PERIOD_MEMORY_SCOPES)
    values: list[Any]
    if isinstance(raw, str):
        values = [
            part.strip()
            for part in raw.replace(";", ",").split(",")
            if part.strip()
        ]
    elif isinstance(raw, list | tuple | set):
        values = list(raw)
    else:
        values = list(DEFAULT_PERIOD_MEMORY_SCOPES)
    scopes: list[str] = []
    for value in values:
        scope = _normalize_period_memory_scope(value)
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes or list(DEFAULT_PERIOD_MEMORY_SCOPES)


def _service_due_slots(service: Any, *, memory_scopes: list[str]) -> list[str]:
    due_slots = getattr(service, "due_slots")
    try:
        parameters = inspect.signature(due_slots).parameters
    except (TypeError, ValueError):
        return list(due_slots())
    if "memory_scopes" in parameters:
        return list(due_slots(memory_scopes=memory_scopes))
    return list(due_slots())


def _scoped_period_context(context: dict[str, Any], scope: str) -> dict[str, Any]:
    scoped = dict(context)
    scoped["memory_scope"] = scope
    scoped["target_scope"] = scope
    return scoped


def _current_kst_date() -> date:
    return datetime.now(timezone.utc).astimezone(KST).date()


def _daily_discovery_window_open() -> bool:
    local = datetime.now(timezone.utc).astimezone(KST)
    return local.time() >= time(8, 0)


def _is_open_day_for_service(service: Any, trading_day: date) -> bool:
    is_open_day = getattr(service, "_is_open_day", None)
    if callable(is_open_day):
        try:
            return bool(is_open_day(trading_day))
        except Exception as exc:
            logger.warning("investment memory open-day check failed: %s", exc)
    return trading_day.weekday() < 5


def _cycle_status_from_results(
    results: list[dict[str, Any]],
    *,
    default: str = "idle",
) -> str:
    statuses = [
        str(row.get("status") or "").strip().lower()
        for row in results
        if isinstance(row, dict)
    ]
    if any(status in ERROR_RESULT_STATUSES for status in statuses):
        return "error"
    if any(status in DEGRADED_RESULT_STATUSES for status in statuses):
        return "degraded"
    if any(status == "ok" for status in statuses):
        return "ok"
    return default


def _memory_status_for_runner(service: Any) -> dict[str, Any]:
    repository = getattr(service, "repository", None)
    repo_status = getattr(repository, "status", None)
    if callable(repo_status):
        return repo_status()
    service_status = getattr(service, "status", None)
    if callable(service_status):
        value = service_status()
        return value if isinstance(value, dict) else {}
    return {}


def _compact_text_for_state(value: str, *, limit: int = STATE_TEXT_LIMIT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"[omitted {len(text)} chars]"


def _compact_value_for_state(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _compact_text_for_state(value)
    if isinstance(value, list):
        return {
            "count": len(value),
            "items": [
                _compact_value_for_state(item, depth=depth + 1)
                for item in value[:STATE_SAMPLE_LIMIT]
            ],
            "truncated": len(value) > STATE_SAMPLE_LIMIT,
        }
    if isinstance(value, tuple):
        return _compact_value_for_state(list(value), depth=depth)
    if isinstance(value, dict):
        if depth >= 3:
            return {
                "key_count": len(value),
                "keys": [str(key) for key in list(value)[:STATE_DICT_KEY_LIMIT]],
            }
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= STATE_DICT_KEY_LIMIT:
                compact["truncated"] = True
                compact["key_count"] = len(value)
                break
            compact[str(key)] = _compact_value_for_state(item, depth=depth + 1)
        return compact
    return _compact_text_for_state(str(value))


def _compact_memory_status_for_state(status: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {
            "state_compacted": True,
            "status": "unknown",
        }
    compact = {
        str(key): _compact_value_for_state(value)
        for key, value in status.items()
    }
    compact["state_compacted"] = True
    return compact


def _iso_to_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_terminal_block_at(db_path: Any) -> datetime | None:
    path = str(db_path or "").strip()
    if not path:
        return None
    try:
        with sqlite3.connect(path, timeout=2.0) as conn:
            row = conn.execute(
                """
                SELECT MAX(COALESCE(NULLIF(closed_at, ''), NULLIF(updated_at, ''), NULLIF(created_at, '')))
                FROM blocks
                WHERE status IN ('closed', 'error')
                """
            ).fetchone()
    except sqlite3.Error:
        return None
    return _iso_to_utc(row[0] if row else "")


def _reflection_catchup_due(
    settings: AppSettings,
    repo_status: dict[str, Any],
    *,
    grace_sec: int = 900,
) -> bool:
    latest_reflection_at = _iso_to_utc(repo_status.get("latest_reflection_at"))
    latest_terminal_at = max(
        (
            item
            for item in (
                _latest_terminal_block_at(
                    getattr(settings, "kis_block_trader_db_path", "")
                ),
                _latest_terminal_block_at(
                    getattr(settings, "binance_block_trader_db_path", "")
                ),
            )
            if item is not None
        ),
        default=None,
    )
    if latest_terminal_at is None:
        return False
    if (
        datetime.now(timezone.utc) - latest_terminal_at
    ).total_seconds() < max(int(grace_sec), 1):
        return False
    if latest_reflection_at is None:
        return True
    return latest_terminal_at > latest_reflection_at + timedelta(
        seconds=max(int(grace_sec), 1)
    )


def _build_block_trader(settings: AppSettings) -> Any:
    from tradecraft.runtime.kis_block_trader_runner import (
        _build_block_trader as build_trader,
    )

    return build_trader(settings)


def _build_binance_block_trader(settings: AppSettings) -> Any:
    from tradecraft.runtime.binance_block_trader_runner import (
        _build_trader as build_trader,
    )

    return build_trader(settings)


def _build_memory_service(settings: AppSettings) -> InvestmentMemoryService:
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=settings.codex_runtime_mode,
            sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
            timeout_ms=settings.codex_runtime_timeout_ms,
            model=settings.llm_model,
            reasoning_effort=settings.llm_reasoning_effort,
            usage_enabled=settings.llm_usage_enabled,
            usage_db_path=settings.llm_usage_db_path,
            usage_component="investment_memory",
            **codex_native_thread_config_kwargs(settings),
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
            ops_summary_cache_ttl_sec=int(
                getattr(settings, "investment_memory_ops_summary_cache_ttl_sec", 10)
            ),
        ),
        codex_runtime=bridge,
        telegram=telegram,
    )


def _build_daily_discovery_service(settings: AppSettings) -> Any:
    from tradecraft.services.daily_discovery import (
        DailyDiscoveryConfig,
        DailyDiscoveryService,
    )
    from tradecraft.services.intelligence import build_report_intelligence_stack
    from tradecraft.services.symbol_analysis import SymbolAnalysisService
    from tradecraft.services.symbol_fundamentals import (
        SymbolFundamentalsConfig,
        SymbolFundamentalsService,
    )

    trader = _build_block_trader(settings)
    stack = build_report_intelligence_stack(settings)
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=settings.codex_runtime_mode,
            sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
            timeout_ms=settings.codex_runtime_timeout_ms,
            model=settings.llm_model,
            reasoning_effort=settings.llm_reasoning_effort,
            usage_enabled=settings.llm_usage_enabled,
            usage_db_path=settings.llm_usage_db_path,
            usage_component="daily_discovery",
            **codex_native_thread_config_kwargs(settings),
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
    symbol_analysis = SymbolAnalysisService(
        codex_runtime=bridge,
        memory_service=_build_memory_service(settings),
        fundamentals=fundamentals,
        quote_provider=trader.quote_service,
        report_repository=stack.repository,
        rag_store=stack.rag_store,
        block_provider=trader,
        timeout_ms=settings.codex_runtime_timeout_ms,
    )

    return DailyDiscoveryService(
        config=DailyDiscoveryConfig(
            db_path=settings.daily_discovery_db_path,
            enabled=settings.daily_discovery_enabled,
            kospi_count=settings.daily_discovery_kospi_count,
            kosdaq_count=settings.daily_discovery_kosdaq_count,
            exclude_recent_days=settings.daily_discovery_exclude_recent_days,
            candidate_limit_per_market=(
                settings.daily_discovery_candidate_limit_per_market
            ),
            force_collect=True,
        ),
        directory_source=stack.repository,
        symbol_analysis=symbol_analysis,
    )


def _latest_daily_discovery_context(settings: AppSettings, *, limit: int = 10) -> dict[str, Any]:
    run = DailyDiscoveryRepository(settings.daily_discovery_db_path).latest_run()
    if run.get("status") == "missing":
        return {
            "status": "missing",
            "trading_day": "",
            "summary": {},
            "items": [],
            "block_candidates": [],
        }
    max_rows = max(int(limit), 1)
    results = [
        _compact_discovery_result(row)
        for row in list(run.get("results") or [])[:max_rows]
        if isinstance(row, dict)
    ]
    return {
        "status": run.get("status"),
        "trading_day": run.get("trading_day"),
        "summary": run.get("summary") or {},
        "items": results,
        "block_candidates": [
            row
            for row in results
            if (row.get("analysis") or {}).get("stance") == "block_candidate"
        ],
        "updated_at": run.get("updated_at"),
    }


def _compact_llm_usage_for_memory(payload: dict[str, Any]) -> dict[str, Any]:
    total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
    rows = payload.get("by_component") if isinstance(payload.get("by_component"), list) else []
    return {
        "trading_day": payload.get("trading_day"),
        "total": {
            "call_count": total.get("call_count"),
            "total_tokens": total.get("total_tokens"),
            "prompt_tokens": total.get("prompt_tokens"),
            "completion_tokens": total.get("completion_tokens"),
            "estimated_token_count": total.get("estimated_token_count"),
            "error_count": total.get("error_count"),
        },
        "by_component": [
            {
                "component": row.get("component"),
                "call_count": row.get("call_count"),
                "total_tokens": row.get("total_tokens"),
                "prompt_tokens": row.get("prompt_tokens"),
                "completion_tokens": row.get("completion_tokens"),
                "error_count": row.get("error_count"),
            }
            for row in rows[:8]
            if isinstance(row, dict)
        ],
    }


async def _snapshot_for_memory(trader: Any) -> dict[str, Any]:
    snapshot_compact = getattr(trader, "snapshot_compact", None)
    if callable(snapshot_compact):
        return await snapshot_compact()
    return await trader.snapshot()


async def _build_context(
    settings: AppSettings,
    *,
    include_trading: bool = True,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    if include_trading:
        try:
            trader = _build_block_trader(settings)
            block_snapshot = await _snapshot_for_memory(trader)
            context["blocks"] = block_snapshot
            context["account"] = block_snapshot.get("account") or {}
            context["clock"] = (block_snapshot.get("summary") or {}).get("clock") or {}
            context["latest_manager_run"] = block_snapshot.get("latest_manager_run") or {}
        except Exception as exc:
            logger.warning("investment memory block context failed: %s", exc)
            context["blocks"] = {"status": "error", "error_message": str(exc)}
            context["account"] = {}
        try:
            binance_trader = _build_binance_block_trader(settings)
            context["binance_blocks"] = await _snapshot_for_memory(binance_trader)
        except Exception as exc:
            logger.warning("investment memory binance block context failed: %s", exc)
            context["binance_blocks"] = {"status": "error", "error_message": str(exc)}
    else:
        context["blocks"] = {"status": "skipped", "reason": "idle_light_context"}
        context["binance_blocks"] = {
            "status": "skipped",
            "reason": "idle_light_context",
        }
        context["account"] = {}
    try:
        research, research_status = read_active_research_feed(settings)
        context["research"] = (
            research if isinstance(research, dict) else {"status": research_status}
        )
    except Exception as exc:
        logger.warning("investment memory research context failed: %s", exc)
        context["research"] = {"status": "error", "error_message": str(exc)}
    try:
        day = datetime.now(timezone.utc).astimezone(KST).date().isoformat()
        usage = LLMUsageRepository(settings.llm_usage_db_path).daily_summary(day)
        context["llm_usage"] = _compact_llm_usage_for_memory(usage)
    except Exception as exc:
        logger.warning("investment memory llm usage context failed: %s", exc)
        context["llm_usage"] = {"status": "error", "error_message": str(exc)}
    try:
        context["daily_discovery"] = _latest_daily_discovery_context(settings, limit=10)
    except Exception as exc:
        logger.warning("investment memory daily discovery context failed: %s", exc)
        context["daily_discovery"] = {"status": "error", "error_message": str(exc)}
    return context


async def _build_context_for_runner(
    settings: AppSettings,
    *,
    include_trading: bool,
) -> dict[str, Any]:
    try:
        return await _build_context(settings, include_trading=include_trading)
    except TypeError as exc:
        if "include_trading" not in str(exc):
            raise
        return await _build_context(settings)


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
    compaction_interval = max(
        int(_setting(resolved_settings, "investment_memory_compaction_interval_sec", 0)),
        0,
    )
    previous_snapshot = state_store.read_snapshot() or {}
    last_compaction_at: datetime | None = _iso_to_utc(
        previous_snapshot.get("last_compaction_at")
    )
    previous_runtime_compaction = (
        previous_snapshot.get("runtime_compaction")
        if isinstance(previous_snapshot.get("runtime_compaction"), dict)
        else {}
    )
    if (
        last_compaction_at is None
        and str(previous_runtime_compaction.get("status") or "").strip().lower()
        == "ok"
    ):
        last_compaction_at = _iso_to_utc(previous_snapshot.get("updated_at"))
    cycle = 0
    resolved_service.initialize()

    while True:
        cycle += 1
        period_scopes = _period_memory_scopes(resolved_settings)
        due_slots = _service_due_slots(
            resolved_service,
            memory_scopes=period_scopes,
        )
        results: list[dict[str, Any]] = []
        seed_result: dict[str, Any] = {"status": "skipped", "reason": "already_seeded"}
        reflection_result: dict[str, Any] = {"status": "skipped", "reason": "not_checked"}
        status = "idle"
        include_trading_context = False
        reflection_catchup_due = False
        daily_discovery_due = False
        daily_discovery_runner_enabled = bool(
            _setting(resolved_settings, "investment_memory_run_daily_discovery", False)
        )
        daily_discovery_service: Any | None = None
        runtime_compaction: dict[str, Any] = {
            "status": "skipped",
            "reason": "not_due",
        }
        try:
            trading_day = _current_kst_date()
            repo_status = _memory_status_for_runner(resolved_service)
            open_day = _is_open_day_for_service(resolved_service, trading_day)
            reflection_catchup_due = _reflection_catchup_due(
                resolved_settings,
                repo_status,
            )
            if (
                daily_discovery_runner_enabled
                and bool(_setting(resolved_settings, "daily_discovery_enabled", False))
                and open_day
                and _daily_discovery_window_open()
            ):
                daily_discovery_service = _build_daily_discovery_service(
                    resolved_settings
                )
                daily_discovery_due = daily_discovery_service.should_run_for_day(
                    trading_day
                )
            include_trading_context = (
                bool(due_slots)
                or not bool(repo_status.get("seeded"))
                or int(repo_status.get("pending_event_count") or 0) > 0
                or reflection_catchup_due
                or daily_discovery_due
            )
            context: dict[str, Any] = {
                "status": "idle_context_skipped",
                "trading_day": trading_day.isoformat(),
                "reason": "no_due_slots_or_pending_reflections",
            }
            if include_trading_context:
                context = await _build_context_for_runner(
                    resolved_settings,
                    include_trading=include_trading_context,
                )
            if daily_discovery_due and daily_discovery_service is not None:
                discovery_result = await daily_discovery_service.run_once(
                    trading_day=trading_day,
                    force=False,
                )
                context["daily_discovery"] = daily_discovery_service.latest_context(
                    limit=10
                )
                results.append(
                    {
                        "status": discovery_result.get("status"),
                        "slot": "daily_discovery",
                        "analyzed_count": discovery_result.get(
                            "analyzed_count",
                            0,
                        ),
                    }
                )
            if not bool(repo_status.get("seeded")):
                seed_result = resolved_service.seed_current(context=context)
                if seed_result.get("status") == "ok":
                    status = "ok"
                    results.append(
                        {
                            "status": "ok",
                            "slot": "seed",
                            "run_id": seed_result.get("run_id"),
                        }
                    )
            if include_trading_context:
                reflection_result = resolved_service.run_due_reflections(context=context)
            else:
                reflection_result = {
                    "status": "skipped",
                    "reason": "idle_no_due_reflections",
                }
            if reflection_result.get("status") == "ok":
                status = "ok"
                results.append(
                    {
                        "status": "ok",
                        "slot": "block_reflection",
                        "created_count": reflection_result.get("created_count", 0),
                    }
                )
            if due_slots:
                review_slots = [
                    slot
                    for slot in due_slots
                    if slot in {"weekly_review", "monthly_review"}
                ]
                for review_slot in review_slots:
                    period_type = (
                        "monthly" if review_slot == "monthly_review" else "weekly"
                    )
                    for memory_scope in period_scopes:
                        scoped_context = _scoped_period_context(context, memory_scope)
                        review_result = await resolved_service.run_period_review(
                            period_type=period_type,
                            context=scoped_context,
                            force=False,
                        )
                        results.append(
                            {
                                "status": review_result.get("status"),
                                "slot": review_slot,
                                "memory_scope": memory_scope,
                                "period_key": review_result.get("period_key"),
                                "revision_count": review_result.get(
                                    "revision_count",
                                    0,
                                ),
                            }
                        )
                replay_slots = [
                    slot
                    for slot in due_slots
                    if slot in {"weekly_replay"}
                ]
                for replay_slot in replay_slots:
                    for memory_scope in period_scopes:
                        scoped_context = _scoped_period_context(context, memory_scope)
                        replay_result = await resolved_service.run_historical_replay(
                            period_type="weekly",
                            context=scoped_context,
                            force=False,
                        )
                        results.append(
                            {
                                "status": replay_result.get("status"),
                                "slot": replay_slot,
                                "memory_scope": memory_scope,
                                "period_key": replay_result.get("period_key"),
                                "case_count": replay_result.get("case_count", 0),
                                "revision_count": replay_result.get(
                                    "revision_count",
                                    0,
                                ),
                            }
                        )
                ritual_slots = [
                    slot
                    for slot in due_slots
                    if slot not in {"weekly_review", "monthly_review", "weekly_replay"}
                ]
                for slot in ritual_slots:
                    result = await resolved_service.run_ritual(
                        slot=slot,
                        context=context,
                        send_telegram=bool(
                            _setting(
                                resolved_settings,
                                "investment_memory_send_telegram",
                                False,
                            )
                        ),
                    )
                    results.append(result)
                status = "ok"
            now_utc = datetime.now(timezone.utc)
            compaction_due = compaction_interval > 0 and (
                last_compaction_at is None
                or (now_utc - last_compaction_at).total_seconds() >= compaction_interval
            )
            if compaction_due:
                validation_event_retained_rows = int(
                    _setting(
                        resolved_settings,
                        "investment_memory_validation_event_retained_rows_per_venue",
                        720,
                    )
                )
                runtime_compaction = resolved_service.compact_runtime_storage(
                    policy_retired_keep=int(
                        _setting(
                            resolved_settings,
                            "investment_memory_policy_retired_keep",
                            400,
                        )
                    ),
                    validation_event_retained_rows_per_venue=validation_event_retained_rows,
                    memory_run_recent_rows_per_group=int(
                        _setting(
                            resolved_settings,
                            "investment_memory_run_recent_rows_per_group",
                            720,
                        )
                    ),
                    symbol_analysis_recent_rows_per_symbol=int(
                        _setting(
                            resolved_settings,
                            "investment_memory_symbol_analysis_recent_rows_per_symbol",
                            120,
                        )
                    ),
                    vacuum=True,
                )
                last_compaction_at = now_utc
                runtime_compaction["finished_at"] = last_compaction_at.isoformat()
                results.append(
                    {
                        "status": runtime_compaction.get("status", "ok"),
                        "slot": "runtime_compaction",
                        "deleted_policy_rules": (
                            runtime_compaction.get("policy_rules") or {}
                        ).get("deleted_count", 0),
                        "vacuum": bool(runtime_compaction.get("vacuum")),
                    }
                )
        except Exception as exc:
            logger.exception("investment memory cycle failed")
            status = "error"
            results.append({"status": "error", "error_message": str(exc)})
        if status != "error":
            status = _cycle_status_from_results(results, default=status)

        snapshot = {
            "service": "tradecraft-investment-memory",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interval_sec": interval,
            "due_slots": due_slots,
            "results": results,
            "seed_result": seed_result,
            "reflection_result": reflection_result,
            "runtime_compaction": runtime_compaction,
            "last_compaction_at": (
                last_compaction_at.isoformat()
                if last_compaction_at is not None
                else ""
            ),
            "include_trading_context": include_trading_context,
            "reflection_catchup_due": reflection_catchup_due,
            "daily_discovery_runner_enabled": daily_discovery_runner_enabled,
            "daily_discovery_due": daily_discovery_due,
            "memory": _compact_memory_status_for_state(resolved_service.status()),
        }
        state_store.write_snapshot(snapshot)
        logger.info(
            "investment memory cycle=%s status=%s due=%s",
            cycle,
            status,
            ",".join(due_slots) if due_slots else "-",
        )
        if bool(_setting(resolved_settings, "investment_memory_once", False)):
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
    try:
        if not settings.investment_memory_enabled:
            logger.info(
                "investment memory disabled: TRADECRAFT_INVESTMENT_MEMORY_ENABLED=false"
            )
            return
        asyncio.run(run_investment_memory_loop(settings=settings))
    except KeyboardInterrupt:
        logger.info("investment memory runner interrupted; stopping")
    finally:
        clear_current_runner_pid("investment_memory")


if __name__ == "__main__":
    run()
