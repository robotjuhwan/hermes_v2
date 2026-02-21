from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tradecraft.config import AppSettings
from tradecraft.services.binance import STABLE_USD_ASSETS, BinanceAdapter, BinanceConfig
from tradecraft.services.bithumb import BithumbAdapter, BithumbConfig
from tradecraft.services.fx import FxRateConfig, FxRateService
from tradecraft.services.freqtrade import (
    FreqtradeBotConfig,
    FreqtradeBridge,
    FreqtradeBridgeConfig,
)
from tradecraft.services.freqtrade_process import (
    FreqtradeProcessManager,
    FreqtradeProcessManagerConfig,
)
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.kis_llm_trader import KISLLMTrader, KISLLMTraderConfig
from tradecraft.services.portfolio_coach import PortfolioCoachStore
from tradecraft.services.naver_reports import (
    NaverReportCrawlerConfig,
    NaverReportRepository,
    NaverSecuritiesCrawler,
)
from tradecraft.services.rag_store import RAGStore, RAGStoreConfig
from tradecraft.services.market import (
    mock_dashboard,
    recalculate_dashboard_totals,
    replace_venue_assets,
    upsert_venue_assets,
)
from tradecraft.services.runtime_bridge import (
    ResearchSnapshotReader,
    RuntimeSnapshotReader,
)
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.telegram import TelegramBridge, TelegramConfig
from tradecraft.services.telegram_cli import TelegramCLI
from tradecraft.services.upbit import UpbitAdapter, UpbitConfig

