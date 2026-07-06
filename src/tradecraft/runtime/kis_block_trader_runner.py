from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from tradecraft.config import AppSettings
from tradecraft.runtime.live_evaluator_runner import build_live_authority_payload
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.daily_discovery import DailyDiscoveryConfig, DailyDiscoveryService
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.etf_research import (
    ConfiguredETFResearchProvider,
    ETFResearchRepository,
    collect_etf_research,
    expand_default_etf_universe,
    fetch_naver_etf_universe,
    parse_etf_universe_config,
    stale_etf_symbols,
)
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.kis_block_trader import (
    KISBlockTrader,
    KISBlockTraderConfig,
    run_due_manager,
)
from tradecraft.services.kis_config_policy import (
    parse_etf_universe,
    parse_horizon_targets,
)
from tradecraft.services.kr_equity_pattern_lab import KREquityPatternLabRepository
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_selector import (
    JueWikiSelectionRequest,
    JueWikiSelector,
    resolve_jue_wiki_prompt_mode,
)
from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
    codex_native_thread_config_kwargs,
)
from tradecraft.services.market_judgment import (
    MarketJudgmentConfig,
    MarketJudgmentEngine,
)
from tradecraft.services.market_pulse import MarketPulseConfig, MarketPulseService
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
)
from tradecraft.services.symbol_fundamentals import (
    SymbolFundamentalsConfig,
    SymbolFundamentalsService,
)
from tradecraft.services.symbol_analysis import SymbolAnalysisService
from tradecraft.services.telegram import TelegramBridge, TelegramConfig

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]
NowFn = Callable[[], datetime]
KST = ZoneInfo("Asia/Seoul")
MANAGER_TASK_TIMEOUT_GRACE_SEC = 30.0
MANAGER_TASK_TIMEOUT_FLOOR_SEC = 60.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cycle_log_level(*, status: str, manager_used: bool, action_count: int) -> int:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in {"ok", "skipped"} and not manager_used and action_count <= 0:
        return logging.DEBUG
    return logging.INFO


def _symbols_from_csv(value: Any) -> list[str]:
    out: list[str] = []
    for item in str(value or "").replace(";", ",").split(","):
        symbol = item.strip()
        if len(symbol) == 6 and symbol.isdigit() and symbol not in out:
            out.append(symbol)
    return out


def _jue_wiki_arg_list(kwargs: dict[str, Any], name: str) -> list[str]:
    raw = kwargs.get(name)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = [raw]
    return [str(value).strip() for value in values if str(value).strip()]


def _jue_wiki_prompt_mode(settings: AppSettings) -> str:
    mode = str(getattr(settings, "jue_wiki_prompt_mode", "assist") or "assist").strip().lower()
    return mode if mode in {"observe", "assist", "primary"} else "assist"


def _selector_context_provider(
    service: JueWikiService,
    settings: AppSettings,
) -> Callable[..., dict[str, Any]]:
    def provider(**kwargs: Any) -> dict[str, Any]:
        default_max_chars = int(
            getattr(
                settings,
                "jue_wiki_full_prompt_max_chars",
                getattr(settings, "jue_wiki_context_max_chars", 24000),
            )
        )
        result = JueWikiSelector(service).select(
            JueWikiSelectionRequest(
                target_scope=str(kwargs.get("target_scope") or ""),
                symbols=_jue_wiki_arg_list(kwargs, "symbols"),
                page_types=_jue_wiki_arg_list(kwargs, "page_types"),
                lanes=_jue_wiki_arg_list(kwargs, "lanes"),
                regimes=_jue_wiki_arg_list(kwargs, "regimes"),
                block_ids=_jue_wiki_arg_list(kwargs, "block_ids"),
                horizons=_jue_wiki_arg_list(kwargs, "horizons"),
                max_chars=int(
                    kwargs["max_chars"]
                    if kwargs.get("max_chars") is not None
                    else default_max_chars
                ),
                max_pages=int(getattr(settings, "jue_wiki_selector_max_pages", 24)),
                min_confidence=float(
                    getattr(settings, "jue_wiki_selector_min_confidence", 0.15)
                ),
                exclude_lint_warnings=bool(
                    getattr(settings, "jue_wiki_exclude_lint_warnings", False)
                ),
                effectiveness_weight=float(
                    getattr(settings, "jue_wiki_effectiveness_weight", 0.12)
                ),
                effectiveness_max_adjustment=float(
                    getattr(settings, "jue_wiki_effectiveness_max_adjustment", 8.0)
                ),
            )
        )
        configured_prompt_mode = _jue_wiki_prompt_mode(settings)
        prompt_mode_resolution = resolve_jue_wiki_prompt_mode(
            configured_prompt_mode,
            result.mode_recommendation,
        )
        return {
            "status": result.status,
            "selection_run_id": result.selection_run_id,
            "target_scope": result.target_scope,
            "prompt_mode": prompt_mode_resolution["prompt_mode"],
            "configured_prompt_mode": prompt_mode_resolution[
                "configured_prompt_mode"
            ],
            "mode_recommendation": prompt_mode_resolution["mode_recommendation"],
            "prompt_mode_policy": prompt_mode_resolution["prompt_mode_policy"],
            "trust_profile_effectiveness": result.trust_profile_effectiveness,
            "repair_priority_effectiveness": result.repair_priority_effectiveness,
            "validation_repair_effectiveness": (
                result.validation_repair_effectiveness
            ),
            "wiki_application_coverage": result.wiki_application_coverage,
            "content": result.content,
            "effectiveness_policy": result.effectiveness_policy,
            "repair_priorities": result.repair_priorities,
            "repair_action_batches": result.repair_action_batches,
            "evidence_quality": result.evidence_quality,
            "pages": [
                {
                    "page_id": page.page_id,
                    "rank": page.rank,
                    "score": page.score,
                    "selection_reasons": page.reasons,
                    "selection_penalties": page.penalties,
                    "char_count": page.char_count,
                    "source_refs": page.source_refs,
                    "effectiveness": page.effectiveness,
                    "evidence_quality": page.evidence_quality,
                    "quality_status": page.quality_status,
                    "quality_warnings": page.quality_warnings,
                }
                for page in result.pages
            ],
            "rejected_pages": result.rejected_pages,
            "budget_report": result.budget_report,
        }

    return provider


