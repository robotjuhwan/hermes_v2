from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tradecraft.backtest.engine import BacktestConfig
from tradecraft.backtest.scenarios import apply_scenario
from tradecraft.config import AppSettings
from tradecraft.services.binance import STABLE_USD_ASSETS, BinanceAdapter, BinanceConfig
from tradecraft.services.bithumb import BithumbAdapter, BithumbConfig
from tradecraft.services.fx import FxRateConfig, FxRateService
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.kis_block_trader import (
    KISBlockTrader,
    KISBlockTraderConfig,
)
from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.kis_llm_trader import KISLLMTrader, KISLLMTraderConfig
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig
from tradecraft.services.intelligence import (
    build_report_intelligence_status,
    build_report_intelligence_stack,
    run_report_collection_cycle,
    sync_report_rag,
)
from tradecraft.services.portfolio_coach import PortfolioCoachStore
from tradecraft.services.market import (
    mock_dashboard,
    recalculate_dashboard_totals,
    replace_venue_assets,
    upsert_venue_assets,
)
from tradecraft.services.market_judgment import (
    MarketJudgmentConfig,
    MarketJudgmentEngine,
)
from tradecraft.services.runtime_bridge import (
    ResearchSnapshotReader,
    RuntimeSnapshotReader,
)
from tradecraft.services.runtime_maintenance import (
    RuntimeStoragePolicy,
    build_runtime_storage_report,
    cleanup_runtime_storage,
)
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
    StrategyInsightCollector,
    classify_strategy_intent,
)
from tradecraft.services.symbol_fundamentals import (
    SymbolFundamentalsConfig,
    SymbolFundamentalsService,
)
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    runner_process_status,
    write_current_runner_pid,
)
from tradecraft.runtime.research_feed import read_active_research_feed
from tradecraft.services.telegram import TelegramBridge, TelegramConfig
from tradecraft.services.telegram_cli import TelegramCLI
from tradecraft.services.upbit import UpbitAdapter, UpbitConfig

settings = AppSettings()


def _settings_symbols_from_csv(value: Any) -> list[str]:
    return [
        symbol
        for symbol in dict.fromkeys(
            item.strip()
            for item in re.split(r"[\s,;]+", str(value or ""))
            if item.strip()
        )
        if re.fullmatch(r"\d{6}", symbol)
    ]


