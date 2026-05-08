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
from tradecraft.services.intelligence import (
    build_report_intelligence_stack,
    run_report_collection_cycle,
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


def _build_pipeline(settings: AppSettings) -> ResearchPipeline:
    config = ResearchPipelineConfig(
        state_path=settings.research_state_path,
        strategy_md_path=settings.research_strategy_md_path,
        market_scope=settings.research_market_scope,
        codex_command=settings.research_codex_command,
        codex_query=settings.research_codex_query,
        codex_timeout_sec=settings.research_codex_timeout_sec,
        report_urls=settings.research_report_url_list,
        trader_state_path=settings.kis_trader_state_path,
        report_db_path=settings.naver_reports_db_path,
        report_db_top_k=settings.research_db_reference_top_k,
        rag_enabled=settings.rag_enabled,
        rag_persist_path=settings.rag_persist_path,
        rag_collection_name=settings.rag_collection_name,
        rag_query_top_k=settings.rag_query_top_k,
        max_items=settings.research_max_items,
        knowledge_max_chars=settings.research_knowledge_max_chars,
        llm_bridge_command=settings.llm_bridge_command,
        llm_bridge_args=settings.llm_bridge_args,
        llm_bridge_url=settings.llm_bridge_url,
        llm_bridge_token=settings.llm_bridge_token,
        llm_bridge_timeout_ms=settings.llm_bridge_timeout_ms,
        llm_model=settings.llm_model,
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


def _parse_krw_amount(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    text = str(value or "").strip()
    if not text:
        return 0.0
    normalized = re.sub(r"[^0-9.\-]", "", text)
    if not normalized:
        return 0.0
    return max(_safe_float(normalized), 0.0)


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
                pass

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


def _sync_kis_trader_targets_from_morning_advice(
    *,
    snapshot: dict[str, Any],
    label: str,
    scheduled_at: datetime,
    trader_state_path: str,
    max_symbols: int,
) -> list[str]:
    if str(label).strip() != "장전":
        return []
    path = str(trader_state_path or "").strip()
    if not path:
        return []
    picks = _extract_pick_codes(snapshot, limit=max_symbols)
    if len(picks) < max_symbols:
        for item in list(snapshot.get("items") or []):
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "")
            for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", summary):
                if code in picks:
                    continue
                picks.append(code)
                if len(picks) >= max_symbols:
                    break
            if len(picks) >= max_symbols:
                break
    if not picks:
        return []

    store = RuntimeStateStore(path)
    payload = store.read_snapshot() or {}
    if not isinstance(payload, dict):
        payload = {}
    payload["target_symbols"] = picks
    payload["target_symbols_updated_at"] = utc_now_iso()
    payload["target_symbols_source"] = "research_runner_morning_advice"
    payload["target_symbols_as_of"] = scheduled_at.astimezone(KST).strftime(
        "%Y-%m-%d %H:%M KST"
    )
    store.write_snapshot(payload)
    return picks


def _extract_rebalance_target_weights_from_payload(
    payload: dict[str, Any] | None,
    *,
    max_symbols: int,
) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    pack = payload.get("pack")
    if not isinstance(pack, dict):
        return {}
    seed = pack.get("advice_seed_json")
    if not isinstance(seed, dict):
        return {}

    model = seed.get("model_portfolio")
    rows = []
    if isinstance(model, dict):
        rows = [
            item for item in list(model.get("targets") or []) if isinstance(item, dict)
        ]

    if not rows:
        action_plan = seed.get("action_plan")
        if isinstance(action_plan, dict):
            rows = [
                item
                for item in list(action_plan.get("rebalance_table_rows") or [])
                if isinstance(item, dict)
            ]

    out: dict[str, float] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip()
        if not re.fullmatch(r"\d{6}", ticker):
            continue
        target = _safe_float(row.get("target_weight"))
        if target <= 0:
            continue
        out[ticker] = max(min(target, 1.0), 0.0)
        if len(out) >= max(max_symbols, 1):
            break
    return out


def _extract_target_cash_weight_from_payload(
    payload: dict[str, Any] | None,
) -> float | None:
    if not isinstance(payload, dict):
        return None
    pack = payload.get("pack")
    if not isinstance(pack, dict):
        return None
    seed = pack.get("advice_seed_json")
    if not isinstance(seed, dict):
        return None

    strategy_spec = seed.get("strategy_spec")
    if isinstance(strategy_spec, dict):
        weight = _safe_float(strategy_spec.get("target_cash_weight"))
        if 0.0 <= weight < 1.0:
            return round(weight, 6)

    model = seed.get("model_portfolio")
    if isinstance(model, dict):
        weight = _safe_float(model.get("target_cash_weight"))
        if 0.0 <= weight < 1.0:
            return round(weight, 6)

    return None


def _extract_total_value_krw_from_payload(payload: dict[str, Any] | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    pack = payload.get("pack")
    if not isinstance(pack, dict):
        return 0.0
    seed = pack.get("advice_seed_json")
    if not isinstance(seed, dict):
        return 0.0
    portfolio = seed.get("portfolio")
    if not isinstance(portfolio, dict):
        return 0.0

    numeric = _safe_float(portfolio.get("total_value_krw"))
    if numeric > 0:
        return numeric

    for key in ("total_krw", "total"):
        parsed = _parse_krw_amount(portfolio.get(key))
        if parsed > 0:
            return parsed
    return 0.0


def _sync_kis_rebalance_targets_to_trader_state(
    *,
    payload: dict[str, Any] | None,
    snapshot: dict[str, Any] | None = None,
    trader_state_path: str,
    max_symbols: int,
) -> dict[str, float]:
    targets = _extract_rebalance_target_weights_from_payload(
        payload,
        max_symbols=max_symbols,
    )
    target_cash_weight = _extract_target_cash_weight_from_payload(payload)
    investable_ratio = 1.0
    if target_cash_weight is not None:
        investable_ratio = max(1.0 - target_cash_weight, 0.0)
    portfolio_total_krw = _extract_total_value_krw_from_payload(payload)

    pick_limit = max(max_symbols, 1)
    if isinstance(snapshot, dict):
        pick_codes = _extract_pick_codes(snapshot, limit=pick_limit)
        if pick_codes:
            missing = [code for code in pick_codes if code not in targets]
            if missing:
                if targets:
                    base_weight = min(max(min(targets.values()) * 0.5, 0.03), 0.08)
                    for code in missing:
                        targets[code] = base_weight
                else:
                    equal_weight = investable_ratio / float(len(missing))
                    for code in missing:
                        targets[code] = equal_weight

    path = str(trader_state_path or "").strip()
    if not path:
        return {}

    store = RuntimeStateStore(path)
    trader_payload = store.read_snapshot() or {}
    if not isinstance(trader_payload, dict):
        trader_payload = {}

    if target_cash_weight is None:
        existing = _safe_float(trader_payload.get("target_cash_weight"))
        if 0.0 <= existing < 1.0:
            target_cash_weight = round(existing, 6)
            investable_ratio = max(1.0 - target_cash_weight, 0.0)

    if not targets:
        existing_weights = trader_payload.get("target_weights")
        if isinstance(existing_weights, dict):
            recovered: dict[str, float] = {}
            for raw_ticker, raw_weight in existing_weights.items():
                ticker = str(raw_ticker or "").strip()
                if len(ticker) != 6 or not ticker.isdigit():
                    continue
                weight = _safe_float(raw_weight)
                if weight <= 0:
                    continue
                recovered[ticker] = weight
                if len(recovered) >= pick_limit:
                    break
            targets = recovered

    if targets:
        sorted_targets = sorted(targets.items(), key=lambda item: item[1], reverse=True)
        targets = dict(sorted_targets[:pick_limit])
        total_weight = sum(float(weight) for weight in targets.values())
        if total_weight > investable_ratio and investable_ratio >= 0.0:
            scale = (investable_ratio / total_weight) if total_weight > 0 else 0.0
            targets = {
                ticker: round(float(weight) * scale, 6)
                for ticker, weight in targets.items()
            }

    if not targets:
        return {}

    trader_payload["target_weights"] = {
        ticker: round(weight, 6) for ticker, weight in targets.items()
    }
    trader_payload["target_symbols"] = list(targets.keys())
    if target_cash_weight is not None:
        trader_payload["target_cash_weight"] = round(target_cash_weight, 6)
    if portfolio_total_krw > 0:
        trader_payload["portfolio_total_krw"] = round(portfolio_total_krw, 2)
    now_iso = utc_now_iso()
    trader_payload["target_weights_updated_at"] = now_iso
    trader_payload["target_symbols_updated_at"] = now_iso
    trader_payload["target_weights_source"] = "research_runner_portfolio_coach"
    store.write_snapshot(trader_payload)
    return targets


def run(service_name: str = "tradecraft-research") -> None:
    runner_key = "intelligence" if service_name == "tradecraft-intelligence" else "research"
    write_current_runner_pid(runner_key)
    settings = AppSettings()
    update_interval = max(int(settings.research_run_interval_sec), 300)
    reports_interval = max(int(settings.naver_reports_interval_sec), 300)
    telegram = TelegramBridge(
        TelegramConfig(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    )
    llm_connected = settings.llm_bridge_ready or bool(
        settings.research_codex_command.strip()
    )
    calendar = KRXHolidayCalendar()
    kis = _build_primary_kis(settings)
    report_stack = build_report_intelligence_stack(settings)
    report_repository = report_stack.repository
    rag_store = report_stack.rag_store
    portfolio_coach: PortfolioCoachService | None = None
    if settings.portfolio_coach_enabled and kis is not None:
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
                llm_bridge_command=settings.llm_bridge_command,
                llm_bridge_args=settings.llm_bridge_args,
                llm_bridge_url=settings.llm_bridge_url,
                llm_bridge_token=settings.llm_bridge_token,
                llm_bridge_timeout_ms=settings.llm_bridge_timeout_ms,
                llm_model=settings.llm_model,
            ),
            holdings_provider=KISHoldingsProvider(kis),
            report_repo=report_repository,
            rag_store=rag_store,
            kis=kis,
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)

    research_enabled = bool(settings.research_enabled)
    reports_enabled = bool(settings.naver_reports_enabled)
    once_mode = bool(getattr(settings, "intelligence_once", False))
    if not research_enabled and not reports_enabled:
        logger.info(
            "%s disabled: TRADECRAFT_RESEARCH_ENABLED=false and "
            "TRADECRAFT_NAVER_REPORTS_ENABLED=false",
            service_name,
        )
        return

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
                report_result = asyncio.run(
                    run_report_collection_cycle(
                        crawler=report_stack.crawler,
                        repository=report_repository,
                        rag_store=rag_store,
                        rag_enabled=settings.rag_enabled,
                        rag_sync_chunk_limit=settings.rag_sync_chunk_limit,
                    )
                )
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
                        used = list(payload.get("used_candidates") or [])
                        message_id = int(payload.get("message_id") or 0)
                        logger.info(
                            "portfolio coach status=%s message_id=%s candidates=%s reason=%s",
                            status,
                            message_id,
                            len(used),
                            reason,
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
                        did_symbol_sync = False
                        rebalance_targets: dict[str, float] = {}
                        execution_result: dict[str, Any] = {}
                        if sent_ok:
                            synced = _sync_kis_trader_targets_from_morning_advice(
                                snapshot=snapshot,
                                label=label,
                                scheduled_at=next_advice_at,
                                trader_state_path=settings.kis_trader_state_path,
                                max_symbols=settings.kis_trader_max_candidate_codes,
                            )
                            if synced:
                                did_symbol_sync = True
                                logger.info(
                                    "kis trader target symbols synced from morning advice: %s",
                                    ",".join(synced),
                                )
                            rebalance_targets = (
                                _sync_kis_rebalance_targets_to_trader_state(
                                    payload=payload,
                                    snapshot=snapshot,
                                    trader_state_path=settings.kis_trader_state_path,
                                    max_symbols=settings.kis_trader_max_candidate_codes,
                                )
                            )
                            if rebalance_targets:
                                logger.info(
                                    "kis rebalance targets synced: %s",
                                    ",".join(
                                        f"{ticker}:{weight:.3f}"
                                        for ticker, weight in rebalance_targets.items()
                                    ),
                                )
                        if did_symbol_sync or rebalance_targets:
                            logger.info(
                                "kis direct trader state updated after advice: symbols=%s weights=%s",
                                did_symbol_sync,
                                bool(rebalance_targets),
                            )
                        if sent_ok and execution_result:
                            try:
                                executed_rows = list(
                                    execution_result.get("executed") or []
                                )
                                failed_rows = list(execution_result.get("failed") or [])
                                ticker_name_map = _extract_payload_ticker_name_map(
                                    payload
                                )
                                pair_tickers = [
                                    str(row.get("pair") or "").split("/")[0].strip()
                                    for row in (executed_rows + failed_rows)
                                ]
                                resolved_map = _resolve_symbol_names_for_codes(
                                    codes=pair_tickers,
                                    report_repository=report_repository,
                                    kis=kis,
                                    initial_map=ticker_name_map,
                                )
                                ticker_name_map.update(resolved_map)
                                notice_lines = [
                                    f"[Portfolio Coach Execution] {label} {next_advice_at.strftime('%Y-%m-%d %H:%M')} (KST)",
                                    f"- status: {execution_result.get('status')}",
                                    f"- attempted: {execution_result.get('attempted')}",
                                    f"- executed: {len(executed_rows)}",
                                    f"- failed: {len(failed_rows)}",
                                ]
                                target_invested = _safe_float(
                                    execution_result.get("target_invested_ratio")
                                )
                                target_cash = _safe_float(
                                    execution_result.get("target_cash_weight")
                                )
                                notice_lines.append(
                                    "- invested ratio target: "
                                    f"{target_invested*100:.1f}% (cash {target_cash*100:.1f}%)"
                                )
                                actual_invested_raw = execution_result.get(
                                    "actual_invested_ratio"
                                )
                                actual_cash_raw = execution_result.get(
                                    "actual_cash_ratio"
                                )
                                if (
                                    actual_invested_raw is not None
                                    or actual_cash_raw is not None
                                ):
                                    actual_invested = (
                                        _safe_float(actual_invested_raw)
                                        if actual_invested_raw is not None
                                        else max(
                                            1.0 - _safe_float(actual_cash_raw), 0.0
                                        )
                                    )
                                    actual_cash = (
                                        _safe_float(actual_cash_raw)
                                        if actual_cash_raw is not None
                                        else max(1.0 - actual_invested, 0.0)
                                    )
                                    notice_lines.append(
                                        "- invested ratio actual: "
                                        f"{actual_invested*100:.1f}% (cash {actual_cash*100:.1f}%)"
                                    )
                                ratio_base_krw = _safe_float(
                                    execution_result.get("allocation_ratio_base_krw")
                                )
                                open_stake_total_krw = _safe_float(
                                    execution_result.get("open_stake_total_krw")
                                )
                                if ratio_base_krw > 0 and open_stake_total_krw >= 0:
                                    notice_lines.append(
                                        "- ratio basis: "
                                        f"open_stake {int(open_stake_total_krw):,} / base {int(ratio_base_krw):,} KRW"
                                    )
                                for row in executed_rows[:3]:
                                    pair = str(row.get("pair") or "")
                                    display = _format_pair_with_name(
                                        pair, ticker_name_map
                                    )
                                    notice_lines.append(
                                        f"- filled: {display} / stake {int(_safe_float(row.get('stakeamount'))):,} KRW"
                                    )
                                if failed_rows:
                                    first = failed_rows[0]
                                    fail_pair = str(first.get("pair") or "")
                                    fail_display = _format_pair_with_name(
                                        fail_pair, ticker_name_map
                                    )
                                    notice_lines.append(
                                        f"- fail_sample: {fail_display} / {str(first.get('reason') or '')[:120]}"
                                    )
                                asyncio.run(
                                    telegram.send_message("\n".join(notice_lines))
                                )
                            except Exception:
                                pass
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