def _build_jue_wiki_context_provider(
    settings: AppSettings,
) -> Callable[..., dict[str, Any]] | None:
    if not bool(getattr(settings, "jue_wiki_enabled", True)):
        return None
    service = JueWikiService(
        config=JueWikiConfig(
            root_path=settings.jue_wiki_root_path,
            db_path=settings.jue_wiki_db_path,
            enabled=bool(getattr(settings, "jue_wiki_enabled", True)),
            context_max_chars=settings.jue_wiki_context_max_chars,
            page_max_chars=settings.jue_wiki_page_max_chars,
            context_page_limit=settings.jue_wiki_context_page_limit,
            kis_blocks_db_path=settings.kis_block_trader_db_path,
            binance_blocks_db_path=settings.binance_block_trader_db_path,
            investment_memory_db_path=settings.investment_memory_db_path,
            daily_discovery_db_path=settings.daily_discovery_db_path,
            trading_validation_db_path=settings.trading_validation_db_path,
            naver_reports_db_path=settings.naver_reports_db_path,
            crypto_market_research_db_path=settings.crypto_market_research_db_path,
        )
    )
    return _selector_context_provider(service, settings)


def _merge_etf_items(
    *groups: list[Any],
    limit: int = 200,
) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            symbol = str(getattr(item, "symbol", "") or "").strip()
            if len(symbol) != 6 or not symbol.isdigit() or symbol in seen:
                continue
            seen.add(symbol)
            out.append(item)
            if len(out) >= max(int(limit), 1):
                return out
    return out


def _runtime_etf_universe(settings: AppSettings) -> list[Any]:
    configured = expand_default_etf_universe(
        parse_etf_universe_config(str(getattr(settings, "etf_research_universe", "")))
    )
    if max(int(getattr(settings, "etf_research_max_symbols", 0)), 0) <= len(configured):
        return configured
    try:
        discovered = fetch_naver_etf_universe(limit=200, timeout_sec=8.0)
    except Exception as exc:
        logger.warning("Naver ETF universe discovery failed: %s", exc)
        discovered = []
    return _merge_etf_items(configured, discovered, limit=200)