telegram = TelegramBridge(
    TelegramConfig(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
)
upbit = UpbitAdapter(
    UpbitConfig(
        access_key=settings.upbit_access_key,
        secret_key=settings.upbit_secret_key,
        base_url=settings.upbit_base_url,
    )
)
bithumb = BithumbAdapter(
    BithumbConfig(
        access_key=settings.bithumb_access_key,
        secret_key=settings.bithumb_secret_key,
        base_url=settings.bithumb_base_url,
    )
)
binance = BinanceAdapter(
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
kis_primary = KISAdapter(
    KISConfig(
        app_key=settings.kis_primary_app_key,
        app_secret=settings.kis_primary_app_secret,
        account_no=settings.kis_primary_account_no,
        product_code=settings.kis_primary_product_code,
        base_url=settings.kis_base_url,
    )
)
kis_secondary = KISAdapter(
    KISConfig(
        app_key=settings.kis_secondary_app_key,
        app_secret=settings.kis_secondary_app_secret,
        account_no=settings.kis_secondary_account_no,
        product_code=settings.kis_secondary_product_code,
        base_url=settings.kis_base_url,
    )
)
telegram_cli = TelegramCLI(mock_dashboard)
runtime_reader = RuntimeSnapshotReader(
    path=settings.runtime_state_path,
    max_age_sec=settings.runtime_max_age_sec,
)
research_reader = ResearchSnapshotReader(
    path=settings.research_state_path,
    max_age_sec=settings.research_max_age_sec,
)
kis_trader_store = RuntimeStateStore(settings.kis_trader_state_path)
portfolio_coach_store = PortfolioCoachStore(settings.portfolio_coach_db_path)
report_intelligence_stack = build_report_intelligence_stack(settings)
naver_report_repository = report_intelligence_stack.repository
naver_report_crawler = report_intelligence_stack.crawler
rag_store = report_intelligence_stack.rag_store
helper_llm_bridge = LLMBridge(
    LLMBridgeConfig(
        command=settings.llm_bridge_command,
        args=settings.llm_bridge_args,
        url=settings.llm_bridge_url,
        token=settings.llm_bridge_token,
        timeout_ms=settings.llm_bridge_timeout_ms,
        model=settings.llm_model,
    )
)
symbol_fundamentals_service = SymbolFundamentalsService(
    SymbolFundamentalsConfig(
        db_path=settings.valuation_db_path,
        timeout_sec=settings.valuation_timeout_sec,
        min_refresh_hours=settings.valuation_min_refresh_hours,
        max_symbols_per_collect=settings.valuation_max_symbols_per_collect,
    )
)
investment_memory_service = InvestmentMemoryService(
    config=InvestmentMemoryConfig(
        root_path=settings.investment_memory_root_path,
        db_path=settings.investment_memory_db_path,
        strategy_md_path=settings.research_strategy_md_path,
        policy_mode=settings.investment_memory_policy_mode,
        persona_tone=settings.investment_memory_persona_tone,
        telegram_enabled=settings.investment_memory_send_telegram,
        context_max_chars=settings.investment_memory_context_max_chars,
    ),
    llm_bridge=helper_llm_bridge,
    telegram=telegram,
)
strategy_intelligence = StrategyIntelligenceEngine(
    repository=naver_report_repository,
    rag_store=rag_store,
    llm_bridge=helper_llm_bridge,
    fundamentals_repository=symbol_fundamentals_service,
    config=StrategyIntelligenceConfig(
        insight_db_path=settings.strategy_insight_db_path,
        model_timeout_ms=settings.llm_bridge_timeout_ms,
    ),
)
market_judgment_engine = MarketJudgmentEngine(
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
    kis=kis_primary,
    llm_bridge=helper_llm_bridge,
    strategy_engine=strategy_intelligence,
    report_repository=naver_report_repository,
    fundamentals_repository=symbol_fundamentals_service,
    rag_store=rag_store,
    research_feed_provider=lambda: _read_strategy_research_feed(),
    watchlist=_settings_symbols_from_csv(settings.valuation_watchlist),
)
kis_block_trader = KISBlockTrader(
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
    kis=kis_primary,
    llm_bridge=helper_llm_bridge,
    strategy_engine=strategy_intelligence,
    market_judgment_provider=market_judgment_engine,
    research_feed_provider=lambda: _read_strategy_research_feed(),
    memory_context_provider=investment_memory_service.context_pack,
)


def _build_strategy_insight_collector(
    sources: list[dict[str, Any]] | None = None,
) -> StrategyInsightCollector:
    return StrategyInsightCollector(
        engine=strategy_intelligence,
        sources=settings.strategy_insight_source_list if sources is None else sources,
        timeout_sec=settings.strategy_insight_request_timeout_sec,
    )


kis_llm_trader = KISLLMTrader(
    config=KISLLMTraderConfig(
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
    ),
    kis=kis_primary,
    report_repo=naver_report_repository,
    rag_store=rag_store,
)
fx_rates = FxRateService(
    FxRateConfig(
        upbit_base_url=settings.upbit_base_url,
        bithumb_base_url=settings.bithumb_base_url,
        fallback_usdt_krw=settings.binance_usdt_krw,
        fallback_usd_krw=settings.usd_krw,
        cache_ttl_sec=settings.fx_cache_ttl_sec,
    )
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
RESEARCH_QUERY_LOG_PATH = Path(".runtime/research_query.log.jsonl")
telegram_poller_task: asyncio.Task[None] | None = None
telegram_update_offset: int | None = None
bithumb_cached_assets: list[dict[str, Any]] | None = None
kis_primary_cached_assets: list[dict[str, Any]] | None = None
kis_primary_us_cached_assets: list[dict[str, Any]] | None = None
kis_secondary_cached_assets: list[dict[str, Any]] | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram_poller_task
    write_current_runner_pid("control")
    if telegram_poller_task is None and telegram.config.ready:
        await _prime_telegram_offset()
        telegram_poller_task = asyncio.create_task(_telegram_poll_worker())
    try:
        yield
    finally:
        clear_current_runner_pid("control")
        if telegram_poller_task is not None:
            telegram_poller_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telegram_poller_task
            telegram_poller_task = None


app = FastAPI(title="TradeCraft UI", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _safe_positive_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        out = float(value)
        return out if out > 0 else 0.0
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        out = float(text)
    except ValueError:
        return 0.0
    return out if out > 0 else 0.0


def _is_fx_source_degraded(source: str) -> bool:
    normalized = source.strip().lower()
    if not normalized:
        return True
    return normalized.startswith("fallback") or normalized.endswith("_proxy")


def _apply_mock_fx_rates(
    dashboard: dict[str, Any], usdt_krw: float, usd_krw: float
) -> None:
    stable_assets = set(STABLE_USD_ASSETS)
    changed = False
    for venue in dashboard.get("venues", []):
        venue_id = str(venue.get("id") or "")
        for asset in venue.get("assets", []):
            if not isinstance(asset, dict):
                continue
            if str(asset.get("kind") or "") != "cash":
                continue

            code = str(asset.get("asset") or "").upper().strip()
            qty = _safe_positive_float(asset.get("qty"))
            if qty <= 0:
                continue

            target_rate = 0.0
            if code == "USD" and venue_id == "us_stock":
                target_rate = _safe_positive_float(usd_krw)
            elif code in stable_assets:
                target_rate = _safe_positive_float(usdt_krw)
            elif code.endswith("-FUT") and code.removesuffix("-FUT") in stable_assets:
                target_rate = _safe_positive_float(usdt_krw)

            if target_rate <= 0:
                continue

            asset["avg_price"] = target_rate
            asset["mark_price"] = target_rate
            asset["value_krw"] = qty * target_rate
            changed = True

    if changed:
        recalculate_dashboard_totals(dashboard)


async def _build_dashboard_payload(include_telegram: bool = True) -> dict[str, Any]:
    global \
        bithumb_cached_assets, \
        kis_primary_cached_assets, \
        kis_primary_us_cached_assets, \
        kis_secondary_cached_assets
    data = mock_dashboard()
    fx_snapshot: dict[str, Any]
    try:
        fx_snapshot = await fx_rates.get_snapshot()
    except Exception as exc:
        logger.warning("fx rate fetch failed: %s", exc)
        fallback_usdt = _safe_positive_float(settings.binance_usdt_krw) or 1387.0
        fallback_usd = _safe_positive_float(settings.usd_krw) or fallback_usdt
        fx_snapshot = {
            "usdt_krw": fallback_usdt,
            "usd_krw": fallback_usd,
            "usdt_source": "fallback",
            "usd_source": "fallback",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        data["events"].append(
            {"type": "fx", "message": "환율 조회 실패: fallback 환율 사용"}
        )

    usdt_krw = _safe_positive_float(fx_snapshot.get("usdt_krw")) or 1387.0
    usd_krw = _safe_positive_float(fx_snapshot.get("usd_krw")) or usdt_krw
    usdt_source = str(fx_snapshot.get("usdt_source") or "unknown").strip() or "unknown"
    usd_source = str(fx_snapshot.get("usd_source") or "unknown").strip() or "unknown"
    fx_status = (
        "warn"
        if _is_fx_source_degraded(usdt_source) or _is_fx_source_degraded(usd_source)
        else "ok"
    )
    fx_snapshot["usdt_krw"] = usdt_krw
    fx_snapshot["usd_krw"] = usd_krw
    fx_snapshot["usdt_source"] = usdt_source
    fx_snapshot["usd_source"] = usd_source
    fx_snapshot["status"] = fx_status
    data["fx"] = fx_snapshot
    _apply_mock_fx_rates(data, usdt_krw=usdt_krw, usd_krw=usd_krw)
    if fx_status == "warn":
        data["events"].append(
            {"type": "fx", "message": "환율 품질 주의: fallback/proxy 소스 포함"}
        )
    data["events"].append(
        {
            "type": "fx",
            "message": (
                f"환율 반영 USDT/KRW {usdt_krw:,.2f} ({usdt_source}), "
                f"USD/KRW {usd_krw:,.2f} ({usd_source})"
            ),
        }
    )

    if settings.upbit_ready:
        try:
            upbit_assets = await upbit.fetch_balance_assets()
            if replace_venue_assets(data, "upbit", upbit_assets):
                data["events"].append(
                    {"type": "upbit", "message": "업비트 실잔고 연동 완료"}
                )
        except Exception as exc:
            logger.warning("upbit balance fetch failed: %s", exc)
            data["events"].append(
                {"type": "upbit", "message": "업비트 잔고 조회 실패, mock 유지"}
            )
    else:
        data["events"].append(
            {"type": "upbit", "message": "업비트 키 미설정: mock 잔고 사용"}
        )

    if settings.bithumb_ready:
        try:
            bithumb_assets = await bithumb.fetch_balance_assets()
            bithumb_cached_assets = bithumb_assets
            upsert_venue_assets(
                data,
                venue_id="bithumb",
                label="빗썸",
                market="국내 가상자산",
                assets=bithumb_assets,
            )
            data["events"].append(
                {"type": "bithumb", "message": "빗썸 실잔고 연동 완료"}
            )
        except Exception as exc:
            logger.warning("bithumb balance fetch failed: %s", exc)
            if bithumb_cached_assets is not None:
                upsert_venue_assets(
                    data,
                    venue_id="bithumb",
                    label="빗썸",
                    market="국내 가상자산",
                    assets=bithumb_cached_assets,
                )
                data["events"].append(
                    {"type": "bithumb", "message": "빗썸 조회 실패, 최근 실잔고 유지"}
                )
            else:
                data["events"].append({"type": "bithumb", "message": "빗썸 조회 실패"})
    else:
        data["events"].append({"type": "bithumb", "message": "빗썸 키 미설정"})

    binance_spot_synced = False
    if settings.binance_spot_ready:
        try:
            spot_assets = await binance.fetch_spot_assets(usdt_krw_rate=usdt_krw)
            if not replace_venue_assets(data, "binance", spot_assets):
                upsert_venue_assets(
                    data,
                    venue_id="binance",
                    label="바이낸스 현물",
                    market="해외 가상자산 (Spot)",
                    assets=spot_assets,
                )
            binance_spot_synced = True
            data["events"].append(
                {"type": "binance", "message": "바이낸스 Spot 잔고 연동 완료"}
            )
        except Exception as exc:
            logger.warning("binance spot balance fetch failed: %s", exc)
            data["events"].append(
                {"type": "binance", "message": "바이낸스 Spot 조회 실패"}
            )
    else:
        data["events"].append({"type": "binance", "message": "바이낸스 Spot 키 미설정"})

    binance_futures_synced = False
    if settings.binance_futures_ready:
        try:
            futures_assets = await binance.fetch_futures_assets(usdt_krw_rate=usdt_krw)
            upsert_venue_assets(
                data,
                venue_id="binance_futures",
                label="바이낸스 선물",
                market="해외 가상자산 (Futures)",
                assets=futures_assets,
            )
            binance_futures_synced = True
            data["events"].append(
                {"type": "binance", "message": "바이낸스 Futures 잔고 연동 완료"}
            )
        except Exception as exc:
            logger.warning("binance futures balance fetch failed: %s", exc)
            data["events"].append(
                {"type": "binance", "message": "바이낸스 Futures 조회 실패"}
            )
    else:
        data["events"].append(
            {"type": "binance", "message": "바이낸스 Futures 키 미설정"}
        )

    if (not binance_spot_synced and not binance_futures_synced) and (
        not settings.binance_spot_ready and not settings.binance_futures_ready
    ):
        data["events"].append(
            {"type": "binance", "message": "바이낸스 키 미설정: mock 잔고 사용"}
        )

    if settings.kis_primary_ready:
        try:
            primary_assets = await kis_primary.fetch_balance_assets()
            if replace_venue_assets(data, "kr_stock", primary_assets):
                kis_primary_cached_assets = primary_assets
                data["events"].append(
                    {"type": "kis", "message": "KIS 1번 계좌 실잔고 연동 완료"}
                )
        except Exception as exc:
            logger.warning("kis primary balance fetch failed: %s", exc)
            if kis_primary_cached_assets:
                replace_venue_assets(data, "kr_stock", kis_primary_cached_assets)
                data["events"].append(
                    {
                        "type": "kis",
                        "message": "KIS 1번 계좌 조회 실패, 최근 실잔고 유지",
                    }
                )
            else:
                data["events"].append(
                    {"type": "kis", "message": "KIS 1번 계좌 조회 실패, mock 유지"}
                )
        try:
            primary_us_assets = await kis_primary.fetch_us_balance_assets(
                usd_krw_rate=usd_krw
            )
            if replace_venue_assets(data, "us_stock", primary_us_assets):
                kis_primary_us_cached_assets = primary_us_assets
                data["events"].append(
                    {"type": "kis", "message": "KIS 1번 계좌 미장 실잔고 연동 완료"}
                )
        except Exception as exc:
            logger.warning("kis primary us balance fetch failed: %s", exc)
            if kis_primary_us_cached_assets is not None:
                replace_venue_assets(data, "us_stock", kis_primary_us_cached_assets)
                data["events"].append(
                    {
                        "type": "kis",
                        "message": "KIS 1번 계좌 미장 조회 실패, 최근 실잔고 유지",
                    }
                )
            else:
                data["events"].append(
                    {"type": "kis", "message": "KIS 1번 계좌 미장 조회 실패, mock 유지"}
                )
    else:
        data["events"].append(
            {"type": "kis", "message": "KIS 1번 키 미설정: mock 잔고 사용"}
        )

    if settings.kis_secondary_ready:
        try:
            secondary_assets = await kis_secondary.fetch_balance_assets()
            kis_secondary_cached_assets = secondary_assets
            upsert_venue_assets(
                data,
                venue_id="kr_stock_2",
                label="국장(2번)",
                market="KRX",
                assets=secondary_assets,
            )
            data["events"].append(
                {"type": "kis", "message": "KIS 2번 계좌 실잔고 연동 완료"}
            )
        except Exception as exc:
            logger.warning("kis secondary balance fetch failed: %s", exc)
            if kis_secondary_cached_assets:
                upsert_venue_assets(
                    data,
                    venue_id="kr_stock_2",
                    label="국장(2번)",
                    market="KRX",
                    assets=kis_secondary_cached_assets,
                )
                data["events"].append(
                    {
                        "type": "kis",
                        "message": "KIS 2번 계좌 조회 실패, 최근 실잔고 유지",
                    }
                )
            else:
                data["events"].append(
                    {"type": "kis", "message": "KIS 2번 계좌 조회 실패"}
                )
    else:
        data["events"].append({"type": "kis", "message": "KIS 2번 키 미설정"})

    runtime_snapshot, runtime_status = runtime_reader.read_snapshot()
    runtime_sessions = (
        list(runtime_snapshot.get("sessions") or [])
        if isinstance(runtime_snapshot, dict)
        else None
    )
    if runtime_sessions is not None:
        data["sessions"] = runtime_sessions
        data["runtime"] = {
            **dict(runtime_snapshot.get("runtime") or {}),
            "updated_at": runtime_snapshot.get("updated_at"),
            "status": runtime_status,
            "age_sec": runtime_snapshot.get("age_sec"),
            "max_age_sec": runtime_snapshot.get("max_age_sec"),
        }
        runtime_message = (
            "매매 런타임 stale: 마지막 세션 상태 표시"
            if runtime_status == "stale"
            else "매매 런타임 모듈 연결됨"
        )
        data["events"].append({"type": "runtime", "message": runtime_message})
    else:
        if runtime_status == "missing":
            data["events"].append(
                {
                    "type": "runtime",
                    "message": "매매 런타임 모듈 미연결: mock 세션 사용",
                }
            )
        elif runtime_status == "stale":
            data["events"].append(
                {"type": "runtime", "message": "매매 런타임 stale: mock 세션 사용"}
            )
        else:
            data["events"].append(
                {"type": "runtime", "message": "매매 런타임 상태 오류: mock 세션 사용"}
            )

    if not settings.research_enabled:
        data["research"] = {
            "updated_at": None,
            "source": "disabled",
            "query": "general",
            "status": "disabled",
            "count": 0,
            "items": [],
            "stale": False,
        }
        data["events"].append(
            {
                "type": "research",
                "message": "요약리서치 비활성화: 전략 판단에서 제외",
            }
        )
    else:
        research_payload, research_status = research_reader.read_feed(allow_stale=True)
        if research_payload is not None:
            data["research"] = research_payload
            if research_status == "stale":
                data["events"].append(
                    {
                        "type": "research",
                        "message": "리서치 스냅샷 오래됨: 마지막 결과 표시",
                    }
                )
            else:
                data["events"].append(
                    {"type": "research", "message": "최근 리서치 결과 반영됨"}
                )
        elif research_status == "missing":
            data["research"] = {
                "updated_at": None,
                "source": "scheduled",
                "query": "general",
                "status": "missing",
                "count": 0,
                "items": [],
            }
            data["events"].append(
                {"type": "research", "message": "리서치 스냅샷 미연결: no data"}
            )
        else:
            data["research"] = {
                "updated_at": None,
                "source": "scheduled",
                "query": "general",
                "status": research_status,
                "count": 0,
                "items": [],
            }
            data["events"].append(
                {"type": "research", "message": f"리서치 상태 오류: {research_status}"}
            )

    if include_telegram:
        data["telegram"] = telegram.status()
    return data


async def _build_investment_memory_context() -> dict[str, Any]:
    context: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        block_snapshot = await kis_block_trader.snapshot()
        context["blocks"] = block_snapshot
        context["account"] = block_snapshot.get("account") or {}
        context["clock"] = (block_snapshot.get("summary") or {}).get("clock") or {}
    except Exception as exc:
        context["blocks"] = {"status": "error", "error_message": str(exc)}
        context["account"] = {}
    try:
        context["market_judgment"] = market_judgment_engine.latest_judgment()
        context["market_clock"] = market_judgment_engine.clock()
    except Exception as exc:
        context["market_judgment"] = {"status": "error", "error_message": str(exc)}
    try:
        research_feed = _read_strategy_research_feed()
        context["research"] = research_feed if isinstance(research_feed, dict) else {}
    except Exception as exc:
        context["research"] = {"status": "error", "error_message": str(exc)}
    try:
        context["reports_status"] = naver_report_repository.status()
    except Exception as exc:
        context["reports_status"] = {"status": "error", "error_message": str(exc)}
    return context


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _normalize_backtest_session_filter(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    out = {str(item).strip().lower() for item in value if str(item).strip()}
    return out


def _build_backtest_config(payload: dict[str, Any]) -> BacktestConfig:
    config = BacktestConfig(
        cycles=max(_as_int(payload.get("cycles"), settings.backtest_cycles), 1),
        step_sec=max(_as_int(payload.get("step_sec"), settings.backtest_step_sec), 1),
        speed=max(_as_float(payload.get("speed"), settings.backtest_speed), 0.01),
        initial_price=max(
            _as_float(payload.get("initial_price"), settings.backtest_initial_price),
            1.0,
        ),
        volatility_bps=max(
            _as_float(payload.get("volatility_bps"), settings.backtest_volatility_bps),
            0.0,
        ),
        drift_bps=_as_float(payload.get("drift_bps"), settings.backtest_drift_bps),
        fee_rate=max(
            _as_float(payload.get("fee_rate"), settings.backtest_fee_rate), 0.0
        ),
        slippage_bps=max(
            _as_float(payload.get("slippage_bps"), settings.backtest_slippage_bps),
            0.0,
        ),
        seed=_as_int(payload.get("seed"), settings.backtest_seed),
    )
    scenario = str(payload.get("scenario") or "baseline").strip().lower()
    return apply_scenario(config, scenario)


async def _process_telegram_text(text: str, chat_id: str) -> dict[str, Any]:
    if text:
        telegram.last_webhook_message = text
    command, args = telegram_cli.parse(text)
    if not command:
        return {"handled": False, "sent": False}

    if command in {"start", "help"}:
        handled, reply = telegram_cli.execute(text)
    elif command == "watchlist":
        query = telegram_cli.strategy_query_text("strategy", args)
        payload = await strategy_intelligence.build_brief(
            query=query,
            research_feed=_read_strategy_research_feed(),
            use_llm=False,
            limit=8,
        )
        handled, reply = True, telegram_cli.strategy_watchlist_text(payload)
    elif command == "why":
        symbol = str(args[0] if args else "").strip()
        query = f"{symbol} 왜 후보인지 근거와 반론을 설명해줘" if symbol else telegram_cli.strategy_query_text("strategy", [])
        payload = await strategy_intelligence.build_brief(
            query=query,
            research_feed=_read_strategy_research_feed(),
            use_llm=True,
            limit=8,
        )
        handled, reply = True, telegram_cli.strategy_why_text(payload, symbol)
    elif command == "market":
        payload = market_judgment_engine.latest_judgment()
        handled, reply = True, telegram_cli.market_judgment_text(payload)
    elif command == "judge":
        if not settings.kis_primary_ready:
            payload = {
                "status": "kis_primary_not_configured",
                "run": {
                    "mode": "unavailable",
                    "status": "kis_primary_not_configured",
                },
                "judgments": [],
            }
        else:
            payload = await market_judgment_engine.run_once(use_llm=True)
        handled, reply = True, telegram_cli.market_judgment_text(payload)
    elif command == "why-now":
        symbol = str(args[0] if args else "").strip()
        payload = market_judgment_engine.latest_judgment()
        handled, reply = True, telegram_cli.market_why_now_text(payload, symbol)
    elif command == "memory":
        payload = investment_memory_service.status()
        handled, reply = True, telegram_cli.memory_status_text(payload)
    elif command == "mindset":
        today_payload = investment_memory_service.today()
        journal = next(
            (
                row
                for row in list(today_payload.get("journals") or [])
                if str(row.get("slot") or "") == "pre_open"
            ),
            None,
        )
        if journal is None:
            payload = await investment_memory_service.run_ritual(
                slot="pre_open",
                context=await _build_investment_memory_context(),
                send_telegram=False,
            )
            journal = payload.get("journal") if isinstance(payload, dict) else None
        handled, reply = True, telegram_cli.memory_journal_text(journal or {})
    elif command == "reflect":
        payload = await investment_memory_service.run_ritual(
            slot="block_reflection",
            context=await _build_investment_memory_context(),
            send_telegram=False,
            force=True,
        )
        handled, reply = True, telegram_cli.memory_journal_text(payload.get("journal") or {})
    elif command == "journal":
        payload = investment_memory_service.today()
        handled, reply = True, telegram_cli.memory_today_text(payload)
    elif command == "why-block":
        block_id = str(args[0] if args else "").strip()
        payload = investment_memory_service.block_memory(block_id)
        handled, reply = True, telegram_cli.memory_block_text(payload)
    elif command in {"ask", "strategy", "bot"}:
        query = telegram_cli.strategy_query_text(command, args)
        payload = await strategy_intelligence.build_brief(
            query=query,
            research_feed=_read_strategy_research_feed(),
            use_llm=True,
            limit=5,
        )
        handled, reply = True, telegram_cli.strategy_brief_text(payload)
    else:
        dashboard_data = await _build_dashboard_payload(include_telegram=False)
        handled, reply = telegram_cli.execute_with_dashboard(text, dashboard_data)

    if not handled:
        return {"handled": False, "sent": False}

    parse_mode = "HTML" if reply.startswith("<pre>") else None
    target_chat_id = chat_id.strip() if chat_id else ""
    sent = await telegram.send_message(
        reply, parse_mode=parse_mode, chat_id=target_chat_id or None
    )
    return {
        "handled": True,
        "sent": bool(sent.get("ok")),
    }


async def _prime_telegram_offset() -> None:
    global telegram_update_offset
    updates = await telegram.get_updates(offset=None, timeout_sec=0)
    if not updates.get("ok"):
        return
    rows = list(updates.get("result") or [])
    if not rows:
        return

    latest = 0
    for item in rows:
        update_id = int(item.get("update_id") or 0)
        if update_id > latest:
            latest = update_id
    if latest:
        telegram_update_offset = latest + 1


async def _telegram_poll_worker() -> None:
    global telegram_update_offset
    while True:
        updates = await telegram.get_updates(
            offset=telegram_update_offset, timeout_sec=15
        )
        if not updates.get("ok"):
            await asyncio.sleep(2.0)
            continue

        rows = list(updates.get("result") or [])
        if not rows:
            await asyncio.sleep(0.2)
            continue

        for item in rows:
            update_id = int(item.get("update_id") or 0)
            if update_id:
                telegram_update_offset = update_id + 1

            message = item.get("message") or {}
            text = str(message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id") or "").strip()
            if not text:
                continue

            result = await _process_telegram_text(text, chat_id)
            if result.get("handled"):
                command = text.split(maxsplit=1)[0]
                logger.info("telegram command handled by polling: %s", command)


def _runner_status_with_cover(
    status: dict[str, Any],
    *,
    covered_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(status)
    direct_alive = bool(payload.get("alive"))
    payload["direct_alive"] = direct_alive
    payload["effective_alive"] = direct_alive
    if not direct_alive and covered_by and bool(covered_by.get("alive")):
        payload["status"] = "covered"
        payload["effective_alive"] = True
        payload["covered_by"] = str(covered_by.get("key") or "")
        payload["covered_by_label"] = str(covered_by.get("label") or "")
    return payload


async def _build_kis_rebalance_status_payload() -> dict[str, Any]:
    updated_at = datetime.now(timezone.utc).isoformat()
    trader_snapshot = kis_trader_store.read_snapshot() or {}
    if not isinstance(trader_snapshot, dict):
        trader_snapshot = {}

    target_weights_raw = trader_snapshot.get("target_weights")
    target_weights: dict[str, float] = {}
    if isinstance(target_weights_raw, dict):
        for raw_ticker, raw_weight in target_weights_raw.items():
            ticker = str(raw_ticker or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            try:
                weight = float(raw_weight)
            except Exception:
                continue
            if weight > 0:
                target_weights[ticker] = weight

    target_symbols = [
        str(code).strip()
        for code in list(
            trader_snapshot.get("target_symbols")
            or trader_snapshot.get("target_codes")
            or []
        )
        if re.fullmatch(r"\d{6}", str(code).strip())
    ]
    target_symbols = list(dict.fromkeys(target_symbols))

    target_cash_weight = _safe_positive_float(trader_snapshot.get("target_cash_weight"))
    target_cash_weight = max(0.0, min(1.0, target_cash_weight))

    if not target_weights and target_symbols:
        investable_ratio = max(1.0 - target_cash_weight, 0.0)
        equal_weight = investable_ratio / float(len(target_symbols))
        target_weights = {ticker: equal_weight for ticker in target_symbols}

    target_ticker_order = [
        ticker
        for ticker, _ in sorted(
            target_weights.items(), key=lambda item: item[1], reverse=True
        )
    ]
    if not target_ticker_order and target_symbols:
        target_ticker_order = target_symbols

    target_invested_ratio = max(
        0.0,
        min(1.0, sum(float(weight) for weight in target_weights.values())),
    )
    if target_cash_weight > 0:
        target_invested_ratio = max(0.0, min(1.0, 1.0 - target_cash_weight))
    target_total_value_krw = _safe_positive_float(
        trader_snapshot.get("portfolio_total_krw")
    )
    strategy_config: dict[str, Any] = {
        "source": "kis_direct+trader_state",
        "bot_id": "kis_trader",
        "api_connected": settings.kis_primary_ready,
        "show_config": {
            "bot_name": "KIS Direct Trader",
            "state": "direct",
            "runmode": (
                "live"
                if settings.kis_trader_enabled and settings.kis_trader_execute_orders
                else "dry_run"
                if settings.kis_trader_enabled
                else "disabled"
            ),
            "strategy": "PortfolioCoach+ResearchTargets",
            "strategy_version": "",
            "timeframe": "daily/advice",
            "trading_mode": "direct_kis",
            "max_open_trades": int(settings.kis_trader_max_orders_per_cycle),
            "stake_currency": "KRW",
            "stake_amount": str(settings.kis_trader_max_budget_per_order_krw),
            "available_capital": 0.0,
            "force_entry_enable": False,
            "position_adjustment_enable": False,
            "max_entry_position_adjustment": 0,
        },
        "override": {
            "target_weights_updated_at": str(
                trader_snapshot.get("target_weights_updated_at")
                or trader_snapshot.get("target_symbols_updated_at")
                or ""
            ),
            "target_cash_weight": target_cash_weight,
            "portfolio_total_krw": target_total_value_krw,
            "force_entry_enable": False,
            "pair_whitelist_count": len(target_ticker_order),
        },
        "errors": [],
    }

    execution: dict[str, Any] = {
        "open_trade_count": 0,
        "open_pairs": [],
        "open_stake_total_krw": 0.0,
        "total_value_krw": target_total_value_krw,
        "actual_invested_ratio": 0.0,
        "recent_order_count": len(list(trader_snapshot.get("orders") or [])),
        "errors": [],
    }
    balance_value_by_ticker: dict[str, float] = {}
    krw_cash_balance = 0.0

    def _ingest_kis_assets(rows: list[dict[str, Any]]) -> None:
        nonlocal krw_cash_balance
        for row in rows:
            if not isinstance(row, dict):
                continue
            asset = str(row.get("asset") or "").strip()
            value_krw = _safe_positive_float(row.get("value_krw"))
            if str(row.get("kind") or "") == "cash" and asset.upper() == "KRW":
                krw_cash_balance += value_krw
                continue
            if not re.fullmatch(r"\d{6}", asset) or value_krw <= 0:
                continue
            balance_value_by_ticker[asset] = (
                balance_value_by_ticker.get(asset, 0.0) + value_krw
            )

    if settings.kis_primary_ready:
        try:
            _ingest_kis_assets(await kis_primary.fetch_balance_assets())
        except Exception as exc:
            cached_rows = [
                row
                for row in list(kis_primary_cached_assets or [])
                if isinstance(row, dict)
            ]
            if cached_rows:
                _ingest_kis_assets(cached_rows)
                strategy_config["errors"].append(f"kis_balance_cache_used: {exc}")
            else:
                execution["errors"].append(f"kis_balance: {exc}")
                strategy_config["errors"].append(f"kis_balance: {exc}")
    else:
        execution["errors"].append("kis_primary_not_configured")
        strategy_config["errors"].append("kis_primary_not_configured")

    if not balance_value_by_ticker and isinstance(
        trader_snapshot.get("positions"), list
    ):
        for row in list(trader_snapshot.get("positions") or []):
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or row.get("symbol") or "").strip()
            if not re.fullmatch(r"\d{6}", ticker):
                continue
            value_krw = _safe_positive_float(row.get("value_krw"))
            if value_krw <= 0:
                continue
            balance_value_by_ticker[ticker] = (
                balance_value_by_ticker.get(ticker, 0.0) + value_krw
            )

    holdings_total_krw = sum(
        _safe_positive_float(value) for value in balance_value_by_ticker.values()
    )
    total_value_krw = float(execution.get("total_value_krw") or 0.0)
    computed_total_krw = krw_cash_balance + holdings_total_krw
    if computed_total_krw > total_value_krw:
        total_value_krw = computed_total_krw
        execution["total_value_krw"] = total_value_krw

    if total_value_krw > 0:
        execution["actual_invested_ratio"] = max(
            0.0, min(1.0, holdings_total_krw / total_value_krw)
        )

    all_tickers = list(
        dict.fromkeys(target_ticker_order + list(balance_value_by_ticker.keys()))
    )
    symbol_name_map: dict[str, str] = {}
    if all_tickers:
        try:
            symbol_name_map = naver_report_repository.resolve_symbol_names(all_tickers)
        except Exception:
            symbol_name_map = {}

    def _display_symbol_name(ticker: str) -> str:
        cleaned = _clean_helper_text(symbol_name_map.get(ticker) or ticker, limit=40)
        if not cleaned or "리포트 보기" in cleaned:
            return ticker
        return cleaned

    clamped_target_cash_weight = max(0.0, min(1.0, target_cash_weight))

    target_rows = [
        {
            "ticker": ticker,
            "name": _display_symbol_name(ticker),
            "weight": float(target_weights.get(ticker) or 0.0),
        }
        for ticker in target_ticker_order
    ]
    target_rows.append(
        {
            "ticker": "KRW",
            "name": "현금",
            "weight": clamped_target_cash_weight,
        }
    )

    extra_current_tickers = [
        ticker
        for ticker, _ in sorted(
            balance_value_by_ticker.items(), key=lambda item: item[1], reverse=True
        )
        if ticker not in set(target_ticker_order)
    ]
    current_ticker_order = target_ticker_order + extra_current_tickers
    current_rows: list[dict[str, Any]] = []
    for ticker in current_ticker_order:
        current_value = float(balance_value_by_ticker.get(ticker) or 0.0)
        current_weight = (
            max(0.0, min(1.0, current_value / total_value_krw))
            if total_value_krw > 0
            else 0.0
        )
        current_rows.append(
            {
                "ticker": ticker,
                "name": _display_symbol_name(ticker),
                "weight": current_weight,
            }
        )
    current_cash_weight = (
        max(0.0, min(1.0, krw_cash_balance / total_value_krw))
        if total_value_krw > 0
        else max(
            0.0,
            min(1.0, 1.0 - float(execution.get("actual_invested_ratio") or 0.0)),
        )
    )
    current_rows.append(
        {
            "ticker": "KRW",
            "name": "현금",
            "weight": current_cash_weight,
        }
    )

    return {
        "status": "ok",
        "updated_at": updated_at,
        "target": {
            "updated_at": str(
                trader_snapshot.get("target_weights_updated_at")
                or trader_snapshot.get("target_symbols_updated_at")
                or ""
            ),
            "target_cash_weight": target_cash_weight,
            "target_invested_ratio": target_invested_ratio,
            "rows": target_rows,
        },
        "current": {
            "updated_at": updated_at,
            "rows": current_rows,
        },
        "execution": execution,
        "strategy_config": strategy_config,
    }


def _extract_symbol_hint(query: str) -> str:
    raw = str(query or "")

    match = re.search(r"(?<!\d)(\d{6})(?!\d)", raw)
    return match.group(1) if match else ""


def _format_citation(broker: str, published_at: str, page: str) -> str:
    b = str(broker or "-").strip() or "-"
    p = str(published_at or "-").strip() or "-"
    pg = str(page or "?").strip() or "?"
    return f"[{b}, {p}, p.{pg}]"


def _append_research_query_log(payload: dict[str, Any]) -> None:
    try:
        RESEARCH_QUERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RESEARCH_QUERY_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _clean_helper_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(int(limit), 1)]


def _clean_report_title(row: dict[str, Any]) -> str:
    title = _clean_helper_text(row.get("title"), limit=120)
    if title and "리포트 보기" not in title:
        return title
    symbol = str(row.get("symbol") or "").strip()
    category = str(row.get("category") or "report").strip() or "report"
    return f"{symbol or 'Naver'} {category}"


def _safe_helper_limit(value: Any) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = 8
    return max(min(raw, 12), 1)


def _safe_strategy_limit(value: Any) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = 8
    return max(min(raw, 12), 3)


def _default_strategy_query(query: Any = "") -> str:
    text = str(query or "").strip()
    return text or "다음 거래일 관심 후보를 전략적으로 정리해줘"


def _read_strategy_research_feed() -> dict[str, Any] | None:
    feed, _ = read_active_research_feed(settings)
    return feed


def _is_krx_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _symbols_from_csv(value: Any) -> list[str]:
    return [
        symbol
        for symbol in dict.fromkeys(
            item.strip()
            for item in re.split(r"[\s,;]+", str(value or ""))
            if item.strip()
        )
        if _is_krx_symbol(symbol)
    ]


def _strategy_fundamental_targets(limit: int | None = None) -> list[str]:
    max_items = max(int(limit or settings.valuation_max_symbols_per_collect), 1)
    symbols: list[str] = []

    symbols.extend(_symbols_from_csv(settings.valuation_watchlist))

    feed = _read_strategy_research_feed()
    if isinstance(feed, dict):
        for row in list(feed.get("items") or [])[:30]:
            if isinstance(row, dict):
                symbols.extend(str(item or "").strip() for item in list(row.get("picks") or []))

    try:
        strategy_payload = strategy_intelligence.build_candidates(
            query=_default_strategy_query(""),
            research_feed=feed,
            limit=12,
        )
    except Exception:
        strategy_payload = {}
    for row in list(strategy_payload.get("candidates") or []) + list(strategy_payload.get("exclusions") or []):
        if isinstance(row, dict):
            symbols.append(str(row.get("symbol") or "").strip())

    try:
        signal_payload = strategy_intelligence.list_external_signals(limit=300)
    except Exception:
        signal_payload = {}
    for row in list(signal_payload.get("items") or []):
        if isinstance(row, dict):
            symbols.append(str(row.get("symbol") or "").strip())

    try:
        report_rows = naver_report_repository.search(
            query="",
            category="company_analysis",
            limit=max_items,
        )
    except Exception:
        report_rows = []
    for row in report_rows:
        symbols.append(str(row.get("symbol") or "").strip())

    return [
        symbol
        for symbol in dict.fromkeys(symbols)
        if _is_krx_symbol(symbol)
    ][:max_items]


def _helper_query_keywords(query: str) -> list[str]:
    stopwords = {
        "최근",
        "리포트",
        "보고서",
        "긍정",
        "부정",
        "근거",
        "리스크",
        "위험",
        "정리",
        "알려줘",
        "설명",
        "요약",
        "투자",
        "전망",
        "후보",
    }
    keywords: list[str] = []
    for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", str(query or "")):
        if token in stopwords or token.isdigit():
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= 5:
            break
    return keywords


def _helper_report_sort_key(
    row: dict[str, Any],
    *,
    query: str,
    symbol: str,
) -> tuple[int, int, int]:
    category = str(row.get("category") or "")
    category_score = {
        "company_analysis": 0,
        "industry_analysis": 6,
        "invest_info": 10,
        "market_info": 14,
        "economy_analysis": 16,
        "bond_analysis": 18,
    }.get(category, 20)
    row_symbol = str(row.get("symbol") or "").strip()
    score = category_score
    if symbol and row_symbol == symbol:
        score -= 3
    elif symbol:
        score += 8
    if symbol and category == "market_info":
        score += 8

    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "company_name", "snippet", "_sort_text", "broker")
    )
    compact_text = re.sub(r"\s+", "", text)
    for keyword in _helper_query_keywords(query):
        if keyword and keyword in text:
            score -= 2
        if symbol and f"{keyword}({symbol})" in compact_text:
            score -= 25

    published_digits = re.sub(r"\D", "", str(row.get("published_at") or ""))
    published_rank = int(published_digits or "0")
    report_id = int(row.get("report_id") or 0)
    return (score, -published_rank, -report_id)


def _helper_report_has_exact_symbol_match(
    row: dict[str, Any],
    *,
    query: str,
    symbol: str,
) -> bool:
    if not symbol:
        return False
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "company_name", "snippet", "_sort_text")
    )
    compact_text = re.sub(r"\s+", "", text)
    return any(
        f"{keyword}({symbol})" in compact_text
        for keyword in _helper_query_keywords(query)
    )


def _collect_helper_report_rows(
    *,
    query: str,
    symbol: str,
    broker: str,
    date_from: str,
    date_to: str,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = naver_report_repository.search(
        query=query,
        symbol=symbol,
        category="",
        limit=limit * 3,
    )

    def apply_filters(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in candidate_rows:
            if broker and str(row.get("broker") or "") != broker:
                continue
            published = str(row.get("published_at") or "")
            if date_from and published and published < date_from:
                continue
            if date_to and published and published > date_to:
                continue
            out.append(row)
        return out

    filtered = apply_filters(rows)
    for keyword in _helper_query_keywords(query):
        if filtered:
            break
        rows = naver_report_repository.search(
            query=keyword,
            symbol=symbol,
            category="",
            limit=100,
        )
        filtered = apply_filters(rows)

    if not filtered and symbol:
        rows = naver_report_repository.search(
            query="",
            symbol=symbol,
            category="",
            limit=limit * 3,
        )
        filtered = apply_filters(rows)

    if not filtered and not symbol:
        for keyword in _helper_query_keywords(query) or [query]:
            rows = naver_report_repository.search(
                query=keyword,
                symbol="",
                category="",
                limit=100,
            )
            filtered = apply_filters(rows)
            if filtered:
                break

    enriched_rows: list[dict[str, Any]] = []
    for row in filtered:
        payload = dict(row)
        report_id = int(payload.get("report_id") or 0)
        if report_id > 0:
            report = naver_report_repository.get_report(report_id)
            if report:
                payload["_sort_text"] = _clean_helper_text(
                    report.get("content"), limit=2200
                )
        enriched_rows.append(payload)
    filtered = enriched_rows

    exact_rows = [
        row
        for row in filtered
        if _helper_report_has_exact_symbol_match(row, query=query, symbol=symbol)
    ]
    if exact_rows:
        filtered = exact_rows

    filtered = sorted(
        filtered,
        key=lambda row: _helper_report_sort_key(row, query=query, symbol=symbol),
    )[:limit]
    facts_rows: list[dict[str, Any]] = []
    citations: list[str] = []
    for row in filtered:
        report_id = int(row.get("report_id") or 0)
        facts = naver_report_repository.get_report_facts(report_id)
        facts_payload = dict(row)
        facts_payload.pop("_sort_text", None)
        facts_payload["title"] = _clean_report_title(facts_payload)
        facts_payload["snippet"] = _clean_helper_text(
            facts_payload.get("snippet"), limit=700
        )
        if facts:
            facts_payload["facts"] = facts
            for quote in list(facts.get("evidence_quotes") or [])[:2]:
                page = str((quote or {}).get("page") or "?")
                citations.append(
                    _format_citation(
                        str(row.get("broker") or ""),
                        str(row.get("published_at") or ""),
                        page,
                    )
                )
        facts_rows.append(facts_payload)

    return facts_rows, citations


def _collect_helper_rag_rows(
    *,
    query: str,
    symbol: str,
    broker: str,
    date_from: str,
    date_to: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not settings.rag_enabled or rag_store is None:
        return []
    rows = rag_store.query(
        query=query,
        symbol=symbol,
        broker=broker,
        date_from=date_from,
        date_to=date_to,
        limit=min(limit, 8),
    )
    for keyword in _helper_query_keywords(query):
        if rows:
            break
        rows = rag_store.query(
            query=keyword,
            symbol=symbol,
            broker=broker,
            date_from=date_from,
            date_to=date_to,
            limit=min(limit, 8),
        )
    if not rows and symbol:
        for fallback_query in [*_helper_query_keywords(query), query]:
            rows = rag_store.query(
                query=fallback_query,
                symbol="",
                broker=broker,
                date_from=date_from,
                date_to=date_to,
                limit=min(limit, 8),
            )
            if rows:
                break
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["title"] = _clean_report_title(payload)
        payload["content"] = _clean_helper_text(payload.get("content"), limit=900)
        if not payload["content"] or "편집상의 공백페이지" in payload["content"]:
            continue
        out.append(payload)
    return out


def _collect_helper_strategy_context(query: str, limit: int) -> dict[str, Any]:
    try:
        payload = strategy_intelligence.build_candidates(
            query=_default_strategy_query(query),
            research_feed=_read_strategy_research_feed(),
            limit=_safe_strategy_limit(limit),
        )
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc)[:240],
            "candidates": [],
            "source_status": [],
        }

    candidates: list[dict[str, Any]] = []
    for row in list(payload.get("candidates") or [])[: max(min(int(limit), 8), 1)]:
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "name": str(row.get("name") or ""),
                "score": int(row.get("score") or 0),
                "confidence": int(row.get("confidence") or 0),
                "confidence_label": str(row.get("confidence_label") or ""),
                "stance": str(row.get("stance") or ""),
                "reasons": list(row.get("reasons") or [])[:5],
                "risks": list(row.get("risks") or [])[:4],
                "checks": list(row.get("checks") or [])[:4],
                "sources": list(row.get("sources") or [])[:6],
                "citations": list(row.get("citations") or [])[:5],
                "score_components": row.get("score_components") or {},
            }
        )

    return {
        "status": str(payload.get("status") or "ok"),
        "query": str(payload.get("query") or query),
        "candidates": candidates,
        "source_status": strategy_intelligence.source_status(),
    }


def _build_helper_answer_fallback(
    *,
    query: str,
    facts_rows: list[dict[str, Any]],
    rag_rows: list[dict[str, Any]],
    strategy_context: dict[str, Any] | None = None,
) -> str:
    summary_lines: list[str] = []
    evidence_lines: list[str] = []
    risk_lines: list[str] = []
    strategy_lines: list[str] = []
    rating_counts: dict[str, int] = {}
    target_values: list[int] = []

    for item in list((strategy_context or {}).get("candidates") or [])[:5]:
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or symbol or "후보").strip()
        score = int(item.get("score") or 0)
        stance = str(item.get("stance") or "watch")
        reasons = [
            _clean_helper_text(value, limit=90)
            for value in list(item.get("reasons") or [])[:2]
            if _clean_helper_text(value, limit=90)
        ]
        risks = [
            _clean_helper_text(value, limit=70)
            for value in list(item.get("risks") or [])[:1]
            if _clean_helper_text(value, limit=70)
        ]
        reason_text = "; ".join(reasons) if reasons else "근거 추가 확인 필요"
        risk_text = f" / 리스크: {risks[0]}" if risks else ""
        strategy_lines.append(
            f"- {name}({symbol}) score {score}, {stance}: {reason_text}{risk_text}"
        )

    for row in facts_rows:
        snippet = _clean_helper_text(row.get("snippet"), limit=180)
        if snippet and len(summary_lines) < 3:
            summary_lines.append(f"- {snippet}")

        facts = row.get("facts")
        if not isinstance(facts, dict):
            continue
        rating = str(facts.get("rating") or "UNKNOWN")
        rating_counts[rating] = rating_counts.get(rating, 0) + 1
        target = facts.get("target_price")
        if isinstance(target, dict):
            value = int(target.get("value") or 0)
            if value > 0:
                target_values.append(value)
        for bullet in list(facts.get("summary_bullets") or [])[:1]:
            quote_text = _clean_helper_text(bullet, limit=160)
            if not quote_text:
                continue
            evidence_quote = list(facts.get("evidence_quotes") or [])
            page = str((evidence_quote[0] if evidence_quote else {}).get("page") or "?")
            evidence_lines.append(
                f"- {quote_text} "
                f"{_format_citation(str(row.get('broker') or ''), str(row.get('published_at') or ''), page)}"
            )
        for risk in list(facts.get("risks") or [])[:2]:
            risk_text = _clean_helper_text(risk, limit=160)
            if risk_text:
                risk_lines.append(f"- {risk_text}")

    for row in rag_rows[:3]:
        content = _clean_helper_text(row.get("content"), limit=160)
        if content:
            evidence_lines.append(
                "- "
                f"{content} "
                f"{_format_citation(str(row.get('broker') or ''), str(row.get('published_at') or ''), str(row.get('page_start') or '?'))}"
            )

    if not summary_lines and strategy_lines:
        summary_lines = [
            "- 전략 후보 엔진의 교차 신호를 우선 요약합니다.",
            "- 후보는 매수 지시가 아니라 다음 거래일 확인용 감시 리스트입니다.",
            "- 시초가 갭, 거래대금, 섹터 수급, 리포트 리스크를 함께 확인해야 합니다.",
        ]

    while len(summary_lines) < 3:
        summary_lines.append("- 리포트에 명시 근거 없음/추가 자료 필요")

    consensus_line = "- 리포트에 명시 근거 없음/추가 자료 필요"
    if rating_counts:
        top_rating = sorted(rating_counts.items(), key=lambda item: item[1], reverse=True)[
            0
        ][0]
        consensus_line = f"- 투자의견 다수: {top_rating}"
        if target_values:
            avg_target = int(round(sum(target_values) / len(target_values)))
            consensus_line = (
                f"- 투자의견 다수: {top_rating}, 목표주가 평균: {avg_target:,} KRW"
            )

    if not evidence_lines:
        evidence_lines = ["- 리포트/RAG에 명시 근거 없음"]
    if not risk_lines:
        risk_lines = ["- 명시 리스크 부족. 원문 리포트와 최신 공시/시황 교차 확인 필요"]

    return "\n".join(
        [
            f"질문: {query}",
            "",
            "요약(3줄)",
            *summary_lines[:3],
            "",
            "전략 후보/감시 리스트",
            *(strategy_lines[:5] or ["- 전략 후보 엔진에 충분한 교차 신호 없음"]),
            "",
            "핵심 근거(인용 포함)",
            *evidence_lines[:6],
            "",
            "리포트 간 차이/컨센서스",
            consensus_line,
            "",
            "리스크/반론 체크리스트",
            *risk_lines[:5],
            "",
            "근거 부족 시 안내",
            "- 수집 리포트와 RAG 문단 기반 정보 제공이며 매매 추천이 아닙니다.",
        ]
    )


def _parse_helper_llm_content(content: str) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"answer_md": text}
    return parsed if isinstance(parsed, dict) else None


