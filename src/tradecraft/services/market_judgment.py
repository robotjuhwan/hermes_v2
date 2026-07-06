from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import inspect
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

import httpx

from tradecraft.services.db_retention import gzip_base64_archive_text
from tradecraft.services.jue_language_policy import jue_language_policy
from tradecraft.services.jue_wiki import normalize_jue_wiki_quality_status
from tradecraft.services.kis import KISAdapter
from tradecraft.services.krx_holiday import KRXHolidayCalendar
from tradecraft.services.codex_native import CodexNativeRuntime
from tradecraft.services.jue_wiki_application import (
    build_jue_wiki_quality_pressure_action_plan_for_prompt,
    summarize_jue_wiki_quality_pressure_for_prompt,
)
from tradecraft.services.jue_wiki_prompt_quality import (
    canonical_jue_wiki_evidence_quality,
    jue_wiki_quality_status_from_evidence,
)
from tradecraft.services.jue_wiki_selector import (
    build_jue_wiki_decision_adjustment_audit_contract_for_prompt,
    build_jue_wiki_decision_adjustments_for_prompt,
    build_jue_wiki_repair_contract_for_prompt,
    build_jue_wiki_trust_profile_for_prompt,
    build_jue_wiki_validation_repair_contract_for_prompt,
    compact_jue_wiki_application_coverage_for_prompt,
    compact_jue_wiki_repair_loop_effectiveness_for_prompt,
    compact_jue_wiki_validation_repair_effectiveness_for_prompt,
)
from tradecraft.services.manager_prompt_budget import attach_jue_wiki_budget_report

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
JUE_WIKI_BUDGET_REPORT_MAX_CHARS = 48_000

ACCOUNT_ACTIONS = {
    "hold",
    "watch_add",
    "avoid_add",
    "trim_watch",
    "risk_check",
    "new_watch",
}
STANCES = {"watch", "confirm", "hold", "risk_check", "avoid", "stale"}
HORIZONS = {"intraday", "short_term", "mid_term", "long_term", "unknown"}
_DISPLAY_NAME_ALLOWLIST = {"CJ", "DB", "HL", "JYP", "KT", "LG", "LS", "PI", "SBS", "SK"}
_DISPLAY_NAME_DENYLIST = {
    "buy",
    "hold",
    "initiation",
    "preview",
    "revi",
    "review",
    "su",
    "suek",
    "목",
    "예상",
    "원전",
    "유",
    "하락의",
}


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

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]: ...


