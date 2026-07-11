from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from datetime import date as date_type
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.codex_native import codex_native_service_config_kwargs
from tradecraft.services.llm_model_policy import resolve_llm_model_policy
from tradecraft.services.intelligence import (
    build_report_intelligence_stack,
    run_report_collection_cycle,
    run_report_collection_cycle_with_timeout,
)
from tradecraft.services.kis import KISAdapter, KISConfig
from tradecraft.services.portfolio_coach import (
    KISHoldingsProvider,
    PortfolioCoachConfig,
    PortfolioCoachService,
)
from tradecraft.services.research_pipeline import (
    ResearchPipeline,
    ResearchPipelineConfig,
)
from tradecraft.services.krx_holiday import KRXHolidayCalendar
from tradecraft.services.naver_reports import NaverReportRepository
from tradecraft.services.telegram import TelegramBridge, TelegramConfig

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
ADVICE_SLOTS_KST: list[tuple[int, int, str]] = [
    (8, 0, "장전"),
    (12, 0, "점심"),
    (15, 40, "장마감"),
]


def _service_reports_enabled(
    service_name: str,
    enabled: bool,
    *,
    research_runner_collect_reports: bool = False,
) -> bool:
    if not enabled:
        return False
    clean_service = str(service_name or "").strip()
    if clean_service == "tradecraft-intelligence":
        return False
    if clean_service == "tradecraft-research" and not bool(
        research_runner_collect_reports
    ):
        return False
    return True


def _write_research_disabled_snapshot(
    *,
    state_store: RuntimeStateStore,
    service_name: str,
    research_enabled: bool,
    reports_enabled: bool,
) -> dict[str, Any]:
    snapshot = {
        "updated_at": utc_now_iso(),
        "source": "research_runner",
        "service": service_name,
        "status": "disabled",
        "research_enabled": bool(research_enabled),
        "reports_enabled": bool(reports_enabled),
        "reason": "research_and_reports_disabled",
    }
    state_store.write_snapshot(snapshot)
    return snapshot


def _build_pipeline(settings: AppSettings) -> ResearchPipeline:
    llm_policy = resolve_llm_model_policy(settings, component="research_pipeline")
    config = ResearchPipelineConfig(
        state_path=settings.research_state_path,
        strategy_md_path=settings.research_strategy_md_path,
        market_scope=settings.research_market_scope,
        codex_command=settings.research_codex_command,
        codex_query=settings.research_codex_query,
        codex_timeout_sec=settings.research_codex_timeout_sec,
        report_urls=settings.research_report_url_list,
        kis_block_db_path=settings.kis_block_trader_db_path,
        report_db_path=settings.naver_reports_db_path,
        report_db_top_k=settings.research_db_reference_top_k,
        rag_enabled=settings.rag_enabled,
        rag_persist_path=settings.rag_persist_path,
        rag_collection_name=settings.rag_collection_name,
        rag_query_top_k=settings.rag_query_top_k,
        max_items=settings.research_max_items,
        knowledge_max_chars=settings.research_knowledge_max_chars,
        codex_runtime_mode=settings.codex_runtime_mode,
        codex_runtime_sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
        codex_runtime_timeout_ms=settings.codex_runtime_timeout_ms,
        llm_model=llm_policy.model,
        llm_reasoning_effort=llm_policy.reasoning_effort,
        llm_usage_enabled=settings.llm_usage_enabled,
        llm_usage_db_path=settings.llm_usage_db_path,
        llm_usage_component="research_pipeline",
        **codex_native_service_config_kwargs(settings),
        market_intelligence_sources=settings.market_intelligence_source_list,
    )
    return ResearchPipeline(config)