def _build_block_trader(settings: AppSettings) -> KISBlockTrader:
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
            usage_component="kis_block_manager",
            **codex_native_thread_config_kwargs(settings),
        )
    )
    discovery_bridge = CodexNativeRuntime(
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
    kis = KISAdapter(
        KISConfig(
            app_key=settings.kis_primary_app_key,
            app_secret=settings.kis_primary_app_secret,
            account_no=settings.kis_primary_account_no,
            product_code=settings.kis_primary_product_code,
            base_url=settings.kis_base_url,
            rate_limit_enabled=settings.kis_rate_limit_enabled,
            rest_rate_limit_per_sec=settings.kis_rest_rate_limit_per_sec,
            account_min_interval_sec=settings.kis_account_min_interval_sec,
            token_min_interval_sec=settings.kis_token_min_interval_sec,
            rate_limit_db_path=settings.kis_rate_limit_db_path,
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
    etf_research_provider = ConfiguredETFResearchProvider(
        repository_factory=lambda: ETFResearchRepository(settings.etf_research_db_path),
        universe_provider=lambda: _runtime_etf_universe(settings),
    )
    strategy_engine = StrategyIntelligenceEngine(
        repository=stack.repository,
        rag_store=stack.rag_store,
        codex_runtime=bridge,
        fundamentals_repository=fundamentals,
        etf_research_repository=etf_research_provider,
        config=StrategyIntelligenceConfig(
            insight_db_path=settings.strategy_insight_db_path,
            model_timeout_ms=settings.codex_runtime_timeout_ms,
        ),
    )
    wiki_context_provider = _build_jue_wiki_context_provider(settings)
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
        codex_runtime=bridge,
        strategy_engine=strategy_engine,
        report_repository=stack.repository,
        fundamentals_repository=fundamentals,
        rag_store=stack.rag_store,
        research_feed_provider=lambda: read_active_research_feed(settings)[0],
        wiki_context_provider=wiki_context_provider,
        watchlist=_symbols_from_csv(settings.valuation_watchlist),
    )
    market_pulse = MarketPulseService(
        config=MarketPulseConfig(
            db_path=settings.market_pulse_db_path,
            enabled=settings.market_pulse_enabled,
            timeout_sec=settings.market_pulse_timeout_sec,
            index_codes=settings.market_pulse_index_codes,
            sector_signal_limit=settings.market_pulse_sector_signal_limit,
            investor_flow_enabled=settings.market_pulse_investor_flow_enabled,
            investor_flow_markets=settings.market_pulse_investor_flow_markets,
            program_trading_enabled=settings.market_pulse_program_trading_enabled,
            program_trading_markets=settings.market_pulse_program_trading_markets,
            fx_enabled=settings.market_pulse_fx_enabled,
        ),
        strategy_signal_provider=strategy_engine,
    )
    telegram = TelegramBridge(
        TelegramConfig(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
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
            ops_summary_cache_ttl_sec=int(
                getattr(settings, "investment_memory_ops_summary_cache_ttl_sec", 10)
            ),
        ),
        codex_runtime=bridge,
        wiki_context_provider=wiki_context_provider,
    )

    trader = KISBlockTrader(
        config=KISBlockTraderConfig(
            db_path=settings.kis_block_trader_db_path,
            state_path=settings.kis_block_trader_state_path,
            enabled=settings.kis_block_trader_enabled,
            execute_orders=settings.kis_block_trader_execute_orders,
            rule_interval_sec=settings.kis_block_trader_rule_interval_sec,
            manager_interval_sec=settings.kis_block_trader_manager_interval_sec,
            aggressive_limit_bps=settings.kis_block_trader_aggressive_limit_bps,
            cost_buy_fee_rate=settings.kis_validation_buy_fee_rate,
            cost_sell_fee_rate=settings.kis_validation_sell_fee_rate,
            cost_sell_tax_rate=settings.kis_validation_sell_tax_rate,
            cost_slippage_bps=settings.kis_validation_slippage_bps,
            cost_spread_bps=settings.kis_validation_spread_bps,
            pending_reconcile_timeout_sec=(
                settings.kis_block_trader_pending_reconcile_timeout_sec
            ),
            failed_exit_retry_cooldown_sec=(
                settings.kis_block_trader_failed_exit_retry_cooldown_sec
            ),
            max_manager_symbols=settings.kis_block_trader_max_manager_symbols,
            prompt_target_chars=settings.kis_block_trader_prompt_target_chars,
            prompt_warn_chars=settings.kis_block_trader_prompt_warn_chars,
            prompt_max_chars=settings.kis_block_trader_prompt_max_chars,
            strategy_revision_id=settings.jue_strategy_revision_id,
            use_naver_fallback=settings.market_judge_use_naver_fallback,
            manager_query=settings.kis_block_trader_manager_query,
            telegram_enabled=settings.investment_memory_send_telegram,
            horizon_targets=parse_horizon_targets(settings.block_horizon_targets),
            etf_universe=parse_etf_universe(settings.kis_block_trader_etf_universe),
        ),
        kis=kis,
        codex_runtime=bridge,
        strategy_engine=strategy_engine,
        etf_research_provider=etf_research_provider,
        market_judgment_provider=market_judgment,
        research_feed_provider=read_research,
        memory_context_provider=investment_memory.context_pack,
        wiki_context_provider=wiki_context_provider,
        market_pulse_provider=market_pulse.context_for_blocks,
        live_authority_provider=lambda: build_live_authority_payload(settings)[
            "venues"
        ]["kis"],
        kr_pattern_lab_provider=lambda: KREquityPatternLabRepository(
            settings.kr_equity_pattern_lab_db_path
        ).context(limit=12),
        symbol_name_resolver=stack.repository.resolve_symbol_names,
        telegram=telegram,
    )
    symbol_analysis = SymbolAnalysisService(
        codex_runtime=bridge,
        memory_service=investment_memory,
        fundamentals=fundamentals,
        quote_provider=market_judgment.quote_service,
        report_repository=stack.repository,
        rag_store=stack.rag_store,
        block_provider=trader,
        timeout_ms=settings.codex_runtime_timeout_ms,
    )
    trader.symbol_analysis_runner = symbol_analysis.run
    daily_discovery_symbol_analysis = SymbolAnalysisService(
        codex_runtime=discovery_bridge,
        memory_service=investment_memory,
        fundamentals=fundamentals,
        quote_provider=market_judgment.quote_service,
        report_repository=stack.repository,
        rag_store=stack.rag_store,
        block_provider=trader,
        timeout_ms=settings.codex_runtime_timeout_ms,
    )
    daily_discovery = DailyDiscoveryService(
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
        symbol_analysis=daily_discovery_symbol_analysis,
    )
    discovery_context_limit = min(
        max(
            int(settings.daily_discovery_kospi_count)
            + int(settings.daily_discovery_kosdaq_count),
            30,
        ),
        120,
    )
    trader.daily_discovery_provider = lambda: daily_discovery.latest_context(
        limit=discovery_context_limit
    )
    trader.daily_discovery_run_once = daily_discovery.run_once
    trader.daily_discovery_should_run = daily_discovery.should_run_for_day
    return trader


def _runner_etf_collect_skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason, "auto": True}


def _runner_fundamentals_collect_skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason, "auto": True}


def _runner_daily_discovery_skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason, "auto": True}


def _trading_day_from_clock(clock: dict[str, Any]) -> date:
    raw = str(clock.get("date") or "").strip()
    if raw:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    return datetime.now(timezone.utc).astimezone(KST).date()


def _parse_manager_run_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _manager_task_timeout_sec(settings: AppSettings) -> float:
    timeout_ms = _to_float(getattr(settings, "codex_runtime_timeout_ms", 600_000))
    runtime_timeout_sec = max(timeout_ms / 1000.0, 0.0)
    return max(
        runtime_timeout_sec + MANAGER_TASK_TIMEOUT_GRACE_SEC,
        MANAGER_TASK_TIMEOUT_FLOOR_SEC,
    )


def _manager_task_timeout_message(timeout_sec: float) -> str:
    seconds = max(int(math.ceil(max(float(timeout_sec), 1.0))), 1)
    return f"manager_task_timeout_after_{seconds}s"


