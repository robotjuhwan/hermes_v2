from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import io
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx

from tradecraft.runtime.state_store import utc_now_iso
from tradecraft.services.codex_native import CodexNativeConfig, CodexNativeRuntime

logger = logging.getLogger(__name__)

_OPS_STATUS_DISK_CACHE_MTIME_TOLERANCE_NS = 5_000_000_000

_KRX_SYMBOL_DIRECTORY_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_KRX_SYMBOL_DIRECTORY_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
}
_KRX_STOCK_MARKET_LABELS = {
    "STK": "KOSPI",
    "KSQ": "KOSDAQ",
    "KNX": "KONEX",
}


def _is_six_digit_symbol(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"\d{6}", text))


def _clean_company_name(value: Any) -> str:
    text = _clean_metadata_text(value, limit=80)
    if not text:
        return ""
    if _is_six_digit_symbol(text):
        return ""
    if re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\.[a-z]{2,})?", text.lower()):
        return ""
    if (
        _is_html_artifact(text)
        or _is_generic_company_name(text)
        or _has_company_name_boilerplate(text)
    ):
        return ""
    return text[:80]


def _fetch_krx_symbol_directory_direct(
    *, timeout_sec: float = 15.0
) -> tuple[list[tuple[str, str, str]], list[str]]:
    rows: list[tuple[str, str, str]] = []
    errors: list[str] = []

    def _post(
        client: httpx.Client, payload: dict[str, str], label: str
    ) -> list[dict[str, Any]]:
        try:
            response = client.post(
                _KRX_SYMBOL_DIRECTORY_URL,
                headers=_KRX_SYMBOL_DIRECTORY_HEADERS,
                data=payload,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            return []
        block = data.get("block1") if isinstance(data, dict) else None
        if not isinstance(block, list):
            errors.append(f"{label}: missing block1")
            return []
        return [item for item in block if isinstance(item, dict)]

    timeout = httpx.Timeout(timeout_sec)
    with httpx.Client(timeout=timeout) as client:
        stock_rows = _post(
            client,
            {
                "bld": "dbms/comm/finder/finder_stkisu",
                "mktsel": "ALL",
                "searchText": "",
            },
            "STOCK",
        )
        for item in stock_rows:
            code = str(item.get("short_code") or "").strip()
            name = _clean_company_name(item.get("codeName"))
            market_code = str(item.get("marketCode") or "").strip().upper()
            market = _KRX_STOCK_MARKET_LABELS.get(market_code, market_code or "KRX")
            if _is_six_digit_symbol(code) and name:
                rows.append((code, name, market))

        for market in ("ETF", "ETN"):
            etx_rows = _post(
                client,
                {
                    "bld": "dbms/comm/finder/finder_secuprodisu",
                    "mktsel": market,
                    "searchText": "",
                },
                market,
            )
            for item in etx_rows:
                code = str(item.get("short_code") or "").strip()
                name = _clean_company_name(item.get("codeName"))
                if _is_six_digit_symbol(code) and name:
                    rows.append((code, name, market))

    return rows, errors


def _to_text(raw: str) -> str:
    text = html.unescape(str(raw or ""))
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_html_artifact(value: Any) -> bool:
    text = html.unescape(str(value or "")).strip()
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "<",
            ">",
            "btn_report.gif",
            "static/nfinance",
            "리포트 보기",
            "align=",
            "absmiddle",
            "alt=",
        )
    )


def _clean_metadata_text(value: Any, *, limit: int = 120) -> str:
    text = _to_text(str(value or ""))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'`|")
    if not text:
        return ""
    return text[: max(int(limit), 1)].strip()


def _is_generic_company_name(value: Any) -> bool:
    text = _clean_metadata_text(value, limit=80).lower()
    return text in {
        "brief",
        "report",
        "리포트",
        "기업분석",
        "산업분석",
        "시황",
        "market",
        "daily",
        "review",
        "리뷰",
        "실적 리뷰",
        "실적리뷰",
        "preview",
        "프리뷰",
        "comment",
        "코멘트",
        "update",
        "업데이트",
        "earnings",
        "company",
        "company brief",
        "ks",
        "kq",
        "kospi",
        "kosdaq",
        "현재가",
        "액면가",
        "자본금",
        "시가총액",
        "원",
        "억원",
        "십억원",
        "주",
        "unknown",
        "정보",
        "테마",
        "네이버",
        "네이버에",
        "콘텐츠",
        "제공",
        "것들",
        "이제",
        "코스콤",
        "국내",
        "시세",
        "국내 시세 정보",
        "코스콤 국내 시세 정보",
        "테마 정보 네이버에 콘텐츠 제공 코스콤 국내 시세 정보",
    }


def _has_company_name_boilerplate(value: Any) -> bool:
    compact = re.sub(r"\s+", "", str(value or "")).lower()
    return any(
        re.sub(r"\s+", "", marker).lower() in compact
        for marker in (
            "코스콤 국내 시세 정보",
            "네이버에 콘텐츠 제공",
            "테마 정보",
        )
    )


_AUTHORITATIVE_SYMBOL_DIRECTORY_SOURCES = {"pykrx", "krx", "krx_lookup"}


def _company_names_overlap(left: Any, right: Any) -> bool:
    left_name = _clean_company_candidate(left)
    right_name = _clean_company_candidate(right)
    if not left_name or not right_name:
        return False
    left_compact = re.sub(r"\s+", "", left_name).lower()
    right_compact = re.sub(r"\s+", "", right_name).lower()
    return (
        left_compact == right_compact
        or left_compact in right_compact
        or right_compact in left_compact
    )


def _is_authoritative_symbol_source(source: Any, confidence: Any) -> bool:
    name = str(source or "").strip().lower()
    try:
        score = float(confidence)
    except (TypeError, ValueError):
        score = 0.0
    return name in _AUTHORITATIVE_SYMBOL_DIRECTORY_SOURCES and score >= 0.99


def _is_trusted_symbol_link_directory(source: Any, confidence: Any) -> bool:
    try:
        score = float(confidence)
    except (TypeError, ValueError):
        score = 0.0
    return _is_authoritative_symbol_source(source, score) or score >= 0.99


def _choose_identity_company(
    *,
    symbol: str,
    inferred_company: str,
    mapped_company: str,
    mapped_source: str,
    mapped_confidence: float,
) -> str:
    inferred = _clean_company_candidate(inferred_company)
    mapped = _clean_company_candidate(mapped_company)
    if inferred and mapped and not _company_names_overlap(inferred, mapped):
        if _is_authoritative_symbol_source(mapped_source, mapped_confidence):
            return mapped
        return inferred
    if mapped:
        return mapped
    return inferred


def _is_probable_short_date_symbol(value: Any) -> bool:
    text = str(value or "").strip()
    if not _is_six_digit_symbol(text):
        return False
    year = int(text[:2])
    month = int(text[2:4])
    day = int(text[4:6])
    return 24 <= year <= 35 and 1 <= month <= 12 and 1 <= day <= 31


def _is_report_date_symbol(value: Any, published_at: Any = "") -> bool:
    text = str(value or "").strip()
    if not _is_six_digit_symbol(text):
        return False
    published = str(published_at or "").strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", published)
    if match and text == f"{match.group(1)[2:]}{match.group(2)}{match.group(3)}":
        return True
    return False


def _clean_report_symbol(value: Any, *, published_at: Any = "") -> str:
    text = str(value or "").strip()
    if not _is_six_digit_symbol(text):
        return ""
    if _is_report_date_symbol(text, published_at):
        return ""
    return text


_GENERIC_SYMBOL_LINK_NAMES = {
    "etf",
    "etn",
    "펀드",
    "상장지수펀드",
    "투자유망",
    "정보",
    "국내 etf",
    "해외 etf",
}