settings = AppSettings()
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
naver_report_repository = NaverReportRepository(settings.naver_reports_db_path)
rag_store = (
    RAGStore(
        RAGStoreConfig(
            persist_path=settings.rag_persist_path,
            collection_name=settings.rag_collection_name,
        )
    )
    if settings.rag_enabled
    else None
)
naver_report_crawler = NaverSecuritiesCrawler(
    config=NaverReportCrawlerConfig(
        db_path=settings.naver_reports_db_path,
        pdf_archive_dir=settings.naver_reports_pdf_archive_dir,
        seed_url=settings.naver_reports_seed_url,
        seed_urls=settings.naver_reports_seed_url_list,
        max_pages=settings.naver_reports_max_pages,
        since_date=settings.naver_reports_since_date,
        request_delay_sec=settings.naver_reports_request_delay_sec,
        min_pdf_text_chars=settings.naver_reports_min_pdf_text_chars,
        llm_bridge_command=settings.llm_bridge_command,
        llm_bridge_args=settings.llm_bridge_args,
        llm_bridge_url=settings.llm_bridge_url,
        llm_bridge_token=settings.llm_bridge_token,
        llm_bridge_timeout_ms=settings.llm_bridge_timeout_ms,
        llm_model=settings.llm_model,
    ),
    repository=naver_report_repository,
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
freqtrade_bot_configs = [
    FreqtradeBotConfig(
        bot_id="spot",
        label="Freqtrade Spot",
        api_url=settings.freqtrade_spot_api_url,
        username=settings.freqtrade_spot_username,
        password=settings.freqtrade_spot_password,
        config_path=settings.freqtrade_spot_config_path,
        venue_id="binance",
        venue_label="바이낸스 현물",
        market_tag="FREQTRADE_SPOT",
    ),
    FreqtradeBotConfig(
        bot_id="futures",
        label="Freqtrade Futures",
        api_url=settings.freqtrade_futures_api_url,
        username=settings.freqtrade_futures_username,
        password=settings.freqtrade_futures_password,
        config_path=settings.freqtrade_futures_config_path,
        venue_id="binance_futures",
        venue_label="바이낸스 선물",
        market_tag="FREQTRADE_FUTURES",
    ),
    FreqtradeBotConfig(
        bot_id="e0v1e",
        label="Freqtrade E0V1E",
        config_path="third_party/freqtrade/user_data/config_jurobot_e0v1e.json",
        venue_id="binance",
        venue_label="바이낸스 현물",
        market_tag="FREQTRADE_E0V1E",
    ),
    FreqtradeBotConfig(
        bot_id="freqai_reforcexy",
        label="FreqAI ReforceXY",
        config_path="third_party/freqtrade/user_data/config_jurobot_freqai_reforcexy.json",
        venue_id="binance",
        venue_label="바이낸스 현물",
        market_tag="FREQAI_REFORCEXY",
    ),
    FreqtradeBotConfig(
        bot_id="freqai_reforcexy_futures",
        label="FreqAI ReforceXY Futures",
        config_path="third_party/freqtrade/user_data/config_jurobot_freqai_reforcexy_futures.json",
        venue_id="binance_futures",
        venue_label="바이낸스 선물",
        market_tag="FREQAI_REFORCEXY_FUTURES",
    ),
    FreqtradeBotConfig(
        bot_id="freqai_lstm",
        label="FreqAI LSTM",
        config_path="third_party/freqtrade/user_data/config_jurobot_freqai_lstm.json",
        venue_id="binance_futures",
        venue_label="바이낸스 선물",
        market_tag="FREQAI_LSTM",
    ),
    FreqtradeBotConfig(
        bot_id="kis",
        label="Freqtrade KIS",
        config_path="third_party/freqtrade/user_data/config_kis_jurobot.json",
        venue_id="kis",
        venue_label="국장",
        market_tag="FREQTRADE_KIS",
    ),
]
freqtrade_bridge = FreqtradeBridge(
    FreqtradeBridgeConfig(
        timeout_sec=settings.freqtrade_timeout_sec,
        bot_api_url_overrides=settings.freqtrade_bot_api_url_map,
        bots=freqtrade_bot_configs,
    )
)
freqtrade_process_manager = FreqtradeProcessManager(
    FreqtradeProcessManagerConfig(
        executable_path=settings.freqtrade_executable_path,
        workdir=settings.freqtrade_workdir,
        runtime_dir=settings.freqtrade_runtime_dir,
        stop_timeout_sec=settings.freqtrade_stop_timeout_sec,
    ),
    bots=freqtrade_bot_configs,
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
    if telegram_poller_task is None and telegram.config.ready:
        await _prime_telegram_offset()
        telegram_poller_task = asyncio.create_task(_telegram_poll_worker())
    try:
        yield
    finally:
        if telegram_poller_task is None:
            return
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

    runtime_sessions, runtime_status = runtime_reader.read_sessions()
    runtime_sessions_applied = False
    if runtime_sessions is not None:
        data["sessions"] = runtime_sessions
        runtime_sessions_applied = True
        data["events"].append({"type": "runtime", "message": "매매 런타임 모듈 연결됨"})
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

    research_payload, research_status = research_reader.read_feed()
    if research_payload is not None:
        data["research"] = research_payload
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

    try:
        freqtrade_payload = await freqtrade_bridge.fetch_sessions(
            usdt_krw_rate=usdt_krw
        )
    except Exception as exc:
        logger.warning("freqtrade bridge fetch failed: %s", exc)
        freqtrade_payload = {"bots": [], "sessions": []}

    freqtrade_bots = list(freqtrade_payload.get("bots") or [])
    freqtrade_sessions = [
        row
        for row in list(freqtrade_payload.get("sessions") or [])
        if isinstance(row, dict)
    ]
    freqtrade_connected = any(
        bool(bot.get("connected")) for bot in freqtrade_bots if isinstance(bot, dict)
    )
    if freqtrade_connected and not runtime_sessions_applied:
        # When runtime snapshot is unavailable, prefer live freqtrade sessions over mock session cards.
        data["sessions"] = []
    if freqtrade_sessions:
        data["sessions"] = [*list(data.get("sessions") or []), *freqtrade_sessions]

    for bot in freqtrade_bots:
        if not isinstance(bot, dict):
            continue
        label = str(bot.get("label") or bot.get("bot_id") or "freqtrade")
        connected = bool(bot.get("connected"))
        open_trades = int(bot.get("open_trades") or 0)
        if connected:
            data["events"].append(
                {
                    "type": "freqtrade",
                    "message": f"{label} 연결됨: 오픈 포지션 {open_trades}건",
                }
            )
            continue
        if bool(bot.get("configured")):
            data["events"].append(
                {"type": "freqtrade", "message": f"{label} 연결 실패"}
            )

    if include_telegram:
        data["telegram"] = telegram.status()
    return data


async def _process_telegram_text(text: str, chat_id: str) -> dict[str, Any]:
    if text:
        telegram.last_webhook_message = text
    command, _ = telegram_cli.parse(text)
    if not command:
        return {"handled": False, "sent": False}

    if command in {"start", "help"}:
        handled, reply = telegram_cli.execute(text)
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


def _is_api_reachable(api_url: str) -> bool:
    clean = api_url.strip().rstrip("/")
    if not clean:
        return False
    parsed = urlparse(clean)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    try:
        with socket.create_connection((host, int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def _is_process_running(pattern: str) -> bool:
    query = str(pattern or "").strip()
    if not query:
        return False
    try:
        proc = subprocess.run(
            ["pgrep", "-f", query],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    return bool(str(proc.stdout or "").strip())


def _build_strategy_control_payload(
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    statuses = freqtrade_process_manager.list_statuses()
    resolved_api_urls: dict[str, str] = {}
    for bot in freqtrade_bot_configs:
        resolved = freqtrade_bridge._resolve_bot_config(bot)
        resolved_api_urls[bot.bot_id] = resolved.api_url if resolved else ""

    items: list[dict[str, Any]] = []
    for row in statuses:
        bot_id = str(row.get("bot_id") or "")
        api_url = resolved_api_urls.get(bot_id, "")
        enriched = dict(row)
        enriched["api_url"] = api_url
        enriched["api_reachable"] = _is_api_reachable(api_url)
        items.append(enriched)

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "actions": list(actions or []),
    }


async def _close_all_positions_before_stop(bot_id: str) -> dict[str, Any]:
    bot = next((row for row in freqtrade_bot_configs if row.bot_id == bot_id), None)
    if bot is None:
        raise KeyError(f"unknown bot_id: {bot_id}")

    resolved = freqtrade_bridge._resolve_bot_config(bot)
    if resolved is None:
        return {
            "bot_id": bot_id,
            "label": bot.label,
            "action": "position_cleanup_skipped",
            "reason": "freqtrade_api_not_configured",
        }

    auth = freqtrade_bridge._auth_tuple(resolved.username, resolved.password)
    status_url = f"{resolved.api_url}/api/v1/status"
    forceexit_url = f"{resolved.api_url}/api/v1/forceexit"
    timeout = httpx.Timeout(3.5)
    request_kwargs: dict[str, Any] = {"headers": {"Accept": "application/json"}}
    if auth is not None:
        request_kwargs["auth"] = auth

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            status_before = await client.get(status_url, **request_kwargs)
            status_before.raise_for_status()
            open_before = len(status_before.json() or [])
        except Exception as exc:
            return {
                "bot_id": bot_id,
                "label": bot.label,
                "action": "position_cleanup_failed",
                "reason": f"status_read_failed: {exc}",
            }

        if open_before <= 0:
            return {
                "bot_id": bot_id,
                "label": bot.label,
                "action": "positions_already_closed",
                "closed_trades": 0,
            }

        try:
            response = await client.post(
                forceexit_url,
                json={"tradeid": "all", "ordertype": "market"},
                **request_kwargs,
            )
            response.raise_for_status()
        except Exception:
            response = await client.post(
                forceexit_url,
                json={"tradeid": "all"},
                **request_kwargs,
            )
            response.raise_for_status()

        for _ in range(20):
            await asyncio.sleep(0.5)
            status_after = await client.get(status_url, **request_kwargs)
            status_after.raise_for_status()
            open_after = len(status_after.json() or [])
            if open_after <= 0:
                return {
                    "bot_id": bot_id,
                    "label": bot.label,
                    "action": "positions_closed",
                    "closed_trades": open_before,
                }

        return {
            "bot_id": bot_id,
            "label": bot.label,
            "action": "position_cleanup_failed",
            "reason": "open_positions_remain_after_forceexit",
        }


async def _run_strategy_action(
    action: str,
    bot_id: str | None = None,
) -> dict[str, Any]:
    try:
        if action == "start" and bot_id:
            actions = [freqtrade_process_manager.start(bot_id)]
        elif action == "stop" and bot_id:
            cleanup = await _close_all_positions_before_stop(bot_id)
            if cleanup.get("action") == "position_cleanup_failed":
                raise HTTPException(status_code=400, detail=cleanup.get("reason"))
            actions = [cleanup, freqtrade_process_manager.stop(bot_id)]
        elif action == "start_all":
            actions = freqtrade_process_manager.start_all()
        elif action == "stop_all":
            actions = []
            for row in freqtrade_bot_configs:
                cleanup = await _close_all_positions_before_stop(row.bot_id)
                actions.append(cleanup)
                if cleanup.get("action") == "position_cleanup_failed":
                    continue
                actions.append(freqtrade_process_manager.stop(row.bot_id))
        else:
            raise HTTPException(status_code=400, detail="invalid strategy action")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _build_strategy_control_payload(actions=actions)


def _extract_symbol_hint(query: str) -> str:
    raw = str(query or "")
    import re

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


@app.get("/api/research/ask")
async def research_ask(
    query: str,
    symbol: str = "",
    broker: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    text = str(query or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="query is required")

    resolved_symbol = str(symbol or "").strip() or _extract_symbol_hint(text)
    rows = naver_report_repository.search(
        query=text,
        symbol=resolved_symbol,
        category="",
        limit=max(min(int(limit), 20), 1) * 3,
    )

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if broker and str(row.get("broker") or "") != broker:
            continue
        published = str(row.get("published_at") or "")
        if date_from and published and published < date_from:
            continue
        if date_to and published and published > date_to:
            continue
        filtered.append(row)
        if len(filtered) >= max(min(int(limit), 20), 1):
            break

    facts_rows: list[dict[str, Any]] = []
    citations: list[str] = []
    for row in filtered:
        report_id = int(row.get("report_id") or 0)
        facts = naver_report_repository.get_report_facts(report_id)
        facts_payload = dict(row)
        if facts:
            facts_payload["facts"] = facts
            evidence_quotes = list(facts.get("evidence_quotes") or [])
            for quote in evidence_quotes[:2]:
                page = str((quote or {}).get("page") or "?")
                citations.append(
                    _format_citation(
                        str(row.get("broker") or ""),
                        str(row.get("published_at") or ""),
                        page,
                    )
                )
        facts_rows.append(facts_payload)

    summary_lines: list[str] = []
    for row in facts_rows[:3]:
        snippet = str(row.get("snippet") or "").strip()
        if snippet:
            summary_lines.append(f"- {snippet[:140]}")

    evidence_lines: list[str] = []
    risk_lines: list[str] = []
    rating_counts: dict[str, int] = {}
    target_values: list[int] = []
    for row in facts_rows:
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
            quote_text = str(bullet or "").strip()
            if not quote_text:
                continue
            evidence_quote = list(facts.get("evidence_quotes") or [])
            page = str((evidence_quote[0] if evidence_quote else {}).get("page") or "?")
            evidence_lines.append(
                f"- {quote_text[:140]} {_format_citation(str(row.get('broker') or ''), str(row.get('published_at') or ''), page)}"
            )
        for risk in list(facts.get("risks") or [])[:2]:
            risk_text = str(risk or "").strip()
            if risk_text:
                risk_lines.append(f"- {risk_text[:140]}")

    consensus_line = "- 리포트에 명시 근거 없음/추가 자료 필요"
    if rating_counts:
        top_rating = sorted(rating_counts.items(), key=lambda x: x[1], reverse=True)[0][
            0
        ]
        if target_values:
            avg_target = int(round(sum(target_values) / len(target_values)))
            consensus_line = (
                f"- 투자의견 다수: {top_rating}, 목표주가 평균: {avg_target:,} KRW"
            )
        else:
            consensus_line = f"- 투자의견 다수: {top_rating}"

    if not summary_lines:
        summary_lines = [
            "- 리포트에 명시 근거 없음/추가 자료 필요",
            "- 리포트에 명시 근거 없음/추가 자료 필요",
            "- 리포트에 명시 근거 없음/추가 자료 필요",
        ]
    else:
        while len(summary_lines) < 3:
            summary_lines.append("- 리포트에 명시 근거 없음/추가 자료 필요")

    if not evidence_lines:
        evidence_lines = ["- 리포트에 명시 근거 없음/추가 자료 필요"]
    if not risk_lines:
        risk_lines = ["- 리포트에 명시 근거 없음/추가 자료 필요"]

    answer = "\n".join(
        [
            "요약(3줄)",
            *summary_lines[:3],
            "",
            "핵심 근거(인용 포함)",
            *evidence_lines[:5],
            "",
            "리포트 간 차이/컨센서스",
            consensus_line,
            "",
            "리스크/반론 체크리스트",
            *risk_lines[:5],
            "",
            "근거 부족 시 안내",
            "- 리포트에 명시 근거 없음/추가 자료 필요",
        ]
    )

    used_report_ids = [
        int(row.get("report_id") or 0)
        for row in facts_rows
        if int(row.get("report_id") or 0) > 0
    ]
    _append_research_query_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": text,
            "symbol": resolved_symbol,
            "filters": {
                "broker": broker,
                "date_from": date_from,
                "date_to": date_to,
            },
            "top_k": max(min(int(limit), 20), 1),
            "used_report_ids": used_report_ids,
            "citations": citations[:20],
        }
    )

    return {
        "status": "ok",
        "query": text,
        "symbol": resolved_symbol,
        "count": len(facts_rows),
        "answer": answer,
        "citations": citations[:20],
        "items": facts_rows,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    _, runtime_status = runtime_reader.read_sessions()
    _, research_status = research_reader.read_feed()
    runtime_runner_alive = _is_process_running(
        r"tradecraft-runtime|tradecraft\.runtime\.runner|runtime/runner\.py"
    )
    research_runner_alive = _is_process_running(
        r"tradecraft-research|tradecraft\.runtime\.research_runner|research_runner\.py"
    )
    kis_trader_runner_alive = _is_process_running(
        r"tradecraft-kis-trader|tradecraft\.runtime\.kis_trader_runner|kis_trader_runner\.py"
    )
    naver_reports_runner_alive = _is_process_running(
        r"tradecraft-naver-reports|tradecraft\.runtime\.naver_reports_runner|naver_reports_runner\.py"
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
        "research_connected": research_status == "ok",
        "research_status": research_status,
        "runtime_runner_alive": runtime_runner_alive,
        "research_runner_alive": research_runner_alive,
        "kis_trader_runner_alive": kis_trader_runner_alive,
        "naver_reports_runner_alive": naver_reports_runner_alive,
        "kis_trader_enabled": settings.kis_trader_enabled,
        "naver_reports_enabled": settings.naver_reports_enabled,
        "llm_bridge_mode": settings.llm_bridge_mode,
        "llm_bridge_ready": settings.llm_bridge_ready,
    }


@app.get("/api/dashboard")
async def dashboard() -> dict[str, Any]:
    return await _build_dashboard_payload(include_telegram=True)


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


@app.get("/api/freqtrade/strategies")
async def freqtrade_strategy_status() -> dict[str, Any]:
    return _build_strategy_control_payload()


@app.post("/api/freqtrade/strategies/start-all")
async def freqtrade_strategy_start_all() -> dict[str, Any]:
    return await _run_strategy_action(action="start_all")


@app.post("/api/freqtrade/strategies/stop-all")
async def freqtrade_strategy_stop_all() -> dict[str, Any]:
    return await _run_strategy_action(action="stop_all")


@app.post("/api/freqtrade/strategies/{bot_id}/start")
async def freqtrade_strategy_start(bot_id: str) -> dict[str, Any]:
    return await _run_strategy_action(action="start", bot_id=bot_id)


@app.post("/api/freqtrade/strategies/{bot_id}/stop")
async def freqtrade_strategy_stop(bot_id: str) -> dict[str, Any]:
    return await _run_strategy_action(action="stop", bot_id=bot_id)


@app.post("/api/freqtrade/strategies/{bot_id}/usdt-limit")
async def freqtrade_strategy_set_usdt_limit(
    bot_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    usdt_raw = payload.get("usdt_limit")
    if usdt_raw is None:
        raise HTTPException(status_code=400, detail="usdt_limit is required")
    try:
        usdt_limit = float(usdt_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid usdt_limit") from exc

    try:
        action = freqtrade_process_manager.set_usdt_limit(bot_id, usdt_limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _build_strategy_control_payload(actions=[action])


@app.get("/api/kis/trader/status")
async def kis_trader_status() -> dict[str, Any]:
    snapshot = kis_trader_store.read_snapshot()
    if not snapshot:
        return {
            "status": "missing",
            "enabled": settings.kis_trader_enabled,
        }
    if not isinstance(snapshot, dict):
        return {
            "status": "invalid",
            "enabled": settings.kis_trader_enabled,
        }
    return {
        "status": "ok",
        "enabled": settings.kis_trader_enabled,
        "snapshot": snapshot,
    }


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
        "rag": rag_status,
    }


@app.post("/api/reports/crawl-once")
async def reports_crawl_once() -> dict[str, Any]:
    snapshot = await naver_report_crawler.crawl_once()
    rag_sync: dict[str, Any] | None = None
    if settings.rag_enabled and rag_store is not None:
        docs = naver_report_repository.list_chunks_for_rag(
            limit=settings.rag_sync_chunk_limit
        )
        rag_sync = rag_store.sync_documents(docs)
    return {
        "status": "ok",
        "snapshot": snapshot,
        "rag_sync": rag_sync,
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
async def rag_sync() -> dict[str, Any]:
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
    docs = naver_report_repository.list_chunks_for_rag(
        limit=settings.rag_sync_chunk_limit
    )
    result = rag_store.sync_documents(docs)
    return {
        "status": "ok",
        "enabled": True,
        "result": result,
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