def _running_manager_result(
    *,
    started_at: datetime | None,
    now: datetime,
    settings: AppSettings,
) -> dict[str, Any]:
    elapsed_sec = (
        max((now - started_at).total_seconds(), 0.0)
        if started_at is not None
        else 0.0
    )
    return {
        "status": "running",
        "started_at": started_at.isoformat() if started_at is not None else "",
        "elapsed_sec": round(elapsed_sec, 3),
        "timeout_sec": round(_manager_task_timeout_sec(settings), 3),
    }


def _record_manager_task_timeout_run(
    trader: KISBlockTrader,
    *,
    settings: AppSettings,
    timeout_message: str,
) -> dict[str, Any]:
    repository = getattr(trader, "repository", None)
    save_manager_run = getattr(repository, "save_manager_run", None)
    if not callable(save_manager_run):
        return {"recorded": False, "reason": "repository_unavailable"}
    try:
        run_id = save_manager_run(
            run={
                "prompt": {"runner_timeout": True},
                "response": {},
                "status": "error",
                "mode": "llm",
                "model": str(getattr(settings, "llm_model", "")),
                "error_message": timeout_message,
            },
            actions={"create_blocks": []},
        )
    except Exception as exc:
        logger.exception("failed to record kis manager runner timeout")
        return {"recorded": False, "error_message": str(exc)}
    return {"recorded": True, "run_id": run_id}


def _latest_manager_run_at(trader: KISBlockTrader) -> datetime | None:
    repository = getattr(trader, "repository", None)
    latest_fn = getattr(repository, "latest_manager_run", None)
    if not callable(latest_fn):
        return None
    try:
        try:
            latest = latest_fn(public=False, include_payload=False)
        except TypeError:
            latest = latest_fn(public=False)
    except Exception:
        logger.exception("failed to read latest kis block manager run")
        return None
    if not isinstance(latest, dict):
        return None
    return _parse_manager_run_at(latest.get("run_at"))


def _recover_last_manager_result(trader: KISBlockTrader) -> dict[str, Any] | None:
    repository = getattr(trader, "repository", None)
    latest_fn = getattr(repository, "latest_manager_run", None)
    if not callable(latest_fn):
        return None
    try:
        try:
            latest = latest_fn(public=False, include_payload=False)
        except TypeError:
            latest = latest_fn(public=False)
    except Exception:
        logger.exception("failed to recover latest kis block manager result")
        return None
    if not isinstance(latest, dict):
        return None
    status = str(latest.get("status") or "").strip()
    if not status or status == "missing":
        return None
    result: dict[str, Any] = {"status": status}
    if latest.get("id") is not None:
        result["run_id"] = latest.get("id")
    for key in ("run_at", "mode", "model", "error_message"):
        value = latest.get(key)
        if value not in (None, ""):
            result[key] = value
    return result


def _compact_manager_text(value: Any, *, limit: int = 360) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _manager_action_count_from_result(value: dict[str, Any]) -> int:
    for key in ("action_count", "created_count", "updated_count", "closed_count"):
        raw = value.get(key)
        if isinstance(raw, (int, float)):
            return int(raw)
    actions = value.get("actions")
    if isinstance(actions, dict):
        total = 0
        for rows in actions.values():
            if isinstance(rows, list):
                total += len(rows)
            elif isinstance(rows, dict) and rows:
                total += 1
        return total
    return 0


