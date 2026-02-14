from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tradecraft.config import AppSettings
from tradecraft.services.binance import BinanceAdapter, BinanceConfig
from tradecraft.services.bithumb import BithumbAdapter, BithumbConfig
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.market import mock_dashboard, replace_venue_assets, upsert_venue_assets
from tradecraft.services.runtime_bridge import RuntimeSnapshotReader
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
logger = logging.getLogger(__name__)
telegram_poller_task: asyncio.Task[None] | None = None
telegram_update_offset: int | None = None
bithumb_cached_assets: list[dict[str, Any]] | None = None
kis_primary_cached_assets: list[dict[str, Any]] | None = None
kis_primary_us_cached_assets: list[dict[str, Any]] | None = None
kis_secondary_cached_assets: list[dict[str, Any]] | None = None

app = FastAPI(title="TradeCraft UI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def _build_dashboard_payload(include_telegram: bool = True) -> dict[str, Any]:
    global bithumb_cached_assets, kis_primary_cached_assets, kis_primary_us_cached_assets, kis_secondary_cached_assets
    data = mock_dashboard()
    if settings.upbit_ready:
        try:
            upbit_assets = await upbit.fetch_balance_assets()
            if replace_venue_assets(data, "upbit", upbit_assets):
                data["events"].append({"type": "upbit", "message": "업비트 실잔고 연동 완료"})
        except Exception as exc:
            logger.warning("upbit balance fetch failed: %s", exc)
            data["events"].append({"type": "upbit", "message": "업비트 잔고 조회 실패, mock 유지"})
    else:
        data["events"].append({"type": "upbit", "message": "업비트 키 미설정: mock 잔고 사용"})

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
            data["events"].append({"type": "bithumb", "message": "빗썸 실잔고 연동 완료"})
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
                data["events"].append({"type": "bithumb", "message": "빗썸 조회 실패, 최근 실잔고 유지"})
            else:
                data["events"].append({"type": "bithumb", "message": "빗썸 조회 실패"})
    else:
        data["events"].append({"type": "bithumb", "message": "빗썸 키 미설정"})

    binance_spot_synced = False
    if settings.binance_spot_ready:
        try:
            spot_assets = await binance.fetch_spot_assets()
            if not replace_venue_assets(data, "binance", spot_assets):
                upsert_venue_assets(
                    data,
                    venue_id="binance",
                    label="바이낸스 현물",
                    market="해외 가상자산 (Spot)",
                    assets=spot_assets,
                )
            binance_spot_synced = True
            data["events"].append({"type": "binance", "message": "바이낸스 Spot 잔고 연동 완료"})
        except Exception as exc:
            logger.warning("binance spot balance fetch failed: %s", exc)
            data["events"].append({"type": "binance", "message": "바이낸스 Spot 조회 실패"})
    else:
        data["events"].append({"type": "binance", "message": "바이낸스 Spot 키 미설정"})

    binance_futures_synced = False
    if settings.binance_futures_ready:
        try:
            futures_assets = await binance.fetch_futures_assets()
            upsert_venue_assets(
                data,
                venue_id="binance_futures",
                label="바이낸스 선물",
                market="해외 가상자산 (Futures)",
                assets=futures_assets,
            )
            binance_futures_synced = True
            data["events"].append({"type": "binance", "message": "바이낸스 Futures 잔고 연동 완료"})
        except Exception as exc:
            logger.warning("binance futures balance fetch failed: %s", exc)
            data["events"].append({"type": "binance", "message": "바이낸스 Futures 조회 실패"})
    else:
        data["events"].append({"type": "binance", "message": "바이낸스 Futures 키 미설정"})

    if (not binance_spot_synced and not binance_futures_synced) and (
        not settings.binance_spot_ready and not settings.binance_futures_ready
    ):
        data["events"].append({"type": "binance", "message": "바이낸스 키 미설정: mock 잔고 사용"})

    if settings.kis_primary_ready:
        try:
            primary_assets = await kis_primary.fetch_balance_assets()
            if replace_venue_assets(data, "kr_stock", primary_assets):
                kis_primary_cached_assets = primary_assets
                data["events"].append({"type": "kis", "message": "KIS 1번 계좌 실잔고 연동 완료"})
        except Exception as exc:
            logger.warning("kis primary balance fetch failed: %s", exc)
            if kis_primary_cached_assets:
                replace_venue_assets(data, "kr_stock", kis_primary_cached_assets)
                data["events"].append({"type": "kis", "message": "KIS 1번 계좌 조회 실패, 최근 실잔고 유지"})
            else:
                data["events"].append({"type": "kis", "message": "KIS 1번 계좌 조회 실패, mock 유지"})
        try:
            primary_us_assets = await kis_primary.fetch_us_balance_assets()
            if replace_venue_assets(data, "us_stock", primary_us_assets):
                kis_primary_us_cached_assets = primary_us_assets
                data["events"].append({"type": "kis", "message": "KIS 1번 계좌 미장 실잔고 연동 완료"})
        except Exception as exc:
            logger.warning("kis primary us balance fetch failed: %s", exc)
            if kis_primary_us_cached_assets is not None:
                replace_venue_assets(data, "us_stock", kis_primary_us_cached_assets)
                data["events"].append({"type": "kis", "message": "KIS 1번 계좌 미장 조회 실패, 최근 실잔고 유지"})
            else:
                data["events"].append({"type": "kis", "message": "KIS 1번 계좌 미장 조회 실패, mock 유지"})
    else:
        data["events"].append({"type": "kis", "message": "KIS 1번 키 미설정: mock 잔고 사용"})

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
            data["events"].append({"type": "kis", "message": "KIS 2번 계좌 실잔고 연동 완료"})
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
                data["events"].append({"type": "kis", "message": "KIS 2번 계좌 조회 실패, 최근 실잔고 유지"})
            else:
                data["events"].append({"type": "kis", "message": "KIS 2번 계좌 조회 실패"})
    else:
        data["events"].append({"type": "kis", "message": "KIS 2번 키 미설정"})

    runtime_sessions, runtime_status = runtime_reader.read_sessions()
    if runtime_sessions is not None:
        data["sessions"] = runtime_sessions
        data["events"].append({"type": "runtime", "message": "매매 런타임 모듈 연결됨"})
    else:
        if runtime_status == "missing":
            data["events"].append({"type": "runtime", "message": "매매 런타임 모듈 미연결: mock 세션 사용"})
        elif runtime_status == "stale":
            data["events"].append({"type": "runtime", "message": "매매 런타임 stale: mock 세션 사용"})
        else:
            data["events"].append({"type": "runtime", "message": "매매 런타임 상태 오류: mock 세션 사용"})

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
    sent = await telegram.send_message(reply, parse_mode=parse_mode, chat_id=target_chat_id or None)
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
        updates = await telegram.get_updates(offset=telegram_update_offset, timeout_sec=15)
        if not updates.get("ok"):
            await asyncio.sleep(2.0)
            continue

        rows = list(updates.get("result") or [])
        if not rows:
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


@app.on_event("startup")
async def startup_telegram_polling() -> None:
    global telegram_poller_task
    if telegram_poller_task is not None:
        return
    if not telegram.config.ready:
        return

    await _prime_telegram_offset()
    telegram_poller_task = asyncio.create_task(_telegram_poll_worker())


@app.on_event("shutdown")
async def shutdown_telegram_polling() -> None:
    global telegram_poller_task
    if telegram_poller_task is None:
        return
    telegram_poller_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await telegram_poller_task
    telegram_poller_task = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    _, runtime_status = runtime_reader.read_sessions()
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


def run() -> None:
    import uvicorn

    uvicorn.run(
        "tradecraft.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