def _build_primary_kis(settings: AppSettings) -> KISAdapter | None:
    if not settings.kis_primary_ready:
        return None
    return KISAdapter(
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


def _format_krw(value: float) -> str:
    if value >= 1_0000_0000:
        return f"{value/1_0000_0000:.2f}B KRW"
    if value >= 1_0000:
        return f"{value/1_0000:.1f}W KRW"
    return f"{value:,.0f} KRW"


def _summarize_balance(assets: list[dict]) -> dict[str, str]:
    cash_krw = 0.0
    total_krw = 0.0
    positions: list[tuple[str, float]] = []
    for row in assets:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        asset = str(row.get("asset") or "")
        value_krw = float(row.get("value_krw") or 0.0)
        total_krw += max(value_krw, 0.0)
        if kind == "cash" and asset == "KRW":
            cash_krw += max(value_krw, 0.0)
        elif kind == "position" and value_krw > 0:
            positions.append((asset, value_krw))

    positions.sort(key=lambda item: item[1], reverse=True)
    top = ", ".join(symbol for symbol, _ in positions[:3])
    return {
        "total": _format_krw(total_krw),
        "cash": _format_krw(cash_krw),
        "count": str(len(positions)),
        "top": top,
    }


def _read_knowledge_excerpt(path: str, max_chars: int) -> str:
    try:
        text = open(path, "r", encoding="utf-8").read()
    except Exception:
        return ""
    limit = max(int(max_chars), 200)
    return text[:limit].strip()


def _report_db_last_updated_at(repository: NaverReportRepository) -> str | None:
    try:
        status = repository.status()
    except Exception:
        return None
    return str(status.get("last_updated_at") or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _extract_pick_codes(snapshot: dict, limit: int = 6) -> list[str]:
    out: list[str] = []
    max_items = max(int(limit), 1)
    for item in list(snapshot.get("items") or []):
        if not isinstance(item, dict):
            continue
        for code in list(item.get("picks") or []):
            text = str(code).strip()
            if not re.fullmatch(r"\d{6}", text):
                continue
            if text in out:
                continue
            out.append(text)
            if len(out) >= max_items:
                return out
    return out


def _company_name_from_title(title: str, symbol: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", str(title or ""))
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""
    cleaned = raw.replace(symbol, "")
    cleaned = cleaned.replace("(", " ").replace(")", " ")
    cleaned = cleaned.replace("리포트", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned == symbol or re.fullmatch(r"\d{6}", cleaned):
        return ""
    return cleaned[:40]


def _fetch_company_name_from_naver(symbol: str) -> str:
    code = str(symbol or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return ""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        response = httpx.get(url, timeout=4.0, follow_redirects=True)
        if response.status_code >= 400:
            return ""
        html = response.text
    except Exception:
        return ""
    match = re.search(r"<title>\s*([^:<]+?)\s*[:<]", html, flags=re.IGNORECASE)
    if not match:
        return ""
    name = re.sub(r"\s+", " ", str(match.group(1) or "")).strip()
    if not name or name == code or re.fullmatch(r"\d{6}", name):
        return ""
    return name[:40]


def _fetch_company_name_from_pykrx(symbol: str) -> str:
    code = str(symbol or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return ""
    try:
        from pykrx import stock  # type: ignore

        name = str(stock.get_market_ticker_name(code) or "").strip()
    except Exception:
        return ""
    if not name or name == code or re.fullmatch(r"\d{6}", name):
        return ""
    return name[:40]


def _resolve_symbol_names_for_codes(
    *,
    codes: list[str],
    report_repository: NaverReportRepository,
    kis: KISAdapter | None,
    initial_map: dict[str, str] | None = None,
) -> dict[str, str]:
    unique_codes: list[str] = []
    for code in codes:
        text = str(code or "").strip()
        if not re.fullmatch(r"\d{6}", text):
            continue
        if text not in unique_codes:
            unique_codes.append(text)

    resolved: dict[str, str] = {}
    for code, name in dict(initial_map or {}).items():
        c = str(code or "").strip()
        n = str(name or "").strip()
        if re.fullmatch(r"\d{6}", c) and n and not re.fullmatch(r"\d{6}", n):
            resolved[c] = n

    if not unique_codes:
        return resolved

    if hasattr(report_repository, "resolve_symbol_names"):
        try:
            cached = report_repository.resolve_symbol_names(unique_codes)
        except Exception:
            cached = {}
        for code, name in dict(cached or {}).items():
            text_name = str(name or "").strip()
            if text_name and not re.fullmatch(r"\d{6}", text_name):
                resolved[str(code).strip()] = text_name

    for code in unique_codes:
        if resolved.get(code):
            continue
        rows = report_repository.search(query="", symbol=code, category="", limit=3)
        for row in rows:
            company_name = str(row.get("company_name") or "").strip()
            if company_name and (
                company_name == code or re.fullmatch(r"\d{6}", company_name)
            ):
                company_name = ""
            if not company_name:
                company_name = _company_name_from_title(
                    str(row.get("title") or ""), code
                )
            if company_name:
                resolved[code] = company_name
                break

    for code in unique_codes:
        if resolved.get(code):
            continue
        pykrx_name = _fetch_company_name_from_pykrx(code)
        if pykrx_name:
            resolved[code] = pykrx_name

    unresolved = [code for code in unique_codes if not resolved.get(code)]
    if unresolved and hasattr(report_repository, "refresh_symbol_directory_from_krx"):
        try:
            report_repository.refresh_symbol_directory_from_krx()
            refreshed = report_repository.resolve_symbol_names(unresolved)
        except Exception:
            refreshed = {}
        for code, name in dict(refreshed or {}).items():
            text_name = str(name or "").strip()
            if text_name and not re.fullmatch(r"\d{6}", text_name):
                resolved[str(code).strip()] = text_name

    for code in unique_codes:
        if resolved.get(code):
            continue
        naver_name = _fetch_company_name_from_naver(code)
        if naver_name:
            resolved[code] = naver_name

    if kis is not None:
        for code in unique_codes:
            if resolved.get(code):
                continue
            try:
                quote = asyncio.run(kis.fetch_domestic_quote(code))
            except Exception:
                continue
            name = str(quote.get("name") or "").strip()
            if name and not re.fullmatch(r"\d{6}", name):
                resolved[code] = name

    if hasattr(report_repository, "upsert_symbol_directory"):
        for code, name in list(resolved.items()):
            if not re.fullmatch(r"\d{6}", code):
                continue
            if not str(name or "").strip() or re.fullmatch(
                r"\d{6}", str(name or "").strip()
            ):
                continue
            try:
                report_repository.upsert_symbol_directory(
                    symbol=code,
                    company_name=name,
                    source="research_runner_resolve",
                    confidence=0.95,
                    status="active",
                )
            except Exception:
                logger.warning(
                    "failed to upsert resolved symbol name code=%s name=%s",
                    code,
                    name,
                    exc_info=True,
                )

    return resolved


def _resolve_pick_name_map(
    snapshot: dict,
    report_repository: NaverReportRepository,
    kis: KISAdapter | None,
) -> dict[str, str]:
    picks = _extract_pick_codes(snapshot)
    return _resolve_symbol_names_for_codes(
        codes=picks,
        report_repository=report_repository,
        kis=kis,
        initial_map={},
    )


def _should_run_learning(
    current_db_updated_at: str | None,
    previous_db_updated_at: str,
    has_snapshot: bool,
    snapshot_updated_at: str | None,
    max_snapshot_age_sec: int,
) -> bool:
    if current_db_updated_at is None:
        return True
    if not has_snapshot:
        return True
    raw_snapshot_updated_at = str(snapshot_updated_at or "").strip()
    if not raw_snapshot_updated_at:
        return True
    try:
        snapshot_dt = datetime.fromisoformat(raw_snapshot_updated_at)
    except ValueError:
        return True
    if snapshot_dt.tzinfo is None:
        snapshot_dt = snapshot_dt.replace(tzinfo=KST)
    age_sec = (datetime.now(KST) - snapshot_dt.astimezone(KST)).total_seconds()
    if age_sec > max(max_snapshot_age_sec, 1):
        return True
    return current_db_updated_at != previous_db_updated_at


def _next_advice_slot(
    now: datetime,
    is_open_day: Callable[[date_type], bool] | None = None,
) -> tuple[datetime, str]:
    base = now.astimezone(KST)
    day = base.date()
    checker = is_open_day or (lambda value: value.weekday() < 5)

    for _ in range(14):
        if checker(day):
            for hour, minute, label in ADVICE_SLOTS_KST:
                candidate = datetime(
                    year=day.year,
                    month=day.month,
                    day=day.day,
                    hour=hour,
                    minute=minute,
                    tzinfo=KST,
                )
                if candidate > base:
                    return candidate, label
        day = day + timedelta(days=1)

    fallback = base + timedelta(minutes=1)
    return fallback, "장전"


def _build_advice_message(
    snapshot: dict,
    label: str,
    scheduled_at: datetime,
    balance: dict[str, str] | None,
    knowledge_excerpt: str,
    pick_name_map: dict[str, str] | None = None,
) -> str:
    picks: list[str] = []
    summaries: list[str] = []
    for item in list(snapshot.get("items") or []):
        if isinstance(item, dict):
            for code in list(item.get("picks") or []):
                text = str(code).strip()
                if text and text not in picks:
                    picks.append(text)
                    if len(picks) >= 6:
                        break
            summary = str(item.get("summary") or "").strip()
            if summary:
                summaries.append(summary)
        if len(picks) >= 6 and len(summaries) >= 2:
            break

    display_picks: list[str] = []
    name_map = pick_name_map or {}
    for code in picks:
        company_name = str(name_map.get(code) or "").strip()
        if company_name:
            display_picks.append(f"{company_name}({code})")
        else:
            display_picks.append(f"종목미상({code})")

    lines = [
        f"[국장 조언/{label}] {scheduled_at.strftime('%Y-%m-%d %H:%M')} KST",
        f"- 리서치 업데이트: {snapshot.get('updated_at')}",
        f"- 쿼리: {snapshot.get('query')}",
        f"- 후보종목: {', '.join(display_picks) if display_picks else '없음'}",
    ]
    if balance:
        lines.append(
            f"- 계좌현황: 총 {balance['total']} / 현금 {balance['cash']} / 보유 {balance['count']}종목"
        )
        if balance.get("top"):
            lines.append(f"- 상위보유: {balance['top']}")
    if summaries:
        lines.append(f"- 요약1: {summaries[0][:240]}")
    if len(summaries) > 1:
        lines.append(f"- 요약2: {summaries[1][:240]}")
    if knowledge_excerpt:
        compact = " ".join(knowledge_excerpt.splitlines()[:6]).strip()
        if compact:
            lines.append(f"- Knowledge: {compact[:280]}")
    return "\n".join(lines)


def _extract_payload_ticker_name_map(payload: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(payload, dict):
        return out
    pack = payload.get("pack")
    if not isinstance(pack, dict):
        return out
    seed = pack.get("advice_seed_json")
    if not isinstance(seed, dict):
        return out
    model = seed.get("model_portfolio")
    if not isinstance(model, dict):
        return out
    for row in list(model.get("targets") or []):
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip()
        name = str(row.get("name") or "").strip()
        if re.fullmatch(r"\d{6}", ticker) and name and not re.fullmatch(r"\d{6}", name):
            out[ticker] = name
    return out


def _format_pair_with_name(pair: str, ticker_name_map: dict[str, str]) -> str:
    text = str(pair or "").strip()
    if not text:
        return "-"
    ticker = text.split("/")[0].strip()
    if not re.fullmatch(r"\d{6}", ticker):
        return text
    name = str(ticker_name_map.get(ticker) or "").strip()
    if name and not re.fullmatch(r"\d{6}", name):
        return f"{name}({ticker})"
    return f"종목미상({ticker})"


def run(service_name: str = "tradecraft-research") -> None:
    runner_key = "intelligence" if service_name == "tradecraft-intelligence" else "research"
    write_current_runner_pid(runner_key)
    settings = AppSettings()
    update_interval = max(int(settings.research_run_interval_sec), 300)
    reports_interval = max(int(settings.naver_reports_interval_sec), 300)
    reports_cycle_timeout_sec = max(
        int(settings.naver_reports_cycle_timeout_sec),
        0,
    )
    state_store = RuntimeStateStore(settings.research_state_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)

    research_enabled = bool(settings.research_enabled)
    reports_enabled = _service_reports_enabled(
        service_name,
        bool(settings.naver_reports_enabled),
        research_runner_collect_reports=bool(settings.research_runner_collect_reports),
    )
    if not research_enabled and not reports_enabled:
        _write_research_disabled_snapshot(
            state_store=state_store,
            service_name=service_name,
            research_enabled=research_enabled,
            reports_enabled=reports_enabled,
        )
        logger.info(
            "%s disabled: TRADECRAFT_RESEARCH_ENABLED=false and "
            "TRADECRAFT_NAVER_REPORTS_ENABLED=false",
            service_name,
        )
        return

    telegram = TelegramBridge(
        TelegramConfig(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    )
    llm_connected = settings.codex_runtime_ready or bool(
        settings.research_codex_command.strip()
    )
    calendar = KRXHolidayCalendar()
    kis = _build_primary_kis(settings)
    report_stack = build_report_intelligence_stack(settings)
    report_repository = report_stack.repository
    rag_store = report_stack.rag_store
    portfolio_coach: PortfolioCoachService | None = None
    if settings.portfolio_coach_enabled and kis is not None:
        portfolio_llm_policy = resolve_llm_model_policy(
            settings,
            component="portfolio_coach",
        )
        portfolio_coach = PortfolioCoachService(
            PortfolioCoachConfig(
                state_db_path=settings.portfolio_coach_db_path,
                user_id=settings.portfolio_coach_user_id,
                lookback_days=settings.portfolio_coach_lookback_days,
                concentration_threshold=settings.portfolio_coach_concentration_threshold,
                max_candidates=settings.portfolio_coach_max_candidates,
                top_n=settings.portfolio_coach_top_n,
                option_count=settings.portfolio_coach_option_count,
                trigger_count=settings.portfolio_coach_trigger_count,
                time_horizon=settings.portfolio_coach_time_horizon,
                max_single_position_weight=settings.portfolio_coach_max_single_position_weight,
                max_sector_weight=settings.portfolio_coach_max_sector_weight,
                rebalance_frequency=settings.portfolio_coach_rebalance_frequency,
                risk_budget=settings.portfolio_coach_risk_budget,
                idea_filters=settings.portfolio_coach_idea_filters,
                factor_weights_json=settings.portfolio_coach_factor_weights_json,
                ticker_name_map_json=settings.portfolio_coach_ticker_name_map_json,
                review_queue_enabled=settings.portfolio_coach_review_queue_enabled,
                codex_runtime_mode=settings.codex_runtime_mode,
                codex_runtime_sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
                codex_runtime_timeout_ms=settings.codex_runtime_timeout_ms,
                llm_model=portfolio_llm_policy.model,
                llm_reasoning_effort=portfolio_llm_policy.reasoning_effort,
                llm_usage_enabled=settings.llm_usage_enabled,
                llm_usage_db_path=settings.llm_usage_db_path,
                llm_usage_component="portfolio_coach",
                **codex_native_service_config_kwargs(settings),
            ),
            holdings_provider=KISHoldingsProvider(kis),
            report_repo=report_repository,
            rag_store=rag_store,
            kis=kis,
        )
    once_mode = bool(getattr(settings, "intelligence_once", False))

    pipeline = _build_pipeline(settings) if research_enabled else None
    logger.info(
        "%s started: research=%s reports=%s state_path=%s strategy_md=%s "
        "llm_connected=%s research_interval=%ss reports_interval=%ss once=%s",
        service_name,
        research_enabled,
        reports_enabled,
        settings.research_state_path,
        settings.research_strategy_md_path,
        llm_connected,
        update_interval,
        reports_interval,
        once_mode,
    )

    cycle = 0
    report_cycle = 0
    now = datetime.now(KST)
    next_report_at: datetime | None = now if reports_enabled else None
    next_update_at: datetime | None = now if pipeline is not None else None
    next_advice_at: datetime | None = None
    label = ""
    if pipeline is not None:
        next_advice_at, label = _next_advice_slot(
            now,
            is_open_day=calendar.is_open_day,
        )
    latest_snapshot: dict | None = None
    last_report_db_updated_at = _report_db_last_updated_at(report_repository)
    if last_report_db_updated_at is None:
        last_report_db_updated_at = ""
    while True:
        now = datetime.now(KST)

        if pipeline is not None and next_update_at is not None and now >= next_update_at:
            cached_snapshot = latest_snapshot
            if cached_snapshot is None:
                cached_snapshot = pipeline.store.read_snapshot()

            current_db_updated_at = _report_db_last_updated_at(report_repository)
            should_run = _should_run_learning(
                current_db_updated_at=current_db_updated_at,
                previous_db_updated_at=last_report_db_updated_at,
                has_snapshot=cached_snapshot is not None,
                snapshot_updated_at=(cached_snapshot or {}).get("updated_at"),
                max_snapshot_age_sec=settings.research_max_age_sec,
            )
            if should_run:
                cycle += 1
                try:
                    latest_snapshot = asyncio.run(pipeline.run_once())
                    logger.info(
                        "research knowledge updated: cycle=%s items=%s db_last_updated_at=%s",
                        cycle,
                        int(latest_snapshot.get("count") or 0),
                        str(current_db_updated_at or "-"),
                    )
                except Exception as exc:
                    logger.warning("research update failed: %s", exc)
            else:
                logger.info(
                    "research update skipped: report db unchanged last_updated_at=%s",
                    str(current_db_updated_at or "-"),
                )

            if current_db_updated_at is not None:
                last_report_db_updated_at = current_db_updated_at
            next_update_at = now + timedelta(seconds=update_interval)

        if next_report_at is not None and now >= next_report_at:
            report_cycle += 1
            try:
                logger.info(
                    "report collection starting: cycle=%s timeout=%ss",
                    report_cycle,
                    reports_cycle_timeout_sec,
                )
                state_store.write_snapshot(
                    {
                        "updated_at": utc_now_iso(),
                        "source": "research_runner",
                        "service": service_name,
                        "status": "report_collection_running",
                        "reports_enabled": reports_enabled,
                        "research_enabled": research_enabled,
                        "report_cycle": report_cycle,
                        "reports_interval_sec": reports_interval,
                        "reports_cycle_timeout_sec": reports_cycle_timeout_sec,
                    }
                )
                report_result = asyncio.run(
                    run_report_collection_cycle_with_timeout(
                        run_report_collection_cycle(
                            crawler=report_stack.crawler,
                            repository=report_repository,
                            rag_store=rag_store,
                            rag_enabled=settings.rag_enabled,
                            rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
                        ),
                        timeout_sec=reports_cycle_timeout_sec,
                    )
                )
                if report_result.get("status") == "timeout":
                    logger.warning(
                        "report collection timeout: cycle=%s timeout=%ss",
                        report_cycle,
                        reports_cycle_timeout_sec,
                    )
                    state_store.write_snapshot(
                        {
                            "updated_at": utc_now_iso(),
                            "source": "research_runner",
                            "service": service_name,
                            "status": "report_collection_timeout",
                            "reports_enabled": reports_enabled,
                            "research_enabled": research_enabled,
                            "report_cycle": report_cycle,
                            "error_message": str(
                                report_result.get("error_message") or ""
                            ),
                            "reports_interval_sec": reports_interval,
                            "reports_cycle_timeout_sec": reports_cycle_timeout_sec,
                        }
                    )
                    next_report_at = now + timedelta(seconds=reports_interval)
                    continue
                report_snapshot = report_result.get("snapshot") or {}
                symbol_refresh = report_result.get("symbol_refresh") or {}
                rag_sync = report_result.get("rag_sync") or {}
                if symbol_refresh:
                    logger.info("report symbol refresh: %s", symbol_refresh)
                if rag_sync:
                    logger.info(
                        "report rag sync: status=%s synced=%s",
                        str(rag_sync.get("status") or "unknown"),
                        int(rag_sync.get("synced") or 0),
                    )
                logger.info(
                    "report collection updated: cycle=%s inserted=%s total=%s",
                    report_cycle,
                    int(report_snapshot.get("inserted") or 0),
                    int(
                        (report_snapshot.get("repository") or {}).get(
                            "total_reports"
                        )
                        or 0
                    ),
                )
                state_store.write_snapshot(
                    {
                        "updated_at": utc_now_iso(),
                        "source": "research_runner",
                        "service": service_name,
                        "status": "report_collection_ok",
                        "reports_enabled": reports_enabled,
                        "research_enabled": research_enabled,
                        "report_cycle": report_cycle,
                        "snapshot": report_snapshot,
                        "symbol_refresh": symbol_refresh,
                        "rag_sync": rag_sync,
                        "reports_interval_sec": reports_interval,
                        "reports_cycle_timeout_sec": reports_cycle_timeout_sec,
                    }
                )
                refreshed_db_updated_at = _report_db_last_updated_at(report_repository)
                if (
                    pipeline is not None
                    and refreshed_db_updated_at is not None
                    and refreshed_db_updated_at != last_report_db_updated_at
                ):
                    next_update_at = datetime.now(KST)
                    logger.info(
                        "research update scheduled: report db changed last_updated_at=%s",
                        refreshed_db_updated_at,
                    )
            except Exception as exc:
                logger.warning("report collection failed: %s", exc)
                state_store.write_snapshot(
                    {
                        "updated_at": utc_now_iso(),
                        "source": "research_runner",
                        "service": service_name,
                        "status": "report_collection_error",
                        "reports_enabled": reports_enabled,
                        "research_enabled": research_enabled,
                        "report_cycle": report_cycle,
                        "error_message": str(exc),
                        "reports_interval_sec": reports_interval,
                        "reports_cycle_timeout_sec": reports_cycle_timeout_sec,
                    }
                )
            next_report_at = now + timedelta(seconds=reports_interval)

        if pipeline is not None and next_advice_at is not None and now >= next_advice_at:
            snapshot = latest_snapshot
            if snapshot is None:
                snapshot = pipeline.store.read_snapshot()
            if snapshot is None:
                try:
                    snapshot = asyncio.run(pipeline.run_once())
                    latest_snapshot = snapshot
                except Exception as exc:
                    logger.warning(
                        "research advice skipped: snapshot unavailable (%s)", exc
                    )
                    snapshot = None

            if snapshot is not None:
                balance_summary: dict[str, str] | None = None
                if kis is not None:
                    try:
                        assets = asyncio.run(kis.fetch_balance_assets())
                        balance_summary = _summarize_balance(assets)
                    except Exception as exc:
                        logger.warning("research advice balance fetch failed: %s", exc)

                knowledge_excerpt = _read_knowledge_excerpt(
                    settings.research_strategy_md_path,
                    settings.research_advice_context_max_chars,
                )

                if telegram.config.ready:
                    message = ""
                    payload: dict[str, Any] | None = None
                    if portfolio_coach is not None:
                        payload = asyncio.run(portfolio_coach.build_advice())
                        message = str(payload.get("message") or "").strip()
                        status = str(payload.get("status") or "unknown")
                        reason = str(payload.get("reason") or "").strip()
                        llm_status = (
                            payload.get("llm_status")
                            if isinstance(payload.get("llm_status"), dict)
                            else {}
                        )
                        used = list(payload.get("used_candidates") or [])
                        message_id = int(payload.get("message_id") or 0)
                        logger.info(
                            "portfolio coach status=%s message_id=%s candidates=%s reason=%s llm_status=%s llm_reason=%s llm_model=%s",
                            status,
                            message_id,
                            len(used),
                            reason,
                            str(llm_status.get("status") or ""),
                            str(llm_status.get("reason") or ""),
                            str(llm_status.get("model") or ""),
                        )
                        if status == "pending_review":
                            logger.info(
                                "portfolio coach pending_review auto-approved for scheduled send"
                            )
                    if not message:
                        pick_name_map = _resolve_pick_name_map(
                            snapshot=snapshot,
                            report_repository=report_repository,
                            kis=kis,
                        )
                        message = _build_advice_message(
                            snapshot,
                            label=label,
                            scheduled_at=next_advice_at,
                            balance=balance_summary,
                            knowledge_excerpt=knowledge_excerpt,
                            pick_name_map=pick_name_map,
                        )
                        payload = None

                    if message:
                        sent = asyncio.run(telegram.send_message(message))
                        sent_ok = bool(sent.get("ok"))
                        if sent_ok:
                            logger.info(
                                "research advice sent; KIS block manager consumes research DB/wiki directly"
                            )
                        if portfolio_coach is not None and payload is not None:
                            portfolio_coach.mark_sent(
                                payload, status="sent" if sent_ok else "failed"
                            )
                        logger.info("research advice telegram sent=%s", sent_ok)
                    else:
                        logger.info("research advice queued for review (no auto-send)")
                else:
                    logger.info(
                        "research advice telegram skipped: telegram config missing"
                    )

            next_advice_at, label = _next_advice_slot(
                now + timedelta(seconds=1),
                is_open_day=calendar.is_open_day,
            )

        if once_mode:
            logger.info("%s once mode completed", service_name)
            return

        wakeups = [
            value
            for value in (next_report_at, next_update_at, next_advice_at)
            if value is not None
        ]
        if not wakeups:
            logger.info("%s has no active schedules", service_name)
            return
        sleep_sec = max(
            int(min(wakeups).timestamp() - datetime.now(KST).timestamp()),
            1,
        )
        sleep_sec = min(sleep_sec, 60)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    run()