def _compact_manager_result_for_state(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if value.get("state_compacted") is True:
        return value
    compact: dict[str, Any] = {}
    for key in (
        "status",
        "manager_run_id",
        "run_id",
        "run_at",
        "mode",
        "model",
        "started_at",
        "elapsed_sec",
        "timeout_sec",
    ):
        item = value.get(key)
        if item not in (None, "", [], {}):
            compact[key] = item
    error = _compact_manager_text(value.get("error_message"), limit=360)
    if error:
        compact["error_message"] = error
    summary = _compact_manager_text(
        value.get("summary") or value.get("note") or value.get("status_note"),
        limit=260,
    )
    if summary:
        compact["summary"] = summary
    action_count = _manager_action_count_from_result(value)
    if action_count:
        compact["action_count"] = action_count
    if compact.keys() == value.keys() and len(compact) == len(value):
        return value
    compact["state_compacted"] = True
    return compact


def _manager_due_reason(
    *,
    clock: dict[str, Any],
    trader: KISBlockTrader,
    last_manager_at: datetime | None,
    now: datetime,
) -> str | None:
    session = str(clock.get("session") or "closed")
    if session == "pre_open":
        trading_day = str(clock.get("date") or datetime.now(KST).date())
        last_trading_day = (
            last_manager_at.astimezone(KST).date().isoformat()
            if last_manager_at is not None
            else ""
        )
        if last_manager_at is None or last_trading_day != trading_day:
            return "pre_open"
        return None
    if session in {"regular", "closing_watch"}:
        if session == "closing_watch":
            return None
        if last_manager_at is None:
            return "regular_initial"
        elapsed = (now - last_manager_at).total_seconds()
        if elapsed >= max(int(trader.config.manager_interval_sec), 60):
            return "regular_interval"
    return None


async def _run_daily_discovery_if_due(
    *,
    settings: AppSettings,
    trader: KISBlockTrader,
    clock: dict[str, Any],
) -> dict[str, Any]:
    if not bool(getattr(settings, "daily_discovery_enabled", True)):
        return _runner_daily_discovery_skipped("disabled")
    session = str(clock.get("session") or "").strip().lower()
    if session == "closed":
        return _runner_daily_discovery_skipped("market_closed")
    run_once = getattr(trader, "daily_discovery_run_once", None)
    should_run = getattr(trader, "daily_discovery_should_run", None)
    if not callable(run_once):
        return _runner_daily_discovery_skipped("runner_unavailable")
    trading_day = _trading_day_from_clock(clock)
    if callable(should_run) and not bool(should_run(trading_day)):
        return _runner_daily_discovery_skipped("fresh")
    result = await run_once(trading_day=trading_day, force=False)
    return {
        **(result if isinstance(result, dict) else {"status": "invalid"}),
        "auto": True,
        "trigger": "kis_block_trader_runner",
    }


async def _collect_etf_research_if_due(
    *,
    settings: AppSettings,
    trader: KISBlockTrader,
    last_attempt_at: datetime | None,
) -> tuple[datetime | None, dict[str, Any]]:
    if not bool(getattr(settings, "etf_research_auto_collect", True)):
        return last_attempt_at, _runner_etf_collect_skipped("disabled")
    kis = getattr(trader, "kis", None)
    if kis is None or not hasattr(kis, "fetch_domestic_quote"):
        return last_attempt_at, _runner_etf_collect_skipped("kis_unavailable")

    configured = _runtime_etf_universe(settings)
    if not configured:
        return last_attempt_at, _runner_etf_collect_skipped("empty_universe")

    max_symbols = max(int(getattr(settings, "etf_research_max_symbols", 30)), 0)
    if max_symbols <= 0:
        return last_attempt_at, _runner_etf_collect_skipped("max_symbols_zero")

    repository = ETFResearchRepository(str(getattr(settings, "etf_research_db_path")))
    symbols = stale_etf_symbols(
        repository,
        configured,
        stale_sec=int(getattr(settings, "etf_research_stale_sec", 1800)),
        max_symbols=max_symbols,
        rotation_key=datetime.now(timezone.utc).date().isoformat(),
    )
    if not symbols:
        return last_attempt_at, _runner_etf_collect_skipped("fresh")

    now = datetime.now(timezone.utc)
    min_interval = max(
        int(getattr(settings, "etf_research_auto_min_interval_sec", 300)),
        0,
    )
    if (
        last_attempt_at is not None
        and (now - last_attempt_at).total_seconds() < min_interval
    ):
        return last_attempt_at, {
            **_runner_etf_collect_skipped("throttled"),
            "requested": symbols,
        }

    result = await collect_etf_research(
        repository=repository,
        configured=configured,
        fetch_quote=kis.fetch_domestic_quote,
        symbols=symbols,
        force=False,
        retention_days=int(getattr(settings, "etf_research_retention_days", 7)),
        archive_retention_days=int(
            getattr(settings, "etf_research_archive_retention_days", 14)
        ),
    )
    return now, {**result, "auto": True, "trigger": "kis_block_trader_runner"}


def _active_block_symbols(trader: KISBlockTrader) -> list[str]:
    repository = getattr(trader, "repository", None)
    list_blocks = getattr(repository, "list_blocks", None)
    if not callable(list_blocks):
        return []
    blocks = list_blocks(include_closed=False)
    out: list[str] = []
    for row in blocks if isinstance(blocks, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if len(symbol) == 6 and symbol.isdigit() and symbol not in out:
            out.append(symbol)
    return out


def _account_position_symbols(trader: KISBlockTrader) -> list[str]:
    repository = getattr(trader, "repository", None)
    latest_account = getattr(repository, "latest_reconciliation_account", None)
    if not callable(latest_account):
        return []
    try:
        account = latest_account()
    except Exception:
        logger.exception("failed to read latest kis reconciliation account")
        return []
    if not isinstance(account, dict):
        return []
    out: list[str] = []
    for row in list(account.get("positions") or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if len(symbol) == 6 and symbol.isdigit() and symbol not in out:
            out.append(symbol)
    return out


def _fundamentals_collect_symbols(
    *,
    settings: AppSettings,
    trader: KISBlockTrader,
    max_symbols: int,
) -> list[str]:
    symbols = [
        *_symbols_from_csv(getattr(settings, "valuation_watchlist", "")),
        *_account_position_symbols(trader),
        *_active_block_symbols(trader),
    ]
    return [
        symbol
        for symbol in dict.fromkeys(symbols)
        if len(symbol) == 6 and symbol.isdigit()
    ][: max(int(max_symbols), 1)]


async def _collect_fundamentals_if_due(
    *,
    settings: AppSettings,
    trader: KISBlockTrader,
    fundamentals_service: Any | None,
    last_attempt_at: datetime | None,
) -> tuple[datetime | None, dict[str, Any]]:
    if not bool(getattr(settings, "valuation_auto_collect", True)):
        return last_attempt_at, _runner_fundamentals_collect_skipped("disabled")

    max_symbols = max(int(getattr(settings, "valuation_auto_max_symbols", 8)), 0)
    if max_symbols <= 0:
        return last_attempt_at, _runner_fundamentals_collect_skipped("max_symbols_zero")

    now = datetime.now(timezone.utc)
    min_interval = max(
        int(getattr(settings, "valuation_auto_min_interval_sec", 1800)),
        0,
    )
    if (
        last_attempt_at is not None
        and (now - last_attempt_at).total_seconds() < min_interval
    ):
        return last_attempt_at, _runner_fundamentals_collect_skipped("throttled")

    symbols = _fundamentals_collect_symbols(
        settings=settings,
        trader=trader,
        max_symbols=max_symbols,
    )
    if not symbols:
        return last_attempt_at, _runner_fundamentals_collect_skipped("empty_targets")

    collector = fundamentals_service or SymbolFundamentalsService(
        SymbolFundamentalsConfig(
            db_path=settings.valuation_db_path,
            timeout_sec=settings.valuation_timeout_sec,
            min_refresh_hours=settings.valuation_min_refresh_hours,
            max_symbols_per_collect=max_symbols,
        )
    )
    result = await collector.collect_symbols(symbols, force=False)
    payload = result if isinstance(result, dict) else {"status": "invalid"}
    return now, {
        **payload,
        "auto": True,
        "trigger": "kis_block_trader_runner",
        "target_symbols": symbols,
    }


async def _run_coro_factory_in_worker(
    factory: Callable[[], Awaitable[Any]],
) -> Any:
    return await asyncio.to_thread(lambda: asyncio.run(factory()))


async def run_kis_block_trader_loop(
    *,
    settings: AppSettings | None = None,
    trader: KISBlockTrader | None = None,
    fundamentals_service: Any | None = None,
    sleep: SleepFn = asyncio.sleep,
    now_provider: NowFn = _utc_now,
) -> None:
    resolved_settings = settings or AppSettings()
    store = RuntimeStateStore(resolved_settings.kis_block_trader_state_path)
    resolved_trader = trader or _build_block_trader(resolved_settings)
    interval = max(int(resolved_settings.kis_block_trader_rule_interval_sec), 1)
    manager_error_retry_sec = max(
        int(getattr(resolved_settings, "kis_block_trader_manager_error_retry_sec", 300)),
        60,
    )
    retention_interval = max(
        int(getattr(resolved_settings, "kis_block_trader_retention_interval_sec", 3600)),
        60,
    )
    cycle = 0
    last_manager_at: datetime | None = _latest_manager_run_at(resolved_trader)
    last_retention_at: datetime | None = None
    last_etf_research_collect_attempt_at: datetime | None = None
    last_fundamentals_collect_attempt_at: datetime | None = None
    manager_task: asyncio.Task[dict[str, Any]] | None = None
    manager_started_at: datetime | None = None
    daily_discovery_task: asyncio.Task[dict[str, Any]] | None = None
    daily_discovery_started_at: datetime | None = None
    etf_research_collect_task: (
        asyncio.Task[tuple[datetime | None, dict[str, Any]]] | None
    ) = None
    etf_research_collect_started_at: datetime | None = None
    fundamentals_collect_task: (
        asyncio.Task[tuple[datetime | None, dict[str, Any]]] | None
    ) = None
    fundamentals_collect_started_at: datetime | None = None
    previous_snapshot = store.read_snapshot() or {}
    last_manager_result = (
        dict(previous_snapshot.get("last_manager_result"))
        if isinstance(previous_snapshot.get("last_manager_result"), dict)
        else None
    )
    if last_manager_result is None:
        last_manager_result = _recover_last_manager_result(resolved_trader)
    last_manager_due_reason = str(
        previous_snapshot.get("last_manager_due_reason")
        or previous_snapshot.get("manager_due_reason")
        or ""
    ).strip() or None

    while True:
        cycle += 1
        now_dt = now_provider()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        manager_used = False
        manager_result: dict[str, Any] | None = None
        manager_due_reason: str | None = None
        etf_research_collect_result: dict[str, Any] | None = None
        fundamentals_collect_result: dict[str, Any] | None = None
        daily_discovery_result: dict[str, Any] | None = None
        retention_result: dict[str, Any] | None = None
        try:
            manager_finished_this_cycle = False
            if manager_task is not None and manager_task.done():
                try:
                    manager_result = manager_task.result()
                except asyncio.CancelledError:
                    logger.exception("kis block trader manager task was cancelled")
                    manager_result = {
                        "status": "error",
                        "error_message": "manager task cancelled",
                    }
                except Exception as exc:
                    logger.exception("kis block trader manager task failed")
                    manager_result = {"status": "error", "error_message": str(exc)}
                manager_task = None
                manager_started_at = None
                last_manager_at = now_dt
                manager_used = True
                manager_finished_this_cycle = True
            if (
                manager_task is not None
                and not manager_task.done()
                and manager_started_at is not None
            ):
                elapsed_sec = max((now_dt - manager_started_at).total_seconds(), 0.0)
                manager_timeout_sec = _manager_task_timeout_sec(resolved_settings)
                if elapsed_sec >= manager_timeout_sec:
                    manager_task.cancel()
                    try:
                        await manager_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning(
                            "kis block trader manager task raised during timeout cancel: %s",
                            exc,
                        )
                    timeout_message = _manager_task_timeout_message(
                        manager_timeout_sec
                    )
                    logger.error(
                        "kis block trader manager task timed out: elapsed=%.1fs timeout=%.1fs",
                        elapsed_sec,
                        manager_timeout_sec,
                    )
                    manager_result = {
                        "status": "error",
                        "error_message": timeout_message,
                        "started_at": manager_started_at.isoformat(),
                        "elapsed_sec": round(elapsed_sec, 3),
                        "timeout_sec": round(manager_timeout_sec, 3),
                    }
                    timeout_record = _record_manager_task_timeout_run(
                        resolved_trader,
                        settings=resolved_settings,
                        timeout_message=timeout_message,
                    )
                    if timeout_record.get("recorded"):
                        manager_result["run_id"] = timeout_record.get("run_id")
                    elif timeout_record.get("error_message"):
                        manager_result["record_error"] = timeout_record.get(
                            "error_message"
                        )
                    manager_task = None
                    manager_started_at = None
                    last_manager_at = now_dt
                    manager_used = True
                    manager_finished_this_cycle = True
            if (
                isinstance(manager_result, dict)
                and str(manager_result.get("status") or "").strip().lower()
                != "running"
            ):
                last_manager_result = _compact_manager_result_for_state(manager_result)
            latest_manager_failed = (
                isinstance(last_manager_result, dict)
                and str(last_manager_result.get("status") or "").strip().lower()
                == "error"
            )
            elapsed_since_manager = (
                (now_dt - last_manager_at).total_seconds()
                if last_manager_at is not None
                else float("inf")
            )
            manager_error_retry_due = (
                latest_manager_failed
                and not manager_finished_this_cycle
                and manager_task is None
                and elapsed_since_manager >= manager_error_retry_sec
                and elapsed_since_manager
                < max(int(resolved_settings.kis_block_trader_manager_interval_sec), 60)
            )
            if daily_discovery_task is not None and daily_discovery_task.done():
                try:
                    daily_discovery_result = daily_discovery_task.result()
                except asyncio.CancelledError:
                    logger.exception("daily discovery task was cancelled")
                    daily_discovery_result = {
                        "status": "error",
                        "error_message": "daily discovery task cancelled",
                        "auto": True,
                    }
                except Exception as exc:
                    logger.exception("daily discovery task failed")
                    daily_discovery_result = {
                        "status": "error",
                        "error_message": str(exc),
                        "auto": True,
                    }
                daily_discovery_task = None
                daily_discovery_started_at = None
            if (
                etf_research_collect_task is not None
                and etf_research_collect_task.done()
            ):
                try:
                    (
                        last_etf_research_collect_attempt_at,
                        etf_research_collect_result,
                    ) = etf_research_collect_task.result()
                except asyncio.CancelledError:
                    logger.exception("etf research auto collect task was cancelled")
                    etf_research_collect_result = {
                        "status": "error",
                        "error_message": "etf research auto collect task cancelled",
                        "auto": True,
                    }
                except Exception as exc:
                    logger.exception("etf research auto collect task failed")
                    etf_research_collect_result = {
                        "status": "error",
                        "error_message": str(exc),
                        "auto": True,
                    }
                etf_research_collect_task = None
                etf_research_collect_started_at = None
            if (
                fundamentals_collect_task is not None
                and fundamentals_collect_task.done()
            ):
                try:
                    (
                        last_fundamentals_collect_attempt_at,
                        fundamentals_collect_result,
                    ) = fundamentals_collect_task.result()
                except asyncio.CancelledError:
                    logger.exception("fundamentals auto collect task was cancelled")
                    fundamentals_collect_result = {
                        "status": "error",
                        "error_message": "fundamentals auto collect task cancelled",
                        "auto": True,
                    }
                except Exception as exc:
                    logger.exception("fundamentals auto collect task failed")
                    fundamentals_collect_result = {
                        "status": "error",
                        "error_message": str(exc),
                        "auto": True,
                    }
                fundamentals_collect_task = None
                fundamentals_collect_started_at = None
            clock = resolved_trader.clock()
            tick_result = await resolved_trader.executor_tick()
            if resolved_settings.kis_block_trader_once:
                try:
                    daily_discovery_result = await _run_daily_discovery_if_due(
                        settings=resolved_settings,
                        trader=resolved_trader,
                        clock=clock,
                    )
                except Exception as exc:
                    logger.exception("daily discovery auto run failed")
                    daily_discovery_result = {
                        "status": "error",
                        "error_message": str(exc),
                        "auto": True,
                    }
                try:
                    (
                        last_etf_research_collect_attempt_at,
                        etf_research_collect_result,
                    ) = await _collect_etf_research_if_due(
                        settings=resolved_settings,
                        trader=resolved_trader,
                        last_attempt_at=last_etf_research_collect_attempt_at,
                    )
                except Exception as exc:
                    logger.exception("etf research auto collect failed")
                    etf_research_collect_result = {
                        "status": "error",
                        "error_message": str(exc),
                        "auto": True,
                    }
            else:
                if daily_discovery_task is None and daily_discovery_result is None:
                    daily_discovery_started_at = now_dt
                    daily_discovery_task = asyncio.create_task(
                        _run_daily_discovery_if_due(
                            settings=resolved_settings,
                            trader=resolved_trader,
                            clock=clock,
                        )
                    )
                    daily_discovery_result = {
                        "status": "running",
                        "started_at": now_dt.isoformat(),
                        "auto": True,
                    }
                elif daily_discovery_task is not None:
                    daily_discovery_result = {
                        "status": "running",
                        "started_at": daily_discovery_started_at.isoformat()
                        if daily_discovery_started_at is not None
                        else "",
                        "auto": True,
                    }
                if (
                    etf_research_collect_task is None
                    and etf_research_collect_result is None
                ):
                    etf_research_collect_started_at = now_dt
                    etf_research_collect_task = asyncio.create_task(
                        _run_coro_factory_in_worker(
                            lambda: _collect_etf_research_if_due(
                                settings=resolved_settings,
                                trader=resolved_trader,
                                last_attempt_at=(
                                    last_etf_research_collect_attempt_at
                                ),
                            )
                        )
                    )
                    etf_research_collect_result = {
                        "status": "running",
                        "started_at": now_dt.isoformat(),
                        "auto": True,
                    }
                elif etf_research_collect_task is not None:
                    etf_research_collect_result = {
                        "status": "running",
                        "started_at": etf_research_collect_started_at.isoformat()
                        if etf_research_collect_started_at is not None
                        else "",
                        "auto": True,
                    }
            if resolved_settings.kis_block_trader_once:
                manager_used, manager_result = await run_due_manager(
                    resolved_trader,
                    last_manager_at=last_manager_at,
                )
                if manager_used:
                    last_manager_at = now_dt
                if (
                    isinstance(manager_result, dict)
                    and str(manager_result.get("status") or "").strip().lower()
                    != "running"
                ):
                    last_manager_result = _compact_manager_result_for_state(
                        manager_result
                    )
            elif manager_task is None and manager_result is None:
                if manager_error_retry_due:
                    manager_due_reason = "retry_after_manager_error"
                else:
                    manager_due_reason = _manager_due_reason(
                        clock=clock,
                        trader=resolved_trader,
                        last_manager_at=last_manager_at,
                        now=now_dt,
                    )
                if manager_due_reason is not None:
                    last_manager_at = now_dt
                    manager_started_at = now_dt
                    last_manager_due_reason = manager_due_reason
                    manager_task = asyncio.create_task(
                        resolved_trader.run_manager_once()
                    )
                    manager_used = True
                    manager_result = _running_manager_result(
                        started_at=manager_started_at,
                        now=now_dt,
                        settings=resolved_settings,
                    )
            elif manager_task is not None:
                manager_due_reason = last_manager_due_reason
                manager_result = _running_manager_result(
                    started_at=manager_started_at,
                    now=now_dt,
                    settings=resolved_settings,
                )
            if resolved_settings.kis_block_trader_once:
                try:
                    (
                        last_fundamentals_collect_attempt_at,
                        fundamentals_collect_result,
                    ) = await _collect_fundamentals_if_due(
                        settings=resolved_settings,
                        trader=resolved_trader,
                        fundamentals_service=fundamentals_service,
                        last_attempt_at=last_fundamentals_collect_attempt_at,
                    )
                except Exception as exc:
                    logger.exception("fundamentals auto collect failed")
                    fundamentals_collect_result = {
                        "status": "error",
                        "error_message": str(exc),
                        "auto": True,
                    }
            else:
                if (
                    fundamentals_collect_task is None
                    and fundamentals_collect_result is None
                ):
                    fundamentals_collect_started_at = now_dt
                    fundamentals_collect_task = asyncio.create_task(
                        _run_coro_factory_in_worker(
                            lambda: _collect_fundamentals_if_due(
                                settings=resolved_settings,
                                trader=resolved_trader,
                                fundamentals_service=fundamentals_service,
                                last_attempt_at=(
                                    last_fundamentals_collect_attempt_at
                                ),
                            )
                        )
                    )
                    fundamentals_collect_result = {
                        "status": "running",
                        "started_at": now_dt.isoformat(),
                        "auto": True,
                    }
                elif fundamentals_collect_task is not None:
                    fundamentals_collect_result = {
                        "status": "running",
                        "started_at": fundamentals_collect_started_at.isoformat()
                        if fundamentals_collect_started_at is not None
                        else "",
                        "auto": True,
                    }
            prune_method = getattr(resolved_trader, "prune_operational_history", None)
            retention_due = (
                last_retention_at is None
                or (now_dt - last_retention_at).total_seconds() >= retention_interval
            )
            if callable(prune_method) and retention_due:
                last_retention_at = now_dt
                try:
                    retention_result = prune_method(
                        quote_retention_days=int(
                            resolved_settings.kis_block_trader_quote_retention_days
                        ),
                        manager_run_retention_days=int(
                            resolved_settings.kis_block_trader_manager_run_retention_days
                        ),
                        reconciliation_retention_days=int(
                            resolved_settings.kis_block_trader_reconciliation_retention_days
                        ),
                        archive_retention_days=int(
                            resolved_settings.kis_block_trader_archive_retention_days
                        ),
                    )
                except Exception as exc:
                    logger.warning("kis block trader retention cleanup failed: %s", exc)
                    retention_result = {
                        "status": "error",
                        "error_message": str(exc),
                    }
            status = str(tick_result.get("status") or "ok")
        except Exception as exc:
            logger.exception("kis block trader cycle failed")
            status = "error"
            tick_result = {"status": "error", "detail": str(exc)}

        snapshot = {
            "service": "tradecraft-kis-block-trader",
            "status": status,
            "cycle": cycle,
            "updated_at": now_dt.isoformat(),
            "interval_sec": interval,
            "manager_interval_sec": int(
                resolved_settings.kis_block_trader_manager_interval_sec
            ),
            "manager_error_retry_sec": manager_error_retry_sec,
            "manager_due_reason": manager_due_reason,
            "last_manager_due_reason": last_manager_due_reason,
            "manager_used": manager_used,
            "manager_result": _compact_manager_result_for_state(manager_result),
            "last_manager_result": _compact_manager_result_for_state(
                last_manager_result
            ),
            "etf_research_collect_result": etf_research_collect_result,
            "fundamentals_collect_result": fundamentals_collect_result,
            "daily_discovery_result": daily_discovery_result,
            "retention_result": retention_result,
            "tick_result": tick_result,
        }
        store.write_snapshot(snapshot)
        action_count = (
            len(list(tick_result.get("actions") or []))
            if isinstance(tick_result, dict)
            else 0
        )
        logger.log(
            _cycle_log_level(
                status=status,
                manager_used=manager_used,
                action_count=action_count,
            ),
            "kis block trader cycle=%s status=%s manager=%s actions=%s",
            cycle,
            status,
            manager_used,
            action_count,
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
    try:
        if not settings.kis_block_trader_enabled:
            logger.info(
                "kis block trader disabled: TRADECRAFT_KIS_BLOCK_TRADER_ENABLED=false"
            )
            return
        if not settings.kis_primary_ready:
            logger.info("kis block trader disabled: KIS primary account not configured")
            return
        asyncio.run(run_kis_block_trader_loop(settings=settings))
    except KeyboardInterrupt:
        logger.info("kis block trader runner interrupted; stopping")
    finally:
        clear_current_runner_pid("kis_block_trader")


if __name__ == "__main__":
    run()
