from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.etf_research import (
    ConfiguredETFResearchProvider,
    ETFResearchRepository,
    expand_default_etf_universe,
    fetch_naver_etf_universe,
    parse_etf_universe_config,
)
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.kis import KISAdapter, KISConfig
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
from tradecraft.services.opportunity_scanner import rank_opportunities
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


def _is_symbol(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 6 and text.isdigit()


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


def _build_market_judge(settings: AppSettings) -> MarketJudgmentEngine:
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
            usage_component="market_judge",
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
    strategy_engine = StrategyIntelligenceEngine(
        repository=stack.repository,
        rag_store=stack.rag_store,
        codex_runtime=bridge,
        fundamentals_repository=fundamentals,
        config=StrategyIntelligenceConfig(
            insight_db_path=settings.strategy_insight_db_path,
            model_timeout_ms=settings.codex_runtime_timeout_ms,
        ),
    )
    etf_research_provider = ConfiguredETFResearchProvider(
        repository_factory=lambda: ETFResearchRepository(settings.etf_research_db_path),
        universe_provider=lambda: _runtime_etf_universe(settings),
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
    wiki_context_provider = _build_jue_wiki_context_provider(settings)
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
    def read_research() -> dict[str, Any] | None:
        return read_active_research_feed(settings)[0]

    def opportunity_provider(
        *,
        limit: int,
        account: dict[str, Any] | None = None,
        strategy_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = strategy_payload if isinstance(strategy_payload, dict) else {}
        strategy_candidates = [
            row for row in list(payload.get("candidates") or []) if isinstance(row, dict)
        ]
        try:
            external = strategy_engine.list_external_signals(limit=300)
            external_signals = [
                row for row in list(external.get("items") or []) if isinstance(row, dict)
            ]
        except Exception:
            external_signals = []
        try:
            symbol_rows = stack.repository.list_symbol_directory(limit=3_000)
        except Exception:
            symbol_rows = []
        try:
            report_rows = stack.repository.search(
                query="",
                category="company_analysis",
                limit=100,
            )
        except Exception:
            report_rows = []
        positions = [
            row
            for row in list((account or {}).get("positions") or [])
            if isinstance(row, dict)
        ]
        fundamentals_rows: list[dict[str, Any]] = []
        for symbol in dict.fromkeys(
            str(row.get("symbol") or "")
            for row in [*positions, *strategy_candidates, *report_rows]
            if isinstance(row, dict)
        ):
            if not _is_symbol(symbol):
                continue
            try:
                latest = fundamentals.latest(symbol)
            except Exception:
                latest = None
            if isinstance(latest, dict):
                fundamentals_rows.append(latest)
            if len(fundamentals_rows) >= max(int(limit) * 2, 30):
                break
        etf_rows: list[dict[str, Any]] = []
        try:
            for row in etf_research_provider.list_universe():
                symbol = str(row.get("symbol") or "")
                if not _is_symbol(symbol):
                    continue
                score = etf_research_provider.latest_score(symbol)
                etf_rows.append({**row, **(score if isinstance(score, dict) else {})})
        except Exception:
            etf_rows = []
        return rank_opportunities(
            symbols=symbol_rows,
            reports=report_rows,
            insights=[*strategy_candidates, *external_signals],
            fundamentals=fundamentals_rows,
            etfs=etf_rows,
            positions=positions,
            limit=max(int(limit), 1),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

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
        codex_runtime=bridge,
        strategy_engine=strategy_engine,
        report_repository=stack.repository,
        fundamentals_repository=fundamentals,
        rag_store=stack.rag_store,
        research_feed_provider=read_research,
        market_pulse_provider=market_pulse.latest,
        memory_context_provider=investment_memory.context_pack,
        wiki_context_provider=wiki_context_provider,
        opportunity_provider=opportunity_provider,
        watchlist=_symbols_from_csv(settings.valuation_watchlist),
    )


def _should_use_llm(
    *,
    last_judged_at: datetime | None,
    interval_sec: int,
    clock: dict[str, Any],
    has_session_run_today: bool = False,
) -> bool:
    session = str(clock.get("session") or "closed")
    if session in {"closed", "closing_watch"}:
        return False
    if session in {"pre_open", "post_close_review"}:
        return not has_session_run_today
    if session == "regular":
        local_time = _clock_local_time(clock)
        if local_time is not None and local_time < time(9, 5):
            return False
    if last_judged_at is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last_judged_at).total_seconds()
    return elapsed >= max(int(interval_sec), 60)


def _clock_local_time(clock: dict[str, Any]) -> time | None:
    raw = str(clock.get("now") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timetz().replace(tzinfo=None)
    except ValueError:
        return None


def _build_schedule_snapshot(
    engine: Any,
    *,
    quote_interval: int,
    judge_interval: int,
    last_judged_at: datetime | None,
) -> dict[str, Any]:
    if hasattr(engine, "schedule"):
        return engine.schedule(last_judged_at=last_judged_at)
    return {
        "quote_interval_sec": quote_interval,
        "judge_interval_sec": judge_interval,
        "latest_llm_run_at": last_judged_at.isoformat() if last_judged_at else "",
    }


def _write_runner_snapshot(
    store: RuntimeStateStore,
    *,
    status: str,
    cycle: int,
    quote_interval: int,
    judge_interval: int,
    use_llm: bool,
    schedule: dict[str, Any],
    result: dict[str, Any],
) -> None:
    store.write_snapshot(
        {
            "service": "tradecraft-market-judge",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interval_sec": quote_interval,
            "judge_interval_sec": judge_interval,
            "llm_used": use_llm,
            "schedule": schedule,
            "result": result,
        }
    )


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
    try:
        last_judged_at = resolved_engine.latest_llm_run_at()
    except AttributeError:
        last_judged_at = None

    while True:
        cycle += 1
        clock = resolved_engine.clock()
        session = str(clock.get("session") or "closed")
        trading_day = str(clock.get("date") or "").strip()
        try:
            has_session_run_today = resolved_engine.has_llm_run_for_session_date(
                session=session,
                trading_day=trading_day,
            )
        except AttributeError:
            has_session_run_today = False
        use_llm = _should_use_llm(
            last_judged_at=last_judged_at,
            interval_sec=judge_interval,
            clock=clock,
            has_session_run_today=has_session_run_today,
        )
        if session == "closed":
            status = "closed"
            result = {
                "status": "closed",
                "mode": "idle",
                "reason": "market_closed",
                "clock": clock,
                "judgments": [],
            }
        else:
            try:
                if use_llm:
                    _write_runner_snapshot(
                        store,
                        status="llm_in_progress",
                        cycle=cycle,
                        quote_interval=quote_interval,
                        judge_interval=judge_interval,
                        use_llm=True,
                        schedule=_build_schedule_snapshot(
                            resolved_engine,
                            quote_interval=quote_interval,
                            judge_interval=judge_interval,
                            last_judged_at=last_judged_at,
                        ),
                        result={
                            "status": "llm_in_progress",
                            "mode": "llm",
                            "clock": clock,
                            "started_at": datetime.now(timezone.utc).isoformat(),
                            "judgments": [],
                        },
                    )
                result = await resolved_engine.run_once(use_llm=use_llm)
                status = str(result.get("status") or "ok")
                if use_llm:
                    last_judged_at = datetime.now(timezone.utc)
                try:
                    prune_method = getattr(resolved_engine, "prune_history", None)
                    if callable(prune_method):
                        prune_method(
                            retention_days=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_quote_retention_days",
                                    3,
                                )
                            ),
                            quote_archive_retention_days=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_quote_archive_retention_days",
                                    7,
                                )
                            ),
                            account_retention_days=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_account_retention_days",
                                    30,
                                )
                            ),
                            judgment_retention_days=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_judgment_retention_days",
                                    30,
                                )
                            ),
                            judgment_archive_retention_days=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_judgment_archive_retention_days",
                                    30,
                                )
                            ),
                            compact_recent_run_count=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_compact_recent_run_count",
                                    48,
                                )
                            ),
                            compact_min_chars=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_compact_min_chars",
                                    20_000,
                                )
                            ),
                            compact_symbol_min_chars=int(
                                getattr(
                                    resolved_settings,
                                    "market_judge_compact_symbol_min_chars",
                                    2_000,
                                )
                            ),
                        )
                except Exception as exc:
                    logger.warning("market judge retention cleanup failed: %s", exc)
            except Exception as exc:
                logger.exception("market judge cycle failed")
                status = "error"
                result = {"status": "error", "detail": str(exc), "clock": clock}

        _write_runner_snapshot(
            store,
            status=status,
            cycle=cycle,
            quote_interval=quote_interval,
            judge_interval=judge_interval,
            use_llm=use_llm,
            schedule=_build_schedule_snapshot(
                resolved_engine,
                quote_interval=quote_interval,
                judge_interval=judge_interval,
                last_judged_at=last_judged_at,
            ),
            result=result,
        )
        logger.info(
            "market judge cycle=%s status=%s llm=%s judgments=%s",
            cycle,
            status,
            use_llm,
            len(list(result.get("judgments") or [])) if isinstance(result, dict) else 0,
        )

        if resolved_settings.market_judge_once:
            return
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
    try:
        asyncio.run(run_market_judge_loop(settings=settings))
    except KeyboardInterrupt:
        logger.info("market judge runner interrupted; stopping")


if __name__ == "__main__":
    run()