def _normalize_helper_answer_contract(answer: str) -> str:
    replacements = {
        "요약": "요약(3줄)",
        "핵심 근거": "핵심 근거(인용 포함)",
        "컨센서스": "리포트 간 차이/컨센서스",
        "리스크/반론": "리스크/반론 체크리스트",
        "안내": "근거 부족 시 안내",
    }
    out: list[str] = []
    for raw_line in str(answer or "").splitlines():
        stripped = raw_line.strip()
        out.append(replacements.get(stripped, raw_line))
    return "\n".join(out).strip()


async def _build_helper_llm_answer(
    *,
    query: str,
    symbol: str,
    facts_rows: list[dict[str, Any]],
    rag_rows: list[dict[str, Any]],
    strategy_context: dict[str, Any] | None,
    fallback_answer: str,
) -> dict[str, Any]:
    strategy_refs = list((strategy_context or {}).get("candidates") or [])[:8]
    if not helper_llm_bridge.ready or not (facts_rows or rag_rows or strategy_refs):
        return {
            "answer": fallback_answer,
            "mode": "deterministic",
            "followups": [],
            "limitations": ["LLM bridge unavailable or evidence missing"],
        }

    report_refs = [
        {
            "report_id": int(row.get("report_id") or 0),
            "title": _clean_report_title(row),
            "broker": str(row.get("broker") or ""),
            "published_at": str(row.get("published_at") or ""),
            "symbol": str(row.get("symbol") or ""),
            "snippet": _clean_helper_text(row.get("snippet"), limit=500),
            "facts": row.get("facts") if isinstance(row.get("facts"), dict) else {},
        }
        for row in facts_rows[:8]
    ]
    rag_refs = [
        {
            "report_id": int(row.get("report_id") or 0),
            "chunk_index": int(row.get("chunk_index") or 0),
            "title": _clean_report_title(row),
            "broker": str(row.get("broker") or ""),
            "published_at": str(row.get("published_at") or ""),
            "symbol": str(row.get("symbol") or ""),
            "page_start": int(row.get("page_start") or 0),
            "content": _clean_helper_text(row.get("content"), limit=650),
        }
        for row in rag_rows[:8]
    ]
    prompt = {
        "question": query,
        "symbol_hint": symbol,
        "report_facts": report_refs,
        "rag_chunks": rag_refs,
        "strategy_candidates": strategy_refs,
        "strategy_source_status": list(
            (strategy_context or {}).get("source_status") or []
        )[:8],
        "fallback_answer": fallback_answer,
        "rules": [
            "Use report_facts, rag_chunks, and strategy_candidates as evidence.",
            "For open-ended candidate questions, provide a ranked watchlist, "
            "entry validation conditions, and invalidation checks instead of only "
            "saying evidence is weak.",
            "Treat Whale Insight and after-close sources as 참고 신호, not standalone proof.",
            "If evidence is weak, say so explicitly.",
            "Do not give direct buy/sell/order instructions.",
            "Use these section headings when applicable: 요약(3줄), 전략 후보/감시 리스트, "
            "핵심 근거(인용 포함), 리포트 간 차이/컨센서스, 리스크/반론 체크리스트, 근거 부족 시 안내.",
            "Write in Korean.",
        ],
        "output_schema": {
            "answer_md": "string",
            "confidence": "low|medium|high",
            "followups": ["string"],
            "limitations": ["string"],
        },
    }
    payload = {
        "model": helper_llm_bridge.resolved_model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return only one JSON object. You are an evidence-bound "
                    "investment research assistant. This is information only, not "
                    "trading advice."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    timeout_ms = min(max(int(settings.llm_bridge_timeout_ms), 1000), 120000)
    result = await helper_llm_bridge.complete(payload, timeout_ms=timeout_ms)
    if not bool(result.get("ok")):
        return {
            "answer": fallback_answer,
            "mode": "deterministic",
            "followups": [],
            "limitations": [str(result.get("error") or "llm_bridge_failed")[:240]],
        }

    parsed = _parse_helper_llm_content(str(result.get("content") or ""))
    if not parsed:
        return {
            "answer": fallback_answer,
            "mode": "deterministic",
            "followups": [],
            "limitations": ["LLM response was empty"],
        }
    answer = str(parsed.get("answer_md") or parsed.get("answer") or "").strip()
    if not answer:
        answer = fallback_answer
    answer = _normalize_helper_answer_contract(answer)
    return {
        "answer": answer,
        "mode": "llm",
        "confidence": str(parsed.get("confidence") or "medium"),
        "followups": [
            str(item).strip()
            for item in list(parsed.get("followups") or [])
            if str(item).strip()
        ][:5],
        "limitations": [
            str(item).strip()
            for item in list(parsed.get("limitations") or [])
            if str(item).strip()
        ][:5],
        "usage": result.get("usage"),
    }


@app.post("/api/helper/ask")
async def helper_ask(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    query = query[:600]
    limit = _safe_helper_limit(payload.get("limit"))
    symbol = str(payload.get("symbol") or "").strip() or _extract_symbol_hint(query)
    broker = str(payload.get("broker") or "").strip()
    date_from = str(payload.get("date_from") or "").strip()
    date_to = str(payload.get("date_to") or "").strip()

    facts_rows, citations = _collect_helper_report_rows(
        query=query,
        symbol=symbol,
        broker=broker,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    rag_rows = _collect_helper_rag_rows(
        query=query,
        symbol=symbol,
        broker=broker,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    for row in rag_rows[:8]:
        citations.append(
            _format_citation(
                str(row.get("broker") or ""),
                str(row.get("published_at") or ""),
                str(row.get("page_start") or "?"),
            )
        )

    strategy_context = _collect_helper_strategy_context(query, limit)
    for row in list(strategy_context.get("candidates") or [])[:5]:
        for citation in list(row.get("citations") or [])[:2]:
            citations.append(str(citation))

    fallback_answer = _build_helper_answer_fallback(
        query=query,
        facts_rows=facts_rows,
        rag_rows=rag_rows,
        strategy_context=strategy_context,
    )
    answer_payload = await _build_helper_llm_answer(
        query=query,
        symbol=symbol,
        facts_rows=facts_rows,
        rag_rows=rag_rows,
        strategy_context=strategy_context,
        fallback_answer=fallback_answer,
    )

    used_report_ids = sorted(
        {
            int(row.get("report_id") or 0)
            for row in [*facts_rows, *rag_rows]
            if int(row.get("report_id") or 0) > 0
        }
    )
    _append_research_query_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "helper_ask",
            "query": query,
            "symbol": symbol,
            "filters": {
                "broker": broker,
                "date_from": date_from,
                "date_to": date_to,
            },
            "top_k": limit,
            "answer_mode": answer_payload.get("mode"),
            "used_report_ids": used_report_ids,
            "citations": citations[:24],
            "strategy_candidate_count": len(strategy_context.get("candidates") or []),
        }
    )

    return {
        "status": "ok",
        "query": query,
        "intent": classify_strategy_intent(query),
        "symbol": symbol,
        "model": helper_llm_bridge.resolved_model,
        "mode": answer_payload.get("mode"),
        "confidence": answer_payload.get("confidence", "medium"),
        "answer": answer_payload.get("answer") or fallback_answer,
        "citations": citations[:24],
        "followups": answer_payload.get("followups") or [],
        "limitations": answer_payload.get("limitations") or [],
        "count": len(facts_rows),
        "rag_count": len(rag_rows),
        "strategy": strategy_context,
        "items": facts_rows,
        "rag_items": rag_rows,
    }


@app.post("/api/strategy/intent")
async def strategy_intent(payload: dict[str, Any]) -> dict[str, Any]:
    query = _default_strategy_query(payload.get("query"))
    return {
        "status": "ok",
        "query": query,
        "intent": classify_strategy_intent(query),
    }


@app.get("/api/strategy/insights")
async def strategy_insights() -> dict[str, Any]:
    return {
        "status": "ok",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sources": strategy_intelligence.source_status(),
        "schema": {
            "symbol": "005930",
            "name": "삼성전자",
            "signal_type": "large_holder_change | after_close_flow",
            "direction": "positive | negative | neutral",
            "strength": 0,
            "summary": "근거 요약",
            "as_of": "ISO-8601 timestamp",
            "tags": ["optional"],
        },
    }


@app.get("/api/strategy/insights/signals")
async def strategy_insight_signals(
    source_id: str = "",
    symbol: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    return strategy_intelligence.list_external_signals(
        source_id=source_id,
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@app.post("/api/strategy/insights/collect")
async def strategy_insights_collect(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    sources_payload = payload.get("sources") if isinstance(payload, dict) else None
    sources = sources_payload if isinstance(sources_payload, list) else None
    try:
        return await _build_strategy_insight_collector(sources).collect_once()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/strategy/insights/{source_id}")
async def strategy_insight_append(
    source_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        return strategy_intelligence.append_external_signals(
            source_id=source_id,
            payload=payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/symbols/fundamentals/collect")
async def symbol_fundamentals_collect(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    raw_symbols = body.get("symbols") if isinstance(body, dict) else None
    if isinstance(raw_symbols, list):
        symbols = [str(item or "").strip() for item in raw_symbols]
        target_source = "explicit"
    elif isinstance(raw_symbols, str) and raw_symbols.strip():
        symbols = _symbols_from_csv(raw_symbols)
        target_source = "explicit"
    else:
        symbols = _strategy_fundamental_targets()
        target_source = "strategy_targets"
    force = bool(body.get("force")) if isinstance(body, dict) else False
    result = await symbol_fundamentals_service.collect_symbols(symbols, force=force)
    result["target_source"] = target_source
    result["target_symbols"] = symbols[: settings.valuation_max_symbols_per_collect]
    return result


@app.get("/api/symbols/{symbol}/fundamentals")
async def symbol_fundamentals(symbol: str) -> dict[str, Any]:
    code = str(symbol or "").strip()
    if not _is_krx_symbol(code):
        raise HTTPException(status_code=400, detail="symbol must be a 6-digit KRX code")
    latest = symbol_fundamentals_service.latest(code)
    if latest is None:
        return {"status": "missing", "symbol": code}
    return latest


@app.get("/api/strategy/candidates")
async def strategy_candidates(
    query: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    return strategy_intelligence.build_candidates(
        query=_default_strategy_query(query),
        research_feed=_read_strategy_research_feed(),
        limit=_safe_strategy_limit(limit),
    )


@app.post("/api/strategy/candidates")
async def strategy_candidates_post(payload: dict[str, Any]) -> dict[str, Any]:
    return strategy_intelligence.build_candidates(
        query=_default_strategy_query(payload.get("query")),
        research_feed=_read_strategy_research_feed(),
        limit=_safe_strategy_limit(payload.get("limit")),
    )


@app.get("/api/strategy/brief")
async def strategy_brief(
    query: str = "",
    limit: int = 8,
    use_llm: bool = False,
) -> dict[str, Any]:
    return await strategy_intelligence.build_brief(
        query=_default_strategy_query(query),
        research_feed=_read_strategy_research_feed(),
        use_llm=bool(use_llm),
        limit=_safe_strategy_limit(limit),
    )


@app.post("/api/strategy/brief")
async def strategy_brief_post(payload: dict[str, Any]) -> dict[str, Any]:
    return await strategy_intelligence.build_brief(
        query=_default_strategy_query(payload.get("query")),
        research_feed=_read_strategy_research_feed(),
        use_llm=bool(payload.get("use_llm")),
        limit=_safe_strategy_limit(payload.get("limit")),
    )


@app.get("/api/research/ask")
async def research_ask(
    query: str,
    symbol: str = "",
    broker: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    result = await helper_ask(
        {
            "query": query,
            "symbol": symbol,
            "broker": broker,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        }
    )
    result["source"] = "research_ask"
    return result


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    runtime_snapshot, runtime_status = runtime_reader.read_snapshot()
    _, research_status = read_active_research_feed(settings)
    control_process = _runner_status_with_cover(
        runner_process_status("control")
    )
    runtime_process = _runner_status_with_cover(
        runner_process_status("runtime")
    )
    intelligence_process = _runner_status_with_cover(
        runner_process_status("intelligence")
    )
    research_process = _runner_status_with_cover(
        runner_process_status("research"),
        covered_by=intelligence_process,
    )
    kis_trader_process = _runner_status_with_cover(
        runner_process_status("kis_trader")
    )
    kis_block_trader_process = _runner_status_with_cover(
        runner_process_status("kis_block_trader")
    )
    investment_memory_process = _runner_status_with_cover(
        runner_process_status("investment_memory")
    )
    naver_reports_process = _runner_status_with_cover(
        runner_process_status("naver_reports"),
        covered_by=intelligence_process,
    )
    strategy_insight_process = _runner_status_with_cover(
        runner_process_status("strategy_insights")
    )
    market_judge_process = _runner_status_with_cover(
        runner_process_status("market_judge")
    )
    runner_processes = {
        "control": control_process,
        "runtime": runtime_process,
        "intelligence": intelligence_process,
        "research": research_process,
        "kis_trader": kis_trader_process,
        "kis_block_trader": kis_block_trader_process,
        "investment_memory": investment_memory_process,
        "naver_reports": naver_reports_process,
        "strategy_insights": strategy_insight_process,
        "market_judge": market_judge_process,
    }
    kis_readiness = _build_kis_trader_readiness(process=kis_trader_process)
    runtime_meta = (
        dict(runtime_snapshot.get("runtime") or {})
        if isinstance(runtime_snapshot, dict)
        else {}
    )
    return {
        "status": "ok",
        "telegram_ready": telegram.config.ready,
        "upbit_ready": settings.upbit_ready,
        "bithumb_ready": settings.bithumb_ready,
        "binance_spot_ready": settings.binance_spot_ready,
        "binance_futures_ready": settings.binance_futures_ready,
        "kis_primary_ready": settings.kis_primary_ready,
        "kis_secondary_ready": settings.kis_secondary_ready,
        "runtime_connected": runtime_status == "ok",
        "runtime_status": runtime_status,
        "runtime_role": runtime_meta.get("role") or "",
        "runtime_execution_mode": runtime_meta.get("execution_mode") or "",
        "runtime_executes_orders": bool(runtime_meta.get("executes_orders")),
        "research_connected": research_status == "ok",
        "research_enabled": settings.research_enabled,
        "research_status": research_status,
        "runtime_runner_alive": bool(runtime_process.get("direct_alive")),
        "intelligence_runner_alive": bool(intelligence_process.get("direct_alive")),
        "research_runner_alive": bool(research_process.get("direct_alive")),
        "research_service_alive": bool(research_process.get("effective_alive")),
        "kis_trader_runner_alive": bool(kis_trader_process.get("direct_alive")),
        "kis_block_trader_runner_alive": bool(
            kis_block_trader_process.get("direct_alive")
        ),
        "investment_memory_runner_alive": bool(
            investment_memory_process.get("direct_alive")
        ),
        "naver_reports_runner_alive": bool(naver_reports_process.get("direct_alive")),
        "naver_reports_service_alive": bool(
            naver_reports_process.get("effective_alive")
        ),
        "strategy_insight_runner_alive": bool(
            strategy_insight_process.get("direct_alive")
        ),
        "market_judge_runner_alive": bool(
            market_judge_process.get("direct_alive")
        ),
        "runner_processes": runner_processes,
        "kis_trader_enabled": settings.kis_trader_enabled,
        "kis_trader_execution_mode": kis_readiness["execution_mode"],
        "kis_trader_ready_to_plan": kis_readiness["ready_to_plan"],
        "kis_trader_ready_to_execute": kis_readiness["ready_to_execute"],
        "kis_trader_blockers": kis_readiness["blockers"],
        "kis_trader_warnings": kis_readiness["warnings"],
        "kis_trader_readiness": kis_readiness,
        "kis_block_trader_enabled": settings.kis_block_trader_enabled,
        "kis_block_trader_execution_mode": (
            "live" if settings.kis_block_trader_execute_orders else "paper"
        ),
        "kis_block_trader": kis_block_trader.status(),
        "investment_memory_enabled": settings.investment_memory_enabled,
        "investment_memory": investment_memory_service.status(),
        "naver_reports_enabled": settings.naver_reports_enabled,
        "naver_reports_llm_facts_enabled": settings.naver_reports_llm_facts_enabled,
        "naver_reports_llm_facts_active": settings.naver_reports_llm_facts_active,
        "llm_bridge_mode": settings.llm_bridge_mode,
        "llm_bridge_ready": settings.llm_bridge_ready,
        "market_judge_enabled": settings.market_judge_enabled,
        "market_judge": market_judgment_engine.status(),
    }


@app.get("/api/dashboard")
async def dashboard() -> dict[str, Any]:
    return await _build_dashboard_payload(include_telegram=True)


@app.get("/api/market/clock")
async def market_clock() -> dict[str, Any]:
    return market_judgment_engine.clock()


@app.get("/api/market/quotes")
async def market_quotes(limit: int = 100) -> dict[str, Any]:
    return market_judgment_engine.latest_quotes(limit=max(min(int(limit), 300), 1))


@app.get("/api/market/account")
async def market_account() -> dict[str, Any]:
    return market_judgment_engine.latest_account()


@app.get("/api/market/judgments/latest")
async def market_judgment_latest() -> dict[str, Any]:
    return market_judgment_engine.latest_judgment()


@app.post("/api/market/judgments/run-once")
async def market_judgment_run_once(use_llm: bool = True) -> dict[str, Any]:
    if not settings.kis_primary_ready:
        raise HTTPException(
            status_code=400,
            detail="kis primary account not configured",
        )
    return await market_judgment_engine.run_once(use_llm=bool(use_llm))


@app.get("/api/memory/status")
async def investment_memory_status() -> dict[str, Any]:
    return investment_memory_service.status()


@app.get("/api/memory/today")
async def investment_memory_today() -> dict[str, Any]:
    return investment_memory_service.today()


@app.get("/api/memory/symbols/{symbol}")
async def investment_memory_symbol(symbol: str) -> dict[str, Any]:
    result = investment_memory_service.symbol_memory(symbol)
    if result.get("status") == "invalid_symbol":
        raise HTTPException(status_code=400, detail="invalid symbol")
    return result


@app.get("/api/memory/blocks/{block_id}")
async def investment_memory_block(block_id: str) -> dict[str, Any]:
    result = investment_memory_service.block_memory(block_id)
    if result.get("status") == "invalid_block_id":
        raise HTTPException(status_code=400, detail="invalid block id")
    return result


@app.post("/api/memory/init")
async def investment_memory_init(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return investment_memory_service.initialize(force=bool((payload or {}).get("force")))


@app.post("/api/memory/rituals/run-once")
async def investment_memory_ritual_run_once(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    slot = str(body.get("slot") or "pre_open")
    return await investment_memory_service.run_ritual(
        slot=slot,
        context=await _build_investment_memory_context(),
        send_telegram=bool(body.get("send_telegram", False)),
        force=bool(body.get("force", True)),
    )


@app.post("/api/memory/update/run-once")
async def investment_memory_update_run_once(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    return await investment_memory_service.run_update(
        context=await _build_investment_memory_context(),
        force=bool(body.get("force", True)),
    )


@app.get("/api/telegram/status")
async def telegram_status() -> dict[str, Any]:
    return telegram.status()


@app.post("/api/telegram/webhook")
async def telegram_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message") or {}
    text = str(message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "").strip()
    result = await _process_telegram_text(text, chat_id)
    return {"ok": True, **result}


def _build_kis_trader_readiness(
    *,
    process: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _, research_status = research_reader.read_feed(allow_stale=True)
    llm_ready = bool(
        settings.llm_bridge_ready or settings.kis_trader_llm_command.strip()
    )
    execution_enabled = bool(settings.kis_trader_execute_orders)
    process_payload = (
        dict(process)
        if process is not None
        else _runner_status_with_cover(runner_process_status("kis_trader"))
    )

    blockers: list[str] = []
    warnings: list[str] = []
    if not settings.kis_trader_enabled:
        blockers.append("kis_trader_disabled")
    if not settings.kis_primary_ready:
        blockers.append("kis_primary_not_configured")
    if research_status != "ok":
        blockers.append(f"research_{research_status}")
    if not execution_enabled:
        warnings.append("dry_run_mode")
    if not llm_ready:
        warnings.append("llm_unavailable_fallback_only")
    if not bool(process_payload.get("direct_alive")):
        warnings.append("runner_not_running")

    ready_to_plan = (
        bool(settings.kis_trader_enabled)
        and bool(settings.kis_primary_ready)
        and research_status == "ok"
    )
    ready_to_execute = (
        ready_to_plan
        and execution_enabled
        and bool(process_payload.get("direct_alive"))
    )
    return {
        "enabled": settings.kis_trader_enabled,
        "primary_ready": settings.kis_primary_ready,
        "research_status": research_status,
        "llm_ready": llm_ready,
        "execution_enabled": execution_enabled,
        "execution_mode": "live" if execution_enabled else "dry_run",
        "runner": process_payload,
        "ready_to_plan": ready_to_plan,
        "ready_to_execute": ready_to_execute,
        "blockers": blockers,
        "warnings": warnings,
        "limits": {
            "interval_sec": int(settings.kis_trader_interval_sec),
            "max_orders_per_cycle": int(settings.kis_trader_max_orders_per_cycle),
            "max_budget_per_order_krw": float(
                settings.kis_trader_max_budget_per_order_krw
            ),
            "min_confidence": float(settings.kis_trader_min_confidence),
            "allow_sell": bool(settings.kis_trader_allow_sell),
            "report_context_top_k": int(settings.kis_trader_report_context_top_k),
        },
    }


def _runtime_storage_policy() -> RuntimeStoragePolicy:
    runtime_dir = Path(settings.runtime_state_path).parent
    return RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir or Path(".runtime")),
        reports_db_path=settings.naver_reports_db_path,
        pdf_archive_dir=settings.naver_reports_pdf_archive_dir,
        large_file_threshold_mb=settings.runtime_storage_large_file_threshold_mb,
        prune_unreferenced_pdfs=settings.runtime_storage_prune_unreferenced_pdfs,
    )


@app.get("/api/runtime/storage")
async def runtime_storage_status() -> dict[str, Any]:
    return build_runtime_storage_report(_runtime_storage_policy())


@app.post("/api/runtime/storage/cleanup")
async def runtime_storage_cleanup(dry_run: bool = True) -> dict[str, Any]:
    result = cleanup_runtime_storage(_runtime_storage_policy(), dry_run=dry_run)
    result["after"] = build_runtime_storage_report(_runtime_storage_policy())
    return result


@app.get("/api/kis/trader/status")
async def kis_trader_status() -> dict[str, Any]:
    snapshot = kis_trader_store.read_snapshot()
    readiness = _build_kis_trader_readiness()
    if not snapshot:
        return {
            "status": "missing",
            "enabled": settings.kis_trader_enabled,
            "readiness": readiness,
        }
    if not isinstance(snapshot, dict):
        return {
            "status": "invalid",
            "enabled": settings.kis_trader_enabled,
            "readiness": readiness,
        }
    return {
        "status": "ok",
        "enabled": settings.kis_trader_enabled,
        "readiness": readiness,
        "snapshot": snapshot,
    }


@app.get("/api/rebalance/kis-status")
async def rebalance_kis_status() -> dict[str, Any]:
    return await _build_kis_rebalance_status_payload()


@app.post("/api/kis/trader/run-once")
async def kis_trader_run_once() -> dict[str, Any]:
    if not settings.kis_primary_ready:
        raise HTTPException(
            status_code=400, detail="kis primary account not configured"
        )
    snapshot = await kis_llm_trader.run_once()
    return {
        "status": "ok",
        "snapshot": snapshot,
    }


@app.get("/api/kis/blocks/status")
async def kis_blocks_status() -> dict[str, Any]:
    return kis_block_trader.status()


@app.get("/api/kis/blocks")
async def kis_blocks() -> dict[str, Any]:
    return await kis_block_trader.snapshot()


@app.post("/api/kis/blocks/manager/run-once")
async def kis_blocks_manager_run_once() -> dict[str, Any]:
    if not settings.kis_primary_ready:
        raise HTTPException(
            status_code=400, detail="kis primary account not configured"
        )
    return await kis_block_trader.run_manager_once()


@app.post("/api/kis/blocks/adopt-existing/run-once")
async def kis_blocks_adopt_existing_run_once() -> dict[str, Any]:
    if not settings.kis_primary_ready:
        raise HTTPException(
            status_code=400, detail="kis primary account not configured"
        )
    return await kis_block_trader.run_adoption_once()


@app.post("/api/kis/blocks/executor/tick")
async def kis_blocks_executor_tick() -> dict[str, Any]:
    if not settings.kis_primary_ready:
        raise HTTPException(
            status_code=400, detail="kis primary account not configured"
        )
    return await kis_block_trader.executor_tick(manual=True)


@app.post("/api/kis/blocks/kill-switch")
async def kis_blocks_kill_switch(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    reason = str((payload or {}).get("reason") or "manual")
    return {
        "status": "ok",
        "kill_switch": kis_block_trader.set_kill_switch(True, reason=reason),
    }


@app.post("/api/kis/blocks/kill-switch/release")
async def kis_blocks_kill_switch_release(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason = str((payload or {}).get("reason") or "manual_release")
    return {
        "status": "ok",
        "kill_switch": kis_block_trader.set_kill_switch(False, reason=reason),
    }


@app.post("/api/kis/blocks/orders/{order_id}/cancel")
async def kis_block_order_cancel(
    order_id: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason = str((payload or {}).get("reason") or "manual_cancel")
    result = await kis_block_trader.cancel_order(order_id, reason=reason)
    if result.get("status") == "missing":
        raise HTTPException(status_code=404, detail="order not found")
    return result


@app.get("/api/kis/blocks/{block_id}")
async def kis_block_detail(block_id: str) -> dict[str, Any]:
    result = kis_block_trader.block_detail(block_id)
    if result.get("status") == "missing":
        raise HTTPException(status_code=404, detail="block not found")
    return result


@app.post("/api/kis/blocks/{block_id}/pause")
async def kis_block_pause(block_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    reason = str((payload or {}).get("reason") or "manual_pause")
    result = kis_block_trader.pause_block(block_id, reason=reason)
    if result.get("status") == "missing":
        raise HTTPException(status_code=404, detail="block not found")
    return result


@app.post("/api/kis/blocks/{block_id}/resume")
async def kis_block_resume(block_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    reason = str((payload or {}).get("reason") or "manual_resume")
    result = kis_block_trader.resume_block(block_id, reason=reason)
    if result.get("status") == "missing":
        raise HTTPException(status_code=404, detail="block not found")
    return result


@app.post("/api/kis/blocks/{block_id}/close")
async def kis_block_close(block_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.kis_primary_ready:
        raise HTTPException(
            status_code=400, detail="kis primary account not configured"
        )
    reason = str((payload or {}).get("reason") or "manual_close")
    result = await kis_block_trader.close_block(block_id, reason=reason)
    if result.get("status") == "missing":
        raise HTTPException(status_code=404, detail="block not found")
    return result


@app.get("/api/portfolio-coach/review-queue")
async def portfolio_coach_review_queue(
    status: str = "pending_review",
    limit: int = 20,
) -> dict[str, Any]:
    rows = portfolio_coach_store.list_advice_messages(status=status, limit=limit)
    return {
        "status": "ok",
        "count": len(rows),
        "items": rows,
    }


@app.post("/api/portfolio-coach/review-queue/{message_id}/approve")
async def portfolio_coach_review_approve(
    message_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    row = portfolio_coach_store.get_advice_message(message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")

    message = str(row.get("message_md") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="empty message")

    review_note = str(payload.get("review_note") or "").strip()
    sent = await telegram.send_message(message)
    sent_ok = bool(sent.get("ok"))
    portfolio_coach_store.update_message_status(
        message_id=message_id,
        status="sent" if sent_ok else "failed",
        review_note=review_note,
    )
    return {
        "status": "ok",
        "message_id": int(message_id),
        "sent": sent_ok,
    }


@app.post("/api/portfolio-coach/review-queue/{message_id}/reject")
async def portfolio_coach_review_reject(
    message_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    review_note = str(payload.get("review_note") or "").strip()
    updated = portfolio_coach_store.update_message_status(
        message_id=message_id,
        status="rejected",
        review_note=review_note,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="message not found")
    return {
        "status": "ok",
        "message_id": int(message_id),
        "updated": True,
    }


@app.get("/api/reports/status")
async def reports_status() -> dict[str, Any]:
    rag_status = (
        rag_store.status()
        if (settings.rag_enabled and rag_store is not None)
        else {
            "available": False,
            "reason": "rag_disabled",
            "persist_path": settings.rag_persist_path,
            "collection_name": settings.rag_collection_name,
        }
    )
    return {
        "status": "ok",
        "enabled": settings.naver_reports_enabled,
        "repository": naver_report_repository.status(),
        "intelligence": build_report_intelligence_status(settings),
        "rag": rag_status,
        "fundamentals": symbol_fundamentals_service.status(),
    }


@app.post("/api/reports/crawl-once")
async def reports_crawl_once() -> dict[str, Any]:
    result = await run_report_collection_cycle(
        crawler=naver_report_crawler,
        repository=naver_report_repository,
        rag_store=rag_store,
        rag_enabled=settings.rag_enabled,
        rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
        refresh_symbol_directory=False,
    )
    return {
        "status": "ok",
        "snapshot": result.get("snapshot"),
        "metadata_repair": result.get("metadata_repair"),
        "rag_sync": result.get("rag_sync"),
        "rag_metadata_sync": result.get("rag_metadata_sync"),
    }


@app.post("/api/reports/repair-metadata")
async def reports_repair_metadata(
    sync_rag_after: bool = True,
    prune_orphans: bool = True,
) -> dict[str, Any]:
    repair = naver_report_repository.repair_metadata_quality()
    rag_sync_result: dict[str, Any] | None = None
    if sync_rag_after:
        rag_sync_result = sync_report_rag(
            repository=naver_report_repository,
            rag_store=rag_store,
            enabled=settings.rag_enabled,
            limit=settings.rag_sync_chunk_limit,
            metadata_only=True,
            prune_missing=prune_orphans,
        )
    return {
        "status": "ok",
        "repair": repair,
        "rag_sync": rag_sync_result,
    }


@app.get("/api/reports/search")
async def reports_search(
    query: str = "",
    symbol: str = "",
    category: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    rows = naver_report_repository.search(
        query=query,
        symbol=symbol,
        category=category,
        limit=limit,
    )
    return {
        "status": "ok",
        "count": len(rows),
        "items": rows,
    }


@app.get("/api/rag/status")
async def rag_status() -> dict[str, Any]:
    if not settings.rag_enabled:
        return {
            "status": "ok",
            "enabled": False,
            "rag": {
                "available": False,
                "reason": "rag_disabled",
                "persist_path": settings.rag_persist_path,
                "collection_name": settings.rag_collection_name,
            },
        }
    if rag_store is None:
        return {
            "status": "ok",
            "enabled": True,
            "rag": {
                "available": False,
                "reason": "rag_store_missing",
                "persist_path": settings.rag_persist_path,
                "collection_name": settings.rag_collection_name,
            },
        }
    return {
        "status": "ok",
        "enabled": True,
        "rag": rag_store.status(),
    }


@app.post("/api/rag/sync")
async def rag_sync(
    force: bool = False,
    metadata_only: bool = False,
    prune_orphans: bool = False,
) -> dict[str, Any]:
    if not settings.rag_enabled:
        return {
            "status": "ok",
            "enabled": False,
            "result": {
                "status": "skipped",
                "reason": "rag_disabled",
            },
        }
    if rag_store is None:
        return {
            "status": "ok",
            "enabled": True,
            "result": {
                "status": "skipped",
                "reason": "rag_store_missing",
            },
        }
    result = sync_report_rag(
        repository=naver_report_repository,
        rag_store=rag_store,
        enabled=settings.rag_enabled,
        limit=settings.rag_sync_chunk_limit,
        force_update=force,
        metadata_only=metadata_only,
        prune_missing=prune_orphans,
    )
    return {
        "status": "ok",
        "enabled": True,
        "result": result or {"status": "skipped", "reason": "rag_store_missing"},
    }


@app.get("/api/rag/search")
async def rag_search(
    query: str,
    symbol: str = "",
    broker: str = "",
    doc_id: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    text = str(query or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="query is required")
    if not settings.rag_enabled or rag_store is None:
        return {
            "status": "ok",
            "enabled": False,
            "count": 0,
            "items": [],
        }
    resolved_limit = settings.rag_query_top_k if limit is None else int(limit)
    rows = rag_store.query(
        query=text,
        symbol=symbol,
        broker=broker,
        doc_id=doc_id,
        date_from=date_from,
        date_to=date_to,
        limit=resolved_limit,
    )
    return {
        "status": "ok",
        "enabled": True,
        "count": len(rows),
        "items": rows,
    }


def run() -> None:
    import uvicorn

    uvicorn.run(
        "tradecraft.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
