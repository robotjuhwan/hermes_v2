from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timedelta
from datetime import date as date_type
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from tradecraft.config import AppSettings
from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
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
from tradecraft.services.rag_store import RAGStore, RAGStoreConfig
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


def _extract_trade_id_from_forceenter_error(reason: str) -> int:
    text = str(reason or "")
    match = re.search(r"already open - id:\s*(\d+)", text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


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
    freqtrade_runtime_dir: str = "",
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

    runtime_dir = str(freqtrade_runtime_dir or "").strip()
    if runtime_dir:
        override_path = Path(runtime_dir) / "kis.override.json"
        override_payload: dict[str, Any] = {}
        try:
            if override_path.exists():
                loaded = json.loads(override_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    override_payload = loaded
        except Exception:
            override_payload = {}

        exchange_payload = override_payload.get("exchange")
        if not isinstance(exchange_payload, dict):
            exchange_payload = {}
        exchange_payload["pair_whitelist"] = [f"{code}/KRW" for code in picks]
        override_payload["exchange"] = exchange_payload
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text(
            json.dumps(override_payload, ensure_ascii=True),
            encoding="utf-8",
        )
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


def _extract_balance_totals(
    balance_payload: dict[str, Any] | None,
) -> tuple[float, float]:
    if not isinstance(balance_payload, dict):
        return 0.0, 0.0

    total = _safe_float(balance_payload.get("total") or balance_payload.get("value"))
    krw_free = 0.0

    currencies = balance_payload.get("currencies")
    if isinstance(currencies, list):
        for row in currencies:
            if not isinstance(row, dict):
                continue
            if str(row.get("currency") or "").upper() != "KRW":
                continue
            krw_free = max(
                _safe_float(row.get("free") or row.get("balance") or row.get("value")),
                0.0,
            )
            if total <= 0:
                total = max(_safe_float(row.get("balance") or row.get("free")), 0.0)
            break

    return max(total, 0.0), krw_free


def _sum_open_stake_amount(status_rows: list[dict[str, Any]]) -> float:
    open_stake = 0.0
    for row in status_rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("is_open", True)):
            continue
        open_stake += max(_safe_float(row.get("stake_amount")), 0.0)
    return open_stake


def _sync_kis_rebalance_targets_to_freqtrade_override(
    *,
    payload: dict[str, Any] | None,
    snapshot: dict[str, Any] | None = None,
    runtime_dir: str,
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

    path = str(runtime_dir or "").strip()
    if not path:
        return {}

    override_path = Path(path) / "kis.override.json"
    override_payload: dict[str, Any] = {}
    try:
        if override_path.exists():
            loaded = json.loads(override_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                override_payload = loaded
    except Exception:
        override_payload = {}

    if target_cash_weight is None:
        existing = _safe_float(override_payload.get("tradecraft_target_cash_weight"))
        if 0.0 <= existing < 1.0:
            target_cash_weight = round(existing, 6)
            investable_ratio = max(1.0 - target_cash_weight, 0.0)

    if not targets:
        existing_weights = override_payload.get("tradecraft_target_weights")
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

    override_payload["tradecraft_target_weights"] = {
        ticker: round(weight, 6) for ticker, weight in targets.items()
    }
    if target_cash_weight is not None:
        override_payload["tradecraft_target_cash_weight"] = round(target_cash_weight, 6)
    if portfolio_total_krw > 0:
        override_payload["tradecraft_portfolio_total_krw"] = round(
            portfolio_total_krw, 2
        )
    override_payload["tradecraft_target_weights_updated_at"] = utc_now_iso()
    override_payload["force_entry_enable"] = True

    exchange_payload = override_payload.get("exchange")
    if not isinstance(exchange_payload, dict):
        exchange_payload = {}
    target_pairs = [f"{ticker}/KRW" for ticker in targets.keys()]
    existing_pairs = [
        str(pair).strip()
        for pair in list(exchange_payload.get("pair_whitelist") or [])
        if str(pair).strip()
    ]
    merged_pairs = list(dict.fromkeys(target_pairs + existing_pairs))
    exchange_payload["pair_whitelist"] = merged_pairs[
        : max(pick_limit, len(target_pairs))
    ]
    override_payload["exchange"] = exchange_payload

    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(
        json.dumps(override_payload, ensure_ascii=True),
        encoding="utf-8",
    )
    return targets


def _extract_reduce_tickers_from_payload(payload: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    if not isinstance(payload, dict):
        return out
    pack = payload.get("pack")
    if not isinstance(pack, dict):
        return out
    seed = pack.get("advice_seed_json")
    if not isinstance(seed, dict):
        return out
    action_plan = seed.get("action_plan")
    if not isinstance(action_plan, dict):
        return out
    for row in list(action_plan.get("rebalance_table_rows") or []):
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").upper().strip()
        if action not in {"REDUCE", "SELL"}:
            continue
        ticker = str(row.get("ticker") or "").strip()
        if re.fullmatch(r"\d{6}", ticker):
            out.add(ticker)
    return out


async def _execute_kis_forcexy_target_orders(
    *,
    settings: AppSettings,
    payload: dict[str, Any] | None,
    rebalance_targets: dict[str, float],
) -> dict[str, Any]:
    if not rebalance_targets:
        return {"status": "skipped", "reason": "empty_targets"}

    bridge = FreqtradeBridge(
        FreqtradeBridgeConfig(
            bots=[
                FreqtradeBotConfig(
                    bot_id="kis",
                    label="Freqtrade KIS",
                    config_path="third_party/freqtrade/user_data/config_kis_jurobot.json",
                )
            ],
            timeout_sec=4.0,
            bot_api_url_overrides=settings.freqtrade_bot_api_url_map,
        )
    )
    resolved = bridge._resolve_bot_config(bridge.config.bots[0])
    if resolved is None:
        return {"status": "skipped", "reason": "kis_api_not_configured"}

    reduce_tickers = _extract_reduce_tickers_from_payload(payload)
    buy_tickers = [
        ticker
        for ticker, _ in sorted(
            rebalance_targets.items(), key=lambda item: item[1], reverse=True
        )
        if ticker not in reduce_tickers
    ]
    target_cash_weight = _extract_target_cash_weight_from_payload(payload)
    target_invested_ratio = min(
        max(sum(float(weight) for weight in rebalance_targets.values()), 0.0), 1.0
    )
    if target_cash_weight is not None:
        target_invested_ratio = max(1.0 - target_cash_weight, 0.0)
    target_cash_weight_effective = max(1.0 - target_invested_ratio, 0.0)

    max_orders = max(int(settings.kis_trader_max_orders_per_cycle), 1)
    buy_tickers = buy_tickers[:max_orders]
    early_reason = ""
    if not buy_tickers:
        early_reason = "no_buy_targets"

    total_value_krw = _extract_total_value_krw_from_payload(payload)

    max_budget = max(float(settings.kis_trader_max_budget_per_order_krw), 0.0)
    min_budget = max(
        float(getattr(settings, "portfolio_coach_min_trade_krw", 10000)), 10000.0
    )
    status_url = f"{resolved.api_url}/api/v1/status"
    balance_url = f"{resolved.api_url}/api/v1/balance"
    pair_candles_url = f"{resolved.api_url}/api/v1/pair_candles"
    forceenter_url = f"{resolved.api_url}/api/v1/forceenter"
    trades_url = f"{resolved.api_url}/api/v1/trades"
    auth = bridge._auth_tuple(resolved.username, resolved.password)
    request_kwargs: dict[str, Any] = {"headers": {"Accept": "application/json"}}
    if auth is not None:
        request_kwargs["auth"] = auth

    executed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    final_status_rows: list[dict[str, Any]] = []
    balance_payload: dict[str, Any] | None = None
    timeout = httpx.Timeout(4.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        ready_tickers: list[str] = list(buy_tickers)
        for ticker in list(buy_tickers):
            pair = f"{ticker}/KRW"
            try:
                candles_res = await client.get(
                    pair_candles_url,
                    params={"pair": pair, "timeframe": "5m", "limit": 20},
                    **request_kwargs,
                )
                candles_res.raise_for_status()
                candles_payload = candles_res.json()
                candles = (
                    candles_payload.get("data")
                    if isinstance(candles_payload, dict)
                    else []
                )
                if len(list(candles or [])) == 0:
                    logger.warning(
                        "pair_candles empty; continuing with forceenter: %s",
                        pair,
                    )
            except Exception:
                logger.warning(
                    "pair_candles check failed; continuing with forceenter: %s",
                    pair,
                )
                continue

        buy_tickers = ready_tickers
        if not buy_tickers:
            early_reason = "no_candle_ready_targets"

        try:
            status_res = await client.get(status_url, **request_kwargs)
            status_res.raise_for_status()
            status_payload = status_res.json()
            status_rows = [
                row for row in list(status_payload or []) if isinstance(row, dict)
            ]
            open_pairs = {str(row.get("pair") or "").strip() for row in status_rows}
            final_status_rows = status_rows
        except Exception:
            status_rows = []
            open_pairs = set()

        for ticker in buy_tickers:
            pair = f"{ticker}/KRW"
            if pair in open_pairs:
                continue
            target_weight = float(rebalance_targets.get(ticker) or 0.0)
            stake_amount = max_budget
            if total_value_krw > 0 and target_weight > 0:
                stake_amount = min(
                    max_budget, max(min_budget, total_value_krw * target_weight)
                )
            if stake_amount <= 0:
                continue
            body = {
                "pair": pair,
                "side": "long",
                "ordertype": "market",
                "stakeamount": round(stake_amount, 2),
                "entry_tag": "pc_target_rebalance",
            }
            retry_stakes = [
                round(stake_amount, 2),
                round(max(stake_amount, 200000.0), 2),
                round(max(stake_amount, 400000.0), 2),
            ]
            retry_stakes = list(dict.fromkeys(retry_stakes))

            last_reason = ""
            done = False
            stale_trade_reset_done = False
            for retry_stake in retry_stakes:
                body["stakeamount"] = retry_stake
                try:
                    response = await client.post(
                        forceenter_url, json=body, **request_kwargs
                    )
                    response.raise_for_status()
                    payload_json = response.json()
                    executed.append(
                        {
                            "ticker": ticker,
                            "pair": pair,
                            "stakeamount": retry_stake,
                            "trade_id": payload_json.get("trade_id"),
                        }
                    )
                    done = True
                    break
                except Exception as exc:
                    last_reason = str(exc)
                    trade_id = _extract_trade_id_from_forceenter_error(last_reason)
                    if trade_id > 0 and not stale_trade_reset_done:
                        try:
                            drop_res = await client.delete(
                                f"{trades_url}/{trade_id}",
                                **request_kwargs,
                            )
                            drop_res.raise_for_status()
                            stale_trade_reset_done = True
                            logger.warning(
                                "dropped stale open trade during forceenter retry: pair=%s trade_id=%s",
                                pair,
                                trade_id,
                            )
                            continue
                        except Exception:
                            pass
                    continue

            if not done:
                failed.append(
                    {
                        "ticker": ticker,
                        "pair": pair,
                        "reason": last_reason,
                    }
                )

        try:
            status_res = await client.get(status_url, **request_kwargs)
            status_res.raise_for_status()
            status_payload = status_res.json()
            final_status_rows = [
                row for row in list(status_payload or []) if isinstance(row, dict)
            ]
        except Exception:
            pass

        try:
            balance_res = await client.get(balance_url, **request_kwargs)
            balance_res.raise_for_status()
            payload_json = balance_res.json()
            if isinstance(payload_json, dict):
                balance_payload = payload_json
        except Exception:
            pass

    open_stake_total_krw = _sum_open_stake_amount(final_status_rows)
    balance_total_krw, balance_krw_free_krw = _extract_balance_totals(balance_payload)
    ratio_base_krw = total_value_krw if total_value_krw > 0 else balance_total_krw

    actual_invested_ratio: float | None = None
    if ratio_base_krw > 0:
        actual_invested_ratio = min(
            max(open_stake_total_krw / ratio_base_krw, 0.0), 1.0
        )
    elif balance_total_krw > 0:
        actual_invested_ratio = min(
            max(1.0 - (balance_krw_free_krw / balance_total_krw), 0.0), 1.0
        )

    actual_cash_ratio: float | None = None
    if actual_invested_ratio is not None:
        actual_cash_ratio = max(1.0 - actual_invested_ratio, 0.0)

    ratio_metrics: dict[str, Any] = {
        "target_invested_ratio": round(target_invested_ratio, 6),
        "target_cash_weight": round(target_cash_weight_effective, 6),
        "actual_invested_ratio": (
            round(actual_invested_ratio, 6)
            if actual_invested_ratio is not None
            else None
        ),
        "actual_cash_ratio": round(actual_cash_ratio, 6)
        if actual_cash_ratio is not None
        else None,
        "allocation_ratio_base_krw": round(ratio_base_krw, 2),
        "open_stake_total_krw": round(open_stake_total_krw, 2),
    }
    logger.info(
        "kis forcexy allocation ratios: target_invested=%s actual_invested=%s target_cash=%s actual_cash=%s base_krw=%.0f open_stake_krw=%.0f",
        f"{target_invested_ratio*100:.1f}%",
        (
            f"{actual_invested_ratio*100:.1f}%"
            if actual_invested_ratio is not None
            else "n/a"
        ),
        f"{target_cash_weight_effective*100:.1f}%",
        f"{actual_cash_ratio*100:.1f}%" if actual_cash_ratio is not None else "n/a",
        ratio_base_krw,
        open_stake_total_krw,
    )

    if executed:
        return {
            "status": "ok",
            "attempted": len(buy_tickers),
            "executed": executed,
            "failed": failed,
            **ratio_metrics,
        }
    return {
        "status": "skipped",
        "reason": early_reason or "forceenter_not_executed",
        "attempted": len(buy_tickers),
        "failed": failed,
        **ratio_metrics,
    }


def _restart_kis_freqtrade_if_running(settings: AppSettings) -> dict[str, Any]:
    def _is_live_pid(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "state="],
                text=True,
            ).strip()
        except Exception:
            return False
        if not out:
            return False
        state = out[:1].upper()
        return state != "Z"

    def _list_live_kis_pids() -> list[int]:
        out: list[int] = []
        try:
            ps_out = subprocess.check_output(
                ["ps", "-ax", "-o", "pid=,command="],
                text=True,
            )
        except Exception:
            return out
        for line in ps_out.splitlines():
            text = str(line or "").strip()
            if "freqtrade trade" not in text:
                continue
            if "config_kis_jurobot.json" not in text:
                continue
            parts = text.split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if _is_live_pid(pid):
                out.append(pid)
        return sorted(set(out))

    def _terminate_pids(pids: list[int]) -> None:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                continue
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            alive = [pid for pid in pids if _is_live_pid(pid)]
            if not alive:
                return
            time.sleep(0.1)

    manager = FreqtradeProcessManager(
        FreqtradeProcessManagerConfig(
            executable_path=settings.freqtrade_executable_path,
            workdir=settings.freqtrade_workdir,
            runtime_dir=settings.freqtrade_runtime_dir,
            stop_timeout_sec=settings.freqtrade_stop_timeout_sec,
        ),
        bots=[
            FreqtradeBotConfig(
                bot_id="kis",
                label="Freqtrade KIS",
                config_path="third_party/freqtrade/user_data/config_kis_jurobot.json",
            )
        ],
    )
    live_pids = _list_live_kis_pids()
    stopped_external_pids: list[int] = []
    if len(live_pids) >= 2:
        stopped_external_pids = list(live_pids)
        _terminate_pids(live_pids)

    status = manager.list_statuses()[0]
    if not bool(status.get("running")):
        fresh_live_pids = _list_live_kis_pids()
        if fresh_live_pids:
            stopped_external_pids = list(
                sorted(set(stopped_external_pids + fresh_live_pids))
            )
            _terminate_pids(fresh_live_pids)

    stop_action = manager.stop("kis")
    start_action = manager.start("kis")
    return {
        "status": "ok",
        "stop_action": stop_action.get("action"),
        "start_action": start_action.get("action"),
        "pid": start_action.get("pid"),
        "stopped_external_pids": stopped_external_pids,
    }


def _reload_kis_freqtrade_config(settings: AppSettings) -> dict[str, Any]:
    config_path = Path("third_party/freqtrade/user_data/config_kis_jurobot.json")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "reason": f"config_read_failed: {exc}"}
    api_server = payload.get("api_server") if isinstance(payload, dict) else None
    if not isinstance(api_server, dict):
        return {"status": "skipped", "reason": "api_server_missing"}
    port = int(api_server.get("listen_port") or 0)
    username = str(api_server.get("username") or "").strip()
    password = str(api_server.get("password") or "").strip()
    if port <= 0:
        return {"status": "skipped", "reason": "api_port_missing"}
    api_url = f"http://127.0.0.1:{port}"
    headers = {"Accept": "application/json"}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": httpx.Timeout(3.5)}
    if username and password:
        kwargs["auth"] = (username, password)
    try:
        with httpx.Client() as client:
            res = client.post(f"{api_url}/api/v1/reload_config", **kwargs)
            res.raise_for_status()
            body = res.json() if res.content else {}
        return {"status": "ok", "api_url": api_url, "body": body}
    except Exception as exc:
        return {"status": "error", "api_url": api_url, "reason": str(exc)}


def _probe_kis_freqtrade_api(settings: AppSettings) -> dict[str, Any]:
    config_path = Path("third_party/freqtrade/user_data/config_kis_jurobot.json")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "reason": f"config_read_failed: {exc}"}

    api_server = payload.get("api_server") if isinstance(payload, dict) else None
    if not isinstance(api_server, dict):
        return {"status": "error", "reason": "api_server_missing"}

    port = int(api_server.get("listen_port") or 0)
    username = str(api_server.get("username") or "").strip()
    password = str(api_server.get("password") or "").strip()
    if port <= 0:
        return {"status": "error", "reason": "api_port_missing"}

    api_url = f"http://127.0.0.1:{port}"
    headers = {"Accept": "application/json"}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": httpx.Timeout(3.5)}
    if username and password:
        kwargs["auth"] = (username, password)

    try:
        with httpx.Client() as client:
            res = client.get(f"{api_url}/api/v1/show_config", **kwargs)
            res.raise_for_status()
        return {"status": "ok", "api_url": api_url}
    except Exception as exc:
        return {"status": "error", "api_url": api_url, "reason": str(exc)}


def _start_kis_freqtrade_if_stopped(settings: AppSettings) -> dict[str, Any]:
    def _is_live_pid(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "state="],
                text=True,
            ).strip()
        except Exception:
            return False
        if not out:
            return False
        state = out[:1].upper()
        return state != "Z"

    manager = FreqtradeProcessManager(
        FreqtradeProcessManagerConfig(
            executable_path=settings.freqtrade_executable_path,
            workdir=settings.freqtrade_workdir,
            runtime_dir=settings.freqtrade_runtime_dir,
            stop_timeout_sec=settings.freqtrade_stop_timeout_sec,
        ),
        bots=[
            FreqtradeBotConfig(
                bot_id="kis",
                label="Freqtrade KIS",
                config_path="third_party/freqtrade/user_data/config_kis_jurobot.json",
            )
        ],
    )
    status = manager.list_statuses()[0]
    running_pid = int(status.get("pid") or 0)
    if bool(status.get("running")) and _is_live_pid(running_pid):
        return {
            "status": "ok",
            "action": "already_running",
            "pid": status.get("pid"),
        }

    external_pids: list[int] = []
    try:
        ps_out = subprocess.check_output(
            ["ps", "-ax", "-o", "pid=,command="],
            text=True,
        )
        for line in ps_out.splitlines():
            text = str(line or "").strip()
            if "freqtrade trade" not in text:
                continue
            if "config_kis_jurobot.json" not in text:
                continue
            parts = text.split(None, 1)
            if not parts:
                continue
            pid = int(parts[0])
            if pid > 0 and _is_live_pid(pid):
                external_pids.append(pid)
    except Exception:
        external_pids = []
    if external_pids:
        return {
            "status": "ok",
            "action": "external_running",
            "external_pids": external_pids,
        }

    started = manager.start("kis")
    return {
        "status": "ok",
        "action": started.get("action"),
        "pid": started.get("pid"),
    }


def run() -> None:
    settings = AppSettings()
    update_interval = max(int(settings.research_run_interval_sec), 300)
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
    report_repository = NaverReportRepository(settings.naver_reports_db_path)
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

    if not settings.research_enabled:
        logger.info("research runner disabled: TRADECRAFT_RESEARCH_ENABLED=false")
        return

    pipeline = _build_pipeline(settings)
    logger.info(
        "research runner started: state_path=%s strategy_md=%s llm_connected=%s update_interval=%ss",
        settings.research_state_path,
        settings.research_strategy_md_path,
        llm_connected,
        update_interval,
    )

    cycle = 0
    now = datetime.now(KST)
    next_update_at = now
    next_advice_at, label = _next_advice_slot(now, is_open_day=calendar.is_open_day)
    latest_snapshot: dict | None = None
    last_report_db_updated_at = _report_db_last_updated_at(report_repository)
    if last_report_db_updated_at is None:
        last_report_db_updated_at = ""
    last_kis_recover_at = now - timedelta(seconds=300)
    kis_recover_interval = timedelta(seconds=60)
    kis_api_fail_streak = 0
    while True:
        now = datetime.now(KST)

        if now - last_kis_recover_at >= kis_recover_interval:
            probe_result = _probe_kis_freqtrade_api(settings)
            if str(probe_result.get("status")) == "ok":
                kis_api_fail_streak = 0
            else:
                kis_api_fail_streak += 1
                start_result = _start_kis_freqtrade_if_stopped(settings)
                logger.warning(
                    "kis bot health-check failed (%s/3): probe=%s start=%s",
                    kis_api_fail_streak,
                    probe_result,
                    start_result,
                )
                if kis_api_fail_streak >= 3:
                    restart_result = _restart_kis_freqtrade_if_running(settings)
                    logger.warning(
                        "kis bot auto-recover restart executed: %s",
                        restart_result,
                    )
                    kis_api_fail_streak = 0
            last_kis_recover_at = now

        if now >= next_update_at:
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

        if now >= next_advice_at:
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
                                freqtrade_runtime_dir=settings.freqtrade_runtime_dir,
                            )
                            if synced:
                                did_symbol_sync = True
                                logger.info(
                                    "kis trader target symbols synced from morning advice: %s",
                                    ",".join(synced),
                                )
                            rebalance_targets = (
                                _sync_kis_rebalance_targets_to_freqtrade_override(
                                    payload=payload,
                                    snapshot=snapshot,
                                    runtime_dir=settings.freqtrade_runtime_dir,
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
                            try:
                                refresh_result = _reload_kis_freqtrade_config(settings)
                                if str(refresh_result.get("status")) != "ok":
                                    refresh_result = _restart_kis_freqtrade_if_running(
                                        settings
                                    )
                                logger.info(
                                    "kis freqtrade refresh after target sync: %s",
                                    refresh_result,
                                )
                            except Exception as exc:
                                logger.warning(
                                    "kis freqtrade refresh after target sync failed: %s",
                                    exc,
                                )
                                restart_result = _restart_kis_freqtrade_if_running(
                                    settings
                                )
                                logger.info(
                                    "kis freqtrade refresh after target sync: %s",
                                    restart_result,
                                )
                        if rebalance_targets:
                            try:
                                execution_result = asyncio.run(
                                    _execute_kis_forcexy_target_orders(
                                        settings=settings,
                                        payload=payload,
                                        rebalance_targets=rebalance_targets,
                                    )
                                )
                                logger.info(
                                    "kis forcexy target execution: %s",
                                    execution_result,
                                )
                                logger.info(
                                    "kis forcexy invested ratio target=%s actual=%s",
                                    (
                                        f"{_safe_float(execution_result.get('target_invested_ratio'))*100:.1f}%"
                                    ),
                                    (
                                        f"{_safe_float(execution_result.get('actual_invested_ratio'))*100:.1f}%"
                                        if execution_result.get("actual_invested_ratio")
                                        is not None
                                        else "n/a"
                                    ),
                                )
                            except Exception as exc:
                                logger.warning(
                                    "kis forcexy target execution failed: %s",
                                    exc,
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

        sleep_sec = max(
            int(
                min(next_update_at, next_advice_at).timestamp()
                - datetime.now(KST).timestamp()
            ),
            1,
        )
        sleep_sec = min(sleep_sec, 60)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    run()
