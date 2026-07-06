from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from tradecraft.services.jue_language_policy import jue_language_policy
from tradecraft.services.codex_native import CodexNativeRuntime

logger = logging.getLogger(__name__)


class ReportRepository(Protocol):
    def search(
        self,
        query: str,
        symbol: str = "",
        category: str = "",
        limit: int = 10,
        broker: str = "",
        analyst: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]: ...

    def get_report(self, report_id: int) -> dict[str, Any] | None: ...

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None: ...

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]: ...

    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...


class FundamentalsRepository(Protocol):
    def latest(self, symbol: str) -> dict[str, Any] | None: ...


class ETFResearchProvider(Protocol):
    def list_universe(self) -> list[dict[str, Any]]: ...

    def latest_snapshot(self, symbol: str) -> dict[str, Any]: ...

    def latest_score(self, symbol: str) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...


class RAGQueryStore(Protocol):
    def query(
        self,
        query: str,
        symbol: str = "",
        limit: int = 8,
        broker: str = "",
        doc_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class ExternalInsightSourceConfig:
    source_id: str
    label: str
    role: str
    path: str
    signal_types: list[str]
    coverage: list[str]
    caution: str


@dataclass(slots=True)
class StrategyIntelligenceConfig:
    whale_insight_path: str = ".runtime/insights/whale_insight.jsonl"
    sesiban_path: str = ".runtime/insights/sesiban.jsonl"
    insight_db_path: str = ""
    decision_log_path: str = ".runtime/strategy_intelligence_decisions.jsonl"
    model_timeout_ms: int = 120000
    max_report_scan: int = 90
    max_candidates: int = 8
    brief_cache_ttl_sec: int = 60
    migrate_legacy_jsonl: bool = False
    legacy_jsonl_sidecar_max_lines: int = 500


_CANDIDATE_WORDS = {
    "뭐사",
    "뭐 사",
    "담으",
    "후보",
    "관심",
    "스윙",
    "단타",
    "월요일",
    "다음주",
    "다음 주",
    "종가",
    "매수",
}
_MARKET_WORDS = {"시장", "거시", "분위기", "regime", "리스크온", "리스크오프"}
_REVIEW_WORDS = {"복기", "결과", "맞았", "틀렸", "성과"}
_STOCK_WORDS = {"종목", "목표가", "목표주가", "실적", "밸류", "리스크"}
_REPORT_WORDS = {"리포트", "보고서", "근거", "요약", "원문"}
_ETF_INTENT_WORDS = {
    "etf",
    "상장지수",
    "kodex",
    "tiger",
}
_POSITIVE_WORDS = {
    "상향",
    "개선",
    "성장",
    "수혜",
    "호실적",
    "컨센서스 상회",
    "턴어라운드",
    "수주",
    "ai",
    "hbm",
    "전력",
    "반도체",
    "가격 상승",
}
_NEGATIVE_WORDS = {
    "하향",
    "둔화",
    "부진",
    "적자",
    "리스크",
    "우려",
    "관세",
    "전쟁",
    "유가",
    "금리",
}
_EVIDENCE_WORDS = {
    "ai",
    "hbm",
    "eps",
    "반도체",
    "수급",
    "외국인",
    "기관",
    "실적",
    "이익",
    "매출",
    "영업이익",
    "목표주가",
    "컨센서스",
    "상향",
    "하향",
    "수주",
    "가격",
    "마진",
    "점유율",
    "리스크",
    "변동성",
    "섹터",
    "거래대금",
    "주주환원",
}
_NOISE_PHRASES = {
    "btn_report",
    "ssl.pstatic",
    "static.nfinance",
    "리포트 보기",
    "편집상의 공백페이지",
    "본 조사분석자료",
    "동 자료의 추천종목",
    "금융투자분석사의 확인",
    "담당자 및 그 배우자",
    "중요 공시는 appendix",
    "appendix 참조",
    "목표주가 추이",
    "투자의견 변동내역",
    "가처분 소득 대비 비율",
}
_GENERIC_TICKERS = {"260311", "260406", "260430", "260429", "071858"}
_NAME_NOISE_TOKENS = {
    "buy",
    "hold",
    "sell",
    "company",
    "report",
    "brief",
    "analyst",
    "ra",
    "유지",
    "상향",
    "하향",
    "목표주가",
    "현재주가",
    "투자의견",
    "리포트",
    "보기",
    "정보",
    "테마",
    "네이버",
    "네이버에",
    "콘텐츠",
    "제공",
    "코스콤",
    "국내",
    "시세",
    "img",
    "alt",
    "align",
    "absmiddle",
    "gif",
    "analysis",
}
_GENERIC_NAME_TOKENS = {
    "소재",
    "산업",
    "전략",
    "업황",
    "기업분석",
    "산업분석",
    "update",
    "preview",
    "review",
    "comment",
    "company",
    "earnings",
    "정보",
    "테마",
    "국내시세정보",
    "코스콤국내시세정보",
}
_GENERIC_ETF_NAME_PREFIXES = {
    "ACE",
    "ARIRANG",
    "HANARO",
    "KBSTAR",
    "KODEX",
    "KOSEF",
    "RISE",
    "SOL",
    "TIGER",
    "TIMEFOLIO",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, *, limit: int = 700) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(int(limit), 1)]


def _parse_signal_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _external_source_stale_after_days(source_id: str) -> int:
    normalized = str(source_id or "").strip().lower()
    if normalized == "after_close_330":
        return 2
    if normalized == "whale_insight":
        return 5
    return 7


def _latest_signal_date(rows: list[dict[str, Any]]) -> str:
    dated: list[tuple[date, str]] = []
    for row in rows:
        raw = str(row.get("as_of") or row.get("published_at") or "").strip()
        parsed = _parse_signal_date(raw)
        if parsed is not None:
            dated.append((parsed, raw))
    if not dated:
        return ""
    dated.sort(key=lambda item: item[0])
    return dated[-1][1]


def _apply_external_source_freshness(payload: dict[str, Any]) -> None:
    if not payload.get("signals") and int(payload.get("count") or 0) <= 0:
        return
    source_id = str(payload.get("source_id") or "")
    latest_as_of = str(payload.get("latest_as_of") or "").strip()
    if not latest_as_of:
        latest_as_of = _latest_signal_date(list(payload.get("signals") or []))
        if latest_as_of:
            payload["latest_as_of"] = latest_as_of
    parsed = _parse_signal_date(latest_as_of)
    warnings = list(payload.get("warnings") or [])
    if parsed is None:
        payload["status"] = "stale"
        payload["stale"] = True
        payload["stale_reason"] = "missing_latest_as_of"
        warnings.append("latest_as_of 없음: 외부 수급/고래 신호 최신성 확인 필요")
        payload["warnings"] = warnings
        return
    today = datetime.now(timezone.utc).date()
    stale_days = max((today - parsed).days, 0)
    stale_after_days = _external_source_stale_after_days(source_id)
    payload["stale_days"] = stale_days
    payload["stale_after_days"] = stale_after_days
    if stale_days > stale_after_days:
        payload["status"] = "stale"
        payload["stale"] = True
        payload["stale_reason"] = "latest_as_of_too_old"
        warnings.append(
            f"latest_as_of {latest_as_of} is {stale_days} days old "
            f"(limit {stale_after_days}d)"
        )
    else:
        payload["stale"] = False
    if warnings:
        payload["warnings"] = warnings


def _is_external_signal_stale(source_id: str, signal: dict[str, Any]) -> bool:
    return bool(_external_signal_freshness(source_id, signal)["stale"])


def _external_signal_freshness(source_id: str, signal: dict[str, Any]) -> dict[str, Any]:
    raw = str(signal.get("as_of") or signal.get("published_at") or "").strip()
    stale_after_days = _external_source_stale_after_days(
        str(signal.get("source_id") or source_id or "")
    )
    parsed = _parse_signal_date(raw)
    if parsed is None:
        return {
            "stale": True,
            "stale_days": None,
            "stale_after_days": stale_after_days,
            "stale_reason": "missing_as_of",
        }
    stale_days = max((datetime.now(timezone.utc).date() - parsed).days, 0)
    stale = stale_days > stale_after_days
    return {
        "stale": stale,
        "stale_days": stale_days,
        "stale_after_days": stale_after_days,
        "stale_reason": "as_of_too_old" if stale else "",
    }


def _decorate_external_signal_freshness(
    source_id: str, signal: dict[str, Any]
) -> dict[str, Any]:
    row = dict(signal)
    row.update(_external_signal_freshness(source_id, row))
    return row


def _keyword_hits(text: str, words: set[str]) -> list[str]:
    lower = text.lower()
    return [word for word in words if word in lower]


def _has_etf_intent(query: str) -> bool:
    text = str(query or "").lower()
    return any(word in text for word in _ETF_INTENT_WORDS)


def _is_etf_payload(row: dict[str, Any]) -> bool:
    return str(row.get("asset_class") or "").lower() == "etf"