class StrategyEngine(Protocol):
    def build_candidates(
        self,
        *,
        query: str,
        research_feed: dict[str, Any] | None,
        limit: int | None = None,
    ) -> dict[str, Any]: ...

    def list_external_signals(
        self,
        source_id: str = "",
        symbol: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> dict[str, Any]: ...


class FundamentalsRepository(Protocol):
    def latest(self, symbol: str) -> dict[str, Any] | None: ...


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


ResearchFeedProvider = Callable[[], dict[str, Any] | None]
MarketPulseProvider = Callable[..., dict[str, Any] | None]
MemoryContextProvider = Callable[..., dict[str, Any] | None]
OpportunityProvider = Callable[..., dict[str, Any] | None]


@dataclass(slots=True)
class MarketJudgmentConfig:
    db_path: str = ".runtime/market_judgment.db"
    state_path: str = ".runtime/market_judge.json"
    quote_interval_sec: int = 60
    judge_interval_sec: int = 1800
    max_symbols: int = 60
    llm_max_symbols: int = 12
    use_naver_fallback: bool = False
    query: str = "장중 현재 움직임과 내 국장1 계좌를 반영해 관심/보류 판단을 정리해줘"
    request_timeout_sec: float = 8.0
    quote_concurrency: int = 4


def _jue_wiki_prompt_mode(jue_wiki: dict[str, Any] | None) -> str:
    if isinstance(jue_wiki, dict):
        mode = str(jue_wiki.get("prompt_mode") or "").strip().lower()
        if mode in {"observe", "assist", "primary"}:
            return mode
    return "assist"


def _sanitize_jue_wiki_observation(payload: dict[str, Any]) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    raw_pages = [
        page for page in list(payload.get("pages") or []) if isinstance(page, dict)
    ]
    for page in payload.get("pages") or []:
        if not isinstance(page, dict):
            continue
        pages.append(
            {
                "page_id": page.get("page_id"),
                "rank": page.get("rank"),
                "score": page.get("score"),
                "selection_reasons": list(page.get("selection_reasons") or []),
                "selection_penalties": list(page.get("selection_penalties") or []),
                "char_count": page.get("char_count"),
                "source_refs": list(page.get("source_refs") or []),
                "effectiveness": page.get("effectiveness")
                if isinstance(page.get("effectiveness"), dict)
                else {},
            }
        )
    rejected_pages = [
        {key: value for key, value in page.items() if key != "content"}
        for page in payload.get("rejected_pages") or []
        if isinstance(page, dict)
    ]
    effectiveness_attention_items = _compact_jue_wiki_effectiveness_attention_items(
        payload.get("effectiveness_attention_items")
    )
    if not effectiveness_attention_items:
        effectiveness_attention_items = _jue_wiki_effectiveness_attention_items_from_rows(
            raw_pages
        )
    return {
        "status": payload.get("status"),
        "selection_run_id": payload.get("selection_run_id"),
        "target_scope": payload.get("target_scope"),
        "prompt_mode": "observe",
        "configured_prompt_mode": payload.get("configured_prompt_mode"),
        "mode_recommendation": payload.get("mode_recommendation")
        if isinstance(payload.get("mode_recommendation"), dict)
        else {},
        "prompt_mode_policy": payload.get("prompt_mode_policy")
        if isinstance(payload.get("prompt_mode_policy"), dict)
        else {},
        "trust_profile_effectiveness": payload.get("trust_profile_effectiveness")
        if isinstance(payload.get("trust_profile_effectiveness"), dict)
        else {},
        "effectiveness_policy": payload.get("effectiveness_policy")
        if isinstance(payload.get("effectiveness_policy"), dict)
        else {},
        "repair_priorities": [
            dict(item)
            for item in list(payload.get("repair_priorities") or [])[:8]
            if isinstance(item, dict)
        ],
        "repair_priority_effectiveness": (
            compact_jue_wiki_repair_loop_effectiveness_for_prompt(
                payload.get("repair_priority_effectiveness")
            )
        ),
        "validation_repair_effectiveness": (
            compact_jue_wiki_validation_repair_effectiveness_for_prompt(
                payload.get("validation_repair_effectiveness")
            )
        ),
        "wiki_application_coverage": (
            compact_jue_wiki_application_coverage_for_prompt(
                payload.get("wiki_application_coverage")
            )
        ),
        "effectiveness_attention_items": effectiveness_attention_items,
        "pages": pages,
        "rejected_pages": rejected_pages,
        "budget_report": payload.get("budget_report") or {},
    }


def _compact_jue_wiki_source_ref(value: Any) -> dict[str, Any] | str:
    if not isinstance(value, dict):
        return _clean_text(value, limit=180)
    row: dict[str, Any] = {}
    for key in (
        "source_type",
        "source_id",
        "source_scope",
        "kind",
        "id",
        "status",
        "action_type",
        "repair_status",
        "decision_use",
        "observed_at",
        "as_of",
    ):
        child = value.get(key)
        if child not in (None, "", [], {}):
            row[key] = _clean_text(child, limit=180)
    symbols = [
        _clean_text(symbol, limit=40)
        for symbol in list(value.get("symbols") or [])[:6]
        if str(symbol).strip()
    ]
    if symbols:
        row["symbols"] = symbols
    evidence_quality = value.get("evidence_quality")
    if isinstance(evidence_quality, dict):
        canonical_evidence_quality = canonical_jue_wiki_evidence_quality(
            evidence_quality
        )
        row["evidence_quality"] = {
            key: canonical_evidence_quality.get(key)
            for key in (
                "summary_line",
                "source_count",
                "status_counts",
                "warning_counts",
                "source_type_counts",
                "top_warnings",
            )
            if canonical_evidence_quality.get(key) not in (None, "", [], {})
        }
    quality_status = normalize_jue_wiki_quality_status(value.get("quality_status"))
    if not quality_status:
        quality_status = _jue_wiki_quality_status_from_evidence(
            row.get("evidence_quality")
        )
    if quality_status:
        row["quality_status"] = quality_status
    quality_warnings = [
        _clean_text(warning, limit=120)
        for warning in list(value.get("quality_warnings") or [])[:6]
        if str(warning).strip()
    ]
    if not quality_warnings:
        quality_warnings = _jue_wiki_quality_warnings_from_evidence(
            row.get("evidence_quality"),
            limit=6,
        )
    if quality_warnings:
        row["quality_warnings"] = quality_warnings
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_quality_warning_effectiveness(
    value: Any,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(value or [])[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        warning = _clean_text(item.get("warning"), limit=120)
        if not warning:
            continue
        row: dict[str, Any] = {"warning": warning}
        for key, max_len in (
            ("page_id", 160),
            ("source_type", 80),
            ("source_id", 160),
            ("status", 80),
        ):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                row[key] = _clean_text(raw, limit=max_len)
        if item.get("sample_count") not in (None, "", [], {}):
            row["sample_count"] = _safe_int(item.get("sample_count"))
        for key in (
            "win_rate",
            "expectancy",
            "avg_return_pct",
            "helpful_score",
            "confidence",
        ):
            if item.get(key) not in (None, "", [], {}):
                row[key] = _safe_float(item.get(key))
        reasons = [
            _clean_text(reason, limit=120)
            for reason in list(item.get("reasons") or [])[:4]
            if str(reason).strip()
        ]
        if reasons:
            row["reasons"] = reasons
        rows.append(row)
    return rows


def _compact_jue_wiki_effectiveness_bundle(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (("status", 80), ("decision_use", 180), ("summary", 180)):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=max_len)
    metrics: list[dict[str, Any]] = []
    for item in list(value.get("metrics") or [])[:4]:
        if not isinstance(item, dict):
            continue
        metric: dict[str, Any] = {}
        for key, max_len in (
            ("warning", 120),
            ("page_id", 160),
            ("source_type", 80),
            ("source_id", 160),
            ("status", 80),
        ):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                metric[key] = _clean_text(raw, limit=max_len)
        if item.get("sample_count") not in (None, "", [], {}):
            metric["sample_count"] = _safe_int(item.get("sample_count"))
        for key in (
            "win_rate",
            "expectancy",
            "avg_return_pct",
            "helpful_score",
            "confidence",
        ):
            if item.get(key) not in (None, "", [], {}):
                metric[key] = _safe_float(item.get(key))
        reasons = [
            _clean_text(reason, limit=120)
            for reason in list(item.get("reasons") or [])[:4]
            if str(reason).strip()
        ]
        if reasons:
            metric["reasons"] = reasons
        if metric:
            metrics.append(metric)
    if metrics:
        row["metrics"] = metrics
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_usage_guidance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key, max_len in (
        ("trust_level", 40),
        ("risk_posture", 80),
        ("decision_use", 180),
    ):
        raw = value.get(key)
        if raw not in (None, "", [], {}):
            row[key] = _clean_text(raw, limit=max_len)
    for key in ("allowed_uses", "required_cross_checks"):
        items = [
            _clean_text(item, limit=100)
            for item in list(value.get(key) or [])[:8]
            if str(item).strip()
        ]
        if items:
            row[key] = items
    if value.get("hard_blocker") not in (None, "", [], {}):
        row["hard_blocker"] = bool(value.get("hard_blocker"))
    if value.get("max_confidence_without_cross_check") not in (None, "", [], {}):
        row["max_confidence_without_cross_check"] = _safe_float(
            value.get("max_confidence_without_cross_check")
        )
    return {key: child for key, child in row.items() if child not in (None, "", [], {})}


def _compact_jue_wiki_status_list(value: Any, *, limit: int = 6) -> list[str]:
    statuses: list[str] = []
    for item in list(value or [])[: max(int(limit), 0)]:
        status = _clean_text(item, limit=80).lower()
        if status and status not in statuses:
            statuses.append(status)
    return statuses


def _compact_jue_wiki_effectiveness_attention_items(
    value: Any,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in list(value or [])[: max(int(limit), 0)]:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        for key, max_len in (
            ("page_id", 160),
            ("kind", 80),
            ("status", 80),
            ("evidence_id", 180),
            ("warning", 160),
        ):
            raw = item.get(key)
            if raw not in (None, "", [], {}):
                row[key] = _clean_text(raw, limit=max_len)
        if row and row not in items:
            items.append(row)
    return items


def _jue_wiki_effectiveness_attention_items_from_rows(
    rows: list[Any],
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        page_id = _clean_text(row.get("page_id"), limit=160)
        if not page_id:
            continue
        for kind, key in (
            ("usage_guidance", "usage_guidance_effectiveness"),
            ("memory_card_quality", "memory_card_quality_effectiveness"),
            ("quality_warning_source", "quality_warning_source_effectiveness"),
            ("quality_warning", "quality_warning_effectiveness"),
        ):
            for item in _jue_wiki_effectiveness_attention_items_for_value(
                page_id=page_id,
                kind=kind,
                value=row.get(key),
            ):
                if item not in items:
                    items.append(item)
                if len(items) >= limit:
                    return _compact_jue_wiki_effectiveness_attention_items(
                        items,
                        limit=limit,
                    )
    return _compact_jue_wiki_effectiveness_attention_items(items, limit=limit)


def _jue_wiki_effectiveness_attention_items_for_value(
    *,
    page_id: str,
    kind: str,
    value: Any,
) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else [value]
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metrics = [
            metric
            for metric in list(row.get("metrics") or [])
            if isinstance(metric, dict)
        ]
        source_rows = metrics or [row]
        for source in source_rows:
            if not isinstance(source, dict):
                continue
            status = _clean_text(
                source.get("status") or row.get("status"),
                limit=80,
            ).lower()
            evidence_id = (
                ""
                if kind == "quality_warning"
                else _clean_text(
                    source.get("page_id")
                    or source.get("source_id")
                    or source.get("rule_id"),
                    limit=180,
                )
            )
            warning = _clean_text(
                source.get("warning") or row.get("warning"),
                limit=160,
            )
            if not status and not evidence_id and not warning:
                continue
            item: dict[str, Any] = {"page_id": page_id, "kind": kind}
            if status:
                item["status"] = status
            if evidence_id:
                item["evidence_id"] = evidence_id
            if warning:
                item["warning"] = warning
            if item not in items:
                items.append(item)
    return items


def _compact_jue_wiki_page_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key in (
        "page_id",
        "rank",
        "score",
        "reason",
        "char_count",
        "freshness",
        "quality_status",
        "updated_at",
        "as_of",
    ):
        child = value.get(key)
        if child not in (None, "", [], {}):
            row[key] = child
    if "quality_status" in row:
        row["quality_status"] = normalize_jue_wiki_quality_status(
            row.get("quality_status")
        )
    for key in ("selection_reasons", "selection_penalties"):
        raw = value.get(key)
        if isinstance(raw, list):
            raw_chars = len(_json_dumps(raw))
            if raw_chars <= 300:
                items = [_clean_text(item, limit=120) for item in raw[:3]]
                if items:
                    row[key] = items
            else:
                row[f"{key}_count"] = len(raw)
                row[f"{key}_chars"] = raw_chars
    quality_warnings = value.get("quality_warnings")
    if isinstance(quality_warnings, list):
        row["quality_warnings"] = [
            _clean_text(item, limit=120)
            for item in quality_warnings[:3]
            if str(item).strip()
        ]
    source_refs = value.get("source_refs")
    if isinstance(source_refs, list):
        refs = [
            ref
            for ref in (_compact_jue_wiki_source_ref(item) for item in source_refs[:3])
            if ref not in (None, "", [], {})
        ]
        if refs:
            row["source_refs"] = refs
    evidence_quality = value.get("evidence_quality")
    if isinstance(evidence_quality, dict):
        row["evidence_quality"] = canonical_jue_wiki_evidence_quality(evidence_quality)
        if "quality_status" not in row:
            status = _jue_wiki_quality_status_from_evidence(row["evidence_quality"])
            if status:
                row["quality_status"] = status
        if not row.get("quality_warnings"):
            warnings = _jue_wiki_quality_warnings_from_evidence(row["evidence_quality"])
            if warnings:
                row["quality_warnings"] = warnings
    effectiveness = value.get("effectiveness")
    if isinstance(effectiveness, dict):
        row["effectiveness"] = {
            str(key): _compact_prompt_value(effectiveness.get(key), list_limit=2, string_limit=100)
            for key in (
                "status",
                "sample_count",
                "win_rate",
                "expectancy",
                "avg_return_pct",
                "median_mae_pct",
                "drawdown_pressure",
                "helpful_score",
                "confidence",
                "reasons",
            )
            if effectiveness.get(key) not in (None, "", [], {})
        }
    usage_guidance = _compact_jue_wiki_usage_guidance(value.get("usage_guidance"))
    if usage_guidance:
        row["usage_guidance"] = usage_guidance
    for key in (
        "usage_guidance_effectiveness",
        "memory_card_quality_effectiveness",
        "quality_warning_source_effectiveness",
    ):
        effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle(
            value.get(key)
        )
        if effectiveness_bundle:
            row[key] = effectiveness_bundle
    quality_warning_effectiveness = _compact_jue_wiki_quality_warning_effectiveness(
        value.get("quality_warning_effectiveness")
    )
    if quality_warning_effectiveness:
        row["quality_warning_effectiveness"] = quality_warning_effectiveness
        statuses = _compact_jue_wiki_status_list(
            value.get("quality_warning_effectiveness_statuses")
        )
        if not statuses:
            statuses = _compact_jue_wiki_status_list(
                [item.get("status") for item in quality_warning_effectiveness]
            )
        if statuses:
            row["quality_warning_effectiveness_statuses"] = statuses
    return row


def _compact_jue_wiki_memory_text(value: Any, *, limit: int) -> str:
    text = _compact_prompt_text(value, limit=max(int(limit), 1))
    if not text:
        return ""
    if len(text) > limit and not re.search(r"\s", text):
        return ""
    sentence_match = re.match(r"^(.{1,%d}?[.!?。])(?:\s|$)" % max(limit, 1), text)
    if sentence_match:
        return sentence_match.group(1).strip()
    return _clean_text(text, limit=limit)


def _jue_wiki_quality_status_from_evidence(evidence_quality: Any) -> str:
    return jue_wiki_quality_status_from_evidence(evidence_quality)


def _jue_wiki_quality_warnings_from_evidence(
    evidence_quality: Any,
    *,
    limit: int = 3,
) -> list[str]:
    if not isinstance(evidence_quality, dict):
        return []
    warnings: list[str] = []
    for item in list(evidence_quality.get("top_warnings") or []):
        if isinstance(item, dict):
            warning = str(item.get("warning") or "").strip()
        else:
            warning = str(item).strip()
        if warning and warning not in warnings:
            warnings.append(_clean_text(warning, limit=120))
        if len(warnings) >= max(int(limit), 0):
            break
    return warnings


def _compact_jue_wiki_requested_symbol_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row: dict[str, Any] = {}
    for key in (
        "symbol",
        "page_id",
        "title",
        "selected_as_page",
        "confidence",
        "freshness",
        "quality_status",
        "updated_at",
        "as_of",
    ):
        child = value.get(key)
        if child not in (None, "", [], {}):
            row[key] = child
    if "quality_status" in row:
        row["quality_status"] = normalize_jue_wiki_quality_status(
            row.get("quality_status")
        )
    quality_warnings = value.get("quality_warnings")
    if isinstance(quality_warnings, list):
        row["quality_warnings"] = [
            _clean_text(item, limit=120)
            for item in quality_warnings[:3]
            if str(item).strip()
        ]
    evidence_quality = value.get("evidence_quality")
    if "quality_status" not in row:
        status = _jue_wiki_quality_status_from_evidence(evidence_quality)
        if status:
            row["quality_status"] = status
    if not row.get("quality_warnings"):
        warnings = _jue_wiki_quality_warnings_from_evidence(evidence_quality)
        if warnings:
            row["quality_warnings"] = warnings
    summary = _compact_jue_wiki_memory_text(value.get("summary"), limit=240)
    if summary:
        row["summary"] = summary
    if isinstance(evidence_quality, dict):
        canonical_evidence_quality = canonical_jue_wiki_evidence_quality(
            evidence_quality
        )
        row["evidence_quality"] = {
            key: canonical_evidence_quality.get(key)
            for key in ("summary_line", "status_counts", "top_warnings")
            if canonical_evidence_quality.get(key) not in (None, "", [], {})
        }
    effectiveness = value.get("effectiveness")
    if isinstance(effectiveness, dict):
        row["effectiveness"] = {
            key: _compact_prompt_value(effectiveness.get(key), list_limit=2, string_limit=100)
            for key in (
                "status",
                "sample_count",
                "win_rate",
                "expectancy",
                "helpful_score",
                "confidence",
                "reasons",
            )
            if effectiveness.get(key) not in (None, "", [], {})
        }
    usage_guidance = _compact_jue_wiki_usage_guidance(value.get("usage_guidance"))
    if usage_guidance:
        row["usage_guidance"] = usage_guidance
    for key in (
        "usage_guidance_effectiveness",
        "memory_card_quality_effectiveness",
        "quality_warning_source_effectiveness",
    ):
        effectiveness_bundle = _compact_jue_wiki_effectiveness_bundle(
            value.get(key)
        )
        if effectiveness_bundle:
            row[key] = effectiveness_bundle
    quality_warning_effectiveness = _compact_jue_wiki_quality_warning_effectiveness(
        value.get("quality_warning_effectiveness")
    )
    if quality_warning_effectiveness:
        row["quality_warning_effectiveness"] = quality_warning_effectiveness
        statuses = _compact_jue_wiki_status_list(
            value.get("quality_warning_effectiveness_statuses")
        )
        if not statuses:
            statuses = _compact_jue_wiki_status_list(
                [item.get("status") for item in quality_warning_effectiveness]
            )
        if statuses:
            row["quality_warning_effectiveness_statuses"] = statuses
    memory_card = value.get("memory_card")
    if isinstance(memory_card, dict):
        card: dict[str, str] = {}
        for key, limit in (
            ("stance", 220),
            ("durable_facts", 220),
            ("trading_history", 300),
            ("lessons", 260),
            ("contradictions", 160),
            ("open_questions", 260),
        ):
            text = _compact_jue_wiki_memory_text(memory_card.get(key), limit=limit)
            if text:
                card[key] = text
        if card:
            row["memory_card"] = card
    return row


def _compact_jue_wiki_prompt_payload(
    payload: dict[str, Any],
    *,
    max_chars: int,
) -> dict[str, Any]:
    budget = max(int(max_chars), 1_000)
    safe_budget = max(budget - 512, 1_000)
    original_chars = len(_json_dumps(payload))
    compact = dict(payload)
    repair_priority_effectiveness = compact.get("repair_priority_effectiveness")
    if isinstance(repair_priority_effectiveness, dict):
        compact["repair_priority_effectiveness"] = (
            compact_jue_wiki_repair_loop_effectiveness_for_prompt(
                repair_priority_effectiveness
            )
        )
    validation_repair_effectiveness = compact.get(
        "validation_repair_effectiveness"
    )
    if isinstance(validation_repair_effectiveness, dict):
        compact["validation_repair_effectiveness"] = (
            compact_jue_wiki_validation_repair_effectiveness_for_prompt(
                validation_repair_effectiveness
            )
        )
    wiki_application_coverage = compact.get("wiki_application_coverage")
    if isinstance(wiki_application_coverage, dict):
        compact["wiki_application_coverage"] = (
            compact_jue_wiki_application_coverage_for_prompt(
                wiki_application_coverage
            )
        )
    effectiveness_attention_items = compact.get("effectiveness_attention_items")
    if isinstance(effectiveness_attention_items, list):
        compact["effectiveness_attention_items"] = (
            _compact_jue_wiki_effectiveness_attention_items(
                effectiveness_attention_items
            )
        )
    pages = compact.get("pages")
    if isinstance(pages, list):
        compact["pages"] = [
            row
            for row in (_compact_jue_wiki_page_row(page) for page in pages[:12])
            if row
        ]
    rejected_pages = compact.get("rejected_pages")
    if isinstance(rejected_pages, list):
        compact["rejected_pages"] = [
            row
            for row in (
                _compact_jue_wiki_page_row(page) for page in rejected_pages[:20]
            )
            if row
        ]
        omitted = max(len(rejected_pages) - len(compact["rejected_pages"]), 0)
        if omitted:
            compact["rejected_pages_omitted_count"] = omitted
    requested_symbol_summaries = compact.get("requested_symbol_summaries")
    if isinstance(requested_symbol_summaries, list):
        compact["requested_symbol_summaries"] = [
            row
            for row in (
                _compact_jue_wiki_requested_symbol_summary(item)
                for item in requested_symbol_summaries[:8]
            )
            if row
        ]
        omitted = max(
            len(requested_symbol_summaries)
            - len(compact["requested_symbol_summaries"]),
            0,
        )
        if omitted:
            compact["requested_symbol_summaries_omitted_count"] = omitted
    if not compact.get("effectiveness_attention_items"):
        compact_pages = (
            compact.get("pages") if isinstance(compact.get("pages"), list) else []
        )
        compact_requested = (
            compact.get("requested_symbol_summaries")
            if isinstance(compact.get("requested_symbol_summaries"), list)
            else []
        )
        derived_attention_items = _jue_wiki_effectiveness_attention_items_from_rows(
            [*compact_pages, *compact_requested]
        )
        if derived_attention_items:
            compact["effectiveness_attention_items"] = derived_attention_items
    content = str(compact.get("content") or "")
    if content:
        content_limit = max(min(int(budget * 0.72), budget - 3_000), 800)
        compact["content"] = _clean_text(content, limit=content_limit)
    while len(_json_dumps(compact)) > budget and compact.get("content"):
        overflow = len(_json_dumps(compact)) - budget
        current = str(compact.get("content") or "")
        next_limit = max(len(current) - overflow - 512, 0)
        compact["content"] = _clean_text(current, limit=next_limit)
        if next_limit <= 0:
            compact.pop("content", None)
            break
    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("rejected_pages"), list
    ):
        compact["rejected_pages"] = list(compact["rejected_pages"][:8])
    if len(_json_dumps(compact)) > budget and isinstance(compact.get("pages"), list):
        compact["pages"] = list(compact["pages"][:6])
    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("requested_symbol_summaries"), list
    ):
        compact["requested_symbol_summaries"] = list(
            compact["requested_symbol_summaries"][:4]
        )
    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("requested_symbol_summaries"), list
    ):
        for row in compact["requested_symbol_summaries"]:
            if isinstance(row, dict):
                row.pop("memory_card", None)
    final_chars = len(_json_dumps(compact))
    if final_chars < original_chars or original_chars > budget:
        report = (
            dict(compact.get("budget_report"))
            if isinstance(compact.get("budget_report"), dict)
            else {}
        )
        report.update(
            {
                "prompt_payload_original_chars": original_chars,
                "prompt_payload_chars": final_chars,
                "prompt_payload_max_chars": budget,
                "prompt_payload_status": (
                    "compacted" if final_chars < original_chars else "ok"
                ),
            }
        )
        compact["budget_report"] = report
    while len(_json_dumps(compact)) > budget and isinstance(
        compact.get("requested_symbol_summaries"), list
    ) and len(compact["requested_symbol_summaries"]) > 1:
        compact["requested_symbol_summaries"].pop()
        compact["requested_symbol_summaries_omitted_count"] = int(
            compact.get("requested_symbol_summaries_omitted_count") or 0
        ) + 1
    if len(_json_dumps(compact)) > budget and isinstance(
        compact.get("requested_symbol_summaries"), list
    ):
        for row in compact["requested_symbol_summaries"]:
            if isinstance(row, dict):
                row.pop("memory_card", None)
    if isinstance(compact.get("budget_report"), dict):
        for _ in range(3):
            compact["budget_report"]["prompt_payload_chars"] = len(
                _json_dumps(compact)
            )
        while len(_json_dumps(compact)) > safe_budget and isinstance(
            compact.get("requested_symbol_summaries"), list
        ) and len(compact["requested_symbol_summaries"]) > 1:
            compact["requested_symbol_summaries"].pop()
            compact["requested_symbol_summaries_omitted_count"] = int(
                compact.get("requested_symbol_summaries_omitted_count") or 0
            ) + 1
            compact["budget_report"]["prompt_payload_chars"] = len(
                _json_dumps(compact)
            )
    return compact


def _jue_wiki_application_metadata(jue_wiki: dict[str, Any]) -> dict[str, Any]:
    pages = jue_wiki.get("pages") if isinstance(jue_wiki.get("pages"), list) else []
    requested_summaries = (
        jue_wiki.get("requested_symbol_summaries")
        if isinstance(jue_wiki.get("requested_symbol_summaries"), list)
        else []
    )
    selected_page_ids = _jue_wiki_page_ids(pages)
    requested_symbol_summary_page_ids = _jue_wiki_page_ids(requested_summaries)
    applied_page_ids = list(
        dict.fromkeys([*selected_page_ids, *requested_symbol_summary_page_ids])
    )
    metadata = {
        "status": "ok" if jue_wiki.get("selection_run_id") else "missing",
        "selection_run_id": str(jue_wiki.get("selection_run_id") or ""),
        "prompt_mode": str(jue_wiki.get("prompt_mode") or ""),
        "selected_page_ids": selected_page_ids,
        "requested_symbol_summary_page_ids": requested_symbol_summary_page_ids,
        "applied_page_ids": applied_page_ids,
        "requested_symbol_summary_count": len(requested_symbol_summary_page_ids),
        "budget_report": jue_wiki.get("budget_report")
        if isinstance(jue_wiki.get("budget_report"), dict)
        else {},
    }
    configured_mode = str(jue_wiki.get("configured_prompt_mode") or "").strip()
    if configured_mode:
        metadata["configured_prompt_mode"] = configured_mode
    if isinstance(jue_wiki.get("mode_recommendation"), dict):
        metadata["mode_recommendation"] = jue_wiki["mode_recommendation"]
    if isinstance(jue_wiki.get("prompt_mode_policy"), dict):
        metadata["prompt_mode_policy"] = jue_wiki["prompt_mode_policy"]
    trust_profile = build_jue_wiki_trust_profile_for_prompt(jue_wiki)
    if trust_profile:
        metadata["trust_profile"] = trust_profile
        decision_adjustments = build_jue_wiki_decision_adjustments_for_prompt(
            trust_profile
        )
        if decision_adjustments:
            metadata["decision_adjustments"] = decision_adjustments
    if isinstance(jue_wiki.get("trust_profile_effectiveness"), dict):
        metadata["trust_profile_effectiveness"] = jue_wiki[
            "trust_profile_effectiveness"
        ]
    validation_repair_effectiveness = (
        compact_jue_wiki_validation_repair_effectiveness_for_prompt(
            jue_wiki.get("validation_repair_effectiveness")
        )
    )
    if validation_repair_effectiveness:
        metadata["validation_repair_effectiveness"] = (
            validation_repair_effectiveness
        )
    wiki_application_coverage = compact_jue_wiki_application_coverage_for_prompt(
        jue_wiki.get("wiki_application_coverage")
    )
    if wiki_application_coverage:
        metadata["wiki_application_coverage"] = wiki_application_coverage
    effectiveness_attention_items = _compact_jue_wiki_effectiveness_attention_items(
        jue_wiki.get("effectiveness_attention_items")
    )
    if not effectiveness_attention_items:
        effectiveness_attention_items = _jue_wiki_effectiveness_attention_items_from_rows(
            [*pages, *requested_summaries]
        )
    if effectiveness_attention_items:
        metadata["effectiveness_attention_items"] = effectiveness_attention_items
    quality_summary = summarize_jue_wiki_quality_pressure_for_prompt(
        [*pages, *requested_summaries]
    )
    if quality_summary:
        metadata["quality_summary"] = quality_summary
        quality_action_plan = build_jue_wiki_quality_pressure_action_plan_for_prompt(
            quality_summary
        )
        if quality_action_plan:
            metadata["quality_pressure_action_plan"] = quality_action_plan
    coverage_action_plan = _jue_wiki_requested_symbol_coverage_action_plan(
        metadata.get("budget_report") if isinstance(metadata.get("budget_report"), dict) else {}
    )
    if coverage_action_plan:
        metadata["requested_symbol_coverage_action_plan"] = coverage_action_plan
    return metadata


def _jue_wiki_page_ids(rows: list[Any]) -> list[str]:
    page_ids = [
        str(row.get("page_id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("page_id") or "").strip()
    ]
    return list(dict.fromkeys(page_ids))


def _attach_jue_wiki_repair_contract(
    prompt: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    contract = build_jue_wiki_repair_contract_for_prompt(payload)
    if not contract:
        prompt.pop("jue_wiki_repair_contract", None)
        return
    prompt["jue_wiki_repair_contract"] = contract
    decision_inputs = list(prompt.get("decision_inputs") or [])
    if "jue_wiki_repair_contract" not in decision_inputs:
        decision_inputs.append("jue_wiki_repair_contract")
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_validation_repair_effectiveness_input(
    prompt: dict[str, Any],
) -> None:
    marker = "jue_wiki_validation_repair_effectiveness"
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    validation = (
        application.get("validation_repair_effectiveness")
        if isinstance(application.get("validation_repair_effectiveness"), dict)
        else {}
    )
    decision_inputs = [
        item for item in list(prompt.get("decision_inputs") or []) if item != marker
    ]
    if not validation:
        prompt.pop(marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        elif "decision_inputs" in prompt:
            prompt.pop("decision_inputs", None)
        return
    prompt[marker] = validation
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_validation_repair_contract(prompt: dict[str, Any]) -> None:
    marker = "jue_wiki_validation_repair_contract"
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    contract = build_jue_wiki_validation_repair_contract_for_prompt(application)
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if not contract:
        prompt.pop(marker, None)
        if decision_inputs != existing_inputs:
            if decision_inputs:
                prompt["decision_inputs"] = decision_inputs
            else:
                prompt.pop("decision_inputs", None)
        return
    prompt[marker] = contract
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_contract_feedback_gap_input(prompt: dict[str, Any]) -> None:
    marker = "jue_wiki_contract_feedback_gap"
    contract = (
        prompt.get("jue_wiki_validation_repair_contract")
        if isinstance(prompt.get("jue_wiki_validation_repair_contract"), dict)
        else {}
    )
    gap = (
        contract.get("contract_feedback_gap")
        if isinstance(contract.get("contract_feedback_gap"), dict)
        else {}
    )
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if not gap:
        prompt.pop(marker, None)
        if decision_inputs != existing_inputs:
            if decision_inputs:
                prompt["decision_inputs"] = decision_inputs
            else:
                prompt.pop("decision_inputs", None)
        return
    prompt[marker] = {
        **gap,
        "source_contract": "jue_wiki_validation_repair_contract",
    }
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _market_validation_repair_resolution_schema() -> dict[str, Any]:
    return {
        "required": (
            "mandatory when jue_wiki_contract_feedback_gap is present or "
            "jue_wiki_validation_repair_contract.requires_validation_repair_resolution=true"
        ),
        "resolved_candidates": [
            {
                "symbol": "6-digit KRX code or MARKET",
                "resolution": (
                    "regime_confirmed_wait|candidate_rejected|risk_check_defer|"
                    "new_watch_with_trigger"
                ),
                "horizon": "intraday|short_term|mid_term|long_term|unknown",
                "next_trigger": "price/volume/regime condition that would change judgment",
                "evidence_gap": "precise missing evidence, if rejected or deferred",
                "expected_wiki_update": (
                    "what future wiki/memory update should learn from this judgment"
                ),
            }
        ],
        "blanket_hold_allowed": False,
    }


def _market_validation_repair_response_contract(
    prompt: dict[str, Any],
) -> dict[str, Any]:
    contract = (
        prompt.get("jue_wiki_validation_repair_contract")
        if isinstance(prompt.get("jue_wiki_validation_repair_contract"), dict)
        else {}
    )
    feedback_gap = (
        prompt.get("jue_wiki_contract_feedback_gap")
        if isinstance(prompt.get("jue_wiki_contract_feedback_gap"), dict)
        else {}
    )
    requires_resolution = bool(
        contract.get("requires_validation_repair_resolution") or feedback_gap
    )
    if not requires_resolution:
        return {}
    out: dict[str, Any] = {
        "version": "market_validation_repair_response_contract_v1",
        "required_when": (
            "jue_wiki_validation_repair_contract requires validation repair "
            "resolution or jue_wiki_contract_feedback_gap is present"
        ),
        "required_output": "validation_repair_resolution",
        "core_rule": (
            "Market wiki repair pressure must be turned into concrete regime, "
            "risk, trigger, or reject evidence so future Jue Wiki updates can "
            "measure whether this judgment repaired the degraded memory."
        ),
        "accepted_resolutions": [
            "regime_confirmed_wait",
            "candidate_rejected",
            "risk_check_defer",
            "new_watch_with_trigger",
        ],
        "blanket_hold_allowed": False,
    }
    if contract:
        out["source_contract"] = {
            key: value
            for key, value in {
                "status": contract.get("status"),
                "top_disciplines": contract.get("top_disciplines"),
                "repair_action_ids": contract.get("repair_action_ids"),
                "allowed_entry_postures": contract.get("allowed_entry_postures"),
                "contract_feedback_gap": contract.get("contract_feedback_gap"),
            }.items()
            if value not in (None, "", [], {})
        }
    if feedback_gap:
        out["feedback_gap"] = feedback_gap
    return out


def _attach_market_validation_repair_response_contract(
    prompt: dict[str, Any],
) -> None:
    marker = "validation_repair_response_contract"
    contract = _market_validation_repair_response_contract(prompt)
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    output_schema = (
        prompt.get("output_schema") if isinstance(prompt.get("output_schema"), dict) else {}
    )
    if not contract:
        prompt.pop(marker, None)
        if isinstance(output_schema, dict):
            output_schema.pop("validation_repair_resolution", None)
        if decision_inputs != existing_inputs:
            if decision_inputs:
                prompt["decision_inputs"] = decision_inputs
            else:
                prompt.pop("decision_inputs", None)
        return
    prompt[marker] = contract
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs
    if isinstance(output_schema, dict):
        output_schema["validation_repair_resolution"] = (
            _market_validation_repair_resolution_schema()
        )


def _market_prompt_requires_validation_repair_resolution(
    prompt: dict[str, Any],
) -> bool:
    if not isinstance(prompt, dict):
        return False
    response_contract = (
        prompt.get("validation_repair_response_contract")
        if isinstance(prompt.get("validation_repair_response_contract"), dict)
        else {}
    )
    if response_contract:
        return True
    contract = (
        prompt.get("jue_wiki_validation_repair_contract")
        if isinstance(prompt.get("jue_wiki_validation_repair_contract"), dict)
        else {}
    )
    if bool(contract.get("requires_validation_repair_resolution")):
        return True
    if isinstance(contract.get("contract_feedback_gap"), dict):
        return True
    feedback_gap = (
        prompt.get("jue_wiki_contract_feedback_gap")
        if isinstance(prompt.get("jue_wiki_contract_feedback_gap"), dict)
        else {}
    )
    return bool(feedback_gap)


def _market_response_has_concrete_repair_resolution(
    response: dict[str, Any],
) -> bool:
    resolution = (
        response.get("validation_repair_resolution")
        if isinstance(response, dict)
        else {}
    )
    resolution = resolution if isinstance(resolution, dict) else {}
    accepted = {
        "regime_confirmed_wait",
        "candidate_rejected",
        "risk_check_defer",
        "new_watch_with_trigger",
    }
    for row in list(resolution.get("resolved_candidates") or []):
        if not isinstance(row, dict):
            continue
        kind = str(row.get("resolution") or "").strip().lower()
        if kind not in accepted:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol != "MARKET" and not _is_symbol(symbol):
            continue
        next_trigger = _clean_text(row.get("next_trigger"), limit=300)
        evidence_gap = _clean_text(row.get("evidence_gap"), limit=300)
        if kind in {"candidate_rejected", "risk_check_defer"}:
            if evidence_gap or next_trigger:
                return True
            continue
        if next_trigger:
            return True
    return False


def market_judgment_response_contract_error(
    *,
    prompt: dict[str, Any],
    response: dict[str, Any],
) -> str:
    if not _market_prompt_requires_validation_repair_resolution(prompt):
        return ""
    if _market_response_has_concrete_repair_resolution(response):
        return ""
    return "validation_repair_resolution_missing_from_model"


def _attach_jue_wiki_application_coverage_input(prompt: dict[str, Any]) -> None:
    marker = "jue_wiki_application_coverage"
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    coverage = (
        application.get("wiki_application_coverage")
        if isinstance(application.get("wiki_application_coverage"), dict)
        else {}
    )
    decision_inputs = [
        item for item in list(prompt.get("decision_inputs") or []) if item != marker
    ]
    if not coverage:
        prompt.pop(marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        elif "decision_inputs" in prompt:
            prompt.pop("decision_inputs", None)
        return
    prompt[marker] = coverage
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_decision_adjustments_input(prompt: dict[str, Any]) -> None:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    marker = "jue_wiki_decision_adjustments"
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if application.get("decision_adjustments"):
        decision_inputs.append(marker)
        prompt["decision_inputs"] = decision_inputs
    elif decision_inputs != existing_inputs:
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        else:
            prompt.pop("decision_inputs", None)


def _market_requested_symbol_token(value: Any) -> str:
    text = _clean_text(value, limit=80).upper()
    match = re.search(r"\b\d{6}\b", text)
    if match:
        return match.group(0)
    match = re.search(r"\b[A-Z0-9]{2,24}(?:USDT|USDC|BTC|ETH|BNB|KRW)\b", text)
    if match:
        return match.group(0)
    return ""


def _market_requested_symbol_list(value: Any, *, limit: int = 24) -> list[str]:
    if isinstance(value, (list, tuple)):
        rows = value
    else:
        rows = []
    symbols = [
        symbol
        for symbol in (_market_requested_symbol_token(item) for item in rows)
        if symbol
    ]
    return list(dict.fromkeys(symbols))[: max(int(limit), 0)]


def _market_requested_symbol_degraded_reasons(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for item in list(value)[:24]:
        if not isinstance(item, dict):
            continue
        symbol = _market_requested_symbol_token(item.get("symbol"))
        if not symbol:
            continue
        row: dict[str, Any] = {"symbol": symbol}
        freshness = _clean_text(item.get("freshness"), limit=80)
        if freshness:
            row["freshness"] = freshness
        quality_status = normalize_jue_wiki_quality_status(item.get("quality_status"))
        if quality_status:
            row["quality_status"] = quality_status
        warnings = [
            _clean_text(warning, limit=120)
            for warning in list(item.get("quality_warnings") or [])[:6]
            if str(warning).strip()
        ]
        if warnings:
            row["quality_warnings"] = warnings
        rows.append(row)
    return rows


def _jue_wiki_requested_symbol_coverage_action_plan(
    budget_report: dict[str, Any],
) -> dict[str, Any]:
    status = str(
        budget_report.get("requested_symbol_summary_coverage_status") or ""
    ).strip()
    has_missing_field = "requested_symbol_missing_summary_symbols" in budget_report
    has_prompt_omitted_field = (
        "requested_symbol_prompt_omitted_symbols" in budget_report
    )
    missing_symbols = _market_requested_symbol_list(
        budget_report.get("requested_symbol_missing_summary_symbols")
    )
    prompt_omitted_symbols = _market_requested_symbol_list(
        budget_report.get("requested_symbol_prompt_omitted_symbols")
    )
    degraded_symbols = _market_requested_symbol_list(
        budget_report.get("requested_symbol_degraded_summary_symbols")
    )
    degraded_reasons = _market_requested_symbol_degraded_reasons(
        budget_report.get("requested_symbol_degraded_summary_reasons")
    )
    unsummarized_symbols = _market_requested_symbol_list(
        budget_report.get("requested_symbol_unsummarized_symbols")
    )
    if not unsummarized_symbols and (missing_symbols or prompt_omitted_symbols):
        unsummarized_symbols = list(
            dict.fromkeys([*missing_symbols, *prompt_omitted_symbols])
        )
    if status not in {"partial", "none"} and not degraded_symbols:
        status = "partial" if unsummarized_symbols else ""
    if (
        status not in {"partial", "none", "full"}
        or (not unsummarized_symbols and not degraded_symbols)
    ):
        return {}
    requested_count = int(budget_report.get("requested_symbol_count") or 0)
    unsummarized_count = int(
        budget_report.get("requested_symbol_unsummarized_count")
        or len(unsummarized_symbols)
    )
    summarized_count = max(requested_count - unsummarized_count, 0)
    required_adjustments: list[dict[str, Any]] = []
    if has_missing_field or has_prompt_omitted_field:
        if missing_symbols:
            required_adjustments.append(
                {
                    "adjustment_type": "coverage_gap_follow_up",
                    "reason": "requested_symbols_missing_from_wiki_summary",
                    "symbols": missing_symbols,
                    "resolution": (
                        "collect_or_rebuild_summary_before_confident_decision"
                    ),
                }
            )
        if prompt_omitted_symbols:
            required_adjustments.append(
                {
                    "adjustment_type": "prompt_omission_follow_up",
                    "reason": "requested_symbols_omitted_from_prompt_summary",
                    "symbols": prompt_omitted_symbols,
                    "resolution": (
                        "treat_as_reviewed_but_lower_confidence_until_direct_summary_check"
                    ),
                }
            )
    elif unsummarized_symbols:
        required_adjustments.append(
            {
                "adjustment_type": "coverage_gap_follow_up",
                "reason": "requested_symbols_missing_from_wiki_summary",
                "symbols": unsummarized_symbols,
                "resolution": (
                    "defer_confident_decision_until_summary_or_live_cross_check"
                ),
            }
        )
    if degraded_symbols:
        required_adjustments.append(
            {
                "adjustment_type": "degraded_summary_cross_check",
                "reason": "requested_symbol_summary_stale_or_weak",
                "symbols": degraded_symbols,
                "resolution": (
                    "cross_check_live_research_and_lower_confidence_until_refreshed"
                ),
            }
        )
    plan = {
        "status": status,
        "hard_blocker": False,
        "decision_policy": (
            "do_not_assume_unsummarized_symbols_were_reviewed"
            if unsummarized_symbols
            else "do_not_overtrust_stale_or_weak_requested_symbol_summaries"
        ),
        "requested_symbol_count": requested_count,
        "summarized_symbol_count": summarized_count,
        "unsummarized_symbol_count": unsummarized_count,
        "unsummarized_symbols": unsummarized_symbols,
        "required_adjustments": required_adjustments,
    }
    if degraded_symbols:
        plan["degraded_summary_count"] = int(
            budget_report.get("requested_symbol_degraded_summary_count")
            or len(degraded_symbols)
        )
        plan["degraded_summary_symbols"] = degraded_symbols
        if degraded_reasons:
            plan["degraded_summary_reasons"] = degraded_reasons
        if not unsummarized_symbols:
            plan["required_action"] = (
                "before confident decisions on stale or weak requested-symbol "
                "summaries, cross-check live research and treat the wiki memory "
                "as cautionary until refreshed"
            )
    if has_missing_field:
        plan["missing_summary_count"] = int(
            budget_report.get("requested_symbol_missing_summary_count")
            or len(missing_symbols)
        )
        plan["missing_summary_symbols"] = missing_symbols
    if has_prompt_omitted_field:
        plan["prompt_omitted_count"] = int(
            budget_report.get("requested_symbol_prompt_omitted_count")
            or len(prompt_omitted_symbols)
        )
        plan["prompt_omitted_symbols"] = prompt_omitted_symbols
    return plan


def _jue_wiki_requested_symbol_coverage_contract(
    action_plan: dict[str, Any],
) -> dict[str, Any]:
    contract = {
        "version": "jue_wiki_requested_symbol_coverage_v1",
        "status": str(action_plan.get("status") or ""),
        "hard_blocker": bool(action_plan.get("hard_blocker") or False),
        "decision_policy": str(action_plan.get("decision_policy") or ""),
        "required_action": str(
            action_plan.get("required_action")
            or (
                "before confident decisions on unsummarized symbols, perform live "
                "cross-check or request/record a fresh wiki summary"
            )
        ),
        "unsummarized_symbols": _market_requested_symbol_list(
            action_plan.get("unsummarized_symbols")
        ),
        "required_adjustments": [
            dict(item)
            for item in list(action_plan.get("required_adjustments") or [])[:4]
            if isinstance(item, dict)
        ],
    }
    if "missing_summary_symbols" in action_plan:
        contract["missing_summary_symbols"] = _market_requested_symbol_list(
            action_plan.get("missing_summary_symbols")
        )
    if "prompt_omitted_symbols" in action_plan:
        contract["prompt_omitted_symbols"] = _market_requested_symbol_list(
            action_plan.get("prompt_omitted_symbols")
        )
    if "degraded_summary_symbols" in action_plan:
        contract["degraded_summary_symbols"] = _market_requested_symbol_list(
            action_plan.get("degraded_summary_symbols")
        )
    if "degraded_summary_reasons" in action_plan:
        contract["degraded_summary_reasons"] = (
            _market_requested_symbol_degraded_reasons(
                action_plan.get("degraded_summary_reasons")
            )
        )
    return contract


def _attach_jue_wiki_requested_symbol_coverage_input(
    prompt: dict[str, Any],
) -> None:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    marker = "jue_wiki_requested_symbol_coverage"
    plan = (
        application.get("requested_symbol_coverage_action_plan")
        if isinstance(application.get("requested_symbol_coverage_action_plan"), dict)
        else {}
    )
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if plan:
        prompt[marker] = _jue_wiki_requested_symbol_coverage_contract(plan)
        decision_inputs.append(marker)
        prompt["decision_inputs"] = decision_inputs
    elif decision_inputs != existing_inputs:
        prompt.pop(marker, None)
        if decision_inputs:
            prompt["decision_inputs"] = decision_inputs
        else:
            prompt.pop("decision_inputs", None)


def _attach_jue_wiki_decision_adjustment_audit_contract(
    prompt: dict[str, Any],
) -> None:
    application = (
        prompt.get("jue_wiki_application")
        if isinstance(prompt.get("jue_wiki_application"), dict)
        else {}
    )
    marker = "jue_wiki_decision_adjustment_audit_contract"
    contract = build_jue_wiki_decision_adjustment_audit_contract_for_prompt(
        application
    )
    existing_inputs = list(prompt.get("decision_inputs") or [])
    decision_inputs = [item for item in existing_inputs if item != marker]
    if not contract:
        prompt.pop(marker, None)
        if decision_inputs != existing_inputs:
            if decision_inputs:
                prompt["decision_inputs"] = decision_inputs
            else:
                prompt.pop("decision_inputs", None)
        return
    prompt[marker] = contract
    decision_inputs.append(marker)
    prompt["decision_inputs"] = decision_inputs


def _attach_jue_wiki_prompt_context(
    prompt: dict[str, Any],
    jue_wiki: dict[str, Any] | None,
    *,
    max_chars: int,
) -> None:
    payload = jue_wiki if isinstance(jue_wiki, dict) else {"status": "missing"}
    mode = _jue_wiki_prompt_mode(payload)
    if mode == "observe":
        observation = _sanitize_jue_wiki_observation(payload)
        prompt["jue_wiki_selection_observation"] = observation
        prompt["jue_wiki_application"] = _jue_wiki_application_metadata(observation)
        _attach_jue_wiki_decision_adjustments_input(prompt)
        _attach_jue_wiki_requested_symbol_coverage_input(prompt)
        _attach_jue_wiki_validation_repair_effectiveness_input(prompt)
        _attach_jue_wiki_validation_repair_contract(prompt)
        _attach_jue_wiki_contract_feedback_gap_input(prompt)
        _attach_market_validation_repair_response_contract(prompt)
        _attach_jue_wiki_application_coverage_input(prompt)
        _attach_jue_wiki_decision_adjustment_audit_contract(prompt)
        _attach_jue_wiki_repair_contract(prompt, observation)
        prompt.pop("jue_wiki", None)
        prompt.pop("jue_wiki_budget_report", None)
        return
    payload = _compact_jue_wiki_prompt_payload(payload, max_chars=max_chars)
    if mode == "primary":
        payload = {
            **payload,
            "prompt_mode": "primary",
            "primary_context": True,
            "raw_context_policy": "evidence_only",
        }
        prompt["jue_wiki_primary_context_policy"] = {
            "raw_context_policy": "evidence_only",
            "instruction": (
                "Treat raw memory, RAG, and research context as compact evidence "
                "summaries only; use selected Jue Wiki pages as the primary "
                "compiled knowledge context."
            ),
        }
    prompt["jue_wiki"] = payload
    prompt["jue_wiki_application"] = _jue_wiki_application_metadata(payload)
    _attach_jue_wiki_decision_adjustments_input(prompt)
    _attach_jue_wiki_requested_symbol_coverage_input(prompt)
    _attach_jue_wiki_validation_repair_effectiveness_input(prompt)
    _attach_jue_wiki_validation_repair_contract(prompt)
    _attach_jue_wiki_contract_feedback_gap_input(prompt)
    _attach_market_validation_repair_response_contract(prompt)
    _attach_jue_wiki_application_coverage_input(prompt)
    _attach_jue_wiki_decision_adjustment_audit_contract(prompt)
    _attach_jue_wiki_repair_contract(prompt, payload)
    attach_jue_wiki_budget_report(prompt, max_chars=max_chars)


def _looks_like_signature_type_error(exc: TypeError) -> bool:
    message = str(exc)
    return any(
        marker in message
        for marker in (
            "unexpected keyword argument",
            "positional arguments but",
            "takes no keyword arguments",
            "takes 0 positional arguments",
            "required positional argument",
            "missing 1 required",
        )
    )


def _call_wiki_context_provider(
    provider: Callable[..., dict[str, Any]],
    *,
    target_scope: str,
    symbols: list[str],
    horizons: list[str] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "target_scope": target_scope,
        "symbols": symbols,
        "horizons": horizons or [],
    }
    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        parameters = signature.parameters
        if not parameters:
            return provider()
        accepts_var_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if not accepts_var_kwargs:
            kwargs = {key: value for key, value in kwargs.items() if key in parameters}
    try:
        payload = provider(**kwargs)
    except TypeError as exc:
        if not _looks_like_signature_type_error(exc):
            raise
        if signature is not None and signature.parameters:
            raise
        payload = provider()
    return payload


def _market_session_wiki_horizon(clock: dict[str, Any]) -> str:
    session = str(clock.get("session") or "").strip().lower()
    if not session:
        return ""
    clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in session)
    clean = "_".join(part for part in clean.split("_") if part)
    return f"market_session:{clean}" if clean else ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_int(value: Any) -> int:
    return int(round(_safe_float(value)))


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default
    return parsed


_QUOTE_RAW_ALLOWLIST = {
    "stck_shrn_iscd",
    "hts_kor_isnm",
    "stck_prpr",
    "prdy_vrss",
    "prdy_ctrt",
    "prdy_vrss_sign",
    "stck_oprc",
    "stck_hgpr",
    "stck_lwpr",
    "acml_vol",
    "acml_tr_pbmn",
    "aspr_unit",
    "bstp_kor_isnm",
    "source",
    "symbol",
    "name",
}


def _compact_quote_raw_for_storage(value: Any, *, max_raw_chars: int = 1200) -> Any:
    if not isinstance(value, dict):
        return value
    try:
        raw_text = _json_dumps(value)
    except (TypeError, ValueError):
        return {"_raw_compacted": True, "_raw_error": "non_json_serializable"}
    if len(raw_text) <= max(int(max_raw_chars), 1):
        return value

    compact: dict[str, Any] = {
        key: item
        for key, item in value.items()
        if key in _QUOTE_RAW_ALLOWLIST and isinstance(item, (str, int, float, bool))
    }
    if not compact:
        for key, item in value.items():
            if len(compact) >= 24:
                break
            if isinstance(item, (int, float, bool)):
                compact[str(key)] = item
                continue
            if isinstance(item, str) and len(item) <= 180:
                compact[str(key)] = item
    compact["_raw_compacted"] = True
    compact["_raw_key_count"] = len(value)
    compact["_raw_original_chars"] = len(raw_text)
    compact["_raw_stored_keys"] = len(compact)
    return compact


def _clean_text(value: Any, *, limit: int = 260) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(int(limit), 1)]


def _compact_prompt_text(value: Any, *, limit: int = 260) -> str:
    text = _clean_text(value, limit=max(int(limit), 1))
    text = re.sub(r"RAW_[A-Z0-9_]{8,}", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_prompt_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    numeric = _safe_float(value)
    if numeric == 0.0 and str(value).strip() not in {"0", "0.0", "0.00"}:
        return None
    if abs(numeric - round(numeric)) < 0.000001:
        return int(round(numeric))
    return round(numeric, 4)


_PROMPT_DROP_KEYS = {
    "raw",
    "raw_json",
    "html",
    "payload",
    "prompt",
    "response",
    "source_snapshot",
    "source_snapshot_json",
    "input",
    "output",
}


def _compact_prompt_value(
    value: Any,
    *,
    depth: int = 0,
    list_limit: int = 8,
    string_limit: int = 260,
) -> Any:
    if depth > 4:
        return _compact_prompt_text(value, limit=string_limit)
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key or "")
            if not text_key or text_key.lower() in _PROMPT_DROP_KEYS:
                continue
            compacted = _compact_prompt_value(
                item,
                depth=depth + 1,
                list_limit=list_limit,
                string_limit=max(int(string_limit * 0.8), 80),
            )
            if compacted in ({}, [], "", None):
                continue
            compact[text_key] = compacted
            if len(compact) >= 24:
                break
        return compact
    if isinstance(value, list):
        return [
            item
            for item in (
                _compact_prompt_value(
                    row,
                    depth=depth + 1,
                    list_limit=list_limit,
                    string_limit=max(int(string_limit * 0.8), 80),
                )
                for row in value[: max(int(list_limit), 1)]
            )
            if item not in ({}, [], "", None)
        ]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _compact_prompt_number(value)
    text = _compact_prompt_text(value, limit=string_limit)
    return text or None


def _compact_account_for_prompt(account: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "status",
        "captured_at",
        "account_label",
        "cash_krw",
        "settled_cash_krw",
        "orderable_cash_krw",
        "receivable_cash_krw",
        "settlement_cash_krw",
        "next_day_cash_krw",
        "today_sell_amount_krw",
        "position_value_krw",
        "total_value_krw",
        "broker_total_value_krw",
        "computed_total_value_krw",
        "total_value_basis",
        "position_count",
        "stale",
        "error_message",
    )
    compact = {
        key: _compact_prompt_value(account.get(key), string_limit=180)
        for key in keys
        if key in account
    }
    positions: list[dict[str, Any]] = []
    for row in list(account.get("positions") or [])[:12]:
        if not isinstance(row, dict):
            continue
        positions.append(
            {
                key: _compact_prompt_value(row.get(key), string_limit=120)
                for key in (
                    "symbol",
                    "name",
                    "qty",
                    "available_qty",
                    "avg_price",
                    "mark_price",
                    "value_krw",
                    "unrealized_pnl_krw",
                    "unrealized_pnl_pct",
                    "position_weight",
                )
                if key in row
            }
        )
    compact["positions"] = positions
    return {key: value for key, value in compact.items() if value not in ({}, [], "", None)}


def _compact_market_pulse_for_prompt(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "missing"}
    compact: dict[str, Any] = {
        key: _compact_prompt_value(value.get(key), list_limit=3, string_limit=120)
        for key in (
            "status",
            "captured_at",
            "trading_day",
            "regime",
            "score",
            "score_method_version",
            "risk_flags",
            "block_exposure",
        )
        if key in value
    }
    if isinstance(value.get("score_components"), dict):
        compact["score_components"] = {
            key: _compact_prompt_value(row, list_limit=2, string_limit=100)
            for key, row in value["score_components"].items()
            if key
            in {
                "index_score",
                "investor_flow_score",
                "program_score",
                "sector_score",
                "fx_risk_score",
                "total_score",
                "risk_cap",
            }
        }
    if isinstance(value.get("indices"), list):
        compact["indices"] = [
            {
                key: _compact_prompt_value(row.get(key), string_limit=80)
                for key in ("code", "name", "value", "change_pct", "direction", "status")
                if isinstance(row, dict) and key in row
            }
            for row in value["indices"][:4]
            if isinstance(row, dict)
        ]
    if isinstance(value.get("investor_flows"), list):
        compact["investor_flows"] = [
            {
                key: _compact_prompt_value(row.get(key), string_limit=80)
                for key in (
                    "market",
                    "bias",
                    "foreign_net_buy_100m_krw",
                    "institution_net_buy_100m_krw",
                    "foreign_institution_sum_100m_krw",
                    "individual_net_buy_100m_krw",
                    "as_of",
                    "status",
                )
                if isinstance(row, dict) and key in row
            }
            for row in value["investor_flows"][:3]
            if isinstance(row, dict)
        ]
    if isinstance(value.get("program_trading"), list):
        compact["program_trading"] = [
            {
                key: _compact_prompt_value(row.get(key), string_limit=80)
                for key in ("market", "bias", "total_net_buy_100m_krw", "as_of", "status")
                if isinstance(row, dict) and key in row
            }
            for row in value["program_trading"][:2]
            if isinstance(row, dict)
        ]
    if isinstance(value.get("futures"), dict):
        compact["futures"] = {
            key: _compact_prompt_value(value["futures"].get(key), string_limit=80)
            for key in ("basis", "basis_pct", "direction", "status")
            if key in value["futures"]
        }
    if isinstance(value.get("fx"), dict):
        compact["fx"] = {
            key: _compact_prompt_value(value["fx"].get(key), string_limit=80)
            for key in ("usd_krw", "change", "change_pct", "direction", "status")
            if key in value["fx"]
        }
    sectors = value.get("sectors")
    if isinstance(sectors, dict):
        items = sectors.get("items") if isinstance(sectors.get("items"), list) else []
        compact["sectors"] = {
            "items": [
                {
                    key: _compact_prompt_value(row.get(key), list_limit=3, string_limit=90)
                    for key in (
                        "name",
                        "direction",
                        "avg_strength",
                        "signal_count",
                        "positive_count",
                        "negative_count",
                        "symbols",
                    )
                    if isinstance(row, dict) and key in row
                }
                for row in items[:5]
                if isinstance(row, dict)
            ]
        }
    return {key: item for key, item in compact.items() if item not in ({}, [], "", None)}


def _compact_policy_rule_evaluation_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {
        key: _compact_prompt_value(value.get(key), string_limit=80)
        for key in ("status", "active_rule_count", "applied_count")
        if key in value
    }
    global_rows = value.get("global")
    if isinstance(global_rows, list):
        compact["global"] = [
            _compact_prompt_value(
                {
                    key: row.get(key)
                    for key in ("policy_id", "rule_id", "status", "action", "reason")
                    if isinstance(row, dict) and key in row
                },
                list_limit=2,
                string_limit=90,
            )
            for row in global_rows[:2]
            if isinstance(row, dict)
        ]
    by_symbol = value.get("by_symbol")
    if isinstance(by_symbol, dict):
        symbol_rows: dict[str, Any] = {}
        for symbol, rows in list(by_symbol.items())[:5]:
            if not isinstance(rows, list):
                continue
            compact_rows = []
            for row in rows[:1]:
                if not isinstance(row, dict):
                    continue
                compact_rows.append(
                    _compact_prompt_value(
                        {
                            key: row.get(key)
                            for key in (
                                "policy_id",
                                "rule_id",
                                "status",
                                "action",
                                "reason",
                            )
                            if key in row
                        },
                        list_limit=2,
                        string_limit=90,
                    )
                )
            if compact_rows:
                symbol_rows[str(symbol)] = compact_rows
        if symbol_rows:
            compact["by_symbol"] = symbol_rows
    return {key: item for key, item in compact.items() if item not in ({}, [], "", None)}


def _compact_decision_skills_for_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, row in list(value.items())[:6]:
        if not isinstance(row, dict):
            continue
        compact = {
            item_key: _compact_prompt_value(row.get(item_key), string_limit=700)
            for item_key in ("version", "title", "summary", "content_md")
            if item_key in row
        }
        compact = {
            item_key: item_value
            for item_key, item_value in compact.items()
            if item_value not in ({}, [], "", None)
        }
        if compact:
            out[str(key)] = compact
    return out


def _compact_investment_memory_for_prompt(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "missing"}
    compact: dict[str, Any] = {}
    for key in (
        "status",
        "source_scope",
        "target_scope",
        "persona",
        "decision_skill_status",
        "active_policies",
        "policy_rules",
        "policy_scorecards",
        "recent_reflections",
        "block_reflections",
        "lifecycle_artifacts",
        "symbol_analyses",
        "scoped_memory",
        "policy_rule_evaluation",
        "market_pulse",
        "decision_packet_v2",
        "daily_discovery",
        "etf_core",
    ):
        if key not in value:
            continue
        if key == "decision_skills":
            continue
        if key == "policy_rule_evaluation":
            compacted = _compact_policy_rule_evaluation_for_prompt(value.get(key))
        else:
            list_limit = 4 if key not in {"policy_rules", "symbol_analyses"} else 6
            compacted = _compact_prompt_value(
                value.get(key),
                list_limit=list_limit,
                string_limit=500 if key == "persona" else 220,
            )
        if compacted not in ({}, [], "", None):
            compact[key] = compacted
    if "decision_skills" in value:
        skills = _compact_decision_skills_for_prompt(value.get("decision_skills"))
        if skills:
            compact["decision_skills"] = skills
    for key, item in value.items():
        if key in compact or key in {"decision_skills"} or str(key).lower() in _PROMPT_DROP_KEYS:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            compacted = _compact_prompt_value(item, string_limit=180)
            if compacted not in ("", None):
                compact[str(key)] = compacted
    return compact or {"status": "missing"}


def _compact_previous_judgment_for_prompt(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    run = value.get("run") if isinstance(value.get("run"), dict) else value
    compact = {
        key: _compact_prompt_value(run.get(key), string_limit=180)
        for key in ("id", "run_at", "market_session", "status", "mode", "model", "query", "error_message")
        if key in run
    }
    rows = value.get("judgments") if isinstance(value.get("judgments"), list) else []
    if rows:
        compact["judgments"] = [
            _compact_prompt_value(
                {
                    key: row.get(key)
                    for key in (
                        "symbol",
                        "name",
                        "stance",
                        "account_action",
                        "horizon",
                        "confidence",
                        "reasons",
                        "risks",
                        "triggers",
                        "data_gaps",
                    )
                    if isinstance(row, dict) and key in row
                },
                list_limit=2,
                string_limit=90,
            )
            for row in rows[:5]
            if isinstance(row, dict)
        ]
    return {key: item for key, item in compact.items() if item not in ({}, [], "", None)}


def _compact_language_policy_for_prompt() -> dict[str, Any]:
    policy = jue_language_policy()
    return {
        "version": policy.get("version"),
        "user_facing_language": policy.get("user_facing_language"),
        "operator_display_language": policy.get("operator_display_language"),
        "internal_reasoning_language": policy.get("internal_reasoning_language"),
        "user_visible_generation_order": policy.get("user_visible_generation_order"),
        "rule": policy.get("rule"),
        "display_fields": [
            "reasons",
            "risks",
            "triggers",
            "data_gaps",
        ],
    }


def _market_session_for(local: datetime, *, is_open_day: bool) -> str:
    if not is_open_day:
        return "closed"
    current = local.time()
    if time(8, 30) <= current < time(9, 0):
        return "pre_open"
    if time(9, 0) <= current < time(15, 20):
        return "regular"
    if time(15, 20) <= current < time(15, 30):
        return "closing_watch"
    if time(15, 30) <= current < time(16, 0):
        return "post_close_review"
    return "closed"


def build_market_clock(
    *,
    now: datetime | None = None,
    calendar: KRXHolidayCalendar | None = None,
) -> dict[str, Any]:
    local = (now or datetime.now(KST)).astimezone(KST)
    current_date = local.date()
    is_open_day = current_date.weekday() < 5
    if is_open_day and calendar is not None:
        is_open_day = calendar.is_open_day(current_date)
    session = _market_session_for(local, is_open_day=is_open_day)
    next_open = _next_open_datetime(local, calendar=calendar)
    return {
        "status": "ok",
        "timezone": "Asia/Seoul",
        "now": local.isoformat(),
        "date": current_date.isoformat(),
        "is_open_day": is_open_day,
        "session": session,
        "is_market_open": session in {"regular", "closing_watch"},
        "next_open_at": next_open.isoformat() if next_open else "",
    }


def next_krx_decision_due_at(
    clock: dict[str, Any],
    *,
    now: datetime | None = None,
    include_post_close: bool = True,
) -> str:
    local = (now or datetime.now(KST)).astimezone(KST)
    session = str(clock.get("session") or "closed").strip().lower()
    if session in {"pre_open", "regular", "post_close_review"}:
        return local.isoformat()
    if session == "closing_watch" and include_post_close:
        post_close = local.replace(hour=15, minute=30, second=0, microsecond=0)
        return (post_close if post_close > local else local).isoformat()

    next_open = _parse_clock_datetime(clock.get("next_open_at"))
    if next_open is None:
        return ""
    next_open_local = next_open.astimezone(KST)
    pre_open = next_open_local.replace(hour=8, minute=30, second=0, microsecond=0)
    if pre_open > local:
        return pre_open.isoformat()
    if next_open_local > local:
        return next_open_local.isoformat()
    return ""


def _parse_clock_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed


def _next_open_datetime(
    local: datetime,
    *,
    calendar: KRXHolidayCalendar | None = None,
) -> datetime | None:
    open_today = local.replace(hour=9, minute=0, second=0, microsecond=0)
    probe = local.date()
    if local < open_today and _is_open_date(probe, calendar=calendar):
        return open_today
    for _ in range(14):
        probe = probe + timedelta(days=1)
        if _is_open_date(probe, calendar=calendar):
            return datetime.combine(probe, time(9, 0), tzinfo=KST)
    return None


def _is_open_date(value: date, *, calendar: KRXHolidayCalendar | None = None) -> bool:
    if value.weekday() >= 5:
        return False
    if calendar is None:
        return True
    return calendar.is_open_day(value)


def normalize_account_assets(assets: list[dict[str, Any]]) -> dict[str, Any]:
    cash_krw = 0.0
    settled_cash_krw = 0.0
    orderable_cash_krw = 0.0
    receivable_cash_krw = 0.0
    settlement_cash_krw = 0.0
    next_day_cash_krw = 0.0
    today_sell_amount_krw = 0.0
    today_fee_tax_krw = 0.0
    broker_total_value_krw = 0.0
    positions: list[dict[str, Any]] = []
    for row in assets:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        symbol = str(row.get("asset") or "").strip()
        value_krw = _safe_float(row.get("value_krw"))
        if kind == "cash" and symbol.upper() == "KRW":
            cash_value = max(value_krw or _safe_float(row.get("qty")), 0.0)
            cash_krw += cash_value
            settled_cash_krw += max(
                _safe_float(row.get("settled_cash_krw")) or cash_value,
                0.0,
            )
            orderable_cash_krw += max(
                _safe_float(row.get("orderable_cash_krw"))
                or _safe_float(row.get("available"))
                or cash_value,
                0.0,
            )
            receivable_cash_krw += max(_safe_float(row.get("receivable_cash_krw")), 0.0)
            settlement_cash_krw += max(_safe_float(row.get("settlement_cash_krw")), 0.0)
            next_day_cash_krw += max(_safe_float(row.get("next_day_cash_krw")), 0.0)
            today_sell_amount_krw += max(_safe_float(row.get("today_sell_amount_krw")), 0.0)
            today_fee_tax_krw += max(_safe_float(row.get("today_fee_tax_krw")), 0.0)
            broker_total_value_krw += max(
                _safe_float(row.get("net_asset_krw"))
                or _safe_float(row.get("total_value_krw")),
                0.0,
            )
            continue
        if kind != "position" or not _is_symbol(symbol):
            continue
        qty = _safe_float(row.get("qty"))
        available_qty = _safe_float(row.get("available") or qty)
        avg_price = _safe_float(row.get("avg_price"))
        mark_price = _safe_float(row.get("mark_price"))
        pnl_krw = _safe_float(row.get("pnl_krw"))
        cost_krw = avg_price * qty if avg_price > 0 and qty > 0 else value_krw - pnl_krw
        pnl_pct = (pnl_krw / cost_krw * 100.0) if cost_krw > 0 else 0.0
        positions.append(
            {
                "symbol": symbol,
                "name": str(row.get("asset_name") or symbol),
                "qty": qty,
                "available_qty": available_qty,
                "avg_price": avg_price,
                "mark_price": mark_price,
                "value_krw": value_krw,
                "unrealized_pnl_krw": pnl_krw,
                "unrealized_pnl_pct": pnl_pct,
            }
        )
    position_value_krw = sum(float(row.get("value_krw") or 0.0) for row in positions)
    computed_total_value_krw = cash_krw + position_value_krw
    total_value_krw = (
        broker_total_value_krw
        if broker_total_value_krw > 0
        else computed_total_value_krw
    )
    for row in positions:
        value_krw = float(row.get("value_krw") or 0.0)
        row["position_weight"] = value_krw / total_value_krw if total_value_krw > 0 else 0.0
    return {
        "status": "ok",
        "captured_at": utc_now_iso(),
        "account_label": "국장1",
        "cash_krw": cash_krw,
        "settled_cash_krw": settled_cash_krw,
        "orderable_cash_krw": orderable_cash_krw,
        "receivable_cash_krw": receivable_cash_krw,
        "settlement_cash_krw": settlement_cash_krw,
        "next_day_cash_krw": next_day_cash_krw,
        "today_sell_amount_krw": today_sell_amount_krw,
        "today_fee_tax_krw": today_fee_tax_krw,
        "position_value_krw": position_value_krw,
        "total_value_krw": total_value_krw,
        "broker_total_value_krw": broker_total_value_krw,
        "computed_total_value_krw": computed_total_value_krw,
        "total_value_basis": (
            "broker_net_asset"
            if broker_total_value_krw > 0
            else "cash_plus_positions"
        ),
        "position_count": len(positions),
        "positions": sorted(
            positions,
            key=lambda row: float(row.get("value_krw") or 0.0),
            reverse=True,
        ),
    }


class MarketJudgmentRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS quote_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price REAL,
                    change REAL,
                    change_pct REAL,
                    open_price REAL,
                    high_price REAL,
                    low_price REAL,
                    volume REAL,
                    trading_value REAL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_quote_symbol_time
                    ON quote_snapshots(symbol, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cash_krw REAL,
                    position_value_krw REAL,
                    total_value_krw REAL,
                    position_count INTEGER,
                    error_message TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS judgment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    market_session TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL,
                    query TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    source_snapshot_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS symbol_judgments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    stance TEXT NOT NULL,
                    account_action TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    confidence REAL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    triggers_json TEXT NOT NULL DEFAULT '[]',
                    data_gaps_json TEXT NOT NULL DEFAULT '[]',
                    quote_json TEXT NOT NULL DEFAULT '{}',
                    position_json TEXT NOT NULL DEFAULT '{}',
                    strategy_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(run_id) REFERENCES judgment_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_symbol_judgments_run
                    ON symbol_judgments(run_id, symbol);
                """
            )

    def save_quotes(self, quotes: list[dict[str, Any]]) -> None:
        if not quotes:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO quote_snapshots (
                    symbol, name, price, change, change_pct, open_price, high_price,
                    low_price, volume, trading_value, source, fetched_at, status,
                    error_message, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row.get("symbol") or ""),
                        str(row.get("name") or row.get("symbol") or ""),
                        row.get("price"),
                        row.get("change"),
                        row.get("change_pct"),
                        row.get("open_price"),
                        row.get("high_price"),
                        row.get("low_price"),
                        row.get("volume"),
                        row.get("trading_value"),
                        str(row.get("source") or ""),
                        str(row.get("fetched_at") or utc_now_iso()),
                        str(row.get("status") or "ok"),
                        str(row.get("error_message") or ""),
                        _json_dumps(_compact_quote_raw_for_storage(row.get("raw") or {})),
                    )
                    for row in quotes
                ],
            )

    def prune_history(
        self,
        *,
        retention_days: int = 7,
        quote_archive_retention_days: int | None = None,
        account_retention_days: int | None = None,
        judgment_retention_days: int | None = 30,
        judgment_archive_retention_days: int | None = None,
        compact_recent_run_count: int = 96,
        compact_min_chars: int = 20_000,
        compact_symbol_min_chars: int = 2_000,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if int(retention_days) <= 0:
            return {"status": "skipped", "reason": "retention_disabled"}
        base_now = now or datetime.now(timezone.utc)
        cutoff = (base_now - timedelta(days=int(retention_days))).isoformat()
        account_days = (
            int(account_retention_days)
            if account_retention_days is not None
            else int(retention_days)
        )
        judgment_days = int(judgment_retention_days or 0)
        with self._connect() as conn:
            quote_archived = self._archive_before_cutoff(
                conn,
                table="quote_snapshots",
                archive_table="quote_snapshots_archive",
                timestamp_column="fetched_at",
                cutoff=cutoff,
                compress_columns=("raw_json",),
            )
            quote_deleted = int(
                conn.execute(
                    "DELETE FROM quote_snapshots WHERE fetched_at < ?",
                    (cutoff,),
                ).rowcount
                or 0
            )
            quote_archive_deleted = 0
            if quote_archive_retention_days is not None and int(
                quote_archive_retention_days
            ) > 0:
                quote_archive_cutoff = (
                    base_now - timedelta(days=int(quote_archive_retention_days))
                ).isoformat()
                quote_archive_deleted = self._delete_archive_before_cutoff(
                    conn,
                    table="quote_snapshots_archive",
                    timestamp_column="fetched_at",
                    cutoff=quote_archive_cutoff,
                )
            account_deleted = 0
            account_archived = 0
            if account_days > 0:
                account_cutoff = (base_now - timedelta(days=account_days)).isoformat()
                account_archived = self._archive_before_cutoff(
                    conn,
                    table="account_snapshots",
                    archive_table="account_snapshots_archive",
                    timestamp_column="captured_at",
                    cutoff=account_cutoff,
                    compress_columns=("raw_json",),
                )
                account_deleted = int(
                    conn.execute(
                        "DELETE FROM account_snapshots WHERE captured_at < ?",
                        (account_cutoff,),
                    ).rowcount
                    or 0
                )
            symbol_judgments_deleted = 0
            judgment_runs_deleted = 0
            symbol_judgments_archived = 0
            judgment_runs_archived = 0
            judgment_runs_archive_deleted = 0
            symbol_judgments_archive_deleted = 0
            if judgment_days > 0:
                judgment_cutoff = (
                    base_now - timedelta(days=judgment_days)
                ).isoformat()
                old_run_ids = [
                    int(row[0])
                    for row in conn.execute(
                        "SELECT id FROM judgment_runs WHERE run_at < ?",
                        (judgment_cutoff,),
                    ).fetchall()
                ]
                if old_run_ids:
                    placeholders = ",".join("?" for _ in old_run_ids)
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS symbol_judgments_archive AS "
                        "SELECT * FROM symbol_judgments WHERE 0"
                    )
                    symbol_judgments_archived = self._archive_by_ids(
                        conn,
                        table="symbol_judgments",
                        archive_table="symbol_judgments_archive",
                        id_column="run_id",
                        ids=old_run_ids,
                        compress_columns=(
                            "reasons_json",
                            "risks_json",
                            "triggers_json",
                            "data_gaps_json",
                            "quote_json",
                            "position_json",
                            "strategy_json",
                        ),
                    )
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS judgment_runs_archive AS "
                        "SELECT * FROM judgment_runs WHERE 0"
                    )
                    judgment_runs_archived = self._archive_by_ids(
                        conn,
                        table="judgment_runs",
                        archive_table="judgment_runs_archive",
                        id_column="id",
                        ids=old_run_ids,
                        compress_columns=(
                            "prompt_json",
                            "response_json",
                            "source_snapshot_json",
                        ),
                    )
                    symbol_judgments_deleted = int(
                        conn.execute(
                            f"DELETE FROM symbol_judgments WHERE run_id IN ({placeholders})",
                            old_run_ids,
                        ).rowcount
                        or 0
                    )
                    judgment_runs_deleted = int(
                        conn.execute(
                            f"DELETE FROM judgment_runs WHERE id IN ({placeholders})",
                            old_run_ids,
                        ).rowcount
                        or 0
                    )
                if (
                    judgment_archive_retention_days is not None
                    and int(judgment_archive_retention_days) > 0
                ):
                    judgment_archive_cutoff = (
                        base_now
                        - timedelta(
                            days=judgment_days
                            + int(judgment_archive_retention_days)
                        )
                    ).isoformat()
                    (
                        judgment_runs_archive_deleted,
                        symbol_judgments_archive_deleted,
                    ) = self._delete_judgment_archives_before_cutoff(
                        conn,
                        cutoff=judgment_archive_cutoff,
                    )
            judgment_runs_compacted = self._compact_old_judgment_run_payloads(
                conn,
                recent_run_count=compact_recent_run_count,
                min_chars=compact_min_chars,
                compacted_at=base_now.isoformat(),
            )
            symbol_judgments_compacted = self._compact_old_symbol_judgment_payloads(
                conn,
                recent_run_count=compact_recent_run_count,
                min_chars=compact_symbol_min_chars,
                compacted_at=base_now.isoformat(),
            )
        vacuumed = False
        if any(
            value > 0
            for value in (
                quote_deleted,
                quote_archive_deleted,
                account_deleted,
                judgment_runs_deleted,
                symbol_judgments_deleted,
                judgment_runs_archive_deleted,
                symbol_judgments_archive_deleted,
                judgment_runs_compacted,
                symbol_judgments_compacted,
            )
        ):
            with sqlite3.connect(str(self.path), isolation_level=None) as conn:
                conn.execute("VACUUM")
            vacuumed = True
        return {
            "status": "ok",
            "cutoff": cutoff,
            "quote_snapshots_deleted": quote_deleted,
            "account_snapshots_deleted": account_deleted,
            "judgment_runs_deleted": judgment_runs_deleted,
            "symbol_judgments_deleted": symbol_judgments_deleted,
            "archive_deleted": {
                "quote_snapshots_archive": quote_archive_deleted,
                "judgment_runs_archive": judgment_runs_archive_deleted,
                "symbol_judgments_archive": symbol_judgments_archive_deleted,
            },
            "archived": {
                "quote_snapshots": quote_archived,
                "account_snapshots": account_archived,
                "judgment_runs": judgment_runs_archived,
                "symbol_judgments": symbol_judgments_archived,
            },
            "compacted": {
                "judgment_runs": judgment_runs_compacted,
                "symbol_judgments": symbol_judgments_compacted,
                "recent_run_count": max(int(compact_recent_run_count), 0),
                "min_chars": max(int(compact_min_chars), 0),
                "symbol_min_chars": max(int(compact_symbol_min_chars), 0),
            },
            "vacuumed": vacuumed,
        }

    @staticmethod
    def _recent_judgment_run_ids(
        conn: sqlite3.Connection,
        *,
        keep_count: int,
    ) -> list[int]:
        return [
            int(row[0])
            for row in conn.execute(
                """
                SELECT id FROM judgment_runs
                ORDER BY run_at DESC, id DESC
                LIMIT ?
                """,
                (max(int(keep_count), 0),),
            ).fetchall()
        ]

    @staticmethod
    def _compact_old_judgment_run_payloads(
        conn: sqlite3.Connection,
        *,
        recent_run_count: int,
        min_chars: int,
        compacted_at: str,
    ) -> int:
        keep_count = max(int(recent_run_count), 0)
        threshold = max(int(min_chars), 0)
        keep_ids = MarketJudgmentRepository._recent_judgment_run_ids(
            conn,
            keep_count=keep_count,
        )
        params: list[Any] = [threshold]
        keep_filter = ""
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            keep_filter = f"AND id NOT IN ({placeholders})"
            params.extend(keep_ids)
        rows = conn.execute(
            f"""
            SELECT id, run_at, mode, model, status,
                   length(prompt_json), length(response_json), length(source_snapshot_json)
            FROM judgment_runs
            WHERE (
                length(prompt_json) + length(response_json) + length(source_snapshot_json)
            ) > ?
              {keep_filter}
            """,
            tuple(params),
        ).fetchall()
        compacted = 0
        for row in rows:
            original_chars = {
                "prompt_json": int(row[5] or 0),
                "response_json": int(row[6] or 0),
                "source_snapshot_json": int(row[7] or 0),
            }
            base_marker = {
                "compacted": True,
                "compacted_at": compacted_at,
                "reason": "market_judgment_run_payload_retention",
                "run_id": int(row[0]),
                "run_at": str(row[1] or ""),
                "mode": str(row[2] or ""),
                "model": str(row[3] or ""),
                "status": str(row[4] or ""),
                "recent_run_count": keep_count,
                "original_chars": original_chars,
            }
            conn.execute(
                """
                UPDATE judgment_runs
                SET prompt_json = ?, response_json = ?, source_snapshot_json = ?
                WHERE id = ?
                """,
                (
                    _json_dumps({**base_marker, "field": "prompt_json"}),
                    _json_dumps({**base_marker, "field": "response_json"}),
                    _json_dumps({**base_marker, "field": "source_snapshot_json"}),
                    int(row[0]),
                ),
            )
            compacted += 1
        return compacted

    @staticmethod
    def _compact_old_symbol_judgment_payloads(
        conn: sqlite3.Connection,
        *,
        recent_run_count: int,
        min_chars: int,
        compacted_at: str,
    ) -> int:
        keep_count = max(int(recent_run_count), 0)
        threshold = max(int(min_chars), 0)
        keep_ids = MarketJudgmentRepository._recent_judgment_run_ids(
            conn,
            keep_count=keep_count,
        )
        params: list[Any] = [
            threshold,
            "%market_judgment_symbol_payload_retention%",
            "%market_judgment_symbol_payload_retention%",
            "%market_judgment_symbol_payload_retention%",
        ]
        keep_filter = ""
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            keep_filter = f"AND run_id NOT IN ({placeholders})"
            params.extend(keep_ids)
        rows = conn.execute(
            f"""
            SELECT id, run_id, symbol, name,
                   length(quote_json), length(position_json), length(strategy_json)
            FROM symbol_judgments
            WHERE (
                length(quote_json) + length(position_json) + length(strategy_json)
            ) > ?
              AND NOT (
                quote_json LIKE ?
                AND position_json LIKE ?
                AND strategy_json LIKE ?
              )
              {keep_filter}
            """,
            tuple(params),
        ).fetchall()
        compacted = 0
        for row in rows:
            original_chars = {
                "quote_json": int(row[4] or 0),
                "position_json": int(row[5] or 0),
                "strategy_json": int(row[6] or 0),
            }
            base_marker = {
                "compacted": True,
                "compacted_at": compacted_at,
                "reason": "market_judgment_symbol_payload_retention",
                "symbol_judgment_id": int(row[0]),
                "run_id": int(row[1]),
                "symbol": str(row[2] or ""),
                "name": str(row[3] or ""),
                "recent_run_count": keep_count,
                "original_chars": original_chars,
            }
            conn.execute(
                """
                UPDATE symbol_judgments
                SET quote_json = ?, position_json = ?, strategy_json = ?
                WHERE id = ?
                """,
                (
                    _json_dumps({**base_marker, "field": "quote_json"}),
                    _json_dumps({**base_marker, "field": "position_json"}),
                    _json_dumps({**base_marker, "field": "strategy_json"}),
                    int(row[0]),
                ),
            )
            compacted += 1
        return compacted

    @staticmethod
    def _delete_judgment_archives_before_cutoff(
        conn: sqlite3.Connection,
        *,
        cutoff: str,
    ) -> tuple[int, int]:
        runs_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("judgment_runs_archive",),
        ).fetchone()
        if not runs_exists:
            return 0, 0
        old_run_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM judgment_runs_archive WHERE run_at < ?",
                (cutoff,),
            ).fetchall()
        ]
        if not old_run_ids:
            return 0, 0
        placeholders = ",".join("?" for _ in old_run_ids)
        symbol_deleted = 0
        symbols_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("symbol_judgments_archive",),
        ).fetchone()
        if symbols_exists:
            symbol_deleted = int(
                conn.execute(
                    f"DELETE FROM symbol_judgments_archive WHERE run_id IN ({placeholders})",
                    old_run_ids,
                ).rowcount
                or 0
            )
        run_deleted = int(
            conn.execute(
                f"DELETE FROM judgment_runs_archive WHERE id IN ({placeholders})",
                old_run_ids,
            ).rowcount
            or 0
        )
        return run_deleted, symbol_deleted

    @staticmethod
    def _delete_archive_before_cutoff(
        conn: sqlite3.Connection,
        *,
        table: str,
        timestamp_column: str,
        cutoff: str,
    ) -> int:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if not exists:
            return 0
        return int(
            conn.execute(
                f"DELETE FROM {table} WHERE {timestamp_column} < ?",
                (cutoff,),
            ).rowcount
            or 0
        )

    @staticmethod
    def _archive_before_cutoff(
        conn: sqlite3.Connection,
        *,
        table: str,
        archive_table: str,
        timestamp_column: str,
        cutoff: str,
        compress_columns: tuple[str, ...] = (),
    ) -> int:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {archive_table} AS "
            f"SELECT * FROM {table} WHERE 0"
        )
        if compress_columns:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {timestamp_column} < ?",
                (cutoff,),
            ).fetchall()
            if not rows:
                return 0
            columns = [
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            compress_indexes = {columns.index(column) for column in compress_columns}
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(columns)
            archived = 0
            for row in rows:
                values = list(row)
                for index in compress_indexes:
                    values[index] = gzip_base64_archive_text(values[index])
                conn.execute(
                    f"INSERT INTO {archive_table} ({column_sql}) VALUES ({placeholders})",
                    values,
                )
                archived += 1
            return archived
        return int(
            conn.execute(
                f"INSERT INTO {archive_table} "
                f"SELECT * FROM {table} WHERE {timestamp_column} < ?",
                (cutoff,),
            ).rowcount
            or 0
        )

    @staticmethod
    def _archive_by_ids(
        conn: sqlite3.Connection,
        *,
        table: str,
        archive_table: str,
        id_column: str,
        ids: list[int],
        compress_columns: tuple[str, ...] = (),
    ) -> int:
        if not ids:
            return 0
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {archive_table} AS "
            f"SELECT * FROM {table} WHERE 0"
        )
        placeholders_for_ids = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {id_column} IN ({placeholders_for_ids})",
            ids,
        ).fetchall()
        if not rows:
            return 0
        columns = [
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        compress_indexes = {
            columns.index(column) for column in compress_columns if column in columns
        }
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        archived = 0
        for row in rows:
            values = list(row)
            for index in compress_indexes:
                values[index] = gzip_base64_archive_text(values[index])
            conn.execute(
                f"INSERT INTO {archive_table} ({column_sql}) VALUES ({placeholders})",
                values,
            )
            archived += 1
        return archived

    def save_account(self, account: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_snapshots (
                    captured_at, status, cash_krw, position_value_krw,
                    total_value_krw, position_count, error_message, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(account.get("captured_at") or utc_now_iso()),
                    str(account.get("status") or "ok"),
                    account.get("cash_krw"),
                    account.get("position_value_krw"),
                    account.get("total_value_krw"),
                    int(account.get("position_count") or 0),
                    str(account.get("error_message") or ""),
                    _json_dumps(account),
                ),
            )

    def save_judgment_run(
        self,
        *,
        run: dict[str, Any],
        judgments: list[dict[str, Any]],
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO judgment_runs (
                    run_at, market_session, status, mode, model, query,
                    error_message, prompt_json, response_json, source_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.get("run_at") or utc_now_iso()),
                    str(run.get("market_session") or "closed"),
                    str(run.get("status") or "ok"),
                    str(run.get("mode") or "deterministic"),
                    str(run.get("model") or ""),
                    str(run.get("query") or ""),
                    str(run.get("error_message") or ""),
                    _json_dumps(run.get("prompt") or {}),
                    _json_dumps(run.get("response") or {}),
                    _json_dumps(run.get("source_snapshot") or {}),
                ),
            )
            run_id = int(cursor.lastrowid)
            conn.executemany(
                """
                INSERT INTO symbol_judgments (
                    run_id, symbol, name, stance, account_action, horizon,
                    confidence, reasons_json, risks_json, triggers_json,
                    data_gaps_json, quote_json, position_json, strategy_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        str(row.get("symbol") or ""),
                        str(row.get("name") or row.get("symbol") or ""),
                        str(row.get("stance") or "confirm"),
                        str(row.get("account_action") or "hold"),
                        str(row.get("horizon") or "unknown"),
                        float(row.get("confidence") or 0.0),
                        _json_dumps(row.get("reasons") or []),
                        _json_dumps(row.get("risks") or []),
                        _json_dumps(row.get("triggers") or []),
                        _json_dumps(row.get("data_gaps") or []),
                        _json_dumps(row.get("quote") or {}),
                        _json_dumps(row.get("position") or {}),
                        _json_dumps(row.get("strategy") or {}),
                    )
                    for row in judgments
                ],
            )
            return run_id

    def latest_quotes(
        self,
        *,
        limit: int = 100,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        requested_symbols = [
            str(symbol or "").strip()
            for symbol in list(symbols or [])
            if _is_symbol(symbol)
        ]
        requested_symbols = list(dict.fromkeys(requested_symbols))
        with self._connect() as conn:
            params: list[Any] = []
            symbol_filter = ""
            if requested_symbols:
                placeholders = ",".join("?" for _ in requested_symbols)
                symbol_filter = f"WHERE symbol IN ({placeholders})"
                params.extend(requested_symbols)
            params.append(max(int(limit), 1))
            rows = conn.execute(
                f"""
                SELECT q.*
                FROM quote_snapshots q
                JOIN (
                    SELECT symbol, MAX(fetched_at) AS fetched_at
                    FROM quote_snapshots
                    {symbol_filter}
                    GROUP BY symbol
                ) latest
                ON latest.symbol = q.symbol AND latest.fetched_at = q.fetched_at
                ORDER BY q.fetched_at DESC, q.symbol ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return {
            "status": "ok",
            "count": len(rows),
            "symbols": requested_symbols,
            "items": [self._row_to_quote(row) for row in rows],
        }

    def latest_account(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_snapshots ORDER BY captured_at DESC, id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {"status": "missing", "account_label": "국장1", "positions": []}
        payload = _json_loads(row["raw_json"], {})
        return payload if isinstance(payload, dict) else {"status": "invalid"}

    def latest_successful_account(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM account_snapshots
                WHERE status IN ('ok', 'stale')
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {"status": "missing", "account_label": "국장1", "positions": []}
        payload = _json_loads(row["raw_json"], {})
        return payload if isinstance(payload, dict) else {"status": "invalid"}

    def latest_judgment(self) -> dict[str, Any]:
        with self._connect() as conn:
            run = conn.execute(
                "SELECT * FROM judgment_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if run is None:
                return {"status": "missing", "judgments": []}
            rows = conn.execute(
                "SELECT * FROM symbol_judgments WHERE run_id = ? ORDER BY id ASC",
                (int(run["id"]),),
            ).fetchall()
        return {
            "status": str(run["status"] or "ok"),
            "run": self._row_to_run(run),
            "judgments": [self._row_to_judgment(row) for row in rows],
        }

    def latest_llm_run_at(self) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_at FROM judgment_runs
                WHERE mode = 'llm'
                ORDER BY run_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return _parse_iso_datetime(row["run_at"])

    def has_llm_run_for_session_date(self, *, session: str, trading_day: str) -> bool:
        target_session = str(session or "").strip()
        target_day = str(trading_day or "").strip()
        if not target_session or not target_day:
            return False
        try:
            local_day = date.fromisoformat(target_day)
        except ValueError:
            return False
        start_utc = datetime.combine(local_day, time(0, 0), tzinfo=KST).astimezone(timezone.utc)
        end_utc = start_utc + timedelta(days=1)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM judgment_runs
                WHERE mode = 'llm'
                  AND market_session = ?
                  AND run_at >= ?
                  AND run_at < ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_session, start_utc.isoformat(), end_utc.isoformat()),
            ).fetchone()
        return row is not None

    def recent_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM judgment_runs
                ORDER BY run_at DESC, id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            quote_count = int(conn.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0])
            account_count = int(conn.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0])
            run_count = int(conn.execute("SELECT COUNT(*) FROM judgment_runs").fetchone()[0])
            latest_run = conn.execute(
                "SELECT run_at, status, mode FROM judgment_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return {
            "status": "ok",
            "db_path": str(self.path),
            "quote_count": quote_count,
            "account_count": account_count,
            "run_count": run_count,
            "latest_run_at": str(latest_run["run_at"]) if latest_run else "",
            "latest_run_status": str(latest_run["status"]) if latest_run else "missing",
            "latest_run_mode": str(latest_run["mode"]) if latest_run else "",
        }

    @staticmethod
    def _row_to_quote(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": row["symbol"],
            "name": row["name"],
            "price": row["price"],
            "change": row["change"],
            "change_pct": row["change_pct"],
            "open_price": row["open_price"],
            "high_price": row["high_price"],
            "low_price": row["low_price"],
            "volume": row["volume"],
            "trading_value": row["trading_value"],
            "source": row["source"],
            "fetched_at": row["fetched_at"],
            "status": row["status"],
            "error_message": row["error_message"],
            "raw": _json_loads(row["raw_json"], {}),
        }

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_at": row["run_at"],
            "market_session": row["market_session"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "query": row["query"],
            "error_message": row["error_message"],
            "source_snapshot": _json_loads(row["source_snapshot_json"], {}),
        }

    @staticmethod
    def _row_to_judgment(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": row["symbol"],
            "name": row["name"],
            "stance": row["stance"],
            "account_action": row["account_action"],
            "horizon": row["horizon"],
            "confidence": row["confidence"],
            "reasons": _json_loads(row["reasons_json"], []),
            "risks": _json_loads(row["risks_json"], []),
            "triggers": _json_loads(row["triggers_json"], []),
            "data_gaps": _json_loads(row["data_gaps_json"], []),
            "quote": _json_loads(row["quote_json"], {}),
            "position": _json_loads(row["position_json"], {}),
            "strategy": _json_loads(row["strategy_json"], {}),
        }


class MarketQuoteService:
    def __init__(
        self,
        kis: KISAdapter,
        *,
        use_naver_fallback: bool = False,
        timeout_sec: float = 8.0,
    ) -> None:
        self.kis = kis
        self.use_naver_fallback = use_naver_fallback
        self.timeout_sec = max(float(timeout_sec), 1.0)

    async def collect_quotes(
        self,
        symbols: list[str],
        *,
        concurrency: int = 4,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(max(int(concurrency), 1))

        async def fetch(symbol: str) -> dict[str, Any]:
            async with semaphore:
                return await self.fetch_quote(symbol)

        tasks = [fetch(symbol) for symbol in symbols if _is_symbol(symbol)]
        if not tasks:
            return []
        return list(await asyncio.gather(*tasks))

    async def fetch_quote(self, symbol: str) -> dict[str, Any]:
        code = str(symbol or "").strip()
        fetched_at = utc_now_iso()
        try:
            payload = await self.kis.fetch_domestic_quote(code)
            return normalize_kis_quote(payload, fetched_at=fetched_at)
        except Exception as exc:
            kis_error = str(exc)
            if self.use_naver_fallback:
                try:
                    quote = await self._fetch_naver_quote(code)
                    quote["fallback_reason"] = kis_error
                    return quote
                except Exception as fallback_exc:
                    return {
                        "symbol": code,
                        "name": code,
                        "price": None,
                        "change": None,
                        "change_pct": None,
                        "source": "kis+naver",
                        "fetched_at": fetched_at,
                        "status": "error",
                        "error_message": f"{kis_error}; fallback:{fallback_exc}",
                        "raw": {},
                    }
            return {
                "symbol": code,
                "name": code,
                "price": None,
                "change": None,
                "change_pct": None,
                "source": "kis",
                "fetched_at": fetched_at,
                "status": "error",
                "error_message": kis_error,
                "raw": {},
            }

    async def _fetch_naver_quote(self, symbol: str) -> dict[str, Any]:
        code = str(symbol or "").strip()
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
        timeout = httpx.Timeout(self.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        response.encoding = response.encoding or "euc-kr"
        return parse_naver_quote_html(
            response.text,
            symbol=code,
            source_url=url,
            fetched_at=utc_now_iso(),
        )


def normalize_kis_quote(payload: dict[str, Any], *, fetched_at: str | None = None) -> dict[str, Any]:
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    price = _safe_float(payload.get("price") or raw.get("stck_prpr"))
    change = _safe_float(raw.get("prdy_vrss") or raw.get("prdy_vrss_sign"))
    sign = str(raw.get("prdy_vrss_sign") or "").strip()
    if sign in {"5", "-"} and change > 0:
        change = -change
    change_pct = _safe_float(raw.get("prdy_ctrt"))
    if change < 0 and change_pct > 0:
        change_pct = -change_pct
    return {
        "symbol": str(payload.get("symbol") or raw.get("stck_shrn_iscd") or ""),
        "name": str(payload.get("name") or raw.get("hts_kor_isnm") or payload.get("symbol") or ""),
        "price": price if price > 0 else None,
        "change": change,
        "change_pct": change_pct,
        "open_price": _safe_float(raw.get("stck_oprc")) or None,
        "high_price": _safe_float(raw.get("stck_hgpr")) or None,
        "low_price": _safe_float(raw.get("stck_lwpr")) or None,
        "volume": _safe_float(raw.get("acml_vol")) or None,
        "trading_value": _safe_float(raw.get("acml_tr_pbmn")) or None,
        "source": "kis",
        "fetched_at": fetched_at or utc_now_iso(),
        "status": "ok",
        "error_message": "",
        "raw": raw,
    }


def parse_naver_quote_html(
    raw_html: str,
    *,
    symbol: str,
    source_url: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    text = str(raw_html or "")
    title_match = re.search(r"<title>\s*([^:<]+)", text, re.IGNORECASE)
    name = _clean_text(title_match.group(1) if title_match else symbol, limit=60)
    price = _first_number(
        [
            r'class="no_today"[\s\S]{0,500}?<span class="blind">([0-9,]+)</span>',
            r"현재가\s*([0-9,]+)",
        ],
        text,
    )
    change_pct = _first_number(
        [
            r'class="no_exday"[\s\S]{0,900}?<span class="blind">([+-]?[0-9.]+)%?</span>',
            r"등락률\s*([+-]?[0-9.]+)",
        ],
        text,
    )
    volume = _first_number([r'거래량</span>[\s\S]{0,300}?<span class="blind">([0-9,]+)</span>'], text)
    if price <= 0:
        raise ValueError("naver quote price missing")
    return {
        "symbol": symbol,
        "name": name or symbol,
        "price": price,
        "change": None,
        "change_pct": change_pct if change_pct else None,
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "volume": volume if volume else None,
        "trading_value": None,
        "source": "naver",
        "source_url": source_url,
        "fetched_at": fetched_at or utc_now_iso(),
        "status": "ok",
        "error_message": "",
        "raw": {"source_url": source_url},
    }


def _first_number(patterns: list[str], text: str) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _safe_float(match.group(1))
    return 0.0


def _is_usable_symbol_display_name(symbol: str, name: Any) -> bool:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text or text == str(symbol or "").strip():
        return False
    lowered = text.lower().strip(" ._-")
    compact = re.sub(r"[\s._-]+", "", lowered)
    if lowered in _DISPLAY_NAME_DENYLIST or compact in _DISPLAY_NAME_DENYLIST:
        return False
    if any(marker in lowered for marker in ("www.", ".com", "목표주가", "리포트")):
        return False
    if re.fullmatch(r"[A-Za-z]{1,4}", text) and text.upper() not in _DISPLAY_NAME_ALLOWLIST:
        return False
    if len(text) > 40:
        return False
    return True


class MarketJudgmentEngine:
    def __init__(
        self,
        *,
        config: MarketJudgmentConfig,
        kis: KISAdapter,
        codex_runtime: CodexNativeRuntime,
        strategy_engine: StrategyEngine,
        report_repository: ReportRepository | None = None,
        fundamentals_repository: FundamentalsRepository | None = None,
        rag_store: RAGQueryStore | None = None,
        research_feed_provider: ResearchFeedProvider | None = None,
        market_pulse_provider: MarketPulseProvider | None = None,
        memory_context_provider: MemoryContextProvider | None = None,
        wiki_context_provider: Callable[..., dict[str, Any]] | None = None,
        opportunity_provider: OpportunityProvider | None = None,
        calendar: KRXHolidayCalendar | None = None,
        watchlist: list[str] | None = None,
    ) -> None:
        self.config = config
        self.kis = kis
        self.codex_runtime = codex_runtime
        self.strategy_engine = strategy_engine
        self.report_repository = report_repository
        self.fundamentals_repository = fundamentals_repository
        self.rag_store = rag_store
        self.research_feed_provider = research_feed_provider
        self.market_pulse_provider = market_pulse_provider
        self.memory_context_provider = memory_context_provider
        self.wiki_context_provider = wiki_context_provider
        self.opportunity_provider = opportunity_provider
        self.calendar = calendar or KRXHolidayCalendar()
        self.watchlist = [symbol for symbol in list(watchlist or []) if _is_symbol(symbol)]
        self.repository = MarketJudgmentRepository(config.db_path)
        self._last_opportunity_scan: dict[str, Any] = {"status": "not_scanned"}
        self._last_strategy_payload: dict[str, Any] | None = None
        self.quote_service = MarketQuoteService(
            kis,
            use_naver_fallback=config.use_naver_fallback,
            timeout_sec=config.request_timeout_sec,
        )

    def clock(self, *, now: datetime | None = None) -> dict[str, Any]:
        return build_market_clock(now=now, calendar=self.calendar)

    def status(self) -> dict[str, Any]:
        return {
            **self.repository.status(),
            "candidate_coverage": self._candidate_coverage(),
            "config": {
                "quote_interval_sec": int(self.config.quote_interval_sec),
                "judge_interval_sec": int(self.config.judge_interval_sec),
                "max_symbols": int(self.config.max_symbols),
                "llm_max_symbols": int(self.config.llm_max_symbols),
                "use_naver_fallback": bool(self.config.use_naver_fallback),
            },
        }

    def latest_llm_run_at(self) -> datetime | None:
        return self.repository.latest_llm_run_at()

    def has_llm_run_for_session_date(self, *, session: str, trading_day: str) -> bool:
        return self.repository.has_llm_run_for_session_date(
            session=session,
            trading_day=trading_day,
        )

    def schedule(self, *, last_judged_at: datetime | None = None) -> dict[str, Any]:
        clock = self.clock()
        latest_llm = last_judged_at or self.latest_llm_run_at()
        now = datetime.now(timezone.utc)
        elapsed = (
            (now - latest_llm).total_seconds()
            if latest_llm is not None
            else None
        )
        next_llm_due_at = ""
        if latest_llm is not None:
            candidate_due = latest_llm + timedelta(
                seconds=max(int(self.config.judge_interval_sec), 60)
            )
            next_llm_due_at = (
                candidate_due.isoformat()
                if candidate_due > now
                else next_krx_decision_due_at(clock, now=now)
            )
        return {
            "status": "ok",
            "clock": clock,
            "quote_interval_sec": int(self.config.quote_interval_sec),
            "judge_interval_sec": int(self.config.judge_interval_sec),
            "latest_llm_run_at": latest_llm.isoformat() if latest_llm else "",
            "seconds_since_llm": elapsed,
            "next_llm_due_at": next_llm_due_at,
            "recent_runs": self.repository.recent_runs(limit=8),
            "candidate_coverage": self._candidate_coverage(),
            "cadence": {
                "pre_open": "08:30 KST slot once per trading day",
                "opening_quote_only": "09:00-09:05 KST quotes only",
                "regular_llm": "30 minute cadence by default",
                "midday": "11:40 KST memory ritual",
                "closing_watch": "15:20 이후 신규 진입 억제",
                "post_close": "15:45 KST review slot once per trading day",
            },
        }

    async def collect_account(self) -> dict[str, Any]:
        try:
            assets = await self.kis.fetch_balance_assets()
            account = normalize_account_assets(assets)
        except Exception as exc:
            account = {
                "status": "error",
                "captured_at": utc_now_iso(),
                "account_label": "국장1",
                "cash_krw": 0.0,
                "position_value_krw": 0.0,
                "total_value_krw": 0.0,
                "position_count": 0,
                "positions": [],
                "error_message": str(exc),
            }
        self.repository.save_account(account)
        return account

    async def run_once(self, *, use_llm: bool = True) -> dict[str, Any]:
        run_at = utc_now_iso()
        clock = self.clock()
        research_feed = self.research_feed_provider() if self.research_feed_provider else None
        account = await self.collect_account()
        strategy_payload = self._strategy_payload_for_run(
            research_feed,
            use_llm=use_llm,
        )
        refresh_sources = use_llm or str(
            (self._last_opportunity_scan or {}).get("status") or ""
        ) in {"", "not_scanned"}
        symbols = self._build_universe(
            account=account,
            strategy_payload=strategy_payload,
            refresh_sources=refresh_sources,
        )
        quotes = await self.quote_service.collect_quotes(
            symbols,
            concurrency=self.config.quote_concurrency,
        )
        self.repository.save_quotes(quotes)
        focus_symbols = self._focus_symbols(
            account=account,
            strategy_payload=strategy_payload,
            quotes=quotes,
        )
        prompt: dict[str, Any] = {}
        if use_llm:
            market_pulse = self._market_pulse_context(
                symbols=focus_symbols,
                quotes=quotes,
                strategy_payload=strategy_payload,
            )
            investment_memory = self._investment_memory_context(
                symbols=focus_symbols,
                quotes=quotes,
                account=account,
                strategy_payload=strategy_payload,
                market_pulse=market_pulse,
            )
            jue_wiki = self._wiki_context(
                target_scope="kis",
                symbols=focus_symbols,
                horizons=[_market_session_wiki_horizon(clock)],
            )
            prompt = self._build_prompt(
                clock=clock,
                account=account,
                strategy_payload=strategy_payload,
                quotes=quotes,
                focus_symbols=focus_symbols,
                research_feed=research_feed,
                market_pulse=market_pulse,
                investment_memory=investment_memory,
                jue_wiki=jue_wiki,
            )
        response_payload: dict[str, Any] = {}
        mode = "error" if use_llm else "quote_only"
        status = "error" if use_llm else "quotes_only"
        error_message = ""
        judgments: list[dict[str, Any]] = []

        if not use_llm:
            judgments = []
        elif str(account.get("status") or "") == "error":
            error_message = f"account_fetch_failed:{account.get('error_message') or ''}"
        elif not focus_symbols:
            error_message = "no_focus_symbols"
        elif not getattr(self.codex_runtime, "ready", False):
            error_message = "codex_runtime_unavailable"
        else:
            llm_result = await self._run_llm(prompt)
            response_payload = llm_result
            if bool(llm_result.get("ok")):
                parsed_response = _json_loads(llm_result.get("content"), {})
                contract_error = market_judgment_response_contract_error(
                    prompt=prompt,
                    response=parsed_response if isinstance(parsed_response, dict) else {},
                )
                if contract_error:
                    status = "error"
                    mode = "error"
                    error_message = contract_error
                else:
                    parsed = self._parse_llm_judgments(
                        llm_result.get("content"),
                        focus_symbols=focus_symbols,
                        account=account,
                        strategy_payload=strategy_payload,
                        quotes=quotes,
                    )
                    if parsed:
                        judgments = parsed
                        mode = "llm"
                        status = "ok"
                    else:
                        status = "error"
                        mode = "error"
                        error_message = "llm returned no valid symbol judgments"
            else:
                status = "error"
                mode = "error"
                error_message = str(llm_result.get("error") or "llm failed")
        run = {
            "run_at": run_at,
            "market_session": str(clock.get("session") or "closed"),
            "status": status,
            "mode": mode,
            "model": self.codex_runtime.resolved_model,
            "query": self.config.query,
            "error_message": error_message,
            "prompt": prompt,
            "response": response_payload,
            "source_snapshot": {
                "clock": clock,
                "account_status": account.get("status"),
                "quote_count": len(quotes),
                "focus_symbols": focus_symbols,
                "market_pulse_status": (prompt.get("market_pulse") or {}).get("status"),
                "market_pulse_regime": (prompt.get("market_pulse") or {}).get("regime"),
                "jue_wiki_requested_symbol_coverage": (
                    prompt.get("jue_wiki_requested_symbol_coverage")
                    if isinstance(
                        prompt.get("jue_wiki_requested_symbol_coverage"),
                        dict,
                    )
                    else {}
                ),
                "candidate_coverage": self._candidate_coverage(),
            },
        }
        should_persist_judgment = status != "quotes_only" or self.latest_judgment().get("status") == "missing"
        run_id = (
            self.repository.save_judgment_run(run=run, judgments=judgments)
            if should_persist_judgment
            else 0
        )
        payload = {
            "status": status,
            "run": {k: v for k, v in run.items() if k not in {"prompt", "response"}},
            "run_id": run_id,
            "clock": clock,
            "account": account,
            "quotes": quotes,
            "focus_symbols": focus_symbols,
            "error_message": error_message,
            "market_pulse": prompt.get("market_pulse"),
            "candidate_coverage": self._candidate_coverage(),
            "judgments": judgments,
            "disclaimer": "실거래 판단용 장중 기록입니다. 주문은 블록 트레이더와 안전 게이트 검증 후 실행됩니다.",
        }
        return payload

    def latest_quotes(
        self,
        *,
        limit: int = 100,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = self.repository.latest_quotes(limit=limit, symbols=symbols)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        missing_name_symbols = [
            str(row.get("symbol") or "")
            for row in items
            if isinstance(row, dict)
            and _is_symbol(row.get("symbol"))
            and not _is_usable_symbol_display_name(
                str(row.get("symbol") or ""),
                row.get("name"),
            )
        ]
        if self.report_repository is not None and missing_name_symbols:
            try:
                resolved = self.report_repository.resolve_symbol_names(
                    list(dict.fromkeys(missing_name_symbols))
                )
            except Exception:
                resolved = {}
            for row in items:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "")
                name = resolved.get(symbol)
                if _is_usable_symbol_display_name(symbol, name):
                    row["name"] = str(name)
        return payload

    def latest_account(self) -> dict[str, Any]:
        return self.repository.latest_account()

    def latest_judgment(self) -> dict[str, Any]:
        payload = self.repository.latest_judgment()
        payload["account"] = self.repository.latest_account()
        current_coverage = self._candidate_coverage()
        stored_coverage = (
            ((payload.get("run") or {}).get("source_snapshot") or {}).get(
                "candidate_coverage"
            )
            if isinstance(payload.get("run"), dict)
            else None
        )
        if (
            str(current_coverage.get("status") or "") in {"not_scanned", "missing"}
            and isinstance(stored_coverage, dict)
        ):
            payload["candidate_coverage"] = stored_coverage
        else:
            payload["candidate_coverage"] = current_coverage
        payload["disclaimer"] = "실거래 판단용 장중 기록입니다. 주문은 블록 트레이더와 안전 게이트 검증 후 실행됩니다."
        return payload

    def prune_history(
        self,
        *,
        retention_days: int = 7,
        quote_archive_retention_days: int | None = None,
        account_retention_days: int | None = None,
        judgment_retention_days: int | None = 30,
        judgment_archive_retention_days: int | None = None,
        compact_recent_run_count: int = 96,
        compact_min_chars: int = 20_000,
        compact_symbol_min_chars: int = 2_000,
    ) -> dict[str, Any]:
        return self.repository.prune_history(
            retention_days=retention_days,
            quote_archive_retention_days=quote_archive_retention_days,
            account_retention_days=account_retention_days,
            judgment_retention_days=judgment_retention_days,
            judgment_archive_retention_days=judgment_archive_retention_days,
            compact_recent_run_count=compact_recent_run_count,
            compact_min_chars=compact_min_chars,
            compact_symbol_min_chars=compact_symbol_min_chars,
        )

    def _strategy_payload(self, research_feed: dict[str, Any] | None) -> dict[str, Any]:
        try:
            payload = self.strategy_engine.build_candidates(
                query=self.config.query,
                research_feed=research_feed,
                limit=max(int(self.config.llm_max_symbols), 3),
            )
            self._last_strategy_payload = payload if isinstance(payload, dict) else None
            return payload
        except Exception as exc:
            payload = {
                "status": "error",
                "query": self.config.query,
                "candidates": [],
                "exclusions": [],
                "error_message": str(exc),
            }
            self._last_strategy_payload = payload
            return payload

    def _strategy_payload_for_run(
        self,
        research_feed: dict[str, Any] | None,
        *,
        use_llm: bool,
    ) -> dict[str, Any]:
        if use_llm or not isinstance(self._last_strategy_payload, dict):
            return self._strategy_payload(research_feed)
        return self._last_strategy_payload

    def _build_universe(
        self,
        *,
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        refresh_sources: bool = True,
    ) -> list[str]:
        out: list[str] = []
        out.extend(str(row.get("symbol") or "") for row in list(account.get("positions") or []))
        out.extend(self.watchlist)
        for key in ("candidates", "exclusions"):
            for row in list(strategy_payload.get(key) or []):
                if isinstance(row, dict):
                    out.append(str(row.get("symbol") or ""))
        opportunities = (
            self._scan_opportunities(
                account=account,
                strategy_payload=strategy_payload,
                limit=max(int(self.config.max_symbols), 1),
            )
            if refresh_sources
            else self._last_opportunity_scan
        )
        for row in list(opportunities.get("candidates") or []):
            if isinstance(row, dict):
                out.append(str(row.get("symbol") or ""))
        if refresh_sources:
            try:
                signals = self.strategy_engine.list_external_signals(limit=300)
            except Exception:
                signals = {}
            for row in list(signals.get("items") or []):
                if isinstance(row, dict):
                    out.append(str(row.get("symbol") or ""))
            if self.report_repository is not None:
                try:
                    reports = self.report_repository.search(
                        query="",
                        category="company_analysis",
                        limit=max(int(self.config.max_symbols), 1),
                    )
                except Exception:
                    reports = []
                for row in reports:
                    out.append(str(row.get("symbol") or ""))
        unique = [symbol for symbol in dict.fromkeys(out) if _is_symbol(symbol)]
        return unique[: max(int(self.config.max_symbols), 1)]

    def _focus_symbols(
        self,
        *,
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
    ) -> list[str]:
        out: list[str] = []
        out.extend(str(row.get("symbol") or "") for row in list(account.get("positions") or []))
        for row in list(strategy_payload.get("candidates") or []):
            if isinstance(row, dict):
                out.append(str(row.get("symbol") or ""))
        for row in self._opportunity_candidates():
            out.append(str(row.get("symbol") or ""))
        if not out:
            movers = sorted(
                quotes,
                key=lambda row: abs(float(row.get("change_pct") or 0.0)),
                reverse=True,
            )
            out.extend(str(row.get("symbol") or "") for row in movers)
        return [symbol for symbol in dict.fromkeys(out) if _is_symbol(symbol)][
            : max(int(self.config.llm_max_symbols), 1)
        ]

    def _scan_opportunities(
        self,
        *,
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        limit: int,
    ) -> dict[str, Any]:
        provider = self.opportunity_provider
        if provider is None:
            self._last_opportunity_scan = {"status": "missing"}
            return self._last_opportunity_scan
        try:
            payload = provider(
                limit=max(int(limit), 1),
                account=account,
                strategy_payload=strategy_payload,
            )
        except TypeError:
            try:
                payload = provider(limit=max(int(limit), 1))
            except Exception as exc:
                payload = {"status": "error", "error_message": str(exc)}
        except Exception as exc:
            payload = {"status": "error", "error_message": str(exc)}
        self._last_opportunity_scan = (
            payload if isinstance(payload, dict) else {"status": "invalid"}
        )
        return self._last_opportunity_scan

    def _opportunity_candidates(self) -> list[dict[str, Any]]:
        rows = self._last_opportunity_scan.get("candidates")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _candidate_coverage(self) -> dict[str, Any]:
        scan = self._last_opportunity_scan if isinstance(self._last_opportunity_scan, dict) else {}
        candidates = scan.get("candidates") if isinstance(scan.get("candidates"), list) else []
        return {
            "status": str(scan.get("status") or "not_scanned"),
            "pool_count": int(scan.get("pool_count") or 0),
            "candidate_count": len(candidates),
            "llm_focus_limit": int(self.config.llm_max_symbols),
            "quote_limit": int(self.config.max_symbols),
            "last_scan_at": str(scan.get("last_scan_at") or scan.get("generated_at") or ""),
            "coverage": scan.get("coverage") if isinstance(scan.get("coverage"), dict) else {},
        }

    def _build_prompt(
        self,
        *,
        clock: dict[str, Any],
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
        focus_symbols: list[str],
        research_feed: dict[str, Any] | None,
        market_pulse: dict[str, Any] | None,
        investment_memory: dict[str, Any] | None,
        jue_wiki: dict[str, Any] | None,
    ) -> dict[str, Any]:
        quote_by_symbol = {str(row.get("symbol") or ""): row for row in quotes}
        candidates = {
            str(row.get("symbol") or ""): row
            for row in list(strategy_payload.get("candidates") or [])
            if isinstance(row, dict)
        }
        for row in self._opportunity_candidates():
            symbol = str(row.get("symbol") or "")
            if symbol and symbol not in candidates:
                candidates[symbol] = row
        positions = {
            str(row.get("symbol") or ""): row
            for row in list(account.get("positions") or [])
            if isinstance(row, dict)
        }
        context_rows: list[dict[str, Any]] = []
        for symbol in focus_symbols:
            context_rows.append(
                {
                    "symbol": symbol,
                    "quote": _trim_quote(quote_by_symbol.get(symbol) or {}),
                    "position": positions.get(symbol) or {},
                    "strategy": _trim_strategy(candidates.get(symbol) or {}),
                    "valuation": _trim_valuation(self._valuation(symbol)),
                    "rag": self._rag_context(symbol),
                }
            )
        previous = self.repository.latest_judgment()
        prompt = {
            "task": "한국 주식 장중 실거래 판단을 JSON으로 작성한다. 이 판단은 블록 매니저와 룰 실행기의 입력이다.",
            "language_policy": _compact_language_policy_for_prompt(),
            "rules": [
                "허용된 account_action으로 보유/추가검토/청산점검/회피/신규관심 판단을 분명히 쓴다.",
                "국장1 계좌의 현금, 보유 비중, 평가손익, 보유 종목을 반드시 반영한다.",
                "리포트/RAG/밸류/고래/세시반/시세가 부족하면 data_gaps에 적는다.",
                "Research Runner는 선택적 legacy 피드다. 비활성 자체를 리서치 장애나 data_gaps로 쓰지 않는다.",
                "수량, 주문가격, 즉시주문은 블록 트레이더가 안전 게이트에서 검증하므로 여기서는 트리거와 블록 운영 판단에 집중한다.",
                "쥬는 소극적 요약자가 아니라 공격적인 기회 탐색자다. 눌림목, 순환매 다음 후보, 비대칭 손익비, 기존 블록 보강/축소 기회를 명확히 찾는다.",
                "공격성은 안전 게이트 우회를 뜻하지 않는다. 현금/수량/중복주문/kill switch는 항상 우선한다.",
            ],
            "aggressive_trader_policy": {
                "style": "aggressive_genius_trader",
                "principles": [
                    "비대칭 손익비가 보이면 관망만 하지 말고 실행 가능한 트리거로 바꾼다.",
                    "강한 1등주 추격, 눌림목 매수, 다음 섹터 후보 선점 중 어느 가설인지 분리한다.",
                    "보유 블록은 방치하지 않고 추가/유지/축소/리스크 점검의 이유를 쓴다.",
                    "자료가 부족하면 회피가 아니라 필요한 확인 조건과 다음 행동을 제시한다.",
                ],
            },
            "allowed_account_actions": sorted(ACCOUNT_ACTIONS),
            "allowed_stances": sorted(STANCES),
            "allowed_horizons": sorted(HORIZONS),
            "query": self.config.query,
            "clock": clock,
            "account": _compact_account_for_prompt(account),
            "research": {
                "status": "active"
                if isinstance(research_feed, dict)
                else "optional_disabled",
                "updated_at": (research_feed or {}).get("updated_at") if isinstance(research_feed, dict) else "",
                "count": (research_feed or {}).get("count") if isinstance(research_feed, dict) else 0,
                "note": (
                    "legacy Research Runner is optional; use strategy_summary.sources "
                    "for Naver Reports/RAG, ETF research, Whale, and 세시반 coverage"
                ),
            },
            "market_pulse": _compact_market_pulse_for_prompt(market_pulse),
            "investment_memory": _compact_investment_memory_for_prompt(investment_memory),
            "required_decision_skills": ["market_judge", "risk_manager"],
            "strategy_summary": {
                "status": strategy_payload.get("status"),
                "score_method_version": strategy_payload.get("score_method_version"),
                "regime": strategy_payload.get("regime"),
                "sources": strategy_payload.get("sources"),
            },
            "symbols": context_rows,
            "previous_judgment": _compact_previous_judgment_for_prompt(previous),
            "output_schema": {
                "judgments": [
                    {
                        "symbol": "6-digit KRX code",
                        "stance": "watch|confirm|hold|risk_check|avoid|stale",
                        "account_action": "hold|watch_add|avoid_add|trim_watch|risk_check|new_watch",
                        "horizon": "intraday|short_term|mid_term|long_term|unknown",
                        "confidence": "0.0-1.0",
                        "reasons": ["근거"],
                        "risks": ["반론"],
                        "triggers": ["확인 조건"],
                        "data_gaps": ["부족한 자료"],
                    }
                ]
            },
        }
        _attach_jue_wiki_prompt_context(
            prompt,
            jue_wiki,
            max_chars=JUE_WIKI_BUDGET_REPORT_MAX_CHARS,
        )
        prompt["prompt_budget"] = {
            "compacted": True,
            "chars": len(_json_dumps(prompt)),
            "symbol_count": len(context_rows),
            "max_symbols": int(self.config.llm_max_symbols),
        }
        return prompt

    def _wiki_context(
        self,
        *,
        target_scope: str,
        symbols: list[str],
        horizons: list[str] | None = None,
    ) -> dict[str, Any]:
        provider = self.wiki_context_provider
        clean_symbols = [
            str(symbol).strip().upper()
            for symbol in symbols
            if str(symbol).strip()
        ]
        clean_horizons = [
            str(horizon).strip().lower()
            for horizon in list(horizons or [])
            if str(horizon).strip()
        ]
        if provider is None:
            return {
                "status": "missing",
                "reason": "wiki_context_provider_not_configured",
                "target_scope": target_scope,
                "symbols": clean_symbols,
                "horizons": clean_horizons,
            }
        try:
            payload = _call_wiki_context_provider(
                provider,
                target_scope=target_scope,
                symbols=clean_symbols,
                horizons=clean_horizons,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error_message": str(exc),
                "target_scope": target_scope,
                "symbols": clean_symbols,
                "horizons": clean_horizons,
            }
        return (
            payload
            if isinstance(payload, dict)
            else {
                "status": "error",
                "error_message": "wiki_context_provider_returned_non_dict",
                "target_scope": target_scope,
                "symbols": clean_symbols,
            }
        )

    def _market_pulse_context(
        self,
        *,
        symbols: list[str],
        quotes: list[dict[str, Any]],
        strategy_payload: dict[str, Any],
    ) -> dict[str, Any]:
        provider = self.market_pulse_provider
        if provider is None:
            return {"status": "missing"}
        try:
            payload = provider(
                symbols=symbols,
                quotes=quotes,
                strategy=strategy_payload,
            )
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}
        return payload if isinstance(payload, dict) else {"status": "invalid"}

    def _investment_memory_context(
        self,
        *,
        symbols: list[str],
        quotes: list[dict[str, Any]],
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        market_pulse: dict[str, Any],
    ) -> dict[str, Any]:
        provider = self.memory_context_provider
        if provider is None:
            return {"status": "missing"}
        try:
            payload = provider(
                symbols=symbols,
                quotes=quotes,
                account=account,
                strategy=strategy_payload,
                market_pulse=market_pulse,
                target_scope="kis",
                source_scope="kis",
            )
        except TypeError:
            try:
                payload = provider(
                    symbols=symbols,
                    quotes=quotes,
                    account=account,
                    strategy=strategy_payload,
                    market_pulse=market_pulse,
                )
            except Exception as exc:
                return {"status": "error", "error_message": str(exc)}
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}
        return payload if isinstance(payload, dict) else {"status": "invalid"}

    def _valuation(self, symbol: str) -> dict[str, Any]:
        if self.fundamentals_repository is None:
            return {"status": "missing"}
        try:
            return self.fundamentals_repository.latest(symbol) or {"status": "missing"}
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}

    def _rag_context(self, symbol: str) -> list[dict[str, Any]]:
        if self.rag_store is None:
            return []
        try:
            return [
                {
                    "report_id": row.get("report_id"),
                    "broker": row.get("broker"),
                    "published_at": row.get("published_at"),
                    "content": _clean_text(row.get("content"), limit=120),
                }
                for row in self.rag_store.query(query=self.config.query, symbol=symbol, limit=1)
            ]
        except Exception:
            return []

    async def _run_llm(self, prompt: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.codex_runtime.resolved_model,
            "native_thread_mode": "ephemeral",
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "telemetry": {"component": "market_judge", "operation": "run_once"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only JSON. You are producing live trading judgment "
                        "for HERMES block trading; order execution is gated separately."
                    ),
                },
                {"role": "user", "content": _json_dumps(prompt)},
            ],
        }
        return await self.codex_runtime.complete(payload)

    def _parse_llm_judgments(
        self,
        content: Any,
        *,
        focus_symbols: list[str],
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        text = str(content or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows = parsed.get("judgments") if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            return []
        base = self._context_maps(
            account=account,
            strategy_payload=strategy_payload,
            quotes=quotes,
        )
        out: list[dict[str, Any]] = []
        allowed = set(focus_symbols)
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            if symbol not in allowed:
                continue
            confidence = _safe_float(row.get("confidence"))
            if confidence > 1.0:
                confidence = confidence / 100.0
            stance = str(row.get("stance") or "confirm").strip()
            account_action = str(row.get("account_action") or "hold").strip()
            horizon = str(row.get("horizon") or "unknown").strip()
            out.append(
                {
                    "symbol": symbol,
                    "name": base["names"].get(symbol) or symbol,
                    "stance": stance if stance in STANCES else "confirm",
                    "account_action": account_action if account_action in ACCOUNT_ACTIONS else "hold",
                    "horizon": horizon if horizon in HORIZONS else "unknown",
                    "confidence": max(0.0, min(confidence, 1.0)),
                    "reasons": _string_list(row.get("reasons"), limit=4),
                    "risks": _string_list(row.get("risks"), limit=4),
                    "triggers": _string_list(row.get("triggers"), limit=4),
                    "data_gaps": _filter_legacy_research_runner_gaps(
                        _string_list(row.get("data_gaps"), limit=4)
                    ),
                    "quote": base["quotes"].get(symbol) or {},
                    "position": base["positions"].get(symbol) or {},
                    "strategy": base["strategies"].get(symbol) or {},
                }
            )
        return out

    def _context_maps(
        self,
        *,
        account: dict[str, Any],
        strategy_payload: dict[str, Any],
        quotes: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        quotes_by_symbol = {str(row.get("symbol") or ""): row for row in quotes if isinstance(row, dict)}
        positions = {
            str(row.get("symbol") or ""): row
            for row in list(account.get("positions") or [])
            if isinstance(row, dict)
        }
        strategies = {
            str(row.get("symbol") or ""): row
            for row in list(strategy_payload.get("candidates") or [])
            if isinstance(row, dict)
        }
        for row in self._opportunity_candidates():
            symbol = str(row.get("symbol") or "")
            if symbol and symbol not in strategies:
                strategies[symbol] = row
        names: dict[str, str] = {}
        for source in (quotes_by_symbol, positions, strategies):
            for symbol, row in source.items():
                name = str(row.get("name") or "").strip()
                if _is_usable_symbol_display_name(symbol, name):
                    names[symbol] = name
        if self.report_repository is not None:
            symbols = list(dict.fromkeys([*quotes_by_symbol, *positions, *strategies]))
            if symbols:
                try:
                    resolved = self.report_repository.resolve_symbol_names(symbols)
                    for symbol, name in resolved.items():
                        if _is_usable_symbol_display_name(symbol, name):
                            names[symbol] = name
                except Exception:
                    logger.warning(
                        "failed to resolve market judgment symbol names for %d symbols",
                        len(symbols),
                        exc_info=True,
                    )
        return {
            "quotes": quotes_by_symbol,
            "positions": positions,
            "strategies": strategies,
            "names": names,
        }


def _string_list(value: Any, *, limit: int = 4) -> list[str]:
    if isinstance(value, list):
        rows = value
    else:
        rows = [value] if value else []
    out = [_clean_text(row, limit=180) for row in rows if _clean_text(row, limit=180)]
    return out[: max(int(limit), 1)]


def _filter_legacy_research_runner_gaps(rows: list[str]) -> list[str]:
    patterns = (
        "research runner",
        "research_runner",
        "리서치 러너",
        "리서치러너",
    )
    out: list[str] = []
    for row in rows:
        text = str(row or "").strip()
        lower = text.lower()
        if any(pattern in lower or pattern in text for pattern in patterns):
            continue
        out.append(text)
    return out


def _trim_quote(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "symbol",
            "name",
            "price",
            "change",
            "change_pct",
            "volume",
            "trading_value",
            "source",
            "fetched_at",
            "status",
            "error_message",
        )
        if key in row
    }


def _trim_suitability(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for horizon in ("balanced", "short_term", "mid_term", "long_term"):
        row = value.get(horizon)
        if not isinstance(row, dict):
            continue
        item: dict[str, Any] = {
            key: _compact_prompt_value(row.get(key), list_limit=2, string_limit=80)
            for key in ("score", "grade", "drivers", "risks")
            if key in row
        }
        item = {key: val for key, val in item.items() if val not in ({}, [], "", None)}
        if item:
            compact[horizon] = item
    return compact


def _trim_valuation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    valuation = value.get("valuation") if isinstance(value.get("valuation"), dict) else {}
    score = value.get("score") if isinstance(value.get("score"), dict) else {}
    compact: dict[str, Any] = {
        "status": value.get("status"),
        "label": score.get("label") or valuation.get("label"),
        "price": valuation.get("price"),
        "per": valuation.get("per"),
        "pbr": valuation.get("pbr"),
        "industry_per": valuation.get("industry_per"),
        "relative_per_discount_pct": score.get("relative_per_discount_pct"),
        "undervalued_score": score.get("undervalued_score"),
        "overvalued_risk": score.get("overvalued_risk"),
        "quality_score": score.get("quality_score"),
        "growth_score": score.get("growth_score"),
        "reasons": _compact_prompt_value(score.get("reasons"), list_limit=2, string_limit=120),
        "risks": _compact_prompt_value(score.get("risks"), list_limit=2, string_limit=120),
    }
    return {
        key: _compact_prompt_value(val, list_limit=2, string_limit=120)
        for key, val in compact.items()
        if val not in ({}, [], "", None)
    }


def _trim_strategy(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "score": row.get("score"),
        "score_method_version": row.get("score_method_version"),
        "confidence": row.get("confidence"),
        "stance": row.get("stance"),
        "reasons": _compact_prompt_value(row.get("reasons"), list_limit=2, string_limit=90),
        "risks": _compact_prompt_value(row.get("risks"), list_limit=2, string_limit=90),
        "checks": _compact_prompt_value(row.get("checks"), list_limit=2, string_limit=90),
        "data_warnings": _compact_prompt_value(row.get("data_warnings"), list_limit=2, string_limit=90),
        "data_coverage": _compact_prompt_value(row.get("data_coverage"), list_limit=4, string_limit=100),
        "valuation": _trim_valuation(row.get("valuation")),
        "suitability": _trim_suitability(row.get("suitability")),
        "opportunity_score": row.get("opportunity_score"),
        "source_scores": _compact_prompt_value(row.get("source_scores"), list_limit=4, string_limit=80),
        "sources": _compact_prompt_value(row.get("sources"), list_limit=5, string_limit=80),
    }
    return {
        key: value
        for key, value in compact.items()
        if value not in ({}, [], "", None)
    }