def _symbol_asset_class_from_market(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "etf":
        return "etf"
    if text == "etn":
        return "etn"
    return "stock"


def _clean_symbol_link_name(value: Any) -> str:
    name = _clean_metadata_text(value, limit=120)
    if not name:
        return ""
    if name.lower() in _GENERIC_SYMBOL_LINK_NAMES:
        return ""
    return name


def _compact_symbol_alias(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _symbol_name_pattern(name: str) -> re.Pattern[str]:
    tokens = [re.escape(token) for token in re.split(r"\s+", name.strip()) if token]
    if not tokens:
        return re.compile(r"a^")
    pattern = r"\s*".join(tokens)
    boundary_chars = r"가-힣A-Za-z0-9"
    korean_particles = "은는이가을를과와도에의로"
    return re.compile(
        rf"(?<![{boundary_chars}]){pattern}(?=$|[^{boundary_chars}]|[{korean_particles}])",
        flags=re.IGNORECASE,
    )


def _symbol_link_evidence(text: str, start: int, end: int) -> str:
    left = max(start - 32, 0)
    right = min(end + 32, len(text))
    return re.sub(r"\s+", " ", text[left:right]).strip()[:160]


def _candidate_name_before_symbol(text: str, position: int) -> str:
    names = _candidate_names_before_symbol(text, position)
    return names[0] if names else ""


def _candidate_names_before_symbol(text: str, position: int) -> list[str]:
    left = str(text or "")[max(int(position) - 72, 0) : max(int(position), 0)]
    left = re.sub(r"[\s([{（]+$", "", left).strip()
    match = re.search(r"([가-힣A-Za-z][가-힣A-Za-z0-9&.\- ]{1,42})$", left)
    if not match:
        return []
    raw = match.group(1).strip()
    candidates = [raw]
    tokens = raw.split()
    if tokens:
        candidates.extend([tokens[-1], " ".join(tokens[-2:])])
    names: list[str] = []
    for candidate in reversed(candidates):
        cleaned = _clean_company_candidate(candidate)
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


def _has_conflicting_name_near_symbol(
    text: str,
    *,
    position: int,
    expected_name: str,
    asset_class: str,
) -> bool:
    if str(asset_class or "stock").strip().lower() in {"etf", "etn"}:
        return False
    expected = _clean_company_candidate(expected_name)
    if not expected:
        return False
    nearby_names = _candidate_names_before_symbol(text, position)
    if not nearby_names:
        return False
    return not any(_company_names_overlap(name, expected) for name in nearby_names)


def _allow_name_only_symbol_link(name: str, asset_class: str = "stock") -> bool:
    if str(asset_class or "").strip().lower() in {"etf", "etn"}:
        return True
    compact = _compact_symbol_alias(name)
    if len(compact) <= 2:
        return False
    if re.fullmatch(r"[a-z]{1,3}", compact):
        return False
    return True


def _extract_report_symbol_links(
    text: str,
    *,
    symbol_names: dict[str, str],
    asset_class_by_symbol: dict[str, str] | None = None,
    published_at: str = "",
) -> list[dict[str, Any]]:
    raw_text = str(text or "")
    if not raw_text.strip():
        return []
    asset_classes = asset_class_by_symbol or {}
    candidates: dict[str, dict[str, Any]] = {}

    def remember(
        *,
        symbol: str,
        name: str,
        confidence: float,
        evidence: str,
        position: int,
    ) -> None:
        current = candidates.get(symbol)
        if current and float(current["confidence"]) > confidence:
            return
        if current and float(current["confidence"]) == confidence:
            position = min(int(current["_position"]), position)
        candidates[symbol] = {
            "symbol": symbol,
            "name": name,
            "asset_class": str(asset_classes.get(symbol) or "stock").strip()
            or "stock",
            "link_type": "mention",
            "source": "text_extract",
            "confidence": confidence,
            "evidence": evidence[:160],
            "_position": position,
        }

    compact_text = _compact_symbol_alias(raw_text)
    head_length = min(len(raw_text), 1500)
    occupied_name_spans: list[tuple[int, int]] = []
    sorted_symbols = sorted(
        symbol_names.items(),
        key=lambda item: len(_compact_symbol_alias(item[1])),
        reverse=True,
    )
    for symbol, raw_name in sorted_symbols:
        code = _clean_report_symbol(symbol, published_at=published_at)
        name = _clean_symbol_link_name(raw_name)
        if not code or not name:
            continue
        pattern = _symbol_name_pattern(name)
        first_name_match = pattern.search(raw_text)
        code_match = re.search(rf"(?<!\d){re.escape(code)}(?!\d)", raw_text)
        if code_match:
            start = max(code_match.start() - 80, 0)
            end = min(code_match.end() + 80, len(raw_text))
            near_text = raw_text[start:end]
            confidence = 0.85
            evidence_start = code_match.start()
            evidence_end = code_match.end()
            near_name_match = pattern.search(near_text)
            has_canonical_name = bool(
                near_name_match
                or _compact_symbol_alias(name) in _compact_symbol_alias(near_text)
            )
            if has_canonical_name:
                confidence = 0.95
                if near_name_match:
                    evidence_start = start + near_name_match.start()
                    occupied_name_spans.append(
                        (
                            start + near_name_match.start(),
                            start + near_name_match.end(),
                        )
                    )
                    evidence_end = code_match.end()
            elif _has_conflicting_name_near_symbol(
                raw_text,
                position=code_match.start(),
                expected_name=name,
                asset_class=asset_classes.get(symbol, "stock"),
            ):
                continue
            remember(
                symbol=code,
                name=name,
                confidence=confidence,
                evidence=_symbol_link_evidence(raw_text, evidence_start, evidence_end),
                position=code_match.start(),
            )
            continue
        compact_name = _compact_symbol_alias(name)
        if compact_name and compact_name in compact_text and first_name_match is None:
            if not _allow_name_only_symbol_link(name, asset_classes.get(symbol, "")):
                continue
            position = compact_text.find(compact_name)
            compact_end = position + len(compact_name)
            if any(
                position < end and compact_end > start
                for start, end in occupied_name_spans
            ):
                continue
            confidence = 0.75 if position < head_length else 0.55
            remember(
                symbol=code,
                name=name,
                confidence=confidence,
                evidence=name,
                position=position,
            )
            occupied_name_spans.append((position, compact_end))
            continue
        if first_name_match is None:
            continue
        if not _allow_name_only_symbol_link(name, asset_classes.get(symbol, "")):
            continue
        if any(
            first_name_match.start() < end and first_name_match.end() > start
            for start, end in occupied_name_spans
        ):
            continue
        confidence = 0.75 if first_name_match.start() < head_length else 0.55
        remember(
            symbol=code,
            name=name,
            confidence=confidence,
            evidence=_symbol_link_evidence(
                raw_text,
                first_name_match.start(),
                first_name_match.end(),
            ),
            position=first_name_match.start(),
        )
        occupied_name_spans.append((first_name_match.start(), first_name_match.end()))

    ordered = sorted(
        candidates.values(),
        key=lambda item: (int(item["_position"]), -float(item["confidence"])),
    )[:20]
    for item in ordered:
        item.pop("_position", None)
    return ordered


def _derive_title_from_content(content: Any, *, category: str = "") -> str:
    text = _clean_metadata_text(content, limit=320)
    if not text:
        return ""
    text = re.sub(r"^(?:▶|■|●|▪|[-–—])\s*", "", text).strip()
    if len(text) < 4:
        return ""
    if len(text) > 96:
        text = text[:96].rstrip()
    return text


def _clean_report_title(value: Any, *, content: Any = "", category: str = "") -> str:
    title = _clean_metadata_text(value, limit=140)
    bad_title = (
        not title
        or _is_html_artifact(value)
        or title.lower() in {"report", "리포트", "brief"}
    )
    if bad_title:
        derived = _derive_title_from_content(content, category=category)
        if derived:
            return derived
        label = str(category or "report").strip() or "report"
        return label[:120]
    return title[:140]


def _trim_report_title_tail(value: str) -> str:
    text = _clean_metadata_text(value, limit=160)
    if not text:
        return ""
    text = re.split(
        r"\s+(?:What[’']?s New|Analysis|So What|투자의견|목표주가|현재주가|시가총액|Stock Data|Analyst|RA\b|www\.|Forecasts and valuations)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.split(
        r"\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+",
        text,
        maxsplit=1,
    )[0]
    text = re.split(
        r"\s+[가-힣]{2,4}\s+[a-z][a-z0-9._%+-]*",
        text,
        maxsplit=1,
    )[0]
    text = re.split(
        r"\s+\|\s*\d{4}[./]\s*\d{1,2}[./]\s*\d{1,2}",
        text,
        maxsplit=1,
    )[0]
    text = re.split(r"\s+[■●▶▪]\s+", text, maxsplit=1)[0]
    for marker in (
        " 최근 ",
        " 동사의 ",
        " 이번 ",
        " 약 ",
        " ETF 까지 ",
        " 목표주가",
        " Check Point",
    ):
        marker_index = text.find(marker)
        if marker_index >= 16:
            text = text[:marker_index]
            break
    return text.strip(" ,:;-/|")[:96].strip()


def _derive_company_report_title(
    *,
    title: Any,
    content: Any,
    company_name: str,
    symbol: str,
) -> str:
    company = _clean_company_candidate(company_name)
    code = _clean_report_symbol(symbol)
    if not company or not code:
        return ""
    probe = "\n".join(
        [
            _clean_metadata_text(title, limit=260),
            _clean_metadata_text(content, limit=5000),
        ]
    )
    if not probe.strip():
        return ""
    pattern = re.compile(
        rf"{re.escape(company)}[ \t]*[\(（][ \t]*{re.escape(code)}(?:/[A-Z]{{1,6}})?[ \t]*[\)）][ \t]*(?P<tail>[^\n\r]{{0,120}})",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(probe))
    match = next(
        (
            item
            for item in matches
            if _trim_report_title_tail(item.group("tail") or "")
        ),
        matches[0] if matches else None,
    )
    if not match:
        return ""
    tail = _trim_report_title_tail(match.group("tail") or "")
    candidate = f"{company} ({code})"
    if tail:
        candidate = f"{candidate} {tail}"
    return _clean_metadata_text(candidate, limit=140)


def _derive_company_bracket_report_title(
    *,
    title: Any,
    content: Any,
    company_name: str,
    symbol: str,
) -> str:
    company = _clean_company_candidate(company_name)
    code = _clean_report_symbol(symbol)
    if not company or not code:
        return ""
    probe = "\n".join(
        [
            _clean_metadata_text(title, limit=260),
            _clean_metadata_text(content, limit=5000),
        ]
    )
    if not probe.strip():
        return ""
    pattern = re.compile(
        rf"[\[［]\s*{re.escape(company)}\s*[\]］][ \t]*(?P<tail>[^\n\r]{{0,120}})",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(probe))
    match = next(
        (
            item
            for item in matches
            if _trim_report_title_tail(item.group("tail") or "")
        ),
        matches[0] if matches else None,
    )
    if not match:
        return ""
    tail = _trim_report_title_tail(match.group("tail") or "")
    repeat_match = re.search(
        rf"\s+{re.escape(company)}(?:은|는|이|가|의|,|\s)",
        tail,
    )
    if repeat_match and repeat_match.start() >= 8:
        tail = tail[: repeat_match.start()].strip()
    candidate = f"{company} ({code})"
    if tail:
        candidate = f"{candidate} {tail}"
    return _clean_metadata_text(candidate, limit=140)


def _strip_leading_report_disclaimer(value: Any) -> str:
    text = _clean_metadata_text(value, limit=1200)
    if not text:
        return ""
    leading_noise = (
        text.lower().startswith("www.")
        or "본 조사분석자료" in text[:260]
        or "고객께서는 자신의 판단과 책임" in text[:320]
        or "금융투자분석사의 확인" in text[:260]
        or "compliance notice" in text[:260].lower()
    )
    if not leading_noise:
        return text
    useful_markers = (
        r"\bIBKS\s+Spot\s+Comment\b",
        r"\bETF\s+Catch[-\s]?up\b",
        r"\bQuant\s+Monthly\b",
        r"\bKIWOOM\s+FICC\s+DAILY\b",
        r"\bWeekly\s*\|",
        r"\bIssue\s+Comment\b",
        r"\bCompany\s+Report\b",
        r"\bEarnings\s+Preview\b",
        r"\bNOT\s+RATED\b",
        r"\bBUY\b",
        r"\bStrategy\s+Comment\b",
        r"\bSector\s+Comment\b",
    )
    for pattern in useful_markers:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.start() > 0:
            return text[match.start() :].strip()
    return text


def _normalize_search_snippet(
    value: Any,
    *,
    company_name: Any = "",
    symbol: Any = "",
) -> str:
    text = _strip_leading_report_disclaimer(value)
    company = _clean_company_candidate(company_name)
    code = _clean_report_symbol(symbol)
    if not text or not company or not code:
        return text
    pattern = re.compile(
        rf"^(?:\d{{1,3}}\s+)?{re.escape(company)}[ \t]*[\(（][ \t]*{re.escape(code)}(?:/[A-Z]{{1,6}})?[ \t]*[\)）][ \t]*",
        flags=re.IGNORECASE,
    )
    return pattern.sub(f"{company} ({code}) ", text, count=1).strip()


def _improve_company_report_title(
    title: Any,
    *,
    content: Any,
    company_name: str,
    symbol: str,
) -> str:
    current = _clean_metadata_text(title, limit=140)
    derived = _derive_company_report_title(
        title=current,
        content=content,
        company_name=company_name,
        symbol=symbol,
    )
    if not derived:
        derived = _derive_company_bracket_report_title(
            title=current,
            content=content,
            company_name=company_name,
            symbol=symbol,
        )
    if not derived:
        return current
    company = _clean_company_candidate(company_name)
    code = _clean_report_symbol(symbol)
    canonical_prefix = f"{company} ({code})"
    if current.startswith(canonical_prefix):
        return current
    prefix_index = current.find(canonical_prefix)
    if prefix_index > 0:
        return derived
    noisy_prefix_markers = (
        "주가 및 주요이벤트",
        "기업가치 제고",
        "기업분석 ",
        "www.",
        "Stock Data",
        "시가총액",
        "재무지표",
        "밸류에이션 지표",
    )
    if (
        len(current) > 90
        or any(marker in current for marker in noisy_prefix_markers)
        or re.match(r"^\d{1,3}\s+", current)
        or re.match(r"^\d{4}[./-]\s*\d{1,2}[./-]\s*\d{1,2}\s+", current)
    ):
        return derived
    return current


def _is_noisy_non_company_report_title(value: Any) -> bool:
    title = _clean_metadata_text(value, limit=180)
    if not title:
        return True
    lowered = title.lower()
    if lowered.startswith("www.") or "본 조사분석자료" in title:
        return True
    noisy_starts = (
        "1 제목입니다",
        "시가총액 △eps",
        "지수 선물",
        "종가 1d",
        "글로벌 배출권 가격",
    )
    if lowered.startswith(noisy_starts):
        return True
    number_tokens = len(re.findall(r"[-+]?\d+(?:[.,]\d+)?%?", title))
    word_tokens = len(re.findall(r"[가-힣A-Za-z]+", title))
    return number_tokens >= 8 and number_tokens > word_tokens


def _trim_non_company_title(value: str) -> str:
    text = _clean_metadata_text(value, limit=180)
    if not text:
        return ""
    text = re.split(
        r"\s+(?:What[’']?s New|자료:|Research Center|Table|Chart)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.split(r"\s+[■●▶▪]\s+", text, maxsplit=1)[0]
    for marker in (" 이번 ", " 최근 ", " 해당 ", " 다만 "):
        marker_index = text.find(marker)
        if marker_index >= 16:
            text = text[:marker_index]
            break
    return text.strip(" ,:;-/|")[:96].strip()


def _derive_non_company_report_title(content: Any) -> str:
    text = _clean_metadata_text(content, limit=5000)
    if not text:
        return ""
    weekly = re.search(
        r"(?:주간|월간|Weekly|Monthly)?\s*Comment\s*\([^)）]{3,40}[)）]",
        text,
        flags=re.IGNORECASE,
    )
    if weekly:
        return _trim_non_company_title(weekly.group(0))

    spot = re.search(r"\bIBKS\s+Spot\s+Comment\b(?P<body>.{0,520})", text)
    if spot:
        body = spot.group("body") or ""
        bracket = re.search(r"(\[[^\]]{2,30}\]\s*.{4,140})", body)
        if bracket:
            headline = _trim_non_company_title(bracket.group(1))
            if headline:
                return _clean_metadata_text(
                    f"IBKS Spot Comment {headline}",
                    limit=140,
                )
        return "IBKS Spot Comment"

    return ""


def _improve_non_company_report_title(
    title: Any,
    *,
    content: Any,
) -> str:
    current = _clean_metadata_text(title, limit=140)
    if current and not _is_noisy_non_company_report_title(current):
        return current
    derived = _derive_non_company_report_title(content)
    return derived or current


def _parse_date(text: str) -> str:
    match = re.search(r"(\d{4})[./-](\d{2})[./-](\d{2})", text)
    if not match:
        return ""
    return _normalize_report_date(
        f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    )


def _normalize_report_date(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return ""
    try:
        return date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ).isoformat()
    except ValueError:
        return ""


def _parse_symbol(text: str) -> str:
    for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", str(text or "")):
        candidate = match.group(1)
        if not _is_probable_short_date_symbol(candidate):
            return candidate
    return ""


def _clean_company_candidate(value: Any) -> str:
    text = _clean_company_name(value)
    if not text:
        return ""
    text = re.sub(r"(?<=기술분석보고서)\s+", "", text)
    noisy_markers = (
        "www.",
        ".com",
        "issue",
        "news",
        "earnings",
        "preview",
        "review",
        "comment",
        "update",
        "earnings review",
        "기업코멘트",
        "기업분석",
        "기술분석보고서",
        "리서치센터",
        "price trend",
        "investment",
        "securities",
        "판단",
        "예상",
        "수혜",
        "stock data",
        "company data",
    )
    if any(marker in text.lower() for marker in noisy_markers):
        company_tokens = re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9&.\-]{1,24}", text)
        company_tokens = [
            token
            for token in company_tokens
            if token.lower()
            not in {
                "기업분석",
                "기업코멘트",
                "산업분석",
                "리포트",
                "목표주가",
                "현재주가",
                "리서치센터",
                "price",
                "trend",
                "earnings",
                "preview",
                "review",
                "comment",
                "update",
                "investment",
                "securities",
                "ds",
            }
            and not re.fullmatch(r"\d+", token)
            and not (re.search(r"\d", token) and not re.search(r"[가-힣]", token))
            and "@" not in token
            and not re.fullmatch(
                r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\.[a-z]{2,})?",
                token.lower(),
            )
            and not any(
                marker in token
                for marker in ("기업분석", "기업코멘트", "산업분석", "리서치센터")
            )
        ]
        if company_tokens:
            suffix = company_tokens[-1].rstrip(".").lower()
            if (
                suffix in {"ent", "inc", "corp", "co", "ltd"}
                and len(company_tokens) >= 2
            ):
                text = f"{company_tokens[-2]} {company_tokens[-1]}".strip()
            else:
                text = company_tokens[-1]
        else:
            return ""
    if len(text) > 36:
        return ""
    lower = text.lower()
    bad_markers = (
        "목표주가",
        "현재가",
        "현재주가",
        "액면가",
        "자본금",
        "시가총액",
        "투자의견",
        "영업이익",
        "매출액",
        "컨센서스",
        "수익모델",
        "구조적",
        "계절적",
        "analyst",
        "research",
        "company data",
        "stock data",
        "buy",
        "hold",
        "sell",
        "not rated",
        "trading buy",
    )
    if any(marker in lower for marker in bad_markers):
        return ""
    if re.search(r"[0-9][0-9,]{2,}\s*(?:원|억원|조원|%)", text):
        return ""
    text = re.split(
        r"\s+(?:\d{4}[./-]\d{1,2}[./-]\d{1,2}|20\d{2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    text = re.split(
        r"\s+(?:투자의견|목표주가|현재주가|Analyst|BUY|HOLD|SELL|Not Rated|Trading Buy|매수|중립)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    tokens = text.split()
    if len(tokens) > 1 and any(
        token.lower().strip(":/_-")
        in {"earnings", "preview", "review", "results", "comment", "company", "brief"}
        for token in tokens[:-1]
    ):
        text = tokens[-1]
    if re.fullmatch(r"[가-힣](?:\s+[가-힣]){2,}[가-힣A-Za-z0-9&.\-]*", text):
        text = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", text)
    text = re.sub(r"(?<=[A-Za-z])\s+(?=[가-힣])", "", text)
    if not text or _is_generic_company_name(text):
        return ""
    return text[:36]


def _clean_broker_name(value: Any) -> str:
    text = _clean_metadata_text(value, limit=60)
    if not text or _is_html_artifact(text):
        return ""
    text = text.replace("㈜", "(주)")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^\(주\)", "", text)
    text = re.sub(r"\(주\)$", "", text)
    if not text:
        return ""
    aliases = {
        "KIRS": "한국IR협의회",
        "한국IR협의회": "한국IR협의회",
        "나이스평가정보": "나이스평가정보",
        "NICE평가정보": "나이스평가정보",
        "서울평가정보": "서울평가정보",
    }
    if text in aliases:
        return aliases[text]
    if re.search(r"(증권|평가정보|신용평가|IR협의회)$", text):
        return text[:40]
    return ""


_BROKER_DOMAIN_HINTS: tuple[tuple[str, str], ...] = (
    ("kirs.or.kr", "한국IR협의회"),
    ("ibks.com", "IBK투자증권"),
    ("sks.co.kr", "SK증권"),
    ("eugenefn.com", "유진투자증권"),
    ("kiwoom.com", "키움증권"),
    ("daishin.com", "대신증권"),
    ("miraeasset.com", "미래에셋증권"),
    ("hanwha.com", "한화투자증권"),
    ("hanafn.com", "하나증권"),
    ("shinhan.com", "신한투자증권"),
    ("nhqv.com", "NH투자증권"),
    ("yuantakorea.com", "유안타증권"),
    ("ds-sec.co.kr", "DS투자증권"),
)

_STRONG_BROKER_TEXT_HINTS: tuple[tuple[str, str], ...] = (
    ("yuantakorea.com", "유안타증권"),
    ("yuanta morning snapshot", "유안타증권"),
    ("yuanta securities", "유안타증권"),
    ("yuanta research", "유안타증권"),
    ("miraeasset.com", "미래에셋증권"),
    ("mirae asset securities research", "미래에셋증권"),
    ("mirae asset equity research", "미래에셋증권"),
)

_ANALYST_EMAIL_HINTS: tuple[tuple[str, str], ...] = (
    ("younggun.kim", "김영건"),
    ("un.kim.a@miraeasset.com", "김영건"),
    ("joohee.kim", "김주희"),
    ("jinsuk.kim", "김진석"),
)


def _extract_strong_broker_from_text(text: Any) -> str:
    raw = _to_text(str(text or ""))
    if not raw:
        return ""
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", raw)
    lowered = re.sub(r"\s+", " ", raw).strip().lower()
    compact_no_space = re.sub(r"\s+", "", lowered)
    for marker, broker in _STRONG_BROKER_TEXT_HINTS:
        marker_lower = marker.lower()
        marker_compact = re.sub(r"\s+", "", marker_lower)
        if marker_lower in lowered or marker_compact in compact_no_space:
            return broker
    return ""


def _looks_like_garbled_pdf_text(text: Any) -> bool:
    sample = str(text or "")[:12000]
    if len(sample) < 120:
        return False
    controls = sum(
        1 for char in sample if ord(char) < 32 and char not in {"\n", "\r", "\t"}
    )
    unusual_ranges = (
        ("\u0500", "\u052f"),
        ("\u0580", "\u05ff"),
        ("\u0980", "\u09ff"),
        ("\u0a00", "\u0a7f"),
        ("\u0b00", "\u0b7f"),
        ("\u0f00", "\u0fff"),
    )
    unusual = sum(
        1
        for char in sample
        if any(start <= char <= end for start, end in unusual_ranges)
    )
    hangul = sum(1 for char in sample if "\uac00" <= char <= "\ud7a3")
    length = max(len(sample), 1)
    return (
        controls / length > 0.01
        or (unusual > 30 and hangul / length < 0.04)
        or ("\x01" in sample and unusual > 10)
    )


def _extract_analyst_from_email_hint(text: Any) -> str:
    compact = re.sub(r"\s+", "", _to_text(str(text or "")).lower())
    if not compact:
        return ""
    for marker, analyst in _ANALYST_EMAIL_HINTS:
        if marker in compact:
            return analyst
    return ""


def _extract_broker_from_text(text: Any) -> str:
    raw = _to_text(str(text or ""))
    if not raw:
        return ""
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", raw)
    compact = re.sub(r"\s+", " ", raw).strip()
    compact_no_space = re.sub(r"\s+", "", raw).lower()
    strong_broker = _extract_strong_broker_from_text(raw)
    if strong_broker:
        return strong_broker
    for marker, broker in _BROKER_DOMAIN_HINTS:
        if marker in compact_no_space:
            return broker

    match = re.search(
        r"([가-힣A-Za-z0-9]+(?:투자)?증권)",
        compact,
        flags=re.IGNORECASE,
    )
    if match:
        broker = _clean_broker_name(match.group(1))
        if broker:
            return broker

    institution_patterns = (
        r"작\s*성\s*기\s*관\s*([가-힣A-Za-z0-9()㈜.\s]{0,24}?평가정보(?:\(주\)|㈜)?)",
        r"작\s*성\s*기\s*관\s*([가-힣A-Za-z0-9()㈜.\s]{0,24}?신용평가(?:\(주\)|㈜)?)",
        r"(한국\s*IR\s*협의회|KIRS)",
    )
    for pattern in institution_patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if match:
            broker = _clean_broker_name(match.group(1))
            if broker:
                return broker
    return ""


def _company_from_symbol_map(symbol: str, symbol_names: dict[str, str]) -> str:
    code = str(symbol or "").strip()
    if not code:
        return ""
    return _clean_company_candidate(symbol_names.get(code, ""))


def _extract_company_symbol_from_text(
    text: Any,
    *,
    symbol_names: dict[str, str] | None = None,
    published_at: str = "",
) -> tuple[str, str]:
    raw = _to_text(str(text or ""))
    if not raw:
        return "", ""
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return "", ""

    known = dict(symbol_names or {})

    def _valid_symbol(value: Any) -> str:
        return _clean_report_symbol(value, published_at=published_at)

    def _pick(symbol: Any, company: Any = "") -> tuple[str, str] | None:
        code = _valid_symbol(symbol)
        if not code:
            return None
        mapped_company = _company_from_symbol_map(code, known)
        candidate_company = _clean_company_candidate(company)
        if candidate_company:
            if mapped_company and (
                mapped_company in candidate_company or candidate_company in mapped_company
            ):
                return code, mapped_company
            return code, candidate_company
        if mapped_company:
            return code, mapped_company
        return code, ""

    patterns: tuple[tuple[re.Pattern[str], str, int], ...] = (
        (
            re.compile(
                r"기술분석보고서\s*([가-힣A-Za-z][가-힣A-Za-z0-9&.\- ]{1,30})"
                r"\s*\(\s*(\d{6})\s*(?:\s*[/,.)]\s*(?:KS|KQ|KOSPI|KOSDAQ))?\s*\)",
                flags=re.IGNORECASE,
            ),
            "company_symbol",
            0,
        ),
        (
            re.compile(
                r"\(\s*(\d{6})\s*(?:\s*[/,.)]\s*(?:KS|KQ|KOSPI|KOSDAQ))?\s*\)\s*"
                r"([가-힣A-Za-z][가-힣A-Za-z0-9&.\- ]{1,30})",
                flags=re.IGNORECASE,
            ),
            "symbol_company",
            1,
        ),
        (
            re.compile(
                r"([가-힣A-Za-z][가-힣A-Za-z0-9&.\-]{1,30})\s*"
                r"\(\s*(\d{6})\s*(?:\s*[/,.)]\s*(?:KS|KQ|KOSPI|KOSDAQ))?\s*\)",
                flags=re.IGNORECASE,
            ),
            "company_symbol",
            1,
        ),
        (
            re.compile(
                r"(?<!\d)(\d{6})(?!\d)\s*[·|/]\s*"
                r"(?:[가-힣A-Za-z&/\-]{1,24}\s+)?"
                r"([가-힣A-Za-z][가-힣A-Za-z0-9&.\-]{1,30})",
                flags=re.IGNORECASE,
            ),
            "symbol_company",
            2,
        ),
        (
            re.compile(
                r"([가-힣][가-힣A-Za-z0-9&.\-]{1,24})\s+"
                r"(?<!\d)(\d{6})(?!\d)"
                r"(?:\s*[/,.)]\s*(?:KS|KQ|KOSPI|KOSDAQ))?",
                flags=re.IGNORECASE,
            ),
            "company_symbol",
            2,
        ),
    )

    candidates: list[tuple[int, int, tuple[str, str]]] = []
    for pattern, orientation, priority in patterns:
        for match in pattern.finditer(raw[:12000]):
            if orientation == "company_symbol":
                result = _pick(match.group(2), match.group(1))
            else:
                result = _pick(match.group(1), match.group(2))
            if result and (result[1] or result[0] in known):
                candidates.append((match.start(), priority, result))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", raw[:12000]):
        code = _valid_symbol(match.group(1))
        if code and code in known:
            return code, _company_from_symbol_map(code, known)

    company_matches: list[tuple[int, int, str, str]] = []
    head = raw[:1200]
    for code, name in known.items():
        company = _clean_company_candidate(name)
        if not _is_six_digit_symbol(code) or len(company) < 2:
            continue
        idx = head.find(company)
        if idx >= 0:
            company_matches.append((idx, -len(company), code, company))
    if company_matches:
        company_matches.sort()
        _, _, code, company = company_matches[0]
        return code, company
    return "", ""


def _clean_analyst_name(value: Any) -> str:
    text = _clean_metadata_text(value, limit=60)
    if not text:
        return ""
    if text.startswith("부서:"):
        return _clean_department_author_label(text[3:])
    if re.fullmatch(r"[가-힣](?:\s*[가-힣]){1,4}", text):
        text = re.sub(r"\s+", "", text)
    match = re.search(r"[가-힣]{2,5}|[A-Za-z][A-Za-z .]{2,30}", text)
    if not match:
        return ""
    name = str(match.group(0) or "").strip(" .")
    if name in {
        "Analyst",
        "애널리스트",
        "Research",
        "센터",
        "자료",
        "리서치",
        "리서치센터",
        "리서치본부",
        "투자전략",
        "작성자",
        "연구원",
        "책임",
        "정보",
        "평가정보",
        "본인의",
        "당사는",
        "당사",
        "외부",
        "경제",
        "중동",
        "전망",
        "우라늄",
        "재생에너지",
        "시장",
        "채권",
        "금리",
        "반도체",
        "원자재",
        "개발",
        "발간일자",
        "금융",
        "서비스",
        "미드",
        "스몰캡",
        "종합",
        "글로벌",
        "정보팀",
        "지표는",
        "전망의",
        "본읶의",
        "세계경제는",
        "대성의",
        "충격의",
        "전반의",
        "비축유",
        "목표는",
        "영향",
        "여파로",
        "전망치는",
        "산업으로의",
        "사태의",
        "성장률은",
        "사절단은",
        "성장률",
        "전반은",
    }:
        return ""
    if any(marker in name for marker in ("리서치", "센터", "본부", "본인의", "본읶")):
        return ""
    if name.endswith(("의", "는", "로")):
        return ""
    if len(name) >= 4 and name.endswith("은"):
        return ""
    if len(name) < 2:
        return ""
    return name[:40]


def _clean_department_author_label(value: Any, *, broker: str = "") -> str:
    text = _clean_metadata_text(value, limit=100)
    if not text:
        return ""
    text = re.sub(r"^(?:작성자|작성\s*자)\s*[:：]?\s*", "", text).strip()
    text = text.strip("()[]{} ")
    text = text.replace("Research Center", "리서치센터")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*[・·]\s*", "·", text)
    text = re.sub(r"^(?:당사|본사)\s*", "", text).strip()
    text = re.sub(r"\s*(?:에서|가|는|은|의)$", "", text).strip()

    allowed_markers = (
        "리서치센터",
        "리서치본부",
        "투자분석부",
        "투자전략정보팀",
        "기간산업분석부",
        "혁신기업분석부",
        "글로벌전략팀",
        "FICC 리서치부",
        "해외주식분석실",
    )
    if not any(marker in text for marker in allowed_markers):
        return ""
    if re.search(r"[A-Za-z0-9._%+-]+@", text):
        return ""

    clean_broker = _clean_broker_name(broker)
    if clean_broker and clean_broker not in text:
        text = f"{clean_broker} {text}".strip()
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 4:
        return ""
    return f"부서: {text[:80]}"


def _extract_analyst_from_text(text: Any) -> str:
    raw = _to_text(str(text or ""))
    if not raw:
        return ""
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""

    patterns: tuple[tuple[str, int], ...] = (
        (
            r"(?:애널리스트|Analyst|책임연구위원|선임연구위원|연구위원|연구원|RA)"
            r"\s*[:：]?\s*([가-힣]{2,5})\s*"
            r"(?:0\d{1,2}|[A-Za-z0-9._%+-]+\s*@)",
            1,
        ),
        (
            r"(?:Strategist|Economist|Analyst|Quant|RA)\s+"
            r"([가-힣]\s*[가-힣]\s*[가-힣]?(?:\s*[가-힣])?)"
            r"\s*,?\s*(?:CFA|Ph\.?D\.?)?\s+"
            r"[A-Za-z0-9._%+-]+\s*@",
            1,
        ),
        (
            r"(?:애널리스트|금융투자분석사|조사분석담당자)\s*"
            r"\(\s*([가-힣]{2,5})\s*\)",
            1,
        ),
        (r"(?:작성자|작성\s*자)\s*\(\s*([가-힣]{2,5})\s*\)", 1),
        (
            r"([가-힣]{2,5})\s*(?:책임\s*)?(?:책임연구원|연구원|연구위원)\s*"
            r"(?:발간일자|작성일|[0-9]{4}).{0,90}?"
            r"[A-Za-z0-9._%+-]+\s*@",
            1,
        ),
        (
            r"(?:[가-힣A-Za-z/·\s]{2,30}팀)\s+([가-힣]{2,5})\s+"
            r"\d{2,4}\)?\s*\d{3,4}[-_\s]*\d{4}[_\s/]*"
            r"[A-Za-z0-9._%+-]+\s*@",
            1,
        ),
        (
            r"(?:[가-힣A-Za-z&·/\- ]{1,30}팀)\s+([가-힣]{2,5})\s+"
            r"(?:Analyst|Strategist|Economist|RA)\s+"
            r"[A-Za-z0-9._%+-]+\s*@",
            1,
        ),
        (
            r"(?:미드\s*/\s*스몰캡|미드스몰캡|스몰캡|퀀트|전략)\s+"
            r"([가-힣]{2,5})\s+[A-Za-z0-9._%+-]+\s*@",
            1,
        ),
        (
            r"(?:리서치센터|투자전략팀|Research Center).{0,40}?"
            r"(?:채권전략|투자전략|경제|FX|퀀트|Quant|시황|전략)\s*([가-힣]{2,5})",
            1,
        ),
        (
            r"(?:채권전략|투자전략|경제|FX|퀀트|Quant|시황|전략)\s+([가-힣]{2,5})\s*(?:/|\\||\d{4}|$)",
            1,
        ),
        (r"(?:작성자|작성\s*자)\s*[:：]?\s*([가-힣]{2,5})", 1),
        (
            r"작\s*성\s*기\s*관.{0,60}?([가-힣]{2,5})\s*(?:책임\s*)?작\s*성\s*자",
            1,
        ),
        (
            r"작\s*성\s*기\s*관.{0,60}?([가-힣]{2,5})\s*(?:책임)?\s*연구원",
            1,
        ),
        (
            r"([가-힣]{2,5})\s*/\s*[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}",
            1,
        ),
        (
            r"(?<![가-힣/])([가-힣]{2,5})\s*/\s*"
            r"(?:Strategist|Economist|Analyst|RA|Researcher|Quant|Global|EM|DM)"
            r"[A-Za-z .&\-]{0,30}\s+"
            r"[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+",
            1,
        ),
        (
            r"([가-힣]{2,5})\s+\d{2,4}(?:[-)]?\s*\d{3,4})?(?:-\d{4})?\s*/?\s*[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}",
            1,
        ),
        (
            r"(?<![가-힣])([가-힣]\s*[가-힣]\s*[가-힣]?(?:\s*[가-힣])?)\s+\d{2,4}(?:[-)]?\s*\d{3,4})?(?:-\d{4})?\s*/?\s*[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}",
            1,
        ),
        (
            r"(?<![가-힣])([가-힣]{2,5})\s+"
            r"[가-힣A-Za-z&·/.\- ]{2,42}\s+"
            r"\d{2,4}(?:[-)]?\s*\d{3,4})?(?:-\d{4})?\s*/?\s*"
            r"[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+",
            1,
        ),
        (
            r"([가-힣]{2,5})\s+\d{3,4}\s*/\s*[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}",
            1,
        ),
        (
            r"(?:[가-힣A-Za-z/&.\- ]{0,24})\s([가-힣]{2,5})\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+",
            1,
        ),
        (
            r"([가-힣]{2,5})\s+[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}",
            1,
        ),
        (
            r"([가-힣]{2,5})\s*(?:Ph\.?D\.?)?\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+",
            1,
        ),
        (r"담당자\s*[:：]\s*([가-힣]{2,5})(?:\s*[,/]\s*[가-힣]{2,5})?", 1),
    )
    windows = [raw[:12000]]
    if len(raw) > 12000:
        windows.append(raw[-12000:])
    for keyword in (
        "작성자",
        "작성 자",
        "@",
        "Analyst",
        "애널리스트",
        "연구위원",
        "연구원",
        "담당자",
        "Strategist",
        "스몰캡",
        "작성자(",
    ):
        start = 0
        while True:
            idx = raw.find(keyword, start)
            if idx < 0:
                break
            windows.append(raw[max(idx - 300, 0) : idx + 700])
            start = idx + len(keyword)
            if len(windows) >= 24:
                break
        if len(windows) >= 24:
            break

    seen: set[str] = set()
    for window in windows:
        if not window or window in seen:
            continue
        seen.add(window)
        for pattern, group_idx in patterns:
            match = re.search(pattern, window, flags=re.IGNORECASE)
            if match:
                name = _clean_analyst_name(match.group(group_idx))
                if name:
                    return name
        name = _extract_analyst_from_email_hint(window)
        if name:
            return name
    return ""


def _extract_department_author_from_text(text: Any, *, broker: str = "") -> str:
    raw = _to_text(str(text or ""))
    if not raw:
        return ""
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""

    patterns: tuple[tuple[str, int], ...] = (
        (r"작성자\s*\(\s*([^)]+?리서치(?:센터|본부)[^)]*)\)", 1),
        (r"\[(리서치본부\s*[가-힣A-Za-z0-9/\s]{0,30}팀)\]", 1),
        (
            r"(?:│|\|)\s*\d{4}\.?\s*\d{1,2}\.?\s*\d{1,2}\s*(?:│|\|)\s*"
            r"([가-힣A-Za-z・·/\s]{2,60}부)\b",
            1,
        ),
        (r"(기간산업분석부\s*[・·]\s*혁신기업분석부)", 1),
        (
            r"(투자분석부|투자전략정보팀|글로벌전략팀|FICC\s+리서치부|해외주식분석실)",
            1,
        ),
        (r"(?:당사|본사)?\s*(리서치센터|리서치본부)", 1),
        (r"(Research Center)", 1),
    )
    windows = [raw[:12000]]
    if len(raw) > 12000:
        windows.append(raw[-12000:])
    for keyword in (
        "작성자(",
        "리서치센터",
        "리서치본부",
        "투자분석부",
        "투자전략정보팀",
        "Research Center",
        "기간산업분석부",
        "FICC",
    ):
        start = 0
        while True:
            idx = raw.find(keyword, start)
            if idx < 0:
                break
            windows.append(raw[max(idx - 260, 0) : idx + 520])
            start = idx + len(keyword)
            if len(windows) >= 24:
                break
        if len(windows) >= 24:
            break

    seen: set[str] = set()
    for window in windows:
        if not window or window in seen:
            continue
        seen.add(window)
        for pattern, group_idx in patterns:
            match = re.search(pattern, window, flags=re.IGNORECASE)
            if not match:
                continue
            label = _clean_department_author_label(match.group(group_idx), broker=broker)
            if label:
                return label
    return ""


def _infer_default_department_author_from_context(
    *,
    broker: Any,
    category: Any,
    text: Any,
) -> str:
    clean_broker = _clean_broker_name(broker)
    category_text = str(category or "").strip()
    if not clean_broker or category_text == "company_analysis":
        return ""
    raw = _to_text(str(text or ""))
    if not raw:
        return ""
    lowered = raw.lower()

    rules: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = (
        (
            "SK증권",
            ("market_info", "invest_info", "economy_analysis"),
            "리서치센터",
            ("SK증권", "SK 증권", "sks.co.kr", "Quantiwise, SK"),
        ),
        (
            "다올투자증권",
            ("market_info",),
            "리서치센터",
            ("buly.kr", "daolfn.com", "Daol", "다올"),
        ),
        (
            "다올투자증권",
            ("bond_analysis",),
            "리서치센터",
            ("KR Market KR Bond", "KR Credit", "daolfn.com"),
        ),
        (
            "유안타증권",
            ("market_info",),
            "리서치센터",
            ("Yuanta Morning Snapshot", "Yuanta", "유안타"),
        ),
        (
            "유안타증권",
            ("bond_analysis",),
            "리서치센터",
            ("금융투자분석사의 확인", "Appendix"),
        ),
        (
            "IBK투자증권",
            ("market_info", "invest_info"),
            "투자분석부",
            ("IBKS RESEARCH", "IBK투자증권", "Quantiwise"),
        ),
        (
            "IBK투자증권",
            ("bond_analysis", "economy_analysis"),
            "리서치본부",
            ("IBKS RESEARCH", "IBKS Bond"),
        ),
        (
            "키움증권",
            ("invest_info",),
            "리서치센터",
            ("키움", "Kiwoom"),
        ),
        (
            "하나증권",
            ("invest_info",),
            "해외주식분석실",
            ("해외주식분석실",),
        ),
    )
    for rule_broker, categories, department, markers in rules:
        if clean_broker != rule_broker or category_text not in categories:
            continue
        if any(marker.lower() in lowered for marker in markers):
            return _clean_department_author_label(department, broker=clean_broker)
    return ""


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return url.strip()
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("market_info", "market_info"),
    ("/research/daily", "market_info"),
    ("category=market", "market_info"),
    ("invest", "invest_info"),
    ("/research/invest", "invest_info"),
    ("category=invest", "invest_info"),
    ("company", "company_analysis"),
    ("/research/company", "company_analysis"),
    ("category=company", "company_analysis"),
    ("industry", "industry_analysis"),
    ("/research/industry", "industry_analysis"),
    ("category=industry", "industry_analysis"),
    ("economy", "economy_analysis"),
    ("/research/economy", "economy_analysis"),
    ("category=economy", "economy_analysis"),
    ("debenture", "bond_analysis"),
    ("/research/debenture", "bond_analysis"),
    ("category=debenture", "bond_analysis"),
)


def _infer_report_category(source_url: str, detail_url: str) -> str:
    haystack = f"{source_url} {detail_url}".lower()
    for marker, category in _CATEGORY_RULES:
        if marker in haystack:
            return category
    return "unknown"


_RESEARCH_SEED_CATEGORY_PRIORITY: dict[str, int] = {
    "company_analysis": 0,
    "industry_analysis": 1,
    "invest_info": 2,
    "market_info": 3,
    "economy_analysis": 4,
    "bond_analysis": 5,
    "unknown": 9,
}


def _prioritize_research_seed_urls(values: list[str]) -> list[str]:
    indexed: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(values):
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        category = _infer_report_category(url, url)
        priority = _RESEARCH_SEED_CATEGORY_PRIORITY.get(category, 9)
        indexed.append((priority, index, url))
    return [url for _, _, url in sorted(indexed)]


def _is_research_detail_url(url: str) -> bool:
    lower = str(url or "").lower()
    if "/research/" not in lower:
        return False
    if "_read.naver" in lower:
        return True
    path = (urlparse(lower).path or "").strip()
    return bool(
        re.search(
            r"/research/(daily|invest|company|industry|economy|debenture)/\d+",
            path,
        )
    )


def _extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pattern = re.compile(
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        href = match.group(1).strip()
        label = _to_text(match.group(2) or "")
        if not href:
            continue
        absolute = urljoin(base_url, href)
        out.append((_canonical_url(absolute), label))
    return out


def _split_chunks(text: str, chunk_size: int, max_chunks: int) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned) and len(chunks) < max(max_chunks, 1):
        end = min(start + max(chunk_size, 200), len(cleaned))
        chunks.append(cleaned[start:end])
        start = end
    return chunks


def _safe_non_negative_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        if value != value:
            return 0
        return max(int(round(value)), 0)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return max(int(round(float(text))), 0)
    except ValueError:
        return 0


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _normalize_price_krw(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _split_page_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            out.append(line)
    return out


def _remove_repeated_header_footer(pages: list[str]) -> list[str]:
    if len(pages) <= 1:
        return [re.sub(r"\s+", " ", page).strip() for page in pages]

    first_counts: dict[str, int] = {}
    last_counts: dict[str, int] = {}
    page_lines: list[list[str]] = []
    for page in pages:
        lines = _split_page_lines(page)
        page_lines.append(lines)
        if not lines:
            continue
        first = lines[0]
        last = lines[-1]
        first_counts[first] = first_counts.get(first, 0) + 1
        last_counts[last] = last_counts.get(last, 0) + 1

    min_repeat = max(len(pages) // 2, 2)
    repeated_heads = {line for line, cnt in first_counts.items() if cnt >= min_repeat}
    repeated_tails = {line for line, cnt in last_counts.items() if cnt >= min_repeat}

    cleaned: list[str] = []
    for lines in page_lines:
        rows = list(lines)
        while rows and rows[0] in repeated_heads:
            rows = rows[1:]
        while rows and rows[-1] in repeated_tails:
            rows = rows[:-1]
        cleaned.append(re.sub(r"\s+", " ", "\n".join(rows)).strip())
    return cleaned


def _detect_section_title(text: str) -> str:
    lines = _split_page_lines(text)
    if not lines:
        return "unknown"

    patterns: tuple[tuple[str, str], ...] = (
        (r"요약|투자포인트|핵심포인트", "summary"),
        (r"리스크|위험|하방", "risk"),
        (r"밸류에이션|valuation|per|pbr|ev/?ebitda|dcf", "valuation"),
        (r"실적|earnings|이익|매출|eps", "earnings"),
        (r"촉매|모멘텀|catalyst", "catalyst"),
    )

    probe = " ".join(lines[:6])
    lower = probe.lower()
    for pattern, label in patterns:
        if re.search(pattern, lower, flags=re.IGNORECASE):
            return label

    head = lines[0][:80]
    if len(head) <= 2:
        return "unknown"
    return head


def _build_chunk_rows(
    pages: list[dict[str, Any]],
    chunk_size: int,
    max_chunks: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    size = max(int(chunk_size), 300)
    overlap = max(int(round(size * 0.12)), 40)
    step = max(size - overlap, 120)

    for page in pages:
        page_no = _safe_non_negative_int(page.get("page_number")) or 1
        section_title = str(page.get("section_title") or "unknown")
        text = re.sub(r"\s+", " ", str(page.get("content") or "")).strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                out.append(
                    {
                        "content": chunk,
                        "page_start": page_no,
                        "page_end": page_no,
                        "section_title": section_title,
                    }
                )
            if end >= len(text) or len(out) >= max(int(max_chunks), 1):
                break
            start += step
        if len(out) >= max(int(max_chunks), 1):
            break
    return out


def _extract_basic_structured(
    text: str,
    pages: list[dict[str, Any]],
    *,
    title: str,
    broker: str,
    symbol: str,
) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    lower = compact.lower()

    rating = "UNKNOWN"
    if re.search(r"\b(buy|매수)\b", lower, flags=re.IGNORECASE):
        rating = "BUY"
    elif re.search(r"\b(hold|중립|보유)\b", lower, flags=re.IGNORECASE):
        rating = "HOLD"
    elif re.search(r"\b(sell|매도)\b", lower, flags=re.IGNORECASE):
        rating = "SELL"

    target_price = 0
    target_changed = "UNKNOWN"
    m = re.search(
        r"(목표주가|target\s*price|tp)\D{0,12}([0-9][0-9,]{2,})",
        compact,
        flags=re.IGNORECASE,
    )
    if m:
        target_price = _normalize_price_krw(m.group(2))
    if re.search(r"상향|raise|upward", compact, flags=re.IGNORECASE):
        target_changed = "UP"
    elif re.search(r"하향|downward|cut", compact, flags=re.IGNORECASE):
        target_changed = "DOWN"
    elif target_price > 0:
        target_changed = "UNCHANGED"

    valuation_method = "UNKNOWN"
    if re.search(r"ev/?ebitda", lower):
        valuation_method = "EV/EBITDA"
    elif re.search(r"\bper\b", lower):
        valuation_method = "PER"
    elif re.search(r"\bpbr\b", lower):
        valuation_method = "PBR"
    elif re.search(r"\bdcf\b", lower):
        valuation_method = "DCF"

    summary_bullets: list[str] = []
    for sentence in re.split(r"(?<=[.!?다])\s+", compact):
        line = sentence.strip()
        if len(line) < 14:
            continue
        summary_bullets.append(line[:180])
        if len(summary_bullets) >= 3:
            break

    investment_thesis: list[str] = []
    risks: list[str] = []
    catalysts: list[str] = []
    for sentence in re.split(r"(?<=[.!?다])\s+", compact):
        line = sentence.strip()
        if len(line) < 10:
            continue
        low = line.lower()
        if ("리스크" in line or "위험" in line or "risk" in low) and len(risks) < 3:
            risks.append(line[:180])
        if ("투자" in line or "thesis" in low or "포인트" in line) and len(
            investment_thesis
        ) < 3:
            investment_thesis.append(line[:180])
        if ("촉매" in line or "모멘텀" in line or "catalyst" in low) and len(
            catalysts
        ) < 3:
            catalysts.append(line[:180])

    evidence_quotes: list[dict[str, Any]] = []
    for page in pages:
        page_no = _safe_non_negative_int(page.get("page_number")) or 1
        content = str(page.get("content") or "")
        if not content:
            continue
        if target_price > 0 and re.search(
            r"목표주가|target\s*price|tp", content, flags=re.IGNORECASE
        ):
            snippet = re.sub(r"\s+", " ", content)[:140]
            evidence_quotes.append(
                {"page": page_no, "tag": "target_price", "text": snippet}
            )
        if risks and re.search(r"리스크|위험|risk", content, flags=re.IGNORECASE):
            snippet = re.sub(r"\s+", " ", content)[:140]
            evidence_quotes.append({"page": page_no, "tag": "risk", "text": snippet})
        if len(evidence_quotes) >= 3:
            break

    if not evidence_quotes and pages:
        first_page_no = _safe_non_negative_int(pages[0].get("page_number")) or 1
        first_text = re.sub(r"\s+", " ", str(pages[0].get("content") or "")).strip()
        if first_text:
            evidence_quotes.append(
                {"page": first_page_no, "tag": "summary", "text": first_text[:140]}
            )

    return {
        "rating": rating,
        "target_price": {
            "value": target_price,
            "currency": "KRW",
            "changed": target_changed,
        },
        "summary_bullets": summary_bullets,
        "investment_thesis": investment_thesis,
        "risks": risks,
        "earnings_outlook": [],
        "valuation": {
            "method": valuation_method,
            "value": None,
            "basis": "",
            "notes": "",
        },
        "catalysts": catalysts,
        "evidence_quotes": evidence_quotes,
        "report_meta": {
            "title": title,
            "broker": broker,
            "symbol": symbol,
        },
    }


@dataclass(slots=True)
class NaverReportCrawlerConfig:
    db_path: str
    pdf_archive_dir: str = ".runtime/naver_reports/pdfs"
    seed_url: str = "https://finance.naver.com/research/company_list.naver"
    seed_urls: list[str] | None = None
    max_pages: int = 5
    timeout_sec: float = 20.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    )
    since_date: str = ""
    chunk_size: int = 1200
    max_chunks_per_report: int = 24
    max_pdf_chars: int = 120000
    min_pdf_text_chars: int = 240
    max_detail_pages: int = 40
    max_pdfs_per_cycle: int = 80
    request_delay_sec: float = 1.8
    codex_runtime_mode: str = "auto"
    codex_runtime_sdk_codex_bin: str = ""
    codex_runtime_timeout_ms: int = 60000
    llm_model: str = "gpt-5.6-luna"
    llm_reasoning_effort: str = "xhigh"
    llm_usage_enabled: bool = True
    llm_usage_db_path: str = ".runtime/llm_usage.db"
    llm_usage_component: str = "research_reports"
    codex_native_thread_mode: str = "daily"
    codex_native_thread_db_path: str = ".runtime/codex_native_threads.db"
    codex_native_compact_after_turns: int = 8
    codex_native_read_turns: bool = False
    codex_native_developer_instructions_enabled: bool = True
    llm_facts_enabled: bool = False


class NaverReportRepository:
    def __init__(self, db_path: str, *, status_cache_ttl_sec: float = 30.0) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ops_status_disk_cache_path = self.path.with_name(
            f"{self.path.name}.ops_status_cache.json"
        )
        self.status_cache_ttl_sec = max(float(status_cache_ttl_sec), 0.0)
        self._status_cache: tuple[float, int, dict[str, Any]] | None = None
        self._ops_status_cache: tuple[float, int, dict[str, Any]] | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _connect_readonly(self) -> sqlite3.Connection:
        db_uri = f"{self.path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(db_uri, timeout=30.0, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _db_mtime_ns(self) -> int:
        candidates = [
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ]
        latest = 0
        for candidate in candidates:
            try:
                latest = max(latest, candidate.stat().st_mtime_ns)
            except FileNotFoundError:
                continue
        return latest

    def _db_file_footprint(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for label, candidate in (
            ("db", self.path),
            ("wal", Path(f"{self.path}-wal")),
            ("shm", Path(f"{self.path}-shm")),
        ):
            try:
                out[label] = int(candidate.stat().st_size)
            except FileNotFoundError:
                out[label] = 0
        return out

    def _invalidate_status_cache(self) -> None:
        self._status_cache = None
        self._ops_status_cache = None
        try:
            self._ops_status_disk_cache_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _read_ops_status_disk_cache(
        self,
        *,
        mtime_ns: int,
    ) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._ops_status_disk_cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if int(payload.get("version") or 0) != 1:
            return None
        cached_mtime_ns = int(payload.get("db_mtime_ns") or 0)
        if cached_mtime_ns != int(mtime_ns):
            cached_footprint = payload.get("db_footprint")
            if not isinstance(cached_footprint, dict):
                return None
            current_footprint = self._db_file_footprint()
            normalized_cached_footprint = {
                str(key): int(value or 0)
                for key, value in cached_footprint.items()
            }
            mtime_delta = abs(int(mtime_ns) - cached_mtime_ns)
            if (
                normalized_cached_footprint != current_footprint
                or mtime_delta > _OPS_STATUS_DISK_CACHE_MTIME_TOLERANCE_NS
            ):
                return None
        if str(payload.get("db_path") or "") != str(self.path):
            return None
        status = payload.get("status")
        if not isinstance(status, dict):
            return None
        return copy.deepcopy(status)

    def _write_ops_status_disk_cache(
        self,
        *,
        mtime_ns: int,
        status: dict[str, Any],
    ) -> None:
        try:
            tmp_path = self._ops_status_disk_cache_path.with_suffix(
                self._ops_status_disk_cache_path.suffix + ".tmp"
            )
            tmp_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "db_path": str(self.path),
                        "db_mtime_ns": int(mtime_ns),
                        "db_footprint": self._db_file_footprint(),
                        "cached_at": utc_now_iso(),
                        "status": status,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            tmp_path.replace(self._ops_status_disk_cache_path)
        except (OSError, TypeError, ValueError):
            return

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'unknown',
                    source_url TEXT NOT NULL,
                    detail_url TEXT NOT NULL,
                    pdf_url TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    company_name TEXT NOT NULL DEFAULT '',
                    broker TEXT NOT NULL,
                    analyst TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    crawled_at TEXT NOT NULL DEFAULT '',
                    pdf_sha256 TEXT NOT NULL DEFAULT '',
                    pdf_archived_path TEXT NOT NULL DEFAULT '',
                    content_source TEXT NOT NULL DEFAULT 'pdf_extract',
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_chunks (
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    page_start INTEGER NOT NULL DEFAULT 0,
                    page_end INTEGER NOT NULL DEFAULT 0,
                    section_title TEXT NOT NULL DEFAULT 'unknown',
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(report_id, chunk_index)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_facts (
                    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL UNIQUE,
                    rating TEXT NOT NULL DEFAULT 'UNKNOWN',
                    target_price_value INTEGER NOT NULL DEFAULT 0,
                    target_price_currency TEXT NOT NULL DEFAULT 'KRW',
                    target_price_changed TEXT NOT NULL DEFAULT 'UNKNOWN',
                    valuation_method TEXT NOT NULL DEFAULT 'UNKNOWN',
                    valuation_value REAL,
                    valuation_basis TEXT NOT NULL DEFAULT '',
                    valuation_notes TEXT NOT NULL DEFAULT '',
                    summary_bullets_json TEXT NOT NULL DEFAULT '[]',
                    investment_thesis_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    earnings_outlook_json TEXT NOT NULL DEFAULT '[]',
                    catalysts_json TEXT NOT NULL DEFAULT '[]',
                    evidence_quotes_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(report_id) REFERENCES reports(report_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_symbol_links (
                    report_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    asset_class TEXT NOT NULL DEFAULT 'stock',
                    link_type TEXT NOT NULL DEFAULT 'mention',
                    source TEXT NOT NULL DEFAULT 'unknown',
                    confidence REAL NOT NULL DEFAULT 0.0,
                    evidence TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (report_id, symbol, link_type),
                    FOREIGN KEY (report_id) REFERENCES reports(report_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_symbol_links_symbol
                ON report_symbol_links(symbol, asset_class, confidence DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_symbol_links_report
                ON report_symbol_links(report_id)
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_symbol_date ON reports(symbol, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_broker_date ON reports(broker, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_analyst_date ON reports(analyst, published_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_directory (
                    symbol TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT '',
                    asset_class TEXT NOT NULL DEFAULT 'stock',
                    status TEXT NOT NULL DEFAULT 'active',
                    source TEXT NOT NULL DEFAULT 'unknown',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbol_directory_name ON symbol_directory(company_name)"
            )
            self._ensure_column(
                conn=conn,
                table="reports",
                column="category",
                definition="TEXT NOT NULL DEFAULT 'unknown'",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_category_date ON reports(category, published_at)"
            )
            self._backfill_category(conn)
            self._ensure_column(
                conn=conn,
                table="reports",
                column="doc_id",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="reports",
                column="company_name",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="reports",
                column="analyst",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="reports",
                column="crawled_at",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="reports",
                column="pdf_sha256",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="reports",
                column="pdf_archived_path",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="reports",
                column="content_source",
                definition="TEXT NOT NULL DEFAULT 'pdf_extract'",
            )
            self._backfill_doc_id(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_doc_id ON reports(doc_id)"
            )
            self._ensure_column(
                conn=conn,
                table="report_chunks",
                column="page_start",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn=conn,
                table="report_chunks",
                column="page_end",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn=conn,
                table="report_chunks",
                column="section_title",
                definition="TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="market",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="asset_class",
                definition="TEXT NOT NULL DEFAULT 'stock'",
            )
            conn.execute(
                """
                UPDATE symbol_directory
                SET asset_class = CASE
                    WHEN UPPER(COALESCE(market, '')) = 'ETF' THEN 'etf'
                    WHEN UPPER(COALESCE(market, '')) = 'ETN' THEN 'etn'
                    ELSE asset_class
                END
                WHERE UPPER(COALESCE(market, '')) IN ('ETF', 'ETN')
                  AND LOWER(COALESCE(NULLIF(asset_class, ''), 'stock')) = 'stock'
                """
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="status",
                definition="TEXT NOT NULL DEFAULT 'active'",
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="source",
                definition="TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="confidence",
                definition="REAL NOT NULL DEFAULT 1.0",
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="first_seen_at",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="updated_at",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table="symbol_directory",
                column="last_verified_at",
                definition="TEXT NOT NULL DEFAULT ''",
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _backfill_category(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT report_id, source_url, detail_url, category FROM reports"
        ).fetchall()
        for row in rows:
            current = str(row["category"] or "").strip().lower()
            if current and current != "unknown":
                continue
            source_url = str(row["source_url"] or "")
            detail_url = str(row["detail_url"] or "")
            inferred = _infer_report_category(source_url, detail_url)
            if inferred == "unknown":
                continue
            conn.execute(
                "UPDATE reports SET category = ? WHERE report_id = ?",
                (inferred, int(row["report_id"])),
            )

    @staticmethod
    def _backfill_doc_id(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT report_id, doc_id, pdf_sha256, pdf_url FROM reports"
        ).fetchall()
        for row in rows:
            current = str(row["doc_id"] or "").strip()
            if current:
                continue
            raw = str(row["pdf_sha256"] or "").strip()
            if not raw:
                raw = hashlib.sha256(
                    str(row["pdf_url"] or "").encode("utf-8")
                ).hexdigest()
            conn.execute(
                "UPDATE reports SET doc_id = ? WHERE report_id = ?",
                (raw, int(row["report_id"])),
            )

    def has_pdf_url(self, pdf_url: str) -> bool:
        raw = str(pdf_url or "").strip()
        if not raw:
            return False
        canonical = _canonical_url(raw)
        with self._connect_readonly() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM reports
                WHERE pdf_url IN (?, ?)
                LIMIT 1
                """,
                (raw, canonical),
            ).fetchone()
        return row is not None

    def has_detail_url(self, detail_url: str) -> bool:
        raw = str(detail_url or "").strip()
        if not raw:
            return False
        canonical = _canonical_url(raw)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM reports
                WHERE detail_url IN (?, ?)
                LIMIT 1
                """,
                (raw, canonical),
            ).fetchone()
        return row is not None

    def upsert_report(
        self,
        category: str,
        source_url: str,
        detail_url: str,
        pdf_url: str,
        pdf_sha256: str,
        pdf_archived_path: str,
        title: str,
        company_name: str,
        broker: str,
        analyst: str,
        symbol: str,
        published_at: str,
        crawled_at: str,
        content_source: str,
        content: str,
        chunk_size: int,
        max_chunks_per_report: int,
        chunks: list[dict[str, Any]] | None = None,
        structured_facts: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now_iso()
        text = content.strip()
        normalized_published_at = _normalize_report_date(published_at)
        normalized_title = _clean_report_title(
            title,
            content=text,
            category=category,
        )
        normalized_company_name = _clean_company_name(company_name)
        normalized_symbol = _clean_report_symbol(
            symbol,
            published_at=normalized_published_at,
        )
        directory_source = "naver_reports"
        directory_confidence = 0.8
        chunk_rows = list(chunks or [])
        if not chunk_rows:
            plain_chunks = _split_chunks(
                text, chunk_size=chunk_size, max_chunks=max_chunks_per_report
            )
            chunk_rows = [
                {
                    "content": chunk,
                    "page_start": 0,
                    "page_end": 0,
                    "section_title": "unknown",
                }
                for chunk in plain_chunks
            ]
        doc_id = (
            str(pdf_sha256 or "").strip()
            or hashlib.sha256(str(pdf_url or "").encode("utf-8")).hexdigest()
        )
        with self._connect() as conn:
            symbol_rows = conn.execute(
                "SELECT symbol, company_name, market, source, confidence FROM symbol_directory"
            ).fetchall()
            symbol_names: dict[str, str] = {}
            symbol_meta: dict[str, tuple[str, float]] = {}
            asset_class_by_symbol: dict[str, str] = {}
            for symbol_row in symbol_rows:
                code = str(symbol_row["symbol"] or "").strip()
                name = _clean_symbol_link_name(symbol_row["company_name"])
                if not _is_six_digit_symbol(code) or not name:
                    continue
                symbol_names[code] = name
                symbol_meta[code] = (
                    str(symbol_row["source"] or ""),
                    float(symbol_row["confidence"] or 0.0),
                )
                asset_class_by_symbol[code] = _symbol_asset_class_from_market(
                    symbol_row["market"]
                )
            if category == "company_analysis":
                inferred_symbol, inferred_company = _extract_company_symbol_from_text(
                    f"{normalized_title}\n{text[:12000]}\n{text[-12000:] if len(text) > 12000 else ''}",
                    symbol_names=symbol_names,
                    published_at=normalized_published_at,
                )
                if inferred_symbol:
                    mapped_source, mapped_confidence = symbol_meta.get(
                        inferred_symbol,
                        ("", 0.0),
                    )
                    selected_company = _choose_identity_company(
                        symbol=inferred_symbol,
                        inferred_company=inferred_company,
                        mapped_company=symbol_names.get(inferred_symbol, ""),
                        mapped_source=mapped_source,
                        mapped_confidence=mapped_confidence,
                    )
                    normalized_symbol = inferred_symbol
                    directory_source = "text_extract"
                    directory_confidence = 0.9
                    if selected_company:
                        normalized_company_name = selected_company
            elif category != "company_analysis":
                normalized_symbol = ""
                normalized_company_name = ""
                normalized_title = _improve_non_company_report_title(
                    normalized_title,
                    content=text,
                )
            if category == "company_analysis":
                normalized_title = _improve_company_report_title(
                    normalized_title,
                    content=text,
                    company_name=normalized_company_name,
                    symbol=normalized_symbol,
                )

            row = conn.execute(
                "SELECT report_id, created_at FROM reports WHERE doc_id = ? OR pdf_url = ?",
                (doc_id, pdf_url),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO reports (
                        doc_id, category, source_url, detail_url, pdf_url, title, company_name,
                        broker, analyst, symbol, published_at, crawled_at,
                        pdf_sha256, pdf_archived_path, content_source,
                        content, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        category,
                        source_url,
                        detail_url,
                        pdf_url,
                        normalized_title,
                        normalized_company_name,
                        broker,
                        analyst,
                        normalized_symbol,
                        normalized_published_at,
                        crawled_at,
                        pdf_sha256,
                        pdf_archived_path,
                        content_source,
                        text,
                        now,
                        now,
                    ),
                )
                report_id = int(
                    conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                )
            else:
                report_id = int(row["report_id"])
                created_at = str(row["created_at"] or now)
                conn.execute(
                    """
                    UPDATE reports
                    SET doc_id = ?, category = ?, source_url = ?, detail_url = ?, title = ?,
                        company_name = ?, broker = ?, analyst = ?, symbol = ?,
                        published_at = ?, crawled_at = ?, pdf_sha256 = ?, pdf_archived_path = ?,
                        content_source = ?, content = ?, created_at = ?, updated_at = ?
                    WHERE report_id = ?
                    """,
                    (
                        doc_id,
                        category,
                        source_url,
                        detail_url,
                        normalized_title,
                        normalized_company_name,
                        broker,
                        analyst,
                        normalized_symbol,
                        normalized_published_at,
                        crawled_at,
                        pdf_sha256,
                        pdf_archived_path,
                        content_source,
                        text,
                        created_at,
                        now,
                        report_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM report_chunks WHERE report_id = ?", (report_id,)
                )

            self._upsert_symbol_directory_with_conn(
                conn=conn,
                symbol=normalized_symbol,
                company_name=normalized_company_name,
                market="",
                source=directory_source,
                confidence=directory_confidence,
                status="active",
                verified_at=now,
            )
            if normalized_symbol and normalized_company_name:
                symbol_names[normalized_symbol] = normalized_company_name
                asset_class_by_symbol.setdefault(normalized_symbol, "stock")
            link_text = (
                f"{normalized_title}\n{text[:12000]}"
                f"\n{text[-12000:] if len(text) > 12000 else ''}"
            )
            symbol_links = _extract_report_symbol_links(
                link_text,
                symbol_names=symbol_names,
                asset_class_by_symbol=asset_class_by_symbol,
                published_at=normalized_published_at,
            )
            if category == "company_analysis" and normalized_symbol:
                primary_name = (
                    normalized_company_name
                    or symbol_names.get(normalized_symbol)
                    or normalized_symbol
                )
                symbol_links.insert(
                    0,
                    {
                        "symbol": normalized_symbol,
                        "name": primary_name,
                        "asset_class": asset_class_by_symbol.get(
                            normalized_symbol,
                            "stock",
                        ),
                        "link_type": "primary",
                        "source": "reports.symbol",
                        "confidence": 1.0,
                        "evidence": normalized_title,
                    },
                )
            self._delete_generated_report_symbol_links_with_conn(
                conn=conn,
                report_id=report_id,
            )
            self._upsert_report_symbol_links_with_conn(
                conn=conn,
                report_id=report_id,
                links=symbol_links,
            )

            for idx, row_payload in enumerate(chunk_rows):
                chunk_text = str(row_payload.get("content") or "").strip()
                if not chunk_text:
                    continue
                conn.execute(
                    """
                    INSERT INTO report_chunks (
                        report_id, chunk_index, page_start, page_end, section_title, content, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        idx,
                        _safe_non_negative_int(row_payload.get("page_start")),
                        _safe_non_negative_int(row_payload.get("page_end")),
                        str(row_payload.get("section_title") or "unknown")[:120],
                        chunk_text,
                        now,
                    ),
                )
            if isinstance(structured_facts, dict):
                self._upsert_report_facts_with_conn(
                    conn=conn,
                    report_id=report_id,
                    facts=structured_facts,
                )
            self._invalidate_status_cache()
            return report_id

    def _upsert_symbol_directory_with_conn(
        self,
        *,
        conn: sqlite3.Connection,
        symbol: str,
        company_name: str,
        market: str,
        source: str,
        confidence: float,
        status: str,
        verified_at: str,
        asset_class: str = "",
    ) -> None:
        code = str(symbol or "").strip()
        name = _clean_company_name(company_name)
        if not _is_six_digit_symbol(code) or not name:
            return
        now = str(verified_at or utc_now_iso())
        market_text = str(market or "").strip()[:24]
        asset_class_text = (
            str(asset_class or "").strip().lower()
            or _symbol_asset_class_from_market(market_text)
        )[:24]
        conn.execute(
            """
            INSERT INTO symbol_directory(
                symbol,
                company_name,
                market,
                asset_class,
                status,
                source,
                confidence,
                first_seen_at,
                updated_at,
                last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                company_name=CASE
                    WHEN excluded.company_name <> ''
                      AND (
                        TRIM(COALESCE(symbol_directory.company_name, '')) = ''
                        OR excluded.confidence > symbol_directory.confidence
                        OR (
                          symbol_directory.source NOT IN ('pykrx', 'krx', 'krx_lookup')
                          AND excluded.source IN ('pykrx', 'krx', 'krx_lookup')
                          AND excluded.confidence >= 0.99
                        )
                        OR (
                          symbol_directory.source NOT IN ('pykrx', 'krx', 'krx_lookup', 'metadata_repair', 'text_extract')
                          AND excluded.source IN ('metadata_repair', 'text_extract')
                          AND excluded.confidence >= 0.85
                        )
                        OR (
                          symbol_directory.source = 'naver_reports'
                          AND excluded.source IN ('metadata_repair', 'text_extract')
                          AND excluded.confidence >= symbol_directory.confidence
                        )
                      )
                    THEN excluded.company_name
                    ELSE symbol_directory.company_name
                END,
                market=CASE
                    WHEN excluded.market <> '' THEN excluded.market
                    ELSE symbol_directory.market
                END,
                asset_class=CASE
                    WHEN excluded.asset_class <> ''
                      AND (
                        LOWER(excluded.asset_class) <> 'stock'
                        OR excluded.market <> ''
                        OR TRIM(COALESCE(symbol_directory.asset_class, '')) = ''
                      )
                    THEN excluded.asset_class
                    ELSE symbol_directory.asset_class
                END,
                status=CASE
                    WHEN excluded.status <> '' THEN excluded.status
                    ELSE symbol_directory.status
                END,
                source=CASE
                    WHEN excluded.source <> ''
                      AND excluded.confidence >= symbol_directory.confidence
                    THEN excluded.source
                    ELSE symbol_directory.source
                END,
                confidence=CASE
                    WHEN excluded.confidence > symbol_directory.confidence
                        THEN excluded.confidence
                    ELSE symbol_directory.confidence
                END,
                updated_at=excluded.updated_at,
                last_verified_at=excluded.last_verified_at
            """,
            (
                code,
                name,
                market_text,
                asset_class_text or "stock",
                str(status or "active").strip()[:24],
                str(source or "unknown").strip()[:40],
                max(min(float(confidence), 1.0), 0.0),
                now,
                now,
                now,
            ),
        )

    def upsert_symbol_directory(
        self,
        *,
        symbol: str,
        company_name: str,
        market: str = "",
        asset_class: str = "",
        source: str = "manual",
        confidence: float = 1.0,
        status: str = "active",
        verified_at: str = "",
    ) -> None:
        with self._connect() as conn:
            self._upsert_symbol_directory_with_conn(
                conn=conn,
                symbol=symbol,
                company_name=company_name,
                market=market,
                source=source,
                confidence=confidence,
                status=status,
                verified_at=verified_at or utc_now_iso(),
                asset_class=asset_class,
            )
        self._invalidate_status_cache()

    def seed_symbol_directory(self, items: list[dict[str, Any]]) -> int:
        updated = 0
        now = utc_now_iso()
        with self._connect() as conn:
            for item in items:
                symbol = _clean_report_symbol(item.get("symbol"))
                name = _clean_symbol_link_name(
                    item.get("name") or item.get("company_name")
                )
                if not symbol or not name:
                    continue
                market = str(item.get("market") or "").strip()
                asset_class = str(item.get("asset_class") or "").strip()
                source = str(item.get("source") or "configured_etf").strip()
                self._upsert_symbol_directory_with_conn(
                    conn=conn,
                    symbol=symbol,
                    company_name=name,
                    market=market,
                    source=source or "configured_etf",
                    confidence=1.0,
                    status=str(item.get("status") or "active"),
                    verified_at=now,
                    asset_class=asset_class,
                )
                updated += 1
        if updated:
            self._invalidate_status_cache()
        return updated

    def upsert_report_symbol_links(
        self,
        report_id: int,
        links: list[dict[str, Any]],
    ) -> int:
        with self._connect() as conn:
            written = self._upsert_report_symbol_links_with_conn(
                conn=conn,
                report_id=report_id,
                links=links,
            )
        if written:
            self._invalidate_status_cache()
        return written

    def _upsert_report_symbol_links_with_conn(
        self,
        *,
        conn: sqlite3.Connection,
        report_id: int,
        links: list[dict[str, Any]],
        directory_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> int:
        rid = int(report_id)
        if rid <= 0:
            return 0
        now = utc_now_iso()
        seen: set[tuple[str, str]] = set()
        updated = 0
        directory_by_symbol = directory_by_symbol or self._symbol_directory_map_with_conn(conn)
        for link in links[:20]:
            symbol = _clean_report_symbol(link.get("symbol"))
            if not symbol:
                continue
            link_type = str(link.get("link_type") or "mention").strip()[:24]
            key = (symbol, link_type)
            if key in seen:
                continue
            seen.add(key)
            name = _clean_symbol_link_name(link.get("name"))
            asset_class = str(link.get("asset_class") or "stock").strip().lower()[:24]
            source = str(link.get("source") or "unknown").strip()[:40]
            evidence = _clean_metadata_text(link.get("evidence"), limit=180)
            try:
                confidence = float(link.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            directory = directory_by_symbol.get(symbol) or {}
            directory_name = _clean_symbol_link_name(directory.get("name"))
            directory_asset_class = str(directory.get("asset_class") or "").strip()
            if directory_asset_class and asset_class == "stock":
                asset_class = directory_asset_class
            if (
                asset_class == "stock"
                and directory_name
                and name
                and not _company_names_overlap(name, directory_name)
                and _is_trusted_symbol_link_directory(
                    directory.get("source"),
                    directory.get("confidence"),
                )
            ):
                if source in {"text_extract", "reports.symbol"}:
                    continue
                name = directory_name
            elif not name and directory_name:
                name = directory_name
            conn.execute(
                """
                INSERT INTO report_symbol_links(
                    report_id, symbol, name, asset_class, link_type, source,
                    confidence, evidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id, symbol, link_type) DO UPDATE SET
                    name=excluded.name,
                    asset_class=excluded.asset_class,
                    source=excluded.source,
                    confidence=excluded.confidence,
                    evidence=excluded.evidence,
                    updated_at=excluded.updated_at
                """,
                (
                    rid,
                    symbol,
                    name,
                    asset_class or "stock",
                    link_type or "mention",
                    source or "unknown",
                    max(min(confidence, 1.0), 0.0),
                    evidence,
                    now,
                    now,
                ),
            )
            updated += 1
        return updated

    def _symbol_directory_map_with_conn(
        self,
        conn: sqlite3.Connection,
    ) -> dict[str, dict[str, Any]]:
        directory_rows = conn.execute(
            "SELECT symbol, company_name, market, source, confidence FROM symbol_directory"
        ).fetchall()
        return {
            str(row["symbol"] or "").strip(): {
                "name": _clean_symbol_link_name(row["company_name"]),
                "asset_class": _symbol_asset_class_from_market(row["market"]),
                "source": str(row["source"] or ""),
                "confidence": float(row["confidence"] or 0.0),
            }
            for row in directory_rows
            if _is_six_digit_symbol(row["symbol"])
        }

    @staticmethod
    def _delete_generated_report_symbol_links_with_conn(
        *,
        conn: sqlite3.Connection,
        report_id: int,
    ) -> None:
        rid = int(report_id)
        if rid <= 0:
            return
        conn.execute(
            """
            DELETE FROM report_symbol_links
            WHERE report_id = ?
              AND source IN ('text_extract', 'reports.symbol')
            """,
            (rid,),
        )

    def list_report_symbol_links(self, report_id: int) -> list[dict[str, Any]]:
        rid = int(report_id)
        if rid <= 0:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  report_id, symbol, name, asset_class, link_type, source,
                  confidence, evidence, created_at, updated_at
                FROM report_symbol_links
                WHERE report_id = ?
                ORDER BY confidence DESC, symbol ASC, link_type ASC
                """,
                (rid,),
            ).fetchall()
        return [self._format_report_symbol_link(row) for row in rows]

    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        code = _clean_report_symbol(symbol)
        if not code:
            return []
        max_rows = max(min(int(limit), 100), 1)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  r.report_id,
                  r.doc_id,
                  r.category,
                  r.title,
                  r.company_name,
                  r.broker,
                  r.analyst,
                  COALESCE(NULLIF(r.symbol, ''), l.symbol) AS symbol,
                  r.published_at,
                  r.crawled_at,
                  r.pdf_sha256,
                  r.pdf_url,
                  r.pdf_archived_path,
                  r.content_source,
                  r.detail_url,
                  r.updated_at,
                  l.name AS link_name,
                  l.asset_class,
                  l.link_type,
                  l.confidence AS link_confidence,
                  l.evidence AS link_evidence
                FROM report_symbol_links l
                JOIN reports r ON r.report_id = l.report_id
                WHERE l.symbol = ?
                ORDER BY
                  r.published_at DESC,
                  r.updated_at DESC,
                  l.confidence DESC,
                  r.report_id DESC,
                  l.symbol ASC,
                  l.link_type ASC
                LIMIT ?
                """,
                (code, max_rows),
            ).fetchall()
        return [
            {
                "report_id": int(row["report_id"]),
                "doc_id": str(row["doc_id"] or ""),
                "category": str(row["category"] or "unknown"),
                "title": str(row["title"] or ""),
                "company_name": str(row["company_name"] or row["link_name"] or ""),
                "broker": str(row["broker"] or ""),
                "analyst": str(row["analyst"] or ""),
                "symbol": str(row["symbol"] or ""),
                "published_at": str(row["published_at"] or ""),
                "crawled_at": str(row["crawled_at"] or ""),
                "pdf_sha256": str(row["pdf_sha256"] or ""),
                "pdf_url": str(row["pdf_url"] or ""),
                "pdf_archived_path": str(row["pdf_archived_path"] or ""),
                "content_source": str(row["content_source"] or ""),
                "detail_url": str(row["detail_url"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "linked_name": str(row["link_name"] or ""),
                "asset_class": str(row["asset_class"] or "stock"),
                "link_type": str(row["link_type"] or "mention"),
                "link_confidence": float(row["link_confidence"] or 0.0),
                "link_evidence": str(row["link_evidence"] or ""),
            }
            for row in rows
        ]

    def backfill_report_symbol_links(
        self,
        limit: int = 0,
        asset_class: str = "etf",
    ) -> dict[str, Any]:
        requested_asset_class = str(asset_class or "etf").strip().lower() or "etf"
        max_rows = max(int(limit), 0)
        now = utc_now_iso()
        with self._connect() as conn:
            directory_rows = conn.execute(
                """
                SELECT symbol, company_name, market, source, confidence
                FROM symbol_directory
                ORDER BY symbol ASC
                """
            ).fetchall()
            symbol_names: dict[str, str] = {}
            asset_class_by_symbol: dict[str, str] = {}
            directory_by_symbol: dict[str, dict[str, Any]] = {}
            for row in directory_rows:
                symbol = str(row["symbol"] or "").strip()
                name = _clean_symbol_link_name(row["company_name"])
                item_asset_class = _symbol_asset_class_from_market(row["market"])
                if _is_six_digit_symbol(symbol):
                    directory_by_symbol[symbol] = {
                        "name": name,
                        "asset_class": item_asset_class,
                        "source": str(row["source"] or ""),
                        "confidence": float(row["confidence"] or 0.0),
                    }
                if (
                    not _is_six_digit_symbol(symbol)
                    or not name
                    or item_asset_class != requested_asset_class
                ):
                    continue
                symbol_names[symbol] = name
                asset_class_by_symbol[symbol] = item_asset_class
            if not symbol_names:
                return {
                    "ok": True,
                    "asset_class": requested_asset_class,
                    "scanned_reports": 0,
                    "updated_reports": 0,
                    "linked_symbols": [],
                    "updated_at": now,
                }
            if requested_asset_class in {"etf", "etn"}:
                sql = """
                    SELECT report_id, title, content, published_at
                    FROM reports
                    WHERE category IN ('invest_info', 'market_info', 'industry_analysis')
                      AND (
                        title LIKE '%ETF%'
                        OR content LIKE '%ETF%'
                        OR title LIKE '%상장지수펀드%'
                        OR content LIKE '%상장지수펀드%'
                      )
                    ORDER BY published_at DESC, updated_at DESC, report_id DESC
                """
            else:
                sql = """
                    SELECT report_id, title, content, published_at
                    FROM reports
                    WHERE category IN ('invest_info', 'market_info', 'industry_analysis')
                    ORDER BY published_at DESC, updated_at DESC, report_id DESC
                """
            params: list[Any] = []
            if max_rows > 0:
                sql += " LIMIT ?"
                params.append(max_rows)
            report_rows = conn.execute(sql, params).fetchall()
            scanned_reports = 0
            updated_reports = 0
            linked_symbols: set[str] = set()
            for row in report_rows:
                scanned_reports += 1
                title = str(row["title"] or "")
                content = str(row["content"] or "")
                text = (
                    f"{title}\n{content[:12000]}"
                    f"\n{content[-12000:] if len(content) > 12000 else ''}"
                )
                scan_symbol_names = symbol_names
                scan_asset_classes = asset_class_by_symbol
                if requested_asset_class == "stock":
                    mentioned_codes = {
                        match.group(1)
                        for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text)
                        if not _is_probable_short_date_symbol(match.group(1))
                    }
                    if not mentioned_codes:
                        continue
                    scan_symbol_names = {
                        code: symbol_names[code]
                        for code in mentioned_codes
                        if code in symbol_names
                    }
                    if not scan_symbol_names:
                        continue
                    scan_asset_classes = {
                        code: asset_class_by_symbol.get(code, requested_asset_class)
                        for code in scan_symbol_names
                    }
                links = _extract_report_symbol_links(
                    text,
                    symbol_names=scan_symbol_names,
                    asset_class_by_symbol=scan_asset_classes,
                    published_at=str(row["published_at"] or ""),
                )
                written = self._upsert_report_symbol_links_with_conn(
                    conn=conn,
                    report_id=int(row["report_id"] or 0),
                    links=links,
                    directory_by_symbol=directory_by_symbol,
                )
                if written:
                    updated_reports += 1
                    linked_symbols.update(str(link["symbol"]) for link in links)
            return {
                "ok": True,
                "asset_class": requested_asset_class,
                "scanned_reports": scanned_reports,
                "updated_reports": updated_reports,
                "linked_symbols": sorted(linked_symbols),
                "updated_at": now,
            }

    @staticmethod
    def _format_report_symbol_link(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "report_id": int(row["report_id"]),
            "symbol": str(row["symbol"] or ""),
            "name": str(row["name"] or ""),
            "asset_class": str(row["asset_class"] or "stock"),
            "link_type": str(row["link_type"] or "mention"),
            "source": str(row["source"] or "unknown"),
            "confidence": float(row["confidence"] or 0.0),
            "evidence": str(row["evidence"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        codes = [
            str(symbol or "").strip()
            for symbol in symbols
            if _is_six_digit_symbol(symbol)
        ]
        unique_codes = list(dict.fromkeys(codes))
        if not unique_codes:
            return {}
        placeholders = ",".join("?" for _ in unique_codes)
        sql = f"SELECT symbol, company_name FROM symbol_directory WHERE symbol IN ({placeholders})"
        with self._connect() as conn:
            rows = conn.execute(sql, unique_codes).fetchall()
        out: dict[str, str] = {}
        for row in rows:
            code = str(row["symbol"] or "").strip()
            name = _clean_company_name(row["company_name"])
            if _is_six_digit_symbol(code) and name:
                out[code] = name
        return out

    def list_symbol_directory(
        self,
        *,
        market: str = "",
        limit: int = 100,
        exclude_symbols: set[str] | None = None,
        asset_class: str = "stock",
    ) -> list[dict[str, Any]]:
        market_filter = str(market or "").strip().upper()
        requested_asset_class = str(asset_class or "stock").strip().lower() or "stock"
        max_rows = max(int(limit), 1)
        excluded = {
            str(item or "").strip()
            for item in (exclude_symbols or set())
            if str(item or "").strip()
        }
        with self._connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(symbol_directory)").fetchall()
            }
            required = {"symbol", "company_name"}
            if not required.issubset(columns):
                return []

            select_columns = ["symbol", "company_name"]
            for column in ("market", "asset_class", "source", "confidence", "updated_at"):
                if column in columns:
                    select_columns.append(column)
            where = ["TRIM(COALESCE(symbol, '')) <> ''"]
            where.append("TRIM(COALESCE(company_name, '')) <> ''")
            params: list[Any] = []
            if "market" in columns:
                if requested_asset_class in {"etf", "etn"}:
                    where.append("UPPER(COALESCE(market, '')) = ?")
                    params.append(market_filter or requested_asset_class.upper())
                else:
                    where.append("UPPER(COALESCE(market, '')) NOT IN ('ETF', 'ETN')")
                    if market_filter:
                        where.append("UPPER(COALESCE(market, '')) = ?")
                        params.append(market_filter)
            if "asset_class" in columns:
                if requested_asset_class in {"etf", "etn"}:
                    where.append(
                        "LOWER(COALESCE(NULLIF(asset_class, ''), 'stock')) = ?"
                    )
                    params.append(requested_asset_class)
                else:
                    where.append(
                        "LOWER(COALESCE(NULLIF(asset_class, ''), 'stock')) = 'stock'"
                    )
            if "status" in columns:
                where.append(
                    """
                    LOWER(COALESCE(NULLIF(status, ''), 'active')) NOT IN (
                        'halted', 'managed', 'delisted', 'suspended',
                        'inactive', 'deleted'
                    )
                    """
                )
            query = f"""
                SELECT {', '.join(select_columns)}
                FROM symbol_directory
                WHERE {' AND '.join(where)}
                ORDER BY symbol ASC
                LIMIT ?
            """
            params.append(max_rows + len(excluded))
            rows = conn.execute(query, tuple(params)).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row["symbol"] or "").strip()
            name = _clean_company_name(row["company_name"])
            if not _is_six_digit_symbol(symbol) or not name or symbol in excluded:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "market": str(row["market"] or "") if "market" in row.keys() else "",
                    "source": str(row["source"] or "") if "source" in row.keys() else "",
                    "confidence": (
                        float(row["confidence"] or 0.0)
                        if "confidence" in row.keys()
                        else 0.0
                    ),
                    "updated_at": (
                        str(row["updated_at"] or "") if "updated_at" in row.keys() else ""
                    ),
                    "asset_class": (
                        str(row["asset_class"] or "").strip().lower()
                        if "asset_class" in row.keys()
                        else _symbol_asset_class_from_market(
                            str(row["market"] or "") if "market" in row.keys() else ""
                        )
                    )
                    or requested_asset_class,
                }
            )
            if len(out) >= max_rows:
                break
        return out

    def get_symbol_name(self, symbol: str) -> str:
        return str(
            self.resolve_symbol_names([symbol]).get(str(symbol or "").strip()) or ""
        )

    def refresh_symbol_directory_from_krx(self, as_of: str = "") -> dict[str, Any]:
        as_of_date = str(as_of or date.today().isoformat())
        now = utc_now_iso()
        rows, errors = _fetch_krx_symbol_directory_direct()

        merged: dict[str, tuple[str, str]] = {}
        for code, name, market in rows:
            if not _is_six_digit_symbol(code) or not name:
                continue
            merged[code] = (name, market)

        if not merged:
            return {
                "ok": False,
                "reason": "krx_symbol_directory_unavailable",
                "detail": "; ".join(errors)[:300],
                "updated": 0,
                "as_of": as_of_date,
                "updated_at": now,
            }

        with self._connect() as conn:
            for code, (name, market) in merged.items():
                self._upsert_symbol_directory_with_conn(
                    conn=conn,
                    symbol=code,
                    company_name=name,
                    market=market,
                    source="krx",
                    confidence=1.0,
                    status="active",
                    verified_at=now,
                )
        self._invalidate_status_cache()

        return {
            "ok": True,
            "updated": len(merged),
            "errors": errors[:12],
            "as_of": as_of_date,
            "updated_at": now,
        }

    def repair_metadata_quality(self, limit: int = 0) -> dict[str, Any]:
        max_rows = max(int(limit), 0)
        now = utc_now_iso()
        updated_reports = 0
        cleaned_titles = 0
        cleaned_company_names = 0
        cleaned_brokers = 0
        cleaned_analysts = 0
        cleared_symbols = 0
        backfilled_symbols = 0
        backfilled_company_names = 0
        backfilled_brokers = 0
        backfilled_analysts = 0
        corrected_symbols = 0
        corrected_company_names = 0
        corrected_brokers = 0
        cleaned_symbol_directory = 0
        deleted_symbol_directory = 0
        cleaned_published_at = 0
        canonicalized_symbol_links = 0
        deleted_symbol_links = 0

        with self._connect() as conn:
            symbol_rows = conn.execute(
                "SELECT symbol, company_name, market, source, confidence FROM symbol_directory"
            ).fetchall()
            clean_symbol_map: dict[str, str] = {}
            clean_symbol_meta: dict[str, tuple[str, float]] = {}
            asset_class_by_symbol: dict[str, str] = {}
            for row in symbol_rows:
                code = str(row["symbol"] or "").strip()
                name = _clean_company_candidate(row["company_name"])
                if not _is_six_digit_symbol(code) or not name:
                    conn.execute("DELETE FROM symbol_directory WHERE symbol = ?", (code,))
                    deleted_symbol_directory += 1
                    continue
                clean_symbol_map[code] = name
                clean_symbol_meta[code] = (
                    str(row["source"] or ""),
                    float(row["confidence"] or 0.0),
                )
                asset_class_by_symbol[code] = _symbol_asset_class_from_market(
                    row["market"]
                )
                if name != str(row["company_name"] or ""):
                    conn.execute(
                        """
                        UPDATE symbol_directory
                        SET company_name = ?, updated_at = ?
                        WHERE symbol = ?
                        """,
                        (name, now, code),
                    )
                    cleaned_symbol_directory += 1

            sql = """
                SELECT report_id, category, title, company_name, broker, analyst,
                       symbol, published_at, content, pdf_url
                FROM reports
                ORDER BY report_id ASC
            """
            params: tuple[Any, ...] = ()
            if max_rows > 0:
                sql += " LIMIT ?"
                params = (max_rows,)

            report_rows = conn.execute(sql, params).fetchall()
            for row in report_rows:
                report_id = int(row["report_id"])
                category = str(row["category"] or "unknown")
                current_title = str(row["title"] or "")
                current_company_name = str(row["company_name"] or "")
                current_broker = str(row["broker"] or "")
                current_analyst = str(row["analyst"] or "")
                current_symbol = str(row["symbol"] or "").strip()
                current_published_at = str(row["published_at"] or "")
                published_at = _normalize_report_date(current_published_at)
                if current_published_at and published_at != current_published_at:
                    cleaned_published_at += 1
                content = str(row["content"] or "")
                content_tail = content[-12000:] if len(content) > 12000 else ""
                metadata_text = f"{current_title}\n{content[:12000]}\n{content_tail}"

                title = _clean_report_title(
                    current_title,
                    content=content,
                    category=category,
                )
                symbol = _clean_report_symbol(
                    current_symbol,
                    published_at=published_at,
                )
                company_name = _clean_company_candidate(current_company_name)
                broker = _clean_broker_name(current_broker)
                analyst = _clean_analyst_name(current_analyst)

                if category != "company_analysis":
                    symbol = ""
                    company_name = ""
                    title = _improve_non_company_report_title(
                        title,
                        content=content,
                    )

                inferred_symbol = ""
                inferred_company = ""
                if category == "company_analysis":
                    inferred_symbol, inferred_company = _extract_company_symbol_from_text(
                        metadata_text,
                        symbol_names=clean_symbol_map,
                        published_at=published_at,
                    )
                    mapped_source, mapped_confidence = clean_symbol_meta.get(
                        inferred_symbol,
                        ("", 0.0),
                    )
                    resolved_inferred_company = _choose_identity_company(
                        symbol=inferred_symbol,
                        inferred_company=inferred_company,
                        mapped_company=clean_symbol_map.get(inferred_symbol, ""),
                        mapped_source=mapped_source,
                        mapped_confidence=mapped_confidence,
                    )
                    if inferred_symbol and not symbol:
                        symbol = inferred_symbol
                        backfilled_symbols += 1
                    elif inferred_symbol and symbol != inferred_symbol:
                        symbol = inferred_symbol
                        corrected_symbols += 1

                    if resolved_inferred_company:
                        if not company_name:
                            company_name = resolved_inferred_company
                            backfilled_company_names += 1
                        elif company_name != resolved_inferred_company:
                            company_name = resolved_inferred_company
                            corrected_company_names += 1

                    if symbol and symbol in clean_symbol_map:
                        mapped_company = clean_symbol_map[symbol]
                        mapped_source, mapped_confidence = clean_symbol_meta.get(
                            symbol,
                            ("", 0.0),
                        )
                        prefer_mapped = (
                            mapped_company
                            and not _company_names_overlap(company_name, mapped_company)
                            and _is_authoritative_symbol_source(
                                mapped_source,
                                mapped_confidence,
                            )
                        )
                        if mapped_company and (
                            not company_name
                            or _company_names_overlap(company_name, mapped_company)
                            or prefer_mapped
                        ) and company_name != mapped_company:
                            if company_name:
                                corrected_company_names += 1
                            else:
                                backfilled_company_names += 1
                            company_name = mapped_company

                    if symbol and symbol not in clean_symbol_map and not company_name:
                        symbol = ""

                    title = _improve_company_report_title(
                        title,
                        content=content,
                        company_name=company_name,
                        symbol=symbol,
                    )

                strong_broker = _extract_strong_broker_from_text(metadata_text)
                if strong_broker and broker != strong_broker:
                    broker = strong_broker
                    corrected_brokers += 1
                elif not broker:
                    inferred_broker = _extract_broker_from_text(metadata_text)
                    if inferred_broker:
                        broker = inferred_broker
                        backfilled_brokers += 1

                if not analyst:
                    inferred_analyst = _extract_analyst_from_text(metadata_text)
                    if inferred_analyst:
                        analyst = inferred_analyst
                        backfilled_analysts += 1
                if not analyst:
                    inferred_department_author = _extract_department_author_from_text(
                        metadata_text,
                        broker=broker or current_broker,
                    )
                    if inferred_department_author:
                        analyst = inferred_department_author
                        backfilled_analysts += 1
                if not analyst:
                    inferred_default_author = _infer_default_department_author_from_context(
                        broker=broker or current_broker,
                        category=category,
                        text=metadata_text,
                    )
                    if inferred_default_author:
                        analyst = inferred_default_author
                        backfilled_analysts += 1

                identity_verified = bool(
                    category == "company_analysis"
                    and symbol
                    and company_name
                    and (symbol in clean_symbol_map or inferred_symbol == symbol)
                )

                if title != current_title:
                    cleaned_titles += 1
                if company_name != current_company_name:
                    cleaned_company_names += 1
                if broker != current_broker:
                    cleaned_brokers += 1
                if symbol != current_symbol:
                    cleared_symbols += 1
                if analyst != current_analyst:
                    cleaned_analysts += 1

                if (
                    title == current_title
                    and company_name == current_company_name
                    and broker == current_broker
                    and symbol == current_symbol
                    and analyst == current_analyst
                    and published_at == current_published_at
                ):
                    if (
                        identity_verified
                        and clean_symbol_map.get(symbol) != company_name
                    ):
                        self._upsert_symbol_directory_with_conn(
                            conn=conn,
                            symbol=symbol,
                            company_name=company_name,
                            market="",
                            source="metadata_repair",
                            confidence=0.9,
                            status="active",
                            verified_at=now,
                        )
                        clean_symbol_map[symbol] = company_name
                        clean_symbol_meta[symbol] = ("metadata_repair", 0.9)
                    continue

                conn.execute(
                    """
                    UPDATE reports
                    SET title = ?, company_name = ?, broker = ?, analyst = ?,
                        symbol = ?, published_at = ?, updated_at = ?
                    WHERE report_id = ?
                    """,
                    (
                        title,
                        company_name,
                        broker,
                        analyst,
                        symbol,
                        published_at,
                        now,
                        report_id,
                    ),
                )
                updated_reports += 1

                if identity_verified:
                    self._upsert_symbol_directory_with_conn(
                        conn=conn,
                        symbol=symbol,
                        company_name=company_name,
                        market="",
                        source="metadata_repair",
                        confidence=0.9,
                        status="active",
                        verified_at=now,
                    )
                    clean_symbol_map[symbol] = company_name
                    clean_symbol_meta[symbol] = ("metadata_repair", 0.9)

            link_rows = conn.execute(
                """
                SELECT
                  l.report_id,
                  l.symbol,
                  l.name,
                  l.asset_class,
                  l.link_type,
                  l.source,
                  l.evidence
                FROM report_symbol_links l
                ORDER BY l.report_id ASC
                """
            ).fetchall()
            for link in link_rows:
                code = str(link["symbol"] or "").strip()
                if not _is_six_digit_symbol(code):
                    continue
                asset_class = str(link["asset_class"] or "stock").strip().lower()
                if asset_class_by_symbol.get(code, asset_class) != "stock":
                    continue
                directory_name = clean_symbol_map.get(code, "")
                directory_source, directory_confidence = clean_symbol_meta.get(
                    code,
                    ("", 0.0),
                )
                link_name = _clean_symbol_link_name(link["name"])
                if (
                    not directory_name
                    or not link_name
                    or _company_names_overlap(link_name, directory_name)
                    or not _is_trusted_symbol_link_directory(
                        directory_source,
                        directory_confidence,
                    )
                ):
                    continue
                source = str(link["source"] or "")
                if source in {"text_extract", "reports.symbol"}:
                    conn.execute(
                        """
                        DELETE FROM report_symbol_links
                        WHERE report_id = ? AND symbol = ? AND link_type = ?
                        """,
                        (
                            int(link["report_id"] or 0),
                            code,
                            str(link["link_type"] or "mention"),
                        ),
                    )
                    deleted_symbol_links += 1
                    continue
                conn.execute(
                    """
                    UPDATE report_symbol_links
                    SET name = ?, updated_at = ?
                    WHERE report_id = ? AND symbol = ? AND link_type = ?
                    """,
                    (
                        directory_name,
                        now,
                        int(link["report_id"] or 0),
                        code,
                        str(link["link_type"] or "mention"),
                    ),
                )
                canonicalized_symbol_links += 1

        if (
            updated_reports
            or cleaned_titles
            or cleaned_company_names
            or cleaned_brokers
            or cleaned_analysts
            or cleared_symbols
            or backfilled_symbols
            or backfilled_company_names
            or backfilled_brokers
            or backfilled_analysts
            or corrected_symbols
            or corrected_company_names
            or corrected_brokers
            or cleaned_symbol_directory
            or deleted_symbol_directory
            or cleaned_published_at
            or canonicalized_symbol_links
            or deleted_symbol_links
        ):
            self._invalidate_status_cache()

        return {
            "status": "ok",
            "updated_at": now,
            "scanned_reports": len(report_rows),
            "updated_reports": updated_reports,
            "cleaned_titles": cleaned_titles,
            "cleaned_company_names": cleaned_company_names,
            "cleaned_brokers": cleaned_brokers,
            "cleaned_analysts": cleaned_analysts,
            "cleared_symbols": cleared_symbols,
            "backfilled_symbols": backfilled_symbols,
            "backfilled_company_names": backfilled_company_names,
            "backfilled_brokers": backfilled_brokers,
            "backfilled_analysts": backfilled_analysts,
            "corrected_symbols": corrected_symbols,
            "corrected_company_names": corrected_company_names,
            "corrected_brokers": corrected_brokers,
            "cleaned_published_at": cleaned_published_at,
            "cleaned_symbol_directory": cleaned_symbol_directory,
            "deleted_symbol_directory": deleted_symbol_directory,
            "canonicalized_symbol_links": canonicalized_symbol_links,
            "deleted_symbol_links": deleted_symbol_links,
        }

    def upsert_report_facts(self, report_id: int, facts: dict[str, Any]) -> None:
        with self._connect() as conn:
            self._upsert_report_facts_with_conn(
                conn=conn,
                report_id=report_id,
                facts=facts,
            )
        self._invalidate_status_cache()

    def _upsert_report_facts_with_conn(
        self,
        *,
        conn: sqlite3.Connection,
        report_id: int,
        facts: dict[str, Any],
    ) -> None:
        rid = int(report_id)
        if rid <= 0:
            return

        target = facts.get("target_price")
        if not isinstance(target, dict):
            target = {}
        valuation = facts.get("valuation")
        if not isinstance(valuation, dict):
            valuation = {}
        valuation_value_raw = valuation.get("value")
        valuation_value = (
            _safe_float(valuation_value_raw)
            if isinstance(valuation_value_raw, (int, float, str))
            else 0.0
        )

        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO report_facts (
                report_id,
                rating,
                target_price_value,
                target_price_currency,
                target_price_changed,
                valuation_method,
                valuation_value,
                valuation_basis,
                valuation_notes,
                summary_bullets_json,
                investment_thesis_json,
                risks_json,
                earnings_outlook_json,
                catalysts_json,
                evidence_quotes_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO UPDATE SET
                rating=excluded.rating,
                target_price_value=excluded.target_price_value,
                target_price_currency=excluded.target_price_currency,
                target_price_changed=excluded.target_price_changed,
                valuation_method=excluded.valuation_method,
                valuation_value=excluded.valuation_value,
                valuation_basis=excluded.valuation_basis,
                valuation_notes=excluded.valuation_notes,
                summary_bullets_json=excluded.summary_bullets_json,
                investment_thesis_json=excluded.investment_thesis_json,
                risks_json=excluded.risks_json,
                earnings_outlook_json=excluded.earnings_outlook_json,
                catalysts_json=excluded.catalysts_json,
                evidence_quotes_json=excluded.evidence_quotes_json,
                updated_at=excluded.updated_at
            """,
            (
                rid,
                str(facts.get("rating") or "UNKNOWN")[:24],
                _safe_non_negative_int(target.get("value")),
                str(target.get("currency") or "KRW")[:12],
                str(target.get("changed") or "UNKNOWN")[:24],
                str(valuation.get("method") or "UNKNOWN")[:24],
                valuation_value if valuation_value > 0 else None,
                str(valuation.get("basis") or "")[:32],
                str(valuation.get("notes") or "")[:400],
                json.dumps(
                    list(facts.get("summary_bullets") or [])[:8], ensure_ascii=False
                ),
                json.dumps(
                    list(facts.get("investment_thesis") or [])[:8],
                    ensure_ascii=False,
                ),
                json.dumps(list(facts.get("risks") or [])[:8], ensure_ascii=False),
                json.dumps(
                    list(facts.get("earnings_outlook") or [])[:8],
                    ensure_ascii=False,
                ),
                json.dumps(list(facts.get("catalysts") or [])[:8], ensure_ascii=False),
                json.dumps(
                    list(facts.get("evidence_quotes") or [])[:12],
                    ensure_ascii=False,
                ),
                now,
            ),
        )

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        rid = int(report_id)
        if rid <= 0:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  rating,
                  target_price_value,
                  target_price_currency,
                  target_price_changed,
                  valuation_method,
                  valuation_value,
                  valuation_basis,
                  valuation_notes,
                  summary_bullets_json,
                  investment_thesis_json,
                  risks_json,
                  earnings_outlook_json,
                  catalysts_json,
                  evidence_quotes_json,
                  updated_at
                FROM report_facts
                WHERE report_id = ?
                """,
                (rid,),
            ).fetchone()
            if row is None:
                return None
            return {
                "rating": str(row["rating"] or "UNKNOWN"),
                "target_price": {
                    "value": int(row["target_price_value"] or 0),
                    "currency": str(row["target_price_currency"] or "KRW"),
                    "changed": str(row["target_price_changed"] or "UNKNOWN"),
                },
                "valuation": {
                    "method": str(row["valuation_method"] or "UNKNOWN"),
                    "value": row["valuation_value"],
                    "basis": str(row["valuation_basis"] or ""),
                    "notes": str(row["valuation_notes"] or ""),
                },
                "summary_bullets": self._parse_json_array(row["summary_bullets_json"]),
                "investment_thesis": self._parse_json_array(
                    row["investment_thesis_json"]
                ),
                "risks": self._parse_json_array(row["risks_json"]),
                "earnings_outlook": self._parse_json_array(
                    row["earnings_outlook_json"]
                ),
                "catalysts": self._parse_json_array(row["catalysts_json"]),
                "evidence_quotes": self._parse_json_array(row["evidence_quotes_json"]),
                "updated_at": str(row["updated_at"] or ""),
            }

    @staticmethod
    def _parse_json_array(value: Any) -> list[Any]:
        raw = str(value or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
        return []

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        mtime_ns = self._db_mtime_ns()
        if self.status_cache_ttl_sec > 0 and self._status_cache is not None:
            cached_at, cached_mtime_ns, cached_payload = self._status_cache
            if (
                cached_mtime_ns == mtime_ns
                and now - cached_at <= self.status_cache_ttl_sec
            ):
                return copy.deepcopy(cached_payload)

        payload = self._compute_status()
        self._status_cache = (now, self._db_mtime_ns(), copy.deepcopy(payload))
        return payload

    def ops_status(self) -> dict[str, Any]:
        now = time.monotonic()
        mtime_ns = self._db_mtime_ns()
        if self.status_cache_ttl_sec > 0 and self._ops_status_cache is not None:
            cached_at, cached_mtime_ns, cached_payload = self._ops_status_cache
            if (
                cached_mtime_ns == mtime_ns
                and now - cached_at <= self.status_cache_ttl_sec
            ):
                return copy.deepcopy(cached_payload)

        disk_payload = self._read_ops_status_disk_cache(mtime_ns=mtime_ns)
        if disk_payload is not None:
            self._ops_status_cache = (now, mtime_ns, copy.deepcopy(disk_payload))
            return disk_payload

        payload = self._compute_ops_status()
        cached_mtime_ns = self._db_mtime_ns()
        self._ops_status_cache = (now, cached_mtime_ns, copy.deepcopy(payload))
        self._write_ops_status_disk_cache(
            mtime_ns=cached_mtime_ns,
            status=payload,
        )
        return payload

    def _compute_ops_status(self) -> dict[str, Any]:
        with self._connect_readonly() as conn:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_reports,
                  MAX(updated_at) AS last_updated_at,
                  MAX(published_at) AS last_published_at
                FROM reports
                """
            ).fetchone()
            cat_rows = conn.execute(
                "SELECT category, COUNT(*) AS cnt FROM reports GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            symbol_row = conn.execute(
                "SELECT COUNT(*) AS cnt, MAX(updated_at) AS last_updated_at FROM symbol_directory"
            ).fetchone()
            symbol_link_row = conn.execute(
                """
                SELECT
                  COUNT(*) AS symbol_link_count,
                  SUM(CASE WHEN asset_class = 'etf' THEN 1 ELSE 0 END) AS etf_link_count,
                  COUNT(DISTINCT report_id) AS linked_report_count,
                  MAX(updated_at) AS last_symbol_link_updated_at
                FROM report_symbol_links
                """
            ).fetchone()
            facts_row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_facts,
                  SUM(CASE WHEN target_price_value > 0 THEN 1 ELSE 0 END) AS target_price_count,
                  SUM(CASE WHEN rating <> 'UNKNOWN' THEN 1 ELSE 0 END) AS rating_count
                FROM report_facts
                """
            ).fetchone()
            return {
                "status": "ok",
                "quality_mode": "lightweight",
                "total_reports": int(row["total_reports"] or 0),
                "last_updated_at": str(row["last_updated_at"] or ""),
                "last_published_at": str(row["last_published_at"] or ""),
                "category_counts": {
                    str(item["category"] or "unknown"): int(item["cnt"] or 0)
                    for item in cat_rows
                },
                "total_symbols": int(symbol_row["cnt"] or 0),
                "symbol_last_updated_at": str(symbol_row["last_updated_at"] or ""),
                "symbol_link_count": int(
                    symbol_link_row["symbol_link_count"] or 0
                ),
                "etf_link_count": int(symbol_link_row["etf_link_count"] or 0),
                "linked_report_count": int(
                    symbol_link_row["linked_report_count"] or 0
                ),
                "last_symbol_link_updated_at": str(
                    symbol_link_row["last_symbol_link_updated_at"] or ""
                ),
                "facts": {
                    "total_facts": int(facts_row["total_facts"] or 0),
                    "target_price_count": int(facts_row["target_price_count"] or 0),
                    "rating_count": int(facts_row["rating_count"] or 0),
                },
                "db_path": str(self.path),
            }

    def _compute_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_reports,
                  MAX(updated_at) AS last_updated_at,
                  MAX(published_at) AS last_published_at
                FROM reports
                """
            ).fetchone()
            cat_rows = conn.execute(
                "SELECT category, COUNT(*) AS cnt FROM reports GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            symbol_row = conn.execute(
                "SELECT COUNT(*) AS cnt, MAX(updated_at) AS last_updated_at FROM symbol_directory"
            ).fetchone()
            facts_row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_facts,
                  SUM(CASE WHEN target_price_value > 0 THEN 1 ELSE 0 END) AS target_price_count,
                  SUM(CASE WHEN rating <> 'UNKNOWN' THEN 1 ELSE 0 END) AS rating_count
                FROM report_facts
                """
            ).fetchone()
            symbol_link_row = conn.execute(
                """
                SELECT
                  COUNT(*) AS symbol_link_count,
                  SUM(CASE WHEN asset_class = 'etf' THEN 1 ELSE 0 END) AS etf_link_count,
                  COUNT(DISTINCT report_id) AS linked_report_count,
                  MAX(updated_at) AS last_symbol_link_updated_at
                FROM report_symbol_links
                """
            ).fetchone()
            unlinked_etf_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM reports r
                WHERE r.category IN ('invest_info', 'market_info', 'industry_analysis')
                  AND (
                    r.title LIKE '%ETF%'
                    OR r.content LIKE '%ETF%'
                    OR r.title LIKE '%상장지수펀드%'
                    OR r.content LIKE '%상장지수펀드%'
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM report_symbol_links l
                    WHERE l.report_id = r.report_id
                      AND l.asset_class = 'etf'
                  )
                """
            ).fetchone()
            quality_row = conn.execute(
                """
                SELECT
                  SUM(
                    CASE
                      WHEN category = 'company_analysis' AND TRIM(company_name) = ''
                      THEN 1
                      ELSE 0
                    END
                  ) AS missing_company_name_count,
                  SUM(
                    CASE
                      WHEN title LIKE '%<img%'
                        OR title LIKE '%</%'
                        OR title LIKE '%<a %'
                        OR title LIKE '%<span%'
                        OR title LIKE '%<div%'
                        OR title LIKE '%<br%'
                        OR title LIKE '%&lt;img%'
                        OR title LIKE '%&lt;/%'
                        OR title LIKE '%&lt;a %'
                        OR title LIKE '%&lt;span%'
                        OR title LIKE '%&lt;div%'
                        OR title LIKE '%&lt;br%'
                        OR title LIKE '%리포트 보기%'
                        OR title LIKE '%btn_report.gif%'
                      THEN 1
                      ELSE 0
                    END
                  ) AS html_title_count,
                  SUM(
                    CASE
                      WHEN company_name LIKE '%<img%'
                        OR company_name LIKE '%</%'
                        OR company_name LIKE '%<a %'
                        OR company_name LIKE '%<span%'
                        OR company_name LIKE '%<div%'
                        OR company_name LIKE '%<br%'
                        OR company_name LIKE '%&lt;img%'
                        OR company_name LIKE '%&lt;/%'
                        OR company_name LIKE '%&lt;a %'
                        OR company_name LIKE '%&lt;span%'
                        OR company_name LIKE '%&lt;div%'
                        OR company_name LIKE '%&lt;br%'
                        OR company_name LIKE '%리포트 보기%'
                        OR company_name LIKE '%btn_report.gif%'
                      THEN 1
                      ELSE 0
                    END
                  ) AS html_company_name_count,
                  SUM(
                    CASE
                      WHEN category = 'company_analysis' AND TRIM(symbol) = ''
                      THEN 1
                      ELSE 0
                    END
                  ) AS missing_symbol_count,
                  SUM(CASE WHEN TRIM(broker) = '' THEN 1 ELSE 0 END) AS missing_broker_count,
                  SUM(CASE WHEN TRIM(analyst) = '' THEN 1 ELSE 0 END) AS missing_analyst_count,
                  SUM(
                    CASE WHEN TRIM(category) = '' OR category = 'unknown' THEN 1 ELSE 0 END
                  ) AS unknown_category_count
                FROM reports
                """
            ).fetchone()
            drift_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM reports r
                LEFT JOIN symbol_directory s ON s.symbol = r.symbol
                WHERE TRIM(r.symbol) <> ''
                  AND (
                    s.symbol IS NULL
                    OR (
                      TRIM(COALESCE(s.company_name, '')) <> ''
                      AND TRIM(COALESCE(r.company_name, '')) <> ''
                      AND s.company_name <> r.company_name
                    )
                  )
                """
            ).fetchone()
            identity_rows = conn.execute(
                """
                SELECT
                  r.report_id,
                  r.symbol,
                  r.company_name,
                  r.title,
                  s.company_name AS directory_name
                FROM reports r
                LEFT JOIN symbol_directory s ON s.symbol = r.symbol
                WHERE r.category = 'company_analysis'
                  AND TRIM(COALESCE(r.symbol, '')) <> ''
                ORDER BY r.published_at DESC, r.report_id DESC
                """
            ).fetchall()
            directory_quality_rows = conn.execute(
                "SELECT symbol, company_name, source, confidence FROM symbol_directory"
            ).fetchall()
            published_rows = conn.execute(
                """
                SELECT report_id, published_at, title
                FROM reports
                WHERE TRIM(COALESCE(published_at, '')) <> ''
                ORDER BY report_id DESC
                """
            ).fetchall()
            symbol_link_quality_rows = conn.execute(
                """
                SELECT
                  l.report_id,
                  l.symbol,
                  l.name,
                  l.asset_class,
                  l.link_type,
                  l.source,
                  l.evidence,
                  s.company_name AS directory_name,
                  s.source AS directory_source,
                  s.confidence AS directory_confidence
                FROM report_symbol_links l
                LEFT JOIN symbol_directory s ON s.symbol = l.symbol
                WHERE LOWER(COALESCE(NULLIF(l.asset_class, ''), 'stock')) = 'stock'
                ORDER BY l.updated_at DESC, l.report_id DESC
                """
            ).fetchall()
            category_counts = {
                str(item["category"] or "unknown"): int(item["cnt"] or 0)
                for item in cat_rows
            }
            generic_symbol_directory_count = sum(
                1
                for item in directory_quality_rows
                if not _clean_company_candidate(item["company_name"])
            )
            identity_suspect_count = 0
            identity_drift_samples: list[dict[str, Any]] = []
            for item in identity_rows:
                code = str(item["symbol"] or "").strip()
                raw_report_name = str(item["company_name"] or "")
                report_name = _clean_company_candidate(item["company_name"])
                directory_name = _clean_company_candidate(item["directory_name"])
                suspect_reason = ""
                if raw_report_name.strip() and raw_report_name.strip() != report_name:
                    suspect_reason = "cleanable_report_name"
                elif not report_name:
                    suspect_reason = "missing_or_generic_report_name"
                elif report_name == code:
                    suspect_reason = "name_equals_symbol"
                elif directory_name and not _company_names_overlap(
                    report_name,
                    directory_name,
                ):
                    suspect_reason = "report_directory_mismatch"
                elif not directory_name:
                    suspect_reason = "missing_symbol_directory_name"
                if not suspect_reason:
                    continue
                identity_suspect_count += 1
                if len(identity_drift_samples) >= 8:
                    continue
                identity_drift_samples.append(
                    {
                        "report_id": int(item["report_id"] or 0),
                        "symbol": code,
                        "company_name": str(item["company_name"] or ""),
                        "directory_name": str(item["directory_name"] or ""),
                        "title": str(item["title"] or "")[:140],
                        "reason": suspect_reason,
                    }
                )
            invalid_published_at_count = 0
            invalid_published_at_samples: list[dict[str, Any]] = []
            for item in published_rows:
                published_at = str(item["published_at"] or "")
                if _normalize_report_date(published_at):
                    continue
                invalid_published_at_count += 1
                if len(invalid_published_at_samples) >= 8:
                    continue
                invalid_published_at_samples.append(
                    {
                        "report_id": int(item["report_id"] or 0),
                        "published_at": published_at,
                        "title": str(item["title"] or "")[:140],
                    }
                )

            suspicious_symbol_link_count = 0
            suspicious_symbol_link_samples: list[dict[str, Any]] = []
            for item in symbol_link_quality_rows:
                link_name = _clean_symbol_link_name(item["name"])
                directory_name = _clean_symbol_link_name(item["directory_name"])
                if (
                    not link_name
                    or not directory_name
                    or _company_names_overlap(link_name, directory_name)
                    or not _is_trusted_symbol_link_directory(
                        item["directory_source"],
                        item["directory_confidence"],
                    )
                ):
                    continue
                suspicious_symbol_link_count += 1
                if len(suspicious_symbol_link_samples) >= 8:
                    continue
                suspicious_symbol_link_samples.append(
                    {
                        "report_id": int(item["report_id"] or 0),
                        "symbol": str(item["symbol"] or ""),
                        "link_name": str(item["name"] or ""),
                        "directory_name": str(item["directory_name"] or ""),
                        "link_type": str(item["link_type"] or "mention"),
                        "source": str(item["source"] or ""),
                        "evidence": str(item["evidence"] or "")[:140],
                    }
                )
            return {
                "total_reports": int(row["total_reports"] or 0),
                "last_updated_at": str(row["last_updated_at"] or ""),
                "last_published_at": str(row["last_published_at"] or ""),
                "category_counts": category_counts,
                "total_symbols": int(symbol_row["cnt"] or 0),
                "symbol_last_updated_at": str(symbol_row["last_updated_at"] or ""),
                "symbol_link_count": int(
                    symbol_link_row["symbol_link_count"] or 0
                ),
                "etf_link_count": int(symbol_link_row["etf_link_count"] or 0),
                "linked_report_count": int(
                    symbol_link_row["linked_report_count"] or 0
                ),
                "unlinked_etf_keyword_report_count": int(
                    unlinked_etf_row["cnt"] or 0
                ),
                "last_symbol_link_updated_at": str(
                    symbol_link_row["last_symbol_link_updated_at"] or ""
                ),
                "facts": {
                    "total_facts": int(facts_row["total_facts"] or 0),
                    "target_price_count": int(facts_row["target_price_count"] or 0),
                    "rating_count": int(facts_row["rating_count"] or 0),
                },
                "quality": {
                    "missing_company_name_count": int(
                        quality_row["missing_company_name_count"] or 0
                    ),
                    "html_title_count": int(quality_row["html_title_count"] or 0),
                    "html_company_name_count": int(
                        quality_row["html_company_name_count"] or 0
                    ),
                    "missing_symbol_count": int(
                        quality_row["missing_symbol_count"] or 0
                    ),
                    "missing_broker_count": int(
                        quality_row["missing_broker_count"] or 0
                    ),
                    "missing_analyst_count": int(
                        quality_row["missing_analyst_count"] or 0
                    ),
                    "unknown_category_count": int(
                        quality_row["unknown_category_count"] or 0
                    ),
                    "symbol_directory_drift_count": int(drift_row["cnt"] or 0),
                    "identity_suspect_count": identity_suspect_count,
                    "generic_symbol_directory_count": generic_symbol_directory_count,
                    "invalid_published_at_count": invalid_published_at_count,
                    "suspicious_symbol_link_count": suspicious_symbol_link_count,
                    "identity_drift_samples": identity_drift_samples,
                    "invalid_published_at_samples": invalid_published_at_samples,
                    "suspicious_symbol_link_samples": suspicious_symbol_link_samples,
                },
                "db_path": str(self.path),
            }

    def resolve_symbol_from_text(self, text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        symbol_match = re.search(r"(?<!\d)(\d{6})(?!\d)", raw)
        with self._connect() as conn:
            if symbol_match:
                symbol = symbol_match.group(1)
                row = conn.execute(
                    """
                    SELECT symbol, company_name, market, source, confidence,
                           updated_at, last_verified_at
                    FROM symbol_directory
                    WHERE symbol = ?
                    """,
                    (symbol,),
                ).fetchone()
                if row:
                    return {
                        "symbol": str(row["symbol"] or ""),
                        "company_name": str(row["company_name"] or ""),
                        "market": str(row["market"] or ""),
                        "source": str(row["source"] or ""),
                        "confidence": float(row["confidence"] or 0.0),
                        "updated_at": str(row["updated_at"] or ""),
                        "last_verified_at": str(row["last_verified_at"] or ""),
                        "match_type": "symbol",
                    }
            rows = conn.execute(
                """
                SELECT symbol, company_name, market, source, confidence,
                       updated_at, last_verified_at
                FROM symbol_directory
                WHERE status = 'active'
                  AND company_name != ''
                ORDER BY LENGTH(company_name) DESC, confidence DESC, symbol ASC
                """
            ).fetchall()
        normalized = re.sub(r"\s+", "", raw).lower()
        for row in rows:
            name = str(row["company_name"] or "").strip()
            if len(name) < 2:
                continue
            name_key = re.sub(r"\s+", "", name).lower()
            if name_key and name_key in normalized:
                return {
                    "symbol": str(row["symbol"] or ""),
                    "company_name": name,
                    "market": str(row["market"] or ""),
                    "source": str(row["source"] or ""),
                    "confidence": float(row["confidence"] or 0.0),
                    "updated_at": str(row["updated_at"] or ""),
                    "last_verified_at": str(row["last_verified_at"] or ""),
                    "match_type": "company_name",
                }
        return None

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
    ) -> list[dict[str, Any]]:
        q = str(query or "").strip()
        sym = str(symbol or "").strip()
        cat = str(category or "").strip()
        broker_text = str(broker or "").strip()
        analyst_text = str(analyst or "").strip()
        published_from = str(date_from or "").strip()
        published_to = str(date_to or "").strip()
        max_rows = max(min(int(limit), 100), 1)
        prefix_sql = ""
        prefix_params: list[Any] = []
        where: list[str] = []
        params: list[Any] = []
        if sym:
            prefix_sql = """
                WITH candidate_reports AS (
                  SELECT report_id FROM reports WHERE symbol = ?
                  UNION
                  SELECT report_id FROM report_symbol_links WHERE symbol = ?
                )
            """
            prefix_params.extend([sym, sym])
        if cat:
            where.append("r.category = ?")
            params.append(cat)
        if broker_text:
            where.append("r.broker LIKE ?")
            params.append(f"%{broker_text}%")
        if analyst_text:
            where.append("r.analyst LIKE ?")
            params.append(f"%{analyst_text}%")
        if published_from:
            where.append("r.published_at >= ?")
            params.append(published_from)
        if published_to:
            where.append("r.published_at <= ?")
            params.append(published_to)
        if q:
            like = f"%{q}%"
            where.append("(r.title LIKE ? OR r.content LIKE ? OR c.content LIKE ?)")
            params.extend([like, like, like])

        if not q:
            sql = (
                prefix_sql
                + """
                SELECT
                  r.report_id,
                  r.doc_id,
                  r.category,
                  r.title,
                  r.company_name,
                  r.broker,
                  r.analyst,
                  r.symbol,
                  r.published_at,
                  r.crawled_at,
                  r.pdf_sha256,
                  r.pdf_url,
                  r.pdf_archived_path,
                  r.content_source,
                  r.detail_url,
                  r.updated_at,
                  SUBSTR(r.content, 1, 480) AS snippet
                FROM """
            )
            if sym:
                sql += """
                candidate_reports candidate
                JOIN reports r ON r.report_id = candidate.report_id
                """
            else:
                sql += "reports r"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY r.published_at DESC, r.updated_at DESC LIMIT ?"
            params = [*prefix_params, *params, max_rows]

            with self._connect_readonly() as conn:
                rows = conn.execute(sql, params).fetchall()
                return [self._format_search_row(row) for row in rows]

        snippet_sql = """
              COALESCE(
                (
                  SELECT c2.content
                  FROM report_chunks c2
                  WHERE c2.report_id = r.report_id
                    AND c2.content LIKE ?
                  ORDER BY c2.chunk_index ASC
                  LIMIT 1
                ),
                CASE
                  WHEN INSTR(r.content, ?) > 0 THEN
                    SUBSTR(
                      r.content,
                      CASE
                        WHEN INSTR(r.content, ?) > 80 THEN INSTR(r.content, ?) - 80
                        ELSE 1
                      END,
                      480
                    )
                  ELSE NULL
                END,
                MAX(c.content),
                r.content
              ) AS snippet
        """
        snippet_params = [like, q, q, q]

        sql = (
            prefix_sql
            + """
            SELECT
              r.report_id,
              r.doc_id,
              r.category,
              r.title,
              r.company_name,
              r.broker,
              r.analyst,
              r.symbol,
              r.published_at,
              r.crawled_at,
              r.pdf_sha256,
              r.pdf_url,
              r.pdf_archived_path,
              r.content_source,
              r.detail_url,
              r.updated_at,
        """
            + snippet_sql
            + """
            FROM """
        )
        if sym:
            sql += """
            candidate_reports candidate
            JOIN reports r ON r.report_id = candidate.report_id
            """
        else:
            sql += "reports r"
        sql += """
            LEFT JOIN report_chunks c ON c.report_id = r.report_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY r.report_id ORDER BY r.published_at DESC, r.updated_at DESC LIMIT ?"
        params = [*prefix_params, *snippet_params, *params, max_rows]

        with self._connect_readonly() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._format_search_row(row) for row in rows]

    @staticmethod
    def _format_search_row(row: sqlite3.Row) -> dict[str, Any]:
        snippet = _normalize_search_snippet(
            row["snippet"],
            company_name=row["company_name"],
            symbol=row["symbol"],
        )
        if len(snippet) > 480:
            snippet = snippet[:480]
        return {
            "report_id": int(row["report_id"]),
            "doc_id": str(row["doc_id"] or ""),
            "category": str(row["category"] or "unknown"),
            "title": str(row["title"] or ""),
            "company_name": str(row["company_name"] or ""),
            "broker": str(row["broker"] or ""),
            "analyst": str(row["analyst"] or ""),
            "symbol": str(row["symbol"] or ""),
            "published_at": str(row["published_at"] or ""),
            "crawled_at": str(row["crawled_at"] or ""),
            "pdf_sha256": str(row["pdf_sha256"] or ""),
            "pdf_url": str(row["pdf_url"] or ""),
            "pdf_archived_path": str(row["pdf_archived_path"] or ""),
            "content_source": str(row["content_source"] or ""),
            "detail_url": str(row["detail_url"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "snippet": snippet,
        }

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        rid = int(report_id)
        if rid <= 0:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  report_id,
                  doc_id,
                  category,
                  source_url,
                  detail_url,
                  pdf_url,
                  title,
                  company_name,
                  broker,
                  analyst,
                  symbol,
                  published_at,
                  crawled_at,
                  pdf_sha256,
                  pdf_archived_path,
                  content_source,
                  content,
                  created_at,
                  updated_at
                FROM reports
                WHERE report_id = ?
                """,
                (rid,),
            ).fetchone()
            if row is None:
                return None
            return {
                "report_id": int(row["report_id"]),
                "doc_id": str(row["doc_id"] or ""),
                "category": str(row["category"] or "unknown"),
                "source_url": str(row["source_url"] or ""),
                "detail_url": str(row["detail_url"] or ""),
                "pdf_url": str(row["pdf_url"] or ""),
                "title": str(row["title"] or ""),
                "company_name": str(row["company_name"] or ""),
                "broker": str(row["broker"] or ""),
                "analyst": str(row["analyst"] or ""),
                "symbol": str(row["symbol"] or ""),
                "published_at": str(row["published_at"] or ""),
                "crawled_at": str(row["crawled_at"] or ""),
                "pdf_sha256": str(row["pdf_sha256"] or ""),
                "pdf_archived_path": str(row["pdf_archived_path"] or ""),
                "content_source": str(row["content_source"] or ""),
                "content": str(row["content"] or ""),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }

    def list_report_chunks(
        self,
        report_id: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        rid = int(report_id)
        if rid <= 0:
            return []
        max_rows = max(min(int(limit), 5000), 1)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  chunk_id,
                  report_id,
                  chunk_index,
                  page_start,
                  page_end,
                  section_title,
                  content,
                  created_at
                FROM report_chunks
                WHERE report_id = ?
                ORDER BY chunk_index ASC
                LIMIT ?
                """,
                (rid, max_rows),
            ).fetchall()
            return [
                {
                    "chunk_id": int(row["chunk_id"]),
                    "report_id": int(row["report_id"]),
                    "chunk_index": int(row["chunk_index"]),
                    "page_start": int(row["page_start"] or 0),
                    "page_end": int(row["page_end"] or 0),
                    "section_title": str(row["section_title"] or "unknown"),
                    "content": str(row["content"] or ""),
                    "created_at": str(row["created_at"] or ""),
                }
                for row in rows
            ]

    def list_recent_report_facts(
        self,
        lookback_days: int = 90,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        days = max(int(lookback_days), 1)
        max_rows = max(min(int(limit), 20000), 1)
        since = (date.today() - timedelta(days=days)).isoformat()
        sql = """
            SELECT
              r.report_id,
              r.symbol,
              r.company_name,
              r.title,
              r.broker,
              r.category,
              r.published_at,
              f.rating,
              f.target_price_value,
              f.target_price_currency,
              f.target_price_changed,
              f.catalysts_json,
              f.risks_json,
              f.investment_thesis_json,
              f.evidence_quotes_json
            FROM reports r
            LEFT JOIN report_facts f ON f.report_id = r.report_id
            WHERE r.symbol <> '' AND r.published_at <> '' AND r.published_at >= ?
            ORDER BY r.published_at DESC, r.updated_at DESC, r.report_id DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (since, max_rows)).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "report_id": int(row["report_id"] or 0),
                        "symbol": str(row["symbol"] or ""),
                        "company_name": str(row["company_name"] or ""),
                        "title": str(row["title"] or ""),
                        "broker": str(row["broker"] or ""),
                        "category": str(row["category"] or "unknown"),
                        "published_at": str(row["published_at"] or ""),
                        "rating": str(row["rating"] or "UNKNOWN"),
                        "target_price_value": int(row["target_price_value"] or 0),
                        "target_price_currency": str(
                            row["target_price_currency"] or "KRW"
                        ),
                        "target_price_changed": str(
                            row["target_price_changed"] or "UNKNOWN"
                        ),
                        "catalysts": self._parse_json_array(row["catalysts_json"]),
                        "risks": self._parse_json_array(row["risks_json"]),
                        "investment_thesis": self._parse_json_array(
                            row["investment_thesis_json"]
                        ),
                        "evidence_quotes": self._parse_json_array(
                            row["evidence_quotes_json"]
                        ),
                    }
                )
            return out

    def list_chunks_for_rag(
        self,
        limit: int = 50000,
        *,
        updated_since: str | None = None,
    ) -> list[dict[str, Any]]:
        max_rows = max(min(int(limit), 200000), 1)
        filters: list[str] = []
        params: list[Any] = []
        clean_updated_since = str(updated_since or "").strip()
        if clean_updated_since:
            filters.append("r.updated_at >= ?")
            params.append(clean_updated_since)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        sql = """
            SELECT
              r.report_id,
              r.doc_id,
              r.category,
              r.title,
              r.company_name,
              r.broker,
              r.analyst,
              r.symbol,
              r.published_at,
              r.crawled_at,
              r.pdf_sha256,
              r.pdf_url,
              r.pdf_archived_path,
              r.content_source,
              r.detail_url,
              r.updated_at,
              c.chunk_index,
              c.page_start,
              c.page_end,
              c.section_title,
              c.content,
              COALESCE(linked.linked_symbols, '') AS linked_symbols,
              COALESCE(linked.linked_names, '') AS linked_names,
              COALESCE(linked.linked_asset_classes, '') AS linked_asset_classes
            FROM reports r
            JOIN report_chunks c ON c.report_id = r.report_id
            LEFT JOIN (
              SELECT
                report_id,
                GROUP_CONCAT(symbol, ',') AS linked_symbols,
                GROUP_CONCAT(name, ',') AS linked_names,
                GROUP_CONCAT(asset_class, ',') AS linked_asset_classes
              FROM (
                SELECT
                  report_id,
                  symbol,
                  MAX(name) AS name,
                  MAX(asset_class) AS asset_class,
                  MAX(confidence) AS confidence
                FROM report_symbol_links
                GROUP BY report_id, symbol
                ORDER BY report_id ASC, confidence DESC, symbol ASC
              )
              GROUP BY report_id
            ) linked ON linked.report_id = r.report_id
            {where_sql}
            ORDER BY r.published_at DESC, r.updated_at DESC, c.chunk_index ASC
            LIMIT ?
        """.format(where_sql=where_sql)
        params.append(max_rows)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "report_id": int(row["report_id"]),
                        "doc_id": str(row["doc_id"] or ""),
                        "category": str(row["category"] or "unknown"),
                        "title": str(row["title"] or ""),
                        "company_name": str(row["company_name"] or ""),
                        "broker": str(row["broker"] or ""),
                        "analyst": str(row["analyst"] or ""),
                        "symbol": str(row["symbol"] or ""),
                        "published_at": str(row["published_at"] or ""),
                        "crawled_at": str(row["crawled_at"] or ""),
                        "pdf_sha256": str(row["pdf_sha256"] or ""),
                        "pdf_url": str(row["pdf_url"] or ""),
                        "pdf_archived_path": str(row["pdf_archived_path"] or ""),
                        "content_source": str(row["content_source"] or ""),
                        "detail_url": str(row["detail_url"] or ""),
                        "updated_at": str(row["updated_at"] or ""),
                        "chunk_index": int(row["chunk_index"]),
                        "page_start": int(row["page_start"] or 0),
                        "page_end": int(row["page_end"] or 0),
                        "section_title": str(row["section_title"] or "unknown"),
                        "content": str(row["content"] or ""),
                        "linked_symbols": str(row["linked_symbols"] or ""),
                        "linked_names": str(row["linked_names"] or ""),
                        "linked_asset_classes": str(
                            row["linked_asset_classes"] or ""
                        ),
                    }
                )
            return out

    def list_report_sources(self, limit: int = 0) -> list[dict[str, str]]:
        max_rows = max(int(limit), 0)
        sql = """
            SELECT
              doc_id,
              source_url,
              detail_url,
              pdf_url,
              category,
              title,
              company_name,
              broker,
              analyst,
              symbol,
              published_at,
              crawled_at
            FROM reports
            ORDER BY report_id ASC
        """
        params: tuple[Any, ...] = ()
        if max_rows > 0:
            sql += " LIMIT ?"
            params = (max_rows,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "source_url": str(row["source_url"] or ""),
                    "detail_url": str(row["detail_url"] or ""),
                    "pdf_url": str(row["pdf_url"] or ""),
                    "doc_id": str(row["doc_id"] or ""),
                    "category": str(row["category"] or "unknown"),
                    "title": str(row["title"] or ""),
                    "company_name": str(row["company_name"] or ""),
                    "broker": str(row["broker"] or ""),
                    "analyst": str(row["analyst"] or ""),
                    "symbol": str(row["symbol"] or ""),
                    "published_at": str(row["published_at"] or ""),
                    "crawled_at": str(row["crawled_at"] or ""),
                }
                for row in rows
            ]


class NaverSecuritiesCrawler:
    def __init__(
        self, config: NaverReportCrawlerConfig, repository: NaverReportRepository
    ) -> None:
        self.config = config
        self.repository = repository
        self._since_date = self._parse_since_date(config.since_date)
        self._archive_dir = Path(self.config.pdf_archive_dir)
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._codex_runtime = CodexNativeRuntime(
            CodexNativeConfig(
                mode=config.codex_runtime_mode,
                sdk_codex_bin=config.codex_runtime_sdk_codex_bin,
                timeout_ms=config.codex_runtime_timeout_ms,
                model=config.llm_model,
                reasoning_effort=config.llm_reasoning_effort,
                usage_enabled=config.llm_usage_enabled,
                usage_db_path=config.llm_usage_db_path,
                usage_component=config.llm_usage_component,
                thread_mode=config.codex_native_thread_mode,
                thread_db_path=config.codex_native_thread_db_path,
                compact_after_turns=config.codex_native_compact_after_turns,
                read_turns=config.codex_native_read_turns,
                developer_instructions_enabled=(
                    config.codex_native_developer_instructions_enabled
                ),
            )
        )

    async def crawl_once(self) -> dict[str, Any]:
        discovered = 0
        inserted = 0
        skipped = 0
        errors = 0
        pdf_seen: set[str] = set()

        def snapshot(*, cycle_limited: bool = False) -> dict[str, Any]:
            payload = {
                "status": "ok",
                "updated_at": utc_now_iso(),
                "discovered": discovered,
                "inserted": inserted,
                "skipped": skipped,
                "errors": errors,
                "repository": self.repository.status(),
            }
            if cycle_limited:
                payload["cycle_limited"] = True
                payload["max_pdfs_per_cycle"] = int(self.config.max_pdfs_per_cycle)
            return payload

        timeout = httpx.Timeout(max(self.config.timeout_sec, 3.0))
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/pdf,*/*",
        }
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            for seed in self._seed_urls():
                detail_pages_seen: set[str] = set()
                for page in range(1, max(int(self.config.max_pages), 1) + 1):
                    page_url = self._page_url(seed, page)
                    html = await self._fetch_text(client, page_url)
                    if not html:
                        continue

                    links = _extract_links(html, page_url)
                    for link_url, label in links:
                        lower = link_url.lower()
                        if lower.endswith(".pdf"):
                            discovered += 1
                            if link_url in pdf_seen:
                                skipped += 1
                                continue
                            pdf_seen.add(link_url)
                            if self.repository.has_pdf_url(link_url):
                                skipped += 1
                                continue
                            category = _infer_report_category(page_url, page_url)
                            ok = await self._ingest_pdf(
                                client=client,
                                category=category,
                                source_url=page_url,
                                detail_url=page_url,
                                pdf_url=link_url,
                                title=label or self._title_from_url(link_url),
                                broker="",
                                symbol=_parse_symbol(label),
                                published_at=_parse_date(label),
                                detail_html=html,
                            )
                            if ok:
                                inserted += 1
                            else:
                                errors += 1
                            if discovered >= max(int(self.config.max_pdfs_per_cycle), 1):
                                return snapshot(cycle_limited=True)
                            continue

                        if not _is_research_detail_url(link_url):
                            continue
                        if link_url in detail_pages_seen:
                            continue
                        detail_pages_seen.add(link_url)
                        if self.repository.has_detail_url(link_url):
                            skipped += 1
                            continue
                        if len(detail_pages_seen) > max(
                            self.config.max_detail_pages, 1
                        ):
                            break

                        detail_html = await self._fetch_text(client, link_url)
                        if not detail_html:
                            continue
                        detail_links = _extract_links(detail_html, link_url)
                        detail_title = (
                            self._extract_title(detail_html)
                            or label
                            or self._title_from_url(link_url)
                        )
                        detail_date = _parse_date(detail_html) or _parse_date(label)
                        detail_symbol = (
                            self._symbol_from_query(link_url)
                            or self._symbol_from_links(detail_links)
                            or _parse_symbol(detail_title)
                            or _parse_symbol(label)
                        )
                        detail_broker = self._extract_broker(detail_html)

                        for pdf_url, pdf_label in detail_links:
                            if not pdf_url.lower().endswith(".pdf"):
                                continue
                            discovered += 1
                            if pdf_url in pdf_seen:
                                skipped += 1
                                continue
                            pdf_seen.add(pdf_url)
                            if self.repository.has_pdf_url(pdf_url):
                                skipped += 1
                                continue
                            category = _infer_report_category(page_url, link_url)
                            ok = await self._ingest_pdf(
                                client=client,
                                category=category,
                                source_url=page_url,
                                detail_url=link_url,
                                pdf_url=pdf_url,
                                title=pdf_label or detail_title,
                                broker=detail_broker,
                                symbol=detail_symbol,
                                published_at=detail_date,
                                detail_html=detail_html,
                            )
                            if ok:
                                inserted += 1
                            else:
                                errors += 1
                            if discovered >= max(int(self.config.max_pdfs_per_cycle), 1):
                                return snapshot(cycle_limited=True)

        return snapshot()

    async def rebuild_all_from_pdf(self, limit: int = 0) -> dict[str, Any]:
        targets = self.repository.list_report_sources(limit=limit)
        updated = 0
        skipped = 0
        errors = 0
        timeout = httpx.Timeout(max(self.config.timeout_sec, 3.0))
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/pdf,*/*",
        }
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            for item in targets:
                detail_url = str(item.get("detail_url") or "")
                detail_html = await self._fetch_text(client, detail_url) if detail_url else ""
                detail_links = _extract_links(detail_html, detail_url) if detail_html else []
                detail_title = self._extract_title(detail_html) if detail_html else ""
                detail_date = _parse_date(detail_html) if detail_html else ""
                detail_symbol = (
                    self._symbol_from_query(detail_url)
                    or self._symbol_from_links(detail_links)
                    or _parse_symbol(detail_title)
                )
                detail_broker = self._extract_broker(detail_html) if detail_html else ""
                ok = await self._ingest_pdf(
                    client=client,
                    category=str(item.get("category") or "unknown"),
                    source_url=str(item.get("source_url") or ""),
                    detail_url=detail_url,
                    pdf_url=str(item.get("pdf_url") or ""),
                    title=detail_title or str(item.get("title") or ""),
                    broker=detail_broker or str(item.get("broker") or ""),
                    symbol=detail_symbol or str(item.get("symbol") or ""),
                    published_at=detail_date or str(item.get("published_at") or ""),
                    detail_html=detail_html,
                    force=True,
                )
                if ok:
                    updated += 1
                else:
                    errors += 1
        return {
            "status": "ok",
            "updated_at": utc_now_iso(),
            "target_count": len(targets),
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "repository": self.repository.status(),
        }

    async def _ingest_pdf(
        self,
        client: httpx.AsyncClient,
        category: str,
        source_url: str,
        detail_url: str,
        pdf_url: str,
        title: str,
        broker: str,
        symbol: str,
        published_at: str,
        detail_html: str,
        force: bool = False,
    ) -> bool:
        if not force and self._since_date is not None and published_at:
            try:
                pub = date.fromisoformat(published_at)
            except ValueError:
                pub = None
            if pub is not None and pub < self._since_date:
                return True

        binary = await self._fetch_bytes(client, pdf_url)
        if not binary:
            return False

        pdf_sha256, archived_path = self._archive_pdf(pdf_url=pdf_url, payload=binary)
        if not archived_path:
            return False

        raw_pages = await self._extract_pdf_pages_with_timeout(binary)
        cleaned_pages = _remove_repeated_header_footer(raw_pages)
        page_rows: list[dict[str, Any]] = []
        for idx, page_text in enumerate(cleaned_pages, start=1):
            compact = re.sub(r"\s+", " ", str(page_text or "")).strip()
            if not compact:
                continue
            page_rows.append(
                {
                    "page_number": idx,
                    "section_title": _detect_section_title(compact),
                    "content": compact,
                }
            )

        text = "\n".join(str(row.get("content") or "") for row in page_rows).strip()
        if len(text) < max(int(self.config.min_pdf_text_chars), 1):
            return False
        if len(text) > self.config.max_pdf_chars:
            text = text[: self.config.max_pdf_chars]
        text_tail = text[-12000:] if len(text) > 12000 else ""
        metadata_probe = ""
        needs_metadata_probe = _looks_like_garbled_pdf_text(text)
        if not needs_metadata_probe:
            quick_analyst_text = f"{detail_html}\n{text[:12000]}\n{text_tail}"
            likely_mirae = (
                _clean_broker_name(broker) == "미래에셋증권"
                or "miraeasset.com" in detail_html.lower()
                or "mirae asset" in text[:2000].lower()
            )
            needs_metadata_probe = (
                likely_mirae and not _extract_analyst_from_text(quick_analyst_text)
            )
        if needs_metadata_probe:
            metadata_probe = self._extract_pdf_metadata_ocr_text(binary)

        chunk_rows = _build_chunk_rows(
            page_rows,
            chunk_size=self.config.chunk_size,
            max_chunks=self.config.max_chunks_per_report,
        )

        normalized_title = _clean_report_title(
            title or self._title_from_url(pdf_url),
            content=text,
            category=category,
        )
        normalized_symbol = _clean_report_symbol(symbol, published_at=published_at)
        if not normalized_symbol:
            normalized_symbol = _clean_report_symbol(
                _parse_symbol(normalized_title),
                published_at=published_at,
            )
        normalized_date = published_at.strip()
        if not normalized_date:
            normalized_date = _parse_date(detail_html)
        if not normalized_date:
            normalized_date = utc_now_iso()[:10]
        known_symbol_names = (
            self.repository.resolve_symbol_names([normalized_symbol])
            if normalized_symbol
            else {}
        )
        identity_text = (
            f"{normalized_title}\n{_to_text(detail_html)}\n"
            f"{metadata_probe}\n{text[:12000]}"
        )
        inferred_symbol, inferred_company = _extract_company_symbol_from_text(
            identity_text,
            symbol_names=known_symbol_names,
            published_at=normalized_date,
        )
        if inferred_symbol and (
            not normalized_symbol
            or (normalized_symbol not in known_symbol_names and inferred_company)
        ):
            normalized_symbol = inferred_symbol
            known_symbol_names = self.repository.resolve_symbol_names([normalized_symbol])
        normalized_company_name = self._extract_company_name(
            detail_html,
            normalized_title,
            content=text,
            symbol_names=known_symbol_names,
            published_at=normalized_date,
        )
        if not normalized_company_name and normalized_symbol:
            normalized_company_name = self.repository.get_symbol_name(normalized_symbol)
        if not normalized_company_name and inferred_company:
            normalized_company_name = inferred_company
        if category != "company_analysis":
            normalized_symbol = ""
            normalized_company_name = ""
        broker_text = f"{detail_html}\n{metadata_probe}\n{text[:8000]}"
        normalized_broker = (
            _extract_strong_broker_from_text(broker_text)
            or _clean_broker_name(broker)
            or _extract_broker_from_text(broker_text)
        )
        analyst_text = f"{detail_html}\n{metadata_probe}\n{text[:12000]}\n{text_tail}"
        normalized_analyst = self._extract_analyst(analyst_text)
        if not normalized_analyst:
            normalized_analyst = _extract_department_author_from_text(
                analyst_text,
                broker=normalized_broker,
            )
        if not normalized_analyst:
            normalized_analyst = _infer_default_department_author_from_context(
                broker=normalized_broker,
                category=category,
                text=analyst_text,
            )
        normalized_crawled_at = utc_now_iso()

        structured_facts = _extract_basic_structured(
            text,
            page_rows,
            title=normalized_title,
            broker=normalized_broker,
            symbol=normalized_symbol,
        )
        structured_facts = await self._refine_structured_facts_via_native(
            facts=structured_facts,
            text=text,
        )

        self.repository.upsert_report(
            category=category,
            source_url=source_url,
            detail_url=detail_url,
            pdf_url=pdf_url,
            pdf_sha256=pdf_sha256,
            pdf_archived_path=archived_path,
            title=normalized_title,
            company_name=normalized_company_name,
            broker=normalized_broker,
            analyst=normalized_analyst,
            symbol=normalized_symbol,
            published_at=normalized_date,
            crawled_at=normalized_crawled_at,
            content_source="pdf_extract",
            content=text,
            chunk_size=self.config.chunk_size,
            max_chunks_per_report=self.config.max_chunks_per_report,
            chunks=chunk_rows,
            structured_facts=structured_facts,
        )
        return True

    def _archive_pdf(self, pdf_url: str, payload: bytes) -> tuple[str, str]:
        if not payload:
            return "", ""
        sha256 = hashlib.sha256(payload).hexdigest()
        suffix = ".pdf"
        path = urlparse(pdf_url).path.strip()
        if path.lower().endswith(".pdf"):
            suffix = Path(path).suffix or ".pdf"
        subdir = self._archive_dir / sha256[:2] / sha256[2:4]
        subdir.mkdir(parents=True, exist_ok=True)
        archive_path = subdir / f"{sha256}{suffix}"
        if not archive_path.exists():
            archive_path.write_bytes(payload)
        return sha256, str(archive_path)

    async def _fetch_text(self, client: httpx.AsyncClient, url: str) -> str:
        await asyncio.sleep(max(float(self.config.request_delay_sec), 0.0))
        try:
            res = await client.get(url)
            if res.status_code >= 400:
                return ""
            return res.text
        except Exception:
            return ""

    async def _fetch_bytes(self, client: httpx.AsyncClient, url: str) -> bytes:
        await asyncio.sleep(max(float(self.config.request_delay_sec), 0.0))
        try:
            res = await client.get(url)
            if res.status_code >= 400:
                return b""
            return bytes(res.content)
        except Exception:
            return b""

    def _extract_pdf_metadata_ocr_text(self, payload: bytes) -> str:
        magick = shutil.which("magick")
        tesseract = shutil.which("tesseract")
        if not magick or not tesseract or not payload:
            return ""

        temp_parent = self._archive_dir.parent / "ocr_tmp"
        try:
            temp_parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return ""

        try:
            with tempfile.TemporaryDirectory(dir=str(temp_parent)) as temp_dir:
                temp_path = Path(temp_dir)
                pdf_path = temp_path / "source.pdf"
                image_path = temp_path / "page.png"
                pdf_path.write_bytes(payload)
                render = subprocess.run(
                    [
                        magick,
                        "-density",
                        "200",
                        f"{pdf_path}[0]",
                        "-alpha",
                        "remove",
                        "-background",
                        "white",
                        "-colorspace",
                        "Gray",
                        "-depth",
                        "8",
                        str(image_path),
                    ],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
                if render.returncode != 0 or not image_path.exists():
                    return ""
                ocr = subprocess.run(
                    [tesseract, str(image_path.resolve()), "stdout", "-l", "eng", "--psm", "6"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if ocr.returncode != 0:
                    return ""
                return _clean_metadata_text(ocr.stdout, limit=12000)
        except Exception:
            return ""

    def _extract_pdf_pages(self, payload: bytes) -> list[str]:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            try:
                from PyPDF2 import PdfReader  # type: ignore
            except Exception:
                return []

        try:
            reader = PdfReader(io.BytesIO(payload))
        except Exception:
            return []

        texts: list[str] = []
        for page in reader.pages:
            try:
                item = page.extract_text() or ""
            except Exception:
                item = ""
            if item:
                texts.append(item)
            if sum(len(row) for row in texts) >= self.config.max_pdf_chars:
                break
        return texts

    async def _extract_pdf_pages_with_timeout(self, payload: bytes) -> list[str]:
        timeout_sec = max(float(self.config.timeout_sec) * 2.0, 30.0)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._extract_pdf_pages, payload),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "naver report pdf text extraction timeout after %.1fs",
                timeout_sec,
            )
            return []
        except Exception as exc:
            logger.warning("naver report pdf text extraction failed: %s", exc)
            return []

    async def _refine_structured_facts_via_native(
        self,
        *,
        facts: dict[str, Any],
        text: str,
    ) -> dict[str, Any]:
        if not self.config.llm_facts_enabled:
            return facts
        runtime = self._codex_runtime
        if not runtime.ready:
            return facts

        payload = {
            "model": runtime.resolved_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "native_thread_mode": "ephemeral",
            "telemetry": {
                "component": self.config.llm_usage_component,
                "operation": "report_fact_extraction",
            },
            "jue_workflow": {"workflow_id": "report_fact_extraction"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only one JSON object matching output_schema.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Extract structured analyst-report fields with short evidence quotes.",
                            "output_schema": {
                                "rating": "BUY|HOLD|SELL|UNKNOWN",
                                "target_price": {
                                    "value": "integer KRW",
                                    "currency": "KRW",
                                    "changed": "UP|DOWN|UNCHANGED|UNKNOWN",
                                },
                                "summary_bullets": ["string"],
                                "investment_thesis": ["string"],
                                "risks": ["string"],
                                "earnings_outlook": ["object"],
                                "valuation": {
                                    "method": "PER|PBR|EV/EBITDA|DCF|UNKNOWN",
                                    "value": "number|null",
                                    "basis": "string",
                                    "notes": "string",
                                },
                                "catalysts": ["string"],
                                "evidence_quotes": [
                                    {
                                        "page": "integer",
                                        "tag": "string",
                                        "text": "string",
                                    }
                                ],
                            },
                            "base_facts": facts,
                            "report_excerpt": text[:9000],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }

        async def _request_once() -> dict[str, Any] | None:
            result = await runtime.complete(payload)
            if not bool(result.get("ok")):
                return None
            out_text = str(result.get("content") or "").strip()
            if not out_text:
                return None
            try:
                parsed = json.loads(out_text)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None

        parsed_once = await _request_once()
        merged = dict(facts)
        if isinstance(parsed_once, dict):
            merged.update(parsed_once)

        target = merged.get("target_price")
        target_value = 0
        if isinstance(target, dict):
            target_value = _safe_non_negative_int(target.get("value"))
        if target_value > 0:
            return merged

        parsed_retry = await _request_once()
        if isinstance(parsed_retry, dict):
            merged.update(parsed_retry)
        return merged

    @staticmethod
    def _extract_company_name(
        detail_html: str,
        fallback_title: str,
        *,
        content: str = "",
        symbol_names: dict[str, str] | None = None,
        published_at: str = "",
    ) -> str:
        text = f"{_to_text(detail_html)}\n{fallback_title}\n{str(content or '')[:12000]}"
        _, company = _extract_company_symbol_from_text(
            text,
            symbol_names=symbol_names,
            published_at=published_at,
        )
        return company

    @staticmethod
    def _extract_analyst(detail_html: str) -> str:
        return _extract_analyst_from_text(detail_html)

    def _page_url(self, seed_url: str, page: int) -> str:
        base = seed_url.strip()
        parsed = urlparse(base)
        if not parsed.scheme:
            return base
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["page"] = [str(page)]
        parts = []
        for key, values in query.items():
            for value in values:
                parts.append(f"{key}={value}")
        query_text = "&".join(parts)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                query_text,
                parsed.fragment,
            )
        )

    def _seed_urls(self) -> list[str]:
        values = [
            item.strip()
            for item in list(self.config.seed_urls or [])
            if item and item.strip()
        ]
        if values:
            return _prioritize_research_seed_urls(values)
        single = self.config.seed_url.strip()
        return _prioritize_research_seed_urls([single]) if single else []

    @staticmethod
    def _extract_title(html: str) -> str:
        match = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, flags=re.IGNORECASE)
        if not match:
            return ""
        return _to_text(match.group(1) or "")

    @staticmethod
    def _extract_broker(html: str) -> str:
        return _extract_broker_from_text(html)

    @staticmethod
    def _symbol_from_query(url: str) -> str:
        query = parse_qs(urlparse(url).query)
        for key in ("code", "symbol", "item_code"):
            val = str((query.get(key) or [""])[0]).strip()
            if len(val) == 6 and val.isdigit():
                return val
        return ""

    @staticmethod
    def _symbol_from_links(links: list[tuple[str, str]]) -> str:
        for link_url, link_label in links:
            query = parse_qs(urlparse(str(link_url or "")).query)
            from_query = ""
            for key in ("code", "symbol", "item_code"):
                val = str((query.get(key) or [""])[0]).strip()
                if len(val) == 6 and val.isdigit():
                    from_query = val
                    break
            if from_query:
                return from_query
            from_label = _parse_symbol(str(link_label or ""))
            if from_label:
                return from_label
        return ""

    @staticmethod
    def _title_from_url(url: str) -> str:
        path = urlparse(url).path
        if not path:
            return "report"
        name = Path(path).name.strip()
        if not name:
            return "report"
        if name.lower().endswith(".pdf"):
            return name[:-4]
        return name

    @staticmethod
    def _parse_since_date(text: str) -> date | None:
        raw = text.strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