def _clean_etf_display_name(value: Any, *, symbol: str = "") -> str:
    text = _clean_text(value, limit=50)
    if not text or _has_subject_boilerplate(text):
        return ""
    if symbol:
        text = re.sub(rf"\(?\b{re.escape(symbol)}\b\)?", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:/|,.;")
    return text if re.search(r"[A-Za-z가-힣]", text) else ""


def _is_generic_etf_display_name(value: Any) -> bool:
    text = _clean_etf_display_name(value)
    if not text:
        return True
    compact = _compact(text).upper()
    return compact in _GENERIC_ETF_NAME_PREFIXES


def _prefer_etf_display_name(current: Any, candidate: Any, *, symbol: str = "") -> str:
    current_name = _clean_etf_display_name(current, symbol=symbol)
    candidate_name = _clean_etf_display_name(candidate, symbol=symbol)
    if not candidate_name:
        return current_name
    if not current_name or current_name == symbol:
        return candidate_name
    if _is_generic_etf_display_name(current_name) and not _is_generic_etf_display_name(
        candidate_name,
    ):
        return candidate_name
    if _compact(candidate_name).upper().startswith(_compact(current_name).upper()) and len(
        candidate_name
    ) > len(current_name):
        return candidate_name
    if len(candidate_name) > len(current_name) and not _is_generic_etf_display_name(candidate_name):
        return candidate_name
    return current_name


def _include_etf_intent_candidates(
    candidates: list[dict[str, Any]],
    *,
    max_items: int,
    etf_intent: bool,
) -> list[dict[str, Any]]:
    top_candidates = list(candidates[:max_items])
    if max_items <= 0:
        return top_candidates
    if not etf_intent:
        non_etf_pool = [row for row in candidates if not _is_etf_payload(row)]
        target_non_etf = min(len(non_etf_pool), max(1, max_items // 2))
        if target_non_etf <= 0:
            return top_candidates

        selected_symbols = {str(row.get("symbol") or "") for row in top_candidates}
        selected_non_etf = sum(1 for row in top_candidates if not _is_etf_payload(row))
        for equity_row in non_etf_pool:
            if selected_non_etf >= target_non_etf:
                break
            symbol = str(equity_row.get("symbol") or "")
            if symbol in selected_symbols:
                continue
            replace_index = next(
                (
                    index
                    for index in range(len(top_candidates) - 1, -1, -1)
                    if _is_etf_payload(top_candidates[index])
                ),
                None,
            )
            if replace_index is None:
                break
            removed_symbol = str(top_candidates[replace_index].get("symbol") or "")
            selected_symbols.discard(removed_symbol)
            top_candidates[replace_index] = equity_row
            selected_symbols.add(symbol)
            selected_non_etf += 1
        top_candidates.sort(key=lambda row: (int(row["score"]), int(row["confidence"])), reverse=True)
        if len(top_candidates) >= 3 and not any(
            not _is_etf_payload(row) for row in top_candidates[:3]
        ):
            non_etf_index = next(
                (
                    index
                    for index, row in enumerate(top_candidates[3:], start=3)
                    if not _is_etf_payload(row)
                ),
                None,
            )
            if non_etf_index is not None:
                non_etf_row = top_candidates.pop(non_etf_index)
                top_candidates.insert(2, non_etf_row)
        return top_candidates[:max_items]

    selected_symbols = {str(row.get("symbol") or "") for row in top_candidates}
    desired_etfs = [row for row in candidates if _is_etf_payload(row)][
        : min(max(3, max_items // 3), max_items)
    ]
    for etf_row in desired_etfs:
        symbol = str(etf_row.get("symbol") or "")
        if symbol in selected_symbols:
            continue
        if len(top_candidates) < max_items:
            top_candidates.append(etf_row)
            selected_symbols.add(symbol)
            continue

        replace_index = next(
            (
                index
                for index in range(len(top_candidates) - 1, -1, -1)
                if not _is_etf_payload(top_candidates[index])
            ),
            None,
        )
        if replace_index is None:
            break
        removed_symbol = str(top_candidates[replace_index].get("symbol") or "")
        selected_symbols.discard(removed_symbol)
        top_candidates[replace_index] = etf_row
        selected_symbols.add(symbol)

    top_candidates.sort(key=lambda row: (int(row["score"]), int(row["confidence"])), reverse=True)
    return top_candidates[:max_items]


def _is_low_quality_evidence_text(value: Any, *, min_chars: int = 18) -> bool:
    text = _clean_text(value, limit=500)
    if len(text) < min_chars:
        return True
    lower = text.lower()
    if any(phrase in lower for phrase in _NOISE_PHRASES):
        return True
    useful_hits = _keyword_hits(lower, _EVIDENCE_WORDS | _POSITIVE_WORDS | _NEGATIVE_WORDS)
    alpha_tokens = re.findall(r"[A-Za-z가-힣]{2,}", text)
    if len(alpha_tokens) < 3 and not useful_hits:
        return True
    if text.lstrip().startswith("/") and len(alpha_tokens) < 5:
        return True
    if "목표주가" in text and len(alpha_tokens) < 4:
        return True
    digit_ratio = sum(ch.isdigit() for ch in text) / max(len(text), 1)
    hangul_alpha = re.sub(r"[^A-Za-z가-힣]", "", text)
    if len(hangul_alpha) < 8 and not useful_hits:
        return True
    if digit_ratio > 0.45 and len(useful_hits) < 2:
        return True
    return False


def _is_market_table_evidence_text(value: Any) -> bool:
    text = _clean_text(value, limit=500)
    if not text:
        return True
    lower = text.lower()
    table_tokens = (
        "1d(%)",
        "5d(%)",
        "mtd(%)",
        "ytd(%)",
        "1d(bp)",
        "5d(bp)",
        "close",
        "dow",
        "nasdaq",
        "d-20",
        "global indices",
        "korea market",
        "ficc",
        "주요지수",
        "지수등락률",
    )
    token_hits = sum(1 for token in table_tokens if token in lower)
    evidence_hits = _keyword_hits(lower, _EVIDENCE_WORDS | _POSITIVE_WORDS | _NEGATIVE_WORDS)
    return token_hits >= 3 and len(evidence_hits) <= 1


def _is_etf_specific_evidence_text(value: Any, *, symbol: str, name: str) -> bool:
    text = _clean_text(value, limit=500)
    if not text or _is_market_table_evidence_text(text):
        return False
    lower = text.lower()
    if symbol and symbol in text:
        return True
    clean_name = _clean_etf_display_name(name, symbol=symbol)
    if clean_name and len(clean_name) >= 4 and clean_name.lower() in lower:
        return True
    compact_text = _compact(text).lower()
    compact_name = _compact(clean_name).lower()
    if compact_name and len(compact_name) >= 4 and compact_name in compact_text:
        return True
    return any(
        keyword in lower
        for keyword in (
            "etf",
            "상장지수",
            "코스닥150",
            "kosdaq150",
            "코스피200",
            "kospi200",
            "s&p500",
            "sp500",
            "나스닥100",
            "nasdaq100",
        )
    )


def _query_terms(query: str) -> list[str]:
    stopwords = {
        "다음",
        "거래일",
        "관심",
        "후보",
        "전략",
        "정리",
        "시장",
        "종목",
        "봐줘",
        "알려줘",
    }
    terms = [
        token.lower()
        for token in re.findall(r"[A-Za-z가-힣0-9]{2,}", str(query or ""))
        if token.lower() not in stopwords
    ]
    return list(dict.fromkeys(terms))[:10]


def _rag_context_quality_score(row: dict[str, Any], *, query: str, content: str) -> int:
    if _is_low_quality_evidence_text(content, min_chars=24):
        return 0
    score = 18
    if len(content) >= 80:
        score += 8
    if len(content) >= 150:
        score += 6
    useful_hits = _keyword_hits(content, _EVIDENCE_WORDS | _POSITIVE_WORDS | _NEGATIVE_WORDS)
    score += min(len(useful_hits), 6) * 5
    term_hits = [term for term in _query_terms(query) if term in content.lower()]
    score += min(len(term_hits), 4) * 4
    symbol = str(row.get("symbol") or "").strip()
    if _is_candidate_symbol(symbol):
        score += 6
    if int(row.get("report_id") or 0) > 0:
        score += 4
    return max(0, min(100, score))


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _score_0_100(value: Any, default: float = 50.0) -> int:
    raw = _safe_float(value)
    if raw <= 0:
        raw = default
    return max(0, min(100, int(round(raw))))


def _average_score(values: list[Any]) -> int:
    rows = [_score_0_100(value) for value in values if _safe_float(value) > 0]
    if not rows:
        return 0
    return max(0, min(100, int(round(sum(rows) / len(rows)))))


def _clamp_score(value: Any) -> int:
    return max(0, min(100, int(round(_safe_float(value)))))


def _candidate_item(symbol: str, name: str, confidence: int) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "score": 0.0,
        "confidence": confidence,
        "reasons": [],
        "risks": [],
        "checks": [],
        "sources": set(),
        "report_ids": [],
        "citations": [],
        "facts": [],
        "published_dates": [],
        "components": {
            "report": 0.0,
            "research": 0.0,
            "whale": 0.0,
            "after_close": 0.0,
            "quality": 0.0,
            "growth": 0.0,
            "risk": 0.0,
        },
        "external_strengths": {},
    }


def _add_score(item: dict[str, Any], component: str, delta: float) -> None:
    item["score"] = float(item.get("score") or 0.0) + float(delta)
    components = item.setdefault("components", {})
    current = float(components.get(component) or 0.0)
    if component == "risk":
        components[component] = current + abs(float(delta))
    elif delta > 0:
        components[component] = current + float(delta)


def _source_component(source_id: str) -> str:
    if source_id == "whale_insight":
        return "whale"
    if source_id == "after_close_330":
        return "after_close"
    return "research"


def _candidate_component_scores(
    item: dict[str, Any],
    *,
    risks: list[str],
) -> dict[str, int]:
    components = dict(item.get("components") or {})
    external = dict(item.get("external_strengths") or {})
    whale = _average_score(list(external.get("whale_insight") or []))
    after_close = _average_score(list(external.get("after_close_330") or []))
    report = max(0, min(100, int(round(float(components.get("report") or 0.0) * 2.0))))
    research = max(0, min(100, int(round(float(components.get("research") or 0.0) * 7.0))))
    text_risk = min(36.0, float(components.get("risk") or 0.0) * 6.0)
    listed_risk = min(24.0, len(risks) * 4.0)
    valuation_payload = item.get("valuation") if isinstance(item.get("valuation"), dict) else {}
    valuation_score = int(
        max(
            0,
            min(
                100,
                _safe_float((valuation_payload.get("score") or {}).get("undervalued_score")) or 0,
            ),
        )
    )
    valuation_risk = int(
        max(
            0,
            min(
                100,
                _safe_float((valuation_payload.get("score") or {}).get("overvalued_risk")) or 0,
            ),
        )
    )
    valuation_risk_contribution = min(18.0, valuation_risk * 0.20)
    risk_penalty = max(
        0,
        min(
            100,
            int(round(text_risk + listed_risk + valuation_risk_contribution)),
        ),
    )
    quality_score = max(
        _clamp_score((valuation_payload.get("score") or {}).get("quality_score")),
        max(0, min(100, int(round(float(components.get("quality") or 0.0) * 6.0)))),
    )
    growth_score = max(
        _clamp_score((valuation_payload.get("score") or {}).get("growth_score")),
        max(0, min(100, int(round(float(components.get("growth") or 0.0) * 6.0)))),
    )
    recency = _recency_score(list(item.get("published_dates") or []))
    evidence = _evidence_depth_score(item)
    fit = _weighted_candidate_score(
        {
            "report": report,
            "research": research,
            "whale": whale,
            "after_close": after_close,
            "valuation": valuation_score,
            "quality": quality_score,
            "growth": growth_score,
            "risk_penalty": risk_penalty,
            "recency": recency,
            "evidence": evidence,
        },
        source_count=len(set(item.get("sources") or [])),
    )
    return {
        "report": report,
        "research": research,
        "whale": whale,
        "after_close": after_close,
        "valuation": valuation_score,
        "quality": quality_score,
        "growth": growth_score,
        "risk_penalty": risk_penalty,
        "risk_score": risk_penalty,
        "recency": recency,
        "evidence": evidence,
        "fit": fit,
    }


def _weighted_candidate_score(
    components: dict[str, int],
    *,
    source_count: int,
) -> int:
    report = int(components.get("report") or 0)
    research = int(components.get("research") or 0)
    whale = int(components.get("whale") or 0)
    after_close = int(components.get("after_close") or 0)
    valuation = int(components.get("valuation") or 0)
    recency = int(components.get("recency") or 0)
    evidence = int(components.get("evidence") or 0)
    risk = int(components.get("risk_penalty") or 0)
    source_bonus = min(max(source_count, 0), 4) * 2.5
    sparse_penalty = 8.0 if max(report, research, whale, after_close, valuation) < 35 else 0.0
    high_risk_penalty = 6.0 if risk >= 70 else 0.0
    raw = (
        28.0
        + report * 0.32
        + research * 0.14
        + whale * 0.17
        + after_close * 0.17
        + valuation * 0.08
        + recency * 0.08
        + evidence * 0.10
        + source_bonus
        - risk * 0.30
        - sparse_penalty
        - high_risk_penalty
    )
    return max(0, min(100, int(round(raw))))


def _grade_rank(grade: str) -> int:
    order = {"A": 4, "B": 3, "C": 2, "D": 1, "E": 0}
    return order.get(str(grade or "E").upper(), 0)


def _min_grade(left: str, right: str) -> str:
    return left if _grade_rank(left) <= _grade_rank(right) else right


def _grade_for_score(
    score: int,
    *,
    evidence: int,
    coverage_score: int,
) -> str:
    if score >= 80:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 50:
        grade = "C"
    elif score >= 35:
        grade = "D"
    else:
        grade = "E"
    if evidence < 45:
        grade = _min_grade(grade, "B")
    if coverage_score < 35:
        grade = _min_grade(grade, "C")
    return grade


def _top_component_drivers(
    components: dict[str, int],
    labels: dict[str, str],
    *,
    minimum: int = 55,
    limit: int = 3,
) -> list[str]:
    rows = sorted(
        ((key, int(components.get(key) or 0)) for key in labels),
        key=lambda row: row[1],
        reverse=True,
    )
    return [
        f"{labels[key]} {score}"
        for key, score in rows
        if score >= minimum
    ][:limit]


def _candidate_data_coverage(
    *,
    sources: list[str],
    valuation: dict[str, Any],
    components: dict[str, int],
) -> dict[str, Any]:
    source_set = set(sources)
    has_valuation = str(valuation.get("status") or "").lower() == "ok"
    has_quality_data = has_valuation and int(components.get("quality") or 0) > 0
    has_growth_data = has_valuation and int(components.get("growth") or 0) > 0
    coverage_score = min(
        100,
        len(source_set) * 16
        + (18 if has_valuation else 0)
        + (10 if has_quality_data else 0)
        + (10 if has_growth_data else 0)
        + (12 if int(components.get("evidence") or 0) >= 60 else 0),
    )
    missing: list[str] = []
    if "whale_insight" not in source_set:
        missing.append("고래")
    if "after_close_330" not in source_set:
        missing.append("종가수급")
    if "naver_reports" not in source_set:
        missing.append("리포트")
    if not has_valuation:
        missing.append("밸류")
    return {
        "source_count": len(source_set),
        "coverage_score": coverage_score,
        "has_report": "naver_reports" in source_set,
        "has_research": bool(source_set.intersection({"research_feed", "daily_discovery"})),
        "has_whale": "whale_insight" in source_set,
        "has_after_close": "after_close_330" in source_set,
        "has_valuation": has_valuation,
        "has_quality_data": has_quality_data,
        "has_growth_data": has_growth_data,
        "missing": missing[:6],
    }


def _candidate_identity_status(symbol: str, name: Any) -> dict[str, Any]:
    code = str(symbol or "").strip()
    clean_name = _sanitize_subject_name(name, symbol=code)
    if not _is_candidate_symbol(code):
        return {
            "status": "suspect",
            "label": "종목코드 확인 필요",
            "reason": "invalid_symbol",
        }
    if not clean_name or clean_name == code:
        return {
            "status": "suspect",
            "label": "종목명 검증 필요",
            "reason": "missing_or_generic_name",
        }
    if not _is_good_subject_name(clean_name):
        return {
            "status": "suspect",
            "label": "종목명 검증 필요",
            "reason": "low_quality_name",
        }
    return {
        "status": "ok",
        "label": "종목명 확인",
        "reason": "resolved",
    }


def _candidate_data_warnings(
    *,
    identity_status: dict[str, Any],
    coverage: dict[str, Any],
    valuation: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if str(identity_status.get("status") or "") != "ok":
        warnings.append(str(identity_status.get("label") or "종목명 검증 필요"))
    if int(coverage.get("source_count") or 0) <= 1:
        warnings.append("소스 1개")
    missing = set(str(item) for item in list(coverage.get("missing") or []))
    valuation_status = str(valuation.get("status") or "").lower()
    if (
        valuation_status != "not_applicable"
        and ("밸류" in missing or valuation_status != "ok")
    ):
        warnings.append("밸류 미수집")
    if "고래" in missing:
        warnings.append("고래 없음")
    if "종가수급" in missing:
        warnings.append("세시반 없음")
    if "리포트" in missing:
        warnings.append("리포트 없음")
    return list(dict.fromkeys(warnings))[:6]


def _etf_candidate_suitability(
    *,
    components: dict[str, int],
    reasons: list[str],
    risks: list[str],
    has_etf_research: bool,
    has_report: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = int(components.get("report") or 0)
    liquidity = int(components.get("liquidity") or 0)
    momentum = int(components.get("momentum") or 0)
    core_fit = int(components.get("core_fit") or 0)
    risk = int(components.get("risk_penalty") or 0)
    recency = int(components.get("recency") or 0)
    evidence = int(components.get("evidence") or 0)
    source_count = int(has_etf_research) + int(has_report)
    coverage_score = min(
        100,
        24
        + (28 if has_etf_research else 0)
        + (18 if has_report else 0)
        + (16 if liquidity >= 50 else 0)
        + (12 if evidence >= 45 else 0),
    )
    coverage = {
        "source_count": source_count,
        "coverage_score": coverage_score,
        "has_report": has_report,
        "has_research": False,
        "has_whale": False,
        "has_after_close": False,
        "has_valuation": False,
        "has_quality_data": False,
        "has_growth_data": False,
        "has_etf_research": has_etf_research,
        "missing": [] if has_etf_research else ["ETF리서치"],
    }
    base_risks = list(risks[:2])
    if risk >= 65:
        base_risks.insert(0, f"리스크 점수 {risk}")
    labels = {
        "report": "ETF 리포트",
        "liquidity": "ETF 유동성",
        "momentum": "ETF 모멘텀",
        "core_fit": "코어 적합도",
        "recency": "최신성",
        "evidence": "근거품질",
    }
    short_raw = (
        18.0
        + report * 0.16
        + liquidity * 0.24
        + momentum * 0.30
        + core_fit * 0.18
        + recency * 0.06
        + evidence * 0.06
        - risk * 0.20
    )
    mid_raw = (
        18.0
        + report * 0.18
        + liquidity * 0.26
        + momentum * 0.18
        + core_fit * 0.28
        + recency * 0.04
        + evidence * 0.06
        - risk * 0.20
    )
    long_raw = (
        18.0
        + report * 0.14
        + liquidity * 0.24
        + momentum * 0.10
        + core_fit * 0.40
        + recency * 0.04
        + evidence * 0.08
        - risk * 0.18
    )
    short_term = _horizon_bucket(
        score=short_raw,
        components=components,
        coverage=coverage,
        labels=labels,
        base_drivers=list(reasons[:2]),
        base_risks=base_risks,
    )
    mid_term = _horizon_bucket(
        score=mid_raw,
        components=components,
        coverage=coverage,
        labels=labels,
        base_drivers=list(reasons[:2]),
        base_risks=base_risks,
    )
    long_term = _horizon_bucket(
        score=long_raw,
        components=components,
        coverage=coverage,
        labels=labels,
        base_drivers=list(reasons[:2]),
        base_risks=base_risks,
    )
    balanced_score = _clamp_score(
        short_term["score"] * 0.30 + mid_term["score"] * 0.30 + long_term["score"] * 0.40
    )
    balanced = {
        "score": balanced_score,
        "grade": _grade_for_score(
            balanced_score,
            evidence=evidence,
            coverage_score=coverage_score,
        ),
        "drivers": list(
            dict.fromkeys(
                short_term["drivers"][:1]
                + mid_term["drivers"][:1]
                + long_term["drivers"][:1]
            )
        )[:4],
        "risks": list(dict.fromkeys(base_risks))[:4],
    }
    return {
        "short_term": short_term,
        "mid_term": mid_term,
        "long_term": long_term,
        "balanced": balanced,
    }, coverage


def _horizon_bucket(
    *,
    score: float,
    components: dict[str, int],
    coverage: dict[str, Any],
    labels: dict[str, str],
    base_drivers: list[str],
    base_risks: list[str],
) -> dict[str, Any]:
    final_score = _clamp_score(score)
    evidence = int(components.get("evidence") or 0)
    coverage_score = int(coverage.get("coverage_score") or 0)
    drivers = list(dict.fromkeys(_top_component_drivers(components, labels) + base_drivers))[:4]
    risks = list(dict.fromkeys(base_risks))[:4]
    return {
        "score": final_score,
        "grade": _grade_for_score(
            final_score,
            evidence=evidence,
            coverage_score=coverage_score,
        ),
        "drivers": drivers or ["근거 보강 필요"],
        "risks": risks or ["리스크 추가 점검"],
    }


def _raise_horizon_floor(
    bucket: dict[str, Any],
    *,
    floor: int,
    evidence: int,
    coverage_score: int,
    driver: str,
) -> None:
    current = int(bucket.get("score") or 0)
    if current >= floor:
        return
    bucket["score"] = floor
    bucket["grade"] = _grade_for_score(
        floor,
        evidence=evidence,
        coverage_score=coverage_score,
    )
    drivers = list(bucket.get("drivers") or [])
    bucket["drivers"] = list(dict.fromkeys([driver] + drivers))[:4]


def _candidate_suitability(
    *,
    item: dict[str, Any],
    components: dict[str, int],
    sources: list[str],
    reasons: list[str],
    risks: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    valuation = item.get("valuation") if isinstance(item.get("valuation"), dict) else {}
    coverage = _candidate_data_coverage(
        sources=sources,
        valuation=valuation,
        components=components,
    )
    risk = int(components.get("risk_penalty") or 0)
    report = int(components.get("report") or 0)
    research = int(components.get("research") or 0)
    after_close = int(components.get("after_close") or 0)
    whale = int(components.get("whale") or 0)
    valuation_score = int(components.get("valuation") or 0)
    quality = int(components.get("quality") or 0)
    growth = int(components.get("growth") or 0)
    recency = int(components.get("recency") or 0)
    evidence = int(components.get("evidence") or 0)
    quality_growth = int(round((quality + growth) / 2))

    common_risks = list(risks[:2])
    if risk >= 65:
        common_risks.insert(0, f"리스크 점수 {risk}")
    has_valuation = bool(coverage.get("has_valuation"))
    if not has_valuation:
        common_risks.append("밸류 미수집")
    if int(coverage.get("source_count") or 0) <= 1:
        common_risks.append("소스 1개 기반")

    short_raw = (
        17.0
        + after_close * 0.30
        + report * 0.25
        + research * 0.25
        + recency * 0.15
        + evidence * 0.10
        + whale * 0.10
        + valuation_score * 0.05
        + quality_growth * 0.05
        - risk * 0.22
    )
    mid_raw = (
        17.0
        + report * 0.25
        + research * 0.18
        + growth * 0.20
        + valuation_score * 0.15
        + after_close * 0.15
        + whale * 0.10
        + quality * 0.10
        + evidence * 0.05
        - risk * 0.24
    )
    long_raw = (
        17.0
        + quality * 0.25
        + growth * 0.20
        + valuation_score * 0.20
        + whale * 0.15
        + report * 0.10
        + research * 0.08
        + evidence * 0.05
        + after_close * 0.05
        - risk * 0.22
    )
    if not has_valuation:
        mid_raw -= 5.0
        long_raw -= 14.0
    short_term = _horizon_bucket(
        score=short_raw,
        components=components,
        coverage=coverage,
        labels={
            "after_close": "단기 수급",
            "report": "리포트 모멘텀",
            "research": "디스커버리/리서치",
            "recency": "최신성",
            "evidence": "근거품질",
            "whale": "고래",
        },
        base_drivers=list(reasons[:2]),
        base_risks=common_risks,
    )
    mid_term = _horizon_bucket(
        score=mid_raw,
        components=components,
        coverage=coverage,
        labels={
            "report": "리포트 모멘텀",
            "research": "디스커버리/리서치",
            "growth": "성장",
            "valuation": "밸류",
            "after_close": "수급",
            "quality": "퀄리티",
        },
        base_drivers=list(reasons[:2]),
        base_risks=common_risks,
    )
    long_term = _horizon_bucket(
        score=long_raw,
        components=components,
        coverage=coverage,
        labels={
            "quality": "퀄리티",
            "growth": "성장",
            "valuation": "밸류",
            "whale": "고래",
            "report": "리포트",
            "research": "디스커버리/리서치",
        },
        base_drivers=list(reasons[:2]),
        base_risks=common_risks,
    )
    if after_close >= 70:
        _raise_horizon_floor(
            short_term,
            floor=min(72, 48 + int(after_close * 0.20)),
            evidence=evidence,
            coverage_score=int(coverage.get("coverage_score") or 0),
            driver=f"세시반 독립 수급 {after_close}",
        )
        _raise_horizon_floor(
            mid_term,
            floor=min(54, 32 + int(after_close * 0.12)),
            evidence=evidence,
            coverage_score=int(coverage.get("coverage_score") or 0),
            driver=f"세시반 다음 거래일 확인 {after_close}",
        )
    if whale >= 70:
        _raise_horizon_floor(
            mid_term,
            floor=min(56, 34 + int(whale * 0.12)),
            evidence=evidence,
            coverage_score=int(coverage.get("coverage_score") or 0),
            driver=f"고래 포지션 변화 {whale}",
        )
        _raise_horizon_floor(
            long_term,
            floor=min(58, 36 + int(whale * 0.13)),
            evidence=evidence,
            coverage_score=int(coverage.get("coverage_score") or 0),
            driver=f"고래 중장기 검토 {whale}",
        )
    balanced_score = _clamp_score(
        (short_term["score"] + mid_term["score"] + long_term["score"]) / 3
    )
    balanced = {
        "score": balanced_score,
        "grade": _grade_for_score(
            balanced_score,
            evidence=evidence,
            coverage_score=int(coverage.get("coverage_score") or 0),
        ),
        "drivers": list(
            dict.fromkeys(
                short_term["drivers"][:1]
                + mid_term["drivers"][:1]
                + long_term["drivers"][:1]
            )
        )[:4],
        "risks": list(dict.fromkeys(common_risks))[:4],
    }
    return {
        "short_term": short_term,
        "mid_term": mid_term,
        "long_term": long_term,
        "balanced": balanced,
    }, coverage


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", text)
    if not match:
        return None
    year, month, day = (_safe_int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _recency_score(values: list[Any]) -> int:
    dates = [parsed for value in values if (parsed := _parse_date(value)) is not None]
    if not dates:
        return 35
    latest = max(dates)
    age_days = max((datetime.now(timezone.utc).date() - latest).days, 0)
    if age_days <= 7:
        return 100
    if age_days <= 14:
        return 88
    if age_days <= 30:
        return 72
    if age_days <= 60:
        return 54
    if age_days <= 120:
        return 34
    return 18


def _evidence_depth_score(item: dict[str, Any]) -> int:
    report_count = len(set(item.get("report_ids") or []))
    fact_count = len([row for row in list(item.get("facts") or []) if str(row).strip()])
    source_count = len(set(item.get("sources") or []))
    external_count = sum(len(list(values or [])) for values in dict(item.get("external_strengths") or {}).values())
    research_score = float((item.get("components") or {}).get("research") or 0.0)
    raw = (
        report_count * 16
        + fact_count * 8
        + source_count * 5
        + external_count * 14
        + (12 if research_score > 0 else 0)
    )
    return max(0, min(100, int(raw)))


def _is_six_digit_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _is_report_date_symbol(value: Any) -> bool:
    symbol = str(value or "").strip()
    if not _is_six_digit_symbol(symbol):
        return False
    year = _safe_int(symbol[:2])
    month = _safe_int(symbol[2:4])
    day = _safe_int(symbol[4:6])
    return 20 <= year <= 35 and 1 <= month <= 12 and 1 <= day <= 31


def _is_candidate_symbol(value: Any) -> bool:
    symbol = str(value or "").strip()
    return (
        _is_six_digit_symbol(symbol)
        and symbol not in _GENERIC_TICKERS
        and not _is_report_date_symbol(symbol)
    )


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _corporate_name_key(value: Any) -> str:
    key = _compact(value)
    if not key:
        return ""
    key = key.replace("㈜", "").replace("(주)", "").replace("주식회사", "")
    for suffix in ("공사", "공단", "유한회사"):
        if key.endswith(suffix) and len(key) > len(suffix) + 1:
            key = key[: -len(suffix)]
            break
    return key


def _sanitize_subject_name(value: Any, *, symbol: str = "") -> str:
    text = _clean_text(value, limit=120)
    if not text:
        return ""
    if _has_subject_boilerplate(text):
        return ""
    before_date = re.split(r"\b\d{4}[./-]\d{1,2}", text, maxsplit=1)[0].strip()
    if len(before_date) > 1 and re.search(r"[A-Za-z가-힣]", before_date):
        text = before_date
    text = re.sub(r"https?://\S+|www\.\S+|src=\S+", " ", text, flags=re.IGNORECASE)
    text = text.replace("<", " ").replace(">", " ").replace('"', " ")
    if symbol:
        text = re.sub(rf"\(?\b{re.escape(symbol)}\b[^)]*\)?", " ", text)
    text = re.sub(r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b", " ", text)
    text = re.sub(r"\b\d{4}[./-]\d{1,2}\b", " ", text)
    tokens: list[str] = []
    for token in text.split():
        item = token.strip("()[]{}:;,.·/|")
        if not item:
            continue
        if "@" in item or re.fullmatch(r"[a-z]+(?:\.[a-z]+)+", item.lower()):
            continue
        if "=" in item:
            continue
        if item.lower() in _NAME_NOISE_TOKENS:
            continue
        if len(item) == 1 and item.isascii():
            continue
        if re.search(r"\d", item):
            continue
        if not re.search(r"[A-Za-z가-힣]", item):
            continue
        if any(marker in item.lower() for marker in ["http", "static", "pstatic", "ssl"]):
            continue
        tokens.append(item)
    if not tokens:
        return ""
    if len(tokens) >= 2 and re.search(r"[A-Za-z]", tokens[-1]) and re.search(
        r"[A-Za-z]",
        tokens[-2],
    ):
        candidate = _clean_text(f"{tokens[-2]} {tokens[-1]}", limit=40)
        return "" if candidate.lower() in _GENERIC_NAME_TOKENS else candidate
    if len(tokens) >= 2 and tokens[-2].isupper() and re.search(r"[가-힣]", tokens[-1]):
        candidate = _clean_text(f"{tokens[-2]} {tokens[-1]}", limit=40)
        return "" if candidate.lower() in _GENERIC_NAME_TOKENS else candidate
    candidate = _clean_text(tokens[-1], limit=40)
    return "" if candidate.lower() in _GENERIC_NAME_TOKENS else candidate


def _has_subject_boilerplate(value: Any) -> bool:
    compact = _compact(str(value or "")).lower()
    return any(
        _compact(marker).lower() in compact
        for marker in (
            "코스콤 국내 시세 정보",
            "네이버에 콘텐츠 제공",
            "테마 정보",
        )
    )


def _is_good_subject_name(value: Any) -> bool:
    text = _sanitize_subject_name(value)
    if not text:
        return False
    lower = text.lower()
    compact = _compact(text).lower()
    return (
        lower not in _NAME_NOISE_TOKENS
        and lower not in _GENERIC_NAME_TOKENS
        and compact not in _GENERIC_NAME_TOKENS
        and bool(re.search(r"[A-Za-z가-힣]", text))
    )


def _extract_subject_from_text(
    text: str,
    *,
    preferred_symbol: str = "",
) -> tuple[str, str, bool]:
    patterns = [
        (35, r"([가-힣A-Za-z0-9&._\-\s]{2,60})\((\d{6})[^)]*\)", "name_symbol"),
        (30, r"\((\d{6})\)\s*([가-힣A-Za-z0-9&._\-\s]{2,60})", "symbol_name"),
        (24, r"([가-힣A-Za-z0-9&._\-\s]{2,60})\s+(\d{6})(?!\d)", "name_symbol"),
        (18, r"(?<!\d)(\d{6})\s+([가-힣A-Za-z0-9&._\-\s]{2,60})", "symbol_name"),
    ]
    candidates: list[tuple[int, str, str, bool]] = []
    for base_score, pattern, mode in patterns:
        for match in re.finditer(pattern, text):
            if mode == "name_symbol":
                raw_name = match.group(1)
                symbol = match.group(2)
            else:
                symbol = match.group(1)
                raw_name = match.group(2)
            if not _is_candidate_symbol(symbol):
                continue
            name = _sanitize_subject_name(raw_name, symbol=symbol)
            if not name:
                continue
            score = base_score + (20 if symbol == preferred_symbol else 0)
            candidates.append((score, symbol, name, base_score >= 30))
    if not candidates:
        return "", "", False
    candidates.sort(key=lambda row: row[0], reverse=True)
    _, symbol, name, exact = candidates[0]
    return symbol, name, exact


def classify_strategy_intent(query: str) -> dict[str, Any]:
    text = str(query or "").strip().lower()
    compact = _compact(text)
    symbol_match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    symbol_hint = symbol_match.group(1) if symbol_match else ""
    if not text:
        return {
            "intent": "market_brief",
            "label": "시장 판단",
            "confidence": "low",
            "route": "strategy_brief",
        }

    if any(word in compact or word in text for word in _REVIEW_WORDS):
        intent = "strategy_review"
        label = "전략 복기"
        route = "strategy_review"
    elif any(word in compact or word in text for word in _CANDIDATE_WORDS):
        intent = "candidate_scout"
        label = "후보 발굴"
        route = "strategy_candidates"
    elif any(word in compact or word in text for word in _MARKET_WORDS):
        intent = "market_brief"
        label = "시장 판단"
        route = "strategy_brief"
    elif _is_six_digit_symbol(symbol_hint) or any(
        word in compact or word in text
        for word in _STOCK_WORDS
    ):
        intent = "stock_analysis"
        label = "종목 분석"
        route = "strategy_stock"
    elif any(word in compact or word in text for word in _REPORT_WORDS):
        intent = "report_qa"
        label = "리포트 질문"
        route = "research_qa"
    else:
        intent = "market_brief"
        label = "시장 판단"
        route = "strategy_brief"

    confidence = "high" if intent in {"candidate_scout", "stock_analysis"} else "medium"
    return {
        "intent": intent,
        "label": label,
        "confidence": confidence,
        "route": route,
        "signals_needed": [
            "report_sentiment",
            "target_price_delta",
            "eps_revision",
            "sector_momentum",
            "whale_position",
            "after_close_flow",
            "risk_event",
        ],
    }


def _extract_report_subject(row: dict[str, Any], content: str) -> tuple[str, str, bool]:
    text = " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("company_name") or ""),
            content[:1800],
        ]
    )
    row_symbol = str(row.get("symbol") or "").strip()
    text_symbol, text_name, text_exact = _extract_subject_from_text(
        text,
        preferred_symbol=row_symbol,
    )
    if _is_candidate_symbol(row_symbol):
        row_name = _sanitize_subject_name(row.get("company_name"), symbol=row_symbol)
        title_name = _sanitize_subject_name(row.get("title"), symbol=row_symbol)
        if text_symbol:
            if text_symbol == row_symbol:
                name = row_name or title_name or text_name or text_symbol
                if not row_name and text_exact and _is_good_subject_name(text_name):
                    name = text_name
                elif len(text_name) >= len(name) + 2 and _is_good_subject_name(text_name):
                    name = text_name
            else:
                name = text_name or text_symbol
            return text_symbol, name, text_exact
        name = row_name or title_name or text_name or row_symbol
        exact = bool(re.search(rf"(?<!\d){re.escape(row_symbol)}(?!\d)", text))
        return row_symbol, name, exact

    if text_symbol:
        return text_symbol, text_name, text_exact

    symbol = row_symbol
    if not _is_candidate_symbol(symbol):
        return "", "", False
    name = _sanitize_subject_name(row.get("company_name"), symbol=symbol)
    if not name or "리포트 보기" in name:
        name = symbol
    return symbol, name, False


class JSONLInsightSource:
    def __init__(
        self,
        config: ExternalInsightSourceConfig,
        repository: StrategyInsightRepository | None = None,
    ) -> None:
        self.config = config
        self.repository = repository

    def read(self, *, limit: int = 200) -> dict[str, Any]:
        path = Path(self.config.path)
        signals: list[dict[str, Any]] = []
        total_count = 0
        latest_as_of = ""
        latest_collected_at = ""
        if self.repository is not None:
            summary = self.repository.source_summary(
                source_id=self.config.source_id,
                limit=limit,
            )
            signals = list(summary.get("signals") or [])
            total_count = int(summary.get("total_count") or 0)
            latest_as_of = str(summary.get("latest_as_of") or "")
            latest_collected_at = str(summary.get("latest_collected_at") or "")
        elif path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or row.get("code") or "").strip()
                if symbol and not _is_candidate_symbol(symbol):
                    continue
                strength = _score_0_100(
                    row.get("strength") or row.get("score") or row.get("confidence"),
                    default=55,
                )
                raw_tags = row.get("tags") or []
                if isinstance(raw_tags, str):
                    tags = [raw_tags]
                elif isinstance(raw_tags, list):
                    tags = raw_tags
                else:
                    tags = []
                signals.append(
                    {
                        "source_id": self.config.source_id,
                        "source": self.config.label,
                        "symbol": symbol,
                        "name": _clean_text(row.get("name") or row.get("company"), limit=50),
                        "signal_type": str(row.get("signal_type") or row.get("type") or "insight"),
                        "direction": str(row.get("direction") or row.get("sentiment") or "neutral"),
                        "strength": strength,
                        "summary": _clean_text(row.get("summary") or row.get("note") or row.get("reason"), limit=240),
                        "as_of": str(row.get("as_of") or row.get("published_at") or row.get("updated_at") or ""),
                        "tags": [str(item) for item in tags[:6]],
                    }
                )
            total_count = len(signals)
            latest_as_of = _latest_signal_date(signals)

        payload = {
            "source_id": self.config.source_id,
            "label": self.config.label,
            "role": self.config.role,
            "coverage": self.config.coverage,
            "signal_types": self.config.signal_types,
            "caution": self.config.caution,
            "path": str(path),
            "available": path.exists(),
            "status": "ok" if signals else "waiting",
            "count": total_count,
            "returned_count": len(signals),
            "signals": [
                _decorate_external_signal_freshness(self.config.source_id, row)
                for row in signals
            ],
        }
        if self.repository is not None:
            payload["db_path"] = self.repository.db_path
            payload["storage"] = "sqlite"
            payload["available"] = True
            payload["latest_as_of"] = latest_as_of
            payload["latest_collected_at"] = latest_collected_at
        else:
            payload["storage"] = "jsonl"
            if latest_as_of:
                payload["latest_as_of"] = latest_as_of
        _apply_external_source_freshness(payload)
        return payload


def _bool_value(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off", "disabled"}


def _signal_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        signals = payload.get("signals")
        if isinstance(signals, list):
            return [row for row in signals if isinstance(row, dict)]
        return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _signal_rows_from_text(text: str) -> list[dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        return _signal_rows_from_payload(json.loads(raw))
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in raw.splitlines():
            item = line.strip()
            if not item:
                continue
            try:
                payload = json.loads(item)
            except json.JSONDecodeError:
                continue
            rows.extend(_signal_rows_from_payload(payload))
        return rows


def _signal_fingerprint(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or row.get("code") or "").strip()
    signal_type = str(row.get("signal_type") or row.get("type") or "insight").strip()
    direction = str(row.get("direction") or row.get("sentiment") or "neutral").strip()
    as_of = str(row.get("as_of") or row.get("published_at") or row.get("updated_at") or "").strip()
    summary = _clean_text(
        row.get("summary") or row.get("note") or row.get("reason") or "",
        limit=240,
    )
    return "|".join([symbol, signal_type, direction, as_of, summary]).lower()


def _source_limit(source: dict[str, Any], default: int) -> int:
    try:
        return max(1, min(int(source.get("limit") or default), 200))
    except (TypeError, ValueError):
        return default


def _collector_cache_path(source: dict[str, Any], default_path: str) -> Path:
    return Path(str(source.get("cache_path") or default_path)).expanduser()


def _read_signal_cache(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        rows = payload.get("signals")
    else:
        rows = payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _write_signal_cache(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    source_id: str,
    url: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_id": source_id,
        "url": url,
        "cached_at": _now_iso(),
        "count": len(rows),
        "signals": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_symbol_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("symbols"), dict):
        payload = payload.get("symbols")
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in payload.items():
        symbol = str(value or "").strip()
        if _is_candidate_symbol(symbol):
            out[_compact(key)] = symbol
    return out


def _write_symbol_cache(path: Path, symbols: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _now_iso(),
        "symbols": dict(sorted(symbols.items())),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tags_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
        value = parsed
    if isinstance(value, list):
        return [str(item) for item in value[:12] if str(item).strip()]
    return []


class StrategyInsightRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path or "").strip()
        if self.db_path:
            self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path:
            raise RuntimeError("strategy insight db path is not configured")
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        if str(path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_signals (
                    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    signal_type TEXT NOT NULL DEFAULT 'insight',
                    direction TEXT NOT NULL DEFAULT 'neutral',
                    strength INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    as_of TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    collected_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(source_id, fingerprint)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_signals_source_asof "
                "ON strategy_signals(source_id, as_of DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_signals_symbol_asof "
                "ON strategy_signals(symbol, as_of DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_signals_type_asof "
                "ON strategy_signals(signal_type, as_of DESC)"
            )

    @staticmethod
    def _row_to_signal(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_id": str(row["source_id"] or ""),
            "symbol": str(row["symbol"] or ""),
            "name": str(row["name"] or ""),
            "signal_type": str(row["signal_type"] or "insight"),
            "direction": str(row["direction"] or "neutral"),
            "strength": int(row["strength"] or 0),
            "summary": str(row["summary"] or ""),
            "as_of": str(row["as_of"] or ""),
            "tags": _tags_from_payload(row["tags_json"]),
            "collected_at": str(row["collected_at"] or ""),
            "signal_id": int(row["signal_id"] or 0),
        }

    def upsert_signals(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        now = _now_iso()
        inserted = 0
        skipped = 0
        with self._connect() as conn:
            for row in rows:
                source_id = str(row.get("source_id") or "").strip()
                symbol = str(row.get("symbol") or "").strip()
                if not source_id or not _is_candidate_symbol(symbol):
                    skipped += 1
                    continue
                fingerprint = hashlib.sha256(
                    f"{source_id}|{_signal_fingerprint(row)}".encode("utf-8")
                ).hexdigest()
                existing = conn.execute(
                    """
                    SELECT signal_id FROM strategy_signals
                    WHERE source_id = ? AND fingerprint = ?
                    """,
                    (source_id, fingerprint),
                ).fetchone()
                tags = _tags_from_payload(row.get("tags"))
                payload = (
                    source_id,
                    fingerprint,
                    symbol,
                    _clean_text(row.get("name"), limit=50),
                    str(row.get("signal_type") or "insight")[:80],
                    str(row.get("direction") or "neutral")[:24],
                    _score_0_100(row.get("strength"), default=55),
                    _clean_text(row.get("summary"), limit=300),
                    str(row.get("as_of") or "")[:80],
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    now,
                )
                if existing is not None:
                    conn.execute(
                        """
                        UPDATE strategy_signals
                        SET name = ?, signal_type = ?, direction = ?, strength = ?,
                            summary = ?, as_of = ?, tags_json = ?, raw_json = ?,
                            collected_at = ?, updated_at = ?
                        WHERE signal_id = ?
                        """,
                        (
                            payload[3],
                            payload[4],
                            payload[5],
                            payload[6],
                            payload[7],
                            payload[8],
                            payload[9],
                            payload[10],
                            payload[11],
                            now,
                            int(existing["signal_id"]),
                        ),
                    )
                    skipped += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO strategy_signals (
                        source_id, fingerprint, symbol, name, signal_type, direction,
                        strength, summary, as_of, tags_json, raw_json, collected_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
                inserted += 1
        return {
            "status": "ok",
            "inserted": inserted,
            "skipped": skipped,
            "input_rows": len(rows),
            "db_path": self.db_path,
        }

    def list_signals(
        self,
        *,
        source_id: str = "",
        symbol: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        max_rows = max(1, min(int(limit or 200), 5000))
        where: list[str] = []
        params: list[Any] = []
        if source_id:
            where.append("source_id = ?")
            params.append(source_id)
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if date_from:
            where.append("as_of >= ?")
            params.append(date_from)
        if date_to:
            where.append("as_of <= ?")
            params.append(date_to)
        sql = "SELECT * FROM strategy_signals"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY as_of DESC, signal_id DESC LIMIT ?"
        params.append(max_rows)
        with self._connect() as conn:
            return [self._row_to_signal(row) for row in conn.execute(sql, params)]

    def source_summary(
        self,
        *,
        source_id: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total_count, MAX(as_of) AS latest_as_of,
                       MAX(collected_at) AS latest_collected_at
                FROM strategy_signals
                WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        signals = self.list_signals(source_id=source_id, limit=limit)
        return {
            "total_count": int((row or {})["total_count"] or 0) if row else 0,
            "latest_as_of": str((row or {})["latest_as_of"] or "") if row else "",
            "latest_collected_at": str((row or {})["latest_collected_at"] or "")
            if row
            else "",
            "signals": signals,
        }

    def prune_history(
        self,
        *,
        retention_days: int = 45,
        signal_row_cap_per_symbol: int = 96,
        now_iso: str | None = None,
        vacuum: bool = True,
    ) -> dict[str, Any]:
        days = int(retention_days)
        if days <= 0:
            return {
                "status": "skipped",
                "reason": "retention_disabled",
                "retention_days": days,
                "deleted": {},
                "vacuumed": False,
            }
        try:
            base_now = (
                datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
                if now_iso
                else datetime.now(timezone.utc)
            )
        except ValueError:
            base_now = datetime.now(timezone.utc)
        if base_now.tzinfo is None:
            base_now = base_now.replace(tzinfo=timezone.utc)
        cutoff = (base_now - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            deleted = int(
                conn.execute(
                    "DELETE FROM strategy_signals WHERE as_of < ?",
                    (cutoff,),
                ).rowcount
                or 0
            )
            capped_deleted = self._delete_repeated_signal_rows(
                conn,
                row_cap_per_symbol=signal_row_cap_per_symbol,
            )
        vacuumed = False
        if vacuum and (deleted or capped_deleted):
            with sqlite3.connect(self.db_path, isolation_level=None) as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "retention_days": days,
            "cutoff": cutoff,
            "signal_row_cap_per_symbol": int(signal_row_cap_per_symbol),
            "deleted": {
                "strategy_signals": deleted,
                "strategy_signals_capped": capped_deleted,
            },
            "vacuumed": vacuumed,
        }

    @staticmethod
    def _delete_repeated_signal_rows(
        conn: sqlite3.Connection,
        *,
        row_cap_per_symbol: int,
    ) -> int:
        cap = int(row_cap_per_symbol)
        if cap <= 0:
            return 0
        return int(
            conn.execute(
                """
                DELETE FROM strategy_signals
                WHERE signal_id IN (
                    SELECT signal_id
                    FROM (
                        SELECT
                            signal_id,
                            ROW_NUMBER() OVER (
                                PARTITION BY source_id, signal_type, symbol
                                ORDER BY as_of DESC, signal_id DESC
                            ) AS row_rank
                        FROM strategy_signals
                    )
                    WHERE row_rank > ?
                )
                """,
                (cap,),
            ).rowcount
            or 0
        )

    def migrate_jsonl(
        self,
        *,
        source_id: str,
        path: str,
    ) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            return {
                "status": "skipped",
                "reason": "source_file_missing",
                "path": str(file_path),
                "inserted": 0,
                "skipped": 0,
            }
        rows = _signal_rows_from_text(file_path.read_text(encoding="utf-8"))
        normalized = [
            {**row, "source_id": str(row.get("source_id") or source_id)}
            for row in rows
            if isinstance(row, dict)
        ]
        result = self.upsert_signals(normalized)
        result["path"] = str(file_path)
        return result


_JS_FIELD_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
    r"(?:'((?:\\.|[^'])*)'|\"((?:\\.|[^\"])*)\"|(-?\d+(?:\.\d+)?|true|false|null))",
    re.IGNORECASE,
)


def _decode_js_field(value: str) -> str:
    return (
        str(value or "")
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
    )


def _parse_js_object_array(text: str, array_name: str) -> list[dict[str, str]]:
    match = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(array_name)}\s*=\s*\[(.*?)\]\s*;?",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return []
    rows: list[dict[str, str]] = []
    for object_match in re.finditer(r"\{(.*?)\}", match.group(1), flags=re.DOTALL):
        row: dict[str, str] = {}
        for field_match in _JS_FIELD_RE.finditer(object_match.group(1)):
            key = field_match.group(1)
            value = next(
                (
                    group
                    for group in field_match.groups()[1:]
                    if group is not None
                ),
                "",
            )
            row[key] = _decode_js_field(value)
        if row:
            rows.append(row)
    return rows


def _whale_major_rows_to_signals(
    rows: list[dict[str, Any]],
    *,
    symbol_by_name: dict[str, str],
    limit: int,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for row in rows[:limit]:
        name = _clean_text(row.get("company"), limit=50)
        symbol = symbol_by_name.get(_compact(name), "")
        if not name or not _is_candidate_symbol(symbol):
            continue
        pct_delta = _safe_float(row.get("stkrt_irds"))
        share_delta = _safe_float(row.get("stkqy_irds"))
        direction = "neutral"
        if pct_delta > 0 or share_delta > 0:
            direction = "positive"
        elif pct_delta < 0 or share_delta < 0:
            direction = "negative"
        strength = max(
            45,
            min(
                95,
                int(
                    round(
                        55
                        + min(abs(pct_delta) * 7.0, 30.0)
                        + min(abs(share_delta) / 1_000_000.0, 10.0)
                    )
                ),
            ),
        )
        summary = (
            f"Whale Insight 5% 지분 변동: {name} 지분율 {row.get('stkrt') or '-'}%, "
            f"변동 {row.get('stkrt_irds') or '-'}%p, 주식수 변동 {row.get('stkqy_irds') or '-'}주, "
            f"사유 {row.get('report_resn') or '-'}"
        )
        signals.append(
            {
                "symbol": symbol,
                "name": name,
                "signal_type": "large_holder_change",
                "direction": direction,
                "strength": strength,
                "summary": summary,
                "as_of": str(row.get("date") or _now_iso()),
                "tags": ["whale", "dart", "major_holder"],
            }
        )
    return signals


def _sesiban_leading_payload_to_signals(
    payload: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    sectors = payload.get("sectors") if isinstance(payload, dict) else []
    if not isinstance(sectors, list):
        return []
    generated_at = str(payload.get("generated_at") or _now_iso())
    signals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sector in sectors:
        if not isinstance(sector, dict):
            continue
        sector_name = _clean_text(sector.get("name"), limit=50)
        sector_rank = _safe_int(sector.get("rank"))
        intensity = _safe_float(sector.get("intensity"))
        weighted_return = _safe_float(sector.get("weighted_return_pct"))
        stocks = sector.get("leading_stocks")
        if not isinstance(stocks, list):
            continue
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            symbol = str(stock.get("symbol") or "").strip()
            if not _is_candidate_symbol(symbol) or symbol in seen:
                continue
            seen.add(symbol)
            name = _clean_text(stock.get("name"), limit=50)
            change_rate = _safe_float(stock.get("change_rate"))
            contribution = _safe_float(stock.get("contribution_pct"))
            direction = "positive" if change_rate >= 0 else "negative"
            strength = max(
                45,
                min(
                    95,
                    int(
                        round(
                            50
                            + min(abs(change_rate) * 1.1, 22.0)
                            + min(contribution * 0.7, 18.0)
                            + min(max(intensity, 0.0) * 8.0, 15.0)
                        )
                    ),
                ),
            )
            summary = (
                f"세시반 선도 섹터 {sector_rank or '-'}위 {sector_name}: "
                f"{name} 등락률 {change_rate:.2f}%, 거래대금 기여 {contribution:.2f}%, "
                f"섹터 수익률 {weighted_return:.2f}%"
            )
            signals.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "signal_type": "sector_treemap",
                    "direction": direction,
                    "strength": strength,
                    "summary": summary,
                    "as_of": generated_at,
                    "tags": ["sesiban", "sector_treemap", "after_close_flow"],
                }
            )
            if len(signals) >= limit:
                return signals
    return signals


class StrategyInsightCollector:
    def __init__(
        self,
        *,
        engine: "StrategyIntelligenceEngine",
        sources: list[dict[str, Any]] | None,
        timeout_sec: float = 10.0,
    ) -> None:
        self.engine = engine
        self.sources = [dict(row) for row in (sources or []) if isinstance(row, dict)]
        self.timeout_sec = max(float(timeout_sec or 10.0), 1.0)

    async def collect_once(self) -> dict[str, Any]:
        active_sources = [row for row in self.sources if _bool_value(row.get("enabled"), True)]
        if not active_sources:
            return {
                "status": "skipped",
                "reason": "no_sources_configured",
                "updated_at": _now_iso(),
                "inserted": 0,
                "sources": [],
                "errors": [],
            }

        inserted = 0
        errors: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        known_keys = self._known_signal_keys()

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            for source in active_sources:
                source_id = str(source.get("source_id") or source.get("id") or "").strip()
                summary = {
                    "source_id": source_id,
                    "label": str(source.get("label") or source_id or "source").strip(),
                    "loaded": 0,
                    "inserted": 0,
                    "skipped": 0,
                    "status": "pending",
                }
                if not source_id:
                    summary["status"] = "error"
                    errors.append({"source_id": "", "detail": "source_id is required"})
                    summaries.append(summary)
                    continue

                try:
                    rows = await self._load_source(source, client=client, summary=summary)
                except Exception as exc:
                    summary["status"] = "error"
                    errors.append({"source_id": source_id, "detail": str(exc)})
                    summaries.append(summary)
                    continue

                summary["loaded"] = len(rows)
                source_inserted = 0
                source_error_count = len(errors)
                dedupe = _bool_value(source.get("dedupe"), True)
                source_keys = known_keys.setdefault(source_id, set())
                for row in rows:
                    key = _signal_fingerprint(row)
                    if dedupe and key and key in source_keys:
                        summary["skipped"] += 1
                        continue
                    try:
                        result = self.engine.append_external_signals(
                            source_id=source_id,
                            payload=row,
                        )
                    except ValueError as exc:
                        errors.append({"source_id": source_id, "detail": str(exc)})
                        continue
                    row_inserted = int(result.get("inserted") or 0)
                    if row_inserted <= 0:
                        summary["skipped"] += 1
                    source_inserted += row_inserted
                    inserted += row_inserted
                    if key:
                        source_keys.add(key)

                summary["inserted"] = source_inserted
                if len(errors) > source_error_count:
                    summary["status"] = "partial" if source_inserted else "error"
                else:
                    summary["status"] = "ok" if rows else "empty"
                summaries.append(summary)

        status = "ok"
        if errors and inserted:
            status = "partial"
        elif errors:
            status = "error"
        return {
            "status": status,
            "updated_at": _now_iso(),
            "inserted": inserted,
            "sources": summaries,
            "errors": errors[:20],
        }

    async def _load_source(
        self,
        source: dict[str, Any],
        *,
        client: httpx.AsyncClient,
        summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        source_kind = str(source.get("kind") or source.get("type") or "").strip().lower()
        if source_kind in {"whale_insight_static", "whale_static"}:
            return await self._load_whale_insight_static_source(
                source,
                client=client,
                summary=summary,
            )
        if source_kind in {"sesiban_leading", "sesiban_public"}:
            return await self._load_sesiban_leading_source(
                source,
                client=client,
                summary=summary,
            )

        if "signals" in source:
            return _signal_rows_from_payload(source.get("signals"))
        if "payload" in source:
            return _signal_rows_from_payload(source.get("payload"))
        if "text" in source:
            return _signal_rows_from_text(str(source.get("text") or ""))

        path_value = str(source.get("path") or "").strip()
        if path_value:
            path = Path(path_value).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"source path not found: {path}")
            return _signal_rows_from_text(path.read_text(encoding="utf-8"))

        url = str(source.get("url") or "").strip()
        if url:
            response = await client.get(url)
            response.raise_for_status()
            return _signal_rows_from_text(response.text)

        return []

    async def _load_whale_insight_static_source(
        self,
        source: dict[str, Any],
        *,
        client: httpx.AsyncClient,
        summary: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        source_id = str(source.get("source_id") or "whale_insight")
        url = str(source.get("url") or "https://whale-insight.com/major_stock").strip()
        cache_path = _collector_cache_path(
            source,
            ".runtime/cache/whale_insight_public_signals.json",
        )
        try:
            script_text = await self._fetch_whale_major_script(url, client=client)
            major_rows = _parse_js_object_array(script_text, "MAJOR_DATA")
            if not major_rows:
                raise ValueError("Whale Insight MAJOR_DATA rows not found")
            limit = _source_limit(source, 40)
            symbol_cache_path = Path(
                str(
                    source.get("symbol_cache_path")
                    or ".runtime/cache/strategy_insight_symbol_cache.json"
                )
            ).expanduser()
            symbol_cache = _read_symbol_cache(symbol_cache_path)
            unresolved: list[str] = []
            for row in major_rows[:limit]:
                name = _clean_text(row.get("company"), limit=50)
                if not name:
                    continue
                key = _compact(name)
                if _is_candidate_symbol(symbol_cache.get(key)):
                    continue
                symbol = self._resolve_symbol_from_repository(name)
                if not symbol:
                    symbol = await self._resolve_symbol_from_public_search(
                        name,
                        source=source,
                        client=client,
                    )
                if symbol:
                    symbol_cache[key] = symbol
                else:
                    unresolved.append(name)
            _write_symbol_cache(symbol_cache_path, symbol_cache)
            rows = _whale_major_rows_to_signals(
                major_rows,
                symbol_by_name=symbol_cache,
                limit=limit,
            )
            if summary is not None:
                summary["cache"] = "updated"
                if unresolved:
                    summary["unresolved"] = len(unresolved)
                    summary["warnings"] = [
                        f"종목코드 매핑 실패 {len(unresolved)}건: {', '.join(unresolved[:5])}"
                    ]
            _write_signal_cache(cache_path, rows, source_id=source_id, url=url)
            return rows
        except Exception as exc:
            cached = _read_signal_cache(cache_path)
            if cached:
                if summary is not None:
                    summary["cache"] = "fallback"
                    summary["warnings"] = [
                        f"실시간 수집 실패로 캐시 사용: {exc}"
                    ]
                return cached
            raise

    async def _fetch_whale_major_script(
        self,
        url: str,
        *,
        client: httpx.AsyncClient,
    ) -> str:
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
        if re.search(r"\b(?:const|let|var)\s+MAJOR_DATA\s*=", text):
            return text
        match = re.search(
            r"src=[\"']([^\"']*nps_major_stock\.js[^\"']*)[\"']",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            raise ValueError("Whale Insight major stock script URL not found")
        script_url = urljoin(url, match.group(1))
        script_response = await client.get(script_url)
        script_response.raise_for_status()
        return script_response.text

    def _resolve_symbol_from_repository(self, name: str) -> str:
        repository = getattr(self.engine, "repository", None)
        resolve_from_text = getattr(repository, "resolve_symbol_from_text", None)
        if callable(resolve_from_text):
            try:
                resolved = resolve_from_text(name)
            except Exception:
                resolved = None
            if isinstance(resolved, dict):
                symbol = str(resolved.get("symbol") or "").strip()
                if _is_candidate_symbol(symbol):
                    return symbol
        search = getattr(repository, "search", None)
        if not callable(search):
            return ""
        try:
            rows = search(query=name, limit=8)
        except Exception:
            return ""
        name_key = _compact(name)
        canonical_name_key = _corporate_name_key(name)
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            if not _is_candidate_symbol(symbol):
                continue
            row_name = row.get("company_name") or row.get("name") or row.get("title")
            row_key = _compact(row_name)
            if row_key == name_key:
                return symbol
            if canonical_name_key and _corporate_name_key(row_name) == canonical_name_key:
                return symbol
        return ""

    async def _resolve_symbol_from_public_search(
        self,
        name: str,
        *,
        source: dict[str, Any],
        client: httpx.AsyncClient,
    ) -> str:
        search_url = str(
            source.get("symbol_search_url")
            or "https://www.sesiban.site/api/v1/assets"
        ).strip()
        response = await client.get(
            search_url,
            params={
                "search": name,
                "page": 1,
                "size": 5,
                "sort_by": "market_cap",
                "sort_order": "desc",
                "asset_type": "ALL",
            },
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return ""
        name_key = _compact(name)
        fallback = ""
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip()
            if not _is_candidate_symbol(symbol):
                continue
            item_name = _clean_text(item.get("name"), limit=50)
            market = str(item.get("market") or "").upper()
            if market and market not in {"KOSPI", "KOSDAQ", "KONEX", "KR"}:
                continue
            if _compact(item_name) == name_key:
                return symbol
            if not fallback:
                fallback = symbol
        return fallback

    async def _load_sesiban_leading_source(
        self,
        source: dict[str, Any],
        *,
        client: httpx.AsyncClient,
        summary: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        source_id = str(source.get("source_id") or "after_close_330")
        url = str(
            source.get("url")
            or "https://www.sesiban.site/api/v1/rankings/leading?market=KR"
        ).strip()
        cache_path = _collector_cache_path(
            source,
            ".runtime/cache/sesiban_public_signals.json",
        )
        try:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            rows = _sesiban_leading_payload_to_signals(
                payload,
                limit=_source_limit(source, 40),
            )
            if summary is not None:
                summary["cache"] = "updated"
                if not rows:
                    summary["warnings"] = ["세시반 선도 섹터 응답에 후보 종목이 없습니다."]
            _write_signal_cache(cache_path, rows, source_id=source_id, url=url)
            return rows
        except Exception as exc:
            cached = _read_signal_cache(cache_path)
            if cached:
                if summary is not None:
                    summary["cache"] = "fallback"
                    summary["warnings"] = [
                        f"실시간 수집 실패로 캐시 사용: {exc}"
                    ]
                return cached
            raise

    def _known_signal_keys(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        try:
            statuses = self.engine.source_status()
        except Exception:
            return out
        for status in statuses:
            source_id = str(status.get("source_id") or "").strip()
            if not source_id:
                continue
            keys = out.setdefault(source_id, set())
            for row in list(status.get("signals") or []):
                if isinstance(row, dict):
                    key = _signal_fingerprint(row)
                    if key:
                        keys.add(key)
        return out


class StrategyIntelligenceEngine:
    def __init__(
        self,
        *,
        repository: ReportRepository,
        rag_store: RAGQueryStore | None,
        codex_runtime: CodexNativeRuntime,
        fundamentals_repository: FundamentalsRepository | None = None,
        etf_research_repository: ETFResearchProvider | None = None,
        config: StrategyIntelligenceConfig | None = None,
    ) -> None:
        self.repository = repository
        self.rag_store = rag_store
        self.codex_runtime = codex_runtime
        self.fundamentals_repository = fundamentals_repository
        self.etf_research_repository = etf_research_repository
        self.config = config or StrategyIntelligenceConfig()
        self._brief_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.insight_repository = (
            StrategyInsightRepository(self.config.insight_db_path)
            if self.config.insight_db_path
            else None
        )
        self.sources = [
            JSONLInsightSource(
                ExternalInsightSourceConfig(
                    source_id="whale_insight",
                    label="Whale Insight",
                    role="국민연금/5% 대량보유/큰손 포지션 변화를 후보 검증 신호로 사용",
                    path=self.config.whale_insight_path,
                    signal_types=[
                        "large_holder_change",
                        "institutional_position",
                        "legend_portfolio",
                    ],
                    coverage=["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"],
                    caution="자동화 수집 기반 참고 데이터로 오류 가능성을 전제로 교차검증",
                ),
                repository=self.insight_repository,
            ),
            JSONLInsightSource(
                ExternalInsightSourceConfig(
                    source_id="after_close_330",
                    label="세시반",
                    role="장마감 후 수급, 섹터 트리맵, 종가 후보를 다음 거래일 관심 신호로 사용",
                    path=self.config.sesiban_path,
                    signal_types=[
                        "after_close_flow",
                        "sector_treemap",
                        "closing_candidate",
                    ],
                    coverage=["KOSPI", "KOSDAQ"],
                    caution="다음 거래일 블록 매매 후보를 좁히는 당일 수급/섹터 운영 신호",
                ),
                repository=self.insight_repository,
            ),
        ]
        self._migrate_existing_jsonl_signals()

    def _migrate_existing_jsonl_signals(self) -> None:
        if self.insight_repository is None:
            return
        if not bool(self.config.migrate_legacy_jsonl):
            return
        for source in self.sources:
            try:
                summary = self.insight_repository.source_summary(
                    source_id=source.config.source_id,
                    limit=1,
                )
                if int(summary.get("total_count") or 0) > 0:
                    continue
                self.insight_repository.migrate_jsonl(
                    source_id=source.config.source_id,
                    path=source.config.path,
                )
            except Exception as exc:
                logger.warning(
                    "strategy insight legacy JSONL migration skipped for %s: %s",
                    source.config.source_id,
                    exc,
                )

    def compact_legacy_jsonl_sidecars(
        self,
        *,
        max_lines_per_source: int | None = None,
    ) -> dict[str, Any]:
        if self.insight_repository is None:
            return {
                "status": "skipped",
                "reason": "sqlite_storage_disabled",
                "sources": [],
            }
        limit = int(
            max_lines_per_source
            if max_lines_per_source is not None
            else self.config.legacy_jsonl_sidecar_max_lines
        )
        if limit <= 0:
            return {
                "status": "skipped",
                "reason": "sidecar_compaction_disabled",
                "sources": [],
            }

        sources: list[dict[str, Any]] = []
        for source in self.sources:
            path = Path(source.config.path)
            rows = self.insight_repository.list_signals(
                source_id=source.config.source_id,
                limit=limit,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            old_bytes = path.stat().st_size if path.exists() else 0
            with path.open("w", encoding="utf-8") as handle:
                for row in reversed(rows):
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            new_bytes = path.stat().st_size if path.exists() else 0
            sources.append(
                {
                    "source_id": source.config.source_id,
                    "path": str(path),
                    "rows": len(rows),
                    "old_bytes": old_bytes,
                    "new_bytes": new_bytes,
                    "bytes_saved": max(old_bytes - new_bytes, 0),
                }
            )
        return {
            "status": "ok",
            "max_lines_per_source": limit,
            "sources": sources,
        }

    def source_status(self) -> list[dict[str, Any]]:
        return [source.read(limit=200) for source in self.sources]

    def list_external_signals(
        self,
        *,
        source_id: str = "",
        symbol: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> dict[str, Any]:
        normalized_source = ""
        if source_id:
            normalized_source = self._resolve_source(source_id).config.source_id
        if self.insight_repository is not None:
            rows = self.insight_repository.list_signals(
                source_id=normalized_source,
                symbol=symbol,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
            items = [
                _decorate_external_signal_freshness(
                    str(row.get("source_id") or normalized_source), row
                )
                for row in rows
            ]
            return {
                "status": "ok",
                "storage": "sqlite",
                "db_path": self.insight_repository.db_path,
                "count": len(items),
                "items": items,
            }

        items: list[dict[str, Any]] = []
        for source in self.sources:
            if normalized_source and source.config.source_id != normalized_source:
                continue
            for row in list(source.read(limit=limit).get("signals") or []):
                if symbol and str(row.get("symbol") or "") != symbol:
                    continue
                as_of = str(row.get("as_of") or "")
                if date_from and as_of < date_from:
                    continue
                if date_to and as_of > date_to:
                    continue
                items.append(
                    _decorate_external_signal_freshness(source.config.source_id, row)
                )
        items.sort(key=lambda row: str(row.get("as_of") or ""), reverse=True)
        max_rows = max(1, min(int(limit or 200), 5000))
        return {
            "status": "ok",
            "storage": "jsonl",
            "count": len(items[:max_rows]),
            "items": items[:max_rows],
        }

    def _resolve_source(self, source_id: str) -> JSONLInsightSource:
        aliases = {
            "whale": "whale_insight",
            "whale_insight": "whale_insight",
            "sesiban": "after_close_330",
            "three_thirty": "after_close_330",
            "after_close": "after_close_330",
            "after_close_330": "after_close_330",
        }
        normalized = aliases.get(str(source_id or "").strip().lower(), "")
        for source in self.sources:
            if source.config.source_id == normalized:
                return source
        raise ValueError(f"unknown strategy insight source: {source_id}")

    def append_external_signals(
        self,
        *,
        source_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        source = self._resolve_source(source_id)
        raw_rows = payload.get("signals") if isinstance(payload.get("signals"), list) else [payload]
        rows = [
            self._normalize_external_signal(source.config, row)
            for row in raw_rows
            if isinstance(row, dict)
        ]
        if not rows:
            raise ValueError("at least one signal object is required")

        db_result: dict[str, Any] | None = None
        if self.insight_repository is not None:
            db_result = self.insight_repository.upsert_signals(rows)

        path = Path(source.config.path)
        if self.insight_repository is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        status = source.read(limit=200)
        inserted = (
            int(db_result.get("inserted") or 0)
            if db_result is not None
            else len(rows)
        )
        return {
            "status": "ok",
            "source_id": source.config.source_id,
            "inserted": inserted,
            "skipped": int((db_result or {}).get("skipped") or 0),
            "path": str(path),
            "db_path": (db_result or {}).get("db_path"),
            "source": {
                "status": status.get("status"),
                "count": status.get("count"),
                "label": status.get("label"),
            },
            "signals": rows,
        }

    def _normalize_external_signal(
        self,
        config: ExternalInsightSourceConfig,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or payload.get("code") or "").strip()
        if not _is_candidate_symbol(symbol):
            raise ValueError("symbol must be a valid 6-digit KRX code")
        direction = str(
            payload.get("direction")
            or payload.get("sentiment")
            or "neutral"
        ).strip().lower()
        if direction not in {
            "positive",
            "negative",
            "neutral",
            "bullish",
            "bearish",
            "buy",
            "sell",
            "up",
            "down",
        }:
            direction = "neutral"

        raw_tags = payload.get("tags") or []
        if isinstance(raw_tags, str):
            tags = [raw_tags]
        elif isinstance(raw_tags, list):
            tags = [str(item) for item in raw_tags if str(item).strip()]
        else:
            tags = []

        summary = _clean_text(
            payload.get("summary")
            or payload.get("note")
            or payload.get("reason")
            or "",
            limit=240,
        )
        if not summary:
            summary = f"{config.label} {direction} signal"

        return {
            "schema_version": 1,
            "source_id": config.source_id,
            "symbol": symbol,
            "name": _clean_text(payload.get("name") or payload.get("company"), limit=50),
            "signal_type": str(
                payload.get("signal_type")
                or payload.get("type")
                or config.signal_types[0]
            ),
            "direction": direction,
            "strength": _score_0_100(
                payload.get("strength")
                or payload.get("score")
                or payload.get("confidence"),
                default=55,
            ),
            "summary": summary,
            "as_of": str(
                payload.get("as_of")
                or payload.get("published_at")
                or payload.get("updated_at")
                or _now_iso()
            ),
            "tags": tags[:6],
        }

    def _research_pick_signals(self, research_feed: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        if not isinstance(research_feed, dict):
            return out

        def ensure_item(code: str) -> dict[str, Any]:
            return out.setdefault(
                code,
                {
                    "symbol": code,
                    "name": "",
                    "score": 0.0,
                    "confidence": 0,
                    "reasons": [],
                    "risks": [],
                    "facts": [],
                    "published_dates": [],
                    "sources": set(),
                },
            )

        for row in list(research_feed.get("items") or [])[:30]:
            if not isinstance(row, dict):
                continue
            picks = list(row.get("picks") or [])
            for symbol in picks:
                code = str(symbol or "").strip()
                if not _is_candidate_symbol(code):
                    continue
                item = ensure_item(code)
                item["score"] += 8.0
                item["sources"].add(str(row.get("source") or "research_feed"))
                title = _clean_text(row.get("title"), limit=70)
                if title:
                    item["reasons"].append(f"리서치 후보군에 포함: {title}")
                published = str(row.get("published_at") or row.get("as_of") or "")
                if published:
                    item["published_dates"].append(published)

        discovery = research_feed.get("daily_discovery")
        if isinstance(discovery, dict):
            trading_day = str(
                discovery.get("trading_day")
                or discovery.get("date")
                or discovery.get("generated_at")
                or "",
            )
            for row in list(discovery.get("items") or [])[:40]:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("symbol") or row.get("code") or "").strip()
                if not _is_candidate_symbol(code):
                    continue
                analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
                item = ensure_item(code)
                name = _sanitize_subject_name(
                    row.get("name") or analysis.get("name"),
                    symbol=code,
                )
                if name:
                    item["name"] = name

                raw_score = _safe_float(row.get("score") or analysis.get("score"))
                stance = str(analysis.get("stance") or row.get("stance") or "").lower()
                if raw_score <= 0:
                    raw_score = 60.0
                if stance in {"block_candidate", "create", "buy", "enter"}:
                    score_delta = 14.0
                elif stance in {"confirm", "watch", "watchlist"}:
                    score_delta = 8.0 + max(0.0, min(6.0, (raw_score - 55.0) / 6.0))
                elif stance in {"risk_check", "caution"}:
                    score_delta = 5.0 + max(0.0, min(3.0, (raw_score - 55.0) / 10.0))
                else:
                    score_delta = 6.0 + max(0.0, min(5.0, (raw_score - 55.0) / 8.0))
                item["score"] += score_delta
                confidence_raw = _safe_float(analysis.get("confidence") or row.get("confidence"))
                if 0 < confidence_raw <= 1:
                    confidence_raw *= 100
                item["confidence"] = max(int(item.get("confidence") or 0), _clamp_score(confidence_raw))
                item["sources"].add("daily_discovery")
                if trading_day:
                    item["published_dates"].append(trading_day)

                summary = _clean_text(analysis.get("summary") or row.get("summary"), limit=130)
                if summary:
                    item["reasons"].append(f"데일리 디스커버리: {summary}")
                    item["facts"].append(summary)
                for reason in list(analysis.get("reasons") or row.get("reasons") or [])[:3]:
                    clean = _clean_text(reason, limit=120)
                    if clean:
                        item["reasons"].append(clean)
                        item["facts"].append(clean)
                for risk in list(analysis.get("risks") or row.get("risks") or [])[:3]:
                    clean = _clean_text(risk, limit=120)
                    if clean:
                        item["risks"].append(clean)
        return out

    def _external_signals(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        statuses = self.source_status()
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for status in statuses:
            if bool(status.get("stale")) or str(status.get("status") or "") == "stale":
                continue
            source_id = str(status.get("source_id") or "")
            for signal in list(status.get("signals") or []):
                signal_source_id = str(
                    signal.get("source_id") or signal.get("source") or source_id
                )
                if _is_external_signal_stale(signal_source_id, signal):
                    continue
                symbol = str(signal.get("symbol") or "").strip()
                if not _is_candidate_symbol(symbol):
                    continue
                by_symbol.setdefault(symbol, []).append(signal)
        return statuses, by_symbol

    def _collect_report_candidates(self, *, query: str) -> dict[str, dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        rows.extend(
            self.repository.search(
                query="",
                category="company_analysis",
                limit=self.config.max_report_scan,
            )
        )
        rows.extend(
            self.repository.search(
                query="반도체 AI 실적 목표주가 상향 수급",
                category="industry_analysis",
                limit=max(self.config.max_report_scan // 3, 20),
            )
        )
        by_symbol: dict[str, dict[str, Any]] = {}
        seen_report_ids: set[int] = set()
        for row in rows:
            report_id = int(row.get("report_id") or 0)
            if report_id <= 0 or report_id in seen_report_ids:
                continue
            seen_report_ids.add(report_id)
            report = self.repository.get_report(report_id) or {}
            content = _clean_text(report.get("content") or row.get("snippet"), limit=2400)
            symbol, name, exact = _extract_report_subject(row, content)
            if not symbol:
                continue
            facts = self.repository.get_report_facts(report_id) or {}
            item = by_symbol.setdefault(
                symbol,
                _candidate_item(symbol, name or symbol, 40),
            )
            if item["name"] == symbol and name != symbol:
                item["name"] = name
            _add_score(item, "report", 6.0 if exact else 1.5)
            item["confidence"] = max(int(item["confidence"]), 68 if exact else 48)
            item["sources"].add("naver_reports")
            item["report_ids"].append(report_id)
            broker = str(row.get("broker") or "")
            published = str(row.get("published_at") or "")
            if published:
                item.setdefault("published_dates", []).append(published)
            item["citations"].append(f"[{broker or '-'}, {published or '-'}, p.?]")

            category = str(row.get("category") or "")
            if category == "company_analysis":
                _add_score(item, "report", 7.0)
            elif category == "industry_analysis":
                _add_score(item, "report", 3.5)

            rating = str(facts.get("rating") or "").upper()
            if rating == "BUY":
                _add_score(item, "report", 14.0)
                item["reasons"].append("최신 리포트 투자의견 BUY")
            elif rating == "HOLD":
                _add_score(item, "report", 2.0)
                item["checks"].append("투자의견 HOLD 근거 확인")
            elif rating == "SELL":
                _add_score(item, "risk", -18.0)
                item["risks"].append("SELL 의견 리포트 존재")

            target = facts.get("target_price") if isinstance(facts, dict) else {}
            if isinstance(target, dict):
                target_value = _safe_int(target.get("value"))
                target_changed = str(target.get("changed") or "").upper()
                target_value_trustworthy = target_value == 0 or target_value >= 1000
                if target_value >= 1000:
                    _add_score(item, "report", 5.0)
                    item["reasons"].append(f"목표주가 {target_value:,} KRW 근거 존재")
                elif target_value > 0:
                    item["checks"].append("목표주가 단위/OCR 확인 필요")
                if target_changed == "UP" and target_value_trustworthy:
                    _add_score(item, "report", 9.0)
                    item["reasons"].append("목표주가 상향 신호")
                elif target_changed == "DOWN" and target_value_trustworthy:
                    _add_score(item, "risk", -7.0)
                    item["risks"].append("목표주가 하향 신호")

            text = " ".join(
                [
                    content,
                    " ".join(str(x) for x in list(facts.get("summary_bullets") or [])),
                    " ".join(str(x) for x in list(facts.get("investment_thesis") or [])),
                    query,
                ]
            ).lower()
            positive_hits = [word for word in _POSITIVE_WORDS if word in text]
            negative_hits = [word for word in _NEGATIVE_WORDS if word in text]
            if positive_hits:
                _add_score(item, "report", min(len(positive_hits), 5) * 2.5)
                item["reasons"].append(f"긍정 키워드: {', '.join(positive_hits[:4])}")
                growth_hits = [
                    word
                    for word in positive_hits
                    if word
                    in {
                        "ai",
                        "hbm",
                        "eps",
                        "실적",
                        "이익",
                        "매출",
                        "영업이익",
                        "성장",
                        "가격 상승",
                    }
                ]
                if growth_hits:
                    _add_score(item, "growth", min(len(growth_hits), 4) * 2.5)
            if negative_hits:
                _add_score(item, "risk", min(len(negative_hits), 4) * -1.5)
                item["risks"].append(f"리스크 키워드: {', '.join(negative_hits[:4])}")

            for bullet in list(facts.get("summary_bullets") or [])[:2]:
                clean = _clean_text(bullet, limit=120)
                if clean and not _is_low_quality_evidence_text(clean, min_chars=14):
                    item["facts"].append(clean)
            for risk in list(facts.get("risks") or [])[:2]:
                clean = _clean_text(risk, limit=120)
                if clean and not _is_low_quality_evidence_text(clean, min_chars=8):
                    item["risks"].append(clean)
                    _add_score(item, "risk", -0.6)

        self._canonicalize_candidate_names(by_symbol)
        return by_symbol

    def _etf_universe(self) -> list[dict[str, Any]]:
        if self.etf_research_repository is None:
            return []
        try:
            universe = self.etf_research_repository.list_universe()
        except Exception:
            return []
        return [row for row in universe if isinstance(row, dict)]

    def _linked_etf_reports(self, symbol: str, *, limit: int = 20) -> list[dict[str, Any]]:
        linked_reports = getattr(self.repository, "latest_symbol_linked_reports", None)
        if callable(linked_reports):
            try:
                rows = linked_reports(symbol, limit=limit)
            except Exception:
                rows = []
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]
        try:
            rows = self.repository.search(query="", symbol=symbol, limit=limit)
        except Exception:
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _collect_etf_report_candidates(
        self,
        universe: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        by_symbol: dict[str, dict[str, Any]] = {}
        seen_pairs: set[tuple[str, int]] = set()
        universe_symbols = [
            str(row.get("symbol") or "").strip()
            for row in universe[: max(self.config.max_candidates * 3, 12)]
            if isinstance(row, dict) and _is_candidate_symbol(str(row.get("symbol") or "").strip())
        ]
        resolved_names: dict[str, str] = {}
        resolver = getattr(self.repository, "resolve_symbol_names", None)
        if callable(resolver) and universe_symbols:
            try:
                raw_names = resolver(universe_symbols)
            except Exception:
                raw_names = {}
            if isinstance(raw_names, dict):
                resolved_names = {
                    str(symbol): str(name)
                    for symbol, name in raw_names.items()
                    if name
                }
        for row in universe[: max(self.config.max_candidates * 3, 12)]:
            symbol = str(row.get("symbol") or "").strip()
            if not _is_candidate_symbol(symbol):
                continue
            universe_name = _clean_text(row.get("name") or symbol, limit=50)
            directory_name = _prefer_etf_display_name(
                universe_name,
                resolved_names.get(symbol),
                symbol=symbol,
            )
            reports = self._linked_etf_reports(symbol, limit=20)
            for report_row in reports:
                report_id = int(report_row.get("report_id") or 0)
                if report_id <= 0 or (symbol, report_id) in seen_pairs:
                    continue
                seen_pairs.add((symbol, report_id))
                asset_class = str(report_row.get("asset_class") or "").lower()
                linked_asset_classes = str(report_row.get("linked_asset_classes") or "").lower()
                if asset_class and asset_class != "etf" and "etf" not in linked_asset_classes:
                    continue
                report = self.repository.get_report(report_id) or {}
                content = _clean_text(
                    report.get("content")
                    or report_row.get("snippet")
                    or report_row.get("link_evidence")
                    or report_row.get("title"),
                    limit=1200,
                )
                facts = self.repository.get_report_facts(report_id) or {}
                report_name = _clean_text(
                    report_row.get("linked_name")
                    or report_row.get("company_name")
                    or "",
                    limit=50,
                )
                name = _prefer_etf_display_name(
                    report_name or symbol,
                    directory_name or universe_name or symbol,
                    symbol=symbol,
                )
                item = by_symbol.setdefault(
                    symbol,
                    _candidate_item(symbol, name or symbol, 58),
                )
                item["name"] = (
                    _prefer_etf_display_name(item.get("name"), name, symbol=symbol)
                    or symbol
                )
                item.update(
                    {
                        "asset_class": "etf",
                        "horizon_bias": "core_etf",
                        "valuation": {"status": "not_applicable", "label": "etf"},
                        "has_etf_research": False,
                    }
                )
                item["sources"].add("naver_reports")
                item["report_ids"].append(report_id)
                _add_score(item, "report", 8.0)
                item["confidence"] = max(int(item["confidence"]), 62)

                broker = str(report_row.get("broker") or "")
                published = str(report_row.get("published_at") or "")
                if published:
                    item.setdefault("published_dates", []).append(published)
                item["citations"].append(f"[{broker or '-'}, {published or '-'}, p.?]")

                title = _clean_text(report_row.get("title"), limit=90)
                if title and _is_etf_specific_evidence_text(title, symbol=symbol, name=name):
                    item["reasons"].append(f"ETF 연결 리포트: {title}")
                evidence = _clean_text(
                    report_row.get("link_evidence") or content,
                    limit=140,
                )
                if (
                    evidence
                    and not _is_low_quality_evidence_text(evidence, min_chars=10)
                    and _is_etf_specific_evidence_text(evidence, symbol=symbol, name=name)
                ):
                    item["facts"].append(evidence)
                for bullet in list(facts.get("summary_bullets") or [])[:2]:
                    clean = _clean_text(bullet, limit=120)
                    if (
                        clean
                        and not _is_low_quality_evidence_text(clean, min_chars=14)
                        and _is_etf_specific_evidence_text(clean, symbol=symbol, name=name)
                    ):
                        item["facts"].append(clean)

        for item in by_symbol.values():
            components = _candidate_component_scores(item, risks=list(item.get("risks") or []))
            item["score_components_override"] = {
                "report": min(45, components.get("report", 0)),
                "research": 0,
                "whale": 0,
                "after_close": 0,
                "valuation": 0,
                "quality": 0,
                "growth": 0,
                "risk_penalty": components.get("risk_penalty", 0),
                "risk_score": components.get("risk_score", 0),
                "recency": components.get("recency", 35),
                "evidence": min(45, components.get("evidence", 0)),
                "liquidity": 0,
                "momentum": 0,
                "core_fit": 0,
            }
        self._canonicalize_candidate_names(by_symbol)
        return by_symbol

    def _merge_candidate_item(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        if existing.get("name") == existing.get("symbol") and incoming.get("name"):
            existing["name"] = incoming["name"]
        if (
            str(existing.get("asset_class") or incoming.get("asset_class") or "").lower() == "etf"
            and incoming.get("name")
        ):
            symbol = str(existing.get("symbol") or incoming.get("symbol") or "")
            existing["name"] = _prefer_etf_display_name(
                existing.get("name"),
                incoming.get("name"),
                symbol=symbol,
            ) or str(existing.get("name") or incoming.get("name") or symbol)
        existing["confidence"] = max(
            int(existing.get("confidence") or 0),
            int(incoming.get("confidence") or 0),
        )
        for key in ("reasons", "risks", "checks", "report_ids", "citations", "facts", "published_dates"):
            existing.setdefault(key, [])
            existing[key].extend(list(incoming.get(key) or []))
        existing.setdefault("sources", set()).update(set(incoming.get("sources") or []))
        for key in ("asset_class", "horizon_bias", "valuation", "etf_snapshot", "etf_score"):
            if incoming.get(key):
                existing[key] = incoming[key]
        existing["has_etf_research"] = bool(existing.get("has_etf_research")) or bool(
            incoming.get("has_etf_research")
        )

        existing_components = dict(existing.get("components") or {})
        for key, value in dict(incoming.get("components") or {}).items():
            existing_components[key] = float(existing_components.get(key) or 0.0) + float(value or 0.0)
        existing["components"] = existing_components

        if isinstance(existing.get("score_components_override"), dict) or isinstance(
            incoming.get("score_components_override"), dict
        ):
            merged = dict(existing.get("score_components_override") or {})
            for key, value in dict(incoming.get("score_components_override") or {}).items():
                if key in {"report", "research", "whale", "after_close", "evidence"}:
                    merged[key] = min(100, int(merged.get(key) or 0) + int(value or 0))
                elif key == "recency":
                    merged[key] = max(int(merged.get(key) or 0), int(value or 0))
                elif key in {"risk_penalty", "risk_score"}:
                    merged[key] = max(int(merged.get(key) or 0), int(value or 0))
                else:
                    merged[key] = max(int(merged.get(key) or 0), int(value or 0))
            existing["score_components_override"] = merged
        return existing

    def _merge_candidate_maps(
        self,
        target: dict[str, dict[str, Any]],
        incoming: dict[str, dict[str, Any]],
    ) -> None:
        for symbol, item in incoming.items():
            if symbol in target:
                target[symbol] = self._merge_candidate_item(target[symbol], item)
            else:
                target[symbol] = item

    def _collect_etf_candidates(
        self,
        universe: list[dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if self.etf_research_repository is None:
            return {}
        if universe is None:
            universe = self._etf_universe()
        by_symbol: dict[str, dict[str, Any]] = {}
        for row in universe[: max(self.config.max_candidates * 3, 12)]:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            if not _is_candidate_symbol(symbol):
                continue
            name = _clean_text(row.get("name") or symbol, limit=50)
            snapshot: dict[str, Any] = {}
            score: dict[str, Any] = {}
            try:
                snapshot = self.etf_research_repository.latest_snapshot(symbol)
            except Exception:
                snapshot = {"status": "error", "symbol": symbol}
            try:
                score = self.etf_research_repository.latest_score(symbol)
            except Exception:
                score = {"label": "unknown", "symbol": symbol}
            if not isinstance(snapshot, dict):
                snapshot = {"status": "missing", "symbol": symbol}
            if not isinstance(score, dict):
                score = {"label": "unknown", "symbol": symbol}

            has_snapshot = str(snapshot.get("status") or "").lower() == "ok"
            score_label = str(score.get("label") or "unknown")
            has_score = score_label != "unknown" or any(
                _safe_float(score.get(key)) > 0
                for key in (
                    "liquidity_score",
                    "momentum_score",
                    "core_fit_score",
                )
            )
            if not has_snapshot and not has_score:
                continue

            liquidity = _clamp_score(score.get("liquidity_score"))
            momentum = _clamp_score(score.get("momentum_score"))
            core_fit = _clamp_score(score.get("core_fit_score"))
            risk = _clamp_score(score.get("risk_score"))
            evidence = 55 if has_score else 35
            if has_snapshot:
                evidence += 10
            components = {
                "report": 0,
                "research": 0,
                "whale": 0,
                "after_close": 0,
                "valuation": 0,
                "quality": 0,
                "growth": 0,
                "risk_penalty": risk,
                "risk_score": risk,
                "recency": 45,
                "evidence": min(evidence, 80),
                "liquidity": liquidity,
                "momentum": momentum,
                "core_fit": core_fit,
            }
            item = _candidate_item(symbol, name or symbol, 58)
            item.update(
                {
                    "asset_class": "etf",
                    "horizon_bias": "core_etf",
                    "valuation": {"status": "not_applicable", "label": "etf"},
                    "score_components_override": components,
                    "etf_snapshot": snapshot,
                    "etf_score": score,
                    "has_etf_research": has_snapshot or has_score,
                }
            )
            item["sources"].add("etf_research")
            item["facts"].extend(
                [
                    f"ETF liquidity score {liquidity}",
                    f"ETF momentum score {momentum}",
                    f"ETF core fit score {core_fit}",
                ]
            )
            for reason in list(score.get("reasons") or [])[:3]:
                clean = _clean_text(reason, limit=120)
                if clean:
                    item["reasons"].append(clean)
            for risk_text in list(score.get("risks") or [])[:3]:
                clean = _clean_text(risk_text, limit=120)
                if clean:
                    item["risks"].append(clean)
            if has_snapshot:
                change_pct = _safe_float(snapshot.get("change_pct"))
                turnover = _safe_float(snapshot.get("turnover_krw"))
                item["reasons"].append(f"ETF 최신 스냅샷 등락률 {change_pct:.2f}%")
                item["facts"].append(f"ETF turnover {turnover:,.0f} KRW")
                captured_at = str(snapshot.get("captured_at") or "")
                if captured_at:
                    item["published_dates"].append(captured_at[:10])
            if score_label in {"core_fit", "theme_momentum"}:
                item["reasons"].insert(0, f"ETF 리서치 라벨: {score_label}")
            by_symbol[symbol] = item
        return by_symbol

    def _canonicalize_candidate_names(self, by_symbol: dict[str, dict[str, Any]]) -> None:
        resolver = getattr(self.repository, "resolve_symbol_names", None)
        if not callable(resolver) or not by_symbol:
            return
        try:
            names = resolver(list(by_symbol.keys()))
        except Exception:
            return
        if not isinstance(names, dict):
            return
        for symbol, item in by_symbol.items():
            raw_resolved = names.get(symbol)
            if _has_subject_boilerplate(raw_resolved):
                continue
            if str(item.get("asset_class") or "").lower() == "etf":
                resolved = _clean_etf_display_name(raw_resolved, symbol=symbol)
                if resolved:
                    item["name"] = _prefer_etf_display_name(
                        item.get("name"),
                        resolved,
                        symbol=symbol,
                    ) or str(item.get("name") or resolved)
                continue
            resolved = _sanitize_subject_name(raw_resolved, symbol=symbol)
            if resolved:
                item["name"] = resolved

    def _candidate_valuation_payload(self, symbol: str) -> dict[str, Any]:
        if self.fundamentals_repository is None:
            return {"status": "unavailable", "label": "unknown"}
        latest_fn = getattr(self.fundamentals_repository, "latest", None)
        if not callable(latest_fn):
            return {"status": "unavailable", "label": "unknown"}
        try:
            latest = latest_fn(symbol)
        except Exception:
            return {"status": "error", "label": "unknown"}
        if not isinstance(latest, dict):
            return {"status": "missing", "label": "unknown"}
        valuation = latest.get("valuation") if isinstance(latest.get("valuation"), dict) else {}
        score = latest.get("score") if isinstance(latest.get("score"), dict) else {}
        return {
            "status": str(latest.get("status") or "unknown"),
            "label": str(score.get("label") or "unknown"),
            "score": {
                "undervalued_score": int(score.get("undervalued_score") or 0),
                "overvalued_risk": int(score.get("overvalued_risk") or 0),
                "quality_score": int(score.get("quality_score") or 0),
                "growth_score": int(score.get("growth_score") or 0),
                "relative_per_discount_pct": score.get("relative_per_discount_pct"),
            },
            "metrics": {
                "price": valuation.get("price"),
                "market_cap_krw": valuation.get("market_cap_krw"),
                "per": valuation.get("per"),
                "eps": valuation.get("eps"),
                "pbr": valuation.get("pbr"),
                "bps": valuation.get("bps"),
                "dividend_yield_pct": valuation.get("dividend_yield_pct"),
                "industry_per": valuation.get("industry_per"),
                "as_of": valuation.get("as_of"),
                "crawled_at": valuation.get("crawled_at"),
            },
            "reasons": list(score.get("reasons") or [])[:4],
            "risks": list(score.get("risks") or [])[:4],
        }

    def _attach_valuation_context(self, by_symbol: dict[str, dict[str, Any]]) -> None:
        for symbol, item in by_symbol.items():
            if str(item.get("asset_class") or "").lower() == "etf":
                item["valuation"] = {"status": "not_applicable", "label": "etf"}
                continue
            valuation = self._candidate_valuation_payload(symbol)
            item["valuation"] = valuation
            if valuation.get("status") != "ok":
                continue
            label = str(valuation.get("label") or "unknown")
            for reason in list(valuation.get("reasons") or [])[:2]:
                item["reasons"].insert(0, f"밸류에이션: {reason}")
            for risk in list(valuation.get("risks") or [])[:2]:
                item["risks"].insert(0, f"밸류에이션: {risk}")
            if label == "undervalued":
                item["checks"].append("저평가 신호가 실적/수급과 함께 유지되는지 확인")
            elif label == "expensive":
                item["risks"].append("밸류에이션 부담 구간")

    def _collect_rag_context(self, query: str) -> list[dict[str, Any]]:
        if self.rag_store is None:
            return []
        search_text = (
            query
            or "다음 거래일 관심 후보 수급 섹터 목표주가 상향 EPS 상향 리스크"
        )
        rows = self.rag_store.query(query=search_text, limit=24)
        out: list[dict[str, Any]] = []
        for row in rows:
            content = _clean_text(row.get("content"), limit=260)
            quality_score = _rag_context_quality_score(row, query=search_text, content=content)
            if quality_score <= 0:
                continue
            out.append(
                {
                    "report_id": int(row.get("report_id") or 0),
                    "symbol": str(row.get("symbol") or ""),
                    "broker": str(row.get("broker") or ""),
                    "published_at": str(row.get("published_at") or ""),
                    "page_start": int(row.get("page_start") or 0),
                    "quality_score": quality_score,
                    "content": content,
                }
            )
        out.sort(key=lambda item: int(item.get("quality_score") or 0), reverse=True)
        return out[:6]

    def _infer_market_regime(
        self,
        *,
        research_feed: dict[str, Any] | None,
        rag_context: list[dict[str, Any]],
    ) -> dict[str, Any]:
        text_parts: list[str] = []
        if isinstance(research_feed, dict):
            text_parts.append(str(research_feed.get("query") or ""))
            for row in list(research_feed.get("items") or [])[:6]:
                if isinstance(row, dict):
                    text_parts.append(str(row.get("summary") or ""))
        text_parts.extend(str(row.get("content") or "") for row in rag_context[:4])
        text = " ".join(text_parts).lower()
        risk_words = ["변동성", "유가", "금리", "fomc", "전쟁", "매도", "반락", "리스크"]
        momentum_words = ["신고가", "상향", "호실적", "수급", "반도체", "ai", "이익"]
        risk_score = sum(1 for word in risk_words if word in text)
        momentum_score = sum(1 for word in momentum_words if word in text)
        if momentum_score >= risk_score + 2:
            label = "risk-on selective"
            stance = "강한 섹터 중심의 선택적 추세 추종"
        elif risk_score >= momentum_score + 2:
            label = "risk-managed"
            stance = "고점권 변동성 관리와 확인 후 진입"
        else:
            label = "mixed"
            stance = "모멘텀과 이벤트 리스크가 공존"
        return {
            "label": label,
            "stance": stance,
            "risk_score": risk_score,
            "momentum_score": momentum_score,
            "confidence": "medium" if text_parts else "low",
        }

    def build_candidates(
        self,
        *,
        query: str,
        research_feed: dict[str, Any] | None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        raw_limit = int(limit or self.config.max_candidates)
        max_items = max(3, min(raw_limit, 30))
        intent = classify_strategy_intent(query)
        etf_intent = _has_etf_intent(query)
        report_candidates = self._collect_report_candidates(query=query)
        etf_universe = self._etf_universe()
        self._merge_candidate_maps(
            report_candidates,
            self._collect_etf_report_candidates(etf_universe),
        )
        etf_candidates = self._collect_etf_candidates(etf_universe)
        self._merge_candidate_maps(
            report_candidates,
            etf_candidates,
        )
        source_statuses, external_by_symbol = self._external_signals()
        research_by_symbol = self._research_pick_signals(research_feed)
        rag_context = self._collect_rag_context(query)

        for symbol, signal in research_by_symbol.items():
            item = report_candidates.setdefault(
                symbol,
                _candidate_item(symbol, symbol, 42),
            )
            signal_name = _sanitize_subject_name(signal.get("name"), symbol=symbol)
            if signal_name and str(item.get("name") or "") == symbol:
                item["name"] = signal_name
            _add_score(item, "research", float(signal.get("score") or 0))
            item["confidence"] = max(
                int(item.get("confidence") or 0),
                int(signal.get("confidence") or 0),
            )
            item["sources"].add("research_feed")
            item["sources"].update(set(signal.get("sources") or []))
            item["reasons"].extend(list(signal.get("reasons") or [])[:4])
            item["risks"].extend(list(signal.get("risks") or [])[:3])
            item["facts"].extend(list(signal.get("facts") or [])[:4])
            item["published_dates"].extend(list(signal.get("published_dates") or [])[:4])

        for symbol, signals in external_by_symbol.items():
            item = report_candidates.setdefault(
                symbol,
                _candidate_item(symbol, symbol, 45),
            )
            for signal in signals[:5]:
                signal_name = _sanitize_subject_name(signal.get("name"), symbol=symbol)
                if signal_name and str(item.get("name") or "") == symbol:
                    item["name"] = signal_name
                strength = _score_0_100(signal.get("strength"), default=55)
                direction = str(signal.get("direction") or "neutral").lower()
                source_id = str(signal.get("source_id") or signal.get("source") or "external")
                component = _source_component(source_id)
                strengths = item.setdefault("external_strengths", {})
                strengths.setdefault(source_id, []).append(strength)
                delta = (strength - 45) / 4.0
                if direction in {"negative", "bearish", "sell", "down"}:
                    delta = -abs(delta)
                    item["risks"].append(_clean_text(signal.get("summary"), limit=130))
                    _add_score(item, "risk", delta)
                else:
                    item["reasons"].append(_clean_text(signal.get("summary"), limit=130))
                    _add_score(item, component, delta)
                item["confidence"] = max(int(item["confidence"]), min(82, 45 + strength // 3))
                item["sources"].add(source_id)
                as_of = str(signal.get("as_of") or signal.get("published_at") or "").strip()
                if as_of:
                    item.setdefault("published_dates", []).append(as_of)

        self._canonicalize_candidate_names(report_candidates)
        self._attach_valuation_context(report_candidates)

        candidates: list[dict[str, Any]] = []
        exclusions: list[dict[str, Any]] = []
        for symbol, item in report_candidates.items():
            is_etf = str(item.get("asset_class") or "").lower() == "etf"
            reasons = [row for row in dict.fromkeys(item.get("reasons") or []) if row]
            risks = [row for row in dict.fromkeys(item.get("risks") or []) if row]
            checks = [row for row in dict.fromkeys(item.get("checks") or []) if row]
            sources = sorted(str(row) for row in set(item.get("sources") or []))
            if is_etf and isinstance(item.get("score_components_override"), dict):
                component_scores = dict(item["score_components_override"])
                if etf_intent:
                    component_scores["report"] = min(
                        100,
                        int(component_scores.get("report") or 0) + 20,
                    )
                    component_scores["evidence"] = min(
                        100,
                        int(component_scores.get("evidence") or 0) + 20,
                    )
                suitability, data_coverage = _etf_candidate_suitability(
                    components=component_scores,
                    reasons=reasons,
                    risks=risks,
                    has_etf_research=bool(item.get("has_etf_research")),
                    has_report="naver_reports" in sources,
                )
            else:
                component_scores = _candidate_component_scores(
                    item,
                    risks=risks,
                )
                suitability, data_coverage = _candidate_suitability(
                    item=item,
                    components=component_scores,
                    sources=sources,
                    reasons=reasons,
                    risks=risks,
                )
            identity_status = _candidate_identity_status(
                symbol,
                item.get("name") or symbol,
            )
            data_warnings = _candidate_data_warnings(
                identity_status=identity_status,
                coverage=data_coverage,
                valuation=item.get("valuation")
                if isinstance(item.get("valuation"), dict)
                else {},
            )
            score = int((suitability.get("balanced") or {}).get("score") or 0)
            component_scores["fit"] = score
            confidence_raw = (
                float(item.get("confidence") or 45)
                + float(component_scores.get("evidence") or 0) * 0.10
                + len(sources) * 1.5
                + float(data_coverage.get("coverage_score") or 0) * 0.04
                - float(component_scores.get("risk_penalty") or 0) * 0.05
            )
            if not is_etf and not bool(data_coverage.get("has_valuation")):
                confidence_raw -= 5.0
            confidence_score = max(0, min(100, int(round(confidence_raw))))
            if len(sources) <= 1 and not is_etf:
                confidence_score = min(confidence_score, 62)
            if int(component_scores.get("evidence") or 0) < 45:
                confidence_score = min(confidence_score, 70)
            payload = {
                "symbol": symbol,
                "name": str(item.get("name") or symbol),
                "asset_class": str(item.get("asset_class") or "equity"),
                "horizon_bias": str(item.get("horizon_bias") or ""),
                "score": score,
                "score_method_version": "v2",
                "score_components": component_scores,
                "suitability": suitability,
                "risk_score": int(component_scores.get("risk_score") or 0),
                "data_coverage": data_coverage,
                "identity_status": identity_status,
                "data_warnings": data_warnings,
                "valuation": item.get("valuation") or {"status": "missing", "label": "unknown"},
                "confidence": confidence_score,
                "confidence_label": "high"
                if confidence_score >= 72
                else "medium"
                if confidence_score >= 52
                else "low",
                "stance": "watch"
                if score >= 68
                else "confirm"
                if score >= 30
                else "exclude",
                "reasons": reasons[:5],
                "risks": risks[:4],
                "checks": (
                    checks
                    or [
                        "다음 거래일 시초가 갭/거래대금 확인",
                        "섹터 수급과 리포트 근거가 같은 방향인지 확인",
                    ]
                )[:4],
                "sources": sources,
                "report_ids": list(dict.fromkeys(item.get("report_ids") or []))[:8],
                "citations": list(dict.fromkeys(item.get("citations") or []))[:8],
                "facts": list(dict.fromkeys(item.get("facts") or []))[:5],
            }
            if payload["stance"] == "exclude":
                exclusions.append(
                    {
                        "symbol": payload["symbol"],
                        "name": payload["name"],
                        "asset_class": payload["asset_class"],
                        "horizon_bias": payload["horizon_bias"],
                        "reason": "점수/근거가 후보 기준에 미달",
                        "score": payload["score"],
                        "score_method_version": payload["score_method_version"],
                        "score_components": payload["score_components"],
                        "suitability": payload["suitability"],
                        "risk_score": payload["risk_score"],
                        "data_coverage": payload["data_coverage"],
                        "identity_status": payload["identity_status"],
                        "data_warnings": payload["data_warnings"],
                        "valuation": payload["valuation"],
                        "reasons": payload["reasons"],
                        "risks": payload["risks"],
                        "checks": payload["checks"],
                        "sources": payload["sources"],
                        "report_ids": payload["report_ids"],
                        "citations": payload["citations"],
                        "facts": payload["facts"],
                    }
                )
            else:
                candidates.append(payload)

        candidates.sort(key=lambda row: (int(row["score"]), int(row["confidence"])), reverse=True)
        exclusions.sort(key=lambda row: int(row["score"]), reverse=True)
        top_candidates = _include_etf_intent_candidates(
            candidates,
            max_items=max_items,
            etf_intent=etf_intent,
        )
        regime = self._infer_market_regime(
            research_feed=research_feed,
            rag_context=rag_context,
        )
        return {
            "status": "ok",
            "updated_at": _now_iso(),
            "query": query,
            "intent": intent,
            "model": self.codex_runtime.resolved_model,
            "score_method_version": "v2",
            "regime": regime,
            "next_session": {
                "label": "다음 거래일",
                "mode": "watchlist",
                "disclaimer": "실거래 판단용 관심 후보입니다. 실제 주문은 블록 규칙과 안전 게이트 검증 후 실행됩니다.",
            },
            "candidates": top_candidates,
            "candidate_count": len(top_candidates),
            "exclusions": exclusions[:6],
            "sources": self._summarize_sources(
                source_statuses,
                research_feed,
                rag_context,
                etf_usable_count=len(etf_candidates),
            ),
            "rag_context": rag_context[:6],
            "methodology": [
                "단기·중기·장기 투자 검토 적합도를 분리하고 balanced 평균으로 기본 정렬",
                "리포트/수급/고래/밸류/퀄리티/성장/근거품질을 기간별 가중치로 다르게 반영",
                "RAG 문단은 그림 캡션/공시 문구/이미지 노이즈를 제외하고 품질 점수 상위만 사용",
                "소스가 부족하거나 밸류 데이터가 없으면 confidence와 장기 적합도를 검증 기반으로 제한",
                "후보는 블록 매매 후보군이며 실제 진입은 가격·거래대금·수급 확인과 안전 게이트를 통과한 뒤 판단",
            ],
        }

    def _summarize_sources(
        self,
        source_statuses: list[dict[str, Any]],
        research_feed: dict[str, Any] | None,
        rag_context: list[dict[str, Any]],
        etf_usable_count: int | None = None,
    ) -> list[dict[str, Any]]:
        research_runner_count = (
            int((research_feed or {}).get("count") or 0)
            if isinstance(research_feed, dict)
            else 0
        )
        research_runner_items = (
            list((research_feed or {}).get("items") or [])
            if isinstance(research_feed, dict)
            else []
        )
        research_runner_active = bool(research_runner_count or research_runner_items)
        rows = [
            {
                "source_id": "naver_reports",
                "label": "Naver Reports/RAG",
                "status": "active",
                "count": len(rag_context),
                "role": "리포트 근거와 문단 검색",
            },
            {
                "source_id": "research_runner",
                "label": "Research Runner",
                "status": "active"
                if research_runner_active
                else "optional_disabled",
                "count": research_runner_count,
                "role": (
                    "legacy market brief feed; optional when Naver Reports/RAG, "
                    "ETF research, Whale, and 세시반 are primary"
                ),
            },
        ]
        if isinstance(research_feed, dict):
            discovery = research_feed.get("daily_discovery")
            if isinstance(discovery, dict):
                discovery_items = [
                    row for row in list(discovery.get("items") or []) if isinstance(row, dict)
                ]
                rows.append(
                    {
                        "source_id": "daily_discovery",
                        "label": "Daily Discovery",
                        "status": str(discovery.get("status") or "active"),
                        "count": len(discovery_items),
                        "role": "일반 종목 랜덤/심층 디스커버리 후보",
                    }
                )
        if self.etf_research_repository is not None:
            try:
                etf_status = self.etf_research_repository.status()
            except Exception:
                etf_status = {}
            if etf_usable_count is not None:
                usable_count = max(int(etf_usable_count or 0), 0)
            elif "usable_research_count" in etf_status:
                usable_count = int(etf_status.get("usable_research_count") or 0)
            else:
                usable_count = int(etf_status.get("candidate_count") or 0)
                if usable_count <= 0:
                    usable_count = max(
                        int(etf_status.get("score_count") or 0),
                        int(etf_status.get("snapshot_count") or 0),
                    )
            rows.append(
                {
                    "source_id": "etf_research",
                    "label": "ETF Research",
                    "status": "active" if usable_count > 0 else "waiting",
                    "count": usable_count,
                    "role": "ETF 코어/테마 후보 스냅샷과 점수",
                    "db_path": etf_status.get("db_path"),
                }
            )
        for status in source_statuses:
            rows.append(
                {
                    "source_id": status.get("source_id"),
                    "label": status.get("label"),
                    "status": status.get("status"),
                    "count": status.get("count"),
                    "role": status.get("role"),
                    "path": status.get("path"),
                    "caution": status.get("caution"),
                }
            )
        return rows

    async def build_brief(
        self,
        *,
        query: str,
        research_feed: dict[str, Any] | None,
        use_llm: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        cache_key = self._brief_cache_key(
            query=query,
            research_feed=research_feed,
            limit=limit,
        )
        cache_ttl_sec = max(0, int(self.config.brief_cache_ttl_sec))
        if not use_llm and cache_ttl_sec > 0:
            cached = self._brief_cache.get(cache_key)
            if cached:
                cached_at, cached_payload = cached
                if time.monotonic() - cached_at <= cache_ttl_sec:
                    payload = copy.deepcopy(cached_payload)
                    payload["cache_status"] = "hit"
                    return payload
                self._brief_cache.pop(cache_key, None)

        payload = self.build_candidates(
            query=query,
            research_feed=research_feed,
            limit=limit,
        )
        if use_llm:
            payload["brief_mode"] = "llm_error"
            payload["brief_md"] = ""
            if not getattr(self.codex_runtime, "ready", False):
                payload["status"] = "error"
                payload["error_message"] = "codex_runtime_unavailable"
            elif not payload.get("candidates"):
                payload["status"] = "error"
                payload["error_message"] = "no_strategy_candidates"
            else:
                llm_brief, llm_error = await self._llm_brief(payload)
                if llm_brief:
                    payload["brief_mode"] = "llm"
                    payload["brief_md"] = llm_brief
                else:
                    payload["status"] = "error"
                    payload["error_message"] = llm_error or "codex_runtime_error"
        else:
            payload["brief_mode"] = "deterministic"
            payload["brief_md"] = self._deterministic_brief(payload)
        payload["cache_status"] = "miss"
        if not use_llm and cache_ttl_sec > 0:
            self._brief_cache[cache_key] = (time.monotonic(), copy.deepcopy(payload))
            if len(self._brief_cache) > 32:
                oldest_key = min(
                    self._brief_cache,
                    key=lambda key: self._brief_cache[key][0],
                )
                self._brief_cache.pop(oldest_key, None)
        self._append_decision_log(payload)
        return payload

    def _brief_cache_key(
        self,
        *,
        query: str,
        research_feed: dict[str, Any] | None,
        limit: int | None,
    ) -> str:
        try:
            research_fingerprint = json.dumps(
                research_feed or {},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except TypeError:
            research_fingerprint = str(research_feed)
        raw = json.dumps(
            {
                "query": query,
                "limit": int(limit or self.config.max_candidates),
                "research": research_fingerprint,
                "score_method_version": "v2",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _deterministic_brief(self, payload: dict[str, Any]) -> str:
        regime = payload.get("regime") or {}
        candidates = list(payload.get("candidates") or [])
        lines = [
            f"시장 판단: {regime.get('label', 'mixed')} - {regime.get('stance', '확인 필요')}",
            "",
            "다음 거래일 관심 후보",
        ]
        if not candidates:
            lines.append("- 현재 기준 watchlist 후보가 부족합니다.")
        for row in candidates[:5]:
            reason = "; ".join(list(row.get("reasons") or [])[:2]) or "근거 보강 필요"
            risk = "; ".join(list(row.get("risks") or [])[:1]) or "리스크 추가 점검"
            warnings = " · ".join(list(row.get("data_warnings") or [])[:3])
            suitability = row.get("suitability") if isinstance(row.get("suitability"), dict) else {}
            balanced = suitability.get("balanced") if isinstance(suitability.get("balanced"), dict) else {}
            short_term = suitability.get("short_term") if isinstance(suitability.get("short_term"), dict) else {}
            mid_term = suitability.get("mid_term") if isinstance(suitability.get("mid_term"), dict) else {}
            long_term = suitability.get("long_term") if isinstance(suitability.get("long_term"), dict) else {}
            lines.append(
                f"- {row.get('name')}({row.get('symbol')}): "
                f"균형 {balanced.get('grade', '-')} {row.get('score')}, "
                f"단기 {short_term.get('grade', '-')} / 중기 {mid_term.get('grade', '-')} / 장기 {long_term.get('grade', '-')}, "
                f"{reason} / risk={risk}"
                f"{f' / 자료={warnings}' if warnings else ''}"
            )
        lines.extend(
            [
                "",
                "운영 원칙",
                "- 관심 후보는 다음 거래일 블록 생성/수정 판단을 위한 우선순위입니다.",
                "- 시초 갭, 거래대금, 섹터 수급, 리스크 이벤트를 확인해 진입/보류/회피를 결정합니다.",
            ]
        )
        return "\n".join(lines)

    async def _llm_brief(self, payload: dict[str, Any]) -> tuple[str, str]:
        prompt = {
            "task": "Build a Korean strategy-intelligence brief from structured evidence.",
            "language_policy": jue_language_policy(),
            "rules": [
                "Frame candidates as HERMES block-trading priorities, not generic research notes.",
                "Do not fabricate quantities or order prices; block execution gates decide them.",
                "Explain why each candidate is interesting, what must be confirmed, and why it could be wrong.",
                "Use Whale Insight/Sesiban only if source signals are actually present; otherwise say they are waiting adapters.",
            ],
            "payload": {
                "query": payload.get("query"),
                "regime": payload.get("regime"),
                "candidates": list(payload.get("candidates") or [])[:6],
                "sources": payload.get("sources"),
                "methodology": payload.get("methodology"),
            },
            "output_schema": {"brief_md": "string"},
        }
        result = await self.codex_runtime.complete(
            {
                "model": self.codex_runtime.resolved_model,
                "response_format": {"type": "json_object"},
                "telemetry": {
                    "component": "strategy_intelligence",
                    "operation": "build_brief",
                },
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only one JSON object. You are a cautious, evidence-bound "
                            "investment strategy intelligence layer. Analyze and draft in "
                            "English, then translate the final brief_md into Korean."
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            },
            timeout_ms=max(int(self.config.model_timeout_ms), 1000),
        )
        if not bool(result.get("ok")):
            return "", str(result.get("error_message") or result.get("error") or "codex_runtime_error")
        text = str(result.get("content") or "").strip()
        if not text:
            return "", "codex_runtime_empty_content"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text, ""
        if isinstance(parsed, dict):
            brief = str(parsed.get("brief_md") or parsed.get("answer") or "").strip()
            return brief, "" if brief else "codex_runtime_invalid_schema"
        return "", "codex_runtime_invalid_schema"

    def _append_decision_log(self, payload: dict[str, Any]) -> None:
        path = Path(self.config.decision_log_path)
        row = {
            "ts": _now_iso(),
            "query": payload.get("query"),
            "intent": (payload.get("intent") or {}).get("intent"),
            "regime": payload.get("regime"),
            "candidate_symbols": [
                str(row.get("symbol") or "")
                for row in list(payload.get("candidates") or [])[:8]
            ],
            "brief_mode": payload.get("brief_mode"),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            return
